"""One-shot contained jobs for checkers: no network, no host mounts, no privileges.

A checker uses a job to run untrusted code, such as a worker's candidate program.
The job's program runs as UID 1000 with no capabilities in a fresh container from a
pinned image, with only its own files in a private /work. Its result is attributed to
that program alone: the program can shape its own output, so a checker must treat a
job's output as the program's claim, never as host evidence about anything else.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from warranted._containers import SandboxFailure, _run, require_runtime

OUTPUT_LIMIT = 256 * 1024
_FILE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}")
# A registry reference pinned by digest, or a local image ID (64 hex digits, as
# `podman image inspect --format '{{.Id}}'` prints it) for an image built locally.
_PINNED_IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}|[0-9a-f]{64}")
MAX_TIMEOUT_SECONDS = 3600


def pinned_image(image) -> bool:
    return type(image) is str and _PINNED_IMAGE.fullmatch(image) is not None


@dataclass(frozen=True)
class JobLimits:
    """Resources for one job. The defaults suit a small interpreter run."""

    memory_mb: int = 128
    pids: int = 32
    scratch_mb: int = 8  # each of the private /work and /tmp
    cpus: int = 1

    def __post_init__(self):
        for name, low, high in (
            ("memory_mb", 16, 8192),
            ("pids", 8, 1024),
            ("scratch_mb", 1, 4096),
            ("cpus", 1, 8),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"job {name} must be an integer from {low} to {high}")

    def record(self) -> dict[str, int]:
        return {
            "memory_mb": self.memory_mb,
            "pids": self.pids,
            "scratch_mb": self.scratch_mb,
            "cpus": self.cpus,
        }


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


DEFAULT_LIMITS = JobLimits()


@dataclass(frozen=True)
class JobResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    truncated: bool = False


class JobRunner(Protocol):
    # Bound into the project identity; change it when containment changes.
    identity: str

    def run(
        self,
        image: str,
        argv: Sequence[str],
        files: Mapping[str, bytes],
        *,
        stdin: bytes = b"",
        timeout_seconds: int = 10,
        limits: JobLimits = DEFAULT_LIMITS,
    ) -> JobResult: ...


def validate_job(
    image: str, argv: Sequence[str], files: Mapping[str, bytes], timeout_seconds: int
) -> None:
    if not pinned_image(image):
        raise ValueError("job image must be pinned by sha256 digest or image ID")
    if not argv or not all(type(arg) is str for arg in argv):
        raise ValueError("job argv must be a nonempty list of strings")
    for name, data in files.items():
        if type(name) is not str or not _FILE_NAME.fullmatch(name):
            raise ValueError(f"unsafe job file name: {name!r}")
        if type(data) is not bytes:
            raise TypeError("job files must be bytes")
    if type(timeout_seconds) is not int or not (
        1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(f"job timeout must be 1 to {MAX_TIMEOUT_SECONDS} seconds")


class PodmanJobs:
    """Run each job in a fresh rootless container; host errors raise SandboxFailure."""

    identity = "podman-jobs-v1"

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
        limits: JobLimits = DEFAULT_LIMITS,
    ) -> JobResult:
        validate_job(image, argv, files, timeout_seconds)
        if type(limits) is not JobLimits:
            raise TypeError("job limits must be JobLimits")
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
                f"--pids-limit={limits.pids}",
                f"--memory={limits.memory_mb}m",
                f"--memory-swap={limits.memory_mb}m",
                f"--cpus={limits.cpus}",
                f"--tmpfs=/work:rw,nosuid,nodev,size={limits.scratch_mb}m,mode=1777",
                f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={limits.scratch_mb}m,"
                "mode=1777",
                "--user=1000:1000",
                "--workdir=/work",
                f"--timeout={timeout_seconds + 30}",
                "--log-driver=none",
                # The image's own entrypoint would otherwise wrap the command.
                "--entrypoint=python",
                image,
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


class JobContext:
    """Host-recorded entropy and contained jobs for trusted host code.

    `draw_seed` returns fresh entropy and records it. `run_job` validates and runs a
    job, then records the request digests, the result, and the capped output, so
    the caller can store all of it as raw evidence.
    """

    def __init__(self, jobs: JobRunner | None = None):
        self._jobs = jobs
        self.seeds: list[int] = []
        self.job_log: list[dict] = []
        self.job_output: dict[str, bytes] = {}

    def draw_seed(self) -> int:
        seed = secrets.randbits(64)
        self.seeds.append(seed)
        return seed

    def run_job(
        self,
        image: str,
        argv: Sequence[str],
        files: Mapping[str, bytes],
        *,
        stdin: bytes = b"",
        timeout_seconds: int = 10,
        limits: JobLimits = DEFAULT_LIMITS,
    ) -> JobResult:
        if self._jobs is None:
            raise RuntimeError("this project has no job runner")
        validate_job(image, argv, files, timeout_seconds)
        result = self._jobs.run(
            image,
            argv,
            files,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            limits=limits,
        )
        prefix = f"jobs/{len(self.job_log) + 1}"
        self.job_output[f"{prefix}/stdout"] = result.stdout
        self.job_output[f"{prefix}/stderr"] = result.stderr
        self.job_log.append(
            {
                "image": image,
                "argv": list(argv),
                "files": {n: _sha256(d) for n, d in sorted(files.items())},
                "stdin": _sha256(stdin),
                "timeout_seconds": timeout_seconds,
                "limits": limits.record(),
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "truncated": result.truncated,
                "stdout": {
                    "channel": f"{prefix}/stdout",
                    "digest": _sha256(result.stdout),
                },
                "stderr": {
                    "channel": f"{prefix}/stderr",
                    "digest": _sha256(result.stderr),
                },
            }
        )
        return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
