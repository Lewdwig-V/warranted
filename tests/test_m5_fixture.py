"""Migration checks retain independent legacy and revised-contract failures."""

import json
import os
import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m5/demo.py"


def module():
    return runpy.run_path(str(SCRIPT))


def test_renaming_cannot_hide_a_lost_legacy_label():
    demo = module()
    expected = {
        "version": 2,
        "endpoint": "service.invalid",
        "timeout_seconds": 0,
        "label": "",
    }
    case = {"kind": "migrate", "expected": expected, "legacy": ""}
    dropped = {key: value for key, value in expected.items() if key != "label"}
    checked = demo["judge"](case, json.dumps(dropped).encode(), 0, False)
    assert checked == {"output_schema": True, "renaming": True, "legacy_label": False}
    assert all(demo["judge"](case, json.dumps(expected).encode(), 0, False).values())


@pytest.mark.parametrize(
    "output",
    [b"{}", b'{"passed": true}', b'{"version": 2, "version": 2}', b"NaN"],
)
def test_malformed_or_self_reported_success_cannot_pass(output):
    case = {
        "kind": "repeat",
        "expected": {"version": 2, "endpoint": "x", "timeout_seconds": 1},
    }
    assert module()["judge"](case, output, 0, False) == {"repetition": False}


def test_invalid_input_needs_completed_explicit_failure():
    judge = module()["judge"]
    case = {"kind": "invalid"}
    assert judge(case, b"", 1, False) == {"input_rejection": True}
    for output, code, timeout in (
        (b"", 0, False),
        (b"", 124, True),
        (b"", None, False),
    ):
        assert judge(case, output, code, timeout) == {"input_rejection": False}


@pytest.mark.parametrize(
    "payload",
    [
        b'{"../migrate.py":"pass"}',
        b'{"migrate.py":"pass","legacy_consumer.py":"pass"}',
        b'{"migrate.py":"pass","migrate.py":"other"}',
        b'{"migrate.py":null}',
    ],
)
def test_candidate_cannot_edit_protected_paths_or_forge_payload(payload):
    with pytest.raises(ValueError):
        module()["candidate_source"](payload)


def scripted(tmp_path, monkeypatch):
    from warranted.ledger import Outcome, Result
    from warranted.worker import AttemptResult

    demo = module()
    root = tmp_path / "demo"
    demo["initialize"](root)
    references = json.loads((SCRIPT.parent / "fixture/references.json").read_bytes())

    def execute(root, episode, inputs):
        name = episode.inputs[0].removeprefix("candidate-").removesuffix(".py")
        raw = {}
        for case_name in inputs:
            case = references[case_name]
            code = int(
                case["kind"] == "invalid"
                or (name == "one-way" and case["kind"] == "repeat")
            )
            output = b""
            if not code:
                value = dict(case["expected"])
                if name == "drop-label" and case["kind"] == "migrate":
                    value.pop("label", None)
                output = json.dumps(value).encode()
            raw[case_name + "/stdout"] = output
            raw[case_name + "/stderr"] = b"invalid configuration" if code else b""
            raw[case_name + "/execution.json"] = json.dumps(
                {
                    "returncode": code,
                    "timed_out": False,
                    "output_limited": False,
                    "elapsed_ns": 1,
                }
            ).encode()
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"batch": 1}, 123),
            raw,
        )

    monkeypatch.setitem(demo["demonstrate"].__globals__, "execute", execute)
    return demo, root


def assert_matrix(report):
    assert report["spent"] == {"batch": 3, "check": 6}
    assert report["reserved"] == {"batch": 0, "check": 0}
    assert report["executions"] == 9
    for name, statuses in {
        "one-way": ("accepted", "rejected"),
        "drop-label": ("rejected", "rejected"),
        "complete": ("accepted", "accepted"),
    }.items():
        item = report["matrix"][name]
        assert (
            tuple(item["contracts"][v]["status"] for v in ("initial", "revised"))
            == statuses
        )
        assert item["checks"] == {
            "repository_integrity": True,
            "output_schema": True,
            "renaming": True,
            "legacy_label": name != "drop-label",
            "input_rejection": True,
            "repetition": name != "one-way",
        }
    assert Path(report["export"]).is_file()


