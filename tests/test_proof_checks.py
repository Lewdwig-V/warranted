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
