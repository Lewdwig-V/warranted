# Claims and acceptance

Two host-side APIs sit on top of the [evidence ledger](evidence-ledger.md).
`warranted.claims.Claims` records assertions about exact evidence and reports
their current support: checker result and dependency applicability, kept
separate. `warranted.acceptance.Acceptance` is the local gate: it resolves the
current host-owned policy, checks independent checker receipts, and commits an
acceptance decision. Claims never grant acceptance. Both record into the
existing ledger schema and add no dependencies.

These implement the dependency representation in
[durable knowledge](../design.md#durable-knowledge) and the local part of
[rules, gates, and verification](../design.md#rules-gates-and-verification); see
also [intent, contracts, and acceptance](../design.md#intent-contracts-and-acceptance).
The CSV fixture experiments that exercise them (restart, offset, annotation, and
definition revisions) are described in
[CSV transformation](../fixtures/csv-transformation.md).

## Evidence references

`Evidence(name, artifact)` (in `warranted.acceptance`) binds a snapshot name or an
observation channel `observation/<sequence>/<channel>` to its `ArtifactRef`.
`Evidence.captured(observation, channel)` builds one from a committed observation.
Equal bytes from different observations keep different references.

## Claims

The trusted host constructs `Claims(ledger, session)` and calls:

```python
claims.record(statement, target, assumptions, *, parents=(), validation=None, complete=False)
```

| Argument | Meaning |
| --- | --- |
| `statement` | The assertion text, with no implied truth guarantee. |
| `target` | `Evidence` for the exact snapshot or observation the claim is about. |
| `assumptions` | Stable names mapped to the `Evidence` versions the claim used. |
| `parents` | Earlier claim references whose dependencies also apply. |
| `validation` | Optional `(Request, field)`: the expected checker request and the boolean field of its `result.json`, or `"proof"` for a proof receipt (see [proof verification](proof-verification.md)). The request must name the exact target among its inputs. Omit it for an unchecked assertion. |
| `complete` | Whether the host declares the dependency list complete. Defaults to `False`. |

`record` returns an `Evidence` reference to an immutable `claim.json` observation
whose origin links the target, assumptions, parents, and checker inputs. The
validation entry binds the full checker request by digest and operation ID; the
private project context stays in the operation journal. Identical claims reuse the
existing capture. Parents must already be captured claims that precede the new
one, which prevents cycles.

`claims.assess(claim, current)` takes the host's current version of each stable
name (an `Evidence`, or `None` for unknown). For example, `offset` maps to the
evidence named `offset-v2` after a revision. It returns an `Assessment` with
separate `validation`, `applicability`, and per-dependency `dependencies`
(`assumption/<name>` and `parent/<name>` entries).

| Applicability | Meaning |
| --- | --- |
| `current` | Every declared version matches and every parent is current. |
| `stale` | A recorded assumption differs, directly or through a parent. |
| `unknown` | A current version is missing or the dependency list is not declared complete. |

Staleness propagates through parent edges; unaffected claims stay current. When a
mismatch and an unknown dependency coexist, the result is stale and the dependency
map keeps both reasons.

| Validation | Meaning |
| --- | --- |
| `unproved` | No validator was configured. |
| `missing` | No operation exists for the recorded checker ID. |
| `unknown` | The checker operation is pending or unknown. |
| `unsupported` | A different request occupies the ID, the checker did not succeed, or `result.json` lacks a boolean field. |
| `infrastructure_failure` | The checker completion reports an infrastructure failure. |
| `passed` / `rejected` | The committed checker completion's field is true / false. |

Validation reads only the exact committed checker completion. Raw observations
cannot stand in for it. Validation states are separate from unknown applicability.
Assessment never runs a checker, adds evidence, or changes accounting; repeated
assessment preserves raw claim bytes, usage, and prior decisions.

`current` establishes version agreement, not that an assumption is true. Parent
edges track applicability; they do not prove an inference from parent statements
or turn an unchecked transformation into a validated claim. A historical boolean
result remains readable under its original interpretation even when the claim is
stale. A checker that returns several fields in one receipt binds all its inputs,
so a revision affecting any one input marks every claim validated by that receipt
stale.

## Acceptance

The host constructs `Acceptance(ledger, session, resolve, scope=ROOT_SCOPE)`.
Decisions and exceptions are recorded in `scope`, normally the run's
[ledger scope](evidence-ledger.md#scopes). The resolver is
trusted code that reads current versions when the boundary calls it. It takes the
candidate `Evidence` and returns an `AcceptanceContext`:

| Field | Meaning |
| --- | --- |
| `policy` | `Evidence` for the current policy JSON. |
| `revision` | `Evidence` for the host's current interpretation or revision event. |
| `checks` | Checker name to the expected checker `Request`, or `None` when applicability is unknown. |

The resolver must include all relevant dependencies. It does not discover omitted
dependencies or authenticate remote owners. The set of checker names must equal
the checks named by the policy.

### Policy format

```json
{
  "version": 1,
  "owner": "fixture-owner",
  "transition": "accept-candidate",
  "requirements": {
    "unique_ids": {"kind": "gate", "check": "evaluate", "field": "unique_ids"},
    "explicit_source_offsets": {"kind": "rule", "check": "source-style", "field": "explicit_offsets"}
  }
}
```

Each requirement is a `rule` or a `gate` with a checker name and a result field.
At least one gate is required. Unknown fields, kinds, versions, and transitions
raise `ValueError` before any acceptance is recorded. The full fixture policy is
[`examples/m2/fixture/acceptance.json`](../../examples/m2/fixture/acceptance.json).

### Methods

`accept(target, receipts, exceptions=None)` assesses and records a decision.
`receipts` maps checker names to operation IDs (missing IDs block). `exceptions`
maps rule names to previously recorded exception operation IDs. It returns a
`Decision` with `status`, per-requirement `requirements`, and the ledger
`completion`.

`record_exception(target, rule, reason)` records a rule exception and returns its
operation ID. Call it only from the host's authorized exception path. It records
the policy owner, reason, target, policy, revision, and expected checker
identities. It refuses gates and unknown rules. The owner label is attribution,
not authentication.

Both methods resolve current context themselves. `accept` takes no earlier
decision, caller-supplied applicability, or gate waiver. An exception recorded for
another target, revision, or checker identity is stale. Extra receipt names, or an
exception offered for a gate or unknown rule, make the decision `unsupported`.

### Checker evidence

The boundary reads committed operation completions through `Ledger.lookup`. Raw
observations with matching IDs or text cannot replace them. A gate's checker
request must name the exact candidate among its inputs. A checker must complete
with `SUCCEEDED` and supply a `result.json` object with exactly the policy's
fields for that checker, all booleans. Duplicate keys, truthy numbers, missing
fields, and extra fields do not establish success.

| Status | Meaning |
| --- | --- |
| `passed` | The required field is true under current support. |
| `excepted` | A scoped, host-authorized exception permits departure from a rule. |
| `rejected` | A required, unexcepted field is false. |
| `missing` | A required receipt does not exist or was not supplied. |
| `stale` | The supplied receipt or exception belongs to another identity or context. |
| `unknown` | Current applicability or a required operation outcome is unresolved. |
| `unsupported` | Policy, evidence, identity, or checker output cannot support acceptance. |
| `infrastructure_failure` | A required checker reported an infrastructure failure. |
| `accepted` | Every requirement passed or has a valid rule exception. |

The overall status is the first present of `unsupported`,
`infrastructure_failure`, `unknown`, `missing`, `stale`, `rejected`; otherwise
`accepted`. Every requirement's status is retained, so a passing narrow check
cannot erase a failed gate, and checker failure stays distinct from a false
property. Invalid policy and damaged artifacts raise before a decision is recorded.
A recorded budget breach in the boundary's scope or the root scope, or a negative
project total, raises `BudgetExceeded`, including on reuse of an earlier decision.
An unknown operation in the scope or the root scope blocks committing a new
decision: `accept` raises `UnknownOutcome` and records nothing, not even an
`unknown` decision, until that operation settles.

### Persistence and recovery

Decisions and rule exceptions are ledger operations (kinds `decision` and
`rule-exception`) with an empty reservation and zero usage: they are host
bookkeeping, not checker executions. Elapsed time is recorded separately. Claims
are likewise uncharged.

A completed decision operation means the assessment was recorded. Candidate
acceptance additionally requires `decision.status == "accepted"`; the operation's
successful process outcome alone does not establish task success.

The decision's request identity binds the target, policy, revision, checker
request digests, submitted IDs, and assessment. Identical calls recheck current
evidence before reusing a completion. Changed support creates a new decision and
preserves prior ones. An interrupted decision stays pending or unknown under the
ledger's [recovery rules](evidence-ledger.md#operation-identity-and-recovery). A
known pending decision can continue after reassessment; an unknown one raises
`RuntimeError` and is never retried automatically. A lost response after
completion reuses that exact completion without another transition or charge.

The protected transition is the committed acceptance itself. A returned decision
does not authorise a later file publication, tool call, or external effect.

## Contract revisions

The library does not define a revision protocol; the host supplies the current
`revision` evidence and assumption versions. The CSV fixture host
([`examples/m2/experiments.py`](../../examples/m2/experiments.py)) demonstrates one:

- The pinned [intent record](../../examples/m2/fixture/intent.json) holds the
  request, source documents, obligation links, known gaps, initial
  interpretation, and owner approvals. Each approval names one experiment
  condition, exact changes, affected obligations, and a reason.
- At the single submission checkpoint, the host compares the proposed
  interpretation with the selected approval. An unapproved proposal fails before
  any revision write. A second revision event is refused.
- The committed revision retains both interpretations, the approval reference,
  sources, prior candidates, receipts, and claims. Every later acceptance
  re-reads the revision and checks it against the pinned approval again; a
  missing or duplicated revision blocks the fixture.
- A worker-authored revision, or an edited definition under an old identity,
  cannot select a new contract. A changed fixture fails the project context check.

Approvals are trusted local fixture data captured at project start. The owner name
is attribution, not remote authentication. The host, approval source, and ledger
writer must be outside any worker's writable workspace.

## Guarantees and limits

- One trusted, serialized writer on a working local filesystem. Do not interleave
  another revision or write during a boundary call.
- Workers must not receive the resolver, `record_exception`, the ledger writer, or
  private checker state. Python objects and command wrappers provide no isolation;
  see [worker and containment](worker-and-containment.md).
- The host supplies semantic dependencies and current versions. Nothing here
  discovers omitted dependencies, infers user intent, or authenticates a remote
  owner. An incomplete declaration returns `unknown` and needs broader review.
- Claims reports cannot replace the independent evidence acceptance requires.
- History scans suit small fixtures; larger histories need indexing.
- Not provided: concurrent writers, power-loss durability, remote owner
  authentication, external-effect authorisation, or replay.

## Tests

| Test file | Observable guarantees |
| --- | --- |
| [`tests/test_claims.py`](../../tests/test_claims.py) | Staleness propagates across restart without rewriting validation; unknown dependencies and raw success text supply no support; a different request under the expected ID cannot validate; validation outcomes are not relabelled as success. |
| [`tests/test_acceptance.py`](../../tests/test_acceptance.py) | Rule exceptions are scoped and never waive a gate; restart rechecks current versions; missing, unknown, forged, and narrow evidence cannot accept; checker failure differs from a failed obligation; unknown applicability does not skip a gate; unknown kinds and empty policies fail; changed checker or environment cannot reuse a receipt; malformed results never pass; raw exceptions and budget breaches cannot bypass; termination immediately before and after a decision commit is unknown or reuses the receipt. |
| [`tests/test_m2_fixture.py`](../../tests/test_m2_fixture.py) | The same boundary and claims across restart and approved revisions in the CSV fixture: an unapproved revision cannot replace the pinned contract; claims and the revision committed before forced termination are recovered without repeating work; a previously rejected candidate passing a fresh check after an approved offset revision while the old rejection is retained and blocked as stale. |

```bash
uv run --locked pytest -q tests/test_claims.py tests/test_acceptance.py tests/test_m2_fixture.py
```
