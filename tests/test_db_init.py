import json
import sqlite3
import subprocess
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
        StorageError,
        apply_completion_commit_migration,
        apply_completion_evidence_migration,
        apply_initial_schema_migration,
        apply_paused_state_migration,
        apply_review_evidence_migration,
        connect_initialized,
        ensure_project_meta,
        initial_schema_sql,
        project_identity,
        resolve_database_target,
    )
finally:
    sys.path.pop(0)


def run_taskgov(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
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


class DbInitTests(unittest.TestCase):
    def test_db_init_creates_temp_database_with_json_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "db.init")
            self.assertTrue(payload["data"]["created"])
            self.assertEqual(payload["data"]["migrations_applied"], [1, 2, 3, 4, 5])
            self.assertEqual(payload["data"]["schema_version"], 5)
            self.assertEqual(Path(payload["db_path"]), db.resolve())
            self.assertTrue(db.exists())
            default_db = (
                SKILL_ROOT.resolve()
                / "state"
                / "projects"
                / payload["project_id"]
                / "taskgov.sqlite"
            )
            self.assertFalse(default_db.exists())
            self.assertFalse(default_db.parent.exists())

            with closing(sqlite3.connect(db)) as connection:
                version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                project_count = connection.execute("SELECT COUNT(*) FROM project_meta").fetchone()[0]
            self.assertEqual(version, 5)
            self.assertEqual(project_count, 1)

    def test_db_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            first = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
            second = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            payload = json.loads(second.stdout)
            self.assertFalse(payload["data"]["created"])
            self.assertEqual(payload["data"]["migrations_applied"], [])
            self.assertEqual(payload["data"]["schema_version"], 5)

    def test_db_init_migrates_schema_v1_database_through_paused_state(self):
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

            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["data"]["created"])
            self.assertEqual(payload["data"]["migrations_applied"], [2, 3, 4, 5])
            self.assertEqual(payload["data"]["schema_version"], 5)
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
            self.assertEqual(versions, [1, 2, 3, 4, 5])
            self.assertEqual(task["completion_commit_required"], 1)
            self.assertEqual(task["completion_commit_hash"], "")
            self.assertEqual(task["pause_reason"], "")

    def test_db_init_migrates_v2_tasks_and_events_to_paused_state_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            create_v2_database(db, repo)

            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"]["migrations_applied"], [3, 4, 5])
            self.assertEqual(payload["data"]["schema_version"], 5)
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
            self.assertEqual(versions, [1, 2, 3, 4, 5])
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

    def test_db_init_warns_when_inconsistent_legacy_evidence_is_preserved(self):
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

            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 0, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"]["migrations_applied"], [4, 5])
            self.assertEqual(payload["warnings"][0]["code"], "legacy_completion_evidence_preserved")
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    """
                    SELECT completion_commit_required, completion_commit_hash,
                           completion_evidence_kind, completion_evidence_revision
                      FROM tasks
                    """
                ).fetchone()
            self.assertEqual(row, (0, "preserved-revision", "legacy_unverified", "preserved-revision"))

    def test_db_init_returns_json_error_for_invalid_db_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "directory-instead-of-db"
            db.mkdir()
            repo = Path(tmp) / "repo"

            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["command"], "db.init")
            self.assertEqual(payload["errors"][0]["code"], "internal_error")
            self.assertIn("database", payload["errors"][0]["message"])

    def test_db_init_read_only_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "readonly" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov(
                "db",
                "init",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--read-only",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_db_init_creates_required_tables_and_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(db)) as connection:
                table_rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
                index_rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
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
            self.assertTrue(
                {
                    "schema_migrations",
                    "project_meta",
                    "tasks",
                    "task_events",
                    "tool_events",
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
                }.issubset(indexes)
            )
            self.assertIn("CREATE UNIQUE INDEX", unique_index_sql.upper())
            self.assertIn("WHERE kind = 'sequential'", unique_index_sql)
            with closing(sqlite3.connect(db)) as connection:
                column_rows = connection.execute("PRAGMA table_info(tasks)").fetchall()
            task_columns = {row[1] for row in column_rows}
            self.assertIn("completion_commit_required", task_columns)
            self.assertIn("completion_commit_hash", task_columns)
            self.assertIn("completion_evidence_kind", task_columns)
            self.assertIn("completion_evidence_revision", task_columns)
            self.assertIn("completion_evidence_reason", task_columns)
            self.assertIn("external_revision_approved", task_columns)

    def test_project_mismatch_is_rejected_for_explicit_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo_one = Path(tmp) / "repo-one"
            repo_two = Path(tmp) / "repo-two"

            first = run_taskgov("db", "init", "--repo", str(repo_one), "--db", str(db), "--json")
            second = run_taskgov("db", "init", "--repo", str(repo_two), "--db", str(db), "--json")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 2)
            payload = json.loads(second.stdout)
            self.assertEqual(payload["errors"][0]["code"], "project_mismatch")

    def test_project_mismatch_does_not_migrate_an_owned_v2_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            owner_repo = Path(tmp) / "owner"
            other_repo = Path(tmp) / "other"
            create_v2_database(db, owner_repo)

            result = run_taskgov("db", "init", "--repo", str(other_repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["errors"][0]["code"], "project_mismatch")
            with closing(sqlite3.connect(db)) as connection:
                versions = [
                    row[0] for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                task_columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
            self.assertEqual(versions, [1, 2])
            self.assertNotIn("pause_reason", task_columns)

    def test_initialized_write_connection_holds_lock_after_readiness_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            initialized = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )

            with closing(connect_initialized(target)) as connection:
                self.assertTrue(connection.in_transaction)
                with closing(sqlite3.connect(db, timeout=0)) as contender:
                    with self.assertRaises(sqlite3.OperationalError):
                        contender.execute("BEGIN IMMEDIATE")

            with closing(sqlite3.connect(db, timeout=0)) as contender:
                contender.execute("BEGIN IMMEDIATE")
                contender.rollback()

    def test_schema_constraints_reject_invalid_task_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)

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


if __name__ == "__main__":
    unittest.main()
