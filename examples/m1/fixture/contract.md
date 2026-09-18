# CSV normalization contract, version 1

Preserve every input row, its order, identifier, and integer value.
Convert each timestamp from its explicit offset to UTC.
Sum the values by UTC date, not by the source date.

The fixed expected files come from the M1 walkthrough in `docs/pilot.md`.
The host compares normalized rows and parsed totals with those files.
This establishes behavior for this fixture only, not general correctness or M2
task acceptance. Model and proof configuration do not apply.
