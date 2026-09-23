# M5 contexts and recovery treatments

M5 slice 4 connects the [A–E policy](pilot.md#knowledge-workflow-conditions) to
both complete recovery fixtures. `examples/m5/treatments.py` runs a fixed worker
by default. An explicit `--local-model` option uses the existing Ollama adapter.
Both modes check the first submission, commit the approved revision, and can kill
the host. A fresh process rejects the stale receipt and old candidate, restores
the worker's files, and checks a correction. A second resume reuses completed work.

The fixed mode is an offline integration run. The opt-in local mode records raw
HTTP requests and responses, token usage, the model configuration, the installed
model digest, and Ollama metadata. It sends the same worker prompt and commands
through the existing rootless container and independent task checkers.
Neither mode alone measures learning or a benefit from any treatment.

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

Workers save ordinary files and prose notes under `/work/workspace/`. Submission
captures that directory after all worker processes stop. `Episode.workspace`
binds the resulting evidence to the next episode, where those files are writable
again. The snapshot preserves relative paths and file bytes. It accepts at most
128 regular files, 16 path components, and 1 MiB of file content. Links, special
files, path traversal, and file/directory conflicts fail. Restoration writes only
inside the container, as the worker UID, without additional capabilities.
Empty directories and file modes are not retained; invoke saved programs through
their interpreter. Files outside this directory are scratch files unless captured
as `result.json`. This bounded convention applies equally to A–E.

`submitted_files()` resolves the exact submission and workspace from a completed
episode. `submitted_candidate()` selects its `result.json`. Candidate parsing, revision
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
| E | Scheduled proof proposals, verification, and scoped results | E |

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

The treatment runner adds the exact files disclosed to the prior episode, then
the current revision and checker feedback. This completes the permitted history
for these two-episode runs. It never includes private grading inputs or a general
ledger export. History remains a local JSONL file that workers can query with
ordinary Python or shell commands.

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

## Treatment behavior

All conditions use the same model and request settings, two worker episodes with
up to four shell actions each, shell and file permissions, available proof verifier, and
host caps. Current task input,
revision feedback, and ordinary workspace are available in A. B adds history.
C adds `model.py` and `regression.py`; their pinned source changes with the approved
revision. The fixed worker runs the public regression when present. These models
are executable development aids, not private grading references.

D records the initial and current model dependencies through `Claims`. The old
model becomes stale after revision, while the replacement is current. Both remain
unproved assertions: a current dependency does not establish model correctness.

E verifies the fixed Lean proposals and runs the [supported proof cases](m5-proofs.md)
before the forced kill. CSV uses the uniqueness and timestamp theorems; migration
uses its renaming theorem after the revision is approved. Their receipts survive
restart. The resumed worker receives the proof sources and scoped results. The host checks
the final candidate's correspondence separately and reports `qualified` only when
task acceptance and all required E support pass. This report does not replace or
weaken the independent task acceptance receipt. A failed proof stops the workflow
and remains in the ledger with its cost.

The historical proof-case matrix preserves record loss, timestamp mistranslation,
and the dropped legacy label as task failures despite supported narrow proofs.
The timestamp counterexample uses the initial one-hour contract; the later
approved zero-offset revision does not rewrite that historical decision.

Every condition can submit an optional proof source under `workspace/`, using the
filenames in `tools.md`. The host checks it at submission through the same pinned
verifier, records its result and cost, and returns initial results on resume even
in A. Approved target definitions are equally available in `tool-targets.json`.
The migration target is released only after the revision, because it mentions
current-version inputs. The fixed baseline workers do not request proofs; a
separate fast test exercises this capability.

Caps per run are 8 model attempts, 8 shell attempts, 6 proof attempts, 24 synthetic
checks, 4 migration batches, and 5 migration task checks. Reports retain each unit
separately, measured operation time, and the bundle's recorded build time.
Scripted E spends two proof units for CSV and one for migration. A–D spend none
unless the worker requests a proof. Units are not money and cannot be summed as
a monetary cost. Human fixture development and formalization costs are not measured.

The four-command phase limit leaves one command to inspect task inputs together,
then three commands to produce, test, and submit the result. The final command
must print the submission marker. Fixed responses still submit in one command.
All conditions have the same limit.

The prompt names `task.md`, `context.json`, `tools.md`, and
`repository-files.json` when present so the first command can inspect them
together. This prompt change needs a fresh run; prior ledgers pin the old source.

## Run and verify

Prepare rootless Podman and a pinned bundle as in the
[verification guide](m4-verification.md), then use a fresh directory for each
family and condition:

```bash
uv run --locked python examples/m5/treatments.py start runs/csv-E --family csv --condition E --bundle runs/m4-tools/bundle.json --crash
uv run --locked python examples/m5/treatments.py resume runs/csv-E --family csv --condition E --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m5/treatments.py resume runs/csv-E --family csv --condition E --bundle runs/m4-tools/bundle.json
```

The crash command exits with shell status 137 after writing its report. Use
`--family migration` for the second fixture and `--condition A` through `E` for
the treatments. Family, condition, fixture, host, and bundle bytes cannot change
within a run. Unknown outcomes retain reservations and block new work. A missing
treatment checkpoint also blocks resume before further dispatch; this fixed
driver does not reconstruct a partially prepared checkpoint.

Fast tests exercise all ten combinations with scripted external boundaries and
the real task checkers. Native tests run E on both families through a kill and
two fresh resumes. Existing native tests cover baseline recovery and A/E file
disclosure; the new CI cases do not repeat those baseline runs.
CI runs the three proof-case targets inside E instead of
repeating the standalone M5 proof demonstration. Existing M4 tests retain the
incomplete-target and failed-proof controls.

```bash
uv run --locked pytest -q -m 'not proof' tests/test_m5_treatments.py
WARRANTED_PROOF_TESTS=1 WARRANTED_PROOF_BUNDLE=runs/m4-tools/bundle.json \
  uv run --locked pytest -q -m proof --durations=5 tests/test_m5_treatments.py
```

Reports and exports contain private development references for host inspection.
They must not become worker input. Frontier/small-model comparisons, held-out
tasks, threshold selection, and campaign cost reporting remain slice 5.

## Local-model development runs

Initialize a development plan with one local model:

```bash
uv run --locked python examples/m5/trials.py init runs/m5-qwen-ae \
  --bundle runs/m4-tools/bundle.json --repetitions 1 \
  --local-model qwen3.8:27b
```

The plan pins the model request settings and installed model digest. It prepares
ten ledgers for the two fixed task families and conditions A through E. It does
not send model requests. Each run command must use the same model and settings:

```bash
uv run --locked python examples/m5/treatments.py start \
  runs/m5-qwen-ae/runs/001-migration-A --family migration --condition A \
  --bundle runs/m4-tools/bundle.json --local-model qwen3.8:27b --crash
uv run --locked python examples/m5/treatments.py resume \
  runs/m5-qwen-ae/runs/001-migration-A --family migration --condition A \
  --bundle runs/m4-tools/bundle.json --local-model qwen3.8:27b
uv run --locked python examples/m5/trials.py report runs/m5-qwen-ae
```

Repeat the start and resume commands for every path in the plan. The start
command exits when it kills the host after the approved checkpoint. Local runs
pin the model and runtime before each new inference request. Changed or missing
metadata records an infrastructure failure with zero model usage and releases
the reservation. Repeated calls reuse that failure without polling Ollama.
Each live episode budgets its container lifetime for four inference request
deadlines, three metadata request deadlines per turn, and all bounded Podman calls.
The host enforces each deadline across connection, headers, and body reads. The report sums
recorded prompt and completion tokens across live-model runs. Fixed scripted
runs keep token totals unreported.
Failures before inference contribute known zero tokens. Token totals can be
complete for an unfinished task when every recorded model operation has known
token usage.
One repetition of these two fixtures is development evidence, not a set of
independent or held-out tasks.

## First Qwen development attempt

On 2026-09-23, one local `qwen3.8:27b` migration-A run began under a pinned
development plan at `runs/m5-qwen-dev-20260923`. Ollama 0.34.2 reported
Q4_K_M model digest `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`.
The plan digest is `dc00f8acf4bf10d34be813baf3b5346a40d364080bdfc70227c11645d3d7e40c`;
its host code came from merge commit `a806b0f`.
The run used seed 0, a 1,536-token output limit, a 180-second request deadline,
and the four-command phase limit. The first episode made four successful model
requests and four successful shell calls. Its generated program passed the
public example and the model then ran more local checks, but it never submitted
`result.json`. The host stopped at the phase limit before the revision checkpoint.
No independent task check ran, so this is an unfinished task, not a task success.

The ledger records 5,214 prompt tokens, 1,349 completion tokens, 6,563 total
tokens, four spent model units, four spent tool units, and no reservations. The
sum of recorded model durations is 76.5 seconds and the sum of tool durations
is 35.1 seconds. These are operation times, not complete wall time or money.
The other nine prepared slots were not run, so the campaign is incomplete.
This attempt is development data and cannot become held-out evidence.

The next development check uses a new plan and the clearer first-command
instruction above. Keep the same model, task, evaluator, seed, request limits,
four-command limit, host caps, and shell/file capabilities across A–E. Require
both episodes to submit candidates, the approved revision and forced restart to
recover, and a repeated resume to add no completed work. Count a missing
submission as failure even if public examples pass. Report independent task
checks, proof support, stale reuse, unknown attempts, tokens, spent units, and
setup costs separately. Start the ten-slot development matrix only after this
single-run recovery check; its public fixtures remain development data. A later
comparison needs separate task lineages, untouched final instances, repeated
runs, a fixed usefulness threshold, and a spending cap for any paid model.
