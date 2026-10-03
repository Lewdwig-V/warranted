# M7 proposal: harness API and CLI

Proposed 2026-10-02. This is a design for review, not a description of existing
behavior. It covers the [M7 roadmap items](../roadmap.md#m7--stable-harness-api-and-cli)
except host-mediated operations, which get their own [design document](m7-host-operations.md).

## Problem

Warranted has tested low-level parts: the [evidence ledger](../reference/evidence-ledger.md),
[claims and acceptance](../reference/claims-and-acceptance.md), the
[worker and container sandbox](../reference/worker-and-containment.md), and
[proof verification](../reference/proof-verification.md). It has no layer that
turns them into a task. Each driver under `examples/` spends 400–900 lines
creating a project, pinning a manifest, wrapping checker runs in ledger
operations, building an acceptance policy and resolver, running episodes, and
handling crashes. Several import private helpers such as `_encode`, `_digest`,
and `_run`.

ReSchema's `engine.py` carries the same responsibilities around its
reverse-engineering checkers. M7 adds the missing task layer so that both the
existing fixtures and ReSchema can be written against one public API.

## Decisions so far

| # | Question | Decision |
| --- | --- | --- |
| 1 | How are tasks defined? | TOML files. Python can build the same `TaskSpec` objects. |
| 2 | Is the low-level host kit public? | Yes, importable as `warranted.host`, but outside the semantic-versioning promise for now. |
| 3 | Concurrency | One project per campaign, runs serialised under the existing single writer. Parallel runs are a later optimisation. |
| 4 | Worker-visible feedback | Each checker decides what to reveal. Warranted only guarantees that host-only verdict data never reaches the worker. |
| 5 | Submit, feedback, retry | Each submission ends an episode. The next episode starts in a fresh container with the restored workspace and the new feedback. |
| 6 | Task versus run | A task says what to solve; a run configuration says who solves it and with what limits. |
| 7 | Memory | Verified facts record what they depend on and become stale when a dependency changes. Included in M7. |
| 8 | Domain builds | Corpus builds and similar steps stay in the domain project. Warranted imports their outputs as pinned artifacts. |

## Layers

- **`warranted` (task layer, stable at 1.0):** `Project`, `TaskSpec`, `RunConfig`,
  `Domain`, `Checker`, `CheckContext`, `Verdict`, `Run`, `Fact`, and result types.
  The CLI uses only this layer.
- **`warranted.host` (host kit, public but unversioned):** the current ledger,
  acceptance, claims, sandbox, proof, and adapter modules. Existing modules move
  behind it; private helpers that examples use today get public equivalents or
  stop being needed.

## Concepts

| Concept | Meaning |
| --- | --- |
| Project | One ledger directory and its single writer. A campaign uses one project. |
| Domain | Trusted Python supplied by the consumer: worker image, workspace preparation, checkers, and candidate normalisation. |
| Task | A TOML file: objective, pinned inputs, checkers, memory scope, and task-level budgets. |
| Run configuration | Model, provider, token and step limits, and credentials file. |
| Run | One attempt at a task under one run configuration. Resumable. |
| Episode | One worker session in one container, ending at a submission or a limit. |
| Submission | The candidate files, optional notes, and nominated cases captured at the end of an episode. |
| Verdict | A checker's typed result for one submission. |
| Fact | A memory entry with a tier (verified or unverified), a scope, and recorded dependencies. |

## Task and run files

```toml
# tasks/rot13-gcc-O2-sym.toml
id = "rot13::gcc-O2-sym"
objective = "Write a C model whose observable behaviour matches the binary."
memory_scope = "rot13"

[inputs]
binary = { artifact = "sha256:…" }          # imported with `warranted import`
manifest = { artifact = "sha256:…" }

[[checks]]
name = "program"
checker = "program-replay"                  # a key in the domain's checker table
required = true

[budgets]
submissions = 12
checks = 24
```

```toml
# runs/qwen-dev.toml
model = "qwen3.8:27b"
provider = "ollama"
max_steps = 12
max_tokens = 3072
timeout_seconds = 180
```

The worker image belongs to the domain alone, not to the run configuration. The
host records the image digest the domain declares with each run, so a run's
record always names the environment it executed in, and a changed image blocks
resume like any other changed domain input.

A campaign file lists tasks, run configurations, and repetitions, and pins them
all before the first run, as the existing trial plans do.

## Domain interface

```python
class Domain(Protocol):
    name: str
    version: str  # recorded with every run
    worker_image: str  # pinned digest; tools for the worker
    checkers: Mapping[str, Checker]
    sources: Sequence[Path]  # optional: other files the checkers depend on

    def prepare(self, task: TaskSpec, ctx: PrepareContext) -> Workspace: ...
    def normalize(self, candidate: Files) -> bytes: ...  # for the duplicate guard
```

The CLI loads a domain from a `module:object` reference, for example
`warranted init runs/reschema --domain reschema.domain:ReSchema`. There is no
plugin registry. The domain's name, version, and source digest are pinned in the
project manifest, so a changed domain blocks resume the same way a changed fixture
does today. The digest covers the source files of the domain and checker classes
and every file under the domain's declared `sources`; imports are not followed,
so code loaded any other way must be declared. The Warranted version and a digest
of its package files are pinned as well.

`prepare` writes the task files the worker sees. `PrepareContext` exposes the task
inputs and the facts in the task's memory scope. Facts are written under
`/work/context/` with their tier marked.

## Checker interface

```python
class Checker(Protocol):
    name: str
    version: str

    def check(self, ctx: CheckContext) -> Verdict: ...


class CheckContext:
    candidate: Files  # captured bytes, not worker paths
    inputs: Files  # the task's pinned inputs
    private: Files  # host-only data, never mounted for the worker

    def draw_seed(self) -> int: ...  # recorded as host-only evidence
    def run_job(
        self, image: str, argv: Sequence[str], files: Files, *, timeout_seconds: int
    ) -> JobResult: ...


@dataclass(frozen=True)
class Verdict:
    status: VerdictStatus  # passed, rejected, unsupported, ...
    feedback: JSON  # shown to the worker
    host_only: JSON = None  # recorded, never shown to the worker
    facts: Sequence[FactSpec] = ()  # recorded as verified if accepted
```

Checkers are trusted host code, like ReSchema's in-process qiling recorder. A
checker that touches no state shared with other runs declares `isolated = True`;
only isolated checks are confined to their run's [ledger scope](m7-run-scopes.md).
Untrusted code runs only through `run_job`: a one-shot container from a pinned
image, with no network and only a per-job scratch directory mounted. The check is
the ledger operation: its seeds, job records, job output, and verdict are recorded
as evidence of that one operation, so a completed check is reused on resume and an
interrupted one stays unknown. Jobs are not separate operations; see
[question 7](#answers-to-open-questions-1-5-and-7).

The host builds the acceptance decision from the task's required checks. A
missing, unknown, or stale required verdict blocks acceptance, as it does now.
Lean verification becomes a built-in checker with the same interface.

## Worker convention

| Path | Meaning |
| --- | --- |
| `/work/task.md`, `/work/inputs/` | Task description and inputs written by `prepare` |
| `/work/context/` | Facts from memory, each marked verified or unverified |
| `/work/feedback/NNN.json` | Feedback for each earlier submission |
| `/work/submission/` | Candidate files for the next submission |
| `/work/submission/notes.json` | Optional worker notes, stored as unverified facts |
| `/work/submission/cases.json` | Optional nominated cases |
| `submit` | Helper in the worker image: validates the layout, then prints the marker |

The domain's worker image includes the `submit` helper and any investigation tools,
such as ReSchema's emulator and `experiment` command. The submission-control
experiments showed that an explicit layout and a helper command matter.

## Run lifecycle

1. The host prepares the workspace and starts an episode.
2. The episode ends at `submit`, the step limit, or a worker failure.
3. On submission, the host captures the files, applies the duplicate guard, and
   runs the required checkers.
4. If every required check passes, the run is accepted and verified facts are
   recorded; notes from this submission are promoted.
5. Otherwise the feedback is written and, if budget remains, a new episode starts
   with the restored workspace.

A run ends with one outcome:

| Outcome | Meaning | CLI exit code |
| --- | --- | --- |
| `accepted` | All required checks passed on a submission | 0 |
| `rejected` | Budget exhausted after one or more rejected submissions | 1 |
| `incomplete` | Budget or steps exhausted with no submission | 2 |
| `unknown` | An operation's outcome could not be established; the run is blocked | 3 |
| `unsupported` | A required check cannot assess this task | 4 |
| `infrastructure_failure` | The host could not run a required step | 5 |

Every run has an explicit identifier. `project.start(task, config)` creates a new
run with a fresh ID and records the task, configuration, and domain identity
against it; `project.resume(run_id)` continues that run. Starting the same task
and configuration twice creates two independent runs, so repetitions never
merge, and failed attempts keep their own accounting. `resume` refuses a run
whose recorded task, configuration, or domain no longer matches. After a crash,
completed episodes, jobs, and checks are reused; unknown operations stay blocked
and are reported.

## Budgets and the duplicate guard

Task budgets cover submissions and checks; run budgets cover model calls, shell
commands, and tokens. The host enforces them all; a budget is never a worker
instruction. The duplicate guard normalises each candidate with the domain's
`normalize` and refuses a submission whose fingerprint repeats too often, using
configurable thresholds for exact repeats and small edits (ReSchema's current
values are 3 and 4). A refused submission still counts against the budget.

## Contract revisions

Both fixtures depend on owner-approved contract revisions, and M7 requires them to
run through the public API with their current guarantees. Revisions are therefore
part of the task layer.

- A task's contract is its required checks plus the interpretation documents
  listed in the task file. Each version of the contract has its own identity.
- A revision is a TOML file naming the owner, the reason, the affected checks or
  documents, and the new versions. It is recorded only through
  `project.revise(...)` or `warranted revise`, both host operations. Nothing the
  worker writes can create or select a revision.
- Acceptance always uses the task's current contract. A verdict produced under an
  earlier revision becomes stale for the affected checks and is re-run; it is
  never rewritten, so an old rejection stays on record.
- A task can schedule a pinned revision at a run checkpoint, such as after the
  first submission, as both fixtures do. The host applies it at that point, and a
  restart before or after the checkpoint resumes on the same contract version.
- An unscheduled revision applies to the task, not to runs already in progress.
  Resuming such a run is refused because its contract no longer matches, as with
  a changed domain or worker image. A new run uses the revised contract; the old
  run keeps its record and costs.

Every run is therefore bound to one contract version or to one planned sequence
of versions. Revising a run in flight would add a new boundary for little gain,
since runs are resumable and a new run is cheap to start.

Owner identity is attribution from trusted local files, as it is today; remote
owner authentication remains out of scope.

## Memory

A fact has a scope (for example a ReSchema family), a tier, a body, and
dependencies on evidence: input artifact digests, the checker's name and version,
and the domain version. Checker-supplied facts are verified when their submission
is accepted. Worker notes are unverified and are promoted only when their own
submission is accepted.

Facts are built on the existing claims API. When a dependency changes, for
example a re-recorded binary or a new checker version, the fact is reported as
stale and is no longer presented as verified. ReSchema's manual
canonicaliser-version check becomes an ordinary dependency.

## CLI

```
warranted init DIR --domain MODULE:OBJECT
warranted import DIR FILE...                  # pin domain-built artifacts
warranted run DIR TASK.toml --config RUN.toml # always a new run; prints its ID
warranted resume DIR RUN
warranted revise DIR TASK REVISION.toml       # record an owner-approved revision
warranted status DIR [RUN] [--json]
warranted export DIR RUN DEST
warranted campaign run DIR CAMPAIGN.toml
warranted campaign report DIR CAMPAIGN [--json]
```

All commands use the public API. `--json` output is versioned with it. A
campaign assigns each planned run its ID when the plan is pinned, so a resumed
campaign continues exactly the runs it planned.

## How existing code maps

| Today | Under this proposal |
| --- | --- |
| `examples/m1`–`m4` CSV drivers | One CSV domain, tasks for the offset and annotation revisions, the uniqueness proof as a Lean check |
| `examples/m5` migration drivers | One migration domain; `treatments.py` and `trials.py` become a campaign |
| ReSchema `engine.TaskStore`, ledger counters, audit seeds | Project ledger, run accounting, recorded seeds |
| ReSchema program and function gates | Two checkers using `run_job` and `draw_seed` |
| ReSchema `driver/podrun.py` | `run_job` |
| ReSchema `memory.py` | Facts with scope `family` |
| ReSchema MCP tools and opencode runner | Worker convention, domain worker image, model adapters |
| ReSchema dogfood campaigns | Warranted campaigns |

## Out of scope for M7

- Host-mediated operations, implemented in the prototype from [their design](m7-host-operations.md).
- Parallel runs and multiple writers.
- An MCP interface to Warranted.

## Prototype findings

### Slice 1: CSV task through the task layer

`warranted.experimental` implements TOML tasks, a domain's checkers, explicit run
IDs, one episode per submission, worker-visible feedback, and the six run outcomes.
It reuses the existing worker, ledger, and acceptance boundary: each required check
becomes a gate on a `passed` field. `examples/m7/csv/` wraps the M2 transformation
evaluator as one checker. `tests/test_experimental_tasks.py` runs it with a
scripted model and a scripted environment; the real container sandbox is not
exercised in this slice.

What worked:

- Acceptance needed no changes. Rejected, unsupported, unknown, and
  infrastructure-failure verdicts stay distinct through the existing gate.
- One episode per submission fits the current worker: feedback files and the
  restored workspace are ordinary episode inputs, and resume reuses completed
  episodes, checks, and decisions without new model, tool, or check charges.
- Private task files never reach episode inputs, and host-only verdict data stays
  out of the feedback file.

What did not:

- **Budgets are project-wide.** Ledger allowances are fixed when the project is
  created, so per-run model, tool, and check budgets cannot be enforced by the
  ledger. Only the submission budget is enforced, by the run loop. With one
  project per campaign, the ledger needs run-scoped allowances.
- **An unknown operation blocks every run.** The worker refuses to dispatch while
  any operation in the project is unknown, so one lost response in one run turns a
  fresh run in the same project into `unknown` without any new attempt. Blocking
  needs to be scoped to the run, or campaigns cannot survive a single lost response.
- Both findings above are now addressed by [run scopes](m7-run-scopes.md): each run
  has its own ledger scope with caps under the project total, and unknown
  operations and breaches block only their own run.
- **Domain identity misses imported code.** The source digest covers the domain's
  own file, but the CSV checker imports the M2 evaluator, so a change there would
  not block resume. This answers open question 3: a source digest alone is not
  enough.
- **The worker convention is not implemented.** The current sandbox captures only
  `result.json` and `workspace/`, and uses its own pinned image. The prototype keeps
  that convention, records the domain's `worker_image` without using it, and passes
  feedback as `feedback-NNN.json` input files.

Evidence on the open questions:

- Question 1: the restored workspace plus feedback files were enough for scripted
  runs. Live-model behaviour is untested.
- Question 4: the CSV checker's feedback is 293 bytes; no reason for a cap yet.
- Question 5: the prototype imports `Acceptance`, `AcceptanceContext`, `Evidence`,
  `Status`, `Ledger`, `Manifest`, `Origin`, `Outcome`, `Request`, `Result`,
  `Sandbox`, `SANDBOX_ID`, `Episode`, `UnknownOutcome`, `record_once`,
  `run_workflow`, and `submitted_files`, and no private helpers.

### Slice 2: a ReSchema-style task with hidden cases

`examples/m7/mystery/` asks the worker to model a small black-box program. The
worker submits `result.json` with a model and up to 16 nominated cases. The
checker draws hidden cases from a host-recorded seed and private configuration,
then runs the original and the model in two contained jobs (`warranted.jobs`).
Feedback shows the first divergence on a nominated case but only counts for hidden
cases. `tests/test_experimental_mystery.py` runs it with a local stand-in job
runner; `tests/test_jobs.py` checks the Podman runner natively in CI.

What worked:

- `CheckContext.draw_seed` and `run_job` keep seeds and job records as host-only
  channels of the check operation. Resume reuses the check without new jobs or
  seeds, and each new run draws fresh hidden cases.
- A memorising model passes its nominated cases and fails hidden ones, and the
  worker's feedback never contains a hidden input or the seed.
- A looping model is rejected with feedback; a job-runner failure is an
  infrastructure failure. Malformed submissions are rejected before any job.
- The `[private]` task section was enough for the checker's hidden-case
  configuration.

What changed or did not fit:

- **Nominated cases need no generic field.** The domain reads them from its own
  submission payload, so `CheckContext.nominated_cases` is unnecessary.
- **Jobs cannot be separate ledger operations.** A check is an in-flight
  operation while its jobs run, and `begin` refuses dispatch in a scope with an
  in-flight unknown operation. Jobs are therefore recorded inside their check
  (`jobs.json`, with digests of files and input, plus each job's output stored as
  its own raw channel) and are not charged as a separate unit. Charging or recovering jobs individually would need parent and
  child operations.
- **A job's program can shape its own result record.** It runs as the same user as
  the in-container runner. That is acceptable because it can only misreport its
  own behaviour, which it controls anyway; job output is never host evidence about
  anything else.
- **The job runner is part of the project identity.** A checker's verdict depends
  on how its jobs were contained, so a project refuses to reopen with a runner
  whose `identity` differs.
- The worker convention is unchanged: cases travel inside `result.json`, not a
  separate `cases.json`.

### Answers to open questions 3 and 4

**Domain identity (question 3).** Following imports cannot find all the code a
checker runs: the CSV checker loads the M2 evaluator with `runpy.run_path`, which
leaves no import to follow. The domain therefore declares the other files it
depends on as `sources`, files or directories, and the host digests them with the
domain's and checkers' class source files. Each file is named by its role and
relative path, not its absolute path, so moving a checkout keeps the identity, and
editing, adding, or removing a file changes it. A declared path that does not
exist is refused. Undeclared code is not pinned; that is the domain author's
responsibility, and the reason the declaration exists. The installed Warranted
build is also part of the identity, since the host's own checking and acceptance
code shapes every verdict: its version, and a digest of the package's files,
because an editable install keeps its version string while its code changes. Third-party packages are left to the domain's lockfile.
`examples/m7/csv/` declares the M2 evaluator.

**Feedback size (question 4).** The host enforces a 64 KiB limit on a verdict's
encoded feedback, since feedback enters the worker's context. Larger feedback
turns the verdict into an infrastructure failure: truncating it could mislead the
worker, and rejecting would turn a pass into a false rejection. The oversized
feedback, the original status, and the original host-only data are kept as
host-only evidence. The CSV checker's feedback is 293 bytes, so the limit only
catches faults.

### Answers to open questions 1, 5, and 7

These are decisions from the prototype's evidence, not measurements. Each says
what would reopen it.

**Follow-on episodes (question 1).** A follow-on episode receives the restored
workspace and the feedback files, not the earlier conversation. State the worker
needs to carry forward belongs in the workspace, as files it chose to write, which
is the legible state this harness is built around; a transcript is the model's
private scratch space and grows with every step, which matters once
[token budgets](m7-token-budgets.md) bind. A fresh conversation also makes each
episode's input exactly the recorded files, which keeps replay and resume simple.
The evidence is weak: scripted runs in both slices needed nothing more, and live
models have not been tried. Reopen it if M8 shows live workers repeating work that
the earlier conversation would have prevented and that workspace notes do not.

**Host kit names (question 5).** The domain code of both prototype domains imports
only `CheckContext`, `Verdict`, and `VerdictStatus` from the task layer, plus a
pinned image constant from `warranted.sandbox`. The fixture runners in
`examples/m1` to `examples/m5` import far more, including private helpers
(`_encode`, `_digest`, `_json_object`, `containers._run`), because each runner
drives the ledger, worker, and acceptance directly; on the task layer, `Project`
does that driving. What the host kit must keep exposing is therefore what the task
layer does not yet cover:

| Need | Names | Until |
| --- | --- | --- |
| Model services passed to `Project.start` | `LocalChatCompletions`, `OpenRouterChatCompletions` | Permanent; adapters are host-kit components |
| Evidence export | `export_evidence` | The CLI gains an export command |
| Proof checks for the migration fixture | `Proofs`, `proof_status`, `Applicability`, `Claims` | The migration fixture moves onto the task layer |
| Worker image for a domain | `warranted.sandbox.IMAGE` | Domains supply their own pinned worker images; `IMAGE` remains the default and the example domains' image |

Private helpers get no public equivalent unless a migrated fixture still needs
one. The M5 research runners are not migrated; they stay on the host kit as they
are. The answer is provisional until the migration fixture actually moves.

**Checker jobs (question 7).** Jobs stay evidence inside their check, as slice 2
built them. A job is contained, offline, bounded in time, and has no external
effect, and its cost is covered by the check's own unit. Making jobs separate
operations needs parent and child operations in the ledger, because `begin`
refuses dispatch in a scope with an in-flight operation, and that is a new
mechanism with no demonstrated need. Two things would reopen it: a job expensive
enough to need its own charge or budget, or a job that needs network, credentials,
or another external effect. The second is a host-mediated operation, not a
checker job, and belongs to that design.

## Open questions

1. Answered provisionally: only the restored workspace and feedback. See
   [open questions 1, 5, and 7](#answers-to-open-questions-1-5-and-7).
2. Provisionally answered by slices 1 and 2: a `[private]` section of the task
   file. Revisit if private data must be large or shared between tasks.
3. Answered: the domain declares `sources`, and the host digests them with the
   class source files. See [open questions 3 and 4](#answers-to-open-questions-3-and-4).
4. Answered: yes, 64 KiB, and oversized feedback is a checker fault. See
   [open questions 3 and 4](#answers-to-open-questions-3-and-4).
5. Answered provisionally from the current imports; final once the migration
   fixture moves. See [open questions 1, 5, and 7](#answers-to-open-questions-1-5-and-7).
6. Answered: budgets and blocking are scoped to a run by ledger scopes. See the
   [run scopes design note](m7-run-scopes.md).
7. Answered: jobs stay evidence inside their check until a job needs its own
   charge or a side effect. See
   [open questions 1, 5, and 7](#answers-to-open-questions-1-5-and-7).
