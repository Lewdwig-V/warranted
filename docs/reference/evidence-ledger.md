# Evidence ledger

`warranted.ledger.Ledger` is the host's authoritative store of raw evidence and
operation receipts for one project. It keeps exact bytes with their origin, binds
each operation ID to one request, records reservations and dispatch before
execution, and settles usage exactly once. `warranted.exports.export_evidence`
writes a selected, non-authoritative copy for a separate consumer. Both are Python
interfaces for trusted host code. They are not worker tools and do not isolate a
worker.

For a runnable example see the README's
[local evidence API](../../README.md#local-evidence-api). The fixed CSV
walkthrough that exercises the ledger, exports, and resume is described in
[CSV transformation](../fixtures/csv-transformation.md). Claims and acceptance
build on this ledger; see [claims and acceptance](claims-and-acceptance.md).

## Storage

A project directory contains `ledger.sqlite3` and `artifacts/sha256/`. The ledger
uses only the Python standard library (SQLite, JSON, hashing, files); it has no
runtime dependencies. Keep the working directory of any script or worker separate
from this authoritative directory.

An artifact is an exact byte sequence stored at `artifacts/sha256/<digest>`.
Storage paths derive only from validated digests; callers cannot choose paths.
Temporary files are created in the same directory before publication.

The database has five tables. Identity, ordering, and session references are
columns; manifests, origins, requests, and artifact references are versioned JSON
validated before writing and on read.

| Record | Fields | Purpose |
| --- | --- | --- |
| Project | Project ID, storage format version, creation time, immutable manifest, named snapshot references | Identify the project and its exact starting context after reopening. |
| Session | Session ID, start time | Separate process sessions without discarding history. |
| Observation | Committed sequence, session ID, capture time, origin, named artifact references, optional superseded sequence | Preserve one capture and its lineage. Sequence orders history independently of wall-clock time; it is stable but need not be gapless. |
| Scope | Scope ID, creating session, creation time, immutable per-unit caps | Bound and isolate one run's work inside the project. |
| Operation | Operation ID, scope, request, reservation, original session/time, optional dispatch session/time, optional completion observation/result/breaches | Preserve identity, uncertain execution, and settled usage. |

The current storage format is version 3. Projects in earlier formats fail
explicitly on open and remain unchanged; there is no migration.

## Value types

All value types are frozen dataclasses with validated fields.

| Type | Fields |
| --- | --- |
| `ArtifactRef` | `digest` (lowercase SHA-256 hex), `size_bytes`. The writer derives both from the bytes. |
| `Snapshot` | `data` (bytes), `origin` (source label), `version`. Input to `Ledger.create`. |
| `SnapshotRef` | `artifact`, `origin`, `version`. Stored in `project.snapshots`. |
| `Manifest` | `fixture_id`, `fixture_version`, `run_id`, `world_id`, `environment` (str to str), `allowances` (unit to int), `parent_world_id` (must be `None`). |
| `Project` | `project_id`, `created_at`, `manifest`, `snapshots`. |
| `Session` | `session_id`, `started_at`. |
| `Origin` | `operation_id`, `kind`, `producer`, `producer_version`, `inputs` (name to `ArtifactRef`). |
| `Observation` | `sequence`, `session_id`, `captured_at`, `origin`, `artifacts`, `supersedes`. |
| `Request` | `origin`, `context` (the complete `Project`). |
| `Result` | `outcome` (`Outcome`), `exit_code`, `usage` (unit to int), `elapsed_ns`. |
| `Completion` | `observation`, `result`, `breaches` (tuple of unit names). |
| `Operation` | `request`, `session_id`, `reserved_at`, `reservation`, `dispatch_session_id`, `dispatched_at`, `completion`; property `state`. |
| `Balance` | `limit`, `spent`, `reserved`; property `available`. |

The manifest records the fixture and run identities, root world identity with an
explicit absent parent, environment, declared allowances, and (through the
snapshots) the starting inputs with versions and origin labels. Labels explain
where the host obtained bytes; they do not establish byte identity. All sessions
reuse this one fixed context. It records the
[Dream-RSI context requirements](../design.md#dream-rsi-exploration-and-replay)
without a world registry or branch executor.

Artifact identity does not replace observation identity: two captures can share
bytes while keeping separate origins and times.

### Origins and inputs

An origin names the host's operation ID and kind, the producer and its version,
and exact input references. An input name selects either a project snapshot or a
committed observation channel written `observation/<sequence>/<channel>`, which
lets one operation consume another's output. Snapshot names take precedence when
both forms match. Channel names are labels, not filesystem paths. Before use the
host checks that the named record exists, that its reference matches (not merely
equal bytes elsewhere), and that the stored bytes match the digest and length.

Store each raw channel of a capture separately, for example standard output,
standard error, and each output file. Exit status, usage, and elapsed time are
typed `Result` fields. Raw text never becomes a structured result because it
contains words such as `accepted` or `success`.

## Host API

The names and parameter shapes are provisional until a second use case tests them.

| Method | Contract |
| --- | --- |
| `Ledger.create(root, manifest, snapshots)` | Create a new project from a `Manifest` and named `Snapshot` bytes; compute references internally. Refuse an existing path. |
| `Ledger.open(root)` | Open an existing project. Require a complete, supported schema and valid metadata. Never create or repair a project. |
| `ledger.project` | The immutable `Project` record. |
| `ledger.root` | The authoritative project directory. |
| `ledger.start_session()` | Persist and return a new session ID. Opening or reading does not create a session. |
| `ledger.sessions()` | All sessions, including those with no observations. |
| `ledger.record(session_id, origin, raw, supersedes=None)` | Publish non-empty named raw bytes and commit one observation with all references. Return the committed `Observation`. |
| `ledger.history(after=0)` | Observations in committed sequence order with origins and references, without loading artifact contents. |
| `ledger.read_artifact(ref)` | Return exact bytes after checking digest and length. |
| `ledger.open_scope(session_id, scope_id, caps)` | Record a scope once; see [scopes](#scopes). |
| `ledger.scopes()` | Recorded scopes and their caps. |
| `ledger.reserve(session_id, request, reservation, scope=ROOT_SCOPE)` | See [operation identity](#operation-identity-and-recovery). |
| `ledger.begin(session_id, request)` | Commit a dispatch marker; only `True` permits execution. Refuses dispatch in a blocked scope. |
| `ledger.complete(session_id, request, result, raw)` | Commit evidence and completion together; settle usage once. |
| `ledger.lookup(request)` | Match identity, verify bytes, return the `Operation` or `None`. |
| `ledger.operations()` | Operation metadata in reservation order, including pending and unknown work. |
| `ledger.accounting(scope=None)` | Per-unit `Balance` derived from committed operations, for the project or one scope. |
| `ledger.check_budget(scope=ROOT_SCOPE)` | Raise `BudgetExceeded` if a breach or the project total blocks this scope. |
| `ledger.unresolved(scope=ROOT_SCOPE)` | Unknown operations that block this scope. |

`Ledger` is a context manager that closes its connection; `close()` does the same.
There is no arbitrary SQL access, generic event writer, update, or delete method.

`record` rejects an unknown session, malformed or mismatched input reference,
empty capture, or nonexistent superseded observation before committing. A
correction records a new observation that points to the old one with `supersedes`.

`record` captures evidence only. It does not complete an operation, enforce a
budget, deduplicate execution, or establish acceptance. A repeated call records
another capture, so do not retry it automatically after a lost response; use the
operation methods when execution identity matters.

### Errors

| Exception | Raised when |
| --- | --- |
| `InvalidProject` | The project header, schema, or stored metadata is incomplete or invalid. |
| `UnsupportedVersion` | The storage format version is not 3. |
| `CorruptArtifact` | Stored bytes do not match their digest or length. A missing file raises `FileNotFoundError`. |
| `OperationConflict` | An operation ID names a different request, reservation, or completion, or the request context differs from the project. |
| `BudgetExceeded` | A recorded breach or negative project total blocks a reservation or dispatch in this scope, or a reservation exceeds the remaining project allowance or scope cap. |
| `UnknownOutcome` | `begin` is refused because an unknown operation blocks the operation's scope. |

Other invalid input raises `TypeError` or `ValueError`. File and database errors
propagate to the caller.

## Publication and transactions

The writer owns each transaction and sets transaction behavior explicitly
(see the [Python 3.12 sqlite3 transaction guidance](https://docs.python.org/3.12/library/sqlite3.html#transaction-control)).
Foreign keys are enabled. Database durability settings are left at their defaults.

`record` and `complete` follow this order:

1. Validate the session, origin, references, and any superseded observation.
2. Write each new artifact to a temporary file and close it.
3. Publish each complete file without replacing an existing digest path.
4. Commit the observation and all references (and, for `complete`, the
   completion) in one database transaction.
5. Return only after the commit succeeds.

If a digest path already exists, its contents are checked before reuse. A mismatch
is corruption and the path is never overwritten. A new capture whose bytes match a
committed artifact that is now missing or corrupt fails rather than silently
repairing it. A failed file or database write propagates and preserves earlier
evidence. Unreferenced files from an interrupted attempt are inert; there is no
cleanup yet.

`create` publishes initial snapshot files before committing the schema and project
metadata together. `open` rejects an incompletely initialised project and does not
guess how to finish it. `create` refuses to overwrite that directory on retry.

History identifies recorded facts even when a referenced file later disappears.
Returned metadata does not certify availability. Call `read_artifact` before
treating bytes as usable evidence. A missing or corrupt artifact never becomes an
empty byte string or an inferred successful result.

## Operation identity and recovery

A `Request` binds an `Origin` to the complete immutable `Project` context: snapshot
versions, source labels, environment, world lineage, fixture, run, and allowances.
All context fields participate in equality; sessions do not. A changed context
requires a new project, even when the resulting bytes match. Supply the exact
requested context, not one copied from a cached receipt after inputs change. The
host is responsible for capturing every execution input; there is no general
command runner or argument parser.

The operations table stores one row per operation ID. Reservation fields stay
fixed. Dispatch and completion fields each move from absent to present once.
Original session and dispatch times remain visible after a later session records
the result.

| Method | Contract |
| --- | --- |
| `reserve(session_id, request, reservation, scope=ROOT_SCOPE)` | Reserve nonnegative integer amounts in declared units, in one scope, before execution. An identical repeat returns the existing operation. A changed identity, reservation, or scope raises `OperationConflict`. Unknown units or scopes, a blocking breach, or insufficient project or scope balance fail. Never dispatches. |
| `begin(session_id, request)` | Commit a dispatch marker, then return `True`; only this return permits execution. Return `False` for unknown or completed work. Unreserved work raises `ValueError`. An unknown operation in the same scope or the root scope raises `UnknownOutcome`; a breach there, or a negative project total, raises `BudgetExceeded`. |
| `complete(session_id, request, result, raw)` | Require a prior dispatch marker and non-empty raw bytes. Publish bytes, then commit one observation and the completion together. Settle usage once. |
| `lookup(request)` | Check identity and that input, snapshot, and result bytes are intact. Return the operation, or `None` when the ID has no reservation. |
| `operations()` | Metadata only; does not certify artifact availability. |
| `accounting(scope=None)` | Each unit's `limit`, `spent`, `reserved` (unresolved reservations), and `available`, for the project or for one scope. A scope's limit is its cap, or the project allowance for an uncapped unit. Does not read artifacts. |

| `Operation.state` | Meaning |
| --- | --- |
| `pending` | Reserved, no dispatch marker. |
| `unknown` | Dispatch marker committed; execution may have started. The marker does not assert that a process launched. |
| `completed` | Completion receipt committed. |

A crash after `begin` commits but before execution leaves the operation unknown,
because repeating execution is unsafe. Opening a project starts no work and
changes no operation state. A host can explicitly call `begin` for known pending
work after a restart. An unknown operation keeps its reservation. There is no
cancel, release, or automatic retry. A host can settle unknown work only from an
attributable captured result; an operator's guess or a new session is not such
evidence.

### Results and usage

| `Outcome` | Constraint |
| --- | --- |
| `SUCCEEDED` | `exit_code == 0`. |
| `FAILED` | Nonzero integer exit code, including a negative signal code. |
| `INFRASTRUCTURE_FAILURE` | `exit_code is None`; a known failure without a process exit code. |

An uncertain outcome must stay unknown (no completion). Outcomes describe
execution, not task acceptance.

Actual usage must include every reserved unit, with explicit zeros. Completion
releases the whole reservation and adds actual usage once. Usage above the
reservation, including units with no allowance, is retained and named in
`Completion.breaches`. Balances can be negative; nothing is clipped. Totals derive
from committed receipts; there is no separate mutable total.

A recorded breach blocks new reservations and pending dispatches in its own scope,
and in every scope when it is in the root scope. Existing unknown operations can
still record their results. Completed requests remain readable. An
identical repeat completion (same result, elapsed time, and named raw bytes)
returns the original and changes nothing. A conflicting completion fails and
preserves the original evidence and charge.

## Scopes

A scope is a host-created subdivision of the project, normally one run. Every
operation belongs to exactly one scope, fixed at reservation. Work outside any run
uses `ROOT_SCOPE`, which behaves as the whole project did before scopes existed.

`open_scope(session_id, scope_id, caps)` records a scope once with immutable
per-unit caps. Repeating it with the same caps is a no-op; different caps raise
`OperationConflict`. Caps must name project units; `ROOT_SCOPE` is reserved.

- **Budgets.** A reservation must fit the scope's remaining cap and the project's
  remaining allowance. Caps are ceilings, not set-asides: caps may sum above the
  project total, and the project total still bounds every reservation.
- **Blocking.** `begin` refuses dispatch while the operation's own scope or the
  root scope has an unknown operation or a recorded breach, or while the project
  total is negative. An unknown operation or breach in one run's scope does not
  block another run.
- **Reservations.** An unknown operation keeps its reservation, counted in its
  scope and in the project total, until an attributable result settles it.

Scoping the block is safe only when runs share no external state through an
operation whose outcome is unknown. An operation that writes to a shared external
resource belongs in the root scope. The design and its alternatives are in the
[run scopes note](../proposals/m7-run-scopes.md).

## Permitted exports

`export_evidence(ledger, destination, *, snapshots=(), observations=None, operations=())`
writes an inspection copy to a new directory and returns the path of its
`index.json`. The trusted host supplies explicit selections; every selection
defaults to empty.

```python
from pathlib import Path

from warranted.exports import export_evidence


def export_result(ledger, observation):
    return export_evidence(
        ledger,
        Path("inspection-copy"),
        snapshots=("input.csv", "contract.md"),
        observations={observation.sequence: ("stdout", "normalized.csv")},
        operations=("transform-1",),
    )
```

Snapshot and operation selections are tuples of exact names. Observation
selections map sequence numbers to tuples of channel names. Unknown records or
channels, duplicate names, malformed selections, and an existing destination fail
before anything is written. Artifact filenames come only from checked digests.

| Selection | Exported fields |
| --- | --- |
| Snapshot | Name, version, digest, byte length. Source labels are excluded. |
| Observation | Sequence, original session and time, selected raw references, operation ID, kind, producer/version, selected input references. `supersedes` appears only when that observation is also selected (or is absent). |
| Operation | Origin fields above, reservation, original session/time, dispatch session/time, state, and any result with usage, elapsed time, and breached units. The completion's observation sequence appears only when that observation is selected. |

The index also contains the project ID. Environment fields, fixture/run/world
labels, complete request contexts, unselected input references, and unselected
channel names and bytes are excluded. Selecting an operation does not select its
raw evidence. An input link to an observation channel appears only when that exact
channel is selected, so selecting a derived result does not disclose its inputs.
No project-wide totals are exported, because they can disclose unselected work;
the host can inspect them with `ledger.accounting()`.

Approving a field or channel approves its complete value or bytes. The function
does not scrub secrets in approved text. Give the consumer only the directory,
never the `Ledger`, database connection, run directory, or authoritative path.
Selected bytes can equal an unselected artifact's bytes; that grants none of the
unselected record's name, provenance, or fields.

`index.json` declares format `warranted-evidence-export` version 1 with
`"authoritative": false` and `"partial": true`. Missing fields mean omitted
information, not negative evidence. An exported completion reports recorded
state; it does not assert that all evidence was selected, certify acceptance, or
act as an acceptance receipt. There is no import path, and editing an export
cannot change the ledger or settle usage.

The writer copies checked bytes into `artifacts/<digest>` (never links), then
atomically publishes `index.json`. A failed copy, including a damaged selected
artifact, raises and removes the directory. A killed process can leave files
without an index; consumers must treat such a directory as incomplete and ignore
it. The destination must be separate from authoritative storage, including through
existing symlink aliases, and its parent must remain under trusted host control
during publication.

## Guarantees and limits

- Durability covers process termination on a working local filesystem. It does not
  cover power loss, disk loss, or a hostile process that can write to
  authoritative storage.
- One trusted, serialized writer is a deployment requirement, including during
  export. Concurrent writers are not supported.
- The operation methods enforce the protocol for trusted host code. They do not
  prevent arbitrary shell execution or direct database writes. `record` cannot
  create a completion, even when raw text claims success.
- Keep the authoritative project outside any untrusted worker's writable
  workspace. Worker containment is described in
  [worker and containment](worker-and-containment.md).
- History and accounting scans suit small fixtures; larger histories need indexes.

## Tests

Interruption tests run a child process, terminate it at a known boundary with
explicit synchronization, and reopen from a fresh process; they do not rely on
sleeps or exception-only crash simulation.

| Test file | Observable guarantees |
| --- | --- |
| [`tests/test_ledger.py`](../../tests/test_ledger.py) | Fresh-process reopen recovers project, sessions, order, origins, and bytes. Duplicate bytes keep separate origins; corrections keep the old observation. Termination during publication and around the commit exposes all or nothing. Invalid sessions, references, digests, and superseded records add no history. Missing or corrupt bytes fail and are never replaced. Incomplete or unsupported projects fail without repair. Storage errors propagate without partial records. Termination before and after reservation, dispatch, execution, publication, evidence insertion, and completion commit recovers either the unresolved reservation or the entire completion, with a separate execution counter. Reuse, conflicting identities and receipts, multiple units, and persistent breaches. |
| [`tests/test_scopes.py`](../../tests/test_scopes.py) | Scope caps under the project total, caps that sum above it, unknown operations and breaches blocking only their own scope across restart, root-scope blocking of every scope, `begin` refusing without a preflight, immutable scopes and operation binding, and invalid or unopened scopes. |
| [`tests/test_exports.py`](../../tests/test_exports.py) | Disclosure limits, indirect references, copy independence, damaged selected bytes, destination overlap, write failure, unknown operations, and termination before and after index publication. |
| [`tests/test_walkthrough.py`](../../tests/test_walkthrough.py) | The CSV walkthrough in separate processes keeps the operation's original evidence across resume. |

```bash
uv run --locked pytest -q tests/test_ledger.py tests/test_scopes.py tests/test_exports.py
uv run --locked pytest -q tests
```
