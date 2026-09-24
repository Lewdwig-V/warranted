# M5 submission controls

The early Qwen runs mixed two questions: can the worker solve the task, and can it submit its work?
On 2026-09-24, we paused model comparisons to test the submission interface.
These controls use development data. They do not complete M5 or measure differences between models.

The original worker instructions named `result.json` but omitted its required structure:
`{"migrate.py": "<complete Python source>"}`.
The six-command Qwen run instead wrote the migrated configuration into `workspace/result.json`.
The worker also received no count of its remaining turns.
After a run stopped without submission, the host removed its container without saving the workspace.

The OpenRouter attempts ended before useful task work or submission.
They do not establish a third model failure at the same transition.
The Qwen records provide direct evidence of the interface problem.

## Controls

[The diagnostic runner](../examples/m5/diagnostics.py) reuses mini-swe-agent,
LangGraph, the rootless container, and the migration checker.
It changes the worker interface in two separate controls:

| Control | Worker task | Interface change |
| --- | --- | --- |
| Submission only | Submit a supplied valid candidate | Explicit copy instruction and first-line marker instruction |
| Plain task loop | Solve the original migration task | Source payload schema, submission helper, and remaining model turns |

The helper packages `workspace/migrate.py` into `/work/result.json` and prints the marker.
It does not run tests or grant acceptance.
The task checker applies the original initial contract after submission.
The plain control changes the completion instructions together, so it cannot isolate the effect of each change.

