"""Local evidence persistence for one trusted writer, without operation acceptance.

Transactions follow https://docs.python.org/3.12/library/sqlite3.html#transaction-control.
Artifact publication precedes database references. The durability scope is process
termination on a working local filesystem, not power loss or hostile host access.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from uuid import uuid4

FORMAT_VERSION = 1


class InvalidProject(ValueError):
    """Project metadata is incomplete or invalid."""


class UnsupportedVersion(ValueError):
    """The storage format is not supported by this implementation."""


class CorruptArtifact(ValueError):
    """Stored bytes do not match their recorded identity."""


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a nonempty string")


def _integer(value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"expected an integer >= {minimum}")


def _frozen_map(values: Mapping, item_type: type) -> Mapping:
    if not isinstance(values, Mapping):
        raise TypeError("expected a mapping")
    copied = dict(values)
    for name, value in copied.items():
        _text(name)
        if type(value) is not item_type:
            raise TypeError(f"{name}: expected {item_type.__name__}")
        if item_type is str:
            _text(value)
        elif item_type is int:
            _integer(value)
    return MappingProxyType(copied)


def _time(value: str) -> None:
    _text(value)
    if datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("recorded time must include a timezone")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ArtifactRef:
    digest: str
    size_bytes: int

    def __post_init__(self):
        if not isinstance(self.digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.digest
        ):
            raise ValueError("expected a lowercase SHA-256 digest")
        _integer(self.size_bytes)


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    origin: str
    version: str

    def __post_init__(self):
        if type(self.data) is not bytes:
            raise TypeError("snapshot data must be bytes")
        _text(self.origin)
        _text(self.version)


@dataclass(frozen=True)
class SnapshotRef:
    artifact: ArtifactRef
    origin: str
    version: str

    def __post_init__(self):
        if not isinstance(self.artifact, ArtifactRef):
            raise TypeError("expected an ArtifactRef")
        _text(self.origin)
        _text(self.version)


@dataclass(frozen=True)
class Manifest:
    fixture_id: str
    fixture_version: str
    run_id: str
    world_id: str
    environment: Mapping[str, str]
    allowances: Mapping[str, int]
    parent_world_id: None = None

    def __post_init__(self):
        for value in (
            self.fixture_id,
            self.fixture_version,
            self.run_id,
            self.world_id,
        ):
            _text(value)
        if self.parent_world_id is not None:
            raise ValueError("this slice supports one root world only")
        object.__setattr__(self, "environment", _frozen_map(self.environment, str))
        object.__setattr__(self, "allowances", _frozen_map(self.allowances, int))


@dataclass(frozen=True)
class Project:
    project_id: str
    created_at: str
    manifest: Manifest
    snapshots: Mapping[str, SnapshotRef]

    def __post_init__(self):
        _text(self.project_id)
        _time(self.created_at)
        if not isinstance(self.manifest, Manifest):
            raise TypeError("expected a Manifest")
        object.__setattr__(self, "snapshots", _frozen_map(self.snapshots, SnapshotRef))


@dataclass(frozen=True)
class Session:
    session_id: str
    started_at: str

    def __post_init__(self):
        _text(self.session_id)
        _time(self.started_at)


@dataclass(frozen=True)
class Origin:
    operation_id: str
    kind: str
    producer: str
    producer_version: str
    inputs: Mapping[str, ArtifactRef]

    def __post_init__(self):
        for value in (
            self.operation_id,
            self.kind,
            self.producer,
            self.producer_version,
        ):
            _text(value)
        object.__setattr__(self, "inputs", _frozen_map(self.inputs, ArtifactRef))


@dataclass(frozen=True)
class Observation:
    sequence: int
    session_id: str
    captured_at: str
    origin: Origin
    artifacts: Mapping[str, ArtifactRef]
    supersedes: int | None = None

    def __post_init__(self):
        _integer(self.sequence, 1)
        _text(self.session_id)
        _time(self.captured_at)
        if not isinstance(self.origin, Origin):
            raise TypeError("expected an Origin")
        object.__setattr__(self, "artifacts", _frozen_map(self.artifacts, ArtifactRef))
        if not self.artifacts:
            raise ValueError("an observation needs raw evidence")
        if self.supersedes is not None:
            _integer(self.supersedes, 1)
            if self.supersedes >= self.sequence:
                raise ValueError("superseded observation must precede its correction")


def _json_default(value):
    if isinstance(value, Mapping):
        return dict(value)
    return {field.name: getattr(value, field.name) for field in fields(value)}


def _dump(value) -> str:
    return json.dumps(
        {"version": FORMAT_VERSION, "value": value},
        default=_json_default,
        sort_keys=True,
        allow_nan=False,
    )


def _json_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON field")
    return result


def _load(data: str, decode):
    try:
        envelope = json.loads(data, object_pairs_hook=_json_object)
        if (
            set(envelope) != {"version", "value"}
            or type(envelope["version"]) is not int
        ):
            raise ValueError("invalid record envelope")
        if envelope["version"] != FORMAT_VERSION:
            raise UnsupportedVersion(
                f"unsupported record version: {envelope['version']}"
            )
        return decode(envelope["value"])
    except UnsupportedVersion:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise InvalidProject("invalid stored record") from error


def _refs(data) -> Mapping[str, ArtifactRef]:
    return _frozen_map(
        {name: ArtifactRef(**ref) for name, ref in data.items()}, ArtifactRef
    )


def _project(data) -> Project:
    if "parent_world_id" not in data["manifest"]:
        raise ValueError("missing world lineage")
    return Project(
        **{
            **data,
            "manifest": Manifest(**data["manifest"]),
            "snapshots": {
                name: SnapshotRef(
                    **{**snapshot, "artifact": ArtifactRef(**snapshot["artifact"])}
                )
                for name, snapshot in data["snapshots"].items()
            },
        }
    )


def _origin(data) -> Origin:
    return Origin(**{**data, "inputs": _refs(data["inputs"])})


def _read(root: Path, ref: ArtifactRef) -> bytes:
    if not isinstance(ref, ArtifactRef):
        raise TypeError("expected an ArtifactRef")
    data = (root / "artifacts" / "sha256" / ref.digest).read_bytes()
    if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.digest:
        raise CorruptArtifact(f"artifact does not match its reference: {ref.digest}")
    return data


def _publish(root: Path, data: bytes) -> ArtifactRef:
    ref = ArtifactRef(hashlib.sha256(data).hexdigest(), len(data))
    directory = root / "artifacts" / "sha256"
    destination = directory / ref.digest
    if destination.exists():
        _read(root, ref)
        return ref
    file = NamedTemporaryFile(dir=directory, prefix=".tmp-", delete=False)
    temporary = Path(file.name)
    try:
        with file:
            file.write(data)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            _read(root, ref)
    finally:
        temporary.unlink(missing_ok=True)
    return ref


def _connect(root: Path, *, create: bool = False) -> sqlite3.Connection:
    uri = (root / "ledger.sqlite3").as_uri() + ("?mode=rwc" if create else "?mode=rw")
    connection = sqlite3.connect(uri, uri=True, autocommit=True)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.autocommit = False
        return connection
    except BaseException:
        connection.close()
        raise


class Ledger:
    """A trusted host's append-only observations and raw artifacts."""

    def __init__(self, root: Path, connection: sqlite3.Connection, project: Project):
        self.root = root
        self._connection = connection
        self._project = project

    @property
    def project(self) -> Project:
        return self._project

    @classmethod
    def create(
        cls, root: Path, manifest: Manifest, snapshots: Mapping[str, Snapshot]
    ) -> Ledger:
        if not isinstance(manifest, Manifest):
            raise TypeError("expected a Manifest")
        snapshots = _frozen_map(snapshots, Snapshot)
        root = Path(root).absolute()
        root.mkdir()
        (root / "artifacts" / "sha256").mkdir(parents=True)
        published = {
            name: SnapshotRef(
                _publish(root, snapshot.data), snapshot.origin, snapshot.version
            )
            for name, snapshot in snapshots.items()
        }
        project = Project(str(uuid4()), _now(), manifest, published)
        connection = _connect(root, create=True)
        try:
            with connection:
                connection.execute("""CREATE TABLE project (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    format_version INTEGER NOT NULL, data TEXT NOT NULL)""")
                connection.execute("""CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)""")
                connection.execute("""CREATE TABLE observations (
                    sequence INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    captured_at TEXT NOT NULL, origin TEXT NOT NULL,
                    artifacts TEXT NOT NULL,
                    supersedes INTEGER REFERENCES observations(sequence))""")
                connection.execute(
                    "INSERT INTO project VALUES (1, ?, ?)",
                    (FORMAT_VERSION, _dump(project)),
                )
            return cls(root, connection, project)
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open(cls, root: Path) -> Ledger:
        root = Path(root).absolute()
        if not (root / "ledger.sqlite3").is_file():
            raise FileNotFoundError(root / "ledger.sqlite3")
        connection = _connect(root)
        try:
            with connection:
                header = {
                    row[1] for row in connection.execute("PRAGMA table_info(project)")
                }
                if not {"singleton", "format_version", "data"} <= header:
                    raise InvalidProject("incomplete project header")
                rows = connection.execute(
                    "SELECT singleton, format_version, data FROM project"
                ).fetchall()
                if len(rows) != 1 or rows[0][0] != 1:
                    raise InvalidProject("missing or invalid project record")
                if rows[0][1] != FORMAT_VERSION:
                    raise UnsupportedVersion(
                        f"unsupported project version: {rows[0][1]}"
                    )
                required = {
                    "project": {"singleton", "format_version", "data"},
                    "sessions": {"session_id", "started_at"},
                    "observations": {
                        "sequence",
                        "session_id",
                        "captured_at",
                        "origin",
                        "artifacts",
                        "supersedes",
                    },
                }
                for table, columns in required.items():
                    actual = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    }
                    if actual != columns:
                        raise InvalidProject(f"incomplete or invalid schema: {table}")
                project = _load(rows[0][2], _project)
            return cls(root, connection, project)
        except BaseException:
            connection.close()
            raise

    def start_session(self) -> str:
        session = Session(str(uuid4()), _now())
        with self._connection:
            self._connection.execute(
                "INSERT INTO sessions VALUES (?, ?)",
                (session.session_id, session.started_at),
            )
        return session.session_id

    def sessions(self) -> tuple[Session, ...]:
        with self._connection:
            rows = self._connection.execute(
                "SELECT session_id, started_at FROM sessions ORDER BY rowid"
            ).fetchall()
        try:
            return tuple(Session(*row) for row in rows)
        except (TypeError, ValueError) as error:
            raise InvalidProject("invalid stored session") from error

    def record(
        self,
        session_id: str,
        origin: Origin,
        raw: Mapping[str, bytes],
        supersedes: int | None = None,
    ) -> Observation:
        _text(session_id)
        if not isinstance(origin, Origin):
            raise TypeError("expected an Origin")
        raw = _frozen_map(raw, bytes)
        if not raw:
            raise ValueError("an observation needs raw evidence")
        if supersedes is not None:
            _integer(supersedes, 1)
        with self._connection:
            if (
                self._connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("unknown session")
            if (
                supersedes is not None
                and self._connection.execute(
                    "SELECT 1 FROM observations WHERE sequence = ?", (supersedes,)
                ).fetchone()
                is None
            ):
                raise ValueError("unknown superseded observation")
            for name, ref in origin.inputs.items():
                snapshot = self.project.snapshots.get(name)
                if snapshot is None or snapshot.artifact != ref:
                    raise ValueError(f"input is not a project snapshot: {name}")
                self.read_artifact(ref)

            # ponytail: scan references on writes; index artifacts if volume grows.
            known = {
                s.artifact.digest: s.artifact for s in self.project.snapshots.values()
            }
            for (encoded,) in self._connection.execute(
                "SELECT artifacts FROM observations"
            ):
                known.update(
                    (ref.digest, ref) for ref in _load(encoded, _refs).values()
                )
            for data in raw.values():
                if ref := known.get(hashlib.sha256(data).hexdigest()):
                    # Never silently repair missing committed bytes.
                    self.read_artifact(ref)
            artifacts = {name: _publish(self.root, data) for name, data in raw.items()}
            return self._insert_observation(session_id, origin, artifacts, supersedes)

    def _insert_observation(
        self,
        session_id: str,
        origin: Origin,
        artifacts: Mapping[str, ArtifactRef],
        supersedes: int | None,
    ) -> Observation:
        """Insert within the caller's transaction, without committing it."""
        captured_at = _now()
        cursor = self._connection.execute(
            "INSERT INTO observations "
            "(session_id, captured_at, origin, artifacts, supersedes) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, captured_at, _dump(origin), _dump(artifacts), supersedes),
        )
        return Observation(
            cursor.lastrowid, session_id, captured_at, origin, artifacts, supersedes
        )

    def history(self, after: int = 0) -> tuple[Observation, ...]:
        _integer(after)
        with self._connection:
            rows = self._connection.execute(
                "SELECT sequence, session_id, captured_at, origin, artifacts, "
                "supersedes "
                "FROM observations WHERE sequence > ? ORDER BY sequence",
                (after,),
            ).fetchall()
        try:
            return tuple(
                Observation(
                    sequence,
                    session,
                    time,
                    _load(origin, _origin),
                    _load(artifacts, _refs),
                    supersedes,
                )
                for sequence, session, time, origin, artifacts, supersedes in rows
            )
        except UnsupportedVersion:
            raise
        except (TypeError, ValueError) as error:
            raise InvalidProject("invalid stored observation") from error

    def read_artifact(self, ref: ArtifactRef) -> bytes:
        return _read(self.root, ref)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
