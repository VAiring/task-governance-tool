"""Explicit protection exercises the real managed recovery repository."""

from contextlib import closing
from dataclasses import replace
import json
import os
import sqlite3
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
                self.assertEqual((manifest["format"], manifest["validation_rules"]), ("taskgov-protection-v2", 2))
                self.assertEqual(manifest["components"]["usage"]["presence"], "present" if usage else "absent")
                proof = json.loads((capture / "verification.json").read_text())
                self.assertEqual(proof["usage"], "restored" if usage else "absent")
                for record in (result, manifest, proof):
                    self.assertEqual(record["usage_aggregation"], "pending" if usage else "absent")
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

    def test_retained_v1_capture_keeps_its_original_proof_and_recoverable_components(self):
        # A retained legacy proof is not upgraded in place or treated as current
        # aggregation after a new restore. Its managed component format is shared.
        target, root = self.fixture(usage=True)
        with mock.patch.object(protection, "FORMAT", "taskgov-protection-v1"):
            legacy = self.protect(target, root)
        capture = root / legacy["capture_id"]
        proof = json.loads((capture / "verification.json").read_bytes())
        proof.pop("usage_aggregation")
        proof["usage"] = "reconciled"
        (capture / "verification.json").write_bytes(protection._bytes(proof))
        manifest = json.loads((capture / "manifest.json").read_bytes())
        manifest.pop("usage_aggregation")
        manifest["validation_rules"] = 1
        import hashlib
        manifest["verification"]["sha256"] = hashlib.sha256((capture / "verification.json").read_bytes()).hexdigest()
        (capture / "manifest.json").write_bytes(protection._bytes(manifest))
        before = {path.name: path.read_bytes() for path in capture.iterdir()}
        newer = self.protect(target, root)
        self.assertNotEqual(newer["capture_id"], legacy["capture_id"])
        self.assertEqual(before, {path.name: path.read_bytes() for path in capture.iterdir()})
        inbox = target.db_path.parent / "legacy-recovery"
        (inbox / "backups").mkdir(parents=True)
        filename = manifest["components"]["core"]["filename"]
        (inbox / "backups" / filename).write_bytes(before[filename])
        restored = protection_repository.rehearse(target, inbox, capture / "taskgov-usage.sqlite")
        self.assertEqual((restored["core"], restored["usage_aggregation"]), ("passed", "pending"))
        self.assertEqual(before, {path.name: path.read_bytes() for path in capture.iterdir()})

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

    def test_usage_restore_admits_one_core_read_without_reconciliation(self):
        from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
        from task_governance_tool import usage_reconciliation_publication as publication
        target, root = self.fixture(usage=True)
        original = publication.captured_basis
        reads = []
        core_paths = []

        def tracked_basis(repository, core, numerical):
            core_paths.append(repository.path.with_name("taskgov.sqlite"))
            reads.append(core)
            self.assertTrue(core.in_transaction)
            self.assertEqual(core.execute("PRAGMA query_only").fetchone()[0], 1)
            self.assertEqual(numerical.execute("PRAGMA query_only").fetchone()[0], 1)
            return original(repository, core, numerical)

        with mock.patch.object(publication, "captured_basis", new=tracked_basis), \
                mock.patch.object(UsageReconciliationRepository, "refresh", side_effect=AssertionError("full drain")), \
                mock.patch.object(publication, "reconcile", side_effect=AssertionError("full drain")), \
                mock.patch.object(storage, "connect_initialized_readonly", wraps=storage.connect_initialized_readonly) as admitted:
            self.assertTrue(self.protect(target, root)["completed"])
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(core_paths), 1)
        # Recovery also validates its normalization temporary file. Count only
        # the restored Core admission used by Usage, preserving those checks.
        self.assertEqual(sum(call.args[0].db_path == core_paths[0]
                             for call in admitted.call_args_list), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            reads[0].execute("SELECT 1")

    def test_usage_initial_core_admission_failure_prevents_publication(self):
        from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
        target, root = self.fixture(usage=True)
        before = (target.db_path.read_bytes(), target.resolved_usage_database.read_bytes())
        original = storage.connect_initialized_readonly
        rejected = []

        def fail_usage_admission(candidate, **kwargs):
            if candidate.db_path.is_relative_to(root) and candidate.db_path.name == "taskgov.sqlite":
                rejected.append(candidate.db_path)
                raise storage.StorageError("project_state_unreadable", "injected admission failure")
            return original(candidate, **kwargs)

        with mock.patch.object(storage, "connect_initialized_readonly", side_effect=fail_usage_admission), \
                mock.patch.object(UsageReconciliationRepository, "refresh") as refresh:
            with self.assertRaises(storage.StorageError):
                self.protect(target, root)
        self.assertEqual(len(rejected), 1)
        refresh.assert_not_called()
        self.assertEqual(before, (target.db_path.read_bytes(), target.resolved_usage_database.read_bytes()))
        self.assertFalse(list(root.rglob("manifest.json")))

    def test_usage_failure_closes_core_read_and_preserves_sources(self):
        from task_governance_tool import usage_reconciliation_publication as publication
        target, root = self.fixture(usage=True)
        before = (target.db_path.read_bytes(), target.resolved_usage_database.read_bytes())
        reads = []

        def failed_basis(repository, connection, numerical):
            reads.append(connection)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM tasks")
            raise RuntimeError("injected admission failure")

        with mock.patch.object(publication, "captured_basis", new=failed_basis):
            with self.assertRaises(RuntimeError):
                self.protect(target, root)
        self.assertEqual(before, (target.db_path.read_bytes(), target.resolved_usage_database.read_bytes()))
        self.assertFalse(list(root.rglob("manifest.json")))
        with self.assertRaises(sqlite3.ProgrammingError):
            reads[0].execute("SELECT 1")

    def test_unexpected_current_or_invalid_usage_fence_prevents_publication(self):
        from task_governance_tool import usage_reconciliation_publication as publication
        from task_governance_tool.usage_values import UsageError
        for result in ("unexpected-current", UsageError("usage_schema_invalid")):
            with self.subTest(result=str(result)):
                target, root = self.fixture(usage=True)
                with mock.patch.object(publication, "captured_basis", side_effect=
                        result if isinstance(result, Exception) else None, return_value=result):
                    with self.assertRaises((storage.StorageError, UsageError)):
                        self.protect(target, root)
                self.assertFalse(list(root.glob("tg_protection_*")))


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
        from tests.m14_test_support import make_current_physical_install as make_physical_install, file_snapshot
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
