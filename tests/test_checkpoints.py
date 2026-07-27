import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.checkpoints import (  # noqa: E402
    PUBLIC_CHECKPOINT_EVENT_FIELDS,
    PUBLIC_CHECKPOINT_FIELDS,
    read_latest_checkpoint,
    record_checkpoint,
)
from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    connect_initialized,
    initialize_database,
    project_identity,
)
from task_governance_tool.tasks import (  # noqa: E402
    TaskRepositoryError,
    TaskValidationError,
    add_task,
)


def checkpoint_connection(
    project,
    *,
    task_id="tg_task_checkpoint",
    status="in_progress",
    contract_revision=1,
):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE project_meta (
          project_id TEXT PRIMARY KEY
        );
        CREATE TABLE tasks (
          task_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          status TEXT NOT NULL,
          current_contract_revision INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
        );
        CREATE TABLE task_contract_revisions (
          contract_revision_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          project_id TEXT NOT NULL,
          revision INTEGER NOT NULL,
          scope TEXT NOT NULL,
          acceptance TEXT NOT NULL,
          constraints_text TEXT NOT NULL,
          authority_ref TEXT NOT NULL,
          change_reason TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (task_id) REFERENCES tasks(task_id),
          FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
        );
        CREATE TABLE task_events (
          task_event_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          project_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          summary TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (task_id) REFERENCES tasks(task_id)
        );
        CREATE TABLE task_checkpoints (
          checkpoint_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          project_id TEXT NOT NULL,
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          summary TEXT NOT NULL,
          next_action TEXT NOT NULL,
          unresolved_risks_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          FOREIGN KEY (task_id) REFERENCES tasks(task_id),
          FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
        );
        CREATE INDEX idx_checkpoints_project_task_created
          ON task_checkpoints(project_id, task_id, created_at, checkpoint_id);
        """
    )
    connection.execute(
        "INSERT INTO project_meta(project_id) VALUES (?)",
        (project.project_id,),
    )
    connection.execute(
        """
        INSERT INTO tasks(
          task_id, project_id, status, current_contract_revision, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            task_id,
            project.project_id,
            status,
            contract_revision,
            "2026-07-27T00:00:00.000000Z",
        ),
    )
    if contract_revision:
        connection.execute(
            """
            INSERT INTO task_contract_revisions(
              contract_revision_id, task_id, project_id, revision,
              scope, acceptance, constraints_text, authority_ref,
              change_reason, created_at
            ) VALUES (?, ?, ?, ?, 'Checkpoint scope', 'Checkpoint accepted',
                      '', '', '', ?)
            """,
            (
                f"tg_contract_{contract_revision}",
                task_id,
                project.project_id,
                contract_revision,
                "2026-07-27T00:00:00.000000Z",
            ),
        )
    connection.commit()
    return connection


class CheckpointRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.project = project_identity(Path(self.tempdir.name) / "repo")

    def test_record_is_atomic_content_free_and_does_not_touch_task_timestamp(self):
        connection = checkpoint_connection(self.project)
        self.addCleanup(connection.close)
        before = connection.execute(
            "SELECT updated_at FROM tasks WHERE task_id = 'tg_task_checkpoint'"
        ).fetchone()["updated_at"]

        result = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            summary="Parser and storage are implemented.",
            next_action="Wire the CLI projection.",
            unresolved_risks=["Migration retry still needs verification."],
        )
        connection.commit()

        self.assertTrue(result.created)
        self.assertFalse(result.replayed)
        self.assertEqual(set(result.checkpoint), set(PUBLIC_CHECKPOINT_FIELDS))
        self.assertEqual(result.checkpoint["contract_revision"], 1)
        self.assertEqual(
            result.checkpoint["unresolved_risks"],
            ["Migration retry still needs verification."],
        )
        self.assertIsNotNone(result.event)
        self.assertEqual(set(result.event), set(PUBLIC_CHECKPOINT_EVENT_FIELDS))
        self.assertEqual(result.event["event_type"], "checkpoint_recorded")
        stored_event = connection.execute(
            "SELECT event_type, summary FROM task_events"
        ).fetchone()
        self.assertEqual(
            dict(stored_event),
            {
                "event_type": "checkpoint_recorded",
                "summary": "Checkpoint recorded",
            },
        )
        self.assertNotIn("Parser", stored_event["summary"])
        self.assertEqual(
            connection.execute(
                "SELECT updated_at FROM tasks WHERE task_id = 'tg_task_checkpoint'"
            ).fetchone()["updated_at"],
            before,
        )

    def test_exact_replay_is_scoped_to_latest_same_contract_revision(self):
        connection = checkpoint_connection(self.project)
        self.addCleanup(connection.close)
        first = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            summary="Current state",
            next_action="Continue work",
            unresolved_risks=["One risk"],
        )
        connection.commit()

        replay = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            summary="Current state",
            next_action="Continue work",
            unresolved_risks=["One risk"],
        )
        connection.commit()
        self.assertFalse(replay.created)
        self.assertTrue(replay.replayed)
        self.assertIsNone(replay.event)
        self.assertEqual(replay.checkpoint, first.checkpoint)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM task_checkpoints").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
            1,
        )

        connection.execute(
            """
            INSERT INTO task_contract_revisions(
              contract_revision_id, task_id, project_id, revision,
              scope, acceptance, constraints_text, authority_ref,
              change_reason, created_at
            ) VALUES ('tg_contract_2', 'tg_task_checkpoint', ?, 2,
                      'Checkpoint scope', 'Checkpoint accepted', '',
                      'user_instruction:tg_task_checkpoint:2',
                      'Scope updated',
                      '2026-07-27T01:00:00.000000Z')
            """,
            (self.project.project_id,),
        )
        connection.execute(
            """
            UPDATE tasks
               SET current_contract_revision = 2
             WHERE task_id = 'tg_task_checkpoint'
            """
        )
        connection.commit()
        after_contract_change = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            summary="Current state",
            next_action="Continue work",
            unresolved_risks=["One risk"],
        )
        connection.commit()
        self.assertTrue(after_contract_change.created)
        self.assertEqual(after_contract_change.checkpoint["contract_revision"], 2)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM task_checkpoints").fetchone()[0],
            2,
        )

    def test_exact_replay_preserves_and_distinguishes_line_endings(self):
        connection = checkpoint_connection(self.project)
        self.addCleanup(connection.close)
        content = {
            "summary": "First line\r\nSecond line",
            "next_action": "Continue\rwhen ready",
            "unresolved_risks": ["Risk one\r\nRisk two"],
        }

        first = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            **content,
        )
        connection.commit()
        replay = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            **content,
        )
        connection.commit()
        lf_variant = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            summary=content["summary"].replace("\r\n", "\n"),
            next_action=content["next_action"].replace("\r", "\n"),
            unresolved_risks=[
                content["unresolved_risks"][0].replace("\r\n", "\n")
            ],
        )
        connection.commit()

        self.assertEqual(first.checkpoint["summary"], content["summary"])
        self.assertEqual(first.checkpoint["next_action"], content["next_action"])
        self.assertEqual(
            first.checkpoint["unresolved_risks"],
            content["unresolved_risks"],
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.checkpoint, first.checkpoint)
        self.assertTrue(lf_variant.created)
        self.assertNotEqual(lf_variant.checkpoint, first.checkpoint)

    def test_latest_projection_uses_created_time_then_rowid(self):
        connection = checkpoint_connection(self.project)
        self.addCleanup(connection.close)
        with mock.patch(
            "task_governance_tool.checkpoints.utc_now",
            return_value="2026-07-27T02:00:00Z",
        ):
            first = record_checkpoint(
                connection,
                self.project,
                "tg_task_checkpoint",
                summary="First",
                next_action="Continue",
            )
            second = record_checkpoint(
                connection,
                self.project,
                "tg_task_checkpoint",
                summary="Second",
                next_action="Continue",
            )
        connection.commit()

        latest = read_latest_checkpoint(
            connection,
            project_id=self.project.project_id,
            task_id="tg_task_checkpoint",
        )
        self.assertEqual(latest, second.checkpoint)
        self.assertNotEqual(latest, first.checkpoint)

    def test_validates_privacy_and_utf8_bounds_before_write_transaction(self):
        connection = checkpoint_connection(self.project)
        self.addCleanup(connection.close)
        cases = (
            {
                "summary": "a" * 1_025,
                "next_action": "continue",
                "unresolved_risks": None,
                "field": "summary",
            },
            {
                "summary": "safe",
                "next_action": "あ" * 342,
                "unresolved_risks": None,
                "field": "next_action",
            },
            {
                "summary": "safe",
                "next_action": "continue",
                "unresolved_risks": ["r" * 513],
                "field": "unresolved_risk",
            },
            {
                "summary": "safe",
                "next_action": "continue",
                "unresolved_risks": [str(index) for index in range(9)],
                "field": "unresolved_risks",
            },
            {
                "summary": "Authorization: Bearer secret-value",
                "next_action": "continue",
                "unresolved_risks": None,
                "field": "summary",
            },
            {
                "summary": "raw stdout: build completed",
                "next_action": "continue",
                "unresolved_risks": None,
                "field": "summary",
            },
            {
                "summary": "safe",
                "next_action": "command output: all tests passed",
                "unresolved_risks": None,
                "field": "next_action",
            },
            {
                "summary": "safe",
                "next_action": "continue",
                "unresolved_risks": ["raw stderr: retry later"],
                "field": "unresolved_risk",
            },
        )
        for case in cases:
            with self.subTest(field=case["field"]):
                with self.assertRaises(TaskValidationError) as raised:
                    record_checkpoint(
                        connection,
                        self.project,
                        "tg_task_checkpoint",
                        summary=case["summary"],
                        next_action=case["next_action"],
                        unresolved_risks=case["unresolved_risks"],
                    )
                self.assertEqual(raised.exception.field, case["field"])
                self.assertFalse(connection.in_transaction)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM task_checkpoints").fetchone()[0],
            0,
        )

        boundary = record_checkpoint(
            connection,
            self.project,
            "tg_task_checkpoint",
            summary="s" * 1_024,
            next_action="n" * 1_024,
            unresolved_risks=["r" * 512 for _ in range(8)],
        )
        connection.commit()
        self.assertTrue(boundary.created)

    def test_schema_v12_accepts_the_complete_valid_content_boundary(self):
        db_path = Path(self.tempdir.name) / "taskgov.sqlite"
        target = DatabaseTarget(
            project=self.project,
            db_path=db_path,
            explicit_db=True,
        )
        initialize_database(target)
        connection = connect_initialized(target)
        self.addCleanup(connection.close)
        task = add_task(connection, self.project, title="Boundary task").task
        connection.commit()

        result = record_checkpoint(
            connection,
            self.project,
            task["task_id"],
            summary="s" * 1_024,
            next_action="n" * 1_024,
            unresolved_risks=["\x00" * 512 for _ in range(8)],
            database_target=target,
        )
        connection.commit()
        self.assertTrue(result.created)
        self.assertEqual(
            len(result.checkpoint["summary"].encode("utf-8"))
            + len(result.checkpoint["next_action"].encode("utf-8"))
            + sum(
                len(risk.encode("utf-8"))
                for risk in result.checkpoint["unresolved_risks"]
            ),
            6_144,
        )
        self.assertEqual(
            connection.execute(
                """
                SELECT length(CAST(unresolved_risks_json AS BLOB))
                  FROM task_checkpoints
                 WHERE checkpoint_id = ?
                """,
                (result.checkpoint["checkpoint_id"],),
            ).fetchone()[0],
            24_601,
        )

    def test_done_and_foreign_tasks_are_rejected_without_checkpoint(self):
        connection = checkpoint_connection(self.project, status="done")
        self.addCleanup(connection.close)
        with self.assertRaises(TaskRepositoryError) as done_error:
            record_checkpoint(
                connection,
                self.project,
                "tg_task_checkpoint",
                summary="Done",
                next_action="None",
            )
        self.assertEqual(done_error.exception.code, "done_task_requires_reopen")
        connection.rollback()

        foreign = project_identity(Path(self.tempdir.name) / "foreign")
        with self.assertRaises(TaskRepositoryError) as foreign_error:
            record_checkpoint(
                connection,
                foreign,
                "tg_task_checkpoint",
                summary="State",
                next_action="Continue",
            )
        self.assertEqual(foreign_error.exception.code, "not_found")
        connection.rollback()
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM task_checkpoints").fetchone()[0],
            0,
        )

    def test_event_failure_rolls_back_checkpoint_with_caller_transaction(self):
        connection = checkpoint_connection(self.project)
        self.addCleanup(connection.close)
        connection.execute(
            """
            CREATE TRIGGER reject_checkpoint_event
            BEFORE INSERT ON task_events
            WHEN NEW.event_type = 'checkpoint_recorded'
            BEGIN
              SELECT RAISE(ABORT, 'event rejected');
            END
            """
        )
        connection.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            record_checkpoint(
                connection,
                self.project,
                "tg_task_checkpoint",
                summary="State",
                next_action="Continue",
            )
        connection.rollback()
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM task_checkpoints").fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
