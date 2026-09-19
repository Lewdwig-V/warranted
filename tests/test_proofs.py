"""The verifier must decide from captured proof bytes, never claimed success."""

import json

import pytest

from warranted.proofs import RESOURCES, SOURCE_LIMIT, policy_digest, verify


@pytest.mark.parametrize("source", [b"", b"x" * (1024 * 1024 + 1)])
def test_invalid_source_cannot_start_verification(tmp_path, source):
    assert SOURCE_LIMIT == 1024 * 1024
    with pytest.raises(ValueError, match="source"):
        verify(source, tmp_path / "missing-bundle.json")


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
        verify(b"theorem fake : True := True.intro", path)
