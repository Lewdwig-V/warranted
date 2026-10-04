"""One hosted-model attempt through litellm, in a killable child process.

litellm reaches OpenAI-compatible APIs and the Anthropic API. It runs offline
(the bundled model map), silent (no callbacks), and without retries at either
layer. The key travels over stdin and is never recorded; the host records the
exact call without it and litellm's normalized response. Design:
docs/proposals/m7-model-providers.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass, field
from functools import cache
from pathlib import Path
from time import monotonic_ns
from typing import ClassVar
from urllib.parse import urlsplit

from warranted._acceptance import _encode
from warranted._chat_completions import MAX_BYTES, MESSAGE_MARGIN
from warranted._containers import SandboxFailure, _run
from warranted._ledger import (
    ArtifactRef,
    Outcome,
    Request,
    Result,
    Snapshot,
    _json_object,
)
from warranted._openrouter import command_format
from warranted._worker import AttemptResult

# The child's whole environment: litellm's bundled copies of every file it would
# otherwise fetch from GitHub (model map, Anthropic beta headers, blog posts,
# autorouter presets, policy templates), no .env loading, and no inherited
# provider keys, proxies, or CA settings. `python -I` ignores PYTHON* variables.
ENV = {
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS": "True",
    "LITELLM_LOCAL_BLOG_POSTS": "True",
    "LITELLM_LOCAL_AUTOROUTER_PRESETS": "True",
    "LITELLM_LOCAL_POLICY_TEMPLATES": "True",
    "LITELLM_MODE": "PRODUCTION",
    "LITELLM_TELEMETRY": "False",
}
# Interpreter start and litellm import, on top of the request's own timeout.
STARTUP_SECONDS = 20
OUTPUT_LIMIT = 4 * MAX_BYTES

# litellm prints notices on stdout, so the one result goes to the saved stream.
_PRELUDE = """
import json, sys
config = json.load(sys.stdin)
out, sys.stdout = sys.stdout, sys.stderr
import httpx, litellm
litellm.telemetry = False
litellm.suppress_debug_info = True
litellm.callbacks, litellm.success_callback, litellm.failure_callback = [], [], []
"""

_PROBE = (
    _PRELUDE
    + """
from importlib.metadata import version
try:
    info = litellm.get_model_info(config['model'])
    native = info.get('supports_native_structured_output') is True
except litellm.ModelNotMappedError:
    native = False  # an unmapped model: send no response_format
out.write(json.dumps({
    'versions': {name: version(name) for name in ('litellm', 'openai', 'httpx')},
    'native': native,
}))
"""
)

_CALL = (
    _PRELUDE
    + """
result = {'response': None, 'headers': None, 'status': None, 'error': None,
          'lost': False}
try:
    response = litellm.completion(**config['request'], api_key=config['key'])
    result['response'] = response.model_dump(mode='json')
    result['headers'] = (response._hidden_params or {}).get('additional_headers')
except Exception as error:
    # litellm reports a dropped connection as a 500; only the cause chain shows
    # that no response arrived.
    chain, stack = [], [error]
    while stack:
        item = stack.pop()
        if item is not None and all(item is not seen for seen in chain):
            chain.append(item)
            stack += [item.__cause__, item.__context__]
    lost = (litellm.Timeout, httpx.TransportError, OSError)
    headers = getattr(error, 'litellm_response_headers', None)
    result.update(
        lost=any(isinstance(item, lost) for item in chain),
        status=getattr(error, 'status_code', None),
        headers=None if headers is None else dict(headers),
        error={'class': type(error).__name__, 'message': str(error)},
    )
