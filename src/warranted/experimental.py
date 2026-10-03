"""Prototype task layer for M7. No stability promise.

This module tests the shape proposed in docs/proposals/m7-harness-api.md against the
existing ledger, worker, and acceptance boundaries. Names and behavior may change
or disappear without notice. It covers one slice: TOML tasks, a domain's checkers,
explicit run IDs, one episode per submission, worker-visible feedback, and the six
run outcomes. Memory, revisions, the duplicate guard, and campaigns are absent.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import version as package_version
from pathlib import Path
from time import perf_counter_ns
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from warranted.acceptance import Acceptance, AcceptanceContext, Evidence, Status
from warranted.jobs import JobContext, JobRunner, PodmanJobs
from warranted.ledger import (
    ROOT_SCOPE,
    BudgetExceeded,
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
)
from warranted.operations import (
    Operation,
    Requests,
    reconcile_operation,
    validate_operations,
)
from warranted.sandbox import SANDBOX_ID, Sandbox
from warranted.worker import (
    TOKEN_UNITS,
    Episode,
    UnknownOutcome,
    record_once,
    run_workflow,
    submitted_files,
)

PRODUCER = "warranted-tasks"
# Feedback reaches the worker's context; a checker that writes more is faulty.
FEEDBACK_LIMIT = 64 * 1024
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,100}")
_RESERVED = {"context.json", "result.json", "workspace", "responses"}


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class VerdictStatus(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"
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
    """A checker's result. Only `feedback` is ever shown to the worker."""

    status: VerdictStatus
    feedback: Any = None
    host_only: Any = None

    def __post_init__(self):
        object.__setattr__(self, "status", VerdictStatus(self.status))
        _json(self.feedback), _json(self.host_only)  # must be JSON-serialisable


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
    runner = getattr(jobs, "identity", None)
    if type(runner) is not str or not runner:
        raise ValueError("job runner needs a stable identity string")
    return domain_identity(domain) | {
        "worker_environment": environment_id,
        "job_runner": runner,
    }


@dataclass(frozen=True)
class TaskSpec:
    """What to solve. Loaded from TOML; inputs and private files are pinned bytes."""

    id: str
    objective: str
    inputs: Mapping[str, bytes]
    private: Mapping[str, bytes]
    checks: tuple[str, ...]
    submissions: int
    check_budget: int | None = None

    @classmethod
    def load(cls, path: Path) -> TaskSpec:
        path = Path(path)
        data = tomllib.loads(path.read_text())
        known = {"id", "objective", "inputs", "private", "checks", "budgets"}
        if not data.keys() <= known or not {"id", "objective", "checks"} <= set(data):
            raise ValueError("task needs id, objective, and checks, and nothing else")
        budgets = data.get("budgets", {})
        if not budgets.keys() <= {"submissions", "checks"}:
            raise ValueError(
                "unknown task budget; model, tool, and token budgets belong to runs"
            )

        def files(table: Mapping[str, str]) -> dict[str, bytes]:
            return {
                name: (path.parent / relative).read_bytes()
                for name, relative in table.items()
            }

        return cls(
            data["id"],
            data["objective"],
            files(data.get("inputs", {})),
            files(data.get("private", {})),
            tuple(data["checks"]),
            budgets.get("submissions", 1),
            budgets.get("checks"),
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
                }
            )
        }
        raw |= {f"input/{name}": data for name, data in self.inputs.items()}
        raw |= {f"private/{name}": data for name, data in self.private.items()}
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


@dataclass
class _Run:
    run_id: str
    spec: Any  # Observation
    task: TaskSpec
    config: RunConfig
    policy: Evidence

    @property
    def scope(self) -> str:
        return f"run/{self.run_id}"

    def evidence(self, channel: str) -> Evidence:
        return Evidence.captured(self.spec, channel)


