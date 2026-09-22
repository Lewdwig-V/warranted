"""Valid narrow proofs cannot erase independent task failures."""

import json
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m5/proof_cases.py"


def module():
    return runpy.run_path(str(SCRIPT))


def test_wrong_offset_model_matches_candidate_but_not_source_requirement():
    demo = module()
    source = (SCRIPT.parent.parent / "m2/fixture/input.csv").read_bytes()
    cases = json.loads(
        (SCRIPT.parent.parent / "m2/fixture/candidates.json").read_bytes()
    )
    assert demo["timestamp_correspondence"](source, cases["wrong-offset"], 0)[
        "correspondence"
    ]
    assert demo["timestamp_correspondence"](source, cases["correct"], 60)[
        "correspondence"
    ]
    assert not demo["timestamp_correspondence"](source, cases["wrong-offset"], 60)[
        "correspondence"
    ]
    assert not demo["timestamp_correspondence"](source, cases["correct"], 0)[
        "correspondence"
    ]
    references = json.loads(
        (SCRIPT.parent.parent / "m2/fixture/references.json").read_bytes()
    )
    checks = demo["M2"]["evaluate"](
        demo["M2"]["read_rows"](source),
        cases["wrong-offset"],
        references["offset-v1"],
        "exact",
    )
    assert checks["utc_timestamps"] is False and checks["daily_totals"] is False


def test_migration_model_distinguishes_absent_and_empty_labels():
    model = module()["migration_model"]
    value = {"version": 1, "host": "x", "timeout": 0, "label": ""}
    assert model(value, True) == {
        "version": 2,
        "endpoint": "x",
        "timeout_seconds": 0,
        "label": "",
    }
    assert "label" not in model(value, False)
    current = model(value, True)
    assert model(current, False) == current


@pytest.mark.parametrize(
    "change",
    [{"version": 3}, {"version": True}, {"timeout": False}, {"label": 7}, {"extra": 1}],
)
def test_migration_proof_model_excludes_inputs_outside_its_domain(change):
    value = {"version": 1, "host": "x", "timeout": 0, "label": "", **change}
    with pytest.raises(ValueError, match="domain"):
        module()["migration_model"](value, False)


def test_correspondence_requires_the_full_output_and_known_success():
    demo = module()
    cases = {
        "label": {
            "kind": "migrate",
            "input": '{"version":1,"host":"x","timeout":0,"label":""}',
        }
    }
    raw = {
        "label/stdout": b'{"version":2,"endpoint":"x","timeout_seconds":0}',
        "label/execution.json": (
            b'{"returncode":0,"timed_out":false,"output_limited":false}'
        ),
    }
    assert demo["migration_correspondence"](cases, raw, False)["correspondence"]
    assert not demo["migration_correspondence"](cases, raw, True)["correspondence"]
    assert not demo["migration_correspondence"](cases, {}, False)["correspondence"]
    raw["label/execution.json"] = (
        b'{"returncode":1,"timed_out":false,"output_limited":false}'
    )
    assert not demo["migration_correspondence"](cases, raw, False)["correspondence"]


@pytest.mark.proof
@pytest.mark.skipif(
    os.environ.get("WARRANTED_PROOF_TESTS") != "1",
    reason="requires pinned native verifier",
)
def test_native_three_proof_cases_survive_kill_and_two_resumes(tmp_path):
    root = Path(
        os.environ.get("WARRANTED_M5_PROOF_REPORTS", tmp_path / "proof-cases")
    ).resolve()
    bundle = os.environ["WARRANTED_PROOF_BUNDLE"]

    def invoke(stage, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), stage, str(root), "--bundle", bundle, *args],
            capture_output=True,
            text=True,
            timeout=90,
        )

    started = invoke("start", "--crash")
    assert started.returncode == -signal.SIGKILL, started.stderr
    first = json.loads((root / "reports/start.json").read_text())
    assert first["proof_executions"] == 3
    for index in range(2):
        completed = invoke("resume")
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)
        assert report["spent"] == {
            "proof": 3,
            "synthetic-work": 12,
            "batch": 2,
            "check": 2,
        }
        assert not any(report["reserved"].values())
        assert report["proof_executions"] == 3
        assert report["checker_executions"] == 16
        matrix = report["matrix"]
        for name in ("dropped-row", "empty"):
            assert matrix["uniqueness"][name]["checks"]["unique_ids"] is True
            assert matrix["uniqueness"][name]["checks"]["preserved_rows"] is False
        assert matrix["timestamp"]["wrong-offset"]["checks"]["utc_timestamps"] is False
        assert matrix["timestamp"]["wrong-offset"]["checks"]["daily_totals"] is False
        assert matrix["migration"]["drop-label"]["checks"]["renaming"] is True
        assert matrix["migration"]["drop-label"]["checks"]["legacy_label"] is False
        for family, candidates in report["matrix"].items():
            for name, candidate in candidates.items():
                assert candidate["support"]["status"] == "supported", (
                    family,
                    name,
                    candidate,
                )
                assert candidate["decision"] == (
                    "accepted" if name in ("correct", "complete") else "rejected"
                )
        if index:
            assert report["new_operations"] == 0
