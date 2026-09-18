# Provisional roadmap

Updated 2026-09-18. These are ordered experiments and implementation slices, not
delivery dates. Only M0 is complete. Review the design when a milestone exposes
a simpler way to meet the requirements; preserve the independent comparisons.

| Milestone | Result | Depends on | Status |
| --- | --- | --- | --- |
| M0 | Reproducible repository scaffold | — | Complete |
| M1 | Inspectable evidence that survives restart | M0 | Next |
| M2 | Changed-premise recovery with explicit gates | M1 | Planned |
| M3 | Bounded worker and trustworthy operation recovery | M2 | Planned |
| M4 | Independently checked Lean obligations | M2; M3 for agent trials | Planned |
| M5 | Second task family and knowledge-workflow comparisons | M3, M4 | Planned |
| M6 | Dream-RSI adaptation: replay-based scheduling pilot | M5; recording begins in M1 | Planned |

## M0 — Repository foundation

- [x] Python package, uv configuration/lockfile, and help/version entry points.
- [x] README, contributor guidance, standalone design, and provisional roadmap.
- [x] CI for source checks, build, and installation smoke checks.

This is scaffolding only. No ledger, gate, agent integration, or replay capability
is claimed by successful packaging checks.

## M1 — Local evidence and restart

Build a local SQLite event store with one writer and artifact files. Use a
scripted task so persistence can be verified without a model or external service.
Start with observations, operation identity, raw results, and usage; add richer
claim semantics in M2.

- [ ] Commit events atomically and preserve immutable observation/artifact versions.
- [ ] Reopen a project in a fresh process and recover committed state.
- [ ] Deduplicate a completed operation by ID and input identity; reject collisions.
- [ ] Retain pending/unknown operations and resource reservations after interruption.
- [ ] Expose legible files or permitted read-only queries without giving workers
  access to the authoritative write path.
- [ ] Record world/parent identity, input/context versions, session boundaries,
  allowances, and costs needed for the Dream-RSI adaptation in M6. Do not build
  the replay engine yet.

**Completion evidence:** local tests demonstrate reopen durability, atomicity,
deduplication, changed-input rejection, and no double counting after a simulated
crash. A short scripted walkthrough exposes the recorded raw evidence.

**Suggested first three PRs:** event/artifact persistence and reopening; operation
receipts with recovery/accounting; read-only inspection plus the scripted walkthrough.
Each PR includes the negative cases for the behavior it introduces.

