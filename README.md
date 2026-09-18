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
another charge, and uncertain executions remain blocked. Worker isolation, gates,
Lean verification, and replay remain planned. M1 is still in progress.

There are no model calls, external services, or runtime dependencies in the
current package. The command does not create a project or execute a task yet.

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
uv run --locked pytest -q tests/test_ledger.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --no-sources
```

Use `uv add` for new dependencies and commit the resulting lockfile. The focused
tests cover conflicting requests, duplicate charges, damaged artifacts, budget
breaches, and forced process termination. CI runs these tests on pull requests,
alongside lint, formatting, entry points, and installation of the built wheel.

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
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | Persistence tests and package checks |

The next implementation slice adds permitted exports and the scripted CSV
walkthrough. See [M1 in the roadmap](docs/roadmap.md#m1--local-evidence-and-restart).
