# Qwen development runs, 2026-09-23 to 2026-09-24

| | |
| --- | --- |
| Dates | 2026-09-23 (first, second, and input-naming attempts); 2026-09-24 (six- and twelve-command diagnostics) |
| Model | Local `qwen3.8:27b` through Ollama 0.34.2 |
| Task | Repository migration, condition A, one run per plan |
| Kind | Development diagnostic |
| Outcome | No run submitted `result.json`; no independent task check, revision, or forced restart ran |

How to initialize and run local-model plans is described in the
[context and treatment runner reference](../reference/contexts.md).

## First Qwen development attempt

Later [submission controls](2026-09-24-submission-controls.md) test whether the interface
lets Qwen submit completed work. Model comparisons are paused for this diagnostic.

On 2026-09-23, one local `qwen3.8:27b` migration-A run began under a pinned
development plan at `runs/m5-qwen-dev-20260923`. Ollama 0.34.2 reported
Q4_K_M model digest `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`.
The plan digest is `dc00f8acf4bf10d34be813baf3b5346a40d364080bdfc70227c11645d3d7e40c`;
its host code came from merge commit `a806b0f`.
The run used seed 0, a 1,536-token output limit, a 180-second request deadline,
and the four-command phase limit. The first episode made four successful model
requests and four successful shell calls. Its generated program passed the
public example and the model then ran more local checks, but it never submitted
`result.json`. The host stopped at the phase limit before the revision checkpoint.
No independent task check ran, so this is an unfinished task, not a task success.

The ledger records 5,214 prompt tokens, 1,349 completion tokens, 6,563 total
tokens, four spent model units, four spent tool units, and no reservations. The
sum of recorded model durations is 76.5 seconds and the sum of tool durations
is 35.1 seconds. These are operation times, not complete wall time or money.
The other nine prepared slots were not run, so the campaign is incomplete.
This attempt is development data and cannot become held-out evidence.

The follow-up development check below uses a new plan and the clearer first-command
instruction in the [treatment runner](../reference/contexts.md). Keep the same model, task, evaluator, seed, request limits,
four-command limit, host caps, and shell/file capabilities across A–E. Require
both episodes to submit candidates, the approved revision and forced restart to
recover, and a repeated resume to add no completed work. Count a missing
submission as failure even if public examples pass. Report independent task
checks, proof support, stale reuse, unknown attempts, tokens, spent units, and
setup costs separately. Start the ten-slot development matrix only after this
single-run recovery check; its public fixtures remain development data. A later
comparison needs separate task lineages, untouched final instances, repeated
runs, a fixed usefulness threshold, and a spending cap for any paid model.

## Second Qwen development attempt

After PR #28 merged at `f6ab1fc`, a fresh plan at
`runs/m5-qwen-dev-20260923-next` pinned digest
`e47d2e0b82631f30ce34805bf0d5f5da2b8621878f40de1667314623dc50e475`.
One migration-A run used the same local model and limits. The first command
inspected the named inputs; the second separately opened `tool-targets.json`
and listed the workspace. The third wrote `migrate.py`; the fourth tested it
against public examples and negative cases. The worker did not create
`result.json` or submit. No independent task check or forced restart ran.

The ledger records 6,144 prompt tokens, 1,088 completion tokens, 7,232 total
tokens, four spent model units, four spent tool units, and no reservations.
Recorded model operations total 13.9 seconds and tool operations 15.6 seconds;
these are not complete wall time or monetary cost. The campaign remains
incomplete. The failed run remains development evidence, not task success.

A further one-run experiment used a new plan at
`runs/m5-qwen-dev-20260923-inputs`, digest
`1117756db7b2b1a15352f591f4e521f3570a774935cf7ba440318dd130459114`.
Its temporary prompt named `tool-targets.json` and the workspace explicitly.
The complete temporary episode instruction, retained here because local run
ledgers are ignored by Git, was:

