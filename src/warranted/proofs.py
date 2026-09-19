"""One bounded, independent proof check. Durable operation receipts follow in M4/2.

The bundle is host-owned output from scripts/build_proof_bundle.py. A worker can
submit only source bytes; it cannot select the challenge, policy, or tool image.
"""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import perf_counter_ns

from warranted.containers import SandboxFailure, _run, require_runtime

RESOURCES = Path(__file__).with_name("proof")
SOURCE_LIMIT = 1024 * 1024
EXPORT_LIMIT = 8 * 1024 * 1024
LIMITS = {
    "source_bytes": SOURCE_LIMIT,
    "export_bytes": EXPORT_LIMIT,
    "output_bytes": 2 * 1024 * 1024,
    "seconds": 120,
    "container_seconds": 180,
    "memory_bytes": 1024 * 1024 * 1024,
    "workspace_bytes": 64 * 1024 * 1024,
    "pids": 64,
    "cpus": 1,
}


class ProofStatus(StrEnum):
    PROVED = "proved"
    REJECTED = "rejected"
    UNPROVED = "unproved"
    UNSUPPORTED = "unsupported"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True)
class Verification:
    status: ProofStatus
    diagnostic: str
    axioms: tuple[str, ...]
    identity: dict
    elapsed_ns: int
    raw: dict[str, bytes]

    def as_dict(self) -> dict:
        return {
            "version": 1,
            "status": self.status.value,
            "diagnostic": self.diagnostic,
            "axioms": list(self.axioms),
            "identity": self.identity,
            "elapsed_ns": self.elapsed_ns,
            "raw": {
                name: base64.b64encode(data).decode() for name, data in self.raw.items()
            },
        }


def policy_digest() -> str:
    digest = hashlib.sha256(json.dumps(LIMITS, sort_keys=True).encode())
    for path in [
        Path(__file__),
        Path(__file__).with_name("containers.py"),
        *sorted(RESOURCES.iterdir()),
    ]:
        if path.is_file():
            digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def _checked(args: list[str], data: bytes = b"", **kwargs) -> bytes:
    result = _run(args, data, **kwargs)
    if result.returncode:
        raise SandboxFailure(
            "proof container control failed", result.stdout, result.stderr
        )
    return result.stdout


