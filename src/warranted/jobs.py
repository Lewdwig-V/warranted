"""One-shot contained jobs for checkers: no network, no host mounts, no privileges.

A checker uses a job to run untrusted code, such as a worker's candidate program.
The job's program runs as UID 1000 with no capabilities in a fresh container from a
pinned image, with only its own files in a private /work. Its result is attributed to
that program alone: the program can shape its own output, so a checker must treat a
job's output as the program's claim, never as host evidence about anything else.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from warranted.containers import SandboxFailure, _run, require_runtime

OUTPUT_LIMIT = 256 * 1024
_FILE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}")
_PINNED_IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")

# Host code, passed as an argument; never read from the job's files.
_RUN = """
import base64, json, os, subprocess, sys
payload = json.load(sys.stdin)
for name, encoded in payload['files'].items():
    with open('/work/' + name, 'xb') as file:
        file.write(base64.b64decode(encoded, validate=True))
try:
    done = subprocess.run(payload['argv'], input=base64.b64decode(payload['stdin']),
                          capture_output=True, timeout=payload['seconds'])
    result = {'returncode': done.returncode, 'timed_out': False,
              'stdout': done.stdout, 'stderr': done.stderr}
except subprocess.TimeoutExpired as expired:
    result = {'returncode': None, 'timed_out': True,
              'stdout': expired.stdout or b'', 'stderr': expired.stderr or b''}
limit = payload['limit']
for channel in ('stdout', 'stderr'):
    result[channel + '_truncated'] = len(result[channel]) > limit
    result[channel] = base64.b64encode(result[channel][:limit]).decode()
sys.stdout.write(json.dumps(result))
"""


@dataclass(frozen=True)
class JobResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    truncated: bool = False


class JobRunner(Protocol):
    def run(
        self,
        image: str,
        argv: Sequence[str],
        files: Mapping[str, bytes],
        *,
        stdin: bytes = b"",
        timeout_seconds: int = 10,
    ) -> JobResult: ...


def validate_job(
    image: str, argv: Sequence[str], files: Mapping[str, bytes], timeout_seconds: int
) -> None:
    if type(image) is not str or not _PINNED_IMAGE.fullmatch(image):
        raise ValueError("job image must be pinned by sha256 digest")
    if not argv or not all(type(arg) is str for arg in argv):
        raise ValueError("job argv must be a nonempty list of strings")
    for name, data in files.items():
        if type(name) is not str or not _FILE_NAME.fullmatch(name):
            raise ValueError(f"unsafe job file name: {name!r}")
        if type(data) is not bytes:
            raise TypeError("job files must be bytes")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("job timeout must be 1 to 300 seconds")


class PodmanJobs:
    """Run each job in a fresh rootless container; host errors raise SandboxFailure."""

    def __init__(self):
        self._checked = False

    def run(
        self,
        image: str,
        argv: Sequence[str],
        files: Mapping[str, bytes],
        *,
        stdin: bytes = b"",
        timeout_seconds: int = 10,
    ) -> JobResult:
        validate_job(image, argv, files, timeout_seconds)
        if not self._checked:
            require_runtime()
            self._checked = True
        payload = json.dumps(
            {
                "files": {n: base64.b64encode(d).decode() for n, d in files.items()},
                "argv": list(argv),
                "stdin": base64.b64encode(stdin).decode(),
                "seconds": timeout_seconds,
                "limit": OUTPUT_LIMIT,
            }
        ).encode()
        completed = _run(
            [
                "run",
                "--rm",
                "--interactive",
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
                "--security-opt=no-new-privileges",
                "--pids-limit=32",
                "--memory=128m",
                "--memory-swap=128m",
                "--cpus=1",
                "--tmpfs=/work:rw,nosuid,nodev,size=8m,mode=1777",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m,mode=1777",
                "--user=1000:1000",
                "--workdir=/work",
                f"--timeout={timeout_seconds + 30}",
                "--log-driver=none",
                image,
                "python",
                "-I",
                "-c",
                _RUN,
            ],
            payload,
            seconds=timeout_seconds + 40,
            output_limit=4 * OUTPUT_LIMIT + 4096,
        )
        if completed.returncode:
            raise SandboxFailure(
                "job runner failed", completed.stdout, completed.stderr
            )
        try:
            value = json.loads(completed.stdout)
            return JobResult(
                value["returncode"],
                base64.b64decode(value["stdout"], validate=True),
                base64.b64decode(value["stderr"], validate=True),
                value["timed_out"],
                value["stdout_truncated"] or value["stderr_truncated"],
            )
        except (ValueError, KeyError, TypeError) as error:
            raise SandboxFailure(
                "invalid job result", completed.stdout, completed.stderr
            ) from error
