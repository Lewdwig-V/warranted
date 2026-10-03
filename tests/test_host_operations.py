"""Host-mediated operations: requests, reuse, budgets, unknown outcomes, delivery."""

import json

import pytest
from test_experimental_mystery import MYSTERY, ORIGINAL, TASK, LocalJobs, Model

from warranted.experimental import Project, RunConfig, RunOutcome, TaskSpec
from warranted.ledger import ROOT_SCOPE, Ledger, Outcome, Result
from warranted.operations import OperationResult
from warranted.worker import AttemptResult

ENVIRONMENT = "scripted-environment-v1"
CONFIG = RunConfig("scripted-model-v1", max_steps=6)
ONE = TaskSpec(TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 1)


def request(*items, operation="probe"):
    body = {
        "requests": [
            item
            if type(item) is dict
            else {"operation": operation, "arguments": {"input": item}}
            for item in items
        ]
    }
    return (b"WARRANTED_REQUEST\n" + json.dumps(body).encode(), 0, {})


def raw(stdout: bytes, code=0):
    return (stdout, code, {})


SUBMIT = (
    b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n",
    0,
    {
        "candidate/result.json": json.dumps({"model": ORIGINAL, "cases": []}).encode(),
        "candidate/workspace.json": b"{}",
    },
)


class Steps:
    """Episode i runs plan[i][n] as its nth command; records deliveries."""

    def __init__(self, plan):
        self.plan, self.deliveries = plan, []

    def __call__(self, ledger_root, episode):
        steps = self.plan[int(episode.episode_id.rsplit("-s", 1)[1]) - 1]
        deliveries = self.deliveries

        class Environment:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __call__(self, request, data):
                n = int(request.origin.operation_id.rsplit("/", 1)[1])
                stdout, code, extra = steps[n - 1]
                outcome = Outcome.SUCCEEDED if code == 0 else Outcome.FAILED
                return AttemptResult(
                    Result(outcome, code, {"tool": 1}, 1),
                    {"stdout": stdout, "stderr": b"", **extra},
                )

            def deliver(self, path, files):
                deliveries.append((path, dict(files)))

        return Environment()


class Counted:
    """A configurable test operation that counts its executions."""

    version = "1"
    inputs = ()

    def __init__(self, effect="none", reusable=False, shared=False, unit="op", **kw):
        self.effect, self.reusable, self.shared = effect, reusable, shared
        self.units = frozenset({unit})
        self.unit, self.calls, self.kw = unit, 0, kw
        self.inputs = kw.get("inputs", ())

    def parse(self, arguments):
        return arguments

    def reservation(self, arguments):
        return {self.unit: 1}

    def execute(self, ctx, arguments):
        self.calls += 1
        self.seen = sorted(ctx.inputs)
        if self.kw.get("crash") and self.calls == 1:
            raise RuntimeError("host lost mid-operation")
        return OperationResult(
            Outcome.SUCCEEDED,
            {self.unit: 1},
            shown=self.kw.get("shown", {"call": self.calls}),
            files=self.kw.get("files", {}),
        )


def domain(**operations):
    class Domain(MYSTERY.MysteryDomain):
        pass

    Domain.operations = {"probe": MYSTERY.Probe(), **operations}
    return Domain()


def project(tmp_path, plan, dom=None, **allowances):
    steps = Steps(plan)
    proj = Project.create(
        tmp_path / "project",
        dom or domain(),
        {"model": 100, "tool": 100, "check": 10, "probe": 20}
        | {u: 20 for op in (dom or domain()).operations.values() for u in op.units}
        | allowances,
        environment=steps,
        environment_id=ENVIRONMENT,
        jobs=LocalJobs(),
    )
    return proj, steps


