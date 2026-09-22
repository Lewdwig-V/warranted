"""The migration revision survives restart without repeating completed work."""

import json
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from warranted.ledger import Ledger, Outcome, Result
from warranted.worker import AttemptResult, UnknownOutcome

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m5/recovery.py"


def scripted(tmp_path, monkeypatch, initial_payload=None):
    host = runpy.run_path(str(SCRIPT))
    scope = host["demonstrate"].__globals__
    root = tmp_path / "recovery"
    host["initialize"](root)

    class Workspace:
        def __init__(self, ledger_root, episode):
            self.episode = episode

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __call__(self, request, payload):
            name = "one-way" if self.episode.episode_id == "initial" else "complete"
            with Ledger.open(root / "ledger") as ledger:
                candidate = ledger.read_artifact(
                    ledger.project.snapshots[f"candidate-{name}.json"].artifact
                )
            if name == "one-way" and initial_payload is not None:
                candidate = initial_payload
            return AttemptResult(
                Result(Outcome.SUCCEEDED, 0, {"tool": 1}, 1),
                {
                    "stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n",
                    "stderr": b"",
                    "candidate/result.json": candidate,
                },
            )

    def execute(root, episode, inputs, source):
        cases = json.loads((SCRIPT.parent / "fixture/references.json").read_text())
        raw = {}
        for name in inputs:
            case = cases[name]
            code = int(
                case["kind"] == "invalid"
                or (case["kind"] == "repeat" and b"ACCEPT_CURRENT = False" in source)
            )
            raw[name + "/stdout"] = (
                b"" if code else json.dumps(case["expected"]).encode()
            )
            raw[name + "/stderr"] = b""
            raw[name + "/execution.json"] = json.dumps(
                {
                    "returncode": code,
                    "timed_out": False,
                    "output_limited": False,
                    "elapsed_ns": 1,
                }
            ).encode()
        return AttemptResult(Result(Outcome.SUCCEEDED, 0, {"batch": 1}, 1), raw)

    monkeypatch.setitem(scope, "Sandbox", Workspace)
    monkeypatch.setitem(scope["M5"], "execute", execute)
    return host, root


def assert_recovered(report):
    assert report["old_receipt"] == "stale"
    assert report["old_candidate"]["status"] == "rejected"
    assert report["old_candidate"]["checks"]["repetition"] is False
    assert all(
        value
        for key, value in report["old_candidate"]["checks"].items()
        if key != "repetition"
    )
    assert report["candidate"]["status"] == "accepted"
    assert all(report["candidate"]["checks"].values())
    assert report["spent"] == {"model": 2, "tool": 2, "batch": 2, "check": 3}
    assert not any(report["reserved"].values())
    assert report["dispatches"] == {"model": 2, "tool": 2, "batch": 2, "check": 3}


def test_revision_blocks_stale_receipt_before_correction_and_reuses_work(
    tmp_path, monkeypatch
):
    host, root = scripted(tmp_path, monkeypatch)
    first = host["demonstrate"]("start", root)
    assert first["candidate"]["status"] == "accepted"
    resumed = host["demonstrate"]("resume", root)
    assert_recovered(resumed)
    with Ledger.open(root / "ledger") as ledger:
        episodes = [o for o in ledger.history() if o.origin.kind == "episode"]
        initial = json.loads(
            ledger.read_artifact(episodes[0].artifacts["episode.json"])
        )
        assert "revision.txt" not in initial["episode"]["inputs"]
        assert "repetition" not in initial["episode"]["objective"]
        assert "references.json" not in episodes[0].origin.inputs
        revision = next(o for o in ledger.history() if o.origin.kind == "revision")
        decisions = [o for o in ledger.history() if o.origin.kind == "decision"]
        stale = next(
            o
            for o in decisions
            if json.loads(ledger.read_artifact(o.artifacts["decision.json"]))["status"]
            == "stale"
        )
        assert revision.sequence < stale.sequence < episodes[1].sequence

    def forbidden(*args):
        pytest.fail("completed work was dispatched again")

    monkeypatch.setitem(host["demonstrate"].__globals__, "model", forbidden)
    monkeypatch.setitem(host["demonstrate"].__globals__, "Sandbox", forbidden)
    monkeypatch.setitem(host["demonstrate"].__globals__["M5"], "execute", forbidden)
    monkeypatch.setitem(host["demonstrate"].__globals__["M5"], "evaluate", forbidden)
    again = host["demonstrate"]("resume", root)
    assert_recovered(again)
    assert again["new_operations"] == 0


