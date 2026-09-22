"""Host-selected A–E files. This policy does not inspect or trust worker prose."""

import base64
import json
from collections.abc import Mapping
from enum import StrEnum

from warranted.acceptance import Evidence, _encode
from warranted.ledger import Ledger, Origin, Request
from warranted.worker import UnknownOutcome, record_once


class Condition(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


def capture_history(
    ledger: Ledger, session: str, episode_id: str, previous: tuple[str, ...]
) -> Evidence:
    """Capture complete model/shell exchanges for explicitly named past episodes.

    Copy public request payloads and worker-visible response channels, not ledger
    metadata, private checker observations, or dependency edges. Preserve bytes
    with base64. Missing or unfinished history is not an empty successful history.
    """
    if (
        type(previous) is not tuple
        or any(type(parent) is not str for parent in previous)
        or len(set(previous)) != len(previous)
        or episode_id in previous
    ):
        raise ValueError("history must name distinct previous episodes")
    events = {event.origin.operation_id: event for event in ledger.history()}
    inputs, rows = {}, []
    for parent in previous:
        prefix = f"episode/{parent}"
        finished = events.get(prefix + "/finished")
        if finished is None or (
            finished.origin.kind,
            finished.origin.producer,
            finished.origin.producer_version,
        ) != ("episode-finished", "warranted-worker", "1"):
            raise UnknownOutcome("history requires a completed worker episode")
        ledger.lookup(Request(finished.origin, ledger.project))
        for op in ledger.operations():
            if not op.request.origin.operation_id.startswith(prefix + "/"):
                continue
            ledger.lookup(op.request)
            if op.completion is None:
                raise UnknownOutcome("history has an unfinished worker attempt")
            kind = op.request.origin.kind
            if kind not in ("model", "tool"):
                raise ValueError("unexpected worker history operation")
            request = events[op.request.origin.operation_id + "/request"]
            request_ref = Evidence.captured(request, "request.json")
            if op.request.origin.inputs.get(request_ref.name) != request_ref.artifact:
                raise ValueError("history request does not match its attempt")
            inputs[request_ref.name] = request_ref.artifact
            response = {}
            for channel in ("response",) if kind == "model" else ("stdout", "stderr"):
                ref = Evidence.captured(op.completion.observation, channel)
                inputs[ref.name] = ref.artifact
                response[channel] = base64.b64encode(
                    ledger.read_artifact(ref.artifact)
                ).decode()
            rows.append(
                _encode(
                    {
                        "episode": parent,
                        "kind": kind,
                        "request": json.loads(
                            ledger.read_artifact(request_ref.artifact)
                        ),
                        "response_base64": response,
                        "exit_code": op.completion.result.exit_code,
                    }
                )
            )
    event = record_once(
        ledger,
        session,
        Origin(
            "visible-history/" + episode_id,
            "visible-history",
            "warranted-contexts",
            "1",
            inputs,
        ),
        {"history.jsonl": b"\n".join(rows) + (b"\n" if rows else b"")},
    )
    return Evidence.captured(event, "history.jsonl")


def capture_context(
    ledger: Ledger,
    session: str,
    episode_id: str,
    layers: Mapping[Condition, Mapping[str, Evidence]],
) -> dict[str, Evidence]:
    """Freeze the permitted files for one episode under its pinned treatment.

    A: workspace/notes; B: visible history; C: executable models/regressions;
    D: dependencies/applicability; E: proof work. Fixtures own content selection.
    No layer is inferred from ledger contents or from candidate-authored labels.
    """
    try:
        condition = Condition(ledger.project.manifest.environment["condition"])
    except (KeyError, ValueError) as error:
        raise ValueError("unknown or missing knowledge condition") from error
    if set(layers) != set(Condition):
        raise ValueError("host must supply all five context layers")
    selected = {}
    for level in Condition:
        if level > condition:
            break
        for name, ref in layers[level].items():
            if name in selected or name == "selection.json":
                raise ValueError("duplicate or reserved context filename")
            if type(ref) is not Evidence:
                raise TypeError("context requires captured evidence")
            selected[name] = ref
    inputs = {}
    for ref in selected.values():
        if ref.name in inputs and inputs[ref.name] != ref.artifact:
            raise ValueError("conflicting context evidence")
        inputs[ref.name] = ref.artifact
    event = record_once(
        ledger,
        session,
        Origin(
            "worker-context/" + episode_id,
            "worker-context",
            "warranted-contexts",
            "1",
            inputs,
        ),
        {
            **{
                name: ledger.read_artifact(ref.artifact)
                for name, ref in selected.items()
            },
            "selection.json": _encode(
                {"condition": condition, "files": sorted(selected)}
            ),
        },
    )
    return {name: Evidence.captured(event, name) for name in selected}
