"""Run scopes: per-scope caps under the project total, and scoped blocking."""

import sqlite3

import pytest

from warranted.ledger import (
    ROOT_SCOPE,
    BudgetExceeded,
    InvalidProject,
    Ledger,
    Manifest,
    OperationConflict,
    Origin,
    Outcome,
    Request,
    Result,
    UnknownOutcome,
)


def manifest(work=10):
    return Manifest("scopes", "1", "run", "world", {"python": "3.12"}, {"work": work})


@pytest.fixture
def ledger(tmp_path):
    with Ledger.create(tmp_path / "project", manifest(), {}) as ledger:
        yield ledger


def request(ledger, operation_id):
    return Request(Origin(operation_id, "script", "test", "1", {}), ledger.project)


def reserve(ledger, session, name, amount=1, scope=ROOT_SCOPE):
    return ledger.reserve(session, request(ledger, name), {"work": amount}, scope)


def complete(ledger, session, name, used=1):
    ledger.complete(
        session,
        request(ledger, name),
        Result(Outcome.SUCCEEDED, 0, {"work": used}, 1),
        {"stdout": b"done"},
    )


def test_a_reservation_above_its_scope_cap_is_refused(ledger):
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 2})
    with pytest.raises(BudgetExceeded, match="scope"):
        reserve(ledger, session, "big", 3, "run-a")
    reserve(ledger, session, "fits", 2, "run-a")
    with pytest.raises(BudgetExceeded, match="scope"):
        reserve(ledger, session, "more", 1, "run-a")
    assert ledger.accounting("run-a")["work"].available == 0
    assert ledger.accounting()["work"].available == 8


def test_scope_caps_summing_above_the_project_total_cannot_overspend(ledger):
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 8})
    ledger.open_scope(session, "run-b", {"work": 8})
    reserve(ledger, session, "a", 6, "run-a")
    with pytest.raises(BudgetExceeded, match="project"):
        reserve(ledger, session, "b", 6, "run-b")
    reserve(ledger, session, "b", 4, "run-b")
    assert ledger.accounting()["work"].available == 0


def test_unknown_operation_blocks_only_its_own_scope_across_restart(ledger):
    root = ledger.root
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 5})
    ledger.open_scope(session, "run-b", {"work": 5})
    reserve(ledger, session, "lost", 2, "run-a")
    assert ledger.begin(session, request(ledger, "lost"))
    ledger.close()
    with Ledger.open(root) as reopened:
        session = reopened.start_session()
        reserve(reopened, session, "a-next", 1, "run-a")
        with pytest.raises(UnknownOutcome):
            reopened.begin(session, request(reopened, "a-next"))
        assert [
            o.request.origin.operation_id for o in reopened.unresolved("run-a")
        ] == ["lost"]
        assert reopened.unresolved("run-b") == ()
        reserve(reopened, session, "b-next", 1, "run-b")
        assert reopened.begin(session, request(reopened, "b-next"))
        assert reopened.accounting("run-a")["work"].reserved == 3
        assert reopened.accounting()["work"].reserved == 4


def test_unknown_operation_in_the_root_scope_blocks_every_scope(ledger):
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 5})
    reserve(ledger, session, "root-lost")
    assert ledger.begin(session, request(ledger, "root-lost"))
    for name, scope in (("a", "run-a"), ("root", ROOT_SCOPE)):
        reserve(ledger, session, name, 1, scope)
        with pytest.raises(UnknownOutcome):
            ledger.begin(session, request(ledger, name))


def test_root_scope_work_continues_past_an_unknown_run_operation(ledger):
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 5})
    reserve(ledger, session, "a-lost", 1, "run-a")
    assert ledger.begin(session, request(ledger, "a-lost"))
    reserve(ledger, session, "root")
    assert ledger.begin(session, request(ledger, "root"))


def test_breach_blocks_its_own_scope_but_not_another(ledger):
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 5})
    ledger.open_scope(session, "run-b", {"work": 5})
    reserve(ledger, session, "over", 1, "run-a")
    reserve(ledger, session, "pending-a", 1, "run-a")
    assert ledger.begin(session, request(ledger, "over"))
    complete(ledger, session, "over", used=2)
    with pytest.raises(BudgetExceeded):
        ledger.begin(session, request(ledger, "pending-a"))
    with pytest.raises(BudgetExceeded):
        reserve(ledger, session, "new-a", 1, "run-a")
    with pytest.raises(BudgetExceeded):
        ledger.check_budget("run-a")
    ledger.check_budget("run-b")
    reserve(ledger, session, "b", 1, "run-b")
    assert ledger.begin(session, request(ledger, "b"))


def test_negative_project_total_blocks_every_scope(tmp_path):
    with Ledger.create(tmp_path / "p", manifest(work=2), {}) as ledger:
        session = ledger.start_session()
        ledger.open_scope(session, "run-a", {"work": 2})
        reserve(ledger, session, "x", 2, "run-a")
        assert ledger.begin(session, request(ledger, "x"))
        complete(ledger, session, "x", used=3)
        with pytest.raises(BudgetExceeded):
            ledger.check_budget(ROOT_SCOPE)
        with pytest.raises(BudgetExceeded):
            reserve(ledger, session, "root", 0)


def test_scopes_are_immutable_and_bind_their_operations(ledger):
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 2})
    ledger.open_scope(session, "run-a", {"work": 2})
    with pytest.raises(OperationConflict):
        ledger.open_scope(session, "run-a", {"work": 3})
    ledger.open_scope(session, "run-b", {})
    reserve(ledger, session, "x", 1, "run-a")
    with pytest.raises(OperationConflict):
        reserve(ledger, session, "x", 1, "run-b")
    assert ledger.operations()[0].scope == "run-a"


@pytest.mark.parametrize(
    "scope_id, caps",
    [(ROOT_SCOPE, {}), ("run-a", {"tokens": 1}), ("run-a", {"work": -1}), (" ", {})],
)
def test_invalid_scopes_are_rejected(ledger, scope_id, caps):
    with pytest.raises(ValueError):
        ledger.open_scope(ledger.start_session(), scope_id, caps)


def test_reserving_in_an_unopened_scope_is_rejected(ledger):
    with pytest.raises(ValueError, match="unknown scope"):
        reserve(ledger, ledger.start_session(), "x", 1, "run-missing")


def test_scope_caps_survive_restart(ledger):
    root = ledger.root
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {"work": 2})
    ledger.close()
    with Ledger.open(root) as reopened:
        assert reopened.scopes() == {"run-a": {"work": 2}}
        with pytest.raises(BudgetExceeded, match="scope"):
            reserve(reopened, reopened.start_session(), "big", 3, "run-a")


def test_a_stored_operation_naming_an_unrecorded_scope_is_invalid(ledger):
    root = ledger.root
    session = ledger.start_session()
    ledger.open_scope(session, "run-a", {})
    reserve(ledger, session, "x", 1, "run-a")
    ledger.close()
    with sqlite3.connect(root / "ledger.sqlite3") as connection:
        connection.execute("UPDATE operations SET scope = 'run-forged'")
    with Ledger.open(root) as reopened, pytest.raises(InvalidProject):
        reopened.operations()
