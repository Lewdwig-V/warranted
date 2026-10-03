# Command line

The `warranted` command runs tasks through the public task layer in `warranted`.
It imports nothing else, and a test enforces that. This page covers its
commands, files, output, and limits. `python -m warranted` runs the same command.

```bash
warranted init DIR --domain REF [--allow UNIT=N ...]
warranted run DIR TASK.toml --config RUN.toml [--json]
warranted resume DIR RUN --config RUN.toml [--json]
warranted status DIR [RUN] [--json]
warranted memory DIR TASK.toml [--json]
warranted export DIR RUN DEST
warranted import DIR FILE...
warranted revise DIR TASK_ID REVISION.toml
warranted campaign run DIR CAMPAIGN.toml [--json]
warranted campaign report DIR CAMPAIGN_ID [--json]
```

## Projects

`init` creates a project directory holding one ledger, and writes
`DIR/warranted.toml` naming the domain:

```toml
domain = "../domains/reschema.py:ReSchema"
```

`REF` is either `package.module:Object`, for an importable domain, or
`path/to/domain.py:Object`, which is loaded from the file. A file path is stored
relative to the project, so the project and the domain can move together. A
class is instantiated without arguments; any other object is used as it is.

The project pins the domain's identity when it is created: the domain's name and
version, its class and declared source files, its operations, and the Warranted
version and package digest. Every later command loads the domain again, and the
project refuses to open if that identity has changed. A changed domain needs a
new project.

`--allow UNIT=N` sets a project allowance, such as `model=200`, `tool=400`, or
`check=50`. Units that are not given are allowed zero, so a project without
allowances cannot run.

## Run configuration

```toml
model = "qwen3:8b"
provider = "ollama"          # the only provider the task layer supports so far
max_steps = 12

[adapter]
base_url = "http://127.0.0.1:11434/v1"   # the default
max_tokens = 3072
timeout_seconds = 180
seed = 0
model_digest = "<64 hex>"    # optional; needed before a model can be verified

[budgets]                    # per-run caps under the project allowances
model = 40
tool = 80
```

`run` builds a [`LocalChatCompletions`](model-adapters.md) adapter from this file
and starts a new run with a fresh ID. The run records the adapter's configuration
and service identity, and every model request in the run cites that record.

`resume` uses the run's recorded task, budgets, and step limit. From `--config`
it takes only the model connection, and it refuses a model whose name or adapter
configuration differs from the run's record.

`provider = "openrouter"` is refused for now. The OpenRouter adapter checks each
request against a pinned runtime that only the M5 harness records.

## Output and exit status

Without `--json`, commands print short human-readable lines. With `--json`, they
print one JSON object with `"version": 1`. Fields may be added within a version;
renaming or removing one increases the version.

| Status | `run` and `resume` |
| --- | --- |
| 0 | accepted |
| 1 | rejected, or incomplete |
| 2 | usage error: bad input, a changed domain or model, or an existing destination |
| 3 | unknown: an operation's outcome is unresolved and blocks the run |
| 4 | unsupported |
| 5 | infrastructure failure |

`campaign run` exits 3 if any planned run is unknown, otherwise 5 if any ended
in infrastructure failure, otherwise 0; each run's outcome is in its report.
`init`, `status`, `memory`, `export`, `import`, `revise`, and `campaign report`
exit 0 on success and 2 on a usage error.

## Status

`status DIR` lists every run with its task, outcome, and the number of
submissions used. `status DIR RUN` adds each submission's decision and verdicts,
the operations blocking the run, and the run scope's usage for each unit
(`spent`, `reserved`, and `limit`).

Status is read-only and needs no model. Its outcome is taken from recorded
decisions:

- `unknown`: an operation in the run's scope or the root scope is unresolved;
- the final decision of the last submission, when it ended the run;
- `rejected`: every allowed submission was used without acceptance;
- `incomplete` (or `rejected`, after earlier submissions): the next episode
  finished without submitting, for example at its step limit, or its model
  attempt failed. Resume would only replay that episode;
- `infrastructure_failure`: an attempt in the next episode recorded one;
- `open` (JSON `null`): otherwise. The run has submissions left and nothing
  recorded has ended it, so `resume` can continue it.

A run that a budget stopped before its next step could start still shows
`open`, because nothing records that stop; resuming it reports the budget again.

## Memory

`memory DIR TASK.toml` lists every entry in the task's
[memory scope](../proposals/m7-scoped-memory.md), assessed against that task. The
list includes withheld entries, with their tier, applicability, and whether a
new run of the task would show them. Listing memory records only derived
bookkeeping: entries, claims, and version records. It runs no checker.

## Export

