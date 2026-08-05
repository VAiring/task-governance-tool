import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    initialize_taskgov_internal,
    internal_command_context,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import cli as cli_service  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    DATABASE_BUSY_MESSAGE,
    connect_initialized,
)


FINGERPRINT = "sha256:" + "a" * 64
BUSY_ERROR = [{"code": "database_busy", "message": DATABASE_BUSY_MESSAGE}]


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def successful_json(*args):
    result = run_taskgov(*args, "--json")
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def init_db(db, repo):
    repo.mkdir(parents=True, exist_ok=True)
    initialize_taskgov_internal(repo=repo, db=db)


def add_task(db, repo, title="Contention task"):
    return successful_json(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
    )["data"]["task"]


def set_review_target(db, repo, task_id):
    return successful_json(
        "review",
        "target",
        "set",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--kind",
        "diff_fingerprint",
        "--revision",
        FINGERPRINT,
    )["data"]["task"]


def add_review_receipt(db, repo, task_id):
    return successful_json(
        "review",
        "receipt",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--reviewer",
        "contention-reviewer",
        "--kind",
        "independent",
        "--verdict",
        "pass",
        "--reviewer-class",
        "human",
        "--model-state",
        "not_applicable",
        "--skill-state",
        "not_applicable",
        "--context-relation",
        "external_context",
    )["data"]["receipt"]


def add_review_finding(db, repo, task_id, receipt_id):
    return successful_json(
        "review",
        "finding",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--receipt-id",
        receipt_id,
        "--severity",
        "medium",
        "--summary",
        "Bounded review finding",
    )["data"]["finding"]


def record_handoff(db, repo, task_id):
    return successful_json(
        "handoff",
        "record",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--summary",
        "Deferred contention follow-up",
    )["data"]["handoff"]


def command_result(*args):
    return cli_service.handle_command(internal_command_context(*args, "--json"))


def database_rows(db):
    with closing(sqlite3.connect(db)) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                """
                SELECT name
                  FROM sqlite_master
                 WHERE type = 'table'
                   AND name NOT LIKE 'sqlite_%'
                 ORDER BY name
                """
            ).fetchall()
        ]
        return {
            table: tuple(
                sorted(
                    (tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')),
                    key=repr,
                )
            )
            for table in tables
        }


def sqlite_lock_error(error_code):
    error = sqlite3.OperationalError("sensitive SQLite lock detail")
    error.sqlite_errorcode = error_code
    return error


class BeginFailingConnection:
    def __init__(self, connection, error_code=sqlite3.SQLITE_BUSY):
        self._connection = connection
        self._error_code = error_code
        self.begin_attempts = 0

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def execute(self, statement, parameters=()):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            self.begin_attempts += 1
            raise sqlite_lock_error(self._error_code)
        return self._connection.execute(statement, parameters)

    def close(self):
        self._connection.close()


class CommitFailingConnection:
    def __init__(self, connection, error_code=sqlite3.SQLITE_LOCKED):
        self._connection = connection
        self._error_code = error_code
        self.commit_attempts = 0

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            return self._connection.__exit__(exc_type, exc_value, traceback)
        try:
            self.commit()
        except sqlite3.Error:
            self._connection.rollback()
            raise
        return False

    def commit(self):
        self.commit_attempts += 1
        raise sqlite_lock_error(self._error_code)

    def close(self):
        self._connection.close()


class WriteContentionTests(unittest.TestCase):
    def assert_busy_result(self, result, *, command, empty_data):
        self.assertFalse(result.ok)
        self.assertEqual(result.command, command)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.errors, BUSY_ERROR)
        self.assertEqual(result.data, empty_data)
        self.assertEqual(result.warnings, [])

    def test_task_add_begin_busy_is_sanitized_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            before = database_rows(db)
            wrappers = []

            def busy_connection(target):
                wrapper = BeginFailingConnection(connect_initialized(target))
                wrappers.append(wrapper)
                return wrapper

            with mock.patch.object(
                cli_service,
                "connect_initialized",
                side_effect=busy_connection,
            ) as connect_mock:
                result = command_result(
                    "task",
                    "add",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    "--title",
                    "Must not be stored",
                )

            self.assert_busy_result(result, command="task.add", empty_data={})
            self.assertEqual(connect_mock.call_count, 1)
            self.assertEqual(len(wrappers), 1)
            self.assertEqual(wrappers[0].begin_attempts, 1)
            self.assertEqual(database_rows(db), before)

    def test_task_edit_context_commit_locked_is_sanitized_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            task = add_task(db, repo)
            before = database_rows(db)
            wrappers = []

            def locked_commit_connection(target):
                wrapper = CommitFailingConnection(connect_initialized(target))
                wrappers.append(wrapper)
                return wrapper

            with mock.patch.object(
                cli_service,
                "connect_initialized",
                side_effect=locked_commit_connection,
            ) as connect_mock:
                result = command_result(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task["task_id"],
                    "--title",
                    "Must roll back",
                )

            self.assert_busy_result(
                result,
                command="task.edit",
                empty_data={"task": None, "changed_fields": [], "event": None},
            )
            self.assertEqual(connect_mock.call_count, 1)
            self.assertEqual(len(wrappers), 1)
            self.assertEqual(wrappers[0].commit_attempts, 1)
            self.assertEqual(database_rows(db), before)

    def test_review_write_commands_begin_busy_are_sanitized_and_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            task = add_task(db, repo, title="Review contention task")
            task_id = task["task_id"]

            cases = [
                (
                    "target",
                    (
                        "review",
                        "target",
                        "set",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        task_id,
                        "--kind",
                        "diff_fingerprint",
                        "--revision",
                        FINGERPRINT,
                    ),
                    "review.target.set",
                    {"task": None, "changed_fields": [], "event": None},
                )
            ]

            for name, command, expected_command, empty_data in cases:
                with self.subTest(operation=name):
                    self._assert_review_begin_busy(
                        db,
                        command,
                        expected_command=expected_command,
                        empty_data=empty_data,
                    )

            set_review_target(db, repo, task_id)
            receipt_command = (
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--reviewer",
                "busy-reviewer",
                "--kind",
                "independent",
                "--verdict",
                "pass",
                "--reviewer-class",
                "human",
                "--model-state",
                "not_applicable",
                "--skill-state",
                "not_applicable",
                "--context-relation",
                "external_context",
            )
            with self.subTest(operation="receipt"):
                self._assert_review_commit_locked(
                    db,
                    receipt_command,
                    expected_command="review.receipt.add",
                    empty_data={"receipt": None, "event": None},
                )

            receipt = add_review_receipt(db, repo, task_id)
            finding_command = (
                "review",
                "finding",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--receipt-id",
                receipt["review_receipt_id"],
                "--severity",
                "medium",
                "--summary",
                "Must not be stored",
            )
            with self.subTest(operation="finding.add"):
                self._assert_review_begin_busy(
                    db,
                    finding_command,
                    expected_command="review.finding.add",
                    empty_data={"finding": None, "event": None},
                )

            finding = add_review_finding(
                db,
                repo,
                task_id,
                receipt["review_receipt_id"],
            )
            resolve_command = (
                "review",
                "finding",
                "resolve",
                "--repo",
                str(repo),
                "--db",
                str(db),
                finding["review_finding_id"],
                "--resolution",
                "Must not be stored",
            )
            with self.subTest(operation="finding.resolve"):
                self._assert_review_begin_busy(
                    db,
                    resolve_command,
                    expected_command="review.finding.resolve",
                    empty_data={"finding": None, "event": None},
                )

    def _assert_review_begin_busy(
        self,
        db,
        command,
        *,
        expected_command,
        empty_data,
    ):
        before = database_rows(db)
        wrappers = []

        def busy_connection(target):
            wrapper = BeginFailingConnection(connect_initialized(target))
            wrappers.append(wrapper)
            return wrapper

        with mock.patch.object(
            cli_service,
            "connect_initialized",
            side_effect=busy_connection,
        ) as connect_mock:
            result = command_result(*command)

        self.assert_busy_result(
            result,
            command=expected_command,
            empty_data=empty_data,
        )
        self.assertEqual(connect_mock.call_count, 1)
        self.assertEqual(len(wrappers), 1)
        self.assertEqual(wrappers[0].begin_attempts, 1)
        self.assertEqual(database_rows(db), before)

    def _assert_review_commit_locked(
        self,
        db,
        command,
        *,
        expected_command,
        empty_data,
    ):
        before = database_rows(db)
        wrappers = []

        def locked_commit_connection(target):
            wrapper = CommitFailingConnection(connect_initialized(target))
            wrappers.append(wrapper)
            return wrapper

        with mock.patch.object(
            cli_service,
            "connect_initialized",
            side_effect=locked_commit_connection,
        ) as connect_mock:
            result = command_result(*command)

        self.assert_busy_result(
            result,
            command=expected_command,
            empty_data=empty_data,
        )
        self.assertEqual(connect_mock.call_count, 1)
        self.assertEqual(len(wrappers), 1)
        self.assertEqual(wrappers[0].commit_attempts, 1)
        self.assertEqual(database_rows(db), before)

    def test_handoff_withdraw_commit_locked_is_sanitized_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            db = root / "taskgov.sqlite"
            init_db(db, repo)
            task = add_task(db, repo)
            handoff = record_handoff(db, repo, task["task_id"])
            before = database_rows(db)
            wrappers = []

            def locked_commit_connection(target):
                wrapper = CommitFailingConnection(connect_initialized(target))
                wrappers.append(wrapper)
                return wrapper

            with mock.patch.object(
                cli_service,
                "connect_initialized",
                side_effect=locked_commit_connection,
            ) as connect_mock:
                result = command_result(
                    "handoff",
                    "withdraw",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    handoff["handoff_id"],
                    "--reason",
                    "Must roll back",
                )

            self.assert_busy_result(
                result,
                command="handoff.withdraw",
                empty_data={"handoff": None, "changed_fields": []},
            )
            self.assertEqual(connect_mock.call_count, 1)
            self.assertEqual(len(wrappers), 1)
            self.assertEqual(wrappers[0].commit_attempts, 1)
            self.assertEqual(database_rows(db), before)


if __name__ == "__main__":
    unittest.main()
