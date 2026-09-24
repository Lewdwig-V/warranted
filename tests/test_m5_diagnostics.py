"""Submission controls preserve the difference between captured and submitted."""

import json
import os
import runpy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from minisweagent.exceptions import FormatError

from warranted.acceptance import Evidence
from warranted.contexts import Condition
from warranted.ledger import Ledger, Outcome, Result
from warranted.worker import AttemptResult, Episode, Journal, submitted_candidate

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m5/diagnostics.py"


def test_helper_packages_source_without_executing_it(tmp_path):
    demo = runpy.run_path(str(SCRIPT))
    (tmp_path / "submit.py").write_bytes(demo["SUBMIT"])
    (tmp_path / "workspace").mkdir()
    source = "raise RuntimeError('do not execute this source')\n"
    (tmp_path / "workspace/migrate.py").write_text(source)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "submit.py")],
        cwd=tmp_path / "workspace",
        capture_output=True,
        check=True,
    )
    assert result.stdout == b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"
    assert json.loads((tmp_path / "result.json").read_bytes()) == {"migrate.py": source}


@pytest.mark.parametrize("model_class", ["ProgressModel", "CountdownModel"])
def test_remaining_turns_count_format_errors_and_reuse_recorded_calls(
    tmp_path, model_class
):
    from test_worker import project

    demo = runpy.run_path(str(SCRIPT))
    project(tmp_path)
    episode = Episode("first", "submit", ("task",), max_steps=2)
    calls = []

    def model(request, payload):
        calls.append(json.loads(payload))
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"model": 1}, 1),
            {"response": b"invalid" if len(calls) == 1 else b'{"command":"true"}'},
        )

    for _ in range(2):
        with Ledger.open(tmp_path / "ledger") as ledger:
            client = demo[model_class](Journal(ledger, episode), model)
            with pytest.raises(FormatError):
                client.query([])
            client.query([])
    assert len(calls) == 2
    for remaining, payload in zip((2, 1), calls, strict=True):
        assert f"including this one: {remaining}." in payload["messages"][-1]["content"]
        assert ("submit.py" in payload["messages"][-1]["content"]) == (
            model_class == "ProgressModel"
        )
        if model_class == "CountdownModel":
            assert payload["messages"][-1]["content"] == (
                f"Model turns remaining, including this one: {remaining}."
            )


def test_ablation_changes_only_declared_instructions_and_helper(tmp_path):
    from test_worker import project

    demo = runpy.run_path(str(SCRIPT))
    project(tmp_path)
    with Ledger.open(tmp_path / "ledger") as ledger:
        helper = Evidence("task", ledger.project.snapshots["task"].artifact)
    episode = Episode(
        "initial",
        "Original objective. The final command must start with `printf '%s\\n' "
        "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT;` so the marker is the first "
        "stdout line. On continuation, use the restored workspace.",
        ("task",),
        max_steps=12,
    )
    for mode in ("original", "schema", "helper"):
        control = demo["ablation"](episode, mode, helper)
        counted = demo["ablation"](episode, mode + "-budget", helper)
        assert counted == control  # Countdown changes model feedback only.
        assert (
            replace(control, objective=episode.objective, files=episode.files)
            == episode
        )
        if mode == "helper":
            assert "must start with `printf" not in control.objective
            assert "python /work/submit.py" in control.objective
            assert "On continuation, use the restored workspace." in control.objective
        else:
            assert control.objective.startswith(episode.objective)
        assert ("complete Python source" in control.objective) == (mode != "original")
        assert dict(control.files) == (
            {"submit.py": helper} if mode == "helper" else {}
        )
        if mode == "original":
            assert control == episode
    with pytest.raises(ValueError, match="unknown ablation"):
        demo["ablation"](episode, "typo-budget", helper)
    with pytest.raises(ValueError, match="marker instruction changed"):
        demo["ablation"](
            replace(episode, objective="Changed protocol."), "helper", helper
        )


