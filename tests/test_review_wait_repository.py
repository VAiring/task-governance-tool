"""Offline source-only repository tests; no installed state or real host calls."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tools.review_wait_controller import (
    Appointment,
    ControllerError,
    Reservation,
    ReviewWaitController,
    Reviewer,
    WaitBinding,
)
from tools.review_wait_repository import RepositoryError, ReviewWaitRepository
from tools import review_wait_repository as repository_module


ROOT = Path(__file__).absolute().parents[1]
NOW = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
RULE = "FREQ=DAILY;BYHOUR=0;BYMINUTE=10;BYSECOND=0;COUNT=1"


def make_controller(*, wait_id="wait-1", reviewers=None):
    binding = WaitBinding(
        wait_id=wait_id,
        project_id="project-1",
        project_path_hash="a" * 64,
        project_binding_generation=1,
        task_id="task-1",
        execution_id="execution-1",
        contract_revision=1,
        target_kind="working_tree",
        target_value="sha256:" + "b" * 64,
        target_base_revision="c" * 40,
        target_generation=1,
        artifact_manifest_id="manifest-1",
        parent_thread_id="parent-1",
        ownership_generation=1,
    )
    reservation = Reservation("timer-1", "parent-1", RULE, "UTC", "PAUSED", "d" * 64)
    return ReviewWaitController(binding, reviewers or (Reviewer("child-1", "turn-1"),), reservation)


def arm(controller):
    reservation = Reservation("timer-1", "parent-1", RULE, "UTC", "PAUSED", "d" * 64)
    return controller.arm(
        parent_thread_id="parent-1",
        binding=controller.binding,
        appointment=Appointment(NOW, NOW + timedelta(minutes=10), RULE, "UTC"),
        readback=reservation,
        now=NOW,
    )


class ReviewWaitRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="taskgov-wait-repository-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "wait.sqlite"

    def create(self):
        return ReviewWaitRepository.create(self.path, make_controller())

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(RepositoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(code, str(caught.exception))

    def assert_pending_blocks_replay(self, controller):
        self.assertIsNotNone(controller.pending)
        before = controller.to_snapshot()
        try:
            result = arm(controller)
        except ControllerError:
            pass
        else:
            self.assertIsNone(result)
        self.assertEqual(before, controller.to_snapshot())

    def test_explicit_create_roundtrip_and_schema_history(self):
        repo = self.create()
        stored = repo.read()
        self.assertEqual(1, stored.revision)
        self.assertEqual(make_controller().to_snapshot(), stored.controller.to_snapshot())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual((1,), connection.execute("PRAGMA user_version").fetchone())
            self.assertEqual([(1, "review-wait-source-v1")], connection.execute("SELECT * FROM schema_history").fetchall())
        self.assertEqual(stored.controller.to_snapshot(), ReviewWaitRepository.open_existing(self.path).read().controller.to_snapshot())

    def test_open_missing_does_not_create_anything(self):
        self.assert_code("state_unreadable", ReviewWaitRepository.open_existing, self.path)
        self.assertEqual([], list(self.path.parent.iterdir()))

    def test_create_rejects_existing_and_preserves_bytes(self):
        self.create()
        before = self.path.read_bytes()
        self.assert_code("state_exists", ReviewWaitRepository.create, self.path, make_controller())
        self.assertEqual(before, self.path.read_bytes())

    def test_missing_parent_and_relative_path_do_not_create(self):
        self.assert_code("state_path_invalid", ReviewWaitRepository.create, self.path.parent / "missing" / "state.sqlite", make_controller())
        self.assert_code("state_path_invalid", ReviewWaitRepository.create, Path("relative.sqlite"), make_controller())
        self.assertEqual([], list(self.path.parent.iterdir()))

    def test_pending_intent_is_durable_and_sqlite_transaction_ends_before_effect(self):
        repo = self.create()
        effects = []
        with repo.writer() as lease:
            stored = lease.read()
            intent = arm(stored.controller)
            self.assertIsNotNone(intent)
            saved = lease.save(stored.controller, expected_revision=stored.revision)
            self.assertEqual(2, saved.revision)
            # This independent SQLite writer would fail if save retained its transaction.
            with closing(sqlite3.connect(self.path, timeout=0)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            self.assertIsNotNone(repo.read().controller.pending)
            effects.append("fake-host-effect")
        self.assertEqual(["fake-host-effect"], effects)
        self.assert_pending_blocks_replay(ReviewWaitRepository.open_existing(self.path).read().controller)

    def test_stale_cas_and_binding_changes_are_rejected(self):
        repo = self.create()
        with repo.writer() as lease:
            current = lease.read()
            lease.save(current.controller, expected_revision=1)
            self.assert_code("revision_conflict", lease.save, current.controller, expected_revision=1)
            self.assert_code("revision_conflict", lease.save, current.controller, expected_revision=True)
            self.assert_code("binding_mismatch", lease.save, make_controller(wait_id="wait-other"), expected_revision=2)
            changed = make_controller(reviewers=(Reviewer("child-other", "turn-other"),))
            self.assert_code("binding_mismatch", lease.save, changed, expected_revision=2)
        self.assertEqual(2, repo.read().revision)

    def test_effect_cannot_be_saved_without_first_persisting_intent(self):
        repo = self.create()
        with repo.writer() as lease:
            stored = lease.read()
            intent = arm(stored.controller)
            stored.controller.settle(intent.operation_id, readback=intent.after, now=NOW, operation_finished=True)
            self.assert_code("state_transition_invalid", lease.save, stored.controller, expected_revision=1)
        self.assertEqual(1, repo.read().revision)

    def test_closed_state_and_recorded_terminal_facts_cannot_be_rolled_back(self):
        repo = self.create()
        with repo.writer() as lease:
            stored = lease.read()
            intent = arm(stored.controller)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            stored.controller.settle(intent.operation_id, readback=intent.after, now=NOW, operation_finished=True)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            active = stored.controller.to_snapshot()
            stored.controller.observe_terminal(binding=stored.controller.binding, reviewer=stored.controller.reviewers[0], status="completed")
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            self.assert_code("state_transition_invalid", lease.save, ReviewWaitController.from_snapshot(active), expected_revision=stored.revision)
            intent = stored.controller.cancel(parent_thread_id="parent-1", binding=stored.controller.binding, expected_arm=1, readback=stored.controller.reservation)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            stored.controller.settle(intent.operation_id, readback=intent.after, now=NOW, operation_finished=True)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            self.assertEqual("closed", stored.controller.phase)
            self.assert_code("state_transition_invalid", lease.save, make_controller(), expected_revision=stored.revision)

    def test_same_arm_shortening_attempt_and_timer_identity_cannot_be_reset(self):
        repo = self.create()
        with repo.writer() as lease:
            stored = lease.read()
            intent = arm(stored.controller)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            stored.controller.settle(intent.operation_id, readback=intent.after, now=NOW, operation_finished=True)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            stored.controller.observe_terminal(binding=stored.controller.binding, reviewer=stored.controller.reviewers[0], status="failed")
            intent = stored.controller.maybe_shorten(binding=stored.controller.binding, expected_arm=1, readback=stored.controller.reservation, now=NOW)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            stored.controller.settle(intent.operation_id, readback=intent.before, now=NOW, operation_finished=True)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            payload = stored.controller.to_snapshot()
            payload["shortening_attempted"] = False
            self.assert_code("state_transition_invalid", lease.save, ReviewWaitController.from_snapshot(payload), expected_revision=stored.revision)
            payload = stored.controller.to_snapshot()
            payload["reservation"]["timer_id"] = "other-timer"
            self.assert_code("binding_mismatch", lease.save, ReviewWaitController.from_snapshot(payload), expected_revision=stored.revision)

    def test_unresolved_shorten_cannot_be_replaced_by_valid_pause_snapshot(self):
        repo = self.create()
        with repo.writer() as lease:
            stored = lease.read()
            first = arm(stored.controller)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            stored.controller.settle(first.operation_id, readback=first.after, now=NOW, operation_finished=True)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            stored.controller.observe_terminal(binding=stored.controller.binding, reviewer=stored.controller.reviewers[0], status="completed")
            intent = stored.controller.maybe_shorten(binding=stored.controller.binding, expected_arm=1, readback=stored.controller.reservation, now=NOW)
            stored.controller.settle(intent.operation_id, readback=None, now=NOW)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            original = stored.controller.to_snapshot()
            for advance in (0, 1):
                with self.subTest(sequence_advance=advance):
                    payload = json.loads(json.dumps(original))
                    payload["phase"] = "closed"
                    payload["operation_sequence"] += advance
                    payload["pending"]["operation_id"] = f"wait-1:{payload['operation_sequence']}"
                    payload["pending"]["kind"] = "pause"
                    payload["pending"]["after"] = {**payload["reservation"], "status": "PAUSED"}
                    candidate = ReviewWaitController.from_snapshot(payload)
                    self.assertEqual("pause", candidate.pending.kind)
                    self.assert_code("state_transition_invalid", lease.save, candidate, expected_revision=stored.revision)
            self.assertEqual(original, lease.read().controller.to_snapshot())
            # Cancellation may latch without replacing the unresolved intent.
            stored.controller.cancel(parent_thread_id="parent-1", binding=stored.controller.binding, expected_arm=1, readback=None)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            self.assertEqual(intent, stored.controller.pending)
            self.assertEqual("closed", stored.controller.phase)
            # A separately saved confirmed settlement permits the next pause.
            stored.controller.settle(intent.operation_id, readback=intent.after, now=NOW, operation_finished=True)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            self.assertIsNone(stored.controller.pending)
            pause = stored.controller.cancel(parent_thread_id="parent-1", binding=stored.controller.binding, expected_arm=1, readback=stored.controller.reservation)
            saved = lease.save(stored.controller, expected_revision=stored.revision)
            self.assertEqual(pause, saved.controller.pending)

    def test_arm_settlement_and_next_pause_cannot_share_one_save(self):
        repo = self.create()
        with repo.writer() as lease:
            stored = lease.read()
            intent = arm(stored.controller)
            stored = lease.save(stored.controller, expected_revision=stored.revision)
            original = stored.controller.to_snapshot()
            stored.controller.settle(intent.operation_id, readback=intent.after, now=NOW, operation_finished=True)
            stored.controller.cancel(parent_thread_id="parent-1", binding=stored.controller.binding, expected_arm=1, readback=stored.controller.reservation)
            candidate = ReviewWaitController.from_snapshot(stored.controller.to_snapshot())
            self.assertEqual(1, candidate.arm_number)
            self.assertEqual("pause", candidate.pending.kind)
            self.assert_code("state_transition_invalid", lease.save, candidate, expected_revision=stored.revision)
            self.assertEqual(original, lease.read().controller.to_snapshot())

    def test_released_lease_and_wrong_thread_cannot_write(self):
        repo = self.create()
        errors = []
        with repo.writer() as lease:
            def other_thread():
                try:
                    lease.save(make_controller(), expected_revision=1)
                except RepositoryError as exc:
                    errors.append(exc.code)
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(["writer_required"], errors)
        self.assert_code("writer_required", lease.read)
        self.assert_code("writer_required", lease.save, make_controller(), expected_revision=1)

    def test_competing_thread_cannot_acquire_writer(self):
        repo = self.create()
        results = []
        with repo.writer():
            def contender():
                try:
                    with ReviewWaitRepository.open_existing(self.path).writer():
                        results.append("unexpected")
                except RepositoryError as exc:
                    results.append(exc.code)
            worker = threading.Thread(target=contender)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(["writer_busy"], results)
        with repo.writer() as lease:
            self.assertEqual(1, lease.read().revision)

    def test_competing_process_cannot_acquire_writer(self):
        repo = self.create()
        script = """from pathlib import Path
