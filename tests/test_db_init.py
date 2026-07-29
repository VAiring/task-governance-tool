import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_PATH = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_PATH))
try:
    from task_governance_tool.storage import (
        MigrationBackupMetadata,
        SCHEMA_VERSION,
        StorageError,
        apply_completion_commit_migration,
        apply_completion_evidence_migration,
        apply_git_snapshot_schema_migration,
        apply_initial_schema_migration,
        apply_managed_backup_generations_migration,
        apply_migrations,
        apply_paused_state_migration,
        apply_project_maintenance_migration,
        apply_review_evidence_migration,
        apply_task_checkpoints_migration,
        apply_viewer_maintenance_migration,
        begin_initialized_write,
        connect_initialized,
        ensure_project_meta,
        initialize_database,
        initial_schema_sql,
        project_identity,
        resolve_database_target,
    )
finally:
    sys.path.pop(0)

try:
    from m14_test_support import (
        create_v10_database,
        create_v12_database,
        create_v9_database,
        initialize_taskgov_internal,
        make_legacy_physical_install as make_physical_install,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        create_v10_database,
        create_v12_database,
        create_v9_database,
        initialize_taskgov_internal,
        make_legacy_physical_install as make_physical_install,
    )


def task_row(**overrides):
    row = {
        "task_id": "tg_task_test",
        "project_id": "project-123456789abc",
        "title": "Test task",
        "description": "",
        "kind": "optional",
        "lane": "",
        "lane_order": None,
        "priority": "normal",
        "status": "ready",
        "blocked_reason": "",
        "review_tier": 1,
        "verification": "",
        "tags": "",
        "created_at": "2026-07-06T00:00:00Z",
        "updated_at": "2026-07-06T00:00:00Z",
        "completed_at": None,
    }
    row.update(overrides)
    return row


def insert_task(connection, **overrides):
    row = task_row(**overrides)
    connection.execute(
        """
        INSERT INTO tasks(
          task_id,
          project_id,
          title,
          description,
          kind,
          lane,
          lane_order,
          priority,
          status,
          blocked_reason,
          review_tier,
          verification,
          tags,
          created_at,
          updated_at,
          completed_at
        )
        VALUES (
          :task_id,
          :project_id,
          :title,
          :description,
          :kind,
          :lane,
          :lane_order,
          :priority,
          :status,
          :blocked_reason,
          :review_tier,
          :verification,
          :tags,
          :created_at,
          :updated_at,
          :completed_at
        )
        """,
        row,
    )


