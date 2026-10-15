"""Public-helper composition in an isolated candidate project; no host mutation."""

from pathlib import Path
from types import SimpleNamespace
import json
import io
import unittest
from unittest import mock

from tests import test_review_preparation as fixtures
from tests.test_review_continuation import A, B
from tests import test_review_continuation_registration as registration
from task_governance_tool import review_handoff_transport as handoff_transport
from task_governance_tool import review_handoff as files, review_results, storage, review_session_transport as transport
from task_governance_tool.review_continuation_handoff_repository import read_state
from task_governance_tool.session_identity import CallerIdentity
from contextlib import closing
from task_governance_tool import review_handoff_continuation as continuation
from task_governance_tool.review_handoff_transport import HandoffError
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository


class ContinuationEntryTests(unittest.TestCase):
    def test_public_parser_passes_bounded_selection_without_extra_mutation(self):
        raw = b'{"version":1,"reviewers":[]}'
        response = dict(ok=False, handoff=dict(status="unavailable"), errors=[])
        with mock.patch.object(continuation, "continue_reviews", return_value=response) as invoked, \
             mock.patch.object(files.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw))), \
             mock.patch.object(handoff_transport, "emit_json", return_value=True):
            result = files.main(["continue", "--repo", ".", "--task-id", "tg_task_" + "1" * 16,
                "--target-generation", "1", "--directory", "reviews/new"])
        self.assertEqual(result, 1)
        self.assertEqual(invoked.call_args.args[2], raw)
        self.assertEqual(invoked.call_args.args[1].target_generation, 1)

    def test_records_only_accepts_only_the_closed_manual_command_arguments(self):
        from task_governance_tool.task_record_policy import validate_record_arguments, RecordPolicyError
        args = ["--records-only", "continue", "--repo", "project", "--task-id", "tg_task_" + "1" * 16,
            "--target-generation", "1", "--directory", "reviews/new"]
        self.assertEqual(validate_record_arguments("review_handoff.py", args), "project")
        with self.assertRaises(RecordPolicyError):
            validate_record_arguments("review_handoff.py", args + ["--commit-approved"])