def test_ablation_dispatch_keeps_countdown_and_helper_separate(tmp_path, monkeypatch):
    from test_m5_treatments import scripted

    from warranted import worker

    _, _, bundle = scripted(tmp_path, monkeypatch, "migration", Condition.A)
    demo = runpy.run_path(str(SCRIPT))
    requests = []

    def propose(root, episode, client):
        def model(request, payload):
            requests.append((episode, json.loads(payload)))
            return AttemptResult(
                Result(Outcome.SUCCEEDED, 0, {"model": 1}, 1),
                {"response": b'{"command":"true"}'},
            )

        with Ledger.open(root / "ledger") as ledger:
            worker.WorkerModel(Journal(ledger, episode), model).query(
                [{"role": "user", "content": episode.objective}]
            )
        raise RuntimeError("worker did not submit")

    monkeypatch.setitem(demo["T"], "propose", propose)
    for control in demo["ABLATIONS"]:
        root = tmp_path / control
        demo["initialize"](root, bundle, control, None, max_steps=12)
        report = demo["run"](root, bundle, None)
        episode, payload = requests[-1]
        assert report["submitted"] is False
        assert report["spent"]["model"] == 1
        messages = payload["messages"]
        counted = control.endswith("-budget")
        assert len(messages) == (2 if counted else 1)
        if counted:
            assert messages[-1]["content"] == (
                "Model turns remaining, including this one: 12."
            )
        assert ("submit.py" in episode.files) == control.startswith("helper")
        assert "submission.md" not in episode.files
        assert ("must start with `printf" in episode.objective) == (
            not control.startswith("helper")
        )


@pytest.mark.container
@pytest.mark.parametrize("fault", ["payload", "binary", "workspace"])
@pytest.mark.skipif(
    os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
    reason="requires rootless Podman",
)
def test_unsubmitted_source_is_captured_and_checked_without_becoming_submission(
    tmp_path, monkeypatch, fault
):
    from test_m5_treatments import scripted

    _, _, bundle = scripted(tmp_path, monkeypatch, "migration", Condition.A)
    demo = runpy.run_path(str(SCRIPT))
    original = demo["T"]["snapshots"]

    def snapshots(*args, **kwargs):
        values = original(*args, **kwargs)
        source = (
            b"\xff"
            if fault == "binary"
            else json.loads(values["candidate-one-way.json"].data)[
                "migrate.py"
            ].encode()
        )
        command = (
            "python - <<'PY'\nfrom pathlib import Path\n"
            f"Path('workspace/migrate.py').write_bytes({source!r})\n"
            + (
                "Path('result.json').symlink_to('/etc/passwd')\n"
                if fault == "payload"
                else ""
            )
            + (
                "__import__('os').symlink('/etc/passwd', b'workspace/link-\\xff')\n"
                "Path('result.json').write_text('{}')\n"
                if fault == "workspace"
                else ""
            )
            + "PY"
            + (
                "\nprintf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n'"
                if fault in {"payload", "workspace"}
                else ""
            )
        )
        values["model-initial.json"] = replace(
            values["model-initial.json"], data=json.dumps({"command": command}).encode()
        )
        return values

    monkeypatch.setitem(demo["T"], "snapshots", snapshots)
    root = tmp_path / "control"
    demo["initialize"](root, bundle, "plain", None, max_steps=1)
    result = demo["run"](root, bundle, None)
    assert result["submitted"] is False
    assert result["assessment"] is None
    assessments = result["unfinished_assessment"]
    if fault == "workspace":
        assert assessments["workspace"]["status"] == "unsupported"
        assert assessments["payload"]["status"] == "rejected"
    else:
        assert assessments["source"]["status"] == (
            "unsupported" if fault == "binary" else "accepted"
        )
        if fault == "payload":
            assert assessments["payload"]["status"] == "unsupported"
    with Ledger.open(root / "ledger") as ledger:
        with pytest.raises(ValueError):
            submitted_candidate(ledger, "initial")
    repeated = demo["run"](root, bundle, None)
    assert repeated["spent"] == result["spent"]
    assert repeated["unfinished_assessment"] == result["unfinished_assessment"]
