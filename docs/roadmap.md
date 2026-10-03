# Roadmap

Updated 2026-10-02. Milestones are ordered, not dated. M0–M4 are complete within
the scopes stated below. The current priority is turning Warranted into a usable
harness with ReSchema as its first consumer: a stable API and CLI (M7), then
ReSchema rebuilt on top of them (M8). M5's remaining comparison, M6, and M3a are
deferred until M8 lands.

| Milestone | Result | Status |
| --- | --- | --- |
| M0 | Reproducible repository scaffold | Complete |
| M1 | Inspectable evidence that survives restart | Complete: local, single writer |
| M2 | Changed-premise recovery with explicit gates | Complete: trusted local fixture |
| M3 | Bounded worker and trustworthy operation recovery | Complete: scripted model |
| M4 | Independently checked Lean obligations | Complete: fixed proof proposals |
| M5 | Second task family and A–E knowledge-workflow comparison | Fixtures complete; comparison deferred |
| M7 | Stable harness API and CLI | Next |
| M8 | ReSchema rebuilt on Warranted | Planned, after M7 |
| M6 | Dream-RSI replay-based scheduling | Deferred |
| M3a | Optional Jev classifier and judge | Deferred |

"Complete" means the milestone's completion evidence was demonstrated in tests
and CI on its fixture. It does not extend to live models, untrusted hosts, or
tasks outside the fixtures.

## M0 — Repository foundation

Python package, uv lockfile, help/version entry point, contributor guidance, and
CI for lint, formatting, build, and wheel installation. Packaging checks claim
no runtime behavior.

## M1 — Local evidence and restart

**Delivered:** a SQLite ledger with SHA-256 artifact files and one writer.
Observations are immutable. Operations have stable identities; completed results
are reused without another charge, and identity collisions are rejected.
Reservations and unknown outcomes survive restart and block blind retry.
Selected exports give inspectors copies without access to the authoritative store.
The ledger records the world, session, input, and allowance identities that later
replay would need. No replay engine exists.

