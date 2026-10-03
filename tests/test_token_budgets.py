"""Token budgets: bounded reservations before dispatch, conservative settlement."""

import json
from http.client import RemoteDisconnected

import pytest
from test_chat_completions import response, server
from test_experimental_tasks import CONFIG, CSV, ENVIRONMENT, TASK, Model, Script

from warranted._acceptance import _encode
from warranted._chat_completions import MAX_BYTES, MESSAGE_MARGIN, LocalChatCompletions
from warranted._ledger import BudgetExceeded, Ledger, Manifest, Outcome, Result
from warranted._tasks import Project, RunConfig, RunOutcome
from warranted._worker import (
    AttemptResult,
    Episode,
    Journal,
    UnknownOutcome,
    WorkerModel,
)

MESSAGE = [{"role": "user", "content": "Return a JSON command"}]


DIGEST = "a" * 64
INSTALLED = {"model": {"name": "gemma4:26b", "digest": DIGEST}}


class Verified(LocalChatCompletions):
    verified_models = frozenset({f"gemma4:26b@{DIGEST}"})


def verified(url, **options):
    return Verified(url, "gemma4:26b", model_digest=DIGEST, **options)


def setup(root, client, **allowances):
    with Ledger.create(
        root,
        Manifest("tokens", "1", "run", "world", {}, {"model": 4} | allowances),
        {"model-api": client.snapshot},
    ):
        pass


def query(root, client, messages=MESSAGE):
    with Ledger.open(root) as ledger:
        episode = Episode(
            "probe",
            "Return a command",
            (),
            model=client.model,
            model_service=client.service_id,
        )
        return WorkerModel(Journal(ledger, episode), client).query(messages)


def bound(client, messages=MESSAGE):
    wire = json.dumps({"messages": messages}).encode()
    return client.reservation(wire)


def balances(root):
    with Ledger.open(root) as ledger:
        accounting = ledger.accounting()
        return {
            unit: (accounting[unit].spent, accounting[unit].reserved)
            for unit in ("model", "prompt_tokens", "completion_tokens")
        }


def test_a_verified_model_reserves_its_bound_and_settles_reported_tokens(tmp_path):
    with server(response(), metadata=INSTALLED) as (url, calls):
        client = verified(url)
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=10_000, completion_tokens=1_000)
        query(root, client)
    [wire] = [body for path, body in calls if path == "/v1/chat/completions"]
    expected = len(_encode(wire))
    reserved = bound(client)
    assert reserved == {
        "model": 1,
        "prompt_tokens": expected + MESSAGE_MARGIN * 2,
        "completion_tokens": client.max_tokens,
    }
    with Ledger.open(root) as ledger:
        [operation] = ledger.operations()
        assert dict(operation.reservation) == reserved
        assert operation.completion.breaches == ()
    assert balances(root) == {
        "model": (1, 0),
        "prompt_tokens": (12, 0),
        "completion_tokens": (6, 0),
    }


def test_a_bound_above_the_remaining_allowance_is_refused_before_dispatch(tmp_path):
    with server(response(), metadata=INSTALLED) as (url, calls):
        client = verified(url)
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=50, completion_tokens=1_000)
        with pytest.raises(BudgetExceeded):
            query(root, client)
        assert calls == []
    with Ledger.open(root) as ledger:
        assert ledger.operations() == ()


def test_a_lost_response_keeps_its_whole_token_reservation_across_restart(tmp_path):
    with server(response(), drop="before", metadata=INSTALLED) as (url, calls):
        client = verified(url)
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=10_000, completion_tokens=1_000)
        with pytest.raises((RemoteDisconnected, ValueError)):
            query(root, client)
        # A restarted host recomputes the identical reservation and stays blocked.
        with pytest.raises(UnknownOutcome):
            query(root, client)
        assert [path for path, _ in calls].count("/v1/chat/completions") == 1
    reserved = bound(client)
    assert balances(root) == {
        "model": (0, 1),
        "prompt_tokens": (0, reserved["prompt_tokens"]),
        "completion_tokens": (0, reserved["completion_tokens"]),
    }


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 19},
        {"prompt_tokens": -1, "completion_tokens": 6, "total_tokens": 5},
    ],
)
def test_unmeasured_usage_is_charged_at_the_bound_never_zero(tmp_path, usage):
    with server(response(usage=usage), metadata=INSTALLED) as (url, _):
        client = verified(url)
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=10_000, completion_tokens=1_000)
        with pytest.raises(RuntimeError):
            query(root, client)
    reserved = bound(client)
    assert balances(root) == {
        "model": (1, 0),
        "prompt_tokens": (reserved["prompt_tokens"], 0),
        "completion_tokens": (reserved["completion_tokens"], 0),
    }


def test_reported_tokens_above_the_bound_are_a_breach_that_blocks(tmp_path):
    over = {"prompt_tokens": 9_000, "completion_tokens": 6, "total_tokens": 9_006}
    with server(response(usage=over), metadata=INSTALLED) as (url, _):
        client = verified(url)
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=10_000, completion_tokens=1_000)
        query(root, client)
    with Ledger.open(root) as ledger:
        [operation] = ledger.operations()
        assert operation.completion.breaches == ("prompt_tokens",)
        assert ledger.accounting()["prompt_tokens"].spent == 9_000
        with pytest.raises(BudgetExceeded, match="breach"):
            ledger.check_budget()


