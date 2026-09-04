from __future__ import annotations

import io
import sys
import traceback
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "task-governance-tool"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import storage as storage_module  # noqa: E402
from task_governance_tool import tasks as tasks_module  # noqa: E402
from task_governance_tool.artifact_lock import ArtifactLockError  # noqa: E402
from task_governance_tool.artifact_manifest import (  # noqa: E402
    ArtifactManifestError,
)
from task_governance_tool.completion import CompletionEvidenceError  # noqa: E402
from task_governance_tool.evidence_ledger import EvidenceLedgerError  # noqa: E402
from task_governance_tool.evidence_projection import (  # noqa: E402
    EvidenceProjectionError,
)
from task_governance_tool.git_snapshot import GitSnapshotError  # noqa: E402
from task_governance_tool.handoffs import HandoffError  # noqa: E402
from task_governance_tool.review_packet import ReviewPacketError  # noqa: E402
from task_governance_tool.review_provenance import (  # noqa: E402
    INVALID_REVIEW_PROVENANCE_MESSAGE,
    ReviewProvenanceError,
)
from task_governance_tool.tasks import (  # noqa: E402
    TaskRepositoryError,
    TaskValidationError,
)
from task_governance_tool.verification_receipts import (  # noqa: E402
    VerificationReceiptError,
)
from task_governance_tool.verification_runner_git import (  # noqa: E402
    TARGET_ERROR_MESSAGE,
    VerificationRunnerGitError,
)
from task_governance_tool.verification_runner_runtime import (  # noqa: E402
    VerificationRunnerRuntimeError,
)
from task_governance_tool.viewer import ViewerError  # noqa: E402
from tests.evidence_reader_codec import EvidenceCodecError  # noqa: E402
from tests.evidence_reader_oracle import EvidenceConsumerError  # noqa: E402


