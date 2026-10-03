# Proof verification

Warranted checks captured Lean source against a host-selected, pinned theorem
inside a contained verifier, and records the attributable result as a durable
proof receipt in the ledger. A worker can supply only proof source bytes; it
cannot choose the target, axiom policy, build configuration, or tool image.
A proof receipt establishes one conditional theorem. It never authorizes task
acceptance, proves a Python implementation correct, or establishes the premises
of an application. Those need independent checks (see
[intent, contracts, and acceptance](../design.md#intent-contracts-and-acceptance)).

The verifier is exercised with fixed, reviewed proof proposals. There is no
model-driven proof worker or proof-search evaluation.

In the task layer, a domain lists a `LeanProof` checker and the project is created with `proofs=LeanVerifier(bundle)`; see [proofs in the task layer](../proposals/m7-proofs.md).

## Targets and axiom policy

A domain supplies each target as a `ProofTarget(path, theorem)`: a Lean challenge
file and the qualified theorem name a proof must establish. `ProofTarget` reads
the challenge bytes once, and the host passes both challenge and theorem to the
in-container supervisor at verification time; a worker cannot select or alter
them. The fixtures' three targets are listed in
[`examples/proof_targets.py`](../../examples/proof_targets.py):

| Key | Challenge | Required theorem | Statement |
| --- | --- | --- | --- |
| `uniqueness` | [`examples/m7/csv/UniquenessChallenge.lean`](../../examples/m7/csv/UniquenessChallenge.lean) | `Warranted.uniqueness_preserved` | An injective identifier mapping preserves `Nodup` |
| `timestamp` | [`examples/m7/csv/TimestampChallenge.lean`](../../examples/m7/csv/TimestampChallenge.lean) | `Warranted.timestamp_roundtrip` | `(localSeconds - assumedOffsetSeconds) + assumedOffsetSeconds = localSeconds` over `Int` |
| `migration` | [`examples/m7/migration/MigrationChallenge.lean`](../../examples/m7/migration/MigrationChallenge.lean) | `Warranted.migration_renaming` | Migration preserves host and timeout under renaming, for label-keeping and label-dropping variants |

The uniqueness target uses Lean's core string and list definitions with exact
identifier equality (`Nodup` means a list has no duplicate elements):

```lean
import Init

namespace Warranted

def UniquenessTarget : Prop :=
  ∀ (ids : List String) (f : String → String),
    (∀ a b, f a = f b → a = b) →
    ids.Nodup → (ids.map f).Nodup

theorem uniqueness_preserved : UniquenessTarget := by sorry

end Warranted
```

Each challenge pins the statement and every definition that determines its
meaning; the worker cannot replace `Nodup`, change equality, or add hypotheses.
The challenge contains a `sorry` placeholder, as Comparator expects; the solution
must supply the same theorem and definitions. There are no definition holes
(which would let a candidate choose part of the specification). Challenges import
only `Init`, without Mathlib. Candidate metaprograms can use the bundled Lean
distribution but cannot add external libraries.

The permitted axioms are exactly `propext` and `Quot.sound`
([`config.json`](../../src/warranted/proof/config.json)). The verifier records the
actual axiom set across all dependencies and rejects `sorryAx`, custom axioms,
and native-evaluation axioms, including indirect uses. Expanding the set requires
a new reviewed policy version, which changes the policy digest.

A value that is not a `ProofTarget` fails before any runtime dispatch. A receipt for one target
cannot support another. The timestamp theorem treats the offset as a model
parameter; it does not establish which offset the source contract requires. The
migration theorem does not cover the optional label. The fixtures that use these
targets keep independent task checks for exactly those gaps.

| Target | Used by |
| --- | --- |
| `uniqueness` | [CSV transformation, M4 uniqueness application](../fixtures/csv-transformation.md#m4-uniqueness-application) |
| `timestamp`, `migration` (and `uniqueness` again) | [Configuration migration proof cases](../fixtures/config-migration.md#proof-cases) |

## Tools, trust, and attribution

Lean supplies the logic and kernel, the small component that checks proof terms.
The [Lean proof-validation guide](https://lean-lang.org/doc/reference/latest/ValidatingProofs/)
distinguishes compilation, axiom inspection, and validation of hostile proofs,
and documents risks from executable tactics and malformed compiled artifacts.

[Lean FRO's Comparator](https://github.com/leanprover/comparator) supplies theorem
comparison against the trusted challenge, axiom traversal, export parsing, and
kernel replay. Warranted replaces its CLI entry point with
[a small driver](../../src/warranted/proof/Verify.lean) that keeps the upstream
checking functions, writes an attributable decision separately from compiler
output, and captures the actual axiom set and both serialized exports.
[lean4export](https://github.com/leanprover/lean4export) produces the serialized
declarations. [Landrun](https://github.com/Zouuup/landrun) restricts files and
actions during compilation and export. Warranted supplies containment, artifact
identity, and durable evidence.

The [toolchain manifest](../../src/warranted/proof/toolchain.json) pins release
and source-archive digests: Lean 4.34.0, matching Comparator and lean4export
revisions, Landrun, Go 1.26.1, and a base Python image by registry digest.
Landrun's pinned `go.mod` and `go.sum` identify its build dependencies. The
build checks those modules, uses a local checked exporter archive, and never
resolves Comparator's upstream floating `master` dependency. The built verifier
and exporter use the same Lean distribution. An installed Lean version alone does
not establish compatibility.

Independence means separation from the worker's processes and mutable state.
The verifier uses Comparator's Lean kernel; no second kernel implementation is
used. The host, tool builder, pinned verifier, Lean kernel, Landrun, operating
system, and container runtime are trusted.

## Verification flow

1. The host records the challenge, definitions, axiom policy, tool identities,
   and limits before dispatch.
2. A worker proposes a solution in its own bounded workspace. All solution code,
   build output, exports, and claimed verdicts are untrusted.
3. The host stops the producing worker and captures bounded regular files as
   immutable evidence. Later work binds to those captured bytes.
4. Verification starts in a fresh container. The host supplies the challenge,
   build configuration, dependencies, and Comparator configuration outside every
   path that untrusted compilation can modify.
5. Compilation and export run contained, separately from the verifier that reads
   the serialized proof. The verifier compares target and definitions, enforces
   the axiom policy, and replays the proof in the kernel.
6. The host records the attributable outcome and raw evidence, and recomputes
   current application support before any protected transition.

## Build the bundle and run native checks

Requirements: Linux x86-64, rootless Podman, cgroup v2, seccomp, Landlock ABI 3
or newer, and `zstd` for the Lean release archive. The build downloads pinned
tools and needs several gigabytes of disk; verification itself has no network.

```bash
uv sync --locked
uv run --locked python scripts/build_proof_bundle.py runs/m4-tools
WARRANTED_PROOF_TESTS=1 \
WARRANTED_PROOF_BUNDLE=runs/m4-tools/bundle.json \
uv run --locked pytest -q -m proof tests/test_proofs_native.py tests/test_m4_fixture.py
```

`build_proof_bundle.py DESTINATION [--image-archive PATH]` builds the image and
writes a host-owned `DESTINATION/bundle.json`; `--image-archive` also saves the
image as an OCI archive. Keep `bundle.json` outside worker storage. It records the
image ID, the tool-closure manifest (digests of every file under `/opt` in the
image), source pins, policy digest, and `build_elapsed_ns`. The image ID also
identifies the base system and libraries. A bundle whose policy digest, image ID
format, or toolchain pins disagree with the current source is refused before
dispatch, so rebuild after changing anything under `src/warranted/proof/`,
`_proofs.py`, or `_containers.py`.

The ordinary suite skips native proof tests unless `WARRANTED_PROOF_TESTS=1`.
An enabled native test fails when isolation or the tool image is unavailable.
Native tests write per-case reports under `WARRANTED_PROOF_REPORTS` (default
`runs/m4-native`). The `proof` job in [CI](../../.github/workflows/ci.yml) builds
or restores the bundle, runs `tests/test_proofs_native.py`,
`tests/test_m4_fixture.py`, and `tests/test_m5_treatments.py` with `-m proof`, and
retains reports, `bundle.json`, the build log, and execution witnesses for 14 days.
Download and cached build times vary and do not measure proof reuse.

## Direct API: `verify()`

```python
import json
from pathlib import Path

from warranted import ProofTarget
from warranted.host import verify_proof as verify

target = ProofTarget(
    "examples/m7/csv/UniquenessChallenge.lean", "Warranted.uniqueness_preserved"
)
source = Path("examples/m4/Solution.lean").read_bytes()
result = verify(source, Path("runs/m4-tools/bundle.json"), target=target)
Path("runs/m4-control.json").write_text(json.dumps(result.as_dict(), indent=2))
print(result.status, result.axioms)
```

The expected status is `proved` with actual axioms `Quot.sound` and `propext`.

`verify(source, bundle_path, *, target, seconds=120)` returns a `Verification`
(`status`, `diagnostic`, `axioms`, `identity`, `elapsed_ns`, `raw`). `source` must
be nonempty `bytes` of at most 1 MiB, captured by the host after stopping the
producing worker. `seconds` is an integer from 1 to 120; the result records the
exact value. `verify()` does not reserve budget, persist evidence, or authorize
acceptance; use [`Proofs`](#durable-proof-receipts) for that. If container cleanup
cannot be established, it raises instead of returning a known result.

## Containment and limits

- A fresh rootless container per attempt: no network, no host mounts, no host
  credentials, runtime socket, ledger, or private evaluator; read-only root;
  private PID/IPC/UTS/cgroup namespaces; `no-new-privileges`.
- Only the source bytes are copied in. The image, trusted configuration,
  challenge, and source stay outside the compiler's writable paths.
- Compilation and export run under Landrun as UID 1000 without effective
  capabilities. Comparator runs outside that Landrun domain and reads only the
  serialized exports. The host never imports a generated `.olean` file.
- An inherited seccomp filter, stacked on Podman's default, denies Unix socket
  creation, socket pairs, `ptrace`, and `process_vm_writev`, enforcing
  [Comparator's documented Unix-socket restriction](https://github.com/leanprover/comparator#readme).
  Before each attempt a probe requires Landlock support, denied writes to the
  decision file, and denied Unix sockets. Missing controls are an infrastructure
  failure; there is no fallback to an unsandboxed build, worker-supplied
  configuration, or a successful `lean` exit code.

| Limit | Value |
| --- | --- |
| CPU / memory / processes | 1 CPU, 1 GiB (no extra swap), 64 |
| Verification time | 120 s ceiling; host may select less with `seconds=` |
| Container lifetime | 180 s |
| Source | 1 MiB |
| Each export | 8 MiB |
| Captured console output | 2 MiB |
| Workspace / temporary directory | 64 MiB / 8 MiB |

The host stops remaining processes before capturing bounded regular files, and
removes the container before returning a known result.

## Outcomes

| Status | Meaning |
| --- | --- |
| `proved` | Exact comparison, axiom enforcement, and kernel replay pass. |
| `rejected` | An attributable verifier decision rejects the exported target, definitions, axioms, or proof. |
| `unproved` | Compilation or export fails, or the attempt reaches the time or output limit. |
| `unsupported` | The host lacks a complete, well-formed verifier decision. |
| `infrastructure_failure` | Required tools, isolation, or trusted execution fail. |

Compiler output is diagnostic evidence only; a printed success message cannot
decide the result. The decision comes from the protected decision file and the
supervisor's exit status. `rejected` requires a decision bound to the exact
artifact and policy. No status other than `proved` shows the target theorem false.

## Durable proof receipts

`warranted.host.Proofs` connects the verifier to the
[evidence ledger](evidence-ledger.md) and [claims](claims-and-acceptance.md), with
one trusted writer outside worker storage. Build the bundle first, then run once
with a new ledger destination:

```python
from pathlib import Path

from warranted.host import Evidence
from warranted.host import Claims
from warranted.host import Ledger, Manifest, Snapshot
from warranted.host import Proofs

root = Path("runs/m4-receipts")
root.parent.mkdir(parents=True, exist_ok=True)
bundle = Path("runs/m4-tools/bundle.json")
snapshots = {
    "solution": Snapshot(
        Path("examples/m4/Solution.lean").read_bytes(),
        "reviewed development proof",
        "1",
    ),
}
with Ledger.create(
    root,
    Manifest("m4-proof", "1", "run-1", "world-1", {}, {"proof": 1}),
    snapshots,
) as ledger:
    session = ledger.start_session()
    proofs = Proofs(ledger, session, bundle, target=target)
    solution = Evidence("solution", ledger.project.snapshots["solution"].artifact)
    request = proofs.check("uniqueness-1", solution)
    claim = Claims(ledger, session).record(
        "The conditional uniqueness theorem.",
        proofs.target,
        {},
        validation=(request, "proof"),
        complete=True,
    )
    print(Claims(ledger, session).assess(claim, {}))

with Ledger.open(root) as ledger:
    session = ledger.start_session()
    proofs = Proofs(ledger, session, bundle, target=target)
    assert proofs.check("uniqueness-1", solution) == request
    print(Claims(ledger, session).assess(claim, {}))
    print(ledger.accounting()["proof"])
```

The valid example's validation is `passed`; the reopened ledger reuses the
completion, with one spent `proof` unit and nothing reserved.

API summary:

- `Proofs(ledger, session, bundle, *, target, seconds=120)` captures the host
  policy (bundle bytes, challenge, configuration, identity) once as ledger
  evidence. `proofs.target` is the captured challenge artifact.
- `check(operation_id, solution)` returns the operation `Request`, not a success
  flag. `request(operation_id, solution)` builds the same request without
  dispatching, for capturing a claim reference beforehand.
- Read status through `Claims.assess()` (validation field `"proof"`) or
  `proof_status(ledger, request, proofs.target)`, which returns `passed`,
  `rejected`, `unproved`, `infrastructure_failure`, `unsupported`, `unknown`, or
  `missing`.

### Budget and costs

`check()` reserves one synthetic `proof` unit before verification. It covers one
bounded compilation, export, and kernel replay. Every attributable completion
spends that unit, including rejections, unproved attempts, timeouts, and
infrastructure failures. Measured elapsed nanoseconds are recorded separately
from these units. Proof generation and model costs belong to the worker's own
attempts ([model adapters](model-adapters.md)) and are not included.

### Identity binding

A request binds the solution's captured origin and bytes, target ID, challenge and
definitions, exact theorem configuration, bundle bytes, image, tool manifest,
policy digest, actual limits, project context, host kernel release, Python
version, and Podman executable digest. The completion records Podman's reported
version as execution evidence. Bundle bytes are captured once, so later edits to
the file cannot change an attempt. A changed source, target, policy, environment,
or limit cannot reuse an operation ID, and lookup validates all captured input
and completion bytes before reuse.

The receipt stores `verification.json` (format version 2) beside the original
source, bundle, challenge, selected configuration, diagnostics, decision, and
exports. It binds the complete request, including operation ID and project
context. Receipts are assessed only for their exact challenge artifact.
Historical assessment reads stored evidence and needs neither installed tools,
the original bundle file, nor the current target registry. There is no reader for
earlier receipt formats; use a fresh bundle and run directory.

### Unknown and unsupported attempts

| Situation | Result |
| --- | --- |
| Host dies before a completion is recorded | `unknown`; the unit stays reserved |
| Verifier raises, including uncertain cleanup | `unknown`; the unit stays reserved |
| Result is malformed, forged, or mismatched with the request | Raw evidence retained; `unsupported`; operation stays unknown and reserved |
| Repeating either kind of unresolved attempt | No new execution |
| Any unresolved operation | Blocks new dispatch through the host recovery check |

Unsupported evidence cannot release a reservation or establish success. There is
no imported-receipt reconciliation API; an unresolved attempt stays blocked.

### Claims and applications

`Claims.assess()` keeps validation separate from version applicability: `CURRENT`
establishes current versions, not the truth of an assumption, and parent edges do
not infer truth from another claim. It preserves `rejected`, `unproved`, and
`infrastructure_failure` as distinct outcomes alongside ordinary boolean checker
fields.

An application of a theorem is supported only by passing, current evidence for the
theorem, every application premise (a valid input domain), and a correspondence
check showing the exact candidate's output matches the formal model. Missing,
failed, uncertain, rejected, or unproved premises cannot establish support. When a
premise changes or loses support, the application is blocked while the
conditional theorem and its historical receipt stay valid. The
[CSV fixture](../fixtures/csv-transformation.md#m4-uniqueness-application) and
[configuration migration fixture](../fixtures/config-migration.md) implement these
checks for development inputs.

## Test coverage

| Area | Tests | What is covered |
| --- | --- | --- |
| Input and policy validation (fast) | `tests/test_proofs.py` | Unknown target, invalid source, timeout ceiling, changed bundle pins, runtime preflight timeout |
| Native rejection and containment | `tests/test_proofs_native.py` | Valid proof with exact axioms; direct and indirect `sorry`; custom and native-evaluation axioms; changed definitions; extra hypotheses; incomplete proofs; forged success output; corrupt `.olean`; unchecked terms rejected by kernel replay; writes to challenge, configuration, decision, and verifier; secret exclusion; resource limits; excessive output; timeout cleanup |
| Durable receipts (fast, scripted verifier) | `tests/test_proof_receipts.py` | Cross-target and copied receipts; outcomes and costs across restart; forged identity; changed request, context, or bundle file; corrupt raw exports; budget exhaustion; unknown attempts with forged success; host kills after reservation, dispatch, execution, and completion; historical claims without tools; runtime unavailable vs. uncertain cleanup |

Every native case also records a durable receipt, reopens the ledger, and resumes
twice without another execution or charge; reports retain exact request
identities and an execution witness kept outside the ledger. Host-death cases run
each recovery in a fresh process and count executions outside the ledger.

## Limits

- These development cases establish the tested local boundary, not resistance to
  every Lean, Comparator, Landrun, kernel, or container-runtime vulnerability.
- A proved target establishes only its statement. It says nothing about record
  preservation, timestamp semantics, the host parser, or other task obligations.
- Not provided: a second proof kernel, imported-receipt reconciliation, readers for
  earlier receipt formats, model proof-search trials, or broad performance
  comparisons with executable checks.
