# M1: evidence persistence and operation recovery

Status: PR1, PR2, and PR3 are implemented with local tests. The
[scripted walkthrough](pilot.md#m1-scripted-walkthrough) defines the full M1
requirements. This document describes the first records and provisional host API.

PR1 stores raw evidence, preserves its origin, and recovers it in a fresh process.
PR2 adds operation identity enforcement, reservations, and completion receipts.
PR3 adds permitted exports and the complete scripted walkthrough.

## Records and files

Use one concrete `Ledger` class in `src/warranted/ledger.py`. Use Python's standard
library for SQLite, serialization, identifiers, hashing, and files. There are no
runtime dependencies. pytest is a development dependency for the failure cases.

The project directory contains `ledger.sqlite3` and `artifacts/sha256/`.
An artifact is a stored sequence of exact bytes. Name each artifact file by its
SHA-256 digest. Temporary files belong on the same filesystem as final artifacts.
The script's working directory remains separate from this authoritative directory.

Use four tables. Keep identity, ordering, and session references in columns.
Store the typed manifest, origin, and artifact references as versioned JSON where
they contain several fields. Validate their structure before writing or using them.

| Record | Required fields | Reason |
| --- | --- | --- |
| Project | Project ID, storage format version, creation time, immutable manifest with named snapshot references | Identify the project and the exact starting context after reopening. |
| Session | Session ID, start time | Separate process sessions without discarding earlier history. Each project database owns its sessions. |
| Observation | Committed sequence, session ID, capture time, origin, named artifact references, optional superseded observation sequence | Preserve one capture and its lineage. Order history without relying on wall-clock time. |
| Operation | Operation ID, request, reservation, original session/time, optional dispatch session/time, optional completion reference/result/breaches | Preserve identity, uncertain execution, and settled usage. |

An `ArtifactRef` contains the digest and byte length. The writer derives both
values from the bytes. Artifact identity does not replace observation identity:
two captures can share bytes while retaining separate origins and times.
The observation sequence is stable within its project. It need not be gapless.

The immutable manifest records the fixture and run identities, root world identity,
explicit absence of a parent, environment, declared allowances, and named snapshots.
Snapshots include the input, source contract, and transformation source with their
versions and origin labels. Use the labels to explain where the host obtained them.
Labels alone do not establish byte identity.

All sessions reuse this one fixed context. The manifest records the
[Dream-RSI context requirements](design.md#dream-rsi-exploration-and-replay)
without adding a world registry or branch executor. Model configuration is not
applicable. PR2 enforces allowances and records actual usage.

An `Origin` contains the host's operation ID and kind, producer name and version,
and exact input references. An input name selects a project snapshot or a committed
channel through `observation/<sequence>/<channel>`. Snapshot names take precedence
if both forms match. Channel names remain labels, not filesystem paths.
The host checks the selected record, digest, length, and bytes before use.
This M2 extension lets one operation consume another operation's output.
It adds no tables or fields to storage format 2. Older readers cannot use the new
input names, and existing snapshot references retain their meaning.
The origin refers to the project's manifest for the environment and context.
PR1 records this source description. PR2 binds an
operation ID to one request identity and rejects conflicting reuse.

Store each capture's raw channels separately. For a script result, these include
standard output, standard error, and each output file. The operation receipt stores
exit status, usage, and elapsed time as typed fields. Other result metadata can use
a named JSON artifact. Raw text never becomes a
structured result merely because it contains words such as `accepted` or `success`.

## Host interface

Use ordinary methods on the concrete ledger. These are Python interfaces for
trusted host code, not worker tools or a mandatory sequence of model actions.
The names and parameter shapes remain provisional until a second use case tests them.

`Manifest` records string environment entries and integer allowances by unit.
`Snapshot` takes raw bytes, an origin label, and a version. Creation returns
`SnapshotRef` values in the project's immutable snapshot mapping. `Origin` takes
the operation ID and kind, producer name and version, and named input references.
See the [runnable example](../README.md#local-evidence-api).

| Method | Contract |
| --- | --- |
| `Ledger.create(root, manifest, snapshots)` | Create a new project from typed manifest data and named raw snapshot bytes with origin labels. Compute references internally. Refuse an existing path. |
| `Ledger.open(root)` | Open an existing project. Require a complete, supported schema and valid project metadata. Never create or repair a project as a side effect. |
| `ledger.start_session()` | Persist and return a new session ID under the same fixed manifest. Opening or reading alone does not create a session. |
| `ledger.record(session_id, origin, raw, supersedes=None)` | Publish named raw byte artifacts. Commit one observation and all its references together. Return the committed observation. |
| `ledger.history(after=0)` | Return observations in committed sequence order, with their origins and artifact references. Do not load all artifact contents into memory. |
| `ledger.read_artifact(ref)` | Read exact bytes and make sure that their digest and length match. Missing or corrupt bytes are an explicit failure. |

Expose the immutable project record through `ledger.project`. Provide
`ledger.sessions()` to read session records, including sessions with no observations.
`Ledger` also supports a context manager that closes its connection. Define small
immutable Python value types for the manifest, origin, references, and returned
observations. Do not expose arbitrary SQL or a generic event-kind/payload writer.

Reject an unknown session, malformed reference, or nonexistent superseded
observation before committing. A correction records a new observation that points
to the old one. The API has no method to update or delete an old observation.
Reject caller-selected paths for artifact storage. Derive storage paths solely
from validated digests.

`record` captures evidence. It does not mark an operation complete, enforce its
budget, deduplicate execution, or establish task acceptance. A repeated call
records another capture. Do not retry a write automatically after a lost response.
PR2 supplies the operation receipt needed to resolve that ambiguity.

These methods grant host access. They do not implement worker isolation.
The separate export function restricts exported fields and files.
M3 must enforce the process boundary before an untrusted worker receives access.

## Publication and transaction contract

The writer owns each transaction. Private insertion methods let `complete` store
evidence and completion together. Accounting derives from the committed operations.
There is no separate mutable total that can drift from the receipts.

For `record`, follow this order:

1. Validate the session, origin, references, and optional superseded observation.
2. Write each new artifact to a temporary file and close it after writing all bytes.
3. Publish each complete file without replacing an existing digest path.
4. Commit the observation and all its references in one database transaction.
5. Return the committed observation only after the commit succeeds.

If a digest path exists, make sure that its contents match before reusing it.
Treat a mismatch as corruption. Never overwrite that path to hide the mismatch.
If a file or database write fails, propagate the failure and preserve old evidence.
Unreferenced files from an interrupted attempt remain inert until cleanup exists.

Set transaction behavior explicitly. Python 3.12's SQLite connection context
manager handles commit or rollback but does not close the connection. Use the
[standard-library transaction guidance](https://docs.python.org/3.12/library/sqlite3.html#transaction-control)
when implementing connection ownership. Keep the database's normal durability
settings. No journal-mode tuning is required for this single-writer slice.

Initialize schema and project metadata together. Publish initial snapshot files
before committing their references too. If initialization is interrupted, `open`
must reject an incomplete project. It must not guess how to finish it.
`create` must not overwrite that directory on a retry. Detect unsupported storage
versions and report them explicitly. The current storage format is version 2.
Version 1 projects remain unchanged and require the PR1 reader. Automatic migration
is outside this slice.

History identifies recorded facts even when a referenced file later disappears.
Returning metadata does not certify the file's availability. Consumers must use
`read_artifact` before treating those bytes as usable evidence. A missing or corrupt
artifact never becomes an empty byte string or an inferred successful result.

The durability claim covers process termination on a working local filesystem.
It does not cover power loss, disk loss, or a hostile process that can write to
authoritative storage. One trusted writer remains a deployment requirement.

## Required tests and PR boundary

Write the negative cases before the behavior that resolves them. Use one focused
`tests/test_ledger.py` with temporary project directories. Tests must observe the
public methods and stored raw bytes, rather than assert private table layouts.

| Case | Observable result |
| --- | --- |
| Fresh-process reopen | A second process recovers the same project, original session, observation order, origins, and byte-for-byte artifacts. A new session preserves the old history. |
| Duplicate bytes and corrected observations | Two captures retain separate origins while reading the same bytes. A correction retains the old observation and records its superseded reference. |
| Interruption during file publication | Recovery exposes no partial observation. Earlier committed evidence remains readable. Temporary or unreferenced files add no history. |
| Interruption before, during, and after the record commit | Recovery exposes the entire observation or none of it. Every visible reference points to complete published bytes. |
| Invalid source or reference | Unknown sessions, missing input references, invalid digests, and nonexistent superseded records fail without adding history. |
| Missing or corrupt stored bytes | Reading the affected artifact fails explicitly. Other committed evidence remains readable. A later write cannot silently replace the corrupt bytes. |
| Incomplete project or unsupported version | Opening fails explicitly. It creates no replacement database, repairs no records, and leaves existing files intact. |
| File or database failure | The caller sees the storage error. A failed transaction adds no partial observation and preserves all earlier evidence. |

Use a child process and explicit synchronization for interruption tests. Terminate
it at a known boundary, then reopen from another process. Do not depend on timing
sleeps or exception-only simulations of a crash. Test instrumentation can pause
at private boundaries without becoming a production callback interface.

Run `uv run --locked pytest -q tests/test_ledger.py` for the focused ledger tests.
Run `uv run --locked pytest -q tests` for the complete M1 suite.
CI runs this suite on pull requests and main. Keep `pyproject.toml` and `uv.lock`
together when dependencies change. Run the documented lint, entry-point, and build
checks before proposing a merge.

PR1 and PR2 implement evidence persistence and operation accounting within this scope.
PR3 supplies permitted exports and the fixed CSV walkthrough. The complete M1
suite demonstrates this scope. A general runner and worker isolation remain later work.

## Operation identity and recovery

A `Request` binds an `Origin` to the complete immutable `Project` context.
This reuses the existing types for snapshot versions, source labels, environment,
world lineage, fixture, run, and allowances. All context fields participate in
equality. Sessions do not participate. Context changes require a new project in
M1, even when the resulting bytes match. M2 adds changed-premise handling.

Supply the exact requested context, not a context copied from a cached receipt
after the caller changes its inputs. The host remains responsible for capturing
every execution input. This API has no general command runner or argument parser.

The `operations` table stores one row per operation ID. Reservation fields stay
fixed. Dispatch and completion fields each move from absent to present once.
SQL foreign keys bind sessions and the completion observation. Original session
and dispatch times remain visible after a later session records the result.

| Method | Contract |
| --- | --- |
| `reserve(session_id, request, reservation)` | Reserve nonnegative integer amounts by declared unit before execution. Repeating an identical request and reservation returns its existing state. Reject changed identity or reservation. |
| `begin(session_id, request)` | Commit a dispatch marker before returning `True`. Only this return permits execution. Return `False` for unknown or completed work. Reject unreserved work or a budget breach. |
| `complete(session_id, request, result, raw)` | Publish raw bytes, then commit one observation and completion together. Settle usage exactly once. Require a prior dispatch marker. |
| `lookup(request)` | Compare identity and make sure that snapshot and result bytes remain intact. Return the operation, or `None` when the ID has no reservation. |
| `operations()` | Return operation metadata in reservation order, including pending and unknown work. This inspection does not certify artifact availability. |
| `accounting()` | Return each unit's limit, spent usage, unresolved reservations, and available balance. This inspection does not read artifact contents. |

An operation is `pending` before its dispatch marker, `unknown` after that marker,
and `completed` after its receipt. The marker means execution can have started.
It does not assert that the process launched. A crash after `begin` commits but
before execution leaves an unknown operation, because repeating execution is unsafe.
Opening a project starts no work and changes no operation state.

Repeated `reserve` calls never dispatch work. A host can explicitly call `begin`
for known pending work after restart. An unknown operation keeps its reservation.
There is no cancel, release, or automatic retry method. A host can settle an
unknown operation only from an attributable captured result. An operator's guess
or a new session supplies no such evidence. External receipt reconciliation
remains M3 work.

A `Result` records an `Outcome`, exit code, integer usage by unit, and elapsed
nanoseconds. `SUCCEEDED` requires exit code zero. `FAILED` requires a nonzero exit
code, including a negative signal code. `INFRASTRUCTURE_FAILURE` has no process
exit code and requires a known failure. An uncertain outcome must remain unknown.
These outcomes describe execution, not independent task acceptance.

Actual usage must include every reserved unit, including an explicit zero when
unused. Completion releases the entire reservation and adds actual usage once.
The ledger retains usage above the reservation, including unexpected units, and
records the affected units in `Completion.breaches`. Unexpected units have a zero
allowance. Available balances can be negative. No value is clipped to fit a limit.

A recorded breach blocks new reservations and pending dispatches. Existing
unknown operations can still record their results and usage. Repeated completed
requests remain readable. An identical completion, including elapsed time and
named raw bytes, changes nothing. Conflicting completions fail and preserve the
original evidence and charge.

The operation methods enforce this protocol for trusted host code. They do not
prevent arbitrary shell execution or direct writes to the database. `record`
cannot create a completion, even when raw text claims success. Worker isolation
and independent acceptance remain later milestones.

The tests force process termination before reservation commit, after reservation,
before and after dispatch commit, after execution, during publication, after
evidence insertion, and before and after completion commit. A separate file counts
executions. Recovery runs in a fresh process and observes either the unresolved
reservation or the entire completion. Other tests cover successful reuse, known
failure reuse, conflicting identities and receipts, missing bytes, transaction
failure, multiple allowance units, and persistent budget breaches.

## Permitted exports

`warranted.exports.export_evidence` writes an inspection copy to a new directory.
The trusted host supplies explicit selections. Every selection defaults to empty.

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

Snapshot and operation selections are tuples of exact names. Observation selections
map sequence numbers to tuples of raw channel names. Unknown records, unknown
channels, malformed selections, and existing destinations fail explicitly.
Names remain JSON labels. Artifact filenames come only from their checked digests.

The fixed format includes the generated project ID and these selected fields:

| Selection | Exported fields |
| --- | --- |
| Snapshot | Name, version, digest, and byte length. Source labels are excluded. |
| Observation | Sequence, original session and time, selected raw references, operation ID, kind, producer/version, and selected input references. A superseded sequence appears only when that observation is also selected. |
| Operation | Origin fields above, reservation, original session/time, dispatch session/time, state, and any result with usage, elapsed time, and breached units. A completion links only to a selected observation. |

Environment fields, fixture/run/world labels, complete request contexts, unselected
input references, and unselected channel names and bytes are excluded. Selecting
an operation does not implicitly select its raw evidence. No project-wide totals
are exported because those totals can disclose unselected work. The host can
inspect totals with `ledger.accounting()`.

An input link to an observation appears only when that exact source channel is
selected. Selecting a derived result does not disclose its unselected inputs.

Approving a field or raw channel approves its complete value or bytes. The function
does not scrub secrets embedded in approved text. The host must select disclosures
before providing the export to a consumer. A consumer receives only that directory,
not the `Ledger` object, database connection, run directory, or authoritative path.
Selected bytes can match an unselected artifact's bytes. That does not grant the
unselected record's name, provenance, or other fields.

`index.json` identifies export format version 1 and marks the view as partial and
non-authoritative. Missing fields mean omitted information, not negative evidence.
An exported completion reports recorded state. It does not assert that all evidence
was selected, certify task acceptance, or become a new acceptance receipt.
There is no export import path.

The writer copies checked artifact bytes into `artifacts/<digest>`. It creates no
links to authoritative files. After all copies finish, it atomically publishes
`index.json`. A failed copy reports its error and removes the incomplete directory.
A killed process can leave files without an index. Such a directory is incomplete.
Consumers must ignore it. Export edits cannot change the ledger or settle usage.

The destination must be separate from authoritative storage, including through
existing symlink aliases. Its parent must remain under trusted host control during
publication. M1 does not defend against concurrent hostile filesystem changes or
provide process isolation. Reuse the one-writer deployment rule during export.

The [export tests](../tests/test_exports.py) cover disclosure, indirect references,
copy independence, damaged selected bytes, destination overlap, write failure,
and forced termination before and after index publication. The
[walkthrough tests](../tests/test_walkthrough.py) run the documented CSV workflow
in separate processes and retain the operation's original evidence across resume.
