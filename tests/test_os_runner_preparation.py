"""POSIX Runner preparation and private-tree checks without Runner launch."""

from __future__ import annotations

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

from task_governance_tool import (  # noqa: E402
    _verification_runner_executable_posix as executable_posix,
    verification_runner_git as runner_git,
    verification_runner_lifecycle as lifecycle,
    verification_runner_process as process,
    verification_runner_runtime as runtime,
)


ATTEMPT_ID = "tg_verification_runner_attempt_0123456789abcdef"
POSIX_HOST = os.name == "posix" and sys.platform in {"linux", "darwin"}


def _attempt(root: Path):
    paths = lifecycle.verification_runner_state_paths(root / "runner")
    lifecycle.ensure_runner_layout(paths)
    exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
    lifecycle.create_scratch_directories(paths, ATTEMPT_ID)
    return paths, exact


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=True,
        shell=False,
    ).stdout


class PosixImageObservationTests(unittest.TestCase):
    def assert_runtime_unavailable(self, call):
        with self.assertRaises(runtime.VerificationRunnerRuntimeError) as caught:
            call()
        self.assertEqual(caught.exception.code, "runtime_unavailable")
        self.assertEqual(
            str(caught.exception), "the fixed package runtime could not be verified",
        )

    def test_linux_image_uses_proc_and_sanitizes_unavailable_or_deleted_image(self):
        with mock.patch.object(
            executable_posix.os, "readlink", return_value="/runtime/python3",
        ) as readlink:
            self.assertEqual(executable_posix._linux_process_image(), "/runtime/python3")
        readlink.assert_called_once_with("/proc/self/exe")
        with mock.patch.object(
            executable_posix.os, "readlink", side_effect=OSError("private observation detail"),
        ):
            self.assert_runtime_unavailable(executable_posix._linux_process_image)
        with mock.patch.object(
            executable_posix.os, "readlink", return_value="/private/python3 (deleted)",
        ):
            self.assert_runtime_unavailable(executable_posix._linux_process_image)

    def test_macos_image_uses_current_pid_and_rejects_failed_or_truncated_syscall(self):
        library = mock.Mock(spec=["proc_pidpath"])
        image = b"/Library/Frameworks/Python.framework/Versions/3.12/Python"

        def observe(pid, buffer, capacity):
            self.assertEqual(pid, os.getpid())
            self.assertEqual(capacity, 4096)
            buffer.value = image
            return len(image)

        library.proc_pidpath.side_effect = observe
        with mock.patch.object(executable_posix.ctypes, "CDLL", return_value=library) as load:
            self.assertEqual(executable_posix._macos_process_image(), image.decode("ascii"))
        load.assert_called_once_with("/usr/lib/libproc.dylib", use_errno=True)
        self.assertEqual(len(library.proc_pidpath.argtypes), 3)
        library.proc_pidpath.side_effect = None
        for count in (0, -1, 4096, 1):
            with self.subTest(count=count), mock.patch.object(
                executable_posix.ctypes, "CDLL", return_value=library,
            ):
                library.proc_pidpath.return_value = count
                self.assert_runtime_unavailable(executable_posix._macos_process_image)

    def test_macos_library_and_invalid_utf8_fail_without_raw_details(self):
        with mock.patch.object(
            executable_posix.ctypes, "CDLL", side_effect=OSError("private library detail"),
        ):
            self.assert_runtime_unavailable(executable_posix._macos_process_image)
        library = mock.Mock(spec=["proc_pidpath"])

        def invalid_image(_pid, buffer, _capacity):
            buffer.value = b"/private/\xff"
            return len(buffer.value)

        library.proc_pidpath.side_effect = invalid_image
        with mock.patch.object(executable_posix.ctypes, "CDLL", return_value=library):
            self.assert_runtime_unavailable(executable_posix._macos_process_image)

    def test_fixed_image_is_the_selector_and_geometry_or_inside_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _paths, exact = _attempt(root)
            fixed = root / "fixed-python"
            fixed.write_bytes(b"fixture executable")
            with mock.patch.object(
                executable_posix, "_observe_parent_process_executable", return_value=fixed,
            ) as observe, mock.patch.object(
                executable_posix.sys, "executable", str(root / "unselected-python"),
            ):
                self.assertEqual(
                    executable_posix.observe_fixed_package_runtime(exact.target, exact.scratch),
                    fixed,
                )
                observe.assert_called_once_with()
                observe.reset_mock()
                self.assert_runtime_unavailable(
                    lambda: executable_posix.observe_fixed_package_runtime(exact.target, root),
                )
                observe.assert_not_called()

            inside = exact.target / "python3"
            inside.write_bytes(b"target-controlled executable")
            for image in (inside, root / "missing-python"):
                with self.subTest(image=image.name), mock.patch.object(
                    executable_posix, "_observe_parent_process_executable", return_value=image,
                ):
                    self.assert_runtime_unavailable(
                        lambda: executable_posix.observe_fixed_package_runtime(
                            exact.target, exact.scratch,
                        ),
                    )


