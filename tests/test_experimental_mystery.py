"""Second prototype slice: private inputs, recorded seeds, jobs, nominated cases."""

import base64
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from warranted.containers import SandboxFailure
from warranted.experimental import Project, RunConfig, RunOutcome, TaskSpec
from warranted.jobs import JobResult, validate_job
from warranted.ledger import Ledger, Outcome, Result
from warranted.worker import AttemptResult

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/m7/mystery"
ENVIRONMENT = "scripted-environment-v1"
ORIGINAL = (EXAMPLE / "mystery.py").read_text()
ECHO = "import sys\nsys.stdout.write(sys.stdin.read())\n"
LOOP = "while True:\n    pass\n"


def load_domain():
    path = EXAMPLE / "domain.py"
    spec = importlib.util.spec_from_file_location("m7_mystery_domain", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MYSTERY = load_domain()
TASK = TaskSpec.load(EXAMPLE / "task.toml")
CONFIG = RunConfig("scripted-model-v1", max_steps=2)


def lookup(table):
    """A model that memorises nominated cases and echoes everything else."""
    return (
        "import sys\n"
        f"TABLE = {table!r}\n"
        "data = sys.stdin.read()\n"
        "sys.stdout.write(TABLE.get(data, data))\n"
    )


class LocalJobs:
    """Test-only runner: same interface as PodmanJobs, without containment."""

    identity = "local-test-jobs"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def run(self, image, argv, files, *, stdin=b"", timeout_seconds=10, limits=None):
        validate_job(image, argv, files, timeout_seconds)
        self.calls += 1
        if self.fail:
            raise SandboxFailure("container runtime unavailable")
        with tempfile.TemporaryDirectory() as directory:
            for name, data in files.items():
                (Path(directory) / name).write_bytes(data)
            command = [sys.executable if argv[0] == "python" else argv[0], *argv[1:]]
            try:
                done = subprocess.run(
                    command,
                    cwd=directory,
                    input=stdin,
                    capture_output=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return JobResult(None, b"", b"", timed_out=True)
            return JobResult(done.returncode, done.stdout, done.stderr)


class Model:
    def __init__(self):
        self.model, self.calls = "scripted-model-v1", 0

    def __call__(self, request, payload):
        self.calls += 1
        return AttemptResult(
            Result(Outcome.SUCCEEDED, 0, {"model": 1}, 1),
            {"response": json.dumps({"command": "work"}).encode()},
        )


class Script:
    """Episode i submits plan[i] as result.json; records each episode's files."""

    def __init__(self, plan):
        self.plan, self.episodes = list(plan), []

    def __call__(self, ledger_root, episode):
        self.episodes.append(episode)
        payload = self.plan[int(episode.episode_id.rsplit("-s", 1)[1]) - 1]

        class Environment:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __call__(self, request, data):
                body = payload if isinstance(payload, bytes) else json.dumps(payload)
                return AttemptResult(
                    Result(Outcome.SUCCEEDED, 0, {"tool": 1}, 1),
                    {
                        "stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n",
                        "stderr": b"",
                        "candidate/result.json": body
                        if isinstance(body, bytes)
                        else body.encode(),
                        "candidate/workspace.json": b"{}",
                    },
                )

        return Environment()


def project(tmp_path, plan, jobs=None):
    script, jobs = Script(plan), jobs or LocalJobs()
    created = Project.create(
        tmp_path / "project",
        MYSTERY.MysteryDomain(),
        {"model": 40, "tool": 40, "check": 40},
        environment=script,
        environment_id=ENVIRONMENT,
        jobs=jobs,
    )
    return created, script, jobs


def check_records(proj, run_id):
    """Every completed check operation's raw channels for one run, in order."""
    with Ledger.open(proj.ledger_root) as ledger:
        return [
            {
                name: json.loads(ledger.read_artifact(ref))
                for name, ref in op.completion.observation.artifacts.items()
                if name.endswith(".json")
            }
            for op in ledger.operations()
            if op.request.origin.operation_id.startswith(f"run/{run_id}/")
            and op.request.origin.kind == "check"
            and op.completion
        ]


def test_a_faithful_model_is_accepted_with_seed_and_jobs_recorded(tmp_path):
    proj, _, jobs = project(tmp_path, [{"model": ORIGINAL, "cases": ["hello"]}])
    result = proj.start(TASK, CONFIG, Model())
    assert result.outcome is RunOutcome.ACCEPTED
    [record] = check_records(proj, result.run_id)
    assert len(record["seeds.json"]) == 1
    assert len(record["jobs.json"]) == jobs.calls == 2
    assert len(record["host-only.json"]["hidden_cases"]) == 8


def test_job_output_is_kept_as_raw_evidence(tmp_path):
    proj, _, _ = project(tmp_path, [{"model": ORIGINAL, "cases": ["hello"]}])
    result = proj.start(TASK, CONFIG, Model())
    with Ledger.open(proj.ledger_root) as ledger:
        [op] = [
            op
            for op in ledger.operations()
            if op.request.origin.operation_id.startswith(f"run/{result.run_id}/")
            and op.request.origin.kind == "check"
        ]
        artifacts = op.completion.observation.artifacts
        log = json.loads(ledger.read_artifact(artifacts["jobs.json"]))
        for job in log:
            for channel in ("stdout", "stderr"):
                ref = artifacts[job[channel]["channel"]]
                assert ref.digest == job[channel]["digest"]
        model_rows = json.loads(
            ledger.read_artifact(artifacts[log[1]["stdout"]["channel"]])
        )
    assert model_rows[0] == [0, base64.b64encode(b"hE2lO").decode()]


def test_reopening_with_a_different_job_runner_is_refused(tmp_path):
    proj, _, _ = project(tmp_path, [{"model": ORIGINAL, "cases": []}])

    class Other(LocalJobs):
        identity = "weaker-jobs"

    with pytest.raises(ValueError, match="differs"):
        Project(
            proj.root, MYSTERY.MysteryDomain(), environment_id=ENVIRONMENT, jobs=Other()
        )


def test_a_job_runner_without_an_identity_is_refused(tmp_path):
    class Anonymous(LocalJobs):
        identity = None

    with pytest.raises(ValueError, match="identity"):
        project(tmp_path, [], jobs=Anonymous())
    assert not (tmp_path / "project").exists()


def test_memorising_nominated_cases_fails_hidden_cases_without_revealing_them(
    tmp_path,
):
    table = {"hello": "hE2lO", "aa": "2A"}
    plan = [
        {"model": lookup(table), "cases": list(table)},
        {"model": ORIGINAL, "cases": []},
    ]
    proj, script, _ = project(tmp_path, plan)
    result = proj.start(TASK, CONFIG, Model())
    assert [s.decision for s in result.submissions] == ["rejected", "accepted"]
    first = check_records(proj, result.run_id)[0]
    feedback = first["verdict.json"]["feedback"]
    assert feedback["nominated"] == {"checked": 2, "first_divergence": None}
    assert feedback["hidden"]["failed"] > 0
    with Ledger.open(proj.ledger_root) as ledger:
        shown = ledger.read_artifact(
            script.episodes[1].files["feedback-001.json"].artifact
        ).decode()
    for case in first["host-only.json"]["hidden_cases"]:
        if len(case) > 3:  # short strings can appear by coincidence
            assert json.dumps(case) not in shown
    assert "seed" not in shown


def test_the_first_nominated_divergence_is_shown_in_detail(tmp_path):
    proj, _, _ = project(tmp_path, [{"model": ECHO, "cases": ["xyz", "aa"]}])
    result = proj.start(
        TaskSpec(TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 1),
        CONFIG,
        Model(),
    )
    assert result.outcome is RunOutcome.REJECTED
    feedback = check_records(proj, result.run_id)[0]["verdict.json"]["feedback"]
    assert feedback["nominated"]["first_divergence"] == {
        "case": "aa",
        "expected": "2A",
        "actual": "aa",
    }


def test_a_looping_model_is_rejected_not_an_infrastructure_failure(tmp_path):
    proj, _, _ = project(tmp_path, [{"model": LOOP, "cases": ["a"]}])
    one = TaskSpec(TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 1)
    result = proj.start(one, CONFIG, Model())
    assert result.outcome is RunOutcome.REJECTED
    feedback = check_records(proj, result.run_id)[0]["verdict.json"]["feedback"]
    assert feedback["nominated"]["first_divergence"]["actual"] == "timed out"


@pytest.mark.parametrize(
    "payload",
    [b"not json", b"[" * 20000 + b"]" * 20000, {"model": 3, "cases": []}],
)
def test_malformed_submissions_are_rejected_with_feedback(tmp_path, payload):
    proj, _, jobs = project(tmp_path, [payload])
    one = TaskSpec(TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 1)
    result = proj.start(one, CONFIG, Model())
    assert result.outcome is RunOutcome.REJECTED
    assert jobs.calls == 0


def test_resume_reuses_the_check_without_new_jobs_or_seeds(tmp_path):
    proj, _, jobs = project(tmp_path, [{"model": ORIGINAL, "cases": []}])
    first = proj.start(TASK, CONFIG, Model())
    calls = jobs.calls
    assert proj.resume(first.run_id, Model()) == first
    assert jobs.calls == calls
    assert len(check_records(proj, first.run_id)) == 1


def test_each_run_draws_fresh_hidden_cases(tmp_path):
    proj, _, _ = project(tmp_path, [{"model": ORIGINAL, "cases": []}])
    seeds = [
        check_records(proj, proj.start(TASK, CONFIG, Model()).run_id)[0]["seeds.json"]
        for _ in range(2)
    ]
    assert seeds[0] != seeds[1]


def test_a_job_runner_failure_is_an_infrastructure_failure(tmp_path):
    proj, _, _ = project(
        tmp_path, [{"model": ORIGINAL, "cases": []}], jobs=LocalJobs(fail=True)
    )
    assert proj.start(TASK, CONFIG, Model()).outcome is (
        RunOutcome.INFRASTRUCTURE_FAILURE
    )


@pytest.mark.parametrize(
    "image, argv, files, seconds",
    [
        ("python:3.12", ["python"], {}, 5),
        (MYSTERY.IMAGE, [], {}, 5),
        (MYSTERY.IMAGE, ["python"], {"../escape": b""}, 5),
        (MYSTERY.IMAGE, ["python"], {"a.py": "text"}, 5),
        (MYSTERY.IMAGE, ["python"], {}, 0),
    ],
)
def test_job_requests_are_validated(image, argv, files, seconds):
    with pytest.raises((ValueError, TypeError)):
        validate_job(image, argv, files, seconds)
