"""One global audit per Setup/Doctor read, with real isolated history."""
from contextlib import ExitStack, closing, redirect_stdout, redirect_stderr
import io
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from tests.m14_test_support import file_snapshot
from tests import verification_receipt_test_support as reviews
from tests.review_answer_fixture_support import attach_existing
from task_governance_tool import storage, state_resolver, doctor, cli
from task_governance_tool import stored_task_validation, task_ownership
from task_governance_tool import schema_viewer_finding_index as schema
from task_governance_tool import schema_task_event_notes as notes


def history_fixture(root):
    install, target, _ = _setup_current(root)

    def invoke(*args, **options):
        args = list(args)
        at = args.index("--db")
        del args[at:at + 2]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), mock.patch.object(
            cli, "cli_script_path", return_value=install.entrypoint
        ):
            code = cli.main(args, _maintenance_enabled=False)
        return SimpleNamespace(returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())

    with mock.patch.object(reviews, "run_taskgov", side_effect=invoke):
        task_id = reviews.add_task(target.db_path, install.project_root, review_tier=2,
            status="ready")["task_id"]
        contract = invoke("task", "edit", "--repo", str(install.project_root),
            "--db", str(target.db_path), task_id, "--status", "in_progress",
            "--contract-scope", "Isolated read validation",
            "--contract-acceptance", "Full snapshot remains valid", "--json")
        if contract.returncode:
            raise AssertionError(contract.stdout + contract.stderr)
        generation = reviews.set_target(target.db_path, install.project_root, task_id)
        fixture = attach_existing(db=target.db_path, repo=install.project_root,
            task_id=task_id, target=target, schema_version=storage.SCHEMA_VERSION)
        _, finding, successor = fixture.reassess_with_finding()
        fixture.resolve(finding, successor=successor)
        for result in (reviews.add_receipt(target.db_path, install.project_root, task_id, generation),
                       reviews.completion(target.db_path, install.project_root, task_id)):
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        reviews.add_task(target.db_path, install.project_root, review_tier=1)
    return install, target


def measure(operation):
    responsibilities = {
        "task": (stored_task_validation, "validate_stored_task_rows"),
        "runner": (storage, "_validated_verification_runner_references"),
        "ownership": (task_ownership, "validate_storage_rows"),
        "review": (storage, "_validate_review_storage_rows"),
        "note": (notes, "validate_note_rows"),
        "cycle": (storage, "validate_completion_cycle_storage"),
        "ledger": (storage, "_validate_evidence_ledger_rows"),
    }
    with ExitStack() as stack:
        probes = {label: stack.enter_context(mock.patch.object(owner, name,
                  wraps=getattr(owner, name))) for label, (owner, name) in responsibilities.items()}
        result = operation()
        counts = {label: probe.call_count for label, probe in probes.items()}
        batches = [len(call.args[0]) for call in probes["task"].call_args_list]
    return result, counts, batches


class SetupSnapshotValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.install, cls.target = history_fixture(Path(cls.directory.name))
        cls.original = cls.target.db_path.read_bytes()

    def setUp(self):
        self.target.db_path.write_bytes(self.original)

    def run_doctor(self):
        return doctor.run_doctor(repo=str(self.install.project_root), repo_explicit=True,
            script_path=self.install.entrypoint)

    def read_setup(self):
        with closing(storage.connect_readonly(self.target.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            result = storage.read_setup_state(connection, self.target)
            self.assertTrue(connection.in_transaction)
            return result

    def read_doctor(self):
        with closing(storage.connect_readonly(self.target.db_path)) as connection:
            return storage.read_doctor_state(connection, self.target)

    def test_single_global_pass_and_full_batch_in_each_standalone_entry(self):
        before = file_snapshot(self.install.project_root)
        for name, operation in (("setup", self.read_setup), ("doctor", self.run_doctor),
                                ("standalone_doctor", self.read_doctor)):
            with self.subTest(entry=name):
                result, counts, batches = measure(operation)
                self.assertEqual(counts, dict.fromkeys(counts, 1))
                self.assertEqual(batches, [2])
                if name == "doctor":
                    self.assertTrue(result.ok, result)
                    summary = result.data["components"]["task_summary"]
                    self.assertEqual((summary["done"], summary["active"]), (1, 1))
                elif name == "setup":
                    self.assertFalse(result.needs_migration)
                else:
                    self.assertEqual(result.task_counts["done"], 1)
        self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_schema_standalone_still_validates_and_returns_no_proof(self):
        with closing(storage.connect_readonly(self.target.db_path)) as connection:
            result, counts, batches = measure(lambda: schema.validate_storage(connection))
            self.assertIsNone(result)
            self.assertEqual(counts, dict.fromkeys(counts, 1))
            self.assertEqual(batches, [2])

    def corrupt(self, table, assignment):
        with closing(storage.connect(self.target.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("PRAGMA ignore_check_constraints=ON")
            triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
            for row in triggers:
                connection.execute('DROP TRIGGER "' + row["name"] + '"')
            changed = connection.execute(f"UPDATE {table} SET {assignment}").rowcount
            self.assertGreater(changed, 0, "corruption must affect an actual fixture row")
            for row in triggers:
                connection.execute(row["sql"])
            connection.commit()

    def test_each_domain_corruption_is_rejected_without_projection_or_write(self):
        faults = (
            ("tasks", "priority='invalid'"),
            ("task_contract_revisions", "scope='Authorization: Bearer synthetic-private-value'"),
            ("task_ownership", "generation=generation+17"),
            ("task_events", "note_body='Authorization: Bearer synthetic-private-value'"),
            ("review_receipts", "summary='Authorization: Bearer synthetic-private-value'"),
            ("task_completion_cycles", "saved_cycle_ordinal=99"),
            ("completion_evidence_bundles", "bundle_digest='sha256:' || printf('%064d',0)"),
        )
        for table, assignment in faults:
            with self.subTest(domain=table):
                self.target.db_path.write_bytes(self.original)
                self.corrupt(table, assignment)
                before = file_snapshot(self.install.project_root)
                result = self.run_doctor()
                self.assertFalse(result.ok, result)
                components = result.data["components"]
                self.assertEqual(components["project_state"]["code"], "unreadable")
                self.assertFalse(result.data["setup_eligible"])
                for key in ("task_summary", "handoff_delivery", "maintenance"):
                    self.assertEqual(components[key], {"code": "unavailable"})
                self.assertNotIn("synthetic-private-value", repr(result))
                with self.assertRaises(storage.StorageError) as error:
                    self.read_setup()
                self.assertEqual(error.exception.code, "project_state_unreadable")
                with closing(storage.connect_readonly(self.target.db_path)) as connection:
                    with self.assertRaises(storage.StorageError):
                        schema.validate_storage(connection)
                self.assertEqual(file_snapshot(self.install.project_root), before)

    def test_new_snapshot_observes_corruption_after_success(self):
        self.assertTrue(self.run_doctor().ok)
        self.corrupt("tasks", "priority='invalid'")
        self.assertFalse(self.run_doctor().ok)
        with self.assertRaises(storage.StorageError):
            self.read_setup()

    def test_doctor_closes_connection_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                if fail:
                    self.corrupt("tasks", "priority='invalid'")
                opened = []
                original = state_resolver.connect_readonly
                def capture(*args, **kwargs):
                    connection = original(*args, **kwargs)
                    opened.append(connection)
                    return connection
                with mock.patch.object(state_resolver, "connect_readonly", side_effect=capture):
                    self.run_doctor()
                self.assertEqual(len(opened), 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    opened[0].execute("SELECT 1")

    def test_busy_stays_distinct_and_leaks_no_sqlite_detail(self):
        with mock.patch.object(schema, "_validate_storage_with_task_rows",
                side_effect=storage.StorageError("database_busy", storage.DATABASE_BUSY_MESSAGE)):
            result = self.run_doctor()
        self.assertFalse(result.ok)
        self.assertEqual(result.data["components"]["project_state"]["code"], "busy")


    def test_doctor_physical_checks_run_once_and_each_new_call_rechecks(self):
        original = storage.connect_readonly
        statements = []
        def opened(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with mock.patch.object(state_resolver, "connect_readonly", side_effect=opened):
            for expected in (1, 2):
                self.assertTrue(self.run_doctor().ok)
                for check in ("quick_check", "integrity_check", "foreign_key_check"):
                    self.assertEqual(statements.count("PRAGMA " + check), expected, statements)

    def test_standalone_setup_calls_do_not_share_successes(self):
        with closing(storage.connect_readonly(self.target.db_path)) as connection:
            statements = []
            connection.set_trace_callback(statements.append)
            for expected in (1, 2):
                storage.read_setup_state(connection, self.target)
                for check in ("quick_check", "integrity_check", "foreign_key_check"):
                    self.assertEqual(statements.count("PRAGMA " + check), expected)

    def test_quick_full_and_fk_failures_keep_doctor_unreadable(self):
        original = storage._physical_integrity_ok
        before = self.target.db_path.read_bytes()
        for rejected in ("quick_check", "integrity_check", "foreign_key_check"):
            def checked(connection, check):
                return False if check == rejected else original(connection, check)
            with self.subTest(check=rejected), mock.patch.object(storage, "_physical_integrity_ok", side_effect=checked), \
                 mock.patch.object(state_resolver, "_physical_integrity_ok", side_effect=checked):
                result = self.run_doctor()
                self.assertFalse(result.ok)
                self.assertEqual(result.data["components"]["project_state"]["code"], "unreadable")
                self.assertEqual(self.target.db_path.read_bytes(), before)

    def test_transaction_replacement_writes_and_other_connections_drop_successes(self):
        with closing(sqlite3.connect(":memory:")) as connection, closing(sqlite3.connect(":memory:")) as other:
            connection.execute("CREATE TABLE fixture(value INTEGER)")
            connection.execute("BEGIN")
            other.execute("BEGIN")
            for current in (connection, other):
                current.execute("PRAGMA query_only=ON")
            statements = []
            connection.set_trace_callback(statements.append)
            @storage._reuse_read_integrity
            def read():
                self.assertTrue(storage._physical_integrity_ok(connection, "quick_check"))
                self.assertTrue(storage._physical_integrity_ok(connection, "quick_check"))
                self.assertTrue(storage._physical_integrity_ok(other, "quick_check"))
                self.assertEqual(statements.count("PRAGMA quick_check"), 1)
                connection.rollback()
                connection.execute("BEGIN")
                self.assertTrue(storage._physical_integrity_ok(connection, "quick_check"))
                self.assertEqual(statements.count("PRAGMA quick_check"), 2)
                connection.execute("PRAGMA query_only=OFF")
                connection.execute("INSERT INTO fixture VALUES (1)")
                connection.execute("PRAGMA query_only=ON")
                self.assertTrue(storage._physical_integrity_ok(connection, "quick_check"))
                self.assertEqual(statements.count("PRAGMA quick_check"), 3)
                connection.execute("PRAGMA query_only=OFF")
                self.assertTrue(storage._physical_integrity_ok(connection, "quick_check"))
                connection.execute("PRAGMA query_only=ON")
                self.assertTrue(storage._physical_integrity_ok(connection, "quick_check"))
                self.assertEqual(statements.count("PRAGMA quick_check"), 5)
            read()
            self.assertIsNone(storage._read_integrity_scope.get())

    def test_supported_schema_read_branches_keep_independent_physical_checks(self):
        from tests.test_review_session_migration import candidate_runtime
        from tests.verification_receipt_test_support import initialize, target_for
        for version in range(21, 36):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                with candidate_runtime(version):
                    repo, db = initialize(Path(directory))
                target = target_for(db, repo)
                with closing(storage.connect_readonly(db)) as connection:
                    statements = []
                    connection.set_trace_callback(statements.append)
                    result = storage.read_setup_state(connection, target)
                    self.assertEqual(result.schema_version, version)
                    for check in ("quick_check", "integrity_check", "foreign_key_check"):
                        self.assertEqual(statements.count("PRAGMA " + check), 1, (version, check))

    def test_recovery_observation_shares_checks_only_inside_each_read(self):
        original = state_resolver.connect_readonly
        statements = []
        def opened(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with mock.patch.object(state_resolver, "connect_readonly", side_effect=opened):
            for expected in (1, 2):
                observation = state_resolver._inspect_database(
                    self.target.db_path, classify_recovery_content=True)
                self.assertTrue(observation.recovery_content_valid)
                for check in ("quick_check", "integrity_check", "foreign_key_check"):
                    self.assertEqual(statements.count("PRAGMA " + check), expected)

    def test_publication_checks_do_not_escape_the_phase_or_connection(self):
        from task_governance_tool import backup
        with closing(storage.connect_readonly(self.target.db_path)) as connection:
            statements = []
            connection.set_trace_callback(statements.append)
            for expected in (1, 2):
                self.assertEqual(backup._validate_publication_source(connection, self.target),
                                 storage.SCHEMA_VERSION)
                for check in ("quick_check", "integrity_check", "foreign_key_check"):
                    self.assertEqual(statements.count("PRAGMA " + check), expected)
        with closing(storage.connect_readonly(self.target.db_path)) as connection:
            statements = []
            connection.set_trace_callback(statements.append)
            backup._validate_publication_source(connection, self.target)
            for check in ("quick_check", "integrity_check", "foreign_key_check"):
                self.assertEqual(statements.count("PRAGMA " + check), 1)

    def test_ddl_invalidates_checks_without_a_row_write(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE fixture(value INTEGER)")
            connection.execute("BEGIN")
            connection.execute("PRAGMA query_only=ON")
            statements = []
            connection.set_trace_callback(statements.append)
            @storage._reuse_read_integrity
            def read():
                for check in ("quick_check", "integrity_check", "foreign_key_check"):
                    self.assertTrue(storage._physical_integrity_ok(connection, check))
                before_changes = connection.total_changes
                connection.execute("PRAGMA query_only=OFF")
                connection.execute("CREATE INDEX fixture_value ON fixture(value)")
                connection.execute("PRAGMA query_only=ON")
                self.assertEqual(connection.total_changes, before_changes)
                for check in ("quick_check", "integrity_check", "foreign_key_check"):
                    self.assertTrue(storage._physical_integrity_ok(connection, check))
                    self.assertTrue(storage._physical_integrity_ok(connection, check))
                    self.assertEqual(statements.count("PRAGMA " + check), 2)
            read()
            self.assertIsNone(storage._read_integrity_scope.get())

    def test_recovery_pair_keeps_writers_uncached_and_each_read_fresh(self):
        from task_governance_tool import backup
        with closing(storage.connect_readonly(self.target.db_path)) as connection:
            statements = []
            connection.set_trace_callback(statements.append)
            for expected in (1, 2):
                self.assertEqual(backup._inspect_recovery_database(connection, self.target),
                                 (storage.SCHEMA_VERSION, True))
                for check in ("quick_check", "integrity_check", "foreign_key_check"):
                    self.assertEqual(statements.count("PRAGMA " + check), expected)
        with closing(storage.connect_existing(self.target.db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            statements = []
            connection.set_trace_callback(statements.append)
            version = backup._validate_database(connection, self.target)
            self.assertTrue(backup._recovery_content_valid(connection, version, self.target))
            checks = ("quick_check", "integrity_check", "foreign_key_check")
            baseline = {check: statements.count("PRAGMA " + check) for check in checks}
            for check in ("quick_check", "foreign_key_check"):
                self.assertGreaterEqual(baseline[check], 2)
            statements.clear()
            self.assertEqual(backup._inspect_recovery_database(connection, self.target),
                             (storage.SCHEMA_VERSION, True))
            # Writable state has no admitted query-only lifetime: retain both
            # obligations, including nested exact-schema checks, just as the
            # separate pre-composition calls do on this unchanged writer.
            self.assertEqual(
                {check: statements.count("PRAGMA " + check) for check in checks}, baseline)
            connection.rollback()

    def test_failed_physical_checks_are_repeated_and_scope_is_released(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript("CREATE TABLE parent(id INTEGER PRIMARY KEY);"
                "CREATE TABLE child(id INTEGER REFERENCES parent(id)); INSERT INTO child VALUES (1);")
            connection.execute("BEGIN")
            connection.execute("PRAGMA query_only=ON")
            statements = []
            connection.set_trace_callback(statements.append)
            @storage._reuse_read_integrity
            def read():
                self.assertTrue(storage._physical_integrity_ok(connection, 'quick_check'))
                for _ in range(2):
                    self.assertFalse(storage._physical_integrity_ok(connection, 'foreign_key_check'))
                raise RuntimeError('isolated scope exit')
            with self.assertRaises(RuntimeError):
                read()
            self.assertEqual(statements.count('PRAGMA foreign_key_check'), 2)
            self.assertIsNone(storage._read_integrity_scope.get())
            self.assertTrue(connection.in_transaction)

    def test_shared_physical_audit_reduces_actual_sqlite_vm_work(self):
        import json
        import time
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE fixture(id INTEGER PRIMARY KEY, value TEXT CHECK(length(value)>0))")
            connection.executemany("INSERT INTO fixture VALUES (?,?)", [(i, 'synthetic') for i in range(1000)])
            connection.commit()
            connection.execute("BEGIN")
            connection.execute("PRAGMA query_only=ON")
            def measure(shared):
                steps = 0
                statements = []
                def progress():
                    nonlocal steps
                    steps += 1
                    return 0
                connection.set_progress_handler(progress, 100)
                connection.set_trace_callback(statements.append)
                started = time.perf_counter()
                def inspect():
                    return [storage._physical_integrity_ok(connection, check) for _ in range(2)
                            for check in ('quick_check', 'integrity_check', 'foreign_key_check')]
                try:
                    results = storage._reuse_read_integrity(inspect)() if shared else inspect()
                finally:
                    elapsed = time.perf_counter() - started
                    connection.set_progress_handler(None, 0)
                    connection.set_trace_callback(None)
                scans = {check: statements.count('PRAGMA ' + check)
                         for check in ('quick_check', 'integrity_check', 'foreign_key_check')}
                return results, steps, scans, elapsed
            baseline, baseline_work, baseline_scans, baseline_seconds = measure(False)
            shared, shared_work, shared_scans, shared_seconds = measure(True)
            self.assertEqual(baseline, [True] * 6)
            self.assertEqual(shared, baseline)
            self.assertTrue(all(value == 2 for value in baseline_scans.values()))
            self.assertTrue(all(value == 1 for value in shared_scans.values()))
            self.assertLess(shared_work, baseline_work)
            print(json.dumps({'physical_audit_vm_quanta_100': [baseline_work, shared_work],
                              'scans_before': baseline_scans, 'scans_after': shared_scans,
                              'inclusive_seconds': [baseline_seconds, shared_seconds],
                              'connections': 1, 'read_transactions': 1}))


class LegacySetupTaskBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_review_session_migration import candidate_runtime
        from tests.verification_receipt_test_support import initialize, add_task, target_for
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.sources = {}
        for version in range(21, 33):
            root = Path(cls.directory.name) / str(version)
            root.mkdir()
            with candidate_runtime(version):
                repo, db = initialize(root)
                empty = db.read_bytes()
                for index in range(2):
                    add_task(db, repo, title=f"Legacy Task {index}", status="ready")
            cls.sources[version] = (target_for(db, repo), empty, db.read_bytes())

    def test_each_legacy_batch_is_consumed_once_including_empty(self):
        original = stored_task_validation.validate_stored_task_rows
        for version, (target, empty, populated) in self.sources.items():
            for data, count in ((empty, 0), (populated, 2)):
                target.db_path.write_bytes(data)
                for repeat in range(2):
                    with self.subTest(version=version, count=count, repeat=repeat), \
                         closing(storage.connect_readonly(target.db_path)) as connection, \
                         mock.patch.object(stored_task_validation, "validate_stored_task_rows", wraps=original) as checked:
                        statements = []
                        connection.set_trace_callback(statements.append)
                        result = storage.read_setup_state(connection, target)
                        self.assertEqual(result.schema_version, version)
                        self.assertTrue(result.needs_migration)
                        self.assertEqual(checked.call_count, 1)
                        self.assertEqual(len(checked.call_args.args[0]), count)
                        self.assertEqual(sum(sql.strip() == "SELECT * FROM tasks ORDER BY task_id" for sql in statements), 1)
                self.assertEqual(target.db_path.read_bytes(), data)

    def test_legacy_consumed_task_and_contract_corruption_still_reject(self):
        for version, (target, _, populated) in self.sources.items():
            for assignment in ("title='Authorization: Bearer synthetic-private-value'",
                               "current_contract_revision=current_contract_revision+1"):
                with self.subTest(version=version, fault=assignment):
                    target.db_path.write_bytes(populated)
                    with closing(storage.connect(target.db_path)) as connection:
                        connection.execute("UPDATE tasks SET " + assignment)
                        connection.commit()
                    before = target.db_path.read_bytes()
                    with closing(storage.connect_readonly(target.db_path)) as connection:
                        with self.assertRaises(storage.StorageError):
                            storage.read_setup_state(connection, target)
                    self.assertEqual(target.db_path.read_bytes(), before)

    def test_standalone_validators_return_no_batch_and_revalidate(self):
        import importlib
        modules = {23:"schema_verification_declaration",24:"schema_task_ownership",
            25:"schema_review_sessions",26:"schema_session_slot",27:"schema_session_slot_reacquisition",
            28:"schema_task_event_notes",29:"schema_review_continuation",30:"schema_review_answers",
            31:"schema_criterion_link_collector",32:"schema_session_slot_index"}
        original = stored_task_validation.validate_stored_task_rows
        for version, (target, _, populated) in self.sources.items():
            target.db_path.write_bytes(populated)
            validator = (getattr(storage, f"validate_schema{version}_storage") if version < 23 else
                importlib.import_module("task_governance_tool." + modules[version]).validate_storage)
            with self.subTest(version=version), closing(storage.connect_readonly(target.db_path)) as connection, \
                 mock.patch.object(stored_task_validation, "validate_stored_task_rows", wraps=original) as checked:
                self.assertIsNone(validator(connection))
                self.assertIsNone(validator(connection))
                self.assertEqual(checked.call_count, 2)

    def test_foreign_project_target_never_consumes_the_admitted_batch(self):
        from dataclasses import replace
        for version, (target, _, populated) in self.sources.items():
            target.db_path.write_bytes(populated)
            foreign = replace(target, project=replace(target.project,
                project_id="tg_project_" + "f" * 32))
            with self.subTest(version=version), closing(storage.connect_readonly(target.db_path)) as connection:
                with self.assertRaises(storage.StorageError):
                    storage.read_setup_state(connection, foreign)
            self.assertEqual(target.db_path.read_bytes(), populated)


if __name__ == "__main__":
    unittest.main()
