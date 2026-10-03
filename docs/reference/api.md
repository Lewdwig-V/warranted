# Public API

A domain project builds on `warranted`, the task layer. This page lists every
name it exports, what the name is for, and where its behaviour is documented. A
test checks that this list and `warranted.__all__` are the same set, so a name
cannot be exported without being documented here, or documented without being
exported.

`warranted.host` is the low-level host kit: public, but outside semantic
versioning. Its names may change between minor releases; each change is listed in the
[changelog](../../CHANGELOG.md). Modules whose names start with an underscore are
private and may change without notice.

## Versions and deprecation

Warranted uses [semantic versioning](https://semver.org/spec/v2.0.0.html) for
`warranted`, its CLI (commands, options, exit statuses, and `--json` output
versions), and the task, revision, run, and campaign files a user writes. It does
not cover project directories: see *Projects and upgrades* below.

- **Before 1.0**, a minor release (0.x to 0.x+1) may change or remove public
  names; a patch release does not. Every change is in the changelog.
- **From 1.0**, only a major release removes or incompatibly changes a public
  name.
- **Deprecation.** A name is deprecated for at least one minor release before it
  is removed. A deprecated name keeps working, raises `DeprecationWarning` when
  used, names its replacement, and is listed under *Deprecated* in the changelog
  entry that deprecates it.
- **Projects and upgrades.** A project is bound to the exact Warranted build
  that created it: its identity pins the package version and a digest of the
  package's files. Every upgrade, including a patch release, refuses to open a
  project created by another build, so recorded state is never reinterpreted.
  Finish or export a project's runs before upgrading, then create a new project.
  Opening older projects is not supported in any release so far; a release that
  adds it will say so here and in the changelog.

Domain projects such as ReSchema pin a tagged release
(`warranted @ git+https://github.com/Lewdwig-V/warranted@vX.Y.Z`) until 1.0 is
published to PyPI.

## Releasing

1. Move the changelog's *Unreleased* entries under a new `## [X.Y.Z] - DATE`
   heading.
2. Set `version = "X.Y.Z"` in `pyproject.toml` and run `uv lock`.
3. Merge through a pull request with the usual checks green.
4. Tag the merge commit and push the tag; CI runs on the tag and fails if the
   tag is not `v` plus the package version:

   ```bash
   git tag -a vX.Y.Z -m "Warranted X.Y.Z" && git push origin vX.Y.Z
   ```

## Projects and runs

| Name | What it is | Reference |
| --- | --- | --- |
| `Project` | One ledger and its single writer: create, start, resume, status, export, revise, memory, campaigns | [CLI](cli.md), [run lifecycle](../proposals/m7-harness-api.md#run-lifecycle) |
| `RunConfig` | The model identity, step limit, and per-run budget caps | [run configuration](cli.md#run-configuration) |
| `RunResult` | A run's ID, outcome, and submissions after `start` or `resume` | [run lifecycle](../proposals/m7-harness-api.md#run-lifecycle) |
| `RunOutcome` | Accepted, rejected, incomplete, unknown, unsupported, or infrastructure failure | [status](cli.md#status) |
| `RunStatus` | What the ledger records about a run, read without running anything | [status](cli.md#status) |
| `Submission` | One submission's verdicts and decision | [status](cli.md#status) |
| `Balance` | A budget unit's limit, spent, and reserved amounts | [scopes](evidence-ledger.md#scopes) |

## Tasks and revisions

| Name | What it is | Reference |
| --- | --- | --- |
| `TaskSpec` | What to solve: objective, worker-visible and private files, checks, budgets, guard, memory, scheduled revisions | [task and run files](../proposals/m7-harness-api.md#task-and-run-files) |
| `Revision` | An owner-approved change to a task's contract | [contract revisions](cli.md#contract-revisions) |
| `ScheduledRevision` | A revision applied after a given number of submissions | [contract revisions](cli.md#contract-revisions) |
| `DuplicateGuard` | Refuses exact or near repeats of rejected candidates before checking | [duplicate guard](../proposals/m7-harness-api.md#budgets-and-the-duplicate-guard) |
| `default_normalize` | The guard's default normalisation: captured files in name order, length-prefixed | [duplicate guard](../proposals/m7-harness-api.md#budgets-and-the-duplicate-guard) |
| `FEEDBACK_LIMIT` | Maximum bytes of checker feedback shown to a worker; more is a checker fault | [checker interface](../proposals/m7-harness-api.md#checker-interface) |

## Domains and checkers

| Name | What it is | Reference |
| --- | --- | --- |
| `Domain` | Protocol: a name, version, pinned worker image, checkers, and optional sources and operations | [domain interface](../proposals/m7-harness-api.md#domain-interface) |
| `domain_identity` | The identity a project pins for a domain: name, version, image, Warranted build, source digest | [projects](cli.md#projects) |
| `Checker` | Protocol: trusted host code that assesses captured candidate bytes | [checker interface](../proposals/m7-harness-api.md#checker-interface) |
| `CheckContext` | What a checker reads (candidate, inputs, private files) and does (seeds, jobs) | [checker jobs](worker-and-containment.md#checker-jobs) |
| `Verdict` | A checker's status, worker feedback, host-only data, and facts | [checker interface](../proposals/m7-harness-api.md#checker-interface) |
| `VerdictStatus` | Passed, rejected, unsupported, or infrastructure failure | [checker interface](../proposals/m7-harness-api.md#checker-interface) |
| `DEFAULT_WORKER_IMAGE` | The pinned Python image used when a domain supplies no image of its own | [container sandbox](worker-and-containment.md#container-sandbox) |

## Jobs

| Name | What it is | Reference |
| --- | --- | --- |
| `JobContext` | Host-recorded entropy (`draw_seed`) and contained jobs (`run_job`) | [checker jobs](worker-and-containment.md#checker-jobs) |
| `JobRunner` | Protocol for running a job; bound into the project identity | [checker jobs](worker-and-containment.md#checker-jobs) |
| `PodmanJobs` | The job runner: each job in a fresh rootless container without network | [checker jobs](worker-and-containment.md#checker-jobs) |
| `JobLimits` | Memory, processes, scratch space, and CPUs for one job | [checker jobs](worker-and-containment.md#checker-jobs) |
| `JobResult` | A job's exit status, captured output, and timeout and truncation flags | [checker jobs](worker-and-containment.md#checker-jobs) |

## Host-mediated operations

| Name | What it is | Reference |
| --- | --- | --- |
| `Operation` | Protocol: trusted domain code the host runs at a worker's request | [host-mediated operations](worker-and-containment.md#host-mediated-operations) |
| `OperationContext` | What an operation reads (declared task files) and does (seeds, jobs) | [host-mediated operations](worker-and-containment.md#host-mediated-operations) |
| `OperationResult` | An operation's outcome, usage, raw evidence, and what the worker is shown | [host-mediated operations](worker-and-containment.md#host-mediated-operations) |
| `Outcome` | A completed operation's outcome: succeeded, failed, or infrastructure failure | [results and usage](evidence-ledger.md#results-and-usage) |

## Memory

| Name | What it is | Reference |
| --- | --- | --- |
| `MemorySpec` | A task's memory scope and the files whose bytes its entries depend on | [scoped memory](../proposals/m7-scoped-memory.md) |
| `MemoryEntry` | Facts from one check or notes from one submission, with tier and applicability | [memory](cli.md#memory) |
| `Applicability` | Whether an entry's dependencies are current, stale, or unknown | [scoped memory](../proposals/m7-scoped-memory.md) |
| `FACTS_COUNT` | Maximum facts one verdict may record | [scoped memory](../proposals/m7-scoped-memory.md) |
| `FACTS_LIMIT` | Maximum bytes of facts one verdict may record | [scoped memory](../proposals/m7-scoped-memory.md) |
| `NOTES_COUNT` | Maximum notes one submission may record | [scoped memory](../proposals/m7-scoped-memory.md) |
| `NOTES_LIMIT` | Maximum bytes of a submission's notes file | [scoped memory](../proposals/m7-scoped-memory.md) |
| `MEMORY_LIMIT` | Maximum bytes of the memory file a worker is shown | [scoped memory](../proposals/m7-scoped-memory.md) |

## Campaigns

| Name | What it is | Reference |
| --- | --- | --- |
| `CampaignSpec` | Tasks times run configurations times repetitions, run serially | [campaigns](cli.md#campaigns) |
| `CampaignTask` | A task in a campaign and the split it belongs to | [campaigns](cli.md#campaigns) |
| `CampaignRun` | One planned run and, once it exists, its status | [campaigns](cli.md#campaigns) |
| `CampaignReport` | A campaign's planned runs and their statuses | [campaigns](cli.md#campaigns) |
| `SPLITS` | The task splits: training, development, and held-out | [campaigns](cli.md#campaigns) |

## Model adapters

| Name | What it is | Reference |
| --- | --- | --- |
| `LocalChatCompletions` | Adapter for a local OpenAI-compatible endpoint, such as Ollama | [local endpoint](model-adapters.md#local-openai-compatible-endpoint) |
| `OpenRouterChatCompletions` | Adapter for OpenRouter with a pinned runtime | [OpenRouter](model-adapters.md#openrouter) |
