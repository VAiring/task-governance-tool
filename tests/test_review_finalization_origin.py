"""Read-only reconciliation keeps all immutable original and target boundaries."""
from hashlib import sha256
from contextlib import closing
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.review_answer_fixture_support import review_fixture
from tests.test_review_continuation import A, B, ident
from tests.verification_receipt_test_support import add_receipt, completion
from task_governance_tool import review_continuation_repository as continuation
from task_governance_tool import storage
from task_governance_tool.review_finalization_repository import registered_originals
from task_governance_tool.review_wait_runtime.review_wait_basis import _FIELDS


class OriginalReconciliationTests(unittest.TestCase):
    def test_collected_source_survives_two_consumers_and_sealed_evidence(self):
        fx = self.enterContext(review_fixture(schema_version=31))
        fx.register_collected_source()
        verified = add_receipt(fx.db, fx.repo, fx.task_id, fx.basis["target_generation"])
        self.assertEqual(verified.returncode, 0, verified.stdout or verified.stderr)
        completed = completion(fx.db, fx.repo, fx.task_id)
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        with closing(storage.connect_initialized_readonly(fx.target)) as connection:
            bundle = connection.execute("SELECT continuation_basis_json FROM completion_evidence_bundles WHERE task_id=?",
                                        (fx.task_id,)).fetchone()
            self.assertIsNotNone(bundle["continuation_basis_json"])
            records = continuation.read_continuations(connection,
                project_id=fx.basis["project_id"], task_id=fx.task_id)
            current = records[-1]
            rows = connection.execute("""SELECT binding.*, judgment.continuation_id
                FROM review_receipt_sessions AS binding JOIN review_receipt_judgments AS judgment
                ON judgment.review_receipt_id=binding.review_receipt_id ORDER BY binding.session_id""").fetchall()
        self.assertEqual(len(records), 3)
        source_actor = records[0].selected[0].session_id
        source_receipt = next(row for row in rows if row["session_id"] == source_actor)
        self.assertEqual(source_receipt["continuation_id"], records[0].continuation_id)
        source = {**fx.basis, "parent_thread_id": fx.basis["parent_session_id"], "task_status": "in_progress"}
        record = SimpleNamespace(basis={key: source[key] for key in _FIELDS},
            continuation_id=current.continuation_id,
            active_sessions=tuple(choice.session_id for choice in current.selected),
            originals=tuple((row["session_id"], row["original_result_digest"]) for row in rows))
        receipt_ids = tuple(row["review_receipt_id"] for row in rows)
        before = fx.db.read_bytes()
        with mock.patch.object(continuation, "prepare_publications_locked", side_effect=AssertionError("read started writer")), \
             mock.patch.object(continuation, "require_current_basis", side_effect=AssertionError("read used writer admission")):
            self.assertEqual(registered_originals(fx.target, record, receipt_ids), receipt_ids)
            self.assertEqual(registered_originals(fx.target, record, receipt_ids), receipt_ids)
        self.assertEqual(fx.db.read_bytes(), before)

    def test_target_execution_and_original_controls_remain_query_only(self):
        fx = self.enterContext(review_fixture(schema_version=31))
        originals = [fx.original(A), fx.original(B)]
        result = fx.register(originals)
        receipt_ids = tuple(row["receipt"]["review_receipt_id"] for row in result["receipts"])
        source = {**fx.basis, "parent_thread_id": fx.basis["parent_session_id"], "task_status": "in_progress"}
        # Exercise the query against real native receipts; integrated snapshot
        # admission is covered separately by the installed end-to-end tests.
        record = SimpleNamespace(basis={key: source[key] for key in _FIELDS},
            continuation_id=None, originals=tuple(
                (actor, sha256(raw).hexdigest()) for actor, (raw, _) in zip((A, B), originals)))
        with mock.patch.object(continuation, "prepare_publications_locked", side_effect=AssertionError("read started writer")), \
             mock.patch.object(continuation, "require_current_basis", side_effect=AssertionError("read used writer admission")):
            self.assertEqual(registered_originals(fx.target, record, receipt_ids), receipt_ids)
            for key, value in (
                ("project_id", ident("tg_project_", 99)),
                ("task_id", ident("tg_task_", 99)),
                ("target_generation", 2), ("target_value", "sha256:" + "f" * 64),
                ("target_base_revision", "different-base"),
                ("execution_id", ident("tg_execution_", 99)),
            ):
                with self.subTest(field=key):
                    candidate = SimpleNamespace(**{**vars(record), "basis": {**record.basis, key: value}})
                    self.assertEqual(registered_originals(fx.target, candidate, receipt_ids), ())
            altered = SimpleNamespace(**{**vars(record), "originals": ((A, "f" * 64), (B, "e" * 64))})
            self.assertEqual(registered_originals(fx.target, altered, receipt_ids), ())