def test_rejected_submission_still_reaches_approved_revision(tmp_path, monkeypatch):
    host, root = scripted(tmp_path, monkeypatch, b'{"legacy_consumer.py":"pass"}')
    first = host["demonstrate"]("start", root)
    assert first["candidate"]["status"] == "rejected"
    assert first["candidate"]["checks"]["repository_integrity"] is False
    resumed = host["demonstrate"]("resume", root)
    assert resumed["old_receipt"] == "stale"
    assert resumed["old_candidate"]["status"] == "rejected"
    assert resumed["candidate"]["status"] == "accepted"


def test_missing_revision_and_changed_approval_block_before_dispatch(
    tmp_path, monkeypatch
):
    host, root = scripted(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="committed revision"):
        host["demonstrate"]("resume", root)
    assert not (root / "worker-dispatches.jsonl").exists()
    host["demonstrate"]("start", root)
    before = (root / "worker-dispatches.jsonl").read_bytes()
    with Ledger.open(root / "ledger") as ledger:
        artifact = ledger.project.snapshots["contracts.json"].artifact
        (ledger.root / "artifacts/sha256" / artifact.digest).write_bytes(b"unapproved")
    with pytest.raises(ValueError, match="artifact"):
        host["demonstrate"]("resume", root)
    assert (root / "worker-dispatches.jsonl").read_bytes() == before


def test_unknown_batch_keeps_reservation_and_blocks_all_new_work(tmp_path, monkeypatch):
    host, root = scripted(tmp_path, monkeypatch)

    def lost(*args):
        raise RuntimeError("lost batch response")

    monkeypatch.setitem(host["demonstrate"].__globals__["M5"], "execute", lost)
    with pytest.raises(RuntimeError, match="lost batch"):
        host["demonstrate"]("start", root)
    before = (root / "executions.jsonl").read_bytes()
    for _ in range(2):
        with pytest.raises(UnknownOutcome):
            host["demonstrate"]("start", root)
        assert (root / "executions.jsonl").read_bytes() == before
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.accounting()["batch"].reserved == 1
        assert ledger.accounting()["batch"].spent == 0
        assert ledger.accounting()["model"].spent == 1
        assert not any(o.origin.kind == "revision" for o in ledger.history())


def test_lost_model_result_stays_unknown_after_reopening(tmp_path, monkeypatch):
    host, root = scripted(tmp_path, monkeypatch)
    original = host["model"]

    def lost(request, payload):
        original(root, request, payload)
        raise RuntimeError("model result lost before recording")

    monkeypatch.setitem(
        host["demonstrate"].__globals__, "model", lambda root, *args: lost(*args)
    )
    with pytest.raises(RuntimeError):
        host["demonstrate"]("start", root)
    before = (root / "worker-dispatches.jsonl").read_bytes()
    for _ in range(2):
        with pytest.raises(UnknownOutcome):
            host["demonstrate"]("start", root)
        assert (root / "worker-dispatches.jsonl").read_bytes() == before
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.accounting()["model"].reserved == 1
        assert ledger.accounting()["model"].spent == 0
        assert ledger.accounting()["tool"].spent == 0


@pytest.mark.container
@pytest.mark.skipif(
    os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
    reason="requires native rootless Podman",
)
def test_native_revision_survives_host_kill_and_two_fresh_resumes(tmp_path):
    root = Path(
        os.environ.get("WARRANTED_M5_RECOVERY", tmp_path / "recovery")
    ).resolve()

    def invoke(stage, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), stage, str(root), *extra],
            capture_output=True,
            text=True,
            timeout=90,
        )

    first = invoke("start", "--crash")
    assert first.returncode == -signal.SIGKILL, first.stderr
    initial = json.loads((root / "reports/start.json").read_text())
    assert initial["candidate"]["status"] == "accepted"
    for index in range(2):
        result = invoke("resume")
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert_recovered(report)
        if index:
            assert report["new_operations"] == 0
    assert (root / "graph.sqlite3").is_file()
