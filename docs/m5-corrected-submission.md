# M5 corrected submission controls

This development experiment repeats the six submission controls after the prompt fixes in PR #32.
It keeps submission, checker results, and additional diagnostic cases separate.
It does not complete M5 or compare models.
The [earlier report](m5-submission-controls.md#results-of-the-twelve-run-comparison) retains the measurements from the original, confounded controls.

All twelve runs finish on 2026-09-25.
Seven submit accepted payloads, two submit rejected payloads, and three end without recognized submission.
Every retained final program passes the original five checks when assessed as source.
Only two reject the separate boolean-version case, and neither of those episodes completes submission.

## Fixed method

The annotated tag
[`m5-submission-corrected-20260925`](https://github.com/Lewdwig-V/warranted/tree/m5-submission-corrected-20260925)
preserves measured source `7bb96504d2287c9d3adbbeb1c80d4bb59b5a9437` independently of later squash merges.
The countdown now states only the remaining model turns.
The helper instructions replace the original manual marker instruction.
These fixes remove the extra format reminder and contradictory command wording from the earlier comparison.

| Control | Submission instructions | Helper file | Remaining turns |
| --- | --- | --- | --- |
| `original` | Original instructions | No | No |
| `schema` | Explicit source payload, root path, and source location | No | No |
| `helper` | Same schema instructions plus a separate helper command | Yes | No |
| `original-budget` | Original instructions | No | Yes |
| `schema-budget` | Same schema instructions | No | Yes |
| `helper-budget` | Same schema and helper instructions | Yes | Yes |

Before inference, the local plan fixes twelve fresh runs in this order:
`original`, `schema`, `helper`, `original-budget`, `schema-budget`, `helper-budget`, then the same six in reverse order.
Both repetitions use seed zero.
They measure repeatability on one development task, not variation across seeds or unseen tasks.
Do not pool these results with the earlier controls, whose prompts differ.

All runs use local `qwen3.8:27b` through Ollama, temperature zero, a 3,072-token output limit, and a 180-second request deadline.
Each run permits twelve model turns and twelve shell commands.
The model digest is `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643`.
The adapter requests no reasoning output and requires the same command JSON structure.
The ledger records Python 3.12.12, mini-swe-agent 2.4.6, LangGraph 1.2.11, and langgraph-checkpoint-sqlite 3.1.1.
It also pins the rootless container image, model runtime, fixture, evaluator, and policy bytes.
The GPU is an RTX 4090. Before the first run, it reports 19% utilization and 13,419 MiB of used memory.
The launcher records GPU load before each run and stops on infrastructure failure or an unresolved operation.
Completed model failures remain in the comparison without retries.

The task is the initial repository migration under condition A.
The original five independent checks run after each episode, so the worker never sees their verdicts during task work.
The worker sees only its own commands and tests.
The helper packages source and prints the submission marker. It does not test the program or grant acceptance.
The approved revision and recovery stages remain outside this diagnostic.

## Outcomes

Every run has a recovered source version from turn three that passes the original checker.
Acceptance below refers only to those five checks, not every task requirement.
The boolean column assesses the final retained program, including separately recovered source when submission fails.

| Run | Control | First checker-passing source turn | Submitted payload | Final program rejects boolean version |
| --- | --- | ---: | --- | --- |
| 01 | `original` | 3 | Not submitted | No |
| 02 | `schema` | 3 | Not submitted | Yes |
| 03 | `helper` | 3 | Accepted | No |
| 04 | `original-budget` | 3 | Rejected | No |
| 05 | `schema-budget` | 3 | Accepted | No |
| 06 | `helper-budget` | 3 | Accepted | No |
| 07 | `helper-budget` | 3 | Accepted | No |
| 08 | `schema-budget` | 3 | Accepted | No |
| 09 | `original-budget` | 3 | Rejected | No |
| 10 | `helper` | 3 | Accepted | No |
| 11 | `schema` | 3 | Accepted | No |
| 12 | `original` | 3 | Not submitted | Yes |

Runs 04 and 09 copy raw Python into `/work/result.json` instead of creating the required JSON object.
Both emit the marker on the first stdout line and reach submission.
The host rejects their payloads. Separate assessments of their captured workspace source pass all five checks.
These are packaging failures, not failures to write a checker-passing program.

Run 02 prepares a payload that later passes the checker.
It prints status text before the marker on turns eleven and twelve.
The host therefore does not recognize either emission as submission, and the episode reaches its turn limit.
Its retained payload and source both pass the original checks.
Run 08 makes the same marker-placement error on turn eight, then submits with the marker first on turn nine.
This distinguishes an attempted completion signal from a recognized submission.

Runs 01 and 12 never emit a submission marker.
Run 01 stops when response six reaches the output-token limit while generating additional tests.
Run 12 uses all twelve turns. Its last three commands repeat the same tests without editing source or submitting.
Earlier in run 12, useful testing exposes boolean-version acceptance, and turn eight repairs it.
The later repetition does not erase that verification benefit.

All four helper runs submit accepted payloads with a separate helper command.
The helper-only pair uses seven turns each. The helper-plus-countdown pair uses six turns each.
Both schema-plus-countdown runs submit, in eight and nine turns.
Schema-only submits once in two runs, while original-plus-countdown submits twice with invalid packaging.
Countdown visibility therefore does not supply the missing payload contract.

These repetitions support explicit packaging instructions and a separate submission command as concrete interface repairs.
They do not establish general reliability or a winning prompt.
The schema-only reverse run succeeds without a helper or countdown.
Only runs 02 and 12 reject the extra boolean case, so none of the seven accepted submissions satisfies that diagnostic requirement.
Successful completion does not establish adequate verification coverage.

## Additional assessments

After the live episodes, thirteen separate ledgers assess recovered source versions.
Two versions appear in full in recorded tool output. Eleven are reconstructed from quoted heredocs in recorded commands.
The analysis never executes those shell commands.
It executes recovered Python only through the existing contained checker.
These observations identify the earliest recovered checker-passing source, not the exact moment that the workspace first satisfies every requirement.
They do not cover every possible intermediate edit.

In runs 06 and 07, turn three writes source, then fails while reading a missing `settings.json`.
The reconstructed source exactly matches the final captured file.
The three later commands prepare the public example, test the program, and submit. None edits the program.
The finishing counts therefore retain turn three despite the later command failure.
The raw inspection summary and the evidence for this clarification remain separate.

The finishing interval starts after that source-writing action and includes the submission action or terminal failed response.
Its token count includes repeated conversation context.
Extra turns can contain useful tests, repairs, repeated inspections, or submission errors.
The interval measures work, not wasted effort.

The separate boolean diagnostic uses `{"version":true,"host":"h","timeout":5,"label":"kept"}`.
Its paired valid control changes only `version` to integer 1.
The task already requires rejection of booleans as integers.
The original private cases test boolean timeout but omit boolean version.
These new assessments leave every original verdict unchanged.
All twelve final programs accept the paired valid input. Only runs 02 and 12 reject the boolean input.
Run 02 detects the boolean problem on turn six and patches it on turn seven, before its unsuccessful marker attempts.

## Work and cost

The later-call and later-token columns start after the first recovered checker-passing source action.
Run 01 includes its truncated response in both total and later token counts.
Parent time covers execution and assessment, with initialization reported separately below.

| Run | Model / shell calls | Later model / shell calls | Later input / output tokens | Total tokens | Parent time (seconds) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 01 | 6 / 5 | 3 / 2 | 9243 / 3812 | 17100 | 121.60 |
| 02 | 12 / 12 | 9 / 9 | 37978 / 2002 | 44376 | 94.75 |
| 03 | 7 / 7 | 4 / 4 | 11731 / 728 | 17031 | 60.34 |
| 04 | 9 / 9 | 6 / 6 | 17144 / 748 | 21967 | 59.50 |
| 05 | 8 / 8 | 5 / 5 | 16472 / 824 | 21623 | 62.40 |
| 06 | 6 / 6 | 3 / 3 | 9021 / 777 | 14402 | 51.46 |
| 07 | 6 / 6 | 3 / 3 | 9021 / 777 | 14402 | 51.08 |
| 08 | 9 / 9 | 6 / 6 | 19324 / 1034 | 24725 | 65.46 |
| 09 | 10 / 10 | 7 / 7 | 21165 / 842 | 26042 | 63.09 |
| 10 | 7 / 7 | 4 / 4 | 12008 / 953 | 17533 | 56.81 |
| 11 | 8 / 8 | 5 / 5 | 16359 / 1037 | 21792 | 62.13 |
| 12 | 12 / 12 | 9 / 9 | 33737 / 3472 | 41236 | 97.43 |

The twelve episodes spend 100 model units, 99 shell units, 13 batch units, 13 checker units, and three capture units.
Run 02 has two assessments because the host checks its unfinished payload and retained source separately.
Token totals are 258,032 input and 24,197 output, or 282,229 overall.
Parent processes measure 6.65 seconds for initialization and 846.05 seconds for execution and assessment in total.
All reservations settle. No paid requests run. Electricity and human development costs remain unmeasured.

The source-version assessments add thirteen batch units and thirteen checker units over 68.94 seconds.
The final-program diagnostics add fourteen batch units and two checker units over 65.41 seconds.
The two extra checker units assess the captured source from rejected submissions 04 and 09.
These analysis jobs overlap after the live episodes. Do not add their elapsed times as wall time.

## Retained evidence and limits

Local evidence is under `runs/m5-submission-corrected-20260925/`.
The plan predates inference and records the order, source tag, source commit, model configuration, and launcher digests.
The timing directory retains parent measurements and raw command output for every initialization and execution.
The summary compares every task and runtime snapshot across runs. Only the control selection differs in the manifest environment.
It also records commands, tokens, and marker positions, including emissions that fail the first-line rule.

The `versions/` and `boolean/` directories hold fresh ledgers with pinned source, original observation references, analysis code, inputs, and results.
The `completion-summary.json` file preserves the finishing counts and the two failed-command clarifications.
The one-off scripts and raw ledgers are ignored local evidence, not published Git artifacts.
The source tag preserves the runner and fixture needed for a fresh reproduction, not identical model trajectories.

This report changes no worker instructions, evaluator cases, or acceptance rules.
The known boolean gap needs a separately versioned evaluator change before broader quality claims.
The normal A–E runner still needs the explicit submission contract before model comparisons resume.
The next development run must then attempt the approved revision and recovery sequence.

## Reproduce a run

Prepare the container and proof bundle through the existing [M5 instructions](m5-contexts.md).
Use the preserved source tag and a fresh directory for each run.
Replace `helper` with the required control.

```bash
uv run --locked python examples/m5/diagnostics.py init runs/corrected-helper \
  --bundle runs/m4-tools/bundle.json --control helper \
  --local-model qwen3.8:27b --max-tokens 3072 --timeout 180 --max-steps 12
uv run --locked python examples/m5/diagnostics.py run runs/corrected-helper \
  --bundle runs/m4-tools/bundle.json --control helper \
  --local-model qwen3.8:27b --max-tokens 3072 --timeout 180 --max-steps 12
```

Keep the pinned files and configuration unchanged between initialization and execution.
Repeated execution reads recorded work instead of repeating completed calls.
These local model runs remain outside CI and make no paid API requests.
