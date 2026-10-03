"""Domain worker images and job limits: pinning, bounds, and recorded settings."""

import pytest
from test_experimental_mystery import MYSTERY, LocalJobs

from warranted._jobs import (
    DEFAULT_LIMITS,
    JobContext,
    JobLimits,
    pinned_image,
    validate_job,
)
from warranted._sandbox import IMAGE, SANDBOX_ID, Sandbox, sandbox_id
from warranted._tasks import Project
from warranted._worker import Episode

LOCAL_ID = "a" * 64


@pytest.mark.parametrize(
    "image, ok",
    [
        (IMAGE, True),
        (LOCAL_ID, True),
        ("localhost/reschema-toolchain:1", False),
        ("python:3.12", False),
        ("sha256:" + LOCAL_ID, False),
        ("a" * 63, False),
        ("A" * 64, False),
        (None, False),
    ],
)
def test_images_are_pinned_by_digest_or_local_image_id(image, ok):
    assert pinned_image(image) is ok


@pytest.mark.parametrize(
    "field, value",
    [
        ("memory_mb", 15),
        ("memory_mb", 8193),
        ("pids", 7),
        ("scratch_mb", 0),
        ("cpus", 9),
        ("cpus", 1.5),
    ],
)
def test_job_limits_are_bounded(field, value):
    with pytest.raises(ValueError):
        JobLimits(**{field: value})


def test_long_jobs_are_allowed_up_to_an_hour():
    validate_job(IMAGE, ["true"], {}, 3600)
    with pytest.raises(ValueError):
        validate_job(IMAGE, ["true"], {}, 3601)


def test_each_job_records_its_limits_and_timeout():
    ctx = JobContext(LocalJobs())
    limits = JobLimits(memory_mb=1024, pids=128, scratch_mb=64, cpus=2)
    ctx.run_job(
        IMAGE, ["python", "-c", "print(1)"], {}, timeout_seconds=30, limits=limits
    )
    ctx.run_job(IMAGE, ["python", "-c", "print(2)"], {})
    first, second = ctx.job_log
    assert first["limits"] == limits.record() and first["timeout_seconds"] == 30
    assert second["limits"] == DEFAULT_LIMITS.record()


def test_the_sandbox_requires_a_pinned_image_matching_the_episode(tmp_path):
    episode = Episode("e", "o", (), environment=sandbox_id(LOCAL_ID))
    with pytest.raises(ValueError, match="pinned"):
        Sandbox(tmp_path, episode, image="python:3.12")
    with pytest.raises(ValueError, match="container environment"):
        Sandbox(tmp_path, Episode("e", "o", (), environment=SANDBOX_ID), image=LOCAL_ID)


def test_a_project_runs_workers_in_the_domain_image_by_default(tmp_path):
    class Toolchain(MYSTERY.MysteryDomain):
        worker_image = LOCAL_ID

    proj = Project.create(tmp_path / "p", Toolchain(), {"model": 1}, jobs=LocalJobs())
    assert proj.environment_id == sandbox_id(LOCAL_ID)
    assert proj.environment.func is Sandbox
    assert proj.environment.keywords == {"image": LOCAL_ID}


def test_a_domain_with_an_unpinned_worker_image_is_refused(tmp_path):
    class Floating(MYSTERY.MysteryDomain):
        worker_image = "python:3.12"

    with pytest.raises(ValueError, match="worker image"):
        Project.create(tmp_path / "p", Floating(), {"model": 1}, jobs=LocalJobs())
    assert not (tmp_path / "p").exists()
