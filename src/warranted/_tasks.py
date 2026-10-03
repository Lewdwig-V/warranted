"""Prototype task layer for M7. No stability promise.

This module tests the shape proposed in docs/proposals/m7-harness-api.md against the
existing ledger, worker, and acceptance boundaries. Names and behavior may change
or disappear without notice. It covers one slice: TOML tasks, a domain's checkers,
explicit run IDs, one episode per submission, worker-visible feedback, and the six
run outcomes, plus the duplicate guard and scoped memory. Revisions and campaigns
are absent.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from importlib.metadata import version as package_version
from pathlib import Path
from time import perf_counter_ns
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from warranted._acceptance import Acceptance, AcceptanceContext, Evidence, Status
from warranted._claims import Applicability, Claims
from warranted._exports import export_evidence
from warranted._guard import DuplicateGuard, default_normalize
from warranted._jobs import JobContext, JobRunner, PodmanJobs, pinned_image
from warranted._ledger import (
    ROOT_SCOPE,
    Balance,
    BudgetExceeded,
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
)
from warranted._operations import (
    Operation,
    Requests,
    reconcile_operation,
    validate_operations,
)
from warranted._sandbox import Sandbox, sandbox_id
from warranted._worker import (
    TOKEN_UNITS,
    Episode,
    UnknownOutcome,
    record_once,
    run_workflow,
    submitted_files,
)

PRODUCER = "warranted-tasks"
# A submission refused by the duplicate guard: never checked, still counted.
DUPLICATE = "duplicate"
# Feedback reaches the worker's context; a checker that writes more is faulty.
FEEDBACK_LIMIT = 64 * 1024
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,100}")
_RUN_ID = re.compile(r"run-[0-9a-f]{12}")
# Task splits a campaign keeps apart (invariant 7).
SPLITS = ("training", "development", "held-out")
# Scoped memory: checker facts, worker notes, and the per-run snapshot.
NOTES, MEMORY = "notes.json", "memory.json"
# A real model adapter's configuration, recorded with the run so its requests can
# cite it; scripted models have none.
MODEL_PIN = "model-api.json"
# The campaign and split a run belongs to; memory is shared within a split only.
CAMPAIGN = "campaign.json"
# The project revisions a run's task included when the run was created.
REVISED = "project-revisions.json"
FACTS_COUNT, FACTS_LIMIT = 16, 16 * 1024
NOTES_COUNT, NOTES_LIMIT = 32, 16 * 1024
MEMORY_LIMIT = 64 * 1024
_RESERVED = {"context.json", "result.json", "workspace", "responses", NOTES, MEMORY}
# Captured beside the candidate but never shown to a checker.
_WORKER_NOTES = {NOTES, "notes-error.txt"}


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class VerdictStatus(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"
    UNPROVED = "unproved"  # could not be established within limits; fails the gate
    UNSUPPORTED = "unsupported"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class RunOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True)
class Verdict:
    """A checker's result. `feedback` is shown to this run's worker; `facts` reach
    later tasks in the same memory scope if the submission is accepted."""

    status: VerdictStatus
    feedback: Any = None
    host_only: Any = None
    facts: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "status", VerdictStatus(self.status))
        if not isinstance(self.facts, (tuple, list)):
            raise TypeError("facts must be a sequence of JSON values")
        object.__setattr__(self, "facts", tuple(self.facts))
        _json(self.feedback), _json(self.host_only)  # must be JSON-serialisable
        # Facts reach later workers and claims; strict JSON only (no NaN or Infinity).
        json.dumps(list(self.facts), allow_nan=False)


class CheckContext(JobContext):
    """What a checker may read and do. Seeds and jobs are kept as host-only evidence.

    `candidate`, `inputs`, and `private` are exact bytes. `draw_seed` returns fresh
    entropy and records it; `run_job` runs untrusted code in a contained job and
    records the job and its result. Both records stay out of worker feedback.
    """

    def __init__(self, candidate, inputs, private, jobs: JobRunner | None = None):
        super().__init__(jobs)
        self.candidate: Mapping[str, bytes] = MappingProxyType(dict(candidate))
        self.inputs: Mapping[str, bytes] = MappingProxyType(dict(inputs))
        self.private: Mapping[str, bytes] = MappingProxyType(dict(private))


class Checker(Protocol):
    """Trusted host code that assesses captured bytes.

    Set `isolated = True` only when the check touches no state shared with other
    runs: no files, services, or caches outside its inputs. Isolated checks run in
    the run's ledger scope; others run in the root scope, where an unknown outcome
    blocks every run.
    """

    version: str

    def check(self, ctx: CheckContext) -> Verdict: ...


def _isolated(checker: Checker) -> bool:
    return getattr(checker, "isolated", False) is True


class Domain(Protocol):
    name: str
    version: str
    worker_image: str
    checkers: Mapping[str, Checker]
    # Optional: further files or directories whose contents the checkers depend on,
    # such as code loaded with runpy or importlib, or data files. Imports are not
    # followed automatically, so undeclared code is not pinned.
    # sources: Sequence[Path]
    # Optional: host-mediated operations the worker may request by name.
    # operations: Mapping[str, Operation]


def _operations(domain: Domain) -> Mapping[str, Operation]:
    return getattr(domain, "operations", {})


def _source_files(domain: Domain) -> list[tuple[str, bytes]]:
    """The class source files plus every file under the domain's declared sources.

    Names are machine independent: class files by file name, declared sources by
    position and relative path. Moving the tree keeps the identity; editing,
    adding, or removing a file changes it.
    """
    files: list[tuple[str, Path]] = []
    for item in (domain, *domain.checkers.values(), *_operations(domain).values()):
        path = inspect.getsourcefile(type(item))
        if path is None:
            raise ValueError("domain and checker classes need source files")
        files.append(("class/" + Path(path).name, Path(path)))
    for index, declared in enumerate(getattr(domain, "sources", ())):
        path = Path(declared)
        if path.is_file():
            files.append((f"source/{index}/{path.name}", path))
        elif path.is_dir():
            files.extend(
                (f"source/{index}/{child.relative_to(path).as_posix()}", child)
                for child in sorted(path.rglob("*"))
                if child.is_file() and "__pycache__" not in child.parts
            )
        else:
            raise ValueError(f"declared domain source does not exist: {declared}")
    return sorted({(name, path.read_bytes()) for name, path in files})


# The installed Warranted package. An editable install keeps its version string
# while its code changes, so the identity pins the package's files, not only the
# version.
PACKAGE_ROOT = Path(__file__).resolve().parent


def _tree_digest(root: Path) -> str:
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    )
    return _digest(
        _json(
            [[p.relative_to(root).as_posix(), _digest(p.read_bytes())] for p in files]
        )
    )


def domain_identity(domain: Domain) -> dict[str, str]:
    """Name, version, image, Warranted build, and a digest of the domain's source."""
    sources = _source_files(domain)
    identity = {
        "domain": domain.name,
        "domain_version": domain.version,
        "worker_image": domain.worker_image,
        "domain_source": _digest(
            _json([[name, _digest(data)] for name, data in sources])
        ),
        "warranted": package_version("warranted"),
        "warranted_source": _tree_digest(PACKAGE_ROOT),
    }
    for name, checker in sorted(domain.checkers.items()):
        identity[f"checker/{name}"] = checker.version
        identity[f"checker/{name}/isolated"] = str(_isolated(checker)).lower()
    for name, operation in sorted(_operations(domain).items()):
        identity[f"operation/{name}"] = json.dumps(
            {
                "version": operation.version,
                "effect": operation.effect,
                "shared": operation.shared,
                "reusable": operation.reusable,
                "inputs": list(operation.inputs),
                "units": sorted(operation.units),
            },
            sort_keys=True,
        )
    return identity


def _project_identity(domain: Domain, environment_id: str, jobs) -> dict[str, str]:
    """A project binds its domain, worker environment, and checker job runner."""
    if not pinned_image(domain.worker_image):
        raise ValueError("domain worker image must be pinned by digest or image ID")
    runner = getattr(jobs, "identity", None)
    if type(runner) is not str or not runner:
        raise ValueError("job runner needs a stable identity string")
    return domain_identity(domain) | {
        "worker_environment": environment_id,
        "job_runner": runner,
    }


def _guard(table) -> DuplicateGuard | None:
    if table is None:
        return None
    if type(table) is not dict:
        raise ValueError("duplicate_guard must be a table")
    fields = set(DuplicateGuard.__dataclass_fields__)
    if set(table) != fields:
        raise ValueError(f"duplicate_guard needs exactly {sorted(fields)}")
    return DuplicateGuard(**table)


@dataclass(frozen=True)
class MemorySpec:
    """A task's memory scope, and the task files whose bytes define the family."""

    scope: str
    depends: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.scope) is not str or not _ID.fullmatch(self.scope):
            raise ValueError("invalid memory scope")
        if not isinstance(self.depends, (tuple, list)):
            raise ValueError("memory depends must list task file names")
        object.__setattr__(self, "depends", tuple(self.depends))
        if not all(type(name) is str for name in self.depends) or len(
            set(self.depends)
        ) != len(self.depends):
            raise ValueError("memory depends must name distinct task files")

    def record(self) -> dict:
        return {"scope": self.scope, "depends": list(self.depends)}


def _memory_channel(version: int) -> str:
    """The run-spec channel holding the memory snapshot for a contract version."""
    return MEMORY if version == 0 else f"memory/{version}.json"


