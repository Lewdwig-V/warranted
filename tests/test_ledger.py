"""Observable persistence and failure contracts for the trusted local ledger."""

import json
import multiprocessing
import sqlite3
from dataclasses import FrozenInstanceError, replace

import pytest

import warranted.ledger as storage
from warranted.ledger import (
    ArtifactRef,
    CorruptArtifact,
    InvalidProject,
    Ledger,
    Manifest,
    Origin,
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
def test_unsupported_or_invalid_metadata_is_rejected(ledger, changed_schema):
    root = ledger.root
    ledger.close()
    with sqlite3.connect(root / "ledger.sqlite3") as connection:
        connection.execute("UPDATE project SET format_version = 999")
        if changed_schema:
            connection.execute("ALTER TABLE sessions ADD COLUMN future TEXT")
    with pytest.raises(UnsupportedVersion):
        Ledger.open(root)
    with sqlite3.connect(root / "ledger.sqlite3") as connection:
        connection.execute("UPDATE project SET format_version = 1, data = '{}' ")
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
