# OpenRouter development diagnostic

The M5 runner accepts `--openrouter-model z-ai/glm-5.3-flash` with a host-only
`--api-key-file`. The adapter reuses the local chat request journal and response
parser. It adds HTTPS, provider selection, native credit limits, and recorded USD
costs. CI uses fake responses and requires no model credentials.
GLM requires reasoning on the selected endpoint. Requests enable reasoning and
keep its tokens inside the same output limit.

Each fresh request checks the key and provider metadata. The key must have
remaining credit and a positive limit without a reset period. The recorded limit
and key identity must remain unchanged. OpenRouter enforces its native credit
limit. Warranted records that limit but does not reserve dollars in its ledger.
Keys billed through another provider account (BYOK) are unsupported. The native
limit covers OpenRouter credits, not charges outside OpenRouter.

Requests select only `inference-net/fp4`, disable fallback, and require support for the
requested parameters. Price ceilings are $0.15 per million input tokens, $0.50 per
million output tokens, and no fixed request fee. The runner records the model,
provider metadata, pricing, request configuration, source files, tokens, and
`usage.cost`. Remote metadata does not establish an immutable model weight identity.

The key stays outside recorded requests, worker inputs, and exports. HTTPS runs
in a child process with a deadline that includes DNS, TLS, headers, and body reads.
The adapter uses no redirects or retries. If a response is lost, the attempt keeps
its reservation and blocks further work. Its paid cost remains unknown.
Completed requests reuse their recorded result without accessing the key or network.

## Run one migration-A diagnostic

Use a fresh directory and the existing pinned proof bundle. Store the key in a
file readable only by its owner, inside the ignored `runs/.secrets/` directory.
The first command prepares ten development slots. Run only migration-A for this
diagnostic. The other slots remain unexecuted.

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

If start reaches the forced kill, repeat the second command with `resume` and
without `--crash`. Repeat resume once more to measure reuse. If an attempt has an
unknown outcome, stop and retain the ledger. Do not launch a replacement request.

The command limit is recorded in each manifest. Twelve steps permit at most twelve
requests and shell commands per episode, or twenty-four across start and resume.
The default remains four. The shell container has a maximum lifetime of one hour.
Acceptance rules, independent checks, and fixture inputs remain the same.

Record elapsed time around the entire init, start, resume, and repeated resume
processes. Operation durations exclude setup, metadata checks, and other host work.
Record the proof bundle build separately if an existing bundle is reused.

```bash
uv run --locked python examples/m5/trials.py report runs/m5-openrouter
```

`model_cost_usd.reported_total` sums returned costs, including failed attempts.
`complete` is false when any dispatched attempt lacks its cost or any trial is
missing or unreadable. Costs exclude electricity, human effort, and development.
These public fixtures are development tasks. A single diagnostic does not establish
model quality, treatment differences, or results on unseen tasks.

API behavior follows OpenRouter's [authentication and key limits](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key),
[provider routing](https://github.com/openrouterteam/docs/blob/main/guides/routing/provider-selection.mdx),
and [usage accounting](https://github.com/openrouterteam/docs/blob/main/cookbook/administration/usage-accounting.mdx).
The requested model is [GLM-5.3-Flash](https://openrouter.ai/z-ai/glm-5.3-flash).

## Diagnostic attempts on September 24, 2026

Each attempt used a fresh ten-slot development plan and ran only migration-A.
All five attempts stopped before submitting a candidate. Their ledgers remain under
the ignored `runs/` directory. No acceptance result or restart success follows
from these attempts.

| Run directory under `runs/` | Source commit | Init seconds | Start seconds | Observed result |
| --- | --- | ---: | ---: | --- |
| `m5-glm-openrouter-20260924` | `ad8069d` | 2.109 | 4.424 | HTTP 400: selected endpoint requires reasoning |
| `m5-glm-reasoning-20260924` | `10f37a1` | 2.935 | 14.286 | Metadata deadline expired before inference |
| `m5-glm-reasoning-2-20260924` | `10f37a1` | 2.881 | 2.431 | DeepInfra returned HTTP 429 with `engine_overloaded` |
| `m5-glm-inferencenet-20260924` | `3e87615` | 1.856 | 110.431 | Second completion exhausted 3,072 tokens without a command |
| `m5-glm-8192-20260924` | `4eb701e` | 2.505 | 170.912 | Third response reported an InferenceNet internal server error |

The metadata failure recorded zero model usage and zero inference cost. The HTTP
400 and 429 responses omitted token and cost receipts. Their reports retain
incomplete cost accounting instead of inventing zero charges.

InferenceNet returned two billed responses totaling $0.00051356. They reported
1,684 input tokens and 3,143 completion tokens, or 4,827 total tokens. The first
response produced one inspection command. The second returned `finish_reason:
length` and null content. The original parser recorded infrastructure failure
for that response. Commit `4eb701e` classifies this case as failed truncation
and retains its token and cost receipts.

The provider also reported 3,664 reasoning tokens within the second response,
which exceeds its 3,072 completion-token count. The raw response retains this
inconsistency. Totals above use the top-level token fields and returned USD cost.

Timing records use a parent process around each complete command, including setup
and metadata requests. The existing proof bundle records a separate 333.971-second
build. These attempts reused that bundle and did not rebuild it.

The final attempt used 8,192 output tokens and a 300-second request deadline.
GLM recovered from a shell syntax error and inspected the fixture. Its third
response returned HTTP 200 with `finish_reason: error`, a nested 502 error,
and no command. The provider reported 5,386 reasoning tokens for that response.
This was a returned provider failure, not a host timeout or exhausted token limit.

That attempt reported 1,983 input tokens and 5,570 completion tokens, totaling
7,553 tokens. Its three responses reported $0.00003739 in OpenRouter credits.
The failed response reported zero credit cost despite a nonzero upstream cost.
The report uses the credit cost charged through OpenRouter.

Both InferenceNet runs together reported $0.00055095 and 12,380 tokens.
The key's final usage meter also reported $0.00055095, matching those receipts.
The earlier 400 and 429 responses still lack per-request cost receipts.
All five runs stopped before independent candidate assessment, forced restart,
or repeated resume. They retain all failed operations and have no unresolved
request reservations. The other nine slots in each plan remain unexecuted.

The larger budget did not establish GLM suitability because the provider failed
before delivering its next command. These observations justify improving provider
reliability before interpreting further attempts as a test of model ability.

The InferenceNet plan digests are
`873b4487f21429add8b1d7cf957673a4886fc94ad073feff0b5be1af13b6d854`
for 3,072 tokens and
`5d834cd786a08531cc04d37c6836b92e1be975ee7c166e7c31cabf1cef41f3e4`
for 8,192 tokens. Each run has `summary.json`. Its adjacent `-timing/` directory
contains the complete command, source commit, elapsed time, exit code, and raw
stdout and stderr for init and start.
