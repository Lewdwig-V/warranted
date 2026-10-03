"""The configuration migration fixture through the public task layer.

The M5 candidates, reference cases, and approved revision run as task-layer tasks;
see docs/fixtures/config-migration.md. Jobs run locally here; the container test
runs one candidate through rootless Podman.
"""

import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_experimental_mystery import ENVIRONMENT, LocalJobs, Model, Script

from warranted import (
    CheckContext,
    PodmanJobs,
    Project,
    Revision,
    RunConfig,
    RunOutcome,
    TaskSpec,
    VerdictStatus,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/m7/migration"
COMPLETE = (EXAMPLE.parent.parent / "m5/fixture/complete.py").read_text()
CANDIDATES = {
    "complete": COMPLETE,
    "one-way": COMPLETE.replace("ACCEPT_CURRENT = True", "ACCEPT_CURRENT = False"),
    "drop-label": COMPLETE.replace("KEEP_LABEL = True", "KEEP_LABEL = False"),
}
INITIAL = (
    "repository_integrity",
    "output_schema",
    "renaming",
    "legacy_label",
    "input_rejection",
)
# M5's matrix: (legacy_label, repetition) per candidate; the rest always pass.
MATRIX = {
    "one-way": (True, False),
    "drop-label": (False, True),
    "complete": (True, True),
}
CONFIG = RunConfig("scripted-model-v1", max_steps=2)


def load_domain():
    path = EXAMPLE / "domain.py"
    spec = importlib.util.spec_from_file_location("m7_migration_domain", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MIGRATION = load_domain()
TASK = TaskSpec.load(EXAMPLE / "task.toml")
REVISED = Revision.load(EXAMPLE / "revisions/revised.toml").apply(TASK)


def submit(name):
    return {"migrate.py": CANDIDATES[name]}


def check(task, payload, jobs=None):
    jobs = jobs or LocalJobs()
    ctx = CheckContext(
        {"result.json": json.dumps(payload).encode()}, task.inputs, task.private, jobs
    )
    return MIGRATION.Migration().check(ctx), ctx, jobs


def project(tmp_path, plan, jobs=None):
    script, jobs = Script(plan), jobs or LocalJobs()
    created = Project.create(
        tmp_path / "project",
        MIGRATION.MigrationDomain(),
        {"model": 40, "tool": 40, "check": 40},
        environment=script,
        environment_id=ENVIRONMENT,
        jobs=jobs,
    )
    return created, script, jobs


@pytest.mark.parametrize("name", MATRIX)
def test_the_matrix_under_both_contracts(name):
    legacy, repetition = MATRIX[name]
    initial, _, _ = check(TASK, submit(name))
    revised, _, _ = check(REVISED, submit(name))
    # Feedback shows only the obligations of the contract in force.
    assert set(initial.feedback["obligations"]) == set(INITIAL)
    assert set(revised.feedback["obligations"]) == {*INITIAL, "repetition"}
    for obligation in INITIAL:
        expected = legacy if obligation == "legacy_label" else True
        assert initial.feedback["obligations"][obligation] is expected
    assert revised.feedback["obligations"]["repetition"] is repetition
    # Repetition is diagnostic under the initial contract: host-only, never a gate.
    assert initial.host_only["obligations"]["repetition"] is repetition
    assert (initial.status is VerdictStatus.PASSED) is legacy
    assert (revised.status is VerdictStatus.PASSED) is (legacy and repetition)


def test_each_case_runs_in_its_own_job():
    _, ctx, jobs = check(TASK, submit("complete"))
    cases = json.loads(TASK.private["references.json"])
    assert jobs.calls == len(cases) == len(ctx.job_log)
    assert all(job["timeout_seconds"] == 2 for job in ctx.job_log)


@pytest.mark.parametrize(
    "payload",
    [
        {"migrate.py": COMPLETE, "example_test.py": "print('ok')"},  # extra path
        {"migrate.py": ""},
        {"other.py": COMPLETE},
        b'{"migrate.py": "x", "migrate.py": "y"}',  # duplicate key
    ],
)
def test_an_invalid_patch_fails_integrity_without_running(payload):
    jobs = LocalJobs()
    ctx = CheckContext(
        {
            "result.json": payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode()
        },
        TASK.inputs,
        TASK.private,
        jobs,
    )
    verdict = MIGRATION.Migration().check(ctx)
    assert verdict.status is VerdictStatus.REJECTED
    assert verdict.feedback["obligations"]["repository_integrity"] is False
    assert jobs.calls == 0


def test_a_timeout_is_a_failed_case_not_input_rejection():
    # A nonzero exit without output would pass input rejection; a timeout must not.
    cases = json.loads(TASK.private["references.json"])
    one = {"unsupported": cases["unsupported"]}
    task = replace(
        TASK, private={**TASK.private, "references.json": json.dumps(one).encode()}
    )
    verdict, _, _ = check(task, {"migrate.py": "while True:\n    pass\n"})
    assert verdict.status is VerdictStatus.REJECTED
    assert verdict.host_only["cases"]["unsupported"] == {"input_rejection": False}


def test_a_contract_naming_an_unknown_obligation_is_a_checker_fault():
    task = replace(
        TASK,
        private={**TASK.private, "contract.json": b'{"obligations": ["fast"]}'},
    )
    with pytest.raises(ValueError, match="obligation"):
        check(task, submit("complete"))


def test_scheduled_revision_after_a_rejected_first_submission(tmp_path):
    task = TaskSpec.load(EXAMPLE / "scheduled.toml")
    proj, script, _ = project(tmp_path, [submit("drop-label"), submit("complete")])
    model = Model()
    result = proj.start(task, CONFIG, model)
    assert [s.decision for s in result.submissions] == ["rejected", "accepted"]
    second = script.episodes[1]
    assert "idempotence" in second.objective.lower() or "repeat" in second.objective
    assert "references.json" not in second.files
    assert proj.resume(result.run_id, model) == result


def test_an_accepted_one_way_converter_is_rejected_after_the_revision(tmp_path):
    proj, script, _ = project(tmp_path, [submit("one-way")])
    model = Model()
    before = proj.start(TASK, CONFIG, model)
    assert before.outcome is RunOutcome.ACCEPTED
    proj.revise(TASK.id, Revision.load(EXAMPLE / "revisions/revised.toml"))
    with pytest.raises(ValueError):
        proj.resume(before.run_id, model)
    assert proj.status(before.run_id).outcome is RunOutcome.ACCEPTED
    script.plan[:] = [submit("one-way"), submit("complete")]
    after = proj.start(TASK, CONFIG, model)
    assert [s.decision for s in after.submissions] == ["rejected", "accepted"]


def test_a_job_runner_failure_is_an_infrastructure_failure(tmp_path):
    proj, _, _ = project(tmp_path, [submit("complete")], jobs=LocalJobs(fail=True))
    result = proj.start(TASK, CONFIG, Model())
    assert result.outcome is RunOutcome.INFRASTRUCTURE_FAILURE


@pytest.mark.container
@pytest.mark.skipif(
    os.environ.get("WARRANTED_CONTAINER_TESTS") != "1",
    reason="set WARRANTED_CONTAINER_TESTS=1 to run rootless Podman tests",
)
def test_the_matrix_in_containers():
    jobs = PodmanJobs()
    for name in MATRIX:
        legacy, repetition = MATRIX[name]
        verdict, _, _ = check(REVISED, submit(name), jobs)
        assert (verdict.status is VerdictStatus.PASSED) is (legacy and repetition)