def test_matrix_keeps_independent_failures_and_reuses_actual_checks(
    tmp_path, monkeypatch
):
    demo, root = scripted(tmp_path, monkeypatch)
    first = demo["demonstrate"](root)
    assert_matrix(first)
    assert first["new_operations"] == 15

    def forbidden(*args):
        pytest.fail("cached results must not execute a candidate or rerun the checker")

    monkeypatch.setitem(demo["demonstrate"].__globals__, "execute", forbidden)
    monkeypatch.setitem(demo["demonstrate"].__globals__, "evaluate", forbidden)
    repeated = demo["demonstrate"](root)
    assert_matrix(repeated)
    assert repeated["new_operations"] == 0
    assert repeated["matrix"] == first["matrix"]
    assert repeated["execution_elapsed_ns"] == first["execution_elapsed_ns"]


def test_old_contract_receipt_cannot_pass_revised_acceptance(tmp_path, monkeypatch):
    from warranted.acceptance import Acceptance, AcceptanceContext, Evidence, Status
    from warranted.ledger import Ledger

    demo, root = scripted(tmp_path, monkeypatch)
    demo["demonstrate"](root)
    with Ledger.open(root / "ledger") as ledger:

        def ref(name):
            return Evidence(name, ledger.project.snapshots[name].artifact)

        target = ref("candidate-complete.json")
        checks = {
            version: next(
                op.request
                for op in ledger.operations()
                if op.request.origin.kind == "migration-check"
                and target.name in op.request.origin.inputs
                and f"policy-{version}.json" in op.request.origin.inputs
            )
            for version in ("initial", "revised")
        }
        context = AcceptanceContext(
            ref("policy-revised.json"),
            ref("contracts.json"),
            {"evaluate": checks["revised"]},
        )
        boundary = Acceptance(ledger, ledger.start_session(), lambda _: context)
        decision = boundary.accept(
            target, {"evaluate": checks["initial"].origin.operation_id}
        )
        assert decision.status != Status.ACCEPTED
        assert set(decision.requirements.values()) == {Status.STALE}


def test_unknown_execution_retains_reservation_and_blocks_retry(tmp_path, monkeypatch):
    from warranted.ledger import Ledger
    from warranted.worker import UnknownOutcome

    demo, root = scripted(tmp_path, monkeypatch)

    def lost(*args):
        raise RuntimeError("lost container outcome")

    monkeypatch.setitem(demo["demonstrate"].__globals__, "execute", lost)
    with pytest.raises(RuntimeError, match="lost"):
        demo["demonstrate"](root)
    with pytest.raises(UnknownOutcome):
        demo["demonstrate"](root)
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.accounting()["batch"].reserved == 1
        assert ledger.accounting()["batch"].spent == 0
    assert len((root / "executions.jsonl").read_bytes().splitlines()) == 1


def test_unavailable_isolation_never_runs_or_reports_candidate_success(
    tmp_path, monkeypatch
):
    from warranted.containers import SandboxFailure
    from warranted.ledger import Outcome

    demo = module()
    closed = []

    class Unavailable:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def _prepare(self, first):
            raise SandboxFailure("isolation unavailable")

        def __exit__(self, *args):
            closed.append(True)

    monkeypatch.setitem(demo["execute"].__globals__, "Sandbox", Unavailable)
    result = demo["execute"](tmp_path, None, {"probe": "{}"})
    assert result.result.outcome is Outcome.INFRASTRUCTURE_FAILURE
    assert result.result.exit_code is None
    assert b"isolation unavailable" in result.raw["diagnostic"]
    assert closed == [True]


def test_infrastructure_failure_cannot_be_mistaken_for_input_rejection(
    tmp_path, monkeypatch
):
    from warranted.ledger import Outcome, Result
    from warranted.worker import AttemptResult

    demo, root = scripted(tmp_path, monkeypatch)

    def failed(*args):
        return AttemptResult(
            Result(Outcome.INFRASTRUCTURE_FAILURE, None, {"batch": 1}, 1),
            {"stdout": b"", "stderr": b"", "diagnostic": b"runtime unavailable"},
        )

    monkeypatch.setitem(demo["demonstrate"].__globals__, "execute", failed)
    report = demo["demonstrate"](root)
    for item in report["matrix"].values():
        assert item["checks"]["input_rejection"] is False
        for contract in item["contracts"].values():
            assert contract["status"] != "accepted"
            assert set(contract["requirements"].values()) == {"infrastructure_failure"}


native = pytest.mark.skipif(
    os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
    reason="requires native rootless Podman",
)


