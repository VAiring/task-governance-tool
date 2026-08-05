from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import (
    PhysicalInstall,
    canonical_managed_sqlite_files,
    extract_skill_at_commit,
    file_snapshot,
    json_payload,
    make_physical_install,
    require_repository_git,
)


M22_2_BASELINE_COMMIT = "b954372b30ff3a0b08fca9b804f78b08004825d3"
M22_2_PACKAGE_TREE = "e2bbd5a0e34859680118b25565daa15ca63b8c05"
BACKUP_SCRIPT = r"""
import json
import sys
from pathlib import Path

scripts = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(scripts))
from task_governance_tool.backup import run_routine_backup
from task_governance_tool.state_resolver import (
    consumer_error_code,
    resolve_project_state,
)

project_root = Path(sys.argv[2]).resolve()
resolution = resolve_project_state(
    skill_root=scripts.parent,
    repo=project_root,
)
error_code = consumer_error_code(resolution)
if error_code is not None or resolution.target is None:
    raise RuntimeError(error_code or "project_state_unreadable")
target = resolution.target
result = run_routine_backup(target, observed_at="2099-01-01T00:00:00Z")
print(json.dumps({"attempted": result.attempted, "code": result.code}))
"""


def table_counts(db: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(db)) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("tasks", "task_events")
        )


def task_row(db: Path, task_id: str) -> tuple[object, ...]:
    with closing(sqlite3.connect(db)) as connection:
        row = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if row is None:
        raise AssertionError("test Task is missing")
    return tuple(row)


def setup_install(root: Path) -> PhysicalInstall:
    install = make_physical_install(root)
    result = install.run("setup", "--json")
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return install


def add_task(
    install: PhysicalInstall,
    verification: str,
    *,
    title: str = "Verification capacity task",
) -> tuple[object, dict[str, object]]:
    result = install.run(
        "task",
        "add",
        "--title",
        title,
        "--verification",
        verification,
        "--json",
    )
    return result, json.loads(result.stdout)


def require_cli_json(install: PhysicalInstall, *arguments: str) -> dict[str, object]:
    result = install.run(*arguments, "--json")
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json_payload(result)


def run_exact_package_backup(install: PhysicalInstall) -> dict[str, object]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            "-c",
            BACKUP_SCRIPT,
            str(install.skill_root / "scripts"),
            str(install.project_root),
        ],
        cwd=install.project_root,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json.loads(result.stdout)


