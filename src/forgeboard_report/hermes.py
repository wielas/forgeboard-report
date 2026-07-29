"""Read-only acquisition of stable Hermes 0.19 board snapshots."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from forgeboard_report.domain import (
    RawHermesCard,
    RawHermesComment,
    RawHermesEvent,
    RawHermesLink,
    RawHermesRun,
    RawHermesSnapshot,
    SourceFileFingerprint,
    SourceFingerprint,
)
from forgeboard_report.errors import (
    InvalidSchemaError,
    SourceInconsistentError,
    SourceUnavailableError,
)

HERMES_SCHEMA_VERSION = "0.19.0"
MAX_CAPTURE_ATTEMPTS = 3

_BOARD_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SIDECAR_SUFFIXES = ("-journal", "-wal")
_COPY_CHUNK_SIZE = 1024 * 1024

_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "tasks": (
        "id",
        "title",
        "body",
        "assignee",
        "status",
        "priority",
        "created_by",
        "created_at",
        "started_at",
        "completed_at",
        "result",
        "idempotency_key",
        "block_kind",
    ),
    "task_links": ("parent_id", "child_id"),
    "task_runs": (
        "id",
        "task_id",
        "profile",
        "step_key",
        "status",
        "outcome",
        "started_at",
        "ended_at",
        "summary",
        "metadata",
        "error",
    ),
    "task_events": ("id", "task_id", "run_id", "kind", "payload", "created_at"),
    "task_comments": ("id", "task_id", "author", "body", "created_at"),
}

CopyFile = Callable[[Path, Path], None]


@dataclass(frozen=True, slots=True)
class HermesBoardLocation:
    """A validated board location confined to one resolved Hermes root."""

    board_slug: str
    root: Path
    database: Path


class Hermes019Layout:
    """Resolve Hermes 0.19 board files without importing or invoking Hermes."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        standard_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._standard_root = standard_root
        self._home = Path.home() if home is None else Path(home)

    def resolve(self, board_slug: str) -> HermesBoardLocation:
        """Return one existing, confined database or raise an actionable error."""
        slug = self._normalize_slug(board_slug)
        source = f"Hermes board {slug!r}"
        root = self._resolve_root(source)

        if slug == "default":
            database = root / "kanban.db"
        else:
            board_directory = root / "kanban" / "boards" / slug
            if not board_directory.exists():
                raise SourceUnavailableError(source, "unknown board")
            self._confined_resolve(
                board_directory,
                root,
                source,
                "board directory",
            )
            database = board_directory / "kanban.db"

        if not database.exists():
            raise SourceUnavailableError(
                source,
                f"database is missing at {database}",
            )
        resolved_database = self._confined_resolve(database, root, source, "database")
        if not resolved_database.is_file():
            raise SourceUnavailableError(
                source,
                f"database is not a regular file: {database}",
            )
        return HermesBoardLocation(slug, root, resolved_database)

    @staticmethod
    def _normalize_slug(board_slug: str) -> str:
        if not isinstance(board_slug, str) or not _BOARD_SLUG.fullmatch(board_slug):
            raise SourceUnavailableError(
                "Hermes board",
                "invalid board slug; expected 1-64 alphanumerics, hyphens, or underscores",
            )
        return board_slug.lower()

    def _resolve_root(self, source: str) -> Path:
        override = self._environ.get("HERMES_KANBAN_HOME", "").strip()
        root = Path(override).expanduser() if override else self._default_root()
        try:
            resolved = root.resolve(strict=True)
        except OSError as error:
            raise SourceUnavailableError(
                source,
                f"Hermes root is unavailable at {root}: {error}",
            ) from error
        if not resolved.is_dir():
            raise SourceUnavailableError(source, f"Hermes root is not a directory: {root}")
        return resolved

    def _default_root(self) -> Path:
        if self._standard_root is not None:
            return Path(self._standard_root).expanduser()

        native_root = (
            self._home / "AppData" / "Local" / "hermes"
            if sys.platform == "win32"
            else self._home / ".hermes"
        )
        configured_home = self._environ.get("HERMES_HOME", "").strip()
        if not configured_home:
            return native_root

        configured = Path(configured_home).expanduser()
        try:
            configured.resolve(strict=False).relative_to(native_root.resolve(strict=False))
        except ValueError:
            if configured.parent.name == "profiles":
                return configured.parent.parent
            return configured
        return native_root

    @staticmethod
    def _confined_resolve(
        path: Path,
        root: Path,
        source: str,
        label: str,
    ) -> Path:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except ValueError as error:
            raise SourceUnavailableError(
                source,
                f"confined-path violation: {label} escapes Hermes root",
            ) from error
        except OSError as error:
            raise SourceUnavailableError(
                source,
                f"{label} is unavailable at {path}: {error}",
            ) from error
        return resolved


