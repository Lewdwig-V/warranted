# Roadmap

Updated 2026-10-02. Milestones are ordered experiments, not delivery dates.
M0–M4 are complete within the scopes stated below. M5 has its fixtures,
treatments, and live-model adapters, but no measured comparison. M6 and the
optional M3a have not started.

| Milestone | Result | Status |
| --- | --- | --- |
| M0 | Reproducible repository scaffold | Complete |
| M1 | Inspectable evidence that survives restart | Complete: local, single writer |
| M2 | Changed-premise recovery with explicit gates | Complete: trusted local fixture |
| M3 | Bounded worker and trustworthy operation recovery | Complete: scripted model |
| M4 | Independently checked Lean obligations | Complete: fixed proof proposals |
| M5 | Second task family and A–E knowledge-workflow comparison | In progress: no comparison yet |
| M6 | Dream-RSI replay-based scheduling | Not started |
| M3a | Optional Jev classifier and judge | Not started |

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
([persistence](m1-persistence.md), [walkthrough](../README.md#run-the-m1-walkthrough)).

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
override ([pilot](pilot.md#m2-fixture-experiments), [claims](m2-claims.md),
[acceptance](m2-acceptance.md)).

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
([M3 demonstration](m3-adoption.md#changed-premise-demonstration)).

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
or checker executions ([verification](m4-verification.md), [fixture](m4-fixture.md)).

**Limits:** proofs are fixed proposals. Model proof search and a cost comparison
against executable checks have not been run.

## M5 — Generality and the knowledge experiment

**Delivered:**

- A configuration-migration fixture with a preserved legacy consumer, an approved
  safe-repetition requirement, contained candidate execution, and an independent
  checker under both contracts ([fixture](m5-fixture.md)).
- Recovery through the approved revision and a host kill with a fixed worker.
- Migration and timestamp proof cases alongside M4 uniqueness, each with a
  successful control and an independent task failure ([proofs](m5-proofs.md)).
- A shared context boundary and an A–E treatment runner across both families
  ([contexts](m5-contexts.md)).
- Pinned trial plans and reports that keep missing runs and failures
  ([trial accounting](m5-migration.md#scripted-trial-accounting)).
- Opt-in adapters for local Ollama and OpenRouter models with token and cost
  receipts ([local probe](m5-migration.md#local-model-probe),
  [OpenRouter](m5-openrouter.md)).

**Live-model findings so far:** development diagnostics only, on migration
condition A. Qwen and GLM did not submit under the original instructions. Explicit
packaging instructions and a submission helper let most Qwen runs submit accepted
payloads, but accepted programs still missed an unchecked boolean case
([submission controls](m5-submission-controls.md),
[corrected controls](m5-corrected-submission.md)). No live run has reached the
revision and restart stage.

**Remaining:**

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

Not started. The plan adapts [Dream-RSI](https://arxiv.org/html/2609.14858v1) to
evolve only the scheduling policy using replayed discovery trees, with the model,
checkers, contracts, and budgets frozen, and compares it with the fixed policy on
untouched live tasks. It depends on M5. M1's ledger already records the identities
replay would need; nothing replays them yet. See the
[design](design.md#dream-rsi-exploration-and-replay) for the requirements we expect
to carry forward.

## M3a — Jev as a System 1 classifier and judge

Not started and optional. The idea is to evaluate
[TypeSafe's Jev](https://docs.typesafe.ai/introduction) for routing and for
contract-designated judgment gates, first in shadow mode and outside the A–E and
Dream-RSI comparisons. See the [design](design.md#jev-system-1-classification-and-judgment).

## Later, only if justified

- Hindsight retrieval tied to versioned evidence.
- AutoSaddler or another optimiser for broader harness changes.
- Further domain adapters, parallelism, or multiple writers.

## Open decisions

Before measured M5 trials: choose provider/model versions and a total spending
cap; pin equal capabilities, scheduling, per-attempt limits, and task-success
predicates across A–E; set useful-improvement thresholds on development runs and
freeze them; define task lineages for the held-out split. Before M6: define the
policy objective, support rule, and lineage split.

Earlier milestone checklists and plans remain in Git history.
