from __future__ import annotations

import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.m23_test_support import write_evidence_tree
from tests import test_m232_analysis_publication as publication_test_support


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402
from task_governance_tool import analysis_outbox  # noqa: E402
from task_governance_tool._analysis_windows_process import (  # noqa: E402
    MockScenario,
)
from task_governance_tool.analysis_contracts import default_recipe  # noqa: E402
from task_governance_tool.analysis_outbox import (  # noqa: E402
    AnalysisOutboxError,
    AnalysisOutboxSession,
    enqueue_analysis_source,
    replace_analysis_status,
)
from task_governance_tool.analysis_packet import build_analysis_packet  # noqa: E402
from task_governance_tool.analysis_validator import (  # noqa: E402
    REPORT_JSON_MAX_BYTES,
    build_analysis_report,
)
from task_governance_tool.analysis_renderer import (  # noqa: E402
    REPORT_MARKDOWN_MAX_BYTES,
)
from task_governance_tool.codex_analysis_adapter import (  # noqa: E402
    abort_prepared_mock_attempt,
    bind_closed_mock_attempt,
    execute_prepared_mock_attempt,
    mark_prepared_mock_attempt_recorded,
    prepare_closed_mock_input,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.state_paths import analysis_state_paths  # noqa: E402


def _close_retained_publication(session: AnalysisOutboxSession) -> None:
    """Test-only close of intentionally retained final leaf handles."""

    for resource in session._retained_resources:
        if isinstance(
            resource,
            (
                analysis_outbox._AdapterPublicationTree,
                analysis_outbox._NoAdapterPublicationTree,
            ),
        ):
            for leaf in (resource.json_leaf, resource.markdown_leaf):
                if leaf is not None and leaf.handle is not None and not leaf.handle.closed:
                    leaf.handle.close()
                    leaf.handle = None
    publication_test_support._force_release_retained_session(session)


@unittest.skipUnless(os.name == "nt", "TG-M23.2 publication is Windows-only")
class AnalysisAdapterPublicationTests(unittest.TestCase):
    def _fixture(self, root: Path):
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
        return paths, queued.descriptor, packet, running

    def _prepare(self, session, descriptor, packet, number, scenario, prior=None):
        input_result = prepare_closed_mock_input(
            descriptor,
            packet,
            number,
            scenario,
            prior,
        )
        self.assertTrue(input_result.ready)
        prepared_input = input_result.prepared_input
        slot = session.create_adapter_tree_slot(
            descriptor,
            packet,
            prepared_input.binding,
            number,
        )
        prepared = bind_closed_mock_attempt(
            prepared_input,
            slot.root_capability,
        )
        return slot, prepared

    def _execute(self, session, descriptor, current, prepared):
        counted = deepcopy(current)
        counted.update(
            {
                "adapter_attempt_count": current["adapter_attempt_count"] + 1,
                "inference_state": "running",
            }
        )
        counted_result = session.cas_status(
            descriptor=descriptor,
            expected_status=current,
            status=counted,
        )
        self.assertTrue(counted_result.applied)
        mark_prepared_mock_attempt_recorded(prepared)
        attempt = execute_prepared_mock_attempt(prepared)
        outcome = deepcopy(counted_result.status)
        outcome.update(
            {
                "inference_state": attempt.inference_state,
                "duration_ms": counted_result.status["duration_ms"]
                + attempt.duration_ms,
                "accepted_output_digest": (
                    attempt.adapter_output.accepted_output_digest
                    if attempt.adapter_output is not None
                    else None
                ),
            }
        )
        outcome_result = session.cas_status(
            descriptor=descriptor,
            expected_status=counted_result.status,
            status=outcome,
        )
        self.assertTrue(outcome_result.applied)
        return outcome_result.status, attempt

    def _ready_publication(self, root: Path, scenario: MockScenario):
        paths, descriptor, packet, running = self._fixture(root)
        session = AnalysisOutboxSession.acquire(paths)
        slot, prepared = self._prepare(
            session,
            descriptor,
            packet,
            1,
            scenario,
        )
        outcome, attempt = self._execute(
            session,
            descriptor,
            running,
            prepared,
        )
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state=outcome["inference_state"],
            adapter_output=attempt.adapter_output,
            prompt_digest=attempt.prompt_digest,
        )
        return paths, descriptor, packet, session, slot, outcome, attempt, report

    def test_abort_is_no_write_and_permanently_closes_no_adapter_lane(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running = self._fixture(Path(temporary))
            session = AnalysisOutboxSession.acquire(paths)
            slot, prepared = self._prepare(
                session,
                descriptor,
                packet,
                1,
                MockScenario.SUCCESS,
            )
            root_path = paths.temporary / slot._root_basename
            self.assertTrue(root_path.is_dir())
            aborted = abort_prepared_mock_attempt(prepared)
            session.abort_adapter_tree(slot, aborted)

            self.assertFalse(root_path.exists())
            self.assertEqual(session.read_bound_job(descriptor).status, running)
            self.assertEqual(list(paths.reports.iterdir()), [])
            self.assertEqual(list(paths.rendered.iterdir()), [])
            with self.assertRaises(AnalysisOutboxError):
                session.seal_no_adapter_controller_proof()
            with self.assertRaises(AnalysisOutboxError):
                session.abort_adapter_tree(slot, aborted)
            session.release_normal()

    def test_discarded_n1_is_absent_before_fresh_n2_is_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, descriptor, packet, running = self._fixture(Path(temporary))
            session = AnalysisOutboxSession.acquire(paths)
            first_slot, prepared = self._prepare(
                session,
                descriptor,
                packet,
                1,
                MockScenario.TIMEOUT,
            )
            timeout_status, first = self._execute(
                session,
                descriptor,
                running,
                prepared,
            )
            old_name = first_slot._root_basename
            old_identity = first_slot._root_identity
            prior = session.discard_adapter_tree(first_slot, first.tree_proof)
            self.assertFalse((paths.temporary / old_name).exists())

            second_slot, second_prepared = self._prepare(
                session,
                descriptor,
                packet,
                2,
                MockScenario.SUCCESS,
                prior,
            )
            self.assertNotEqual(second_slot._root_basename, old_name)
            self.assertFalse(second_slot._root_identity.same_object(old_identity))
            self.assertEqual(timeout_status["adapter_attempt_count"], 1)
            aborted = abort_prepared_mock_attempt(second_prepared)
            session.abort_adapter_tree(second_slot, aborted)
            self.assertEqual(list(paths.temporary.iterdir()), [])
            session.release_normal()

    def test_success_and_final_timeout_publish_reports_on_the_exact_attempt_root(self):
        for scenario, expected_output in (
            (MockScenario.SUCCESS, True),
            (MockScenario.TIMEOUT, False),
        ):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temporary:
                    (
                        paths,
                        descriptor,
                        packet,
                        session,
                        slot,
                        outcome,
                        attempt,
                        report,
                    ) = self._ready_publication(Path(temporary), scenario)
                    report_parents = []
                    real_create = win32_boundary.create_relative_file
                    real_open = win32_boundary.open_relative_file

                    def observed_create(parent, basename, *args, **kwargs):
                        if basename in {"report.json", "report.md"}:
                            report_parents.append(parent)
                        return real_create(parent, basename, *args, **kwargs)

                    def reject_raw_output(parent, basename, *args, **kwargs):
                        if basename == "output.json":
                            raise AssertionError("publication opened raw adapter output")
                        return real_open(parent, basename, *args, **kwargs)

                    with (
                        patch.object(
                            win32_boundary,
                            "create_relative_file",
                            side_effect=observed_create,
                        ),
                        patch.object(
                            win32_boundary,
                            "open_relative_file",
                            side_effect=reject_raw_output,
                        ),
                    ):
                        result = session.publish_adapter(
                            descriptor=descriptor,
                            expected_status=outcome,
                            packet=packet,
                            report=report,
                            slot=slot,
                            tree_proof=attempt.tree_proof,
                            adapter_output=attempt.adapter_output,
                            prompt_digest=attempt.prompt_digest,
                        )

                    self.assertEqual(result.disposition, "published")
                    self.assertEqual(result.status["state"], "published")
                    self.assertEqual(
                        result.status["accepted_output_digest"] is not None,
                        expected_output,
                    )
                    self.assertEqual(report_parents, [slot._root_handle, slot._root_handle])
                    self.assertEqual(
                        publication_test_support.AnalysisPublicationTests._read_final(
                            paths,
                            directory_name=paths.reports.name,
                            basename=f"{report.report_id}.json",
                            maximum=REPORT_JSON_MAX_BYTES,
                        ),
                        report.report_document,
                    )
                    self.assertEqual(
                        publication_test_support.AnalysisPublicationTests._read_final(
                            paths,
                            directory_name=paths.rendered.name,
                            basename=f"{report.report_id}.md",
                            maximum=REPORT_MARKDOWN_MAX_BYTES,
                        ),
                        report.markdown_bytes,
                    )
                    self.assertEqual(list(paths.temporary.iterdir()), [])

    def test_wrong_slot_or_tree_proof_is_rejected_before_consumption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one").mkdir()
            (root / "two").mkdir()
            first = self._ready_publication(root / "one", MockScenario.TIMEOUT)
            second = self._ready_publication(root / "two", MockScenario.TIMEOUT)
            p1, d1, packet1, s1, slot1, status1, attempt1, report1 = first
            p2, d2, _packet2, s2, slot2, _status2, attempt2, _report2 = second
            with self.assertRaises(AnalysisOutboxError):
                s1.publish_adapter(
                    descriptor=d1,
                    expected_status=status1,
                    packet=packet1,
                    report=report1,
                    slot=slot2,
                    tree_proof=attempt1.tree_proof,
                    adapter_output=None,
                    prompt_digest=attempt1.prompt_digest,
                )
            with self.assertRaises(AnalysisOutboxError):
                s1.publish_adapter(
                    descriptor=d1,
                    expected_status=status1,
                    packet=packet1,
                    report=report1,
                    slot=slot1,
                    tree_proof=attempt2.tree_proof,
                    adapter_output=None,
                    prompt_digest=attempt1.prompt_digest,
                )
            self.assertFalse(attempt1.tree_proof._consumed)
            self.assertFalse(attempt2.tree_proof._consumed)
            s1.discard_adapter_tree(slot1, attempt1.tree_proof)
            s2.discard_adapter_tree(slot2, attempt2.tree_proof)
            s1.release_normal()
            s2.release_normal()
            self.assertEqual(list(p1.temporary.iterdir()), [])
            self.assertEqual(list(p2.temporary.iterdir()), [])

    def test_full_temporary_inventory_blocks_adapter_and_no_adapter_root_33(self):
        with self.subTest(lane="adapter"):
            with tempfile.TemporaryDirectory() as temporary:
                paths, descriptor, packet, running = self._fixture(Path(temporary))
                for number in range(32):
                    (paths.temporary / f".taskgov-analysis-{number:08x}").mkdir()
                before = sorted(item.name for item in paths.temporary.iterdir())
                prepared = prepare_closed_mock_input(
                    descriptor,
                    packet,
                    1,
                    MockScenario.SUCCESS,
                ).prepared_input
                session = AnalysisOutboxSession.acquire(paths)
                with self.assertRaises(AnalysisOutboxError):
                    session.create_adapter_tree_slot(
                        descriptor,
                        packet,
                        prepared.binding,
                        1,
                    )
                self.assertEqual(
                    sorted(item.name for item in paths.temporary.iterdir()),
                    before,
                )
                self.assertEqual(session.read_bound_job(descriptor).status, running)
                session.release_normal()

        with self.subTest(lane="no-adapter"):
            with tempfile.TemporaryDirectory() as temporary:
                paths, descriptor, packet, running, report = (
                    publication_test_support.AnalysisPublicationTests()._fixture(
                        Path(temporary)
                    )
                )
                for number in range(32):
                    (paths.temporary / f".taskgov-analysis-{number:08x}").mkdir()
                before = sorted(item.name for item in paths.temporary.iterdir())
                session = AnalysisOutboxSession.acquire(paths)
                proof = session.seal_no_adapter_controller_proof()
                result = session.publish_no_adapter(
                    descriptor=descriptor,
                    expected_status=running,
                    packet=packet,
                    report=report,
                    proof=proof,
                )
                self.assertEqual(result.disposition, "deferred")
                self.assertEqual(result.status, running)
                self.assertEqual(
                    sorted(item.name for item in paths.temporary.iterdir()),
                    before,
                )
                self.assertEqual(list(paths.reports.iterdir()), [])
                self.assertEqual(list(paths.rendered.iterdir()), [])

    def test_terminal_close_failure_returns_published_not_stale_intent_in_both_lanes(self):
        cases = ("adapter", "no-adapter")
        for lane in cases:
            with self.subTest(lane=lane):
                with tempfile.TemporaryDirectory() as temporary:
                    if lane == "adapter":
                        (
                            _paths,
                            descriptor,
                            packet,
                            session,
                            slot,
                            outcome,
                            attempt,
                            report,
                        ) = self._ready_publication(
                            Path(temporary),
                            MockScenario.SUCCESS,
                        )
                        publish = lambda: session.publish_adapter(
                            descriptor=descriptor,
                            expected_status=outcome,
                            packet=packet,
                            report=report,
                            slot=slot,
                            tree_proof=attempt.tree_proof,
                            adapter_output=attempt.adapter_output,
                            prompt_digest=attempt.prompt_digest,
                        )
                    else:
                        _paths, descriptor, packet, running, report = (
                            publication_test_support.AnalysisPublicationTests()._fixture(
                                Path(temporary)
                            )
                        )
                        session = AnalysisOutboxSession.acquire(_paths)
                        proof = session.seal_no_adapter_controller_proof()
                        publish = lambda: session.publish_no_adapter(
                            descriptor=descriptor,
                            expected_status=running,
                            packet=packet,
                            report=report,
                            proof=proof,
                        )
                    try:
                        with patch.object(
                            analysis_outbox._HeldPublicationLeaf,
                            "close_published",
                            side_effect=win32_boundary.Win32QuarantineRequired(
                                "fixture-final-close-unproved",
                                handle=None,
                            ),
                        ):
                            result = publish()
                        self.assertEqual(result.disposition, "deferred")
                        self.assertTrue(result.lease_retained)
                        self.assertEqual(result.status["state"], "published")
                    finally:
                        _close_retained_publication(session)

    def test_ambiguous_published_cas_rereads_terminal_status_in_both_lanes(self):
        for lane in ("adapter", "no-adapter"):
            for observe_fails in (False, True):
                with self.subTest(lane=lane, observe_fails=observe_fails):
                    with tempfile.TemporaryDirectory() as temporary:
                        if lane == "adapter":
                            (
                                _paths,
                                descriptor,
                                packet,
                                session,
                                slot,
                                outcome,
                                attempt,
                                report,
                            ) = self._ready_publication(
                                Path(temporary),
                                MockScenario.SUCCESS,
                            )
                            publish = lambda: session.publish_adapter(
                                descriptor=descriptor,
                                expected_status=outcome,
                                packet=packet,
                                report=report,
                                slot=slot,
                                tree_proof=attempt.tree_proof,
                                adapter_output=attempt.adapter_output,
                                prompt_digest=attempt.prompt_digest,
                            )
                        else:
                            _paths, descriptor, packet, running, report = (
                                publication_test_support.AnalysisPublicationTests()._fixture(
                                    Path(temporary)
                                )
                            )
                            session = AnalysisOutboxSession.acquire(_paths)
                            proof = session.seal_no_adapter_controller_proof()
                            publish = lambda: session.publish_no_adapter(
                                descriptor=descriptor,
                                expected_status=running,
                                packet=packet,
                                report=report,
                                proof=proof,
                            )

                        original_cas = AnalysisOutboxSession.cas_status
                        original_read = AnalysisOutboxSession.read_bound_job
                        calls = 0
                        observing_uncertain_cas = False

                        def applied_then_uncertain(current_session, **kwargs):
                            nonlocal calls, observing_uncertain_cas
                            calls += 1
                            result = original_cas(current_session, **kwargs)
                            if calls == 2:
                                observing_uncertain_cas = True
                                raise win32_boundary.Win32QuarantineRequired(
                                    "fixture-published-cas-unproved",
                                    handle=None,
                                )
                            return result

                        def controlled_read(current_session, selected_descriptor):
                            if observing_uncertain_cas and observe_fails:
                                raise win32_boundary.Win32QuarantineRequired(
                                    "fixture-status-reread-unproved",
                                    handle=None,
                                )
                            return original_read(current_session, selected_descriptor)

                        try:
                            with (
                                patch.object(
                                    AnalysisOutboxSession,
                                    "cas_status",
                                    new=applied_then_uncertain,
                                ),
                                patch.object(
                                    AnalysisOutboxSession,
                                    "read_bound_job",
                                    new=controlled_read,
                                ),
                            ):
                                result = publish()
                            self.assertEqual(calls, 2)
                            self.assertEqual(result.disposition, "deferred")
                            self.assertTrue(result.lease_retained)
                            if observe_fails:
                                self.assertIsNone(result.status)
                            else:
                                self.assertEqual(result.status["state"], "published")
                        finally:
                            _close_retained_publication(session)


if __name__ == "__main__":
    unittest.main()
