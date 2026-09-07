"""Project binding snapshots, lineage, and atomic metadata updates."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from task_governance_tool.sqlite_connection import (
    StorageError,
    operational_sqlite_error,
)

if TYPE_CHECKING:
    from task_governance_tool.storage import DatabaseTarget


PROJECT_ID_HASH_LENGTH = 12


IDENTITY_SCHEMES = {"legacy_path_v1", "uuid_v1"}


BINDING_REASONS = {
    "legacy_migration",
    "fresh_setup",
    "confirmed_relocation",
}


LEGACY_PROJECT_ID_PATTERN = re.compile(
    rf"^[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{{{PROJECT_ID_HASH_LENGTH}}}$"
)


UUID_PROJECT_ID_PATTERN = re.compile(r"^tg_project_([0-9a-f]{32})$")


MANAGED_BACKUP_FILENAME_PATTERN = re.compile(
    r"^backups/taskgov-backup-v1_\d{8}T\d{6}Z_"
    r"[0-9a-f]{32}_r(?:[1-9]|1[0-9]|20)\.sqlite$"
)


CLEANUP_TEMP_ENTRY_PATTERNS = (
    re.compile(r"^\.taskgov-restore-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^backups/\.taskgov-backup-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^viewer/\.task-viewer-[a-z0-9_]{8}\.tmp$"),
    re.compile(r"^evidence/\.taskgov-evidence-index-[a-z0-9_]{8}\.tmp$"),
    re.compile(
        r"^evidence/bundles/\.taskgov-evidence-bundle-[a-z0-9_]{8}\.tmp$"
    ),
)


CLEANUP_EVIDENCE_BUNDLE_PATTERN = re.compile(
    r"^evidence/bundles/tg_completion_evidence_bundle_[0-9a-f]{16}\.json$"
)


CLEANUP_FIXED_ENTRIES = {
    "taskgov.sqlite",
    "backups/taskgov-backup.lock",
    "viewer/task-viewer.html",
    "viewer/taskgov-viewer.lock",
    "evidence/index.json",
    "evidence/taskgov-evidence.lock",
}


CLEANUP_INVENTORY_MAX_ENTRIES = 32


CLEANUP_INVENTORY_MAX_BYTES = 16_384


@dataclass(frozen=True)
class ProjectBindingState:
    project_id: str
    identity_scheme: str
    binding_generation: int
    canonical_path_hash: str
    display_name: str
    binding_reason: str
    binding_updated_at: str
    legacy_cleanup_pending: bool
    legacy_cleanup_inventory: str | None
    legacy_cleanup_fingerprint: str | None


@dataclass(frozen=True)
class ProjectPathBinding:
    project_id: str
    binding_generation: int
    previous_path_hash: str | None
    canonical_path_hash: str
    display_name: str
    reason: str
    confirmation_token_digest: str | None
    bound_at: str


def validate_project_display_name(value: str) -> str:
    from task_governance_tool.storage import (
        sanitize_project_display_name,
    )

    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or sanitize_project_display_name(value) != value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise StorageError("internal_error", "project display name is invalid")
    return value


def validate_identity_project_id(project_id: str, identity_scheme: str) -> str:
    if identity_scheme not in IDENTITY_SCHEMES:
        raise StorageError("internal_error", "project identity scheme is invalid")
    if identity_scheme == "legacy_path_v1":
        if not isinstance(project_id, str) or not LEGACY_PROJECT_ID_PATTERN.fullmatch(
            project_id
        ):
            raise StorageError("internal_error", "legacy project identity is invalid")
        return project_id
    if not isinstance(project_id, str):
        raise StorageError("internal_error", "UUID project identity is invalid")
    match = UUID_PROJECT_ID_PATTERN.fullmatch(project_id)
    if match is None:
        raise StorageError("internal_error", "UUID project identity is invalid")
    raw_uuid = match.group(1)
    if raw_uuid[12] != "4" or raw_uuid[16] not in {"8", "9", "a", "b"}:
        raise StorageError("internal_error", "UUID project identity is invalid")
    return project_id


def validate_binding_generation(value: object) -> int:
    from task_governance_tool.storage import (
        SQLITE_INT64_MAX,
    )

    if (
        type(value) is not int
        or not 1 <= value <= SQLITE_INT64_MAX
    ):
        raise StorageError("internal_error", "project binding generation is invalid")
    return value


def _recognized_cleanup_entry(value: str) -> bool:
    return (
        value in CLEANUP_FIXED_ENTRIES
        or MANAGED_BACKUP_FILENAME_PATTERN.fullmatch(value) is not None
        or CLEANUP_EVIDENCE_BUNDLE_PATTERN.fullmatch(value) is not None
        or any(pattern.fullmatch(value) is not None for pattern in CLEANUP_TEMP_ENTRY_PATTERNS)
    )


def validate_cleanup_inventory(
    inventory: str,
    fingerprint: str,
) -> tuple[str, str]:
    from task_governance_tool.storage import (
        LOWER_HEX_64_PATTERN,
        validate_lower_hex_64,
    )

    if (
        not isinstance(inventory, str)
        or not 1 <= len(inventory.encode("utf-8")) <= CLEANUP_INVENTORY_MAX_BYTES
        or not inventory.isascii()
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            inventory,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeError, ValueError) as exc:
        raise StorageError(
            "internal_error",
            "legacy cleanup inventory is invalid",
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"entries", "v"}
        or type(payload.get("v")) is not int
        or payload.get("v") != 1
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    entries = payload.get("entries")
    if (
        not isinstance(entries, list)
        or not 1 <= len(entries) <= CLEANUP_INVENTORY_MAX_ENTRIES
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    names: list[str] = []
    backup_count = 0
    temporary_counts = [0] * len(CLEANUP_TEMP_ENTRY_PATTERNS)
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "kind",
            "name",
            "sha256",
            "size",
        }:
            raise StorageError(
                "internal_error",
                "legacy cleanup inventory is invalid",
            )
        name = entry.get("name")
        size = entry.get("size")
        if (
            entry.get("kind") != "file"
            or not isinstance(name, str)
            or not _recognized_cleanup_entry(name)
            or not isinstance(entry.get("sha256"), str)
            or LOWER_HEX_64_PATTERN.fullmatch(entry["sha256"]) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise StorageError(
                "internal_error",
                "legacy cleanup inventory is invalid",
            )
        names.append(name)
        if MANAGED_BACKUP_FILENAME_PATTERN.fullmatch(name) is not None:
            backup_count += 1
        for index, pattern in enumerate(CLEANUP_TEMP_ENTRY_PATTERNS):
            if pattern.fullmatch(name) is not None:
                temporary_counts[index] += 1
    if (
        len(set(names)) != len(names)
        or names != sorted(names, key=lambda value: value.encode("utf-8"))
        or backup_count > 21
        or any(count > 1 for count in temporary_counts)
    ):
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if inventory != canonical:
        raise StorageError("internal_error", "legacy cleanup inventory is invalid")
    validate_lower_hex_64(fingerprint, field="legacy cleanup fingerprint")
    if hashlib.sha256(inventory.encode("ascii")).hexdigest() != fingerprint:
        raise StorageError("internal_error", "legacy cleanup fingerprint is invalid")
    return inventory, fingerprint


def _read_project_binding_snapshot(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str | None = None,
) -> tuple[ProjectBindingState, tuple[ProjectPathBinding, ...]]:
    """Validate and return the one schema-v14 current binding and its lineage."""
    from task_governance_tool.storage import (
        _unreadable_project_state,
        validate_lower_hex_64,
        validate_utc_timestamp,
    )

    try:
        rows = connection.execute(
            """
            SELECT project_id, canonical_path_hash, display_name, created_at,
                   updated_at, identity_scheme, binding_generation,
                   binding_reason, binding_updated_at, legacy_cleanup_pending,
                   legacy_cleanup_inventory, legacy_cleanup_fingerprint
              FROM project_meta
             ORDER BY project_id
            """
        ).fetchall()
        if len(rows) != 1:
            raise _unreadable_project_state()
        row = rows[0]
        project_id = str(row["project_id"])
        identity_scheme = str(row["identity_scheme"])
        validate_identity_project_id(project_id, identity_scheme)
        if expected_project_id is not None and project_id != expected_project_id:
            raise StorageError(
                "project_mismatch",
                "task database belongs to a different project",
            )
        canonical_hash = validate_lower_hex_64(
            str(row["canonical_path_hash"]),
            field="canonical path hash",
        )
        display_name = validate_project_display_name(str(row["display_name"]))
        validate_utc_timestamp(str(row["created_at"]), field="project creation time")
        validate_utc_timestamp(str(row["updated_at"]), field="project update time")
        binding_generation = validate_binding_generation(row["binding_generation"])
        binding_reason = str(row["binding_reason"])
        if binding_reason not in BINDING_REASONS:
            raise StorageError("internal_error", "project binding reason is invalid")
        binding_updated_at = validate_utc_timestamp(
            str(row["binding_updated_at"]),
            field="project binding time",
        )

        cleanup_pending = int(row["legacy_cleanup_pending"])
        cleanup_inventory = (
            str(row["legacy_cleanup_inventory"])
            if row["legacy_cleanup_inventory"] is not None
            else None
        )
        cleanup_fingerprint = (
            str(row["legacy_cleanup_fingerprint"])
            if row["legacy_cleanup_fingerprint"] is not None
            else None
        )
        if cleanup_pending == 0:
            if cleanup_inventory is not None or cleanup_fingerprint is not None:
                raise StorageError(
                    "internal_error",
                    "legacy cleanup metadata is invalid",
                )
        elif cleanup_pending == 1:
            if cleanup_inventory is None or cleanup_fingerprint is None:
                raise StorageError(
                    "internal_error",
                    "legacy cleanup metadata is invalid",
                )
            validate_cleanup_inventory(cleanup_inventory, cleanup_fingerprint)
        else:
            raise StorageError(
                "internal_error",
                "legacy cleanup metadata is invalid",
            )

        history_rows = connection.execute(
            """
            SELECT project_id, binding_generation, previous_path_hash,
                   canonical_path_hash, display_name, reason,
                   confirmation_token_digest, bound_at
              FROM project_path_binding_history
             WHERE project_id = ?
             ORDER BY binding_generation
            """,
            (project_id,),
        ).fetchall()
        history_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM project_path_binding_history"
            ).fetchone()[0]
        )
        if (
            len(history_rows) != binding_generation
            or history_count != len(history_rows)
        ):
            raise StorageError("internal_error", "project binding history is invalid")

        previous_hash: str | None = None
        history: list[ProjectPathBinding] = []
        for expected_generation, history_row in enumerate(history_rows, start=1):
            history_project_id = str(history_row["project_id"])
            generation = validate_binding_generation(
                history_row["binding_generation"]
            )
            history_previous_hash = (
                validate_lower_hex_64(
                    str(history_row["previous_path_hash"]),
                    field="previous canonical path hash",
                )
                if history_row["previous_path_hash"] is not None
                else None
            )
            history_hash = validate_lower_hex_64(
                str(history_row["canonical_path_hash"]),
                field="canonical path hash",
            )
            history_display = validate_project_display_name(
                str(history_row["display_name"])
            )
            reason = str(history_row["reason"])
            token_digest = (
                validate_lower_hex_64(
                    str(history_row["confirmation_token_digest"]),
                    field="confirmation token digest",
                )
                if history_row["confirmation_token_digest"] is not None
                else None
            )
            bound_at = validate_utc_timestamp(
                str(history_row["bound_at"]),
                field="project binding time",
            )
            if (
                history_project_id != project_id
                or generation != expected_generation
                or history_previous_hash != previous_hash
            ):
                raise StorageError(
                    "internal_error",
                    "project binding history is invalid",
                )
            expected_reason = (
                "legacy_migration"
                if identity_scheme == "legacy_path_v1"
                else "fresh_setup"
            )
            if generation == 1:
                if reason != expected_reason or token_digest is not None:
                    raise StorageError(
                        "internal_error",
                        "project binding history is invalid",
                    )
            elif reason != "confirmed_relocation" or token_digest is None:
                raise StorageError(
                    "internal_error",
                    "project binding history is invalid",
                )
            history.append(ProjectPathBinding(
                project_id=history_project_id,
                binding_generation=generation,
                previous_path_hash=history_previous_hash,
                canonical_path_hash=history_hash,
                display_name=history_display,
                reason=reason,
                confirmation_token_digest=token_digest,
                bound_at=bound_at,
            ))
            previous_hash = history_hash

        history_head = history[-1] if history else None
        if history_head is None or (
            history_head.binding_generation != binding_generation
            or history_head.canonical_path_hash != canonical_hash
            or history_head.display_name != display_name
            or history_head.reason != binding_reason
            or history_head.bound_at != binding_updated_at
        ):
            raise StorageError("internal_error", "project binding history is invalid")
        state = ProjectBindingState(
            project_id=project_id,
            identity_scheme=identity_scheme,
            binding_generation=binding_generation,
            canonical_path_hash=canonical_hash,
            display_name=display_name,
            binding_reason=binding_reason,
            binding_updated_at=binding_updated_at,
            legacy_cleanup_pending=bool(cleanup_pending),
            legacy_cleanup_inventory=cleanup_inventory,
            legacy_cleanup_fingerprint=cleanup_fingerprint,
        )
        return state, tuple(history)
    except StorageError as exc:
        if exc.code == "project_mismatch":
            raise
        raise _unreadable_project_state() from exc
    except (TypeError, ValueError, sqlite3.Error) as exc:
        raise _unreadable_project_state() from exc


def read_project_binding_state(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str | None = None,
) -> ProjectBindingState:
    """Validate and return the one schema-v14 current binding."""

    return _read_project_binding_snapshot(
        connection,
        expected_project_id=expected_project_id,
    )[0]


def read_project_binding_history(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str | None = None,
) -> tuple[ProjectPathBinding, ...]:
    """Validate and return the complete schema-v14 binding lineage."""

    return _read_project_binding_snapshot(
        connection,
        expected_project_id=expected_project_id,
    )[1]


def _validate_target_binding(
    binding: ProjectBindingState,
    target: DatabaseTarget,
) -> None:
    from task_governance_tool.storage import (
        _unreadable_project_state,
        validate_lower_hex_64,
    )

    existing_project_id = binding.project_id
    if existing_project_id != target.project.project_id:
        raise StorageError(
            "project_mismatch",
            "task database belongs to a different project",
        )
    if (
        (target.binding_path_hash is None)
        != (target.binding_generation is None)
    ):
        raise StorageError(
            "internal_error",
            "database target binding basis is incomplete",
        )
    if target.binding_path_hash is not None:
        try:
            expected_hash = validate_lower_hex_64(
                target.binding_path_hash,
                field="database target binding hash",
            )
            expected_generation = validate_binding_generation(
                target.binding_generation
            )
        except StorageError as exc:
            raise StorageError(
                "internal_error",
                "database target binding basis is invalid",
            ) from exc
        if (
            binding.canonical_path_hash != expected_hash
            or binding.binding_generation != expected_generation
        ):
            raise _unreadable_project_state()


def compare_and_swap_project_binding(
    target: DatabaseTarget,
    *,
    project_id: str,
    identity_scheme: str,
    expected_generation: int,
    expected_old_hash: str,
    new_hash: str,
    new_display_name: str,
    reason: str,
    confirmation_token_digest: str,
    bound_at: str,
    fail_stage: str | None = None,
) -> ProjectBindingState:
    """Append one confirmed binding and advance Viewer source state atomically."""
    from task_governance_tool.storage import (
        SQLITE_INT64_MAX,
        _unreadable_project_state,
        begin_initialized_write,
        connect_initialized,
        sanitize_project_display_name,
        validate_lower_hex_64,
        validate_utc_timestamp,
        validate_viewer_generation,
    )

    validate_identity_project_id(project_id, identity_scheme)
    validate_binding_generation(expected_generation)
    validate_lower_hex_64(expected_old_hash, field="previous canonical path hash")
    validate_lower_hex_64(new_hash, field="canonical path hash")
    display_name = validate_project_display_name(new_display_name)
    if expected_old_hash == new_hash:
        raise StorageError("internal_error", "project binding did not change")
    if reason != "confirmed_relocation":
        raise StorageError("internal_error", "project binding reason is invalid")
    token_digest = validate_lower_hex_64(
        confirmation_token_digest,
        field="confirmation token digest",
    )
    timestamp = validate_utc_timestamp(bound_at, field="project binding time")
    if (
        target.project.project_id != project_id
        or target.project.canonical_path_hash != new_hash
        or sanitize_project_display_name(target.project.display_name) != display_name
    ):
        raise StorageError("internal_error", "project binding target is invalid")

    try:
        opened_connection = connect_initialized(target)
    except StorageError as exc:
        if exc.code in {"internal_error", "migration_required"}:
            raise _unreadable_project_state() from exc
        raise
    with closing(opened_connection) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if (
                current.identity_scheme != identity_scheme
                or current.binding_generation != expected_generation
                or current.canonical_path_hash != expected_old_hash
            ):
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            if current.binding_generation >= SQLITE_INT64_MAX:
                raise _unreadable_project_state()
            viewer_row = connection.execute(
                """
                SELECT source_generation
                  FROM viewer_maintenance_state
                 WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if viewer_row is None:
                raise _unreadable_project_state()
            try:
                viewer_generation = validate_viewer_generation(
                    viewer_row["source_generation"],
                    field="Viewer source generation",
                )
            except StorageError as exc:
                raise _unreadable_project_state() from exc
            if viewer_generation >= SQLITE_INT64_MAX:
                raise _unreadable_project_state()

            next_generation = current.binding_generation + 1
            connection.execute(
                """
                INSERT INTO project_path_binding_history(
                  project_id, binding_generation, previous_path_hash,
                  canonical_path_hash, display_name, reason,
                  confirmation_token_digest, bound_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    next_generation,
                    expected_old_hash,
                    new_hash,
                    display_name,
                    reason,
                    token_digest,
                    timestamp,
                ),
            )
            if fail_stage == "after_history":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )

            cursor = connection.execute(
                """
                UPDATE project_meta
                   SET canonical_path_hash = ?,
                       display_name = ?,
                       binding_generation = ?,
                       binding_reason = ?,
                       binding_updated_at = ?,
                       updated_at = ?
                 WHERE project_id = ?
                   AND identity_scheme = ?
                   AND binding_generation = ?
                   AND canonical_path_hash = ?
                """,
                (
                    new_hash,
                    display_name,
                    next_generation,
                    reason,
                    timestamp,
                    timestamp,
                    project_id,
                    identity_scheme,
                    expected_generation,
                    expected_old_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            if fail_stage == "after_current":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )

            cursor = connection.execute(
                """
                UPDATE viewer_maintenance_state
                   SET source_generation = source_generation + 1
                 WHERE project_id = ?
                   AND source_generation < ?
                """,
                (project_id, SQLITE_INT64_MAX),
            )
            if cursor.rowcount != 1:
                raise _unreadable_project_state()
            if fail_stage == "after_viewer":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )

            updated = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if fail_stage == "before_commit":
                raise StorageError(
                    "internal_error",
                    "injected project binding failure",
                )
            connection.commit()
            return updated
        except StorageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise operational_sqlite_error(
                exc,
                fallback_message="project state could not be updated safely",
            ) from exc
        except Exception:
            connection.rollback()
            raise


def set_legacy_cleanup_pending(
    target: DatabaseTarget,
    *,
    project_id: str,
    expected_identity_scheme: str,
    expected_generation: int,
    expected_path_hash: str,
    inventory: str,
    fingerprint: str,
) -> ProjectBindingState:
    """Persist one canonical cleanup plan against an exact binding basis."""
    from task_governance_tool.storage import (
        begin_initialized_write,
        connect_initialized,
        validate_lower_hex_64,
    )


    validate_identity_project_id(project_id, expected_identity_scheme)
    validate_binding_generation(expected_generation)
    validate_lower_hex_64(expected_path_hash, field="canonical path hash")
    validate_cleanup_inventory(inventory, fingerprint)
    if target.project.project_id != project_id:
        raise StorageError("internal_error", "project binding target is invalid")
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if (
                current.identity_scheme != expected_identity_scheme
                or current.binding_generation != expected_generation
                or current.canonical_path_hash != expected_path_hash
            ):
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            if current.legacy_cleanup_pending:
                if (
                    current.legacy_cleanup_inventory != inventory
                    or current.legacy_cleanup_fingerprint != fingerprint
                ):
                    raise StorageError(
                        "project_binding_stale",
                        "project binding state changed",
                    )
                connection.rollback()
                return current
            cursor = connection.execute(
                """
                UPDATE project_meta
                   SET legacy_cleanup_pending = 1,
                       legacy_cleanup_inventory = ?,
                       legacy_cleanup_fingerprint = ?
                 WHERE project_id = ?
                   AND identity_scheme = ?
                   AND binding_generation = ?
                   AND canonical_path_hash = ?
                   AND legacy_cleanup_pending = 0
                """,
                (
                    inventory,
                    fingerprint,
                    project_id,
                    expected_identity_scheme,
                    expected_generation,
                    expected_path_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            updated = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            connection.commit()
            return updated
        except StorageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise operational_sqlite_error(
                exc,
                fallback_message="project state could not be updated safely",
            ) from exc


def clear_legacy_cleanup_pending(
    target: DatabaseTarget,
    *,
    project_id: str,
    expected_identity_scheme: str,
    expected_generation: int,
    expected_path_hash: str,
    expected_inventory_fingerprint: str,
) -> ProjectBindingState:
    """Clear only the persisted cleanup plan proven complete by setup."""
    from task_governance_tool.storage import (
        begin_initialized_write,
        connect_initialized,
        validate_lower_hex_64,
    )


    validate_identity_project_id(project_id, expected_identity_scheme)
    validate_binding_generation(expected_generation)
    validate_lower_hex_64(expected_path_hash, field="canonical path hash")
    validate_lower_hex_64(
        expected_inventory_fingerprint,
        field="legacy cleanup fingerprint",
    )
    if target.project.project_id != project_id:
        raise StorageError("internal_error", "project binding target is invalid")
    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            current = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            if (
                current.identity_scheme != expected_identity_scheme
                or current.binding_generation != expected_generation
                or current.canonical_path_hash != expected_path_hash
                or not current.legacy_cleanup_pending
                or current.legacy_cleanup_inventory is None
                or current.legacy_cleanup_fingerprint
                != expected_inventory_fingerprint
            ):
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            validate_cleanup_inventory(
                current.legacy_cleanup_inventory,
                expected_inventory_fingerprint,
            )
            cursor = connection.execute(
                """
                UPDATE project_meta
                   SET legacy_cleanup_pending = 0,
                       legacy_cleanup_inventory = NULL,
                       legacy_cleanup_fingerprint = NULL
                 WHERE project_id = ?
                   AND identity_scheme = ?
                   AND binding_generation = ?
                   AND canonical_path_hash = ?
                   AND legacy_cleanup_pending = 1
                   AND legacy_cleanup_fingerprint = ?
                """,
                (
                    project_id,
                    expected_identity_scheme,
                    expected_generation,
                    expected_path_hash,
                    expected_inventory_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                raise StorageError(
                    "project_binding_stale",
                    "project binding state changed",
                )
            updated = read_project_binding_state(
                connection,
                expected_project_id=project_id,
            )
            connection.commit()
            return updated
        except StorageError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise operational_sqlite_error(
                exc,
                fallback_message="project state could not be updated safely",
            ) from exc
