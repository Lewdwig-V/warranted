"""The `warranted` command: init, run, resume, status, memory, and export."""

import json

import pytest
from test_chat_completions import response, server
from test_experimental_tasks import CANDIDATES, ENVIRONMENT, EXAMPLE, Script

import warranted._tasks as tasks
from warranted._cli import main

DOMAIN = EXAMPLE / "domain.py"
TASK = EXAMPLE / "task.toml"
MODEL = "gemma4:26b"


class Tiny:
    """A domain reachable as `test_cli:Tiny`, to test module references."""

    name, version = "tiny", "1"
    worker_image = "f" * 64
    checkers = {}


@pytest.fixture
def scripted(monkeypatch):
    """Run episodes in a scripted environment instead of a container."""
    script = Script([])
    monkeypatch.setattr(
        tasks, "Sandbox", lambda root, episode, image: script(root, episode)
    )
    monkeypatch.setattr(tasks, "sandbox_id", lambda image: ENVIRONMENT)
    return script.plan


@pytest.fixture
def model(tmp_path):
    with server(response()) as (url, calls):
        config = tmp_path / "run.toml"
        config.write_text(
            f'model = "{MODEL}"\nmax_steps = 2\n[adapter]\nbase_url = "{url}"\n'
        )
        yield config, calls


def init(tmp_path, capsys, *extra):
    project = tmp_path / "project"
    code = main(
        [
            "init",
            str(project),
            "--domain",
            f"{DOMAIN}:CsvDomain",
            "--allow",
            "model=20",
            "--allow",
            "tool=20",
            "--allow",
            "check=20",
            *extra,
        ]
    )
    assert code == 0
    capsys.readouterr()
    return project


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_init_records_the_domain_relative_to_the_project(tmp_path, capsys):
    project = init(tmp_path, capsys)
    reference = (project / "warranted.toml").read_text()
    assert reference.startswith('domain = "../')
    assert reference.rstrip().endswith(':CsvDomain"')
    assert main(["status", str(project), "--json"]) == 0
    assert out(capsys) == {"version": 1, "runs": []}


def test_init_accepts_an_importable_module(tmp_path, capsys):
    assert main(["init", str(tmp_path / "p"), "--domain", "test_cli:Tiny"]) == 0
    assert "tiny 1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["init", "{p}", "--domain", "no-colon"],
        ["init", "{p}", "--domain", "test_cli:Missing"],
        ["init", "{p}", "--domain", "missing_package:Domain"],
        ["init", "{p}", "--domain", "missing.py:Domain"],
        ["init", "{p}", "--domain", "test_cli:Tiny", "--allow", "model"],
        ["status", "{p}"],
    ],
)
def test_bad_input_is_a_usage_error(tmp_path, capsys, argv):
    argv = [a.format(p=tmp_path / "p") for a in argv]
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith(f"warranted {argv[0]}:")


def test_a_project_cannot_be_initialised_twice(tmp_path, capsys):
    project = init(tmp_path, capsys)
    assert main(["init", str(project), "--domain", f"{DOMAIN}:CsvDomain"]) == 2


def test_run_reports_the_outcome_and_exits_by_it(tmp_path, capsys, scripted, model):
    project = init(tmp_path, capsys)
    config, calls = model
    scripted += ["wrong-offset", "correct"]
    assert (
        main(["run", str(project), str(TASK), "--config", str(config), "--json"]) == 0
    )
    assert any(path == "/v1/chat/completions" for path, _ in calls)
    result = out(capsys)
    assert result["outcome"] == "accepted"
    assert [s["decision"] for s in result["submissions"]] == ["rejected", "accepted"]
    assert main(["status", str(project), result["run"], "--json"]) == 0
    status = out(capsys)
    assert status["outcome"] == "accepted"
    assert status["submissions"] == result["submissions"]
    assert status["accounting"]["check"]["spent"] == 2
    assert status["blocked"] == []


def test_a_rejected_run_exits_1(tmp_path, capsys, scripted, model):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += ["wrong-offset"] * 3
    assert main(["run", str(project), str(TASK), "--config", str(config)]) == 1
    assert "rejected" in capsys.readouterr().out


def test_a_run_that_ended_without_submitting_is_not_open(
    tmp_path, capsys, scripted, model
):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += [None]  # the first episode never submits
    code = main(["run", str(project), str(TASK), "--config", str(config), "--json"])
    assert code == 1
    run = out(capsys)
    assert run["outcome"] == "incomplete"
    # The episode finished without submitting; resume would only replay it.
    assert main(["status", str(project), run["run"], "--json"]) == 0
    assert out(capsys)["outcome"] == "incomplete"
    scripted[0] = "correct"
    resumed = main(["resume", str(project), run["run"], "--config", str(config)])
    assert resumed == 1  # the recorded episode replays as it was