out.write(json.dumps(result, default=str))
"""
)

# The only extra `completion` arguments: sampling and model behaviour. Anything
# else could change retries, routing, the endpoint, or what is recorded.
PARAMETERS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "seed",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "reasoning_effort",
        "thinking",
        "user",
    }
)
# Characters a key may hold, so redacting it never breaks the recorded JSON.
KEY_PATTERN = re.compile(r"[A-Za-z0-9._~+/=-]+")


def _child(script: str, config: bytes, seconds: int) -> bytes:
    try:
        result = _run(
            ["-I", "-c", script],
            config,
            executable=sys.executable,
            seconds=seconds,
            output_limit=OUTPUT_LIMIT,
            env=dict(ENV),
        )
    except SandboxFailure:
        raise ConnectionError("litellm attempt interrupted; outcome unknown") from None
    if result.returncode:
        raise ConnectionError("litellm attempt failed; outcome unknown")
    return result.stdout


def _redact(data: bytes, key: str) -> bytes:
    """The key, and its usual masked fragments, replaced.

    A fragment (first 8 or last 4 characters) is replaced only where a provider
    masked the key around it (`sk-...abcd`, `sk-proj-****abcd`, `sk-abcdefgh...`),
    so a chance match elsewhere in the response is left as recorded.
    """
    data = data.replace(key.encode(), b"[api key]")
    if len(key) >= 12:  # a short key's fragments would hide unrelated text
        head, tail = re.escape(key[:8].encode()), re.escape(key[-4:].encode())
        data = re.sub(rb"(?<=[*.])" + tail, b"[api key]", data)
        data = re.sub(head + rb"(?=[*.])", b"[api key]", data)
    return data


@cache
def _probe(model: str) -> dict:
    """The child's package versions and whether litellm knows native structured
    output for the model. An error other than an unmapped model propagates."""
    try:
        return json.loads(_child(_PROBE, _encode({"model": model}), STARTUP_SECONDS))
    except (ConnectionError, ValueError) as error:
        raise ValueError(f"litellm is unavailable: {error}") from None


@dataclass(frozen=True)
class LiteLLMChatCompletions:
    provider: ClassVar[str] = "litellm"
    adapter_name: ClassVar[str] = "litellm-chat-completions"
    # Models whose reported prompt tokens a recorded measurement has shown to stay
    # within the byte bound. Empty until such a measurement exists.
    verified_models: ClassVar[frozenset[str]] = frozenset()
    model: str
    _: KW_ONLY
    api_key_file: Path
    max_tokens: int
    timeout_seconds: int
    api_base: str | None = None
    parameters: Mapping = field(default_factory=dict, hash=False)
    # Read from the child at construction and pinned.
    versions: Mapping = field(init=False, hash=False)
    structured_output: bool = field(init=False)

    def __post_init__(self):
        if (
            type(self.model) is not str
            or "/" not in self.model.strip("/")
            or self.model != self.model.strip()
        ):
            raise ValueError("model must be a litellm model string: provider/model")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 131072:
            raise ValueError("max_tokens must be between 1 and 131072")
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 600
        ):
            raise ValueError("request timeout must be between 1 and 600 seconds")
        if self.api_base is not None:
            url = urlsplit(self.api_base) if type(self.api_base) is str else None
            if (
                url is None
                or url.scheme not in ("http", "https")
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise ValueError("api_base must be an http(s) URL without credentials")
        parameters = self.parameters
        if not isinstance(parameters, Mapping):
            raise ValueError("parameters must be a table")
        refused = sorted(str(name) for name in parameters if name not in PARAMETERS)
        if refused:
            raise ValueError(
                f"parameters {refused} are not allowed; only {sorted(PARAMETERS)}"
            )
        try:
            copied = json.loads(_encode(dict(parameters)))
        except (TypeError, ValueError):
            raise ValueError("parameters must be JSON values") from None
        if copied != dict(parameters):
            raise ValueError("parameters must be JSON values")
        object.__setattr__(self, "api_key_file", Path(self.api_key_file))
        object.__setattr__(self, "parameters", copied)
        self._key()
        probe = _probe(self.model)
        object.__setattr__(self, "versions", probe["versions"])
        object.__setattr__(self, "structured_output", probe["native"])

    def _key(self) -> str:
        """The key, read now; never stored on the adapter or recorded."""
        try:
            if self.api_key_file.stat().st_mode & 0o077:
                raise ValueError(
                    f"API key file {self.api_key_file} must be readable only by "
                    "its owner"
                )
            key = self.api_key_file.read_text().strip()
        except (OSError, UnicodeError) as error:
            raise ValueError(f"API key file is unavailable: {error}") from None
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"API key file {self.api_key_file} must hold one key of "
                "letters, digits, and ._~+/=-"
            )
        return key

    @property
    def verification_key(self) -> str:
        """A measurement holds for one model at one endpoint."""
        return f"{self.model}@{self.api_base or ''}"

    @property
    def token_bounded(self) -> bool:
        return self.verification_key in self.verified_models

    @property
    def reserved_units(self) -> frozenset[str]:
        if self.token_bounded:
            return frozenset({"model", "prompt_tokens", "completion_tokens"})
        return frozenset({"model"})

    def _request(self, payload: bytes) -> tuple[dict, int]:
        """The exact litellm call without the key, and its message count."""
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
        request = {
            **self.parameters,
            "model": self.model,
            # mini's host-only extra/actions metadata is never part of the prompt.
            "messages": [{k: m[k] for k in ("role", "content")} for m in messages],
            "max_tokens": self.max_tokens,
            "timeout": self.timeout_seconds,
            "stream": False,
            "num_retries": 0,
            "max_retries": 0,
        }
        if self.api_base is not None:
            request["api_base"] = self.api_base
        if self.structured_output:
            request["response_format"] = command_format()
        return request, len(messages)

    def reservation(self, payload: bytes) -> dict[str, int]:
        """An upper bound on this request's charge, computed before dispatch."""
        if not self.token_bounded:
            return {"model": 1}
        request, messages = self._request(payload)
        wire = _encode(request)
        if len(wire) > MAX_BYTES:  # never sent
            return {"model": 1, "prompt_tokens": 0, "completion_tokens": 0}
        return {
            "model": 1,
            "prompt_tokens": len(wire) + MESSAGE_MARGIN * (messages + 1),
            "completion_tokens": self.max_tokens,
        }

    def _usage(self, model: int, tokens: Mapping[str, int] | None = None):
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
                    "model": self.model,
                    "versions": self.versions,
                    "api_base": self.api_base,
                    "max_tokens": self.max_tokens,
                    "timeout_seconds": self.timeout_seconds,
                    "parameters": self.parameters,
                    "structured_output": self.structured_output,
                    "max_bytes": MAX_BYTES,
                    "token_bound": {
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
        return "litellm/" + hashlib.sha256(self.snapshot.data).hexdigest()

    def __call__(self, request: Request, payload: bytes) -> AttemptResult:
        pinned = request.context.snapshots.get("model-api")
        expected = ArtifactRef(
            hashlib.sha256(self.snapshot.data).hexdigest(), len(self.snapshot.data)
        )
        cited = any(
            name.endswith("/model-api.json") and ref == expected
            for name, ref in request.origin.inputs.items()
        )
        if (
            request.origin.kind != "model"
            or request.origin.producer != self.service_id
            or not (cited or (pinned is not None and pinned.artifact == expected))
        ):
            raise ValueError(
                "model service configuration differs from the pinned request"
            )
        call, _ = self._request(payload)
        wire = _encode(call)
        reserved = self.reservation(payload)
        if len(wire) > MAX_BYTES:
            return AttemptResult(
                Result(Outcome.FAILED, 1, self._usage(0), 0),
                {"diagnostic": b"model request exceeds byte limit"},
            )
        try:
            key = self._key()
        except ValueError as error:  # nothing was sent
            return AttemptResult(
                Result(Outcome.INFRASTRUCTURE_FAILURE, None, self._usage(0), 0),
                {"diagnostic": str(error).encode()},
            )
        started = monotonic_ns()
        # An interrupted child raises, leaving the journal reservation open.
        stdout = _child(
            _CALL,
            _encode({"key": key, "request": call}),
            self.timeout_seconds + STARTUP_SECONDS,
        )
        elapsed = monotonic_ns() - started
        try:
            value = json.loads(stdout, object_pairs_hook=_json_object)
            lost = value["lost"]
        except (ValueError, TypeError, KeyError):
            raise ConnectionError(
                "unreadable litellm result; outcome unknown"
            ) from None
        if lost is not False:
            # The journal cannot record an unsettled attempt; the redacted error
            # class and status travel in the exception instead.
            error = value.get("error") or {}
            detail = _redact(
                f"{error.get('class')}, status {value.get('status')}".encode(), key
            ).decode()
            raise ConnectionError(f"litellm transport lost ({detail}); outcome unknown")
        raw = {
            "litellm-request.json": wire,
            # A provider error may echo the key or a masked form of it.
            "litellm-response.json": _redact(stdout, key),
        }
        outcome, exit_code = Outcome.INFRASTRUCTURE_FAILURE, None
        charged = self._usage(1, reserved)  # unmeasured: charged at the bound
        try:
            if value.get("error") is not None:
                error = value["error"]
                raise ValueError(
                    f"litellm {error.get('class')} (status {value.get('status')})"
                )
            response = value.get("response")
            if type(response) is not dict:
                raise ValueError("expected a litellm response")
            usage = response.get("usage")
            keys = ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(usage) is not dict or any(
                type(usage.get(name)) is not int or usage[name] < 0 for name in keys
            ):
                raise ValueError("missing or invalid token usage")
            # litellm fills absent usage with zeros; a sent prompt is never empty.
            if usage["prompt_tokens"] == 0:
                raise ValueError("missing token usage")
            if (
                usage["total_tokens"]
                != usage["prompt_tokens"] + usage["completion_tokens"]
            ):
                raise ValueError("inconsistent token usage")
            raw["tokens.json"] = _encode({name: usage[name] for name in keys})
            charged = self._usage(1, usage)
            if usage["completion_tokens"] > self.max_tokens:
                raise ValueError(
                    "reported generation exceeds the requested token limit"
                )
            choices = response.get("choices")
            if (
                type(choices) is not list
                or len(choices) != 1
                or type(choices[0]) is not dict
                or type(choices[0].get("message")) is not dict
            ):
                raise ValueError("expected one completion choice")
            finish = choices[0].get("finish_reason")
            message = choices[0]["message"]
            refusal = message.get("refusal") or (
                message.get("provider_specific_fields") or {}
            ).get("refusal")
            content = message.get("content")
            if content is None and (finish in ("length", "content_filter") or refusal):
                content = ""
            if (
                message.get("role") != "assistant"
                or type(content) is not str
                or message.get("tool_calls")
                or message.get("function_call")
            ):
                raise ValueError("expected assistant text")
            # Thinking and reasoning stay in the recorded response, never here.
            raw["response"] = content.encode()
            if refusal or finish == "content_filter":
                raw["refusal"] = _encode(refusal or finish)
                outcome, exit_code = Outcome.FAILED, 1
            elif finish == "length":
                outcome, exit_code = Outcome.FAILED, 1
            elif finish == "stop":
                outcome, exit_code = Outcome.SUCCEEDED, 0
            else:
                raise ValueError("unsupported completion finish reason")
        except (ValueError, UnicodeError, AttributeError) as error:
            raw["error"] = str(error).encode()
        return AttemptResult(Result(outcome, exit_code, charged, elapsed), raw)
