"""Focused native artifact operations and portable fail-closed checks."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import artifact_lock, linux_no_replace, no_replace  # noqa: E402
from task_governance_tool.state_paths import (  # noqa: E402
    StatePathError,
    hash_physical_file,
    inspect_physical_directory,
)


_LOCK_PROBE = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool.artifact_lock import ArtifactLockError, zero_wait_artifact_lock
try:
    with zero_wait_artifact_lock(Path(sys.argv[2])):
        print("acquired")
except ArtifactLockError as exc:
    if not exc.contended:
        raise
    print("contended")
"""


class ArtifactLockOperationTests(unittest.TestCase):
    @unittest.skipUnless(
        os.name == "nt" or sys.platform == "linux",
        "native lock contention is enabled on Windows and Linux",
    )
    def test_same_process_contention_preserves_outer_lock_and_reuses_after_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "artifact.lock"
            with artifact_lock.zero_wait_artifact_lock(path) as contents:
                self.assertEqual(contents, b"\0")
                for _ in range(2):
                    with self.assertRaises(artifact_lock.ArtifactLockError) as caught:
                        with artifact_lock.zero_wait_artifact_lock(path):
                            self.fail("a second open acquired the held lock")
                    self.assertTrue(caught.exception.contended)

            with artifact_lock.zero_wait_artifact_lock(path) as contents:
                self.assertEqual(contents, b"\0")
            self.assertEqual(path.read_bytes(), b"\0")

    @unittest.skipUnless(
        os.name == "nt" or sys.platform == "linux",
        "native lock contention is enabled on Windows and Linux",
    )
    def test_independent_process_contends_then_acquires_after_release(self):
        def probe(path: Path) -> str:
            result = subprocess.run(
                [sys.executable, "-B", "-c", _LOCK_PROBE, str(SCRIPTS_ROOT), str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
                shell=False,
                close_fds=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "artifact.lock"
            with artifact_lock.zero_wait_artifact_lock(path):
                self.assertEqual(probe(path), "contended")
            self.assertEqual(probe(path), "acquired")
            self.assertEqual(path.read_bytes(), b"\0")

    def test_exception_exit_releases_lock_for_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "artifact.lock"
            with self.assertRaisesRegex(RuntimeError, "injected body failure"):
                with artifact_lock.zero_wait_artifact_lock(path):
                    raise RuntimeError("injected body failure")
            with artifact_lock.zero_wait_artifact_lock(path) as contents:
                self.assertEqual(contents, b"\0")

    def test_acquire_failure_closes_descriptor_and_preserves_error_classification(self):
        for code, contended in ((errno.EAGAIN, True), (errno.EIO, False)):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "artifact.lock"
                with mock.patch.object(
                    artifact_lock, "_acquire", side_effect=OSError(code, "injected")
                ) as acquire:
                    with self.assertRaises(artifact_lock.ArtifactLockError) as caught:
                        with artifact_lock.zero_wait_artifact_lock(path):
                            self.fail("failed acquisition entered the body")
                self.assertEqual(caught.exception.contended, contended)
                descriptor = acquire.call_args.args[0]
                with self.assertRaises(OSError) as closed:
                    os.fstat(descriptor)
                self.assertEqual(closed.exception.errno, errno.EBADF)
                with artifact_lock.zero_wait_artifact_lock(path):
                    pass


class LinuxNoReplaceOperationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "requires native Linux renameat2")
    def test_racing_file_or_empty_directory_destination_is_never_replaced(self):
        native_move = linux_no_replace.rename_no_replace
        for kind in ("file", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source"
                destination = root / "destination"
                if kind == "file":
                    source.write_bytes(b"source")
                    validated = hash_physical_file(source, root=root)
                else:
                    source.mkdir()
                    (source / "child").write_bytes(b"source")
                    validated = inspect_physical_directory(source, root=root)
                raced_identity = None

                def race(admitted_source: Path, admitted_destination: Path) -> None:
                    nonlocal raced_identity
                    if kind == "file":
                        admitted_destination.write_bytes(b"destination")
                    else:
                        admitted_destination.mkdir()
                    details = admitted_destination.lstat()
                    raced_identity = (details.st_dev, details.st_ino)
                    native_move(admitted_source, admitted_destination)

                with (
                    mock.patch.object(linux_no_replace, "rename_no_replace", side_effect=race) as move,
                    mock.patch.object(os, "rename", side_effect=AssertionError("overwrite fallback")) as rename,
                    mock.patch.object(os, "replace", side_effect=AssertionError("overwrite fallback")) as replace,
                ):
                    with self.assertRaises(StatePathError) as caught:
                        no_replace.rename_no_replace(validated, destination, root=root)
                    move.assert_called_once_with(source, destination)
                    rename.assert_not_called()
                    replace.assert_not_called()
                self.assertEqual(caught.exception.__cause__.errno, errno.EEXIST)
                details = destination.lstat()
                self.assertEqual((details.st_dev, details.st_ino), raced_identity)
                if kind == "file":
                    self.assertEqual(source.read_bytes(), b"source")
                    self.assertEqual(destination.read_bytes(), b"destination")
                else:
                    self.assertEqual((source / "child").read_bytes(), b"source")
                    self.assertEqual(list(destination.iterdir()), [])

    def test_unavailable_api_and_filesystem_rejection_fail_without_overwrite_fallback(self):
        for failure, expected_errno in (
            ("loader", errno.ENOSYS),
            ("symbol", errno.ENOSYS),
            ("kernel", errno.ENOSYS),
            ("filesystem", errno.EINVAL),
        ):
            for kind in ("file", "directory"):
                with self.subTest(failure=failure, kind=kind), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    source = root / "source"
                    destination = root / "destination"
                    if kind == "file":
                        source.write_bytes(b"source")
                        validated = hash_physical_file(source, root=root)
                    else:
                        source.mkdir()
                        (source / "child").write_bytes(b"source")
                        validated = inspect_physical_directory(source, root=root)
                    syscall = mock.Mock(return_value=-1)
                    library = (
                        SimpleNamespace()
                        if failure == "symbol"
                        else SimpleNamespace(renameat2=syscall)
                    )
                    with (
                        mock.patch.object(sys, "platform", "linux"),
                        mock.patch.object(
                            linux_no_replace.ctypes,
                            "CDLL",
                            return_value=library,
                            side_effect=OSError("unavailable") if failure == "loader" else None,
                        ) as load,
                        mock.patch.object(linux_no_replace.ctypes, "get_errno", return_value=expected_errno),
                        mock.patch.object(os, "rename", side_effect=AssertionError("overwrite fallback")) as rename,
                        mock.patch.object(os, "replace", side_effect=AssertionError("overwrite fallback")) as replace,
                    ):
                        with self.assertRaises(StatePathError) as caught:
                            no_replace.rename_no_replace(validated, destination, root=root)
                        load.assert_called_once_with(None, use_errno=True)
                        rename.assert_not_called()
                        replace.assert_not_called()
                    self.assertEqual(caught.exception.code, "state_path_invalid")
                    self.assertEqual(caught.exception.__cause__.errno, expected_errno)
                    if failure in {"kernel", "filesystem"}:
                        syscall.assert_called_once_with(
                            -100, os.fsencode(source), -100, os.fsencode(destination), 1
                        )
                    else:
                        syscall.assert_not_called()
                    self.assertFalse(destination.exists())
                    if kind == "file":
                        self.assertEqual(source.read_bytes(), b"source")
                    else:
                        self.assertEqual((source / "child").read_bytes(), b"source")


if __name__ == "__main__":
    unittest.main()