class M215VerificationInputCapacityTests(unittest.TestCase):
    def test_task_add_accepts_new_boundaries_and_rejects_1001_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = setup_install(Path(temporary))
            accepted = (
                (500, "x"),
                (501, "x"),
                (1_000, "x"),
                (1_000, "界"),
            )
            for length, character in accepted:
                with self.subTest(length=length, character=character):
                    value = character * length
                    result, payload = add_task(
                        install,
                        value,
                        title=f"Add boundary {length} {ord(character)}",
                    )
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(payload["data"]["task"]["verification"], value)

            before_bytes = install.db_path.read_bytes()
            before_counts = table_counts(install.db_path)
            result, payload = add_task(install, "x" * 1_001)

            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(payload["errors"][0]["code"], "invalid_argument")
            self.assertEqual(install.db_path.read_bytes(), before_bytes)
            self.assertEqual(table_counts(install.db_path), before_counts)

    def test_task_edit_accepts_new_boundaries_and_rejects_1001_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = setup_install(Path(temporary))
            added, payload = add_task(install, "initial verification")
            self.assertEqual(added.returncode, 0, added.stdout)
            task_id = str(payload["data"]["task"]["task_id"])

            accepted = (
                (500, "x"),
                (501, "x"),
                (1_000, "x"),
                (1_000, "界"),
            )
            for length, character in accepted:
                with self.subTest(length=length, character=character):
                    value = character * length
                    result = install.run(
                        "task",
                        "edit",
                        task_id,
                        "--verification",
                        value,
                        "--json",
                    )
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(
                        json_payload(result)["data"]["task"]["verification"],
                        value,
                    )

            before_bytes = install.db_path.read_bytes()
            before_counts = table_counts(install.db_path)
            before_task = task_row(install.db_path, task_id)
            result = install.run(
                "task",
                "edit",
                task_id,
                "--verification",
                "x" * 1_001,
                "--json",
            )
            rejected = json_payload(result)

            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(rejected["errors"][0]["code"], "invalid_argument")
            self.assertEqual(install.db_path.read_bytes(), before_bytes)
            self.assertEqual(table_counts(install.db_path), before_counts)
            self.assertEqual(task_row(install.db_path, task_id), before_task)

    def test_privacy_precedes_capacity_for_add_and_edit_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = setup_install(Path(temporary))
            added, payload = add_task(install, "initial verification")
            self.assertEqual(added.returncode, 0, added.stdout)
            task_id = str(payload["data"]["task"]["task_id"])
            private_overflow = "token=secret " + ("x" * 1_000)

            for command in (
                (
                    "task",
                    "add",
                    "--title",
                    "Private verification add",
                    "--verification",
                    private_overflow,
                    "--json",
                ),
                (
                    "task",
                    "edit",
                    task_id,
                    "--verification",
                    private_overflow,
                    "--json",
                ),
            ):
                with self.subTest(command=command[:2]):
                    before_bytes = install.db_path.read_bytes()
                    before_counts = table_counts(install.db_path)
                    before_task = task_row(install.db_path, task_id)
                    result = install.run(*command)
                    rejected = json_payload(result)

                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertEqual(
                        rejected["errors"][0]["code"],
                        "privacy_rejected",
                    )
                    self.assertNotIn("token=secret", result.stdout + result.stderr)
                    self.assertEqual(install.db_path.read_bytes(), before_bytes)
                    self.assertEqual(table_counts(install.db_path), before_counts)
                    self.assertEqual(task_row(install.db_path, task_id), before_task)

    def test_exact_m22_2_package_accepts_m215_database_without_mixed_files(self):
        self.assertEqual(
            require_repository_git(
                "rev-parse",
                f"{M22_2_BASELINE_COMMIT}^{{commit}}",
            ).decode("ascii").strip(),
            M22_2_BASELINE_COMMIT,
        )
        self.assertEqual(
            require_repository_git(
                "rev-parse",
                f"{M22_2_BASELINE_COMMIT}:task-governance-tool",
            ).decode("ascii").strip(),
            M22_2_PACKAGE_TREE,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = setup_install(root / "current")
            added = install.run(
                "task",
                "add",
                "--title",
                "M21.5 compatibility Task",
                "--status",
                "in_progress",
                "--review-tier",
                "1",
                "--verification",
                "界" * 1_000,
                "--contract-scope",
                "Exercise the exact M22.2 package against M21.5 state.",
                "--contract-acceptance",
                "All representative compatibility consumers remain usable.",
                "--contract-constraints",
                "Do not replay stored verification through public ingress.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m22-evidence-ledger.md:1228",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout)
            writer_payload = json_payload(added)
            project_id = str(writer_payload["project_id"])
            task_id = str(writer_payload["data"]["task"]["task_id"])

            baseline = extract_skill_at_commit(
                root / "baseline-archive",
                M22_2_BASELINE_COMMIT,
            )
            expected_package = file_snapshot(baseline, exclude_state=True)
            held_state = root / "held-state"
            shutil.move(str(install.skill_root / "state"), held_state)
            shutil.rmtree(install.skill_root)
            shutil.move(str(baseline), install.skill_root)
            shutil.move(str(held_state), install.skill_root / "state")

            self.assertEqual(
                file_snapshot(install.skill_root, exclude_state=True),
                expected_package,
            )

            listed = require_cli_json(install, "task", "list")
            self.assertEqual(
                [task["task_id"] for task in listed["data"]["tasks"]],
                [task_id],
            )
            shown = require_cli_json(install, "task", "show", task_id)
            self.assertEqual(shown["project_id"], project_id)
            self.assertEqual(
                shown["data"]["task"]["verification"],
                "界" * 1_000,
            )
            selected = require_cli_json(install, "task", "current")
            self.assertEqual(
                [task["task_id"] for task in selected["data"]["tasks"]],
                [task_id],
            )
            compact = require_cli_json(
                install,
                "task",
                "current",
                "--compact",
            )
            self.assertEqual(
                [task["task_id"] for task in compact["data"]["tasks"]],
                [task_id],
            )
            edited = require_cli_json(
                install,
                "task",
                "edit",
                task_id,
                "--priority",
                "high",
            )
            self.assertEqual(
                edited["data"]["task"]["verification"],
                "界" * 1_000,
            )

            revised = require_cli_json(
                install,
                "task",
                "edit",
                task_id,
                "--contract-scope",
                "Exercise the complete exact-package compatibility canary.",
                "--contract-acceptance",
                "The baseline package consumes M21.5 state without mutation loss.",
                "--contract-constraints",
                "Preserve the 1,000-code-point verification value exactly.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m22-evidence-ledger.md:1228",
                "--contract-change-reason",
                "Advance the compatibility canary Contract.",
            )
            self.assertEqual(revised["data"]["task"]["verification"], "界" * 1_000)
            self.assertEqual(
                revised["data"]["contract_write"],
                {"recorded": True, "revision": 2},
            )

            install.viewer_path.unlink()
            setup = require_cli_json(
                install,
                "setup",
                "--backup-interval-minutes",
                "1",
                "--backup-generations",
                "3",
            )
            self.assertIn(setup["data"]["status"], {"already_setup", "setup_complete"})
            self.assertEqual(setup["project_id"], project_id)
            self.assertIn("viewer_publish", setup["data"]["completed_writes"])
            self.assertEqual(setup["data"]["viewer_status"], "published")
            self.assertTrue(install.viewer_path.is_file())
            projected = require_cli_json(install, "task", "show", task_id)
            self.assertEqual(
                projected["data"]["task"]["verification"],
                "界" * 1_000,
            )

            first_target = require_cli_json(
                install,
                "review",
                "target",
                "set",
                task_id,
                "--kind",
                "diff_fingerprint",
                "--revision",
                "sha256:" + ("a" * 64),
            )
            self.assertEqual(
                first_target["data"]["task"]["verification"],
                "界" * 1_000,
            )
            changes = require_cli_json(
                install,
                "review",
                "receipt",
                "add",
                task_id,
                "--reviewer",
                "baseline-reviewer-changes",
                "--kind",
                "independent",
                "--verdict",
                "changes_requested",
                "--summary",
                "One representative finding exercises the retained evidence path.",
                "--reviewer-class",
                "human",
                "--model-state",
                "not_applicable",
                "--skill-state",
                "not_applicable",
                "--review-profile",
                "general",
                "--review-lens",
                "correctness",
                "--context-relation",
                "external_context",
                "--review-method",
                "review_packet_inspection",
            )
            finding = require_cli_json(
                install,
                "review",
                "finding",
                "add",
                task_id,
                "--receipt-id",
                str(changes["data"]["receipt"]["review_receipt_id"]),
                "--severity",
                "medium",
                "--summary",
                "Representative compatibility finding.",
            )
            require_cli_json(
                install,
                "review",
                "finding",
                "resolve",
                str(finding["data"]["finding"]["review_finding_id"]),
                "--resolution",
                "Resolved within the compatibility canary.",
            )

            target = require_cli_json(
                install,
                "review",
                "target",
                "set",
                task_id,
                "--kind",
                "diff_fingerprint",
                "--revision",
                "sha256:" + ("b" * 64),
            )
            generation = str(target["data"]["task"]["review_target_generation"])
            require_cli_json(
                install,
                "verification",
                "receipt",
                "add",
                task_id,
                "--result",
                "pass",
                "--duration-ms",
                "7",
                "--scope-coverage",
                "full",
                "--expected-target-generation",
                generation,
            )
            require_cli_json(
                install,
                "review",
                "receipt",
                "add",
                task_id,
                "--reviewer",
                "baseline-reviewer-pass",
                "--kind",
                "independent",
                "--verdict",
                "pass",
                "--reviewer-class",
                "human",
                "--model-state",
                "not_applicable",
                "--skill-state",
                "not_applicable",
                "--review-profile",
                "general",
                "--review-lens",
                "correctness",
                "--context-relation",
                "external_context",
                "--review-method",
                "review_packet_inspection",
            )
            completed = require_cli_json(
                install,
                "task",
                "complete",
                task_id,
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            self.assertEqual(completed["data"]["task"]["status"], "done")
            reopened = require_cli_json(
                install,
                "task",
                "edit",
                task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Exercise exact-package reopen compatibility.",
            )
            self.assertEqual(reopened["data"]["task"]["verification"], "界" * 1_000)

            before_backups = canonical_managed_sqlite_files(
                install,
                exclude=(install.db_path,),
            )
            backup = run_exact_package_backup(install)
            self.assertEqual(backup, {"attempted": True, "code": "succeeded"})
            after_backups = canonical_managed_sqlite_files(
                install,
                exclude=(install.db_path,),
            )
            self.assertGreater(len(after_backups), len(before_backups))

            install.db_path.unlink()
            preview = require_cli_json(install, "setup", "--read-only")
            self.assertEqual(preview["data"]["status"], "setup_preview")
            self.assertIn("database_restore", preview["data"]["planned_writes"])
            restored = require_cli_json(install, "setup")
            self.assertEqual(restored["project_id"], project_id)
            self.assertIn("database_restore", restored["data"]["completed_writes"])
            recovered = require_cli_json(install, "task", "show", task_id)
            self.assertEqual(recovered["project_id"], project_id)
            self.assertEqual(recovered["data"]["task"]["verification"], "界" * 1_000)
            self.assertEqual(recovered["data"]["task"]["status"], "in_progress")
            self.assertEqual(recovered["data"]["completion_history"]["total"], 1)
            self.assertEqual(
                file_snapshot(install.skill_root, exclude_state=True),
                expected_package,
            )


if __name__ == "__main__":
    unittest.main()
