"""Public own-turn adapter observations, fixed retrieval and hook hints."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_host import PublicAnswerHost
from task_governance_tool.review_answer_hook import collect_event, decode_event
from test_review_answer_pure_values import metadata_fixture, TURN1, TURN2


def turn(identifier, *, items=None, status="completed"):
    return dict(id=identifier, status=status, items=items or [], error=None,
                startedAt=1, completedAt=2 if status != "inProgress" else None, durationMs=1)


def page(thread, limit, *turns):
    return dict(schemaVersion=1, thread=dict(id=thread, kind="codex", hostId="local"),
                page=dict(order="newest_first", limit=limit, hasMore=False, nextCursor=None), turns=list(turns))


class HostTests(unittest.TestCase):
    def setUp(self):
        self.metadata, self.answer = metadata_fixture()
        self.parent = self.metadata["binding"]["parent_session_id"]
        self.child = self.metadata["binding"]["reviewer_session_id"]
        self.parent_turn = "30000000-0000-4000-8000-000000000001"
        self.started = dict(type="subAgentActivity", id="dispatch-1", kind="started", agentThreadId=self.child)
        self.public = Mock()
        self.host = PublicAnswerHost(self.public, self.parent, self.parent_turn)
        self.final = dict(type="agentMessage", id="message-1", phase="final_answer", text=self.answer.raw.decode())
        self.arguments = dict(basis_digest=self.metadata["binding"]["basis_digest"], slot=1,
            attempt=0, reviewer_session_id=self.child, turn_id=TURN1, expected_message_id=None)

    def bind_pages(self, child_status="completed"):
        return [page(self.parent, 1, turn(self.parent_turn, items=[self.started])),
                page(self.child, 1, turn(TURN1, status=child_status))]

    def test_fresh_own_turn_admission_accepts_completion_race_and_inherited_context(self):
        self.public._json_call.side_effect = self.bind_pages()
        result = self.host.bind_dispatch("dispatch-1", self.child, creation_context=True)
        self.assertEqual(result["initial_turn_id"], TURN1)
        # No fork-context claim or require-in-progress condition exists here.
        self.assertEqual(len(self.public._json_call.call_args_list), 2)
        for call in self.public._json_call.call_args_list:
            self.assertEqual(call.args[0], "read_thread")
            self.assertEqual(call.args[1]["turnLimit"], 1)
            self.assertNotIn("cursor", call.args[1])

    def test_missing_prebinding_context_never_infers_restart_binding(self):
        with self.assertRaises(AnswerError):
            self.host.bind_dispatch("dispatch-1", self.child)
        self.assertFalse(self.public._json_call.called)

    def test_parent_completion_notice_keeps_the_original_dispatch(self):
        for single in (False, True):
            for status in ("inProgress", "completed"):
                with self.subTest(single=single, status=status):
                    values = self.bind_pages(status)
                    values[0]["turns"][0]["items"].append(dict(type="subAgentActivity",
                        id="completed-1", kind="completed", agentThreadId=self.child))
                    self.public.reset_mock()
                    self.public._json_call.side_effect = values
                    bound = (self.host.bind_dispatch("dispatch-1", self.child, creation_context=True)
                             if single else self.host.bind_reviewers((self.child,), creation_context=True)[0])
                    self.assertEqual(bound["initial_turn_id"], TURN1)
                    self.assertEqual(self.public._json_call.call_count, 2)

    def test_completion_notice_does_not_hide_reuse_or_ambiguous_activity(self):
        completion = dict(type="subAgentActivity", id="completed-1", kind="completed", agentThreadId=self.child)
        extras = [dict(completion, id="other", kind=kind)
                  for kind in ("started", "interacted", "interrupted", "failed", "unknown", "completed")]
        extras += [dict(completion, agentThreadId="40000000-0000-4000-8000-000000000001"),
                   dict(self.started, agentThreadId="40000000-0000-4000-8000-000000000001")]
        for single in (False, True):
            for extra in extras:
                with self.subTest(single=single, extra=extra):
                    values = self.bind_pages()
                    values[0]["turns"][0]["items"].extend([completion, extra])
                    self.public.reset_mock()
                    self.public._json_call.side_effect = values
                    with self.assertRaises(AnswerError):
                        if single:
                            self.host.bind_dispatch("dispatch-1", self.child, creation_context=True)
                        else:
                            self.host.bind_reviewers((self.child,), creation_context=True)
                    self.public._json_call.assert_called_once()

    def test_completion_notice_retains_content_and_single_child_turn_checks(self):
        for single in (False, True):
            for fault in ("clipped", "missing_id", "dispatch_id", "extra_turn", "extra_page"):
                with self.subTest(single=single, fault=fault):
                    values = self.bind_pages()
                    completion = dict(type="subAgentActivity", id="completed-1", kind="completed", agentThreadId=self.child)
                    values[0]["turns"][0]["items"].append(completion)
                    if fault == "clipped": completion["truncated"] = True
                    elif fault == "missing_id": completion.pop("id")
                    elif fault == "dispatch_id": completion["id"] = "dispatch-1"
                    elif fault == "extra_turn": values[1]["turns"].append(turn(TURN2))
                    else: values[1]["page"].update(hasMore=True, nextCursor="older")
                    self.public._json_call.side_effect = values
                    with self.assertRaises(AnswerError):
                        if single:
                            self.host.bind_dispatch("dispatch-1", self.child, creation_context=True)
                        else:
                            self.host.bind_reviewers((self.child,), creation_context=True)

    def test_runtime_multi_binding_reads_one_parent_and_each_exact_child_once(self):
        other = "40000000-0000-4000-8000-000000000001"
        parent = page(self.parent, 1, turn(self.parent_turn, items=[self.started,
            dict(type="subAgentActivity", id="completed-1", kind="completed", agentThreadId=self.child),
            dict(type="subAgentActivity", id="dispatch-2", kind="started", agentThreadId=other)]))
        self.public._json_call.side_effect = [parent, page(self.child, 1, turn(TURN1)), page(other, 1, turn(TURN2))]
        result = self.host.bind_reviewers((self.child, other), creation_context=True)
        self.assertEqual(tuple(row["dispatch_id"] for row in result), ("dispatch-1", "dispatch-2"))
        self.assertEqual(tuple(row["initial_turn_id"] for row in result), (TURN1, TURN2))
        self.assertEqual([call.args[1]["threadId"] for call in self.public._json_call.call_args_list], [self.parent, self.child, other])

    def test_runtime_binding_rejects_dispatch_identity_reused_by_other_child(self):
        parent = page(self.parent, 1, turn(self.parent_turn, items=[self.started,
            dict(self.started, agentThreadId="40000000-0000-4000-8000-000000000001")]))
        self.public._json_call.return_value = parent
        with self.assertRaises(AnswerError):
            self.host.bind_reviewers((self.child,), creation_context=True)
        self.public._json_call.assert_called_once()

    def test_failed_or_interrupted_turn_can_bind_without_becoming_success(self):
        for status in ("failed", "interrupted"):
            values = self.bind_pages(status)
            values[1]["turns"][0]["error"] = {"message": "private execution failure"}
            self.public._json_call.side_effect = values
            with self.subTest(status=status):
                bound = self.host.bind_reviewers((self.child,), creation_context=True)
                self.assertEqual(bound[0]["initial_turn_id"], TURN1)
                failed = turn(TURN1, items=[self.final], status=status)
                failed["error"] = {"message": "private execution failure"}
                self.public._json_call.side_effect = None
                self.public._json_call.return_value = page(self.child, 2, failed)
                terminal = self.host.observe_terminal(**{key: value for key, value in self.arguments.items() if key != "expected_message_id"})
                self.assertEqual(terminal["terminal"], status)
                self.assertNotIn("private", json.dumps(terminal))

    def test_observed_interaction_or_known_uncertain_send_rejects(self):
        values = self.bind_pages()
        values[0]["turns"][0]["items"].append(dict(type="subAgentActivity", id="interaction", kind="interacted", agentThreadId=self.child))
        self.public._json_call.side_effect = values
        with self.assertRaises(AnswerError):
            self.host.bind_dispatch("dispatch-1", self.child, creation_context=True)
        self.assertEqual(self.public._json_call.call_count, 1)
        self.public.reset_mock()
        with self.assertRaises(AnswerError):
            self.host.bind_dispatch("dispatch-1", self.child, creation_context=True, known_conflict=True)
        self.assertFalse(self.public._json_call.called)

    def test_child_page_requires_exact_typed_single_own_turn_proof(self):
        mutations = [lambda x: x.update(schemaVersion=True),
            lambda x: x["thread"].update(hostId="remote"),
            lambda x: x["page"].update(limit=True),
            lambda x: x["page"].update(hasMore=True, nextCursor="older"),
            lambda x: x["page"].pop("nextCursor"),
            lambda x: x["turns"].append(turn(TURN2))]
        for mutate in mutations:
            values = self.bind_pages()
            mutate(values[1])
            self.public._json_call.side_effect = values
            with self.subTest(mutate=mutate), self.assertRaises(AnswerError):
                self.host.bind_dispatch("dispatch-1", self.child, creation_context=True)

    def test_initial_interaction_reason_requires_unambiguous_complete_same_child_activity(self):
        for single in (False, True):
            for fault in (None, "duplicate_start", "wrong_child", "duplicate_id", "clipped", "other_kind"):
                with self.subTest(single=single, fault=fault):
                    pages = self.bind_pages()
                    items = pages[0]["turns"][0]["items"]
                    interaction = dict(type="subAgentActivity", id="interaction-1",
                                       kind="interacted", agentThreadId=self.child,
                                       text="private message must not appear")
                    items.append(interaction)
                    if fault == "duplicate_start": items.append(dict(self.started, id="dispatch-2"))
                    elif fault == "wrong_child": interaction.update(agentThreadId=self.parent, id="dispatch-1")
                    elif fault == "duplicate_id": interaction["id"] = "dispatch-1"
                    elif fault == "clipped": interaction["truncated"] = True
                    elif fault == "other_kind": items.append(dict(interaction, id="other", kind="unknown"))
                    self.public.reset_mock()
                    self.public._json_call.side_effect = pages
                    with self.assertRaises(AnswerError) as raised:
                        if single:
                            self.host.bind_dispatch("dispatch-1", self.child, creation_context=True)
                        else:
                            self.host.bind_reviewers((self.child,), creation_context=True)
                    failure = raised.exception.failure
                    if fault is None:
                        self.assertEqual(failure, dict(**{"class": "binding_mismatch"},
                            field=None, reason="initial_binding_interacted"))
                    else:
                        self.assertNotEqual(failure["reason"], "initial_binding_interacted")
                    self.assertNotIn("private", json.dumps(failure))
                    self.public._json_call.assert_called_once()

    def test_bound_retrieval_selects_saved_turn_even_when_newer_turn_exists(self):
        self.public._json_call.return_value = page(self.child, 2,
            turn(TURN2, items=[dict(self.final, id="unrelated", text="unrelated")]), turn(TURN1, items=[self.final]))
        result = self.host.observe_answer(**self.arguments)
        self.assertEqual(result["answer_bytes"], self.answer.raw)
        self.assertEqual(result["source_identity"]["turn_id"], TURN1)
        self.public._json_call.assert_called_once_with("read_thread", dict(threadId=self.child,
            hostId="local", turnLimit=2, includeOutputs=False, maxOutputCharsPerItem=256))

    def test_missing_saved_turn_does_not_paginate_or_substitute_latest(self):
        value = page(self.child, 2, turn(TURN2, items=[self.final]))
        value["page"].update(hasMore=True, nextCursor="older")
        self.public._json_call.return_value = value
        with self.assertRaises(AnswerError):
            self.host.observe_answer(**self.arguments)
        self.public._json_call.assert_called_once()

    def test_known_message_identity_and_explicit_visible_errors_refused(self):
        cases = [lambda x: x["turns"][0]["items"][0].update(truncated=True),
            lambda x: x["turns"][0].update(error={"message": "private error"}),
            lambda x: x["turns"][0]["items"].append(copy.deepcopy(self.final)),
            lambda x: x["turns"][0]["items"][0].update(id="other"),
            lambda x: x["turns"][0]["items"][0].update(text="\ud800")]
        for mutate in cases:
            value = page(self.child, 2, turn(TURN1, items=[copy.deepcopy(self.final)]))
            mutate(value)
            self.public._json_call.return_value = value
            with self.subTest(mutate=mutate), self.assertRaises(AnswerError):
                self.host.observe_answer(**dict(self.arguments, expected_message_id="message-1"))

    def test_answer_capacity_does_not_truncate_or_summarize(self):
        self.public._json_call.return_value = page(self.child, 2, turn(TURN1, items=[dict(self.final, text="a" * 262145)]))
        with self.assertRaises(AnswerError) as raised:
            self.host.observe_answer(**self.arguments)
        self.assertEqual(raised.exception.failure["class"], "capacity_exceeded")

    def test_no_unproven_native_correction_send(self):
        self.assertEqual(self.host.send_correction(), dict(effect="not_sent", correction_turn_id=None, source=None))
        self.assertFalse(self.public._json_call.called)

    def test_tracking_arm_requires_terminal_latest_turn_and_records_no_answer(self):
        for status in ("completed", "failed", "interrupted"):
            self.public._json_call.return_value = page(self.child, 2, turn(TURN1, status=status,
                items=[dict(self.final, text="not retained during arming")]))
            baseline = self.host.observe_tracking_baseline(self.child)
            self.assertEqual(baseline, dict(turn_id=TURN1, terminal=status,
                binding_source=dict(adapter="codex-app-tools-review-answer", version=2, operation="reviewer_tracking")))
        self.public._json_call.return_value = page(self.child, 2, turn(TURN1, status="inProgress"))
        with self.assertRaises(AnswerError):
            self.host.observe_tracking_baseline(self.child)

    def tracking_arguments(self, boundary="exclusive"):
        return dict(basis_digest=self.metadata["binding"]["basis_digest"], slot=1,
            reviewer_session_id=self.child, boundary_turn_id=TURN1, boundary=boundary)

    def test_tracking_excludes_prior_turn_then_selects_latest_completed_final(self):
        self.public._json_call.return_value = page(self.child, 2, turn(TURN1, items=[self.final]))
        with self.assertRaises(AnswerError):
            self.host.observe_latest_answer(**self.tracking_arguments())
        self.public._json_call.return_value = page(self.child, 2,
            turn(TURN2, items=[dict(self.final, id="later-final")]), turn(TURN1, items=[self.final]))
        result = self.host.observe_latest_answer(**self.tracking_arguments())
        self.assertEqual(result["source_identity"], dict(reviewer_session_id=self.child,
            turn_id=TURN2, message_id="later-final"))
        self.assertEqual(result["terminal"], "completed")
        self.assertEqual(result["answer_source"]["version"], 2)

    def test_tracking_never_falls_back_from_running_failed_or_missing_latest_final(self):
        for status, items in (("inProgress", [self.final]), ("failed", [self.final]),
                              ("interrupted", [self.final]), ("completed", [])):
            self.public._json_call.return_value = page(self.child, 2,
                turn(TURN2, items=items, status=status), turn(TURN1, items=[self.final]))
            with self.subTest(status=status, items=bool(items)), self.assertRaises(AnswerError):
                self.host.observe_latest_answer(**self.tracking_arguments())

    def test_initial_tracking_accepts_its_original_turn_but_requires_completion(self):
        for status in ("inProgress", "completed"):
            self.public._json_call.return_value = page(self.child, 2, turn(TURN1, status=status, items=[self.final]))
            if status == "completed":
                result = self.host.observe_latest_answer(**self.tracking_arguments("inclusive"))
                self.assertEqual(result["source_identity"]["turn_id"], TURN1)
            else:
                with self.assertRaises(AnswerError):
                    self.host.observe_latest_answer(**self.tracking_arguments("inclusive"))

    def test_initial_tracking_never_upgrades_an_invalid_dispatch_proof_source(self):
        from task_governance_tool.review_answer_runtime import bind_initial
        basis = {**self.metadata["basis"], "version": 2}
        service = Mock()
        service.repository.read.return_value = [dict(basis=basis, binding=None)]
        host = Mock()
        fact = dict(dispatch_id="dispatch-1", parent_session_id=self.parent,
            reviewer_session_id=self.child, initial_turn_id=TURN1)
        for invalid in (None, {}, dict(adapter="other", version=1, operation="dispatch_binding"),
                        dict(adapter="codex-app-tools-review-answer", version=2, operation="reviewer_tracking")):
            host.bind_reviewers.return_value = ({**fact, "source": invalid},)
            with self.subTest(source=invalid), self.assertRaises(AnswerError):
                bind_initial(service, host, (self.child,), creation_context=True)
        service.bind.assert_not_called()

    def test_hook_is_identity_hint_and_discards_body_and_parent_turn(self):
        event = dict(hook_event_name="SubagentStop", session_id=self.parent, agent_id=self.child,
            turn_id=self.parent_turn, stop_hook_active=False, last_assistant_message="not an answer",
            agent_transcript_path="private-path")
        raw = json.dumps(event).encode()
        self.assertEqual(set(decode_event(raw)), {"parent_session_id", "reviewer_session_id", "turn_id"})
        service = Mock()
        service.repository.read.return_value = [dict(binding=self.metadata["binding"])]
        self.assertEqual(collect_event(raw, service=service, host=self.host), {})
        service.recover.assert_called_once_with(1, self.host, require_terminal=False)
        event["session_id"] = "40000000-0000-4000-8000-000000000001"
        with self.assertRaises(AnswerError):
            collect_event(json.dumps(event).encode(), service=service, host=self.host)
        self.assertEqual(service.recover.call_count, 1)

    def test_hook_duplicate_keys_invalid_utf8_and_oversize_rejected(self):
        for raw in (b'{"hook_event_name":"SubagentStop","hook_event_name":"Stop"}', b"\xff", b"x" * 262145):
            with self.subTest(size=len(raw)), self.assertRaises(AnswerError):
                decode_event(raw)


if __name__ == "__main__":
    unittest.main()