@unittest.skipUnless(POSIX_HOST, "requires an actual Linux/macOS host")
class PosixRunnerPreparationTests(unittest.TestCase):
    def test_actual_image_and_clean_environment_prepare_a_usable_fixed_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths, exact = _attempt(root)
            ambient = {
                "HOME": "/unselected/home", "TMPDIR": "/unselected/tmp",
                "PYTHONPATH": "/unselected/modules", "RUNNER_TEST_SECRET": "exclude-me",
            }
            with mock.patch.dict(os.environ, ambient), mock.patch.object(
                sys, "executable", "/unselected/python",
            ):
                before = dict(os.environ)
                executable = runtime.observe_fixed_package_runtime(exact.target, exact.scratch)
                environment = process.build_clean_environment(exact.scratch)
                self.assertEqual(dict(os.environ), before)
            self.assertTrue(executable.is_absolute())
            self.assertTrue(executable.is_file())
            self.assertFalse(executable.is_symlink())
            self.assertFalse(executable.is_relative_to(exact.root))
            self.assertEqual(environment, (
                ("HOME", str(exact.scratch / "home")),
                ("PYTHONDONTWRITEBYTECODE", "1"),
                ("PYTHONNOUSERSITE", "1"),
                ("PYTHONUTF8", "1"),
                ("TEMP", str(exact.scratch / "tmp")),
                ("TMP", str(exact.scratch / "tmp")),
                ("TMPDIR", str(exact.scratch / "tmp")),
            ))
            self.assertEqual(
                {child.name for child in exact.scratch.iterdir()},
                {"home", "tmp", "local", "roaming"},
            )
            # This fixed benign interpreter probe runs no materialized target
            # and does not claim that the POSIX Runner has launched.
            probe = subprocess.run(
                [str(executable), "-I", "-B", "-c",
                 "import os,tempfile; "
                 "assert os.path.isdir(os.environ['HOME']); "
                 "assert tempfile.gettempdir()==os.environ['TMPDIR']; "
                 "assert 'RUNNER_TEST_SECRET' not in os.environ; print('prepared')"],
                cwd=exact.scratch,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                shell=False,
                check=False,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(probe.stdout, b"prepared\n")
            self.assertEqual(lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID).state, "absent")

    def test_exact_commit_materialization_and_both_cleanup_locations_preserve_ambient(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repo = root / "repo"
            repo.mkdir()
            _git(repo, "init", "--quiet")
            (repo / "payload.txt").write_bytes(b"committed\n")
            marker = root / "target-code-ran"
            script = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n".encode("utf-8")
            (repo / "entry.py").write_bytes(script)
            _git(repo, "add", "--all")
            _git(repo, "-c", "user.name=Runner Test", "-c", "user.email=runner@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
            revision = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
            (repo / "payload.txt").write_bytes(b"dirty ambient\n")
            outside = root / "outside.bin"
            outside.write_bytes(b"preserve")
            before_status = _git(repo, "status", "--porcelain=v1", "-z")
            before_index = (repo / ".git" / "index").read_bytes()
            before_refs = _git(repo, "show-ref")
            observed = runner_git.observe_commit_runner_target(repo, revision)
            material = runner_git.preflight_runner_material(repo, observed)
            paths = lifecycle.verification_runner_state_paths(root / "runner")
            for quarantine in (False, True):
                with self.subTest(quarantine=quarantine), lifecycle.zero_wait_runner_lock(paths):
                    exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
                    lifecycle.create_scratch_directories(paths, ATTEMPT_ID)
                    process.build_clean_environment(exact.scratch)
                    result = runner_git.materialize_runner_target(repo, material, exact.target)
                    self.assertEqual(result.target_material_digest, material.target_material_digest)
                    self.assertEqual((exact.target / "payload.txt").read_bytes(), b"committed\n")
                    self.assertEqual((exact.target / "entry.py").read_bytes(), script)
                    (exact.target / "payload.txt").write_bytes(b"private modification\n")
                    (exact.scratch / "tmp" / "transient.bin").write_bytes(b"private")
                    if quarantine:
                        lifecycle.quarantine_attempt_tree(paths, ATTEMPT_ID)
                        self.assertFalse(exact.root.exists())
                        self.assertTrue(exact.quarantine.is_dir())
                    cleanup = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)
                    self.assertEqual(cleanup.state, "absent")
                    self.assertFalse(exact.root.exists())
                    self.assertFalse(exact.quarantine.exists())
                    self.assertEqual(lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID), cleanup)
            self.assertFalse(marker.exists())
            self.assertEqual(outside.read_bytes(), b"preserve")
            self.assertEqual((repo / "payload.txt").read_bytes(), b"dirty ambient\n")
            self.assertEqual((repo / ".git" / "index").read_bytes(), before_index)
            self.assertEqual(_git(repo, "show-ref"), before_refs)
            self.assertEqual(_git(repo, "status", "--porcelain=v1", "-z"), before_status)

    def test_symlink_preparation_and_cleanup_refuse_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths, exact = _attempt(root)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.bin"
            sentinel.write_bytes(b"preserve")
            home = exact.scratch / "home"
            home.rmdir()
            home.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(process.RunnerProcessError):
                process.build_clean_environment(exact.scratch)
            self.assertEqual(lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID).state, "uncertain")
            self.assertEqual(sentinel.read_bytes(), b"preserve")
            self.assertTrue(home.is_symlink())
            home.unlink()
            home.mkdir()
            image_link = root / "python-link"
            image_link.symlink_to(executable_posix._observe_parent_process_executable())
            with mock.patch.object(
                executable_posix, "_observe_parent_process_executable", return_value=image_link,
            ), self.assertRaises(runtime.VerificationRunnerRuntimeError):
                runtime.observe_fixed_package_runtime(exact.target, exact.scratch)
            self.assertEqual(lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID).state, "absent")
            self.assertEqual(sentinel.read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
