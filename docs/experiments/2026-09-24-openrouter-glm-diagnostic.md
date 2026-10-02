# OpenRouter GLM diagnostic, 2026-09-24

| | |
| --- | --- |
| Date | 2026-09-24 |
| Model | `z-ai/glm-5.3-flash` through OpenRouter |
| Task | Repository migration, condition A, one run per plan |
| Kind | Development diagnostic |
| Outcome | All five attempts stopped before submitting a candidate; none reached independent assessment or forced restart |

Setup: each attempt initialized a fresh plan with `examples/m5/trials.py init` and
ran `examples/m5/treatments.py start` for migration-A only, with
`--openrouter-model z-ai/glm-5.3-flash`, a host-only `--api-key-file`, and the
existing pinned proof bundle `runs/m4-tools/bundle.json`. The documented
diagnostic command used `--max-steps 12 --max-tokens 3072 --timeout 180`; the
final attempt used 8,192 output tokens and a 300-second request deadline, as
recorded below. The adapter changed between the source commits listed in the
table. The current adapter, including provider selection, credit-limit checks,
and cost receipts, is described in the
[model adapter reference](../reference/model-adapters.md).

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
That older metadata failure counted preflight time as model-operation time.
Current code records zero inference time for this case and saves preflight time separately.
The parent-process times in the table include both scopes and remain valid.

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
