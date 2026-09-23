"""Full treatment recovery with the existing independent fixture checkers."""

import base64
import json
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from warranted import proofs
from warranted import sandbox as sandbox_module
from warranted.containers import PODMAN_COMMAND_TIMEOUT_SECONDS
from warranted.contexts import Condition
from warranted.ledger import Ledger, Origin, Outcome, Request, Result
from warranted.worker import AttemptResult, Episode, UnknownOutcome, submitted_files

SCRIPT = Path(__file__).resolve().parents[1] / "examples/m5/treatments.py"


def scripted(
    tmp_path,
    monkeypatch,
    family,
    condition,
    *,
    spontaneous=False,
    spontaneous_both=False,
):
    from test_proof_receipts import boundary

    demo = runpy.run_path(str(SCRIPT))
    scope = demo["demonstrate"].__globals__
    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "policy": proofs.policy_digest(),
                "image": "sha256:" + "0" * 64,
                "toolchain": json.loads(
                    (proofs.RESOURCES / "toolchain.json").read_bytes()
                ),
                "manifest": {},
                "build_elapsed_ns": 123,
            }
        )
    )
    boundary(monkeypatch)
    root = tmp_path / "run"
    demo["initialize"](root, family, condition, bundle)

    class Workspace:
        def __init__(self, ledger_root, episode):
            self.episode = episode

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __call__(self, request, payload):
            revised = self.episode.episode_id == "revised"
            if family == "csv":
                name = "candidate/wrong-offset" if revised else "candidate/correct"
            else:
                name = (
                    "candidate-complete.json" if revised else "candidate-one-way.json"
                )
            with Ledger.open(root / "ledger") as ledger:
                candidate = ledger.read_artifact(
                    ledger.project.snapshots[name].artifact
                )
                source = (
                    ledger.read_artifact(
                        ledger.project.snapshots["Solution.lean"].artifact
                    )
                    if spontaneous
                    else b""
                )
                proof_sources = (
                    {
                        name: ledger.read_artifact(
                            ledger.project.snapshots[filename].artifact
                        )
                        for name, filename in {
                            "uniqueness": "Solution.lean",
                            "timestamp": "proof/timestamp",
                        }.items()
                    }
                    if spontaneous_both
                    else {}
                )
            workspace = {
                "notes.md": base64.b64encode(b"ordinary notes").decode(),
                "repo/main.py": base64.b64encode(b"source retained").decode(),
            }
            for name, source_data in proof_sources.items():
                workspace[name + ".lean"] = base64.b64encode(source_data).decode()
            if spontaneous and not revised:
                workspace["uniqueness.lean"] = base64.b64encode(source).decode()
            return AttemptResult(
                Result(Outcome.SUCCEEDED, 0, {"tool": 1}, 1),
                {
                    "stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n",
                    "stderr": b"binary diagnostic: \xff",
                    "candidate/result.json": candidate,
                    "candidate/workspace.json": json.dumps(workspace).encode(),
                },
            )

    def execute(root, episode, inputs, source):
        cases = json.loads((SCRIPT.parent / "fixture/references.json").read_bytes())
        raw = {}
        for name in inputs:
            case = cases[name]
            code = int(
                case["kind"] == "invalid"
                or (case["kind"] == "repeat" and b"ACCEPT_CURRENT = False" in source)
            )
            value = dict(case.get("expected", {}))
            if b"KEEP_LABEL = False" in source and case["kind"] == "migrate":
                value.pop("label", None)
            raw[name + "/stdout"] = b"" if code else json.dumps(value).encode()
            raw[name + "/stderr"] = b""
            raw[name + "/execution.json"] = json.dumps(
                {
                    "returncode": code,
                    "timed_out": False,
                    "output_limited": False,
                    "elapsed_ns": 1,
                }
            ).encode()
        return AttemptResult(Result(Outcome.SUCCEEDED, 0, {"batch": 1}, 1), raw)

    monkeypatch.setitem(scope, "Sandbox", Workspace)
    monkeypatch.setitem(scope["M5"], "execute", execute)
    monkeypatch.setitem(scope["P"]["M5"], "execute", execute)
    return demo, root, bundle


