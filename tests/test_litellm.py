"""litellm attempts against a fake provider: no network, no credentials.

litellm itself is not mocked. A local HTTP server speaks the OpenAI Chat
Completions and Anthropic Messages wire formats, and each attempt runs the real
`litellm.completion` in the adapter's child process.
"""

import hashlib
import json
import os
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_chat_completions import episode, query, setup
from test_experimental_tasks import TASK, project

import warranted._litellm as adapter_module
from warranted import LiteLLMChatCompletions, RunOutcome
from warranted._ledger import Ledger, Outcome
from warranted._tasks import RunConfig
from warranted._worker import Journal, UnknownOutcome, WorkerModel

# Fragments must not occur by chance in a ledger (hex digests, UUIDs, timestamps).
KEY = "sk-file-key-0123456789wxyzQ~vZ"
COMMAND = '{"command":"true"}'


def openai_body(content=COMMAND, finish="stop", **changes):
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
    }
    body.update(changes)
    return json.dumps(body).encode()


def anthropic_body(content=None, stop="end_turn", **changes):
    body = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": COMMAND}] if content is None else content,
        "stop_reason": stop,
        "usage": {"input_tokens": 12, "output_tokens": 6},
    }
    body.update(changes)
    return json.dumps(body).encode()


@contextmanager
def provider(status=200, body=None, mode=None):
    """A fake provider; records every request it receives."""
    calls = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path, dict(self.headers), json.loads(data)))
            if mode == "drip":
                # Headers trickle in forever: no client read timeout fires.
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                while not release.wait(0.2):
                    try:
                        self.wfile.write(b"X")
                        self.wfile.flush()
                    except OSError:
                        return
                return
            if mode == "close":  # read the request, then drop the connection
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            if mode == "sleep":  # past the request timeout, within the deadline
                release.wait(4)
                return
            reply = body
            if reply is None:
                if self.path.endswith("/messages"):
                    reply = anthropic_body()
                else:
                    reply = openai_body()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            extra = 10 if mode == "partial" else 0
            self.send_header("Content-Length", str(len(reply) + extra))
            self.end_headers()
            self.wfile.write(reply)
            if mode == "partial":
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)

        def log_message(self, *_):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False

    with Server(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}", calls
        finally:
            release.set()
            httpd.shutdown()
            thread.join()


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "secrets" / "key"
    path.parent.mkdir()
    path.write_text(KEY + "\n")
    path.chmod(0o600)
    return path


def client(key_file, url, model="openai/fake-model", **changes):
    base = url + "/v1" if model.startswith("openai/") else url
    settings = {"api_base": base, "max_tokens": 64, "timeout_seconds": 5}
    return LiteLLMChatCompletions(model, api_key_file=key_file, **(settings | changes))


def completion(root):
    with Ledger.open(root) as ledger:
        completion = ledger.operations()[0].completion
        raw = {
            name: ledger.read_artifact(ref)
            for name, ref in completion.observation.artifacts.items()
        }
        return completion.result, raw, ledger.accounting()["model"]


def stored(root: Path) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