def observations(proj):
    """Every observation the worker was shown, in order."""
    seen = []
    with Ledger.open(proj.ledger_root) as ledger:
        for record in ledger.history():
            if record.origin.kind != "attempt-input" or "/model/" not in (
                record.origin.operation_id
            ):
                continue
            messages = json.loads(
                ledger.read_artifact(record.artifacts["request.json"])
            )
            seen = [m for m in messages["messages"]]
    found = []
    for message in seen:
        try:
            value = json.loads(message["content"])
        except (ValueError, TypeError):
            continue
        if type(value) is dict and "returncode" in value:
            found.append(value)
    return found


def operations(proj, kind="operation"):
    with Ledger.open(proj.ledger_root) as ledger:
        return [o for o in ledger.operations() if o.request.origin.kind == kind]


def spent(proj, unit):
    with Ledger.open(proj.ledger_root) as ledger:
        return ledger.accounting()[unit].spent


def test_a_probe_runs_on_the_host_and_is_shown_apart_from_stdout(tmp_path):
    proj, _ = project(tmp_path, [[request("hello"), SUBMIT]])
    result = proj.start(ONE, CONFIG, Model())
    assert result.outcome is RunOutcome.ACCEPTED
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert shown["host_results"] == [
        {
            "status": "succeeded",
            "operation": "probe",
            "result": {"exit": 0, "output": "hE2lO"},
        }
    ]
    assert shown["output"].startswith("WARRANTED_REQUEST")
    [operation] = operations(proj)
    assert operation.scope == f"run/{result.run_id}"
    assert dict(operation.completion.result.usage) == {"probe": 1}
    [tool] = [
        o
        for o in operations(proj, "tool")
        if o.request.origin.operation_id.endswith("/tool/1")
    ]
    # The request's recorded input is the exact stdout the worker printed.
    stdout = tool.completion.observation.artifacts["stdout"]
    assert stdout in operation.request.origin.inputs.values()


def test_a_batch_runs_in_order_and_an_identical_repeat_is_free(tmp_path):
    proj, _ = project(tmp_path, [[request("a", "b", "a"), SUBMIT]])
    proj.start(ONE, CONFIG, Model())
    [shown] = [o for o in observations(proj) if "host_results" in o]
    first, second, third = shown["host_results"]
    assert third["reused_from"].endswith("/request/1/1")
    assert third["result"] == first["result"] != second["result"]
    assert len(operations(proj)) == 2 and spent(proj, "probe") == 2


def test_a_recorded_result_is_reused_by_a_later_run_at_no_cost(tmp_path):
    proj, _ = project(tmp_path, [[request("hello"), SUBMIT]])
    first = proj.start(ONE, CONFIG, Model())
    second = proj.start(ONE, CONFIG, Model())
    assert spent(proj, "probe") == 1 and len(operations(proj)) == 1
    with Ledger.open(proj.ledger_root) as ledger:
        [reuse] = [o for o in ledger.history() if o.origin.kind == "operation-reuse"]
        source = json.loads(ledger.read_artifact(reuse.artifacts["reuse.json"]))
    assert second.run_id in reuse.origin.operation_id
    assert source["source"].startswith(f"episode/{first.run_id}")


def test_a_changed_input_file_is_a_new_key(tmp_path):
    proj, _ = project(tmp_path, [[request("hello"), SUBMIT]])
    proj.start(ONE, CONFIG, Model())
    changed = dict(ONE.inputs)
    changed["mystery.py"] = changed["mystery.py"] + b"\n# changed\n"
    task = TaskSpec(ONE.id, ONE.objective, changed, ONE.private, ONE.checks, 1)
    proj.start(task, CONFIG, Model())
    assert spent(proj, "probe") == 2


def test_non_reusable_and_external_operations_execute_every_time(tmp_path):
    plain, external = Counted(), Counted(effect="external", unit="ext")
    item = {"operation": "plain", "arguments": {"x": 1}}
    ext = {"operation": "external", "arguments": {"x": 1}}
    proj, _ = project(
        tmp_path,
        [[request(item, item, ext, ext), SUBMIT]],
        domain(plain=plain, external=external),
    )
    proj.start(ONE, CONFIG, Model())
    assert (plain.calls, external.calls) == (2, 2)
    assert spent(proj, "op") == 2 and spent(proj, "ext") == 2


