"""Explicit protection exercises the real managed recovery repository."""

from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.test_routine_backup import make_target, metadata
from task_governance_tool import protection, protection_repository, backup, storage
from task_governance_tool.usage_evidence_service import repository_for


class ProtectionTests(unittest.TestCase):
    def fixture(self, *, usage=False, enabled=True):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        target = make_target(root, enabled=enabled)
        from task_governance_tool.project_binding_repository import read_project_binding_state
        with closing(storage.connect_readonly(target.db_path)) as connection:
            binding = read_project_binding_state(connection)
        target = replace(target, binding_path_hash=binding.canonical_path_hash,
                         binding_generation=binding.binding_generation)
        if usage:
            repository_for(target).initialize()
        return target, target.db_path.parent / "protection"

    def protect(self, target, root, **kwargs):
        return protection.protect(target, root, package_basis="a" * 64, **kwargs)

    def test_real_recovery_core_and_usage_preserves_sources(self):
        for usage in (False, True):
            with self.subTest(usage=usage):
                target, root = self.fixture(usage=usage)
                before = target.db_path.read_bytes()
                usage_before = target.resolved_usage_database.read_bytes() if usage else None
                result = self.protect(target, root)
                self.assertTrue(result["completed"])
                capture = root / result["capture_id"]
                manifest = json.loads((capture / "manifest.json").read_text())
                self.assertEqual(manifest["components"]["usage"]["presence"], "present" if usage else "absent")
                self.assertEqual(target.db_path.read_bytes(), before)
                if usage:
                    self.assertEqual(target.resolved_usage_database.read_bytes(), usage_before)
                self.assertFalse((capture / "recovery").exists())
                self.assertEqual(json.loads((capture / "verification.json").read_text())["business_records"], "equal")

    def test_preview_has_no_writes_and_does_not_require_routine_opt_in(self):
        target, root = self.fixture(enabled=False)
        before = target.db_path.read_bytes()
        result = self.protect(target, root, read_only=True)
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["completed"])
        self.assertFalse(root.exists())
        self.assertEqual(before, target.db_path.read_bytes())
        self.assertTrue(self.protect(target, root)["completed"])

    def test_invalid_present_usage_is_not_absence(self):
        target, root = self.fixture()
        target.resolved_usage_database.write_bytes(b"invalid")
        with self.assertRaises(Exception):
            self.protect(target, root)
        self.assertFalse(root.exists())

    def test_interrupted_copy_does_not_publish_manifest(self):
        target, root = self.fixture()
        with mock.patch.object(protection_repository, "copy_core", side_effect=OSError("private")):
            with self.assertRaises(OSError):
                self.protect(target, root)
        self.assertFalse(list(root.glob("tg_protection_*")))
        self.assertFalse(list(root.rglob("manifest.json")))

    def test_future_generation_is_rejected_without_clock_fabrication(self):
        target, root = self.fixture()
        future = replace(metadata(1, 0, 3), published_at="9998-01-01T00:00:00Z")
        backup._copy(target, future)
        from task_governance_tool.backup_metadata_repository import record_managed_backup
        record_managed_backup(target, future)
        before = target.db_path.read_bytes()
        with self.assertRaises(storage.StorageError):
            self.protect(target, root)
        self.assertEqual(before, target.db_path.read_bytes())
        self.assertFalse(list(root.glob("tg_protection_*")))

    def test_old_embedded_generations_are_normalized_only_in_rehearsal(self):
        target, root = self.fixture()
        for index in (1, 2):
            generation = metadata(index, index * 30, 3)
            backup._copy(target, generation)
            from task_governance_tool.backup_metadata_repository import record_managed_backup
            record_managed_backup(target, generation)
        before = target.db_path.read_bytes()
        result = self.protect(target, root)
        capture = root / result["capture_id"]
        files = {p.name: p.read_bytes() for p in capture.iterdir()}
        self.assertEqual(before, target.db_path.read_bytes())
        # Routine retention never traverses the dedicated protection area.
        backup.run_routine_backup(target, observed_at="2027-01-01T00:00:00Z")
        self.assertEqual(files, {p.name: p.read_bytes() for p in capture.iterdir()})

    def test_business_mutation_in_recovery_is_rejected(self):
        target, root = self.fixture()
        original = backup.restore_managed_backup
        def corrupt(restored, candidate):
            value = original(restored, candidate)
            with closing(storage.connect_existing(restored.db_path)) as connection:
                connection.execute("UPDATE project_meta SET display_name='changed'")
                connection.commit()
            return value
        with mock.patch.object(backup, "restore_managed_backup", side_effect=corrupt):
            with self.assertRaises(storage.StorageError):
                self.protect(target, root)
        self.assertFalse(list(root.glob("tg_protection_*")))

    def test_live_writer_is_released_before_private_validation(self):
        target, root = self.fixture()
        original = protection_repository.capture.validate_snapshot
        def validate(path, *args):
            from task_governance_tool.tasks import add_task
            with closing(storage.connect_initialized(target)) as connection:
                add_task(connection, target.project, database_target=target,
                         title="Later write", description="Outside captured point")
                connection.commit()
            return original(path, *args)
        with mock.patch.object(protection_repository.capture, "validate_snapshot", side_effect=validate):
            result = self.protect(target, root)
        manifest = json.loads((root / result["capture_id"] / "manifest.json").read_text())
        with closing(storage.connect_readonly(root / result["capture_id"] / manifest["components"]["core"]["filename"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        with closing(storage.connect_readonly(target.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_source_replacement_refuses_publication(self):
        target, root = self.fixture()
        original = protection_repository.rehearse
        def replace_source(*args):
            result = original(*args)
            replacement = target.db_path.with_suffix(".replacement")
            replacement.write_bytes(target.db_path.read_bytes())
            os.replace(replacement, target.db_path)
            return result
        with mock.patch.object(protection_repository, "rehearse", side_effect=replace_source):
            with self.assertRaises(storage.StorageError):
                self.protect(target, root)
        self.assertFalse(list(root.glob("tg_protection_*")))

    def test_usage_copy_failure_never_publishes_core_only_success(self):
        target, root = self.fixture(usage=True)
        with mock.patch.object(protection_repository, "copy_usage", side_effect=OSError("private")):
            with self.assertRaises(OSError):
                self.protect(target, root)
        self.assertTrue(list(root.rglob("*.sqlite")))
        self.assertFalse(list(root.rglob("manifest.json")))

    def test_source_replacement_during_final_admission_refuses_publication(self):
        # Replace identical bytes after the first final identity observation,
        # immediately before the fresh repository admission opens that source.
        from types import SimpleNamespace
        for component in ("core", "usage"):
            with self.subTest(component=component):
                target, root = self.fixture(usage=component == "usage")
                path = target.db_path if component == "core" else target.resolved_usage_database
                original_identity = protection_repository.source_identity(path)
                original_rehearse = protection_repository.rehearse
                original_control = protection_repository.capture.read_control
                original_repository = protection_repository.repository_for
                ready = False
                replaced = False

                def rehearse(*args):
                    nonlocal ready
                    result = original_rehearse(*args)
                    ready = True
                    return result

                def replace_source():
                    nonlocal replaced
                    replacement = path.with_suffix(".replacement")
                    replacement.write_bytes(path.read_bytes())
                    os.replace(replacement, path)
                    replaced = True
                    self.assertNotEqual(protection_repository.source_identity(path), original_identity)

                def read_control(current_target, **kwargs):
                    if ready and not replaced and current_target.db_path == target.db_path:
                        replace_source()
                    return original_control(current_target, **kwargs)

                def usage_repository(current_target):
                    repository = original_repository(current_target)
                    if ready and current_target.db_path == target.db_path:
                        def connection():
                            replace_source()
                            return repository.connection()
                        return SimpleNamespace(connection=connection)
                    return repository

                with mock.patch.object(protection_repository, "rehearse", side_effect=rehearse):
                    owner = protection_repository.capture if component == "core" else protection_repository
                    name = "read_control" if component == "core" else "repository_for"
                    substitute = read_control if component == "core" else usage_repository
                    with mock.patch.object(owner, name, side_effect=substitute):
                        with self.assertRaises(storage.StorageError):
                            self.protect(target, root)
                self.assertTrue(replaced)
                self.assertFalse(list(root.glob("tg_protection_*")))
                self.assertFalse(list(root.rglob("manifest.json")))

    def test_usage_appearing_after_absence_refuses_publication(self):
        target, root = self.fixture()
        original = protection_repository.rehearse
        def add_usage(*args):
            result = original(*args)
            repository_for(target).initialize()
            return result
        with mock.patch.object(protection_repository, "rehearse", side_effect=add_usage):
            with self.assertRaises(storage.StorageError):
                self.protect(target, root)
        self.assertFalse(list(root.glob("tg_protection_*")))

    def test_usage_binding_drift_in_place_refuses_publication(self):
        target, root = self.fixture(usage=True)
        original = protection_repository.rehearse
        def change_binding(*args):
            result = original(*args)
            with repository_for(target).connection(write=True) as connection:
                connection.execute("UPDATE usage_meta SET binding_generation=binding_generation+1")
            return result
        with mock.patch.object(protection_repository, "rehearse", side_effect=change_binding):
            with self.assertRaises(Exception):
                self.protect(target, root)
        self.assertFalse(list(root.glob("tg_protection_*")))

    def test_package_drift_prevents_manifest_and_publication(self):
        target, root = self.fixture()
        def changed():
            raise protection_repository.failure()
        with self.assertRaises(storage.StorageError):
            self.protect(target, root, publication_guard=changed)
        self.assertFalse(list(root.rglob("manifest.json")))

    def test_busy_source_is_bounded_and_no_write(self):
        target, root = self.fixture()
        with closing(storage.connect_existing(target.db_path)) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(storage.StorageError):
                self.protect(target, root)
            connection.rollback()
        self.assertFalse(root.exists())

    def test_manifest_last_and_no_replace_preserve_existing_capture(self):
        target, root = self.fixture()
        first = self.protect(target, root)
        original_files = {p.name: p.read_bytes() for p in (root / first["capture_id"]).iterdir()}
        original = protection.create_exclusive_durable_file
        order = []
        def interrupted(path, *args, **kwargs):
            order.append(path.name)
            if path.name == "manifest.json":
                raise OSError("private")
            return original(path, *args, **kwargs)
        with mock.patch.object(protection, "create_exclusive_durable_file", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.protect(target, root)
        self.assertEqual(order, ["verification.json", "manifest.json"])
        self.assertEqual(len(list(root.glob("tg_protection_*"))), 1)
        with mock.patch.object(protection.secrets, "token_hex", return_value=first["capture_id"].removeprefix("tg_protection_")):
            with self.assertRaises(storage.StorageError):
                self.protect(target, root)
        self.assertEqual(original_files, {p.name: p.read_bytes() for p in (root / first["capture_id"]).iterdir()})

    def test_multiple_linked_source_is_rejected(self):
        target, root = self.fixture()
        try:
            os.link(target.db_path, target.db_path.with_suffix(".linked"))
        except OSError:
            self.skipTest("hard links unavailable")
        with self.assertRaises(Exception):
            self.protect(target, root)
        self.assertFalse(root.exists())

    def test_missing_usage_anchor_stays_unbound_through_real_recovery(self):
        from task_governance_tool.usage_turn_adapter import OperationObservation
        from task_governance_tool.usage_attribution_repository import resolve_operation
        target, root = self.fixture(usage=True)
        operation = OperationObservation("00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002", target.project.project_id,
            "tg_task_0123456789abcdef", "tg_event_0123456789abcdef", 1, "in_progress")
        from task_governance_tool.session_identity import CallerIdentity
        repository_for(target).register_session(CallerIdentity(operation.thread_id))
        repository_for(target).record((operation,))
        original = protection_repository.rehearse
        observed = []
        def rehearse(*args):
            result = original(*args)
            with closing(storage.connect_readonly(args[1] / "taskgov.sqlite")) as core:
                observed.append(resolve_operation(core, operation))
            with closing(storage.connect_readonly(args[1] / "taskgov-usage.sqlite")) as numerical:
                self.assertEqual(numerical.execute("SELECT COUNT(*) FROM usage_operation_turns").fetchone()[0], 1)
            return result
        with mock.patch.object(protection_repository, "rehearse", side_effect=rehearse):
            self.assertTrue(self.protect(target, root)["completed"])
        self.assertEqual(observed, [None])


class ProtectionCliTests(unittest.TestCase):
    def test_public_preview_capture_and_reentry_preserve_source(self):
        from tests.test_m17_recovery_hardening import _setup_current
        from tests.m14_test_support import file_snapshot
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        install, target, _ = _setup_current(root)
        before = file_snapshot(install.project_root)
        preview = install.run("backup", "protect", "--read-only", "--json")
        self.assertEqual(preview.returncode, 0, preview.stdout)
        self.assertFalse(json.loads(preview.stdout)["data"]["completed"])
        self.assertEqual(file_snapshot(install.project_root), before)
        captured = install.run("backup", "protect", "--json")
        self.assertEqual(captured.returncode, 0, captured.stdout)
        result = json.loads(captured.stdout)
        self.assertEqual(result["command"], "backup.protect")
        self.assertTrue(result["data"]["completed"])
        self.assertTrue((install.project_root / result["data"]["location"] / "manifest.json").is_file())
        self.assertEqual(install.run("doctor", "--json").returncode, 0)
        self.assertEqual(install.run("backup", "protect", "--read-only", "--json").returncode, 0)

    def test_invalid_arguments_and_records_only_fail_before_writes(self):
        from tests.m14_test_support import make_physical_install, file_snapshot
        install = make_physical_install(Path(self.enterContext(tempfile.TemporaryDirectory())).resolve())
        before = file_snapshot(install.project_root)
        for arguments in (("backup", "protect", "--output", "private", "--json"),
                          ("backup", "protect", "--db", "private", "--json"),
                          ("--records-only", "backup", "protect", "--json"), ("backup", "--json")):
            result = install.run(*arguments)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("private", result.stdout)
        help_result = install.run("backup", "protect", "--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--read-only", help_result.stdout)
        self.assertEqual(file_snapshot(install.project_root), before)
