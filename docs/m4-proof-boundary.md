# M4: Lean proof boundary

Implementation plan, 2026-09-19. M3 is complete for the local scripted fixture.
M4 remains unimplemented. This plan defines three implementation PRs.
The optional M3a Jev experiment remains separate.

## Target and scope

Prove one reusable statement: an injective identifier mapping preserves uniqueness.
An injective mapping never maps distinct inputs to the same output.
Use Lean's core string and list definitions with exact identifier equality.

```lean
import Init

namespace Warranted

def UniquenessTarget : Prop :=
  ∀ (ids : List String) (f : String → String),
    (∀ a b, f a = f b → a = b) →
    ids.Nodup → (ids.map f).Nodup

end Warranted
```

`Nodup` means that a list contains no duplicate elements.
The required theorem is `Warranted.uniqueness_preserved : Warranted.UniquenessTarget`.
Pin the statement and every definition that determines its meaning.
The worker cannot replace `Nodup`, change equality, or add assumptions to the target.

Start with `Init`, without Mathlib. Permit only `propext` and `Quot.sound` as
logical axioms, which are assumptions accepted without proof.
Record the actual axiom set across all proof dependencies.
Reject `sorryAx`, custom axioms, and native-evaluation axioms, including indirect uses.
Any expansion requires a new reviewed policy version.

A trusted scratch proof using `List.Pairwise.map` passes on local Lean 4.32.1.
Its reported axioms are exactly `propext` and `Quot.sound`.
This establishes target feasibility only. It does not establish hostile-proof isolation
or a tested Comparator integration.

## Existing tools and trust

