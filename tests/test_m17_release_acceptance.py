import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


try:
    from m14_test_support import (
        json_payload,
        make_physical_install,
    )
    from test_m17_relocation_setup import (
        relocate_install,
        tree_snapshot,
    )
    from test_migration_acceptance import (
        create_realistic_review_database,
        create_realistic_v2_database,
        legacy_v2_projection,
        load_fixture,
        nonidentity_table_projection,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        json_payload,
        make_physical_install,
    )
    from tests.test_m17_relocation_setup import (
        relocate_install,
        tree_snapshot,
    )
    from tests.test_migration_acceptance import (
        create_realistic_review_database,
        create_realistic_v2_database,
        legacy_v2_projection,
        load_fixture,
        nonidentity_table_projection,
    )

from task_governance_tool.storage import project_identity


PRE_V9_WRITES = [
    "legacy_state_publish",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "viewer_publish",
    "legacy_state_cleanup",
]
MOVED_V13_WRITES = [
    "legacy_state_publish",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "project_binding_update",
    "viewer_publish",
    "legacy_state_cleanup",
]


def preserved_business_projection(connection: sqlite3.Connection) -> dict:
    """Exclude the setup-owned operational rows that are expected to change."""

    projection = nonidentity_table_projection(connection)
    for table in (
        "managed_backup_generations",
        "project_maintenance",
        "viewer_maintenance_state",
    ):
        projection.pop(table, None)
    return projection


