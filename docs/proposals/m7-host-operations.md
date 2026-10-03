# M7 design note: host-mediated operations

Proposed and accepted 2026-10-03, with its review questions decided the same
day (see [decisions](#decisions)); implemented the same day in
`warranted.operations`, the worker, the sandbox, and the experimental task layer.
The [worker reference](../reference/worker-and-containment.md#host-mediated-operations)
describes the implemented behaviour. This note designs the
**host-mediated operations** item of
[M7](../roadmap.md#m7--stable-harness-api-and-cli), whose implementation the
roadmap allowed only after a reviewed design.

## Problem

The worker runs shell commands in a container with no network, no credentials,
and no host mounts ([worker and containment](../reference/worker-and-containment.md)).
That boundary is deliberate, but some work belongs outside it:

- **Authoritative results.** A probe run inside the worker's container is the
  worker's own claim. ReSchema's `experiment` command, which runs the target
  program on a chosen input, should produce a result the host observed, recorded
  as reusable evidence and charged per probe
  ([M8](../roadmap.md#m8--reschema-rebuilt-on-warranted)).
- **Credentials, network, paid APIs, and private data**, which must never enter
  the worker's container.
- **External side effects**, which need reservation, an operation identity, and
  reconciliation instead of a blind retry.

Today the only path out of an episode is the submission, which ends it. Checker
jobs run only during a check, after the episode has ended, and have no external
effects ([open question 7](m7-harness-api.md#open-questions)).

## Requirements

The roadmap fixes the scope of this design:

1. A worker requests an operation without network access or a socket to the host.
2. Each operation has a stable identity, and a repeated request is deduplicated
   rather than executed twice.
3. The operation is reserved before execution, in the same ledger and scopes as
   model, tool, and check units.
4. If the host dies during an operation, the outcome is unknown and stays blocked
   until it is reconciled. Nothing is retried blindly.
5. The design defines what the worker sees back.
6. Forged requests, forged results, and budget bypasses have negative cases.

The invariants in [AGENTS.md](../../AGENTS.md) also apply. In particular,
operations must not become a mandatory sequence of model-facing tools, and shell
access must keep the same acceptance and external-effect boundaries as every
other interface.

## Proposal

### Requests travel through the existing command channel

The host already sees every worker command: `WorkerEnvironment.execute` journals
the command, the sandbox runs it, and the host records `stdout`, `stderr`, and a
privileged exit status before the worker parses anything. Submissions already use
this channel. A command that exits 0 with `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
as its first stdout line ends the episode, following
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)'s submission
convention.

A request uses the same channel with a different marker. A command that exits 0
with `WARRANTED_REQUEST` as its first stdout line, and one JSON object as the
rest, is a request. The object holds a batch of one or more items:

```json
{"requests": [
  {"operation": "experiment", "arguments": {"input": "..."}},
  {"operation": "experiment", "arguments": {"input": "..."}}
]}
```

- No new channel crosses the container boundary. The request is ordinary command
  output, so it is already recorded as raw evidence of the tool operation that
  produced it, with the worker as its author.
- A domain's worker image wraps the marker in an ordinary command. ReSchema's
  image would provide `experiment <input-file>`, which reads the file, prints the
  marker and JSON, and exits. The worker never writes the marker by hand, and
  nothing requires it to use operations at all.
- A request carries its data inline. File contents are base64 inside the JSON,
  and the encoded request may be at most 1 MiB, within the existing 2 MiB stdout
  cap.
- A batch holds at most 32 items. Batching is optional but encouraged: the whole
  batch costs one `tool` unit and one worker turn, while each item is still its
  own operation with its own reservation. A domain's command should accept
  several inputs, for example `experiment a.bin b.bin c.bin`, and its task
  presentation should say that batching saves turns.
- One command makes at most one batch. A command that prints both markers is
  neither a request nor a submission. It gets an error observation.

Because the request is untrusted text, accidental or forged markers are harmless.
A marker printed by `cat` or by a background process is just a request, and it is
validated and charged like any other.

### Domains declare their operations

Operations are domain code, like checkers. They run in the host process and are
trusted:

```python
class Operation(Protocol):
    version: str
    effect: Literal["none", "external"]
    shared: bool  # touches state shared beyond one run
    reusable: bool  # deterministic and effect-free: equal requests give equal results
    inputs: tuple[str, ...]  # the task files the operation may read

    def parse(self, arguments: JSON) -> JSON: ...  # canonical arguments, or raise
    def reservation(self, arguments: JSON) -> Mapping[str, int]: ...
    def execute(self, ctx: OperationContext, arguments: JSON) -> OperationResult: ...
    # Optional, for effect == "external": settle an unknown outcome from a receipt.
    def reconcile(
        self, ctx: OperationContext, arguments: JSON
    ) -> OperationResult | None: ...


@dataclass(frozen=True)
class OperationResult:
    outcome: Outcome  # succeeded, failed, or infrastructure_failure
    usage: Mapping[str, int]
    raw: Mapping[str, bytes]  # host evidence
    shown: JSON  # what the worker sees, at most 64 KiB encoded
    files: Mapping[str, bytes] = {}  # delivered read-only to the worker
```

`Domain.operations: Mapping[str, Operation]` names them. `reusable` may be true
only when `effect` is `"none"`, and only if the operation is deterministic: an
effect-free probe that draws a seed or measures time is not reusable. Their versions join the
[domain identity](m7-harness-api.md#open-questions), so a changed
operation blocks resume. `OperationContext` gives the operation the task files it names in `inputs`
(worker-visible or private), `draw_seed`, and `run_job`, as `CheckContext` does
for checkers. Reading any other task file raises.
Credentials stay in the domain's own host-side configuration, never in task
files.

An operation's units are ordinary ledger units, such as `probe`, chosen by the
domain. They must appear in the project allowances, and run configuration
`[budgets]` may cap them, as it caps model, tool, and token units. The exception
is a unit used by any shared operation. Shared operations reserve in the root
scope, which a run's cap does not see, so a run configuration that caps such a
unit is refused at start rather than silently unenforced. The project allowance
still binds those units.

### Lifecycle of one request

Each item of a batch follows these steps, in order:

1. **Record.** The tool operation that printed the batch completes as usual and
   is charged one `tool` unit. Its stdout holds the request bytes.
2. **Validate.** The host checks that the item is well formed, names a
   declared operation, and passes the operation's `parse`. If any check fails,
   the item gets an error result. No operation is recorded or charged
   beyond the tool unit.
3. **Identify.** The operation ID is `<episode>/request/<tool index>/<item>`, so
   each batch item maps to one operation, and resuming finds the same IDs. The request's
   origin binds the tool operation's stdout artifact, the operation name and
   version, and the digest of the canonical arguments.
4. **Deduplicate.** If the operation ID is already completed, its recorded result
   is reused without executing anything. A `reusable` operation is also
   deduplicated by content, across every run in the project. The key is the
   operation name and version, the canonical arguments, and the digests of the
   task files named in `inputs`. Only a completed `succeeded` or `failed` result
   is reused; an infrastructure failure, an unmeasured settlement, or an unknown
   operation never is. The host records a reuse in the requesting run that cites
   the earlier operation and its run, and charges zero units. The worker sees the
   same result a fresh execution would give, so reuse reveals nothing new; the
   citation keeps cross-run reuse visible to lineage and evaluation reports. An
   operation that is not reusable, including every operation with an external
   effect, is never deduplicated by content: asking twice means acting twice, and
   each request is charged.
5. **Reserve.** The host reserves `reservation(arguments)` in the run's scope. An
   operation with `shared = True` uses the root scope instead, following the
   [run scopes rule](m7-run-scopes.md#shared-external-state) for operations that
   write to state shared across runs. If the reservation exceeds a cap, the worker
   sees a budget error and the operation is not recorded.
6. **Execute.** `begin` dispatches, which refuses if the scope already holds an
   unknown operation or a breach. The host then calls `execute`. An exception from
   `execute` leaves the operation unknown, with its reservation held.
7. **Complete.** The host records the result's raw evidence and settled usage.
   Usage above the reservation is a breach that blocks the scope, as for every
   other unit.
8. **Deliver.** The worker's next observation carries `shown` in a field the
   host writes, separate from the command's stdout. If the result has `files`, the
   host writes them into `/work/responses/<tool index>/<item>/` before the next
   command.
   The files are owned by root and read-only to the worker, which runs as UID
   1000 without `CAP_DAC_OVERRIDE`. They are convenience copies; the ledger
   record is the result.

Within a batch, items run one at a time, in the order given:

- An item that fails validation is refused on its own; later items still run.
- Once one item's reservation is refused for budget, every later item is refused
  too without being reserved, since remaining budget only shrinks.
- If an item's outcome becomes unknown, no later item runs, and the episode stops
  as for any unknown outcome.
- The observation lists every item's result in order. Each item's `shown` keeps
  its own 64 KiB limit, and the batch's observation is limited to 256 KiB. Once
  that is reached, each later item's `shown` is delivered only as a file in its
  response directory, and the observation names the file.

Execution happens between two worker commands, while the container stays up. Any
background processes the worker left running continue, but they cannot reach the
host. A long operation blocks the episode for its duration, so every operation
needs its own timeout.

### Unknown outcomes

A host death between `begin` and `complete` leaves the operation unknown, in its
scope. On resume, the same operation ID is found unknown, and the run stops as
`unknown`, as it does for a lost model response. Reconciliation then depends on
the effect:

- **`effect = "none"`** (for example, a probe of a pinned binary in a contained
  job). The operation changed nothing outside the host, so the only unknown is
  what it consumed. Reconciliation settles it as an infrastructure failure
  charged its full reservation, the conservative bound, with a record that the
  usage was unmeasured. It does not execute the operation again: a second run
  would consume resources the first attempt's settlement could not account for.
  The worker can ask again, which is a new operation with its own reservation.
- **`effect = "external"`**. Only the operation's `reconcile` can settle it, from
  a receipt held by the external service, following the
  [receipt pattern](../reference/model-adapters.md#fake-receipt-service) that M3
  uses for model attempts. If there is no receipt or `reconcile` is absent, the
  operation stays unknown and the scope stays blocked. Nothing is retried.

### What the worker sees

| Case | Observation |
| --- | --- |
| Completed or reused | `shown`, and the paths of any delivered files |
| Malformed, unknown operation, or failed `parse` | An error naming the problem; no operation recorded |
| Budget exhausted | A budget error naming the unit; no operation recorded |
| Operation `failed` | `shown` from the operation, marked as a failure |
| `infrastructure_failure` | A generic failure; the raw evidence stays host-only |
| Unknown | Nothing; the episode stops and the run ends `unknown` |

The 64 KiB limit and the oversized-is-a-fault rule from
[open question 4](m7-harness-api.md#open-questions) apply to
`shown`. As with checker feedback, keeping private inputs out of `shown` is the
operation's responsibility; the host guarantees only that `raw` never reaches the
worker.

### Results as evidence

A completed operation is host evidence: its producer is the operation, not the
worker. A checker or claim can cite it by operation ID, and the host verifies that
the operation exists, completed, belongs to the same run, and matches the current
operation version. A reuse record counts as belonging to the run that requested
it; the host follows its citation to the source operation's evidence. The evidence states what the host observed for those
arguments. The arguments themselves remain worker-authored, so a result says
nothing about why the worker chose them.

Copies the worker holds prove nothing. A delivered file copied into the workspace
and submitted is just a candidate file. Only the ledger record counts.

### Replay

Replay of an episode returns recorded request results by operation ID, under the
same compatibility checks as other recorded outcomes. A request with no recorded
result is unsupported; replay never executes an operation.

## Negative cases to implement first

- A forged request (the marker printed by `cat`, a background process, or a
  command that also exits non-zero) is either refused or treated as an ordinary
  request: validated, charged, and recorded with the worker as author. It never
  bypasses `parse` or the reservation.
- A command that prints both the request and submission markers is neither.
- A request naming an undeclared operation, or failing `parse`, records no
  operation and charges only the tool unit.
- A request over a run's cap for the operation's unit is refused before
  execution; the cap holds across restarts.
- Repeating a reusable request with the same arguments and inputs reuses the
  result at zero cost, within a run and from another run in the same project.
  The reuse cites the source operation and run. Changing an input file named in
  `inputs`, or the operation version, gives a new key and executes again.
- An infrastructure failure, an unmeasured settlement, or an unknown operation is
  never reused.
- An effect-free operation that is not declared reusable, and every
  external-effect operation, executes and is charged again on every request.
- An operation reading a task file outside its declared `inputs` raises.
- A batch over 32 items, or over 1 MiB, is refused as a whole. Within a batch, a
  budget refusal refuses every later item without reserving it, and an unknown
  outcome stops the remaining items.
- A host killed during `execute` leaves the operation unknown and the run blocked.
  Reconciling an effect-free operation charges its full reservation without
  executing it again, so the run's spent total never undercounts the lost attempt.
  An external one without a receipt stays blocked and is never executed again.
- A run configuration that caps a unit used by a shared operation is refused at
  start.
- A worker cannot modify delivered files in place, and whatever it does with its
  copies, a forged result file in a submitted workspace cannot be cited as
  evidence.
- Worker stdout imitating a result does not appear in the host-written result
  field.
- A shared operation runs in the root scope, so its unknown outcome blocks every
  run.
- An oversized `shown` is an infrastructure failure, kept whole as host evidence.

## Alternatives considered

- **A socket or RPC endpoint inside the container.** This is direct, but it
  breaks the no-socket boundary and adds a host service the worker can probe.
- **Request files picked up after each command.** This needs an extra host read
  after every command. Background processes could also change the file between
  the command's exit and the read. A stdout marker is atomic with the command's
  recorded output.
- **Requests only at the end of an episode.** No new mechanism is needed, but every
  probe would cost a fresh container and a lost conversation, which makes the
  probe loop ReSchema needs impractical.
- **Model-facing tools (MCP or function calls).** ReSchema is moving away from
  these to a shell-and-files worker. They also conflict with keeping ledger
  records from becoming a mandatory tool sequence.
- **Running authoritative probes as checker jobs.** Checks run only after a
  submission ends the episode, so the worker could not use the results while
  investigating.

## Implementation outline

These are the expected changes, recorded so the review can judge the cost:

- `worker.WorkerEnvironment.execute` recognises the request marker after
  journaling the tool operation. It then runs the lifecycle above through a
  host-supplied request handler, and returns the observation.
- `Sandbox` gains a method that writes read-only result files into a running
  container through `podman exec`, as `_LOAD` does at creation.
- `warranted.experimental` gains `Domain.operations`, `OperationContext`, the
  request handler with scope selection and content deduplication, and domain
  units in run budgets.
- A probe operation is added to the mystery fixture, running the original
  program on a worker-chosen input in a contained job. Its tests cover the
  negative cases above.

## Decisions

The review questions were decided on 2026-10-03:

1. **A reused result costs nothing.** Each reuse is still recorded and cites its
   source, so repeated probes remain visible in accounting and reports.
2. **Results are reused across runs in a project**, for reusable operations,
   keyed by operation, version, canonical arguments, and the digests of the
   operation's declared input files. Reuse is an exact replay of a recorded
   observation, not memory: it carries no claim beyond what the source operation
   observed, and it stays separate from
   [scoped memory](../roadmap.md#m7--stable-harness-api-and-cli).
3. **Batches are supported and encouraged, not required.** One command carries up
   to 32 items, each its own operation, for one `tool` unit and one worker turn.

Still open:

4. Do shared operations need per-run caps? That would need the ledger to charge
   one operation against both its run's scope and the root scope. Refusing the cap
   keeps the ledger unchanged until a case needs it.

## Implementation notes

- **Operations declare `units`.** The design's interface had no static list of
  the units an operation may reserve, but two rules need one: refusing run caps on
  units of shared operations, and giving each declared unit a zero default
  allowance so an unfunded operation is refused rather than crashing the run. A
  reservation naming an undeclared unit is a host error.
- **Refusals are recorded.** A budget refusal depends on ledger state at the
  time, so replaying an episode could otherwise turn a refused item into an
  executed one and change what the worker was shown. Each refusal is recorded
  under the item's ID and returned unchanged on replay, as results and reuses are.
- **Inputs cite evidence.** The operation's request cites the command's stdout,
  the canonical arguments, the reuse key, and each declared task file by their
  evidence names, so the ledger checks every one against a recorded capture.
- **Delivery is idempotent and repeated on replay.** A host that stops between
  recording a result and delivering its files completes the delivery when the
  episode replays. A repeated delivery with the same content is a no-op, and
  different content at a delivered path is refused. Without a live container,
  delivery is skipped; the next new command then needs a fresh episode anyway.
- **The host owns `/work/responses`.** It is created root-owned and read-only
  when the container is loaded, so the worker cannot create a path or symlink
  under it before a delivery. Delivery refuses any component that is not a
  host-owned directory.
- **Both markers, in either order, are neither.** The worker and the sandbox both
  apply the rule, so output beginning with the submission marker and carrying a
  request marker is not captured as a candidate.
- **Reconciliation reads the cited inputs.** Each request records which evidence
  each declared input file is, and `reconcile` reads those bytes, so a shared
  operation settled during another run never sees that run's files.
- **A request marker without operations is answered.** A task whose domain
  declares no operations returns one error result rather than silently ignoring
  the request.
- `examples/m7/mystery/` gains a reusable `probe` operation that runs the original
  program in a contained job, and `probe.py`, the request command a worker image
  would provide.
