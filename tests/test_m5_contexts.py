"""A–E disclosures against both fixtures, using the real worker journal."""

import base64
import json
import os
import runpy
from pathlib import Path

import pytest

from warranted.acceptance import Evidence, _encode
from warranted.claims import Claims
from warranted.contexts import Condition, capture_context, capture_history
from warranted.ledger import Ledger, Manifest, Origin, Outcome, Result, Snapshot
from warranted.sandbox import SANDBOX_ID, Sandbox
from warranted.worker import (
    AttemptResult,
    Episode,
    UnknownOutcome,
    input_files,
    record_once,
    run_workflow,
    submitted_candidate,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(root, family, condition):
    script = EXAMPLES / ("m3/demo.py" if family == "csv" else "m5/recovery.py")
    captured = runpy.run_path(str(script))["snapshots"]()
    captured["private-canary"] = Snapshot(b"PRIVATE GRADING CANARY", "checker", "1")
    captured["future-canary"] = Snapshot(b"FUTURE REVISION CANARY", "owner", "2")
    with Ledger.create(
        root / "ledger",
        Manifest(
            "context-" + family,
            "1",
            "run",
            "world",
            {"condition": str(condition), "family": family},
            {"model": 2, "tool": 2},
        ),
        captured,
    ):
        pass


def prepared(root, family, stage):
    with Ledger.open(root / "ledger") as ledger:
        session = ledger.start_session()

        def ref(name):
            return Evidence(name, ledger.project.snapshots[name].artifact)

        revised = stage == "revised"
        if family == "csv":
            old, current = (
                ref("offset-v1"),
                ref("offset-v2" if revised else "offset-v1"),
            )
            workspace = {
                "input.csv": ref("input.csv"),
                current.name: current,
                "task.md": ref("worker-task.md"),
            }
            models = {"model.sh": ref("model-command.sh")}
        else:
            old = ref("seed/README.md")
            current = ref("revision.txt") if revised else old
            workspace = {
                "repository-files.json": ref("repository-files.json"),
                "task.md": old,
            }
            if revised:
                workspace["revision.txt"] = current
            models = {"regression.py": ref("seed/example_test.py")}
        if revised:
            workspace["previous-result.json"] = submitted_candidate(ledger, "initial")
        history = capture_history(
            ledger, session, stage, ("initial",) if revised else ()
        )
        # This claim is deliberately unproved. A current dependency is not proof.
        claims = Claims(ledger, session)
        claim = claims.record(
            "The recorded model uses this input version.",
            old,
            {"input": old},
            complete=True,
        )
        assessment = claims.assess(claim, {"input": current})
        event = record_once(
            ledger,
            session,
            Origin(
                "support/" + stage,
                "support",
                "fixture",
                "1",
                {claim.name: claim.artifact, current.name: current.artifact},
            ),
            {
                "dependencies.json": _encode(
                    {
                        "validation": assessment.validation,
                        "applicability": assessment.applicability,
                        "dependencies": dict(assessment.dependencies),
                    }
                ),
                "proof-work.json": _encode({"status": "unproved", "attempts": 0}),
            },
        )
        files = capture_context(
            ledger,
            session,
            stage,
            {
                Condition.A: workspace,
                Condition.B: {"history.jsonl": history},
                Condition.C: models,
                Condition.D: {
                    "dependencies.json": Evidence.captured(event, "dependencies.json")
                },
                Condition.E: {
                    "proof-work.json": Evidence.captured(event, "proof-work.json")
                },
            },
        )
        return Episode(
            stage,
            "Inspect the permitted files, then submit result.json.",
            (),
            environment=SANDBOX_ID,
            max_steps=1,
            files=files,
            continues="initial" if revised else None,
        )


def scripted(root, episode):
    def boundary(request, payload):
        kind = request.origin.kind
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {kind: 1}, 1),
            {"response": b'{"command":"inspect public files and submit"}'}
            if kind == "model"
            else {
                "stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\npublic result\n",
                "stderr": b"raw diagnostic: \xff",
                "candidate/result.json": b'{"proposal":true}',
            },
        )

    return run_workflow(
        root / "ledger",
        root / "graph.sqlite3",
        episode,
        model=boundary,
        environment=boundary,
    )