class HermesSnapshotAdapter:
    """Capture and extract a stable Hermes board through file reads only."""

    def __init__(
        self,
        layout: Hermes019Layout | None = None,
        *,
        copy_file: CopyFile | None = None,
    ) -> None:
        self._layout = Hermes019Layout() if layout is None else layout
        self._copy_file = _copy_binary_read_only if copy_file is None else copy_file

    def capture(self, board_slug: str) -> RawHermesSnapshot:
        """Return a complete raw snapshot or expose no snapshot at all."""
        location = self._layout.resolve(board_slug)
        source = f"Hermes board {location.board_slug!r}"

        for _attempt in range(1, MAX_CAPTURE_ATTEMPTS + 1):
            try:
                with tempfile.TemporaryDirectory(prefix="forgeboard-hermes-") as temp_name:
                    temp_directory = Path(temp_name)
                    captured = self._capture_attempt(location, temp_directory)
                    if captured is None:
                        continue
                    source_files, private_database = captured
                    return _extract_snapshot(
                        private_database,
                        location.board_slug,
                        source_files,
                    )
            except _SourceChanged:
                continue

        raise SourceInconsistentError(
            source,
            f"source membership or bytes changed during all {MAX_CAPTURE_ATTEMPTS} "
            "capture attempts",
        )

    def _capture_attempt(
        self,
        location: HermesBoardLocation,
        temp_directory: Path,
    ) -> tuple[tuple[SourceFileFingerprint, ...], Path] | None:
        before = _fingerprint_source_set(location)
        private_database = temp_directory / location.database.name

        copied: list[SourceFileFingerprint] = []
        for member in before:
            source_path = location.database.with_name(member.name)
            destination = temp_directory / member.name
            try:
                self._copy_file(source_path, destination)
                copied.append(_fingerprint_file(destination, member.name))
            except FileNotFoundError as error:
                raise _SourceChanged from error
            except OSError as error:
                raise SourceUnavailableError(
                    f"Hermes board {location.board_slug!r}",
                    f"cannot copy {source_path} through a read-only handle: {error}",
                ) from error

        after = _fingerprint_source_set(location)
        copied_tuple = tuple(copied)
        if before != after or before != copied_tuple:
            return None
        return before, private_database


def capture_hermes_snapshot(
    board_slug: str,
    *,
    layout: Hermes019Layout | None = None,
) -> RawHermesSnapshot:
    """Convenience boundary for production callers."""
    return HermesSnapshotAdapter(layout).capture(board_slug)


class _SourceChanged(RuntimeError):
    """An attempt lost source membership while it was reading."""


def _discover_source_paths(location: HermesBoardLocation) -> tuple[Path, ...]:
    database = location.database
    paths = [_resolve_source_member(database, location, "database")]
    for suffix in _SIDECAR_SUFFIXES:
        candidate = database.with_name(database.name + suffix)
        try:
            present = candidate.exists()
        except OSError as error:
            raise SourceUnavailableError(
                f"Hermes board {location.board_slug!r}",
                f"cannot inspect SQLite sidecar {candidate}: {error}",
            ) from error
        if not present:
            continue
        paths.append(_resolve_source_member(candidate, location, "SQLite sidecar"))
    return tuple(paths)


def _resolve_source_member(
    path: Path,
    location: HermesBoardLocation,
    label: str,
) -> Path:
    source = f"Hermes board {location.board_slug!r}"
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise _SourceChanged from error
    except OSError as error:
        raise SourceUnavailableError(
            source,
            f"{label} is unavailable at {path}: {error}",
        ) from error
    try:
        resolved.relative_to(location.root)
    except ValueError as error:
        raise SourceUnavailableError(
            source,
            f"confined-path violation: {label} escapes Hermes root",
        ) from error
    if not resolved.is_file():
        raise SourceUnavailableError(source, f"{label} is not a regular file: {path}")
    return resolved


def _fingerprint_source_set(
    location: HermesBoardLocation,
) -> tuple[SourceFileFingerprint, ...]:
    try:
        return tuple(
            _fingerprint_file(path, path.name) for path in _discover_source_paths(location)
        )
    except FileNotFoundError as error:
        raise _SourceChanged from error
    except OSError as error:
        raise SourceUnavailableError(
            f"Hermes board {location.board_slug!r}",
            f"cannot read source file: {error}",
        ) from error


def _fingerprint_file(path: Path, name: str) -> SourceFileFingerprint:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return SourceFileFingerprint(name=name, size=size, sha256=digest.hexdigest())


def _copy_binary_read_only(source_path: Path, destination: Path) -> None:
    with source_path.open("rb") as source, destination.open("xb") as target:
        shutil.copyfileobj(source, target, length=_COPY_CHUNK_SIZE)


