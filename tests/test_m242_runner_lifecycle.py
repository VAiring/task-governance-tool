import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    import task_governance_tool.verification_runner_lifecycle as lifecycle_module
    from task_governance_tool.state_paths import verification_runner_state_paths
    from task_governance_tool.verification_runner_lifecycle import (
        VerificationRunnerLifecycleError,
        create_scratch_directories,
        create_attempt_directories,
        inspect_runner_layout,
        quarantine_attempt_tree,
        remove_attempt_tree,
        require_known_attempt_inventory,
        validate_empty_attempt_tree,
        zero_wait_runner_lock,
    )
finally:
    sys.path.pop(0)


ATTEMPT_ID = "tg_verification_runner_attempt_0123456789abcdef"


class RunnerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixed_root = Path(self.temporary.name) / "current"
        self.fixed_root.mkdir()
        self.paths = verification_runner_state_paths(self.fixed_root)

    def test_lock_creates_only_fixed_layout_and_attempt_tree_is_exact(self):
        with zero_wait_runner_lock(self.paths) as inventory:
            self.assertEqual(inventory.attempt_ids, ())
            self.assertEqual(inventory.quarantine_ids, ())
            created = create_attempt_directories(self.paths, ATTEMPT_ID)
            self.assertEqual(validate_empty_attempt_tree(self.paths, ATTEMPT_ID), created)
            self.assertEqual(
                {entry.name for entry in created.root.iterdir()},
                {"target", "scratch"},
            )
            scratch = create_scratch_directories(self.paths, ATTEMPT_ID)
            self.assertEqual(
                {path.name for path in scratch},
                {"tmp", "home", "local", "roaming"},
            )

    def test_quarantine_cleanup_is_bounded_to_the_db_named_attempt(self):
        with zero_wait_runner_lock(self.paths):
            created = create_attempt_directories(self.paths, ATTEMPT_ID)
            (created.target / "fixture.py").write_bytes(b"print('offline')\n")
            quarantine_attempt_tree(self.paths, ATTEMPT_ID)
            inventory = inspect_runner_layout(self.paths)
            self.assertEqual(inventory.attempt_ids, ())
            self.assertEqual(inventory.quarantine_ids, (ATTEMPT_ID,))
            require_known_attempt_inventory(
                inventory,
                known_attempt_ids=(ATTEMPT_ID,),
            )
            remove_attempt_tree(self.paths, ATTEMPT_ID)
            self.assertEqual(inspect_runner_layout(self.paths).quarantine_ids, ())

    def test_cleanup_traversal_stops_at_cap_sentinel_and_preserves_tree(self):
        with zero_wait_runner_lock(self.paths):
            created = create_attempt_directories(self.paths, ATTEMPT_ID)
            for ordinal in range(8):
                (created.target / f"fixture-{ordinal:02d}.py").write_bytes(b"pass\n")

            real_scandir = os.scandir
            target_reads = 0

            class LazyScandir:
                def __init__(self, directory):
                    self.directory = Path(directory)
                    self.iterator = real_scandir(directory)

                def __enter__(self):
                    self.iterator.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.iterator.__exit__(exc_type, exc, traceback)

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal target_reads
                    if self.directory == created.target:
                        target_reads += 1
                        if target_reads > 4:
                            raise AssertionError("enumerated beyond the cap sentinel")
                    return next(self.iterator)

            with (
                mock.patch.object(
                    lifecycle_module,
                    "_MAX_ATTEMPT_TRAVERSAL_ENTRIES",
                    7,
                ),
                mock.patch.object(
                    lifecycle_module.os,
                    "scandir",
                    side_effect=LazyScandir,
                ),
            ):
                with self.assertRaises(VerificationRunnerLifecycleError):
                    remove_attempt_tree(self.paths, ATTEMPT_ID)

            self.assertEqual(target_reads, 4)
            self.assertTrue(created.root.is_dir())
            self.assertEqual(
                {entry.name for entry in created.target.iterdir()},
                {f"fixture-{ordinal:02d}.py" for ordinal in range(8)},
            )

    def test_cleanup_traversal_checks_deadline_after_each_lazy_read(self):
        directory = self.fixed_root / "deadline-fixture"
        directory.mkdir()
        (directory / "entry.py").write_bytes(b"pass\n")
        real_scandir = os.scandir
        reads = 0

        class LazyScandir:
            def __init__(self, path):
                self.iterator = real_scandir(path)

            def __enter__(self):
                self.iterator.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                return self.iterator.__exit__(exc_type, exc, traceback)

            def __iter__(self):
                return self

            def __next__(self):
                nonlocal reads
                reads += 1
                if reads > 1:
                    raise AssertionError("deadline did not stop enumeration")
                return next(self.iterator)

        budget = lifecycle_module._TraversalBudget(
            maximum_entries=10,
            deadline=100.0,
        )
        with (
            mock.patch.object(
                lifecycle_module.os,
                "scandir",
                side_effect=LazyScandir,
            ),
            mock.patch.object(
                lifecycle_module.time,
                "monotonic",
                side_effect=(99.0, 101.0),
            ),
        ):
            with self.assertRaises(VerificationRunnerLifecycleError):
                lifecycle_module._bounded_sorted_children(
                    directory,
                    budget=budget,
                )
        self.assertEqual(reads, 1)
        self.assertEqual(budget.observed_entries, 0)

    def test_unknown_attempt_owner_fails_closed(self):
        unknown = "tg_verification_runner_attempt_fedcba9876543210"
        with zero_wait_runner_lock(self.paths):
            create_attempt_directories(self.paths, unknown)
            with self.assertRaises(VerificationRunnerLifecycleError) as raised:
                require_known_attempt_inventory(
                    inspect_runner_layout(self.paths),
                    known_attempt_ids=(ATTEMPT_ID,),
                )
        self.assertEqual(raised.exception.code, "runner_state_invalid")

    def test_unexpected_runner_root_child_fails_closed(self):
        with zero_wait_runner_lock(self.paths):
            (self.paths.root / "unexpected.txt").write_bytes(b"x")
            with self.assertRaises(VerificationRunnerLifecycleError):
                inspect_runner_layout(self.paths)

    def test_second_controller_fails_zero_wait_while_first_holds_lock(self):
        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def first_controller() -> None:
            try:
                with zero_wait_runner_lock(self.paths):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("fixture release was not observed")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=first_controller)
        worker.start()
        self.assertTrue(entered.wait(5))
        try:
            with self.assertRaises(VerificationRunnerLifecycleError) as raised:
                with zero_wait_runner_lock(self.paths):
                    self.fail("contending controller entered the Runner lock")
            self.assertEqual(raised.exception.code, "runner_busy")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
