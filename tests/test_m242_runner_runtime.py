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


IDENTITY = runtime._RuntimeFileIdentity(1, 2, 3, 0, 4, 5, 6)


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


def _owner(handle: int | None = None) -> runtime.RunnerFixedExecutableLease:
    owner = runtime.RunnerFixedExecutableLease(
        r"C:\private-attempt\target",
        r"C:\private-attempt\scratch",
    )
    if handle is not None:
        owner._primary.handle = handle
        owner._primary.phase = "open"
    return owner


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


class RunnerFixedExecutableOwnerTests(unittest.TestCase):
    def test_constructor_is_resource_free_and_sanitized(self):
        with mock.patch.object(runtime, "_kernel32") as kernel, mock.patch.object(
            runtime,
            "_observe_physical_path",
        ) as observe:
            owner = _owner()
            representation = repr(owner)

        kernel.assert_not_called()
        observe.assert_not_called()
        self.assertEqual(owner._state, "new")
        self.assertEqual(owner._primary, runtime._OwnedRuntimeHandle())
        self.assertEqual(owner._probe, runtime._OwnedRuntimeHandle())
        self.assertFalse(owner.closed)
        self.assertNotIn("private-attempt", representation)

    def test_acquisition_registers_owner_before_identity_and_returns_no_raw_handle(self):
        owner = _owner()
        executable = Path(r"C:\Python\python.exe")
        kernel = mock.Mock()
        kernel.CreateFileW.return_value = 701
        kernel.GetHandleInformation.return_value = 1

        def query_identity(handle: int) -> runtime._RuntimeFileIdentity:
            self.assertEqual(handle, 701)
            self.assertEqual(owner._primary.handle, 701)
            self.assertEqual(owner._primary.phase, "open")
            return IDENTITY

        with mock.patch.object(
            runtime,
            "_kernel32",
            return_value=kernel,
        ), mock.patch.object(
            runtime,
            "_query_identity",
            side_effect=query_identity,
        ):
            with owner._lock:
                acquired = owner._acquire_identity_locked("primary", executable)

        self.assertIs(acquired, IDENTITY)
        self.assertNotIsInstance(acquired, int)
        self.assertEqual(owner._primary.handle, 701)
        self.assertEqual(owner._primary.phase, "open")
        kernel.CloseHandle.assert_not_called()

    def test_failure_after_registration_is_finalized_by_reachable_owner(self):
        owner = _owner()
        kernel = mock.Mock()
        kernel.CreateFileW.return_value = 702
        kernel.GetHandleInformation.return_value = 1
        kernel.CloseHandle.return_value = 1

        with mock.patch.object(
            runtime,
            "_kernel32",
            return_value=kernel,
        ), mock.patch.object(
            runtime,
            "_query_identity",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                with owner._lock:
                    owner._acquire_identity_locked(
                        "primary",
                        Path(r"C:\Python\python.exe"),
                    )
            self.assertEqual(owner._primary.handle, 702)
            self.assertEqual(owner._primary.phase, "open")
            self.assertEqual(owner.finalize_owner(), "closed")

        self.assertTrue(owner.closed)
        kernel.CloseHandle.assert_called_once()

    def test_interrupted_native_acquisition_without_returned_value_is_uncertain(self):
        owner = _owner()
        kernel = mock.Mock()
        kernel.CreateFileW.side_effect = KeyboardInterrupt()

        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            with self.assertRaises(KeyboardInterrupt):
                with owner._lock:
                    owner._acquire_identity_locked(
                        "primary",
                        Path(r"C:\Python\python.exe"),
                    )
            self.assertEqual(owner._primary.phase, "uncertain")
            self.assertEqual(owner.finalize_owner(), "uncertain")
            self.assertEqual(owner.finalize_owner(), "uncertain")

        kernel.CloseHandle.assert_not_called()

    def test_parent_binding_returns_only_path_and_identity(self):
        owner = _owner()
        observed = Path(r"C:\Python\python.exe")
        declared = Path(r"D:\Python\python.exe")
        kernel = mock.Mock()

        def get_module_filename(_module, buffer, _capacity):
            buffer.value = str(observed)
            return len(str(observed))

        kernel.GetModuleFileNameW.side_effect = get_module_filename
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
            runtime.RunnerFixedExecutableLease,
            "_acquire_identity_locked",
            autospec=True,
            side_effect=(IDENTITY, IDENTITY),
        ) as acquire, mock.patch.object(
            runtime.RunnerFixedExecutableLease,
            "_release_probe_locked",
            autospec=True,
            return_value="closed",
        ):
            bound = runtime._bind_parent_process_executable(owner)

        self.assertEqual(bound, (observed, IDENTITY))
        self.assertEqual(
            [call.args[1] for call in acquire.call_args_list],
            ["primary", "probe"],
        )
        self.assertTrue(all(len(call.args) == 3 for call in acquire.call_args_list))

    def test_definite_close_retries_only_on_later_finalization(self):
        owner = _owner(703)
        kernel = mock.Mock()
        kernel.CloseHandle.side_effect = (0, 1)
        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            self.assertEqual(owner.finalize_owner(), "open")
            self.assertEqual(owner._primary.close_attempts, 1)
            self.assertEqual(owner.finalize_owner(), "closed")
            self.assertEqual(owner.finalize_owner(), "closed")

        self.assertEqual(owner._primary.close_attempts, 2)
        self.assertEqual(kernel.CloseHandle.call_count, 2)
        self.assertTrue(owner.closed)

    def test_definite_close_never_exceeds_two_native_attempts(self):
        owner = _owner(704)
        kernel = mock.Mock()
        kernel.CloseHandle.side_effect = (0, 0, 1)
        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            self.assertEqual(owner.finalize_owner(), "open")
            self.assertEqual(owner.finalize_owner(), "open")
            self.assertEqual(owner.finalize_owner(), "open")

        self.assertEqual(owner._primary.close_attempts, 2)
        self.assertEqual(kernel.CloseHandle.call_count, 2)
        self.assertFalse(owner.closed)

    def test_pre_native_close_interruption_remains_definitely_open_for_retry(self):
        owner = _owner(705)
        kernel = mock.Mock()
        kernel.CloseHandle.return_value = 1
        with mock.patch.object(
            runtime,
            "_kernel32",
            side_effect=(KeyboardInterrupt(), kernel),
        ) as resolve_kernel:
            self.assertEqual(owner.finalize_owner(), "open")
            self.assertEqual(owner.finalize_owner(), "closed")

        self.assertEqual(resolve_kernel.call_count, 2)
        kernel.CloseHandle.assert_called_once()
        self.assertTrue(owner.closed)

    def test_native_close_interruption_is_uncertain_and_never_retried(self):
        owner = _owner(706)
        kernel = mock.Mock()
        kernel.CloseHandle.side_effect = KeyboardInterrupt()

        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            self.assertEqual(owner.finalize_owner(), "uncertain")
            self.assertEqual(owner.finalize_owner(), "uncertain")

        self.assertEqual(owner._primary.phase, "uncertain")
        self.assertEqual(owner._primary.handle, 0)
        self.assertEqual(owner._primary.close_attempts, 1)
        kernel.CloseHandle.assert_called_once()

    def test_cleanup_aggregate_preserves_uncertain_over_open_over_closed(self):
        cases = (
            (("closed", 0), ("closed", 0), "closed"),
            (("open", 801), ("closed", 0), "open"),
            (("open", 801), ("uncertain", 0), "uncertain"),
            (("closed", 0), ("closing", 802), "uncertain"),
        )
        for primary, probe, expected in cases:
            with self.subTest(expected=expected, primary=primary, probe=probe):
                owner = _owner()
                owner._primary.phase, owner._primary.handle = primary
                owner._probe.phase, owner._probe.handle = probe
                with owner._lock:
                    observed = owner._aggregate_cleanup_state_locked()
                self.assertEqual(observed, expected)

    def test_active_close_reports_aggregate_uncertainty_without_releasing(self):
        owner = _owner(711)
        owner._probe.phase = "uncertain"
        owner._state = "active"

        with mock.patch.object(runtime, "_kernel32") as kernel:
            with self.assertRaises(runtime.VerificationRunnerRuntimeError) as caught:
                owner.close()

        self.assertEqual(caught.exception.handle_cleanup_state, "uncertain")
        self.assertEqual(owner._primary.handle, 711)
        self.assertEqual(owner._primary.phase, "open")
        kernel.assert_not_called()

    def test_concurrent_finalization_is_serialized_and_idempotent(self):
        owner = _owner(707)
        native_entered = threading.Event()
        release_native = threading.Event()
        start = threading.Barrier(3)
        results: list[str] = []
        failures: list[BaseException] = []
        result_lock = threading.Lock()
        kernel = mock.Mock()

        def close_handle(_handle: object) -> int:
            native_entered.set()
            if not release_native.wait(2.0):
                raise RuntimeError("test release was not signalled")
            return 1

        def finalize() -> None:
            start.wait()
            try:
                result = owner.finalize_owner()
                with result_lock:
                    results.append(result)
            except BaseException as exc:
                with result_lock:
                    failures.append(exc)

        threads = (threading.Thread(target=finalize), threading.Thread(target=finalize))
        kernel.CloseHandle.side_effect = close_handle
        with mock.patch.object(runtime, "_kernel32", return_value=kernel):
            for thread in threads:
                thread.start()
            start.wait()
            self.assertTrue(native_entered.wait(1.0))
            release_native.set()
            for thread in threads:
                thread.join(2.0)
            self.assertEqual(owner.finalize_owner(), "closed")

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(results, ["closed", "closed"])
        kernel.CloseHandle.assert_called_once()
        self.assertTrue(owner.closed)

    def test_nested_entry_and_direct_close_while_active_fail_closed(self):
        owner = _owner()
        executable = Path(r"C:\Python\python.exe")
        kernel = mock.Mock()
        kernel.CloseHandle.return_value = 1

        def bind(bound_owner: runtime.RunnerFixedExecutableLease) -> Path:
            self.assertIs(bound_owner, owner)
            bound_owner._primary.handle = 708
            bound_owner._primary.phase = "open"
            return executable

        with mock.patch.object(
            runtime,
            "_bind_fixed_package_runtime",
            side_effect=bind,
        ), mock.patch.object(runtime, "_kernel32", return_value=kernel):
            with owner as active_executable:
                self.assertEqual(active_executable, executable)
                self.assertEqual(owner.executable, executable)
                with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                    owner.__enter__()
                self.assertEqual(owner._state, "active")
                self.assertEqual(owner._primary.handle, 708)
                with self.assertRaises(runtime.VerificationRunnerRuntimeError) as close:
                    owner.close()
                self.assertEqual(close.exception.handle_cleanup_state, "open")
                kernel.CloseHandle.assert_not_called()

        self.assertTrue(owner.closed)
        kernel.CloseHandle.assert_called_once()

    def test_unmatched_exit_and_reentry_after_release_fail_closed(self):
        owner = _owner()
        with self.assertRaises(runtime.VerificationRunnerRuntimeError) as unmatched:
            owner.__exit__(None, None, None)
        self.assertEqual(unmatched.exception.handle_cleanup_state, "uncertain")
        self.assertEqual(owner._state, "new")

        self.assertEqual(owner.finalize_owner(), "closed")
        with self.assertRaises(runtime.VerificationRunnerRuntimeError):
            owner.__enter__()
        with self.assertRaises(runtime.VerificationRunnerRuntimeError):
            owner.__exit__(None, None, None)
        self.assertTrue(owner.closed)

    def test_entry_failure_after_owner_registration_closes_handle(self):
        owner = _owner()
        kernel = mock.Mock()
        kernel.CloseHandle.return_value = 1

        def bind(bound_owner: runtime.RunnerFixedExecutableLease) -> Path:
            bound_owner._primary.handle = 709
            bound_owner._primary.phase = "open"
            raise KeyboardInterrupt()

        with mock.patch.object(
            runtime,
            "_bind_fixed_package_runtime",
            side_effect=bind,
        ), mock.patch.object(runtime, "_kernel32", return_value=kernel):
            with self.assertRaises(KeyboardInterrupt):
                owner.__enter__()

        self.assertTrue(owner.closed)
        kernel.CloseHandle.assert_called_once()

    def test_exit_native_interruption_is_uncertain_and_not_retried(self):
        owner = _owner()
        kernel = mock.Mock()
        kernel.CloseHandle.side_effect = KeyboardInterrupt()

        def bind(bound_owner: runtime.RunnerFixedExecutableLease) -> Path:
            bound_owner._primary.handle = 710
            bound_owner._primary.phase = "open"
            return Path(r"C:\Python\python.exe")

        with mock.patch.object(
            runtime,
            "_bind_fixed_package_runtime",
            side_effect=bind,
        ), mock.patch.object(runtime, "_kernel32", return_value=kernel):
            with self.assertRaises(runtime.VerificationRunnerRuntimeError) as caught:
                with owner:
                    pass
            self.assertEqual(caught.exception.handle_cleanup_state, "uncertain")
            self.assertEqual(owner.finalize_owner(), "uncertain")

        kernel.CloseHandle.assert_called_once()

    def test_invalid_geometry_fails_before_native_acquisition(self):
        owner = runtime.RunnerFixedExecutableLease(
            r"C:\private-attempt\target",
            r"C:\different-attempt\scratch",
        )
        with mock.patch.object(runtime, "_kernel32") as kernel:
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                with owner:
                    pass

        kernel.assert_not_called()
        self.assertTrue(owner.closed)


@unittest.skipUnless(os.name == "nt", "fixed package runtime is Windows-only")
class RunnerFixedExecutableWindowsTests(unittest.TestCase):
    def test_fixed_parent_runtime_ignores_path_and_holds_noninheritable_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, scratch = _attempt_roots(Path(temporary))
            owner = runtime.RunnerFixedExecutableLease(target, scratch)
            with mock.patch.dict(
                os.environ,
                {"PATH": str(Path(temporary) / "untrusted")},
                clear=False,
            ):
                with owner as executable:
                    self.assertEqual(executable.name.casefold(), "python.exe")
                    self.assertEqual(owner.executable, executable)
                    self.assertNotIn(str(executable), repr(owner))
                    flags = ctypes.c_uint32()
                    self.assertTrue(
                        runtime._kernel32().GetHandleInformation(
                            ctypes.c_void_p(owner._primary.handle),
                            ctypes.byref(flags),
                        )
                    )
                    self.assertFalse(flags.value & runtime._HANDLE_FLAG_INHERIT)

            self.assertTrue(owner.closed)
            self.assertEqual(owner.finalize_owner(), "closed")
            with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                _ = owner.executable

    def test_owned_handle_denies_write_and_replacement_until_finalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "python.exe"
            replacement = root / "replacement.exe"
            executable.write_bytes(b"original")
            replacement.write_bytes(b"replacement")
            owner = _owner()
            with owner._lock:
                identity = owner._acquire_identity_locked("primary", executable)
            self.assertIsInstance(identity, runtime._RuntimeFileIdentity)

            try:
                with self.assertRaises(OSError):
                    executable.write_bytes(b"changed")
                with self.assertRaises(OSError):
                    os.replace(replacement, executable)
            finally:
                self.assertEqual(owner.finalize_owner(), "closed")

            executable.write_bytes(b"changed")
            self.assertEqual(executable.read_bytes(), b"changed")

    def test_reparse_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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
