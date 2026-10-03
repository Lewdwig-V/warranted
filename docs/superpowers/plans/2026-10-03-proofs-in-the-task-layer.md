# Proofs in the Task Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement [docs/proposals/m7-proofs.md](../../proposals/m7-proofs.md). Domains own Lean proof targets, a proof is a task-layer checker, `VerdictStatus.UNPROVED` exists, and the CSV and migration fixtures' proof cases run on the task layer.

**Architecture:** The work lands as three PRs, each green on its own:

- **A. Verifier core:** domain-supplied targets. The supervisor receives the challenge and theorem from the host. The three challenge files move to `examples/`. The host kit and the M4/M5 drivers take a `ProofTarget`.
- **B. Task-layer API:** `UNPROVED`, a `ProofVerifier` passed to `Project`, `CheckContext.verify`, checker `sources`, and the `LeanProof` checker.
- **C. Fixtures:** proof checkers, premises and tasks for CSV and migration. Proof-marked tests, CI, docs, and version 0.2.0.

The design note named two PRs. PR A is split out because moving the target registry touches the host kit, three drivers, and five test files, independently of the task layer.

**Tech Stack:** Python 3.12, uv, pytest, rootless Podman, and the pinned Lean verifier bundle (`scripts/build_proof_bundle.py`).

## Global Constraints

- Use Python 3.12+ and uv. Every command runs as `uv run --locked ...`.
- The default suite (`uv run --locked pytest -q tests`) needs no credentials, network, containers, or Lean.
- Container tests are marked `container`, and proof tests are marked `proof` and gated by `WARRANTED_PROOF_TESTS=1` and `WARRANTED_PROOF_BUNDLE=<bundle.json>`.
- Lint with `uv run --locked ruff check .` and check formatting with `uv run --locked ruff format --check .`. Both must pass.
- `docs/reference/api.md` must list exactly `warranted.__all__` (enforced by `tests/test_release.py`).
- The CLI and `examples/m7/**` import only `warranted` and `warranted.host` public names (enforced by `tests/test_public_api.py`).
- Changelog entries go under `## [Unreleased]` until PR C, which moves them to `## [0.2.0] - <merge date>` and sets `version = "0.2.0"`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not change the axiom policy (`src/warranted/proof/config.json`) or `toolchain.json`.

## File map

| File | Change | Responsibility |
| --- | --- | --- |
| `src/warranted/_proofs.py` | Modify | `ProofTarget`; `_inputs`, `verify`, and `_verify` take a target |
| `src/warranted/proof/supervisor.py` | Modify | `prepare` reads the source, challenge, and theorem from stdin JSON |
| `src/warranted/proof/{Challenge,Timestamp,Migration}.lean`, `targets.json` | Move or delete | Challenges leave the core |
| `examples/m7/csv/UniquenessChallenge.lean`, `TimestampChallenge.lean` | Create (moved) | CSV-owned targets |
| `examples/m7/migration/MigrationChallenge.lean` | Create (moved) | Migration-owned target |
| `examples/proof_targets.py` | Create | `TARGETS` dict that the drivers and tests share |
| `src/warranted/_proof_receipts.py` | Modify | `Proofs(..., target=ProofTarget)` |
| `examples/m4/demo.py`, `examples/m5/proof_cases.py`, `examples/m5/treatments.py` | Modify | Use `TARGETS` |
| `src/warranted/_tasks.py` | Modify | `UNPROVED`; `proofs=` and its identity; `CheckContext.verify`; checker `sources`; refusing a task without a verifier |
| `src/warranted/_proof_checks.py` | Create | `ProofVerifier`, `LeanVerifier`, and `LeanProof` |
| `src/warranted/__init__.py` | Modify | Export the new names |
| `examples/m7/csv/domain.py`, `examples/m7/migration/domain.py` | Modify | Proof checkers and premises |
| `examples/m7/csv/proof-uniqueness.toml`, `proof-timestamp.toml`, `examples/m7/migration/proof.toml` | Create | Proof tasks |
| `tests/test_proofs.py`, `tests/test_proof_receipts.py`, `tests/test_proofs_native.py`, `tests/test_m4_fixture.py` | Modify | Pass targets |
| `tests/test_proof_checks.py` | Create | Fast API tests with a fake verifier |
| `tests/test_proof_fixtures.py` | Create | Fixture tests: fast, plus `proof`-marked |
| `.github/workflows/ci.yml` | Modify | Proof job runs `tests/test_proof_fixtures.py` |
| `docs/reference/api.md`, `docs/reference/proof-verification.md`, `CHANGELOG.md`, `docs/roadmap.md`, `docs/fixtures/*.md`, `README.md` | Modify | Docs |

---

## PR A: verifier core

Branch: `git switch -c proofs-verifier origin/main`.

### Task A1: Domain-supplied proof targets

**Files:**
- Modify: `src/warranted/_proofs.py`, `src/warranted/proof/supervisor.py`, `src/warranted/_proof_receipts.py`, `src/warranted/__init__.py`, `src/warranted/host.py`
- Move: `src/warranted/proof/Challenge.lean` to `examples/m7/csv/UniquenessChallenge.lean`; `src/warranted/proof/Timestamp.lean` to `examples/m7/csv/TimestampChallenge.lean`; `src/warranted/proof/Migration.lean` to `examples/m7/migration/MigrationChallenge.lean`
- Delete: `src/warranted/proof/targets.json`
- Create: `examples/proof_targets.py`
- Modify: `examples/m4/demo.py`, `examples/m5/proof_cases.py`, `examples/m5/treatments.py`
- Modify tests: `tests/test_proofs.py`, `tests/test_proof_receipts.py`, `tests/test_proofs_native.py`, `tests/test_m4_fixture.py`
- Modify docs: `docs/reference/api.md`, `docs/reference/proof-verification.md`, `CHANGELOG.md`

**Interfaces:**
- Produces: `warranted.ProofTarget(path: Path | str, theorem: str)`, with attributes `.path: Path`, `.theorem: str`, `.challenge: bytes`.
- Produces: `_proofs.verify(source: bytes, bundle_path: Path, *, target: ProofTarget, seconds: int = 120) -> Verification`.
- Produces: `_proofs._verify(source: bytes, bundle_bytes: bytes, *, target: ProofTarget, seconds: int) -> Verification`.
- Produces: `_proofs._inputs(source: bytes, bundle_bytes: bytes, seconds: int, target: ProofTarget) -> tuple[dict, dict]`. Its identity keeps the key `"target_id"`, whose value is `target.theorem`.
- Produces: `_proof_receipts.Proofs(ledger, session, bundle: Path, *, target: ProofTarget, seconds=120)`, with attribute `.proof_target`.
- Produces: `examples/proof_targets.py` defines `TARGETS: dict[str, ProofTarget]` with keys `"uniqueness"`, `"timestamp"`, and `"migration"`.

- [ ] **Step 1: Move the challenge files**

```bash
git mv src/warranted/proof/Challenge.lean examples/m7/csv/UniquenessChallenge.lean
git mv src/warranted/proof/Timestamp.lean examples/m7/csv/TimestampChallenge.lean
git mv src/warranted/proof/Migration.lean examples/m7/migration/MigrationChallenge.lean
git rm -q src/warranted/proof/targets.json
```

- [ ] **Step 2: Write the failing tests for `ProofTarget`**

In `tests/test_proofs.py`, replace `test_unknown_target_cannot_start_verification` with the code below. Also add `import runpy` and the `TARGETS` lines below the existing imports.

```python
import runpy

from warranted import ProofTarget

ROOT = Path(__file__).resolve().parents[1]
TARGETS = runpy.run_path(str(ROOT / "examples/proof_targets.py"))["TARGETS"]


def test_a_target_must_be_a_proof_target(tmp_path):
    with pytest.raises(ValueError, match="ProofTarget"):
        verify(b"source", tmp_path / "missing-bundle.json", target="uniqueness")


@pytest.mark.parametrize(
    "theorem", ["", "x; y", "Warranted.", ".x", "a b", 7, "Warranted.ok\n"]
)
def test_a_target_needs_a_qualified_theorem_name(theorem):
    with pytest.raises(ValueError, match="theorem"):
        ProofTarget(TARGETS["uniqueness"].path, theorem)


def test_a_target_needs_a_nonempty_bounded_challenge(tmp_path):
    empty = tmp_path / "Empty.lean"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="challenge"):
        ProofTarget(empty, "Warranted.x")
    with pytest.raises(FileNotFoundError):
        ProofTarget(tmp_path / "missing.lean", "Warranted.x")


def test_a_target_reads_its_challenge_once():
    target = TARGETS["uniqueness"]
    assert target.challenge == target.path.read_bytes()
    assert b"uniqueness_preserved" in target.challenge
```