**Evidence:** process-termination tests and a two-process CSV walkthrough
([persistence](reference/evidence-ledger.md), [walkthrough](../README.md#run-the-m1-walkthrough)).

**Limits:** durability covers process termination on a working local filesystem,
not power or disk loss. The storage format is version 2 with no migration from
version 1.

## M2 — Claims, applicability, and gates

**Delivered:** claims that separate assertions, assumption versions, historical
checks, and current support. Staleness propagates through declared dependencies;
incomplete capture is reported as unknown. Gates and rule exceptions are distinct,
and the protected acceptance operation checks exact current versions. Owner-approved
contract revisions retain the old outcome and require reassessment.

**Evidence:** three fixture experiments: selective rebuilding, a changed definition
with identical output, and independent failures that a narrow success cannot
override ([CSV fixture](fixtures/csv-transformation.md#m2-fixture-experiments), [claims](reference/claims-and-acceptance.md#claims),
[acceptance](reference/claims-and-acceptance.md#acceptance)).

**Limits:** one trusted, serialized host. Approvals are pinned local fixture data;
remote owner authentication and external-effect authorization do not exist.

## M3 — Bounded worker and execution recovery

**Delivered:** mini-swe-agent 2.4.6 runs inside a serial LangGraph 1.2.11 graph
with the SQLite checkpointer 3.1.1; the ledger, not the checkpoint, is
authoritative. Generated commands run in a rootless Podman container with no host
mounts, network, credentials, or ledger access. All model, tool, and checker work
is reserved and charged. A loopback fake service tests lost responses: exact
receipts settle usage, and missing receipts leave the attempt blocked.

**Evidence:** the changed-premise CSV task runs across a forced host kill and
repeated resumes, with unchanged independent gates and costs
([M3 demonstration](fixtures/csv-transformation.md#m3-changed-premise-demonstration)).

**Limits:** the model is scripted. The host, kernel, and container runtime are
trusted.

## M4 — Lean proof boundary

**Delivered:** pinned Lean, Comparator, and Landrun check a reviewed uniqueness
target, reject weakened statements, `sorryAx`, and unapproved axioms, and replay
the proof in the kernel. Durable proof receipts bind exact inputs and record
timeouts and failures as unproved. A changed premise blocks the theorem's
application without invalidating it.

**Evidence:** the same theorem applies to correct, record-dropping, and empty
candidates; only the correct candidate passes the task gates. Native tests kill
the host before application and resume twice; the second resume adds no verifier
or checker executions ([verification](reference/proof-verification.md), [fixture](fixtures/csv-transformation.md#m4-uniqueness-application)).

**Limits:** proofs are fixed proposals. Model proof search and a cost comparison
against executable checks have not been run.

## M7 — Stable harness API and CLI

**Goal:** a public, versioned Python API and a `warranted` CLI that a domain
project can build on without importing internal modules. ReSchema is the driving
consumer; the CSV and migration fixtures must move onto the same API, so every
interface has three users before it is called stable.

Warranted will own the neurosymbolic machinery ReSchema currently implements
itself: task state, the ledger and accounting, gate enforcement and private
checker data, contained execution, and verified versus unverified memory. The
domain project supplies only domain knowledge. Specifically:

- [ ] **Public API.** One documented import surface for projects, task
  definitions, runs, status, claims, and exports, with typed results that keep
  rejected, unproved, unsupported, unknown, and infrastructure failure distinct.
  Everything else becomes private.
- [ ] **Checker interface.** A domain checker assesses captured candidate bytes
  in a contained job and returns a typed verdict plus worker-visible feedback.
  The host keeps private inputs and seeds out of the worker, records them as
  evidence, and issues the receipt. Fresh per-submission inputs, such as
  ReSchema's hidden cases, are drawn and recorded by the host. A submission may
  nominate cases; the checker recomputes their ground truth itself, so anything
  the worker recorded stays a hint, never evidence.
- [x] **Domain worker images.** A domain project supplies a pinned worker image
  with its own tools, such as an emulator and the target binary, so the worker
  can investigate with ordinary shell commands. The image adds tools inside the
  container; it does not widen what the container can reach. The prototype task
  layer runs workers in the domain's `worker_image`, pinned by digest or local
  image ID ([reference](reference/worker-and-containment.md#container-sandbox));
  only the default Python image is exercised in CI.
- [x] **Host-mediated operations: design, then implement.** Some operations
  belong on the host: results that should be authoritative, reusable evidence;
  anything needing credentials, network, paid APIs, or private data; and any
  external side effect. Write and review a design before implementing it. The
  design must cover how a worker requests an operation without network or a
  socket to the host, operation identity and deduplication, reservation before
  execution, unknown outcomes after a host death with no blind retry, what the
  worker sees back, and negative cases for forged requests and budget bypass.
  M8 does not wait for this item. Implemented from the accepted
  [design](proposals/m7-host-operations.md) in `warranted.operations` and the
  prototype task layer, with its negative cases
  ([reference](reference/worker-and-containment.md#host-mediated-operations)).
- [x] **Domain execution jobs.** Domain code can run compile, emulation, or
  native jobs in pinned images through Warranted's container boundary, mounting
  only per-job scratch. Worker-readable mounts never include oracle or ledger
  state. Checkers and operations call `run_job` with per-job limits and no host
  mounts ([reference](reference/worker-and-containment.md#checker-jobs)); no
  compiler toolchain image has been run in CI yet.
- [x] **Run-scoped budgets and blocking.** Each run has a ledger scope with caps
  under the project total; unknown operations and breaches block only their own
  run, enforced in `Ledger.begin` ([design](proposals/m7-run-scopes.md),
  [reference](reference/evidence-ledger.md#scopes)).
- [ ] **Token budgets.** Make prompt and completion tokens reserved ledger units
  so that a run's token budget is enforced like model, tool, and check units
  ([design](proposals/m7-token-budgets.md),
  [reference](reference/model-adapters.md#token-budgets)). Enforcement is
  implemented. The item completes when a first model is verified by a recorded
  measurement; until then no adapter reserves tokens.
- [x] **Submission policy.** Budgets for probes and submissions, and a
  repeated-candidate guard using a domain-supplied normalisation, enforced by
  the host rather than the worker. Probes are budgeted as
  [operation units](reference/worker-and-containment.md#host-mediated-operations),
  submissions by the task, and repeats by the task's
  [duplicate guard](proposals/m7-harness-api.md#budgets-and-the-duplicate-guard).
- [x] **Scoped memory.** Claims scoped to a family of related tasks, with
  receipt-backed facts kept separate from worker notes. A note is promoted only
  when its own submission is accepted. Selected facts reach later tasks as
  context files ([design](proposals/m7-scoped-memory.md)). Implemented in the
  prototype task layer; the RE-style fixture across restart is still part of
  M7's completion evidence.
- [ ] **CLI.** Commands to initialise a project, run and resume a task, report
  status and accounting, export evidence, and run a pinned campaign of tasks. The
  CLI uses only the public API.
- [ ] **Release discipline.** Tagged releases with a changelog, semantic
  versioning, and a deprecation policy for the public API; reference docs
  generated from or tested against it.

**Completion evidence:** both existing fixtures run through the public API and
CLI with their current guarantees and tests intact. A minimal reverse-engineering
style fixture exercises checker private inputs, worker-nominated cases, a domain
worker image, the repeated-candidate guard, and scoped memory across restart,
with negative cases for leaked private inputs, forged verdicts, and bypassed
budgets. Host-mediated operations are complete when their reviewed design is
implemented with its negative cases.

**Constraint:** the core contains no reverse-engineering concepts. Traces,
canonicalisation, emulation, and fuzzing stay in ReSchema behind the checker,
job, and worker-image interfaces.

## M8 — ReSchema rebuilt on Warranted

**Goal:** ReSchema depends on a pinned Warranted release and keeps only the
reverse-engineering task: corpus generation, ground-truth recording,
canonicalisation, the replay and differential-fuzz checkers, disassembly facts,
and task presentation.

- [ ] ReSchema pins a tagged Warranted release as a uv git dependency; publish to
  PyPI once the API reaches 1.0.
- [ ] Replace ReSchema's task ledger, counters, audit seeds, and journal with the
  Warranted ledger and accounting.
- [ ] Re-express the program and function gates as Warranted checkers, with hidden
  inputs and fuzz seeds held as private checker data.
- [ ] Run compile, emulation, and native jobs through Warranted's container
  boundary in place of ReSchema's own Podman driver.
- [ ] Replace the family deduction cache with Warranted scoped claims.
- [ ] Replace the five MCP tools and the external agent runner with Warranted's
  shell-and-files worker: task files in the workspace, an `experiment` command
  inside a ReSchema worker image, and the submission convention. Probes are then
  charged as shell commands rather than counted separately.
- [ ] Once host-mediated operations exist, move `experiment` onto them to make
  probe results authoritative evidence and restore per-probe accounting.
- [ ] Port ReSchema's live-agent campaigns to Warranted campaigns.

**Completion evidence:** ReSchema's existing gate regression tests pass against
the Warranted-backed implementation, with the same accept and reject decisions on
recorded cases. Its isolation regressions, including the scratch-mount and
host-write cases, still hold. A live-agent campaign completes through the CLI with
full accounting.

**Decision:** any capability ReSchema needs that only makes sense for reverse
engineering stays in ReSchema. If M8 shows an M7 interface does not fit, revise
the interface before 1.0 rather than adding a ReSchema-specific path.

## M5 — Generality and the knowledge experiment

**Delivered:**

- A configuration-migration fixture with a preserved legacy consumer, an approved
  safe-repetition requirement, contained candidate execution, and an independent
  checker under both contracts ([fixture](fixtures/config-migration.md)).
- Recovery through the approved revision and a host kill with a fixed worker.
- Migration and timestamp proof cases alongside M4 uniqueness, each with a
  successful control and an independent task failure ([proofs](fixtures/config-migration.md#proof-cases)).
- A shared context boundary and an A–E treatment runner across both families
  ([contexts](reference/contexts.md)).
- Pinned trial plans and reports that keep missing runs and failures
  ([trial accounting](experiments/evaluation-design.md#trial-plans-and-accounting)).
- Opt-in adapters for local Ollama and OpenRouter models with token and cost
  receipts ([local probe](reference/model-adapters.md#local-openai-compatible-endpoint),
  [OpenRouter](reference/model-adapters.md#openrouter)).

**Live-model findings so far:** development diagnostics only, on migration
condition A. Qwen and GLM did not submit under the original instructions. Explicit
packaging instructions and a submission helper let most Qwen runs submit accepted
payloads, but accepted programs still missed an unchecked boolean case
([submission controls](experiments/2026-09-24-submission-controls.md),
[corrected controls](experiments/2026-09-25-corrected-submission-controls.md)). No live run has reached the
revision and restart stage.

**Remaining (deferred until M8):**

- [ ] Give the normal A–E runner the explicit submission contract, then show a
  live model completing the revision and restart sequence.
- [ ] Add the boolean-version regression case as a separately versioned
  evaluator change.
- [ ] Run A–E with fixed scheduling and equal worker capabilities, including
  frontier and smaller models.
- [ ] Separate development and held-out tasks by lineage; keep all failure records.
- [ ] Report task success, stale reuse, recovery, repeated work, and total cost.
- [ ] Report specification failures separately from proof and checker failures.

**Decision:** if the history or executable-model conditions capture most of the
benefit, simplify the design. Keep Lean only where its contribution justifies its
cost.

## M6 — Dream-RSI adaptation for search improvement

Deferred until M8; not started. The plan adapts [Dream-RSI](https://arxiv.org/html/2609.14858v1) to
evolve only the scheduling policy using replayed discovery trees, with the model,
checkers, contracts, and budgets frozen, and compares it with the fixed policy on
untouched live tasks. It depends on M5. M1's ledger already records the identities
replay would need; nothing replays them yet. See the
[design](design.md#dream-rsi-exploration-and-replay) for the requirements we expect
to carry forward.

## M3a — Jev as a System 1 classifier and judge

Deferred until M8; not started and optional. The idea is to evaluate
[TypeSafe's Jev](https://docs.typesafe.ai/introduction) for routing and for
contract-designated judgment gates, first in shadow mode and outside the A–E and
Dream-RSI comparisons. See the [design](design.md#jev-system-1-classification-and-judgment).

## Later, only if justified

- Hindsight retrieval tied to versioned evidence.
- AutoSaddler or another optimiser for broader harness changes.
- Further domain adapters, parallelism, or multiple writers.

## Open decisions

For M7: the public module layout and naming; the host-mediated operation design,
including its request convention; how checker verdicts expose worker-visible feedback
without revealing private inputs; and the campaign file format. For M8: whether
ReSchema's efficiency metric becomes a Warranted report or stays domain code,
given that probes are charged as shell commands until host-mediated operations
exist.

Before measured M5 trials: choose provider/model versions and a total spending
cap; pin equal capabilities, scheduling, per-attempt limits, and task-success
predicates across A–E; set useful-improvement thresholds on development runs and
freeze them; define task lineages for the held-out split. Before M6: define the
policy objective, support rule, and lineage split.

Earlier milestone checklists and plans remain in Git history.
