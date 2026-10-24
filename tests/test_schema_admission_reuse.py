"""One current owned-definition check without weakening independent admission."""

from contextlib import closing
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from task_governance_tool import storage as s, schema_own_review_indexes as schema
from task_governance_tool.tasks import add_task
from tests.test_routine_backup import make_target
from tests.review_answer_fixture_support import review_fixture
from tests.test_review_session_migration import candidate_runtime


class SchemaAdmissionReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.target = make_target(root, enabled=False)
        with closing(s.connect_initialized(cls.target)) as connection:
            cls.task_id = add_task(connection, cls.target.project,
                database_target=cls.target, title="Admission counter fixture").task["task_id"]
            connection.commit()
        cls.saved = root / "saved.sqlite"
        shutil.copyfile(cls.target.db_path, cls.saved)

    def setUp(self):
        shutil.copyfile(self.saved, self.target.db_path)
        self.connection = self.enterContext(closing(s.connect_existing(self.target.db_path)))

    def assert_one_check(self, operation):
        trace = []
        self.connection.set_trace_callback(trace.append)
        try:
            with mock.patch.object(schema, "validate_owned_contract", wraps=schema.validate_owned_contract) as owned:
                operation()
                self.assertEqual(owned.call_count, 1)
            fingerprints = [sql for sql in trace if sql.startswith("SELECT type,name,tbl_name,sql FROM sqlite_master")]
            self.assertEqual(len(fingerprints), 1)
        finally:
            self.connection.set_trace_callback(None)
        return trace

    def test_global_local_and_selected_admission_each_hash_once(self):
        for local in (False, True):
            for task_id in (None, self.task_id):
                with self.subTest(operation_local=local, selected=task_id is not None):
                    self.assert_one_check(lambda: s.validate_current_database(self.connection,
                        self.target, operation_local=local, review_task_id=task_id))

    def test_writer_revalidates_once_after_preflight_and_each_transaction(self):
        for local in (False, True):
            for task_id in (None, self.task_id):
                self.assert_one_check(lambda: s.validate_current_database(self.connection,
                    self.target, operation_local=local, review_task_id=task_id))
                for _ in range(2):
                    self.assert_one_check(lambda: s.begin_initialized_write(self.connection,
                        self.target, operation_local=local, review_task_id=task_id))
                    self.assertTrue(self.connection.in_transaction)
                    self.connection.rollback()

    def test_standalone_evidence_helpers_perform_full_validation_each_time(self):
        for helper in (s._validate_evidence_ledger_schema_contract,
                       s._validate_completion_evidence_bundle_schema_contract):
            for _ in range(2):
                trace = self.assert_one_check(lambda: helper(self.connection))
                self.assertFalse(any(sql.startswith(("BEGIN", "COMMIT", "SAVEPOINT")) for sql in trace))

    def test_schema_damage_rejected_by_combined_and_standalone_helpers(self):
        mutations = (
            "ALTER TABLE tasks ADD COLUMN unowned TEXT",
            "DROP INDEX idx_review_continuations_task_history",
            "DROP TRIGGER trg_review_receipts_provenance_basis_insert",
            "CREATE INDEX extra_receipt_index ON review_receipts(task_id)",
            "CREATE TRIGGER extra_receipt_trigger BEFORE DELETE ON review_receipts BEGIN SELECT 1; END",
            "CREATE TABLE COMPLETION_EVIDENCE_BUNDLES_V32(placeholder)",
        )
        for ddl in mutations:
            with self.subTest(ddl=ddl):
                self.connection.execute("BEGIN")
                self.connection.execute(ddl)
                for helper in (s._validate_current_schema_contract,
                               s._validate_evidence_ledger_schema_contract,
                               s._validate_completion_evidence_bundle_schema_contract):
                    with self.assertRaises(s.StorageError):
                        helper(self.connection)
                self.connection.rollback()

    def test_individual_ledger_foreign_keys_and_columns_are_still_checked(self):
        # Simulate malformed PRAGMA results while the complete DDL fingerprint
        # remains correct, proving these checks were not replaced by the proof.
        result = s._Schema33OwnedValidation(self.connection)
        try:
            with mock.patch.object(s, "_foreign_key_signatures", return_value=[]):
                with self.assertRaises(s.StorageError) as rejected:
                    s._validate_evidence_ledger_schema_contract(self.connection, _owned_contract=result)
                self.assertEqual(rejected.exception.code, "evidence_ledger_inconsistent")
        finally:
            result.close()

        class ColumnDrift:
            def __getattr__(proxy, name):
                return getattr(self.connection, name)

            def execute(proxy, sql, *args):
                cursor = self.connection.execute(sql, *args)
                if sql.startswith("PRAGMA table_xinfo(") and "verification_receipts" in sql:
                    rows = [dict(row) for row in cursor.fetchall()]
                    rows[0]["hidden"] = 1
                    return SimpleNamespace(fetchall=lambda: rows)
                return cursor

        proxy = ColumnDrift()
        result = s._Schema33OwnedValidation(proxy)
        try:
            with self.assertRaises(s.StorageError) as rejected:
                s._validate_evidence_ledger_schema_contract(proxy, _owned_contract=result)
            self.assertEqual(rejected.exception.code, "evidence_ledger_inconsistent")
        finally:
            result.close()

    def test_migration_history_and_binding_still_reject_before_operation(self):
        self.connection.execute("UPDATE schema_migrations SET name='invalid' WHERE version=1")
        with self.assertRaises(s.StorageError):
            s.validate_current_database(self.connection, self.target, operation_local=True)
        self.connection.rollback()
        with mock.patch.object(s, "_validate_target_binding", side_effect=s.StorageError("project_mismatch", "Isolated binding mismatch")):
            with self.assertRaises(s.StorageError) as rejected:
                s.validate_current_database(self.connection, self.target, operation_local=True)
            self.assertEqual(rejected.exception.code, "project_mismatch")

    def test_result_expires_on_success_and_failure_including_new_transaction(self):
        real = s._validate_evidence_ledger_schema_contract
        for failure in (None, "database_busy", "evidence_ledger_inconsistent"):
            captured = []
            def ledger(connection, *, _owned_contract=None):
                captured.append(_owned_contract)
                if failure:
                    raise s.StorageError(failure, "Isolated failure")
                return real(connection, _owned_contract=_owned_contract)
            with mock.patch.object(s, "_validate_evidence_ledger_schema_contract", side_effect=ledger):
                if failure:
                    with self.assertRaises(s.StorageError) as rejected:
                        s._validate_current_schema_contract(self.connection)
                    self.assertEqual(rejected.exception.code,
                        "database_busy" if failure == "database_busy" else "project_state_unreadable")
                else:
                    s._validate_current_schema_contract(self.connection)
            self.assertEqual(len(captured), 1)
            for begin in (False, True):
                if begin:
                    self.connection.execute("BEGIN")
                with self.assertRaises(s.StorageError):
                    s._validate_completion_evidence_bundle_schema_contract(self.connection,
                        _owned_contract=captured[0])
                self.connection.rollback()

    def test_connection_and_ddl_drift_cannot_use_active_result(self):
        with closing(s.connect_existing(self.target.db_path)) as other:
            result = s._Schema33OwnedValidation(self.connection)
            try:
                with self.assertRaises(s.StorageError):
                    result.require(other)
                self.connection.execute("CREATE TABLE unrelated_ddl(value)")
                with self.assertRaises(s.StorageError):
                    result.require(self.connection)
            finally:
                result.close()
        # New admission is fresh even though unrelated objects are permitted.
        self.assert_one_check(lambda: s._validate_current_schema_contract(self.connection))
        self.connection.execute("DROP INDEX idx_review_continuations_task_history")
        with self.assertRaises(s.StorageError):
            s.begin_initialized_write(self.connection, self.target, operation_local=True)
        self.assertFalse(self.connection.in_transaction)

    def test_readonly_admission_retains_query_only_and_transaction(self):
        with closing(s.connect_readonly(self.target.db_path)) as connection:
            with mock.patch.object(schema, "validate_owned_contract", wraps=schema.validate_owned_contract) as owned:
                before = connection.in_transaction
                s.validate_current_database(connection, self.target, operation_local=True)
                self.assertEqual(owned.call_count, 1)
                self.assertEqual(connection.in_transaction, before)
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)

    def test_ddl_during_owned_validation_cannot_issue_a_stale_result(self):
        original = schema.validate_owned_contract
        def changed(connection):
            original(connection)
            connection.execute("DROP INDEX idx_review_continuations_task_history")
        with mock.patch.object(schema, "validate_owned_contract", side_effect=changed):
            with self.assertRaises(s.StorageError):
                s._validate_current_schema_contract(self.connection)

    def test_predecessor_standalone_and_reentry_keep_their_own_contract(self):
        from task_governance_tool import schema_session_slot_index as previous
        with review_fixture(schema_version=32, contract=None) as fixture, \
             closing(s.connect_existing(fixture.db)) as connection, candidate_runtime(32):
            before = previous.owned_fingerprint(connection)
            s._validate_current_schema_contract(connection)
            s._validate_evidence_ledger_schema_contract(connection)
            s._validate_completion_evidence_bundle_schema_contract(connection)
            self.assertFalse(previous.migrate(connection))
            self.assertEqual(previous.owned_fingerprint(connection), before)
            connection.execute("DROP INDEX idx_task_ownership_held_actor")
            for helper in (s._validate_current_schema_contract,
                           s._validate_evidence_ledger_schema_contract,
                           s._validate_completion_evidence_bundle_schema_contract):
                with self.assertRaises(s.StorageError):
                    helper(connection)


if __name__ == "__main__":
    unittest.main()
