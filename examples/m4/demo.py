"""Apply one checked conditional theorem without weakening M2 task acceptance."""

import argparse
import hashlib
import json
import os
import platform
import runpy
import signal
from dataclasses import asdict
from pathlib import Path
from time import perf_counter_ns
from unittest.mock import patch
from uuid import uuid4

os.environ["MSWEA_SILENT_STARTUP"] = "1"

from warranted import proof_receipts, proofs  # noqa: E402
from warranted.acceptance import Evidence, Status, _digest  # noqa: E402
from warranted.claims import Applicability  # noqa: E402
from warranted.exports import export_evidence  # noqa: E402
from warranted.ledger import Ledger, Manifest, Origin, Request, Snapshot  # noqa: E402
from warranted.proof_receipts import Proofs, proof_status  # noqa: E402
from warranted.worker import record_once, unresolved  # noqa: E402

HERE = Path(__file__).resolve().parent
M2 = runpy.run_path(str(HERE.parent / "m2/experiments.py"))
FIELDS = ("injective", "unique_input", "correspondence")
MAPPING = {"version": 1, "function": "identity", "equality": "exact"}


def premises(source: bytes, candidate: dict, selection: list, mapping: dict) -> dict:
    rows = M2["read_rows"](source)
    if type(selection) is not list or any(
        type(index) is not int or not 0 <= index < len(rows) for index in selection
    ):
        raise ValueError("selection must contain source row indices")
    identifiers = [rows[index][0] for index in selection]
    output = candidate.get("rows") if type(candidate) is dict else None
    valid = type(output) is list and all(
        type(row) is list and len(row) == 3 and type(row[0]) is str for row in output
    )
    candidate_ids = [row[0] for row in output] if valid else None
    return {
        # The host supports only the identity function, not sampled injectivity.
        "injective": type(mapping) is dict
        and mapping == MAPPING
        and type(mapping["version"]) is int,
        "unique_input": M2["unique_ids"](identifiers, "exact"),
        "correspondence": valid and candidate_ids == identifiers,
        "selected_ids": identifiers,
        "candidate_ids": candidate_ids,
    }


def support_status(assessments: dict) -> str:
    if set(assessments) != {"theorem", *FIELDS}:
        return "unknown"
    if any(a.applicability is Applicability.STALE for a in assessments.values()):
        return "stale"
    if any(a.applicability is not Applicability.CURRENT for a in assessments.values()):
        return "unknown"
    values = {a.validation for a in assessments.values()}
    if Status.INFRASTRUCTURE_FAILURE in values:
        return "infrastructure_failure"
    if values & {Status.UNKNOWN, Status.MISSING}:
        return "unknown"
    return "supported" if values == {Status.PASSED} else "unsupported"


def snapshots(bundle: Path) -> dict:
    captured = M2["snapshots"](HERE.parent / "m2/fixture")
    for name, path in {
        "m4-host.py": Path(__file__),
        "m4-intent.json": HERE / "intent.json",
        "input-duplicates.csv": HERE / "input-duplicates.csv",
        "Solution.lean": HERE / "Solution.lean",
        "Challenge.lean": proofs.RESOURCES / "Challenge.lean",
        "proofs.py": Path(proofs.__file__),
        "proof_receipts.py": Path(proof_receipts.__file__),
        "proof-bundle.json": bundle,
    }.items():
        captured[name] = Snapshot(path.read_bytes(), f"m4/{name}", "1")
    intent = M2["decode"](captured["m4-intent.json"].data)
    for name, selection in intent["selections"].items():
        captured[f"selection/{name}"] = Snapshot(
            M2["encode"](selection), f"m4/intent.json#selections/{name}", "1"
        )
    captured["mapping"] = Snapshot(
        M2["encode"](intent["mapping"]), "m4/intent.json#mapping", "1"
    )
    captured["Incomplete.lean"] = Snapshot(
        captured["Challenge.lean"].data.replace(b"by sorry", b"by skip"),
        "m4/demo.py#incomplete-proposal",
        "1",
    )
    captured["Timeout.lean"] = Snapshot(
        captured["Solution.lean"].data.replace(
            b"import Init\n", b"import Init\n#eval IO.sleep 200000\n"
        ),
        "m4/demo.py#timeout-proposal",
        "1",
    )
    duplicate = M2["decode"](captured["candidate/correct"].data)
    duplicate["rows"][1][0] = "r1"
    captured["candidate/duplicate-ids"] = Snapshot(
        M2["encode"](duplicate), "m4/demo.py#duplicate-candidate", "1"
    )
    return captured


