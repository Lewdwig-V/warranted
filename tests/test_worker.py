"""Actual framework recovery with independently witnessed external calls."""

import json
import multiprocessing

import pytest

from warranted.ledger import (
    CorruptArtifact,
    Ledger,
    Manifest,
    OperationConflict,
    Outcome,
    Result,
    Snapshot,
)
from warranted.worker import AttemptResult, Episode, UnknownOutcome, run_workflow


def project(root):
    with Ledger.create(
        root / "ledger",
        Manifest("m3-recovery", "1", "run", "world", {}, {"model": 10, "tool": 10}),
        {"task": Snapshot(b"Submit the fixture.", "test", "1")},
    ):
        pass


def boundary(root, request, payload):
    kind = request.origin.kind
    with (root / "executions.jsonl").open("ab") as witness:
        witness.write(json.dumps([request.origin.operation_id, kind]).encode() + b"\n")
    raw = (
        {"response": b'{"command":"submit"}'}
        if kind == "model"
        else {
            "stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nready\n",
            "stderr": b"\xff",
        }
    )
    return AttemptResult(Result(Outcome.SUCCEEDED, 0, {kind: 1}, 1), raw)


def run(root, episode=None):
    return run_workflow(
        root / "ledger",
        root / "checkpoints.sqlite3",
        episode or Episode("first", "Submit the fixture.", ("task",)),
        model=lambda req, data: boundary(root, req, data),
        environment=lambda req, data: boundary(root, req, data),
    )


def counts(root):
    return [
        json.loads(line)[1]
        for line in (root / "executions.jsonl").read_bytes().splitlines()
    ]


def killed(root, pipe, when):
    complete = Ledger.complete

    def pause(self, session, request, result, raw):
        if request.origin.kind != "tool":
            return complete(self, session, request, result, raw)
        if when == "after":
            result = complete(self, session, request, result, raw)
        pipe.send("tool completed externally")
        pipe.recv()
        return result

    Ledger.complete = pause
    run(root)


def kill_at(root, when):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=killed, args=(root, child, when))
    process.start()
    try:
        assert parent.poll(20), f"host did not reach crash point: {process.exitcode}"
        assert parent.recv() == "tool completed externally"
        process.kill()
        process.join(10)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()


def test_ledger_ahead_of_graph_reuses_completed_execution(tmp_path):
    project(tmp_path)
    kill_at(tmp_path, "after")
    assert run(tmp_path)["exit_status"] == "Submitted"
    assert run(tmp_path)["exit_status"] == "Submitted"
    assert counts(tmp_path) == ["model", "tool"]
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert {k: (v.spent, v.reserved) for k, v in ledger.accounting().items()} == {
            "model": (1, 0),
            "tool": (1, 0),
        }
        tool = next(
            op for op in ledger.operations() if op.request.origin.kind == "tool"
        )
        assert (
            ledger.read_artifact(tool.completion.observation.artifacts["stderr"])
            == b"\xff"
        )
        receipt = next(
            o for o in ledger.history() if o.origin.kind == "episode-finished"
        )
        for operation in ledger.operations():
            observation = operation.completion.observation
            for channel, ref in observation.artifacts.items():
                assert (
                    receipt.origin.inputs[
                        f"observation/{observation.sequence}/{channel}"
                    ]
                    == ref
                )


def test_unknown_execution_blocks_resume_and_new_episode(tmp_path):
    project(tmp_path)
    kill_at(tmp_path, "before")
    for episode in (None, None, Episode("replacement", "Try again", ("task",))):
        with pytest.raises(UnknownOutcome, match="unknown"):
            run(tmp_path, episode)
    assert counts(tmp_path) == ["model", "tool"]
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert ledger.accounting()["tool"].reserved == 1
        assert ledger.accounting()["tool"].spent == 0


def test_checkpoint_loss_reuses_host_receipt_and_changed_request_conflicts(tmp_path):
    project(tmp_path)
    assert run(tmp_path)["exit_status"] == "Submitted"
    (tmp_path / "checkpoints.sqlite3").unlink()
    assert run(tmp_path)["exit_status"] == "Submitted"
    assert counts(tmp_path) == ["model", "tool"]
    with pytest.raises(OperationConflict, match="different"):
        run(tmp_path, Episode("first", "Changed objective", ("task",)))


def test_graph_progress_without_host_evidence_blocks(tmp_path):
    project(tmp_path)
    run(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    project(other)
    # A checkpoint from another project cannot grant progress in this one.
    with pytest.raises(UnknownOutcome, match="checkpoint"):
        run_workflow(
            other / "ledger",
            tmp_path / "checkpoints.sqlite3",
            Episode("first", "Submit the fixture.", ("task",)),
            model=lambda *_: pytest.fail("must not dispatch"),
            environment=lambda *_: pytest.fail("must not dispatch"),
        )


@pytest.mark.parametrize("checkpoint_lost", [False, True])
@pytest.mark.parametrize("channel", ["response", "stdout"])
@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_finished_receipt_requires_intact_attempt_evidence(
    tmp_path, checkpoint_lost, channel, damage
):
    project(tmp_path)
    run(tmp_path)
    if checkpoint_lost:
        (tmp_path / "checkpoints.sqlite3").unlink()
    with Ledger.open(tmp_path / "ledger") as ledger:
        observation = next(
            op.completion.observation
            for op in ledger.operations()
            if channel in op.completion.observation.artifacts
        )
        ref = observation.artifacts[channel]
        path = ledger.root / "artifacts" / "sha256" / ref.digest
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(b"corrupt")
    with pytest.raises(FileNotFoundError if damage == "missing" else CorruptArtifact):
        run(tmp_path)
    assert counts(tmp_path) == ["model", "tool"]
