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
    from task_governance_tool.storage import initial_schema_sql, project_identity
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


def task_row(project_id, **overrides):
    row = {
        "task_id": "tg_task_test",
        "project_id": project_id,
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


def insert_task(connection, project_id, **overrides):
    row = task_row(project_id, **overrides)
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


def init_db(db, repo):
    result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


class DbStatusTests(unittest.TestCase):
    def test_missing_explicit_db_status_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["command"], "db.status")
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertEqual(
                payload["data"],
                {
                    "exists": False,
                    "needs_init": True,
                    "needs_migration": False,
                    "schema_version": None,
                    "counts": {
                        "active": 0,
                        "paused": 0,
                        "blocked": 0,
                        "review_pending": 0,
                        "done": 0,
                        "next_actionable": 0,
                        "handoff_pending": 0,
                    },
                    "handoff_delivery": {
                        "adapter_enabled": False,
                        "sync_due": False,
                    },
                },
            )

    def test_missing_default_db_status_does_not_create_skill_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"

            result = run_taskgov("db", "status", "--repo", str(repo), "--json")

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            db_path = Path(payload["db_path"])
            self.assertFalse(db_path.exists())
            self.assertFalse(db_path.parent.exists())

    def test_initialized_db_status_returns_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_payload = init_db(db, repo)
            project_id = init_payload["project_id"]
            with closing(sqlite3.connect(db)) as connection:
                insert_task(connection, project_id, task_id="tg_task_ready")
                insert_task(
                    connection,
                    project_id,
                    task_id="tg_task_in_progress",
                    status="in_progress",
                )
                insert_task(
                    connection,
                    project_id,
                    task_id="tg_task_blocked",
                    kind="sequential",
                    lane="blocked-lane",
                    lane_order=1,
                    status="blocked",
                    blocked_reason="waiting",
                )
                insert_task(
                    connection,
                    project_id,
                    task_id="tg_task_review",
                    status="review_pending",
                )
                insert_task(
                    connection,
                    project_id,
                    task_id="tg_task_done",
                    status="done",
                    completed_at="2026-07-06T00:00:01Z",
                )
                insert_task(
                    connection,
                    project_id,
                    task_id="tg_task_lane_done",
                    kind="sequential",
                    lane="ready-lane",
                    lane_order=1,
                    status="done",
                    completed_at="2026-07-06T00:00:01Z",
                )
                insert_task(
                    connection,
                    project_id,
                    task_id="tg_task_lane_next",
                    kind="sequential",
                    lane="ready-lane",
                    lane_order=2,
                )
                insert_task(
                    connection,
                    project_id,
                    task_id="tg_task_blocked_lane_later",
                    kind="sequential",
                    lane="blocked-lane",
                    lane_order=2,
                )
                connection.commit()

            result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "db.status")
            self.assertEqual(payload["data"]["exists"], True)
            self.assertEqual(payload["data"]["needs_init"], False)
            self.assertEqual(payload["data"]["needs_migration"], False)
            self.assertEqual(payload["data"]["schema_version"], 8)
            self.assertEqual(
                payload["data"]["counts"],
                {
                    "active": 6,
                    "paused": 0,
                    "blocked": 1,
                    "review_pending": 1,
                    "done": 2,
                    "next_actionable": 2,
                    "handoff_pending": 0,
                },
            )
            self.assertEqual(
                payload["data"]["handoff_delivery"],
                {"adapter_enabled": False, "sync_due": False},
            )

    def test_migration_required_status_does_not_migrate_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            with closing(sqlite3.connect(db)):
                pass

            result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "migration_required")
            self.assertTrue(payload["data"]["exists"])
            self.assertFalse(payload["data"]["needs_init"])
            self.assertTrue(payload["data"]["needs_migration"])
            self.assertEqual(payload["data"]["schema_version"], 0)
            self.assertEqual(
                payload["data"]["counts"],
                {
                    "active": 0,
                    "paused": 0,
                    "blocked": 0,
                    "review_pending": 0,
                    "done": 0,
                    "next_actionable": 0,
                    "handoff_pending": 0,
                },
            )
            with closing(sqlite3.connect(db)) as connection:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            self.assertEqual(tables, [])

    def test_schema_v1_status_reports_migration_required_without_migrating(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            project = project_identity(repo)
            with closing(sqlite3.connect(db)) as connection:
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
                insert_task(connection, project.project_id)
                connection.commit()

            result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "migration_required")
            self.assertTrue(payload["data"]["needs_migration"])
            self.assertEqual(payload["data"]["schema_version"], 1)
            self.assertEqual(payload["data"]["counts"]["paused"], 0)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                versions = [
                    row["version"]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
                }
            self.assertEqual(versions, [1])
            self.assertNotIn("completion_commit_required", columns)
            self.assertNotIn("completion_commit_hash", columns)

    def test_project_mismatch_status_is_reported_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo_one = Path(tmp) / "repo-one"
            repo_two = Path(tmp) / "repo-two"
            init_payload = init_db(db, repo_one)
            before_bytes = db.read_bytes()
            before_entries = sorted(path.name for path in db.parent.iterdir())

            result = run_taskgov("db", "status", "--repo", str(repo_two), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "project_mismatch")
            self.assertEqual(
                payload["data"]["counts"],
                {
                    "active": 0,
                    "paused": 0,
                    "blocked": 0,
                    "review_pending": 0,
                    "done": 0,
                    "next_actionable": 0,
                    "handoff_pending": 0,
                },
            )
            self.assertEqual(db.read_bytes(), before_bytes)
            self.assertEqual(
                sorted(path.name for path in db.parent.iterdir()),
                before_entries,
            )
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute("SELECT project_id FROM project_meta").fetchone()
            self.assertEqual(row[0], init_payload["project_id"])

    def test_status_does_not_create_wal_sidecar_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(db) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            before = sorted(path.name for path in db.parent.iterdir())

            result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            after = sorted(path.name for path in db.parent.iterdir())
            self.assertEqual(after, before)
            self.assertEqual(after, ["taskgov.sqlite"])

    def test_status_rejects_existing_wal_sidecar_instead_of_returning_stale_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_payload = init_db(db, repo)
            project_id = init_payload["project_id"]
            with closing(sqlite3.connect(db)) as connection:
                journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                self.assertEqual(journal_mode.lower(), "wal")
                insert_task(connection, project_id, task_id="tg_task_uncheckpointed")
                connection.commit()
                sidecars = sorted(
                    path.name
                    for path in db.parent.iterdir()
                    if path.name.startswith("taskgov.sqlite-")
                )
                self.assertIn("taskgov.sqlite-wal", sidecars)
                before = sorted(path.name for path in db.parent.iterdir())

                result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")

                after = sorted(path.name for path in db.parent.iterdir())

            self.assertEqual(result.returncode, 2)
            self.assertEqual(after, before)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "internal_error")
            self.assertIn("WAL sidecar", payload["errors"][0]["message"])
            self.assertEqual(payload["data"]["counts"]["active"], 0)
            self.assertEqual(payload["data"]["counts"]["paused"], 0)

    def test_status_counts_all_paused_tasks_without_changing_active_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            project_id = init_db(db, repo)["project_id"]
            with closing(sqlite3.connect(db)) as connection:
                for index in range(25):
                    insert_task(
                        connection,
                        project_id,
                        task_id=f"tg_task_paused_{index:02d}",
                        status="in_progress",
                    )
                    connection.execute(
                        """
                        UPDATE tasks
                           SET status = 'paused',
                               pause_reason = 'scheduled hold'
                         WHERE task_id = ?
                        """,
                        (f"tg_task_paused_{index:02d}",),
                    )
                connection.commit()
            before_bytes = db.read_bytes()
            before_entries = sorted(path.name for path in db.parent.iterdir())

            result = run_taskgov(
                "db",
                "status",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            counts = json.loads(result.stdout)["data"]["counts"]
            self.assertEqual(counts["paused"], 25)
            self.assertEqual(counts["active"], 25)
            self.assertEqual(db.read_bytes(), before_bytes)
            self.assertEqual(
                sorted(path.name for path in db.parent.iterdir()),
                before_entries,
            )

    def test_status_text_output_is_concise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            lines = result.stdout.strip().splitlines()
            self.assertLessEqual(len(lines), 7)
            self.assertIn("Status: ready", result.stdout)
            self.assertIn("Paused: 0", result.stdout)
            self.assertIn("Next actionable: 0", result.stdout)
            self.assertIn("Pending handoffs: 0", result.stdout)

    def test_status_returns_json_error_for_invalid_db_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "directory-instead-of-db"
            db.mkdir()
            repo = Path(tmp) / "repo"

            result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "internal_error")


if __name__ == "__main__":
    unittest.main()
