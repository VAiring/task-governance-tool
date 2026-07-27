import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import create_v10_target, create_v9_target


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import backup as backup_service  # noqa: E402
from task_governance_tool import storage as storage_service  # noqa: E402
from task_governance_tool.backup import (  # noqa: E402
    managed_backup_lock,
    publish_setup_backup,
)
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    connect_readonly,
    initialize_database,
    read_managed_backup_repository,
    read_project_maintenance,
    resolve_database_target,
)


SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"


def make_target(root: Path, name: str = "project"):
    repo = root / name
    repo.mkdir()
    return resolve_database_target(
        repo=repo,
        db=root / f"{name}-state" / "taskgov.sqlite",
        script_path=SCRIPT_PATH,
    )


def backup_directory(target) -> Path:
    return target.db_path.parent / "backups"


def deterministic_metadata(index: int) -> tuple[str, str]:
    return (
        f"2026-07-27T00:00:{index:02d}Z",
        f"tg_backup_{index:032x}",
    )


def publish(target, retention: int, index: int):
    published_at, generation_id = deterministic_metadata(index)
    with (
        mock.patch(
            "task_governance_tool.backup.utc_now",
            return_value=published_at,
        ),
        mock.patch(
            "task_governance_tool.backup.secrets.token_hex",
            return_value=generation_id.removeprefix("tg_backup_"),
        ),
    ):
        return publish_setup_backup(target, retention)


def canonical_files(target) -> list[Path]:
    directory = backup_directory(target)
    if not directory.exists():
        return []
    return sorted(directory.glob("taskgov-backup-v1_*.sqlite"))


