# Configuration migration fixture

The second task family is a repository migration with an owner-approved
requirement change partway through. A worker edits one file in a tiny
dependency-free Python repository; a host-side checker assesses the captured
candidate against versioned contracts in a fresh contained process. The fixture
provides a fixed three-candidate matrix (`examples/m5/demo.py`), a scripted
worker run across the revision and a forced host kill (`examples/m5/recovery.py`),
and three supported proof cases (`examples/m5/proof_cases.py`). All of it uses
fixed proposals or a fixed fake model: it tests acceptance and recovery
boundaries, not model quality.

The formats and legacy consumer are synthetic test cases, not compatibility
commitments for Warranted's own formats.

## Task

The [seed repository](../../examples/m5/fixture/seed/README.md) contains
`settings.json`, an unfinished `migrate.py`, `modern_consumer.py`,
`legacy_consumer.py`, and a public `example_test.py`. Only `migrate.py` may
change. The program reads one JSON object on stdin and writes one JSON object on
stdout, migrating version 1 to version 2:

```json
{"version": 1, "host": "service.invalid", "timeout": 30, "label": "billing"}
```

```json
{"version": 2, "endpoint": "service.invalid", "timeout_seconds": 30, "label": "billing"}
```

- `host` becomes `endpoint` and `timeout` becomes `timeout_seconds`; both are
  seconds, so only names change. The output version is integer 2.
- The new consumer reads `endpoint` and `timeout_seconds`. The legacy consumer
  reads `label`, defaulting to `"default"` when absent. An empty label stays
  empty and does not select the default. The migration must preserve the label's
  presence and exact value. Both consumers are required from the first contract.
- Published input domain: integer version, nonempty string host, nonnegative
  integer timeout, optional string label. Boolean timeouts and duplicate JSON keys
  are invalid. Unknown fields and unsupported versions must fail explicitly.
  Version 2 uses the renamed fields with the same restrictions. These restrictions
  ship with the task; they are not hidden requirements.

The [reference cases](../../examples/m5/fixture/references.json) hold literal
expected outputs and consumer results independent of any migration program. The
ten cases cover present, absent, and empty labels, a zero timeout, three
already-migrated configurations, a boolean timeout, duplicate keys, an unknown
field, and an unsupported version. Private cases and expected results stay
outside the worker workspace. Candidate tests can guide a worker, but only the
host checker supplies acceptance evidence.

## Contracts and approved revision

[`contracts.json`](../../examples/m5/fixture/contracts.json) pins both
requirement sets, the owner approval, and the revision checkpoint:

| Contract | Obligations |
| --- | --- |
| `initial` | `repository_integrity`, `output_schema`, `renaming`, `legacy_label`, `input_rejection` |
| `revised` | The initial five plus `repetition`: valid version 2 input returns the same parsed configuration, including any label (idempotence) |

The owner approves the revision before the run. It adds an obligation and removes
or weakens none. It is delivered at a submission checkpoint, not a wall-clock
deadline: after the first submitted candidate receives its independent
assessment, in every condition, even when that candidate fails. The complete
revision is pinned privately at initialization; the initial worker sees only the
initial contract and notice that a revision checkpoint exists.

| Fixed candidate | Renaming | Legacy label | Safe repetition | Initial contract | Revised contract |
| --- | --- | --- | --- | --- | --- |
| One-way converter (accepts only version 1) | Pass | Pass | Fail | Accept | Reject |
| Drops the label on version 1 input, preserves version 2 input | Pass | Fail | Pass | Reject | Reject |
| Complete converter | Pass | Pass | Pass | Accept | Accept |

