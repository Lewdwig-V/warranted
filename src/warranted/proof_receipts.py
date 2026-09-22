"""Durable verification under one trusted writer, outside worker storage.

One synthetic proof unit buys one bounded compile/export/replay attempt. A lost
or unattributable result stays reserved. There is no automatic retry or imported
receipt reconciliation. Historical claims read evidence without invoking tools.
"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from warranted import proofs
from warranted.acceptance import Evidence, Status, _digest, _encode
from warranted.ledger import Ledger, Origin, Outcome, Request, Result, _json_object
from warranted.worker import record_once, unresolved

PRODUCER = ("proof", "warranted-proofs", "1")


def _spec(ledger: Ledger, request: Request) -> tuple[dict, Evidence, Evidence]:
    """Recover the exact host policy and source from the request's provenance."""
    origin = request.origin
    if (origin.kind, origin.producer, origin.producer_version) != PRODUCER:
        raise ValueError("not a host proof operation")
    ledger.lookup(request)
    matches = []
    for event in ledger.history():
        if (
            event.origin.kind == "proof-policy"
            and event.origin.producer == PRODUCER[1]
            and event.origin.producer_version == PRODUCER[2]
            and set(event.artifacts)
            == {"policy.json", "bundle.json", "Challenge.lean", "config.json"}
        ):
            inputs = {
                Evidence.captured(event, name).name: ref
                for name, ref in event.artifacts.items()
            }
            if inputs.items() <= origin.inputs.items():
                matches.append((event, inputs))
    if len(matches) != 1:
        raise ValueError("proof request needs one exact host policy")
    event, inputs = matches[0]
    identity = json.loads(
        ledger.read_artifact(event.artifacts["policy.json"]),
        object_pairs_hook=_json_object,
    )
    if event.origin != Origin(
        "proof-policy/" + _digest(identity), "proof-policy", *PRODUCER[1:], {}
    ):
        raise ValueError("invalid proof policy capture")
    sources = dict(origin.inputs.items() - inputs.items())
    if len(sources) != 1:
        raise ValueError("proof request needs exactly one captured solution")
    solution = Evidence(*next(iter(sources.items())))
    return identity, Evidence.captured(event, "Challenge.lean"), solution


def _decision(ledger: Ledger, request: Request, raw: dict[str, bytes]) -> Status:
    """Validate receipt identity without consulting today's tools or environment."""
    identity, target, solution = _spec(ledger, request)
    report = json.loads(raw["verification.json"], object_pairs_hook=_json_object)
    actual = dict(report["identity"])
    runtime = actual.pop("podman", None)
    status = proofs.ProofStatus(report["status"])
    if (
        set(report)
        != {
            "version",
            "request",
            "identity",
            "status",
            "diagnostic",
            "axioms",
            "elapsed_ns",
        }
        or type(report["version"]) is not int
        or report["version"] != 2
        or report["request"] != _digest(request)
        or actual != {**identity, "solution": solution.artifact.digest}
        or type(report["elapsed_ns"]) is not int
        or report["elapsed_ns"] < 0
        or type(report["diagnostic"]) is not str
        or type(report["axioms"]) is not list
        or any(type(a) is not str for a in report["axioms"])
        or len(set(report["axioms"])) != len(report["axioms"])
        or not set(report["axioms"]) <= {"propext", "Quot.sound"}
        or raw["Solution.lean"] != ledger.read_artifact(solution.artifact)
        or raw["Challenge.lean"] != ledger.read_artifact(target.artifact)
        or hashlib.sha256(raw["Challenge.lean"]).hexdigest() != identity["challenge"]
        or hashlib.sha256(raw["config.json"]).hexdigest() != identity["config"]
        or hashlib.sha256(raw["bundle.json"]).hexdigest() != identity["bundle"]
        or (
            status is not proofs.ProofStatus.INFRASTRUCTURE_FAILURE
            and type(runtime) is not dict
        )
    ):
        return Status.UNSUPPORTED
    if status in (proofs.ProofStatus.PROVED, proofs.ProofStatus.REJECTED):
        verdict = json.loads(raw["verdict.json"], object_pairs_hook=_json_object)
        verdict["axioms"] = sorted(verdict["axioms"])
        if verdict != {
            name: report[name] for name in ("status", "diagnostic", "axioms")
        }:
            return Status.UNSUPPORTED
        if status is proofs.ProofStatus.PROVED and not all(
            raw[name] for name in ("solution.ndjson", "challenge.ndjson")
        ):
            return Status.UNSUPPORTED
    return (
        Status.PASSED if status is proofs.ProofStatus.PROVED else Status(status.value)
    )