def _read_files(
    table: Any, base: Path, resolve: Callable[[str], bytes] | None
) -> dict[str, bytes]:
    """Task-file values: paths relative to `base`, or `{ artifact = REF }`."""
    if not isinstance(table, dict):
        raise ValueError("task files must be a table of names")

    def read(value) -> bytes:
        if isinstance(value, str):
            return (base / value).read_bytes()
        if not isinstance(value, dict) or set(value) != {"artifact"}:
            raise ValueError("a task file is a path or { artifact = REF }")
        if resolve is None:
            raise ValueError("task names imported artifacts; load it through a project")
        return resolve(value["artifact"])

    return {name: read(value) for name, value in table.items()}


@dataclass(frozen=True)
class Revision:
    """An owner-approved change to a task's contract.

    It may replace, add, or remove worker-visible and private task files, change
    the required checks, and add a note to the objective. Checker code and the
    domain never change through a revision; a changed domain is a new project.
    The owner is attribution from trusted local files, not authentication.
    """

    id: str
    owner: str
    reason: str
    inputs: Mapping[str, bytes] = field(default_factory=dict)
    private: Mapping[str, bytes] = field(default_factory=dict)
    remove: tuple[str, ...] = ()
    checks: tuple[str, ...] | None = None
    note: str = ""

    def __post_init__(self):
        if type(self.id) is not str or not _ID.fullmatch(self.id):
            raise ValueError("invalid revision ID")
        for value in (self.owner, self.reason):
            if type(value) is not str or not value.strip():
                raise ValueError("a revision names its owner and reason")
        if type(self.note) is not str:
            raise ValueError("a revision note is text")
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))
        object.__setattr__(self, "private", MappingProxyType(dict(self.private)))
        object.__setattr__(self, "remove", tuple(self.remove))
        if self.checks is not None:
            object.__setattr__(self, "checks", tuple(self.checks))
        changed = (*self.inputs, *self.private)
        if not all(type(n) is str for n in (*changed, *self.remove)) or set(
            changed
        ) & set(self.remove):
            raise ValueError("a revision cannot both set and remove a file")
        if not (changed or self.remove or self.checks is not None or self.note):
            raise ValueError("a revision must change something")

    def apply(self, task: TaskSpec) -> TaskSpec:
        """The task under this revision; scheduled revisions are kept."""
        missing = set(self.remove) - task.inputs.keys() - task.private.keys()
        if missing:
            raise ValueError(
                f"revision removes files the task lacks: {sorted(missing)}"
            )
        inputs = {n: d for n, d in task.inputs.items() if n not in self.remove}
        private = {n: d for n, d in task.private.items() if n not in self.remove}
        for name in self.inputs:
            private.pop(name, None)
        for name in self.private:
            inputs.pop(name, None)
        objective = task.objective + (f"\n\n{self.note}" if self.note else "")
        return replace(
            task,
            objective=objective,
            inputs=inputs | dict(self.inputs),
            private=private | dict(self.private),
            checks=self.checks if self.checks is not None else task.checks,
        )

    def record(self) -> dict[str, bytes]:
        raw = {
            "revision.json": _json(
                {
                    "id": self.id,
                    "owner": self.owner,
                    "reason": self.reason,
                    "inputs": sorted(self.inputs),
                    "private": sorted(self.private),
                    "remove": list(self.remove),
                    "checks": None if self.checks is None else list(self.checks),
                    "note": self.note,
                }
            )
        }
        raw |= {f"input/{name}": data for name, data in self.inputs.items()}
        raw |= {f"private/{name}": data for name, data in self.private.items()}
        return raw

    @classmethod
    def from_record(cls, raw: Mapping[str, bytes]) -> Revision:
        meta = json.loads(raw["revision.json"])
        return cls(
            meta["id"],
            meta["owner"],
            meta["reason"],
            {n: raw[f"input/{n}"] for n in meta["inputs"]},
            {n: raw[f"private/{n}"] for n in meta["private"]},
            tuple(meta["remove"]),
            None if meta["checks"] is None else tuple(meta["checks"]),
            meta["note"],
        )

    @classmethod
    def load(
        cls, path: Path, resolve: Callable[[str], bytes] | None = None
    ) -> Revision:
        """Load a revision TOML file; file values work as in task files."""
        path = Path(path)
        data = tomllib.loads(path.read_text())
        known = {
            "id",
            "owner",
            "reason",
            "note",
            "checks",
            "remove",
            "inputs",
            "private",
        }
        if not data.keys() <= known or not {"id", "owner", "reason"} <= data.keys():
            raise ValueError("revision needs id, owner, and reason, and nothing else")
        return cls(
            data["id"],
            data["owner"],
            data["reason"],
            _read_files(data.get("inputs", {}), path.parent, resolve),
            _read_files(data.get("private", {}), path.parent, resolve),
            tuple(data.get("remove", ())),
            None if "checks" not in data else tuple(data["checks"]),
            data.get("note", ""),
        )


@dataclass(frozen=True)
class ScheduledRevision:
    """A revision the host applies once `after_submission` submissions are used."""

    after_submission: int
    revision: Revision

    def __post_init__(self):
        if type(self.after_submission) is not int or self.after_submission < 1:
            raise ValueError("after_submission must be a positive integer")
        if not isinstance(self.revision, Revision):
            raise ValueError("a scheduled revision needs a Revision")


@dataclass(frozen=True)
class TaskSpec:
    """What to solve. Loaded from TOML; inputs and private files are pinned bytes.

    `revisions` are scheduled contract revisions: submissions after a checkpoint
    are assessed under the revised contract (see `contract`).
    """

    id: str
    objective: str
    inputs: Mapping[str, bytes]
    private: Mapping[str, bytes]
    checks: tuple[str, ...]
    submissions: int
    check_budget: int | None = None
    duplicate_guard: DuplicateGuard | None = None
    memory: MemorySpec | None = None
    revisions: tuple[ScheduledRevision, ...] = ()

    def version(self, index: int) -> int:
        """How many scheduled revisions apply to submission `index`."""
        return sum(1 for r in self.revisions if r.after_submission < index)

    def contract(self, index: int) -> TaskSpec:
        """The contract in force for submission `index`, without its schedule."""
        task = replace(self, revisions=())
        for scheduled in self.revisions[: self.version(index)]:
            task = scheduled.revision.apply(task)
        return task

    @classmethod
    def load(
        cls, path: Path, resolve: Callable[[str], bytes] | None = None
    ) -> TaskSpec:
        """Load a task TOML file. File values are paths relative to the task file,
        or `{ artifact = "sha256:<hex>" }` for bytes imported into a project;
        `resolve` maps such a reference to its bytes (see `Project.load_task`).
        """
        path = Path(path)
        data = tomllib.loads(path.read_text())
        known = {
            "id",
            "objective",
            "inputs",
            "private",
            "checks",
            "budgets",
            "duplicate_guard",
            "memory",
            "revisions",
        }
        if not data.keys() <= known or not {"id", "objective", "checks"} <= set(data):
            raise ValueError("task needs id, objective, and checks, and nothing else")
        budgets = data.get("budgets", {})
        if not budgets.keys() <= {"submissions", "checks"}:
            raise ValueError(
                "unknown task budget; model, tool, and token budgets belong to runs"
            )

        def files(table: Any) -> dict[str, bytes]:
            return _read_files(table, path.parent, resolve)

        scheduled = []
        for entry in data.get("revisions", []):
            if not isinstance(entry, dict) or set(entry) != {
                "after_submission",
                "file",
            }:
                raise ValueError("each revision needs after_submission and file")
            scheduled.append(
                ScheduledRevision(
                    entry["after_submission"],
                    Revision.load(path.parent / entry["file"], resolve),
                )
            )

        memory = data.get("memory")
        if memory is not None:
            if not isinstance(memory, dict) or not {"scope"} <= memory.keys() <= {
                "scope",
                "depends",
            }:
                raise ValueError("memory needs a scope and optional depends")
            memory = MemorySpec(memory["scope"], memory.get("depends", ()))
        return cls(
            data["id"],
            data["objective"],
            files(data.get("inputs", {})),
            files(data.get("private", {})),
            tuple(data["checks"]),
            budgets.get("submissions", 1),
            budgets.get("checks"),
            _guard(data.get("duplicate_guard")),
            memory,
            tuple(scheduled),
        )

    def __post_init__(self):
        if type(self.id) is not str or not _ID.fullmatch(self.id):
            raise ValueError("invalid task ID")
        if type(self.objective) is not str or not self.objective.strip():
            raise ValueError("task needs an objective")
        for name in (*self.inputs, *self.private):
            if (
                not _NAME.fullmatch(name)
                or name in _RESERVED
                or name.startswith("feedback-")
            ):
                raise ValueError(f"unsafe or reserved file name: {name}")
        if self.inputs.keys() & self.private.keys():
            raise ValueError("a file cannot be both worker-visible and private")
        if (
            not self.checks
            or not all(type(c) is str and _NAME.fullmatch(c) for c in self.checks)
            or len(set(self.checks)) != len(self.checks)
        ):
            raise ValueError("task needs distinct, named required checks")
        if type(self.submissions) is not int or self.submissions < 1:
            raise ValueError("submission budget must be a positive integer")
        if self.check_budget is not None and (
            type(self.check_budget) is not int or self.check_budget < 0
        ):
            raise ValueError("check budget must be a non-negative integer")
        if self.duplicate_guard is not None and not isinstance(
            self.duplicate_guard, DuplicateGuard
        ):
            raise ValueError("duplicate_guard must be a DuplicateGuard")
        if self.memory is not None:
            if not isinstance(self.memory, MemorySpec):
                raise ValueError("memory must be a MemorySpec")
            if not set(self.memory.depends) <= self.inputs.keys() | self.private.keys():
                raise ValueError("memory depends names a file the task does not have")
        object.__setattr__(self, "revisions", tuple(self.revisions))
        if not all(isinstance(r, ScheduledRevision) for r in self.revisions):
            raise ValueError("revisions must be ScheduledRevision entries")
        points = [r.after_submission for r in self.revisions]
        if points != sorted(set(points)) or any(p >= self.submissions for p in points):
            raise ValueError(
                "scheduled revisions need increasing checkpoints before the last "
                "submission"
            )
        ids = [r.revision.id for r in self.revisions]
        if len(set(ids)) != len(ids):
            raise ValueError("scheduled revisions need distinct IDs")
        if self.revisions:
            self.contract(self.submissions)  # every revised contract must be valid

    def record(self) -> dict[str, bytes]:
        raw = {
            "task.json": _json(
                {
                    "id": self.id,
                    "objective": self.objective,
                    "inputs": sorted(self.inputs),
                    "private": sorted(self.private),
                    "checks": list(self.checks),
                    "submissions": self.submissions,
                    "check_budget": self.check_budget,
                    "duplicate_guard": self.duplicate_guard
                    and self.duplicate_guard.record(),
                    "memory": self.memory and self.memory.record(),
                    "revisions": [r.after_submission for r in self.revisions],
                }
            )
        }
        raw |= {f"input/{name}": data for name, data in self.inputs.items()}
        raw |= {f"private/{name}": data for name, data in self.private.items()}
        for number, scheduled in enumerate(self.revisions, 1):
            raw |= {
                f"revision/{number}/{name}": data
                for name, data in scheduled.revision.record().items()
            }
        return raw


