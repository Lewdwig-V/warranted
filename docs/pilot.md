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
support to A. Keep Hindsight and broader harness optimisation out of this study.

Run multiple fresh trials and include both frontier and smaller models. Begin
with inexpensive development runs to set budgets and a minimum useful improvement,
then freeze them before final evaluation. Split by task instance and derivation
lineage so variants or descendants do not leak between training and held-out data.

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
