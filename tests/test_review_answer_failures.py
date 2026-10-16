"""Collected errors survive typed framing, public CLI and uncertain admission."""

import base64
import hashlib
import io
import json
import shlex
import subprocess
import sys
from types import SimpleNamespace
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import TestCase, mock

from tests.test_review_answer_pure_values import metadata_fixture
from tests.test_review_handoff_preparation import PreparationFixture
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_transport import (
    FORMAT, LIMIT, CollectedItem, decode_collected_submission,
    frame_collected_submission, answer_input_error,
)
from task_governance_tool.review_results import ReviewResultInputError
from task_governance_tool.review_session_transport import decode_submission


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


def envelope(*payloads):
    rows = []
    for slot, payload in enumerate(payloads or ({},), 1):
        metadata, _ = metadata_fixture()
        answer = dict(verdict="pass", summary="Assigned review complete", findings=[], checks=[], limitations=[])
        answer.update(payload)
        raw = encode(answer)
        metadata["binding"].update(slot=slot, reviewer_key="reviewer-" + str(slot),
            reviewer_session_id=f"10000000-0000-4000-8000-{slot + 100:012x}")
        metadata["source_identity"]["reviewer_session_id"] = metadata["binding"]["reviewer_session_id"]
        metadata["original"].update(byte_length=len(raw), digest="sha256:" + hashlib.sha256(raw).hexdigest(),
            payload_start=0, payload_length=len(raw), payload_digest="sha256:" + hashlib.sha256(raw).hexdigest())
        rows.append(dict(kind="collected", slot=slot, original_base64=base64.b64encode(raw).decode(), metadata=metadata))
    return dict(format=FORMAT, preparation_id=rows[0]["metadata"]["basis"]["preparation_id"],
        continuation=None, items=rows, retained_receipt_ids=[], retained_originals=[])


def findings(count):
    return [dict(severity="low", summary="Finding " + str(index)) for index in range(count)]


class CollectedFailureTests(TestCase):
    def rejected(self, value, code, *, field=None, reason=None):
        with self.assertRaises(ReviewResultInputError) as caught:
            decode_collected_submission(encode(value))
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.field, field)
        if reason is not None:
            self.assertEqual(caught.exception.message, "collected review answer: " + reason)
        return caught.exception

    def test_aggregate_findings_limit_survives_framing_and_decode(self):
        self.rejected(envelope(dict(findings=findings(33)), dict(findings=findings(32))),
            "capacity_exceeded", field="findings", reason="bound")
        admitted = decode_collected_submission(encode(envelope(
            dict(findings=findings(32)), dict(findings=findings(32)))))
        too_many = decode_collected_submission(encode(envelope(dict(findings=findings(33)))))
        with self.assertRaises(ReviewResultInputError) as caught:
            frame_collected_submission(admitted.preparation_id, (too_many.items[0], admitted.items[1]))
        self.assertEqual(caught.exception.code, "capacity_exceeded")
        self.assertEqual(caught.exception.field, "findings")

    def test_individual_limits_preserve_fixed_field_and_reason(self):
        for payload, field in ((dict(findings=findings(65)), "findings"),
                               (dict(summary="x" * 1001), "summary"),
                               (dict(checks=["Checked"] * 33), "checks")):
            with self.subTest(field=field):
                self.rejected(envelope(payload), "capacity_exceeded", field=field, reason="bound")

    def test_privacy_precedes_semantic_capacity_and_binding(self):
        value = envelope(dict(summary="token=PRIVATE_SENTINEL", verdict="bad", findings=findings(65)))
        value["items"][0]["metadata"]["binding"]["basis_digest"] = "sha256:" + "f" * 64
        error = self.rejected(value, "privacy_rejected", reason="privacy")
        self.assertNotIn("PRIVATE_SENTINEL", str(error))

    def test_shape_precedes_privacy_without_echoing_unknown_keys(self):
        value = envelope(dict(summary="token=PRIVATE_SENTINEL"))
        value["items"][0]["metadata"]["UNKNOWN_SENTINEL"] = "private"
        error = self.rejected(value, "format_invalid", reason="shape")
        self.assertNotIn("SENTINEL", str(error))
        value = envelope(dict(UNKNOWN_SENTINEL="private"))
        self.rejected(value, "format_invalid", reason="shape")

    def test_identity_mismatch_is_not_format_failure(self):
        for area, key, replacement in (
            ("source_identity", "turn_id", "10000000-0000-4000-8000-000000000009"),
            ("original", "digest", "sha256:" + "f" * 64),
            ("binding", "basis_digest", "sha256:" + "f" * 64),
        ):
            value = envelope()
            value["items"][0]["metadata"][area][key] = replacement
            with self.subTest(area=area):
                self.rejected(value, "binding_mismatch", reason="identity")
        value = envelope()
        value["preparation_id"] = "tg_review_preparation_" + "f" * 16
        self.rejected(value, "binding_mismatch", reason="identity")

    def test_typed_whole_input_cap_and_untyped_legacy_boundary_are_distinct(self):
        value = envelope()
        oversized = encode(value) + b" " * LIMIT
        with self.assertRaises(ReviewResultInputError) as caught:
            decode_collected_submission(oversized)
        self.assertEqual(caught.exception.code, "capacity_exceeded")
        # The public generic reader cannot infer a protocol from clipped stdin.
        with self.assertRaises(Exception) as caught:
            decode_submission(oversized[:LIMIT + 1])
        self.assertEqual(caught.exception.code, "invalid_review_evidence")
        admitted = decode_collected_submission(encode(envelope(dict(
            checks=["x" * 1000] * 32, limitations=["x" * 1000] * 32))))
        item = admitted.items[0]
        with self.assertRaises(ReviewResultInputError) as caught:
            frame_collected_submission(admitted.preparation_id, (item,) * 4)
        self.assertEqual(caught.exception.code, "capacity_exceeded")

    def test_untrusted_error_details_cannot_escape_mapping(self):
        error = answer_input_error(AnswerError("PRIVATE_SENTINEL", "UNKNOWN_SENTINEL", "private detail"))
        self.assertEqual((error.code, error.field, error.message),
            ("format_invalid", None, "collected review answer: shape"))


