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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Protocol
from uuid import uuid4

from warranted.acceptance import Acceptance, AcceptanceContext, Evidence, Status
from warranted.ledger import Ledger, Manifest, Origin, Outcome, Request, Result
from warranted.sandbox import SANDBOX_ID, Sandbox
from warranted.worker import (
    Episode,
    UnknownOutcome,
    record_once,
    run_workflow,
    submitted_files,
)

PRODUCER = "warranted-tasks"
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,100}")
_RESERVED = {"context.json", "result.json", "workspace"}


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


@dataclass(frozen=True)
class CheckContext:
    candidate: Mapping[str, bytes]
    inputs: Mapping[str, bytes]
    private: Mapping[str, bytes]


class Checker(Protocol):
    version: str

    def check(self, ctx: CheckContext) -> Verdict: ...


class Domain(Protocol):
    name: str
    version: str
    worker_image: str
    checkers: Mapping[str, Checker]


def domain_identity(domain: Domain) -> dict[str, str]:
    """Name, version, image, and a digest of the domain's and checkers' source."""
    sources = []
    for item in (domain, *domain.checkers.values()):
        path = inspect.getsourcefile(type(item))
        if path is None:
            raise ValueError("domain and checker classes need source files")
        sources.append(Path(path).read_bytes())
    identity = {
        "domain": domain.name,
        "domain_version": domain.version,
        "worker_image": domain.worker_image,
        "domain_source": _digest(b"".join(sorted(set(sources)))),
    }
    for name, checker in sorted(domain.checkers.items()):
        identity[f"checker/{name}"] = checker.version
    return identity


@dataclass(frozen=True)
class TaskSpec:
    """What to solve. Loaded from TOML; inputs and private files are pinned bytes."""

    id: str
    objective: str
    inputs: Mapping[str, bytes]
    private: Mapping[str, bytes]
    checks: tuple[str, ...]
    submissions: int

    @classmethod
    def load(cls, path: Path) -> TaskSpec:
        path = Path(path)
        data = tomllib.loads(path.read_text())
        known = {"id", "objective", "inputs", "private", "checks", "budgets"}
        if not data.keys() <= known or not {"id", "objective", "checks"} <= set(data):
            raise ValueError("task needs id, objective, and checks, and nothing else")
        budgets = data.get("budgets", {})
        if not budgets.keys() <= {"submissions"}:
            raise ValueError("unknown task budget")

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

    def __post_init__(self):
        if type(self.model) is not str or not self.model.strip():
            raise ValueError("run configuration needs a model identity")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 100:
            raise ValueError("max_steps must be an integer from 1 to 100")

    def record(self) -> bytes:
        return _json({"model": self.model, "max_steps": self.max_steps})


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
    ):
        self.root, self.domain = Path(root), domain
        self.environment, self.environment_id = environment, environment_id
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
        return domain_identity(self.domain) | {
            "worker_environment": self.environment_id
        }

    @classmethod
    def create(
        cls,
        root: Path,
        domain: Domain,
        allowances: Mapping[str, int],
        **options,
    ) -> Project:
        root = Path(root)
        root.mkdir(parents=True)
        environment_id = options.get("environment_id", SANDBOX_ID)
        identity = domain_identity(domain) | {"worker_environment": environment_id}
        with Ledger.create(
            root / "ledger",
            Manifest(
                "warranted-project",
                "1",
                str(uuid4()),
                str(uuid4()),
                identity,
                {"model": 0, "tool": 0, "check": 0} | dict(allowances),
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
        with Ledger.open(self.ledger_root) as ledger:
            session = ledger.start_session()
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
                    )
            except UnknownOutcome as error:
                return RunResult(
                    run_id, RunOutcome.UNKNOWN, tuple(submissions), str(error)
                )
            if result.get("exit_status") != "Submitted":
                outcome = RunOutcome.REJECTED if submissions else RunOutcome.INCOMPLETE
                return RunResult(
                    run_id, outcome, tuple(submissions), str(result.get("exit_status"))
                )
            submission = self._assess(run, index, episode.episode_id)
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
        operation = ledger.reserve(session, request, {"check": 1})
        if operation.completion is None:
            if not ledger.begin(session, request):
                raise UnknownOutcome(f"unknown outcome: {request.origin.operation_id}")
            files = submitted_files(ledger, self._episode_id(run, index))
            context = CheckContext(
                {n: ledger.read_artifact(ref.artifact) for n, ref in files.items()},
                dict(run.task.inputs),
                dict(run.task.private),
            )
            started = perf_counter_ns()
            try:
                verdict = self.domain.checkers[name].check(context)
            except Exception as error:  # a checker crash is the host's failure
                verdict = Verdict(
                    VerdictStatus.INFRASTRUCTURE_FAILURE,
                    host_only={"error": repr(error)},
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
                },
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

            decision = Acceptance(ledger, session, resolve).accept(target, receipts)
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
