import sqlite3
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock


SCRIPTS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "task-governance-tool"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.completion import (  # noqa: E402
    CompletionEvidenceError,
    CompletionRequest,
    resolve_completion_request,
)
from task_governance_tool.reviews import (  # noqa: E402
    ReviewEvidenceError,
    enforce_review_gate,
    first_review_gate_error,
)


FULL_COMMIT = "a" * 40


def evidence_task(
    *,
    kind="none",
    revision="",
    reason="",
    approved=0,
    required=1,
    legacy_hash="",
):
    return {
        "completion_evidence_kind": kind,
        "completion_evidence_revision": revision,
        "completion_evidence_reason": reason,
        "external_revision_approved": approved,
        "completion_commit_required": required,
        "completion_commit_hash": legacy_hash,
    }


def review_evidence(
    *,
    target=True,
    blocking=False,
    changes_requested=0,
    satisfied=True,
):
    return {
        "target": {
            "kind": "diff_fingerprint" if target else "",
            "value": "sha256:" + ("b" * 64) if target else "",
            "generation": 1 if target else 0,
        },
        "gate": {"satisfied": satisfied},
        "counts": {
            "changes_requested_current_generation": changes_requested,
        },
        "blocking_findings": (
            [{"review_finding_id": "tg_finding_example"}]
            if blocking
            else []
        ),
    }


class CompletionRequestTests(unittest.TestCase):
    def test_request_and_resolution_are_frozen_and_existing_evidence_is_reused(self):
        request = CompletionRequest(
            task_id="tg_task_example",
            verification_complete=True,
            review_complete=True,
        )
        with self.assertRaises(FrozenInstanceError):
            request.task_id = "changed"

        existing = evidence_task(
            kind="git_commit",
            revision=FULL_COMMIT,
            required=1,
            legacy_hash=FULL_COMMIT,
        )
        with mock.patch(
            "task_governance_tool.completion.resolve_git_commit",
            side_effect=AssertionError("existing evidence must not be resolved here"),
        ):
            resolved = resolve_completion_request(
                repo=Path("unused"),
                request=request,
                existing_task=existing,
            )

        self.assertEqual(resolved.completion_evidence_kind, "git_commit")
        self.assertEqual(resolved.completion_evidence_revision, FULL_COMMIT)
        self.assertIsNone(resolved.audit_marker)
        with self.assertRaises(FrozenInstanceError):
            resolved.completion_evidence_kind = "commit_not_required"

    def test_typed_git_input_is_canonicalized_without_retaining_raw_revision(self):
        request = CompletionRequest(
            task_id="tg_task_example",
            verification_complete=True,
            review_complete=True,
            completion_evidence_kind="git_commit",
            completion_revision="  feature/reviewed-state  ",
        )
        self.assertNotIn("feature/reviewed-state", repr(request))
        with mock.patch(
            "task_governance_tool.completion.resolve_git_commit",
            return_value=FULL_COMMIT,
        ) as resolve:
            resolved = resolve_completion_request(
                repo=Path("repo"),
                request=request,
                existing_task=evidence_task(),
            )

        resolve.assert_called_once_with(Path("repo"), "feature/reviewed-state")
        self.assertEqual(resolved.completion_evidence_revision, FULL_COMMIT)
        self.assertEqual(resolved.completion_commit_hash, FULL_COMMIT)
        self.assertNotIn("feature/reviewed-state", repr(resolved))
        self.assertEqual(resolved.audit_marker, "Git completion commit verified")

    def test_external_and_commit_not_required_inputs_use_existing_matrix_rules(self):
        external = resolve_completion_request(
            repo=Path("unused"),
            request=CompletionRequest(
                task_id="tg_task_example",
                verification_complete=True,
                review_complete=True,
                completion_evidence_kind="external_revision",
                completion_revision="  release-42  ",
                completion_evidence_reason="  approved durable archive  ",
                external_revision_approved=True,
            ),
            existing_task=evidence_task(),
        )
        self.assertEqual(external.completion_evidence_revision, "release-42")
        self.assertEqual(
            external.completion_evidence_reason,
            "approved durable archive",
        )
        self.assertEqual(external.external_revision_approved, 1)
        self.assertEqual(external.completion_commit_hash, "release-42")

        not_required = resolve_completion_request(
            repo=Path("unused"),
            request=CompletionRequest(
                task_id="tg_task_example",
                verification_complete=True,
                review_complete=True,
                completion_evidence_kind="commit_not_required",
            ),
            existing_task=external.to_task_values(),
        )
        self.assertEqual(
            not_required.to_task_values(),
            evidence_task(
                kind="commit_not_required",
                required=0,
            ),
        )

    def test_missing_kind_and_invalid_existing_matrix_fail_without_raw_values(self):
        raw_value = "secret-caller-revision"
        with self.assertRaises(CompletionEvidenceError) as missing_kind:
            resolve_completion_request(
                repo=Path("unused"),
                request=CompletionRequest(
                    task_id="tg_task_example",
                    verification_complete=True,
                    review_complete=True,
                    completion_revision=raw_value,
                ),
                existing_task=evidence_task(),
            )
        self.assertEqual(
            missing_kind.exception.code,
            "completion_evidence_conflict",
        )
        self.assertNotIn(raw_value, str(missing_kind.exception))

        with self.assertRaises(CompletionEvidenceError) as invalid_existing:
            resolve_completion_request(
                repo=Path("unused"),
                request=CompletionRequest(
                    task_id="tg_task_example",
                    verification_complete=True,
                    review_complete=True,
                ),
                existing_task=evidence_task(
                    kind="legacy_unverified",
                    revision=raw_value,
                    legacy_hash=raw_value,
                ),
            )
        self.assertEqual(
            invalid_existing.exception.code,
            "completion_evidence_conflict",
        )
        self.assertNotIn(raw_value, str(invalid_existing.exception))

    def test_resolution_returns_a_fresh_task_value_mapping(self):
        resolved = resolve_completion_request(
            repo=Path("unused"),
            request=CompletionRequest(
                task_id="tg_task_example",
                verification_complete=True,
                review_complete=True,
                completion_evidence_kind="commit_not_required",
            ),
            existing_task=evidence_task(),
        )
        values = resolved.to_task_values()
        values["completion_evidence_kind"] = "changed"
        self.assertEqual(
            resolved.completion_evidence_kind,
            "commit_not_required",
        )


