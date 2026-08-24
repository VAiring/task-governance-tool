from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import __version__  # noqa: E402
from task_governance_tool import verification_runner_runtime as runtime  # noqa: E402
from task_governance_tool.self_status import (  # noqa: E402
    ReleaseManifestVerificationError,
    verify_release_manifest_core,
)


def _write_minimal_manifest(root: Path) -> None:
    core = root / "core.py"
    core.write_bytes(b"value = 1\n")
    manifest = {
        "manifest_version": 1,
        "package_name": "task-governance-tool",
        "package_version": __version__,
        "release_origin": "github:VAiring/task-governance-tool",
        "core_files": {
            "core.py": "sha256:" + hashlib.sha256(core.read_bytes()).hexdigest()
        },
    }
    (root / "release-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )


def _attempt_roots(root: Path) -> tuple[Path, Path]:
    attempt = root / "tg_verification_runner_attempt_0123456789abcdef"
    target = attempt / "target"
    scratch = attempt / "scratch"
    target.mkdir(parents=True)
    scratch.mkdir()
    return target, scratch


class RunnerRuntimeManifestTests(unittest.TestCase):
    def test_strict_manifest_verifier_returns_closed_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = Path(temporary) / "task-governance-tool"
            skill.mkdir()
            _write_minimal_manifest(skill)

            manifest = verify_release_manifest_core(
                skill,
                expected_package_version=__version__,
            )
            identity = runtime.capture_runner_implementation(skill)

            self.assertEqual(
                set(manifest.canonical_value()),
                {"core_files", "manifest_version", "package_name", "package_version"},
            )
            self.assertEqual(identity.core_files, tuple(manifest.core_files.items()))
            self.assertEqual(identity.package_version, __version__)
            self.assertRegex(identity.implementation_digest, r"^sha256:[0-9a-f]{64}$")

    def test_strict_manifest_failure_is_sanitized_and_retains_no_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = Path(temporary) / "private-name"
            skill.mkdir()
            _write_minimal_manifest(skill)
            (skill / "core.py").write_text("changed\n", encoding="utf-8")

            with self.assertRaises(ReleaseManifestVerificationError) as caught:
                verify_release_manifest_core(
                    skill,
                    expected_package_version=__version__,
                )
            with self.assertRaises(runtime.VerificationRunnerRuntimeError) as mapped:
                runtime.capture_runner_implementation(skill)

            self.assertEqual(caught.exception.code, "package_core_unverified")
            self.assertNotIn(temporary, str(caught.exception))
            self.assertEqual(mapped.exception.code, "policy_mismatch")
            self.assertNotIn(temporary, str(mapped.exception))

        with self.assertRaises(ReleaseManifestVerificationError):
            verify_release_manifest_core(
                object(),
                expected_package_version=__version__,
            )


class RunnerFixedExecutableLeaseStateTests(unittest.TestCase):
    @staticmethod
    def _lease(handle: int = 701) -> runtime.RunnerFixedExecutableLease:
        return runtime.RunnerFixedExecutableLease(
            Path(r"C:\Python\python.exe"),
            handle,
            runtime._RuntimeFileIdentity(1, 2, 3, 0, 4, 5, 6),
        )

    @staticmethod
    def _run_concurrent_closes(
        lease: runtime.RunnerFixedExecutableLease,
    ) -> tuple[list[BaseException], tuple[threading.Thread, threading.Thread]]:
        start = threading.Barrier(3)
        failures: list[BaseException] = []
        failures_lock = threading.Lock()

        def close_lease() -> None:
            start.wait()
            try:
                lease.close()
            except BaseException as exc:
                with failures_lock:
                    failures.append(exc)

        threads = (
            threading.Thread(target=close_lease),
            threading.Thread(target=close_lease),
        )
        for thread in threads:
            thread.start()
        start.wait()
        return failures, threads

    def test_concurrent_close_success_calls_native_once(self):
        lease = self._lease()
        native_entered = threading.Event()
        release_native = threading.Event()
        kernel = mock.Mock()

        def close_handle(_handle: object) -> int:
            native_entered.set()
            if not release_native.wait(2.0):
                raise RuntimeError("test release was not signalled")
            return 1

        kernel.CloseHandle.side_effect = close_handle
        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            failures, threads = self._run_concurrent_closes(lease)
            self.assertTrue(native_entered.wait(1.0))
            release_native.set()
            for thread in threads:
                thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertTrue(lease.closed)
        kernel.CloseHandle.assert_called_once()

    def test_concurrent_close_serializes_definitive_failure_and_retry(self):
        lease = self._lease(702)
        first_entered = threading.Event()
        release_first = threading.Event()
        call_lock = threading.Lock()
        active_native_calls = 0
        maximum_native_calls = 0
        call_count = 0
        kernel = mock.Mock()

        def close_handle(_handle: object) -> int:
            nonlocal active_native_calls, maximum_native_calls, call_count
            with call_lock:
                active_native_calls += 1
                maximum_native_calls = max(maximum_native_calls, active_native_calls)
                call_count += 1
                current_call = call_count
            try:
                if current_call == 1:
                    first_entered.set()
                    if not release_first.wait(2.0):
                        raise RuntimeError("test release was not signalled")
                    return 0
                return 1
            finally:
                with call_lock:
                    active_native_calls -= 1

        kernel.CloseHandle.side_effect = close_handle
        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            failures, threads = self._run_concurrent_closes(lease)
            self.assertTrue(first_entered.wait(1.0))
            release_first.set()
            for thread in threads:
                thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], runtime.VerificationRunnerRuntimeError)
        self.assertEqual(maximum_native_calls, 1)
        self.assertEqual(kernel.CloseHandle.call_count, 2)
        self.assertTrue(lease.closed)

    def test_concurrent_close_after_interruption_never_retries(self):
        lease = self._lease(703)
        kernel = mock.Mock()
        kernel.CloseHandle.side_effect = KeyboardInterrupt()

        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            failures, threads = self._run_concurrent_closes(lease)
            for thread in threads:
                thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(failures), 2)
        self.assertTrue(
            all(
                isinstance(exc, runtime.VerificationRunnerRuntimeError)
                for exc in failures
            )
        )
        self.assertFalse(lease.closed)
        self.assertEqual(lease._handle, 0)
        kernel.CloseHandle.assert_called_once()

    def test_executable_access_waits_for_close_and_then_fails(self):
        lease = self._lease(704)
        native_entered = threading.Event()
        release_native = threading.Event()
        access_started = threading.Event()
        access_finished = threading.Event()
        failures: list[BaseException] = []
        kernel = mock.Mock()

        def close_handle(_handle: object) -> int:
            native_entered.set()
            if not release_native.wait(2.0):
                raise RuntimeError("test release was not signalled")
            return 1

        def access_executable() -> None:
            access_started.set()
            try:
                _ = lease.executable
            except BaseException as exc:
                failures.append(exc)
            finally:
                access_finished.set()

        kernel.CloseHandle.side_effect = close_handle
        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            closer = threading.Thread(target=lease.close)
            closer.start()
            self.assertTrue(native_entered.wait(1.0))
            accessor = threading.Thread(target=access_executable)
            accessor.start()
            self.assertTrue(access_started.wait(1.0))
            self.assertFalse(access_finished.wait(0.05))
            release_native.set()
            closer.join(2.0)
            accessor.join(2.0)

        self.assertFalse(closer.is_alive())
        self.assertFalse(accessor.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], runtime.VerificationRunnerRuntimeError)
        self.assertTrue(lease.closed)
        kernel.CloseHandle.assert_called_once()

    def test_nested_entry_and_direct_close_while_active_fail_closed(self):
        lease = self._lease(705)
        kernel = mock.Mock()
        kernel.CloseHandle.return_value = 1

        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            with lease as active:
                self.assertIs(active, lease)
                with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                    lease.__enter__()
                with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                    lease.close()
                self.assertEqual(active.executable.name.casefold(), "python.exe")
                kernel.CloseHandle.assert_not_called()

        self.assertTrue(lease.closed)
        kernel.CloseHandle.assert_called_once()


