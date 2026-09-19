"""Observable persistence and failure contracts for the trusted local ledger."""

import json
import multiprocessing
import sqlite3
from dataclasses import FrozenInstanceError, replace

import pytest

import warranted.ledger as storage
from warranted.ledger import (
    ArtifactRef,
    BudgetExceeded,
    CorruptArtifact,
    InvalidProject,
    Ledger,
    Manifest,
    OperationConflict,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
    UnsupportedVersion,
)


def manifest():
    return Manifest(
        fixture_id="csv-normalization",
        fixture_version="1",
        run_id="run-1",
        world_id="world-1",
        environment={"python": "3.12", "transform": "1"},
        allowances={"synthetic-work": 5},
    )


def snapshots():
    return {
        "input.csv": Snapshot(b"id,value\nr1,7\n", "fixture/input.csv", "1"),
        "contract": Snapshot(b"Preserve every record.", "fixture/contract", "1"),
        "transform": Snapshot(b"print('scripted fixture')\n", "fixture/script", "1"),
    }


def origin(ledger, operation_id="transform-1"):
    return Origin(
        operation_id=operation_id,
        kind="script",
        producer="fixture-runner",
        producer_version="1",
        inputs={name: item.artifact for name, item in ledger.project.snapshots.items()},
    )


@pytest.fixture
def ledger(tmp_path):
    with Ledger.create(tmp_path / "project", manifest(), snapshots()) as ledger:
        yield ledger


