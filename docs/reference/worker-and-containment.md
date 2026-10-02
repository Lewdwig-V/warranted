# Worker and containment

Warranted runs a worker through mini-swe-agent's existing agent loop inside a
one-node LangGraph lifecycle, with each external attempt journaled in the
[evidence ledger](evidence-ledger.md) before its result is parsed. Generated shell
commands run in a rootless Podman container that has no network, no credentials,
and no host mounts. When the worker submits, the host stops all worker processes,
captures selected regular files as immutable evidence, and passes only those
captured bytes to the independent checker. This page covers `warranted.worker`,
`warranted.sandbox`, and `warranted.containers`. Model boundaries are described in
[model adapters](model-adapters.md), and what each episode may see is described in
[contexts](contexts.md).

These integrations are provisional. They are exercised by two task families
([CSV transformation](../fixtures/csv-transformation.md) and
[configuration migration](../fixtures/config-migration.md)), and the container
assumes a trusted kernel, runtime, and host.

## Ownership

| Component | Supplies | Trust |
| --- | --- | --- |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) 2.4.6 | `DefaultAgent` loop; Warranted adds `WorkerModel` and `WorkerEnvironment` adapters for its Model and Environment protocols | Trusted host code |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) 1.2.11 + langgraph-checkpoint-sqlite 3.1.1 | Outer lifecycle and native SQLite checkpointer (synchronous writes, separate database) | Trusted host code; checkpoint grants no authority |
| Warranted | Dispatch permission, immutable raw evidence, cumulative budgets, revision authority, private checker, acceptance | Authoritative |
| Generated shell commands, candidate files, worker prose | — | Untrusted |

