"""A fixed local demonstration, not a general runner or worker sandbox.

Subprocess capture uses https://docs.python.org/3.12/library/subprocess.html#subprocess.run.
The fixture is trusted. Run start and resume as separate commands.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

from warranted.exports import export_evidence
from warranted.ledger import (
    ArtifactRef,
    Completion,
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
    SnapshotRef,
)

PUBLIC_INPUTS = ("input.csv", "contract.md", "transform.py")
EXPECTED = ("expected-normalized.csv", "expected-totals.json")
OUTPUTS = ("normalized.csv", "totals.json")


def snapshots(fixture: Path) -> dict[str, Snapshot]:
    captured = {
        name: Snapshot((fixture / name).read_bytes(), f"m1-fixture/{name}", "1")
        for name in (*PUBLIC_INPUTS, *EXPECTED)
    }
    captured["host.py"] = Snapshot(
        Path(__file__).read_bytes(), "m1/walkthrough.py", "1"
    )
    return captured


def environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "policy": "fixed-script-v1",
        "evaluator": "fixed-outputs-v1",
        "model": "none",
        "timeout_seconds": "10",
    }


def request(ledger: Ledger, captured: dict[str, Snapshot]) -> Request:
    # Reconstruct the caller's current inputs before checking a cached receipt.
    # Taking the cached request itself would conceal a changed fixture or host.
    refs = {
        name: SnapshotRef(
            ArtifactRef(hashlib.sha256(snapshot.data).hexdigest(), len(snapshot.data)),
            snapshot.origin,
            snapshot.version,
        )
        for name, snapshot in captured.items()
    }
    context = replace(
        ledger.project,
        manifest=replace(ledger.project.manifest, environment=environment()),
        snapshots=refs,
    )
    return Request(
        Origin(
            "transform-1",
            "script",
            "m1-walkthrough",
            "1",
            {name: refs[name].artifact for name in PUBLIC_INPUTS},
        ),
        context,
    )


def execute(
    ledger: Ledger, session: str, req: Request, candidate: Path
) -> tuple[Completion, bool]:
    operation = ledger.reserve(session, req, {"synthetic-work": 3})
    if operation.completion is not None:
        return operation.completion, True
    if operation.state == "unknown":
        raise RuntimeError(
            "operation outcome is unknown; reservation retained, no retry"
        )
    candidate.mkdir(exist_ok=True)
    for name in PUBLIC_INPUTS:
        (candidate / name).write_bytes(ledger.read_artifact(req.origin.inputs[name]))
    for name in OUTPUTS:
        (candidate / name).unlink(missing_ok=True)
    if not ledger.begin(session, req):
        raise RuntimeError("operation cannot be dispatched")
    started = perf_counter_ns()
    # Timeout or host failure leaves the operation unknown. No guessed settlement.
    process = subprocess.run(
        [sys.executable, "-I", "transform.py"],
        cwd=candidate,
        capture_output=True,
        timeout=10,
    )
    elapsed = perf_counter_ns() - started
    raw = {"stdout": process.stdout, "stderr": process.stderr}
    for name in OUTPUTS:
        if (candidate / name).is_file():
            raw[name] = (candidate / name).read_bytes()
    result = Result(
        Outcome.SUCCEEDED if process.returncode == 0 else Outcome.FAILED,
        process.returncode,
        {"synthetic-work": 2},
        elapsed,
    )
    return ledger.complete(session, req, result, raw), False


def check_outputs(ledger: Ledger, completion: Completion) -> dict[str, bool]:
    def rows(data: bytes) -> list[list[str]]:
        return list(csv.reader(io.StringIO(data.decode("utf-8")), strict=True))

    expected_rows = rows(
        ledger.read_artifact(ledger.project.snapshots[EXPECTED[0]].artifact)
    )
    expected_totals = json.loads(
        ledger.read_artifact(ledger.project.snapshots[EXPECTED[1]].artifact)
    )
    checks = {
        "process_exited_zero": completion.result.outcome is Outcome.SUCCEEDED,
        "normalized_rows": False,
        "daily_totals": False,
    }
    raw = completion.observation.artifacts
    if "normalized.csv" in raw:
        try:
            checks["normalized_rows"] = (
                rows(ledger.read_artifact(raw["normalized.csv"])) == expected_rows
            )
        except (UnicodeDecodeError, csv.Error):
            pass
    if "totals.json" in raw:
        try:
            totals = json.loads(ledger.read_artifact(raw["totals.json"]))
            checks["daily_totals"] = (
                type(totals) is dict
                and all(type(value) is int for value in totals.values())
                and totals == expected_totals
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return checks


def walkthrough(command: str, root: Path, fixture: Path) -> dict:
    root = root.absolute()
    captured = snapshots(fixture)
    if command == "start":
        root.mkdir(parents=True)
        (root / "exports").mkdir()
        (root / "reports").mkdir()
        manifest = Manifest(
            "csv-normalization",
            "1",
            str(uuid4()),
            str(uuid4()),
            environment(),
            {"synthetic-work": 5},
        )
        ledger = Ledger.create(root / "ledger", manifest, captured)
    else:
        ledger = Ledger.open(root / "ledger")
    with ledger:
        req = request(ledger, captured)
        ledger.lookup(req)  # Reject changed context before creating a new session.
        session = ledger.start_session()
        completion, reused = execute(ledger, session, req, root / "candidate")
        checks = check_outputs(ledger, completion)
        usage = ledger.accounting()["synthetic-work"]
        index = export_evidence(
            ledger,
            root / "exports" / session,
            snapshots=PUBLIC_INPUTS,
            operations=("transform-1",),
            observations={
                completion.observation.sequence: tuple(completion.observation.artifacts)
            },
        )
        report = {
            "project_id": ledger.project.project_id,
            "session_id": session,
            "reused": reused,
            "execution_count": (root / "candidate" / "executions.log")
            .read_bytes()
            .count(b"executed\n"),
            "accounting": {
                "limit": usage.limit,
                "spent": usage.spent,
                "reserved": usage.reserved,
                "available": usage.available,
            },
            "checks": checks,
            "export": str(index),
        }
        (root / "reports" / f"{session}.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--fixture", type=Path, default=Path(__file__).parent / "fixture"
    )
    args = parser.parse_args()
    report = walkthrough(args.command, args.root, args.fixture)
    print(json.dumps(report, indent=2))
    passed = (
        all(report["checks"].values())
        and report["execution_count"] == 1
        and report["accounting"]
        == {"limit": 5, "spent": 2, "reserved": 0, "available": 3}
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
