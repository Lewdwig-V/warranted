# M4 verification boundary

This implements the verification and receipt slices of the [M4 plan](m4-proof-boundary.md).
The host can check captured Lean source against the fixed uniqueness theorem.
The ledger retains exact proof receipts, budgets, and claim outcomes across restarts.
The [fixture demonstration](m4-fixture.md) adds supported applications and changed-premise recovery.
The result cannot authorize task acceptance.

## Run the native checks

Use Linux x86-64 with rootless Podman, cgroup v2, seccomp, and Landlock ABI 3 or newer.
The host also needs `zstd` for the Lean release archive.
The build downloads trusted tools and dependencies. Verification has no network access.

```bash
uv sync --locked
uv run --locked python scripts/build_proof_bundle.py runs/m4-tools
WARRANTED_PROOF_TESTS=1 \
WARRANTED_PROOF_BUNDLE=runs/m4-tools/bundle.json \
uv run --locked pytest -q -m proof tests/test_proofs_native.py tests/test_m4_fixture.py
```

The build creates an image and a host-owned `bundle.json`.
Keep that file outside worker storage.
It records the image ID, executable digests, tool closure digest, source pins, policy, and build time.
A tool closure digest identifies every file under `/opt` in the image.
The image ID also identifies the base system and its libraries.
Each verification report includes this bundle, captured source bytes, limits, runtime version, and elapsed time.
Native tests write reports under `runs/m4-native`.
The separate `proof` CI job runs these checks and retains their reports for 14 days.

The ordinary suite skips native proof tests unless explicitly enabled.
An enabled native test fails when isolation or the tool image is unavailable.
The build needs several gigabytes of disk space.
Download time and cached build time vary. They do not measure proof reuse.

## Host API

The [example](../examples/m4/Solution.lean) proves the reviewed target.
Run it through the verifier:

```python
import json
from pathlib import Path

from warranted.proofs import verify

source = Path("examples/m4/Solution.lean").read_bytes()
result = verify(source, Path("runs/m4-tools/bundle.json"))
Path("runs/m4-control.json").write_text(json.dumps(result.as_dict(), indent=2))
print(result.status, result.axioms)
```

The expected status is `proved`, with `Quot.sound` and `propext` as the actual axioms.
The theorem states that an injective mapping preserves uniqueness in a list of identifiers.
It says nothing about preserving records or satisfying the other fixture obligations.

`verify()` accepts bounded immutable bytes from the trusted host.
The host must stop a producing worker before capturing those bytes.
This slice tests fixed source proposals. It does not add a model-driven proof worker.
Workers cannot select a target, build configuration, axiom policy, or image.

## Pinned tools and attribution

The [toolchain manifest](../src/warranted/proof/toolchain.json) pins release and source archive digests.
It selects Lean 4.34.0, matching Comparator and lean4export revisions, Landrun, and Go 1.26.1.
The base Python image has an immutable registry digest.
Landrun's pinned `go.mod` and `go.sum` identify its build dependencies.
The build checks those modules and uses a local, checked exporter archive.
It never resolves Comparator's upstream `master` dependency.

