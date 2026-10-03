"""Pinned campaigns and imported artifacts in the task layer."""

from dataclasses import replace

import pytest
from test_experimental_tasks import CONFIG, EXAMPLE, TASK, Model, project

from warranted import (
    CampaignSpec,
    CampaignTask,
    MemorySpec,
    RunConfig,
    RunOutcome,
    TaskSpec,
)


def spec(campaign="c1", tasks=None, configs=None, repetitions=2):
    tasks = tasks or (CampaignTask(TASK, "development"),)
    return CampaignSpec(campaign, tasks, configs or {"scripted": CONFIG}, repetitions)


MODELS = {"scripted": Model()}


def test_a_campaign_pins_its_runs_and_runs_them_in_order(tmp_path):
    proj, script = project(tmp_path, ["correct"])
    other = replace(TASK, id="csv-offset-v1-copy")
    plan = spec(
        tasks=(CampaignTask(TASK, "development"), CampaignTask(other, "held-out"))
    )
    report = proj.run_campaign(plan, MODELS)
    assert [(r.task_id, r.split, r.repetition) for r in report.runs] == [
        (TASK.id, "development", 1),
        (other.id, "held-out", 1),
        (TASK.id, "development", 2),
        (other.id, "held-out", 2),
    ]
    assert all(r.status.outcome is RunOutcome.ACCEPTED for r in report.runs)
    assert [e.episode_id.rsplit("-s", 1)[0] for e in script.episodes] == [
        r.run_id for r in report.runs
    ]
    assert proj.campaigns() == ("c1",)


def test_rerunning_a_campaign_continues_exactly_the_planned_runs(tmp_path):
    proj, script = project(tmp_path, ["correct"])
    started = []
    original = proj._start

    def interrupted(*args):
        if started:
            raise KeyboardInterrupt  # the host stops before the second run
        started.append(args[3])
        return original(*args)

    proj._start = interrupted
    with pytest.raises(KeyboardInterrupt):
        proj.run_campaign(spec(), MODELS)
    del proj._start
    assert proj.campaign_report("c1").runs[1].status is None
    report = proj.run_campaign(spec(), MODELS)
    assert report.runs[0].run_id == started[0]
    assert len(script.episodes) == 2  # the finished first run was not repeated
    assert proj.run_campaign(spec(), MODELS) == report
    assert len(script.episodes) == 2