class Project:
    """One ledger and its single writer. Runs are serialised."""

    def __init__(
        self,
        root: Path,
        domain: Domain,
        *,
        environment: Callable = Sandbox,
        environment_id: str = SANDBOX_ID,
        jobs: JobRunner | None = None,
    ):
        self.root, self.domain = Path(root), domain
        self.environment, self.environment_id = environment, environment_id
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
            options.get("environment_id", SANDBOX_ID),
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

    def start(self, task: TaskSpec, config: RunConfig, model) -> RunResult:
        """Create a new run with a fresh ID, then run it."""
        unknown = set(task.checks) - set(self.domain.checkers)
        if unknown:
            raise ValueError(f"task names unknown checks: {sorted(unknown)}")
        run_id = "run-" + uuid4().hex[:12]
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
            shared = [n for n in task.checks if not _isolated(self.domain.checkers[n])]
            if shared:
                # A shared-state check runs in the root scope, where a run's cap
                # cannot apply; refuse rather than silently ignore the budget.
                raise ValueError(
                    f"check budgets require isolated checkers; not isolated: {shared}"
                )
            caps["check"] = task.check_budget
        with Ledger.open(self.ledger_root) as ledger:
            session = ledger.start_session()
            # Caps are recorded before any run record, so a resume cannot change them.
            ledger.open_scope(session, f"run/{run_id}", caps)
            record_once(
                ledger,
                session,
                Origin(f"run/{run_id}/spec", "run-spec", PRODUCER, "1", {}),
                task.record() | {"config.json": config.record()},
            )
            policy = {
                "version": 1,
                "owner": "task-owner",
                "transition": "accept-candidate",
                "requirements": {
                    name: {"kind": "gate", "check": name, "field": "passed"}
                    for name in task.checks
                },
            }
            record_once(
                ledger,
                session,
                Origin(f"run/{run_id}/policy", "run-policy", PRODUCER, "1", {}),
                {"policy.json": _json(policy)},
            )
        return self.resume(run_id, model)

    def runs(self) -> tuple[str, ...]:
        with Ledger.open(self.ledger_root) as ledger:
            return tuple(
                o.origin.operation_id.split("/")[1]
                for o in ledger.history()
                if o.origin.kind == "run-spec"
            )

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
        )
        config = RunConfig(**json.loads(read["config.json"]))
        policy = Evidence.captured(found["run-policy"], "policy.json")
        return _Run(run_id, spec, task, config, policy)

    def resume(self, run_id: str, model, *, task: TaskSpec | None = None) -> RunResult:
        """Continue a run. Completed episodes and checks are reused, never repeated."""
        with Ledger.open(self.ledger_root) as ledger:
            run = self._load(ledger, run_id)
        if task is not None and task.record() != run.task.record():
            raise ValueError("task differs from the one this run recorded")
        if getattr(model, "model", None) != run.config.model:
            raise ValueError("model boundary differs from the run configuration")
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
                        requests=self._requests(run),
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

    def _task_files(self, run: _Run) -> dict[str, Evidence]:
        return {n: run.evidence(f"input/{n}") for n in run.task.inputs} | {
            n: run.evidence(f"private/{n}") for n in run.task.private
        }

    def _requests(self, run: _Run) -> Requests | None:
        operations = _operations(self.domain)
        if not operations:
            return None
        return Requests(operations, self._task_files(run), self.jobs)

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
        files = {name: run.evidence(f"input/{name}") for name in run.task.inputs}
        workspace = None
        objective = run.task.objective
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
            model_service=run.config.model,
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
        checks = {}
        for name in run.task.checks:
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
        channels = [f"input/{name}" for name in run.task.inputs]
        channels += [f"private/{name}" for name in run.task.private]
        for channel in channels:
            ref = run.evidence(channel)
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
                {n: ledger.read_artifact(ref.artifact) for n, ref in files.items()},
                run.task.inputs,
                run.task.private,
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
            outcome, code = {
                VerdictStatus.PASSED: (Outcome.SUCCEEDED, 0),
                VerdictStatus.REJECTED: (Outcome.SUCCEEDED, 0),
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
                | context.job_output,
            )
        return request.origin.operation_id

    def _assess(self, run: _Run, index: int, episode_id: str) -> Submission:
        with Ledger.open(self.ledger_root) as ledger:
            session = ledger.start_session()
            target = self._target(ledger, run, index)
            try:
                receipts = {
                    name: self._run_check(ledger, session, run, index, name)
                    for name in run.task.checks
                }
            except UnknownOutcome:
                return Submission(index, {}, Status.UNKNOWN)

            def resolve(candidate: Evidence) -> AcceptanceContext:
                if candidate != target:
                    raise ValueError("acceptance target is not this submission")
                return AcceptanceContext(
                    run.policy,
                    run.evidence("task.json"),
                    {
                        name: self._check_request(ledger, run, index, name, target)
                        for name in run.task.checks
                    },
                )

            decision = Acceptance(ledger, session, resolve, run.scope).accept(
                target, receipts
            )
            verdicts = {}
            for name in run.task.checks:
                operation = self._check_operation(ledger, run, index, name)
                verdicts[name] = VerdictStatus(
                    json.loads(
                        ledger.read_artifact(
                            operation.completion.observation.artifacts["verdict.json"]
                        )
                    )["status"]
                )
            return Submission(index, verdicts, str(decision.status))