The worker sees an objective, the host-selected files, shell results, and its own
conversation. It does not use ledger forms or tools. Graph state holds only
identifiers. Ledger connections are opened inside the node that uses them, because
LangGraph may run nodes on another thread. A graph cursor, a model submission, or
a receipt written by the worker cannot grant acceptance. See
[boundaries and ownership](../design.md#boundaries-and-ownership).

## Running an episode

```python
from warranted.sandbox import SANDBOX_ID, Sandbox
from warranted.worker import Episode, run_workflow, submitted_candidate

episode = Episode(
    "initial",
    "Solve task.md and submit result.json.",
    ("input.csv",),
    environment=SANDBOX_ID,
    model_service=service_id,
)
with Sandbox(ledger_root, episode) as shell:
    result = run_workflow(
        ledger_root,
        checkpoint_path,
        episode,
        model=model_boundary,
        environment=shell,
        reconcile=None,
    )
```

`Episode` is a frozen, validated specification:

| Field | Meaning |
| --- | --- |
| `episode_id` | `[a-zA-Z0-9_-]{1,80}`; operation IDs are `episode/<id>/model/<n>` and `episode/<id>/tool/<n>` |
| `objective` | Task text given to the agent |
| `inputs` | Names of project snapshots to copy in |
| `files` | Worker filename → captured `Evidence`; see [contexts](contexts.md) |
| `workspace` | Captured `workspace.json` from an earlier episode, restored as writable |
| `model`, `model_service`, `environment` | Boundary identities. `model_service` becomes the producer of each model request, so the request digest binds the service identity |
| `max_steps` | mini step limit, 1–100 (default 4) |
| `model_reservation`, `tool_reservation` | Units reserved for each attempt (default 1) |
| `container_timeout_seconds` | 30–3600 (default 120) |
| `continues` | A different, already recorded episode that this one continues |
| `scope` | [Ledger scope](evidence-ledger.md#scopes) for the episode's model and tool attempts (default `ROOT_SCOPE`) |

`run_workflow` refuses a checkpoint database inside the ledger root. It runs
`reconcile` (when one is supplied) for each unknown operation in the episode's
scope or the root scope, never another run's, refuses to proceed
while an unknown operation blocks the episode's scope (its own or the root
scope's), and returns mini's result only when the
graph's receipt matches an intact host receipt `episode/<id>/finished`.

Worker protocol:

- Each model response must be exactly `{"command": "<nonempty string>"}`. Any
  other response is recorded and then returned to mini as a `FormatError`.
- A command submits when it exits 0 and its first stdout line is
  `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`. The candidate is `/work/result.json`.
- `submitted_candidate(ledger, episode_id)` returns the captured `result.json`, and
  `submitted_files()` returns every `candidate/*` channel (including
  `workspace.json`) from the single completed submission. Both resolve recorded
  evidence and never read a host path supplied by the worker.

## Dispatch and recovery

Before dispatch, `Journal` records the episode's identity and inputs, including
package versions and the `serial-v1` policy. Each attempt has a stable slot bound
to its full request. Its request bytes are recorded first, then it is reserved,
then a dispatch marker is written. Raw stdout, stderr, provider responses, and
known usage are recorded before observations are formatted or mini raises its
submission exception. A parse failure still consumes resources. A restarted node
cannot mint a new ID to bypass an uncertain predecessor, and a changed request in
an existing slot fails with `OperationConflict`.

| Durable state | Recovery action |
| --- | --- |
| Ledger has completion; graph is behind | Reuse the exact completion without execution or another charge |
| Graph claims progress without matching ledger evidence | Block the claim |
| Reservation exists; dispatch has not begun | Dispatch that same operation once |
| Dispatch may have happened | Obtain an exact receipt or remain unknown and reserved |
| All dispatches resolved; conversation cannot be restored | Record a bounded fresh continuation under the same cumulative budget |
| Contract or premise changed | Reassess applicability and check the exact current candidate |
| Graph database lost | Recover from recorded host facts or block; never infer a successful effect |

On resume, the adapter rebuilds the same conversation from exact cached calls and
does not re-execute any of them. Unknown work blocks reconstruction, fresh
continuation, and new episodes in the same scope; `Ledger.begin` enforces this
even for callers that skip the preflight. The block persists through repeated host deaths
and fresh sessions. `Episode.continues` records the predecessor's exact episode
evidence. The host chooses that continuation under its fixed policy, and the
continuation starts a fresh conversation without changing allowances or erasing
old attempts.

The graph and the ledger commit in separate transactions. This design gives
operation deduplication and explicit uncertainty. It does not provide exactly-once
execution of arbitrary external effects. See
[recovery and accounting](../design.md#recovery-and-accounting).

## Container sandbox

`Sandbox(ledger_root, episode)` is the trusted `environment` boundary, and the
episode must pin `environment=SANDBOX_ID`. The sandbox requires local rootless
Podman, seccomp, and cgroup v2 with cpu, memory, and pids controllers. If any of
these is unavailable, no command runs. There is no local-shell fallback. The sandbox runs one container per episode, and the host
constructs every runtime argument without forwarding its environment.

| Setting | Value |
| --- | --- |
| Image | `warranted.sandbox.IMAGE`, a Python image pinned by digest; `--pull=never` |
| Network | `--network=none`; Podman proxy forwarding disabled |
| Namespaces | Private pid, ipc, uts, cgroup |
| Filesystem | Read-only root; `/work` and `/tmp` are 8 MiB `noexec,nosuid,nodev` tmpfs; no host directory or runtime socket mounted |
| Resources | 128 MiB memory (no extra swap), 1 CPU, 32 processes |
| Lifetime | `container_timeout_seconds` (120 s default; live-model episodes compute a longer value, see [contexts](contexts.md#treatment-runner)) |
| Each Podman call | 20 s deadline (`PODMAN_COMMAND_TIMEOUT_SECONDS`), 2 MiB combined stdout+stderr |
| Capture transport | 4 MiB for the encoded candidate and workspace |

**Privilege split.** The worker runs as UID 1000 with no effective capabilities
and `no-new-privileges`. A trusted supervisor inside the container keeps only
`CAP_KILL`, `CAP_SETUID`, and `CAP_SETGID`. It uses them to start each command
with restricted credentials and to write the exit status into a root-only
directory. Neither the worker's output nor Podman's reserved exit codes can
impersonate that status. As a result, shell failures (`failed`, workspace kept for
correction) are distinguished from runtime failures (`infrastructure_failure`).

**Inputs.** The host copies the selected snapshot bytes and evidence files into
`/work` as read-only files, together with a `context.json` inventory
(`{"authoritative": false, "files": [...]}`). The inventory contains no host
provenance or project metadata. Filenames must be safe basenames other than
`context.json`, `result.json`, and `workspace`, and the total input is limited to
1 MiB. The worker receives no ledger, checkpoint database, private reference, or
future fixture version. Publishing a fixture as development evidence does not make
it worker context.

**Capture on submission.** The supervisor kills all worker descendants and waits
until none remain alive. It then:

1. Opens `result.json` without following links. It accepts only a regular file
   with one link and at most 1 MiB.
2. Captures `/work/workspace/` as `workspace.json`. The capture holds at most 128
   regular files, 16 path components, and 1 MiB of content. Directory links, file
   links, special files, and hard-linked files fail the capture.
3. Removes the container. The bytes are recorded in the tool receipt only after
   removal.

The checker must use that captured artifact, never a mutable workspace path.
Runtime archive copies and capture scripts written by the worker grant no evidence.
On restore, `decode_workspace` checks relative paths again (no absolute paths,
`..`, NUL, or file/directory conflicts). Files are written inside a fresh container
as UID 1000. Empty directories and file modes are not kept.

**Failures.** A runtime timeout or output-limit breach terminates the container.
If cleanup succeeds, the receipt records `infrastructure_failure` with the captured
stream prefixes. If cleanup cannot be established, the attempt remains unknown and
reserved. A failed capture after a marked submission is recorded as a failed
command and leaves the container available. A missing workspace is never recreated
partway through an episode. Only a fresh episode authorized by the host can start
new work after resolved attempts.

**Unfinished episodes.** The submission-control runner in
`examples/m5/diagnostics.py` can capture an unsubmitted container before cleanup.
It uses the same process-stop and file checks and records the bytes as
`unfinished/*`, kept separate from `candidate/*`. Such a capture never changes
submission status. See
[submission controls](../experiments/2026-09-24-submission-controls.md).

## Checker jobs

`warranted.jobs.PodmanJobs().run(image, argv, files, stdin=b"", timeout_seconds=10)`
runs one program for a checker in a fresh container and returns a `JobResult`
(`returncode`, `stdout`, `stderr`, `timed_out`, `truncated`). The prototype task
layer exposes it to checkers as `CheckContext.run_job`.

- The image must be pinned by `sha256` digest. File names must be safe basenames;
  argv must be a nonempty list of strings; the timeout is 1 to 300 seconds.
- The container has no network, no host mounts, a read-only root, a private 8 MiB
  `/work` holding only the given files, and runs as UID 1000 with no capabilities,
  `no-new-privileges`, and the same process, memory, and CPU limits as the worker
  sandbox. Output is capped at 256 KiB per stream.
- A host-side runtime error raises `SandboxFailure`; a program that fails, times
  out, or overruns its output is a normal result.
- The job's program runs as the same user as the in-container runner, so it can
  shape its own result record. Treat job output as that program's claim about
  itself, never as host evidence about anything else.

## Native tests

```bash
MSWEA_SILENT_STARTUP=1 uv run --locked python -c 'from warranted.sandbox import IMAGE; print(IMAGE)'
# Pull the printed image once with podman pull. Tests never pull images.
WARRANTED_CONTAINER_TESTS=1 uv run --locked pytest -q -m container tests
```

Ordinary test runs skip native cases unless they are explicitly enabled. The CI
`containment` job installs Podman, allows unprivileged user namespaces on the
Ubuntu runner, pulls the image, and requires the `container` tests to run and pass.
It uses no model credentials. Fast tests use scripted model responses and the
local fake service while exercising the real pinned frameworks. External requests
are counted outside both the ledger and the checkpoint database.

`examples/m3/demo.py` runs the changed-offset CSV task through this path and a
forced restart. The scenario, budgets, and commands are in
[CSV transformation](../fixtures/csv-transformation.md). The migration worker and
restart are in [configuration migration](../fixtures/config-migration.md).

## Demonstrated crash boundaries

| Boundary | Required observation | Test source |
| --- | --- | --- |
| Reserved, before dispatch | Same pending slot executes once | `test_worker.py` |
| Dispatch marker, before observed effect | Unknown and reserved, no retry | `test_worker.py` |
| External response lost | Exact receipt settles once or remains unknown | `test_attempts.py` |
| Repeated death during reconciliation | One POST, retained reservation until settled | `test_attempts.py` |
| Shell submission, before capture | Unknown, no candidate acceptance | `test_sandbox.py` |
| Candidate captured, before ledger commit | Unknown, no duplicate shell dispatch | `test_sandbox.py` |
| Candidate receipt committed | Reuse the exact bytes after native container removal | `test_sandbox.py` |
| Episode receipt before graph checkpoint | Reuse completed attempts or the episode receipt | `test_worker.py` |
| Graph checkpoint committed or deleted | No additional execution or charge | `test_worker.py` |
| Graph claims a missing host receipt | Block rather than infer success | `test_worker.py` |
| Approved revision followed by host kill | Old checks become stale; new candidate passes current gates | `test_m3_demo.py` |
| Acceptance interrupted before/after commit | Remain unknown or reuse its committed receipt | `test_acceptance.py` |

The last row uses the existing acceptance tests because the demonstration calls
the same boundary (see [claims and acceptance](claims-and-acceptance.md)). Other
tests cover escape attempts, host-state and credential reads, background writers,
links and special files, invalid UTF-8, corrupt parent evidence, checkpoints from
another project, changed episode identity, resource limits, redirects, billed
failures, and budget breaches. Deleting or rewinding a checkpoint does not reset
budgets or create extra effects.

## Limits

- Container isolation assumes a trusted kernel, runtime, controller, and host
  filesystem. It does not resist kernel exploits or hostile host administrators.
- Operation deduplication does not give exactly-once arbitrary external effects.
- [LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
  can execute downstream calls again, so it is not Warranted replay. Replay must
  reveal only recorded outcomes and cannot call live tools; it is not provided. See
  [Dream-RSI](../design.md#dream-rsi-exploration-and-replay).
- Each check establishes only its stated scope. An empty output, a forged receipt,
  or a passing narrow check cannot waive other gates. Acceptance resolves the
  current inputs and obligations again at the transition.
- Reports distinguish synthetic units from measured elapsed time and real provider
  costs. They record package, model, policy, evaluator, environment, and task
  versions.
