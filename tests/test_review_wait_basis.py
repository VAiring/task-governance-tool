"""Offline fake CLI peers; these tests never read a live Task or call a host."""

from dataclasses import asdict
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools import review_wait_basis as provider


PARENT = "11111111-1111-4111-8111-111111111111"
TASK = "tg_task_0123456789abcdef"
SECRET = "PRIVATE_RESPONSE_MUST_NOT_ESCAPE"


def response():
    return {"ok": True, "status": "review_wait_basis", "basis": {
        "version": 1, "project_id": "project-1", "project_path_hash": "a" * 64,
        "project_binding_generation": 1, "task_id": TASK, "task_status": "in_progress",
        "execution_id": "tg_execution_0123456789abcdef", "ownership_generation": 4,
        "parent_thread_id": PARENT, "contract_revision": 1, "target_kind": "git_snapshot",
        "target_value": "sha256:" + "b" * 64, "target_base_revision": "c" * 40,
        "target_generation": 1, "artifact_manifest_id": "tg_artifact_manifest_0123456789abcdef"}}


class BasisParserTests(unittest.TestCase):
    def parse(self, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        return provider.parse_basis(raw, wait_id="wait-1", task_id=TASK, parent_thread_id=PARENT)

    def test_complete_current_basis_preserves_all_fields(self):
        value = response()
        expected = {k: v for k, v in value["basis"].items() if k not in ("version", "task_status")}
        self.assertEqual(asdict(self.parse(value)), {"wait_id": "wait-1", **expected})
        value["basis"]["task_status"] = "review_pending"
        self.assertEqual(self.parse(value).ownership_generation, 4)

    def test_exact_task_parent_and_closed_fields_required(self):
        mutations = [lambda x: x.update(extra=SECRET), lambda x: x.update(ok=1),
            lambda x: x.update(status="review_wait_ended"), lambda x: x["basis"].update(version=True),
            lambda x: x["basis"].update(task_id="other"), lambda x: x["basis"].update(parent_thread_id="other"),
            lambda x: x["basis"].update(task_status="paused"), lambda x: x["basis"].update(unknown=SECRET),
            lambda x: x["basis"].pop("artifact_manifest_id"), lambda x: x["basis"].update(execution_id=None),
            lambda x: x["basis"].update(ownership_generation=True),
            lambda x: x["basis"].update(project_binding_generation=0),
            lambda x: x["basis"].update(target_kind="invented"),
            lambda x: x["basis"].update(target_value="not-a-fingerprint"),
            lambda x: x["basis"].update(target_base_revision="0" * 40),
            lambda x: x["basis"].update(target_kind="diff_fingerprint"),
            lambda x: x["basis"].update(project_path_hash="not-a-hash")]
        for mutate in mutations:
            value = response()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(provider.BasisError) as caught:
                self.parse(value)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_invalid_duplicate_trailing_nonfinite_and_private_error_never_succeed(self):
        raw = json.dumps(response()).encode()
        for value in [b"", raw[:-1], raw + b"{}", b'{"ok":true,"ok":true}',
                      raw.replace(b'"version": 1', b'"version": NaN'), b"\xff",
                      b" " * (provider.MAX_RESPONSE_BYTES + 1),
                      json.dumps({"ok": False, "code": SECRET, "message": SECRET}).encode()]:
            with self.subTest(raw=value[:20]), self.assertRaises(provider.BasisError) as caught:
                self.parse(value)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_new_ownership_generation_is_not_collapsed_into_old_binding(self):
        before = self.parse(response())
        value = response()
        value["basis"]["ownership_generation"] += 1
        self.assertNotEqual(self.parse(value), before)

    def test_existing_unicode_task_and_external_target_limits_are_preserved(self):
        value = response()
        task_id = "任務" * 64
        target = "日本 語" * 125
        self.assertEqual((len(task_id), len(target)), (128, 500))
        value["basis"].update(task_id=task_id, target_kind="external_revision",
                              target_value=target, target_base_revision="", contract_revision=0)
        actual = provider.parse_basis(json.dumps(value, ensure_ascii=False).encode("utf-8"),
            wait_id="wait-1", task_id=task_id, parent_thread_id=PARENT)
        self.assertEqual(actual.task_id, task_id)
        self.assertEqual(actual.target_value, target)
        self.assertEqual(actual.contract_revision, 0)


class BasisPeerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def reader(self, script, **options):
        peer = self.root / "peer.py"
        peer.write_text(script, encoding="utf-8")
        return provider.PublicTaskBasisReader(self.root, TASK, PARENT, "wait-1",
            test_command=[sys.executable, "-I", "-B", str(peer)], **options)

    def test_fixed_original_task_arguments_and_fresh_response_each_read(self):
        expected = ["wait-basis", "--repo=" + str(self.root), "--task-id=" + TASK]
        payload = self.root / "response.json"
        payload.write_text(json.dumps(response()), encoding="utf-8")
        reader = self.reader("import sys,pathlib\nassert sys.argv[1:] == " + repr(expected)
                             + "\nsys.stdout.write(pathlib.Path('response.json').read_text())\n")
        first = reader()
        value = response()
        value["basis"]["target_generation"] = 2
        value["basis"]["artifact_manifest_id"] = "tg_artifact_manifest_fedcba9876543210"
        payload.write_text(json.dumps(value), encoding="utf-8")
        second = reader()
        self.assertNotEqual(first, second)
        self.assertEqual(second.target_generation, 2)

    def test_nonzero_exit_even_with_success_json_is_unavailable_and_stderr_private(self):
        reader = self.reader("import sys\nsys.stdout.write(" + repr(json.dumps(response()))
                             + ")\nsys.stderr.write(" + repr(SECRET) + ")\nsys.exit(1)\n")
        with self.assertRaises(provider.BasisError) as caught:
            reader()
        self.assertEqual(str(caught.exception), "wait_basis_unavailable")

    def test_timeout_and_oversized_response_reap_the_owned_child(self):
        for script in ["import time\ntime.sleep(10)\n", "import sys,time\nsys.stdout.write('x'*20000)\nsys.stdout.flush()\ntime.sleep(10)\n"]:
            reader = self.reader(script, timeout_seconds=0.15)
            real_popen, children = subprocess.Popen, []
            def launch(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child
            started = time.monotonic()
            with mock.patch.object(provider.subprocess, "Popen", side_effect=launch), self.assertRaises(provider.BasisError):
                reader()
            self.assertLess(time.monotonic() - started, 2)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdout.closed)

    def test_missing_helper_or_invalid_parameters_fail_without_private_details(self):
        reader = provider.PublicTaskBasisReader(self.root, TASK, PARENT, "wait-1")
        with self.assertRaises(provider.BasisError):
            reader()
        for kwargs in [{"timeout_seconds": True}, {"timeout_seconds": float("nan")},
                       {"test_command": SECRET}, {"cleanup_seconds": 0}]:
            with self.assertRaises(provider.BasisError) as caught:
                provider.PublicTaskBasisReader(self.root, TASK, PARENT, "wait-1", **kwargs)
            self.assertEqual(str(caught.exception), "wait_basis_invalid_arguments")

    def test_cleanup_failure_prevents_success_and_still_closes_pipe(self):
        reader = self.reader("import sys\nsys.stdout.write(" + repr(json.dumps(response())) + ")\n")
        real_popen, children = subprocess.Popen, []
        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            child.wait = mock.Mock(side_effect=OSError(SECRET))
            children.append(child)
            return child
        with mock.patch.object(provider.subprocess, "Popen", side_effect=launch), self.assertRaises(provider.BasisError) as caught:
            reader()
        self.assertEqual(str(caught.exception), "wait_basis_unavailable")
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
