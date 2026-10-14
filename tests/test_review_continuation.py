from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from task_governance_tool import review_continuation as c
from task_governance_tool.task_values import TaskValidationError


def ident(prefix: str, value: int = 1) -> str:
    return prefix + f"{value:016x}"


OWNER = "00000000-0000-4000-8000-000000000001"
A = "00000000-0000-4000-8000-000000000002"
B = "00000000-0000-4000-8000-000000000003"


def basis(**changes) -> dict:
    value = dict(version=1, project_id="test-project", task_id=ident("tg_task_"),
        contract_revision=1, authority_snapshot_id=ident("tg_authority_snapshot_"),
        acceptance_criterion_id=ident("tg_contract_criterion_"),
        verification_criterion_id=ident("tg_contract_criterion_", 2),
        artifact_manifest_id=ident("tg_artifact_manifest_"), review_tier=2,
        verification_requirement="required", verification_not_required_reason="",
        completion_commit_required=1, target_kind="git_snapshot", target_value="sha256:" + "a" * 64,
        target_base_revision="b" * 40, target_generation=1, project_path_hash="c" * 64,
        project_binding_generation=1, parent_session_id=OWNER,
        execution_id=ident("tg_execution_"), ownership_generation=1)
    value.update(changes)
    return value


def judgment(index=1, *, actor=A, key="a", verdict="pass", revision=1, prior=None, quality=None):
    return c.Judgment(ident("tg_review_receipt_", index), key, actor, revision, prior,
        ident("tg_review_continuation_") if revision > 1 else None,
        verdict, c.quality_identity(quality or basis()))


class ContinuationValueTests(unittest.TestCase):
    def test_closed_basis_and_hash_order(self):
        value = basis()
        self.assertEqual(c.basis_digest(value), c.basis_digest(dict(reversed(list(value.items())))))
        for changes in ({"version": True}, {"target_generation": True}, {"review_tier": 3},
                        {"project_path_hash": "not-a-digest"}, {"execution_id": None},
                        {"target_base_revision": ""}, {"verification_requirement": "not_required"}):
            with self.subTest(changes=changes), self.assertRaises(TaskValidationError):
                c.validate_basis(basis(**changes))
        with self.assertRaises(TaskValidationError):
            c.validate_basis({**value, "approval": True})

    def test_full_quality_conditions_and_distinct_owner_basis(self):
        original = basis()
        for field, value in (("contract_revision", 2), ("authority_snapshot_id", ident("tg_authority_snapshot_", 2)),
            ("artifact_manifest_id", ident("tg_artifact_manifest_", 2)), ("target_generation", 2),
            ("review_tier", 1)):
            changed = basis(**{field: value})
            with self.subTest(field=field):
                self.assertNotEqual(c.quality_identity(original), c.quality_identity(changed))
                self.assertNotEqual(c.basis_digest(original), c.basis_digest(changed))
        changed = basis(ownership_generation=2, execution_id=ident("tg_execution_", 2))
        self.assertEqual(c.quality_identity(original), c.quality_identity(changed))
        self.assertNotEqual(c.basis_digest(original), c.basis_digest(changed))
        changed = basis(completion_commit_required=0)
        self.assertEqual(c.quality_identity(original), c.quality_identity(changed))
        self.assertNotEqual(c.basis_digest(original), c.basis_digest(changed))

    def test_selection_sorted_and_closed(self):
        rows = [dict(session_id=B, action="supplement", prior_receipt_id=None, reason="不足資料の補完"),
                dict(session_id=A, action="reassess", prior_receipt_id=ident("tg_review_receipt_"), reason="指摘根拠の再確認")]
        selected = c.decode_selection(json.dumps(dict(version=1, reviewers=rows)).encode())
        self.assertEqual([row.session_id for row in selected], [A, B])
        self.assertEqual(c.selection_digest(selected), c.selection_digest(tuple(reversed(selected))))
        self.assertEqual(selected[0].reason, "指摘根拠の再確認")

    def test_selection_rejects_duplicate_unknown_and_invalid_original_identity(self):
        row = dict(session_id=A, action="supplement", prior_receipt_id=None, reason="needed")
        bad = [dict(version=1, reviewers=[row, row]), dict(version=True, reviewers=[row]),
            dict(version=1, reviewers=[]), dict(version=1, reviewers=[{**row, "turn_id": A}]),
            dict(version=1, reviewers=[{**row, "prior_receipt_id": ident("tg_review_receipt_")}]),
            dict(version=1, reviewers=[{**row, "action": "reassess"}]),
            dict(version=1, reviewers=[{**row, "session_id": "alias-a"}])]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(TaskValidationError):
                c.validate_selection(value)
        with self.assertRaises(TaskValidationError):
            c.decode_selection(b'{"version":1,"version":1,"reviewers":[]}')
        with self.assertRaises(TaskValidationError):
            c.decode_selection(b" " * (c.INPUT_LIMIT + 1))

    def test_reason_privacy_and_boundary(self):
        c.SelectedReviewer(A, "supplement", None, "a" * 1000)
        for value in ("a" * 1001, "", "api_key=secret-value", "private prompt: example"):
            with self.subTest(value=value[:30]), self.assertRaises(TaskValidationError):
                c.SelectedReviewer(A, "supplement", None, value)