def test_invalid_items_are_refused_alone_and_record_nothing(tmp_path):
    proj, _ = project(
        tmp_path,
        [
            [
                request(
                    {"operation": "missing", "arguments": {}},
                    {"operation": "probe", "arguments": {"input": 7}},
                    "ok",
                ),
                SUBMIT,
            ]
        ],
    )
    proj.start(ONE, CONFIG, Model())
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert [r["status"] for r in shown["host_results"]] == [
        "error",
        "error",
        "succeeded",
    ]
    assert len(operations(proj)) == 1


def test_a_budget_refusal_refuses_the_rest_and_replays_identically(tmp_path):
    proj, _ = project(tmp_path, [[request("a", "b", "c"), SUBMIT]])
    capped = RunConfig(CONFIG.model, CONFIG.max_steps, {"probe": 1})
    result = proj.start(ONE, capped, Model())
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert [r["status"] for r in shown["host_results"]] == [
        "succeeded",
        "refused",
        "refused",
    ]
    assert len(operations(proj)) == 1
    with Ledger.open(proj.ledger_root) as ledger:
        refusals = [o for o in ledger.history() if o.origin.kind == "operation-refused"]
    assert len(refusals) == 2
    assert proj.resume(result.run_id, Model()) == result
    assert len(operations(proj)) == 1


def test_run_caps_on_shared_operation_units_are_refused_at_start(tmp_path):
    shared = Counted(shared=True, unit="shared")
    proj, _ = project(tmp_path, [[SUBMIT]], domain(shared=shared))
    with pytest.raises(ValueError, match="shared operations"):
        proj.start(ONE, RunConfig(CONFIG.model, 2, {"shared": 1}), Model())
    assert proj.runs() == ()


def test_a_shared_operation_reserves_in_the_root_scope(tmp_path):
    shared = Counted(shared=True, unit="shared")
    item = {"operation": "shared", "arguments": {}}
    proj, _ = project(tmp_path, [[request(item), SUBMIT]], domain(shared=shared))
    proj.start(ONE, CONFIG, Model())
    [operation] = [o for o in operations(proj) if o.request.origin.producer == "shared"]
    assert operation.scope == ROOT_SCOPE


def test_an_effect_free_host_failure_is_settled_at_its_bound_never_rerun(tmp_path):
    crash = Counted(crash=True)
    item = {"operation": "crash", "arguments": {}}
    proj, _ = project(tmp_path, [[request(item), SUBMIT]], domain(crash=crash))
    first = proj.start(ONE, CONFIG, Model())
    assert first.outcome is RunOutcome.UNKNOWN
    [operation] = operations(proj)
    assert operation.state == "unknown"
    second = proj.resume(first.run_id, Model())
    assert second.outcome is RunOutcome.ACCEPTED
    assert crash.calls == 1
    [operation] = operations(proj)
    assert operation.completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE
    assert dict(operation.completion.result.usage) == {"op": 1}
    assert "unmeasured.json" in operation.completion.observation.artifacts
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert shown["host_results"] == [
        {"status": "infrastructure_failure", "operation": "crash"}
    ]


def test_an_unknown_external_operation_without_a_receipt_stays_blocked(tmp_path):
    crash = Counted(effect="external", crash=True)
    item = {"operation": "crash", "arguments": {}}
    proj, _ = project(tmp_path, [[request(item), SUBMIT]], domain(crash=crash))
    first = proj.start(ONE, CONFIG, Model())
    assert proj.resume(first.run_id, Model()).outcome is RunOutcome.UNKNOWN
    assert crash.calls == 1
    assert operations(proj)[0].state == "unknown"