class Python314ExceptionReportingTests(unittest.TestCase):
    def test_task_and_review_errors_are_traceback_compatible(self):
        cases = (
            (
                lambda: TaskValidationError("privacy_rejected", "task invalid"),
                {
                    "code": "privacy_rejected",
                    "message": "task invalid",
                    "field": None,
                },
            ),
            (
                lambda: TaskRepositoryError("not_found", "task missing"),
                {"code": "not_found", "message": "task missing"},
            ),
            (
                lambda: CompletionEvidenceError(
                    "invalid_argument", "completion invalid", "revision"
                ),
                {
                    "code": "invalid_argument",
                    "message": "completion invalid",
                    "field": "revision",
                },
            ),
            (
                lambda: HandoffError("not_found", "handoff missing"),
                {"code": "not_found", "message": "handoff missing"},
            ),
            (
                lambda: ReviewPacketError("review_packet_stale", "packet stale"),
                {"code": "review_packet_stale", "message": "packet stale"},
            ),
            (
                ReviewProvenanceError,
                {
                    "code": "invalid_review_evidence",
                    "message": INVALID_REVIEW_PROVENANCE_MESSAGE,
                    "field": "review_provenance",
                },
            ),
            (
                lambda: VerificationReceiptError(
                    "invalid_verification_evidence", "receipt invalid"
                ),
                {
                    "code": "invalid_verification_evidence",
                    "message": "receipt invalid",
                    "field": None,
                },
            ),
        )

        for factory, expected_fields in cases:
            with self.subTest(error_type=type(factory()).__name__):
                error = factory()
                duplicate = factory()
                self.assertIsInstance(error, Exception)
                self.assertEqual(error, duplicate)
                self.assertIsNone(type(error).__hash__)
                for field, expected in expected_fields.items():
                    self.assertEqual(getattr(error, field), expected)
                self.assertEqual(str(error), expected_fields["message"])

                try:
                    raise error
                except Exception as caught:
                    attached_traceback = caught.__traceback__
                    rendered = "".join(traceback.format_exception(caught))
                    caught.__traceback__ = attached_traceback
                    self.assertIs(caught.__traceback__, attached_traceback)
                    self.assertIn(type(error).__name__, rendered)
                    self.assertIn(str(error), rendered)

    def test_evidence_and_artifact_errors_report_chains_and_continue(self):
        cases = (
            (
                lambda: ArtifactLockError(contended=True),
                {"contended": True},
                "",
            ),
            (
                lambda: ArtifactManifestError("artifact_manifest_stale", "stale"),
                {"code": "artifact_manifest_stale", "message": "stale"},
                "stale",
            ),
            (
                lambda: EvidenceLedgerError(
                    "evidence_ledger_inconsistent", "ledger inconsistent"
                ),
                {
                    "code": "evidence_ledger_inconsistent",
                    "message": "ledger inconsistent",
                },
                "ledger inconsistent",
            ),
            (
                lambda: EvidenceProjectionError(
                    "evidence_bundle_too_large", "bundle too large"
                ),
                {
                    "code": "evidence_bundle_too_large",
                    "message": "bundle too large",
                },
                "bundle too large",
            ),
            (
                lambda: ViewerError("project_state_unreadable", "viewer invalid"),
                {
                    "code": "project_state_unreadable",
                    "message": "viewer invalid",
                },
                "viewer invalid",
            ),
            (
                EvidenceConsumerError,
                {
                    "code": "source_invalid",
                    "message": "Evidence source is invalid",
                },
                "Evidence source is invalid",
            ),
            (
                EvidenceCodecError,
                {
                    "code": "evidence_codec_invalid",
                    "message": "Evidence JSON is invalid",
                },
                "Evidence JSON is invalid",
            ),
        )

        for factory, expected_fields, expected_string in cases:
            with self.subTest(error_type=type(factory()).__name__):
                error = factory()
                self.assertIsInstance(error, Exception)
                if type(error) in {EvidenceConsumerError, EvidenceCodecError}:
                    self.assertIsInstance(error, ValueError)
                self.assertEqual(error, factory())
                self.assertIsNone(type(error).__hash__)
                for field, expected in expected_fields.items():
                    self.assertEqual(getattr(error, field), expected)
                self.assertEqual(str(error), expected_string)

                try:
                    raise error
                except Exception as caught:
                    attached_traceback = caught.__traceback__
                    rendered = "".join(traceback.format_exception(caught))
                    caught.__traceback__ = attached_traceback
                    self.assertIs(caught.__traceback__, attached_traceback)
                    self.assertIn(type(error).__name__, rendered)
                    if expected_string:
                        self.assertIn(expected_string, rendered)

                continued: list[bool] = []
                chained_error = factory()

                class ChainedFailure(unittest.TestCase):
                    def runTest(self):
                        try:
                            raise RuntimeError("synthetic cause")
                        except RuntimeError as cause:
                            raise chained_error from cause

                class FollowingTest(unittest.TestCase):
                    def runTest(self):
                        continued.append(True)

                stream = io.StringIO()
                result = unittest.TextTestRunner(stream=stream, verbosity=0).run(
                    unittest.TestSuite((ChainedFailure(), FollowingTest()))
                )
                report = stream.getvalue()
                self.assertEqual(result.testsRun, 2)
                self.assertEqual(len(result.errors), 1)
                self.assertEqual(result.failures, [])
                self.assertFalse(result.wasSuccessful())
                self.assertEqual(continued, [True])
                self.assertIn("synthetic cause", report)
                self.assertIn(type(chained_error).__name__, report)
                if expected_string:
                    self.assertIn(expected_string, report)
                self.assertNotIn("FrozenInstanceError", report)

    def test_git_and_runner_errors_report_chains_and_continue(self):
        cases = (
            (
                lambda: GitSnapshotError(
                    "git_commit_not_found_or_ambiguous",
                    "revision is invalid",
                    "revision",
                ),
                {
                    "code": "git_commit_not_found_or_ambiguous",
                    "message": "revision is invalid",
                    "field": "revision",
                },
                "revision is invalid",
            ),
            (
                lambda: VerificationRunnerGitError(code="target_invalid"),
                {
                    "code": "target_invalid",
                    "message": TARGET_ERROR_MESSAGE,
                },
                TARGET_ERROR_MESSAGE,
            ),
            (
                lambda: VerificationRunnerRuntimeError(
                    "runtime_unavailable",
                    "runner runtime is unavailable",
                ),
                {
                    "code": "runtime_unavailable",
                    "message": "runner runtime is unavailable",
                    "handle_cleanup_state": "uncertain",
                },
                "runner runtime is unavailable",
            ),
        )

        for factory, expected_fields, expected_string in cases:
            with self.subTest(error_type=type(factory()).__name__):
                error = factory()
                self.assertIsInstance(error, Exception)
                self.assertEqual(error, factory())
                self.assertIsNone(type(error).__hash__)
                for field, expected in expected_fields.items():
                    self.assertEqual(getattr(error, field), expected)
                self.assertEqual(str(error), expected_string)

                try:
                    raise error
                except Exception as caught:
                    attached_traceback = caught.__traceback__
                    rendered = "".join(traceback.format_exception(caught))
                    caught.__traceback__ = attached_traceback
                    self.assertIs(caught.__traceback__, attached_traceback)
                    self.assertIn(type(error).__name__, rendered)
                    self.assertIn(expected_string, rendered)

                continued: list[bool] = []
                chained_error = factory()

                class ChainedFailure(unittest.TestCase):
                    def runTest(self):
                        try:
                            raise RuntimeError("synthetic cause")
                        except RuntimeError as cause:
                            raise chained_error from cause

                class FollowingTest(unittest.TestCase):
                    def runTest(self):
                        continued.append(True)

                stream = io.StringIO()
                result = unittest.TextTestRunner(stream=stream, verbosity=0).run(
                    unittest.TestSuite((ChainedFailure(), FollowingTest()))
                )
                report = stream.getvalue()
                self.assertEqual(result.testsRun, 2)
                self.assertEqual(len(result.errors), 1)
                self.assertEqual(result.failures, [])
                self.assertFalse(result.wasSuccessful())
                self.assertEqual(continued, [True])
                self.assertIn("synthetic cause", report)
                self.assertIn(type(chained_error).__name__, report)
                self.assertIn(expected_string, report)
                self.assertNotIn("FrozenInstanceError", report)

        uncertain = VerificationRunnerRuntimeError("runtime_unavailable", "failure")
        closed = VerificationRunnerRuntimeError(
            "runtime_unavailable",
            "failure",
            handle_cleanup_state="closed",
        )
        open_error = VerificationRunnerRuntimeError(
            "runtime_unavailable",
            "failure",
            handle_cleanup_state="open",
        )
        self.assertFalse(uncertain.handles_closed)
        self.assertTrue(closed.handles_closed)
        self.assertFalse(open_error.handles_closed)

    def test_storage_privacy_failure_is_reported_and_unittest_continues(self):
        continued: list[bool] = []
        injected = TaskValidationError(
            code="privacy_rejected",
            message="stored validation rejected safely",
            field="description",
        )

        class StorageFailure(unittest.TestCase):
            def runTest(self):
                with mock.patch.object(
                    tasks_module,
                    "reject_private_or_raw_content",
                    side_effect=injected,
                ):
                    storage_module._validate_evidence_ledger_stored_privacy(
                        "description",
                        "benign stored text",
                        privacy_success_cache=set(),
                    )

        class FollowingTest(unittest.TestCase):
            def runTest(self):
                continued.append(True)

        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=0).run(
            unittest.TestSuite((StorageFailure(), FollowingTest()))
        )
        report = stream.getvalue()

        self.assertEqual(result.testsRun, 2)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.failures, [])
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(continued, [True])
        self.assertIn(type(injected).__name__, report)
        self.assertIn(str(injected), report)
        self.assertIn(
            str(storage_module.evidence_ledger_inconsistent()),
            report,
        )
        self.assertNotIn("FrozenInstanceError", report)


if __name__ == "__main__":
    unittest.main()
