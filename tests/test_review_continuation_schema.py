"""Isolated relation guards; not a claim of schema-29 activation or live migration."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from task_governance_tool import schema_review_continuation as schema
from task_governance_tool import review_continuation as values
from task_governance_tool import review_continuation_repository as repository
from task_governance_tool.storage import StorageError
from task_governance_tool.evidence_ledger import canonical_json_bytes
from tests.test_review_continuation import A, B, basis, ident


class ContinuationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
CREATE TABLE tasks(project_id TEXT,task_id TEXT PRIMARY KEY,UNIQUE(project_id,task_id));
CREATE TABLE review_receipt_provenance(review_provenance_id TEXT PRIMARY KEY);
CREATE TABLE review_findings(review_finding_id TEXT PRIMARY KEY,review_receipt_id TEXT,status TEXT);
CREATE TABLE review_receipt_sessions(review_receipt_id TEXT PRIMARY KEY,session_id TEXT,
 binding_source TEXT,execution_id TEXT,original_result_digest TEXT);
CREATE TABLE evidence_references(source_id TEXT,source_kind TEXT,source_state TEXT,
 contract_revision INTEGER,authority_snapshot_id TEXT,acceptance_criterion_id TEXT,verification_criterion_id TEXT);
""")
        self.db.execute(schema.receipt_statement())
        for statement in schema.relation_statements():
            self.db.execute(statement)
        self.db.execute("INSERT INTO tasks VALUES (?,?)", (basis()["project_id"], basis()["task_id"]))
        self.db.commit()

    def receipt(self, index=1, *, actor=A, revision=1, prior=None, continuation=None,
                key="a", verdict="pass", contract_revision=1, authority=None, commit=True):
        value = basis()
        receipt = ident("tg_review_receipt_", index)
        self.db.execute("""INSERT INTO review_receipts(review_receipt_id,task_id,project_id,reviewer_key,
receipt_kind,verdict,target_kind,target_value,target_base_revision,target_generation,created_at,judgment_revision)
VALUES (?,?,?,?,'independent',?,?,?,?,1,'2026-10-14T00:00:00Z',?)""",
            (receipt, value["task_id"], value["project_id"], key, verdict,
             value["target_kind"], value["target_value"], value["target_base_revision"], revision))
        self.db.execute("INSERT INTO review_receipt_sessions VALUES (?,?,'handoff',?,?)",
            (receipt, actor, value["execution_id"], f"{index:064x}"))
        self.db.execute("INSERT INTO evidence_references VALUES (?,'review_receipt','recorded',?,?,?,?)",
            (receipt, contract_revision, authority or value["authority_snapshot_id"],
             value["acceptance_criterion_id"], value["verification_criterion_id"]))
        self.db.execute("INSERT INTO review_receipt_judgments VALUES (?,?,?,?)", (receipt, revision, prior, continuation))
        if commit:
            self.db.commit()
        return receipt

    def continuation(self, index=1, *, prior=None, sequence=1, actor=A, action="reassess", prior_receipt=None):
        value = basis()
        current = ident("tg_review_continuation_", index)
        self.db.execute("""INSERT INTO review_continuations
(continuation_id,project_id,task_id,target_generation,preparation_id,sequence,predecessor_id,basis_json,basis_digest,selection_digest,route,state,created_at,registered_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,'preparing',?,NULL)""",
            (current, value["project_id"], value["task_id"], value["target_generation"],
             ident("tg_review_preparation_"), sequence, prior,
             canonical_json_bytes(value).decode(), values.basis_digest(value),
             values.selection_digest((values.SelectedReviewer(actor, action, prior_receipt, "explicit reconsideration"),)), "manual",
             "2026-10-14T00:00:00Z"))
        self.db.execute("INSERT INTO review_continuation_reviewers VALUES (?,?,?,?,?,NULL,NULL)",
            (current, actor, action, prior_receipt, "explicit reconsideration"))
        self.db.execute("UPDATE review_continuations SET state='prepared' WHERE continuation_id=?", (current,))
        self.db.commit()
        return current

    def publish(self, current, receipt, actor=A, index=2):
        self.db.execute("""UPDATE review_continuation_reviewers SET result_receipt_id=?,original_result_digest=?
WHERE continuation_id=? AND session_id=?""", (receipt, f"{index:064x}", current, actor))
        self.db.execute("UPDATE review_continuations SET state='registered',registered_at=? WHERE continuation_id=?",
            ("2026-10-14T00:00:01Z", current))
        self.db.commit()

    def test_no_committed_receipt_without_exact_judgment_metadata(self):
        receipt = self.receipt()
        self.assertEqual(self.db.execute("SELECT judgment_revision FROM review_receipt_judgments WHERE review_receipt_id=?", (receipt,)).fetchone(), (1,))
        self.db.execute("""INSERT INTO review_receipts(review_receipt_id,task_id,project_id,reviewer_key,
receipt_kind,verdict,target_kind,target_value,target_base_revision,target_generation,created_at)
VALUES (?,?,?,'missing','independent','pass','git_snapshot','x','base',1,'2026-10-14T00:00:00Z')""",
            (ident("tg_review_receipt_", 3), basis()["task_id"], basis()["project_id"]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_same_reviewer_successor_preserves_old_ng(self):
        old = self.receipt(verdict="changes_requested")
        current = self.continuation(prior_receipt=old)
        fresh = self.receipt(2, revision=2, prior=old, continuation=current)
        self.publish(current, fresh)
        self.assertEqual(self.db.execute("SELECT verdict FROM review_receipts ORDER BY judgment_revision").fetchall(), [("changes_requested",), ("pass",)])
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_unselected_pass_is_untouched_by_other_supplement(self):
        retained = self.receipt()
        current = self.continuation(actor=B, action="supplement")
        fresh = self.receipt(2, actor=B, key="b", continuation=current)
        self.publish(current, fresh, actor=B)
        self.assertEqual(self.db.execute("SELECT judgment_revision,prior_receipt_id,continuation_id FROM review_receipt_judgments WHERE review_receipt_id=?", (retained,)).fetchone(), (1, None, None))

    def test_cross_session_alias_contract_and_authority_successors_rejected(self):
        old = self.receipt()
        current = self.continuation(prior_receipt=old)
        for changes in (dict(actor=B), dict(key="alias"), dict(contract_revision=2),
                        dict(authority=ident("tg_authority_snapshot_", 2)), dict(revision=3)):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.receipt(2, revision=changes.get("revision", 2), prior=old, continuation=current,
                    **{key: value for key, value in changes.items() if key != "revision"})
            self.db.rollback()

    def test_arbitrary_new_revision_and_ordinary_duplicate_rejected(self):
        old = self.receipt()
        for changes in (dict(revision=2, prior=old), dict()):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.receipt(2, **changes)
            self.db.rollback()

    def test_result_cannot_publish_before_actual_registered_judgment(self):
        old = self.receipt()
        current = self.continuation(prior_receipt=old)
        with self.assertRaises(sqlite3.IntegrityError):
            self.publish(current, old, index=1)
        self.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE review_continuations SET state='registered',registered_at='2026-10-14T00:00:01Z' WHERE continuation_id=?", (current,))

    def test_registration_monotone_and_old_history_not_deleted(self):
        old = self.receipt()
        current = self.continuation(prior_receipt=old)
        fresh = self.receipt(2, revision=2, prior=old, continuation=current)
        self.publish(current, fresh)
        for sql, parameters in (
            ("UPDATE review_continuations SET state='prepared',registered_at=NULL WHERE continuation_id=?", (current,)),
            ("DELETE FROM review_continuations WHERE continuation_id=?", (current,)),
            ("DELETE FROM review_continuation_reviewers WHERE continuation_id=?", (current,)),
            ("UPDATE review_receipt_judgments SET prior_receipt_id=NULL WHERE review_receipt_id=?", (fresh,)),
            ("DELETE FROM review_receipt_judgments WHERE review_receipt_id=?", (old,))):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql, parameters)
            self.db.rollback()

    def test_second_round_requires_registered_exact_predecessor(self):
        old = self.receipt()
        current = self.continuation(prior_receipt=old)
        with self.assertRaises(sqlite3.IntegrityError):
            self.continuation(2, prior=current, sequence=2, prior_receipt=old)
        self.db.rollback()
        fresh = self.receipt(2, revision=2, prior=old, continuation=current)
        self.publish(current, fresh)
        next_round = self.continuation(2, prior=current, sequence=2, prior_receipt=fresh)
        self.assertEqual(self.db.execute("SELECT sequence FROM review_continuations WHERE continuation_id=?", (next_round,)).fetchone(), (2,))

    def test_resolution_requires_registered_successor_and_resolved_ancestor(self):
        old = self.receipt(verdict="changes_requested")
        finding = ident("tg_review_finding_")
        self.db.execute("INSERT INTO review_findings VALUES (?,?,'open')", (finding, old))
        self.db.commit()
        current = self.continuation(prior_receipt=old)
        fresh = self.receipt(2, revision=2, prior=old, continuation=current)
        args = (finding, fresh, current, "original reviewer reconsidered", "f" * 64, "2026-10-14T00:00:02Z")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO review_reassessment_resolutions VALUES (?,?,?,?,?,?)", args)
        self.db.rollback()
        self.publish(current, fresh)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO review_reassessment_resolutions VALUES (?,?,?,?,?,?)", args)
        self.db.rollback()
        self.db.execute("UPDATE review_findings SET status='resolved' WHERE review_finding_id=?", (finding,))
        self.db.execute("INSERT INTO review_reassessment_resolutions VALUES (?,?,?,?,?,?)", args)
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE review_reassessment_resolutions SET resolution_summary='overwrite'")
        self.db.rollback()


class ContinuationRepositoryReadTests(unittest.TestCase):
    receipt = ContinuationSchemaTests.receipt
    continuation = ContinuationSchemaTests.continuation
    publish = ContinuationSchemaTests.publish

    def setUp(self):
        ContinuationSchemaTests.setUp(self)
        self.db.row_factory = sqlite3.Row

    def read(self):
        return repository.read_continuations(self.db, project_id=basis()["project_id"], task_id=basis()["task_id"])

    def test_prepared_read_is_complete_and_write_free(self):
        old = self.receipt(verdict="changes_requested")
        current = self.continuation(prior_receipt=old)
        before = self.db.total_changes
        rows = self.read()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].continuation_id, current)
        self.assertEqual(rows[0].selected[0].prior_receipt_id, old)
        self.assertEqual(rows[0].state, "prepared")
        self.assertEqual(rows[0].results, ())
        self.assertEqual(self.db.total_changes, before)

    def test_registered_read_keeps_previous_round_and_exact_original_binding(self):
        old = self.receipt(verdict="changes_requested")
        current = self.continuation(prior_receipt=old)
        fresh = self.receipt(2, revision=2, prior=old, continuation=current)
        self.publish(current, fresh)
        next_round = self.continuation(2, prior=current, sequence=2, prior_receipt=fresh)
        rows = self.read()
        self.assertEqual([row.state for row in rows], ["registered", "prepared"])
        self.assertEqual(rows[0].results, ((A, fresh, f"{2:064x}"),))
        self.assertEqual(rows[1].predecessor_id, current)
        self.assertEqual(rows[1].continuation_id, next_round)

    def test_partial_publication_cannot_be_admitted_as_committed_round(self):
        old = self.receipt()
        current = self.continuation(prior_receipt=old)
        fresh = self.receipt(2, revision=2, prior=old, continuation=current)
        self.db.execute("UPDATE review_continuation_reviewers SET result_receipt_id=?,original_result_digest=? WHERE continuation_id=?",
                        (fresh, f"{2:064x}", current))
        self.db.commit()
        with self.assertRaises(StorageError):
            self.read()

    def test_offline_binding_corruption_not_trusted_because_sql_once_passed(self):
        old = self.receipt()
        current = self.continuation(prior_receipt=old)
        fresh = self.receipt(2, revision=2, prior=old, continuation=current)
        self.publish(current, fresh)
        self.db.execute("UPDATE review_receipt_sessions SET session_id=? WHERE review_receipt_id=?", (B, fresh))
        self.db.commit()
        with self.assertRaises(StorageError):
            self.read()

    def test_offline_digest_corruption_fails_before_display(self):
        old = self.receipt()
        current = self.continuation(prior_receipt=old)
        self.db.execute("DROP TRIGGER trg_review_continuations_monotone")
        self.db.execute("UPDATE review_continuations SET basis_digest=? WHERE continuation_id=?", ("f" * 64, current))
        self.db.commit()
        with self.assertRaises(StorageError):
            self.read()


