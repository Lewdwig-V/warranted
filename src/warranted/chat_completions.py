"""One local OpenAI-compatible HTTP attempt, without SDK retries.

The model unit counts attempts. For a verified model, the adapter also reserves a
bound on prompt and completion tokens and settles the reported counts; otherwise
token counts are only retained as evidence. Money is never a unit. A lost response
has no reconciliation endpoint and stays unknown.
The host must trust the local server and pin its model files outside this API.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
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
# Prompt-token allowance per message, plus one for the generation prompt, covering
# chat-template special tokens beyond the wire body's byte length.
MESSAGE_MARGIN = 16


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
    # Models whose reported prompt tokens a recorded measurement has shown to stay
    # within the byte bound. Empty until such a measurement exists.
    verified_models: ClassVar[frozenset[str]] = frozenset()
    base_url: str
    model: str
    max_tokens: int = 256
    timeout_seconds: int = 120
    seed: int = 0
    # The installed model's digest; required before a local model can be verified.
    model_digest: str | None = None

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
        if self.model_digest is not None and (
            type(self.model_digest) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", self.model_digest)
        ):
            raise ValueError("model digest must be a lowercase SHA-256 digest")

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
    def verification_key(self) -> str | None:
        """A tag can be repointed, so only a pinned installed digest is verifiable."""
        if self.model_digest is None:
            return None
        return f"{self.model}@{self.model_digest}"

    @property
    def token_bounded(self) -> bool:
        key = self.verification_key
        return key is not None and key in self.verified_models

    def _model_changed(self) -> str | None:
        """Why the served model is not the verified one, or None if it is."""
        try:
            with http_response(
                self.base_url.removesuffix("/v1") + "/api/tags", None, 10
            ) as response:
                body = response.read(MAX_BYTES + 1)
                status = response.status
            if status != 200 or len(body) > MAX_BYTES:
                return f"model metadata HTTP status {status}"
            models = json.loads(body, object_pairs_hook=_json_object)["models"]
            digests = [m.get("digest") for m in models if m.get("name") == self.model]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            return f"model metadata unavailable: {error}"
        if digests != [self.model_digest]:
            return "installed model digest differs from the verified model"
        return None

    @property
    def reserved_units(self) -> frozenset[str]:
        if self.token_bounded:
            return frozenset({"model", "prompt_tokens", "completion_tokens"})
        return frozenset({"model"})

    def _wire(self, payload: bytes) -> tuple[bytes, int]:
        """The exact request body and its message count; raises on invalid input."""
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
        return wire, len(messages)

    def reservation(self, payload: bytes) -> dict[str, int]:
        """An upper bound on this request's charge, computed before dispatch."""
        if not self.token_bounded:
            return {"model": 1}
        wire, messages = self._wire(payload)
        if len(wire) > MAX_BYTES:  # never sent
            return {"model": 1, "prompt_tokens": 0, "completion_tokens": 0}
        return {
            "model": 1,
            "prompt_tokens": len(wire) + MESSAGE_MARGIN * (messages + 1),
            "completion_tokens": self.max_tokens,
        }

    def _usage(self, model: int, tokens: Mapping[str, int] | None = None):
        """Settled usage: token units only when reserved, zero unless given."""
        if not self.token_bounded:
            return {"model": model}
        tokens = tokens or {}
        return {
            "model": model,
            "prompt_tokens": tokens.get("prompt_tokens", 0),
            "completion_tokens": tokens.get("completion_tokens", 0),
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
                    "token_bound": {
                        "model_digest": self.model_digest,
                        "verified": self.token_bounded,
                        "message_margin": MESSAGE_MARGIN,
                    },
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
        wire, _ = self._wire(payload)
        reserved = self.reservation(payload)
        if len(wire) > MAX_BYTES:
            return AttemptResult(
                Result(Outcome.FAILED, 1, self._usage(0), 0),
                {"diagnostic": b"model request exceeds byte limit"},
            )
        if self.token_bounded:
            # The bound was measured for one installed model; never send to another.
            changed = self._model_changed()
            if changed is not None:
                return AttemptResult(
                    Result(Outcome.INFRASTRUCTURE_FAILURE, None, self._usage(0), 0),
                    {"diagnostic": changed.encode()},
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
        # Unmeasured usage is charged at the bound, never as zero.
        charged = self._usage(1, reserved)
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
            charged = self._usage(1, usage)
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
                choice.get("finish_reason") == "length"
                and type(message) is dict
                and message.get("content") is None
            ):
                message = {**message, "content": ""}
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
        return AttemptResult(Result(outcome, exit_code, charged, elapsed), raw)
