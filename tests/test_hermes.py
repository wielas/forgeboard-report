"""Focused tests for stable, read-only Hermes 0.19 acquisition."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import forgeboard_report.hermes as hermes_module
from fixtures.hermes_019 import (
    ARCHIVED_METADATA,
    ARCHIVED_RUN_ID,
    ARCHIVED_TASK_ID,
    BLOCKED_RUN_ID,
    PARENT_TASK_ID,
    SidecarMutatingCopier,
    add_non_hot_journal,
    build_hermes_019_board,
    recorded_archived_show_json,
)
from forgeboard_report.errors import (
    InvalidSchemaError,
    SourceInconsistentError,
    SourceUnavailableError,
)
from forgeboard_report.hermes import (
    HERMES_SCHEMA_VERSION,
    MAX_CAPTURE_ATTEMPTS,
    Hermes019Layout,
    HermesSnapshotAdapter,
)


@pytest.fixture
def hermes_board(tmp_path: Path):
    fixture = build_hermes_019_board(tmp_path / "hermes")
    try:
        yield fixture
    finally:
        fixture.close()


def test_capture_preserves_required_rows_ids_raw_values_and_source_bytes(
    hermes_board,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = hermes_board.source_bytes()
    opened_paths: list[Path] = []
    real_connect = sqlite3.connect

    def recording_connect(database, *args, **kwargs):
        opened_paths.append(Path(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(hermes_module.sqlite3, "connect", recording_connect)
    snapshot = HermesSnapshotAdapter(Hermes019Layout(standard_root=hermes_board.root)).capture(
        "FORGE-BOARD"
    )

    assert hermes_board.source_bytes() == before
    assert opened_paths
    assert all(not path.is_relative_to(hermes_board.root) for path in opened_paths)
    assert snapshot.board_slug == "forge-board"
    assert snapshot.fingerprint.version == HERMES_SCHEMA_VERSION
    assert {member.name for member in snapshot.source_files} == {
        "kanban.db",
        "kanban.db-wal",
    }
    assert {card.id for card in snapshot.cards} == {
        PARENT_TASK_ID,
        ARCHIVED_TASK_ID,
        "t_f88c0d15a6ee",
    }
    assert [link.parent_id for link in snapshot.links] == [PARENT_TASK_ID]
    assert [run.id for run in snapshot.runs] == [41, ARCHIVED_RUN_ID, BLOCKED_RUN_ID]
    assert [event.id for event in snapshot.events] == [101, 102, 103, 104, 105]
    assert [comment.id for comment in snapshot.comments] == [201, 202]
    assert snapshot.runs[1].metadata == ARCHIVED_METADATA
    assert snapshot.runs[2].metadata == b'{"schema":"unknown.future.v1"}'
    with pytest.raises(FrozenInstanceError):
        snapshot.cards = ()  # type: ignore[misc]


def test_capture_agrees_with_recorded_public_show_json_but_retains_omitted_ids(
    hermes_board,
) -> None:
    snapshot = HermesSnapshotAdapter(Hermes019Layout(standard_root=hermes_board.root)).capture(
        hermes_board.board_slug
    )
    oracle = recorded_archived_show_json()
    card = next(card for card in snapshot.cards if card.id == ARCHIVED_TASK_ID)

    for field, expected in oracle["task"].items():
        assert getattr(card, field) == expected
    assert card.idempotency_key == oracle["bootstrap_idempotency_key"]

    comments = [comment for comment in snapshot.comments if comment.task_id == card.id]
    assert [
        {
            "author": comment.author,
            "body": comment.body,
            "created_at": comment.created_at,
        }
        for comment in comments
    ] == oracle["comments"]
    assert [comment.id for comment in comments] == [201]

    events = [event for event in snapshot.events if event.task_id == card.id]
    assert [
        {
            "kind": event.kind,
            "payload": json.loads(event.payload),
            "created_at": event.created_at,
            "run_id": event.run_id,
        }
        for event in events
    ] == oracle["events"]
    assert [event.id for event in events] == [103, 104]

    runs = [run for run in snapshot.runs if run.task_id == card.id]
    assert [
        {
            "id": run.id,
            "profile": run.profile,
            "step_key": run.step_key,
            "status": run.status,
            "outcome": run.outcome,
            "summary": run.summary,
            "error": run.error,
            "metadata": json.loads(run.metadata),
            "started_at": run.started_at,
            "ended_at": run.ended_at,
        }
        for run in runs
    ] == oracle["runs"]


def test_archived_bootstrap_key_and_unrelated_opaque_ids_are_not_substituted(
    hermes_board,
) -> None:
    snapshot = HermesSnapshotAdapter(Hermes019Layout(standard_root=hermes_board.root)).capture(
        "forge-board"
    )
    archived = next(card for card in snapshot.cards if card.status == "archived")
    unrelated = next(card for card in snapshot.cards if card.id == "t_f88c0d15a6ee")

    assert archived.id == ARCHIVED_TASK_ID
    assert archived.idempotency_key == "forge-board-CHUNK-ARCHIVED"
    assert unrelated.idempotency_key == "unrelated-exact-key"
    assert "forge-board-CHUNK-ARCHIVED" in (unrelated.body or "")
    assert all(card.id != "CHUNK-ARCHIVED" for card in snapshot.cards)


def test_capture_accepts_a_stable_rollback_journal_member(tmp_path: Path) -> None:
    fixture = build_hermes_019_board(
        tmp_path / "hermes",
        journal_mode="delete",
    )
    add_non_hot_journal(fixture)
    try:
        before = fixture.source_bytes()
        snapshot = HermesSnapshotAdapter(Hermes019Layout(standard_root=fixture.root)).capture(
            fixture.board_slug
        )
        assert fixture.source_bytes() == before
        assert [member.name for member in snapshot.source_files] == [
            "kanban.db",
            "kanban.db-journal",
        ]
    finally:
        fixture.close()


def test_membership_or_bytes_change_is_bounded_to_three_attempts(
    hermes_board,
) -> None:
    copier = SidecarMutatingCopier(hermes_board.database)
    adapter = HermesSnapshotAdapter(
        Hermes019Layout(standard_root=hermes_board.root),
        copy_file=copier,
    )

    with pytest.raises(SourceInconsistentError, match="all 3 capture attempts") as raised:
        adapter.capture(hermes_board.board_slug)

    assert copier.attempts == MAX_CAPTURE_ATTEMPTS
    assert raised.value.source == "Hermes board 'forge-board'"
    assert not any(
        path.name.startswith("forgeboard-hermes-") for path in hermes_board.root.rglob("*")
    )


def test_layout_honors_kanban_home_before_standard_root(tmp_path: Path) -> None:
    override = build_hermes_019_board(tmp_path / "override")
    other_root = tmp_path / "other"
    other_root.mkdir()
    try:
        layout = Hermes019Layout(
            environ={"HERMES_KANBAN_HOME": str(override.root)},
            standard_root=other_root,
        )
        location = layout.resolve("FORGE-BOARD")
        assert location.board_slug == "forge-board"
        assert location.database == override.database.resolve()
    finally:
        override.close()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("unknown", "unknown board"),
        ("missing", "database is missing"),
        ("invalid", "invalid board slug"),
        ("escape", "confined-path violation"),
    ],
)
def test_layout_errors_are_specific_and_create_nothing(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    root = tmp_path / "hermes"
    root.mkdir()
    slug = "missing-board"
    if case == "missing":
        (root / "kanban" / "boards" / slug).mkdir(parents=True)
    elif case == "invalid":
        slug = "../escape"
    elif case == "escape":
        outside = build_hermes_019_board(tmp_path / "outside", board_slug="escaped")
        board_parent = root / "kanban" / "boards"
        board_parent.mkdir(parents=True)
        (board_parent / "escaped").symlink_to(outside.database.parent, target_is_directory=True)
        slug = "escaped"

    before = _tree_bytes(root)
    with pytest.raises(SourceUnavailableError, match=message):
        Hermes019Layout(standard_root=root).resolve(slug)
    assert _tree_bytes(root) == before

    if case == "escape":
        outside.close()


def test_missing_required_019_column_fails_without_live_root_writes(tmp_path: Path) -> None:
    fixture = build_hermes_019_board(
        tmp_path / "hermes",
        journal_mode="delete",
    )
    with sqlite3.connect(fixture.database) as connection:
        connection.execute("ALTER TABLE task_runs DROP COLUMN metadata")
    before = _tree_bytes(fixture.root)

    with pytest.raises(InvalidSchemaError, match=r"task_runs\.metadata") as raised:
        HermesSnapshotAdapter(Hermes019Layout(standard_root=fixture.root)).capture(
            fixture.board_slug
        )

    assert raised.value.source == "Hermes board 'forge-board'"
    assert _tree_bytes(fixture.root) == before


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
        if not path.is_symlink()
    }
