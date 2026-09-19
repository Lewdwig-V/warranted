"""One rootless Podman container per episode, with no host filesystem mounts.

The trusted supervisor can drop worker credentials and kill workers. Workers use UID
1000, no effective capabilities, and no-new-privileges. The supervisor kills
workers before reading candidates. Kernel/runtime/host compromise is out of scope.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from warranted.containers import SandboxFailure, _run, require_runtime
from warranted.exports import export_evidence
from warranted.ledger import Ledger, Outcome, Request, Result
from warranted.worker import AttemptResult, Episode

IMAGE = (
    "docker.io/library/python@sha256:"
    "72d3d75f2639ab82b34b29390ad3d6e0827c775befee94edda8e9976818f488d"
)
SANDBOX_ID = "podman-rootless-v2/" + IMAGE
CANDIDATE_LIMIT = 1024 * 1024

# This code comes from the host, never the worker's workspace or environment.
_LOAD = """
import base64, json, os, sys
os.mkdir('/tmp/warranted-host', 0o700)
for name, encoded in json.load(sys.stdin).items():
    with open('/work/' + name, 'xb') as file:
        file.write(base64.b64decode(encoded, validate=True))
    os.chmod('/work/' + name, 0o444)
"""
_EXECUTE = """
import subprocess, sys
from pathlib import Path
status = Path('/tmp/warranted-host/status')
status.write_text('')
code = subprocess.run(['/bin/sh', '-c', sys.argv[1]],
                      user=1000, group=1000, extra_groups=[]).returncode
status.write_text(str(128 - code if code < 0 else code))
"""
_CAPTURE = """
import base64, json, os, signal, stat, time
from pathlib import Path
try:
    os.kill(-1, signal.SIGKILL)
except ProcessLookupError:
    pass
for _ in range(300):
    live = False
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            fields = dict(line.split(':', 1) for line in path.read_text().splitlines())
        except FileNotFoundError:
            continue
        if fields['Uid'].split()[0] == '1000' and fields['State'].strip()[0] != 'Z':
            live = True
    if not live:
        break
    time.sleep(0.01)
else:
    raise RuntimeError('worker processes did not stop')
directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
fd = os.open('result.json', flags, dir_fd=directory)
with os.fdopen(fd, 'rb') as file:
    info = os.fstat(file.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1048576:
        raise ValueError('candidate must be a bounded regular file with one link')
    data = file.read(1048577)
    if len(data) > 1048576:
        raise ValueError('candidate exceeds limit')
print(json.dumps({'result.json': base64.b64encode(data).decode()}))
"""


class Sandbox:
    """Trusted callable for WorkerEnvironment. Use as a context manager."""

    def __init__(self, ledger_root: Path, episode: Episode):
        if episode.environment != SANDBOX_ID:
            raise ValueError("episode must pin the container environment")
        if any(
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", name)
            or name in {"context.json", "result.json"}
            for name in episode.inputs
        ):
            raise ValueError("worker input names must be safe, distinct basenames")
        self.root, self.episode = ledger_root, episode
        with Ledger.open(ledger_root) as ledger:
            self.project = ledger.project
        identity = self.project.project_id + "/" + episode.episode_id
        self.name = "warranted-" + hashlib.sha256(identity.encode()).hexdigest()[:24]

    def _checked(self, args: list[str], data: bytes = b"") -> bytes:
        result = _run(args, data)
        if result.returncode:
            raise SandboxFailure(
                "container control failed", result.stdout, result.stderr
            )
        return result.stdout

    def _prepare(self, first: bool) -> None:
        require_runtime()
        exists = _run(["container", "exists", self.name]).returncode
        if exists == 0:
            state = json.loads(self._checked(["inspect", self.name]))[0]["State"]
            if not state["Running"] or state["Paused"]:
                raise SandboxFailure("worker workspace is no longer live")
            return
        if exists != 1 or not first:
            raise SandboxFailure(
                "worker workspace is missing; a fresh episode is required"
            )
        self._checked(
            [
                "run",
                "--detach",
                "--name",
                self.name,
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
                "--security-opt=no-new-privileges",
                "--pids-limit=32",
                "--memory=128m",
                "--memory-swap=128m",
                "--cpus=1",
                "--tmpfs=/work:rw,noexec,nosuid,nodev,size=8m,mode=1777",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m,mode=1777",
                "--user=0:0",
                "--workdir=/work",
                "--timeout=120",
                "--stop-timeout=0",
                "--log-driver=none",
                IMAGE,
                "sleep",
                "120",
            ]
        )
        with Ledger.open(self.root) as ledger, TemporaryDirectory() as directory:
            index = export_evidence(
                ledger, Path(directory) / "context", snapshots=self.episode.inputs
            )
            files = {"context.json": index.read_bytes()}
            files.update(
                {
                    name: ledger.read_artifact(ledger.project.snapshots[name].artifact)
                    for name in self.episode.inputs
                }
            )
        if sum(map(len, files.values())) > CANDIDATE_LIMIT:
            raise SandboxFailure("permitted context exceeds limit")
        encoded = json.dumps(
            {name: base64.b64encode(data).decode() for name, data in files.items()}
        ).encode()
        self._checked(
            [
                "exec",
                "--interactive",
                "--user=0:0",
                self.name,
                "python",
                "-I",
                "-c",
                _LOAD,
            ],
            encoded,
        )

    def __call__(self, request: Request, payload: bytes) -> AttemptResult:
        prefix = f"episode/{self.episode.episode_id}/tool/"
        if (
            request.context != self.project
            or not request.origin.operation_id.startswith(prefix)
        ):
            raise ValueError("container request belongs to another episode or project")
        action = json.loads(payload)
        if (
            type(action) is not dict
            or set(action) != {"command"}
            or type(action["command"]) is not str
        ):
            raise ValueError("expected one shell command")
        started = perf_counter_ns()
        raw = {}
        try:
            self._prepare(request.origin.operation_id == prefix + "1")
            result = _run(
                [
                    "exec",
                    "--user=0:0",
                    "--workdir=/work",
                    self.name,
                    "python",
                    "-I",
                    "-c",
                    _EXECUTE,
                    action["command"],
                ]
            )
            raw = {"stdout": result.stdout, "stderr": result.stderr}
            if result.returncode != 0:
                raise SandboxFailure(
                    "runtime or worker transport failed", result.stdout, result.stderr
                )
            # Only the trusted supervisor can write this status. Worker streams
            # and Podman's reserved exit codes cannot impersonate it.
            status = self._checked(
                ["exec", "--user=0:0", self.name, "cat", "/tmp/warranted-host/status"]
            )
            if not status.isdigit() or not 0 <= int(status) <= 255:
                raise SandboxFailure("missing worker exit status", **raw)
            code = int(status)
            outcome = Outcome.SUCCEEDED if code == 0 else Outcome.FAILED
            lines = result.stdout.splitlines()
            if (
                code == 0
                and lines
                and lines[0].strip() == b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            ):
                captured = _run(
                    ["exec", "--user=0:0", self.name, "python", "-I", "-c", _CAPTURE]
                )
                if captured.returncode:
                    code, outcome = 1, Outcome.FAILED
                    raw["diagnostic"] = b"candidate capture failed\n" + captured.stderr
                else:
                    raw["candidate/result.json"] = base64.b64decode(
                        json.loads(captured.stdout)["result.json"], validate=True
                    )
                self.close()
        except SandboxFailure as error:
            # Cleanup must succeed before claiming a known stopped attempt.
            self.close()
            code, outcome = None, Outcome.INFRASTRUCTURE_FAILURE
            raw = {
                "stdout": error.stdout,
                "stderr": error.stderr,
                "diagnostic": str(error).encode(),
            }
        return AttemptResult(
            Result(outcome, code, {"tool": 1}, perf_counter_ns() - started), raw
        )

    def close(self) -> None:
        self._checked(["rm", "--force", "--time=0", "--ignore", self.name])
        if _run(["container", "exists", self.name]).returncode != 1:
            raise SandboxFailure("container cleanup is not established")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