def verify(source: bytes, bundle_path: Path) -> Verification:
    """Verify captured bytes under a host-selected immutable build manifest.

    Cleanup failure raises instead of claiming a known, stopped attempt. This
    function does not reserve budgets, persist evidence, or authorize acceptance.
    """
    if type(source) is not bytes or not 0 < len(source) <= SOURCE_LIMIT:
        raise ValueError("source must be nonempty bounded bytes")
    bundle_bytes = bundle_path.read_bytes()
    bundle = json.loads(bundle_bytes)
    if (
        bundle.get("policy") != policy_digest()
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", bundle.get("image", ""))
        or bundle.get("toolchain")
        != json.loads((RESOURCES / "toolchain.json").read_bytes())
    ):
        raise ValueError("bundle does not match the pinned proof policy")
    identity = {
        "solution": hashlib.sha256(source).hexdigest(),
        "challenge": hashlib.sha256(
            (RESOURCES / "Challenge.lean").read_bytes()
        ).hexdigest(),
        "bundle": hashlib.sha256(bundle_bytes).hexdigest(),
        "image": bundle["image"],
        "tools": bundle["manifest"],
        "policy": bundle["policy"],
        "limits": dict(LIMITS),
        "kernel": platform.release(),
    }
    name = "warranted-proof-" + uuid.uuid4().hex
    started = perf_counter_ns()
    raw: dict[str, bytes] = {
        "bundle.json": bundle_bytes,
        "Solution.lean": source,
        "Challenge.lean": (RESOURCES / "Challenge.lean").read_bytes(),
    }
    status, diagnostic, axioms = ProofStatus.INFRASTRUCTURE_FAILURE, "", ()
    created = False
    executing = False
    try:
        runtime = require_runtime()
        identity["podman"] = runtime["version"]
        created = True
        _checked(
            [
                "run",
                "--detach",
                "--name",
                name,
                "--pull=never",
                "--network=none",
                "--http-proxy=false",
                "--pid=private",
                "--ipc=private",
                "--uts=private",
                "--cgroupns=private",
                "--read-only",
                "--read-only-tmpfs=false",
                "--cap-drop=ALL",
                "--cap-add=KILL",
                "--cap-add=SETUID",
                "--cap-add=SETGID",
                "--cap-add=CHOWN",
                "--security-opt=no-new-privileges",
                "--pids-limit=64",
                "--memory=1g",
                "--memory-swap=1g",
                "--cpus=1",
                "--user=0:0",
                "--tmpfs=/work:rw,noexec,nosuid,nodev,size=64m,mode=0755",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m,mode=1777",
                "--timeout=180",
                "--stop-timeout=0",
                "--log-driver=none",
                bundle["image"],
            ]
        )
        recorded = _checked(["exec", name, "cat", "/opt/manifest.json"])
        if json.loads(recorded) != bundle["manifest"]:
            raise SandboxFailure("image does not match the pinned executable manifest")
        entry = ["exec", "--user=0:0", name, "python", "-I", "/opt/proof/supervisor.py"]
        _checked(
            [
                "exec",
                "--interactive",
                "--user=0:0",
                name,
                "python",
                "-I",
                "/opt/proof/supervisor.py",
                "prepare",
            ],
            source,
        )
        executing = True
        result = _run([*entry, "execute"], seconds=LIMITS["seconds"])
        executing = False
        raw.update(stdout=result.stdout, stderr=result.stderr)
        if result.returncode:
            raise SandboxFailure(
                "verification supervisor failed", result.stdout, result.stderr
            )
        captured = json.loads(
            _checked([*entry, "capture"], output_limit=3 * EXPORT_LIMIT)
        )
        if captured["exit_code"] != 0:
            raise SandboxFailure(
                "verifier process failed", result.stdout, result.stderr
            )
        raw.update(
            {
                key: base64.b64decode(value, validate=True)
                for key, value in captured["files"].items()
            }
        )
        try:
            verdict = json.loads(raw["verdict.json"])
            status = ProofStatus(verdict["status"])
            diagnostic = verdict["diagnostic"]
            axioms = tuple(sorted(verdict["axioms"]))
            if (
                set(verdict) != {"status", "diagnostic", "axioms"}
                or type(diagnostic) is not str
                or type(verdict["axioms"]) is not list
                or any(type(axiom) is not str for axiom in axioms)
                or len(axioms) != len(set(axioms))
                or not set(axioms) <= {"propext", "Quot.sound"}
                or (
                    status is ProofStatus.PROVED
                    and not all(raw[n] for n in ("challenge.ndjson", "solution.ndjson"))
                )
            ):
                raise ValueError("invalid verdict")
        except (ValueError, KeyError, TypeError):
            status, diagnostic, axioms = (
                ProofStatus.UNSUPPORTED,
                "missing or malformed verifier decision",
                (),
            )
    except (SandboxFailure, OSError) as error:
        diagnostic = str(error)
        status = (
            ProofStatus.UNPROVED
            if executing
            and (
                "runtime timeout" in diagnostic or "runtime output limit" in diagnostic
            )
            else ProofStatus.INFRASTRUCTURE_FAILURE
        )
        if isinstance(error, SandboxFailure):
            raw.update(stdout=error.stdout, stderr=error.stderr)
    finally:
        if created:
            _checked(["rm", "--force", "--time=0", "--ignore", name])
            if _run(["container", "exists", name]).returncode != 1:
                raise SandboxFailure("proof container cleanup is not established")
    return Verification(
        status, diagnostic, axioms, identity, perf_counter_ns() - started, raw
    )
