# Design

Status: provisional, 2026-09-18. This describes intended behavior. See the
[roadmap](roadmap.md) for implementation status and the [pilot](pilot.md) for
the experiments that should determine which parts are worth keeping.

## Purpose

Help an agent finish interdependent work across sessions by preserving executable
artifacts, evidence, assumptions, and the reasons conclusions are applicable.
When an input changes, revisit the affected work instead of reconstructing the
entire argument from prose.

Test two hypotheses separately: checked, reusable knowledge improves completion
and recovery; replay-based search scheduling can add further gains at an acceptable
total cost. A fixed model should suffice for either experiment. Neither benefit
is assumed, and Lean must earn its cost over executable models and ordinary checks.

## Boundaries and ownership

| Component | Responsibility | Initial direction |
| --- | --- | --- |
| Host controller | Operation permissions, authoritative writes, budgets, recovery, acceptance | Small Python implementation with one ledger writer |
| Evidence ledger | Versioned observations, claims, dependencies, attempts, and receipts | SQLite plus content-addressed artifact files |
| Worker | Inspect permitted context, propose artifacts and investigations | Existing bounded coding worker; shell and files |
| Workflow runner | Dispatch and checkpoint bounded sessions | Evaluate LangGraph when the worker slice lands |
| Domain environment | Supply observations and execute permitted operations | Local, controlled fixtures first |
| Independent checker | Assess a specific artifact against a pinned contract | Deterministic task checks; later Lean proof checking |
| Exploration policy | Select branches to continue, branch out, batch, or stop | Fixed policy first; replay-improved policy later |

These are responsibility boundaries, not seven services or a mandatory class
hierarchy. Keep them in one package until working use cases require separation.
mini-swe-agent is the initial worker candidate. LangGraph and the worker supply
execution infrastructure; neither owns evidence semantics. Add and pin their
dependencies when integrating them, rather than building replacements now.

The worker's interface should remain small. Expose documented, searchable files
and permitted read-only state; allow scripts and normal command composition.
Host adapters record raw results, snapshots, and costs mechanically. Ask for
semantic dependencies or claim interpretations when they cannot be observed.
Avoid making the worker navigate a rigid sequence of bookkeeping forms.

Keep the authoritative database, receipts, and checker state outside the worker's
writable workspace. Read-only projections must respect visibility rules. A SQL
view or a prompt instruction alone is not an isolation boundary. Domain operations
that need mediation go through host-owned execution boundaries; shell access
cannot bypass them.

## Durable knowledge

Start from the smallest records needed by the next complete slice:

- Observations retain raw results, source operation, input versions, environment,
  and time. They establish what a source reported, not infallible truth.
- Artifacts retain content digests, reproducible input versions, and dependencies.
- Claims retain statements, assumptions, scope, and supporting/conflicting evidence.
- Validation receipts bind checker identity/version, target, artifact, input,
  environment, method, and outcome. Workers cannot author accepted receipts.
- Operations retain stable IDs, input identity, status, resource reservations,
  actual usage, and any attributable response.
- Decisions and policy versions link to the records and contracts they used.

Keep observations immutable; corrections create new records with lineage.
Keep a conditional theorem's checked status separate from support for applying
it to current inputs. Retracting a premise invalidates dependent applications,
not the theorem's logical derivation. Incomplete dependency capture requires a
conservative broader review rather than a claim of precise invalidation.

There are three distinct structures: the workflow execution/checkpoint graph,
the dependency graph between knowledge artifacts, and the discovery tree of
investigation attempts. Link them without treating one as a substitute for another.

## Rules, gates, and verification

A rule allows a recorded, scoped exception. A gate permits a protected transition
only after designated, independently checkable evidence is present. Gate definitions
include their applicability predicate, exact target, evidence requirements, and
checker. Unknown kinds, unknown applicability, missing evidence, and checker
failure do not default to permission.

Check the current input and dependency versions at acceptance, not merely when a
worker began. All entry paths, including resumes and direct commands, use the
same boundary. Neither the worker nor an optimiser may alter the objective,
acceptance contract, checker, gate classification, or enforcement to improve a
score. Contract-owner revisions are explicit new versions, not passes of old gates.

