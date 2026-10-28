"""Service-owned opportunity lifecycle; idle observation never opens SQLite."""
from __future__ import annotations

import threading
import time
import tempfile
import queue
import os
import sqlite3
import subprocess
import sys
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from task_governance_tool.artifact_lock import ArtifactLockError
from task_governance_tool.async_backup_service import BackupServiceObserver


class AsyncBackupServiceTests(unittest.TestCase):
    def setUp(self):
        from task_governance_tool import async_backup_opportunity as opportunity
        self.opportunity = opportunity
        self.target = object()
        self.compatibility = opportunity.current_compatibility()
        self.active = threading.Event()
        self.released = threading.Event()
        self.lease_calls = 0
        self.owner = "a" * 32
        self.patchers = []

        @contextmanager
        def lease(target):
            self.assertIs(target, self.target)
            self.lease_calls += 1
            self.released.clear()
            try:
                yield self.owner
            finally:
                self.released.set()

        def activate(*args):
            self.active.set()
            return None

        self.lease = self.patch("service_lease", side_effect=lease)
        self.activate = self.patch("activate_service", side_effect=activate)
        self.observe = self.patch("observe_opportunity", return_value=None)
        self.launch = mock.Mock(return_value=SimpleNamespace(code="started"))
        self.compatible = mock.Mock(return_value=True)
        self.admit_owner = mock.Mock(return_value=True)

    def patch(self, name, **kwargs):
        patcher = mock.patch.object(self.opportunity, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def start(self, **kwargs):
        observer = BackupServiceObserver(
            self.target, compatibility=self.compatibility,
            compatible=self.compatible, launch=kwargs.pop("launch", self.launch),
            admit_owner=kwargs.pop("admit_owner", self.admit_owner),
            _poll_seconds=.01, **kwargs)
        self.addCleanup(observer.close)
        self.assertTrue(observer.start())
        self.assertTrue(self.active.wait(2), "observer did not activate")
        return observer

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate(), "observer boundary was not reached")

    def test_idle_checks_only_opportunity_without_database_or_package_hash(self):
        with mock.patch("sqlite3.connect", side_effect=AssertionError("idle DB access")):
            observer = self.start()
            self.wait_for(lambda: self.observe.call_count >= 5)
            self.assertTrue(observer.close())
        self.assertEqual(self.lease_calls, 1)
        self.activate.assert_called_once_with(self.target, self.owner, self.compatibility)
        self.launch.assert_not_called()
        self.compatible.assert_not_called()
        self.admit_owner.assert_called_once_with(self.target)

    def test_non_contention_lease_failure_stops_without_admission_or_retry(self):
        for failure in (ArtifactLockError(contended=False), OSError("fixture I/O failure"),
                        ValueError("fixture malformed lease")):
            with self.subTest(failure=type(failure).__name__):
                self.lease.reset_mock()
                self.lease.side_effect = failure
                observer = BackupServiceObserver(self.target,
                    compatibility=self.compatibility, compatible=self.compatible,
                    launch=self.launch, admit_owner=self.admit_owner, _poll_seconds=.01)
                with mock.patch("sqlite3.connect") as connect:
                    try:
                        observer.start()
                        observer._thread.join(2)
                        self.assertFalse(observer._thread.is_alive())
                        self.lease.assert_called_once_with(self.target)
                        connect.assert_not_called()
                        self.admit_owner.assert_not_called()
                        self.activate.assert_not_called()
                        self.observe.assert_not_called()
                        self.launch.assert_not_called()
                    finally:
                        self.assertTrue(observer.close())

    def test_confirmed_lease_contention_retries_then_admits_only_once(self):
        @contextmanager
        def lease(target):
            if self.lease.call_count < 3:
                raise ArtifactLockError(contended=True)
            yield self.owner

        self.lease.side_effect = lease
        with mock.patch("sqlite3.connect") as connect:
            observer = self.start()
            self.wait_for(lambda: self.observe.call_count >= 3)
            self.assertTrue(observer.close())
            connect.assert_not_called()
        self.assertEqual(self.lease.call_count, 3)
        self.admit_owner.assert_called_once_with(self.target)
        self.activate.assert_called_once_with(self.target, self.owner, self.compatibility)
        self.launch.assert_not_called()

    def test_lease_cleanup_failure_never_retries_even_when_marked_contended(self):
        for failure in (ArtifactLockError(contended=True), OSError("fixture unlock failure")):
            with self.subTest(failure=type(failure).__name__):
                @contextmanager
                def lease(target):
                    try:
                        yield self.owner
                    finally:
                        raise failure

                self.lease.reset_mock()
                self.lease.side_effect = lease
                self.admit_owner.reset_mock()
                self.admit_owner.return_value = False
                observer = BackupServiceObserver(self.target,
                    compatibility=self.compatibility, compatible=self.compatible,
                    launch=self.launch, admit_owner=self.admit_owner, _poll_seconds=.01)
                try:
                    observer.start()
                    observer._thread.join(2)
                    self.assertFalse(observer._thread.is_alive())
                    self.lease.assert_called_once_with(self.target)
                    self.admit_owner.assert_called_once_with(self.target)
                    self.activate.assert_not_called()
                    self.observe.assert_not_called()
                    self.launch.assert_not_called()
                finally:
                    self.assertTrue(observer.close())

    def test_failed_or_disabled_admission_stops_without_presence_or_database_retry(self):
        for outcome in (False, OSError("fixture stale target")):
            with self.subTest(outcome=type(outcome).__name__):
                self.active.clear()
                self.released.clear()
                self.activate.reset_mock()
                self.observe.reset_mock()
                admission = mock.Mock(side_effect=outcome if isinstance(outcome, Exception) else None,
                                      return_value=outcome)
                observer = BackupServiceObserver(self.target,
                    compatibility=self.compatibility, compatible=self.compatible,
                    launch=self.launch, admit_owner=admission, _poll_seconds=.01)
                self.addCleanup(observer.close)
                observer.start()
                self.assertTrue(self.released.wait(2))
                time.sleep(.04)
                self.assertTrue(observer.close())
                admission.assert_called_once_with(self.target)
                self.activate.assert_not_called()
                self.observe.assert_not_called()
                self.launch.assert_not_called()

    def test_default_owner_admission_uses_repository_after_lease_before_file_activation(self):
        from task_governance_tool import async_backup_repository as repository
        ordered = []

        @contextmanager
        def lease(target):
            ordered.append("lease")
            try:
                yield self.owner
            finally:
                self.released.set()

        self.lease.side_effect = lease
        self.activate.side_effect = lambda *args: (ordered.append("activate") or self.active.set())

        def control(target):
            ordered.append("admit")
            return SimpleNamespace(maintenance=SimpleNamespace(enabled=True))

        with mock.patch.object(repository, "read_control", side_effect=control) as read:
            observer = self.start(admit_owner=None)
            self.wait_for(lambda: self.observe.call_count >= 3)
            self.assertTrue(observer.close())
            read.assert_called_once_with(self.target)
        self.assertEqual(ordered, ["lease", "admit", "activate"])

    def test_existing_baseline_is_not_replayed_and_new_request_is_consumed_once(self):
        self.activate.side_effect = lambda *args: (self.active.set() or "b" * 32)
        self.observe.return_value = {"request_id": "b" * 32}
        observer = self.start()
        self.wait_for(lambda: self.observe.call_count >= 3)
        self.launch.assert_not_called()
        self.observe.return_value = {"request_id": "c" * 32}
        self.wait_for(lambda: self.launch.call_count == 1)
        before = self.observe.call_count
        self.wait_for(lambda: self.observe.call_count >= before + 3)
        self.assertTrue(observer.close())
        self.launch.assert_called_once_with(self.target)

    def test_transient_or_invalid_read_does_not_consume_request(self):
        request = {"request_id": "b" * 32}
        values = iter([OSError("fixture read interrupted"), ValueError("invalid fixture"), request])

        def read(*args, **kwargs):
            value = next(values, request)
            if isinstance(value, Exception):
                raise value
            return value

        self.observe.side_effect = read
        observer = self.start()
        self.wait_for(lambda: self.observe.call_count >= 6)
        self.assertTrue(observer.close())
        self.launch.assert_called_once_with(self.target)

    def test_unknown_or_failed_dispatch_does_not_retry_current_opportunity(self):
        self.observe.return_value = {"request_id": "b" * 32}
        self.launch.side_effect = OSError("fixture uncertain launch")
        observer = self.start()
        self.wait_for(lambda: self.observe.call_count >= 5)
        self.launch.assert_called_once_with(self.target)
        self.observe.return_value = {"request_id": "c" * 32}
        self.wait_for(lambda: self.launch.call_count == 2)
        self.assertTrue(observer.close())

    def test_current_identity_coalescing_is_not_a_historical_replay_ledger(self):
        self.observe.return_value = {"request_id": "b" * 32}
        observer = self.start()
        for count, nonce in ((1, "b"), (2, "c"), (3, "b")):
            self.observe.return_value = {"request_id": nonce * 32}
            self.wait_for(lambda: self.launch.call_count == count)
        self.assertTrue(observer.close())
        self.assertEqual(self.launch.call_count, 3)

    def test_activation_failure_stops_without_readmission_or_dispatch(self):
        self.activate.side_effect = OSError("fixture baseline unavailable")
        observer = BackupServiceObserver(self.target,
            compatibility=self.compatibility, compatible=self.compatible,
            admit_owner=self.admit_owner, launch=self.launch, _poll_seconds=.01)
        self.addCleanup(observer.close)
        observer.start()
        self.assertTrue(self.released.wait(2))
        time.sleep(.04)
        self.assertTrue(observer.close())
        self.assertEqual(self.lease_calls, 1)
        self.admit_owner.assert_called_once_with(self.target)
        self.activate.assert_called_once()
        self.observe.assert_not_called()
        self.launch.assert_not_called()

    def test_close_during_admission_does_not_publish_new_presence_afterward(self):
        entered, finish = threading.Event(), threading.Event()

        def admit(target):
            entered.set()
            self.assertTrue(finish.wait(3))
            return True

        self.addCleanup(finish.set)
        observer = BackupServiceObserver(self.target,
            compatibility=self.compatibility, compatible=self.compatible,
            admit_owner=admit, launch=self.launch, _poll_seconds=.01)
        self.addCleanup(observer.close)
        observer.start()
        self.assertTrue(entered.wait(2))
        self.assertFalse(observer.close())
        finish.set()
        self.assertTrue(self.released.wait(2))
        self.assertTrue(observer.close())
        self.activate.assert_not_called()
        self.observe.assert_not_called()
        self.launch.assert_not_called()

    def test_close_does_not_stop_backup_or_release_lease_under_inflight_dispatch(self):
        entered, finish = threading.Event(), threading.Event()

        def launch(target):
            entered.set()
            self.assertTrue(finish.wait(3), "fixture dispatcher was not released")
            return SimpleNamespace(code="started")

        self.addCleanup(finish.set)
        self.observe.return_value = {"request_id": "b" * 32}
        observer = self.start(launch=launch)
        self.assertTrue(entered.wait(2))
        started = time.monotonic()
        self.assertFalse(observer.close())
        self.assertLess(time.monotonic() - started, .9)
        self.assertFalse(self.released.is_set())
        finish.set()
        self.assertTrue(self.released.wait(2))
        self.assertTrue(observer.close())
        self.assertFalse(observer.start())

    def test_default_dispatch_passes_compatibility_to_due_admission(self):
        from task_governance_tool import async_backup
        self.observe.return_value = {"request_id": "b" * 32}
        with mock.patch.object(async_backup, "launch_backup", return_value=SimpleNamespace(code="current")) as launch:
            observer = self.start(launch=None)
            self.wait_for(lambda: launch.call_count == 1)
            self.assertTrue(observer.close())
        launch.assert_called_once_with(self.target, _admit_launch=self.compatible)
        self.compatible.assert_not_called()

    def test_close_before_start_and_repeated_start_are_bounded(self):
        observer = BackupServiceObserver(self.target, compatibility=self.compatibility,
                                         compatible=self.compatible)
        self.assertTrue(observer.close())
        self.assertFalse(observer.start())
        observer = self.start()
        self.assertTrue(observer.start())
        self.assertTrue(observer.close())
        self.assertEqual(self.lease_calls, 1)