def test_open_never_creates_or_repairs_a_project(tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(FileNotFoundError):
        Ledger.open(root)
    assert not root.exists()
    root.mkdir()
    with pytest.raises(FileNotFoundError):
        Ledger.open(root)
    assert list(root.iterdir()) == []
    with sqlite3.connect(root / "ledger.sqlite3") as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    before = (root / "ledger.sqlite3").read_bytes()
    with pytest.raises(InvalidProject):
        Ledger.open(root)
    assert (root / "ledger.sqlite3").read_bytes() == before
    with pytest.raises(FileExistsError):
        Ledger.create(root, manifest(), snapshots())


def test_invalid_capture_leaves_history_and_artifacts_unchanged(ledger):
    session = ledger.start_session()
    baseline = ledger.record(session, origin(ledger), {"stdout": b"original"})
    before = set((ledger.root / "artifacts" / "sha256").iterdir())
    missing = ArtifactRef("0" * 64, 1)
    cases = [
        {"session_id": "absent"},
        {"supersedes": 999},
        {"supersedes": True},
        {"origin": replace(origin(ledger), inputs={"input.csv": missing})},
        {"raw": {"stdout": "not bytes"}},
    ]
    for change in cases:
        request = dict(
            session_id=session, origin=origin(ledger), raw={"stdout": b"new"}
        )
        request.update(change)
        with pytest.raises((ValueError, TypeError)):
            ledger.record(**request)
        assert ledger.history() == (baseline,)
        assert set((ledger.root / "artifacts" / "sha256").iterdir()) == before


def test_operation_inputs_can_cite_exact_prior_observation_channels(ledger):
    session = ledger.start_session()
    first = ledger.record(session, origin(ledger), {"output.csv": b"derived input"})
    ref = first.artifacts["output.csv"]
    name = f"observation/{first.sequence}/output.csv"
    req = Request(
        replace(origin(ledger, "derived"), inputs={name: ref}), ledger.project
    )
    for bad_name, bad_ref in (
        ("observation/999/output.csv", ref),
        (f"observation/{first.sequence}/absent", ref),
        (name, replace(ref, size_bytes=ref.size_bytes + 1)),
        ("observation/01/output.csv", ref),
        ("output.csv", ref),
    ):
        bad = replace(req, origin=replace(req.origin, inputs={bad_name: bad_ref}))
        with pytest.raises(ValueError):
            ledger.reserve(session, bad, {"synthetic-work": 1})
    assert ledger.operations() == ()
    ledger.reserve(session, req, {"synthetic-work": 1})
    assert ledger.begin(session, req)
    completion = ledger.complete(session, req, result(units=1), {"stdout": b"done"})
    second = ledger.record(
        session, origin(ledger, "same-bytes"), {"output.csv": b"derived input"}
    )
    changed_origin = replace(
        req.origin, inputs={f"observation/{second.sequence}/output.csv": ref}
    )
    with pytest.raises(OperationConflict):
        ledger.lookup(replace(req, origin=changed_origin))
    with Ledger.open(ledger.root) as reopened:
        assert reopened.lookup(req).completion == completion
        assert reopened.read_artifact(req.origin.inputs[name]) == b"derived input"
    # Cached downstream success cannot hide a damaged input artifact.
    (ledger.root / "artifacts" / "sha256" / ref.digest).unlink()
    with pytest.raises(FileNotFoundError):
        ledger.lookup(req)


@pytest.mark.parametrize("damage", ["missing", "changed", "wrong-size"])
def test_artifact_damage_is_explicit_and_never_repaired(ledger, damage):
    session = ledger.start_session()
    record = ledger.record(session, origin(ledger), {"stdout": b"original"})
    ref = record.artifacts["stdout"]
    path = ledger.root / "artifacts" / "sha256" / ref.digest
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(b"corrupted")
    else:
        ref = replace(ref, size_bytes=ref.size_bytes + 1)
    with pytest.raises((FileNotFoundError, CorruptArtifact)):
        ledger.read_artifact(ref)
    assert ledger.history() == (record,)
    if damage in ("changed", "missing"):
        with pytest.raises((FileNotFoundError, CorruptArtifact)):
            ledger.record(session, origin(ledger), {"stdout": b"original"})
        if damage == "changed":
            assert path.read_bytes() == b"corrupted"
        else:
            assert not path.exists()
        assert ledger.history() == (record,)
    assert ledger.read_artifact(ledger.project.snapshots["contract"].artifact)


@pytest.mark.parametrize("digest", ["../outside", "A" * 64, "a" * 63, None])
def test_invalid_digest_cannot_select_a_path(digest):
    with pytest.raises((ValueError, TypeError)):
        ArtifactRef(digest, 0)


def test_records_remain_immutable_and_repeated_bytes_keep_their_origins(ledger):
    session = ledger.start_session()
    first = ledger.record(session, origin(ledger), {"stdout": b"same"})
    second = ledger.record(
        session,
        origin(ledger, "transform-2"),
        {"stdout": b"same"},
        supersedes=first.sequence,
    )
    assert first.artifacts == second.artifacts
    assert first.origin != second.origin
    assert second.supersedes == first.sequence
    assert ledger.history(after=first.sequence) == (second,)
    with pytest.raises(FrozenInstanceError):
        first.sequence = 99
    with pytest.raises(TypeError):
        first.artifacts["stdout"] = ArtifactRef("0" * 64, 0)
    with pytest.raises(TypeError):
        ledger.project.manifest.allowances["synthetic-work"] = 99
    assert ledger.history() == (first, second)
    project = ledger.project
    ledger.close()
    with Ledger.open(ledger.root) as reopened:
        assert reopened.project == project
        assert reopened.history() == (first, second)
        assert reopened.read_artifact(first.artifacts["stdout"]) == b"same"


def test_write_failures_preserve_old_evidence(ledger, monkeypatch):
    session = ledger.start_session()
    baseline = ledger.record(session, origin(ledger), {"stdout": b"original"})

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "link", fail)
        with pytest.raises(OSError, match="disk unavailable"):
            ledger.record(session, origin(ledger), {"stdout": b"new"})
    assert ledger.history() == (baseline,)

    # Fail after the INSERT, before the transaction commits.
    insert = ledger._insert_observation

    def fail_transaction(*args, **kwargs):
        insert(*args, **kwargs)
        raise sqlite3.OperationalError("database write failed")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_insert_observation", fail_transaction)
        with pytest.raises(sqlite3.OperationalError, match="database write failed"):
            ledger.record(session, origin(ledger), {"stdout": b"new"})
    assert ledger.history() == (baseline,)
    assert ledger.read_artifact(baseline.artifacts["stdout"]) == b"original"


@pytest.mark.parametrize("changed_schema", [False, True])
@pytest.mark.parametrize("version", [1, 999])
def test_unsupported_or_invalid_metadata_is_rejected(ledger, changed_schema, version):
    root = ledger.root
    ledger.close()
    with sqlite3.connect(root / "ledger.sqlite3") as connection:
        connection.execute("UPDATE project SET format_version = ?", (version,))
        if changed_schema:
            connection.execute("ALTER TABLE sessions ADD COLUMN future TEXT")
    with pytest.raises(UnsupportedVersion):
        Ledger.open(root)
    with sqlite3.connect(root / "ledger.sqlite3") as connection:
        connection.execute(
            "UPDATE project SET format_version = ?, data = '{}'",
            (storage.FORMAT_VERSION,),
        )
    with pytest.raises(InvalidProject):
        Ledger.open(root)