class PublicCollectedFailureTests(PreparationFixture):
    def test_public_cli_errors_preserve_classes_and_leave_database_unchanged(self):
        task = self.task()
        before = self.install.db_path.read_bytes()
        private = envelope(dict(summary="token=PRIVATE_SENTINEL", findings=findings(65)))
        unknown = envelope(dict(UNKNOWN_SENTINEL="private"))
        wrong_binding = envelope()
        wrong_binding["items"][0]["metadata"]["source_identity"]["turn_id"] = "10000000-0000-4000-8000-000000000009"
        cases = (
            (encode(envelope(dict(findings=findings(33)), dict(findings=findings(32)))), "capacity_exceeded", "findings"),
            (encode(envelope(dict(summary="x" * 1001))), "capacity_exceeded", "summary"),
            (encode(private), "privacy_rejected", None),
            (encode(unknown), "format_invalid", None),
            (encode(wrong_binding), "binding_mismatch", None),
            (b'{"version":1}', "invalid_review_evidence", "contract_revision"),
            ((encode(envelope()) + b" " * LIMIT), "invalid_review_evidence", None),
        )
        for raw, code, field in cases:
            with self.subTest(code=code, field=field):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", str(self.install.entrypoint),
                    "review", "result", "add", task, "--repo", str(self.root), "--json"],
                    input=raw, cwd=self.root, capture_output=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, b"")
                response = json.loads(result.stdout)
                self.assertFalse(response["ok"])
                self.assertEqual(response["errors"][0]["code"], code)
                self.assertEqual(response["errors"][0].get("field"), field)
                self.assertNotIn(b"SENTINEL", result.stdout)
                self.assertEqual(self.install.db_path.read_bytes(), before)


