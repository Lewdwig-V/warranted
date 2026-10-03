"""The duplicate guard refuses repeats of rejected candidates before any check."""

import json

import pytest
from test_experimental_tasks import CANDIDATES, CONFIG, CSV, TASK, Model, project

from warranted._guard import DuplicateGuard, default_normalize, edit_mass
from warranted._ledger import Ledger
from warranted._tasks import RunOutcome, TaskSpec

GUARD = DuplicateGuard(
    exact_repeats=2, near_repeats=3, near_edit_floor=8, near_edit_percent=0, window=8
)


def task(guard=GUARD, submissions=4):
    return TaskSpec(
        TASK.id,
        TASK.objective,
        TASK.inputs,
        TASK.private,
        TASK.checks,
        submissions,
        None,
        guard,
    )


def checks_run(proj):
    with Ledger.open(proj.ledger_root) as ledger:
        return sorted(
            int(o.request.origin.operation_id.split("/")[3])
            for o in ledger.operations()
            if o.request.origin.kind == "check"
        )


def feedback(proj, script, episode, number):
    with Ledger.open(proj.ledger_root) as ledger:
        ref = script.episodes[episode].files[f"feedback-{number:03d}.json"]
        return json.loads(ledger.read_artifact(ref.artifact))


def test_an_exact_repeat_of_rejected_candidates_is_refused_before_checking(tmp_path):
    plan = ["wrong-offset", "wrong-offset", "wrong-offset", "correct"]
    proj, script = project(tmp_path, plan)
    result = proj.start(task(), CONFIG, Model())
    assert [s.decision for s in result.submissions] == [
        "rejected",
        "rejected",
        "duplicate",
        "accepted",
    ]
    assert result.outcome is RunOutcome.ACCEPTED
    assert checks_run(proj) == [1, 2, 4]  # the refused submission cost no check
    shown = feedback(proj, script, 3, 3)
    assert shown["duplicate"]["band"] == "exact"
    assert shown["duplicate"]["matches"] == 2


def test_small_edits_get_more_runway_than_exact_repeats(tmp_path, monkeypatch):
    base = CANDIDATES["wrong-offset"]
    for n in range(1, 4):
        variant = json.loads(json.dumps(base))
        variant["rows"][0][2] = base["rows"][0][2] + n
        monkeypatch.setitem(CANDIDATES, f"near-{n}", variant)
    plan = ["wrong-offset", "near-1", "near-2", "near-3"]
    proj, _ = project(tmp_path, plan)
    result = proj.start(task(), CONFIG, Model())
    # Three near matches are allowed to be checked; the fourth is refused.
    assert [s.decision for s in result.submissions] == [
        "rejected",
        "rejected",
        "rejected",
        "duplicate",
    ]
    assert result.outcome is RunOutcome.REJECTED


def test_a_real_change_of_approach_is_checked(tmp_path):
    plan = ["wrong-offset", "wrong-offset", "dropped-row", "correct"]
    proj, _ = project(tmp_path, plan)
    result = proj.start(task(), CONFIG, Model())
    assert [s.decision for s in result.submissions] == [
        "rejected",
        "rejected",
        "rejected",
        "accepted",
    ]


def test_without_a_guard_every_repeat_is_checked(tmp_path):
    plan = ["wrong-offset"] * 4
    proj, _ = project(tmp_path, plan)
    result = proj.start(task(guard=None), CONFIG, Model())
    assert [s.decision for s in result.submissions] == ["rejected"] * 4
    assert checks_run(proj) == [1, 2, 3, 4]


def test_the_domain_normalisation_decides_what_counts_as_a_repeat(tmp_path):
    class Coarse(CSV.CsvDomain):
        @staticmethod
        def normalize(candidate):
            return b"every candidate looks the same"

    plan = ["wrong-offset", "dropped-row", "empty", "correct"]
    proj, _ = project(tmp_path, plan, Coarse())
    result = proj.start(task(), CONFIG, Model())
    assert [s.decision for s in result.submissions][:3] == [
        "rejected",
        "rejected",
        "duplicate",
    ]


def test_resume_replays_the_recorded_decision(tmp_path):
    plan = ["wrong-offset", "wrong-offset", "wrong-offset", "correct"]
    proj, _ = project(tmp_path, plan)
    first = proj.start(task(), CONFIG, Model())
    assert proj.resume(first.run_id, Model()) == first
    assert checks_run(proj) == [1, 2, 4]


def test_the_guard_is_part_of_the_task_file_and_its_record(tmp_path):
    path = tmp_path / "task.toml"
    path.write_text(
        'id = "t"\nobjective = "o"\nchecks = ["c"]\n'
        "[duplicate_guard]\nexact_repeats = 2\nnear_repeats = 3\n"
        "near_edit_floor = 24\nnear_edit_percent = 6\nwindow = 8\n"
    )
    loaded = TaskSpec.load(path)
    assert loaded.duplicate_guard == DuplicateGuard(2, 3, 24, 6, 8)
    assert json.loads(loaded.record()["task.json"])["duplicate_guard"]["window"] == 8
    path.write_text(
        'id = "t"\nobjective = "o"\nchecks = ["c"]\n'
        "[duplicate_guard]\nexact_repeats = 2\n"
    )
    with pytest.raises(ValueError, match="duplicate_guard needs exactly"):
        TaskSpec.load(path)


@pytest.mark.parametrize(
    "values",
    [(0, 3, 0, 0, 8), (2, 3, -1, 0, 8), (2, 3, 0, 101, 8), (2, 3, 0, 0, 0)],
)
def test_guard_settings_are_validated(values):
    with pytest.raises(ValueError):
        DuplicateGuard(*values)


def test_edit_mass_counts_changed_bytes():
    assert edit_mass(b"abcdef", b"abcdef") == 0
    assert edit_mass(b"abcdef", b"abXdef") == 1
    assert edit_mass(b"abc", b"abcdef") == 3


def test_file_boundaries_are_unambiguous_after_default_normalisation():
    assert default_normalize({"a": b"x\0b\0y"}) != default_normalize(
        {"a": b"x", "b": b"y"}
    )


def test_a_near_threshold_below_the_exact_one_still_decides():
    guard = DuplicateGuard(
        exact_repeats=3,
        near_repeats=2,
        near_edit_floor=0,
        near_edit_percent=0,
        window=8,
    )
    assert guard.verdict([b"same", b"same"], b"same") == {
        "band": "near",
        "matches": 2,
        "edit": 0,
    }


def test_the_decision_cites_the_candidates_it_compared(tmp_path):
    plan = ["wrong-offset", "wrong-offset", "wrong-offset", "correct"]
    proj, _ = project(tmp_path, plan)
    proj.start(task(), CONFIG, Model())
    with Ledger.open(proj.ledger_root) as ledger:
        decisions = [o for o in ledger.history() if o.origin.kind == "duplicate-guard"]
        refused = decisions[-1]
    assert decisions[0].origin.inputs  # even the first decision cites its candidate
    # The refused candidate and the two rejections it repeats, by their captures.
    assert len({name.split("/")[1] for name in refused.origin.inputs}) == 3