@unittest.skipUnless(os.name == "nt", "fixed package runtime is Windows-only")
class RunnerFixedExecutableWindowsTests(unittest.TestCase):
    def test_parent_runtime_is_fixed_ignores_path_and_closes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, scratch = _attempt_roots(Path(temporary))
            with mock.patch.dict(
                os.environ,
                {"PATH": str(Path(temporary) / "untrusted")},
                clear=False,
            ):
                lease = runtime.open_fixed_package_runtime(target, scratch)
            self.assertFalse(lease.closed)
            self.assertEqual(lease.executable.name.casefold(), "python.exe")
            self.assertTrue(
                runtime._same_locked_path(Path(sys.executable), lease._identity)
            )
            flags = ctypes.c_uint32()
            self.assertTrue(
                runtime._kernel32().GetHandleInformation(
                    ctypes.c_void_p(lease._handle),
                    ctypes.byref(flags),
                )
            )
            self.assertFalse(flags.value & runtime._HANDLE_FLAG_INHERIT)
            self.assertNotIn(str(lease.executable), repr(lease))
            lease.close()
            self.assertTrue(lease.closed)
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                _ = lease.executable
            lease.close()

    def test_parent_runtime_rejects_sys_executable_mismatch(self):
        with mock.patch.object(runtime.sys, "executable", r"C:\not-parent\python.exe"):
            with self.assertRaises(runtime.VerificationRunnerRuntimeError) as caught:
                runtime._parent_process_executable()
        self.assertEqual(caught.exception.code, "runtime_unavailable")

    def test_parent_runtime_accepts_different_spelling_for_same_identity(self):
        observed = Path(r"C:\Program Files\Python\python.exe")
        declared = Path(r"C:\PROGRA~1\Python\python.exe")
        identity = runtime._RuntimeFileIdentity(1, 2, 3, 0, 4, 5, 6)
        kernel = mock.Mock()

        def get_module_filename(_module, buffer, _capacity):
            buffer.value = str(observed)
            return len(str(observed))

        kernel.GetModuleFileNameW.side_effect = get_module_filename
        kernel.CloseHandle.return_value = 1
        with mock.patch.object(runtime, "_kernel32", return_value=kernel), mock.patch.object(
            runtime.sys,
            "executable",
            str(declared),
        ), mock.patch.object(runtime, "_observe_physical_path") as observed_path, mock.patch.object(
            runtime,
            "_open_runtime_handle",
            side_effect=((101, identity), (102, identity)),
        ):
            path, handle, returned_identity = runtime._parent_process_executable()
            kernel.CloseHandle(ctypes.c_void_p(handle))

        self.assertEqual(path, observed)
        self.assertEqual(handle, 101)
        self.assertEqual(returned_identity, identity)
        self.assertEqual(
            [call.args[0].value for call in kernel.CloseHandle.call_args_list],
            [102, 101],
        )
        self.assertEqual(
            observed_path.call_args_list,
            [
                mock.call(observed, directory=False),
                mock.call(declared, directory=False),
                mock.call(observed, directory=False),
                mock.call(declared, directory=False),
            ],
        )

    def test_parent_runtime_rejects_different_physical_identity(self):
        observed = Path(r"C:\Python\python.exe")
        declared = Path(r"D:\Python\python.exe")
        observed_identity = runtime._RuntimeFileIdentity(1, 2, 3, 0, 4, 5, 6)
        declared_identity = runtime._RuntimeFileIdentity(7, 8, 9, 0, 4, 5, 6)
        kernel = mock.Mock()

        def get_module_filename(_module, buffer, _capacity):
            buffer.value = str(observed)
            return len(str(observed))

        kernel.GetModuleFileNameW.side_effect = get_module_filename
        kernel.CloseHandle.return_value = 1
        with mock.patch.object(runtime, "_kernel32", return_value=kernel), mock.patch.object(
            runtime.sys,
            "executable",
            str(declared),
        ), mock.patch.object(runtime, "_observe_physical_path"), mock.patch.object(
            runtime,
            "_open_runtime_handle",
            side_effect=((101, observed_identity), (102, declared_identity)),
        ), self.assertRaises(runtime.VerificationRunnerRuntimeError) as caught:
            runtime._parent_process_executable()

        self.assertEqual(caught.exception.code, "runtime_unavailable")
        self.assertEqual(
            [call.args[0].value for call in kernel.CloseHandle.call_args_list],
            [102, 101],
        )

    def test_locked_path_corroboration_requires_second_handle_close(self):
        identity = runtime._RuntimeFileIdentity(1, 2, 3, 0, 4, 5, 6)
        kernel = mock.Mock()
        kernel.CloseHandle.return_value = 0
        with mock.patch.object(
            runtime,
            "_open_runtime_handle",
            return_value=(123, identity),
        ), mock.patch.object(runtime, "_kernel32", return_value=kernel):
            self.assertFalse(runtime._same_locked_path(Path(r"C:\Python\python.exe"), identity))
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_runtime_handle_interruption_is_closed_or_reports_uncertainty(self):
        executable = Path(r"C:\Python\python.exe")
        for close_result, expected in (
            (1, KeyboardInterrupt),
            (0, runtime.VerificationRunnerRuntimeError),
        ):
            with self.subTest(close_result=close_result):
                kernel = mock.Mock()
                kernel.CreateFileW.return_value = 701
                kernel.GetHandleInformation.return_value = 1
                kernel.CloseHandle.return_value = close_result
                with mock.patch.object(
                    runtime,
                    "_kernel32",
                    return_value=kernel,
                ), mock.patch.object(
                    runtime,
                    "_query_identity",
                    side_effect=KeyboardInterrupt(),
                ):
                    with self.assertRaises(expected) as raised:
                        runtime._open_runtime_handle(executable)

                if isinstance(
                    raised.exception,
                    runtime.VerificationRunnerRuntimeError,
                ):
                    self.assertEqual(raised.exception.code, "runtime_unavailable")
                self.assertEqual(
                    [
                        call.args[0].value
                        for call in kernel.CloseHandle.call_args_list
                    ],
                    [701],
                )

    def test_runtime_return_handle_without_raw_value_is_uncertain(self):
        kernel = mock.Mock()
        acquired: list[int] = []

        def acquire_then_interrupt(*_args: object) -> int:
            acquired.append(802)
            raise KeyboardInterrupt()

        kernel.CreateFileW.side_effect = acquire_then_interrupt
        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            with self.assertRaises(
                runtime.VerificationRunnerRuntimeError
            ) as raised:
                runtime._open_runtime_handle(Path(r"C:\Python\python.exe"))

        self.assertEqual(acquired, [802])
        self.assertEqual(raised.exception.code, "runtime_unavailable")
        kernel.CloseHandle.assert_not_called()

    def test_held_handle_denies_write_and_replacement_until_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "python.exe"
            replacement = root / "replacement.exe"
            executable.write_bytes(b"original")
            replacement.write_bytes(b"replacement")
            handle, identity = runtime._open_runtime_handle(executable)
            lease = runtime.RunnerFixedExecutableLease(executable, handle, identity)
            try:
                self.assertTrue(runtime._same_locked_path(executable, identity))
                with self.assertRaises(OSError):
                    executable.write_bytes(b"changed")
                with self.assertRaises(OSError):
                    os.replace(replacement, executable)
            finally:
                lease.close()
            executable.write_bytes(b"changed")
            self.assertEqual(executable.read_bytes(), b"changed")

    def test_held_leaf_denies_parent_and_grandparent_path_swap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            grandparent = root / "grandparent"
            parent = grandparent / "parent"
            parent.mkdir(parents=True)
            executable = parent / "python.exe"
            executable.write_bytes(b"fixed")
            moved_parent = grandparent / "moved-parent"
            moved_grandparent = root / "moved-grandparent"
            handle, identity = runtime._open_runtime_handle(executable)
            lease = runtime.RunnerFixedExecutableLease(executable, handle, identity)
            try:
                with self.assertRaises(OSError):
                    os.replace(parent, moved_parent)
                with self.assertRaises(OSError):
                    os.replace(grandparent, moved_grandparent)
                self.assertEqual(executable.read_bytes(), b"fixed")
            finally:
                lease.close()

            os.replace(parent, moved_parent)
            self.assertEqual((moved_parent / "python.exe").read_bytes(), b"fixed")

    def test_close_failure_retains_handle_ownership_for_later_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "python.exe"
            executable.write_bytes(b"fixture")
            handle, identity = runtime._open_runtime_handle(executable)
            lease = runtime.RunnerFixedExecutableLease(executable, handle, identity)

            failing = mock.Mock()
            failing.CloseHandle.return_value = 0
            with mock.patch.object(runtime, "_kernel32", return_value=failing):
                with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                    lease.close()
            self.assertFalse(lease.closed)
            self.assertEqual(lease._handle, handle)

            lease.close()
            self.assertTrue(lease.closed)

    def test_interrupted_lease_close_is_uncertain_and_is_not_retried(self):
        identity = runtime._RuntimeFileIdentity(1, 2, 3, 0, 4, 5, 6)
        lease = runtime.RunnerFixedExecutableLease(
            Path(r"C:\Python\python.exe"),
            702,
            identity,
        )
        kernel = mock.Mock()
        kernel.CloseHandle.side_effect = KeyboardInterrupt()

        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                lease.close()
            self.assertFalse(lease.closed)
            self.assertEqual(lease._handle, 0)
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                _ = lease.executable
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                lease.__enter__()
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                lease.close()

        kernel.CloseHandle.assert_called_once()

    def test_parent_runtime_cleanup_attempts_both_handles_after_close_failure(self):
        observed = Path(r"C:\Python\python.exe")
        declared = Path(r"D:\Python\python.exe")
        first = runtime._RuntimeFileIdentity(1, 2, 3, 0, 4, 5, 6)
        second = runtime._RuntimeFileIdentity(7, 8, 9, 0, 4, 5, 6)
        kernel = mock.Mock()

        def get_module_filename(_module, buffer, _capacity):
            buffer.value = str(observed)
            return len(str(observed))

        kernel.GetModuleFileNameW.side_effect = get_module_filename
        kernel.CloseHandle.side_effect = (0, 1)
        with mock.patch.object(
            runtime,
            "_kernel32",
            return_value=kernel,
        ), mock.patch.object(
            runtime.sys,
            "executable",
            str(declared),
        ), mock.patch.object(
            runtime,
            "_observe_physical_path",
        ), mock.patch.object(
            runtime,
            "_open_runtime_handle",
            side_effect=((703, first), (704, second)),
        ), self.assertRaises(runtime.VerificationRunnerRuntimeError):
            runtime._parent_process_executable()

        self.assertEqual(
            [call.args[0].value for call in kernel.CloseHandle.call_args_list],
            [704, 703],
        )

    def test_geometry_and_reparse_parent_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, scratch = _attempt_roots(root / "good")
            wrong = root / "wrong"
            wrong.mkdir()
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                runtime.open_fixed_package_runtime(target, wrong)

            physical = root / "physical"
            physical.mkdir()
            (physical / "python.exe").write_bytes(b"fixture")
            junction = root / "linked"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(physical)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest("Windows junction creation unavailable")
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                runtime._observe_physical_path(
                    junction / "python.exe",
                    directory=False,
                )


if __name__ == "__main__":
    unittest.main()