def test_a_planned_id_used_outside_the_campaign_is_refused(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    planned = proj.plan_campaign(spec(), MODELS)["entries"][0]["run"]
    other = replace(TASK, id="something-else")
    proj.start(other, CONFIG, Model(), run_id=planned)
    with pytest.raises(ValueError, match="not started by the campaign"):
        proj.run_campaign(spec(), MODELS)
    with pytest.raises(ValueError, match="not started by the campaign"):
        proj.campaign_report("c1")


def test_unknown_runs_are_resumed_when_the_campaign_runs_again(tmp_path):
    from warranted.host import Ledger, Origin, Request

    proj, _ = project(tmp_path, ["correct"])
    first = proj.run_campaign(spec(repetitions=1), MODELS).runs[0].run_id
    with Ledger.open(proj.ledger_root) as ledger:
        session = ledger.start_session()
        request = Request(Origin("lost/1", "tool", "test", "1", {}), ledger.project)
        ledger.reserve(session, request, {"tool": 1}, f"run/{first}")
        assert ledger.begin(session, request)  # dispatched; the response was lost
    resumed = []
    original = proj.resume
    proj.resume = lambda run, *a, **k: resumed.append(run) or original(run, *a, **k)
    report = proj.run_campaign(spec(repetitions=1), MODELS)
    assert resumed == [first]
    assert report.runs[0].status.outcome is RunOutcome.UNKNOWN


@pytest.mark.parametrize(
    "change",
    [
        lambda s: replace(s, repetitions=3),
        lambda s: replace(s, configs={"scripted": replace(CONFIG, max_steps=3)}),
        lambda s: replace(
            s,
            tasks=(
                CampaignTask(
                    replace(TASK, objective=TASK.objective + " Again."), "development"
                ),
            ),
        ),
        lambda s: replace(s, tasks=(CampaignTask(TASK, "training"),)),
    ],
    ids=["repetitions", "config", "task", "split"],
)
def test_a_changed_plan_is_refused(tmp_path, change):
    proj, _ = project(tmp_path, ["correct"])
    proj.plan_campaign(spec(), MODELS)
    with pytest.raises(ValueError, match="differs from its pinned plan"):
        proj.run_campaign(change(spec()), MODELS)


def test_a_memory_scope_cannot_span_splits():
    scoped = replace(TASK, memory=MemorySpec("csv"))
    other = replace(scoped, id="other")
    with pytest.raises(ValueError, match="shared across splits: csv"):
        spec(tasks=(CampaignTask(scoped, "training"), CampaignTask(other, "held-out")))
    spec(tasks=(CampaignTask(scoped, "training"), CampaignTask(other, "training")))


@pytest.mark.parametrize(
    "build",
    [
        lambda: CampaignTask(TASK, "test"),
        lambda: spec(tasks=(CampaignTask(TASK, "training"),) * 2),
        lambda: spec(campaign="no spaces"),
        lambda: spec(repetitions=0),
        lambda: CampaignSpec("c", (CampaignTask(TASK, "training"),), {}),
    ],
)
def test_campaign_settings_are_validated(build):
    with pytest.raises(ValueError):
        build()


def test_every_configuration_needs_its_own_matching_model(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    with pytest.raises(ValueError, match="one model for each"):
        proj.plan_campaign(spec(), {})
    with pytest.raises(ValueError, match="differs from its configuration"):
        proj.plan_campaign(spec(), {"scripted": Model("other-model")})


def test_a_planned_run_id_cannot_reuse_an_existing_run(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    run = proj.start(TASK, CONFIG, Model()).run_id
    with pytest.raises(ValueError, match="already exists"):
        proj.start(TASK, CONFIG, Model(), run_id=run)
    with pytest.raises(ValueError, match="invalid run ID"):
        proj.start(TASK, CONFIG, Model(), run_id="../escape")
    with pytest.raises(ValueError, match="differs from the run configuration"):
        proj.start(TASK, RunConfig("another-model"), Model())


def test_an_unknown_campaign_has_no_report(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    with pytest.raises(ValueError, match="unknown campaign"):
        proj.campaign_report("missing")


def test_imported_artifacts_resolve_in_task_files(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    data = (EXAMPLE / "offset-v1").read_bytes()
    reference = proj.import_artifact(data)
    assert reference.startswith("sha256:")
    assert proj.import_artifact(data) == reference  # idempotent
    original = (EXAMPLE / "task.toml").read_text()
    path = EXAMPLE / "imported-task.toml"
    path.write_text(
        original.replace(
            '"offset-v1" = "offset-v1"', f'"offset-v1" = {{ artifact = "{reference}" }}'
        )
    )
    try:
        assert proj.load_task(path) == TASK
        with pytest.raises(ValueError, match="through a project"):
            TaskSpec.load(path)
        path.write_text(path.read_text().replace(reference, "sha256:" + "0" * 64))
        with pytest.raises(ValueError, match="not imported"):
            proj.load_task(path)
        path.write_text(path.read_text().replace("sha256:" + "0" * 64, "md5:x"))
        with pytest.raises(ValueError, match="invalid artifact reference"):
            proj.load_task(path)
    finally:
        path.unlink()


def test_memory_is_shared_only_within_a_split_across_campaigns(tmp_path):
    import json

    from test_scoped_memory import Domain as Remembering

    from warranted.host import Ledger

    proj, script = project(tmp_path, ["correct"], Remembering())
    scoped = replace(TASK, memory=MemorySpec("csv"))

    def shown():
        ref = script.episodes[-1].files["memory.json"]
        with Ledger.open(proj.ledger_root) as ledger:
            return json.loads(ledger.read_artifact(ref.artifact))["entries"]

    training = (CampaignTask(scoped, "training"),)
    proj.run_campaign(spec("train-1", training, repetitions=1), MODELS)
    proj.start(scoped, CONFIG, Model())  # outside any campaign
    assert shown() == []  # training facts never reach other groups
    held_out = (CampaignTask(scoped, "held-out"),)
    with pytest.raises(ValueError, match="used by training runs of campaign train-1"):
        proj.run_campaign(spec("held-1", held_out, repetitions=1), MODELS)
    proj.run_campaign(spec("train-2", training, repetitions=1), MODELS)
    assert [e["tier"] for e in shown()] == ["verified"]  # same split: shared


def test_a_task_cannot_change_split_across_campaigns(tmp_path):
    proj, _ = project(tmp_path, ["correct"])
    proj.run_campaign(
        spec("train-1", (CampaignTask(TASK, "training"),), repetitions=1), MODELS
    )
    for split in ("development", "held-out"):
        with pytest.raises(ValueError, match="training runs of campaign train-1"):
            proj.plan_campaign(spec(f"c-{split}", (CampaignTask(TASK, split),)), MODELS)
    # A revised task keeps its ID, and so its lineage and split.
    from test_revisions import REVISION

    proj.revise(TASK.id, REVISION)
    with pytest.raises(ValueError, match="training runs of campaign train-1"):
        proj.plan_campaign(
            spec("after-revision", (CampaignTask(TASK, "held-out"),)), MODELS
        )
    # The same split in another campaign is allowed.
    proj.plan_campaign(spec("train-2", (CampaignTask(TASK, "training"),)), MODELS)
