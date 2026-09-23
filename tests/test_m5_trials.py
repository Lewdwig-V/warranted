"""Trial accounting uses committed evidence, including unfinished work."""

import json
import runpy
import shutil

import pytest
from test_m5_treatments import SCRIPT, scripted

from warranted import proofs
from warranted.contexts import Condition
from warranted.ledger import Ledger, Origin, Outcome, Request, Result, Snapshot

TRIALS = runpy.run_path(str(SCRIPT.with_name("trials.py")))


def test_stage_results_survive_deleted_reports_and_repeated_resume(
    tmp_path, monkeypatch
):
    demo, root, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.A)
    demo["demonstrate"]("start", root, bundle)
    resumed = demo["demonstrate"]("resume", root, bundle)
    for path in (root / "reports").iterdir():
        path.unlink()
    with Ledger.open(root / "ledger") as ledger:
        results = [
            o
            for o in ledger.history()
            if o.origin.operation_id.startswith("treatment/result/")
        ]
        assert len(results) == 2
        result = json.loads(ledger.read_artifact(results[-1].artifacts["result.json"]))
        assert result["candidate"] == resumed["candidate"]
        assert result["qualified"] is True
    assert demo["demonstrate"]("resume", root, bundle)["new_operations"] == 0
    with Ledger.open(root / "ledger") as ledger:
        before = (ledger.history(), ledger.operations())
        summary = TRIALS["trial_result"](ledger)
        assert summary["state"] == "completed"
        assert summary["qualified"] is True
        assert summary["spent"] == resumed["spent"]
        assert summary["stages"]["resume"]["old_candidate"]["old_receipt"] == "stale"
        assert (ledger.history(), ledger.operations()) == before
        raw = next(
            op.completion.observation.artifacts["stderr"]
            for op in ledger.operations()
            if op.request.origin.kind == "tool"
        )
        (ledger.root / "artifacts/sha256" / raw.digest).write_bytes(b"damaged")
        with pytest.raises(ValueError, match="artifact"):
            TRIALS["trial_result"](ledger)


def test_plan_preserves_all_slots_and_rejects_replaced_run(tmp_path, monkeypatch):
    _, _, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.A)
    root = tmp_path / "campaign"
    TRIALS["initialize"](root, bundle, repetitions=2)
    initial = TRIALS["report"](root)
    assert len(initial["trials"]) == 20
    assert all(g["planned"] == 2 and g["qualified"] == 0 for g in initial["groups"])
    assert initial["plan"]["split"] == "development"
    assert initial["plan"]["lineages"] == TRIALS["LINEAGES"]
    assert not any(initial["known_spent"].values())
    assert initial["usage_complete"]
    first, second = [root / r["path"] for r in initial["trials"][:2]]
    # Even a genuine, intact ledger cannot fill a different predeclared slot.
    shutil.rmtree(first / "ledger")
    shutil.copytree(second / "ledger", first / "ledger")
    shutil.rmtree(second / "ledger")
    # Mutable reports cannot supply a missing receipt or make a run succeed.
    (first / "reports").mkdir()
    (first / "reports/resume.json").write_text('{"qualified":true}')
    third = root / initial["trials"][2]["path"] / "ledger"
    with Ledger.open(third) as ledger:
        session = ledger.start_session()
        request = Request(
            Origin("failed-tool", "tool", "test", "1", {}), ledger.project
        )
        ledger.reserve(session, request, {"tool": 1})
        ledger.begin(session, request)
        completion = ledger.complete(
            session,
            request,
            Result(Outcome.FAILED, 1, {"tool": 1}, 1),
            {"stderr": b"failure"},
        )
        (
            third
            / "artifacts/sha256"
            / completion.observation.artifacts["stderr"].digest
        ).unlink()
    summary = TRIALS["report"](root)
    assert summary["plan_digest"] == initial["plan_digest"]
    assert [r["state"] for r in summary["trials"][:2]] == ["unreadable", "missing"]
    assert not summary["usage_complete"]
    assert summary["trials"][2]["state"] == "unreadable"
    assert summary["known_spent"]["tool"] == 1
    assert all(g["planned"] == 2 and g["qualified"] == 0 for g in summary["groups"])
    with pytest.raises(FileExistsError):
        TRIALS["initialize"](root, bundle)


