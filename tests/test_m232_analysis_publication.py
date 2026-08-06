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

from task_governance_tool import analysis_outbox  # noqa: E402
from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402
from task_governance_tool.analysis_contracts import default_recipe  # noqa: E402
from task_governance_tool.analysis_outbox import (  # noqa: E402
    AnalysisOutboxSession,
    BoundAnalysisJob,
    enqueue_analysis_source,
    replace_analysis_status,
)
from task_governance_tool.analysis_packet import build_analysis_packet  # noqa: E402
from task_governance_tool.analysis_renderer import (  # noqa: E402
    REPORT_MARKDOWN_MAX_BYTES,
)
from task_governance_tool.analysis_validator import (  # noqa: E402
    REPORT_JSON_MAX_BYTES,
    AnalysisValidationError,
    build_analysis_report,
)
from task_governance_tool.codex_analysis_adapter import (  # noqa: E402
    FIXED_PROMPT_DIGEST,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.state_paths import analysis_state_paths  # noqa: E402


class _FakeLeaseOwnership:
    def __init__(self, events: list[str] | None = None, *, real=None) -> None:
        self.events = [] if events is None else events
        self.real = real
        self.root = object()

    def borrow_root(self):
        if self.real is not None:
            return self.real.borrow_root()
        return self.root

    def release_normal(self) -> None:
        if self.real is not None:
            self.real.release_normal()
        self.events.extend(("unlock", "lease-close"))

    def retain_for_quarantine(self) -> None:
        self.events.append("retain")
        if self.real is not None:
            self.real.retain_for_quarantine()


class _FakeDirectories:
    def prove(self, _root) -> None:
        return None

    def close(self) -> None:
        return None


def _force_release_retained_session(session) -> None:
    """Test-only cleanup after proving intentional fail-fast retention."""

    for resource in session._retained_resources:
        if isinstance(resource, analysis_outbox._PublicationParents):
            resource.close()
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


@unittest.skipUnless(os.name == "nt", "TG-M23.2 publication is Windows-only")
class AnalysisPublicationTests(unittest.TestCase):
    @staticmethod
    def _read_final(paths, *, directory_name: str, basename: str, maximum: int):
        root_handle = win32_boundary.open_no_follow(
            paths.root,
            win32_boundary.R0,
            expect_directory=True,
            kind="test-publication-root",
        )
        parent = handle = None
        try:
            parent = win32_boundary.open_relative_directory(
                root_handle,
                directory_name,
                win32_boundary.DP,
                kind="test-publication-parent",
            )
            handle = win32_boundary.open_relative_file_if_present(
                parent,
                basename,
                maximum=maximum,
                kind="test-publication-leaf",
            )
            if handle is None:
                return None
            return win32_boundary.read_handle_capped(handle, maximum=maximum)
        finally:
            if handle is not None and not handle.closed:
                handle.close()
            if parent is not None and not parent.closed:
                parent.close()
            root_handle.close()

    def _fixture(self, root: Path):
        fixed_root = root / "fixed"
        fixed_root.mkdir()
        paths = analysis_state_paths(fixed_root)
        index = read_evidence_index(write_evidence_tree(root / "source"))
        entry = next(
            item for item in index.entries if item["bundle_state"] == "legacy_unknown"
        )
        source = validate_evidence_source(index, entry)
        queued = enqueue_analysis_source(
            paths=paths,
            source=source,
            recipe=default_recipe(),
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
        return paths, queued.descriptor, packet, running, report

    def _optional_no_call_fixture(self, root: Path, inference_state: str):
        fixed_root = root / "fixed"
        fixed_root.mkdir()
        paths = analysis_state_paths(fixed_root)
        index = read_evidence_index(write_evidence_tree(root / "source"))
        entry = next(
            item for item in index.entries if item["bundle_state"] == "legacy_unknown"
        )
        source = validate_evidence_source(index, entry)
        queued = enqueue_analysis_source(
            paths=paths,
            source=source,
            recipe=default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixed-mock",
            ),
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
        running = replace_analysis_status(
            paths=paths,
            descriptor=queued.descriptor,
            expected_status=queued.status,
            status=running,
        )
        no_call = deepcopy(running)
        no_call["inference_state"] = inference_state
        no_call = replace_analysis_status(
            paths=paths,
            descriptor=queued.descriptor,
            expected_status=running,
            status=no_call,
        )
        report = build_analysis_report(
            descriptor=queued.descriptor,
            packet=packet,
            inference_state=inference_state,
            prompt_digest=FIXED_PROMPT_DIGEST,
        )
        return paths, queued.descriptor, packet, no_call, report

    @staticmethod
    def _intent(running, report):
        intent = deepcopy(running)
        intent.update(
            {
                "report_id": report.report_id,
                "report_digest": report.report_digest,
                "render_digest": report.render_digest,
            }
        )
        return intent

    def _arm_recovery(self, paths, descriptor, running, report):
        intent = self._intent(running, report)
        return replace_analysis_status(
            paths=paths,
            descriptor=descriptor,
            expected_status=running,
            status=intent,
        )

    def test_offline_success_publishes_exact_bytes_and_releases_lease_last(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            events: list[str] = []
            real_acquire = analysis_outbox._acquire_analysis_lease
            original_close = win32_boundary.OwnedHandle.close

            def observed_close(handle):
                events.append("handle-close:" + handle.kind)
                return original_close(handle)

            with (
                patch.object(
                    analysis_outbox,
                    "_acquire_analysis_lease",
                    side_effect=lambda selected_paths: _FakeLeaseOwnership(
                        events,
                        real=real_acquire(selected_paths),
                    ),
                ),
                patch.object(
                    win32_boundary.OwnedHandle,
                    "close",
                    new=observed_close,
                ),
            ):
                session = AnalysisOutboxSession.acquire(paths)
                proof = session.seal_no_adapter_controller_proof()
                result = session.publish_no_adapter(
                    descriptor=descriptor,
                    expected_status=running,
                    packet=packet,
                    report=report,
                    proof=proof,
                )

            self.assertEqual(result.disposition, "published")
            self.assertEqual(result.status["state"], "published")
            self.assertEqual(session.state, "released")
            self.assertEqual(events[-2:], ["unlock", "lease-close"])
            self.assertEqual(
                self._read_final(
                    paths,
                    directory_name=paths.reports.name,
                    basename=f"{report.report_id}.json",
                    maximum=REPORT_JSON_MAX_BYTES,
                ),
                report.report_document,
            )
            self.assertEqual(
                self._read_final(
                    paths,
                    directory_name=paths.rendered.name,
                    basename=f"{report.report_id}.md",
                    maximum=REPORT_MARKDOWN_MAX_BYTES,
                ),
                report.markdown_bytes,
            )
            self.assertEqual(list(paths.temporary.iterdir()), [])

    def test_optional_no_call_states_publish_without_adapter_tree(self):
        for inference_state in ("policy_blocked", "input_too_large"):
            with self.subTest(inference_state=inference_state), tempfile.TemporaryDirectory() as temporary:
                paths, descriptor, packet, running, report = (
                    self._optional_no_call_fixture(
                        Path(temporary),
                        inference_state,
                    )
                )
                session = AnalysisOutboxSession.acquire(paths)
                proof = session.seal_no_adapter_controller_proof()
                result = session.publish_no_adapter(
                    descriptor=descriptor,
                    expected_status=running,
                    packet=packet,
                    report=report,
                    proof=proof,
                    prompt_digest=FIXED_PROMPT_DIGEST,
                )

                self.assertEqual(result.disposition, "published")
                self.assertEqual(result.status["state"], "published")
                self.assertEqual(result.status["adapter_attempt_count"], 0)
                self.assertEqual(result.status["inference_state"], inference_state)
                self.assertEqual(
                    self._read_final(
                        paths,
                        directory_name=paths.reports.name,
                        basename=f"{report.report_id}.json",
                        maximum=REPORT_JSON_MAX_BYTES,
                    ),
                    report.report_document,
                )
                self.assertEqual(list(paths.temporary.iterdir()), [])

    def test_optional_no_call_wrong_prompt_is_preintent_report_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._optional_no_call_fixture(
                Path(temporary),
                "policy_blocked",
            )
            session = AnalysisOutboxSession.acquire(paths)
            proof = session.seal_no_adapter_controller_proof()
            result = session.publish_no_adapter(
                descriptor=descriptor,
                expected_status=running,
                packet=packet,
                report=report,
                proof=proof,
                prompt_digest="sha256:" + "0" * 64,
            )

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["state"], "failed")
            self.assertEqual(result.status["fixed_code"], "report_invalid")
            self.assertEqual(result.status["report_id"], None)
            self.assertEqual(session.state, "released")
            self.assertEqual(list(paths.reports.iterdir()), [])
            self.assertEqual(list(paths.rendered.iterdir()), [])
            self.assertEqual(list(paths.temporary.iterdir()), [])

    def test_no_adapter_ledger_is_closed_zero_only_session_bound_and_single_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            categories = (
                "B",
                "T",
                "J",
                "restricted_token",
                "mapping",
                "event",
                "pipe",
                "stdio",
                "worker",
            )
            self.assertEqual(
                set(categories),
                analysis_outbox._CONTROLLER_RESOURCE_CATEGORIES,
            )
            for category in categories:
                with self.subTest(category=category):
                    session = AnalysisOutboxSession.acquire(paths)
                    try:
                        before = session.read_bound_job(descriptor).status
                        session.mark_controller_resource_created(category)
                        with self.assertRaises(analysis_outbox.AnalysisOutboxError):
                            session.seal_no_adapter_controller_proof()
                        self.assertEqual(
                            session.read_bound_job(descriptor).status,
                            before,
                        )
                    finally:
                        session.release_normal()

            with self.assertRaises(analysis_outbox.AnalysisOutboxError):
                analysis_outbox.NoAdapterControllerProof(
                    object(),
                    _token=object(),
                )

            first = AnalysisOutboxSession.acquire(paths)
            foreign = first.seal_no_adapter_controller_proof()
            first.release_normal()

            second = AnalysisOutboxSession.acquire(paths)
            try:
                before = second.read_bound_job(descriptor).status
                with self.assertRaises(analysis_outbox.AnalysisOutboxError):
                    second.publish_no_adapter(
                        descriptor=descriptor,
                        expected_status=running,
                        packet=packet,
                        report=report,
                        proof=foreign,
                    )
                self.assertEqual(second.read_bound_job(descriptor).status, before)
                proof = second.seal_no_adapter_controller_proof()
                result = second.publish_no_adapter(
                    descriptor=descriptor,
                    expected_status=running,
                    packet=packet,
                    report=report,
                    proof=proof,
                )
            finally:
                if second.state == "active":
                    second.release_normal()
            self.assertEqual(result.disposition, "published")
            self.assertTrue(proof._consumed)
            with self.assertRaises(analysis_outbox.AnalysisOutboxError):
                second.publish_no_adapter(
                    descriptor=descriptor,
                    expected_status=running,
                    packet=packet,
                    report=report,
                    proof=proof,
                )

    def test_post_intent_rollback_keeps_parents_and_lease_on_failed_cas_uncertainty(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            session = AnalysisOutboxSession.acquire(paths)
            proof = session.seal_no_adapter_controller_proof()
            events: list[str] = []
            original_cas = AnalysisOutboxSession.cas_status
            original_rollback = analysis_outbox._NoAdapterPublicationTree.rollback
            original_parent_close = analysis_outbox._PublicationParents.close
            original_release = analysis_outbox._AnalysisLeaseOwnership.release_normal
            calls = 0

            def controlled_cas(current_session, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    events.append("intent-cas")
                    return original_cas(current_session, **kwargs)
                if calls == 2:
                    events.append("published-cas-not-applied")
                    return analysis_outbox.StatusCasResult(
                        kwargs["expected_status"],
                        "ambiguous_not_applied",
                    )
                if calls == 3:
                    events.append("failed-cas-not-applied")
                    return analysis_outbox.StatusCasResult(
                        kwargs["expected_status"],
                        "ambiguous_not_applied",
                    )
                raise AssertionError("unexpected CAS")

            def observed_rollback(tree):
                events.append("rollback")
                return original_rollback(tree)

            def observed_parent_close(parents):
                events.append("parent-close")
                return original_parent_close(parents)

            def observed_release(lease):
                events.append("lease-release")
                return original_release(lease)

            try:
                with (
                    patch.object(
                        AnalysisOutboxSession,
                        "cas_status",
                        new=controlled_cas,
                    ),
                    patch.object(
                        analysis_outbox._NoAdapterPublicationTree,
                        "rollback",
                        new=observed_rollback,
                    ),
                    patch.object(
                        analysis_outbox._PublicationParents,
                        "close",
                        new=observed_parent_close,
                    ),
                    patch.object(
                        analysis_outbox._AnalysisLeaseOwnership,
                        "release_normal",
                        new=observed_release,
                    ),
                ):
                    result = session.publish_no_adapter(
                        descriptor=descriptor,
                        expected_status=running,
                        packet=packet,
                        report=report,
                        proof=proof,
                    )
                self.assertEqual(
                    events,
                    [
                        "intent-cas",
                        "published-cas-not-applied",
                        "rollback",
                        "failed-cas-not-applied",
                    ],
                )
                self.assertEqual(result.disposition, "deferred")
                self.assertTrue(result.lease_retained)
                self.assertEqual(session.state, "retained")
                parents = next(
                    item
                    for item in session._retained_resources
                    if isinstance(item, analysis_outbox._PublicationParents)
                )
                self.assertFalse(parents.reports.closed)
                self.assertFalse(parents.rendered.closed)
                self.assertFalse(session._lease._byte_lock.released)
            finally:
                _force_release_retained_session(session)

    def test_markdown_collision_rolls_back_json_and_preserves_foreign_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            foreign = b"foreign-markdown"
            markdown_path = paths.rendered / f"{report.report_id}.md"
            markdown_path.write_bytes(foreign)
            session = AnalysisOutboxSession.acquire(paths)
            proof = session.seal_no_adapter_controller_proof()
            result = session.publish_no_adapter(
                descriptor=descriptor,
                expected_status=running,
                packet=packet,
                report=report,
                proof=proof,
            )

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["fixed_code"], "publication_failed")
            self.assertFalse((paths.reports / f"{report.report_id}.json").exists())
            self.assertEqual(markdown_path.read_bytes(), foreign)
            self.assertEqual(list(paths.temporary.iterdir()), [])
            self.assertEqual(session.state, "released")

    def test_preintent_revalidation_failure_cleans_tree_and_fails_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            with patch.object(
                analysis_outbox,
                "validate_report_document",
                side_effect=(
                    report,
                    AnalysisValidationError(
                        "report_invalid",
                        "analysis report is invalid",
                    ),
                ),
            ):
                session = AnalysisOutboxSession.acquire(paths)
                proof = session.seal_no_adapter_controller_proof()
                result = session.publish_no_adapter(
                    descriptor=descriptor,
                    expected_status=running,
                    packet=packet,
                    report=report,
                    proof=proof,
                )

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["state"], "failed")
            self.assertEqual(result.status["fixed_code"], "report_invalid")
            self.assertFalse((paths.reports / f"{report.report_id}.json").exists())
            self.assertFalse((paths.rendered / f"{report.report_id}.md").exists())
            self.assertEqual(list(paths.temporary.iterdir()), [])
            audit = AnalysisOutboxSession.acquire(paths)
            try:
                self.assertEqual(audit.read_bound_job(descriptor).status, result.status)
            finally:
                audit.release_normal()

    def test_recovery_two_valid_files_is_counter_neutral_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            intent = self._arm_recovery(paths, descriptor, running, report)
            (paths.reports / f"{report.report_id}.json").write_bytes(
                report.report_document
            )
            (paths.rendered / f"{report.report_id}.md").write_bytes(
                report.markdown_bytes
            )
            session = AnalysisOutboxSession.acquire(paths)
            result = session.recover_publication(
                descriptor=descriptor,
                expected_status=intent,
                packet=packet,
            )

            self.assertEqual(result.disposition, "published")
            self.assertEqual(result.status["state"], "published")
            self.assertEqual(
                result.status["worker_attempt_count"],
                intent["worker_attempt_count"],
            )
            self.assertEqual(
                result.status["adapter_attempt_count"],
                intent["adapter_attempt_count"],
            )
            self.assertEqual(
                (paths.reports / f"{report.report_id}.json").read_bytes(),
                report.report_document,
            )

    def test_optional_recovery_is_counter_neutral_and_never_opens_private_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._optional_no_call_fixture(
                Path(temporary),
                "policy_blocked",
            )
            intent = self._arm_recovery(paths, descriptor, running, report)
            (paths.reports / f"{report.report_id}.json").write_bytes(
                report.report_document
            )
            (paths.rendered / f"{report.report_id}.md").write_bytes(
                report.markdown_bytes
            )
            retained_root = paths.temporary / ".taskgov-analysis-aaaaaaaa"
            retained_root.mkdir()
            retained_output = retained_root / "output.json"
            retained_output.write_bytes(b"must-not-be-opened")

            session = AnalysisOutboxSession.acquire(paths)
            original_open = win32_boundary.open_relative_file_if_present
            with patch.object(
                win32_boundary,
                "open_relative_file_if_present",
                wraps=original_open,
            ) as opened:
                result = session.recover_publication(
                    descriptor=descriptor,
                    expected_status=intent,
                    packet=packet,
                    expected_prompt_digest=FIXED_PROMPT_DIGEST,
                )

            self.assertEqual(result.disposition, "published")
            self.assertEqual(result.status["state"], "published")
            self.assertEqual(
                result.status["worker_attempt_count"],
                intent["worker_attempt_count"],
            )
            self.assertEqual(
                result.status["adapter_attempt_count"],
                intent["adapter_attempt_count"],
            )
            self.assertNotIn(
                "output.json",
                [call.args[1] for call in opened.call_args_list],
            )
            self.assertEqual(retained_output.read_bytes(), b"must-not-be-opened")

    def test_optional_recovery_wrong_prompt_preserves_unmatched_finals(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._optional_no_call_fixture(
                Path(temporary),
                "input_too_large",
            )
            intent = self._arm_recovery(paths, descriptor, running, report)
            report_path = paths.reports / f"{report.report_id}.json"
            markdown_path = paths.rendered / f"{report.report_id}.md"
            report_path.write_bytes(report.report_document)
            markdown_path.write_bytes(report.markdown_bytes)

            session = AnalysisOutboxSession.acquire(paths)
            result = session.recover_publication(
                descriptor=descriptor,
                expected_status=intent,
                packet=packet,
                expected_prompt_digest="sha256:" + "0" * 64,
            )

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["fixed_code"], "report_invalid")
            self.assertEqual(report_path.read_bytes(), report.report_document)
            self.assertEqual(markdown_path.read_bytes(), report.markdown_bytes)

    def test_recovery_json_only_rolls_back_match_and_fails_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            intent = self._arm_recovery(paths, descriptor, running, report)
            report_path = paths.reports / f"{report.report_id}.json"
            report_path.write_bytes(report.report_document)
            session = AnalysisOutboxSession.acquire(paths)
            result = session.recover_publication(
                descriptor=descriptor,
                expected_status=intent,
                packet=packet,
            )

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["fixed_code"], "publication_failed")
            self.assertFalse(report_path.exists())
            self.assertEqual(result.status["report_id"], None)

    def test_recovery_mismatch_rolls_back_matching_peer_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            intent = self._arm_recovery(paths, descriptor, running, report)
            report_path = paths.reports / f"{report.report_id}.json"
            markdown_path = paths.rendered / f"{report.report_id}.md"
            report_path.write_bytes(report.report_document)
            foreign = report.markdown_bytes + b"foreign"
            markdown_path.write_bytes(foreign)
            session = AnalysisOutboxSession.acquire(paths)
            result = session.recover_publication(
                descriptor=descriptor,
                expected_status=intent,
                packet=packet,
            )

            self.assertEqual(result.disposition, "failed")
            self.assertEqual(result.status["fixed_code"], "report_invalid")
            self.assertFalse(report_path.exists())
            self.assertEqual(markdown_path.read_bytes(), foreign)

    def test_recovery_access_uncertainty_is_no_write_deferred(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            intent = self._arm_recovery(paths, descriptor, running, report)
            events: list[str] = []
            real_acquire = analysis_outbox._acquire_analysis_lease
            real_open = win32_boundary.open_relative_file_if_present

            def fail_recovery_report(parent, basename, **kwargs):
                if kwargs.get("kind") == "analysis-recovery-report":
                    raise win32_boundary.Win32BoundaryError()
                return real_open(parent, basename, **kwargs)

            with (
                patch.object(
                    analysis_outbox,
                    "_acquire_analysis_lease",
                    side_effect=lambda selected_paths: _FakeLeaseOwnership(
                        events,
                        real=real_acquire(selected_paths),
                    ),
                ),
                patch.object(
                    win32_boundary,
                    "open_relative_file_if_present",
                    side_effect=fail_recovery_report,
                ),
            ):
                session = AnalysisOutboxSession.acquire(paths)
                result = session.recover_publication(
                    descriptor=descriptor,
                    expected_status=intent,
                    packet=packet,
                )

            self.assertEqual(result.disposition, "deferred")
            self.assertFalse(result.lease_retained)
            self.assertEqual(events, ["unlock", "lease-close"])
            self.assertEqual(result.status, intent)

    def test_publication_quarantine_retains_without_unlock(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running, report = self._fixture(
                Path(temporary)
            )
            intent = self._intent(running, report)
            events: list[str] = []
            fake_lease = _FakeLeaseOwnership(events)
            session = AnalysisOutboxSession(
                paths,
                fake_lease,
                _token=analysis_outbox._SESSION_CONSTRUCTOR_TOKEN,
            )
            with (
                patch.object(
                    AnalysisOutboxSession,
                    "read_bound_job",
                    return_value=BoundAnalysisJob(descriptor, intent),
                ),
                patch.object(
                    AnalysisOutboxSession,
                    "_open_publication_parents",
                    side_effect=win32_boundary.Win32QuarantineRequired(
                        "fixture-uncertain",
                        handle=None,
                    ),
                ),
            ):
                result = session.recover_publication(
                    descriptor=descriptor,
                    expected_status=intent,
                    packet=packet,
                )

            self.assertEqual(result.disposition, "deferred")
            self.assertTrue(result.lease_retained)
            self.assertEqual(session.state, "retained")
            self.assertEqual(events, ["retain"])


if __name__ == "__main__":
    unittest.main()
