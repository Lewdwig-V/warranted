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

`init`, `status`, `memory`, and `export` exit 0 on success and 2 on a usage error.

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

## Limits

- `run` and `resume` start workers in rootless Podman containers using the
  domain's pinned worker image. The default test suite exercises the commands
  with a scripted environment and a loopback model server.
- Campaigns and `import` are not implemented yet. `revise` waits until contract
  revisions are part of the task layer.
- Runs are serial. One process at a time may write a project.
