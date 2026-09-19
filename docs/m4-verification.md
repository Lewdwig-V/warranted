# M4 verification boundary

This implements PR 1 of the [M4 plan](m4-proof-boundary.md).
The host can check captured Lean source against the fixed uniqueness theorem.
Durable receipts, proof budgets across restarts, claims, and fixture applications remain planned.
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
uv run --locked pytest -q -m proof tests/test_proofs_native.py
```

The build creates an image and a host-owned `bundle.json`.
Keep that file outside worker storage.
It records the image ID, executable digests, tool closure digest, source pins, policy, and build time.
A tool closure digest identifies every file under `/opt` in the image.
The image ID also identifies the base system and its libraries.
Each verification report includes this bundle, source identity, limits, runtime version, and elapsed time.
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

The limits are one CPU, 1 GiB memory, 64 processes, and 120 seconds for verification.
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
This API does not reconcile interrupted operations. PR 2 will retain their reservations and distinguish unknown outcomes.

## Evidence and limits

Native cases cover direct and indirect `sorry`, custom and native-evaluation axioms, changed definitions, and extra hypotheses.
They also cover incomplete proofs, forged output, malformed compiled artifacts, and unchecked proof terms rejected during kernel replay.
Containment cases attempt writes to the challenge, configuration, decision, and verifier executable.
They exercise secret exclusion, resource limits, excessive output, and timeout cleanup.
All 19 existing M3 containment tests pass after sharing the bounded Podman transport.

The host, tool builder, pinned verifier, Lean kernel, Landrun, operating system, and container runtime remain trusted.
A second proof kernel is not used.
These development cases establish the tested local boundary. They do not establish resistance to every implementation vulnerability.
The [Lean proof-validation guide](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) explains the underlying trust assumptions.
M4 still needs durable proof receipts, supported fixture applications, recovery, and comparisons with the executable baseline.