def test_a_request_that_is_never_sent_settles_zero_tokens(tmp_path):
    huge = [{"role": "user", "content": "x" * MAX_BYTES}]
    with server(response(), metadata=INSTALLED) as (url, calls):
        client = verified(url)
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=10_000, completion_tokens=1_000)
        with pytest.raises(RuntimeError):
            query(root, client, huge)
        assert calls == []
    assert balances(root) == {
        "model": (0, 0),
        "prompt_tokens": (0, 0),
        "completion_tokens": (0, 0),
    }


def test_an_unverified_model_cannot_serve_a_project_that_allows_tokens(tmp_path):
    with server(response(), metadata=INSTALLED) as (url, calls):
        client = LocalChatCompletions(url, "gemma4:26b")
        assert client.reserved_units == {"model"}
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=10_000, completion_tokens=1_000)
        with pytest.raises(ValueError, match="enforced units"):
            query(root, client)
        assert calls == []
    with Ledger.open(root) as ledger:
        assert ledger.operations() == ()


def test_an_unverified_model_settles_only_attempts(tmp_path):
    with server(response(), metadata=INSTALLED) as (url, _):
        client = LocalChatCompletions(url, "gemma4:26b")
        root = tmp_path / "ledger"
        setup(root, client)
        query(root, client)
    with Ledger.open(root) as ledger:
        [operation] = ledger.operations()
        assert dict(operation.completion.result.usage) == {"model": 1}
        assert "tokens.json" in operation.completion.observation.artifacts


def test_verification_is_part_of_the_pinned_service_identity():
    url = "http://127.0.0.1:9/v1"
    plain = LocalChatCompletions(url, "gemma4:26b", model_digest=DIGEST)
    assert not plain.token_bounded and verified(url).token_bounded
    assert plain.service_id != verified(url).service_id


def test_a_tag_without_a_pinned_digest_is_never_verified():
    client = Verified("http://127.0.0.1:9/v1", "gemma4:26b")
    assert not client.token_bounded
    assert client.reserved_units == {"model"}


@pytest.mark.parametrize(
    "installed",
    [
        {"model": {"name": "gemma4:26b", "digest": "b" * 64}},
        {"model": {"name": "other:1b", "digest": DIGEST}},
    ],
)
def test_a_repointed_tag_is_refused_before_inference(tmp_path, installed):
    with server(response(), metadata=installed) as (url, calls):
        client = verified(url)
        root = tmp_path / "ledger"
        setup(root, client, prompt_tokens=10_000, completion_tokens=1_000)
        with pytest.raises(RuntimeError, match="infrastructure_failure"):
            query(root, client)
        assert [path for path, _ in calls] == ["/api/tags"]
    assert balances(root) == {
        "model": (0, 0),
        "prompt_tokens": (0, 0),
        "completion_tokens": (0, 0),
    }


class Declares:
    """A model service that declares one set of units and reserves another."""

    model = "scripted-model-v1"

    def __init__(self, declared, reserved):
        self.reserved_units, self._reserved = frozenset(declared), reserved
        self.calls = 0

    def reservation(self, payload):
        return dict(self._reserved)

    def __call__(self, request, payload):
        self.calls += 1
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, dict(self._reserved), 1),
            {"response": b'{"command": "true"}'},
        )


@pytest.mark.parametrize(
    "declared, reserved, error",
    [
        (
            {"model", "prompt_tokens", "completion_tokens"},
            {"model": 1},
            "did not declare",
        ),
        ({"model"}, {"model": 1}, "enforced units"),
    ],
)
def test_a_service_cannot_leave_an_enforced_unit_unreserved(
    tmp_path, declared, reserved, error
):
    service = Declares(declared, reserved)
    root = tmp_path / "ledger"
    with Ledger.create(
        root,
        Manifest(
            "tokens",
            "1",
            "run",
            "world",
            {},
            {"model": 4, "prompt_tokens": 100, "completion_tokens": 100},
        ),
        {},
    ):
        pass
    with Ledger.open(root) as ledger, pytest.raises(ValueError, match=error):
        WorkerModel(Journal(ledger, Episode("probe", "o", ())), service).query(MESSAGE)
    assert service.calls == 0


class TokenModel(Model):
    """The scripted task-layer model, reserving a fixed token bound per call."""

    reserved_units = frozenset({"model", "prompt_tokens", "completion_tokens"})

    def reservation(self, payload):
        return {"model": 1, "prompt_tokens": 400, "completion_tokens": 50}

    def __call__(self, request, payload):
        attempt = super().__call__(request, payload)
        usage = {"model": 1, "prompt_tokens": 100, "completion_tokens": 20}
        return AttemptResult(
            Result(attempt.result.outcome, attempt.result.exit_code, usage, 1),
            attempt.raw,
        )


def project(tmp_path, **tokens):
    return Project.create(
        tmp_path / "project",
        CSV.CsvDomain(),
        {"model": 40, "tool": 40, "check": 40} | tokens,
        environment=Script([]),
        environment_id=ENVIRONMENT,
    )


def test_a_project_with_token_allowances_refuses_a_service_without_them(tmp_path):
    proj = project(tmp_path, prompt_tokens=1_000, completion_tokens=1_000)
    with pytest.raises(ValueError, match="token units"):
        proj.start(TASK, CONFIG, Model())
    assert proj.runs() == ()


def test_a_run_token_cap_ends_the_run_before_the_model_is_called(tmp_path):
    proj = project(tmp_path, prompt_tokens=10_000, completion_tokens=1_000)
    model = TokenModel()
    capped = RunConfig(CONFIG.model, CONFIG.max_steps, {"prompt_tokens": 399})
    result = proj.start(TASK, capped, model)
    assert result.outcome is RunOutcome.INCOMPLETE
    assert "budget exhausted" in result.detail
    assert model.calls == 0
