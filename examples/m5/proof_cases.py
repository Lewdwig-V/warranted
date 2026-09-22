"""Three supported narrow proofs alongside unchanged independent task checks."""

import argparse
import calendar
import hashlib
import json
import os
import runpy
import signal
from dataclasses import asdict
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ["MSWEA_SILENT_STARTUP"] = "1"

from warranted import proofs  # noqa: E402
from warranted.acceptance import (
    Acceptance,
    AcceptanceContext,
    Evidence,
    Status,
    _encode,
)  # noqa: E402
from warranted.claims import Applicability  # noqa: E402
from warranted.exports import export_evidence  # noqa: E402
from warranted.ledger import Ledger, Manifest, Origin, Outcome, Snapshot  # noqa: E402
from warranted.proof_receipts import Proofs, proof_status  # noqa: E402
from warranted.sandbox import SANDBOX_ID  # noqa: E402
from warranted.worker import Episode, record_once, unresolved  # noqa: E402

HERE = Path(__file__).resolve().parent
M4 = runpy.run_path(str(HERE.parent / "m4/demo.py"))
M2 = M4["M2"]
M5 = runpy.run_path(str(HERE / "demo.py"))


def timestamp_correspondence(source: bytes, candidate: dict, minutes: int) -> dict:
    if type(minutes) is not int:
        return {"domain": False, "correspondence": False}
    rows, totals = [], {}
    for identifier, local, value in M2["read_rows"](source):
        seconds = calendar.timegm(
            datetime.strptime(local, "%Y-%m-%dT%H:%M:%S").timetuple()
        )
        utc = datetime.fromtimestamp(seconds - minutes * 60, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows.append([identifier, utc, value])
        totals[utc[:10]] = totals.get(utc[:10], 0) + value
    expected = {"rows": rows, "totals": totals}
    return {
        "domain": True,
        "correspondence": _encode(candidate) == _encode(expected),
        "model_output": expected,
    }


def migration_model(value: dict, keep_label: bool) -> dict:
    if type(keep_label) is not bool or type(value) is not dict:
        raise ValueError("outside the migration model domain")
    if M5["valid_output"](value):
        return dict(value)
    required = {"version", "host", "timeout"}
    if (
        not required <= value.keys() <= required | {"label"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("outside the migration model domain")
    result = {
        "version": 2,
        "endpoint": value["host"],
        "timeout_seconds": value["timeout"],
    }
    if "label" in value:
        result["label"] = value["label"]
    if not M5["valid_output"](result):
        raise ValueError("outside the migration model domain")
    if not keep_label:
        result.pop("label", None)
    return result


def migration_correspondence(
    cases: dict, raw: dict[str, bytes], keep_label: bool
) -> dict:
    matches, outputs = {}, {}
    domain = type(keep_label) is bool
    for name, case in cases.items():
        if case["kind"] == "invalid":
            # The formal model covers valid configurations, not parser rejection.
            continue
        try:
            expected = migration_model(M5["decode"](case["input"].encode()), keep_label)
        except ValueError:
            domain = matches[name] = False
            continue
        value = None
        try:
            state = M5["decode"](raw[name + "/execution.json"])
            if state["returncode"] == 0 and not (
                state["timed_out"] or state["output_limited"]
            ):
                value = M5["decode"](raw[name + "/stdout"])
        except (KeyError, ValueError, RecursionError):
            pass
        matches[name] = M5["valid_output"](value) and value == expected
        outputs[name] = expected
    return {
        "domain": domain,
        "correspondence": bool(matches) and all(matches.values()),
        "cases": matches,
        "model_outputs": outputs,
    }


def snapshots(bundle: Path) -> dict[str, Snapshot]:
    captured = M4["snapshots"](bundle)
    captured.update(
        {"migration/" + name: value for name, value in M5["snapshots"]().items()}
    )
    for name, path in {
        "proof-cases.py": Path(__file__),
        "proof-intent.json": HERE / "proof-intent.json",
        "proof/migration": HERE / "Migration.lean",
        "proof/timestamp": HERE / "Timestamp.lean",
    }.items():
        captured[name] = Snapshot(path.read_bytes(), "m5/" + name, "1")
    for target_id in ("uniqueness", "migration", "timestamp"):
        target = proofs._target(target_id)
        captured["target/" + target_id] = Snapshot(
            (proofs.RESOURCES / target["challenge"]).read_bytes(),
            "m5/approved-target/" + target_id,
            "1",
        )
    intent = M5["decode"](captured["proof-intent.json"].data)
    for family in ("migration", "timestamp"):
        for name, value in intent[family]["variants"].items():
            captured[f"model/{family}/{name}"] = Snapshot(
                _encode(value), f"m5/proof-intent#{family}/{name}", "1"
            )
    return captured


def environment() -> dict:
    return {
        **M4["environment"](),
        "policy": "fixed-three-proof-cases-v1",
        "migration_runtime": SANDBOX_ID,
    }


def initialize(root: Path, bundle: Path) -> None:
    root.mkdir(parents=True)
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m5-supported-proofs",
            "1",
            str(uuid4()),
            str(uuid4()),
            environment(),
            {"proof": 3, "synthetic-work": 12, "batch": 2, "check": 2},
        ),
        snapshots(bundle),
    ):
        pass


def validate(ledger: Ledger, bundle: Path) -> None:
    captured = snapshots(bundle)
    if (
        ledger.project.manifest.environment != environment()
        or captured.keys() != ledger.project.snapshots.keys()
    ):
        raise ValueError("fixture or environment changed")
    for name, value in captured.items():
        ref = ledger.project.snapshots[name]
        if (ref.origin, ref.version, ledger.read_artifact(ref.artifact)) != (
            value.origin,
            value.version,
            value.data,
        ):
            raise ValueError("fixture, bundle, or host changed: " + name)


def proof_work(host, bundle: Path) -> tuple[dict, dict]:
    theorems, outcomes = {}, {}
    for target_id, source, operation_id in (
        ("uniqueness", "Solution.lean", "m4-proof/control"),
        ("migration", "proof/migration", "m5-proof/migration"),
        ("timestamp", "proof/timestamp", "m5-proof/timestamp"),
    ):
        adapter = Proofs(host.ledger, host.session, bundle, target_id=target_id)
        if adapter.target.artifact != host.ref("target/" + target_id).artifact:
            raise ValueError("verifier target differs from the approved fixture target")
        request = adapter.check(operation_id, host.ref(source))
        status = proof_status(host.ledger, request, adapter.target)
        if status is not Status.PASSED:
            raise RuntimeError(f"{target_id} proof is {status}")
        theorems[target_id] = host.claims.record(
            "The approved conditional " + target_id + " theorem.",
            adapter.target,
            {},
            validation=(request, "proof"),
            complete=True,
        )
        completion = host.ledger.lookup(request).completion
        outcomes[target_id] = {
            "status": status,
            "request": operation_id,
            "receipt": asdict(
                Evidence.captured(completion.observation, "verification.json")
            ),
            "elapsed_ns": completion.result.elapsed_ns,
        }
    return theorems, outcomes


def application(
    host, family: str, theorem: Evidence, target: Evidence, inputs: dict, compute
) -> dict:
    pinned = host.claims._claim(theorem)
    if (
        Evidence.restored(pinned["target"]).artifact
        != host.ref("target/" + family).artifact
    ):
        raise ValueError("application names another proof target")
    operation = host.perform("m5-correspondence/" + family, inputs, compute)
    claims = {"theorem": theorem}
    for field in ("domain", "correspondence"):
        claims[field] = host.claims.record(
            family + " application: " + field,
            target,
            inputs,
            validation=(operation.request, field),
            complete=True,
        )
    assessed = {name: host.claims.assess(ref, inputs) for name, ref in claims.items()}
    statuses = {value.validation for value in assessed.values()}
    if any(value.applicability is Applicability.STALE for value in assessed.values()):
        status = "stale"
    elif any(
        value.applicability is not Applicability.CURRENT for value in assessed.values()
    ):
        status = "unknown"
    elif Status.INFRASTRUCTURE_FAILURE in statuses:
        status = "infrastructure_failure"
    elif statuses & {Status.UNKNOWN, Status.MISSING}:
        status = "unknown"
    else:
        status = "supported" if statuses == {Status.PASSED} else "unsupported"
    return {
        "status": status,
        "checks": host.result(operation),
        "claims": {name: asdict(ref) for name, ref in claims.items()},
        "evidence": {
            name: {"validation": value.validation, "applicability": value.applicability}
            for name, value in assessed.items()
        },
    }


def csv_cases(host, theorems: dict) -> dict:
    host.source_style()
    matrix = {"uniqueness": {}, "timestamp": {}}
    source = host.ref("input.csv")
    for name in ("correct", "dropped-row", "empty"):
        target = host.ref("candidate/" + name)
        app = M4["application"](host, name, target, source, theorems["uniqueness"])
        checked = host.check(target, M2["Interpretation"]())
        matrix["uniqueness"][name] = {
            "support": M4["support"](host, app, theorems["uniqueness"], source),
            "checks": host.result(checked),
            "decision": host.decide(target, checked.request.origin.operation_id),
        }
    for name in ("correct", "wrong-offset"):
        target = host.ref("candidate/" + name)
        model = host.ref("model/timestamp/" + name)
        support = application(
            host,
            "timestamp",
            theorems["timestamp"],
            target,
            {"candidate": target, "source": source, "model": model},
            lambda target=target, model=model: timestamp_correspondence(
                host.ledger.read_artifact(source.artifact),
                host.read(target),
                host.read(model),
            ),
        )
        checked = host.check(target, M2["Interpretation"]())
        matrix["timestamp"][name] = {
            "support": support,
            "checks": host.result(checked),
            "decision": host.decide(target, checked.request.origin.operation_id),
        }
    return matrix


def migration_cases(host, theorem: Evidence) -> dict:
    ledger, root, session = host.ledger, host.root, host.session
    cases_ref = host.ref("migration/references.json")
    cases = host.read(cases_ref)
    policy = host.ref("migration/policy-revised.json")
    revision = host.ref("migration/contracts.json")
    fields = host.read(policy)["requirements"]
    matrix = {}
    for name in ("drop-label", "complete"):
        target = host.ref(f"migration/candidate-{name}.json")
        source = M5["candidate_source"](ledger.read_artifact(target.artifact))
        req = M5["request"](
            ledger,
            "migration-batch",
            (
                target.name,
                "migration/host.py",
                "migration/sandbox.py",
                "migration/containers.py",
                cases_ref.name,
            ),
        )
        episode = Episode(
            "proof-case-" + name,
            "Assess captured migration.",
            (),
            environment=SANDBOX_ID,
        )
        batch = M5["perform"](
            ledger,
            session,
            req,
            "batch",
            partial(
                M5["execute"],
                root,
                episode,
                {key: case["input"] for key, case in cases.items()},
                source,
            ),
        )
        evidence = {
            Evidence.captured(batch.observation, channel).name: ref
            for channel, ref in batch.observation.artifacts.items()
        }
        req = M5["request"](
            ledger,
            "migration-check",
            (
                target.name,
                policy.name,
                revision.name,
                cases_ref.name,
                "migration/repository.json",
                "migration/host.py",
            ),
            evidence,
        )
        checked = M5["perform"](
            ledger,
            session,
            req,
            "check",
            partial(M5["evaluate"], ledger, cases, batch, True, fields),
        )
        context = AcceptanceContext(policy, revision, {"evaluate": req})
        decision = Acceptance(
            ledger, session, partial(M5["resolve"], target, context)
        ).accept(target, {"evaluate": req.origin.operation_id})
        model = host.ref("model/migration/" + name)
        inputs = {
            "candidate": target,
            "model": model,
            "cases": cases_ref,
            **{key: Evidence(key, ref) for key, ref in evidence.items()},
        }

        def correspondence(batch=batch, model=model):
            raw = {
                channel: ledger.read_artifact(ref)
                for channel, ref in batch.observation.artifacts.items()
            }
            result = migration_correspondence(cases, raw, host.read(model))
            if batch.result.outcome is not Outcome.SUCCEEDED:
                result["correspondence"] = False
            return result

        support = application(
            host, "migration", theorem, target, inputs, correspondence
        )
        matrix[name] = {
            "support": support,
            "decision": decision.status,
            "checks": M5["decode"](
                ledger.read_artifact(checked.observation.artifacts["result.json"])
            ),
        }
    return matrix


def demonstrate(stage: str, root: Path, bundle: Path, *, crash: bool = False) -> dict:
    execute = proofs._verify

    def witnessed(data, captured, *, target_id, seconds):
        with (root / "proof-executions.jsonl").open("ab") as stream:
            stream.write(
                _encode(
                    {"target": target_id, "source": hashlib.sha256(data).hexdigest()}
                )
                + b"\n"
            )
        return execute(data, captured, target_id=target_id, seconds=seconds)

    with (
        Ledger.open(root / "ledger") as ledger,
        patch.object(proofs, "_verify", witnessed),
    ):
        validate(ledger, bundle)
        unresolved(ledger)
        before = len(ledger.operations())
        host = M2["Experiment"](ledger, ledger.start_session(), root)
        if stage == "resume" and not any(
            o.origin.operation_id == "m5-proof-checkpoint" for o in ledger.history()
        ):
            raise ValueError("missing committed proof checkpoint")
        theorems, outcomes = proof_work(host, bundle)
        record_once(
            ledger,
            host.session,
            Origin(
                "m5-proof-checkpoint",
                "checkpoint",
                "m5-proof-cases",
                "1",
                host.origins(theorems),
            ),
            {
                "checkpoint.json": _encode(
                    {name: asdict(ref) for name, ref in theorems.items()}
                )
            },
        )
        report = {"stage": stage, "proofs": outcomes, "environment": environment()}
        if stage == "resume":
            report["matrix"] = {
                **csv_cases(host, theorems),
                "migration": migration_cases(host, theorems["migration"]),
            }
        balances = ledger.accounting()
        (root / "exports").mkdir(exist_ok=True)
        report.update(
            spent={key: value.spent for key, value in balances.items()},
            reserved={key: value.reserved for key, value in balances.items()},
            proof_executions=M4["count"](root / "proof-executions.jsonl"),
            checker_executions=M4["count"](root / "executions.jsonl"),
            new_operations=len(ledger.operations()) - before,
            elapsed_ns_by_kind={
                kind: sum(
                    op.completion.result.elapsed_ns
                    for op in ledger.operations()
                    if op.completion and op.request.origin.kind == kind
                )
                for kind in {op.request.origin.kind for op in ledger.operations()}
            },
            proof_bundle_build_elapsed_ns=json.loads(bundle.read_bytes())[
                "build_elapsed_ns"
            ],
            export=str(
                export_evidence(
                    ledger,
                    root / "exports" / host.session,
                    snapshots=tuple(ledger.project.snapshots),
                    observations={
                        o.sequence: tuple(o.artifacts) for o in ledger.history()
                    },
                    operations=tuple(
                        op.request.origin.operation_id for op in ledger.operations()
                    ),
                )
            ),
        )
        (root / "reports").mkdir(exist_ok=True)
        for name in (stage, host.session):
            (root / "reports" / (name + ".json")).write_bytes(_encode(report))
        if crash:
            os.kill(os.getpid(), signal.SIGKILL)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--crash", action="store_true")
    args = parser.parse_args()
    if args.crash and args.stage != "start":
        parser.error("--crash requires start")
    root, bundle = args.root.resolve(), args.bundle.resolve()
    if args.stage == "start" and not (root / "ledger").exists():
        initialize(root, bundle)
    report = demonstrate(args.stage, root, bundle, crash=args.crash)
    if args.stage == "resume" and any(
        item["support"]["status"] != "supported"
        or item["decision"]
        != ("accepted" if name in ("correct", "complete") else "rejected")
        for cases in report["matrix"].values()
        for name, item in cases.items()
    ):
        raise RuntimeError("proof cases did not establish the required outcomes")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