def environment():
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "model": "none",
        "policy": "fixed-m4-proposals-v1",
        "evaluator": "four-obligations-v1",
        "split": "development",
        "scenario": "matrix",
    }


def initialize(root: Path, bundle: Path):
    root.mkdir(parents=True)
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m4-uniqueness-applications",
            "1",
            str(uuid4()),
            str(uuid4()),
            environment(),
            {"proof": 3, "synthetic-work": 8},
        ),
        snapshots(bundle),
    ):
        pass


def validate(ledger: Ledger, bundle: Path):
    captured = snapshots(bundle)
    if (
        set(captured) != set(ledger.project.snapshots)
        or ledger.project.manifest.environment != environment()
    ):
        raise ValueError("fixture or environment changed")
    for name, value in captured.items():
        ref = ledger.project.snapshots[name]
        if (ref.origin, ref.version, ledger.read_artifact(ref.artifact)) != (
            value.origin,
            value.version,
            value.data,
        ):
            raise ValueError("fixture, tool bundle, or host bytes changed")


def proof_work(host, bundle: Path) -> tuple[Evidence, dict]:
    outcomes = {}
    theorem = None
    for name, source, seconds in (
        ("control", "Solution.lean", 120),
        ("incomplete", "Incomplete.lean", 120),
        ("timeout", "Timeout.lean", 5),
    ):
        adapter = Proofs(host.ledger, host.session, bundle, seconds=seconds)
        request = adapter.check("m4-proof/" + name, host.ref(source))
        status = proof_status(host.ledger, request, adapter.target)
        expected = Status.PASSED if name == "control" else Status.UNPROVED
        if status is not expected:
            raise RuntimeError(f"{name} verification is {status}; expected {expected}")
        operation = host.ledger.lookup(request)
        outcomes[name] = {
            "status": status.value,
            "request": request.origin.operation_id,
            "elapsed_ns": operation.completion.result.elapsed_ns,
            "receipt": asdict(
                Evidence.captured(operation.completion.observation, "verification.json")
            ),
        }
        if name == "control":
            theorem = host.claims.record(
                "An injective mapping preserves identifier uniqueness.",
                adapter.target,
                {},
                validation=(request, "proof"),
                complete=True,
            )
    return theorem, outcomes


def application(
    host, name: str, candidate: Evidence, source: Evidence, theorem: Evidence
) -> Evidence:
    inputs = {
        "candidate": candidate,
        "source": source,
        "selection": host.ref("selection/" + name),
        "mapping": host.ref("mapping"),
    }
    operation = host.perform(
        "m4-premises",
        inputs,
        lambda: premises(
            host.ledger.read_artifact(source.artifact),
            host.read(candidate),
            host.read(inputs["selection"]),
            host.read(inputs["mapping"]),
        ),
    )
    claims = {"theorem": theorem}
    for field in FIELDS:
        claims[field] = host.claims.record(
            f"Uniqueness application premise: {field}.",
            candidate,
            inputs,
            validation=(operation.request, field),
            complete=True,
        )
    body = {
        "version": 1,
        "inputs": {name: asdict(ref) for name, ref in inputs.items()},
        "claims": {name: asdict(ref) for name, ref in claims.items()},
    }
    event = record_once(
        host.ledger,
        host.session,
        Origin(
            "m4-application/" + _digest(body),
            "proof-application",
            "m4-fixture",
            "1",
            host.origins({**inputs, **claims}),
        ),
        {"application.json": M2["encode"](body)},
    )
    return Evidence.captured(event, "application.json")


