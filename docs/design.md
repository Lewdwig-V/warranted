# Design

Updated 2026-10-02. This document states the invariants Warranted enforces and
the reasons for them. Each section says what is implemented. The
[reference docs](reference/) describe how to use each component, the
[roadmap](roadmap.md) gives status, and the [evaluation design](experiments/evaluation-design.md)
describes the experiments. Jev and the Dream-RSI scheduling experiment are not built.

## Purpose

Warranted pairs a neural worker, which proposes plans, programs, and proofs, with
symbolic machinery the worker cannot override: a versioned ledger, declared
dependencies, deterministic checkers, and Lean verification. The goal is to help
an agent finish interdependent work across sessions by preserving executable
artifacts, evidence, assumptions, and the reasons conclusions are applicable.
When an input changes, revisit the affected work instead of reconstructing the
entire argument from prose.

Test two hypotheses separately: checked, reusable knowledge improves completion
and recovery; Dream-RSI-inspired search scheduling can add further gains at an
acceptable total cost. A fixed model should suffice for either experiment.
Neither benefit is assumed, and Lean must earn its cost over executable models
and ordinary checks.

Neither hypothesis has been tested yet. Both experiments are deferred while
Warranted gains a stable API and CLI and ReSchema is rebuilt on it (see the
[roadmap](roadmap.md#m7--stable-harness-api-and-cli)). The A–E knowledge comparison
then comes before the scheduling experiment.

## Boundaries and ownership

| Component | Responsibility | Current implementation |
| --- | --- | --- |
| Host controller | Operation permissions, authoritative writes, budgets, recovery, acceptance | Python modules with one trusted ledger writer |
| Evidence ledger | Versioned observations, claims, dependencies, attempts, and receipts | SQLite plus SHA-256-addressed artifact files (`warranted.host.Ledger`, `warranted.host.Claims`) |
| Worker | Inspect permitted context, propose artifacts and investigations | mini-swe-agent 2.4.6 with shell and files in rootless Podman |
| Workflow runner | Dispatch and checkpoint bounded sessions | LangGraph 1.2.11 with a separate SQLite checkpoint database |
| Domain environment | Supply observations and execute permitted operations | Two local fixtures: CSV transformation and configuration migration |
| Independent checker | Assess a specific artifact against a pinned contract | Deterministic fixture checkers and Lean verification; no model-judgment gates |
| Exploration policy | Select branches to continue, branch out, batch, or stop | One fixed serial policy; no replay or evolved policy |

These are responsibility boundaries, not seven services or a mandatory class
hierarchy. Keep them in one package until working use cases require separation.
[mini-swe-agent](https://mini-swe-agent.com/latest/) supplies the initial worker:
its small, shell-based agent loop fits bounded investigation sessions.
[LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) supplies
workflow persistence and resumption. Warranted uses those projects' execution
infrastructure while the host owns evidence semantics.
The [worker adoption notes](reference/worker-and-containment.md) record the integration boundary and
pinned versions. The local demonstration uses a scripted model, native rootless shell
containment, and independent acceptance to test recovery across a changed premise.

The preference for a small worker interface also draws directly on
[Vercel's d0 case study](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools).
Their revised agent explored documented files through shell commands and retained
a SQL execution tool. For Warranted, the lesson is to make the environment legible
and give the worker general ways to inspect it. Expose documented, searchable
files and permitted read-only state; allow scripts and normal command composition.
Host adapters record raw results, snapshots, and costs mechanically. Ask for
semantic dependencies or claim interpretations when they cannot be observed.
Avoid making the worker navigate a rigid sequence of bookkeeping forms.
The case study motivates this choice; its costs and benefits on Warranted's
tasks have not yet been measured.

Keep the authoritative database, receipts, and checker state outside the worker's
writable workspace. The rootless container has no host mounts, network, or
credentials, and the host captures candidate files after stopping the worker.
Read-only projections must respect visibility rules. A SQL
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

Warranted's extension ties these artifacts and histories to explicit
assumptions, dependencies, and acceptance receipts. The A–E conditions
separate history, executable models, dependency tracking, and Lean to measure
what each contributes; that comparison has not been run.

The ledger, claims, acceptance, and proof-receipt modules implement these records
for one trusted writer:

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
Dependencies are declared by the trusted host; they are not inferred.

There are three distinct structures: the workflow execution/checkpoint graph,
the dependency graph between knowledge artifacts, and the discovery tree of
investigation attempts. Link them without treating one as a substitute for another.

## Rules, gates, and verification

A rule allows a recorded, scoped exception. A gate permits a protected transition
only after designated, independently checkable evidence is present. Gate definitions
include their applicability predicate, exact target, evidence requirements, and
checker. Unknown kinds, unknown applicability, missing evidence, and checker
failure do not default to permission.

The implemented gates use deterministic fixture checkers and Lean verification.
A gate need not be deductive: a contract owner could designate a model judge for
a statistical criterion such as rubric-based quality, with the host obtaining
the judge's attributable result under a pinned policy. No such gate exists yet.
Independence means the candidate cannot control the checker or manufacture its
receipt, not that two models' errors are necessarily uncorrelated.

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
The [verification boundary](reference/proof-verification.md) pins the Lean toolchain, target,
and permitted axioms, isolates untrusted elaboration and tactics, and uses
Comparator to check the resulting artifact and its transitive dependencies,
including indirect use of `sorryAx` or unapproved axioms.

A checked theorem establishes the stated proposition. It does not establish that
the proposition captures the user's requirement or that empirical premises hold.
A proof of a Lean function does not verify a separate implementation or parser.
An unsuccessful proof attempt is unproved, not a counterexample or a false claim.

## Intent, contracts, and acceptance

Formalisation can enforce chosen properties without establishing that those
properties express the intended or complete requirement. An implementation can
also preserve every proved property while changing behavior the user expected
to keep. Treat these as distinct failure modes when defining acceptance.

Preserve the original request, subsequent clarifications, reference examples,
assumptions, and declared behavior to preserve alongside each versioned formal
interpretation. Link obligations to their source requirements and record known
gaps or unresolved ambiguities. Use ordinary documents and existing artifact and
dependency records; do not require a new specification language or bookkeeping
interface. A generated interpretation or explanation is a proposal, not authority
to replace the request. The contract owner approves targets and the evidence
required for acceptance.

Keep three assessments distinct: whether a proposition has a valid proof,
whether its premises and implementation correspondence support its current use,
and whether the candidate satisfies the current task's acceptance obligations.
A receipt establishes only its stated scope. A valid proof cannot override a
failed behavioral check or discharge an unrelated obligation. Missing or unknown
required evidence remains blocking. Acceptance means the pinned contract's
requirements were met; it does not certify that the contract captures every
user need. Preserve known limitations in the acceptance record.

Review a proposed contract by asking: what implementation would satisfy it but
clearly disappoint the user? Compare concrete counterexamples with independently
supplied requirements and examples, including empty or degenerate behavior that
makes a property vacuously true. Candidate-generated tests may reveal gaps, but
cannot replace independent acceptance checks. This review can expose omissions;
it does not guarantee discovery of every unstated requirement.

For changes to existing work, identify intended changes and declared behavior
to preserve. Retain relevant regression checks, compatibility cases, and observable
examples outside the new formal obligations. Their results remain separate
acceptance evidence; a proof is not a substitute for them. Record uncovered
behavior rather than claiming that all unintended deviation is excluded.

A contract may be fallible while remaining binding until an authorised revision.
Revision review covers supporting definitions and dependency versions as well as
the top-level statement: changing a predicate can change what a law means without
changing its text. Record the owner, rationale, and affected requirements; reassess
dependent acceptance evidence against the new version. An agent's discovery of
a gap does not waive an existing gate or turn a failed old contract into a pass.

The [specification failure cases](experiments/evaluation-design.md#specification-and-behavioral-failures) exercise these
requirements without assuming that Warranted can infer missing user intent.

## Jev: System 1 classification and judgment

Not built. [Jev](https://docs.typesafe.ai/introduction), from TypeSafe, is a
candidate provider for bounded classification and rubric-based judgment through
its typed Choice and Score primitives. The optional M3a experiment would use it
to route investigations and, where a contract owner explicitly adopts one, to
satisfy a judgment gate. Any integration must keep the invariants above: the host
invokes the designated judge independently of the worker, binds its result to
exact inputs, and treats abstention, failure, or staleness as blocking. A judgment
cannot waive another failed check. TypeSafe's
[confidence documentation](https://docs.typesafe.ai/confidence) describes a
statistic of the returned distribution, not a verified probability of correctness,
so thresholds would need calibration on Warranted's tasks.

Separating generation from judgment may reduce shared errors and self-validation
bias. Studies of [LLM self-preference](https://arxiv.org/abs/2404.13076) and
[evaluation on verifiable tasks](https://arxiv.org/abs/2504.03846) motivate testing
that hypothesis; they do not show that Jev removes the bias. A useful comparison
would measure acceptance of the generator's actual errors, alongside false
rejection, abstention, and cost.

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

The ledger implements reservations, unknown outcomes, and blocked retry. A loopback
fake service tests lost responses; the Ollama and OpenRouter adapters make one
attempt per operation and leave a lost response unknown. No adapter performs
external writes. Keep pure recomputation, historical replay, and retries of live
operations distinct.

## Dream-RSI: exploration and replay

Not built. The planned scheduling experiment adapts Tong Zheng and colleagues'
[Dream-RSI: Recursive Self-Improvement through Evolving Worlds](https://arxiv.org/html/2609.14858v1),
especially Sections 2–3. Its central contribution is to use recorded discovery trees
as replay worlds for evaluating exploration policies. A policy-development agent
revises scheduling code using replay feedback; the selected policy guides another
live rollout, expanding the history for the next round. The discovery model stays
fixed.

Warranted's adaptation would add context compatibility, evidence, gate, and
accounting requirements:

- Replay reveals only recorded observations in recorded order. A missing
  continuation, lost response, or incompatible context is unsupported. Replay
  cannot call live tools or add evidence to the ledger.
- Only scheduling code evolves. Worker prompts, model settings, tools, checkers,
  contracts, and budget enforcement stay frozen; policy code cannot read the
  unrevealed trace, private evaluator data, or enforcement state.
- Shared knowledge is published only at validated rollout boundaries; a changed
  shared context starts a new world.
- Replay scores select policies; they do not guarantee live improvement. Train on
  authorised histories, freeze the selected policy, and charge all collection,
  development, and replay costs.

The M1 ledger already records the world, session, input, and allowance identities
these rules need. Upstream implementation reuse remains to be assessed.

## Scope and provisional choices

The implemented scope is a local host, one ledger writer, bounded workers in
rootless containers, and two controlled task families. There is no distributed
coordination, unrestricted self-modification, weight training, universal ontology,
or real-world write. A full simulator of arbitrary environments is not assumed.

The implementation uses Python, SQLite, uv, and ordinary files. The schema, worker
adapter, workflow wiring, and proof setup remain provisional. The shared context
boundary was extracted only after both fixtures worked; fixture-specific
requirements and checkers stay outside the core. Evidence semantics must not
inherit a particular tool API.

[Hindsight](https://hindsight.vectorize.io/) is a later candidate for memory
retrieval, consolidation, and reflection. We would connect its recalled memories
to versioned evidence and artifact records; recalled text remains untrusted and
cannot satisfy a gate on its own.

[AutoSaddler](https://github.com/microsoft/AutoSaddler) is a later candidate for
trace-driven changes to prompts, tools, and other harness components. That wider
mutation surface deserves a separate experiment from the Dream-RSI scheduling
experiment. Keep both Hindsight and AutoSaddler outside the A–E and scheduling comparisons.

## Sources and provenance

This standalone design derives from the
[merged long-horizon proposal](https://github.com/Lewdwig-V/reschema/blob/42612d1c24f1dd063f8b021e346f3c23c8affbca/docs/proposals/long-horizon-reasoning-harness.md).
That document is historical context; this repository's design and roadmap govern
Warranted.

The project grew out of ReSchema's reverse-engineering work and its use of
validated executable artifacts. The dependency now runs the other way: ReSchema
is being rebuilt to use Warranted as its harness (see
[M7 and M8](roadmap.md#m7--stable-harness-api-and-cli)). Warranted's core stays
domain-independent; reverse-engineering behavior remains in ReSchema.

| Source | Influence and intended use | Where developed here |
| --- | --- | --- |
| [Schema](https://schema-harness.github.io/) | Executable world models checked against observations | [Durable knowledge](#durable-knowledge) |
| [PRO-LONG, v2](https://arxiv.org/html/2607.20064v2) | Complete, programmatically searchable interaction history | [Durable knowledge](#durable-knowledge) |
| [Dream-RSI, v1](https://arxiv.org/html/2609.14858v1) | Method for the not-yet-started scheduling experiment | [Exploration and replay](#dream-rsi-exploration-and-replay) |
| [Vercel's d0 case study](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools) | Legible files and a small worker interface | [Boundaries and ownership](#boundaries-and-ownership) |
| [mini-swe-agent](https://mini-swe-agent.com/latest/) | Integrated worker loop, pinned at 2.4.6 | [Boundaries and ownership](#boundaries-and-ownership) |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | Integrated workflow and checkpoint runner, pinned at 1.2.11 | [Boundaries and ownership](#boundaries-and-ownership) |
| [Lean](https://lean-lang.org/doc/reference/latest/) | Formal language and proof checking, with pinned Comparator and Landrun | [Rules, gates, and verification](#rules-gates-and-verification) |
| [Jev / TypeSafe](https://docs.typesafe.ai/introduction) | Candidate for the optional, not-yet-started classifier and judge | [Classification and judgment](#jev-system-1-classification-and-judgment) |
| [Hindsight](https://hindsight.vectorize.io/) | Later memory integration candidate | [Scope and provisional choices](#scope-and-provisional-choices) |
| [AutoSaddler](https://github.com/microsoft/AutoSaddler) | Later harness-optimisation candidate | [Scope and provisional choices](#scope-and-provisional-choices) |

Credit the source where its idea is introduced, explain our adaptation, and retain
the citation when refactoring the design. Research inspiration does not imply an
integration exists or that upstream results have been reproduced here.