class AdmissionFailureTests(TestCase):
    def test_unknown_admission_effects_preserve_class_without_opening_writer(self):
        from task_governance_tool import review_answer_registration as registration
        for classification, reason in (("save_unknown", "effect_unknown"),
                                       ("registration_unknown", "effect_unknown"),
                                       ("binding_mismatch", "identity"),
                                       ("privacy_rejected", "privacy")):
            context = SimpleNamespace(repo="unused", caller=None)
            with self.subTest(classification=classification), \
                 mock.patch.object(registration, "registration_admission", side_effect=AnswerError(classification, reason=reason)), \
                 mock.patch.object(registration, "connect_initialized") as connect:
                with self.assertRaises(ReviewResultInputError) as caught:
                    registration.register(context, None, None)
                self.assertEqual(caught.exception.code, classification)
                self.assertEqual(caught.exception.message, "collected review answer: " + reason)
                connect.assert_not_called()

    def test_helper_boundary_contains_answer_error_without_dispatch_or_traceback(self):
        from task_governance_tool import review_handoff as helper
        for classification, reason in (("save_unknown", "effect_unknown"),
                                       ("registration_unknown", "effect_unknown"),
                                       ("privacy_rejected", "privacy")):
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(classification=classification), \
                 mock.patch.object(helper.originals, "submission", side_effect=AnswerError(classification, reason=reason)), \
                 mock.patch.object(helper.subprocess, "run") as dispatch, \
                 redirect_stdout(output), redirect_stderr(errors):
                status = helper.main(["submit", "--repo", ".", "--packet", "reviews/packet.json", "--", "reviews/review-1.json"])
            self.assertEqual(status, 1)
            response = json.loads(output.getvalue())
            self.assertEqual(response["code"], classification)
            self.assertIn("collected review answer: " + reason, response["message"])
            self.assertEqual(response["registration_status"], "not_started")
            self.assertEqual(errors.getvalue(), "")
            dispatch.assert_not_called()

    def test_finalizer_parent_report_preserves_answer_failure_class_only(self):
        from task_governance_tool import review_answer_finalization as finalization
        from task_governance_tool.review_handoff_scope import error_code
        root = SimpleNamespace(route="integrated", finalization_store="finish.sqlite",
            packet_path="reviews/packet.json", packet_digest="a" * 64, result_paths=("reviews/review-1.json",))
        record = SimpleNamespace(basis=dict(parent_thread_id="parent", task_id="task", target_generation=1),
            original_locations=(), originals=(), packet_path=root.packet_path, result_paths=root.result_paths)
        finalizer = SimpleNamespace(path=Path("finish.sqlite"), repo=Path.cwd(),
            resolution=SimpleNamespace(paths=SimpleNamespace(review_preparation_store=lambda *_: Path("unused.sqlite"))),
            journal=SimpleNamespace(read_rounds=lambda: (root,)))
        for classification, reason in (("save_unknown", "effect_unknown"),
                                       ("registration_unknown", "effect_unknown"),
                                       ("binding_mismatch", "identity"),
                                       ("privacy_rejected", "privacy")):
            with self.subTest(classification=classification), \
                 mock.patch("task_governance_tool.state_paths.path_lexically_exists", return_value=True), \
                 mock.patch("task_governance_tool.review_preparation_repository.PreparationRepository") as repository, \
                 mock.patch("task_governance_tool.review_handoff_originals.read_original", side_effect=AnswerError(classification, reason=reason)):
                repository.return_value.read.return_value = ((), root)
                self.assertEqual(finalization.prepared_reports(finalizer, record),
                    [dict(path="reviews/review-1.json", status="unavailable", code=classification)])
        self.assertEqual(error_code(RuntimeError("PRIVATE_SENTINEL")), "finalization_unavailable")
        self.assertEqual(error_code(AnswerError("PRIVATE_SENTINEL", reason="bad value")), "format_invalid")


class InstalledUnknownEffectTests(TestCase):
    def test_public_submit_preserves_real_saved_and_registration_unknowns(self):
        from tests.test_review_answer_installed import InstalledAnswerTests
        fixture = InstalledAnswerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.collect("fail-save")
        before = fixture.install.db_path.read_bytes()
        state = fixture.collect("inspect")
        result = fixture.invoke("submit", "--repo", str(fixture.root), "--packet", fixture.packet, "--", *fixture.results)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        response = json.loads(result.stdout)
        self.assertEqual(response["code"], "save_unknown")
        self.assertEqual(response["registration_status"], "not_started")
        self.assertEqual(fixture.collect("inspect"), state)
        self.assertEqual(fixture.install.db_path.read_bytes(), before)

        # Explicit recovery settles the saved exact bytes; no unknown effect is
        # replayed. Reserve a real journal dispatch intent without a core write.
        fixture.collect("finish-save")
        script = """
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from task_governance_tool.review_answer_preparation import locate
from task_governance_tool.review_answer_handoff import service_for
from task_governance_tool.review_handoff_transport import decode_packet
repo=Path(sys.argv[2]); packet_path=sys.argv[3]
raw=(repo/packet_path).read_bytes()
resolution,root=locate(repo,packet_path,raw,decode_packet(raw))
service=service_for(repo,resolution,root,root.collection_bases[0])
service.mark_registration((1,2),'sha256:'+'d'*64)
"""
        reserved = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script,
            str(fixture.install.skill_root / "scripts"), str(fixture.root), fixture.packet],
            cwd=fixture.root, capture_output=True, check=False)
        self.assertEqual(reserved.returncode, 0, reserved.stdout or reserved.stderr)
        state = fixture.collect("inspect")
        result = fixture.invoke("submit", "--repo", str(fixture.root), "--packet", fixture.packet, "--", *fixture.results)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        response = json.loads(result.stdout)
        self.assertEqual(response["errors"][0]["code"], "registration_unknown")
        self.assertIn("effect_unknown", response["errors"][0]["message"])
        self.assertEqual(fixture.collect("inspect"), state)
        self.assertEqual(fixture.install.db_path.read_bytes(), before)
        self.assertEqual(fixture.cli("task", "show", fixture.task_id)["review_evidence"]["counts"]["receipts_current_generation"], 0)


