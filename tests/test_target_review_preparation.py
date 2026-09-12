"""Target-bound read-only preparation after a committed target operation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_review_packet import (
    FINGERPRINT_A, FINGERPRINT_B, database_target,
)
from tests.m14_test_support import initialize_taskgov_internal, run_taskgov_internal
from task_governance_tool import cli, review_packet, verification_runner_service as service


class TargetReviewPreparationTests(unittest.TestCase):
    def invoke(self, db, repo, *args, maintenance=False):
        result = run_taskgov_internal(*args, "--repo", str(repo), "--db", str(db),
                                     "--json", maintenance_enabled=maintenance)
        return result, json.loads(result.stdout)

    def create(self, root, verification=""):
        db, repo = root / "state.sqlite", root / "repo"
        initialize_taskgov_internal(repo=repo, db=db)
        result, payload = self.invoke(db, repo, "task", "add", "--title", "Packet connection",
                                     "--status", "in_progress", "--verification", verification,
                                     "--contract-scope", "Prepare exactly this target",
                                     "--contract-acceptance", "Bound read-only Packet",
                                     "--contract-authority-ref", "user:packet-preparation")
        self.assertEqual(result.returncode, 0, result.stdout)
        return db, repo, payload["data"]["task"]["task_id"]

    def target(self, db, repo, task_id, **kwargs):
        result, payload = self.invoke(db, repo, "review", "target", "set", task_id,
                                     "--kind", "diff_fingerprint", "--revision", FINGERPRINT_A,
                                     **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(payload["ok"])
        return payload

    def test_success_replaces_prepare_and_retry_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, repo, task_id = self.create(Path(tmp))
            with mock.patch.object(cli, "run_post_commit_maintenance", return_value=[]) as maintenance:
                payload = self.target(db, repo, task_id, maintenance=True)
            self.assertEqual(maintenance.call_count, 1)
            prepared = payload["data"]["review_preparation"]
            self.assertEqual(prepared["status"], "ready")
            self.assertEqual(prepared["errors"], [])
            self.assertEqual(prepared["packet"]["review_target"]["generation"], 1)
            before = db.read_bytes()
            with mock.patch.object(cli, "set_review_target_with_optional_runner",
                                   side_effect=AssertionError("retry cannot set or launch")), \
                 mock.patch.object(cli, "run_post_commit_maintenance",
                                   side_effect=AssertionError("retry cannot maintain")):
                result, retry = self.invoke(db, repo, "review", "prepare", task_id,
                                            "--expected-binding", prepared["binding"],
                                            "--read-only", maintenance=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(retry["data"], prepared["packet"])
            self.assertEqual(db.read_bytes(), before)
            # One response now contains both operations; compare actual bytes
            # without claiming an overall token or time reduction.
            legacy = json.loads(json.dumps(payload))
            del legacy["data"]["review_preparation"]
            encode = lambda value: (json.dumps(value, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")) + "\n").encode("utf-8")
            old_bytes = len(encode(legacy)) + len(encode(retry))
            new_bytes = len(encode(payload))
            self.assertEqual(json.loads(encode(payload))["data"]["review_preparation"]["packet"], retry["data"])
            print(f"Target preparation: calls 2 -> 1; UTF-8 bytes {old_bytes} -> {new_bytes}")

    def test_receipt_required_does_not_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, repo, task_id = self.create(Path(tmp), "Focused checks")
            with mock.patch.object(cli, "handle_review_prepare", side_effect=AssertionError("unexpected prepare")):
                payload = self.target(db, repo, task_id)
            self.assertEqual(payload["data"]["verification_route"], "receipt_required")
            self.assertEqual(payload["data"]["review_preparation"],
                             {"status": "not_applicable", "binding": None, "packet": None, "errors": []})

    def test_persisted_runner_pass_and_blocked_routes(self):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import _launch, _persist_terminal

        for branch in ("pass", "blocking"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as tmp:
                fixture = RunnerServiceFixture(Path(tmp))
                _prepared, intent = _launch(fixture)
                observation = _persist_terminal(fixture, intent, branch=branch)
                routed = service._routed_runner_target(intent.review, intent.resolution, observation)
                with mock.patch.object(cli, "set_review_target_with_optional_runner", return_value=routed), \
                     mock.patch.object(cli, "handle_review_prepare", wraps=cli.handle_review_prepare) as prepare:
                    result, payload = self.invoke(fixture.db, fixture.repo, "review", "target", "set",
                                                   fixture.task_id, "--kind", "git_snapshot")
                self.assertEqual(result.returncode, 0, result.stdout)
                preparation = payload["data"]["review_preparation"]
                self.assertEqual(prepare.call_count, 1 if branch == "pass" else 0)
                self.assertEqual(preparation["status"], "ready" if branch == "pass" else "not_applicable")
                if branch == "pass":
                    self.assertEqual(preparation["packet"]["review_target"]["value"], routed.task["review_target_value"])

    def test_packet_limit_failure_is_partial_success_with_one_maintenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, repo, task_id = self.create(Path(tmp))
            with mock.patch.object(cli, "REVIEW_PACKET_MAX_BYTES", 1), \
                 mock.patch.object(cli, "run_post_commit_maintenance", return_value=[]) as maintenance:
                payload = self.target(db, repo, task_id, maintenance=True)
            self.assertEqual(maintenance.call_count, 1)
            preparation = payload["data"]["review_preparation"]
            self.assertEqual(preparation["status"], "failed")
            self.assertEqual(preparation["errors"][0]["code"], "review_packet_too_large")
            self.assertEqual(payload["data"]["task"]["review_target_generation"], 1)

    def test_task_contract_and_target_drift_fail_before_first_read_and_during_prepare(self):
        for phase in ("before", "during"):
            for change in ("task", "contract", "target"):
                with self.subTest(phase=phase, change=change), tempfile.TemporaryDirectory() as tmp:
                    db, repo, task_id = self.create(Path(tmp))
                    target = database_target(db, repo)
                    original_prepare = cli.prepare_review_packet
                    original_observe = review_packet._observe_target

                    def mutate():
                        if change == "target":
                            service.set_review_target_with_optional_runner(
                                target, task_id, kind="diff_fingerprint", revision=FINGERPRINT_B)
                        else:
                            args = ("--priority", "high") if change == "task" else (
                                "--contract-scope", "Revised scope", "--contract-acceptance", "Revised acceptance",
                                "--contract-authority-ref", "user:revision", "--contract-change-reason", "Explicit revision")
                            result, _ = self.invoke(db, repo, "task", "edit", task_id, *args)
                            self.assertEqual(result.returncode, 0, result.stdout)

                    def before(*args, **kwargs):
                        mutate()
                        return original_prepare(*args, **kwargs)

                    def during(*args, **kwargs):
                        mutate()
                        return original_observe(*args, **kwargs)

                    patch = mock.patch.object(cli, "prepare_review_packet", side_effect=before) if phase == "before" else \
                        mock.patch.object(review_packet, "_observe_target", side_effect=during)
                    with patch:
                        payload = self.target(db, repo, task_id)
                    prepared = payload["data"]["review_preparation"]
                    self.assertEqual(prepared["status"], "failed")
                    self.assertIsNone(prepared["packet"])
                    self.assertEqual(prepared["errors"][0]["code"], "review_packet_stale")
                    result, retry = self.invoke(db, repo, "review", "prepare", task_id,
                                                "--expected-binding", prepared["binding"])
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(retry["errors"][0]["code"], "review_packet_stale")

    def test_preparation_failure_preserves_commit_and_lost_response_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, repo, task_id = self.create(Path(tmp))
            with mock.patch.object(cli, "prepare_review_packet", side_effect=RuntimeError("private detail")):
                payload = self.target(db, repo, task_id)
            self.assertEqual(payload["data"]["review_preparation"]["status"], "failed")
            self.assertNotIn("private detail", json.dumps(payload))
            # Simulate discarding the target response: inspect saved state, then
            # recover its exact current binding without another target write.
            before = db.read_bytes()
            result, audit = self.invoke(db, repo, "task", "show", task_id, "--audit")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(audit["data"]["task"]["review_target_generation"], 1)
            binding = audit["data"]["review_evidence"]["preparation_binding"]
            result, prepared = self.invoke(db, repo, "review", "prepare", task_id,
                                           "--expected-binding", binding, "--read-only")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(prepared["data"]["review_target"]["generation"], 1)
            self.assertEqual(db.read_bytes(), before)

    def test_read_only_target_rejection_and_invalid_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, repo, task_id = self.create(Path(tmp))
            before = db.read_bytes()
            result, _ = self.invoke(db, repo, "review", "target", "set", task_id, "--kind",
                                    "diff_fingerprint", "--revision", FINGERPRINT_A, "--read-only")
            self.assertEqual(result.returncode, 1)
            result, payload = self.invoke(db, repo, "review", "prepare", task_id, "--expected-binding", "invalid")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(payload["errors"][0]["code"], "invalid_review_evidence")
            self.assertEqual(db.read_bytes(), before)
