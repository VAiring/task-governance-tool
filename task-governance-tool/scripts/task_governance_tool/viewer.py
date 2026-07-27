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
    default_viewer_output_path,
    operational_sqlite_error,
    utc_now,
    validate_snapshot_database,
)
from task_governance_tool.tasks import STATUSES, list_tasks_for_viewer


SNAPSHOT_VERSION = 3
VIEWER_EVENT_LIMIT = 10
TEMPLATE_PLACEHOLDER = "__TASKGOV_SNAPSHOT_BASE64__"
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
    explicit: bool
    database_path: Path | None = None
    state_root: Path | None = None
    parent_identity: tuple[int, int] | None = None


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


def viewer_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / TEMPLATE_RELATIVE_PATH


def inspect_canonical_viewer_status(
    *,
    path: Path,
    target: DatabaseTarget,
    current_snapshot: dict[str, Any] | None,
    compare_snapshot: bool,
    verify_template: bool,
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
        template = viewer_template_path().read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "repair_required"
    normalized_rendered = (
        rendered[:encoded_start]
        + TEMPLATE_PLACEHOLDER
        + rendered[encoded_end:]
    )
    return "current" if normalized_rendered == template else "repair_required"


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


def render_viewer_html(
    snapshot: dict[str, Any],
    *,
    template_path: Path | None = None,
) -> str:
    source_path = template_path or viewer_template_path()
    try:
        template = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ViewerError("internal_error", "viewer template could not be read") from exc

    if template.count(TEMPLATE_PLACEHOLDER) != 1:
        raise ViewerError(
            "internal_error",
            "viewer template must contain exactly one snapshot placeholder",
        )
    return template.replace(TEMPLATE_PLACEHOLDER, encode_snapshot(snapshot))


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


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


def directory_identity(path: Path) -> tuple[int, int]:
    try:
        details = path.stat()
    except OSError as exc:
        raise ViewerError(
            "output_path_invalid",
            "viewer output parent could not be inspected",
        ) from exc
    if not stat.S_ISDIR(details.st_mode):
        raise ViewerError(
            "output_path_invalid",
            "viewer output parent must be a directory",
        )
    return (details.st_dev, details.st_ino)


def validate_explicit_output_parent(target: ViewerOutputTarget) -> None:
    if target.parent_identity is None:
        raise ViewerError("internal_error", "explicit viewer parent identity is missing")
    if directory_identity(target.path.parent) != target.parent_identity:
        raise ViewerError(
            "output_path_invalid",
            "explicit viewer output parent changed after approval",
        )


def is_windows_device_path(value: str | os.PathLike[str]) -> bool:
    if os.name != "nt":
        return False
    raw = os.fspath(value).replace("/", "\\")
    return raw.startswith(("\\\\?\\", "\\\\.\\", "\\??\\"))


def is_windows_reserved_path(value: str | os.PathLike[str]) -> bool:
    if os.name != "nt":
        return False
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(
        f"{prefix}{number}"
        for prefix in ("COM", "LPT")
        for number in "123456789"
    )
    reserved.update(
        f"{prefix}{number}"
        for prefix in ("COM", "LPT")
        for number in "\u00b9\u00b2\u00b3"
    )
    for component in os.fspath(value).replace("/", "\\").split("\\"):
        basename = component.rstrip(" .").split(".", 1)[0].rstrip(" .").upper()
        if basename in reserved:
            return True
    return False


def has_windows_invalid_filename(value: str | os.PathLike[str]) -> bool:
    if os.name != "nt":
        return False
    filename = os.fspath(value).replace("/", "\\").rsplit("\\", 1)[-1]
    return any(character in '<>:"/\\|?*' or ord(character) < 32 for character in filename)


def path_is_reparse_point(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
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


def resolve_viewer_output_target(
    *,
    output: str | os.PathLike[str] | None,
    skill_root: Path,
    database_target: DatabaseTarget,
) -> ViewerOutputTarget:
    skill_root = skill_root.resolve()
    state_root = skill_root / "state"
    if output is None:
        path = default_viewer_output_path(
            skill_root,
            database_target.project.project_id,
        )
        validate_existing_output(path)
        validate_default_output_parent(path, state_root)
        validate_output_database_separation(path, database_target.db_path)
        return ViewerOutputTarget(
            path=path,
            explicit=False,
            database_path=database_target.db_path,
            state_root=state_root,
        )

    if (
        is_windows_device_path(output)
        or is_windows_reserved_path(output)
        or has_windows_invalid_filename(output)
    ):
        raise ViewerError(
            "output_path_invalid",
            "Windows device paths, reserved names, and invalid characters are not valid outputs",
        )
    lexical_path = Path(os.path.abspath(Path(output).expanduser()))
    if lexical_path.suffix.lower() not in {".html", ".htm"}:
        raise ViewerError(
            "output_path_invalid",
            "explicit viewer output must end in .html or .htm",
        )
    validate_existing_output(lexical_path)
    if not lexical_path.parent.exists():
        raise ViewerError(
            "output_parent_missing",
            "explicit viewer output parent does not exist",
        )
    if not lexical_path.parent.is_dir():
        raise ViewerError(
            "output_path_invalid",
            "explicit viewer output parent must be a directory",
        )

    try:
        resolved_path = lexical_path.parent.resolve(strict=True) / lexical_path.name
        canonical_repo = database_target.project.canonical_repo.resolve(strict=False)
        resolved_state_root = state_root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ViewerError("output_path_invalid", "viewer output path could not be resolved") from exc

    inside_repo = (
        path_is_within(lexical_path, canonical_repo)
        or path_is_within(resolved_path, canonical_repo)
        or existing_parent_is_within(resolved_path.parent, canonical_repo)
    )
    inside_state = (
        path_is_within(lexical_path, state_root)
        and path_is_within(resolved_path, resolved_state_root)
        and existing_parent_is_within(resolved_path.parent, resolved_state_root)
    )
    if inside_repo and not inside_state:
        raise ViewerError(
            "output_path_invalid",
            "viewer output inside the target project must stay under skill state",
        )
    validate_output_database_separation(resolved_path, database_target.db_path)
    return ViewerOutputTarget(
        path=resolved_path,
        explicit=True,
        database_path=database_target.db_path,
        state_root=state_root,
        parent_identity=directory_identity(resolved_path.parent),
    )


def write_viewer_html(
    target: ViewerOutputTarget,
    html: str,
) -> bool:
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        if target.explicit:
            validate_explicit_output_parent(target)
        else:
            target.path.parent.mkdir(parents=True, exist_ok=True)
            if target.state_root is None:
                raise ViewerError("internal_error", "default viewer state root is missing")
            validate_default_output_parent(target.path, target.state_root)
        validate_existing_output(target.path)
        if target.database_path is not None:
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
        if not target.explicit and target.state_root is not None:
            validate_default_output_parent(target.path, target.state_root)
        if target.explicit:
            validate_explicit_output_parent(target)
        validate_existing_output(target.path)
        if target.database_path is not None:
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
