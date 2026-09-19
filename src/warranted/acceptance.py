"""A trusted single writer records acceptance under current host-owned policy.

Acceptance is the local ledger transition itself, not permission to perform a later
external effect. The host owns the resolver, checker requests, and exception method.
Workers must not receive this object or the ledger writer. Isolation remains M3.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from time import perf_counter_ns
from types import MappingProxyType

from warranted.ledger import (
    ArtifactRef,
    BudgetExceeded,
    Completion,
    Ledger,
    Observation,
    OperationConflict,
    Origin,
    Outcome,
    Request,
    Result,
    _json_default,
    _json_object,
    _text,
)


def _encode(value) -> bytes:
    return json.dumps(
        value, default=_json_default, sort_keys=True, allow_nan=False
    ).encode()


def _digest(value) -> str:
    return hashlib.sha256(_encode(value)).hexdigest()


@dataclass(frozen=True)
class Evidence:
    name: str
    artifact: ArtifactRef

    def __post_init__(self):
        _text(self.name)
        if type(self.artifact) is not ArtifactRef:
            raise TypeError("expected an ArtifactRef")

    @classmethod
    def captured(cls, observation: Observation, channel: str) -> Evidence:
        return cls(
            f"observation/{observation.sequence}/{channel}",
            observation.artifacts[channel],
        )

    @classmethod
    def restored(cls, value: dict) -> Evidence:
        if set(value) != {"name", "artifact"}:
            raise ValueError("invalid evidence reference")
        return cls(value["name"], ArtifactRef(**value["artifact"]))


@dataclass(frozen=True)
class AcceptanceContext:
    policy: Evidence
    revision: Evidence
    checks: Mapping[str, Request | None]

    def __post_init__(self):
        if type(self.policy) is not Evidence or type(self.revision) is not Evidence:
            raise TypeError("expected policy and revision evidence")
        checks = dict(self.checks)
        for name, request in checks.items():
            _text(name)
            if request is not None and type(request) is not Request:
                raise TypeError("expected a Request or unknown applicability (None)")
        object.__setattr__(self, "checks", MappingProxyType(checks))


class Status(StrEnum):
    PASSED = "passed"
    EXCEPTED = "excepted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MISSING = "missing"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNPROVED = "unproved"
    UNSUPPORTED = "unsupported"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True)
class Decision:
    status: Status
    requirements: Mapping[str, Status]
    completion: Completion

    def __post_init__(self):
        object.__setattr__(
            self, "requirements", MappingProxyType(dict(self.requirements))
        )


class Acceptance:
    """Host-only boundary. Calls must share the ledger's serialized writer."""

    def __init__(
        self,
        ledger: Ledger,
        session: str,
        resolve: Callable[[Evidence], AcceptanceContext],
    ):
        self.ledger, self.session, self.resolve = ledger, session, resolve

    def _read(self, evidence: Evidence):
        return json.loads(
            self.ledger.read_artifact(evidence.artifact), object_pairs_hook=_json_object
        )

    def _current(self, target: Evidence):
        if type(target) is not Evidence:
            raise TypeError("expected target evidence")
        current = self.resolve(target)
        if type(current) is not AcceptanceContext:
            raise TypeError("host resolver must return an AcceptanceContext")
        policy = self._read(current.policy)
        if (
            type(policy) is not dict
            or set(policy) != {"version", "owner", "transition", "requirements"}
            or type(policy["version"]) is not int
            or policy["version"] != 1
            or policy["transition"] != "accept-candidate"
        ):
            raise ValueError("unsupported acceptance policy")
        _text(policy["owner"])
        requirements = policy["requirements"]
        if type(requirements) is not dict or not requirements:
            raise ValueError("acceptance policy requires at least one gate")
        for name, condition in requirements.items():
            _text(name)
            if (
                type(condition) is not dict
                or set(condition) != {"kind", "check", "field"}
                or condition["kind"] not in ("rule", "gate")
            ):
                raise ValueError("unknown requirement kind or fields")
            _text(condition["check"])
            _text(condition["field"])
        if not any(item["kind"] == "gate" for item in requirements.values()):
            raise ValueError("acceptance policy requires at least one gate")
        if {item["check"] for item in requirements.values()} != current.checks.keys():
            raise ValueError("host checks do not match the pinned policy")
        return current, policy

    def _scope(self, target: Evidence, current: AcceptanceContext) -> dict:
        return {
            "target": asdict(target),
            "policy": asdict(current.policy),
            "revision": asdict(current.revision),
            # Full requests remain private ledger metadata; bind them by digest here.
            "checks": {
                name: _digest(req) if req is not None else None
                for name, req in current.checks.items()
            },
        }

    def _request(self, kind: str, evidence: list[Evidence], body: dict) -> Request:
        inputs = {}
        for ref in evidence:
            if ref.name in inputs and inputs[ref.name] != ref.artifact:
                raise ValueError("conflicting evidence references")
            inputs[ref.name] = ref.artifact
        return Request(
            Origin(
                f"{kind}/{_digest({'inputs': inputs, 'body': body})}",
                kind,
                "warranted-acceptance",
                "1",
                inputs,
            ),
            self.ledger.project,
        )

    def _commit(
        self, kind: str, evidence: list[Evidence], body: dict, started: int
    ) -> Completion:
        # Also check before cached reuse: a later budget breach cannot be bypassed.
        if any(
            op.completion and op.completion.breaches for op in self.ledger.operations()
        ) or any(
            balance.available < 0 for balance in self.ledger.accounting().values()
        ):
            raise BudgetExceeded("recorded budget breach blocks acceptance")
        request = self._request(kind, evidence, body)
        raw = {f"{kind}.json": _encode(body)}
        operation = self.ledger.reserve(self.session, request, {})
        if operation.completion is not None:
            completion = operation.completion
            if (
                completion.result.outcome is not Outcome.SUCCEEDED
                or set(completion.observation.artifacts) != raw.keys()
                or any(
                    self.ledger.read_artifact(completion.observation.artifacts[name])
                    != value
                    for name, value in raw.items()
                )
            ):
                raise OperationConflict("conflicting acceptance completion")
            return completion
        if not self.ledger.begin(self.session, request):
            raise RuntimeError("acceptance outcome is unknown; no retry")
        return self.ledger.complete(
            self.session,
            request,
            Result(Outcome.SUCCEEDED, 0, {}, perf_counter_ns() - started),
            raw,
        )

    def record_exception(self, target: Evidence, rule: str, reason: str) -> str:
        """Record a host-authorized rule exception for this exact current scope."""
        started = perf_counter_ns()
        _text(reason)
        current, policy = self._current(target)
        condition = policy["requirements"].get(rule)
        if condition is None or condition["kind"] != "rule":
            raise ValueError("unknown rule or attempted gate waiver")
        body = {
            "version": 1,
            "scope": self._scope(target, current),
            "owner": policy["owner"],
            "rule": rule,
            "reason": reason,
        }
        completion = self._commit(
            "rule-exception", [target, current.policy, current.revision], body, started
        )
        return completion.observation.origin.operation_id

    def _exception(self, target, current, policy, rule, operation_id):
        # ponytail: scan the local journal; index IDs if history makes this costly.
        operation = next(
            (
                op
                for op in self.ledger.operations()
                if op.request.origin.operation_id == operation_id
            ),
            None,
        )
        if operation is None:
            return Status.MISSING, None
        operation = self.ledger.lookup(operation.request)
        if operation.completion is None:
            return Status.UNKNOWN, None
        completion = operation.completion
        if (
            completion.result.outcome is not Outcome.SUCCEEDED
            or "rule-exception.json" not in completion.observation.artifacts
        ):
            return Status.UNSUPPORTED, None
        evidence = Evidence.captured(completion.observation, "rule-exception.json")
        try:
            body = self._read(evidence)
            reason = body["reason"]
            _text(reason)
        except (ValueError, TypeError, KeyError):
            return Status.UNSUPPORTED, evidence
        expected = {
            "version": 1,
            "scope": self._scope(target, current),
            "owner": policy["owner"],
            "rule": rule,
            "reason": reason,
        }
        request = self._request(
            "rule-exception", [target, current.policy, current.revision], expected
        )
        if body != expected or operation.request != request:
            return Status.STALE, evidence
        return Status.EXCEPTED, evidence

    def _check(self, target, request, operation_id, fields, gate):
        if request is None:
            return Status.UNKNOWN, None
        if gate and request.origin.inputs.get(target.name) != target.artifact:
            return Status.UNSUPPORTED, None
        if operation_id is None:
            return Status.MISSING, None
        if type(operation_id) is not str or not operation_id:
            return Status.UNSUPPORTED, None
        if operation_id != request.origin.operation_id:
            known = any(
                op.request.origin.operation_id == operation_id
                for op in self.ledger.operations()
            )
            return Status.STALE if known else Status.UNSUPPORTED, None
        try:
            operation = self.ledger.lookup(request)
        except OperationConflict:
            return Status.UNSUPPORTED, None
        if operation is None:
            return Status.MISSING, None
        if operation.completion is None:
            return Status.UNKNOWN, None
        completion = operation.completion
        if completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE:
            return Status.INFRASTRUCTURE_FAILURE, None
        if (
            completion.result.outcome is not Outcome.SUCCEEDED
            or "result.json" not in completion.observation.artifacts
        ):
            return Status.UNSUPPORTED, None
        evidence = Evidence.captured(completion.observation, "result.json")
        try:
            values = self._read(evidence)
            if (
                type(values) is not dict
                or set(values) != fields
                or any(type(value) is not bool for value in values.values())
            ):
                return Status.UNSUPPORTED, evidence
        except (ValueError, TypeError):
            return Status.UNSUPPORTED, evidence
        return values, evidence

    def accept(
        self,
        target: Evidence,
        receipts: Mapping[str, str | None],
        exceptions: Mapping[str, str] | None = None,
    ) -> Decision:
        """Resolve current requirements, check evidence, and commit the decision.

        No caller-supplied prior decision, applicability flag, or waiver can grant
        acceptance. The single writer cannot interleave a revision into this call.
        """
        started = perf_counter_ns()
        if not isinstance(receipts, Mapping) or (
            exceptions is not None and not isinstance(exceptions, Mapping)
        ):
            raise TypeError("receipts and exceptions must be mappings")
        current, policy = self._current(target)
        receipts = dict(receipts)
        exceptions = dict(exceptions) if exceptions is not None else {}
        conditions = policy["requirements"]
        invalid = bool(receipts.keys() - current.checks.keys()) or any(
            name not in conditions or conditions[name]["kind"] != "rule"
            for name in exceptions
        )
        evidence = [target, current.policy, current.revision]
        checks = {}
        for name, request in current.checks.items():
            selected = [
                condition
                for condition in conditions.values()
                if condition["check"] == name
            ]
            values, ref = self._check(
                target,
                request,
                receipts.get(name),
                {item["field"] for item in selected},
                any(item["kind"] == "gate" for item in selected),
            )
            checks[name] = values
            if ref is not None:
                evidence.append(ref)
        requirements = {}
        for name, condition in conditions.items():
            values = checks[condition["check"]]
            status = (
                values
                if isinstance(values, Status)
                else Status.PASSED
                if values[condition["field"]]
                else Status.REJECTED
            )
            if condition["kind"] == "rule" and name in exceptions:
                status, ref = self._exception(
                    target, current, policy, name, exceptions[name]
                )
                if ref is not None:
                    evidence.append(ref)
            requirements[name] = status
        status = Status.ACCEPTED
        for blocked in (
            Status.UNSUPPORTED,
            Status.INFRASTRUCTURE_FAILURE,
            Status.UNKNOWN,
            Status.MISSING,
            Status.STALE,
            Status.REJECTED,
        ):
            if blocked in requirements.values():
                status = blocked
                break
        if invalid:
            status = Status.UNSUPPORTED
        body = {
            "version": 1,
            "scope": self._scope(target, current),
            "status": status,
            "requirements": requirements,
            "receipts": receipts,
            "exceptions": exceptions,
        }
        completion = self._commit("decision", evidence, body, started)
        return Decision(status, requirements, completion)
