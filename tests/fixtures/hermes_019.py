"""Hermes 0.19 SQLite fixtures and recorded public JSON compatibility values."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PARENT_TASK_ID = "t_03a7f2d9c140"
ARCHIVED_TASK_ID = "t_9b4e1f672acd"
UNRELATED_TASK_ID = "t_f88c0d15a6ee"

PARENT_RUN_ID = 41
ARCHIVED_RUN_ID = 42
BLOCKED_RUN_ID = 43

PARENT_METADATA = (
    '{"schema":"forge.chunk.v1","chunk_id":"CHUNK-1",'
    '"pr":"https://github.com/example/forge/pull/11"}'
)
ARCHIVED_METADATA = (
    '{"schema":"forge.judge.v1","chunk_id":"CHUNK-ARCHIVED",'
    '"pr":"https://github.com/example/forge/pull/12","verdict":"approve",'
    '"scores":{"spec_fidelity":2,"scenario_integrity":2,'
    '"architectural_conformance":3,"scope_discipline":3,'
    '"debt_honesty":3,"doc_reconciliation":3},"findings":[],'
    '"nits_as_cards":[],"spot_check_suggestion":"Inspect the archived diff.",'
    '"judge_model":"fixture-judge","tokens_estimate":0}'
)

HERMES_019_SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT,
    assignee TEXT,
    status TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    created_by TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    workspace_kind TEXT NOT NULL DEFAULT 'scratch',
    workspace_path TEXT,
    branch_name TEXT,
    claim_lock TEXT,
    claim_expires INTEGER,
    tenant TEXT,
    result TEXT,
    idempotency_key TEXT,
    current_run_id INTEGER,
    block_kind TEXT
);
CREATE TABLE task_links (
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
CREATE TABLE task_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    run_id INTEGER,
    kind TEXT NOT NULL,
    payload TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    profile TEXT,
    step_key TEXT,
    status TEXT NOT NULL,
    claim_lock TEXT,
    claim_expires INTEGER,
    worker_pid INTEGER,
    max_runtime_seconds INTEGER,
    last_heartbeat_at INTEGER,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    outcome TEXT,
    summary TEXT,
    metadata TEXT,
    error TEXT
);
"""


@dataclass
class Hermes019Fixture:
    """A board whose connection stays open when a WAL sidecar is requested."""

    root: Path
    board_slug: str
    database: Path
    connection: sqlite3.Connection | None

    def source_bytes(self) -> dict[str, bytes]:
        """Return all live SQLite bytes, including sidecars the adapter ignores."""
        return {
            path.name: path.read_bytes()
            for path in sorted(self.database.parent.iterdir())
            if path.name.startswith(self.database.name)
        }

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None


