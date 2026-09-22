"""Provisional mini-swe-agent adapters and a serial LangGraph lifecycle.

Callbacks are trusted host boundaries: each performs at most one external attempt.
They must never execute generated shell on the host. Raw results enter the ledger
before parsing or mini's submission control flow. No provider SDK is enabled here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import FormatError, Submitted

from warranted.acceptance import Evidence, _encode
from warranted.ledger import (
    Completion,
    Ledger,
    Observation,
    OperationConflict,
    Origin,
    Outcome,
    Request,
    Result,
    _json_object,
)


class UnknownOutcome(RuntimeError):
    """An exact completion is missing. Automatic retry is forbidden."""


@dataclass(frozen=True)
class AttemptResult:
    result: Result
    raw: Mapping[str, bytes]


Boundary = Callable[[Request, bytes], AttemptResult]


@dataclass(frozen=True)
class Episode:
    episode_id: str
    objective: str
    inputs: tuple[str, ...]
    model: str = "scripted-v1"
    environment: str = "scripted-v1"
    max_steps: int = 4
    model_reservation: int = 1
    tool_reservation: int = 1
    continues: str | None = None
    model_service: str | None = None
    files: Mapping[str, Evidence] = field(default_factory=dict)

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.episode_id):
            raise ValueError("invalid episode ID")
        if not all(
            type(s) is str and s.strip()
            for s in (self.objective, self.model, self.environment)
        ):
            raise ValueError("episode requires objective and boundary versions")
        if type(self.inputs) is not tuple or len(set(self.inputs)) != len(self.inputs):
            raise ValueError("episode inputs must be distinct snapshot names")
        if not isinstance(self.files, Mapping) or any(
            type(name) is not str or not name or type(ref) is not Evidence
            for name, ref in self.files.items()
        ):
            raise ValueError("episode files require filenames and captured evidence")
        if set(self.files) & set(self.inputs):
            raise ValueError("episode filename repeats a snapshot input")
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 100:
            raise ValueError("episode step limit must be between 1 and 100")
        if self.continues is not None and (
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.continues)
            or self.continues == self.episode_id
        ):
            raise ValueError("continuation must name a different episode")
        if self.model_service is not None and (
            type(self.model_service) is not str or not self.model_service.strip()
        ):
            raise ValueError("model service identity must be nonempty")
        if any(
            type(n) is not int or n < 1
            for n in (self.model_reservation, self.tool_reservation)
        ):
            raise ValueError("attempt reservations must be positive integers")


def record_once(
    ledger: Ledger, session: str, origin: Origin, raw: dict[str, bytes]
) -> Observation:
    """Bind a host-authored record to one stable identity, including its bytes."""
    ledger.lookup(Request(origin, ledger.project))
    # ponytail: scan this small local journal; index named captures if it grows.
    matches = [
        o for o in ledger.history() if o.origin.operation_id == origin.operation_id
    ]
    if matches:
        if (
            len(matches) != 1
            or matches[0].origin != origin
            or {
                name: ledger.read_artifact(ref)
                for name, ref in matches[0].artifacts.items()
            }
            != raw
        ):
            raise OperationConflict("record ID already names different evidence")
        return matches[0]
    return ledger.record(session, origin, raw)


def unresolved(ledger: Ledger) -> None:
    for op in ledger.operations():
        if op.state == "unknown":
            raise UnknownOutcome(f"unknown outcome: {op.request.origin.operation_id}")


def input_files(ledger: Ledger, episode: Episode) -> dict[str, bytes]:
    """Resolve only the host's explicit files, with exact evidence provenance."""
    refs = {
        name: Evidence(name, ledger.project.snapshots[name].artifact)
        for name in episode.inputs
    } | dict(episode.files)
    inputs = {}
    for ref in refs.values():
        if ref.name in inputs and inputs[ref.name] != ref.artifact:
            raise ValueError("conflicting context evidence")
        inputs[ref.name] = ref.artifact
    ledger.lookup(
        Request(
            Origin(
                "context-validation",
                "context",
                "warranted-worker",
                "1",
                inputs,
            ),
            ledger.project,
        )
    )
    return {name: ledger.read_artifact(ref.artifact) for name, ref in refs.items()}


def submitted_candidate(ledger: Ledger, episode_id: str) -> Evidence:
    """Find the single captured submission, never a worker-supplied host path."""
    matches = [
        op
        for op in ledger.operations()
        if op.request.origin.operation_id.startswith(f"episode/{episode_id}/tool/")
        and op.completion
        and "candidate/result.json" in op.completion.observation.artifacts
    ]
    if len(matches) != 1:
        raise ValueError("submission lacks one exact captured candidate")
    operation = ledger.lookup(matches[0].request)
    return Evidence.captured(operation.completion.observation, "candidate/result.json")


