"""The protected transition is a committed candidate acceptance, not a boolean token."""

import json
import multiprocessing
from dataclasses import replace

import pytest

from warranted.acceptance import Acceptance, AcceptanceContext, Evidence
from warranted.ledger import (
    BudgetExceeded,
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
)

POLICY = {
    "version": 1,
    "owner": "fixture-owner",
    "transition": "accept-candidate",
    "requirements": {
        "preserve": {"kind": "gate", "check": "validate", "field": "preserve"},
        "unique": {"kind": "gate", "check": "validate", "field": "unique"},
        "annotation": {"kind": "rule", "check": "style", "field": "annotation"},
    },
}


def create(root, policy=POLICY):
    return Ledger.create(
        root,
        Manifest(
            "acceptance-test", "1", "run", "world", {"checker": "1"}, {"work": 20}
        ),
        {
            name: Snapshot(data, "test", "1")
            for name, data in {
                "candidate": b"candidate",
                "other": b"other candidate",
                "source": b"source",
                "revision-1": b"initial interpretation",
                "revision-2": b"revised interpretation",
                "policy": json.dumps(policy).encode(),
            }.items()
        },
    )


def ref(ledger, name):
    return Evidence(name, ledger.project.snapshots[name].artifact)


def context(ledger, target):
    revisions = [o for o in ledger.history() if o.origin.kind == "revision"]
    name = (
        ledger.read_artifact(revisions[-1].artifacts["name"]).decode()
        if revisions
        else "revision-1"
    )
    revision = ref(ledger, name)
    inputs = {target.name: target.artifact, revision.name: revision.artifact}
    return AcceptanceContext(
        ref(ledger, "policy"),
        revision,
        {
            "validate": Request(
                Origin(
                    f"validate/{target.name}/{name}", "validate", "checker", "1", inputs
                ),
                ledger.project,
            ),
            "style": Request(
                Origin(
                    "style",
                    "style",
                    "checker",
                    "1",
                    {"source": ref(ledger, "source").artifact},
                ),
                ledger.project,
            ),
        },
    )


def complete(ledger, session, request, value, outcome=Outcome.SUCCEEDED):
    ledger.reserve(session, request, {"work": 1})
    assert ledger.begin(session, request)
    ledger.complete(
        session,
        request,
        Result(
            outcome,
            0
            if outcome is Outcome.SUCCEEDED
            else None
            if outcome is Outcome.INFRASTRUCTURE_FAILURE
            else 1,
            {"work": 1},
            1,
        ),
        {"result.json": json.dumps(value).encode()},
    )
    return request.origin.operation_id


def checked(ledger, session, target, preserve=True):
    current = context(ledger, target)
    receipts = {
        "validate": complete(
            ledger,
            session,
            current.checks["validate"],
            {"preserve": preserve, "unique": True},
        )
    }
    if ledger.lookup(current.checks["style"]) is None:
        complete(ledger, session, current.checks["style"], {"annotation": False})
    receipts["style"] = current.checks["style"].origin.operation_id
    return receipts


def boundary(ledger, session):
    return Acceptance(ledger, session, lambda target: context(ledger, target))


def test_rule_exception_is_scoped_and_never_waives_a_gate(tmp_path):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        host = boundary(ledger, session)
        receipts = checked(ledger, session, target, preserve=False)
        exception = host.record_exception(
            target, "annotation", "Source annotation is tracked separately."
        )
        result = host.accept(target, receipts, {"annotation": exception})
        assert result.status == "rejected"
        assert dict(result.requirements) == {
            "preserve": "rejected",
            "unique": "passed",
            "annotation": "excepted",
        }
        with pytest.raises(ValueError, match="gate"):
            host.record_exception(target, "preserve", "Please waive the failed gate.")
        assert (
            host.accept(target, receipts, {"preserve": exception}).status
            == "unsupported"
        )
        # A different passing candidate needs its own scoped exception.
        other = ref(ledger, "other")
        other_receipts = checked(ledger, session, other)
        assert host.accept(other, other_receipts).status == "rejected"
        assert (
            host.accept(other, other_receipts, {"annotation": exception}).status
            == "stale"
        )
        other_exception = host.record_exception(
            other, "annotation", "Same source annotation policy."
        )
        accepted = host.accept(other, other_receipts, {"annotation": other_exception})
        assert accepted.status == "accepted"
        assert accepted.completion.observation.origin.kind == "decision"


