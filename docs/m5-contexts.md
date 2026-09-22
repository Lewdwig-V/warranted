# M5 context boundary

This is the first part of M5 slice 4. The shared worker boundary now accepts
explicit files from recorded observations as well as initial snapshots.
Both the CSV and migration runners use one function to find a captured submission.
The [A–E policy](pilot.md#knowledge-workflow-conditions) selects which recorded
files the host prepares for a worker episode.

The implementation covers file disclosure and exact episode identity. It does
not yet run the complete A–E comparison. The existing recovery demonstrations
retain their fixed context policies and unchanged task checkers.

## Shared responsibilities

`Episode.inputs` selects initial snapshots by name. `Episode.files` maps a
worker filename to an `Evidence` reference, which identifies exact recorded bytes.
This permits a later episode to receive prior candidate files, notes, or a
prepared history. Both paths bind their source evidence into the episode record.
The host rejects conflicting names or changed files under the same episode ID.
The attachment mapping cannot change after episode creation.

`Sandbox` copies the selected bytes into its existing container boundary.
It accepts only safe filenames and applies the same total input size limit.
The worker receives no ledger, checkpoint database, or host directory mount.
Its `context.json` contains a file inventory, without host provenance or project metadata.
The copied inputs remain read-only. General evidence exports remain available
for separate host inspection.

`submitted_candidate()` finds one exact `candidate/result.json` capture for an
episode. Both working fixtures use this function. Candidate parsing, revision
approval, execution, and acceptance remain in their existing fixture adapters.
These shared functions do not define a universal task or checker interface.

## Selecting support

The run manifest pins `environment["condition"]` to `A`, `B`, `C`, `D`, or `E`.
`capture_context()` takes five explicit maps from the trusted host:

| Layer | Host-selected files | Conditions that receive them |
| --- | --- | --- |
| A | Ordinary workspace and recorded prose notes | A–E |
| B | Permitted observation and action history | B–E |
| C | Executable models and regression checks | C–E |
| D | Dependency and current applicability reports | D–E |
| E | Formal targets, proof work, and its scoped results | E |

An unknown condition fails. No layer is inferred from arbitrary ledger records
or worker-authored labels. The host must classify each disclosure correctly.
The policy does not detect secrets hidden inside an otherwise permitted file.
Changing a published context under the same episode ID fails rather than
silently substituting new support during recovery.

`capture_history()` records the model and shell exchanges from explicitly named,
completed episodes. It includes request payloads and the worker response channels.
Response bytes use base64 so malformed UTF-8 remains intact. Each JSON line can
be inspected with ordinary shell or Python tools. Private checker observations,
operation metadata, and dependency edges are excluded.
Missing or unfinished episodes cannot become an empty successful history.

The caller supplies all permitted prior episodes in order. This function does
not reconstruct missing events or permit live execution during inspection.
The trial runner must keep each condition and its history separate. It must not
copy an E episode into a B run or expose a future revision before its checkpoint.

For a prepared selection, attach the returned files to the existing worker API:

```python
from warranted.contexts import capture_context
from warranted.sandbox import SANDBOX_ID
from warranted.worker import Episode

files = capture_context(ledger, session, "revised", layers)
episode = Episode(
    "revised",
    "Inspect the permitted files and submit a candidate.",
    (),
    files=files,
    environment=SANDBOX_ID,
    continues="initial",
)
```

`layers` contains the five maps in the table. Each file value is an exact evidence
reference. Supply this episode to `run_workflow()` and the existing `Sandbox`.
Worker-visible reports remain copies. They cannot authorize acceptance or replace
the original receipt used by the host.

## Demonstrated scope

The fast matrix uses snapshots from both real fixtures and the real worker journal.
It covers all five conditions before and after a fixture-selected input revision.
Private references and unreleased revision text stay outside the selected files.
The tests retain an old candidate, preserve binary diagnostics in history, expose
a stale dependency in D/E, and reuse completed worker attempts after reopening.

The E visibility fixture deliberately reports zero proof attempts and an unproved
status. Receiving a proof-work file does not establish formal support.
The [separate supported-proof demonstration](m5-proofs.md) supplies the real Lean
verification and acceptance evidence. The two demonstrations are not yet one E run.

Native tests exercise the minimal A selection and the full E selection with
actual containers. They compare every received file and check host-state isolation.
These are disclosure tests, not task-success or model-quality measurements.

```bash
uv run --locked pytest -q -m 'not container' tests/test_contexts.py tests/test_m5_contexts.py
WARRANTED_CONTAINER_TESTS=1 uv run --locked pytest -q -m container tests/test_m5_contexts.py
```

The remaining part of slice 4 must connect these selections to both full recovery
runs. It must preserve ordinary workspace and notes, publish complete permitted
history, maintain models and dependency reports, and charge E for its additional
proof work. All conditions need equal worker capabilities and the same independent
task-success checks. Measured model trials remain slice 5.
