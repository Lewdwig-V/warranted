"""One pinned OpenRouter worker request, using a limited host-only API key."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from time import monotonic_ns
from typing import ClassVar

from warranted.acceptance import _encode
from warranted.chat_completions import MAX_BYTES, LocalChatCompletions
from warranted.containers import SandboxFailure, _run
from warranted.ledger import Ledger, Origin, Outcome, Result, _json_object
from warranted.worker import AttemptResult, UnknownOutcome, record_once

# A child process gives DNS, TLS, headers, and body reads one killable deadline.
# Credentials travel over stdin, never argv, captured requests, or worker files.
_HTTPS = """
import base64, http.client, json, sys
try:
    config = json.load(sys.stdin)
    connection = http.client.HTTPSConnection('openrouter.ai', timeout=config['seconds'])
    headers = {'Content-Type': 'application/json'}
    if config['key']:
        headers['Authorization'] = 'Bearer ' + config['key']
    data = None if config['data'] is None else base64.b64decode(config['data'])
    connection.request('GET' if data is None else 'POST', config['path'], data, headers)
    with connection.getresponse() as response:
        body = response.read(config['max_bytes'] + 1)
        length = response.headers.get('Content-Length')
        if len(body) > config['max_bytes']:
            raise ValueError('response too large')
        if length is not None and int(length) != len(body):
            raise ValueError('incomplete response')
        print(json.dumps({'status': response.status,
                          'body': base64.b64encode(body).decode()}))
    connection.close()
except Exception as error:
    print(type(error).__name__, file=sys.stderr)
    sys.exit(1)
