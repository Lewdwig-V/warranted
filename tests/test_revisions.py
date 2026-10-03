"""Contract revisions: scheduled within a run, and recorded for new runs."""

import json
from dataclasses import replace

import pytest
from test_experimental_tasks import CONFIG, EXAMPLE, TASK, Model, project

from warranted import (
    CampaignSpec,
    CampaignTask,
    DuplicateGuard,
    Revision,
    RunOutcome,
    ScheduledRevision,
    TaskSpec,
)
from warranted.host import Ledger

OFFSET_V2 = b'{"minutes": 0, "version": "2"}'
REVISION = Revision(
    "offset-v2",
    "fixture-owner",
    "The offset was wrong; the source data is UTC.",
    inputs={"offset-v2": OFFSET_V2},
    remove=("offset-v1",),
    note="Use offset-v2.",
)


def scheduled(after=1, submissions=3, guard=None):
    return replace(
        TASK,
        submissions=submissions,
        duplicate_guard=guard,
        revisions=(ScheduledRevision(after, REVISION),),
    )


def decisions(result):
    return [s.decision for s in result.submissions]


def test_submissions_after_the_checkpoint_use_the_revised_contract(tmp_path):
    proj, script = project(tmp_path, ["wrong-offset", "wrong-offset"])
    result = proj.start(scheduled(), CONFIG, Model())
    # The same candidate: rejected under offset-v1, accepted under offset-v2.
    assert decisions(result) == ["rejected", "accepted"]
    first, second = script.episodes
    assert "offset-v1" in first.files and "offset-v2" not in first.files
    assert "offset-v2" in second.files and "offset-v1" not in second.files
    assert "revised after submission 1" in second.objective
    assert "Use offset-v2." in second.objective
    status = proj.status(result.run_id)
    assert [s.decision for s in status.submissions] == ["rejected", "accepted"]
    with Ledger.open(proj.ledger_root) as ledger:
        decided = [
            op.request.origin.inputs
            for op in ledger.operations()
            if op.request.origin.kind == "decision"
        ]
    # Each decision binds the contract version it used.
    assert any(name.endswith("/task.json") for name in decided[0])
    assert any(name.endswith("/revision/1/revision.json") for name in decided[1])


def test_acceptance_before_the_checkpoint_ends_the_run(tmp_path):
    proj, script = project(tmp_path, ["correct"])
    result = proj.start(scheduled(), CONFIG, Model())
    assert result.outcome is RunOutcome.ACCEPTED
    assert decisions(result) == ["accepted"]
    assert len(script.episodes) == 1


def test_a_resubmission_after_a_revision_is_not_a_duplicate(tmp_path):
    guard = DuplicateGuard(1, 1, 0, 0, 8)
    proj, _ = project(tmp_path, ["wrong-offset"] * 3)
    # Without a revision the repeat is refused before any check.
    unrevised = replace(TASK, submissions=3, duplicate_guard=guard)
    assert decisions(proj.start(unrevised, CONFIG, Model()))[:2] == [
        "rejected",
        "duplicate",
    ]
    revised = proj.start(scheduled(guard=guard), CONFIG, Model())
    assert decisions(revised) == ["rejected", "accepted"]


def test_a_resumed_run_keeps_its_scheduled_contract(tmp_path):
    proj, _ = project(tmp_path, ["wrong-offset", "wrong-offset"])
    first = proj.start(scheduled(), CONFIG, Model())
    assert proj.resume(first.run_id, Model()) == first
    assert proj.resume(first.run_id, Model(), task=scheduled()) == first


def test_a_project_revision_applies_to_new_runs_only(tmp_path):
    proj, script = project(tmp_path, [None])
    waiting = proj.start(TASK, CONFIG, Model())
    assert waiting.outcome is RunOutcome.INCOMPLETE
    proj.revise(TASK.id, REVISION)
    proj.revise(TASK.id, REVISION)  # recording it again changes nothing
    assert [r.id for r in proj.revisions(TASK.id)] == ["offset-v2"]
    with pytest.raises(ValueError, match="revised after this run started"):
        proj.resume(waiting.run_id, Model())
    script.plan[0] = "wrong-offset"
    revised = proj.start(TASK, CONFIG, Model())
    assert revised.outcome is RunOutcome.ACCEPTED
    assert "offset-v2" in script.episodes[-1].files
    assert proj.current_task(TASK).inputs.keys() == script.episodes[-1].files.keys()