@pytest.mark.container
@native
def test_native_migration_matrix_and_fresh_process_reuse(tmp_path):
    import subprocess
    import sys

    root = Path(os.environ.get("WARRANTED_M5_REPORTS", tmp_path / "matrix")).resolve()
    for expected in (15, 0):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(root)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert_matrix(report)
        assert report["new_operations"] == expected


def native_case(root, source, inputs=None):
    from warranted.ledger import Ledger, Manifest, Snapshot
    from warranted.sandbox import SANDBOX_ID
    from warranted.worker import Episode

    demo = module()
    with Ledger.create(
        root / "ledger",
        Manifest("m5-case", "1", "run", "world", {}, {"batch": 1}),
        {
            "program.py": Snapshot(source, "probe", "1"),
            "input.json": Snapshot(b"{}", "probe-input", "1"),
            "private-answer": Snapshot(b"HOST-ONLY", "grading", "1"),
        },
    ) as ledger:
        episode = Episode("probe", "One probe", ("program.py",), environment=SANDBOX_ID)
        req = demo["request"](ledger, "migration-batch", episode.inputs)
        completed = demo["perform"](
            ledger,
            ledger.start_session(),
            req,
            "batch",
            lambda: demo["execute"](root, episode, inputs or {"probe": "{}"}),
        )
        return completed.result, {
            name: ledger.read_artifact(ref)
            for name, ref in completed.observation.artifacts.items()
        }


@pytest.mark.container
@native
def test_native_timeout_preserves_output_and_known_failure(tmp_path):
    from warranted.ledger import Outcome

    result, raw = native_case(
        tmp_path, b"import time\nprint('started', flush=True)\ntime.sleep(60)\n"
    )
    assert (
        result.outcome is Outcome.SUCCEEDED
    )  # The supervisor captured a known failure.
    state = json.loads(raw["probe/execution.json"])
    assert state["returncode"] is None and state["timed_out"] is True
    assert raw["probe/stdout"] == b"started\n"
    assert result.usage == {"batch": 1}


@pytest.mark.container
@native
def test_native_candidate_cannot_forge_status_or_read_answers(tmp_path):
    from warranted.ledger import Outcome

    result, raw = native_case(
        tmp_path,
        b"""
import os
from pathlib import Path
assert "private-answer" not in Path("/work/context.json").read_text()
assert not Path("/work/input.json").exists()
try:
    Path("/tmp/warranted-host/status").write_text('{"returncode":0,"timed_out":false}')
except PermissionError:
    pass
else:
    raise AssertionError("forged supervisor status")
print('{"returncode":0,"timed_out":false}', flush=True)
os._exit(7)
""",
    )
    assert result.outcome is Outcome.SUCCEEDED
    assert json.loads(raw["probe/execution.json"])["returncode"] == 7
    assert json.loads(raw["probe/stdout"])["returncode"] == 0


@pytest.mark.container
@native
def test_native_batch_stops_children_and_bounds_output_between_cases(tmp_path):
    from warranted.ledger import Outcome

    result, raw = native_case(
        tmp_path,
        b"""
import json, os, subprocess, sys
from pathlib import Path
phase = json.load(sys.stdin)
if phase == 1:
    Path('case-local').write_text('must not reach the next working directory')
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    Path('/tmp/probe-pid').write_text(str(child.pid))
    try:
        os.open('/proc/' + str(os.getppid()) + '/fd/0', os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError:
        pass
    else:
        raise AssertionError('read supervisor inputs')
    print('first')
elif phase == 2:
    assert not Path('case-local').exists()
    pid = Path('/tmp/probe-pid').read_text()
    try:
        status = Path('/proc/' + pid + '/status').read_text()
    except FileNotFoundError:
        pass
    else:
        state = next(line for line in status.splitlines() if line.startswith('State:'))
        assert state.split()[1] == 'Z'
    print('second')
else:
    os.write(1, b'x' * 100000)
""",
        {"first": "1", "second": "2", "third": "3"},
    )
    assert result.outcome is Outcome.SUCCEEDED, raw
    assert raw["first/stdout"] == b"first\n"
    assert raw["second/stdout"] == b"second\n"
    assert json.loads(raw["third/execution.json"])["output_limited"] is True
    assert len(raw["third/stdout"]) + len(raw["third/stderr"]) == 64 * 1024
