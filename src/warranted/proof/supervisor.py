"""Trusted image entry point. Never imported from the candidate workspace."""

import base64
import ctypes
import errno
import hashlib
import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path("/opt/proof")
WORK = Path("/work")
EXPORT_LIMIT = 8 * 1024 * 1024


def socket_filter():
    """Stack with Podman's default filter; no inherited Unix sockets exist."""
    if platform.machine() != "x86_64":
        raise RuntimeError("the pinned bundle requires Linux x86_64")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(444, 0, 0, 1) < 3:  # LANDLOCK_CREATE_RULESET_VERSION
        raise RuntimeError("Landlock ABI 3 or newer is required")
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)

    class Comparison(ctypes.Structure):
        _fields_ = [
            ("arg", ctypes.c_uint),
            ("op", ctypes.c_int),
            ("a", ctypes.c_uint64),
            ("b", ctypes.c_uint64),
        ]

    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(Comparison),
    ]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("seccomp initialization failed")
    try:
        for name in (b"socket", b"socketpair", b"ptrace", b"process_vm_writev"):
            number = lib.seccomp_syscall_resolve_name(name)
            comparison = Comparison(0, 4, 1, 0)  # arg 0 == AF_UNIX
            count = int(name in (b"socket", b"socketpair"))
            if number < 0 or lib.seccomp_rule_add_array(
                context,
                0x00050000 | errno.EPERM,
                number,
                count,
                ctypes.byref(comparison),
            ):
                raise RuntimeError("seccomp rule installation failed")
        if lib.seccomp_load(context):
            raise RuntimeError("seccomp filter installation failed")
    finally:
        lib.seccomp_release(context)


def prepare():
    source = sys.stdin.buffer.read(1024 * 1024 + 1)
    if not 0 < len(source) <= 1024 * 1024:
        raise ValueError("source exceeds limit or is empty")
    os.mkdir("/tmp/host", 0o700)
    for name in ("Challenge.lean", "config.json", "lakefile.toml"):
        shutil.copyfile(ROOT / name, WORK / name)
    (WORK / "lake-manifest.json").write_text(
        '{"version":"1.2.0","packagesDir":".lake/packages",'
        '"packages":[],"name":"WarrantedProof","lakeDir":".lake"}'
    )
    (WORK / "Solution.lean").write_bytes(source)
    (WORK / ".lake").mkdir()
    os.chown(WORK / ".lake", 1000, 1000)
    for name in ("verdict.json", "challenge.ndjson", "solution.ndjson"):
        (WORK / name).touch()
        os.chmod(WORK / name, 0o644)
        os.chown(WORK / name, 1000, 1000)
    os.chmod(WORK, 0o755)


PROBE = """
import errno, socket
from pathlib import Path
for family in (socket.AF_UNIX,):
    for make in (lambda: socket.socket(family), lambda: socket.socketpair(family)):
        try: make()
        except PermissionError: pass
        else: raise RuntimeError('Unix socket restriction missing')
try: Path('/work/verdict.json').write_text('forged')
except PermissionError: pass
else: raise RuntimeError('Landrun write restriction missing')
Path('/work/.lake/probe').write_text('permitted')
"""


def execute():
    socket_filter()
    probe = subprocess.run(
        [
            "/opt/proof/landrun.py",
            "--best-effort",
            "--ro",
            "/",
            "--rw",
            "/dev",
            "--rwx",
            "/work/.lake",
            "--rox",
            "/usr",
            "--add-exec",
            "--ldd",
            "--",
            "/usr/local/bin/python",
            "-I",
            "-c",
            PROBE,
        ],
        user=1000,
        group=1000,
        extra_groups=[],
    )
    if probe.returncode:
        raise RuntimeError("native isolation probe failed")
    completed = subprocess.run(
        ["/opt/bin/verify"], user=1000, group=1000, extra_groups=[]
    )
    Path("/tmp/host/status").write_text(str(completed.returncode))


def capture():
    # This exec is root in a private PID namespace. Kill all verification descendants.
    try:
        os.kill(-1, signal.SIGKILL)
    except ProcessLookupError:
        pass
    # Wait until no untrusted process can change a captured file.
    import time

    for _ in range(300):
        live = False
        for path in Path("/proc").glob("[0-9]*/status"):
            try:
                fields = dict(
                    line.split(":", 1) for line in path.read_text().splitlines()
                )
            except FileNotFoundError:
                continue
            if fields["Uid"].split()[0] == "1000" and fields["State"].strip()[0] != "Z":
                live = True
        if not live:
            break
        time.sleep(0.01)
    else:
        raise RuntimeError("verification processes did not stop")
    files = {}
    for name in ("verdict.json", "challenge.ndjson", "solution.ndjson"):
        fd = os.open(WORK / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            limit = 65536 if name == "verdict.json" else EXPORT_LIMIT
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > limit
            ):
                raise ValueError("invalid verifier artifact")
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("verifier artifact exceeds limit")
            files[name] = base64.b64encode(raw).decode()
    print(
        json.dumps(
            {"exit_code": int(Path("/tmp/host/status").read_text()), "files": files}
        )
    )


def manifest():
    tree = hashlib.sha256()
    for path in sorted(Path("/opt").rglob("*")):
        if path.is_file() and path != Path("/opt/manifest.json"):
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            tree.update(str(path).encode() + b"\0" + digest.encode() + b"\n")
    tools = {}
    for path in (
        "/opt/lean/bin/lean",
        "/opt/lean/bin/lake",
        "/opt/bin/verify",
        "/opt/bin/lean4export",
        "/opt/bin/landrun",
    ):
        with open(path, "rb") as stream:
            tools[path] = hashlib.file_digest(stream, "sha256").hexdigest()
    Path("/opt/manifest.json").write_text(
        json.dumps({"closure": tree.hexdigest(), "executables": tools}, sort_keys=True)
    )


if __name__ == "__main__":
    {"prepare": prepare, "execute": execute, "capture": capture, "manifest": manifest}[
        sys.argv[1]
    ]()
