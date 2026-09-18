# Pilot and evaluation

Status: proposed. No fixtures, evaluator, or benchmark results exist yet. Start
with scripted deterministic runs, then add bounded agents. The task families
below exercise the design without adopting another harness's interfaces.

## First family: data transformation with revised assumptions

Create a small local dataset with identifiers, timestamps, and values, plus
versioned source documentation. The objective is to produce a normalised dataset,
an aggregate, and an executable transformation with independently checked outputs.
Use CSV/JSON and ordinary files; no external data service is needed.

The first source contract states a timestamp interpretation and identifier
uniqueness assumption. The controlled environment later supplies a revised
contract and input snapshot, for example a changed offset convention or duplicate
identifiers. The agent must revisit affected results, preserve unaffected work,
and finish under the current requirements after a forced fresh-session restart.

The revision mechanism is part of the fixture contract. Deliver it at the same
predeclared logical checkpoint or charged-work boundary across conditions, rather
than at a wall-clock time that advantages a faster run. The host persists and
exposes the revision; do not expect a dependency graph to detect unseen changes.

The independent evaluator owns the current requirements, reference examples,
and private acceptance cases. Candidate code and generated tests are useful
artifacts, but cannot replace that evaluator. Keep published feedback distinct
from private grading inputs. Version the fixture, task contract, and evaluator.

A later Lean obligation may prove that an injective mapping preserves uniqueness
for unique input identifiers. Checks supporting those premises belong to the
current input snapshot. The theorem does not by itself verify the separate
transformation implementation or establish the source data's properties.

## M1 scripted walkthrough

Status: acceptance cases for M1. Local ledger tests now cover persistence,
operation recovery, and accounting. The complete CSV script, fixture, and permitted
exports remain PR3 work. The history below defines observable facts, not mandatory
tool calls or event types.

### Fixed fixture and expected outputs

Use this small instance of the first task family. Keep its source contract fixed
throughout M1. M2 adds revised assumptions and dependent claims.

The input is a UTF-8 CSV file:

```csv
id,timestamp,value
r1,2026-01-01T00:30:00+01:00,7
r2,2026-01-01T02:00:00+01:00,11
r3,2026-01-02T00:15:00+01:00,5
```

The source contract requires the transformation to preserve every row, its order,
its identifier, and its integer value. Convert each explicit timestamp offset to
UTC. Sum the values by the UTC date, not the source date.

The expected normalized CSV is:

```csv
id,timestamp,value
r1,2025-12-31T23:30:00Z,7
r2,2026-01-01T01:00:00Z,11
r3,2026-01-01T23:15:00Z,5
```

The expected daily totals are:

```json
{"2025-12-31": 7, "2026-01-01": 16}
```

Store the fixed expected outputs independently of the transformation code. Compare
the normalized rows and parsed totals with these fixed expectations. Preserve
the exact raw output bytes even when the comparison ignores serialization details.
This comparison establishes this fixture's output behavior. It does not implement
the M2 acceptance gates or prove the transformation correct for other inputs.

### Identity and recorded context

An operation ID names one requested execution within a project. Bind it to the
operation kind, input bytes, transformation version, source contract, and relevant
environment and context versions. Compare this identity before returning a stored
result or dispatching work. A new session does not change an operation's identity.
Equal output bytes do not make different requests identical.

