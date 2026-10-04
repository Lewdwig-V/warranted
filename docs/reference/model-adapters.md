# Model adapters

A model adapter is the trusted `model` boundary passed to
[`run_workflow`](worker-and-containment.md#running-an-episode). Each call makes
one external attempt for one journaled operation and returns the raw bytes and
known usage. The host records that result before the worker parses it. Warranted
provides four adapters, listed in the table below. None of them uses streaming,
automatic retries, or environment proxies. The first three use no provider SDK or
redirects; `LiteLLMChatCompletions` calls [litellm](https://github.com/BerriAI/litellm),
which loads the provider's SDK inside a child process with retries disabled at
both layers.

| Adapter | Module | Endpoint | Lost response | Cost recorded |
| --- | --- | --- | --- | --- |
| `ReceiptService` | `warranted.host` | Loopback fake service (`http://127.0.0.1:<port>`) | Reconciled from an exact receipt, or left unknown | Synthetic integer units |
| `LocalChatCompletions` | `warranted` | Local OpenAI-compatible `http://127.0.0.1:<port>/v1` (Ollama) | Unknown; cannot be reconciled | Attempts plus token counts |
| `OpenRouterChatCompletions` | `warranted` | `https://openrouter.ai/api/v1` | Unknown; cannot be reconciled; paid cost unknown | Attempts, tokens, and reported USD |
| `LiteLLMChatCompletions` | `warranted` | Any provider `litellm.completion` reaches: OpenAI-compatible APIs, the Anthropic API | Unknown; cannot be reconciled; paid cost unknown | Attempts plus token counts |

## Shared attempt contract

- The episode reserves each attempt before dispatch (see
  [dispatch and recovery](worker-and-containment.md#dispatch-and-recovery)). The
  `model` ledger unit counts attempts. Prompt and completion tokens are reserved
  only for [verified models](#token-budgets). Money is never a ledger unit.
- Each request records its service as the request producer, so the request digest
  binds the configured service identity. The adapter's configuration is pinned
  either as the project's `model-api` snapshot or, in the task layer, as the
  run's recorded `model-api.json`, which every model request cites. An adapter
  whose configuration differs from the pin refuses to send anything.
- Raw request and response bytes, status, and known usage are recorded before
  parsing. HTTP failures, malformed replies, and parse errors keep their charge.
- If a response is lost after dispatch, the attempt stays unknown, keeps its
  reservation through restarts, and blocks further dispatch. It is never retried
  automatically.
- A completed attempt is reused from the journal without another request.
- Only text in the `system`, `user`, and `assistant` roles reaches the wire. Worker
  metadata (mini's `extra`/`actions`) and the private ledger stay out of the
  prompt.

## Fake receipt service

`ReceiptService(url, service_id)` is a client for the loopback service in
`examples/m3/fake_service.py`. It is credential-free and exists to test operation
receipts.

- Each model call makes one HTTP POST to `/attempt` (5-second timeout, response at
  most 2 MiB). The service records every POST independently of the ledger and
  graph, and it counts duplicate requests as extra effects.
- The adapter rejects a different configured service before sending. The loopback
  port may change across restarts.
- Failed responses and parse errors keep their charges. Usage above the
  reservation is charged in full and blocks further work. The fixture's integer
  units are synthetic, not dollars or measured tokens.
- `reconcile(request)` (pass it as `run_workflow(..., reconcile=...)`) makes one
  read-only `GET /receipt/<digest>` for each unknown model attempt. It accepts only
  the exact service, project, operation, and full request digest with known usage.
  A missing receipt leaves the attempt unknown. Wrong identity, duplicate
  receipts, malformed data, and unknown usage cannot settle it. Lookups are logged
  independently, are free, and perform no model computation.

`tests/test_attempts.py` runs the service in another process. It covers lost
responses, repeated deaths during reconciliation, unprovable outcomes, unknown
usage, wrong identities, billed failures, parse errors, redirects, and cumulative
budget enforcement. The protocol assumes a trusted local service and provides no
production service authentication. A paid provider's reconciliation would need
its own reservation and usage contract.

## Local OpenAI-compatible endpoint

`LocalChatCompletions(base_url, model, max_tokens=256, timeout_seconds=120, seed=0, model_digest=None)`
sends one text request to `/v1/chat/completions`, following
[Ollama's compatibility API](https://docs.ollama.com/api/openai-compatibility).
It uses only Python's standard library.

- **Configuration.** `base_url` must be `http://127.0.0.1:<port>/v1`.
  `max_tokens` is 1–8192, `timeout_seconds` is 1–300, and `seed` is a nonnegative
  32-bit integer. `model_digest`, when given, is the installed model's SHA-256
  digest; only a model with a pinned digest can be
  [verified for token budgets](#token-budgets). The fixed parameters are `temperature: 0`, `stream: false`,
  `reasoning_effort: "none"`, and a JSON-object response format.
  `examples/m5/treatments.py` replaces the response format with a strict
  `{"command": string}` JSON schema.
- **Pinning.** The project snapshot `model-api` records the endpoint, the model
  tag, the seed, the output limit, the total timeout, the 2 MiB byte limit, and the
  fixed parameters. `service_id` is `local-chat-completions/<sha256 of that
  snapshot>`.
- **Deadline.** A single deadline covers connection, headers, and body reads. At
  the deadline the host shuts down that request's socket. This does not prove
  that server computation stopped. Request and response bodies are limited to
  2 MiB.

| Situation | Outcome | `model` units | Token units (verified models) | Retained |
| --- | --- | --- | --- | --- |
| Request body over 2 MiB | `failed`, not sent | 0 | 0 | Diagnostic |
| Timeout, dropped connection, oversized or short response | Unknown, reservation kept | Reserved | Reserved | — |
| Missing, invalid, or inconsistent token counts | `infrastructure_failure` | 1 | The full reservation | Request, response, status, error |
| Non-200 status; malformed JSON; different `model`; not exactly one choice; tool calls, refusal, or non-text content; unsupported finish reason | `infrastructure_failure` | 1 | Reported counts if they were read before the error, else the full reservation | Request, response, status, error |
| Reported completion tokens above `max_tokens` | `infrastructure_failure` | 1 | Reported counts (a breach) | Also `tokens.json` |
| `finish_reason: "length"` (truncated; null content is read as empty) | `failed` | 1 | Reported counts | Response text, `tokens.json` |
| `finish_reason: "stop"` | `succeeded` | 1 | Reported counts | `response`, `tokens.json` |

Token counts are stored in a separate `tokens.json` artifact. If they are missing,
the attempt is unmeasured and cannot complete successfully. Truncated output never
becomes a worker action. Malformed command text is recorded before the worker
rejects it.

### Token budgets

The ledger units `prompt_tokens` and `completion_tokens` bound what model calls
may spend ([design](../proposals/m7-token-budgets.md)). An adapter reserves them
only when its model is in the class's `verified_models`, keyed by
`model@model_digest` for `LocalChatCompletions`, by `model@provider_tag` for
`OpenRouterChatCompletions`, and by `model@api_base` for
`LiteLLMChatCompletions`. An Ollama tag can be repointed or re-pulled, so a
local model without a pinned `model_digest` is never verified, and before each
inference for a verified model the adapter reads `/api/tags` and settles an
`infrastructure_failure` with zero usage, without sending, unless the tag still
names exactly the pinned digest. A re-pull between that check and inference is
not detected. OpenRouter relies on its existing runtime preflight, which pins the
provider endpoint. **All three lists are empty.** A model is added only after
a recorded measurement under `docs/experiments/` shows its reported prompt tokens
within the bound, including for short prompts. For a local model,
`examples/m7/token_bound.py` takes that measurement:

```bash
uv run --locked python examples/m7/token_bound.py measure runs/token-bound \
  --model <installed-tag> --base-url http://127.0.0.1:11434/v1
```

It reads the tag's installed digest, checks it again before every case and once
after the last, and stops if the tag has moved. It sends ten fixed cases with
`max_tokens` 16. The cases cover empty, one-character, digit-dense, symbol-dense, whitespace,
non-ASCII, many tiny messages, and longer prose. It writes `report.json` and every
raw request and response, and the raw metadata responses that pin the digest.
A case counts only if its response passes the adapter's own checks: the same
model, and non-negative, consistent token counts. It exits 0 and prints the `model@digest` key only when
every case reported prompt tokens within the bound; a missing count fails the
measurement. The script only measures; adding the key is a separate reviewed
change.

For a verified model, `reservation(payload)` returns, before dispatch:

- `completion_tokens`: `max_tokens`;
- `prompt_tokens`: the byte length of the exact wire body, plus
  `MESSAGE_MARGIN` (16) for each message and once more for the generation prompt.

The bound rests on the model's tokenizer emitting at least one byte per ordinary
token; verification is evidence for that, not proof. Reported usage above the
reservation is charged in full, recorded as a breach, and blocks further dispatch
in its scope. Whether the model is verified, and the margin, are part of the
pinned `model-api` snapshot, so they change the service identity.

`reserved_units` declares the units an adapter reserves: all three for a verified
model, only `model` otherwise. An unverified model settles only `model` usage;
its token counts remain in `tokens.json` as evidence. A project whose allowances
include a token unit enforces it on every model call: the journal refuses, before
reserving anything, a service whose reservation omits such a unit or names units
it did not declare, and `Project.start` refuses such a service before the run
begins.

### Local probe

`examples/m5/local_model.py` checks connectivity against an installed Ollama model:

```bash
uv run --locked python examples/m5/local_model.py init runs/gemma-probe \
  --model gemma4:26b --max-tokens 64 --timeout 120
uv run --locked python examples/m5/local_model.py run runs/gemma-probe
uv run --locked python examples/m5/local_model.py run runs/gemma-probe
```

- `init` reads metadata only (`/api/version`, `/api/tags`, `/api/show`, 10-second
  deadline each) and makes no inference request. It records the server version,
  model digest, quantization, and configuration. It rejects cloud-backed models
  (`remote_host`/`remote_model`) and does not download models. It also snapshots
  every Warranted Python source file and the probe script. `run` rejects any
  source change before dispatch or reuse.
- `run` compares the metadata again before the single allowed inference request.
  It asks for `{"command":"true"}` and prints the result without executing it. A
  repeated `run` reuses the recorded response, even when Ollama is stopped.
- If metadata has changed or cannot be read, the host records an
  `infrastructure_failure` with zero model usage. The record keeps every metadata
  response and status, including malformed JSON, partial bodies, and a bounded
  prefix of oversized responses. The reservation is released, and later runs reuse
  the failure without polling Ollama again. Keep failed probe directories when you
  prepare a new probe.

The first probe's result is recorded in
[the 2026-09-22 local probe](../experiments/2026-09-22-local-model-probe.md).

The treatment runner uses the same adapter through `--local-model`. It compares
the metadata again before each new inference request (see
[contexts](contexts.md#live-models)).

### Limits

- The host trusts the local server and must prevent concurrent changes to the
  model or the server. Comparing metadata is not an atomic attestation of the
  weights used for generation.
- The compatible API does not set Ollama's context size. Server context defaults,
  prompt truncation, and hardware costs are outside these guarantees.
- Temperature 0 and a recorded seed do not make model output reproducible.
- The probe shows only connectivity and response handling. It does not measure
  task quality, run a treatment, or grant spending authority. Model downloads and
  inference stay out of ordinary tests and GitHub checks.

## OpenRouter

`OpenRouterChatCompletions` reuses the local request journal and response parser.
It adds HTTPS, provider pinning, native credit limits, and recorded USD costs. The
runners accept `--openrouter-model <id>` with a host-only `--api-key-file`. CI uses
fake responses and requires no credentials.

- **Request.** The request enables reasoning (the selected GLM endpoint requires
  it), and reasoning tokens count against the same output limit. The response
  format is the strict command schema. Provider routing is
  `order: ["inference-net/fp4"]` with `allow_fallbacks: false`,
  `require_parameters: true`, and price ceilings of $0.15 per million input
  tokens, $0.50 per million output tokens, and no request fee.
- **Key.** The key file must contain an `sk-or-v1-` key. It is read only by the
  host and sent over stdin to a child process that performs the HTTPS request.
  The key never appears in argv, recorded requests, worker inputs, or exports. The
  runtime snapshot records only its SHA-256. The child process has one deadline
  covering DNS, TLS, headers, and body reads, and it uses no redirects or retries.
- **Preflight.** The adapter needs a ledger with a pinned `runtime` snapshot and a
  begun model operation, so an unbound adapter cannot send inference. Before each
  new inference request, inside its dispatch boundary, it reads `/api/v1/key` and
  the model's endpoints and saves them as a `<operation>/preflight` record, with
  its own duration in `preflight.json`. The key must have positive remaining
  credit, a positive limit, and no reset period. The pinned key, credit limit,
  endpoint (InferenceNet, status 0, required parameters, enough completion tokens),
  and pricing must all be unchanged. Consumed credit may change. Any mismatch is
  an `infrastructure_failure` with zero model usage and `cost.json` of $0, and no
  inference is dispatched. If a preflight is already recorded, dispatch stops as
  unknown and is not repeated, so a lost inference response cannot erase the
  credit and routing evidence.
- **Billing receipt.** A response must report a finite, nonnegative `usage.cost`,
  `usage.is_byok: false`, provider `InferenceNet`, and a generation ID. The cost is
  stored as `cost.json` (scope "OpenRouter credits"). If any of these is missing,
  the attempt becomes an `infrastructure_failure` with the raw response kept.
- **Limits enforcement.** OpenRouter enforces its native credit limit. Warranted
  records that limit but does not reserve dollars in its ledger. Keys billed
  through another provider account (BYOK) are unsupported, and the native limit
  covers only OpenRouter credits. Remote metadata does not establish an immutable
  model weight identity.
- **Lost response.** The attempt keeps its reservation and blocks further work,
  and its paid cost stays unknown. Completed requests are reused without reading
  the key or touching the network.

API behavior follows OpenRouter's
[authentication and key limits](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key),
[provider routing](https://github.com/openrouterteam/docs/blob/main/guides/routing/provider-selection.mdx),
and [usage accounting](https://github.com/openrouterteam/docs/blob/main/cookbook/administration/usage-accounting.mdx).
The tested model is [GLM-5.3-Flash](https://openrouter.ai/z-ai/glm-5.3-flash).

### Run one migration-A diagnostic

Use a fresh directory and an existing pinned proof bundle (see
[proof verification](proof-verification.md)). Store the key in a file readable
only by its owner, inside the ignored `runs/.secrets/` directory. `init` prepares
ten development slots. Run only migration-A; the other slots stay unexecuted.

```bash
uv run --locked python examples/m5/trials.py init runs/m5-openrouter \
  --bundle runs/m4-tools/bundle.json --repetitions 1 \
  --openrouter-model z-ai/glm-5.3-flash \
  --api-key-file runs/.secrets/openrouter-api-key \
  --max-steps 12 --max-tokens 3072 --timeout 180

uv run --locked python examples/m5/treatments.py start \
  runs/m5-openrouter/runs/001-migration-A \
  --family migration --condition A --bundle runs/m4-tools/bundle.json \
  --openrouter-model z-ai/glm-5.3-flash \
  --api-key-file runs/.secrets/openrouter-api-key \
  --max-steps 12 --max-tokens 3072 --timeout 180 --crash
```

If `start` reaches the forced kill, run the second command again with `resume` and
without `--crash`. Run `resume` once more to measure reuse. If an attempt has an
unknown outcome, stop and keep the ledger; do not launch a replacement request.

`--max-steps` is recorded in each manifest (default 4). Twelve steps allow at
most twelve requests and twelve shell commands per episode, or twenty-four across
start and resume. The shell container's lifetime is capped at one hour.
Acceptance rules, independent checks, and fixture inputs do not change. Time the
whole init, start, resume, and repeated-resume processes from outside. Recorded
inference durations exclude setup, metadata checks, and other host work. Record
the proof bundle build separately if you reuse an existing bundle.

```bash
uv run --locked python examples/m5/trials.py report runs/m5-openrouter
```

`model_cost_usd.reported_total` sums the returned costs, including those of failed
attempts. `complete` is false when any dispatched attempt lacks its cost or any
trial is missing or unreadable. Costs exclude electricity, human effort, and
development. These public fixtures are development tasks, so a single diagnostic
does not establish model quality, treatment differences, or results on unseen
tasks. The recorded attempts are in the
[OpenRouter GLM diagnostic](../experiments/2026-09-24-openrouter-glm-diagnostic.md).

## litellm

`LiteLLMChatCompletions(model, *, api_key_file, max_tokens, timeout_seconds, api_base=None, parameters={})`
makes one [`litellm.completion`](https://docs.litellm.ai/docs/completion) call per
attempt. It is the task layer's path to hosted models. The
[design note](../proposals/m7-model-providers.md) records why litellm and how it is
contained.

- **Model.** A litellm model string that names its provider:
  `anthropic/claude-opus-5-5`, `openai/gpt-5`, `openrouter/<org>/<model>`. Any
  OpenAI-compatible endpoint is `openai/<model>` with `api_base`.
- **Configuration.** `max_tokens` is 1–131072 and `timeout_seconds` 1–600.
  `api_base` is an http(s) URL without credentials. `parameters` are extra
  JSON-valued `completion` arguments from an allowlist of sampling and model
  settings: `temperature`, `top_p`, `top_k`, `seed`, `stop`,
  `presence_penalty`, `frequency_penalty`, `reasoning_effort`, `thinking`, and
  `user`. Any other name is refused at construction, because litellm arguments
  such as `retry_policy`, `extra_body`, or `context_window_fallback_dict` can
  re-enable retries, reroute the call, or change what is sent. Anthropic's
  effort setting is reached through `reasoning_effort`.
- **Key.** `api_key_file` names a file that is readable only by its owner and
  holds one key of letters, digits, and `._~+/=-`. The adapter refuses a
  missing, empty, or group- or other-readable file, at construction and again
  before each call. The key goes to the child process over stdin. It never
  appears in argv, the child's environment, recorded requests, worker inputs, or
  exports. No digest of it is recorded, so a rotated key resumes a run. Before
  the child's output is recorded, the whole key is replaced with `[api key]`
  wherever it appears. Its first 8 and last 4 characters are replaced only where
  a provider masked the key around them (`sk-...abcd`, `sk-proj-****abcd`,
  `sk-abcdefgh...`), so the same characters appearing by chance elsewhere, such
  as inside a signature, stay as recorded. For a key shorter than 12 characters
  only the whole key is replaced. Other fragments a provider might echo are not
  detected.
- **Child process.** Each call runs `python -I` with an environment that holds
  only `LITELLM_LOCAL_MODEL_COST_MAP`, `LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS`,
  `LITELLM_LOCAL_BLOG_POSTS`, `LITELLM_LOCAL_AUTOROUTER_PRESETS`, and
  `LITELLM_LOCAL_POLICY_TEMPLATES` (all `True`), `LITELLM_MODE=PRODUCTION`, and
  `LITELLM_TELEMETRY=False`. litellm uses its bundled copies of the files it
  would otherwise fetch from GitHub; without them, every Anthropic call that
  sends an `anthropic-beta` header, including native structured output, fetches
  the beta-header table. It does not load a `.env` file and sees no inherited
  provider keys or proxies. A test routes the child through a recording proxy
  and checks that nothing but the provider request is made. Callbacks are
  cleared. The call sets `stream=False`, `num_retries=0`, `max_retries=0`, and
  `timeout=timeout_seconds`. The host kills the child at `timeout_seconds` plus
  20 seconds for start-up. This does not prove the provider stopped.
- **Structured output.** At construction, a child with the same environment asks
  litellm's `get_model_info` whether the model has
  `supports_native_structured_output`. Only then does the call send a
  `response_format` with the strict `{"command": string}` schema. Otherwise it
  sends none, so litellm never falls back to a forced tool call. The prompt still
  asks for the command object, and the worker rejects malformed replies as usual.
  An unmapped model counts as not native. litellm 1.104.0 reports native
  structured output for `anthropic/claude-opus-5-5` but not for `openai/gpt-5`
  or `anthropic/claude-sonnet-5-5`.
- **Pinning.** The `model-api` record holds the model string, the litellm,
  `openai`, and `httpx` versions the child imports, `api_base`, `max_tokens`, `timeout_seconds`,
  `parameters`, whether structured output is sent, the 2 MiB byte limit, and the
  token-bound settings. `service_id` is `litellm/<sha256 of that record>`.
  Resuming with any change, including another package version, is refused. The
  key file path is not pinned.
- **Records.** `litellm-request.json` is the exact call without the key.
  `litellm-response.json` is the child's result: litellm's normalized response
  (`model_dump()`), the response headers litellm exposes, and, on failure, the
  HTTP status and the error class and message. litellm does not report the
  status of a successful call. `tokens.json` holds the normalized usage, and
  `response` holds only the final message text. Thinking blocks and reasoning
  text remain in `litellm-response.json` and never become the command. The
  response's `model` is recorded but not compared, because providers return
  dated aliases.

| Situation | Outcome | `model` units | Retained |
| --- | --- | --- | --- |
| Request over 2 MiB | `failed`, not sent | 0 | Diagnostic |
| Key file missing or unsafe at call time | `infrastructure_failure`, not sent | 0 | Diagnostic |
| Child killed at the deadline, timeout, connection lost (a transport error anywhere in the exception's cause chain), or HTTP 408 or 504 (litellm raises `litellm.Timeout`) | Unknown, reservation kept; the exception names the error class and status | Reserved | — |
| Any other HTTP error (4xx, 429, 500, 502, 503, 529), or another litellm error | `infrastructure_failure`; never retried | 1 (a verified model is charged its full token reservation, because the upstream may have run) | Request, result, error |
| Malformed provider reply, or missing, zero-prompt, or inconsistent usage | `infrastructure_failure` | 1 | Request, result, error |
| Tool calls, no text, more than one choice, or an unsupported finish reason | `infrastructure_failure` | 1 | Request, result, `tokens.json`, error |
| Refusal (Anthropic `stop_reason: "refusal"`, `finish_reason: "content_filter"`, or an OpenAI `refusal`) | `failed` | 1 | Also `refusal`, `response`, `tokens.json` |
| Truncated (`finish_reason: "length"`) | `failed` | 1 | `response`, `tokens.json` |
| One text completion (`finish_reason: "stop"`) | `succeeded` | 1 | `response`, `tokens.json` |

litellm fills absent usage with zeros, so a reply that reports zero prompt
tokens is treated as unmeasured. litellm reports a dropped connection as an HTTP
500, so the child checks the error's cause chain for a timeout or transport
error; only a chain without one counts as a received reply. A 429 or 529 means
the provider did not run the request, but the adapter still never retries it.
Token budgets follow the [verified-models rule](#token-budgets), keyed by
`model@api_base` (empty after `@` when no `api_base` is set): a measurement
holds for one model at one endpoint. The list is empty, so only `model` units are reserved. Money stays
out of the ledger, and the adapter does not compute dollars from litellm's price
table.

`tests/test_litellm.py` runs the real litellm against a local fake provider that
speaks both the Chat Completions and the Messages wire formats. Every HTTP error
class reaches the server exactly once. One opt-in test, marked `live`, makes a
single real request:

```bash
WARRANTED_LIVE_TESTS=1 WARRANTED_LIVE_KEY_FILE=runs/.secrets/anthropic \
WARRANTED_LIVE_MODEL=anthropic/claude-opus-5-5 \
  uv run --locked pytest -q -m live tests/test_litellm.py
```

Set `WARRANTED_LIVE_API_BASE` for an `openai/<model>` endpoint. CI never sets
these variables. The first live runs, through OpenRouter, are recorded in the
[2026-10-04 live check](../experiments/2026-10-04-litellm-openrouter-live-check.md).

### Limits

- litellm's normalized response is the recorded evidence. Normalization can drop
  provider-specific fields, and the raw provider bytes are not kept.
- The child's environment drops `SSL_CERT_FILE`, `SSL_CERT_DIR`, and the proxy
  variables. A corporate CA or a required proxy is unsupported. The default
  suite exercises only plain HTTP to a loopback server; the HTTPS path to a real
  provider is tested only by the opt-in live test, which has passed against
  OpenRouter.
- A lost attempt cannot be recorded without settling it, so its error class and
  status are only in the raised exception, not the ledger.
- The bundled model map lags new models. A newer litellm is taken by upgrading
  the pin, which changes every run's service identity.
- Distinguishing a lost connection from a received error depends on litellm
  keeping the transport error in the exception chain, as 1.104.0 does for the
  OpenAI and Anthropic paths.
- Remote providers give no immutable model identity, and output is not
  reproducible.
