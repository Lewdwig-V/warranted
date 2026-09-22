# M5 supported proof cases

The third [M5 slice](m5-migration.md#five-implementation-slices) adds timestamp and
migration proofs alongside the existing M4 uniqueness theorem. Each family has a
successful control and a candidate that passes its narrow proof but fails an
independent task requirement. The unchanged M2 and M5 checkers decide acceptance.
These are fixed development examples, not model proof-search trials or A–E results.

## Targets and task outcomes

The host selects a target from `src/warranted/proof/targets.json` before receiving
candidate proof source. The [M4 verification boundary](m4-verification.md) binds
the exact challenge, definitions, comparator configuration, source, tool bundle,
and limits. An unknown target fails explicitly. A receipt for one target cannot
support another. Historical receipts remain assessable without the tools or
current target registry. The original uniqueness challenge is unchanged.

| Target | Candidate and model | Proof support | Task acceptance |
| --- | --- | --- | --- |
| Uniqueness | Correct output | Supported | Accepted |
| Uniqueness | Dropped row or empty output | Supported | Rejected: record loss |
| Timestamp | One-hour offset | Supported | Accepted |
| Timestamp | Zero-offset interpretation | Supported | Rejected: timestamps and daily totals |
| Migration | Preserve the optional label | Supported | Accepted |
| Migration | Drop the label during version 1 conversion | Supported | Rejected: legacy consumer |

Uniqueness reuses the [M4 theorem and application checks](m4-fixture.md).
The timestamp theorem proves a round trip for integer seconds:
`(localSeconds - assumedOffsetSeconds) + assumedOffsetSeconds = localSeconds`.
The correct candidate selects 60 minutes; the mistranslated candidate selects zero.
Both outputs match their selected model. The theorem does not establish that the
selected offset follows the source contract. The independent timestamp and daily
total requirements still reject the zero-offset result.

The migration theorem preserves the host and timeout values under renaming for
both label-preserving and label-dropping variants. Both leave version 2 input
unchanged. Its formal configurations contain an optional string label, including
the distinction between absence and an empty string. The host checks the complete
observed output against the selected variant, including that label. The failing
candidate therefore matches its model; the model's narrow theorem omits a binding
task obligation. The legacy consumer check preserves that failure.

`examples/m5/proof-intent.json` records the selected models and known gaps.
Application support requires the checked theorem, a valid input domain, and
matching output from the exact candidate. Missing, failed, or uncertain execution
cannot establish correspondence. These checks cover the captured development
inputs. They do not prove the Python parser, calendar conversion, or every possible
program execution correct. Invalid-input rejection remains an independent task check.

## Run and recover

Use the native rootless Podman setup from the [verification guide](m4-verification.md).
Build a fresh verifier bundle for these targets and pull the pinned migration runtime:

```bash
uv run --locked python scripts/build_proof_bundle.py runs/m4-tools
podman pull "$(uv run --locked python -c 'from warranted.sandbox import IMAGE; print(IMAGE)')"
uv run --locked python examples/m5/proof_cases.py start runs/m5-proofs --bundle runs/m4-tools/bundle.json --crash
```

The start command verifies all three proposals, writes a committed checkpoint,
then kills the host while its ledger is open. Exit status 137 is expected in a shell.
Run each resume in a fresh process:

```bash
uv run --locked python examples/m5/proof_cases.py resume runs/m5-proofs --bundle runs/m4-tools/bundle.json
uv run --locked python examples/m5/proof_cases.py resume runs/m5-proofs --bundle runs/m4-tools/bundle.json
```

The first resume checks all applications and task requirements. The second reuses
the completed proofs, executions, checks, claims, and decisions. Changed fixture,
host, or bundle bytes block dispatch. An unresolved operation retains its reserved
budget and blocks new work.

| Recorded work | After start | After either resume |
| --- | --- | --- |
| Native proof attempts | 3 | 3 |
| CSV and application checks (`synthetic-work`) | 0 | 12 |
| Migration execution batches | 0 | 2 |
| Migration task checks | 0 | 2 |

These are separate synthetic attempt units. Reports also retain measured operation
time and the bundle's recorded build time. Completed work leaves no reservations.
The independent witness files contain three proof executions and sixteen combined
checker or batch executions after either resume. The second resume records zero
new operations. One local native test completed the kill and both resumes in 61
seconds; this is a development measurement, not a performance guarantee.

Reports under `reports/` and evidence under `exports/` are for host inspection.
They include private development references and must not become worker context.
The durable receipt format is now version 2 and the proof API requires `target_id`.
Use a new run directory and rebuilt bundle; there is no old-format compatibility reader.

## Checks and remaining work

```bash
uv run --locked pytest -q -m 'not proof' tests/test_m5_proof_cases.py
WARRANTED_PROOF_TESTS=1 WARRANTED_PROOF_BUNDLE=runs/m4-tools/bundle.json \
  uv run --locked pytest -q -m proof --durations=5 tests/test_m5_proof_cases.py
```

CI now runs these cases inside the [E recovery treatments](m5-contexts.md), once
per relevant family. It retains their reports, exports, and worker/checker witnesses.
The standalone command remains available for focused inspection. The existing M4
tests separately cover incomplete proofs, timeout, changed premises, and isolation.

The [shared context boundary](m5-contexts.md) now supplies complete condition E
recovery runs with real proof receipts and scoped application checks.
Paid trials and model selection remain separate work. These fixed proposals demonstrate acceptance boundaries;
they do not show that a model can discover missing requirements.
