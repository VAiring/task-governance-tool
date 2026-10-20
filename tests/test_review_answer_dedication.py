"""Closed declarations, historical origin and current key eligibility."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import unittest

from tests.test_review_answer_continuation_preparation import fixture
from tests.test_review_answer_pure_values import ident, A
from tests.test_review_continuation import B
from task_governance_tool import review_answer_dedication as dedication
from task_governance_tool import review_continuation as continuation
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.task_values import TaskValidationError
from task_governance_tool.review_wait_runtime.request_continuation import encode, decode
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError


class LegacyDedicationTests(unittest.TestCase):
    def setUp(self):
        self.root, self.native, self.round = fixture()
        self.root.version = 1
        self.native.preparation_id = self.root.preparation_id
        self.state = SimpleNamespace(records=(), heads=())

    def initial(self):
        declaration = dedication.issue(self.root, (), self.state, self.native.selected,
                                       self.native.continuation_id, explicit=True)
        self.round = replace(self.round, answer_protocol="review-answer-v1",
                             legacy_migration=declaration)
        self.state.records = (self.native,)
        return declaration

    def test_declaration_is_explicit_and_native_absence_has_no_fallback(self):
        with self.assertRaises(TaskValidationError):
            dedication.issue(self.root, (), self.state, self.native.selected,
                             self.native.continuation_id)
        self.root.version = 2
        self.assertIsNone(dedication.issue(self.root, (), self.state, self.native.selected,
                                          self.native.continuation_id))
        with self.assertRaises(TaskValidationError):
            dedication.issue(self.root, (), self.state, self.native.selected,
                             self.native.continuation_id, explicit=True)

    def test_round_encoding_preserves_old_bytes_and_rejects_noncanonical_migration(self):
        old = encode(self.round)
        self.assertNotIn("legacy_migration", json.loads(old))
        self.assertEqual(encode(decode(old)), old)
        self.initial()
        self.assertEqual(decode(encode(self.round)), self.round)
        value = json.loads(old)
        value["legacy_migration"] = None
        with self.assertRaises(RepositoryError):
            decode(json.dumps(value, sort_keys=True, separators=(",", ":")))
        with self.assertRaises(RepositoryError):
            replace(self.round, answer_protocol="legacy")

    def test_later_round_reuses_same_origin_and_key_without_explicit_flag(self):
        first = self.initial()
        next_id = ident("tg_review_continuation_", 9)
        result = dedication.issue(self.root, (self.round,), self.state,
                                  self.native.selected, next_id)
        self.assertEqual(result, first)
        self.assertEqual(dedication.current(self.root, (self.round,), self.state, self.round),
                         dedication.decode(first))

    def test_migration_keeps_exact_registered_predecessor_key(self):
        head = SimpleNamespace(session_id=A, reviewer_key="legacy-key",
                               receipt_id=ident("tg_review_receipt_", 7))
        self.state.heads = (head,)
        selected = (continuation.SelectedReviewer(A, "reassess", head.receipt_id, "Reassess"),)
        result = dedication.issue(self.root, (), self.state, selected,
                                  self.native.continuation_id, explicit=True)
        self.assertEqual(result["assignments"][0]["reviewer_key"], "legacy-key")
        wrong = (continuation.SelectedReviewer(A, "reassess", ident("tg_review_receipt_", 8), "Reassess"),)
        with self.assertRaises(TaskValidationError):
            dedication.issue(self.root, (), self.state, wrong,
                             self.native.continuation_id, explicit=True)

    def test_origin_basis_selection_and_registered_key_conflicts_reject(self):
        self.initial()
        for corruption in ("origin", "basis", "selection", "head"):
            with self.subTest(corruption=corruption):
                round_record = self.round
                state = deepcopy(self.state)
                if corruption == "origin":
                    value = deepcopy(round_record.legacy_migration)
                    value["assignments"][0]["origin_continuation_id"] = ident("tg_review_continuation_", 9)
                    round_record = replace(round_record, legacy_migration=value)
                elif corruption == "basis":
                    round_record = replace(round_record, basis_digest="f" * 64)
                elif corruption == "selection":
                    round_record = replace(round_record, selection_digest="f" * 64)
                else:
                    state.heads = (SimpleNamespace(session_id=A, reviewer_key="another-key"),)
                with self.assertRaises(TaskValidationError):
                    dedication.current(self.root, (round_record,), state, round_record)

    def test_saved_original_key_conflict_cannot_be_hidden_by_precedence(self):
        self.state.heads = (SimpleNamespace(session_id=A, reviewer_key="registered-key",
                                           receipt_id=ident("tg_review_receipt_", 7)),)
        source = SimpleNamespace(bound=SimpleNamespace(binding=SimpleNamespace(session_id=A)),
                                 reviewer_key="changed-original-key")
        with self.assertRaises(TaskValidationError):
            dedication.issue(self.root, (), self.state, self.native.selected,
                             self.native.continuation_id, explicit=True, sources=(source,))
        self.state.heads = (SimpleNamespace(session_id=B, reviewer_key=A),)
        with self.assertRaises(TaskValidationError):
            dedication.issue(self.root, (), self.state, self.native.selected,
                             self.native.continuation_id, explicit=True)

    def test_declaration_is_closed_bounded_and_session_unique(self):
        value = self.initial()
        for corrupt in ("extra", "empty", "duplicate", "future_version"):
            changed = deepcopy(value)
            if corrupt == "extra":
                changed["assignments"][0]["dispatch_id"] = "invented"
            elif corrupt == "empty":
                changed["assignments"] = []
            elif corrupt == "duplicate":
                changed["assignments"] *= 2
            else:
                changed["version"] = 2
            with self.subTest(corruption=corrupt), self.assertRaises(AnswerError):
                dedication.decode(changed)
