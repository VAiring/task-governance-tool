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


def init_db(db, repo):
    result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def table_count(db, table):
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TaskAddTests(unittest.TestCase):
    def test_task_add_registers_optional_task_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Review CLI help text",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.add")
            task = payload["data"]["task"]
            event = payload["data"]["event"]
            self.assertTrue(task["task_id"].startswith("tg_task_"))
            self.assertEqual(task["title"], "Review CLI help text")
            self.assertEqual(task["kind"], "optional")
            self.assertEqual(task["lane"], "")
            self.assertIsNone(task["lane_order"])
            self.assertEqual(task["priority"], "normal")
            self.assertEqual(task["status"], "ready")
            self.assertEqual(task["review_tier"], 1)
            self.assertEqual(task["completed_at"], None)
            self.assertEqual(event["task_id"], task["task_id"])
            self.assertEqual(event["event_type"], "task_added")
            self.assertIn("Task registered", event["summary"])

            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
                event_row = connection.execute("SELECT COUNT(*) FROM task_events").fetchone()
            self.assertEqual(row[0], 1)
            self.assertEqual(event_row[0], 1)

    def test_task_add_auto_fills_sequential_lane_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            first = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "First sequential task",
                "--kind",
                "sequential",
                "--json",
            )
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("UPDATE tasks SET lane = ' default '")
                connection.commit()
            second = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Second sequential task",
                "--kind",
                "sequential",
                "--json",
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_task = json.loads(first.stdout)["data"]["task"]
            second_task = json.loads(second.stdout)["data"]["task"]
            self.assertEqual(first_task["lane"], "default")
            self.assertEqual(first_task["lane_order"], 1)
            self.assertEqual(second_task["lane"], "default")
            self.assertEqual(second_task["lane_order"], 2)

    def test_task_add_event_summary_does_not_reject_benign_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Command output: improve formatting",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"]["task"]["title"], "Command output: improve formatting")
            self.assertEqual(payload["data"]["event"]["summary"], "Task registered")

    def test_task_add_stores_explicit_fields_and_blocked_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "SQLite schema review",
                "--description",
                "Register the review task",
                "--kind",
                "sequential",
                "--lane",
                "TG-M2",
                "--order",
                "10",
                "--priority",
                "high",
                "--status",
                "blocked",
                "--blocked-reason",
                "Waiting for user decision",
                "--review-tier",
                "2",
                "--verification",
                "python -m unittest",
                "--tags",
                "task,sqlite",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            task = json.loads(result.stdout)["data"]["task"]
            self.assertEqual(task["description"], "Register the review task")
            self.assertEqual(task["lane"], "TG-M2")
            self.assertEqual(task["lane_order"], 10)
            self.assertEqual(task["priority"], "high")
            self.assertEqual(task["status"], "blocked")
            self.assertEqual(task["blocked_reason"], "Waiting for user decision")
            self.assertEqual(task["review_tier"], 2)
            self.assertEqual(task["verification"], "python -m unittest")
            self.assertEqual(task["tags"], "task,sqlite")

    def test_task_add_rejects_blocked_without_reason_before_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Blocked task",
                "--status",
                "blocked",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "blocked_reason_required")
            self.assertEqual(table_count(db, "tasks"), 0)
            self.assertEqual(table_count(db, "task_events"), 0)

    def test_task_add_rejects_duplicate_sequential_lane_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            first = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "First",
                "--kind",
                "sequential",
                "--lane",
                "TG-M2",
                "--order",
                "1",
                "--json",
            )
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("UPDATE tasks SET lane = ' TG-M2 '")
                connection.commit()
            second = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Second",
                "--kind",
                "sequential",
                "--lane",
                "TG-M2",
                "--order",
                "1",
                "--json",
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 1)
            payload = json.loads(second.stdout)
            self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
            self.assertEqual(table_count(db, "tasks"), 1)
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_add_rejects_explicit_and_automatic_int64_overflow_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            maximum = str((1 << 63) - 1)
            seeded = run_taskgov(
                "task", "add", "--repo", str(repo), "--db", str(db),
                "--title", "At maximum", "--kind", "sequential",
                "--lane", " LIMIT ", "--order", maximum, "--json",
            )
            self.assertEqual(seeded.returncode, 0, seeded.stdout)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("UPDATE tasks SET lane = ' LIMIT '")
                connection.commit()
            before = (table_count(db, "tasks"), table_count(db, "task_events"))

            cases = (
                ("Automatic overflow", ("--lane", "LIMIT")),
                ("Explicit overflow", ("--lane", "OVER", "--order", str(1 << 63))),
                ("Huge decimal", ("--lane", "HUGE", "--order", "9" * 5000)),
            )
            for title, extra in cases:
                with self.subTest(title=title):
                    result = run_taskgov(
                        "task", "add", "--repo", str(repo), "--db", str(db),
                        "--title", title, "--kind", "sequential", *extra, "--json",
                    )
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertEqual(
                        json.loads(result.stdout)["errors"][0]["code"],
                        "invalid_argument",
                    )
                    self.assertNotIn("Traceback", result.stdout + result.stderr)
                    self.assertEqual(
                        (table_count(db, "tasks"), table_count(db, "task_events")),
                        before,
                    )

    def test_task_add_privacy_rejection_prevents_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Task",
                "--description",
                "Bearer secret",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "privacy_rejected")
            self.assertEqual(table_count(db, "tasks"), 0)
            self.assertEqual(table_count(db, "task_events"), 0)

    def test_task_add_read_only_does_not_create_or_modify_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            missing = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(missing_db),
                "--title",
                "Read only task",
                "--read-only",
                "--json",
            )

            self.assertEqual(missing.returncode, 1)
            self.assertFalse(missing_db.exists())
            self.assertFalse(missing_db.parent.exists())
            payload = json.loads(missing.stdout)
            self.assertEqual(payload["errors"][0]["code"], "invalid_argument")

            existing_db = Path(tmp) / "taskgov.sqlite"
            init_db(existing_db, repo)
            existing = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(existing_db),
                "--title",
                "Read only task",
                "--read-only",
                "--json",
            )

            self.assertEqual(existing.returncode, 1)
            self.assertEqual(table_count(existing_db, "tasks"), 0)
            self.assertEqual(table_count(existing_db, "task_events"), 0)

    def test_task_add_text_output_is_concise_and_operational(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Text output task",
                "--kind",
                "sequential",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            lines = result.stdout.strip().splitlines()
            self.assertLessEqual(len(lines), 6)
            self.assertIn("Task added: tg_task_", result.stdout)
            self.assertIn("Lane: default  Order: 1", result.stdout)

    def test_task_add_requires_explicit_db_init_without_creating_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Needs explicit init",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_task_add_reports_readiness_before_initial_done_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Already done",
                "--status",
                "done",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_task_add_rejects_initial_done_without_storing_task_or_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            before_tasks = table_count(db, "tasks")
            before_events = table_count(db, "task_events")

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "No completion bypass",
                "--status",
                "done",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "initial_done_forbidden")
            self.assertEqual(table_count(db, "tasks"), before_tasks)
            self.assertEqual(table_count(db, "task_events"), before_events)

    def test_task_add_rejects_initial_paused_without_storing_task_or_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Cannot start paused",
                "--status",
                "paused",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "initial_paused_forbidden")
            self.assertEqual(table_count(db, "tasks"), 0)
            self.assertEqual(table_count(db, "task_events"), 0)

    def test_task_add_rejects_project_mismatch_without_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            owner_repo = Path(tmp) / "owner"
            other_repo = Path(tmp) / "other"
            init_db(db, owner_repo)

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(other_repo),
                "--db",
                str(db),
                "--title",
                "Wrong project",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "project_mismatch")
            self.assertEqual(table_count(db, "tasks"), 0)
            self.assertEqual(table_count(db, "task_events"), 0)

    def test_task_add_does_not_migrate_old_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("DELETE FROM schema_migrations WHERE version = 8")
                connection.execute("DROP TABLE task_contract_revisions")
                connection.execute(
                    "ALTER TABLE tasks DROP COLUMN current_contract_revision"
                )
                connection.commit()
            before = db.read_bytes()

            result = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Must migrate explicitly",
                "--status",
                "done",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "migration_required")
            self.assertEqual(db.read_bytes(), before)
            with closing(sqlite3.connect(db)) as connection:
                versions = [row[0] for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )]
                task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            self.assertEqual(versions, [1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(task_count, 0)


if __name__ == "__main__":
    unittest.main()
