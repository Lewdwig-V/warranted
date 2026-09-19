# Claims and current support

`warranted.claims.Claims` stores a claim, an assertion about exact evidence.
It keeps the checker result separate from current applicability, the match between
recorded dependencies and current versions. This implements the small dependency
representation described in [durable knowledge](design.md#durable-knowledge).

## Host interface

The trusted host constructs `Claims(ledger, session)` and calls `record` with:

| Argument | Meaning |
| --- | --- |
| `statement` | The assertion, without an implied truth guarantee |
| `target` | Exact snapshot or observation evidence |
| `assumptions` | Stable names mapped to the versions used by the claim |
| `parents` | Earlier claims whose version dependencies also apply |
| `validation` | Optional pair of expected checker request and boolean result field |
| `complete` | Whether the host declares the dependency list complete; defaults to false |

The method returns an `Evidence` reference to an immutable `claim.json` observation.
Identical captures reuse that reference. Parent claims must already exist, which
prevents cycles. The observation links its target, assumptions, parents, and checker
inputs. The validation reference binds the full request by digest and operation ID.
Private project context stays in the operation journal.

Call `assess(claim, current)` with the host's current assumption versions.
For example, the stable name `offset` maps to evidence named `offset-v2` after revision.
The result has separate `validation`, `applicability`, and `dependencies` fields.

| Applicability | Meaning |
| --- | --- |
| `current` | Every declared version matches and every parent has current dependencies |
| `stale` | A recorded assumption differs, directly or through a parent |
| `unknown` | A current version is missing or dependency capture is incomplete |

Staleness propagates through parent edges. Unaffected claims remain current.
When a mismatch and an unknown dependency coexist, the result stays stale.
The dependency map preserves the separate reasons.

Validation reads the exact committed checker completion. A true boolean field
reports `passed`, and a false field reports `rejected`. Missing receipts, unresolved
operations, malformed results, and infrastructure failures remain distinct.
A claim without a validator reports `unproved`. A configured checker with an
unresolved operation reports `unknown`. These validation states remain separate
from unknown applicability caused by missing versions or incomplete dependencies.
Raw observations cannot replace operation completions. Assessment never runs a
checker, adds evidence, or changes accounting.

`current` establishes version agreement, not the truth of an assumption.
Parent edges track applicability. They do not prove an inference from parent
statements or promote an unchecked transformation into a validated claim.
The fixture marks transformation assertions unchecked and independently checks
candidate obligations. Candidate acceptance still requires the
[acceptance boundary](m2-acceptance.md), even when a claim has a passing check.

## Approved fixture revisions

The pinned [intent record](../examples/m2/fixture/intent.json) retains the request,
source documents, obligation links, known gaps, initial interpretation, and owner approvals.
Each approval names one experiment condition, exact changes, affected obligations,
and a reason. Source references link the clarifications, examples, and acceptance
policy to immutable bytes. The initial decision binds this intent record.

At the submission checkpoint, the host compares the proposed interpretation with
the selected owner approval. An unapproved proposal fails before a revision write.
The committed revision retains both interpretations, the approval reference,
sources, prior candidates, receipts, and claims. Every later acceptance resolves
and checks that revision against the pinned approval again.

A worker-authored revision or an edited definition under an old identity cannot
select a new contract. A changed fixture fails the existing project context check.
Two revision events are ambiguous and block the fixture. The fixture supports one
checkpoint. Another revision protocol requires a separately reviewed change.

These approvals come from trusted local fixture data captured when the project
starts. The owner name supplies attribution, not remote authentication.
The host, approval source, and ledger writer remain outside any future worker's
writable workspace. Worker isolation remains M3 work.

## Demonstrated behavior and limits

The [fixture experiments](pilot.md#m2-fixture-experiments) retain claims before
restart and assess them against the approved revision afterward. An offset change
makes normalization and its dependent claims stale. Source facts remain current.
An annotation change preserves all calculations. A definition change preserves
candidate bytes but makes the old candidate validation claims stale.

The evaluator returns all four fields in one receipt. Claims conservatively bind
all inputs to that receipt. A definition revision therefore marks all four old
validation claims stale, although only uniqueness changes meaning.
Each historical boolean result remains readable under its original interpretation.

Tests demonstrate a previously rejected candidate passing a fresh check after an
approved offset revision. They retain the old rejection and block its receipt as
stale. Repeated assessment preserves raw claim bytes, usage, and prior decisions.
The existing forced-process-termination tests also cover claims committed before
the revision checkpoint and recovered in a new process.

This slice uses the existing ledger schema and adds no dependency.
Claim writes use atomic observation commits and have no external effects.
Claims and support reporting are uncharged host bookkeeping. They do not enter
the synthetic execution count or measure arbitrary worker computation.
History scans suit these small fixtures. Larger histories need indexing.

The host supplies semantic dependencies and current versions. This slice cannot
discover omitted dependencies, infer user intent, or authenticate a remote owner.
An incomplete declaration returns unknown and requires broader review.
Concurrent writers, hostile host access, power loss, Lean validation, and replay
remain outside this implementation.
