"""An export grants selected evidence, never access to authoritative storage."""

import json
import multiprocessing
from dataclasses import replace

import pytest

import warranted.exports as exports
from warranted.exports import export_evidence
from warranted.ledger import (
    CorruptArtifact,
    Ledger,
    Manifest,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
)


@pytest.fixture
def captured(tmp_path):
    manifest = Manifest(
        "private-fixture",
        "1",
        "private-run",
        "private-world",
        {"secret-token": "private-environment"},
        {"work": 5},
    )
    with Ledger.create(
        tmp_path / "ledger",
        manifest,
        {
            "input": Snapshot(b"public input", "/private/source/path", "1"),
            "private-snapshot": Snapshot(
                b"private snapshot bytes", "private origin", "1"
            ),
        },
    ) as ledger:
        session = ledger.start_session()
        origin = Origin(
            "run",
            "script",
            "fixture",
            "1",
            {
                name: snapshot.artifact
                for name, snapshot in ledger.project.snapshots.items()
            },
        )
        request = Request(origin, ledger.project)
        ledger.reserve(session, request, {"work": 3})
        ledger.begin(session, request)
        completion = ledger.complete(
            session,
            request,
            Result(Outcome.SUCCEEDED, 0, {"work": 2}, 10),
            {
                "../../public": b"public output",
                "private-channel": b"private diagnostic",
            },
        )
        ledger.record(
            session,
            replace(origin, operation_id="private-operation"),
            {"raw": b"private observation"},
        )
        yield ledger, request, completion


def test_selection_excludes_private_bytes_fields_and_indirect_references(
    captured, tmp_path
):
    ledger, request, completion = captured
    destination = tmp_path / "export"
    sequence = completion.observation.sequence
    index = export_evidence(
        ledger,
        destination,
        snapshots=("input",),
        observations={sequence: ("../../public",)},
        operations=("run",),
    )
    data = json.loads(index.read_text())
    assert data["authoritative"] is False
    assert data["partial"] is True
    assert data["observations"][0]["origin"]["inputs"] == {
        "input": data["snapshots"]["input"]["artifact"],
    }
    assert data["operations"][0]["state"] == "completed"
    assert data["operations"][0]["observation_sequence"] == sequence
    assert set(data["observations"][0]["artifacts"]) == {"../../public"}
    files = [path for path in destination.rglob("*") if path.is_file()]
    combined = b"\n".join(path.read_bytes() for path in files)
    assert b"private" not in combined
    assert b"secret-token" not in combined
    assert str(ledger.root).encode() not in combined
    assert not any(path.is_symlink() for path in destination.rglob("*"))
    assert not (tmp_path / "public").exists()
    assert not (destination / "ledger.sqlite3").exists()
    assert ledger.lookup(request).completion == completion


def test_exports_are_copies_and_default_selection_exposes_no_evidence(
    captured, tmp_path
):
    ledger, request, completion = captured
    destination = tmp_path / "empty"
    data = json.loads(export_evidence(ledger, destination).read_text())
    assert data["snapshots"] == {}
    assert data["observations"] == []
    assert data["operations"] == []
    assert list((destination / "artifacts").iterdir()) == []
    destination = tmp_path / "selected"
    before = (ledger.root / "ledger.sqlite3").read_bytes()
    history = ledger.history()
    sessions = ledger.sessions()
    index = export_evidence(
        ledger,
        destination,
        observations={completion.observation.sequence: ("../../public",)},
    )
    ref = completion.observation.artifacts["../../public"]
    artifact = destination / "artifacts" / ref.digest
    assert not artifact.samefile(ledger.root / "artifacts" / "sha256" / ref.digest)
    artifact.write_bytes(b"forged output")
    index.write_text('{"accepted": true}')
    assert ledger.read_artifact(ref) == b"public output"
    assert ledger.lookup(request).completion == completion
    assert ledger.history() == history
    assert ledger.sessions() == sessions
    assert (ledger.root / "ledger.sqlite3").read_bytes() == before


def test_derived_input_links_require_the_exact_source_channel_selection(
    captured, tmp_path
):
    ledger, _, completion = captured
    source = completion.observation
    inputs = {
        f"observation/{source.sequence}/{channel}": ref
        for channel, ref in source.artifacts.items()
    }
    derived = ledger.record(
        ledger.start_session(),
        Origin("derived", "check", "fixture", "1", inputs),
        {"result": b"public derived result"},
    )
    for selected in (False, True):
        selection = {derived.sequence: ("result",)}
        if selected:
            selection[source.sequence] = ("../../public",)
        index = export_evidence(
            ledger, tmp_path / str(selected), observations=selection
        )
        data = json.loads(index.read_text())
        view = next(
            item
            for item in data["observations"]
            if item["sequence"] == derived.sequence
        )
        assert set(view["origin"]["inputs"]) == (
            {f"observation/{source.sequence}/../../public"} if selected else set()
        )
        assert "private-channel" not in index.read_text()