def test_invalid_manifest_does_not_create_files(tmp_path):
    with pytest.raises((TypeError, ValueError)):
        replace(manifest(), allowances={"synthetic-work": True})
    with pytest.raises((TypeError, ValueError)):
        replace(manifest(), environment={"python": 3.12})
    root = tmp_path / "invalid"
    with pytest.raises((TypeError, ValueError)):
        Ledger.create(root, manifest(), {"input": "not a snapshot"})
    assert not root.exists()


def test_missing_world_lineage_is_not_assumed_to_be_a_root(ledger):
    root = ledger.root
    ledger.close()
    with sqlite3.connect(root / "ledger.sqlite3") as connection:
        encoded = connection.execute("SELECT data FROM project").fetchone()[0]
        data = json.loads(encoded)
        del data["value"]["manifest"]["parent_world_id"]
        connection.execute("UPDATE project SET data = ?", (json.dumps(data),))
    with pytest.raises(InvalidProject):
        Ledger.open(root)


def test_raw_names_and_success_text_do_not_select_paths_or_become_receipts(ledger):
    session = ledger.start_session()
    raw = {"../../outside": b'{"accepted": true}', "stderr": b""}
    record = ledger.record(session, origin(ledger), raw)
    assert not (ledger.root.parent / "outside").exists()
    assert {
        name: ledger.read_artifact(ref) for name, ref in record.artifacts.items()
    } == raw
    repeated = ledger.record(session, origin(ledger), raw)
    assert repeated.sequence != record.sequence
    assert ledger.history() == (record, repeated)
    assert ledger.lookup(Request(origin(ledger), ledger.project)) is None


def _read_in_new_process(root, pipe):
    with Ledger.open(root) as ledger:
        history = ledger.history()
        old_sessions = ledger.sessions()
        new_session = ledger.start_session()
        pipe.send(
            {
                "project_id": ledger.project.project_id,
                "run_id": ledger.project.manifest.run_id,
                "old_sessions": [s.session_id for s in old_sessions],
                "new_session": new_session,
                "observations": [
                    (
                        o.sequence,
                        o.session_id,
                        o.origin.operation_id,
                        ledger.read_artifact(o.artifacts["stdout"]),
                    )
                    for o in history
                ],
            }
        )


def _receive(process, pipe):
    assert pipe.poll(15), f"child did not reach its boundary (exit={process.exitcode})"
    return pipe.recv()


def test_fresh_process_recovers_bytes_origins_and_sessions(ledger):
    session = ledger.start_session()
    record = ledger.record(session, origin(ledger), {"stdout": b"\x00\xffraw\r\n"})
    project_id = ledger.project.project_id
    ledger.close()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_read_in_new_process, args=(ledger.root, child))
    process.start()
    try:
        result = _receive(process, parent)
        process.join(15)
        assert process.exitcode == 0
        assert result["project_id"] == project_id
        assert result["run_id"] == "run-1"
        assert result["old_sessions"] == [session]
        assert result["new_session"] != session
        assert result["observations"] == [
            (record.sequence, session, "transform-1", b"\x00\xffraw\r\n")
        ]
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()