All three pass the output-schema and invalid-input checks. The
[complete proposal](../../examples/m5/fixture/complete.py) is the successful
control; two fixed flag changes produce the other candidates. The repetition
result is diagnostic under the initial contract and a required gate under the
revised one, and its private result is not exposed to the initial worker. The
label regression fails under both contracts, separating an authorised
requirement addition from a pre-existing compatibility obligation. An
initial-contract receipt cannot authorise acceptance under the revised contract,
even for the complete converter. See
[intent, contracts, and acceptance](../design.md#intent-contracts-and-acceptance)
and [contract revisions](../reference/claims-and-acceptance.md).

## Checker and execution boundary

**Candidate capture.** The host records the seed revision and a manifest of every
seed path and content digest. A candidate is a JSON object `{"migrate.py":
"<source>"}`; the host captures it before assessment and binds every result to
those exact bytes. Extra paths, duplicate keys, non-string or empty source, and
oversized payloads are rejected before execution (source limit 64 KiB). Changes
outside `migrate.py` fail the separate `repository_integrity` obligation. There
is no archive extraction, arbitrary repository-tree capture, or mount of a
worker directory into the checker.

**Execution.** Each candidate runs in one fresh instance of the rootless
container boundary described in
[worker and containment](../reference/worker-and-containment.md), with no
network, host mounts, credentials, expected answers, or ledger. A root
supervisor starts candidate Python as UID 1000, runs each case in a fresh process
and temporary working directory with only that case's input, stops all candidate
processes between cases, and captures output to protected files. Cases share the
outer container and its temporary filesystem outside those directories: this is
a bounded test batch, not full isolation between cases. Each case has a
two-second deadline and a 64 KiB combined output cap; the containment process,
CPU, memory, and filesystem limits also apply.

**Assessment.** After container cleanup, the host compares captured stdout with
the independent references and checks renaming, output schema, legacy behavior,
input rejection, and (under the revised contract) repetition. Every failed
obligation stays in the result rather than stopping at the first. Malformed
output cannot establish success. Candidate exit codes, printed success labels,
edited tests, and self-reported receipts cannot authorise acceptance.

**Outcomes.** A completed batch can contain failed cases; a known timeout remains
a failed case with its captured output and cost. Unavailable isolation and failed
transport are infrastructure failures, never candidate success or input
rejection. If cleanup cannot establish that execution stopped, the operation stays
unknown and reserved, and the next invocation blocks instead of retrying.

The fixture uses the existing ledger, acceptance boundary, claims, reservations,
and execution witnesses ([evidence ledger](../reference/evidence-ledger.md)).
Contracts, checker code, references, runtime identity, and budgets are pinned in
each run manifest. Completed requests reuse exact results without new executions
or charges. Budget units are attempt counts, kept separate from measured
operation time and provider cost.

## Run the fixed matrix

Prepare the pinned image and rootless Podman environment
([worker and containment](../reference/worker-and-containment.md)), then use a new
destination:

```bash
uv run --locked python examples/m5/demo.py runs/m5-demo
uv run --locked python examples/m5/demo.py runs/m5-demo
```

The first command runs each candidate on the ten cases and records independent
acceptance decisions under both contracts. The second reuses the exact
executions, checker results, and decisions. Neither makes model requests or
publishes repository changes. A changed fixture, checker, or recorded
environment requires a new destination.

| Budget unit | Allowance | Buys |
| --- | ---: | --- |
| `batch` | 3 | One bounded run of a candidate against all ten cases |
| `check` | 6 | One assessment against a fixed contract |

An interrupted batch stays reserved even if some cases finished. The execution
witness `executions.jsonl` records nine dispatches on the first run and none on
reuse; supervisor reports retain all 30 candidate/input results. Reports are in
`reports/` and copied evidence in `exports/`. The ledger keeps stdout, stderr,
supervisor status, infrastructure diagnostics, checker results, and acceptance
receipts. These exports include development references: they are for host
inspection, not worker context, replay support, or held-out evidence.

One local development run completed the matrix and reuse check in 25.00 seconds;
the full native suite took 46.79 seconds. Batching reduced container startups from
30 to three without removing cases. These are test timings, not model
performance evidence.

## Worker and restart

```bash
uv run --locked python examples/m5/recovery.py start runs/m5-recovery --crash
uv run --locked python examples/m5/recovery.py resume runs/m5-recovery
uv run --locked python examples/m5/recovery.py resume runs/m5-recovery
```

`start --crash` records the initial assessment, commits the approved revision,
then SIGKILLs the host while the ledger is open (all candidate containers are
already stopped). Omit `--crash` to exit normally after the initial report. The
first `resume` rejects reuse of the stale initial-contract receipt, rejects the
captured one-way converter under the revised contract, and accepts the corrected
converter. The second `resume` adds no operations or dispatches.

The run uses the mini-swe-agent and LangGraph integration from
[worker and containment](../reference/worker-and-containment.md). LangGraph keeps
workflow checkpoints in `graph.sqlite3`; the ledger keeps authoritative
attempts, outcomes, and budgets, and the host checks recorded operations before
any dispatch, including on resume (see LangGraph's
[durable execution guidance](https://docs.langchain.com/oss/python/langgraph/durable-execution)).
An in-process fake model supplies one fixed command per episode: materialize the
seed, write `migrate.py`, run the public example, and submit the source in
`result.json`. No provider, credential, or HTTP model service is involved.

- The initial worker receives the initial task and seed files, including notice of
  the checkpoint, but not the revision or private references.
- After the initial assessment, the host records the pinned approval, candidate,
  checker receipt, and acceptance decision together. Only then can a fresh
  continuation receive the revision, its previous source, and current independent
  results. Private inputs, expected answers, and future model responses never
  enter either workspace.
- The host reuses the exact raw results of the old candidate and runs a new
  assessment under the revised contract. The old acceptance, the later
  repetition failure, and the corrected acceptance all remain in history.
- The host treats a submission as a patch to the immutable seed. An invalid patch
  consumes one batch unit without executing its source; extra paths fail
  repository integrity. Workspace test edits cannot change the checker.

| Recorded work (unit) | After start | After first resume | After second resume |
| --- | ---: | ---: | ---: |
| Model responses (`model`) | 1 | 2 | 2 |
| Worker shell attempts (`tool`) | 1 | 2 | 2 |
| Candidate batches (`batch`) | 1 | 2 | 2 |
| Independent assessments (`check`) | 1 | 3 | 3 |

These totals are the full run budget; completions leave no reservations, and
reports also retain measured operation time. Witnesses are
`worker-dispatches.jsonl` (worker) and `executions.jsonl` (batch and checker),
separate from the ledger and workflow checkpoints.

A model or batch result lost before recording stays reserved after reopening and
blocks all new work; this fake model has no receipt lookup to settle it (the fake
HTTP service in [model adapters](../reference/model-adapters.md) tests
receipt-based reconciliation). Changed fixture, approval, or environment
identities block dispatch, and a resume without a committed revision fails
explicitly. Never supply the run directory or its exports as worker context.

The same fixture runs under conditions A–E through the
[treatment runner](../reference/contexts.md).

## Task layer

[`examples/m7/migration`](../../examples/m7/migration) runs the task through the
public API (`warranted`). The `migration` checker reuses the M5 driver's candidate
parser and `judge` unchanged, and runs each of the ten reference cases in its own
[checker job](../reference/worker-and-containment.md#checker-jobs): the job runs as
UID 1000 with a two-second deadline, no network, and per-job scratch, so cases
share no process or filesystem, unlike the M5 batch. An invalid patch is never
executed and fails `repository_integrity`.

The private `contract.json` names the gating obligations. `task.toml` uses the
initial contract; [`revisions/revised.toml`](../../examples/m7/migration/revisions/revised.toml)
replaces it with the revised one and tells the worker the new requirement.
`scheduled.toml` applies that revision after the first submission. Feedback shows
only the obligations of the contract in force, so the repetition result stays
host-only under the initial contract. A contract naming an unknown obligation, or
a reference suite with no case for a required obligation, is a checker fault:
missing evidence never counts as a pass.

[`tests/test_migration_fixture.py`](../../tests/test_migration_fixture.py) covers
the three-candidate matrix under both contracts, one job per case, invalid patches
that run nothing, a timeout that cannot pass input rejection, a rejected first
submission followed by the scheduled revision, an accepted one-way converter whose
record survives a later project revision while the new run rejects it, and a job
runner failure reported as an infrastructure failure. Its container test runs the
matrix through rootless Podman.

Differences from the M5 drivers: a candidate accepted before the checkpoint ends
its run, so M5's "accept, revise, reject the same run" sequence becomes a project
revision and a new run. The A–E treatments stay on the host kit.

[`proof.toml`](../../examples/m7/migration/proof.toml) adds a `LeanProof` check
named `renaming` beside the other obligations, verified against
`MigrationChallenge.lean`. Its correspondence check `renaming_correspondence`
runs the candidate on the `migrate` reference cases and requires that host and
timeout are renamed exactly; an invalid patch, or a reference suite with no
`migrate` case, never yields a true result (the latter is a checker fault). A
proved theorem whose correspondence fails is the worker's mistake, so `renaming`
is rejected with feedback and the run continues. The theorem covers both label
variants, so a candidate that drops the label passes the proof and is rejected by
`legacy_label`. The proofs stay unable to accept on their own.

| Case | `renaming` | Run decision |
| --- | --- | --- |
| `complete`, valid proof | passed | accepted |
| `drop-label`, valid proof | passed | rejected by `legacy_label` |
| keeps `host` beside `endpoint`, valid proof | rejected (`renaming_correspondence` fails) | rejected; a later `complete` submission is accepted |

## Proof cases

`examples/m5/proof_cases.py` pairs each of three host-approved proof targets
([`examples/proof_targets.py`](../../examples/proof_targets.py)) with a successful control and a candidate
that passes its narrow proof but fails an independent task obligation. The
unchanged CSV and migration checkers decide acceptance. Target binding, receipts,
and the verifier are described in
[proof verification](../reference/proof-verification.md).

| Target | Candidate and model | Proof support | Task acceptance |
| --- | --- | --- | --- |
| `uniqueness` | Correct output | Supported | Accepted |
| `uniqueness` | Dropped row or empty output | Supported | Rejected: record loss |
| `timestamp` | One-hour offset | Supported | Accepted |
| `timestamp` | Zero-offset interpretation | Supported | Rejected: timestamps and daily totals |
| `migration` | Preserve the optional label | Supported | Accepted |
| `migration` | Drop the label during version 1 conversion | Supported | Rejected: legacy consumer |

- **Uniqueness** reuses the CSV fixture's theorem and application checks
  ([CSV transformation](csv-transformation.md)).
- **Timestamp** proves a round trip for integer seconds,
  `(localSeconds - assumedOffsetSeconds) + assumedOffsetSeconds = localSeconds`.
  The correct candidate selects 60 minutes, the mistranslated one zero. Both match
  their selected model; the theorem does not establish that the offset follows the
  source contract, so the independent timestamp and daily-total checks reject the
  zero-offset result.
- **Migration** proves that renaming preserves host and timeout values for both
  label-preserving and label-dropping variants, and both leave version 2 input
  unchanged. The formal configuration distinguishes an absent label from an empty
  one. The host checks complete observed output against the selected variant,
  including the label, so the failing candidate matches its model; the narrow
  theorem simply omits a binding task obligation, and the legacy-consumer check
  preserves that failure.

[`proof-intent.json`](../../examples/m5/proof-intent.json) records the selected
variants and known gaps. Application support requires the checked theorem, a
valid input domain, and matching output from the exact candidate; missing, failed,
or uncertain execution cannot establish correspondence. Proof validity, current
application support, and task acceptance are separate outcomes. A candidate cannot
select the host's target, and a receipt for one target cannot support another.

Build the verifier bundle and pull the runtime image
([proof verification](../reference/proof-verification.md)), then run each command
in a fresh process:

```bash
uv run --locked python scripts/build_proof_bundle.py runs/m4-tools
podman pull "$(uv run --locked python -c 'from warranted.host import IMAGE; print(IMAGE)')"
uv run --locked python examples/m5/proof_cases.py start runs/m5-proofs --bundle runs/m4-tools/bundle.json --crash
uv run --locked python examples/m5/proof_cases.py resume runs/m5-proofs --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m5/proof_cases.py resume runs/m5-proofs --bundle runs/m4-tools/bundle.json
```

`start --crash` verifies all three proposals, writes a committed checkpoint, then
kills the host (exit status 137 in a shell). The first resume checks all
applications and task requirements; the second reuses completed proofs,
executions, checks, claims, and decisions and records zero new operations.
Changed fixture, host, or bundle bytes block dispatch; an unresolved operation
keeps its reservation and blocks new work.

| Recorded work (unit) | After start | After either resume |
| --- | ---: | ---: |
| Native proof attempts (`proof`) | 3 | 3 |
| CSV and application checks (`synthetic-work`) | 0 | 12 |
| Migration execution batches (`batch`) | 0 | 2 |
| Migration task checks (`check`) | 0 | 2 |

Witness files hold three proof executions and sixteen combined checker or batch
executions after either resume. Reports also retain measured operation time and
the bundle's recorded build time. One local native test completed the kill and
both resumes in 61 seconds (a development measurement). `reports/` and
`exports/` contain private development references and must not become worker
context. The receipt format is version 2 and the proof API takes `target=` (a `ProofTarget`);
there is no reader for the older format, so use a new run directory and rebuilt
bundle.

## Tests

Fast checks need no containers, proof tools, or model credentials:

```bash
uv run --locked pytest -q -m 'not container' tests/test_m5_fixture.py
uv run --locked pytest -q -m 'not container' tests/test_m5_recovery.py
uv run --locked pytest -q -m 'not proof' tests/test_m5_proof_cases.py
```

Native checks need the pinned image, rootless Podman, and (for proofs) the bundle:

```bash
WARRANTED_CONTAINER_TESTS=1 uv run --locked pytest -q -m container tests/test_m5_fixture.py
WARRANTED_CONTAINER_TESTS=1 uv run --locked pytest -q -m container --durations=5 tests/test_m5_recovery.py
WARRANTED_PROOF_TESTS=1 WARRANTED_PROOF_BUNDLE=runs/m4-tools/bundle.json \
  uv run --locked pytest -q -m proof --durations=5 tests/test_m5_proof_cases.py
```

Covered negative cases include a lost legacy label hidden by correct renaming,
malformed or self-reported success, invalid input without explicit failure,
protected-path edits and forged payloads, stale old-contract receipts, unknown
executions that must block retry, unavailable isolation, infrastructure failure
mistaken for input rejection, timeout, forged supervisor status, background
processes and output limits between cases, a rejected first submission that still
reaches the revision, missing or changed approval, lost model and batch results,
wrong-offset and label-dropping models that match their candidates, and partial
output correspondence.

CI runs the native container suite (matrix plus one forced-kill recovery with two
fresh resumes) and keeps reports, exports, and witnesses for 14 days. CI runs the
proof cases inside the condition-E [treatment](../reference/contexts.md) runs, once
per relevant family; the standalone script is for focused inspection.

## Limits

- The ten cases are finite development inputs. Passing them does not establish
  behavior on every input, Python parser correctness, or calendar conversion.
- The checker enforces declared requirements; it does not discover unstated user
  intent. Fixed proofs demonstrate acceptance boundaries, not that a model can
  find missing requirements or search for proofs.
- Cases within one batch are not fully isolated from each other.
- The fixed matrix and scripted recovery do not measure model quality or held-out
  performance; the public fixture is development data only. Measured trials are
  covered in [evaluation design](../experiments/evaluation-design.md).
