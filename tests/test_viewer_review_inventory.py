"""Viewer Finding scan sharing preserves the scalar review evidence boundary."""

import gc
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.test_viewer_snapshot import initialized_target
from tests.m14_test_support import remove_v18_evidence_ledger_for_test
from tests.review_test_helpers import seed_review_evidence_connection
from tests.test_viewer_maintenance import (
    make_target, add_viewer_event, viewer_state, FIXED_TIME,
)
from task_governance_tool import reviews
from task_governance_tool import viewer_maintenance
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.storage import (
    SCHEMA_VERSION, StorageError, connect, connect_snapshot_readonly,
)
from task_governance_tool.tasks import add_task, list_tasks_for_viewer
from task_governance_tool.viewer import (
    build_viewer_snapshot, resolve_canonical_viewer_output_target,
)


def seed_reviews(target):
    task_ids = []
    with closing(connect(target.db_path)) as connection:
        for index in range(3):
            caller = CallerIdentity(f"00000000-0000-4000-8000-{index + 1:012x}")
            task = add_task(
                connection, target.project, title=f"Inventory task {index}",
                caller=caller, priority=("low", "urgent", "normal")[index],
                review_tier=2, status="in_progress",
            ).task
            task_id = task["task_id"]
            task_ids.append(task_id)
            connection.commit()
            if index == 2:
                continue
            for generation in range(1, 3 if index == 0 else 2):
                reviews.set_review_target(
                    connection, target.project, task_id, caller=caller,
                    kind="diff_fingerprint", revision="sha256:" + str(generation) * 64,
                )
                for ordinal in range(12 if generation == 2 else 2):
                    receipt = reviews.add_review_receipt(
                        connection, target.project, task_id, caller=caller,
                        reviewer=f"reviewer-{ordinal}", kind="independent",
                        verdict="pass", summary="Inventory accepted",
                        reviewer_class="human", model_state="not_applicable",
                        skill_state="not_applicable", review_profiles=["general"],
                        review_lenses=["correctness"], context_relation="external_context",
                        review_methods=["review_packet_inspection"],
                    ).receipt
                    if ordinal == 0:
                        for finding_index in range(13):
                            reviews.add_review_finding(
                                connection, target.project, task_id, caller=caller,
                                receipt_id=receipt["review_receipt_id"],
                                severity=(
                                    "medium" if index == 0 and generation == 1
                                    and finding_index == 0 else "low"
                                ), summary=f"Finding {generation} {finding_index}",
                            )
                connection.commit()
    return task_ids


