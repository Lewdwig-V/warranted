"""Local evidence and operation accounting for one trusted writer.

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
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from uuid import uuid4

FORMAT_VERSION = 2


class InvalidProject(ValueError):
    """Project metadata is incomplete or invalid."""


class UnsupportedVersion(ValueError):
    """The storage format is not supported by this implementation."""


class CorruptArtifact(ValueError):
    """Stored bytes do not match their recorded identity."""


class BudgetExceeded(ValueError):
    """The project cannot reserve or dispatch more work."""


class OperationConflict(ValueError):
    """An operation ID already names a different request or completion."""


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


@dataclass(frozen=True)
class Request:
    origin: Origin
    context: Project

    def __post_init__(self):
        if not isinstance(self.origin, Origin) or not isinstance(self.context, Project):
            raise TypeError("expected an Origin and Project context")


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    exit_code: int | None
    usage: Mapping[str, int]
    elapsed_ns: int

    def __post_init__(self):
        if not isinstance(self.outcome, Outcome):
            raise TypeError("expected a known Outcome")
        if self.outcome is Outcome.INFRASTRUCTURE_FAILURE:
            if self.exit_code is not None:
                raise ValueError("infrastructure failure has no process exit code")
        elif type(self.exit_code) is not int or (
            (self.exit_code == 0) != (self.outcome is Outcome.SUCCEEDED)
        ):
            raise ValueError("process outcome and exit code disagree")
        object.__setattr__(self, "usage", _frozen_map(self.usage, int))
        _integer(self.elapsed_ns)


@dataclass(frozen=True)
class Completion:
    observation: Observation
    result: Result
    breaches: tuple[str, ...]


@dataclass(frozen=True)
class Operation:
    request: Request
    session_id: str
    reserved_at: str
    reservation: Mapping[str, int]
    dispatch_session_id: str | None
    dispatched_at: str | None
    completion: Completion | None

    def __post_init__(self):
        if not isinstance(self.request, Request):
            raise TypeError("expected a Request")
        _text(self.session_id)
        _time(self.reserved_at)
        object.__setattr__(self, "reservation", _frozen_map(self.reservation, int))
        if self.dispatch_session_id is None:
            if self.dispatched_at is not None or self.completion is not None:
                raise ValueError("result or dispatch time without a dispatch session")
        else:
            _text(self.dispatch_session_id)
            _time(self.dispatched_at)

    @property
    def state(self) -> str:
        if self.completion is not None:
            return "completed"
        return "unknown" if self.dispatched_at is not None else "pending"


@dataclass(frozen=True)
class Balance:
    limit: int
    spent: int
    reserved: int

    @property
    def available(self) -> int:
        return self.limit - self.spent - self.reserved


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


def _request(data) -> Request:
    return Request(
        **{
            **data,
            "origin": _origin(data["origin"]),
            "context": _project(data["context"]),
        }
    )


def _result(data) -> Result:
    return Result(**{**data, "outcome": Outcome(data["outcome"])})


def _observation(row) -> Observation:
    sequence, session, time, origin, artifacts, supersedes = row
    return Observation(
        sequence,
        session,
        time,
        _load(origin, _origin),
        _load(artifacts, _refs),
        supersedes,
    )


def _breaches(reservation: Mapping[str, int], result: Result) -> tuple[str, ...]:
    return tuple(
        sorted(
            unit
            for unit, used in result.usage.items()
            if unit not in reservation or used > reservation[unit]
        )
    )


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
                connection.execute("""CREATE TABLE operations (
                    operation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    reserved_at TEXT NOT NULL, request TEXT NOT NULL,
                    reservation TEXT NOT NULL,
                    dispatch_session_id TEXT REFERENCES sessions(session_id),
                    dispatched_at TEXT,
                    observation_sequence INTEGER UNIQUE
                        REFERENCES observations(sequence),
                    result TEXT, breaches TEXT,
                    CHECK ((dispatch_session_id IS NULL) = (dispatched_at IS NULL)),
                    CHECK ((observation_sequence IS NULL) = (result IS NULL)),
                    CHECK ((result IS NULL) = (breaches IS NULL)),
                    CHECK (result IS NULL OR dispatched_at IS NOT NULL))""")
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
                    "operations": {
                        "operation_id",
                        "session_id",
                        "reserved_at",
                        "request",
                        "reservation",
                        "dispatch_session_id",
                        "dispatched_at",
                        "observation_sequence",
                        "result",
                        "breaches",
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
            self._check_session(session_id)
            if (
                supersedes is not None
                and self._connection.execute(
                    "SELECT 1 FROM observations WHERE sequence = ?", (supersedes,)
                ).fetchone()
                is None
            ):
                raise ValueError("unknown superseded observation")
            self._check_origin(origin)
            artifacts = self._publish_raw(raw)
            return self._insert_observation(session_id, origin, artifacts, supersedes)

    def _check_session(self, session_id: str) -> None:
        _text(session_id)
        if (
            self._connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            is None
        ):
            raise ValueError("unknown session")

    def _check_origin(self, origin: Origin) -> None:
        for name, ref in origin.inputs.items():
            snapshot = self.project.snapshots.get(name)
            expected = snapshot.artifact if snapshot is not None else None
            if snapshot is None and (
                match := re.fullmatch(
                    r"observation/([1-9][0-9]*)/(.+)", name, re.DOTALL
                )
            ):
                sequence, channel = match.groups()
                # Bind to the committed capture and channel, not just equal bytes.
                row = self._connection.execute(
                    "SELECT artifacts FROM observations WHERE sequence = ?", (sequence,)
                ).fetchone()
                if row is not None:
                    expected = _load(row[0], _refs).get(channel)
            if expected != ref:
                raise ValueError(
                    f"input is not a matching snapshot or observation: {name}"
                )
            self.read_artifact(ref)

    def _publish_raw(self, raw: Mapping[str, bytes]) -> Mapping[str, ArtifactRef]:
        # ponytail: scan references on writes; index artifacts if volume grows.
        known = {s.artifact.digest: s.artifact for s in self.project.snapshots.values()}
        for (encoded,) in self._connection.execute(
            "SELECT artifacts FROM observations"
        ):
            known.update((ref.digest, ref) for ref in _load(encoded, _refs).values())
        for data in raw.values():
            if ref := known.get(hashlib.sha256(data).hexdigest()):
                # Never silently repair missing committed bytes.
                self.read_artifact(ref)
        return {name: _publish(self.root, data) for name, data in raw.items()}

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
            return tuple(_observation(row) for row in rows)
        except UnsupportedVersion:
            raise
        except (TypeError, ValueError) as error:
            raise InvalidProject("invalid stored observation") from error

    def _operation(self, row) -> Operation:
        try:
            (
                operation_id,
                session,
                time,
                request,
                reservation,
                dispatch_session,
                dispatched_at,
                sequence,
                result,
                breaches,
            ) = row
            request = _load(request, _request)
            reservation = _load(reservation, lambda data: _frozen_map(data, int))
            if (
                request.origin.operation_id != operation_id
                or request.context != self.project
            ):
                raise ValueError("operation identity differs from its project or key")
            if not reservation.keys() <= self.project.manifest.allowances.keys():
                raise ValueError("reservation has unknown units")
            completion = None
            if sequence is not None:
                observation = self._connection.execute(
                    "SELECT sequence, session_id, captured_at, origin, artifacts, "
                    "supersedes "
                    "FROM observations WHERE sequence = ?",
                    (sequence,),
                ).fetchone()
                observation = _observation(observation)
                result = _load(result, _result)
                breaches = _load(breaches, lambda data: data)
                if (
                    observation.origin != request.origin
                    or observation.supersedes is not None
                    or not reservation.keys() <= result.usage.keys()
                    or breaches != list(_breaches(reservation, result))
                ):
                    raise ValueError("completion does not match its operation")
                completion = Completion(observation, result, tuple(breaches))
            elif result is not None or breaches is not None:
                raise ValueError("completion is missing its observation")
            return Operation(
                request,
                session,
                time,
                reservation,
                dispatch_session,
                dispatched_at,
                completion,
            )
        except UnsupportedVersion:
            raise
        except (TypeError, ValueError) as error:
            raise InvalidProject("invalid stored operation") from error

    def _operations(self) -> tuple[Operation, ...]:
        rows = self._connection.execute(
            "SELECT operation_id, session_id, reserved_at, request, reservation, "
            "dispatch_session_id, dispatched_at, observation_sequence, "
            "result, breaches "
            "FROM operations ORDER BY rowid"
        ).fetchall()
        return tuple(self._operation(row) for row in rows)

    def operations(self) -> tuple[Operation, ...]:
        """Inspect metadata, including unresolved work and damaged evidence."""
        with self._connection:
            return self._operations()

    def _lookup(self, request: Request) -> Operation | None:
        if not isinstance(request, Request):
            raise TypeError("expected a Request")
        if request.context != self.project:
            raise OperationConflict("request context differs from the fixed project")
        row = self._connection.execute(
            "SELECT operation_id, session_id, reserved_at, request, reservation, "
            "dispatch_session_id, dispatched_at, observation_sequence, "
            "result, breaches "
            "FROM operations WHERE operation_id = ?",
            (request.origin.operation_id,),
        ).fetchone()
        operation = self._operation(row) if row is not None else None
        if operation is not None and operation.request != request:
            raise OperationConflict("operation ID already names a different request")
        self._check_origin(request.origin)
        for snapshot in request.context.snapshots.values():
            self.read_artifact(snapshot.artifact)
        if operation is not None and operation.completion is not None:
            for ref in operation.completion.observation.artifacts.values():
                self.read_artifact(ref)
        return operation

    def lookup(self, request: Request) -> Operation | None:
        """Match exact identity and verify bytes before returning reusable work."""
        with self._connection:
            return self._lookup(request)

    def _accounting(self, operations: tuple[Operation, ...]) -> Mapping[str, Balance]:
        spent = dict.fromkeys(self.project.manifest.allowances, 0)
        reserved = dict(spent)
        # ponytail: derive totals from receipts; add an index if history grows costly.
        for operation in operations:
            if operation.completion is None:
                for unit, amount in operation.reservation.items():
                    reserved[unit] += amount
            else:
                for unit, amount in operation.completion.result.usage.items():
                    spent[unit] = spent.get(unit, 0) + amount
        return MappingProxyType(
            {
                unit: Balance(
                    self.project.manifest.allowances.get(unit, 0),
                    amount,
                    reserved.get(unit, 0),
                )
                for unit, amount in spent.items()
            }
        )

    def accounting(self) -> Mapping[str, Balance]:
        with self._connection:
            return self._accounting(self._operations())

    def _check_budget(self, operations: tuple[Operation, ...]) -> None:
        if any(
            op.completion is not None and op.completion.breaches for op in operations
        ):
            raise BudgetExceeded("recorded usage breach blocks further dispatch")
        if any(item.available < 0 for item in self._accounting(operations).values()):
            raise BudgetExceeded("project allowance exceeded")

    def reserve(
        self, session_id: str, request: Request, reservation: Mapping[str, int]
    ) -> Operation:
        """Reserve once. Repeated requests return existing state without dispatch."""
        reservation = _frozen_map(reservation, int)
        with self._connection:
            self._check_session(session_id)
            existing = self._lookup(request)
            if existing is not None:
                if existing.reservation != reservation:
                    raise OperationConflict("operation has a different reservation")
                return existing
            if not reservation.keys() <= self.project.manifest.allowances.keys():
                raise ValueError("reservation has unknown units")
            operations = self._operations()
            self._check_budget(operations)
            balances = self._accounting(operations)
            if any(
                amount > balances[unit].available
                for unit, amount in reservation.items()
            ):
                raise BudgetExceeded("reservation exceeds remaining allowance")
            operation = Operation(
                request, session_id, _now(), reservation, None, None, None
            )
            self._connection.execute(
                "INSERT INTO operations "
                "(operation_id, session_id, reserved_at, request, reservation) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    request.origin.operation_id,
                    session_id,
                    operation.reserved_at,
                    _dump(request),
                    _dump(reservation),
                ),
            )
            return operation

    def begin(self, session_id: str, request: Request) -> bool:
        """Commit the dispatch marker. Only a True return permits execution."""
        with self._connection:
            self._check_session(session_id)
            operation = self._lookup(request)
            if operation is None:
                raise ValueError("operation has no reservation")
            if operation.state != "pending":
                return False
            self._check_budget(self._operations())
            self._connection.execute(
                "UPDATE operations SET dispatch_session_id = ?, dispatched_at = ? "
                "WHERE operation_id = ?",
                (session_id, _now(), request.origin.operation_id),
            )
        return True

    def complete(
        self,
        session_id: str,
        request: Request,
        result: Result,
        raw: Mapping[str, bytes],
    ) -> Completion:
        """Atomically capture evidence and settle usage; never dispatch or retry."""
        if not isinstance(result, Result):
            raise TypeError("expected a Result")
        raw = _frozen_map(raw, bytes)
        if not raw:
            raise ValueError("completion needs raw evidence")
        with self._connection:
            self._check_session(session_id)
            operation = self._lookup(request)
            if operation is None or operation.state == "pending":
                raise ValueError("operation has no recorded dispatch")
            if operation.completion is not None:
                artifacts = {
                    name: ArtifactRef(hashlib.sha256(data).hexdigest(), len(data))
                    for name, data in raw.items()
                }
                if (
                    operation.completion.result != result
                    or operation.completion.observation.artifacts != artifacts
                ):
                    raise OperationConflict(
                        "operation already has a different completion"
                    )
                return operation.completion
            if not operation.reservation.keys() <= result.usage.keys():
                raise ValueError("actual usage must cover every reserved unit")
            artifacts = self._publish_raw(raw)
            observation = self._insert_observation(
                session_id, request.origin, artifacts, None
            )
            completion = Completion(
                observation, result, _breaches(operation.reservation, result)
            )
            self._connection.execute(
                "UPDATE operations "
                "SET observation_sequence = ?, result = ?, breaches = ? "
                "WHERE operation_id = ?",
                (
                    observation.sequence,
                    _dump(result),
                    _dump(completion.breaches),
                    request.origin.operation_id,
                ),
            )
            return completion

    def read_artifact(self, ref: ArtifactRef) -> bytes:
        return _read(self.root, ref)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