def test_a_different_revision_cannot_reuse_an_id(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    proj.revise(TASK.id, REVISION)
    with pytest.raises(ValueError):
        proj.revise(TASK.id, replace(REVISION, reason="something else"))


def test_a_campaign_pinned_before_a_revision_is_refused(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    spec = CampaignSpec("c1", (CampaignTask(TASK, "development"),), {"m": CONFIG})
    proj.run_campaign(spec, {"m": Model()})
    proj.revise(TASK.id, REVISION)
    with pytest.raises(ValueError, match="differs from its pinned plan"):
        proj.run_campaign(spec, {"m": Model()})


def test_revised_checks_must_exist_in_the_domain(tmp_path):
    proj, _ = project(tmp_path, ["wrong-offset"])
    extra = replace(REVISION, checks=("transformation", "extra"))
    task = replace(TASK, submissions=2, revisions=(ScheduledRevision(1, extra),))
    with pytest.raises(ValueError, match="unknown checks"):
        proj.start(task, CONFIG, Model())


@pytest.mark.parametrize(
    "build",
    [
        lambda: Revision("r", "o", "why"),  # changes nothing
        lambda: Revision("r", "", "why", note="n"),
        lambda: Revision("bad id", "o", "why", note="n"),
        lambda: Revision("r", "o", "why", inputs={"a": b""}, remove=("a",)),
        lambda: replace(Revision("r", "o", "why", remove=("missing",))).apply(TASK),
        lambda: Revision("r", "o", "why", inputs={"result.json": b""}).apply(TASK),
        lambda: scheduled(after=3, submissions=3),  # checkpoint at the last one
        lambda: replace(
            TASK,
            submissions=3,
            revisions=(ScheduledRevision(2, REVISION), ScheduledRevision(1, REVISION)),
        ),
        lambda: ScheduledRevision(0, REVISION),
    ],
)
def test_revisions_are_validated(build):
    with pytest.raises(ValueError):
        build()


def test_revisions_load_from_toml(tmp_path):
    (tmp_path / "offset-v2").write_bytes(OFFSET_V2)
    (tmp_path / "revision.toml").write_text(
        'id = "offset-v2"\nowner = "fixture-owner"\n'
        'reason = "The offset was wrong; the source data is UTC."\n'
        'note = "Use offset-v2."\nremove = ["offset-v1"]\n'
        '[inputs]\n"offset-v2" = "offset-v2"\n'
    )
    assert Revision.load(tmp_path / "revision.toml") == REVISION
    original = (EXAMPLE / "task.toml").read_text()
    path = EXAMPLE / "revised-task.toml"
    path.write_text(
        original
        + f'\n[[revisions]]\nafter_submission = 1\nfile = "{tmp_path}/revision.toml"\n'
    )
    try:
        assert TaskSpec.load(path) == scheduled()
    finally:
        path.unlink()


def test_export_includes_revised_inputs_but_no_revised_private_files(tmp_path):
    secret = b"PRIVATE REVISED REFERENCE"
    revision = replace(REVISION, private={"note.txt": secret})
    task = replace(TASK, submissions=2, revisions=(ScheduledRevision(1, revision),))
    proj, _ = project(tmp_path, ["wrong-offset", "wrong-offset"])
    run = proj.start(task, CONFIG, Model()).run_id
    index = proj.export(run, tmp_path / "export")
    channels = {
        c for o in json.loads(index.read_text())["observations"] for c in o["artifacts"]
    }
    assert "revision/1/input/offset-v2" in channels
    assert "revision/1/revision.json" in channels
    assert not any("/private/" in c for c in channels)
    blobs = b"".join(p.read_bytes() for p in (tmp_path / "export/artifacts").iterdir())
    assert secret not in blobs
