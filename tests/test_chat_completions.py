"""Offline HTTP attempts exercise the real journal and worker model boundary."""

import json
import runpy
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from minisweagent.exceptions import FormatError

from warranted.chat_completions import LocalChatCompletions
from warranted.ledger import Ledger, Manifest, OperationConflict, Outcome
from warranted.worker import Episode, Journal, UnknownOutcome, WorkerModel


@contextmanager
def server(body=None, status=200, drop=False, metadata=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, None))
            self.send_response(200)
            self.end_headers()
            value = (
                metadata["version"]
                if self.path == "/api/version"
                else {"models": [metadata["model"]]}
            )
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            calls.append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            if self.path == "/api/show":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(metadata["show"]).encode())
                return
            if drop == "before":
                self.close_connection = True
                return
            self.send_response(status)
            if drop == "partial":
                self.send_header("Content-Length", str(len(body) + 1))
            if status == 307:
                self.send_header("Location", "/redirected")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}/v1", calls
        finally:
            httpd.shutdown()
            thread.join()


def setup(root, client):
    with Ledger.create(
        root,
        Manifest("local-chat", "1", "run", "world", {}, {"model": 2}),
        {"model-api": client.snapshot},
    ):
        pass


def episode(client):
    return Episode(
        "probe",
        "Return a command",
        (),
        model=client.model,
        model_service=client.service_id,
    )


def query(root, client):
    with Ledger.open(root) as ledger:
        return WorkerModel(Journal(ledger, episode(client)), client).query(
            [
                {
                    "role": "user",
                    "content": "Return a JSON command",
                    "extra": {"host-only": "must not reach provider"},
                }
            ]
        )


def response(**changes):
    return json.dumps(
        {
            "model": "gemma4:26b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"command":"true"}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            **changes,
        }
    ).encode()


@pytest.mark.parametrize("drop", ["before", "partial"])
def test_lost_response_keeps_reservation_and_never_retries(tmp_path, drop):
    with server(response(), drop=drop) as (url, calls):
        client = LocalChatCompletions(url, "gemma4:26b")
        setup(tmp_path / "ledger", client)
        with pytest.raises((RemoteDisconnected, ValueError)):
            query(tmp_path / "ledger", client)
        for _ in range(2):
            with pytest.raises(UnknownOutcome):
                query(tmp_path / "ledger", client)
        assert len(calls) == 1
    with Ledger.open(tmp_path / "ledger") as ledger:
        assert ledger.operations()[0].state == "unknown"
        assert (
            ledger.accounting()["model"].spent,
            ledger.accounting()["model"].reserved,
        ) == (0, 1)


def test_oversized_prompt_is_known_local_failure(tmp_path):
    with server(response()) as (url, calls):
        client = LocalChatCompletions(url, "gemma4:26b")
        root = tmp_path / "ledger"
        setup(root, client)
        for _ in range(2):
            with Ledger.open(root) as ledger:
                with pytest.raises(RuntimeError, match="model attempt"):
                    WorkerModel(Journal(ledger, episode(client)), client).query(
                        [{"role": "user", "content": "x" * (2 * 1024 * 1024)}]
                    )
        assert calls == []
    with Ledger.open(root) as ledger:
        operation = ledger.operations()[0]
        assert operation.completion.result.outcome is Outcome.FAILED
        assert ledger.accounting()["model"].reserved == 0
        assert ledger.accounting()["model"].spent == 0


@pytest.mark.parametrize(
    "body,status",
    [
        (b"broken", 200),
        (response(model="other"), 200),
        (response(usage=None), 200),
        (
            response(
                usage={
                    "prompt_tokens": 1,
                    "completion_tokens": 300,
                    "total_tokens": 301,
                }
            ),
            200,
        ),
        (
            response(
                usage={"prompt_tokens": 1, "completion_tokens": True, "total_tokens": 2}
            ),
            200,
        ),
        (
            response(
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3}
            ),
            200,
        ),
        (b'{"error":"unavailable"}', 503),
        (b"redirect", 307),
    ],
)
def test_bad_http_response_is_retained_charged_and_not_retried(tmp_path, body, status):
    with server(body, status) as (url, calls):
        client = LocalChatCompletions(url, "gemma4:26b")
        setup(tmp_path / "ledger", client)
        for _ in range(2):
            with pytest.raises(RuntimeError, match="model attempt"):
                query(tmp_path / "ledger", client)
        assert len(calls) == 1
    with Ledger.open(tmp_path / "ledger") as ledger:
        completion = ledger.operations()[0].completion
        assert completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE
        assert (
            ledger.read_artifact(completion.observation.artifacts["http-response"])
            == body
        )
        assert (
            ledger.accounting()["model"].spent,
            ledger.accounting()["model"].reserved,
        ) == (1, 0)