def test_live_worker_client_pins_the_strict_command_schema():
    demo = runpy.run_path(str(SCRIPT))
    client = demo["local_client"](
        "qwen3.8:27b",
        base_url="http://127.0.0.1:11434/v1",
        max_tokens=1536,
        timeout=180,
    )
    parameters = client.parameters
    assert parameters["reasoning_effort"] == "none"
    assert parameters["temperature"] == 0
    assert parameters["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "worker_command",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }
    assert json.loads(client.snapshot.data)["parameters"] == parameters


def test_multistep_episode_reuses_one_sandbox(tmp_path, monkeypatch):
    demo = runpy.run_path(str(SCRIPT))
    instances = []

    class Sandbox:
        def __init__(self, *_):
            self.calls = 0
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __call__(self, *_):
            self.calls += 1

    def workflow(*_, environment, **__):
        environment(None, b"first command")
        environment(None, b"second command")
        return {"exit_status": "Submitted"}

    globals_ = demo["propose"].__globals__
    monkeypatch.setitem(globals_, "Sandbox", Sandbox)
    monkeypatch.setitem(globals_, "run_workflow", workflow)
    monkeypatch.setitem(demo["R"], "witness", lambda *_: None)
    demo["propose"](
        tmp_path,
        type("Episode", (), {"episode_id": "initial"})(),
    )
    assert len(instances) == 1
    assert instances[0].calls == 2


def test_sandbox_uses_the_episode_timeout_for_container_and_process(
    tmp_path, monkeypatch
):
    demo, root, _ = scripted(tmp_path, monkeypatch, "csv", Condition.A)
    episode = Episode(
        "timed",
        "Submit the fixture.",
        (),
        environment=demo["SANDBOX_ID"],
        container_timeout_seconds=321,
    )
    sandbox = demo["Sandbox"](root / "ledger", episode)
    calls = []

    def run(args, *_args, **_kwargs):
        calls.append(args)
        code = 1 if args[:2] == ["container", "exists"] else 0
        return subprocess.CompletedProcess(args, code, b"", b"")

    monkeypatch.setattr(sandbox_module, "require_runtime", lambda: {})
    monkeypatch.setattr(sandbox_module, "_run", run)
    monkeypatch.setattr(sandbox_module, "input_files", lambda *_: {})
    sandbox._prepare(True)
    launch = next(args for args in calls if args[0] == "run")
    assert "--timeout=321" in launch
    assert launch[-2:] == ["sleep", "351"]


def test_unknown_live_attempt_blocks_before_runtime_poll(tmp_path, monkeypatch):
    demo, _, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.A)
    client = demo["local_client"](
        "qwen3.8:27b",
        base_url="http://127.0.0.1:11434/v1",
        max_tokens=1536,
        timeout=180,
    )
    root = tmp_path / "live-unknown"
    demo["initialize"](
        root,
        "csv",
        Condition.A,
        bundle,
        model_client=client,
        model_runtime=b"pinned runtime",
    )
    with Ledger.open(root / "ledger") as ledger:
        host = demo["M2"]["Experiment"](ledger, ledger.start_session(), root)
        episode = demo["prepare"](host, "csv", Condition.A, "initial", None, {}, client)
        assert episode.container_timeout_seconds == (
            4 * client.timeout_seconds
            + (7 + 2 * 4) * PODMAN_COMMAND_TIMEOUT_SECONDS
            + 60
        )
        session = ledger.start_session()
        request = Request(
            Origin("episode/initial/model/1", "model", client.service_id, "1", {}),
            ledger.project,
        )
        ledger.reserve(session, request, {"model": 1})
        ledger.begin(session, request)

    def unexpected_poll(_client):
        pytest.fail("runtime metadata was polled before unknown work was blocked")

    monkeypatch.setitem(demo, "local_runtime", unexpected_poll)
    with pytest.raises(UnknownOutcome):
        demo["demonstrate"]("start", root, bundle, model_client=client)
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.accounting()["model"].reserved == 1


