"""Reviewer-selected checks use exact restored files, never rebuilt bodies."""

import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import test_review_handoff_preparation as fixtures
from task_governance_tool import review_workspace as workspace
from task_governance_tool import review_handoff as handoff


COUNTER = '''def summarize_counts(values):
    if not isinstance(values, list) or any(type(x) is not int or x < 0 for x in values):
        raise ValueError("nonnegative integers required")
    return {"count": len(values), "total": sum(values)}
'''
SIX_TESTS = '''import unittest
from counter import summarize_counts
class CounterTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_counts([]), {"count": 0, "total": 0})
    def test_values(self):
        self.assertEqual(summarize_counts([0, 2, 7]), {"count": 3, "total": 9})
    def test_large(self):
        self.assertEqual(summarize_counts([10**40, 1])["total"], 10**40 + 1)
    def test_unchanged(self):
        values = [3, 1, 3]
        summarize_counts(values)
        self.assertEqual(values, [3, 1, 3])
    def test_bad_values(self):
        for value in [-1, True, False, 1.0, "1", None]:
            with self.assertRaises(ValueError): summarize_counts([value])
    def test_not_list(self):
        for value in [None, (), "", 1]:
            with self.assertRaises(ValueError): summarize_counts(value)
'''


class ReviewerWorkspaceTests(fixtures.PreparationFixture):
    git = fixtures.ReviewerMaterialTests.git
    committed_fixture = fixtures.ReviewerMaterialTests.committed_fixture
    material_read = fixtures.ReviewerMaterialTests.material_read

    def setUp(self):
        super().setUp()
        # Use an owned physical temporary parent, also inherited by generated
        # commands. Tests must not depend on the host temp spelling, contents
        # or ACL; alias selection and drift have their own cases below.
        self.workspace_temp = self.root.parent / "review-temporary"
        self.workspace_temp.mkdir()
        temporary_environment = mock.patch.dict(os.environ, {"TMPDIR": str(self.workspace_temp)})
        temporary_environment.start()
        self.addCleanup(temporary_environment.stop)
        # Pure filesystem tests call the source module in-process; its public
        # live read must still use this fixture's supported physical install.
        capture = fixtures.preparation._capture
        def installed_capture(command, *values):
            command = [str(self.install.skill_root / "scripts/taskgov.py")
                       if item == str(Path(workspace.__file__).parent.parent / "taskgov.py") else item
                       for item in command]
            return capture(command, *values)
        patch = mock.patch.object(fixtures.preparation, "_capture", side_effect=installed_capture)
        patch.start()
        self.addCleanup(patch.stop)

    def prepared(self, *, commit=False):
        self.committed_fixture()
        (self.root / "counter.py").write_text("raise RuntimeError('old')\n", encoding="utf-8")
        (self.root / "test_counter.py").write_text(SIX_TESTS, encoding="utf-8")
        (self.root / "日本語's test.txt").write_text("固定資料\n", encoding="utf-8")
        self.git("-c", "core.autocrlf=false", "add", "--", "counter.py", "test_counter.py", "日本語's test.txt")
        self.git("commit", "--quiet", "-m", "Six unchanged checks")
        (self.root / "counter.py").write_text(COUNTER, encoding="utf-8")
        self.git("-c", "core.autocrlf=false", "add", "--", "counter.py")
        options = ["--kind", "git_snapshot"]
        if commit:
            self.git("commit", "--quiet", "-m", "Fixed counter")
            options = ["--kind", "git_commit", "--revision", self.git("rev-parse", "HEAD").decode().strip()]
        _, prepared = self.prepare(self.task(), options=options, directory="reviews/日本語's review")
        packet = prepared["handoff"]["packet_path"]
        raw = (self.root / packet).read_bytes()
        import hashlib
        args = SimpleNamespace(workspace_operation="prepare", packet=packet,
            packet_sha256=hashlib.sha256(raw).hexdigest(), workspace_id=uuid.uuid4().hex,
            temp_root_sha256=workspace._path_digest(workspace._temporary_parent(self.root)))
        # Each fixture owns this preallocated handle, even when output is lost.
        self.addCleanup(self.cleanup, args)
        return args

    def cleanup(self, args):
        args = SimpleNamespace(**{**vars(args), "workspace_operation": "cleanup"})
        return workspace.operate(self.root, args)

    def test_exact_snapshot_six_checks_exclude_ambient_and_cleanup_test_outputs(self):
        args = self.prepared()
        (self.root / "counter.py").write_text("raise RuntimeError('ambient')", encoding="utf-8")
        (self.root / "test_counter.py").write_text("raise RuntimeError('ambient test')", encoding="utf-8")
        (self.root / "untracked.py").write_text("not fixed", encoding="utf-8")
        result = workspace.operate(self.root, args)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verification_status"], "not_run")
        target = Path(result["working_directory"])
        self.assertEqual((target / "counter.py").read_text(encoding="utf-8"), COUNTER)
        self.assertFalse((target / "untracked.py").exists())
        self.assertFalse((target / ".git").exists())
        self.assertEqual((target / "日本語's test.txt").read_text(encoding="utf-8"), "固定資料\n")
        checked = subprocess.run([sys.executable, "-B", "-m", "unittest", "-v"],
                                 cwd=target, capture_output=True, timeout=20)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn(b"Ran 6 tests", checked.stderr)
        (target / "check-output.txt").write_text("temporary", encoding="utf-8")
        self.assertEqual(self.cleanup(args)["status"], "absent")
        self.assertFalse(target.parent.exists())
        self.assertEqual(self.cleanup(args)["status"], "absent")
        self.assertTrue((self.root / "untracked.py").exists())

    def test_commit_material_ignores_later_head_and_separate_reviewers(self):
        args = self.prepared(commit=True)
        (self.root / "counter.py").write_text("raise RuntimeError('later')", encoding="utf-8")
        self.git("add", "--", "counter.py")
        self.git("commit", "--quiet", "-m", "Later target")
        first = workspace.operate(self.root, args)
        second_args = SimpleNamespace(**{**vars(args), "workspace_id": uuid.uuid4().hex})
        self.addCleanup(self.cleanup, second_args)
        second = workspace.operate(self.root, second_args)
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(first["working_directory"], second["working_directory"])
        self.assertEqual(first["material"], second["material"])
        self.assertEqual(Path(second["working_directory"], "counter.py").read_text(encoding="utf-8"), COUNTER)

    def test_generated_quoted_commands_and_read_is_write_free(self):
        args = self.prepared()
        before = fixtures.file_snapshot(self.root)
        temporary_before = fixtures.file_snapshot(self.workspace_temp)
        read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet)
        self.assertEqual(read.returncode, 0, read.stdout)
        normal = json.loads(read.stdout)
        material = normal["review_material"]
        notice = material["verification_workspace"]
        self.assertEqual(set(notice), {"status", "instructions"})
        self.assertEqual(notice["status"], "optional")
        self.assertIn("review_material.recovery_command", notice["instructions"])
        self.assertNotIn("workspace-id", read.stdout.decode())
        details = self.material_read(material["recovery_command"])
        self.assertEqual(details.returncode, 0, details.stdout or details.stderr)
        detailed = json.loads(details.stdout)
        guidance = detailed["review_material"]["verification_workspace"]
        self.assertEqual(set(guidance), {"status", "prepare_command", "cleanup_command", "instructions"})
        self.assertEqual(guidance["status"], "available")
        for key in ("task", "contract", "review_target", "result_template", "result_instructions"):
            self.assertEqual(normal[key], detailed[key])
        self.assertEqual(normal["verification_evidence"]["source_kind"],
                         detailed["verification_evidence"]["source_kind"])
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), temporary_before)
        self.assertEqual(fixtures.file_snapshot(self.root), before)
        result = self.material_read(guidance["prepare_command"])
        try:
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "ready")
        finally:
            cleaned = self.material_read(guidance["cleanup_command"])
        self.assertEqual(cleaned.returncode, 0, cleaned.stdout or cleaned.stderr)
        self.assertEqual(fixtures.file_snapshot(self.root), before)
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), temporary_before)

    def test_normal_notice_does_not_probe_temp_or_allocate_handle(self):
        with mock.patch.object(workspace, "_temporary_parent", side_effect=AssertionError("unused temp probe")), \
             mock.patch.object(workspace.uuid, "uuid4", side_effect=AssertionError("unused handle")):
            notice = workspace.workspace_guidance(self.root, "reviews/packet.json", b"unused")
        self.assertEqual(set(notice), {"status", "instructions"})
        self.assertEqual(notice["status"], "optional")

    def test_details_keep_cleanup_handle_after_unobserved_prepare_output(self):
        args = self.prepared()
        before = fixtures.file_snapshot(self.workspace_temp)
        normal = self.invoke("read", "--repo", str(self.root), "--packet", args.packet)
        command = json.loads(normal.stdout)["review_material"]["recovery_command"]
        details = self.material_read(command)
        self.assertEqual(details.returncode, 0, details.stdout)
        guidance = json.loads(details.stdout)["review_material"]["verification_workspace"]
        cleanup_command = guidance["cleanup_command"]
        try:
            # Deliberately discard prepare output: cleanup uses only the
            # command delivered before preparation, not a returned handle.
            self.material_read(guidance["prepare_command"])
            self.assertEqual(len(list(self.workspace_temp.glob("taskgov-review-*/target"))), 1)
        finally:
            cleaned = self.material_read(cleanup_command)
        self.assertEqual(cleaned.returncode, 0, cleaned.stdout or cleaned.stderr)
        self.assertEqual(json.loads(cleaned.stdout)["status"], "absent")
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), before)

    def test_opaque_material_omits_workspace_in_both_views(self):
        task_id = self.task()
        for kind, revision in (("diff_fingerprint", fixtures.FINGERPRINT),
                               ("external_revision", "fixture:opaque-review")):
            result, prepared = self.prepare(task_id, options=["--kind", kind, "--revision", revision],
                                            directory="reviews/" + kind)
            self.assertEqual(result.returncode, 0, result.stdout)
            packet = prepared["handoff"]["packet_path"]
            for options in ([], ["--material-details"]):
                read = self.invoke("read", "--repo", str(self.root), "--packet", packet, *options)
                self.assertEqual(read.returncode, 0, read.stdout)
                material = json.loads(read.stdout)["review_material"]
                self.assertEqual(material["status"], "requires_supplied_material")
                self.assertNotIn("verification_workspace", material)
                self.assertNotIn("workspace-id", read.stdout.decode())

    def test_record_scope_rejects_operations_and_omits_escape_command(self):
        args = self.prepared()
        before = fixtures.file_snapshot(self.root)
        for options in ([], ["--material-details"]):
            read = self.invoke("--records-only", "read", "--repo", str(self.root), "--packet", args.packet, *options)
            self.assertEqual(read.returncode, 0, read.stdout)
            self.assertNotIn("verification_workspace", json.loads(read.stdout)["review_material"])
            self.assertNotIn("workspace-id", read.stdout.decode())
        for action in ("prepare", "cleanup"):
            result = self.invoke("--records-only", "workspace", action, "--repo", str(self.root),
                "--packet", args.packet, "--packet-sha256", args.packet_sha256, "--workspace-id", args.workspace_id,
                "--temp-root-sha256", args.temp_root_sha256)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["code"], "record_operation_not_allowed")
        self.assertEqual(fixtures.file_snapshot(self.root), before)

    def test_unavailable_temp_keeps_material_read_available_without_writes(self):
        args = self.prepared()
        before = fixtures.file_snapshot(self.root)
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.root)}):
            read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet)
            self.assertEqual(read.returncode, 0, read.stdout)
            notice = json.loads(read.stdout)["review_material"]
            self.assertEqual(notice["verification_workspace"]["status"], "optional")
            read = self.material_read(notice["recovery_command"])
        self.assertEqual(read.returncode, 0, read.stdout)
        material = json.loads(read.stdout)["review_material"]
        self.assertEqual(material["status"], "git_objects_verified")
        self.assertIn("collect_command", material)
        self.assertEqual(material["verification_workspace"]["status"], "unavailable")
        self.assertNotIn("prepare_command", material["verification_workspace"])
        self.assertEqual(fixtures.file_snapshot(self.root), before)

    def test_host_temp_alias_uses_physical_destination_and_retarget_is_rejected(self):
        args = self.prepared()
        alias = self.root.parent / "host-temp-alias"
        try:
            alias.symlink_to(self.root.parent, target_is_directory=True)
        except OSError:
            self.skipTest("host does not allow creating symlinks")
        self.addCleanup(alias.unlink)
        before = fixtures.file_snapshot(self.workspace_temp)
        with mock.patch.dict(os.environ, {"TMPDIR": str(alias / self.workspace_temp.name)}):
            read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet, "--material-details")
            result = workspace.operate(self.root, args)
            self.assertEqual(read.returncode, 0, read.stdout)
            self.assertEqual(json.loads(read.stdout)["review_material"]["verification_workspace"]["status"], "available")
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["working_directory"]).parent.parent, self.workspace_temp)
            changed_parent = self.root.parent / "other-temp"
            (changed_parent / self.workspace_temp.name).mkdir(parents=True)
            alias.unlink()
            alias.symlink_to(changed_parent, target_is_directory=True)
            rejected = self.cleanup(args)
            self.assertEqual(rejected["code"], "review_workspace_temp_changed")
            self.assertEqual(rejected["cleanup_status"], "retained")
            self.assertTrue(Path(result["working_directory"]).is_dir())
        self.assertEqual(self.cleanup(args)["status"], "absent")
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), before)

    def test_alias_resolution_does_not_admit_git_destination_or_change_bound_root(self):
        args = self.prepared()
        # On hosts without symlink-creation permission, exercise the selector
        # seam while keeping real physical admission and restoration checks.
        alias = self.root.parent / "os-alias"
        resolve = Path.resolve
        destination = self.workspace_temp
        def selected(path, **kwargs):
            return destination if path == alias else resolve(path, **kwargs)
        with mock.patch.dict(os.environ, {"TMPDIR": str(alias)}), mock.patch.object(Path, "resolve", selected):
            result = workspace.operate(self.root, args)
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["working_directory"]).parent.parent, self.workspace_temp)
            destination = self.root
            rejected = self.cleanup(args)
            self.assertEqual(rejected["code"], "review_workspace_temp_unavailable")
            self.assertEqual(rejected["cleanup_status"], "retained")
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_changed_packet_and_index_do_not_create_workspace(self):
        args = self.prepared()
        path = self.root / args.packet
        raw = path.read_bytes()
        path.write_bytes(raw + b" ")
        self.assertEqual(workspace.operate(self.root, args)["code"], "review_workspace_packet_changed")
        path.write_bytes(raw)
        (self.root / "counter.py").write_text("new = 1", encoding="utf-8")
        self.git("add", "--", "counter.py")
        result = workspace.operate(self.root, args)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["cleanup_status"], "not_started")
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_post_restore_contract_drift_removes_owned_copy(self):
        args = self.prepared()
        from task_governance_tool import review_handoff_preparation as preparation
        real = preparation.validate_live_packet
        calls = 0
        def checked(*values, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise handoff.HandoffError("review_packet_stale")
            return real(*values, **kwargs)
        with mock.patch.object(preparation, "validate_live_packet", side_effect=checked):
            result = workspace.operate(self.root, args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "review_packet_stale")
        self.assertEqual(result["cleanup_status"], "absent")

    def test_material_failure_cleans_partial_copy_without_pass(self):
        args = self.prepared()
        from task_governance_tool import verification_runner_git as material
        def incomplete(repo, selected, destination):
            (destination / "partial.txt").write_bytes(b"partial")
            raise material.VerificationRunnerGitError("materialization_failed")
        with mock.patch.object(material, "materialize_runner_target", side_effect=incomplete):
            result = workspace.operate(self.root, args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["cleanup_status"], "absent")
        self.assertNotIn("working_directory", result)

    def test_created_root_with_unknown_identity_is_reported_retained(self):
        args = self.prepared()
        root = self.workspace_temp / ("taskgov-review-" + args.workspace_id)
        inspect = workspace.inspect_physical_directory
        def fail_new_root(path, **kwargs):
            if path == root:
                raise workspace.StatePathError()
            return inspect(path, **kwargs)
        with mock.patch.object(workspace, "inspect_physical_directory", side_effect=fail_new_root):
            result = workspace.operate(self.root, args)
        try:
            self.assertFalse(result["ok"])
            self.assertEqual(result["cleanup_status"], "retained")
            self.assertTrue(root.is_dir())
            self.assertEqual(list(root.iterdir()), [])
        finally:
            # This test itself observed the exact new empty directory. The
            # product must not infer ownership from a failed identity check.
            root.rmdir()

    def test_missing_material_and_incomplete_public_reply_never_create(self):
        args = self.prepared()
        from task_governance_tool import verification_runner_git as material
        with mock.patch.object(material, "preflight_runner_material",
                               side_effect=material.VerificationRunnerGitError("target_stale")):
            rejected = workspace.operate(self.root, args)
        self.assertEqual(rejected["code"], "target_stale")
        self.assertEqual(rejected["cleanup_status"], "not_started")
        with mock.patch.object(fixtures.preparation, "_capture", return_value=(0, b"{")):
            rejected = workspace.operate(self.root, args)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["cleanup_status"], "not_started")

    def test_cleanup_uses_retained_binding_after_live_generation_advances(self):
        args = self.prepared()
        ready = workspace.operate(self.root, args)
        self.assertTrue(ready["ok"], ready)
        self.cli("review", "target", "set", ready["task_id"], "--kind", "git_snapshot")
        self.assertEqual(self.cleanup(args)["status"], "absent")
        self.assertEqual(workspace.operate(self.root, args)["code"], "review_packet_stale")

    def test_temp_environment_drift_does_not_claim_old_workspace_absent(self):
        args = self.prepared()
        ready = workspace.operate(self.root, args)
        self.assertTrue(ready["ok"], ready)
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.root.parent)}):
            rejected = self.cleanup(args)
            self.assertEqual(rejected["code"], "review_workspace_temp_changed")
            self.assertEqual(rejected["cleanup_status"], "retained")
        self.assertTrue(Path(ready["working_directory"]).exists())
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_incomplete_packet_and_unsafe_destination_fail_before_writes(self):
        args = self.prepared()
        path = self.root / args.packet
        raw = path.read_bytes()
        import hashlib
        broken = SimpleNamespace(**vars(args))
        path.write_bytes(b"{}")
        broken.packet_sha256 = hashlib.sha256(b"{}").hexdigest()
        self.assertFalse(workspace.operate(self.root, broken)["ok"])
        path.write_bytes(raw)
        for identity in ("../elsewhere", "A" * 32):
            bad = SimpleNamespace(**{**vars(args), "workspace_id": identity})
            rejected = workspace.operate(self.root, bad)
            self.assertEqual(rejected["code"], "review_workspace_invalid_id")
            self.assertIsNone(rejected["workspace_id"])
            self.assertNotIn(identity, json.dumps(rejected))
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.root)}):
            self.assertEqual(workspace.operate(self.root, args)["code"], "review_workspace_temp_unavailable")

    def test_public_rejection_never_echoes_invalid_identity_even_before_location_check(self):
        args = self.prepared()
        invalid = "invalid-workspace-value-for-privacy-regression"
        for packet in (args.packet, "reviews/missing.json"):
            result = self.invoke("workspace", "prepare", "--repo", str(self.root),
                "--packet", packet, "--packet-sha256", args.packet_sha256,
                "--workspace-id", invalid, "--temp-root-sha256", args.temp_root_sha256)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(json.loads(result.stdout)["workspace_id"])
            self.assertNotIn(invalid.encode(), result.stdout + result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX executable mode")
    def test_existing_executable_check_runs_without_reconstructed_script(self):
        args = self.prepared()
        (self.root / "verify").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.git("add", "--", "verify")
        self.git("update-index", "--chmod=+x", "verify")
        # This is a new exact target, with its own complete Packet.
        task = json.loads((self.root / args.packet).read_bytes())["task"]["task_id"]
        _, prepared = self.prepare(task, options=["--kind", "git_snapshot"], directory="reviews/executable")
        args.packet = prepared["handoff"]["packet_path"]
        import hashlib
        args.packet_sha256 = hashlib.sha256((self.root / args.packet).read_bytes()).hexdigest()
        ready = workspace.operate(self.root, args)
        self.assertTrue(ready["ok"], ready)
        checked = subprocess.run(["./verify"], cwd=ready["working_directory"], capture_output=True, timeout=10)
        self.assertEqual(checked.returncode, 0)

    def test_failure_and_timeout_are_reviewer_results_cleanup_after_process_end(self):
        args = self.prepared()
        result = workspace.operate(self.root, args)
        self.assertTrue(result["ok"], result)
        target = Path(result["working_directory"])
        failed = subprocess.run([sys.executable, "-B", "-c", "raise SystemExit(7)"], cwd=target,
                                capture_output=True, timeout=10)
        self.assertEqual(failed.returncode, 7)
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run([sys.executable, "-B", "-c", "import time; time.sleep(5)"],
                           cwd=target, capture_output=True, timeout=0.1)
        self.assertEqual(result["verification_status"], "not_run")
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_existing_workspace_never_reused_and_owner_mismatch_retained(self):
        args = self.prepared()
        result = workspace.operate(self.root, args)
        self.assertTrue(result["ok"], result)
        self.assertEqual(workspace.operate(self.root, args)["code"], "review_workspace_exists")
        marker = Path(result["working_directory"]).parent / workspace.MARKER
        raw = marker.read_bytes()
        marker.write_bytes(b"{}")
        rejected = self.cleanup(args)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["cleanup_status"], "retained")
        marker.write_bytes(raw)
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_cleanup_limits_leave_ownership_and_recover_after_condition_changes(self):
        args = self.prepared()
        result = workspace.operate(self.root, args)
        self.assertTrue(result["ok"], result)
        for name, value in (("CLEANUP_ENTRIES", 1), ("CLEANUP_SECONDS", -1)):
            with self.subTest(name=name), mock.patch.object(workspace, name, value):
                rejected = self.cleanup(args)
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["cleanup_status"], "retained")
                self.assertTrue(Path(result["working_directory"]).parent.joinpath(workspace.MARKER).is_file())
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_cleanup_preserves_link_and_external_content(self):
        args = self.prepared()
        result = workspace.operate(self.root, args)
        self.assertTrue(result["ok"], result)
        target = Path(result["working_directory"])
        outside = self.root / "outside.txt"
        outside.write_text("preserve", encoding="utf-8")
        link = target / "escape"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("host does not allow creating symlinks")
        try:
            rejected = self.cleanup(args)
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["cleanup_status"], "retained")
            self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")
        finally:
            link.unlink()
        self.assertEqual(self.cleanup(args)["status"], "absent")


if __name__ == "__main__":
    unittest.main()