def proof_status(ledger: Ledger, request: Request, target: Evidence) -> Status:
    """Assess one exact theorem receipt, never raw claimed success or other inputs."""
    try:
        _, expected, _ = _spec(ledger, request)
        if target != expected:
            return Status.UNSUPPORTED
        operation = ledger.lookup(request)
        if operation is None:
            return Status.MISSING
        if operation.completion is None:
            return (
                Status.UNSUPPORTED
                if any(o.origin == request.origin for o in ledger.history())
                else Status.UNKNOWN
            )
        completion = operation.completion
        raw = {
            name: ledger.read_artifact(ref)
            for name, ref in completion.observation.artifacts.items()
        }
        status = _decision(ledger, request, raw)
        result = completion.result
        if (
            result.usage != {"proof": 1}
            or completion.breaches
            or result.elapsed_ns != json.loads(raw["verification.json"])["elapsed_ns"]
            or result.outcome
            is not (
                Outcome.INFRASTRUCTURE_FAILURE
                if status is Status.INFRASTRUCTURE_FAILURE
                else Outcome.SUCCEEDED
            )
        ):
            return Status.UNSUPPORTED
        return status
    except (KeyError, TypeError, ValueError):
        return Status.UNSUPPORTED


class Proofs:
    """Host-selected policy; callers supply immutable evidence, never file paths."""

    def __init__(
        self,
        ledger: Ledger,
        session: str,
        bundle: Path,
        *,
        target_id: str,
        seconds: int = proofs.LIMITS["seconds"],
    ):
        self.ledger, self.session = ledger, session
        self.bundle = bundle.read_bytes()
        self.seconds = seconds
        self.target_id = target_id
        identity, raw = proofs._inputs(
            b"policy preparation", self.bundle, seconds, target_id
        )
        del identity["solution"]
        del raw["Solution.lean"]
        raw["policy.json"] = _encode(identity)
        event = record_once(
            ledger,
            session,
            Origin(
                "proof-policy/" + _digest(identity), "proof-policy", *PRODUCER[1:], {}
            ),
            raw,
        )
        self.target = Evidence.captured(event, "Challenge.lean")
        self.inputs = {
            Evidence.captured(event, name).name: ref
            for name, ref in event.artifacts.items()
        }

    def request(self, operation_id: str, solution: Evidence) -> Request:
        request = Request(
            Origin(
                operation_id,
                *PRODUCER,
                {**self.inputs, solution.name: solution.artifact},
            ),
            self.ledger.project,
        )
        _spec(self.ledger, request)
        return request

    def check(self, operation_id: str, solution: Evidence) -> Request:
        request = self.request(operation_id, solution)
        data = self.ledger.read_artifact(solution.artifact)
        proofs._validate(data, self.seconds)
        operation = self.ledger.reserve(self.session, request, {"proof": 1})
        if operation.state != "pending":
            return request
        unresolved(self.ledger)
        if not self.ledger.begin(self.session, request):
            return request
        # Exceptions (including uncertain cleanup) preserve UNKNOWN and reservation.
        verification = proofs._verify(
            data, self.bundle, target_id=self.target_id, seconds=self.seconds
        )
        report = asdict(verification)
        del report["raw"]
        report.update(version=2, request=_digest(request))
        raw = {**verification.raw, "verification.json": _encode(report)}
        try:
            status = _decision(self.ledger, request, raw)
        except (KeyError, TypeError, ValueError):
            status = Status.UNSUPPORTED
        if status is Status.UNSUPPORTED:
            self.ledger.record(self.session, request.origin, raw)
        else:
            failed = status is Status.INFRASTRUCTURE_FAILURE
            self.ledger.complete(
                self.session,
                request,
                Result(
                    Outcome.INFRASTRUCTURE_FAILURE if failed else Outcome.SUCCEEDED,
                    None if failed else 0,
                    {"proof": 1},
                    verification.elapsed_ns,
                ),
                raw,
            )
        return request
