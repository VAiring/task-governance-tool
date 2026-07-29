import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    SCHEMA_VERSION,
    SQLITE_INT64_MAX,
    DatabaseTarget,
    ProjectIdentity,
    StorageError,
    UnboundDatabaseTarget,
    apply_project_identity_bindings_migration,
    apply_completion_commit_migration,
    apply_completion_evidence_migration,
    apply_effort_advisory_migration,
    apply_git_snapshot_schema_migration,
    apply_handoff_outbox_migration,
    apply_initial_schema_migration,
    apply_managed_backup_generations_migration,
    apply_paused_state_migration,
    apply_project_maintenance_migration,
    apply_review_evidence_migration,
    apply_task_checkpoints_migration,
    apply_task_contract_migration,
    apply_viewer_maintenance_migration,
    compare_and_swap_project_binding,
    connect,
    current_schema_version,
    ensure_project_meta,
    initialize_database,
    initialize_uuid_database,
    project_identity,
    read_project_binding_state,
    required_schema_objects_missing,
)
from tests.m14_test_support import (  # noqa: E402
    create_v12_target,
    make_legacy_physical_install as make_physical_install,
)


MIGRATION_TIME = "2026-07-29T01:02:03Z"
REBIND_TIME = "2026-07-29T02:03:04Z"
UUID_HEX = "00112233445546778899aabbccddeeff"
TOKEN_DIGEST = "d" * 64
EXPECTED_TRIGGERS = {
    "trg_project_meta_identity_immutable",
    "trg_project_meta_no_delete",
    "trg_project_meta_cleanup_insert_valid",
    "trg_project_meta_cleanup_update_valid",
    "trg_project_path_binding_history_no_update",
    "trg_project_path_binding_history_no_delete",
}
EXPECTED_PROJECT_COLUMNS = {
    "identity_scheme",
    "binding_generation",
    "binding_reason",
    "binding_updated_at",
    "legacy_cleanup_pending",
    "legacy_cleanup_inventory",
    "legacy_cleanup_fingerprint",
}
MIGRATION_STEPS = (
    (2, apply_completion_commit_migration),
    (3, apply_paused_state_migration),
    (4, apply_completion_evidence_migration),
    (5, apply_review_evidence_migration),
    (6, apply_git_snapshot_schema_migration),
    (7, apply_handoff_outbox_migration),
    (8, apply_task_contract_migration),
    (9, apply_effort_advisory_migration),
    (10, apply_project_maintenance_migration),
    (11, apply_managed_backup_generations_migration),
    (12, apply_task_checkpoints_migration),
    (13, apply_viewer_maintenance_migration),
)


def create_v13_target(target: DatabaseTarget, *, display_name: str | None = None) -> None:
    create_v12_target(target)
    with closing(connect(target.db_path)) as connection:
        apply_viewer_maintenance_migration(connection)
        if display_name is not None:
            connection.execute(
                "UPDATE project_meta SET display_name = ?",
                (display_name,),
            )
            connection.commit()


def migrate_to_v14(target: DatabaseTarget) -> None:
    with (
        closing(connect(target.db_path)) as connection,
        mock.patch(
            "task_governance_tool.storage.utc_now",
            return_value=MIGRATION_TIME,
        ),
    ):
        apply_project_identity_bindings_migration(connection)


def create_source_version(target: DatabaseTarget, source_version: int) -> None:
    target.db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(target.db_path)) as connection:
        apply_initial_schema_migration(connection)
        ensure_project_meta(connection, target.project)
        connection.execute(
            """
            INSERT INTO tasks(
              task_id, project_id, title, description, kind, lane, lane_order,
              priority, status, blocked_reason, review_tier, verification,
              tags, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tg_task_v14_matrix",
                target.project.project_id,
                "Schema v14 migration sentinel",
                "Sanitized deterministic migration record.",
                "sequential",
                "TG-M17-IDENTITY",
                1,
                "normal",
                "ready",
                "",
                2,
                "offline migration verification",
                "migration,sentinel",
                "2026-07-29T00:00:00Z",
                "2026-07-29T00:00:00Z",
                None,
            ),
        )
        connection.execute(
            """
            INSERT INTO task_events(
              task_event_id, task_id, project_id, event_type, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "tg_event_v14_matrix",
                "tg_task_v14_matrix",
                target.project.project_id,
                "task_added",
                "Recorded schema v14 migration sentinel.",
                "2026-07-29T00:00:01Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO tool_events(
              tool_event_id, project_id, command, status, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "tg_tool_event_v14_matrix",
                target.project.project_id,
                "migration sentinel",
                "succeeded",
                "Recorded sanitized migration sentinel.",
                "2026-07-29T00:00:02Z",
            ),
        )
        connection.commit()
        for version, migration in MIGRATION_STEPS:
            if version > source_version:
                break
            migration(connection)
            if connection.in_transaction:
                connection.commit()


def business_record_projection(db_path: Path) -> tuple[tuple[tuple, ...], ...]:
    with closing(sqlite3.connect(db_path)) as connection:
        return (
            tuple(
                connection.execute(
                    """
                    SELECT task_id, project_id, title, description, kind, lane,
                           lane_order, priority, status, blocked_reason,
                           review_tier, verification, tags, created_at,
                           updated_at, completed_at
                      FROM tasks
                     ORDER BY task_id
                    """
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    """
                    SELECT task_event_id, task_id, project_id, event_type,
                           summary, created_at
                      FROM task_events
                     ORDER BY task_event_id
                    """
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    """
                    SELECT tool_event_id, project_id, command, status, summary,
                           created_at
                      FROM tool_events
                     ORDER BY tool_event_id
                    """
                ).fetchall()
            ),
        )


