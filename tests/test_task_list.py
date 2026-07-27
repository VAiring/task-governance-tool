import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.review_test_helpers import seed_review_evidence
from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"


def run_taskgov(*args):
    return run_taskgov_internal(*args)


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


def init_db(db, repo):
    return initialize_taskgov_internal(repo=repo, db=db)


def list_tasks(db, repo, *extra):
    result = run_taskgov(
        "task",
        "list",
        "--repo",
        str(repo),
        "--db",
        str(db),
        *extra,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


class TaskListTests(unittest.TestCase):
    def seed_tasks(self, db, repo):
        seeded = {
            "ready_optional": add_task(
                db,
                repo,
                "Ready optional",
                "--priority",
                "high",
                "--tags",
                "ui,ready",
            ),
            "blocked_optional": add_task(
                db,
                repo,
                "Blocked optional",
                "--status",
                "blocked",
                "--blocked-reason",
                "Waiting",
                "--tags",
                "blocked",
            ),
            "sequential": add_task(
                db,
                repo,
                "Sequential ready",
                "--kind",
                "sequential",
                "--lane",
                "TG-M2",
                "--order",
                "10",
                "--priority",
                "urgent",
                "--tags",
                "backend,ready",
            ),
            "done": add_task(
                db,
                repo,
                "Done task",
                "--tags",
                "archive",
            ),
            "cancelled": add_task(
                db,
                repo,
                "Cancelled task",
                "--status",
                "cancelled",
                "--tags",
                "archive",
            ),
        }
        seed_review_evidence(db, seeded["done"]["task_id"])
        done_result = run_taskgov(
            "task",
            "edit",
            "--repo",
            str(repo),
            "--db",
            str(db),
            seeded["done"]["task_id"],
            "--status",
            "done",
            "--verification-complete",
            "--review-complete",
            "--commit-not-required",
            "--json",
        )
        if done_result.returncode != 0:
            raise AssertionError(done_result.stderr or done_result.stdout)
        seeded["done"] = json.loads(done_result.stdout)["data"]["task"]
        return seeded

    def test_task_list_returns_active_tasks_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            self.seed_tasks(db, repo)

            payload = list_tasks(db, repo)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.list")
            titles = [task["title"] for task in payload["data"]["tasks"]]
            self.assertEqual(titles, ["Sequential ready", "Ready optional", "Blocked optional"])
            self.assertEqual(payload["data"]["count"], 3)
            self.assertEqual(payload["data"]["limit"], 20)

    def test_task_list_filters_by_status_kind_lane_priority_and_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            seeded = self.seed_tasks(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE tasks SET lane = ' TG-M2 ' WHERE task_id = ?",
                    (seeded["sequential"]["task_id"],),
                )
                connection.commit()

            ready = list_tasks(db, repo, "--status", "ready")["data"]["tasks"]
            sequential = list_tasks(db, repo, "--kind", "sequential")["data"]["tasks"]
            lane = list_tasks(db, repo, "--lane", "  TG-M2  ")["data"]["tasks"]
            urgent = list_tasks(db, repo, "--priority", "urgent")["data"]["tasks"]
            tag = list_tasks(db, repo, "--tag", "ui")["data"]["tasks"]

            self.assertEqual([task["title"] for task in ready], ["Sequential ready", "Ready optional"])
            self.assertEqual([task["title"] for task in sequential], ["Sequential ready"])
            self.assertEqual([task["title"] for task in lane], ["Sequential ready"])
            self.assertEqual(lane[0]["lane"], " TG-M2 ")
            self.assertEqual([task["title"] for task in urgent], ["Sequential ready"])
            self.assertEqual([task["title"] for task in tag], ["Ready optional"])

    def test_task_list_limit_and_include_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            self.seed_tasks(db, repo)

            limited = list_tasks(db, repo, "--limit", "2")
            with_done = list_tasks(db, repo, "--include-done")

            self.assertEqual(limited["data"]["count"], 2)
            self.assertEqual(limited["data"]["limit"], 2)
            self.assertNotIn("Done task", [task["title"] for task in limited["data"]["tasks"]])
            titles = [task["title"] for task in with_done["data"]["tasks"]]
            self.assertIn("Done task", titles)
            self.assertIn("Cancelled task", titles)

    def test_task_list_rejects_invalid_limit_and_clamps_large_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            add_task(db, repo, "Ready optional")

            for limit in ("0", "abc"):
                with self.subTest(limit=limit):
                    result = run_taskgov(
                        "task",
                        "list",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        "--limit",
                        limit,
                        "--json",
                    )
                    self.assertEqual(result.returncode, 1)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["errors"][0]["code"], "invalid_argument")

            clamped = list_tasks(db, repo, "--limit", "1000")
            self.assertEqual(clamped["data"]["limit"], 100)

    def test_task_list_validation_errors_are_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            add_task(db, repo, "Ready optional")

            result = run_taskgov(
                "task",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--status",
                "waiting",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["command"], "task.list")
            self.assertEqual(payload["errors"][0]["code"], "invalid_status")

    def test_task_list_missing_db_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov(
                "task",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_task_list_reports_migration_required_without_migrating(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            with closing(sqlite3.connect(db)):
                pass

            result = run_taskgov("task", "list", "--repo", str(repo), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "migration_required")
            self.assertEqual(payload["data"], {"tasks": [], "count": 0, "limit": 0})
            with closing(sqlite3.connect(db)) as connection:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            self.assertEqual(tables, [])

    def test_task_list_reports_project_mismatch_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo_one = Path(tmp) / "repo-one"
            repo_two = Path(tmp) / "repo-two"
            init_payload = init_db(db, repo_one)

            result = run_taskgov("task", "list", "--repo", str(repo_two), "--db", str(db), "--json")

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "project_mismatch")
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute("SELECT project_id FROM project_meta").fetchone()
            self.assertEqual(row[0], init_payload["project_id"])

    def test_task_list_text_output_is_concise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            add_task(db, repo, "Ready optional", "--lane", "side")

            result = run_taskgov("task", "list", "--repo", str(repo), "--db", str(db))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            lines = result.stdout.strip().splitlines()
            self.assertLessEqual(len(lines), 2)
            self.assertIn("Tasks: 1 (limit 20)", result.stdout)
            self.assertIn("Ready optional", result.stdout)
            self.assertIn("optional side - Ready optional", result.stdout)
            self.assertNotIn("#None", result.stdout)


if __name__ == "__main__":
    unittest.main()