@pytest.mark.parametrize("family", ["csv", "migration"])
@pytest.mark.parametrize("condition", list(Condition))
def test_both_fixtures_hide_private_and_future_evidence_across_sessions(
    tmp_path,
    family,
    condition,
):
    fixture(tmp_path, family, condition)
    first = prepared(tmp_path, family, "initial")
    with Ledger.open(tmp_path / "ledger") as ledger:
        files = input_files(ledger, first)
        combined = b"\n".join(files.values())
        assert b"PRIVATE GRADING CANARY" not in combined
        assert b"FUTURE REVISION CANARY" not in combined
        assert "revision.txt" not in files
        assert "references.json" not in files and "contracts.json" not in files
        if family == "migration":
            assert b"safe repetition" not in combined and b"repetition" not in combined
        else:
            assert "offset-v2" not in files
        assert ("history.jsonl" in files) == (condition >= Condition.B)
        assert ("model.sh" in files or "regression.py" in files) == (
            condition >= Condition.C
        )
        assert ("dependencies.json" in files) == (condition >= Condition.D)
        assert ("proof-work.json" in files) == (condition == Condition.E)
    scripted(tmp_path, first)
    revised = prepared(tmp_path, family, "revised")
    with Ledger.open(tmp_path / "ledger") as ledger:
        files = input_files(ledger, revised)
        assert b"PRIVATE GRADING CANARY" not in b"\n".join(files.values())
        assert b"FUTURE REVISION CANARY" not in b"\n".join(files.values())
        assert json.loads(files["previous-result.json"]) == {"proposal": True}
        if family == "migration":
            assert "revision.txt" in files
        if condition >= Condition.B:
            rows = [json.loads(line) for line in files["history.jsonl"].splitlines()]
            assert [row["kind"] for row in rows] == ["model", "tool"]
            assert (
                base64.b64decode(rows[1]["response_base64"]["stderr"])
                == b"raw diagnostic: \xff"
            )
            assert "PRIVATE GRADING CANARY" not in json.dumps(rows)
        if condition >= Condition.D:
            assert json.loads(files["dependencies.json"])["applicability"] == "stale"
            assert json.loads(files["dependencies.json"])["validation"] == "unproved"
        if condition == Condition.E:
            assert json.loads(files["proof-work.json"]) == {
                "status": "unproved",
                "attempts": 0,
            }
    scripted(tmp_path, revised)
    assert prepared(tmp_path, family, "revised") == revised
    scripted(tmp_path, revised)
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert ledger.accounting()["model"].spent == 2
        assert ledger.accounting()["tool"].spent == 2


def test_history_is_not_synthesized_for_a_missing_episode(tmp_path):
    fixture(tmp_path, "csv", Condition.B)
    with Ledger.open(tmp_path / "ledger") as ledger:
        with pytest.raises(UnknownOutcome, match="completed"):
            capture_history(ledger, ledger.start_session(), "next", ("missing",))


def test_lost_attempt_cannot_be_exported_as_completed_history(tmp_path):
    fixture(tmp_path, "csv", Condition.B)
    episode = prepared(tmp_path, "csv", "initial")

    def lost(*args):
        raise RuntimeError("lost response")

    with pytest.raises(RuntimeError, match="lost response"):
        run_workflow(
            tmp_path / "ledger",
            tmp_path / "graph.sqlite3",
            episode,
            model=lost,
            environment=lost,
        )
    with Ledger.open(tmp_path / "ledger") as ledger:
        with pytest.raises(UnknownOutcome, match="completed"):
            capture_history(ledger, ledger.start_session(), "next", ("initial",))
        assert ledger.accounting()["model"].reserved == 1


@pytest.mark.container
@pytest.mark.skipif(
    os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
    reason="requires native rootless Podman",
)
@pytest.mark.parametrize(
    ("family", "condition"), [("csv", Condition.A), ("migration", Condition.E)]
)
def test_native_worker_receives_only_selected_context(tmp_path, family, condition):
    fixture(tmp_path, family, condition)
    episode = prepared(tmp_path, family, "initial")
    with Ledger.open(tmp_path / "ledger") as ledger:
        expected = input_files(ledger, episode)
    command = f"""python - <<'PY'
from pathlib import Path
import json
expected = { {name: data.decode() for name, data in expected.items()}!r}
assert set(p.name for p in Path('.').iterdir()) == set(expected) | {{
    'context.json', 'workspace'
}}
assert Path('workspace').is_dir() and not list(Path('workspace').iterdir())
for name, content in expected.items():
    assert Path(name).read_text() == content
assert not Path({str(tmp_path / "ledger")!r}).exists()
assert not any(
    'PRIVATE GRADING CANARY' in p.read_text()
    for p in Path('.').iterdir() if p.is_file()
)
Path('result.json').write_text('{{"proposal":true}}')
print('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
PY"""

    def model(*args):
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"model": 1}, 1),
            {"response": _encode({"command": command})},
        )

    with Sandbox(tmp_path / "ledger", episode) as sandbox:
        result = run_workflow(
            tmp_path / "ledger",
            tmp_path / "graph.sqlite3",
            episode,
            model=model,
            environment=sandbox,
        )
    assert result["exit_status"] == "Submitted"
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert (
            ledger.read_artifact(submitted_candidate(ledger, "initial").artifact)
            == b'{"proposal":true}'
        )