def logical_database_state(db_path: Path) -> tuple[tuple[tuple, ...], dict[str, tuple]]:
    with closing(sqlite3.connect(db_path)) as connection:
        objects = tuple(
            connection.execute(
                """
                SELECT type, name, tbl_name, sql
                  FROM sqlite_master
                 WHERE name NOT LIKE 'sqlite_%'
                 ORDER BY type, name
                """
            ).fetchall()
        )
        tables = [
            row[0]
            for row in connection.execute(
                """
                SELECT name
                  FROM sqlite_master
                 WHERE type = 'table'
                   AND name NOT LIKE 'sqlite_%'
                 ORDER BY name
                """
            ).fetchall()
        ]
        rows = {
            table: tuple(
                sorted(
                    (
                        tuple(row)
                        for row in connection.execute(
                            f'SELECT * FROM "{table}"'
                        ).fetchall()
                    ),
                    key=repr,
                )
            )
            for table in tables
        }
    return objects, rows


def rebound_target(target: DatabaseTarget, name: str = "moved-project") -> DatabaseTarget:
    moved = project_identity(target.project.canonical_repo.parent / name)
    return DatabaseTarget(
        project=ProjectIdentity(
            project_id=target.project.project_id,
            canonical_repo=moved.canonical_repo,
            canonical_path_hash=moved.canonical_path_hash,
            display_name=moved.display_name,
        ),
        db_path=target.db_path,
        explicit_db=True,
    )


def apply_rebind(
    source: DatabaseTarget,
    destination: DatabaseTarget,
    *,
    fail_stage: str | None = None,
):
    return compare_and_swap_project_binding(
        destination,
        project_id=source.project.project_id,
        identity_scheme="legacy_path_v1",
        expected_generation=1,
        expected_old_hash=source.project.canonical_path_hash,
        new_hash=destination.project.canonical_path_hash,
        new_display_name=destination.project.display_name,
        reason="confirmed_relocation",
        confirmation_token_digest=TOKEN_DIGEST,
        bound_at=REBIND_TIME,
        fail_stage=fail_stage,
    )


