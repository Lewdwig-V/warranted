# M2 data transformation contract, version 3

The contract owner is `fixture-owner`, the trusted author of this scripted fixture.
The original request is to preserve the source records, convert their local
timestamps to UTC, and sum their integer values by UTC date.
The source records are in `input.csv`. Their order, identifiers, and values must
remain unchanged. All identifiers in this fixture contain ASCII characters.

## Source interpretation

The local timestamps have no embedded offset. `offset-v1` in `versions.json`
states a fixed offset of +01:00. `offset-v2` corrects it to +00:00.
These are fixed offsets, with no time zone database or daylight saving rules.
The two annotation versions describe the source and affect no calculation.

`definition-v1` compares identifiers exactly. `definition-v2` ignores ASCII letter
case when assessing uniqueness. Both definitions require the output to retain
the original identifier spelling. The main identifiers are unique under both
definitions. A separate two-record witness uses `rA` and `ra`.
Its expected uniqueness changes from true to false. It tests the checker and
does not replace the main input or its acceptance obligations.

## Independent obligations

Every candidate must satisfy all four obligations:

1. `unique_ids`: No two emitted identifiers compare equal under the current definition.
2. `preserved_rows`: The ordered identifier and value pairs equal the complete source sequence.
3. `utc_timestamps`: Every emitted identifier is known and has its expected UTC timestamp.
4. `daily_totals`: Reported totals and totals independently computed from emitted rows equal the fixed reference.

Empty output satisfies uniqueness and the narrow timestamp obligation. It fails
preservation and totals. Swapping two timestamps on the same UTC date preserves
totals but fails the timestamp obligation. Neither narrow success permits acceptance.

`references.json` supplies literal reference outputs for each offset version.
The transformation does not produce these references. `candidates.json` supplies
six fixed candidate outputs, including the successful control.
These JSON rows represent normalized CSV fields without serialization differences.

`acceptance.json` classifies all four obligations as gates. No exception can waive
them. A separate rule prefers an explicit offset within every source timestamp.
The source-style checker reports that this fixture does not satisfy that rule.
The fixture owner approves an exception because a separate source document supplies
the offset. The host records the reason against the exact candidate and current
interpretation. This exception changes no gate or reference output.

## Revision protocol

The checkpoint is the first candidate submission, after initial checks and a
decision under the initial contract. The host records one approved revision there.
The unchanged, annotation, offset, and definition conditions use the same checkpoint
and allowance. A fresh process reads the event before any acceptance decision.
`intent.json` pins the request, source links, obligation links, known gaps, and
owner approvals for each condition. An approval names exact changes and affected
obligations. The host refuses revisions outside that approval.
The event names old and new versions, the owner, and the reason for the revision.
It links the approval, source evidence, prior candidates, receipts, and claims.
The matrix condition keeps the initial interpretation throughout.

Source facts depend on the input alone. Normalization depends on those facts and
the offset. Aggregation depends on normalization and the offset. Candidate checks
also depend on the current definition and reference. An annotation change permits
reuse of all calculations. An offset change retains source facts but requires new
normalization, aggregation, and affected checks. A definition change requires new
candidate and witness checks even when the candidate bytes remain identical.
The shared host boundary resolves the interpretation before each acceptance call.
Each final decision records the full current interpretation, including annotation.
Identical calls reuse a committed decision only after checking current support.
An earlier accepted decision does not authorize acceptance after a revision.

Claim records retain assertions, assumptions, parent dependencies, and scoped
validation references. Reports separate historical validation from current
applicability. An offset revision propagates staleness through normalization,
aggregation, and candidate claims. Source facts remain current.
Transformation assertions have no validator and report `unproved` validation.
Candidate claims use the independent evaluator. Each binds all inputs to its
four-field receipt, so a definition revision requires reassessing all four fields.
Unknown versions or incomplete dependency lists cannot establish current support.

## Scope and known gaps

This is a trusted, local experiment with one writer. The host and checker code,
source files, versions, candidates, and references are pinned in the ledger.
The checker shares a process with the scripted host. No untrusted worker runs here.
The fixture supplies explicit dependencies and pinned local owner approvals. It
does not discover dependencies, authenticate a remote owner, or infer user intent.
The protected operation records candidate acceptance in the ledger. It does not
authorize a later external effect. The host serializes calls and owns the policy,
current-version resolver, checkers, and exception method. Lean proofs, worker
isolation, and replay remain later work.

Each executed operation costs one synthetic unit. Elapsed nanoseconds are measured
separately. Host bookkeeping, interpretation lookup, and report writing are not
charged operations. Claims use observation records without a synthetic charge.
Decision and exception records use operation receipts with
empty usage and reservations. Their elapsed time is recorded. Each condition has
a 20-unit allowance across all sessions. The source-style check costs one unit
per condition and is reused after restart.
There are no model calls, external services, training runs, or held-out results.