def create_v2_database(db, repo):
    project = project_identity(repo)
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        apply_initial_schema_migration(connection)
        apply_completion_commit_migration(connection)
        ensure_project_meta(connection, project)
        insert_task(connection, project_id=project.project_id)
        connection.execute(
            """
            INSERT INTO task_events(
              task_event_id, task_id, project_id, event_type, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "tg_event_test",
                "tg_task_test",
                project.project_id,
                "task_added",
                "Task registered",
                "2026-07-06T00:00:00Z",
            ),
        )
        connection.commit()
    return project


def create_v5_review_database(db, repo):
    project = project_identity(repo)
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        apply_initial_schema_migration(connection)
        apply_completion_commit_migration(connection)
        ensure_project_meta(connection, project)
        connection.commit()
        apply_paused_state_migration(connection)
        apply_completion_evidence_migration(connection)
        insert_task(
            connection,
            task_id="tg_task_v5_review",
            project_id=project.project_id,
            review_tier=2,
        )
        connection.commit()
        apply_review_evidence_migration(connection)
        connection.execute(
            """
            UPDATE tasks
               SET review_target_kind = 'diff_fingerprint',
                   review_target_value = ?,
                   review_target_generation = 1
             WHERE task_id = 'tg_task_v5_review'
            """,
            ("sha256:" + ("a" * 64),),
        )
        receipts = (
            (
                "tg_receipt_v5_pass",
                "reviewer-pass",
                "pass",
                "Migration PASS receipt",
            ),
            (
                "tg_receipt_v5_changes",
                "reviewer-changes",
                "changes_requested",
                "Migration needs correction",
            ),
        )
        for receipt_id, reviewer, verdict, summary in receipts:
            connection.execute(
                """
                INSERT INTO review_receipts(
                  review_receipt_id, task_id, project_id, reviewer_key,
                  receipt_kind, verdict, target_kind, target_value,
                  target_generation, summary, user_approved, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    receipt_id,
                    "tg_task_v5_review",
                    project.project_id,
                    reviewer,
                    "independent",
                    verdict,
                    "diff_fingerprint",
                    "sha256:" + ("a" * 64),
                    1,
                    summary,
                    "2026-07-26T00:00:00Z",
                ),
            )
        connection.execute(
            """
            INSERT INTO review_findings(
              review_finding_id, review_receipt_id, severity, status,
              summary, resolution_summary, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tg_finding_v5",
                "tg_receipt_v5_changes",
                "medium",
                "open",
                "Preserve this finding",
                "",
                "2026-07-26T00:01:00Z",
                None,
            ),
        )
        connection.commit()
    return project


class StorageInitializationTests(unittest.TestCase):
    def test_initialize_rejects_incomplete_migration_history_without_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            initialize_taskgov_internal(repo=repo, db=db)
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("DELETE FROM schema_migrations WHERE version = 4")
                connection.commit()
            before = db.read_bytes()

            with self.assertRaises(StorageError) as raised:
                initialize_database(target)
            self.assertEqual(raised.exception.code, "migration_required")
            self.assertIn("restore a valid database backup", raised.exception.message)
            self.assertEqual(db.read_bytes(), before)
            with closing(sqlite3.connect(db)) as connection:
                versions = [
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
            self.assertEqual(
                versions,
                [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
            )

    def test_initialize_migrates_schema_v1_database_through_current_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            project = project_identity(repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                connection.executescript(initial_schema_sql())
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (1, "initial_schema", "2026-07-06T00:00:00Z"),
                )
                connection.execute(
                    """
                    INSERT INTO project_meta(
                      project_id,
                      canonical_path_hash,
                      display_name,
                      created_at,
                      updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project.project_id,
                        project.canonical_path_hash,
                        project.display_name,
                        "2026-07-06T00:00:00Z",
                        "2026-07-06T00:00:00Z",
                    ),
                )
                insert_task(connection, project_id=project.project_id)
                connection.commit()

            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            result = initialize_database(target)
            self.assertFalse(result.created)
            self.assertEqual(
                result.migrations_applied,
                [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
            )
            self.assertEqual(result.schema_version, 14)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                task = connection.execute(
                    """
                    SELECT completion_commit_required, completion_commit_hash, pause_reason
                      FROM tasks
                     WHERE task_id = ?
                    """,
                    ("tg_task_test",),
                ).fetchone()
                versions = [
                    row["version"]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                self.assertEqual(
                    versions,
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
                )
            self.assertEqual(task["completion_commit_required"], 1)
            self.assertEqual(task["completion_commit_hash"], "")
            self.assertEqual(task["pause_reason"], "")

    def test_initialize_migrates_v2_tasks_and_events_to_current_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            create_v2_database(db, repo)

            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            result = initialize_database(target)
            self.assertEqual(
                result.migrations_applied,
                [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
            )
            self.assertEqual(result.schema_version, 14)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", ("tg_task_test",)).fetchone()
                event = connection.execute(
                    "SELECT * FROM task_events WHERE task_event_id = ?", ("tg_event_test",)
                ).fetchone()
                versions = [
                    row["version"]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
                foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
                self.assertEqual(
                    versions,
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
                )
            self.assertEqual(task["pause_reason"], "")
            self.assertEqual(event["task_id"], "tg_task_test")
            self.assertEqual(quick_check, "ok")
            self.assertEqual(foreign_key_rows, [])

    def test_paused_state_migration_failure_rolls_back_and_restores_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            create_v2_database(db, repo)

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                with self.assertRaises(StorageError):
                    apply_paused_state_migration(connection, fail_stage="after_copy")

                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                versions = [
                    row["version"]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                task_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
                }
                task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                event_count = connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
                quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
                foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()

            self.assertEqual(versions, [1, 2])
            self.assertNotIn("pause_reason", task_columns)
            self.assertEqual(task_count, 1)
            self.assertEqual(event_count, 1)
            self.assertEqual(quick_check, "ok")
            self.assertEqual(foreign_key_rows, [])

    def test_paused_state_migration_failure_after_parent_replacement_restores_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            create_v2_database(db, repo)

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                with self.assertRaises(StorageError):
                    apply_paused_state_migration(connection, fail_stage="before_commit")

                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                versions = [
                    row["version"]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                task_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
                }
                task_ids = [
                    row["task_id"] for row in connection.execute("SELECT task_id FROM tasks").fetchall()
                ]
                event_links = [
                    (row["task_event_id"], row["task_id"])
                    for row in connection.execute(
                        "SELECT task_event_id, task_id FROM task_events"
                    ).fetchall()
                ]
                index_names = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
                foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()

            self.assertEqual(versions, [1, 2])
            self.assertNotIn("pause_reason", task_columns)
            self.assertEqual(task_ids, ["tg_task_test"])
            self.assertEqual(event_links, [("tg_event_test", "tg_task_test")])
            self.assertIn("idx_tasks_project_completion_commit", index_names)
            self.assertEqual(quick_check, "ok")
            self.assertEqual(foreign_key_rows, [])

    def test_paused_state_migration_success_restores_foreign_keys_on_same_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            create_v2_database(db, repo)

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                apply_paused_state_migration(connection)

                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    3,
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_completion_evidence_migration_maps_legacy_rows_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            project = create_v2_database(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                apply_paused_state_migration(connection)
                for task_id in ("tg_task_not_required", "tg_task_hash", "tg_task_inconsistent"):
                    insert_task(connection, project_id=project.project_id, task_id=task_id)
                connection.execute(
                    "UPDATE tasks SET completion_commit_required = 0 WHERE task_id = ?",
                    ("tg_task_not_required",),
                )
                connection.execute(
                    "UPDATE tasks SET completion_commit_hash = ? WHERE task_id = ?",
                    ("legacy-hash", "tg_task_hash"),
                )
                connection.execute(
                    """
                    UPDATE tasks
                       SET completion_commit_required = 0,
                           completion_commit_hash = ?
                     WHERE task_id = ?
                    """,
                    ("inconsistent-hash", "tg_task_inconsistent"),
                )
                connection.commit()

                with connection:
                    inconsistent = apply_completion_evidence_migration(connection)

                rows = {
                    row["task_id"]: dict(row)
                    for row in connection.execute("SELECT * FROM tasks ORDER BY task_id")
                }
                versions = [
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]

            self.assertEqual(inconsistent, 1)
            self.assertEqual(versions, [1, 2, 3, 4])
            self.assertEqual(rows["tg_task_test"]["completion_evidence_kind"], "none")
            self.assertEqual(
                rows["tg_task_not_required"]["completion_evidence_kind"],
                "commit_not_required",
            )
            self.assertEqual(rows["tg_task_hash"]["completion_evidence_kind"], "legacy_unverified")
            self.assertEqual(rows["tg_task_hash"]["completion_evidence_revision"], "legacy-hash")
            self.assertEqual(
                rows["tg_task_inconsistent"]["completion_evidence_kind"],
                "legacy_unverified",
            )
            self.assertEqual(rows["tg_task_inconsistent"]["completion_commit_required"], 0)
            self.assertEqual(rows["tg_task_inconsistent"]["completion_commit_hash"], "inconsistent-hash")

    def test_completion_evidence_migration_failure_rolls_back_columns_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            create_v2_database(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                apply_paused_state_migration(connection)
                with self.assertRaises(StorageError):
                    with connection:
                        apply_completion_evidence_migration(connection, fail_stage="after_mapping")

                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
                }
                versions = [
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                self.assertNotIn("completion_evidence_kind", columns)
                self.assertEqual(versions, [1, 2, 3])
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_initialize_warns_when_inconsistent_legacy_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            create_v2_database(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                apply_paused_state_migration(connection)
                connection.execute(
                    """
                    UPDATE tasks
                       SET completion_commit_required = 0,
                           completion_commit_hash = 'preserved-revision'
                    """
                )
                connection.commit()

            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            result = initialize_database(target)
            self.assertEqual(
                result.migrations_applied,
                [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
            )
            self.assertEqual(
                result.warnings[0]["code"],
                "legacy_completion_evidence_preserved",
            )
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    """
                    SELECT completion_commit_required, completion_commit_hash,
                           completion_evidence_kind, completion_evidence_revision
                      FROM tasks
                    """
                ).fetchone()
            self.assertEqual(row, (0, "preserved-revision", "legacy_unverified", "preserved-revision"))

    def test_initialize_creates_required_tables_and_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            initialize_taskgov_internal(repo=repo, db=db)
            with closing(sqlite3.connect(db)) as connection:
                table_rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
                index_rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
                trigger_rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
                unique_index_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'index'
                       AND name = 'idx_tasks_project_lane_order_unique'
                    """
                ).fetchone()[0]

            tables = {row[0] for row in table_rows}
            indexes = {row[0] for row in index_rows}
            triggers = {row[0] for row in trigger_rows}
            self.assertTrue(
                {
                    "schema_migrations",
                    "project_meta",
                    "tasks",
                    "task_events",
                    "tool_events",
                    "managed_backup_generations",
                    "task_checkpoints",
                    "viewer_maintenance_state",
                }.issubset(tables)
            )
            self.assertTrue(
                {
                    "idx_tasks_project_status",
                    "idx_tasks_project_kind",
                    "idx_tasks_project_lane_order",
                    "idx_tasks_project_lane_order_unique",
                    "idx_task_events_task_created",
                    "idx_tasks_project_completion_commit",
                    "idx_managed_backup_project_published",
                    "idx_checkpoints_project_task_created",
                }.issubset(indexes)
            )
            self.assertIn("CREATE UNIQUE INDEX", unique_index_sql.upper())
            self.assertIn("WHERE kind = 'sequential'", unique_index_sql)
            self.assertIn("trg_task_events_viewer_generation", triggers)
            with closing(sqlite3.connect(db)) as connection:
                column_rows = connection.execute("PRAGMA table_info(tasks)").fetchall()
            task_columns = {row[1] for row in column_rows}
            self.assertIn("completion_commit_required", task_columns)
            self.assertIn("completion_commit_hash", task_columns)
            self.assertIn("completion_evidence_kind", task_columns)
            self.assertIn("completion_evidence_revision", task_columns)
            self.assertIn("completion_evidence_reason", task_columns)
            self.assertIn("external_revision_approved", task_columns)

    def test_initialize_rejects_project_mismatch_for_injected_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo_one = Path(tmp) / "repo-one"
            repo_two = Path(tmp) / "repo-two"

            initialize_taskgov_internal(repo=repo_one, db=db)
            target = resolve_database_target(
                repo=repo_two,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            with self.assertRaises(StorageError) as raised:
                initialize_database(target)
            self.assertEqual(raised.exception.code, "project_mismatch")

    def test_project_mismatch_does_not_migrate_an_owned_v2_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            owner_repo = Path(tmp) / "owner"
            other_repo = Path(tmp) / "other"
            create_v2_database(db, owner_repo)

            target = resolve_database_target(
                repo=other_repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            with self.assertRaises(StorageError) as raised:
                initialize_database(target)
            self.assertEqual(raised.exception.code, "project_mismatch")
            with closing(sqlite3.connect(db)) as connection:
                versions = [
                    row[0] for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                task_columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
            self.assertEqual(versions, [1, 2])
            self.assertNotIn("pause_reason", task_columns)

    def test_initialized_connection_locks_only_for_explicit_short_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            initialize_taskgov_internal(repo=repo, db=db)
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )

            with closing(connect_initialized(target)) as connection:
                self.assertFalse(connection.in_transaction)
                with closing(sqlite3.connect(db, timeout=0)) as contender:
                    contender.execute("BEGIN IMMEDIATE")
                    contender.rollback()

                begin_initialized_write(connection, target)
                self.assertTrue(connection.in_transaction)
                with closing(sqlite3.connect(db, timeout=0)) as contender:
                    with self.assertRaises(sqlite3.OperationalError):
                        contender.execute("BEGIN IMMEDIATE")
                connection.rollback()

            with closing(sqlite3.connect(db, timeout=0)) as contender:
                contender.execute("BEGIN IMMEDIATE")
                contender.rollback()

    def test_initialized_write_maps_real_contention_to_sanitized_database_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            initialize_taskgov_internal(repo=repo, db=db)
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )

            with closing(sqlite3.connect(db, timeout=0)) as owner:
                owner.execute("BEGIN IMMEDIATE")
                with closing(connect_initialized(target)) as connection:
                    connection.execute("PRAGMA busy_timeout = 0")
                    with self.assertRaises(StorageError) as raised:
                        begin_initialized_write(connection, target)
                    self.assertFalse(connection.in_transaction)
                owner.rollback()

            self.assertEqual(raised.exception.code, "database_busy")
            self.assertEqual(
                raised.exception.message,
                "task database is busy; run the command again later",
            )

    def test_schema_constraints_reject_invalid_task_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            initialize_taskgov_internal(repo=repo, db=db)

            with closing(sqlite3.connect(db)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    insert_task(connection, status="blocked", blocked_reason="")
                insert_task(connection, task_id="tg_task_pause_check")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE tasks SET status = 'paused' WHERE task_id = 'tg_task_pause_check'"
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE tasks SET pause_reason = 'Hold' WHERE task_id = 'tg_task_pause_check'"
                    )
                connection.execute(
                    """
                    UPDATE tasks
                       SET status = 'paused', pause_reason = 'Hold'
                     WHERE task_id = 'tg_task_pause_check'
                    """
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    insert_task(connection, kind="sequential", lane="", lane_order=10)
                with self.assertRaises(sqlite3.IntegrityError):
                    insert_task(connection, kind="sequential", lane="lane-a", lane_order=None)

                insert_task(
                    connection,
                    task_id="tg_task_one",
                    kind="sequential",
                    lane="lane-a",
                    lane_order=1,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    insert_task(
                        connection,
                        task_id="tg_task_two",
                        kind="sequential",
                        lane="lane-a",
                        lane_order=1,
                    )

    def test_review_evidence_migration_preserves_rows_and_adds_normalized_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                apply_initial_schema_migration(connection)
                apply_completion_commit_migration(connection)
                connection.commit()
                apply_paused_state_migration(connection)
                apply_completion_evidence_migration(connection)
                insert_task(connection, task_id="tg_task_review_migration")
                connection.commit()

                apply_review_evidence_migration(connection)
                row = connection.execute(
                    """
                    SELECT review_target_kind, review_target_value,
                           review_target_generation
                      FROM tasks WHERE task_id = 'tg_task_review_migration'
                    """
                ).fetchone()
                self.assertEqual(dict(row), {
                    "review_target_kind": "",
                    "review_target_value": "",
                    "review_target_generation": 0,
                })
                self.assertEqual(
                    connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    5,
                )
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertIn("review_receipts", tables)
                self.assertIn("review_findings", tables)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_review_evidence_migration_failure_rolls_back_schema_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                apply_initial_schema_migration(connection)
                apply_completion_commit_migration(connection)
                connection.commit()
                apply_paused_state_migration(connection)
                apply_completion_evidence_migration(connection)

                with self.assertRaises(StorageError):
                    apply_review_evidence_migration(connection, fail_stage="after_schema")

                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(tasks)")
                }
                self.assertNotIn("review_target_kind", columns)
                self.assertFalse(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'review_receipts'"
                    ).fetchone()
                )
                self.assertEqual(
                    connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    4,
                )

    def test_git_snapshot_migration_preserves_v5_review_evidence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            project = create_v5_review_database(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                before_receipts = connection.execute(
                    """
                    SELECT review_receipt_id, task_id, project_id, reviewer_key,
                           receipt_kind, verdict, target_kind, target_value,
                           target_generation, summary, user_approved, created_at
                      FROM review_receipts ORDER BY review_receipt_id
                    """
                ).fetchall()
                before_findings = connection.execute(
                    "SELECT * FROM review_findings ORDER BY review_finding_id"
                ).fetchall()
                before_project = connection.execute(
                    """
                    SELECT project_id, canonical_path_hash, display_name, created_at
                      FROM project_meta WHERE project_id = ?
                    """,
                    (project.project_id,),
                ).fetchone()

            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            migrated = initialize_database(target)
            self.assertEqual(
                migrated.migrations_applied,
                [6, 7, 8, 9, 10, 11, 12, 13, 14],
            )
            self.assertEqual(migrated.schema_version, 14)

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                task = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = 'tg_task_v5_review'"
                ).fetchone()
                after_receipts = connection.execute(
                    """
                    SELECT review_receipt_id, task_id, project_id, reviewer_key,
                           receipt_kind, verdict, target_kind, target_value,
                           target_generation, summary, user_approved, created_at
                      FROM review_receipts ORDER BY review_receipt_id
                    """
                ).fetchall()
                after_findings = connection.execute(
                    "SELECT * FROM review_findings ORDER BY review_finding_id"
                ).fetchall()
                receipt_bases = connection.execute(
                    """
                    SELECT review_receipt_id, target_base_revision
                      FROM review_receipts ORDER BY review_receipt_id
                    """
                ).fetchall()
                after_project = connection.execute(
                    """
                    SELECT project_id, canonical_path_hash, display_name, created_at
                      FROM project_meta WHERE project_id = ?
                    """,
                    (project.project_id,),
                ).fetchone()
                finding_fk = connection.execute(
                    """
                    SELECT finding.review_finding_id, finding.review_receipt_id
                      FROM review_findings AS finding
                      JOIN review_receipts AS receipt
                        ON receipt.review_receipt_id = finding.review_receipt_id
                    """
                ).fetchall()

                self.assertEqual(task["review_target_base_revision"], "")
                self.assertEqual(
                    [(row["review_receipt_id"], row["target_base_revision"]) for row in receipt_bases],
                    [
                        ("tg_receipt_v5_changes", ""),
                        ("tg_receipt_v5_pass", ""),
                    ],
                )
                self.assertEqual([tuple(row) for row in after_receipts], before_receipts)
                self.assertEqual([tuple(row) for row in after_findings], before_findings)
                self.assertEqual(
                    [tuple(row) for row in finding_fk],
                    [("tg_finding_v5", "tg_receipt_v5_changes")],
                )
                self.assertEqual(tuple(after_project), before_project)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            repeated = initialize_database(target)
            self.assertEqual(repeated.migrations_applied, [])
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipts"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_findings"
                    ).fetchone()[0],
                    1,
                )

    def test_git_snapshot_migration_failures_restore_v5_schema_and_links(self):
        for fail_stage in ("after_copy", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                db = root / "taskgov.sqlite"
                repo = root / "repo"
                create_v5_review_database(db, repo)
                with closing(sqlite3.connect(db)) as connection:
                    before_receipts = connection.execute(
                        "SELECT * FROM review_receipts ORDER BY review_receipt_id"
                    ).fetchall()
                    before_findings = connection.execute(
                        "SELECT * FROM review_findings ORDER BY review_finding_id"
                    ).fetchall()

                with closing(sqlite3.connect(db)) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA foreign_keys = ON")
                    with self.assertRaises(StorageError):
                        apply_git_snapshot_schema_migration(
                            connection,
                            fail_stage=fail_stage,
                        )

                    task_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(tasks)")
                    }
                    receipt_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(review_receipts)")
                    }
                    self.assertNotIn("review_target_base_revision", task_columns)
                    self.assertNotIn("target_base_revision", receipt_columns)
                    self.assertFalse(
                        connection.execute(
                            """
                            SELECT 1 FROM sqlite_master
                             WHERE type = 'table' AND name = 'review_receipts_v6'
                            """
                        ).fetchone()
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        5,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_keys").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        [
                            tuple(row)
                            for row in connection.execute(
                                "SELECT * FROM review_receipts ORDER BY review_receipt_id"
                            ).fetchall()
                        ],
                        before_receipts,
                    )
                    self.assertEqual(
                        [
                            tuple(row)
                            for row in connection.execute(
                                "SELECT * FROM review_findings ORDER BY review_finding_id"
                            ).fetchall()
                        ],
                        before_findings,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA quick_check").fetchone()[0],
                        "ok",
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )

class ProjectMaintenanceMigrationTests(unittest.TestCase):
    def test_v9_to_v10_is_rollback_safe_idempotent_and_starts_partial(self):
        for fail_stage in ("after_schema", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v9_database(install)
                with closing(sqlite3.connect(install.db_path)) as connection:
                    before_counts = {
                        table: connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                        for table in (
                            "tasks",
                            "task_events",
                            "review_receipts",
                            "review_findings",
                        )
                    }

                with closing(sqlite3.connect(install.db_path)) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA foreign_keys = ON")
                    with self.assertRaises(StorageError):
                        apply_project_maintenance_migration(
                            connection,
                            fail_stage=fail_stage,
                        )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        9,
                    )
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table' AND name = 'project_maintenance'
                            """
                        ).fetchone()
                    )

                    apply_project_maintenance_migration(connection)
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        10,
                    )
                    row = connection.execute(
                        """
                        SELECT project_id, enabled_at, backup_interval_minutes,
                               backup_generations, applied_backup_generations
                          FROM project_maintenance
                        """
                    ).fetchone()
                    self.assertEqual(row["project_id"], install.project_id)
                    self.assertEqual(tuple(row)[1:], (None, None, None, None))
                    apply_project_maintenance_migration(connection)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM project_maintenance"
                        ).fetchone()[0],
                        1,
                    )
                    after_counts = {
                        table: connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                        for table in (
                            "tasks",
                            "task_events",
                            "review_receipts",
                            "review_findings",
                        )
                    }
                    self.assertEqual(after_counts, before_counts)
                    self.assertEqual(
                        connection.execute("PRAGMA quick_check").fetchone()[0],
                        "ok",
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )
        self.assertEqual(SCHEMA_VERSION, 14)


class ManagedBackupGenerationMigrationTests(unittest.TestCase):
    @staticmethod
    def metadata(index: int, retention: int = 3) -> MigrationBackupMetadata:
        return MigrationBackupMetadata(
            generation_id=f"tg_backup_{index:032x}",
            published_at=f"2026-07-27T00:00:{index:02d}Z",
            publication_retention=retention,
        )

    def test_v10_to_v11_seeds_deterministic_rows_and_enforces_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            generations = (
                self.metadata(1),
                self.metadata(2),
                self.metadata(3),
            )
            create_v10_database(
                install,
                setup_backup=generations[-1],
            )

            with closing(sqlite3.connect(install.db_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                apply_managed_backup_generations_migration(
                    connection,
                    managed_backups=tuple(reversed(generations)),
                )
                rows = connection.execute(
                    """
                    SELECT generation_id, project_id, published_at,
                           publication_retention
                      FROM managed_backup_generations
                     ORDER BY published_at, generation_id
                    """
                ).fetchall()
                self.assertEqual(
                    [
                        (
                            row["generation_id"],
                            row["project_id"],
                            row["published_at"],
                            row["publication_retention"],
                        )
                        for row in rows
                    ],
                    [
                        (
                            item.generation_id,
                            install.project_id,
                            item.published_at,
                            item.publication_retention,
                        )
                        for item in generations
                    ],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    11,
                )
                apply_managed_backup_generations_migration(
                    connection,
                    managed_backups=generations,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM managed_backup_generations"
                    ).fetchone()[0],
                    3,
                )
                invalid_rows = (
                    (
                        "invalid-generation",
                        install.project_id,
                        "2026-07-27T00:00:04Z",
                        3,
                    ),
                    (
                        f"tg_backup_{4:032x}",
                        install.project_id,
                        "not-a-canonical-time",
                        3,
                    ),
                    (
                        f"tg_backup_{5:032x}",
                        install.project_id,
                        "2026-07-27T00:00:05Z",
                        21,
                    ),
                )
                for row in invalid_rows:
                    with self.subTest(row=row), self.assertRaises(
                        sqlite3.IntegrityError
                    ):
                        connection.execute(
                            """
                            INSERT INTO managed_backup_generations(
                              generation_id, project_id, published_at,
                              publication_retention
                            ) VALUES (?, ?, ?, ?)
                            """,
                            row,
                        )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )

    def test_v11_migration_rolls_back_each_stage_then_is_idempotent(self):
        latest = self.metadata(2, retention=2)
        generations = (self.metadata(1, retention=2), latest)
        for fail_stage in ("after_schema", "after_rows", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v10_database(install, setup_backup=latest)
                with closing(sqlite3.connect(install.db_path)) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA foreign_keys = ON")
                    with self.assertRaises(StorageError):
                        apply_managed_backup_generations_migration(
                            connection,
                            managed_backups=generations,
                            fail_stage=fail_stage,
                        )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        10,
                    )
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table'
                               AND name = 'managed_backup_generations'
                            """
                        ).fetchone()
                    )
                    apply_managed_backup_generations_migration(
                        connection,
                        managed_backups=generations,
                    )
                    apply_managed_backup_generations_migration(
                        connection,
                        managed_backups=generations,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM managed_backup_generations"
                        ).fetchone()[0],
                        2,
                    )

    def test_v11_migration_rejects_missing_or_mismatched_latest_pointer(self):
        latest = self.metadata(2, retention=2)
        cases = {
            "missing": (self.metadata(1, retention=2),),
            "empty": (),
            "mismatched": (
                MigrationBackupMetadata(
                    generation_id=latest.generation_id,
                    published_at="2026-07-27T00:00:03Z",
                    publication_retention=latest.publication_retention,
                ),
            ),
        }
        for name, generations in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v10_database(install, setup_backup=latest)
                with closing(sqlite3.connect(install.db_path)) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA foreign_keys = ON")
                    with self.assertRaises(StorageError):
                        apply_managed_backup_generations_migration(
                            connection,
                            managed_backups=generations,
                        )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        10,
                    )
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table'
                               AND name = 'managed_backup_generations'
                            """
                        ).fetchone()
                    )


