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


def next_tasks(db, repo, *extra):
    result = run_taskgov(
        "task",
        "next",
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


def db_status(db, repo):
    result = run_taskgov("db", "status", "--repo", str(repo), "--db", str(db), "--json")
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def show_task(db, repo, task_id):
    result = run_taskgov(
        "task",
        "show",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def table_count(db, table):
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def titles(payload):
    return [task["title"] for task in payload["data"]["tasks"]]


class TaskNextTests(unittest.TestCase):
    def test_task_next_returns_default_limit_and_json_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            for index in range(6):
                add_task(db, repo, f"Ready task {index}")

            payload = next_tasks(db, repo)
            status = db_status(db, repo)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.next")
            self.assertEqual(payload["data"]["count"], 5)
            self.assertEqual(payload["data"]["limit"], 5)
            self.assertEqual(len(payload["data"]["tasks"]), 5)
            self.assertEqual(status["data"]["counts"]["next_actionable"], 6)
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(payload["data"]["selection_rules"]["status"], "ready")
            self.assertEqual(
                payload["data"]["selection_rules"]["priority_order"],
                ["urgent", "high", "normal", "low"],
            )

    def test_task_next_filters_by_kind_lane_priority_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(db, repo, "Optional high", "--priority", "high")
            add_task(
                db,
                repo,
                "Core high",
                "--kind",
                "sequential",
                "--lane",
                "CORE",
                "--order",
                "10",
                "--priority",
                "high",
            )
            add_task(
                db,
                repo,
                "Docs normal",
                "--kind",
                "sequential",
                "--lane",
                "DOCS",
                "--order",
                "10",
            )
            add_task(db, repo, "Optional low", "--priority", "low")

            optional = next_tasks(db, repo, "--kind", "optional", "--limit", "10")
            core = next_tasks(db, repo, "--lane", "CORE", "--limit", "10")
            high = next_tasks(db, repo, "--priority", "high", "--limit", "10")
            limited = next_tasks(db, repo, "--limit", "2")

            self.assertEqual(titles(optional), ["Optional high", "Optional low"])
            self.assertEqual(titles(core), ["Core high"])
            self.assertEqual(titles(high), ["Optional high", "Core high"])
            self.assertEqual(titles(limited), ["Optional high", "Core high"])

    def test_task_next_skips_blocked_lanes_without_hiding_other_ready_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(
                db,
                repo,
                "Core blocker",
                "--kind",
                "sequential",
                "--lane",
                "CORE",
                "--order",
                "10",
                "--status",
                "blocked",
                "--blocked-reason",
                "Waiting for decision",
                "--priority",
                "urgent",
            )
            add_task(
                db,
                repo,
                "Core later",
                "--kind",
                "sequential",
                "--lane",
                "CORE",
                "--order",
                "20",
                "--priority",
                "urgent",
            )
            add_task(db, repo, "Optional ready", "--priority", "high")
            add_task(
                db,
                repo,
                "Docs ready",
                "--kind",
                "sequential",
                "--lane",
                "DOCS",
                "--order",
                "10",
            )

            payload = next_tasks(db, repo, "--limit", "10")

            self.assertEqual(titles(payload), ["Optional ready", "Docs ready"])

    def test_task_next_blocks_on_in_progress_and_review_pending_earlier_lane_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(db, repo, "Optional ready")
            add_task(
                db,
                repo,
                "In progress earlier",
                "--kind",
                "sequential",
                "--lane",
                "BUILD",
                "--order",
                "10",
                "--status",
                "in_progress",
            )
            add_task(
                db,
                repo,
                "Build later",
                "--kind",
                "sequential",
                "--lane",
                "BUILD",
                "--order",
                "20",
            )
            add_task(
                db,
                repo,
                "Review earlier",
                "--kind",
                "sequential",
                "--lane",
                "REVIEW",
                "--order",
                "10",
                "--status",
                "review_pending",
            )
            add_task(
                db,
                repo,
                "Review later",
                "--kind",
                "sequential",
                "--lane",
                "REVIEW",
                "--order",
                "20",
            )
            done_earlier = add_task(
                db,
                repo,
                "Done earlier",
                "--kind",
                "sequential",
                "--lane",
                "DONE",
                "--order",
                "10",
            )
            edit_task(
                db,
                repo,
                done_earlier["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            add_task(
                db,
                repo,
                "Done lane later",
                "--kind",
                "sequential",
                "--lane",
                "DONE",
                "--order",
                "20",
            )

            payload = next_tasks(db, repo, "--limit", "10")
            status = db_status(db, repo)

            self.assertEqual(titles(payload), ["Optional ready", "Done lane later"])
            self.assertEqual(status["data"]["counts"]["next_actionable"], payload["data"]["count"])

    def test_task_next_missing_db_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"

            result = run_taskgov(
                "task",
                "next",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["command"], "task.next")
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(payload["data"], {"tasks": [], "count": 0, "limit": 0, "selection_rules": {}})
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())

    def test_task_next_readiness_errors_do_not_emit_paused_warning_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            owner = Path(tmp) / "owner"
            other = Path(tmp) / "other"
            init_db(db, owner)
            paused = add_task(db, owner, "Paused work", "--status", "in_progress")
            edit_task(
                db,
                owner,
                paused["task_id"],
                "--status",
                "paused",
                "--pause-reason",
                "Intentional hold",
            )

            before_mismatch = db.read_bytes()
            mismatch = run_taskgov(
                "task",
                "next",
                "--repo",
                str(other),
                "--db",
                str(db),
                "--json",
            )
            mismatch_payload = json.loads(mismatch.stdout)
            self.assertEqual(
                mismatch_payload["errors"][0]["code"],
                "project_mismatch",
            )
            self.assertEqual(mismatch_payload["warnings"], [])
            self.assertEqual(db.read_bytes(), before_mismatch)
            self.assertFalse(other.exists())

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = 5"
                )
                connection.commit()
            before_migration = db.read_bytes()
            migration = run_taskgov(
                "task",
                "next",
                "--repo",
                str(owner),
                "--db",
                str(db),
                "--json",
            )
            migration_payload = json.loads(migration.stdout)
            self.assertEqual(
                migration_payload["errors"][0]["code"],
                "migration_required",
            )
            self.assertEqual(migration_payload["warnings"], [])
            self.assertEqual(db.read_bytes(), before_migration)

    def test_task_next_end_to_end_temp_db_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            first = add_task(
                db,
                repo,
                "First sequential",
                "--kind",
                "sequential",
                "--lane",
                "CORE",
                "--order",
                "10",
            )
            second = add_task(
                db,
                repo,
                "Second sequential",
                "--kind",
                "sequential",
                "--lane",
                "CORE",
                "--order",
                "20",
            )

            before = next_tasks(db, repo, "--limit", "10")
            edit_task(
                db,
                repo,
                first["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            after = next_tasks(db, repo, "--limit", "10")
            shown = show_task(db, repo, second["task_id"])

            self.assertEqual(titles(before), ["First sequential"])
            self.assertEqual(titles(after), ["Second sequential"])
            self.assertEqual(shown["data"]["task"]["task_id"], second["task_id"])

    def test_task_next_read_only_does_not_record_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(db, repo, "Ready optional")
            before_counts = {
                table: table_count(db, table)
                for table in ("tasks", "task_events", "tool_events")
            }

            payload = next_tasks(db, repo, "--read-only")

            self.assertEqual(payload["data"]["count"], 1)
            self.assertEqual(
                {
                    table: table_count(db, table)
                    for table in ("tasks", "task_events", "tool_events")
                },
                before_counts,
            )

    def test_task_next_warns_about_paused_tasks_without_changing_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(db, repo, "Ready optional", "--priority", "high")
            before_pause = next_tasks(db, repo, "--limit", "10")
            for index in range(3):
                paused = add_task(
                    db,
                    repo,
                    f"Private paused title {index}",
                    "--status",
                    "in_progress",
                )
                edit_task(
                    db,
                    repo,
                    paused["task_id"],
                    "--status",
                    "paused",
                    "--pause-reason",
                    f"Private pause reason {index}",
                )
            before_bytes = db.read_bytes()
            before_entries = sorted(path.name for path in db.parent.iterdir())

            after_pause = next_tasks(db, repo, "--limit", "10", "--read-only")

            self.assertEqual(after_pause["data"], before_pause["data"])
            self.assertEqual(
                after_pause["warnings"],
                [
                    {
                        "code": "paused_tasks_present",
                        "message": (
                            "3 paused tasks exist; "
                            "run taskgov task current --status paused"
                        ),
                    }
                ],
            )
            serialized_warning = json.dumps(after_pause["warnings"])
            self.assertNotIn("Private paused title", serialized_warning)
            self.assertNotIn("Private pause reason", serialized_warning)
            self.assertEqual(db.read_bytes(), before_bytes)
            self.assertEqual(
                sorted(path.name for path in db.parent.iterdir()),
                before_entries,
            )

    def test_task_next_validation_errors_are_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)

            cases = (
                (("--kind", "dependency"), "invalid_kind"),
                (("--priority", "soon"), "invalid_priority"),
                (("--limit", "0"), "invalid_argument"),
            )
            for extra, error_code in cases:
                with self.subTest(extra=extra):
                    result = run_taskgov(
                        "task",
                        "next",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        *extra,
                        "--json",
                    )

                    self.assertEqual(result.returncode, 1)
                    payload = json.loads(result.stdout)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["command"], "task.next")
                    self.assertEqual(payload["errors"][0]["code"], error_code)
                    self.assertEqual(payload["warnings"], [])
                    self.assertEqual(payload["data"], {"tasks": [], "count": 0, "limit": 0, "selection_rules": {}})

    def test_task_next_text_output_is_concise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(db, repo, "Ready optional")

            result = run_taskgov("task", "next", "--repo", str(repo), "--db", str(db))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            lines = result.stdout.strip().splitlines()
            self.assertLessEqual(len(lines), 2)
            self.assertIn("Next tasks: 1 (limit 5)", result.stdout)
            self.assertIn("Ready optional", result.stdout)

    def test_task_next_text_includes_the_same_paused_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            add_task(db, repo, "Ready optional")
            paused = add_task(db, repo, "Paused work", "--status", "in_progress")
            edit_task(
                db,
                repo,
                paused["task_id"],
                "--status",
                "paused",
                "--pause-reason",
                "Intentional hold",
            )

            result = run_taskgov(
                "task",
                "next",
                "--repo",
                str(repo),
                "--db",
                str(db),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "Warning: 1 paused task exists; "
                "run taskgov task current --status paused",
                result.stdout,
            )


if __name__ == "__main__":
    unittest.main()