In the same file, replace each `target_id="uniqueness"` with `target=TARGETS["uniqueness"]` (lines 25, 35, 50 and 73).

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `uv run --locked pytest -q tests/test_proofs.py`
Expected: an ImportError (`ProofTarget`), or FileNotFoundError for `examples/proof_targets.py`.

- [ ] **Step 4: Create `examples/proof_targets.py`**

```python
"""The fixtures' proof targets: domain-owned Lean challenges and required theorems.

Shared by the M4 and M5 drivers and their tests; the task-layer domains in
examples/m7 declare the same files in their own checkers.
"""

from pathlib import Path

from warranted import ProofTarget

EXAMPLES = Path(__file__).resolve().parent
TARGETS = {
    "uniqueness": ProofTarget(
        EXAMPLES / "m7/csv/UniquenessChallenge.lean", "Warranted.uniqueness_preserved"
    ),
    "timestamp": ProofTarget(
        EXAMPLES / "m7/csv/TimestampChallenge.lean", "Warranted.timestamp_roundtrip"
    ),
    "migration": ProofTarget(
        EXAMPLES / "m7/migration/MigrationChallenge.lean",
        "Warranted.migration_renaming",
    ),
}
```

- [ ] **Step 5: Add `ProofTarget` to `src/warranted/_proofs.py` and thread it through**

Change the dataclass import to `from dataclasses import dataclass, field`. After the `LIMITS` dict, add:

```python
_THEOREM = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")


@dataclass(frozen=True)
class ProofTarget:
    """A domain-owned proof target: a Lean challenge file and its required theorem.

    The challenge bytes are read once, here; the host supplies them to the
    verifier, so a worker can never select or alter the target.
    """

    path: Path
    theorem: str
    challenge: bytes = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path))
        if type(self.theorem) is not str or not _THEOREM.fullmatch(self.theorem):
            raise ValueError("theorem must be a qualified Lean name")
        data = self.path.read_bytes()
        if not 0 < len(data) <= SOURCE_LIMIT:
            raise ValueError("challenge must be nonempty bounded bytes")
        object.__setattr__(self, "challenge", data)
```

Replace `_target`:

```python
def _target(target) -> ProofTarget:
    if not isinstance(target, ProofTarget):
        raise ValueError("proof target must be a ProofTarget")
    return target
```

In `_inputs`, rename the last parameter to `target` and replace the target lookup, the challenge read, and the theorem line:

```python
def _inputs(
    source: bytes, bundle_bytes: bytes, seconds: int, target: ProofTarget
) -> tuple[dict, dict]:
    _validate(source, seconds)
    target = _target(target)
    bundle = json.loads(bundle_bytes)
    # (the bundle check is unchanged)
    challenge = target.challenge
    config = json.loads((RESOURCES / "config.json").read_bytes())
    config["theorem_names"] = [target.theorem]
```

In the identity dict, change `"target_id": target_id` to `"target_id": target.theorem`. Then update `verify` and `_verify`:

```python
def verify(
    source: bytes,
    bundle_path: Path,
    *,
    target: ProofTarget,
    seconds: int = LIMITS["seconds"],
) -> Verification:
    """Verify captured bytes against a host-supplied target under the pinned bundle.

    Cleanup failure raises instead of claiming a known, stopped attempt. This
    function does not reserve budgets, persist evidence, or authorize acceptance.
    """
    _validate(source, seconds)
    _target(target)
    return _verify(source, bundle_path.read_bytes(), target=target, seconds=seconds)


def _verify(
    source: bytes, bundle_bytes: bytes, *, target: ProofTarget, seconds: int
) -> Verification:
    identity, raw = _inputs(source, bundle_bytes, seconds, target)
```

In `_verify`, the `prepare` call now sends the challenge and theorem as JSON instead of passing `target_id` as an argument:

```python
        _checked(
            [
                "exec",
                "--interactive",
                "--user=0:0",
                name,
                "python",
                "-I",
                "/opt/proof/supervisor.py",
                "prepare",
            ],
            json.dumps(
                {
                    "source": base64.b64encode(source).decode(),
                    "challenge": base64.b64encode(target.challenge).decode(),
                    "theorem": target.theorem,
                }
            ).encode(),
        )
```

- [ ] **Step 6: Make the supervisor read the request**

In `src/warranted/proof/supervisor.py`, add `import re` to the imports and replace the start of `prepare()`, through the `config.json` write:

```python
THEOREM = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")


def prepare():
    payload = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("verification request exceeds limit")
    request = json.loads(payload)
    if (
        type(request) is not dict
        or set(request) != {"source", "challenge", "theorem"}
        or type(request["theorem"]) is not str
        or not THEOREM.fullmatch(request["theorem"])
    ):
        raise ValueError("invalid verification request")
    source = base64.b64decode(request["source"], validate=True)
    challenge = base64.b64decode(request["challenge"], validate=True)
    for data in (source, challenge):
        if not 0 < len(data) <= 1024 * 1024:
            raise ValueError("source or challenge exceeds limit or is empty")
    os.mkdir("/tmp/host", 0o700)
    (WORK / "Challenge.lean").write_bytes(challenge)
    shutil.copyfile(ROOT / "lakefile.toml", WORK / "lakefile.toml")
    config = json.loads((ROOT / "config.json").read_bytes())
    config["theorem_names"] = [request["theorem"]]
    (WORK / "config.json").write_text(json.dumps(config, sort_keys=True))
```

Leave the rest of `prepare()` unchanged: the lake manifest, `Solution.lean` (now `(WORK / "Solution.lean").write_bytes(source)`, as before), `.lake`, and the decision files.

- [ ] **Step 7: Update the host kit's `Proofs`**

In `src/warranted/_proof_receipts.py`, change `Proofs.__init__` to take `target` instead of `target_id`:

```python
    def __init__(
        self,
        ledger: Ledger,
        session: str,
        bundle: Path,
        *,
        target: proofs.ProofTarget,
        seconds: int = proofs.LIMITS["seconds"],
    ):
        self.ledger, self.session = ledger, session
        self.bundle = bundle.read_bytes()
        self.seconds = seconds
        self.proof_target = target
        identity, raw = proofs._inputs(
            b"policy preparation", self.bundle, seconds, target
        )
```

In `Proofs.check`, change the `_verify` call to:

```python
        verification = proofs._verify(
            data, self.bundle, target=self.proof_target, seconds=self.seconds
        )
```

- [ ] **Step 8: Export `ProofTarget`**

In `src/warranted/__init__.py`, add `from warranted._proofs import ProofTarget` and add `"ProofTarget"` to `__all__` in alphabetical position. In `docs/reference/api.md`, add this section before "## Model adapters":

```markdown
## Proofs

| Name | What it is | Reference |
| --- | --- | --- |
| `ProofTarget` | A domain-owned Lean challenge file and the theorem a proof must establish | [proof verification](proof-verification.md#targets-and-axiom-policy) |
```

- [ ] **Step 9: Update the drivers**

Each of `examples/m4/demo.py`, `examples/m5/proof_cases.py`, and `examples/m5/treatments.py` loads the shared targets near its other module-level constants. Add `import runpy` if the file lacks it:

```python
TARGETS = runpy.run_path(str(Path(__file__).resolve().parents[1] / "proof_targets.py"))[
    "TARGETS"
]
```

`examples/m4/demo.py`:
- In `snapshots`, replace `"Challenge.lean": proofs.RESOURCES / "Challenge.lean",` with `"Challenge.lean": TARGETS["uniqueness"].path,`.
- In the proof loop, replace `target_id="uniqueness"` with `target=TARGETS["uniqueness"]`.
- In `demonstrate`, replace the wrapper with:

  ```python
      def witnessed(data, captured_bundle, *, target, seconds):
          with (root / "proof-executions.jsonl").open("ab") as stream:
              stream.write(
                  json.dumps(
                      {"solution": hashlib.sha256(data).hexdigest(), "seconds": seconds}
                  ).encode()
                  + b"\n"
              )
          return execute(data, captured_bundle, target=target, seconds=seconds)
  ```

`examples/m5/proof_cases.py`:
- Replace the target snapshot loop with:

  ```python
      for target_id, target in TARGETS.items():
          captured["target/" + target_id] = Snapshot(
              target.challenge, "m5/approved-target/" + target_id, "1"
          )
  ```

- In `proof_work`, replace `Proofs(host.ledger, host.session, bundle, target_id=target_id)` with `Proofs(host.ledger, host.session, bundle, target=TARGETS[target_id])`.
- Change the `witnessed` wrapper's signature to `(data, captured, *, target, seconds)`. Write `"target": target.theorem` into the log, and call `execute(data, captured, target=target, seconds=seconds)`.

