"""Public installed handoff entry plus filesystem/validation failure boundaries."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.m14_test_support import make_physical_install
from tests.test_review_results import document, encode, receipt, FINGERPRINT
from task_governance_tool import review_handoff as handoff
from task_governance_tool import review_results
from task_governance_tool.reviews import ReviewEvidenceError
from task_governance_tool.task_values import TaskValidationError


def packet_for(payload):
    return {
        "task": {"task_id": payload["task_id"], "review_tier": 2},
        "contract": {"revision": payload["contract_revision"]},
        "review_target": payload["review_target"],
        "result_template": review_results.review_result_template(
            payload["task_id"], payload["contract_revision"], payload["review_target"]),
        "changed_paths_available": False, "changed_paths": [], "changed_paths_total": 0,
        "changed_paths_truncated": False, "review_focus": [], "required_output": [],
        "result_instructions": [], "receipt_command": "unused in filesystem unit checks",
    }


class ReviewHandoffFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True, capture_output=True)
        (self.root / ".gitignore").write_text("/reviews/\n", encoding="utf-8")
        (self.root / "reviews").mkdir()
        self.payload = document(receipts=[receipt(findings=[{"severity": "low", "summary": "example.py:1 Kept"}])])
        self.raw = b"\n " + json.dumps(self.payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        self.packet = "reviews/packet.json"
        (self.root / self.packet).write_bytes(encode(packet_for(self.payload)))
        self.output = "reviews/a.json"

    def save(self, raw=None, output=None, approvals=()):
        return handoff.save(self.root, self.packet, output or self.output,
                            self.raw if raw is None else raw, approvals)

    def test_save_and_submission_preserve_original_bytes_and_all_findings(self):
        result = self.save()
        self.assertEqual(result, {"ok": True, "status": "saved", "path": self.output,
                                  "verdict": "pass", "finding_count": 1})
        self.assertEqual((self.root / self.output).read_bytes(), self.raw)
        second = copy.deepcopy(self.payload)
        second["receipts"][0].update(reviewer="reviewer-b", verdict="changes_requested")
        other = encode(second)
        self.save(other, "reviews/b.json")
        task, raw = handoff.submission(self.root, self.packet, [self.output, "reviews/b.json"])
        self.assertEqual(task, self.payload["task_id"])
        self.assertEqual(raw, b"[" + self.raw + b"," + other + b"]")
        self.assertEqual(len(review_results.decode_review_results(raw)["receipts"]), 2)

    def test_invalid_original_is_rejected_before_any_creation(self):
        invalid = [b"", b"{}", self.raw[:-9], b"[" + self.raw + b"]", b"\xef\xbb\xbf" + self.raw,
                   self.raw + b" " * review_results.REVIEW_RESULTS_INPUT_LIMIT,
                   self.raw.replace(b'"version": 1', b'"version": 1, "version": 1')]
        for mutation in (
            lambda p: p.update(version=True),
            lambda p: p.update(contract_revision=2),
            lambda p: p.update(task_id="tg_task_1123456789abcdef"),
            lambda p: p["review_target"].update(generation=2),
            lambda p: p["review_target"].update(kind="external_revision"),
            lambda p: p["review_target"].update(value="different"),
            lambda p: p["review_target"].update(base_revision="different"),
            lambda p: p["receipts"][0]["provenance"].update(model_state="declared"),
            lambda p: p["receipts"][0].update(summary="password=never-retain"),
            lambda p: p["receipts"].append(receipt()),
        ):
            payload = copy.deepcopy(self.payload)
            mutation(payload)
            invalid.append(encode(payload))
        for raw in invalid:
            with self.subTest(raw_length=len(raw)), self.assertRaises((HandoffFailure, ReviewEvidenceError, TaskValidationError)):
                self.save(raw)
            self.assertFalse((self.root / self.output).exists())

    def test_approval_stays_explicit_and_is_not_inferred_by_save(self):
        raw = encode(document(receipts=[receipt(kind="self_review_fallback")]))
        with self.assertRaises(ReviewEvidenceError):
            self.save(raw)
        self.assertFalse((self.root / self.output).exists())
        self.save(raw, approvals=["reviewer-a"])
        with self.assertRaises(ReviewEvidenceError):
            handoff.submission(self.root, self.packet, [self.output])
        self.assertEqual(handoff.submission(self.root, self.packet, [self.output], ["reviewer-a"])[1],
                         b"[" + raw + b"]")

    def test_lost_save_acknowledgement_never_retries_or_erases_the_original(self):
        import io
        from contextlib import redirect_stdout
        class LostOutput(io.StringIO):
            def write(self, text):
                raise BrokenPipeError("closed consumer")
        original_save = handoff.save
        with mock.patch.object(handoff.sys, "stdin", mock.Mock(buffer=io.BytesIO(self.raw))), \
             mock.patch.object(handoff, "save", wraps=original_save) as saved, \
             redirect_stdout(LostOutput()):
            code = handoff.main(["save", "--repo", str(self.root), "--packet", self.packet, "--output", self.output])
        self.assertEqual(code, 1)
        saved.assert_called_once()
        self.assertEqual((self.root / self.output).read_bytes(), self.raw)

    def test_existing_file_and_failed_write_residue_are_never_removed(self):
        self.save()
        with self.assertRaises(FileExistsError):
            self.save()
        self.assertEqual((self.root / self.output).read_bytes(), self.raw)
        write = os.write
        calls = []
        def partial_then_fail(fd, raw):
            calls.append(True)
            if len(calls) == 1:
                return write(fd, raw[:8])
            raise OSError("private detail")
        with mock.patch.object(handoff.os, "write", side_effect=partial_then_fail):
            with self.assertRaises(OSError):
                self.save(output="reviews/partial.json")
        self.assertEqual((self.root / "reviews/partial.json").read_bytes(), self.raw[:8])
        with self.assertRaises(FileExistsError):
            self.save(output="reviews/partial.json")

    def test_readback_failure_has_no_ready_acknowledgement(self):
        read = handoff._read
        def denied(path, limit):
            if path.name == "a.json":
                raise PermissionError("private path")
            return read(path, limit)
        with mock.patch.object(handoff, "_read", side_effect=denied), self.assertRaises(PermissionError):
            self.save()
        self.assertEqual((self.root / self.output).read_bytes(), self.raw)

    def test_permission_and_flush_failure_do_not_acknowledge_or_clean_up(self):
        with mock.patch.object(handoff.os, "open", side_effect=PermissionError("private detail")):
            with self.assertRaises(PermissionError):
                self.save()
        self.assertFalse((self.root / self.output).exists())
        with mock.patch.object(handoff.os, "fsync", side_effect=OSError("flush failed")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual((self.root / self.output).read_bytes(), self.raw)

    def test_linked_parent_and_replaced_open_file_are_refused(self):
        nested = self.root / "reviews/nested"
        nested.mkdir()
        lstat = Path.lstat
        def linked(path):
            if path == nested:
                value = mock.Mock(st_mode=stat_mode_regular(), st_file_attributes=1024)
                return value
            return lstat(path)
        with mock.patch.object(Path, "lstat", linked), self.assertRaises(HandoffFailure):
            self.save(output="reviews/nested/a.json")
        self.assertFalse((nested / "a.json").exists())
        self.save()
        fstat = os.fstat
        def replaced(descriptor):
            observed = fstat(descriptor)
            value = mock.Mock(wraps=observed)
            for key in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"):
                setattr(value, key, getattr(observed, key))
            value.st_file_attributes = 0
            value.st_ino += 1
            return value
        with mock.patch.object(handoff.os, "fstat", side_effect=replaced), self.assertRaises(HandoffFailure):
            handoff._read(self.root / self.output, review_results.REVIEW_RESULTS_INPUT_LIMIT)

    def test_packet_mismatch_or_truncation_and_unknown_submission_preserve_sources(self):
        packet = packet_for(self.payload)
        packet["result_template"]["version"] = True
        (self.root / self.packet).write_bytes(encode(packet))
        with self.assertRaises(HandoffFailure):
            self.save()
        (self.root / self.packet).write_bytes(b"{")
        with self.assertRaises(ValueError):
            self.save()
        self.assertFalse((self.root / self.output).exists())
        (self.root / self.packet).write_bytes(encode(packet_for(self.payload)))
        self.save()
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        # Explicitly injected transport interruption, not a child retry loop.
        task, framed = handoff.submission(self.root, self.packet, [self.output])
        with mock.patch.object(handoff, "submission", return_value=(task, framed)), \
             mock.patch.object(handoff.subprocess, "run", side_effect=KeyboardInterrupt) as launch, \
             redirect_stdout(output):
            code = handoff.main(["submit", "--repo", str(self.root), "--packet", self.packet, self.output])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "handoff_outcome_unknown")
        launch.assert_called_once()
        self.assertEqual((self.root / self.output).read_bytes(), self.raw)

    def test_changed_saved_content_and_packet_fail_without_cleanup(self):
        write = handoff._write_new
        def changed(path, raw):
            write(path, raw)
            path.write_bytes(raw.replace(b"Kept", b"Edit"))
        with mock.patch.object(handoff, "_write_new", side_effect=changed), self.assertRaises(HandoffFailure):
            self.save()
        self.assertTrue((self.root / self.output).exists())
        packet_bytes = (self.root / self.packet).read_bytes()
        def packet_changed(path, raw):
            write(path, raw)
            (self.root / self.packet).write_bytes(packet_bytes + b" ")
        with mock.patch.object(handoff, "_write_new", side_effect=packet_changed), self.assertRaises(HandoffFailure):
            self.save(output="reviews/b.json")

    def test_path_ignore_link_hardlink_and_parent_boundaries(self):
        for path in ("../outside.json", "/outside.json", "reviews/../a.json", "reviews/a.json:ads",
                     "reviews/NUL.json", "reviews/missing/a.json", "unignored.json", ".taskgov/result.json"):
            with self.subTest(path=path), self.assertRaises((HandoffFailure, OSError)):
                self.save(output=path)
        self.save()
        os.link(self.root / self.output, self.root / "reviews/hard.json")
        with self.assertRaises(HandoffFailure):
            handoff.submission(self.root, self.packet, [self.output])
        try:
            (self.root / "reviews/link.json").symlink_to(self.root / self.packet)
        except OSError:
            # Real symlink creation may need Windows privilege; still exercise
            # the exact native reparse metadata refusal without changing ACLs.
            details = mock.Mock(st_mode=stat_mode_regular(), st_nlink=1,
                                st_file_attributes=1024)
            with self.assertRaises(HandoffFailure):
                handoff._physical(details)
        else:
            with self.assertRaises(HandoffFailure):
                handoff.submission(self.root, self.packet, ["reviews/link.json"])

    def test_tracked_ignored_file_is_refused(self):
        self.save()
        subprocess.run(["git", "-C", str(self.root), "add", "-f", self.output], check=True, capture_output=True)
        with self.assertRaises(HandoffFailure):
            handoff.submission(self.root, self.packet, [self.output])

    def test_inflight_read_change_and_fifo_are_refused(self):
        self.save()
        read = os.read
        changed = []
        def concurrent(fd, size):
            result = read(fd, size)
            if result and not changed:
                changed.append(True)
                with (self.root / self.output).open("ab") as target:
                    target.write(b" ")
            return result
        with mock.patch.object(handoff.os, "read", side_effect=concurrent), self.assertRaises(HandoffFailure):
            handoff._read(self.root / self.output, review_results.REVIEW_RESULTS_INPUT_LIMIT)
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "reviews/fifo.json")
            with self.assertRaises(HandoffFailure):
                handoff.submission(self.root, self.packet, ["reviews/fifo.json"])

    def test_second_original_and_aggregate_capacity_are_checked_before_submission(self):
        self.save()
        other = self.root / "reviews/b.json"
        other.write_bytes(b"{}")
        with self.assertRaises(ReviewEvidenceError):
            handoff.submission(self.root, self.packet, [self.output, "reviews/b.json"])
        other.write_bytes(self.raw)
        with self.assertRaises(ReviewEvidenceError):
            handoff.submission(self.root, self.packet, [self.output, "reviews/b.json"])
        with self.assertRaises(HandoffFailure):
            handoff.submission(self.root, self.packet, [self.output, self.output])
        padded = self.raw + b" " * (review_results.REVIEW_RESULTS_INPUT_LIMIT - len(self.raw))
        self.save(padded, "reviews/full.json")
        with self.assertRaises(ReviewEvidenceError):
            handoff.submission(self.root, self.packet, ["reviews/full.json"])


HandoffFailure = handoff.HandoffError


def stat_mode_regular():
    import stat
    return stat.S_IFREG | 0o600


class InstalledReviewHandoffTests(unittest.TestCase):
    def test_installed_save_submit_public_state_replay_and_stale_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve(), git_managed=True)
            root = install.project_root
            with (root / ".gitignore").open("a", encoding="utf-8") as ignored:
                ignored.write("/reviews/\n")
            (root / "reviews").mkdir()
            def cli(*args):
                result = install.run(*args, "--json")
                self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
                return json.loads(result.stdout)["data"]
            cli("setup")
            task = cli("task", "add", "--title", "Installed handoff", "--review-tier", "2")["task"]["task_id"]
            cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
            packet = cli("review", "prepare", task)
            (root / "reviews/packet.json").write_bytes(encode(packet))
            helper = install.skill_root / "scripts/review_handoff.py"
            def invoke(operation, *args, raw=None):
                return subprocess.run([sys.executable, "-I", "-S", str(helper), operation,
                    "--repo", str(root), "--packet", "reviews/packet.json", *args],
                    input=raw, capture_output=True, check=False, cwd=root)
            originals = []
            for suffix in ("a", "b"):
                payload = copy.deepcopy(packet["result_template"])
                payload["receipts"] = [receipt("reviewer-" + suffix, findings=[{"severity": "low", "summary": "example.py:1 日本語 🚀"}])]
                raw = b"\n" + encode(payload) + b"\n"
                originals.append(raw)
                result = invoke("save", "--output", f"reviews/{suffix}.json", raw=raw)
                self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
                self.assertEqual(json.loads(result.stdout)["finding_count"], 1)
                self.assertNotIn(b"provenance", result.stdout)
            result = invoke("submit", "reviews/a.json", "reviews/b.json")
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            rows = json.loads(result.stdout)["data"]["receipts"]
            self.assertEqual([row["findings"][0]["finding"]["severity"] for row in rows], ["low", "low"])
            # Lost response: recover public state, without an automatic retry.
            observed = cli("task", "show", task)["review_evidence"]
            self.assertEqual(observed["counts"]["receipts_current_generation"], 2)
            replay = invoke("submit", "reviews/a.json", "reviews/b.json")
            self.assertNotEqual(replay.returncode, 0)
            self.assertEqual(cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 2)
            cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
            stale = invoke("submit", "reviews/a.json", "reviews/b.json")
            self.assertNotEqual(stale.returncode, 0)
            self.assertEqual(cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 0)
            for suffix, raw in zip(("a", "b"), originals):
                self.assertEqual((root / f"reviews/{suffix}.json").read_bytes(), raw)

    def test_installed_submit_preserves_option_looking_approval_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve(), git_managed=True)
            root = install.project_root
            with (root / ".gitignore").open("a", encoding="utf-8") as target:
                target.write("/reviews/\n")
            (root / "reviews").mkdir()
            def cli(*args):
                result = install.run(*args, "--json")
                self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
                return json.loads(result.stdout)["data"]
            cli("setup")
            task = cli("task", "add", "--title", "Explicit approval transport", "--review-tier", "2")["task"]["task_id"]
            cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
            packet = cli("review", "prepare", task)
            (root / "reviews/packet.json").write_bytes(encode(packet))
            helper = install.skill_root / "scripts/review_handoff.py"
            for reviewer in ("-reviewer-a", "--reviewer-b"):
                payload = copy.deepcopy(packet["result_template"])
                payload["receipts"] = [receipt(reviewer, kind="self_review_fallback")]
                raw = encode(payload)
                path = "reviews/" + reviewer + ".json"
                for operation, args, input_bytes in (
                    ("save", ["--output", path], raw), ("submit", [path], None),
                ):
                    result = subprocess.run([sys.executable, "-I", "-S", str(helper), operation,
                        "--repo", str(root), "--packet", "reviews/packet.json",
                        "--user-approved-reviewer=" + reviewer, *args],
                        input=input_bytes, capture_output=True, check=False, cwd=root)
                    self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
                registered = json.loads(result.stdout)["data"]["receipts"][0]["receipt"]
                self.assertEqual(registered["reviewer_key"], reviewer)
                self.assertEqual(registered["user_approved"], 1)
                self.assertEqual((root / path).read_bytes(), raw)
