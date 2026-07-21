import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.selection import select_next_tasks  # noqa: E402
from task_governance_tool.storage import connect, initialize_database, resolve_database_target  # noqa: E402
from task_governance_tool.tasks import add_task, edit_task  # noqa: E402


SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"


def initialized_target(tmp: str):
    db = Path(tmp) / "taskgov.sqlite"
    repo = Path(tmp) / "repo"
    target = resolve_database_target(repo=repo, db=db, script_path=SCRIPT_PATH)
    initialize_database(target)
    return target


def add(connection, project, title, **kwargs):
    initial_status = kwargs.pop("status", "ready")
    task = add_task(
        connection,
        project,
        title=title,
        status=("ready" if initial_status == "done" else initial_status),
        **kwargs,
    ).task
    if initial_status == "done":
        task = edit_task(
            connection,
            project,
            task["task_id"],
            status="done",
            verification_complete=True,
            review_complete=True,
            commit_not_required=True,
        ).task
    return task


def task_row(project_id, **overrides):
    row = {
        "task_id": "tg_task_test",
        "project_id": project_id,
        "title": "Test task",
        "description": "",
        "kind": "optional",
        "lane": "",
        "lane_order": None,
        "priority": "normal",
        "status": "ready",
        "blocked_reason": "",
        "review_tier": 1,
        "verification": "",
        "tags": "",
        "created_at": "2026-07-06T00:00:00Z",
        "updated_at": "2026-07-06T00:00:00Z",
        "completed_at": None,
    }
    row.update(overrides)
    return row


def insert_task(connection, project_id, **overrides):
    row = task_row(project_id, **overrides)
    connection.execute(
        """
        INSERT INTO tasks(
          task_id,
          project_id,
          title,
          description,
          kind,
          lane,
          lane_order,
          priority,
          status,
          blocked_reason,
          review_tier,
          verification,
          tags,
          created_at,
          updated_at,
          completed_at
        )
        VALUES (
          :task_id,
          :project_id,
          :title,
          :description,
          :kind,
          :lane,
          :lane_order,
          :priority,
          :status,
          :blocked_reason,
          :review_tier,
          :verification,
          :tags,
          :created_at,
          :updated_at,
          :completed_at
        )
        """,
        row,
    )


def titles(result):
    return [task["title"] for task in result.tasks]


