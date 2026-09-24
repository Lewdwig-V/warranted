"""Paid-provider attempts remain credential-free in CI."""

import base64
import json
import subprocess
from dataclasses import replace
from decimal import Decimal

import pytest
from test_chat_completions import query, setup

from warranted import openrouter
from warranted.ledger import Ledger, Outcome
from warranted.openrouter import OpenRouterChatCompletions
from warranted.worker import UnknownOutcome


@pytest.fixture
def remote(tmp_path, monkeypatch):
    secret = "sk-or-v1-" + "a" * 64
    key_file = tmp_path / "key"
    key_file.write_text(secret)
    client = OpenRouterChatCompletions(
        "https://openrouter.ai/api/v1", "z-ai/glm-5.3-flash", key_file=key_file
    )
    calls = []
    limits = {
        "limit": 1,
        "limit_remaining": 1,
        "limit_reset": None,
        "include_byok_in_limit": False,
    }
    endpoint = {
        "model_id": client.model,
        "provider_name": "InferenceNet",
        "tag": "inference-net/fp4",
        "quantization": "fp4",
        "context_length": 1048576,
        "max_completion_tokens": 131072,
        "max_prompt_tokens": None,
        "supported_parameters": [
            "seed",
            "temperature",
            "max_tokens",
            "reasoning",
            "response_format",
            "structured_outputs",
        ],
        "pricing": {"prompt": "0.000000075", "completion": "0.00000025"},
        "status": 0,
    }
    completion = {
        "id": "gen-offline-1",
        "model": client.model,
        "provider": "InferenceNet",
        "choices": [
            {
                "message": {"role": "assistant", "content": '{"command":"true"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
            "cost": 0.0000024,
            "is_byok": False,
        },
    }

    def request(path, data, key, seconds):
        calls.append((path, data, key, seconds))
        if path.endswith("/key"):
            value = {"data": limits}
        elif path.endswith("/endpoints"):
            value = {"data": {"id": client.model, "endpoints": [endpoint]}}
        else:
            value = completion
        return 200, json.dumps(value).encode()

    monkeypatch.setattr(openrouter, "_request", request)
    return client, calls, limits, endpoint, completion, secret


def test_billed_response_is_reused_without_key_or_network(tmp_path, remote):
    client, calls, _, _, _, secret = remote
    root = tmp_path / "ledger"
    setup(root, client)
    assert query(root, client)["extra"]["actions"] == [{"command": "true"}]
    client.key_file.unlink()
    assert query(root, client)["extra"]["actions"] == [{"command": "true"}]
    assert len(calls) == 1
    assert calls[0][2] == secret
    wire = json.loads(calls[0][1])
    assert wire["provider"]["allow_fallbacks"] is False
    assert wire["provider"]["require_parameters"] is True
    assert wire["provider"]["order"] == ["inference-net/fp4"]
    assert wire["response_format"]["json_schema"]["strict"] is True
    assert wire["reasoning"]["enabled"] is True
    with Ledger.open(root) as ledger:
        op = ledger.operations()[0]
        raw = {
            name: ledger.read_artifact(ref)
            for name, ref in op.completion.observation.artifacts.items()
        }
        assert json.loads(raw["cost.json"])["usd"] == "0.0000024"
        assert secret.encode() not in b"".join(raw.values()) + client.snapshot.data


def test_lost_paid_response_stays_unknown_without_retry(tmp_path, remote, monkeypatch):
    client, _, _, _, _, _ = remote
    attempts = []

    def lost(*args):
        attempts.append(args)
        raise TimeoutError("request deadline")

    monkeypatch.setattr(openrouter, "_request", lost)
    root = tmp_path / "ledger"
    setup(root, client)
    with pytest.raises(TimeoutError):
        query(root, client)
    with pytest.raises(UnknownOutcome):
        query(root, client)
    assert len(attempts) == 1
    with Ledger.open(root) as ledger:
        assert ledger.operations()[0].state == "unknown"
        assert ledger.accounting()["model"].reserved == 1


@pytest.mark.parametrize(
    "change", ["provider", "missing_cost", "negative_cost", "byok"]
)
def test_bad_paid_receipt_stops_worker(tmp_path, remote, change):
    client, _, _, _, completion, _ = remote
    if change == "provider":
        completion["provider"] = "Another provider"
    elif change == "missing_cost":
        del completion["usage"]["cost"]
    elif change == "negative_cost":
        completion["usage"]["cost"] = -1
    else:
        completion["usage"]["is_byok"] = True
    root = tmp_path / "ledger"
    setup(root, client)
    with pytest.raises(RuntimeError, match="model attempt"):
        query(root, client)
    with Ledger.open(root) as ledger:
        assert ledger.operations()[0].completion.result.outcome is not Outcome.SUCCEEDED


@pytest.mark.parametrize("change", ["unlimited", "reset", "spent", "routing", "price"])
def test_runtime_rejects_changed_or_unbounded_service(remote, change):
    client, calls, limits, endpoint, _, secret = remote
    pinned = client.runtime()
    assert secret.encode() not in pinned
    if change == "unlimited":
        limits["limit"] = None
    elif change == "reset":
        limits["limit_reset"] = "daily"
    elif change == "spent":
        limits["limit_remaining"] = 0
    elif change == "routing":
        endpoint["tag"] = "another-provider"
    else:
        endpoint["pricing"]["prompt"] = "0.1"
    failure = client.runtime_failure(pinned)
    assert failure.result.outcome is Outcome.INFRASTRUCTURE_FAILURE
    assert failure.result.usage == {"model": 0}
    assert json.loads(failure.raw["cost.json"])["usd"] == "0"
    assert all(not path.endswith("/chat/completions") for path, *_ in calls)


def test_runtime_ignores_consumed_credit_but_pins_limit(remote):
    client, _, limits, _, _, _ = remote
    pinned = client.runtime()
    limits["limit_remaining"] = 0.5
    assert client.runtime() == pinned
    limits["limit"] = 2
    assert client.runtime_failure(pinned) is not None


@pytest.mark.parametrize("failed_endpoint", ["key", "endpoints"])
def test_metadata_http_failure_retains_raw_receipt(
    tmp_path, remote, monkeypatch, failed_endpoint
):
    from test_m5_treatments import scripted

    from warranted.contexts import Condition

    client, _, _, _, _, _ = remote
    demo, _, bundle = scripted(tmp_path, monkeypatch, "migration", Condition.A)
    root = tmp_path / "metadata-failure"
    demo["initialize"](root, "migration", Condition.A, bundle, model_client=client)
    original = openrouter._request
    body = b'{"error":{"message":"upstream metadata unavailable"}}'

    def request(path, *args):
        return (
            (429, body)
            if path.endswith("/" + failed_endpoint)
            else original(path, *args)
        )

    monkeypatch.setattr(openrouter, "_request", request)
    with pytest.raises(RuntimeError):
        demo["demonstrate"]("start", root, bundle, model_client=client)
    with Ledger.open(root / "ledger") as ledger:
        failure = ledger.operations()[0].completion
        assert failure.result.outcome is Outcome.INFRASTRUCTURE_FAILURE
        assert failure.result.usage == {"model": 0}
        raw = failure.observation.artifacts
        assert ledger.read_artifact(raw[f"api/{failed_endpoint}-response"]) == body
        assert (
            json.loads(ledger.read_artifact(raw[f"api/{failed_endpoint}-status.json"]))
            == 429
        )
    monkeypatch.setattr(
        openrouter, "_request", lambda *_: pytest.fail("repeated metadata request")
    )
    client.key_file.unlink()
    with pytest.raises(RuntimeError):
        demo["demonstrate"]("start", root, bundle, model_client=client)


def test_remote_credentials_cannot_be_sent_to_another_host(remote):
    client, _, _, _, _, _ = remote
    with pytest.raises(ValueError, match="OpenRouter endpoint"):
        replace(client, base_url="https://example.invalid/api/v1")


def test_successful_preflight_survives_restart(tmp_path, remote, monkeypatch):
    from test_m5_treatments import scripted

    from warranted.contexts import Condition

    client, _, limits, _, _, secret = remote
    demo, _, bundle = scripted(tmp_path, monkeypatch, "migration", Condition.A)
    root = tmp_path / "successful-preflight"
    demo["initialize"](root, "migration", Condition.A, bundle, model_client=client)
    with Ledger.open(root / "ledger") as ledger:
        host = demo["M2"]["Experiment"](ledger, ledger.start_session(), root)
        episode = demo["prepare"](
            host, "migration", Condition.A, "initial", None, {}, client
        )
    limits["limit_remaining"] = 0.5
    demo["propose"](root, episode, client)
    with Ledger.open(root / "ledger") as ledger:
        operation = next(
            o for o in ledger.operations() if o.request.origin.kind == "model"
        )
        raw = {
            name: ledger.read_artifact(ref)
            for name, ref in operation.completion.observation.artifacts.items()
        }
        assert json.loads(raw["api/key-response"])["data"]["limit_remaining"] == 0.5
        assert json.loads(raw["api/endpoints-response"])["data"]["id"] == client.model
        assert json.loads(raw["api/key-status.json"]) == 200
        assert json.loads(raw["api/endpoints-status.json"]) == 200
        assert "cost.json" in raw and "http-response" in raw
        assert secret.encode() not in b"".join(raw.values())
    client.key_file.unlink()
    monkeypatch.setattr(
        openrouter, "_request", lambda *_: pytest.fail("network replay")
    )
    demo["propose"](root, episode, client)


def test_key_rotation_after_preflight_cannot_dispatch(tmp_path, remote, monkeypatch):
    from test_m5_treatments import scripted

    from warranted.contexts import Condition

    client, calls, _, _, _, _ = remote
    demo, _, bundle = scripted(tmp_path, monkeypatch, "migration", Condition.A)
    root = tmp_path / "rotated-key"
    demo["initialize"](root, "migration", Condition.A, bundle, model_client=client)
    original = OpenRouterChatCompletions.runtime_failure

    def rotate(self, expected, raw=None):
        result = original(self, expected, raw)
        assert result is None
        self.key_file.write_text("sk-or-v1-" + "b" * 64)
        return result

    monkeypatch.setattr(OpenRouterChatCompletions, "runtime_failure", rotate)
    with pytest.raises(RuntimeError):
        demo["demonstrate"]("start", root, bundle, model_client=client)
    assert all(not path.endswith("/chat/completions") for path, *_ in calls)
    with Ledger.open(root / "ledger") as ledger:
        operation = ledger.operations()[0]
        assert operation.completion.result.usage == {"model": 0}
        assert operation.completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE


def test_decimal_cost_rejects_nonfinite_values():
    for value in (True, None, "0.1", Decimal("NaN"), Decimal("Infinity"), -1):
        with pytest.raises(ValueError):
            openrouter._usd(value)


def test_transport_keeps_credentials_off_argv_and_bounds_process(monkeypatch):
    def run(args, data, **options):
        assert "secret-key" not in repr(args)
        assert json.loads(data)["key"] == "secret-key"
        assert options["seconds"] == 3
        assert options["output_limit"] < 5 * 1024 * 1024
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {"status": 200, "body": base64.b64encode(b"{}").decode()}
            ).encode(),
            b"",
        )

    monkeypatch.setattr(openrouter, "_run", run)
    assert openrouter._request("/api/v1/key", None, "secret-key", 3) == (200, b"{}")