@dataclass(frozen=True)
class RunConfig:
    """Who solves the task and with what limits. The model boundary is supplied live."""

    model: str
    max_steps: int = 4
    budgets: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if type(self.model) is not str or not self.model.strip():
            raise ValueError("run configuration needs a model identity")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 100:
            raise ValueError("max_steps must be an integer from 1 to 100")
        budgets = dict(self.budgets)
        if (
            {"check", "submissions"} & budgets.keys()
            or not all(type(unit) is str and _NAME.fullmatch(unit) for unit in budgets)
            or any(type(value) is not int or value < 0 for value in budgets.values())
        ):
            raise ValueError(
                "run budgets cap model, tool, token, and operation units with "
                "non-negative integers; check and submission budgets belong to tasks"
            )
        object.__setattr__(self, "budgets", MappingProxyType(budgets))

    def record(self) -> bytes:
        return _json(
            {
                "model": self.model,
                "max_steps": self.max_steps,
                "budgets": dict(self.budgets),
            }
        )


@dataclass(frozen=True)
class Submission:
    index: int
    verdicts: Mapping[str, VerdictStatus]
    decision: str


@dataclass(frozen=True)
class RunResult:
    run_id: str
    outcome: RunOutcome
    submissions: tuple[Submission, ...]
    detail: str = ""


@dataclass(frozen=True)
class CampaignTask:
    """A task in a campaign, and the split it belongs to."""

    task: TaskSpec
    split: str

    def __post_init__(self):
        if not isinstance(self.task, TaskSpec):
            raise ValueError("campaign task must be a TaskSpec")
        if self.split not in SPLITS:
            raise ValueError(f"split must be one of {', '.join(SPLITS)}")


@dataclass(frozen=True)
class CampaignSpec:
    """Tasks times run configurations times repetitions, run serially in order.

    Each repetition runs every task under every configuration, in the order
    given. A memory scope may not be shared by tasks of different splits, so
    held-out results never draw on training or development facts.
    """

    id: str
    tasks: tuple[CampaignTask, ...]
    configs: Mapping[str, RunConfig]
    repetitions: int = 1

    def __post_init__(self):
        if type(self.id) is not str or not _ID.fullmatch(self.id):
            raise ValueError("invalid campaign ID")
        object.__setattr__(self, "tasks", tuple(self.tasks))
        object.__setattr__(self, "configs", MappingProxyType(dict(self.configs)))
        if not self.tasks or not all(isinstance(t, CampaignTask) for t in self.tasks):
            raise ValueError("campaign needs CampaignTask entries")
        ids = [t.task.id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("campaign tasks need distinct IDs")
        if not self.configs or not all(
            type(name) is str and _NAME.fullmatch(name) and isinstance(c, RunConfig)
            for name, c in self.configs.items()
        ):
            raise ValueError("campaign needs named run configurations")
        if type(self.repetitions) is not int or not 1 <= self.repetitions <= 1000:
            raise ValueError("repetitions must be an integer from 1 to 1000")
        splits: dict[str, set[str]] = {}
        for entry in self.tasks:
            if entry.task.memory is not None:
                splits.setdefault(entry.task.memory.scope, set()).add(entry.split)
        shared = sorted(scope for scope, found in splits.items() if len(found) > 1)
        if shared:
            raise ValueError(f"memory scopes shared across splits: {', '.join(shared)}")


@dataclass(frozen=True)
class CampaignRun:
    """One planned run and, once it exists, its status."""

    run_id: str
    task_id: str
    config: str
    split: str
    repetition: int
    status: RunStatus | None  # None until the campaign reaches this run


@dataclass(frozen=True)
class CampaignReport:
    campaign_id: str
    runs: tuple[CampaignRun, ...]


@dataclass(frozen=True)
class RunStatus:
    """What the ledger records about a run, read without running anything.

    `outcome` is None while the run can continue: it has submissions left and no
    recorded decision ended it. Resume it to continue. A run blocked by an
    unresolved operation reports `unknown`.
    """

    run_id: str
    task_id: str
    model: str
    submissions_allowed: int
    submissions: tuple[Submission, ...]
    outcome: RunOutcome | None
    blocked: tuple[str, ...]
    accounting: Mapping[str, Balance]


@dataclass(frozen=True)
class MemoryEntry:
    """Facts from one check, or notes from one submission, assessed for a task.

    The tier is derived from the recorded acceptance decision, never stored.
    """

    run_id: str
    task_id: str
    submission: int
    source: str  # "check/<name>" or "notes"
    items: tuple
    accepted: bool
    applicability: Applicability
    dependencies: Mapping[str, Applicability]
    sequence: int  # ledger order of the entry record
    evidence: Evidence  # the entry record itself
    split: str | None = None  # the producing run's campaign split, if any

    @property
    def tier(self) -> str | None:
        if not self.accepted:
            return None
        return "promoted" if self.source == "notes" else "verified"

    @property
    def shown(self) -> bool:
        return self.accepted and self.applicability is Applicability.CURRENT


def _notes(data: bytes) -> tuple[str, ...] | None:
    """Worker notes, or None when the file is malformed or too large."""
    if len(data) > NOTES_LIMIT:
        return None
    try:
        notes = json.loads(data)
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        type(notes) is not list
        or len(notes) > NOTES_COUNT
        or not all(type(note) is str for note in notes)
    ):
        return None
    return tuple(notes)


@dataclass
class _Run:
    run_id: str
    spec: Any  # Observation
    task: TaskSpec
    config: RunConfig
    policy: Evidence
    campaign: Mapping[str, str] | None = None  # {"campaign": id, "split": split}

    @property
    def split(self) -> str | None:
        return None if self.campaign is None else self.campaign["split"]

    def files(self, index: int) -> tuple[dict[str, Evidence], dict[str, Evidence]]:
        """Worker-visible and private file evidence for submission `index`."""
        inputs = {n: self.evidence(f"input/{n}") for n in self.task.inputs}
        private = {n: self.evidence(f"private/{n}") for n in self.task.private}
        applied = self.task.revisions[: self.task.version(index)]
        for number, scheduled in enumerate(applied, 1):
            revision = scheduled.revision
            for name in revision.remove:
                inputs.pop(name, None)
                private.pop(name, None)
            for name in revision.inputs:
                private.pop(name, None)
                inputs[name] = self.evidence(f"revision/{number}/input/{name}")
            for name in revision.private:
                inputs.pop(name, None)
                private[name] = self.evidence(f"revision/{number}/private/{name}")
        return inputs, private

    def contract_evidence(self, index: int) -> Evidence:
        """The recorded contract version that decides submission `index`."""
        version = self.task.version(index)
        if version == 0:
            return self.evidence("task.json")
        return self.evidence(f"revision/{version}/revision.json")

    @property
    def scope(self) -> str:
        return f"run/{self.run_id}"

    @property
    def model_pin(self) -> Evidence | None:
        """The model adapter's recorded configuration, if the run pinned one."""
        if MODEL_PIN not in self.spec.artifacts:
            return None
        return self.evidence(MODEL_PIN)

    def evidence(self, channel: str) -> Evidence:
        return Evidence.captured(self.spec, channel)