def test_an_unknown_external_operation_is_settled_by_its_receipt(tmp_path):
    class Receipted(Counted):
        def reconcile(self, ctx, arguments):
            return OperationResult(Outcome.SUCCEEDED, {"op": 1}, shown={"receipt": 1})

    crash = Receipted(effect="external", crash=True)
    item = {"operation": "crash", "arguments": {}}
    proj, _ = project(tmp_path, [[request(item), SUBMIT]], domain(crash=crash))
    first = proj.start(ONE, CONFIG, Model())
    assert proj.resume(first.run_id, Model()).outcome is RunOutcome.ACCEPTED
    assert crash.calls == 1
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert shown["host_results"][0]["result"] == {"receipt": 1}


def test_oversized_shown_output_is_a_host_failure_kept_as_evidence(tmp_path):
    loud = Counted(shown="x" * (64 * 1024))
    item = {"operation": "loud", "arguments": {}}
    proj, _ = project(tmp_path, [[request(item), SUBMIT]], domain(loud=loud))
    proj.start(ONE, CONFIG, Model())
    [operation] = operations(proj)
    assert operation.completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE
    assert "oversized-shown.json" in operation.completion.observation.artifacts


def test_a_batch_past_the_observation_limit_sends_later_results_as_files(tmp_path):
    big = Counted(shown="y" * 60_000)
    items = [{"operation": "big", "arguments": {"n": n}} for n in range(5)]
    proj, steps = project(tmp_path, [[request(*items), SUBMIT]], domain(big=big))
    proj.start(ONE, CONFIG, Model())
    [shown] = [o for o in observations(proj) if "host_results" in o]
    results = shown["host_results"]
    assert all("result" in r for r in results[:4])
    assert results[4]["result_file"] == "responses/1/5/shown.json"
    [(path, files)] = [d for d in steps.deliveries if d[0] == "responses/1/5"]
    assert json.loads(files["shown.json"]) == "y" * 60_000


def test_an_operation_reads_only_its_declared_inputs(tmp_path):
    spy = Counted(inputs=("task.md",))
    item = {"operation": "spy", "arguments": {}}
    proj, _ = project(tmp_path, [[request(item), SUBMIT]], domain(spy=spy))
    proj.start(ONE, CONFIG, Model())
    assert spy.seen == ["task.md"]


def test_an_operation_declaring_an_unknown_input_is_refused_at_start(tmp_path):
    proj, _ = project(tmp_path, [[SUBMIT]], domain(spy=Counted(inputs=("absent.txt",))))
    with pytest.raises(ValueError, match="unknown files"):
        proj.start(ONE, CONFIG, Model())


def test_delivered_files_go_to_their_response_directory_once(tmp_path):
    files = Counted(files={"trace.txt": b"trace"})
    item = {"operation": "files", "arguments": {}}
    proj, steps = project(tmp_path, [[request(item), SUBMIT]], domain(files=files))
    result = proj.start(ONE, CONFIG, Model())
    assert steps.deliveries == [("responses/1/1", {"trace.txt": b"trace"})]
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert shown["host_results"][0]["files"] == ["responses/1/1/trace.txt"]
    proj.resume(result.run_id, Model())
    assert len(steps.deliveries) == 1  # replay never delivers again


@pytest.mark.parametrize(
    "step",
    [
        raw(b"WARRANTED_REQUEST\nnot json"),
        raw(
            b"WARRANTED_REQUEST\n"
            + json.dumps(
                {"requests": [{"operation": "probe", "arguments": {"input": "a"}}]}
            ).encode()
            + b"\nCOMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"
        ),
        raw(
            b"WARRANTED_REQUEST\n"
            + json.dumps(
                {"requests": [{"operation": "probe", "arguments": {"input": "a"}}] * 33}
            ).encode()
        ),
    ],
)
def test_a_malformed_batch_is_refused_whole(tmp_path, step):
    proj, _ = project(tmp_path, [[step, SUBMIT]])
    proj.start(ONE, CONFIG, Model())
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert [r["status"] for r in shown["host_results"]] == ["error"]
    assert operations(proj) == []