class SetupBackupTests(unittest.TestCase):
    def test_publish_uses_canonical_name_and_returns_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            create_v9_target(target)
            published_at, generation_id = deterministic_metadata(1)

            metadata = publish(target, 3, 1)

            self.assertEqual(
                metadata.__dict__,
                {
                    "generation_id": generation_id,
                    "published_at": published_at,
                    "publication_retention": 3,
                },
            )
            expected = (
                backup_directory(target)
                / (
                    "taskgov-backup-v1_20260727T000001Z_"
                    f"{generation_id.removeprefix('tg_backup_')}_r3.sqlite"
                )
            )
            self.assertTrue(expected.is_file())
            with closing(connect_readonly(expected)) as copied:
                self.assertEqual(
                    copied.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    9,
                )
                self.assertEqual(
                    copied.execute("SELECT project_id FROM project_meta").fetchone()[0],
                    target.project.project_id,
                )
                self.assertEqual(copied.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(copied.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_source_history_project_and_foreign_keys_are_validated_without_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            incomplete = make_target(root, "incomplete")
            create_v9_target(incomplete)
            with closing(sqlite3.connect(incomplete.db_path)) as connection:
                connection.execute("DELETE FROM schema_migrations WHERE version = 5")
                connection.commit()
            with self.assertRaisesRegex(StorageError, "setup backup"):
                publish_setup_backup(incomplete, 3)
            self.assertFalse(backup_directory(incomplete).exists())

            foreign_owner = make_target(root, "foreign-owner")
            initialize_database(foreign_owner)
            foreign_target = resolve_database_target(
                repo=root / "different-project",
                db=foreign_owner.db_path,
                script_path=SCRIPT_PATH,
            )
            with self.assertRaisesRegex(StorageError, "different project"):
                publish_setup_backup(foreign_target, 3)
            self.assertFalse(backup_directory(foreign_owner).exists())

            invalid_fk = make_target(root, "invalid-fk")
            initialize_database(invalid_fk)
            with closing(sqlite3.connect(invalid_fk.db_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id, task_id, project_id, event_type, summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tg_event_invalid_fk",
                        "tg_task_missing",
                        invalid_fk.project.project_id,
                        "note_added",
                        "Synthetic invalid relationship",
                        "2026-07-27T00:00:00Z",
                    ),
                )
                connection.commit()
            with self.assertRaisesRegex(StorageError, "setup backup"):
                publish_setup_backup(invalid_fk, 3)
            self.assertFalse(backup_directory(invalid_fk).exists())

    def test_atomic_publish_failure_cleans_temp_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            create_v9_target(target)
            before = hashlib.sha256(target.db_path.read_bytes()).hexdigest()
            with mock.patch(
                "task_governance_tool.backup.os.replace",
                side_effect=OSError("injected atomic publish failure"),
            ):
                with self.assertRaisesRegex(StorageError, "setup backup"):
                    publish(target, 3, 2)

            self.assertEqual(hashlib.sha256(target.db_path.read_bytes()).hexdigest(), before)
            self.assertEqual(list(backup_directory(target).iterdir()), [])

    def test_pre_v10_repeated_publications_are_pruned_to_applied_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            create_v9_target(target)
            expected_ids = []

            for index in range(1, 6):
                _, generation_id = deterministic_metadata(index)
                publish(target, 2, index)
                expected_ids.append(generation_id)
                self.assertLessEqual(len(canonical_files(target)), 2)

            self.assertEqual(
                [
                    f"tg_backup_{path.name.split('_')[2]}"
                    for path in canonical_files(target)
                ],
                expected_ids[-2:],
            )

    def test_pre_v10_lower_retention_waits_for_successful_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            create_v9_target(target)
            for index in range(1, 4):
                publish(target, 3, index)
            before = [path.name for path in canonical_files(target)]

            with mock.patch(
                "task_governance_tool.backup._copy",
                side_effect=StorageError(
                    "setup_backup_failed",
                    "injected copy failure",
                ),
            ):
                with self.assertRaisesRegex(StorageError, "copy failure"):
                    publish(target, 1, 4)

            self.assertEqual(
                [path.name for path in canonical_files(target)],
                before,
            )

    def test_pre_v10_reconcile_uses_last_published_immutable_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            create_v9_target(target)
            for index in range(1, 4):
                publish(target, 3, index)

            with mock.patch(
                "task_governance_tool.backup._prune",
                side_effect=StorageError(
                    "setup_backup_failed",
                    "injected interrupted prune",
                ),
            ):
                with self.assertRaisesRegex(StorageError, "interrupted prune"):
                    publish(target, 1, 4)
            self.assertEqual(len(canonical_files(target)), 4)

            with mock.patch(
                "task_governance_tool.backup._copy",
                side_effect=StorageError(
                    "setup_backup_failed",
                    "injected later copy failure",
                ),
            ):
                with self.assertRaisesRegex(StorageError, "later copy failure"):
                    publish(target, 5, 5)

            retained = canonical_files(target)
            self.assertEqual(len(retained), 1)
            self.assertIn(f"_{4:032x}_r1.sqlite", retained[0].name)

    def test_managed_backup_lock_is_zero_wait_and_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            create_v9_target(target)

            with managed_backup_lock(target):
                with self.assertRaisesRegex(StorageError, "setup backup"):
                    with managed_backup_lock(target):
                        self.fail("contended lock was acquired")

            with managed_backup_lock(target):
                pass
            self.assertTrue(
                (backup_directory(target) / "taskgov-backup.lock").is_file()
            )

    def test_discovery_and_prune_leave_unrecognized_invalid_foreign_and_linked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = make_target(root, "primary")
            create_v9_target(target)
            _, generation_id = deterministic_metadata(1)
            publish(target, 1, 1)
            directory = backup_directory(target)
            first_file = canonical_files(target)[0]

            legacy = directory / "taskgov.pre-v9-20260727.sqlite"
            shutil.copy2(target.db_path, legacy)
            invalid = (
                directory
                / (
                    "taskgov-backup-v1_20260727T000002Z_"
                    f"{2:032x}_r1.sqlite"
                )
            )
            invalid.write_bytes(b"not a SQLite database")

            foreign = make_target(root, "foreign")
            create_v9_target(foreign)
            foreign_artifact = (
                directory
                / (
                    "taskgov-backup-v1_20260727T000003Z_"
                    f"{3:032x}_r1.sqlite"
                )
            )
            shutil.copy2(foreign.db_path, foreign_artifact)

            linked = (
                directory
                / (
                    "taskgov-backup-v1_20260727T000004Z_"
                    f"{4:032x}_r1.sqlite"
                )
            )
            link_created = False
            try:
                os.symlink(target.db_path, linked)
                link_created = True
            except (OSError, NotImplementedError):
                pass

            publish(target, 1, 5)
            self.assertFalse(first_file.exists())
            self.assertEqual(len(canonical_files(target)), 3 + int(link_created))
            self.assertTrue(legacy.exists())
            self.assertTrue(invalid.exists())
            self.assertTrue(foreign_artifact.exists())
            if link_created:
                self.assertTrue(linked.is_symlink())

    def test_v10_metadata_failure_does_not_prune_and_restart_reconciles(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            create_v10_target(target)
            first_time, first_id = deterministic_metadata(1)
            first = publish(target, 1, 1)
            second_time, second_id = deterministic_metadata(2)

            with mock.patch(
                "task_governance_tool.backup.record_setup_backup",
                side_effect=StorageError(
                    "internal_error",
                    "injected metadata failure",
                ),
            ):
                with self.assertRaisesRegex(StorageError, "injected metadata"):
                    publish(target, 1, 2)

            self.assertEqual(
                len(canonical_files(target)),
                2,
            )
            with closing(connect_readonly(target.db_path)) as connection:
                state = read_project_maintenance(
                    connection,
                    target.project.project_id,
                )
            self.assertIsNotNone(state)
            self.assertEqual(state.latest_backup_generation_id, first.generation_id)

            with mock.patch(
                "task_governance_tool.backup._copy",
                side_effect=StorageError(
                    "setup_backup_failed",
                    "injected post-reconcile stop",
                ),
            ):
                with self.assertRaisesRegex(StorageError, "post-reconcile"):
                    publish(target, 1, 3)
            self.assertEqual(len(canonical_files(target)), 1)
            with closing(connect_readonly(target.db_path)) as connection:
                state = read_project_maintenance(
                    connection,
                    target.project.project_id,
                )
            self.assertIsNotNone(state)
            self.assertEqual(state.latest_backup_generation_id, second_id)
            self.assertEqual(state.backup_last_success_at, second_time)
            self.assertEqual(state.applied_backup_generations, 1)

    def test_v11_setup_publication_keeps_rows_files_and_pointer_in_one_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            initialize_database(target)

            for index in range(1, 4):
                publish(target, 2, index)

            with closing(connect_readonly(target.db_path)) as connection:
                rows = connection.execute(
                    """
                    SELECT generation_id, published_at,
                           publication_retention
                      FROM managed_backup_generations
                     ORDER BY published_at, generation_id
                    """
                ).fetchall()
                state = read_project_maintenance(
                    connection,
                    target.project.project_id,
                )
            self.assertEqual(len(canonical_files(target)), 2)
            self.assertEqual(
                [str(row["generation_id"]) for row in rows],
                [deterministic_metadata(index)[1] for index in (2, 3)],
            )
            self.assertIsNotNone(state)
            self.assertEqual(
                state.latest_backup_generation_id,
                deterministic_metadata(3)[1],
            )
            self.assertEqual(
                state.backup_last_success_at,
                deterministic_metadata(3)[0],
            )
            self.assertEqual(state.applied_backup_generations, 2)

    def test_v11_setup_reconciliation_accepts_supported_older_source_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            initialize_database(target)
            first = publish(target, 2, 1)

            with mock.patch.object(
                backup_service,
                "record_managed_backup",
                side_effect=StorageError(
                    "internal_error",
                    "injected file-only generation",
                ),
            ):
                with self.assertRaisesRegex(StorageError, "file-only generation"):
                    publish(target, 2, 2)

            self.assertEqual(len(canonical_files(target)), 2)
            with (
                mock.patch.object(storage_service, "SCHEMA_VERSION", 12),
                mock.patch.object(backup_service, "SCHEMA_VERSION", 12),
            ):
                with self.assertRaisesRegex(
                    StorageError,
                    "does not match supported version 12",
                ):
                    read_managed_backup_repository(target)
                third = publish(target, 2, 3)

            with closing(connect_readonly(target.db_path)) as connection:
                rows = connection.execute(
                    """
                    SELECT generation_id, published_at,
                           publication_retention
                      FROM managed_backup_generations
                     ORDER BY published_at, generation_id
                    """
                ).fetchall()
                state = read_project_maintenance(
                    connection,
                    target.project.project_id,
                )
            self.assertEqual(
                [str(row["generation_id"]) for row in rows],
                [
                    deterministic_metadata(2)[1],
                    third.generation_id,
                ],
            )
            self.assertEqual(len(canonical_files(target)), 2)
            self.assertNotEqual(first.generation_id, third.generation_id)
            self.assertIsNotNone(state)
            self.assertEqual(
                state.latest_backup_generation_id,
                third.generation_id,
            )
            self.assertEqual(state.backup_last_success_at, third.published_at)
            self.assertEqual(state.applied_backup_generations, 2)


if __name__ == "__main__":
    unittest.main()
