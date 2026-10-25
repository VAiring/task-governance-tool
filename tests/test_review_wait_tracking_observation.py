"""Admitted collection locations supply hints without repeated core reads."""

from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_review_answer_tracking_collection import tracking_fixture
from task_governance_tool import review_answer_runtime as runtime
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection_repository import CollectionRepository, initial_record
from task_governance_tool.review_answer_host import PublicAnswerHost
from task_governance_tool.review_wait_runtime.review_wait_repository import ObservationBusy, RepositoryError


class TrackingObservationTests(unittest.TestCase):
    def setUp(self):
        self.metadata, self.answer = tracking_fixture()
        self.child = self.metadata["binding"]["reviewer_session_id"]
        record = initial_record(self.metadata["basis"])
        record["binding"] = deepcopy(self.metadata["binding"])
        self.records = [record]
        self.repository = SimpleNamespace(
            read=Mock(side_effect=lambda: deepcopy(self.records)),
            read_observation=Mock(side_effect=lambda: deepcopy(self.records)))
        self.service = SimpleNamespace(repository=self.repository, check_basis=Mock())
        self.group = runtime.SourceGroup(self.service, (self.child,), (1,), (1,))
        self.allowed = self.enterContext(patch(
            "task_governance_tool.setup_feature_config.answer_collection_allowed", return_value=True))
        self.sources = self.enterContext(patch.object(runtime, "sources_for_reviewers",
            side_effect=lambda *args, observe_policy=None: self.observe_sources(observe_policy)))
        self.groups = (self.group,)
        self.repo, self.skill, self.binding = object(), object(), object()

    def observer(self):
        return runtime.tracking_observer_for_reviewers(
            self.repo, self.skill, self.binding, (self.child,))

    def observe_sources(self, observe_policy):
        if observe_policy is not None:
            observe_policy(self.allowed())
        return self.groups

    def assert_binding_failure(self, operation):
        with self.assertRaises(AnswerError) as raised:
            operation()
        self.assertEqual(raised.exception.failure["class"], "binding_mismatch")

    def public_host(self, *, raw=None, status="completed", turn_id=None):
        turn_id = turn_id or self.metadata["source_identity"]["turn_id"]
        final = dict(type="agentMessage", id="message-1", phase="final_answer",
            text=(self.answer.raw if raw is None else raw).decode("utf-8"))
        page = dict(schemaVersion=1, thread=dict(id=self.child, kind="codex", hostId="local"),
            page=dict(order="newest_first", limit=2, hasMore=False, nextCursor=None),
            turns=[dict(id=turn_id, status=status, items=[final], error=None,
                startedAt=1, completedAt=2 if status != "inProgress" else None, durationMs=1)])
        public = SimpleNamespace(_json_call=Mock(return_value=page))
        host = PublicAnswerHost(public, self.metadata["binding"]["parent_session_id"], turn_id)
        return host, public, page

    def recover_host_candidate(self, slot, host, **_kwargs):
        binding = self.metadata["binding"]
        return host.observe_latest_answer(basis_digest=binding["basis_digest"], slot=slot,
            reviewer_session_id=self.child, boundary_turn_id=binding["tracking"]["boundary_turn_id"],
            boundary=binding["tracking"]["boundary"])

    def collection_host(self, observe, public):
        return observe.make_host(public, self.metadata["binding"]["parent_session_id"],
            self.metadata["binding"]["tracking"]["boundary_turn_id"])

    def test_creation_is_fresh_and_repeated_observation_never_readmits_core(self):
        observe = self.observer()
        self.sources.assert_called_once()
        self.assertEqual(self.sources.call_args.args, (self.repo, self.skill, self.binding, (self.child,)))
        self.service.check_basis.assert_called_once_with(self.metadata["basis"], 1)
        self.repository.read.assert_called_once_with()
        for _ in range(100):
            result = observe()
            self.assertEqual(result, {self.child: dict(
                boundary_turn_id=self.metadata["binding"]["tracking"]["boundary_turn_id"],
                frozen=False, source_identity=None, terminal="unknown")})
        self.assertEqual(self.repository.read_observation.call_count, 100)
        self.sources.assert_called_once()
        self.service.check_basis.assert_called_once()
        self.repository.read.assert_called_once()

    def test_completed_frozen_result_is_observed_without_core_read_and_is_not_aliased(self):
        observe = self.observer()
        self.assertFalse(observe()[self.child]["frozen"])
        row = self.records[0]
        row["tracking_state"] = "frozen"
        row["attempts"][0].update(source_identity=deepcopy(self.metadata["source_identity"]), terminal="completed")
        result = observe()[self.child]
        self.assertTrue(result["frozen"])
        self.assertEqual(result["terminal"], "completed")
        self.assertEqual(result["source_identity"], self.metadata["source_identity"])
        result["source_identity"]["message_id"] = "caller-modified"
        self.assertEqual(observe()[self.child]["source_identity"], self.metadata["source_identity"])
        self.service.check_basis.assert_called_once()

    def test_invalidated_source_rejects_both_admission_and_later_observation(self):
        observe = self.observer()
        self.records[0]["tracking_state"] = "invalidated"
        self.assert_binding_failure(observe)
        self.assert_binding_failure(self.observer)

    def test_basis_binding_and_reviewer_changes_do_not_inherit_admission(self):
        original = deepcopy(self.records[0])
        changes = (
            lambda row: row["basis"].update(packet_digest="sha256:" + "e" * 64),
            lambda row: row["binding"]["tracking"].update(boundary_turn_id="99999999-9999-4999-8999-999999999999"),
            lambda row: row["binding"].update(reviewer_session_id="99999999-9999-4999-8999-999999999999"),
            lambda row: row.update(binding=None),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                self.records = [deepcopy(original)]
                observe = self.observer()
                change(self.records[0])
                self.assert_binding_failure(observe)

    def test_initial_unbound_slot_needs_new_source_admission_after_binding(self):
        self.records[0]["binding"] = None
        observe = self.observer()
        for _ in range(100):
            self.assertEqual(observe(), {})
        self.records[0]["binding"] = deepcopy(self.metadata["binding"])
        with self.assertRaises(runtime.TrackingReadmissionRequired):
            observe()
        self.sources.assert_called_once()
        self.service.check_basis.assert_called_once()
        # Only an explicit fresh construction can admit the newly bound slot.
        renewed = self.observer()
        self.assertIn(self.child, renewed())
        self.assertEqual(self.sources.call_count, 2)
        self.assertEqual(self.service.check_basis.call_count, 2)
        with self.assertRaises(runtime.TrackingReadmissionRequired):
            observe()

    def test_new_binding_for_wrong_reviewer_is_failure_not_readmission(self):
        self.records[0]["binding"] = None
        observe = self.observer()
        self.records[0]["binding"] = deepcopy(self.metadata["binding"])
        self.records[0]["binding"]["reviewer_session_id"] = "99999999-9999-4999-8999-999999999999"
        self.assert_binding_failure(observe)

    def test_unbound_continuation_is_not_optional_initial_collection(self):
        metadata, _answer = tracking_fixture(continuation=True)
        self.records = [initial_record(metadata["basis"])]
        self.assert_binding_failure(self.observer)

    def test_inventory_change_is_rejected_before_reusing_slot_mapping(self):
        observe = self.observer()
        self.records.append(deepcopy(self.records[0]))
        self.assert_binding_failure(observe)

    def test_busy_is_propagated_without_retry_or_fresh_core_read(self):
        observe = self.observer()
        self.repository.read_observation.side_effect = ObservationBusy()
        with self.assertRaises(ObservationBusy):
            observe()
        self.repository.read_observation.assert_called_once()
        self.sources.assert_called_once()
        self.service.check_basis.assert_called_once()
        self.repository.read.assert_called_once()
        self.repository.read_observation.side_effect = lambda: deepcopy(self.records)
        self.assertIn(self.child, observe())

    def test_nonapplicable_sources_return_an_empty_observer(self):
        self.groups = None
        observe = self.observer()
        for _ in range(100):
            self.assertEqual(observe(), {})
        self.repository.read.assert_not_called()
        self.repository.read_observation.assert_not_called()
        self.service.check_basis.assert_not_called()

    def test_failed_initial_admission_does_not_produce_an_observer(self):
        self.service.check_basis.side_effect = AnswerError("binding_mismatch", reason="identity")
        self.assert_binding_failure(self.observer)
        self.repository.read_observation.assert_not_called()

    def test_collection_repository_inherits_bounded_read_observation(self):
        with tempfile.TemporaryDirectory(prefix="taskgov-tracking-observation-") as directory:
            repository = CollectionRepository(Path(directory) / "collection.sqlite")
            expected = repository.initialize([self.metadata["basis"]])
            with patch.object(repository, "_lease", side_effect=AssertionError("observation acquired writer lease")):
                actual = repository.read_observation()
            self.assertIsInstance(actual, tuple)
            self.assertEqual(actual, expected)

    def test_candidate_probe_never_repeats_core_or_parses_review_json(self):
        observe = self.observer()
        host, public, _page = self.public_host()
        with patch("task_governance_tool.review_answer.decode_answer",
                side_effect=AssertionError("progress probe decoded review JSON")):
            for index in range(100):
                observe()
                self.assertEqual(observe.candidate_changed(host, self.child), index == 0)
        self.assertEqual(self.repository.read_observation.call_count, 100)
        self.assertEqual(public._json_call.call_count, 100)
        self.sources.assert_called_once()
        self.service.check_basis.assert_called_once()
        self.repository.read.assert_called_once()
        for call in public._json_call.call_args_list:
            self.assertEqual(call.args[0], "read_thread")
            self.assertEqual(call.args[1], dict(threadId=self.child, hostId="local", turnLimit=2,
                includeOutputs=False, maxOutputCharsPerItem=256))

    def test_candidate_compares_same_message_bytes_without_retaining_body(self):
        observe = self.observer()
        host, _public, page = self.public_host(raw=b"not a review")
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertFalse(observe.candidate_changed(host, self.child))
        page["turns"][0]["items"][0]["text"] = self.answer.raw.decode()
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertFalse(observe.candidate_changed(host, self.child))
        # Process-local keys contain only bounded source fields and an opaque
        # digest, never either candidate's bytes or parsed payload.
        key = observe._candidate_keys[self.child]
        self.assertEqual(len(key[4]), 32)
        self.assertNotIn(self.answer.raw, key)
        self.assertNotIn(b"not a review", key)
        self.assertNotIn("answer_bytes", vars(observe))

    def test_incomplete_same_turn_is_reprobed_until_complete(self):
        observe = self.observer()
        host, public, page = self.public_host()
        page["turns"][0]["items"][0]["truncated"] = True
        with patch.object(runtime.hashlib, "sha256", wraps=runtime.hashlib.sha256) as digest:
            self.assertTrue(observe.candidate_changed(host, self.child))
            for _ in range(10):
                self.assertFalse(observe.candidate_changed(host, self.child))
            digest.assert_not_called()
        page["turns"][0]["items"][0]["truncated"] = False
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertFalse(observe.candidate_changed(host, self.child))
        self.assertEqual(public._json_call.call_count, 13)
        self.service.check_basis.assert_called_once()

    def test_running_is_healthy_pending_and_missing_final_can_arrive_later(self):
        observe = self.observer()
        host, _public, page = self.public_host(status="inProgress")
        for _ in range(10):
            self.assertFalse(observe.candidate_changed(host, self.child))
        turn = page["turns"][0]
        final = turn["items"].pop()
        turn.update(status="completed", completedAt=2)
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertFalse(observe.candidate_changed(host, self.child))
        turn["items"].append(final)
        self.assertTrue(observe.candidate_changed(host, self.child))

    def test_transport_failure_is_bounded_and_recovery_is_a_new_candidate(self):
        observe = self.observer()
        host, public, _page = self.public_host()
        public._json_call.side_effect = RuntimeError("private transport detail")
        self.assertTrue(observe.candidate_changed(host, self.child))
        for _ in range(10):
            self.assertFalse(observe.candidate_changed(host, self.child))
        self.assertEqual(observe._candidate_keys[self.child],
            ("unavailable", "collection_unavailable", None, "transport_unavailable"))
        public._json_call.side_effect = None
        self.assertTrue(observe.candidate_changed(host, self.child))

    def test_new_source_identity_is_a_candidate_even_with_identical_bytes(self):
        observe = self.observer()
        host, _public, page = self.public_host()
        self.assertTrue(observe.candidate_changed(host, self.child))
        page["turns"][0]["items"][0]["id"] = "message-2"
        self.assertTrue(observe.candidate_changed(host, self.child))
        page["turns"][0]["id"] = "10000000-0000-4000-8000-000000000002"
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertFalse(observe.candidate_changed(host, self.child))

    def test_frozen_unknown_publication_never_probes_or_retries_host(self):
        observe = self.observer()
        host, public, _page = self.public_host()
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.records[0]["tracking_state"] = "frozen"
        self.records[0]["attempts"][0]["publication"] = dict(state="unknown")
        observe()
        observe.retry_unprocessed(self.child)
        observe()
        for _ in range(10):
            self.assertFalse(observe.candidate_changed(host, self.child))
        public._json_call.assert_called_once()

    def test_basis_change_discards_candidate_hints_and_fresh_observer_reconsiders(self):
        observe = self.observer()
        host, public, _page = self.public_host()
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.records[0]["basis"]["packet_digest"] = "sha256:" + "e" * 64
        self.assert_binding_failure(observe)
        self.assertEqual(observe._candidate_keys, {})
        self.assertFalse(observe.candidate_changed(host, self.child))
        public._json_call.assert_called_once()
        renewed = self.observer()
        self.assertTrue(renewed.candidate_changed(host, self.child))
        self.assertEqual(self.sources.call_count, 2)

    def test_continuation_boundary_is_preserved_during_host_probe(self):
        metadata, self.answer = tracking_fixture(continuation=True)
        self.metadata = metadata
        record = initial_record(metadata["basis"])
        record["binding"] = deepcopy(metadata["binding"])
        self.records = [record]
        observe = self.observer()
        host, _public, page = self.public_host(turn_id=metadata["binding"]["tracking"]["boundary_turn_id"])
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertFalse(observe.candidate_changed(host, self.child))
        self.assertEqual(observe._candidate_keys[self.child][0], "unavailable")
        page["turns"][0]["id"] = metadata["source_identity"]["turn_id"]
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertEqual(observe._candidate_keys[self.child][0], "complete")

    def test_collection_does_not_infer_retryability_from_generic_or_effect_failure(self):
        callback = Mock()
        self.service.recover = Mock(side_effect=RepositoryError("writer_busy"))
        result = runtime.collect_sources(self.group, object(), on_retryable=callback)
        self.assertEqual(result, dict(collected=[], unavailable=[dict(slot=1,
            failure={"class": "collection_unavailable", "field": None, "reason": "incomplete"})]))
        callback.assert_called_once_with(self.child)
        for error in (RepositoryError("state_unreadable"), RuntimeError("writer_busy"),
                AnswerError("format_invalid", reason="shape"),
                AnswerError("privacy_rejected", reason="privacy"),
                AnswerError("capacity_exceeded", reason="bound"),
                AnswerError("binding_mismatch", reason="conflict"),
                AnswerError("collection_unavailable", reason="incomplete"),
                AnswerError("collection_unavailable", reason="effect_unknown"),
                AnswerError("collection_unavailable", reason="observer_unavailable"),
                AnswerError("collection_unavailable", field="summary", reason="shape"),
                AnswerError("save_unknown", reason="effect_unknown"),
                AnswerError("registration_unknown", reason="effect_unknown")):
            with self.subTest(error=type(error).__name__, code=str(error)):
                callback.reset_mock()
                self.service.recover.side_effect = error
                self.assertEqual(len(runtime.collect_sources(self.group, object(),
                    on_retryable=callback)["unavailable"]), 1)
                callback.assert_not_called()

    def test_collector_read_failure_replaces_probe_key_without_retry_callback(self):
        for reason in ("missing", "incomplete", "transport_unavailable"):
            with self.subTest(reason=reason):
                observe = self.observer()
                host, public, _page = self.public_host()
                self.assertTrue(observe.candidate_changed(host, self.child))
                error = AnswerError("collection_unavailable", reason=reason)
                public._json_call.side_effect = error
                self.service.recover = Mock(side_effect=self.recover_host_candidate)
                callback = Mock()
                result = runtime.collect_sources(self.group, self.collection_host(observe, public),
                    on_retryable=callback)
                self.assertEqual(result, dict(collected=[], unavailable=[dict(slot=1, failure=error.failure)]))
                callback.assert_not_called()
                self.assertFalse(observe.candidate_changed(host, self.child))
                public._json_call.side_effect = None
                observe()
                self.assertTrue(observe.candidate_changed(host, self.child))

    def test_collector_other_invalid_body_does_not_mark_probe_body_handled(self):
        observe = self.observer()
        host, public, page = self.public_host()
        self.assertTrue(observe.candidate_changed(host, self.child))
        page["turns"][0]["items"][0]["text"] = "different invalid review"
        def recover(slot, adapter, **kwargs):
            self.recover_host_candidate(slot, adapter, **kwargs)
            raise AnswerError("format_invalid", reason="shape")
        self.service.recover = Mock(side_effect=recover)
        callback = Mock()
        result = runtime.collect_sources(self.group, self.collection_host(observe, public),
            on_retryable=callback)
        self.assertEqual(result["unavailable"][0]["failure"]["class"], "format_invalid")
        callback.assert_not_called()
        self.assertFalse(observe.candidate_changed(host, self.child))
        page["turns"][0]["items"][0]["text"] = self.answer.raw.decode()
        self.assertTrue(observe.candidate_changed(host, self.child))

    def test_collection_host_does_not_read_another_admitted_source(self):
        observe = self.observer()
        _host, public, _page = self.public_host()
        adapter = self.collection_host(observe, public)
        binding = self.metadata["binding"]
        self.assert_binding_failure(lambda: adapter.observe_latest_answer(
            basis_digest="sha256:" + "e" * 64, slot=1, reviewer_session_id=self.child,
            boundary_turn_id=binding["tracking"]["boundary_turn_id"], boundary=binding["tracking"]["boundary"]))
        public._json_call.assert_not_called()

    def test_known_invalid_answer_remains_handled_until_candidate_changes(self):
        observe = self.observer()
        host, _public, page = self.public_host(raw=b"invalid review")
        self.assertTrue(observe.candidate_changed(host, self.child))
        error = AnswerError("format_invalid", reason="shape")
        self.service.recover = Mock(side_effect=error)
        runtime.collect_sources(self.group, host, on_retryable=observe.retry_unprocessed)
        self.records[0]["attempts"][0].update(observation="complete", failure=error.failure)
        observe()
        self.assertFalse(observe.candidate_changed(host, self.child))
        page["turns"][0]["items"][0]["text"] = self.answer.raw.decode()
        self.assertTrue(observe.candidate_changed(host, self.child))

    def test_writer_busy_retries_new_candidate_even_after_an_older_rejection(self):
        self.records[0]["attempts"][0].update(observation="complete",
            source_identity=deepcopy(self.metadata["source_identity"]),
            failure={"class": "format_invalid", "field": None, "reason": "shape"})
        observe = self.observer()
        host, _public, _page = self.public_host()
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.service.recover = Mock(side_effect=RepositoryError("writer_busy"))
        runtime.collect_sources(self.group, host, on_retryable=observe.retry_unprocessed)
        self.repository.read_observation.assert_not_called()
        self.assertFalse(observe.candidate_changed(host, self.child))
        observe()
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertFalse(observe.candidate_changed(host, self.child))
        self.service.check_basis.assert_called_once()
        self.assertEqual(self.records[0]["attempts"][0]["failure"]["class"], "format_invalid")

    def test_retry_request_survives_busy_operational_read_until_fresh_snapshot(self):
        observe = self.observer()
        host, _public, _page = self.public_host()
        self.assertTrue(observe.candidate_changed(host, self.child))
        observe.retry_unprocessed(self.child)
        self.repository.read_observation.side_effect = ObservationBusy()
        with self.assertRaises(ObservationBusy):
            observe()
        self.assertFalse(observe.candidate_changed(host, self.child))
        self.repository.read_observation.side_effect = lambda: deepcopy(self.records)
        observe()
        self.assertTrue(observe.candidate_changed(host, self.child))
        self.assertEqual(observe._retry_requested, {})

    def test_retry_request_cannot_replay_publication_or_registration(self):
        original = deepcopy(self.records[0])
        changes = (
            lambda row: row.update(tracking_state="frozen"),
            lambda row: row["attempts"][0].update(publication={"state": "intent"}),
            lambda row: row["attempts"][0].update(publication={"state": "unknown"}),
            lambda row: row["registration"].update(state="intent"),
            lambda row: row["registration"].update(state="unknown"),
            lambda row: row["registration"].update(state="registered"),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                self.records = [deepcopy(original)]
                observe = self.observer()
                host, _public, _page = self.public_host()
                self.assertTrue(observe.candidate_changed(host, self.child))
                candidate = observe._candidate_keys[self.child]
                observe.retry_unprocessed(self.child)
                change(self.records[0])
                observe()
                self.assertEqual(observe._candidate_keys[self.child], candidate)
                self.assertEqual(observe._retry_requested, {})

    def test_old_retry_request_does_not_reset_a_changed_candidate(self):
        observe = self.observer()
        host, _public, page = self.public_host()
        self.assertTrue(observe.candidate_changed(host, self.child))
        observe.retry_unprocessed(self.child)
        page["turns"][0]["items"][0]["text"] += " "
        self.assertTrue(observe.candidate_changed(host, self.child))
        observe()
        self.assertFalse(observe.candidate_changed(host, self.child))

    def test_retry_request_for_same_unavailable_never_repeats_core_collection(self):
        observe = self.observer()
        host, _public, page = self.public_host()
        page["turns"][0]["items"][0]["truncated"] = True
        self.assertTrue(observe.candidate_changed(host, self.child))
        observe.retry_unprocessed(self.child)
        observe()
        self.assertFalse(observe.candidate_changed(host, self.child))
        self.assertEqual(observe._retry_requested, {})


if __name__ == "__main__":
    unittest.main()
