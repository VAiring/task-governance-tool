import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import connect, initialize_database, resolve_database_target  # noqa: E402
from task_governance_tool.tasks import (  # noqa: E402
    TaskValidationError,
    add_task,
    find_task_ids_by_completion_commit_hash,
)


SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"


def initialized_target(tmp: str):
    db = Path(tmp) / "taskgov.sqlite"
    repo = Path(tmp) / "repo"
    target = resolve_database_target(repo=repo, db=db, script_path=SCRIPT_PATH)
    initialize_database(target)
    return target


class CompletionCommitSchemaTests(unittest.TestCase):
    def test_added_task_gets_completion_commit_defaults_without_public_json_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    task = add_task(connection, target.project, title="Completion schema task").task
                raw = connection.execute(
                    """
                    SELECT completion_commit_required, completion_commit_hash
                      FROM tasks
                     WHERE task_id = ?
                    """,
                    (task["task_id"],),
                ).fetchone()

            self.assertNotIn("completion_commit_required", task)
            self.assertNotIn("completion_commit_hash", task)
            self.assertEqual(raw["completion_commit_required"], 1)
            self.assertEqual(raw["completion_commit_hash"], "")

    def test_find_task_ids_by_non_empty_completion_commit_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    first = add_task(connection, target.project, title="First committed task").task
                    second = add_task(connection, target.project, title="Second committed task").task
                    add_task(connection, target.project, title="Uncommitted task")
                    connection.execute(
                        """
                        UPDATE tasks
                           SET completion_commit_hash = ?
                         WHERE task_id IN (?, ?)
                        """,
                        ("abc123def456", first["task_id"], second["task_id"]),
                    )

                matches = find_task_ids_by_completion_commit_hash(
                    connection,
                    target.project,
                    "abc123def456",
                )
                no_matches = find_task_ids_by_completion_commit_hash(
                    connection,
                    target.project,
                    "missinghash",
                )

            self.assertEqual(matches, sorted([first["task_id"], second["task_id"]]))
            self.assertEqual(no_matches, [])

    def test_completion_commit_hash_lookup_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    visible = add_task(connection, target.project, title="Visible task").task
                    hidden = add_task(connection, target.project, title="Other project task").task
                    connection.execute(
                        """
                        UPDATE tasks
                           SET completion_commit_hash = ?
                         WHERE task_id IN (?, ?)
                        """,
                        ("samehash123", visible["task_id"], hidden["task_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE tasks
                           SET project_id = ?
                         WHERE task_id = ?
                        """,
                        ("other-project-123456789abc", hidden["task_id"]),
                    )

                matches = find_task_ids_by_completion_commit_hash(
                    connection,
                    target.project,
                    "samehash123",
                )

            self.assertEqual(matches, [visible["task_id"]])

    def test_completion_commit_hash_lookup_rejects_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with self.assertRaises(TaskValidationError) as empty:
                    find_task_ids_by_completion_commit_hash(connection, target.project, "")
                with self.assertRaises(TaskValidationError) as private:
                    find_task_ids_by_completion_commit_hash(
                        connection,
                        target.project,
                        "Bearer secret",
                    )

            self.assertEqual(empty.exception.code, "invalid_argument")
            self.assertEqual(private.exception.code, "privacy_rejected")


if __name__ == "__main__":
    unittest.main()