class Journal:
    """One host writer, stable attempt slots, and exact raw completions."""

    def __init__(self, ledger: Ledger, episode: Episode):
        self.ledger, self.episode = ledger, episode
        self.session = ledger.start_session()
        self.prefix = f"episode/{episode.episode_id}"
        inputs = {
            name: ledger.project.snapshots[name].artifact for name in episode.inputs
        }
        for ref in episode.files.values():
            if ref.name in inputs and inputs[ref.name] != ref.artifact:
                raise ValueError("conflicting context evidence")
            inputs[ref.name] = ref.artifact
        if episode.continues is not None:
            parents = [
                o
                for o in ledger.history()
                if o.origin.operation_id == f"episode/{episode.continues}"
                and (o.origin.kind, o.origin.producer, o.origin.producer_version)
                == ("episode", "warranted-worker", "1")
            ]
            if len(parents) != 1:
                raise ValueError("continuation has no unique recorded parent episode")
            parent = Evidence.captured(parents[0], "episode.json")
            inputs[parent.name] = parent.artifact
        self.spec = record_once(
            ledger,
            self.session,
            Origin(self.prefix, "episode", "warranted-worker", "1", inputs),
            {
                "episode.json": _encode(
                    {
                        "episode": episode,
                        "packages": {
                            name: version(name)
                            for name in (
                                "mini-swe-agent",
                                "langgraph",
                                "langgraph-checkpoint-sqlite",
                            )
                        },
                        "policy": "serial-v1",
                    }
                )
            },
        )
        self.inputs = {
            Evidence.captured(self.spec, "episode.json").name: self.spec.artifacts[
                "episode.json"
            ]
        }

    def call(
        self, kind: str, index: int, payload: dict, boundary: Boundary
    ) -> Completion:
        operation_id = f"{self.prefix}/{kind}/{index}"
        encoded = _encode(payload)
        capture = record_once(
            self.ledger,
            self.session,
            Origin(
                operation_id + "/request",
                "attempt-input",
                "warranted-worker",
                "1",
                self.inputs,
            ),
            {"request.json": encoded},
        )
        evidence = Evidence.captured(capture, "request.json")
        request = Request(
            Origin(
                operation_id,
                kind,
                (self.episode.model_service or "warranted-worker")
                if kind == "model"
                else "warranted-worker",
                "1",
                {**self.inputs, evidence.name: evidence.artifact},
            ),
            self.ledger.project,
        )
        reservation = getattr(self.episode, kind + "_reservation")
        op = self.ledger.reserve(self.session, request, {kind: reservation})
        if op.completion is not None:
            return op.completion
        unresolved(self.ledger)
        if not self.ledger.begin(self.session, request):
            raise UnknownOutcome(f"unknown outcome: {operation_id}")
        # Exceptions leave the dispatch unknown and its reservation intact.
        response = boundary(request, encoded)
        return self.ledger.complete(
            self.session, request, response.result, response.raw
        )

    def raw(self, completion: Completion, channel: str) -> bytes:
        return self.ledger.read_artifact(completion.observation.artifacts[channel])

    def completion_inputs(self) -> dict:
        inputs = dict(self.inputs)
        for operation in self.ledger.operations():
            if operation.request.origin.operation_id.startswith(self.prefix + "/"):
                self.ledger.lookup(operation.request)
                if operation.completion is None:
                    raise UnknownOutcome("episode has an unfinished attempt")
                observation = operation.completion.observation
                for channel in observation.artifacts:
                    evidence = Evidence.captured(observation, channel)
                    inputs[evidence.name] = evidence.artifact
        return inputs

    def receipt(self) -> Observation | None:
        receipt = next(
            (
                o
                for o in self.ledger.history()
                if o.origin.operation_id == self.prefix + "/finished"
            ),
            None,
        )
        if receipt is not None:
            if receipt.origin.inputs != self.completion_inputs():
                raise UnknownOutcome("episode receipt has different attempt evidence")
            self.ledger.lookup(Request(receipt.origin, self.ledger.project))
            for ref in receipt.artifacts.values():
                self.ledger.read_artifact(ref)
        return receipt


class WorkerModel:
    """Mini Model protocol; one recorded raw response per query, no retry loop."""

    def __init__(self, journal: Journal, boundary: Boundary):
        self.journal, self.boundary = journal, boundary
        self.config = {"model": journal.episode.model}
        self.index = 0

    def query(self, messages: list[dict], **kwargs) -> dict:
        self.index += 1
        completion = self.journal.call(
            "model", self.index, {"messages": messages}, self.boundary
        )
        if completion.result.outcome is not Outcome.SUCCEEDED:
            raise RuntimeError(f"model attempt: {completion.result.outcome}")
        raw = self.journal.raw(completion, "response")
        try:
            value = json.loads(raw, object_pairs_hook=_json_object)
            if (
                type(value) is not dict
                or set(value) != {"command"}
                or type(value["command"]) is not str
                or not value["command"].strip()
            ):
                raise ValueError("expected one nonempty command")
        except (UnicodeError, ValueError) as error:
            raise FormatError(
                {"role": "user", "content": f"Invalid model response: {error}"}
            ) from error
        return {
            "role": "assistant",
            "content": raw.decode(),
            "extra": {"actions": [value]},
        }

    def format_message(self, **kwargs) -> dict:
        return kwargs

    def format_observation_messages(
        self, message, outputs, template_vars=None
    ) -> list[dict]:
        return [
            {"role": "user", "content": json.dumps(output, sort_keys=True)}
            for output in outputs
        ]

    def get_template_vars(self, **kwargs) -> dict:
        return kwargs

    def serialize(self) -> dict:
        return {"info": {"config": {"model": self.config}}}


