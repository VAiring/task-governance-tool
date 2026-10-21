"""Isolated real Hook/collector service: running is pending, never adoption."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_review_answer_hook_caller as fixture
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection import CollectionInvalidated
from task_governance_tool.review_answer_hook import PENDING_MESSAGE, PENDING_RESULT
from task_governance_tool.review_answer_runtime import SourceGroup
from task_governance_tool.review_wait_runtime.review_wait_server import _call
from task_governance_tool import review_handoff_transport as files
from tests.test_review_answer_pure_values import TURN1, TURN2


class HookPendingTests(unittest.TestCase):
    # Share only the real isolated service fixture, not its discovered tests.
    setUp = fixture.ReviewerHookTests.setUp
    reader = fixture.ReviewerHookTests.reader
    parent_host = fixture.ReviewerHookTests.parent_host
    response = fixture.ReviewerHookTests.response
    call = fixture.ReviewerHookTests.call
    tracking_continuation = fixture.ReviewerHookTests.tracking_continuation

    def tracking(self):
        self.tracking_continuation()
        self.active_turn = TURN2
        self.caller = {"threadId": self.child}
        self.status = "inProgress"
        self.mutate = lambda value: None
        self.public.side_effect = self.observation

    def observation(self, name, arguments, **kwargs):
        response = self.response(name, arguments, **kwargs)
        value = json.loads(response["content"][0]["text"])
        value["turns"][0]["status"] = self.status
        self.mutate(value)
        response["content"][0]["text"] = json.dumps(value)
        return response

    def assert_pending(self, wire):
        self.assertFalse(wire["isError"], wire)
        self.assertEqual(json.loads(wire["content"][0]["text"]), {"systemMessage": PENDING_MESSAGE})

    def assert_failure(self, wire, classification=None):
        self.assertTrue(wire["isError"], wire)
        result = json.loads(wire["content"][0]["text"])
        if classification:
            self.assertEqual(result["error"], classification)
        self.assertNotIn("systemMessage", result)
        return result

    def parent_sources(self):
        group = SourceGroup(self.service, (self.child,), (1,), (1,))
        return patch("task_governance_tool.review_answer_runtime.sources_for_reviewers", return_value=(group,))

    def worker_collect(self):
        host = self.parent_host(metadata={"threadId": self.parent, "turnId": TURN1},
            task_id=self.current.task_id, server_path=self.root / "server.mjs",
            automation_id="existing-worker", codex_home=self.root, confirmed_timezone="UTC",
            timezone_source="host_node_intl", reviewer_ids=(self.child,))
        with self.parent_sources():
            return self.session._collect_answers(self.current, host, (self.child,))

    def parent_collect(self):
        paths = SimpleNamespace(review_wait_request_store=lambda *args: self.root / "absent.sqlite3")
        with self.parent_sources(), patch.object(self.session, "_location", return_value=paths), \
                patch.object(self.session, "basis_factory", return_value=lambda: self.current):
            return _call(dict(name="review_answer_collect",
                arguments=dict(task_id=self.current.task_id, reviewer_ids=[self.child]),
                _meta=dict(threadId=self.parent, turnId=TURN1)), self.session)

    def test_both_hook_callers_report_pending_without_writing_or_spawning(self):
        self.tracking()
        before, journal = self.store.read(), self.store.path.read_bytes()
        self.session.session_factory = Mock(side_effect=AssertionError("no worker"))
        for caller in (self.caller, {"threadId": self.parent, "turnId": TURN1}):
            with self.subTest(caller=caller), patch.object(files, "write_exclusive") as publish:
                self.assert_pending(self.call(caller=caller))
                self.assertEqual(self.store.read(), before)
                self.assertEqual(self.store.path.read_bytes(), journal)
                self.assertFalse(self.original.exists())
                publish.assert_not_called()
        self.assertEqual(self.public.call_count, 2)
        self.session.session_factory.assert_not_called()
        self.assertEqual(self.session.sessions, {})
        # Registration's complete-original gate remains closed.
        with self.assertRaises(AnswerError), self.service.admission():
            self.fail("pending answer admitted")

    def test_internal_result_distinguishes_pending_from_collected(self):
        self.tracking()
        result = self.session.handle("answer_collect", {"event": self.event}, self.caller)
        self.assertEqual(result, {"ok": True, **PENDING_RESULT})
        self.status = "completed"
        self.assertEqual(self.session.handle("answer_collect", {"event": self.event}, self.caller),
                         {"ok": True, "status": "collected"})

    def test_existing_worker_collects_same_turn_once_after_pending_hook(self):
        self.tracking()
        self.assert_pending(self.call())
        self.assert_pending(self.call())
        self.status = "completed"
        with patch.object(files, "write_exclusive", wraps=files.write_exclusive) as publish:
            self.assertEqual(self.worker_collect(), {"collected": [1], "unavailable": []})
            self.assertEqual(self.worker_collect(), {"collected": [1], "unavailable": []})
            self.assertEqual(self.call(), {"content": [{"type": "text", "text": "{}"}], "isError": False})
            self.assertEqual(publish.call_count, 1)
        self.assertEqual(self.public.call_count, 3)
        raw, metadata = self.service.readback(1)
        self.assertEqual(raw, self.answer.raw)
        self.assertEqual(metadata["source_identity"]["turn_id"], TURN2)
        self.assertEqual(metadata["terminal"], "completed")
        self.assertEqual(self.store.read()[0]["registration"]["state"], "not_started")

    def test_workerless_parent_collects_same_turn_once_after_pending_hook(self):
        self.tracking()
        self.assert_pending(self.call())
        self.status = "completed"
        with patch.object(files, "write_exclusive", wraps=files.write_exclusive) as publish:
            for _ in range(2):
                wire = self.parent_collect()
                self.assertFalse(wire["isError"], wire)
                self.assertEqual(json.loads(wire["content"][0]["text"])["collected"], [1])
            self.assertEqual(publish.call_count, 1)
        self.assertEqual(self.public.call_count, 2)
        self.assertEqual(self.service.readback(1)[1]["source_identity"]["turn_id"], TURN2)
        self.assertEqual(self.session.sessions, {})

    def test_parent_and_wait_do_not_treat_running_as_collected(self):
        self.tracking()
        result = self.worker_collect()
        self.assertEqual(result["collected"], [])
        self.assertEqual(result["unavailable"][0]["failure"]["reason"], "incomplete")
        self.assert_failure(self.parent_collect())
        self.assertFalse(self.original.exists())
        self.assertEqual(self.store.read()[0]["tracking_state"], "watching")

    def test_running_only_establishes_turn_state_not_answer_completeness(self):
        self.tracking()
        self.mutate = lambda value: value["turns"][0].update(items=[])
        self.assert_pending(self.call())
        self.assertFalse(self.original.exists())
        # Once terminal, the same absent final remains a real error.
        self.status = "completed"
        self.assert_failure(self.call(), "collection_unavailable")

    def test_other_incomplete_and_invalid_answers_never_become_pending(self):
        self.tracking()
        cases = [
            ("inProgress", lambda v: v["turns"][0].update(error={})),
            ("failed", lambda v: None), ("interrupted", lambda v: None),
            ("completed", lambda v: v["turns"][0].update(error={})),
            ("inProgress", lambda v: v.update(truncated=True)),
            ("inProgress", lambda v: v["thread"].update(complete=False)),
            ("inProgress", lambda v: v["page"].update(truncated=True)),
            ("inProgress", lambda v: v["turns"][0].update(complete=False)),
            ("inProgress", lambda v: v["turns"][0]["items"][0].update(textTruncated=True)),
            ("completed", lambda v: v["turns"][0]["items"][0].update(textTruncated=True)),
            ("completed", lambda v: v["turns"][0].update(items=[])),
            ("completed", lambda v: v["turns"][0]["items"].append(copy.deepcopy(v["turns"][0]["items"][0]))),
            ("completed", lambda v: v["turns"][0]["items"][0].update(text="invalid json")),
            ("inProgress", lambda v: v["turns"][0].update(items="not a list")),
            ("inProgress", lambda v: v["thread"].update(id=fixture.OTHER)),
        ]
        for index, (status, mutate) in enumerate(cases):
            self.status, self.mutate = status, mutate
            with self.subTest(case=index):
                self.assert_failure(self.call())
                self.assertFalse(self.original.exists())
                self.assertEqual(self.store.read()[0]["tracking_state"], "watching")
        self.assertEqual(self.public.call_count, len(cases))

    def test_excluded_turn_and_changed_basis_never_become_pending(self):
        self.tracking()
        self.active_turn = TURN1
        self.assert_failure(self.call(), "collection_unavailable")
        self.active_turn = TURN2
        for error in (AnswerError("binding_mismatch", reason="identity"), CollectionInvalidated()):
            self.basis_admit.side_effect = error
            with self.subTest(error=type(error).__name__):
                self.assert_failure(self.call(), "binding_mismatch")
        self.assertEqual(self.public.call_count, 1)
        self.assertFalse(self.original.exists())

    def test_basis_change_during_host_read_prevents_pending(self):
        self.tracking()
        def changed(value):
            self.basis_admit.side_effect = CollectionInvalidated()
        self.mutate = changed
        self.assert_failure(self.call(), "binding_mismatch")
        self.assertEqual(self.store.read()[0]["tracking_state"], "invalidated")
        self.assertFalse(self.original.exists())

    def test_selection_change_during_host_read_prevents_pending(self):
        self.tracking()
        before = self.store.read()
        replacement = SimpleNamespace(repository=SimpleNamespace(path=self.root / "replacement.sqlite3"))
        self.mutate = lambda value: setattr(self.lookup, "return_value", (replacement, self.current))
        self.assert_failure(self.call(), "binding_mismatch")
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.original.exists())

    def test_frozen_unknown_publication_precedes_running_observation(self):
        self.tracking()
        self.status = "completed"
        write = files.write_exclusive
        def uncertain(path, raw):
            write(path, raw)
            raise OSError("fixture")
        with patch.object(files, "write_exclusive", side_effect=uncertain):
            self.assert_failure(self.call(), "save_unknown")
        before = self.store.read()
        self.status = "inProgress"
        self.assert_failure(self.call(), "save_unknown")
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.public.call_count, 1)

    def test_pending_does_not_overwrite_a_previous_format_failure(self):
        self.tracking()
        self.status = "completed"
        self.mutate = lambda value: value["turns"][0]["items"][0].update(text="invalid json")
        self.assert_failure(self.call(), "format_invalid")
        before = self.store.read()
        self.status, self.mutate = "inProgress", lambda value: None
        self.assert_pending(self.call())
        self.assertEqual(self.store.read(), before)

    def test_initial_tracking_turn_is_pending_without_requiring_a_continuation(self):
        from task_governance_tool.review_answer_collection import CollectionService
        from task_governance_tool.review_answer_collection_repository import CollectionRepository
        from tests.test_review_answer_tracking_collection import tracking_fixture
        self.tracking()
        metadata, _ = tracking_fixture()
        self.store = CollectionRepository(self.root / "initial-tracking.sqlite3")
        self.store.initialize((metadata["basis"],))
        self.service = CollectionService(self.root, self.store,
            original_paths=(metadata["original"]["path"],), admit_basis=self.basis_admit)
        self.service.bind(1, metadata["binding"])
        self.lookup.return_value = (self.service, self.current)
        self.active_turn = TURN1
        self.assert_pending(self.call())
        self.status = "completed"
        self.assertFalse(self.call()["isError"])
        self.assertEqual(self.service.readback(1)[1]["source_identity"]["turn_id"], TURN1)

    def test_concurrent_worker_save_is_read_back_instead_of_reporting_pending(self):
        self.tracking()
        from task_governance_tool.review_answer_collection import source
        observed = dict(source_identity={**self.metadata["source_identity"], "turn_id": TURN2},
            answer_bytes=self.answer.raw, complete=True, terminal="completed",
            answer_source=source("answer_observation", 2), terminal_source=source("terminal_observation", 2))
        self.mutate = lambda value: self.service.observe(1, observed)
        with patch.object(files, "write_exclusive", wraps=files.write_exclusive) as publish:
            self.assertEqual(self.call(), {"content": [{"type": "text", "text": "{}"}], "isError": False})
            self.assertEqual(publish.call_count, 1)
        self.assertEqual(self.service.readback(1)[0], self.answer.raw)
        self.assertEqual(self.public.call_count, 1)

    def test_concurrent_unknown_save_does_not_become_pending(self):
        self.tracking()
        from task_governance_tool.review_answer_collection import source
        observed = dict(source_identity={**self.metadata["source_identity"], "turn_id": TURN2},
            answer_bytes=self.answer.raw, complete=True, terminal="completed",
            answer_source=source("answer_observation", 2), terminal_source=source("terminal_observation", 2))
        write = files.write_exclusive
        def mutate(value):
            def uncertain(path, raw):
                write(path, raw)
                raise OSError("fixture")
            with patch.object(files, "write_exclusive", side_effect=uncertain), self.assertRaises(AnswerError):
                self.service.observe(1, observed)
        self.mutate = mutate
        self.assert_failure(self.call(), "save_unknown")
        self.assertEqual(self.public.call_count, 1)
        self.assertEqual(self.store.read()[0]["attempts"][0]["publication"]["state"], "unknown")

    def test_version_one_still_publishes_pending_terminal_then_requires_completion(self):
        self.status = "inProgress"
        self.mutate = lambda value: None
        self.public.side_effect = self.observation
        self.assertEqual(self.call(), {"content": [{"type": "text", "text": "{}"}], "isError": False})
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertEqual(self.store.read()[0]["attempts"][0]["terminal"], "in_progress")
        with self.assertRaises(AnswerError):
            self.service.readback(1)
        self.status = "completed"
        self.assertEqual(self.worker_collect(), {"collected": [1], "unavailable": []})
        self.assertEqual(self.service.readback(1)[1]["terminal"], "completed")


if __name__ == "__main__":
    unittest.main()
