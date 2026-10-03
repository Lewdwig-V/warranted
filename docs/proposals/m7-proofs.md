# M7 design note: proofs in the task layer

Proposed 2026-10-03, with its four review questions decided the same day (see
[decisions](#decisions)). This note designs how proof verification enters the
public task layer, so the CSV and migration fixtures' proof cases can run on it.
This is the remaining gap in M7's
[completion evidence](../roadmap.md#m7--stable-harness-api-and-cli). It builds on
[proof verification](../reference/proof-verification.md), which stays the
reference for the verifier itself, and on the
[checker interface](m7-harness-api.md#checker-interface).

## Problem

Proof verification exists only in the host kit (`warranted.host`: `Proofs`,
`proof_status`, `Claims`). The M4 and M5 drivers call it directly. Three things
keep it out of the task layer:

- **Targets live in the core.** `src/warranted/proof/targets.json` and its three
  `.lean` challenges (`uniqueness`, `timestamp`, `migration`) are fixture content.
  They are also baked into the verifier image: the in-image supervisor reads the
  challenge from `/opt/proof`. A domain such as ReSchema cannot add a target
  without changing Warranted. That contradicts AGENTS.md, which says domain
  behaviour enters only through the checker, job, and worker-image interfaces.
- **No task-layer shape.** A task requires checks, and nothing connects a
  required check to a verifier, a bundle, or a proof application.
- **No unproved outcome.** `VerdictStatus` cannot express "could not be
  established within limits". AGENTS.md lists that outcome as one to keep
  distinct.

## Decisions

1. **Domains own their targets.** A domain declares each target as a challenge
   file plus the required theorem. The core keeps the verifier, the axiom policy,
   and the toolchain. The three existing targets move to `examples/`, next to
   their fixtures.
2. **A proof is a checker.** Warranted ships a ready-made `LeanProof` checker
   that a domain lists in `checkers`. A task requires it by name next to its
   other checks. Proof obligations and application records are not added to
   `TaskSpec`.
3. **`VerdictStatus.UNPROVED`** is a new status. It fails the gate like
   `rejected`, but it is recorded and shown as its own outcome. Any checker may
   use it.
4. **The verifier is project infrastructure.** It is passed to the project the
   way the job runner is, and the bundle digest is bound into the project
   identity.

## Interface

New public names in `warranted`:

| Name | Role |
| --- | --- |
| `ProofTarget(challenge, theorem)` | A domain-owned target. It reads the challenge bytes when constructed. `theorem` is the fully qualified theorem name. |
| `LeanProof(target, source="Solution.lean", premises=None)` | A checker: it verifies the captured file `source` against `target`, then checks the premises. |
| `ProofVerifier` | A protocol: `identity: str` and `verify(challenge, theorem, source) -> ProofResult`. |
| `LeanVerifier(bundle_path)` | The verifier: the pinned bundle and hardened container from [proof verification](../reference/proof-verification.md). |
| `ProofResult` | The verifier's status, diagnostic, axiom set, and raw evidence. |
| `VerdictStatus.UNPROVED` | Fails the gate and is recorded distinctly. |

```python
class CsvDomain:
    ...
    checkers = {
        "transformation": Transformation(),
        "uniqueness": LeanProof(
            ProofTarget(HERE / "Uniqueness.lean", "Warranted.uniqueness_preserved"),
            premises=uniqueness_premises,
        ),
    }

project = Project.create(root, CsvDomain(), allowances,
                         proofs=LeanVerifier(Path("runs/m4-tools/bundle.json")))
```

`CheckContext` gains `verify(target, source)`. It calls the project's verifier
and records the call and its raw output as host-only evidence, as `run_job` does.

## Data flow

1. The worker writes the proof file (`Solution.lean` by default) next to its
   other candidate files. The host captures it with the candidate.
2. `LeanProof.check(ctx)` rejects a missing, empty, or oversized source before
   verification, with feedback.
3. `ctx.verify(target, source)` runs the verifier. The host supplies the
   challenge bytes and the theorem name to the supervisor at verification time,
   not from the image. The worker supplies only the proof source, so it cannot
   select or alter the target.
4. The proof status maps to a verdict:

   | Proof | Verdict |
   | --- | --- |
   | `proved` | `PASSED`, subject to premises |
   | `rejected` | `REJECTED` |
   | `unproved` | `UNPROVED` |
   | `unsupported` | `UNSUPPORTED` |
   | infrastructure failure | `INFRASTRUCTURE_FAILURE` |

5. Feedback holds the proof status, the diagnostic, the axiom set, and each
   premise result. The raw verifier output stays host-only in the check record,
   and that record is the durable proof receipt.

A passing proof never authorizes acceptance on its own. Acceptance requires
every required check, so a task that also requires `transformation` rejects a
proved but record-dropping candidate. This is M4's guarantee, now enforced by
the ordinary gate.

## Premises and applicability

`premises` is trusted domain code, `fn(ctx) -> {name: bool}`. It checks the
theorem's premises against the exact candidate and inputs, and it may use
`run_job`. The checker always runs both the proof and the premises:

| Proof | Premises | Verdict |
| --- | --- | --- |
| proved | all hold | `PASSED` |
| proved | any fails | `UNSUPPORTED`: the theorem stays valid; it does not apply here |
| not proved | any | the proof's own status |

This keeps proof validity separate from current applicability (invariant 2).
The task layer re-checks every submission under the contract in force, so an
application is never carried past a revision that changes its premises. That
is the task-layer form of M4's "stale, then unsupported" result. A premise
function that raises is a checker fault, never a pass.

## Identity and pinning

- **Target bytes.** A checker may declare `sources`, which are folded into the
  domain's identity digest beside the domain's own declared sources.
  `LeanProof` declares its challenge file, so an edited theorem or challenge
  refuses to reopen the project.
- **Verifier.** `LeanVerifier.identity` includes the bundle digest, the policy
  digest, and the toolchain manifest. A rebuilt bundle refuses to reopen the
  project, as a changed job runner does.
- **No verifier.** A project created without `proofs=` refuses to start a task
  whose contracts, including scheduled revisions, require a `LeanProof` checker.
- **Bundle change.** The supervisor now reads the challenge and theorem from the
  host at verification time, which changes the image. Existing bundles are
  refused and must be rebuilt with `scripts/build_proof_bundle.py`. `toolchain.json`
  and the axiom policy are unchanged.

## Fixtures

The three targets move out of `src/warranted/proof/`:

| Target | Moves to | Task-layer cases |
| --- | --- | --- |
| `uniqueness` | `examples/m7/csv/` | Correct: accepted. Dropped row: the proof passes, `transformation` rejects. Duplicate-ID input: `uniqueness` is `UNSUPPORTED` because a premise fails. |
| `timestamp` | `examples/m7/csv/` | One-hour offset: accepted. Zero offset: proved, rejected by `transformation`. |
| `migration` | `examples/m7/migration/` | Label kept: accepted. Label dropped: proved, rejected by the `legacy_label` obligation. |

Premise functions port the M4 and M5 application checks, including the
correspondence between the candidate's output and the selected model variant.

The host kit's `Proofs` and `proof_status` stay in `warranted.host` so the M4
and M5 drivers keep working. They now read the moved challenges by path.
Retiring those drivers is separate work.

## Negative cases

| Case | Required result |
| --- | --- |
| Proof file missing, empty, or oversized | `REJECTED` with feedback; no verification runs |
| Worker writes `verdict.json` or a receipt-like file | Ignored; only the verifier's output decides |
| Proof uses `sorry` or a forbidden axiom | `REJECTED` by the axiom policy, recorded with its axioms |
| Proof fails to compile or export, or reaches the time or output limit | `UNPROVED`, distinct in status, export, and campaign reports |
| Proved, but a premise fails | `UNSUPPORTED`; the proof result stays in feedback |
| Premise function raises | Checker fault; never a pass |
| Edited challenge or theorem name | Project refuses to reopen |
| Rebuilt bundle | Project refuses to reopen |
| Task needs a proof, project has no verifier | `start` refused before any run record |
| Container runtime unavailable | `INFRASTRUCTURE_FAILURE`, not rejection |

## Tests

- **Fast suite (no Lean).** A `FakeVerifier` returns scripted `ProofResult`s.
  It covers the status mapping, premises, every negative case above except the
  real axiom-policy check, identity pinning, and refusal without a verifier.
- **Proof suite (`-m proof`).** It uses the real bundle: one accepted and one
  rejected-by-the-gate row per target, plus one `sorry` proof. CI's proof job
  gains the new test file.

## Versioning

This ships as **0.2.0**. It adds names to `warranted`, adds a member to
`VerdictStatus`, and requires a rebuilt bundle. The changelog lists all three
under *Added* and *Changed*, and the [API reference](../reference/api.md) gains
the new names.

## Out of scope

- Retiring the M4 and M5 drivers or the host kit's `Proofs`.
- Provers other than Lean, and model-driven proof search.
- Running proof tasks through the CLI. They need the same scripted-model path as
  the other fixtures.

## Delivery

Two pull requests:

1. **Verifier and API.** Domain-supplied challenges in the supervisor, the
   `ProofTarget`, `LeanProof`, `ProofVerifier`, `LeanVerifier`, and `ProofResult`
   types, `UNPROVED`, identity pinning, the fast suite, and docs.
2. **Fixtures.** The three targets move to `examples/`, with their task files,
   premise functions, and proof suite. The roadmap and changelog are updated.