def test_resume_refuses_a_different_model(tmp_path, capsys, scripted, model):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += ["correct"]
    main(["run", str(project), str(TASK), "--config", str(config), "--json"])
    run = out(capsys)["run"]
    other = tmp_path / "other.toml"
    other.write_text('model = "other:1b"\n')
    assert main(["resume", str(project), run, "--config", str(other)]) == 2
    assert "model" in capsys.readouterr().err


def test_openrouter_is_refused_until_the_task_layer_supports_it(tmp_path, capsys):
    project = init(tmp_path, capsys)
    config = tmp_path / "run.toml"
    config.write_text('model = "a/b"\nprovider = "openrouter"\n')
    assert main(["run", str(project), str(TASK), "--config", str(config)]) == 2
    assert "openrouter" in capsys.readouterr().err


def test_export_includes_public_records_and_no_private_bytes(
    tmp_path, capsys, scripted, model
):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += ["wrong-offset", "correct"]
    main(["run", str(project), str(TASK), "--config", str(config), "--json"])
    run = out(capsys)["run"]
    dest = tmp_path / "export"
    assert main(["export", str(project), run, str(dest)]) == 0
    index = json.loads((dest / "index.json").read_text())
    channels = {c for o in index["observations"] for c in o["artifacts"]}
    assert {"task.json", "config.json", "submission.json", "feedback.json"} <= channels
    assert {"verdict.json", "decision.json", "candidate/result.json"} <= channels
    assert not {"host-only.json", "seeds.json", "jobs.json"} & channels
    assert not any(c.startswith("private/") for c in channels)
    blobs = b"".join(p.read_bytes() for p in (dest / "artifacts").iterdir())
    private = (EXAMPLE / "../../m2/fixture/references.json").read_bytes()
    assert private not in blobs
    assert json.dumps(CANDIDATES["correct"]).encode() in blobs
    assert main(["export", str(project), run, str(dest)]) == 2  # never overwritten


def test_memory_lists_entries_for_a_task(tmp_path, capsys, scripted, model, request):
    project = init(tmp_path, capsys)
    config, _ = model
    task = EXAMPLE / "memory-task.toml"
    task.write_text(
        TASK.read_text() + '[memory]\nscope = "csv"\ndepends = ["offset-v1"]\n'
    )
    request.addfinalizer(task.unlink)
    scripted += ["correct"]
    main(["run", str(project), str(task), "--config", str(config)])
    capsys.readouterr()
    assert main(["memory", str(project), str(task), "--json"]) == 0
    view = out(capsys)
    assert view["scope"] == "csv"
    assert view["entries"] == []  # the CSV checker supplies no facts or notes


def test_status_lists_every_run(tmp_path, capsys, scripted, model):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += ["correct"]
    main(["run", str(project), str(TASK), "--config", str(config)])
    capsys.readouterr()
    assert main(["status", str(project)]) == 0
    line = capsys.readouterr().out
    assert "csv-offset-v1  accepted  1/3 submissions" in line


def test_help_and_version(capsys):
    assert main([]) == 0
    assert "init" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit:
        main(["--version"])
    assert exit.value.code == 0
    assert capsys.readouterr().out.startswith("warranted ")


def test_the_package_runs_as_a_module():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "warranted", "status", "/nonexistent"],
        capture_output=True,
    )
    assert result.returncode == 2


def test_resume_refuses_a_changed_adapter_configuration(
    tmp_path, capsys, scripted, model
):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += [None]
    main(["run", str(project), str(TASK), "--config", str(config), "--json"])
    run = out(capsys)["run"]
    changed = tmp_path / "changed.toml"
    changed.write_text(config.read_text() + "max_tokens = 99\n")
    assert main(["resume", str(project), run, "--config", str(changed)]) == 2
    assert "adapter configuration differs" in capsys.readouterr().err