`examples/m5/treatments.py`:
- Replace `Proofs(host.ledger, host.session, bundle, target_id=name)` with `Proofs(host.ledger, host.session, bundle, target=TARGETS[name])`.
- Change `witnessed` exactly as in `proof_cases.py`.
- `"target/" + name` snapshots: if `treatments.py` captures them from `proofs._target` or `RESOURCES` (run `grep -n "_target\|RESOURCES" examples/m5/treatments.py`), use `TARGETS[name].challenge` instead.

- [ ] **Step 10: Update the remaining tests**

Add `TARGETS` to each of these test files as in Step 2:
- `tests/test_proof_receipts.py`: every `Proofs(..., target_id="X")` becomes `Proofs(..., target=TARGETS["X"])`. In `boundary`, `def verify(data, bundle, *, target_id, seconds)` becomes `def verify(data, bundle, *, target, seconds)`, which calls `proofs._inputs(data, bundle, seconds, target)`. At line 235, `target_id="uniqueness"` becomes `target=TARGETS["uniqueness"]`. At lines 217 to 223, the "changed tools" case copies `RESOURCES` and still works because `RESOURCES` keeps its other files.
- `tests/test_proofs_native.py`: lines 66 and 96 use `target=TARGETS["uniqueness"]`.
- `tests/test_m4_fixture.py`: line 95 becomes `def verify(data, captured, *, target, seconds):`, and line 96 becomes `proofs._inputs(data, captured, seconds, target)`.

Find any remaining callers with `grep -rn "target_id=" src examples tests scripts`. The only hits allowed are recorded-identity reads such as `identity["target_id"]`.

- [ ] **Step 11: Run the fast suite**

Run: `uv run --locked pytest -q tests && uv run --locked ruff check . && uv run --locked ruff format --check .`
Expected: all pass.

- [ ] **Step 12: Rebuild the bundle and run the proof suites**

```bash
uv run --locked python scripts/build_proof_bundle.py runs/m4-tools-v2
WARRANTED_PROOF_TESTS=1 WARRANTED_PROOF_BUNDLE=runs/m4-tools-v2/bundle.json \
  uv run --locked pytest -q -m proof tests/test_proofs_native.py tests/test_m4_fixture.py tests/test_m5_treatments.py
```

Expected: all pass. The old bundle is refused because the policy digest changed.

- [ ] **Step 13: Docs and changelog**

In `docs/reference/proof-verification.md`:
- Replace the "trusted registry `src/warranted/proof/targets.json`" paragraph with one saying that a domain supplies each target as a `ProofTarget` (a challenge file and a theorem name). Say that the host passes both to the supervisor at verification time, and that the fixtures' three targets are listed in `examples/proof_targets.py`.
- Keep the table of the three targets, and change its "Challenge" column to the new paths.

In `CHANGELOG.md`, under `## [Unreleased]`, add:

```markdown
### Changed

- Proof targets are owned by domains: `ProofTarget(challenge_path, theorem)` replaces
  the core `targets.json` registry, and the verifier receives the challenge and
  theorem from the host. The three fixture challenges moved to `examples/`.
  `warranted.host.Proofs` takes `target=` instead of `target_id=`. Existing proof
  bundles are refused; rebuild with `scripts/build_proof_bundle.py`.

### Added

- `ProofTarget` in `warranted`.
```

- [ ] **Step 14: Commit**

```bash
git add -A src/warranted examples tests docs CHANGELOG.md
git commit -m "Let domains own proof targets

The verifier now receives the challenge and theorem from the host, so the
core no longer carries fixture theorems. The three challenges move to
examples/, and the host kit's Proofs takes a ProofTarget.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## PR B: task-layer API

Branch: `git switch -c proofs-api origin/main` (after PR A merges).

### Task B1: `VerdictStatus.UNPROVED`

**Files:**
- Modify: `src/warranted/_tasks.py:96-101` (enum) and `src/warranted/_tasks.py:2126-2134` (outcome mapping)
- Create: `tests/test_proof_checks.py`

**Interfaces:**
- Produces: `VerdictStatus.UNPROVED == "unproved"`. A check record's `result.json` has `{"passed": false}`, its outcome is `Outcome.SUCCEEDED` with code 0, and the submission decision is `"rejected"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_proof_checks.py`:

```python
"""Proofs in the task layer: UNPROVED, the verifier boundary, and LeanProof."""

import json

from test_experimental_tasks import CONFIG, Model, project

from warranted import RunOutcome, TaskSpec, Verdict, VerdictStatus
from warranted.host import Ledger


class Unproved:
    version = "1"
    isolated = True

    def check(self, ctx):
        return Verdict(VerdictStatus.UNPROVED, {"proof": "timed out"})


def test_unproved_fails_the_gate_and_is_recorded_distinctly(tmp_path):
    from test_experimental_tasks import CSV, TASK

    class Domain(CSV.CsvDomain):
        checkers = {"transformation": Unproved()}

    proj, _ = project(tmp_path, ["correct"], Domain())
    result = proj.start(
        TaskSpec(TASK.id, TASK.objective, TASK.inputs, TASK.private, TASK.checks, 1),
        CONFIG,
        Model(),
    )
    assert result.outcome is RunOutcome.REJECTED
    assert result.submissions[0].verdicts == {"transformation": VerdictStatus.UNPROVED}
    assert proj.status(result.run_id).submissions[0].verdicts == {
        "transformation": VerdictStatus.UNPROVED
    }
    with Ledger.open(proj.ledger_root) as ledger:
        verdicts = [
            json.loads(ledger.read_artifact(op.completion.observation.artifacts[c]))
            for op in ledger.operations()
            if op.request.origin.kind == "check"
            for c in op.completion.observation.artifacts
            if c == "verdict.json"
        ]
    assert verdicts == [{"status": "unproved", "feedback": {"proof": "timed out"}}]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run --locked pytest -q tests/test_proof_checks.py`
Expected: FAIL with `AttributeError: UNPROVED`.

- [ ] **Step 3: Implement**

In `src/warranted/_tasks.py`, add a member to `VerdictStatus` after `REJECTED`:

```python
    UNPROVED = "unproved"  # could not be established within limits; fails the gate
```

In `_run_check`'s outcome mapping, add:

```python
                VerdictStatus.UNPROVED: (Outcome.SUCCEEDED, 0),
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run --locked pytest -q tests/test_proof_checks.py tests/test_experimental_tasks.py tests/test_cli.py`
Expected: PASS. If `_cli.py` maps verdict statuses to text or exit codes, run `grep -n "VerdictStatus\|rejected" src/warranted/_cli.py`. Any exhaustive mapping then needs an `unproved` entry with the same exit status as `rejected`.

- [ ] **Step 5: Commit**

```bash
git add src/warranted/_tasks.py tests/test_proof_checks.py
git commit -m "Add VerdictStatus.UNPROVED

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B2: The verifier boundary, `CheckContext.verify`, and checker sources

**Files:**
- Create: `src/warranted/_proof_checks.py`
- Modify: `src/warranted/_tasks.py`: `CheckContext`, `_source_files`, `_project_identity`, `Project.__init__`, `Project.create`, and `_run_check`
- Test: `tests/test_proof_checks.py`

**Interfaces:**
- Consumes: `ProofTarget` and `Verification` from `warranted._proofs`.
- Produces: the `ProofVerifier` protocol: `identity: str` and `verify(source: bytes, target: ProofTarget) -> Verification`.
- Produces: `LeanVerifier(bundle_path: Path | str, seconds: int = 120)`, whose identity is `"lean-verifier-v1:" + sha256(bundle bytes)`.
- Produces: `Project(..., proofs: ProofVerifier | None = None)` and `Project.create(..., proofs=...)`. When `proofs` is given, the project identity gains `"proof_verifier": proofs.identity`.
- Produces: `CheckContext(candidate, inputs, private, jobs=None, proofs=None)` and `CheckContext.verify(target, source) -> Verification`. It records `proof_log: list[dict]` and `proof_output: dict[str, bytes]`.
- Produces: a check record gains `proofs.json` and `proofs/N/<raw name>` channels only when a proof was verified. Export leaves them out, because its channel list is an allow-list.
- Produces: an optional checker attribute `sources: Sequence[Path]`, folded into `domain_identity`'s `domain_source` digest.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proof_checks.py`:

