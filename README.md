# Warranted

**Durable, checkable knowledge for long-horizon agent work.**

Warranted is a neurosymbolic harness for agents doing long, interdependent work.
A language-model worker explores freely in a contained shell. The host keeps an
authoritative evidence ledger, tracks what each conclusion depends on, and
accepts work only when the independent checks its contract requires pass:
deterministic checkers, plus Lean proofs where the contract asks for them.
The working principle is: **make the world legible, give the model room to
explore, and be precise about what counts as established knowledge.**

Warranted is moving from a research prototype to a usable harness. Today it is
a Python library with example drivers for two task fixtures. Its APIs are not yet
stable, and the `warranted` command prints help and its version only.

## What exists

Everything below runs locally with one trusted host process writing the ledger.
Scope limits are stated in each linked document.

| Component | What it does | Import | Details |
| --- | --- | --- | --- |
| Task layer | Projects, TOML tasks, runs with resume, domain checkers, the duplicate guard, and scoped memory | `warranted` | [M7 proposal](docs/proposals/m7-harness-api.md) |
| Evidence ledger | Immutable observations, SHA-256 artifact files, operation receipts, reservations, and usage in SQLite; survives process termination | `warranted.host` | [Evidence ledger](docs/reference/evidence-ledger.md) |
| Exports | Explicitly selected, non-authoritative copies for inspection | `warranted.host` | [Exports](docs/reference/evidence-ledger.md#permitted-exports) |
| Claims and support | Assertions with assumption versions, historical checks, and conservative staleness through declared dependencies | `warranted.host` | [Claims](docs/reference/claims-and-acceptance.md#claims) |
| Acceptance boundary | Gates checked against exact current versions; scoped rule exceptions; owner-approved contract revisions | `warranted.host` | [Acceptance](docs/reference/claims-and-acceptance.md#acceptance) |
| Worker integration | mini-swe-agent 2.4.6 inside a serial LangGraph 1.2.11 lifecycle with its SQLite checkpointer | `warranted.host` | [Worker](docs/reference/worker-and-containment.md) |
| Containment | One rootless Podman container per episode, with no host mounts or network; candidate capture after worker processes stop | `warranted.host` | [Containment](docs/reference/worker-and-containment.md#container-sandbox) |
| Checker jobs | One-shot contained jobs that let a checker run untrusted code, such as a candidate program | `warranted` | [Checker jobs](docs/reference/worker-and-containment.md#checker-jobs) |
| Host-mediated operations | Worker requests the host executes, reserves, records, and reuses, with results shown apart from command output | `warranted` | [Host-mediated operations](docs/reference/worker-and-containment.md#host-mediated-operations) |
| External attempts | Single-attempt adapters for a loopback fake service, a local OpenAI-compatible server (Ollama), and OpenRouter; lost responses stay blocked | `warranted`, `warranted.host` | [Model adapters](docs/reference/model-adapters.md) |
| Lean verification | Pinned Lean, Comparator, and Landrun check an exact target and axiom policy; durable proof receipts | `warranted.host` | [Proof verification](docs/reference/proof-verification.md) |
| A–E contexts | Host-selected worker context for the five knowledge-workflow conditions | `warranted.host` | [Contexts](docs/reference/contexts.md) |

The fixtures live under [`examples/`](examples): a CSV transformation with a
revised timestamp interpretation (M1–M4) and a configuration-file repository
migration with an approved requirement change (M5).

## What has been demonstrated

All demonstrations use fixed or scripted model responses unless stated otherwise.
They establish integration and recovery behavior on these fixtures, not model
quality or general task performance.

- **Restart and accounting.** A scripted CSV run recovers after process
  termination without repeating completed work or double-charging usage, and
  operations with unknown outcomes keep their reservations
  ([M1 walkthrough](#run-the-m1-walkthrough)).
- **Changed premises and independent obligations.** Revised inputs and
  definitions block stale reuse and rebuild only affected work; narrow checks
  cannot override a failed behavioral obligation
  ([M2 experiments](#run-the-m2-fixture-experiments)).
- **Contained worker across a host kill.** A scripted model completes the
  changed-premise CSV task in a rootless container across a forced restart, with
  unchanged independent gates ([M3 demonstration](docs/fixtures/csv-transformation.md#m3-changed-premise-demonstration)).
- **Proof support without proof overreach.** One Lean uniqueness theorem applies
  to correct, record-dropping, and empty candidates; only the correct one passes
  the task gates ([M4 fixture](docs/fixtures/csv-transformation.md#m4-uniqueness-application)). Migration and timestamp proof
  cases repeat this pattern ([M5 proofs](docs/fixtures/config-migration.md#proof-cases)).
- **Second task family.** A fixed worker delivers the approved migration
  revision across a host kill and a repeated resume ([M5 fixture](docs/fixtures/config-migration.md)).
  Scripted tests run all ten family × condition combinations; native tests take
  condition E on both families through a kill and two fresh resumes
  ([contexts](docs/reference/contexts.md#run-and-verify)).

## What live-model runs have shown

Live inference is opt-in, local or capped, and outside CI. All runs so far are
development diagnostics on the migration task under condition A.

- Early local Qwen runs and five OpenRouter GLM attempts never submitted a
  candidate. The GLM attempts ended in configuration, provider, or token-limit
  failures before useful task work
  ([contexts](docs/experiments/2026-09-23-qwen-development-runs.md),
  [OpenRouter](docs/experiments/2026-09-24-openrouter-glm-diagnostic.md)).
- The submission instructions omitted the required payload structure. With
  explicit packaging instructions, a separate submission helper, or both, most
  Qwen runs submitted accepted payloads. Across both twelve-run comparisons, only
  one of fifteen accepted submissions also rejected a boolean `version`, a case
  the private checks omit
  ([submission controls](docs/experiments/2026-09-24-submission-controls.md),
  [corrected controls](docs/experiments/2026-09-25-corrected-submission-controls.md)).

No run has yet completed the approved revision and restart sequence with a live
model, and no A–E comparison has been run.

## Not yet built

The next work is a stable public API and CLI, so that
[ReSchema](https://github.com/Lewdwig-V/reschema) can use Warranted as its harness
and keep only its reverse-engineering logic
([M7 and M8](docs/roadmap.md#m7--stable-harness-api-and-cli)). Until then:

- The public API is not yet stable. `warranted` exports the task layer
  (projects, tasks, runs, domains, checkers, operations, and memory) from the
  [M7 proposal](docs/proposals/m7-harness-api.md), and `warranted.host` exports
  the low-level kit. Modules whose names start with an underscore are private.
  Until 1.0, either surface may change; changes will be listed in the changelog.
- There is no campaign interface, and contract revisions are not yet part of the
  task layer.
- The measured A–E comparison, with frontier and smaller models, separated
  development and held-out tasks, and full cost reporting.
- Replay of recorded histories and the planned
  [Dream-RSI](https://arxiv.org/html/2609.14858v1) scheduling experiment.
- An optional Jev classifier/judge, Hindsight retrieval, and AutoSaddler harness
  optimisation.
- Remote owner authentication, external side effects beyond local copies,
  multiple writers, and a user-facing CLI.

The research items above are deferred until ReSchema runs on Warranted.

See the [roadmap](docs/roadmap.md) for status and open decisions, and the
[design](docs/design.md) for the invariants these components enforce.

## Inspirations and foundations

Warranted builds on other people's research and engineering:

- **[Schema](https://schema-harness.github.io/) and
  [PRO-LONG](https://arxiv.org/html/2607.20064v2)** motivate executable world models
  and complete, programmatically accessible histories, respectively. They inform
  conditions B and C; Warranted does not replicate either system.
- **[Vercel's tool-reduction case study](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools)
  and [mini-swe-agent](https://mini-swe-agent.com/latest/)** inform the small,
  shell-and-files worker interface. mini-swe-agent is the integrated worker loop.
- **[LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)** runs the
  worker lifecycle and checkpoints; the ledger remains authoritative.
- **[Lean](https://lean-lang.org/doc/reference/latest/)**, with the pinned
  Comparator and Landrun tools, checks the formal proof cases.
- **[Dream-RSI](https://arxiv.org/html/2609.14858v1)** supplies the method for the
  planned, not yet started, scheduling experiment.

The [design's sources and provenance](docs/design.md#sources-and-provenance) also
explain the project's origins and later candidates, Hindsight and AutoSaddler.

## Get started

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/Lewdwig-V/warranted.git
cd warranted
uv sync --locked
uv run --locked pytest -q tests
```

The package requires Python 3.12 or newer. uv manages the environment through
`pyproject.toml` and the committed `uv.lock`.

## Development

```bash
uv run --locked pytest -q tests
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --no-sources
```

The default test run needs no model credentials, network services, or containers.
Tests marked `container` need rootless Podman and the pinned local image; tests
marked `proof` also need the pinned Lean verifier bundle. CI runs the default
suite, both native groups in separate jobs, the M1 and M2 scripts across two
processes, and installation of the built wheel. Use `uv add` for new dependencies
and commit the resulting lockfile.

## Run the M1 walkthrough

From the repository root, run these commands with a new destination:

```bash
uv run --locked python examples/m1/walkthrough.py start runs/m1
uv run --locked python examples/m1/walkthrough.py resume runs/m1
```

The first process converts the fixed CSV timestamps to UTC and sums values by UTC
date. The second process recovers the same result. Each report must show one
execution, two spent units, zero reserved units, three available units, and three
passing output checks. These work units are synthetic. Elapsed nanoseconds are
measured separately in the operation result.

The script retains authoritative state in `runs/m1/ledger`, candidate files in
`runs/m1/candidate`, and reports in `runs/m1/reports`. Each report names a separate
export under `runs/m1/exports`. Open its `index.json` to trace selected snapshots,
observations, and operation results to copied files in `artifacts/`.

Give inspection consumers only the export directory. Evaluator snapshot records,
host code, environment fields, and authoritative database files are excluded. Export edits
cannot change the ledger. The script uses trusted fixture code and does not
provide worker isolation. A changed fixture or environment fails before reuse.
Unknown execution keeps its reservation and is not retried.

CI runs both commands and retains the exports, reports, and execution counter in
the `m1-walkthrough` artifact for 14 days. The [CSV fixture](docs/fixtures/csv-transformation.md#m1-scripted-walkthrough)
defines the fixed outputs and the limits of this demonstration.

## Run the M2 fixture experiments

Use a new destination and run the two commands in separate processes:

```bash
uv run --locked python examples/m2/experiments.py start runs/m2
uv run --locked python examples/m2/experiments.py resume runs/m2
```

The fixture compares harmless and meaningful revisions, changes a definition
without changing the main output, and rejects five fixed failure candidates.
The correct candidate passes. Each report links to copied evidence and shows
charged operations, retained reservations, and an independent execution count.

The [CSV fixture](docs/fixtures/csv-transformation.md#m2-fixture-experiments) gives the expected results and
operation counts. CI runs all three experiments and retains the public development
evidence for 14 days. The host uses explicit dependencies and trusted revision
events. [Claims and current support](docs/reference/claims-and-acceptance.md#claims) separate historical checks
from current dependency versions and retain approved revision provenance.
The [acceptance boundary](docs/reference/claims-and-acceptance.md#acceptance) enforces all four gates and
records a separate source-format rule exception. Its protected operation records
local acceptance. Owner authentication and external-effect authorization remain
planned. This M2 script uses trusted code. M3 exercises a separately contained
worker against the same evaluator and acceptance rules.

## Local evidence API

This example reserves three synthetic units, reads a snapshot, and charges two
units once. These units demonstrate accounting, not measured money or tokens.
The host records elapsed time separately. The CLI still provides help and version
information only.

```python
import platform
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from warranted.host import (
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
)

manifest = Manifest(
    fixture_id="example",
    fixture_version="1",
    run_id="run-1",
    world_id="world-1",
    environment={"python": platform.python_version()},
    allowances={"synthetic-work": 5},
)
snapshots = {"input": Snapshot(b"example bytes\n", "example literal", "1")}

with TemporaryDirectory() as directory:
    root = Path(directory) / "evidence"
    with Ledger.create(root, manifest, snapshots) as ledger:
        session = ledger.start_session()
        source = ledger.project.snapshots["input"].artifact
        origin = Origin(
            operation_id="read-1",
            kind="file-read",
            producer="python",
            producer_version=platform.python_version(),
            inputs={"input": source},
        )
        request = Request(origin, ledger.project)
        ledger.reserve(session, request, {"synthetic-work": 3})
        if ledger.begin(session, request):
            started = perf_counter_ns()
            raw = ledger.read_artifact(source)
            result = Result(
                Outcome.SUCCEEDED,
                exit_code=0,
                usage={"synthetic-work": 2},
                elapsed_ns=perf_counter_ns() - started,
            )
            ledger.complete(session, request, result, {"raw": raw})
    with Ledger.open(root) as ledger:
        observation = ledger.lookup(request).completion.observation
        assert ledger.read_artifact(observation.artifacts["raw"]) == b"example bytes\n"
        assert not ledger.begin(ledger.start_session(), request)
        assert ledger.accounting()["synthetic-work"].spent == 2
```

Only a successful `begin` returning `True` permits the host to execute work.
If execution might have started, the operation stays unknown until the host
captures an attributable result. Reopening or repeating the request does not
release its reservation. A completion records the process outcome, not task
acceptance. Missing or corrupt artifact bytes fail explicitly.

`record` remains available for evidence without an operation receipt. It does not
complete work or settle usage. Host code must use the operation methods around
execution. This Python API does not isolate a worker or intercept shell commands.

The storage format is now version 3, which adds per-run
[scopes](docs/reference/evidence-ledger.md#scopes). Projects in earlier formats
fail explicitly on open and remain unchanged; there is no migration.

The [evidence ledger reference](docs/reference/evidence-ledger.md) describes the API and tests.
The tests establish recovery from process termination on a working local
filesystem. They do not establish recovery from power loss or disk loss.
Keep the authoritative project outside any untrusted worker's writable workspace.

## Project map

| Path | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Guidance for coding agents and contributors |
| [docs/design.md](docs/design.md) | Invariants, boundaries, and the reasoning behind them |
| [docs/roadmap.md](docs/roadmap.md) | Milestone status, remaining work, and open decisions |
| [docs/reference](docs/reference) | How each component works, its API, guarantees, and limits |
| [docs/fixtures](docs/fixtures) | The two task fixtures, how to run them, and what they establish |
| [docs/experiments](docs/experiments) | Evaluation design and dated records of measured runs |
| [src/warranted](src/warranted) | Library modules listed under [What exists](#what-exists) |
| [examples](examples) | M1–M5 fixtures, demonstration scripts, and diagnostic runners |
| [tests](tests) | Fast tests plus `container` and `proof` native groups |
| [scripts](scripts) | Builder for the pinned Lean verifier bundle |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | Fast, containment, and proof CI jobs |
