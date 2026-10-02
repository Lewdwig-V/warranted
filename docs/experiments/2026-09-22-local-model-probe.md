# Local model probe, 2026-09-22

| | |
| --- | --- |
| Date | 2026-09-22 |
| Model | `gemma4:26b` through Ollama 0.32.9 |
| Task | None: one connectivity request asking for `{"command":"true"}` |
| Kind | Development diagnostic |
| Outcome | Request timed out; the attempt stayed unknown and was not retried |

The probe used `examples/m5/local_model.py`, described in the
[model adapter reference](../reference/model-adapters.md#local-probe).

The first probe, run on 2026-09-22, used Ollama 0.32.9 and `gemma4:26b` (Q4_K_M,
digest `5571076f3d70050487b26b341705799e0ab29b808164f90d20d4cf84f699d251`). It
requested at most 64 output tokens and timed out after 120 seconds without a
response. The ledger kept an unknown attempt with one reserved model unit and no
token counts. A repeated invocation stopped at that unknown attempt without
another HTTP dispatch. This is a failed connectivity probe, not a model-quality
result.
