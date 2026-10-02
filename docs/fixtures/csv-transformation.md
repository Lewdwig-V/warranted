# CSV transformation fixture

The first task family is a small local CSV transformation: preserve source rows,
convert timestamps to UTC, and sum values by UTC date, under a versioned source
contract that later changes. It exercises durable operations and restart (M1
walkthrough), independent acceptance gates and revisions (M2 experiments), a
contained worker across a changed premise (M3 demonstration), and a Lean
uniqueness theorem applied without weakening task acceptance (M4 application).
All runs are local, deterministic development cases with scripted or fixed
candidates; none measures model quality, held-out performance, or speed.

## Task family

The input holds identifiers, timestamps, and integer values, with versioned
source documentation. The objective is a normalised dataset, a daily aggregate,
and an executable transformation with independently checked outputs. Everything
is ordinary CSV/JSON files; no external data service is involved.

The source contract states a timestamp interpretation and an identifier
uniqueness assumption. A later approved revision changes one of them (the offset
convention, the identifier equality definition, or, in the premise experiment,
duplicate identifiers). A run must revisit affected results, preserve unaffected
work, and finish under the current requirements after a fresh-process restart.
Revisions are delivered at a predeclared logical checkpoint, not a wall-clock
time, and the host persists and exposes them; a dependency graph is not expected
to detect unseen changes.

The independent evaluator owns the current requirements, reference examples, and
private acceptance cases. Candidate code and generated tests cannot replace it.
Published feedback stays distinct from private grading inputs, and the fixture,
contract, and evaluator are versioned.

| Directory | Contents |
| --- | --- |
| [`examples/m1`](../../examples/m1) | Fixed input with explicit offsets, expected outputs, trusted transform script, walkthrough |
| [`examples/m2`](../../examples/m2) | [Contract v3](../../examples/m2/fixture/contract.md), input without offsets, `versions.json` revisions, candidates, references, owner intent, experiments |
| [`examples/m3`](../../examples/m3) | Worker task, fake model service and command, demo |
| [`examples/m4`](../../examples/m4) | Reviewed `Solution.lean`, owner `intent.json`, `input-duplicates.csv`, demo |

## M1 scripted walkthrough

A trusted script transforms the fixed input once; a second process recovers the
same result without re-executing it. The run covers persistence, operation
recovery, accounting, and permitted exports. The history below defines
observable facts, not mandatory tool calls or event types.

```bash
uv run --locked python examples/m1/walkthrough.py start runs/m1
uv run --locked python examples/m1/walkthrough.py resume runs/m1
```

