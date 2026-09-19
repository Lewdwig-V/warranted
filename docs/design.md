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

A separate optional experiment evaluates Jev as a System 1 classifier and judge
around the reasoning worker. Keep it outside the original knowledge and scheduling
comparisons so its contribution can be measured independently.

## Boundaries and ownership

| Component | Responsibility | Initial direction |
| --- | --- | --- |
| Host controller | Operation permissions, authoritative writes, budgets, recovery, acceptance | Small Python implementation with one ledger writer |
| Evidence ledger | Versioned observations, claims, dependencies, attempts, and receipts | SQLite plus content-addressed artifact files |
| Worker | Inspect permitted context, propose artifacts and investigations | Existing bounded coding worker; shell and files |
| Workflow runner | Dispatch and checkpoint bounded sessions | Evaluate LangGraph when the worker slice lands |
| Domain environment | Supply observations and execute permitted operations | Local, controlled fixtures first |
| Independent checker | Assess a specific artifact against a pinned contract | Deterministic task checks; later Lean and designated model-judgment gates |
| Exploration policy | Select branches to continue, branch out, batch, or stop | Fixed policy first; replay-improved policy later |

These are responsibility boundaries, not seven services or a mandatory class
hierarchy. Keep them in one package until working use cases require separation.
[mini-swe-agent](https://mini-swe-agent.com/latest/) is the initial worker candidate:
its small, shell-based agent loop fits bounded investigation sessions.
[LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) supplies
workflow persistence and resumption. We intend to build on those projects'
execution infrastructure while the Warranted host owns evidence semantics.
The [M3 adoption plan](m3-adoption.md) records the integration boundary and pinned
versions. The initial integration uses scripted boundaries to test recovery.

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

A gate need not be deductive. The contract owner may designate a model judge for
a statistical acceptance criterion, such as relevance or rubric-based quality.
The host independently obtains and checks the judge's attributable result against
the pinned gate policy. Passing establishes that criterion was met, with measured
error risk; it does not establish a proof or satisfy other required obligations.
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
Pin the Lean toolchain, libraries, target, and permitted axioms. Isolate untrusted
elaboration/tactics and independently check the resulting artifact and transitive
dependencies, including indirect use of `sorryAx` or unapproved axioms.

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

The [pilot cases](pilot.md#specification-and-behavioral-failures) exercise these
requirements without assuming that Warranted can infer missing user intent.

## Jev: System 1 classification and judgment

[Jev](https://docs.typesafe.ai/introduction), from TypeSafe, is the proposed first
provider for bounded classification and rubric-based judgment. Its documented
Choice and Score primitives return typed decisions and distributions. Warranted's
adaptation uses those outputs to prioritise investigation, escalate uncertainty,
and satisfy explicitly designated judgment gates. The reasoning worker develops
plans and artifacts; the host enforces acceptance, including independent model
judgments where the contract requires them. See [M3a](roadmap.md#m3a--jev-as-a-system-1-classifier-and-judge)
and the [separate comparison](pilot.md#jev-classifier-and-judge-comparison).

Use supplied categories and explicit, versioned rubrics: classify a failure for
the next investigation, rank candidate continuations, or assess evidence relevance
and apparent contradiction. A judgment records what the model predicted about
the supplied evidence. For advisory use it informs the next action. For a judgment
gate, the contract owner pins the target, evidence requirements, judge identity
and version, rubric, decision rule, retry/aggregation policy, tolerable error, and
escalation path. The host invokes that judge independently of the candidate worker
and binds its response
to an acceptance receipt when the criterion is met. This is authoritative for
that obligation, without certifying factual truth, complete user intent, or a
separately required deterministic or Lean obligation.

For routing, the host supplies only context already permitted to the worker. For
gates, it supplies the contract's evidence bundle through the isolated checker
path; evaluator answers stay private. Neither the provider nor worker-authored
text can change the policy, rubric, or acceptance contract. Bind predictions to
exact input versions and re-evaluate after relevant changes. Preserve raw responses,
probabilities, model identity, question/rubric versions, and costs as model-derived
observations. A validation receipt additionally records the host's application of
the designated gate policy; an arbitrary model response cannot stand in for it.

Start with shadow evaluation, then evaluate routing and judgment gates separately.
Advisory abstention uses a budgeted baseline fallback. A gate stays blocked on an
unknown, invalid, stale, or nonqualifying result until qualifying evidence arrives
under the contract or its approved escalation path. Repeated judging cannot be
used to cherry-pick a pass outside the pinned retry policy. Worker reasoning alone
cannot substitute for the required judge, and high scores cannot override other
failed checks. Treat candidate evidence as untrusted input, including attempts to
instruct the judge. Confidence
alone cannot detect every unfamiliar case or confidently wrong answer.

Certainty and diagnostic usefulness are separate routing inputs. A confident
rejection can be correct while leaving the worker unable to repair the candidate.
Return criterion-level outcomes and references selected
from supplied evidence, with a known failure category or an explicit unknown;
do not assume a scalar score provides a repair direction. These diagnostic hints
remain hypotheses even when the gate verdict is authoritative.

Under a bounded host policy, request System 2 investigation for missing diagnosis,
conflicting criteria, novel failure modes, or repeated unsuccessful repairs, even
when Jev is confident. Run a known missing check directly when that supplies the
needed evidence. System 2 can gather evidence, explain interactions, and propose
repairs; its explanation does not waive the rejected gate. Re-evaluate changed
candidates under the pinned contract. This adds a route for useful deliberation
without requiring a heavyweight model to explain every routine verdict.

TypeSafe's [confidence documentation](https://docs.typesafe.ai/confidence) describes
Choice/Score confidence as a statistic of the returned distribution. Do not read it
as a verified probability that the selected answer is correct. Measure calibration
and error costs on our tasks before choosing thresholds. Typed output constrains
the answer format; it does not ensure factual correctness. The integration and
its benefits remain unimplemented hypotheses.

### Evaluator independence

A second hypothesis is that separating generation from judgment reduces shared
errors and self-validation bias, beyond any latency benefit. Studies of
[LLM self-preference](https://arxiv.org/abs/2404.13076) and
[evaluation on verifiable tasks](https://arxiv.org/abs/2504.03846) motivate testing
this failure mode. They do not establish that Jev removes confirmation bias or
that generative reasoning makes it unavoidable.

Preserve three distinct forms of separation:

- **Reasoning context:** invoke the judge in a fresh context without the worker's
  deliberation or preferred verdict; retain evidence needed to assess the artifact.
- **Evidence access:** the host supplies the requirements, candidate, and relevant
  raw observations under the gate's visibility contract. A worker-selected summary
  must not be the sole basis for judgment or hide contrary evidence.
- **Learned failure modes:** a different model or training objective may provide
  complementary errors. Different providers or System 1/System 2 labels alone
  do not establish statistical independence.

Measure whether Jev catches the generator's actual mistakes against a fresh-context
same-model judge and a different generative judge in the [pilot](pilot.md#jev-classifier-and-judge-comparison).
Overall judge accuracy is insufficient if its errors coincide with the generator's.
Keep false rejection, abstention, and cost visible alongside erroneous acceptance;
rejecting everything cannot demonstrate a useful reduction in shared errors.

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
| [Jev / TypeSafe](https://docs.typesafe.ai/introduction) | Planned optional System 1 classifier and rubric-based judge | [Classification and judgment](#jev-system-1-classification-and-judgment) |
| [Hindsight](https://hindsight.vectorize.io/) | Later memory integration candidate | [Scope and provisional choices](#scope-and-provisional-choices) |
| [AutoSaddler](https://github.com/microsoft/AutoSaddler) | Later harness-optimisation candidate | [Scope and provisional choices](#scope-and-provisional-choices) |

Credit the source where its idea is introduced, explain our adaptation, and retain
the citation when refactoring the design. Check and pin implementation versions
at the relevant milestone; research inspiration does not imply an integration
already exists or that upstream results have been reproduced here.
