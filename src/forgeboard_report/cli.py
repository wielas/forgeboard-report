"""The signed, read-only ``forgeboard-report`` command boundary."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from forgeboard_report.dependencies import audit, required_pull_request_refs
from forgeboard_report.domain import RawHermesSnapshot, ReportRequest
from forgeboard_report.errors import (
    InvalidCoreError,
    PublicationError,
    SourceInconsistentError,
    SourceUnavailableError,
    UsageError,
)
from forgeboard_report.github import fetch
from forgeboard_report.graph import load_graph
from forgeboard_report.hermes import Hermes019Layout, HermesSnapshotAdapter
from forgeboard_report.metrics import calculate
from forgeboard_report.normalize import normalize_sources
from forgeboard_report.publish import publish_report
from forgeboard_report.render import build_report, render

_Runner = Callable[..., subprocess.CompletedProcess[str]]
_Capture = Callable[[str, Path | None, _Runner], RawHermesSnapshot]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgeboard-report")
    parser.add_argument("--board", required=True, metavar="slug")
    parser.add_argument("--graph", required=True, metavar="path")
    parser.add_argument("--from", dest="from_original", required=True, metavar="aware-iso")
    parser.add_argument("--to", dest="to_original", required=True, metavar="aware-iso")
    parser.add_argument("--operator", action="append", default=[], metavar="exact-id")
    parser.add_argument("--output", required=True, metavar="new-directory")
    return parser


def _find_hermes_home(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    """Return the Hermes root implied by the first configured database path.

    The lower snapshot adapter owns all source validation.  This helper only
    selects the compatible root without invoking Hermes or creating files.
    """
    environment = os.environ if environ is None else environ
    configured = environment.get("HERMES_KANBAN_DB", "").strip()
    if configured:
        return Path(configured).expanduser().parent

    user_home = Path.home() if home is None else Path(home)
    for database in (
        user_home / ".hermes" / "kanban.db",
        user_home / ".config" / "hermes" / "kanban.db",
    ):
        if database.is_file():
            return database.parent
    return None


def capture(board_slug: str, hermes_home: Path | None, runner: _Runner) -> RawHermesSnapshot:
    """Capture through the established read-only Hermes 0.19 adapter.

    ``runner`` is deliberately accepted as the command's acquisition port;
    Hermes capture itself never launches a subprocess.
    """
    del runner
    layout = Hermes019Layout(standard_root=hermes_home) if hermes_home else Hermes019Layout()
    return HermesSnapshotAdapter(layout).capture(board_slug)


def _request_from_namespace(arguments: argparse.Namespace) -> ReportRequest:
    return ReportRequest(
        board_slug=arguments.board,
        graph_path=Path(arguments.graph),
        from_original=arguments.from_original,
        to_original=arguments.to_original,
        operator_ids=tuple(arguments.operator),
        output_directory=Path(arguments.output),
    )


def _validate_output_destination(destination: Path) -> None:
    """Fail invalid publication targets before acquiring any external source."""
    if destination.exists() or destination.is_symlink():
        raise UsageError("output", "destination must not already exist")
    if not destination.parent.is_dir():
        raise UsageError("output", "destination parent must already exist")


def run(
    argv: Sequence[str] | None = None,
    *,
    runner: _Runner = subprocess.run,
    snapshotter: _Capture = capture,
) -> None:
    """Run all report phases, creating output only at atomic publication."""
    arguments = _parser().parse_args(argv)
    request = _request_from_namespace(arguments)
    _validate_output_destination(request.output_directory)

    graph_snapshot = load_graph(request.graph_path)
    raw_snapshot = snapshotter(request.board_slug, _find_hermes_home(), runner)
    snapshot = normalize_sources(raw_snapshot, graph_snapshot, request)
    pr_refs = required_pull_request_refs(snapshot)
    pr_facts = fetch(pr_refs, runner)
    report = build_report(snapshot, calculate(snapshot), audit(snapshot, pr_facts))
    rendered = render(report)
    publish_report(request.output_directory, report, renderer=lambda _report: rendered)


def main(argv: Sequence[str] | None = None) -> int:
    """Translate typed boundary failures to the command's stable exit codes."""
    try:
        run(argv)
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2
    except UsageError as error:
        _diagnostic(error)
        return 2
    except (SourceUnavailableError, SourceInconsistentError) as error:
        _diagnostic(error)
        return 3
    except InvalidCoreError as error:
        _diagnostic(error)
        return 4
    except PublicationError as error:
        _diagnostic(error)
        return 5
    return 0


def _diagnostic(error: Exception) -> None:
    print(f"forgeboard-report: {error}", file=sys.stderr)
