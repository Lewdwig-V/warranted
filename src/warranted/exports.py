"""Selected, non-authoritative file copies for inspection by a separate consumer."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile

from warranted.ledger import ArtifactRef, Ledger, Origin, _integer, _json_default, _text


def _names(values: tuple[str, ...]) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise TypeError("selection must be a tuple of names")
    for value in values:
        _text(value)
    if len(set(values)) != len(values):
        raise ValueError("selection contains duplicate names")
    return values


def export_evidence(
    ledger: Ledger,
    destination: Path,
    *,
    snapshots: tuple[str, ...] = (),
    observations: Mapping[int, tuple[str, ...]] | None = None,
    operations: tuple[str, ...] = (),
) -> Path:
    """Copy only explicitly selected records/channels and fixed public fields.

    The trusted host selects disclosures. The destination and its parent must be
    host-owned during export. Give consumers only the completed directory, never
    the Ledger object or access to its root. This is not process isolation.
    """
    snapshots = _names(snapshots)
    operations = _names(operations)
    if observations is None:
        observations = {}
    if not isinstance(observations, Mapping):
        raise TypeError("observations must map sequence numbers to channel tuples")
    observations = dict(observations)
    for sequence, channels in observations.items():
        _integer(sequence, 1)
        _names(channels)

    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination = destination.resolve()
    root = ledger.root.resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("export must be separate from authoritative storage")

    known_snapshots = ledger.project.snapshots
    known_observations = (
        {o.sequence: o for o in ledger.history()} if observations else {}
    )
    known_operations = (
        {op.request.origin.operation_id: op for op in ledger.operations()}
        if operations
        else {}
    )
    if (
        not set(snapshots) <= known_snapshots.keys()
        or not observations.keys() <= known_observations.keys()
        or not set(operations) <= known_operations.keys()
    ):
        raise ValueError("selection names an unknown record")
    for sequence, channels in observations.items():
        if not set(channels) <= known_observations[sequence].artifacts.keys():
            raise ValueError("selection names an unknown raw channel")

    selected_inputs = set(snapshots) | {
        name
        for sequence, channels in observations.items()
        for channel in channels
        if (name := f"observation/{sequence}/{channel}") not in known_snapshots
    }

    artifacts: dict[str, ArtifactRef] = {}

    def include(ref: ArtifactRef) -> ArtifactRef:
        artifacts[ref.digest] = ref
        return ref

    def origin_view(origin: Origin) -> dict:
        return {
            "operation_id": origin.operation_id,
            "kind": origin.kind,
            "producer": origin.producer,
            "producer_version": origin.producer_version,
            "inputs": {
                name: ref
                for name, ref in origin.inputs.items()
                if name in selected_inputs
            },
        }

    view = {
        "format": "warranted-evidence-export",
        "version": 1,
        "authoritative": False,
        "partial": True,
        "project_id": ledger.project.project_id,
        "snapshots": {
            name: {
                "version": known_snapshots[name].version,
                "artifact": include(known_snapshots[name].artifact),
            }
            for name in snapshots
        },
        "observations": [],
        "operations": [],
    }
    for sequence in sorted(observations):
        observation = known_observations[sequence]
        item = {
            "sequence": sequence,
            "session_id": observation.session_id,
            "captured_at": observation.captured_at,
            "origin": origin_view(observation.origin),
            "artifacts": {
                name: include(observation.artifacts[name])
                for name in observations[sequence]
            },
        }
        if observation.supersedes is None or observation.supersedes in observations:
            item["supersedes"] = observation.supersedes
        view["observations"].append(item)
    for operation_id in operations:
        operation = known_operations[operation_id]
        item = {
            "origin": origin_view(operation.request.origin),
            "session_id": operation.session_id,
            "reserved_at": operation.reserved_at,
            "reservation": operation.reservation,
            "state": operation.state,
            "dispatch_session_id": operation.dispatch_session_id,
            "dispatched_at": operation.dispatched_at,
        }
        if operation.completion is not None:
            item["result"] = operation.completion.result
            item["breaches"] = operation.completion.breaches
            sequence = operation.completion.observation.sequence
            if sequence in observations:
                item["observation_sequence"] = sequence
        view["operations"].append(item)

    encoded = (
        json.dumps(
            view, default=_json_default, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    )
    destination.mkdir()
    try:
        (destination / "artifacts").mkdir()
        for ref in artifacts.values():
            # Copy bytes, never a link to the authoritative inode.
            with (destination / "artifacts" / ref.digest).open("xb") as file:
                file.write(ledger.read_artifact(ref))
        # index.json is the publication marker; interrupted copies have no index.
        with NamedTemporaryFile(
            dir=destination, prefix=".index-", delete=False
        ) as file:
            file.write(encoded.encode("utf-8"))
        os.replace(file.name, destination / "index.json")
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination / "index.json"
