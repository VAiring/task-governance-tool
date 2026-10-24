"""Complete core marker admission at supported source and consumer boundaries."""

from contextlib import closing
from pathlib import Path
import hashlib
import importlib
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current, _backup_snapshot
from tests.test_routine_backup import make_target, metadata, timestamp
from task_governance_tool import storage as s, backup, state_resolver, doctor, setup
from task_governance_tool.usage_locator_repository import UsageLocatorRepository
from task_governance_tool.usage_values import UsageError


SCRIPT = Path(__file__).resolve().parents[1] / "task-governance-tool/scripts/taskgov.py"


def migration_steps():
    steps = [s.apply_initial_schema_migration, s.apply_completion_commit_migration,
        s.apply_paused_state_migration, s.apply_completion_evidence_migration,
        s.apply_review_evidence_migration, s.apply_git_snapshot_schema_migration,
        s.apply_handoff_outbox_migration, s.apply_task_contract_migration,
        s.apply_effort_advisory_migration, s.apply_project_maintenance_migration,
        s.apply_managed_backup_generations_migration, s.apply_task_checkpoints_migration,
        s.apply_viewer_maintenance_migration, s.apply_project_identity_bindings_migration,
        s.apply_completion_cycle_history_migration, s.apply_completion_cycle_capture_activation_migration,
        s.apply_verification_receipts_migration, s.apply_evidence_ledger_capture_migration,
        s.apply_completion_evidence_bundle_migration, s._migrate_schema20_connection,
        s._migrate_schema21_connection, s._migrate_schema22_connection]
    modules = ("schema_verification_declaration", "schema_task_ownership", "schema_review_sessions",
        "schema_session_slot", "schema_session_slot_reacquisition", "schema_task_event_notes",
        "schema_review_continuation", "schema_review_answers", "schema_criterion_link_collector",
        "schema_session_slot_index", "schema_own_review_indexes")
    return steps + [importlib.import_module("task_governance_tool." + name).migrate for name in modules]


def history(connection):
    return tuple(map(tuple, connection.execute("SELECT version,name,applied_at FROM schema_migrations ORDER BY version")))


class MigrationHistoryValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.repo = cls.root / "project"
        cls.repo.mkdir()
        working = cls.root / "build.sqlite"
        cls.sources = {}
        for version, migrate in enumerate(migration_steps(), 1):
            with closing(s.connect(working)) as connection:
                migrate(connection)
                if version == 1:
                    s.ensure_project_meta(connection, s.project_identity(cls.repo))
                connection.commit()
                assert s.current_schema_version(connection) == version
            destination = cls.root / f"source-{version}.sqlite"
            shutil.copyfile(working, destination)
            cls.sources[version] = destination

    def setUp(self):
        self.db = self.root / "case.sqlite"

    def case(self, version, sql=None, parameters=()):
        shutil.copyfile(self.sources[version], self.db)
        if sql:
            with closing(s.connect_existing(self.db)) as connection:
                connection.execute(sql, parameters)
                connection.commit()
        return s.resolve_database_target(repo=self.repo, db=self.db, script_path=SCRIPT)

    def boundaries(self, target, version):
        def read(operation):
            with closing(s.connect_readonly(self.db)) as connection:
                return operation(connection)
        result = {
            "resolver": lambda: state_resolver._inspect_database(self.db),
            "recovery": lambda: state_resolver._inspect_database(self.db, classify_recovery_content=True),
            "backup": lambda: read(lambda c: backup._validate_publication_source(c, target)),
            "setup": lambda: read(lambda c: s.read_setup_state(c, target)),
        }
        if version >= s.VIEWER_MIN_SOURCE_SCHEMA_VERSION:
            result["viewer"] = lambda: read(lambda c: s._validate_snapshot_database_state(c, target))
        if version == s.SCHEMA_VERSION:
            result.update(global_admission=lambda: read(lambda c: s.validate_current_database(c, target)),
                local_admission=lambda: read(lambda c: s.validate_current_database(c, target, operation_local=True)),
                doctor=lambda: read(lambda c: s.read_doctor_state(c, target)))
        return result

    def assert_rejected(self, operations):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for name, operation in operations.items():
            with self.subTest(boundary=name), self.assertRaises((s.StorageError, state_resolver._ResolverFailure)):
                operation()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_all_supported_sources_preserve_exact_history_without_time_ordering(self):
        self.assertEqual(len(self.sources), s.SCHEMA_VERSION)
        for version in self.sources:
            with self.subTest(version=version):
                target = self.case(version)
                with closing(s.connect_existing(self.db)) as connection:
                    # Existing timestamps have format validity, not ordering authority.
                    connection.execute("UPDATE schema_migrations SET applied_at=CASE WHEN version%2=0 "
                                       "THEN '2001-01-01T00:00:00Z' ELSE '2026-10-24T01:02:03Z' END")
                    connection.commit()
                    before = history(connection)
                    self.assertEqual(s.missing_migration_versions(connection, version), [])
                    self.assertEqual(tuple(row[1] for row in before), s._MIGRATION_NAMES[:version])
                original = self.db.read_bytes()
                for operation in self.boundaries(target, version).values():
                    operation()
                self.assertEqual(self.db.read_bytes(), original)
                with closing(s.connect_existing(self.db)) as connection:
                    s.apply_migrations(connection)
                    self.assertEqual(history(connection)[:version], before)
                    self.assertEqual(s.current_schema_version(connection), s.SCHEMA_VERSION)

    def test_every_current_marker_name_time_and_storage_type_is_checked(self):
        mutations = (("name", "incorrect"), ("name", b"incorrect"),
                     ("applied_at", "2026-02-30T00:00:00Z"), ("applied_at", b"2026-10-24T00:00:00Z"))
        for marker in self.sources:
            for field, value in mutations:
                with self.subTest(marker=marker, field=field, value_type=type(value).__name__):
                    target = self.case(s.SCHEMA_VERSION,
                        f"UPDATE schema_migrations SET {field}=? WHERE version=?", (value, marker))
                    self.assert_rejected(self.boundaries(target, s.SCHEMA_VERSION))
                    with closing(s.connect_existing(self.db)) as connection:
                        before = tuple(connection.iterdump())
                        with self.assertRaises(s.StorageError):
                            s.apply_migrations(connection)
                        self.assertEqual(tuple(connection.iterdump()), before)

    def test_each_source_rejects_old_latest_extra_and_missing_markers(self):
        for version in self.sources:
            mutations = (
                ("UPDATE schema_migrations SET name='incorrect' WHERE version=1", ()),
                ("UPDATE schema_migrations SET applied_at='bad' WHERE version=?", (version,)),
                ("INSERT INTO schema_migrations VALUES(0,'extra','2026-10-24T00:00:00Z')", ()),
                ("INSERT INTO schema_migrations VALUES(-1,'extra','2026-10-24T00:00:00Z')", ()),
                ("INSERT INTO schema_migrations VALUES(34,'future','2026-10-24T00:00:00Z')", ()),
            )
            for sql, parameters in mutations:
                with self.subTest(version=version, mutation=sql):
                    target = self.case(version, sql, parameters)
                    self.assert_rejected(self.boundaries(target, version))
                    with closing(s.connect_existing(self.db)) as connection:
                        before = tuple(connection.iterdump())
                        with self.assertRaises(s.StorageError):
                            s.apply_migrations(connection)
                        self.assertEqual(tuple(connection.iterdump()), before)
            if version > 1:
                with self.subTest(version=version, mutation="gap"):
                    target = self.case(version, "DELETE FROM schema_migrations WHERE version=1")
                    self.assert_rejected(self.boundaries(target, version))

    def test_empty_and_invalid_version_probes_and_full_history_prefix_boundary(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(s.current_schema_version(connection), 0)
            connection.execute("CREATE TABLE schema_migrations(version,name,applied_at)")
            self.assertEqual(s.current_schema_version(connection), 0)
            for value in (None, 0, -1, 1.5, "1", b"1"):
                with self.subTest(version=value):
                    connection.execute("DELETE FROM schema_migrations")
                    connection.execute("INSERT INTO schema_migrations VALUES(?,'initial_schema','2026-10-24T00:00:00Z')", (value,))
                    with self.assertRaises(s.StorageError):
                        s.current_schema_version(connection)
                    with self.assertRaises(s.StorageError):
                        s.missing_migration_versions(connection, 1)
            connection.execute("DELETE FROM schema_migrations")
            connection.executemany("INSERT INTO schema_migrations VALUES(1,'initial_schema','2026-10-24T00:00:00Z')", [(), ()])
            with self.assertRaises(s.StorageError):
                s.missing_migration_versions(connection, 1)
        self.case(s.SCHEMA_VERSION, "DELETE FROM schema_migrations WHERE version=32")
        with closing(s.connect_readonly(self.db)) as connection:
            self.assertEqual(s.missing_migration_versions(connection, 20), [32])


class MigrationHistoryPhysicalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_doctor_setup_and_new_backup_reject_bad_primary_history(self):
        install, target, _ = _setup_current(self.root)
        with closing(s.connect_existing(target.db_path)) as connection:
            connection.execute("UPDATE schema_migrations SET applied_at='bad' WHERE version=1")
            connection.commit()
        original = target.db_path.read_bytes()
        before = _backup_snapshot(target.backups_path)
        args = dict(repo=str(install.project_root), repo_explicit=True, script_path=install.entrypoint)
        diagnosed = doctor.run_doctor(**args)
        self.assertEqual(diagnosed.data["components"]["project_state"]["code"], "unreadable")
        preview = setup.run_setup(**args, read_only=True, backup_interval_minutes=None, backup_generations=None)
        self.assertFalse(preview.ok)
        # Admission fails before a publication attempt or outcome recording.
        with self.assertRaises(s.StorageError) as raised:
            backup.run_routine_backup(target, observed_at=timestamp(0))
        self.assertEqual(raised.exception.code, "project_state_unreadable")
        self.assertEqual(_backup_snapshot(target.backups_path), before)
        self.assertEqual(target.db_path.read_bytes(), original)

    def test_backup_private_copy_and_fresh_source_validate_history_again(self):
        for stage in ("copy", "fresh"):
            with self.subTest(stage=stage):
                target = make_target(self.root / stage)
                original = backup._validate_publication_source
                calls = []
                def inspect(connection, *args, **kwargs):
                    calls.append(connection)
                    if len(calls) == 2 and stage == "copy":
                        connection.execute("UPDATE schema_migrations SET name='incorrect' WHERE version=1")
                    return original(connection, *args, **kwargs)
                real_fsync = backup.os.fsync
                def sync(descriptor):
                    real_fsync(descriptor)
                    if stage == "fresh":
                        with closing(s.connect_existing(target.db_path)) as connection:
                            connection.execute("UPDATE schema_migrations SET name='incorrect' WHERE version=1")
                            connection.commit()
                with mock.patch.object(backup, "_validate_publication_source", side_effect=inspect), \
                        mock.patch.object(backup.os, "fsync", side_effect=sync):
                    with self.assertRaises(s.StorageError):
                        backup._copy(target, metadata(1, 0, 3))
                self.assertEqual(len(calls), 2 if stage == "copy" else 3)
                self.assertEqual(backup.discover_managed_backup_metadata(target), ())

    def test_public_setup_restore_preserves_normal_history_and_rejects_bad_candidate(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                install, target, _ = _setup_current(self.root / str(corrupt))
                with closing(s.connect_readonly(target.db_path)) as connection:
                    expected = history(connection)
                self.assertEqual(backup.run_routine_backup(target, observed_at=timestamp(0)).code, "succeeded")
                candidates = list(target.backups_path.glob("taskgov-backup-v1_*.sqlite"))
                self.assertEqual(len(candidates), 1)
                if corrupt:
                    with closing(s.connect_existing(candidates[0])) as connection:
                        connection.execute("UPDATE schema_migrations SET applied_at='bad' WHERE version=1")
                        connection.commit()
                original = candidates[0].read_bytes()
                # This is exclusively the test's disposable physical install.
                self.assertTrue(target.db_path.resolve().is_relative_to(self.root.resolve()))
                target.db_path.unlink()
                result = setup.run_setup(repo=str(install.project_root), repo_explicit=True,
                    script_path=install.entrypoint, read_only=False, backup_interval_minutes=None, backup_generations=None)
                self.assertEqual(result.ok, not corrupt)
                if corrupt:
                    self.assertEqual(result.error_code, "project_state_unreadable")
                    self.assertFalse(target.db_path.exists())
                    self.assertEqual(candidates[0].read_bytes(), original)
                else:
                    with closing(s.connect_readonly(target.db_path)) as connection:
                        self.assertEqual(history(connection), expected)

    def test_separate_usage_history_keeps_its_existing_validation(self):
        repository = UsageLocatorRepository(self.root / "usage.sqlite", "fixture", "a" * 64, 1)
        repository.initialize()
        self.assertEqual(repository.inspect(), "current")
        with closing(sqlite3.connect(repository.path)) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE usage_migrations SET name='incorrect' WHERE version=1")
            connection.commit()
        with self.assertRaisesRegex(UsageError, "usage_schema_invalid"):
            repository.inspect()