The host appends the remaining-turn count to each model request.
The count includes the current turn and counts malformed responses.
This matches mini-swe-agent's
[call-based step limit](https://github.com/SWE-agent/mini-swe-agent/blob/main/docs/advanced/control_flow.md).
The ledger retains that feedback as part of the request.

For an unfinished episode, the diagnostic runner stops worker processes and captures bounded files before cleanup.
It records those files as `unfinished/*`, separate from `candidate/*`.
It can assess an existing payload and separately package retained source for an independent check.
A passing diagnostic check does not change the episode's submission status.
Non-UTF-8 source receives an `unsupported` diagnostic result.
The original bytes remain captured, and repeated reporting does not repeat the worker.
The capture uses the existing file, link, size, and process isolation checks.
In unfinished mode, an invalid payload records a separate error without discarding a valid workspace.
An invalid workspace also preserves a safely captured payload.
If marked submission fails capture, the container remains available until correction or episode cleanup.
An unknown external outcome remains blocked.

## Local measurements

Both controls use local `qwen3.8:27b`, Ollama 0.34.2, temperature 0, seed 0,
a 3,072-token output limit, a 180-second request deadline, and at most 12 model turns.
The model digest is `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`.
These match the twelve-turn development attempt's model configuration and budget.
The implementation commit is `d89b666`.
No paid requests ran during these controls.

The submission-only control submitted in one model turn and one shell command.
The checker accepted it under all five initial obligations.
The parent process measured 0.62 seconds for initialization and 45.51 seconds for execution and assessment.
One success establishes that Qwen can follow the explicit protocol in this case.
It does not establish reliable submission across tasks.

The plain task control submitted on turn nine and passed all five initial obligations.
It inspected the inputs, wrote source, ran tests, corrected its source, and invoked `python /work/submit.py`.
The parent process measured 0.78 seconds for initialization and 75.26 seconds for execution and assessment.
It spent nine model units, nine tool units, one batch unit, and one checker unit.
Both controls ended with zero unresolved reservations.

| Control | Prompt tokens | Completion tokens | Total tokens |
| --- | ---: | ---: | ---: |
| Submission only | 93 | 31 | 124 |
| Plain task loop | 27,470 | 2,224 | 29,694 |

The submission-only run spent 30.30 seconds in model operations and 6.61 seconds in shell operations.
The plain task loop spent 28.91 seconds and 36.03 seconds in those operations.
These partial times exclude metadata, setup, and assessment work.
Local electricity and human development costs are unmeasured.

The original twelve-turn run never submitted, although its reconstructed program now passes the same initial contract.
This controlled change demonstrates a working completion path for Qwen on this task.
It does not prove which individual interface change caused the improvement.
The next development slice must apply a clear submission contract before any further model comparison.
Then the same model must attempt the approved revision and recovery sequence.

## Inspecting earlier work

Five new diagnostic ledgers assess source recovered from earlier Qwen records.
The task inputs, contracts, reference cases, and evaluator source match the original recorded bytes.
The new ledgers record the current container host source and environment separately.
The shared process runner now accepts an executable parameter whose default remains Podman.
The new optional workspace capture does not change the task checker.

Two completed tools printed their source files in full.
For the other three runs, inspection extracts source from recorded heredoc commands without executing those commands.
Those three programs are reconstructions of proposed source, not retained final files.
All five original episodes remain unsubmitted.

| Original run directory | Source evidence | Initial contract |
| --- | --- | --- |
| `m5-qwen-dev-20260923` | Recorded source command | Accepted |
| `m5-qwen-dev-20260923-next` | Source printed by tool 3 | Accepted |
| `m5-qwen-dev-20260923-inputs` | Source printed by tool 3 | Rejected |
| `m5-qwen-generous-20260924` | Recorded source command | Accepted |
| `m5-qwen-twelve-20260924` | Recorded source command | Accepted |

The rejected program fails renaming, output schema, and legacy-label preservation.
Four programs pass the unchanged initial contract despite their original runs never submitting.
This supports a completion-interface problem alongside occasional task failures.
It does not prove whole-task success, because the revision and recovery stages did not run.

Local evidence is under `runs/m5-harness-diagnostics-20260924/`.
The `submission/` and `plain/` directories hold control ledgers and reports.
The `inspected/` directories hold new assessments, original observation references, source bytes, and checker identities.
The one-off extraction script is retained beside those ledgers as `inspect_recordings.py`.
The `timing/` directory holds parent-process measurements and raw command output.
These ignored local files are not part of the Git repository.

## Reproduce the controls

Prepare the pinned container and proof bundle using the existing M5 instructions.
Use a new output directory for each control.
Replace `submission` with `plain` to run the full task control.

```bash
uv run --locked python examples/m5/diagnostics.py init runs/control-submission \
  --bundle runs/m4-tools/bundle.json --control submission \
  --local-model qwen3.8:27b --max-tokens 3072 --timeout 180 --max-steps 12
uv run --locked python examples/m5/diagnostics.py run runs/control-submission \
  --bundle runs/m4-tools/bundle.json --control submission \
  --local-model qwen3.8:27b --max-tokens 3072 --timeout 180 --max-steps 12
```

Do not change pinned source, model settings, or fixture bytes between initialization and execution.
Repeated execution reuses recorded work.
Live controls remain opt-in and never run in CI.
Offline tests cover source packaging, turn counts after malformed responses, and recorded-call reuse.
A native container test proves that a passing unfinished program remains unsubmitted.

## Isolating the interface changes

The next diagnostic uses six variants of the original task objective.
It keeps the original task files, model settings, twelve-turn limit, and checker.
It does not use the rewritten objective from `plain`.

| Control | Added instructions | Helper file | Remaining turns |
| --- | --- | --- | --- |
| `original` | None | No | No |
| `original-budget` | None | No | Yes |
| `schema` | Payload schema, root path, source location | No | No |
| `schema-budget` | Same schema instructions | No | Yes |
| `helper` | Same schema instructions plus helper command | Yes | No |
| `helper-budget` | Same schema and helper instructions | Yes | Yes |

The countdown contains no submission reminder or helper command.
The schema instructions distinguish Python source from its output configuration.
The helper instructions replace the manually written final marker command.
This separates countdown feedback from completion instructions.
The helper contrast measures its added value once the schema is explicit;
it does not measure an undocumented helper or every possible interaction.

Before any model calls, we fix this order for twelve fresh ledgers:
`original`, `schema`, `helper`, `original-budget`, `schema-budget`, `helper-budget`,
then the same six in reverse order.
Each run uses local Qwen with the settings recorded above.
Both repetitions keep seed zero; they test repeatability, not a distribution of seeds.
The GPU must be available before starting, unless the user authorizes contention.
We retain failed attempts and stop on unknown external outcomes or infrastructure failure.
We do not spend paid API credits or put model inference in CI.

Record submission, initial acceptance, independently checked unfinished artifacts,
model and tool calls, tokens, parent-process elapsed time, and reservations for every run.
A contrast must repeat before we use it to choose an interface correction.
Two repetitions of one development task do not establish general reliability.
These variants diagnose the original interface; they do not decompose every wording
change in the earlier `plain` control or complete the revision/recovery experiment.

Use the same commands above with the desired `--control` and a fresh run directory.

## Results of the twelve-run comparison

The twelve runs finished on 2026-09-24 using implementation commit `5a771dd`.
All task, checker, model, and runtime snapshots match across runs.
The control selection is the only environment difference.
Both repetitions use seed zero, but their trajectories differ.
These are observations on one development task, not estimates of general reliability.

The table separates a checker-passing source version, submission of a payload, and one additional diagnostic requirement.
The host ran all five independent checks after each episode.
The worker saw its own test output, but never saw those independent verdicts.

| Run | Control | First checker-passing source turn | Submitted payload | Final program rejects boolean version |
| --- | --- | ---: | --- | --- |
| 01 | `original` | 3 | Not submitted | No |
| 02 | `schema` | 3 | Accepted | Yes |
| 03 | `helper` | 3 | Accepted | No |
| 04 | `original-budget` | 2 | Accepted | No |
| 05 | `schema-budget` | None recovered | Not submitted | No |
| 06 | `helper-budget` | 3 | Accepted | No |
| 07 | `helper-budget` | 2 | Accepted | No |
| 08 | `schema-budget` | 5 | Accepted | No |
| 09 | `original-budget` | 3 | Rejected | No |
| 10 | `helper` | 3 | Accepted | No |
| 11 | `schema` | 7 | Accepted | No |
| 12 | `original` | 3 | Not submitted | Yes |

Run 05 has a coding failure: its program incorrectly requires the optional label.
It never reaches a recovered checker-passing version.
Run 08 starts with a failing program, then produces a checker-passing version on turn five.
Run 11 first reaches that point on turn seven.

Run 09 has a packaging failure. Its first marker attempt finds no payload at the work root.
Its second attempt submits a status report containing file paths, which the host rejects.
A separate assessment of source from its captured workspace passes all five original checks.
This does not change the rejected submission.
It directly demonstrates why the worker needs the required payload structure.

Runs 01 and 12 make no marker attempt despite retaining checker-passing programs.
Run 01 stops when its sixth response reaches the output-token limit while generating more tests.
Run 12 consumes all twelve turns. Its final command runs more tests.
These are completion failures under the fixed budget, separate from run 05 and run 09.

All seven submissions with explicit schema instructions contain accepted payloads.
Both countdown-only runs reach submission, but only one packages the source correctly.
The original-instructions runs never submit.
This supports explicit packaging instructions and further study of stopping behavior.
Two repetitions do not establish which intervention reliably improves completion.

### Verification quality

The extra diagnostic uses `{"version":true,"host":"h","timeout":5,"label":"kept"}`.
A paired valid control changes only `version` to integer 1.
All twelve retained programs accept the valid control. Only runs 02 and 12 reject the boolean version.
The original contract rejects booleans as integers, but its private cases exercise boolean timeout rather than boolean version.
The original five-check results remain unchanged.

In the first schema/helper pair, run 02 finds the boolean-version error on turn seven and repairs it on turn eight.
Run 03 submits five turns earlier, but still accepts that invalid input.
This speed difference is not an improvement at equivalent quality.
In the reverse pair, both final programs accept the invalid input.
Run 12 rejects it but never submits. The speed–quality pattern therefore does not repeat across the pairs.

Packaging reliability, verification coverage, and stopping behavior need separate treatment.
The evidence supports documenting the payload contract and adding a regression case for boolean versions in a later checker change.
It does not justify rewarding immediate submission after the first checker pass or selecting a winning prompt.
The task checker and normal A–E worker instructions are unchanged in this diagnostic slice.

### Work after a checker-passing source version

After the live runs, twenty fresh diagnostic ledgers assess recoverable source versions against the unchanged initial checker.
Six versions come from complete source printed by a tool. Fourteen are reconstructed from literal quoted heredocs in recorded commands.
The inspection never executes those shell commands. It executes the recovered Python only through the contained checker.
These are the earliest recoverable passing versions, not continuous observations of workspace state.
Run 07 writes and lists its source on turn two, then fails to read a missing `settings.json`.
That reconstructed source exactly matches its final captured file; the later command failure does not erase the write.

The following counts start after the source-writing action and include the submission action or failed terminal response.
Input tokens include repeated conversation context.
These counts measure work, not waste: some extra tests expose real gaps, while other commands repeat unsuccessful inspection.

| Run | Later model calls | Later shell calls | Later input tokens | Later output tokens | Termination |
| --- | ---: | ---: | ---: | ---: | --- |
| 01 | 3 | 2 | 9243 | 3812 | output-token limit |
| 02 | 9 | 9 | 52365 | 3871 | submitted |
| 03 | 4 | 4 | 16129 | 1470 | submitted |
| 04 | 7 | 7 | 21684 | 903 | submitted |
| 05 | — | — | — | — | turn limit |
| 06 | 3 | 3 | 10873 | 590 | submitted |
| 07 | 3 | 3 | 8887 | 629 | submitted |
| 08 | 2 | 2 | 9170 | 195 | submitted |
| 09 | 8 | 8 | 28527 | 1147 | submitted |
| 10 | 7 | 7 | 25827 | 1413 | submitted |
| 11 | 4 | 4 | 21844 | 1458 | submitted |
| 12 | 9 | 9 | 55471 | 6135 | turn limit |

Run 05 has no finishing-gap measurement because no recovered version passes the checker.
The source-version analysis and boolean witness were added during the comparison at the user’s request.
They are exploratory diagnostics. They did not change the predeclared treatments, order, budgets, or original verdicts.

### Cost and retained evidence

| Run | Model / shell calls | Total tokens | Parent execution and assessment (seconds) |
| --- | ---: | ---: | ---: |
| 01 | 6 / 5 | 17100 | 73.69 |
| 02 | 12 / 12 | 60608 | 108.49 |
| 03 | 7 / 7 | 22283 | 64.21 |
| 04 | 9 / 9 | 24816 | 65.00 |
| 05 | 12 / 12 | 37449 | 81.30 |
| 06 | 6 / 6 | 16440 | 52.46 |
| 07 | 5 / 5 | 12266 | 44.79 |
| 08 | 7 / 7 | 21561 | 65.70 |
| 09 | 11 / 11 | 33829 | 72.16 |
| 10 | 10 / 10 | 31925 | 75.97 |
| 11 | 11 / 11 | 41819 | 88.71 |
| 12 | 12 / 12 | 65657 | 121.62 |

The twelve episodes spend 108 model units, 107 shell units, 12 batch units, 12 checker units, and three capture units.
They use 353,108 input tokens and 32,645 output tokens, including the truncated response.
Parent processes measure 6.42 seconds for initialization and 914.10 seconds for execution and assessment in total.
All reservations settle. No paid requests run. Electricity and human development costs remain unmeasured.

The version inspections add twenty batch units and twenty checker units over 109.85 seconds.
The final-program diagnostics add thirteen batch units and one checker unit over 60.56 seconds.
The extra checker unit assesses run 09’s captured source separately.
These two post-hoc jobs overlap after all live episodes finish; their elapsed times must not be added as wall time.

Local evidence is under `runs/m5-submission-ablation-20260924/`.
`plan.json`, the runner scripts, and `timing/` preserve the order, source commit, and parent measurements.
The launcher stops after run 01’s known token-limit failure. `continuation.json` records why execution resumes at run 02 without a retry.
`summary.json` retains episode results, tokens, commands, and marker-attempt counts.
`versions/` and `boolean/` contain fresh ledgers with source, original observation references, inspection code, checker inputs, and results.
`completion-summary.json` records finishing gaps, including run 07’s file-write clarification.
These ignored local artifacts are not included in Git. The six controls remain opt-in and outside CI.