```python
import hashlib
import runpy
from pathlib import Path

import pytest

from warranted import CheckContext, Project, domain_identity
from warranted._proofs import ProofStatus, Verification

ROOT = Path(__file__).resolve().parents[1]
TARGETS = runpy.run_path(str(ROOT / "examples/proof_targets.py"))["TARGETS"]


class FakeVerifier:
    """Test-only verifier: `outcomes` maps source bytes to a ProofStatus."""

    identity = "fake-verifier-v1"

    def __init__(self, outcomes=None, default=ProofStatus.REJECTED):
        self.outcomes, self.default, self.calls = dict(outcomes or {}), default, []

    def verify(self, source, target):
        self.calls.append((source, target.theorem))
        status = self.outcomes.get(source, self.default)
        return Verification(
            status,
            f"fake {status.value}",
            ("propext",),
            {"target_id": target.theorem},
            1,
            {"stdout": b"fake output"},
        )


def test_verify_records_the_call_as_host_only_evidence():
    ctx = CheckContext({}, {}, {}, None, FakeVerifier({b"proof": ProofStatus.PROVED}))
    result = ctx.verify(TARGETS["uniqueness"], b"proof")
    assert result.status is ProofStatus.PROVED
    assert ctx.proof_log == [
        {
            "target": "Warranted.uniqueness_preserved",
            "challenge": hashlib.sha256(TARGETS["uniqueness"].challenge).hexdigest(),
            "source": hashlib.sha256(b"proof").hexdigest(),
            "status": "proved",
            "diagnostic": "fake proved",
            "axioms": ["propext"],
            "identity": {"target_id": "Warranted.uniqueness_preserved"},
        }
    ]
    assert ctx.proof_output == {"proofs/1/stdout": b"fake output"}


def test_verify_without_a_verifier_is_a_host_error():
    with pytest.raises(RuntimeError, match="no proof verifier"):
        CheckContext({}, {}, {}).verify(TARGETS["uniqueness"], b"proof")


def test_the_verifier_is_bound_into_the_project_identity(tmp_path):
    from test_experimental_tasks import CSV, ENVIRONMENT, Script

    options = {"environment": Script([]), "environment_id": ENVIRONMENT}
    root = tmp_path / "project"
    Project.create(root, CSV.CsvDomain(), {"model": 1}, proofs=FakeVerifier(), **options)
    Project(root, CSV.CsvDomain(), proofs=FakeVerifier(), **options)

    class Rebuilt(FakeVerifier):
        identity = "fake-verifier-v2"

    with pytest.raises(ValueError, match="differs"):
        Project(root, CSV.CsvDomain(), proofs=Rebuilt(), **options)
    with pytest.raises(ValueError, match="differs"):
        Project(root, CSV.CsvDomain(), **options)


def test_checker_sources_are_pinned_in_the_domain_identity(tmp_path):
    from test_experimental_tasks import CSV

    challenge = tmp_path / "Target.lean"
    challenge.write_bytes(b"theorem t : True := by sorry\n")

    class Pinned:
        version = "1"
        isolated = True
        sources = (challenge,)

        def check(self, ctx):
            return Verdict(VerdictStatus.PASSED)

    class Domain(CSV.CsvDomain):
        checkers = {"pinned": Pinned()}

    before = domain_identity(Domain())["domain_source"]
    challenge.write_bytes(b"theorem t : False := by sorry\n")
    assert domain_identity(Domain())["domain_source"] != before
    challenge.unlink()
    with pytest.raises(ValueError, match="checker source"):
        domain_identity(Domain())
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --locked pytest -q tests/test_proof_checks.py`
Expected: FAIL (`CheckContext` takes no `proofs`, and `Project` has no `proofs`).

- [ ] **Step 3: Create `src/warranted/_proof_checks.py` with the verifier**

```python
"""Proofs as task-layer checkers: the verifier boundary and the LeanProof checker.

A domain owns its targets (ProofTarget); the project owns the verifier, whose
identity is bound into the project. See docs/proposals/m7-proofs.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from warranted import _proofs
from warranted._proofs import ProofTarget, Verification


class ProofVerifier(Protocol):
    """Host infrastructure that checks a proof source against a target."""

    # Bound into the project identity; change it when the verifier changes.
    identity: str

    def verify(self, source: bytes, target: ProofTarget) -> Verification: ...


class LeanVerifier:
    """The pinned Lean bundle and hardened container (docs/reference/proof-verification.md)."""

    def __init__(self, bundle_path: Path | str, seconds: int = _proofs.LIMITS["seconds"]):
        self.bundle = Path(bundle_path).read_bytes()
        _proofs._validate(b"x", seconds)
        self.seconds = seconds
        self.identity = "lean-verifier-v1:" + hashlib.sha256(self.bundle).hexdigest()

    def verify(self, source: bytes, target: ProofTarget) -> Verification:
        _proofs._validate(source, self.seconds)
        return _proofs._verify(source, self.bundle, target=target, seconds=self.seconds)
```

- [ ] **Step 4: Extend `CheckContext` in `src/warranted/_tasks.py`**

Add `import hashlib` to the imports if it's missing. Then replace the `CheckContext` class:

```python
class CheckContext(JobContext):
    """What a checker may read and do. Seeds, jobs, and proofs are host-only evidence.

    `candidate`, `inputs`, and `private` are exact bytes. `draw_seed` returns fresh
    entropy and records it; `run_job` runs untrusted code in a contained job and
    records the job and its result; `verify` checks a proof source against a
    domain-owned target with the project's verifier and records the result. None
    of these records reach worker feedback.
    """

    def __init__(
        self, candidate, inputs, private, jobs: JobRunner | None = None, proofs=None
    ):
        super().__init__(jobs)
        self.candidate: Mapping[str, bytes] = MappingProxyType(dict(candidate))
        self.inputs: Mapping[str, bytes] = MappingProxyType(dict(inputs))
        self.private: Mapping[str, bytes] = MappingProxyType(dict(private))
        self._proofs = proofs
        self.proof_log: list[dict] = []
        self.proof_output: dict[str, bytes] = {}

    def verify(self, target, source: bytes):
        if self._proofs is None:
            raise RuntimeError("this project has no proof verifier")
        result = self._proofs.verify(source, target)
        prefix = f"proofs/{len(self.proof_log) + 1}"
        for name, data in sorted(result.raw.items()):
            self.proof_output[f"{prefix}/{name}"] = data
        self.proof_log.append(
            {
                "target": target.theorem,
                "challenge": hashlib.sha256(target.challenge).hexdigest(),
                "source": hashlib.sha256(source).hexdigest(),
                "status": result.status.value,
                "diagnostic": result.diagnostic,
                "axioms": list(result.axioms),
                "identity": result.identity,
            }
        )
        return result
```

- [ ] **Step 5: Pin checker sources**

In `_source_files`, add this loop before the `for index, declared in enumerate(getattr(domain, "sources", ())):` loop:

```python
    for name, checker in sorted(domain.checkers.items()):
        for index, declared in enumerate(getattr(checker, "sources", ())):
            path = Path(declared)
            if not path.is_file():
                raise ValueError(f"declared checker source does not exist: {declared}")
            files.append((f"checker/{name}/{index}/{path.name}", path))
```

Then update the comment above the `Domain` protocol's `sources` line to read: "Checkers may declare `sources` too; both are pinned."

- [ ] **Step 6: Bind the verifier into the project**

Replace `_project_identity`:

```python
def _project_identity(
    domain: Domain, environment_id: str, jobs, proofs=None
) -> dict[str, str]:
    """A project binds its domain, worker environment, job runner, and verifier."""
    if not pinned_image(domain.worker_image):
        raise ValueError("domain worker image must be pinned by digest or image ID")
    runner = getattr(jobs, "identity", None)
    if type(runner) is not str or not runner:
        raise ValueError("job runner needs a stable identity string")
    identity = domain_identity(domain) | {
        "worker_environment": environment_id,
        "job_runner": runner,
    }
    if proofs is not None:
        verifier = getattr(proofs, "identity", None)
        if type(verifier) is not str or not verifier:
            raise ValueError("proof verifier needs a stable identity string")
        identity["proof_verifier"] = verifier
    return identity
```

In `Project.__init__`, add the keyword parameter `proofs=None`, store it with `self.proofs = proofs`, and change `_identity` to `return _project_identity(self.domain, self.environment_id, self.jobs, self.proofs)`. In `Project.create`, pass `options.get("proofs")` as the fourth argument to `_project_identity`.

- [ ] **Step 7: Pass the verifier to checks and record proofs**

In `_run_check`, construct the context with `self.proofs`:

```python
            context = CheckContext(
                {
                    n: ledger.read_artifact(ref.artifact)
                    for n, ref in files.items()
                    if n not in _WORKER_NOTES
                },
                run.task.contract(index).inputs,
                run.task.contract(index).private,
                self.jobs,
                self.proofs,
            )
```

In the `ledger.complete` raw mapping, add after `| context.job_output`:

```python
                | (
                    {"proofs.json": _json(context.proof_log)}
                    if context.proof_log
                    else {}
                )
                | context.proof_output,
```

