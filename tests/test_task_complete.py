import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.review_test_helpers import seed_review_evidence


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import cli as cli_module  # noqa: E402
from task_governance_tool import completion_workflow  # noqa: E402
from task_governance_tool import tasks as task_service  # noqa: E402
from task_governance_tool.completion import (  # noqa: E402
    COMPLETION_CHECK_MAX_BYTES,
)


def run_taskgov(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_taskgov_raw(*args):
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def init_db(db, repo):
    result = run_taskgov(
        "db",
        "init",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


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


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def init_git_repo(repo):
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "taskgov@example.invalid")
    git(repo, "config", "user.name", "Taskgov Test")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    git(repo, "add", "--", "tracked.txt")
    git(repo, "commit", "-q", "-m", "base")
    return git(repo, "rev-parse", "HEAD")


def complete_args(db, repo, task_id, *extra):
    return (
        "task",
        "complete",
        task_id,
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--verification-complete",
        "--review-complete",
        "--commit-not-required",
        *extra,
    )


def database_counts(db):
    with closing(sqlite3.connect(db)) as connection:
        return {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in (
                "tasks",
                "task_events",
                "review_receipts",
                "review_findings",
            )
        }


class TaskCompleteCliTests(unittest.TestCase):
    def test_check_ready_is_bounded_exact_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Ready to complete",
                "--status",
                "in_progress",
                "--review-tier",
                "2",
            )
            seed_review_evidence(db, task["task_id"])
            before_bytes = db.read_bytes()
            before_counts = database_counts(db)

            result = run_taskgov(
                *complete_args(
                    db,
                    repo,
                    task["task_id"],
                    "--check",
                    "--read-only",
                    "--json",
                )
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.complete")
            self.assertEqual(
                set(payload["data"]),
                {
                    "task_id",
                    "ready",
                    "status",
                    "blocking_codes",
                    "contract_revision",
                    "review_target_generation",
                    "completion_evidence_kind",
                    "suggested_action",
                },
            )
            self.assertTrue(payload["data"]["ready"])
            self.assertEqual(payload["data"]["blocking_codes"], [])
            self.assertEqual(
                payload["data"]["completion_evidence_kind"],
                "commit_not_required",
            )
            self.assertLessEqual(
                len(result.stdout.encode("utf-8")),
                COMPLETION_CHECK_MAX_BYTES,
            )
            self.assertEqual(db.read_bytes(), before_bytes)
            self.assertEqual(database_counts(db), before_counts)

            text_result = run_taskgov(
                *complete_args(
                    db,
                    repo,
                    task["task_id"],
                    "--check",
                    "--read-only",
                )
            )
            self.assertEqual(
                text_result.stdout,
                (
                    f"Task {task['task_id']}: ready\n"
                    "Blocking: none\n"
                    "Suggested action: run task complete with the same evidence "
                    "and confirmations\n"
                ),
            )

    def test_check_error_envelope_bounds_a_long_diagnostic_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            result = run_taskgov_raw(
                "task",
                "complete",
                "tg_task_aaaaaaaaaaaaaaaa",
                "--repo",
                str(repo),
                "--db",
                "x" * 17_000,
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
                "--check",
                "--read-only",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(
                payload["errors"][0]["code"],
                "db_not_initialized",
            )
            self.assertIsNone(payload["db_path"])
            normalized = result.stdout.replace(b"\r\n", b"\n")
            portable = normalized.replace(b"\n", b"\r\n")
            self.assertLessEqual(
                len(portable),
                COMPLETION_CHECK_MAX_BYTES,
            )
            self.assertLessEqual(
                len(result.stdout),
                COMPLETION_CHECK_MAX_BYTES,
            )

    def test_check_dynamic_and_parse_errors_use_final_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            owner_repo = Path(tmp) / "owner"
            init_db(db, owner_repo)
            common = (
                "task",
                "complete",
                "tg_task_aaaaaaaaaaaaaaaa",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
                "--check",
                "--json",
            )
            mismatch = run_taskgov_raw(
                *common,
                "--repo",
                "r" * 17_000,
                "--db",
                str(db),
                "--read-only",
            )
            rejected = run_taskgov_raw(
                *common,
                "--unknown-" + ("u" * 9_000),
            )
            abbreviated = run_taskgov_raw(
                "task",
                "complete",
                "tg_task_aaaaaaaaaaaaaaaa",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
                "--che",
                "--j",
                "--repo",
                "r" * 17_000,
                "--db",
                str(db),
                "--read-only",
            )
            abbreviated_parse = run_taskgov_raw(
                "task",
                "complete",
                "tg_task_aaaaaaaaaaaaaaaa",
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
                "--che",
                "--j",
                "--unknown-" + ("u" * 9_000),
            )

            self.assertEqual(mismatch.returncode, 2)
            mismatch_payload = json.loads(
                mismatch.stdout.decode("utf-8")
            )
            self.assertEqual(
                mismatch_payload["errors"][0]["code"],
                "project_mismatch",
            )
            self.assertEqual(rejected.returncode, 1)
            rejected_payload = json.loads(
                rejected.stdout.decode("utf-8")
            )
            self.assertEqual(rejected_payload["command"], "parse")
            self.assertEqual(
                rejected_payload["errors"][0]["code"],
                "invalid_argument",
            )
            self.assertEqual(abbreviated.returncode, 2)
            self.assertEqual(
                json.loads(abbreviated.stdout.decode("utf-8"))[
                    "errors"
                ][0]["code"],
                "project_mismatch",
            )
            self.assertEqual(abbreviated_parse.returncode, 1)
            self.assertEqual(
                json.loads(abbreviated_parse.stdout.decode("utf-8"))[
                    "errors"
                ][0]["code"],
                "invalid_argument",
            )
            for result in (
                mismatch,
                rejected,
                abbreviated,
                abbreviated_parse,
            ):
                payload = json.loads(result.stdout.decode("utf-8"))
                self.assertEqual(
                    payload["errors"][0]["message"],
                    (
                        "diagnostic details omitted to satisfy the "
                        "bounded output limit"
                    ),
                )
                normalized = result.stdout.replace(b"\r\n", b"\n")
                portable = normalized.replace(b"\n", b"\r\n")
                self.assertLessEqual(
                    len(portable),
                    COMPLETION_CHECK_MAX_BYTES,
                )
                self.assertLessEqual(
                    len(result.stdout),
                    COMPLETION_CHECK_MAX_BYTES,
                )

    def test_check_returns_only_the_first_existing_gate_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(db, repo, "Not ready", "--status", "in_progress")

            verification = run_taskgov(
                "task",
                "complete",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--commit-not-required",
                "--check",
                "--json",
            )
            review = run_taskgov(
                "task",
                "complete",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--verification-complete",
                "--commit-not-required",
                "--check",
                "--json",
            )
            target = run_taskgov(
                *complete_args(
                    db,
                    repo,
                    task["task_id"],
                    "--check",
                    "--json",
                )
            )
            conflict = run_taskgov(
                *complete_args(
                    db,
                    repo,
                    task["task_id"],
                    "--completion-evidence-kind",
                    "external_revision",
                    "--check",
                    "--json",
                )
            )

            self.assertEqual(
                json.loads(verification.stdout)["data"]["blocking_codes"],
                ["verification_required"],
            )
            self.assertEqual(
                json.loads(review.stdout)["data"]["blocking_codes"],
                ["review_required"],
            )
            self.assertEqual(
                json.loads(target.stdout)["data"]["blocking_codes"],
                ["review_target_required"],
            )
            self.assertEqual(conflict.returncode, 0)
            self.assertEqual(
                json.loads(conflict.stdout)["data"]["blocking_codes"],
                ["completion_evidence_conflict"],
            )

    def test_check_and_write_share_state_first_evidence_error_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Paused completion conflict",
                "--status",
                "in_progress",
            )
            paused = run_taskgov(
                "task",
                "edit",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--status",
                "paused",
                "--pause-reason",
                "Waiting for a deterministic dependency",
                "--json",
            )
            self.assertEqual(paused.returncode, 0, paused.stderr)
            conflicting = (
                "task",
                "complete",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--completion-evidence-kind",
                "external_revision",
                "--commit-not-required",
                "--verification-complete",
                "--review-complete",
            )

            checked = run_taskgov(*conflicting, "--check", "--json")
            written = run_taskgov(*conflicting, "--json")

            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(
                json.loads(checked.stdout)["data"]["blocking_codes"],
                ["invalid_status_transition"],
            )
            self.assertEqual(written.returncode, 1, written.stderr)
            self.assertEqual(
                json.loads(written.stdout)["errors"][0]["code"],
                "invalid_status_transition",
            )
            with closing(sqlite3.connect(db)) as connection:
                status = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
            self.assertEqual(status, "paused")

    def test_thin_complete_delegates_to_existing_edit_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Complete through thin command",
                "--status",
                "in_progress",
            )
            seed_review_evidence(db, task["task_id"])

            result = run_taskgov(
                *complete_args(db, repo, task["task_id"], "--json")
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["command"], "task.complete")
            self.assertEqual(
                set(payload["data"]),
                {"task", "changed_fields", "event"},
            )
            self.assertEqual(payload["data"]["task"]["status"], "done")
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    """
                    SELECT status, completion_evidence_kind
                      FROM tasks
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                ).fetchone()
            self.assertEqual(row, ("done", "commit_not_required"))

            text_task = add_task(
                db,
                repo,
                "Complete with text",
                "--status",
                "in_progress",
            )
            seed_review_evidence(db, text_task["task_id"])
            text_result = run_taskgov(
                *complete_args(db, repo, text_task["task_id"])
            )
            self.assertEqual(text_result.returncode, 0, text_result.stderr)
            self.assertTrue(
                text_result.stdout.startswith(
                    f"Task completed: {text_task['task_id']}\n"
                )
            )
            self.assertIn(
                "Changed: status, completed_at, completion_commit_required, "
                "completion_evidence_kind",
                text_result.stdout,
            )

    def test_check_detects_a_relevant_change_between_its_two_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Stale check",
                "--status",
                "in_progress",
            )
            seed_review_evidence(db, task["task_id"])
            original = completion_workflow.prepare_completion_plan

            def mutate_after_git(*args, **kwargs):
                plan = original(*args, **kwargs)
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute(
                        """
                        UPDATE tasks
                           SET current_contract_revision =
                               current_contract_revision + 1
                         WHERE task_id = ?
                        """,
                        (task["task_id"],),
                    )
                    connection.commit()
                return plan

            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = list(
                complete_args(
                    db,
                    repo,
                    task["task_id"],
                    "--check",
                    "--json",
                )
            )
            with mock.patch.object(
                completion_workflow,
                "prepare_completion_plan",
                side_effect=mutate_after_git,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_code = cli_module.main(argv)

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                payload["data"]["blocking_codes"],
                ["completion_check_stale"],
            )
            self.assertFalse(payload["data"]["ready"])

    def test_check_ignores_an_unrelated_task_change_between_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Stable check",
                "--status",
                "in_progress",
            )
            unrelated = add_task(db, repo, "Unrelated")
            seed_review_evidence(db, task["task_id"])
            original = completion_workflow.prepare_completion_plan

            def mutate_unrelated_after_git(*args, **kwargs):
                plan = original(*args, **kwargs)
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute(
                        """
                        UPDATE tasks
                           SET title = 'Unrelated changed',
                               updated_at = '2999-01-01T00:00:00Z'
                         WHERE task_id = ?
                        """,
                        (unrelated["task_id"],),
                    )
                    connection.commit()
                return plan

            stdout = io.StringIO()
            argv = list(
                complete_args(
                    db,
                    repo,
                    task["task_id"],
                    "--check",
                    "--json",
                )
            )
            with mock.patch.object(
                completion_workflow,
                "prepare_completion_plan",
                side_effect=mutate_unrelated_after_git,
            ), contextlib.redirect_stdout(stdout):
                exit_code = cli_module.main(argv)

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["data"]["ready"])
            self.assertEqual(payload["data"]["blocking_codes"], [])

    def test_stale_basis_wins_over_an_earlier_readiness_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Blocked then stale",
                "--status",
                "in_progress",
            )
            seed_review_evidence(db, task["task_id"])
            original = completion_workflow.prepare_completion_plan

            def block_then_mutate(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                finally:
                    with closing(sqlite3.connect(db)) as connection:
                        connection.execute(
                            """
                            UPDATE tasks
                               SET title = 'Changed during check',
                                   updated_at = '2999-01-01T00:00:00Z'
                             WHERE task_id = ?
                            """,
                            (task["task_id"],),
                        )
                        connection.commit()

            argv = [
                "task",
                "complete",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--commit-not-required",
                "--check",
                "--json",
            ]
            stdout = io.StringIO()
            with mock.patch.object(
                completion_workflow,
                "prepare_completion_plan",
                side_effect=block_then_mutate,
            ), contextlib.redirect_stdout(stdout):
                exit_code = cli_module.main(argv)

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads(stdout.getvalue())["data"]["blocking_codes"],
                ["completion_check_stale"],
            )

    def test_sequential_predecessor_and_privacy_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            first = add_task(
                db,
                repo,
                "First",
                "--kind",
                "sequential",
                "--lane",
                "FLOW",
                "--order",
                "1",
            )
            second = add_task(
                db,
                repo,
                "Second",
                "--kind",
                "sequential",
                "--lane",
                "FLOW",
                "--order",
                "2",
            )
            seed_review_evidence(db, second["task_id"])

            predecessor = run_taskgov(
                *complete_args(
                    db,
                    repo,
                    second["task_id"],
                    "--check",
                    "--json",
                )
            )
            privacy = run_taskgov(
                "task",
                "complete",
                first["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-1",
                "--completion-evidence-reason",
                "Authorization: Bearer private-token",
                "--external-revision-approved",
                "--verification-complete",
                "--review-complete",
                "--check",
                "--json",
            )

            self.assertEqual(predecessor.returncode, 0)
            self.assertEqual(
                json.loads(predecessor.stdout)["data"]["blocking_codes"],
                ["sequential_predecessor_incomplete"],
            )
            self.assertEqual(privacy.returncode, 1)
            privacy_payload = json.loads(privacy.stdout)
            self.assertFalse(privacy_payload["ok"])
            self.assertEqual(
                privacy_payload["errors"][0]["code"],
                "privacy_rejected",
            )
            self.assertNotIn("private-token", privacy.stdout)

    def test_thin_git_completion_stores_the_canonical_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Git completion",
                "--status",
                "in_progress",
            )
            seed_review_evidence(
                db,
                task["task_id"],
                target_kind="git_commit",
                target_value=revision,
            )
            typed = (
                "task",
                "complete",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--verification-complete",
                "--review-complete",
                "--completion-evidence-kind",
                "git_commit",
                "--completion-revision",
                "HEAD",
            )

            checked = run_taskgov(*typed, "--check", "--json")
            completed = run_taskgov(*typed, "--json")

            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertTrue(json.loads(checked.stdout)["data"]["ready"])
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    """
                    SELECT status, completion_evidence_kind,
                           completion_evidence_revision,
                           completion_commit_hash
                      FROM tasks
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                ).fetchone()
            self.assertEqual(
                row,
                ("done", "git_commit", revision, revision),
            )

    def test_check_closes_its_basis_transaction_before_git(self):
        class TrackedConnection:
            def __init__(self, connection):
                self.connection = connection
                self.closed = False

            def close(self):
                self.connection.close()
                self.closed = True

            def __getattr__(self, name):
                return getattr(self.connection, name)

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo)
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Git transaction boundary",
                "--status",
                "in_progress",
            )
            seed_review_evidence(
                db,
                task["task_id"],
                target_kind="git_commit",
                target_value=revision,
            )
            opened = []
            original_connect = (
                completion_workflow.connect_initialized_readonly
            )
            original_resolve = task_service.resolve_completion_request
            original_revalidate = task_service.revalidate_done_git_evidence

            def tracked_connect(target):
                tracked = TrackedConnection(original_connect(target))
                opened.append(tracked)
                return tracked

            def assert_closed_then_resolve(*args, **kwargs):
                self.assertTrue(opened)
                self.assertTrue(all(item.closed for item in opened))
                return original_resolve(*args, **kwargs)

            def assert_closed_then_revalidate(*args, **kwargs):
                self.assertTrue(opened)
                self.assertTrue(all(item.closed for item in opened))
                return original_revalidate(*args, **kwargs)

            argv = [
                "task",
                "complete",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--verification-complete",
                "--review-complete",
                "--completion-evidence-kind",
                "git_commit",
                "--completion-revision",
                "HEAD",
                "--check",
                "--json",
            ]
            stdout = io.StringIO()
            with mock.patch.object(
                completion_workflow,
                "connect_initialized_readonly",
                side_effect=tracked_connect,
            ), mock.patch.object(
                task_service,
                "resolve_completion_request",
                side_effect=assert_closed_then_resolve,
            ), mock.patch.object(
                task_service,
                "revalidate_done_git_evidence",
                side_effect=assert_closed_then_revalidate,
            ), contextlib.redirect_stdout(stdout):
                exit_code = cli_module.main(argv)

            self.assertEqual(exit_code, 0)
            self.assertTrue(json.loads(stdout.getvalue())["data"]["ready"])
            self.assertEqual(len(opened), 2)
            self.assertTrue(all(item.closed for item in opened))

    def test_locked_completion_basis_materializes_bounded_history(self):
        class RecordingCursor:
            def __init__(self, cursor, statement, observations):
                self.cursor = cursor
                self.statement = statement
                self.observations = observations

            def fetchall(self):
                rows = self.cursor.fetchall()
                normalized = " ".join(self.statement.lower().split())
                if (
                    normalized.startswith("select")
                    and (
                        " from review_receipts" in normalized
                        or " from review_findings" in normalized
                    )
                ):
                    self.observations.append((normalized, len(rows)))
                return rows

            def __getattr__(self, name):
                return getattr(self.cursor, name)

        class RecordingConnection:
            def __init__(self, connection, observations):
                self.connection = connection
                self.observations = observations

            def execute(self, statement, parameters=()):
                return RecordingCursor(
                    self.connection.execute(statement, parameters),
                    statement,
                    self.observations,
                )

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.connection, name)

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_task(
                db,
                repo,
                "Bounded history completion",
                "--status",
                "in_progress",
                "--review-tier",
                "2",
            )
            target_value = "sha256:" + ("d" * 64)
            with closing(sqlite3.connect(db)) as connection:
                project_id = connection.execute(
                    "SELECT project_id FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE tasks
                       SET review_target_kind = 'diff_fingerprint',
                           review_target_value = ?,
                           review_target_base_revision = '',
                           review_target_generation = 201
                     WHERE task_id = ?
                    """,
                    (target_value, task["task_id"]),
                )
                for generation in range(1, 201):
                    receipt_id = (
                        f"tg_review_receipt_history_{generation:04d}"
                    )
                    connection.execute(
                        """
                        INSERT INTO review_receipts(
                          review_receipt_id, task_id, project_id, reviewer_key,
                          receipt_kind, verdict, target_kind, target_value,
                          target_base_revision, target_generation, summary,
                          user_approved, created_at
                        ) VALUES (
                          ?, ?, ?, 'history-reviewer', 'independent', 'pass',
                          'diff_fingerprint', ?, '', ?, '', 0,
                          '2026-07-22T00:00:00Z'
                        )
                        """,
                        (
                            receipt_id,
                            task["task_id"],
                            project_id,
                            target_value,
                            generation,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO review_findings(
                          review_finding_id, review_receipt_id, severity,
                          status, summary, resolution_summary, created_at,
                          resolved_at
                        ) VALUES (
                          ?, ?, 'low', 'open', 'Historical low finding', '',
                          '2026-07-22T00:00:00Z', NULL
                        )
                        """,
                        (
                            f"tg_review_finding_history_{generation:04d}",
                            receipt_id,
                        ),
                    )
                for reviewer in ("current-reviewer-a", "current-reviewer-b"):
                    connection.execute(
                        """
                        INSERT INTO review_receipts(
                          review_receipt_id, task_id, project_id, reviewer_key,
                          receipt_kind, verdict, target_kind, target_value,
                          target_base_revision, target_generation, summary,
                          user_approved, created_at
                        ) VALUES (
                          ?, ?, ?, ?, 'independent', 'pass',
                          'diff_fingerprint', ?, '', 201, '', 0,
                          '2026-07-22T00:01:00Z'
                        )
                        """,
                        (
                            "tg_review_receipt_" + reviewer,
                            task["task_id"],
                            project_id,
                            reviewer,
                            target_value,
                        ),
                    )
                connection.commit()

            observations = []
            original_read = (
                completion_workflow.connect_initialized_readonly
            )
            original_write = completion_workflow.connect_initialized

            def recording_read(target):
                return RecordingConnection(
                    original_read(target),
                    observations,
                )

            def recording_write(target):
                return RecordingConnection(
                    original_write(target),
                    observations,
                )

            stdout = io.StringIO()
            argv = list(complete_args(db, repo, task["task_id"], "--json"))
            with mock.patch.object(
                completion_workflow,
                "connect_initialized_readonly",
                side_effect=recording_read,
            ), mock.patch.object(
                completion_workflow,
                "connect_initialized",
                side_effect=recording_write,
            ), contextlib.redirect_stdout(stdout):
                exit_code = cli_module.main(argv)

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads(stdout.getvalue())["data"]["task"]["status"],
                "done",
            )
            self.assertTrue(observations)
            self.assertLessEqual(
                max(row_count for _, row_count in observations),
                10,
            )


if __name__ == "__main__":
    unittest.main()
