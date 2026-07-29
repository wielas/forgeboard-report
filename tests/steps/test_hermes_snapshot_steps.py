"""BDD steps for stable, read-only Hermes 0.19 acquisition."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from fixtures.hermes_019 import (
    ARCHIVED_RUN_ID,
    ARCHIVED_TASK_ID,
    PARENT_TASK_ID,
    SidecarMutatingCopier,
    build_hermes_019_board,
    recorded_archived_show_json,
)
from forgeboard_report.errors import (
    InvalidSchemaError,
    SourceInconsistentError,
    SourceUnavailableError,
)
from forgeboard_report.hermes import Hermes019Layout, HermesSnapshotAdapter

scenarios("../features/hermes_snapshot.feature")


def _adapter(board) -> HermesSnapshotAdapter:
    return HermesSnapshotAdapter(Hermes019Layout(standard_root=board.root))


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
        if not path.is_symlink()
    }


@given(
    "a stable Hermes 0.19 database and sidecar set",
    target_fixture="stable_board",
)
def stable_board(tmp_path: Path, request):
    board = build_hermes_019_board(tmp_path / "stable-hermes")
    request.addfinalizer(board.close)
    return {
        "board": board,
        "before": board.source_bytes(),
        "snapshot": None,
    }


@when("the Hermes board is captured")
def capture_board(stable_board) -> None:
    stable_board["snapshot"] = _adapter(stable_board["board"]).capture(
        stable_board["board"].board_slug
    )


@then("every required row and stable id is in the raw snapshot")
def assert_required_rows(stable_board) -> None:
    snapshot = stable_board["snapshot"]
    assert {card.id for card in snapshot.cards} == {
        PARENT_TASK_ID,
        ARCHIVED_TASK_ID,
        "t_f88c0d15a6ee",
    }
    assert [(link.parent_id, link.child_id) for link in snapshot.links] == [
        (PARENT_TASK_ID, ARCHIVED_TASK_ID)
    ]
    assert [run.id for run in snapshot.runs] == [41, 42, 43]
    assert [event.id for event in snapshot.events] == [101, 102, 103, 104, 105]
    assert [comment.id for comment in snapshot.comments] == [201, 202]


@then("representative rows agree with recorded public show JSON")
def assert_public_compatibility(stable_board) -> None:
    snapshot = stable_board["snapshot"]
    oracle = recorded_archived_show_json()
    card = next(card for card in snapshot.cards if card.id == ARCHIVED_TASK_ID)
    run = next(run for run in snapshot.runs if run.id == ARCHIVED_RUN_ID)

    assert card.status == oracle["task"]["status"]
    assert card.created_at == oracle["task"]["created_at"]
    assert card.completed_at == oracle["task"]["completed_at"]
    assert card.idempotency_key == oracle["bootstrap_idempotency_key"]
    assert json.loads(run.metadata) == oracle["runs"][0]["metadata"]


@then("the live Hermes source bytes are unchanged")
def assert_live_bytes_unchanged(stable_board) -> None:
    assert stable_board["board"].source_bytes() == stable_board["before"]


@given(
    "a Hermes source that changes during every copy attempt",
    target_fixture="unstable_board",
)
def unstable_board(tmp_path: Path, request):
    board = build_hermes_019_board(tmp_path / "unstable-hermes")
    request.addfinalizer(board.close)
    copier = SidecarMutatingCopier(board.database)
    return {
        "board": board,
        "copier": copier,
        "snapshot": None,
        "error": None,
    }


@when("unstable Hermes capture is attempted")
def capture_unstable_board(unstable_board) -> None:
    adapter = HermesSnapshotAdapter(
        Hermes019Layout(standard_root=unstable_board["board"].root),
        copy_file=unstable_board["copier"],
    )
    try:
        unstable_board["snapshot"] = adapter.capture(unstable_board["board"].board_slug)
    except SourceInconsistentError as error:
        unstable_board["error"] = error


@then("capture fails as source-inconsistent after exactly three attempts")
def assert_bounded_inconsistency(unstable_board) -> None:
    assert isinstance(unstable_board["error"], SourceInconsistentError)
    assert unstable_board["copier"].attempts == 3


@then("no partial raw Hermes snapshot is exposed")
def assert_no_partial_snapshot(unstable_board) -> None:
    assert unstable_board["snapshot"] is None


@given(
    "an archived card with the only matching bootstrap key and unrelated opaque task ids",
    target_fixture="stable_board",
)
def archived_identity_board(tmp_path: Path, request):
    board = build_hermes_019_board(tmp_path / "archived-hermes")
    request.addfinalizer(board.close)
    return {
        "board": board,
        "before": board.source_bytes(),
        "snapshot": None,
    }


@then("all task rows are retained without prose or task-id substitution")
def assert_opaque_archived_rows(stable_board) -> None:
    snapshot = stable_board["snapshot"]
    archived = next(card for card in snapshot.cards if card.status == "archived")
    unrelated = next(card for card in snapshot.cards if card.id == "t_f88c0d15a6ee")

    assert archived.id == ARCHIVED_TASK_ID
    assert archived.idempotency_key == "forge-board-CHUNK-ARCHIVED"
    assert unrelated.idempotency_key == "unrelated-exact-key"
    assert archived.id != "CHUNK-ARCHIVED"
    assert "forge-board-CHUNK-ARCHIVED" in unrelated.body


@given(
    "unknown, missing, escaping, and incompatible Hermes board sources",
    target_fixture="invalid_sources",
)
def invalid_sources(tmp_path: Path, request):
    unknown_root = tmp_path / "unknown"
    unknown_root.mkdir()

    missing_root = tmp_path / "missing"
    (missing_root / "kanban" / "boards" / "known").mkdir(parents=True)

    outside = build_hermes_019_board(tmp_path / "outside", board_slug="escaped")
    request.addfinalizer(outside.close)
    escaping_root = tmp_path / "escaping"
    escaping_parent = escaping_root / "kanban" / "boards"
    escaping_parent.mkdir(parents=True)
    (escaping_parent / "escaped").symlink_to(
        outside.database.parent,
        target_is_directory=True,
    )

    incompatible = build_hermes_019_board(
        tmp_path / "incompatible",
        journal_mode="delete",
    )
    request.addfinalizer(incompatible.close)
    with sqlite3.connect(incompatible.database) as connection:
        connection.execute("ALTER TABLE task_events DROP COLUMN payload")

    cases = [
        (
            HermesSnapshotAdapter(Hermes019Layout(standard_root=unknown_root)),
            "unknown",
            SourceUnavailableError,
            "unknown board",
            unknown_root,
        ),
        (
            HermesSnapshotAdapter(Hermes019Layout(standard_root=missing_root)),
            "known",
            SourceUnavailableError,
            "database is missing",
            missing_root,
        ),
        (
            HermesSnapshotAdapter(Hermes019Layout(standard_root=escaping_root)),
            "escaped",
            SourceUnavailableError,
            "confined-path violation",
            escaping_root,
        ),
        (
            _adapter(incompatible),
            incompatible.board_slug,
            InvalidSchemaError,
            "task_events.payload",
            incompatible.root,
        ),
    ]
    return {
        "cases": cases,
        "before": {root: _tree_bytes(root) for *_, root in cases},
        "errors": [],
    }


@when("each invalid Hermes source is captured")
def capture_invalid_sources(invalid_sources) -> None:
    for adapter, slug, error_type, message, _root in invalid_sources["cases"]:
        with pytest.raises(error_type) as raised:
            adapter.capture(slug)
        invalid_sources["errors"].append((raised.value, message))


@then("each capture raises its specific actionable source or schema error")
def assert_specific_source_errors(invalid_sources) -> None:
    assert len(invalid_sources["errors"]) == 4
    for error, message in invalid_sources["errors"]:
        assert message in str(error)


@then("no invalid live Hermes root gains a file")
def assert_invalid_roots_unchanged(invalid_sources) -> None:
    for root, before in invalid_sources["before"].items():
        assert _tree_bytes(root) == before