def test_success_records_response_and_tokens(tmp_path, key_file, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-host-environment")
    with provider() as (url, calls):
        model = client(key_file, url)
        root = tmp_path / "ledger"
        setup(root, model)
        assert query(root, model)["extra"]["actions"] == [{"command": "true"}]
        assert query(root, model)["content"] == COMMAND  # reused, not re-sent
    assert len(calls) == 1
    path, headers, wire = calls[0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    # An unknown model gets no structured-output request in any form.
    assert not {"response_format", "tools", "tool_choice"} & wire.keys()
    assert wire["messages"] == [{"role": "user", "content": "Return a JSON command"}]
    result, raw, balance = completion(root)
    assert result.outcome is Outcome.SUCCEEDED
    assert result.usage == {"model": 1}
    assert (balance.spent, balance.reserved) == (1, 0)
    assert raw["response"] == COMMAND.encode()
    assert json.loads(raw["tokens.json"]) == {
        "prompt_tokens": 12,
        "completion_tokens": 6,
        "total_tokens": 18,
    }
    request = json.loads(raw["litellm-request.json"])
    assert request["model"] == "openai/fake-model"
    assert (request["num_retries"], request["max_retries"]) == (0, 0)
    assert request["stream"] is False
    assert "api_key" not in request


def test_anthropic_thinking_is_recorded_but_only_text_is_the_response(
    tmp_path, key_file
):
    body = anthropic_body(
        [
            {"type": "thinking", "thinking": '{"command":"rm -rf"}', "signature": "s"},
            {"type": "text", "text": COMMAND},
        ]
    )
    with provider(body=body) as (url, calls):
        model = client(key_file, url, "anthropic/claude-test")
        root = tmp_path / "ledger"
        setup(root, model)
        assert query(root, model)["extra"]["actions"] == [{"command": "true"}]
    path, headers, _ = calls[0]
    assert path == "/v1/messages"
    assert headers["x-api-key"] == KEY
    result, raw, _ = completion(root)
    assert result.outcome is Outcome.SUCCEEDED
    assert raw["response"] == COMMAND.encode()
    assert b"rm -rf" in raw["litellm-response.json"]


@pytest.mark.parametrize(
    "model,body",
    [
        ("openai/fake-model", openai_body(finish="length")),
        ("anthropic/claude-test", anthropic_body(stop="max_tokens")),
        ("anthropic/claude-test", anthropic_body([], stop="refusal")),
        ("openai/fake-model", openai_body(finish="content_filter")),
    ],
)
def test_truncation_and_refusal_fail_without_a_command(tmp_path, key_file, model, body):
    with provider(body=body) as (url, calls):
        model = client(key_file, url, model)
        root = tmp_path / "ledger"
        setup(root, model)
        for _ in range(2):
            with pytest.raises(RuntimeError, match="model attempt"):
                query(root, model)
    assert len(calls) == 1
    result, raw, balance = completion(root)
    assert result.outcome is Outcome.FAILED
    assert "tokens.json" in raw
    assert (balance.spent, balance.reserved) == (1, 0)


@pytest.mark.parametrize(
    "model,status",
    [
        *(("openai/fake-model", status) for status in (400, 401, 429, 500, 529)),
        ("anthropic/claude-test", 529),
        ("anthropic/claude-test", 429),
    ],
)
def test_http_errors_are_infrastructure_failures_sent_exactly_once(
    tmp_path, key_file, model, status
):
    body = json.dumps(
        {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    ).encode()
    with provider(status, body) as (url, calls):
        model = client(key_file, url, model)
        root = tmp_path / "ledger"
        setup(root, model)
        for _ in range(2):
            with pytest.raises(RuntimeError, match="model attempt"):
                query(root, model)
    assert len(calls) == 1  # no retries in litellm or the provider SDK
    result, raw, balance = completion(root)
    assert result.outcome is Outcome.INFRASTRUCTURE_FAILURE
    assert result.usage == {"model": 1}
    assert (balance.spent, balance.reserved) == (1, 0)
    assert json.loads(raw["litellm-response.json"])["error"]["class"]


@pytest.mark.parametrize(
    "model,body",
    [
        ("openai/fake-model", b"{not json"),
        ("anthropic/claude-test", b"{not json"),
        ("openai/fake-model", openai_body(usage=None)),
        ("anthropic/claude-test", anthropic_body(usage=None)),
        (
            "openai/fake-model",
            openai_body(
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3}
            ),
        ),
    ],
)
def test_malformed_or_unmeasured_replies_are_infrastructure_failures(
    tmp_path, key_file, model, body
):
    with provider(body=body) as (url, calls):
        model = client(key_file, url, model)
        root = tmp_path / "ledger"
        setup(root, model)
        with pytest.raises(RuntimeError, match="model attempt"):
            query(root, model)
    assert len(calls) == 1
    result, raw, _ = completion(root)
    assert result.outcome is Outcome.INFRASTRUCTURE_FAILURE
    assert "response" not in raw


def test_a_stalled_provider_is_killed_and_the_attempt_stays_unknown(
    tmp_path, key_file, monkeypatch
):
    # Enough start-up allowance that only the stalled provider can hit the deadline.
    monkeypatch.setattr(adapter_module, "STARTUP_SECONDS", 15)
    with provider(mode="drip") as (url, calls):
        model = client(key_file, url, timeout_seconds=1)
        root = tmp_path / "ledger"
        setup(root, model)
        with pytest.raises(ConnectionError, match="unknown"):
            query(root, model)
        with pytest.raises(UnknownOutcome):
            query(root, model)
    assert len(calls) == 1
    with Ledger.open(root) as ledger:
        assert ledger.operations()[0].state == "unknown"
        balance = ledger.accounting()["model"]
        assert (balance.spent, balance.reserved) == (0, 1)


def test_the_key_reaches_only_the_child_stdin(tmp_path, key_file, monkeypatch):
    seen = []
    run = adapter_module._run

    def spy(args, data=b"", **kwargs):
        seen.append((args, data, kwargs))
        return run(args, data, **kwargs)

    monkeypatch.setattr(adapter_module, "_run", spy)
    with provider() as (url, _):
        model = client(key_file, url)
        root = tmp_path / "ledger"
        setup(root, model)
        query(root, model)
    args, data, kwargs = seen[-1]
    assert KEY in data.decode()
    assert not any(KEY in arg for arg in args)
    env = kwargs["env"]
    assert KEY not in json.dumps(env)
    assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert not any("API_KEY" in name for name in env)
    digest = hashlib.sha256(KEY.encode()).hexdigest().encode()
    assert KEY.encode() not in stored(root)
    assert digest not in stored(root)


@pytest.mark.parametrize("problem", ["group", "other", "empty", "missing"])
def test_an_unsafe_or_missing_key_file_is_refused(key_file, problem):
    if problem == "group":
        key_file.chmod(0o640)
    elif problem == "other":
        key_file.chmod(0o604)
    elif problem == "empty":
        key_file.write_text("\n")
    else:
        key_file.unlink()
    with pytest.raises(ValueError, match="key file"):
        client(key_file, "http://127.0.0.1:9")


def test_native_structured_output_sends_the_schema(tmp_path, key_file):
    with provider() as (url, calls):
        model = client(key_file, url, "anthropic/claude-opus-5-5")
        assert model.structured_output is True
        assert json.loads(model.snapshot.data)["structured_output"] is True
        root = tmp_path / "ledger"
        setup(root, model)
        query(root, model)
    wire = calls[0][2]
    assert wire["output_format"]["schema"] == {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
        "additionalProperties": False,
    }
    assert "tool_choice" not in wire and "tools" not in wire


def test_an_unknown_model_sends_no_response_format(tmp_path, key_file):
    with provider() as (url, calls):
        model = client(key_file, url, "anthropic/claude-test-unknown")
        assert model.structured_output is False
        root = tmp_path / "ledger"
        setup(root, model)
        query(root, model)
    wire = calls[0][2]
    assert not {"output_format", "tool_choice", "tools", "response_format"} & set(wire)


def test_oversized_request_is_a_known_local_failure(tmp_path, key_file):
    with provider() as (url, calls):
        model = client(key_file, url)
        root = tmp_path / "ledger"
        setup(root, model)
        with Ledger.open(root) as ledger:
            with pytest.raises(RuntimeError, match="model attempt"):
                WorkerModel(Journal(ledger, episode(model)), model).query(
                    [{"role": "user", "content": "x" * (2 * 1024 * 1024)}]
                )
    assert calls == []
    result, _, balance = completion(root)
    assert result.outcome is Outcome.FAILED
    assert (balance.spent, balance.reserved) == (0, 0)


@pytest.mark.parametrize(
    "parameters",
    [
        {"api_key": "x"},
        {"num_retries": 3},
        {"retry_policy": {"InternalServerErrorRetries": 3}},
        {"extra_body": {"x": 1}},
        {"context_window_fallback_dict": {"openai/fake-model": "openai/other"}},
        {"mock_response": "x"},
        {"temperature": object()},
    ],
)
def test_reserved_or_unrecordable_parameters_are_refused(key_file, parameters):
    with pytest.raises(ValueError):
        client(key_file, "http://127.0.0.1:9", parameters=parameters)


def test_a_task_run_uses_the_adapter_and_pins_it(tmp_path, key_file):
    with provider() as (url, calls):
        model = client(key_file, url, parameters={"temperature": 0})
        proj, _ = project(tmp_path, ["correct"])
        config = RunConfig(model.model, max_steps=2)
        result = proj.start(TASK, config, model)
        assert result.outcome is RunOutcome.ACCEPTED
        assert len(calls) == 1
        assert calls[0][2]["temperature"] == 0
        index = proj.export(result.run_id, tmp_path / "export")
        assert index.is_file()
    assert KEY.encode() not in stored(tmp_path / "export")
    assert KEY.encode() not in stored(proj.root)


def test_resume_refuses_a_changed_configuration_but_accepts_a_rotated_key(
    tmp_path, key_file, monkeypatch
):
    with provider() as (url, calls):
        model = client(key_file, url)
        proj, _ = project(tmp_path, [None])
        run_id = proj.start(TASK, RunConfig(model.model, max_steps=2), model).run_id
        sent = len(calls)
        for changed in (
            client(key_file, url, parameters={"temperature": 0.5}),
            client(key_file, url, max_tokens=65),
        ):
            with pytest.raises(ValueError, match="adapter configuration differs"):
                proj.resume(run_id, changed)
        probe = adapter_module._probe
        for package in ("litellm", "openai", "httpx"):

            def other(name, package=package):
                found = probe(name)
                versions = {**found["versions"], package: "0.0.1"}
                return {**found, "versions": versions}

            monkeypatch.setattr(adapter_module, "_probe", other)
            with pytest.raises(ValueError, match="adapter configuration differs"):
                proj.resume(run_id, client(key_file, url))
        monkeypatch.setattr(adapter_module, "_probe", probe)
        with pytest.raises(ValueError, match="model boundary differs"):
            proj.resume(run_id, client(key_file, url, "openai/other-model"))
        rotated = key_file.parent / "rotated"
        rotated.write_text("sk-rotated-key")
        rotated.chmod(0o600)
        result = proj.resume(run_id, client(rotated, url))
        assert result.outcome is RunOutcome.INCOMPLETE
        assert len(calls) == sent  # the recorded episode is reused


def test_the_cli_builds_a_litellm_adapter(tmp_path, key_file):
    from warranted._cli import load_run_config

    config = tmp_path / "run.toml"
    config.write_text(
        'model = "anthropic/claude-test"\nprovider = "litellm"\nmax_steps = 3\n'
        f'[adapter]\napi_key_file = "{key_file}"\nmax_tokens = 128\n'
        'timeout_seconds = 30\napi_base = "http://127.0.0.1:9"\n'
        "[adapter.parameters]\ntemperature = 0\n"
    )
    run, model = load_run_config(str(config))
    assert run == RunConfig("anthropic/claude-test", 3)
    assert isinstance(model, LiteLLMChatCompletions)
    assert (model.max_tokens, model.timeout_seconds) == (128, 30)
    assert model.parameters == {"temperature": 0}
    assert model.api_base == "http://127.0.0.1:9"


def test_the_cli_refuses_a_group_readable_key(tmp_path, key_file, capsys):
    from test_cli import init

    from warranted._cli import main

    project_dir = init(tmp_path, capsys)
    key_file.chmod(0o640)
    config = tmp_path / "run.toml"
    config.write_text(
        'model = "openai/x"\nprovider = "litellm"\n'
        f'[adapter]\napi_key_file = "{key_file}"\n'
        "max_tokens = 64\ntimeout_seconds = 30\n"
    )
    task = Path(__file__).resolve().parents[1] / "examples/m7/csv/task.toml"
    assert main(["run", str(project_dir), str(task), "--config", str(config)]) == 2
    assert "key file" in capsys.readouterr().err


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("WARRANTED_LIVE_TESTS") != "1"
    or not os.environ.get("WARRANTED_LIVE_KEY_FILE")
    or not os.environ.get("WARRANTED_LIVE_MODEL"),
    reason="requires WARRANTED_LIVE_TESTS=1, WARRANTED_LIVE_KEY_FILE and "
    "WARRANTED_LIVE_MODEL; makes one real, paid request",
)
def test_one_live_request(tmp_path):
    model = LiteLLMChatCompletions(
        os.environ["WARRANTED_LIVE_MODEL"],
        api_key_file=Path(os.environ["WARRANTED_LIVE_KEY_FILE"]),
        api_base=os.environ.get("WARRANTED_LIVE_API_BASE"),
        max_tokens=1024,
        timeout_seconds=120,
    )
    root = tmp_path / "ledger"
    setup(root, model)
    # An exact instruction: a model without native structured output gets no
    # schema, so the prompt alone must ask for the command object.
    instruction = (
        'Reply with exactly this JSON object and nothing else: {"command": "true"}'
    )
    with Ledger.open(root) as ledger:
        action = WorkerModel(Journal(ledger, episode(model)), model).query(
            [{"role": "user", "content": instruction}]
        )
    result, raw, _ = completion(root)
    assert result.outcome is Outcome.SUCCEEDED
    assert result.usage == {"model": 1}
    tokens = json.loads(raw["tokens.json"])
    assert tokens["prompt_tokens"] > 0 and tokens["completion_tokens"] > 0
    assert action["extra"]["actions"] == [{"command": "true"}]


@pytest.mark.parametrize("model", ["openai/fake-model", "anthropic/claude-test"])
@pytest.mark.parametrize(
    "mode,status", [("close", 200), ("partial", 200), ("sleep", 200), (None, 504)]
)
def test_a_lost_reply_stays_unknown_and_is_never_resent(
    tmp_path, key_file, model, mode, status
):
    with provider(
        status,
        b'{"error": {"message": "gateway"}}' if status != 200 else None,
        mode=mode,
    ) as (url, calls):
        model = client(key_file, url, model, timeout_seconds=1)
        root = tmp_path / "ledger"
        setup(root, model)
        with pytest.raises(ConnectionError, match="unknown"):
            query(root, model)
        with pytest.raises(UnknownOutcome):
            query(root, model)
    assert len(calls) == 1
    with Ledger.open(root) as ledger:
        assert ledger.operations()[0].state == "unknown"
        balance = ledger.accounting()["model"]
        assert (balance.spent, balance.reserved) == (0, 1)


@pytest.mark.parametrize("model", ["openai/fake-model", "anthropic/claude-test"])
@pytest.mark.parametrize("status", [502, 503])
def test_a_bad_gateway_or_unavailable_reply_is_known(tmp_path, key_file, model, status):
    with provider(status, b'{"error": {"message": "down"}}') as (url, calls):
        model = client(key_file, url, model)
        root = tmp_path / "ledger"
        setup(root, model)
        with pytest.raises(RuntimeError, match="model attempt"):
            query(root, model)
    assert len(calls) == 1
    result, _, balance = completion(root)
    assert result.outcome is Outcome.INFRASTRUCTURE_FAILURE
    assert (balance.spent, balance.reserved) == (1, 0)


def test_an_allowed_parameter_reaches_the_wire(tmp_path, key_file):
    with provider() as (url, calls):
        model = client(key_file, url, parameters={"temperature": 0.25, "seed": 7})
        root = tmp_path / "ledger"
        setup(root, model)
        query(root, model)
    assert (calls[0][2]["temperature"], calls[0][2]["seed"]) == (0.25, 7)


@pytest.mark.parametrize("model", ["openai/fake-model", "anthropic/claude-test"])
def test_echoed_key_material_is_redacted(tmp_path, key_file, model):
    body = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": f"bad key {KEY}, or sk-...{KEY[-4:]} ({KEY[:8]}...)",
            },
        }
    ).encode()
    with provider(401, body) as (url, _):
        model = client(key_file, url, model)
        root = tmp_path / "ledger"
        setup(root, model)
        with pytest.raises(RuntimeError, match="model attempt"):
            query(root, model)
    _, raw, _ = completion(root)
    record = raw["litellm-response.json"]
    assert b"[api key]" in record
    assert KEY[-4:].encode() not in record
    assert KEY[:8].encode() not in record
    assert KEY[-4:].encode() not in stored(root)


