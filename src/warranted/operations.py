"""Host-mediated operations: worker requests executed and recorded by the host.

A worker requests operations by printing `WARRANTED_REQUEST` as the first stdout
line of a successful command, followed by one JSON object holding a batch:
`{"requests": [{"operation": name, "arguments": ...}, ...]}`. The request is
ordinary command output, so it is already recorded as raw evidence of the tool
operation that printed it. The host validates each item, deduplicates it, reserves
it in the ledger, executes it, records the result, and returns a host-written
observation. See docs/proposals/m7-host-operations.md for the design.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter_ns
from types import MappingProxyType
from typing import Any, Literal, Protocol

from warranted.acceptance import Evidence, _encode
from warranted.jobs import _FILE_NAME, JobContext, JobRunner
from warranted.ledger import (
    ROOT_SCOPE,
    BudgetExceeded,
    Completion,
    Origin,
    Outcome,
    Request,
    Result,
    _json_object,
)
from warranted.worker import (
    SUBMIT_MARKER,
    AttemptResult,
    Journal,
    UnknownOutcome,
    record_once,
)

PRODUCER = "warranted-operations"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_ITEMS = 32
SHOWN_LIMIT = 64 * 1024
BATCH_SHOWN_LIMIT = 256 * 1024
RESPONSES = "responses"


@dataclass(frozen=True)
class OperationResult:
    outcome: Outcome  # succeeded, failed, or infrastructure_failure
    usage: Mapping[str, int]
    raw: Mapping[str, bytes] = field(default_factory=dict)  # host evidence only
    shown: Any = None  # what the worker sees, at most 64 KiB encoded
    files: Mapping[str, bytes] = field(default_factory=dict)  # read-only copies

    def __post_init__(self):
        if self.outcome not in (
            Outcome.SUCCEEDED,
            Outcome.FAILED,
            Outcome.INFRASTRUCTURE_FAILURE,
        ):
            raise ValueError("an operation succeeds, fails, or fails on the host")
        _encode(self.shown)  # must be JSON-serialisable
        if any(
            type(v) is not bytes for v in (*self.raw.values(), *self.files.values())
        ):
            raise TypeError("operation evidence and files must be bytes")
        if not all(type(n) is str and _FILE_NAME.fullmatch(n) for n in self.files):
            raise ValueError("delivered file names must be safe basenames")


class OperationContext(JobContext):
    """What an operation may read and do: its declared task files, seeds, jobs."""

    def __init__(self, inputs: Mapping[str, bytes], jobs: JobRunner | None = None):
        super().__init__(jobs)
        self.inputs: Mapping[str, bytes] = MappingProxyType(dict(inputs))


class Operation(Protocol):
    """Trusted domain code the host runs on a worker's request.

    `effect` is "none" when the operation changes nothing outside the host;
    `shared` when it touches state shared beyond one run, so it reserves in the
    root scope; `reusable` when it is also deterministic, so equal requests may
    reuse a recorded result. `inputs` names the task files it may read, and `units`
    the ledger units its reservations may use.
    """

    version: str
    effect: Literal["none", "external"]
    shared: bool
    reusable: bool
    inputs: tuple[str, ...]
    units: frozenset[str]

    def parse(self, arguments: Any) -> Any: ...  # canonical arguments, or raise
    def reservation(self, arguments: Any) -> Mapping[str, int]: ...
    def execute(self, ctx: OperationContext, arguments: Any) -> OperationResult: ...


def validate_operations(operations: Mapping[str, Operation], files) -> None:
    """Reject a declaration the host could not enforce."""
    for name, operation in operations.items():
        if operation.effect not in ("none", "external"):
            raise ValueError(f"operation {name} has an unknown effect")
        if operation.reusable is True and operation.effect != "none":
            raise ValueError(f"operation {name} has an effect, so it cannot be reused")
        missing = set(operation.inputs) - set(files)
        if missing:
            raise ValueError(f"operation {name} reads unknown files: {sorted(missing)}")
        if not operation.units or "check" in operation.units:
            raise ValueError(f"operation {name} needs its own ledger units")


@dataclass(frozen=True)
class Delivery:
    path: str  # relative to /work, for example responses/3/1
    files: Mapping[str, bytes]


class Requests:
    """Handle the batch printed by one tool operation of one episode."""

    def __init__(
        self,
        operations: Mapping[str, Operation],
        files: Mapping[str, Evidence],
        jobs: JobRunner | None = None,
    ):
        validate_operations(operations, files)
        self.operations = MappingProxyType(dict(operations))
        self.files = MappingProxyType(dict(files))
        self.jobs = jobs

    def __call__(
        self, journal: Journal, index: int, tool: Completion
    ) -> tuple[list[dict], list[Delivery]]:
        stdout = journal.raw(tool, "stdout")
        _, _, body = stdout.partition(b"\n")
        try:
            items = self._parse(stdout, body)
        except ValueError as error:
            return [{"status": "error", "error": str(error)}], []
        results, deliveries = [], []
        refused = False
        shown_total = 0
        for number, item in enumerate(items, 1):
            result, files, refused = self._item(
                journal, index, tool, number, item, refused
            )
            path = f"{RESPONSES}/{index}/{number}"
            encoded = len(_encode(result))
            if shown_total + encoded > BATCH_SHOWN_LIMIT and "result" in result:
                # Over the batch limit: the shown result travels only as a file.
                files = {**files, "shown.json": _encode(result.pop("result"))}
                result["result_file"] = f"{path}/shown.json"
                encoded = len(_encode(result))
            shown_total += encoded
            if files:
                # Delivery is idempotent, so a replay repeats it until it holds.
                result["files"] = sorted(f"{path}/{name}" for name in files)
                deliveries.append(Delivery(path, files))
            results.append(result)
        return results, deliveries

    @staticmethod
    def _parse(stdout: bytes, body: bytes) -> list:
        if any(line.strip() == SUBMIT_MARKER for line in stdout.splitlines()):
            raise ValueError("a command cannot both request operations and submit")
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("a request batch is limited to 1 MiB")
        try:
            value = json.loads(body, object_pairs_hook=_json_object)
        except (ValueError, RecursionError):
            raise ValueError("the request is not one JSON object") from None
        if type(value) is not dict or set(value) != {"requests"}:
            raise ValueError('the request needs exactly one "requests" list')
        items = value["requests"]
        if type(items) is not list or not 1 <= len(items) <= MAX_ITEMS:
            raise ValueError(f"a batch holds 1 to {MAX_ITEMS} requests")
        return items

    def _item(self, journal, index, tool, number, item, refused):
        """One batch item. Returns (observation, files, budget refused so far)."""
        operation_id = f"{journal.prefix}/request/{index}/{number}"
        if (
            type(item) is not dict
            or set(item) != {"operation", "arguments"}
            or type(item["operation"]) is not str
        ):
            return _error("malformed request item"), {}, refused
        name = item["operation"]
        operation = self.operations.get(name)
        if operation is None:
            return _error(f"unknown operation {name!r}"), {}, refused
        try:
            arguments = operation.parse(item["arguments"])
        except (ValueError, TypeError, KeyError) as error:
            return _error(str(error), name), {}, refused
        ledger, session = journal.ledger, journal.session
        inputs = {n: self.files[n] for n in operation.inputs}
        key = _encode(
            {
                "operation": name,
                "version": operation.version,
                "arguments": arguments,
                "inputs": {n: ref.artifact.digest for n, ref in sorted(inputs.items())},
            }
        )
        capture = record_once(
            ledger,
            session,
            Origin(operation_id + "/input", "operation-input", PRODUCER, "1", {}),
            {
                "arguments.json": _encode(arguments),
                "key.json": key,
                # Which evidence each declared file is, so reconciliation later
                # reads this run's bytes even when another run triggers it.
                "inputs.json": _encode({n: ref.name for n, ref in inputs.items()}),
            },
        )
        # Inputs are named by the evidence they cite, so the ledger binds each one.
        stdout = Evidence.captured(tool.observation, "stdout")
        key = Evidence.captured(capture, "key.json")
        cited = [
            stdout,
            Evidence.captured(capture, "arguments.json"),
            Evidence.captured(capture, "inputs.json"),
            key,
        ]
        cited += list(inputs.values())
        request = Request(
            Origin(
                operation_id,
                "operation",
                name,
                operation.version,
                {e.name: e.artifact for e in cited},
            ),
            ledger.project,
        )

        # Replay returns exactly what was recorded: a result, a reuse, or a refusal.
        existing = ledger.lookup(request)
        if existing is not None and existing.completion is not None:
            return (*self._seen(ledger, name, existing.completion), refused)
        reuse = _record(ledger, operation_id + "/reuse")
        if reuse is not None:
            source = json.loads(ledger.read_artifact(reuse.artifacts["reuse.json"]))
            completion = _completed(ledger, source["source"])
            return (*self._seen(ledger, name, completion, source["source"]), refused)
        refusal = _record(ledger, operation_id + "/refused")
        if refusal is not None:
            return (
                json.loads(ledger.read_artifact(refusal.artifacts["refused.json"])),
                {},
                True,
            )

        def refuse(reason: str):
            shown = {"status": "refused", "operation": name, "error": reason}
            record_once(
                ledger,
                session,
                Origin(operation_id + "/refused", "operation-refused", name, "1", {}),
                {"refused.json": _encode(shown)},
            )
            return shown, {}, True

        if existing is None:
            if refused:
                return refuse("budget exhausted by an earlier item")
            if operation.reusable:
                source = _reusable_source(ledger, key.artifact)
                if source is not None:
                    source_id = source.request.origin.operation_id
                    record_once(
                        ledger,
                        session,
                        Origin(
                            operation_id + "/reuse",
                            "operation-reuse",
                            name,
                            operation.version,
                            {key.name: key.artifact, stdout.name: stdout.artifact},
                        ),
                        {"reuse.json": _encode({"source": source_id})},
                    )
                    return (
                        *self._seen(ledger, name, source.completion, source_id),
                        refused,
                    )
            reservation = dict(operation.reservation(arguments))
            if not reservation or not set(reservation) <= set(operation.units):
                raise ValueError(f"operation {name} reserved undeclared units")
            scope = ROOT_SCOPE if operation.shared else journal.episode.scope
            try:
                ledger.reserve(session, request, reservation, scope)
            except BudgetExceeded as error:
                return refuse(f"budget exhausted: {error}")
        if not ledger.begin(session, request):
            raise UnknownOutcome(f"unknown outcome: {operation_id}")
        ctx = OperationContext(
            {n: ledger.read_artifact(ref.artifact) for n, ref in inputs.items()},
            self.jobs,
        )
        started = perf_counter_ns()
        try:
            result = operation.execute(ctx, arguments)
            if type(result) is not OperationResult:
                raise TypeError("an operation must return an OperationResult")
        except Exception as error:
            # The operation may have consumed resources; its outcome is unknown.
            raise UnknownOutcome(
                f"operation {operation_id} failed: {error!r}"
            ) from error
        completion = ledger.complete(
            session,
            request,
            Result(
                *_settled(_outcome(result)),
                dict(result.usage),
                perf_counter_ns() - started,
            ),
            _evidence(result, ctx),
        )
        return (*self._seen(ledger, name, completion), refused)

    def _seen(self, ledger, name, completion, source=None):
        """The observation and delivered files for one recorded result."""
        return self._shown(ledger, name, completion, source), self._files(
            ledger, completion
        )

    @staticmethod
    def _shown(ledger, name, completion: Completion, source) -> dict:
        outcome = completion.result.outcome
        result = {"status": outcome.value, "operation": name}
        if source is not None:
            result["reused_from"] = source
        if outcome in (Outcome.SUCCEEDED, Outcome.FAILED):
            result["result"] = json.loads(
                ledger.read_artifact(completion.observation.artifacts["shown.json"])
            )
        return result

    @staticmethod
    def _files(ledger, completion: Completion) -> dict[str, bytes]:
        if completion.result.outcome not in (Outcome.SUCCEEDED, Outcome.FAILED):
            return {}
        return {
            name.removeprefix("files/"): ledger.read_artifact(ref)
            for name, ref in completion.observation.artifacts.items()
            if name.startswith("files/")
        }


def _cited(origin: Origin, channel: str):
    """The artifact an operation cites for one of its own input channels."""
    for name, ref in origin.inputs.items():
        if name.endswith("/" + channel):
            return ref
    return None


def _record(ledger, operation_id: str):
    matches = [o for o in ledger.history() if o.origin.operation_id == operation_id]
    return matches[0] if matches else None


def _completed(ledger, operation_id: str) -> Completion:
    for operation in ledger.operations():
        if operation.request.origin.operation_id == operation_id:
            if operation.completion is None:
                break
            return operation.completion
    raise UnknownOutcome(f"reused operation {operation_id} has no recorded result")


def _reusable_source(ledger, key):
    """An earlier measured result for the same key, from any run in the project."""
    for operation in ledger.operations():
        origin = operation.request.origin
        if (
            origin.kind == "operation"
            and _cited(origin, "key.json") == key
            and operation.completion is not None
            and operation.completion.result.outcome
            in (Outcome.SUCCEEDED, Outcome.FAILED)
            and "unmeasured.json" not in operation.completion.observation.artifacts
        ):
            return operation
    return None


def _error(message: str, name: str | None = None) -> dict:
    result = {"status": "error", "error": message}
    if name is not None:
        result["operation"] = name
    return result


def _outcome(result: OperationResult) -> Outcome:
    """Oversized worker-visible output is the operation's fault, not a result."""
    if len(_encode(result.shown)) > SHOWN_LIMIT:
        return Outcome.INFRASTRUCTURE_FAILURE
    return result.outcome