class AsyncBackupServiceOwnershipTests(unittest.TestCase):
    @contextmanager
    def serialized_opportunity_io(self, opportunity):
        # These tests judge owner takeover, not zero-wait file-lock contention.
        # Keep the fast test observer from racing publication; contention has
        # its own explicit no-mutation coverage in test_async_backup_opportunity.
        access = threading.Lock()
        original_observe = opportunity.observe_opportunity

        def observe(*args, **kwargs):
            with access:
                return original_observe(*args, **kwargs)

        def request(target):
            with access:
                return opportunity.request_backup(target)

        with mock.patch.object(opportunity, "observe_opportunity", side_effect=observe):
            yield request

    def admission_gate(self):
        from task_governance_tool import async_backup_repository as repository
        allowed = threading.local()
        actual_connect = sqlite3.connect
        calls = []

        def connect(*args, **kwargs):
            if not getattr(allowed, "value", False):
                raise AssertionError("idle opportunity DB access")
            return actual_connect(*args, **kwargs)

        def admit(target):
            calls.append(target)
            allowed.value = True
            try:
                return repository.read_control(target).maintenance.enabled is True
            finally:
                allowed.value = False

        return admit, connect, calls

    def test_unsafe_lease_artifacts_stop_without_database_or_artifact_changes(self):
        from task_governance_tool import async_backup_opportunity as opportunity
        from task_governance_tool.async_backup_state import runtime_root

        for artifact in ("malformed_lock", "hardlinked_lock", "runtime_file"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                _, target, _ = _setup_current(Path(directory))
                root = runtime_root(target)
                root.parent.mkdir(exist_ok=True)
                if artifact == "runtime_file":
                    root.write_bytes(b"preserve unsafe runtime")
                else:
                    root.mkdir()
                    lock = root / "service.lock"
                    lock.write_bytes(b"malformed" if artifact == "malformed_lock" else b"\0")
                    if artifact == "hardlinked_lock":
                        os.link(lock, root / "linked.lock")
                    (root / "service.json").write_bytes(b"preserve service")
                    (root / "opportunity.json").write_bytes(b"preserve opportunity")

                def snapshot():
                    return {path.relative_to(root.parent): path.read_bytes()
                            for path in root.parent.rglob("*") if path.is_file()}

                before = snapshot()
                database_before = target.db_path.read_bytes()
                launch = mock.Mock()
                observer = BackupServiceObserver(target,
                    compatibility=opportunity.current_compatibility(), compatible=lambda: True,
                    launch=launch, _poll_seconds=.01)
                with mock.patch.object(opportunity, "service_lease", wraps=opportunity.service_lease) as lease, \
                        mock.patch.object(opportunity, "activate_service") as activate, \
                        mock.patch("sqlite3.connect") as connect:
                    try:
                        observer.start()
                        observer._thread.join(2)
                        self.assertFalse(observer._thread.is_alive())
                        lease.assert_called_once_with(target)
                        connect.assert_not_called()
                        activate.assert_not_called()
                        launch.assert_not_called()
                        self.assertEqual(snapshot(), before)
                        self.assertEqual(target.db_path.read_bytes(), database_before)
                    finally:
                        self.assertTrue(observer.close())

    def test_stale_cached_target_cannot_replace_presence_and_does_not_retry_db(self):
        from task_governance_tool import async_backup_opportunity as opportunity
        from task_governance_tool import async_backup_repository as repository
        from task_governance_tool.async_backup_state import runtime_root

        with tempfile.TemporaryDirectory() as directory:
            _, target, _ = _setup_current(Path(directory))
            root = runtime_root(target, create=True)
            compatibility = opportunity.current_compatibility()
            with opportunity.service_lease(target) as owner:
                opportunity.activate_service(target, owner, compatibility)
                self.assertEqual(opportunity.request_backup(target).code, "requested")
            before = {name: (root / name).read_bytes()
                      for name in ("service.json", "opportunity.json")}
            stale = replace(target, binding_generation=target.binding_generation + 1)
            observer = BackupServiceObserver(stale, compatibility=compatibility,
                compatible=lambda: True, _poll_seconds=.01)
            with mock.patch.object(repository, "read_control", wraps=repository.read_control) as read, \
                    mock.patch.object(opportunity, "activate_service", wraps=opportunity.activate_service) as activate:
                try:
                    observer.start()
                    deadline = time.monotonic() + 3
                    while observer._thread.is_alive() and time.monotonic() < deadline:
                        time.sleep(.005)
                    self.assertFalse(observer._thread.is_alive())
                    time.sleep(.04)
                    read.assert_called_once_with(stale)
                    activate.assert_not_called()
                    self.assertEqual({name: (root / name).read_bytes() for name in before}, before)
                finally:
                    self.assertTrue(observer.close())

    def test_process_owner_termination_allows_takeover_but_drops_old_opportunity(self):
        from task_governance_tool import async_backup_opportunity as opportunity

        child_code = r'''
import sys
from pathlib import Path
skill, repo, project, binding, generation = sys.argv[1:]
sys.path.insert(0, str(Path(skill) / "scripts"))
from task_governance_tool.state_resolver import backup_runtime_target
from task_governance_tool.async_backup_opportunity import (
    activate_service, current_compatibility, service_lease)
target = backup_runtime_target(Path(skill), repo=Path(repo), project_id=project,
    binding_path_hash=binding, binding_generation=int(generation))
with service_lease(target) as nonce:
    activate_service(target, nonce, current_compatibility())
    print("owned", flush=True)
    sys.stdin.buffer.read(1)
'''
        with tempfile.TemporaryDirectory() as directory:
            install, target, _ = _setup_current(Path(directory))
            child = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", child_code,
                 str(install.skill_root), str(target.project.canonical_repo),
                 target.project.project_id, target.binding_path_hash,
                 str(target.binding_generation)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ready = queue.Queue(maxsize=1)
            reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
            reader.start()
            active = threading.Event()
            launch = mock.Mock(return_value=SimpleNamespace(code="started"))
            admit, connect, admissions = self.admission_gate()
            observer = BackupServiceObserver(target,
                compatibility=opportunity.current_compatibility(), compatible=lambda: True,
                launch=launch, admit_owner=admit, _poll_seconds=.01)
            activate = opportunity.activate_service

            def track(*args):
                baseline = activate(*args)
                active.set()
                return baseline

            try:
                self.assertEqual(ready.get(timeout=5), b"owned\r\n" if sys.platform == "win32" else b"owned\n")
                self.assertEqual(opportunity.request_backup(target).code, "requested")
                with self.serialized_opportunity_io(opportunity) as request, \
                        mock.patch.object(opportunity, "activate_service", side_effect=track), \
                        mock.patch("sqlite3.connect", side_effect=connect):
                    observer.start()
                    self.assertFalse(active.wait(.06))
                    self.assertEqual(admissions, [])
                    launch.assert_not_called()
                    child.terminate()
                    child.wait(timeout=3)
                    self.assertTrue(active.wait(3))
                    # A crash does not turn the prior caller's publication into
                    # permission for the newly owning service to replay it.
                    time.sleep(.04)
                    launch.assert_not_called()
                    self.assertEqual(request(target).code, "requested")
                    deadline = time.monotonic() + 3
                    while not launch.called and time.monotonic() < deadline:
                        time.sleep(.005)
                    launch.assert_called_once_with(target)
                    self.assertEqual(admissions, [target])
            finally:
                self.assertTrue(observer.close())
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=3)
                reader.join(timeout=1)

    def test_standby_takes_over_without_replaying_previous_owner_request(self):
        from task_governance_tool import async_backup_opportunity as opportunity

        with tempfile.TemporaryDirectory() as directory:
            _, target, _ = _setup_current(Path(directory))
            compatibility = opportunity.current_compatibility()
            activated = []
            activate = opportunity.activate_service

            def track(*args):
                result = activate(*args)
                activated.append(args[1])
                return result

            first_launch = mock.Mock(return_value=SimpleNamespace(code="started"))
            second_launch = mock.Mock(return_value=SimpleNamespace(code="started"))
            admit, connect, admissions = self.admission_gate()
            first = BackupServiceObserver(target, compatibility=compatibility,
                compatible=lambda: True, launch=first_launch, admit_owner=admit, _poll_seconds=.01)
            second = BackupServiceObserver(target, compatibility=compatibility,
                compatible=lambda: True, launch=second_launch, admit_owner=admit, _poll_seconds=.01)

            def wait_for(predicate):
                deadline = time.monotonic() + 3
                while not predicate() and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertTrue(predicate(), "real service lease transition timed out")

            with self.serialized_opportunity_io(opportunity) as request, \
                    mock.patch.object(opportunity, "activate_service", side_effect=track), \
                    mock.patch("sqlite3.connect", side_effect=connect):
                try:
                    first.start()
                    wait_for(lambda: len(activated) == 1)
                    second.start()
                    self.assertEqual(request(target).code, "requested")
                    wait_for(lambda: first_launch.call_count == 1)
                    second_launch.assert_not_called()
                    self.assertTrue(first.close())
                    wait_for(lambda: len(activated) == 2)
                    self.assertNotEqual(activated[0], activated[1])
                    # Existing request is fenced by the old owner and remains
                    # a baseline rather than authorizing recovery on takeover.
                    time.sleep(.04)
                    second_launch.assert_not_called()
                    self.assertEqual(request(target).code, "requested")
                    wait_for(lambda: second_launch.call_count == 1)
                    self.assertEqual(admissions, [target, target])
                finally:
                    self.assertTrue(first.close())
                    self.assertTrue(second.close())
            self.assertEqual(opportunity.request_backup(target).code, "deferred")


if __name__ == "__main__":
    unittest.main()
