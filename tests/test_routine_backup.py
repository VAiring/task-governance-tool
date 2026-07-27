from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from tests.m14_test_support import SOURCE_SKILL_ROOT

from task_governance_tool import backup as backup_service
from task_governance_tool.backup import (
    discover_managed_backup_metadata,
    managed_backup_lock,
    run_routine_backup,
)
from task_governance_tool.storage import (
    MigrationBackupMetadata,
    StorageError,
    begin_initialized_write,
    configure_project_maintenance,
    connect_initialized,
    initialize_database,
    read_managed_backup_repository,
    record_backup_attempt_outcome,
    record_managed_backup,
    resolve_database_target,
)


SCRIPT_PATH = SOURCE_SKILL_ROOT / "scripts" / "taskgov.py"
BASE_TIME = datetime(2026, 7, 27, tzinfo=UTC)


def timestamp(minute: int) -> str:
    return (BASE_TIME + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")


def metadata(index: int, minute: int, retention: int) -> MigrationBackupMetadata:
    return MigrationBackupMetadata(
        generation_id=f"tg_backup_{index:032x}",
        published_at=timestamp(minute),
        publication_retention=retention,
    )


def make_target(
    root: Path,
    *,
    enabled: bool = True,
    interval: int = 30,
    generations: int = 3,
):
    repo = root / "project"
    repo.mkdir(parents=True)
    target = resolve_database_target(
        repo=repo,
        db=root / "state" / "taskgov.sqlite",
        script_path=SCRIPT_PATH,
    )
    initialize_database(target)
    if enabled:
        configure_project_maintenance(
            target,
            requested_interval_minutes=interval,
            requested_generations=generations,
            enabled_at=timestamp(0),
        )
    return target


def repository(target):
    return read_managed_backup_repository(target)


class RoutineBackupTests(unittest.TestCase):
    def test_opt_in_and_exact_due_offsets_attempt_only_zero_thirty_sixty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            disabled = make_target(root / "disabled", enabled=False)

            not_opted_in = run_routine_backup(
                disabled,
                observed_at=timestamp(0),
            )

            self.assertEqual(not_opted_in.code, "not_opted_in")
            self.assertFalse(not_opted_in.attempted)
            self.assertEqual(discover_managed_backup_metadata(disabled), ())

        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            observed = []
            real_copy = backup_service._copy

            def counted_copy(*args, **kwargs):
                observed.append(args[1].published_at)
                return real_copy(*args, **kwargs)

            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=counted_copy,
            ):
                results = [
                    run_routine_backup(target, observed_at=timestamp(minute))
                    for minute in (0, 1, 5, 29, 30, 31, 59, 60)
                ]

            self.assertEqual(
                [(item.code, item.attempted) for item in results],
                [
                    ("succeeded", True),
                    ("current", False),
                    ("current", False),
                    ("current", False),
                    ("succeeded", True),
                    ("current", False),
                    ("current", False),
                    ("succeeded", True),
                ],
            )
            self.assertEqual(
                observed,
                [timestamp(0), timestamp(30), timestamp(60)],
            )
            self.assertEqual(len(repository(target).generations), 3)
            self.assertEqual(len(discover_managed_backup_metadata(target)), 3)

    def test_rotation_and_reduced_policy_apply_only_after_next_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=3)
            for minute in (0, 30, 60):
                result = run_routine_backup(target, observed_at=timestamp(minute))
                self.assertEqual(result.code, "succeeded")

            before_change = repository(target)
            self.assertEqual(len(before_change.generations), 3)
            self.assertEqual(
                before_change.maintenance.applied_backup_generations,
                3,
            )

            configure_project_maintenance(
                target,
                requested_interval_minutes=None,
                requested_generations=1,
                enabled_at=timestamp(61),
            )
            configured = repository(target)
            self.assertEqual(configured.maintenance.backup_generations, 1)
            self.assertEqual(
                configured.maintenance.applied_backup_generations,
                3,
            )
            self.assertEqual(len(configured.generations), 3)

            not_due = run_routine_backup(target, observed_at=timestamp(89))
            self.assertEqual((not_due.code, not_due.attempted), ("current", False))
            self.assertEqual(len(repository(target).generations), 3)

            applied = run_routine_backup(target, observed_at=timestamp(90))
            self.assertEqual((applied.code, applied.attempted), ("succeeded", True))
            final = repository(target)
            self.assertEqual(final.maintenance.backup_generations, 1)
            self.assertEqual(final.maintenance.applied_backup_generations, 1)
            self.assertEqual(len(final.generations), 1)
            self.assertEqual(len(discover_managed_backup_metadata(target)), 1)
            self.assertEqual(final.generations[0].publication_retention, 1)

    def test_same_second_reverse_id_keeps_new_publication_and_applies_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=3)
            previous = metadata(
                int("f" * 32, 16),
                0,
                3,
            )
            backup_service._copy(target, previous)
            record_managed_backup(target, previous)
            configure_project_maintenance(
                target,
                requested_interval_minutes=None,
                requested_generations=1,
                enabled_at=timestamp(0),
            )
            record_backup_attempt_outcome(
                target,
                code="deferred",
                occurred_at=timestamp(0),
            )
            requested = metadata(0, 1, 1)

            with mock.patch.object(
                backup_service.secrets,
                "token_hex",
                return_value="0" * 32,
            ):
                result = run_routine_backup(
                    target,
                    observed_at=timestamp(0),
                )

            self.assertEqual((result.code, result.attempted), ("succeeded", True))
            state = repository(target)
            self.assertEqual(state.maintenance.backup_generations, 1)
            self.assertEqual(state.maintenance.applied_backup_generations, 1)
            self.assertEqual(
                state.maintenance.latest_backup_generation_id,
                requested.generation_id,
            )
            self.assertEqual(len(state.generations), 1)
            published = state.generations[0]
            self.assertEqual(published.generation_id, requested.generation_id)
            self.assertGreater(
                (published.published_at, published.generation_id),
                (previous.published_at, previous.generation_id),
            )
            self.assertEqual(
                discover_managed_backup_metadata(target),
                (published,),
            )

    def test_lock_deferred_and_copy_failure_both_remain_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))

            with managed_backup_lock(target):
                deferred = run_routine_backup(
                    target,
                    observed_at=timestamp(0),
                )

            self.assertEqual((deferred.code, deferred.attempted), ("deferred", True))
            deferred_state = repository(target).maintenance
            self.assertEqual(deferred_state.backup_last_outcome_code, "deferred")
            self.assertIsNone(deferred_state.backup_last_success_at)

            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=StorageError(
                    "setup_backup_failed",
                    "injected bounded copy failure",
                ),
            ) as failed_copy:
                failed = run_routine_backup(
                    target,
                    observed_at=timestamp(1),
                )

            self.assertEqual((failed.code, failed.attempted), ("failed", True))
            self.assertEqual(failed_copy.call_count, 1)
            failed_state = repository(target).maintenance
            self.assertEqual(failed_state.backup_last_outcome_code, "failed")
            self.assertIsNone(failed_state.backup_last_success_at)

            recovered = run_routine_backup(
                target,
                observed_at=timestamp(2),
            )
            self.assertEqual((recovered.code, recovered.attempted), ("succeeded", True))
            self.assertEqual(
                repository(target).maintenance.backup_last_outcome_code,
                "succeeded",
            )

    def test_copy_starts_without_a_held_sqlite_writer_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            real_copy = backup_service._copy
            observed = []

            def assert_writer_available(*args, **kwargs):
                with closing(connect_initialized(target)) as connection:
                    begin_initialized_write(connection, target)
                    observed.append(connection.in_transaction)
                    connection.rollback()
                return real_copy(*args, **kwargs)

            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=assert_writer_available,
            ):
                result = run_routine_backup(
                    target,
                    observed_at=timestamp(0),
                )

            self.assertEqual((result.code, result.attempted), ("succeeded", True))
            self.assertEqual(observed, [True])

    def test_file_only_generation_is_imported_without_second_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=3)
            orphan = metadata(1, 0, 3)
            backup_service._copy(target, orphan)

            configure_project_maintenance(
                target,
                requested_interval_minutes=None,
                requested_generations=1,
                enabled_at=timestamp(1),
            )
            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=AssertionError("recovery must not publish again"),
            ) as unexpected_copy:
                recovered = run_routine_backup(
                    target,
                    observed_at=timestamp(1),
                )

            self.assertEqual((recovered.code, recovered.attempted), ("current", True))
            unexpected_copy.assert_not_called()
            state = repository(target)
            self.assertEqual(state.maintenance.backup_generations, 1)
            self.assertEqual(state.maintenance.applied_backup_generations, 3)
            self.assertEqual(state.maintenance.latest_backup_generation_id, orphan.generation_id)
            self.assertEqual(state.generations, (orphan,))

    def test_row_commit_and_file_before_row_prune_crashes_are_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=1)
            first = metadata(1, 0, 1)
            second = metadata(2, 1, 1)
            for item in (first, second):
                backup_service._copy(target, item)
                record_managed_backup(target, item)

            self.assertEqual(len(repository(target).generations), 2)
            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=AssertionError("prune recovery must not republish"),
            ) as unexpected_copy:
                repaired = run_routine_backup(
                    target,
                    observed_at=timestamp(2),
                )
            self.assertEqual((repaired.code, repaired.attempted), ("current", True))
            unexpected_copy.assert_not_called()
            self.assertEqual(repository(target).generations, (second,))
            self.assertEqual(discover_managed_backup_metadata(target), (second,))

        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=1)
            first = metadata(3, 0, 1)
            second = metadata(4, 1, 1)
            for item in (first, second):
                backup_service._copy(target, item)
                record_managed_backup(target, item)

            backup_directory = target.db_path.parent / "backups"
            first_path = next(
                path
                for path in backup_directory.glob("taskgov-backup-v1_*.sqlite")
                if first.generation_id[10:] in path.name
            )
            first_path.unlink()

            with mock.patch.object(
                backup_service,
                "_copy",
                side_effect=AssertionError(
                    "incomplete reconciliation must not publish"
                ),
            ) as unexpected_copy:
                incomplete = run_routine_backup(
                    target,
                    observed_at=timestamp(2),
                )
            self.assertEqual((incomplete.code, incomplete.attempted), ("failed", True))
            unexpected_copy.assert_not_called()
            self.assertEqual(repository(target).generations, (second,))
            self.assertEqual(discover_managed_backup_metadata(target), (second,))

            completed = run_routine_backup(
                target,
                observed_at=timestamp(3),
            )
            self.assertEqual((completed.code, completed.attempted), ("succeeded", True))
            self.assertEqual(len(repository(target).generations), 1)
            self.assertEqual(len(discover_managed_backup_metadata(target)), 1)


if __name__ == "__main__":
    unittest.main()
