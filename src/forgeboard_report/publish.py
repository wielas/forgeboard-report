"""Atomic publication of the two already-rendered report artifacts."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from forgeboard_report.domain import Report
from forgeboard_report.errors import (
    InvalidCoreError,
    PublicationError,
    PublicationFlushError,
    PublicationRenameError,
    PublicationWriteError,
    UsageError,
)
from forgeboard_report.render import RenderedReport, render


@dataclass(frozen=True, slots=True)
class PublishedReport:
    """The one visible report bundle after a successful atomic rename."""

    destination: Path


def publish(destination: Path, artifacts: RenderedReport) -> PublishedReport:
    """Atomically publish a fully-rendered pair into a new destination directory."""
    destination = Path(destination)
    _validate_destination(destination)
    _validate_artifacts(artifacts)
    staging: Path | None = None
    try:
        try:
            staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
        except OSError as error:
            raise PublicationError("staging", str(error)) from error
        try:
            _write_artifact(staging / "report.json", artifacts.json)
            _write_artifact(staging / "report.md", artifacts.markdown)
        except (PublicationWriteError, PublicationFlushError):
            raise
        except OSError as error:
            raise PublicationWriteError(str(error)) from error
        try:
            os.replace(staging, destination)
        except OSError as error:
            raise PublicationRenameError(str(error)) from error
        staging = None
        return PublishedReport(destination=destination)
    finally:
        if staging is not None:
            _remove_staging(staging)


def publish_report(
    destination: Path,
    report: Report,
    *,
    renderer: Callable[[Report], RenderedReport] = render,
) -> PublishedReport:
    """Render both projections in memory, then atomically publish the pair."""
    _validate_destination(Path(destination))
    try:
        artifacts = renderer(report)
    except InvalidCoreError:
        raise
    except Exception as error:
        raise InvalidCoreError("report", f"renderer failed: {error}") from error
    return publish(destination, artifacts)


def _validate_destination(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise UsageError("output", "destination must not already exist")
    if not destination.parent.is_dir():
        raise UsageError("output", "destination parent must already exist")


def _validate_artifacts(artifacts: RenderedReport) -> None:
    if not isinstance(artifacts, RenderedReport):
        raise InvalidCoreError("report", "renderer must return RenderedReport")
    for name, contents in (("report.json", artifacts.json), ("report.md", artifacts.markdown)):
        if (
            not isinstance(contents, bytes)
            or not contents.endswith(b"\n")
            or contents.endswith(b"\n\n")
        ):
            raise InvalidCoreError("report", f"{name} must be UTF-8 bytes with one final newline")
        try:
            contents.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidCoreError("report", f"{name} is not UTF-8") from error


def _write_artifact(path: Path, contents: bytes) -> None:
    try:
        with path.open("xb") as stream:
            try:
                stream.write(contents)
            except OSError as error:
                raise PublicationWriteError(f"{path.name}: {error}") from error
            try:
                stream.flush()
                os.fsync(stream.fileno())
            except OSError as error:
                raise PublicationFlushError(f"{path.name}: {error}") from error
    except (PublicationWriteError, PublicationFlushError):
        raise
    except OSError as error:
        raise PublicationWriteError(f"{path.name}: {error}") from error


def _remove_staging(staging: Path) -> None:
    """Best-effort cleanup constrained to the directory this call created."""
    with suppress(OSError):
        shutil.rmtree(staging)
