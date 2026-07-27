import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import cli as cli_service  # noqa: E402
from task_governance_tool.checkpoints import (  # noqa: E402
    PUBLIC_CHECKPOINT_EVENT_FIELDS,
    PUBLIC_CHECKPOINT_FIELDS,
)
from task_governance_tool.compact import COMPACT_CURRENT_TASK_FIELDS  # noqa: E402
from task_governance_tool.maintenance import MutationOutcome  # noqa: E402


CHECKPOINT_DATA_FIELDS = {
    "checkpoint",
    "created",
    "replayed",
    "event",
}


def run_taskgov(*args, maintenance_enabled=True):
    return run_taskgov_internal(
        *args,
        maintenance_enabled=maintenance_enabled,
    )


def init_db(db, repo):
    return initialize_taskgov_internal(repo=repo, db=db)


def add_active_task(db, repo, title="Checkpoint CLI task"):
    result = run_taskgov(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        "--status",
        "in_progress",
        "--json",
        maintenance_enabled=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["data"]["task"]


def checkpoint_args(db, repo, task_id):
    return (
        "task",
        "checkpoint",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--summary",
        "Parser and repository are connected.",
        "--next-action",
        "Run the focused acceptance tests.",
        "--unresolved-risk",
        "Manifest refresh remains outside this test slice.",
    )


class CheckpointCliTests(unittest.TestCase):
    def test_checkpoint_requires_summary_and_next_action_before_state_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            cases = (
                (
                    "--next-action",
                    "Continue",
                ),
                (
                    "--summary",
                    "Current state",
                ),
            )
            for supplied in cases:
                with self.subTest(supplied=supplied):
                    result = run_taskgov(
                        "task",
                        "checkpoint",
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        "tg_task_missing",
                        *supplied,
                        "--json",
                    )

                    self.assertEqual(result.returncode, 1)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["command"], "parse")
                    self.assertEqual(
                        payload["errors"],
                        [
                            {
                                "code": "invalid_argument",
                                "message": "arguments are invalid",
                            }
                        ],
                    )
                    self.assertFalse(db.parent.exists())
                    self.assertFalse(repo.exists())

    def test_checkpoint_read_only_rejects_with_fixed_empty_shape_and_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_active_task(db, repo)
            before = db.read_bytes()

            result = run_taskgov(
                *checkpoint_args(db, repo, task["task_id"]),
                "--read-only",
                "--json",
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["command"], "task.checkpoint")
            self.assertEqual(
                payload["data"],
                {
                    "checkpoint": None,
                    "created": False,
                    "replayed": False,
                    "event": None,
                },
            )
            self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
            self.assertEqual(db.read_bytes(), before)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_checkpoints"
                    ).fetchone()[0],
                    0,
                )

    def test_checkpoint_create_and_exact_replay_have_exact_outputs_and_maintenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_active_task(db, repo)
            args = checkpoint_args(db, repo, task["task_id"])

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as create_maintenance:
                created_result = run_taskgov(*args, "--json")

            self.assertEqual(created_result.returncode, 0, created_result.stderr)
            created_payload = json.loads(created_result.stdout)
            self.assertTrue(created_payload["ok"])
            self.assertEqual(created_payload["command"], "task.checkpoint")
            self.assertEqual(set(created_payload["data"]), CHECKPOINT_DATA_FIELDS)
            created = created_payload["data"]
            self.assertTrue(created["created"])
            self.assertFalse(created["replayed"])
            self.assertEqual(
                set(created["checkpoint"]),
                set(PUBLIC_CHECKPOINT_FIELDS),
            )
            self.assertEqual(
                set(created["event"]),
                set(PUBLIC_CHECKPOINT_EVENT_FIELDS),
            )
            self.assertEqual(created["event"]["event_type"], "checkpoint_recorded")
            self.assertEqual(created["checkpoint"]["task_id"], task["task_id"])
            self.assertEqual(
                created["checkpoint"]["unresolved_risks"],
                ["Manifest refresh remains outside this test slice."],
            )
            create_maintenance.assert_called_once()
            self.assertEqual(
                create_maintenance.call_args.args[1],
                MutationOutcome(state_changed=True, viewer_relevant=True),
            )

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
            ) as replay_maintenance:
                replay_result = run_taskgov(*args, "--json")

            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            replay = json.loads(replay_result.stdout)["data"]
            self.assertEqual(set(replay), CHECKPOINT_DATA_FIELDS)
            self.assertFalse(replay["created"])
            self.assertTrue(replay["replayed"])
            self.assertIsNone(replay["event"])
            self.assertEqual(replay["checkpoint"], created["checkpoint"])
            replay_maintenance.assert_not_called()

            text_result = run_taskgov(
                *args,
                maintenance_enabled=False,
            )
            self.assertEqual(text_result.returncode, 0, text_result.stderr)
            self.assertEqual(
                text_result.stdout,
                (
                    f"Checkpoint {created['checkpoint']['checkpoint_id']}: "
                    f"replayed for task {task['task_id']}\n"
                ),
            )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_checkpoints"
                    ).fetchone()[0],
                    1,
                )
                stored_event = connection.execute(
                    """
                    SELECT event_type, summary
                      FROM task_events
                     WHERE event_type = 'checkpoint_recorded'
                    """
                ).fetchone()
                self.assertEqual(
                    stored_event,
                    ("checkpoint_recorded", "Checkpoint recorded"),
                )

    def test_default_show_and_current_expose_latest_while_compact_omits_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_active_task(db, repo)
            checkpoint_result = run_taskgov(
                *checkpoint_args(db, repo, task["task_id"]),
                "--json",
                maintenance_enabled=False,
            )
            checkpoint = json.loads(checkpoint_result.stdout)["data"]["checkpoint"]

            show_result = run_taskgov(
                "task",
                "show",
                "--repo",
                str(repo),
                "--db",
                str(db),
                task["task_id"],
                "--json",
            )
            current_result = run_taskgov(
                "task",
                "current",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--json",
            )
            compact_result = run_taskgov(
                "task",
                "current",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--compact",
                "--json",
            )

            self.assertEqual(show_result.returncode, 0, show_result.stderr)
            self.assertEqual(current_result.returncode, 0, current_result.stderr)
            self.assertEqual(compact_result.returncode, 0, compact_result.stderr)
            self.assertEqual(
                json.loads(show_result.stdout)["data"]["latest_checkpoint"],
                checkpoint,
            )
            current_task = json.loads(current_result.stdout)["data"]["tasks"][0]
            self.assertEqual(current_task["latest_checkpoint"], checkpoint)
            compact_task = json.loads(compact_result.stdout)["data"]["tasks"][0]
            self.assertEqual(set(compact_task), set(COMPACT_CURRENT_TASK_FIELDS))
            self.assertNotIn("latest_checkpoint", compact_task)
            for private_checkpoint_content in (
                checkpoint["summary"],
                checkpoint["next_action"],
                *checkpoint["unresolved_risks"],
            ):
                self.assertNotIn(
                    private_checkpoint_content,
                    compact_result.stdout,
                )

    def test_current_fails_closed_on_invalid_stored_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            repo = Path(tmp) / "repo"
            init_db(db, repo)
            task = add_active_task(db, repo)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    """
                    INSERT INTO task_checkpoints(
                      checkpoint_id, task_id, project_id, contract_revision,
                      summary, next_action, unresolved_risks_json, created_at
                    ) VALUES (
                      'tg_checkpoint_invalid', ?, ?, 0,
                      'Safe summary', 'Safe next action', 'not-json',
                      '2026-07-27T00:00:00Z'
                    )
                    """,
                    (task["task_id"], task["project_id"]),
                )
                connection.commit()

            result = run_taskgov(
                "task",
                "current",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "internal_error")
            self.assertEqual(
                payload["errors"][0]["message"],
                "stored checkpoint unresolved risks are invalid",
            )
            self.assertNotIn("not-json", result.stdout)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
