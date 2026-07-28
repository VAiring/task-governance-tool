"""Read-only snapshot assembly for the static Task Viewer."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.storage import (
    SCHEMA_VERSION,
    DatabaseTarget,
    StorageError,
    operational_sqlite_error,
    utc_now,
    validate_snapshot_database,
)
from task_governance_tool.tasks import STATUSES, list_tasks_for_viewer
from task_governance_tool.viewer_config import (
    VIEWER_REFRESH_DISABLED_SECONDS,
    VIEWER_REFRESH_MAX_SECONDS,
    VIEWER_REFRESH_MIN_SECONDS,
)


SNAPSHOT_VERSION = 3
VIEWER_EVENT_LIMIT = 10
TEMPLATE_PLACEHOLDER = "__TASKGOV_SNAPSHOT_BASE64__"
REFRESH_INTERVAL_PLACEHOLDER = "__TASKGOV_REFRESH_INTERVAL_SECONDS__"
TEMPLATE_RELATIVE_PATH = Path("assets") / "task-viewer.template.html"
SNAPSHOT_ELEMENT_PREFIX = (
    '<script id="taskgov-snapshot" type="application/octet-stream">'
)
SNAPSHOT_ELEMENT_SUFFIX = "</script>"
# The accepted 500-task/5,000-event fixture can contain UTF-8 text near the
# persisted field limits before base64 expansion. This remains a bounded read.
MAX_VIEWER_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ViewerError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ViewerSnapshotResult:
    snapshot: dict[str, Any]
    task_count: int
    event_count: int


@dataclass(frozen=True)
class ViewerOutputTarget:
    path: Path
    database_path: Path
    state_root: Path


def build_viewer_snapshot(
    connection: sqlite3.Connection,
    target: DatabaseTarget,
    *,
    generated_at: str | None = None,
) -> ViewerSnapshotResult:
    """Build snapshot version 3 from one validated SQLite read transaction."""
    try:
        source_schema_version = validate_snapshot_database(connection, target)
        task_result = list_tasks_for_viewer(
            connection,
            target.project,
            event_limit=VIEWER_EVENT_LIMIT,
        )
    except StorageError:
        raise
    except sqlite3.Error as exc:
        raise operational_sqlite_error(
            exc,
            fallback_message="could not read viewer snapshot",
        ) from exc
    timestamp = generated_at or utc_now()
    counts = {"total": len(task_result.tasks)}
    counts.update(
        {
            status: sum(1 for task in task_result.tasks if task["status"] == status)
            for status in STATUSES
        }
    )
    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "generated_at": timestamp,
        "project": {
            "project_id": target.project.project_id,
            "display_name": target.project.display_name,
        },
        "source_schema_version": source_schema_version,
        "counts": counts,
        "tasks": task_result.tasks,
    }
    return ViewerSnapshotResult(
        snapshot=snapshot,
        task_count=len(task_result.tasks),
        event_count=task_result.event_count,
    )


def viewer_template_path(skill_root: Path | None = None) -> Path:
    root = (
        Path(skill_root)
        if skill_root is not None
        else Path(__file__).resolve().parents[2]
    )
    return root / TEMPLATE_RELATIVE_PATH


def inspect_canonical_viewer_status(
    *,
    path: Path,
    target: DatabaseTarget,
    current_snapshot: dict[str, Any] | None,
    compare_snapshot: bool,
    verify_template: bool,
    refresh_interval_seconds: int = VIEWER_REFRESH_DISABLED_SECONDS,
    skill_root: Path | None = None,
) -> str:
    """Inspect one canonical artifact at the requested maintenance boundary."""

    try:
        if path_is_reparse_point(path):
            return "repair_required"
        if not path.exists():
            return "not_present"
        if not path.is_file():
            return "repair_required"
        if path.stat().st_size > MAX_VIEWER_ARTIFACT_BYTES:
            return "repair_required"
        rendered = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "repair_required"

    if (
        rendered.count(SNAPSHOT_ELEMENT_PREFIX) != 1
        or rendered.count(SNAPSHOT_ELEMENT_SUFFIX) < 1
    ):
        return "repair_required"
    encoded_start = rendered.find(SNAPSHOT_ELEMENT_PREFIX) + len(
        SNAPSHOT_ELEMENT_PREFIX
    )
    encoded_end = rendered.find(SNAPSHOT_ELEMENT_SUFFIX, encoded_start)
    if encoded_end < encoded_start:
        return "repair_required"
    encoded = rendered[encoded_start:encoded_end]
    try:
        decoded = base64.b64decode(encoded, validate=True)
        snapshot = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return "repair_required"
    if not isinstance(snapshot, dict):
        return "repair_required"
    project = snapshot.get("project")
    if not isinstance(project, dict):
        return "repair_required"
    if (
        snapshot.get("snapshot_version") != SNAPSHOT_VERSION
        or snapshot.get("source_schema_version") != SCHEMA_VERSION
        or project.get("project_id") != target.project.project_id
    ):
        return "repair_required"
    if compare_snapshot:
        if current_snapshot is None:
            return "repair_required"
        generated_at = snapshot.get("generated_at")
        if not isinstance(generated_at, str):
            return "repair_required"
        expected_snapshot = dict(current_snapshot)
        expected_snapshot["generated_at"] = generated_at
        if snapshot != expected_snapshot:
            return "repair_required"
    if not verify_template:
        return "current"
    try:
        template = viewer_template_path(skill_root).read_text(encoding="utf-8")
        expected_template = _prepare_template(
            template,
            refresh_interval_seconds=refresh_interval_seconds,
        )
    except (OSError, UnicodeError):
        return "repair_required"
    except ViewerError:
        return "repair_required"
    normalized_rendered = (
        rendered[:encoded_start]
        + TEMPLATE_PLACEHOLDER
        + rendered[encoded_end:]
    )
    return (
        "current"
        if normalized_rendered == expected_template
        else "repair_required"
    )


def encode_snapshot(snapshot: dict[str, Any]) -> str:
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ViewerError("internal_error", "viewer snapshot version is unsupported")
    try:
        serialized = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ViewerError("internal_error", "viewer snapshot could not be serialized") from exc
    return base64.b64encode(serialized).decode("ascii")


def _validate_refresh_interval(value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or (
            value != VIEWER_REFRESH_DISABLED_SECONDS
            and not VIEWER_REFRESH_MIN_SECONDS
            <= value
            <= VIEWER_REFRESH_MAX_SECONDS
        )
    ):
        raise ViewerError(
            "internal_error",
            "viewer refresh interval is unsupported",
        )


def _prepare_template(
    template: str,
    *,
    refresh_interval_seconds: int,
) -> str:
    _validate_refresh_interval(refresh_interval_seconds)
    if template.count(TEMPLATE_PLACEHOLDER) != 1:
        raise ViewerError(
            "internal_error",
            "viewer template must contain exactly one snapshot placeholder",
        )
    if template.count(REFRESH_INTERVAL_PLACEHOLDER) != 1:
        raise ViewerError(
            "internal_error",
            "viewer template must contain exactly one refresh placeholder",
        )
    return template.replace(
        REFRESH_INTERVAL_PLACEHOLDER,
        str(refresh_interval_seconds),
    )


def render_viewer_html(
    snapshot: dict[str, Any],
    *,
    template_path: Path | None = None,
    refresh_interval_seconds: int = VIEWER_REFRESH_DISABLED_SECONDS,
) -> str:
    source_path = template_path or viewer_template_path()
    try:
        template = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ViewerError("internal_error", "viewer template could not be read") from exc

    prepared = _prepare_template(
        template,
        refresh_interval_seconds=refresh_interval_seconds,
    )
    return prepared.replace(TEMPLATE_PLACEHOLDER, encode_snapshot(snapshot))


def path_key(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = Path(os.path.abspath(path))
    return os.path.normcase(os.path.normpath(str(resolved)))


def paths_refer_to_same_location(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists() and os.path.samefile(left, right):
            return True
    except OSError:
        pass
    return path_key(left) == path_key(right)


def existing_parent_is_within(parent: Path, root: Path) -> bool:
    if not parent.exists() or not root.exists():
        return False
    current = parent
    while True:
        try:
            if os.path.samefile(current, root):
                return True
        except OSError:
            return False
        next_parent = current.parent
        if next_parent == current:
            return False
        current = next_parent


def path_is_reparse_point(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def validate_default_output_parent(path: Path, state_root: Path) -> None:
    try:
        relative_parent = path.parent.relative_to(state_root)
    except ValueError as exc:
        raise ViewerError(
            "output_path_invalid",
            "default viewer output must stay under skill state",
        ) from exc

    current = state_root
    components = [current]
    for part in relative_parent.parts:
        current /= part
        components.append(current)
    for component in components:
        try:
            if path_is_reparse_point(component):
                raise ViewerError(
                    "output_path_invalid",
                    "default viewer output parent must not contain a reparse point",
                )
        except OSError as exc:
            raise ViewerError(
                "output_path_invalid",
                "default viewer output parent could not be inspected",
            ) from exc

    if path.parent.exists():
        if not path.parent.is_dir():
            raise ViewerError(
                "output_path_invalid",
                "default viewer output parent must be a directory",
            )
        if not existing_parent_is_within(path.parent, state_root):
            raise ViewerError(
                "output_path_invalid",
                "default viewer output parent escaped skill state",
            )


def validate_output_database_separation(path: Path, database_path: Path) -> None:
    if paths_refer_to_same_location(path, database_path):
        raise ViewerError(
            "output_path_invalid",
            "viewer output must not refer to the task database",
        )


def validate_existing_output(path: Path) -> None:
    if path.is_symlink():
        raise ViewerError("output_path_invalid", "viewer output must not be a symbolic link")
    if path.exists() and not path.is_file():
        raise ViewerError("output_path_invalid", "viewer output must be a regular file")


def resolve_canonical_viewer_output_target(
    database_target: DatabaseTarget,
) -> ViewerOutputTarget:
    """Resolve the one DB-owned Viewer artifact; no custom output exists."""

    state_root = database_target.db_path.parent
    path = state_root / "viewer" / "task-viewer.html"
    validate_existing_output(path)
    validate_default_output_parent(path, state_root)
    validate_output_database_separation(path, database_target.db_path)
    return ViewerOutputTarget(
        path=path,
        database_path=database_target.db_path,
        state_root=state_root,
    )


def write_viewer_html(
    target: ViewerOutputTarget,
    html: str,
) -> bool:
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        target.path.parent.mkdir(parents=True, exist_ok=True)
        validate_default_output_parent(target.path, target.state_root)
        validate_existing_output(target.path)
        validate_output_database_separation(target.path, target.database_path)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".task-viewer-",
            suffix=".tmp",
            dir=target.path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(html)
            stream.flush()
            os.fsync(stream.fileno())
        validate_default_output_parent(target.path, target.state_root)
        validate_existing_output(target.path)
        validate_output_database_separation(target.path, target.database_path)
        replaced = target.path.exists()
        os.replace(temporary_path, target.path)
        temporary_path = None
        return replaced
    except OSError as exc:
        raise ViewerError("output_write_failed", "viewer output could not be written") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()
