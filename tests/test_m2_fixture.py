"""Observable outcomes for the fixed M2 experiments, including fresh processes."""

import json
import multiprocessing
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from warranted.ledger import Ledger, Origin

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "m2"
SCRIPT = EXAMPLE / "experiments.py"


def invoke(root, command, fixture=None):
    args = [sys.executable, str(SCRIPT), command, str(root)]
    if fixture is not None:
        args += ["--fixture", str(fixture)]
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def test_independent_obligations_reject_the_fixed_counterexamples():
    module = runpy.run_path(str(SCRIPT))
    fixture = EXAMPLE / "fixture"
    source = module["read_rows"]((fixture / "input.csv").read_bytes())
    reference = json.loads((fixture / "references.json").read_bytes())["offset-v1"]
    candidates = json.loads((fixture / "candidates.json").read_bytes())
    expected = {
        "correct": (True, True, True, True),
        "wrong-offset": (True, True, False, False),
        "dropped-row": (True, False, True, False),
        "dropped-row-wrong-offset": (True, False, False, False),
        "empty": (True, False, True, False),
        "swapped-timestamps": (True, True, False, True),
    }
    for name, outcomes in expected.items():
        checks = module["evaluate"](source, candidates[name], reference, "exact")
        assert tuple(checks) == (
            "unique_ids",
            "preserved_rows",
            "utc_timestamps",
            "daily_totals",
        )
        assert tuple(checks.values()) == outcomes, name
    # A favourable total must not hide lost rows or repeated JSON keys.
    forged = {"rows": [], "totals": reference["totals"]}
    assert not module["evaluate"](source, forged, reference, "exact")["daily_totals"]
    with pytest.raises(ValueError, match="duplicate"):
        module["decode"](b'{"rows": [], "rows": [], "totals": {}}')
    for malformed in ({}, {"rows": [["r1", "bad", True]], "totals": {}}):
        with pytest.raises(ValueError):
            module["evaluate"](source, malformed, reference, "exact")


def test_all_experiments_survive_restart_with_selective_work(tmp_path):
    root = tmp_path / "run"
    first = invoke(root, "start")
    assert first.returncode == 0, first.stderr
    initial = json.loads(first.stdout)
    second = invoke(root, "resume")
    assert second.returncode == 0, second.stderr
    resumed = json.loads(second.stdout)
    expected_new = {
        "matrix": 1,
        "unchanged": 0,
        "annotation": 0,
        "offset": 4,
        "definition": 2,
    }
    expected_total = {
        "matrix": 8,
        "unchanged": 5,
        "annotation": 5,
        "offset": 9,
        "definition": 8,
    }
    for name, report in resumed.items():
        assert report["session_id"] != initial[name]["session_id"]
        assert report["new_operations"] == expected_new[name]
        assert report["spent"] == report["execution_count"] == expected_total[name]
        assert report["reserved"] == 0
    for name in ("unchanged", "annotation"):
        assert resumed[name]["before_reassessment"] == "accepted"
        assert resumed[name]["reused_pipeline"] == [
            "source-facts",
            "normalize",
            "aggregate",
        ]
    assert resumed["offset"]["before_reassessment"] == "stale"
    assert resumed["offset"]["reused_pipeline"] == ["source-facts"]
    assert resumed["offset"]["candidate"]["totals"] == {
        "2026-01-01": 18,
        "2026-01-02": 5,
    }
    assert resumed["offset"]["old_candidate_decision"] == "rejected"
    for name in ("unchanged", "annotation", "offset", "definition"):
        assert resumed[name]["decision"] == "accepted"
    definition = resumed["definition"]
    assert definition["before_reassessment"] == "stale"
    assert definition["candidate_digest"] == initial["definition"]["candidate_digest"]
    assert definition["witness_before"] is True
    assert definition["witness_after"] is False
    with Ledger.open(root / "definition" / "ledger") as ledger:
        revised = next(
            op
            for op in ledger.operations()
            if op.request.origin.kind == "evaluate"
            and "definition-v2" in op.request.origin.inputs
        )
        decisions = [
            item for item in ledger.history() if item.origin.kind == "decision"
        ]
        # Identical success bytes must still cite the newly checked interpretation.
        key = f"observation/{revised.completion.observation.sequence}/result.json"
        assert key in decisions[-1].origin.inputs
    matrix = resumed["matrix"]
    assert matrix["narrow_empty_pass"] is True
    assert matrix["decisions"] == {
        "correct": "accepted",
        "wrong-offset": "rejected",
        "dropped-row": "rejected",
        "dropped-row-wrong-offset": "rejected",
        "empty": "rejected",
        "swapped-timestamps": "rejected",
    }
    # Another fresh process reuses the work without erasing historical failures.
    third = invoke(root, "resume")
    assert third.returncode == 0, third.stderr
    assert all(
        report["new_operations"] == 0 for report in json.loads(third.stdout).values()
    )
    with Ledger.open(root / "matrix" / "ledger") as ledger:
        decisions = [
            json.loads(ledger.read_artifact(item.artifacts["decision.json"]))
            for item in ledger.history()
            if item.origin.kind == "decision"
        ]
        assert sum(item["status"] == "rejected" for item in decisions) == 5
        assert all(
            item["requirements"]["explicit_source_offsets"] == "excepted"
            for item in decisions
        )


