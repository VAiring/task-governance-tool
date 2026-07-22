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

    def test_task_edit_status_done_is_terminal_and_preserves_completed_at(self):
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
            before = fetch_task(db, task["task_id"])
            before_events = table_count(db, "task_events")

            ready = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--status", "ready", "--json",
            )

            self.assertEqual(ready.returncode, 1, ready.stdout)
            self.assertEqual(
                json.loads(ready.stdout)["errors"][0]["code"],
                "task_done_immutable",
            )
            self.assertEqual(fetch_task(db, task["task_id"]), before)
            self.assertEqual(table_count(db, "task_events"), before_events)

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
                connection.execute("DELETE FROM schema_migrations WHERE version = 5")
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
            self.assertEqual(versions, [1, 2, 3, 4])

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

    def test_task_edit_rejects_order_outside_sqlite_int64_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(
                db, repo, "Ordered", "--kind", "sequential", "--lane", "EDIT", "--order", "1"
            )
            before_events = table_count(db, "task_events")

            result = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--order", str(2**63), "--json",
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(json.loads(result.stdout)["errors"][0]["code"], "invalid_argument")
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            self.assertEqual(fetch_task(db, task["task_id"])["lane_order"], 1)
            self.assertEqual(table_count(db, "task_events"), before_events)

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

    def test_done_task_rejects_every_task_edit_as_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Done gate metadata")
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
            before = fetch_task(db, task["task_id"])
            before_events = table_count(db, "task_events")

            attempts = (
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--title", "Late title", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--add-note", "Late note", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--status", "in_progress", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--review-tier", "0",
                    "--review-tier-change-reason", "Reclassify as mechanical", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--commit-not-required", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--verification-complete", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--review-tier", "9", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--add-note", "", "--json",
                ),
                run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    task["task_id"], "--title", "Authorization: Bearer secret", "--json",
                ),
            )
            for result in attempts:
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(
                    json.loads(result.stdout)["errors"][0]["code"],
                    "task_done_immutable",
                )
            self.assertEqual(fetch_task(db, task["task_id"]), before)
            self.assertEqual(table_count(db, "task_events"), before_events)

    def test_review_tier_downgrade_requires_reason_and_no_review_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Tier downgrade", "--review-tier", "2")

            missing_reason = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--review-tier", "1", "--json",
            )
            self.assertEqual(missing_reason.returncode, 1)
            self.assertEqual(json.loads(missing_reason.stdout)["errors"][0]["code"], "invalid_argument")
            private_reason = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Authorization: Bearer secret", "--json",
            )
            self.assertEqual(json.loads(private_reason.stdout)["errors"][0]["code"], "privacy_rejected")

            target = run_taskgov(
                "review", "target", "set", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--kind", "diff_fingerprint",
                "--revision", "sha256:" + "a" * 64, "--json",
            )
            self.assertEqual(target.returncode, 0, target.stdout)
            target_only = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Scope is now narrow", "--json",
            )
            self.assertEqual(target_only.returncode, 1, target_only.stdout)
            self.assertEqual(
                json.loads(target_only.stdout)["errors"][0]["code"],
                "invalid_review_evidence",
            )

            reset = run_taskgov(
                "review", "target", "set", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--kind", "diff_fingerprint",
                "--revision", "sha256:" + "b" * 64, "--json",
            )
            self.assertEqual(reset.returncode, 0, reset.stdout)
            still_rejected = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Scope is now narrow", "--json",
            )
            self.assertEqual(still_rejected.returncode, 1, still_rejected.stdout)
            self.assertEqual(
                json.loads(still_rejected.stdout)["errors"][0]["code"],
                "invalid_review_evidence",
            )

            fresh = add_task(db, repo, "Fresh tier downgrade", "--review-tier", "2")
            downgraded = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                fresh["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Scope is now narrow", "--json",
            )
            self.assertEqual(downgraded.returncode, 0, downgraded.stdout)
            self.assertIn("Scope is now narrow", json.loads(downgraded.stdout)["data"]["event"]["summary"])

            upgraded = edit_task(db, repo, fresh["task_id"], "--review-tier", "2")
            self.assertEqual(upgraded["data"]["task"]["review_tier"], 2)
            edit_task(db, repo, fresh["task_id"], "--status", "review_pending")
            unsafe_state = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                fresh["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Review gate changed", "--json",
            )
            self.assertEqual(unsafe_state.returncode, 1, unsafe_state.stdout)
            self.assertEqual(
                json.loads(unsafe_state.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )
            combined_resume = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                fresh["task_id"], "--status", "in_progress", "--review-tier", "1",
                "--review-tier-change-reason", "Review gate changed", "--json",
            )
            self.assertEqual(combined_resume.returncode, 1, combined_resume.stdout)
            self.assertEqual(
                json.loads(combined_resume.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )
            resumed = edit_task(db, repo, fresh["task_id"], "--status", "in_progress")
            self.assertEqual(resumed["data"]["task"]["status"], "in_progress")
            historical_review = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                fresh["task_id"], "--status", "ready", "--json",
            )
            self.assertEqual(historical_review.returncode, 0, historical_review.stdout)
            after_return_to_ready = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                fresh["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Review gate changed", "--json",
            )
            self.assertEqual(after_return_to_ready.returncode, 1, after_return_to_ready.stdout)
            self.assertEqual(
                json.loads(after_return_to_ready.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )

            unreviewed = add_task(db, repo, "Unreviewed combinations", "--review-tier", "2")
            for extra in (
                ("--commit-not-required",),
                ("--verification-complete",),
                ("--review-complete",),
            ):
                combined_gate = run_taskgov(
                    "task", "edit", "--repo", str(repo), "--db", str(db),
                    unreviewed["task_id"], "--review-tier", "1",
                    "--review-tier-change-reason", "Scope is now narrow",
                    *extra, "--json",
                )
                self.assertEqual(combined_gate.returncode, 1, combined_gate.stdout)
                self.assertEqual(
                    json.loads(combined_gate.stdout)["errors"][0]["code"],
                    "invalid_argument",
                )
            entering_review = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                unreviewed["task_id"], "--status", "review_pending",
                "--review-tier", "1", "--review-tier-change-reason", "Scope is now narrow",
                "--json",
            )
            self.assertEqual(entering_review.returncode, 1, entering_review.stdout)
            self.assertEqual(
                json.loads(entering_review.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )

    def test_review_complete_latches_review_started_before_tier_downgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            task = add_task(db, repo, "Confirmed review", "--review-tier", "2")

            confirmed = edit_task(
                db,
                repo,
                task["task_id"],
                "--review-complete",
            )
            self.assertEqual(confirmed["data"]["event"]["event_type"], "review_started")
            self.assertIn("review complete", confirmed["data"]["event"]["summary"])

            rejected = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                task["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Scope is now narrow", "--json",
            )
            self.assertEqual(rejected.returncode, 1, rejected.stdout)
            self.assertEqual(
                json.loads(rejected.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )

    def test_review_tier_downgrade_latch_survives_review_pause_and_legacy_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            initially_reviewing = add_task(
                db,
                repo,
                "Initially reviewing",
                "--review-tier",
                "2",
                "--status",
                "review_pending",
            )
            with closing(sqlite3.connect(db)) as connection:
                initial_event = connection.execute(
                    "SELECT event_type FROM task_events WHERE task_id = ?",
                    (initially_reviewing["task_id"],),
                ).fetchone()[0]
            self.assertEqual(initial_event, "review_started")
            edit_task(db, repo, initially_reviewing["task_id"], "--status", "in_progress")
            initial_downgrade = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                initially_reviewing["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Review already started", "--json",
            )
            self.assertEqual(initial_downgrade.returncode, 1, initial_downgrade.stdout)
            self.assertEqual(
                json.loads(initial_downgrade.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )

            paused = add_task(db, repo, "Pause after review", "--review-tier", "2")
            edit_task(db, repo, paused["task_id"], "--status", "in_progress")
            entered = edit_task(
                db,
                repo,
                paused["task_id"],
                "--status",
                "review_pending",
                "--add-note",
                "Review handoff",
                "--commit-not-required",
                "--verification-complete",
            )
            self.assertEqual(entered["data"]["event"]["event_type"], "review_started")
            self.assertIn("Review handoff", entered["data"]["event"]["summary"])
            self.assertIn("commit not required", entered["data"]["event"]["summary"])
            edit_task(
                db,
                repo,
                paused["task_id"],
                "--status",
                "paused",
                "--pause-reason",
                "Waiting for review",
            )
            edit_task(db, repo, paused["task_id"], "--status", "in_progress")
            after_resume = run_taskgov(
                "task", "edit", "--repo", str(repo), "--db", str(db),
                paused["task_id"], "--review-tier", "1",
                "--review-tier-change-reason", "Review already started", "--json",
            )
            self.assertEqual(after_resume.returncode, 1, after_resume.stdout)
            self.assertEqual(
                json.loads(after_resume.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )

            ambiguous_tasks = {
                "legacy": add_task(db, repo, "Legacy marker", "--review-tier", "2"),
                "missing": add_task(db, repo, "Missing marker", "--review-tier", "2"),
                "duplicate": add_task(db, repo, "Duplicate marker", "--review-tier", "2"),
            }
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE task_events SET event_type = 'task_added' WHERE task_id = ?",
                    (ambiguous_tasks["legacy"]["task_id"],),
                )
                connection.execute(
                    "DELETE FROM task_events WHERE task_id = ?",
                    (ambiguous_tasks["missing"]["task_id"],),
                )
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id, task_id, project_id, event_type, summary, created_at
                    )
                    SELECT 'tg_event_duplicate_marker', task_id, project_id,
                           event_type, summary, created_at
                      FROM task_events
                     WHERE task_id = ?
                    """,
                    (ambiguous_tasks["duplicate"]["task_id"],),
                )
                connection.commit()
            for marker_case, task in ambiguous_tasks.items():
                with self.subTest(marker_case=marker_case):
                    rejected = run_taskgov(
                        "task", "edit", "--repo", str(repo), "--db", str(db),
                        task["task_id"], "--review-tier", "1",
                        "--review-tier-change-reason", "Marker is ambiguous", "--json",
                    )
                    self.assertEqual(rejected.returncode, 1, rejected.stdout)
                    self.assertEqual(
                        json.loads(rejected.stdout)["errors"][0]["code"],
                        "invalid_status_transition",
                    )

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

    def test_task_edit_help_includes_review_tier_change_reason(self):
        result = run_taskgov("task", "edit", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--review-tier-change-reason", result.stdout)


if __name__ == "__main__":
    unittest.main()
