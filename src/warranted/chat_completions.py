"""One local OpenAI-compatible HTTP attempt, without SDK retries.

The model unit counts attempts, not tokens or money. Token counts are retained
separately. A lost response has no reconciliation endpoint and stays unknown.
The host must trust the local server and pin its model files outside this API.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import HTTPConnection
from socket import SHUT_RDWR
from threading import Event, Timer
from time import monotonic_ns
from typing import ClassVar
from urllib.parse import urlsplit

from warranted.acceptance import _encode
from warranted.ledger import (
    ArtifactRef,
    Outcome,
    Request,
    Result,
    Snapshot,
    _json_object,
)
from warranted.worker import AttemptResult

MAX_BYTES = 2 * 1024 * 1024


@contextmanager
def http_response(url: str, data: bytes | None, timeout_seconds: float):
    """One direct HTTP request with a deadline through headers and body reads."""
    target = urlsplit(url)
    connection = HTTPConnection(target.hostname, target.port, timeout=timeout_seconds)
    started = monotonic_ns()
    try:
        # Callers supply only the validated numeric loopback address; no DNS lookup.
        connection.connect()
        socket = connection.sock
        expired = Event()

        def expire():
            expired.set()
            try:
                socket.shutdown(SHUT_RDWR)
            except OSError:
                pass  # The peer may already have closed this exact connection.

        remaining = timeout_seconds - (monotonic_ns() - started) / 1e9
        if remaining <= 0:
            raise TimeoutError("HTTP request deadline exceeded")
        timer = Timer(remaining, expire)
        timer.start()
        try:
            # HTTPConnection has no proxy handling, redirects, or automatic retries.
            connection.request(
                "GET" if data is None else "POST",
                target.path,
                body=data,
                headers={"Content-Type": "application/json"},
            )
            with connection.getresponse() as response:
                yield response
        finally:
            timer.cancel()
            timer.join()
            if expired.is_set() or (monotonic_ns() - started) / 1e9 >= timeout_seconds:
                raise TimeoutError("HTTP request deadline exceeded")
    finally:
        connection.close()


@dataclass(frozen=True)
class LocalChatCompletions:
    provider: ClassVar[str] = "ollama"
    adapter_name: ClassVar[str] = "local-chat-completions"
    base_url: str
    model: str
    max_tokens: int = 256
    timeout_seconds: int = 120
    seed: int = 0

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or not url.port
            or url.username is not None
            or url.password is not None
            or url.path != "/v1"
            or url.query
            or url.fragment
        ):
            raise ValueError("use http://127.0.0.1:<port>/v1 for the local endpoint")
        self._validate_parameters()

    def _validate_parameters(self):
        if type(self.model) is not str or not self.model.strip():
            raise ValueError("an explicit installed model is required")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 8192:
            raise ValueError("max_tokens must be between 1 and 8192")
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 300
        ):
            raise ValueError("request timeout must be between 1 and 300 seconds")
        if type(self.seed) is not int or not 0 <= self.seed < 2**31:
            raise ValueError("seed must be a nonnegative 32-bit signed integer")

    @property
    def parameters(self) -> dict:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "seed": self.seed,
            "temperature": 0,
            "stream": False,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
        }

    @property
    def snapshot(self) -> Snapshot:
        return Snapshot(
            _encode(
                {
                    "adapter": self.adapter_name + "-v1",
                    "base_url": self.base_url,
                    "timeout_seconds": self.timeout_seconds,
                    "max_bytes": MAX_BYTES,
                    "parameters": self.parameters,
                }
            ),
            "warranted-" + self.adapter_name,
            "1",
        )

    @property
    def service_id(self) -> str:
        return self.adapter_name + "/" + hashlib.sha256(self.snapshot.data).hexdigest()

    def _post(self, wire: bytes) -> tuple[int, bytes]:
        with http_response(
            self.base_url + "/chat/completions", wire, self.timeout_seconds
        ) as response:
            body = response.read(MAX_BYTES + 1)
            status = response.status
            length = response.headers.get("Content-Length")
        if len(body) > MAX_BYTES:
            raise ValueError("model response exceeds byte limit; outcome unknown")
        if length is not None and int(length) != len(body):
            raise ValueError("incomplete model response; outcome unknown")
        return status, body

    def __call__(self, request: Request, payload: bytes) -> AttemptResult:
        pinned = request.context.snapshots.get("model-api")
        expected = ArtifactRef(
            hashlib.sha256(self.snapshot.data).hexdigest(), len(self.snapshot.data)
        )
        if (
            request.origin.kind != "model"
            or request.origin.producer != self.service_id
            or pinned is None
            or pinned.artifact != expected
        ):
            raise ValueError(
                "model service configuration differs from the pinned request"
            )
        value = json.loads(payload, object_pairs_hook=_json_object)
        if type(value) is not dict or set(value) != {"messages"}:
            raise ValueError("expected only model messages")
        messages = value["messages"]
        if (
            type(messages) is not list
            or not messages
            or any(
                type(message) is not dict
                or message.get("role") not in ("system", "user", "assistant")
                or type(message.get("content")) is not str
                for message in messages
            )
        ):
            raise ValueError("expected text chat messages")
        # mini's host-only extra/actions metadata is never part of the wire prompt.
        wire = _encode(
            {
                **self.parameters,
                "messages": [
                    {key: message[key] for key in ("role", "content")}
                    for message in messages
                ],
            }
        )
        if len(wire) > MAX_BYTES:
            return AttemptResult(
                Result(Outcome.FAILED, 1, {"model": 0}, 0),
                {"diagnostic": b"model request exceeds byte limit"},
            )
        started = monotonic_ns()
        # Transport/read exceptions deliberately leave the journal reservation open.
        status, body = self._post(wire)
        elapsed = monotonic_ns() - started
        raw = {
            "http-request.json": wire,
            "http-response": body,
            "http-status.json": _encode(status),
        }
        outcome, exit_code = Outcome.INFRASTRUCTURE_FAILURE, None
        try:
            if status != 200:
                raise ValueError(f"model HTTP status {status}")
            value = json.loads(body, object_pairs_hook=_json_object)
            if type(value) is not dict or value.get("model") != self.model:
                raise ValueError("response model differs from the requested model")
            usage = value.get("usage")
            keys = ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(usage) is not dict or any(
                type(usage.get(key)) is not int or usage[key] < 0 for key in keys
            ):
                raise ValueError("missing or invalid token usage")
            if (
                usage["total_tokens"]
                != usage["prompt_tokens"] + usage["completion_tokens"]
            ):
                raise ValueError("inconsistent token usage")
            raw["tokens.json"] = _encode({key: usage[key] for key in keys})
            if usage["completion_tokens"] > self.max_tokens:
                raise ValueError(
                    "reported generation exceeds the requested token limit"
                )
            choices = value.get("choices")
            if (
                type(choices) is not list
                or len(choices) != 1
                or type(choices[0]) is not dict
            ):
                raise ValueError("expected one completion choice")
            choice = choices[0]
            message = choice.get("message")
            if (
                type(message) is not dict
                or message.get("role") != "assistant"
                or type(message.get("content")) is not str
                or message.get("tool_calls")
                or message.get("function_call")
                or message.get("refusal")
            ):
                raise ValueError("expected assistant text")
            raw["response"] = message["content"].encode()
            if choice.get("finish_reason") == "stop":
                outcome, exit_code = Outcome.SUCCEEDED, 0
            elif choice.get("finish_reason") == "length":
                outcome, exit_code = Outcome.FAILED, 1
            else:
                raise ValueError("unsupported completion finish reason")
        except (ValueError, UnicodeError) as error:
            raw["error"] = str(error).encode()
        return AttemptResult(Result(outcome, exit_code, {"model": 1}, elapsed), raw)
