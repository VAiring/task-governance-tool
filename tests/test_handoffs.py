import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    initialize_taskgov_internal,
    internal_command_context,
    remove_v10_maintenance_for_test,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.cli import handle_command  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    apply_handoff_outbox_migration,
    apply_migrations,
    connect,
    connect_initialized,
    connect_initialized_readonly,
    project_identity,
)


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def json_command(*args):
    result = run_taskgov(*args, "--json")
    payload = json.loads(result.stdout)
    return result, payload


def init_db(db, repo):
    return initialize_taskgov_internal(repo=repo, db=db)


def add_task(db, repo, title="Source task"):
    result, payload = json_command(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return payload["data"]["task"]


def record(db, repo, task_id, summary="Out-of-scope improvement", *extra):
    return json_command(
        "handoff",
        "record",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--summary",
        summary,
        *extra,
    )


class HandoffCommandTests(unittest.TestCase):
    def test_record_is_durable_additive_and_rediscoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                task_before = connection.execute(
                    "SELECT updated_at FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]
                event_count_before = connection.execute(
                    "SELECT COUNT(*) FROM task_events"
                ).fetchone()[0]

            result, payload = record(
                db,
                repo,
                task["task_id"],
                "Move optional hardening to an Issue candidate",
                "--rationale",
                "Current acceptance does not require it",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "handoff.record")
            handoff = payload["data"]["handoff"]
            local = payload["data"]["local_record"]
            self.assertEqual(handoff["state"], "pending_handoff")
            self.assertEqual(handoff["source_contract_revision"], 0)
            self.assertEqual(handoff["source_task_id"], task["task_id"])
            self.assertNotIn("claim_token", handoff)
            self.assertEqual(
                local,
                {
                    "durable": True,
                    "created": True,
                    "replayed": False,
                    "handoff_id": handoff["handoff_id"],
                },
            )

            list_result, listed = json_command(
                "handoff",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--read-only",
            )
            self.assertEqual(list_result.returncode, 0, list_result.stderr)
            self.assertEqual(listed["data"]["count"], 1)
            self.assertEqual(listed["data"]["total_matching"], 1)
            self.assertEqual(listed["data"]["states"], ["pending_handoff"])
            self.assertEqual(
                listed["data"]["handoffs"][0]["handoff_id"],
                handoff["handoff_id"],
            )

            _, shown = json_command(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--read-only",
            )
            self.assertEqual(
                shown["data"]["handoff_summary"],
                {
                    "pending_handoff": 1,
                    "handed_off": 0,
                    "handoff_withdrawn_by_user": 0,
                },
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT updated_at FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    task_before,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
                    event_count_before,
                )

    def test_exact_replay_and_explicit_occurrence_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)

            _, first = record(db, repo, task["task_id"])
            _, replay = record(db, repo, task["task_id"])
            _, distinct = record(
                db,
                repo,
                task["task_id"],
                "Out-of-scope improvement",
                "--occurrence-id",
                "user-request-002",
            )

            self.assertEqual(
                replay["data"]["handoff"]["handoff_id"],
                first["data"]["handoff"]["handoff_id"],
            )
            self.assertEqual(
                replay["data"]["local_record"],
                {
                    "durable": True,
                    "created": False,
                    "replayed": True,
                    "handoff_id": first["data"]["handoff"]["handoff_id"],
                },
            )
            self.assertNotEqual(
                distinct["data"]["handoff"]["handoff_id"],
                first["data"]["handoff"]["handoff_id"],
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0],
                    2,
                )

    def test_list_is_bounded_oldest_first_and_terminal_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            handoff_ids = []
            for index in range(3):
                _, payload = record(
                    db,
                    repo,
                    task["task_id"],
                    f"Discovery {index}",
                    "--occurrence-id",
                    f"occurrence-{index}",
                )
                handoff_ids.append(payload["data"]["handoff"]["handoff_id"])
            with closing(sqlite3.connect(db)) as connection:
                for index, handoff_id in enumerate(handoff_ids):
                    connection.execute(
                        "UPDATE handoff_records SET created_at = ? WHERE handoff_id = ?",
                        (f"2026-07-26T00:00:0{index}Z", handoff_id),
                    )
                connection.commit()

            _, withdrawn = json_command(
                "handoff",
                "withdraw",
                "--repo",
                str(repo),
                "--db",
                str(db),
                handoff_ids[0],
                "--reason",
                "The user handled this outside Task Skill",
            )
            self.assertEqual(
                withdrawn["data"]["changed_fields"],
                ["state", "withdraw_reason", "withdrawn_at"],
            )
            self.assertEqual(
                withdrawn["data"]["handoff"]["state"],
                "handoff_withdrawn_by_user",
            )

            _, pending = json_command(
                "handoff",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--limit",
                "1",
            )
            self.assertEqual(pending["data"]["count"], 1)
            self.assertEqual(pending["data"]["total_matching"], 2)
            self.assertEqual(
                pending["data"]["handoffs"][0]["handoff_id"],
                handoff_ids[1],
            )
            _, terminal = json_command(
                "handoff",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--state",
                "handoff_withdrawn_by_user",
            )
            self.assertEqual(terminal["data"]["total_matching"], 1)
            self.assertEqual(
                terminal["data"]["handoffs"][0]["handoff_id"],
                handoff_ids[0],
            )
            second_withdraw, second_payload = json_command(
                "handoff",
                "withdraw",
                "--repo",
                str(repo),
                "--db",
                str(db),
                handoff_ids[0],
                "--reason",
                "Try again",
            )
            self.assertEqual(second_withdraw.returncode, 1)
            self.assertEqual(
                second_payload["errors"][0]["code"],
                "handoff_not_withdrawable",
            )

    def test_occurrence_validation_and_compact_list_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            for invalid in ("   ", "x" * 201):
                with self.subTest(length=len(invalid)):
                    result, payload = record(
                        db,
                        repo,
                        task["task_id"],
                        "Safe summary",
                        "--occurrence-id",
                        invalid,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(
                        payload["errors"][0]["code"],
                        "handoff_occurrence_invalid",
                    )
            _, created = record(db, repo, task["task_id"])
            _, listed = json_command(
                "handoff",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
            )
            self.assertEqual(
                set(listed["data"]["handoffs"][0]),
                {
                    "handoff_id",
                    "source_task_id",
                    "source_contract_revision",
                    "summary",
                    "state",
                    "created_at",
                    "updated_at",
                },
            )
            self.assertEqual(
                listed["data"]["handoffs"][0]["handoff_id"],
                created["data"]["handoff"]["handoff_id"],
            )

    def test_cli_list_and_show_use_one_validated_read_transaction_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            _, created = record(db, repo, task["task_id"])
            handoff_id = created["data"]["handoff"]["handoff_id"]
            list_context = internal_command_context(
                "handoff",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--read-only",
                "--json",
            )
            show_context = internal_command_context(
                "handoff",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                handoff_id,
                "--read-only",
                "--json",
            )

            def open_validated(target):
                connection = connect_initialized_readonly(target)
                self.assertTrue(connection.in_transaction)
                self.assertEqual(
                    connection.execute("PRAGMA query_only").fetchone()[0],
                    1,
                )
                return connection

            with mock.patch(
                "task_governance_tool.cli.connect_initialized_readonly",
                side_effect=open_validated,
            ) as validated_reader:
                list_result = handle_command(list_context)
                show_result = handle_command(show_context)

            self.assertTrue(list_result.ok)
            self.assertEqual(list_result.data["count"], 1)
            self.assertEqual(list_result.data["total_matching"], 1)
            self.assertTrue(show_result.ok)
            self.assertEqual(show_result.data["handoff"]["handoff_id"], handoff_id)
            self.assertEqual(validated_reader.call_count, 2)
            for call in validated_reader.call_args_list:
                target = call.args[0]
                self.assertEqual(target.db_path, db.resolve())
                self.assertEqual(target.project.project_id, task["project_id"])

            with mock.patch(
                "task_governance_tool.cli.connect_initialized_readonly",
                side_effect=StorageError(
                    "internal_error",
                    "validated reader unavailable",
                ),
            ):
                failure = handle_command(list_context)
            self.assertFalse(failure.ok)
            self.assertEqual(failure.errors[0]["code"], "internal_error")
            self.assertEqual(
                failure.data,
                {
                    "handoffs": [],
                    "count": 0,
                    "total_matching": 0,
                    "limit": 0,
                    "states": [],
                },
            )

    def test_corrupt_or_private_stored_handoff_is_not_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            _, created = record(db, repo, task["task_id"])
            handoff_id = created["data"]["handoff"]["handoff_id"]

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE handoff_records SET summary = ? WHERE handoff_id = ?",
                    ("Authorization: Bearer secret-value", handoff_id),
                )
                connection.commit()
            private_result, private_payload = json_command(
                "handoff",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                handoff_id,
            )
            self.assertEqual(private_result.returncode, 2)
            self.assertEqual(private_payload["errors"][0]["code"], "internal_error")
            self.assertNotIn("secret-value", private_result.stdout)

            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    UPDATE handoff_records
                       SET summary = 'Safe summary',
                           state = 'handoff_withdrawn_by_user',
                           withdraw_reason = '',
                           withdrawn_at = NULL
                     WHERE handoff_id = ?
                    """,
                    (handoff_id,),
                )
                connection.commit()
            state_result, state_payload = json_command(
                "handoff",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                handoff_id,
            )
            self.assertEqual(state_result.returncode, 2)
            self.assertEqual(state_payload["errors"][0]["code"], "internal_error")
            self.assertIn("state fields", state_payload["errors"][0]["message"])

    def test_corrupt_stored_counter_returns_fixed_errors_without_leaking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            _, created = record(db, repo, task["task_id"])
            handoff_id = created["data"]["handoff"]["handoff_id"]
            corrupt_value = "not-an-integer"
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute(
                    "UPDATE handoff_records SET delivery_attempts = ? WHERE handoff_id = ?",
                    (corrupt_value, handoff_id),
                )
                connection.commit()

            commands = (
                ("handoff", "show", "--repo", str(repo), "--db", str(db), handoff_id),
                ("handoff", "list", "--repo", str(repo), "--db", str(db)),
                ("task", "show", "--repo", str(repo), "--db", str(db), task["task_id"]),
            )
            for command in commands:
                with self.subTest(command=command[:2]):
                    result, payload = json_command(*command)
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["errors"][0]["code"], "internal_error")
                    self.assertNotIn(corrupt_value, result.stdout)
                    self.assertEqual(result.stderr, "")
            self.assertIsNone(payload["data"]["handoff_summary"])

    def test_corrupt_public_timestamp_returns_fixed_internal_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            _, created = record(db, repo, task["task_id"])
            handoff_id = created["data"]["handoff"]["handoff_id"]
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE handoff_records SET created_at = ? WHERE handoff_id = ?",
                    (sqlite3.Binary(b"\xff\x00private"), handoff_id),
                )
                connection.commit()

            result, payload = json_command(
                "handoff",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                handoff_id,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["errors"][0]["code"], "internal_error")
            self.assertEqual(payload["data"], {"handoff": None})
            self.assertEqual(result.stderr, "")
            self.assertNotIn("private", result.stdout)

    def test_privacy_rejection_covers_all_free_form_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            cases = (
                ("--summary", "Authorization: Bearer secret-value"),
                ("--rationale", "raw stdout: failure output"),
                ("--occurrence-id", "Password=do-not-store"),
            )
            for option, value in cases:
                args = [
                    "handoff",
                    "record",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task["task_id"],
                    "--summary",
                    "Safe summary",
                ]
                if option == "--summary":
                    args[-1] = value
                else:
                    args.extend([option, value])
                with self.subTest(option=option):
                    result, payload = json_command(*args)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(payload["errors"][0]["code"], "privacy_rejected")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0],
                    0,
                )

            _, created = record(db, repo, task["task_id"])
            handoff_id = created["data"]["handoff"]["handoff_id"]
            result, payload = json_command(
                "handoff",
                "withdraw",
                "--repo",
                str(repo),
                "--db",
                str(db),
                handoff_id,
                "--reason",
                "raw stderr: secret failure",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(payload["errors"][0]["code"], "privacy_rejected")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM handoff_records WHERE handoff_id = ?",
                        (handoff_id,),
                    ).fetchone()[0],
                    "pending_handoff",
                )

    def test_one_sanitized_abstraction_after_privacy_rejection_stores_no_raw_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            rejected_value = "Authorization: Bearer raw-m13-secret-value"

            rejected, rejected_payload = json_command(
                "handoff",
                "record",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--summary",
                rejected_value,
            )

            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(
                rejected_payload["errors"][0]["code"],
                "privacy_rejected",
            )
            self.assertNotIn("raw-m13-secret-value", rejected.stdout)
            self.assertNotIn("raw-m13-secret-value", rejected.stderr)

            sanitized, sanitized_payload = json_command(
                "handoff",
                "record",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--summary",
                "Authentication configuration needs separate investigation",
                "--rationale",
                "Outside the current acceptance criteria",
            )

            self.assertEqual(sanitized.returncode, 0, sanitized.stderr)
            self.assertTrue(
                sanitized_payload["data"]["local_record"]["durable"],
            )
            with closing(sqlite3.connect(db)) as connection:
                rows = connection.execute(
                    "SELECT summary, rationale FROM handoff_records"
                ).fetchall()
            self.assertEqual(
                rows,
                [
                    (
                        "Authentication configuration needs separate investigation",
                        "Outside the current acceptance criteria",
                    )
                ],
            )
            self.assertNotIn(b"raw-m13-secret-value", db.read_bytes())

    def test_read_only_and_missing_database_paths_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing" / "taskgov.sqlite"
            repo = root / "repo"
            result, payload = json_command(
                "handoff",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(missing),
                "--read-only",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
            self.assertFalse(missing.exists())
            self.assertFalse(missing.parent.exists())

            db = root / "taskgov.sqlite"
            init_db(db, repo)
            task = add_task(db, repo)
            record_result, record_payload = json_command(
                "handoff",
                "record",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--summary",
                "Must not write",
                "--read-only",
            )
            self.assertEqual(record_result.returncode, 1)
            self.assertEqual(record_payload["errors"][0]["code"], "invalid_argument")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0],
                    0,
                )

    def test_project_mismatch_and_unknown_source_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo_one = root / "repo-one"
            repo_two = root / "repo-two"
            init_db(db, repo_one)
            task = add_task(db, repo_one)
            result, payload = record(db, repo_two, task["task_id"])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["errors"][0]["code"], "project_mismatch")
            unknown, unknown_payload = record(db, repo_one, "tg_task_missing")
            self.assertEqual(unknown.returncode, 1)
            self.assertEqual(unknown_payload["errors"][0]["code"], "not_found")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0],
                    0,
                )

    def test_concurrent_exact_record_creates_one_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)

            def worker():
                return record(db, repo, task["task_id"])

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: worker(), range(2)))
            self.assertTrue(all(result.returncode == 0 for result, _ in results))
            ids = {
                payload["data"]["handoff"]["handoff_id"]
                for _, payload in results
            }
            self.assertEqual(len(ids), 1)
            receipts = [payload["data"]["local_record"] for _, payload in results]
            self.assertEqual(sum(receipt["created"] for receipt in receipts), 1)
            self.assertEqual(sum(receipt["replayed"] for receipt in receipts), 1)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0],
                    1,
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_concurrent_withdraw_has_one_winner_and_does_not_touch_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)
            _, created = record(db, repo, task["task_id"])
            handoff_id = created["data"]["handoff"]["handoff_id"]
            with closing(sqlite3.connect(db)) as connection:
                before_task = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                before_events = connection.execute(
                    "SELECT * FROM task_events ORDER BY rowid"
                ).fetchall()

            def worker(index):
                return json_command(
                    "handoff",
                    "withdraw",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    handoff_id,
                    "--reason",
                    f"User handled occurrence {index}",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(worker, range(2)))
            self.assertEqual(
                sorted(result.returncode for result, _ in outcomes),
                [0, 1],
            )
            loser = next(payload for result, payload in outcomes if result.returncode == 1)
            self.assertEqual(
                loser["errors"][0]["code"],
                "handoff_not_withdrawable",
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT state FROM handoff_records WHERE handoff_id = ?",
                        (handoff_id,),
                    ).fetchone()[0],
                    "handoff_withdrawn_by_user",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone(),
                    before_task,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT * FROM task_events ORDER BY rowid"
                    ).fetchall(),
                    before_events,
                )

    def test_pending_handoff_does_not_block_source_task_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            add_result, add_payload = json_command(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Finish accepted scope",
                "--review-tier",
                "0",
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)
            task = add_payload["data"]["task"]
            _, handoff = record(
                db,
                repo,
                task["task_id"],
                "Optional hardening belongs outside this task",
            )
            fingerprint = "sha256:" + ("a" * 64)
            target_result, _ = json_command(
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
                fingerprint,
            )
            self.assertEqual(target_result.returncode, 0, target_result.stderr)
            receipt_result, _ = json_command(
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--reviewer",
                "tier0-not-required",
                "--kind",
                "not_required",
                "--verdict",
                "not_required",
                "--summary",
                "Mechanical forward-flow fixture",
            )
            self.assertEqual(receipt_result.returncode, 0, receipt_result.stderr)
            done_result, done_payload = json_command(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--status",
                "done",
                "--commit-not-required",
                "--verification-complete",
                "--review-complete",
            )
            self.assertEqual(done_result.returncode, 0, done_result.stderr)
            self.assertEqual(done_payload["data"]["task"]["status"], "done")
            _, pending = json_command(
                "handoff",
                "list",
                "--repo",
                str(repo),
                "--db",
                str(db),
            )
            self.assertEqual(pending["data"]["total_matching"], 1)
            self.assertEqual(
                pending["data"]["handoffs"][0]["handoff_id"],
                handoff["data"]["handoff"]["handoff_id"],
            )
            self.assertEqual(pending["warnings"], [])

    def test_commit_phase_failure_retries_with_new_transaction_and_never_false_durable(self):
        class CommitFailingConnection:
            def __init__(self, connection):
                self._connection = connection

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def commit(self):
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error

            def close(self):
                self._connection.close()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task = add_task(db, repo)

            def context_for(summary):
                return internal_command_context(
                    "handoff",
                    "record",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task["task_id"],
                    "--summary",
                    summary,
                    "--json",
                )

            attempts = 0

            def fail_first_commit(target):
                nonlocal attempts
                attempts += 1
                connection = connect_initialized(target)
                if attempts == 1:
                    return CommitFailingConnection(connection)
                return connection

            with mock.patch(
                "task_governance_tool.cli.connect_initialized",
                side_effect=fail_first_commit,
            ) as connect_mock:
                recovered = handle_command(context_for("Recovered after busy commit"))
            self.assertTrue(recovered.ok)
            self.assertTrue(recovered.data["local_record"]["durable"])
            self.assertTrue(recovered.data["local_record"]["created"])
            self.assertEqual(connect_mock.call_count, 2)

            def always_fail_commit(target):
                return CommitFailingConnection(connect_initialized(target))

            with mock.patch(
                "task_governance_tool.cli.connect_initialized",
                side_effect=always_fail_commit,
            ) as connect_mock:
                failed = handle_command(context_for("Must never become durable"))
            self.assertFalse(failed.ok)
            self.assertEqual(failed.exit_code, 2)
            self.assertEqual(failed.errors[0]["code"], "database_busy")
            self.assertEqual(
                failed.errors[0]["message"],
                "task database is busy; run the command again later",
            )
            self.assertEqual(
                failed.data,
                {
                    "handoff": None,
                    "local_record": {
                        "durable": False,
                        "created": False,
                        "replayed": False,
                        "handoff_id": None,
                    },
                },
            )
            self.assertEqual(connect_mock.call_count, 2)
            with closing(sqlite3.connect(db)) as connection:
                rows = connection.execute(
                    "SELECT summary FROM handoff_records ORDER BY created_at, handoff_id"
                ).fetchall()
            self.assertEqual(rows, [("Recovered after busy commit",)])

    def test_transient_record_failure_retries_once_then_is_not_durable(self):
        context = internal_command_context(
            "handoff",
            "record",
            "--repo",
            ".",
            "--db",
            "unused.sqlite",
            "tg_task_source",
            "--summary",
            "Safe summary",
            "--json",
        )
        with mock.patch(
            "task_governance_tool.cli.connect_initialized",
            side_effect=sqlite3.OperationalError("database is locked"),
        ) as connect_mock:
            result = handle_command(context)

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.errors[0]["code"], "database_busy")
        self.assertEqual(
            result.errors[0]["message"],
            "task database is busy; run the command again later",
        )
        self.assertEqual(
            result.data,
            {
                "handoff": None,
                "local_record": {
                    "durable": False,
                    "created": False,
                    "replayed": False,
                    "handoff_id": None,
                },
            },
        )
        self.assertEqual(connect_mock.call_count, 2)


class HandoffMigrationTests(unittest.TestCase):
    def test_v6_to_v7_adds_only_outbox_and_preserves_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            identity = project_identity(repo)
            with closing(connect(db)) as connection:
                applied, _ = apply_migrations(connection)
                self.assertEqual(
                    applied,
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
                )
                remove_v10_maintenance_for_test(connection)
                connection.execute("DELETE FROM schema_migrations WHERE version = 9")
                connection.execute("DROP TABLE task_effort_bases")
                connection.execute("DROP TABLE task_effort_activity")
                connection.execute(
                    "ALTER TABLE project_meta DROP COLUMN effort_activity_generation"
                )
                connection.execute("DELETE FROM schema_migrations WHERE version = 8")
                connection.execute("DROP TABLE task_contract_revisions")
                connection.execute(
                    "ALTER TABLE tasks DROP COLUMN current_contract_revision"
                )
                connection.execute("DELETE FROM schema_migrations WHERE version = 7")
                connection.execute("DROP TABLE handoff_records")
                connection.commit()
                before = {
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
                apply_handoff_outbox_migration(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    7,
                )
                self.assertEqual(
                    {
                        table: connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                        for table in before
                    },
                    before,
                )
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(handoff_records)"
                    ).fetchall()
                }
                expected_columns = {
                    "handoff_id",
                    "project_id",
                    "source_task_id",
                    "source_contract_revision",
                    "idempotency_key",
                    "occurrence_id",
                    "summary",
                    "rationale",
                    "state",
                    "adapter_key",
                    "adapter_version",
                    "delivery_attempts",
                    "last_delivery_code",
                    "next_attempt_at",
                    "claim_token",
                    "claim_expires_at",
                    "receiver_receipt",
                    "withdraw_reason",
                    "created_at",
                    "updated_at",
                    "handed_off_at",
                    "withdrawn_at",
                }
                self.assertEqual(columns, expected_columns)
                indexes = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA index_list(handoff_records)"
                    ).fetchall()
                }
                self.assertTrue(
                    {
                        "idx_handoff_project_state_created",
                        "idx_handoff_project_source",
                        "idx_handoff_due_claim",
                    }.issubset(indexes)
                )
                self.assertNotIn("priority", columns)
                self.assertNotIn("issue_state", columns)
                self.assertNotIn("resulting_task_id", columns)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertTrue(identity.project_id)

    def test_v7_migration_failure_rolls_back_schema_and_version(self):
        for fail_stage in ("after_schema", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "taskgov.sqlite"
                with closing(connect(db)) as connection:
                    apply_migrations(connection)
                    remove_v10_maintenance_for_test(connection)
                    connection.execute("DELETE FROM schema_migrations WHERE version = 9")
                    connection.execute("DROP TABLE task_effort_bases")
                    connection.execute("DROP TABLE task_effort_activity")
                    connection.execute(
                        "ALTER TABLE project_meta DROP COLUMN effort_activity_generation"
                    )
                    connection.execute("DELETE FROM schema_migrations WHERE version = 8")
                    connection.execute("DROP TABLE task_contract_revisions")
                    connection.execute(
                        "ALTER TABLE tasks DROP COLUMN current_contract_revision"
                    )
                    connection.execute("DELETE FROM schema_migrations WHERE version = 7")
                    connection.execute("DROP TABLE handoff_records")
                    connection.commit()
                    with self.assertRaisesRegex(Exception, "injected handoff-outbox"):
                        apply_handoff_outbox_migration(
                            connection,
                            fail_stage=fail_stage,
                        )
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table' AND name = 'handoff_records'
                            """
                        ).fetchone()
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        6,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )


if __name__ == "__main__":
    unittest.main()
