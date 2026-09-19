"""A separate HTTP process witnesses requests independently of both host stores."""

import json
import multiprocessing
import runpy
from contextlib import contextmanager
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError

import pytest

from warranted.attempts import ReceiptService
from warranted.ledger import BudgetExceeded, Ledger, Manifest, Outcome, Result, Snapshot
from warranted.worker import AttemptResult, Episode, UnknownOutcome, run_workflow


def serve(root, pipe):
    script = Path(__file__).resolve().parents[1] / "examples/m3/fake_service.py"
    runpy.run_path(str(script))["serve"](root, pipe)


@contextmanager
def service(root, **options):
    root.mkdir()
    (root / "config.json").write_text(
        json.dumps(
            {
                "service_id": "fixture-v1",
                "mode": "receipts",
                "drop_response": False,
                "responses": ['{"command":"submit"}'],
                "usage": [1],
                **options,
            }
        )
    )
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=serve, args=(root, child))
    process.start()
    try:
        assert parent.poll(15), "fake service did not start"
        yield ReceiptService(parent.recv(), "fixture-v1")
    finally:
        process.terminate()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()
        parent.close()
        child.close()


def project(root, allowance=10):
    with Ledger.create(
        root / "ledger",
        Manifest(
            "m3-attempts", "1", "run", "world", {}, {"model": allowance, "tool": 10}
        ),
        {"task": Snapshot(b"Submit", "fixture", "1")},
    ):
        pass


def run(root, client, reservation=1):
    def tool(*_):
        with (root / "tools.log").open("ab") as file:
            file.write(b"executed\n")
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"tool": 1}, 1),
            {"stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", "stderr": b""},
        )

    return run_workflow(
        root / "ledger",
        root / "graph.sqlite3",
        Episode(
            "first",
            "Submit",
            ("task",),
            model="fixture-v1",
            model_service="fixture-v1",
            model_reservation=reservation,
        ),
        model=client,
        environment=tool,
        reconcile=client.reconcile,
    )


def requests(root):
    return [
        json.loads(line)
        for line in (root / "service" / "requests.jsonl").read_text().splitlines()
    ]


def test_lost_response_reconciles_exact_receipt_without_second_post(tmp_path):
    project(tmp_path)
    with service(tmp_path / "service", drop_response=True) as client:
        with pytest.raises(RemoteDisconnected):
            run(tmp_path, client)
        with Ledger.open(tmp_path / "ledger") as ledger:
            assert ledger.accounting()["model"].reserved == 1
        for _ in range(2):
            assert run(tmp_path, client)["exit_status"] == "Submitted"
    assert len(requests(tmp_path)) == 1
    assert (tmp_path / "tools.log").read_bytes() == b"executed\n"
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert (
            ledger.accounting()["model"].spent,
            ledger.accounting()["model"].reserved,
        ) == (1, 0)


@pytest.mark.parametrize(
    "mode", ["unprovable", "unknown-usage", "mismatched", "redirect"]
)
def test_unprovable_or_invalid_receipt_never_defaults_to_free_retry(tmp_path, mode):
    project(tmp_path)
    with service(tmp_path / "service", mode=mode, drop_response=True) as client:
        with pytest.raises((RemoteDisconnected, HTTPError)):
            run(tmp_path, client)
        for _ in range(2):
            with pytest.raises((UnknownOutcome, ValueError)):
                run(tmp_path, client)
    assert len(requests(tmp_path)) == 1
    assert not (tmp_path / "tools.log").exists()
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert (
            ledger.accounting()["model"].spent,
            ledger.accounting()["model"].reserved,
        ) == (0, 1)


def test_parse_failure_is_charged_before_next_model_attempt(tmp_path):
    project(tmp_path)
    with service(
        tmp_path / "service",
        responses=["malformed", '{"command":"submit"}'],
        usage=[2, 3],
    ) as client:
        assert run(tmp_path, client, reservation=3)["exit_status"] == "Submitted"
        assert run(tmp_path, client, reservation=3)["exit_status"] == "Submitted"
    assert len(requests(tmp_path)) == 2
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert ledger.accounting()["model"].spent == 5
        first = next(
            op for op in ledger.operations() if op.request.origin.kind == "model"
        )
        assert (
            ledger.read_artifact(first.completion.observation.artifacts["response"])
            == b"malformed"
        )


def test_cumulative_budget_blocks_next_request_after_billed_parse_failure(tmp_path):
    project(tmp_path, allowance=4)
    with service(tmp_path / "service", responses=["malformed"], usage=[2]) as client:
        for _ in range(2):
            with pytest.raises(BudgetExceeded):
                run(tmp_path, client, reservation=3)
    assert len(requests(tmp_path)) == 1
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert ledger.accounting()["model"].spent == 2