Lean supplies the logic and kernel, the small component that checks proof terms.
Its [proof-validation guide](https://lean-lang.org/doc/reference/latest/ValidatingProofs/)
distinguishes compilation, axiom inspection, and validation of hostile proofs.
It documents risks from executable tactics and malformed compiled artifacts.

Adopt [Lean FRO's Comparator](https://github.com/leanprover/comparator) for theorem
comparison, axiom enforcement, and proof replay through the kernel.
Comparator compares a solution with a trusted challenge, the required theorem statement.
Its exporter produces serialized declarations for independent checking.
This is an intended integration. Warranted adds operation identity, containment,
evidence storage, applicability, and acceptance.

PR 1 must pin compatible Lean, Comparator, exporter, and Landrun revisions.
Landrun restricts the files and actions available during proof processing.
Pin build dependencies, executable digests, and the container image as well.
Resolve upstream floating dependencies before building the pinned tool bundle.
An installed Lean version alone does not establish compatibility.

Use Comparator's Lean kernel initially. Independence means separation from the
worker's process and mutable state. A second kernel implementation remains deferred.
The host, pinned verifier, kernel, operating system, and container runtime remain trusted.

## Verification flow

1. The host records the challenge, supporting definitions, axiom policy, tool
   identities, and resource limits before dispatch.
2. The worker proposes a solution in a bounded workspace. Treat all solution code,
   build output, exports, and claimed verdicts as untrusted.
3. Stop worker processes and capture bounded regular files as immutable evidence.
   Bind subsequent work to those captured bytes.
4. Start verification in a fresh environment. The host supplies the trusted
   challenge, build configuration, dependencies, and Comparator configuration.
   Keep them outside every path that untrusted compilation can modify.
5. Contain compilation and export separately from the verifier that reads the
   serialized proof. Compare the target and its definitions, enforce the axiom
   policy, and replay the proof before reporting success.
6. The host records the verifier's attributable outcome and raw evidence.
   Recompute current application support before any protected transition.

Reuse the proven container controls from M3 where their assumptions hold.
Give proof work no network, host credentials, runtime socket, ledger, or private evaluator.
Keep binaries and trusted inputs read-only. Bound processes, CPU, memory, time,
workspace size, captured output, and exported proof size.
Select explicit limits from the first control run and pin them before comparisons.

Comparator's documented sandbox assumptions are part of PR 1's compatibility tests.
Its current guidance includes restrictions on Unix sockets.
Prove that the selected runtime enforces those restrictions inside the outer container.
Missing isolation blocks verification. Do not substitute an unsandboxed build,
worker-supplied configuration, or a successful `lean` exit code.
Never import worker-produced `.olean` files into the host process.

## Durable evidence and outcomes

Use the existing `Ledger`, `Request`, `Evidence`, and `Claims` records.
Add a small proof adapter. Keep the current single trusted host writer.
Reserve the bounded verification attempt before Comparator starts. Its budget
covers compilation, export, and proof replay. Record worker and model attempts
separately through the existing M3 boundary.
Record raw diagnostics, captured artifacts, elapsed time, limits, and known usage.
Report synthetic attempt units separately from measured time and provider costs.

Each verification request binds the exact solution artifact, challenge, supporting
definitions, complete dependency identities, tool bundle, axiom policy, and limits.
A receipt for another target, artifact, environment, or policy cannot transfer.
Validate recorded bytes before reuse. Recovery reuses a completed exact attempt.
An interrupted attempt retains its reservation until its outcome is established.

Keep process execution, proof status, and application support separate:

| Event | Required interpretation |
| --- | --- |
| Exact theorem and policy pass | The conditional theorem is proved |
| Incomplete proof or a known timeout | Unproved, with costs retained |
| Attributable verifier finds a target mismatch or forbidden axiom | `REJECTED`: the submitted proof violates the pinned contract |
| Forged or unattributable receipt | `UNSUPPORTED`: the evidence cannot establish a verifier decision |
| Verifier unavailable or runtime failure | Infrastructure failure |
| Host interruption without an attributable completion | Unknown and reserved |
| A premise changes or loses support | Application blocked, historical theorem still valid |

`REJECTED` requires a verifier decision bound to the exact artifact and policy.
`UNSUPPORTED` evidence cannot settle an unknown operation or release its reservation.
Neither status establishes that the target theorem is false.

`Claims.assess()` currently distinguishes validation from version applicability.
Its `CURRENT` result establishes current versions, not the truth of an assumption.
Parent edges also do not infer truth from another claim.
Require passing, current evidence for every application premise and the candidate
correspondence check. Preserve `UNPROVED` for an unsuccessful proof attempt.
Adapt result handling explicitly if the current boolean checker field cannot express it.

## Fixture applications

Reuse the M2 evaluator and its four independent gates without weakening them.
Retain the existing scoped source-offset exception.
For each candidate, record a host-defined selection of source rows and use the
identity mapping on identifiers. The identity mapping is injective.
The host checks selected identifiers for duplicates and compares the candidate's
identifier sequence with the mapped selection.

| Candidate | Selected source rows | Proof application | Task acceptance |
| --- | --- | --- | --- |
| Existing correct candidate | All three | Supported | Accepted |
| Existing dropped-row candidate | First two | Supported | Rejected |
| Existing empty candidate | None | Supported | Rejected |

All three selections satisfy the theorem's premises. Their identifiers match the
formal model, including the empty list. The two negative candidates demonstrate
that proved uniqueness permits record loss. The record-preservation gate blocks
them, and every other failed obligation remains recorded.

Treat premise changes as a separate experiment. Use an explicitly versioned input
with duplicate selected identifiers and a pinned host-authorized transition.
The old application becomes stale. A new application lacks the uniqueness premise.
Neither event invalidates the conditional theorem or its historical proof receipt.

The host's parsing and correspondence checks connect this theorem to the captured
candidate. This does not prove the Python transformation or parser correct.
Timestamp proofs, case-insensitive equality, and repository migrations remain later work.

## Three implementation PRs

1. Verification boundary. Pin the compatible tool bundle, exact challenge, and
   axiom policy. Run a valid proof through contained compilation and independent
   verification. Cover direct and indirect `sorryAx`, forbidden axioms, changed
   definitions, extra hypotheses, malformed artifacts, forged output, and writes
   to trusted files. Establish native CI coverage before claiming this boundary.
2. Durable proof receipts. Connect the verifier to host operations and claims.
   Bind exact identities, retain costs, and distinguish all outcomes above.
   Assert the specified `REJECTED` and `UNSUPPORTED` statuses before and after restart.
   Test wrong-target receipt reuse, corrupt bytes, budget exhaustion, unknown
   attempts, and host deaths before and after completion. Use an execution witness
   outside the ledger to detect duplicate work.
3. Fixture and recovery demonstration. Reuse the theorem for the three candidates
   and the separate changed-premise case. Restart between proof capture and
   application. Preserve proof success, failed task obligations, and total costs.
   Require repeated resume to add no completed verification or checker executions.

Each PR must provide a complete runnable slice and pass GitHub checks.
Keep ordinary tests free of external services and model credentials.
Run actual Lean and isolation tests in a separate required native job.
Retain versioned reports, raw diagnostics, dependency identities, and execution witnesses.
Leave the M4 roadmap boxes unchecked until their stated evidence exists.

Compare proof setup, first verification, and reuse costs with the existing executable
baseline. Report failures and timeouts alongside successes.
M4 completion establishes this fixture's proof boundary, not a measured general benefit
from Lean or an assurance that the formal target captures every user requirement.
