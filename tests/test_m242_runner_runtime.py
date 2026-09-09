from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import __version__  # noqa: E402
from task_governance_tool import (  # noqa: E402
    _verification_runner_executable_win32 as runtime_executable,
)
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


class RunnerFixedExecutableObservationTests(unittest.TestCase):
    def test_parent_observation_returns_only_the_verified_absolute_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, scratch = _attempt_roots(root)
            observed = root / "python.exe"
            declared = root / "declared-python.exe"
            observed.write_bytes(b"fixture")
            os.link(observed, declared)
            kernel = mock.Mock(spec=["GetModuleFileNameW"])

            def get_module_filename(module, buffer, capacity):
                self.assertIsNone(module)
                self.assertEqual(capacity, runtime_executable._MAX_WINDOWS_PATH)
                buffer.value = str(observed)
                return len(str(observed))

            kernel.GetModuleFileNameW.side_effect = get_module_filename
            with mock.patch.object(
                runtime_executable, "_kernel32", return_value=kernel
            ), mock.patch.object(runtime_executable.sys, "executable", str(declared)):
                executable = runtime_executable.observe_fixed_package_runtime(
                    target, scratch
                )

            self.assertIsInstance(executable, Path)
            self.assertTrue(executable.is_absolute())
            self.assertEqual(executable, observed)
            kernel.GetModuleFileNameW.assert_called_once()

    def test_mismatched_parent_runtime_is_rejected_without_path_disclosure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, scratch = _attempt_roots(root)
            observed = root / "python.exe"
            declared = root / "different-python.exe"
            observed.write_bytes(b"same bytes")
            declared.write_bytes(b"same bytes")
            kernel = mock.Mock(spec=["GetModuleFileNameW"])

            def get_module_filename(_module, buffer, _capacity):
                buffer.value = str(observed)
                return len(str(observed))

            kernel.GetModuleFileNameW.side_effect = get_module_filename
            with mock.patch.object(
                runtime_executable, "_kernel32", return_value=kernel
            ), mock.patch.object(runtime_executable.sys, "executable", str(declared)):
                with self.assertRaises(runtime.VerificationRunnerRuntimeError) as caught:
                    runtime_executable.observe_fixed_package_runtime(target, scratch)

            self.assertEqual(caught.exception.code, "runtime_unavailable")
            self.assertNotIn(temporary, str(caught.exception))

    def test_missing_or_truncated_parent_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, scratch = _attempt_roots(Path(temporary))
            for count in (0, runtime_executable._MAX_WINDOWS_PATH):
                with self.subTest(count=count):
                    kernel = mock.Mock(spec=["GetModuleFileNameW"])
                    kernel.GetModuleFileNameW.return_value = count
                    with mock.patch.object(
                        runtime_executable, "_kernel32", return_value=kernel
                    ):
                        with self.assertRaises(
                            runtime.VerificationRunnerRuntimeError
                        ) as caught:
                            runtime_executable.observe_fixed_package_runtime(
                                target, scratch
                            )
                    self.assertEqual(caught.exception.code, "runtime_unavailable")

    def test_invalid_geometry_fails_before_native_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, _scratch = _attempt_roots(root)
            with mock.patch.object(runtime_executable, "_kernel32") as kernel:
                with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                    runtime_executable.observe_fixed_package_runtime(
                        target, root / "different-attempt" / "scratch"
                    )

        kernel.assert_not_called()

    def test_runtime_must_be_python_outside_both_attempt_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, scratch = _attempt_roots(root)
            for executable in (
                root / "not-python.exe",
                target / "python.exe",
                scratch / "python.exe",
            ):
                with self.subTest(executable=executable.name, parent=executable.parent.name):
                    executable.write_bytes(b"fixture")
                    kernel = mock.Mock(spec=["GetModuleFileNameW"])

                    def get_module_filename(_module, buffer, _capacity):
                        buffer.value = str(executable)
                        return len(str(executable))

                    kernel.GetModuleFileNameW.side_effect = get_module_filename
                    with mock.patch.object(
                        runtime_executable, "_kernel32", return_value=kernel
                    ), mock.patch.object(
                        runtime_executable.sys, "executable", str(executable)
                    ):
                        with self.assertRaises(runtime.VerificationRunnerRuntimeError):
                            runtime_executable.observe_fixed_package_runtime(
                                target, scratch
                            )


@unittest.skipUnless(os.name == "nt", "fixed package runtime is Windows-only")
class RunnerFixedExecutableWindowsTests(unittest.TestCase):
    def test_fixed_parent_runtime_ignores_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            target, scratch = _attempt_roots(Path(temporary))
            with mock.patch.dict(
                os.environ,
                {"PATH": str(Path(temporary) / "untrusted")},
                clear=False,
            ):
                executable = runtime_executable.observe_fixed_package_runtime(
                    target, scratch
                )

            self.assertEqual(executable.name.casefold(), "python.exe")
            self.assertTrue(executable.is_absolute())
            self.assertTrue(os.path.samefile(executable, sys.executable))

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
                runtime_executable._observe_physical_path(
                    junction / "python.exe",
                    directory=False,
                )


if __name__ == "__main__":
    unittest.main()
