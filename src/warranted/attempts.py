"""Concrete loopback fake-service adapter; no SDK retries or live credentials.

This protocol tests exact receipts under a trusted local service. It is not an
authenticated production provider protocol. Service identity is pinned by the
host. The service receives a request digest and permitted model messages, never
the full private ledger context.
"""

from __future__ import annotations

import base64
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from urllib.request import Request as HTTPRequest

from warranted.acceptance import _digest, _encode
from warranted.ledger import Outcome, Request, Result, _json_object
from warranted.worker import AttemptResult


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ReceiptService:
    """One HTTP dispatch per call; reconcile uses a separate read-only lookup."""

    def __init__(self, url: str, service_id: str):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or not parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("fake service requires an explicit loopback HTTP port")
        if not service_id.strip():
            raise ValueError("service identity is required")
        self.url, self.service_id = url.rstrip("/"), service_id
        # No environment proxy, redirect, SDK retry, or implicit provider call.
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _identity(self, request: Request) -> dict:
        service = request.origin.producer
        if service != self.service_id:
            raise ValueError("service identity differs from the recorded request")
        return {
            "service": service,
            "project": request.context.project_id,
            "operation": request.origin.operation_id,
            "request": _digest(request),
        }

    def _read(self, request: Request, path: str, data: bytes | None) -> AttemptResult:
        identity = self._identity(request)
        query = HTTPRequest(
            self.url + path, data=data, headers={"Content-Type": "application/json"}
        )
        with self.opener.open(query, timeout=5) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("receipt exceeds limit")
        receipt = json.loads(raw, object_pairs_hook=_json_object)
        if (
            type(receipt) is not dict
            or set(receipt) != {"identity", "result", "channels"}
            or receipt["identity"] != identity
        ):
            raise ValueError("receipt identity differs from the exact request")
        result = receipt["result"]
        if type(result) is not dict or set(result) != {
            "outcome",
            "exit_code",
            "usage",
            "elapsed_ns",
        }:
            raise ValueError("invalid or unknown result/usage")
        if type(result["usage"]) is not dict or set(result["usage"]) != {
            request.origin.kind
        }:
            raise ValueError("invalid or unknown usage")
        result = Result(**{**result, "outcome": Outcome(result["outcome"])})
        channels = receipt["channels"]
        if (
            type(channels) is not dict
            or set(channels) != {"response"}
            or type(channels["response"]) is not str
        ):
            raise ValueError("invalid response channels")
        decoded = base64.b64decode(channels["response"], validate=True)
        return AttemptResult(result, {"response": decoded, "receipt.json": raw})

    def __call__(self, request: Request, payload: bytes) -> AttemptResult:
        if request.origin.kind != "model":
            raise ValueError("fake model service only accepts model attempts")
        return self._read(
            request,
            "/attempt",
            _encode(
                {
                    "identity": self._identity(request),
                    "payload": json.loads(payload, object_pairs_hook=_json_object),
                }
            ),
        )

    def reconcile(self, request: Request) -> AttemptResult | None:
        if request.origin.kind != "model":
            return None
        try:
            return self._read(request, "/receipt/" + _digest(request), None)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise
