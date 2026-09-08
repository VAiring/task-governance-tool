"""Evidence projection metadata; cycle transactions and file publication stay with callers."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_governance_tool.sqlite_connection import StorageError

if TYPE_CHECKING:
    from task_governance_tool.storage import DatabaseTarget


EVIDENCE_PROJECTION_OUTCOMES = {"succeeded", "deferred", "failed"}


@dataclass(frozen=True)
class EvidenceProjectionState:
    project_id: str
    source_generation: int
    published_generation: int | None
    index_digest: str | None
    last_success_at: str | None
    last_outcome_code: str | None
    last_outcome_at: str | None

    @property
    def due(self) -> bool:
        return (
            self.published_generation is None
            or self.published_generation < self.source_generation
            or self.last_outcome_code in {"deferred", "failed"}
        )


def _projection_state_from_row(row: sqlite3.Row) -> EvidenceProjectionState:
    from task_governance_tool.storage import (
        SHA256_DIGEST_PATTERN,
        SQLITE_INT64_MAX,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    project_id = row["project_id"]
    source_generation = row["source_generation"]
    published_generation = row["published_generation"]
    index_digest = row["index_digest"]
    last_success_at = row["last_success_at"]
    last_outcome_code = row["last_outcome_code"]
    last_outcome_at = row["last_outcome_at"]
    if (
        type(project_id) is not str
        or not project_id
        or type(source_generation) is not int
        or source_generation < 0
        or source_generation > SQLITE_INT64_MAX
        or (
            published_generation is not None
            and (
                type(published_generation) is not int
                or published_generation < 0
                or published_generation > source_generation
            )
        )
        or (
            (published_generation is None) != (index_digest is None)
        )
        or (
            index_digest is not None
            and (
                type(index_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(index_digest) is None
            )
        )
        or (
            last_outcome_code is not None
            and (
                type(last_outcome_code) is not str
                or last_outcome_code not in EVIDENCE_PROJECTION_OUTCOMES
            )
        )
        or ((last_outcome_code is None) != (last_outcome_at is None))
        or (last_success_at is not None and type(last_success_at) is not str)
        or (last_outcome_at is not None and type(last_outcome_at) is not str)
    ):
        raise evidence_ledger_inconsistent()
    try:
        if last_success_at is not None:
            validate_utc_timestamp(
                last_success_at,
                field="Evidence projection success time",
            )
        if last_outcome_at is not None:
            validate_utc_timestamp(
                last_outcome_at,
                field="Evidence projection outcome time",
            )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    return EvidenceProjectionState(
        project_id=project_id,
        source_generation=source_generation,
        published_generation=published_generation,
        index_digest=index_digest,
        last_success_at=last_success_at,
        last_outcome_code=last_outcome_code,
        last_outcome_at=last_outcome_at,
    )


def read_evidence_projection_state(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    row = connection.execute(
        "SELECT * FROM evidence_projection_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    return _projection_state_from_row(row)


def ensure_evidence_projection_state_row(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    """Seed the one project projection row after project identity exists."""

    from task_governance_tool.storage import (
        SCHEMA_VERSION,
        SQLITE_INT64_MAX,
        current_schema_version,
        evidence_ledger_inconsistent,
    )

    if current_schema_version(connection) != SCHEMA_VERSION:
        raise StorageError(
            "migration_required",
            f"Evidence projection state requires schema version {SCHEMA_VERSION}",
        )
    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    project_count = connection.execute(
        "SELECT COUNT(*) FROM project_meta WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    source_generation = connection.execute(
        "SELECT COUNT(*) FROM task_completion_cycles WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    if type(project_count) is not int or project_count != 1:
        raise evidence_ledger_inconsistent()
    if (
        type(source_generation) is not int
        or source_generation < 0
        or source_generation > SQLITE_INT64_MAX
    ):
        raise evidence_ledger_inconsistent()
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO evidence_projection_state(
              project_id, source_generation, published_generation,
              index_digest, last_success_at, last_outcome_code,
              last_outcome_at
            ) VALUES (?, ?, NULL, NULL, NULL, NULL, NULL)
            """,
            (project_id, source_generation),
        )
    except sqlite3.IntegrityError as exc:
        raise evidence_ledger_inconsistent() from exc
    state = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    if state.source_generation != source_generation:
        raise evidence_ledger_inconsistent()
    return state