class HandoffContinuationTests(unittest.TestCase):
    setUp = fixtures.PreparationPublicationTests.setUp
    physical = fixtures.PreparationPublicationTests.physical
    publish = fixtures.PreparationPublicationTests.publish

    def ready(self):
        self.physical()
        self.root_preparation = self.publish()
        paths = self.resolution.paths
        paths.review_wait_request_store = lambda *_: self.root / "request.sqlite"
        paths.review_wait_dispatch_store = lambda *_: self.root / "dispatch.sqlite"
        paths.review_continuation_store = lambda *_: self.root / "transition.sqlite"
        self.enterContext(mock.patch.object(continuation, "resolve", return_value=self.resolution))
        self.args = SimpleNamespace(task_id=self.task_id, target_generation=self.basis["target_generation"], directory="reviews/continue-1", records_only=False)
        self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=A, action="supplement", prior_receipt_id=None, reason="Restore the missing source material")])).encode()

    def call(self):
        return continuation.continue_reviews(self.repo, self.args, self.raw, caller=self.caller)

    def test_missing_legacy_locator_never_searches_or_invents_binding(self):
        self.physical()
        paths = self.resolution.paths
        self.enterContext(mock.patch.object(continuation, "resolve", return_value=self.resolution))
        self.args = SimpleNamespace(task_id=self.task_id, target_generation=self.basis["target_generation"], directory="reviews/new", records_only=False)
        self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=A, action="supplement", prior_receipt_id=None, reason="Missing source")])).encode()
        result = self.call()
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "review_continuation_unavailable")
        self.assertFalse((self.repo / "reviews/new").exists())

    def test_manual_prepare_has_one_selected_request_and_no_finalization_authority(self):
        self.ready()
        result = self.call()
        self.assertTrue(result["ok"], result)
        handoff = result["handoff"]
        self.assertEqual(handoff["route"], "manual")
        self.assertIn("submit_command", handoff)
        self.assertNotIn("finalization_command", handoff)
        self.assertEqual(len(handoff["review_requests"]), 1)
        self.assertEqual(handoff["review_requests"][0]["session_id"], A)
        packet = json.loads((self.repo / handoff["packet_path"]).read_bytes())
        self.assertEqual(packet["review_session_context"]["continuation_id"], handoff["continuation_id"])
        self.assertEqual(TransitionRepository(self.root / "transition.sqlite").read()[-1].phase, "prepared")

    def test_same_request_reconciles_and_returns_no_redispatch_instruction(self):
        self.ready()
        first = self.call()
        self.assertTrue(first["ok"], first)
        before = {path.relative_to(self.repo).as_posix(): path.read_bytes() for path in (self.repo / "reviews").rglob("*") if path.is_file()}
        replay = self.call()
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["handoff"]["replayed"])
        self.assertEqual(replay["handoff"]["continuation_id"], first["handoff"]["continuation_id"])
        self.assertEqual(replay["handoff"]["review_requests"], [])
        self.assertEqual({path.relative_to(self.repo).as_posix(): path.read_bytes() for path in (self.repo / "reviews").rglob("*") if path.is_file()}, before)

    def save_actor(self, actor, packet_path, output):
        raw = registration.ContinuationRegistrationTests.original(self, actor)[0]
        with mock.patch("task_governance_tool.session_identity.capture_caller_identity", return_value=CallerIdentity(actor)), \
             mock.patch("task_governance_tool.usage_lifecycle.register_reviewer"):
            result = files.save(self.repo, packet_path, output, raw)
        self.assertEqual(result["status"], "saved")
        return raw

    def submit(self, handoff):
        task, framed = continuation.assemble_submission(self.repo, handoff["packet_path"],
            [row["result_path"] for row in handoff["review_requests"]], caller=self.caller)
        decoded = transport.decode_submission(framed)
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            return review_results.add_review_results(connection, self.target.project, task, decoded.document,
                database_target=self.target, caller=self.caller, session_bindings=decoded.bindings,
                continuation_identity=decoded.continuation, retained_receipt_ids=decoded.retained_receipt_ids,
                retained_originals=decoded.retained_originals)

    def test_complete_manual_flow_keeps_unregistered_v1_original_and_new_v2_result(self):
        self.ready()
        old = self.save_actor(B, "reviews/packet.json", "reviews/b.json")
        result = self.call()
        self.assertTrue(result["ok"], result)
        handoff = result["handoff"]
        self.save_actor(A, handoff["packet_path"], handoff["review_requests"][0]["result_path"])
        outcome = self.submit(handoff)
        self.assertTrue(outcome["review_gate"]["satisfied"])
        self.assertEqual((self.repo / "reviews/b.json").read_bytes(), old)

    def test_repeated_unjudged_continuation_carries_exact_old_v2_original(self):
        self.ready()
        self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=actor, action="supplement", prior_receipt_id=None,
            reason="Missing original material") for actor in (A,B)])).encode()
        first = self.call()
        self.assertTrue(first["ok"], first)
        saved = self.save_actor(A, first["handoff"]["packet_path"], first["handoff"]["review_requests"][0]["result_path"])
        self.args.directory = "reviews/continue-2"
        self.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=B, action="supplement", prior_receipt_id=None, reason="Continue only missing B")])).encode()
        second = self.call()
        self.assertTrue(second["ok"], second)
        self.save_actor(B, second["handoff"]["packet_path"], second["handoff"]["review_requests"][0]["result_path"])
        outcome = self.submit(second["handoff"])
        self.assertTrue(outcome["review_gate"]["satisfied"])
        self.assertEqual((self.repo / first["handoff"]["review_requests"][0]["result_path"]).read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
