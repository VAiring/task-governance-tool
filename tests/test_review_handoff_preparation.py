"""Start before the Packet-producing command, not with a prepared Packet file."""

from __future__ import annotations

import copy
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.m14_test_support import make_physical_install
from tests.test_review_results import encode, receipt, FINGERPRINT
from task_governance_tool import review_handoff as handoff
from task_governance_tool import review_handoff_preparation as preparation


class PreparationFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name).resolve(), git_managed=True)
        self.root = self.install.project_root
        with (self.root / ".gitignore").open("a", encoding="utf-8") as target:
            target.write("/reviews/\n/-reviews/\n")
        self.cli("setup")
        self.helper = self.install.skill_root / "scripts/review_handoff.py"

    def cli(self, *args):
        result = self.install.run(*args, "--json")
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return json.loads(result.stdout)["data"]

    def task(self, verification=""):
        return self.cli("task", "add", "--title", "Full pipeline 日本語", "--review-tier", "2",
                        "--status", "in_progress", "--verification", verification)["task"]["task_id"]

    def invoke(self, *args, raw=None):
        return subprocess.run([sys.executable, "-I", "-S", str(self.helper), *args],
                              input=raw, capture_output=True, cwd=self.root, check=False)

    def prepare(self, task, action="target", options=None, directory="reviews/g1", raw=None):
        options = options if options is not None else ["--kind", "diff_fingerprint", "--revision", FINGERPRINT]
        completed = self.invoke("prepare", "--repo", str(self.root), "--directory=" + directory,
                                action, task, *options, raw=raw)
        return completed, json.loads(completed.stdout)