class ViewerReviewInventoryTests(unittest.TestCase):
    def test_shared_inventory_does_not_cap_task_selection_at_500(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                for index in range(501):
                    add_task(connection, target.project, title=f"Task {index}")
                connection.commit()
            statements = []
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                connection.set_trace_callback(statements.append)
                result = build_viewer_snapshot(connection, target)
            self.assertEqual(result.task_count, 501)
            self.assertEqual(len(result.snapshot["tasks"]), 501)
            self.assertEqual(sum(
                "from review_findings as finding" in " ".join(sql.lower().split())
                for sql in statements
            ), 1)
            self.assertTrue(all(
                task["completion_history"]["total"] == 0
                for task in result.snapshot["tasks"]
            ))

    def test_invalid_finding_preserves_last_good_viewer_and_due_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            seed_reviews(target)
            viewer_maintenance.publish_setup_viewer(target, observed_at=FIXED_TIME)
            output = resolve_canonical_viewer_output_target(target)
            last_good = output.path.read_bytes()
            previous = viewer_state(target)
            add_viewer_event(target, "Keep Viewer due")
            with closing(connect(target.db_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "UPDATE review_findings SET review_receipt_id = ? "
                    "WHERE rowid = (SELECT MIN(rowid) FROM review_findings)",
                    ("tg_review_receipt_" + "f" * 16,),
                )
                connection.commit()
            result = viewer_maintenance.run_routine_viewer_refresh(
                target, observed_at="2026-07-27T00:00:01Z",
            )
            self.assertEqual((result.code, result.renders), ("failed", 0))
            self.assertEqual(output.path.read_bytes(), last_good)
            state = viewer_state(target)
            self.assertTrue(state.due)
            self.assertEqual(state.rendered_generation, previous.rendered_generation)
            self.assertEqual(state.last_success_at, previous.last_success_at)
            self.assertEqual(list(output.path.parent.glob(".task-viewer-*.tmp")), [])

    def test_corruption_in_any_generation_still_fails_the_whole_batch(self):
        cases = (
            "orphan", "parent_blob", "receipt_task_blob", "receipt_project",
            "parent_alias_collision", "historical_severity", "reference", "provenance",
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            task_ids = seed_reviews(target)
            for case in cases:
                with self.subTest(case=case):
                    db = Path(tmp) / f"{case}.sqlite"
                    with (
                        closing(sqlite3.connect(target.db_path)) as source,
                        closing(sqlite3.connect(db)) as destination,
                    ):
                        source.backup(destination)
                    with closing(connect(db)) as connection:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        connection.execute("PRAGMA ignore_check_constraints = ON")
                        finding = connection.execute(
                            "SELECT finding.* FROM review_findings AS finding "
                            "JOIN review_receipts AS receipt USING(review_receipt_id) "
                            "WHERE receipt.task_id = ? ORDER BY finding.rowid LIMIT 1",
                            (task_ids[0],),
                        ).fetchone()
                        receipt_id = finding["review_receipt_id"]
                        finding_id = finding["review_finding_id"]
                        if case in {"orphan", "parent_blob"}:
                            invalid_parent = (
                                "tg_review_receipt_" + "f" * 16 if case == "orphan"
                                else sqlite3.Binary(receipt_id.encode("utf-8"))
                            )
                            connection.execute(
                                "UPDATE review_findings SET review_receipt_id = ? "
                                "WHERE review_finding_id = ?", (invalid_parent, finding_id),
                            )
                        elif case == "receipt_task_blob":
                            connection.execute(
                                "UPDATE review_receipts SET task_id = CAST(? AS BLOB) "
                                "WHERE review_receipt_id = ?", (task_ids[0], receipt_id),
                            )
                        elif case == "receipt_project":
                            connection.execute(
                                "UPDATE review_receipts SET project_id = 'foreign' "
                                "WHERE review_receipt_id = ?", (receipt_id,),
                            )
                        elif case == "parent_alias_collision":
                            foreign_receipt = connection.execute(
                                "SELECT review_receipt_id FROM review_receipts "
                                "WHERE task_id = ? LIMIT 1", (task_ids[1],),
                            ).fetchone()[0]
                            connection.execute(
                                "UPDATE review_receipts SET review_receipt_id = CAST(? AS BLOB) "
                                "WHERE review_receipt_id = ?", (receipt_id, foreign_receipt),
                            )
                        elif case == "historical_severity":
                            connection.execute(
                                "UPDATE review_findings SET severity = 1.5 "
                                "WHERE review_finding_id = ?", (finding_id,),
                            )
                        else:
                            table = (
                                "evidence_references" if case == "reference"
                                else "review_receipt_provenance"
                            )
                            trigger_name = f"trg_{table}_no_update"
                            trigger = connection.execute(
                                "SELECT sql FROM sqlite_master WHERE name = ?",
                                (trigger_name,),
                            ).fetchone()[0]
                            connection.execute(f"DROP TRIGGER {trigger_name}")
                            if case == "reference":
                                connection.execute(
                                    "UPDATE evidence_references SET source_id = ? "
                                    "WHERE source_kind = 'review_finding' AND source_id = ?",
                                    ("tg_review_finding_" + "f" * 16, finding_id),
                                )
                            else:
                                connection.execute(
                                    "UPDATE review_receipt_provenance "
                                    "SET created_at = '1999-01-01T00:00:00Z' "
                                    "WHERE review_receipt_id = ?", (receipt_id,),
                                )
                            connection.execute(trigger)
                        connection.commit()
                    before = db.read_bytes()
                    with closing(connect_snapshot_readonly(db)) as connection:
                        rows = [connection.execute(
                            "SELECT * FROM tasks WHERE task_id = ?", (task_id,),
                        ).fetchone() for task_id in task_ids]
                        with self.assertRaises(StorageError) as failure:
                            reviews._read_viewer_review_evidence(
                                connection, target.project.project_id, rows,
                                source_schema_version=SCHEMA_VERSION,
                            )
                        self.assertEqual(failure.exception.code, "evidence_ledger_inconsistent")
                        self.assertEqual(connection.total_changes, 0)
                        self.assertTrue(connection.in_transaction)
                    self.assertEqual(db.read_bytes(), before)
                    # A chained validator error can retain SQLite statement
                    # frames after connection.close(); release them before
                    # Windows removes this isolated corruption fixture.
                    del failure
                    gc.collect()

    def test_legacy_snapshot_retains_shared_scan_and_scalar_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                # Downgrade the empty fixture, never discard current ownership
                # history to manufacture a legacy database.
                remove_v18_evidence_ledger_for_test(connection)
                for index in range(3):
                    task = add_task(
                        connection, target.project, title=f"Legacy task {index}",
                        review_tier=2,
                    ).task
                    if index != 2:
                        seed_review_evidence_connection(connection, task["task_id"])
                        receipt_id = connection.execute(
                            "SELECT review_receipt_id FROM review_receipts "
                            "WHERE task_id = ? LIMIT 1", (task["task_id"],),
                        ).fetchone()[0]
                        connection.execute(
                            "INSERT INTO review_findings VALUES "
                            "(?, ?, 'low', 'open', 'Legacy note', '', "
                            "'2026-07-22T00:00:00Z', NULL)",
                            ("tg_review_finding_" + f"{index:016x}", receipt_id),
                        )
                connection.commit()
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                rows = connection.execute("SELECT * FROM tasks").fetchall()
                expected = {row["task_id"]: reviews.read_review_evidence(
                    connection, target.project.project_id, row["task_id"],
                    validated_task=row, source_schema_version=17,
                ) for row in rows}
                for evidence in expected.values():
                    for receipt in evidence["recent_receipts"]:
                        receipt.pop("review_provenance", None)
                statements = []
                connection.set_trace_callback(statements.append)
                result = list_tasks_for_viewer(
                    connection, target.project, source_schema_version=17,
                )
                connection.set_trace_callback(None)
                self.assertEqual(
                    {task["task_id"]: task["review_evidence"] for task in result.tasks},
                    expected,
                )
                self.assertEqual(sum(
                    "from review_findings as finding" in " ".join(sql.lower().split())
                    for sql in statements
                ), 1)

    def test_nontransactional_repository_reader_keeps_scalar_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                add_task(connection, target.project, title="Ordinary reader")
                connection.commit()
                self.assertFalse(connection.in_transaction)
                with mock.patch.object(
                    reviews, "read_review_evidence", wraps=reviews.read_review_evidence,
                ) as scalar:
                    result = list_tasks_for_viewer(connection, target.project)
                self.assertEqual(len(result.tasks), 1)
                scalar.assert_called_once()
                self.assertFalse(connection.in_transaction)

    def test_shared_scan_matches_scalar_evidence_and_validates_every_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            task_ids = seed_reviews(target)
            before = target.db_path.read_bytes()
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                rows = connection.execute("SELECT * FROM tasks").fetchall()
                expected = {}
                for row in rows:
                    evidence = reviews.read_review_evidence(
                        connection, target.project.project_id, row["task_id"],
                        validated_task=row,
                    )
                    for receipt in evidence["recent_receipts"]:
                        receipt.pop("review_provenance", None)
                    expected[row["task_id"]] = evidence
                statements = []
                validated_ids = set()
                validator = reviews.validate_selected_task_receipt_evidence

                def observe_validation(connection, **kwargs):
                    ids = kwargs["review_finding_ids"]
                    self.assertLessEqual(len(ids), 3)
                    validated_ids.update(ids)
                    return validator(connection, **kwargs)

                with (
                    mock.patch.object(reviews, "COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE", 3),
                    mock.patch.object(
                        reviews, "validate_selected_task_receipt_evidence",
                        side_effect=observe_validation,
                    ),
                ):
                    connection.set_trace_callback(statements.append)
                    result = list_tasks_for_viewer(connection, target.project)
                    connection.set_trace_callback(None)
                self.assertEqual(
                    {task["task_id"]: task["review_evidence"] for task in result.tasks},
                    expected,
                )
                all_ids = {
                    row[0] for row in connection.execute(
                        "SELECT review_finding_id FROM review_findings"
                    )
                }
                self.assertEqual(validated_ids, all_ids)
                inventory_sql = [
                    sql for sql in statements
                    if "from review_findings as finding" in " ".join(sql.lower().split())
                ]
                self.assertEqual(len(inventory_sql), 1)
                plan = [row[3].lower() for row in connection.execute(
                    "EXPLAIN QUERY PLAN " + inventory_sql[0]
                )]
                self.assertEqual(sum("scan finding" in step for step in plan), 1, plan)
                self.assertTrue(any("search receipt using" in step for step in plan), plan)
                self.assertTrue(connection.in_transaction)
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                self.assertEqual(connection.total_changes, 0)
            first, second, empty = (expected[task_id] for task_id in task_ids)
            self.assertEqual(first["counts"]["open_medium"], 1)
            self.assertEqual(first["counts"]["receipts_current_generation"], 12)
            self.assertFalse(first["gate"]["satisfied"])
            self.assertTrue(second["gate"]["satisfied"])
            self.assertEqual(len(first["recent_receipts"]), 10)
            self.assertEqual(len(second["recent_findings"]), 10)
            self.assertEqual(empty["counts"]["receipts_total"], 0)
            generated_at = "2026-10-12T00:00:00Z"
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                shared_snapshot = build_viewer_snapshot(
                    connection, target, generated_at=generated_at,
                ).snapshot

            def scalar_batch(connection, project_id, task_rows, *, source_schema_version):
                return [reviews.read_review_evidence(
                    connection, project_id, row["task_id"], validated_task=row,
                    source_schema_version=source_schema_version,
                ) for row in task_rows]

            with (
                closing(connect_snapshot_readonly(target.db_path)) as connection,
                mock.patch.object(reviews, "_read_viewer_review_evidence", scalar_batch),
            ):
                scalar_snapshot = build_viewer_snapshot(
                    connection, target, generated_at=generated_at,
                ).snapshot
            self.assertEqual(shared_snapshot, scalar_snapshot)
            self.assertEqual(target.db_path.read_bytes(), before)

    def test_empty_task_batch_opens_no_finding_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            statements = []
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                connection.set_trace_callback(statements.append)
                result = list_tasks_for_viewer(connection, target.project)
            self.assertEqual(result.tasks, [])
            self.assertFalse(any("FROM review_findings AS finding" in sql for sql in statements))

    def test_shared_inventory_sql_errors_are_sanitized_and_cursor_closed(self):
        for error_code, expected in (
            (sqlite3.SQLITE_BUSY, "database_busy"),
            (sqlite3.SQLITE_LOCKED, "database_busy"),
            (sqlite3.SQLITE_ERROR, "evidence_ledger_inconsistent"),
        ):
            for stage in ("open", "consume"):
                with self.subTest(error_code=error_code, stage=stage):
                    connection = mock.Mock(in_transaction=True)
                    error = sqlite3.OperationalError("Authorization: Bearer private")
                    error.sqlite_errorcode = error_code
                    cursor = mock.Mock()
                    if stage == "open":
                        connection.execute.side_effect = error
                    else:
                        connection.execute.return_value = cursor
                    with (
                        mock.patch.object(reviews, "_read_review_evidence", side_effect=error),
                        self.assertRaises(StorageError) as failure,
                    ):
                        reviews._read_viewer_review_evidence(
                            connection, "project", [{"task_id": "tg_task_" + "1" * 16}],
                            source_schema_version=SCHEMA_VERSION,
                        )
                    self.assertEqual(failure.exception.code, expected)
                    self.assertNotIn("Authorization", str(failure.exception))
                    if stage == "consume":
                        cursor.close.assert_called_once()
