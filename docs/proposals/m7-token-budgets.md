# M7 design note: token budgets

Proposed and accepted 2026-10-02; not yet implemented. This note designs the
**token budgets** step of
[M7](../roadmap.md#m7--stable-harness-api-and-cli): model tokens become reserved
ledger units, enforced like model, tool, and check units, in the project and in
each run's [scope](m7-run-scopes.md).

## Problem

The `model` ledger unit counts attempts, not tokens
([model adapters](../reference/model-adapters.md#shared-attempt-contract)). An
episode reserves `{"model": 1}` before each call and the adapter settles 1.
`LocalChatCompletions` and `OpenRouterChatCompletions` already validate the
provider's reported `prompt_tokens`, `completion_tokens`, and `total_tokens` and
store them in a `tokens.json` artifact, but no budget reads that artifact. A run
can be capped at 24 model calls and still spend any number of tokens, and a worker
whose prompt grows with its trajectory makes later calls much more expensive than
early ones. Token totals appear only in experiment reports, after the fact.

## Requirements

1. A run can be capped in prompt tokens and in completion tokens, and the project
   total stays binding, with the same rules as other units: caps are ceilings,
   reservations precede dispatch, nothing is released early.
2. The reservation for a call is known before dispatch and is an upper bound on
   what the call can be charged, so an honest provider cannot breach it.
3. If usage is unmeasured or exceeds the bound, the call is charged conservatively
   and further work in its scope is blocked by the existing breach rule. It never
   silently counts as zero.
4. A run with a token cap cannot be served by a model service that does not
   reserve tokens; the cap is never silently unenforced.
5. The core does not depend on any model's tokenizer.
6. There are no external users, so recorded projects need no compatibility or
   migration.

## Proposal

### Two units

Add two ledger units, `prompt_tokens` and `completion_tokens`, alongside `model`.
Two units rather than one because providers price them differently (output is
commonly several times dearer) and because the bound on completion tokens is exact
while the bound on prompt tokens is an estimate. A run's budget can cap either,
both, or neither. The `model` unit keeps counting attempts.

USD is not a unit. OpenRouter reports cost only after a call, so it cannot be
reserved; it stays recorded in `cost.json` and unenforced.

### The reservation is a bound computed from the request

| Unit | Reserved | Why it bounds the charge |
| --- | --- | --- |
| `completion_tokens` | The request's `max_tokens` | The adapter already sends it, and already treats more reported completion tokens as an infrastructure failure. Reasoning tokens count against the same limit on OpenRouter. |
| `prompt_tokens` | The byte length of the exact wire request body | Byte-level BPE and byte-fallback tokenizers emit at least one byte of text per ordinary token, so the text costs at most its UTF-8 length. The JSON keys, quotes, and request parameters around each message are longer than the few special tokens a chat template adds. |

The prompt bound is pessimistic: English text averages about four bytes per token,
so the reservation is roughly four times the eventual charge. Because the whole
reservation is released on settlement, the cost is that a run stops when its
remaining cap cannot cover the **next** call's bound, losing at most about one
call's worth of over-estimate. For a run whose prompts grow to 20,000 tokens under
a 1,000,000-token cap, that is under 10% of the budget.

The bound is a property of supported tokenizers, not a guarantee from the
provider. A provider that adds hidden prompt text, or a tokenizer that emits
tokens without bytes, can exceed it. That case is safe: the actual usage is
charged in full, recorded as a breach, and blocks the scope (requirement 3). An
adapter can later supply a tighter bound, such as an exact count from the model's
own tokenizer, through the same interface.

### Adapters compute the reservation

Today `Journal.call` reserves a fixed `{kind: episode.model_reservation}`. Instead,
a model service may provide `reservation(payload) -> Mapping[str, int]`, and the
journal reserves what it returns for that request:

- `LocalChatCompletions` and `OpenRouterChatCompletions` build the wire body they
  would send, deterministically from the messages and their pinned parameters,
  and return `{"model": 1, "prompt_tokens": len(wire), "completion_tokens":
  max_tokens}`. A body over the 2 MiB limit is never sent, so it reserves
  `{"model": 1}` and zero tokens, then settles as today.
- A service without the method keeps the fixed `{"model": model_reservation}`.
  Scripted and fake services in tests fall in this group.
- Because the reservation is a function of the request, a resumed episode
  computes the same reservation, and `reserve` keeps rejecting a different one as
  an `OperationConflict`.

### Settlement

Completion must already report every reserved unit. The adapters settle:

| Situation | `model` | `prompt_tokens`, `completion_tokens` |
| --- | --- | --- |
| Valid reported usage | 1 | As reported |
| Response received, usage missing, invalid, or inconsistent | 1 | The full reservation (unmeasured, so charged at the bound) |
| Failure before inference (preflight, credentials, oversized request) | 0 | 0 |
| Response lost after dispatch | Unknown; the whole reservation stays held, as today | Same |

Reported usage above the reservation is charged in full and recorded in
`Completion.breaches`; the existing rule then blocks the call's scope, and every
scope if the call was in the root scope. `tokens.json` stays as raw evidence;
accounting reads the settled usage.

### Configuration

- Run configuration `[budgets]` may name `prompt_tokens` and `completion_tokens`
  besides `model` and `tool`. They become caps on the run's scope.
- Project allowances gain both units. A project whose allowance for a unit is zero
  cannot reserve that unit, so a project that uses a token-reserving adapter must
  set token allowances. M5 example runners gain token allowance options.
- Starting a run with a token cap is refused unless its model service provides
  `reservation`. The check happens at `Project.start`, before any operation.
- A run that cannot reserve its next call ends as `budget_exhausted`, as for other
  units.

### Storage format

The units are names in existing tables, so the schema does not change. Settled
usage now includes token units, which changes what recorded projects contain;
there is no migration.

## Negative cases to implement first

- A call whose token bound exceeds the run's remaining `prompt_tokens` or
  `completion_tokens` cap is refused before dispatch, and the run ends
  `budget_exhausted` without contacting the model.
- The same holds against the project total when run caps are larger.
- A lost response keeps its full token reservation in the run and project after
  restart, and a resumed episode computes the identical reservation.
- Reported prompt tokens above the byte bound are charged in full, recorded as a
  breach, and block the run's further dispatch and acceptance.
- A response with missing or inconsistent usage is charged the full reservation,
  never zero.
- A failure before inference settles zero tokens.
- A run with a token cap and a model service without `reservation` is refused at
  start.
- A task or run file that sets a token unit in the wrong owner's table is
  rejected, as for other units.

The byte bound itself should be measured against recorded live runs before the
implementation claims it: for each recorded `http-request.json` and
`tokens.json`, the request length must exceed the reported prompt tokens.

## Alternatives considered

- **One `tokens` unit.** Simpler to configure, but it cannot express a cap on the
  dearer output tokens and mixes an exact bound with an estimate.
- **Exact prompt counts from the model's tokenizer.** Tight reservations, but it
  adds a tokenizer dependency per model family to the core, and the tokenizer must
  match the served model exactly or the count is wrong anyway. It remains possible
  later as an adapter-supplied bound.
- **Settle-only enforcement**: charge reported tokens and check the cap after each
  call. One call could overshoot the cap by its full size, and a lost response
  would leave its tokens uncounted, violating requirements 1 and 2.
- **A characters-per-token heuristic.** Tighter than bytes, but not a bound, so an
  honest provider could breach and block a run.

## Decisions

- Two units; byte-length prompt bound; `max_tokens` completion bound; missing
  usage charged at the bound. Accepted 2026-10-02.
- USD budgets are out of scope until a provider offers a reservable cost bound.