class SidecarMutatingCopier:
    """Copy normally, then change source membership/bytes once per attempt."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.attempts = 0

    def __call__(self, source: Path, destination: Path) -> None:
        with source.open("rb") as source_handle, destination.open("xb") as target:
            shutil.copyfileobj(source_handle, target)
        if source == self.database:
            self.attempts += 1
            journal = self.database.with_name(self.database.name + "-journal")
            journal.write_bytes(f"changed-on-attempt-{self.attempts}".encode())


def build_hermes_019_board(
    root: Path,
    *,
    board_slug: str = "forge-board",
    journal_mode: str = "wal",
) -> Hermes019Fixture:
    """Create representative 0.19 rows without importing or running Hermes."""
    if board_slug == "default":
        database = root / "kanban.db"
    else:
        database = root / "kanban" / "boards" / board_slug / "kanban.db"
    database.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database)
    mode = connection.execute(f"PRAGMA journal_mode={journal_mode}").fetchone()[0]
    if mode == "wal":
        connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(HERMES_019_SCHEMA)
    _insert_rows(connection, board_slug)
    connection.commit()

    keep_open = connection if mode == "wal" else None
    if keep_open is None:
        connection.close()
    return Hermes019Fixture(root, board_slug, database, keep_open)


def add_non_hot_journal(fixture: Hermes019Fixture) -> Path:
    """Add a stable rollback-journal member SQLite safely ignores."""
    journal = fixture.database.with_name(fixture.database.name + "-journal")
    journal.write_bytes(b"")
    return journal


def recorded_archived_show_json(board_slug: str = "forge-board") -> dict[str, Any]:
    """Recorded Hermes 0.19 ``show --json`` values for the archived fixture row."""
    return {
        "task": {
            "id": ARCHIVED_TASK_ID,
            "title": "Archived graph card",
            "body": "The graph key appears only in idempotency_key.",
            "assignee": "judge",
            "status": "archived",
            "priority": 4,
            "created_by": "forge-bootstrap",
            "created_at": 1_754_000_100,
            "started_at": 1_754_000_200,
            "completed_at": 1_754_000_300,
            "result": None,
        },
        "parents": [PARENT_TASK_ID],
        "children": [],
        "comments": [
            {
                "author": "operator-exact",
                "body": "Keep the archived evidence.",
                "created_at": 1_754_000_250,
            }
        ],
        "events": [
            {
                "kind": "claimed",
                "payload": {"lock": "opaque-lock", "run_id": ARCHIVED_RUN_ID},
                "created_at": 1_754_000_200,
                "run_id": ARCHIVED_RUN_ID,
            },
            {
                "kind": "completed",
                "payload": {"result_len": 0, "summary": "judge complete"},
                "created_at": 1_754_000_300,
                "run_id": ARCHIVED_RUN_ID,
            },
        ],
        "runs": [
            {
                "id": ARCHIVED_RUN_ID,
                "profile": "judge",
                "step_key": None,
                "status": "done",
                "outcome": "completed",
                "summary": "judge complete",
                "error": None,
                "metadata": json.loads(ARCHIVED_METADATA),
                "started_at": 1_754_000_200,
                "ended_at": 1_754_000_300,
            }
        ],
        "bootstrap_idempotency_key": f"{board_slug}-CHUNK-ARCHIVED",
    }


def _insert_rows(connection: sqlite3.Connection, board_slug: str) -> None:
    connection.executemany(
        """
        INSERT INTO tasks (
            id, title, body, assignee, status, priority, created_by,
            created_at, started_at, completed_at, result, idempotency_key,
            current_run_id, block_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                PARENT_TASK_ID,
                "Parent chunk",
                "Structured handoff lives on its run.",
                "worker",
                "done",
                2,
                "forge-bootstrap",
                1_754_000_000,
                1_754_000_010,
                1_754_000_090,
                None,
                f"{board_slug}-CHUNK-1",
                None,
                None,
            ),
            (
                ARCHIVED_TASK_ID,
                "Archived graph card",
                "The graph key appears only in idempotency_key.",
                "judge",
                "archived",
                4,
                "forge-bootstrap",
                1_754_000_100,
                1_754_000_200,
                1_754_000_300,
                None,
                f"{board_slug}-CHUNK-ARCHIVED",
                None,
                None,
            ),
            (
                UNRELATED_TASK_ID,
                f"Misleading prose {board_slug}-CHUNK-NOT-A-KEY",
                f"Body mentions {board_slug}-CHUNK-ARCHIVED but is unrelated.",
                "worker",
                "blocked",
                0,
                "human",
                1_754_000_400,
                1_754_000_410,
                None,
                None,
                "unrelated-exact-key",
                None,
                "needs_input",
            ),
        ],
    )
    connection.execute(
        "INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)",
        (PARENT_TASK_ID, ARCHIVED_TASK_ID),
    )
    connection.executemany(
        """
        INSERT INTO task_runs (
            id, task_id, profile, step_key, status, started_at, ended_at,
            outcome, summary, metadata, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                PARENT_RUN_ID,
                PARENT_TASK_ID,
                "worker",
                None,
                "done",
                1_754_000_010,
                1_754_000_090,
                "completed",
                "implementation complete",
                PARENT_METADATA,
                None,
            ),
            (
                ARCHIVED_RUN_ID,
                ARCHIVED_TASK_ID,
                "judge",
                None,
                "done",
                1_754_000_200,
                1_754_000_300,
                "completed",
                "judge complete",
                ARCHIVED_METADATA,
                None,
            ),
            (
                BLOCKED_RUN_ID,
                UNRELATED_TASK_ID,
                "worker",
                "implement",
                "blocked",
                1_754_000_410,
                1_754_000_420,
                "blocked",
                "waiting for a human",
                b'{"schema":"unknown.future.v1"}',
                "opaque failure evidence",
            ),
        ],
    )
    connection.executemany(
        """
        INSERT INTO task_events (id, task_id, run_id, kind, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (101, PARENT_TASK_ID, None, "created", '{"status":"ready"}', 1_754_000_000),
            (
                102,
                PARENT_TASK_ID,
                PARENT_RUN_ID,
                "completed",
                '{"result_len":0,"summary":"implementation complete"}',
                1_754_000_090,
            ),
            (
                103,
                ARCHIVED_TASK_ID,
                ARCHIVED_RUN_ID,
                "claimed",
                '{"lock":"opaque-lock","run_id":42}',
                1_754_000_200,
            ),
            (
                104,
                ARCHIVED_TASK_ID,
                ARCHIVED_RUN_ID,
                "completed",
                '{"result_len":0,"summary":"judge complete"}',
                1_754_000_300,
            ),
            (
                105,
                UNRELATED_TASK_ID,
                BLOCKED_RUN_ID,
                "blocked",
                '{"reason":"ask user","kind":"needs_input","recurrences":0}',
                1_754_000_420,
            ),
        ],
    )
    connection.executemany(
        """
        INSERT INTO task_comments (id, task_id, author, body, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                201,
                ARCHIVED_TASK_ID,
                "operator-exact",
                "Keep the archived evidence.",
                1_754_000_250,
            ),
            (
                202,
                PARENT_TASK_ID,
                "worker",
                "Handoff is in structured metadata.",
                1_754_000_080,
            ),
        ],
    )
