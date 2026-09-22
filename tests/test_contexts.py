"""Worker context is an explicit disclosure, never a copy of the host ledger."""

import json
from dataclasses import replace

import pytest

from warranted.acceptance import Evidence
from warranted.contexts import Condition, capture_context
from warranted.ledger import Ledger, Manifest, OperationConflict, Origin, Snapshot
from warranted.worker import Episode, Journal, input_files


def project(root, condition):
    return Ledger.create(
        root,
        Manifest("visibility", "1", "run", "world", {"condition": str(condition)}, {}),
        {"private": Snapshot(b"PRIVATE GRADING ANSWER", "private", "1")},
    )


@pytest.mark.parametrize("condition", list(Condition))
def test_only_selected_layers_reach_worker_files_and_provenance(tmp_path, condition):
    with project(tmp_path / "ledger", condition) as ledger:
        session = ledger.start_session()
        layers = {}
        for level in Condition:
            event = ledger.record(
                session,
                Origin(str(level), "example", "fixture", "1", {}),
                {level + ".txt": ("layer " + level).encode()},
            )
            layers[level] = {level + ".txt": Evidence.captured(event, level + ".txt")}
        files = capture_context(ledger, session, "next", layers)
        episode = Episode("next", "Use permitted files.", (), files=files)
        exposed = input_files(ledger, episode)
        assert set(exposed) == {
            level + ".txt" for level in Condition if level <= condition
        }
        assert b"PRIVATE" not in b"".join(exposed.values())
        journal = Journal(ledger, episode)
        encoded = ledger.read_artifact(journal.spec.artifacts["episode.json"])
        assert b"private" not in encoded
        assert not any(
            level + ".txt" in json.loads(encoded)["episode"]["files"]
            for level in Condition
            if level > condition
        )
        assert capture_context(ledger, session, "next", layers) == files
        layers[Condition.A] = {"changed.txt": next(iter(layers[Condition.A].values()))}
        with pytest.raises(OperationConflict):
            capture_context(ledger, session, "next", layers)


def test_unknown_condition_and_conflicting_filenames_fail_closed(tmp_path):
    with project(tmp_path / "bad", "unknown") as ledger:
        with pytest.raises(ValueError, match="condition"):
            capture_context(ledger, ledger.start_session(), "next", {})
    with project(tmp_path / "ledger", "E") as ledger:
        private = Evidence("private", ledger.project.snapshots["private"].artifact)
        layers = {level: {} for level in Condition}
        layers[Condition.A] = {"shared.txt": private}
        layers[Condition.B] = {"shared.txt": private}
        with pytest.raises(ValueError, match="filename"):
            capture_context(ledger, ledger.start_session(), "next", layers)


def test_attached_evidence_is_bound_to_episode_and_cannot_replace_a_snapshot(tmp_path):
    with project(tmp_path / "ledger", "A") as ledger:
        session = ledger.start_session()

        def note(operation, data):
            event = ledger.record(
                session, Origin(operation, "note", "worker", "1", {}), {"note": data}
            )
            return Evidence.captured(event, "note")

        first, second = note("one", b"first"), note("two", b"second")
        episode = Episode("next", "Use the note.", (), files={"notes.txt": first})
        with pytest.raises(TypeError):
            episode.files["notes.txt"] = second
        Journal(ledger, episode)
        with pytest.raises(OperationConflict):
            Journal(ledger, replace(episode, files={"notes.txt": second}))
        with pytest.raises(ValueError, match="filename"):
            Episode("next", "Use the note.", ("private",), files={"private": first})
        forged = Evidence("private", first.artifact)
        with pytest.raises(ValueError, match="matching snapshot or observation"):
            input_files(ledger, replace(episode, files={"notes.txt": forged}))