```text
Use at most four shell commands. In the first command, inspect task.md, context.json, tools.md, tool-targets.json, repository-files.json if present, and the workspace together. Create and test result.json, then submit it. The final command must start with `printf '%s\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT;` so the marker is the first stdout line. On continuation, use the restored workspace and current feedback.
```

The pinned `treatments.py` snapshot has SHA-256 digest
`1a8ba1fa5f2c84b06e51de45188a547cff171501ca1096d0726ca6b85624b3e7`;
the instruction above is the only source change from the merged version.
Qwen still used a second command to inspect empty workspace files. It wrote
`migrate.py` on command three. Its fourth model response exceeded the 1,536-token
limit while generating a long test command, so the model attempt failed and no
fourth shell command ran. The second shell command also exited 1 because the
inspected files were absent. No submission, task check, or forced restart ran.
The ledger records 6,358 prompt tokens, 2,174 completion tokens, 8,532 total
tokens, four spent model units, three spent tool units, and no reservations.
Recorded model operations total 23.2 seconds and tool operations 11.5 seconds.
This input-naming prompt did not solve the submission problem and is not retained.
All three local Qwen attempts remain development failures under the current
four-command, 1,536-token setup; none tests the revision/restart path.
Further development trials need a model that submits under a pinned plan before
the ten-slot matrix can start. A paid provider also needs a spending cap.

## Six-command Qwen diagnostic

On 2026-09-24, a single fresh migration-A run used plan digest
`202248a78aaa7434da532569d426f486ab7c6a5ded9a76c28e5389974471a32c`.
It kept the same model, seed, task, evaluator, and 180-second request deadline,
but allowed six commands per episode, twelve model and tool units across both
episodes, and 3,072 output tokens per model request. The pinned `treatments.py`
snapshot digest is
`5dd7b40aff3679e6d7cf008991314c24576c75d8c29325942d4b24a0cebaf6b2`.
Only the command limit, matching prompt wording, and model/tool caps differed
from the merged source. The exact episode instruction was:

```text
Use at most six shell commands. In the first command, inspect task.md, context.json, tools.md, repository-files.json if present, and the other task inputs together. Create and test result.json, then submit it. The final command must start with `printf '%s\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT;` so the marker is the first stdout line. On continuation, use the restored workspace and current feedback.
```

Qwen used six model and six shell actions. It inspected twice, wrote `migrate.py`,
ran the public example, ran more local checks, then wrote a sample `result.json`
inside `workspace/`. It never placed `result.json` at the work root or printed
the submission marker. The host stopped before the first independent task check
or forced restart. The second shell command exited 1 while reading absent files;
the other five succeeded. The ledger records 11,512 prompt tokens, 1,292
completion tokens, 12,804 total tokens, six spent model units, six spent tool
units, and no reservations. Recorded model operations total 28.5 seconds and
tool operations 24.0 seconds; complete wall time and monetary cost are not
measured. No response reached the output-token limit in this run, but the worker
did not submit. Each response also stayed below the previous 1,536-token cap, so
this run does not establish that raising that cap prevented truncation. The
temporary limit change was reverted.

## Twelve-command Qwen diagnostic

A fresh migration-A run at `runs/m5-qwen-twelve-20260924` used plan digest
`5fb083d707416080b697daef2e3fb0e3ace8f83dd1c9b4ddfbce71f78a6c4f0f`.
The model, seed, task, evaluator, 3,072-token output limit, and 180-second request
deadline stayed the same as the six-command diagnostic. This run allowed twelve
commands per episode and twenty-four model and tool units across both episodes.
The computed container lifetime was capped at the runtime's existing 3,600-second
maximum. Those limits and the matching instruction below were the only changes
to `treatments.py` from `f6ab1fc`; its pinned snapshot digest is
`84eda8b5fcbd6a5532aed5cd089121269460a8b7afe2eb31d83c10367fc6dc78`.

```text
Use at most twelve shell commands. In the first command, inspect task.md, context.json, tools.md, repository-files.json if present, and the other task inputs together. Create and test result.json, then submit it. The final command must start with `printf '%s\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT;` so the marker is the first stdout line. On continuation, use the restored workspace and current feedback.
```

Qwen inspected twice, wrote the migration and public example files, then tested
and inspected their output. Commands 10, 11, and 12 were byte-identical
`od -c | tail -3` inspections of two migration outputs. It never created
`result.json` or printed the submission marker. All twelve model responses ended
normally; none hit the output limit. The final HTTP request still contained the
original instructions. The second shell command exited 1 while reading absent
files; the other eleven succeeded. No independent task check or restart ran.

The ledger records 34,428 prompt tokens, 1,946 completion tokens, 36,374 total
tokens, twelve spent model units, twelve spent tool units, and no reservations.
Recorded model operations total 42.5 seconds and tool operations 45.7 seconds;
these exclude complete wall time and monetary cost. The temporary changes were
reverted. This run shows repeated actions under the larger budget; it does not
establish that Qwen can never submit with another budget or policy.

## Diagnostic cost scope

All five attempts above reused the same proof bundle. Each pinned plan retains
its original build time of 333,971,258,619 ns (334.0 seconds). That is shared setup
cost from an earlier build, not new work charged to each attempt. No new proof
bundle was built for these diagnostics.

Plan initialization, model loading, container and metadata overhead outside
recorded operations, and total trial elapsed time were not timed separately.
Human effort, electricity, and total monetary cost were also not measured.
These missing costs cannot be recovered from the operation-duration sums.
The runs therefore support observations about submission behavior under the
stated limits, not rankings of model speed or total cost. A future timing or
cost comparison must measure setup and the whole trial separately, including
failed runs, and state how shared setup costs are allocated.
