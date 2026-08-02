from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import (
    json_payload,
    make_physical_install,
    run_taskgov_internal,
)
from tests.m214c_test_support import (
    FIXED_CODE,
    FIXED_MESSAGE,
    assert_fixed_cli_failure,
    inject_contract_pointer_fault,
    seed_current_task,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import viewer_maintenance  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    configure_project_maintenance,
    connect_initialized_readonly,
    resolve_database_target,
)
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


def add_contract_task(
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
        "--contract-scope",
        "Validate one selected Contract relationship.",
        "--contract-acceptance",
        "The pointer resolves to the latest revision.",
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json.loads(result.stdout)["data"]["task"]


class StoredContractPointerConsumerTests(unittest.TestCase):
    def test_public_projection_and_lifecycle_paths_fail_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(
                Path(temp),
                status="ready",
                with_contract=True,
            )
            inject_contract_pointer_fault(db, task["task_id"], pointer=99)
            before = db.read_bytes()
            for command in (
                ("task", "list"),
                ("task", "next"),
                ("task", "next", "--compact"),
                ("task", "show", task["task_id"]),
            ):
                with self.subTest(command=command):
                    assert_fixed_cli_failure(self, run_json(repo, db, *command))
                    self.assertEqual(db.read_bytes(), before)

        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(
                Path(temp),
                status="in_progress",
                with_contract=True,
            )
            inject_contract_pointer_fault(db, task["task_id"], pointer=99)
            before = db.read_bytes()
            commands = (
                ("task", "current"),
                ("task", "current", "--compact"),
                (
                    "task",
                    "checkpoint",
                    task["task_id"],
                    "--summary",
                    "Current state",
                    "--next-action",
                    "Continue relationship verification",
                ),
                ("handoff", "record", task["task_id"], "--summary", "Defer"),
                ("task", "effort", task["task_id"], "--read-only"),
                ("task", "edit", task["task_id"], "--title", "Updated title"),
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
                    assert_fixed_cli_failure(self, run_json(repo, db, *command))
                    self.assertEqual(db.read_bytes(), before)

    def test_selected_batch_does_not_scan_unreturned_relationship(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db, first = seed_current_task(
                Path(temp),
                status="ready",
                priority="urgent",
                with_contract=True,
            )
            second = add_contract_task(
                repo,
                db,
                title="Unselected dangling Contract Task",
                status="ready",
                priority="low",
            )
            inject_contract_pointer_fault(db, second["task_id"], pointer=99)

            result = run_json(repo, db, "task", "next", "--limit", "1", "--compact")

            self.assertEqual(result.returncode, 0, result.stdout)
            tasks = json.loads(result.stdout)["data"]["tasks"]
            self.assertEqual([task["task_id"] for task in tasks], [first["task_id"]])

    def test_viewer_preserves_last_good_on_relationship_fault(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(
                Path(temp),
                status="ready",
                with_contract=True,
            )
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
            inject_contract_pointer_fault(db, task["task_id"], pointer=99)

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
            self.assertEqual(list(output.path.parent.glob(".task-viewer-*.tmp")), [])

    def test_doctor_and_setup_use_the_whole_batch_boundary(self):
        for consumer in ("doctor", "setup"):
            with self.subTest(consumer=consumer), tempfile.TemporaryDirectory() as temp:
                install = make_physical_install(Path(temp))
                setup = install.run("setup", "--json")
                self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
                added = install.run(
                    "task",
                    "add",
                    "--title",
                    f"{consumer} Contract relationship boundary",
                    "--contract-scope",
                    "Validate the whole-project Contract relationship.",
                    "--contract-acceptance",
                    "The pointer resolves to the latest revision.",
                    "--json",
                )
                self.assertEqual(added.returncode, 0, added.stdout or added.stderr)
                task_id = json_payload(added)["data"]["task"]["task_id"]
                inject_contract_pointer_fault(install.db_path, task_id, pointer=99)
                before = install.db_path.read_bytes()

                if consumer == "doctor":
                    result = install.run("doctor", "--json")
                    payload = json_payload(result)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(
                        payload["errors"],
                        [{"code": FIXED_CODE, "message": FIXED_MESSAGE}],
                    )
                    components = payload["data"]["components"]
                    self.assertEqual(
                        components["project_state"]["code"],
                        "unreadable",
                    )
                    for name in ("task_summary", "handoff_delivery", "maintenance"):
                        self.assertEqual(components[name], {"code": "unavailable"})
                else:
                    result = install.run(
                        "setup",
                        "--backup-generations",
                        "4",
                        "--json",
                    )
                    payload = assert_fixed_cli_failure(self, result)
                    self.assertEqual(payload["data"]["completed_writes"], [])
                self.assertEqual(install.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
