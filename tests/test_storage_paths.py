import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPT = SKILL_ROOT / "scripts" / "taskgov.py"
SCRIPTS_PATH = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_PATH))
try:
    from task_governance_tool.storage import (
        UnboundDatabaseTarget,
        default_db_path,
        default_viewer_output_path,
        initialize_database,
        initialize_uuid_database,
        project_identity,
        resolve_database_target,
        sanitize_project_basename,
        skill_root_from_script,
        uses_unsupported_linked_install,
    )
    from task_governance_tool.state_resolver import observe_current_root
finally:
    sys.path.pop(0)

from tests.m14_test_support import make_physical_install


class StoragePathTests(unittest.TestCase):
    def test_skill_root_is_inferred_from_script_path(self):
        self.assertEqual(skill_root_from_script(SCRIPT), SKILL_ROOT.resolve())

    def test_entrypoint_symlink_install_branch_is_rejected(self):
        with (
            mock.patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path == SCRIPT.absolute(),
            ),
            mock.patch.object(Path, "is_junction", return_value=False),
        ):
            self.assertTrue(uses_unsupported_linked_install(SCRIPT))

    def test_project_id_sanitizes_name_and_hides_parent_path(self):
        with tempfile.TemporaryDirectory(prefix="Task Gov Parent ") as tmp:
            repo = Path(tmp) / "My Project_01"
            identity = project_identity(repo)

        self.assertRegex(identity.project_id, r"^my-project-01-[0-9a-f]{12}$")
        self.assertNotIn("Task Gov Parent", identity.project_id)
        self.assertNotIn(str(Path(tmp).parent), identity.project_id)

    def test_legacy_path_helpers_retain_the_project_keyed_transition_layout(self):
        database = default_db_path(SKILL_ROOT, "example-123456789abc")
        viewer = default_viewer_output_path(
            SKILL_ROOT,
            "example-123456789abc",
        )

        self.assertEqual(
            database,
            (
                SKILL_ROOT.resolve()
                / "state"
                / "projects"
                / "example-123456789abc"
                / "taskgov.sqlite"
            ),
        )
        self.assertEqual(
            viewer,
            (
                SKILL_ROOT.resolve()
                / "state"
                / "projects"
                / "example-123456789abc"
                / "viewer"
                / "task-viewer.html"
            ),
        )

    def test_physical_install_uses_fixed_state_and_explicit_legacy_fixture_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            expected_legacy_id = project_identity(
                install.project_root
            ).project_id

            self.assertEqual(
                install.db_path,
                install.skill_root.resolve()
                / "state"
                / "current"
                / "taskgov.sqlite",
            )
            self.assertEqual(
                install.viewer_path,
                install.skill_root.resolve()
                / "state"
                / "current"
                / "viewer"
                / "task-viewer.html",
            )
            self.assertEqual(install.project_id, expected_legacy_id)
            self.assertEqual(install.legacy_project_id, expected_legacy_id)
            self.assertEqual(
                install.legacy_root,
                install.skill_root.resolve()
                / "state"
                / "projects"
                / expected_legacy_id,
            )
            self.assertEqual(
                install.legacy_db_path,
                install.legacy_root / "taskgov.sqlite",
            )
            self.assertEqual(
                install.legacy_target.db_path,
                install.legacy_db_path,
            )
            self.assertEqual(install.target, install.legacy_target)
            initialize_database(install.target)
            self.assertTrue(install.legacy_db_path.is_file())
            self.assertFalse(install.db_path.exists())
            self.assertEqual(install.project_id, expected_legacy_id)

    def test_physical_install_reads_the_single_fixed_uuid_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            observed = observe_current_root(install.project_root)
            unbound = UnboundDatabaseTarget(
                canonical_repo=observed.canonical_repo,
                canonical_path_hash=observed.canonical_path_hash,
                display_name=observed.display_name,
                db_path=install.db_path,
            )
            raw_uuid = "00112233445546778899aabbccddeeff"
            initialize_uuid_database(
                unbound,
                project_id_factory=lambda: raw_uuid,
                clock=lambda: "2026-07-29T00:00:00Z",
            )

            expected_id = f"tg_project_{raw_uuid}"
            target = install.target
            self.assertEqual(install.project_id, expected_id)
            self.assertEqual(target.project.project_id, expected_id)
            self.assertEqual(
                target.project.canonical_repo,
                observed.canonical_repo,
            )
            self.assertEqual(
                target.project.canonical_path_hash,
                observed.canonical_path_hash,
            )
            self.assertEqual(target.project.display_name, observed.display_name)
            self.assertEqual(target.db_path, install.db_path)
            self.assertTrue(target.explicit_db)

    def test_explicit_db_path_remains_an_internal_repository_test_seam(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "custom" / "taskgov.sqlite"
            target = resolve_database_target(repo=repo, db=db, script_path=SCRIPT)

            self.assertTrue(target.explicit_db)
            self.assertEqual(target.db_path, db.resolve())
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())
            legacy_path = default_db_path(
                SKILL_ROOT,
                target.project.project_id,
            )
            self.assertFalse(legacy_path.exists())
            self.assertFalse(legacy_path.parent.exists())

    def test_sanitize_project_basename_has_stable_fallback(self):
        self.assertEqual(sanitize_project_basename("___"), "project")


if __name__ == "__main__":
    unittest.main()