class ReviewGatePureHelperTests(unittest.TestCase):
    def test_first_error_order_matches_existing_review_gate(self):
        target_error = first_review_gate_error(
            review_evidence(
                target=False,
                blocking=True,
                changes_requested=1,
                satisfied=False,
            )
        )
        self.assertEqual(target_error.code, "review_target_required")

        finding_error = first_review_gate_error(
            review_evidence(
                blocking=True,
                changes_requested=1,
                satisfied=False,
            )
        )
        self.assertEqual(finding_error.code, "review_finding_unresolved")

        changes_error = first_review_gate_error(
            review_evidence(changes_requested=1, satisfied=False)
        )
        self.assertEqual(changes_error.code, "review_changes_requested")

        receipt_error = first_review_gate_error(
            review_evidence(satisfied=False)
        )
        self.assertEqual(receipt_error.code, "review_receipts_insufficient")
        self.assertIsNone(first_review_gate_error(review_evidence()))

    def test_enforce_review_gate_delegates_to_pure_helper(self):
        evidence = review_evidence()
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        with mock.patch(
            "task_governance_tool.reviews.read_review_evidence",
            return_value=evidence,
        ) as read, mock.patch(
            "task_governance_tool.reviews.first_review_gate_error",
            return_value=None,
        ) as first_error:
            result = enforce_review_gate(
                connection,
                project_id="project",
                task_id="task",
                review_tier=2,
            )

        self.assertIs(result, evidence)
        read.assert_called_once_with(
            connection,
            "project",
            "task",
            review_tier=2,
        )
        first_error.assert_called_once_with(evidence)

    def test_enforce_review_gate_raises_the_pure_helper_error(self):
        evidence = review_evidence(satisfied=False)
        expected = ReviewEvidenceError(
            "review_receipts_insufficient",
            "fixed message",
            "review_receipt",
        )
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        with mock.patch(
            "task_governance_tool.reviews.read_review_evidence",
            return_value=evidence,
        ), mock.patch(
            "task_governance_tool.reviews.first_review_gate_error",
            return_value=expected,
        ):
            with self.assertRaises(ReviewEvidenceError) as raised:
                enforce_review_gate(
                    connection,
                    project_id="project",
                    task_id="task",
                    review_tier=2,
                )
        self.assertIs(raised.exception, expected)


if __name__ == "__main__":
    unittest.main()
