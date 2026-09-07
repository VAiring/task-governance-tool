"""Repository-facing stored Task batch and Contract relationship validation.

Use the caller's connection and selected rows without changing their scope.
The single-Task reader begins and rolls back only its own read transaction;
connection lifetime and existing caller transactions remain caller-owned.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from task_governance_tool.completion import (
    CompletionEvidenceError,
    FULL_GIT_OBJECT_ID,
    validate_evidence_matrix,
)
from task_governance_tool.ordering import canonical_lane
from task_governance_tool.storage import (
    PRIVATE_SCHEMA22_VERSION,
    SCHEMA_VERSION,
    StorageError,
    current_schema_version,
    stored_task_sqlite_error,
    stored_task_verification_limit,
    validate_selected_task_authority_storage,
    validate_utc_timestamp,
)
from task_governance_tool.task_values import (
    KINDS,
    PRIORITIES,
    REVIEW_TIERS,
    SQLITE_INT64_MAX,
    SQLITE_INT64_MIN,
    STATUSES,
    TEXT_LIMITS,
    TaskValidationError,
    reject_private_or_raw_content,
)


@dataclass(frozen=True)
class StoredTaskSchemaCapabilities:
    """Source-schema facts used by the shared stored Task validator."""

    source_schema_version: int
    verification_limit: int
    has_completion_commit: bool
    has_pause_reason: bool
    has_completion_evidence: bool
    has_review_target: bool
    has_review_target_base: bool
    has_contract_revision: bool
    has_completion_history_coverage: bool


@dataclass(frozen=True)
class StoredTaskValidationResult:
    """Private facts retained from one bounded stored-Task validation."""

    verification_rejection: str | None = None
    verification_rejected_task_ids: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        compare=False,
    )
    current_contract_rows: dict[str, sqlite3.Row | None] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )


_AUTHORITY_PRIVACY_REUSE_FIELDS = frozenset(
    {"title", "description", "verification"}
)


def _stored_task_privacy_success_cache(
    prevalidated_privacy_successes: frozenset[tuple[str, str]] | None,
) -> set[tuple[str, str]]:
    """Copy one internal same-call authority proof into a local cache."""

    if prevalidated_privacy_successes is None:
        return set()
    if type(prevalidated_privacy_successes) is not frozenset:
        raise _stored_task_unreadable()
    for item in prevalidated_privacy_successes:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or item[0] not in _AUTHORITY_PRIVACY_REUSE_FIELDS
            or type(item[1]) is not str
        ):
            raise _stored_task_unreadable()
    return set(prevalidated_privacy_successes)


def stored_task_schema_capabilities(
    source_schema_version: object,
) -> StoredTaskSchemaCapabilities:
    """Resolve one immutable source-schema capability without reading SQLite."""

    if (
        type(source_schema_version) is not int
        or (
            not 1 <= source_schema_version <= SCHEMA_VERSION
            and source_schema_version != PRIVATE_SCHEMA22_VERSION
        )
    ):
        raise _stored_task_unreadable()
    try:
        verification_limit = stored_task_verification_limit(
            source_schema_version
        )
    except StorageError as exc:
        raise _stored_task_unreadable() from exc
    return StoredTaskSchemaCapabilities(
        source_schema_version=source_schema_version,
        verification_limit=verification_limit,
        has_completion_commit=source_schema_version >= 2,
        has_pause_reason=source_schema_version >= 3,
        has_completion_evidence=source_schema_version >= 4,
        has_review_target=source_schema_version >= 5,
        has_review_target_base=source_schema_version >= 6,
        has_contract_revision=source_schema_version >= 8,
        has_completion_history_coverage=source_schema_version >= 15,
    )


def _stored_task_unreadable() -> StorageError:
    return StorageError(
        "project_state_unreadable",
        "project state could not be read safely",
    )


def fetch_stored_task_rows(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] | list[Any] = (),
) -> list[sqlite3.Row]:
    """Fetch a Task batch while preserving busy and sanitizing decode faults."""

    try:
        return connection.execute(query, parameters).fetchall()
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc


def fetch_stored_task_row(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] | list[Any] = (),
) -> sqlite3.Row | None:
    """Fetch one Task row with the same storage-fault mapping as batches."""

    try:
        return connection.execute(query, parameters).fetchone()
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc


def _stored_value(row: sqlite3.Row | dict[str, Any], field: str) -> object:
    try:
        if field not in row.keys():
            raise KeyError(field)
        return row[field]
    except (IndexError, KeyError, TypeError, AttributeError) as exc:
        raise _stored_task_unreadable() from exc


def _stored_text(
    row: sqlite3.Row | dict[str, Any],
    field: str,
    *,
    required: bool = False,
    limit: int | None = None,
    verification_limit: int | None = None,
    privacy_field: str | None = None,
    privacy_success_cache: set[tuple[str, str]] | None = None,
) -> tuple[str, str | None]:
    value = _stored_value(row, field)
    if type(value) is not str:
        raise _stored_task_unreadable()
    if required and not value.strip():
        raise _stored_task_unreadable()
    verification_rejection: str | None = None
    resolved_privacy_field = privacy_field or field
    privacy_key = (resolved_privacy_field, value)
    if privacy_success_cache is None or privacy_key not in privacy_success_cache:
        try:
            reject_private_or_raw_content(resolved_privacy_field, value)
        except TaskValidationError as exc:
            if field == "verification" and exc.code == "privacy_rejected":
                verification_rejection = "privacy"
            else:
                raise _stored_task_unreadable() from exc
        else:
            if privacy_success_cache is not None:
                privacy_success_cache.add(privacy_key)
    if limit is not None and len(value) > limit:
        raise _stored_task_unreadable()
    if (
        verification_limit is not None
        and len(value) > verification_limit
        and verification_rejection != "privacy"
    ):
        verification_rejection = "capacity"
    return value, verification_rejection


def _stored_integer(
    row: sqlite3.Row | dict[str, Any],
    field: str,
    *,
    nullable: bool = False,
    minimum: int = SQLITE_INT64_MIN,
    maximum: int = SQLITE_INT64_MAX,
) -> int | None:
    value = _stored_value(row, field)
    if value is None and nullable:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise _stored_task_unreadable()
    return value


def _stored_timestamp(
    row: sqlite3.Row | dict[str, Any],
    field: str,
    *,
    nullable: bool = False,
) -> str | None:
    value = _stored_value(row, field)
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise _stored_task_unreadable()
    try:
        return validate_utc_timestamp(value, field=field)
    except StorageError as exc:
        raise _stored_task_unreadable() from exc


def _validate_stored_completion_evidence(
    row: sqlite3.Row | dict[str, Any],
    privacy_success_cache: set[tuple[str, str]],
) -> None:
    task = {
        "completion_evidence_kind": _stored_text(
            row,
            "completion_evidence_kind",
            privacy_success_cache=privacy_success_cache,
        )[0],
        "completion_evidence_revision": _stored_text(
            row,
            "completion_evidence_revision",
            limit=TEXT_LIMITS["completion_revision"],
            privacy_field="completion_revision",
            privacy_success_cache=privacy_success_cache,
        )[0],
        "completion_evidence_reason": _stored_text(
            row,
            "completion_evidence_reason",
            limit=TEXT_LIMITS["completion_evidence_reason"],
            privacy_success_cache=privacy_success_cache,
        )[0],
        "external_revision_approved": _stored_integer(
            row,
            "external_revision_approved",
            minimum=0,
            maximum=1,
        ),
        "completion_commit_required": _stored_integer(
            row,
            "completion_commit_required",
            minimum=0,
            maximum=1,
        ),
        "completion_commit_hash": _stored_text(
            row,
            "completion_commit_hash",
            limit=TEXT_LIMITS["completion_revision"],
            privacy_field="completion_revision",
            privacy_success_cache=privacy_success_cache,
        )[0],
    }
    kind = str(task["completion_evidence_kind"])
    revision = str(task["completion_evidence_revision"])
    legacy_hash = str(task["completion_commit_hash"])
    if kind == "git_commit" and set(revision) == {"0"}:
        raise _stored_task_unreadable()
    if kind == "legacy_unverified" and not revision:
        raise _stored_task_unreadable()
    try:
        validate_evidence_matrix(task, allow_legacy=True)
    except (CompletionEvidenceError, TypeError, ValueError, OverflowError) as exc:
        raise _stored_task_unreadable() from exc


def _validate_stored_review_target(
    row: sqlite3.Row | dict[str, Any],
    capabilities: StoredTaskSchemaCapabilities,
    privacy_success_cache: set[tuple[str, str]],
) -> None:
    kind = _stored_text(
        row,
        "review_target_kind",
        privacy_success_cache=privacy_success_cache,
    )[0]
    value = _stored_text(
        row,
        "review_target_value",
        limit=TEXT_LIMITS["review_target_value"],
        privacy_success_cache=privacy_success_cache,
    )[0]
    generation = _stored_integer(
        row,
        "review_target_generation",
        minimum=0,
    )
    base_revision = (
        _stored_text(
            row,
            "review_target_base_revision",
            limit=TEXT_LIMITS["review_target_value"],
            privacy_success_cache=privacy_success_cache,
        )[0]
        if capabilities.has_review_target_base
        else ""
    )
    if not kind:
        if value or base_revision:
            raise _stored_task_unreadable()
        return
    if generation is None or generation <= 0 or not value or value != value.strip():
        raise _stored_task_unreadable()
    if kind == "git_commit":
        if (
            FULL_GIT_OBJECT_ID.fullmatch(value) is None
            or set(value) == {"0"}
            or base_revision
        ):
            raise _stored_task_unreadable()
    elif kind == "diff_fingerprint":
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None or base_revision:
            raise _stored_task_unreadable()
    elif kind == "external_revision":
        if base_revision:
            raise _stored_task_unreadable()
    elif kind == "git_snapshot":
        if (
            not capabilities.has_review_target_base
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
            or FULL_GIT_OBJECT_ID.fullmatch(base_revision) is None
            or set(base_revision) == {"0"}
        ):
            raise _stored_task_unreadable()
    else:
        raise _stored_task_unreadable()


def _validate_stored_task_row(
    row: sqlite3.Row | dict[str, Any],
    *,
    capabilities: StoredTaskSchemaCapabilities,
    expected_project_id: str,
    privacy_success_cache: set[tuple[str, str]],
) -> str | None:
    task_id = _stored_text(
        row,
        "task_id",
        required=True,
        limit=128,
        privacy_success_cache=privacy_success_cache,
    )[0]
    if task_id != task_id.strip():
        raise _stored_task_unreadable()
    project_id = _stored_text(
        row,
        "project_id",
        required=True,
        privacy_success_cache=privacy_success_cache,
    )[0]
    if project_id != expected_project_id:
        raise _stored_task_unreadable()
    _stored_text(
        row,
        "title",
        required=True,
        limit=TEXT_LIMITS["title"],
        privacy_success_cache=privacy_success_cache,
    )
    _stored_text(
        row,
        "description",
        limit=TEXT_LIMITS["description"],
        privacy_success_cache=privacy_success_cache,
    )
    kind = _stored_text(
        row,
        "kind",
        privacy_success_cache=privacy_success_cache,
    )[0]
    lane = _stored_text(
        row,
        "lane",
        privacy_success_cache=privacy_success_cache,
    )[0]
    lane_order = _stored_integer(row, "lane_order", nullable=True)
    priority = _stored_text(
        row,
        "priority",
        privacy_success_cache=privacy_success_cache,
    )[0]
    status = _stored_text(
        row,
        "status",
        privacy_success_cache=privacy_success_cache,
    )[0]
    blocked_reason = _stored_text(
        row,
        "blocked_reason",
        privacy_success_cache=privacy_success_cache,
    )[0]
    pause_reason = (
        _stored_text(
            row,
            "pause_reason",
            limit=TEXT_LIMITS["pause_reason"],
            privacy_success_cache=privacy_success_cache,
        )[0]
        if capabilities.has_pause_reason
        else ""
    )
    review_tier = _stored_integer(
        row,
        "review_tier",
        minimum=0,
        maximum=2,
    )
    _, verification_rejection = _stored_text(
        row,
        "verification",
        verification_limit=capabilities.verification_limit,
        privacy_success_cache=privacy_success_cache,
    )
    _stored_text(
        row,
        "tags",
        limit=TEXT_LIMITS["tags"],
        privacy_success_cache=privacy_success_cache,
    )
    created_at = _stored_timestamp(row, "created_at")
    updated_at = _stored_timestamp(row, "updated_at")
    completed_at = _stored_timestamp(row, "completed_at", nullable=True)

    allowed_statuses = STATUSES if capabilities.has_pause_reason else tuple(
        value for value in STATUSES if value != "paused"
    )
    if (
        kind not in KINDS
        or priority not in PRIORITIES
        or status not in allowed_statuses
        or review_tier not in REVIEW_TIERS
        or lane != canonical_lane(lane)
        or (kind == "sequential" and (not lane or lane_order is None))
        or (status == "blocked" and not blocked_reason.strip())
        or (
            capabilities.has_pause_reason
            and (
                (status == "paused" and not pause_reason.strip())
                or (status != "paused" and pause_reason != "")
            )
        )
        or (completed_at is None) != (status != "done")
        or created_at is None
        or updated_at is None
    ):
        raise _stored_task_unreadable()

    if capabilities.has_completion_commit:
        _stored_integer(
            row,
            "completion_commit_required",
            minimum=0,
            maximum=1,
        )
        _stored_text(
            row,
            "completion_commit_hash",
            limit=TEXT_LIMITS["completion_revision"],
            privacy_field="completion_revision",
            privacy_success_cache=privacy_success_cache,
        )
    if capabilities.has_completion_evidence:
        _validate_stored_completion_evidence(row, privacy_success_cache)
    if capabilities.has_review_target:
        _validate_stored_review_target(
            row,
            capabilities,
            privacy_success_cache,
        )
    if capabilities.has_contract_revision:
        _stored_integer(row, "current_contract_revision", minimum=0)
    if capabilities.has_completion_history_coverage:
        if _stored_text(
            row,
            "completion_history_coverage",
            privacy_success_cache=privacy_success_cache,
        )[0] not in {
            "legacy_unknown",
            "complete",
        }:
            raise _stored_task_unreadable()
    return verification_rejection


def _validate_stored_contract_relationships(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    *,
    expected_project_id: str,
    privacy_success_cache: set[tuple[str, str]],
) -> dict[str, sqlite3.Row | None]:
    """Validate current Contract pointers with one selected-batch read."""

    pointers: dict[str, int] = {}
    for row in rows:
        task_id = _stored_text(
            row,
            "task_id",
            required=True,
            limit=128,
            privacy_success_cache=privacy_success_cache,
        )[0]
        pointer = _stored_integer(row, "current_contract_revision", minimum=0)
        if pointer is None or task_id in pointers:
            raise _stored_task_unreadable()
        pointers[task_id] = pointer
    if not pointers:
        return {}

    selected_ids = json.dumps(
        list(pointers),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    relationship_rows = fetch_stored_task_rows(
        connection,
        """
        WITH selected_task_ids(value) AS (
            SELECT value FROM json_each(?)
        ),
        selected_storage_keys(value) AS (
            SELECT value FROM selected_task_ids
            UNION ALL
            SELECT CAST(value AS BLOB) FROM selected_task_ids
        )
        SELECT *
          FROM task_contract_revisions
         WHERE task_id IN (SELECT value FROM selected_storage_keys)
         ORDER BY task_id, revision
        """,
        (selected_ids,),
    )
    revisions: dict[str, dict[int, sqlite3.Row]] = {
        task_id: {} for task_id in pointers
    }
    for relationship in relationship_rows:
        project_id = _stored_text(
            relationship,
            "project_id",
            required=True,
            privacy_success_cache=privacy_success_cache,
        )[0]
        task_id = _stored_text(
            relationship,
            "task_id",
            required=True,
            limit=128,
            privacy_success_cache=privacy_success_cache,
        )[0]
        revision = _stored_integer(
            relationship,
            "revision",
            minimum=1,
        )
        if (
            revision is None
            or project_id != expected_project_id
            or task_id not in pointers
            or pointers[task_id] == 0
            or revision in revisions[task_id]
        ):
            raise _stored_task_unreadable()
        revisions[task_id][revision] = relationship

    for task_id, pointer in pointers.items():
        related = revisions[task_id]
        if pointer == 0:
            if related:
                raise _stored_task_unreadable()
        elif not related or pointer not in related or pointer != max(related):
            raise _stored_task_unreadable()
    return {
        task_id: (revisions[task_id].get(pointer) if pointer > 0 else None)
        for task_id, pointer in pointers.items()
    }


def validate_stored_task_rows(
    rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    *,
    connection: sqlite3.Connection | None = None,
    source_schema_version: object,
    expected_project_id: str,
    verification_rejection_is_local: bool = False,
    _prevalidated_privacy_successes: frozenset[tuple[str, str]] | None = None,
) -> StoredTaskValidationResult:
    """Validate one loaded Task batch before projection or derived use.

    No stored value is coerced, normalized, rewritten, or included in an error.
    The recovery-only flag preserves M21.4B's candidate-local exception for
    verification privacy/capacity while every structural fault stays fatal.
    The private authority seed is copied, shape-checked, and consumed only by
    this call's ordinary field-bound privacy cache.
    """

    if type(expected_project_id) is not str or not expected_project_id:
        raise _stored_task_unreadable()
    capabilities = stored_task_schema_capabilities(source_schema_version)
    privacy_success_cache = _stored_task_privacy_success_cache(
        _prevalidated_privacy_successes
    )
    rejection: str | None = None
    rejected_task_ids: set[str] = set()
    for row in rows:
        row_rejection = _validate_stored_task_row(
            row,
            capabilities=capabilities,
            expected_project_id=expected_project_id,
            privacy_success_cache=privacy_success_cache,
        )
        if row_rejection == "privacy":
            rejection = "privacy"
        elif row_rejection == "capacity" and rejection is None:
            rejection = "capacity"
        if row_rejection is not None:
            rejected_task_ids.add(str(row["task_id"]))
    current_contract_rows: dict[str, sqlite3.Row | None] = {}
    if capabilities.has_contract_revision:
        if connection is None:
            raise _stored_task_unreadable()
        current_contract_rows = _validate_stored_contract_relationships(
            connection,
            rows,
            expected_project_id=expected_project_id,
            privacy_success_cache=privacy_success_cache,
        )
    if rejection is not None and not verification_rejection_is_local:
        raise _stored_task_unreadable()
    return StoredTaskValidationResult(
        verification_rejection=rejection,
        verification_rejected_task_ids=frozenset(rejected_task_ids),
        current_contract_rows=current_contract_rows,
    )


def validate_current_stored_task_rows(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    *,
    expected_project_id: str,
) -> None:
    version = current_schema_version(connection)
    validation = validate_stored_task_rows(
        rows,
        connection=connection,
        source_schema_version=(
            version
            if 1 <= version <= SCHEMA_VERSION or version == PRIVATE_SCHEMA22_VERSION
            else SCHEMA_VERSION
        ),
        expected_project_id=expected_project_id,
    )
    if version >= 18:
        try:
            validate_selected_task_authority_storage(
                connection,
                rows,
                expected_project_id=expected_project_id,
                current_contract_rows=validation.current_contract_rows,
            )
        except StorageError as exc:
            if exc.code == "database_busy":
                raise
            raise _stored_task_unreadable() from exc


def fetch_validated_current_task_row(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> sqlite3.Row | None:
    """Read one current Task and its Contract relation in one snapshot."""

    owns_read_transaction = not connection.in_transaction
    try:
        if owns_read_transaction:
            connection.execute("BEGIN")
        row = fetch_stored_task_row(
            connection,
            "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
            (project_id, task_id),
        )
        if row is not None:
            validate_current_stored_task_rows(
                connection,
                [row],
                expected_project_id=project_id,
            )
        return row
    except sqlite3.Error as exc:
        raise stored_task_sqlite_error(exc) from exc
    finally:
        if owns_read_transaction and connection.in_transaction:
            connection.rollback()
