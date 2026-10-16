"""Public collection diagnostics with real isolated journals and original I/O."""

from contextlib import contextmanager
from dataclasses import replace
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool import review_handoff_transport as files
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection import CollectionService, source
from task_governance_tool.review_answer_collection_repository import CollectionRepository
from task_governance_tool.review_answer_runtime import SourceGroup
from task_governance_tool.review_wait_runtime.failure_diagnostics import CallDiagnostics, DiagnosticError
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_server import _call
from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn, HostAdapterError
from task_governance_tool.state_resolver import canonical_state_paths
from tests.test_review_answer_pure_values import metadata_fixture, TURN2
from tests.test_review_wait_service import BINDING


PRIVATE = "PRIVATE_UNRETAINED_EXCEPTION"


class CollectionDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".tmp-review").mkdir()
        self.metadata, self.answer = metadata_fixture()
        self.store = CollectionRepository(self.root / "collection.sqlite3")
        self.store.initialize((self.metadata["basis"],))
        self.service = CollectionService(self.root, self.store,
            original_paths=(self.metadata["original"]["path"],), admit_basis=Mock())
        self.service.bind(1, self.metadata["binding"])
        self.enterContext(patch.object(files, "require_ignored"))
        binding = self.metadata["binding"]
        self.parent, self.child = binding["parent_session_id"], binding["reviewer_session_id"]
        self.task = self.metadata["basis"]["execution_basis"]["task_id"]
        self.current = SimpleNamespace(task_id=self.task, parent_thread_id=self.parent)
        self.caller = dict(threadId=self.parent, turnId=TURN2)
        self.host = Mock(_metadata=self.caller)
        self.adapter = Mock()
        self.adapter.observe_answer.return_value = self.observed()
        self.worker = Mock(side_effect=AssertionError("worker must not start"))
        self.session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "host.mjs", self.root, "UTC"),
            basis_factory=lambda *args, **kwargs: lambda: self.current,
            managed_host_factory=lambda **kwargs: self.host, session_factory=self.worker)
        paths = SimpleNamespace(review_wait_request_store=lambda *args: self.root / "absent.sqlite3")
        self.enterContext(patch.object(self.session, "_location", return_value=paths))
        self.enterContext(patch("task_governance_tool.setup_feature_config.answer_collection_allowed", return_value=True))
        self.enterContext(patch("task_governance_tool.review_answer_runtime.sources_for_reviewers",
            side_effect=lambda _repo, _skill, _binding, reviewers: self.groups(reviewers)))
        self.enterContext(patch("task_governance_tool.review_answer_runtime.service_for_event", side_effect=lambda *args: (self.service, self.current)))
        self.enterContext(patch("task_governance_tool.review_answer_host.PublicAnswerHost", return_value=self.adapter))

    def groups(self, reviewers=None):
        reviewers = tuple(reviewers or (self.child,))
        slots = tuple(range(1, len(reviewers) + 1))
        return (SourceGroup(self.service, reviewers, slots, slots),)

    def observed(self, *, child=None):
        identity = copy.deepcopy(self.metadata["source_identity"])
        if child is not None:
            identity["reviewer_session_id"] = child
        return dict(source_identity=identity, answer_bytes=self.answer.raw, complete=True,
            terminal="completed", answer_source=source("answer_observation"), terminal_source=source("terminal_observation"))

    def call(self, arm="hook", *, caller=None, reviewers=None):
        arguments = (dict(event=dict(hook_event_name="SubagentStop", session_id=self.parent,
            agent_id=self.child, turn_id=TURN2, stop_hook_active=False)) if arm == "hook" else
            dict(task_id=self.task, reviewer_ids=reviewers or [self.child]))
        wire = _call(dict(name="review_answer_collect", arguments=arguments,
            _meta=self.caller if caller is None else caller), self.session)
        self.worker.assert_not_called()
        self.host._json_call.assert_not_called()
        return json.loads(wire["content"][0]["text"])

    def diagnostic(self, result, *, local, retained, recovery):
        self.assertFalse(result["ok"], result)
        value = result["diagnostic"]
        self.assertEqual(value["local_state"], local)
        self.assertEqual(value["retained_effects"], retained)
        self.assertEqual(value["recovery"], recovery)
        self.assertEqual(value["host_mutation"], "not_dispatched")
        self.assertEqual(value["cleanup"], "not_attempted")
        self.assertEqual(set(value), {"version", "stage", "reason", "local_state", "host_mutation",
            "retained_effects", "cleanup", "recovery", "turn_end"})
        self.assertLessEqual(len(json.dumps(value).encode()), 2048)
        self.assertNotIn(PRIVATE, json.dumps(result))
        self.assertIsNone(self.store._observation)

    def leave_unknown(self):
        real_write = files.write_exclusive
        def interrupted(path, raw):
            real_write(path, raw)
            raise OSError(PRIVATE)
        with patch.object(files, "write_exclusive", side_effect=interrupted), self.assertRaises(AnswerError):
            self.service.observe(1, self.observed())

    def test_hook_write_then_lost_ack_preserves_uncertainty(self):
        self.assert_publication_failure("hook")

    def test_parent_write_then_lost_ack_preserves_uncertainty(self):
        self.assert_publication_failure("parent")

    def assert_publication_failure(self, arm):
        real_write = files.write_exclusive
        def interrupted(path, raw):
            real_write(path, raw)
            raise OSError(PRIVATE)
        with patch.object(files, "write_exclusive", side_effect=interrupted):
            result = self.call(arm)
        self.diagnostic(result, local="may_have_changed", retained="present_unresolved", recovery="inspect_existing")
        failure = result["failure"] if arm == "hook" else result["unavailable"][0]["failure"]
        self.assertEqual(failure["class"], "save_unknown")
        self.assertEqual((self.root / self.metadata["original"]["path"]).read_bytes(), self.answer.raw)
        self.assertEqual(self.store.read()[0]["attempts"][0]["publication"]["state"], "unknown")
        self.adapter.observe_answer.assert_called_once()
        self.adapter.send_correction.assert_not_called()

    def test_parent_binding_commit_then_lost_ack_requires_inspection(self):
        self.store = CollectionRepository(self.root / "unbound.sqlite3")
        self.store.initialize((self.metadata["basis"],))
        self.service = CollectionService(self.root, self.store,
            original_paths=(self.metadata["original"]["path"],), admit_basis=Mock())
        binding = self.metadata["binding"]
        self.adapter.bind_reviewers.return_value = (dict(dispatch_id=binding["dispatch_id"],
            parent_session_id=self.parent, reviewer_session_id=self.child,
            initial_turn_id=binding["initial_turn_id"], source=source("dispatch_binding")),)
        connection = self.store._connection
        @contextmanager
        def lost_ack(*, write=False):
            with connection(write=write) as current:
                yield current
            if write:
                raise RepositoryError("state_unreadable")
        with patch.object(self.store, "_connection", new=lost_ack):
            result = self.call("parent")
        self.diagnostic(result, local="may_have_changed", retained="not_inspected", recovery="inspect_existing")
        self.assertEqual(self.store.read()[0]["binding"]["reviewer_session_id"], self.child)
        self.adapter.observe_answer.assert_not_called()

    def test_genuine_hook_caller_rejection_does_not_claim_local_write(self):
        result = self.call(caller=dict(threadId=self.child, turnId=TURN2))
        value = result["diagnostic"]
        self.assertEqual((value["local_state"], value["retained_effects"], value["host_mutation"]),
            ("not_attempted", "not_inspected", "not_dispatched"))
        self.assertEqual(result["error"], "caller_mismatch")
        self.adapter.observe_answer.assert_not_called()

    def test_parent_binding_identity_rejection_stays_prewrite(self):
        result = self.call("parent", reviewers=["90000000-0000-4000-8000-000000000001"])
        self.diagnostic(result, local="not_attempted", retained="absent", recovery="report_and_resume")
        self.assertEqual(result["error"], "binding_mismatch")
        self.adapter.observe_answer.assert_not_called()

    def test_preexisting_unknown_hook_is_read_only_and_requires_inspection(self):
        self.leave_unknown()
        before = self.store.read()
        result = self.call()
        self.diagnostic(result, local="not_attempted", retained="present_unresolved", recovery="inspect_existing")
        self.assertEqual(before, self.store.read())
        self.adapter.observe_answer.assert_not_called()

    def test_unknown_then_failed_read_keeps_recovery_without_stale_inventory(self):
        self.leave_unknown()
        connection = self.store._connection
        reads = []
        @contextmanager
        def fail_second(*, write=False):
            reads.append(write)
            if len(reads) == 2:
                raise RepositoryError("state_unreadable")
            with connection(write=write) as current:
                yield current
        with patch.object(self.store, "_connection", new=fail_second):
            result = self.call()
        self.diagnostic(result, local="not_attempted", retained="not_inspected", recovery="inspect_existing")
        self.adapter.observe_answer.assert_not_called()

    def test_parent_fresh_basis_failure_invalidates_earlier_absence(self):
        reader = Mock(side_effect=[self.current, RepositoryError("state_unreadable")])
        self.session.basis_factory = lambda *args, **kwargs: reader
        result = self.call("parent")
        self.diagnostic(result, local="not_attempted", retained="not_inspected", recovery="report_and_resume")
        self.adapter.observe_answer.assert_not_called()

    def test_parent_second_service_failure_keeps_unknown_recovery(self):
        self.leave_unknown()
        with patch("task_governance_tool.review_answer_runtime.sources_for_reviewers",
                   side_effect=[self.groups(), RepositoryError("state_unreadable")]):
            result = self.call("parent")
        self.diagnostic(result, local="not_attempted", retained="not_inspected", recovery="inspect_existing")
        self.adapter.observe_answer.assert_not_called()

    def test_parent_missing_second_service_does_not_certify_settlement(self):
        self.leave_unknown()
        with patch("task_governance_tool.review_answer_runtime.sources_for_reviewers",
                   side_effect=[self.groups(), None]):
            result = self.call("parent")
        self.diagnostic(result, local="not_attempted", retained="not_inspected", recovery="inspect_existing")
        self.adapter.observe_answer.assert_not_called()

    def test_persisted_observation_failure_reports_known_local_write(self):
        self.adapter.observe_answer.side_effect = AnswerError("capacity_exceeded", reason="bound")
        result = self.call()
        self.diagnostic(result, local="may_have_changed", retained="absent", recovery="report_and_resume")
        self.assertEqual(result["failure"]["class"], "capacity_exceeded")
        self.assertEqual(self.store.read()[0]["attempts"][0]["observation"], "unavailable")

    def test_successful_sibling_cannot_erase_unresolved_first_slot(self):
        second = "90000000-0000-4000-8000-000000000001"
        self.store = CollectionRepository(self.root / "two.sqlite3")
        self.store.initialize((self.metadata["basis"], self.metadata["basis"]))
        self.service = CollectionService(self.root, self.store,
            original_paths=(self.metadata["original"]["path"], ".tmp-review/second.json"), admit_basis=Mock())
        self.service.bind(1, self.metadata["binding"])
        binding = copy.deepcopy(self.metadata["binding"])
        binding.update(slot=2, reviewer_key="second", reviewer_session_id=second)
        self.service.bind(2, binding)
        self.leave_unknown()
        # Explicit parent recovery cannot settle partial residue.
        (self.root / self.metadata["original"]["path"]).write_bytes(b"partial")
        self.adapter.observe_answer.return_value = self.observed(child=second)
        result = self.call("parent", reviewers=[self.child, second])
        self.diagnostic(result, local="may_have_changed", retained="present_unresolved", recovery="inspect_existing")
        self.assertEqual(result["collected"], [2])
        self.assertEqual(result["unavailable"][0]["slot"], 1)

    def test_failed_lease_release_invalidates_settled_inventory(self):
        lease = self.store._lease
        @contextmanager
        def failed_release():
            with lease() as value:
                yield value
            raise RepositoryError("state_unreadable")
        with patch.object(self.store, "_lease", new=failed_release):
            result = self.call()
        self.diagnostic(result, local="may_have_changed", retained="not_inspected", recovery="inspect_existing")
        self.assertEqual(self.store.read()[0]["attempts"][0]["publication"]["state"], "saved")

    def test_successful_reconciliation_clears_only_same_inventory_uncertainty(self):
        self.leave_unknown()
        diagnostic = CallDiagnostics()
        with diagnostic.collecting(self.store):
            self.store.read()
            self.assertEqual(diagnostic.retained_effects, "present_unresolved")
            self.service.reconcile_publication(1)
            self.assertEqual(diagnostic.retained_effects, "settled")
        result = diagnostic.failure({"ok": False, "error": "unavailable"})
        self.assertEqual(result["diagnostic"]["recovery"], "report_and_resume")
        self.assertIsNone(self.store._observation)

    def test_unknown_correction_is_not_a_mutation_of_this_call(self):
        self.assert_correction_unknown("unknown")

    def test_accepted_correction_without_turn_remains_unresolved(self):
        self.assert_correction_unknown("accepted")

    def assert_correction_unknown(self, effect):
        observation = self.observed()
        observation["answer_bytes"] = self.answer.raw.replace(b'"pass"', b'"maybe"')
        with self.assertRaises(AnswerError):
            self.service.observe(1, observation)
        correction_host = Mock()
        correction_host.send_correction.return_value = dict(effect=effect, correction_turn_id=None, source=None)
        self.service.correction(1, correction_host, observer=lambda _row: True)
        result = self.call()
        self.diagnostic(result, local="not_attempted", retained="present_unresolved", recovery="inspect_existing")
        self.adapter.observe_answer.assert_not_called()
        self.adapter.send_correction.assert_not_called()

    def test_registration_dispatched_and_unknown_remain_unresolved(self):
        self.service.observe(1, self.observed())
        digest = "sha256:" + "d" * 64
        self.service.mark_registration((1,), digest)
        for unknown in (False, True):
            if unknown:
                with self.store.serial() as lease:
                    self.service.unknown_registration_locked(lease, (1,), digest)
            with self.subTest(unknown=unknown):
                diagnostic = CallDiagnostics()
                with diagnostic.collecting(self.store):
                    self.store.read()
                result = diagnostic.failure({"ok": False, "error": "unavailable"})
                self.diagnostic(result, local="not_attempted", retained="present_unresolved", recovery="inspect_existing")

    def wait_session(self, *, ended=False):
        binding = replace(BINDING, parent_thread_id=self.parent, task_id=self.task)
        self.session._reader = lambda *args, **kwargs: lambda: binding
        self.session._finalizer = Mock(return_value=None)
        self.session._wait_round_current = Mock()
        self.session._enabled = Mock(return_value=True)
        self.host.read_child.return_value = ChildTurn(self.child, self.metadata["binding"]["initial_turn_id"],
            "completed" if ended else "inProgress", "idle" if ended else "active")
        self.host.read_parent.return_value = ChildTurn(self.parent, TURN2, "inProgress", "active")
        paths = canonical_state_paths(self.root / "skill", repo=self.root)
        paths.fixed_root.mkdir(parents=True)
        self.session._location.return_value = paths
        return paths

    def wait(self):
        return self.session.handle("wait", dict(task_id=self.task, reviewer_ids=[self.child]), self.caller)

    def test_wait_binding_write_before_child_read_failure_is_observed(self):
        self.wait_session()
        self.store = CollectionRepository(self.root / "wait-unbound.sqlite3")
        self.store.initialize((self.metadata["basis"],))
        self.service = CollectionService(self.root, self.store,
            original_paths=(self.metadata["original"]["path"],), admit_basis=Mock())
        binding = self.metadata["binding"]
        self.adapter.bind_reviewers.return_value = (dict(dispatch_id=binding["dispatch_id"],
            parent_session_id=self.parent, reviewer_session_id=self.child,
            initial_turn_id=binding["initial_turn_id"], source=source("dispatch_binding")),)
        self.host.read_child.side_effect = HostAdapterError("invalid_host_response")
        result = self.wait()
        self.diagnostic(result, local="may_have_changed", retained="absent", recovery="report_and_resume")
        self.assertIsNotNone(self.store.read()[0]["binding"])
        self.host.create_heartbeat.assert_not_called()

    def test_wait_unknown_collection_survives_later_request_absence(self):
        self.wait_session()
        self.leave_unknown()
        with patch("task_governance_tool.review_wait_runtime.managed_wait.ten_minute_appointment",
                   side_effect=DiagnosticError("invalid_configuration")):
            result = self.wait()
        self.diagnostic(result, local="may_have_changed", retained="present_unresolved", recovery="inspect_existing")
        self.host.create_heartbeat.assert_not_called()
        self.adapter.observe_answer.assert_not_called()

    def test_wait_ended_collection_write_then_failure_retains_unknown(self):
        self.wait_session(ended=True)
        real_write = files.write_exclusive
        def interrupted(path, raw):
            real_write(path, raw)
            raise OSError(PRIVATE)
        with patch.object(files, "write_exclusive", side_effect=interrupted), \
                patch.object(self.session, "_collect_answers", wraps=self.session._collect_answers) as collect, \
                patch.object(self.session, "_ensure_root", side_effect=RepositoryError("state_unreadable")):
            result = self.wait()
        self.assertIsInstance(collect.call_args.kwargs["diagnostic"], CallDiagnostics)
        self.diagnostic(result, local="may_have_changed", retained="not_inspected", recovery="inspect_existing")
        self.assertEqual(result["answer_collection"]["unavailable"][0]["failure"]["class"], "save_unknown")
        self.assertEqual(self.store.read()[0]["attempts"][0]["publication"]["state"], "unknown")
        self.adapter.observe_answer.assert_called_once()
        self.host.create_heartbeat.assert_not_called()

    def test_wait_ended_collection_preserves_closed_failure_category(self):
        self.wait_session(ended=True)
        error = AnswerError("privacy_rejected", field="summary", reason="privacy")
        with patch.object(self.session, "_collect_answers", side_effect=error):
            result = self.wait()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "reviews_ended")
        self.assertEqual(result["answer_collection"]["unavailable"][0]["failure"], error.failure)
        self.host.create_heartbeat.assert_not_called()

    def test_settled_collection_cannot_erase_unresolved_wait_record(self):
        self.service.observe(1, self.observed())
        diagnostic = CallDiagnostics()
        diagnostic.observe_record(SimpleNamespace(phase="started"))
        with diagnostic.collecting(self.store):
            self.store.read()
        result = diagnostic.failure({"ok": False, "error": "unavailable"})
        self.diagnostic(result, local="not_attempted", retained="present_unresolved", recovery="inspect_existing")

    def test_wait_noncollection_binding_preserves_request_absence(self):
        self.wait_session()
        self.host.read_child.side_effect = HostAdapterError("invalid_host_response")
        with patch("task_governance_tool.review_answer_runtime.sources_for_reviewers", return_value=None):
            result = self.wait()
        self.diagnostic(result, local="not_attempted", retained="absent", recovery="report_and_resume")
        self.adapter.bind_reviewers.assert_not_called()
        self.adapter.observe_answer.assert_not_called()

    def test_noncollection_helpers_do_not_settle_previously_observed_unknown(self):
        self.leave_unknown()
        diagnostic = CallDiagnostics()
        with diagnostic.collecting(self.store):
            self.store.read()
        with patch("task_governance_tool.review_answer_runtime.sources_for_reviewers", return_value=None):
            self.assertIsNone(self.session._bind_answers(self.current, self.host, [self.child],
                fresh_context=False, diagnostic=diagnostic))
            self.assertIsNone(self.session._collect_answers(self.current, self.host,
                [self.child], diagnostic=diagnostic))
        result = diagnostic.failure({"ok": False, "error": "unavailable"})
        self.diagnostic(result, local="not_attempted", retained="present_unresolved", recovery="inspect_existing")


if __name__ == "__main__":
    unittest.main()
