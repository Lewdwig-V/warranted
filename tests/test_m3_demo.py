"""Changed-premise task through the real frameworks and rootless worker."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from warranted.ledger import Ledger

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m3/demo.py"
pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
        reason="requires WARRANTED_CONTAINER_TESTS=1 and rootless Podman",
    ),
]


def invoke(root, stage, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), stage, str(root), *args],
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_changed_premise_survives_forced_restart_and_repeated_resume(tmp_path):
    root = tmp_path / "demo"
    first = invoke(root, "start", "--crash")
    assert first.returncode == -signal.SIGKILL, first.stderr
    initial = json.loads((root / "reports/start.json").read_text())
    assert initial["decision"] == "accepted"
    assert initial["spent"] == {"model": 1, "tool": 1, "synthetic-work": 2}
    for _ in range(2):
        result = invoke(root, "resume")
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["old_receipt"] == "stale"
        assert report["old_candidate"] == "rejected"
        assert report["old_checks"] == {
            "unique_ids": True,
            "preserved_rows": True,
            "utc_timestamps": False,
            "daily_totals": False,
        }
        assert report["decision"] == "accepted"
        assert all(report["checks"].values())
        assert report["spent"] == {"model": 2, "tool": 2, "synthetic-work": 4}
        assert all(value == 0 for value in report["reserved"].values())
        assert report["model_requests"] == report["tool_dispatches"] == 2
        assert report["checker_executions"] == 4
        assert all(
            item["applicability"] == "stale"
            for item in report["support_before"].values()
        )
        assert all(
            item["applicability"] == "current"
            for item in report["support_after"].values()
        )
        assert Path(report["export"]).is_file()
    with Ledger.open(root / "ledger") as ledger:
        results = [
            json.loads(ledger.read_artifact(o.artifacts["result.json"]))
            for o in ledger.history()
            if o.origin.kind == "evaluate"
        ]
        assert len(results) == 3
        assert any(not result["utc_timestamps"] for result in results)
        first_episode = next(
            o for o in ledger.history() if o.origin.operation_id == "episode/initial"
        )
        assert set(first_episode.origin.inputs) == {
            "input.csv",
            "offset-v1",
            "worker-task.md",
        }
        assert "reference/offset-v2" not in first_episode.origin.inputs


def test_changed_fixture_is_rejected_before_any_new_dispatch(tmp_path):
    root = tmp_path / "demo"
    first = invoke(root, "start")
    assert first.returncode == 0, first.stderr
    with Ledger.open(root / "ledger") as ledger:
        ref = ledger.project.snapshots["worker-task.md"].artifact
        (ledger.root / "artifacts/sha256" / ref.digest).write_bytes(
            b"weakened contract"
        )
    resumed = invoke(root, "resume")
    assert resumed.returncode != 0
    assert "artifact" in resumed.stderr
    assert len((root / "service/requests.jsonl").read_text().splitlines()) == 1
