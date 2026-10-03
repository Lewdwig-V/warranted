# Changelog

Changes to Warranted's public API (`warranted`), CLI, file formats, and host kit
(`warranted.host`). The [API reference](docs/reference/api.md) states the
versioning and deprecation policy. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- A campaign plan is refused if one of its tasks was planned in another split
  by a campaign already pinned in the project.
- Proof targets are owned by domains: `ProofTarget(path, theorem)` replaces
  the core `targets.json` registry, and the verifier receives the challenge and
  theorem from the host. The three fixture challenges moved to `examples/`.
  `warranted.host.Proofs` takes `target=` instead of `target_id=`. Existing proof
  bundles are refused; rebuild with `scripts/build_proof_bundle.py`.

### Added

- Proofs in the task layer: the `LeanProof` checker, `ProofVerifier` and
  `LeanVerifier` (passed as `Project(..., proofs=...)` and bound into the project
  identity), `ProofResult`, `ProofStatus`, and `CheckContext.verify`. Checkers may
  declare `sources`, which are pinned like domain sources. `LeanProof` takes
  `premises` (applicability; a failure is unsupported) and `correspondence` (the
  candidate matches the theorem's model; a failure is rejected), and is
  isolated only when constructed with `isolated=True`. Its version pins the
  challenge bytes it verifies; the verifier identity includes the timeout.
- `VerdictStatus.UNPROVED`: fails the gate and is recorded distinctly.
- `ProofTarget` in `warranted`.
- An Apache License 2.0 `LICENSE` file; the package metadata declares
  `Apache-2.0`.

### Fixed

- A checker result that is not a `Verdict` is recorded as an infrastructure
  failure. Before, a non-`Verdict` left the run `unknown`, and an object shaped
  like a passing `Verdict` was accepted.

## [0.1.0] - 2026-10-03

The first tagged release, and the first a domain project can pin. The task layer
is the M7 prototype: its interfaces are derived from three fixtures and remain
provisional until ReSchema runs on them (M8).

### Added

- **Task layer (`warranted`).** `Project` with one ledger per project: create,
  start, resume, status, export, and memory; typed run outcomes that keep
  rejected, incomplete, unknown, unsupported, and infrastructure failure
  distinct (#40, #55).
- **Domains and checkers.** `Domain`, `Checker`, `CheckContext`, and `Verdict`;
  checker private inputs, host-recorded seeds, contained checker jobs
  (`PodmanJobs`, `JobLimits`), pinned domain sources, and a 64 KiB feedback limit
  (#43, #46, #52).
- **Domain worker images** pinned by digest or local image ID (#52).
- **Run-scoped budgets and blocking**: caps per run under the project
  allowances; an unknown outcome blocks only its own run (#42).
- **Token budgets** as reserved ledger units, enforced for verified models; no
  model is verified yet (#45).
- **Host-mediated operations** (`Operation`) with reservation before execution,
  deduplication, and no blind retry of unknown outcomes (#50).
- **Duplicate guard** for exact and near repeats of rejected candidates (#51).
- **Scoped memory**: verified facts and promoted notes shared across a task
  family, withheld when a dependency changes (#54).
- **Campaigns** and artifact import (#57).
- **Contract revisions**: `Revision`, scheduled revisions at submission
  checkpoints, and project revisions for new runs (#58).
- **CLI**: `warranted init`, `run`, `resume`, `status`, `memory`, `export`,
  `import`, `revise`, and `campaign` (#56, #57, #58).
- **Fixtures on the task layer**: the CSV transformation and configuration
  migration fixtures (#59), and a reverse-engineering-style fixture with negative
  cases for leaked private inputs, forged verdicts, and bypassed budgets (#60).
- **Host kit (`warranted.host`)**: the evidence ledger, acceptance boundary,
  claims, proof verification, worker sandbox, and model adapters behind the
  M1–M5 milestones (#4–#36).
- **Release discipline**: this changelog, the [API reference](docs/reference/api.md)
  tested against `warranted.__all__`, and a CI check that a release tag names the
  package version.

### Known gaps

- The CSV and migration fixtures' proof cases still run only on the host kit,
  and no fixture runs through the CLI yet.
- The CLI supports only local OpenAI-compatible model endpoints.
- A project opens only with the Warranted build that created it; every upgrade
  needs a new project.

[Unreleased]: https://github.com/Lewdwig-V/warranted/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Lewdwig-V/warranted/releases/tag/v0.1.0
