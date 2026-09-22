"""Prepare and inspect scripted development trials; never dispatch work."""

import argparse
import json
import runpy
import sqlite3
from collections import Counter
from pathlib import Path
from uuid import uuid4

from warranted.acceptance import _digest, _encode
from warranted.ledger import Ledger, Manifest, Request, Snapshot

T = runpy.run_path(str(Path(__file__).with_name("treatments.py")))
Condition = T["Condition"]
LINEAGES = {"csv": "m2-controlled-csv-v1", "migration": "m5-controlled-migration-v1"}


def initialize(root: Path, bundle: Path, repetitions: int = 1) -> None:
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("repetitions must be a positive integer")
    root.mkdir(parents=True)
    trials = []
    for repeat in range(1, repetitions + 1):
        for family in LINEAGES:
            for condition in Condition:
                path = f"runs/{repeat:03d}-{family}-{condition}"
                T["initialize"](root / path, family, condition, bundle)
                with Ledger.open(root / path / "ledger") as ledger:
                    trials.append(
                        {
                            "path": path,
                            "repeat": repeat,
                            "family": family,
                            "condition": condition,
                            "project": _digest(ledger.project),
                            "manifest": ledger.project.manifest,
                        }
                    )
    plan = {
        "scope": "scripted-development",
        "split": "development",
        "lineages": LINEAGES,
        "repetitions": repetitions,
        "order": "repeat, family (csv then migration), condition (A through E)",
        "trials": trials,
        "proof_bundle_build_elapsed_ns": json.loads(bundle.read_bytes())[
            "build_elapsed_ns"
        ],
    }
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m5-scripted-trials",
            "1",
            str(uuid4()),
            str(uuid4()),
            {"split": "development", "model": "m5-fixed-two-proposals-v1"},
            {},
        ),
        {
            "plan.json": Snapshot(_encode(plan), "m5/trial-plan", "1"),
            "trials.py": Snapshot(
                Path(__file__).read_bytes(), "m5/trial-reporter", "1"
            ),
        },
    ):
        pass


def trial_result(ledger: Ledger) -> dict:
    """Read host receipts without running a worker, checker, or verifier."""
    operations = ledger.operations()
    balances = ledger.accounting()
    history = ledger.history()
    # An intact summary cannot hide lost raw evidence from its recorded trial.
    for snapshot in ledger.project.snapshots.values():
        ledger.read_artifact(snapshot.artifact)
    for event in history:
        for artifact in event.artifacts.values():
            ledger.read_artifact(artifact)
    stages = {}
    for stage in ("start", "resume"):
        events = [
            o for o in history if o.origin.operation_id == "treatment/result/" + stage
        ]
        if not events:
            continue
        if len(events) != 1:
            raise ValueError("ambiguous stage result")
        event = events[0]
        if (
            event.origin.kind,
            event.origin.producer,
            event.origin.producer_version,
        ) != ("treatment", "m5-host", "1") or set(event.artifacts) != {"result.json"}:
            raise ValueError("invalid stage result origin")
        ledger.lookup(Request(event.origin, ledger.project))
        result = json.loads(ledger.read_artifact(event.artifacts["result.json"]))
        env = ledger.project.manifest.environment
        if (result["stage"], result["family"], result["condition"]) != (
            stage,
            env["family"],
            env["condition"],
        ) or type(result["qualified"]) is not bool:
            raise ValueError("stage result context changed")
        stages[stage] = result
    pending = {
        op.request.origin.operation_id: op.state
        for op in operations
        if op.completion is None
    }
    breaches = {
        op.request.origin.operation_id: op.completion.breaches
        for op in operations
        if op.completion and op.completion.breaches
    }
    final = stages.get("resume")
    if "unknown" in pending.values():
        state = "unknown"
    elif breaches or any(b.available < 0 for b in balances.values()):
        state = "budget_breach"
    elif pending or not final or "start" not in stages:
        state = "incomplete"
    else:
        state = "completed"
    return {
        "state": state,
        "stages": stages,
        "qualified": state == "completed" and final["qualified"],
        "spent": {unit: b.spent for unit, b in balances.items()},
        "reserved": {unit: b.reserved for unit, b in balances.items()},
        "pending": pending,
        "breaches": breaches,
        "failed_operations": {
            op.request.origin.operation_id: op.completion.result.outcome
            for op in operations
            if op.completion and op.completion.result.outcome != "succeeded"
        },
        "proof_attempts": {
            op.request.origin.operation_id: json.loads(
                ledger.read_artifact(
                    op.completion.observation.artifacts["verification.json"]
                )
            )
            for op in operations
            if op.completion and op.request.origin.kind == "proof"
        },
        "elapsed_ns_by_kind": {
            kind: sum(
                op.completion.result.elapsed_ns
                for op in operations
                if op.completion and op.request.origin.kind == kind
            )
            for kind in {op.request.origin.kind for op in operations}
        },
    }