def assert_recovered(report, family, condition):
    assert report["old_candidate"]["old_receipt"] == "stale"
    assert report["old_candidate"]["status"] == "rejected"
    assert report["candidate"]["status"] == "accepted"
    assert all(report["candidate"]["checks"].values())
    assert report["qualified"] is True
    assert report["spent"]["model"] == report["spent"]["tool"] == 2
    assert report["spent"]["proof"] == (
        (2 if family == "csv" else 1) if condition == Condition.E else 0
    )
    assert report["proof_executions"] == report["spent"]["proof"]
    assert not any(report["reserved"].values())
    if condition == Condition.E:
        assert all(
            value["status"] == "supported" for value in report["support"].values()
        )
        for candidates in report["matrix"].values():
            for name, candidate in candidates.items():
                assert candidate["support"]["status"] == "supported"
                assert candidate["decision"] == (
                    "accepted" if name in ("correct", "complete") else "rejected"
                )
    else:
        assert report["support"] == report["matrix"] == {}


@pytest.mark.parametrize("family", ["csv", "migration"])
@pytest.mark.parametrize("condition", list(Condition))
def test_full_recovery_keeps_task_checks_costs_and_visibility(
    tmp_path, monkeypatch, family, condition
):
    demo, root, bundle = scripted(tmp_path, monkeypatch, family, condition)
    started = demo["demonstrate"]("start", root, bundle)
    assert started["candidate"]["status"] == "accepted"
    resumed = demo["demonstrate"]("resume", root, bundle)
    assert_recovered(resumed, family, condition)
    with Ledger.open(root / "ledger") as ledger:
        captures = {
            o.origin.operation_id: o
            for o in ledger.history()
            if o.origin.kind == "worker-context"
        }
        initial, revised = (
            captures["worker-context/initial"],
            captures["worker-context/revised"],
        )
        for event in (initial, revised):
            files = {
                name: ledger.read_artifact(ref) for name, ref in event.artifacts.items()
            }
            assert "references.json" not in files and "contracts.json" not in files
            assert ("history.jsonl" in files) == (condition >= Condition.B)
            assert ("model.py" in files) == (condition >= Condition.C)
            assert ("dependencies.json" in files) == (condition >= Condition.D)
            assert ("proof-work.json" in files) == (condition == Condition.E)
        initial_bytes = b"\n".join(
            ledger.read_artifact(ref) for ref in initial.artifacts.values()
        )
        assert b"offset-v2" not in initial_bytes and b"repetition" not in initial_bytes
        if condition >= Condition.B:
            rows = [
                json.loads(line)
                for line in ledger.read_artifact(
                    revised.artifacts["history.jsonl"]
                ).splitlines()
            ]
            assert [r["kind"] for r in rows] == [
                "disclosure",
                "model",
                "tool",
                "disclosure",
            ]
            assert (
                base64.b64decode(rows[2]["response_base64"]["stderr"])
                == b"binary diagnostic: \xff"
            )
        if condition >= Condition.C:
            assert initial.artifacts["model.py"] != revised.artifacts["model.py"]
        if condition >= Condition.D:
            dependencies = json.loads(
                ledger.read_artifact(revised.artifacts["dependencies.json"])
            )
            assert dependencies["initial"]["applicability"] == "stale"
            assert dependencies["current"]["applicability"] == "current"
            assert dependencies["current"]["validation"] == "unproved"
        episodes = [o for o in ledger.history() if o.origin.kind == "episode"]
        episode_specs = [
            json.loads(ledger.read_artifact(event.artifacts["episode.json"]))["episode"]
            for event in episodes
        ]
        assert [episode["max_steps"] for episode in episode_specs] == [4, 4]
        assert all(
            "printf '%s\\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT;"
            in episode["objective"]
            for episode in episode_specs
        )
        spec = json.loads(ledger.read_artifact(episodes[1].artifacts["episode.json"]))
        restored = spec["episode"]["workspace"]
        assert restored["name"] in episodes[1].origin.inputs
        workspace = json.loads(
            ledger.read_artifact(episodes[1].origin.inputs[restored["name"]])
        )
        assert base64.b64decode(workspace["notes.md"]) == b"ordinary notes"
    before = (root / "worker-dispatches.jsonl").read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("completed boundary was dispatched again")

    monkeypatch.setitem(demo["demonstrate"].__globals__, "Sandbox", forbidden)
    monkeypatch.setattr(proofs, "_verify", forbidden)
    again = demo["demonstrate"]("resume", root, bundle)
    assert_recovered(again, family, condition)
    assert again["new_operations"] == 0
    assert again["spent"] == resumed["spent"]
    assert (root / "worker-dispatches.jsonl").read_bytes() == before