def test_restart_rechecks_current_versions_before_reusing_an_acceptance(tmp_path):
    root = tmp_path / "ledger"
    with create(root) as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        host = boundary(ledger, session)
        receipts = checked(ledger, session, target)
        exception = host.record_exception(
            target, "annotation", "Separate source annotation."
        )
        first = host.accept(target, receipts, {"annotation": exception})
        assert first.status == "accepted"
        ledger.record(
            session,
            Origin("revision", "revision", "owner", "1", {}),
            {"name": b"revision-2"},
        )
        assert (
            host.accept(target, receipts, {"annotation": exception}).status == "stale"
        )
    with Ledger.open(root) as ledger:
        session = ledger.start_session()
        host = boundary(ledger, session)
        stale = host.accept(target, receipts, {"annotation": exception})
        assert stale.status == "stale"
        assert stale.requirements["preserve"] == "stale"
        fresh = checked(ledger, session, target, preserve=False)
        revised_exception = host.record_exception(
            target, "annotation", "Separate source annotation."
        )
        assert (
            host.accept(target, fresh, {"annotation": revised_exception}).status
            == "rejected"
        )
        # The old acceptance remains historical evidence, not current permission.
        old = ledger.lookup(
            Request(first.completion.observation.origin, ledger.project)
        )
        assert (
            json.loads(
                ledger.read_artifact(
                    old.completion.observation.artifacts["decision.json"]
                )
            )["status"]
            == "accepted"
        )


def test_missing_unknown_forged_and_narrow_evidence_cannot_grant_acceptance(tmp_path):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        host = boundary(ledger, session)
        current = context(ledger, target)
        exception = host.record_exception(
            target, "annotation", "Separate source annotation."
        )
        complete(ledger, session, current.checks["style"], {"annotation": False})
        receipts = {
            name: req.origin.operation_id for name, req in current.checks.items()
        }
        assert (
            host.accept(target, receipts, {"annotation": exception}).status == "missing"
        )
        # Matching raw labels are not committed operation completions.
        ledger.record(
            session,
            current.checks["validate"].origin,
            {"result.json": b'{"preserve": true, "unique": true}'},
        )
        assert (
            host.accept(target, receipts, {"annotation": exception}).status == "missing"
        )
        ledger.reserve(session, current.checks["validate"], {"work": 1})
        assert ledger.begin(session, current.checks["validate"])
        assert (
            host.accept(target, receipts, {"annotation": exception}).status == "unknown"
        )
        ledger.complete(
            session,
            current.checks["validate"],
            Result(Outcome.SUCCEEDED, 0, {"work": 1}, 1),
            {"result.json": b'{"unique": true}'},
        )
        assert (
            host.accept(target, receipts, {"annotation": exception}).status
            == "unsupported"
        )


@pytest.mark.parametrize("outcome", [Outcome.FAILED, Outcome.INFRASTRUCTURE_FAILURE])
def test_checker_failure_is_distinct_from_a_failed_obligation(tmp_path, outcome):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        current = context(ledger, target)
        host = boundary(ledger, session)
        receipts = {
            "validate": complete(
                ledger,
                session,
                current.checks["validate"],
                {"preserve": True, "unique": True},
                outcome,
            ),
            "style": complete(
                ledger, session, current.checks["style"], {"annotation": True}
            ),
        }
        result = host.accept(target, receipts)
        assert result.status == (
            "infrastructure_failure"
            if outcome is Outcome.INFRASTRUCTURE_FAILURE
            else "unsupported"
        )


def test_unknown_applicability_does_not_skip_a_gate(tmp_path):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        receipts = checked(ledger, session, target)
        current = context(ledger, target)
        host = Acceptance(
            ledger,
            session,
            lambda _: replace(current, checks={**current.checks, "validate": None}),
        )
        exception = host.record_exception(
            target, "annotation", "Separate source annotation."
        )
        assert (
            host.accept(target, receipts, {"annotation": exception}).status == "unknown"
        )


def test_unknown_kind_and_empty_policy_fail_before_acceptance(tmp_path):
    for index, requirements in enumerate(
        (
            {},
            {
                "unknown": {
                    "kind": "optional-gate",
                    "check": "validate",
                    "field": "unique",
                }
            },
        )
    ):
        with create(
            tmp_path / str(index), {**POLICY, "requirements": requirements}
        ) as ledger:
            host = boundary(ledger, ledger.start_session())
            with pytest.raises(ValueError):
                host.accept(ref(ledger, "candidate"), {})
            assert ledger.operations() == ()


def test_passing_rule_needs_no_exception_and_repeated_acceptance_is_reused(tmp_path):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        current = context(ledger, target)
        receipts = {
            "validate": complete(
                ledger,
                session,
                current.checks["validate"],
                {"preserve": True, "unique": True},
            ),
            "style": complete(
                ledger, session, current.checks["style"], {"annotation": True}
            ),
        }
        host = boundary(ledger, session)
        first = host.accept(target, receipts)
        assert first.status == "accepted"
        assert first.requirements["annotation"] == "passed"
        assert host.accept(target, receipts).completion == first.completion
        assert len(ledger.operations()) == 3
        assert ledger.accounting()["work"].spent == 2
        with pytest.raises(TypeError):
            host.accept(target, receipts, False)