class PersistedCollectionFailureTests(TestCase):
    CASES = (
        ("format", "format_invalid", "verdict", "choice"),
        ("privacy", "privacy_rejected", None, "privacy"),
        ("capacity", "capacity_exceeded", "summary", "bound"),
        ("missing", "collection_unavailable", None, "incomplete"),
        ("conflict", "binding_mismatch", None, "conflict"),
        ("unknown-conflict", "save_unknown", None, "effect_unknown"),
        ("intent-conflict", "save_unknown", None, "effect_unknown"),
    )

    def persisted_failure(self, fixture, mode):
        from tests.test_review_answer_installed import COLLECT
        # Reuse only the installed fixture's preparation locator, public host
        # pages and binding helpers. Collection, journal validation and every
        # later readback remain real; no original reader is replaced.
        script = COLLECT.split("if mode in ('collect-parent',", 1)[0] + r'''
from task_governance_tool.review_answer_runtime import collect_hook_event
normal_answer=answer
def answer(slot):
 if slot!=1 or mode not in ('format','privacy','capacity'):return normal_answer(slot)
 payload=dict(verdict='pass',summary='Rejected answer fixture',findings=[],checks=[],limitations=[])
 if mode=='format':payload['verdict']='not-a-verdict'
 if mode=='privacy':payload['summary']='token=PRIVATE_SENTINEL'
 if mode=='capacity':payload['summary']='x'*1001
 return json.dumps(payload,separators=(',',':')).encode()
for slot in range(1,len(result_paths)+1):bind(slot)
def event(slot):
 return dict(hook_event_name='SubagentStop',session_id=parent,agent_id=child(slot),turn_id=turn(slot),stop_hook_active=False)
if mode in ('format','privacy','capacity'):
 try:collect_hook_event(event(1),service=service,host=host)
 except AnswerError:pass
 else:raise AssertionError('invalid answer accepted')
elif mode!='missing':
 if mode=='unknown-conflict':observe(1,failed=True)
 elif mode=='intent-conflict':
  write=files.write_exclusive
  def interrupted(path,raw):
   write(path,raw)
   raise KeyboardInterrupt()
  try:
   with patch.object(files,'write_exclusive',side_effect=interrupted):observe(1)
  except KeyboardInterrupt:pass
  else:raise AssertionError('publication was not interrupted')
 else:observe(1)
 binding=service.repository.read()[0]['binding']
 changed=host.observe_answer(basis_digest=binding['basis_digest'],slot=1,attempt=0,reviewer_session_id=child(1),turn_id=turn(1),expected_message_id=None)
 changed['source_identity']['message_id']='conflicting-message'
 try:service.observe(1,changed)
 except AnswerError as error:assert error.failure['class']=='binding_mismatch'
 else:raise AssertionError('conflicting source accepted')
for slot in range(2,len(result_paths)+1):collect_hook_event(event(slot),service=service,host=host)
before=service.repository.path.read_bytes()
try:service.readback(1)
except AnswerError as error:failure=error.failure
else:raise AssertionError('failed slot read back successfully')
try:
 with service.admission():raise AssertionError('failed slot admitted for registration')
except AnswerError as error:admission_failure=error.failure
assert service.repository.path.read_bytes()==before
record=service.repository.read()[0]
print(json.dumps(dict(stored_failure=record['attempts'][0]['failure'],readback_failure=failure,
 admission_failure=admission_failure,
 publication=record['attempts'][0]['publication'],observation=record['attempts'][0]['observation'],
 private_retained=b'PRIVATE_SENTINEL' in before,registration=record['registration']),sort_keys=True))
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script,
            str(fixture.install.skill_root / "scripts"), str(fixture.root), fixture.packet, mode],
            cwd=fixture.root, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertNotIn(b"PRIVATE_SENTINEL", result.stdout)
        return json.loads(result.stdout)

    def exercise(self, *, integrated):
        from tests.test_review_answer_installed import InstalledAnswerTests
        from tests.test_review_answer_installed_finalization import InstalledAnswerFinalizationTests
        for mode, classification, field, reason in self.CASES:
            with self.subTest(mode=mode):
                fixture = InstalledAnswerFinalizationTests() if integrated else InstalledAnswerTests()
                try:
                    fixture.setUp()
                    if integrated:
                        fixture.task_id, fixture.handoff = fixture.prepared()
                        fixture.packet = fixture.handoff["packet_path"]
                        fixture.results = tuple(row["result_path"] for row in fixture.handoff["review_requests"])
                    observed = self.persisted_failure(fixture, mode)
                    expected = {"class": classification, "field": field, "reason": reason}
                    self.assertEqual(observed["readback_failure"], expected)
                    self.assertEqual(observed["admission_failure"], expected)
                    self.assertFalse(observed["private_retained"])
                    self.assertEqual(observed["registration"]["state"], "not_started")
                    if mode == "missing":
                        self.assertIsNone(observed["stored_failure"])
                        self.assertEqual(observed["observation"], "missing")
                    elif mode.endswith("-conflict"):
                        self.assertEqual(observed["stored_failure"],
                            {"class": "binding_mismatch", "field": None, "reason": "conflict"})
                        self.assertEqual(observed["publication"]["state"], mode.split("-")[0])
                    else:
                        self.assertEqual(observed["stored_failure"], expected)
                    if mode in ("format", "privacy", "capacity", "missing"):
                        self.assertIsNone(observed["publication"])
                        self.assertFalse((fixture.root / fixture.results[0]).exists())
                    before = fixture.install.db_path.read_bytes()
                    state = fixture.collect("inspect")
                    originals = {path: (fixture.root / path).read_bytes()
                        for path in fixture.results if (fixture.root / path).exists()}
                    command = fixture.handoff["finalization_command" if integrated else "submit_command"]
                    arguments = shlex.split(command.removeprefix("& "))
                    if integrated:
                        arguments.append("--check")
                        head = fixture.git("rev-parse", "HEAD")
                    result = subprocess.run(arguments, cwd=fixture.root, capture_output=True, check=False)
                    self.assertEqual(result.stderr, b"")
                    self.assertNotIn(b"SENTINEL", result.stdout)
                    response = json.loads(result.stdout)
                    if integrated:
                        self.assertEqual(result.returncode, 0, response)
                        self.assertEqual(response["originals"][0],
                            dict(path=fixture.results[0], status="unavailable", code=classification))
                        self.assertEqual(response["originals"][1]["status"], "available")
                        self.assertEqual(response["stages"], dict(registration="not_started",
                            commit="not_started", completion="not_started"))
                        self.assertEqual(fixture.git("rev-parse", "HEAD"), head)
                    else:
                        self.assertNotEqual(result.returncode, 0, response)
                        self.assertEqual((response["code"], response.get("field")), (classification, field))
                        self.assertIn("collected review answer: " + reason, response["message"])
                        self.assertEqual(response["registration_status"], "not_started")
                    self.assertEqual(fixture.collect("inspect"), state)
                    self.assertEqual(fixture.install.db_path.read_bytes(), before)
                    self.assertEqual({path: (fixture.root / path).read_bytes()
                        for path in fixture.results if (fixture.root / path).exists()}, originals)
                    self.assertEqual(fixture.cli("task", "show", fixture.task_id)
                        ["review_evidence"]["counts"]["receipts_current_generation"], 0)
                finally:
                    fixture.doCleanups()

    def test_generated_submit_preserves_persisted_failures_and_unknown_precedence(self):
        self.exercise(integrated=False)

    def test_check_only_finalizer_reports_persisted_failures_and_unknown_precedence(self):
        self.exercise(integrated=True)