@pytest.mark.container
@pytest.mark.skipif(
    __import__("os").environ.get("WARRANTED_CONTAINER_TESTS") != "1",
    reason="requires WARRANTED_CONTAINER_TESTS=1 and rootless Podman",
)
def test_a_cli_run_drives_a_real_container_with_a_real_adapter(tmp_path, capsys):
    project = init(tmp_path, capsys)
    with server(response()) as (url, calls):
        config = tmp_path / "run.toml"
        config.write_text(
            f'model = "{MODEL}"\nmax_steps = 1\n[adapter]\nbase_url = "{url}"\n'
        )
        code = main(["run", str(project), str(TASK), "--config", str(config), "--json"])
    # The model only ever answers `true`, so the episode ends without a submission.
    assert code == 1
    assert out(capsys)["outcome"] == "incomplete"
    assert [p for p, _ in calls].count("/v1/chat/completions") == 1


def test_status_reports_an_open_run_and_each_blocker_once(
    tmp_path, capsys, scripted, model
):
    from warranted._ledger import ROOT_SCOPE, Ledger, Origin, Request

    project = init(tmp_path, capsys)
    config, _ = model
    scripted += ["wrong-offset", "correct"]
    main(["run", str(project), str(TASK), "--config", str(config), "--json"])
    run = out(capsys)["run"]
    with Ledger.open(project / "ledger") as ledger:
        session = ledger.start_session()
        request = Request(
            Origin("shared/probe", "tool", "test", "1", {}), ledger.project
        )
        ledger.reserve(session, request, {"tool": 1}, ROOT_SCOPE)
        assert ledger.begin(session, request)  # dispatched, never completed
    assert main(["status", str(project), run, "--json"]) == 0
    status = out(capsys)
    assert status["outcome"] == "unknown"
    assert status["blocked"] == ["shared/probe"]


def campaign_file(tmp_path, config, *, repetitions=2, split="development"):
    path = tmp_path / "campaign.toml"
    path.write_text(
        f'id = "csv-dev"\nrepetitions = {repetitions}\n'
        f'[configs]\nlocal = "{config}"\n'
        f'[[tasks]]\npath = "{TASK}"\nsplit = "{split}"\n'
    )
    return path


def test_campaign_run_pins_runs_and_continues_them(tmp_path, capsys, scripted, model):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += ["correct"]
    campaign = campaign_file(tmp_path, config)
    assert main(["campaign", "run", str(project), str(campaign), "--json"]) == 0
    first = out(capsys)
    assert [r["outcome"] for r in first["runs"]] == ["accepted", "accepted"]
    assert first["splits"]["development"]["outcomes"] == {"accepted": 2}
    assert first["splits"]["development"]["spent"]["check"] == 2
    # Running it again continues the same plan; nothing is left to run.
    assert main(["campaign", "run", str(project), str(campaign), "--json"]) == 0
    assert out(capsys)["runs"] == first["runs"]
    assert main(["campaign", "report", str(project), "csv-dev"]) == 0
    assert "development: 2 accepted" in capsys.readouterr().out


def test_a_changed_campaign_is_a_usage_error(tmp_path, capsys, scripted, model):
    project = init(tmp_path, capsys)
    config, _ = model
    scripted += ["correct"]
    main(["campaign", "run", str(project), str(campaign_file(tmp_path, config))])
    capsys.readouterr()
    changed = campaign_file(tmp_path, config, repetitions=3)
    assert main(["campaign", "run", str(project), str(changed)]) == 2
    assert "pinned plan" in capsys.readouterr().err


def test_import_pins_files_for_task_references(tmp_path, capsys):
    project = init(tmp_path, capsys)
    data = tmp_path / "binary"
    data.write_bytes(b"\x7fELF")
    assert main(["import", str(project), str(data)]) == 0
    reference, name = capsys.readouterr().out.split()
    assert reference.startswith("sha256:") and name == str(data)
    assert main(["import", str(project), str(tmp_path / "missing")]) == 2


def test_revise_records_a_revision_for_new_runs(tmp_path, capsys, scripted, model):
    project = init(tmp_path, capsys)
    config, _ = model
    (tmp_path / "offset-v2").write_bytes(b'{"minutes": 0, "version": "2"}')
    revision = tmp_path / "revision.toml"
    revision.write_text(
        'id = "offset-v2"\nowner = "owner"\nreason = "UTC source"\n'
        'remove = ["offset-v1"]\n[inputs]\n"offset-v2" = "offset-v2"\n'
    )
    assert main(["revise", str(project), "csv-offset-v1", str(revision)]) == 0
    assert "revised by owner: offset-v2" in capsys.readouterr().out
    scripted += ["wrong-offset"]
    assert main(["run", str(project), str(TASK), "--config", str(config)]) == 0
    revision.write_text('id = "empty"\nowner = "owner"\nreason = "nothing"\n')
    assert main(["revise", str(project), "csv-offset-v1", str(revision)]) == 2
