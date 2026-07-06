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
            self.assertEqual(payload["data"]["migrations_applied"], [1])
            self.assertEqual(payload["data"]["schema_version"], 1)
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
            self.assertEqual(version, 1)
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
            self.assertEqual(payload["data"]["schema_version"], 1)

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
                }.issubset(indexes)
            )
            self.assertIn("CREATE UNIQUE INDEX", unique_index_sql.upper())
            self.assertIn("WHERE kind = 'sequential'", unique_index_sql)

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

    def test_schema_constraints_reject_invalid_task_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)

            with closing(sqlite3.connect(db)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    insert_task(connection, status="blocked", blocked_reason="")
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


if __name__ == "__main__":
    unittest.main()