def test_receipt_from_different_operation_cannot_settle_unknown(tmp_path):
    project(tmp_path)
    with service(tmp_path / "service", drop_response=True) as client:
        with pytest.raises(RemoteDisconnected):
            run(tmp_path, client)
        receipt = tmp_path / "service" / "receipts.jsonl"
        value = json.loads(receipt.read_text())
        value["identity"]["operation"] = "episode/other/model/1"
        receipt.write_text(json.dumps(value) + "\n")
        with pytest.raises(ValueError, match="identity"):
            run(tmp_path, client)
    assert len(requests(tmp_path)) == 1


@pytest.mark.parametrize("action", ["dispatch", "reconcile", "resume"])
def test_different_service_cannot_dispatch_or_settle_recorded_attempt(tmp_path, action):
    project(tmp_path)
    with service(tmp_path / "service", drop_response=True) as original:
        with pytest.raises(RemoteDisconnected):
            run(tmp_path, original)
        with Ledger.open(tmp_path / "ledger") as ledger:
            request = ledger.operations()[0].request
        receipt = json.loads((tmp_path / "service/receipts.jsonl").read_text())
        receipt["identity"]["service"] = "replacement-v1"
        with service(tmp_path / "replacement", service_id="replacement-v1") as endpoint:
            replacement = ReceiptService(endpoint.url, "replacement-v1")
            (tmp_path / "replacement/receipts.jsonl").write_text(
                json.dumps(receipt) + "\n"
            )
            with pytest.raises(ValueError, match="service"):
                if action == "dispatch":
                    replacement(request, b'{"messages":[]}')
                elif action == "reconcile":
                    replacement.reconcile(request)
                else:
                    run(tmp_path, replacement)
        assert not (tmp_path / "replacement/requests.jsonl").exists()
        assert not (tmp_path / "replacement/lookups.jsonl").exists()
        with Ledger.open(tmp_path / "ledger") as ledger:
            assert ledger.operations()[0].state == "unknown"
            assert ledger.accounting()["model"].reserved == 1
            assert ledger.accounting()["model"].spent == 0
        assert not (tmp_path / "tools.log").exists()
        assert run(tmp_path, original)["exit_status"] == "Submitted"
    assert len(requests(tmp_path)) == 1


def interrupted_reconciliation(root, url, pipe):
    complete = Ledger.complete

    def pause(self, session, request, result, raw):
        if request.origin.kind == "model":
            pipe.send("receipt obtained")
            pipe.recv()
        return complete(self, session, request, result, raw)

    Ledger.complete = pause
    try:
        run(root, ReceiptService(url, "fixture-v1"))
    except UnknownOutcome:
        pipe.send("unknown")
        pipe.recv()


@pytest.mark.parametrize("mode", ["receipts", "unprovable"])
def test_repeated_deaths_during_reconciliation_keep_one_external_request(
    tmp_path, mode
):
    project(tmp_path)
    with service(tmp_path / "service", mode=mode, drop_response=True) as client:
        with pytest.raises(RemoteDisconnected):
            run(tmp_path, client)
        for _ in range(2):
            ctx = multiprocessing.get_context("spawn")
            parent, child = ctx.Pipe()
            process = ctx.Process(
                target=interrupted_reconciliation, args=(tmp_path, client.url, child)
            )
            process.start()
            try:
                assert parent.poll(15), (
                    f"resume did not reach barrier: {process.exitcode}"
                )
                assert parent.recv() == (
                    "receipt obtained" if mode == "receipts" else "unknown"
                )
            finally:
                process.kill()
                process.join(10)
                parent.close()
                child.close()
            with Ledger.open(tmp_path / "ledger") as ledger:
                assert ledger.accounting()["model"].reserved == 1
        if mode == "receipts":
            assert run(tmp_path, client)["exit_status"] == "Submitted"
        else:
            with pytest.raises(UnknownOutcome):
                run(tmp_path, client)
    assert len(requests(tmp_path)) == 1


def test_usage_above_reservation_is_charged_and_blocks_more_work(tmp_path):
    project(tmp_path)
    with service(tmp_path / "service", usage=[2]) as client:
        for _ in range(2):
            with pytest.raises(BudgetExceeded):
                run(tmp_path, client)
    assert len(requests(tmp_path)) == 1
    assert not (tmp_path / "tools.log").exists()
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert ledger.accounting()["model"].spent == 2
        assert ledger.operations()[0].completion.breaches == ("model",)


def test_known_provider_failure_retains_its_charge_without_hidden_retry(tmp_path):
    project(tmp_path)
    with service(
        tmp_path / "service", outcome="failed", exit_code=1, usage=[2]
    ) as client:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="model attempt: failed"):
                run(tmp_path, client, reservation=2)
    assert len(requests(tmp_path)) == 1
    assert not (tmp_path / "tools.log").exists()
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert ledger.accounting()["model"].spent == 2
