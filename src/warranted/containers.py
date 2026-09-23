"""Bounded Podman transport and the native runtime requirements shared by adapters."""

import json
import os
import selectors
import signal
import subprocess
from time import monotonic

OUTPUT_LIMIT = 2 * 1024 * 1024
PODMAN_COMMAND_TIMEOUT_SECONDS = 20


class SandboxFailure(RuntimeError):
    def __init__(self, message: str, stdout: bytes = b"", stderr: bytes = b""):
        super().__init__(message)
        self.stdout, self.stderr = stdout, stderr


def _run(
    args: list[str],
    data: bytes = b"",
    *,
    seconds: int = PODMAN_COMMAND_TIMEOUT_SECONDS,
    output_limit: int = OUTPUT_LIMIT,
) -> subprocess.CompletedProcess:
    """Bound client time and captured bytes without buffering unbounded output."""
    output = {"stdout": bytearray(), "stderr": bytearray()}
    with (
        subprocess.Popen(
            ["podman", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process,
        selectors.DefaultSelector() as selector,
    ):
        remaining = memoryview(data)
        for stream, event, name in (
            (process.stdout, selectors.EVENT_READ, "stdout"),
            (process.stderr, selectors.EVENT_READ, "stderr"),
            (process.stdin, selectors.EVENT_WRITE, "stdin"),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event, name)
        deadline = monotonic() + seconds
        try:
            while selector.get_map():
                if monotonic() >= deadline:
                    raise SandboxFailure("runtime timeout")
                for key, _ in selector.select(min(0.1, max(0, deadline - monotonic()))):
                    if key.data == "stdin":
                        if remaining:
                            try:
                                remaining = remaining[
                                    os.write(key.fd, remaining[:65536]) :
                                ]
                            except BrokenPipeError:
                                remaining = memoryview(b"")
                        if not remaining:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        else:
                            room = output_limit - sum(map(len, output.values()))
                            output[key.data].extend(chunk[:room])
                            if len(chunk) > room:
                                raise SandboxFailure(
                                    "runtime output limit; raw streams are prefixes"
                                )
            code = process.wait(timeout=max(0.01, deadline - monotonic()))
        except (SandboxFailure, subprocess.TimeoutExpired) as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise SandboxFailure(
                "runtime timeout"
                if isinstance(error, subprocess.TimeoutExpired)
                else str(error),
                bytes(output["stdout"]),
                bytes(output["stderr"]),
            ) from error
    return subprocess.CompletedProcess(
        args, code, bytes(output["stdout"]), bytes(output["stderr"])
    )


def require_runtime() -> dict:
    result = _run(["info", "--format", "json"])
    if result.returncode:
        raise SandboxFailure(
            "container runtime unavailable", result.stdout, result.stderr
        )
    info = json.loads(result.stdout)
    host = info["host"]
    if (
        not host["security"]["rootless"]
        or not host["security"]["seccompEnabled"]
        or host["serviceIsRemote"]
        or host["cgroupVersion"] != "v2"
        or not {"cpu", "memory", "pids"} <= set(host["cgroupControllers"])
    ):
        raise SandboxFailure(
            "rootless local Podman, seccomp, and cgroup v2 limits are required"
        )
    return info
