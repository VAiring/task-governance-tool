import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import (
    initialize_taskgov_internal,
    remove_v10_maintenance_for_test,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    apply_task_contract_migration,
    connect,
    connect_initialized,
    resolve_database_target,
)
from task_governance_tool.tasks import edit_task  # noqa: E402


def run_taskgov(*args):
    return run_taskgov_internal(*args)


def json_command(*args):
    result = run_taskgov(*args, "--json")
    return result, json.loads(result.stdout)


def init_db(db, repo):
    return initialize_taskgov_internal(repo=repo, db=db)


def add_task(db, repo, title, *extra):
    result, payload = json_command(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        *extra,
    )
    return result, payload


def edit_contract(db, repo, task_id, scope, acceptance, *extra):
    return json_command(
        "task",
        "edit",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--contract-scope",
        scope,
        "--contract-acceptance",
        acceptance,
        *extra,
    )


def durable_contract_state(db, task_id):
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        task = dict(
            connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        )
        revisions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                  FROM task_contract_revisions
                 WHERE task_id = ?
                 ORDER BY revision
                """,
                (task_id,),
            ).fetchall()
        ]
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                  FROM task_events
                 WHERE task_id = ?
                 ORDER BY created_at, rowid
                """,
                (task_id,),
            ).fetchall()
        ]
    return task, revisions, events


