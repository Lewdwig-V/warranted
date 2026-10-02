# Contexts and treatments

`warranted.contexts` controls what a worker episode may see. The trusted host
assigns each file to one of five cumulative layers, A through E, and a run's
pinned condition decides which layers reach the worker. The selection is frozen
as evidence and bound to the episode. `examples/m5/treatments.py` applies this
boundary to both task families across an approved revision and a forced restart.
It runs either a fixed scripted worker or an opt-in live model. Neither mode, by
itself, measures learning or any benefit from a treatment. The evaluation design
and decision criteria are in
[evaluation design](../experiments/evaluation-design.md).

## Attaching evidence to an episode

| Field | Source | Notes |
| --- | --- | --- |
| `Episode.inputs` | Project snapshots by name | Initial public inputs |
| `Episode.files` | Worker filename → `Evidence` (exact recorded bytes) | Prior candidates, notes, prepared history, context layers |
| `Episode.workspace` | Captured `workspace.json` from a previous episode | Restored as writable files |

The episode record binds all of this source evidence. The host rejects
conflicting names, a filename that repeats a snapshot input, or changed files
under the same episode ID. Attachments cannot change after the episode is created.
`Sandbox` copies the selected bytes into the container as read-only files, using
the same safe-name rule and the 1 MiB total input limit. `context.json` lists
those files without host provenance or project metadata. The worker receives no
ledger, checkpoint database, or host mount. General evidence exports remain
available for separate host inspection (see
[container sandbox](worker-and-containment.md#container-sandbox)).

**Persistent workspace.** Workers keep ordinary files and prose notes under
`/work/workspace/`. On submission, this directory is captured after all worker
processes stop, with relative paths and bytes preserved (at most 128 regular
files, 16 path components, and 1 MiB). Links, special files, path traversal, and
file/directory conflicts fail the capture. Restoration writes only inside the new
container, as the worker UID. Empty directories and file modes are not kept, so
saved programs must be invoked through their interpreter. Files outside this
directory are scratch, except `result.json`. The same convention applies in every
condition.

`submitted_files()` and `submitted_candidate()` resolve the exact submission and
workspace of a completed episode. Candidate parsing, revision approval,
execution, and acceptance stay in each fixture's adapter. These helpers do not
define a universal task or checker interface.

## Selecting support

The run manifest pins `environment["condition"]` to `A`, `B`, `C`, `D`, or `E`.
`capture_context(ledger, session, episode_id, layers)` takes all five layer maps
from the trusted host and returns the frozen `{filename: Evidence}` for the
episode. It also records a `selection.json` with the condition and filenames.

| Layer | Host-selected files | Conditions that receive them |
| --- | --- | --- |
| A | Ordinary workspace and recorded prose notes | A–E |
| B | Permitted observation and action history | B–E |
| C | Executable models and regression checks | C–E |
| D | Dependency and current applicability reports | D–E |
| E | Scheduled proof proposals, verification, and scoped results | E |

The selection fails closed in these cases:

- An unknown or missing condition.
- A missing layer map.
- A duplicate filename or the reserved name `selection.json`.
- A value that is not `Evidence`.
- Changed context under the same episode ID. Recovery never silently substitutes
  new support.

No layer is inferred from arbitrary ledger records or from labels written by the
worker. The host must classify each disclosure correctly, and the policy does not
detect secrets hidden inside a file that is otherwise permitted.

`capture_history(ledger, session, episode_id, previous)` records the model and
shell exchanges of explicitly named, completed episodes as `history.jsonl`. Each
line holds a request payload and the worker-visible response channels, with bytes
base64-encoded so that malformed UTF-8 survives. Lines can be inspected with
ordinary shell or Python tools. Private checker observations, operation metadata,
and dependency edges are excluded. A missing or unfinished episode raises an
error rather than producing an empty history, and the function never reconstructs
events or runs live tools. The caller supplies all permitted prior episodes in
order. It must keep each condition's history separate, for example by never
copying an E episode into a B run or disclosing a future revision before its
checkpoint.

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

`layers` maps each `Condition` to `{filename: Evidence}`. Pass the episode to
`run_workflow()` together with a `Sandbox`. Reports visible to the worker are
copies. They cannot authorize acceptance or replace the original receipt that the
host uses.

## Treatment runner

`examples/m5/treatments.py` runs two worker episodes on the
[CSV](../fixtures/csv-transformation.md) or
[migration](../fixtures/config-migration.md) family. The first episode is checked,
the approved revision is committed, and the host can be killed. A fresh process
then rejects the stale receipt and the old candidate, restores the worker's
workspace, and checks a correction. A second `resume` reuses completed work.

All conditions use the same model and request settings, shell and file
permissions, proof verifier, step limit, and host caps.

| Condition | Adds to the worker's files |
| --- | --- |
| A | Current task inputs (CSV: `input.csv`, current offset, `task.md`; migration: `repository-files.json`, `task.md`, and `revision.txt` after revision), `tools.md`, and `tool-targets.json`. After the revision it also adds `previous-result.json`, `feedback.json` (the old candidate's checks and stale receipt), `proof-results.json`, and the restored workspace |
| B | `history.jsonl`: prior episode's exchanges plus the exact files disclosed to each episode |
| C | `model.py` and `regression.py`, pinned executable development aids that change with the approved revision (not private grading references) |
| D | `dependencies.json`: validation and applicability of claims for the initial and current models, recorded through `Claims`. After revision the old model is stale and the replacement is current. Both remain unproved assertions |
| E | `proof-work.json` and the fixed Lean proposals. The resumed worker receives proof sources and scoped results |

In E, the host verifies the fixed Lean proposals and runs the supported proof
cases before the forced kill. CSV uses the uniqueness and timestamp theorems, and
migration uses its renaming theorem once the revision is approved. Their receipts
survive restart. The host checks the final candidate's correspondence separately
and reports `qualified` only when task acceptance and all required E support
pass. This report does not replace or weaken the independent task acceptance
receipt. A failed proof stops the workflow and stays in the ledger with its cost.
The historical proof-case matrix keeps record loss, timestamp mistranslation, and
the dropped legacy label as task failures even though the narrow proofs are
supported. The timestamp counterexample uses the initial one-hour contract, and
the later zero-offset revision does not rewrite that decision. See
[proof verification](proof-verification.md).

**Optional proofs in every condition.** A worker may save
`workspace/uniqueness.lean` or `workspace/timestamp.lean` (CSV) or
`workspace/migration.lean` (migration, after the revision only). The host checks
each one once at submission with the same pinned verifier, charges one proof unit,
and returns the initial results on resume, even in A. `tool-targets.json` makes
the approved target definitions equally available. The migration target is
released only after the revision, because it mentions current-version inputs. The
fixed workers do not request proofs; a separate fast test exercises this path.

**Budgets.** Each run caps `model` and `tool` at twice `--max-steps` (8 each at
the default of 4), plus 6 proof, 24 synthetic-work, 4 migration batch, and 5
migration check units. Scripted E spends two proof units for CSV and one for
migration. A–D spend none unless the worker requests a proof. Reports keep each
unit separately, along with measured operation times and the bundle's recorded
build time. Units are not money and cannot be summed into a monetary cost. Human
fixture-development and formalization costs are not measured.

**Prompt.** The episode instruction states the command limit. It tells the worker
to inspect `task.md`, `context.json`, `tools.md`, `repository-files.json` (when
present), and the other inputs in the first command, then create, test, and
submit `result.json`. The final command must begin with
`printf '%s\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT;`. At the default limit, one
command inspects and three produce, test, and submit. The fixed responses submit
in one command. Changing the prompt requires a fresh run, because each ledger
pins the treatment source.

**Container lifetime.** Fixed runs use 120 seconds. Live runs use
`min(3600, steps × (request timeout + 3 × 10 s metadata) + (7 + 5 × steps) × 20 s + 60)`.
This covers each inference request and metadata deadline and every bounded Podman
call, so the workspace stays available across requests.

## Run and verify

Prepare rootless Podman and a pinned proof bundle as described in
[proof verification](proof-verification.md). Then use a fresh directory for each
family and condition:

```bash
uv run --locked python examples/m5/treatments.py start runs/csv-E --family csv --condition E --bundle runs/m4-tools/bundle.json --crash
uv run --locked python examples/m5/treatments.py resume runs/csv-E --family csv --condition E --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m5/treatments.py resume runs/csv-E --family csv --condition E --bundle runs/m4-tools/bundle.json
```

The `--crash` start exits with shell status 137 after writing its report. Use
`--family migration` for the second fixture and `--condition A` through `E` for
the treatments. Family, condition, step limit, fixture, host source, and bundle
bytes cannot change within a run. Unknown outcomes keep their reservations and
block new work. A missing treatment checkpoint also blocks `resume` before any
dispatch, because this driver does not reconstruct a partially prepared
checkpoint.

Fast tests run all ten family/condition combinations with scripted external
boundaries and the real task checkers. The native `proof` job runs E on both
families through a kill and two fresh resumes, including the three proof-case
targets. Other native tests cover baseline recovery and A/E file disclosure.

```bash
uv run --locked pytest -q -m 'not proof' tests/test_m5_treatments.py
WARRANTED_PROOF_TESTS=1 WARRANTED_PROOF_BUNDLE=runs/m4-tools/bundle.json \
  uv run --locked pytest -q -m proof --durations=5 tests/test_m5_treatments.py
```

Reports and exports contain private development references for inspection by the
host. They must not become worker input.

## Live models

`treatments.py`, `trials.py`, and `examples/m5/diagnostics.py` accept either
`--local-model <tag>` (Ollama at `--base-url`, default `http://127.0.0.1:11434/v1`)
or `--openrouter-model <id> --api-key-file <path>`. They also accept
`--max-tokens` (default 1536), `--timeout` (default 180), and `--max-steps`
(default 4). See [model adapters](model-adapters.md). To prepare a development
plan:

```bash
uv run --locked python examples/m5/trials.py init runs/m5-qwen-ae \
  --bundle runs/m4-tools/bundle.json --repetitions 1 \
  --local-model qwen3.8:27b
```

The plan pins the model request settings, the installed model digest, and runtime
metadata. It prepares ten ledgers (CSV then migration, A through E for each
repetition) and sends no model request. Every run command must use the same model
and settings:

```bash
uv run --locked python examples/m5/treatments.py start \
  runs/m5-qwen-ae/runs/001-migration-A --family migration --condition A \
  --bundle runs/m4-tools/bundle.json --local-model qwen3.8:27b --crash
uv run --locked python examples/m5/treatments.py resume \
  runs/m5-qwen-ae/runs/001-migration-A --family migration --condition A \
  --bundle runs/m4-tools/bundle.json --local-model qwen3.8:27b
uv run --locked python examples/m5/trials.py report runs/m5-qwen-ae
```

Repeat `start` and `resume` for every path in the plan. Before each new inference
request, the runner checks that the pinned model and runtime are unchanged. If
metadata is missing or has changed, the runner records an infrastructure failure
with zero model usage, releases the reservation, and reuses that failure on later
calls without polling Ollama again. `report` checks every trial against the plan
and sums known spent and reserved units. For live models it also sums recorded
prompt and completion tokens, and for OpenRouter it sums reported USD. Fixed
scripted runs leave token totals unreported. Failures before inference count as
known zero tokens, so token totals can be complete even for an unfinished task.
One repetition of these two fixtures is development evidence, not a set of
independent or held-out tasks.

Recorded live runs are in
[Qwen development runs](../experiments/2026-09-23-qwen-development-runs.md),
[OpenRouter GLM diagnostic](../experiments/2026-09-24-openrouter-glm-diagnostic.md),
and [submission controls](../experiments/2026-09-24-submission-controls.md).

## Limits

- The host classifies every disclosure. The policy does not inspect file
  contents for hidden secrets.
- History covers only explicitly named, completed episodes. Missing history is
  unsupported, not empty.
- Treatment runs show integration and recovery. They do not show a learning
  benefit from any condition. Comparisons need separate task lineages, untouched
  held-out instances, repeated runs, fixed thresholds, and full cost accounting
  (see [evaluation design](../experiments/evaluation-design.md)).
