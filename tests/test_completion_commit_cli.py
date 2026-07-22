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


def init_git_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=TaskGov Test",
            "-c",
            "user.email=taskgov@example.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def init_db(db, repo):
    result = run_taskgov("db", "init", "--repo", str(repo), "--db", str(db), "--json")
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


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


def edit_task(db, repo, task_id, *extra):
    if "--status" in extra and extra[extra.index("--status") + 1] == "done":
        target_kind = None
        target_value = None
        if "--completion-commit-hash" in extra:
            supplied = extra[extra.index("--completion-commit-hash") + 1]
            target_kind = "git_commit"
            target_value = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", f"{supplied}^{{commit}}"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
        elif "--completion-evidence-kind" in extra:
            target_kind = extra[extra.index("--completion-evidence-kind") + 1]
            if target_kind in {"git_commit", "external_revision"}:
                target_value = extra[extra.index("--completion-revision") + 1]
                if target_kind == "git_commit":
                    target_value = subprocess.run(
                        ["git", "-C", str(repo), "rev-parse", f"{target_value}^{{commit}}"],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip()
            else:
                target_kind = None
        seed_review_evidence(
            db,
            task_id,
            target_kind=target_kind,
            target_value=target_value,
        )
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


def fetch_task_state(db, task_id):
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT status, completed_at, completion_commit_required, completion_commit_hash
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
            revision = init_git_repo(repo)
            task = add_task(db, repo, "Commit hash task")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--completion-commit-hash",
                revision[:12],
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.edit")
            self.assertIn("completion_commit_hash", payload["data"]["changed_fields"])
            self.assertNotIn("completion_commit_hash", payload["data"]["task"])
            self.assertEqual(
                payload["data"]["event"]["summary"],
                "Recorded: Git completion commit verified",
            )
            self.assertEqual(
                fetch_completion_state(db, task["task_id"]),
                {
                    "completion_commit_required": 1,
                    "completion_commit_hash": revision,
                },
            )

    def test_task_edit_completion_commit_hash_text_output_says_what_was_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
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
                revision[:12],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Event: task_updated - Recorded: Git completion commit verified", result.stdout)

    def test_task_edit_commit_not_required_clears_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
            task = add_task(db, repo, "No material task")
            edit_task(db, repo, task["task_id"], "--completion-commit-hash", revision)

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
            self.assertEqual(payload["errors"][0]["code"], "completion_evidence_conflict")
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

    def test_task_edit_done_rejects_missing_verification_confirmation_without_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
            task = add_task(db, repo, "Missing verification confirmation")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "done",
                "--review-complete",
                "--completion-commit-hash",
                revision,
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "verification_required")
            self.assertEqual(fetch_task_state(db, task["task_id"])["status"], "ready")
            self.assertEqual(fetch_task_state(db, task["task_id"])["completion_commit_hash"], "")
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_done_rejects_missing_review_confirmation_without_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
            task = add_task(db, repo, "Missing review confirmation")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--completion-commit-hash",
                revision,
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "review_required")
            self.assertEqual(fetch_task_state(db, task["task_id"])["status"], "ready")
            self.assertEqual(fetch_task_state(db, task["task_id"])["completion_commit_hash"], "")
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_done_rejects_missing_required_commit_hash_without_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Missing completion commit")

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "commit_required")
            self.assertEqual(fetch_task_state(db, task["task_id"])["status"], "ready")
            self.assertIsNone(fetch_task_state(db, task["task_id"])["completed_at"])
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_done_rejects_inconsistent_commit_not_required_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Inconsistent commit state")
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET completion_commit_required = 0,
                           completion_commit_hash = ?
                     WHERE task_id = ?
                    """,
                    ("abc123def456", task["task_id"]),
                )
                connection.commit()

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "completion_evidence_conflict")
            self.assertEqual(fetch_task_state(db, task["task_id"])["status"], "ready")
            self.assertEqual(table_count(db, "task_events"), 1)

    def test_task_edit_done_accepts_confirmations_and_commit_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
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
                revision[:12],
            )

            self.assertEqual(payload["data"]["task"]["status"], "done")
            self.assertIsNotNone(payload["data"]["task"]["completed_at"])
            self.assertIn("status", payload["data"]["changed_fields"])
            self.assertIn("completed_at", payload["data"]["changed_fields"])
            self.assertIn("completion_commit_hash", payload["data"]["changed_fields"])
            self.assertEqual(
                fetch_completion_state(db, task["task_id"])["completion_commit_hash"],
                revision,
            )

    def test_task_edit_done_accepts_previously_recorded_commit_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
            task = add_task(db, repo, "Done with earlier commit metadata")
            edit_task(db, repo, task["task_id"], "--completion-commit-hash", revision)

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
            )

            self.assertEqual(payload["data"]["task"]["status"], "done")
            self.assertIsNotNone(payload["data"]["task"]["completed_at"])
            self.assertEqual(
                fetch_completion_state(db, task["task_id"])["completion_commit_hash"],
                revision,
            )

    def test_task_edit_done_accepts_commit_not_required_with_confirmations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Done without managed material")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )

            self.assertEqual(payload["data"]["task"]["status"], "done")
            self.assertIsNotNone(payload["data"]["task"]["completed_at"])
            self.assertEqual(
                fetch_completion_state(db, task["task_id"]),
                {
                    "completion_commit_required": 0,
                    "completion_commit_hash": "",
                },
            )

    def test_task_edit_done_accepts_previously_recorded_commit_not_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Done with earlier no-commit decision")
            edit_task(db, repo, task["task_id"], "--commit-not-required")

            payload = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
            )

            self.assertEqual(payload["data"]["task"]["status"], "done")
            self.assertIsNotNone(payload["data"]["task"]["completed_at"])
            self.assertEqual(
                fetch_completion_state(db, task["task_id"]),
                {
                    "completion_commit_required": 0,
                    "completion_commit_hash": "",
                },
            )

    def test_task_show_exposes_completion_commit_fields_but_list_and_next_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
            task = add_task(db, repo, "Completion metadata visibility")
            edit_task(db, repo, task["task_id"], "--completion-commit-hash", revision)

            shown = task_payload("show", db, repo, task["task_id"])["data"]["task"]
            listed = task_payload("list", db, repo)["data"]["tasks"][0]
            next_task = task_payload("next", db, repo)["data"]["tasks"][0]

            self.assertEqual(shown["completion_commit_required"], 1)
            self.assertEqual(shown["completion_commit_hash"], revision)
            for public_task in (listed, next_task):
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
        self.assertIn("--completion-evidence-kind", result.stdout)
        self.assertIn("--completion-revision", result.stdout)
        self.assertIn("--completion-evidence-reason", result.stdout)
        self.assertIn("--external-revision-approved", result.stdout)
        self.assertIn("--commit-not-required", result.stdout)
        self.assertIn("--verification-complete", result.stdout)
        self.assertIn("--review-complete", result.stdout)


if __name__ == "__main__":
    unittest.main()
