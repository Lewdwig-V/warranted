"""Native checker jobs: contained, unprivileged, offline, and bounded in time."""

import os
import subprocess

import pytest

from warranted.jobs import JobLimits, PodmanJobs
from warranted.sandbox import IMAGE

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
        reason="requires WARRANTED_CONTAINER_TESTS=1 and rootless Podman",
    ),
]

PROBE = b"""
import os, socket, sys
print(open('data.txt').read().strip(), sys.stdin.read().strip(), os.getuid())
try:
    socket.create_connection(('1.1.1.1', 53), timeout=2)
    print('network')
except OSError:
    print('offline')
try:
    open('/etc/escaped', 'w')
    print('writable')
except OSError:
    print('read-only')
"""


def test_a_job_sees_only_its_files_and_runs_offline_without_privilege():
    result = PodmanJobs().run(
        IMAGE,
        ["python", "-I", "probe.py"],
        {"probe.py": PROBE, "data.txt": b"from-file"},
        stdin=b"from-stdin",
        timeout_seconds=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().split() == [
        "from-file",
        "from-stdin",
        "1000",
        "offline",
        "read-only",
    ]


def test_a_job_that_overruns_is_reported_as_timed_out():
    result = PodmanJobs().run(
        IMAGE,
        ["python", "-I", "-c", "import time; time.sleep(30)"],
        {},
        timeout_seconds=1,
    )
    assert result.timed_out and result.returncode is None


def test_a_job_failure_keeps_its_exit_code_and_output():
    result = PodmanJobs().run(
        IMAGE,
        ["python", "-I", "-c", "import sys; print('partial'); sys.exit(3)"],
        {},
        timeout_seconds=20,
    )
    assert (result.returncode, result.stdout) == (3, b"partial\n")


ALLOCATE = [
    "python",
    "-I",
    "-c",
    "data = bytearray(200 * 1024 * 1024); print(len(data))",
]


def test_job_limits_take_effect():
    small = PodmanJobs().run(IMAGE, ALLOCATE, {}, timeout_seconds=60)
    large = PodmanJobs().run(
        IMAGE, ALLOCATE, {}, timeout_seconds=60, limits=JobLimits(memory_mb=512)
    )
    assert small.returncode != 0  # 200 MiB does not fit the default 128 MiB
    assert (large.returncode, large.stdout) == (0, b"209715200\n")


def test_a_local_image_id_pins_a_job():
    image_id = subprocess.run(
        ["podman", "image", "inspect", "--format", "{{.Id}}", IMAGE],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    result = PodmanJobs().run(
        image_id, ["python", "-I", "-c", "print('local')"], {}, timeout_seconds=30
    )
    assert (result.returncode, result.stdout) == (0, b"local\n")
