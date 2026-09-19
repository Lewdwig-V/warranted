# Local acceptance boundary

`warranted.acceptance.Acceptance` records whether a candidate satisfies the current
host-owned policy. This implements the local part of [rules and gates](design.md#rules-gates-and-verification).
The protected transition is the committed acceptance itself. A returned decision
does not grant permission for a later file publication, tool call, or external effect.

## Host interface

The host constructs `Acceptance(ledger, session, resolve)`. The resolver is trusted
code that reads current versions when the boundary calls it. It accepts a candidate
`Evidence` and returns an immutable `AcceptanceContext`:

| Field | Meaning |
| --- | --- |
| `policy` | Exact reference to the current policy JSON |
| `revision` | Exact reference to the host's current interpretation or revision event |
| `checks` | Named expected checker requests, or `None` when applicability is unknown |

`Evidence` binds a snapshot name or observation channel to its digest and byte
length. Equal bytes from different observations retain different references.
Checker requests bind producer, version, inputs, and the complete project context.
Every gate's request must name the exact candidate among its inputs.
The resolver must include all relevant dependencies. This slice does not discover
omitted dependencies or authenticate owner revisions.

The [fixture policy](../examples/m2/fixture/acceptance.json) is the first policy format.
It declares version 1, owner attribution, the `accept-candidate` transition, and
named requirements. Each requirement declares `rule` or `gate`, a checker name,
and a result field. At least one gate is required. Unknown fields, kinds, and
versions fail explicitly. Other transition types are unsupported.

Call `accept(target, receipts, exceptions=None)` to assess and record acceptance.
`receipts` maps checker names to operation IDs. Missing IDs remain blocking.
`exceptions` maps rule names to previously recorded exception operation IDs.
Call `record_exception(target, rule, reason)` only from the trusted host's authorized
exception path. It records the owner from the policy, reason, target, policy,
revision, and expected checker identities. It refuses gates and unknown rules.
An owner label records attribution, not authentication.

Both methods resolve current context themselves. The acceptance call does not
accept an earlier decision, caller-supplied applicability, or a gate waiver.
An exception for another target or revision is stale. Extra receipt names or an
exception offered for a gate block acceptance.

## Checker evidence and outcomes

The boundary reads committed operation completions through `Ledger.lookup`.
Raw observations with matching IDs or text cannot replace those completions.
A checker must complete successfully and supply a `result.json` object with exactly
the required fields and boolean values. Duplicate keys, truthy numbers, missing
fields, and extra fields do not establish success.

| Status | Meaning |
| --- | --- |
| `passed` | The required checker field is true under current support |
| `excepted` | A scoped host-authorized exception permits departure from a rule |
| `rejected` | At least one required, unexcepted field is false |
| `missing` | A required operation receipt does not exist or was not supplied |
| `stale` | The supplied receipt or exception belongs to another identity or context |
| `unknown` | Current applicability or a required operation outcome is unresolved |
| `unsupported` | Policy, evidence, identity, or checker output cannot support acceptance |
| `infrastructure_failure` | A required checker reports an infrastructure failure |
| `accepted` | Every requirement passes or has a valid rule exception |

Each decision retains every requirement's status. A successful narrow check cannot
erase a failed gate. Checker failure remains distinct from a false checked property.
Invalid policy and damaged artifacts raise explicit errors before acceptance.
A budget breach also blocks the boundary, including reuse of an earlier decision.

## Persistence and recovery

Decisions and exceptions use the existing operation journal and artifact store.
No storage schema or dependencies change. Their reservations and usage are empty
because they record host bookkeeping, not new checker executions. Elapsed time
is recorded separately. The fixture reports these operation counts separately
from charged checks and transformations.
The timing covers first recorded assessments. It excludes later cached
revalidation and report overhead.

A completed decision operation means the assessment was recorded. Candidate
acceptance additionally requires `decision.status == "accepted"`. The operation's
successful process outcome alone does not establish task success.

The request identity binds the current scope, evidence references, submitted IDs,
and assessment. Identical calls recheck current evidence before reusing a completion.
Changed support creates a new decision and preserves all prior results.
An interrupted operation remains pending or unknown under the M1 recovery rules.
A known pending operation can continue after reassessment. An unknown decision
cannot be retried automatically. Losing a response after completion reuses that
exact completion without another transition or charge.

The [boundary tests](../tests/test_acceptance.py) cover rule exceptions, gate waivers,
changed versions, malformed results, forged text, budget breaches, and process
termination immediately before and after a decision commit. The original
[fixture tests](../tests/test_m2_fixture.py) exercise the same boundary after restart.

## Scope

This remains one trusted, serialized writer on a working local filesystem.
The host must not interleave another revision or write during a boundary call.
Workers must not receive the resolver, exception method, ledger writer, or private
checker state. Python objects and command wrappers provide no worker isolation.
Concurrent writers, power loss, general claim graphs, remote owner authentication,
external-effect authorization, Lean verification, and replay remain outside this slice.