def test_success_reuses_response_and_pins_wire_configuration(tmp_path, monkeypatch):
    body = response()
    with server(body) as (url, calls):
        # Environment proxies must not receive the prompt.
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("no_proxy", "")
        client = LocalChatCompletions(url, "gemma4:26b", max_tokens=64)
        root = tmp_path / "ledger"
        setup(root, client)
        assert query(root, client)["extra"]["actions"] == [{"command": "true"}]
        assert query(root, client)["content"] == '{"command":"true"}'
        with pytest.raises(OperationConflict):
            query(root, replace(client, max_tokens=65))
        assert len(calls) == 1
        path, wire = calls[0]
        assert path == "/v1/chat/completions"
        assert wire == {
            "model": "gemma4:26b",
            "max_tokens": 64,
            "temperature": 0,
            "seed": 0,
            "reasoning_effort": "none",
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": "Return a JSON command"}],
        }
    with Ledger.open(root) as ledger:
        completion = ledger.operations()[0].completion
        assert completion.result.usage == {"model": 1}
        raw = completion.observation.artifacts
        assert ledger.read_artifact(raw["http-response"]) == body
        assert json.loads(ledger.read_artifact(raw["tokens.json"])) == {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
        }
        assert json.loads(ledger.read_artifact(raw["http-request.json"])) == wire


@pytest.mark.parametrize(
    "reason,content,error,outcome",
    [
        ("length", '{"command":"true"}', RuntimeError, Outcome.FAILED),
        ("stop", "malformed", FormatError, Outcome.SUCCEEDED),
    ],
)
def test_truncated_or_malformed_command_never_reaches_a_tool(
    tmp_path, reason, content, error, outcome
):
    body = response(
        choices=[
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": reason,
            }
        ]
    )
    with server(body) as (url, calls):
        client = LocalChatCompletions(url, "gemma4:26b")
        root = tmp_path / "ledger"
        setup(root, client)
        for _ in range(2):
            with pytest.raises(error):
                query(root, client)
        assert len(calls) == 1
    with Ledger.open(root) as ledger:
        completion = ledger.operations()[0].completion
        assert completion.result.outcome is outcome
        assert completion.result.usage == {"model": 1}
        assert (
            ledger.read_artifact(completion.observation.artifacts["response"])
            == content.encode()
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1?token=secret",
        "http://user@127.0.0.1:11434/v1",
    ],
)
def test_only_explicit_local_endpoint_is_allowed(url):
    with pytest.raises(ValueError):
        LocalChatCompletions(url, "gemma4:26b")


def test_probe_rejects_cloud_and_changed_model_then_reuses_offline(tmp_path):
    probe = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/m5/local_model.py")
    )
    metadata = {
        "version": {"version": "test"},
        "model": {"name": "gemma4:26b", "size": 1, "digest": "a" * 64},
        "show": {"remote_host": "https://cloud.example"},
    }
    with server(response(), metadata=metadata) as (url, calls):
        client = LocalChatCompletions(url, "gemma4:26b")
        with pytest.raises(ValueError, match="local model files"):
            probe["initialize"](tmp_path / "cloud", client)
        assert not (tmp_path / "cloud").exists()
        metadata["show"] = {}
        probe["initialize"](tmp_path / "changed", client)
        metadata["model"]["digest"] = "b" * 64
        with pytest.raises(RuntimeError, match="model attempt"):
            probe["run"](tmp_path / "changed")
        assert not any(path == "/v1/chat/completions" for path, _ in calls)
        probe["initialize"](tmp_path / "good", client)
        assert probe["run"](tmp_path / "good") == {"command": "true", "executed": False}
        assert sum(path == "/v1/chat/completions" for path, _ in calls) == 1
    # Server is stopped. Reuse must not even query metadata.
    assert probe["run"](tmp_path / "good") == {"command": "true", "executed": False}


def test_probe_rejects_changed_adapter_before_inference(tmp_path, monkeypatch):
    probe = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/m5/local_model.py")
    )
    metadata = {
        "version": {"version": "test"},
        "model": {"name": "gemma4:26b", "size": 1, "digest": "a" * 64},
        "show": {},
    }
    with server(response(), metadata=metadata) as (url, calls):
        client = LocalChatCompletions(url, "gemma4:26b")
        root = tmp_path / "adapter-changed"
        probe["initialize"](root, client)
        changed = Path(probe["chat_completions"].__file__)
        read_bytes = Path.read_bytes

        def changed_bytes(path):
            data = read_bytes(path)
            return data + b"\n# changed model adapter\n" if path == changed else data

        monkeypatch.setattr(Path, "read_bytes", changed_bytes)
        with pytest.raises(ValueError, match="model adapter changed"):
            probe["run"](root)
        assert not any(path == "/v1/chat/completions" for path, _ in calls)
