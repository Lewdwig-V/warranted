# M5: fixed migration candidates

The first M5 slice checks three small migration programs against two fixed contracts.
The formats and legacy consumer are invented test cases.
They do not define compatibility commitments for Warranted's own formats.
Worker recovery, revision delivery, migration proofs, and A–E trials remain planned.

## Run the matrix

Prepare the pinned Python image and rootless Podman environment described in the [M3 plan](m3-adoption.md).
From the repository root, run the demonstration with a new destination:

```bash
uv run --locked python examples/m5/demo.py runs/m5-demo
uv run --locked python examples/m5/demo.py runs/m5-demo
```

The first command executes each candidate on ten cases and records independent acceptance decisions.
The second command reuses the exact candidate executions, checker results, and decisions.
Neither command makes model requests or publishes repository changes.
A changed fixture, checker, or recorded environment requires a new destination.

The [seed repository](../examples/m5/fixture/seed/README.md) contains a configuration, an unfinished migration, two consumers, and a public example test.
Only `migrate.py` can change.
Each candidate supplies a JSON object containing that file's source.
The host rejects extra paths, duplicate keys, non-string source, and oversized payloads before dispatch.
The base manifest retains every seed file's path and content digest.
No archive extraction or arbitrary repository-tree capture is implemented.

The [complete proposal](../examples/m5/fixture/complete.py) supplies the successful control.
Two fixed flag changes produce a one-way converter and a converter that drops labels.
The host captures all three resulting source files before execution.
The [reference cases](../examples/m5/fixture/references.json) contain literal expected values independent of those programs.

## Requirements and results

The migration renames `host` to `endpoint` and `timeout` to `timeout_seconds`.
It sets the output version to integer 2 and preserves both values.
Both timeout fields use seconds.
The legacy consumer reads an optional label, with `"default"` when absent.
The migration must preserve that field's presence and exact value, including the empty string.

The initial contract also requires valid output structure and explicit rejection of invalid inputs.
The revised contract adds safe repetition: valid version 2 input must return the same parsed object.
The [contracts and owner approval](../examples/m5/fixture/contracts.json) retain both requirement sets and the planned revision checkpoint.
This slice compares independent contract contexts. It does not yet deliver a revision to a worker.

| Candidate | Renaming | Legacy label | Safe repetition | Initial contract | Revised contract |
| --- | --- | --- | --- | --- | --- |
| One-way converter | Pass | Pass | Fail | Accept | Reject |
| Drops the label on version 1 input | Pass | Fail | Pass | Reject | Reject |
| Complete converter | Pass | Pass | Pass | Accept | Accept |

All three candidates pass the output-schema and invalid-input checks.
Each failed obligation remains separate in the report and ledger.
The repetition result is diagnostic for the initial contract and required for the revised contract.
An initial-contract receipt cannot authorize acceptance under the revised contract, even for the successful control.

The ten cases cover present, absent, and empty labels, zero timeout, and three already-migrated configurations.
They also cover boolean timeout values, duplicate keys, an unknown field, and an unsupported version.
These are finite development cases. Passing them does not establish behavior for every possible input or discover missing requirements.

## Execution and evidence

Each candidate uses one fresh instance of M3's rootless container boundary.
A trusted supervisor runs its ten cases in fresh processes and temporary working directories.
It stops all candidate processes between cases and delivers only the current input to each process.
Cases share the outer container, including its temporary filesystem outside those working directories.
This is a bounded test batch, not a claim of complete isolation between cases.
It has no expected answers, authoritative ledger, host mounts, credentials, or network access.
A root supervisor starts candidate Python as UID 1000 and captures output in protected files.
It reports each process's actual exit status, timeout, output limit, and elapsed time.
The host compares captured stdout against the independent references after container cleanup.
Printed success labels and candidate-supplied exit reports cannot establish acceptance.

Candidate execution has a two-second deadline and a 64 KiB combined output cap.
Source files have a 64 KiB limit. M3's process, CPU, memory, and filesystem limits still apply.
A completed supervisor batch can contain failed candidate executions.
A known timeout remains a failed case with captured output and its cost.
Unavailable isolation and failed transport remain infrastructure failures.
If cleanup cannot establish that execution stopped, the operation stays unknown and reserved.
The next invocation blocks rather than retrying it.

The run allows three batch units and six checker units.
One batch unit buys one bounded run of a candidate against the ten cases.
An interrupted batch stays reserved, including when some cases finish before the interruption.
One checker unit buys one assessment against a fixed contract.
These units are attempt counts, not measured CPU time or provider cost.
Reports retain measured execution and checker time separately.
The independent execution witness records nine dispatches on the first run and no additions on reuse.
The supervisor reports retain all 30 candidate/input results.

Reports are in `reports/`, copied evidence is in `exports/`, and the witness is `executions.jsonl`.
The ledger preserves stdout, stderr, supervisor status, infrastructure diagnostics, checker results, and acceptance receipts.
CI retains the reports, exports, and witness for 14 days.
These exports include the development references and belong to host inspection, not worker context.
They are not replay support or held-out evaluation evidence.

## Verification

Run the fast host checks without containers or model credentials:

```bash
uv run --locked pytest -q -m 'not container' tests/test_m5_fixture.py
```

Run the native matrix, timeout, and status-forgery cases with the pinned image available:

```bash
WARRANTED_CONTAINER_TESTS=1 uv run --locked pytest -q -m container tests/test_m5_fixture.py
```

The native matrix uses two fresh host processes to establish completed-result reuse.
The remaining tests cover independent failures, malformed output, protected paths, stale receipts, unknown outcomes, and unavailable isolation.
One local development run completed the matrix and reuse check in 25.00 seconds.
The complete native M5 suite took 46.79 seconds, including timeout, forged-result, background-process, and output-limit cases.
Batching reduced container startups from 30 to three without removing matrix cases.
These are test timings, not evidence of improved model performance.
Forced worker restart at the approved revision remains the [next implementation slice](m5-migration.md#five-implementation-slices).
