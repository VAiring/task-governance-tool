from __future__ import annotations

import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.m23_test_support import write_evidence_tree


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402
from task_governance_tool import analysis_worker  # noqa: E402
from task_governance_tool._analysis_windows_process import MockScenario  # noqa: E402
from task_governance_tool.analysis_contracts import default_recipe  # noqa: E402
from task_governance_tool.analysis_outbox import (  # noqa: E402
    AnalysisOutboxSession,
    PublicationResult,
    enqueue_analysis_source,
    replace_analysis_status,
)
from task_governance_tool.analysis_packet import (  # noqa: E402
    AnalysisPacketError,
    build_analysis_packet,
)
from task_governance_tool.analysis_validator import build_analysis_report  # noqa: E402
from task_governance_tool.analysis_worker import run_once  # noqa: E402
from task_governance_tool.codex_analysis_adapter import ClosedMockPlan  # noqa: E402
from task_governance_tool.evidence_consumer import (  # noqa: E402
    EvidenceConsumerError,
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.state_paths import analysis_state_paths  # noqa: E402


@unittest.skipUnless(os.name == "nt", "TG-M23.2 worker is Windows-only")
class AnalysisWorkerTests(unittest.TestCase):
    def _fixture(self, root: Path, *, optional: bool = False):
        fixed = root / "fixed"
        fixed.mkdir()
        paths = analysis_state_paths(fixed)
        index = read_evidence_index(write_evidence_tree(root / "evidence"))
        entry = next(
            item for item in index.entries if item["bundle_state"] == "legacy_unknown"
        )
        source = validate_evidence_source(index, entry)
        queued = enqueue_analysis_source(
            paths=paths,
            source=source,
            recipe=default_recipe(
                inference_mode="codex_optional" if optional else "offline",
                declared_model_id="fixture-model" if optional else None,
            ),
        )
        return paths, index, source, queued

    @staticmethod
    def _final_files(paths):
        return (
            sorted(item.name for item in paths.reports.iterdir()),
            sorted(item.name for item in paths.rendered.iterdir()),
            sorted(item.name for item in paths.temporary.iterdir()),
        )

    @staticmethod
    def _force_release_retained_session(session) -> None:
        """Test-only cleanup after proving deliberate fail-fast retention."""

        if session._directories is not None:
            session._directories.close()
            session._directories = None
        lease = session._lease
        if not lease._byte_lock.released:
            win32_boundary.unlock_byte_zero(lease._byte_lock)
        for handle in (
            lease._lock_handle,
            lease._root_handle,
            lease._parent_handle,
        ):
            if not handle.closed:
                handle.close()
        if not lease._security.closed:
            lease._security.close()

    def test_offline_run_once_publishes_exactly_one_job_then_idles(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, _source, queued = self._fixture(Path(temporary))
            result = run_once(paths, index)

            self.assertEqual(result.disposition, "published")
            self.assertEqual(result.analysis_job_id, queued.descriptor["analysis_job_id"])
            self.assertEqual(result.status["state"], "published")
            self.assertEqual(result.status["worker_attempt_count"], 1)
            self.assertEqual(result.status["adapter_attempt_count"], 0)
            self.assertEqual(result.status["inference_state"], "disabled")
            self.assertTrue(all(result.status[name] for name in (
                "report_id", "report_digest", "render_digest"
            )))
            reports, rendered, private = self._final_files(paths)
            self.assertEqual(reports, [result.status["report_id"] + ".json"])
            self.assertEqual(rendered, [result.status["report_id"] + ".md"])
            self.assertEqual(private, [])
            self.assertEqual(run_once(paths, index).disposition, "idle")

    def test_optional_without_mock_is_counter_zero_policy_blocked_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, _source, _queued = self._fixture(
                Path(temporary), optional=True
            )
            result = run_once(paths, index)

            self.assertEqual(result.disposition, "published")
            self.assertEqual(result.status["adapter_attempt_count"], 0)
            self.assertEqual(result.status["inference_state"], "policy_blocked")
            self.assertEqual(self._final_files(paths)[2], [])

    def test_source_failure_is_terminal_before_packet_or_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, _source, queued = self._fixture(Path(temporary))
            with patch(
                "task_governance_tool.analysis_worker.revalidate_descriptor_source",
                side_effect=EvidenceConsumerError(),
            ):
                result = run_once(paths, index)

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["fixed_code"], "source_invalid")
            self.assertEqual(result.status["worker_attempt_count"], 1)
            self.assertIsNone(result.status["packet_digest"])
            self.assertEqual(self._final_files(paths), ([], [], []))
            audit = AnalysisOutboxSession.acquire(paths)
            try:
                self.assertEqual(
                    audit.read_bound_job(queued.descriptor).status,
                    result.status,
                )
            finally:
                audit.release_normal()

    def test_offline_reclaim_is_counter_only_then_interrupted(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, source, queued = self._fixture(Path(temporary))
            packet = build_analysis_packet(queued.descriptor, source)
            running = deepcopy(queued.status)
            running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": packet.packet_digest,
                }
            )
            replace_analysis_status(
                paths=paths,
                descriptor=queued.descriptor,
                expected_status=queued.status,
                status=running,
            )

            with patch(
                "task_governance_tool.analysis_worker.revalidate_descriptor_source"
            ) as source_read:
                result = run_once(paths, index)

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["worker_attempt_count"], 2)
            self.assertEqual(result.status["fixed_code"], "interrupted")
            self.assertEqual(result.status["duration_ms"], 0)
            self.assertEqual(source_read.call_count, 0)
            self.assertEqual(self._final_files(paths), ([], [], []))

    def test_optional_pre_call_reclaim_uses_exact_failed_phase(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, source, queued = self._fixture(
                Path(temporary), optional=True
            )
            packet = build_analysis_packet(queued.descriptor, source)
            running = deepcopy(queued.status)
            running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": packet.packet_digest,
                }
            )
            replace_analysis_status(
                paths=paths,
                descriptor=queued.descriptor,
                expected_status=queued.status,
                status=running,
            )

            result = run_once(paths, index)
            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["worker_attempt_count"], 2)
            self.assertEqual(result.status["adapter_attempt_count"], 0)
            self.assertEqual(result.status["inference_state"], "failed")
            self.assertEqual(result.status["fixed_code"], "interrupted")

    def test_complete_intent_recovery_is_counter_neutral(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, source, queued = self._fixture(Path(temporary))
            packet = build_analysis_packet(queued.descriptor, source)
            running = deepcopy(queued.status)
            running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": packet.packet_digest,
                }
            )
            running = replace_analysis_status(
                paths=paths,
                descriptor=queued.descriptor,
                expected_status=queued.status,
                status=running,
            )
            report = build_analysis_report(
                descriptor=queued.descriptor,
                packet=packet,
                inference_state="disabled",
            )
            intent = deepcopy(running)
            intent.update(
                {
                    "report_id": report.report_id,
                    "report_digest": report.report_digest,
                    "render_digest": report.render_digest,
                }
            )
            replace_analysis_status(
                paths=paths,
                descriptor=queued.descriptor,
                expected_status=running,
                status=intent,
            )
            (paths.reports / f"{report.report_id}.json").write_bytes(
                report.report_document
            )
            (paths.rendered / f"{report.report_id}.md").write_bytes(
                report.markdown_bytes
            )

            result = run_once(paths, index)
            self.assertEqual(result.disposition, "published")
            self.assertEqual(result.status["worker_attempt_count"], 1)
            self.assertEqual(result.status["adapter_attempt_count"], 0)

    def test_closed_mock_success_and_retry_publish_from_fresh_attempt_roots(self):
        for scenarios, expected_count in (
            ((MockScenario.SUCCESS,), 1),
            ((MockScenario.TIMEOUT, MockScenario.SUCCESS), 2),
        ):
            with self.subTest(scenarios=scenarios):
                with tempfile.TemporaryDirectory() as temporary:
                    paths, index, _source, _queued = self._fixture(
                        Path(temporary), optional=True
                    )
                    result = run_once(paths, index, ClosedMockPlan(scenarios))

                    self.assertEqual(result.disposition, "published")
                    self.assertEqual(result.status["inference_state"], "succeeded")
                    self.assertEqual(
                        result.status["adapter_attempt_count"], expected_count
                    )
                    self.assertIsNotNone(result.status["accepted_output_digest"])
                    self.assertEqual(self._final_files(paths)[2], [])

    def test_closed_mock_cancel_discards_private_root_before_terminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, _source, _queued = self._fixture(
                Path(temporary), optional=True
            )
            result = run_once(
                paths,
                index,
                ClosedMockPlan((MockScenario.CANCEL,)),
            )

            self.assertEqual(result.disposition, "cancelled")
            self.assertEqual(result.status["state"], "cancelled")
            self.assertEqual(result.status["inference_state"], "cancelled")
            self.assertEqual(result.status["fixed_code"], "cancelled")
            self.assertEqual(result.status["adapter_attempt_count"], 1)
            self.assertEqual(self._final_files(paths), ([], [], []))

    def test_mock_input_too_large_never_creates_or_counts_adapter_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, _source, _queued = self._fixture(
                Path(temporary), optional=True
            )
            with patch(
                "task_governance_tool.codex_analysis_adapter.build_analysis_stdin_frame",
                side_effect=AnalysisPacketError(
                    "input_too_large",
                    "analysis input exceeds the supported size",
                ),
            ):
                result = run_once(
                    paths,
                    index,
                    ClosedMockPlan((MockScenario.SUCCESS,)),
                )

            self.assertEqual(result.disposition, "published")
            self.assertEqual(result.status["inference_state"], "input_too_large")
            self.assertEqual(result.status["adapter_attempt_count"], 0)
            self.assertEqual(self._final_files(paths)[2], [])

    def test_native_slot_quarantine_retains_lease_without_stale_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, index, _source, _queued = self._fixture(
                Path(temporary), optional=True
            )
            captured = []
            real_acquire = AnalysisOutboxSession.acquire

            def capture_session(selected_paths):
                session = real_acquire(selected_paths)
                captured.append(session)
                return session

            quarantine = win32_boundary.Win32QuarantineRequired(
                "test_adapter_root_create_unproved",
                handle=None,
            )
            with (
                patch(
                    "task_governance_tool.analysis_worker._acquire_session",
                    side_effect=capture_session,
                ),
                patch.object(
                    AnalysisOutboxSession,
                    "create_adapter_tree_slot",
                    autospec=True,
                    side_effect=quarantine,
                ),
            ):
                result = run_once(
                    paths,
                    index,
                    ClosedMockPlan((MockScenario.SUCCESS,)),
                )

            session = captured[0]
            try:
                self.assertEqual(result.disposition, "deferred")
                self.assertIsNone(result.analysis_job_id)
                self.assertIsNone(result.status)
                self.assertTrue(result.lease_retained)
                self.assertEqual(session.state, "retained")
                self.assertFalse(session._lease._byte_lock.released)
                self.assertEqual(session._retained_resources, (quarantine,))
            finally:
                self._force_release_retained_session(session)

    def test_unknown_retained_publication_omits_job_and_stale_status(self):
        descriptor = {
            "analysis_job_id": "tg_analysis_job_0123456789abcdef",
        }
        result = analysis_worker._publication_result(
            descriptor,
            PublicationResult(None, "deferred", lease_retained=True),
        )
        self.assertEqual(result.disposition, "deferred")
        self.assertIsNone(result.analysis_job_id)
        self.assertIsNone(result.status)
        self.assertTrue(result.lease_retained)


if __name__ == "__main__":
    unittest.main()
