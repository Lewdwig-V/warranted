# CSV transformation

Read `input.csv` and the single `offset-v*` file supplied in this workspace.
The offset file gives the current fixed offset in minutes. Convert every source
timestamp to UTC. Preserve every row's order, identifier spelling, and integer
value. Identifiers must remain unique under exact comparison.

Write `result.json` with exactly two fields. `rows` contains arrays of
`[identifier, UTC timestamp, integer value]`. Use `YYYY-MM-DDTHH:MM:SSZ` for each
timestamp. `totals` maps each UTC date to the sum of its row values.

All four requirements apply independently: unique identifiers, preserved rows,
correct UTC timestamps, and correct daily totals. An empty result does not
satisfy this task. Use the supplied offset even though input timestamps do not
contain an explicit offset. The host records the corresponding rule exception.

Submit by printing `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` on the first stdout
line of your final command. Submission requests an independent check. It does
not establish acceptance. The host captures only `result.json`.
