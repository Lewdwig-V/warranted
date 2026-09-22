# M5: repository migration and knowledge comparisons

Implementation plan, 2026-09-22. M4 is complete for its fixed development fixture.
The [first fixture slice](m5-fixture.md) implements the fixed migration matrix and an independent checker.
The [worker demonstration](m5-fixture.md#worker-and-restart) implements the second slice with a fixed fake model and forced restart.
The [supported proof cases](m5-proofs.md) implement the third slice across all three targets.
The [A–E treatment runner](m5-contexts.md) completes slice 4 for fixed workers:
captured workspace and notes, permitted history, updated executable models,
dependency reports, and charged proof work across both recovery fixtures.
Measured model comparisons remain planned.

## First migration task

Use a small Python repository with no third-party dependencies.
Its formats and legacy consumer are synthetic test cases, not compatibility commitments for Warranted.
It contains `settings.json`, `migrate.py`, two consumers, and public example tests.
The worker changes only `migrate.py`.
The program reads one JSON object from standard input and writes one JSON object to standard output.
The task migrates the configuration from version 1 to version 2:

```json
{"version": 1, "host": "service.invalid", "timeout": 30, "label": "billing"}
```

```json
{"version": 2, "endpoint": "service.invalid", "timeout_seconds": 30, "label": "billing"}
```

Both timeout fields use seconds. The migration changes names, not units.
The new consumer reads `endpoint` and `timeout_seconds`.
The legacy consumer reads `label`, with `"default"` when that field is absent.
An empty label remains empty. It does not select the default.
Preserve the label's presence and exact value when migrating.
Both consumers remain required from the first contract version.

The initial input domain has an integer version, a nonempty string host, and a nonnegative integer timeout.
The optional label is a string. Boolean timeout values and duplicate JSON keys are invalid.
Unknown fields and unsupported versions fail explicitly under this bounded fixture contract.
Version 2 uses the renamed fields with the same value restrictions.
Publish these restrictions with the task rather than treating them as hidden requirements.

Store literal expected configurations and consumer results independently of the migration program.
Include a present label, an absent label, an empty label, and a zero timeout.
Keep private grading cases and their expected results outside the worker workspace.
Candidate tests can guide the worker, but only the host's checker supplies acceptance evidence.

## Approved requirement change

Contract version 1 requires migration of valid version 1 configurations and preservation of both consumers' behavior.
Contract version 2 adds safe repetition: valid version 2 input must return the same parsed configuration.
This property is idempotence, meaning repeated application leaves the result unchanged.
The owner approves this addition before the run.
It does not remove or weaken the existing obligations.

Deliver the revision after the first submitted candidate receives its independent assessment.
Commit the revision before killing the host, while its ledger context remains open.
Use this submission checkpoint in every condition, even when the first candidate fails.
Do not deliver the revision at a wall-clock deadline.
Pin the complete revision privately at initialization and expose it only at the checkpoint.
The initial worker receives the initial contract and notice of the revision checkpoint.

On resume, the host first rejects reuse of the old acceptance receipt under the new contract.
It then checks the captured old candidate against the revised requirements.
A converter that only accepts version 1 fails the new repetition obligation.
The worker can submit a corrected converter that also accepts version 2 unchanged.
Keep the earlier acceptance, subsequent failure, and corrected result in history.

| Fixed candidate | Renaming | Legacy label | Safe repetition | Contract 1 | Contract 2 |
| --- | --- | --- | --- | --- | --- |
| Initial one-way converter | Pass | Pass | Fail | Accept | Reject |
| Drops the label when converting version 1, preserves version 2 input | Pass | Fail | Pass | Reject | Reject |
| Complete converter | Pass | Pass | Pass | Accept | Accept |

The repetition result is diagnostic under contract 1 and becomes a required gate under contract 2.
Its private result must not expose the later requirement to the initial worker.
The label regression is a failure under both contracts.
This separates an authorized requirement addition from a pre-existing compatibility obligation.

## Checker and execution boundary

Record the seed repository revision and a manifest of each permitted path and its content digest.
Capture the candidate before assessment, and bind every result to those exact bytes.
Changes outside `migrate.py` fail a separate repository-integrity obligation.
Check the renamed values, output schema, legacy behavior, and current repetition requirement independently.
Keep every failed obligation in the result rather than stopping after the first failure.
Malformed output cannot establish success. Distinguish candidate failures from missing isolation or other infrastructure failures.

Each candidate runs inside one fresh, bounded container with no network or host mounts.
A trusted supervisor runs its cases in fresh processes and temporary working directories, stopping candidate processes between cases.
Each process receives only its current input. Expected answers and the authoritative ledger stay outside the container.
Cases share the outer container's temporary filesystem. Full isolation between cases is not claimed.
The host captures output and computes the acceptance result outside that container.
Candidate exit codes, printed success labels, edited tests, and self-reported receipts cannot authorize acceptance.
Bound runtime, processes, memory, input size, and captured output before dispatch.
Retain raw output, diagnostics, exit status, and known costs for failed attempts as well as successes.

Reuse M3's [worker containment](m3-adoption.md) and M4's [verification boundary](m4-verification.md) where their assumptions apply.
The existing worker adapter captures one bounded `result.json`, not an arbitrary repository tree.
Begin with this flat repository and one editable source file.
Use a bounded candidate payload for that source, and retain the immutable base manifest separately.
Reject unexpected paths and oversized or malformed payloads before execution.
Do not add a general archive extractor or mount a worker's directory into the host checker.

Use the existing ledger, acceptance boundary, claims, reservations, and execution witnesses.
Pin contracts, checker code, reference cases, runtime identity, and budgets in each run manifest.
An interrupted operation retains its reservation and blocks dispatch until an attributable outcome exists.
Completed requests reuse exact results without new executions or charges.
Synthetic attempt counts remain separate from measured time and provider cost.

## Proof cases

The migration theorem establishes field renaming and preservation of the host and timeout values.
Its formal model has two variants: retain the optional label, or omit it during version 1 conversion.
Both variants satisfy the same narrow renaming target.
Both preserve version 2 input unchanged.
The host checks complete output correspondence with the selected variant, including the label's presence and value.
The regression therefore fails the independent legacy obligation despite a valid, applicable proof and matching implementation output.
The successful control preserves the label and passes every task obligation.

The pilot's [timestamp-mistranslation case](pilot.md#specification-and-behavioral-failures) uses a separate target.
Its negative candidate matches the zero-offset model and its valid round-trip theorem.
The source contract requires a one-hour offset, so independent timestamp and total checks reject it.
The demonstration retains the existing M4 incomplete-target case and successful controls for all three cases.
Proof verification, current application support, and final task acceptance remain separate outcomes.

The verifier now requires one of three explicit host-approved target IDs.
Each target pins its challenge, supporting definitions, comparator configuration, and verification receipt.
The M4 uniqueness challenge remains unchanged. Candidates cannot select the host's target.
Tests reject wrong-target receipt reuse and assess historical receipts without installed tools or target resources.
These proofs do not establish correctness of the Python parser or discover undeclared user requirements.

## Five implementation slices

1. Fixture and independent acceptance. Add the seed repository, versioned contracts, owner approval, reference cases, and three fixed candidates.
   Demonstrate the matrix above with contained candidate execution and separate host checks.
   Cover malformed output, duplicate keys, incorrect values, lost labels, edited protected files, timeout, and unavailable isolation.
   Keep fast parser and comparison tests separate from native execution tests.
2. Worker and restart. Use the existing mini-swe-agent and LangGraph integration with a fixed fake model and the approved revision checkpoint.
   Demonstrate stale receipt rejection, a failed old candidate, correction, retained costs, and a second resume without repeated work.
   Include unknown outcomes and an external execution witness.
   This establishes integration behavior, not model quality.
3. Supported proofs. Add exact target identities and the migration and timestamp proof cases described above.
   Preserve all three successful controls, independent failures, and proof costs across restart.
   Keep executable acceptance identical with and without proof support.
4. Shared boundary and A–E treatments. Compare both working fixtures before extracting common code.
   Extract only the repeated operations needed by both, with fixture-specific requirements and checkers kept outside the core.
   Implement the [pilot's A–E visibility rules](pilot.md#knowledge-workflow-conditions) and credential-free treatment tests.
   Host-only recording must not expose queryable history, dependencies, private cases, or future revisions to an unauthorized treatment.
   The [context boundary and treatment runner](m5-contexts.md) now connect the
   recovery and proof workflows. Fast tests cover A–E; native E tests cover
   both fixtures through a host kill and two fresh resumes, alongside the existing
   baseline recovery tests.
5. Measured trials. Record task lineage, split assignments, model versions, inference settings, fixed scheduling, budgets, and evaluator identities.
   Use development runs to select thresholds, then freeze them before untouched final evaluation.
   Run repeated frontier and smaller-model trials on both families, with failures and all costs retained.
   Report each family's results and the specification failures separately.

Each slice requires a focused PR and relevant GitHub checks.
Add no model dependency or paid trial to the first fixture slice.
Keep native execution out of the fast local suite and avoid timeout tests longer than their failure case requires.
Leave M5's completion boxes unchecked until their stated evidence exists.

## Trial decisions still required

Before paid development trials, choose available provider/model versions and an explicit total spending cap.
Pin equal worker capabilities, fixed scheduling, per-attempt limits, and task-success predicates across A–E.
Select development thresholds for useful improvement, then freeze models, budgets, and evaluation rules before final runs.
Split by task instance and derivation lineage, not random copies of the same examples.
The public examples in this document belong to development and cannot become held-out evidence.

Report stale reuse, recovery failures, repeated work, task success, human interventions, and total work and cost.
Include failed proof attempts, fixture setup, formalization, and trial development in campaign costs.
Adopt dependency or proof support only when its measured benefit justifies its cost against the simpler conditions.
Keep the [optional Jev experiment](roadmap.md#m3a--jev-as-a-system-1-classifier-and-judge) and [M6 scheduling experiment](roadmap.md#m6--dream-rsi-adaptation-for-search-improvement) separate.
