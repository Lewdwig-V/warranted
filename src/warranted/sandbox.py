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
from pathlib import Path, PurePosixPath
from time import perf_counter_ns

from warranted.containers import SandboxFailure, _run, require_runtime
from warranted.ledger import Ledger, Outcome, Request, Result, _json_object
from warranted.worker import AttemptResult, Episode, input_files

IMAGE = (
    "docker.io/library/python@sha256:"
    "72d3d75f2639ab82b34b29390ad3d6e0827c775befee94edda8e9976818f488d"
)
SANDBOX_ID = "podman-rootless-v3/" + IMAGE
CANDIDATE_LIMIT = 1024 * 1024

# This code comes from the host, never the worker's workspace or environment.
_LOAD = """
import base64, json, os, sys
os.mkdir('/tmp/warranted-host', 0o700)
payload = json.load(sys.stdin)
for name, encoded in payload['inputs'].items():
    with open('/work/' + name, 'xb') as file:
        file.write(base64.b64decode(encoded, validate=True))
    os.chmod('/work/' + name, 0o444)
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
os.mkdir('/work/workspace')
for name, encoded in payload['workspace'].items():
    path = '/work/workspace/' + name
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    with open(path, 'xb') as file:
        file.write(base64.b64decode(encoded, validate=True))
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
import base64, json, os, signal, stat, sys, time
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
captured = {}
unfinished = sys.argv[1:] == ['unfinished']
try:
    if not unfinished or os.path.lexists('/work/result.json'):
        fd = os.open('result.json', flags, dir_fd=directory)
        with os.fdopen(fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > 1048576):
                raise ValueError('candidate requires a bounded regular file, one link')
            data = file.read(1048577)
            if len(data) > 1048576:
                raise ValueError('candidate exceeds limit')
        captured['result.json'] = base64.b64encode(data).decode()
except (OSError, ValueError) as error:
    if not unfinished:
        raise
    detail = str(error).encode('utf-8', 'backslashreplace')
    captured['result-error.txt'] = base64.b64encode(detail).decode()
try:
    workspace, size = {}, 0
    workspace_dir = os.open('workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
    for parent, dirs, files, directory in os.fwalk(
            '.', dir_fd=workspace_dir, follow_symlinks=False):
        if len(Path(parent).parts) > 16:
            raise ValueError('workspace exceeds depth limit')
        for name in dirs:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('workspace contains a directory link')
        for name in sorted(files):
            path = str(Path(parent, name))
            if len(workspace) >= 128 or len(Path(path).parts) > 16:
                raise ValueError('workspace exceeds file or depth limit')
            with os.fdopen(os.open(name, flags, dir_fd=directory), 'rb') as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('workspace requires regular files with one link')
                data = file.read(1048577 - size)
            size += len(data)
            if size > 1048576:
                raise ValueError('workspace exceeds size limit')
            workspace[path] = base64.b64encode(data).decode()
    encoded = json.dumps(workspace, sort_keys=True).encode()
    captured['workspace.json'] = base64.b64encode(encoded).decode()
except (OSError, ValueError) as error:
    if not unfinished:
        raise
    detail = str(error).encode('utf-8', 'backslashreplace')
    captured['workspace-error.txt'] = base64.b64encode(detail).decode()
print(json.dumps(captured))
"""


def decode_workspace(data: bytes) -> dict[str, str]:
    """Validate an untrusted bounded file snapshot before restoring any bytes."""
    if len(data) > 2 * CANDIDATE_LIMIT:
        raise ValueError("workspace exceeds encoded size limit")
    files = json.loads(data, object_pairs_hook=_json_object)
    if type(files) is not dict or len(files) > 128:
        raise ValueError("workspace requires at most 128 files")
    size = 0
    for name, encoded in files.items():
        path = PurePosixPath(name)
        if (
            not path.parts
            or path.is_absolute()
            or str(path) != name
            or ".." in path.parts
            or len(path.parts) > 16
            or "\x00" in name
            or type(encoded) is not str
        ):
            raise ValueError("unsafe workspace entry")
        if any(str(parent) in files for parent in path.parents):
            raise ValueError("workspace file conflicts with a directory")
        size += len(base64.b64decode(encoded, validate=True))
        if size > CANDIDATE_LIMIT:
            raise ValueError("workspace exceeds size limit")
    return files


class Sandbox:
    """Trusted callable for WorkerEnvironment. Use as a context manager."""

    def __init__(self, ledger_root: Path, episode: Episode):
        if episode.environment != SANDBOX_ID:
            raise ValueError("episode must pin the container environment")
        if any(
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", name)
            or name in {"context.json", "result.json", "workspace"}
            for name in (*episode.inputs, *episode.files)
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
        timeout = self.episode.container_timeout_seconds
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
                f"--timeout={timeout}",
                "--stop-timeout=0",
                "--log-driver=none",
                IMAGE,
                "sleep",
                str(timeout + 30),
            ]
        )
        with Ledger.open(self.root) as ledger:
            files = input_files(ledger, self.episode)
            workspace = (
                decode_workspace(ledger.read_artifact(self.episode.workspace.artifact))
                if self.episode.workspace
                else {}
            )
        files["context.json"] = json.dumps(
            {"authoritative": False, "files": sorted(files)}, sort_keys=True
        ).encode()
        if sum(map(len, files.values())) > CANDIDATE_LIMIT:
            raise SandboxFailure("permitted context exceeds limit")
        encoded = json.dumps(
            {
                "inputs": {
                    name: base64.b64encode(data).decode()
                    for name, data in files.items()
                },
                "workspace": workspace,
            }
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
                    ["exec", "--user=0:0", self.name, "python", "-I", "-c", _CAPTURE],
                    output_limit=4 * CANDIDATE_LIMIT,
                )
                if captured.returncode:
                    code, outcome = 1, Outcome.FAILED
                    raw["diagnostic"] = b"candidate capture failed\n" + captured.stderr
                else:
                    for name, encoded in json.loads(captured.stdout).items():
                        raw["candidate/" + name] = base64.b64decode(
                            encoded, validate=True
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
