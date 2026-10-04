# litellm live check through OpenRouter, 2026-10-04

| | |
| --- | --- |
| Date | 2026-10-04 |
| Adapter | `LiteLLMChatCompletions`, litellm 1.104.0 ([reference](../reference/model-adapters.md)) |
| Model | `openrouter/z-ai/glm-5.3-flash` |
| Task | None: one connectivity request asking for `{"command": "true"}` |
| Kind | Development diagnostic |
| Outcome | Expired key: a known 401. Fresh key: succeeded with the exact command |
| Source | Runs 1–3 at `cb949a3` (`main` after #72); run 4 at `6138d93` (this test's exact instruction) |
| Environment | Python 3.12.12; litellm 1.104.0, openai 2.54.0, httpx 0.28.1 (from `uv.lock`); Linux 6.18.33.2-microsoft-standard-WSL2 |
| Total cost | $0.0003931 reported by OpenRouter (runs 3 and 4); runs 1 and 2 report no cost and are unknown, though a rejected key is not expected to be billed |

The check is the opt-in `tests/test_litellm.py::test_one_live_request`, run with
`WARRANTED_LIVE_TESTS=1`, an owner-only key file, and
`WARRANTED_LIVE_MODEL=openrouter/z-ai/glm-5.3-flash`. litellm does not report
native structured output for this model, so no schema was sent and the prompt
alone asked for the command object. Each run made one request with `max_tokens`
1024 and a 120-second timeout.

| Run | Key | Recorded outcome | Usage | Provider-reported cost |
| --- | --- | --- | --- | --- |
| 1 | Expired | `infrastructure_failure`: `AuthenticationError`, status 401, "API key expired" | 1 `model` unit; sent once | — |
| 2 | Expired (the key file had not been replaced) | Same as run 1 | 1 `model` unit; sent once | — |
| 3 | Fresh | `succeeded` in 20.5 s, but the reply was prose with an example JSON block, so `WorkerModel` refused it as a command | 16 prompt, 680 completion (556 reasoning) tokens | $0.0003424 |
| 4 | Fresh | `succeeded`; `response` was exactly `{"command": "true"}` | 28 prompt, 93 completion tokens | $0.0000507 |

What this establishes:

- **The real HTTPS path works from litellm's isolated child process.** That
  child has a stripped environment and no inherited certificate or proxy
  variables. Before this check, only loopback tests had run.
- **A provider error is a known failure, charged once and not retried.** The
  record holds the status, the headers and the error text, and no key material.
- **Reasoning stays out of the command.** Reasoning tokens are counted, but the
  `response` artifact holds only the final text.
- **Without a schema, the reply's shape depends on the prompt.** Run 3 used the
  unit tests' vague prompt ("Return a JSON command"), and the model answered in
  Markdown; the worker would have received a format error. Run 4 gave an exact
  instruction, and the reply parsed as a command. The live test now uses the
  exact instruction.

It does not establish model quality, task performance, or behaviour on any other
provider. Costs exclude everything but the reported inference charge, which the
recorded litellm response carries in `usage.cost`.