Formalisation is progressive. Begin with executable models and regression checks;
introduce Lean for obligations whose reuse or failure cost makes proofs worthwhile.
Pin the Lean toolchain, libraries, target, and permitted axioms. Isolate untrusted
elaboration/tactics and independently check the resulting artifact and transitive
dependencies, including indirect use of `sorryAx` or unapproved axioms.

A checked theorem establishes the stated proposition. It does not establish that
the proposition captures the user's requirement or that empirical premises hold.
A proof of a Lean function does not verify a separate implementation or parser.
An unsuccessful proof attempt is unproved, not a counterexample or a false claim.

## Recovery and accounting

The ledger is authoritative; checkpoints carry references and execution cursors.
Commit related events and dependency updates transactionally. Reconcile a stale
checkpoint from committed events and deduplicate operations by stable ID and
input identity. Reusing an ID with different inputs is an error.

Reserve resources before dispatch, then reconcile actual usage. Reservations for
unknown outcomes survive restart, and branch creation does not reset budgets.
Record external attempts before issuing them. When a response is lost, use a
receipt or an adapter-supported observation to reconcile the specific operation.
If it cannot be established, retain an unknown outcome and block automatic retry.
Do not promise exactly-once effects from arbitrary external APIs.

Use a controlled fake service to test these cases before real external writes.
Keep pure recomputation, historical replay, and retries of live operations distinct.

## Exploration and replay

Design recording for replay early, but establish fixed-policy behavior first.
The policy receives only currently permitted observations and host-owned limits;
it requests a bounded batch of eligible root/leaf continuations or stops. Stopping
search does not establish task completion. Policy code cannot access live tools,
the full unrevealed trace, private evaluator data, or enforcement state directly.

Each investigation has a pinned workspace, worker-visible context, configuration,
environment, and per-investigation allowance. It inherits only its branch history.
Keep global remaining resources in the host/policy view. Share results at explicit
rollout boundaries after validation; a changed shared context starts a new world.
Relevant external changes end the affected world and trigger normal invalidation.

Replay reveals recorded children in parent order; root selections reveal new
branches in their recorded order. A missing continuation, lost response, or
incompatible context is unsupported. Replay cannot infer what an untried action
would produce, rerun live tools, or add fresh evidence to the project ledger.
Its evaluation records refer to existing evidence without adding corroboration.

Only scheduling code evolves in the pilot. Freeze worker prompts, model settings,
retrieval, tool behavior, checker, and task contract within compatible worlds.
Train on authorised histories, select on development data, then freeze the policy
for untouched tasks. Treat replay score as a selection signal, never a guarantee
of live improvement. Preserve the fixed-policy baseline and account for all costs.

## Scope and provisional choices

The initial scope is a local host, one ledger writer, bounded workers, and two
controlled task families. There is no commitment to distributed coordination,
unrestricted self-modification, weight training, a universal ontology, or automatic
real-world writes. A full simulator of arbitrary environments is not assumed.

SQLite, Python, uv, and ordinary artifacts are the starting implementation choices.
The exact schema, worker adapter, workflow wiring, and proof library remain
provisional. Introduce shared domain interfaces after two working fixtures expose
what is common. Evidence semantics must not inherit a particular tool API.

Hindsight may later help retrieval through provenance-linked projections; recalled
text remains untrusted and cannot satisfy a gate on its own. AutoSaddler may later
optimise broader harness choices. Keep both outside the first pilot comparisons.

## Sources and provenance

This standalone design derives from the
[merged long-horizon proposal](https://github.com/Lewdwig-V/reschema/blob/42612d1c24f1dd063f8b021e346f3c23c8affbca/docs/proposals/long-horizon-reasoning-harness.md).
That document is historical context; this repository's design and roadmap govern
Warranted. No interface or runtime dependency on its originating project is implied.

- [Dream-RSI](https://arxiv.org/html/2609.14858v1) motivates the scheduling experiment;
  use its published method without assuming an available integration.
- [Vercel's tool-reduction case study](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools)
  motivates a small worker interface, not a universal performance claim.
- [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview),
  [mini-swe-agent](https://mini-swe-agent.com/latest/), and
  [Lean](https://lean-lang.org/doc/reference/latest/) are integration candidates;
  check and pin concrete versions at the relevant milestone.