Use a new run directory for `start`. See the
[README](../../README.md#run-the-m1-walkthrough) for the run-directory layout and
export inspection.

### Fixed input and expected outputs

`examples/m1/fixture/input.csv`:

```csv
id,timestamp,value
r1,2026-01-01T00:30:00+01:00,7
r2,2026-01-01T02:00:00+01:00,11
r3,2026-01-02T00:15:00+01:00,5
```

The contract requires preserving every row, its order, identifier, and integer
value; converting each explicit offset to UTC; and summing values by UTC date.
Expected normalized CSV:

```csv
id,timestamp,value
r1,2025-12-31T23:30:00Z,7
r2,2026-01-01T01:00:00Z,11
r3,2026-01-01T23:15:00Z,5
```

Expected totals: `{"2025-12-31": 7, "2026-01-01": 16}`.

Expected outputs are stored independently of the transformation code. The checks
compare normalized rows and parsed totals, while the exact raw output bytes are
preserved. These checks establish this fixture's output behavior only; they are
not acceptance gates and do not prove the transformation correct for other inputs.

### Identity and recorded context

An operation ID names one requested execution within a project, bound to the
operation kind, input bytes, transformation version, source contract, and
relevant environment and context. The identity is compared before returning a
stored result or dispatching work. A new session does not change it, and equal
output bytes do not make different requests identical.

The run records one root world (fixture version, run identity, initial limits,
environment, parent or explicit absence, session boundaries); it does not execute
branches or replay histories (see
[Dream-RSI](../design.md#dream-rsi-exploration-and-replay)). Each result records
the script version and input references. The host captures raw stdout, stderr,
exit status, and output files; agent-authored text cannot supply the origin of an
authoritative result.

### Expected history

The project limit is five synthetic work units. The walkthrough reserves three
before dispatch and reports two units of actual usage. These numbers test
accounting; they are not tokens, time, or money. Elapsed time is recorded
separately.

| Step | Observable history | Spent / reserved / available |
| --- | --- | --- |
| Create | Project, session, manifest, source contract, input snapshot | 0 / 0 / 5 |
| Reserve | `transform-1` with exact request identity and reservation, before execution | 0 / 3 / 2 |
| Execute | Script runs once; raw results and output files captured outside authoritative storage | 0 / 3 / 2 |
| Complete | Artifact bytes published; result references, outcome, and accounting committed together | 2 / 0 / 3 |
| Reopen | Fresh process and session recover the same history and bytes | 2 / 0 / 3 |
| Repeat | Same identity returns the recorded outcome without execution or charge | 2 / 0 / 3 |
| Inspect | Permitted export traces output bytes to operation, input, and contract | 2 / 0 / 3 |

Completion releases the whole reservation and adds actual usage once. Execution
count stays one across completion and reopening. Both `start` and `resume` report
three passing output checks, one execution, and these totals; the script exits
nonzero otherwise.

### Interruption and negative cases

The required behavior at each interruption and misuse point (exercised through
the ledger tests and [`tests/test_walkthrough.py`](../../tests/test_walkthrough.py)):

| Case | Required result |
| --- | --- |
| Interrupt before the reservation commits | No execution, partial operation, or charge; five units available |
| Interrupt after reservation, before a committed result | Reservation survives; reopening dispatches nothing; a known unstarted operation stays pending, uncertain execution stays unknown; repeating does not dispatch or reserve again |
| Interrupt during artifact publication | No completion references a partial or missing artifact; unreferenced files supply no evidence; reservation survives |
| Interrupt during the completion transaction | Either the unresolved reservation or the complete settled outcome, never a partial accounting update |
| Completion committed, response lost | Repeat returns the committed result; one execution; 2 spent, 0 reserved |
| Repeated completion receipt | Identical receipt changes nothing; conflicting outcome, artifact, or usage is rejected |
| Operation ID reused with changed identity | Changed input, transformation version, contract, or context fails before execution or cached success, even with identical output |
| New observation or artifact version | Retains its origin; old record and bytes stay unchanged; replacement through the writer fails |
| Referenced artifact removed or altered | Reported as missing or corrupt; never silently regenerated |
| Reservation beyond the allowance | Fails before execution; an unresolved three-unit reservation blocks another under the five-unit cap across sessions and IDs |
| Usage above reservation or cap | Full usage retained, breach recorded, further dispatch blocked |
| Script fails with a known outcome | Nonzero exit, diagnostics, and usage preserved; reservation settled once; repeat returns the failure |

Artifact publication precedes the transaction that references it, so a crash can
leave an unreferenced file; it establishes nothing and is not cleaned up
automatically. The walkthrough tests count executions outside the ledger and cover
a nonzero exit, wrong output with exit zero, changed input or evaluator files, and
a host killed after execution (unknown, reserved, not retried). The successful
resume is required alongside these failures.

### Scope

Single trusted writer, process interruption on a local filesystem. Not covered:
power or disk loss, hostile changes to host-owned state (missing or corrupt
evidence is detected), concurrent writers, untrusted workers. Expected-output
records and host code are separate snapshots, excluded from the candidate
directory and exports. Inspection consumers get only permitted exports; editing
an export leaves history unchanged. CI runs both commands and retains exports,
reports, and the execution counter as `m1-walkthrough` for 14 days. No model
credentials or external services are needed.

## M2 fixture experiments

The [contract](../../examples/m2/fixture/contract.md) (version 3, owner
`fixture-owner`) uses the same IDs, values, and local clock times as M1, but the
timestamps have no embedded offset. The offset is versioned separately in
`versions.json`: `offset-v1` is +01:00 and `offset-v2` corrects it to +00:00.
`definition-v1` compares identifiers exactly; `definition-v2` ignores ASCII case.
Two annotation versions affect no calculation. Every candidate must satisfy four
independent gates: `unique_ids`, `preserved_rows`, `utc_timestamps`, and
`daily_totals`. Acceptance goes through the
[shared acceptance boundary](../reference/claims-and-acceptance.md).

```bash
uv run --locked python examples/m2/experiments.py start runs/m2
uv run --locked python examples/m2/experiments.py resume runs/m2
```

`start` records claims, results, and decisions under the initial contract and
commits the approved revision at the first submission checkpoint. `resume` reads
that revision in a fresh process before assessing acceptance. Each of the five
conditions (`matrix`, `unchanged`, `annotation`, `offset`, `definition`) has its
own ledger and a 20-unit cap. The host pins the complete fixture, including future
revision files, in one immutable manifest; the script does not hide future data
from a worker, model replay worlds, or run a worker.

### Selective rebuilding

The unchanged restart and the annotation edit reuse all three transformation
stages and the existing candidate check. The offset correction (+01:00 to +00:00)
reuses source facts but repeats normalization, aggregation, and affected checks.
It first blocks the old receipt as stale, then rejects the old candidate under the
new interpretation. The rebuilt candidate passes with daily totals of 18 on
2026-01-01 and 5 on 2026-01-02.

Dependencies are explicit. Source facts describe the input; each candidate check
binds the exact candidate, source, offset, definition, and reference. The project
pins the host, contract, and environment, and every decision binds the current
revision and annotation. Reusing source facts never transplants a check between
candidates. [Claims](../reference/claims-and-acceptance.md) propagate staleness
through declared parents: the offset condition keeps passing historical
validation while marking dependent claims stale, and rebuilding yields current
claims and a new decision.

### Changed definitions with identical output

The owner changes identifier equality from exact to ASCII case-insensitive. The
main candidate bytes are identical and satisfy both versions, but the old receipt
cannot authorize the new decision. A separate two-ID witness (`rA`, `ra`) passes
under version 1 and fails under version 2, detecting a checker that changes its
label but keeps the old meaning; it does not replace the main obligations. All
four candidate claims bind the shared evaluator receipt and become stale together;
transformation claims stay current.

### Independent failures

Literal candidates and references; each check keeps its own result, and a passing
narrow check after restart cannot erase another check's failure.

| Candidate | Unique IDs | Preserved rows | UTC timestamps | Daily totals | Decision |
| --- | --- | --- | --- | --- | --- |
| Correct | Pass | Pass | Pass | Pass | Accept |
| Wrong offset | Pass | Pass | Fail | Fail | Reject |
| Dropped row | Pass | Fail | Pass | Fail | Reject |
| Dropped row and wrong offset | Pass | Fail | Fail | Fail | Reject |
| Empty | Pass | Fail | Pass | Fail | Reject |
| Swapped timestamps on the same UTC date | Pass | Pass | Fail | Pass | Reject |

All four obligations are gates. A separate rule prefers timestamps with embedded
offsets; the owner supplies an exception because the offset is documented
separately, and the host records it per exact target and revision. An exception
never waives a gate. The timestamp gate checks each emitted row, so empty output
passes it without establishing preservation. The totals gate independently sums
emitted rows and compares both those sums and the reported totals with the
reference.

### Evidence and limits

Expected charged operations after one `start` and one `resume`:

| Condition | Initial | New after restart | Total |
| --- | --- | --- | --- |
| `matrix` | 7 | 1 | 8 |
| `unchanged` | 5 | 0 | 5 |
| `annotation` | 5 | 0 | 5 |
| `offset` | 5 | 4 | 9 |
| `definition` | 6 | 2 | 8 |

One operation costs one synthetic unit; elapsed nanoseconds are recorded
separately. The offset total includes the check that rejects the old candidate.
Each condition adds one source-style check and reuses it after restart. Decisions
and exceptions have empty usage; claim capture and support reporting are uncharged
bookkeeping. Decisions are recorded before and after the revision, so both remain
in history. A further `resume` repeats no charged operations, reuses identical
decision and exception completions, and reports a new session ID. These counts
describe scripted work, not worker computation or savings.

Each condition keeps a ledger, an independent execution log
(`executions.jsonl`), JSON reports, and evidence exports. CI keeps logs, reports,
and exports as `m2-experiments` for 14 days. Exports include public references and
revisions but exclude host, acceptance, and claims source snapshots and the
authoritative database.

[`tests/test_m2_fixture.py`](../../tests/test_m2_fixture.py) covers the matrix,
selective restart counts, changed fixture files, missing, forged, wrong-target,
and unfinished receipts, unapproved revisions, reassessing a rejected candidate
after an approved revision (fresh check required, old failure kept), and process
termination. A host killed after a committed revision recovers it without repeating
work; one killed before an operation completion keeps an unknown operation and
its reservation and never retries it. A run without a committed checkpoint fails
explicitly. A changed host or contract requires a new run directory.

Owner approvals are checked against pinned approvals in the fixture; remote owner
authentication and external-effect authorization are not provided.

## M3 changed-premise demonstration

[`examples/m3/demo.py`](../../examples/m3/demo.py) runs M2's offset revision with
a real worker: mini-swe-agent's loop and LangGraph, the rootless Podman shell
adapter, and a fake model in a separate HTTP process that returns a pinned
transformation program (see [worker and containment](../reference/worker-and-containment.md)
and [model adapters](../reference/model-adapters.md)). It reuses M2's private
evaluator, four gates, scoped rule exception, claims, and approved revision
protocol unchanged. This tests integration and recovery, not learned reasoning.

The fixed serial policy runs one initial episode and one fresh episode after the
+00:00 revision, each with at most two model steps. Project caps are 4 model, 4
tool, and 20 checker units across restarts. Each workspace receives only the
current offset, source CSV, and public task instructions; the contract, future
versions, and literal answers stay private.

After pulling the pinned image, run:

```bash
podman pull "$(uv run --locked python -c 'from warranted.sandbox import IMAGE; print(IMAGE)')"
uv run --locked python examples/m3/demo.py start runs/m3 --crash
# The expected SIGKILL exit status is 137 in a shell.
uv run --locked python examples/m3/demo.py resume runs/m3
uv run --locked python examples/m3/demo.py resume runs/m3
```

The initial candidate passes under +01:00. `--crash` kills the host after durable
reporting and resource cleanup. The first resume rejects the old receipt as stale,
then checks the old candidate against the new reference and retains its two
failures (`utc_timestamps`, `daily_totals`). The new candidate must pass all four
gates; the failed obligations stay in the ledger after acceptance.

| Stage | Model requests / spent | Tool dispatches / spent | Checker executions / spent | Reserved |
| --- | --- | --- | --- | --- |
| Initial checkpoint | 1 / 1 | 1 / 1 | 2 / 2 | 0 |
| Revised acceptance | 2 / 2 | 2 / 2 | 4 / 4 | 0 |
| Repeated resume | 2 / 2 | 2 / 2 | 4 / 4 | 0 |

Model request counts come from the separate service; dispatch and checker logs
live outside both databases and the worker. A dispatch count does not prove that
an uncertain shell effect happened. Reports retain package, model, policy,
evaluator, task, and environment versions, costs, obligations, host elapsed time,
and new-operation elapsed time; these are local measurements, not speed or
quality claims. The `containment` CI job runs these commands and retains reports,
evidence, and request logs as `m3-recovery`.
[`tests/test_m3_demo.py`](../../tests/test_m3_demo.py) (requires
`WARRANTED_CONTAINER_TESTS=1`) also checks that a changed fixture is rejected
before any new dispatch and that the first episode's inputs exclude the future
offset.

### Crash boundaries

| Boundary | Required observation | Test |
| --- | --- | --- |
| Reserved, before dispatch | Same pending slot executes once | `test_worker.py` |
| Dispatch marker, before observed effect | Unknown and reserved, no retry | `test_worker.py` |
| External response lost | Exact receipt settles once or remains unknown | `test_attempts.py` |
| Repeated death during reconciliation | One POST, reservation retained until settled | `test_attempts.py` |
| Shell submission, before capture | Unknown, no candidate acceptance | `test_sandbox.py` |
| Candidate captured, before ledger commit | Unknown, no duplicate shell dispatch | `test_sandbox.py` |
| Candidate receipt committed | Exact bytes reused after container removal | `test_sandbox.py` |
| Episode receipt before graph checkpoint | Completed attempts or episode receipt reused | `test_worker.py` |
| Graph checkpoint committed or deleted | No additional execution or charge | `test_worker.py` |
| Graph claims a missing host receipt | Blocked, success not inferred | `test_worker.py` |
| Approved revision, then host kill | Old checks stale; new candidate passes current gates | `test_m3_demo.py` |
| Acceptance interrupted before/after commit | Unknown, or committed receipt reused | `test_acceptance.py` |

Native [LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
can re-execute downstream calls; it is not Warranted replay, which may reveal only
recorded outcomes. Container isolation assumes a trusted kernel, runtime,
controller, and host filesystem.

## M4 uniqueness application

[`examples/m4/demo.py`](../../examples/m4/demo.py) applies the
[`uniqueness` theorem](../reference/proof-verification.md#targets-and-axiom-policy)
(an injective mapping preserves identifier uniqueness) to three M2 candidates.
All three applications are supported; only the correct candidate passes task
acceptance. A separate experiment introduces duplicate identifiers: the old
application becomes stale and the new one lacks a premise, while the conditional
theorem stays valid. Proposals are fixed source files; no model requests are made.

### Run and resume

Build the [pinned proof bundle](../reference/proof-verification.md#build-the-bundle-and-run-native-checks)
first, then use a new destination:

```bash
uv run --locked python examples/m4/demo.py start runs/m4-demo --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m4/demo.py resume runs/m4-demo --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m4/demo.py resume runs/m4-demo --bundle runs/m4-tools/bundle.json
```

`start` verifies three proposals: the valid proof, an incomplete proof, and a
five-second timeout. The latter two stay `unproved`, with costs retained. It
commits the proof checkpoint before any application or task check. `--crash`
kills the host after it writes its report, while the ledger is still open; the
native test does this and resumes in two fresh processes. The first `resume`
performs eight executable checks and records applications and task decisions; the
second performs no new verification or checker executions. `resume` raises unless
all three applications are supported, only `correct` is accepted, and the premise
experiment reports `stale` then `unsupported`.

Each command writes a report under `reports/` and evidence under `exports/`.
Proof and checker execution witnesses (`proof-executions.jsonl`,
`executions.jsonl`) stay outside the ledger. The host verifier is wrapped only to
record that witness and always calls the same pinned verifier; the CLI offers no
replacement checker. The `proof` CI job retains these files for 14 days.

### What is established

The [owner-pinned intent](../../examples/m4/intent.json) selects source rows per
candidate and the identity mapping with exact equality, matching the Lean target.
Identity is injective. One host check establishes uniqueness of the selected
identifiers; another compares the candidate's identifier sequence with the mapped
selection.

| Candidate | Selected rows | Input unique | Correspondence | Proof application | Task acceptance |
| --- | --- | --- | --- | --- | --- |
| Correct | All three | Pass | Pass | Supported | Accepted |
| Dropped row | First two | Pass | Pass | Supported | Rejected |
| Empty | None | Pass | Pass | Supported | Rejected |

The dropped-row and empty candidates fail record preservation and daily totals
while passing uniqueness and timestamps, under the unchanged M2 evaluator, gates,
and scoped source-offset exception. Proved uniqueness permits record loss,
including the empty list. A proof receipt cannot replace the evaluator receipt or
waive a failed gate.

An application records the theorem, candidate, source, selection, mapping, and
checked premises. Support requires passing validation and current versions for
every required claim; a current but rejected, unproved, unknown, or unsupported
premise cannot establish it. The host also checks each premise's exact candidate,
checker request, and result field, so a passing result for another field cannot
replace a failed uniqueness premise.
[`tests/test_m4_fixture.py`](../../tests/test_m4_fixture.py) covers these cases,
wrong correspondence, unknown mappings, invalid selections, unapproved premise
transitions, changed fixtures, and unknown verification blocking dispatch on
resume.

### Changed premise

After the initial applications, the host commits the transition approved in
`intent.json`. The premise experiment uses
[`input-duplicates.csv`](../../examples/m4/input-duplicates.csv), whose selected
identifiers are `r1`, `r1`, `r3`. The matching candidate has the same sequence, so
correspondence passes and identity stays injective, but the input-uniqueness
premise fails. The original application's evidence is unchanged and becomes
`stale`; a new application records the failed premise and is `unsupported`. Both
reports keep a passing, current conditional theorem. The revision affects only
the premise experiment; matrix decisions still describe the original input and
contract, and no task acceptance is claimed for the duplicate-input case.

### Accounting and measurements

The cap is three synthetic proof units and eight synthetic executable-work units
(attempt counts, not money or CPU).

| Stage | Proof executions | Executable checks | Spent proof | Spent executable | Reserved |
| --- | --- | --- | --- | --- | --- |
| Start | 3 | 0 | 3 | 0 | 0 |
| First resume | 3 | 8 | 3 | 8 | 0 |
| Further resume | 3 | 8 | 3 | 8 | 0 |

The executable baseline is three candidate evaluations plus one source-style
check. The proof-assisted path keeps all four and adds four premise/correspondence
checks, the proof attempts, and tool-bundle setup; it does not replace executable
work with Lean.

One local run (Python 3.12.12, Podman 6.1.2, Lean 4.34.0 bundle), without model
costs:

| Work | Elapsed |
| --- | --- |
| Earlier cached bundle build | 47.127 s |
| First valid verification | 12.168 s |
| Incomplete proof attempt | 7.860 s |
| Five-second timeout, including setup and cleanup | 10.061 s |
| Three cached proof calls on the second resume | 0.112 s |
| Three task evaluations and the source-style check, combined | 2.940 ms |
| Four premise/correspondence checks, combined | 1.130 ms |
| Complete second resume, through evidence export | 0.647 s |

These are single-run development observations. Executable timings exclude ledger
publication and acceptance bookkeeping; the cached build excludes the initial
download and is not cold setup cost. The fixture shows reuse and retained costs,
not that Lean earns its cost on this task. Report fields:
`proof_bundle_build_elapsed_ns`, `proof_attempts` (each outcome and time,
including failures), `proof_stage_elapsed_ns`, `elapsed_ns_by_kind`
(verification, premise checks, task checks, acceptance bookkeeping), and
`host_elapsed_ns` (through evidence export, before the final report write).
Cached reports keep historical verification costs. An interrupted or
unattributable attempt keeps its reservation and blocks dispatch; a changed
fixture, host, environment, or bundle fails before cached reuse.

## Specification failures exercised

This fixture supplies two of the
[specification and behavioral failure cases](../experiments/evaluation-design.md),
each with a valid narrow check or proof and an independent failing obligation:

- **Mistranslated requirement:** the wrong-offset candidates meet a timestamp
  interpretation but fail references derived from the contract (M2 matrix and the
  M3 old candidate). A fixed timestamp proof version of this case is in the
  [configuration migration fixture's proof cases](config-migration.md).
- **Incomplete formal target:** the dropped-row and empty candidates satisfy
  identifier uniqueness, proved in Lean in the M4 application, while
  record preservation rejects them.

The correct candidate is the successful control, so rejecting everything cannot
count as success. Narrow successes and task rejections are both recorded and
survive restart; a corrected candidate passes only after the required checks run
again, with the old failure kept. These cases test rejection of known failures,
not discovery of unstated intent.

## Limits

- The host parser, identity mapping, and correspondence check are a trusted link
  to the formal model; Lean does not prove the Python transformation, record
  preservation, or timestamp semantics.
- All candidates and proofs are fixed development cases; the fake model supplies a
  fixed program. Nothing here is held-out evaluation, a learned policy, a live
  provider result, or evidence of model proof-search quality.
- Single trusted local writer; remote owner authentication and external-effect
  authorization are not provided.