def _interrupted_write(root, point, pipe):
    def pause():
        pipe.send("ready")
        pipe.recv()

    if point == "initialization":
        connect = storage.sqlite3.connect

        def instrument(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connection.set_trace_callback(
                lambda sql: pause() if sql == "COMMIT" else None
            )
            return connection

        storage.sqlite3.connect = instrument
        Ledger.create(root, manifest(), snapshots())
        return

    with Ledger.open(root) as ledger:
        session = ledger.sessions()[0].session_id
        if point == "partial-file":
            temporary = storage.NamedTemporaryFile

            def instrument(*args, **kwargs):
                file = temporary(*args, **kwargs)
                write = file.write

                def partial(data):
                    write(data[:1])
                    file.flush()
                    pause()

                file.write = partial
                return file

            storage.NamedTemporaryFile = instrument
        elif point == "published-file":
            link = storage.os.link

            def publish(*args, **kwargs):
                link(*args, **kwargs)
                pause()

            storage.os.link = publish
        elif point == "transaction":
            insert = ledger._insert_observation

            def insert_then_pause(*args, **kwargs):
                result = insert(*args, **kwargs)
                pause()
                return result

            ledger._insert_observation = insert_then_pause
        elif point == "commit":
            ledger._connection.set_trace_callback(
                lambda sql: pause() if sql == "COMMIT" else None
            )
        ledger.record(
            session,
            origin(ledger, "interrupted"),
            {"stdout": b"new", "stderr": b"diagnostic"},
        )
        if point == "committed":
            pause()


@pytest.mark.parametrize(
    "point",
    [
        "partial-file",
        "published-file",
        "transaction",
        "commit",
        "committed",
        "initialization",
    ],
)
def test_process_termination_preserves_atomic_history(tmp_path, point):
    root = tmp_path / "project"
    if point != "initialization":
        with Ledger.create(root, manifest(), snapshots()) as ledger:
            session = ledger.start_session()
            baseline = ledger.record(session, origin(ledger), {"stdout": b"original"})
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_interrupted_write, args=(root, point, child))
    process.start()
    try:
        assert _receive(process, parent) == "ready"
        process.kill()
        process.join(15)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()
    if point == "initialization":
        with pytest.raises(InvalidProject):
            Ledger.open(root)
        return
    with Ledger.open(root) as ledger:
        records = ledger.history()
        assert records[0] == baseline
        assert len(records) == (2 if point == "committed" else 1)
        for record in records:
            for ref in record.artifacts.values():
                ledger.read_artifact(ref)
        assert ledger.read_artifact(baseline.artifacts["stdout"]) == b"original"


def request(ledger, operation_id="transform-1"):
    return Request(origin(ledger, operation_id), ledger.project)


def result(outcome=Outcome.SUCCEEDED, exit_code=0, units=2):
    return Result(outcome, exit_code, {"synthetic-work": units}, elapsed_ns=100)


def balance(ledger):
    usage = ledger.accounting()["synthetic-work"]
    return usage.spent, usage.reserved, usage.available


def test_pending_and_unknown_requests_keep_one_reservation_across_sessions(ledger):
    session = ledger.start_session()
    req = request(ledger)
    pending = ledger.reserve(session, req, {"synthetic-work": 3})
    assert pending.state == "pending"
    assert balance(ledger) == (0, 3, 2)
    ledger.close()
    with Ledger.open(ledger.root) as reopened:
        session = reopened.start_session()
        assert reopened.reserve(session, req, {"synthetic-work": 3}) == pending
        assert reopened.begin(session, req)
        assert reopened.lookup(req).state == "unknown"
        assert not reopened.begin(session, req)
        assert balance(reopened) == (0, 3, 2)
        with pytest.raises(BudgetExceeded):
            reopened.reserve(
                session, request(reopened, "another"), {"synthetic-work": 3}
            )
    with Ledger.open(ledger.root) as reopened:
        assert reopened.lookup(req).state == "unknown"
        assert not reopened.begin(reopened.start_session(), req)
        assert balance(reopened) == (0, 3, 2)


@pytest.mark.parametrize(
    ("outcome", "exit_code"),
    [
        (Outcome.SUCCEEDED, 0),
        (Outcome.FAILED, 7),
        (Outcome.INFRASTRUCTURE_FAILURE, None),
    ],
)
def test_completed_operation_reuses_bytes_and_charges_once(ledger, outcome, exit_code):
    session = ledger.start_session()
    req = request(ledger)
    raw = {"stdout": b"output", "stderr": b"diagnostic"}
    receipt = result(outcome, exit_code)
    executions = ledger.root.parent / "executions"

    def run(host, session):
        operation = host.reserve(session, req, {"synthetic-work": 3})
        if host.begin(session, req):
            with executions.open("ab") as stream:
                stream.write(b"executed\n")
            return host.complete(session, req, receipt, raw)
        return operation.completion

    completed = run(ledger, session)
    assert completed.result == receipt
    assert completed.observation.origin == req.origin
    assert balance(ledger) == (2, 0, 3)
    ledger.close()
    with Ledger.open(ledger.root) as reopened:
        session = reopened.start_session()
        assert run(reopened, session) == completed
        assert reopened.complete(session, req, receipt, raw) == completed
        assert len(reopened.history()) == 1
        assert executions.read_bytes() == b"executed\n"
        assert balance(reopened) == (2, 0, 3)
        assert (
            reopened.read_artifact(completed.observation.artifacts["stderr"])
            == b"diagnostic"
        )
        for changed in [
            {"result": result(units=1)},
            {"result": result(Outcome.FAILED, 1)},
            {"result": replace(receipt, elapsed_ns=101)},
            {"raw": {**raw, "stdout": b"different"}},
        ]:
            args = dict(session_id=session, request=req, result=receipt, raw=raw)
            args.update(changed)
            with pytest.raises(ValueError):
                reopened.complete(**args)
        assert reopened.lookup(req).completion == completed
        assert balance(reopened) == (2, 0, 3)


