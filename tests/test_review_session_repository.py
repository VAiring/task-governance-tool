from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from task_governance_tool import review_session_repository as sessions
from task_governance_tool.schema_review_sessions import binding_statements
from task_governance_tool.schema_task_ownership import ownership_statements
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.storage import StorageError
from task_governance_tool.task_ownership import read_basis
from task_governance_tool.task_values import TaskValidationError


OWNER = CallerIdentity("00000000-0000-4000-8000-000000000001")
REVIEWER = CallerIdentity("00000000-0000-4000-8000-000000000002")
SECOND = CallerIdentity("00000000-0000-4000-8000-000000000003")
TASK = "tg_task_0000000000000001"
EXECUTION = "tg_execution_0000000000000001"
OTHER_EXECUTION = "tg_execution_0000000000000002"
TARGET = sessions.ReviewSessionTarget("test-project", TASK, 1, "git_snapshot", "sha256:" + "a" * 64, "b" * 40, 1)


class ReviewSessionRepositoryTests(unittest.TestCase):
    """Isolated core primitives, not a claim of public schema-25 activation."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "core.sqlite3"
        self.connection = self.connect()
        self.addCleanup(self.connection.close)
        self.connection.executescript("""
            CREATE TABLE tasks (project_id TEXT, task_id TEXT PRIMARY KEY, status TEXT,
              current_contract_revision INTEGER, review_target_kind TEXT, review_target_value TEXT,
              review_target_base_revision TEXT, review_target_generation INTEGER, UNIQUE(project_id, task_id));
            CREATE TABLE task_completion_cycles (completion_cycle_id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT);
            CREATE TABLE review_receipts (review_receipt_id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT,
              reviewer_key TEXT, receipt_kind TEXT, target_kind TEXT, target_value TEXT,
              target_base_revision TEXT, target_generation INTEGER,
              UNIQUE(task_id, target_generation, reviewer_key));
            CREATE TABLE evidence_references (source_id TEXT, source_kind TEXT, source_state TEXT,
              project_id TEXT, task_id TEXT, contract_revision INTEGER, target_kind TEXT, target_value TEXT,
              target_base_revision TEXT, target_generation INTEGER);
            CREATE TABLE test_findings (receipt_id TEXT, summary TEXT);
            CREATE TABLE test_events (receipt_id TEXT, kind TEXT);
        """)
        for statement in ownership_statements():
            self.connection.execute(statement)
        self.connection.execute("INSERT INTO tasks VALUES (?, ?, 'in_progress', ?, ?, ?, ?, ?)",
                                tuple(TARGET.__dict__.values()))
        self.connection.execute("INSERT INTO task_executions VALUES (?, ?, ?, '2026-09-30T00:00:00Z')",
                                (EXECUTION, TARGET.project_id, TASK))
        self.connection.execute("INSERT INTO task_ownership VALUES (?, ?, ?, 1, 'owned', ?, NULL)",
                                (TASK, TARGET.project_id, EXECUTION, OWNER.session_id))
        self.connection.commit()
        self.serial = 0
        self.legacy = self.receipt("legacy")
        self.connection.commit()
        for statement in binding_statements():
            self.connection.execute(statement)
        self.connection.commit()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def writer(self, connection=None):
        connection = connection or self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def receipt(self, key="reviewer", *, connection=None, target=TARGET, kind="independent", number=None):
        connection = connection or self.connection
        if number is None:
            self.serial += 1
            number = self.serial
        receipt_id = f"tg_review_receipt_{number:016x}"
        connection.execute("INSERT INTO review_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (receipt_id, target.project_id, target.task_id, key, kind, target.target_kind,
                            target.target_value, target.target_base_revision, target.target_generation))
        connection.execute("INSERT INTO evidence_references VALUES (?, 'review_receipt', 'recorded', ?, ?, ?, ?, ?, ?, ?)",
                           (receipt_id, *target.__dict__.values()))
        return receipt_id

    def binding(self, reviewer=REVIEWER, source="direct", execution=EXECUTION):
        return sessions.ReviewSessionBinding(reviewer.session_id, execution, source,
                                             "c" * 64 if source == "handoff" else None)

    def basis(self, connection=None):
        return read_basis(connection or self.connection, project_id=TARGET.project_id, task_id=TASK)

    def record(self, receipt_id, *, binding=None, caller=REVIEWER, target=TARGET, observed=None, connection=None):
        connection = connection or self.connection
        sessions.insert_binding_locked(connection, receipt_id=receipt_id, binding=binding or self.binding(),
                                       target=target, observed_ownership=observed or self.basis(connection), caller=caller)

    def test_new_relation_preserves_legacy_receipts_without_backfill(self):
        self.assertEqual(sessions.read_bindings(self.connection), {})
        self.assertEqual(self.connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 1)
        self.assertEqual(sessions.read_bindings(self.connection, receipt_ids={self.legacy}), {})

    def test_direct_and_parent_forwarded_handoff_keep_actual_reviewer_and_no_slot(self):
        owner_before = self.basis()
        with self.writer():
            first = self.receipt("child")
            self.record(first)
            second = self.receipt("independent-root")
            self.record(second, binding=self.binding(SECOND, "handoff"), caller=OWNER)
        self.assertEqual(sessions.read_bindings(self.connection),
                         {first: self.binding(), second: self.binding(SECOND, "handoff")})
        self.assertEqual(self.basis(), owner_before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM task_ownership").fetchone()[0], 1)
        self.assertFalse((self.path.parent / "usage.sqlite3").exists())

    def test_binding_shape_refuses_missing_identity_or_digest_and_direct_digest(self):
        valid = self.binding()
        for changes in ({"session_id": None}, {"session_id": "private invalid value"},
                        {"execution_id": "wrong"}, {"binding_source": "guess"},
                        {"original_result_digest": "c" * 64}, {"binding_source": "handoff"},
                        {"binding_source": "handoff", "original_result_digest": "C" * 64}):
            with self.subTest(changes=changes), self.assertRaises(TaskValidationError) as error:
                replace(valid, **changes)
            self.assertEqual(error.exception.code, "invalid_review_evidence")
            self.assertNotIn("private invalid value", str(error.exception))

    def test_same_session_alias_rejected_but_second_session_accepted(self):
        with self.writer():
            first = self.receipt("first-name")
            self.record(first)
        with self.assertRaises(TaskValidationError) as error, self.writer():
            self.record(self.receipt("alias"))
        self.assertEqual(error.exception.code, "review_receipt_already_recorded")
        with self.writer():
            second = self.receipt("second-reviewer")
            self.record(second, binding=self.binding(SECOND), caller=SECOND)
        self.assertEqual(set(sessions.read_bindings(self.connection)), {first, second})
        self.assertEqual(self.connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 3)

    def test_parent_cannot_be_substituted_for_direct_reviewer(self):
        for binding, caller in ((self.binding(), OWNER), (self.binding(OWNER), OWNER),
                                (self.binding(source="handoff"), SECOND)):
            with self.subTest(binding=binding, caller=caller), self.assertRaises(TaskValidationError), self.writer():
                self.record(self.receipt(), binding=binding, caller=caller)
        self.assertEqual(sessions.read_bindings(self.connection), {})

    def test_self_review_retains_existing_kind_instead_of_becoming_independent(self):
        with self.writer():
            receipt_id = self.receipt(kind="self_review_fallback")
            self.record(receipt_id, binding=self.binding(OWNER), caller=OWNER)
        self.assertEqual(sessions.read_bindings(self.connection)[receipt_id].session_id, OWNER.session_id)

    def test_review_pending_binding_needs_no_reviewer_slot_but_paused_is_rejected(self):
        with self.writer():
            self.connection.execute("UPDATE tasks SET status='review_pending'")
            self.connection.execute("UPDATE task_ownership SET state='completion_only', owner_session_id=NULL, completion_session_id=?",
                                    (OWNER.session_id,))
            receipt_id = self.receipt()
            self.record(receipt_id)
        self.assertEqual(sessions.read_bindings(self.connection)[receipt_id], self.binding())
        with self.assertRaises(TaskValidationError), self.writer():
            self.connection.execute("UPDATE tasks SET status='paused'")
            self.connection.execute("UPDATE task_ownership SET state='none', completion_session_id=NULL")
            self.record(self.receipt("second"), binding=self.binding(SECOND), caller=SECOND)

    def test_every_current_target_field_is_rechecked(self):
        changes = {"current_contract_revision": 2, "review_target_kind": "git_commit",
                   "review_target_value": "sha256:" + "d" * 64,
                   "review_target_base_revision": "d" * 40, "review_target_generation": 2}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(TaskValidationError) as error, self.writer():
                receipt_id = self.receipt()
                self.connection.execute(f"UPDATE tasks SET {field}=? WHERE task_id=?", (value, TASK))
                self.record(receipt_id)
            self.assertEqual(error.exception.code, "review_receipt_mismatch")

    def test_execution_or_observed_owner_change_is_rejected(self):
        with self.assertRaises(TaskValidationError), self.writer():
            self.record(self.receipt(), binding=self.binding(execution=OTHER_EXECUTION))
        observed = self.basis()
        with self.assertRaises(TaskValidationError) as error, self.writer():
            self.connection.execute("UPDATE task_ownership SET generation=2 WHERE task_id=?", (TASK,))
            self.record(self.receipt(), observed=observed)
        self.assertEqual(error.exception.code, "task_ownership_changed")

    def test_current_reference_is_required_and_its_target_cannot_be_replaced(self):
        for statement in ("DELETE FROM evidence_references WHERE source_id=?",
                          "UPDATE evidence_references SET target_value='wrong' WHERE source_id=?"):
            with self.subTest(statement=statement), self.assertRaises(StorageError), self.writer():
                receipt_id = self.receipt()
                self.connection.execute(statement, (receipt_id,))
                self.record(receipt_id)

    def test_post_binding_failure_rolls_back_receipt_binding_findings_and_events(self):
        with self.assertRaisesRegex(RuntimeError, "late write failure"), self.writer():
            receipt_id = self.receipt()
            self.record(receipt_id)
            self.connection.execute("INSERT INTO test_findings VALUES (?, 'finding')", (receipt_id,))
            self.connection.execute("INSERT INTO test_events VALUES (?, 'review')", (receipt_id,))
            raise RuntimeError("late write failure")
        self.assertEqual(sessions.read_bindings(self.connection), {})
        self.assertEqual(self.connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 1)
        for table in ("test_findings", "test_events"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_past_target_binding_stays_readable_after_target_and_owner_advance(self):
        with self.writer():
            old = self.receipt()
            self.record(old)
        new_target = replace(TARGET, contract_revision=2, target_generation=2)
        with self.writer():
            self.connection.execute("UPDATE tasks SET current_contract_revision=2, review_target_generation=2")
            self.connection.execute("UPDATE task_ownership SET generation=2, owner_session_id=?", (SECOND.session_id,))
            newer = self.receipt("new-alias-on-new-target", target=new_target)
            self.record(newer, target=new_target)
        self.assertEqual(set(sessions.read_bindings(self.connection)), {old, newer})
        self.assertEqual(set(sessions.read_bindings(self.connection, receipt_ids={old})), {old})

    def test_immutable_relation_and_cross_task_execution_guard(self):
        with self.writer():
            receipt_id = self.receipt()
            self.record(receipt_id)
        for statement in ("UPDATE review_receipt_sessions SET binding_source='direct'",
                          "DELETE FROM review_receipt_sessions"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError), self.writer():
                self.connection.execute(statement)
        with self.assertRaises(sqlite3.IntegrityError), self.writer():
            other_task = "tg_task_0000000000000002"
            self.connection.execute("INSERT INTO tasks VALUES (?, ?, 'ready', 1, '', '', '', 0)", (TARGET.project_id, other_task))
            self.connection.execute("INSERT INTO task_executions VALUES (?, ?, ?, '2026-09-30T00:00:00Z')",
                                    (OTHER_EXECUTION, TARGET.project_id, other_task))
            second = self.receipt("other")
            self.connection.execute("INSERT INTO review_receipt_sessions VALUES (?, ?, ?, 'direct', NULL)",
                                    (second, SECOND.session_id, OTHER_EXECUTION))

    def test_stored_alias_or_invalid_uuid_fails_closed(self):
        with self.writer():
            good = self.receipt()
            self.record(good)
        for session in (REVIEWER.session_id, "x" * 36):
            with self.subTest(session=session), self.assertRaises(StorageError), self.writer():
                bad = self.receipt("alias")
                self.connection.execute("INSERT INTO review_receipt_sessions VALUES (?, ?, ?, 'direct', NULL)",
                                        (bad, session, EXECUTION))
                sessions.read_bindings(self.connection)

    def test_concurrent_aliases_serialize_to_one_bound_result(self):
        barrier = threading.Barrier(2)
        def submit(number):
            connection = self.connect()
            try:
                observed = self.basis(connection)
                barrier.wait()
                try:
                    with self.writer(connection):
                        receipt_id = self.receipt(f"alias-{number}", number=number, connection=connection)
                        self.record(receipt_id, connection=connection, observed=observed)
                    return "accepted"
                except TaskValidationError as error:
                    return error.code
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (100, 101)))
        self.assertCountEqual(results, ["accepted", "review_receipt_already_recorded"])
        self.assertEqual(len(sessions.read_bindings(self.connection)), 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 2)

    def test_selected_binding_read_does_not_admit_unrelated_corruption_globally(self):
        with self.writer():
            good = self.receipt()
            self.record(good)
            other = replace(TARGET, task_id="tg_task_0000000000000002")
            self.connection.execute("INSERT INTO tasks VALUES (?, ?, 'in_progress', ?, ?, ?, ?, ?)",
                                    tuple(other.__dict__.values()))
            self.connection.execute("INSERT INTO task_executions VALUES (?, ?, ?, '2026-09-30T00:00:00Z')",
                                    (OTHER_EXECUTION, other.project_id, other.task_id))
            bad = self.receipt("other", target=other)
            self.connection.execute("INSERT INTO review_receipt_sessions VALUES (?, ?, ?, 'direct', NULL)",
                                    (bad, "x" * 36, OTHER_EXECUTION))
        self.assertEqual(set(sessions.read_bindings(self.connection, receipt_ids={good})), {good})
        with self.assertRaises(StorageError):
            sessions.read_bindings(self.connection)
        with self.assertRaises(StorageError):
            sessions.read_bindings(self.connection, receipt_ids={bad})

    def test_no_writer_and_missing_relation_are_not_legacy_fallbacks(self):
        with self.assertRaises(RuntimeError):
            self.record(self.legacy)
        self.connection.execute("DROP TABLE review_receipt_sessions")
        with self.assertRaises(sqlite3.Error):
            sessions.read_bindings(self.connection)


if __name__ == "__main__":
    unittest.main()
