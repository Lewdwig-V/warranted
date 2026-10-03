"""Proofs in the task layer: UNPROVED, the verifier boundary, and LeanProof."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest
from test_experimental_tasks import CONFIG, Model, project

from warranted import (
    CheckContext,
    Project,
    RunOutcome,
    TaskSpec,
    Verdict,
    VerdictStatus,
    domain_identity,
)
from warranted._proofs import ProofStatus, Verification
from warranted.host import Ledger

ROOT = Path(__file__).resolve().parents[1]
TARGETS = runpy.run_path(str(ROOT / "examples/proof_targets.py"))["TARGETS"]


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
    Project.create(
        root, CSV.CsvDomain(), {"model": 1}, proofs=FakeVerifier(), **options
    )
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