def _combined_fingerprint(
    source_files: tuple[SourceFileFingerprint, ...],
) -> SourceFingerprint:
    digest = hashlib.sha256()
    for member in source_files:
        digest.update(member.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(member.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(member.sha256))
    return SourceFingerprint(
        kind="hermes-board",
        sha256=digest.hexdigest(),
        version=HERMES_SCHEMA_VERSION,
    )


def _extract_snapshot(
    private_database: Path,
    board_slug: str,
    source_files: tuple[SourceFileFingerprint, ...],
) -> RawHermesSnapshot:
    try:
        connection = sqlite3.connect(str(private_database), isolation_level=None)
    except sqlite3.Error as error:
        raise InvalidSchemaError(
            f"Hermes board {board_slug!r}",
            f"private SQLite snapshot cannot be opened: {error}",
        ) from error

    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        _validate_schema(connection, board_slug)
        cards = _read_cards(connection)
        links = _read_links(connection)
        runs = _read_runs(connection)
        events = _read_events(connection)
        comments = _read_comments(connection)
        connection.rollback()
    except InvalidSchemaError:
        raise
    except sqlite3.Error as error:
        raise InvalidSchemaError(
            f"Hermes board {board_slug!r}",
            f"private SQLite snapshot is unreadable as Hermes 0.19: {error}",
        ) from error
    finally:
        connection.close()

    return RawHermesSnapshot(
        board_slug=board_slug,
        cards=cards,
        links=links,
        runs=runs,
        events=events,
        comments=comments,
        fingerprint=_combined_fingerprint(source_files),
        source_files=source_files,
    )


def _validate_schema(connection: sqlite3.Connection, board_slug: str) -> None:
    for table, required in _REQUIRED_COLUMNS.items():
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            raise InvalidSchemaError(
                f"Hermes board {board_slug!r}",
                f"Hermes 0.19 schema is missing required table {table!r}",
            )
        actual = {row["name"] for row in rows}
        missing = sorted(set(required) - actual)
        if missing:
            missing_names = ", ".join(f"{table}.{column}" for column in missing)
            raise InvalidSchemaError(
                f"Hermes board {board_slug!r}",
                f"Hermes 0.19 schema is missing required column(s): {missing_names}",
            )


def _read_cards(connection: sqlite3.Connection) -> tuple[RawHermesCard, ...]:
    rows = connection.execute(
        """
        SELECT id, title, body, assignee, status, priority, created_by,
               created_at, started_at, completed_at, result, idempotency_key,
               block_kind
          FROM tasks
         ORDER BY id
        """
    )
    return tuple(
        RawHermesCard(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            assignee=row["assignee"],
            status=row["status"],
            priority=row["priority"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=row["result"],
            idempotency_key=row["idempotency_key"],
            block_kind=row["block_kind"],
        )
        for row in rows
    )


def _read_links(connection: sqlite3.Connection) -> tuple[RawHermesLink, ...]:
    rows = connection.execute(
        "SELECT parent_id, child_id FROM task_links ORDER BY parent_id, child_id"
    )
    return tuple(
        RawHermesLink(parent_id=row["parent_id"], child_id=row["child_id"]) for row in rows
    )


def _read_runs(connection: sqlite3.Connection) -> tuple[RawHermesRun, ...]:
    rows = connection.execute(
        """
        SELECT id, task_id, profile, step_key, status, outcome, started_at,
               ended_at, summary, metadata, error
          FROM task_runs
         ORDER BY id
        """
    )
    return tuple(
        RawHermesRun(
            id=row["id"],
            task_id=row["task_id"],
            profile=row["profile"],
            step_key=row["step_key"],
            status=row["status"],
            outcome=row["outcome"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            summary=row["summary"],
            metadata=row["metadata"],
            error=row["error"],
        )
        for row in rows
    )


def _read_events(connection: sqlite3.Connection) -> tuple[RawHermesEvent, ...]:
    rows = connection.execute(
        """
        SELECT id, task_id, run_id, kind, payload, created_at
          FROM task_events
         ORDER BY id
        """
    )
    return tuple(
        RawHermesEvent(
            id=row["id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            kind=row["kind"],
            payload=row["payload"],
            created_at=row["created_at"],
        )
        for row in rows
    )


def _read_comments(connection: sqlite3.Connection) -> tuple[RawHermesComment, ...]:
    rows = connection.execute(
        """
        SELECT id, task_id, author, body, created_at
          FROM task_comments
         ORDER BY id
        """
    )
    return tuple(
        RawHermesComment(
            id=row["id"],
            task_id=row["task_id"],
            author=row["author"],
            body=row["body"],
            created_at=row["created_at"],
        )
        for row in rows
    )