def support(host, application: Evidence, theorem: Evidence, source: Evidence) -> dict:
    event = next(
        (
            o
            for o in host.ledger.history()
            if "application.json" in o.artifacts
            and Evidence.captured(o, "application.json") == application
        ),
        None,
    )
    if event is None:
        raise ValueError("not a captured application")
    body = host.read(application)
    inputs = {name: Evidence.restored(ref) for name, ref in body["inputs"].items()}
    claims = {name: Evidence.restored(ref) for name, ref in body["claims"].items()}
    if (
        type(body["version"]) is not int
        or body["version"] != 1
        or set(inputs) != {"candidate", "source", "selection", "mapping"}
        or set(claims) != {"theorem", *FIELDS}
        or claims["theorem"] != theorem
        or event.origin
        != Origin(
            "m4-application/" + _digest(body),
            "proof-application",
            "m4-fixture",
            "1",
            host.origins({**inputs, **claims}),
        )
    ):
        raise ValueError("application does not match the host evidence")
    host.ledger.lookup(Request(event.origin, host.ledger.project))
    control = next(
        (
            op
            for op in host.ledger.operations()
            if op.request.origin.operation_id == "m4-proof/control"
        ),
        None,
    )
    checked = host.claims._claim(theorem)
    if (
        control is None
        or Evidence.restored(checked["target"]).artifact
        != host.ref("Challenge.lean").artifact
        or checked["assumptions"] != {}
        or checked["validation"]
        != {
            "operation_id": "m4-proof/control",
            "request": _digest(control.request),
            "field": "proof",
        }
    ):
        raise ValueError("application needs the pinned conditional theorem receipt")
    # Bind every field to the designated checker and candidate, not any passing claim.
    request = host.request("m4-premises", inputs)
    for field in FIELDS:
        claim = host.claims._claim(claims[field])
        if (
            claim["target"] != asdict(inputs["candidate"])
            or claim["assumptions"] != body["inputs"]
            or claim["validation"]
            != {
                "operation_id": request.origin.operation_id,
                "request": _digest(request),
                "field": field,
            }
        ):
            raise ValueError("application premise names a different check")
    current = {**inputs, "source": source, "mapping": host.ref("mapping")}
    assessments = {
        name: host.claims.assess(ref, current) for name, ref in claims.items()
    }
    return {
        "status": support_status(assessments),
        "application": asdict(application),
        "candidate": asdict(inputs["candidate"]),
        "current_source": asdict(source),
        "evidence": {
            name: {
                "claim": asdict(claims[name]),
                "validation": a.validation.value,
                "applicability": a.applicability.value,
            }
            for name, a in assessments.items()
        },
    }


def revise(host, original: Evidence, proposed: Evidence) -> Evidence:
    intent = host.read(host.ref("m4-intent.json"))
    approval = intent["revision"]
    if (
        type(intent["version"]) is not int
        or intent["version"] != 1
        or intent["owner"] != host.read(host.ref("acceptance.json"))["owner"]
        or approval["checkpoint"] != "after-initial-applications"
        or approval["previous"] != "input.csv"
        or proposed != host.ref(approval["current"])
    ):
        raise ValueError("premise revision is not approved by the pinned owner")
    inputs = {
        "approval": host.ref("m4-intent.json"),
        "previous": host.ref(approval["previous"]),
        "current": proposed,
        "application": original,
    }
    body = {
        "version": 1,
        "owner": intent["owner"],
        **approval,
        "evidence": {name: asdict(ref) for name, ref in inputs.items()},
    }
    event = record_once(
        host.ledger,
        host.session,
        Origin(
            "m4-premise-revision",
            "premise-revision",
            "m4-fixture",
            "1",
            host.origins(inputs),
        ),
        {"revision.json": M2["encode"](body)},
    )
    return Evidence.captured(event, "revision.json")


def count(path: Path):
    return len(path.read_bytes().splitlines()) if path.exists() else 0


