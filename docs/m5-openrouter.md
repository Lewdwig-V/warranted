# OpenRouter development diagnostic

The M5 runner accepts `--openrouter-model z-ai/glm-5.3-flash` with a host-only
`--api-key-file`. The adapter reuses the local chat request journal and response
parser. It adds HTTPS, provider selection, native credit limits, and recorded USD
costs. CI uses fake responses and requires no model credentials.

Each fresh request checks the key and provider metadata. The key must have
remaining credit and a positive limit without a reset period. The recorded limit
and key identity must remain unchanged. OpenRouter enforces its native credit
limit. Warranted records that limit but does not reserve dollars in its ledger.
Keys billed through another provider account (BYOK) are unsupported. The native
limit covers OpenRouter credits, not charges outside OpenRouter.

Requests select only `deepinfra/fp4`, disable fallback, and require support for the
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