import sys
from tools.review_wait_repository import ReviewWaitRepository, RepositoryError
try:
    with ReviewWaitRepository.open_existing(Path(sys.argv[1])).writer():
        print('unexpected')
except RepositoryError as exc:
    print(exc.code)
"""
        with repo.writer():
            result = subprocess.run([sys.executable, "-B", "-c", script, str(self.path)], cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(0, result.returncode)
        self.assertEqual("writer_busy", result.stdout.strip())

    def test_process_crash_before_and_after_fake_host_effect_retains_pending(self):
        script = """from pathlib import Path
import os, sys
from tools.review_wait_repository import ReviewWaitRepository
from tests.test_review_wait_repository import arm
repo = ReviewWaitRepository.open_existing(Path(sys.argv[1]))
with repo.writer() as lease:
    stored = lease.read()
    arm(stored.controller)
    lease.save(stored.controller, expected_revision=stored.revision)
    if sys.argv[2] == 'after':
        with Path(sys.argv[3]).open('ab') as effect:
            effect.write(b'1')
            effect.flush()
            os.fsync(effect.fileno())
    os._exit(23)
"""
        for when in ("before", "after"):
            with self.subTest(when=when):
                path = self.path.with_name(when + ".sqlite")
                effect = self.path.with_name(when + ".effect")
                ReviewWaitRepository.create(path, make_controller())
                result = subprocess.run([sys.executable, "-B", "-c", script, str(path), when, str(effect)], cwd=ROOT, capture_output=True, timeout=15, check=False)
                self.assertEqual(23, result.returncode)
                repo = ReviewWaitRepository.open_existing(path)
                self.assertEqual(2, repo.read().revision)
                self.assert_pending_blocks_replay(repo.read().controller)
                with repo.writer() as lease:
                    self.assertIsNotNone(lease.read().controller.pending)
                self.assertEqual(b"1" if when == "after" else b"", effect.read_bytes() if effect.exists() else b"")

    def test_schema_and_history_changes_fail_closed_without_repair(self):
        for index, statement in enumerate(("PRAGMA user_version = 2", "DELETE FROM schema_history", "CREATE TABLE unrelated (value TEXT)", "DELETE FROM wait_snapshot")):
            with self.subTest(statement=statement):
                path = self.path.with_name(f"invalid-{index}.sqlite")
                ReviewWaitRepository.create(path, make_controller())
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(statement)
                before = hashlib.sha256(path.read_bytes()).digest()
                self.assert_code("state_unreadable", ReviewWaitRepository.open_existing, path)
                self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())

    def test_malformed_noncanonical_unknown_and_duplicate_snapshot_fields_are_rejected(self):
        repo = self.create()
        original = json.dumps(repo.read().controller.to_snapshot(), sort_keys=True, separators=(",", ":"))
        unknown = json.loads(original)
        unknown["_meta"] = {"private": "sentinel-private-value"}
        cases = ("{}", "not-json", json.dumps(unknown), "{\"version\":1," + original[1:], original + " ")
        for payload in cases:
            with self.subTest(payload_kind=payload[:8]):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute("UPDATE wait_snapshot SET payload = ?", (payload,))
                self.assert_code("state_unreadable", repo.read)

    def test_wal_sidecar_rejected_without_touching_it(self):
        repo = self.create()
        sidecar = Path(str(self.path) + "-wal")
        sidecar.write_bytes(b"private-sentinel")
        self.assert_code("unsupported_journal_mode", repo.read)
        self.assertEqual(b"private-sentinel", sidecar.read_bytes())

    def test_missing_lock_does_not_get_recreated(self):
        repo = self.create()
        repo.lock_path.unlink()
        self.assert_code("state_unreadable", ReviewWaitRepository.open_existing, self.path)
        self.assertFalse(repo.lock_path.exists())

    def test_invalid_snapshot_is_rejected_before_creating_files(self):
        controller = make_controller()
        invalid = controller.to_snapshot()
        invalid["_meta"] = {"private": "private-value-must-not-be-persisted"}
        with patch.object(controller, "to_snapshot", return_value=invalid):
            self.assert_code("invalid_snapshot", ReviewWaitRepository.create, self.path, controller)
        self.assertEqual([], list(self.path.parent.iterdir()))

    def test_lock_cleanup_failure_is_sanitized_and_descriptor_is_closed(self):
        repo = self.create()
        real_lock = repository_module._lock

        def fail_release(descriptor, *, release=False):
            if release:
                raise OSError("private-os-details")
            real_lock(descriptor)

        with patch.object(repository_module, "_lock", side_effect=fail_release):
            with self.assertRaises(RepositoryError) as caught:
                with repo.writer():
                    pass
        self.assertEqual("state_unreadable", str(caught.exception))
        with repo.writer() as lease:
            self.assertEqual(1, lease.read().revision)

    def test_hardlinked_database_is_rejected(self):
        self.create()
        alias = self.path.with_name("hardlink.sqlite")
        os.link(self.path, alias)
        self.assert_code("state_unreadable", ReviewWaitRepository.open_existing, alias)
        self.assert_code("state_unreadable", ReviewWaitRepository.open_existing, self.path)

    def test_symlink_database_is_rejected(self):
        repo = self.create()
        alias = self.path.with_name("alias.sqlite")
        try:
            alias.symlink_to(self.path)
        except OSError:
            self.skipTest("OS does not allow this test to create a symlink")
        self.assert_code("state_unreadable", ReviewWaitRepository.open_existing, alias)
        self.assertEqual(1, repo.read().revision)


if __name__ == "__main__":
    unittest.main()
