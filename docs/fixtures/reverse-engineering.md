# Reverse-engineering-style fixture

The third task family is a minimal stand-in for ReSchema's work: model a black-box
program well enough to reproduce its output on inputs the worker never sees. It
is M7's completion evidence that the task layer carries the pieces ReSchema needs,
all through the public API (`warranted`). It contains no reverse-engineering
concepts in the core: the program, its hidden cases, and the replay checker live
in the domain ([`examples/m7/mystery`](../../examples/m7/mystery)). Candidates are
fixed or scripted; nothing here measures model quality.

## Task family

`mystery.py` reads text and writes a run-length encoding with vowels in upper
case. The worker reads it, runs it, or asks the host to run it with the `probe`
[operation](../reference/worker-and-containment.md#host-mediated-operations), then
submits `result.json`: a `model` program and up to 16 nominated `cases`
([`task.md`](../../examples/m7/mystery/task.md)).

The `replay` checker runs the original program and the model in separate
[checker jobs](../reference/worker-and-containment.md#checker-jobs) on the
nominated cases plus eight hidden cases. It draws the hidden cases from a
host-recorded seed and the private `hidden.json`, and recomputes every expected
output from the original program, so nothing the worker recorded is evidence.
Feedback shows the first divergence on a nominated case and only a count for
hidden cases. On acceptance, the checker records a fact binding the model to the
program's digest.

| Task file | Program | Purpose |
| --- | --- | --- |
| `family-first.toml` | `mystery.py` | Probes, a rejected model, a refused repeat, then acceptance |
| `family-second.toml` | `mystery.py` | Receives the first task's verified model and promoted note |
| `family-rerecorded.toml` | `mystery-v2.py` (vowels unchanged) | The same facts are stale and withheld |

All three share the memory scope `rle-family` with a dependency on `mystery.py`,
a [duplicate guard](../proposals/m7-harness-api.md#budgets-and-the-duplicate-guard)
that refuses an exact or near repeat of a rejected model, and budgets of three
submissions and three checks. `task.toml` is the original single task without
memory or guard.

## What the tests establish

[`tests/test_re_fixture.py`](../../tests/test_re_fixture.py) runs jobs locally;
its container test runs a worker in a domain image.

| Requirement | Test |
| --- | --- |
| Checker private inputs, worker-nominated cases, repeated-candidate guard, scoped memory across restart | `test_a_family_shares_verified_facts_across_a_restart`: the first run is rejected, refused as a duplicate without a check, then accepted; a reopened project's second task sees the verified model and the promoted note, not the rejected note; the re-recorded task withholds all three entries as stale |
| Leaked private inputs | `test_private_inputs_never_reach_the_worker_or_an_export`: `hidden.json`, the drawn hidden cases, and the seeds appear in no episode file, model input, or export |
| Forged verdicts | `test_forged_verdicts_receipts_and_tiers_establish_nothing`: a printed verdict, workspace files named like feedback and decisions, and notes claiming the verified tier leave three host-recorded rejections and nothing in the family's memory |
| Bypassed budgets | `test_budgets_hold_against_a_worker_that_tries_to_exceed_them`: eight probe requests under a cap of two run two jobs; a fourth submission is never taken; a restart resets nothing and reruns nothing |
| Domain worker image | `test_a_worker_uses_a_tool_from_the_domain_image` (container): an image built from [`Containerfile`](../../examples/m7/mystery/Containerfile) adds a `mystery` tool; the worker runs it and its submission is accepted |

```bash
uv run --locked pytest -q tests/test_re_fixture.py
WARRANTED_CONTAINER_TESTS=1 uv run --locked pytest -q -m container tests/test_re_fixture.py
```

The container test builds its image from the pinned Python image with
`--pull=never`, so pull that image first
([worker and containment](../reference/worker-and-containment.md)).

## Limits

- Hidden cases are random strings over a small alphabet; passing them does not
  establish equivalence with the program on every input.
- The leak test searches for the private file, the seeds, and hidden cases of
  eight or more characters; shorter cases are too common to search for.
- Single trusted local writer. The worker image adds tools only; containment is
  that of the [worker sandbox](../reference/worker-and-containment.md#container-sandbox).
