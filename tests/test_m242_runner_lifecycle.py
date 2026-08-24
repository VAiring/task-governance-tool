from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import verification_runner_lifecycle as lifecycle  # noqa: E402


ATTEMPT_ID = "tg_verification_runner_attempt_0123456789abcdef"
FOREIGN_ATTEMPT_ID = "tg_verification_runner_attempt_fedcba9876543210"


def _paths(temporary: str) -> lifecycle.VerificationRunnerStatePaths:
    return lifecycle.verification_runner_state_paths(
        Path(temporary).resolve() / "runner"
    )


class RunnerLifecyclePureTests(unittest.TestCase):
    def test_closed_paths_and_private_result_validate_exact_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "runner"
            paths = lifecycle.verification_runner_state_paths(root)
            self.assertEqual(paths.attempts, root / "attempts")
            self.assertNotIn(str(root), repr(paths))
            with self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
                lifecycle.VerificationRunnerStatePaths(
                    root=root,
                    lock=root / "wrong.lock",
                    attempts=root / "attempts",
                    quarantine=root / "quarantine",
                )

        result = lifecycle.RunnerPrivateTreeResultV1(ATTEMPT_ID, "absent")
        self.assertEqual(asdict(result), {"attempt_id": ATTEMPT_ID, "state": "absent"})
        with self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
            lifecycle.RunnerPrivateTreeResultV1(ATTEMPT_ID, "deleted")
        with self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
            lifecycle.RunnerPrivateTreeResultV1("bad", "uncertain")
        self.assertFalse(hasattr(lifecycle, "remove_attempt_tree"))

    def test_absent_cleanup_is_idempotent_and_returns_no_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            first = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)
            second = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

        self.assertEqual(first, lifecycle.RunnerPrivateTreeResultV1(ATTEMPT_ID, "absent"))
        self.assertEqual(second, first)
        self.assertNotIn(temporary, repr(first))

    def test_foreign_attempt_preflight_is_uncertain_and_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            foreign = lifecycle.create_attempt_directories(paths, FOREIGN_ATTEMPT_ID)
            sentinel = foreign.target / "preserve.bin"
            sentinel.write_bytes(b"preserve")

            result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "uncertain")
            self.assertEqual(sentinel.read_bytes(), b"preserve")
            self.assertTrue(foreign.root.is_dir())
            self.assertFalse(foreign.quarantine.exists())

    def test_foreign_quarantine_before_final_proof_is_uncertain_and_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            original_remove = lifecycle._remove_attempt_tree_or_raise

            def remove_then_inject_foreign_quarantine(*args, **kwargs):
                original_remove(*args, **kwargs)
                foreign = lifecycle.create_attempt_directories(
                    paths,
                    FOREIGN_ATTEMPT_ID,
                )
                (foreign.target / "preserve.bin").write_bytes(b"preserve")
                lifecycle.quarantine_attempt_tree(paths, FOREIGN_ATTEMPT_ID)

            with mock.patch.object(
                lifecycle,
                "_remove_attempt_tree_or_raise",
                side_effect=remove_then_inject_foreign_quarantine,
            ):
                result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            foreign = lifecycle.attempt_paths(paths, FOREIGN_ATTEMPT_ID)
            self.assertEqual(result.state, "uncertain")
            self.assertEqual(
                (foreign.quarantine / "target" / "preserve.bin").read_bytes(),
                b"preserve",
            )
            self.assertFalse(foreign.root.exists())
            self.assertTrue(foreign.quarantine.is_dir())

    def test_populated_attempt_is_removed_with_post_absence_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            outside = root / "outside.bin"
            outside.write_bytes(b"preserve")
            paths = lifecycle.verification_runner_state_paths(root / "runner")
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            lifecycle.create_scratch_directories(paths, ATTEMPT_ID)
            nested = exact.target / "pkg" / "checks"
            nested.mkdir(parents=True)
            (nested / "run.py").write_bytes(b"raise SystemExit(0)\n")
            (exact.scratch / "tmp" / "transient.bin").write_bytes(b"output")

            result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "absent")
            self.assertFalse(exact.root.exists())
            self.assertFalse(exact.quarantine.exists())
            self.assertEqual(outside.read_bytes(), b"preserve")

    def test_quarantined_attempt_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            (exact.target / "one.bin").write_bytes(b"one")
            lifecycle.quarantine_attempt_tree(paths, ATTEMPT_ID)

            result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "absent")
            self.assertFalse(exact.root.exists())
            self.assertFalse(exact.quarantine.exists())

    def test_double_presence_is_uncertain_and_deletes_neither(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            exact.quarantine.mkdir()

            result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "uncertain")
            self.assertTrue(exact.root.is_dir())
            self.assertTrue(exact.quarantine.is_dir())

    def test_removal_or_post_proof_uncertainty_returns_closed_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            with mock.patch.object(
                lifecycle,
                "_remove_validated_tree",
                side_effect=lifecycle.StatePathError(),
            ):
                removal = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)
            with mock.patch.object(
                lifecycle,
                "_attempt_absence_is_proved",
                return_value=False,
            ), mock.patch.object(
                lifecycle,
                "_remove_attempt_tree_or_raise",
                return_value=None,
            ):
                post_proof = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            before = lifecycle._capture_owner_directory_identities(paths)
            changed = lifecycle._RunnerOwnerDirectoryIdentities(
                root=lifecycle.DirectoryIdentity(-1, -1),
                attempts=before.attempts,
                quarantine=before.quarantine,
            )
            with mock.patch.object(
                lifecycle,
                "_capture_owner_directory_identities",
                side_effect=(before, changed),
            ), mock.patch.object(
                lifecycle,
                "_remove_attempt_tree_or_raise",
                return_value=None,
            ):
                owner_swap = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

        self.assertEqual(removal.state, "uncertain")
        self.assertEqual(post_proof.state, "uncertain")
        self.assertEqual(owner_swap.state, "uncertain")

    def test_owner_identity_change_before_delete_preserves_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            (exact.target / "preserve.bin").write_bytes(b"preserve")
            before = lifecycle._capture_owner_directory_identities(paths)
            changed = lifecycle._RunnerOwnerDirectoryIdentities(
                root=lifecycle.DirectoryIdentity(-1, -1),
                attempts=before.attempts,
                quarantine=before.quarantine,
            )
            with mock.patch.object(
                lifecycle,
                "_capture_owner_directory_identities",
                side_effect=(before, changed),
            ):
                result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "uncertain")
            self.assertEqual((exact.target / "preserve.bin").read_bytes(), b"preserve")

    def test_sparse_file_beyond_materialization_bound_is_cleanup_eligible(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            lifecycle.create_scratch_directories(paths, ATTEMPT_ID)
            large = exact.target / "large-sparse.bin"
            with large.open("wb") as stream:
                stream.seek(536_870_912)
                stream.write(b"x")
            (exact.scratch / "tmp" / "small.bin").write_bytes(b"scratch")

            result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "absent")
            self.assertFalse(exact.root.exists())

    def test_delete_deadline_is_checked_before_and_after_each_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            first = exact.target / "first.bin"
            first.write_bytes(b"first")
            first_path, first_identity = lifecycle.inspect_physical_file(
                first,
                root=paths.root,
            )
            validated = lifecycle._OwnedDeletionFile(first_path, first_identity)
            with mock.patch.object(
                lifecycle.time,
                "monotonic",
                return_value=11.0,
            ), self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
                lifecycle._remove_validated_tree(
                    root=paths.root,
                    files=(validated,),
                    directories=(),
                    deadline=10.0,
                )
            self.assertTrue(first.exists())

            with mock.patch.object(
                lifecycle.time,
                "monotonic",
                side_effect=(9.0, 11.0),
            ), self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
                lifecycle._remove_validated_tree(
                    root=paths.root,
                    files=(validated,),
                    directories=(),
                    deadline=10.0,
                )
            self.assertFalse(first.exists())

    def test_owned_file_identity_is_rechecked_immediately_before_unlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            file = exact.target / "replaceable.bin"
            file.write_bytes(b"first")
            file_path, first_identity = lifecycle.inspect_physical_file(
                file,
                root=paths.root,
            )
            owned = lifecycle._OwnedDeletionFile(file_path, first_identity)
            changed_identity = lifecycle.FileIdentity(
                device=first_identity.device,
                inode=first_identity.inode,
                size=first_identity.size + 1,
                modified_ns=first_identity.modified_ns,
            )

            with mock.patch.object(
                lifecycle,
                "inspect_physical_file",
                return_value=(file_path, changed_identity),
            ), self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
                lifecycle._remove_validated_tree(
                    root=paths.root,
                    files=(owned,),
                    directories=(),
                    deadline=float("inf"),
                )

            self.assertEqual(file.read_bytes(), b"first")

    def test_owner_identity_change_after_absence_blocks_absent_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            before = lifecycle._capture_owner_directory_identities(paths)
            changed = lifecycle._RunnerOwnerDirectoryIdentities(
                root=before.root,
                attempts=lifecycle.DirectoryIdentity(-1, -1),
                quarantine=before.quarantine,
            )
            with mock.patch.object(
                lifecycle,
                "_capture_owner_directory_identities",
                side_effect=(before, before, changed),
            ):
                result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "uncertain")

    def test_invalid_attempt_id_is_admission_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = _paths(temporary)
            lifecycle.ensure_runner_layout(paths)
            with self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
                lifecycle.cleanup_attempt_tree(paths, "../outside")
            with self.assertRaises(lifecycle.VerificationRunnerLifecycleError):
                lifecycle.cleanup_attempt_tree(object(), ATTEMPT_ID)


@unittest.skipUnless(os.name == "nt", "junction proof is Windows-only")
class RunnerLifecycleWindowsTests(unittest.TestCase):
    def test_reparse_inside_attempt_is_uncertain_and_outside_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.bin"
            sentinel.write_bytes(b"preserve")
            paths = lifecycle.verification_runner_state_paths(root / "runner")
            lifecycle.ensure_runner_layout(paths)
            exact = lifecycle.create_attempt_directories(paths, ATTEMPT_ID)
            linked = exact.target / "linked"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest("Windows junction creation unavailable")

            result = lifecycle.cleanup_attempt_tree(paths, ATTEMPT_ID)

            self.assertEqual(result.state, "uncertain")
            self.assertEqual(sentinel.read_bytes(), b"preserve")
            self.assertTrue(exact.root.exists())


if __name__ == "__main__":
    unittest.main()
