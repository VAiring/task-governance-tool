"""Real isolated collection with genuine parent/reviewer executor boundaries."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool import review_handoff_transport as files
from task_governance_tool import state_resolver
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection import CollectionService, source
from task_governance_tool.review_answer_collection_repository import CollectionRepository
from task_governance_tool.review_answer_host import PublicAnswerHost
from task_governance_tool.review_answer_read_host import AnswerReadHost, hook_executor_turn_id
from task_governance_tool.review_answer_runtime import reviewer_hook_admission
from task_governance_tool.review_answer_values import basis_digest
from task_governance_tool.review_wait_runtime.managed_host import ManagedHost
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_host import (
    PublicMcpHost, HostAdapterError, executor_turn_id)
from task_governance_tool.review_wait_runtime.review_wait_server import _call
from task_governance_tool.review_wait_runtime import review_wait_mcp_relay as protocol
from tests.test_review_answer_host import page, turn
from tests.test_review_answer_pure_values import metadata_fixture, TURN1, TURN2
from tests.test_review_wait_host import PEER


OTHER = "90000000-0000-4000-8000-000000000001"
SHARED_REQUEST = PublicMcpHost._request


class ReviewerHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".tmp-review").mkdir()
        self.metadata, self.answer = metadata_fixture()
        self.parent = self.metadata["binding"]["parent_session_id"]
        self.child = self.metadata["binding"]["reviewer_session_id"]
        self.store = CollectionRepository(self.root / "collection.sqlite3")
        self.store.initialize((self.metadata["basis"],))
        self.basis_admit = Mock()
        self.service = CollectionService(self.root, self.store,
            original_paths=(self.metadata["original"]["path"],), admit_basis=self.basis_admit)
        self.service.bind(1, self.metadata["binding"])
        self.original = self.root / self.metadata["original"]["path"]
        self.enterContext(patch.object(files, "require_ignored"))
        self.current = SimpleNamespace(parent_thread_id=self.parent,
            task_id=self.metadata["basis"]["execution_basis"]["task_id"])
        self.lookup = self.enterContext(patch(
            "task_governance_tool.review_answer_runtime.service_for_event",
            return_value=(self.service, self.current)))
        def admit_current(value, slot, *, parent_id, validation_reuse):
            # This fixture supplies the same fresh-source boundary as the real
            # admission object; real native/operational reads have separate tests.
            current, binding = self.lookup()
            if (binding != self.current or current.repository.path != self.service.repository.path
                    or parent_id != self.parent):
                raise AnswerError("binding_mismatch", reason="identity")
            self.basis_admit(value, slot)
        self.basis_admit.check_hook.side_effect = admit_current
        self.allowed = self.enterContext(patch(
            "task_governance_tool.setup_feature_config.answer_collection_allowed", return_value=True))
        self.reader_factory = Mock(side_effect=self.reader)
        self.parent_factory = Mock(side_effect=self.parent_host)
        self.session = ProjectReviewWaitSession(ProjectConfig(self.root, self.root / "server.mjs", self.root, "UTC"),
            managed_host_factory=self.parent_factory, answer_read_host_factory=self.reader_factory)
        self.location = self.enterContext(patch.object(self.session, "_location"))
        self.event = dict(hook_event_name="SubagentStop", session_id=self.parent, agent_id=self.child,
            turn_id=TURN2, stop_hook_active=False)
        self.caller = dict(threadId=self.child, turnId=TURN1,
            **{"openai/threadId": self.child, "openai/turnId": TURN1}, nested={"kept": True})
        self.active_turn = TURN1
        self.public = self.enterContext(patch.object(PublicMcpHost, "_request", side_effect=self.response))

    def reader(self, **kwargs):
        kwargs.pop("server_path")
        return AnswerReadHost(command=["offline-unused"], **kwargs)

    def parent_host(self, **kwargs):
        kwargs.pop("server_path")
        kwargs.pop("task_id")
        return PublicMcpHost(command=["offline-unused"], **kwargs)

    def response(self, name, arguments, **_kwargs):
        self.assertEqual((name, arguments), ("read_thread", dict(threadId=self.child, hostId="local",
            turnLimit=2, includeOutputs=False, maxOutputCharsPerItem=256)))
        value = page(self.child, 2, turn(self.active_turn, items=[dict(type="agentMessage", phase="final_answer",
            id=self.metadata["source_identity"]["message_id"], text=self.answer.raw.decode())]))
        return dict(isError=False, content=[dict(type="text", text=json.dumps(value))])

    def call(self, *, caller=None, event=None):
        return _call(dict(name="review_answer_collect", arguments={"event": self.event if event is None else event},
            _meta=self.caller if caller is None else caller), self.session)

    def assert_rejected_without_effect(self, wire, before, *, lookup=True):
        self.assertTrue(wire["isError"], wire)
        value = json.loads(wire["content"][0]["text"])
        self.assertEqual(value["diagnostic"]["local_state"], "not_attempted")
        self.assertEqual(value["diagnostic"]["host_mutation"], "not_dispatched")
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.original.exists())
        self.public.assert_not_called()
        self.reader_factory.assert_not_called()
        self.parent_factory.assert_not_called()
        if not lookup:
            self.lookup.assert_not_called()
        self.assertIs(self.service.admit_basis, self.basis_admit)
        return value

    def test_saved_reviewer_collects_own_exact_turn_with_unchanged_metadata(self):
        before = copy.deepcopy(self.caller)
        wire = self.call()
        self.assertEqual(wire, {"content": [{"type": "text", "text": "{}"}], "isError": False})
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertEqual(self.reader_factory.call_args.kwargs["metadata"], before)
        self.assertEqual(self.caller, before)
        self.assertEqual(self.public.call_count, 1)
        self.parent_factory.assert_not_called()
        self.assertIs(self.service.admit_basis, self.basis_admit)

    def track_validation_cleanup(self):
        cleared = []
        original = state_resolver.DatabaseValidationReuse.clear
        def clear(reuse):
            cleared.append(reuse)
            original(reuse)
        self.enterContext(patch.object(state_resolver.DatabaseValidationReuse, "clear", new=clear))
        return cleared

    def test_reviewer_validation_reuse_is_scoped_to_one_call_even_for_saved_duplicate(self):
        cleared = self.track_validation_cleanup()
        contexts = []
        for _ in range(2):
            self.basis_admit.check_hook.reset_mock()
            self.assertFalse(self.call()["isError"])
            calls = self.basis_admit.check_hook.call_args_list
            self.assertGreater(len(calls), 1)
            reuse = calls[0].kwargs["validation_reuse"]
            self.assertIsInstance(reuse, state_resolver.DatabaseValidationReuse)
            self.assertTrue(all(call.kwargs["validation_reuse"] is reuse for call in calls))
            self.assertIn(reuse, cleared)
            contexts.append(reuse)
        self.assertIsNot(contexts[0], contexts[1])
        self.assertEqual(self.public.call_count, 1)
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertIs(self.service.admit_basis, self.basis_admit)

    def test_validation_reuse_is_cleared_before_a_failed_gate_returns_to_its_caller(self):
        cleared = self.track_validation_cleanup()
        for failure in ("native_admission", "basis", "slot"):
            current = Mock()
            with self.subTest(failure=failure), reviewer_hook_admission(
                    self.service, self.parent, self.child, TURN1, admit_current=current) as admission:
                reuse = current.call_args.kwargs["validation_reuse"]
                self.assertNotIn(reuse, cleared)
                if failure == "native_admission":
                    current.side_effect = AnswerError("binding_mismatch", reason="identity")
                    operation = admission.check
                else:
                    basis = copy.deepcopy(self.metadata["basis"])
                    if failure == "basis":
                        basis["request_digest"] = "sha256:" + "f" * 64
                    operation = lambda: self.service.admit_basis(basis, 2 if failure == "slot" else 1)
                with self.assertRaises(AnswerError):
                    operation()
                self.assertIn(reuse, cleared)
            self.assertIs(self.service.admit_basis, self.basis_admit)
        self.public.assert_not_called()
        self.assertFalse(self.original.exists())

    def test_validation_reuse_clears_on_initial_admission_failure_and_body_exception(self):
        cleared = self.track_validation_cleanup()
        for failure in ("initial_admission", "body"):
            current = Mock(side_effect=AnswerError("binding_mismatch", reason="identity")
                if failure == "initial_admission" else None)
            entered = False
            with self.subTest(failure=failure), self.assertRaises(AnswerError):
                with reviewer_hook_admission(self.service, self.parent, self.child, TURN1,
                        admit_current=current):
                    entered = True
                    raise AnswerError("collection_unavailable", reason="incomplete")
            reuse = current.call_args.kwargs["validation_reuse"]
            self.assertEqual(entered, failure == "body")
            self.assertIn(reuse, cleared)
            self.assertIs(self.service.admit_basis, self.basis_admit)
        self.public.assert_not_called()
        self.assertFalse(self.original.exists())

    def test_parent_hook_preserves_existing_host_and_neutral_result(self):
        wire = self.call(caller=dict(threadId=self.parent, turnId=TURN2))
        self.assertFalse(wire["isError"], wire)
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.parent_factory.assert_called_once()
        self.reader_factory.assert_not_called()
        self.assertEqual(self.public.call_count, 1)
        self.basis_admit.check_hook.assert_not_called()

    def test_missing_executor_turn_uses_saved_source_not_event_or_newest_answer(self):
        caller = {"threadId": self.child}
        final = dict(type="agentMessage", phase="final_answer",
            id=self.metadata["source_identity"]["message_id"], text=self.answer.raw.decode())
        latest = {**final, "id": "newer-message", "text": "not the assigned answer"}
        self.public.side_effect = None
        self.public.return_value = dict(isError=False, content=[dict(type="text", text=json.dumps(
            page(self.child, 2, turn(TURN2, items=[latest]), turn(TURN1, items=[final]))))])
        wire = self.call(caller=caller)
        self.assertFalse(wire["isError"], wire)
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertEqual(self.reader_factory.call_args.kwargs["reviewer_turn_id"], TURN1)
        self.assertEqual(self.reader_factory.call_args.kwargs["metadata"], caller)
        self.assertEqual(caller, {"threadId": self.child})
        self.assertEqual(self.store.read()[0]["attempts"][0]["source_identity"]["turn_id"], TURN1)
        self.assertEqual(self.public.call_count, 1)
        self.parent_factory.assert_not_called()

    def test_parent_hook_without_executor_turn_recovers_saved_source_only(self):
        caller = {"threadId": self.parent}
        wire = self.call(caller=caller)
        self.assertFalse(wire["isError"], wire)
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertEqual(self.parent_factory.call_args.kwargs["metadata"], caller)
        self.assertEqual(self.store.read()[0]["attempts"][0]["source_identity"]["turn_id"], TURN1)
        self.reader_factory.assert_not_called()
        self.assertEqual(self.public.call_count, 1)

    def assert_offline_pipe_preserves_metadata(self):
        arguments = dict(threadId=self.child, hostId="local", turnLimit=2,
            includeOutputs=False, maxOutputCharsPerItem=256)
        expected = dict(name="read_thread", arguments=arguments, _meta=self.caller)
        response = self.response("read_thread", arguments)
        def reader(**kwargs):
            kwargs.pop("server_path")
            return AnswerReadHost(**kwargs, command=[sys.executable, "-B", "-u", "-c", PEER,
                json.dumps(expected), json.dumps(response), "success"], timeout_seconds=2)
        self.reader_factory.side_effect = reader
        processes, original_launch = [], subprocess.Popen
        def launch(*args, **kwargs):
            process = original_launch(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(PublicMcpHost, "_request", new=SHARED_REQUEST), \
                patch.object(protocol.subprocess, "Popen", side_effect=launch):
            wire = self.call()
        self.assertFalse(wire["isError"], wire)
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(all(stream.closed for stream in
            (processes[0].stdin, processes[0].stdout, processes[0].stderr)))

    def test_reviewer_public_dispatch_uses_real_offline_pipe_and_exact_metadata(self):
        self.assert_offline_pipe_preserves_metadata()

    def test_missing_executor_turn_offline_pipe_does_not_forge_metadata(self):
        self.caller = {"threadId": self.child}
        self.assert_offline_pipe_preserves_metadata()
        self.assertEqual(self.caller, {"threadId": self.child})

    def test_third_party_cannot_use_valid_event(self):
        before = self.store.read()
        for caller in ({"threadId": OTHER}, dict(threadId=OTHER, turnId=TURN1)):
            with self.subTest(caller=caller):
                value = self.assert_rejected_without_effect(self.call(caller=caller), before, lookup=False)
                self.assertEqual(value["diagnostic"]["hook_caller_relation"], "other")

    def test_wrong_genuine_turn_is_not_replaced_by_event_turn(self):
        before = self.store.read()
        value = self.assert_rejected_without_effect(self.call(caller=dict(threadId=self.child, turnId=TURN2),
            event={**self.event, "turn_id": TURN1}), before)
        self.assertEqual(value["error"], "caller_mismatch")
        self.assertEqual(value["diagnostic"]["hook_caller_relation"], "event_reviewer")

    def test_conflicting_and_embedded_only_executor_aliases_prevent_lookup(self):
        cases = ({"threadId": self.child, "turnId": TURN1, "openai/turnId": TURN2},
            {"threadId": self.child, "turnId": TURN1, "openai/threadId": self.parent},
            {"threadId": self.child, "x-codex-turn-metadata": {"turn": {"id": TURN1}}})
        for caller in cases:
            with self.subTest(caller=caller):
                self.assert_rejected_without_effect(self.call(caller=caller), self.store.read(), lookup=False)

    def test_executor_turn_failure_is_classified_before_lookup_without_disclosing_metadata(self):
        cases = (({"turnId": None}, "turn_invalid"),
            ({"turnId": "private-invalid-value"}, "turn_invalid"),
            ({"turn": []}, "turn_invalid"),
            ({"x-codex-turn-metadata": {"turn": {"id": TURN1}}}, "turn_embedded_only"))
        for thread in (self.parent, self.child):
            for fields, expected in cases:
                caller = {"threadId": thread, **fields}
                unchanged = copy.deepcopy(caller)
                with self.subTest(thread=thread, expected=expected):
                    value = self.assert_rejected_without_effect(
                        self.call(caller=caller), self.store.read(), lookup=False)
                    self.assertEqual(value["error"], "review_wait_unavailable")
                    self.assertEqual(value["diagnostic"]["reason"], "executor_context_required")
                    self.assertEqual(value["diagnostic"]["hook_executor_context"], expected)
                    self.assertEqual(caller, unchanged)
                    for rejected in (thread, TURN1, "private-invalid-value"):
                        self.assertNotIn(rejected, json.dumps(value))

    def test_unassigned_sibling_cannot_select_another_slot(self):
        before = self.store.read()
        for caller in ({"threadId": OTHER}, dict(threadId=OTHER, turnId=TURN1)):
            with self.subTest(caller=caller):
                self.assert_rejected_without_effect(self.call(caller=caller,
                    event={**self.event, "agent_id": OTHER}), before)

    def test_ambiguous_saved_slots_reject_before_host(self):
        before = self.store.read()
        with patch.object(self.store, "read", return_value=(before[0], copy.deepcopy(before[0]))):
            wire = self.call(caller={"threadId": self.child})
        self.assert_rejected_without_effect(wire, before)

    def test_reviewer_cannot_reconcile_unknown_publication(self):
        observed = dict(source_identity=self.metadata["source_identity"], answer_bytes=self.answer.raw,
            complete=True, terminal="completed", answer_source=source("answer_observation"),
            terminal_source=source("terminal_observation"))
        write = files.write_exclusive
        def lost_ack(path, raw):
            write(path, raw)
            raise OSError("unretained fixture failure")
        with patch.object(files, "write_exclusive", side_effect=lost_ack), self.assertRaises(AnswerError):
            self.service.observe(1, observed)
        before = self.store.read()
        wire = self.call(caller={"threadId": self.child})
        self.assertTrue(wire["isError"])
        value = json.loads(wire["content"][0]["text"])
        self.assertEqual(value["error"], "save_unknown")
        self.assertEqual(value["diagnostic"]["local_state"], "not_attempted")
        self.assertEqual(value["diagnostic"]["retained_effects"], "present_unresolved")
        self.assertEqual(value["diagnostic"]["recovery"], "inspect_existing")
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.public.assert_not_called()

    def test_wrong_parent_stale_basis_and_unbound_slot_reject_before_host(self):
        self.caller = {"threadId": self.child}
        before = self.store.read()
        self.assert_rejected_without_effect(self.call(event={**self.event, "session_id": OTHER}), before)
        with patch.object(self.basis_admit, "check_hook", side_effect=AnswerError("binding_mismatch", reason="identity")):
            wire = self.call()
        self.assert_rejected_without_effect(wire, before)
        for change in ("parent", "binding"):
            records = copy.deepcopy(before)
            if change == "parent":
                records[0]["binding"]["parent_session_id"] = OTHER
            else:
                records[0]["binding"] = None
                records[0]["attempts"][0]["turn_id"] = None
            with self.subTest(change=change), patch.object(self.store, "read", return_value=records):
                wire = self.call()
            self.assert_rejected_without_effect(wire, before)

    def prepare_format_failure(self):
        observed = dict(source_identity=self.metadata["source_identity"],
            answer_bytes=self.answer.raw.replace(b'"pass"', b'"maybe"'), complete=True, terminal="completed",
            answer_source=source("answer_observation"), terminal_source=source("terminal_observation"))
        with self.assertRaises(AnswerError):
            self.service.observe(1, observed)

    def correction(self):
        host = Mock()
        host.send_correction.return_value = dict(effect="accepted", correction_turn_id=TURN2,
            source=source("correction_binding"))
        # An independent already-authorized owner writes this fixture transition;
        # it is never dispatched by the reviewer Hook or the self-read host.
        owner = CollectionService(self.root, self.store,
            original_paths=(self.metadata["original"]["path"],), admit_basis=self.basis_admit)
        return owner.correction(1, host, observer=lambda _: True)

    def test_saved_active_correction_requires_its_exact_executor_turn(self):
        self.prepare_format_failure()
        self.correction()
        before = self.store.read()
        self.assert_rejected_without_effect(self.call(), before)
        self.active_turn = TURN2
        self.assertFalse(self.call(caller=dict(threadId=self.child, turnId=TURN2))["isError"])
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertEqual(self.store.read()[0]["attempts"][1]["source_identity"]["turn_id"], TURN2)

    def test_missing_executor_turn_recovers_exact_saved_correction(self):
        self.prepare_format_failure()
        self.correction()
        self.active_turn = TURN2
        wire = self.call(caller={"threadId": self.child}, event={**self.event, "turn_id": TURN1})
        self.assertFalse(wire["isError"], wire)
        self.assertEqual(self.original.read_bytes(), self.answer.raw)
        self.assertEqual(self.reader_factory.call_args.kwargs["reviewer_turn_id"], TURN2)
        self.assertEqual(self.store.read()[0]["attempts"][1]["source_identity"]["turn_id"], TURN2)
        self.assertEqual(self.public.call_count, 1)

    def test_changed_active_turn_is_rechecked_at_self_read_boundary(self):
        self.prepare_format_failure()
        with reviewer_hook_admission(self.service, self.parent, self.child, None,
                admit_current=self.basis_admit) as admit:
            self.assertEqual(admit.turn_id, TURN1)
            reader = AnswerReadHost(metadata={"threadId": self.child}, reviewer_id=self.child,
                reviewer_turn_id=admit.turn_id, admit=admit.check,
                automation_id="answer-hook", codex_home=self.root, confirmed_timezone="UTC",
                timezone_source="host_node_intl", command=["offline-unused"])
            self.correction()
            before = self.store.read()
            with self.assertRaisesRegex(HostAdapterError, "^caller_mismatch$"):
                reader._request("read_thread", dict(threadId=self.child, hostId="local", turnLimit=2,
                    includeOutputs=False, maxOutputCharsPerItem=256))
            self.assertEqual(self.store.read(), before)
        self.public.assert_not_called()
        self.assertFalse(self.original.exists())
        self.assertIs(self.service.admit_basis, self.basis_admit)

    def test_missing_executor_turn_cannot_follow_new_correction_before_publication(self):
        external = []
        def response(name, arguments, **kwargs):
            value = self.response(name, arguments, **kwargs)
            self.prepare_format_failure()
            self.correction()
            external.append(self.store.read())
            return value
        self.public.side_effect = response
        wire = self.call(caller={"threadId": self.child})
        self.assertTrue(wire["isError"], wire)
        self.assertEqual(len(external), 1)
        self.assertEqual(self.store.read(), external[0])
        self.assertFalse(self.original.exists())
        self.assertEqual(self.public.call_count, 1)
        self.assertIs(self.service.admit_basis, self.basis_admit)

    def test_off_hook_is_inert_even_for_reviewer_without_turn(self):
        self.allowed.return_value = False
        self.assertEqual(self.call(caller={"threadId": self.child}),
            {"content": [{"type": "text", "text": "{}"}], "isError": False})
        self.lookup.assert_not_called()
        self.location.assert_not_called()
        self.reader_factory.assert_not_called()
        self.public.assert_not_called()

    def test_basis_change_immediately_before_read_prevents_dispatch_and_publication(self):
        def changed_reader(**kwargs):
            result = self.reader(**kwargs)
            self.basis_admit.side_effect = AnswerError("binding_mismatch", reason="identity")
            return result
        self.reader_factory.side_effect = changed_reader
        before = self.store.read()
        wire = self.call(caller={"threadId": self.child})
        self.assertTrue(wire["isError"])
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.original.exists())
        self.public.assert_not_called()
        self.assertIs(self.service.admit_basis, self.basis_admit)

    def replacement_head(self, kind):
        basis = copy.deepcopy(self.metadata["basis"])
        if kind == "preparation":
            basis["preparation_id"] = "tg_review_preparation_ffffffffffffffff"
        else:
            basis["continuation"] = dict(continuation_id="tg_review_continuation_ffffffffffffffff",
                sequence=1, selection_digest="f" * 64)
        self.assertEqual(basis["execution_basis"], self.metadata["basis"]["execution_basis"])
        store = CollectionRepository(self.root / (kind + ".sqlite3"))
        store.initialize((basis,))
        replacement = CollectionService(self.root, store,
            original_paths=(".tmp-review/" + kind + ".json",), admit_basis=self.basis_admit)
        binding = {**self.metadata["binding"], "basis_digest": basis_digest(basis)}
        replacement.bind(1, binding)
        return replacement

    def test_same_basis_new_head_before_publication_leaves_old_slot_unmodified(self):
        self.caller = {"threadId": self.child}
        before = self.store.read()
        for kind in ("preparation", "continuation"):
            replacement = self.replacement_head(kind)
            self.lookup.return_value = (self.service, self.current)
            def response(name, arguments, **kwargs):
                result = self.response(name, arguments, **kwargs)
                self.lookup.return_value = (replacement, self.current)
                return result
            self.public.side_effect = response
            with self.subTest(kind=kind):
                wire = self.call()
                self.assertTrue(wire["isError"], wire)
                value = json.loads(wire["content"][0]["text"])
                self.assertEqual(value["diagnostic"]["local_state"], "not_attempted")
                self.assertEqual(self.store.read(), before)
                self.assertFalse(self.original.exists())
        self.assertIs(self.service.admit_basis, self.basis_admit)

    def test_same_basis_continuation_during_helper_initialization_blocks_read_dispatch(self):
        self.caller = {"threadId": self.child}
        replacement = self.replacement_head("continuation")
        arguments = dict(threadId=self.child, hostId="local", turnLimit=2,
            includeOutputs=False, maxOutputCharsPerItem=256)
        expected = dict(name="read_thread", arguments=arguments, _meta=self.caller)
        response = self.response("read_thread", arguments)
        def reader(**kwargs):
            kwargs.pop("server_path")
            return AnswerReadHost(**kwargs, command=[sys.executable, "-B", "-u", "-c", PEER,
                json.dumps(expected), json.dumps(response), "success"], timeout_seconds=2)
        self.reader_factory.side_effect = reader
        result, send, methods = protocol._ChildSession.result, protocol._ChildSession.send, []
        def initialized(child, identifier, **kwargs):
            value = result(child, identifier, **kwargs)
            if identifier == 1:
                self.lookup.return_value = (replacement, self.current)
            return value
        def sending(child, frame):
            methods.append(json.loads(frame)["method"])
            return send(child, frame)
        before = self.store.read()
        with patch.object(PublicMcpHost, "_request", new=SHARED_REQUEST), \
                patch.object(protocol._ChildSession, "result", new=initialized), \
                patch.object(protocol._ChildSession, "send", new=sending):
            wire = self.call()
        self.assertTrue(wire["isError"], wire)
        self.assertIn("initialize", methods)
        self.assertNotIn("tools/call", methods)
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.original.exists())
        self.assertIs(self.service.admit_basis, self.basis_admit)

    def test_missing_executor_turn_correction_during_helper_init_blocks_read_dispatch(self):
        self.caller = {"threadId": self.child}
        arguments = dict(threadId=self.child, hostId="local", turnLimit=2,
            includeOutputs=False, maxOutputCharsPerItem=256)
        expected = dict(name="read_thread", arguments=arguments, _meta=self.caller)
        response = self.response("read_thread", arguments)
        def reader(**kwargs):
            kwargs.pop("server_path")
            return AnswerReadHost(**kwargs, command=[sys.executable, "-B", "-u", "-c", PEER,
                json.dumps(expected), json.dumps(response), "success"], timeout_seconds=2)
        self.reader_factory.side_effect = reader
        result, send, methods, external = protocol._ChildSession.result, protocol._ChildSession.send, [], []
        def initialized(child, identifier, **kwargs):
            value = result(child, identifier, **kwargs)
            if identifier == 1:
                self.prepare_format_failure()
                self.correction()
                external.append(self.store.read())
            return value
        def sending(child, frame):
            methods.append(json.loads(frame)["method"])
            return send(child, frame)
        with patch.object(PublicMcpHost, "_request", new=SHARED_REQUEST), \
                patch.object(protocol._ChildSession, "result", new=initialized), \
                patch.object(protocol._ChildSession, "send", new=sending):
            wire = self.call()
        self.assertTrue(wire["isError"], wire)
        self.assertIn("initialize", methods)
        self.assertNotIn("tools/call", methods)
        self.assertEqual(len(external), 1)
        self.assertEqual(self.store.read(), external[0])
        self.assertFalse(self.original.exists())
        self.assertIs(self.service.admit_basis, self.basis_admit)


class SelfReadTransportTests(unittest.TestCase):
    def setUp(self):
        metadata, _ = metadata_fixture()
        self.parent = metadata["binding"]["parent_session_id"]
        self.child = metadata["binding"]["reviewer_session_id"]
        self.metadata = dict(threadId=self.child, turnId=TURN1,
            **{"openai/threadId": self.child, "openai/turnId": TURN1}, nested={"preserved": [True, 7]})
        self.options = dict(metadata=self.metadata, automation_id="answer-hook", codex_home=Path.cwd(),
            confirmed_timezone="UTC", timezone_source="host_node_intl", command=["offline-unused"])
        self.admit = Mock()
        self.host = AnswerReadHost(**self.options, reviewer_id=self.child, reviewer_turn_id=TURN1, admit=self.admit)
        self.arguments = dict(threadId=self.child, hostId="local", turnLimit=2,
            includeOutputs=False, maxOutputCharsPerItem=256)

    def test_only_exact_typed_self_read_can_reach_shared_transport(self):
        with patch.object(PublicMcpHost, "_request", return_value={}) as shared:
            self.host._request("read_thread", self.arguments)
            shared.assert_called_once_with("read_thread", self.arguments, timeout=None)
        bad = [("automation_update", {}), ("send_message_to_thread", {}), ("wait_threads", {}),
            ("read_thread", {**self.arguments, "threadId": self.parent}),
            ("read_thread", {**self.arguments, "cursor": "older"}),
            ("read_thread", {**self.arguments, "turnLimit": 2.0}),
            ("read_thread", {**self.arguments, "includeOutputs": 0}),
            ("read_thread", {**self.arguments, "maxOutputCharsPerItem": 256.0}),
            ("read_thread", {**self.arguments, "hostId": "remote"})]
        with patch.object(PublicMcpHost, "_request") as shared:
            for name, arguments in bad:
                with self.subTest(name=name, arguments=arguments), self.assertRaises(HostAdapterError):
                    self.host._request(name, arguments)
                with self.assertRaises(HostAdapterError):
                    self.host._tool_call_bytes(name, arguments)
            shared.assert_not_called()

    def test_frame_preserves_genuine_metadata_and_rechecks_admission(self):
        before = copy.deepcopy(self.metadata)
        frame = json.loads(self.host._tool_call_bytes("read_thread", self.arguments))
        self.assertEqual(frame["params"]["_meta"], before)
        self.assertEqual(self.host._metadata, before)
        self.assertEqual(self.metadata, before)
        self.admit.assert_called_once()
        self.admit.side_effect = AnswerError("binding_mismatch", reason="identity")
        with self.assertRaises(AnswerError):
            self.host._tool_call_bytes("read_thread", self.arguments)

    def test_ordinary_hosts_still_reject_executor_as_reviewer(self):
        for kind, extra in ((PublicMcpHost, {}), (ManagedHost, {"task_id": "tg_task_0000000000000001"})):
            with self.subTest(kind=kind.__name__), self.assertRaisesRegex(HostAdapterError, "^invalid_child_set$"):
                kind(**self.options, reviewer_ids=(self.child,), **extra)

    def test_recovery_adapter_cannot_infer_a_parent_dispatch_turn(self):
        host = PublicAnswerHost(self.host, self.parent)
        with patch.object(self.host, "_json_call") as read:
            with self.assertRaises(AnswerError):
                host.bind_dispatch("dispatch-1", self.child, creation_context=True)
            with self.assertRaises(AnswerError):
                host.bind_reviewers((self.child,), creation_context=True)
            read.assert_not_called()


class ExecutorTurnContextTests(unittest.TestCase):
    def test_only_hook_adapter_accepts_absent_turn_without_inventing_metadata(self):
        caller = {"threadId": OTHER}
        self.assertIsNone(hook_executor_turn_id(caller))
        self.assertEqual(caller, {"threadId": OTHER})
        with self.assertRaises(HostAdapterError) as caught:
            executor_turn_id(caller)
        self.assertEqual(caught.exception.code, "executor_context_required")
        self.assertEqual(caught.exception.executor_context, "turn_missing")

    def test_existing_valid_aliases_and_matching_corroboration_remain_accepted(self):
        cases = [{key: TURN1} for key in (
            "turnId", "openai/turnId", "openai/turn_id", "codexTurnId", "codex_turn_id", "turn_id")]
        cases += [{"turn": {"id": TURN1}},
            {"x-codex-turn-metadata": {"turn_id": TURN1}},
            {"x-codex-turn-metadata": json.dumps({"turn_id": TURN1})},
            {"turnId": TURN1, "x-codex-turn-metadata": {"turn": {"id": TURN1}}}]
        for fields in cases:
            caller = {"threadId": OTHER, **fields}
            unchanged = copy.deepcopy(caller)
            with self.subTest(fields=fields):
                self.assertEqual(executor_turn_id(caller), TURN1)
                self.assertEqual(caller, unchanged)

    def test_conflicting_aliases_are_still_mismatch_not_missing_context(self):
        with self.assertRaises(HostAdapterError) as caught:
            executor_turn_id({"threadId": OTHER, "turnId": TURN1, "openai/turnId": TURN2})
        self.assertEqual(caught.exception.code, "caller_mismatch")
        self.assertIsNone(caught.exception.executor_context)

    def test_unadmitted_metadata_does_not_acquire_a_turn_classification(self):
        for caller in (None, {}, {"threadId": OTHER, "x-codex-turn-metadata": []}):
            with self.subTest(caller=caller), self.assertRaises(HostAdapterError) as caught:
                executor_turn_id(caller)
            self.assertEqual(caught.exception.code, "executor_context_required")
            self.assertIsNone(caught.exception.executor_context)


if __name__ == "__main__":
    unittest.main()