class TaskContractCliTests(unittest.TestCase):
    def test_revision_zero_preserves_ordinary_shapes_and_show_adds_fixed_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)

            result, added = add_task(db, repo, "Ordinary task")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(added["data"]), {"task", "event"})
            task = added["data"]["task"]
            self.assertNotIn("current_contract_revision", task)
            shown_result, shown = json_command(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
            )
            self.assertEqual(shown_result.returncode, 0, shown_result.stderr)
            self.assertEqual(
                shown["data"]["contract"],
                {
                    "revision": 0,
                    "scope": "",
                    "acceptance": "",
                    "constraints": "",
                    "authority_ref": "",
                    "change_reason": "",
                    "created_at": None,
                },
            )
            for command in (
                ("task", "list"),
                ("task", "next"),
            ):
                command_result, payload = json_command(
                    *command,
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                )
                self.assertEqual(command_result.returncode, 0, command_result.stderr)
                self.assertTrue(payload["data"]["tasks"])
                self.assertNotIn("contract", payload["data"]["tasks"][0])
                self.assertNotIn(
                    "current_contract_revision",
                    payload["data"]["tasks"][0],
                )

    def test_add_contract_activation_matrix_and_conditional_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            allowed = ("ready", "in_progress", "blocked", "review_pending")
            for status in allowed:
                extra = [
                    "--status",
                    status,
                    "--contract-scope",
                    f"Implement {status} boundary",
                    "--contract-acceptance",
                    "Focused checks pass",
                    "--contract-authority-ref",
                    "roadmap:TG-M12.2",
                ]
                if status == "blocked":
                    extra.extend(["--blocked-reason", "Waiting for local input"])
                result, payload = add_task(
                    db,
                    repo,
                    f"Contract {status}",
                    *extra,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    payload["data"]["contract_write"],
                    {"recorded": True, "revision": 1},
                )
                self.assertEqual(payload["data"]["event"]["event_type"], "task_added")
                task_id = payload["data"]["task"]["task_id"]
                _, shown = json_command(
                    "task",
                    "show",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                )
                self.assertEqual(shown["data"]["contract"]["revision"], 1)

            for status in ("paused", "done", "cancelled"):
                result, payload = add_task(
                    db,
                    repo,
                    f"Forbidden {status}",
                    "--status",
                    status,
                    "--contract-scope",
                    "Forbidden scope",
                    "--contract-acceptance",
                    "Must not be stored",
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    payload["errors"][0]["code"],
                    "contract_activation_forbidden",
                )

    def test_contract_group_validation_and_privacy_precede_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            cases = (
                (
                    ["--contract-scope", "Only one field"],
                    "invalid_argument",
                ),
                (
                    [
                        "--contract-scope",
                        "Initial scope",
                        "--contract-acceptance",
                        "Initial acceptance",
                        "--contract-change-reason",
                        "Not an initial change",
                    ],
                    "invalid_argument",
                ),
                (
                    [
                        "--contract-scope",
                        "Initial scope",
                        "--contract-acceptance",
                        "Initial acceptance",
                        "--contract-change-reason",
                        "",
                    ],
                    "invalid_argument",
                ),
                (
                    [
                        "--contract-scope",
                        "Initial scope",
                        "--contract-acceptance",
                        "Initial acceptance",
                        "--contract-change-reason",
                        "   ",
                    ],
                    "invalid_argument",
                ),
                (
                    [
                        "--contract-scope",
                        "Authorization: Bearer secret",
                        "--contract-acceptance",
                        "Initial acceptance",
                    ],
                    "privacy_rejected",
                ),
            )
            for index, (extra, expected_code) in enumerate(cases):
                result, payload = add_task(
                    db,
                    repo,
                    f"Rejected contract {index}",
                    *extra,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(payload["errors"][0]["code"], expected_code)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_contract_revisions"
                    ).fetchone()[0],
                    0,
                )

    def test_new_contract_rejects_legacy_counter_and_accepts_neutral_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            legacy_constraints = (
                "Required user approval:\n"
                "- The initial approval names `dispatch_authorization=1`; "
                "every later fresh dispatch approval increments it by exactly one.\n"
                "Approval:\n"
                '{"dispatch_authorization":1,"schema":"m19.7-approval-v1"}'
            )

            rejected, rejected_payload = add_task(
                db,
                repo,
                "M19.7 contract",
                "--contract-scope",
                "Publish the accepted candidate only.",
                "--contract-acceptance",
                "The exact candidate CI run passes.",
                "--contract-constraints",
                legacy_constraints,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(
                rejected_payload["errors"][0]["code"],
                "privacy_rejected",
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM tasks"
                    ).fetchone()[0],
                    0,
                )

            neutral_constraints = (
                "Current user approval remains separate from stored evidence.\n"
                "The release operation uses operation_sequence=1 only for "
                "correlation and idempotency."
            )
            result, payload = add_task(
                db,
                repo,
                "Future release contract",
                "--contract-scope",
                "Prepare one bounded future release operation.",
                "--contract-acceptance",
                "The approved operation is correlated deterministically.",
                "--contract-constraints",
                neutral_constraints,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            task_id = payload["data"]["task"]["task_id"]
            _, revisions, _ = durable_contract_state(db, task_id)
            self.assertEqual(
                revisions[0]["constraints_text"],
                neutral_constraints,
            )
            shown_result, shown = json_command(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
            )
            self.assertEqual(shown_result.returncode, 0, shown_result.stderr)
            self.assertEqual(
                shown["data"]["contract"]["constraints"],
                neutral_constraints,
            )

    def test_revision_zero_edit_requires_exact_start_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            for status in ("ready", "blocked"):
                extra = ["--status", status]
                if status == "blocked":
                    extra.extend(["--blocked-reason", "Waiting"])
                _, added = add_task(db, repo, f"Activate {status}", *extra)
                task_id = added["data"]["task"]["task_id"]
                result, payload = edit_contract(
                    db,
                    repo,
                    task_id,
                    "Bounded implementation",
                    "Acceptance passes",
                    "--status",
                    "in_progress",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(payload["data"]["task"]["status"], "in_progress")
                self.assertEqual(
                    payload["data"]["event"]["event_type"],
                    "contract_recorded",
                )
                self.assertEqual(
                    payload["data"]["contract_write"],
                    {"recorded": True, "revision": 1},
                )

            _, added = add_task(db, repo, "Companion rejection")
            task_id = added["data"]["task"]["task_id"]
            result, payload = edit_contract(
                db,
                repo,
                task_id,
                "Scope",
                "Acceptance",
                "--status",
                "in_progress",
                "--add-note",
                "Do not combine",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                payload["errors"][0]["code"],
                "contract_activation_forbidden",
            )
            task, revisions, _ = durable_contract_state(db, task_id)
            self.assertEqual(task["status"], "ready")
            self.assertEqual(task["current_contract_revision"], 0)
            self.assertEqual(revisions, [])

    def test_revision_zero_activation_reuses_sequential_predecessor_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            add_task(
                db,
                repo,
                "Earlier",
                "--kind",
                "sequential",
                "--lane",
                "CONTRACT",
                "--order",
                "10",
            )
            _, later = add_task(
                db,
                repo,
                "Later",
                "--kind",
                "sequential",
                "--lane",
                "CONTRACT",
                "--order",
                "20",
            )
            task_id = later["data"]["task"]["task_id"]

            result, payload = edit_contract(
                db,
                repo,
                task_id,
                "Later scope",
                "Later acceptance",
                "--status",
                "in_progress",
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                payload["errors"][0]["code"],
                "sequential_predecessor_incomplete",
            )
            task, revisions, events = durable_contract_state(db, task_id)
            self.assertEqual(task["status"], "ready")
            self.assertEqual(task["current_contract_revision"], 0)
            self.assertEqual(revisions, [])
            self.assertEqual([event["event_type"] for event in events], ["task_added"])

    def test_later_revision_is_immutable_and_canonical_replay_is_write_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            _, added = add_task(
                db,
                repo,
                "Revision task",
                "--status",
                "in_progress",
                "--contract-scope",
                "Initial scope",
                "--contract-acceptance",
                "Initial acceptance",
                "--contract-constraints",
                "No network",
                "--contract-authority-ref",
                "roadmap:TG-M12.2",
            )
            task_id = added["data"]["task"]["task_id"]
            before = durable_contract_state(db, task_id)

            replay_result, replay = edit_contract(
                db,
                repo,
                task_id,
                "  Initial scope\r\n",
                "Initial acceptance",
            )

            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertEqual(
                replay["data"]["contract_write"],
                {"recorded": False, "revision": 1},
            )
            self.assertIsNone(replay["data"]["event"])
            self.assertEqual(replay["data"]["changed_fields"], [])
            self.assertEqual(durable_contract_state(db, task_id), before)

            missing_authority, authority_payload = edit_contract(
                db,
                repo,
                task_id,
                "Narrowed scope",
                "Initial acceptance",
            )
            self.assertEqual(missing_authority.returncode, 1)
            self.assertEqual(
                authority_payload["errors"][0]["code"],
                "contract_authority_required",
            )
            stale_authority, stale_authority_payload = edit_contract(
                db,
                repo,
                task_id,
                "Semantically changed scope",
                "Initial acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:99",
                "--contract-change-reason",
                "Must not accept unrelated revision authority",
            )
            self.assertEqual(stale_authority.returncode, 1)
            self.assertEqual(
                stale_authority_payload["errors"][0]["code"],
                "contract_authority_required",
            )
            missing_reason, reason_payload = edit_contract(
                db,
                repo,
                task_id,
                "Narrowed scope",
                "Initial acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
            )
            self.assertEqual(missing_reason.returncode, 1)
            self.assertEqual(reason_payload["errors"][0]["code"], "invalid_argument")

            revised_result, revised = edit_contract(
                db,
                repo,
                task_id,
                "Narrowed scope",
                "Initial acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
                "--contract-change-reason",
                "User narrowed the implementation boundary",
            )
            self.assertEqual(revised_result.returncode, 0, revised_result.stderr)
            self.assertEqual(
                revised["data"]["contract_write"],
                {"recorded": True, "revision": 2},
            )
            self.assertEqual(
                revised["data"]["event"]["event_type"],
                "contract_revised",
            )
            _, revisions, _ = durable_contract_state(db, task_id)
            self.assertEqual([row["revision"] for row in revisions], [1, 2])
            self.assertEqual(revisions[0]["constraints_text"], "No network")
            self.assertEqual(revisions[1]["constraints_text"], "No network")

            retried_result, retried = edit_contract(
                db,
                repo,
                task_id,
                "Narrowed scope",
                "Initial acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
                "--contract-change-reason",
                "User narrowed the implementation boundary",
            )
            self.assertEqual(retried_result.returncode, 0, retried_result.stderr)
            self.assertEqual(
                retried["data"]["contract_write"],
                {"recorded": False, "revision": 2},
            )

    def test_post_review_revision_atomically_invalidates_current_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            _, added = add_task(
                db,
                repo,
                "Reviewed contract",
                "--status",
                "in_progress",
                "--review-tier",
                "2",
                "--contract-scope",
                "Initial scope",
                "--contract-acceptance",
                "Initial acceptance",
            )
            task_id = added["data"]["task"]["task_id"]
            target = "sha256:" + ("a" * 64)
            target_result, target_payload = json_command(
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
                target,
            )
            self.assertEqual(target_result.returncode, 0, target_result.stderr)
            receipt_result, receipt_payload = json_command(
                "review",
                "receipt",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--reviewer",
                "contract-reviewer",
                "--kind",
                "independent",
                "--verdict",
                "pass",
                "--summary",
                "Contract review passed",
            )
            self.assertEqual(receipt_result.returncode, 0, receipt_result.stderr)
            completion_result, _ = json_command(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--status",
                "review_pending",
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "external-contract-revision",
                "--completion-evidence-reason",
                "Durable external material",
                "--external-revision-approved",
            )
            self.assertEqual(completion_result.returncode, 0, completion_result.stderr)

            revised_result, revised = edit_contract(
                db,
                repo,
                task_id,
                "Revised scope",
                "Revised acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
                "--contract-change-reason",
                "User changed the accepted boundary",
            )

            self.assertEqual(revised_result.returncode, 0, revised_result.stderr)
            self.assertEqual(revised["data"]["task"]["status"], "in_progress")
            task, revisions, _ = durable_contract_state(db, task_id)
            self.assertEqual(task["current_contract_revision"], 2)
            self.assertEqual(task["completion_evidence_kind"], "none")
            self.assertEqual(task["completion_evidence_revision"], "")
            self.assertEqual(task["completion_evidence_reason"], "")
            self.assertEqual(task["external_revision_approved"], 0)
            self.assertEqual(task["completion_commit_required"], 1)
            self.assertEqual(task["completion_commit_hash"], "")
            self.assertEqual(task["review_target_kind"], "")
            self.assertEqual(task["review_target_value"], "")
            self.assertEqual(task["review_target_base_revision"], "")
            self.assertEqual(task["review_target_generation"], 2)
            self.assertNotIn(
                "review_target_base_revision",
                revised["data"]["changed_fields"],
            )
            self.assertEqual(len(revisions), 2)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipts WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )

    def test_done_and_cancelled_contract_writes_keep_lifecycle_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            task_ids = {}
            for status in ("done", "cancelled"):
                _, added = add_task(
                    db,
                    repo,
                    f"{status} contract",
                    "--contract-scope",
                    "Initial scope",
                    "--contract-acceptance",
                    "Initial acceptance",
                )
                task_id = added["data"]["task"]["task_id"]
                task_ids[status] = task_id
                with closing(sqlite3.connect(db)) as connection:
                    if status == "done":
                        connection.execute(
                            """
                            UPDATE tasks
                               SET status = 'done',
                                   completed_at = updated_at,
                                   completion_commit_required = 0,
                                   completion_commit_hash = '',
                                   completion_evidence_kind = 'commit_not_required',
                                   completion_evidence_revision = '',
                                   completion_evidence_reason = '',
                                   external_revision_approved = 0
                             WHERE task_id = ?
                            """,
                            (task_id,),
                        )
                    else:
                        connection.execute(
                            "UPDATE tasks SET status = ? WHERE task_id = ?",
                            (status, task_id),
                        )
                    connection.commit()

            done_result, done_payload = edit_contract(
                db,
                repo,
                task_ids["done"],
                "Changed scope",
                "Changed acceptance",
                "--contract-authority-ref",
                "roadmap:done",
                "--contract-change-reason",
                "Should require reopen",
            )
            self.assertEqual(done_result.returncode, 1)
            self.assertEqual(
                done_payload["errors"][0]["code"],
                "done_task_requires_reopen",
            )
            cancelled_result, cancelled_payload = edit_contract(
                db,
                repo,
                task_ids["cancelled"],
                "Changed scope",
                "Changed acceptance",
                "--contract-authority-ref",
                "roadmap:cancelled",
                "--contract-change-reason",
                "Should remain cancelled",
            )
            self.assertEqual(cancelled_result.returncode, 1)
            self.assertEqual(
                cancelled_payload["errors"][0]["code"],
                "contract_activation_forbidden",
            )

    def test_handoff_identity_captures_current_contract_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            _, added = add_task(
                db,
                repo,
                "Handoff source",
                "--status",
                "in_progress",
                "--contract-scope",
                "Initial scope",
                "--contract-acceptance",
                "Initial acceptance",
            )
            task_id = added["data"]["task"]["task_id"]
            first_result, first = json_command(
                "handoff",
                "record",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--summary",
                "Out-of-scope hardening",
            )
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(
                first["data"]["handoff"]["source_contract_revision"],
                1,
            )
            revised_result, _ = edit_contract(
                db,
                repo,
                task_id,
                "Revised scope",
                "Initial acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
                "--contract-change-reason",
                "User revised current scope",
            )
            self.assertEqual(revised_result.returncode, 0, revised_result.stderr)
            second_result, second = json_command(
                "handoff",
                "record",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--summary",
                "Out-of-scope hardening",
            )
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertEqual(
                second["data"]["handoff"]["source_contract_revision"],
                2,
            )
            self.assertNotEqual(
                first["data"]["handoff"]["handoff_id"],
                second["data"]["handoff"]["handoff_id"],
            )

    def test_handoff_contract_pointer_failure_keeps_structured_error_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            _, added = add_task(
                db,
                repo,
                "Corrupt pointer source",
                "--status",
                "in_progress",
                "--contract-scope",
                "Initial scope",
                "--contract-acceptance",
                "Initial acceptance",
            )
            task_id = added["data"]["task"]["task_id"]
            with closing(sqlite3.connect(db)) as connection:
                before_events = connection.execute(
                    "SELECT COUNT(*) FROM task_events"
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE tasks
                       SET current_contract_revision = 99
                     WHERE task_id = ?
                    """,
                    (task_id,),
                )
                connection.commit()

            result = run_taskgov(
                "handoff",
                "record",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--summary",
                "Must not be stored",
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["errors"][0]["code"], "internal_error")
            self.assertEqual(
                payload["data"],
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
            self.assertNotIn("Traceback", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM handoff_records"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events"
                    ).fetchone()[0],
                    before_events,
                )

    def test_same_content_concurrent_writers_record_once_then_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            _, added = add_task(
                db,
                repo,
                "Concurrent contract",
                "--status",
                "in_progress",
                "--contract-scope",
                "Initial scope",
                "--contract-acceptance",
                "Initial acceptance",
            )
            task_id = added["data"]["task"]["task_id"]
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )

            def revise():
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        return edit_task(
                            connection,
                            target.project,
                            task_id,
                            contract_scope="Concurrent scope",
                            contract_acceptance="Initial acceptance",
                            contract_authority_ref="roadmap:concurrent",
                            contract_change_reason="One deterministic revision",
                        ).contract_write

            with ThreadPoolExecutor(max_workers=2) as pool:
                receipts = [future.result() for future in (pool.submit(revise), pool.submit(revise))]

            self.assertEqual(
                sorted(receipt["recorded"] for receipt in receipts),
                [False, True],
            )
            self.assertEqual(
                {receipt["revision"] for receipt in receipts},
                {2},
            )
            _, revisions, _ = durable_contract_state(db, task_id)
            self.assertEqual([row["revision"] for row in revisions], [1, 2])

    def test_different_concurrent_user_instructions_serialize_as_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            _, added = add_task(
                db,
                repo,
                "Concurrent user instructions",
                "--status",
                "in_progress",
                "--contract-scope",
                "Initial scope",
                "--contract-acceptance",
                "Initial acceptance",
            )
            task_id = added["data"]["task"]["task_id"]
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            )

            def revise(scope):
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        return edit_task(
                            connection,
                            target.project,
                            task_id,
                            contract_scope=scope,
                            contract_acceptance="Initial acceptance",
                            contract_authority_ref=f"user_instruction:{task_id}:2",
                            contract_change_reason="Concurrent explicit instruction",
                        ).contract_write

            with ThreadPoolExecutor(max_workers=2) as pool:
                receipts = [
                    future.result()
                    for future in (
                        pool.submit(revise, "Concurrent scope A"),
                        pool.submit(revise, "Concurrent scope B"),
                    )
                ]

            self.assertEqual(
                sorted(receipt["revision"] for receipt in receipts),
                [2, 3],
            )
            self.assertTrue(all(receipt["recorded"] for receipt in receipts))
            _, revisions, events = durable_contract_state(db, task_id)
            self.assertEqual([row["revision"] for row in revisions], [1, 2, 3])
            self.assertEqual(
                {row["scope"] for row in revisions[1:]},
                {"Concurrent scope A", "Concurrent scope B"},
            )
            self.assertEqual(
                [row["authority_ref"] for row in revisions[1:]],
                [
                    f"user_instruction:{task_id}:2",
                    f"user_instruction:{task_id}:3",
                ],
            )
            self.assertEqual(
                sum(event["event_type"] == "contract_revised" for event in events),
                2,
            )
            before_retry = durable_contract_state(db, task_id)
            retry_result, retry = edit_contract(
                db,
                repo,
                task_id,
                revisions[-1]["scope"],
                "Initial acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
                "--contract-change-reason",
                "Concurrent explicit instruction",
            )
            self.assertEqual(retry_result.returncode, 0, retry_result.stderr)
            self.assertEqual(
                retry["data"]["contract_write"],
                {"recorded": False, "revision": 3},
            )
            self.assertEqual(retry["data"]["changed_fields"], [])
            self.assertIsNone(retry["data"]["event"])
            self.assertEqual(durable_contract_state(db, task_id), before_retry)


class TaskContractMigrationTests(unittest.TestCase):
    def test_v7_to_v8_preserves_rows_and_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            init_db(db, repo)
            _, added = add_task(db, repo, "Legacy revision-zero task")
            task_id = added["data"]["task"]["task_id"]
            handoff_result, handoff = json_command(
                "handoff",
                "record",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--summary",
                "Legacy pending handoff",
            )
            self.assertEqual(handoff_result.returncode, 0, handoff_result.stderr)
            handoff_id = handoff["data"]["handoff"]["handoff_id"]
            with closing(connect(db)) as connection:
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
                connection.commit()

                before_tasks = connection.execute(
                    "SELECT COUNT(*) FROM tasks"
                ).fetchone()[0]
                before_events = connection.execute(
                    "SELECT COUNT(*) FROM task_events"
                ).fetchone()[0]
                before_handoff = tuple(
                    connection.execute(
                        """
                        SELECT handoff_id, source_contract_revision, state
                          FROM handoff_records
                         WHERE handoff_id = ?
                        """,
                        (handoff_id,),
                    ).fetchone()
                )

                with self.assertRaises(StorageError):
                    apply_task_contract_migration(
                        connection,
                        fail_stage="after_schema",
                    )
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(tasks)")
                }
                self.assertNotIn("current_contract_revision", columns)
                self.assertFalse(
                    connection.execute(
                        """
                        SELECT 1 FROM sqlite_master
                         WHERE type = 'table'
                           AND name = 'task_contract_revisions'
                        """
                    ).fetchone()
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    7,
                )

                apply_task_contract_migration(connection)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    before_tasks,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events"
                    ).fetchone()[0],
                    before_events,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            """
                            SELECT handoff_id, source_contract_revision, state
                              FROM handoff_records
                             WHERE handoff_id = ?
                            """,
                            (handoff_id,),
                        ).fetchone()
                    ),
                    before_handoff,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT current_contract_revision
                          FROM tasks
                         WHERE task_id = ?
                        """,
                        (task_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