[Lean FRO's Comparator](https://github.com/leanprover/comparator) supplies comparison, axiom traversal, export parsing, and kernel replay.
Warranted replaces its CLI entry point with [a small driver](../src/warranted/proof/Verify.lean).
The driver preserves the upstream checking functions and writes an attributable decision separately from compiler output.
It also captures the actual axiom set and both serialized exports.
The built verifier and exporter use the same pinned Lean distribution.
The challenge imports `Init`. Candidate metaprograms can use the bundled Lean distribution, but cannot add external libraries.

The trusted challenge contains a `sorry` placeholder for the required theorem, as Comparator expects.
The solution must supply the same theorem and supporting definitions without forbidden axioms.
There are no definition holes, which permit a candidate to choose part of a specification.
The permitted axiom set is exactly `propext` and `Quot.sound`.

## Containment and outcomes

The host starts a fresh rootless container for each attempt.
It copies only source bytes into that container and uses no host filesystem mounts.
The image, trusted configuration, challenge, and source remain outside the compiler's writable paths.
Compilation and export run under Landrun as UID 1000 without effective capabilities.
Comparator runs outside that Landrun domain and reads serialized exports.
The host never imports a generated `.olean` file, Lean's compiled artifact format.

[Comparator's documented Unix-socket restriction](https://github.com/leanprover/comparator#readme) is enforced by an inherited seccomp filter.
The filter denies Unix socket creation, socket pairs, `ptrace`, and writes through `process_vm_writev`.
It supplements Podman's default filter.
A native probe requires Landlock support, denied writes to the decision file, and denied Unix sockets before each attempt.
Missing controls produce an infrastructure failure.

The limits are one CPU, 1 GiB memory, 64 processes, and at most 120 seconds for verification.
The host can select a smaller timeout with `verify(..., seconds=5)`.
The result records that exact limit. The worker cannot increase the 120-second ceiling.
The native timeout test uses five seconds and requires compilation to start before the timeout.
The container also has a 180-second lifetime limit.
The source limit is 1 MiB, each export limit is 8 MiB, and captured console output is at most 2 MiB.
The workspace is 64 MiB, with a separate 8 MiB temporary directory.
The host stops remaining processes before capturing bounded regular files and removes the container before returning a known result.
Cleanup failure raises an error because stopped execution is not established.

| Result | Meaning |
| --- | --- |
| `proved` | Exact comparison, axiom enforcement, and kernel replay pass. |
| `rejected` | The verifier rejects the exported target, definitions, axioms, or proof. |
| `unproved` | Compilation or export fails, or the attempt reaches a time/output limit. |
| `unsupported` | The host lacks a complete, well-formed verifier decision. |
| `infrastructure_failure` | Required tools, isolation, or trusted execution fail. |

Compiler output is diagnostic evidence. A printed success message cannot decide the result.
The protected decision file and supervisor exit status supply that decision.
An unsuccessful attempt does not prove the target false.
The direct `verify()` API does not persist operations.
Use the durable host adapter below to retain reservations across interruption.

## Durable host API

`Proofs` uses the existing ledger, with one trusted writer outside worker storage.
It reserves one synthetic `proof` unit before verification starts.
That unit covers one bounded compilation, export, and kernel replay attempt.
Each attributable completion spends one unit, including rejection, unfinished proofs, timeouts, and infrastructure failures.
Measured elapsed nanoseconds remain separate from these units.
Proof generation and model costs belong to their M3 attempts and are not included here.

After building the bundle, run this example once with a new ledger destination:

```python
from pathlib import Path

from warranted.acceptance import Evidence
from warranted.claims import Claims
from warranted.ledger import Ledger, Manifest, Snapshot
from warranted.proof_receipts import Proofs

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
    proofs = Proofs(ledger, session, bundle)
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
    proofs = Proofs(ledger, session, bundle)
    assert proofs.check("uniqueness-1", solution) == request
    print(Claims(ledger, session).assess(claim, {}))
    print(ledger.accounting()["proof"])
```

The valid example reports `passed`, then reuses the completion with one spent unit and no reservation.
`check()` returns the operation request, not a success flag.
Read the proof status through `Claims.assess()` or `proof_status(ledger, request, proofs.target)`.
Use `Proofs.request()` to capture a claim reference before dispatch when needed.

Requests bind the solution's captured origin and bytes, challenge and definitions, tool bundle, image, axiom policy, and actual limits.
They also bind the project context, host kernel and Python versions, and Podman executable digest.
The completion records Podman's reported version as execution evidence.
The host captures bundle bytes once, so later edits to its file cannot change the attempt.
A changed source, policy, environment, or limit cannot reuse the same operation ID.
Lookup validates all captured input and completion bytes before reuse.

The receipt stores `verification.json` beside the original source, bundle, challenge, diagnostics, decision, and any exports.
It binds the complete request, including the operation ID and project context.
`Claims.assess()` accepts a proof receipt only for its exact challenge artifact.
It preserves `rejected`, `unproved`, and `infrastructure_failure` as distinct outcomes.
Historical assessment needs neither installed tools nor the original bundle file.
It checks stored evidence without running verification.

A missing completion leaves the operation unknown and reserved.
An unsupported or mismatched result is retained as raw evidence, reports `unsupported`, and also leaves the operation unknown and reserved.
It cannot release the reservation or establish proof success.
Repeating either kind of unknown attempt does not execute it again.
Other unknown operations block new dispatch through the existing host recovery check.
There is no imported-receipt reconciliation API in this slice; an unresolved attempt stays blocked.

Claim validation still describes the conditional theorem only.
Version applicability does not establish an empirical premise, correspondence with a Python implementation, or task acceptance.
The [fixture demonstration](m4-fixture.md) supplies those independent checks.

## Evidence and limits

Native cases cover direct and indirect `sorry`, custom and native-evaluation axioms, changed definitions, and extra hypotheses.
They also cover incomplete proofs, forged output, malformed compiled artifacts, and unchecked proof terms rejected during kernel replay.
Containment cases attempt writes to the challenge, configuration, decision, and verifier executable.
They exercise secret exclusion, resource limits, excessive output, and timeout cleanup.
Every native proof case also records a durable receipt, reopens the ledger, and resumes twice without another execution or charge.
Reports retain exact request identities and a separate execution witness.
Fast tests use a scripted verifier for host deaths after reservation, dispatch, execution, and completion.
Each recovery runs in a fresh process and counts execution outside the ledger.
Other cases cover wrong-target and copied receipts, corrupt bytes, changed identities, exhausted budgets, and unsupported evidence that cannot settle an unknown attempt.

The host, tool builder, pinned verifier, Lean kernel, Landrun, operating system, and container runtime remain trusted.
A second proof kernel is not used.
These development cases establish the tested local boundary. They do not establish resistance to every implementation vulnerability.
The [Lean proof-validation guide](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) explains the underlying trust assumptions.
The fixture demonstration completes M4 for local fixed proposals and reports its cost alongside the executable baseline.
Broader performance comparisons and model proof-search trials remain planned.