class TaskCheckpointMigrationTests(unittest.TestCase):
    def test_v11_to_v12_adds_bounded_append_store_with_project_task_fks(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v10_database(install)
            with closing(sqlite3.connect(install.db_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                apply_managed_backup_generations_migration(connection)
                apply_task_checkpoints_migration(connection)

                project_id = install.project_id
                insert_task(
                    connection,
                    task_id="tg_task_checkpoint_schema",
                    project_id=project_id,
                )
                valid = (
                    "tg_checkpoint_schema",
                    "tg_task_checkpoint_schema",
                    project_id,
                    0,
                    "Summary",
                    "Next action",
                    "[]",
                    "2026-07-27T00:00:01Z",
                )
                connection.execute(
                    """
                    INSERT INTO task_checkpoints(
                      checkpoint_id, task_id, project_id, contract_revision,
                      summary, next_action, unresolved_risks_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    valid,
                )
                for replacement in (
                    valid[:4] + ("x" * 1025,) + valid[5:],
                    valid[:5] + ("x" * 1025,) + valid[6:],
                    valid[:6] + ("x" * 24602,) + valid[7:],
                ):
                    with self.subTest(replacement=replacement[4:7]), self.assertRaises(
                        sqlite3.IntegrityError
                    ):
                        connection.execute(
                            """
                            INSERT INTO task_checkpoints(
                              checkpoint_id, task_id, project_id,
                              contract_revision, summary, next_action,
                              unresolved_risks_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"{replacement[0]}_{len(replacement[4])}_"
                                f"{len(replacement[5])}_{len(replacement[6])}",
                                *replacement[1:],
                            ),
                        )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO task_checkpoints(
                          checkpoint_id, task_id, project_id,
                          contract_revision, summary, next_action,
                          unresolved_risks_json, created_at
                        ) VALUES (
                          'tg_checkpoint_foreign', 'missing-task', ?, 0,
                          'Summary', 'Next', '[]', '2026-07-27T00:00:02Z'
                        )
                        """,
                        (project_id,),
                    )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )

    def test_v12_migration_rolls_back_each_stage_then_is_idempotent(self):
        for fail_stage in ("after_schema", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v10_database(install)
                with closing(sqlite3.connect(install.db_path)) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA foreign_keys = ON")
                    apply_managed_backup_generations_migration(connection)
                    with self.assertRaises(StorageError):
                        apply_task_checkpoints_migration(
                            connection,
                            fail_stage=fail_stage,
                        )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        11,
                    )
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table' AND name = 'task_checkpoints'
                            """
                        ).fetchone()
                    )
                    apply_task_checkpoints_migration(connection)
                    apply_task_checkpoints_migration(connection)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM schema_migrations WHERE version = 12"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM task_checkpoints"
                        ).fetchone()[0],
                        0,
                    )