@pytest.mark.parametrize(
    "changed",
    ["contract.md", "versions.json", "references.json", "input.csv", "acceptance.json"],
)
def test_changed_fixture_fails_before_reuse_or_charges(tmp_path, changed):
    fixture = tmp_path / "fixture"
    shutil.copytree(EXAMPLE / "fixture", fixture)
    root = tmp_path / "run"
    first = invoke(root, "start", fixture)
    assert first.returncode == 0, first.stderr
    path = fixture / changed
    path.write_bytes(path.read_bytes() + b"\n")
    second = invoke(root, "resume", fixture)
    assert second.returncode != 0
    assert "context" in second.stderr
    with Ledger.open(root / "matrix" / "ledger") as ledger:
        assert len(ledger.sessions()) == 1
        assert ledger.accounting()["synthetic-work"].spent == 7


def test_missing_forged_wrong_target_and_unfinished_receipts_block_acceptance(tmp_path):
    root = tmp_path / "run"
    first = invoke(root, "start")
    assert first.returncode == 0, first.stderr
    module = runpy.run_path(str(SCRIPT))
    with Ledger.open(root / "matrix" / "ledger") as ledger:
        host = module["Experiment"](ledger, ledger.start_session(), root / "matrix")
        evidence = module["Evidence"]
        current, event = host.interpretation()
        revision = host.read(evidence.captured(event, "revision.json"))
        empty = evidence.restored(revision["candidates"]["empty"])
        assert host.decide(empty, None) == "missing"
        assert host.decide(empty, "worker-says-accepted") == "unsupported"
        assert host.decide(empty, revision["receipts"]["correct"]) == "stale"
        req = host.request("evaluate", host.check_inputs(empty, current))
        # Raw text with the checker's operation ID cannot replace its completion.
        ledger.record(host.session, req.origin, {"result.json": b'{"accepted": true}'})
        assert host.decide(empty, req.origin.operation_id) == "rejected"
        narrow = host.perform("narrow-unique", {"candidate": empty}, lambda: True)
        assert host.decide(empty, narrow.request.origin.operation_id) == "stale"
        changed = ledger.record(
            host.session,
            Origin("changed", "candidate", "test", "1", {}),
            {"candidate": b'{"rows": [], "totals": {"2026-01-01": 16}}'},
        )
        changed = evidence.captured(changed, "candidate")
        req = host.request("evaluate", host.check_inputs(changed, current))
        assert host.decide(changed, req.origin.operation_id) == "missing"
        ledger.reserve(host.session, req, {"synthetic-work": 1})
        assert host.decide(changed, req.origin.operation_id) == "unknown"
        assert ledger.accounting()["synthetic-work"].reserved == 1
    with pytest.raises(ValueError, match="unsupported"):
        module["unique_ids"](["rA", "ra"], "worker-defined-equality")


def _pause_after_revision(root, pipe):
    original = Ledger.record

    def record(ledger, session, origin, raw, **kwargs):
        item = original(ledger, session, origin, raw, **kwargs)
        if (
            origin.kind == "revision"
            and ledger.project.manifest.environment["scenario"] == "definition"
        ):
            pipe.send("committed")
            pipe.recv()
        return item

    Ledger.record = record
    runpy.run_path(str(SCRIPT))["experiments"]("start", root, EXAMPLE / "fixture")


def test_killed_host_recovers_the_committed_revision_without_repeating_work(tmp_path):
    root = tmp_path / "run"
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_pause_after_revision, args=(root, child))
    process.start()
    try:
        assert parent.poll(15), f"revision not committed: {process.exitcode}"
        assert parent.recv() == "committed"
        process.kill()
        process.join(15)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()
    second = invoke(root, "resume")
    assert second.returncode == 0, second.stderr
    reports = json.loads(second.stdout)
    assert reports["definition"]["before_reassessment"] == "stale"
    assert reports["definition"]["new_operations"] == 2
    assert reports["offset"]["new_operations"] == 4
    assert reports["unchanged"]["new_operations"] == 0


def _pause_before_completion(root, pipe):
    def pause(*args, **kwargs):
        pipe.send("executed")
        pipe.recv()

    Ledger.complete = pause
    runpy.run_path(str(SCRIPT))["experiments"]("start", root, EXAMPLE / "fixture")


def test_killed_checker_keeps_reservation_and_does_not_retry(tmp_path):
    root = tmp_path / "run"
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_pause_before_completion, args=(root, child))
    process.start()
    try:
        assert parent.poll(15), f"checker did not execute: {process.exitcode}"
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
    second = invoke(root, "resume")
    assert second.returncode != 0
    assert "unknown" in second.stderr
    with Ledger.open(root / "matrix" / "ledger") as ledger:
        assert ledger.operations()[0].state == "unknown"
        balance = ledger.accounting()["synthetic-work"]
        assert (balance.spent, balance.reserved) == (0, 1)
    assert len((root / "matrix" / "executions.jsonl").read_text().splitlines()) == 1
