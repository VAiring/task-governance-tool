from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from tests.m14_test_support import SOURCE_SKILL_ROOT

from task_governance_tool import backup as backup_service
from task_governance_tool import tasks as tasks_service
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
    connect_existing,
    connect_initialized,
    initialize_database,
    read_managed_backup_repository,
    record_backup_attempt_outcome,
    record_managed_backup,
    resolve_database_target,
)
from task_governance_tool.tasks import add_task


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
    def test_copy_reuses_only_local_successful_privacy_checks_and_rechecks_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            title = "Backup privacy cache task"
            description = "Backup privacy cache description"
            with closing(connect_initialized(target)) as connection:
                add_task(
                    connection,
                    target.project,
                    database_target=target,
                    title=title,
                    description=description,
                )
                connection.commit()

            real_detector = tasks_service.reject_private_or_raw_content
            with mock.patch.object(
                tasks_service,
                "reject_private_or_raw_content",
                wraps=real_detector,
            ) as detector:
                backup_service._copy(target, metadata(1, 0, 3))

            observed = [call.args for call in detector.call_args_list]
            self.assertEqual(observed.count(("title", title)), 1)
            self.assertEqual(observed.count(("description", description)), 1)

        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with closing(connect_initialized(target)) as connection:
                task = add_task(
                    connection,
                    target.project,
                    database_target=target,
                    title="Backup drift task",
                    description="Initial safe description",
                ).task
                connection.commit()

            forbidden = "Authorization: private-backup-value"
            real_fsync = backup_service.os.fsync

            def mutate_current_after_copy(file_descriptor):
                real_fsync(file_descriptor)
                with closing(connect_existing(target.db_path)) as connection:
                    connection.execute(
                        "UPDATE tasks SET description = ? WHERE task_id = ?",
                        (forbidden, task["task_id"]),
                    )
                    connection.commit()

            with (
                mock.patch.object(
                    backup_service.os,
                    "fsync",
                    side_effect=mutate_current_after_copy,
                ),
                mock.patch.object(
                    backup_service.os,
                    "replace",
                    wraps=backup_service.os.replace,
                ) as replace_file,
                mock.patch.object(
                    tasks_service,
                    "reject_private_or_raw_content",
                    wraps=real_detector,
                ) as detector,
            ):
                with self.assertRaises(StorageError):
                    backup_service._copy(target, metadata(2, 1, 3))

            replace_file.assert_not_called()
            self.assertEqual(
                [call.args for call in detector.call_args_list].count(
                    ("description", forbidden)
                ),
                1,
            )
            self.assertEqual(discover_managed_backup_metadata(target), ())

    def test_routine_seeds_only_immediate_post_reconcile_and_clears_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            title = "Routine privacy seed task"
            description = "Routine privacy seed description"
            with closing(connect_initialized(target)) as connection:
                add_task(
                    connection,
                    target.project,
                    database_target=target,
                    title=title,
                    description=description,
                )
                connection.commit()

            real_copy = backup_service._copy
            real_reconcile = backup_service._reconcile_v11
            real_publication_validation = backup_service._validate_publication_source
            real_artifact_validation = backup_service._artifact_schema_version
            real_detector = tasks_service.reject_private_or_raw_content
            root_caches = []

            def capture_copy(*args, **kwargs):
                root_caches.append(kwargs["_privacy_success_cache"])
                return real_copy(*args, **kwargs)

            with (
                mock.patch.object(
                    backup_service,
                    "_copy",
                    side_effect=capture_copy,
                ),
                mock.patch.object(
                    backup_service,
                    "_reconcile_v11",
                    wraps=real_reconcile,
                ) as reconcile,
                mock.patch.object(
                    backup_service,
                    "_validate_publication_source",
                    wraps=real_publication_validation,
                ) as publication_validation,
                mock.patch.object(
                    backup_service,
                    "_artifact_schema_version",
                    wraps=real_artifact_validation,
                ) as artifact_validation,
                mock.patch.object(
                    tasks_service,
                    "reject_private_or_raw_content",
                    wraps=real_detector,
                ) as detector,
            ):
                first = run_routine_backup(target, observed_at=timestamp(0))

                self.assertEqual((first.code, first.attempted), ("succeeded", True))
                observed = [call.args for call in detector.call_args_list]
                self.assertEqual(observed.count(("title", title)), 1)
                self.assertEqual(observed.count(("description", description)), 1)
                self.assertEqual(publication_validation.call_count, 3)
                self.assertEqual(artifact_validation.call_count, 1)
                self.assertEqual(len(reconcile.call_args_list), 2)
                self.assertIsNone(
                    reconcile.call_args_list[0].kwargs.get("_privacy_success_seed")
                )
                first_seed = reconcile.call_args_list[1].kwargs[
                    "_privacy_success_seed"
                ]
                self.assertIs(type(first_seed), frozenset)
                self.assertTrue(first_seed)
                self.assertEqual(root_caches[0], set())

                detector.reset_mock()
                publication_validation.reset_mock()
                artifact_validation.reset_mock()
                reconcile.reset_mock()

                second = run_routine_backup(target, observed_at=timestamp(30))

                self.assertEqual(
                    (second.code, second.attempted),
                    ("succeeded", True),
                )
                observed = [call.args for call in detector.call_args_list]
                self.assertEqual(observed.count(("title", title)), 2)
                self.assertEqual(observed.count(("description", description)), 2)
                self.assertEqual(publication_validation.call_count, 3)
                self.assertEqual(artifact_validation.call_count, 3)
                self.assertEqual(len(reconcile.call_args_list), 2)
                self.assertIsNone(
                    reconcile.call_args_list[0].kwargs.get("_privacy_success_seed")
                )
                second_seed = reconcile.call_args_list[1].kwargs[
                    "_privacy_success_seed"
                ]
                self.assertIs(type(second_seed), frozenset)
                self.assertTrue(second_seed)

            self.assertEqual(len(root_caches), 2)
            self.assertIsNot(root_caches[0], root_caches[1])
            self.assertTrue(all(cache == set() for cache in root_caches))

    def test_post_reconcile_artifacts_copy_seed_without_cross_artifact_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            for item in (metadata(1, 0, 3), metadata(2, 1, 3)):
                backup_service._copy(target, item)
                record_managed_backup(target, item)

            seed = frozenset({("ordinary", "title", "seed-only-value")})
            artifact_local = (
                "ordinary",
                "description",
                "artifact-local-value",
            )
            real_validator = backup_service.validate_evidence_ledger_storage
            observed_before = []
            local_caches = []

            def observe_artifact_cache(connection, **kwargs):
                cache = kwargs["_privacy_success_cache"]
                observed_before.append(set(cache))
                local_caches.append(cache)
                result = real_validator(connection, **kwargs)
                cache.add(artifact_local)
                return result

            with mock.patch.object(
                backup_service,
                "validate_evidence_ledger_storage",
                side_effect=observe_artifact_cache,
            ):
                artifacts = backup_service._discover(
                    target,
                    _privacy_success_seed=seed,
                )

            self.assertEqual(len(artifacts), 2)
            self.assertEqual(observed_before, [set(seed), set(seed)])
            self.assertTrue(all(cache == set() for cache in local_caches))
            self.assertEqual(seed, frozenset({("ordinary", "title", "seed-only-value")}))

            with mock.patch.object(
                backup_service,
                "_artifact_schema_version",
                wraps=backup_service._artifact_schema_version,
            ) as artifact_validation:
                with self.assertRaises(StorageError):
                    backup_service._discover(
                        target,
                        _privacy_success_seed=frozenset(
                            {("unsupported", "title", "invalid-mode")}
                        ),
                    )
            artifact_validation.assert_not_called()
            with self.assertRaises(StorageError):
                backup_service._copy(
                    target,
                    metadata(3, 2, 3),
                    _privacy_success_cache={
                        ("ordinary", "title", "preseeded-root")
                    },
                )

    def test_post_reconcile_rechecks_new_artifact_value_and_clears_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with closing(connect_initialized(target)) as connection:
                task = add_task(
                    connection,
                    target.project,
                    database_target=target,
                    title="Post reconcile drift task",
                    description="Initial post reconcile description",
                ).task
                connection.commit()

            forbidden = "Authorization: post-reconcile-private-value"
            real_copy = backup_service._copy
            real_record = backup_service.record_managed_backup
            real_detector = tasks_service.reject_private_or_raw_content
            root_caches = []
            mutated_artifacts = []

            def capture_copy(*args, **kwargs):
                root_caches.append(kwargs["_privacy_success_cache"])
                return real_copy(*args, **kwargs)

            def record_then_mutate(*args, **kwargs):
                result = real_record(*args, **kwargs)
                published = args[1]
                artifact_path = (
                    target.resolved_backups_path
                    / backup_service._filename(published)
                )
                mutated_artifacts.append(artifact_path)
                with closing(connect_existing(artifact_path)) as connection:
                    connection.execute(
                        "UPDATE tasks SET description = ? WHERE task_id = ?",
                        (forbidden, task["task_id"]),
                    )
                    connection.commit()
                return result

            with (
                mock.patch.object(
                    backup_service,
                    "_copy",
                    side_effect=capture_copy,
                ),
                mock.patch.object(
                    backup_service,
                    "record_managed_backup",
                    side_effect=record_then_mutate,
                ),
                mock.patch.object(
                    tasks_service,
                    "reject_private_or_raw_content",
                    wraps=real_detector,
                ) as detector,
            ):
                result = run_routine_backup(target, observed_at=timestamp(0))

            self.assertEqual((result.code, result.attempted), ("failed", True))
            self.assertEqual(len(root_caches), 1)
            self.assertEqual(root_caches[0], set())
            self.assertEqual(
                [call.args for call in detector.call_args_list].count(
                    ("description", forbidden)
                ),
                1,
            )
            self.assertEqual(repository(target).generations, ())
            self.assertEqual(discover_managed_backup_metadata(target), ())
            self.assertEqual(len(mutated_artifacts), 1)
            self.assertTrue(mutated_artifacts[0].is_file())

    def test_routine_clears_copy_cache_when_generation_record_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            real_copy = backup_service._copy
            root_caches = []

            def capture_copy(*args, **kwargs):
                root_caches.append(kwargs["_privacy_success_cache"])
                return real_copy(*args, **kwargs)

            with (
                mock.patch.object(
                    backup_service,
                    "_copy",
                    side_effect=capture_copy,
                ),
                mock.patch.object(
                    backup_service,
                    "record_managed_backup",
                    side_effect=StorageError(
                        "internal_error",
                        "injected generation record failure",
                    ),
                ),
            ):
                result = run_routine_backup(target, observed_at=timestamp(0))

            self.assertEqual((result.code, result.attempted), ("failed", True))
            self.assertEqual(len(root_caches), 1)
            self.assertEqual(root_caches[0], set())

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

    def test_routine_metadata_uses_the_reconciled_repository_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=3)
            first = run_routine_backup(target, observed_at=timestamp(0))
            self.assertEqual((first.code, first.attempted), ("succeeded", True))
            previous = repository(target).generations[-1]
            observed_after = []
            real_new_metadata = backup_service._new_metadata

            def capture_reconciled_after(*args, **kwargs):
                observed_after.append(kwargs["after"])
                return real_new_metadata(*args, **kwargs)

            with (
                mock.patch.object(
                    backup_service,
                    "_new_publication_metadata",
                    side_effect=AssertionError(
                        "routine publication must not rediscover reconciled files"
                    ),
                ) as rediscovery,
                mock.patch.object(
                    backup_service,
                    "_new_metadata",
                    side_effect=capture_reconciled_after,
                ),
            ):
                second = run_routine_backup(target, observed_at=timestamp(30))

            self.assertEqual((second.code, second.attempted), ("succeeded", True))
            rediscovery.assert_not_called()
            self.assertEqual(observed_after, [previous])
            self.assertGreater(
                repository(target).generations[-1].published_at,
                previous.published_at,
            )

    def test_routine_metadata_collision_and_final_reconcile_failure_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=3)
            first = run_routine_backup(target, observed_at=timestamp(0))
            self.assertEqual((first.code, first.attempted), ("succeeded", True))
            previous = repository(target).generations[-1]

            with mock.patch.object(
                backup_service,
                "_new_metadata",
                return_value=previous,
            ):
                collision = run_routine_backup(target, observed_at=timestamp(30))

            self.assertEqual(
                (collision.code, collision.attempted),
                ("failed", True),
            )
            self.assertEqual(repository(target).generations, (previous,))
            self.assertEqual(discover_managed_backup_metadata(target), (previous,))

        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), generations=3)
            first = run_routine_backup(target, observed_at=timestamp(0))
            self.assertEqual((first.code, first.attempted), ("succeeded", True))

            with mock.patch.object(
                backup_service,
                "_reconcile_v11",
                side_effect=(True, False),
            ) as reconcile:
                failed = run_routine_backup(target, observed_at=timestamp(30))

            self.assertEqual((failed.code, failed.attempted), ("failed", True))
            self.assertEqual(reconcile.call_count, 2)
            self.assertEqual(
                repository(target).maintenance.backup_last_outcome_code,
                "failed",
            )
            self.assertEqual(len(repository(target).generations), 2)
            self.assertEqual(len(discover_managed_backup_metadata(target)), 2)

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