class EffectiveJudgmentTests(unittest.TestCase):
    def select(self, *rows, pending=(), quality=None):
        return c.select_effective(rows, quality_basis=c.quality_identity(quality or basis()), pending=pending)

    def test_all_pass_reused_without_new_revision_or_pending(self):
        first, second = judgment(), judgment(2, actor=B, key="b")
        selected = self.select(second, first)
        self.assertEqual(selected.active, (first, second))
        self.assertEqual(selected.pending_sessions, ())
        self.assertEqual(first.revision, 1)

    def test_reassessment_preserves_original_ng_and_uses_explicit_successor(self):
        old = judgment(verdict="changes_requested")
        new = judgment(3, revision=2, prior=old.receipt_id)
        retained = judgment(2, actor=B, key="b")
        self.assertEqual(self.select(new, old, retained).active, (new, retained))
        self.assertEqual(old.verdict, "changes_requested")

    def test_later_ng_supersedes_old_pass(self):
        old = judgment()
        new = judgment(3, revision=2, prior=old.receipt_id, verdict="changes_requested")
        self.assertEqual(self.select(old, new).active, (new,))

    def test_pending_selected_result_suspended_other_pass_retained(self):
        first, second = judgment(), judgment(2, actor=B, key="b")
        pending = (c.SelectedReviewer(B, "reassess", second.receipt_id, "reconsider"),)
        selected = self.select(first, second, pending=pending)
        self.assertEqual(selected.heads, (first, second))
        self.assertEqual(selected.active, (first,))
        self.assertEqual(selected.pending_sessions, (B,))

    def test_unjudged_supplement_retains_other_result(self):
        first = judgment()
        selected = self.select(first, pending=(c.SelectedReviewer(B, "supplement", None, "supply"),))
        self.assertEqual(selected.active, (first,))
        self.assertEqual(selected.pending_sessions, (B,))

    def test_registered_judgment_cannot_be_hidden_as_unjudged(self):
        with self.assertRaises(TaskValidationError):
            self.select(judgment(), pending=(c.SelectedReviewer(A, "supplement", None, "supply"),))

    def test_stale_expected_head_rejected(self):
        old = judgment()
        new = judgment(3, revision=2, prior=old.receipt_id)
        with self.assertRaises(TaskValidationError):
            self.select(old, new, pending=(c.SelectedReviewer(A, "reassess", old.receipt_id, "stale"),))

    def test_same_bytes_changed_basis_not_reused(self):
        old = judgment()
        for changed in (basis(target_generation=2), basis(contract_revision=2), basis(review_tier=1)):
            with self.subTest(changed=changed):
                self.assertEqual(self.select(old, quality=changed).active, ())

    def test_branch_cycle_dangling_and_revision_gap_rejected(self):
        old = judgment()
        second = judgment(2, revision=2, prior=old.receipt_id)
        third = judgment(3, revision=2, prior=old.receipt_id)
        for rows in ((old, second, third), (second,), (old, replace(second, revision=3)),
                     (replace(second, prior_receipt_id=second.receipt_id),)):
            with self.subTest(rows=rows), self.assertRaises(TaskValidationError):
                self.select(*rows)

    def test_successor_cannot_change_session_alias_or_conditions(self):
        old = judgment()
        new = judgment(2, revision=2, prior=old.receipt_id)
        for changed in (replace(new, session_id=B), replace(new, reviewer_key="alias"),
                        replace(new, quality_basis=c.quality_identity(basis(contract_revision=2)))):
            with self.subTest(changed=changed), self.assertRaises(TaskValidationError):
                self.select(old, changed)

    def test_unbound_legacy_keeps_ordinary_gate_but_cannot_reassess(self):
        old = judgment(actor=None)
        self.assertEqual(self.select(old).active, (old,))
        with self.assertRaises(TaskValidationError):
            self.select(old, judgment(2, revision=2, prior=old.receipt_id))

    def test_one_actor_under_two_keys_never_counts_twice(self):
        with self.assertRaises(TaskValidationError):
            self.select(judgment(), judgment(2, key="alias"))

    def test_malformed_noncurrent_graph_not_ignored(self):
        current = judgment()
        foreign = judgment(2, revision=2, prior=ident("tg_review_receipt_", 999), quality=basis(target_generation=2))
        with self.assertRaises(TaskValidationError):
            self.select(current, foreign)

    def test_multiple_revisions_one_effective_reviewer(self):
        old = judgment(verdict="changes_requested")
        second = judgment(2, revision=2, prior=old.receipt_id, verdict="changes_requested")
        third = judgment(3, revision=3, prior=second.receipt_id)
        selected = self.select(third, old, second)
        self.assertEqual(selected.active, (third,))


if __name__ == "__main__":
    unittest.main()
