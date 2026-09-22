# Warranted

**Durable, checkable knowledge for long-horizon agent work.**

An agent should be able to preserve what it has learned, explain what supports
it, and revisit the right conclusions when the facts change. Warranted explores
that idea through executable artifacts, an evidence ledger, explicit
dependencies, and independently checked acceptance conditions.

The design principle is simple: **make the world legible, give the model room to
explore, and be precise about what counts as established knowledge.**

## Status

The local ledger stores immutable observations, raw artifact bytes, operation
requests, and resource usage. Its Python API recovers these records after process
interruption. It uses SQLite and files with one trusted writer.

Reservations survive restart. Repeated requests reuse completed results without
another charge, and uncertain executions remain blocked. A trusted host boundary
checks gates and scoped rule exceptions before recording candidate acceptance.
Replay remains planned. M1 is complete for
a local filesystem with one trusted writer. A scripted CSV walkthrough demonstrates restart, result
reuse, accounting, and selected evidence exports. The M2 fixture adds three
scripted experiments for selective rebuilding, changed definitions, and independent
acceptance obligations. Claims retain their assumptions and checker references.
Current support reports propagate staleness through declared dependencies.
The fixture checks contract revisions against pinned local owner approvals.
M2 is complete within this trusted local scope.

M3 now integrates mini-swe-agent 2.4.6 and LangGraph 1.2.11 with the SQLite
checkpointer 3.1.1. Scripted boundary tests exercise real framework recovery,
completed-result reuse, and unknown-outcome blocking. This integration executes
no live model requests. A rootless Podman adapter now contains generated shell
commands and captures a bounded candidate after stopping worker processes.
Its native tests run in a separate CI job. The main CLI still provides help and
version information only. The [M3 adoption plan](docs/m3-adoption.md) describes
the four implementation PRs and their evidence requirements.

A local fake HTTP service now tests lost model responses. Exact operation
receipts can settle known usage after restart. Missing receipts and unknown
usage remain blocked. Malformed responses and known failures retain their costs.
The adapter performs one request per attempt and disables redirects and retries.

