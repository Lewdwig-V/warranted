"""Proofs as task-layer checkers: the verifier boundary and the LeanProof checker.

A domain owns its targets (ProofTarget); the project owns the verifier, whose
identity is bound into the project. See docs/proposals/m7-proofs.md.
"""

from __future__ import annotations

import hashlib
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