def test_live_trial_report_retains_token_totals_and_unknown_model_usage(
    tmp_path, monkeypatch
):
    _, _, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.A)
    client = type(
        "Client",
        (),
        {
            "model": "qwen3.8:27b",
            "service_id": "local-chat-completions/test",
            "snapshot": Snapshot(b"pinned API configuration", "test-api", "1"),
        },
    )()
    monkeypatch.setitem(TRIALS["T"], "local_runtime", lambda _: b"pinned runtime")
    root = tmp_path / "live-campaign"
    TRIALS["initialize"](root, bundle, model_client=client)
    first = root / "runs/001-csv-A/ledger"
    with Ledger.open(first) as ledger:
        session = ledger.start_session()
        request = Request(
            Origin("episode/initial/model/1", "model", client.service_id, "1", {}),
            ledger.project,
        )
        ledger.reserve(session, request, {"model": 1})
        ledger.begin(session, request)
        ledger.complete(
            session,
            request,
            Result(Outcome.SUCCEEDED, 0, {"model": 1}, 1),
            {
                "response": b'{"command":"true"}',
                "tokens.json": json.dumps(
                    {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
                ).encode(),
            },
        )
        lost = Request(
            Origin("episode/initial/model/2", "model", client.service_id, "1", {}),
            ledger.project,
        )
        ledger.reserve(session, lost, {"model": 1})
        ledger.begin(session, lost)
    summary = TRIALS["report"](root)
    row = summary["trials"][0]
    assert summary["plan"]["scope"] == "development"
    assert summary["plan"]["model"]["name"] == "qwen3.8:27b"
    assert row["token_usage"] == {
        "model_attempts": 2,
        "reported_requests": 1,
        "prompt_tokens": 11,
        "completion_tokens": 3,
        "total_tokens": 14,
        "complete": False,
    }
    assert row["reserved"]["model"] == 1
    assert summary["model_token_usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 3,
        "total_tokens": 14,
        "complete": False,
    }


@pytest.mark.parametrize("failure", ["unknown", "unproved"])
def test_unfinished_attempts_keep_usage_and_failure_details(
    tmp_path, monkeypatch, failure
):
    from test_proof_receipts import boundary

    demo, root, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.E)
    if failure == "unproved":
        boundary(monkeypatch, status=proofs.ProofStatus.UNPROVED)
    else:

        def lost(*args):
            raise RuntimeError("lost worker")

        monkeypatch.setitem(demo["demonstrate"].__globals__, "Sandbox", lost)
    with pytest.raises(RuntimeError):
        demo["demonstrate"]("start", root, bundle)
    with Ledger.open(root / "ledger") as ledger:
        summary = TRIALS["trial_result"](ledger)
    assert summary["qualified"] is False
    assert summary["spent"]["model"] == 1
    if failure == "unknown":
        assert summary["state"] == "unknown"
        assert summary["reserved"]["tool"] == 1
        assert "unknown" in summary["pending"].values()
    else:
        assert summary["state"] == "incomplete"
        assert summary["spent"]["proof"] == 1
        assert [r["status"] for r in summary["proof_attempts"].values()] == ["unproved"]


def test_later_unknown_attempt_blocks_completed_trial_and_retains_cost(
    tmp_path, monkeypatch
):
    demo, root, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.A)
    demo["demonstrate"]("start", root, bundle)
    demo["demonstrate"]("resume", root, bundle)
    with Ledger.open(root / "ledger") as ledger:
        session = ledger.start_session()
        request = Request(
            Origin("later-proof", "proof", "test", "1", {}), ledger.project
        )
        ledger.reserve(session, request, {"proof": 1})
        ledger.begin(session, request)
        summary = TRIALS["trial_result"](ledger)
        assert summary["state"] == "unknown"
        assert not summary["qualified"]
        assert summary["reserved"]["proof"] == 1
        ledger.complete(
            session,
            request,
            Result(Outcome.FAILED, 1, {"proof": 5}, 1),
            {"verification.json": b'{"status":"unproved"}'},
        )
        breached = TRIALS["trial_result"](ledger)
        assert breached["state"] == "budget_breach"
        assert not breached["qualified"]
        assert breached["spent"]["proof"] == 5
        assert breached["failed_operations"] == {"later-proof": "failed"}
