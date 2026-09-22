"""Workspace bytes are untrusted, including when restored after a restart."""

import base64
import json

import pytest

from warranted.sandbox import decode_workspace


@pytest.mark.parametrize(
    "files",
    [
        {"../escape": ""},
        {"/absolute": ""},
        {"a/../escape": ""},
        {"a//b": ""},
        {".": ""},
        {"a": "", "a/b": ""},
        {"notes": "not base64"},
        {str(i): "" for i in range(129)},
        {"notes": base64.b64encode(b"x" * 1048577).decode()},
    ],
)
def test_unsafe_workspace_is_rejected(files):
    with pytest.raises(ValueError):
        decode_workspace(json.dumps(files).encode())


def test_nested_source_and_binary_notes_survive():
    files = {"repo/main.py": "cGFzcw==", "notes.md": "/wA="}
    assert decode_workspace(json.dumps(files).encode()) == files


def test_duplicate_workspace_paths_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        decode_workspace(b'{"notes":"YQ==","notes":"Yg=="}')
