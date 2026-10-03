"""The fixtures' proof cases on the task layer (docs/proposals/m7-proofs.md)."""

import base64
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_experimental_mystery import LocalJobs
from test_experimental_tasks import CANDIDATES, CONFIG, CSV, ENVIRONMENT, Model
from test_migration_fixture import CANDIDATES as MIGRATIONS
from test_migration_fixture import MIGRATION

from warranted import (
    CheckContext,
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
    task = one(
        replace(UNIQUENESS, inputs={**UNIQUENESS.inputs, "input.csv": duplicates})
    )
    proj, _ = csv_project(tmp_path, [(CANDIDATES["correct"], UNIQUENESS_PROOF)])
    result = proj.start(task, CONFIG, Model())
    assert result.submissions[0].verdicts["uniqueness"] is VerdictStatus.UNSUPPORTED
    assert [s.decision for s in result.submissions] == ["unsupported"]
    assert result.outcome is RunOutcome.UNSUPPORTED


def test_a_wrong_proof_is_rejected_and_reported(tmp_path):
    proj, _ = csv_project(
        tmp_path, [(CANDIDATES["correct"], b"theorem nope : True := trivial")]
    )
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


def test_timestamp_premises_require_one_offset_for_every_row():
    task = TIMESTAMP

    def single_offset(result):
        ctx = CheckContext(
            {"result.json": json.dumps(result).encode()}, task.inputs, task.private
        )
        return CSV.timestamp_premises(ctx)

    assert single_offset(CANDIDATES["correct"]) == {"single_offset": True}
    assert single_offset(CANDIDATES["wrong-offset"]) == {"single_offset": True}
    shifted = json.loads(json.dumps(CANDIDATES["correct"]))
    shifted["rows"][0][1] = "2025-12-31T22:30:00Z"  # one hour off the other rows
    assert single_offset(shifted) == {"single_offset": False}


def test_uniqueness_premises_fail_closed_on_malformed_rows():
    def identity_selection(result):
        ctx = CheckContext(
            {"result.json": json.dumps(result).encode()},
            UNIQUENESS.inputs,
            UNIQUENESS.private,
        )
        return CSV.uniqueness_premises(ctx)["identity_selection"]

    rows = CANDIDATES["correct"]["rows"]
    assert identity_selection({"rows": rows}) is True
    assert identity_selection({"rows": [5]}) is False
    assert identity_selection({"rows": [[]]}) is False
    assert identity_selection({"rows": [rows[0], [5, "x", 1]]}) is False
    # M4's semantics: an empty selection is an identity selection; the transformation
    # check rejects it (unlike timestamp_premises, which needs a row to show an offset).
    assert identity_selection({"rows": []}) is True


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


def test_renaming_premise_fails_closed_and_needs_a_migrate_case():
    def premises(payload, private=MIGRATION_TASK.private):
        ctx = CheckContext(
            {"result.json": json.dumps(payload).encode()},
            MIGRATION_TASK.inputs,
            private,
            LocalJobs(),
        )
        return MIGRATION.renaming_premises(ctx)

    invalid = {"renaming_correspondence": False}
    assert premises({"migrate.py": ""}) == invalid
    assert premises({"migrate.py": MIGRATIONS["complete"], "extra": "x"}) == invalid
    no_cases = {**MIGRATION_TASK.private, "references.json": b"{}"}
    with pytest.raises(ValueError, match="no migrate case"):
        premises({"migrate.py": MIGRATIONS["complete"]}, no_cases)


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
def test_real_proofs_on_the_task_layer(
    tmp_path, task, check, proof, candidate, decision
):
    verifier = LeanVerifier(os.environ["WARRANTED_PROOF_BUNDLE"])
    proj, _ = csv_project(tmp_path, [(CANDIDATES[candidate], proof)], verifier)
    result = proj.start(one(task), CONFIG, Model())
    assert result.submissions[0].verdicts[check] is VerdictStatus.PASSED
    assert [s.decision for s in result.submissions] == [decision]


@pytest.mark.proof
@needs_lean
def test_a_sorry_proof_is_rejected_by_the_real_verifier(tmp_path):
    verifier = LeanVerifier(os.environ["WARRANTED_PROOF_BUNDLE"])
    sorry = (CSV_DIR / "UniquenessChallenge.lean").read_bytes()  # ends in `by sorry`
    assert b"sorry" in sorry
    proj, _ = csv_project(tmp_path, [(CANDIDATES["correct"], sorry)], verifier)
    result = proj.start(one(UNIQUENESS), CONFIG, Model())
    assert result.submissions[0].verdicts["uniqueness"] is VerdictStatus.REJECTED