class M17ReleaseAcceptanceTests(unittest.TestCase):
    def test_realistic_pre_v9_legacy_layout_migrates_same_binding(self):
        fixture = load_fixture()
        self.assertEqual(fixture["schema_version"], 2)

        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary))
            install.legacy_db_path.parent.mkdir(parents=True)
            project = create_realistic_v2_database(
                install.legacy_db_path,
                install.project_root,
                fixture,
            )
            with closing(sqlite3.connect(install.legacy_db_path)) as connection:
                before_records = legacy_v2_projection(connection)
            before_preview = tree_snapshot(install.project_root)

            preview_process = install.run(
                "setup",
                "--read-only",
                "--json",
            )

            self.assertEqual(
                preview_process.returncode,
                0,
                preview_process.stderr,
            )
            preview = json_payload(preview_process)
            self.assertEqual(preview["project_id"], project.project_id)
            self.assertEqual(preview["data"]["status"], "setup_preview")
            self.assertEqual(
                preview["data"]["planned_writes"],
                PRE_V9_WRITES,
            )
            self.assertEqual(preview["data"]["completed_writes"], [])
            self.assertEqual(preview["data"]["schema_from"], 2)
            self.assertEqual(preview["data"]["schema_to"], 14)
            self.assertEqual(
                preview["data"]["relocation"],
                {
                    "required": False,
                    "source_layout": "legacy_projects_v1",
                    "identity_scheme": "legacy_path_v1",
                    "binding_generation": 1,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )
            self.assertEqual(
                tree_snapshot(install.project_root),
                before_preview,
            )
            self.assertFalse(install.db_path.exists())

            migrated_process = install.run("setup", "--json")

            self.assertEqual(
                migrated_process.returncode,
                0,
                migrated_process.stderr,
            )
            migrated = json_payload(migrated_process)
            self.assertEqual(migrated["project_id"], project.project_id)
            self.assertEqual(migrated["data"]["schema_from"], 2)
            self.assertEqual(migrated["data"]["schema_to"], 14)
            self.assertEqual(
                migrated["data"]["completed_writes"],
                PRE_V9_WRITES,
            )
            self.assertTrue(install.db_path.is_file())
            self.assertFalse(install.legacy_db_path.exists())
            self.assertTrue(install.viewer_path.is_file())

            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    legacy_v2_projection(connection),
                    before_records,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    14,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM tasks"
                    ).fetchone()[0],
                    12,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events"
                    ).fetchone()[0],
                    191,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM tasks
                         WHERE completion_commit_hash != ''
                        """
                    ).fetchone()[0],
                    9,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT project_id, identity_scheme,
                               binding_generation, binding_reason
                          FROM project_meta
                        """
                    ).fetchone(),
                    (
                        project.project_id,
                        "legacy_path_v1",
                        1,
                        "legacy_migration",
                    ),
                )

    def test_realistic_v13_moved_legacy_requires_exact_confirmation(self):
        fixture = load_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = make_physical_install(root)
            original.legacy_db_path.parent.mkdir(parents=True)
            project = create_realistic_review_database(
                original.legacy_db_path,
                original.project_root,
                fixture,
                schema_version=13,
            )
            with closing(sqlite3.connect(original.legacy_db_path)) as connection:
                before_records = preserved_business_projection(connection)

            moved = relocate_install(
                original,
                destination=root / "moved-project",
            )
            moved_legacy_db = (
                moved.skill_root
                / "state"
                / "projects"
                / project.project_id
                / "taskgov.sqlite"
            )
            before_preview = tree_snapshot(moved.project_root)
            source_bytes = moved_legacy_db.read_bytes()

            preview_process = moved.run(
                "setup",
                "--read-only",
                "--json",
            )

            self.assertEqual(
                preview_process.returncode,
                0,
                preview_process.stderr,
            )
            preview = json_payload(preview_process)
            relocation = preview["data"]["relocation"]
            token = relocation["confirmation_token"]
            self.assertEqual(preview["project_id"], project.project_id)
            self.assertEqual(preview["data"]["status"], "relocation_preview")
            self.assertEqual(preview["data"]["schema_from"], 13)
            self.assertEqual(preview["data"]["schema_to"], 14)
            self.assertEqual(
                preview["data"]["planned_writes"],
                MOVED_V13_WRITES,
            )
            self.assertEqual(preview["data"]["completed_writes"], [])
            self.assertTrue(relocation["required"])
            self.assertEqual(
                relocation["source_layout"],
                "legacy_projects_v1",
            )
            self.assertEqual(relocation["identity_scheme"], "legacy_path_v1")
            self.assertEqual(relocation["binding_generation"], 1)
            self.assertIsInstance(token, str)
            self.assertNotEqual(token, "")
            self.assertIsInstance(relocation["expires_at"], str)
            self.assertEqual(
                tree_snapshot(moved.project_root),
                before_preview,
            )
            self.assertEqual(moved_legacy_db.read_bytes(), source_bytes)
            self.assertFalse(moved.db_path.exists())

            confirmed_process = moved.run(
                "setup",
                "--confirm-relocation",
                token,
                "--json",
            )

            self.assertEqual(
                confirmed_process.returncode,
                0,
                confirmed_process.stderr,
            )
            confirmed = json_payload(confirmed_process)
            self.assertEqual(confirmed["project_id"], project.project_id)
            self.assertEqual(
                confirmed["data"]["planned_writes"],
                MOVED_V13_WRITES,
            )
            self.assertEqual(
                confirmed["data"]["completed_writes"],
                MOVED_V13_WRITES,
            )
            self.assertTrue(moved.db_path.is_file())
            self.assertFalse(moved_legacy_db.exists())
            self.assertTrue(moved.viewer_path.is_file())

            expected_binding = project_identity(moved.project_root)
            with closing(sqlite3.connect(moved.db_path)) as connection:
                self.assertEqual(
                    preserved_business_projection(connection),
                    before_records,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    14,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM tasks"
                    ).fetchone()[0],
                    12,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events"
                    ).fetchone()[0],
                    191,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM tasks
                         WHERE completion_commit_hash != ''
                        """
                    ).fetchone()[0],
                    9,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipts"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_findings"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT project_id, identity_scheme,
                               binding_generation, canonical_path_hash,
                               binding_reason
                          FROM project_meta
                        """
                    ).fetchone(),
                    (
                        project.project_id,
                        "legacy_path_v1",
                        2,
                        expected_binding.canonical_path_hash,
                        "confirmed_relocation",
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT binding_generation, reason
                          FROM project_path_binding_history
                         ORDER BY binding_generation
                        """
                    ).fetchall(),
                    [
                        (1, "legacy_migration"),
                        (2, "confirmed_relocation"),
                    ],
                )

            before_replay = tree_snapshot(moved.project_root)
            replay_process = moved.run(
                "setup",
                "--confirm-relocation",
                token,
                "--json",
            )
            self.assertEqual(replay_process.returncode, 2)
            replay = json_payload(replay_process)
            self.assertEqual(
                replay["errors"],
                [{
                    "code": "relocation_token_used",
                    "message": "relocation confirmation has already been used",
                }],
            )
            self.assertEqual(replay["data"]["planned_writes"], [])
            self.assertEqual(replay["data"]["completed_writes"], [])
            self.assertEqual(
                tree_snapshot(moved.project_root),
                before_replay,
            )

            idempotent_process = moved.run("setup", "--json")
            self.assertEqual(
                idempotent_process.returncode,
                0,
                idempotent_process.stderr,
            )
            idempotent = json_payload(idempotent_process)
            self.assertEqual(idempotent["project_id"], project.project_id)
            self.assertEqual(idempotent["data"]["status"], "already_setup")
            self.assertEqual(idempotent["data"]["planned_writes"], [])
            self.assertEqual(idempotent["data"]["completed_writes"], [])
            self.assertEqual(
                tree_snapshot(moved.project_root),
                before_replay,
            )


if __name__ == "__main__":
    unittest.main()
