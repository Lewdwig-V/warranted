# M7 design note: model providers through litellm

Proposed 2026-10-04; not yet reviewed. This note designs how the task layer and the
CLI reach hosted models: any OpenAI-compatible API (OpenAI, OpenRouter, Hugging
Face Inference Providers, and similar) and the Anthropic API. It extends the
[model adapters](../reference/model-adapters.md) and the CLI's
[run configuration](../reference/cli.md#run-configuration). It does not change the
[shared attempt contract](../reference/model-adapters.md#shared-attempt-contract).

## Problem

The CLI accepts only `provider = "ollama"`, through `LocalChatCompletions`, which
requires a loopback endpoint. `OpenRouterChatCompletions` exists, but it is pinned
to one provider route and one price ceiling for the M5 diagnostic, and the task
layer refuses it. A domain project such as ReSchema cannot run a task against a
hosted model at all.

Writing one adapter per wire format (OpenAI Chat Completions, Anthropic Messages,
and the variants each provider adds) would duplicate work that
[litellm](https://github.com/BerriAI/litellm) already does. litellm is already
installed: mini-swe-agent 2.4.6 depends on it, and its `LitellmModel` uses it.
Warranted does not use mini's model, because `LitellmModel.query()` wraps every
call in a retry loop and tracks cost in a process-wide global, outside the ledger.
`WorkerModel` instead sends each call through the journal: reserve, dispatch once,
record the raw result. That boundary stays. Only the call behind it changes.

## Requirements

1. One adapter reaches OpenAI-compatible APIs and the Anthropic API.
2. The [attempt contract](../reference/model-adapters.md#shared-attempt-contract)
   holds unchanged: one dispatch per reserved attempt, raw evidence recorded
   before parsing, a lost response stays unknown and blocks, and nothing is
   retried automatically (AGENTS.md invariant 5).
3. API keys never reach argv, recorded requests, worker inputs, exports, or the
   worker's environment. Records hold only a key's SHA-256.
4. The run pins the provider, model, endpoint, and request parameters. Resuming
   with a different configuration is refused, as it is for the local adapter.
5. Ordinary tests need no network or credentials.

## Proposal

### One adapter: `LiteLLMChatCompletions`

`LiteLLMChatCompletions(model, *, api_key_file, api_base=None, max_tokens, timeout_seconds, parameters={})`
implements the same model boundary as `LocalChatCompletions`: `model`,
`snapshot()`, `service_id`, `reserved_units`, `reservation(payload)`, and
`__call__(request, payload) -> AttemptResult`.

- `model` is a litellm model string, which names the provider:
  `anthropic/claude-opus-5-5`, `openai/gpt-5`, `openrouter/z-ai/glm-5.3-flash`,
  `huggingface/<org>/<model>`. Any OpenAI-compatible endpoint is
  `openai/<model>` with `api_base` set.
- Each call is `litellm.completion(...)` with `stream=False`,
  `num_retries=0`, `max_retries=0`, an explicit `timeout`, the key passed as
  `api_key`, and the pinned `parameters`. The adapter sets the provider SDK's
  retries to zero too, because litellm passes `max_retries` through to the
  OpenAI and Anthropic clients it builds.
- The output format is the worker's `{"command": string}` JSON object, requested
  through litellm's `response_format` with a JSON schema. How litellm honours it
  depends on what it believes the model supports; see
  [model capabilities](#model-capabilities-are-declared-not-looked-up).
- The two existing adapters stay. `LocalChatCompletions` remains the dependency-
  free path for Ollama with digest pinning, and `OpenRouterChatCompletions`
  remains the pinned route the M5 experiments recorded.

### litellm runs offline and silent

The adapter runs litellm in a child process, as `OpenRouterChatCompletions`
already does for HTTPS. This is the isolation boundary:

- **Environment.** The child gets a minimal environment:
  - `LITELLM_LOCAL_MODEL_COST_MAP=True`, so importing litellm never fetches the
    remote price table;
  - telemetry and callbacks off;
  - no inherited provider keys, so litellm cannot fall back to an
    `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the host.
- **Key.** It arrives on stdin, never in argv or the environment.
- **Deadline.** One deadline covers the whole child. At the deadline the host
  kills it, and the attempt is unknown. This does not prove the provider stopped.
- **Output.** The child prints one JSON result: the normalized response, the
  provider's raw response body when litellm exposes it, the response headers,
  the status, and usage. The host records these before parsing.

### Model capabilities are declared, not looked up

litellm decides how to send a request from its model map, a table of each
model's capabilities. Running offline, it uses the copy bundled with the installed
version, and that copy lags new models. In litellm 1.101.0 the bundled map has no
entry for `claude-opus-5-5` or `claude-sonnet-5-5`. For a model it does not know,
litellm asks for JSON output through a tool and forces the call with
`tool_choice: {type: "tool"}`. Claude Opus 5.5, Claude Sonnet 5.5 and Claude
Fable 5.1 reject forced tool use with a 400, so every request would fail. (For
Claude Opus 4.8, which the map does know, litellm uses native structured output.)

The adapter therefore declares the capabilities litellm needs for the run's
model, and the child registers them with `litellm.register_model` before the
call. The values are fields of the adapter's configuration, so they are pinned
and recorded with the run:

- `supports_native_structured_output` (send `output_format` rather than a
  forced tool);
- `supports_forced_tool_use`;
- `supports_output_config` (whether `effort` may be sent).

For Anthropic models the CLI fills these from a small table in the adapter, kept
with the model IDs it names; any other model must state them in `run.toml`. A
model with no declared capabilities is refused when the run is configured, not on
the first call. Upgrading litellm to a version whose bundled map knows the model
removes the need, but a declaration still wins, so behaviour never depends on
which map litellm happens to load.

### Evidence and its limits

The recorded request is the exact litellm call (model, messages, parameters,
endpoint) with the key replaced by its SHA-256. The recorded response is the
provider's raw body when litellm returns it, plus litellm's normalized
`ModelResponse`. When a provider's raw body is unavailable, the record says so,
and the normalized form is the evidence. That limit is stated, not hidden: the
bytes are litellm's, not the provider's, and AGENTS.md's "raw evidence retains its
origin" holds only as far as litellm reports the origin.

Outcomes follow the local adapter's table:

| Situation | Outcome |
| --- | --- |
| Child killed at the deadline, connection lost after sending | Unknown; the reservation is kept |
| Authentication, permission, bad request, not found (4xx except 429) | `infrastructure_failure`, one `model` unit |
| Rate limited (429) or overloaded (529) | `infrastructure_failure`, one `model` unit; never retried automatically |
| Missing or inconsistent usage | `infrastructure_failure`; the attempt is unmeasured |
| Refusal (Anthropic `stop_reason: "refusal"`, or a provider content filter) | `failed`, with the refusal kept; never a command |
| Truncated (`finish_reason: "length"` / `stop_reason: "max_tokens"`) | `failed`, as today |
| One text completion that parses as the command object | `succeeded` |

Thinking blocks and reasoning text are recorded but never become the command.
Only the final text block is parsed.

A 429 or 529 means the provider did not run the request. A later change may let
the host retry those as new, separately reserved attempts. This note does not.

### Cost

Token usage is recorded in `tokens.json`, as today. Money stays out of the ledger.
When the provider reports a cost (OpenRouter's `usage.cost`), it is recorded. The
adapter does not compute dollars from litellm's price table, which is a
convenience estimate, not a receipt.

Token budgets work as today: an unverified model reserves only `model` units, and
a model is verified only by a recorded measurement under `docs/experiments/`
([token budgets](../reference/model-adapters.md#token-budgets)). litellm's
normalized usage keys (`prompt_tokens`, `completion_tokens`) feed the same check.

### Keys

`run.toml` names a key file, never a key:

```toml
model = "anthropic/claude-opus-5-5"
provider = "litellm"
max_steps = 12

[adapter]
api_key_file = "runs/.secrets/anthropic"   # owner-only; read by the host
max_tokens = 4096
timeout_seconds = 300
# api_base = "https://router.huggingface.co/v1"   # for openai/<model> endpoints
# [adapter.parameters]                             # pinned, recorded
# effort = "medium"
```

The CLI refuses a key file readable by group or others. The run records the key's
SHA-256, and resume refuses a different key only if the project pinned one (an
open question below).

### Pinning

The run's `model-api.json` records the litellm model string, the litellm version,
`api_base`, `max_tokens`, `timeout_seconds`, the pinned `parameters`, the response
schema, and the key's SHA-256. `service_id` is `litellm/<sha256 of that record>`.
Upgrading litellm changes the service identity, so it refuses to resume a run that
started on another version, like any other changed adapter configuration.

### Dependency

litellm becomes a direct dependency, pinned to the version mini-swe-agent already
resolves (1.101.0 today), so the lockfile does not change. The model-adapters
reference changes its "no provider SDK" statement. litellm loads provider SDKs
(`openai`, and `anthropic` when installed) inside the child. Retries are disabled
at both layers, and nothing reaches the network outside the one request.

## Tests

- **Fast, no network.** litellm's `mock_response` and a fake provider exercise:
  - success, truncation, refusal and malformed output;
  - each HTTP class, missing usage and a killed child;
  - key handling: the key never appears in argv, records or exports; a
    group-readable file is refused; inherited env keys are ignored;
  - pinning: resume with a different model, parameters or litellm version is
    refused;
  - zero retries: a fake 500 or 429 produces exactly one request.
- **Opt-in live checks.** `-m live`, gated by an environment variable and a key
  file, makes one fixed request each to Anthropic and to an OpenAI-compatible
  endpoint, and records the result under `docs/experiments/`. CI never runs it.

## Open questions

1. **Codex.** Does "Codex" mean OpenAI models over Chat Completions (covered
   here), models served only on OpenAI's Responses API (litellm's `responses()`,
   a second call shape), or driving the Codex CLI as the worker (a different
   integration, out of scope)?
2. **Key pinning.** Should a run be bound to one key's SHA-256, so a rotated key
   refuses to resume, or only to the provider and model, so a rotated key
   resumes?
3. **litellm version.** Pin 1.101.0 (what mini-swe-agent resolves today) and
   declare capabilities as above, or move to a newer litellm whose bundled map
   knows the current Claude models, if mini-swe-agent's constraint allows it?
4. **Raw bodies.** If a provider's raw body is unavailable through litellm, is the
   normalized response acceptable evidence, or should that provider be refused?
