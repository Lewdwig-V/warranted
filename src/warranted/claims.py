"""Scoped claims and conservative support, using the existing observation ledger.

The trusted host declares dependencies. A current version is not proof that an
assumption is true. Claims and support reports never grant candidate acceptance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from types import MappingProxyType

from warranted.acceptance import Evidence, Status, _digest, _encode
from warranted.ledger import Ledger, Origin, Outcome, Request, _json_object, _text


class Applicability(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Assessment:
    validation: Status
    applicability: Applicability
    dependencies: Mapping[str, Applicability]

    def __post_init__(self):
        object.__setattr__(
            self, "dependencies", MappingProxyType(dict(self.dependencies))
        )


class Claims:
    """Host-owned claim capture and read-only assessment for one serialized writer."""

    def __init__(self, ledger: Ledger, session: str):
        self.ledger, self.session = ledger, session

    def record(
        self,
        statement: str,
        target: Evidence,
        assumptions: Mapping[str, Evidence],
        *,
        parents: tuple[Evidence, ...] = (),
        validation: tuple[Request, str] | None = None,
        complete: bool = False,
    ) -> Evidence:
        """Capture a claim, not its truth; incomplete dependencies block reuse.

        Validation names a boolean checker field, or "proof" for a host proof
        receipt. Omitting it preserves an unchecked assertion. Parent edges
        propagate applicability, not logical inference from a parent's truth.
        """
        _text(statement)
        if not isinstance(assumptions, Mapping) or type(complete) is not bool:
            raise TypeError("expected assumptions and an explicit completeness flag")
        for name in assumptions:
            _text(name)
        evidence = [target, *assumptions.values(), *parents]
        if any(type(ref) is not Evidence for ref in evidence):
            raise TypeError("expected evidence references")
        for parent in parents:
            self._claim(parent)
        check = None
        if validation is not None:
            request, field = validation
            _text(field)
            if type(request) is not Request:
                raise TypeError("expected a validation request")
            if request.origin.inputs.get(target.name) != target.artifact:
                raise ValueError("validation must bind the exact target")
            self.ledger.lookup(request)
            check = {
                "operation_id": request.origin.operation_id,
                "request": _digest(request),
                "field": field,
            }
            evidence.extend(
                Evidence(name, ref) for name, ref in request.origin.inputs.items()
            )
        body = {
            "version": 1,
            "statement": statement,
            "target": asdict(target),
            "assumptions": {name: asdict(ref) for name, ref in assumptions.items()},
            "parents": [asdict(ref) for ref in parents],
            "validation": check,
            "complete": complete,
        }
        inputs = {}
        for ref in evidence:
            if ref.name in inputs and inputs[ref.name] != ref.artifact:
                raise ValueError("conflicting evidence references")
            inputs[ref.name] = ref.artifact
        origin = Origin(
            f"claim/{_digest(body)}", "claim", "warranted-claims", "1", inputs
        )
        # Validate input provenance even when returning an existing capture.
        self.ledger.lookup(Request(origin, self.ledger.project))
        raw = _encode(body)
        # ponytail: scan small histories; index claim IDs when volume warrants it.
        for event in self.ledger.history():
            if event.origin == origin and set(event.artifacts) == {"claim.json"}:
                if self.ledger.read_artifact(event.artifacts["claim.json"]) == raw:
                    return Evidence.captured(event, "claim.json")
        event = self.ledger.record(self.session, origin, {"claim.json": raw})
        return Evidence.captured(event, "claim.json")

    def _claim(self, ref: Evidence) -> dict:
        if type(ref) is not Evidence:
            raise TypeError("expected claim evidence")
        event = next(
            (
                event
                for event in self.ledger.history()
                if "claim.json" in event.artifacts
                and Evidence.captured(event, "claim.json") == ref
            ),
            None,
        )
        if event is None or (
            event.origin.kind,
            event.origin.producer,
            event.origin.producer_version,
        ) != ("claim", "warranted-claims", "1"):
            raise ValueError("not a captured host claim")
        self.ledger.lookup(Request(event.origin, self.ledger.project))
        body = json.loads(
            self.ledger.read_artifact(ref.artifact), object_pairs_hook=_json_object
        )
        if (
            type(body) is not dict
            or set(body)
            != {
                "version",
                "statement",
                "target",
                "assumptions",
                "parents",
                "validation",
                "complete",
            }
            or type(body["version"]) is not int
            or body["version"] != 1
            or type(body["complete"]) is not bool
            or event.origin.operation_id != f"claim/{_digest(body)}"
        ):
            raise ValueError("invalid captured claim")
        # Parents must already exist when recorded, so cycles cannot be introduced.
        earlier = {
            Evidence.captured(item, "claim.json")
            for item in self.ledger.history()
            if item.sequence < event.sequence and "claim.json" in item.artifacts
        }
        if any(Evidence.restored(parent) not in earlier for parent in body["parents"]):
            raise ValueError("claim parents must precede the claim")
        return body

    def _validation(self, body: dict) -> Status:
        check = body["validation"]
        if check is None:
            return Status.UNPROVED
        operation = next(
            (
                op
                for op in self.ledger.operations()
                if op.request.origin.operation_id == check["operation_id"]
            ),
            None,
        )
        if operation is None:
            return Status.MISSING
        if _digest(operation.request) != check["request"]:
            return Status.UNSUPPORTED
        if operation.request.origin.kind == "proof":
            from warranted.proof_receipts import proof_status

            if check["field"] != "proof":
                return Status.UNSUPPORTED
            return proof_status(
                self.ledger, operation.request, Evidence.restored(body["target"])
            )
        operation = self.ledger.lookup(operation.request)
        if operation.completion is None:
            return Status.UNKNOWN
        completion = operation.completion
        if completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE:
            return Status.INFRASTRUCTURE_FAILURE
        if (
            completion.result.outcome is not Outcome.SUCCEEDED
            or "result.json" not in completion.observation.artifacts
        ):
            return Status.UNSUPPORTED
        raw = self.ledger.read_artifact(completion.observation.artifacts["result.json"])
        try:
            values = json.loads(
                raw,
                object_pairs_hook=_json_object,
            )
            if type(values) is not dict or type(values.get(check["field"])) is not bool:
                return Status.UNSUPPORTED
        except (ValueError, TypeError):
            return Status.UNSUPPORTED
        return Status.PASSED if values[check["field"]] else Status.REJECTED

    def assess(
        self, claim: Evidence, current: Mapping[str, Evidence | None]
    ) -> Assessment:
        """Read current support without changing the claim or running its checker.

        The host supplies current versions afresh. Missing versions and incomplete
        dependency capture are unknown. A mismatch propagates through all parents.
        """
        if not isinstance(current, Mapping):
            raise TypeError("expected current assumption versions")
        for name, ref in current.items():
            _text(name)
            if ref is not None:
                if type(ref) is not Evidence:
                    raise TypeError("expected evidence or an unknown version")
                self.ledger.lookup(
                    Request(
                        Origin(
                            "support-context",
                            "context",
                            "warranted-claims",
                            "1",
                            {ref.name: ref.artifact},
                        ),
                        self.ledger.project,
                    )
                )
        cache = {}

        def visit(ref):
            if ref in cache:
                return cache[ref]
            body = self._claim(ref)
            dependencies = {
                f"assumption/{name}": Applicability.UNKNOWN
                if current.get(name) is None
                else Applicability.CURRENT
                if current[name] == Evidence.restored(expected)
                else Applicability.STALE
                for name, expected in body["assumptions"].items()
            }
            dependencies.update(
                {
                    f"parent/{parent.name}": visit(parent).applicability
                    for parent in map(Evidence.restored, body["parents"])
                }
            )
            status = Applicability.CURRENT
            if not body["complete"] or Applicability.UNKNOWN in dependencies.values():
                status = Applicability.UNKNOWN
            if Applicability.STALE in dependencies.values():
                status = Applicability.STALE
            cache[ref] = Assessment(self._validation(body), status, dependencies)
            return cache[ref]

        return visit(claim)