def test_unknown_worker_outcome_blocks_recovery_and_retains_reservation(
    tmp_path, monkeypatch
):
    demo, root, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.A)

    def lost(*args):
        raise RuntimeError("lost worker")

    monkeypatch.setitem(demo["demonstrate"].__globals__, "Sandbox", lost)
    with pytest.raises(RuntimeError, match="lost worker"):
        demo["demonstrate"]("start", root, bundle)
    with pytest.raises(UnknownOutcome):
        demo["demonstrate"]("start", root, bundle)
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.accounting()["tool"].reserved == 1
        assert ledger.accounting()["model"].spent == 1


def test_baseline_can_request_proof_work_and_receives_its_charged_result(
    tmp_path, monkeypatch
):
    demo, root, bundle = scripted(
        tmp_path, monkeypatch, "csv", Condition.A, spontaneous=True
    )
    first = demo["demonstrate"]("start", root, bundle)
    assert first["spontaneous_proofs"]["uniqueness"]["status"] == "passed"
    resumed = demo["demonstrate"]("resume", root, bundle)
    assert resumed["spent"]["proof"] == 1
    with Ledger.open(root / "ledger") as ledger:
        context = next(
            o
            for o in ledger.history()
            if o.origin.operation_id == "worker-context/revised"
        )
        results = json.loads(
            ledger.read_artifact(context.artifacts["proof-results.json"])
        )
        assert results["uniqueness"]["status"] == "passed"
        assert "proof-work.json" not in context.artifacts


def test_e_condition_budget_covers_optional_proofs_in_both_phases(
    tmp_path, monkeypatch
):
    demo, root, bundle = scripted(
        tmp_path,
        monkeypatch,
        "csv",
        Condition.E,
        spontaneous_both=True,
    )
    started = demo["demonstrate"]("start", root, bundle)
    assert started["spent"]["proof"] == 4
    resumed = demo["demonstrate"]("resume", root, bundle)
    assert resumed["qualified"] is True
    assert resumed["spent"]["proof"] == 6


def test_failed_e_proof_retains_cost_and_cannot_qualify(tmp_path, monkeypatch):
    from test_proof_receipts import boundary

    demo, root, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.E)
    boundary(monkeypatch, status=proofs.ProofStatus.UNPROVED)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="proof is unproved"):
            demo["demonstrate"]("start", root, bundle)
        with Ledger.open(root / "ledger") as ledger:
            assert ledger.accounting()["proof"].spent == 1
            assert ledger.accounting()["model"].spent == 1
            assert not any(o.origin.kind == "revision" for o in ledger.history())


