from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import (
    create_v14_target,
    json_payload,
    make_physical_install,
    run_taskgov_internal,
)
from tests.m214c_test_support import (
    FIXED_CODE,
    FIXED_MESSAGE,
    PRIVATE_SENTINEL,
    assert_fixed_cli_failure,
    inject_task_fault,
    seed_current_task,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import viewer_maintenance  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    StorageError,
    apply_completion_cycle_capture_activation_migration,
    apply_completion_cycle_history_migration,
    connect,
    configure_project_maintenance,
    connect_initialized_readonly,
    current_schema_version,
    read_viewer_maintenance,
    resolve_database_target,
)
from task_governance_tool.tasks import add_task  # noqa: E402
from task_governance_tool.viewer import (  # noqa: E402
    build_viewer_snapshot,
    resolve_canonical_viewer_output_target,
)


SCRIPT_PATH = SKILL_ROOT / "scripts" / "taskgov.py"


def run_json(repo: Path, db: Path, *args: str):
    return run_taskgov_internal(
        *args,
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--json",
    )


def add_another_task(
    repo: Path,
    db: Path,
    *,
    title: str,
    status: str,
    priority: str,
) -> dict:
    result = run_json(
        repo,
        db,
        "task",
        "add",
        "--title",
        title,
        "--status",
        status,
        "--priority",
        priority,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json.loads(result.stdout)["data"]["task"]


class StoredTaskConsumerBoundaryTests(unittest.TestCase):
    def test_full_and_compact_projections_reject_before_omission_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(
                Path(temp),
                status="ready",
            )
            inject_task_fault(
                db,
                task["task_id"],
                assignments={"description": PRIVATE_SENTINEL},
            )
            before = db.read_bytes()
            commands = (
                ("task", "list"),
                ("task", "next"),
                ("task", "next", "--compact"),
                ("task", "show", task["task_id"]),
            )
            for command in commands:
                with self.subTest(command=command):
                    result = run_json(repo, db, *command)
                    assert_fixed_cli_failure(self, result)
                    self.assertEqual(db.read_bytes(), before)
            text_result = run_taskgov_internal(
                "task",
                "show",
                task["task_id"],
                "--repo",
                str(repo),
                "--db",
                str(db),
            )
            self.assertEqual(text_result.returncode, 2)
            self.assertIn(FIXED_MESSAGE, text_result.stdout + text_result.stderr)
            self.assertNotIn(
                PRIVATE_SENTINEL,
                text_result.stdout + text_result.stderr,
            )
            self.assertEqual(db.read_bytes(), before)

        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(
                Path(temp),
                status="in_progress",
            )
            inject_task_fault(
                db,
                task["task_id"],
                assignments={"description": PRIVATE_SENTINEL},
            )
            before = db.read_bytes()
            for command in (
                ("task", "current"),
                ("task", "current", "--compact"),
            ):
                with self.subTest(command=command):
                    result = run_json(repo, db, *command)
                    assert_fixed_cli_failure(self, result)
                    self.assertEqual(db.read_bytes(), before)

    def test_selected_batch_validation_does_not_scan_nonreturned_rows(self):
        for command in ("list", "next", "current"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temp:
                status = "in_progress" if command == "current" else "ready"
                repo, db, first = seed_current_task(
                    Path(temp),
                    status=status,
                    title="First selected Task",
                    priority="urgent",
                )
                second = add_another_task(
                    repo,
                    db,
                    title="Unselected invalid Task",
                    status=status,
                    priority="low",
                )
                inject_task_fault(
                    db,
                    second["task_id"],
                    assignments={"description": PRIVATE_SENTINEL},
                )
                variants = (("task", command, "--limit", "1"),)
                if command in {"next", "current"}:
                    variants += (("task", command, "--limit", "1", "--compact"),)
                for args in variants:
                    result = run_json(repo, db, *args)
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertNotIn(PRIVATE_SENTINEL, result.stdout + result.stderr)
                    data = json.loads(result.stdout)["data"]
                    tasks = data.get("tasks") or data.get("items") or []
                    if tasks:
                        self.assertEqual(tasks[0]["task_id"], first["task_id"])

    def test_named_lifecycle_loaders_abort_before_any_business_write(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(
                Path(temp),
                status="in_progress",
            )
            inject_task_fault(
                db,
                task["task_id"],
                assignments={"description": PRIVATE_SENTINEL},
            )
            before = db.read_bytes()
            commands = (
                (
                    "task",
                    "checkpoint",
                    task["task_id"],
                    "--summary",
                    "Current state",
                    "--next-action",
                    "Continue focused verification",
                ),
                (
                    "handoff",
                    "record",
                    task["task_id"],
                    "--summary",
                    "Defer one bounded follow-up",
                ),
                ("task", "effort", task["task_id"], "--read-only"),
                (
                    "task",
                    "edit",
                    task["task_id"],
                    "--title",
                    "Updated title",
                ),
                ("task", "complete", task["task_id"], "--check"),
                ("review", "prepare", task["task_id"]),
                (
                    "review",
                    "target",
                    "set",
                    task["task_id"],
                    "--kind",
                    "diff_fingerprint",
                    "--revision",
                    "sha256:" + ("a" * 64),
                ),
            )
            for command in commands:
                with self.subTest(command=command):
                    result = run_json(repo, db, *command)
                    assert_fixed_cli_failure(self, result)
                    self.assertEqual(db.read_bytes(), before)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_checkpoints"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM handoff_records"
                    ).fetchone()[0],
                    0,
                )

    def test_invalid_utf8_text_fetch_uses_the_fixed_storage_error(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(Path(temp), status="ready")
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "UPDATE tasks SET description = CAST(X'80' AS TEXT) "
                    "WHERE task_id = ?",
                    (task["task_id"],),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT typeof(description) FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    "text",
                )
                connection.commit()
            before = db.read_bytes()

            result = run_json(repo, db, "task", "list")

            assert_fixed_cli_failure(self, result)
            self.assertEqual(db.read_bytes(), before)

    def test_viewer_rejects_before_publication_and_preserves_last_good_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db, task = seed_current_task(root, status="ready")
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SCRIPT_PATH,
            )
            configure_project_maintenance(
                target,
                requested_interval_minutes=None,
                requested_generations=None,
                enabled_at="2026-08-03T00:00:00Z",
            )
            initial = viewer_maintenance.publish_setup_viewer(
                target,
                observed_at="2026-08-03T00:00:01Z",
            )
            self.assertEqual(initial.code, "succeeded")
            output = resolve_canonical_viewer_output_target(target)
            last_good = output.path.read_bytes()
            inject_task_fault(
                db,
                task["task_id"],
                assignments={"description": PRIVATE_SENTINEL},
            )

            with closing(connect_initialized_readonly(target)) as connection:
                with self.assertRaises(StorageError) as caught:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(caught.exception.code, FIXED_CODE)

            failed = viewer_maintenance.publish_setup_viewer(
                target,
                observed_at="2026-08-03T00:00:02Z",
            )
            self.assertEqual((failed.code, failed.renders), ("failed", 0))
            self.assertEqual(output.path.read_bytes(), last_good)
            self.assertEqual(
                list(output.path.parent.glob(".task-viewer-*.tmp")),
                [],
            )
            with closing(connect_initialized_readonly(target)) as connection:
                state = read_viewer_maintenance(
                    connection,
                    target.project.project_id,
                )
            self.assertIsNotNone(state)
            self.assertEqual(
                state.rendered_generation,
                state.source_generation,
            )

    def test_viewer_validates_foreign_owned_rows_in_the_whole_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, db, _ = seed_current_task(root, status="ready")
            foreign = add_another_task(
                repo,
                db,
                title="Foreign-owned corrupt Task",
                status="ready",
                priority="low",
            )
            inject_task_fault(
                db,
                foreign["task_id"],
                assignments={"project_id": "foreign-project"},
            )
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SCRIPT_PATH,
            )

            with closing(connect_initialized_readonly(target)) as connection:
                with self.assertRaises(StorageError) as caught:
                    build_viewer_snapshot(connection, target)

            self.assertEqual(caught.exception.code, FIXED_CODE)
            self.assertEqual(caught.exception.message, FIXED_MESSAGE)

    def test_doctor_maps_invalid_task_batch_to_unreadable_components(self):
        with tempfile.TemporaryDirectory() as temp:
            install = make_physical_install(Path(temp))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
            added = install.run(
                "task",
                "add",
                "--title",
                "Doctor stored Task boundary",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
            task_id = json_payload(added)["data"]["task"]["task_id"]
            inject_task_fault(
                install.db_path,
                task_id,
                assignments={"project_id": "foreign-project"},
            )

            result = install.run("doctor", "--json")

            self.assertEqual(result.returncode, 2, result.stdout)
            payload = json_payload(result)
            self.assertEqual(
                payload["errors"],
                [{"code": FIXED_CODE, "message": FIXED_MESSAGE}],
            )
            self.assertEqual(payload["warnings"], [])
            components = payload["data"]["components"]
            self.assertEqual(components["project_state"]["code"], "unreadable")
            for name in ("task_summary", "handoff_delivery", "maintenance"):
                self.assertEqual(components[name], {"code": "unavailable"})
            self.assertFalse(payload["data"]["setup_eligible"])
            self.assertNotIn("foreign-project", result.stdout + result.stderr)

    def test_setup_preflight_rejects_invalid_task_before_configuration_write(self):
        with tempfile.TemporaryDirectory() as temp:
            install = make_physical_install(Path(temp))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
            added = install.run(
                "task",
                "add",
                "--title",
                "Setup stored Task boundary",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
            task_id = json_payload(added)["data"]["task"]["task_id"]
            inject_task_fault(
                install.db_path,
                task_id,
                assignments={"description": PRIVATE_SENTINEL},
            )
            before = install.db_path.read_bytes()

            result = install.run(
                "setup",
                "--backup-generations",
                "4",
                "--json",
            )

            payload = assert_fixed_cli_failure(self, result)
            self.assertEqual(payload["data"]["completed_writes"], [])
            self.assertEqual(install.db_path.read_bytes(), before)
            with closing(sqlite3.connect(install.db_path)) as connection:
                generations = connection.execute(
                    "SELECT backup_generations FROM project_maintenance"
                ).fetchone()[0]
            self.assertEqual(generations, 3)

    def test_schema_v16_setup_rejects_invalid_task_before_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            install = make_physical_install(Path(temp))
            install.db_path.parent.mkdir(parents=True, exist_ok=True)
            target = DatabaseTarget(
                project=install.legacy_target.project,
                db_path=install.db_path,
                explicit_db=True,
            )
            create_v14_target(target)
            with closing(connect(target.db_path)) as connection:
                apply_completion_cycle_history_migration(connection)
            with closing(connect(target.db_path)) as connection:
                apply_completion_cycle_capture_activation_migration(connection)
            with closing(connect(target.db_path)) as connection:
                task = add_task(
                    connection,
                    target.project,
                    title="Schema v16 stored Task boundary",
                ).task
                connection.commit()
            with closing(connect(target.db_path)) as connection:
                self.assertEqual(current_schema_version(connection), 16)
            inject_task_fault(
                target.db_path,
                task["task_id"],
                assignments={"review_tier": 1.5},
            )
            before = target.db_path.read_bytes()

            result = install.run("setup", "--json")

            payload = assert_fixed_cli_failure(self, result)
            self.assertEqual(payload["data"]["completed_writes"], [])
            self.assertEqual(target.db_path.read_bytes(), before)
            with closing(connect(target.db_path)) as connection:
                self.assertEqual(current_schema_version(connection), 16)


if __name__ == "__main__":
    unittest.main()
