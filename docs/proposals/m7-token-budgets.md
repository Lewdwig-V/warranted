# M7 design note: token budgets

Proposed and accepted 2026-10-02; implemented 2026-10-03 in the worker, both
chat-completions adapters, and the experimental task layer, with no verified model
yet. The [model adapters reference](../reference/model-adapters.md#token-budgets)
describes the implemented behaviour. This note designs the
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
   what the call can be charged. Where the bound rests on a model's tokenizer and
   chat template rather than on the request itself, it is accepted only for
   models where it has been verified, and only with a margin.
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
| `prompt_tokens` | The byte length of the exact wire request body, plus a fixed per-message margin, for verified models only | Byte-level BPE and byte-fallback tokenizers emit at least one byte of text per ordinary token, so the text costs at most its UTF-8 length. The JSON keys and quotes around each message, plus the margin, cover the special tokens a chat template adds. |

The prompt bound is pessimistic: English text averages about four bytes per token,
so the reservation is roughly four times the eventual charge. Because the whole
reservation is released on settlement, the cost is that a run stops when its
remaining cap cannot cover the **next** call's bound, losing at most about one
call's worth of over-estimate. For a run whose prompts grow to 20,000 tokens under
a 1,000,000-token cap, that is under 10% of the budget.

The prompt bound is a property of the served model's tokenizer and chat template,
not a guarantee from the request. A tokenizer that emits tokens without bytes, a
template with long fixed preambles, or a provider that adds hidden prompt text can
exceed it, and then an honest call overshoots a cap before the breach can block
later calls. Adapters therefore do not reserve prompt tokens for arbitrary
models:

- Each adapter keeps a list of **verified models**, pinned by the adapter's model
  identifier (the local model digest, or the OpenRouter model and provider). A
  model is added only after a recorded measurement, kept under
  `docs/experiments/`, shows its reported prompt tokens below the bound across
  recorded requests, including short prompts where template tokens dominate.
- The per-message margin and the verified list are part of the adapter's pinned
  configuration, so changing either changes the request identity.
- A token-capped run whose adapter's model is not verified is refused at start.
  An unverified model reserves and settles only `model`, as today, because the
  ledger treats any settled unit that was not reserved as a breach. Its reported
  tokens stay recorded in `tokens.json`; nothing is enforced, so nothing is
  claimed.

Verification is evidence, not proof. If a verified model later exceeds its bound,
because a provider changed its template for example, the actual usage is charged in
full, recorded as a breach, and blocks the scope (requirement 3). The overshoot is
then at most one call, and it is recorded rather than hidden. An adapter can later
supply a tighter or provable bound, such as an exact count from the model's own
tokenizer, through the same interface.

### Adapters compute the reservation

Today `Journal.call` reserves a fixed `{kind: episode.model_reservation}`. Instead,
a model service may provide two things:

- `reserved_units`, the units it bounds for its configured model, for example
  `{"model", "prompt_tokens", "completion_tokens"}`. A verified adapter declares
  both token units; an unverified one declares only `model`.
- `reservation(payload) -> Mapping[str, int]`, the reservation for one request.

The journal reserves what `reservation` returns, after two checks. It refuses the
call, before reserving anything, if the returned units differ from
`reserved_units`. It also refuses if a unit capped in the episode's scope is
missing. A service cannot enforce a cap by declaring the method and then omitting
the unit.

- `LocalChatCompletions` and `OpenRouterChatCompletions` build the wire body they
  would send, deterministically from the messages and their pinned parameters,
  and, for a verified model, return `{"model": 1, "prompt_tokens": len(wire) +
  margin × messages, "completion_tokens": max_tokens}`. For an unverified model
  they return `{"model": 1}` and settle no token units. A body over the 2 MiB
  limit is never sent, so it reserves `{"model": 1}` and zero tokens, then
  settles as today.
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
- Starting a run is refused unless the model service's `reserved_units` include
  every token unit the run caps. The check happens at `Project.start`, before any
  operation; the journal repeats it on every call.
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
- A run with a token cap is refused at start when the model service does not
  declare that unit in `reserved_units`. That covers a service without
  `reservation` and an adapter whose model is not verified.
- A service whose `reservation` omits a capped unit, or returns units other than
  those it declared, is refused by the journal before anything is reserved.
- A task or run file that sets a token unit in the wrong owner's table is
  rejected, as for other units.

Each verified model needs its own measurement before it is added. For each
recorded `http-request.json` and `tokens.json`, the bound must exceed the reported
prompt tokens. Include requests with a single short message.

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
- Review added the verified-model list and per-message margin, so a cap never
  rests on an unmeasured tokenizer, and the `reserved_units` declaration, so a
  service cannot leave a capped unit unreserved.
- USD budgets are out of scope until a provider offers a reservable cost bound.

## Implementation notes

- **Enforcement follows the project allowances.** A token unit is enforced on
  every model call once the project allows it, not only in scopes that cap it.
  The root scope's caps are the project allowances, so this is the same rule
  applied to the root, and it also keeps the project total binding for model calls
  outside any run. A project without token allowances runs any model service as
  before.
- **Budget exhaustion keeps the existing run outcomes.** The task layer has no
  `budget_exhausted` outcome; a run that cannot reserve its next call ends
  `incomplete` (or `rejected` after an earlier submission) with a detail starting
  "budget exhausted", as for model, tool, and check units.
- **Usage read before a later error is settled as reported.** If a response
  carries valid counts but fails a later check, such as an unexpected choice, the
  reported counts are settled; only a response whose counts were never read is
  charged at the bound.
- The per-message margin is 16 tokens, applied once per message and once for the
  generation prompt. It is a starting value for the first measurement to confirm
  or raise.
