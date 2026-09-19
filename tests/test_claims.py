"""Historical validation and support under current assumptions stay separate."""

import json
from dataclasses import replace

import pytest

from warranted.acceptance import Evidence
from warranted.claims import Claims
from warranted.ledger import (
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
)


def setup(root):
    ledger = Ledger.create(
        root,
        Manifest("claims", "1", "run", "world", {}, {"work": 4}),
        {
            name: Snapshot(name.encode(), "test", "1")
            for name in ("target", "offset-v1", "offset-v2", "definition", "annotation")
        },
    )
    session = ledger.start_session()
    refs = {
        name: Evidence(name, value.artifact)
        for name, value in ledger.project.snapshots.items()
    }
    request = Request(
        Origin(
            "check",
            "validate",
            "checker",
            "1",
            {
                refs["target"].name: refs["target"].artifact,
                refs["offset-v1"].name: refs["offset-v1"].artifact,
            },
        ),
        ledger.project,
    )
    ledger.reserve(session, request, {"work": 1})
    return ledger, session, refs, request


def test_restart_propagates_staleness_without_rewriting_validation(tmp_path):
    root = tmp_path / "ledger"
    ledger, session, refs, request = setup(root)
    with ledger:
        assert ledger.begin(session, request)
        ledger.complete(
            session,
            request,
            Result(Outcome.SUCCEEDED, 0, {"work": 1}, 1),
            {"result.json": b'{"timestamp": true}'},
        )
        claims = Claims(ledger, session)
        parent = claims.record(
            "Timestamp matches this offset.",
            refs["target"],
            {"offset": refs["offset-v1"]},
            validation=(request, "timestamp"),
            complete=True,
        )
        child = claims.record(
            "This application depends on that timestamp check.",
            refs["target"],
            {},
            parents=(parent,),
            validation=(request, "timestamp"),
            complete=True,
        )
        unaffected = claims.record(
            "The identifier definition is unchanged.",
            refs["target"],
            {"definition": refs["definition"]},
            complete=True,
        )
        assert (
            claims.assess(child, {"offset": refs["offset-v1"]}).applicability
            == "current"
        )
        before = ledger.read_artifact(parent.artifact)
    with Ledger.open(root) as ledger:
        claims = Claims(ledger, ledger.start_session())
        state = {"offset": refs["offset-v2"], "definition": refs["definition"]}
        report = claims.assess(child, state)
        assert report.validation == "passed"
        assert report.applicability == "stale"
        assert claims.assess(unaffected, state).applicability == "current"
        assert claims.assess(unaffected, state).validation == "unproved"
        assert ledger.read_artifact(parent.artifact) == before
        assert ledger.accounting()["work"].spent == 1


def test_unknown_dependencies_and_raw_success_do_not_supply_support(tmp_path):
    ledger, session, refs, request = setup(tmp_path / "ledger")
    with ledger:
        claims = Claims(ledger, session)
        claim = claims.record(
            "A proposed check.",
            refs["target"],
            {"offset": refs["offset-v1"]},
            validation=(request, "timestamp"),
            complete=True,
        )
        ledger.record(session, request.origin, {"result.json": b'{"timestamp": true}'})
        assert claims.assess(claim, {}).validation == "unknown"
        assert claims.assess(claim, {}).applicability == "unknown"
        incomplete = claims.record(
            "Dependencies are not fully known.", refs["target"], {}, complete=False
        )
        child = claims.record(
            "An incomplete parent blocks precise reuse.",
            refs["target"],
            {},
            parents=(incomplete,),
            complete=True,
        )
        assert claims.assess(child, {}).applicability == "unknown"
        assert claims.assess(child, {}).validation == "unproved"
        wrong = replace(request, origin=replace(request.origin, inputs={}))
        with pytest.raises(ValueError, match="target"):
            claims.record(
                "Wrong target.", refs["target"], {}, validation=(wrong, "timestamp")
            )
        forged = ledger.record(
            session,
            Origin("fake", "claim", "worker", "1", {}),
            {"claim.json": ledger.read_artifact(claim.artifact)},
        )
        with pytest.raises(ValueError, match="claim"):
            claims.assess(Evidence.captured(forged, "claim.json"), {})
        assert ledger.begin(session, request)
    with Ledger.open(tmp_path / "ledger") as ledger:
        claims = Claims(ledger, ledger.start_session())
        assert claims.assess(claim, {}).validation == "unknown"
        assert claims.assess(child, {}).validation == "unproved"
        assert claims.assess(child, {}).applicability == "unknown"
        assert ledger.lookup(request).state == "unknown"
        assert ledger.accounting()["work"].reserved == 1


def test_a_different_request_under_the_planned_id_cannot_validate_a_claim(tmp_path):
    ledger, session, refs, request = setup(tmp_path / "ledger")
    with ledger:
        planned = replace(
            request, origin=replace(request.origin, operation_id="planned")
        )
        claims = Claims(ledger, session)
        claim = claims.record(
            "An exact checker validates this target.",
            refs["target"],
            {"offset": refs["offset-v1"]},
            validation=(planned, "timestamp"),
            complete=True,
        )
        assert (
            claims.assess(claim, {"offset": refs["offset-v1"]}).validation == "missing"
        )
        changed = replace(planned, origin=replace(planned.origin, producer_version="2"))
        ledger.reserve(session, changed, {"work": 1})
        assert ledger.begin(session, changed)
        ledger.complete(
            session,
            changed,
            Result(Outcome.SUCCEEDED, 0, {"work": 1}, 1),
            {"result.json": b'{"timestamp": true}'},
        )
        assert (
            claims.assess(claim, {"offset": refs["offset-v1"]}).validation
            == "unsupported"
        )


@pytest.mark.parametrize(
    "outcome, raw, expected",
    [
        (Outcome.SUCCEEDED, {"timestamp": False}, "rejected"),
        (Outcome.SUCCEEDED, {"timestamp": 1}, "unsupported"),
        (Outcome.SUCCEEDED, {"other": True}, "unsupported"),
        (Outcome.FAILED, {"timestamp": True}, "unsupported"),
        (Outcome.INFRASTRUCTURE_FAILURE, {"timestamp": True}, "infrastructure_failure"),
    ],
)
def test_validation_outcomes_are_not_relabelled_as_success(
    tmp_path, outcome, raw, expected
):
    ledger, session, refs, request = setup(tmp_path / "ledger")
    with ledger:
        assert ledger.begin(session, request)
        ledger.complete(
            session,
            request,
            Result(
                outcome,
                0
                if outcome is Outcome.SUCCEEDED
                else 1
                if outcome is Outcome.FAILED
                else None,
                {"work": 1},
                1,
            ),
            {"result.json": json.dumps(raw).encode()},
        )
        claims = Claims(ledger, session)
        claim = claims.record(
            "A scoped validation.",
            refs["target"],
            {},
            validation=(request, "timestamp"),
            complete=True,
        )
        assessment = claims.assess(claim, {})
        assert assessment.validation == expected
        assert assessment.applicability == "current"