def test_missing_treatment_checkpoint_blocks_before_new_work(tmp_path, monkeypatch):
    demo, root, bundle = scripted(tmp_path, monkeypatch, "migration", Condition.A)
    scope = demo["demonstrate"].__globals__
    original = scope["record"]

    def interrupted(host, name, *args):
        if name == "checkpoint":
            raise RuntimeError("checkpoint interrupted")
        return original(host, name, *args)

    monkeypatch.setitem(scope, "record", interrupted)
    with pytest.raises(RuntimeError, match="checkpoint interrupted"):
        demo["demonstrate"]("start", root, bundle)
    monkeypatch.setitem(scope, "record", original)
    before = (root / "worker-dispatches.jsonl").read_bytes()
    with pytest.raises(ValueError, match="treatment checkpoint"):
        demo["demonstrate"]("resume", root, bundle)
    assert (root / "worker-dispatches.jsonl").read_bytes() == before


@pytest.mark.parametrize("family", ["csv", "migration"])
@pytest.mark.parametrize(
    "helper", ["m2/experiments.py", "m4/demo.py", "m5/recovery.py", "m5/demo.py"]
)
def test_changed_shared_helper_blocks_resume_before_dispatch(
    tmp_path, monkeypatch, family, helper
):
    demo, root, bundle = scripted(tmp_path, monkeypatch, family, Condition.A)
    demo["demonstrate"]("start", root, bundle)
    before = (root / "worker-dispatches.jsonl").read_bytes()
    changed = SCRIPT.parent.parent / helper
    read_bytes = Path.read_bytes

    def changed_bytes(path):
        data = read_bytes(path)
        return data + b"\n# changed host helper\n" if path == changed else data

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    with pytest.raises(ValueError, match="fixture or host changed"):
        demo["demonstrate"]("resume", root, bundle)
    assert (root / "worker-dispatches.jsonl").read_bytes() == before
    with Ledger.open(root / "ledger") as ledger:
        assert ledger.accounting()["model"].spent == 1
        assert ledger.accounting()["tool"].spent == 1


@pytest.mark.parametrize("adapter", ["local_model", "chat_completions"])
def test_changed_model_adapter_blocks_resume_before_dispatch(
    tmp_path, monkeypatch, adapter
):
    demo, root, bundle = scripted(tmp_path, monkeypatch, "csv", Condition.A)
    demo["demonstrate"]("start", root, bundle)
    before = (root / "worker-dispatches.jsonl").read_bytes()
    changed = (
        Path(demo["LOCAL"]["__file__"])
        if adapter == "local_model"
        else Path(demo["chat_completions"].__file__)
    )
    read_bytes = Path.read_bytes

    def changed_bytes(path):
        data = read_bytes(path)
        return data + b"\n# changed model adapter\n" if path == changed else data

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    with pytest.raises(ValueError, match="fixture or host changed"):
        demo["demonstrate"]("resume", root, bundle)
    assert (root / "worker-dispatches.jsonl").read_bytes() == before


@pytest.mark.proof
@pytest.mark.skipif(
    os.environ.get("WARRANTED_PROOF_TESTS") != "1",
    reason="requires native containers and the pinned proof bundle",
)
@pytest.mark.parametrize("family", ["csv", "migration"])
@pytest.mark.parametrize("condition", [Condition.E])
def test_native_treatments_survive_kill_and_two_resumes(tmp_path, family, condition):
    root = Path(os.environ.get("WARRANTED_M5_TREATMENTS", tmp_path)) / (
        family + "-" + condition
    )
    bundle = os.environ["WARRANTED_PROOF_BUNDLE"]

    def invoke(stage, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                stage,
                str(root),
                "--family",
                family,
                "--condition",
                condition,
                "--bundle",
                bundle,
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )

    first = invoke("start", "--crash")
    assert first.returncode == -signal.SIGKILL, first.stderr
    for index in range(2):
        completed = invoke("resume")
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)
        assert_recovered(report, family, condition)
        if index:
            assert report["new_operations"] == 0
    with Ledger.open(root / "ledger") as ledger:
        workspace = submitted_files(ledger, "revised")["workspace.json"]
        files = json.loads(ledger.read_artifact(workspace.artifact))
        assert base64.b64decode(files["notes.md"]) == (
            b"Using offset 60\nUsing offset 0\n"
            if family == "csv"
            else b"Migration initial\nMigration revised\n"
        )