Check that `OperationContext` (in `_operations.py`) still constructs its own context without `proofs`. Run `grep -n "CheckContext(" src`. Only `_run_check` should construct a `CheckContext`.

- [ ] **Step 8: Run the tests and confirm they pass**

Run: `uv run --locked pytest -q tests/test_proof_checks.py tests/test_experimental_tasks.py tests/test_experimental_mystery.py tests/test_domain_images.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/warranted/_tasks.py src/warranted/_proof_checks.py tests/test_proof_checks.py
git commit -m "Bind a proof verifier into projects and record proofs in checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B3: The `LeanProof` checker and refusing a task without a verifier

**Files:**
- Modify: `src/warranted/_proof_checks.py`, `src/warranted/_tasks.py` (`Project._start`)
- Test: `tests/test_proof_checks.py`

**Interfaces:**
- Consumes: `CheckContext.verify`, `ProofTarget`, `Verdict`, `VerdictStatus`.
- Produces: `LeanProof(target: ProofTarget, source: str = "Solution.lean", premises: Callable[[CheckContext], Mapping[str, bool]] | None = None)`, with attributes `version`, `isolated = True`, `needs_proofs = True`, and `sources = (target.path,)`.
- Produces: feedback `{"proof": <status or "missing"/"invalid">, "diagnostic": str, "axioms": [..], "premises": {..}}`. The `error` key appears only for missing or invalid source.
- Produces: `Project.start` raises `ValueError("checks [...] need a proof verifier ...")` before any run record when a required check of any scheduled contract has `needs_proofs` set and the project has no verifier.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proof_checks.py`:

```python
import base64

from warranted import LeanProof

PROOF = b"theorem uniqueness_preserved : UniquenessTarget := by simp"


def workspace(files):
    return json.dumps(
        {n: base64.b64encode(d).decode() for n, d in files.items()}
    ).encode()


def checked(verifier, files, premises=None):
    ctx = CheckContext({"workspace.json": workspace(files)}, {}, {}, None, verifier)
    return LeanProof(TARGETS["uniqueness"], premises=premises).check(ctx), ctx


@pytest.mark.parametrize(
    "status, expected",
    [
        (ProofStatus.PROVED, VerdictStatus.PASSED),
        (ProofStatus.REJECTED, VerdictStatus.REJECTED),
        (ProofStatus.UNPROVED, VerdictStatus.UNPROVED),
        (ProofStatus.UNSUPPORTED, VerdictStatus.UNSUPPORTED),
        (ProofStatus.INFRASTRUCTURE_FAILURE, VerdictStatus.INFRASTRUCTURE_FAILURE),
    ],
)
def test_proof_outcomes_map_to_verdicts(status, expected):
    verdict, _ = checked(FakeVerifier({PROOF: status}), {"Solution.lean": PROOF})
    assert verdict.status is expected
    assert verdict.feedback["proof"] == status.value


def test_a_proved_theorem_whose_premise_fails_is_unsupported():
    premises = lambda ctx: {"input_unique": False, "selection": True}  # noqa: E731
    verifier = FakeVerifier({PROOF: ProofStatus.PROVED})
    verdict, _ = checked(verifier, {"Solution.lean": PROOF}, premises)
    assert verdict.status is VerdictStatus.UNSUPPORTED
    assert verdict.feedback["proof"] == "proved"
    assert verdict.feedback["premises"] == {"input_unique": False, "selection": True}


def test_premises_run_even_when_the_proof_fails():
    seen = []

    def premises(ctx):
        seen.append(True)
        return {"input_unique": True}

    verdict, _ = checked(FakeVerifier(), {"Solution.lean": PROOF}, premises)
    assert verdict.status is VerdictStatus.REJECTED and seen == [True]
    assert verdict.feedback["premises"] == {"input_unique": True}


@pytest.mark.parametrize(
    "premises",
    [lambda ctx: {"x": "yes"}, lambda ctx: 1 / 0],  # non-boolean result; a crash
)
def test_a_faulty_premise_is_never_a_pass(premises):
    with pytest.raises((TypeError, ZeroDivisionError)):
        checked(FakeVerifier({PROOF: ProofStatus.PROVED}), {"Solution.lean": PROOF}, premises)


@pytest.mark.parametrize(
    "files", [{}, {"Solution.lean": b""}, {"Solution.lean": b"x" * (1024 * 1024 + 1)}]
)
def test_a_missing_or_invalid_source_is_rejected_without_verifying(files):
    verifier = FakeVerifier({PROOF: ProofStatus.PROVED})
    verdict, ctx = checked(verifier, files)
    assert verdict.status is VerdictStatus.REJECTED
    assert verdict.feedback["proof"] in {"missing", "invalid"}
    assert verifier.calls == [] and ctx.proof_log == []


def test_a_forged_verdict_file_in_the_workspace_is_ignored():
    forged = {"Solution.lean": b"wrong", "verdict.json": b'{"status": "proved"}'}
    verdict, _ = checked(FakeVerifier({PROOF: ProofStatus.PROVED}), forged)
    assert verdict.status is VerdictStatus.REJECTED


def test_a_lean_proof_pins_its_challenge():
    proof = LeanProof(TARGETS["uniqueness"])
    assert proof.sources == (TARGETS["uniqueness"].path,)
    assert proof.needs_proofs is True and proof.isolated is True


def test_a_task_needing_a_proof_is_refused_without_a_verifier(tmp_path):
    from test_experimental_tasks import CSV, TASK

    class Domain(CSV.CsvDomain):
        checkers = {**CSV.CsvDomain.checkers, "uniqueness": LeanProof(TARGETS["uniqueness"])}

    proj, script = project(tmp_path, ["correct"], Domain())
    task = TaskSpec(
        TASK.id, TASK.objective, TASK.inputs, TASK.private,
        ("transformation", "uniqueness"), 1,
    )
    with pytest.raises(ValueError, match="need a proof verifier"):
        proj.start(task, CONFIG, Model())
    assert proj.runs() == () and script.calls == 0
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --locked pytest -q tests/test_proof_checks.py`
Expected: ImportError (`LeanProof`).

- [ ] **Step 3: Implement `LeanProof`**

Append to `src/warranted/_proof_checks.py`. Add `import base64`, `import json`, and `from typing import Callable, Mapping` at the top, and add a function-local import of the task types to avoid an import cycle:

```python
class LeanProof:
    """A checker: verify the worker's Lean source against a domain-owned target.

    The source is the captured workspace file `source`. `premises`, if given, is
    trusted domain code returning {name: bool}; a proved theorem whose premise
    fails does not apply here, so the verdict is UNSUPPORTED. A proof never
    authorizes acceptance on its own: tasks still require their other checks.
    """

    isolated = True  # a contained verifier over captured bytes; no shared state
    needs_proofs = True

    def __init__(
        self,
        target: ProofTarget,
        source: str = "Solution.lean",
        premises: Callable | None = None,
    ):
        if not isinstance(target, ProofTarget):
            raise ValueError("LeanProof needs a ProofTarget")
        if type(source) is not str or not source or "/" in source:
            raise ValueError("source must be a workspace file name")
        self.target, self.source, self.premises = target, source, premises
        self.sources = (target.path,)
        self.version = "1:" + target.theorem

    def check(self, ctx):
        from warranted._tasks import Verdict, VerdictStatus

        premises = {} if self.premises is None else dict(self.premises(ctx))
        if not all(type(n) is str and type(v) is bool for n, v in premises.items()):
            raise TypeError("premises must map names to booleans")
        try:
            files = json.loads(ctx.candidate["workspace.json"])
            data = base64.b64decode(files[self.source], validate=True)
        except (KeyError, TypeError, ValueError):
            return Verdict(
                VerdictStatus.REJECTED,
                {
                    "proof": "missing",
                    "error": f"{self.source} was not submitted",
                    "premises": premises,
                },
            )
        if not 0 < len(data) <= _proofs.SOURCE_LIMIT:
            return Verdict(
                VerdictStatus.REJECTED,
                {
                    "proof": "invalid",
                    "error": f"{self.source} must be 1 to "
                    f"{_proofs.SOURCE_LIMIT} bytes",
                    "premises": premises,
                },
            )
        result = ctx.verify(self.target, data)
        status = {
            _proofs.ProofStatus.PROVED: VerdictStatus.PASSED
            if all(premises.values())
            else VerdictStatus.UNSUPPORTED,
            _proofs.ProofStatus.REJECTED: VerdictStatus.REJECTED,
            _proofs.ProofStatus.UNPROVED: VerdictStatus.UNPROVED,
            _proofs.ProofStatus.UNSUPPORTED: VerdictStatus.UNSUPPORTED,
            _proofs.ProofStatus.INFRASTRUCTURE_FAILURE: (
                VerdictStatus.INFRASTRUCTURE_FAILURE
            ),
        }[result.status]
        feedback = {
            "proof": result.status.value,
            "diagnostic": result.diagnostic[:4096],
            "axioms": list(result.axioms),
            "premises": premises,
        }
        return Verdict(status, feedback)
```

