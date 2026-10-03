# M7 design note: scoped memory

Proposed 2026-10-03, with review questions 1 to 3 decided the same day (see
[decisions](#decisions)), and implemented the same day in the prototype task
layer (see [implementation notes](#implementation-notes)). This note designs the **scoped memory** item of
[M7](../roadmap.md#m7--stable-harness-api-and-cli): claims scoped to a family of
related tasks, with receipt-backed facts kept separate from worker notes. A note
is promoted only when its own submission is accepted, and selected facts reach
later tasks as files. It refines the [memory sketch](m7-harness-api.md#memory)
in the harness API proposal.

## Problem

Related tasks repeat work. ReSchema solves many functions of one binary, and a
model accepted for one function often answers a sibling outright. ReSchema's
family deduction cache (`reschema/memory.py`) captures this. It keeps one JSONL
file per binary seed with two tiers:

- `verified_fact` entries, written only by the harness when a submission passes
  its hidden gate;
- `unverified_hypothesis` entries, the agent's notes, recorded for every
  submission and marked `promoted` only when the submission they annotate is
  accepted.

When a later task in the same family opens, the agent receives the raw entries
plus a provenance note and a "ready to submit" card built from the newest
verified fact. This is the design we adapt. Its tiering and promotion rule carry
over unchanged.

The cache has no invalidation. A fact recorded under an old canonicaliser or an
earlier recording of the binary is still shown as verified. ReSchema currently
guards against this with a manual version check. Warranted already has the
missing piece: [claims](../reference/claims-and-acceptance.md) record their
assumptions and report `stale` when a host-supplied current version differs.
This note builds memory on claims, so invalidation is no longer a convention the
consumer has to remember.

## Requirements

1. A task opts into a named scope. Workers cannot choose or widen it.
2. Facts supplied by a checker are verified only when their submission is
   accepted. Worker notes are never verified. A note is promoted only when its
   own submission is accepted, never by a later one.
3. Each fact records what it depends on. When a dependency changes, the fact is
   no longer presented as current.
4. A run sees a fixed memory snapshot, so resume and replay show the same files.
5. Memory survives restarts. Tier and promotion are derived from ledger
   records, never from a mutable flag.
6. Agent-authored text cannot forge a verified fact, a promotion, or a
   dependency version. The negative cases are listed below.

[Invariant 2](../../AGENTS.md#invariants-to-preserve) shapes the model. Whether
a fact was accepted (its validation) and whether it still applies to the
current task (its applicability) are recorded separately. A stale fact is not
false; it is not shown as support for the new task.

## Proposal

### Tasks declare a scope and its dependencies

```toml
[memory]
scope = "rot13"           # facts are shared by tasks with the same scope
depends = ["binary"]      # task files whose bytes define the family
```

`scope` follows the task ID syntax. `depends` names files from the task's
`inputs` or `private` tables, and they must exist in the task. A task without
`[memory]` neither reads nor writes memory.

The scope belongs to the project, and a project has one domain. The task author
is trusted to choose scopes. A worker sees the scope's contents but cannot name
another scope.

### Two sources, three tiers

| Source | Recorded when | Tier when shown |
| --- | --- | --- |
| Checker fact (`Verdict.facts`) | The check completes | `verified` if every required check passed and the submission was accepted |
| Worker note (`/work/notes.json`) | The submission is captured | `promoted` if its own submission was accepted |
| Either, from a submission that was not accepted | The same | not shown |

Checkers return facts beside their feedback:

```python
Verdict(VerdictStatus.PASSED, feedback=..., facts=({"params": ..., "c_source": ...},))
```

A fact body is any JSON value. A verdict carries at most 16 facts and 16 KiB of
encoded facts; a larger value is a checker fault, like oversized feedback. Facts
are shown to later workers, so a checker must not put private data in them. That
rule is the same one that already applies to feedback.

A worker may leave `/work/notes.json`, a JSON list of at most 32 strings and
16 KiB, next to `result.json`. The sandbox captures it with the submission under
the same file checks as `result.json`. Checkers never read it, so notes cannot
influence acceptance. A malformed or oversized notes file is recorded as a
capture error and does not affect the submission.

`notes.json` and `memory.json` become reserved file names.

### Facts are claims

Each fact or note is recorded in the run's ledger scope as one record. Its
origin cites the run, the submission target, and (for checker facts) the check
operation. The record holds the scope, the source, and the body. A
[claim](../reference/claims-and-acceptance.md) is recorded on that record with
these assumptions:

- `domain`: the domain identity, which covers the domain version, the checker
  and operation sources, declared `sources`, and the Warranted package digest;
- `file/<name>`: the bytes of each file in the task's `depends`, and for checker
  facts, in the checker's own optional `depends`, a subset of the task's files.

Dependencies are complete by construction, because the host declares the full
list. Recording is idempotent, because identical claims reuse their capture.

Claims compare evidence references, and two runs that pin the same bytes record
different evidence names. So the host records each dependency version once per
`(name, digest)` with `record_once`. Equal bytes then give equal references in
every run, and different bytes give a different one.

Tier is derived, not stored. A record is `verified` or `promoted` when the
ledger holds an `accepted` acceptance decision for its submission target in its
run. Otherwise it is withheld. A crash between acceptance and anything else
leaves nothing to repair, because the fact and the decision are each recorded
once.

### Presentation: one snapshot per run

When a run is created, the host:

1. finds every fact and note in the task's scope from earlier runs;
2. keeps those whose submission was accepted;
3. assesses each claim against the new task's current versions: its domain
   identity and its files under the same names;
4. keeps the `current` ones and counts the `stale` and `unknown` ones;
5. builds `memory.json`, newest first, up to 64 KiB, and records it in the
   same ledger record as the run's task and configuration (`run/<id>/spec`).

Because the snapshot is part of the record that creates the run, there is no
point at which a run exists without its snapshot. A crash before that record
leaves no run to resume. A crash after it leaves a run whose snapshot is
already fixed. Resume never recomputes memory.

```json
{
  "scope": "rot13",
  "entries": [
    {"tier": "verified", "task": "rot13-f2", "run": "...", "check": "model", "body": {}},
    {"tier": "promoted", "task": "rot13-f2", "run": "...", "body": "the key is 13"}
  ],
  "withheld": {"stale": 2, "unknown": 0, "over_limit": 0}
}
```

Every episode of the run receives that same file read-only at `/work/memory.json`.
Resume reads the recorded snapshot rather than recomputing it, so a fact
accepted by another run mid-way does not change what this run's journal replays.
A missing dependency in the new task, such as a sibling task that pins no
`binary`, makes the claim `unknown`. Unknown facts are withheld, never shown.

The domain's worker image or task text explains how to use `memory.json`, the
same way it explains the task's other files. ReSchema's "ready to submit" card
is the newest verified entry, which the domain's prompt can point to. Warranted
adds no model-facing tool.

### Reading memory on the host

`Project.memory(task)` returns every record in the task's scope with its
source, tier, applicability, and per-dependency reasons, assessed against that
task. Stale and withheld entries are
included. The status command and the public API use this. It is read-only and
runs no checker.

## Negative cases to implement first

- A note whose text claims to be verified, or a `notes.json` holding fact-shaped
  objects, is shown only as a note and never as `verified`.
- Checker facts from a submission where any required check rejected are not
  shown. Neither are notes from that submission.
- A note is not promoted by a later accepted submission in the same run.
- A fact whose `depends` file changes bytes under the same name is stale and is
  withheld. An unaffected fact in the same scope stays current.
- A fact recorded under one domain identity is stale when assessed against
  another. This is tested at the claim level; see
  [domain revisions](#domain-revisions) for why a project never presents such
  a fact today.
- A task that lacks a depended-on file name sees the fact as unknown and
  withholds it.
- A task in another scope, or without `[memory]`, sees nothing.
- A worker that writes or replaces `/work/memory.json` changes only its own
  copy. The recorded snapshot is unchanged, and so is the next episode's file.
- After a restart, a resumed run shows the identical snapshot, even when
  another run accepted new facts in between.
- An oversized or malformed `notes.json` does not change the submission's
  acceptance, and oversized checker facts are a checker fault.
- A scope or `depends` name that is invalid, or names a file the task does not
  have, is rejected when the task loads.

## Alternatives considered

- **Mutable promotion flags,** as in ReSchema's JSONL. These need a write after
  acceptance, which opens a crash window and a second source of truth. Deriving
  the tier from the acceptance decision avoids both.
- **Showing stale facts with a label.** A stale verified fact can still be a
  useful hint. But ReSchema's agents copy verified facts verbatim, and a
  labelled stale fact invites the same copy. Withholding is the conservative
  default. Stale facts stay visible to the host.
- **A live view instead of a per-run snapshot.** A live view would show facts
  accepted mid-run, but it breaks replay: a resumed episode could read a file its
  journal never saw.
- **Dependencies inferred from what the checker read.** Inference is attractive,
  but the claims design has the host declare dependencies, and invariant 4 keeps
  the applicability test out of the checker's or worker's hands. Declaring
  `depends` in the task is explicit and reviewable.
- **A domain hook for selection.** ReSchema shows only the newest verified fact
  as a card. Newest-first under a byte limit covers that. A selection hook can
  be added when a second consumer needs one.

## Domain revisions

A project pins its domain identity. `Project` refuses to open a ledger whose
recorded identity differs from the domain it is given, so a changed checker,
canonicaliser, or declared source means a new project. A new project starts
with an empty ledger, and so with empty memory. Facts therefore never outlive
the domain code that produced them, and a project never needs to report a fact
as stale because of a domain change.

The `domain` dependency is still recorded with every fact. It costs nothing,
and it makes the result correct by construction if a later milestone adds a
path that carries facts across a domain change: a domain-revision record inside
a project, or an export from one project and import into another. Either path
would assess each carried fact against the new identity and withhold the stale
ones. Neither path is part of this design.

For ReSchema this means a canonicaliser change starts a fresh project and an
empty family cache. That is the conservative choice, and it matches what
happens to the project's runs. Keeping memory across a domain revision is
[review question 4](#questions-for-review).

## Held-out separation

A scope shares information between tasks. A campaign that mixes training,
development, and held-out tasks must not give them a common scope, or held-out
results would draw on training facts
([invariant 7](../../AGENTS.md#invariants-to-preserve)). Campaigns are not in
M7's slices yet, so this note records the rule. A later campaign planner should
refuse a scope shared across splits.

## Implementation outline

1. `TaskSpec` gains `memory` (scope and depends) and validation. `Verdict` gains
   `facts`, and `notes.json` and `memory.json` are reserved.
2. The sandbox captures optional `notes.json`.
3. The task layer records facts and notes as claims with host-declared
   dependency versions, and `Project.memory(task)` reads them.
4. Run creation records the `memory.json` snapshot in the run's spec record,
   and episodes deliver it.
5. Tests for the negative cases above, plus a two-task example in which the
   second task reuses the first task's verified fact and a revised input makes
   it stale.

## Decisions

Decided by the project owner on 2026-10-03:

1. Memory stays within one project. A scope is not shared across projects.
2. Notes from a submission that was not accepted are withheld entirely.
3. Stale facts are hidden from the worker. The host still reports them through
   `Project.memory(task)`.

Question 4 was not separately decided. The proposal's default stands: a
revised domain means a new project with empty memory.

## Implementation notes

Implemented in `warranted.experimental` and the sandbox capture, with tests in
`tests/test_scoped_memory.py` and container tests for notes capture in
`tests/test_sandbox.py`. Where the implementation differs from the proposal
above:

- **Entries are derived, not written at check time.** Checker facts are stored
  in the check's completion as `facts.json`, and notes stay in the submission
  capture. Memory entries, their claims, and dependency versions are derived
  from those records when a run is created and when `Project.memory(task)` is
  called. The derivation is idempotent. Nothing is recorded only at acceptance,
  so a crash at any point loses no memory.
- **One entry per source.** An entry holds all of one check's facts, or all of
  one submission's notes, and has one claim. The snapshot lists each fact or
  note separately.
- **No checker-level `depends` yet.** Every entry depends on the domain
  identity and on the task's `depends` files. A checker cannot add further
  dependencies.
- **Malformed notes are ignored.** A notes file that is not a JSON list of at
  most 32 strings, within 16 KiB, adds no entry. Its raw bytes stay in the
  submission capture. The sandbox records file-level problems, such as a link or
  an oversized file, as `notes-error.txt`.
- **The snapshot cites its entries.** The run's spec record lists the entries
  it shows as origin inputs.
- **No example directory.** The two-task case runs in the tests rather than as
  an example under `examples/m7/`.
- **The duplicate guard ignores notes.** The guard compares candidates without
  `notes.json` or `notes-error.txt`, so changing a note cannot make a repeated
  candidate look new.

## Questions for review

1. Should a scope be shared across projects? This proposal says no: memory
   lives in one project's ledger, as ReSchema's cache lives in one checkout.
2. Should unaccepted notes be withheld entirely, as proposed? ReSchema shows
   them with `promoted: false`.
3. Should stale facts be withheld, as proposed, or shown with a label?
4. Should memory survive a domain revision? This proposal says no, because a
   project pins its domain and a revised domain means a new project. Supporting
   it needs a domain-revision record or a cross-project import, either of which
   would assess carried facts against the new identity.
