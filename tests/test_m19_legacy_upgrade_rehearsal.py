from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CURRENT_SKILL_ROOT = ROOT / "task-governance-tool"
LEGACY_COMMIT = "f017ee228d435d892fb7136c5e79b3063320fac5"
LEGACY_COMPLETION = "a" * 40
POST_UPGRADE_VERIFICATION = "Run post-upgrade integrated acceptance"
POST_UPGRADE_FINGERPRINT = "sha256:" + "b" * 64
LEGACY_SETUP_WRITES = [
    "legacy_state_publish",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "viewer_publish",
    "legacy_state_cleanup",
]
INJECTED_SETUP = r"""
import sys
from pathlib import Path

scripts = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(scripts))
from task_governance_tool import cli, setup

cli.set_cli_script_path(scripts / "taskgov.py")

def fail_migration(*args, **kwargs):
    raise RuntimeError("injected migration failure")

setup.initialize_database = fail_migration
raise SystemExit(
    cli.main(["setup", "--repo", sys.argv[2], "--json"])
)
"""


def git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


def run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-C",
            str(ROOT),
            *arguments,
        ],
        env=git_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=60,
    )


def require_git(*arguments: str) -> bytes:
    result = run_git(*arguments)
    if result.returncode != 0:
        stderr_digest = hashlib.sha256(result.stderr).hexdigest()
        raise AssertionError(
            "local Git command failed "
            f"(exit={result.returncode}, stderr_sha256={stderr_digest})"
        )
    return result.stdout


def extract_legacy_skill(destination: Path) -> Path:
    require_git("cat-file", "-e", f"{LEGACY_COMMIT}^{{commit}}")
    archive = require_git(
        "archive",
        "--format=tar",
        LEGACY_COMMIT,
        "--",
        "task-governance-tool",
    )
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source.getmembers():
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != "task-governance-tool"
                or any(part in {"", ".", ".."} for part in relative.parts)
                or not (member.isdir() or member.isfile())
            ):
                raise AssertionError("legacy archive has an unsupported member")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            extracted = source.extractfile(member)
            if extracted is None:
                raise AssertionError("legacy archive file could not be read")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(extracted.read())
    return destination / "task-governance-tool"


def staged_package_paths() -> tuple[Path, ...]:
    raw = require_git(
        "ls-files",
        "-z",
        "--",
        "task-governance-tool",
    )
    paths: list[Path] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        decoded = item.decode("utf-8")
        relative = PurePosixPath(decoded)
        if (
            not relative.parts
            or relative.parts[0] != "task-governance-tool"
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise AssertionError("current package inventory is invalid")
        paths.append(Path(*relative.parts[1:]))
    if not paths:
        raise AssertionError("current staged package inventory is empty")
    return tuple(paths)


def overlay_current_package(skill_root: Path) -> None:
    state = skill_root / "state"
    for child in tuple(skill_root.iterdir()):
        if child == state:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for relative in staged_package_paths():
        source = CURRENT_SKILL_ROOT / relative
        if not source.is_file():
            raise AssertionError("current staged package file is missing")
        destination = skill_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def run_cli(
    skill_root: Path,
    project: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            str(skill_root / "scripts" / "taskgov.py"),
            *map(str, arguments),
        ],
        cwd=project,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        shell=False,
        timeout=60,
    )


def run_injected_setup(
    skill_root: Path,
    project: Path,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            "-c",
            INJECTED_SETUP,
            str(skill_root / "scripts"),
            str(project),
        ],
        cwd=project,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        shell=False,
        timeout=60,
    )


def json_payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if len(result.stdout.encode("utf-8")) > 262_144:
        raise AssertionError("CLI JSON exceeded the rehearsal bound")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        digest = hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()
        raise AssertionError(
            f"CLI output was not JSON (stdout_sha256={digest})"
        ) from exc
    if not isinstance(payload, dict):
        raise AssertionError("CLI JSON root must be an object")
    return payload


def tree_snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    if not root.exists():
        return ()
    items: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            items.append((relative, "directory", 0))
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            items.append((relative, digest, path.stat().st_size))
        else:
            raise AssertionError("rehearsal tree contains a link-like entry")
    return tuple(items)