`_proofs.SOURCE_LIMIT` exists. `ctx.candidate["workspace.json"]` is the captured workspace map, whose values are base64. The sandbox captures it on submission (`src/warranted/_sandbox.py`).

- [ ] **Step 4: Refuse a task without a verifier in `Project._start`**

In `_start`, right after the `unknown = required - set(self.domain.checkers)` check, add:

```python
        needs = sorted(
            n for n in required if getattr(self.domain.checkers[n], "needs_proofs", False)
        )
        if needs and self.proofs is None:
            raise ValueError(
                f"checks {needs} need a proof verifier; create the project with proofs="
            )
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run --locked pytest -q tests/test_proof_checks.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/warranted/_proof_checks.py src/warranted/_tasks.py tests/test_proof_checks.py
git commit -m "Add the LeanProof checker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B4: Export the API, document it, and update the changelog

**Files:**
- Modify: `src/warranted/__init__.py`, `docs/reference/api.md`, `docs/reference/proof-verification.md`, `CHANGELOG.md`

**Interfaces:**
- Produces: `warranted` exports `LeanProof`, `LeanVerifier`, `ProofResult` (an alias of `_proofs.Verification`), `ProofStatus`, `ProofTarget`, and `ProofVerifier`.

- [ ] **Step 1: Export**

In `src/warranted/__init__.py`, add these imports and the matching `__all__` entries in sorted position:

```python
from warranted._proof_checks import LeanProof, LeanVerifier, ProofVerifier
from warranted._proofs import ProofStatus, ProofTarget
from warranted._proofs import Verification as ProofResult
```

- [ ] **Step 2: Run the release test and confirm it fails**

Run: `uv run --locked pytest -q tests/test_release.py`
Expected: FAIL, because `LeanProof`, `LeanVerifier`, `ProofResult`, `ProofStatus`, and `ProofVerifier` are undocumented.

- [ ] **Step 3: Document the names**

Add these rows to the `## Proofs` table in `docs/reference/api.md`:

```markdown
| `LeanProof` | A checker: verifies the worker's `Solution.lean` against a target, then checks the domain's premises | [proofs in the task layer](../proposals/m7-proofs.md#data-flow) |
| `ProofVerifier` | Protocol for the verifier a project is created with (`proofs=`); bound into the project identity | [proofs in the task layer](../proposals/m7-proofs.md#identity-and-pinning) |
| `LeanVerifier` | The verifier: the pinned Lean bundle and hardened container | [proof verification](proof-verification.md#verification-flow) |
| `ProofResult` | A verification's status, diagnostic, axiom set, identity, elapsed time, and raw evidence | [proof verification](proof-verification.md#verification-flow) |
| `ProofStatus` | Proved, rejected, unproved, unsupported, or infrastructure failure | [proofs in the task layer](../proposals/m7-proofs.md#data-flow) |
```

In the `## Domains and checkers` table, change the `VerdictStatus` row's description to "Passed, rejected, unproved, unsupported, or infrastructure failure".

At the end of `docs/reference/proof-verification.md`'s introduction, add one paragraph: "In the task layer, a domain lists a `LeanProof` checker and the project is created with `proofs=LeanVerifier(bundle)`; see [proofs in the task layer](../proposals/m7-proofs.md)."

- [ ] **Step 4: Update the changelog**

Under `## [Unreleased]` → `### Added` in `CHANGELOG.md`, add:

```markdown
- Proofs in the task layer: the `LeanProof` checker, `ProofVerifier` and
  `LeanVerifier` (passed as `Project(..., proofs=...)` and bound into the project
  identity), `ProofResult`, `ProofStatus`, and `CheckContext.verify`. Checkers may
  declare `sources`, which are pinned like domain sources.
- `VerdictStatus.UNPROVED`: fails the gate and is recorded distinctly.
```

- [ ] **Step 5: Run the full checks**

Run: `uv run --locked pytest -q tests && uv run --locked ruff check . && uv run --locked ruff format --check . && uv build --no-sources`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/warranted/__init__.py docs CHANGELOG.md
git commit -m "Export and document the proof API

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## PR C: fixtures

Branch: `git switch -c proofs-fixtures origin/main` (after PR B merges).

### Task C1: CSV uniqueness and timestamp proofs

**Files:**
- Modify: `examples/m7/csv/domain.py`
- Create: `examples/m7/csv/proof-uniqueness.toml`, `examples/m7/csv/proof-timestamp.toml`
- Create: `tests/test_proof_fixtures.py`

**Interfaces:**
- Consumes: `LeanProof`, `ProofTarget`, `CheckContext`, and `FakeVerifier` (copied into the new test file).
- Produces: `CsvDomain.checkers` gains `"uniqueness"` and `"timestamp"`. The premises are `uniqueness_premises(ctx) -> {"input_unique", "identity_selection"}` and `timestamp_premises(ctx) -> {"single_offset"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proof_fixtures.py`:

```python
"""The fixtures' proof cases on the task layer (docs/proposals/m7-proofs.md)."""

import base64
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_experimental_tasks import CANDIDATES, CONFIG, CSV, ENVIRONMENT, Model

from warranted import (
    LeanVerifier,
    Project,
    ProofResult,
    ProofStatus,
    RunOutcome,
    TaskSpec,
    VerdictStatus,
)
from warranted.host import AttemptResult, Outcome, Result

ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "examples/m7/csv"
UNIQUENESS_PROOF = (ROOT / "examples/m4/Solution.lean").read_bytes()
TIMESTAMP_PROOF = (ROOT / "examples/m5/Timestamp.lean").read_bytes()
MIGRATION_PROOF = (ROOT / "examples/m5/Migration.lean").read_bytes()


class FakeVerifier:
    identity = "fake-verifier-v1"

    def __init__(self, proved=(UNIQUENESS_PROOF, TIMESTAMP_PROOF, MIGRATION_PROOF)):
        self.proved, self.calls = set(proved), 0

    def verify(self, source, target):
        self.calls += 1
        status = ProofStatus.PROVED if source in self.proved else ProofStatus.REJECTED
        return ProofResult(status, "fake", ("propext",), {}, 1, {})


class Worker:
    """Episode i submits plan[i] = (result.json payload, proof bytes or None)."""

    def __init__(self, plan):
        self.plan, self.episodes = list(plan), []

    def __call__(self, ledger_root, episode):
        self.episodes.append(episode)
        result, proof = self.plan[int(episode.episode_id.rsplit("-s", 1)[1]) - 1]
        files = {} if proof is None else {"Solution.lean": proof}

        class Environment:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __call__(self, request, payload):
                return AttemptResult(
                    Result(Outcome.SUCCEEDED, 0, {"tool": 1}, 1),
                    {
                        "stdout": b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n",
                        "stderr": b"",
                        "candidate/result.json": json.dumps(result).encode(),
                        "candidate/workspace.json": json.dumps(
                            {n: base64.b64encode(d).decode() for n, d in files.items()}
                        ).encode(),
                    },
                )

        return Environment()


def csv_project(tmp_path, plan, verifier=None):
    worker = Worker(plan)
    proj = Project.create(
        tmp_path / "project",
        CSV.CsvDomain(),
        {"model": 40, "tool": 40, "check": 40},
        environment=worker,
        environment_id=ENVIRONMENT,
        proofs=verifier or FakeVerifier(),
    )
    return proj, worker


def one(task):
    return replace(task, submissions=1)


UNIQUENESS = TaskSpec.load(CSV_DIR / "proof-uniqueness.toml")
TIMESTAMP = TaskSpec.load(CSV_DIR / "proof-timestamp.toml")


@pytest.mark.parametrize(
    "candidate, decision, proof",
    [
        ("correct", "accepted", VerdictStatus.PASSED),
        ("dropped-row", "rejected", VerdictStatus.PASSED),  # the proof cannot rescue it
        ("empty", "rejected", VerdictStatus.PASSED),
    ],
)
def test_uniqueness_proof_never_replaces_the_transformation_check(
    tmp_path, candidate, decision, proof
):
    proj, _ = csv_project(tmp_path, [(CANDIDATES[candidate], UNIQUENESS_PROOF)])
    result = proj.start(one(UNIQUENESS), CONFIG, Model())
    assert [s.decision for s in result.submissions] == [decision]
    assert result.submissions[0].verdicts["uniqueness"] is proof


def test_duplicate_identifiers_leave_the_theorem_valid_but_unsupported(tmp_path):
    duplicates = (ROOT / "examples/m4/input-duplicates.csv").read_bytes()
    task = one(replace(UNIQUENESS, inputs={**UNIQUENESS.inputs, "input.csv": duplicates}))
    proj, _ = csv_project(tmp_path, [(CANDIDATES["correct"], UNIQUENESS_PROOF)])
    result = proj.start(task, CONFIG, Model())
    assert result.submissions[0].verdicts["uniqueness"] is VerdictStatus.UNSUPPORTED
    assert result.outcome is RunOutcome.REJECTED


def test_a_wrong_proof_is_rejected_and_reported(tmp_path):
    proj, _ = csv_project(tmp_path, [(CANDIDATES["correct"], b"theorem nope : True := trivial")])
    result = proj.start(one(UNIQUENESS), CONFIG, Model())
    assert result.submissions[0].verdicts["uniqueness"] is VerdictStatus.REJECTED
    assert result.outcome is RunOutcome.REJECTED


@pytest.mark.parametrize(
    "candidate, decision", [("correct", "accepted"), ("wrong-offset", "rejected")]
)
def test_the_timestamp_proof_holds_for_either_offset_but_the_check_decides(
    tmp_path, candidate, decision
):
    proj, _ = csv_project(tmp_path, [(CANDIDATES[candidate], TIMESTAMP_PROOF)])
    result = proj.start(one(TIMESTAMP), CONFIG, Model())
    assert result.submissions[0].verdicts["timestamp"] is VerdictStatus.PASSED
    assert [s.decision for s in result.submissions] == [decision]


def test_the_proof_task_is_refused_without_a_verifier(tmp_path):
    proj = Project.create(
        tmp_path / "project",
        CSV.CsvDomain(),
        {"model": 1},
        environment=Worker([]),
        environment_id=ENVIRONMENT,
    )
    with pytest.raises(ValueError, match="need a proof verifier"):
        proj.start(UNIQUENESS, CONFIG, Model())
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --locked pytest -q tests/test_proof_fixtures.py`
Expected: FAIL (`proof-uniqueness.toml` is missing).