def demonstrate(stage: str, root: Path, bundle: Path) -> dict:
    started = perf_counter_ns()
    execute = proofs._verify

    def witnessed(data, captured_bundle, *, seconds):
        with (root / "proof-executions.jsonl").open("ab") as stream:
            stream.write(
                json.dumps(
                    {"solution": hashlib.sha256(data).hexdigest(), "seconds": seconds}
                ).encode()
                + b"\n"
            )
        return execute(data, captured_bundle, seconds=seconds)

    with (
        Ledger.open(root / "ledger") as ledger,
        patch.object(proofs, "_verify", witnessed),
    ):
        validate(ledger, bundle)
        unresolved(ledger)
        before = {op.request.origin.operation_id for op in ledger.operations()}
        host = M2["Experiment"](ledger, ledger.start_session(), root)
        if stage == "resume" and not any(
            o.origin.operation_id == "m4-proof-checkpoint" for o in ledger.history()
        ):
            raise ValueError("missing proof checkpoint; run start first")
        proof_started = perf_counter_ns()
        theorem, outcomes = proof_work(host, bundle)
        proof_stage_elapsed_ns = perf_counter_ns() - proof_started
        record_once(
            ledger,
            host.session,
            Origin(
                "m4-proof-checkpoint",
                "checkpoint",
                "m4-fixture",
                "1",
                {theorem.name: theorem.artifact},
            ),
            {"checkpoint.json": M2["encode"]({"theorem": asdict(theorem)})},
        )
        report = {
            "stage": stage,
            "session": host.session,
            "project_id": ledger.project.project_id,
            "fixture_version": ledger.project.manifest.fixture_version,
            "environment": dict(ledger.project.manifest.environment),
            "proof_attempts": outcomes,
            "proof_stage_elapsed_ns": proof_stage_elapsed_ns,
        }
        if stage == "resume":
            initial = host.ref("input.csv")
            host.source_style()
            matrix, applications = {}, {}
            for name in host.read(host.ref("m4-intent.json"))["selections"]:
                target = host.ref("candidate/" + name)
                app = application(host, name, target, initial, theorem)
                applications[name] = app
                check = host.check(target, M2["Interpretation"]())
                matrix[name] = {
                    "proof_application": support(host, app, theorem, initial),
                    "checks": host.result(check),
                    "decision": host.decide(target, check.request.origin.operation_id),
                }
            revised = host.ref("input-duplicates.csv")
            revision = revise(host, applications["correct"], revised)
            revised = Evidence.restored(host.read(revision)["evidence"]["current"])
            changed = application(
                host, "correct", host.ref("candidate/duplicate-ids"), revised, theorem
            )
            report.update(
                matrix=matrix,
                premise_revision={
                    "revision": asdict(revision),
                    "previous": support(
                        host, applications["correct"], theorem, revised
                    ),
                    "current": support(host, changed, theorem, revised),
                },
            )
        operations = ledger.operations()
        balances = ledger.accounting()
        report.update(
            spent={unit: b.spent for unit, b in balances.items()},
            reserved={unit: b.reserved for unit, b in balances.items()},
            limits={unit: b.limit for unit, b in balances.items()},
            proof_executions=count(root / "proof-executions.jsonl"),
            checker_executions=count(root / "executions.jsonl"),
            new_executions=sum(
                op.request.origin.operation_id not in before and bool(op.reservation)
                for op in operations
            ),
            elapsed_ns_by_kind={
                kind: sum(
                    op.completion.result.elapsed_ns
                    for op in operations
                    if op.request.origin.kind == kind and op.completion
                )
                for kind in {op.request.origin.kind for op in operations}
            },
            proof_bundle_build_elapsed_ns=json.loads(bundle.read_bytes()).get(
                "build_elapsed_ns"
            ),
        )
        (root / "exports").mkdir(exist_ok=True)
        report["export"] = str(
            export_evidence(
                ledger,
                root / "exports" / host.session,
                snapshots=tuple(
                    name
                    for name in ledger.project.snapshots
                    if not name.endswith(".py")
                ),
                observations={o.sequence: tuple(o.artifacts) for o in ledger.history()},
                operations=tuple(op.request.origin.operation_id for op in operations),
            )
        )
        (root / "reports").mkdir(exist_ok=True)
        report["host_elapsed_ns"] = perf_counter_ns() - started
        raw = M2["encode"](report)
        (root / "reports" / f"{host.session}.json").write_bytes(raw)
        (root / "reports" / f"{stage}.json").write_bytes(raw)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--crash",
        action="store_true",
        help="kill this host after capturing its durable report",
    )
    args = parser.parse_args()
    root, bundle = args.root.resolve(), args.bundle.resolve()
    if args.stage == "start" and not (root / "ledger").exists():
        initialize(root, bundle)
    report = demonstrate(args.stage, root, bundle)
    if args.stage == "resume":
        matrix = report["matrix"]
        if (
            set(matrix) != {"correct", "dropped-row", "empty"}
            or any(
                item["proof_application"]["status"] != "supported"
                or item["decision"] != ("accepted" if name == "correct" else "rejected")
                for name, item in matrix.items()
            )
            or report["premise_revision"]["previous"]["status"] != "stale"
            or report["premise_revision"]["current"]["status"] != "unsupported"
        ):
            raise RuntimeError("demonstration did not establish the required outcomes")
    if args.crash:
        os.kill(os.getpid(), signal.SIGKILL)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
