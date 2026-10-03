# Design note: a verified optimisation loop, and what it needs from Warranted

Proposed 2026-10-03 for review. Nothing here is implemented. This note asks
whether Warranted can serve a second, non-reverse-engineering consumer: a loop
that improves [Minkowski](https://github.com/Lewdwig-V/minkowski)'s performance,
where each accepted change becomes the baseline for the next. It maps that use
case, and a cheaper puzzle fixture, onto the current interfaces, names the gaps,
and proposes the smallest change for each.

The goal comes in two stages, decided by the project owner on 2026-10-03:

- **(a), first: improve Minkowski's code.** Agents propose optimisations;
  Warranted accepts one only when Minkowski's own correctness checks pass and a
  pinned performance measure improves, then makes it the next baseline.
- **(b), eventually: improve the agent.** The same loop scores changes to the
  worker's prompts, tools, or strategy, with Minkowski optimisation as the
  objective. This connects to [M6](../roadmap.md#m6--dream-rsi-adaptation-for-search-improvement).

## Why Minkowski

Minkowski is a Rust storage engine for real-time applications. It is a good
second consumer because its correctness and its objective are both already
machine-checkable:

- **Correctness gates exist.** About 900 unit tests, clippy with warnings as
  errors, fmt, a Miri subset, TSan, and Loom. Its CI runs fmt, clippy, test, TSan,
  and Loom in about four minutes.
- **The objective is measurable without noise.** Besides criterion wall-clock
  benchmarks, it has [iai-callgrind](https://github.com/iai-callgrind/iai-callgrind)
  benchmarks (`minkowski-bench --bench ecs_icount`, `minkowski-lsm --bench
  page_codec`) that count instructions. Its own
  `scripts/capture-bench-baseline.sh` calls these "the machine-independent
  regression signal". Exact counts let acceptance compare numbers rather than
  distributions.
- **The history shows the shape of the work.** `docs/performance.md` records
  optimisations with measured gains, such as join elimination and direct
  archetype iteration. These are the kind of change the loop should find.
- **Unsound speedups are a real risk.** Lock-free allocators, split-phase
  transactions, and unsafe column storage mean a faster change can be subtly
  wrong. The gates, not the agent's judgement, must decide.

## The loop for goal (a)

One *round* is one Warranted run of an optimisation task:

1. **Baseline.** The task pins a Minkowski source tree, a toolchain image, and
   the baseline's measurements.
2. **Work.** The worker reads the code, profiles, and edits it in its sandbox,
   then submits a patch.
3. **Gates.** Contained jobs apply the patch to the pinned tree and run fmt,
   clippy, the tests, and the Miri and Loom subsets. Any failure rejects.
4. **Measure.** A contained job runs the in-sample instruction-count benchmarks
   on the patched tree.
5. **Accept.** The patch is accepted if every gate passes, the target benchmark
   improves by at least a stated margin, and no other benchmark regresses beyond
   a stated tolerance.
6. **Promote.** The accepted patch, applied to the baseline, becomes the next
   round's pinned tree, with its lineage recorded.
7. **Hold out.** At set points, a separate set of benchmarks and workloads that
   rounds never optimise against is measured. A regression there stops
   promotion.

Warranted produces accepted patches with their evidence. It never pushes to
Minkowski: merging stays a human decision, as a PR reviewed like any other.

## What already fits

| Need | Existing part |
| --- | --- |
| Run Rust builds and tests in isolation | [Checker jobs](../reference/worker-and-containment.md#checker-jobs) in a pinned toolchain image |
| A worker with Rust tools | [Domain worker images](../reference/worker-and-containment.md#container-sandbox) |
| Gates the agent cannot weaken | Required checks and [acceptance](../reference/claims-and-acceptance.md#acceptance); [invariant 4](../../AGENTS.md#invariants-to-preserve) |
| Cap expensive compiles and benchmark runs | Task check budgets, run budgets, and `JobLimits` |
| Remember what worked, and forget it when the code changes | [Scoped memory](m7-scoped-memory.md): facts depend on file bytes and go stale |
| Do not retry a failed idea | The [duplicate guard](m7-harness-api.md#budgets-and-the-duplicate-guard) |
| Keep held-out measurements out of the loop | [Campaign splits](../reference/cli.md#campaigns) and split-scoped memory |
| Record every attempt and its cost | The ledger, `status`, `campaign report`, and `export` |

## Gaps, and the smallest change for each

### G1. Candidates are files; Minkowski is a source tree

Task inputs are capped at 1 MiB and the captured workspace at 128 files and
1 MiB. Minkowski's tracked tree is about 4.1 MB in 205 files, before vendored
dependencies.

**Change:** a candidate may be a *patch against a pinned tree*. The tree is
pinned by digest, either built into the worker image or imported with
`warranted import`. The worker edits a checkout in its sandbox; at submission
the sandbox captures `git diff` against the pinned commit, within the existing
size limit. Checkers apply the patch to a fresh copy of the tree inside a job,
so the check never trusts the worker's checkout. Dependencies are vendored into
the toolchain image, because containers have no network.

### G2. Verdicts are pass or fail; speed is a number

`Verdict` has a status, feedback, host-only data, and facts. It has no score,
and acceptance means "every required gate passed".

**Change:** a checker may return named numeric *measurements* alongside its
status. The task declares an *objective* over them: the target measure, the
direction, a minimum improvement relative to the baseline, and a regression
tolerance for the others. The host turns that into the acceptance decision, so
the threshold is part of the contract and a worker cannot change it. With exact
instruction counts, the first version needs no statistics; a margin is enough.
Wall-clock measures are recorded, not gated (see G4).

### G3. Nothing promotes an accepted result into the next task

Runs take fixed, pinned inputs. Campaigns repeat tasks; they do not chain them.

**Change:** a *chain*: a campaign whose next round's baseline is the previous
accepted round's result. The host derives the next round's pinned tree, the
baseline tree plus the accepted patch, and its baseline measurements from
recorded evidence, never from worker output. Each round records its parent
round, so the lineage from any accepted state back to the original baseline is
a chain of receipts. A rejected or unknown round does not advance the baseline.

### G4. Measurements depend on the machine

Criterion timings are only comparable on the same host, and checker jobs share
it with other work.

**Change:** gate only on deterministic measures, such as instruction counts,
whose job records the toolchain and flags. Minkowski's script already overrides
`target-cpu=native` for valgrind. Record wall-clock results with the host's
identity as information, not as a gate. Wall-clock gating on a reference machine
is a later step, if it is ever needed.

### G5. Every check rebuilds from scratch

Jobs share no host mounts, by design, so each gate recompiles the workspace.

**Change:** a *pinned build cache*: a compiled `target/` directory for the
baseline tree, imported as an artifact and copied into each job's scratch space.
The boundary stays intact, because the cache is evidence pinned by digest, not a
shared mount. Rebuilding only what the patch changes is then incremental.
`JobLimits` may need larger scratch space for this.

### G6. One strategy: retry with feedback

Each run is one agent loop that resubmits after feedback. Optimisation, like
puzzle solving, often gains most from trying several candidates and keeping the
best.

**Change:** a *strategy* that decides how a run spends its budget: one episode
at a time (today), several independent candidates per round, or candidates
refined from the best so far. The task layer would supply the episodes and the
host would keep the checks and acceptance; the strategy chooses only which
candidates to try. This gap is shared with ARC (below), which is a cheaper place
to build it.

## A cheaper first fixture: ARC-AGI-1/2

Static [ARC](https://arcprize.org/) puzzles fit today's task shape. Each task
gives a few input and output grid pairs and a test input. The worker writes a
program, and a checker runs it on the training pairs, which is a verifiable,
scoped check. The test output stays private.

The fixture would exercise:

- **G6, the strategy layer.** ARC is won mostly by generating many candidate
  programs and filtering them, which today's single loop cannot express.
- **[Invariant 8](../../AGENTS.md#invariants-to-preserve).** A program that
  reproduces every training pair is not shown to be correct on the test input.
  The fixture would report accepted programs and their test results separately.
- **Development and held-out splits**, with memory kept within each split.

Its results would be engineering evidence only. Public ARC sets are widely
published and some are saturated, so capability claims need private or held-out
tasks.

The interactive [ARC-AGI-3](https://arcprize.org/blog/arc-agi-3-launch) is a
later candidate. It would need stateful host operations (one game session per
run), scored verdicts (G2), and network access or a local game engine.

## Negative cases to write first

- A patch that touches files outside the allowed paths, such as benchmarks,
  tests, CI, or the measurement scripts, is rejected before any measurement.
  The loop must not improve its score by changing the ruler.
- A faster patch that fails any gate, including Miri or Loom, is rejected, and
  its measurements are not recorded as an improvement.
- A worker-supplied number cannot become a measurement; only a checker job's
  recorded output can.
- An improvement below the stated margin, or a regression beyond tolerance on
  any other in-sample benchmark, is rejected.
- A chain never advances from a rejected, unknown, or unsupported round, and a
  restarted chain resumes from the last accepted round.
- A held-out regression stops promotion even when every in-sample measure
  improves.
- A patch that does not apply cleanly to the pinned tree is a rejection, not an
  infrastructure failure.

## Goal (b), later

Improving the agent adds a second level. The rules that keep it honest:

- The optimiser may change the worker: prompts, tools, the worker image's
  helpers, and the strategy. It may never change checkers, objectives,
  benchmarks, baselines, budgets, or the held-out sets. These already sit
  outside the worker's reach by
  [invariant 4](../../AGENTS.md#invariants-to-preserve); the outer loop must
  keep them outside its own reach too.
- Each agent version is evaluated as a campaign: the same chain of optimisation
  tasks under the same budget, with the version pinned by digest. A version is
  promoted only on held-out tasks that no earlier version was tuned on.
- Costs are part of the result. A version that improves Minkowski more but
  spends far more is reported as such, not ranked by improvement alone.
- [M6](../roadmap.md#m6--dream-rsi-adaptation-for-search-improvement) already
  plans this shape for scheduling policies, using replayed histories. Goal (b)
  would build on whatever M6 delivers rather than add a parallel mechanism.

## Proposed order

1. **G6 with the ARC-AGI-1/2 fixture:** a strategy that tries several candidates
   per round. It is cheap, and it fits today's task shape.
2. **G1 and G2:** patch candidates against a pinned tree, and checker
   measurements with a task objective. With these, a single Minkowski
   optimisation round runs end to end, with no promotion yet.
3. **G4 and G5:** deterministic measures as the only gates, and a pinned build
   cache, so rounds are fast enough to repeat.
4. **G3:** chains, so accepted rounds compound into a recorded lineage. This
   completes goal (a).
5. **Goal (b)**, on top of M6.

Each step keeps its interfaces provisional until the next use case confirms
them, as [AGENTS.md](../../AGENTS.md#implementation-style) asks.

## Questions for review

1. Should instruction counts be the only gated measure, with wall-clock
   recorded but never gated? This note proposes yes.
2. Which benchmarks are in-sample, and which are held out? A natural split is
   `ecs_icount` in-sample and `page_codec` plus selected examples held out, but
   that is Minkowski's call.
3. Which paths may a patch touch? This note proposes source under `crates/*/src`
   only, excluding benches, tests, CI, and scripts.
4. Should accepted patches reach Minkowski as PRs opened by a human, or should
   Warranted gain a host-mediated operation, with external effect, that opens
   them? This note proposes a human, at least until goal (b).
5. Where do measurements run: the developer's machine, CI runners, or a
   dedicated host? Instruction counts work anywhere valgrind does; wall-clock
   data is only comparable on one host.
