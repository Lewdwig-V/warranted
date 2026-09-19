"""Native rootless containment; enabled explicitly and required by container CI."""

import json
import multiprocessing
import os
import subprocess

import pytest

from warranted.ledger import Ledger, Manifest, Outcome, Result, Snapshot
from warranted.sandbox import SANDBOX_ID, Sandbox
from warranted.worker import AttemptResult, Episode, UnknownOutcome, run_workflow

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
        reason="requires WARRANTED_CONTAINER_TESTS=1 and rootless Podman",
    ),
]


def setup(root):
    with Ledger.create(
        root / "ledger",
        Manifest("m3-containment", "1", "run", "world", {}, {"model": 4, "tool": 4}),
        {
            "input.txt": Snapshot(b"permitted", "fixture", "1"),
            "private-reference": Snapshot(b"PRIVATE ANSWER", "checker", "1"),
        },
    ):
        pass
    return Episode(
        "contained",
        "Write result.json then submit.",
        ("input.txt",),
        environment=SANDBOX_ID,
        max_steps=1,
    )


def run(root, episode, command):
    def model(*_):
        with (root / "model-calls.log").open("ab") as witness:
            witness.write(b"called\n")
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"model": 1}, 1),
            {"response": json.dumps({"command": command}).encode()},
        )

    with Sandbox(root / "ledger", episode) as sandbox:

        def execute(request, payload):
            with (root / "tool-calls.log").open("ab") as witness:
                witness.write(b"called\n")
            return sandbox(request, payload)

        result = run_workflow(
            root / "ledger",
            root / "graph.sqlite3",
            episode,
            model=model,
            environment=execute,
        )
        name = sandbox.name
    assert subprocess.run(["podman", "container", "exists", name]).returncode == 1
    with Ledger.open(root / "ledger") as ledger:
        op = next(op for op in ledger.operations() if op.request.origin.kind == "tool")
        raw = {
            name: ledger.read_artifact(ref)
            for name, ref in op.completion.observation.artifacts.items()
        }
    return result, raw


def test_worker_cannot_read_host_state_or_credentials_and_capture_is_immutable(
    tmp_path, monkeypatch
):
    episode = setup(tmp_path)
    secret = tmp_path / "credentials"
    secret.write_text("HOST SECRET")
    monkeypatch.setenv("WARRANTED_TEST_SECRET", "HOST SECRET")
    command = f"""python - <<'PY'
import json, os, socket
from pathlib import Path
assert Path('input.txt').read_text() == 'permitted'
assert 'private-reference' not in Path('context.json').read_text()
assert not Path({str(secret)!r}).exists()
assert not Path({str(tmp_path / "ledger" / "ledger.sqlite3")!r}).exists()
assert not Path({str(tmp_path / "graph.sqlite3")!r}).exists()
assert 'WARRANTED_TEST_SECRET' not in os.environ
try:
    assert b'HOST SECRET' not in Path('/proc/1/environ').read_bytes()
except PermissionError:
    pass
assert not Path('/run/podman/podman.sock').exists()
assert not Path('/var/run/docker.sock').exists()
assert 'CapEff:\\t0000000000000000' in Path('/proc/self/status').read_text()
assert Path('/sys/fs/cgroup/memory.max').read_text().strip() == '134217728'
assert Path('/sys/fs/cgroup/pids.max').read_text().strip() == '32'
assert Path('/sys/fs/cgroup/cpu.max').read_text().split() == ['100000', '100000']
fs = os.statvfs('/work')
assert fs.f_blocks * fs.f_frsize == 8388608
try:
    socket.create_connection(('1.1.1.1', 443), timeout=0.2)
except OSError:
    pass
else:
    raise AssertionError('network escaped')
Path('result.json').write_text(json.dumps({{'candidate': True}}))
PY
test $? = 0 && printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\nready\\n'
"""
    result, raw = run(tmp_path, episode, command)
    assert result["exit_status"] == "Submitted", raw
    assert json.loads(raw["candidate/result.json"]) == {"candidate": True}
    assert b"PRIVATE ANSWER" not in b"".join(raw.values())


@pytest.mark.parametrize(
    "command",
    [
        "ln -s /etc/passwd result.json",
        "mkfifo result.json",
        "python -c \"open('result.json','wb').write(b'x' * 1048577)\"",
    ],
)
def test_candidate_links_special_files_and_oversize_are_rejected(tmp_path, command):
    episode = setup(tmp_path)
    result, raw = run(
        tmp_path,
        episode,
        command + "; printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n'",
    )
    assert result["exit_status"] != "Submitted"
    assert "candidate/result.json" not in raw
    assert b"capture" in raw["diagnostic"]


def test_background_writer_is_stopped_before_capture(tmp_path):
    episode = setup(tmp_path)
    command = """python - <<'PY' >/dev/null 2>&1 &
import pathlib, time
p = pathlib.Path('result.json')
p.write_text('{}')
time.sleep(60)
p.write_text('late mutation')
PY
while [ ! -f result.json ]; do :; done
printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n'
"""
    result, raw = run(tmp_path, episode, command)
    assert result["exit_status"] == "Submitted"
    assert raw["candidate/result.json"] == b"{}"


def test_raw_invalid_utf8_survives_capture(tmp_path):
    episode = setup(tmp_path)
    result, raw = run(
        tmp_path,
        episode,
        "printf '{}' > result.json; printf '\\377' >&2; "
        "printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n'",
    )
    assert result["exit_status"] == "Submitted"
    assert raw["stderr"] == b"\xff"


SUBMIT = "printf '{}' > result.json; printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n'"


def capture_crash(root, episode, pipe, when):
    from warranted import sandbox

    execute, complete = sandbox._run, Ledger.complete

    def barrier():
        pipe.send("capture barrier")
        pipe.recv()

    def at_capture(args, *a, **kw):
        if when == "before-capture" and args[-1] == sandbox._CAPTURE:
            barrier()
        return execute(args, *a, **kw)

    def at_complete(self, session, request, result, raw):
        if request.origin.kind != "tool" or when == "before-capture":
            return complete(self, session, request, result, raw)
        if when == "saved":
            value = complete(self, session, request, result, raw)
        else:
            assert raw["candidate/result.json"] == b"{}"
            value = None
        barrier()
        return value

    sandbox._run, Ledger.complete = at_capture, at_complete
    run(root, episode, SUBMIT)


@pytest.mark.parametrize("when", ["before-capture", "captured", "saved"])
def test_native_capture_crash_keeps_exact_evidence_or_blocks(tmp_path, when):
    episode = setup(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=capture_crash, args=(tmp_path, episode, child, when))
    process.start()
    try:
        assert parent.poll(45), f"capture barrier not reached: {process.exitcode}"
        assert parent.recv() == "capture barrier"
    finally:
        if process.is_alive():
            process.kill()
        process.join(10)
        parent.close()
        child.close()
        Sandbox(tmp_path / "ledger", episode).close()
    if when == "saved":
        result, raw = run(tmp_path, episode, SUBMIT)
        assert result["exit_status"] == "Submitted"
        assert raw["candidate/result.json"] == b"{}"
    else:
        for _ in range(2):
            with pytest.raises(UnknownOutcome):
                run(tmp_path, episode, SUBMIT)
        with Ledger.open(tmp_path / "ledger") as ledger:
            assert ledger.accounting()["tool"].reserved == 1
    assert (tmp_path / "model-calls.log").read_bytes() == b"called\n"
    assert (tmp_path / "tool-calls.log").read_bytes() == b"called\n"
