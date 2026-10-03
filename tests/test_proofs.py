"""The verifier must decide from captured proof bytes, never claimed success."""

import json
import runpy
from pathlib import Path

import pytest

from warranted import ProofTarget
from warranted._proofs import (
    RESOURCES,
    SOURCE_LIMIT,
    ProofStatus,
    policy_digest,
    verify,
)

ROOT = Path(__file__).resolve().parents[1]
TARGETS = runpy.run_path(str(ROOT / "examples/proof_targets.py"))["TARGETS"]


def test_a_target_must_be_a_proof_target(tmp_path):
    with pytest.raises(ValueError, match="ProofTarget"):
        verify(b"source", tmp_path / "missing-bundle.json", target="uniqueness")


@pytest.mark.parametrize(
    "theorem", ["", "x; y", "Warranted.", ".x", "a b", 7, "Warranted.ok\n"]
)
def test_a_target_needs_a_qualified_theorem_name(theorem):
    with pytest.raises(ValueError, match="theorem"):
        ProofTarget(TARGETS["uniqueness"].path, theorem)


def test_a_target_needs_a_nonempty_bounded_challenge(tmp_path):
    empty = tmp_path / "Empty.lean"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="challenge"):
        ProofTarget(empty, "Warranted.x")
    with pytest.raises(FileNotFoundError):
        ProofTarget(tmp_path / "missing.lean", "Warranted.x")


def test_a_target_reads_its_challenge_once():
    target = TARGETS["uniqueness"]
    assert target.challenge == target.path.read_bytes()
    assert b"uniqueness_preserved" in target.challenge


@pytest.mark.parametrize("source", [b"", b"x" * (1024 * 1024 + 1)])
def test_invalid_source_cannot_start_verification(tmp_path, source):
    assert SOURCE_LIMIT == 1024 * 1024
    with pytest.raises(ValueError, match="source"):
        verify(source, tmp_path / "missing-bundle.json", target=TARGETS["uniqueness"])


@pytest.mark.parametrize("seconds", [0, -1, 121, True, "5"])
def test_host_timeout_must_stay_within_the_policy_ceiling(tmp_path, seconds):
    with pytest.raises(ValueError, match="timeout"):
        verify(
            b"source",
            tmp_path / "missing-bundle.json",
            seconds=seconds,
            target=TARGETS["uniqueness"],
        )


@pytest.mark.parametrize("field", ["policy", "image", "toolchain"])
def test_changed_bundle_pin_fails_before_runtime_dispatch(tmp_path, field):
    bundle = {
        "policy": policy_digest(),
        "image": "sha256:" + "0" * 64,
        "toolchain": json.loads((RESOURCES / "toolchain.json").read_bytes()),
    }
    bundle[field] = "untrusted:latest"
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="pinned proof policy"):
        verify(b"theorem fake : True := True.intro", path, target=TARGETS["uniqueness"])


def test_runtime_preflight_timeout_is_infrastructure_failure(tmp_path, monkeypatch):
    from warranted import _proofs as proofs
    from warranted._containers import SandboxFailure

    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "policy": policy_digest(),
                "image": "sha256:" + "0" * 64,
                "toolchain": json.loads((RESOURCES / "toolchain.json").read_bytes()),
                "manifest": {},
            }
        )
    )

    def unavailable():
        raise SandboxFailure("runtime timeout")

    monkeypatch.setattr(proofs, "require_runtime", unavailable)
    result = verify(
        b"theorem fake : True := True.intro", path, target=TARGETS["uniqueness"]
    )
    assert result.status is ProofStatus.INFRASTRUCTURE_FAILURE


def test_the_policy_digest_covers_the_container_runner(monkeypatch):
    from pathlib import Path

    from warranted import _containers

    before = policy_digest()
    read_bytes = Path.read_bytes
    runner = Path(_containers.__file__)

    def changed(path):
        data = read_bytes(path)
        return data + b"\n# changed\n" if path == runner else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert policy_digest() != before