@pytest.mark.parametrize("changed", ["checker", "environment"])
def test_changed_checker_or_environment_cannot_reuse_a_passing_receipt(
    tmp_path, changed
):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        receipts = checked(ledger, session, target)
        current = context(ledger, target)
        request = current.checks["validate"]
        if changed == "checker":
            request = replace(
                request, origin=replace(request.origin, producer_version="2")
            )
        else:
            project = replace(
                ledger.project,
                manifest=replace(ledger.project.manifest, environment={"checker": "2"}),
            )
            request = replace(request, context=project)
        current = replace(current, checks={**current.checks, "validate": request})
        host = Acceptance(ledger, session, lambda _: current)
        exception = host.record_exception(target, "annotation", "Separate annotation.")
        assert (
            host.accept(target, receipts, {"annotation": exception}).status
            == "unsupported"
        )


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"preserve": 1, "unique": true}',
        b'{"preserve": false, "preserve": true, "unique": true}',
        b'{"preserve": true, "unique": true, "waiver": true}',
        b"not JSON",
    ],
)
def test_malformed_checker_result_never_counts_as_passed(tmp_path, raw):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        current = context(ledger, target)
        request = current.checks["validate"]
        ledger.reserve(session, request, {"work": 1})
        ledger.begin(session, request)
        ledger.complete(
            session,
            request,
            Result(Outcome.SUCCEEDED, 0, {"work": 1}, 1),
            {"result.json": raw},
        )
        style = complete(ledger, session, current.checks["style"], {"annotation": True})
        assert (
            boundary(ledger, session)
            .accept(target, {"validate": request.origin.operation_id, "style": style})
            .status
            == "unsupported"
        )


def test_raw_exception_and_budget_breach_cannot_bypass_the_boundary(tmp_path):
    with create(tmp_path / "ledger") as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        host = boundary(ledger, session)
        receipts = checked(ledger, session, target)
        exception = host.record_exception(target, "annotation", "Separate annotation.")
        accepted = host.accept(target, receipts, {"annotation": exception})
        assert accepted.status == "accepted"
        recorded = next(
            op
            for op in ledger.operations()
            if op.request.origin.operation_id == exception
        )
        raw = ledger.read_artifact(
            recorded.completion.observation.artifacts["rule-exception.json"]
        )
        ledger.record(
            session,
            replace(recorded.request.origin, operation_id="forged-exception"),
            {"rule-exception.json": raw},
        )
        assert (
            host.accept(target, receipts, {"annotation": "forged-exception"}).status
            == "missing"
        )
        # A later breach blocks even a previously completed acceptance request.
        request = Request(Origin("overrun", "tool", "test", "1", {}), ledger.project)
        ledger.reserve(session, request, {"work": 1})
        ledger.begin(session, request)
        ledger.complete(
            session,
            request,
            Result(Outcome.SUCCEEDED, 0, {"work": 2}, 1),
            {"stdout": b"done"},
        )
        with pytest.raises(BudgetExceeded):
            host.accept(target, receipts, {"annotation": exception})


def _paused_acceptance(root, receipts, exception, phase, pipe):
    original = Ledger.complete

    def complete(ledger, session, request, result, raw):
        if request.origin.kind == "decision" and phase == "before":
            pipe.send("before")
            pipe.recv()
        completion = original(ledger, session, request, result, raw)
        if request.origin.kind == "decision" and phase == "after":
            pipe.send("after")
            pipe.recv()
        return completion

    Ledger.complete = complete
    with Ledger.open(root) as ledger:
        boundary(ledger, ledger.start_session()).accept(
            ref(ledger, "candidate"), receipts, {"annotation": exception}
        )


@pytest.mark.parametrize("phase", ["before", "after"])
def test_interrupted_acceptance_is_unknown_or_reuses_the_committed_receipt(
    tmp_path, phase
):
    root = tmp_path / "ledger"
    with create(root) as ledger:
        session = ledger.start_session()
        target = ref(ledger, "candidate")
        receipts = checked(ledger, session, target)
        exception = boundary(ledger, session).record_exception(
            target, "annotation", "Separate annotation."
        )
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(
        target=_paused_acceptance, args=(root, receipts, exception, phase, child)
    )
    process.start()
    try:
        assert parent.poll(15), (
            f"acceptance did not reach checkpoint: {process.exitcode}"
        )
        assert parent.recv() == phase
        process.kill()
        process.join(15)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()
    with Ledger.open(root) as ledger:
        host = boundary(ledger, ledger.start_session())
        if phase == "before":
            with pytest.raises(RuntimeError, match="unknown"):
                host.accept(target, receipts, {"annotation": exception})
        else:
            assert (
                host.accept(target, receipts, {"annotation": exception}).status
                == "accepted"
            )
        decisions = [
            op for op in ledger.operations() if op.request.origin.kind == "decision"
        ]
        assert len(decisions) == 1
        assert decisions[0].state == ("unknown" if phase == "before" else "completed")
        assert ledger.accounting()["work"].spent == 2