@pytest.mark.parametrize("completed", [False, True])
def test_changed_request_identity_is_rejected_before_reuse(ledger, completed):
    session = ledger.start_session()
    req = request(ledger)
    ledger.reserve(session, req, {"synthetic-work": 3})
    if completed:
        ledger.begin(session, req)
        ledger.complete(session, req, result(), {"stdout": b"same output"})
    project = req.context
    changed_contract = dict(project.snapshots)
    changed_contract["contract"] = replace(changed_contract["contract"], version="2")
    changed_input = dict(req.origin.inputs)
    changed_input["input.csv"] = ArtifactRef("0" * 64, 1)
    changes = [
        replace(req, origin=replace(req.origin, inputs=changed_input)),
        replace(req, origin=replace(req.origin, producer_version="2")),
        replace(req, origin=replace(req.origin, kind="another-kind")),
        replace(req, context=replace(project, snapshots=changed_contract)),
        replace(
            req,
            context=replace(
                project,
                manifest=replace(project.manifest, environment={"python": "changed"}),
            ),
        ),
    ]
    for changed in changes:
        for action in [
            lambda changed=changed: ledger.reserve(
                session, changed, {"synthetic-work": 3}
            ),
            lambda changed=changed: ledger.lookup(changed),
            lambda changed=changed: ledger.begin(session, changed),
            lambda changed=changed: ledger.complete(
                session, changed, result(), {"stdout": b"same output"}
            ),
        ]:
            with pytest.raises(ValueError):
                action()
    with pytest.raises(ValueError):
        ledger.reserve(session, req, {"synthetic-work": 2})
    assert len(ledger.operations()) == 1
    assert balance(ledger) == ((2, 0, 3) if completed else (0, 3, 2))


@pytest.mark.parametrize("units", [4, 7])
def test_overrun_records_full_usage_and_blocks_new_or_pending_dispatch(ledger, units):
    session = ledger.start_session()
    req = request(ledger)
    other = request(ledger, "pending")
    ledger.reserve(session, req, {"synthetic-work": 3})
    ledger.reserve(session, other, {"synthetic-work": 2})
    ledger.begin(session, req)
    completion = ledger.complete(session, req, result(units=units), {"stdout": b"done"})
    assert "synthetic-work" in completion.breaches
    assert balance(ledger) == (units, 2, 3 - units)
    ledger.close()
    with Ledger.open(ledger.root) as reopened:
        session = reopened.start_session()
        assert reopened.lookup(req).completion == completion
        with pytest.raises(BudgetExceeded):
            reopened.reserve(session, request(reopened, "new"), {"synthetic-work": 0})
        with pytest.raises(BudgetExceeded):
            reopened.begin(session, other)
        assert balance(reopened) == (units, 2, 3 - units)


