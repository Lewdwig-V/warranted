# Working on Warranted

## Start here

Read [README.md](README.md), [docs/design.md](docs/design.md), and
[docs/roadmap.md](docs/roadmap.md). Component behavior is documented in
[docs/reference/](docs/reference), task fixtures in [docs/fixtures/](docs/fixtures),
and experiments in [docs/experiments/](docs/experiments). Use
[the evaluation design](docs/experiments/evaluation-design.md) when changing an
evaluator, task fixture, or experiment. Keep implementation status honest: planned
machinery is not an existing guarantee.

Warranted is becoming a usable harness. Document components for the engineer who
runs them: what they do, how to call them, what they guarantee, and their limits.
Record measured runs as dated files under `docs/experiments/` and leave their
findings unchanged afterwards. Keep plans in the roadmap, not in reference docs.

Warranted is a domain-independent harness. ReSchema, a reverse-engineering
project, is its first external consumer and will depend on Warranted's public
API; Warranted never depends on ReSchema. Derive interfaces from the CSV and
migration fixtures and ReSchema's needs together. Do not put another project's
tool names, validator rules, state formats, budgets, or acceptance semantics into
the core; domain behavior enters through the checker, job, and worker-image
interfaces. Host-mediated operations need a reviewed design before any
implementation.

## Development commands

Use Python 3.12+ and uv. Commit `pyproject.toml` and `uv.lock` together when
dependencies change; let uv generate the lockfile.

```bash
uv sync --locked
uv run --locked pytest -q tests
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --no-sources
```

The default suite needs no credentials, network services, or containers. Tests
marked `container` or `proof` need rootless Podman and the pinned images; CI runs
them in separate jobs. The CLI prints help and version only, so its output
validates nothing about the library. Run focused tests via `uv run`, and do not
add passing placeholder tests.

## Implementation style

- Build the smallest complete slice in the roadmap. Keep its interfaces
  provisional until a second use case demonstrates that they generalise.
- Prefer ordinary Python, SQLite, files, and existing runner facilities. Add
  dependencies at the milestone that needs them; avoid a custom agent loop,
  workflow engine, ORM, or plugin framework without a demonstrated need.
- Give the worker shell/file capabilities and legible state. Internal ledger
  records must not become a mandatory sequence of model-facing tools or CLI
  forms. Let the host capture mechanical provenance and resource usage.
- Keep candidate files separate from authoritative state. Shell access must
  preserve the same acceptance and external-effect boundaries as every other
  interface; a command wrapper alone is not isolation.
- Use explicit types at state and trust boundaries. Preserve distinct outcomes
  such as rejected, unproved, unsupported, unknown, and infrastructure failure.
- Keep documentation and runnable commands in sync. Explain why a new abstraction
  or dependency is needed, and distinguish measurements from expectations.
- Name and link research or engineering influences where their ideas enter the
  design. Preserve attribution when generalising interfaces or moving documents;
  distinguish borrowed methods, our adaptations, and implemented integrations.

## Invariants to preserve

1. Raw evidence is versioned and retains its origin. Agent-authored text cannot
   forge a tool result or an authoritative acceptance receipt.
2. Proof validity and current applicability are separate. Superseded premises
   block dependent applications without turning a valid conditional theorem false.
3. Rules have recorded exceptions; gates have independently checked evidence and
   no opt-out. Unknown kinds and missing/stale evidence cannot default to success.
4. The host checks gates against the exact target, inputs, environment, and current
   dependencies at the protected transition. Workers and optimisers cannot weaken
   the target, checker, applicability test, or budget enforcement.
5. Cumulative usage and unresolved reservations survive restarts. An unknown
   external outcome is reconciled or left blocked; never blindly retry a side effect.
6. Replay reveals only recorded outcomes under compatible context. Missing history
   is unsupported, replay adds no new evidence, and it cannot call live tools.
7. Training, development, and held-out tasks remain separate by provenance and
   lineage. Report failed attempts and optimisation cost as well as successes.
8. Passing a check establishes only its stated scope, not fidelity to user intent
   or whole-task success. Specifications remain binding until an authorised
   revision; preserve failed independent obligations and known gaps. See
   [intent, contracts, and acceptance](docs/design.md#intent-contracts-and-acceptance).

These are requirements for the future runtime. Maintain the threat model and
negative cases as each boundary is implemented; do not claim enforcement from
documentation or a prompt alone.

## Verification and changes

For behavior changes, reproduce the failure or write its negative case first.
Test observable guarantees: restart durability, exact operation identity, stale
dependency rejection, gate bypass attempts, and replay leakage. Avoid assertions
that only mirror private implementation structure. Fixture/evaluator changes
must not quietly make the same candidate acceptable under a weaker contract.

Keep fast local checks free of model credentials and external services. Mark and
budget integration/benchmark runs separately. Record the model, policy, evaluator,
environment, task versions, and total costs needed to interpret a result.

Use focused feature branches and PRs after the initial repository bootstrap.
Do not force-push shared branches. Check relevant tests and the documented lint /
build commands before proposing a merge; state any unavailable checks accurately.
Update milestone checkboxes only when their completion criteria are demonstrated.