def without_state_lock(
    snapshot: tuple[tuple[str, str, int], ...],
) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        item for item in snapshot if item[0] != "taskgov-state.lock"
    )


def sqlite_version(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
    return int(row[0])


def integrity(connection: sqlite3.Connection) -> tuple[list[str], list[tuple[Any, ...]]]:
    quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    foreign_keys = [
        tuple(row) for row in connection.execute("PRAGMA foreign_key_check")
    ]
    return quick, foreign_keys


def legacy_projection(db_path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        project = connection.execute(
            """
            SELECT project_id, canonical_path_hash, display_name
              FROM project_meta
            """
        ).fetchall()
        tasks = connection.execute(
            """
            SELECT task_id, project_id, title, description, kind, lane,
                   lane_order, priority, status, blocked_reason, review_tier,
                   verification, tags, created_at, updated_at, completed_at,
                   completion_commit_required, completion_commit_hash
              FROM tasks
             ORDER BY task_id
            """
        ).fetchall()
        events = connection.execute(
            """
            SELECT task_event_id, task_id, project_id, event_type, summary,
                   created_at
              FROM task_events
             ORDER BY task_event_id
            """
        ).fetchall()
        tool_events = connection.execute(
            """
            SELECT tool_event_id, project_id, command, status, summary,
                   created_at
              FROM tool_events
             ORDER BY tool_event_id
            """
        ).fetchall()
        return {
            "project": [tuple(row) for row in project],
            "tasks": [tuple(row) for row in tasks],
            "events": [tuple(row) for row in events],
            "tool_events": [tuple(row) for row in tool_events],
        }


class LegacyUpgradeAndRollbackRehearsalTests(unittest.TestCase):
    """End-to-end complement to the existing 12-Task/191-event migration fixture."""

    def invoke(
        self,
        skill: Path,
        project: Path,
        *arguments: str,
        expected: int = 0,
    ) -> dict[str, Any]:
        result = run_cli(skill, project, *arguments)
        self.assertEqual(result.returncode, expected)
        return json_payload(result)

    def test_exact_legacy_package_upgrades_and_rolls_back_as_one_compatibility_point(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            skill_parent = project / ".agents" / "skills"
            project.mkdir()
            legacy_skill = extract_legacy_skill(skill_parent)
            ancestry = run_git(
                "merge-base", "--is-ancestor", LEGACY_COMMIT, "HEAD"
            )
            self.assertEqual(ancestry.returncode, 0)
            version = run_cli(legacy_skill, project, "--version")
            self.assertEqual(version.returncode, 0)
            self.assertEqual(version.stdout.strip(), "taskgov 0.1.0")

            initialized = self.invoke(
                legacy_skill, project, "db", "init",
                "--repo", str(project), "--json",
            )
            self.assertEqual(initialized["data"]["schema_version"], 2)
            legacy_db = Path(initialized["db_path"])
            self.assertTrue(legacy_db.is_file())

            added = self.invoke(
                legacy_skill, project, "task", "add",
                "--repo", str(project),
                "--title", "Legacy completed sample",
                "--description", "Small real-history release rehearsal",
                "--review-tier", "0",
                "--verification", "Legacy verification recorded",
                "--json",
            )
            task_id = added["data"]["task"]["task_id"]
            completed = self.invoke(
                legacy_skill, project, "task", "edit", task_id,
                "--repo", str(project), "--status", "done",
                "--completion-commit-hash", LEGACY_COMPLETION,
                "--verification-complete",
                "--review-complete",
                "--add-note", "Legacy completion sample", "--json",
            )
            self.assertEqual(completed["data"]["task"]["status"], "done")

            pre_upgrade_projection = legacy_projection(legacy_db)
            self.assertEqual(len(pre_upgrade_projection["tasks"]), 1)
            self.assertEqual(len(pre_upgrade_projection["events"]), 2)
            self.assertEqual(
                pre_upgrade_projection["tasks"][0][-2:],
                (1, LEGACY_COMPLETION),
            )
            self.assertEqual(sqlite_version(legacy_db), 2)
            compatibility = root / "compatibility-point"
            shutil.copytree(legacy_skill, compatibility)
            compatibility_snapshot = tree_snapshot(compatibility)
            legacy_state_snapshot = tree_snapshot(legacy_skill / "state")

            overlay_current_package(legacy_skill)
            overlay_state_snapshot = tree_snapshot(legacy_skill / "state")
            self.assertEqual(overlay_state_snapshot, legacy_state_snapshot)
            version = run_cli(legacy_skill, project, "--version")
            self.assertEqual(version.returncode, 0)
            self.assertEqual(version.stdout.strip(), "taskgov 0.11.0")

            preview = self.invoke(
                legacy_skill, project, "setup", "--repo", str(project),
                "--read-only", "--json",
            )
            self.assertEqual(preview["data"]["schema_from"], 2)
            self.assertEqual(preview["data"]["schema_to"], 17)
            self.assertEqual(preview["data"]["planned_writes"], LEGACY_SETUP_WRITES)
            self.assertEqual(preview["data"]["completed_writes"], [])
            self.assertEqual(
                tree_snapshot(legacy_skill / "state"),
                overlay_state_snapshot,
            )

            failed_result = run_injected_setup(legacy_skill, project)
            self.assertEqual(failed_result.returncode, 2)
            failed = json_payload(failed_result)
            self.assertEqual(failed["errors"][0]["code"], "setup_migration_failed")
            self.assertEqual(failed["data"]["planned_writes"], LEGACY_SETUP_WRITES)
            self.assertEqual(failed["data"]["completed_writes"], [])
            failed_state = tree_snapshot(legacy_skill / "state")
            self.assertEqual(
                without_state_lock(failed_state),
                overlay_state_snapshot,
            )
            self.assertEqual(
                [item[2] for item in failed_state if item[0] == "taskgov-state.lock"],
                [1],
            )
            self.assertFalse(
                any(
                    item[0].startswith(".current-stage-")
                    for item in failed_state
                )
            )
            self.assertEqual(sqlite_version(legacy_db), 2)

            upgraded = self.invoke(
                legacy_skill, project, "setup",
                "--repo", str(project), "--json",
            )
            self.assertEqual(upgraded["data"]["schema_from"], 2)
            self.assertEqual(upgraded["data"]["schema_to"], 17)
            self.assertEqual(upgraded["data"]["planned_writes"], LEGACY_SETUP_WRITES)
            self.assertEqual(upgraded["data"]["completed_writes"], LEGACY_SETUP_WRITES)

            current_db = legacy_skill / "state" / "current" / "taskgov.sqlite"
            viewer = (
                legacy_skill / "state" / "current" / "viewer" / "task-viewer.html"
            )
            self.assertTrue(current_db.is_file())
            self.assertTrue(viewer.is_file())
            self.assertFalse(legacy_db.exists())
            self.assertEqual(sqlite_version(current_db), 17)
            self.assertEqual(legacy_projection(current_db), pre_upgrade_projection)

            with closing(sqlite3.connect(current_db)) as connection:
                connection.row_factory = sqlite3.Row
                self.assertEqual(integrity(connection), (["ok"], []))
                current_task = connection.execute(
                    """
                    SELECT task_id, status, completion_commit_required,
                           completion_commit_hash,
                           completion_history_coverage
                      FROM tasks WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                self.assertEqual(
                    tuple(current_task),
                    (
                        task_id,
                        "done",
                        1,
                        LEGACY_COMPLETION,
                        "legacy_unknown",
                    ),
                )
                cycle = connection.execute(
                    """
                    SELECT task_id, saved_cycle_ordinal, origin, completeness,
                           completion_evidence_kind,
                           completion_evidence_revision,
                           completion_commit_hash,
                           verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
                self.assertEqual(
                    tuple(cycle),
                    (
                        task_id,
                        1,
                        "legacy_current_done",
                        "partial",
                        "legacy_unverified",
                        LEGACY_COMPLETION,
                        LEGACY_COMPLETION,
                        0,
                        None,
                        None,
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_receipts"
                    ).fetchone()[0],
                    0,
                )

            backups = sorted(
                (current_db.parent / "backups").glob("taskgov-backup-v1_*.sqlite")
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(sqlite_version(backups[0]), 2)
            self.assertEqual(legacy_projection(backups[0]), pre_upgrade_projection)
            with closing(sqlite3.connect(backups[0])) as connection:
                self.assertEqual(integrity(connection), (["ok"], []))

            doctor = self.invoke(
                legacy_skill, project, "doctor", "--repo", str(project),
                "--read-only", "--json",
            )
            self.assertTrue(doctor["ok"])
            self.assertEqual(
                doctor["data"]["components"]["package"]["status"],
                "clean",
            )
            self.assertEqual(
                doctor["data"]["components"]["project_state"]["schema_version"],
                17,
            )
            self.assertEqual(
                doctor["data"]["components"]["maintenance"]["viewer"]["code"],
                "current",
            )
            shown = self.invoke(
                legacy_skill, project, "task", "show", task_id,
                "--repo", str(project), "--json",
            )
            self.assertEqual(shown["data"]["task"]["task_id"], task_id)
            self.assertEqual(shown["data"]["task"]["status"], "done")

            fresh = self.invoke(
                legacy_skill, project, "task", "add",
                "--repo", str(project),
                "--title", "Post-upgrade Receipt completion",
                "--status", "in_progress",
                "--review-tier", "2",
                "--verification", POST_UPGRADE_VERIFICATION,
                "--json",
            )
            fresh_task_id = fresh["data"]["task"]["task_id"]
            target = self.invoke(
                legacy_skill, project, "review", "target", "set",
                fresh_task_id, "--repo", str(project),
                "--kind", "diff_fingerprint",
                "--revision", POST_UPGRADE_FINGERPRINT,
                "--json",
            )
            generation = target["data"]["task"]["review_target_generation"]
            receipt = self.invoke(
                legacy_skill, project, "verification", "receipt", "add",
                fresh_task_id, "--repo", str(project),
                "--command-label", "Post-upgrade integrated acceptance",
                "--result", "pass",
                "--duration-ms", "1",
                "--scope-coverage", "full",
                "--expected-target-generation", str(generation),
                "--json",
            )
            receipt_id = receipt["data"]["receipt"]["verification_receipt_id"]
            packet = self.invoke(
                legacy_skill, project, "review", "prepare",
                fresh_task_id, "--repo", str(project),
                "--read-only", "--json",
            )
            self.assertEqual(
                packet["data"]["review_target"],
                {
                    "kind": "diff_fingerprint",
                    "value": POST_UPGRADE_FINGERPRINT,
                    "base_revision": "",
                    "generation": generation,
                },
            )
            for reviewer in ("post-upgrade-review-a", "post-upgrade-review-b"):
                self.invoke(
                    legacy_skill, project, "review", "receipt", "add",
                    fresh_task_id, "--repo", str(project),
                    "--reviewer", reviewer,
                    "--kind", "independent",
                    "--verdict", "pass",
                    "--summary", "Post-upgrade review passed",
                    "--json",
                )
            fresh_completed = self.invoke(
                legacy_skill, project, "task", "complete",
                fresh_task_id, "--repo", str(project),
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
                "--json",
            )
            self.assertEqual(
                fresh_completed["data"]["task"]["status"],
                "done",
            )
            fresh_shown = self.invoke(
                legacy_skill, project, "task", "show", fresh_task_id,
                "--repo", str(project), "--read-only", "--json",
            )
            self.assertEqual(fresh_shown["data"]["task"]["status"], "done")
            self.assertEqual(
                fresh_shown["data"]["verification_evidence"]["gate"]
                ["qualifying_receipt_id"],
                receipt_id,
            )
            self.assertNotIn(
                "verification_receipt_id",
                json.dumps(
                    fresh_shown["data"]["completion_history"],
                    sort_keys=True,
                ),
            )

            expected_digest = hashlib.sha256(
                b"taskgov-verification-expectation-v1\0"
                + POST_UPGRADE_VERIFICATION.encode("utf-8")
            ).hexdigest()
            with closing(sqlite3.connect(current_db)) as connection:
                connection.row_factory = sqlite3.Row
                fresh_cycle = connection.execute(
                    """
                    SELECT origin, completeness, verification_basis_version,
                           verification_expectation_digest,
                           verification_receipt_id
                      FROM task_completion_cycles WHERE task_id = ?
                    """,
                    (fresh_task_id,),
                ).fetchone()
                self.assertEqual(
                    tuple(fresh_cycle),
                    (
                        "native_done",
                        "complete",
                        1,
                        expected_digest,
                        receipt_id,
                    ),
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            """
                            SELECT verification_basis_version,
                                   verification_expectation_digest,
                                   verification_receipt_id
                              FROM task_completion_cycles WHERE task_id = ?
                            """,
                            (task_id,),
                        ).fetchone()
                    ),
                    (0, None, None),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_receipts"
                    ).fetchone()[0],
                    1,
                )

            final_doctor = self.invoke(
                legacy_skill, project, "doctor", "--repo", str(project),
                "--read-only", "--json",
            )
            self.assertEqual(
                final_doctor["data"]["components"]["project_state"]["code"],
                "ready",
            )
            self.assertEqual(
                final_doctor["data"]["components"]["maintenance"]["viewer"]
                ["code"],
                "current",
            )

            # Never invoke the legacy runtime until its matching package and
            # schema-v2 state have both been restored.
            shutil.rmtree(legacy_skill)
            shutil.copytree(compatibility, legacy_skill)
            self.assertEqual(tree_snapshot(legacy_skill), compatibility_snapshot)
            restored_db = next(
                (legacy_skill / "state" / "projects").glob("*/taskgov.sqlite")
            )
            self.assertEqual(sqlite_version(restored_db), 2)
            restarted = self.invoke(
                legacy_skill, project, "db", "status",
                "--repo", str(project), "--read-only", "--json",
            )
            self.assertEqual(restarted["data"]["schema_version"], 2)
            restored_show = self.invoke(
                legacy_skill, project, "task", "show", task_id,
                "--repo", str(project), "--read-only", "--json",
            )
            self.assertEqual(restored_show["data"]["task"]["status"], "done")
            self.assertEqual(legacy_projection(restored_db), pre_upgrade_projection)

    def test_unsupported_legacy_user_wide_and_custom_db_are_refused_unchanged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            user_project = root / "user-project"
            user_project.mkdir()
            user_root = root / "home" / ".agents" / "skills"
            user_skill = extract_legacy_skill(user_root)
            old_user_state = self.invoke(
                user_skill, user_project, "db", "init",
                "--repo", str(user_project), "--json",
            )
            self.assertEqual(old_user_state["data"]["schema_version"], 2)
            overlay_current_package(user_skill)
            user_before = tree_snapshot(root)
            refused_user = self.invoke(
                user_skill, user_project, "setup",
                "--repo", str(user_project), "--json", expected=2,
            )
            self.assertEqual(
                refused_user["errors"][0]["code"],
                "unsupported_install_layout",
            )
            self.assertEqual(tree_snapshot(root), user_before)

            custom_project = root / "custom-project"
            custom_project.mkdir()
            custom_skill = extract_legacy_skill(custom_project / ".agents" / "skills")
            custom_db = root / "custom-state" / "taskgov.sqlite"
            old_custom = self.invoke(
                custom_skill, custom_project, "db", "init",
                "--repo", str(custom_project),
                "--db", str(custom_db), "--json",
            )
            self.assertEqual(old_custom["data"]["schema_version"], 2)
            overlay_current_package(custom_skill)
            custom_before = tree_snapshot(root)
            refused_custom = self.invoke(
                custom_skill, custom_project, "setup",
                "--repo", str(custom_project),
                "--db", str(custom_db), "--json", expected=2,
            )
            self.assertEqual(refused_custom["errors"][0]["code"], "invalid_option")
            self.assertEqual(tree_snapshot(root), custom_before)
            self.assertEqual(sqlite_version(custom_db), 2)
            self.assertFalse((custom_skill / "state").exists())


if __name__ == "__main__":
    unittest.main()