class ContinuationReadScopeTests(unittest.TestCase):
    """Real SQL inventories, including unrelated owners and historical captures."""

    def setUp(self):
        ContinuationSchemaTests.setUp(self)
        self.db.row_factory = sqlite3.Row

    def history(self, task_index=1, *, generation=1, depth=3):
        from task_governance_tool.review_session_transport import continued_metadata_digest

        value = basis(task_id=ident("tg_task_", task_index), target_generation=generation)
        self.db.execute("INSERT OR IGNORE INTO tasks VALUES (?,?)", (value["project_id"], value["task_id"]))
        original = None
        previous = None
        identifiers = []
        for sequence in range(1, depth + 1):
            if previous is not None:
                self.db.execute("""UPDATE review_continuations SET state='superseded',
retired_at='2026-10-14T00:00:02Z',retirement_digest=? WHERE continuation_id=?""", ("e" * 64, previous))
            current = ident("tg_review_continuation_", task_index * 10000 + generation * 100 + sequence)
            selected = tuple(values.SelectedReviewer(actor, "supplement", None, "Missing result")
                             for actor in ((A, B) if sequence == 1 else (B,)))
            self.db.execute("""INSERT INTO review_continuations
(continuation_id,project_id,task_id,target_generation,preparation_id,sequence,predecessor_id,
basis_json,basis_digest,selection_digest,route,state,created_at)
VALUES (?,?,?,?,?,?,?,?,?,?,'manual','preparing','2026-10-14T00:00:00Z')""",
                (current, value["project_id"], value["task_id"], generation,
                 ident("tg_review_preparation_", task_index * 100 + generation), sequence, previous,
                 canonical_json_bytes(value).decode(), values.basis_digest(value), values.selection_digest(selected)))
            for choice in selected:
                self.db.execute("INSERT INTO review_continuation_reviewers VALUES (?,?,?,?,?,NULL,NULL)",
                    (current, choice.session_id, choice.action, choice.prior_receipt_id, choice.reason))
            if original is not None:
                self.db.execute("INSERT INTO review_continuation_retained_originals VALUES (?,?,?,?,?,NULL)",
                    (current, original.continuation_id, A, "d" * 64,
                     continued_metadata_digest(original, original.selected[0], "d" * 64)))
            self.db.execute("UPDATE review_continuations SET state='prepared' WHERE continuation_id=?", (current,))
            if original is None:
                original = repository.read_continuations(self.db, project_id=value["project_id"], task_id=value["task_id"])[-1]
            previous = current
            identifiers.append(current)
        self.db.commit()
        return identifiers

    def measured(self, operation):
        counts = {"continuations": 0, "retained": 0}

        def count_row(cursor, row):
            names = {column[0] for column in cursor.description}
            if {"selection_digest", "retirement_digest"} <= names:
                counts["continuations"] += 1
            if {"metadata_digest", "prior_continuation_id"} <= names:
                counts["retained"] += 1
            return sqlite3.Row(cursor, row)

        before = self.db.total_changes
        self.db.row_factory = count_row
        self.db.execute("PRAGMA query_only=ON")
        try:
            result = operation()
        finally:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=OFF")
        self.assertEqual(self.db.total_changes, before)
        return result, counts

    def read(self, task_index=1):
        return repository.read_continuations(self.db, project_id=basis()["project_id"], task_id=ident("tg_task_", task_index))

    def test_other_task_growth_does_not_expand_retained_acquisition(self):
        self.history()
        expected, small = self.measured(self.read)
        for index in range(2, 102):
            self.history(index)
        actual, large = self.measured(self.read)
        self.assertEqual(actual, expected)
        self.assertEqual(small, {"continuations": 3, "retained": 2})
        self.assertEqual(large, small)
        # The fixture has real, admitted foreign-owner captures, not mock rows.
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_continuation_retained_originals").fetchone()[0], 202)
        repository.validate_judgment_storage(self.db)

    def test_global_validation_acquires_each_history_and_capture_once(self):
        for index in range(1, 26):
            self.history(index)
        for index in range(26, 51):
            self.db.execute("INSERT INTO tasks VALUES (?,?)", (basis()["project_id"], ident("tg_task_", index)))
        self.db.commit()
        _, counts = self.measured(lambda: repository.validate_judgment_storage(self.db))
        self.assertEqual(counts, {"continuations": 75, "retained": 50})

    def test_empty_history_reads_no_foreign_captures(self):
        self.history(2)
        result, counts = self.measured(self.read)
        self.assertEqual(result, ())
        self.assertEqual(counts, {"continuations": 0, "retained": 0})

    def test_all_generations_and_long_retained_chain_remain_validated(self):
        first = self.history(generation=1, depth=25)
        second = self.history(generation=2)
        rows, counts = self.measured(self.read)
        self.assertEqual([row.continuation_id for row in rows], first + second)
        self.assertEqual(counts, {"continuations": 28, "retained": 26})
        # Corruption of an early capture must not be hidden by the latest page.
        self.db.execute("DROP TRIGGER trg_review_retained_originals_publication")
        self.db.execute("UPDATE review_continuation_retained_originals SET metadata_digest=? WHERE continuation_id=?",
                        ("f" * 64, first[1]))
        self.db.commit()
        with self.assertRaises(StorageError):
            self.measured(self.read)
        with self.assertRaises(StorageError):
            self.measured(lambda: repository.validate_judgment_storage(self.db))

    def test_unrelated_corruption_is_still_rejected_by_global_validation(self):
        self.history()
        other = self.history(2)
        expected = self.read()
        self.db.execute("DROP TRIGGER trg_review_retained_originals_publication")
        self.db.execute("UPDATE review_continuation_retained_originals SET metadata_digest=? WHERE continuation_id=?",
                        ("f" * 64, other[1]))
        self.db.commit()
        self.assertEqual(self.measured(self.read)[0], expected)
        with self.assertRaises(StorageError):
            self.measured(lambda: repository.validate_judgment_storage(self.db))

    def test_missing_and_cross_task_retained_ancestors_fail_closed(self):
        current = self.history()
        other = self.history(2)
        self.db.execute("PRAGMA foreign_keys=OFF")
        for prior in (ident("tg_review_continuation_", 999999), other[0]):
            with self.subTest(prior=prior):
                self.db.execute("SAVEPOINT corrupt")
                self.db.execute("DROP TRIGGER trg_review_retained_originals_publication")
                self.db.execute("UPDATE review_continuation_retained_originals SET prior_continuation_id=? WHERE continuation_id=?",
                                (prior, current[-1]))
                with self.assertRaises(StorageError):
                    self.read()
                with self.assertRaises(StorageError):
                    repository.validate_judgment_storage(self.db)
                self.db.execute("ROLLBACK TO corrupt")
                self.db.execute("RELEASE corrupt")


if __name__ == "__main__":
    unittest.main()
