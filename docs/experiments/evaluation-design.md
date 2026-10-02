# Evaluation design

This document defines how Warranted is evaluated: the two controlled task
families, the specification and recovery failure cases every condition must
handle, the A–E knowledge-workflow conditions, how development trial plans are
prepared and reported, and the criteria for keeping or removing machinery. The
fixtures, failure cases, A–E disclosure rules, and offline trial accounting
exist and run with fixed or scripted workers. Measured model comparisons have
not been run; the dated records below are development diagnostics only.

## Experiment records

| Record | Summary |
| --- | --- |
| [2026-09-22 local model probe](2026-09-22-local-model-probe.md) | One `gemma4:26b` connectivity request through Ollama timed out; the unknown attempt was kept and not retried. |
| [2026-09-23 Qwen development runs](2026-09-23-qwen-development-runs.md) | Five local `qwen3.8:27b` migration-A attempts (four- to twelve-command limits); none submitted `result.json`, so no independent check, revision, or restart ran. |
| [2026-09-24 OpenRouter GLM diagnostic](2026-09-24-openrouter-glm-diagnostic.md) | Five `z-ai/glm-5.3-flash` migration-A attempts all stopped before submission, dominated by provider errors, truncation, and a returned provider failure. |
| [2026-09-24 submission controls](2026-09-24-submission-controls.md) | Interface diagnostic with local Qwen: explicit submission instructions produced accepted submissions, but the twelve-run comparison combined prompt changes and cannot isolate their effects. |
| [2026-09-25 corrected submission controls](2026-09-25-corrected-submission-controls.md) | Twelve repeated runs with corrected prompts: seven accepted, two rejected (packaging), three unsubmitted; every final program passes the original five checks, but only two reject the boolean-version diagnostic case. |

All four use public development fixtures. None compares models or treatments,
and none reaches the approved revision and restart path.

## Task families

| Family | Fixture | Revised premise |
| --- | --- | --- |
| Data transformation | [CSV transformation](../fixtures/csv-transformation.md) | Changed definitions and assumptions mid-task |
| Repository migration | [Configuration migration](../fixtures/config-migration.md) | Owner-approved requirement addition at a submission checkpoint, followed by a forced new session |

Both run locally with no deployment or remote write. Acceptance checks run
outside the editable candidate workspace, and runs record exact repository
revisions and fixture changes. A worker should identify which patches,
assumptions, and checks need revision; a passing test from an old requirement
does not satisfy the new one. The two fixtures keep environment observations and
operations, worker proposals, and authoritative checking distinct, and are the
basis for any shared adapter interface; no universal API is frozen from them.

## Specification and behavioral failures