def advance_evidence_source_generation_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    """Advance the DB-authoritative projection generation exactly once."""

    from task_governance_tool.storage import (
        SQLITE_INT64_MAX,
        _require_evidence_writer,
        evidence_ledger_inconsistent,
    )

    _require_evidence_writer(connection)
    current = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    if current.source_generation == SQLITE_INT64_MAX:
        raise evidence_ledger_inconsistent()
    cursor = connection.execute(
        """
        UPDATE evidence_projection_state
           SET source_generation = source_generation + 1
         WHERE project_id = ? AND source_generation = ?
        """,
        (project_id, current.source_generation),
    )
    if cursor.rowcount != 1:
        raise evidence_ledger_inconsistent()
    return read_evidence_projection_state(
        connection,
        project_id=project_id,
    )


def _advance_evidence_source_generation_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> EvidenceProjectionState:
    return advance_evidence_source_generation_locked(
        connection,
        project_id=project_id,
    )


def record_evidence_projection_outcome_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    captured_generation: int,
    outcome_code: str,
    recorded_at: str,
    index_digest: str | None = None,
) -> EvidenceProjectionState:
    """Conditionally record one bounded projector outcome without file data."""

    from task_governance_tool.storage import (
        SHA256_DIGEST_PATTERN,
        _require_evidence_writer,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    _require_evidence_writer(connection)
    current = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    if (
        type(captured_generation) is not int
        or not 0 <= captured_generation <= current.source_generation
        or type(outcome_code) is not str
        or outcome_code not in EVIDENCE_PROJECTION_OUTCOMES
        or type(recorded_at) is not str
        or (
            outcome_code == "succeeded"
            and (
                type(index_digest) is not str
                or SHA256_DIGEST_PATTERN.fullmatch(index_digest) is None
                or (
                    current.published_generation is not None
                    and captured_generation < current.published_generation
                )
            )
        )
        or (outcome_code != "succeeded" and index_digest is not None)
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            recorded_at,
            field="Evidence projection outcome time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    if outcome_code == "succeeded":
        cursor = connection.execute(
            """
            UPDATE evidence_projection_state
               SET published_generation = ?, index_digest = ?,
                   last_success_at = ?, last_outcome_code = ?,
                   last_outcome_at = ?
             WHERE project_id = ? AND source_generation >= ?
            """,
            (
                captured_generation,
                index_digest,
                recorded_at,
                outcome_code,
                recorded_at,
                project_id,
                captured_generation,
            ),
        )
    else:
        cursor = connection.execute(
            """
            UPDATE evidence_projection_state
               SET last_outcome_code = ?, last_outcome_at = ?
             WHERE project_id = ? AND source_generation >= ?
            """,
            (outcome_code, recorded_at, project_id, captured_generation),
        )
    if cursor.rowcount != 1:
        raise evidence_ledger_inconsistent()
    return read_evidence_projection_state(
        connection,
        project_id=project_id,
    )


def record_evidence_projection_outcome(
    target: DatabaseTarget,
    *,
    captured_generation: int,
    outcome_code: str,
    recorded_at: str,
    index_digest: str | None = None,
) -> EvidenceProjectionState:
    """Record one projector outcome through the initialized writer boundary."""

    from task_governance_tool.storage import (
        begin_initialized_write,
        connect_initialized,
    )

    with closing(connect_initialized(target)) as connection:
        try:
            begin_initialized_write(connection, target)
            state = record_evidence_projection_outcome_locked(
                connection,
                project_id=target.project.project_id,
                captured_generation=captured_generation,
                outcome_code=outcome_code,
                recorded_at=recorded_at,
                index_digest=index_digest,
            )
            connection.commit()
            return state
        except Exception:
            connection.rollback()
            raise