class ProjectIdentityBindingTests(unittest.TestCase):
    def test_v13_to_v14_adds_exact_identity_schema_and_history_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v13_target(install.target)
            with closing(connect(install.db_path)) as connection:
                before_project = tuple(
                    connection.execute(
                        """
                        SELECT project_id, canonical_path_hash, display_name,
                               created_at, updated_at
                          FROM project_meta
                        """
                    ).fetchone()
                )
                before_viewer = tuple(
                    connection.execute(
                        """
                        SELECT source_generation, rendered_generation
                          FROM viewer_maintenance_state
                        """
                    ).fetchone()
                )

            migrate_to_v14(install.target)

            with closing(connect(install.db_path)) as connection:
                self.assertEqual(SCHEMA_VERSION, 14)
                self.assertEqual(current_schema_version(connection), 14)
                migration = tuple(
                    connection.execute(
                        """
                        SELECT name, applied_at
                          FROM schema_migrations
                         WHERE version = 14
                        """
                    ).fetchone()
                )
                self.assertEqual(
                    migration,
                    ("project_identity_bindings", MIGRATION_TIME),
                )
                project_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(project_meta)"
                    ).fetchall()
                }
                self.assertTrue(EXPECTED_PROJECT_COLUMNS <= project_columns)
                triggers = {
                    str(row["name"])
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                         WHERE type = 'trigger'
                        """
                    ).fetchall()
                }
                self.assertTrue(EXPECTED_TRIGGERS <= triggers)
                self.assertEqual(required_schema_objects_missing(connection), [])
                after_project = tuple(
                    connection.execute(
                        """
                        SELECT project_id, canonical_path_hash, display_name,
                               created_at, updated_at
                          FROM project_meta
                        """
                    ).fetchone()
                )
                self.assertEqual(after_project, before_project)
                binding = read_project_binding_state(
                    connection,
                    expected_project_id=install.project_id,
                )
                self.assertEqual(binding.identity_scheme, "legacy_path_v1")
                self.assertEqual(binding.binding_generation, 1)
                self.assertEqual(binding.binding_reason, "legacy_migration")
                self.assertEqual(binding.binding_updated_at, MIGRATION_TIME)
                history = tuple(
                    connection.execute(
                        """
                        SELECT project_id, binding_generation,
                               previous_path_hash, canonical_path_hash,
                               display_name, reason,
                               confirmation_token_digest, bound_at
                          FROM project_path_binding_history
                        """
                    ).fetchone()
                )
                self.assertEqual(
                    history,
                    (
                        install.project_id,
                        1,
                        None,
                        install.target.project.canonical_path_hash,
                        install.target.project.display_name,
                        "legacy_migration",
                        None,
                        MIGRATION_TIME,
                    ),
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            """
                            SELECT source_generation, rendered_generation
                              FROM viewer_maintenance_state
                            """
                        ).fetchone()
                    ),
                    before_viewer,
                )

            before_replay = logical_database_state(install.db_path)
            migrate_to_v14(install.target)
            self.assertEqual(logical_database_state(install.db_path), before_replay)

    def test_every_v1_through_v13_source_preserves_legacy_identity_at_v14(self):
        for source_version in range(1, 14):
            with (
                self.subTest(source_version=source_version),
                tempfile.TemporaryDirectory() as tmp,
            ):
                install = make_physical_install(Path(tmp))
                create_source_version(install.target, source_version)
                original_project_id = install.project_id
                original_records = business_record_projection(install.db_path)

                result = initialize_database(install.target)

                self.assertEqual(
                    result.migrations_applied,
                    list(range(source_version + 1, 15)),
                )
                self.assertEqual(result.schema_version, 14)
                self.assertEqual(
                    business_record_projection(install.db_path),
                    original_records,
                )
                with closing(connect(install.db_path)) as connection:
                    binding = read_project_binding_state(connection)
                    self.assertEqual(binding.project_id, original_project_id)
                    self.assertEqual(binding.identity_scheme, "legacy_path_v1")
                    self.assertEqual(binding.binding_generation, 1)
                    self.assertEqual(binding.binding_reason, "legacy_migration")
                    self.assertEqual(
                        [
                            int(row[0])
                            for row in connection.execute(
                                "SELECT version FROM schema_migrations ORDER BY version"
                            ).fetchall()
                        ],
                        list(range(1, 15)),
                    )

    def test_v14_migration_normalizes_only_the_bounded_display_name(self):
        cases = (
            ("Ordinary Project", "Ordinary Project"),
            ("", "project"),
            ("\x00A\nB\u2028C\u2029", "\ufffdA\ufffdB\ufffdC\ufffd"),
            ("x" * 201, "x" * 200),
        )
        for original, expected in cases:
            with self.subTest(original=repr(original)), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v13_target(install.target, display_name=original)
                with closing(connect(install.db_path)) as connection:
                    immutable_before = tuple(
                        connection.execute(
                            """
                            SELECT project_id, canonical_path_hash,
                                   created_at, updated_at
                              FROM project_meta
                            """
                        ).fetchone()
                    )

                migrate_to_v14(install.target)

                with closing(connect(install.db_path)) as connection:
                    immutable_after = tuple(
                        connection.execute(
                            """
                            SELECT project_id, canonical_path_hash,
                                   created_at, updated_at
                              FROM project_meta
                            """
                        ).fetchone()
                    )
                    self.assertEqual(immutable_after, immutable_before)
                    self.assertEqual(
                        connection.execute(
                            "SELECT display_name FROM project_meta"
                        ).fetchone()[0],
                        expected,
                    )
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT display_name
                              FROM project_path_binding_history
                             WHERE binding_generation = 1
                            """
                        ).fetchone()[0],
                        expected,
                    )

    def test_v14_migration_rolls_back_each_injected_boundary(self):
        for fail_stage in ("after_columns", "after_history", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                create_v13_target(install.target)
                before = logical_database_state(install.db_path)

                with (
                    closing(connect(install.db_path)) as connection,
                    mock.patch(
                        "task_governance_tool.storage.utc_now",
                        return_value=MIGRATION_TIME,
                    ),
                    self.assertRaises(StorageError),
                ):
                    apply_project_identity_bindings_migration(
                        connection,
                        fail_stage=fail_stage,
                    )

                self.assertEqual(logical_database_state(install.db_path), before)
                with closing(connect(install.db_path)) as connection:
                    self.assertEqual(current_schema_version(connection), 13)
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT 1 FROM sqlite_master
                             WHERE type = 'table'
                               AND name = 'project_path_binding_history'
                            """
                        ).fetchone()
                    )

                migrate_to_v14(install.target)
                with closing(connect(install.db_path)) as connection:
                    self.assertEqual(current_schema_version(connection), 14)

    def test_identity_and_history_triggers_are_value_free_and_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v13_target(install.target)
            migrate_to_v14(install.target)
            cases = (
                (
                    "UPDATE project_meta SET project_id = ?",
                    ("other-aaaaaaaaaaaa",),
                    "project identity metadata is immutable",
                ),
                (
                    "UPDATE project_meta SET identity_scheme = 'uuid_v1'",
                    (),
                    "project identity metadata is immutable",
                ),
                (
                    "UPDATE project_meta SET created_at = ?",
                    ("2026-07-29T03:04:05Z",),
                    "project identity metadata is immutable",
                ),
                (
                    "DELETE FROM project_meta",
                    (),
                    "project metadata cannot be deleted",
                ),
                (
                    "UPDATE project_path_binding_history SET display_name = ?",
                    ("must-not-appear",),
                    "project binding history is append-only",
                ),
                (
                    "DELETE FROM project_path_binding_history",
                    (),
                    "project binding history is append-only",
                ),
                (
                    """
                    UPDATE project_meta
                       SET legacy_cleanup_pending = 1,
                           legacy_cleanup_inventory = NULL,
                           legacy_cleanup_fingerprint = NULL
                    """,
                    (),
                    "legacy cleanup metadata is invalid",
                ),
            )
            with closing(connect(install.db_path)) as connection:
                for statement, parameters, expected_message in cases:
                    with self.subTest(statement=" ".join(statement.split())):
                        with self.assertRaises(sqlite3.IntegrityError) as raised:
                            connection.execute(statement, parameters)
                        self.assertEqual(str(raised.exception), expected_message)
                        self.assertNotIn("must-not-appear", str(raised.exception))
                        connection.rollback()
                self.assertEqual(
                    read_project_binding_state(connection).binding_generation,
                    1,
                )

    def test_binding_validator_rejects_lineage_and_cleanup_corruption_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v13_target(install.target)
            migrate_to_v14(install.target)
            with closing(connect(install.db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO project_path_binding_history(
                      project_id, binding_generation, previous_path_hash,
                      canonical_path_hash, display_name, reason,
                      confirmation_token_digest, bound_at
                    ) VALUES (?, 2, ?, ?, ?, 'confirmed_relocation', ?, ?)
                    """,
                    (
                        install.project_id,
                        install.target.project.canonical_path_hash,
                        "b" * 64,
                        install.target.project.display_name,
                        "c" * 64,
                        REBIND_TIME,
                    ),
                )
                with self.assertRaises(StorageError) as raised:
                    read_project_binding_state(connection)
                self.assertEqual(
                    (raised.exception.code, raised.exception.message),
                    (
                        "project_state_unreadable",
                        "project state could not be read safely",
                    ),
                )
                connection.rollback()

                valid_inventory = json.dumps(
                    {
                        "entries": [
                            {
                                "kind": "file",
                                "name": "taskgov.sqlite",
                                "sha256": "a" * 64,
                                "size": 1,
                            }
                        ],
                        "v": 1,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                valid_fingerprint = hashlib.sha256(
                    valid_inventory.encode("ascii")
                ).hexdigest()
                connection.execute(
                    """
                    UPDATE project_meta
                       SET legacy_cleanup_pending = 1,
                           legacy_cleanup_inventory = ?,
                           legacy_cleanup_fingerprint = ?
                    """,
                    (valid_inventory, valid_fingerprint),
                )
                self.assertTrue(
                    read_project_binding_state(connection).legacy_cleanup_pending
                )
                connection.rollback()

                inventory = '["../private-path"]'
                fingerprint = hashlib.sha256(inventory.encode("ascii")).hexdigest()
                connection.execute(
                    """
                    UPDATE project_meta
                       SET legacy_cleanup_pending = 1,
                           legacy_cleanup_inventory = ?,
                           legacy_cleanup_fingerprint = ?
                    """,
                    (inventory, fingerprint),
                )
                with self.assertRaises(StorageError) as raised:
                    read_project_binding_state(connection)
                self.assertEqual(
                    (raised.exception.code, raised.exception.message),
                    (
                        "project_state_unreadable",
                        "project state could not be read safely",
                    ),
                )
                self.assertNotIn("private-path", raised.exception.message)
                connection.rollback()

                noninteger_version_inventory = valid_inventory.replace(
                    '"v":1',
                    '"v":1.0',
                )
                noninteger_fingerprint = hashlib.sha256(
                    noninteger_version_inventory.encode("ascii")
                ).hexdigest()
                connection.execute(
                    """
                    UPDATE project_meta
                       SET legacy_cleanup_pending = 1,
                           legacy_cleanup_inventory = ?,
                           legacy_cleanup_fingerprint = ?
                    """,
                    (noninteger_version_inventory, noninteger_fingerprint),
                )
                with self.assertRaises(StorageError):
                    read_project_binding_state(connection)
                connection.rollback()

    def test_binding_validator_rejects_foreign_lineage_and_malformed_id_privately(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialize_database(install.target)
            with closing(sqlite3.connect(install.db_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """
                    INSERT INTO project_path_binding_history(
                      project_id, binding_generation, previous_path_hash,
                      canonical_path_hash, display_name, reason,
                      confirmation_token_digest, bound_at
                    ) VALUES (?, 1, NULL, ?, ?, 'legacy_migration', NULL, ?)
                    """,
                    (
                        "foreign-aaaaaaaaaaaa",
                        "a" * 64,
                        "foreign",
                        MIGRATION_TIME,
                    ),
                )
                connection.commit()
            with closing(connect(install.db_path)) as connection:
                with self.assertRaises(StorageError) as raised:
                    read_project_binding_state(connection)
            self.assertEqual(
                (raised.exception.code, raised.exception.message),
                (
                    "project_state_unreadable",
                    "project state could not be read safely",
                ),
            )

        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v13_target(install.target)
            private_value = r"C:\private\project"
            with closing(sqlite3.connect(install.db_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                for table in (
                    "project_maintenance",
                    "viewer_maintenance_state",
                ):
                    connection.execute(
                        f"UPDATE {table} SET project_id = ?",
                        (private_value,),
                    )
                connection.execute(
                    "UPDATE project_meta SET project_id = ?",
                    (private_value,),
                )
                connection.commit()
            before = logical_database_state(install.db_path)

            with self.assertRaises(StorageError) as raised:
                initialize_database(install.target)

            self.assertEqual(
                (raised.exception.code, raised.exception.message),
                (
                    "project_state_unreadable",
                    "project state could not be read safely",
                ),
            )
            self.assertNotIn(private_value, raised.exception.message)
            self.assertEqual(logical_database_state(install.db_path), before)

    def test_uuid_initialization_is_explicit_injected_and_transactionally_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "governed-project"
            repo.mkdir()
            legacy = project_identity(repo)
            target = DatabaseTarget(
                project=legacy,
                db_path=(root / "state" / "current" / "taskgov.sqlite").resolve(),
                explicit_db=True,
            )
            id_factory = mock.Mock(return_value=UUID_HEX)
            clock = mock.Mock(return_value=MIGRATION_TIME)

            result = initialize_uuid_database(
                target,
                project_id_factory=id_factory,
                clock=clock,
            )

            expected_id = f"tg_project_{UUID_HEX}"
            self.assertEqual(result.schema_version, 14)
            self.assertEqual(result.target.project.project_id, expected_id)
            self.assertEqual(result.target.db_path, target.db_path)
            id_factory.assert_called_once_with()
            clock.assert_called_once_with()
            self.assertFalse((root / "state" / "projects").exists())
            with closing(connect(target.db_path)) as connection:
                binding = read_project_binding_state(
                    connection,
                    expected_project_id=expected_id,
                )
                self.assertEqual(binding.identity_scheme, "uuid_v1")
                self.assertEqual(binding.binding_generation, 1)
                self.assertEqual(binding.binding_reason, "fresh_setup")
                self.assertEqual(binding.binding_updated_at, MIGRATION_TIME)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM project_path_binding_history"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            """
                            SELECT source_generation, rendered_generation
                              FROM viewer_maintenance_state
                             WHERE project_id = ?
                            """,
                            (expected_id,),
                        ).fetchone()
                    ),
                    (0, None),
                )
                self.assertIsNotNone(
                    connection.execute(
                        """
                        SELECT 1 FROM project_maintenance
                         WHERE project_id = ?
                        """,
                        (expected_id,),
                    ).fetchone()
                )

            before_replay = logical_database_state(target.db_path)
            with self.assertRaises(StorageError) as raised:
                initialize_uuid_database(
                    target,
                    project_id_factory=lambda: UUID_HEX,
                    clock=lambda: MIGRATION_TIME,
                )
            self.assertEqual(raised.exception.code, "internal_error")
            self.assertEqual(logical_database_state(target.db_path), before_replay)

    def test_unbound_uuid_initialization_has_no_placeholder_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = (root / "governed-project").resolve()
            repo.mkdir()
            observed = project_identity(repo)
            target = UnboundDatabaseTarget(
                canonical_repo=observed.canonical_repo,
                canonical_path_hash=observed.canonical_path_hash,
                display_name=observed.display_name,
                db_path=(root / "state" / "current" / "taskgov.sqlite").resolve(),
            )

            self.assertTrue(target.explicit_db)
            self.assertFalse(hasattr(target, "project"))
            self.assertFalse(hasattr(target, "project_id"))
            with self.assertRaises(FrozenInstanceError):
                target.display_name = "changed"  # type: ignore[misc]

            with mock.patch(
                "task_governance_tool.storage.project_identity",
                side_effect=AssertionError("path-derived identity must not be created"),
            ) as path_identity:
                result = initialize_uuid_database(
                    target,
                    project_id_factory=lambda: UUID_HEX,
                    clock=lambda: MIGRATION_TIME,
                )

            path_identity.assert_not_called()
            expected_id = f"tg_project_{UUID_HEX}"
            self.assertEqual(result.target.project.project_id, expected_id)
            self.assertEqual(result.target.project.canonical_repo, target.canonical_repo)
            self.assertEqual(
                result.target.project.canonical_path_hash,
                target.canonical_path_hash,
            )
            self.assertEqual(result.target.project.display_name, target.display_name)
            self.assertEqual(result.target.db_path, target.db_path)
            self.assertTrue(result.target.explicit_db)
            with closing(connect(target.db_path)) as connection:
                binding = read_project_binding_state(
                    connection,
                    expected_project_id=expected_id,
                )
                self.assertEqual(
                    (
                        binding.identity_scheme,
                        binding.binding_generation,
                        binding.binding_reason,
                        binding.binding_updated_at,
                    ),
                    ("uuid_v1", 1, "fresh_setup", MIGRATION_TIME),
                )

    def test_unbound_uuid_initializer_requires_the_exact_fixed_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = (root / "governed-project").resolve()
            repo.mkdir()
            observed = project_identity(repo)
            valid = UnboundDatabaseTarget(
                canonical_repo=observed.canonical_repo,
                canonical_path_hash=observed.canonical_path_hash,
                display_name=observed.display_name,
                db_path=(root / "state" / "current" / "taskgov.sqlite").resolve(),
            )
            invalid_targets = (
                replace(valid, explicit_db=False),
                replace(valid, db_path=root / "state" / "current" / "other.sqlite"),
                replace(valid, db_path=root / "state" / "other" / "taskgov.sqlite"),
                replace(valid, db_path=root / "other" / "current" / "taskgov.sqlite"),
                replace(valid, db_path=Path("state/current/taskgov.sqlite")),
            )

            for target in invalid_targets:
                with self.subTest(target=target):
                    with self.assertRaises(StorageError) as raised:
                        initialize_uuid_database(
                            target,
                            project_id_factory=lambda: UUID_HEX,
                            clock=lambda: MIGRATION_TIME,
                        )
                    self.assertEqual(
                        (
                            raised.exception.code,
                            raised.exception.message,
                        ),
                        (
                            "internal_error",
                            "fixed database target is invalid",
                        ),
                    )
            self.assertFalse((root / "state").exists())

    def test_uuid_initializer_rejects_nonexplicit_target_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "governed-project"
            repo.mkdir()
            target = DatabaseTarget(
                project=project_identity(repo),
                db_path=(root / "state" / "current" / "taskgov.sqlite").resolve(),
                explicit_db=False,
            )

            with self.assertRaises(StorageError) as raised:
                initialize_uuid_database(
                    target,
                    project_id_factory=lambda: UUID_HEX,
                    clock=lambda: MIGRATION_TIME,
                )

            self.assertEqual(raised.exception.code, "internal_error")
            self.assertFalse((root / "state").exists())

    def test_uuid_initializer_rejects_invalid_factory_values_before_writing(self):
        cases = (
            ("0" * 32, MIGRATION_TIME),
            ("A" * 32, MIGRATION_TIME),
            (UUID_HEX, "not-a-time"),
        )
        for raw_id, clock_value in cases:
            with (
                self.subTest(raw_id=raw_id, clock=clock_value),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                repo = root / "governed-project"
                repo.mkdir()
                target = DatabaseTarget(
                    project=project_identity(repo),
                    db_path=(
                        root / "state" / "current" / "taskgov.sqlite"
                    ).resolve(),
                    explicit_db=True,
                )
                with self.assertRaises(StorageError):
                    initialize_uuid_database(
                        target,
                        project_id_factory=lambda value=raw_id: value,
                        clock=lambda value=clock_value: value,
                    )
                self.assertFalse((root / "state").exists())

    def test_uuid_initializer_injected_failures_leave_no_fixed_state(self):
        for target_kind in ("bound", "unbound"):
            for fail_stage in ("after_project_row", "after_history_row"):
                with (
                    self.subTest(target_kind=target_kind, fail_stage=fail_stage),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    root = Path(tmp)
                    repo = root / "governed-project"
                    repo.mkdir()
                    observed = project_identity(repo)
                    db_path = (
                        root / "state" / "current" / "taskgov.sqlite"
                    ).resolve()
                    target: DatabaseTarget | UnboundDatabaseTarget
                    if target_kind == "bound":
                        target = DatabaseTarget(
                            project=observed,
                            db_path=db_path,
                            explicit_db=True,
                        )
                    else:
                        target = UnboundDatabaseTarget(
                            canonical_repo=observed.canonical_repo,
                            canonical_path_hash=observed.canonical_path_hash,
                            display_name=observed.display_name,
                            db_path=db_path,
                        )

                    with self.assertRaises(StorageError):
                        initialize_uuid_database(
                            target,
                            project_id_factory=lambda: UUID_HEX,
                            clock=lambda: MIGRATION_TIME,
                            fail_stage=fail_stage,
                        )

                    self.assertFalse((root / "state").exists())
                    self.assertFalse(target.db_path.exists())
                    for suffix in ("-journal", "-wal", "-shm"):
                        self.assertFalse(Path(f"{target.db_path}{suffix}").exists())

                    result = initialize_uuid_database(
                        target,
                        project_id_factory=lambda: UUID_HEX,
                        clock=lambda: MIGRATION_TIME,
                    )
                    self.assertEqual(
                        result.target.project.project_id,
                        f"tg_project_{UUID_HEX}",
                    )

    def test_uuid_initializer_rejects_invalid_target_metadata_and_sidecars(self):
        cases = (
            ("invalid-hash", "g" * 64, "governed-project", None),
            (
                "absolute-display",
                None,
                "C:\\private\\governed-project",
                None,
            ),
            ("preexisting-sidecar", None, "governed-project", "-journal"),
        )
        for label, hash_override, display_override, sidecar_suffix in cases:
            with (
                self.subTest(case=label),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                repo = root / "governed-project"
                repo.mkdir()
                identity = project_identity(repo)
                target = DatabaseTarget(
                    project=ProjectIdentity(
                        project_id=identity.project_id,
                        canonical_repo=identity.canonical_repo,
                        canonical_path_hash=(
                            hash_override or identity.canonical_path_hash
                        ),
                        display_name=display_override,
                    ),
                    db_path=(
                        root / "state" / "current" / "taskgov.sqlite"
                    ).resolve(),
                    explicit_db=True,
                )
                sidecar = (
                    Path(f"{target.db_path}{sidecar_suffix}")
                    if sidecar_suffix is not None
                    else None
                )
                if sidecar is not None:
                    sidecar.parent.mkdir(parents=True)
                    sidecar.touch()

                with self.assertRaises(StorageError):
                    initialize_uuid_database(
                        target,
                        project_id_factory=lambda: UUID_HEX,
                        clock=lambda: MIGRATION_TIME,
                    )

                self.assertFalse(target.db_path.exists())
                if sidecar is None:
                    self.assertFalse((root / "state").exists())
                else:
                    self.assertTrue(sidecar.is_file())

    def test_legacy_initializer_remains_available_for_transition_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            with mock.patch(
                "task_governance_tool.storage.uuid.uuid4",
                side_effect=AssertionError("legacy initializer must not use UUID"),
            ):
                result = initialize_database(install.target)

            self.assertEqual(result.schema_version, 14)
            self.assertRegex(result.target.project.project_id, r"-[0-9a-f]{12}$")
            self.assertTrue(install.db_path.is_file())
            self.assertIn("projects", install.db_path.parts)
            self.assertFalse((install.skill_root / "state" / "current").exists())
            with closing(connect(install.db_path)) as connection:
                binding = read_project_binding_state(
                    connection,
                    expected_project_id=install.project_id,
                )
                self.assertEqual(binding.identity_scheme, "legacy_path_v1")
                self.assertEqual(binding.binding_reason, "legacy_migration")
                self.assertEqual(binding.binding_generation, 1)
            long_name = "a" * 201
            long_identity = project_identity(Path(tmp) / long_name)
            self.assertTrue(long_identity.project_id.startswith(long_name + "-"))
            self.assertEqual(len(long_identity.display_name), 200)

    def test_binding_cas_updates_history_current_and_viewer_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialize_database(install.target)
            destination = rebound_target(install.target)

            updated = apply_rebind(install.target, destination)

            self.assertEqual(updated.binding_generation, 2)
            self.assertEqual(
                updated.canonical_path_hash,
                destination.project.canonical_path_hash,
            )
            self.assertEqual(updated.display_name, destination.project.display_name)
            self.assertEqual(updated.binding_reason, "confirmed_relocation")
            self.assertEqual(updated.binding_updated_at, REBIND_TIME)
            with closing(connect(install.db_path)) as connection:
                history = connection.execute(
                    """
                    SELECT previous_path_hash, canonical_path_hash, display_name,
                           reason, confirmation_token_digest, bound_at
                      FROM project_path_binding_history
                     WHERE binding_generation = 2
                    """
                ).fetchone()
                self.assertEqual(
                    tuple(history),
                    (
                        install.target.project.canonical_path_hash,
                        destination.project.canonical_path_hash,
                        destination.project.display_name,
                        "confirmed_relocation",
                        TOKEN_DIGEST,
                        REBIND_TIME,
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT source_generation
                          FROM viewer_maintenance_state
                         WHERE project_id = ?
                        """,
                        (install.project_id,),
                    ).fetchone()[0],
                    1,
                )

            before_rejections = logical_database_state(install.db_path)
            with self.assertRaises(StorageError) as stale:
                apply_rebind(install.target, destination)
            self.assertEqual(
                (stale.exception.code, stale.exception.message),
                ("project_binding_stale", "project binding state changed"),
            )
            self.assertEqual(logical_database_state(install.db_path), before_rejections)

            with self.assertRaises(StorageError) as unchanged:
                compare_and_swap_project_binding(
                    destination,
                    project_id=install.project_id,
                    identity_scheme="legacy_path_v1",
                    expected_generation=2,
                    expected_old_hash=destination.project.canonical_path_hash,
                    new_hash=destination.project.canonical_path_hash,
                    new_display_name=destination.project.display_name,
                    reason="confirmed_relocation",
                    confirmation_token_digest=TOKEN_DIGEST,
                    bound_at=REBIND_TIME,
                )
            self.assertEqual(unchanged.exception.code, "internal_error")
            self.assertEqual(logical_database_state(install.db_path), before_rejections)

    def test_binding_cas_rejects_invalid_fields_before_writing(self):
        cases = (
            ("expected_old_hash", "g" * 64),
            ("new_hash", "g" * 64),
            ("new_display_name", "x" * 201),
            ("new_display_name", "C:\\private\\governed-project"),
            ("reason", "manual_rebind"),
            ("confirmation_token_digest", "g" * 64),
            ("bound_at", "not-a-time"),
        )
        for field, value in cases:
            with (
                self.subTest(field=field, value=value),
                tempfile.TemporaryDirectory() as tmp,
            ):
                install = make_physical_install(Path(tmp))
                initialize_database(install.target)
                destination = rebound_target(install.target)
                before = logical_database_state(install.db_path)
                arguments = {
                    "project_id": install.project_id,
                    "identity_scheme": "legacy_path_v1",
                    "expected_generation": 1,
                    "expected_old_hash": (
                        install.target.project.canonical_path_hash
                    ),
                    "new_hash": destination.project.canonical_path_hash,
                    "new_display_name": destination.project.display_name,
                    "reason": "confirmed_relocation",
                    "confirmation_token_digest": TOKEN_DIGEST,
                    "bound_at": REBIND_TIME,
                }
                arguments[field] = value

                with self.assertRaises(StorageError) as raised:
                    compare_and_swap_project_binding(destination, **arguments)

                self.assertEqual(logical_database_state(install.db_path), before)
                self.assertNotIn("private", raised.exception.message)

    def test_binding_cas_rejects_binding_generation_overflow_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialize_database(install.target)
            destination = rebound_target(install.target)
            with closing(connect(install.db_path)) as connection:
                maximum_binding = replace(
                    read_project_binding_state(connection),
                    binding_generation=SQLITE_INT64_MAX,
                )
            before = logical_database_state(install.db_path)

            with (
                mock.patch(
                    "task_governance_tool.storage.read_project_binding_state",
                    return_value=maximum_binding,
                ),
                self.assertRaises(StorageError) as raised,
            ):
                compare_and_swap_project_binding(
                    destination,
                    project_id=install.project_id,
                    identity_scheme="legacy_path_v1",
                    expected_generation=SQLITE_INT64_MAX,
                    expected_old_hash=install.target.project.canonical_path_hash,
                    new_hash=destination.project.canonical_path_hash,
                    new_display_name=destination.project.display_name,
                    reason="confirmed_relocation",
                    confirmation_token_digest=TOKEN_DIGEST,
                    bound_at=REBIND_TIME,
                )

            self.assertEqual(
                (raised.exception.code, raised.exception.message),
                (
                    "project_state_unreadable",
                    "project state could not be read safely",
                ),
            )
            self.assertEqual(logical_database_state(install.db_path), before)

    def test_binding_validator_rejects_real_generation_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialize_database(install.target)
            destination = rebound_target(install.target)
            with closing(connect(install.db_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO project_path_binding_history(
                      project_id, binding_generation, previous_path_hash,
                      canonical_path_hash, display_name, reason,
                      confirmation_token_digest, bound_at
                    ) VALUES (?, 2.5, ?, ?, ?, 'confirmed_relocation', ?, ?)
                    """,
                    (
                        install.project_id,
                        install.target.project.canonical_path_hash,
                        destination.project.canonical_path_hash,
                        destination.project.display_name,
                        TOKEN_DIGEST,
                        REBIND_TIME,
                    ),
                )
                connection.execute(
                    """
                    UPDATE project_meta
                       SET canonical_path_hash = ?,
                           display_name = ?,
                           binding_generation = 2.5,
                           binding_reason = 'confirmed_relocation',
                           binding_updated_at = ?,
                           updated_at = ?
                    """,
                    (
                        destination.project.canonical_path_hash,
                        destination.project.display_name,
                        REBIND_TIME,
                        REBIND_TIME,
                    ),
                )
                connection.commit()

                with self.assertRaises(StorageError) as raised:
                    read_project_binding_state(connection)

            self.assertEqual(
                (raised.exception.code, raised.exception.message),
                (
                    "project_state_unreadable",
                    "project state could not be read safely",
                ),
            )

    def test_binding_cas_rejects_real_viewer_generation_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialize_database(install.target)
            destination = rebound_target(install.target)
            with closing(connect(install.db_path)) as connection:
                connection.execute(
                    "UPDATE viewer_maintenance_state SET source_generation = 0.5"
                )
                connection.commit()
            before = logical_database_state(install.db_path)

            with self.assertRaises(StorageError) as raised:
                apply_rebind(install.target, destination)

            self.assertEqual(
                (raised.exception.code, raised.exception.message),
                (
                    "project_state_unreadable",
                    "project state could not be read safely",
                ),
            )
            self.assertEqual(logical_database_state(install.db_path), before)

    def test_binding_cas_rolls_back_every_injected_write_boundary(self):
        for fail_stage in (
            "after_history",
            "after_current",
            "after_viewer",
            "before_commit",
        ):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                initialize_database(install.target)
                destination = rebound_target(install.target)
                before = logical_database_state(install.db_path)

                with self.assertRaises(StorageError):
                    apply_rebind(
                        install.target,
                        destination,
                        fail_stage=fail_stage,
                    )

                self.assertEqual(logical_database_state(install.db_path), before)
                with closing(connect(install.db_path)) as connection:
                    binding = read_project_binding_state(connection)
                    self.assertEqual(binding.binding_generation, 1)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM project_path_binding_history"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT source_generation FROM viewer_maintenance_state"
                        ).fetchone()[0],
                        0,
                    )

    def test_concurrent_binding_cas_has_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialize_database(install.target)
            destination = rebound_target(install.target)
            barrier = threading.Barrier(2)

            def attempt() -> str:
                barrier.wait(timeout=5)
                try:
                    apply_rebind(install.target, destination)
                except StorageError as exc:
                    return exc.code
                return "succeeded"

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = sorted(
                    future.result(timeout=10)
                    for future in (executor.submit(attempt), executor.submit(attempt))
                )

            self.assertEqual(
                outcomes,
                ["project_binding_stale", "succeeded"],
            )
            with closing(connect(install.db_path)) as connection:
                self.assertEqual(read_project_binding_state(connection).binding_generation, 2)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM project_path_binding_history"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT source_generation FROM viewer_maintenance_state"
                    ).fetchone()[0],
                    1,
                )

    def test_binding_cas_rejects_missing_or_overflowed_viewer_state_without_writes(self):
        mutations = (
            "DELETE FROM viewer_maintenance_state",
            (
                "UPDATE viewer_maintenance_state "
                f"SET source_generation = {SQLITE_INT64_MAX}"
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                initialize_database(install.target)
                destination = rebound_target(install.target)
                with closing(connect(install.db_path)) as connection:
                    connection.execute(mutation)
                    connection.commit()
                before = logical_database_state(install.db_path)

                with self.assertRaises(StorageError) as raised:
                    apply_rebind(install.target, destination)

                self.assertEqual(
                    (raised.exception.code, raised.exception.message),
                    (
                        "project_state_unreadable",
                        "project state could not be read safely",
                    ),
                )
                self.assertEqual(logical_database_state(install.db_path), before)


if __name__ == "__main__":
    unittest.main()
