# M1 PR1: evidence persistence

Status: proposed implementation plan. No runtime behavior in this document exists
yet. The [scripted walkthrough](pilot.md#m1-scripted-walkthrough) defines the
observable requirements. This document selects the first records and host API.

PR1 stores raw evidence, preserves its origin, and recovers it in a fresh process.
PR2 adds operation identity enforcement, reservations, and completion receipts.
PR3 adds permitted exports and the complete scripted walkthrough.

## Records and files

Use one concrete `Ledger` class in `src/warranted/ledger.py`. Use Python's standard
library for SQLite, serialization, identifiers, hashing, and files. Add no runtime
dependency. Add pytest through uv when implementation starts.

The project directory contains `ledger.sqlite3` and `artifacts/sha256/`.
An artifact is a stored sequence of exact bytes. Name each artifact file by its
SHA-256 digest. Temporary files belong on the same filesystem as final artifacts.
The script's working directory remains separate from this authoritative directory.

Use three tables. Keep identity, ordering, and session references in columns.
Store the typed manifest, origin, and artifact references as versioned JSON where
they contain several fields. Validate their structure before writing or using them.

| Record | Required fields | Reason |
| --- | --- | --- |
| Project | Project ID, storage format version, creation time, immutable manifest with named snapshot references | Identify the project and the exact starting context after reopening. |
| Session | Session ID, start time | Separate process sessions without discarding earlier history. Each project database owns its sessions. |
| Observation | Committed sequence, session ID, capture time, origin, named artifact references, optional superseded observation sequence | Preserve one capture and its lineage. Order history without relying on wall-clock time. |

An `ArtifactRef` contains the digest and byte length. The writer derives both
values from the bytes. Artifact identity does not replace observation identity:
two captures can share bytes while retaining separate origins and times.
The observation sequence is stable within its project. It need not be gapless.

The immutable manifest records the fixture and run identities, root world identity,
explicit absence of a parent, environment, declared allowances, and named snapshots.
Snapshots include the input, source contract, and transformation source with their
versions and origin labels. Use the labels to explain where the host obtained them.
Labels alone do not establish byte identity.

For PR1, all sessions reuse this one fixed context. The manifest records the
[Dream-RSI context requirements](design.md#dream-rsi-exploration-and-replay)
without adding a world registry or branch executor. Model configuration is not
applicable. PR2 enforces allowances and records actual usage.

An `Origin` contains the host's operation ID and kind, producer name and version,
and the exact named input snapshot references. It refers to the project's manifest
for the environment and context. Validate that referenced snapshots exist and
match their recorded bytes. PR1 records this source description. PR2 binds an
operation ID to one request identity and rejects conflicting reuse.

Store each capture's raw channels separately. For a script result, these include
standard output, standard error, and each output file. Store exit status and other
host-captured result metadata in a named JSON artifact. Raw text never becomes a
structured result merely because it contains words such as `accepted` or `success`.

## Host interface

Use ordinary methods on the concrete ledger. These are Python interfaces for
trusted host code, not worker tools or a mandatory sequence of model actions.
The names and parameter shapes remain provisional until implementation tests them.

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

These methods grant host access. They do not implement worker isolation or the
permission filter for exports. PR3 must restrict exported fields and files.
M3 must enforce the process boundary before an untrusted worker receives access.

## Publication and transaction contract

The writer owns each transaction. Keep the insertion steps private so that PR2
can insert evidence, completion, and accounting in one transaction. PR2 must not
call a method that commits evidence and then settle usage in a separate commit.

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
versions and report them explicitly. Automatic migrations are outside PR1.

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

Run the focused tests through uv and all documented lint, entry-point, and build
checks before proposing the implementation merge. Update `pyproject.toml` and
`uv.lock` together when pytest is added. CI must execute the real tests.

PR1 is complete when these tests establish evidence persistence and the README
describes that limited behavior accurately. Operation receipts, accounting, task
execution, permitted exports, and the full walkthrough remain later PRs. Keep
the M1 milestone open until its complete acceptance cases pass.