"""


def _request(path: str, data: bytes | None, key: str | None, seconds: int):
    config = _encode(
        {
            "path": path,
            "data": None if data is None else base64.b64encode(data).decode(),
            "key": key,
            "seconds": seconds,
            "max_bytes": MAX_BYTES,
        }
    )
    try:
        result = _run(
            ["-I", "-c", _HTTPS],
            config,
            executable=sys.executable,
            seconds=seconds,
            output_limit=2 * MAX_BYTES + 4096,
        )
    except SandboxFailure:
        raise ConnectionError(
            "OpenRouter transport interrupted; outcome unknown"
        ) from None
    if result.returncode:
        raise ConnectionError("OpenRouter transport failed; outcome unknown")
    response = json.loads(result.stdout, object_pairs_hook=_json_object)
    return response["status"], base64.b64decode(response["body"], validate=True)


def command_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "worker_command",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }


def _usd(value) -> Decimal:
    if type(value) not in (int, Decimal):
        raise ValueError("missing or invalid USD amount")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError("missing or invalid USD amount")
    return amount


class _CredentialChanged(ValueError):
    """The pinned credential is unavailable before inference dispatch."""


@dataclass(frozen=True)
class OpenRouterChatCompletions(LocalChatCompletions):
    provider: ClassVar[str] = "openrouter"
    adapter_name: ClassVar[str] = "openrouter-chat-completions"
    provider_tag: ClassVar[str] = "inference-net/fp4"
    provider_name: ClassVar[str] = "InferenceNet"
    key_file: Path | None = None
    key_sha256: str | None = None
    ledger_root: Path | None = None

    def __post_init__(self):
        if self.base_url != "https://openrouter.ai/api/v1":
            raise ValueError("use the fixed HTTPS OpenRouter endpoint")
        self._validate_parameters()
        if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", self.model):
            raise ValueError("use an explicit OpenRouter model ID")
        if not isinstance(self.key_file, Path):
            raise ValueError("a host-only API key file is required")
        if self.key_sha256 is not None and (
            type(self.key_sha256) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", self.key_sha256)
        ):
            raise ValueError("invalid pinned key digest")

    def _key(self) -> str:
        key = self.key_file.read_text().strip()
        if not re.fullmatch(r"sk-or-v1-[a-f0-9]{64}", key):
            raise ValueError("invalid OpenRouter key file")
        return key

    @property
    def parameters(self):
        parameters = super().parameters
        del parameters["reasoning_effort"]
        return {
            **parameters,
            "reasoning": {"enabled": True},
            "response_format": command_format(),
            "provider": {
                "order": [self.provider_tag],
                "allow_fallbacks": False,
                "require_parameters": True,
                "max_price": {"prompt": "0.15", "completion": "0.5", "request": "0"},
            },
        }

    def _post(self, wire):
        try:
            key = self._key()
        except (OSError, ValueError):
            raise _CredentialChanged("OpenRouter key file is unavailable") from None
        if hashlib.sha256(key.encode()).hexdigest() != self.key_sha256:
            raise _CredentialChanged("OpenRouter key changed after preflight")
        return _request("/api/v1/chat/completions", wire, key, self.timeout_seconds)

    def runtime(self, raw: dict[str, bytes] | None = None) -> bytes:
        key = self._key()
        if raw is None:
            raw = {}

        def read(path, credential=None):
            status, body = _request(path, None, credential, 10)
            name = path.rsplit("/", 1)[-1]
            raw[f"api/{name}-status.json"] = _encode(status)
            raw[f"api/{name}-response"] = body
            if status != 200:
                raise ValueError(f"OpenRouter metadata HTTP status {status}")
            return json.loads(
                body, parse_float=Decimal, object_pairs_hook=_json_object
            )["data"]

        limits = read("/api/v1/key", key)
        limit, remaining = _usd(limits["limit"]), _usd(limits["limit_remaining"])
        if limit <= 0 or remaining <= 0 or remaining > limit or limits["limit_reset"]:
            raise ValueError("OpenRouter requires available, non-resetting key credit")
        metadata = read(f"/api/v1/models/{self.model}/endpoints")
        matches = [
            item
            for item in metadata["endpoints"]
            if item.get("tag") == self.provider_tag
        ]
        if metadata["id"] != self.model or len(matches) != 1:
            raise ValueError("pinned OpenRouter model endpoint is unavailable")
        endpoint = matches[0]
        required = {
            "seed",
            "temperature",
            "max_tokens",
            "reasoning",
            "response_format",
            "structured_outputs",
        }
        if (
            endpoint["provider_name"] != self.provider_name
            or endpoint["model_id"] != self.model
            or endpoint["status"] != 0
            or not required <= set(endpoint["supported_parameters"])
            or endpoint["max_completion_tokens"] < self.max_tokens
        ):
            raise ValueError("OpenRouter endpoint cannot honor the pinned request")
        pricing = endpoint["pricing"]
        for kind, ceiling in (("prompt", "0.00000015"), ("completion", "0.0000005")):
            if _usd(Decimal(pricing[kind])) > Decimal(ceiling):
                raise ValueError("OpenRouter pricing exceeds the pinned ceiling")
        return _encode(
            {
                "model": self.model,
                "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
                "credit_limit_usd": str(limit),
                "limit_reset": None,
                "include_byok_in_limit": limits["include_byok_in_limit"],
                "endpoint": {
                    name: endpoint[name]
                    for name in (
                        "model_id",
                        "provider_name",
                        "tag",
                        "quantization",
                        "context_length",
                        "max_completion_tokens",
                        "max_prompt_tokens",
                    )
                },
                "pricing": {
                    name: str(value)
                    for name, value in pricing.items()
                    if name != "discount"
                },
                "supported_parameters": sorted(endpoint["supported_parameters"]),
            }
        )

    def runtime_failure(
        self, expected: bytes, raw: dict[str, bytes] | None = None
    ) -> AttemptResult | None:
        started = monotonic_ns()
        raw = {} if raw is None else raw
        try:
            raw["runtime.json"] = self.runtime(raw)
            if raw["runtime.json"] != expected:
                raise ValueError("OpenRouter key limit or model endpoint changed")
        except Exception as error:
            raw["diagnostic"] = f"{type(error).__name__}: {error}".encode()
            raw["cost.json"] = _encode({"usd": "0", "scope": "no inference dispatched"})
            return AttemptResult(
                Result(
                    Outcome.INFRASTRUCTURE_FAILURE,
                    None,
                    {"model": 0},
                    0,
                ),
                raw,
            )
        finally:
            raw["preflight.json"] = _encode({"elapsed_ns": monotonic_ns() - started})
        return None

    def __call__(self, request, payload):
        try:
            if self.ledger_root is None:
                raise _CredentialChanged("OpenRouter dispatch requires a pinned ledger")
            with Ledger.open(self.ledger_root) as ledger:
                operation = ledger.lookup(request)
                if operation is None or operation.state != "unknown":
                    raise ValueError("dispatch requires a begun journal operation")
                identity = request.origin.operation_id + "/preflight"
                if any(o.origin.operation_id == identity for o in ledger.history()):
                    raise UnknownOutcome(
                        "preflight already recorded; do not redispatch"
                    )
                expected = ledger.read_artifact(
                    ledger.project.snapshots["runtime"].artifact
                )
                preflight = {}
                failure = self.runtime_failure(expected, preflight)
                record_once(
                    ledger,
                    ledger.start_session(),
                    Origin(
                        identity,
                        "model-preflight",
                        self.service_id,
                        "1",
                        {
                            **request.origin.inputs,
                            "runtime": ledger.project.snapshots["runtime"].artifact,
                        },
                    ),
                    preflight,
                )
            if failure is not None:
                return failure
            client = replace(self, key_sha256=json.loads(expected)["key_sha256"])
            attempt = client._inference(request, payload)
            return AttemptResult(attempt.result, {**preflight, **attempt.raw})
        except _CredentialChanged as error:
            return AttemptResult(
                Result(Outcome.INFRASTRUCTURE_FAILURE, None, {"model": 0}, 0),
                {
                    "diagnostic": str(error).encode(),
                    "cost.json": _encode(
                        {"usd": "0", "scope": "no inference dispatched"}
                    ),
                },
            )

    def _inference(self, request, payload):
        try:
            attempt = super().__call__(request, payload)
        except _CredentialChanged as error:
            return AttemptResult(
                Result(Outcome.INFRASTRUCTURE_FAILURE, None, {"model": 0}, 0),
                {
                    "diagnostic": str(error).encode(),
                    "cost.json": _encode(
                        {"usd": "0", "scope": "no inference dispatched"}
                    ),
                },
            )
        if "http-response" not in attempt.raw:
            return attempt
        raw = dict(attempt.raw)
        try:
            response = json.loads(
                raw["http-response"],
                parse_float=Decimal,
                object_pairs_hook=_json_object,
            )
            if type(response) is not dict or type(response.get("usage")) is not dict:
                raise ValueError("missing usage receipt")
            cost = _usd(response["usage"].get("cost"))
            if response["usage"].get("is_byok") is not False:
                raise ValueError("only OpenRouter credit billing is supported")
            raw["cost.json"] = _encode(
                {"usd": str(cost), "scope": "OpenRouter credits"}
            )
            if (
                response.get("provider") != self.provider_name
                or type(response.get("id")) is not str
                or not response["id"].strip()
            ):
                raise ValueError("missing generation ID or unexpected provider")
        except (KeyError, TypeError, ValueError) as error:
            raw["billing-error"] = str(error).encode()
            return AttemptResult(
                replace(
                    attempt.result,
                    outcome=Outcome.INFRASTRUCTURE_FAILURE,
                    exit_code=None,
                ),
                raw,
            )
        return AttemptResult(attempt.result, raw)