def test_invalid_transitions_and_unrecorded_usage_cannot_settle_a_reservation(ledger):
    session = ledger.start_session()
    req = request(ledger)
    with pytest.raises(ValueError):
        ledger.begin(session, req)
    with pytest.raises(ValueError):
        ledger.reserve("absent", req, {"synthetic-work": 3})
    with pytest.raises(ValueError):
        ledger.reserve(session, req, {"unknown-unit": 1})
    ledger.reserve(session, req, {"synthetic-work": 3})
    with pytest.raises(ValueError):
        ledger.complete(session, req, result(), {"stdout": b"not executed"})
    ledger.begin(session, req)
    with pytest.raises(ValueError):
        ledger.complete(
            session, req, replace(result(), usage={}), {"stdout": b"missing usage"}
        )
    assert ledger.lookup(req).state == "unknown"
    assert ledger.history() == ()
    assert balance(ledger) == (0, 3, 2)


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_cached_completion_requires_intact_artifacts(ledger, damage):
    session = ledger.start_session()
    req = request(ledger)
    raw = {"stdout": b"done"}
    ledger.reserve(session, req, {"synthetic-work": 3})
    ledger.begin(session, req)
    completed = ledger.complete(session, req, result(), raw)
    path = (
        ledger.root
        / "artifacts"
        / "sha256"
        / completed.observation.artifacts["stdout"].digest
    )
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    for action in [
        lambda: ledger.lookup(req),
        lambda: ledger.reserve(session, req, {"synthetic-work": 3}),
        lambda: ledger.begin(session, req),
        lambda: ledger.complete(session, req, result(), raw),
    ]:
        with pytest.raises((FileNotFoundError, CorruptArtifact)):
            action()
    assert ledger.operations()[0].completion == completed
    assert balance(ledger) == (2, 0, 3)
    assert len(ledger.history()) == 1


def test_unreserved_usage_is_recorded_and_other_unknown_work_can_still_settle(ledger):
    session = ledger.start_session()
    first, second = request(ledger), request(ledger, "second")
    for req in (first, second):
        ledger.reserve(session, req, {"synthetic-work": 2})
        assert ledger.begin(session, req)
    receipt = replace(result(), usage={"synthetic-work": 2, "unexpected-cost": 9})
    completion = ledger.complete(session, first, receipt, {"stdout": b"done"})
    assert completion.breaches == ("unexpected-cost",)
    assert ledger.accounting()["unexpected-cost"].spent == 9
    assert ledger.accounting()["unexpected-cost"].available == -9
    ledger.complete(session, second, result(), {"stdout": b"done"})
    assert balance(ledger) == (4, 0, 1)
    with pytest.raises(BudgetExceeded):
        ledger.reserve(session, request(ledger, "third"), {"synthetic-work": 1})


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "accepted"},
        {"exit_code": True},
        {"exit_code": 1},
        {"usage": {"synthetic-work": -1}},
        {"usage": {"synthetic-work": True}},
        {"elapsed_ns": -1},
    ],
)
def test_invalid_result_cannot_become_a_receipt(change):
    with pytest.raises((TypeError, ValueError)):
        replace(result(), **change)


def test_multiple_allowances_are_reserved_together(tmp_path):
    limits = replace(manifest(), allowances={"synthetic-work": 5, "calls": 1})
    with Ledger.create(tmp_path / "project", limits, snapshots()) as ledger:
        session = ledger.start_session()
        with pytest.raises(BudgetExceeded):
            ledger.reserve(session, request(ledger), {"synthetic-work": 3, "calls": 2})
        assert ledger.operations() == ()
        assert balance(ledger) == (0, 0, 5)
        ledger.reserve(session, request(ledger), {"synthetic-work": 3, "calls": 1})
        assert ledger.accounting()["calls"].available == 0


