"""Proofs in the task layer: UNPROVED, the verifier boundary, and LeanProof."""

import base64
import hashlib
import json
import runpy
from pathlib import Path

import pytest
from test_experimental_tasks import CANDIDATES, CONFIG, ENVIRONMENT, Model, project

from warranted import (
    CheckContext,
    LeanProof,
    Project,
    ProofTarget,
    RunOutcome,
    TaskSpec,
    Verdict,
    VerdictStatus,
    domain_identity,
)
from warranted._ledger import Outcome, Result
from warranted._proofs import ProofStatus, Verification
from warranted._worker import AttemptResult
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


PROOF = b"theorem uniqueness_preserved : UniquenessTarget := by simp"


def workspace(files):
    return json.dumps(
        {n: base64.b64encode(d).decode() for n, d in files.items()}
    ).encode()


def checked(verifier, files, premises=None, correspondence=None):
    ctx = CheckContext({"workspace.json": workspace(files)}, {}, {}, None, verifier)
    proof = LeanProof(
        TARGETS["uniqueness"], premises=premises, correspondence=correspondence
    )
    return proof.check(ctx), ctx


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


@pytest.mark.parametrize(
    "status, lean",
    [
        (ProofStatus.PROVED, True),
        (ProofStatus.REJECTED, True),
        (ProofStatus.UNPROVED, True),
        (ProofStatus.UNSUPPORTED, False),
        (ProofStatus.INFRASTRUCTURE_FAILURE, False),
    ],
)
def test_only_a_lean_diagnostic_reaches_the_worker(status, lean):
    verdict, _ = checked(FakeVerifier({PROOF: status}), {"Solution.lean": PROOF})
    diagnostic = f"fake {status.value}"
    if lean:
        assert verdict.feedback["diagnostic"] == diagnostic
        assert verdict.host_only is None
    else:
        assert "diagnostic" not in verdict.feedback
        assert verdict.host_only == {"diagnostic": diagnostic}


def test_a_proved_theorem_whose_correspondence_fails_is_rejected():
    verdict, _ = checked(
        FakeVerifier({PROOF: ProofStatus.PROVED}),
        {"Solution.lean": PROOF},
        lambda ctx: {"input_unique": True},
        lambda ctx: {"identity_selection": False},
    )
    assert verdict.status is VerdictStatus.REJECTED
    assert verdict.feedback["proof"] == "proved"
    assert verdict.feedback["premises"] == {"input_unique": True}
    assert verdict.feedback["correspondence"] == {"identity_selection": False}


def test_a_failed_premise_wins_over_a_failed_correspondence():
    verdict, _ = checked(
        FakeVerifier({PROOF: ProofStatus.PROVED}),
        {"Solution.lean": PROOF},
        lambda ctx: {"input_unique": False},
        lambda ctx: {"identity_selection": False},
    )
    assert verdict.status is VerdictStatus.UNSUPPORTED


@pytest.mark.parametrize(
    "correspondence",
    [lambda ctx: {"x": 1}, lambda ctx: {1: True}, lambda ctx: 1 / 0],
)
def test_a_faulty_correspondence_is_never_a_pass(correspondence):
    with pytest.raises((TypeError, ZeroDivisionError)):
        checked(
            FakeVerifier({PROOF: ProofStatus.PROVED}),
            {"Solution.lean": PROOF},
            correspondence=correspondence,
        )


def test_a_rejected_source_reports_premises_and_correspondence():
    verdict, _ = checked(
        FakeVerifier(),
        {},
        lambda ctx: {"input_unique": True},
        lambda ctx: {"identity_selection": False},
    )
    assert verdict.status is VerdictStatus.REJECTED
    assert verdict.feedback["premises"] == {"input_unique": True}
    assert verdict.feedback["correspondence"] == {"identity_selection": False}


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
        checked(
            FakeVerifier({PROOF: ProofStatus.PROVED}),
            {"Solution.lean": PROOF},
            premises,
        )


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


