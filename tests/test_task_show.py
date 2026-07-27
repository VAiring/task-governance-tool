import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def init_db(db, repo):
    return initialize_taskgov_internal(repo=repo, db=db)


def add_task(db, repo, title, *extra):
    if not db.exists():
        init_db(db, repo)
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


def show_task(db, repo, task_id, *extra):
    result = run_taskgov(
        "task",
        "show",
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


def table_count(db, table):
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TaskShowTests(unittest.TestCase):
    def test_task_show_returns_task_events_and_suggested_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(
                db,
                repo,
                "Show task detail",
                "--description",
                "Inspect one task",
                "--kind",
                "sequential",
                "--lane",
                "TG-M2",
                "--order",
                "20",
                "--priority",
                "high",
                "--review-tier",
                "2",
                "--verification",
                "python -m unittest tests.test_task_show",
                "--tags",
                "show,task",
            )
            event_count_before = table_count(db, "task_events")

            payload = show_task(db, repo, task["task_id"], "--read-only")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.show")
            data = payload["data"]
            self.assertEqual(data["task"]["task_id"], task["task_id"])
            self.assertEqual(data["task"]["title"], "Show task detail")
            self.assertEqual(data["task"]["lane"], "TG-M2")
            self.assertEqual(data["task"]["lane_order"], 20)
            self.assertEqual(data["task"]["review_tier"], 2)
            self.assertEqual(data["task"]["completion_commit_required"], 1)
            self.assertEqual(data["task"]["completion_commit_hash"], "")
            self.assertEqual(data["task"]["completion_evidence_kind"], "none")
            self.assertEqual(data["task"]["completion_evidence_revision"], "")
            self.assertEqual(data["task"]["completion_evidence_reason"], "")
            self.assertEqual(data["task"]["external_revision_approved"], 0)
            self.assertFalse(data["effort_advisory_enabled"])
            self.assertIn("created_at", data["task"])
            self.assertIn("updated_at", data["task"])
            self.assertEqual(len(data["events"]), 1)
            self.assertEqual(data["events"][0]["task_id"], task["task_id"])
            self.assertEqual(data["events"][0]["event_type"], "task_added")
            self.assertIn("Start work", data["suggested_next_action"])
            self.assertNotIn("task edit", data["suggested_next_action"])
            self.assertEqual(table_count(db, "task_events"), event_count_before)

    def test_task_show_orders_same_timestamp_events_by_insert_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Event ordering")
            timestamp = "2999-01-01T00:00:00Z"
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id,
                      task_id,
                      project_id,
                      event_type,
                      summary,
                      created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tg_event_same_second_first",
                        task["task_id"],
                        task["project_id"],
                        "note_added",
                        "First same-second event",
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id,
                      task_id,
                      project_id,
                      event_type,
                      summary,
                      created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tg_event_same_second_second",
                        task["task_id"],
                        task["project_id"],
                        "note_added",
                        "Second same-second event",
                        timestamp,
                    ),
                )
                connection.commit()

            payload = show_task(db, repo, task["task_id"])

            summaries = [event["summary"] for event in payload["data"]["events"][:2]]
            self.assertEqual(summaries, ["Second same-second event", "First same-second event"])

    def test_task_show_suggests_blocker_action_for_blocked_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(
                db,
                repo,
                "Blocked task",
                "--status",
                "blocked",
                "--blocked-reason",
                "Waiting for input",
            )

            payload = show_task(db, repo, task["task_id"])

            self.assertEqual(payload["data"]["task"]["blocked_reason"], "Waiting for input")
            self.assertIn("Resolve the blocker", payload["data"]["suggested_next_action"])

    def test_task_show_unknown_task_returns_structured_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            result = run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "tg_task_missing",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["command"], "task.show")
            self.assertEqual(payload["errors"][0]["code"], "not_found")
            self.assertEqual(payload["data"], {
                "task": None,
                "events": [],
                "suggested_next_action": "",
                "review_evidence": None,
                "handoff_summary": None,
                "contract": None,
            })

    def test_task_show_missing_db_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "tg_task_missing",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertEqual(payload["data"], {
                "task": None,
                "events": [],
                "suggested_next_action": "",
                "review_evidence": None,
                "handoff_summary": None,
                "contract": None,
            })
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_task_show_reports_migration_required_without_migrating(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            with closing(sqlite3.connect(db)):
                pass

            result = run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "tg_task_missing",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "migration_required")
            with closing(sqlite3.connect(db)) as connection:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            self.assertEqual(tables, [])

    def test_task_show_reports_project_mismatch_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo_one = Path(tmp) / "repo-one"
            repo_two = Path(tmp) / "repo-two"
            init_payload = init_db(db, repo_one)

            result = run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo_two),
                "--db",
                str(db),
                "tg_task_missing",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "project_mismatch")
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute("SELECT project_id FROM project_meta").fetchone()
            self.assertEqual(row[0], init_payload["project_id"])

    def test_task_show_text_output_is_concise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Ready optional")

            result = run_taskgov("task", "show", "--repo", str(repo), "--db", str(db), task["task_id"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            lines = result.stdout.strip().splitlines()
            self.assertLessEqual(len(lines), 9)
            self.assertIn(f"Task: {task['task_id']}", result.stdout)
            self.assertIn("Title: Ready optional", result.stdout)
            self.assertIn("Completion evidence: none", result.stdout)
            self.assertIn("Latest event: task_added - Task registered", result.stdout)


if __name__ == "__main__":
    unittest.main()
