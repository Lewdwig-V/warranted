# Design note: five use cases, and what they ask of Warranted

Proposed 2026-10-03 for review. Nothing here is implemented. Warranted's two
fixture families (CSV transformation and configuration migration) and its first
consumer, ReSchema, are all "write a program that a checker accepts". That is
enough to build the harness, but not to show that it is domain-independent: an
interface that only one shape of task has used can quietly assume that shape.

This note picks five further use cases that differ from the fixtures and from
each other. It then audits the current interfaces against them, naming each
place that is not yet general and the use cases that force a change. The aim,
set by the project owner on 2026-10-03, is a general-purpose neurosymbolic
harness: a model proposes, and symbolic machinery (tests, solvers, proof
kernels, measurements) decides what is accepted.

## The portfolio

| Use case | Candidate | What decides acceptance | Kind of evidence |
| --- | --- | --- | --- |
| [Minkowski optimisation](#minkowski-optimisation) | A patch to a large Rust tree | Minkowski's own gates, then an exact instruction-count improvement | Tests, sanitizers, model checkers, a measurement |
| [ARC-AGI-1/2](#arc-agi-12) | A program over grids | Reproducing every training pair | Executable examples; the test output is a separate result |
| [ARC-AGI-3](#arc-agi-3) | Actions in an interactive game | The game's score | Environment reward from a stateful session |
| [Logic and constraint puzzles](#logic-and-constraint-puzzles-with-an-smt-solver) | An assignment, or a claim that none or exactly one exists | Evaluating the constraints; for "none" or "exactly one", a solver | A direct check, or a solver result with its trust basis |
| [Lean theorem proving](#theorem-proving-in-lean) | A Lean proof of a per-task theorem | The Lean kernel, through the existing verifier | A kernel-checked proof under an axiom policy |

Together they cover:

- **Every kind of evidence.** Tests, measurement, reward, solver results, and
  kernel-checked proof.
- **Every candidate size.** A grid program, a short proof, and a patch to a
  4 MB source tree.
- **Stateless and stateful worlds.** Most cases are stateless; ARC-AGI-3 and
  interactive proving are stateful.
- **One-shot and compounding work.** Most cases are one-shot. Minkowski rounds
  and lemma libraries build on earlier accepted results.

The roles stay as [AGENTS.md](../../AGENTS.md#implementation-style) sets them.
Domain behaviour (Rust, grids, SMT-LIB, Lean libraries) enters only through
checkers, jobs, and worker images. The core gains only shapes that at least two
of these use cases need.

## What already generalises

| Need | Existing part | Used by |
| --- | --- | --- |
| Run untrusted builds, programs, and solvers in isolation | [Checker jobs](../reference/worker-and-containment.md#checker-jobs) in pinned images | All five |
| A worker with domain tools (cargo, Python, z3, Lean) | [Domain worker images](../reference/worker-and-containment.md#container-sandbox) | All five |
| Gates the worker cannot weaken | Required checks and [acceptance](../reference/claims-and-acceptance.md#acceptance); [invariant 4](../../AGENTS.md#invariants-to-preserve) | All five |
| A kernel-checked proof as a gate | [`LeanProof`](m7-proofs.md) and `VerdictStatus.UNPROVED` | Lean; SMT certificates later |
| Applicability distinct from validity | `LeanProof` premises (`UNSUPPORTED`) and correspondence (`REJECTED`) | Lean, Minkowski chains |
| Bounded spending | Task check budgets, run budgets, and `JobLimits` | All five |
| Facts that go stale when their inputs change | [Scoped memory](m7-scoped-memory.md) | Minkowski, Lean, ARC |
| No repeated failed attempts | The [duplicate guard](m7-harness-api.md#budgets-and-the-duplicate-guard) | All five |
| Separate development and held-out work | [Campaign splits](../reference/cli.md#campaigns) and split-scoped memory | All five |
| A changed contract recorded, not hidden | [Contract revisions](../reference/cli.md#contract-revisions) | Puzzles with errata, Minkowski gate changes |
| Every attempt and its cost recorded | The ledger, `status`, `campaign report`, and `export` | All five |

## Where Warranted is not yet general

Each entry below gives the current behaviour, the use cases it blocks, and the
smallest change. The labels are for review; the [proposed order](#proposed-order)
groups them.

### N1. A candidate is a small set of files

The sandbox captures at most 1 MiB of candidate files
(`CANDIDATE_LIMIT` in `_sandbox.py`), and task inputs have the same size. That
suits grids, proofs, and puzzle answers, but Minkowski's tracked tree is about
4.1 MB in 205 files before its vendored dependencies.

**Forced by:** Minkowski.

**Change:** a candidate may be a *patch against a pinned tree*. The tree is
pinned by digest, either built into the worker image or imported with
`warranted import`. The worker edits a checkout in its sandbox. At submission,
the sandbox captures a diff against the pinned commit, within the existing size
limit. Checkers apply the patch to a fresh copy of the tree inside a job, so a
check never trusts the worker's checkout. The task declares which paths a patch
may touch.

### N2. A verdict is pass or fail

`Verdict` has a status, feedback, host-only data, and facts. It has no number,
and acceptance means "every required gate passed".

**Forced by:**

- Minkowski: speed is a number.
- ARC-AGI-3: a game reports a score.
- Optimisation puzzles: minimise a cost, not just satisfy the constraints.

**Change:** a checker may return named numeric *measurements* alongside its
status. The task declares an *objective* over them: the target measure, its
direction, a minimum improvement over a stated baseline, and a regression
tolerance for the others. The host derives the acceptance decision from these,
so the threshold is part of the contract and the worker cannot change it. Gates
use only deterministic measures, such as instruction counts, whose job records
its toolchain and flags. Wall-clock results are recorded with the host's
identity, as information.

### N3. One fixed worker loop and one strategy

`_worker.py` drives a mini-swe-agent `DefaultAgent` through a serial LangGraph
lifecycle (`serial-v1`). Each run is one agent loop that resubmits after
feedback, one episode at a time.

**Forced by:**

- ARC: usually won by sampling many candidate programs and filtering them.
- Lean: often helped by trying several proof attempts, or a search over
  tactics.
- Minkowski: helped by trying several optimisation ideas per round.

**Change:** a *strategy* that decides how a run spends its budget. It may run
one episode at a time (today), several independent candidates, or candidates
refined from the best so far. It may run episodes concurrently within the
run's budget. The strategy chooses which candidates to try; the host keeps the
checks, acceptance, and budget enforcement. The worker loop becomes one
implementation behind a small episode interface, so that a domain needing a
different loop does not need a fork.

### N4. The world is stateless

Every operation is a contained job with fixed inputs. Nothing holds state
between a worker's actions except its own sandbox.

**Forced by:**

- ARC-AGI-3: one game session per run, advanced by actions, where each step's
  observation depends on all earlier steps.
- Interactive proving: a Lean REPL that holds proof states, as in
  [LeanDojo](https://leandojo.org/) or the
  [Lean REPL](https://github.com/leanprover-community/repl). This is optional,
  because whole-file proofs already work.

**Change:** a *host session*: a stateful, contained process that the host
starts for a run. Workers reach it only through recorded operations, and each
step is recorded with its observation. This is a host-mediated operation, so it
needs the reviewed design that [AGENTS.md](../../AGENTS.md) requires and that
[m7-host-operations.md](m7-host-operations.md) begins. Replay must reveal only
recorded steps ([invariant 6](../../AGENTS.md#invariants-to-preserve)), and a
session's score is evidence only if the host, not the worker, reads it.

### N5. A proof target is fixed per checker, without libraries

A domain constructs `LeanProof(target, ...)` once, so every task that requires
that check proves the same theorem. Challenges import only `Init`, without
Mathlib. The axiom policy allows exactly `propext` and `Quot.sound`
([proof verification](../reference/proof-verification.md)).

**Forced by:** Lean benchmarks such as
[miniF2F](https://github.com/openai/miniF2F) and
[PutnamBench](https://github.com/trishullab/PutnamBench). Each task states its
own theorem, almost all use Mathlib, and Mathlib proofs routinely use
`Classical.choice`.

**Change:**

- **Per-task targets.** A target is read from a task input that the task pins,
  so the theorem comes from the contract and not from the worker.
- **A pinned library set.** Mathlib at a fixed commit, prebuilt, in the
  verifier bundle. Its digest joins the policy digest.
- **A named, reviewed axiom policy** that adds `Classical.choice`, alongside the
  current stricter one. A target names the policy it requires.

### N6. Every pass looks the same

A `PASSED` verdict from a unit test, an instruction-count comparison, a solver,
and a kernel-checked proof all reach acceptance identically. The receipt says
which checker passed, but not what kind of warrant the pass is.

**Forced by:**

- SMT: a solver's `unsat` is trusted solver output unless it comes with a
  checked certificate.
- Lean: a proof is checked by a small kernel.
- Minkowski: tests sample behaviour.
- ARC: a program that fits the training pairs is only consistent with them.

**Change:** each checker declares its *trust basis*, from a small closed set:

- `example`: consistent with given cases, which sample a larger claim;
- `exhaustive`: a complete direct check of a finite claim, such as evaluating
  every constraint on a submitted assignment;
- `measurement`;
- `solver`: trusted solver output, with the solver's version;
- `certificate`: solver output with an independently checked certificate;
- `kernel`: a kernel-checked proof under a named axiom policy.

Reports and exports group results by trust basis. The bases are not ordered:
`kernel`, `certificate`, and `measurement` warrant different claims, not stronger
versions of one claim. A task therefore names, for a check, the exact bases it
accepts; it never states a minimum. This makes [invariant 8](../../AGENTS.md#invariants-to-preserve)
visible in every result instead of in prose. An unknown basis is refused, not
treated as the weakest.

### N7. No place for a trusted symbolic engine

A solver inside the worker's sandbox is an untrusted tool, which is correct.
A solver inside a checker job is trusted, but nothing records that trust apart
from the checker's code.

**Forced by:** SMT puzzles, and later any domain whose checker needs a solver
(for example, checking that a program meets a specification for all inputs).

**Change:** no new mechanism, but a pattern and a fixture.

- **A satisfying assignment** is checked by evaluating the constraints directly.
  This needs no solver.
- **An `unsat` claim** ("no solution exists") is checked by a pinned solver in a
  checker job. The verdict records the solver and its version with trust basis
  `solver`.
- **A "unique solution" claim** is a satisfying assignment plus `unsat` for the
  constraints with that assignment excluded.
- **A stronger, optional check:** ask [cvc5](https://cvc5.github.io/) for an
  Alethe proof and check it with
  [Carcara](https://github.com/ufmg-smite/carcara). This raises the basis to
  `certificate`.

Disagreement between the worker's solver and the checker's solver is the
worker's problem, not the host's: only the checker's run counts.

### N8. Nothing promotes an accepted result into later tasks

Runs take fixed, pinned inputs. Campaigns repeat tasks; they do not chain them.

**Forced by:**

- Minkowski: each accepted patch is the next round's baseline.
- Lean: a proved lemma becomes a premise that later tasks may use.

**Change:** a *chain*: a campaign whose next task derives an input from an
earlier accepted run. The host derives it from recorded evidence, never from
worker output. For Minkowski, that input is the patched tree and its
measurements. For Lean, it is the accepted proof file, added to a later task's
library. Each task records its parents, so the lineage of any result is a chain
of receipts. A rejected, unproved, unknown, or unsupported run never advances
a chain. A superseded parent blocks dependent applications without invalidating
them ([invariant 2](../../AGENTS.md#invariants-to-preserve)).

### N9. Every job starts from nothing

Jobs share no host mounts, by design, so each check rebuilds from scratch.

**Forced by:**

- Minkowski: a full Rust build for each gate.
- Lean with Mathlib: rebuilding Mathlib's `.olean` files takes hours.

**Change:** *receipted build caches*: a build directory (`target/`, or
`.lake/build`) produced by a host-run build job, never imported from a worker or
an outside source. The host records a build receipt that binds the cache's
digest to the exact source tree, toolchain image, flags, and dependencies it was
built from. A checker job may use a cache only when its receipt matches the
job's own pinned inputs; otherwise the cache is refused and the job builds from
scratch or fails as unsupported. A cache digest alone identifies only its bytes,
not what produced them, so pinning is not enough. The cache is copied into the
job's scratch space, not mounted, so the boundary stays intact. For a patched
tree, the build tool rebuilds what the patch changed. `JobLimits` may need larger
scratch space.

### N10. The task layer runs only local models

The CLI refuses `--provider openrouter` because the task layer cannot yet pin
an OpenRouter model ([model adapters](../reference/model-adapters.md)). Only a
local OpenAI-compatible endpoint works.

**Forced by:** any comparison that includes frontier models. ARC and Lean
results are hard to read without one.

**Change:** carry the run-recorded model pin, which local models already use, to
the OpenRouter adapter, with its token and cost receipts.

### N11. Memory neither consolidates nor measures its own usefulness

Warranted has a fast memory tier. The ledger records every episode
immediately, append-only and with its origin. [Scoped memory](m7-scoped-memory.md)
shows later tasks the checker facts and worker notes from accepted submissions,
and it withholds them when the file bytes they depended on change. It has no
slow tier: nothing abstracts what many runs share into knowledge that applies
beyond one scope. And nothing measures whether a shown entry helped.

**Forced by:**

- ARC: reusable grid primitives, learned across tasks.
- Lean: general lemmas extracted from many accepted proofs, beyond the single
  promoted lemma of a chain (N8).
- Minkowski: lessons about which optimisations hold up under Miri and Loom.
- Goal (b): consolidation that edits the worker's helpers is agent improvement.

**Influences.** The two tiers follow complementary learning systems theory:
a fast learner stores separate episodes without overwriting, and a slow learner
extracts regularities through interleaved replay, which avoids catastrophic
interference ([McClelland, McNaughton, and O'Reilly, 1995](https://doi.org/10.1037/0033-295X.102.3.419);
[Kumaran, Hassabis, and McClelland, 2016](https://doi.org/10.1016/j.tics.2016.05.004)).
[DreamCoder](https://arxiv.org/abs/2006.08381) (Ellis and colleagues, 2021)
applies a wake-sleep cycle to program synthesis, growing a library of
abstractions from solved tasks. [Clauderizer](https://github.com/CollinCusce/Clauderizer),
by Collin Cusce and the Clauderizer contributors (Apache-2.0; read at version
2.0.3, commit `3b79259`), supplies two engineering methods:

- **Dream notes and triage.** An agent's short notes are distilled offline into
  proposals, which a person triages before they become memory
  ([`dreams.py`](https://github.com/CollinCusce/Clauderizer/blob/3b792597d2cfc05a72bcad82227eae007bbf41ca/src/clauderizer/dreams.py)).
- **Shown-versus-outcome telemetry.** It joins the lessons a session was shown
  with whether that session's phase passed
  ([`telemetry.py`](https://github.com/CollinCusce/Clauderizer/blob/3b792597d2cfc05a72bcad82227eae007bbf41ca/src/clauderizer/telemetry.py)). We borrow the tier split, the offline cycle, and the shown-versus-outcome
join. Our adaptation is that consolidated knowledge is a candidate that must pass
checks, not only triage, and that it keeps its provenance and goes stale. None of
this is implemented.

**Change, in three parts:**

1. **Measure usefulness.** The host already records each run's memory snapshot
   as an artifact, one per contract version, and gives it to the episodes under
   that version. Reports join each shown entry with the outcome of the run
   that saw it, and compare that with runs in the same scope that did not see
   it. That comparison is observational and confounded, and reports label it so.
   For a causal estimate, a development campaign may withhold entries at random,
   with the assignment pinned in the campaign plan, never chosen by the worker.
2. **Consolidate offline, as a candidate.** A consolidation run reads recorded
   runs from training and development splits only, and calls no live tools.
   It proposes a slow-tier entry: a rule, a helper, a lemma, or a strategy
   setting. The proposal cites its source runs and must pass the checks its
   kind requires. A lemma passes the Lean kernel. A helper passes tests on its
   source tasks. A rule passes when the tasks it summarises are re-run with it
   shown. Promotion also requires two things:
   - **No interference:** no regression on tasks that were already solved.
     This is the interleaved replay that complementary learning systems call
     for.
   - **Transfer:** improvement on development tasks that are not among its
     sources.

   Human triage may be added, but it never replaces the checks.
3. **Keep provenance and staleness.** A slow-tier entry cites its source runs
   and the premises it was checked against. When a premise is superseded, the
   entry is withheld, as scoped memory already does with facts, and its valid
   checks stay recorded ([invariant 2](../../AGENTS.md#invariants-to-preserve)).

Consolidation differs from chains (N8). A chain carries one accepted result into
the next task; consolidation abstracts across many runs. When consolidation
changes the worker's prompts, helpers, or strategy, it is goal (b) and follows
the [same rules](#goal-b-later).

## The use cases

### Minkowski optimisation

[Minkowski](https://github.com/Lewdwig-V/minkowski) is a Rust storage engine for
real-time applications. The project owner set two goals on 2026-10-03:

- **(a), first: improve Minkowski's code.** Accept an optimisation only when
  Minkowski's own correctness checks pass and a pinned measure improves. The
  accepted change then becomes the next baseline.
- **(b), eventually: improve the agent.** See [goal (b)](#goal-b-later).

Minkowski is a good consumer because both its correctness and its objective are
machine-checkable:

- **Correctness gates.** About 900 unit tests; clippy with warnings as errors;
  fmt; a Miri subset; TSan; and Loom. Its CI runs in about four minutes.
- **Noise-free measures.** Besides criterion timings, it has
  [iai-callgrind](https://github.com/iai-callgrind/iai-callgrind) benchmarks
  (`minkowski-bench --bench ecs_icount`, `minkowski-lsm --bench page_codec`).
  These count instructions. Its `scripts/capture-bench-baseline.sh` calls them
  "the machine-independent regression signal".
- **A record of past gains.** Its `docs/performance.md` records optimisations
  with measured gains.
- **A real risk of unsound speedups.** Lock-free allocators and unsafe column
  storage mean a faster change can be subtly wrong. The gates decide, not the
  agent.

One round is one run:

1. **Pin.** Fix the tree, the toolchain image, and the baseline measurements.
2. **Work.** The worker edits and profiles in its sandbox, then submits a
   patch (N1).
3. **Gate.** Jobs apply the patch to the pinned tree and run fmt, clippy, the
   tests, and the Miri and Loom subsets.
4. **Measure.** The instruction-count benchmarks run on the patched tree (N2,
   N9).
5. **Accept.** The patch is accepted on a sufficient improvement with no
   regression beyond tolerance.
6. **Promote.** The accepted patch becomes the next baseline (N8).
7. **Validate.** At set points, measure separate *development* benchmarks that
   rounds never optimise against directly. A regression there stops promotion.
   Because that decision feeds back into later rounds, these benchmarks are
   development data, not held out.
8. **Hold out.** A frozen chain is measured once on held-out benchmarks. Their
   results never steer promotion. Optimising further after a held-out
   measurement retires that held-out set, and later claims need a fresh one
   ([invariant 7](../../AGENTS.md#invariants-to-preserve)).

Warranted never pushes to Minkowski. Merging stays a human decision, through an
ordinarily reviewed PR.

### ARC-AGI-1/2

Static [ARC](https://arcprize.org/) tasks fit today's task shape. The training
pairs are inputs, the worker writes a program, and a checker runs the program on
those pairs. The test output is a private file, scored by the host after
acceptance as a separate result. It never enters feedback. A program that fits
every training pair is accepted with basis `example` and is not thereby correct
on the test input ([invariant 8](../../AGENTS.md#invariants-to-preserve)).

The fixture is cheap and forces N3, the strategy layer, because ARC rewards
generating and filtering many candidates. Its results are engineering evidence
only. The public sets are widely published, so capability claims need private or
held-out tasks.

### ARC-AGI-3

[ARC-AGI-3](https://arcprize.org/blog/arc-agi-3-launch) is interactive: an agent
explores a game with no instructions and is scored on how efficiently it wins.

The use case forces three changes:

- **N4:** one game session per run, advanced by recorded actions.
- **N2:** the score is the objective.
- **Containment:** the game engine runs locally in a pinned image. Network
  access to a hosted engine would be an external effect, so a reviewed design
  must come before any use of one.

It comes after the stateless cases.

### Logic and constraint puzzles with an SMT solver

Puzzles such as Sudoku, KenKen, nonograms, and zebra puzzles have exact answers,
and SMT solvers such as [z3](https://github.com/Z3Prover/z3) and cvc5 solve them
directly. The worker may encode a puzzle in SMT-LIB with a solver from its image,
or reason without one. The interesting question for the harness is not the
solver but the claim the worker makes:

- **"This is a solution."** The checker evaluates every constraint on the
  assignment: basis `exhaustive`.
- **"There is no solution."** A pinned solver checks this in a checker job
  (basis `solver`), optionally with a certificate (N7).
- **"This is the only solution."** A solution plus an `unsat` check.
- **"This is optimal."** For optimisation variants: a measurement and an
  objective (N2), plus an `unsat` check that nothing scores better.

Puzzle generators can produce unlimited fresh, held-out tasks with known
lineage, unlike the public ARC and Lean sets. That makes this the cheapest
setting for honest held-out evaluation and for goal (b).

### Theorem proving in Lean

`LeanProof` already verifies a worker's Lean file against a domain-owned target
under a fixed axiom policy (#64), so a single-theorem task runs today. A
benchmark of theorems needs N5: per-task targets, a pinned Mathlib, and a
reviewed policy that allows `Classical.choice`. N9 keeps Mathlib verification
affordable, N3 lets a run try several proofs, and N8 lets proved lemmas
compound. Interactive proving (N4) is optional.

## What stays out of the core

None of these use cases should add domain vocabulary to Warranted:

- **No domain formats or toolchains.** No grid format, SMT-LIB parser, Rust
  toolchain, or game API. Each is a checker, a job image, or a worker image in
  an example or a consumer.
- **Library contents are not core.** Lean's verifier is already core
  infrastructure, but which libraries a target may import is the domain's
  choice, made through a named policy.
- **Domain semantics stay with the domain.** Objectives, trust bases, sessions,
  chains, and strategies are generic shapes. What a measure means, which
  solver is trusted, and which game is played belong to the domain.

## Negative cases to write first

- **Changing the ruler.** A patch that touches paths outside those the task
  allows, such as benchmarks, tests, CI, or measurement scripts, is rejected
  before any measurement.
- **Gates beat speed.** A faster patch that fails any gate, including Miri or
  Loom, is rejected. Its measurements are not recorded as an improvement.
- **Worker numbers are not measurements.** A worker-supplied number cannot
  become a measurement or a game score. Only a checker job's or a host session's
  recorded output can.
- **Objective thresholds hold.** An improvement below the margin, or a
  regression beyond tolerance on another in-sample measure, is rejected.
- **Chains advance only on acceptance.** A chain never advances from a run that
  was not accepted. A restarted chain resumes from the last accepted run.
- **Validation regressions stop promotion.** This applies even when every
  in-sample measure improves.
- **Held-out results never steer.** No promotion or later round reads a held-out
  result. A chain that continues after a held-out measurement marks that set as
  exposed, and reports refuse to call it held out.
- **Caches match their receipts.** A build cache whose receipt names a different
  source tree, toolchain, flags, or dependencies is refused, as is a cache with
  no host build receipt.
- **Bad patches are rejections.** A patch that does not apply cleanly is a
  rejection, not an infrastructure failure.
- **Theorems come from the contract.** A Lean candidate cannot supply its own
  theorem statement or library. A per-task target comes only from a pinned task
  input.
- **Axioms are checked against the named policy.** A proof that uses
  `Classical.choice` fails under the strict policy and passes only under the
  named policy that allows it.
- **No solver, no `unsat` acceptance.** An `unsat` claim without a checker-side
  solver run is not accepted. A solver timeout is `UNPROVED`, not `REJECTED`.
- **Only the named trust bases count.** A check that accepts only `certificate`
  is not satisfied by `solver`, nor by `kernel`, and an unknown basis is
  refused.
- **Session replay is history only.** A host session's replay reveals only
  recorded steps and cannot start a live game.
- **Consolidation never reads held-out runs.** A consolidation run that cites a
  held-out run is refused, and so is one that cites a run that was not
  accepted.
- **Unchecked consolidation is not memory.** A slow-tier proposal that has not
  passed its checks is never shown, and never shown as verified.
- **Interference blocks promotion.** A slow-tier entry that causes a regression
  on an already-solved task is not promoted, even if it improves others.
- **Observation is not causation.** A usefulness report labels observational
  comparisons as such. A causal claim needs a pinned random withholding plan.
- **Private outputs stay private.** An ARC test output never appears in
  feedback, memory, or worker-visible exports.

## Goal (b), later

Improving the agent adds a second level. These rules keep it honest:

- **The optimiser changes only the worker.** It may change prompts, tools,
  worker-image helpers, and strategies. It may never change checkers, objectives,
  trust bases, benchmarks, baselines, budgets, or held-out sets.
- **Each agent version is evaluated as a campaign.** It runs over the same tasks
  and budget, with the version pinned by digest. Promotion decisions use
  development tasks. Held-out tasks, which no version was tuned or selected on,
  measure a frozen version once and are then retired. Generated puzzles make
  fresh held-out sets cheap.
- **Costs are part of the result.** A version is never ranked by improvement
  alone.
- **It builds on M6.** [M6](../roadmap.md#m6--dream-rsi-adaptation-for-search-improvement)
  already plans this shape for scheduling policies; goal (b) builds on what M6
  delivers instead of adding a parallel mechanism.

## Proposed order

Each step takes the cheapest use case that forces it. It must be confirmed by a
second use case before its interface stops being provisional.

1. **N3 and N10, with ARC-AGI-1/2.** A strategy that tries several candidates,
   and frontier models in the task layer. Confirmed later by Lean and Minkowski.
2. **N6 and N7, with SMT puzzles.** Trust bases, and the solver-checked `unsat`
   pattern. Confirmed by Lean, which already has a kernel basis.
3. **N5 and N9, with Lean on a small Mathlib benchmark.** Per-task targets, a
   pinned library, a named axiom policy, and receipted build caches. Confirmed by
   Minkowski's build cache.
4. **N1 and N2, with one Minkowski round.** Patch candidates and measured
   objectives. Confirmed by optimisation puzzles.
5. **N8, with Minkowski chains and Lean lemma libraries.** This completes goal
   (a).
6. **N11, measuring then consolidating.** Usefulness reports can come early,
   because they use the existing memory. Consolidation follows chains, starting
   with Lean lemmas, whose checks are strongest, and is confirmed by ARC helpers.
7. **N4, with ARC-AGI-3.** Host sessions, after their reviewed design.
8. **Goal (b)**, on top of M6.

## Questions for review

1. Is the trust-basis set (N6) right, and should a task name the bases it
   accepts for every check, or only for checks that opt in?
2. Should instruction counts be the only gated Minkowski measure, with
   wall-clock recorded but never gated? This note proposes yes.
3. Which Minkowski benchmarks are in-sample and which are held out, and which
   paths may a patch touch? This note proposes source under `crates/*/src` only.
   Both are Minkowski's call.
4. For Lean, is a second, named axiom policy with `Classical.choice`
   acceptable, given that M4's targets keep the strict one?
5. Should ARC-AGI-3 wait for a local engine, or should the host-session design
   also cover a networked one as an external effect?
6. Should accepted Minkowski patches reach Minkowski as PRs opened by a human?
   This note proposes yes, at least until goal (b).
7. Should slow-tier promotion require human triage in addition to its checks,
   at least until usefulness reports exist?
