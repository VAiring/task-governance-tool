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


def add_task(db, repo, title, *extra):
    result = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        *extra,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


def edit_task(db, repo, task_id, *extra):
    result = run_taskgov(
        "task",
        "edit",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        *extra,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def fetch_task(db, task_id):
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row)


def table_count(db, table):
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TaskEditTests(unittest.TestCase):
    def test_task_edit_updates_metadata_and_records_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Original title")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--title",
                "Updated title",
                "--description",
                "Updated description",
                "--priority",
                "high",
                "--review-tier",
                "2",
                "--verification",
                "python -m unittest tests.test_task_edit",
                "--tags",
                "edit,task",
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.edit")
            data = payload["data"]
            self.assertEqual(data["task"]["title"], "Updated title")
            self.assertEqual(data["task"]["description"], "Updated description")
            self.assertEqual(data["task"]["priority"], "high")
            self.assertEqual(data["task"]["review_tier"], 2)
            self.assertEqual(data["task"]["verification"], "python -m unittest tests.test_task_edit")
            self.assertEqual(data["task"]["tags"], "edit,task")
            self.assertEqual(data["event"]["event_type"], "task_updated")
            self.assertIn("title", data["changed_fields"])
            self.assertIn("review_tier", data["changed_fields"])
            self.assertEqual(table_count(db, "task_events"), 2)

    def test_task_edit_status_done_sets_and_clears_completed_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Status task")

            done = edit_task(db, repo, task["task_id"], "--status", "done")
            completed_at = done["data"]["task"]["completed_at"]

            self.assertIsNotNone(completed_at)
            self.assertIn("status", done["data"]["changed_fields"])
            self.assertIn("completed_at", done["data"]["changed_fields"])

            ready = edit_task(db, repo, task["task_id"], "--status", "ready")

            self.assertIsNone(ready["data"]["task"]["completed_at"])
            self.assertEqual(ready["data"]["task"]["status"], "ready")
            self.assertIn("completed_at", ready["data"]["changed_fields"])

    def test_task_edit_blocked_status_requires_reason_and_can_clear_on_unblock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Blockable task")

            missing_reason = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "blocked",
                "--json",
            )

            self.assertEqual(missing_reason.returncode, 1)
            payload = json.loads(missing_reason.stdout)
            self.assertEqual(payload["errors"][0]["code"], "blocked_reason_required")
            self.assertEqual(fetch_task(db, task["task_id"])["status"], "ready")
            self.assertEqual(table_count(db, "task_events"), 1)

            blocked = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "blocked",
                "--blocked-reason",
                "Waiting for user decision",
            )
            unblocked = edit_task(db, repo, task["task_id"], "--status", "ready")

            self.assertEqual(blocked["data"]["task"]["blocked_reason"], "Waiting for user decision")
            self.assertEqual(unblocked["data"]["task"]["blocked_reason"], "")

    def test_task_edit_add_note_records_note_event_without_changed_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Note task")

            payload = edit_task(db, repo, task["task_id"], "--add-note", "Checked current blocker")

            self.assertEqual(payload["data"]["changed_fields"], [])
            self.assertEqual(payload["data"]["event"]["event_type"], "note_added")
            self.assertEqual(payload["data"]["event"]["summary"], "Note added: Checked current blocker")
            self.assertEqual(table_count(db, "task_events"), 2)

    def test_task_edit_accepts_note_up_to_2000_chars_with_concise_event_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Long note task")

            payload = edit_task(db, repo, task["task_id"], "--add-note", "x" * 1500)

            summary = payload["data"]["event"]["summary"]
            self.assertEqual(payload["data"]["event"]["event_type"], "note_added")
            self.assertLessEqual(len(summary), 1000)
            self.assertTrue(summary.startswith("Note added: "))
            self.assertTrue(summary.endswith("..."))
            self.assertEqual(table_count(db, "task_events"), 2)

    def test_task_edit_rejects_oversized_note_without_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Note size task")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--add-note",
                "x" * 2001,
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_privacy_rejection_prevents_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Privacy task")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--description",
                "Bearer secret",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "privacy_rejected")
            self.assertEqual(fetch_task(db, task["task_id"])["description"], "")
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_read_only_does_not_create_or_modify_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            missing = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(missing_db),
                "tg_task_missing",
                "--title",
                "No write",
                "--read-only",
                "--json",
            )

            self.assertEqual(missing.returncode, 1)
            self.assertFalse(missing_db.exists())
            self.assertFalse(missing_db.parent.exists())
            self.assertEqual(json.loads(missing.stdout)["errors"][0]["code"], "invalid_argument")

            db = Path(tmp) / "taskgov.sqlite"
            task = add_task(db, repo, "Read only task")
            existing = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--title",
                "Should not change",
                "--read-only",
                "--json",
            )

            self.assertEqual(existing.returncode, 1)
            self.assertEqual(fetch_task(db, task["task_id"])["title"], "Read only task")
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_missing_db_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "tg_task_missing",
                "--title",
                "No database",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertEqual(payload["data"], {"task": None, "changed_fields": [], "event": None})
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_task_edit_duplicate_sequential_order_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            first = add_task(
                db,
                repo,
                "First sequential",
                "--kind",
                "sequential",
                "--lane",
                "TG-M2",
                "--order",
                "1",
            )
            second = add_task(
                db,
                repo,
                "Second sequential",
                "--kind",
                "sequential",
                "--lane",
                "TG-M2",
                "--order",
                "2",
            )

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                second["task_id"],
                "--order",
                "1",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
            self.assertEqual(fetch_task(db, first["task_id"])["lane_order"], 1)
            self.assertEqual(fetch_task(db, second["task_id"])["lane_order"], 2)
            self.assertEqual(table_count(db, "task_events"), 2)

    def test_task_edit_unknown_task_returns_structured_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "tg_task_missing",
                "--title",
                "Missing",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "not_found")
            self.assertEqual(payload["data"], {"task": None, "changed_fields": [], "event": None})
            self.assertEqual(table_count(db, "task_events"), 0)

    def test_task_edit_text_output_is_concise_and_operational(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Text edit task")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--priority",
                "high",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            lines = result.stdout.strip().splitlines()
            self.assertLessEqual(len(lines), 5)
            self.assertIn(f"Task updated: {task['task_id']}", result.stdout)
            self.assertIn("Changed: priority", result.stdout)


if __name__ == "__main__":
    unittest.main()
