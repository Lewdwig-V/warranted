# Model adapters

A model adapter is the trusted `model` boundary passed to
[`run_workflow`](worker-and-containment.md#running-an-episode). Each call makes
one external attempt for one journaled operation and returns the raw bytes and
known usage. The host records that result before the worker parses it. Warranted
provides three adapters, listed in the table below. None of them uses a provider
SDK, streaming, automatic retries, redirects, or environment proxies.

| Adapter | Module | Endpoint | Lost response | Cost recorded |
| --- | --- | --- | --- | --- |
| `ReceiptService` | `warranted.attempts` | Loopback fake service (`http://127.0.0.1:<port>`) | Reconciled from an exact receipt, or left unknown | Synthetic integer units |
| `LocalChatCompletions` | `warranted.chat_completions` | Local OpenAI-compatible `http://127.0.0.1:<port>/v1` (Ollama) | Unknown; cannot be reconciled | Attempts plus token counts |
| `OpenRouterChatCompletions` | `warranted.openrouter` | `https://openrouter.ai/api/v1` | Unknown; cannot be reconciled; paid cost unknown | Attempts, tokens, and reported USD |

## Shared attempt contract

- The episode reserves its per-attempt allowance before dispatch (see
  [dispatch and recovery](worker-and-containment.md#dispatch-and-recovery)). The
  `model` ledger unit counts attempts, not tokens or money.
- Each request records its service as the request producer, so the request digest
  binds the configured service identity. An adapter whose configuration differs
  from the pinned request refuses to send anything.
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

`LocalChatCompletions(base_url, model, max_tokens=256, timeout_seconds=120, seed=0)`
sends one text request to `/v1/chat/completions`, following
[Ollama's compatibility API](https://docs.ollama.com/api/openai-compatibility).
It uses only Python's standard library.

- **Configuration.** `base_url` must be `http://127.0.0.1:<port>/v1`.
  `max_tokens` is 1–8192, `timeout_seconds` is 1–300, and `seed` is a nonnegative
  32-bit integer. The fixed parameters are `temperature: 0`, `stream: false`,
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

| Situation | Outcome | `model` units | Retained |
| --- | --- | --- | --- |
| Request body over 2 MiB | `failed`, not sent | 0 | Diagnostic |
| Timeout, dropped connection, oversized or short response | Unknown, reservation kept | Reserved | — |
| Non-200 status; malformed JSON; different `model`; missing, invalid, or inconsistent token counts; not exactly one choice; tool calls, refusal, or non-text content; unsupported finish reason | `infrastructure_failure` | 1 | Request, response, status, error |
| Reported completion tokens above `max_tokens` | `infrastructure_failure` | 1 | Also `tokens.json` |
| `finish_reason: "length"` (truncated; null content is read as empty) | `failed` | 1 | Response text, `tokens.json` |
| `finish_reason: "stop"` | `succeeded` | 1 | `response`, `tokens.json` |

Token counts are stored in a separate `tokens.json` artifact. If they are missing,
the attempt is unmeasured and cannot complete successfully. Truncated output never
becomes a worker action. Malformed command text is recorded before the worker
rejects it.

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