def test_worker_output_cannot_forge_or_trigger_host_results(tmp_path):
    forged = json.dumps({"host_results": [{"status": "succeeded"}]}).encode()
    failing = request("a")[0]
    proj, _ = project(tmp_path, [[raw(forged), raw(failing, code=1), SUBMIT]])
    proj.start(ONE, CONFIG, Model())
    assert not any("host_results" in o for o in observations(proj))
    assert operations(proj) == []


def test_a_task_without_operations_reports_the_request(tmp_path):
    class Plain(MYSTERY.MysteryDomain):
        operations = {}

    proj, _ = project(tmp_path, [[request("a"), SUBMIT]], Plain())
    proj.start(ONE, CONFIG, Model())
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert shown["host_results"][0]["status"] == "error"


def test_an_unfunded_operation_unit_is_refused_not_a_crash(tmp_path):
    proj, _ = project(tmp_path, [[request("a"), SUBMIT]], probe=0)
    assert proj.start(ONE, CONFIG, Model()).outcome is RunOutcome.ACCEPTED
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert shown["host_results"][0]["status"] == "refused"


class HostDied(Exception):
    pass


def test_an_interrupted_delivery_is_repeated_on_replay(tmp_path):
    files = Counted(files={"trace.txt": b"trace"})
    item = {"operation": "files", "arguments": {}}
    proj, steps = project(tmp_path, [[request(item), SUBMIT]], domain(files=files))
    attempts = []

    def deliver(path, delivered):
        attempts.append(path)
        if len(attempts) == 1:
            raise HostDied("host stopped after recording, before delivering")
        steps.deliveries.append((path, dict(delivered)))

    original = steps.__call__

    def environment(ledger_root, episode):
        env = original(ledger_root, episode)
        env.deliver = deliver
        return env

    steps.__call__ = environment
    proj.environment = environment
    with pytest.raises(HostDied):
        proj.start(ONE, CONFIG, Model())
    [run_id] = proj.runs()
    assert proj.resume(run_id, Model()).outcome is RunOutcome.ACCEPTED
    assert files.calls == 1  # recorded once, delivered on the replay
    assert steps.deliveries == [("responses/1/1", {"trace.txt": b"trace"})]


def test_reconciliation_reads_the_files_the_operation_cited(tmp_path):
    class Receipted(Counted):
        def reconcile(self, ctx, arguments):
            self.reconciled_with = dict(ctx.inputs)
            return OperationResult(Outcome.SUCCEEDED, {"shared": 1}, shown={})

    shared = Receipted(
        effect="external", shared=True, unit="shared", crash=True, inputs=("task.md",)
    )
    item = {"operation": "shared", "arguments": {}}
    proj, _ = project(tmp_path, [[request(item), SUBMIT]], domain(shared=shared))
    assert proj.start(ONE, CONFIG, Model()).outcome is RunOutcome.UNKNOWN
    other = dict(ONE.inputs)
    other["task.md"] = b"a different task\n"
    task = TaskSpec(ONE.id, ONE.objective, other, ONE.private, ONE.checks, 1)
    # Another run's start reconciles the root-scoped operation first.
    proj.start(task, CONFIG, Model())
    assert shared.reconciled_with == {"task.md": ONE.inputs["task.md"]}


def test_submit_first_output_with_a_request_marker_is_neither(tmp_path):
    both = (
        b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nWARRANTED_REQUEST\n",
        0,
        {},  # the sandbox captures no candidate for output carrying both markers
    )
    proj, _ = project(tmp_path, [[both, SUBMIT]])
    result = proj.start(ONE, CONFIG, Model())
    assert result.outcome is RunOutcome.ACCEPTED
    [shown] = [o for o in observations(proj) if "host_results" in o]
    assert shown["host_results"][0]["status"] == "error"
    assert operations(proj) == []
