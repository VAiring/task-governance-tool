from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import storage  # noqa: E402
from task_governance_tool import tasks as _tasks  # noqa: E402,F401
from tests.m223_test_support import logical_database_digest  # noqa: E402
from tests.test_m242_r3b_schema20_activation import (  # noqa: E402
    _add_task,
    _complete_task,
    _seed_completion_gates,
    _start_schema20_runtime_oracle,
    _stop_schema20_runtime_oracle,
)
from tests import test_m242_runner_storage as _runner_storage  # noqa: E402


FIXED_TIME = "2026-08-28T00:00:00Z"


def _identity(seed: str) -> str:
    parts = list(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32])
    parts[12] = "4"
    parts[16] = "8"
    return "".join(parts)


def _new_target(root: Path, seed: str) -> storage.UnboundDatabaseTarget:
    repo = root / "project"
    repo.mkdir(parents=True)
    return storage.UnboundDatabaseTarget(
        canonical_repo=repo,
        canonical_path_hash=hashlib.sha256(str(repo).encode("utf-8")).hexdigest(),
        display_name="schema21-storage-test",
        db_path=root / "state" / "current" / "taskgov.sqlite",
        explicit_db=True,
        canonical_fixed=True,
    )


def _fresh_database(root: Path, seed: str, *, version: int) -> storage.InitResult:
    target = _new_target(root, seed)
    with mock.patch.object(storage, "SCHEMA_VERSION", version):
        return storage.initialize_uuid_database(
            target,
            project_id_factory=lambda: _identity(seed),
            clock=lambda: FIXED_TIME,
        )


