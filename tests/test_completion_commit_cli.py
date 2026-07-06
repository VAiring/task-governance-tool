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


def task_payload(command, db, repo, *extra):
    result = run_taskgov(
        "task",
        command,
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


def fetch_completion_state(db, task_id):
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT completion_commit_required, completion_commit_hash
              FROM tasks
             WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        return dict(row)


def table_count(db, table):
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class CompletionCommitCliTests(unittest.TestCase):
    def test_task_edit_completion_commit_hash_records_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Commit hash task")

            payload = edit_task(db, repo, task["task_id"], "--completion-commit-hash", "abc123def456")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.edit")
            self.assertIn("completion_commit_hash", payload["data"]["changed_fields"])
            self.assertNotIn("completion_commit_hash", payload["data"]["task"])
            self.assertEqual(payload["data"]["event"]["summary"], "Recorded: completion commit hash")
            self.assertEqual(
                fetch_completion_state(db, task["task_id"]),
                {
                    "completion_commit_required": 1,
                    "completion_commit_hash": "abc123def456",
                },
            )

    def test_task_edit_completion_commit_hash_text_output_says_what_was_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Commit hash text task")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--completion-commit-hash",
                "abc123def456",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Event: task_updated - Recorded: completion commit hash", result.stdout)

    def test_task_edit_commit_not_required_clears_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "No material task")
            edit_task(db, repo, task["task_id"], "--completion-commit-hash", "abc123def456")

            payload = edit_task(db, repo, task["task_id"], "--commit-not-required")

            self.assertIn("completion_commit_required", payload["data"]["changed_fields"])
            self.assertIn("completion_commit_hash", payload["data"]["changed_fields"])
            self.assertEqual(payload["data"]["event"]["summary"], "Recorded: commit not required")
            self.assertEqual(
                fetch_completion_state(db, task["task_id"]),
                {
                    "completion_commit_required": 0,
                    "completion_commit_hash": "",
                },
            )

    def test_task_edit_rejects_commit_hash_conflict_without_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Conflict task")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--completion-commit-hash",
                "abc123def456",
                "--commit-not-required",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "completion_commit_conflict")
            self.assertEqual(
                fetch_completion_state(db, task["task_id"]),
                {
                    "completion_commit_required": 1,
                    "completion_commit_hash": "",
                },
            )
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_rejects_private_completion_commit_hash(self):
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
                "--completion-commit-hash",
                "Bearer secret",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "privacy_rejected")
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_records_verification_and_review_confirmations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Confirmation task")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--verification-complete",
                "--review-complete",
            )

            self.assertEqual(payload["data"]["changed_fields"], [])
            self.assertEqual(
                payload["data"]["event"]["summary"],
                "Recorded: verification complete, review complete",
            )
            self.assertEqual(table_count(db, "task_events"), 2)

    def test_task_edit_records_confirmations_when_note_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Confirmation note task")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--verification-complete",
                "--review-complete",
                "--add-note",
                "Checked required gates",
            )

            self.assertEqual(payload["data"]["changed_fields"], [])
            self.assertEqual(payload["data"]["event"]["event_type"], "task_updated")
            self.assertEqual(
                payload["data"]["event"]["summary"],
                "Note added: Checked required gates; Recorded: verification complete, review complete",
            )

    def test_task_edit_long_note_with_confirmation_keeps_marker_within_summary_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Long confirmation note task")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--verification-complete",
                "--add-note",
                "x" * 1500,
            )

            summary = payload["data"]["event"]["summary"]
            self.assertLessEqual(len(summary), 1000)
            self.assertTrue(summary.startswith("Note added: "))
            self.assertTrue(summary.endswith("; Recorded: verification complete"))

    def test_task_edit_done_accepts_confirmations_and_commit_hash_before_enforcement(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Done with completion metadata")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--completion-commit-hash",
                "abc123def456",
            )

            self.assertEqual(payload["data"]["task"]["status"], "done")
            self.assertIsNotNone(payload["data"]["task"]["completed_at"])
            self.assertIn("status", payload["data"]["changed_fields"])
            self.assertIn("completed_at", payload["data"]["changed_fields"])
            self.assertIn("completion_commit_hash", payload["data"]["changed_fields"])
            self.assertEqual(fetch_completion_state(db, task["task_id"])["completion_commit_hash"], "abc123def456")

    def test_public_task_read_commands_do_not_expose_completion_commit_fields_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Hidden completion metadata")
            edit_task(db, repo, task["task_id"], "--completion-commit-hash", "abc123def456")

            shown = task_payload("show", db, repo, task["task_id"])["data"]["task"]
            listed = task_payload("list", db, repo)["data"]["tasks"][0]
            next_task = task_payload("next", db, repo)["data"]["tasks"][0]

            for public_task in (shown, listed, next_task):
                self.assertNotIn("completion_commit_required", public_task)
                self.assertNotIn("completion_commit_hash", public_task)

    def test_task_edit_completion_flags_missing_db_does_not_create_files(self):
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
                "--completion-commit-hash",
                "abc123def456",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_task_edit_completion_flags_report_newer_schema_migration_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    CREATE TABLE schema_migrations (
                      version INTEGER PRIMARY KEY,
                      name TEXT NOT NULL,
                      applied_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (999, "future_schema", "2026-07-06T00:00:00Z"),
                )
                connection.commit()

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "tg_task_missing",
                "--completion-commit-hash",
                "abc123def456",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "migration_required")

    def test_task_edit_help_lists_completion_commit_flags(self):
        result = run_taskgov("task", "edit", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--completion-commit-hash", result.stdout)
        self.assertIn("--commit-not-required", result.stdout)
        self.assertIn("--verification-complete", result.stdout)
        self.assertIn("--review-complete", result.stdout)


if __name__ == "__main__":
    unittest.main()