def report(root: Path) -> dict:
    with Ledger.open(root / "ledger") as ledger:
        if ledger.project.manifest.fixture_id != "m5-scripted-trials":
            raise ValueError("not a scripted trial plan")
        if (
            ledger.read_artifact(ledger.project.snapshots["trials.py"].artifact)
            != Path(__file__).read_bytes()
        ):
            raise ValueError("trial reporter changed")
        ref = ledger.project.snapshots["plan.json"]
        plan = json.loads(ledger.read_artifact(ref.artifact))
        plan_digest = ref.artifact.digest
    rows = []
    for trial in plan["trials"]:
        row = {
            "path": trial["path"],
            "family": trial["family"],
            "condition": trial["condition"],
        }
        location = root / trial["path"] / "ledger"
        if not location.exists():
            row.update(state="missing", qualified=False)
        else:
            try:
                with Ledger.open(location) as ledger:
                    if _digest(ledger.project) != trial["project"]:
                        raise ValueError(
                            "trial project differs from the predeclared plan"
                        )
                    balances = ledger.accounting()
                    row.update(
                        spent={unit: b.spent for unit, b in balances.items()},
                        reserved={unit: b.reserved for unit, b in balances.items()},
                    )
                    row.update(trial_result(ledger))
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                row.update(state="unreadable", qualified=False, error=str(error))
        rows.append(row)
    groups = []
    for family in LINEAGES:
        for condition in Condition:
            selected = [
                r for r in rows if (r["family"], r["condition"]) == (family, condition)
            ]
            groups.append(
                {
                    "family": family,
                    "condition": condition,
                    "planned": len(selected),
                    "task_accepted": sum(
                        r["state"] == "completed"
                        and r["stages"]["resume"]["candidate"]["status"] == "accepted"
                        for r in selected
                    ),
                    "qualified": sum(r["qualified"] for r in selected),
                    "states": dict(Counter(r["state"] for r in selected)),
                }
            )
    units = sorted({unit for row in rows for unit in row.get("spent", {})})
    return {
        "plan_digest": plan_digest,
        "plan": plan,
        "trials": rows,
        "groups": groups,
        "usage_complete": all(
            r["state"] not in ("missing", "unreadable", "unknown") and not r["pending"]
            for r in rows
        ),
        "known_spent": {
            unit: sum(r.get("spent", {}).get(unit, 0) for r in rows) for unit in units
        },
        "known_reserved": {
            unit: sum(r.get("reserved", {}).get(unit, 0) for r in rows)
            for unit in units
        },
        "cost_scope": (
            "Recorded trial units and operation durations only. "
            "Monetary cost, human effort, and development costs are unmeasured."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("root", type=Path)
    init.add_argument("--bundle", type=Path, required=True)
    init.add_argument("--repetitions", type=int, default=1)
    commands.add_parser("report").add_argument("root", type=Path)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.root.resolve(), args.bundle.resolve(), args.repetitions)
    print(_encode(report(args.root.resolve())).decode())


if __name__ == "__main__":
    main()