class Schema21StorageTests(unittest.TestCase):
    def test_fresh_v21_has_exact_marker_inventory_and_validation_only_reentry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=".tmp-m243b-v21-", dir=ROOT) as tmp:
            initialized = _fresh_database(Path(tmp), "fresh-v21", version=21)
            self.assertEqual(initialized.schema_version, 21)
            self.assertEqual(initialized.migrations_applied[-2:], [20, 21])
            with closing(storage.connect(initialized.target.db_path)) as connection:
                storage.validate_schema21_storage(connection)
                marker = connection.execute(
                    "SELECT name FROM schema_migrations WHERE version = 21"
                ).fetchone()
                self.assertEqual(
                    marker["name"],
                    storage.PRIVATE_SCHEMA21_MIGRATION_NAME,
                )
                counts = {
                    row["type"]: row["count"]
                    for row in connection.execute(
                        "SELECT type, COUNT(*) AS count FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' "
                        "AND (type != 'index' OR sql IS NOT NULL) "
                        "GROUP BY type"
                    ).fetchall()
                }
                self.assertEqual(counts, {"index": 42, "table": 35, "trigger": 59})
                for table_name in (
                    "verification_runner_resolutions",
                    "verification_runner_attempts",
                    "verification_runner_observations",
                ):
                    sql = connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                        (table_name,),
                    ).fetchone()["sql"]
                    self.assertIn("gate_eligibility_version IN (0, 1)", sql)
                cycle_guard = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND "
                    "name='trg_task_completion_cycles_verification_basis_insert'"
                ).fetchone()["sql"]
                normalized_guard = " ".join(cycle_guard.split())
                self.assertIn(
                    "resolution.verification_expectation_digest = "
                    "NEW.verification_expectation_digest",
                    normalized_guard,
                )
                before = logical_database_digest(connection)
                self.assertEqual(storage.apply_migrations(connection), ([], []))
                self.assertEqual(logical_database_digest(connection), before)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("PRAGMA legacy_alter_table").fetchone()[0],
                    0,
                )

    def test_v20_to_v21_retires_only_runner_attachment_and_preserves_rows_objects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=".tmp-m243b-v20-", dir=ROOT) as tmp:
            initialized = _fresh_database(Path(tmp), "migrate-v20", version=20)
            with closing(storage.connect(initialized.target.db_path)) as connection:
                connection.execute(
                    "CREATE INDEX extra_runner_created_at "
                    "ON verification_runner_resolutions(created_at)"
                )
                connection.execute(
                    "CREATE INDEX preserved_sandbox_created_at "
                    "ON verification_runner_sandbox_events(created_at)"
                )
                connection.execute(
                    "CREATE TABLE unrelated_schema21_fixture("
                    "fixture_id TEXT PRIMARY KEY, null_value, integer_value, "
                    "real_value, text_value, blob_value) WITHOUT ROWID"
                )
                connection.execute(
                    "INSERT INTO unrelated_schema21_fixture VALUES (?, ?, ?, ?, ?, ?)",
                    ("row-b", None, 7, -0.0, "preserved", sqlite3.Binary(b"\x00\xff")),
                )
                connection.execute(
                    "CREATE INDEX unrelated_schema21_fixture_value "
                    "ON unrelated_schema21_fixture(text_value)"
                )
                connection.execute(
                    "CREATE TRIGGER completion_evidence_bundles_v20 "
                    "AFTER INSERT ON unrelated_schema21_fixture "
                    "BEGIN SELECT 1; END"
                )
                connection.commit()
                before_project = tuple(
                    connection.execute(
                        "SELECT project_id, display_name FROM project_meta"
                    ).fetchone()
                )
            storage.rehearse_schema21_storage(initialized.target.db_path)
            with closing(storage.connect(initialized.target.db_path)) as connection:
                storage.validate_schema21_storage(connection)
                self.assertEqual(storage.current_schema_version(connection), 21)
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='extra_runner_created_at'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='index' AND name='preserved_sandbox_created_at'"
                    ).fetchone()
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            "SELECT null_value, integer_value, real_value, "
                            "text_value, blob_value "
                            "FROM unrelated_schema21_fixture "
                            "WHERE fixture_id='row-b'"
                        ).fetchone()
                    ),
                    (None, 7, -0.0, "preserved", b"\x00\xff"),
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE name='unrelated_schema21_fixture_value'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
                        "AND name='completion_evidence_bundles_v20'"
                    ).fetchone()
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            "SELECT project_id, display_name FROM project_meta"
                        ).fetchone()
                    ),
                    before_project,
                )

    def test_populated_v20_runner_and_bundle_rows_migrate_byte_stably(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-populated-v20-",
            dir=ROOT,
        ) as tmp:
            _start_schema20_runtime_oracle()
            try:
                helper = _runner_storage.RunnerStorageTests()
                target, _basis, _resolution, _attempt = helper._pending_graph(
                    Path(tmp),
                    seed="populated-v20",
                    commit_character="a",
                    token="1234567890abcdef",
                )
                bundle_task = _add_task(
                    self,
                    target,
                    title="Populated v20 Bundle preservation",
                    verification="",
                    with_contract=True,
                )
                _seed_completion_gates(
                    self,
                    target,
                    bundle_task,
                    fingerprint="sha256:" + "b" * 64,
                    verification_required=False,
                )
                _complete_task(self, target, bundle_task)
                with closing(storage.connect(target.db_path)) as connection:
                    storage.validate_schema20_storage(
                        connection,
                        allow_native_bundle_v2=True,
                    )
                    preserved_tables = (
                        "verification_runner_resolutions",
                        "verification_runner_attempts",
                        "verification_runner_observations",
                        "completion_evidence_bundles",
                        "completion_bundle_members",
                    )
                    before = {
                        table_name: tuple(
                            tuple(row)
                            for row in connection.execute(
                                f'SELECT * FROM "{table_name}" ORDER BY rowid'
                            ).fetchall()
                        )
                        for table_name in preserved_tables
                    }
                    bundle_before = dict(
                        connection.execute(
                            "SELECT source_schema_version, bundle_version, "
                            "bundle_digest, payload_size_bytes "
                            "FROM completion_evidence_bundles WHERE task_id = ?",
                            (bundle_task,),
                        ).fetchone()
                    )
            finally:
                _stop_schema20_runtime_oracle()

            storage.rehearse_schema21_storage(target.db_path)
            with closing(storage.connect(target.db_path)) as connection:
                storage.validate_schema21_storage(connection)
                after = {
                    table_name: tuple(
                        tuple(row)
                        for row in connection.execute(
                            f'SELECT * FROM "{table_name}" ORDER BY rowid'
                        ).fetchall()
                    )
                    for table_name in preserved_tables
                }
                self.assertEqual(after, before)
                self.assertEqual(
                    dict(
                        connection.execute(
                            "SELECT source_schema_version, bundle_version, "
                            "bundle_digest, payload_size_bytes "
                            "FROM completion_evidence_bundles WHERE task_id = ?",
                            (bundle_task,),
                        ).fetchone()
                    ),
                    bundle_before,
                )
                self.assertEqual(bundle_before["source_schema_version"], 20)
                self.assertEqual(bundle_before["bundle_version"], 2)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_runner_resolutions "
                        "WHERE gate_eligibility_version = 0"
                    ).fetchone()[0],
                    1,
                )

    def test_v21_reentry_rejects_rebuilt_table_attachment_without_write(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-v21-attachment-",
            dir=ROOT,
        ) as tmp:
            initialized = _fresh_database(Path(tmp), "v21-attachment", version=21)
            with closing(storage.connect(initialized.target.db_path)) as connection:
                connection.execute(
                    "CREATE INDEX forbidden_v21_runner_attachment "
                    "ON verification_runner_resolutions(created_at)"
                )
                connection.commit()
                before = logical_database_digest(connection)

                with self.assertRaises(storage.StorageError):
                    storage.apply_migrations(connection)

                self.assertEqual(logical_database_digest(connection), before)
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE name='forbidden_v21_runner_attachment'"
                    ).fetchone()
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA legacy_alter_table").fetchone()[0],
                    0,
                )

    def test_v21_reentry_rejects_case_alias_temporary_table_without_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-v21-temp-alias-",
            dir=ROOT,
        ) as tmp:
            initialized = _fresh_database(Path(tmp), "v21-temp-alias", version=21)
            with closing(storage.connect(initialized.target.db_path)) as connection:
                connection.execute(
                    "CREATE TABLE Verification_Runner_Resolutions_v20("
                    "marker INTEGER PRIMARY KEY)"
                )
                connection.commit()
                before = logical_database_digest(connection)

                with self.assertRaises(storage.StorageError) as caught:
                    storage.apply_migrations(connection)

                self.assertEqual(caught.exception.code, "project_state_unreadable")
                self.assertEqual(logical_database_digest(connection), before)
                self.assertEqual(storage.current_schema_version(connection), 21)

    def test_v21_reentry_preserves_non_table_temporary_name_aliases(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-v21-temp-nontable-",
            dir=ROOT,
        ) as tmp:
            initialized = _fresh_database(Path(tmp), "v21-temp-nontable", version=21)
            with closing(storage.connect(initialized.target.db_path)) as connection:
                connection.execute(
                    "CREATE TABLE unrelated_temp_name_owner("
                    "marker INTEGER PRIMARY KEY)"
                )
                connection.execute(
                    "CREATE INDEX Completion_Evidence_Bundles_v20 "
                    "ON unrelated_temp_name_owner(marker)"
                )
                connection.execute(
                    "CREATE VIEW Verification_Runner_Attempts_v20 "
                    "AS SELECT marker FROM unrelated_temp_name_owner"
                )
                connection.commit()
                before = logical_database_digest(connection)

                self.assertEqual(storage.apply_migrations(connection), ([], []))

                self.assertEqual(logical_database_digest(connection), before)
                self.assertEqual(storage.current_schema_version(connection), 21)

    def test_v20_and_v21_reject_inherited_owned_sql_drift_without_write(self) -> None:
        for version in (20, 21):
            with self.subTest(version=version), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-v{version}-owned-drift-",
                dir=ROOT,
            ) as tmp:
                initialized = _fresh_database(
                    Path(tmp),
                    f"owned-drift-v{version}",
                    version=version,
                )
                with closing(storage.connect(initialized.target.db_path)) as connection:
                    connection.execute("DROP INDEX idx_tasks_project_status")
                    connection.execute(
                        "CREATE INDEX idx_tasks_project_status "
                        "ON tasks(project_id, status) WHERE status = 'active'"
                    )
                    connection.commit()
                    before = logical_database_digest(connection)

                    with self.assertRaises(storage.StorageError) as caught:
                        storage.apply_migrations(connection)

                    self.assertEqual(caught.exception.code, "project_state_unreadable")
                    self.assertEqual(storage.current_schema_version(connection), version)
                    self.assertEqual(logical_database_digest(connection), before)
                    self.assertIsNone(
                        connection.execute(
                            "SELECT 1 FROM schema_migrations "
                            "WHERE version > ?",
                            (version,),
                        ).fetchone()
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_keys").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA legacy_alter_table").fetchone()[0],
                        0,
                    )

    def test_each_migration_stage_rolls_back_marker_tables_attachment_and_pragmas(
        self,
    ) -> None:
        stages = (
            "after_cycle_guards",
            "after_renames",
            "after_tables",
            "after_copy",
            "after_drop_old",
            "after_objects",
            "after_marker",
            "before_commit",
        )
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-{stage}-",
                dir=ROOT,
            ) as tmp:
                initialized = _fresh_database(Path(tmp), stage, version=20)
                with closing(storage.connect(initialized.target.db_path)) as connection:
                    connection.execute(
                        "CREATE INDEX rollback_runner_attachment "
                        "ON verification_runner_attempts(intent_recorded_at)"
                    )
                    connection.commit()
                    before = logical_database_digest(connection)
                    with self.assertRaises(storage.StorageError):
                        storage._migrate_schema21_connection(
                            connection,
                            fail_stage=stage,
                        )
                    self.assertEqual(storage.current_schema_version(connection), 20)
                    self.assertEqual(logical_database_digest(connection), before)
                    self.assertIsNotNone(
                        connection.execute(
                            "SELECT 1 FROM sqlite_master "
                            "WHERE name='rollback_runner_attachment'"
                        ).fetchone()
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_keys").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA legacy_alter_table").fetchone()[0],
                        0,
                    )

    def test_hybrid_temp_name_and_bundle_attachment_reject_before_write(self) -> None:
        fixtures = (
            "temp_collision",
            "temp_collision_case_alias",
            "temp_collision_view",
            "temp_collision_index",
            "bundle_index_attachment",
            "bundle_trigger_attachment",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory(
                prefix=f".tmp-m243b-{fixture}-",
                dir=ROOT,
            ) as tmp:
                initialized = _fresh_database(Path(tmp), fixture, version=20)
                with closing(storage.connect(initialized.target.db_path)) as connection:
                    if fixture == "temp_collision":
                        connection.execute(
                            "CREATE TABLE verification_runner_resolutions_v20("
                            "marker INTEGER PRIMARY KEY)"
                        )
                    elif fixture == "temp_collision_case_alias":
                        connection.execute(
                            "CREATE TABLE Verification_Runner_Attempts_v20("
                            "marker INTEGER PRIMARY KEY)"
                        )
                    elif fixture == "temp_collision_view":
                        connection.execute(
                            "CREATE VIEW verification_runner_observations_v20 "
                            "AS SELECT 1 AS marker"
                        )
                    elif fixture == "temp_collision_index":
                        connection.execute(
                            "CREATE TABLE unrelated_temp_index_owner("
                            "marker INTEGER PRIMARY KEY)"
                        )
                        connection.execute(
                            "CREATE INDEX Completion_Evidence_Bundles_v20 "
                            "ON unrelated_temp_index_owner(marker)"
                        )
                    elif fixture == "bundle_index_attachment":
                        connection.execute(
                            "CREATE INDEX forbidden_bundle_attachment "
                            "ON completion_evidence_bundles(sealed_at)"
                        )
                    else:
                        connection.execute(
                            "CREATE TRIGGER forbidden_bundle_trigger "
                            "AFTER INSERT ON completion_evidence_bundles "
                            "BEGIN SELECT 1; END"
                        )
                    connection.commit()
                    before = logical_database_digest(connection)
                    for validator in (
                        lambda: storage.validate_schema20_storage(
                            connection,
                            allow_native_bundle_v2=True,
                        ),
                        lambda: storage.validate_schema20_storage_for_recovery(
                            connection
                        ),
                    ):
                        with self.assertRaises(storage.StorageError) as caught:
                            validator()
                        self.assertEqual(
                            caught.exception.code,
                            "project_state_unreadable",
                        )
                        self.assertEqual(
                            logical_database_digest(connection),
                            before,
                        )
                with self.assertRaises(storage.StorageError):
                    storage.rehearse_schema21_storage(initialized.target.db_path)
                with closing(storage.connect(initialized.target.db_path)) as connection:
                    self.assertEqual(storage.current_schema_version(connection), 20)
                    self.assertEqual(logical_database_digest(connection), before)
                    self.assertIsNone(
                        connection.execute(
                            "SELECT 1 FROM schema_migrations WHERE version=21"
                        ).fetchone()
                    )

    def test_owned_contract_is_proved_before_schema21_marker_insert(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m243b-marker-last-", dir=ROOT
        ) as tmp:
            initialized = _fresh_database(Path(tmp), "marker-last", version=20)
            with closing(storage.connect(initialized.target.db_path)) as connection:
                before = logical_database_digest(connection)
            original = storage._validate_schema21_owned_contract
            observed_versions: list[int] = []

            def fail_pre_marker(connection: sqlite3.Connection) -> None:
                observed_versions.append(storage.current_schema_version(connection))
                original(connection)
                if observed_versions[-1] == 20:
                    raise storage.StorageError(
                        "unreadable_project_state", "injected pre-marker failure"
                    )

            with mock.patch.object(
                storage, "_validate_schema21_owned_contract", fail_pre_marker
            ):
                with self.assertRaises(storage.StorageError):
                    storage.rehearse_schema21_storage(initialized.target.db_path)
            self.assertEqual(observed_versions, [20])
            with closing(storage.connect(initialized.target.db_path)) as connection:
                self.assertEqual(storage.current_schema_version(connection), 20)
                self.assertEqual(logical_database_digest(connection), before)
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM schema_migrations WHERE version=21"
                    ).fetchone()
                )


if __name__ == "__main__":
    unittest.main()
