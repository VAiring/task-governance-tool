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
from task_governance_tool import schema_own_review_indexes as schema
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


if __name__ == "__main__":
    unittest.main()
