from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest import mock

from task_governance_tool import backup as backup_service


def publish_generations(target, *timestamps: str, retention: int = 4):
    for timestamp in timestamps:
        with (
            mock.patch.object(
                backup_service,
                "utc_now",
                return_value=timestamp,
            ),
            backup_service.managed_backup_lock(target),
        ):
            backup_service.publish_setup_backup(target, retention)
    return backup_service._discover(target)


def replace_verification(path: Path, title: str, value: object) -> None:
    with closing(sqlite3.connect(path)) as connection:
        cursor = connection.execute(
            "UPDATE tasks SET verification = ? WHERE title = ?",
            (value, title),
        )
        if cursor.rowcount != 1:
            raise AssertionError("recovery test task was not updated")
        connection.commit()


def restore_temporaries(target) -> list[Path]:
    return list(target.db_path.parent.glob(".taskgov-restore-*.tmp"))


def restore_original_mtime(path: Path, before: os.stat_result) -> None:
    changed = path.stat()
    if changed.st_size != before.st_size:
        raise AssertionError("test mutation changed the SQLite file size")
    os.utime(path, ns=(changed.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    if (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise AssertionError("test mutation did not preserve file identity")


def replace_maintenance_pointer(
    path: Path,
    *,
    project_id: str,
    generation_id: str,
    published_at: str,
    retention: int,
) -> None:
    with closing(sqlite3.connect(path)) as connection:
        cursor = connection.execute(
            """
            UPDATE project_maintenance
               SET latest_backup_generation_id = ?,
                   backup_last_success_at = ?,
                   applied_backup_generations = ?
             WHERE project_id = ?
            """,
            (generation_id, published_at, retention, project_id),
        )
        if cursor.rowcount != 1:
            raise AssertionError("maintenance pointer was not updated")
        connection.commit()


def replace_recovery_integer_storage(
    path: Path,
    *,
    location: str,
    value: object,
) -> str:
    """Replace one recovery integer with an exact SQLite storage-class fixture."""

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        if location == "generation_row":
            row = connection.execute(
                """
                SELECT generation_id
                  FROM managed_backup_generations
                 ORDER BY published_at, generation_id
                 LIMIT 1
                """
            ).fetchone()
            if row is None:
                raise AssertionError("generation-row fixture is missing")
            cursor = connection.execute(
                """
                UPDATE managed_backup_generations
                   SET publication_retention = ?
                 WHERE generation_id = ?
                """,
                (value, str(row[0])),
            )
            observed = connection.execute(
                """
                SELECT typeof(publication_retention)
                  FROM managed_backup_generations
                 WHERE generation_id = ?
                """,
                (str(row[0]),),
            ).fetchone()
        elif location in {
            "maintenance_pointer",
            "backup_interval",
            "backup_generations",
        }:
            column = {
                "maintenance_pointer": "applied_backup_generations",
                "backup_interval": "backup_interval_minutes",
                "backup_generations": "backup_generations",
            }[location]
            cursor = connection.execute(
                f"UPDATE project_maintenance SET {column} = ?",
                (value,),
            )
            observed = connection.execute(
                f"SELECT typeof({column}) FROM project_maintenance"
            ).fetchone()
        else:
            raise AssertionError("unknown recovery integer fixture")
        if cursor.rowcount != 1 or observed is None:
            raise AssertionError("recovery integer fixture was not updated")
        connection.commit()
        return str(observed[0])


def inject_primary_candidate_metadata_conflict(target, artifact) -> None:
    generation_id = "tg_backup_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with closing(sqlite3.connect(target.db_path)) as connection:
        connection.execute(
            """
            INSERT INTO managed_backup_generations(
              generation_id,
              project_id,
              published_at,
              publication_retention
            ) VALUES (?, ?, ?, ?)
            """,
            (
                generation_id,
                target.project.project_id,
                "2097-02-01T00:00:00Z",
                artifact.metadata.publication_retention,
            ),
        )
        connection.commit()
    conflicting_published_at = "2098-02-01T00:00:00Z"
    with closing(sqlite3.connect(artifact.path)) as connection:
        connection.execute(
            """
            INSERT INTO managed_backup_generations(
              generation_id,
              project_id,
              published_at,
              publication_retention
            ) VALUES (?, ?, ?, ?)
            """,
            (
                generation_id,
                target.project.project_id,
                conflicting_published_at,
                artifact.metadata.publication_retention,
            ),
        )
        connection.commit()
    replace_maintenance_pointer(
        artifact.path,
        project_id=target.project.project_id,
        generation_id=generation_id,
        published_at=conflicting_published_at,
        retention=artifact.metadata.publication_retention,
    )