def _settled(outcome: Outcome) -> tuple[Outcome, int | None]:
    return outcome, {Outcome.SUCCEEDED: 0, Outcome.FAILED: 1}.get(outcome)


def _evidence(result: OperationResult, ctx: OperationContext | None) -> dict:
    """Raw evidence for a result; an oversized `shown` becomes a host failure."""
    raw = dict(result.raw)
    shown = _encode(result.shown)
    if len(shown) > SHOWN_LIMIT:
        raw["oversized-shown.json"] = shown
        shown = _encode(None)
    raw["shown.json"] = shown
    if ctx is not None:
        raw |= {
            "seeds.json": _encode(ctx.seeds),
            "jobs.json": _encode(ctx.job_log),
            **ctx.job_output,
        }
    raw |= {f"files/{n}": data for n, data in result.files.items()}
    return raw


def reconcile_operation(
    ledger,
    operations: Mapping[str, Operation],
    request: Request,
    jobs: JobRunner | None = None,
) -> AttemptResult | None:
    """Settle an unknown operation without executing it again, or return None.

    An effect-free operation changed nothing outside the host, so only its usage is
    unknown: it is charged its full reservation as an unmeasured infrastructure
    failure. An external operation can be settled only by its own `reconcile`, from
    a receipt; without one it stays unknown.
    """
    if request.origin.kind != "operation":
        return None
    operation = operations.get(request.origin.producer)
    if operation is None or operation.version != request.origin.producer_version:
        return None
    if operation.effect == "none":
        reservation = ledger.lookup(request).reservation
        return AttemptResult(
            Result(Outcome.INFRASTRUCTURE_FAILURE, None, dict(reservation), 0),
            {
                "unmeasured.json": _encode(
                    {"reason": "the host died during an effect-free operation"}
                ),
                "shown.json": _encode(None),
            },
        )
    receipt = getattr(operation, "reconcile", None)
    if receipt is None:
        return None
    arguments = json.loads(
        ledger.read_artifact(_cited(request.origin, "arguments.json"))
    )
    # The files this operation cited when it was requested, whichever run asks now.
    names = json.loads(ledger.read_artifact(_cited(request.origin, "inputs.json")))
    ctx = OperationContext(
        {
            n: ledger.read_artifact(request.origin.inputs[names[n]])
            for n in operation.inputs
        },
        jobs,
    )
    result = receipt(ctx, arguments)
    if result is None:
        return None
    if type(result) is not OperationResult:
        raise TypeError("reconcile must return an OperationResult or None")
    return AttemptResult(
        Result(*_settled(_outcome(result)), dict(result.usage), 0),
        _evidence(result, ctx),
    )
