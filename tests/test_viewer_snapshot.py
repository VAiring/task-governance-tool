import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    SCHEMA_VERSION,
    StorageError,
    apply_completion_commit_migration,
    connect,
    connect_snapshot_readonly,
    ensure_project_meta,
    initial_schema_sql,
    initialize_database,
    inspect_database,
    resolve_database_target,
)
from task_governance_tool.tasks import STATUSES, TASK_SHOW_FIELDS, add_task  # noqa: E402
from task_governance_tool.viewer import build_viewer_snapshot  # noqa: E402


SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"


def initialized_target(tmp: str):
    db = Path(tmp) / "taskgov.sqlite"
    repo = Path(tmp) / "repo"
    target = resolve_database_target(repo=repo, db=db, script_path=SCRIPT_PATH)
    initialize_database(target)
    return target


def table_count(db: Path, table: str) -> int:
    with closing(sqlite3.connect(db)) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class ViewerSnapshotTests(unittest.TestCase):
    def test_snapshot_projects_all_statuses_show_fields_and_bounded_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            tasks = {}
            with closing(connect(target.db_path)) as connection:
                with connection:
                    for index, status in enumerate(STATUSES):
                        initial_status = "ready" if status == "done" else status
                        tasks[status] = add_task(
                            connection,
                            target.project,
                            title=f"{status} task",
                            status=initial_status,
                            blocked_reason=("Waiting for input" if status == "blocked" else ""),
                            priority=("urgent" if index == 0 else "normal"),
                        ).task
                        if status == "done":
                            # Viewer projection deliberately seeds a historical row;
                            # completion-transition gates are tested at the CLI boundary.
                            connection.execute(
                                """
                                UPDATE tasks
                                   SET status = 'done',
                                       completed_at = '2026-07-17T00:00:00Z'
                                 WHERE task_id = ?
                                """,
                                (tasks[status]["task_id"],),
                            )
                            tasks[status]["status"] = "done"
                            tasks[status]["completed_at"] = "2026-07-17T00:00:00Z"
                    selected = tasks["ready"]
                    connection.execute(
                        """
                        UPDATE tasks
                           SET completion_commit_hash = ?
                         WHERE task_id = ?
                        """,
                        ("abc123viewer", selected["task_id"]),
                    )
                    for index in range(12):
                        connection.execute(
                            """
                            INSERT INTO task_events(
                              task_event_id, task_id, project_id,
                              event_type, summary, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"tg_event_viewer_{index:02d}",
                                selected["task_id"],
                                target.project.project_id,
                                "note_added",
                                f"Viewer event {index:02d}",
                                "2999-01-01T00:00:00Z",
                            ),
                        )

            task_events_before = table_count(target.db_path, "task_events")
            tool_events_before = table_count(target.db_path, "tool_events")
            generated_at = "2026-07-17T00:00:00Z"
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                result = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at=generated_at,
                )

            snapshot = result.snapshot
            self.assertEqual(snapshot["snapshot_version"], 1)
            self.assertEqual(snapshot["generated_at"], generated_at)
            self.assertEqual(snapshot["source_schema_version"], SCHEMA_VERSION)
            self.assertEqual(snapshot["project"], {
                "project_id": target.project.project_id,
                "display_name": target.project.display_name,
            })
            self.assertEqual(snapshot["counts"]["total"], len(STATUSES))
            for status in STATUSES:
                self.assertEqual(snapshot["counts"][status], 1)

            self.assertEqual(result.task_count, len(STATUSES))
            ready = next(task for task in snapshot["tasks"] if task["status"] == "ready")
            self.assertEqual(set(ready), set(TASK_SHOW_FIELDS) | {"events"})
            self.assertEqual(ready["completion_commit_required"], 1)
            self.assertEqual(ready["completion_commit_hash"], "abc123viewer")
            self.assertEqual(len(ready["events"]), 10)
            self.assertEqual(
                [event["summary"] for event in ready["events"]],
                [f"Viewer event {index:02d}" for index in range(11, 1, -1)],
            )
            self.assertEqual(result.event_count, 15)
            self.assertEqual(snapshot["tasks"][0]["priority"], "urgent")

            serialized = json.dumps(snapshot)
            self.assertNotIn(str(target.project.canonical_repo), serialized)
            self.assertNotIn(str(target.db_path), serialized)
            self.assertEqual(table_count(target.db_path, "task_events"), task_events_before)
            self.assertEqual(table_count(target.db_path, "tool_events"), tool_events_before)
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(target.db_path) + suffix).exists())

    def test_snapshot_connection_is_query_only_and_revalidates_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                self.assertTrue(connection.in_transaction)
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "UPDATE project_meta SET display_name = 'changed'"
                    )

            other_target = resolve_database_target(
                repo=Path(tmp) / "other-repo",
                db=target.db_path,
                script_path=SCRIPT_PATH,
            )
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                with self.assertRaises(StorageError) as mismatch:
                    build_viewer_snapshot(connection, other_target)
            self.assertEqual(mismatch.exception.code, "project_mismatch")

    def test_snapshot_rejects_migration_mismatch_inside_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    connection.execute("DELETE FROM schema_migrations WHERE version = 2")

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                with self.assertRaises(StorageError) as migration:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(migration.exception.code, "migration_required")

    def test_snapshot_rejects_current_version_database_missing_show_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = resolve_database_target(
                repo=Path(tmp) / "repo",
                db=Path(tmp) / "taskgov.sqlite",
                script_path=SCRIPT_PATH,
            )
            incomplete_schema = initial_schema_sql().replace(
                "  description TEXT NOT NULL DEFAULT '',\n",
                "",
            )
            with closing(connect(target.db_path)) as connection:
                with connection:
                    connection.executescript(incomplete_schema)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (1, 'incomplete_initial_schema', '2026-07-17T00:00:00Z')
                        """
                    )
                    apply_completion_commit_migration(connection)
                    ensure_project_meta(connection, target.project)

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                with self.assertRaises(StorageError) as migration:
                    build_viewer_snapshot(connection, target)

            self.assertEqual(migration.exception.code, "migration_required")

    def test_snapshot_maps_sqlite_read_failure_to_storage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            connection = connect_snapshot_readonly(target.db_path)
            connection.close()

            with self.assertRaises(StorageError) as failure:
                build_viewer_snapshot(connection, target)

            self.assertEqual(failure.exception.code, "internal_error")
            self.assertEqual(failure.exception.message, "could not read viewer snapshot")

    def test_active_wal_is_rejected_before_snapshot_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as writer:
                writer.execute("PRAGMA journal_mode = WAL")
                writer.execute("BEGIN IMMEDIATE")
                writer.execute(
                    "UPDATE project_meta SET display_name = display_name"
                )
                status = inspect_database(target)
                self.assertEqual(status.error_code, "internal_error")
                self.assertIn("WAL sidecar", status.error_message or "")
                writer.rollback()

    def test_open_snapshot_stays_consistent_when_writer_cannot_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    task = add_task(connection, target.project, title="Before writer").task

            with closing(connect_snapshot_readonly(target.db_path)) as snapshot_connection:
                first = build_viewer_snapshot(snapshot_connection, target).snapshot
                with closing(sqlite3.connect(target.db_path, timeout=0.01)) as writer:
                    writer.execute("PRAGMA busy_timeout = 1")
                    writer.execute(
                        "UPDATE tasks SET title = ? WHERE task_id = ?",
                        ("After writer", task["task_id"]),
                    )
                    with self.assertRaises(sqlite3.OperationalError):
                        writer.commit()
                    writer.rollback()
                second = build_viewer_snapshot(snapshot_connection, target).snapshot

            self.assertEqual(first["tasks"][0]["title"], "Before writer")
            self.assertEqual(second["tasks"][0]["title"], "Before writer")


if __name__ == "__main__":
    unittest.main()