class WorkerEnvironment:
    """Mini Environment protocol. Submission is interpreted after raw capture."""

    def __init__(self, journal: Journal, boundary: Boundary):
        self.journal, self.boundary = journal, boundary
        self.config = {"environment": journal.episode.environment}
        self.index = 0

    def execute(self, action: dict, cwd: str = "") -> dict:
        if set(action) != {"command"} or type(action["command"]) is not str or cwd:
            raise ValueError("expected a command in the fixed worker directory")
        self.index += 1
        completion = self.journal.call("tool", self.index, action, self.boundary)
        stdout = self.journal.raw(completion, "stdout")
        stderr = self.journal.raw(completion, "stderr")
        if completion.result.outcome is Outcome.INFRASTRUCTURE_FAILURE:
            raise RuntimeError("worker infrastructure failure")
        lines = stdout.splitlines(keepends=True)
        if (
            completion.result.exit_code == 0
            and lines
            and lines[0].strip() == b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        ):
            raise Submitted(
                {
                    "role": "exit",
                    "content": "Worker submitted candidate",
                    "extra": {
                        "exit_status": "Submitted",
                        "submission": b"".join(lines[1:]).decode(errors="replace"),
                    },
                }
            )
        return {
            "output": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "returncode": completion.result.exit_code,
        }

    def get_template_vars(self, **kwargs) -> dict:
        return kwargs

    def serialize(self) -> dict:
        return {"info": {"config": {"environment": self.config}}}


class WorkflowState(TypedDict):
    project: str
    episode: str
    receipt: str


def run_workflow(
    ledger_root: Path,
    checkpoint_path: Path,
    episode: Episode,
    *,
    model: Boundary,
    environment: Boundary,
    reconcile: Callable[[Request], AttemptResult | None] | None = None,
) -> dict:
    """Run or resume one bounded episode. Checkpoint state grants no authority."""
    if checkpoint_path.resolve().is_relative_to(ledger_root.resolve()):
        raise ValueError("checkpoint database must be separate from the ledger")
    with Ledger.open(ledger_root) as ledger:
        if reconcile is not None:
            session = ledger.start_session()
            for operation in ledger.operations():
                if operation.state == "unknown":
                    response = reconcile(operation.request)
                    if response is not None:
                        ledger.complete(
                            session, operation.request, response.result, response.raw
                        )
        unresolved(ledger)
        journal = Journal(ledger, episode)
        project_id = ledger.project.project_id

    def work(state: WorkflowState) -> dict:
        if state["project"] != project_id or state["episode"] != episode.episode_id:
            raise UnknownOutcome("checkpoint identity differs from the host")
        # LangGraph can execute nodes on another thread. Never share a connection.
        with Ledger.open(ledger_root) as ledger:
            unresolved(ledger)
            journal = Journal(ledger, episode)
            if journal.receipt() is None:
                agent = DefaultAgent(
                    WorkerModel(journal, model),
                    WorkerEnvironment(journal, environment),
                    system_template=(
                        "Use shell commands to complete the task. Submit with "
                        "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT "
                        "on the first stdout line."
                    ),
                    instance_template="{{task}}",
                    step_limit=episode.max_steps,
                    cost_limit=0,
                    output_path=None,
                )
                result = agent.run(episode.objective)
                record_once(
                    ledger,
                    journal.session,
                    Origin(
                        journal.prefix + "/finished",
                        "episode-finished",
                        "warranted-worker",
                        "1",
                        journal.completion_inputs(),
                    ),
                    {
                        "result.json": _encode(result),
                        "trajectory.json": _encode(agent.serialize()),
                    },
                )
            return {"receipt": journal.prefix + "/finished"}

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = StateGraph(WorkflowState).add_node("work", work)
        graph.add_edge(START, "work")
        graph.add_edge("work", END)
        app = graph.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": episode.episode_id}}
        state = app.get_state(config)
        if state.values and (
            state.values.get("project") != project_id
            or state.values.get("episode") != episode.episode_id
        ):
            raise UnknownOutcome("checkpoint identity differs from the host")
        result = app.invoke(
            None
            if state.values
            else {"project": project_id, "episode": episode.episode_id, "receipt": ""},
            config,
            durability="sync",
        )
    with Ledger.open(ledger_root) as ledger:
        unresolved(ledger)
        journal = Journal(ledger, episode)
        receipt = journal.receipt()
        if receipt is None or result["receipt"] != receipt.origin.operation_id:
            raise UnknownOutcome("checkpoint has no matching host receipt")
        return json.loads(ledger.read_artifact(receipt.artifacts["result.json"]))
