import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
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
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.storage import connect_initialized, resolve_database_target
    from task_governance_tool.tasks import (
        TaskRepositoryError,
        TaskValidationError,
        edit_task,
    )
finally:
    sys.path.pop(0)


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def init_db(db, repo):
    initialize_taskgov_internal(repo=repo, db=db)


def add_task(db, repo, title, *extra):
    result = run_taskgov(
        "task", "add", "--repo", str(repo), "--db", str(db), "--title", title, *extra, "--json"
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


def edit_result(db, repo, task_id, *extra):
    return run_taskgov(
        "task", "edit", "--repo", str(repo), "--db", str(db), task_id, *extra, "--json"
    )


def task_state(db, task_id):
    import sqlite3

    with closing(sqlite3.connect(db)) as connection:
        row = connection.execute("SELECT status, lane_order FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        events = connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
    return row, events


class SequentialTransitionTests(unittest.TestCase):
    def test_initial_active_or_review_status_rejects_incomplete_predecessor(self):
        for status in ("in_progress", "review_pending"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "taskgov.sqlite"
                repo = Path(tmp) / "repo"
                init_db(db, repo)
                add_task(db, repo, "Earlier", "--kind", "sequential", "--lane", "ADD", "--order", "10")

                result = run_taskgov(
                    "task", "add", "--repo", str(repo), "--db", str(db), "--title", "Later active",
                    "--kind", "sequential", "--lane", "ADD", "--order", "20", "--status", status, "--json",
                )

                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    json.loads(result.stdout)["errors"][0]["code"],
                    "sequential_predecessor_incomplete",
                )
                self.assertEqual(task_state(db, "tg_task_missing")[1], 1)

    def test_direct_start_review_and_done_reject_incomplete_predecessor(self):
        for status in ("in_progress", "review_pending", "done"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "taskgov.sqlite"
                repo = Path(tmp) / "repo"
                init_db(db, repo)
                add_task(db, repo, "Earlier", "--kind", "sequential", "--lane", "CORE", "--order", "10")
                later = add_task(
                    db, repo, "Later", "--kind", "sequential",
                    "--lane", "SHADOW", "--order", "20"
                )
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute(
                        "UPDATE tasks SET lane = ' CORE ' WHERE task_id = ?",
                        (later["task_id"],),
                    )
                    connection.commit()
                if status == "in_progress":
                    selected = run_taskgov(
                        "task", "next", "--repo", str(repo), "--db", str(db),
                        "--lane", "CORE", "--limit", "10", "--json",
                    )
                    self.assertEqual(
                        [task["title"] for task in json.loads(selected.stdout)["data"]["tasks"]],
                        ["Earlier"],
                    )
                extra = []
                if status == "done":
                    extra = ["--verification-complete", "--review-complete", "--commit-not-required"]

                result = edit_result(db, repo, later["task_id"], "--status", status, *extra)

                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    json.loads(result.stdout)["errors"][0]["code"],
                    "sequential_predecessor_incomplete",
                )
                row, events = task_state(db, later["task_id"])
                self.assertEqual(row[0], "ready")
                self.assertEqual(events, 2)

    def test_paused_predecessor_blocks_its_lane_but_not_other_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            first = add_task(
                db, repo, "First", "--kind", "sequential", "--lane", "CORE", "--order", "10",
                "--status", "in_progress",
            )
            later = add_task(
                db, repo, "Later", "--kind", "sequential", "--lane", "CORE", "--order", "20"
            )
            other = add_task(
                db, repo, "Other lane", "--kind", "sequential", "--lane", "DOCS", "--order", "10"
            )
            optional = add_task(db, repo, "Optional")
            paused = edit_result(
                db, repo, first["task_id"], "--status", "paused", "--pause-reason", "Intentional hold"
            )

            next_payload = json.loads(
                run_taskgov(
                    "task", "next", "--repo", str(repo), "--db", str(db), "--limit", "10", "--json"
                ).stdout
            )

            blocked = edit_result(db, repo, later["task_id"], "--status", "in_progress")
            other_started = edit_result(db, repo, other["task_id"], "--status", "in_progress")
            optional_started = edit_result(db, repo, optional["task_id"], "--status", "in_progress")

            self.assertEqual(paused.returncode, 0, paused.stderr)
            self.assertEqual(
                [task["title"] for task in next_payload["data"]["tasks"]],
                ["Optional", "Other lane"],
            )
            self.assertEqual(json.loads(blocked.stdout)["errors"][0]["code"], "sequential_predecessor_incomplete")
            self.assertEqual(other_started.returncode, 0, other_started.stderr)
            self.assertEqual(optional_started.returncode, 0, optional_started.stderr)

    def test_add_reorder_and_combined_edit_cannot_create_out_of_order_active_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            active = add_task(
                db, repo, "Active", "--kind", "sequential", "--lane", "ADD", "--order", "20",
                "--status", "in_progress",
            )

            inserted = run_taskgov(
                "task", "add", "--repo", str(repo), "--db", str(db), "--title", "Earlier ready",
                "--kind", "sequential", "--lane", "ADD", "--order", "10", "--json",
            )
            self.assertEqual(inserted.returncode, 1)
            self.assertEqual(json.loads(inserted.stdout)["errors"][0]["code"], "sequential_predecessor_incomplete")

            later = add_task(
                db, repo, "Later ready", "--kind", "sequential", "--lane", "ADD", "--order", "30"
            )
            reordered = edit_result(db, repo, later["task_id"], "--order", "10")
            self.assertEqual(reordered.returncode, 1)
            self.assertEqual(json.loads(reordered.stdout)["errors"][0]["code"], "sequential_predecessor_incomplete")
            self.assertEqual(task_state(db, later["task_id"])[0][1], 30)

            optional_active = add_task(db, repo, "Optional active", "--status", "in_progress")
            combined = edit_result(
                db, repo, optional_active["task_id"], "--kind", "sequential", "--lane", "ADD", "--order", "40"
            )
            self.assertEqual(combined.returncode, 1)
            self.assertEqual(json.loads(combined.stdout)["errors"][0]["code"], "sequential_predecessor_incomplete")
            self.assertEqual(task_state(db, active["task_id"])[0][0], "in_progress")

    def test_concurrent_writers_serialize_before_sequential_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            first = add_task(db, repo, "First", "--kind", "sequential", "--lane", "RACE", "--order", "10")
            second = add_task(db, repo, "Second", "--kind", "sequential", "--lane", "RACE", "--order", "20")
            target = resolve_database_target(
                repo=repo, db=db, script_path=SKILL_ROOT / "scripts" / "taskgov.py"
            )
            started = threading.Event()
            finished = threading.Event()
            outcome = {}

            def second_writer():
                started.set()
                try:
                    with closing(connect_initialized(target)) as connection:
                        with connection:
                            edit_task(connection, target.project, second["task_id"], status="in_progress")
                except TaskRepositoryError as exc:
                    outcome["code"] = exc.code
                finally:
                    finished.set()

            with closing(connect_initialized(target)) as first_connection:
                edit_task(first_connection, target.project, first["task_id"], status="in_progress")
                thread = threading.Thread(target=second_writer)
                thread.start()
                self.assertTrue(started.wait(1))
                time.sleep(0.1)
                self.assertFalse(finished.is_set())
                first_connection.commit()
                thread.join(5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome.get("code"), "sequential_predecessor_incomplete")
            self.assertEqual(task_state(db, second["task_id"])[0][0], "ready")

    def test_reopen_rejects_advanced_successor_without_cascade(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            first = add_task(
                db, repo, "First", "--kind", "sequential", "--lane", "REOPEN",
                "--order", "10",
            )
            second = add_task(
                db, repo, "Second", "--kind", "sequential", "--lane", "REOPEN",
                "--order", "20",
            )
            for task in (first, second):
                seed_review_evidence(db, task["task_id"])
                completed = edit_result(
                    db,
                    repo,
                    task["task_id"],
                    "--status",
                    "done",
                    "--verification-complete",
                    "--review-complete",
                    "--commit-not-required",
                )
                self.assertEqual(completed.returncode, 0, completed.stdout)
            before = db.read_bytes()
            with closing(sqlite3.connect(db)) as connection:
                before_rows = connection.execute(
                    """
                    SELECT task_id, status, review_target_generation
                      FROM tasks
                     ORDER BY lane_order
                    """
                ).fetchall()
                before_events = connection.execute(
                    "SELECT COUNT(*) FROM task_events"
                ).fetchone()[0]

            reopened = edit_result(
                db,
                repo,
                first["task_id"],
                "--status",
                "in_progress",
                "--reopen-reason",
                "First task needs correction",
            )

            self.assertEqual(reopened.returncode, 1, reopened.stdout)
            self.assertEqual(
                json.loads(reopened.stdout)["errors"][0]["code"],
                "sequential_predecessor_incomplete",
            )
            self.assertEqual(db.read_bytes(), before)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT task_id, status, review_target_generation
                          FROM tasks
                         ORDER BY lane_order
                        """
                    ).fetchall(),
                    before_rows,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
                    before_events,
                )

    def test_concurrent_exact_reopens_commit_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(db, repo, "Concurrent reopen")
            seed_review_evidence(db, task["task_id"])
            completed = edit_result(
                db,
                repo,
                task["task_id"],
                "--status",
                "done",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )
            with closing(sqlite3.connect(db)) as connection:
                old_generation = connection.execute(
                    "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
            barrier = threading.Barrier(2)
            outcomes = []
            outcome_lock = threading.Lock()

            def reopen_writer():
                barrier.wait()
                try:
                    with closing(connect_initialized(target)) as connection:
                        with connection:
                            edit_task(
                                connection,
                                target.project,
                                task["task_id"],
                                status="in_progress",
                                reopen_reason="Concurrent correction",
                            )
                    code = "ok"
                except (TaskRepositoryError, TaskValidationError) as exc:
                    code = exc.code
                with outcome_lock:
                    outcomes.append(code)

            threads = [threading.Thread(target=reopen_writer) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())

            self.assertCountEqual(outcomes, ["ok", "invalid_argument"])
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    "SELECT status, review_target_generation FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                self.assertEqual(row, ("in_progress", old_generation + 1))
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM task_events
                         WHERE task_id = ? AND event_type = 'task_reopened'
                        """,
                        (task["task_id"],),
                    ).fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
