"""Offline collection state/I/O boundaries and interruption recovery."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection import CollectionService, source
from task_governance_tool.review_answer_collection_repository import CollectionRepository
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool import review_handoff_transport as files
from task_governance_tool.review_answer_runtime import collect_bound, collect_hook_event, SourceGroup
from task_governance_tool.review_answer_values import validate_metadata
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_server import _call
from test_review_answer_pure_values import metadata_fixture, TURN2


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".tmp-review").mkdir()
        self.metadata, self.answer = metadata_fixture()
        self.store = CollectionRepository(self.root / "collection.sqlite3")
        self.store.initialize((self.metadata["basis"],))
        self.admit = Mock()
        self.service = CollectionService(self.root, self.store,
            original_paths=(self.metadata["original"]["path"],), admit_basis=self.admit)
        self.service.bind(1, self.metadata["binding"])
        # The shared transport's Git-ignore preflight has its own integration
        # suite. These tests retain its real physical path and exclusive I/O.
        self.ignore = patch.object(files, "require_ignored")
        self.ignore.start()
        self.addCleanup(self.ignore.stop)

    def observed(self, raw=None, *, turn=None, terminal="completed"):
        identity = copy.deepcopy(self.metadata["source_identity"])
        if turn is not None:
            identity["turn_id"] = turn
        return dict(source_identity=identity, answer_bytes=self.answer.raw if raw is None else raw,
                    complete=True, terminal=terminal, answer_source=source("answer_observation"),
                    terminal_source=source("terminal_observation"))

    def parent_collect(self):
        binding = self.metadata["binding"]
        task_id = self.metadata["basis"]["execution_basis"]["task_id"]
        current = SimpleNamespace(task_id=task_id, parent_thread_id=binding["parent_session_id"])
        metadata = dict(threadId=binding["parent_session_id"], turnId=TURN2)
        host = Mock(_metadata=metadata)
        worker = Mock(side_effect=AssertionError("worker path invoked"))
        session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "host.mjs", self.root, "UTC"),
            basis_factory=lambda *args, **kwargs: lambda: current,
            managed_host_factory=lambda **kwargs: host, session_factory=worker)
        paths = SimpleNamespace(review_wait_request_store=lambda *args: self.root / "absent.sqlite3")
        with patch.object(session, "_location", return_value=paths), \
                patch("task_governance_tool.setup_feature_config.answer_collection_allowed", return_value=True), \
                patch("task_governance_tool.review_answer_runtime.sources_for_reviewers",
                    return_value=(SourceGroup(self.service, (binding["reviewer_session_id"],), (1,), (1,)),)):
            result = _call(dict(name="review_answer_collect", arguments=dict(task_id=task_id,
                reviewer_ids=[binding["reviewer_session_id"]]), _meta=metadata), session)
        host._json_call.assert_not_called()
        worker.assert_not_called()
        self.assertEqual(session.sessions, {})
        return json.loads(result["content"][0]["text"])

    def test_saved_bytes_restart_and_duplicate_do_not_publish_again(self):
        raw = b" \r\n```json\r\n" + json.dumps(dict(verdict="pass", summary="日本語", findings=[], checks=[], limitations=[]), ensure_ascii=False).encode() + b"\r\n```\r\n"
        with patch.object(files, "write_exclusive", wraps=files.write_exclusive) as write:
            self.service.observe(1, self.observed(raw))
            self.service.observe(1, self.observed(raw))
            self.assertEqual(write.call_count, 1)
        restarted = CollectionRepository(self.store.path)
        self.assertEqual(restarted.read(), self.store.read())
        with patch.object(files, "write_exclusive") as write:
            self.assertEqual(self.service.readback(1)[0], raw)
            restarted.initialize((self.metadata["basis"],))
            self.assertFalse(write.called)
        self.assertNotIn("日本語".encode(), self.store.path.read_bytes())

    def test_conflict_keeps_first_original_and_latches(self):
        self.service.observe(1, self.observed())
        before = self.store.read()[0]["attempts"][0]
        changed = self.answer.raw.replace(b"okay", b"different")
        with self.assertRaises(AnswerError):
            self.service.observe(1, self.observed(changed))
        after = self.store.read()[0]["attempts"][0]
        self.assertEqual(after["observation"], "conflict")
        self.assertEqual(after["publication"], before["publication"])
        self.assertEqual(after["observed_digest"], before["observed_digest"])
        self.assertEqual((self.root / self.metadata["original"]["path"]).read_bytes(), self.answer.raw)
        self.assert_conflict_recovery_failure("binding_mismatch", "conflict")

    def assert_conflict_recovery_failure(self, classification, reason):
        expected = dict(**{"class": classification}, field=None, reason=reason)
        before = self.store.read()
        journal = self.store.path.read_bytes()
        original = self.root / self.metadata["original"]["path"]
        host = Mock()
        event = dict(hook_event_name="SubagentStop", session_id=self.metadata["binding"]["parent_session_id"],
            agent_id=self.metadata["binding"]["reviewer_session_id"], turn_id=TURN2, stop_hook_active=False)
        with patch.object(files, "write_exclusive") as write, \
                patch.object(self.store, "update", wraps=self.store.update) as update, \
                patch.object(self.service, "reconcile_publication", wraps=self.service.reconcile_publication) as reconcile:
            for explicit in (False, True):
                with self.subTest(explicit_recovery=explicit):
                    with self.assertRaises(AnswerError) as raised:
                        self.service.recover(1, host, explicit_recovery=explicit)
                    self.assertEqual(raised.exception.failure, expected)
                    result = collect_bound(self.service, host, explicit_recovery=explicit)
                    self.assertEqual(result, dict(collected=[], unavailable=[dict(slot=1, failure=expected)]))
            with self.assertRaises(AnswerError) as raised:
                collect_hook_event(event, service=self.service, host=host)
            self.assertEqual(raised.exception.failure, expected)
            for _ in range(2):
                result = self.parent_collect()
                self.assertFalse(result["ok"])
                self.assertEqual(result["unavailable"], [dict(slot=1, failure=expected)])
            with self.assertRaises(AnswerError) as raised:
                self.service.readback(1)
            self.assertEqual(raised.exception.failure, expected)
            write.assert_not_called()
            update.assert_not_called()
            reconcile.assert_not_called()
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.store.path.read_bytes(), journal)
        self.assertEqual(original.read_bytes(), self.answer.raw)
        self.assertEqual(host.mock_calls, [])

    def assert_interrupted_publication_conflict(self, interruption, state):
        real_write = files.write_exclusive
        def interrupted(path, raw):
            real_write(path, raw)
            raise interruption()
        expected_error = AnswerError if issubclass(interruption, Exception) else interruption
        with patch.object(files, "write_exclusive", side_effect=interrupted), self.assertRaises(expected_error):
            self.service.observe(1, self.observed())
        before = self.store.read()[0]["attempts"][0]
        self.assertEqual(before["publication"]["state"], state)
        with self.assertRaises(AnswerError):
            self.service.observe(1, self.observed(self.answer.raw.replace(b"okay", b"different")))
        after = self.store.read()[0]["attempts"][0]
        self.assertEqual(after["observation"], "conflict")
        self.assertEqual(after["failure"], dict(**{"class": "binding_mismatch"}, field=None, reason="conflict"))
        self.assertEqual(after["source_identity"], before["source_identity"])
        self.assertEqual(after["observed_digest"], before["observed_digest"])
        self.assertEqual(after["publication"], before["publication"])
        # A restarted service must preserve the unresolved effect and conflict;
        # even explicit parent recovery cannot adopt a conflicted publication.
        self.service = CollectionService(self.root, CollectionRepository(self.store.path),
            original_paths=(self.metadata["original"]["path"],), admit_basis=self.admit)
        self.store = self.service.repository
        self.assert_conflict_recovery_failure("save_unknown", "effect_unknown")

    def test_unknown_publication_precedes_latched_conflict_without_replay(self):
        self.assert_interrupted_publication_conflict(OSError, "unknown")

    def test_publication_intent_precedes_latched_conflict_after_restart(self):
        self.assert_interrupted_publication_conflict(KeyboardInterrupt, "intent")

    def assert_unretained_failure(self, raw):
        with patch.object(files, "write_exclusive") as write:
            with self.assertRaises(AnswerError):
                self.service.observe(1, self.observed(raw))
            self.assertFalse(write.called)
            attempt = self.store.read()[0]["attempts"][0]
            self.assertIsNone(attempt["observed_digest"])
            self.assertIsNone(attempt["publication"])

    def test_shape_unknown_never_publishes_or_hashes(self):
        self.assert_unretained_failure(b'{"unknown":"body"}')

    def test_privacy_rejected_never_publishes_or_hashes(self):
        self.assert_unretained_failure(b'{"verdict":"pass","summary":"api_key=sk-abcdefghijklmnopqrstuv","findings":[],"checks":[],"limitations":[]}')

    def test_latched_unknown_failures_do_not_compare_later_bodies(self):
        for raw in (b'{"unknown":"body"}', self.answer.raw):
            with self.subTest(raw=raw), patch.object(files, "write_exclusive") as write:
                with self.assertRaises(AnswerError):
                    self.service.observe(1, self.observed(raw))
                self.assertFalse(write.called)
                attempt = self.store.read()[0]["attempts"][0]
                self.assertIsNone(attempt["observed_digest"])
                self.assertIsNone(attempt["publication"])

    def test_interrupted_publication_requires_explicit_exact_reconciliation(self):
        real_write = files.write_exclusive
        def interrupted(path, raw):
            real_write(path, raw)
            raise OSError("not retained")
        with patch.object(files, "write_exclusive", side_effect=interrupted):
            with self.assertRaises(AnswerError) as raised:
                self.service.observe(1, self.observed())
        self.assertEqual(raised.exception.failure["class"], "save_unknown")
        self.assertEqual(self.store.read()[0]["attempts"][0]["publication"]["state"], "unknown")
        host = Mock()
        before = self.store.read()
        for _ in range(2):
            with self.assertRaises(AnswerError) as raised:
                self.service.recover(1, host)
            self.assertEqual(raised.exception.failure["class"], "save_unknown")
        unavailable = collect_bound(self.service, host)
        self.assertEqual(unavailable["unavailable"][0]["failure"]["class"], "save_unknown")
        event = dict(hook_event_name="SubagentStop", session_id=self.metadata["binding"]["parent_session_id"],
            agent_id=self.metadata["binding"]["reviewer_session_id"], turn_id=TURN2, stop_hook_active=False)
        with self.assertRaises(AnswerError) as raised:
            collect_hook_event(event, service=self.service, host=host)
        self.assertEqual(raised.exception.failure["class"], "save_unknown")
        with self.assertRaises(AnswerError) as raised:
            self.service.readback(1)
        self.assertEqual(raised.exception.failure["class"], "save_unknown")
        self.assertEqual(self.store.read(), before)
        self.assertFalse(host.observe_answer.called)
        self.assertFalse(host.observe_terminal.called)
        self.assertFalse(host.send_correction.called)
        with patch.object(self.store, "update", side_effect=OSError("not retained")):
            unresolved = self.parent_collect()
        self.assertEqual(unresolved["unavailable"][0]["failure"]["class"], "save_unknown")
        self.assertEqual(self.store.read(), before)
        with patch.object(files, "write_exclusive") as write:
            self.assertTrue(self.parent_collect()["ok"])
            self.assertTrue(self.parent_collect()["ok"])
            self.assertFalse(write.called)
        self.assertEqual(self.service.readback(1)[0], self.answer.raw)

    def test_partial_missing_and_conflicting_residue_stay_save_unknown_in_public_recovery(self):
        def partial(path, raw):
            path.write_bytes(raw[:10])
            raise OSError()
        with patch.object(files, "write_exclusive", side_effect=partial), self.assertRaises(AnswerError):
            self.service.observe(1, self.observed())
        path = self.root / self.metadata["original"]["path"]
        for residue in (self.answer.raw[:10], self.answer.raw.replace(b"okay", b"nope"), None):
            with self.subTest(residue=residue):
                if residue is None:
                    path.unlink()
                else:
                    path.write_bytes(residue)
                before = self.store.read()
                with patch.object(files, "write_exclusive") as write:
                    response = self.parent_collect()
                    self.assertFalse(response["ok"])
                    self.assertEqual(response["unavailable"][0]["failure"]["class"], "save_unknown")
                    write.assert_not_called()
                self.assertEqual(self.store.read(), before)
                self.assertEqual(path.read_bytes() if path.exists() else None, residue)

    def test_public_parent_reconciles_saved_intent_after_restart_without_host_read(self):
        real_write = files.write_exclusive
        def stopped(path, raw):
            real_write(path, raw)
            raise KeyboardInterrupt()
        with patch.object(files, "write_exclusive", side_effect=stopped), self.assertRaises(KeyboardInterrupt):
            self.service.observe(1, self.observed())
        self.assertEqual(self.store.read()[0]["attempts"][0]["publication"]["state"], "intent")
        self.service = CollectionService(self.root, CollectionRepository(self.store.path),
            original_paths=(self.metadata["original"]["path"],), admit_basis=self.admit)
        with patch.object(files, "write_exclusive") as write:
            self.assertTrue(self.parent_collect()["ok"])
            write.assert_not_called()
        self.assertEqual(self.service.readback(1)[0], self.answer.raw)

    def test_saved_answer_still_requires_terminal_and_one_exact_recovery(self):
        self.service.observe(1, self.observed(terminal="in_progress"))
        with self.assertRaises(AnswerError):
            self.service.readback(1)
        host = Mock()
        host.observe_terminal.return_value = dict(reviewer_session_id=self.metadata["binding"]["reviewer_session_id"],
            turn_id=self.metadata["binding"]["initial_turn_id"], terminal="completed", terminal_source=source("terminal_observation"))
        self.assertEqual(self.service.recover(1, host)[0], self.answer.raw)
        host.observe_terminal.assert_called_once()
        self.assertFalse(host.observe_answer.called)

    def test_hook_publishes_pending_final_without_qualifying_registration(self):
        host = Mock()
        host.observe_answer.return_value = self.observed(terminal="in_progress")
        event = dict(hook_event_name="SubagentStop", session_id=self.metadata["binding"]["parent_session_id"],
            agent_id=self.metadata["binding"]["reviewer_session_id"], turn_id=TURN2, stop_hook_active=False)
        with patch.object(files, "write_exclusive", wraps=files.write_exclusive) as write:
            self.assertEqual(collect_hook_event(event, service=self.service, host=host), {})
            before = self.store.read()
            journal = self.store.path.read_bytes()
            for _ in range(2):
                self.assertEqual(collect_hook_event(event, service=self.service, host=host), {})
            write.assert_called_once()
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.store.path.read_bytes(), journal)
        self.assertEqual(before[0]["attempts"][0]["publication"]["state"], "saved")
        self.assertEqual(before[0]["attempts"][0]["terminal"], "in_progress")
        self.assertEqual(before[0]["registration"]["state"], "not_started")
        raw, metadata = self.service.readback(1, require_terminal=False)
        self.assertEqual(raw, self.answer.raw)
        self.assertEqual(metadata["terminal"], "in_progress")
        with self.assertRaises(AnswerError):
            validate_metadata(metadata, raw)
        host.observe_answer.assert_called_once()
        host.observe_terminal.assert_not_called()
        host.send_correction.assert_not_called()
        self.assert_registration_requires_terminal()
        terminal = dict(reviewer_session_id=self.metadata["binding"]["reviewer_session_id"],
            turn_id=self.metadata["binding"]["initial_turn_id"], terminal="in_progress",
            terminal_source=source("terminal_observation"))
        host.observe_terminal.return_value = terminal
        with patch("task_governance_tool.review_answer_host.PublicAnswerHost", return_value=host):
            parent = self.parent_collect()
        self.assertFalse(parent["ok"])
        self.assertEqual(parent["unavailable"][0]["failure"]["class"], "collection_unavailable")
        self.assertEqual(collect_bound(self.service, host)["collected"], [])
        self.assertEqual(self.store.read(), before)
        # Completion comes from a separate exact terminal observation, never
        # from the stop event or the existence of a valid final answer.
        host.observe_terminal.return_value = dict(terminal, terminal="completed")
        self.assertEqual(collect_bound(self.service, host), dict(collected=[1], unavailable=[]))
        self.assertEqual(self.service.readback(1)[1]["terminal"], "completed")
        with self.service.admission() as (_lease, originals):
            self.assertEqual(originals[0][0], self.answer.raw)
        self.service.mark_registration((1,), "sha256:" + "d" * 64)
        self.assertEqual(self.store.read()[0]["registration"]["state"], "dispatched")
        host.observe_answer.assert_called_once()

    def assert_registration_requires_terminal(self):
        with self.assertRaises(AnswerError):
            self.service.readback(1)
        with self.assertRaises(AnswerError):
            with self.service.admission():
                self.fail("non-completed answer admitted for registration")
        with self.assertRaises(AnswerError):
            self.service.mark_registration((1,), "sha256:" + "d" * 64)
        self.assertEqual(self.store.read()[0]["registration"]["state"], "not_started")

    def assert_unsuccessful_terminal_never_qualifies(self, terminal):
        host = Mock()
        host.observe_answer.return_value = self.observed(terminal=terminal)
        event = dict(hook_event_name="SubagentStop", session_id=self.metadata["binding"]["parent_session_id"],
            agent_id=self.metadata["binding"]["reviewer_session_id"], turn_id=TURN2, stop_hook_active=False)
        self.assertEqual(collect_hook_event(event, service=self.service, host=host), {})
        self.assertEqual(self.store.read()[0]["attempts"][0]["terminal"], terminal)
        self.assert_registration_requires_terminal()
        with self.assertRaises(AnswerError):
            self.service.recover(1, host)
        self.assertEqual(collect_bound(self.service, host)["collected"], [])
        self.assertFalse(self.parent_collect()["ok"])
        host.observe_answer.assert_called_once()
        host.observe_terminal.assert_not_called()

    def test_hook_does_not_make_failed_turn_eligible_for_registration(self):
        self.assert_unsuccessful_terminal_never_qualifies("failed")

    def test_hook_does_not_make_interrupted_turn_eligible_for_registration(self):
        self.assert_unsuccessful_terminal_never_qualifies("interrupted")

    def test_unavailable_observation_keeps_no_body_and_explicit_recovery_uses_same_source(self):
        host = Mock()
        host.observe_answer.side_effect = [AnswerError("capacity_exceeded", reason="bound"), self.observed()]
        with self.assertRaises(AnswerError):
            self.service.recover(1, host)
        attempt = self.store.read()[0]["attempts"][0]
        self.assertEqual(attempt["observation"], "unavailable")
        self.assertEqual(attempt["failure"]["class"], "capacity_exceeded")
        self.assertIsNone(attempt["observed_digest"])
        host.observe_answer.assert_called_once()
        self.assertEqual(self.service.recover(1, host)[0], self.answer.raw)
        self.assertEqual(host.observe_answer.call_args_list[0], host.observe_answer.call_args_list[1])

    def malformed_format(self):
        # Valid closed shape/privacy, invalid verdict: only its digest survives.
        raw = self.answer.raw.replace(b'"pass"', b'"maybe"')
        with self.assertRaises(AnswerError) as raised:
            self.service.observe(1, self.observed(raw))
        self.assertTrue(raised.exception.privacy_admissible)
        return raw

    def test_correction_observer_absence_consumes_opportunity_without_send(self):
        self.malformed_format()
        host = Mock()
        first = self.service.correction(1, host)
        self.assertEqual(first["correction"]["effect"], "not_sent")
        self.assertEqual(first["correction"]["failure"]["reason"], "observer_unavailable")
        self.assertEqual(self.service.correction(1, host, observer=lambda _: True), first)
        self.assertFalse(host.send_correction.called)

    def test_correction_unknown_and_malformed_ack_never_retry(self):
        self.malformed_format()
        host = Mock()
        host.send_correction.return_value = dict(effect="accepted", correction_turn_id="not-a-turn", source=source("correction_binding"))
        first = self.service.correction(1, host, observer=lambda _: True)
        self.assertEqual(first["correction"]["effect"], "unknown")
        self.assertEqual(len(first["attempts"]), 1)
        self.assertEqual(self.service.correction(1, host, observer=lambda _: True), first)
        host.send_correction.assert_called_once()

    def test_corrected_answer_has_new_turn_and_digest(self):
        initial = self.malformed_format()
        host = Mock()
        host.send_correction.return_value = dict(effect="accepted", correction_turn_id=TURN2, source=source("correction_binding"))
        self.service.correction(1, host, observer=lambda _: True)
        self.service.observe(1, self.observed(turn=TURN2))
        record = self.store.read()[0]
        self.assertEqual(len(record["attempts"]), 2)
        self.assertNotEqual(record["attempts"][0]["observed_digest"], record["attempts"][1]["observed_digest"])
        self.assertEqual(self.service.readback(1)[1]["attempt"], 1)
        self.assertNotEqual(initial, self.service.readback(1)[0])

    def test_whole_batch_registration_intent_survives_unknown_without_replay(self):
        self.service.observe(1, self.observed())
        digest = "sha256:" + "d" * 64
        with self.service.admission() as (lease, values):
            self.assertEqual(values[0][0], self.answer.raw)
            self.service.mark_registration_locked(lease, (1,), digest)
            self.service.unknown_registration_locked(lease, (1,), digest)
        with self.assertRaises(AnswerError):
            self.service.mark_registration((1,), digest)
        self.service.confirm_registration(1, digest, "tg_review_receipt_" + "a" * 16)
        self.assertEqual(self.store.read()[0]["registration"]["state"], "registered")

    def test_binding_immutability_and_capacity(self):
        changed = copy.deepcopy(self.metadata["binding"])
        changed["dispatch_id"] = "other"
        with self.assertRaises(AnswerError):
            self.service.bind(1, changed)
        before = self.store.read()
        for bases in ((), (self.metadata["basis"],) * 9):
            with self.assertRaises(RepositoryError):
                self.store.initialize(bases)
        self.assertEqual(self.store.read(), before)

    def test_atomic_multi_slot_update_rejects_duplicate_actual_reviewer(self):
        path = self.root / "two.sqlite3"
        repo = CollectionRepository(path)
        repo.initialize((self.metadata["basis"], self.metadata["basis"]))
        def bind(slot):
            def change(row):
                row["binding"] = copy.deepcopy(self.metadata["binding"])
                row["binding"]["slot"] = slot
                row["binding"]["reviewer_key"] = f"reviewer-{slot}"
                row["attempts"][0]["turn_id"] = row["binding"]["initial_turn_id"]
                return row
            return change
        with repo.serial() as lease, self.assertRaises(RepositoryError):
            repo.update_many(lease, {1: bind(1), 2: bind(2)})
        self.assertTrue(all(row["binding"] is None for row in repo.read()))

    def test_mixed_continuation_may_mark_selected_source_slot_only(self):
        repo = CollectionRepository(self.root / "subset.sqlite3")
        repo.initialize((self.metadata["basis"], self.metadata["basis"]))
        service = CollectionService(self.root, repo,
            original_paths=(self.metadata["original"]["path"], ".tmp-review/other.json"), admit_basis=self.admit)
        service.bind(1, self.metadata["binding"])
        service.observe(1, self.observed())
        with service.admission((1,)) as (lease, _sources):
            service.mark_registration_locked(lease, (1,), "sha256:" + "d" * 64)
        self.assertEqual([row["registration"]["state"] for row in repo.read()], ["dispatched", "not_started"])


if __name__ == "__main__":
    unittest.main()