class ViewerMaintenanceMigrationTests(unittest.TestCase):
    def test_v12_to_v13_seeds_bounded_state_and_tracks_task_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v12_database(install, enabled=True)
            with closing(sqlite3.connect(install.db_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(
                    """
                    UPDATE project_maintenance
                       SET viewer_last_success_at = '2026-07-27T00:00:01Z',
                           viewer_last_outcome_code = 'succeeded',
                           viewer_last_outcome_at = '2026-07-27T00:00:01Z'
                     WHERE project_id = ?
                    """,
                    (install.project_id,),
                )
                connection.commit()

                apply_viewer_maintenance_migration(connection)
                state = connection.execute(
                    """
                    SELECT project_id, source_generation, rendered_generation,
                           last_success_at, last_outcome_code, last_outcome_at
                      FROM viewer_maintenance_state
                    """
                ).fetchone()
                self.assertEqual(
                    tuple(state),
                    (
                        install.project_id,
                        0,
                        None,
                        "2026-07-27T00:00:01Z",
                        "succeeded",
                        "2026-07-27T00:00:01Z",
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT name FROM schema_migrations
                         WHERE version = 13
                        """
                    ).fetchone()["name"],
                    "viewer_maintenance_state",
                )

                insert_task(
                    connection,
                    task_id="tg_task_viewer_generation",
                    project_id=install.project_id,
                )
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id, task_id, project_id,
                      event_type, summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tg_event_viewer_generation",
                        "tg_task_viewer_generation",
                        install.project_id,
                        "task_created",
                        "Task created",
                        "2026-07-27T00:00:02Z",
                    ),
                )
                connection.commit()
                state = connection.execute(
                    """
                    SELECT source_generation, rendered_generation
                      FROM viewer_maintenance_state
                    """
                ).fetchone()
                self.assertEqual(tuple(state), (1, None))

                invalid_updates = (
                    "source_generation = -1",
                    "rendered_generation = 2",
                    (
                        "last_outcome_code = 'invalid', "
                        "last_outcome_at = '2026-07-27T00:00:03Z'"
                    ),
                    "last_outcome_at = NULL",
                    "last_success_at = 'not-a-timestamp'",
                )
                for assignment in invalid_updates:
                    with self.subTest(assignment=assignment), self.assertRaises(
                        sqlite3.IntegrityError
                    ):
                        connection.execute(
                            f"UPDATE viewer_maintenance_state SET {assignment}"
                        )
                    connection.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO viewer_maintenance_state(
                          project_id, source_generation, rendered_generation,
                          last_success_at, last_outcome_code, last_outcome_at
                        ) VALUES ('missing-project', 0, NULL, NULL, NULL, NULL)
                        """
                    )
                connection.rollback()

                apply_viewer_maintenance_migration(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version = 13"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM sqlite_master
                         WHERE type = 'trigger'
                           AND name = 'trg_task_events_viewer_generation'
                        """
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM viewer_maintenance_state"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )

    def test_v13_migration_rolls_back_each_stage_then_is_idempotent(self):
        for fail_stage in ("after_schema", "after_row", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v12_database(install)
                with closing(sqlite3.connect(install.db_path)) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA foreign_keys = ON")
                    with self.assertRaises(StorageError):
                        apply_viewer_maintenance_migration(
                            connection,
                            fail_stage=fail_stage,
                        )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        12,
                    )
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table'
                               AND name = 'viewer_maintenance_state'
                            """
                        ).fetchone()
                    )
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'trigger'
                               AND name = 'trg_task_events_viewer_generation'
                            """
                        ).fetchone()
                    )

                    apply_viewer_maintenance_migration(connection)
                    apply_viewer_maintenance_migration(connection)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM schema_migrations WHERE version = 13"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM viewer_maintenance_state"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM sqlite_master
                             WHERE type = 'trigger'
                               AND name = 'trg_task_events_viewer_generation'
                            """
                        ).fetchone()[0],
                        1,
                    )


if __name__ == "__main__":
    unittest.main()
