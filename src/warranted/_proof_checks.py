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

    The source is the captured workspace file `source`. `premises` and
    `correspondence`, if given, are trusted domain code returning {name: bool};
    both run before verification and fail closed (a non-boolean result raises
    TypeError, an exception propagates as a checker fault).

    - `premises` are applicability conditions about the task's inputs. A proved
      theorem whose premise fails does not apply here: UNSUPPORTED.
    - `correspondence` checks that the worker's candidate matches the theorem's
      model. A proved theorem whose correspondence fails is the worker's
      mistake: REJECTED, with feedback. A failed premise wins over both.

    Premise and correspondence results are shown to the worker in feedback, so
    they must not be computed from private inputs in a way that leaks them. A
    proof never authorizes acceptance on its own: tasks still require their
    other checks.
    """

    isolated = True  # a contained verifier over captured bytes; no shared state
    needs_proofs = True

    def __init__(
        self,
        target: ProofTarget,
        source: str = "Solution.lean",
        premises: Callable | None = None,
        correspondence: Callable | None = None,
    ):
        if not isinstance(target, ProofTarget):
            raise ValueError("LeanProof needs a ProofTarget")
        if type(source) is not str or not source or "/" in source:
            raise ValueError("source must be a workspace file name")
        self.target, self.source = target, source
        self.premises, self.correspondence = premises, correspondence
        self.sources = (target.path,)
        self.version = "1:" + target.theorem

    def check(self, ctx):
        from warranted._tasks import Verdict, VerdictStatus

        premises = _results(self.premises, ctx, "premises")
        correspondence = _results(self.correspondence, ctx, "correspondence")
        checks = {"premises": premises, "correspondence": correspondence}
        try:
            files = json.loads(ctx.candidate["workspace.json"])
            data = base64.b64decode(files[self.source], validate=True)
        except (KeyError, TypeError, ValueError):
            return Verdict(
                VerdictStatus.REJECTED,
                {
                    "proof": "missing",
                    "error": f"{self.source} was not submitted",
                    **checks,
                },
            )
        if not 0 < len(data) <= _proofs.SOURCE_LIMIT:
            return Verdict(
                VerdictStatus.REJECTED,
                {
                    "proof": "invalid",
                    "error": f"{self.source} must be 1 to {_proofs.SOURCE_LIMIT} bytes",
                    **checks,
                },
            )
        result = ctx.verify(self.target, data)
        proved = _proofs.ProofStatus.PROVED
        status = {
            proved: VerdictStatus.PASSED,
            _proofs.ProofStatus.REJECTED: VerdictStatus.REJECTED,
            _proofs.ProofStatus.UNPROVED: VerdictStatus.UNPROVED,
            _proofs.ProofStatus.UNSUPPORTED: VerdictStatus.UNSUPPORTED,
            _proofs.ProofStatus.INFRASTRUCTURE_FAILURE: (
                VerdictStatus.INFRASTRUCTURE_FAILURE
            ),
        }[result.status]
        if result.status is proved and not all(premises.values()):
            status = VerdictStatus.UNSUPPORTED  # applicability wins
        elif result.status is proved and not all(correspondence.values()):
            status = VerdictStatus.REJECTED
        feedback = {
            "proof": result.status.value,
            "axioms": list(result.axioms),
            **checks,
        }
        diagnostic = result.diagnostic[:4096]
        # Only Lean's own output reaches the worker; a verifier-side
        # unsupported or infrastructure diagnostic can name container internals.
        if result.status in (
            proved,
            _proofs.ProofStatus.REJECTED,
            _proofs.ProofStatus.UNPROVED,
        ):
            return Verdict(status, {**feedback, "diagnostic": diagnostic})
        return Verdict(status, feedback, host_only={"diagnostic": diagnostic})


def _results(fn, ctx, kind):
    results = {} if fn is None else dict(fn(ctx))
    if not all(type(n) is str and type(v) is bool for n, v in results.items()):
        raise TypeError(f"{kind} must map names to booleans")
    return results
