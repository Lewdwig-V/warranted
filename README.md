# Warranted

**Durable, checkable knowledge for long-horizon agent work.**

An agent should be able to preserve what it has learned, explain what supports
it, and revisit the right conclusions when the facts change. Warranted explores
that idea through executable artifacts, an evidence ledger, explicit
dependencies, and independently checked acceptance conditions.

The design principle is simple: **make the world legible, give the model room to
explore, and be precise about what counts as established knowledge.**

## Status

The first local evidence slice is implemented. Its Python API stores immutable
observations and raw artifact bytes with their origins, and recovers them after
process interruption. It uses SQLite and files with one trusted writer.

Operation deduplication, reservations, accounting, worker isolation, gates, Lean
verification, and replay remain planned. M1 is still in progress.

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
tests cover invalid records, damaged artifacts, transaction failures, and forced
process termination. CI runs these tests on pull requests, alongside lint,
formatting, entry points, and installation of the built wheel.

## Local evidence API

This example captures bytes from a file snapshot in a temporary project. It uses
the trusted host API. The CLI still provides help and version information only.

```python
import platform
from pathlib import Path
from tempfile import TemporaryDirectory

from warranted.ledger import Ledger, Manifest, Origin, Snapshot

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
        ledger.record(session, origin, {"raw": ledger.read_artifact(source)})
    with Ledger.open(root) as ledger:
        observation = ledger.history()[0]
        assert ledger.read_artifact(observation.artifacts["raw"]) == b"example bytes\n"
```

`record` commits captured evidence. It does not complete an operation or accept
a task. Repeating a call creates another observation. Missing or corrupt artifact
bytes fail explicitly. Declared allowances are recorded but not yet enforced.

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
| [tests/test_ledger.py](tests/test_ledger.py) | Evidence persistence and failure cases |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | Persistence tests and package checks |

The next implementation slice adds operation receipts, recovery, and accounting
to the local ledger. See
[M1 in the roadmap](docs/roadmap.md#m1--local-evidence-and-restart).