These cases exercise
[intent, contracts, and acceptance](../design.md#intent-contracts-and-acceptance)
with deterministic candidates. Each fixture retains the original request, source
documentation, and independently supplied behavioral examples as versioned
artifacts alongside its formal interpretation. The fixture owner fixes the
acceptance obligations and evaluator before the run; the worker cannot replace
them with its own laws, tests, or explanations.

| Case | Narrow target the candidate meets | Independent obligation that rejects it |
| --- | --- | --- |
| Mistranslated requirement | Timestamp round trip under a zero offset | Source contract requires a one-hour offset; timestamp and daily-total checks fail |
| Incomplete formal target | Identifier uniqueness (including the degenerate candidate that drops every record) | Original request requires preserving all records |
| Regression outside proved properties | Field renaming preserves host and timeout | Predeclared legacy-consumer case fails because the optional label is dropped |

The proof support for all three is implemented with fixed proof proposals
([configuration migration proof cases](../fixtures/config-migration.md#proof-cases)).
The uniqueness theorem is reused for the incomplete-target case and its control;
for example, applying it to an empty selected input establishes output
uniqueness while the independent check rejects losing records from the nonempty
source, with no proof of record preservation or timestamp semantics.
Model-to-output correspondence is checked on development inputs. Both families
run through A–E with fixed workers, including these proofs in condition E; this
establishes integration behavior, not model quality.

Rules for these cases:

- Use valid proofs of inadequate targets. Do not substitute proof forgery,
  unsupported premises, or model/implementation mismatch for a specification
  failure.
- Include successful controls satisfying every obligation, so rejecting every
  candidate cannot count as success. Final task acceptance is identical across
  A–E.
- Record the narrow check or proof as successful and the task as rejected by the
  failed independent obligation. Preserve the conflicting evidence across restart;
  neither another proof nor an agent-authored reinterpretation may erase it.
- A corrected candidate may pass after the required checks run again, with the old
  failure still recorded. An authorised contract revision keeps the old result and
  requires a fresh acceptance decision bound to the revised contract and
  definitions.
- Reject changes to a supporting predicate that make an unchanged law easier to
  satisfy under the old contract identity.

These cases test rejection of known failures against independently supplied
requirements. They do not demonstrate discovery of unstated user intent. Measure
finding omitted requirements separately from enforcing known ones; an evaluator
rejection alone is not successful specification repair.

## Recovery cases

Operation-level crash tests use a controlled fake service that can supply an
idempotency key and exact receipt in one mode and deliberately cannot establish
the outcome in another ([model adapters](../reference/model-adapters.md)). A lost
response is either reconciled for that exact operation or stays unknown with its
reservation retained. Aggregate status is not a substitute for an attributable
receipt. Reopening a project never resets the task's usage or authorises a blind
retry. See [recovery and accounting](../design.md#recovery-and-accounting).

## Knowledge-workflow conditions

B and C make the influences of [PRO-LONG](https://arxiv.org/html/2607.20064v2)
(accessible history) and [Schema](https://schema-harness.github.io/) (executable
models) explicit. They are Warranted conditions inspired by that work, not
replications of those systems or their ARC-AGI-3 evaluations. D and E test the
additional value of dependency tracking and formal proof support.

| Condition | Added support |
| --- | --- |
| A | Ordinary workspace and prose notes |
| B | A plus a complete queryable observation/action history |
| C | B plus executable models and regression checks |
| D | C plus dependency tracking and applicability invalidation |
| E | D plus the Lean formalisation and proof workflow |

What each condition exposes and how to run it is in
[contexts](../reference/contexts.md). Requirements for a valid comparison:

- All conditions use the same shell/file interface and available tools, including
  Lean when applicable. Vary the knowledge workflow and its acceptance policy, not
  the model or access to private data. Record baseline agents' spontaneous tool use.
- The independent final task-success predicate is the same across conditions.
  Additional assurance required by E carries its full cost.
- Use the same initial fixed search policy, task instances, model and inference
  settings, per-attempt allowances, source access, and host caps. Execution
  permissions and evaluator isolation apply to every condition.
- Harness-only recording may capture every run for measurement but must not
  expose B–E's extra support to A. Keep Jev, Hindsight, and broader harness
  optimisation out of this study.
- Run multiple fresh trials with both frontier and smaller models. Use inexpensive
  development runs to set budgets and a minimum useful improvement, then freeze
  them before final evaluation. Split by task instance and derivation lineage so
  variants or descendants cannot leak between training and held-out data.

## Trial plans and accounting

`examples/m5/trials.py` prepares a development trial plan and reports its
results. It never dispatches work. `init` creates one run directory per
repetition, family (`csv`, then `migration`), and condition (A–E), and pins each
ledger's identity, manifest, and snapshots before any worker starts. Manifests
record the model, policy, environment, evaluator, and per-run limits; snapshots
retain exact fixture, host, model-response, and verifier bytes. Without a model
option the plan uses the scripted `m5-fixed-two-proposals-v1` worker.
`--local-model` or `--openrouter-model` (with `--api-key-file`, `--max-tokens`,
`--timeout`, `--max-steps`) pins a live model instead; see
[model adapters](../reference/model-adapters.md). Both public lineages
(`m2-controlled-csv-v1`, `m5-controlled-migration-v1`) are development data, and
repetitions of them are not independent or held-out samples.

Use an existing pinned proof bundle
([proof verification](../reference/proof-verification.md)):

```bash
uv run --locked python examples/m5/trials.py init runs/m5-trials \
  --bundle runs/m4-tools/bundle.json --repetitions 1
uv run --locked python examples/m5/trials.py report runs/m5-trials
```

The plan records its order but does not enforce it. Run each listed path with the
treatment runner, passing the same family, condition, bundle, and model options:

```bash
uv run --locked python examples/m5/treatments.py start \
  runs/m5-trials/runs/001-csv-A --family csv --condition A \
  --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m5/treatments.py resume \
  runs/m5-trials/runs/001-csv-A --family csv --condition A \
  --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m5/trials.py report runs/m5-trials
```

`report` reads host ledger records while trial writers are stopped. It verifies
stored bytes, starts no work, grants no fresh acceptance, and ignores the mutable
`reports/` files.

- Stage results keep independent task checks, stale-receipt decisions, proof
  support, and specification failures. Completed task acceptance is reported
  separately from the treatment's full qualification rule.
- Every planned slot stays in its family and condition denominator. Unknown
  attempts and budget breaches cannot count as qualified.
- Missing, damaged, or substituted ledgers stay visible and mark usage totals
  incomplete, as do unresolved attempts, which keep their reservations. Failed
  proofs stay visible even when they prevent a stage from finishing.
- Totals cover recorded operations in planned runs only, include failed work, and
  separate spent units from unresolved reservations. For live models the report
  sums recorded tokens and, for OpenRouter, returned USD cost; totals are marked
  incomplete when any dispatched attempt lacks them.
- The pinned bundle's build duration appears once in the plan. Operation durations
  are not end-to-end wall time and can overlap. Monetary cost outside provider
  receipts, human interventions, fixture setup, formalisation, and development
  effort are not measured.

An interrupted `init` leaves no complete plan and cannot run as a campaign; keep
its directory for diagnosis and prepare a new plan.

`uv run --locked pytest -q tests/test_m5_trials.py` covers deleted reports and
repeated resume, replaced runs, live token and cost totals with unknown model
usage, and unfinished or later-unknown attempts. It needs no containers or
credentials, but plan initialization records `podman --version`, so a `podman`
binary must be on `PATH`.

## Measurement and decision criteria

Keep an immutable manifest for each run: task, contract, and evaluator versions;
model and inference settings; worker and policy versions; initial context;
environment; limits; split; and outcome. Retain failed and interrupted runs.

Report:

- task success, stale conclusions used, recovery correctness, repeated work, human
  interventions, unknown outcomes, and total tokens, tool and proof work, elapsed
  time, and cost;
- formalisation errors and interface/bookkeeping overhead;
- mistranslated or omitted requirements, regressions outside proved properties,
  and proof/checker failures, separately; cases where formal checks pass but
  independent task acceptance fails (proof success is not task success);
- contract revisions and unresolved gaps, never as passes of the original contract;
- campaign costs including failed proof attempts, fixture setup, formalisation,
  and trial development.

Advance D or E only if their measured benefit justifies their cost against the
simpler conditions across both task families. Retain Lean only where it improves
the trade-off over D. If simpler conditions win, simplify Warranted; do not adjust
acceptance requirements to manufacture a gain.

### Decisions required before measured trials

- Choose available provider/model versions and an explicit total spending cap
  before any paid trial.
- Pin equal worker capabilities, fixed scheduling, per-attempt limits, and
  task-success predicates across A–E.
- Select development thresholds for useful improvement, then freeze models,
  budgets, and evaluation rules before final runs.
- Create held-out task instances split by derivation lineage, not random copies.
  The public fixture examples are development data and cannot become held-out
  evidence.

## Future comparisons

These are not yet planned in detail and stay separate from the A–E study.

**Jev classifier and judge.** Following the
[M3a roadmap item](../roadmap.md#m3a--jev-as-a-system-1-classifier-and-judge)
and [Jev design](../design.md#jev-system-1-classification-and-judgment): compare
no learned router, an LLM classifier/judge, and Jev on the same evidence, choices,
and rubrics, first in shadow mode and then in paired live runs. Judgment gates
compare the generator in a fresh context, a different model, and Jev against
independent reference labels, using the predeclared measure
`P(judge accepts | candidate violates the assessed obligation)` at frozen
development operating points. A favourable judgment can never override another
failed required obligation.

**Dream-RSI scheduling.** Warranted's adaptation of
[Dream-RSI](https://arxiv.org/html/2609.14858v1)
([design](../design.md#dream-rsi-exploration-and-replay),
[M6 roadmap item](../roadmap.md#m6--dream-rsi-adaptation-for-search-improvement))
compares fixed and evolved scheduling policies under conditions C and E, trained
with equal optimisation caps on the same instances. World manifests bind
everything that affects worker inputs; replay reveals only recorded outcomes and
cannot create authoritative evidence. Campaign cost includes seed history, policy
development, replay CPU, and live discovery, and evolved scheduling is adopted only
on untouched live results against its fixed baseline. The original comparison is
Jev-free.
