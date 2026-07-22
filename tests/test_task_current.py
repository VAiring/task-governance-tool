import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.review_test_helpers import seed_review_evidence


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


def add_task(db, repo, title, *extra):
    result = run_taskgov(
        "task", "add", "--repo", str(repo), "--db", str(db), "--title", title, *extra, "--json"
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


def edit_task(db, repo, task_id, *extra):
    if "--status" in extra and extra[extra.index("--status") + 1] == "done":
        seed_review_evidence(db, task_id)
    result = run_taskgov(
        "task", "edit", "--repo", str(repo), "--db", str(db), task_id, *extra, "--json"
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


def current(db, repo, *extra):
    return run_taskgov(
        "task", "current", "--repo", str(repo), "--db", str(db), *extra, "--json"
    )


def insert_current_row(connection, project_id, task_id, title, *, priority="normal", updated_at="2026-07-01T00:00:00Z"):
    connection.execute(
        """
        INSERT INTO tasks(
          task_id, project_id, title, kind, priority, status, review_tier,
          created_at, updated_at
        ) VALUES (?, ?, ?, 'optional', ?, 'in_progress', 1, ?, ?)
        """,
        (task_id, project_id, title, priority, updated_at, updated_at),
    )


def seed_current_states(db, repo):
    in_progress = add_task(db, repo, "Implement feature", "--status", "in_progress", "--priority", "low")
    review = add_task(db, repo, "Review feature", "--status", "review_pending", "--priority", "urgent")
    paused = add_task(db, repo, "Paused feature", "--status", "in_progress", "--priority", "high")
    paused = edit_task(
        db, repo, paused["task_id"], "--status", "paused", "--pause-reason", "Waiting for a safe window"
    )
    blocked = add_task(
        db, repo, "Blocked feature", "--status", "blocked", "--blocked-reason", "User decision needed",
        "--priority", "urgent",
    )
    add_task(db, repo, "Ready excluded")
    done = add_task(db, repo, "Done excluded")
    edit_task(
        db, repo, done["task_id"], "--status", "done", "--verification-complete",
        "--review-complete", "--commit-not-required",
    )
    add_task(db, repo, "Cancelled excluded", "--status", "cancelled")
    return in_progress, review, paused, blocked


class TaskCurrentTests(unittest.TestCase):
    def test_current_returns_only_resume_states_in_deterministic_status_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            seed_current_states(db, repo)

            result = current(db, repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["command"], "task.current")
            data = payload["data"]
            self.assertEqual(
                [task["status"] for task in data["tasks"]],
                ["in_progress", "review_pending", "paused", "blocked"],
            )
            self.assertEqual(data["count"], 4)
            self.assertEqual(data["limit"], 20)
            self.assertEqual(
                data["statuses"],
                ["in_progress", "review_pending", "paused", "blocked"],
            )
            self.assertIn("Waiting for a safe window", data["tasks"][2]["suggested_next_action"])
            self.assertIn("User decision needed", data["tasks"][3]["suggested_next_action"])

    def test_current_orders_priority_updated_at_and_task_id_and_honors_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                project_id = connection.execute("SELECT project_id FROM project_meta").fetchone()[0]
                insert_current_row(
                    connection, project_id, "tg_task_urgent_old", "Older urgent",
                    priority="urgent", updated_at="2026-07-01T00:00:00Z",
                )
                insert_current_row(
                    connection, project_id, "tg_task_urgent_new", "Newer urgent",
                    priority="urgent", updated_at="2026-07-02T00:00:00Z",
                )
                insert_current_row(
                    connection, project_id, "tg_task_high_newest", "High newest",
                    priority="high", updated_at="2026-07-03T00:00:00Z",
                )
                insert_current_row(
                    connection, project_id, "tg_task_tie_beta", "Tie beta",
                    priority="normal", updated_at="2026-07-04T00:00:00Z",
                )
                insert_current_row(
                    connection, project_id, "tg_task_tie_alpha", "Tie alpha",
                    priority="normal", updated_at="2026-07-04T00:00:00Z",
                )
                connection.commit()

            result = current(db, repo, "--limit", "5")

            tasks = json.loads(result.stdout)["data"]["tasks"]
            self.assertEqual(
                [task["task_id"] for task in tasks],
                [
                    "tg_task_urgent_new",
                    "tg_task_urgent_old",
                    "tg_task_high_newest",
                    "tg_task_tie_alpha",
                    "tg_task_tie_beta",
                ],
            )
            self.assertEqual(json.loads(result.stdout)["data"]["limit"], 5)

    def test_current_empty_shape_and_limit_cap_are_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            empty = current(db, repo)
            self.assertEqual(
                json.loads(empty.stdout)["data"],
                {
                    "tasks": [],
                    "count": 0,
                    "limit": 20,
                    "statuses": ["in_progress", "review_pending", "paused", "blocked"],
                },
            )

            with closing(sqlite3.connect(db)) as connection:
                project_id = connection.execute("SELECT project_id FROM project_meta").fetchone()[0]
                for index in range(101):
                    insert_current_row(
                        connection,
                        project_id,
                        f"tg_task_bulk_{index:03d}",
                        f"Bulk {index:03d}",
                    )
                connection.commit()

            capped = current(db, repo, "--limit", "1000")
            data = json.loads(capped.stdout)["data"]
            self.assertEqual(data["limit"], 100)
            self.assertEqual(data["count"], 100)
            self.assertEqual(len(data["tasks"]), 100)

    def test_current_uses_same_second_rowid_tie_break_for_latest_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(db, repo, "Event task", "--status", "in_progress")
            eventless = add_task(db, repo, "Eventless task", "--status", "in_progress")
            with closing(sqlite3.connect(db)) as connection:
                project_id = connection.execute(
                    "SELECT project_id FROM tasks WHERE task_id = ?", (task["task_id"],)
                ).fetchone()[0]
                for event_id, summary in (("tg_event_first", "First"), ("tg_event_second", "Second")):
                    connection.execute(
                        """
                        INSERT INTO task_events(
                          task_event_id, task_id, project_id, event_type, summary, created_at
                        ) VALUES (?, ?, ?, 'note_added', ?, '2999-01-01T00:00:00Z')
                        """,
                        (event_id, task["task_id"], project_id, summary),
                    )
                connection.execute("DELETE FROM task_events WHERE task_id = ?", (eventless["task_id"],))
                connection.commit()

            result = current(db, repo)

            tasks = json.loads(result.stdout)["data"]["tasks"]
            latest = next(item for item in tasks if item["task_id"] == task["task_id"])["latest_event"]
            self.assertEqual(latest["task_event_id"], "tg_event_second")
            self.assertEqual(latest["summary"], "Second")
            self.assertEqual(
                next(item for item in tasks if item["task_id"] == eventless["task_id"])["latest_event"],
                {},
            )

    def test_current_text_is_compact_and_operational(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(db, repo, "Continue this", "--status", "in_progress")

            result = run_taskgov("task", "current", "--repo", str(repo), "--db", str(db))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLessEqual(len(result.stdout.strip().splitlines()), 2)
            self.assertIn("Current tasks: 1 (limit 20)", result.stdout)
            self.assertIn("continue the task and inspect its latest event", result.stdout)

    def test_current_is_read_only_for_database_and_target_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo-not-created"
            init_db(db, repo)
            add_task(db, repo, "Read only current", "--status", "in_progress")
            before = db.read_bytes()

            result = current(db, repo, "--read-only")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(db.read_bytes(), before)
            self.assertFalse(Path(str(db) + "-wal").exists())
            self.assertFalse(Path(str(db) + "-shm").exists())
            self.assertFalse(repo.exists())

    def test_current_readiness_errors_and_invalid_limit_do_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            missing_result = current(missing, repo)
            self.assertEqual(json.loads(missing_result.stdout)["errors"][0]["code"], "db_not_initialized")
            self.assertFalse(missing.parent.exists())

            db = Path(tmp) / "taskgov.sqlite"
            init_db(db, repo)
            for invalid_limit in ("0", "²"):
                with self.subTest(invalid_limit=invalid_limit):
                    before_invalid = db.read_bytes()
                    invalid = current(db, repo, "--limit", invalid_limit)
                    self.assertEqual(
                        json.loads(invalid.stdout)["errors"][0]["code"],
                        "invalid_argument",
                    )
                    self.assertEqual(db.read_bytes(), before_invalid)

            with closing(sqlite3.connect(db)) as connection:
                connection.execute("DELETE FROM schema_migrations WHERE version = 5")
                connection.commit()
            before_migration = db.read_bytes()
            migration = current(db, repo)
            self.assertEqual(json.loads(migration.stdout)["errors"][0]["code"], "migration_required")
            self.assertEqual(db.read_bytes(), before_migration)

    def test_current_project_mismatch_does_not_mutate_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            owner = Path(tmp) / "owner"
            other = Path(tmp) / "other"
            init_db(db, owner)
            before = db.read_bytes()

            result = current(db, other)

            self.assertEqual(json.loads(result.stdout)["errors"][0]["code"], "project_mismatch")
            self.assertEqual(db.read_bytes(), before)
            self.assertFalse(other.exists())


if __name__ == "__main__":
    unittest.main()