Use the [M1 scripted walkthrough](pilot.md#m1-scripted-walkthrough) to derive the
first records and interfaces. It specifies fixed outputs, expected history,
accounting totals, and interruption cases. The plan is not implemented evidence.
The [PR1 persistence plan](m1-persistence.md) proposes the records, host interface,
publication order, and focused tests for the first slice.

## M2 — Claims, applicability, and gates

Use the data-transformation fixture in [pilot.md](pilot.md). Implement the smallest
claim/dependency representation that can recover after one changed premise.

- [ ] Separate observations, assumptions, validation results, and current support.
- [ ] Propagate staleness to dependent applications while preserving unaffected work.
- [ ] Represent rules and gates distinctly; permit a recorded rule exception.
- [ ] Enforce gates against exact versions at the protected operation, including
  missing evidence, unknown applicability, and attempted bypass cases.
- [ ] Apply an independent deterministic completion check to the fixture.
- [ ] Preserve the request, clarifications, examples, and formal interpretations
  as linked versions; identify the contract owner, acceptance obligations, and
  known gaps as described in [the design](design.md#intent-contracts-and-acceptance).
- [ ] Exercise the data-transformation [specification failures](pilot.md#specification-and-behavioral-failures)
  with executable checks: narrow success cannot override a failed behavioral
  obligation, including after restart. Include a successful control.
- [ ] Retain contract revision provenance and prior outcomes; require reassessment
  against changed definitions and reject worker-authored weakening under an old
  contract identity.
- [ ] Resume after a forced fresh-process restart and revised input interpretation.

**Completion evidence:** a scripted run initially reaches a justified result,
receives contrary evidence, blocks stale reuse, and rebuilds only the affected
work. A rule exception never opens a gate. A forged or mismatched receipt fails.
Passing a narrow check cannot establish task completion while an independently
specified obligation fails. An authorised contract revision creates a new
acceptance decision without rewriting the old failure.

**Decision:** keep dependency capture conservative until its omissions can be
detected. Do not construct a general reasoning ontology to support one example.

## M3 — Bounded worker and execution recovery

Integrate an existing worker and workflow runner around the working host. Start
with mini-swe-agent and LangGraph, checking their current interfaces and pinning
versions at integration time. Keep the adapter replaceable and model access optional.

- [ ] Give the worker shell/files, an objective, permitted context, and a bounded
  workspace; capture mechanical provenance in the host.
- [ ] Enforce workspace/process isolation from the ledger writer and private checker.
- [ ] Reconcile workflow cursors with the ledger after interruption; use a fresh
  worker session when an unfinished session cannot be safely resumed.
- [ ] Reserve and charge all model/tool work, including failures and resumed runs.
- [ ] Introduce a minimal fixed exploration policy, initially with serial execution.
- [ ] Exercise lost-response recovery against a fake service with explicit receipts.

**Completion evidence:** an agent performs the changed-premise task across a
restart; deterministic integration tests verify worker isolation, deduplication,
preserved budgets, and blocked retry when the fake service cannot establish an
outcome. Local CI still requires no model credentials.

**Decision:** measure interface and bookkeeping overhead before adding specialised
tools. Checkpoint infrastructure does not replace operation-level receipts.

## M4 — Lean proof boundary

Formalise one reusable property from the first fixture: uniqueness preservation
under an injective identifier mapping. Reuse it for the incomplete-target case;
defer timestamp formalisation to M5. Keep empirical premises and the independent
task evaluator outside the theorem.

- [ ] Pin Lean, the allowed library/axioms, and a reviewed target declaration.
- [ ] Isolate proof generation; independently check the resulting artifact and
  transitive dependencies under the pinned verification policy.
- [ ] Bind receipts to exact artifacts/targets and reject weakened statements,
  `sorryAx`, unapproved axioms, and forged success labels.
- [ ] Keep a checked theorem valid when a premise becomes unsupported, while
  blocking its application to the changed input.
- [ ] Reuse the uniqueness theorem for the pilot's incomplete-target case: a
  candidate drops records while its output remains provably unique. The independent
  record-preservation check blocks task acceptance. Preserve proof success and
  task failure separately, alongside a successful control using the same theorem.
- [ ] Bound and account for proof work; record timeout/failure as unproved.

**Completion evidence:** one accepted conditional theorem with supported
applications to the incomplete-target case and its successful control, plus
negative tests for every acceptance boundary. Only the control passes task
acceptance. The negative case fails because uniqueness permits record loss, not
because its proof is invalid, its premises are unsupported, or its implementation
differs from its model. Separately demonstrate the premise change without claiming
a Lean proof verifies a separate Python program.

**Decision:** compare the additional cost with the executable-check baseline.
Formalisation should target useful obligations rather than all exploratory claims.

## M5 — Generality and the knowledge experiment

Add a controlled repository-migration fixture with changed requirements. It runs
in local copies and uses an independent acceptance checker; no remote publishing
or production writes are required.

- [ ] Complete the same restart/changed-premise exercise in this second task family.
- [ ] Exercise the pilot's migration regression outside the proved properties,
  retaining the declared compatibility obligations and a successful control.
- [ ] In condition E, add valid proofs for the timestamp-mistranslation and
  migration-regression cases, retaining M4's incomplete-target case and successful
  controls for all three.
- [ ] Derive the smallest shared adapter boundary from both working fixtures.
- [ ] Run the A–E conditions in [pilot.md](pilot.md) with fixed scheduling and
  equal worker capabilities, including frontier and small-model trials.
- [ ] Separate training/development/final tasks and preserve full failure records.
- [ ] Report task success, stale reuse, recovery, repeated work, and total cost.
- [ ] Report specification failures separately from proof/checker failures and
  distinguish rejecting known failures from discovering omitted requirements.

**Completion evidence:** reproducible runs on both families, versioned experiment
manifests, and untouched final results. Development runs set useful-improvement
thresholds before final evaluation.

**Decision:** if the log or executable-model baseline captures most of the benefit,
simplify the design. Retain Lean only where its contribution justifies its cost.
The core must remain independent of any pre-existing domain tool protocol.

## M6 — Dream-RSI adaptation for search improvement

Implement the [Dream-RSI](https://arxiv.org/html/2609.14858v1) adaptation described
in the [design](design.md#dream-rsi-exploration-and-replay), using one controlled
task family with stable, replay-compatible contexts. This milestone tests whether
the method adds value alongside Warranted's knowledge workflow. Its placement
after M5 isolates that question from the knowledge experiment; the recording
requirements already shape M1 and the fixed exploration policy begins in M3.

The policy interface serves live and offline executors, with all enforcement
outside policy code. Use the [Dream-RSI scheduling comparison](pilot.md#dream-rsi-scheduling-comparison)
to measure the adaptation against the fixed-policy baseline.

- [ ] Replay recorded histories without executing workers, live tools, or side effects.
- [ ] Reveal only available observations in recorded branch order; label missing or
  incompatible continuations unsupported, and report coverage.
- [ ] Keep branch context isolated and publish shared knowledge only at validated
  rollout boundaries. Changed context starts a new world.
- [ ] Restrict policy mutations to branching, continuation, batching, and stopping;
  freeze worker settings, checkers, contracts, and budget enforcement.
- [ ] Perform live collection → offline policy revisions → another live rollout →
  expanded-history optimisation. Freeze the selected policy for final evaluation.
- [ ] Compare C/E × fixed/evolved scheduling, retaining the original A–E study.
- [ ] Charge seed-history collection, policy development, failed revisions, replay,
  and fresh runs; report both campaign and amortised per-task costs.

**Completion evidence:** a complete bounded improvement cycle plus live results
against the fixed baseline. Negative cases cover future-outcome leakage, unsupported
branches, altered context, hidden evaluator access, counter resets, and replay writes.
Batch width alone is not evidence of a wall-clock speedup.

**Decision:** keep evolved scheduling only if untouched live results justify its
total cost. A successful replay score is not a deployment guarantee. Assess
Dream-RSI's upstream implementation for reuse at this milestone and document
whether we integrate it or implement the published method independently. In
either case, retain attribution and record departures from the method; upstream
availability is not a prerequisite for the small experiment.

## Later, only if justified

- Hindsight retrieval with versioned evidence links, untrusted recalled prose, and
  explicit handling of stale projections and circular corroboration.
- AutoSaddler or another optimiser for broader harness changes, separately measured
  from scheduling and outside the trusted acceptance contract.
- Additional domain adapters after the first two fixtures establish the boundary.
- Wider parallelism or distributed execution after local correctness and recovery.

## Decisions still open

Choose exact task instances and completion predicates in M1–M2; define the first
dependency-capture fallback; select concrete runner versions in M3 and the Lean
library/axiom policy in M4; set model/cost budgets and experimental thresholds before
M5. Define M6's policy objective, support rule, and lineage split before optimisation.
These decisions should become small reviewed changes, not a reason to build all
future infrastructure before the first working slice.