- [ ] **Step 3: Create the task files**

`examples/m7/csv/proof-uniqueness.toml`:

```toml
# The CSV task with the M4 uniqueness proof: the proof is required beside the
# transformation check, so it can never replace it.
id = "csv-proof-uniqueness"
objective = """Read worker-task.md and the current offset. Write result.json, and \
Solution.lean proving Warranted.uniqueness_preserved from uniqueness.md, then submit."""
checks = ["transformation", "uniqueness"]

[inputs]
"input.csv" = "../../m2/fixture/input.csv"
"worker-task.md" = "../../m3/worker-task.md"
"offset-v1" = "offset-v1"
"definition-v1" = "definition-v1"
"uniqueness.md" = "UniquenessChallenge.lean"

[private]
"references.json" = "../../m2/fixture/references.json"

[budgets]
submissions = 3
```

`examples/m7/csv/proof-timestamp.toml` is the same, except for:
- `id = "csv-proof-timestamp"`
- `checks = ["transformation", "timestamp"]`
- the objective names `Warranted.timestamp_roundtrip` from `timestamp.md`
- the input `"timestamp.md" = "TimestampChallenge.lean"` in place of `uniqueness.md`

The challenge is given to the worker under a `.md` name because the task file name rules (`_NAME`) accept it, and it marks the file as a statement to read. The worker writes its own `Solution.lean`.

- [ ] **Step 4: Add the checkers and premises to `examples/m7/csv/domain.py`**

Add `LeanProof` and `ProofTarget` to the `from warranted import ...` line, then add before `class CsvDomain`:

```python
UNIQUENESS = ProofTarget(HERE / "UniquenessChallenge.lean", "Warranted.uniqueness_preserved")
TIMESTAMP = ProofTarget(HERE / "TimestampChallenge.lean", "Warranted.timestamp_roundtrip")


def _candidate_rows(ctx: CheckContext) -> list | None:
    try:
        rows = M2["decode"](ctx.candidate["result.json"])["rows"]
    except (KeyError, TypeError, ValueError, RecursionError):
        return None
    return rows if type(rows) is list else None


def uniqueness_premises(ctx: CheckContext) -> dict[str, bool]:
    """M4's application checks: unique input IDs, and an identity selection of them."""
    source = [row[0] for row in M2["read_rows"](ctx.inputs["input.csv"])]
    rows = _candidate_rows(ctx)
    ids = [] if rows is None else [r[0] for r in rows if type(r) is list and r]
    remaining = iter(source)
    return {
        "input_unique": len(source) == len(set(source)),
        # In order, each candidate ID is a source ID: the mapping is the identity.
        "identity_selection": rows is not None and all(i in remaining for i in ids),
    }


def timestamp_premises(ctx: CheckContext) -> dict[str, bool]:
    """M5's timestamp application: one fixed offset, in whole seconds, for every row."""
    from datetime import datetime

    local = {r[0]: r[1] for r in M2["read_rows"](ctx.inputs["input.csv"])}
    offsets = set()
    for row in _candidate_rows(ctx) or []:
        try:
            utc = datetime.strptime(row[1], "%Y-%m-%dT%H:%M:%SZ")
            offsets.add(int((datetime.fromisoformat(local[row[0]]) - utc).total_seconds()))
        except (KeyError, TypeError, ValueError, IndexError):
            return {"single_offset": False}
    return {"single_offset": len(offsets) == 1}
```

Change `CsvDomain.checkers` to:

```python
    checkers = {
        "transformation": Transformation(),
        "uniqueness": LeanProof(UNIQUENESS, premises=uniqueness_premises),
        "timestamp": LeanProof(TIMESTAMP, premises=timestamp_premises),
    }
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run --locked pytest -q tests/test_proof_fixtures.py tests/test_csv_fixture.py tests/test_experimental_tasks.py tests/test_public_api.py`
Expected: PASS. Existing CSV tests are unaffected because their tasks don't require the proof checks, and a project only needs a verifier for tasks that do.

- [ ] **Step 6: Commit**

```bash
git add examples/m7/csv tests/test_proof_fixtures.py
git commit -m "Run the CSV fixture's proof cases on the task layer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task C2: The migration proof

**Files:**
- Modify: `examples/m7/migration/domain.py`
- Create: `examples/m7/migration/proof.toml`
- Modify: `tests/test_proof_fixtures.py`

**Interfaces:**
- Consumes: `LocalJobs` from `tests/test_experimental_mystery.py`; `CANDIDATES` and `submit` patterns from `tests/test_migration_fixture.py`.
- Produces: `MigrationDomain.checkers["renaming"] = LeanProof(MIGRATION, premises=renaming_premises)`; `renaming_premises(ctx) -> {"renaming_correspondence": bool}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proof_fixtures.py`:

```python
from test_experimental_mystery import LocalJobs
from test_migration_fixture import CANDIDATES as MIGRATIONS
from test_migration_fixture import MIGRATION

MIGRATION_TASK = TaskSpec.load(ROOT / "examples/m7/migration/proof.toml")


@pytest.mark.parametrize(
    "candidate, decision", [("complete", "accepted"), ("drop-label", "rejected")]
)
def test_the_renaming_proof_omits_the_label_and_the_obligation_decides(
    tmp_path, candidate, decision
):
    worker = Worker([({"migrate.py": MIGRATIONS[candidate]}, MIGRATION_PROOF)])
    proj = Project.create(
        tmp_path / "project",
        MIGRATION.MigrationDomain(),
        {"model": 40, "tool": 40, "check": 40},
        environment=worker,
        environment_id=ENVIRONMENT,
        jobs=LocalJobs(),
        proofs=FakeVerifier(),
    )
    result = proj.start(replace(MIGRATION_TASK, submissions=1), CONFIG, Model())
    assert result.submissions[0].verdicts["renaming"] is VerdictStatus.PASSED
    assert [s.decision for s in result.submissions] == [decision]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --locked pytest -q tests/test_proof_fixtures.py -k renaming`
Expected: FAIL (`proof.toml` is missing).

- [ ] **Step 3: Refactor the case runner and add the checker**

In `examples/m7/migration/domain.py`, add `LeanProof` and `ProofTarget` to the import. Then extract the per-case loop from `Migration.check` into a module function, and call it from both places:

```python
def run_cases(ctx: CheckContext, source: bytes, cases: dict) -> dict:
    """Run each case in its own contained job; return {case name: judge result}."""
    detail = {}
    for name, case in cases.items():
        job = ctx.run_job(
            DEFAULT_WORKER_IMAGE,
            ["python", "-I", "migrate.py"],
            {"migrate.py": source},
            stdin=case["input"].encode(),
            timeout_seconds=M5["CASE_SECONDS"],
        )
        detail[name] = M5["judge"](
            case, job.stdout, job.returncode, job.timed_out or job.truncated
        )
    return detail
