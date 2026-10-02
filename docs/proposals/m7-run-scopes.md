# M7 design note: run-scoped budgets and blocking

Proposed 2026-10-02. This is a design for review, not a description of existing
behavior. It answers open question 6 of the [M7 proposal](m7-harness-api.md):
how budgets and blocking work for one run inside a project shared by a campaign.

## Problem

The first prototype slice ran several runs in one project, as the proposal
intends for a campaign. Three behaviours of the current
[evidence ledger](../reference/evidence-ledger.md) and worker treat the whole
project as one unit:

| Coupling | Where | Effect on a campaign |
| --- | --- | --- |
| Allowances are fixed in the manifest at project creation. | `Ledger.create`, `Ledger.reserve` | A run cannot have its own model, tool, or check budget. One run can spend the whole campaign's allowance. |
| Any unknown operation stops all dispatch. | `worker.unresolved`, called by the worker loop and proof receipts | One lost model response in one run turns every later run into `unknown` without an attempt. Verified in slice 1. |
| Any recorded usage breach stops all new reservations, dispatches, and acceptance. | `Ledger._check_budget`, `Acceptance._commit` | One run that used more than it reserved blocks every other run, including acceptance of work already checked. |

All three are correct for a project that holds one task, which is how every
fixture so far is built. They become wrong when a project holds independent runs.

## Requirements

1. Each run can have its own allowance per unit, and the project total stays
   binding across all runs.
2. An unknown operation blocks further dispatch in its own run and never a
   different run, unless the two runs share external state (see below).
3. A recorded breach blocks its own run's dispatch and acceptance. It does not
   block other runs while the project total is not exceeded.
4. Nothing is released early. An unknown operation keeps its reservation against
   both its run and the project total, forever, as today. No retry is introduced.
5. Scopes and their allowances survive restart and cannot be redefined. Workers
   cannot create, change, or select a scope.
6. Existing projects and their retained evidence stay readable.

## Proposal: scopes in the ledger

Add one concept to the ledger: a **scope**. A scope is a named, host-created
subdivision of the project with optional per-unit caps. Every operation belongs to
exactly one scope. The existing project-wide behaviour is the **root scope**.

### Records

- A `scopes` table: scope ID, parent (root for now), creation session and time,
  and immutable caps, for example `{"model": 24, "tool": 24, "check": 12}`.
- A `scope` column on `operations`, fixed at reservation and part of the
  operation's identity. Reserving the same operation ID in a different scope is an
  `OperationConflict`.

### Host API

| Method | Behaviour |
| --- | --- |
| `open_scope(session, scope_id, caps)` | Record a scope once. Repeating it with the same caps returns it; different caps raise `OperationConflict`. Units must exist in the project allowances. |
| `reserve(session, request, reservation, scope=ROOT)` | Reject if the reservation exceeds the scope's available cap **or** the project's available total. |
| `begin(session, request)` | Block on a breach in the operation's own scope or in the root scope, or on a negative project total. |
| `accounting(scope=None)` | Project totals as now; with a scope, that scope's limit, spent, and reserved. |
| `unresolved(scope)` | Unknown operations that block this scope: its own, plus any in the root scope. |

Caps are ceilings, not pre-allocations. The sum of run caps may exceed the project
total; the project total is checked on every reservation, so it can never be
exceeded. A run can therefore be starved by earlier runs, which is acceptable
while runs are serialised. Pre-allocating each run's cap from the project total is
a later option if parallel runs need guaranteed budgets.

### Blocking rules

| Event in scope A | Scope A | Other run scope B | Root scope |
| --- | --- | --- | --- |
| Unknown operation | Blocked | Continues | Continues |
| Breach | Blocked, including acceptance | Continues | Continues |
| Project total negative | Blocked | Blocked | Blocked |
| Unknown or breach **in the root scope** | Blocked | Blocked | Blocked |

Root-scope operations stay as conservative as today, so every existing fixture
keeps its current behaviour unchanged by default.

### Shared external state

Scoping the block is safe only when runs cannot affect each other through an
operation whose outcome is unknown. That holds for today's operations:

- model calls have no side effect beyond cost, and their reservation stays held;
- each shell episode runs in its own container;
- checks and proofs run on captured bytes in fresh contained processes.

An operation that writes to a shared external resource must not be placed in a
run scope. It goes in the root scope, where an unknown outcome blocks everything,
until a later design gives scopes an explicit resource identity. This rule belongs
in the host-mediated operations design as well.

### Task layer

`Project.start` opens scope `run/<run-id>` with caps from the run configuration
(new `budgets` table in the run file) and passes it through the worker, checks,
and acceptance. `Acceptance` takes the scope of the run it decides for.
`worker.unresolved` takes the episode's scope. Proof receipts follow the same
pattern.

### Storage format

Scopes change the schema, so the storage format becomes version 3. Version 2
projects hold retained evidence for published experiment records, so they must
stay readable:

- A version 3 build opens version 2 projects with every operation in the root
  scope, without modifying the file. That keeps today's behaviour for them.
- Writing scopes requires a version 3 project. There is no in-place upgrade.

Version 1 projects keep failing explicitly, as they do today.

## Negative cases to implement first

- A reservation within the project total but above its run's cap is refused.
- Run caps that sum above the project total cannot make the project total
  negative.
- An unknown operation in run A: run A cannot dispatch; run B can; after restart
  the same holds and A's reservation is still counted in both A and the project.
- An unknown operation in the root scope blocks run A and run B.
- A breach in run A blocks A's dispatch and acceptance; run B can still dispatch
  and be accepted.
- Reopening a scope with different caps, or reserving an existing operation ID in
  another scope, raises `OperationConflict`.
- A version 2 project opens unchanged, reports root-scope accounting, and its
  file bytes are not modified.
- Scope and caps are recorded before any reservation in the run, and a resumed
  run cannot use a different scope.

## Alternatives considered

- **One ledger per run, with a campaign ledger.** Each run gets isolation for free:
  the campaign ledger reserves a run's allowance as one operation and settles it
  when the run ends. This needs no schema change, but facts and claims would cross
  ledgers, so memory and dependency tracking would need cross-ledger evidence
  references. Scopes keep one ledger and keep memory simple.
- **Scopes enforced only in the task layer**, from operation-ID prefixes and a
  recorded allowance. This avoids a schema change, but any caller of
  `Ledger.reserve` could bypass the cap. Budget enforcement belongs at the ledger
  boundary.

## Open questions

1. Should caps become pre-allocations once runs can execute in parallel?
2. Should token counts become ledger units so that a run's token budget is enforced
   like other units? Today they are recorded but not reserved.
3. Is a version 3 build opening version 2 projects read-compatibly enough, or does
   retained evidence need a migration tool?