The [M3 demonstration](docs/m3-adoption.md#changed-premise-demonstration) runs the
offset-revision task across a forced restart. It rejects stale checks, preserves
the old candidate's failures, and independently accepts the corrected candidate.
The fake model supplies a fixed program. This establishes local integration
behavior, not model quality or a live provider integration.
M3 is complete for this local scripted-model fixture.

M4 adds a [bounded Lean verification boundary](docs/m4-verification.md).
It uses pinned Comparator and Landrun tools to compare the exact uniqueness target,
enforce the axiom policy, and replay the proof in Lean's kernel.
Durable proof receipts bind exact inputs and retain verification costs.
Claims preserve distinct proof outcomes, completed checks survive restart without
another execution, and unknown attempts keep their reservations.
The [M4 fixture demonstration](docs/m4-fixture.md) applies the same theorem to
the correct, dropped-row, and empty candidates. All three preserve uniqueness;
only the correct candidate passes the unchanged task gates. A separate duplicate-ID
revision blocks application without invalidating the theorem. Native tests cover
a forced restart and repeated reuse. M4 is complete for this local fixed-proposal
fixture; model proof search and broader comparisons remain planned.

The [M5 plan](docs/m5-migration.md) defines a repository migration with a preserved
legacy consumer, an approved requirement for safe repetition, and five implementation slices.
The [first fixture slice](docs/m5-fixture.md) checks three fixed migration programs
under both contracts in isolated containers. It retains independent failures and
reuses completed executions and decisions. These are synthetic fixture formats,
not compatibility commitments for Warranted.
The [worker demonstration](docs/m5-fixture.md#worker-and-restart) now delivers the approved
revision across a forced host kill. It rejects stale evidence, accepts a correction,
and resumes again without repeating work. The model responses are fixed.
The [supported proof cases](docs/m5-proofs.md) add migration and timestamp targets
alongside M4 uniqueness. All three retain successful controls and candidates whose
supported narrow proofs leave independent task failures intact across restart.
The [A–E treatment runner](docs/m5-contexts.md) now connects both fixtures to the
shared context boundary. It preserves workspace files and notes, updates models
and dependency reports, and charges E for real proof work across restart.
Credential-free tests cover all five conditions.
[Scripted trial accounting](docs/m5-migration.md#scripted-trial-accounting) now
pins a development trial list and reports committed results, failures, missing
runs, and recorded usage. Measured model comparisons remain planned.

## What we are building

Warranted is a proposed build system for knowledge. Observations, assumptions,
programs, proofs, and decisions retain their versions and dependencies. A change
can then identify which conclusions need another check.

- **Queryable evidence.** Start with SQLite and ordinary artifact files. Preserve
  raw observations and their provenance; make permitted state easy to inspect.
- **Supported applications.** A valid theorem and justified use of that theorem
  are separate things. Its premises may stop describing the current world.
- **Rules and gates.** A rule permits a recorded exception. A gate requires
  independently checked evidence at its protected transition and has no waiver.
- **Simple worker interfaces.** Prefer shell execution and files. The host owns
  bookkeeping and acceptance; the model chooses how to investigate.
- **Measured search improvement.** Establish a fixed-policy baseline, then test
  an adaptation of [Dream-RSI](https://arxiv.org/html/2609.14858v1) to improve
  scheduling through replay, with the model and acceptance contract held fixed.

The initial task families are controlled data transformations and repository
migrations. Their purpose is to exercise changed assumptions, restarts, and
interdependent work. No existing domain harness or tool protocol defines the
core interfaces.

## Inspirations and foundations

Warranted builds on other people's research and engineering. We want those
influences to be visible alongside the design choices they inform:

- **Dream-RSI** supplies the method behind the planned search-improvement pilot.
  The [design](docs/design.md#dream-rsi-exploration-and-replay) explains its
  contribution and Warranted's adaptation.
- **[Schema](https://schema-harness.github.io/) and
  [PRO-LONG](https://arxiv.org/html/2607.20064v2)** motivate executable world models
  and complete, programmatically accessible histories, respectively.
- **[Vercel's tool-reduction case study](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools)
  and [mini-swe-agent](https://mini-swe-agent.com/latest/)** inform the preference
  for a small worker interface and legible files.
- **[LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) and
  [Lean](https://lean-lang.org/doc/reference/latest/)** are the intended foundations
  for durable workflow execution and formal proof checking.

Our experiment brings these ideas together around evidence, changing assumptions,
and independently checked acceptance. The [design's sources and provenance](docs/design.md#sources-and-provenance)
also explain the project's origins and later candidates, Hindsight and AutoSaddler.

## Get started

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/Lewdwig-V/warranted.git
cd warranted
uv sync --locked
uv run --locked warranted --help
uv run --locked warranted --version
```

The development interpreter is Python 3.12; the package requires Python 3.12 or
newer. uv manages the project environment and dependencies through
`pyproject.toml` and the committed `uv.lock`.

## Development

```bash
uv run --locked pytest -q tests
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --no-sources
```

Use `uv add` for new dependencies and commit the resulting lockfile. The focused
tests cover conflicting requests, duplicate charges, damaged artifacts, budget
breaches, and forced process termination. CI runs these tests on pull requests,
alongside lint, formatting, entry points, and installation of the built wheel.

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
the `m1-walkthrough` artifact for 14 days. The [pilot](docs/pilot.md#m1-scripted-walkthrough)
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

The [pilot](docs/pilot.md#m2-fixture-experiments) gives the expected results and
operation counts. CI runs all three experiments and retains the public development
evidence for 14 days. The host uses explicit dependencies and trusted revision
events. [Claims and current support](docs/m2-claims.md) separate historical checks
from current dependency versions and retain approved revision provenance.
The [acceptance boundary](docs/m2-acceptance.md) enforces all four gates and
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

from warranted.ledger import (
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

The storage format is now version 2. Version 1 projects fail explicitly on open
and remain unchanged. Automatic migration is not implemented.

The [persistence contract](docs/m1-persistence.md) describes the API and tests.
The tests establish recovery from process termination on a working local
filesystem. They do not establish recovery from power loss or disk loss.
Keep the authoritative project outside any untrusted worker's writable workspace.

## Project map

| Path | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Guidance for coding agents and contributors |
| [docs/design.md](docs/design.md) | Standalone design, invariants, and provisional component choices |
| [docs/roadmap.md](docs/roadmap.md) | Ordered milestones, completion criteria, and decision points |
| [docs/pilot.md](docs/pilot.md) | Domain-independent task fixtures and experimental comparisons |
| [src/warranted](src/warranted) | Evidence ledger and help/version CLI |
| [tests/test_ledger.py](tests/test_ledger.py) | Evidence, operation recovery, and accounting cases |
| [src/warranted/exports.py](src/warranted/exports.py) | Explicit selection and independent file copies |
| [src/warranted/acceptance.py](src/warranted/acceptance.py) | Current-context acceptance and scoped rule exceptions |
| [docs/m2-acceptance.md](docs/m2-acceptance.md) | Host boundary, receipt protocol, recovery, and limits |
| [examples/m1](examples/m1) | Fixed CSV fixture and restart walkthrough |
| [examples/m2](examples/m2) | Three fixed experiments for revisions and independent obligations |
| [tests/test_exports.py](tests/test_exports.py) | Export disclosure and publication boundaries |
| [tests/test_walkthrough.py](tests/test_walkthrough.py) | Success, failure, changed input, and interrupted CSV execution |
| [tests/test_m2_fixture.py](tests/test_m2_fixture.py) | Selective work, changed definitions, independent failures, and restart |
| [tests/test_acceptance.py](tests/test_acceptance.py) | Gate bypass attempts, rule exceptions, and acceptance recovery |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | Persistence tests and package checks |

M2 is complete in its trusted local scope. M3 adds worker containment,
external-attempt reconciliation, and a changed-premise demonstration.
See [M3 in the roadmap](docs/roadmap.md#m3--bounded-worker-and-execution-recovery).