A world identifies an investigation with fixed relevant context. Record the
fixture version, run identity, initial limits, and environment. Record the world
identity, its parent or explicit absence, and session boundaries.
The [Dream-RSI adaptation](design.md#dream-rsi-exploration-and-replay) requires
this context later. M1 records one root world. It does not execute branches or
replay histories.

Record the script version and its input references with each result. The host
captures raw standard output, standard error, exit status, and output artifacts.
Keep the source contract and input snapshot as versioned artifacts too.
Agent-authored text cannot supply the origin of an authoritative result.
Model and proof configuration are not applicable to this scripted run.

### Expected history

A reservation holds resources for an unresolved operation. Use a project limit
of five synthetic work units for transformation attempts. Reserve three units
before dispatch and report two units of actual usage for this successful script.
These numbers test accounting. They are not measured tokens, time, or money.
Record measured elapsed time separately.

| Step | Observable history | Spent / reserved / available units |
| --- | --- | --- |
| Create | Create a project and session. Record the manifest, source contract, and input snapshot. | 0 / 0 / 5 |
| Reserve | Record `transform-1`, its exact request identity, and its reservation before execution. | 0 / 3 / 2 |
| Execute | Run the script once. Capture its raw results and output files outside authoritative storage. | 0 / 3 / 2 |
| Complete | Publish complete artifact bytes. Commit the result references, operation outcome, and accounting together. | 2 / 0 / 3 |
| Reopen | Start a fresh process and session. Recover the same project history and artifact bytes. | 2 / 0 / 3 |
| Repeat | Request `transform-1` with the same identity. Return its recorded outcome without execution or another charge. | 2 / 0 / 3 |
| Inspect | Export the permitted history. Trace the output bytes to the operation, input snapshot, and source contract. | 2 / 0 / 3 |

Recording completion releases the entire reservation and adds actual usage once.
Keep the original session and outcome in history when a later session reads them.
Inspection and repeated reads do not add corroborating evidence or repeat work.
The script's execution count must remain one across completion and reopening.
Tests must observe executions independently of the ledger's claim of completion.

### Interruption and negative cases

Run each case from a fresh copy of the fixture. Start recovery in a new process.
Use controlled interruption points, not sleeps. Assert the resulting history,
artifact bytes, execution count, and accounting through supported inspection.

| Case | Required result |
| --- | --- |
| Interrupt before the reservation commits | No execution starts. No partial operation or charge appears. The available allowance remains five units. |
| Interrupt after reservation, before a committed result | The three-unit reservation survives. Reopening dispatches nothing. Preserve a known unstarted operation as pending. If prior execution is uncertain, retain an unknown outcome. Repeating the unresolved request does not dispatch or add another reservation. |
| Interrupt during artifact publication | No committed completion references a partial or missing artifact. Unreferenced and temporary files supply no evidence. The reservation survives. |
| Interrupt during the completion transaction | Recovery sees either the unresolved reservation or the complete outcome with settled usage. It never sees a partial accounting update. |
| Commit completion, then lose the response | The repeated request returns the committed result. Execution count remains one. Spent usage remains two units and the reservation remains zero. |
| Repeat a completion receipt | An identical receipt changes nothing. A conflicting outcome, artifact reference, or usage value is rejected. The original result remains intact. |
| Reuse an operation ID with changed identity | Change input bytes, transformation version, contract version, or relevant context separately. Each request fails before execution or cached success, even if it produces the same output. |
| Record another observation or artifact version | The new record retains its origin. The old record and its raw bytes remain readable and unchanged. Attempts to replace the old record through the writer fail. |
| Remove or alter a referenced artifact | Inspection reports missing or corrupt evidence. It does not report a usable result or silently regenerate the bytes. |
| Reserve beyond the remaining allowance | The request fails before execution. In particular, an unresolved three-unit reservation blocks another three-unit request under the five-unit cap, across sessions and operation IDs. |
| Report usage above the reservation or cap | Retain the full observed usage and record the breach. Block further dispatch. Never reduce recorded usage to fit the reservation or cap. |
| Script fails with a known outcome | Preserve its nonzero exit status, raw diagnostics, and actual usage. Settle its reservation once. A repeated request returns that failure without another execution. |

File publication and a database transaction do not form one atomic operation.
The required ordering publishes complete artifact bytes before a transaction
commits references to them. A crash can leave an unreferenced file. Such a file
does not establish an operation outcome or release a reservation. M1 does not
need automatic cleanup of those files.

The successful case is required alongside the failures. Reopening must recover
usable completed work. Rejecting every operation does not satisfy M1.

### Scope and completion evidence

M1 covers process interruption on a local filesystem with one trusted writer.
The tests do not establish recovery from power loss, disk loss, or hostile changes
to host-owned state. They do establish explicit failure when referenced evidence
is missing or corrupt. Concurrent writers remain outside this milestone.

Keep candidate output separate from authoritative files. Give inspection consumers
only permitted exports or access that cannot reach the authoritative write path.
Editing an export must leave authoritative history unchanged. M1 has no untrusted
worker. Process isolation and worker attempts to forge receipts belong to M3.

An unresolved operation keeps its reservation until attributable evidence permits
reconciliation. An operator's guess or a new session is not such evidence.
The fake service and lost-response reconciliation in the recovery fixture belong
to M3. M1 completion evidence must show that unresolved work stays visible and
blocks unsafe retries.

Implement these cases alongside the three PRs in
[the M1 roadmap](roadmap.md#m1--local-evidence-and-restart). Add pytest with the
first behavior-bearing slice, as required by [AGENTS.md](../AGENTS.md).
Local checks must need no model credentials or external services. Add the exact
walkthrough and test commands when they exist. Retain the resulting history as
completion evidence. This plan alone does not complete any M1 checkbox.

## Second family: repository migration with revised requirements

Provide a small local repository containing configuration, a consumer, and tests.
Ask for a migration between two explicit formats or APIs while preserving declared
behavior. Partway through, revise one requirement, such as compatibility with an
older consumer or preservation of an optional field, and force a new session.

Use independent acceptance checks outside the editable candidate workspace.
Record exact repository revisions and fixture changes. The agent should identify
which patches, assumptions, and checks need revision; a passing test from an old
requirement does not satisfy the new one. Work in local copies with no deployment
or remote write requirement.

These two fixtures should determine the first shared adapter contract. Keep the
environment's observations/operations, the worker's proposals, and authoritative
checking distinct, but avoid freezing a universal API before both fixtures work.

## Specification and behavioral failures

Exercise the [intent and acceptance requirements](design.md#intent-contracts-and-acceptance)
with deterministic candidates before adding agent trials. Each fixture retains
the original request, source documentation, and independently supplied behavioral
examples as versioned artifacts alongside its formal interpretation. The fixture
owner fixes the acceptance obligations and evaluator before the run; the worker
cannot replace them with its own laws, tests, or explanations.

Include three distinct negative cases:

1. **Mistranslated requirement.** A data transformation's formal target uses the
   wrong timestamp offset convention. The candidate meets that target, but fails
   an example derived independently from the source documentation and request.
2. **Incomplete formal target.** A transformation drops records while satisfying
   an identifier-uniqueness law. The original request requires preserving all
   records, and an independent check detects the loss. Include the degenerate
   candidate that drops every record; uniqueness alone permits it.
3. **Regression outside the proved properties.** A repository migration meets
   its field-renaming properties but drops an optional field required by a legacy
   consumer. A predeclared compatibility case fails even though the new formal
   obligations remain satisfied.

Establish the two data-transformation cases with executable checks in M2. In M4,
formalise only uniqueness preservation and reuse that theorem for the incomplete
target case and its successful control. For example, applying the theorem to an
empty selected input can establish output uniqueness while the independent check
rejects losing records from the nonempty source. This requires no proof of record
preservation or timestamp semantics. Introduce the migration fixture across all
conditions in M5; add the timestamp-mistranslation and migration proofs in its
Lean condition, retaining M4's case.

Use valid proofs of these inadequate targets. Do not substitute proof forgery,
unsupported premises, or a mismatch between model and implementation for these
specification failures. Include successful controls satisfying all obligations
so that rejecting every candidate cannot count as success. Keep final task
acceptance identical across A–E.

Record the narrow check or proof as successful and the task as rejected by the
failed independent obligation. Preserve the conflicting evidence across restart;
neither another proof nor an agent-authored reinterpretation may erase it. A
corrected candidate may pass after the required checks run again, with the old
failure still recorded. When the fixture owner authorises a contract revision,
retain the old result and require a fresh acceptance decision bound to the revised
contract and definitions.
Also reject attempts to change a supporting predicate to make an unchanged law
easier to satisfy under the old contract identity.

These cases test rejection of known failures against independently supplied
requirements. They do not demonstrate automatic discovery of unstated user intent.
Measure finding omitted requirements separately from enforcing known ones; do
not count an evaluator rejection alone as successful specification repair.

## Recovery fixture

Use a controlled fake service for operation-level crash tests. It can supply an
idempotency key and exact receipt in one mode, and deliberately cannot establish
the outcome in another. A lost response must either be reconciled for that exact
operation or remain unknown with its reservation retained. Aggregate status is
not a substitute for an attributable receipt. Reopening a project never resets
the task's usage or authorises a blind retry.

## Knowledge-workflow conditions

The first two additions make the influences of
[PRO-LONG](https://arxiv.org/html/2607.20064v2) and
[Schema](https://schema-harness.github.io/) explicit: accessible history in B,
then executable models in C. These are Warranted conditions inspired by that work;
they are not replications of those systems or their ARC-AGI-3 evaluations. D and E
test the additional value of dependency tracking and formal proof support.

| Condition | Added support |
| --- | --- |
| A | Ordinary workspace and prose notes |
| B | A plus a complete queryable observation/action history |
| C | B plus executable models and regression checks |
| D | C plus dependency tracking and applicability invalidation |
| E | D plus the Lean formalisation and proof workflow |

All conditions use the same small shell/file interface and available tools,
including Lean when applicable. Vary the knowledge workflow and associated
acceptance policy, not access to a secretly stronger model or private data.
Baseline agents may use available tools spontaneously; record that behavior.
The independent final task-success predicate is the same across conditions.
Additional assurance required by E carries its full cost.

Use the same initial fixed search policy, task instances, model/inference settings,
per-attempt allowances, source access, and host caps. Execution permissions and
evaluator isolation remain in force for every condition. Harness-only recording
may capture all runs for measurement, but must not expose B–E's extra knowledge
support to A. Keep Jev, Hindsight, and broader harness optimisation out of this study.

Run multiple fresh trials and include both frontier and smaller models. Begin
with inexpensive development runs to set budgets and a minimum useful improvement,
then freeze them before final evaluation. Split by task instance and derivation
lineage so variants or descendants do not leak between training and held-out data.

## Jev classifier and judge comparison

The optional [M3a experiment](roadmap.md#m3a--jev-as-a-system-1-classifier-and-judge)
tests [routing and judgment gates](design.md#jev-system-1-classification-and-judgment)
separately from A–E and Dream-RSI. Begin with M3's first-family baseline; use M5's
second family to test transfer. Fix the knowledge workflow and reasoning worker
within each comparison, along with available context, task acceptance, host caps,
and the surrounding exploration policy.

For routing, compare three arms: the baseline without a learned classifier/judge;
an LLM classifier/judge; and Jev. The latter two receive the same evidence, supplied
choices, and rubrics and use the same allowed routing/ranking actions. Record their
model versions and compute costs separately from the reasoning worker.
First collect shadow predictions without affecting the baseline run; paired live
trials with fresh state are required to establish downstream benefit.

Evaluate judgment gates separately on a common candidate set, comparing designated
LLM and Jev judges against independent reference labels. Keep the requirement,
rubric, evidence access, and error tolerance fixed; record each judge's calibrated
decision rule in its own pinned contract version. Then test them in paired live
runs, retaining the same independent final task-success predicate. A no-judge run
is a workflow baseline, never a way to bypass a required judgment gate. Include
positive controls where a qualifying judgment permits its protected transition,
plus nonqualifying, stale, forged, and abstaining results that leave it blocked.

Define reference labels independently of the candidate and its judge, using
fixture outcomes for objective categories and independently reviewed labels for
subjective rubrics. Preserve ambiguous cases; agreement with another model is
not ground truth. Keep final evaluator answers hidden from all decision inputs.
Split calibration/development and held-out examples by task provenance and lineage.
Set category-specific thresholds, false-acceptance/false-rejection tolerances,
approved escalation paths, and a minimum useful cost or latency gain on development
data, then freeze them for held-out trials.

Report confusion/error rates by category, rubric agreement, probability calibration
(for example Brier score and reliability bins), and error versus retained coverage
as abstention increases. Include confident errors, escalation rates, p50/p95
latency, and total cost including fallbacks, failures, and calibration. For gates,
report false acceptances and rejections with uncertainty estimates, including
correlated worker/judge errors. Report live task success, recovery, and gate
enforcement violations independently of the judge's scores. A wrong judgment
accepted under the declared policy is a measured judge error, not a bypass;
both kinds of failure matter and must remain visible.
Exercise the existing specification-failure candidates, stale or missing evidence,
unfamiliar inputs, adversarial judge instructions, attempts to cherry-pick repeated
judgments, provider failures, and budget exhaustion, with successful controls.
Development fixtures must show that even a
maximally favourable judgment cannot override another failed required obligation.

Compare confidence-only escalation with escalation that also considers diagnostic
usefulness, under equal total budgets. Include confidently rejected candidates
with unclear causes, interacting failures, and repeated repairs that leave the
same defect. Measure successful repairs, repeated rejections, and total time/cost
to independent acceptance, including all diagnostic work. This tests whether fast
verdicts save end-to-end effort rather than merely shifting work to later attempts.
Audit a predeclared sample of confident passes and failures against independent
labels as well as escalated cases; evaluating only uncertain cases would hide
confident errors. Keep audit feedback out of held-out routing decisions.

Retain only categories meeting the frozen criteria on held-out tasks; report
first-family and second-family evidence separately. Keep the original Dream-RSI
comparison Jev-free. A later combined study must freeze Jev's model, rubrics, and
routing thresholds across fixed/evolved policies. Bind them into world identity,
reveal recorded predictions only when their inputs were visible, and make missing
predictions unsupported in replay rather than calling a live judge.

## Dream-RSI scheduling comparison

This is the pilot's adaptation of
[Dream-RSI](https://arxiv.org/html/2609.14858v1), with the method and our additional
requirements explained in the [design](design.md#dream-rsi-exploration-and-replay).
After A–E works, use one controlled family to ask whether Dream-RSI-inspired
scheduling helps both a simpler knowledge workflow and the full Warranted workflow:

| Knowledge workflow | Fixed policy | Dream-RSI-inspired policy |
| --- | --- | --- |
| C | Executable-model baseline | Search improvement with the simpler workflow |
| E | Full knowledge workflow | Combined knowledge and search treatment |

Keep the comparisons distinct: C fixed versus C evolved measures search gains
with the simpler workflow; E fixed versus E evolved measures them with the full
workflow. Comparing their gains tests whether the two approaches complement each
other. Report these as results of Warranted's adaptation, with any departures from
the published method recorded in the experiment manifest.

Train a policy per workflow on the same training instances with equal optimisation
caps. Each policy sees only the observations and knowledge support allowed in that
condition. Never replay E outcomes as if they were generated under C context.
Use development data to select candidates; freeze the winner before final runs.

Begin with recorded, independently checked task outcomes and represented live cost
as the selection objective. Fix coefficients and tie-breaking on development data;
proof counts and self-reported confidence are not task success. Keep the incumbent
among candidate policies. Report unsupported transitions and use a fixed world set:
the initial policy-selection rule requires supported replay on every selected world
and cannot improve a score by silently dropping difficult worlds. If no candidate
qualifies, keep the deployed policy without claiming an improvement.

World manifests bind workspace, worker-visible context, retrieval results,
environment, model/settings, checker/contract versions, and per-attempt allowance.
Unknown or changed relevant inputs make a transition unsupported. Global remaining
budget belongs to the policy/host; changing it must not silently change worker
inputs. Active branches do not receive sibling discoveries. Share validated
knowledge at rollout boundaries and start a new world when shared context changes.

Test the full live → replay → live → expanded-history replay cycle. Offline replay
only navigates stored outcomes in recorded branch order. It cannot reveal future
results early, issue live operations, or create fresh authoritative evidence.
Fresh runs are required to establish whether a policy transfers beyond its history.

## Measurement and decision criteria

Preserve an immutable manifest for each run: task/contract/evaluator versions,
model and inference settings, worker and policy versions, initial context,
environment, limits, split, and outcome. Retain failures and interrupted runs.

Report task success, stale conclusions used, recovery correctness, repeated work,
human interventions, unknown outcomes, and total tokens/tool/proof work, elapsed
time, and cost. Record formalisation errors and interface/bookkeeping overhead.

Separate mistranslated or omitted requirements, regressions outside proved
properties, and proof/checker failures. Report cases where formal checks pass but
independent task acceptance fails; proof success is not task success. Record
contract revisions and unresolved gaps without treating them as passes of the
original contract.

For scheduling, also report replay coverage and mismatches between replay rankings
and live results, including regressions.

Include seed-history collection, policy-development inference, unsuccessful
revisions, replay CPU, development evaluation, and new live discovery in campaign
cost. Report live task cost separately and state how many tasks amortise the
one-time optimisation cost. Shared histories are charged once, not made free;
represented live costs in replay are selection signals, not actual new executions.
Measure concurrency gains live rather than inferring them from batch width.

Advance D/E only if their benefit justifies their cost against the simpler
conditions across both task families. Retain Lean only where it improves the
trade-off over D. Adopt evolved scheduling only on the strength of untouched
live results against its corresponding fixed baseline. If simpler conditions win,
simplify Warranted; do not adjust acceptance requirements to manufacture a gain.
