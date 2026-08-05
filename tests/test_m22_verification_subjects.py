from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import (
    DEFAULT_VERIFICATION,
    FINGERPRINT_A,
    FINGERPRINT_B,
    add_receipt,
    add_task,
    completion,
    initialize,
    initialize_v16_fixture,
    payload,
    run_taskgov,
    seed_current_review_evidence,
    set_target,
    show_task,
    table_count,
    target_for,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import storage as storage_service
from task_governance_tool import verification_receipts as verification_receipt_service
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.storage import (
    DATABASE_BUSY_MESSAGE,
    StorageError,
    apply_verification_receipts_migration,
    connect,
    connect_initialized,
    connect_snapshot_readonly,
    current_schema_version,
    verification_expectation_digest,
)
from task_governance_tool.tasks import read_internal_task
from task_governance_tool.verification_receipts import (
    PUBLIC_VERIFICATION_RECEIPT_FIELDS,
    VerificationReceiptError,
    add_verification_receipt,
    current_verification_gate,
)
from task_governance_tool.viewer import build_viewer_snapshot


class M22VerificationSubjectTests(unittest.TestCase):
    def test_service_records_one_public_receipt_without_mutating_task_or_events(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            generation = set_target(db, repo, task["task_id"])
            target = target_for(db, repo)
            with closing(sqlite3.connect(db)) as raw:
                before_task = raw.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                before_events = raw.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()[0]

            with closing(connect_initialized(target)) as connection:
                with connection:
                    result = add_verification_receipt(
                        connection,
                        target.project,
                        task["task_id"],
                        result="pass",
                        duration_ms=0,
                        scope_coverage="full",
                        expected_target_generation=generation,
                        database_target=target,
                    )
                stored_task = read_internal_task(
                    connection,
                    target.project.project_id,
                    task["task_id"],
                )
                gate = current_verification_gate(
                    connection,
                    task=stored_task,
                )

            receipt = result.receipt
            self.assertEqual(tuple(receipt), PUBLIC_VERIFICATION_RECEIPT_FIELDS)
            self.assertEqual(receipt["result"], "pass")
            self.assertEqual(receipt["duration_ms"], 0)
            self.assertEqual(receipt["scope_coverage"], "full")
            self.assertEqual(
                receipt["verification_subject"],
                {
                    "basis_version": 1,
                    "kind": "task_verification_criterion",
                    "authority_snapshot_id": stored_task[
                        "current_authority_snapshot_id"
                    ],
                    "verification_criterion_id": stored_task[
                        "review_target_verification_criterion_id"
                    ],
                    "legacy_caller_label": None,
                },
            )
            self.assertEqual(
                receipt["source_revision"],
                {
                    "kind": "diff_fingerprint",
                    "value": FINGERPRINT_A,
                    "base_revision": None,
                    "generation": generation,
                },
            )
            self.assertTrue(gate.satisfied)
            self.assertEqual(
                gate.qualifying_receipt_id,
                receipt["verification_receipt_id"],
            )
            with closing(sqlite3.connect(db)) as raw:
                self.assertEqual(
                    raw.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone(),
                    before_task,
                )
                self.assertEqual(
                    raw.execute(
                        "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    before_events,
                )

    def test_expected_generation_and_duplicate_fail_closed_then_fresh_target_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            first_generation = set_target(db, repo, task_id)

            stale = add_receipt(
                db,
                repo,
                task_id,
                first_generation + 1,
            )
            self.assertEqual(stale.returncode, 1, stale.stdout)
            self.assertEqual(
                payload(stale)["errors"][0],
                {
                    "code": "verification_basis_stale",
                    "message": "verification target changed after the reported run",
                },
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)

            first = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(first.returncode, 0, first.stdout)
            duplicate = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(duplicate.returncode, 1, duplicate.stdout)
            self.assertEqual(
                payload(duplicate)["errors"][0]["code"],
                "verification_receipt_already_recorded",
            )

            second_generation = set_target(
                db,
                repo,
                task_id,
                fingerprint=FINGERPRINT_B,
            )
            old_basis = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(old_basis.returncode, 1, old_basis.stdout)
            self.assertEqual(
                payload(old_basis)["errors"][0]["code"],
                "verification_basis_stale",
            )
            second = add_receipt(db, repo, task_id, second_generation)
            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertEqual(table_count(db, "verification_receipts"), 2)

    def test_cli_read_only_privacy_text_and_backup_only_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as maintenance:
                read_only = add_receipt(
                    db,
                    repo,
                    "invalid-task-id",
                    0,
                    result="invalid",
                    duration_ms=-1,
                    scope_coverage="invalid",
                    read_only=True,
                    maintenance_enabled=True,
                )
                self.assertEqual(read_only.returncode, 1, read_only.stdout)
                self.assertEqual(
                    payload(read_only)["errors"][0],
                    {
                        "code": "invalid_argument",
                        "message": (
                            "verification receipt add cannot run with --read-only "
                            "because it writes the database"
                        ),
                    },
                )
                maintenance.assert_not_called()

                recorded = add_receipt(
                    db,
                    repo,
                    task_id,
                    generation,
                    result="timeout",
                    scope_coverage="partial",
                    json_output=False,
                    maintenance_enabled=True,
                )
                self.assertEqual(recorded.returncode, 0, recorded.stderr)
                with closing(sqlite3.connect(db)) as connection:
                    receipt_id = connection.execute(
                        "SELECT verification_receipt_id FROM verification_receipts"
                    ).fetchone()[0]
                self.assertEqual(
                    recorded.stdout,
                    (
                        f"Verification receipt recorded: {receipt_id}\n"
                        "Result: timeout  Coverage: partial\n"
                        f"Source: diff_fingerprint/generation {generation}\n"
                    ),
                )
                maintenance.assert_called_once()
                outcome = maintenance.call_args.args[1]
                self.assertEqual(
                    outcome,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=False,
                    ),
                )

    def test_task_show_adds_exact_json_projection_and_keeps_text_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            before_text = show_task(
                db,
                repo,
                task_id,
                json_output=False,
            ).stdout

            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            recorded_payload = payload(recorded)
            self.assertEqual(set(recorded_payload["data"]), {"receipt"})
            receipt = recorded_payload["data"]["receipt"]
            after_text = show_task(
                db,
                repo,
                task_id,
                json_output=False,
            ).stdout
            self.assertEqual(after_text, before_text)

            shown = payload(
                show_task(
                    db,
                    repo,
                    task_id,
                    json_output=True,
                )
            )
            evidence = shown["data"]["verification_evidence"]
            self.assertEqual(
                set(evidence),
                {
                    "expectation",
                    "contract_revision",
                    "source_revision",
                    "current_verification_subject",
                    "gate",
                    "counts",
                    "recent_receipts",
                },
            )
            self.assertEqual(evidence["expectation"], DEFAULT_VERIFICATION)
            self.assertEqual(
                set(evidence["source_revision"]),
                {"kind", "value", "base_revision", "generation"},
            )
            self.assertEqual(
                evidence["current_verification_subject"],
                receipt["verification_subject"],
            )
            self.assertEqual(
                set(evidence["gate"]),
                {
                    "required",
                    "satisfied",
                    "blocking_code",
                    "qualifying_receipt_id",
                },
            )
            self.assertEqual(
                set(evidence["counts"]),
                {
                    "receipts_total",
                    "receipts_exact_current",
                    "qualifying_exact_current",
                    "blocking_exact_current",
                },
            )
            self.assertEqual(evidence["recent_receipts"], [receipt])
            self.assertNotIn("verification_expectation_digest", receipt)
            self.assertNotIn("verification_basis_version", receipt)

            missing = show_task(
                db,
                repo,
                "tg_task_ffffffffffffffff",
                json_output=True,
            )
            self.assertEqual(missing.returncode, 1, missing.stdout)
            self.assertIsNone(
                payload(missing)["data"]["verification_evidence"]
            )

    def test_verification_result_tamper_fails_show_check_and_complete_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = seed_current_review_evidence(db, repo, task_id)
            recorded = add_receipt(
                db,
                repo,
                task_id,
                generation,
                result="fail",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            with closing(sqlite3.connect(db)) as connection:
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_verification_receipts_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_verification_receipts_no_update"
                )
                connection.execute(
                    "UPDATE verification_receipts SET result = 'pass'"
                )
                connection.execute(trigger_sql)
                connection.commit()

            results = (
                show_task(db, repo, task_id, json_output=True),
                completion(db, repo, task_id, check=True),
                completion(db, repo, task_id),
            )
            for result in results:
                with self.subTest(args=result.args):
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(
                        payload(result)["errors"][0]["code"],
                        "invalid_verification_evidence",
                    )

    def test_current_verification_target_tamper_cannot_hide_from_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            first_target = FINGERPRINT_A
            set_target(
                db,
                repo,
                task_id,
                fingerprint=first_target,
            )
            for index in range(11):
                fingerprint = FINGERPRINT_A if index % 2 == 0 else FINGERPRINT_B
                generation = set_target(
                    db,
                    repo,
                    task_id,
                    fingerprint=fingerprint,
                )
                recorded = add_receipt(db, repo, task_id, generation)
                self.assertEqual(recorded.returncode, 0, recorded.stdout)
            current_generation = set_target(
                db,
                repo,
                task_id,
                fingerprint=FINGERPRINT_B,
            )
            current = add_receipt(
                db,
                repo,
                task_id,
                current_generation,
            )
            self.assertEqual(current.returncode, 0, current.stdout)
            current_id = payload(current)["data"]["receipt"][
                "verification_receipt_id"
            ]
            with closing(sqlite3.connect(db)) as connection:
                trigger_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'trigger'
                       AND name = 'trg_verification_receipts_no_update'
                    """
                ).fetchone()[0]
                connection.execute(
                    "DROP TRIGGER trg_verification_receipts_no_update"
                )
                connection.execute(
                    """
                    UPDATE verification_receipts
                       SET target_value = ?, target_generation = 1,
                           created_at = '2000-01-01T00:00:00Z'
                     WHERE verification_receipt_id = ?
                    """,
                    (first_target, current_id),
                )
                connection.execute(trigger_sql)
                connection.commit()

            before = db.read_bytes()
            results = (
                show_task(db, repo, task_id, json_output=True),
                completion(db, repo, task_id, check=True),
                completion(db, repo, task_id),
            )
            for result in results:
                with self.subTest(args=result.args):
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(
                        payload(result)["errors"][0]["code"],
                        "invalid_verification_evidence",
                    )
                    self.assertEqual(db.read_bytes(), before)

    def test_v16_migration_preserves_rows_without_synthesizing_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            target, task_id = initialize_v16_fixture(Path(temp))
            with closing(connect(target.db_path)) as connection:
                task_columns = tuple(
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(tasks)"
                    ).fetchall()
                )
                cycle_columns = tuple(
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(task_completion_cycles)"
                    ).fetchall()
                )
                task_before = tuple(
                    connection.execute(
                        "SELECT * FROM tasks ORDER BY task_id"
                    ).fetchall()
                )
                cycle_before = tuple(
                    connection.execute(
                        "SELECT * FROM task_completion_cycles ORDER BY rowid"
                    ).fetchall()
                )
                event_before = tuple(
                    connection.execute(
                        "SELECT * FROM task_events ORDER BY rowid"
                    ).fetchall()
                )

                apply_verification_receipts_migration(connection)

                self.assertEqual(current_schema_version(connection), 17)
                self.assertEqual(
                    connection.execute(
                        "SELECT name FROM schema_migrations WHERE version = 17"
                    ).fetchone()[0],
                    "verification_receipts",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_receipts"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT {', '.join(task_columns)} "
                            "FROM tasks ORDER BY task_id"
                        ).fetchall()
                    ),
                    task_before,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT {', '.join(cycle_columns)} "
                            "FROM task_completion_cycles ORDER BY rowid"
                        ).fetchall()
                    ),
                    cycle_before,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            "SELECT * FROM task_events ORDER BY rowid"
                        ).fetchall()
                    ),
                    event_before,
                )
                migrated_cycle = connection.execute(
                    """
                    SELECT verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                self.assertEqual(tuple(migrated_cycle), (0, None, None))

                changes_before_reentry = connection.total_changes
                apply_verification_receipts_migration(connection)
                self.assertEqual(connection.total_changes, changes_before_reentry)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version = 17"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_receipts"
                    ).fetchone()[0],
                    0,
                )

    def test_v17_migration_rolls_back_every_injected_stage_and_reenters(self):
        failure_stages = (
            "after_receipt_schema",
            "after_cycle_columns",
            "after_triggers",
            "after_marker",
            "before_commit",
        )
        with tempfile.TemporaryDirectory() as temp:
            for stage in failure_stages:
                with self.subTest(stage=stage):
                    target, task_id = initialize_v16_fixture(
                        Path(temp) / stage
                    )
                    with closing(connect(target.db_path)) as connection:
                        cycle_before = tuple(
                            connection.execute(
                                "SELECT * FROM task_completion_cycles ORDER BY rowid"
                            ).fetchall()
                        )
                        with self.assertRaises(StorageError) as failure:
                            apply_verification_receipts_migration(
                                connection,
                                fail_stage=stage,
                            )
                        self.assertEqual(failure.exception.code, "internal_error")
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(current_schema_version(connection), 16)
                        self.assertIsNone(
                            connection.execute(
                                """
                                SELECT name FROM sqlite_master
                                 WHERE type = 'table'
                                   AND name = 'verification_receipts'
                                """
                            ).fetchone()
                        )
                        cycle_columns = {
                            str(row["name"])
                            for row in connection.execute(
                                "PRAGMA table_info(task_completion_cycles)"
                            ).fetchall()
                        }
                        self.assertNotIn(
                            "verification_basis_version",
                            cycle_columns,
                        )
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    "SELECT * FROM task_completion_cycles ORDER BY rowid"
                                ).fetchall()
                            ),
                            cycle_before,
                        )

                        apply_verification_receipts_migration(connection)
                        self.assertEqual(current_schema_version(connection), 17)
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    """
                                    SELECT verification_basis_version,
                                           verification_expectation_digest,
                                           verification_receipt_id
                                      FROM task_completion_cycles
                                     WHERE task_id = ?
                                    """,
                                    (task_id,),
                                ).fetchone()
                            ),
                            (0, None, None),
                        )
                        apply_verification_receipts_migration(connection)
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM schema_migrations WHERE version = 17"
                            ).fetchone()[0],
                            1,
                        )

    def test_v17_receipt_migrates_to_legacy_subject_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            target, _done_task_id = initialize_v16_fixture(Path(temp))
            db = target.db_path
            repo = target.project.canonical_repo
            with closing(connect(db)) as connection:
                apply_verification_receipts_migration(connection)
                ready = connection.execute(
                    "SELECT project_id, task_id FROM tasks WHERE status = 'ready'"
                ).fetchone()
                task_id = str(ready["task_id"])
                connection.execute(
                    """
                    UPDATE tasks
                       SET status = 'in_progress', verification = ?,
                           review_target_kind = 'diff_fingerprint',
                           review_target_value = ?,
                           review_target_base_revision = '',
                           review_target_generation = 1
                     WHERE task_id = ?
                    """,
                    (DEFAULT_VERIFICATION, FINGERPRINT_A, task_id),
                )
                connection.execute(
                    """
                    INSERT INTO verification_receipts(
                      verification_receipt_id, project_id, task_id,
                      contract_revision, verification_expectation_digest,
                      command_label, result, duration_ms, scope_coverage,
                      target_kind, target_value, target_base_revision,
                      target_generation, created_at
                    ) VALUES (
                      'tg_verification_receipt_abcdef0123456789', ?, ?, 0, ?,
                      'legacy focused unittest', 'pass', 7, 'full',
                      'diff_fingerprint', ?, '', 1,
                      '2026-08-01T15:03:00Z'
                    )
                    """,
                    (
                        ready["project_id"],
                        task_id,
                        verification_expectation_digest(DEFAULT_VERIFICATION),
                        FINGERPRINT_A,
                    ),
                )
                connection.commit()
                storage_service.apply_evidence_ledger_capture_migration(connection)
                storage_service.apply_completion_evidence_bundle_migration(
                    connection
                )

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 0, shown.stdout)
            evidence = payload(shown)["data"]["verification_evidence"]
            self.assertEqual(
                evidence["gate"]["blocking_code"],
                "evidence_basis_stale",
            )
            self.assertEqual(
                evidence["recent_receipts"][0]["verification_subject"],
                {
                    "basis_version": 0,
                    "kind": "legacy_caller_label",
                    "authority_snapshot_id": None,
                    "verification_criterion_id": None,
                    "legacy_caller_label": "legacy focused unittest",
                },
            )

    def test_writer_lock_reread_rejects_concurrent_target_drift_without_row(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            target = target_for(db, repo)
            actual_begin = storage_service.begin_initialized_write

            def drift_then_lock(connection, database_target):
                set_target(
                    db,
                    repo,
                    task_id,
                    fingerprint=FINGERPRINT_B,
                )
                return actual_begin(connection, database_target)

            with closing(connect_initialized(target)) as connection:
                with mock.patch(
                    "task_governance_tool.verification_receipts.begin_initialized_write",
                    side_effect=drift_then_lock,
                ):
                    with self.assertRaises(VerificationReceiptError) as stale:
                        with connection:
                            add_verification_receipt(
                                connection,
                                target.project,
                                task_id,
                                result="pass",
                                duration_ms=10,
                                scope_coverage="full",
                                expected_target_generation=generation,
                                database_target=target,
                            )
            self.assertEqual(stale.exception.code, "verification_basis_stale")
            self.assertEqual(table_count(db, "verification_receipts"), 0)
            with closing(sqlite3.connect(db)) as connection:
                current_target = connection.execute(
                    """
                    SELECT review_target_value, review_target_generation
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(current_target, (FINGERPRINT_B, generation + 1))

    def test_writer_lock_receipt_snapshot_preserves_database_busy_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            first_generation = set_target(db, repo, task_id)
            recorded = add_receipt(db, repo, task_id, first_generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            current_generation = set_target(
                db,
                repo,
                task_id,
                fingerprint=FINGERPRINT_B,
            )
            target = target_for(db, repo)
            before_bytes = db.read_bytes()
            real_validate_basis = verification_receipt_service._validate_add_basis

            def sqlite_lock_error(error_code: int) -> sqlite3.OperationalError:
                error = sqlite3.OperationalError(
                    "Authorization: Bearer sensitive verification lock detail"
                )
                error.sqlite_errorcode = error_code
                return error

            cases = (
                ("sqlite_busy", sqlite_lock_error(sqlite3.SQLITE_BUSY)),
                ("sqlite_locked", sqlite_lock_error(sqlite3.SQLITE_LOCKED)),
                (
                    "mapped_database_busy",
                    StorageError("database_busy", DATABASE_BUSY_MESSAGE),
                ),
            )
            for label, injected_error in cases:
                with self.subTest(label=label):
                    with closing(connect_initialized(target)) as connection:
                        changes_before = connection.total_changes
                        counts_before = tuple(
                            connection.execute(
                                f"SELECT COUNT(*) FROM {table_name}"
                            ).fetchone()[0]
                            for table_name in (
                                "verification_receipts",
                                "evidence_references",
                                "task_events",
                            )
                        )
                        validation_calls = 0

                        def validate_basis(*args, **kwargs):
                            nonlocal validation_calls
                            validation_calls += 1
                            if validation_calls == 1:
                                return real_validate_basis(*args, **kwargs)
                            with mock.patch.object(
                                storage_service,
                                "validate_selected_task_receipt_evidence",
                                side_effect=injected_error,
                            ):
                                return real_validate_basis(*args, **kwargs)

                        with (
                            mock.patch.object(
                                verification_receipt_service,
                                "_validate_add_basis",
                                side_effect=validate_basis,
                            ),
                            self.assertRaises(StorageError) as failure,
                        ):
                            with connection:
                                add_verification_receipt(
                                    connection,
                                    target.project,
                                    task_id,
                                    result="pass",
                                    duration_ms=10,
                                    scope_coverage="full",
                                    expected_target_generation=current_generation,
                                    database_target=target,
                                )

                        self.assertEqual(validation_calls, 2)
                        self.assertEqual(failure.exception.code, "database_busy")
                        self.assertEqual(
                            failure.exception.message,
                            DATABASE_BUSY_MESSAGE,
                        )
                        self.assertNotIn("Authorization", str(failure.exception))
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(connection.total_changes, changes_before)
                        self.assertEqual(
                            tuple(
                                connection.execute(
                                    f"SELECT COUNT(*) FROM {table_name}"
                                ).fetchone()[0]
                                for table_name in (
                                    "verification_receipts",
                                    "evidence_references",
                                    "task_events",
                                )
                            ),
                            counts_before,
                        )
                    self.assertEqual(db.read_bytes(), before_bytes)

    def test_receipt_rows_are_immutable_and_unique_per_target_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            receipt_id = payload(recorded)["data"]["receipt"][
                "verification_receipt_id"
            ]

            for statement, expected_message in (
                (
                    "UPDATE verification_receipts "
                    "SET command_label = 'changed' "
                    "WHERE verification_receipt_id = ?",
                    "immutable_verification_receipt",
                ),
                (
                    "DELETE FROM verification_receipts "
                    "WHERE verification_receipt_id = ?",
                    "immutable_verification_receipt",
                ),
            ):
                with self.subTest(statement=statement):
                    with closing(sqlite3.connect(db)) as connection:
                        with self.assertRaises(sqlite3.IntegrityError) as rejected:
                            connection.execute(statement, (receipt_id,))
                        connection.rollback()
                    self.assertIn(expected_message, str(rejected.exception))
                    self.assertEqual(table_count(db, "verification_receipts"), 1)

            duplicate_id = (
                "tg_verification_receipt_0000000000000000"
                if receipt_id != "tg_verification_receipt_0000000000000000"
                else "tg_verification_receipt_1111111111111111"
            )
            with closing(connect(db)) as connection:
                with self.assertRaises(sqlite3.IntegrityError) as duplicate:
                    connection.execute(
                        """
                        INSERT INTO verification_receipts(
                          verification_receipt_id, project_id, task_id,
                          contract_revision, verification_expectation_digest,
                          command_label, result, duration_ms, scope_coverage,
                          target_kind, target_value, target_base_revision,
                          target_generation, created_at,
                          verification_subject_basis_version,
                          subject_authority_snapshot_id,
                          subject_verification_criterion_id
                        )
                        SELECT ?, project_id, task_id, contract_revision,
                               verification_expectation_digest,
                               command_label, result, duration_ms,
                               scope_coverage, target_kind, target_value,
                               target_base_revision, target_generation, created_at,
                               verification_subject_basis_version,
                               subject_authority_snapshot_id,
                               subject_verification_criterion_id
                          FROM verification_receipts
                         WHERE verification_receipt_id = ?
                        """,
                        (duplicate_id, receipt_id),
                    )
                connection.rollback()
            self.assertIn("UNIQUE constraint failed", str(duplicate.exception))
            self.assertEqual(table_count(db, "verification_receipts"), 1)

    def test_raw_wrong_current_digest_without_subject_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)
            receipt_id = "tg_verification_receipt_deadbeefdeadbeef"
            wrong_digest = "f" * 64

            with closing(connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_id, current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                parameters = (
                    receipt_id,
                    basis["project_id"],
                    task_id,
                    basis["current_contract_revision"],
                    wrong_digest,
                    "Corrupt direct insert",
                    basis["review_target_kind"],
                    basis["review_target_value"],
                    basis["review_target_base_revision"],
                    generation,
                )
                statement = """
                    INSERT INTO verification_receipts(
                      verification_receipt_id, project_id, task_id,
                      contract_revision, verification_expectation_digest,
                      command_label, result, duration_ms, scope_coverage,
                      target_kind, target_value, target_base_revision,
                      target_generation, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pass', 1, 'full',
                              ?, ?, ?, ?, '2026-08-01T15:00:00Z')
                """
                with self.assertRaises(sqlite3.IntegrityError) as rejected:
                    connection.execute(statement, parameters)
                connection.rollback()
                self.assertIn(
                    "invalid_verification_subject_basis",
                    str(rejected.exception),
                )

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 0, shown.stdout)
            self.assertEqual(
                payload(shown)["data"]["verification_evidence"]["gate"][
                    "blocking_code"
                ],
                "verification_receipt_required",
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)

    def test_raw_command_line_label_is_rejected_by_subject_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            with closing(connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_id, verification,
                           current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                with self.assertRaises(sqlite3.IntegrityError) as rejected:
                    connection.execute(
                        """
                        INSERT INTO verification_receipts(
                          verification_receipt_id, project_id, task_id,
                          contract_revision, verification_expectation_digest,
                          command_label, result, duration_ms, scope_coverage,
                          target_kind, target_value, target_base_revision,
                          target_generation, created_at
                        ) VALUES (
                          'tg_verification_receipt_feedfacefeedface', ?, ?, ?, ?,
                          'python -m pytest tests -q', 'pass', 1, 'full',
                          ?, ?, ?, ?, '2026-08-01T15:01:00Z'
                        )
                        """,
                        (
                            basis["project_id"],
                            task_id,
                            basis["current_contract_revision"],
                            verification_expectation_digest(
                                basis["verification"]
                            ),
                            basis["review_target_kind"],
                            basis["review_target_value"],
                            basis["review_target_base_revision"],
                            generation,
                        ),
                    )
                connection.rollback()
                self.assertIn(
                    "invalid_verification_subject_basis",
                    str(rejected.exception),
                )

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 0, shown.stdout)
            self.assertEqual(
                payload(shown)["data"]["verification_evidence"]["gate"][
                    "blocking_code"
                ],
                "verification_receipt_required",
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)

    def test_raw_receipt_for_empty_current_expectation_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Empty expectation corruption",
                verification="",
            )
            task_id = task["task_id"]
            generation = set_target(db, repo, task_id)

            with closing(connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_id, verification,
                           current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                with self.assertRaises(sqlite3.IntegrityError) as rejected:
                    connection.execute(
                        """
                        INSERT INTO verification_receipts(
                          verification_receipt_id, project_id, task_id,
                          contract_revision, verification_expectation_digest,
                          command_label, result, duration_ms, scope_coverage,
                          target_kind, target_value, target_base_revision,
                          target_generation, created_at
                        ) VALUES (
                          'tg_verification_receipt_0123456789abcdef', ?, ?, ?, ?,
                          'Corrupt empty expectation', 'pass', 1, 'full',
                          ?, ?, ?, ?, '2026-08-01T15:02:00Z'
                        )
                        """,
                        (
                            basis["project_id"],
                            task_id,
                            basis["current_contract_revision"],
                            verification_expectation_digest(
                                basis["verification"]
                            ),
                            basis["review_target_kind"],
                            basis["review_target_value"],
                            basis["review_target_base_revision"],
                            generation,
                        ),
                    )
                connection.rollback()
                self.assertIn(
                    "invalid_verification_subject_basis",
                    str(rejected.exception),
                )

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 0, shown.stdout)
            self.assertTrue(
                payload(shown)["data"]["verification_evidence"]["gate"][
                    "satisfied"
                ]
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)

    def test_viewer_accepts_valid_v1_link_and_rejects_corrupt_receipt_link(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = seed_current_review_evidence(db, repo, task_id)
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            target = target_for(db, repo)

            with closing(connect_snapshot_readonly(db)) as connection:
                valid = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at="2026-08-01T00:00:00Z",
                ).snapshot
            self.assertEqual(valid["snapshot_version"], 4)
            self.assertEqual(valid["source_schema_version"], 19)
            projected = next(
                item for item in valid["tasks"] if item["task_id"] == task_id
            )
            self.assertEqual(
                projected["completion_history"]["cycles"][0]["origin"],
                "native_done",
            )
            serialized = json.dumps(valid)
            self.assertNotIn("verification_receipt_id", serialized)
            self.assertNotIn("verification_evidence", projected)

            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    trigger_sql = connection.execute(
                        """
                        SELECT sql FROM sqlite_master
                         WHERE type = 'trigger'
                           AND name = 'trg_verification_receipts_no_update'
                        """
                    ).fetchone()[0]
                    connection.execute(
                        "DROP TRIGGER trg_verification_receipts_no_update"
                    )
                    connection.execute(
                        "UPDATE verification_receipts SET result = 'fail'"
                    )
                    connection.execute(trigger_sql)

            with closing(connect_snapshot_readonly(db)) as connection:
                with self.assertRaises(StorageError) as corrupt:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(
                corrupt.exception.code,
                "project_state_unreadable",
            )

    def test_empty_verification_native_cycle_uses_v1_digest_and_null_link(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Empty native verification",
                verification="",
            )
            task_id = task["task_id"]
            seed_current_review_evidence(db, repo, task_id)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                cycle = connection.execute(
                    """
                    SELECT verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(
                cycle,
                (
                    1,
                    verification_expectation_digest(""),
                    None,
                ),
            )
            self.assertEqual(table_count(db, "verification_receipts"), 0)
            gate = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["verification_evidence"]["gate"]
            self.assertEqual(
                gate,
                {
                    "required": False,
                    "satisfied": True,
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                },
            )

    def test_whitespace_only_verification_binds_exact_digest_without_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            exact_verification = " \t "
            task = add_task(
                db,
                repo,
                title="Whitespace-only verification",
                verification=exact_verification,
            )
            task_id = task["task_id"]
            seed_current_review_evidence(db, repo, task_id)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                cycle = connection.execute(
                    """
                    SELECT verification_expectation,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(
                cycle,
                (
                    "unspecified",
                    verification_expectation_digest(exact_verification),
                    None,
                ),
            )
            evidence = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["verification_evidence"]
            self.assertEqual(evidence["expectation"], exact_verification)
            self.assertEqual(
                evidence["gate"],
                {
                    "required": False,
                    "satisfied": True,
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                },
            )

            target = target_for(db, repo)
            with closing(connect_snapshot_readonly(db)) as connection:
                build_viewer_snapshot(connection, target)
            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    connection.execute(
                        "UPDATE tasks SET verification = ? WHERE task_id = ?",
                        (" \n ", task_id),
                    )
            with closing(connect_snapshot_readonly(db)) as connection:
                with self.assertRaises(StorageError) as corrupt_viewer:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(
                corrupt_viewer.exception.code,
                "project_state_unreadable",
            )

    def test_empty_verification_cycle_digest_corruption_fails_task_show_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Corrupt empty verification basis",
                verification="",
            )
            task_id = task["task_id"]
            seed_current_review_evidence(db, repo, task_id)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    trigger_sql = connection.execute(
                        """
                        SELECT sql FROM sqlite_master
                         WHERE type = 'trigger'
                           AND name = 'trg_task_completion_cycles_no_update'
                        """
                    ).fetchone()[0]
                    connection.execute(
                        "DROP TRIGGER trg_task_completion_cycles_no_update"
                    )
                    connection.execute(
                        """
                        UPDATE task_completion_cycles
                           SET verification_expectation_digest = ?
                         WHERE task_id = ?
                        """,
                        ("f" * 64, task_id),
                    )
                    connection.execute(trigger_sql)

            shown = show_task(db, repo, task_id, json_output=True)
            self.assertEqual(shown.returncode, 2, shown.stdout)
            shown_payload = payload(shown)
            self.assertEqual(
                shown_payload["errors"][0],
                {
                    "code": "completion_history_inconsistent",
                    "message": "stored completion history is inconsistent",
                },
            )
            self.assertIsNone(shown_payload["data"]["verification_evidence"])

            target = target_for(db, repo)
            with closing(connect_snapshot_readonly(db)) as connection:
                with self.assertRaises(StorageError) as corrupt_viewer:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(
                corrupt_viewer.exception.code,
                "completion_history_inconsistent",
            )

    def test_reopen_rejects_done_task_verification_digest_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(
                db,
                repo,
                title="Corrupt done verification expectation",
            )
            task_id = task["task_id"]
            generation = seed_current_review_evidence(db, repo, task_id)
            with closing(sqlite3.connect(db)) as connection:
                generation = connection.execute(
                    "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            completed = completion(db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(db)) as connection:
                with connection:
                    connection.execute(
                        "UPDATE tasks SET verification = ? WHERE task_id = ?",
                        (DEFAULT_VERIFICATION + " changed", task_id),
                    )

            reopened = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Acceptance changed",
                "--json",
            )
            self.assertEqual(reopened.returncode, 2, reopened.stdout)
            self.assertEqual(
                payload(reopened)["errors"][0],
                {
                    "code": "project_state_unreadable",
                    "message": "project state could not be read safely",
                },
            )
            with closing(sqlite3.connect(db)) as connection:
                stored = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                cycle_count = connection.execute(
                    "SELECT COUNT(*) FROM task_completion_cycles WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            self.assertEqual(stored[0], "done")
            self.assertEqual(cycle_count, 1)

    def test_contract_revision_retires_receipt_and_advances_target_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            added = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "Contract-bound receipt",
                "--status",
                "in_progress",
                "--review-tier",
                "0",
                "--verification",
                DEFAULT_VERIFICATION,
                "--contract-scope",
                "Initial exact scope",
                "--contract-acceptance",
                "Initial exact acceptance",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout)
            task_id = payload(added)["data"]["task"]["task_id"]
            generation = set_target(db, repo, task_id)
            self.assertEqual(generation, 1)
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)
            old_receipt = payload(recorded)["data"]["receipt"]
            self.assertEqual(old_receipt["contract_revision"], 1)

            revised = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--contract-scope",
                "Revised exact scope",
                "--contract-acceptance",
                "Revised exact acceptance",
                "--contract-authority-ref",
                f"user_instruction:{task_id}:2",
                "--contract-change-reason",
                "User revised the accepted boundary",
                "--json",
            )
            self.assertEqual(revised.returncode, 0, revised.stdout)
            revised_task = payload(revised)["data"]["task"]
            self.assertEqual(revised_task["status"], "in_progress")
            with closing(sqlite3.connect(db)) as connection:
                current_basis = connection.execute(
                    """
                    SELECT current_contract_revision,
                           review_target_kind, review_target_value,
                           review_target_base_revision,
                           review_target_generation
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
            self.assertEqual(current_basis, (2, "", "", "", 2))

            evidence = payload(
                show_task(db, repo, task_id, json_output=True)
            )["data"]["verification_evidence"]
            self.assertEqual(evidence["contract_revision"], 2)
            self.assertIsNone(evidence["source_revision"])
            self.assertEqual(
                evidence["counts"],
                {
                    "receipts_total": 1,
                    "receipts_exact_current": 0,
                    "qualifying_exact_current": 0,
                    "blocking_exact_current": 0,
                },
            )
            self.assertEqual(
                evidence["gate"]["blocking_code"],
                "review_target_required",
            )
            self.assertEqual(evidence["recent_receipts"], [old_receipt])

    def test_v17_reentry_rejects_altered_completion_cycle_check_constraint(self):
        with tempfile.TemporaryDirectory() as temp:
            target, _task_id = initialize_v16_fixture(Path(temp))
            db = target.db_path
            with closing(connect(db)) as connection:
                apply_verification_receipts_migration(connection)
            with closing(sqlite3.connect(db)) as connection:
                table_sql = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                     WHERE type = 'table'
                       AND name = 'task_completion_cycles'
                    """
                ).fetchone()[0]
                altered_sql = table_sql.replace(
                    "CHECK (verification_basis_version IN (0, 1))",
                    "CHECK (verification_basis_version IN (0, 1, 2))",
                )
                self.assertNotEqual(altered_sql, table_sql)
                connection.execute("PRAGMA writable_schema = ON")
                connection.execute(
                    """
                    UPDATE sqlite_master SET sql = ?
                     WHERE type = 'table'
                       AND name = 'task_completion_cycles'
                    """,
                    (altered_sql,),
                )
                connection.execute("PRAGMA writable_schema = OFF")
                schema_cookie = int(
                    connection.execute("PRAGMA schema_version").fetchone()[0]
                )
                connection.execute(
                    f"PRAGMA schema_version = {schema_cookie + 1}"
                )
                connection.commit()

            with closing(connect(db)) as connection:
                with self.assertRaises(StorageError) as rejected:
                    apply_verification_receipts_migration(connection)
            self.assertEqual(rejected.exception.code, "project_state_unreadable")

    def test_semantic_verification_edit_invalidates_target_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db = initialize(Path(temp))
            task = add_task(db, repo)
            task_id = task["task_id"]
            generation = seed_current_review_evidence(db, repo, task_id)
            recorded = add_receipt(db, repo, task_id, generation)
            self.assertEqual(recorded.returncode, 0, recorded.stdout)

            evidence = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--completion-evidence-kind",
                "external_revision",
                "--completion-revision",
                "release-1",
                "--completion-evidence-reason",
                "Published by the governed release process",
                "--external-revision-approved",
                "--json",
            )
            self.assertEqual(evidence.returncode, 0, evidence.stdout)
            pending = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--status",
                "review_pending",
                "--json",
            )
            self.assertEqual(pending.returncode, 0, pending.stdout)

            edited = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task_id,
                "--verification",
                "python -m unittest tests.test_verification_receipts -v",
                "--json",
            )
            self.assertEqual(edited.returncode, 0, edited.stdout)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                updated = dict(
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                )
            self.assertEqual(updated["status"], "in_progress")
            self.assertEqual(updated["completion_evidence_kind"], "none")
            self.assertEqual(updated["completion_evidence_revision"], "")
            self.assertEqual(updated["review_target_kind"], "")
            self.assertEqual(updated["review_target_value"], "")
            self.assertEqual(updated["review_target_base_revision"], "")
            self.assertEqual(updated["review_target_generation"], 2)
            self.assertEqual(table_count(db, "verification_receipts"), 1)
            self.assertEqual(table_count(db, "review_receipts"), 1)

            shown = payload(
                show_task(
                    db,
                    repo,
                    task_id,
                    json_output=True,
                )
            )["data"]["verification_evidence"]
            self.assertEqual(shown["counts"]["receipts_total"], 1)
            self.assertEqual(shown["counts"]["receipts_exact_current"], 0)
            self.assertEqual(
                shown["gate"]["blocking_code"],
                "review_target_required",
            )
            self.assertIsNone(shown["source_revision"])

    def test_semantic_verification_edit_preserves_explicit_safe_status(self):
        cases = (
            ("in_progress", (), ""),
            ("paused", ("--pause-reason", "Awaiting local input"), ""),
            ("blocked", ("--blocked-reason", "Dependency unavailable"), "Dependency unavailable"),
            ("cancelled", (), ""),
        )
        for requested_status, extra_args, expected_blocked_reason in cases:
            with (
                self.subTest(status=requested_status),
                tempfile.TemporaryDirectory() as temp,
            ):
                repo, db = initialize(Path(temp))
                task = add_task(db, repo)
                task_id = task["task_id"]
                seed_current_review_evidence(db, repo, task_id)
                pending = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--status",
                    "review_pending",
                    "--json",
                )
                self.assertEqual(pending.returncode, 0, pending.stdout)

                edited = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--verification",
                    "Focused verification after target invalidation",
                    "--status",
                    requested_status,
                    *extra_args,
                    "--json",
                )
                self.assertEqual(edited.returncode, 0, edited.stdout)
                updated = payload(edited)["data"]["task"]
                self.assertEqual(updated["status"], requested_status)
                self.assertEqual(
                    updated["blocked_reason"],
                    expected_blocked_reason,
                )
                with closing(sqlite3.connect(db)) as connection:
                    stored = connection.execute(
                        """
                        SELECT completion_evidence_kind,
                               review_target_kind,
                               review_target_generation
                          FROM tasks WHERE task_id = ?
                        """,
                        (task_id,),
                    ).fetchone()
                self.assertEqual(stored, ("none", "", 2))

    def test_semantic_verification_edit_rejects_freshness_conflicts(self):
        cases = (
            (
                (
                    "--status",
                    "ready",
                ),
                "invalid_status_transition",
            ),
            (
                (
                    "--status",
                    "review_pending",
                ),
                "invalid_status_transition",
            ),
            (
                (
                    "--completion-evidence-kind",
                    "external_revision",
                    "--completion-revision",
                    "release-2",
                    "--completion-evidence-reason",
                    "Governed publication",
                    "--external-revision-approved",
                ),
                "completion_evidence_conflict",
            ),
            (
                (
                    "--status",
                    "done",
                    "--verification-complete",
                    "--review-complete",
                    "--commit-not-required",
                ),
                "invalid_status_transition",
            ),
        )
        for extra_args, expected_code in cases:
            with (
                self.subTest(code=expected_code),
                tempfile.TemporaryDirectory() as temp,
            ):
                repo, db = initialize(Path(temp))
                task = add_task(db, repo)
                task_id = task["task_id"]
                generation = seed_current_review_evidence(db, repo, task_id)
                receipt = add_receipt(db, repo, task_id, generation)
                self.assertEqual(receipt.returncode, 0, receipt.stdout)
                pending = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--status",
                    "review_pending",
                    "--json",
                )
                self.assertEqual(pending.returncode, 0, pending.stdout)

                rejected = run_taskgov(
                    "task",
                    "edit",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db),
                    task_id,
                    "--verification",
                    "Conflicting changed verification",
                    *extra_args,
                    "--json",
                )
                self.assertEqual(rejected.returncode, 1, rejected.stdout)
                self.assertEqual(
                    payload(rejected)["errors"][0]["code"],
                    expected_code,
                )
                with closing(sqlite3.connect(db)) as connection:
                    stored = connection.execute(
                        """
                        SELECT status, verification,
                               review_target_generation, review_target_kind
                          FROM tasks WHERE task_id = ?
                        """,
                        (task_id,),
                    ).fetchone()
                self.assertEqual(
                    stored,
                    (
                        "review_pending",
                        DEFAULT_VERIFICATION,
                        1,
                        "diff_fingerprint",
                    ),
                )
