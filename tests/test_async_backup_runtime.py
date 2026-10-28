"""Bounded launcher/observer behavior on isolated canonical project state."""

from __future__ import annotations

import io
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, closing, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from tests.test_routine_backup import timestamp
from tests.m14_test_support import tree_snapshot
from task_governance_tool import async_backup as runtime
from task_governance_tool import async_backup_capture as capture
from task_governance_tool import async_backup_repository as repository
from task_governance_tool import async_backup_state as state
from task_governance_tool.artifact_lock import zero_wait_artifact_lock
from task_governance_tool.storage import StorageError


class _Pipe:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, value):
        self.data += value
        return len(value)

    def close(self):
        self.closed = True


class _Child:
    def __init__(self, *, stoppable=True):
        self.stdin = _Pipe()
        self.returncode = None
        self.stoppable = stoppable
        self.stops = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.stops.append("terminate")
        if not self.stoppable:
            raise OSError("controlled termination failure")
        self.returncode = 1

    def kill(self):
        self.stops.append("kill")
        if not self.stoppable:
            raise OSError("controlled termination failure")
        self.returncode = 1

    def wait(self, *, timeout):
        if self.returncode is None:
            raise TimeoutError("controlled still-running child")
        return self.returncode


class AsyncBackupRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.install, self.target, _ = _setup_current(self.directory)

    def record(self, *, nonce="a" * 32, status="starting"):
        control = repository.read_control(self.target)
        return dict(v=1, attempt_id=nonce, state=status, started_at=timestamp(0),
                    captured_at=None, published_at=None, generation_id=None,
                    project_id=self.target.project.project_id,
                    binding_path_hash=self.target.binding_path_hash,
                    binding_generation=self.target.binding_generation,
                    schema_version=control.schema_version,
                    database_identity=list(control.database_identity))

    def prepare(self, **kwargs):
        root = state.runtime_root(self.target, create=True)
        record = self.record(**kwargs)
        state.write_record(root, record)
        return root, record

    def test_absent_observation_is_idle_and_creates_no_runtime_or_lock(self):
        before = tree_snapshot(self.target.db_path.parent)
        self.assertEqual(state.observe_runtime(self.target)["state"], "idle")
        self.assertEqual(tree_snapshot(self.target.db_path.parent), before)
        self.assertFalse(state.runtime_root(self.target).exists())

    def test_saved_running_is_interrupted_and_observation_does_not_repair_it(self):
        root, record = self.prepare(status="running")
        before = tree_snapshot(root)
        self.assertEqual(state.observe_runtime(self.target)["state"], "interrupted")
        self.assertEqual(state.read_record(root), record)
        self.assertEqual(tree_snapshot(root), before)

    def test_live_observer_closes_before_waiting_writer_replaces_state(self):
        root, record = self.prepare()
        identity = (root / "state.json").stat()
        opened, release = threading.Event(), threading.Event()
        reader_results, writer_errors = [], []
        writer_done, replacing = threading.Event(), threading.Event()
        original_read = os.read
        original_replace = os.replace

        def held_read(descriptor, size):
            details = os.fstat(descriptor)
            if ((details.st_dev, details.st_ino) == (identity.st_dev, identity.st_ino)
                    and not opened.is_set()):
                opened.set()
                if not release.wait(5):
                    raise TimeoutError("isolated observer barrier timed out")
            return original_read(descriptor, size)

        def observe():
            try:
                reader_results.append(state.read_record(root))
            except (state.StatePathError, StorageError):
                reader_results.append("changed")

        def publish():
            try:
                state.write_record(root, dict(record, state="running"))
            except Exception as error:
                writer_errors.append(error)
            finally:
                writer_done.set()

        def replace_after_reader(*args):
            replacing.set()
            self.assertTrue(release.is_set())
            return original_replace(*args)

        with mock.patch.object(os, "read", side_effect=held_read), mock.patch.object(
                os, "replace", side_effect=replace_after_reader):
            reader = threading.Thread(target=observe)
            reader.start()
            writer = threading.Thread(target=publish)
            try:
                self.assertTrue(opened.wait(5), "observer did not open the actual state reader")
                writer.start()
                self.assertFalse(writer_done.wait(.05), "writer bypassed the reader's state lock")
                self.assertFalse(replacing.is_set())
                self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
            finally:
                release.set()
                reader.join(5)
                if writer.ident is not None:
                    writer.join(5)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual(writer_errors, [])
        self.assertEqual(reader_results, [record])
        self.assertTrue(replacing.is_set())
        self.assertEqual(state.read_record(root)["state"], "running")

    def test_missing_or_contended_state_lock_observation_never_creates_or_repairs(self):
        root, record = self.prepare()
        before = tree_snapshot(root)
        with zero_wait_artifact_lock(root / "state.lock", require_single_link=True):
            self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        self.assertEqual(tree_snapshot(root), before)
        (root / "state.lock").unlink()  # An unreleased local format, only in this fixture.
        before = tree_snapshot(root)
        self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        self.assertEqual(tree_snapshot(root), before)
        self.assertFalse((root / "state.lock").exists())
        state.write_record(root, dict(record, state="running"))
        self.assertTrue((root / "state.lock").is_file())

    def test_state_lock_timeout_does_not_publish_or_change_fixed_temporary(self):
        root, record = self.prepare()
        before = tree_snapshot(root)
        with zero_wait_artifact_lock(root / "state.lock", require_single_link=True):
            with self.assertRaises(StorageError):
                state.write_record(root, dict(record, state="running"), deadline=time.monotonic() + .02)
            with self.assertRaises(StorageError):
                state.read_record(root, deadline=time.monotonic() + .02)
        self.assertEqual(tree_snapshot(root), before)

    def test_held_worker_skips_database_and_spawn_and_is_observed_running(self):
        root, _ = self.prepare(status="running")
        with zero_wait_artifact_lock(root / "worker.lock", require_single_link=True):
            with mock.patch.object(repository, "read_control") as read, mock.patch.object(runtime, "_spawn") as spawn:
                self.assertEqual(runtime.launch_backup(self.target).code, "running")
                read.assert_not_called()
                spawn.assert_not_called()
            self.assertEqual(state.observe_runtime(self.target)["state"], "running")

    def test_launch_contention_does_not_create_second_attempt(self):
        root, record = self.prepare()
        with zero_wait_artifact_lock(root / "launch.lock", require_single_link=True):
            with mock.patch.object(runtime, "_spawn") as spawn:
                self.assertEqual(runtime.launch_backup(self.target).code, "running")
                spawn.assert_not_called()
                self.assertEqual(state.read_record(root), record)

    def test_disabled_not_due_and_noncanonical_opportunities_do_not_spawn(self):
        control = repository.read_control(self.target)
        disabled = replace(control, maintenance=replace(
            control.maintenance, enabled_at=None, backup_interval_minutes=None,
            backup_generations=None))
        current = replace(control, maintenance=replace(
            control.maintenance, latest_backup_generation_id="tg_backup_" + "1" * 32,
            backup_last_success_at=timestamp(0), applied_backup_generations=3,
            backup_last_outcome_code="succeeded", backup_last_outcome_at=timestamp(0)))
        for value, expected in ((disabled, "not_opted_in"), (current, "current")):
            with self.subTest(expected=expected), mock.patch.object(repository, "read_control", return_value=value), mock.patch.object(runtime, "_spawn") as spawn:
                self.assertEqual(runtime.launch_backup(self.target, observed_at=timestamp(1)).code, expected)
                spawn.assert_not_called()
                self.assertFalse(state.runtime_root(self.target).exists())
        with mock.patch.object(repository, "read_control") as read:
            self.assertEqual(runtime.launch_backup(replace(self.target, canonical_fixed=False)).code, "not_opted_in")
            read.assert_not_called()

    def test_handshake_sends_one_go_and_returns_without_waiting_for_completion(self):
        child = _Child()

        def spawn(target, nonce):
            root = state.runtime_root(target)
            record = state.read_record(root)
            self.assertEqual(record["attempt_id"], nonce)
            self.assertEqual(record["state"], "starting")
            state.write_record(root, dict(record, state="running"))
            return child

        result = runtime.launch_backup(self.target, _spawn_child=spawn)
        self.assertEqual(result.code, "started")
        self.assertEqual(child.stdin.data, b"G")
        self.assertTrue(child.stdin.closed)
        self.assertEqual(child.stops, [])
        self.assertIsNone(child.returncode)
        self.assertEqual(state.read_record(state.runtime_root(self.target))["state"], "running")

    def test_handshake_rereads_one_uncertain_observation_without_repeating_go(self):
        child = _Child()
        original = runtime._same_attempt
        observations = []

        def spawn(target, nonce):
            root = state.runtime_root(target)
            state.write_record(root, dict(state.read_record(root), state="running"))
            return child

        def observe(root, nonce, **kwargs):
            observations.append(nonce)
            if len(observations) == 1:
                raise StorageError("backup_runtime_unavailable", "controlled crossing observation")
            return original(root, nonce, **kwargs)

        with mock.patch.object(runtime, "_same_attempt", side_effect=observe):
            self.assertEqual(runtime.launch_backup(self.target, _spawn_child=spawn).code, "started")
        self.assertEqual(len(observations), 2)
        self.assertEqual(child.stdin.data, b"G")
        self.assertEqual(child.stops, [])

    def test_handshake_missing_observation_is_uncertain_not_a_changed_nonce(self):
        child = _Child()
        original = runtime.read_record
        deadlines = []

        def spawn(target, nonce):
            root = state.runtime_root(target)
            state.write_record(root, dict(state.read_record(root), state="running"))
            return child

        def observe(root, *, deadline=None):
            deadlines.append(deadline)
            if len(deadlines) == 1:
                return None
            return original(root, deadline=deadline)

        with mock.patch.object(runtime, "read_record", side_effect=observe):
            self.assertEqual(runtime.launch_backup(self.target, _spawn_child=spawn).code, "started")
        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines[0], deadlines[1])
        self.assertEqual(child.stdin.data, b"G")
        self.assertEqual(child.stops, [])

    def test_startup_lock_wait_uses_existing_deadline_and_never_sends_late_go(self):
        child = _Child()
        held, release = threading.Event(), threading.Event()
        holder = None

        def spawn(target, nonce):
            nonlocal holder
            root = state.runtime_root(target)
            state.write_record(root, dict(state.read_record(root), state="running"))

            def hold():
                with zero_wait_artifact_lock(root / "state.lock", require_single_link=True):
                    held.set()
                    release.wait(2)

            holder = threading.Thread(target=hold)
            holder.start()
            self.assertTrue(held.wait(2))
            return child

        # Releasing during owned-child cleanup lets its terminal observation
        # finish. It must not extend the earlier startup deadline or send GO.
        original_stop = runtime._stop_unstarted
        def stop(process):
            release.set()
            return original_stop(process)

        try:
            with mock.patch.object(runtime, "_stop_unstarted", side_effect=stop):
                self.assertEqual(runtime.launch_backup(self.target, _spawn_child=spawn,
                                                       _startup_seconds=.03).code, "failed")
        finally:
            release.set()
            if holder is not None:
                holder.join(3)
        self.assertEqual(child.stdin.data, b"")
        self.assertEqual(child.stops, ["terminate"])

    def test_handshake_nonce_change_is_rejected_without_go_or_overwriting_successor(self):
        child = _Child()

        def spawn(target, nonce):
            root = state.runtime_root(target)
            current = state.read_record(root)
            state.write_record(root, dict(current, attempt_id="b" * 32, state="running"))
            return child

        self.assertEqual(runtime.launch_backup(self.target, _spawn_child=spawn).code, "failed")
        self.assertEqual(child.stdin.data, b"")
        self.assertEqual(child.stops, ["terminate"])
        self.assertEqual(state.read_record(state.runtime_root(self.target))["attempt_id"], "b" * 32)

    def test_permanently_unknown_handshake_stops_owned_child_at_deadline(self):
        child = _Child()
        with mock.patch.object(runtime, "_same_attempt", side_effect=StorageError(
                "backup_runtime_unavailable", "controlled permanent uncertainty")):
            self.assertEqual(runtime.launch_backup(self.target, _spawn_child=lambda *_: child,
                                                   _startup_seconds=.05).code, "failed")
        self.assertEqual(child.stdin.data, b"")
        self.assertEqual(child.stops, ["terminate"])
        self.assertTrue(child.stdin.closed)

    def test_spawn_failure_and_timeout_keep_generation_metadata_unchanged(self):
        before = repository.read_control(self.target)
        with mock.patch.object(runtime, "_spawn", side_effect=OSError("controlled spawn failure")):
            self.assertEqual(runtime.launch_backup(self.target).code, "failed")
        child = _Child()
        self.assertEqual(runtime.launch_backup(self.target, _spawn_child=lambda *_: child,
                                               _startup_seconds=0).code, "failed")
        self.assertEqual(child.stops, ["terminate"])
        self.assertEqual(child.stdin.data, b"")
        self.assertTrue(child.stdin.closed)
        self.assertEqual(repository.read_control(self.target), before)

    def test_unknown_child_termination_prevents_retry_spawn_and_can_observe_exit(self):
        child = _Child(stoppable=False)
        result = runtime.launch_backup(self.target, _spawn_child=lambda *_: child, _startup_seconds=0)
        self.assertEqual(result.code, "unknown")
        root = state.runtime_root(self.target)
        before = tree_snapshot(root)
        self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        self.assertEqual(tree_snapshot(root), before)
        with mock.patch.object(runtime, "_spawn") as spawn:
            self.assertEqual(runtime.launch_backup(self.target).code, "unknown")
            spawn.assert_not_called()
        # The same fenced child can later save its EOF/exit outcome. Merely
        # waiting or changing a PID is deliberately not an exit certificate.
        record = state.read_record(root)
        state.write_record(root, dict(record, state="failed"))
        self.assertFalse(state.uncertain_launch(root, clear_finished=True))
        self.assertFalse((root / ".launch-uncertain").exists())

    def test_unknown_live_child_holding_state_lock_cannot_prevent_launch_barrier(self):
        root = state.runtime_root(self.target, create=True)
        entered = self.directory / "child-state-lock-held"
        children, nonces = [], []
        child_source = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool import async_backup_state as state