def test_a_lean_proof_pins_the_challenge_bytes_it_verifies(tmp_path):
    # The identity is derived from the bytes the target holds, never by
    # rereading the path, so an edit after construction cannot split the two.
    path = tmp_path / "Challenge.lean"
    path.write_bytes(TARGETS["uniqueness"].challenge)
    target = ProofTarget(path, "Warranted.uniqueness_preserved")
    proof = LeanProof(target)
    assert hashlib.sha256(target.challenge).hexdigest() in proof.version
    path.write_bytes(b"theorem changed : True := trivial\n")
    assert LeanProof(target).version == proof.version
    rebuilt = LeanProof(ProofTarget(path, "Warranted.uniqueness_preserved"))
    assert rebuilt.version != proof.version
    assert not hasattr(proof, "sources")


def test_a_lean_proof_is_isolated_only_when_declared():
    assert LeanProof(TARGETS["uniqueness"]).isolated is False
    assert LeanProof(TARGETS["uniqueness"], isolated=True).isolated is True
    assert LeanProof(TARGETS["uniqueness"]).needs_proofs is True
    with pytest.raises(ValueError, match="isolated"):
        LeanProof(TARGETS["uniqueness"], isolated="yes")


def test_the_verifier_identity_includes_its_timeout(tmp_path):
    from warranted import LeanVerifier
    from warranted._proofs import LIMITS

    bundle = tmp_path / "bundle.json"
    bundle.write_bytes(b'{"bundle": "test"}')
    default, short = LeanVerifier(bundle), LeanVerifier(bundle, seconds=5)
    assert default.identity != short.identity
    assert str(LIMITS["seconds"]) in default.identity


def proof_domain():
    from test_experimental_tasks import CSV

    class Domain(CSV.CsvDomain):
        checkers = {
            **CSV.CsvDomain.checkers,
            "uniqueness": LeanProof(TARGETS["uniqueness"]),
        }

    return Domain()


def test_a_task_needing_a_proof_is_refused_without_a_verifier(tmp_path):
    from test_experimental_tasks import TASK

    proj, script = project(tmp_path, ["correct"], proof_domain())
    task = TaskSpec(
        TASK.id,
        TASK.objective,
        TASK.inputs,
        TASK.private,
        ("transformation", "uniqueness"),
        1,
    )
    with pytest.raises(ValueError, match="need a proof verifier"):
        proj.start(task, CONFIG, Model())
    assert proj.runs() == () and script.calls == 0


def test_a_proof_run_records_host_evidence_that_export_withholds(tmp_path):
    from test_experimental_tasks import TASK

    class Prover:
        def __init__(self):
            self.episodes = []

        def __call__(self, ledger_root, episode):
            self.episodes.append(episode)

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
                            "candidate/result.json": json.dumps(
                                CANDIDATES["correct"]
                            ).encode(),
                            "candidate/workspace.json": workspace(
                                {"Solution.lean": PROOF}
                            ),
                        },
                    )

            return Environment()

    proj = Project.create(
        tmp_path / "project",
        proof_domain(),
        {"model": 40, "tool": 40, "check": 40},
        environment=Prover(),
        environment_id=ENVIRONMENT,
        proofs=FakeVerifier({PROOF: ProofStatus.PROVED}),
    )
    task = TaskSpec(
        TASK.id, TASK.objective, TASK.inputs, TASK.private, ("uniqueness",), 1
    )
    result = proj.start(task, CONFIG, Model())
    assert result.submissions[0].verdicts == {"uniqueness": VerdictStatus.PASSED}
    with Ledger.open(proj.ledger_root) as ledger:
        channels = {
            c
            for op in ledger.operations()
            if op.request.origin.kind == "check"
            for c in op.completion.observation.artifacts
        }
    assert "proofs.json" in channels
    assert any(c.startswith("proofs/1/") for c in channels)
    dest = tmp_path / "export"
    proj.export(result.run_id, dest)
    index = json.loads((dest / "index.json").read_text())
    exported = {c for o in index["observations"] for c in o["artifacts"]}
    assert not any(c == "proofs.json" or c.startswith("proofs/") for c in exported)