def test_paid_treatment_checks_cap_before_dispatch_and_pins_twelve_steps(
    tmp_path, monkeypatch, remote
):
    from test_m5_treatments import scripted

    from warranted.contexts import Condition

    demo, _, bundle = scripted(tmp_path, monkeypatch, "migration", Condition.A)
    client, calls, limits, _, _, _ = remote
    root = tmp_path / "paid"
    demo["initialize"](
        root, "migration", Condition.A, bundle, model_client=client, max_steps=12
    )
    with Ledger.open(root / "ledger") as ledger:
        host = demo["M2"]["Experiment"](ledger, ledger.start_session(), root)
        episode = demo["prepare"](
            host, "migration", Condition.A, "initial", None, {}, client
        )
        assert episode.max_steps == 12
        assert episode.objective.startswith("Use at most twelve shell commands.")
        assert ledger.project.manifest.allowances["model"] == 24
    limits["limit"] = 2
    with pytest.raises(RuntimeError):
        demo["demonstrate"]("start", root, bundle, model_client=client)
    assert all(not path.endswith("/chat/completions") for path, *_ in calls)
    with Ledger.open(root / "ledger") as ledger:
        operation = ledger.operations()[0]
        assert operation.completion.result.usage == {"model": 0}
    client.key_file.unlink()
    with pytest.raises(RuntimeError):
        demo["demonstrate"]("start", root, bundle, model_client=client)