@pytest.mark.parametrize(
    "selection",
    [
        {"snapshots": ("absent",)},
        {"snapshots": "input"},
        {"observations": {999: ("raw",)}},
        {"observations": {True: ("raw",)}},
        {"observations": {1: ("absent",)}},
        {"observations": {1: "../../public"}},
        {"operations": ("absent",)},
    ],
)
def test_invalid_selection_creates_no_export(captured, tmp_path, selection):
    ledger, _, _ = captured
    destination = tmp_path / "export"
    with pytest.raises((ValueError, TypeError)):
        export_evidence(ledger, destination, **selection)
    assert not destination.exists()


def test_destination_cannot_overlap_storage_or_replace_existing_files(
    captured, tmp_path
):
    ledger, _, _ = captured
    alias = tmp_path / "alias"
    alias.symlink_to(ledger.root, target_is_directory=True)
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep"
    marker.write_text("unchanged")
    for destination in [
        ledger.root,
        ledger.root / "nested",
        alias / "nested",
        existing,
        tmp_path,
    ]:
        with pytest.raises((ValueError, FileExistsError)):
            export_evidence(ledger, destination)
    assert marker.read_text() == "unchanged"
    assert not (ledger.root / "nested").exists()


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_selected_damaged_artifact_does_not_publish_an_index(
    captured, tmp_path, damage
):
    ledger, _, completion = captured
    ref = completion.observation.artifacts["../../public"]
    stored = ledger.root / "artifacts" / "sha256" / ref.digest
    if damage == "missing":
        stored.unlink()
    else:
        stored.write_bytes(b"wrong")
    destination = tmp_path / "export"
    with pytest.raises((FileNotFoundError, CorruptArtifact)):
        export_evidence(ledger, destination, observations={1: ("../../public",)})
    assert not (destination / "index.json").exists()
    # Excluded artifacts do not need to be read or disclosed.
    export_evidence(ledger, tmp_path / "input-only", snapshots=("input",))


def test_unknown_operation_stays_unknown_in_export(captured, tmp_path):
    ledger, request, _ = captured
    unknown = replace(request, origin=replace(request.origin, operation_id="unknown"))
    ledger.reserve(ledger.start_session(), unknown, {"work": 3})
    ledger.begin(ledger.start_session(), unknown)
    index = export_evidence(ledger, tmp_path / "export", operations=("unknown",))
    operation = json.loads(index.read_text())["operations"][0]
    assert operation["state"] == "unknown"
    assert operation["reservation"] == {"work": 3}
    assert "result" not in operation
    assert ledger.lookup(unknown).state == "unknown"


def test_copy_error_leaves_no_published_export(captured, tmp_path, monkeypatch):
    ledger, _, _ = captured

    def fail(*args):
        raise OSError("copy failed")

    monkeypatch.setattr(ledger, "read_artifact", fail)
    with pytest.raises(OSError, match="copy failed"):
        export_evidence(ledger, tmp_path / "export", snapshots=("input",))
    assert not (tmp_path / "export").exists()


def _interrupted_export(root, destination, point, pipe):
    publish = exports.os.replace

    def pause(*args):
        if point == "after":
            publish(*args)
        pipe.send("ready")
        pipe.recv()
        if point == "before":
            publish(*args)

    exports.os.replace = pause
    with Ledger.open(root) as ledger:
        export_evidence(
            ledger,
            destination,
            snapshots=("input",),
            observations={1: ("../../public",)},
        )


@pytest.mark.parametrize("point", ["before", "after"])
def test_killed_export_has_complete_index_or_no_index(captured, tmp_path, point):
    ledger, request, completion = captured
    root = ledger.root
    history, sessions = ledger.history(), ledger.sessions()
    ledger.close()
    destination = tmp_path / "export"
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_interrupted_export, args=(root, destination, point, child)
    )
    process.start()
    try:
        assert parent.poll(15), f"child exited before publication: {process.exitcode}"
        assert parent.recv() == "ready"
        process.kill()
        process.join(15)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
        parent.close()
        child.close()
    index = destination / "index.json"
    assert index.exists() == (point == "after")
    if index.exists():
        data = json.loads(index.read_text())
        ref = data["observations"][0]["artifacts"]["../../public"]
        assert (
            destination / "artifacts" / ref["digest"]
        ).read_bytes() == b"public output"
    with Ledger.open(root) as reopened:
        assert reopened.history() == history
        assert reopened.sessions() == sessions
        assert reopened.lookup(request).completion == completion