```

In `Migration.check`, replace the `for name, case in cases.items() if source is not None else ():` loop body with:

```python
        detail = run_cases(ctx, source, cases) if source is not None else {}
        for values in detail.values():
            for obligation, passed in values.items():
                results[obligation] = results.get(obligation, True) and passed
```

Then add:

```python
MIGRATION = ProofTarget(HERE / "MigrationChallenge.lean", "Warranted.migration_renaming")


def renaming_premises(ctx: CheckContext) -> dict[str, bool]:
    """M5's migration application: the candidate renames host and timeout exactly.

    The theorem covers both label variants, so the label is not a premise; the
    legacy_label obligation keeps that gap closed.
    """
    try:
        source = M5["candidate_source"](ctx.candidate["result.json"])
    except (KeyError, ValueError, RecursionError):
        return {"renaming_correspondence": False}
    cases = {
        n: c for n, c in json.loads(ctx.private["references.json"]).items()
        if c["kind"] == "migrate"
    }
    if not cases:
        raise ValueError("no migrate case assesses renaming")
    detail = run_cases(ctx, source, cases)
    return {"renaming_correspondence": all(d["renaming"] for d in detail.values())}
```

Change `MigrationDomain.checkers` to `{"migration": Migration(), "renaming": LeanProof(MIGRATION, premises=renaming_premises)}`.

- [ ] **Step 4: Create `examples/m7/migration/proof.toml`**

Copy `examples/m7/migration/task.toml`, then make these changes:
- set `id = "config-migration-proof"`
- set `checks = ["migration", "renaming"]`
- add the input `"renaming.md" = "MigrationChallenge.lean"`
- use this objective: `"""Read README.md and change only migrate.py. Write result.json as {"migrate.py": "<source>"}, and Solution.lean proving Warranted.migration_renaming from renaming.md, then submit."""`

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run --locked pytest -q tests/test_proof_fixtures.py tests/test_migration_fixture.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add examples/m7/migration tests/test_proof_fixtures.py
git commit -m "Run the migration fixture's proof case on the task layer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task C3: Real-verifier tests, CI, docs, and 0.2.0

**Files:**
- Modify: `tests/test_proof_fixtures.py`, `.github/workflows/ci.yml`, `docs/fixtures/csv-transformation.md`, `docs/fixtures/config-migration.md`, `docs/roadmap.md`, `README.md`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock`

**Interfaces:**
- Consumes: `LeanVerifier(bundle_path)`, the task files from C1 and C2, and the env vars `WARRANTED_PROOF_TESTS` and `WARRANTED_PROOF_BUNDLE`.

- [ ] **Step 1: Add the proof-marked tests**

Append to `tests/test_proof_fixtures.py`:

```python
needs_lean = pytest.mark.skipif(
    os.environ.get("WARRANTED_PROOF_TESTS") != "1"
    or not os.environ.get("WARRANTED_PROOF_BUNDLE"),
    reason="requires WARRANTED_PROOF_TESTS=1 and WARRANTED_PROOF_BUNDLE",
)


@pytest.mark.proof
@needs_lean
@pytest.mark.parametrize(
    "task, check, proof, candidate, decision",
    [
        (UNIQUENESS, "uniqueness", UNIQUENESS_PROOF, "correct", "accepted"),
        (UNIQUENESS, "uniqueness", UNIQUENESS_PROOF, "dropped-row", "rejected"),
        (TIMESTAMP, "timestamp", TIMESTAMP_PROOF, "wrong-offset", "rejected"),
    ],
)
def test_real_proofs_on_the_task_layer(tmp_path, task, check, proof, candidate, decision):
    verifier = LeanVerifier(os.environ["WARRANTED_PROOF_BUNDLE"])
    proj, _ = csv_project(tmp_path, [(CANDIDATES[candidate], proof)], verifier)
    result = proj.start(one(task), CONFIG, Model())
    assert result.submissions[0].verdicts[check] is VerdictStatus.PASSED
    assert [s.decision for s in result.submissions] == [decision]


@pytest.mark.proof
@needs_lean
def test_a_sorry_proof_is_rejected_by_the_real_verifier(tmp_path):
    verifier = LeanVerifier(os.environ["WARRANTED_PROOF_BUNDLE"])
    sorry = UNIQUENESS_PROOF.replace(b":= by", b":= by sorry --", 1)
    proj, _ = csv_project(tmp_path, [(CANDIDATES["correct"], sorry)], verifier)
    result = proj.start(one(UNIQUENESS), CONFIG, Model())
    assert result.submissions[0].verdicts["uniqueness"] is VerdictStatus.REJECTED
```

Before committing, check `UNIQUENESS_PROOF` contains `:= by`, with `grep -c ":= by" examples/m4/Solution.lean`. If it doesn't, build the `sorry` source as the challenge text with `sorry` kept, which is `TARGETS["uniqueness"].challenge`.

- [ ] **Step 2: Run the proof tests**

```bash
WARRANTED_PROOF_TESTS=1 WARRANTED_PROOF_BUNDLE=runs/m4-tools-v2/bundle.json \
  uv run --locked pytest -q -m proof tests/test_proof_fixtures.py
```

Expected: 4 passed. Then run `uv run --locked pytest -q tests/test_proof_fixtures.py`. Expected: the fast tests pass and the 4 proof tests are skipped.

- [ ] **Step 3: Add the file to CI's proof job**

In `.github/workflows/ci.yml`, in the step "Exercise proof verification, isolation, and receipt recovery", add a fourth background run and its wait:

```yaml
          uv run --locked pytest -q -m proof --durations=5 tests/test_proof_fixtures.py > /tmp/proofs-fixtures.log 2>&1 &
          fixtures_pid=$!
```

Add `wait "$fixtures_pid" || failed=1`, and add `/tmp/proofs-fixtures.log` to the `cat` line. That step's existing environment already sets the env vars the other proof tests need; check with `grep -n "WARRANTED_PROOF" .github/workflows/ci.yml`.

Also add `'examples/proof_targets.py', 'examples/m7/**/*Challenge.lean'` to both `hashFiles(...)` lists in the proof job's cache keys, so a changed challenge rebuilds the cache.

- [ ] **Step 4: Update the docs**

- `docs/fixtures/csv-transformation.md`, `## Task layer`: replace the sentence "The M4 uniqueness proof is not yet a task-layer check; the M1–M4 drivers remain for their own guarantees." with a paragraph. It describes `proof-uniqueness.toml` and `proof-timestamp.toml`, their premises (`input_unique`, `identity_selection`, `single_offset`), and the table of cases from Task C1.
- `docs/fixtures/config-migration.md`, `## Task layer`: replace "The proof cases and the A–E treatments stay on the host kit." with a paragraph on `proof.toml`, the `renaming_correspondence` premise, and the fact that the A–E treatments stay on the host kit.
- `README.md`: in the status bullet about fixtures on the task layer, drop "without their proof cases" and "such as proof applications".
- `docs/roadmap.md`: in the Public API item, drop ", without their proof cases". In the M7 status paragraph, change "Both existing fixtures run through the public API without their proof cases, and not yet through the CLI." to "Both existing fixtures, including their proof cases, run through the public API, but not yet through the CLI."

- [ ] **Step 5: Release 0.2.0 in the changelog and version**

In `CHANGELOG.md`, rename `## [Unreleased]` to `## [0.2.0] - <today's date>` and add a fresh empty `## [Unreleased]` above it. Add `- The CSV and migration fixtures' proof cases run on the task layer.` under 0.2.0's *Added*. Leave the 0.1.0 entry unchanged, because released entries are history. Update the link references at the bottom:

```markdown
[Unreleased]: https://github.com/Lewdwig-V/warranted/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Lewdwig-V/warranted/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Lewdwig-V/warranted/releases/tag/v0.1.0
```

Set `version = "0.2.0"` in `pyproject.toml` and run `uv lock`.

- [ ] **Step 6: Run the full checks**

Run: `uv run --locked pytest -q tests && uv run --locked ruff check . && uv run --locked ruff format --check . && uv build --no-sources`
Expected: all pass. `tests/test_release.py` confirms that 0.2.0 matches.

- [ ] **Step 7: Commit**

```bash
git add -A tests .github docs README.md CHANGELOG.md pyproject.toml uv.lock
git commit -m "Run proof cases with the real verifier in CI; release 0.2.0

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

After PR C merges, tag `v0.2.0` on the merge commit, but only when the user confirms (see `docs/reference/api.md#releasing`).
