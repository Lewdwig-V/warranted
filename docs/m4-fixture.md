# M4: applying the uniqueness theorem

The demonstration applies one checked theorem to three existing M2 candidates.
All three applications have supported premises and match the formal model.
Only the correct candidate passes task acceptance.
A separate experiment changes the input to contain duplicate identifiers.
The old application becomes stale, and the new application lacks a required premise.
The conditional theorem remains valid.

## Run and resume

Build the [pinned proof bundle](m4-verification.md#run-the-native-checks) first.
Use a new destination:

```bash
uv run --locked python examples/m4/demo.py start runs/m4-demo --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m4/demo.py resume runs/m4-demo --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m4/demo.py resume runs/m4-demo --bundle runs/m4-tools/bundle.json
```

`start` captures one valid proof, one incomplete proof, and one five-second timeout.
The incomplete proof and timeout remain unproved, with their costs retained.
It commits the proof checkpoint before any application or task check.
Add `--crash` to kill that host after it writes the report.
The native test uses this option, then resumes in two fresh processes.

The first `resume` performs eight executable checks and records the applications and task decisions.
The second `resume` performs no new verification or checker executions.
Each command writes a report under `reports/` and copied evidence under `exports/`.
The proof and executable-check execution witnesses remain outside the ledger.
CI retains these files for 14 days.

The demonstration uses fixed source proposals without model requests.
Lean elaboration and tactics run inside the existing proof containment boundary.
The host verifier is wrapped only to record an independent execution witness.
That wrapper always calls the same pinned verifier.
There is no configurable replacement checker in the CLI.

## What is established

The [owner-pinned fixture intent](../examples/m4/intent.json) selects source rows and the identity function.
This function returns each identifier unchanged, so it is injective: distinct inputs have distinct outputs.
A host check establishes uniqueness of the selected identifiers.
Another check compares the candidate's identifier sequence with the mapped selection.
These checks use exact string equality, matching the Lean target.

| Candidate | Selected rows | Input unique | Correspondence | Proof application | Task acceptance |
| --- | --- | --- | --- | --- | --- |
| Correct | All three | Pass | Pass | Supported | Accepted |
| Dropped row | First two | Pass | Pass | Supported | Rejected |
| Empty | None | Pass | Pass | Supported | Rejected |

The dropped-row and empty candidates both fail record preservation and daily totals.
Their uniqueness and timestamp checks pass.
All four gates use the unchanged M2 evaluator and acceptance policy.
The existing source-offset rule exception remains scoped to each candidate.
A proof receipt cannot replace the evaluator receipt or waive a failed gate.

An application records the theorem, candidate, source, selection, mapping, and checked premises.
Its support report requires passing validation and current versions for every required claim.
A current but rejected, unproved, unknown, or unsupported premise cannot establish support.
The host also checks each premise's exact candidate, checker request, and result field.
A passing result for another field cannot replace a failed uniqueness premise.

## Changed premise

After the initial applications, the host commits the transition approved in `intent.json`.
The separate premise experiment then uses [input-duplicates.csv](../examples/m4/input-duplicates.csv).
Its selected identifiers are `r1`, `r1`, and `r3`.
The corresponding candidate has that same sequence, so correspondence still passes.
The identity function remains injective, but the input uniqueness premise fails.

The original application's evidence remains unchanged and becomes stale under the revised source.
A new application records the failed premise and reports `unsupported`.
Both reports retain a passing, current conditional theorem.
This revision affects the premise experiment only.
The matrix's acceptance decisions still describe the original M2 input and contract.
The demonstration does not claim task acceptance for the duplicate-input case.

## Accounting and interpretation

The cap is three synthetic proof units and eight synthetic executable-work units.
These are attempt counts, not money or measured CPU use.

| Stage | Proof executions | Executable checks | Spent proof units | Spent executable units | Reserved units |
| --- | --- | --- | --- | --- | --- |
| Start | 3 | 0 | 3 | 0 | 0 |
| First resume | 3 | 8 | 3 | 8 | 0 |
| Further resume | 3 | 8 | 3 | 8 | 0 |

The executable baseline comprises three candidate evaluations and one source-style check.
The proof-assisted path retains all four, then adds four premise/correspondence checks.
It also adds the proof attempts and tool-bundle setup.
This demonstration does not replace executable work with Lean or establish a speed benefit.

One local run on Python 3.12.12, Podman 6.1.2, and the pinned Lean 4.34.0 bundle measured:

| Work | Elapsed time |
| --- | --- |
| Earlier cached bundle build | 47.127 s |
| First valid verification | 12.168 s |
| Incomplete proof attempt | 7.860 s |
| Five-second timeout, including setup and cleanup | 10.061 s |
| Three cached proof calls on the second resume | 0.112 s |
| Three task evaluations and the source-style check, combined | 2.940 ms |
| Four premise/correspondence checks, combined | 1.130 ms |
| Complete second resume, through evidence export | 0.647 s |

These are observations from one development run, without model costs.
The executable timings exclude ledger publication and acceptance bookkeeping.
The cached build measurement excludes an initial download and does not represent cold setup cost.
The fixture establishes reuse and retained costs; it does not establish that Lean earns its added cost on this small task.

Reports separate these measurements:

- `proof_bundle_build_elapsed_ns` records the bundle's earlier build time.
- `proof_attempts` records each verifier outcome and elapsed time, including failed attempts.
- `proof_stage_elapsed_ns` measures the three proof calls or their cached reuse in that process.
- `elapsed_ns_by_kind` separates verification, premise checks, task checks, and acceptance bookkeeping.
- `host_elapsed_ns` measures the current invocation through evidence export, before the final report write.

Cached reports retain historical verification costs.
An interrupted or unattributable attempt keeps its reservation and blocks new dispatch.
A changed fixture, host, environment, or tool bundle fails before cached reuse.

The parser, identity mapping, and correspondence check form a trusted connection to the formal model.
Lean does not prove that Python code correct, nor does it establish record preservation or timestamp semantics.
These are development cases, not held-out evaluation or evidence of model proof-search quality.
The native rejection and containment cases remain in the [verification suite](m4-verification.md#evidence-and-limits).
The next milestone introduces the second task family and broader comparisons.