def test_failed_settlement_rolls_back_evidence_and_keeps_the_reservation(ledger):
    session = ledger.start_session()
    req = request(ledger)
    ledger.reserve(session, req, {"synthetic-work": 3})
    ledger.begin(session, req)
    # Force failure after evidence insertion, at the operation update.
    with ledger._connection:
        ledger._connection.execute("""CREATE TEMP TRIGGER fail_settlement
            BEFORE UPDATE OF result ON operations BEGIN
            SELECT RAISE(ABORT, 'settlement write failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="settlement write failed"):
        ledger.complete(session, req, result(), {"stdout": b"done"})
    assert ledger.history() == ()
    assert ledger.lookup(req).state == "unknown"
    assert balance(ledger) == (0, 3, 2)


@pytest.mark.parametrize("damage", ["outcome", "usage", "breach"])
def test_invalid_stored_receipt_cannot_clear_accounting(ledger, damage):
    session = ledger.start_session()
    req = request(ledger)
    ledger.reserve(session, req, {"synthetic-work": 3})
    ledger.begin(session, req)
    ledger.complete(session, req, result(units=4), {"stdout": b"done"})
    ledger.close()
    with sqlite3.connect(ledger.root / "ledger.sqlite3") as connection:
        if damage == "breach":
            connection.execute(
                "UPDATE operations SET breaches = ?", (storage._dump([]),)
            )
        else:
            data = json.loads(
                connection.execute("SELECT result FROM operations").fetchone()[0]
            )
            if damage == "outcome":
                data["value"]["outcome"] = "unrecognized"
            else:
                data["value"]["usage"] = {}
            connection.execute("UPDATE operations SET result = ?", (json.dumps(data),))
    with Ledger.open(ledger.root) as reopened:
        with pytest.raises(InvalidProject):
            reopened.lookup(req)
        with pytest.raises(InvalidProject):
            reopened.accounting()
        with pytest.raises(InvalidProject):
            reopened.reserve(session, request(reopened, "new"), {"synthetic-work": 1})


def _interrupted_operation(root, point, pipe):
    def pause():
        pipe.send("ready")
        pipe.recv()

    with Ledger.open(root) as ledger:
        session = ledger.sessions()[0].session_id
        req = request(ledger)

        def pause_on_commit():
            ledger._connection.set_trace_callback(
                lambda sql: pause() if sql == "COMMIT" else None
            )

        if point == "reserve-commit":
            pause_on_commit()
        ledger.reserve(session, req, {"synthetic-work": 3})
        if point == "reserved":
            pause()
        if point == "begin-commit":
            pause_on_commit()
        assert ledger.begin(session, req)
        if point == "begun":
            pause()
        with (root.parent / "executions").open("ab") as stream:
            stream.write(b"executed\n")
        if point == "executed":
            pause()
        if point == "published":
            publish = storage._publish

            def publish_then_pause(*args):
                ref = publish(*args)
                pause()
                return ref

            storage._publish = publish_then_pause
        if point == "inserted":
            insert = ledger._insert_observation

            def insert_then_pause(*args):
                observation = insert(*args)
                pause()
                return observation

            ledger._insert_observation = insert_then_pause
        if point == "complete-commit":
            pause_on_commit()
        ledger.complete(session, req, result(), {"stdout": b"completed", "stderr": b""})
        if point == "completed":
            pause()


def _recover_operation(root, pipe):
    with Ledger.open(root) as ledger:
        req = request(ledger)
        session = ledger.start_session()
        operation = ledger.lookup(req)
        state = None if operation is None else operation.state
        if operation is not None:
            assert ledger.reserve(session, req, {"synthetic-work": 3}) == operation
            if state != "pending":
                assert not ledger.begin(session, req)
        history = ledger.history()
        pipe.send(
            {
                "state": state,
                "balance": balance(ledger),
                "raw": [
                    {
                        name: ledger.read_artifact(ref)
                        for name, ref in o.artifacts.items()
                    }
                    for o in history
                ],
            }
        )


@pytest.mark.parametrize(
    "point",
    [
        "reserve-commit",
        "reserved",
        "begin-commit",
        "begun",
        "executed",
        "published",
        "inserted",
        "complete-commit",
        "completed",
    ],
)
def test_killed_host_recovers_reservation_or_atomic_completion(tmp_path, point):
    root = tmp_path / "project"
    with Ledger.create(root, manifest(), snapshots()) as ledger:
        ledger.start_session()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_interrupted_operation, args=(root, point, child))
    process.start()
    try:
        assert _receive(process, parent) == "ready"
        process.kill()
        process.join(15)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()

    parent, child = context.Pipe()
    process = context.Process(target=_recover_operation, args=(root, child))
    process.start()
    try:
        recovered = _receive(process, parent)
        process.join(15)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()
    expected_state = {
        "reserve-commit": None,
        "reserved": "pending",
        "begin-commit": "pending",
        "completed": "completed",
    }.get(point, "unknown")
    assert recovered["state"] == expected_state
    assert recovered["balance"] == (
        (0, 0, 5)
        if expected_state is None
        else (2, 0, 3)
        if expected_state == "completed"
        else (0, 3, 2)
    )
    assert recovered["raw"] == (
        [{"stdout": b"completed", "stderr": b""}] if point == "completed" else []
    )
    executions = tmp_path / "executions"
    assert (executions.read_bytes() if executions.exists() else b"") == (
        b""
        if point in ("reserve-commit", "reserved", "begin-commit", "begun")
        else b"executed\n"
    )
