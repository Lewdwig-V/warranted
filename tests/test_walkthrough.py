"""Execute the documented CSV fixture through separate host processes."""

import json
import multiprocessing
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from warranted.ledger import Ledger

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "m1"
WALKTHROUGH = EXAMPLE / "walkthrough.py"


def invoke(root, command, fixture=None):
    args = [sys.executable, str(WALKTHROUGH), command, str(root)]
    if fixture is not None:
        args += ["--fixture", str(fixture)]
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def test_csv_walkthrough_preserves_results_across_fresh_processes(tmp_path):
    root = tmp_path / "run"
    first = invoke(root, "start")
    assert first.returncode == 0, first.stderr
    initial = json.loads(first.stdout)
    second = invoke(root, "resume")
    assert second.returncode == 0, second.stderr
    resumed = json.loads(second.stdout)
    assert initial["session_id"] != resumed["session_id"]
    for report in (initial, resumed):
        assert report["execution_count"] == 1
        assert report["accounting"] == {
            "limit": 5,
            "spent": 2,
            "reserved": 0,
            "available": 3,
        }
        assert report["checks"] == {
            "process_exited_zero": True,
            "normalized_rows": True,
            "daily_totals": True,
        }
        assert Path(report["export"]).is_file()
    assert initial["reused"] is False
    assert resumed["reused"] is True
    assert (root / "candidate" / "executions.log").read_bytes() == b"executed\n"
    with Ledger.open(root / "ledger") as ledger:
        assert len(ledger.history()) == 1
        assert len(ledger.sessions()) == 2
        observation = ledger.history()[0]
        assert observation.session_id == initial["session_id"]
        assert ledger.read_artifact(observation.artifacts["normalized.csv"]) == (
            b"id,timestamp,value\nr1,2025-12-31T23:30:00Z,7\n"
            b"r2,2026-01-01T01:00:00Z,11\nr3,2026-01-01T23:15:00Z,5\n"
        )
        assert json.loads(
            ledger.read_artifact(observation.artifacts["totals.json"])
        ) == {
            "2025-12-31": 7,
            "2026-01-01": 16,
        }
        assert ledger.operations()[0].completion.result.elapsed_ns > 0
    # The second export remains usable without access to the authoritative files.
    export = Path(resumed["export"])
    index = json.loads(export.read_text())
    assert set(index["snapshots"]) == {"input.csv", "contract.md", "transform.py"}
    for snapshot in index["snapshots"].values():
        assert (export.parent / "artifacts" / snapshot["artifact"]["digest"]).is_file()


@pytest.mark.parametrize("failure", ["nonzero", "wrong-output"])
def test_failed_fixture_does_not_pass_or_execute_again(tmp_path, failure):
    fixture = tmp_path / "fixture"
    shutil.copytree(EXAMPLE / "fixture", fixture)
    script = fixture / "transform.py"
    if failure == "nonzero":
        script.write_text(
            script.read_text() + '\nraise RuntimeError("fixture failed")\n'
        )
    else:
        script.write_text(
            script.read_text() + '\nPath("totals.json").write_text("{}")\n'
        )
    root = tmp_path / "run"
    for command in ("start", "resume"):
        process = invoke(root, command, fixture)
        assert process.returncode == 1, process.stderr
        report = json.loads(process.stdout)
        assert not all(report["checks"].values())
        assert report["execution_count"] == 1
        assert report["accounting"]["spent"] == 2
    with Ledger.open(root / "ledger") as ledger:
        assert len(ledger.history()) == 1
        operation = ledger.operations()[0]
        assert operation.completion.result.outcome == (
            "failed" if failure == "nonzero" else "succeeded"
        )
        if failure == "nonzero":
            assert b"fixture failed" in ledger.read_artifact(
                ledger.history()[0].artifacts["stderr"]
            )


@pytest.mark.parametrize(
    "changed", ["contract.md", "input.csv", "transform.py", "expected-totals.json"]
)
def test_changed_fixture_is_rejected_before_cached_success(tmp_path, changed):
    fixture = tmp_path / "fixture"
    shutil.copytree(EXAMPLE / "fixture", fixture)
    root = tmp_path / "run"
    first = invoke(root, "start", fixture)
    assert first.returncode == 0, first.stderr
    path = fixture / changed
    path.write_bytes(path.read_bytes() + b"\n")
    resumed = invoke(root, "resume", fixture)
    assert resumed.returncode != 0
    assert "context" in resumed.stderr
    assert (root / "candidate" / "executions.log").read_bytes() == b"executed\n"
    with Ledger.open(root / "ledger") as ledger:
        assert len(ledger.history()) == 1
        assert ledger.accounting()["synthetic-work"].spent == 2


def _interrupted_walkthrough(root, pipe):
    def pause(*args, **kwargs):
        pipe.send("executed")
        pipe.recv()

    Ledger.complete = pause
    module = runpy.run_path(str(WALKTHROUGH))
    module["walkthrough"]("start", root, EXAMPLE / "fixture")


def test_killed_walkthrough_keeps_unknown_work_reserved(tmp_path):
    root = tmp_path / "run"
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_interrupted_walkthrough, args=(root, child))
    process.start()
    try:
        assert parent.poll(15), f"host did not execute fixture: {process.exitcode}"
        assert parent.recv() == "executed"
        process.kill()
        process.join(15)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()
    resumed = invoke(root, "resume")
    assert resumed.returncode != 0
    assert "unknown" in resumed.stderr
    assert (root / "candidate" / "executions.log").read_bytes() == b"executed\n"
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.history() == ()
        assert ledger.operations()[0].state == "unknown"
        balance = ledger.accounting()["synthetic-work"]
        assert (balance.spent, balance.reserved, balance.available) == (0, 3, 2)


def test_resume_reads_authoritative_bytes_and_rejects_an_extra_execution(tmp_path):
    root = tmp_path / "run"
    first = invoke(root, "start")
    assert first.returncode == 0, first.stderr
    report = json.loads(first.stdout)
    index = Path(report["export"])
    data = json.loads(index.read_text())
    ref = data["observations"][0]["artifacts"]["totals.json"]
    (index.parent / "artifacts" / ref["digest"]).write_text("{}")
    (root / "candidate" / "totals.json").write_text("{}")
    resumed = invoke(root, "resume")
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["checks"]["daily_totals"] is True
    # The independent execution counter is part of the walkthrough's success test.
    with (root / "candidate" / "executions.log").open("ab") as counter:
        counter.write(b"executed\n")
    resumed = invoke(root, "resume")
    assert resumed.returncode == 1
    assert json.loads(resumed.stdout)["execution_count"] == 2
