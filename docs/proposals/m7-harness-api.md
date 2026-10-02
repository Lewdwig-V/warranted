# M7 proposal: harness API and CLI

Proposed 2026-10-02. This is a design for review, not a description of existing
behavior. It covers the [M7 roadmap items](../roadmap.md#m7--stable-harness-api-and-cli)
except host-mediated operations, which get their own design document.

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

    def prepare(self, task: TaskSpec, ctx: PrepareContext) -> Workspace: ...
    def normalize(self, candidate: Files) -> bytes: ...  # for the duplicate guard
```

The CLI loads a domain from a `module:object` reference, for example
`warranted init runs/reschema --domain reschema.domain:ReSchema`. There is no
plugin registry. The domain's name, version, and source digest are pinned in the
project manifest, so a changed domain blocks resume the same way a changed fixture
does today.

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
    nominated_cases: Sequence[bytes]  # hints; the checker recomputes truth
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

Checkers are trusted host code, like ReSchema's in-process qiling recorder.
Untrusted code runs only through `run_job`: a one-shot container from a pinned
image, with no network and only a per-job scratch directory mounted. Each job,
seed, and verdict is a ledger operation, so a completed check is reused on resume
and an interrupted one stays unknown.

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
- To reproduce the fixtures, a task can schedule a pinned revision at a run
  checkpoint, such as after the first submission. The host applies it at that
  point, and a restart before or after the checkpoint resumes on the same
  contract version.

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

- Host-mediated operations, pending their own design.
- Parallel runs and multiple writers.
- An MCP interface to Warranted.

## Open questions

1. Does a follow-on episode receive the earlier conversation, or only the restored
   workspace and feedback? The current worker links episodes but starts each one
   from its inputs.
2. How are a task's private inputs supplied: a private section of the task file,
   or artifacts imported with a host-only flag?
3. How is the domain's code identified for pinning: package version, a digest of
   its source files, or both?
4. Should verdict feedback have a size limit enforced by the host?
5. Which `warranted.host` names do the fixtures still need once they move onto the
   task layer? That list decides what the host kit has to keep exposing.
6. Is a scheduled revision checkpoint expressive enough for future consumers, or
   should a revision also be applicable to a run that is already in progress?
