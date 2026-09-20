#!/usr/local/bin/python -I
"""Pinned Comparator launcher: inherited socket filter and bounded exports."""

import os
import signal
import subprocess
import sys

args = sys.argv[1:]
command = args[args.index("--") + 1]
limit = 8 * 1024 * 1024 if command == "/opt/bin/lean4export" else 2 * 1024 * 1024
with subprocess.Popen(
    ["/opt/bin/landrun", "--env=LEAN_NUM_THREADS=2", *args],
    stdout=subprocess.PIPE,
    start_new_session=True,
) as process:
    size = 0
    while chunk := process.stdout.read(65536):
        size += len(chunk)
        if size > limit:
            os.killpg(process.pid, signal.SIGKILL)
            print("proof subprocess output exceeds limit", file=sys.stderr)
            sys.exit(124)
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    sys.exit(process.wait())
