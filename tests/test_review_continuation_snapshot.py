"""Same-transaction evidence reuse and complete native reassessment ancestry."""

from contextlib import closing
import sqlite3
import unittest
from unittest import mock

from tests.review_answer_fixture_support import ReviewFixture, review_fixture
from tests.test_review_continuation import A, B, ident
from task_governance_tool import storage, reviews
from task_governance_tool import review_continuation_evidence_repository as evidence


class ContinuationSnapshotTests(unittest.TestCase):
    def setUp(self):
        fixture = self.enterContext(review_fixture(schema_version=29, contract=None))
        self.task_id, self.target = fixture.task_id, fixture.target
        self.basis, self.caller, self.packet = fixture.basis, fixture.caller, fixture.packet

    original = ReviewFixture.original
    register = ReviewFixture.register
    prepare = ReviewFixture.prepare
    resolve = ReviewFixture.resolve
    reassess_with_finding = ReviewFixture.reassess_with_finding

    def task_row(self, connection):
        return connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.task_id,)).fetchone()

    def settled(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        return old, finding, successor

    def test_locked_gate_projection_and_frozen_basis_select_same_actual_heads(self):
        old, finding, successor = self.settled()
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self.task_row(connection)
            # A second invocation would be a regression, but the primary
            # assertions below inspect native selected/history evidence.
            actual = evidence.effective_for_task
            with mock.patch.object(evidence, "effective_for_task", wraps=actual) as effective_read:
                selected = evidence.completion_selection_locked(connection, task)
                self.assertEqual(effective_read.call_count, 1)
            projected = reviews.read_review_evidence(connection, self.basis["project_id"], self.task_id,
                validated_task=task, source_schema_version=29)
            frozen = evidence.freeze_basis_locked(connection, task)
            heads = projected["continuation"]["effective_receipt_ids"]
            self.assertTrue(projected["gate"]["satisfied"])
            self.assertEqual(set(selected.qualifying_receipt_ids), set(heads))
            self.assertEqual(frozen["effective_receipt_ids"], heads)
            self.assertIn(successor, heads)
            self.assertNotIn(old, heads)
            self.assertEqual(next(row["verdict"] for row in frozen["receipts"]
                if row["review_receipt_id"] == old), "changes_requested")
            self.assertEqual(frozen["resolutions"][0]["review_finding_id"], finding)
            connection.rollback()

    def test_borrowed_read_cannot_escape_scope_connection_basis_or_write(self):
        self.settled()
        with closing(storage.connect_initialized(self.target)) as connection, \
             closing(storage.connect_initialized(self.target)) as other:
            connection.execute("BEGIN IMMEDIATE")
            other.execute("BEGIN")
            task = self.task_row(connection)
            connection.execute("CREATE TEMP TABLE snapshot_write_probe(value)")
            with evidence.effective_review_snapshot(connection, task) as snapshot:
                view = snapshot.for_task(connection, task)
                self.assertEqual(len(view.active_ids), 2)
                for wrong_connection, wrong_task in (
                    (other, task),
                    (connection, dict(task, review_target_generation=task["review_target_generation"] + 1)),
                    (connection, dict(task, task_id=ident("tg_task_", 900))),
                ):
                    with self.subTest(connection=wrong_connection is connection, task=wrong_task["task_id"]):
                        with self.assertRaises(storage.StorageError) as caught:
                            snapshot.for_task(wrong_connection, wrong_task)
                        self.assertEqual(caught.exception.code, "internal_error")
                connection.execute("INSERT INTO snapshot_write_probe VALUES (1)")
                with self.assertRaises(storage.StorageError):
                    snapshot.for_task(connection, task)
            with self.assertRaises(storage.StorageError):
                snapshot.for_task(connection, task)
            connection.rollback()
            other.rollback()
            with self.assertRaises(storage.StorageError):
                with evidence.effective_review_snapshot(connection, task):
                    self.fail("a transaction is required before sharing validation")

    def test_later_reassessment_gets_fresh_pending_gate_and_keeps_old_history(self):
        old, finding, successor = self.settled()
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with evidence.effective_review_snapshot(connection, self.task_row(connection)) as snapshot:
                before = snapshot.for_task(connection, self.task_row(connection))
                self.assertIn(successor, before.active_ids)
            connection.rollback()
        pending = self.prepare(prior=successor)
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self.task_row(connection)
            projected = reviews.read_review_evidence(connection, self.basis["project_id"], self.task_id,
                validated_task=task, source_schema_version=29)
            self.assertEqual(projected["continuation"]["pending_continuation_id"], pending.continuation_id)
            self.assertEqual(projected["gate"]["qualifying_independent_passes"], 1)
            self.assertFalse(projected["gate"]["satisfied"])
            with self.assertRaises(Exception):
                evidence.completion_selection_locked(connection, task)
            with self.assertRaises(storage.StorageError):
                snapshot.for_task(connection, task)
            self.assertEqual(connection.execute("SELECT verdict FROM review_receipts WHERE review_receipt_id=?",
                (old,)).fetchone()[0], "changes_requested")
            connection.rollback()

    def test_snapshot_rejects_write_free_transaction_restart_and_allows_fresh_read(self):
        self.settled()
        for ending in ("commit", "rollback"):
            with self.subTest(ending=ending), closing(storage.connect_initialized(self.target)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                task = self.task_row(connection)
                with self.assertRaises(storage.StorageError):
                    with evidence.effective_review_snapshot(connection, task) as snapshot:
                        before = connection.total_changes
                        getattr(connection, ending)()
                        connection.execute("BEGIN IMMEDIATE")
                        self.assertEqual(connection.total_changes, before)
                        from task_governance_tool import review_material_repository as materials
                        with mock.patch.object(materials, "review_materials", wraps=materials.review_materials) as inner:
                            with self.assertRaises(storage.StorageError) as caught:
                                reviews.read_review_evidence(connection, self.basis["project_id"], self.task_id,
                                    validated_task=task, source_schema_version=29, _continuation_snapshot=snapshot)
                            self.assertEqual(caught.exception.code, "internal_error")
                            inner.assert_not_called()
                        with evidence.effective_review_snapshot(connection, task) as fresh:
                            self.assertEqual(len(fresh.for_task(connection, task).active_ids), 2)
                connection.rollback()

    def test_snapshot_rejects_schema_change_without_row_write(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self.task_row(connection)
            with evidence.effective_review_snapshot(connection, task) as snapshot:
                before = connection.total_changes
                connection.execute("CREATE TABLE snapshot_schema_probe(value INTEGER)")
                self.assertEqual(connection.total_changes, before)
                with self.assertRaises(storage.StorageError) as caught:
                    snapshot.for_task(connection, task)
                self.assertEqual(caught.exception.code, "internal_error")
            connection.rollback()

    def test_outer_snapshot_refresh_precedes_inner_material_scope(self):
        self.settled()
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self.task_row(connection)
            with evidence.effective_review_snapshot(connection, task) as snapshot:
                statements = []
                connection.set_trace_callback(statements.append)
                try:
                    result = reviews.read_review_evidence(connection, self.basis["project_id"], self.task_id,
                        validated_task=task, source_schema_version=29, _continuation_snapshot=snapshot)
                finally:
                    connection.set_trace_callback(None)
                self.assertTrue(result["gate"]["satisfied"])
                outer_release = statements.index(f"RELEASE SAVEPOINT {snapshot._guard}")
                inner_start = next(index for index, sql in enumerate(statements)
                    if sql.startswith("SAVEPOINT taskgov_review_material_"))
                self.assertLess(outer_release, inner_start)
                self.assertTrue(any(sql.startswith("RELEASE SAVEPOINT taskgov_review_material_")
                    for sql in statements[inner_start + 1:]))
                self.assertEqual(len(snapshot.for_task(connection, task).active_ids), 2)
            with self.assertRaises(sqlite3.Error):
                connection.execute(f"RELEASE SAVEPOINT {snapshot._guard}")
            connection.rollback()

    def test_snapshot_cleanup_preserves_read_and_consumer_exceptions(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self.task_row(connection)
            failure = RuntimeError("isolated snapshot read failure")
            with mock.patch.object(evidence.secrets, "token_hex", return_value="a" * 16), \
                 mock.patch.object(evidence, "effective_for_task", side_effect=failure):
                with self.assertRaises(RuntimeError) as caught:
                    with evidence.effective_review_snapshot(connection, task):
                        self.fail("read failure must precede the consumer")
            self.assertIs(caught.exception, failure)
            with self.assertRaises(sqlite3.Error):
                connection.execute("RELEASE SAVEPOINT taskgov_effective_review_" + "a" * 16)
            consumer_failure = RuntimeError("isolated consumer failure")
            with self.assertRaises(RuntimeError) as caught:
                with evidence.effective_review_snapshot(connection, task) as snapshot:
                    connection.rollback()
                    raise consumer_failure
            self.assertIs(caught.exception, consumer_failure)
            with self.assertRaises(storage.StorageError):
                snapshot.for_task(connection, task)

    def test_resolution_ancestry_follows_multiple_revisions_without_per_edge_sql(self):
        initial = self.register([self.original(A, verdict="changes_requested", findings=[
            dict(severity="medium", summary="First precise condition"),
            dict(severity="high", summary="Second precise condition")]), self.original(B)])
        prior = initial["receipts"][0]["receipt"]["review_receipt_id"]
        retained = initial["receipts"][1]["receipt"]["review_receipt_id"]
        findings = [row["finding"]["review_finding_id"] for row in initial["receipts"][0]["findings"]]
        all_ids = [prior]
        for verdict in ("pass", "changes_requested", "pass"):
            continued = self.prepare(prior=prior)
            result = self.register([self.original(A, verdict=verdict, continued=continued)],
                continued=continued, retained=(retained,))
            prior = result["receipts"][0]["receipt"]["review_receipt_id"]
            all_ids.append(prior)
            if len(all_ids) == 2:
                for finding in findings:
                    self.resolve(finding, successor=prior)
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            statements = []
            connection.set_trace_callback(statements.append)
            view = evidence.effective_for_task(connection, self.task_row(connection))
            links = evidence.read_resolution_links(connection, project_id=self.basis["project_id"], task_id=self.task_id)
            connection.set_trace_callback(None)
            self.assertEqual(view.linked_resolved_finding_ids, frozenset(findings))
            self.assertEqual(view.active_ids, {prior, retained})
            self.assertEqual({row["successor_receipt_id"] for row in links}, {all_ids[1]})
            self.assertEqual(connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 5)
            self.assertFalse(any("SELECT prior_receipt_id FROM review_receipt_judgments WHERE" in sql
                for sql in statements))
            frozen = evidence.freeze_basis_locked(connection, self.task_row(connection))
            self.assertEqual({row["review_receipt_id"] for row in frozen["receipts"]}, set(all_ids) | {retained})
            self.assertEqual(set(frozen["effective_receipt_ids"]), {prior, retained})
            connection.rollback()

    def test_resolution_history_rejects_missing_ancestor_and_cycle(self):
        old, finding, successor = self.settled()
        with closing(storage.connect_initialized(self.target)) as connection:
            # Deliberately corrupt only this isolated fixture, inside rollback
            # scopes, to exercise the reader beyond the database guards.
            for prior in (ident("tg_review_receipt_", 900), successor):
                with self.subTest(prior=prior):
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute("PRAGMA defer_foreign_keys=ON")
                        connection.execute("PRAGMA ignore_check_constraints=ON")
                        connection.execute("DROP TRIGGER trg_review_receipt_judgments_no_update")
                        connection.execute("UPDATE review_receipt_judgments SET prior_receipt_id=? WHERE review_receipt_id=?",
                            (prior, old))
                        with self.assertRaises(Exception) as caught:
                            evidence.read_resolution_links(connection, project_id=self.basis["project_id"], task_id=self.task_id)
                        self.assertEqual(caught.exception.code, "evidence_ledger_inconsistent")
                    finally:
                        connection.rollback()
                        connection.execute("PRAGMA ignore_check_constraints=OFF")


if __name__ == "__main__":
    unittest.main()