class Project:
    """One ledger and its single writer. Runs are serialised."""

    def __init__(
        self,
        root: Path,
        domain: Domain,
        *,
        environment: Callable | None = None,
        environment_id: str | None = None,
        jobs: JobRunner | None = None,
    ):
        """By default, workers run in containers from the domain's worker image."""
        self.root, self.domain = Path(root), domain
        self.environment = environment or partial(Sandbox, image=domain.worker_image)
        self.environment_id = environment_id or sandbox_id(domain.worker_image)
        self.jobs = jobs if jobs is not None else PodmanJobs()
        if set(domain.checkers) and not all(
            _NAME.fullmatch(name) for name in domain.checkers
        ):
            raise ValueError("invalid checker name")
        with Ledger.open(self.ledger_root) as ledger:
            if dict(ledger.project.manifest.environment) != self._identity():
                raise ValueError("domain or environment differs from the project")

    @property
    def ledger_root(self) -> Path:
        return self.root / "ledger"

    def _identity(self) -> dict[str, str]:
        return _project_identity(self.domain, self.environment_id, self.jobs)

    @classmethod
    def create(
        cls,
        root: Path,
        domain: Domain,
        allowances: Mapping[str, int],
        **options,
    ) -> Project:
        root = Path(root)
        identity = _project_identity(
            domain,
            options.get("environment_id") or sandbox_id(domain.worker_image),
            options.get("jobs") or PodmanJobs(),
        )
        root.mkdir(parents=True)
        with Ledger.create(
            root / "ledger",
            Manifest(
                "warranted-project",
                "1",
                str(uuid4()),
                str(uuid4()),
                identity,
                {"model": 0, "tool": 0, "check": 0}
                | {u: 0 for op in _operations(domain).values() for u in op.units}
                | dict(allowances),
            ),
            {},
        ):
            pass
        return cls(root, domain, **options)

    def start(
        self, task: TaskSpec, config: RunConfig, model, *, run_id: str | None = None
    ) -> RunResult:
        """Create a new run, then run it.

        The run gets a fresh ID unless a campaign planned one; a planned ID must
        not name an existing run.
        """
        return self._start(task, config, model, run_id, None)

    def _start(self, task, config, model, run_id, campaign) -> RunResult:
        applied = self.revisions(task.id)
        task = self._revised(task, applied)
        required = {
            name
            for index in range(1, task.submissions + 1)
            for name in task.contract(index).checks
        }
        unknown = required - set(self.domain.checkers)
        if unknown:
            raise ValueError(f"task names unknown checks: {sorted(unknown)}")
        if getattr(model, "model", None) != config.model:
            raise ValueError("model boundary differs from the run configuration")
        if run_id is None:
            run_id = "run-" + uuid4().hex[:12]
        elif type(run_id) is not str or not _RUN_ID.fullmatch(run_id):
            raise ValueError(f"invalid run ID: {run_id!r}")
        if run_id in self.runs():
            raise ValueError(f"run {run_id} already exists; resume it instead")
        caps = dict(config.budgets)
        validate_operations(_operations(self.domain), {**task.inputs, **task.private})
        shared = {
            unit
            for operation in _operations(self.domain).values()
            if operation.shared
            for unit in operation.units
        }
        if shared & caps.keys():
            # Shared operations reserve in the root scope, which a run cap cannot see.
            raise ValueError(
                f"run budgets cannot cap units of shared operations: "
                f"{sorted(shared & caps.keys())}"
            )
        with Ledger.open(self.ledger_root) as ledger:
            self._require_token_units(model, ledger.project.manifest.allowances)
        if task.check_budget is not None:
            # Every scheduled contract's checks count: a revision must not move a
            # check into the root scope, where the run's cap cannot see it.
            shared = sorted(
                n for n in required if not _isolated(self.domain.checkers[n])
            )
            if shared:
                # A shared-state check runs in the root scope, where a run's cap
                # cannot apply; refuse rather than silently ignore the budget.
                raise ValueError(
                    f"check budgets require isolated checkers; not isolated: {shared}"
                )
            caps["check"] = task.check_budget
        with Ledger.open(self.ledger_root) as ledger:
            session = ledger.start_session()
            # Fixed now and recorded with the spec, so resume never recomputes it.
            split = None if campaign is None else campaign["split"]
            # One snapshot per contract version, each assessed against that
            # contract's files, so a scheduled revision of a depended-on file
            # withholds the facts it makes stale.
            snapshots, cited = {}, {}
            for version, index in enumerate(
                [1, *(r.after_submission + 1 for r in task.revisions)]
            ):
                snapshot, found = self._snapshot(
                    ledger, session, task.contract(index), split
                )
                if snapshot is not None:
                    snapshots[_memory_channel(version)] = snapshot
                    cited |= found
            # Caps are recorded before any run record, so a resume cannot change them.
            ledger.open_scope(session, f"run/{run_id}", caps)
            record_once(
                ledger,
                session,
                Origin(f"run/{run_id}/spec", "run-spec", PRODUCER, "1", cited),
                task.record()
                | {"config.json": config.record()}
                | snapshots
                | ({CAMPAIGN: _json(campaign)} if campaign is not None else {})
                | {REVISED: _json([r.id for r in applied])}
                | self._model_record(model),
            )
            record_once(
                ledger,
                session,
                Origin(f"run/{run_id}/policy", "run-policy", PRODUCER, "1", {}),
                {"policy.json": _json(self._policy_body(task.checks))},
            )
        return self.resume(run_id, model)

    @staticmethod
    def _policy_body(checks) -> dict:
        return {
            "version": 1,
            "owner": "task-owner",
            "transition": "accept-candidate",
            "requirements": {
                name: {"kind": "gate", "check": name, "field": "passed"}
                for name in checks
            },
        }

    def _policy(self, ledger: Ledger, session: str, run: _Run, index: int) -> Evidence:
        """The acceptance policy of the contract that decides submission `index`."""
        version = run.task.version(index)
        if version == 0:
            return run.policy
        capture = record_once(
            ledger,
            session,
            Origin(
                f"run/{run.run_id}/policy/{version}", "run-policy", PRODUCER, "1", {}
            ),
            {"policy.json": _json(self._policy_body(run.task.contract(index).checks))},
        )
        return Evidence.captured(capture, "policy.json")

    def status(self, run_id: str) -> RunStatus:
        """The run's recorded submissions, outcome, blockers, and run-scope usage.

        Read-only: it records nothing, runs no checker, and needs no model.
        """
        with Ledger.open(self.ledger_root) as ledger:
            run = self._load(ledger, run_id)
            submissions = []
            for index in range(1, run.task.submissions + 1):
                submission = self._recorded_submission(ledger, run, index)
                if submission is None:
                    break
                submissions.append(submission)
            # A run scope's unresolved operations include the root scope's.
            blocked = tuple(
                dict.fromkeys(
                    op.request.origin.operation_id
                    for op in ledger.unresolved(run.scope)
                )
            )
            accounting = dict(ledger.accounting(run.scope))
            ended = None
            if len(submissions) < run.task.submissions:
                ended = self._ended_without_submission(
                    ledger, self._episode_id(run, len(submissions) + 1)
                )
        outcome = None
        final = {
            str(Status.ACCEPTED): RunOutcome.ACCEPTED,
            str(Status.UNKNOWN): RunOutcome.UNKNOWN,
            str(Status.UNSUPPORTED): RunOutcome.UNSUPPORTED,
            str(Status.INFRASTRUCTURE_FAILURE): RunOutcome.INFRASTRUCTURE_FAILURE,
        }
        if blocked:
            outcome = RunOutcome.UNKNOWN
        elif submissions and submissions[-1].decision in final:
            outcome = final[submissions[-1].decision]
        elif len(submissions) == run.task.submissions:
            outcome = RunOutcome.REJECTED
        elif ended is RunOutcome.INFRASTRUCTURE_FAILURE:
            outcome = ended
        elif ended is not None:
            # Resume would replay the same finished episode, so the run is over.
            outcome = RunOutcome.REJECTED if submissions else RunOutcome.INCOMPLETE
        return RunStatus(
            run.run_id,
            run.task.id,
            run.config.model,
            run.task.submissions,
            tuple(submissions),
            outcome,
            blocked,
            MappingProxyType(accounting),
        )

    @staticmethod
    def _ended_without_submission(ledger: Ledger, episode_id: str):
        """How a finished episode ended without a submission, or None.

        A completed attempt recorded as infrastructure failure ends the run as
        one; a failed model attempt, or a finished episode that did not submit,
        ends it like a step limit does. An episode still in progress, or one that
        submitted, gives None.
        """
        prefix, failed = f"episode/{episode_id}/", None
        for operation in ledger.operations():
            origin = operation.request.origin
            if origin.operation_id.startswith(prefix) and operation.completion:
                outcome = operation.completion.result.outcome
                if outcome is Outcome.INFRASTRUCTURE_FAILURE:
                    return RunOutcome.INFRASTRUCTURE_FAILURE
                if outcome is Outcome.FAILED and origin.kind == "model":
                    failed = RunOutcome.INCOMPLETE
        if failed is not None:
            return failed
        for o in ledger.history():
            if o.origin.operation_id == prefix + "finished":
                result = json.loads(ledger.read_artifact(o.artifacts["result.json"]))
                if result.get("exit_status") != "Submitted":
                    return RunOutcome.INCOMPLETE
        return None

    def _recorded_submission(self, ledger: Ledger, run: _Run, index: int):
        """A submission's recorded decision, or None if none is recorded yet."""
        guard = f"run/{run.run_id}/submission/{index}/guard"
        for o in ledger.history():
            if o.origin.operation_id == guard:
                refused = json.loads(ledger.read_artifact(o.artifacts["guard.json"]))
                if refused["refused"]:
                    return Submission(index, {}, DUPLICATE)
        target = self._recorded_target(ledger, run, index)
        if target is None:
            return None
        decision = None
        for operation in ledger.operations():
            origin = operation.request.origin
            if (
                origin.kind == "decision"
                and origin.inputs.get(target.name) == target.artifact
                and operation.completion is not None
            ):
                ref = operation.completion.observation.artifacts["decision.json"]
                decision = json.loads(ledger.read_artifact(ref))["status"]
        if decision is None:
            return None
        verdicts = {}
        for name in run.task.contract(index).checks:
            operation = ledger.lookup(
                self._check_request(ledger, run, index, name, target)
            )
            if operation is not None and operation.completion is not None:
                ref = operation.completion.observation.artifacts["verdict.json"]
                verdicts[name] = VerdictStatus(
                    json.loads(ledger.read_artifact(ref))["status"]
                )
        return Submission(index, verdicts, decision)

    def export(self, run_id: str, destination: Path) -> Path:
        """Copy a run's worker-visible and decision records to a new directory.

        Included: the task (without private files), configuration, memory
        snapshot, policy, captured submissions, feedback, verdicts, facts, guard
        decisions, and acceptance decisions, with check and decision receipts.
        Excluded: private task files, host-only verdict data, drawn seeds, job
        logs and outputs, and model and tool transcripts. The export is not
        authoritative; the ledger is. Returns the export's index.json.
        """
        public = {"task.json", "config.json", MEMORY}
        shown = {"result.json", "verdict.json", "facts.json"}
        with Ledger.open(self.ledger_root) as ledger:
            run = self._load(ledger, run_id)
            prefix = f"run/{run.run_id}/"
            episodes = tuple(
                f"episode/{self._episode_id(run, i)}/tool/"
                for i in range(1, run.task.submissions + 1)
            )
            observations, operations = {}, []
            for o in ledger.history():
                name = o.origin.operation_id
                channels = ()
                if name == f"{prefix}spec":
                    channels = [
                        c
                        for c in o.artifacts
                        if c in public
                        or c.startswith(("input/", "memory/"))
                        or (c.startswith("revision/") and "/private/" not in c)
                    ]
                elif name.startswith(prefix) and o.origin.kind in (
                    "run-policy",
                    "submission",
                    "feedback",
                    "duplicate-guard",
                ):
                    channels = list(o.artifacts)
                elif name.startswith(episodes):
                    channels = [c for c in o.artifacts if c.startswith("candidate/")]
                if channels:
                    observations[o.sequence] = tuple(channels)
            targets = {
                ref.name
                for i in range(1, run.task.submissions + 1)
                if (ref := self._recorded_target(ledger, run, i)) is not None
            }
            for operation in ledger.operations():
                origin = operation.request.origin
                own_check = origin.kind == "check" and origin.operation_id.startswith(
                    prefix
                )
                decision = origin.kind == "decision" and targets & origin.inputs.keys()
                if not (own_check or decision):
                    continue
                operations.append(origin.operation_id)
                if operation.completion is not None:
                    observation = operation.completion.observation
                    observations[observation.sequence] = tuple(
                        c
                        for c in observation.artifacts
                        if c in shown or c == "decision.json"
                    )
            return export_evidence(
                ledger,
                Path(destination),
                observations=observations,
                operations=tuple(operations),
            )

    def revise(self, task_id: str, revision: Revision) -> None:
        """Record an owner-approved revision of a task for new runs.

        New runs of the task use the revised contract. A run that started before
        the revision cannot be resumed, and a campaign pinned before it is
        refused; start a new run or campaign. Recording the same revision again
        changes nothing; a different revision under a used ID is refused.
        """
        if type(task_id) is not str or not _ID.fullmatch(task_id):
            raise ValueError("invalid task ID")
        if not isinstance(revision, Revision):
            raise TypeError("expected a Revision")
        with Ledger.open(self.ledger_root) as ledger:
            record_once(
                ledger,
                ledger.start_session(),
                Origin(
                    f"task-revision/{task_id}/{revision.id}",
                    "task-revision",
                    PRODUCER,
                    "1",
                    {},
                ),
                revision.record(),
            )

    def revisions(self, task_id: str) -> tuple[Revision, ...]:
        """The task's recorded project revisions, oldest first."""
        prefix = f"task-revision/{task_id}/"
        with Ledger.open(self.ledger_root) as ledger:
            return tuple(
                Revision.from_record(
                    {
                        name: ledger.read_artifact(ref)
                        for name, ref in o.artifacts.items()
                    }
                )
                for o in ledger.history()
                if o.origin.kind == "task-revision"
                and o.origin.operation_id.startswith(prefix)
            )

    @staticmethod
    def _revised(task: TaskSpec, revisions) -> TaskSpec:
        for revision in revisions:
            task = revision.apply(task)
        return task

    def current_task(self, task: TaskSpec) -> TaskSpec:
        """`task` under every project revision recorded for its ID."""
        return self._revised(task, self.revisions(task.id))

    def import_artifact(self, data: bytes) -> str:
        """Record bytes once in the project, such as a domain-built binary.

        Returns the reference `sha256:<hex>` that task files use. Importing the
        same bytes again returns the same reference and records nothing new.
        """
        if type(data) is not bytes:
            raise TypeError("import takes bytes")
        digest = _digest(data)
        with Ledger.open(self.ledger_root) as ledger:
            record_once(
                ledger,
                ledger.start_session(),
                Origin(f"import/{digest}", "import", PRODUCER, "1", {}),
                {"artifact": data},
            )
        return f"sha256:{digest}"

    def imported(self, reference: str) -> bytes:
        """The bytes of an imported artifact; refuses an unknown reference."""
        if type(reference) is not str or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", reference
        ):
            raise ValueError(f"invalid artifact reference: {reference!r}")
        operation_id = "import/" + reference.removeprefix("sha256:")
        with Ledger.open(self.ledger_root) as ledger:
            for o in ledger.history():
                if o.origin.operation_id == operation_id and o.origin.kind == "import":
                    return ledger.read_artifact(o.artifacts["artifact"])
        raise ValueError(f"artifact not imported into this project: {reference}")

    def load_task(self, path: Path) -> TaskSpec:
        """Load a task file, resolving `{ artifact = REF }` from this project."""
        return TaskSpec.load(path, self.imported)

    @staticmethod
    def _task_digest(task: TaskSpec) -> str:
        return _digest(
            _json({name: _digest(data) for name, data in sorted(task.record().items())})
        )

    @staticmethod
    def _campaign_plans(ledger: Ledger) -> list[dict]:
        return [
            json.loads(ledger.read_artifact(o.artifacts["plan.json"]))
            for o in ledger.history()
            if o.origin.kind == "campaign-plan"
        ]

    def _planned(self, ledger: Ledger, campaign_id: str, entry: dict) -> _Run:
        """The existing run for a planned entry; refuses one that does not match."""
        run = self._load(ledger, entry["run"])
        recorded = (
            ledger.read_artifact(run.spec.artifacts[MODEL_PIN])
            if MODEL_PIN in run.spec.artifacts
            else None
        )
        if (
            run.campaign != {"campaign": campaign_id, "split": entry["split"]}
            or self._task_digest(run.task) != entry["task_digest"]
            or json.loads(run.config.record()) != entry["config_record"]
            or (None if recorded is None else _digest(recorded)) != entry["adapter"]
        ):
            raise ValueError(
                f"run {entry['run']} does not match its entry in campaign "
                f"{campaign_id}; it was not started by the campaign"
            )
        return run

    def _campaign_plan(self, ledger: Ledger, campaign_id: str) -> dict | None:
        for o in ledger.history():
            if o.origin.operation_id == f"campaign/{campaign_id}":
                return json.loads(ledger.read_artifact(o.artifacts["plan.json"]))
        return None

    def plan_campaign(self, spec: CampaignSpec, models: Mapping[str, Any]) -> dict:
        """Pin the campaign's plan, assigning every planned run its ID.

        The first call records the plan. Later calls must describe the same
        tasks, configurations, models, splits, and repetitions, and return the
        recorded plan with its run IDs; anything else is refused.
        """
        if set(models) != set(spec.configs):
            raise ValueError("campaign needs one model for each run configuration")
        for name, config in spec.configs.items():
            if getattr(models[name], "model", None) != config.model:
                raise ValueError(f"model for {name} differs from its configuration")
        entries = [
            {
                "task": entry.task.id,
                "task_digest": self._task_digest(self.current_task(entry.task)),
                "split": entry.split,
                "scope": entry.task.memory and entry.task.memory.scope,
                "config": name,
                "config_record": json.loads(config.record()),
                "adapter": _digest(self._model_record(models[name]).get(MODEL_PIN, b""))
                if self._model_record(models[name])
                else None,
                "repetition": repetition,
            }
            for repetition in range(1, spec.repetitions + 1)
            for entry in spec.tasks
            for name, config in spec.configs.items()
        ]
        with Ledger.open(self.ledger_root) as ledger:
            for other in self._campaign_plans(ledger):
                if other["id"] == spec.id:
                    continue
                for planned in other["entries"]:
                    for entry in entries:
                        if (
                            entry["scope"] is not None
                            and entry["scope"] == planned["scope"]
                            and entry["split"] != planned["split"]
                        ):
                            raise ValueError(
                                f"memory scope {entry['scope']} is used by "
                                f"{planned['split']} runs of campaign {other['id']}"
                            )
            pinned = self._campaign_plan(ledger, spec.id)
            if pinned is not None:
                if [
                    {k: v for k, v in e.items() if k != "run"}
                    for e in pinned["entries"]
                ] != entries:
                    raise ValueError(
                        f"campaign {spec.id} differs from its pinned plan; "
                        "use a new campaign ID for a changed plan"
                    )
                return pinned
            plan = {
                "version": 1,
                "id": spec.id,
                "repetitions": spec.repetitions,
                "entries": [
                    {"run": "run-" + uuid4().hex[:12], **entry} for entry in entries
                ],
            }
            record_once(
                ledger,
                ledger.start_session(),
                Origin(f"campaign/{spec.id}", "campaign-plan", PRODUCER, "1", {}),
                {"plan.json": _json(plan)},
            )
            return plan

    def run_campaign(
        self, spec: CampaignSpec, models: Mapping[str, Any]
    ) -> CampaignReport:
        """Run every planned run that has not finished, serially and in order.

        A planned run that does not exist yet is started with its planned ID; an
        open run is resumed; a finished run is left as it is. Rerunning a
        campaign therefore continues exactly the runs it planned.
        """
        plan = self.plan_campaign(spec, models)
        tasks = {entry.task.id: entry.task for entry in spec.tasks}
        existing = set(self.runs())
        for entry in plan["entries"]:
            task, name = tasks[entry["task"]], entry["config"]
            if entry["run"] not in existing:
                campaign = {"campaign": spec.id, "split": entry["split"]}
                self._start(
                    task, spec.configs[name], models[name], entry["run"], campaign
                )
                continue
            with Ledger.open(self.ledger_root) as ledger:
                self._planned(ledger, spec.id, entry)
            # Unknown runs resume too: resume reconciles what it can and never
            # redispatches an operation whose outcome is unknown.
            if self.status(entry["run"]).outcome in (None, RunOutcome.UNKNOWN):
                self.resume(entry["run"], models[name], task=self.current_task(task))
        return self.campaign_report(spec.id)

    def campaign_report(self, campaign_id: str) -> CampaignReport:
        """Every planned run with its status; unstarted runs have none."""
        with Ledger.open(self.ledger_root) as ledger:
            plan = self._campaign_plan(ledger, campaign_id)
            if plan is None:
                raise ValueError(f"unknown campaign: {campaign_id}")
            existing = {
                o.origin.operation_id.split("/")[1]
                for o in ledger.history()
                if o.origin.kind == "run-spec"
            }
            for entry in plan["entries"]:
                if entry["run"] in existing:
                    self._planned(ledger, campaign_id, entry)
        return CampaignReport(
            campaign_id,
            tuple(
                CampaignRun(
                    e["run"],
                    e["task"],
                    e["config"],
                    e["split"],
                    e["repetition"],
                    self.status(e["run"]) if e["run"] in existing else None,
                )
                for e in plan["entries"]
            ),
        )

    def campaigns(self) -> tuple[str, ...]:
        with Ledger.open(self.ledger_root) as ledger:
            return tuple(
                o.origin.operation_id.removeprefix("campaign/")
                for o in ledger.history()
                if o.origin.kind == "campaign-plan"
            )

    def runs(self) -> tuple[str, ...]:
        with Ledger.open(self.ledger_root) as ledger:
            return tuple(
                o.origin.operation_id.split("/")[1]
                for o in ledger.history()
                if o.origin.kind == "run-spec"
            )

    def memory(self, task: TaskSpec) -> tuple[MemoryEntry, ...]:
        """Every entry in the task's memory scope, newest first, assessed against the
        task's files and this project's domain. Withheld entries are included.

        Records only derived bookkeeping (entries, their claims, and version
        records), all idempotent; it runs no checker and changes no decision.
        """
        if task.memory is None:
            return ()
        task = self.current_task(task)
        with Ledger.open(self.ledger_root) as ledger:
            return self._memory(ledger, ledger.start_session(), task)

    def _version(self, ledger: Ledger, session: str, name: str, data: bytes):
        """One record per (name, bytes), so equal bytes compare equal across runs."""
        capture = record_once(
            ledger,
            session,
            Origin(
                f"memory-version/{name}/{_digest(data)}",
                "memory-version",
                PRODUCER,
                "1",
                {},
            ),
            {"version": data},
        )
        return Evidence.captured(capture, "version")

    def _versions(self, ledger, session, files: Mapping[str, bytes], names):
        domain = _json(dict(ledger.project.manifest.environment))
        versions = {"domain": self._version(ledger, session, "domain", domain)}
        for name in names:
            if name in files:
                versions[f"file/{name}"] = self._version(
                    ledger, session, f"file/{name}", files[name]
                )
        return versions

    @staticmethod
    def _recorded_target(ledger: Ledger, run: _Run, index: int) -> Evidence | None:
        for o in ledger.history():
            if o.origin.operation_id == f"run/{run.run_id}/submission/{index}":
                return Evidence.captured(o, "submission.json")
        return None

    @staticmethod
    def _accepted(ledger: Ledger, target: Evidence) -> bool:
        for operation in ledger.operations():
            origin = operation.request.origin
            if (
                origin.kind == "decision"
                and origin.inputs.get(target.name) == target.artifact
                and operation.completion is not None
            ):
                ref = operation.completion.observation.artifacts["decision.json"]
                if json.loads(ledger.read_artifact(ref))["status"] == Status.ACCEPTED:
                    return True
        return False

    def _sources(self, ledger: Ledger, run: _Run, index: int, target: Evidence):
        """Checker facts and worker notes recorded for one assessed submission."""
        for name in run.task.contract(index).checks:
            operation = ledger.lookup(
                self._check_request(ledger, run, index, name, target)
            )
            completion = operation and operation.completion
            if (
                completion is not None
                and "facts.json" in completion.observation.artifacts
            ):
                ref = Evidence.captured(completion.observation, "facts.json")
                items = json.loads(ledger.read_artifact(ref.artifact))
                yield f"check/{name}", ref, tuple(items)
        note = submitted_files(ledger, self._episode_id(run, index)).get(NOTES)
        if note is not None:
            items = _notes(ledger.read_artifact(note.artifact))
            if items:
                yield "notes", note, items

    def _memory(self, ledger: Ledger, session: str, task: TaskSpec):
        """Record any missing entries and claims, then assess them for `task`.

        Entries derive only from recorded checks and captures, so a crash before
        this runs loses nothing: the next call records them.
        """
        claims, found = Claims(ledger, session), []
        runs = [o for o in ledger.history() if o.origin.kind == "run-spec"]
        for spec in runs:
            run = self._load(ledger, spec.origin.operation_id.split("/")[1])
            memory = run.task.memory
            if memory is None or memory.scope != task.memory.scope:
                continue
            for index in range(1, run.task.submissions + 1):
                contract = run.task.contract(index)
                files = {**contract.inputs, **contract.private}
                target = self._recorded_target(ledger, run, index)
                if target is None:
                    continue
                for source, ref, items in self._sources(ledger, run, index, target):
                    body = {
                        "version": 1,
                        "scope": memory.scope,
                        "run": run.run_id,
                        "task": run.task.id,
                        "submission": index,
                        "source": source,
                        "items": list(items),
                    }
                    capture = record_once(
                        ledger,
                        session,
                        Origin(
                            f"run/{run.run_id}/submission/{index}/memory/{source}",
                            "memory-entry",
                            PRODUCER,
                            "1",
                            {target.name: target.artifact, ref.name: ref.artifact},
                        ),
                        {"entry.json": _json(body)},
                    )
                    entry = Evidence.captured(capture, "entry.json")
                    claim = claims.record(
                        f"memory entry {source} in scope {memory.scope}",
                        entry,
                        self._versions(ledger, session, files, memory.depends),
                        complete=True,
                    )
                    found.append((run, index, source, items, target, claim, capture))
        files = {**task.inputs, **task.private}
        current = self._versions(ledger, session, files, files)
        entries = []
        for run, index, source, items, target, claim, capture in found:
            assessment = claims.assess(claim, current)
            entries.append(
                MemoryEntry(
                    run.run_id,
                    run.task.id,
                    index,
                    source,
                    items,
                    self._accepted(ledger, target),
                    assessment.applicability,
                    assessment.dependencies,
                    capture.sequence,
                    Evidence.captured(capture, "entry.json"),
                    run.split,
                )
            )
        return tuple(sorted(entries, key=lambda e: e.sequence, reverse=True))

    def _snapshot(
        self, ledger: Ledger, session: str, task: TaskSpec, split: str | None = None
    ):
        """The memory file a new run shows its worker, and the entries it cites.

        Only entries from runs of the same split are shown; runs outside any
        campaign form their own group. Held-out runs therefore never see facts
        from training or development runs, whichever campaign produced them.
        """
        if task.memory is None:
            return None, {}
        shown, cited, size = [], {}, 0
        withheld = {"stale": 0, "unknown": 0, "over_limit": 0}
        for entry in self._memory(ledger, session, task):
            if entry.split != split:
                continue  # another split's memory is never shown, nor counted
            if not entry.accepted:
                continue  # unaccepted facts and notes are withheld silently
            if entry.applicability is not Applicability.CURRENT:
                withheld[str(entry.applicability)] += 1
                continue
            for item in entry.items:
                record = {
                    "tier": entry.tier,
                    "task": entry.task_id,
                    "run": entry.run_id,
                    "submission": entry.submission,
                    "source": entry.source,
                    "body": item,
                }
                if size + len(_json(record)) > MEMORY_LIMIT:
                    withheld["over_limit"] += 1
                    continue
                size += len(_json(record))
                shown.append(record)
                cited[entry.evidence.name] = entry.evidence.artifact
        raw = _json(
            {"scope": task.memory.scope, "entries": shown, "withheld": withheld}
        )
        return raw, cited

    def _load(self, ledger: Ledger, run_id: str) -> _Run:
        found = {
            o.origin.kind: o
            for o in ledger.history()
            if o.origin.operation_id in (f"run/{run_id}/spec", f"run/{run_id}/policy")
        }
        if set(found) != {"run-spec", "run-policy"}:
            raise ValueError(f"unknown run: {run_id}")
        spec = found["run-spec"]
        read = {name: ledger.read_artifact(ref) for name, ref in spec.artifacts.items()}
        meta = json.loads(read["task.json"])
        task = TaskSpec(
            meta["id"],
            meta["objective"],
            {name: read[f"input/{name}"] for name in meta["inputs"]},
            {name: read[f"private/{name}"] for name in meta["private"]},
            tuple(meta["checks"]),
            meta["submissions"],
            meta["check_budget"],
            _guard(meta["duplicate_guard"]),
            meta.get("memory") and MemorySpec(**meta["memory"]),
            tuple(
                ScheduledRevision(
                    after,
                    Revision.from_record(
                        {
                            name.removeprefix(f"revision/{number}/"): data
                            for name, data in read.items()
                            if name.startswith(f"revision/{number}/")
                        }
                    ),
                )
                for number, after in enumerate(meta.get("revisions", []), 1)
            ),
        )
        config = RunConfig(**json.loads(read["config.json"]))
        policy = Evidence.captured(found["run-policy"], "policy.json")
        campaign = json.loads(read[CAMPAIGN]) if CAMPAIGN in read else None
        return _Run(run_id, spec, task, config, policy, campaign)

    def resume(self, run_id: str, model, *, task: TaskSpec | None = None) -> RunResult:
        """Continue a run. Completed episodes and checks are reused, never repeated."""
        with Ledger.open(self.ledger_root) as ledger:
            run = self._load(ledger, run_id)
        if task is not None and task.record() != run.task.record():
            raise ValueError("task differs from the one this run recorded")
        with Ledger.open(self.ledger_root) as ledger:
            recorded = (
                json.loads(ledger.read_artifact(run.spec.artifacts[REVISED]))
                if REVISED in run.spec.artifacts
                else []
            )
        current = [r.id for r in self.revisions(run.task.id)]
        if current != recorded:
            raise ValueError(
                f"task {run.task.id} was revised after this run started "
                f"({', '.join(current[len(recorded) :]) or 'revisions changed'}); "
                "start a new run"
            )
        if getattr(model, "model", None) != run.config.model:
            raise ValueError("model boundary differs from the run configuration")
        if self._model_record(model) != {
            name: data
            for name, data in self._recorded_model(run).items()
            if data is not None
        }:
            raise ValueError("model adapter configuration differs from the run")
        with Ledger.open(self.ledger_root) as ledger:
            self._require_token_units(model, ledger.project.manifest.allowances)
        submissions: list[Submission] = []
        for index in range(1, run.task.submissions + 1):
            episode = self._episode(run, index)
            try:
                with self.environment(self.ledger_root, episode) as environment:
                    result = run_workflow(
                        self.ledger_root,
                        self.root / "graph.sqlite3",
                        episode,
                        model=model,
                        environment=environment,
                        reconcile=self._reconcile(),
                        requests=self._requests(run, index),
                    )
            except UnknownOutcome as error:
                return RunResult(
                    run_id, RunOutcome.UNKNOWN, tuple(submissions), str(error)
                )
            except BudgetExceeded as error:
                return self._exhausted(run_id, submissions, error)
            except RuntimeError as error:
                failed = self._failed_attempt(episode.episode_id)
                if failed is None:
                    raise
                if failed is Outcome.INFRASTRUCTURE_FAILURE:
                    return RunResult(
                        run_id,
                        RunOutcome.INFRASTRUCTURE_FAILURE,
                        tuple(submissions),
                        str(error),
                    )
                result = {"exit_status": str(error)}
            if result.get("exit_status") != "Submitted":
                outcome = RunOutcome.REJECTED if submissions else RunOutcome.INCOMPLETE
                return RunResult(
                    run_id, outcome, tuple(submissions), str(result.get("exit_status"))
                )
            refused = self._guard(run, index, submissions)
            if refused is not None:
                # Refused before any check; it still uses a submission.
                submissions.append(refused)
                continue
            try:
                submission = self._assess(run, index, episode.episode_id)
            except BudgetExceeded as error:
                return self._exhausted(run_id, submissions, error)
            submissions.append(submission)
            outcome = {
                Status.ACCEPTED: RunOutcome.ACCEPTED,
                Status.UNKNOWN: RunOutcome.UNKNOWN,
                Status.UNSUPPORTED: RunOutcome.UNSUPPORTED,
                Status.INFRASTRUCTURE_FAILURE: RunOutcome.INFRASTRUCTURE_FAILURE,
            }.get(Status(submission.decision))
            if outcome is not None:
                return RunResult(run_id, outcome, tuple(submissions))
        return RunResult(run_id, RunOutcome.REJECTED, tuple(submissions))

    @staticmethod
    def _model_record(model) -> dict[str, bytes]:
        """A real adapter's pinned configuration and service identity, if any."""
        snapshot = getattr(model, "snapshot", None)
        if snapshot is None:
            return {}
        return {
            MODEL_PIN: snapshot.data,
            "model-service.json": _json({"service": model.service_id}),
        }

    def _recorded_model(self, run: _Run) -> dict[str, bytes | None]:
        with Ledger.open(self.ledger_root) as ledger:
            return {
                name: ledger.read_artifact(run.spec.artifacts[name])
                if name in run.spec.artifacts
                else None
                for name in (MODEL_PIN, "model-service.json")
            }

    def _model_service(self, run: _Run) -> str:
        recorded = self._recorded_model(run)["model-service.json"]
        if recorded is None:
            return run.config.model
        return json.loads(recorded)["service"]

    def _requests(self, run: _Run, index: int) -> Requests | None:
        operations = _operations(self.domain)
        if not operations:
            return None
        inputs, private = run.files(index)
        return Requests(operations, inputs | private, self.jobs)

    def _reconcile(self):
        """Settle unknown operations without executing them again.

        Each operation is settled from the evidence it cited when requested, so a
        shared operation from another run is never given this run's files.
        """
        operations = _operations(self.domain)

        def reconcile(request):
            with Ledger.open(self.ledger_root) as ledger:
                return reconcile_operation(ledger, operations, request, self.jobs)

        return reconcile

    @staticmethod
    def _require_token_units(model, allowances: Mapping[str, int]) -> None:
        """A project that allows token units enforces them on every model call."""
        enforced = TOKEN_UNITS & allowances.keys()
        declared = set(getattr(model, "reserved_units", {"model"}))
        if not enforced <= declared:
            raise ValueError(
                "model service does not reserve the project's token units: "
                f"{sorted(enforced - declared)}"
            )

    @staticmethod
    def _exhausted(run_id, submissions, error) -> RunResult:
        """A run or project budget ended the run before this step could start."""
        outcome = RunOutcome.REJECTED if submissions else RunOutcome.INCOMPLETE
        return RunResult(
            run_id, outcome, tuple(submissions), f"budget exhausted: {error}"
        )

    def _failed_attempt(self, episode_id: str) -> Outcome | None:
        """The recorded outcome that stopped an episode, if a worker attempt did.

        A model or tool attempt that completed as infrastructure failure, or a
        model attempt that completed as failed, ends the episode. Anything else
        is a host error and is re-raised by the caller.
        """
        prefix = f"episode/{episode_id}/"
        found = None
        with Ledger.open(self.ledger_root) as ledger:
            for operation in ledger.operations():
                origin = operation.request.origin
                if (
                    not origin.operation_id.startswith(prefix)
                    or not operation.completion
                ):
                    continue
                outcome = operation.completion.result.outcome
                if outcome is Outcome.INFRASTRUCTURE_FAILURE:
                    return outcome
                if outcome is Outcome.FAILED and origin.kind == "model":
                    found = outcome
        return found

    def _episode(self, run: _Run, index: int) -> Episode:
        files = run.files(index)[0]
        channel = _memory_channel(run.task.version(index))
        if channel in run.spec.artifacts:
            files[MEMORY] = run.evidence(channel)
        workspace = None
        objective = run.task.contract(index).objective
        for scheduled in run.task.revisions[: run.task.version(index)]:
            revision = scheduled.revision
            objective += (
                f"\n\nThe task contract was revised after submission "
                f"{scheduled.after_submission} (revision {revision.id}, by "
                f"{revision.owner}): {revision.reason}"
            )
        if index > 1:
            previous = self._episode_id(run, index - 1)
            with Ledger.open(self.ledger_root) as ledger:
                for earlier in range(1, index):
                    feedback = self._feedback(ledger, run, earlier)
                    files[f"feedback-{earlier:03d}.json"] = feedback
                workspace = submitted_files(ledger, previous).get("workspace.json")
            objective += (
                f"\n\nEarlier submissions were not accepted. Their feedback is in "
                f"feedback-001.json to feedback-{index - 1:03d}.json."
            )
        return Episode(
            self._episode_id(run, index),
            objective,
            (),
            model=run.config.model,
            model_service=self._model_service(run),
            model_pin=run.model_pin,
            environment=self.environment_id,
            max_steps=run.config.max_steps,
            continues=self._episode_id(run, index - 1) if index > 1 else None,
            scope=run.scope,
            files=files,
            workspace=workspace,
        )

    @staticmethod
    def _episode_id(run: _Run, index: int) -> str:
        return f"{run.run_id}-s{index:03d}"

    def _feedback(self, ledger: Ledger, run: _Run, index: int) -> Evidence:
        """Worker-visible feedback for one assessed submission; no host-only data."""
        guard = _record(ledger, f"run/{run.run_id}/submission/{index}/guard")
        refused = guard and json.loads(
            ledger.read_artifact(guard.artifacts["guard.json"])
        )
        if refused and refused["refused"]:
            capture = record_once(
                ledger,
                ledger.start_session(),
                Origin(
                    f"run/{run.run_id}/submission/{index}/feedback",
                    "feedback",
                    PRODUCER,
                    "1",
                    {},
                ),
                {
                    "feedback.json": _json(
                        {"submission": index, "duplicate": refused["reason"]}
                    )
                },
            )
            return Evidence.captured(capture, "feedback.json")
        checks = {}
        for name in run.task.contract(index).checks:
            operation = self._check_operation(ledger, run, index, name)
            verdict = json.loads(
                ledger.read_artifact(
                    operation.completion.observation.artifacts["verdict.json"]
                )
            )
            checks[name] = verdict
        capture = record_once(
            ledger,
            ledger.start_session(),
            Origin(
                f"run/{run.run_id}/submission/{index}/feedback",
                "feedback",
                PRODUCER,
                "1",
                {},
            ),
            {"feedback.json": _json({"submission": index, "checks": checks})},
        )
        return Evidence.captured(capture, "feedback.json")

    def _target(self, ledger: Ledger, run: _Run, index: int) -> Evidence:
        files = submitted_files(ledger, self._episode_id(run, index))
        capture = record_once(
            ledger,
            ledger.start_session(),
            Origin(
                f"run/{run.run_id}/submission/{index}",
                "submission",
                PRODUCER,
                "1",
                {ref.name: ref.artifact for ref in files.values()},
            ),
            {
                "submission.json": _json(
                    {name: ref.artifact.digest for name, ref in sorted(files.items())}
                )
            },
        )
        return Evidence.captured(capture, "submission.json")

    def _check_request(
        self, ledger: Ledger, run: _Run, index: int, name: str, target: Evidence
    ) -> Request:
        inputs = {target.name: target.artifact}
        visible, private = run.files(index)
        for ref in (*visible.values(), *private.values()):
            inputs[ref.name] = ref.artifact
        return Request(
            Origin(
                f"run/{run.run_id}/submission/{index}/check/{name}",
                "check",
                f"{self.domain.name}/{name}",
                self.domain.checkers[name].version,
                inputs,
            ),
            ledger.project,
        )

    def _check_operation(self, ledger: Ledger, run: _Run, index: int, name: str):
        target = self._target(ledger, run, index)
        return ledger.lookup(self._check_request(ledger, run, index, name, target))

    def _run_check(
        self, ledger: Ledger, session: str, run: _Run, index: int, name: str
    ) -> str:
        target = self._target(ledger, run, index)
        request = self._check_request(ledger, run, index, name, target)
        scope = run.scope if _isolated(self.domain.checkers[name]) else ROOT_SCOPE
        operation = ledger.reserve(session, request, {"check": 1}, scope)
        if operation.completion is None:
            if not ledger.begin(session, request):
                raise UnknownOutcome(f"unknown outcome: {request.origin.operation_id}")
            files = submitted_files(ledger, self._episode_id(run, index))
            context = CheckContext(
                {
                    n: ledger.read_artifact(ref.artifact)
                    for n, ref in files.items()
                    if n not in _WORKER_NOTES
                },
                run.task.contract(index).inputs,
                run.task.contract(index).private,
                self.jobs,
            )
            started = perf_counter_ns()
            try:
                verdict = self.domain.checkers[name].check(context)
            except Exception as error:  # a checker crash is the host's failure
                verdict = Verdict(
                    VerdictStatus.INFRASTRUCTURE_FAILURE,
                    host_only={"error": repr(error)},
                )
            shown = len(_json(verdict.feedback))
            if shown > FEEDBACK_LIMIT:
                # Truncation could mislead the worker; oversized feedback is a
                # checker fault, kept whole as host-only evidence.
                verdict = Verdict(
                    VerdictStatus.INFRASTRUCTURE_FAILURE,
                    host_only={
                        "error": f"feedback is {shown} bytes; limit {FEEDBACK_LIMIT}",
                        "status": verdict.status,
                        "feedback": verdict.feedback,
                        "host_only": verdict.host_only,
                    },
                )
            facts = len(_json(list(verdict.facts)))
            if len(verdict.facts) > FACTS_COUNT or facts > FACTS_LIMIT:
                # Facts reach later workers; too many is a checker fault, kept whole.
                verdict = Verdict(
                    VerdictStatus.INFRASTRUCTURE_FAILURE,
                    host_only={
                        "error": f"{len(verdict.facts)} facts in {facts} bytes; "
                        f"limit {FACTS_COUNT} facts in {FACTS_LIMIT} bytes",
                        "status": verdict.status,
                        "feedback": verdict.feedback,
                        "host_only": verdict.host_only,
                        "facts": list(verdict.facts),
                    },
                )
            outcome, code = {
                VerdictStatus.PASSED: (Outcome.SUCCEEDED, 0),
                VerdictStatus.REJECTED: (Outcome.SUCCEEDED, 0),
                VerdictStatus.UNPROVED: (Outcome.SUCCEEDED, 0),
                VerdictStatus.UNSUPPORTED: (Outcome.FAILED, 1),
                VerdictStatus.INFRASTRUCTURE_FAILURE: (
                    Outcome.INFRASTRUCTURE_FAILURE,
                    None,
                ),
            }[verdict.status]
            ledger.complete(
                session,
                request,
                Result(outcome, code, {"check": 1}, perf_counter_ns() - started),
                {
                    "result.json": _json(
                        {"passed": verdict.status is VerdictStatus.PASSED}
                    ),
                    "verdict.json": _json(
                        {"status": verdict.status, "feedback": verdict.feedback}
                    ),
                    "host-only.json": _json(verdict.host_only),
                    "seeds.json": _json(context.seeds),
                    "jobs.json": _json(context.job_log),
                }
                | ({"facts.json": _json(list(verdict.facts))} if verdict.facts else {})
                | context.job_output,
            )
        return request.origin.operation_id

    def _guard(
        self, run: _Run, index: int, earlier: list[Submission]
    ) -> Submission | None:
        """Refuse a near-repeat of earlier rejected candidates, before any check.

        The decision is recorded, so a resumed run makes the same one.
        """
        guard = run.task.duplicate_guard
        if guard is None:
            return None
        normalize = getattr(self.domain, "normalize", default_normalize)
        with Ledger.open(self.ledger_root) as ledger:
            operation_id = f"run/{run.run_id}/submission/{index}/guard"
            record = _record(ledger, operation_id)
            if record is None:

                def files(i):
                    # Notes are not the candidate: editing one cannot dodge the guard.
                    return {
                        name: ref
                        for name, ref in submitted_files(
                            ledger, self._episode_id(run, i)
                        ).items()
                        if name not in _WORKER_NOTES
                    }

                def read(refs):
                    return {
                        name: ledger.read_artifact(ref.artifact)
                        for name, ref in refs.items()
                    }

                # Only candidates rejected under the contract now in force count:
                # resubmitting after a revision is a new question, not a repeat.
                version = run.task.version(index)
                compared = [
                    files(s.index)
                    for s in earlier
                    if s.decision == str(Status.REJECTED)
                    and run.task.version(s.index) == version
                ][-guard.window :]
                candidate = files(index)
                verdict = guard.verdict(
                    [normalize(read(refs)) for refs in compared],
                    normalize(read(candidate)),
                )
                # Cite the exact captures the decision was derived from.
                cited = {
                    ref.name: ref.artifact
                    for refs in (*compared, candidate)
                    for ref in refs.values()
                }
                reason = verdict and {
                    **verdict,
                    "message": (
                        f"{verdict['band']} repeat of {verdict['matches']} earlier "
                        f"rejected submission(s) ({verdict['edit']} bytes changed "
                        "after normalisation); change approach"
                    ),
                }
                record = record_once(
                    ledger,
                    ledger.start_session(),
                    Origin(operation_id, "duplicate-guard", PRODUCER, "1", cited),
                    {
                        "guard.json": _json(
                            {"refused": verdict is not None, "reason": reason}
                        )
                    },
                )
            decision = json.loads(ledger.read_artifact(record.artifacts["guard.json"]))
        return Submission(index, {}, DUPLICATE) if decision["refused"] else None

    def _assess(self, run: _Run, index: int, episode_id: str) -> Submission:
        with Ledger.open(self.ledger_root) as ledger:
            session = ledger.start_session()
            target = self._target(ledger, run, index)
            try:
                checks = run.task.contract(index).checks
                receipts = {
                    name: self._run_check(ledger, session, run, index, name)
                    for name in checks
                }
            except UnknownOutcome:
                return Submission(index, {}, Status.UNKNOWN)

            def resolve(candidate: Evidence) -> AcceptanceContext:
                if candidate != target:
                    raise ValueError("acceptance target is not this submission")
                return AcceptanceContext(
                    self._policy(ledger, session, run, index),
                    run.contract_evidence(index),
                    {
                        name: self._check_request(ledger, run, index, name, target)
                        for name in checks
                    },
                )

            decision = Acceptance(ledger, session, resolve, run.scope).accept(
                target, receipts
            )
            verdicts = {}
            for name in checks:
                operation = self._check_operation(ledger, run, index, name)
                verdicts[name] = VerdictStatus(
                    json.loads(
                        ledger.read_artifact(
                            operation.completion.observation.artifacts["verdict.json"]
                        )
                    )["status"]
                )
            return Submission(index, verdicts, str(decision.status))


def _record(ledger: Ledger, operation_id: str):
    matches = [o for o in ledger.history() if o.origin.operation_id == operation_id]
    return matches[0] if matches else None
