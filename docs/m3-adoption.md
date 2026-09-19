# M3: worker and workflow adoption

Approved implementation plan, 2026-09-19. The four pull requests below build on
the trusted local M2 boundary. Completion claims belong with runnable evidence;
this plan alone establishes no new guarantee.

## Ownership

[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) supplies the worker's
existing `DefaultAgent` loop. Two small host adapters implement its Model and
Environment protocols. The worker sees an objective, selected files, shell
results, and its own conversation. It does not operate ledger forms.

[LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) supplies the
outer lifecycle and its native SQLite checkpointer. Use synchronous checkpoint
writes and a separate database. Graph state contains identifiers and evidence
references. Open ledger connections inside the serialized node that uses them.

Warranted owns dispatch permission, immutable raw evidence, cumulative budgets,
revision authority, the private checker, and acceptance. A graph cursor, a model
submission, or worker-authored receipt cannot grant acceptance. The frameworks'
controllers and these adapters are trusted host code; generated shell commands
and candidate files are untrusted.

These integrations remain provisional until a second task family tests them.
Use the existing ledger, exports, claims, and acceptance APIs. Do not add a
checkpoint backend, agent loop, general workflow engine, or plugin registry.

## Four focused pull requests

1. **Adoption and recovery.** Pin tested framework versions, run a real mini
   episode inside a real LangGraph graph with scripted external boundaries,
   reuse completed operations, and block unknown outcomes. Kill the host after
   ledger completion but before a graph checkpoint; verify one actual execution
   using a witness outside both databases. This slice executes no untrusted host
   shell and does not yet establish worker containment.
2. **Worker containment.** Add a rootless container environment with no network,
   credentials, ledger, private references, checkpoint database, runtime socket,
   or broad host mount. Limit processes, memory, CPU, time, and output. Stop all
   worker processes before capturing selected regular files as immutable evidence.
   Test escape attempts, descendants, symlinks, and capture after interruption.
   Unavailable isolation blocks execution; there is no local-shell fallback.
3. **External attempts.** Reserve before every provider or tool attempt and charge
   failures and malformed responses. Use a fake service with an independent
   request witness and exact operation receipts. Reconcile lost responses only
   when identity, bytes, and usage are attributable; otherwise retain the
   reservation and block. Provider retries must be absent or individually
   recorded. Live provider access remains optional and separately budgeted.
4. **M3 demonstration.** Run the changed-offset data transformation across a
   forced restart with a fixed serial policy. Preserve old failed obligations,
   reject stale checks, retain costs, and obtain independent current acceptance.
   Complete the crash matrix, add credential-free CI evidence, and mark only
   demonstrated roadmap criteria complete.

The pull requests form a stack and should merge in that order. Each adds focused
negative cases and runs the repository checks. No paid model run is required.

The first slice pins mini-swe-agent 2.4.6, LangGraph 1.2.11, and
langgraph-checkpoint-sqlite 3.1.1. `tests/test_worker.py` kills a host process after
tool completion and before the graph checkpoint. It also covers unknown dispatch,
checkpoint loss, changed episode identity, and a checkpoint from another project.
The adapter reconstructs the same conversation from exact cached calls before
continuing an incomplete episode. This reconstruction permits no new execution
for a cached call. A changed request in an existing attempt slot fails explicitly.

## Dispatch and recovery

Record an episode's identity and inputs before dispatch. Give each external
attempt a stable slot in that episode, bound to the full request. A restarted
node cannot mint a replacement ID to bypass an uncertain predecessor. Save raw
stdout, stderr, provider responses, and known usage before observation formatting
or mini's submission exception. A parsing failure still consumes resources.

| Durable state | Recovery action |
| --- | --- |
| Ledger has completion; graph is behind | Reuse the exact completion without execution or another charge |
| Graph claims progress without matching ledger evidence | Block the claim |
| Reservation exists; dispatch has not begun | Dispatch that same operation once |
| Dispatch may have happened | Obtain an exact receipt or remain unknown and reserved |
| All dispatches resolved; conversation cannot be restored | Record a bounded fresh continuation under the same cumulative budget |
| Contract or premise changed | Reassess applicability and check the exact current candidate |
| Graph database lost | Recover from recorded host facts or block; never infer a successful effect |

The graph and ledger have separate transactions. This is operation deduplication
and explicit uncertainty, not a promise of exactly-once arbitrary external effects.
Check every dispatching node against the host journal before external work.
An unknown predecessor remains blocking through repeated deaths and fresh sessions.

## Containment and acceptance

Start with native rootless container facilities. Export only approved task inputs
and public requirements. Future fixture versions and private reference answers are
not worker context merely because M2 published them as development evidence.
Run the existing controller on the host and generated commands in the container.
The host constructs all runtime arguments and never forwards its environment.

Submission ends the worker episode. Quiesce the container, capture selected files
without following links, then run the independent checker on the captured bytes.
Acceptance resolves current inputs and obligations again at the transition.
An empty output, forged receipt, or passing narrow check cannot waive other gates.

The containment slice uses local rootless Podman with cgroup v2 and seccomp.
`Sandbox` pins a Python image by digest and gives each episode an 8 MiB workspace,
128 MiB memory, one CPU, 32 processes, and a 120-second container lifetime.
Each command has a 20-second deadline and at most 2 MiB of combined output.
The root filesystem is read-only. No host directory or runtime socket is mounted.
`export_evidence` supplies an explicit selection of public input snapshots.

The worker runs as UID 1000 with no effective capabilities. A trusted supervisor
inside the container retains only `CAP_KILL`. On submission, it kills worker
descendants and waits until none remain live. It then opens `result.json` without
following links and accepts only a regular file with one link and at most 1 MiB.
The host removes the container before recording those bytes in the tool receipt.
The checker must use that captured artifact, never a mutable workspace path.
Neither runtime archive copies nor worker-authored capture scripts grant evidence.

A runtime timeout or output limit terminates the container. If cleanup succeeds,
the receipt records infrastructure failure and the captured stream prefixes.
If cleanup cannot be established, the attempt remains unknown and reserved.
A missing workspace cannot be silently recreated halfway through an episode.
Only a host-authorized fresh episode can start new work after resolved attempts.

To run the native tests after installing rootless Podman:

```bash
MSWEA_SILENT_STARTUP=1 uv run --locked python -c 'from warranted.sandbox import IMAGE; print(IMAGE)'
# Pull the printed image once with podman pull. Tests never pull images.
WARRANTED_CONTAINER_TESTS=1 uv run --locked pytest -q -m container tests
```

The separate CI containment job installs the runtime and pulls that image before
testing. Ubuntu's temporary runner permits unprivileged user namespaces for this
job. Ordinary tests skip native cases unless explicitly enabled. The native job
requires them to execute successfully and uses no model credentials.

## Evidence and limits

Fast tests use scripted model responses and a local fake service, while exercising
the actual pinned frameworks. Count external requests outside the ledger and
checkpoint database. Separately mark native-container tests and require them in
CI. Report package, model, policy, evaluator, environment, and task versions;
distinguish synthetic units from measured elapsed time and real provider costs.

The crash matrix covers reservation, dispatch, external completion, ledger
completion, graph checkpointing, submission, candidate capture, and acceptance.
Test both an attributable receipt and a service that cannot establish an outcome.
Checkpoint deletion or rewind must not reset budgets or create extra effects.

Native [LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
can execute downstream calls again. It is not Warranted's planned M6 replay,
which may reveal only recorded outcomes and cannot call live tools. Container
isolation assumes a trusted kernel, runtime, controller, and host filesystem;
it does not establish resistance to kernel exploits or hostile host administrators.