def test_fragments_are_redacted_only_where_the_key_was_masked():
    from warranted._litellm import _redact

    masked = f"sk-...{KEY[-4:]} and {KEY[:8]}... and ****{KEY[-4:]}".encode()
    assert KEY[-4:].encode() not in _redact(masked, KEY)
    assert KEY[:8].encode() not in _redact(masked, KEY)
    # The same characters by chance elsewhere, e.g. in a signature, stay as recorded.
    chance = f'{{"signature": "AAAA{KEY[-4:]}BBBB{KEY[:8]}CCCC"}}'.encode()
    assert _redact(chance, KEY) == chance
    assert b"[api key]" in _redact(f"bad key {KEY}".encode(), KEY)


@contextmanager
def recording_proxy():
    """A loopback proxy that only counts the connections it is offered."""
    seen = []
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.1)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            seen.append(connection.recv(4096))
            connection.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}", seen
    finally:
        stop.set()
        thread.join()
        listener.close()


def test_the_child_fetches_nothing_but_the_one_request(tmp_path, key_file, monkeypatch):
    with recording_proxy() as (proxy, seen), provider() as (url, calls):
        proxied = {
            name: proxy
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
        }
        monkeypatch.setattr(
            adapter_module,
            "ENV",
            {**adapter_module.ENV, **proxied, "NO_PROXY": "127.0.0.1"},
        )
        adapter_module._probe.cache_clear()
        for index, name in enumerate(
            ("openai/fake-model", "anthropic/claude-opus-5-5")
        ):
            model = client(key_file, url, name)
            root = tmp_path / f"ledger-{index}"
            setup(root, model)
            query(root, model)
        adapter_module._probe.cache_clear()
    assert len(calls) == 2
    assert "anthropic-beta" in calls[1][1]  # the path that used to fetch headers
    assert seen == []


def test_only_an_unmapped_model_counts_as_not_native(monkeypatch):
    # A broken model map is an error at construction, never a silent "not native".
    monkeypatch.setattr(
        adapter_module,
        "_PROBE",
        adapter_module._PROBE.replace(
            "info = litellm.get_model_info(config['model'])",
            "raise RuntimeError('model map unreadable')",
        ),
    )
    adapter_module._probe.cache_clear()
    try:
        with pytest.raises(ValueError, match="litellm is unavailable"):
            adapter_module._probe("openai/fake-model")
    finally:
        adapter_module._probe.cache_clear()


def test_verified_models_are_keyed_by_model_and_endpoint(key_file, monkeypatch):
    model = client(key_file, "http://127.0.0.1:9")
    assert model.verification_key == "openai/fake-model@http://127.0.0.1:9/v1"
    monkeypatch.setattr(
        LiteLLMChatCompletions,
        "verified_models",
        frozenset({model.verification_key}),
    )
    assert model.reserved_units == {"model", "prompt_tokens", "completion_tokens"}
    other = client(key_file, "http://127.0.0.1:10")
    assert other.reserved_units == {"model"}