class InstalledPreparationTests(PreparationFixture):
    def test_padded_task_id_remains_canonical_through_target_receipt_and_recovery(self):
        task = self.task("Focused checks")
        padded = " \t" + task + "\t "
        completed, result = self.prepare(padded)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(result["source"]["task_id"], task)
        completed, result = self.prepare(padded, "receipt", ["--from-stdin"], raw=encode({
            "version": 1, "task_id": task, "result": "pass", "duration_ms": 1,
            "scope_coverage": "full", "expected_target_generation": 1}))
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(result["source"]["task_id"], task)
        completed, result = self.prepare(padded, "recover", ["--verification-receipt-id",
            result["source"]["verification_receipt_id"]], "reviews/recovery")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        packet = json.loads((self.root / result["handoff"]["packet_path"]).read_bytes())
        self.assertEqual(packet["task"]["task_id"], task)

    def test_generated_posix_arguments_preserve_hyphen_leading_paths(self):
        task = self.task()
        completed, result = self.prepare(task, directory="-reviews/日本語 quoted's path")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        packet_path = result["handoff"]["packet_path"]
        packet = json.loads((self.root / packet_path).read_bytes())
        # Exercise POSIX shell quoting/argv on every host; Windows also executes
        # its own generated command in the separate native PowerShell test.
        args = mock.Mock(directory="-reviews/日本語 quoted's path", reviewers=1)
        with mock.patch.object(preparation, "_shell", side_effect=shlex.join), \
             mock.patch.object(preparation, "__file__", str(self.install.skill_root /
                 "scripts/task_governance_tool/review_handoff_preparation.py")):
            generated = preparation._requests(self.root, args, packet_path)
        payload = packet["result_template"]
        payload["receipts"] = [receipt("quoted-path-reviewer")]
        for command, raw in ((generated["review_requests"][0]["save_command"], encode(payload)),
                             (generated["submit_command"], None)):
            invoked = subprocess.run(shlex.split(command), input=raw, capture_output=True,
                                     cwd=self.root, check=False)
            self.assertEqual(invoked.returncode, 0, invoked.stdout or invoked.stderr)
        self.assertEqual((self.root / generated["review_requests"][0]["result_path"]).read_bytes(), encode(payload))

    def test_installed_target_through_original_registration_and_stale_rejection(self):
        task = self.task()
        completed, result = self.prepare(task)
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertEqual(result["source"]["verification_route"], "not_required")
        context = result["handoff"]
        packet = json.loads((self.root / context["packet_path"]).read_bytes())
        self.assertEqual(packet["task"]["task_id"], task)
        self.assertEqual(len(context["review_requests"]), 2)
        original_paths = []
        originals = []
        for index, request in enumerate(context["review_requests"]):
            self.assertIn(str(self.root / context["packet_path"]), request["request"])
            self.assertIn(request["save_command"], request["request"])
            payload = copy.deepcopy(packet["result_template"])
            payload["receipts"] = [receipt("reviewer-" + str(index), findings=[{
                "severity": "low", "summary": "example.py:1 日本語 🚀 retained"}])]
            raw = b"\n " + encode(payload) + b"\n"
            originals.append(raw)
            original_paths.append(request["result_path"])
            saved = self.invoke("save", "--repo", str(self.root), "--packet", context["packet_path"],
                                "--output", request["result_path"], raw=raw)
            self.assertEqual(saved.returncode, 0, saved.stdout)
            self.assertEqual(json.loads(saved.stdout)["finding_count"], 1)
        args = ["submit", "--repo", str(self.root), "--packet", context["packet_path"], *original_paths]
        submitted = self.invoke(*args)
        self.assertEqual(submitted.returncode, 0, submitted.stdout)
        self.assertEqual(len(json.loads(submitted.stdout)["data"]["receipts"]), 2)
        self.assertNotEqual(self.invoke(*args).returncode, 0)
        self.assertEqual(self.cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 2)
        self.cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        self.assertNotEqual(self.invoke(*args).returncode, 0)
        for path, raw in zip(original_paths, originals):
            self.assertEqual((self.root / path).read_bytes(), raw)
        self.assertEqual(sorted(path.name for path in (self.root / "reviews/g1").iterdir()),
                         ["packet.json", "review-1.json", "review-2.json"])

    def test_receipt_required_then_manual_and_structured_receipt_and_recovery(self):
        for from_stdin in (False, True):
            with self.subTest(from_stdin=from_stdin):
                task = self.task("Focused checks")
                directory = "reviews/stdin" if from_stdin else "reviews/manual"
                completed, result = self.prepare(task, directory=directory)
                self.assertEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(result["source"]["verification_route"], "receipt_required")
                self.assertEqual(result["handoff"]["status"], "not_applicable")
                self.assertFalse((self.root / directory).exists())
                generation = result["source"]["review_target"]["generation"]
                raw = encode({"version": 1, "task_id": task, "result": "pass", "duration_ms": 10,
                              "scope_coverage": "full", "expected_target_generation": generation}) if from_stdin else None
                options = ["--from-stdin"] if from_stdin else ["--result", "pass", "--duration-ms", "10",
                    "--scope-coverage", "full", "--expected-target-generation", str(generation)]
                completed, result = self.prepare(task, "receipt", options, directory, raw)
                self.assertEqual(completed.returncode, 0, completed.stdout)
                receipt_id = result["source"]["verification_receipt_id"]
                completed, recovered = self.prepare(task, "recover", ["--verification-receipt-id", receipt_id], directory + "-recovery")
                self.assertEqual(completed.returncode, 0, completed.stdout)
                # CLI serializers can order keys differently; the complete Packet
                # object (unlike original reviewer bytes) is the transport contract.
                self.assertEqual(json.loads((self.root / recovered["handoff"]["packet_path"]).read_bytes()),
                                 json.loads((self.root / result["handoff"]["packet_path"]).read_bytes()))

    def test_bound_recovery_does_not_write_another_target_and_conflicting_binding_fails(self):
        task = self.task()
        _, result = self.prepare(task)
        binding = result["source"]["preparation_binding"]
        completed, recovered = self.prepare(task, "recover", ["--expected-binding", binding], "reviews/recovery")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(json.loads((self.root / recovered["handoff"]["packet_path"]).read_bytes())["review_target"]["generation"], 1)
        completed, failed = self.prepare(task, "recover", ["--expected-binding", binding,
            "--verification-receipt-id", "invalid"], "reviews/invalid")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((self.root / "reviews/invalid").exists())

    def test_actual_source_failure_and_blocked_receipt_do_not_create_handoff(self):
        task = self.task("Focused checks")
        completed, failed = self.prepare(task, options=["--kind", "git_snapshot", "--revision", "forbidden"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(failed["operation_status"], "failed")
        self.assertNotIn("review_requests", failed["handoff"])
        self.assertFalse((self.root / "reviews").exists())
        self.prepare(task)
        completed, blocked = self.prepare(task, "receipt", ["--result", "fail", "--duration-ms", "10",
            "--scope-coverage", "full", "--expected-target-generation", "1"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(blocked["operation_status"], "succeeded", blocked)
        self.assertEqual(blocked["handoff"]["status"], "blocked")
        self.assertTrue(blocked["source"]["verification_receipt_id"])
        self.assertFalse((self.root / "reviews").exists())


class PreparationFailureTests(PreparationFixture):
    # Reuse the real source response for injected transport/file failure checks,
    # not a handwritten Packet. No fixture file precedes the source invocation.
    def setUp(self):
        super().setUp()
        self.task_id = self.task()
        completed = self.install.run("review", "target", "set", self.task_id, "--kind", "diff_fingerprint",
                                     "--revision", FINGERPRINT, "--json")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.response = json.loads(completed.stdout)

    def run_injected(self, response=None, code=0, directory="reviews/g1", capture_error=None):
        def captured(command, raw, started):
            started()
            if capture_error:
                raise capture_error
            return code, encode(self.response) if response is None else response
        output = io.StringIO()
        with mock.patch.object(preparation, "_capture", side_effect=captured) as capture, redirect_stdout(output):
            exit_code = handoff.main(["prepare", "--repo", str(self.root), "--directory", directory,
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        return exit_code, json.loads(output.getvalue()), capture

    def test_malformed_or_incomplete_capture_has_unknown_outcome_and_no_files(self):
        for raw in (b"", b"{", b"{}", b"\xef\xbb\xbf{}", b'{"ok":true,"ok":false}', b"null"):
            with self.subTest(raw=raw):
                code, result, capture = self.run_injected(raw)
                self.assertEqual(code, 1)
                self.assertEqual(result["operation_status"], "unknown")
                capture.assert_called_once()
                self.assertFalse((self.root / "reviews").exists())
        code, result, capture = self.run_injected(capture_error=KeyboardInterrupt())
        self.assertEqual(result["operation_status"], "unknown")
        capture.assert_called_once()

    def test_preparation_failure_preserves_committed_outcome_binding_and_warnings(self):
        changed = copy.deepcopy(self.response)
        changed["warnings"] = [{"code": "viewer_update_failed", "message": "Viewer update failed"}]
        changed["data"]["review_preparation"].update(status="failed", packet=None,
            errors=[{"code": "internal_error", "message": "could not prepare review context"}])
        code, result, capture = self.run_injected(encode(changed))
        self.assertEqual(code, 1)
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertEqual(result["source"]["preparation_binding"], changed["data"]["review_preparation"]["binding"])
        self.assertEqual(result["warnings"], changed["warnings"])
        capture.assert_called_once()
        self.assertFalse((self.root / "reviews").exists())

    def test_packet_missing_or_mismatched_never_dispatches_reviewers(self):
        for change in (
            lambda p: p.pop("required_output"),
            lambda p: p["task"].pop("verification"),
            lambda p: p["contract"].pop("scope"),
            lambda p: p["result_template"]["review_target"].update(generation=9),
            lambda p: p["review_target"].update(base_revision="incorrect"),
            lambda p: p.update(review_focus=[]),
        ):
            changed = copy.deepcopy(self.response)
            change(changed["data"]["review_preparation"]["packet"])
            code, result, capture = self.run_injected(encode(changed))
            self.assertEqual(code, 1)
            self.assertEqual(result["operation_status"], "succeeded")
            self.assertNotIn("review_requests", result["handoff"])
            self.assertFalse((self.root / "reviews").exists())

    def test_stored_constraints_compatibility_does_not_relax_other_privacy(self):
        cases = (("constraints", "dispatch_authorization=07"),
                 ("constraints", '{"dispatch_authorization":"7"}'),
                 ("constraints", "dispatch_authorization=7 token=opaque-value"),
                 ("scope", "dispatch_authorization=7"),
                 ("acceptance", '{"dispatch_authorization":7}'))
        for field, text in cases:
            with self.subTest(field=field, text=text):
                changed = copy.deepcopy(self.response)
                changed["data"]["review_preparation"]["packet"]["contract"][field] = text
                _, result, capture = self.run_injected(encode(changed))
                self.assertEqual(result["operation_status"], "succeeded")
                self.assertEqual(result["errors"][0]["code"], "privacy_rejected")
                self.assertNotIn("review_requests", result["handoff"])
                capture.assert_called_once()
                self.assertFalse((self.root / "reviews").exists())
        for text in ("dispatch_authorization=7", '{"dispatch_authorization":7}'):
            rejected = self.install.run("task", "add", "--title", "New caller input",
                "--contract-scope", "Bounded scope", "--contract-acceptance", "Focused checks",
                "--contract-constraints", text, "--json")
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(json.loads(rejected.stdout)["errors"][0]["code"], "privacy_rejected")

    def test_exit_mismatch_and_source_start_failure_never_claim_success(self):
        _, result, capture = self.run_injected(code=1)
        self.assertEqual(result["operation_status"], "unknown")
        capture.assert_called_once()
        output = io.StringIO()
        with mock.patch.object(preparation, "_capture", side_effect=PermissionError("private details")), redirect_stdout(output):
            code = handoff.main(["prepare", "--repo", str(self.root), "--directory", "reviews/g1",
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["operation_status"], "not_started")
        self.assertFalse((self.root / "reviews").exists())

    def test_preflight_bad_or_existing_directories_never_invoke_source(self):
        (self.root / "reviews").mkdir()
        for directory in ("../escape", ".taskgov/copy", ".agents/copy", "reviews", "unignored", "reviews/NUL", "reviews/a:ads"):
            code, result, capture = self.run_injected(directory=directory)
            self.assertEqual(code, 1, directory)
            self.assertEqual(result["operation_status"], "not_started")
            capture.assert_not_called()
        stat = Path.lstat
        def reparse(path):
            if path == self.root / "reviews":
                value = mock.Mock(st_mode=0o040700, st_file_attributes=1024)
                return value
            return stat(path)
        with mock.patch.object(Path, "lstat", reparse):
            _, result, capture = self.run_injected()
        self.assertEqual(result["operation_status"], "not_started")
        capture.assert_not_called()

    def test_write_and_readback_failures_keep_source_success_and_residue(self):
        for index, method in enumerate(("_write_new", "_read")):
            with mock.patch.object(handoff, method, side_effect=PermissionError("private failure detail")):
                _, result, capture = self.run_injected(directory=f"reviews/fail-{index}")
            self.assertEqual(result["operation_status"], "succeeded")
            self.assertNotIn("review_requests", result["handoff"])
            self.assertNotIn("private failure detail", json.dumps(result))
            self.assertTrue((self.root / f"reviews/fail-{index}").is_dir())
            self.assertEqual((self.root / f"reviews/fail-{index}/packet.json").exists(), method == "_read")
            capture.assert_called_once()

    def test_partial_packet_and_raced_directory_are_retained_without_requests(self):
        write = os.write
        count = []
        def partial(descriptor, raw):
            count.append(True)
            if len(count) == 1:
                return write(descriptor, raw[:8])
            raise OSError("private error")
        with mock.patch.object(handoff.os, "write", side_effect=partial):
            _, result, capture = self.run_injected(directory="reviews/partial")
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertNotIn("review_requests", result["handoff"])
        self.assertEqual((self.root / "reviews/partial/packet.json").stat().st_size, 8)
        capture.assert_called_once()
        def raced(command, raw, started):
            started()
            (self.root / "reviews/raced").mkdir()
            return 0, encode(self.response)
        output = io.StringIO()
        with mock.patch.object(preparation, "_capture", side_effect=raced) as capture, redirect_stdout(output):
            handoff.main(["prepare", "--repo", str(self.root), "--directory", "reviews/raced",
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        self.assertEqual(json.loads(output.getvalue())["operation_status"], "succeeded")
        self.assertFalse((self.root / "reviews/raced/packet.json").exists())
        capture.assert_called_once()

    def test_changed_packet_readback_and_preflight_ignore_failure_do_not_dispatch(self):
        read = handoff._read
        def changed(path, limit):
            return read(path, limit) + b" "
        with mock.patch.object(handoff, "_read", side_effect=changed):
            _, result, capture = self.run_injected()
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertNotIn("review_requests", result["handoff"])
        self.assertTrue((self.root / "reviews/g1/packet.json").exists())
        with mock.patch.object(handoff, "_ignored", side_effect=handoff.HandoffError("handoff_ignore_required")):
            _, result, capture = self.run_injected(directory="reviews/denied")
        self.assertEqual(result["operation_status"], "not_started")
        capture.assert_not_called()

    def test_lost_acknowledgement_never_replays_the_source(self):
        def captured(command, raw, started):
            started()
            return 0, encode(self.response)
        with mock.patch.object(preparation, "_capture", side_effect=captured) as capture, \
             mock.patch.object(handoff, "_emit", return_value=False):
            result = handoff.main(["prepare", "--repo", str(self.root), "--directory", "reviews/g1",
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        self.assertEqual(result, 1)
        capture.assert_called_once()
        self.assertTrue((self.root / "reviews/g1/packet.json").is_file())

    def test_capture_drains_excess_without_persisting_or_replaying(self):
        process = mock.Mock(stdout=io.BytesIO(b"x" * (preparation.RESPONSE_LIMIT + 1)), stdin=None)
        process.wait.return_value = 0
        with mock.patch.object(preparation.subprocess, "Popen", return_value=process) as launch:
            code, raw = preparation._capture(["fixed-entry"], None, lambda: None)
        self.assertEqual(code, 0)
        self.assertIsNone(raw)
        launch.assert_called_once()
        process.wait.assert_called_once()


class LegacyPacketPreparationTests(unittest.TestCase):
    def test_migrated_public_packet_preserves_both_stored_constraints_forms(self):
        from tests.test_m22_evidence_ledger_storage import initialize_v17_fixture, seed_v17_contract_constraints
        from task_governance_tool.storage import connect, apply_migrations
        from task_governance_tool.review_packet import prepare_review_packet

        for constraints in ("dispatch_authorization=7", '{"dispatch_authorization":7}'):
            with self.subTest(constraints=constraints), tempfile.TemporaryDirectory() as temporary:
                target, task_id = initialize_v17_fixture(Path(temporary))
                with closing(connect(target.db_path)) as connection:
                    seed_v17_contract_constraints(connection, project_id=target.project.project_id,
                                                 task_id=task_id, constraints=constraints)
                    apply_migrations(connection)
                # Real migrated storage and public Packet producer, no invented
                # Packet/constraints normalization in the transport regression.
                packet = prepare_review_packet(target, task_id)
                transported = json.loads(preparation._packet(packet, task_id))
                self.assertEqual(transported, packet)
                self.assertEqual(transported["contract"]["constraints"], constraints)
