"""Prototype task layer: run lifecycle, outcomes, feedback, and resume."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from warranted._ledger import Ledger, Outcome, Result
from warranted._tasks import (
    FEEDBACK_LIMIT,
    CheckContext,
    Project,
    RunConfig,
    RunOutcome,
    TaskSpec,
    Verdict,
    VerdictStatus,
    _source_files,
    domain_identity,
)
from warranted._worker import AttemptResult

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/m7/csv"
CANDIDATES = json.loads(
    (EXAMPLE.parent.parent / "m2/fixture/candidates.json").read_bytes()
)
ENVIRONMENT = "scripted-environment-v1"


def load_domain():
    path = EXAMPLE / "domain.py"
    spec = importlib.util.spec_from_file_location("m7_csv_domain", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CSV = load_domain()


class Model:
    """Scripted model boundary: always asks for one command."""

    def __init__(self, name="scripted-model-v1"):
        self.model, self.calls = name, 0

    def __call__(self, request, payload):
        self.calls += 1
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"model": 1}, 1),
            {"response": json.dumps({"command": "work"}).encode()},
        )


class Script:
    """Scripted environment factory. `plan[i]` is episode i's candidate name,
    `None` for no submission, or an exception to raise mid-dispatch."""

    def __init__(self, plan):
        self.plan, self.calls, self.episodes = list(plan), 0, []

    def __call__(self, ledger_root, episode):
        self.episodes.append(episode)
        index = int(episode.episode_id.rsplit("-s", 1)[1]) - 1
        script = self

        class Environment:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __call__(self, request, payload):
                script.calls += 1
                step = script.plan[index]
                if isinstance(step, BaseException):
                    raise step
                if step is None:
                    return AttemptResult(
                        Result(Outcome.SUCCEEDED, 0, {"tool": 1}, 1),
                        {"stdout": b"still working\n", "stderr": b""},
                    )
                workspace = {"notes.md": "c2NyYXRjaA=="}
                return AttemptResult(
                    Result(Outcome.SUCCEEDED, 0, {"tool": 1}, 1),
                    {
                        "stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n",
                        "stderr": b"",
                        "candidate/result.json": json.dumps(CANDIDATES[step]).encode(),
                        "candidate/workspace.json": json.dumps(workspace).encode(),
                    },
                )

        return Environment()


def project(tmp_path, plan, domain=None):
    script = Script(plan)
    created = Project.create(
        tmp_path / "project",
        domain or CSV.CsvDomain(),
        {"model": 40, "tool": 40, "check": 40},
        environment=script,
        environment_id=ENVIRONMENT,
    )
    return created, script


TASK = TaskSpec.load(EXAMPLE / "task.toml")
CONFIG = RunConfig("scripted-model-v1", max_steps=2)


def test_correct_first_submission_is_accepted_through_the_gate(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    result = proj.start(TASK, CONFIG, Model())
    assert result.outcome is RunOutcome.ACCEPTED
    assert [s.decision for s in result.submissions] == ["accepted"]
    assert result.submissions[0].verdicts == {"transformation": VerdictStatus.PASSED}


def test_rejection_feedback_reaches_the_next_episode_without_private_data(tmp_path):
    proj, script = project(tmp_path, ["wrong-offset", "correct"])
    result = proj.start(TASK, CONFIG, Model())
    assert result.outcome is RunOutcome.ACCEPTED
    assert [s.decision for s in result.submissions] == ["rejected", "accepted"]
    second = script.episodes[-1]
    assert set(second.files) == {
        "input.csv",
        "worker-task.md",
        "offset-v1",
        "definition-v1",
        "feedback-001.json",
    }
    assert second.workspace is not None and second.continues is not None
    with Ledger.open(proj.ledger_root) as ledger:
        feedback = json.loads(
            ledger.read_artifact(second.files["feedback-001.json"].artifact)
        )
    assert feedback["checks"]["transformation"]["status"] == "rejected"
    obligations = feedback["checks"]["transformation"]["feedback"]["obligations"]
    assert obligations["utc_timestamps"] is False
    assert "offset" not in json.dumps(feedback)  # host-only verdict data stays out


def test_budget_exhausted_after_rejections_is_rejected(tmp_path):
    proj, _ = project(tmp_path, ["wrong-offset", "empty", "dropped-row"])
    result = proj.start(TASK, CONFIG, Model())
    assert result.outcome is RunOutcome.REJECTED
    assert len(result.submissions) == TASK.submissions


def test_no_submission_is_incomplete(tmp_path):
    proj, _ = project(tmp_path, [None])
    result = proj.start(TASK, CONFIG, Model())
    assert result.outcome is RunOutcome.INCOMPLETE
    assert result.submissions == ()


def test_resume_reuses_completed_work_without_new_attempts(tmp_path):
    proj, script = project(tmp_path, ["wrong-offset", "correct"])
    model = Model()
    first = proj.start(TASK, CONFIG, model)
    calls = (model.calls, script.calls)
    with Ledger.open(proj.ledger_root) as ledger:
        spent = {k: b.spent for k, b in ledger.accounting().items()}
    again = proj.resume(first.run_id, model)
    assert again == first
    assert (model.calls, script.calls) == calls
    with Ledger.open(proj.ledger_root) as ledger:
        assert {k: b.spent for k, b in ledger.accounting().items()} == spent
        assert spent["check"] == 2


def test_two_starts_are_independent_runs(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    first = proj.start(TASK, CONFIG, Model())
    second = proj.start(TASK, CONFIG, Model())
    assert first.run_id != second.run_id
    assert proj.runs() == (first.run_id, second.run_id)
    assert first.outcome is second.outcome is RunOutcome.ACCEPTED


def test_host_death_mid_dispatch_leaves_the_run_unknown_without_retry(tmp_path):
    proj, script = project(tmp_path, [RuntimeError("host killed mid-dispatch")])
    model = Model()
    with pytest.raises(RuntimeError, match="host killed"):
        proj.start(TASK, CONFIG, model)
    run_id = proj.runs()[0]
    calls = script.calls
    result = proj.resume(run_id, model)
    assert result.outcome is RunOutcome.UNKNOWN
    assert script.calls == calls


class Interrupting:
    version = "1"

    def __init__(self):
        self.calls = 0

    def check(self, ctx):
        self.calls += 1
        raise KeyboardInterrupt  # stands in for host death during a check


class Unsupported:
    version = "1"

    def check(self, ctx: CheckContext) -> Verdict:
        return Verdict(VerdictStatus.UNSUPPORTED, {"error": "cannot assess"})


class Crashing:
    version = "1"

    def check(self, ctx: CheckContext) -> Verdict:
        raise OSError("checker toolchain missing")


class Domain:
    name = "scripted"
    version = "1"
    worker_image = "f" * 64  # a pinned local image ID; never run here

    def __init__(self, checker):
        self.checkers = {"transformation": checker}


def test_host_death_during_a_check_leaves_it_unknown_and_never_reruns_it(tmp_path):
    checker = Interrupting()
    proj, _ = project(tmp_path, ["correct"], Domain(checker))
    with pytest.raises(KeyboardInterrupt):
        proj.start(TASK, CONFIG, Model())
    result = proj.resume(proj.runs()[0], Model())
    assert result.outcome is RunOutcome.UNKNOWN
    assert checker.calls == 1


@pytest.mark.parametrize(
    "checker, outcome",
    [
        (Unsupported(), RunOutcome.UNSUPPORTED),
        (Crashing(), RunOutcome.INFRASTRUCTURE_FAILURE),
    ],
)
def test_checker_outcomes_stay_distinct(tmp_path, checker, outcome):
    proj, _ = project(tmp_path, ["correct"], Domain(checker))
    assert proj.start(TASK, CONFIG, Model()).outcome is outcome


def test_resume_refuses_a_changed_task_model_or_domain(tmp_path):
    proj, script = project(tmp_path, ["correct"])
    run_id = proj.start(TASK, CONFIG, Model()).run_id
    changed = TaskSpec(
        TASK.id,
        TASK.objective + " Changed.",
        TASK.inputs,
        TASK.private,
        TASK.checks,
        TASK.submissions,
    )
    with pytest.raises(ValueError, match="task differs"):
        proj.resume(run_id, Model(), task=changed)
    with pytest.raises(ValueError, match="model boundary differs"):
        proj.resume(run_id, Model("another-model"))

    class Revised(CSV.CsvDomain):
        version = "0.2"

    with pytest.raises(ValueError, match="domain or environment differs"):
        Project(proj.root, Revised(), environment=script, environment_id=ENVIRONMENT)


def test_task_files_cannot_overlap_or_use_reserved_names():
    with pytest.raises(ValueError, match="both worker-visible and private"):
        TaskSpec("t", "o", {"a.json": b""}, {"a.json": b""}, ("c",), 1)
    with pytest.raises(ValueError, match="reserved"):
        TaskSpec("t", "o", {"feedback-001.json": b""}, {}, ("c",), 1)
    for name in ("result.json", "responses"):
        with pytest.raises(ValueError, match="reserved"):
            TaskSpec("t", "o", {name: b""}, {}, ("c",), 1)


def test_checks_and_run_configuration_are_validated():
    with pytest.raises(ValueError, match="named required checks"):
        TaskSpec("t", "o", {}, {}, ("c", "c"), 1)
    with pytest.raises(ValueError, match="named required checks"):
        TaskSpec("t", "o", {}, {}, (3,), 1)
    with pytest.raises(ValueError, match="model identity"):
        RunConfig(" ")
    with pytest.raises(ValueError, match="max_steps"):
        RunConfig("m", max_steps=0)


def test_deeply_nested_candidate_is_rejected_with_feedback():
    nested = ("[" * 20000 + "]" * 20000).encode()
    verdict = CSV.Transformation().check(
        CheckContext(
            {"result.json": nested},
            TASK.inputs,
            TASK.private,
        )
    )
    assert verdict.status is VerdictStatus.REJECTED
    assert "nested" in verdict.feedback["error"]


class InfrastructureScript(Script):
    def __call__(self, ledger_root, episode):
        script = self

        class Environment:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __call__(self, request, payload):
                script.calls += 1
                return AttemptResult(
                    Result(Outcome.INFRASTRUCTURE_FAILURE, None, {"tool": 1}, 1),
                    {
                        "stdout": b"",
                        "stderr": b"",
                        "diagnostic": b"container control failed",
                    },
                )

        return Environment()


class FailingModel(Model):
    def __call__(self, request, payload):
        self.calls += 1
        return AttemptResult(
            Result(Outcome.FAILED, 1, {"model": 1}, 1),
            {"response": b"", "error.json": b'{"finish_reason": "length"}'},
        )


def test_worker_infrastructure_failure_is_a_typed_outcome_on_every_resume(tmp_path):
    script = InfrastructureScript([])
    proj = Project.create(
        tmp_path / "project",
        CSV.CsvDomain(),
        {"model": 40, "tool": 40, "check": 40},
        environment=script,
        environment_id=ENVIRONMENT,
    )
    model = Model()
    first = proj.start(TASK, CONFIG, model)
    assert first.outcome is RunOutcome.INFRASTRUCTURE_FAILURE
    calls = (model.calls, script.calls)
    assert proj.resume(first.run_id, model) == first
    assert (model.calls, script.calls) == calls


def test_failed_model_attempt_ends_the_episode_without_submission(tmp_path):
    proj, script = project(tmp_path, ["correct"])
    result = proj.start(TASK, CONFIG, FailingModel())
    assert result.outcome is RunOutcome.INCOMPLETE
    assert script.calls == 0


def test_an_unknown_operation_in_one_run_does_not_block_another(tmp_path):
    proj, script = project(tmp_path, [RuntimeError("host killed mid-dispatch")])
    with pytest.raises(RuntimeError, match="host killed"):
        proj.start(TASK, CONFIG, Model())
    script.plan = ["correct"]
    calls = script.calls
    fresh = proj.start(TASK, CONFIG, Model())
    assert fresh.outcome is RunOutcome.ACCEPTED
    assert script.calls == calls + 1
    first = proj.runs()[0]
    assert proj.resume(first, Model()).outcome is RunOutcome.UNKNOWN


def test_run_scope_caps_combine_task_and_run_budgets(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    task = TaskSpec(
        TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 3, 2
    )
    config = RunConfig("scripted-model-v1", 2, {"model": 5, "tool": 4})
    result = proj.start(task, config, Model())
    with Ledger.open(proj.ledger_root) as ledger:
        assert ledger.scopes()[f"run/{result.run_id}"] == {
            "check": 2,
            "model": 5,
            "tool": 4,
        }


def test_run_model_budget_ends_the_run_while_the_project_has_budget(tmp_path):
    proj, _ = project(tmp_path, [None])
    config = RunConfig("scripted-model-v1", 2, {"model": 1})
    result = proj.start(TASK, config, Model())
    assert result.outcome is RunOutcome.INCOMPLETE
    assert "budget" in result.detail
    with Ledger.open(proj.ledger_root) as ledger:
        assert ledger.accounting()["model"].available > 0


def test_task_check_budget_stops_further_assessment(tmp_path):
    proj, _ = project(tmp_path, ["wrong-offset", "correct"])
    task = TaskSpec(
        TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 3, 1
    )
    result = proj.start(task, CONFIG, Model())
    assert result.outcome is RunOutcome.REJECTED
    assert [s.decision for s in result.submissions] == ["rejected"]
    assert "budget" in result.detail


def test_budgets_belong_to_their_owner(tmp_path):
    for unit in ("check", "submissions"):
        with pytest.raises(ValueError, match="belong to tasks"):
            RunConfig("m", 2, {unit: 1})
    assert RunConfig("m", 2, {"prompt_tokens": 9, "completion_tokens": 3}).budgets
    assert RunConfig("m", 2, {"probe": 4}).budgets  # a domain operation unit
    task = tmp_path / "task.toml"
    for unit in ("model", "prompt_tokens"):
        task.write_text(
            f'id = "t"\nobjective = "o"\nchecks = ["c"]\n[budgets]\n{unit} = 3\n'
        )
        with pytest.raises(ValueError, match="task budget"):
            TaskSpec.load(task)


def test_reconciliation_only_sees_the_episodes_own_scope(tmp_path):
    from warranted._worker import Episode, run_workflow

    proj, script = project(tmp_path, [RuntimeError("host killed mid-dispatch")])
    with pytest.raises(RuntimeError, match="host killed"):
        proj.start(TASK, CONFIG, Model())
    seen = []

    def reconcile(request):
        seen.append(request.origin.operation_id)
        raise AssertionError("asked to reconcile another run's operation")

    with Ledger.open(proj.ledger_root) as ledger:
        ledger.open_scope(ledger.start_session(), "run/other", {})
    script.plan = ["correct"]
    episode = Episode(
        "other-s001",
        "Submit.",
        (),
        model=CONFIG.model,
        model_service=CONFIG.model,
        environment=ENVIRONMENT,
        max_steps=2,
        scope="run/other",
    )
    with script(proj.ledger_root, episode) as environment:
        result = run_workflow(
            proj.ledger_root,
            proj.root / "graph.sqlite3",
            episode,
            model=Model(),
            environment=environment,
            reconcile=reconcile,
        )
    assert result["exit_status"] == "Submitted"
    assert seen == []


class Isolated:
    version = "1"
    isolated = True

    def __init__(self):
        self.calls = 0

    def check(self, ctx):
        self.calls += 1
        raise KeyboardInterrupt  # host death during a check


def test_an_unknown_shared_state_check_blocks_every_run(tmp_path):
    checker = Interrupting()
    proj, script = project(tmp_path, ["correct", "correct"], Domain(checker))
    with pytest.raises(KeyboardInterrupt):
        proj.start(TASK, CONFIG, Model())
    script.plan = ["correct"]
    assert proj.start(TASK, CONFIG, Model()).outcome is RunOutcome.UNKNOWN
    assert checker.calls == 1


def test_an_unknown_isolated_check_blocks_only_its_own_run(tmp_path):
    checker = Isolated()
    proj, script = project(tmp_path, ["correct"], Domain(checker))
    with pytest.raises(KeyboardInterrupt):
        proj.start(TASK, CONFIG, Model())
    checker.check = lambda ctx: Verdict(VerdictStatus.PASSED)
    assert proj.start(TASK, CONFIG, Model()).outcome is RunOutcome.ACCEPTED
    assert proj.resume(proj.runs()[0], Model()).outcome is RunOutcome.UNKNOWN


def test_check_budgets_require_isolated_checkers(tmp_path):
    proj, _ = project(tmp_path, ["correct"], Domain(Unsupported()))
    task = TaskSpec(
        TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 3, 2
    )
    with pytest.raises(ValueError, match="isolated"):
        proj.start(task, CONFIG, Model())


def domain_with(*sources):
    class Declared(CSV.CsvDomain):
        pass

    Declared.sources = (*CSV.CsvDomain.sources, *sources)
    return Declared()


def test_the_csv_domain_pins_the_evaluator_it_loads_with_runpy():
    names = [name for name, _ in _source_files(CSV.CsvDomain())]
    assert names == [
        "checker/timestamp/0/TimestampChallenge.lean",
        "checker/uniqueness/0/UniquenessChallenge.lean",
        "class/_proof_checks.py",
        "class/domain.py",
        "source/0/experiments.py",
    ]


def test_editing_adding_or_removing_a_declared_source_blocks_reopening(tmp_path):
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / "rules.py").write_text("A = 1\n")
    root = tmp_path / "project"
    options = {"environment": Script([]), "environment_id": ENVIRONMENT}
    Project.create(root, domain_with(helper), {"model": 1}, **options)
    Project(root, domain_with(helper), **options)
    for change in (
        lambda: (helper / "rules.py").write_text("A = 2\n"),
        lambda: (helper / "extra.py").write_text(""),
    ):
        change()
        with pytest.raises(ValueError, match="domain or environment differs"):
            Project(root, domain_with(helper), **options)
    (helper / "extra.py").unlink()
    (helper / "rules.py").write_text("A = 1\n")
    Project(root, domain_with(helper), **options)


def test_moving_a_declared_source_tree_keeps_the_identity(tmp_path):
    for place in ("a", "b"):
        (tmp_path / place / "lib").mkdir(parents=True)
        (tmp_path / place / "lib" / "rules.py").write_text("A = 1\n")
    assert domain_identity(domain_with(tmp_path / "a/lib")) == domain_identity(
        domain_with(tmp_path / "b/lib")
    )


def test_a_missing_declared_source_is_refused(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        domain_identity(domain_with(tmp_path / "absent"))


def test_the_project_identity_records_the_warranted_version():
    assert domain_identity(CSV.CsvDomain())["warranted"]


class Talkative:
    version = "1"
    isolated = True

    def __init__(self, size):
        self.size = size

    def check(self, ctx):
        return Verdict(VerdictStatus.PASSED, "x" * self.size, {"note": "kept"})


def talkative_domain(size):
    class Domain(CSV.CsvDomain):
        checkers = {"transformation": Talkative(size)}

    return Domain()


def test_feedback_over_the_limit_is_a_checker_fault_kept_as_host_evidence(
    tmp_path,
):
    proj, script = project(tmp_path, ["correct"], talkative_domain(FEEDBACK_LIMIT))
    result = proj.start(TASK, CONFIG, Model())
    assert result.outcome is RunOutcome.INFRASTRUCTURE_FAILURE
    with Ledger.open(proj.ledger_root) as ledger:
        [check] = [
            op for op in ledger.operations() if op.request.origin.kind == "check"
        ]
        artifacts = check.completion.observation.artifacts
        verdict = json.loads(ledger.read_artifact(artifacts["verdict.json"]))
        kept = json.loads(ledger.read_artifact(artifacts["host-only.json"]))
    assert verdict == {"status": "infrastructure_failure", "feedback": None}
    assert kept["status"] == "passed"
    assert kept["feedback"] == "x" * FEEDBACK_LIMIT
    assert kept["host_only"] == {"note": "kept"}


def test_feedback_at_the_limit_is_shown(tmp_path):
    # The recorded JSON adds two quotes and a trailing newline.
    proj, _ = project(tmp_path, ["correct"], talkative_domain(FEEDBACK_LIMIT - 3))
    assert proj.start(TASK, CONFIG, Model()).outcome is RunOutcome.ACCEPTED


def test_changed_warranted_code_blocks_reopening_even_at_the_same_version(
    tmp_path, monkeypatch
):
    import shutil

    import warranted._tasks as experimental

    package = tmp_path / "warranted"
    shutil.copytree(experimental.PACKAGE_ROOT, package)
    monkeypatch.setattr(experimental, "PACKAGE_ROOT", package)
    root = tmp_path / "project"
    options = {"environment": Script([]), "environment_id": ENVIRONMENT}
    Project.create(root, CSV.CsvDomain(), {"model": 1}, **options)
    Project(root, CSV.CsvDomain(), **options)
    acceptance = package / "_acceptance.py"
    acceptance.write_bytes(acceptance.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="domain or environment differs"):
        Project(root, CSV.CsvDomain(), **options)