`export DIR RUN DEST` writes a non-authoritative copy of the run's public records
with [`export_evidence`](evidence-ledger.md#permitted-exports). The copy includes
the task without its private files, the configuration, the memory snapshot, the
policy, captured candidates and notes, feedback, verdicts and facts, guard
decisions, and acceptance decisions with their receipts. It leaves out private
task files, host-only verdict data, drawn seeds, job logs and outputs, and model
and tool transcripts. `DEST` must not exist and must be outside the project's
ledger.

## Import

`import DIR FILE...` records each file's bytes once in the project and prints
its reference, `sha256:<hex>`. Importing the same bytes again changes nothing.
A task file can then name the bytes instead of a path, so a task does not depend
on a file that a domain build may later overwrite:

```toml
[inputs]
binary = { artifact = "sha256:<hex>" }
```

The CLI loads every task through the project, which resolves these references
and refuses one that was not imported. Domain builds, such as compiling a
corpus, stay in the domain project; Warranted only pins their outputs.

## Contract revisions

A task's contract is its files, worker-visible and private, its required checks,
and its objective. An owner-approved revision changes it:

```toml
id = "offset-v2"
owner = "data-owner"            # attribution from trusted local files
reason = "The source timestamps are UTC."
note = "Use offset-v2."         # optional; appended to the objective
remove = ["offset-v1"]          # optional; files the revision drops
checks = ["transformation"]     # optional; the new required checks

[inputs]                        # optional; files added or replaced
"offset-v2" = "offset-v2"

[private]                       # optional; private files added or replaced
```

File values work as in task files, including `{ artifact = REF }`. A revision
cannot change checker code or the domain; a changed domain is a new project.

**Scheduled revisions.** A task can schedule revisions at submission
checkpoints:

```toml
[[revisions]]
after_submission = 1
file = "revisions/offset-v2.toml"
```

Submissions after the checkpoint are checked and decided under the revised
contract. The next episode receives the revised files and an objective that
names the revision, its owner, and its reason. A submission accepted before the
checkpoint ends the run, so the revision never applies. Earlier verdicts and
decisions stay on record under the contract that produced them; each decision
binds the contract version it used. The duplicate guard compares a candidate
only with candidates rejected under the same contract, so resubmitting the same
bytes after a revision is checked again. The schedule is part of the task, so a
restarted run resumes on the same contract sequence. When a run is created, it records
one memory snapshot for each contract version, assessed against that version's
files, so facts that a revision makes stale are withheld after the checkpoint.
A task budget for checks requires every check of every scheduled contract to be
isolated.

**Project revisions.** `warranted revise DIR TASK_ID REVISION.toml` records a
revision of a task in the project. New runs of that task use the revised
contract, applied before any scheduled revisions. A run that started before the
revision is refused on resume, and a campaign pinned before it is refused as a
changed plan; start a new run or campaign. Recording the same revision again
changes nothing, and a different revision under a used ID is refused. Nothing a
worker writes can create or select a revision.

## Campaigns

A campaign runs tasks under run configurations, repeated, in one project:

```toml
id = "rot13-dev"
repetitions = 3

[configs]                     # name = run configuration file
qwen = "runs/qwen.toml"

[[tasks]]
path = "tasks/rot13-f1.toml"
split = "development"         # training, development, or held-out

[[tasks]]
path = "tasks/rot13-f2.toml"
split = "development"
```

Paths are relative to the campaign file. Each repetition runs every task under
every configuration, in the order given, one run at a time. Runs are created
only when the campaign reaches them, so a task's
[memory snapshot](../proposals/m7-scoped-memory.md) includes facts accepted
earlier in the same campaign.

The first `campaign run` pins the plan: it records every planned run with its
ID, task digest, split, configuration, model adapter, and repetition. A later
`campaign run` with the same campaign ID must describe the same plan, or it is
refused; a changed plan needs a new campaign ID. It then continues exactly the
planned runs: it starts any that do not exist yet with their planned IDs,
resumes open and unknown ones, and leaves finished ones alone. Resuming an
unknown run reconciles what it can and never sends an operation again. Before
it resumes or reports a run, it checks that the run was started by this
campaign for its planned task, configuration, split, and model adapter; a run
started some other way under a planned ID is refused.

Each run records its campaign and split, and a run's memory snapshot shows only
entries from runs of the same split. Runs outside any campaign form their own
group. A plan is also refused if a task's memory scope is shared by tasks of
another split, in this campaign or in any campaign already pinned in the
project. Held-out runs therefore never see training or development facts.
This separates memory, not task lineage: nothing yet refuses the same task in
different splits across campaigns ([enforcement](enforcement.md#7-splits-stay-separate-failures-and-costs-are-reported)).

`campaign report` lists every planned run with its split, task, configuration,
repetition, and outcome, including runs that failed or have not started, and
totals outcomes and spent units for each split.

## Limits

- `run` and `resume` start workers in rootless Podman containers using the
  domain's pinned worker image. The default test suite exercises the commands
  with a scripted environment and a loopback model server.
- Campaign runs are serial; there is no parallel scheduling.
- Runs are serial. One process at a time may write a project.
