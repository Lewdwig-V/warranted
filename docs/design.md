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
and recovery; Dream-RSI-inspired search scheduling can add further gains at an
acceptable total cost. A fixed model should suffice for either experiment.
Neither benefit is assumed, and Lean must earn its cost over executable models
and ordinary checks.

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
[mini-swe-agent](https://mini-swe-agent.com/latest/) is the initial worker candidate:
its small, shell-based agent loop fits bounded investigation sessions.
[LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) supplies
workflow persistence and resumption. We intend to build on those projects'
execution infrastructure while the Warranted host owns evidence semantics.
Add and pin their dependencies when integrating them.

The preference for a small worker interface also draws directly on
[Vercel's d0 case study](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools).
Their revised agent explored documented files through shell commands and retained
a SQL execution tool. For Warranted, the lesson is to make the environment legible
and give the worker general ways to inspect it. Expose documented, searchable
files and permitted read-only state; allow scripts and normal command composition.
Host adapters record raw results, snapshots, and costs mechanically. Ask for
semantic dependencies or claim interpretations when they cannot be observed.
Avoid making the worker navigate a rigid sequence of bookkeeping forms.
The case study motivates this choice; our pilot must measure its costs and benefits
on Warranted's tasks.

Keep the authoritative database, receipts, and checker state outside the worker's
writable workspace. Read-only projections must respect visibility rules. A SQL
view or a prompt instruction alone is not an isolation boundary. Domain operations
that need mediation go through host-owned execution boundaries; shell access
cannot bypass them.

## Durable knowledge

[Schema](https://schema-harness.github.io/) and
[PRO-LONG](https://arxiv.org/html/2607.20064v2) are explicit influences, carried
forward from the [original proposal](#sources-and-provenance). Schema motivates
representing learned world behavior as executable models checked against observed
transitions. PRO-LONG motivates preserving complete interaction histories that
an agent can search programmatically. Their ARC-AGI-3 work inspired this project;
transfer to our task families remains an experimental question.

Warranted's proposed extension ties these artifacts and histories to explicit
assumptions, dependencies, and acceptance receipts. The pilot separates history,
executable models, dependency tracking, and Lean to measure what each contributes.

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
introduce [Lean](https://lean-lang.org/doc/reference/latest/) for obligations whose
reuse or failure cost makes proofs worthwhile. Lean provides the formal language
and proof-checking foundation; Warranted must connect checked statements to the
current evidence and task requirements.
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

## Dream-RSI: exploration and replay

The search-improvement pilot is a planned adaptation of Tong Zheng and colleagues'
[Dream-RSI: Recursive Self-Improvement through Evolving Worlds](https://arxiv.org/html/2609.14858v1),
especially Sections 2–3. Its central contribution is to use recorded discovery trees
as replay worlds for evaluating exploration policies. A policy-development agent
revises scheduling code using replay feedback; the selected policy guides another
live rollout, expanding the history available for the next improvement round.
The discovery model remains fixed. This is the source of our proposed recursive
search-strategy improvement loop.

Warranted asks how that method works alongside durable, checked project knowledge.
Our adaptation adds the context-compatibility, evidence, gate, and accounting
requirements below. They define the Warranted pilot's contract. Shared knowledge
can change an investigation's premises, so recording when that knowledge becomes
visible is essential to deciding whether a historical continuation is usable.
This is a proposed application of the published method; implementation reuse and
benefits on our tasks still need evaluation.

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

[Hindsight](https://hindsight.vectorize.io/) is a later candidate for memory
retrieval, consolidation, and reflection. We would connect its recalled memories
to versioned evidence and artifact records; recalled text remains untrusted and
cannot satisfy a gate on its own.

[AutoSaddler](https://github.com/microsoft/AutoSaddler) is a later candidate for
trace-driven changes to prompts, tools, and other harness components. That wider
mutation surface deserves a separate experiment from the Dream-RSI scheduling
pilot. Keep both Hindsight and AutoSaddler outside the first pilot comparisons.

## Sources and provenance

This standalone design derives from the
[merged long-horizon proposal](https://github.com/Lewdwig-V/reschema/blob/42612d1c24f1dd063f8b021e346f3c23c8affbca/docs/proposals/long-horizon-reasoning-harness.md).
That document is historical context; this repository's design and roadmap govern
Warranted. No interface or runtime dependency on its originating project is implied.

The project also grew out of ReSchema's reverse-engineering work and its use of
validated executable artifacts. Keeping Warranted's core independent preserves
that history while letting new task families determine its interfaces.

| Source | Influence and intended use | Where developed here |
| --- | --- | --- |
| [Schema](https://schema-harness.github.io/) | Executable world models checked against observations | [Durable knowledge](#durable-knowledge) |
| [PRO-LONG, v2](https://arxiv.org/html/2607.20064v2) | Complete, programmatically searchable interaction history | [Durable knowledge](#durable-knowledge) |
| [Dream-RSI, v1](https://arxiv.org/html/2609.14858v1) | Published method adapted for the planned scheduling experiment | [Exploration and replay](#dream-rsi-exploration-and-replay) |
| [Vercel's d0 case study](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools) | Legible files and a small worker interface | [Boundaries and ownership](#boundaries-and-ownership) |
| [mini-swe-agent](https://mini-swe-agent.com/latest/) | Candidate worker implementation | [Boundaries and ownership](#boundaries-and-ownership) |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | Candidate workflow and persistence implementation | [Boundaries and ownership](#boundaries-and-ownership) |
| [Lean](https://lean-lang.org/doc/reference/latest/) | Formal language and proof-checking foundation | [Rules, gates, and verification](#rules-gates-and-verification) |
| [Hindsight](https://hindsight.vectorize.io/) | Later memory integration candidate | [Scope and provisional choices](#scope-and-provisional-choices) |
| [AutoSaddler](https://github.com/microsoft/AutoSaddler) | Later harness-optimisation candidate | [Scope and provisional choices](#scope-and-provisional-choices) |

Credit the source where its idea is introduced, explain our adaptation, and retain
the citation when refactoring the design. Check and pin implementation versions
at the relevant milestone; research inspiration does not imply an integration
already exists or that upstream results have been reproduced here.
