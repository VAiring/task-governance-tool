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
        default_db_path,
        project_identity,
        resolve_database_target,
        sanitize_project_basename,
        skill_root_from_script,
        uses_unsupported_linked_install,
    )
finally:
    sys.path.pop(0)


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

    def test_separate_repo_roots_produce_separate_default_db_paths(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = resolve_database_target(repo=one, db=None, script_path=SCRIPT)
            second = resolve_database_target(repo=two, db=None, script_path=SCRIPT)

        self.assertNotEqual(first.project.project_id, second.project.project_id)
        self.assertNotEqual(first.db_path, second.db_path)
        self.assertFalse(first.explicit_db)
        self.assertTrue(str(first.db_path).endswith("taskgov.sqlite"))

    def test_default_db_path_stays_under_skill_state(self):
        path = default_db_path(SKILL_ROOT, "example-123456789abc")

        self.assertEqual(
            path,
            SKILL_ROOT.resolve() / "state" / "projects" / "example-123456789abc" / "taskgov.sqlite",
        )

    def test_explicit_db_override_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            db = Path(tmp) / "custom" / "taskgov.sqlite"
            target = resolve_database_target(repo=repo, db=db, script_path=SCRIPT)

            self.assertTrue(target.explicit_db)
            self.assertEqual(target.db_path, db.resolve())
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())
            default_path = default_db_path(SKILL_ROOT, target.project.project_id)
            self.assertFalse(default_path.exists())
            self.assertFalse(default_path.parent.exists())

    def test_sanitize_project_basename_has_stable_fallback(self):
        self.assertEqual(sanitize_project_basename("___"), "project")


if __name__ == "__main__":
    unittest.main()