class SelectionTests(unittest.TestCase):
    def test_ready_optional_tasks_remain_selectable_when_one_sequential_lane_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    add(
                        connection,
                        target.project,
                        "Core blocker",
                        kind="sequential",
                        lane="CORE",
                        lane_order=10,
                        status="blocked",
                        blocked_reason="Waiting for decision",
                        priority="urgent",
                    )
                    add(
                        connection,
                        target.project,
                        "Core later",
                        kind="sequential",
                        lane="CORE",
                        lane_order=20,
                        priority="urgent",
                    )
                    add(connection, target.project, "Optional ready", priority="high")
                    add(
                        connection,
                        target.project,
                        "Docs ready",
                        kind="sequential",
                        lane="DOCS",
                        lane_order=10,
                    )

                result = select_next_tasks(connection, target.project, limit=10)

            self.assertEqual(titles(result), ["Optional ready", "Docs ready"])
            self.assertEqual(result.count, 2)
            self.assertEqual(result.limit, 10)
            self.assertEqual(result.selection_rules["status"], "ready")

    def test_later_sequential_tasks_are_hidden_until_earlier_tasks_are_done_or_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    first = add(
                        connection,
                        target.project,
                        "First sequential",
                        kind="sequential",
                        lane="CORE",
                        lane_order=10,
                    )
                    add(
                        connection,
                        target.project,
                        "Second sequential",
                        kind="sequential",
                        lane="CORE",
                        lane_order=20,
                    )
                    add(
                        connection,
                        target.project,
                        "Cancelled earlier",
                        kind="sequential",
                        lane="CANCELLED",
                        lane_order=10,
                        status="cancelled",
                    )
                    add(
                        connection,
                        target.project,
                        "After cancelled",
                        kind="sequential",
                        lane="CANCELLED",
                        lane_order=20,
                    )

                before_done = select_next_tasks(connection, target.project, limit=10)

                with connection:
                    edit_task(
                        connection,
                        target.project,
                        first["task_id"],
                        status="done",
                        verification_complete=True,
                        review_complete=True,
                        commit_not_required=True,
                    )

                after_done = select_next_tasks(connection, target.project, limit=10)

            self.assertEqual(titles(before_done), ["After cancelled", "First sequential"])
            self.assertEqual(titles(after_done), ["After cancelled", "Second sequential"])

    def test_selection_sorting_and_filters_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    add(connection, target.project, "Low optional", priority="low", lane="Z")
                    add(connection, target.project, "Urgent optional", priority="urgent", lane="M")
                    add(
                        connection,
                        target.project,
                        "Done before beta",
                        kind="sequential",
                        lane="BETA",
                        lane_order=10,
                        priority="high",
                        status="done",
                    )
                    add(
                        connection,
                        target.project,
                        "Beta second",
                        kind="sequential",
                        lane="BETA",
                        lane_order=20,
                        priority="high",
                    )
                    add(
                        connection,
                        target.project,
                        "Alpha first",
                        kind="sequential",
                        lane="ALPHA",
                        lane_order=10,
                        priority="high",
                    )
                    add(connection, target.project, "Normal optional", priority="normal", lane="A")

                all_ready = select_next_tasks(connection, target.project, limit=10)
                high_only = select_next_tasks(connection, target.project, priority="high", limit=10)
                sequential_only = select_next_tasks(connection, target.project, kind="sequential", limit=10)
                beta_only = select_next_tasks(connection, target.project, lane="BETA", limit=10)
                limited = select_next_tasks(connection, target.project, limit=2)

            self.assertEqual(
                titles(all_ready),
                [
                    "Urgent optional",
                    "Alpha first",
                    "Beta second",
                    "Normal optional",
                    "Low optional",
                ],
            )
            self.assertEqual(titles(high_only), ["Alpha first", "Beta second"])
            self.assertEqual(titles(sequential_only), ["Alpha first", "Beta second"])
            self.assertEqual(titles(beta_only), ["Beta second"])
            self.assertEqual(titles(limited), ["Urgent optional", "Alpha first"])
            self.assertEqual(limited.count, 2)
            self.assertEqual(limited.limit, 2)

    def test_selection_tie_breakers_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    insert_task(
                        connection,
                        target.project.project_id,
                        task_id="tg_task_null_order",
                        title="Null order",
                        lane="TIE",
                        lane_order=None,
                        priority="low",
                        created_at="2026-07-06T00:00:00Z",
                        updated_at="2026-07-06T00:00:00Z",
                    )
                    insert_task(
                        connection,
                        target.project.project_id,
                        task_id="tg_task_earlier",
                        title="Earlier created",
                        lane="TIE",
                        lane_order=1,
                        priority="low",
                        created_at="2026-07-06T00:00:01Z",
                        updated_at="2026-07-06T00:00:01Z",
                    )
                    insert_task(
                        connection,
                        target.project.project_id,
                        task_id="tg_task_beta",
                        title="Tie beta",
                        lane="TIE",
                        lane_order=1,
                        priority="low",
                        created_at="2026-07-06T00:00:02Z",
                        updated_at="2026-07-06T00:00:02Z",
                    )
                    insert_task(
                        connection,
                        target.project.project_id,
                        task_id="tg_task_alpha",
                        title="Tie alpha",
                        lane="TIE",
                        lane_order=1,
                        priority="low",
                        created_at="2026-07-06T00:00:02Z",
                        updated_at="2026-07-06T00:00:02Z",
                    )
                    insert_task(
                        connection,
                        target.project.project_id,
                        task_id="tg_task_later",
                        title="Later created",
                        lane="TIE",
                        lane_order=1,
                        priority="low",
                        created_at="2026-07-06T00:00:03Z",
                        updated_at="2026-07-06T00:00:03Z",
                    )

                result = select_next_tasks(connection, target.project, lane="TIE", priority="low", limit=10)

            self.assertEqual(
                titles(result),
                ["Earlier created", "Tie alpha", "Tie beta", "Later created", "Null order"],
            )


if __name__ == "__main__":
    unittest.main()
