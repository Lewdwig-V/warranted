"""The CSV transformation fixture through the public task layer.

The M2 candidates and revisions (offset, identifier definition, annotation) run as
task-layer tasks with scheduled or project revisions; see
docs/fixtures/csv-transformation.md.
"""

import json
from dataclasses import replace

import pytest
from test_experimental_tasks import CANDIDATES, CONFIG, CSV, EXAMPLE, Model, project

from warranted import (
    CheckContext,
    Revision,
    RunOutcome,
    TaskSpec,
    VerdictStatus,
)

TASK = TaskSpec.load(EXAMPLE / "task.toml")
OBLIGATIONS = ("unique_ids", "preserved_rows", "utc_timestamps", "daily_totals")

# The M2 matrix under offset-v1: each obligation fails independently.
MATRIX = {
    "correct": (True, True, True, True),
    "wrong-offset": (True, True, False, False),
    "dropped-row": (True, False, True, False),
    "dropped-row-wrong-offset": (True, False, False, False),
    "empty": (True, False, True, False),
    "swapped-timestamps": (True, True, False, True),
}


def check(task, result):
    ctx = CheckContext(
        {"result.json": json.dumps(result).encode()}, task.inputs, task.private
    )
    return CSV.Transformation().check(ctx)


@pytest.mark.parametrize("name", MATRIX)
def test_each_obligation_is_assessed_independently(name):
    verdict = check(TASK, CANDIDATES[name])
    assert (
        tuple(verdict.feedback["obligations"][o] for o in OBLIGATIONS) == MATRIX[name]
    )
    expected = VerdictStatus.PASSED if all(MATRIX[name]) else VerdictStatus.REJECTED
    assert verdict.status is expected


@pytest.mark.parametrize("name", MATRIX)
def test_the_matrix_decides_through_the_gate(tmp_path, name):
    proj, _ = project(tmp_path, [name])
    result = proj.start(replace(TASK, submissions=1), CONFIG, Model())
    assert [s.decision for s in result.submissions] == [
        "accepted" if all(MATRIX[name]) else "rejected"
    ]


def test_offset_revision_rejects_then_accepts_the_same_bytes(tmp_path):
    task = TaskSpec.load(EXAMPLE / "offset.toml")
    proj, script = project(tmp_path, ["wrong-offset", "wrong-offset"])
    model = Model()
    result = proj.start(task, CONFIG, model)
    assert [s.decision for s in result.submissions] == ["rejected", "accepted"]
    assert "offset-v2" in script.episodes[1].files
    assert "offset-v1" not in script.episodes[1].files
    calls = (model.calls, script.calls)
    assert proj.resume(result.run_id, model) == result  # a restart repeats nothing
    assert (model.calls, script.calls) == calls


def test_an_accepted_run_keeps_its_record_when_a_revision_follows(tmp_path):
    proj, script = project(tmp_path, ["correct"])
    model = Model()
    before = proj.start(TASK, CONFIG, model)
    assert before.outcome is RunOutcome.ACCEPTED
    proj.revise(TASK.id, Revision.load(EXAMPLE / "revisions/offset-v2.toml"))
    with pytest.raises(ValueError):
        proj.resume(before.run_id, model)
    assert proj.status(before.run_id).outcome is RunOutcome.ACCEPTED
    # A new run uses offset-v2: the old answer fails, the corrected one passes.
    script.plan[:] = ["correct", "wrong-offset"]
    after = proj.start(TASK, CONFIG, model)
    assert [s.decision for s in after.submissions] == ["rejected", "accepted"]


def test_definition_revision_changes_identifier_equality():
    revised = TASK.contract(1)
    revised = Revision.load(EXAMPLE / "revisions/definition-v2.toml").apply(revised)
    # Two IDs differing only in case: unique when exact, duplicates when not.
    witness = {
        "rows": [["rA", "2025-12-31T23:30:00Z", 7], ["ra", "2026-01-01T01:00:00Z", 11]],
        "totals": {"2025-12-31": 7, "2026-01-01": 11},
    }
    assert check(TASK, witness).feedback["obligations"]["unique_ids"] is True
    assert check(revised, witness).feedback["obligations"]["unique_ids"] is False
    # The main candidate's bytes satisfy both definitions.
    assert check(TASK, CANDIDATES["correct"]).status is VerdictStatus.PASSED
    assert check(revised, CANDIDATES["correct"]).status is VerdictStatus.PASSED


def test_definition_revision_through_a_run(tmp_path):
    task = TaskSpec.load(EXAMPLE / "definition.toml")
    proj, script = project(tmp_path, ["wrong-offset", "correct"])
    result = proj.start(task, CONFIG, Model())
    assert [s.decision for s in result.submissions] == ["rejected", "accepted"]
    assert "definition-v2" in script.episodes[1].files
    assert "definition-v1" not in script.episodes[1].files


def test_annotation_revision_changes_no_calculation(tmp_path):
    task = TaskSpec.load(EXAMPLE / "annotation.toml")
    proj, script = project(tmp_path, ["wrong-offset", "correct"])
    result = proj.start(task, CONFIG, Model())
    assert [s.decision for s in result.submissions] == ["rejected", "accepted"]
    assert "annotation-v2" in script.episodes[1].files
    assert "annotation-v1" not in script.episodes[1].files


def test_a_missing_definition_is_a_checker_fault_not_a_default():
    task = TaskSpec.load(EXAMPLE / "task.toml")
    inputs = {n: d for n, d in task.inputs.items() if not n.startswith("definition-")}
    ctx = CheckContext(
        {"result.json": json.dumps(CANDIDATES["correct"]).encode()},
        inputs,
        task.private,
    )
    with pytest.raises(ValueError, match="definition"):
        CSV.Transformation().check(ctx)
