"""Reviewer-selected checks use exact restored files, never rebuilt bodies."""

import json
import copy
import base64
import os
import re
import subprocess
import sys
import unittest
import uuid
from contextlib import ExitStack, contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import test_review_handoff_preparation as fixtures
from task_governance_tool import review_handoff_transport as handoff_transport
from task_governance_tool import review_workspace as workspace
from task_governance_tool import review_workspace_caller as caller
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

    def prepared(self, *, commit=False, duplicate=False):
        self.committed_fixture()
        (self.root / "counter.py").write_text("raise RuntimeError('old')\n", encoding="utf-8")
        (self.root / "test_counter.py").write_text(SIX_TESTS, encoding="utf-8")
        (self.root / "日本語's test.txt").write_text("固定資料\n", encoding="utf-8")
        self.git("-c", "core.autocrlf=false", "add", "--", "counter.py", "test_counter.py", "日本語's test.txt")
        self.git("commit", "--quiet", "-m", "Six unchanged checks")
        (self.root / "counter.py").write_text(COUNTER, encoding="utf-8")
        self.git("-c", "core.autocrlf=false", "add", "--", "counter.py")
        if duplicate:
            (self.root / "counter-copy.py").write_text(COUNTER, encoding="utf-8")
            self.git("-c", "core.autocrlf=false", "add", "--", "counter-copy.py")
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
            temp_root_sha256=None)
        self.bind_location(args)
        # Each fixture owns this preallocated handle, even when output is lost.
        self.addCleanup(self.cleanup, args)
        return args

    def bind_location(self, args):
        args.workspace_location = workspace._location_binding(
            self.root, workspace._temporary_parent(self.root), args.packet_sha256, args.workspace_id
        )

    def legacy(self, args):
        args.workspace_location = None
        args.temp_root_sha256 = workspace._path_digest(workspace._temporary_parent(self.root))
        return args

    def cleanup(self, args):
        args = SimpleNamespace(**{**vars(args), "workspace_operation": "cleanup"})
        return workspace.operate(self.root, args)

    def retained_commands(self, args):
        """Previously issued pairs retain their original closed CLI format."""
        common = ["--repo=" + str(self.root), "--packet=" + args.packet,
                  "--packet-sha256=" + args.packet_sha256, "--workspace-id=" + args.workspace_id,
                  "--workspace-location=" + args.workspace_location]
        return {action + "_command": fixtures.preparation._shell(
            [sys.executable, "-B", str(self.helper), "workspace", action, *common])
            for action in ("prepare", "cleanup")}

    def run_template(self, guidance, *, argv=None, timeout=20, ended=True, environment=None):
        code = guidance["caller_code"].replace("selected_argv = []", "selected_argv = " + repr(
            argv or [sys.executable, "-B", "-m", "unittest", "-v"]))
        code = code.replace("selected_timeout_seconds = None", "selected_timeout_seconds = " + repr(timeout))
        code = code.replace("normal_return_confirms_all_processes_ended = False",
                            "normal_return_confirms_all_processes_ended = " + repr(ended))
        return subprocess.run([sys.executable, "-I", "-S", "-B", "-"],
            input=code.encode("utf-8"), capture_output=True, timeout=45, env=environment)

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
        self.bind_location(second_args)
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
        self.assertEqual(set(guidance), {"status", "caller_language", "caller_code", "instructions"})
        self.assertEqual(guidance["status"], "template_ready")
        for key in ("task", "contract", "review_target", "result_template", "result_instructions"):
            self.assertEqual(normal[key], detailed[key])
        self.assertEqual(normal["verification_evidence"]["source_kind"],
                         detailed["verification_evidence"]["source_kind"])
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), temporary_before)
        self.assertEqual(fixtures.file_snapshot(self.root), before)
        result = self.run_template(guidance)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertIn("workspace_cleanup_command", rows[0])
        self.assertEqual(rows[1]["workspace_preparation"]["status"], "ready")
        self.assertEqual(rows[1]["workspace_preparation"]["verification_status"], "not_run")
        self.assertEqual(rows[2], {"verification_returncode": 0})
        self.assertEqual(rows[3]["workspace_cleanup"]["status"], "absent")
        self.assertIn(b"Ran 6 tests", result.stderr)
        self.assertEqual(fixtures.file_snapshot(self.root), before)
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), temporary_before)

    def test_both_guidance_views_do_not_probe_temp_or_allocate_handle(self):
        with mock.patch.object(workspace, "_temporary_parent", side_effect=AssertionError("unused temp probe")), \
             mock.patch.object(caller.uuid, "uuid4", side_effect=AssertionError("unused handle")):
            notice = workspace.workspace_guidance(self.root, "reviews/packet.json", b"unused")
            detailed = workspace.workspace_guidance(self.root, "reviews/packet.json", b"unused", details=True)
        self.assertEqual(set(notice), {"status", "instructions"})
        self.assertEqual(notice["status"], "optional")
        self.assertEqual(detailed["status"], "template_ready")

    def test_saved_location_generated_commands_survive_temp_changes(self):
        args = self.prepared()
        alternate = self.root.parent / "alternate temporary 日本語's"
        alternate.mkdir()
        (alternate / "sentinel.txt").write_text("keep", encoding="utf-8")
        first = {**os.environ, "TMPDIR": str(self.workspace_temp),
                 "TEMP": str(self.workspace_temp), "TMP": str(self.workspace_temp)}
        other = {**first, "TMPDIR": str(alternate), "TEMP": str(alternate), "TMP": str(alternate)}
        priority = {**other, "TMPDIR": str(self.workspace_temp)}
        spelling = {**first, "TMPDIR": str(self.workspace_temp / ".." / self.workspace_temp.name)}
        cases = (
            ("same_parent", first, first, first, self.workspace_temp),
            ("changed_before_prepare", first, other, other, self.workspace_temp),
            ("changed_after_prepare", first, first, other, self.workspace_temp),
            ("alternate_parent_valid", other, first, first, alternate),
            ("tmpdir_priority", priority, other, other, self.workspace_temp),
            ("same_physical_spelling", spelling, first, other, self.workspace_temp),
        )
        project_before = fixtures.file_snapshot(self.root)
        first_before, other_before = fixtures.file_snapshot(self.workspace_temp), fixtures.file_snapshot(alternate)
        for label, read_env, prepare_env, cleanup_env, expected_parent in cases:
            with self.subTest(case=label):
                with mock.patch.dict(os.environ, read_env):
                    retained = SimpleNamespace(**{**vars(args), "workspace_id": uuid.uuid4().hex})
                    self.bind_location(retained)
                guidance = self.retained_commands(retained)
                identity = retained.workspace_id
                root = expected_parent / ("taskgov-review-" + identity)
                # Cleanup before creation proves absence at the saved location,
                # even though the current selector names another valid parent.
                absent = self.material_read(guidance["cleanup_command"], environment=cleanup_env)
                self.assertEqual(json.loads(absent.stdout)["status"], "absent")
                try:
                    prepared = self.material_read(guidance["prepare_command"], environment=prepare_env)
                    self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
                    result = json.loads(prepared.stdout)
                    self.assertEqual(Path(result["working_directory"]), root / "target")
                    self.assertEqual((root / "target/counter.py").read_text(encoding="utf-8"), COUNTER)
                    foreign_parent = alternate if expected_parent == self.workspace_temp else self.workspace_temp
                    self.assertFalse((foreign_parent / root.name).exists())
                finally:
                    cleaned = self.material_read(guidance["cleanup_command"], environment=cleanup_env)
                self.assertEqual(cleaned.returncode, 0, cleaned.stdout + cleaned.stderr)
                self.assertEqual(json.loads(cleaned.stdout)["status"], "absent")
                self.assertFalse(root.exists())
                self.assertEqual(fixtures.file_snapshot(self.root), project_before)
                self.assertEqual(fixtures.file_snapshot(self.workspace_temp), first_before)
                self.assertEqual(fixtures.file_snapshot(alternate), other_before)

    def test_saved_parent_missing_replaced_or_unreadable_never_relocates(self):
        args = self.prepared()
        ready = workspace.operate(self.root, args)
        self.assertTrue(ready["ok"], ready)
        moved = self.workspace_temp.with_name("owned-parent-moved")
        self.workspace_temp.rename(moved)
        try:
            missing = self.cleanup(args)
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["cleanup_status"], "unknown")
            self.assertEqual(missing["diagnostic"], {"stage": "location", "reason": "parent_missing"})
            self.workspace_temp.mkdir()
            try:
                replaced = self.cleanup(args)
                self.assertEqual(replaced["code"], "review_workspace_temp_changed")
                self.assertEqual(replaced["cleanup_status"], "unknown")
                self.assertEqual(replaced["diagnostic"], {"stage": "location", "reason": "parent_changed"})
                self.assertEqual(list(self.workspace_temp.iterdir()), [])
            finally:
                self.workspace_temp.rmdir()
            self.assertTrue((moved / ("taskgov-review-" + args.workspace_id) / "target").is_dir())
        finally:
            moved.rename(self.workspace_temp)
        inspect = workspace.inspect_physical_directory
        def denied(path, **kwargs):
            if path == self.workspace_temp:
                raise PermissionError("private fixture diagnostic")
            return inspect(path, **kwargs)
        with mock.patch.object(workspace, "inspect_physical_directory", side_effect=denied):
            rejected = self.cleanup(args)
            self.assertEqual(rejected["cleanup_status"], "unknown")
            self.assertEqual(rejected["diagnostic"], {"stage": "location", "reason": "parent_inaccessible"})
            self.assertNotIn("private fixture diagnostic", json.dumps(rejected))
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_saved_parent_swap_during_preflight_is_rejected_before_creation(self):
        args = self.prepared()
        from task_governance_tool import verification_runner_git as material
        original = material.preflight_runner_material
        moved = self.workspace_temp.with_name("owned-parent-preflight")
        def swap(*values):
            result = original(*values)
            self.workspace_temp.rename(moved)
            self.workspace_temp.mkdir()
            return result
        try:
            with mock.patch.object(material, "preflight_runner_material", side_effect=swap):
                rejected = workspace.operate(self.root, args)
            self.assertEqual(rejected["code"], "review_workspace_temp_changed")
            self.assertEqual(rejected["cleanup_status"], "not_started")
            self.assertEqual(list(self.workspace_temp.iterdir()), [])
            self.assertEqual(list(moved.iterdir()), [])
        finally:
            if moved.exists():
                self.workspace_temp.rmdir()
                moved.rename(self.workspace_temp)

    def test_location_binding_is_closed_and_bound_to_packet_repo_and_handle(self):
        args = self.prepared()
        original = json.loads(base64.urlsafe_b64decode(args.workspace_location + "=" * (-len(args.workspace_location) % 4)))
        before = fixtures.file_snapshot(self.workspace_temp)
        changes = ({"version": True}, {"version": 2}, {"extra": "invalid"},
                   {"workspace_id": "f" * 32}, {"packet_sha256": "0" * 64},
                   {"repo_sha256": "0" * 64}, {"parent": "../elsewhere"},
                   {"parent_identity": {"device": False, "inode": 0}})
        for change in changes:
            with self.subTest(change=change):
                raw = json.dumps({**original, **change}, sort_keys=True, separators=(",", ":")).encode("ascii")
                encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
                bad = SimpleNamespace(**{**vars(args), "workspace_location": encoded})
                rejected = workspace.operate(self.root, bad)
                self.assertEqual(rejected["code"], "review_workspace_invalid_binding")
                self.assertEqual(rejected["cleanup_status"], "not_started")
        for encoded in ("", "invalid!", "A" * (workspace.LOCATION_LIMIT + 1)):
            bad = SimpleNamespace(**{**vars(args), "workspace_location": encoded})
            self.assertEqual(workspace.operate(self.root, bad)["code"], "review_workspace_invalid_binding")
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), before)

    def test_absence_is_not_reported_after_parent_replacement_during_cleanup(self):
        args = self.prepared()
        root = self.workspace_temp / ("taskgov-review-" + args.workspace_id)
        moved = self.workspace_temp.with_name("owned-parent-absence")
        exists = workspace.path_lexically_exists
        def swap(path):
            if path == root:
                self.workspace_temp.rename(moved)
                self.workspace_temp.mkdir()
                return False
            return exists(path)
        try:
            with mock.patch.object(workspace, "path_lexically_exists", side_effect=swap):
                rejected = self.cleanup(args)
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["code"], "review_workspace_temp_changed")
            self.assertEqual(rejected["cleanup_status"], "unknown")
        finally:
            if moved.exists():
                self.workspace_temp.rmdir()
                moved.rename(self.workspace_temp)

    def test_saved_destination_rejects_git_ancestors_and_mixed_public_binding(self):
        args = self.prepared()
        original = json.loads(base64.urlsafe_b64decode(args.workspace_location + "=" * (-len(args.workspace_location) % 4)))
        raw = json.dumps({**original, "parent": str(self.root),
            "parent_identity": workspace.asdict(workspace.inspect_physical_directory(self.root).identity)},
            sort_keys=True, separators=(",", ":")).encode("ascii")
        bad = SimpleNamespace(**{**vars(args),
            "workspace_location": base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")})
        before = fixtures.file_snapshot(self.root)
        self.assertEqual(workspace.operate(self.root, bad)["code"], "review_workspace_temp_unavailable")
        for action in ("prepare", "cleanup"):
            mixed = self.invoke("workspace", action, "--repo", str(self.root), "--packet", args.packet,
                "--packet-sha256", args.packet_sha256, "--workspace-id", args.workspace_id,
                "--workspace-location", args.workspace_location,
                "--temp-root-sha256", workspace._path_digest(self.workspace_temp))
            self.assertNotEqual(mixed.returncode, 0)
            self.assertEqual(json.loads(mixed.stdout)["code"], "handoff_invalid_arguments")
            self.assertNotIn(args.workspace_location.encode(), mixed.stdout + mixed.stderr)
        self.assertEqual(fixtures.file_snapshot(self.root), before)

    def test_replaced_workspace_root_is_retained_even_with_copied_owner(self):
        args = self.prepared()
        ready = workspace.operate(self.root, args)
        self.assertTrue(ready["ok"], ready)
        root = Path(ready["working_directory"]).parent
        moved = root.with_name(root.name + "-owned-test-move")
        marker = (root / workspace.MARKER).read_bytes()
        root.rename(moved)
        root.mkdir()
        replacement_marker = root / workspace.MARKER
        replacement_marker.write_bytes(marker)
        try:
            rejected = self.cleanup(args)
            self.assertEqual(rejected["code"], "review_workspace_owner_mismatch")
            self.assertEqual(rejected["cleanup_status"], "retained")
            self.assertEqual(replacement_marker.read_bytes(), marker)
            self.assertTrue((moved / "target").is_dir())
        finally:
            replacement_marker.unlink()
            root.rmdir()
            moved.rename(root)
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_details_keep_cleanup_handle_after_unobserved_prepare_output(self):
        args = self.prepared()
        before = fixtures.file_snapshot(self.workspace_temp)
        copy = caller.Workspace(self.root, args.packet, args.packet_sha256)
        cleanup_command = copy.cleanup_command
        self.assertIsNotNone(cleanup_command)
        try:
            # Lost output does not require another prepare or new binding.
            copy.prepare()
            self.assertEqual(len(list(self.workspace_temp.glob("taskgov-review-*/target"))), 1)
        finally:
            cleaned = self.material_read(cleanup_command, environment={**os.environ, "TMPDIR": str(self.root)})
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
                "--workspace-location", args.workspace_location)
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
        self.assertEqual(material["verification_workspace"]["status"], "template_ready")
        self.assertNotIn("prepare_command", material["verification_workspace"])
        failed = self.run_template(material["verification_workspace"], environment={**os.environ, "TMPDIR": str(self.root)})
        rows = [json.loads(line) for line in failed.stdout.splitlines()]
        self.assertEqual(rows[1]["workspace_preparation"]["diagnostic"],
                         {"stage": "selection", "reason": "parent_unavailable"})
        self.assertEqual(len(rows), 2)
        self.assertNotIn(b"Ran 6 tests", failed.stderr)
        self.assertEqual(fixtures.file_snapshot(self.root), before)

    def test_host_temp_alias_uses_physical_destination_and_retarget_is_rejected(self):
        args = self.legacy(self.prepared())
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
            self.assertEqual(json.loads(read.stdout)["review_material"]["verification_workspace"]["status"], "template_ready")
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["working_directory"]).parent.parent, self.workspace_temp)
            changed_parent = self.root.parent / "other-temp"
            (changed_parent / self.workspace_temp.name).mkdir(parents=True)
            alias.unlink()
            alias.symlink_to(changed_parent, target_is_directory=True)
            rejected = self.cleanup(args)
            self.assertEqual(rejected["code"], "review_workspace_temp_changed")
            self.assertEqual(rejected["cleanup_status"], "unknown")
            self.assertTrue(Path(result["working_directory"]).is_dir())
        self.assertEqual(self.cleanup(args)["status"], "absent")
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), before)

    def test_alias_resolution_does_not_admit_git_destination_or_change_bound_root(self):
        args = self.legacy(self.prepared())
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
            self.assertEqual(rejected["cleanup_status"], "unknown")
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
                raise handoff_transport.HandoffError("review_packet_stale")
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

    def test_reused_blob_failure_cleans_real_partial_materialization(self):
        args = self.prepared(duplicate=True)
        from task_governance_tool import verification_runner_git as material
        write = material._stream_blob_to_file
        reused = []

        def fail_after_reuse(*values, **kwargs):
            result = write(*values, **kwargs)
            if kwargs.get("cached_blob") is not None:
                reused.append(values[2].relative_posix_path)
                raise material.VerificationRunnerGitError("materialization_failed")
            return result

        with mock.patch.object(material, "_stream_blob_to_file", side_effect=fail_after_reuse):
            result = workspace.operate(self.root, args)
        self.assertEqual(len(reused), 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["cleanup_status"], "absent")
        self.assertNotIn("working_directory", result)
        self.assertFalse((self.workspace_temp / ("taskgov-review-" + args.workspace_id)).exists())

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
            self.assertEqual(result["diagnostic"], {"stage": "identity", "reason": "identity_unconfirmed"})
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
        self.assertEqual(rejected["diagnostic"], {"stage": "live_before", "reason": "public_response_incomplete"})

    def test_structurally_incomplete_public_packets_are_classified_before_and_after_restore(self):
        original = self.prepared()
        packet = json.loads((self.root / original.packet).read_bytes())
        envelope = {"ok": True, "command": "review.prepare", "project_id": None,
                    "data": packet, "warnings": [], "errors": []}
        valid = json.dumps(envelope).encode()
        missing_title = copy.deepcopy(packet)
        del missing_title["task"]["title"]
        invalid_context = {**packet, "review_session_context": {}}
        invalid_evidence = {**packet, "verification_evidence": {}}
        for phase in ("live_before", "live_after"):
            for label, data in (("empty", {}), ("nested_missing", missing_title),
                                ("context", invalid_context), ("evidence", invalid_evidence)):
                with self.subTest(phase=phase, case=label):
                    args = SimpleNamespace(**{**vars(original), "workspace_id": uuid.uuid4().hex})
                    self.bind_location(args)
                    invalid = json.dumps({**envelope, "data": data}).encode()
                    responses = [(0, invalid)] if phase == "live_before" else [(0, valid), (0, invalid)]
                    with mock.patch.object(fixtures.preparation, "_capture", side_effect=responses):
                        refused = workspace.operate(self.root, args)
                    self.assertEqual(refused["code"], "handoff_response_invalid")
                    self.assertEqual(refused["diagnostic"], {"stage": phase, "reason": "public_response_incomplete"})
                    self.assertEqual(refused["cleanup_status"], "not_started" if phase == "live_before" else "absent")
                    self.assertFalse((self.workspace_temp / ("taskgov-review-" + args.workspace_id)).exists())

    def test_live_public_errors_and_valid_different_identity_are_not_malformed(self):
        args = self.prepared()
        packet = json.loads((self.root / args.packet).read_bytes())
        envelope = {"ok": False, "command": "review.prepare", "project_id": None, "data": {},
                    "warnings": [], "errors": [{"code": "writer_busy", "message": "State unavailable."}]}
        with mock.patch.object(fixtures.preparation, "_capture", return_value=(2, json.dumps(envelope).encode())):
            failed = workspace.operate(self.root, args)
        self.assertEqual(failed["code"], "writer_busy")
        self.assertEqual(failed["diagnostic"], {"stage": "live_before", "reason": "unavailable"})
        different = copy.deepcopy(packet)
        other = "tg_task_" + "a" * 16
        different["task"]["task_id"] = different["result_template"]["task_id"] = other
        envelope.update(ok=True, data=different, errors=[])
        with mock.patch.object(fixtures.preparation, "_capture", return_value=(0, json.dumps(envelope).encode())):
            failed = workspace.operate(self.root, args)
        self.assertEqual(failed["code"], "review_target_mismatch")
        self.assertNotEqual(failed["diagnostic"]["reason"], "public_response_incomplete")

    def test_direct_caller_sanitizes_known_dependency_errors_in_prepare_and_cleanup(self):
        args = self.prepared()
        selected = caller.Workspace(self.root, args.packet, args.packet_sha256)
        private = "private-command-argument"
        with mock.patch.object(handoff_transport.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired([private], 2)):
            failed = selected.prepare()
            cleaned = selected.cleanup(processes_ended=True)
        self.assertEqual(failed["diagnostic"], {"stage": "packet", "reason": "unavailable"})
        self.assertEqual(failed["cleanup_status"], "not_started")
        self.assertEqual(cleaned["cleanup_status"], "unknown")
        self.assertNotIn(private, json.dumps([failed, cleaned]))
        # Direct input/domain validation also used to rely on the CLI wrapper.
        from task_governance_tool.reviews import ReviewEvidenceError
        with mock.patch.object(workspace, "_packet", side_effect=ReviewEvidenceError("invalid_review_evidence", private)):
            domain = workspace.operate(self.root, args)
        self.assertFalse(domain["ok"])
        self.assertNotIn(private, json.dumps(domain))

    def test_generated_template_sanitizes_preparation_timeout_without_running_verification(self):
        args = self.prepared()
        read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet, "--material-details")
        self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
        guidance = json.loads(read.stdout)["review_material"]["verification_workspace"]
        injected = """from task_governance_tool import review_handoff_transport as fault_boundary
def injected_timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(['private-timeout-argv'], 2)
fault_boundary.require_ignored = injected_timeout
"""
        guidance["caller_code"] = guidance["caller_code"].replace("copy = Workspace(", injected + "copy = Workspace(")
        result = self.run_template(guidance)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["workspace_preparation"]["diagnostic"], {"stage": "packet", "reason": "unavailable"})
        self.assertNotIn(b"private-timeout-argv", result.stdout + result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertNotIn(b"Ran 6 tests", result.stderr)

    def test_preparation_interruption_retains_owned_root_without_automatic_cleanup(self):
        args = self.prepared()
        from task_governance_tool import verification_runner_git as material
        with mock.patch.object(material, "materialize_runner_target", side_effect=KeyboardInterrupt):
            failed = workspace.operate(self.root, args)
        self.assertEqual(failed["code"], "review_workspace_interrupted")
        self.assertEqual(failed["cleanup_status"], "retained")
        self.assertTrue((self.workspace_temp / ("taskgov-review-" + args.workspace_id)).is_dir())
        self.assertEqual(self.cleanup(args)["status"], "absent")

    def test_interruption_inside_failure_cleanup_is_sanitized_without_another_attempt(self):
        args = self.prepared()
        selected = caller.Workspace(self.root, args.packet, args.packet_sha256)
        from task_governance_tool import verification_runner_git as material
        with mock.patch.object(material, "materialize_runner_target", side_effect=ValueError("private restoration error")), \
             mock.patch.object(workspace, "_remove", side_effect=KeyboardInterrupt) as remove:
            failed = selected.prepare()
        remove.assert_called_once()
        self.assertEqual(failed["code"], "review_workspace_interrupted")
        self.assertEqual(failed["diagnostic"], {"stage": "cleanup", "reason": "unavailable"})
        self.assertEqual(failed["cleanup_status"], "retained")
        self.assertNotIn("private", json.dumps(failed))
        self.assertEqual(selected.cleanup(processes_ended=True)["status"], "absent")

    def test_cleanup_uses_retained_binding_after_live_generation_advances(self):
        args = self.prepared()
        ready = workspace.operate(self.root, args)
        self.assertTrue(ready["ok"], ready)
        self.cli("review", "target", "set", ready["task_id"], "--kind", "git_snapshot")
        self.assertEqual(self.cleanup(args)["status"], "absent")
        self.assertEqual(workspace.operate(self.root, args)["code"], "review_packet_stale")

    def test_temp_environment_drift_does_not_claim_old_workspace_absent(self):
        args = self.legacy(self.prepared())
        ready = workspace.operate(self.root, args)
        self.assertTrue(ready["ok"], ready)
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.root.parent)}):
            rejected = self.cleanup(args)
            self.assertEqual(rejected["code"], "review_workspace_temp_changed")
            self.assertEqual(rejected["cleanup_status"], "unknown")
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
            copy = caller.Workspace(self.root, args.packet, args.packet_sha256)
            self.assertEqual(copy.prepare()["code"], "review_workspace_temp_unavailable")

    def test_public_rejection_never_echoes_invalid_identity_even_before_location_check(self):
        args = self.prepared()
        invalid = "invalid-workspace-value-for-privacy-regression"
        for packet in (args.packet, "reviews/missing.json"):
            result = self.invoke("workspace", "prepare", "--repo", str(self.root),
                "--packet", packet, "--packet-sha256", args.packet_sha256,
                "--workspace-id", invalid, "--workspace-location", args.workspace_location)
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
        self.bind_location(args)
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

    def test_caller_selects_in_execution_invocation_not_detailed_read(self):
        args = self.prepared(commit=True)
        old = self.workspace_temp / "old-host-call"
        old.mkdir()
        with mock.patch.dict(os.environ, {"TMPDIR": str(old)}):
            read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet, "--material-details")
        guidance = json.loads(read.stdout)["review_material"]["verification_workspace"]
        old.rmdir()
        result = self.run_template(guidance)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[1]["workspace_preparation"]["status"], "ready")
        self.assertEqual(rows[1]["workspace_preparation"]["review_target"]["kind"], "git_commit")
        self.assertEqual(rows[-1]["workspace_cleanup"]["status"], "absent")
        self.assertIn(b"Ran 6 tests", result.stderr)

    def test_source_self_host_template_uses_its_explicit_repository(self):
        from tests.m14_test_support import make_source_self_host
        self.install = make_source_self_host(self.root.parent / "self-host 日本語's")
        self.root = self.install.project_root
        self.helper = self.install.skill_root / "scripts/review_handoff.py"
        invoke_cli = self.cli
        self.cli = lambda *values: invoke_cli(*values, "--repo", str(self.root))
        self.git("init", "--quiet")
        (self.root / ".gitignore").write_text("/.taskgov/\n/reviews/\n", encoding="utf-8")
        self.cli("setup")
        args = self.prepared()
        read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet, "--material-details")
        self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
        guidance = json.loads(read.stdout)["review_material"]["verification_workspace"]
        result = self.run_template(guidance)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[1]["workspace_preparation"]["status"], "ready")
        self.assertEqual(rows[2], {"verification_returncode": 0})
        self.assertEqual(rows[3]["workspace_cleanup"]["status"], "absent")
        self.assertIn(b"Ran 6 tests", result.stderr)

    def test_old_split_binding_missing_parent_has_fixed_reason_no_reselection(self):
        args = self.prepared()
        old = self.workspace_temp / "old-bound-parent"
        old.mkdir()
        args.workspace_location = workspace._location_binding(self.root, old, args.packet_sha256, args.workspace_id)
        old.rmdir()
        before = fixtures.file_snapshot(self.workspace_temp)
        with mock.patch.object(workspace, "_temporary_parent", side_effect=AssertionError("no reselection")):
            refused = workspace.operate(self.root, args)
        self.assertEqual(refused["diagnostic"], {"stage": "location", "reason": "parent_missing"})
        self.assertEqual(refused["cleanup_status"], "not_started")
        self.assertFalse(old.exists())
        self.assertEqual(fixtures.file_snapshot(self.workspace_temp), before)

    def test_creation_denial_and_race_are_distinct_without_absence_claim(self):
        args = self.prepared()
        root = self.workspace_temp / ("taskgov-review-" + args.workspace_id)
        original = Path.mkdir
        for reason in ("creation_denied", "creation_conflict"):
            def create(path, *values, **options):
                if path == root:
                    if reason == "creation_conflict":
                        original(path, *values, **options)
                        raise FileExistsError("private race exception")
                    raise PermissionError("private denial exception")
                return original(path, *values, **options)
            with self.subTest(reason=reason), mock.patch.object(Path, "mkdir", create):
                refused = workspace.operate(self.root, args)
            self.assertEqual(refused["diagnostic"], {"stage": "creation", "reason": reason})
            self.assertEqual(refused["cleanup_status"], "not_started")
            self.assertNotIn("private", json.dumps(refused))
            self.assertNotIn(str(root), json.dumps(refused))
            if reason == "creation_conflict":
                self.assertTrue(root.is_dir())
                # The injected race belongs to this fixture, not the helper.
                self.assertEqual(list(root.iterdir()), [])
                root.rmdir()
            else:
                self.assertFalse(root.exists())

    def test_caller_retains_while_process_live_and_does_not_repeat_preparation(self):
        args = self.prepared()
        copy = caller.Workspace(self.root, args.packet, args.packet_sha256)
        result = copy.prepare()
        self.assertTrue(result["ok"], result)
        target = Path(result["working_directory"])
        process = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], cwd=target)
        try:
            with mock.patch.object(workspace, "operate", side_effect=AssertionError("no operation")):
                refused = copy.cleanup(processes_ended=False)
                self.assertEqual(refused["diagnostic"], {"stage": "caller", "reason": "process_end_unconfirmed"})
                self.assertEqual(refused["cleanup_status"], "retained")
                repeated = copy.prepare()
                self.assertEqual(repeated["diagnostic"]["reason"], "preparation_already_attempted")
            self.assertIsNone(process.poll())
            self.assertTrue(target.is_dir())
        finally:
            # This fixture launched a known single Python process, no children.
            process.terminate()
            process.wait(timeout=10)
        self.assertEqual(copy.cleanup(processes_ended=True)["status"], "absent")

    def test_template_failure_timeout_and_unknown_lifetime_never_infer_pass(self):
        args = self.prepared()
        read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet, "--material-details")
        self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
        guidance = json.loads(read.stdout)["review_material"]["verification_workspace"]
        for label, argv, timeout, ended in (
            ("failed", [sys.executable, "-B", "-c", "raise SystemExit(7)"], 20, True),
            ("timeout", [sys.executable, "-B", "-c", "import time; time.sleep(5)"], 0.1, True),
            ("unknown", [sys.executable, "-B", "-c", "pass"], 20, False),
        ):
            with self.subTest(case=label):
                result = self.run_template(guidance, argv=argv, timeout=timeout, ended=ended)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                ready = rows[1]["workspace_preparation"]
                self.assertTrue(ready["ok"], ready)
                self.assertEqual(ready["verification_status"], "not_run")
                if label == "failed":
                    self.assertEqual(rows[2], {"verification_returncode": 7})
                    self.assertEqual(rows[3]["workspace_cleanup"]["status"], "absent")
                else:
                    self.assertEqual(rows[2], {"verification_status": "timeout"} if label == "timeout"
                                     else {"verification_returncode": 0})
                    self.assertEqual(rows[3]["workspace_cleanup"]["diagnostic"]["reason"], "process_end_unconfirmed")
                    self.assertTrue(Path(ready["working_directory"]).is_dir())
                    # This fixture's command is known to have no descendants;
                    # subprocess.run has joined/terminated its only process.
                    cleaned = self.material_read(rows[0]["workspace_cleanup_command"])
                    self.assertEqual(json.loads(cleaned.stdout)["status"], "absent")

    def test_lost_verification_output_does_not_change_preparation_or_cleanup_meaning(self):
        args = self.prepared()
        copy = caller.Workspace(self.root, args.packet, args.packet_sha256)
        ready = copy.prepare()
        # The result is deliberately not used as review/verification evidence.
        subprocess.run([sys.executable, "-B", "-c", "raise SystemExit(9)"],
            cwd=ready["working_directory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        self.assertEqual(ready["verification_status"], "not_run")
        self.assertEqual(copy.cleanup(processes_ended=True)["status"], "absent")

    def test_caller_preserves_unknown_cleanup_until_original_parent_is_restored(self):
        args = self.prepared()
        copy = caller.Workspace(self.root, args.packet, args.packet_sha256)
        self.assertTrue(copy.prepare()["ok"])
        moved = self.workspace_temp.with_name("owned-caller-parent")
        self.workspace_temp.rename(moved)
        try:
            result = copy.cleanup(processes_ended=True)
            self.assertEqual(result["cleanup_status"], "unknown")
            self.assertEqual(result["diagnostic"], {"stage": "location", "reason": "parent_missing"})
            self.assertEqual(copy.cleanup(processes_ended=False)["cleanup_status"], "unknown")
        finally:
            moved.rename(self.workspace_temp)
        self.assertEqual(copy.cleanup(processes_ended=True)["status"], "absent")

    def test_template_launch_error_retains_without_raw_exception(self):
        args = self.prepared()
        read = self.invoke("read", "--repo", str(self.root), "--packet", args.packet, "--material-details")
        self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
        guidance = json.loads(read.stdout)["review_material"]["verification_workspace"]
        command = str(self.root / "private-command-not-found")
        result = self.run_template(guidance, argv=[command])
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(rows[1]["workspace_preparation"]["ok"], rows)
        self.assertEqual(rows[2], {"verification_status": "unavailable"})
        self.assertEqual(rows[3]["workspace_cleanup"]["diagnostic"]["reason"], "process_end_unconfirmed")
        self.assertNotIn(command.encode(), result.stdout + result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        # The nonexistent executable never launched a process in this fixture.
        cleaned = self.material_read(rows[0]["workspace_cleanup_command"])
        self.assertEqual(json.loads(cleaned.stdout)["status"], "absent")

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


class WorkspaceCallerBoundaryTests(unittest.TestCase):
    @contextmanager
    def interrupted_construction(self, boundary):
        sites = (
            ("identity", caller.uuid, "uuid4", SimpleNamespace(hex="b" * 32)),
            ("parent", workspace, "_temporary_parent", Path.cwd()),
            ("binding", workspace, "_location_binding", "fixture-binding"),
            ("command", fixtures.preparation, "_shell", "fixture-cleanup"),
        )
        with ExitStack() as stack:
            observed = {}
            for name, owner, attribute, value in sites:
                observed[name] = stack.enter_context(mock.patch.object(owner, attribute,
                    return_value=value, side_effect=KeyboardInterrupt("private-selection-error")
                    if name == boundary else None))
            operation = stack.enter_context(mock.patch.object(workspace, "operate",
                side_effect=AssertionError("no preparation or cleanup after interrupted selection")))
            yield observed, operation

    def assert_interrupted(self, result, boundary):
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "review_workspace_interrupted")
        self.assertEqual(result["diagnostic"], {"stage": "selection", "reason": "unavailable"})
        self.assertEqual(result["cleanup_status"], "unknown")
        self.assertEqual(result["workspace_id"], None if boundary == "identity" else "b" * 32)
        self.assertNotIn("private-selection-error", json.dumps(result))

    def test_construction_interruption_publishes_no_handle_or_effects(self):
        for boundary in ("identity", "parent", "binding", "command"):
            with self.subTest(boundary=boundary), self.interrupted_construction(boundary) as (observed, operation):
                selected = caller.Workspace(Path.cwd(), "unused-packet.json", "a" * 64)
                result = selected.prepare()
                self.assert_interrupted(result, boundary)
                self.assertIsNone(selected.args)
                self.assertIsNone(selected.cleanup_command)
                self.assertEqual(selected.cleanup(processes_ended=True), result)
                self.assertEqual(selected.cleanup(processes_ended=False)["cleanup_status"], "unknown")
                self.assertEqual(selected.prepare()["diagnostic"]["reason"], "preparation_already_attempted")
                observed[boundary].assert_called_once()
                operation.assert_not_called()

    def test_generated_template_sanitizes_every_selection_boundary(self):
        code = caller.caller_template(Path.cwd(), "unused-packet.json", "a" * 64)
        code = code.replace("selected_argv = []", "selected_argv = ['must-not-run']")
        for boundary in ("identity", "parent", "binding", "command"):
            output = StringIO()
            with self.subTest(boundary=boundary), self.interrupted_construction(boundary) as (observed, operation), \
                 mock.patch.object(subprocess, "run", side_effect=AssertionError("no verifier")) as verifier, \
                 mock.patch.object(sys, "path", list(sys.path)), redirect_stdout(output):
                exec(compile(code, "<generated-workspace-caller>", "exec"), {"__name__": "__main__"})
                rows = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0], {"workspace_cleanup_command": None})
                self.assert_interrupted(rows[1]["workspace_preparation"], boundary)
                observed[boundary].assert_called_once()
                operation.assert_not_called()
                verifier.assert_not_called()


if __name__ == "__main__":
    unittest.main()