with state._state_guard(Path(sys.argv[2])):
    Path(sys.argv[3]).write_text("held", encoding="ascii")
    sys.stdin.buffer.read(1)
'''

        def spawn(target, nonce):
            nonces.append(nonce)
            child = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", child_source,
                 str(self.install.skill_root / "scripts"), str(root), str(entered)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
            )
            children.append(child)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if entered.exists() and entered.stat().st_size > 0:
                    return child
                if child.poll() is not None:
                    self.fail("isolated child exited before holding the state lock")
                time.sleep(.01)
            self.fail("isolated child did not hold the state lock")

        try:
            # Only the exit-confirmation result is controlled. The child and
            # state lock are real, and remain alive after the parent returns.
            with mock.patch.object(runtime, "_stop_unstarted", return_value=False):
                result = runtime.launch_backup(self.target, _spawn_child=spawn,
                                               _startup_seconds=.03)
            self.assertEqual(result.code, "unknown")
            self.assertIsNone(children[0].poll())
            marker = root / ".launch-uncertain"
            self.assertEqual(marker.read_text(encoding="ascii"), nonces[0])
            with mock.patch.object(runtime, "_spawn") as next_spawn:
                self.assertNotEqual(runtime.launch_backup(self.target).code, "started")
                next_spawn.assert_not_called()
            self.assertEqual(marker.read_text(encoding="ascii"), nonces[0])
        finally:
            for child in children:
                child.stdin.close()  # EOF releases only this fixture child.
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        # Released ownership alone cannot erase an unknown launch: the saved
        # attempt is still starting, so the matching terminal proof is absent.
        with mock.patch.object(runtime, "_spawn") as next_spawn:
            self.assertEqual(runtime.launch_backup(self.target).code, "unknown")
            next_spawn.assert_not_called()
        self.assertEqual(marker.read_text(encoding="ascii"), nonces[0])

    def test_eof_before_go_and_old_nonce_have_no_backup_effect(self):
        root, record = self.prepare()
        before = repository.read_control(self.target)
        with mock.patch.object(capture, "run_backup") as run:
            self.assertEqual(runtime.worker_main(self.install.project_root, "b" * 32,
                skill_root=self.install.skill_root, go_stream=io.BytesIO(b"G")), 2)
            self.assertEqual(state.read_record(root), record)
            self.assertEqual(runtime.worker_main(self.install.project_root, record["attempt_id"],
                skill_root=self.install.skill_root, go_stream=io.BytesIO()), 2)
            run.assert_not_called()
        self.assertEqual(state.read_record(root)["state"], "failed")
        self.assertEqual(repository.read_control(self.target), before)
        self.assertEqual(list(self.target.resolved_backups_path.glob("*.sqlite")), [])

    def test_database_replacement_identity_before_go_rejects_effect(self):
        root, record = self.prepare()
        control = repository.read_control(self.target)
        changed = replace(control, database_identity=(control.database_identity[0], control.database_identity[1] + 1))
        errors = []

        def tracked(name, original):
            def call(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                except Exception as error:
                    errors.append((name, type(error).__name__, getattr(error, "code", None),
                                   getattr(error, "winerror", None)))
                    raise
            return call

        original_lock = runtime.zero_wait_artifact_lock

        @contextmanager
        def tracked_lock(*args, **kwargs):
            try:
                with original_lock(*args, **kwargs) as owned:
                    yield owned
            except Exception as error:
                errors.append(("worker_lock", type(error).__name__, getattr(error, "code", None),
                               getattr(error, "winerror", None)))
                raise

        with ExitStack() as stack:
            for name in ("_same_attempt", "backup_runtime_target", "runtime_root", "write_record"):
                stack.enter_context(mock.patch.object(runtime, name, tracked(name, getattr(runtime, name))))
            stack.enter_context(mock.patch.object(runtime, "zero_wait_artifact_lock", tracked_lock))
            read = stack.enter_context(mock.patch.object(repository, "read_control", return_value=changed))
            run = stack.enter_context(mock.patch.object(capture, "run_backup"))
            self.assertEqual(runtime.worker_main(self.install.project_root, record["attempt_id"],
                skill_root=self.install.skill_root, go_stream=io.BytesIO(b"G")), 2)
            run.assert_not_called()
            self.assertEqual(read.call_count, 1, errors)
        self.assertEqual(state.read_record(root)["state"], "failed", errors)

    def test_busy_database_after_go_defers_without_effect_and_next_update_retries(self):
        root, record = self.prepare()
        with closing(repository.connect_existing(self.target.db_path)) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            try:
                with mock.patch.object(capture, "run_backup") as run:
                    self.assertEqual(runtime.worker_main(self.install.project_root, record["attempt_id"],
                        skill_root=self.install.skill_root, go_stream=io.BytesIO(b"G")), 2)
                    run.assert_not_called()
                self.assertEqual(state.read_record(root)["state"], "deferred")
            finally:
                writer.rollback()
        self.assertEqual(repository.read_control(self.target).generations, ())
        child = _Child()

        def spawn(target, nonce):
            current = state.read_record(root)
            self.assertEqual(current["attempt_id"], nonce)
            state.write_record(root, dict(current, state="running"))
            return child

        self.assertEqual(runtime.launch_backup(self.target, _spawn_child=spawn).code, "started")
        self.assertEqual(child.stdin.data, b"G")

    def test_observation_drift_and_malformed_state_remain_unknown_without_writes(self):
        root, record = self.prepare(status="running")
        with mock.patch.object(state, "read_record", side_effect=[record, dict(record, state="failed")]):
            self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        (root / "state.json").write_bytes(b'{"v":1,"v":1}')
        before = tree_snapshot(root)
        self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        self.assertEqual(tree_snapshot(root), before)

    def test_same_contents_database_replacement_and_schema_drift_are_unknown(self):
        root, record = self.prepare(status="running")
        before = tree_snapshot(root)
        self.assertEqual(state.observe_runtime(self.target, schema_version=record["schema_version"] + 1)["state"], "unknown")
        replacement = self.directory / "identical-database.sqlite"
        shutil.copyfile(self.target.db_path, replacement)
        os.replace(replacement, self.target.db_path)
        self.assertEqual(state.observe_runtime(self.target, schema_version=record["schema_version"])["state"], "unknown")
        self.assertEqual(tree_snapshot(root), before)

    def test_hardlinked_state_and_lock_are_rejected_without_touching_alias(self):
        root, record = self.prepare()
        alias = self.directory / "state-alias.json"
        try:
            os.link(root / "state.json", alias)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP}:
                raise
            self.skipTest("hardlink creation is unavailable on this host")
        before = alias.read_bytes()
        self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        with self.assertRaises((StorageError, state.StatePathError, OSError)):
            state.write_record(root, record)
        self.assertEqual(alias.read_bytes(), before)
        alias.unlink()
        state_lock_alias = self.directory / "state-lock-alias"
        os.link(root / "state.lock", state_lock_alias)
        self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        with self.assertRaises((StorageError, state.StatePathError, OSError)):
            state.write_record(root, record)
        self.assertEqual(state_lock_alias.read_bytes(), b"\0")
        state_lock_alias.unlink()
        lock = root / "worker.lock"
        lock.write_bytes(b"\0")
        lock_alias = self.directory / "lock-alias"
        os.link(lock, lock_alias)
        self.assertIsNone(state.lock_owned(lock))
        self.assertEqual(lock_alias.read_bytes(), b"\0")

    def test_symlink_runtime_is_rejected_without_touching_destination(self):
        root = state.runtime_root(self.target)
        root.parent.mkdir(exist_ok=True)
        destination = self.directory / "outside-runtime"
        destination.mkdir()
        (destination / "sentinel").write_bytes(b"unchanged")
        try:
            root.symlink_to(destination, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink creation is unavailable on this host")
        before = tree_snapshot(destination)
        with mock.patch.object(runtime, "_spawn") as spawn:
            self.assertEqual(runtime.launch_backup(self.target).code, "failed")
            spawn.assert_not_called()
        self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        self.assertEqual(tree_snapshot(destination), before)


if __name__ == "__main__":
    unittest.main()
