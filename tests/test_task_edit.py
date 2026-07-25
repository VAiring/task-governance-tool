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
        seed_review_evidence(db, task_id)
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

    def test_task_edit_status_done_requires_exact_reopen_to_clear_completed_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Status task")

            done = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            completed_at = done["data"]["task"]["completed_at"]

            self.assertIsNotNone(completed_at)
            self.assertIn("status", done["data"]["changed_fields"])
            self.assertIn("completed_at", done["data"]["changed_fields"])

            rejected = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "ready",
                "--json",
            )
            reopened = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "Acceptance criteria changed",
            )

            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(
                json.loads(rejected.stdout)["errors"][0]["code"],
                "done_task_requires_reopen",
            )
            self.assertIsNone(reopened["data"]["task"]["completed_at"])
            self.assertEqual(reopened["data"]["task"]["status"], "in_progress")
            self.assertEqual(reopened["data"]["event"]["event_type"], "task_reopened")
            self.assertIn("completed_at", reopened["data"]["changed_fields"])

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

    def test_task_edit_pauses_and_resumes_with_distinct_reason_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Pausable task", "--status", "in_progress")

            paused = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "paused",
                "--pause-reason",
                "Waiting for a safe continuation window",
            )
            status = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")
            resumed = edit_task(db, repo, task["task_id"], "--status", "in_progress")

            self.assertEqual(paused["data"]["task"]["status"], "paused")
            self.assertEqual(
                paused["data"]["task"]["pause_reason"],
                "Waiting for a safe continuation window",
            )
            self.assertIn("Paused: Waiting for a safe continuation window", paused["data"]["event"]["summary"])
            self.assertEqual(json.loads(status.stdout)["data"]["counts"]["active"], 1)
            self.assertEqual(resumed["data"]["task"]["status"], "in_progress")
            self.assertEqual(resumed["data"]["task"]["pause_reason"], "")
            self.assertEqual(
                resumed["data"]["event"]["summary"],
                "Resumed from paused; Previous reason: Waiting for a safe continuation window",
            )

    def test_resume_event_prioritizes_historical_pause_reason_over_long_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Resume history", "--status", "in_progress")
            edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "paused",
                "--pause-reason",
                "Critical handoff reason",
            )

            resumed = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "in_progress",
                "--add-note",
                "x" * 1500,
            )

            summary = resumed["data"]["event"]["summary"]
            self.assertTrue(summary.startswith("Resumed from paused; Previous reason: Critical handoff reason"))
            self.assertLessEqual(len(summary), 1000)
            self.assertEqual(resumed["data"]["task"]["pause_reason"], "")

    def test_task_edit_pause_requires_reason_and_valid_transition_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            ready = add_task(db, repo, "Ready task")

            invalid_source = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), ready["task_id"],
                "--status", "paused", "--pause-reason", "Hold", "--json",
            )
            edit_task(db, repo, ready["task_id"], "--status", "in_progress")
            missing_reason = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), ready["task_id"],
                "--status", "paused", "--json",
            )
            edit_task(
                db, repo, ready["task_id"], "--status", "paused", "--pause-reason", "Hold",
            )
            invalid_resume = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db), ready["task_id"],
                "--status", "blocked", "--blocked-reason", "Dependency", "--json",
            )

            self.assertEqual(json.loads(invalid_source.stdout)["errors"][0]["code"], "invalid_status_transition")
            self.assertEqual(json.loads(missing_reason.stdout)["errors"][0]["code"], "pause_reason_required")
            self.assertEqual(json.loads(invalid_resume.stdout)["errors"][0]["code"], "invalid_status_transition")
            stored = fetch_task(db, ready["task_id"])
            self.assertEqual(stored["status"], "paused")
            self.assertEqual(stored["pause_reason"], "Hold")
            self.assertEqual(table_count(db, "task_events"), 3)

            review_task = add_task(db, repo, "Review task", "--status", "review_pending")
            review_paused = edit_task(
                db,
                repo,
                review_task["task_id"],
                "--status",
                "paused",
                "--pause-reason",
                "Review intentionally deferred",
            )
            self.assertEqual(review_paused["data"]["task"]["status"], "paused")

    def test_task_edit_pause_reason_privacy_and_size_rejections_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Private pause", "--status", "in_progress")

            for reason, code in (("token=secret", "privacy_rejected"), ("x" * 1001, "invalid_argument")):
                with self.subTest(code=code):
                    result = run_taskgov(
                        "task", "edit", "--repo", str(repo), "--db", str(db), task["task_id"],
                        "--status", "paused", "--pause-reason", reason, "--json",
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(json.loads(result.stdout)["errors"][0]["code"], code)
                    self.assertEqual(fetch_task(db, task["task_id"])["status"], "in_progress")
            self.assertEqual(table_count(db, "task_events"), 1)

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

    def test_task_edit_does_not_migrate_old_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("DELETE FROM schema_migrations WHERE version = 6")
                connection.commit()
            before = db.read_bytes()

            result = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "tg_task_missing",
                "--title",
                "No migration",
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
            self.assertEqual(versions, [1, 2, 3, 4, 5])

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

    def test_task_edit_rejects_automatic_lane_order_overflow_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            add_task(
                db, repo, "At maximum", "--kind", "sequential",
                "--lane", "LIMIT", "--order", str((1 << 63) - 1),
            )
            mover = add_task(
                db, repo, "Mover", "--kind", "sequential",
                "--lane", "SOURCE", "--order", "1",
            )
            before = db.read_bytes()

            result = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                mover["task_id"], "--lane", " LIMIT ", "--json",
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(
                json.loads(result.stdout)["errors"][0]["code"], "invalid_argument"
            )
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            self.assertEqual(fetch_task(db, mover["task_id"])["lane"], "SOURCE")
            self.assertEqual(db.read_bytes(), before)

    def test_unrelated_edit_preserves_historical_whitespace_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            add_task(
                db, repo, "Canonical", "--kind", "sequential",
                "--lane", "CORE", "--order", "1",
            )
            historical = add_task(
                db, repo, "Historical", "--kind", "sequential",
                "--lane", "LEGACY", "--order", "1",
            )
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE tasks SET lane = ' CORE ' WHERE task_id = ?",
                    (historical["task_id"],),
                )
                connection.commit()
            selected = run_taskgov(
                "task", "next", "--repo", str(repo), "--db", str(db),
                "--lane", "CORE", "--json",
            )
            self.assertEqual(selected.returncode, 0, selected.stdout)
            self.assertEqual(json.loads(selected.stdout)["data"]["tasks"], [])

            result = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                historical["task_id"], "--title", "Historical updated", "--json",
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            stored = fetch_task(db, historical["task_id"])
            self.assertEqual(stored["title"], "Historical updated")
            self.assertEqual(stored["lane"], " CORE ")
            self.assertEqual(stored["lane_order"], 1)
            blocked = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                historical["task_id"], "--status", "in_progress", "--json",
            )
            self.assertEqual(blocked.returncode, 1, blocked.stdout)
            self.assertEqual(
                json.loads(blocked.stdout)["errors"][0]["code"], "invalid_argument"
            )
            self.assertEqual(fetch_task(db, historical["task_id"])["status"], "ready")

    def test_done_task_accepts_only_exact_reopen_shape_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Locked done task")
            edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            before = db.read_bytes()
            before_events = table_count(db, "task_events")
            rejected_inputs = (
                ("--title", "Still done"),
                ("--add-note", "Late note"),
                ("--review-tier", "2"),
                ("--review-tier", "99"),
                ("--priority", "unsupported"),
                ("--add-note", "token=secret"),
                ("--commit-not-required",),
                (
                    "--completion-commit-hash",
                    "not-a-commit",
                    "--commit-not-required",
                ),
                ("--verification-complete",),
                ("--review-complete",),
                ("--status", "ready"),
                ("--status", "in_progress"),
                ("--reopen-reason", "Reason without transition"),
                (
                    "--status",
                    "in_progress",
                    "--reopen-reason",
                    "Combined mutation",
                    "--priority",
                    "high",
                ),
            )

            for edit_args in rejected_inputs:
                with self.subTest(edit_args=edit_args):
                    result = run_taskgov(
                        "task",
                        "edit",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task["task_id"],
                        *edit_args,
                        "--json",
                    )
                    self.assertEqual(result.returncode, 1, result.stdout)
                    body = json.loads(result.stdout)
                    self.assertEqual(
                        body["errors"][0]["code"],
                        "done_task_requires_reopen",
                    )
                    self.assertEqual(
                        body["data"],
                        {"task": None, "changed_fields": [], "event": None},
                    )
                    self.assertEqual(db.read_bytes(), before)
                    self.assertEqual(table_count(db, "task_events"), before_events)

    def test_exact_reopen_resets_schema_five_state_and_requires_fresh_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Reopen lifecycle")
            completed = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-42",
                "--completion-evidence-reason",
                "Published by the governed release process",
                "--external-revision-approved",
            )
            old_generation = fetch_task(db, task["task_id"])[
                "review_target_generation"
            ]
            with closing(sqlite3.connect(db)) as connection:
                before_receipts = connection.execute(
                    "SELECT COUNT(*) FROM review_receipts WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
                before_events = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]

            reopened = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "R" * 1000,
            )
            stored = fetch_task(db, task["task_id"])

            self.assertEqual(reopened["data"]["event"]["event_type"], "task_reopened")
            self.assertLessEqual(len(reopened["data"]["event"]["summary"]), 1000)
            self.assertIn(
                "previous completion evidence external_revision release-42",
                reopened["data"]["event"]["summary"],
            )
            self.assertEqual(stored["status"], "in_progress")
            self.assertIsNone(stored["completed_at"])
            self.assertEqual(stored["blocked_reason"], "")
            self.assertEqual(stored["pause_reason"], "")
            self.assertEqual(stored["completion_evidence_kind"], "none")
            self.assertEqual(stored["completion_evidence_revision"], "")
            self.assertEqual(stored["completion_evidence_reason"], "")
            self.assertEqual(stored["external_revision_approved"], 0)
            self.assertEqual(stored["completion_commit_required"], 1)
            self.assertEqual(stored["completion_commit_hash"], "")
            self.assertEqual(stored["review_target_kind"], "")
            self.assertEqual(stored["review_target_value"], "")
            self.assertEqual(stored["review_target_generation"], old_generation + 1)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipts WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    before_receipts,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    before_events + 1,
                )

            stale_review = run_taskgov(
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
                "--commit-not-required",
                "--json",
            )
            self.assertEqual(
                json.loads(stale_review.stdout)["errors"][0]["code"],
                "review_target_required",
            )
            target = run_taskgov(
                "review",
                "target",
                "set",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--kind",
                "diff_fingerprint",
                "--revision",
                "sha256:" + "d" * 64,
                "--json",
            )
            self.assertEqual(target.returncode, 0, target.stdout)
            receipt = run_taskgov(
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--reviewer",
                "fresh-reviewer",
                "--kind",
                "independent",
                "--verdict",
                "pass",
                "--json",
            )
            self.assertEqual(receipt.returncode, 0, receipt.stdout)
            recompleted = run_taskgov(
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
                "--commit-not-required",
                "--json",
            )
            self.assertEqual(recompleted.returncode, 0, recompleted.stdout)

    def test_reopen_reason_privacy_historical_redaction_and_overflow_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Safe reopen")
            edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            for reason, expected_code in (
                ("token=secret", "privacy_rejected"),
                ("raw log:\nsecret output", "privacy_rejected"),
                ("x" * 1001, "invalid_argument"),
            ):
                with self.subTest(expected_code=expected_code):
                    before = db.read_bytes()
                    result = run_taskgov(
                        "task",
                        "edit",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task["task_id"],
                        "--status",
                        "in_progress",
                        "--reopen-reason",
                        reason,
                        "--json",
                    )
                    self.assertEqual(
                        json.loads(result.stdout)["errors"][0]["code"],
                        expected_code,
                    )
                    self.assertEqual(db.read_bytes(), before)

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET review_target_generation = ?,
                           completion_evidence_kind = 'external_revision',
                           completion_evidence_revision = 'token=historical-secret'
                     WHERE task_id = ?
                    """,
                    ((1 << 63) - 1, task["task_id"]),
                )
                connection.commit()
            before_overflow = db.read_bytes()
            overflow = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "Retry completion safely",
                "--json",
            )
            self.assertEqual(
                json.loads(overflow.stdout)["errors"][0]["code"],
                "invalid_argument",
            )
            self.assertEqual(db.read_bytes(), before_overflow)

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE tasks SET review_target_generation = 4 WHERE task_id = ?",
                    (task["task_id"],),
                )
                connection.commit()
            redacted = edit_task(
                db,
                repo,
                task["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "Safe retry",
            )
            summary = redacted["data"]["event"]["summary"]
            self.assertNotIn("historical-secret", summary)
            self.assertIn("sha256:", summary)
            self.assertIn("redacted historical revision", summary)

    def test_review_tier_downgrade_boundary_and_upgrade_remain_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(db, repo, "Tier boundary", "--review-tier", "2")
            lowered = edit_task(
                db,
                repo,
                task["task_id"],
                "--review-tier",
                "1",
                "--review-tier-change-reason",
                "Review scope is now localized",
            )
            self.assertEqual(lowered["data"]["event"]["event_type"], "review_tier_changed")
            self.assertIn("2 -> 1", lowered["data"]["event"]["summary"])
            self.assertIn("Review scope is now localized", lowered["data"]["event"]["summary"])

            no_reason = add_task(db, repo, "Missing reason", "--review-tier", "2")
            before = db.read_bytes()
            rejected = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                no_reason["task_id"],
                "--review-tier",
                "1",
                "--json",
            )
            self.assertEqual(
                json.loads(rejected.stdout)["errors"][0]["code"],
                "review_tier_downgrade_forbidden",
            )
            self.assertEqual(db.read_bytes(), before)

            boundary_cases = (
                (
                    ("--status", "review_pending"),
                    ("--review-tier-change-reason", "Unsafe resulting status"),
                    "ready",
                ),
                (
                    (),
                    ("--review-tier-change-reason", "Unsafe current status"),
                    "review_pending",
                ),
                (
                    (),
                    (
                        "--review-tier-change-reason",
                        "Cannot combine evidence",
                        "--commit-not-required",
                    ),
                    "ready",
                ),
                (
                    (),
                    (
                        "--review-tier-change-reason",
                        "Cannot combine confirmations",
                        "--verification-complete",
                    ),
                    "ready",
                ),
            )
            for status_args, extra, initial_status in boundary_cases:
                boundary = add_task(
                    db,
                    repo,
                    f"Boundary {initial_status} {len(extra)}",
                    "--review-tier",
                    "2",
                    "--status",
                    initial_status,
                )
                before = db.read_bytes()
                rejected = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    boundary["task_id"],
                    "--review-tier",
                    "1",
                    *status_args,
                    *extra,
                    "--json",
                )
                self.assertEqual(
                    json.loads(rejected.stdout)["errors"][0]["code"],
                    "review_tier_downgrade_forbidden",
                )
                self.assertEqual(db.read_bytes(), before)

            private_reason = add_task(
                db, repo, "Private tier reason", "--review-tier", "2"
            )
            before = db.read_bytes()
            rejected = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                private_reason["task_id"],
                "--review-tier",
                "1",
                "--review-tier-change-reason",
                "token=secret",
                "--json",
            )
            self.assertEqual(
                json.loads(rejected.stdout)["errors"][0]["code"],
                "privacy_rejected",
            )
            self.assertEqual(db.read_bytes(), before)

            started = add_task(db, repo, "Review started", "--review-tier", "2")
            target = run_taskgov(
                "review",
                "target",
                "set",
                "--repo",
                str(repo),
                "--db",
                str(db),
                started["task_id"],
                "--kind",
                "diff_fingerprint",
                "--revision",
                "sha256:" + "e" * 64,
                "--json",
            )
            self.assertEqual(target.returncode, 0, target.stdout)
            for extra in ((
                "--review-tier-change-reason",
                "Review already started",
            ),):
                before = db.read_bytes()
                rejected = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    started["task_id"],
                    "--review-tier",
                    "1",
                    *extra,
                    "--json",
                )
                self.assertEqual(
                    json.loads(rejected.stdout)["errors"][0]["code"],
                    "review_tier_downgrade_forbidden",
                )
                self.assertEqual(db.read_bytes(), before)

            upgrade = add_task(db, repo, "Upgrade after review", "--review-tier", "1")
            target = run_taskgov(
                "review",
                "target",
                "set",
                "--repo",
                str(repo),
                "--db",
                str(db),
                upgrade["task_id"],
                "--kind",
                "diff_fingerprint",
                "--revision",
                "sha256:" + "f" * 64,
                "--json",
            )
            old_generation = json.loads(target.stdout)["data"]["task"][
                "review_target_generation"
            ]
            raised = edit_task(db, repo, upgrade["task_id"], "--review-tier", "2")
            self.assertEqual(raised["data"]["task"]["review_tier"], 2)
            self.assertEqual(
                fetch_task(db, upgrade["task_id"])["review_target_generation"],
                old_generation,
            )

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
