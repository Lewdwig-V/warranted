"""Proofs as task-layer checkers: the verifier boundary and the LeanProof checker.

A domain owns its targets (ProofTarget); the project owns the verifier, whose
identity is bound into the project. See docs/proposals/m7-proofs.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from warranted import _proofs
from warranted._proofs import ProofTarget, Verification


class ProofVerifier(Protocol):
    """Host infrastructure that checks a proof source against a target."""

    # Bound into the project identity; change it when the verifier changes.
    identity: str

    def verify(self, source: bytes, target: ProofTarget) -> Verification: ...


class LeanVerifier:
    """The pinned Lean bundle and hardened container.

    See docs/reference/proof-verification.md.
    """

    def __init__(
        self, bundle_path: Path | str, seconds: int = _proofs.LIMITS["seconds"]
    ):
        self.bundle = Path(bundle_path).read_bytes()
        _proofs._validate(b"x", seconds)
        self.seconds = seconds
        self.identity = "lean-verifier-v1:" + hashlib.sha256(self.bundle).hexdigest()

    def verify(self, source: bytes, target: ProofTarget) -> Verification:
        _proofs._validate(source, self.seconds)
        return _proofs._verify(source, self.bundle, target=target, seconds=self.seconds)


class LeanProof:
    """A checker: verify the worker's Lean source against a domain-owned target.

    The source is the captured workspace file `source`. `premises`, if given, is
    trusted domain code returning {name: bool}; a proved theorem whose premise
    fails does not apply here, so the verdict is UNSUPPORTED. A proof never
    authorizes acceptance on its own: tasks still require their other checks.
    """

    isolated = True  # a contained verifier over captured bytes; no shared state
    needs_proofs = True

    def __init__(
        self,
        target: ProofTarget,
        source: str = "Solution.lean",
        premises: Callable | None = None,
    ):
        if not isinstance(target, ProofTarget):
            raise ValueError("LeanProof needs a ProofTarget")
        if type(source) is not str or not source or "/" in source:
            raise ValueError("source must be a workspace file name")
        self.target, self.source, self.premises = target, source, premises
        self.sources = (target.path,)
        self.version = "1:" + target.theorem

    def check(self, ctx):
        from warranted._tasks import Verdict, VerdictStatus

        premises = {} if self.premises is None else dict(self.premises(ctx))
        if not all(type(n) is str and type(v) is bool for n, v in premises.items()):
            raise TypeError("premises must map names to booleans")
        try:
            files = json.loads(ctx.candidate["workspace.json"])
            data = base64.b64decode(files[self.source], validate=True)
        except (KeyError, TypeError, ValueError):
            return Verdict(
                VerdictStatus.REJECTED,
                {
                    "proof": "missing",
                    "error": f"{self.source} was not submitted",
                    "premises": premises,
                },
            )
        if not 0 < len(data) <= _proofs.SOURCE_LIMIT:
            return Verdict(
                VerdictStatus.REJECTED,
                {
                    "proof": "invalid",
                    "error": f"{self.source} must be 1 to {_proofs.SOURCE_LIMIT} bytes",
                    "premises": premises,
                },
            )
        result = ctx.verify(self.target, data)
        status = {
            _proofs.ProofStatus.PROVED: VerdictStatus.PASSED
            if all(premises.values())
            else VerdictStatus.UNSUPPORTED,
            _proofs.ProofStatus.REJECTED: VerdictStatus.REJECTED,
            _proofs.ProofStatus.UNPROVED: VerdictStatus.UNPROVED,
            _proofs.ProofStatus.UNSUPPORTED: VerdictStatus.UNSUPPORTED,
            _proofs.ProofStatus.INFRASTRUCTURE_FAILURE: (
                VerdictStatus.INFRASTRUCTURE_FAILURE
            ),
        }[result.status]
        feedback = {
            "proof": result.status.value,
            "diagnostic": result.diagnostic[:4096],
            "axioms": list(result.axioms),
            "premises": premises,
        }
        return Verdict(status, feedback)
