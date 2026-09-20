"""The bounded runtime transport cannot hang after its output pipes close."""

import os

import pytest

from warranted.containers import SandboxFailure, _run


def test_closed_output_pipes_still_obey_the_timeout(tmp_path, monkeypatch):
    runtime = tmp_path / "podman"
    runtime.write_text("#!/bin/sh\nexec 1>&- 2>&-\nexec /bin/sleep 60\n")
    runtime.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    with pytest.raises(SandboxFailure, match="runtime timeout"):
        _run([], seconds=1)
