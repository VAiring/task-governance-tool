import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import make_physical_install


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.effort import (  # noqa: E402
    PROFILE_ID,
    WARNING_KEY,
    build_effort_advisory,
    disabled_profile,
    load_effort_profile,
    observe_git_measurements,
)
from task_governance_tool import effort as effort_service  # noqa: E402
from task_governance_tool import completion as completion_service  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    DATABASE_BUSY_MESSAGE,
    SCHEMA_VERSION,
    StorageError,
    apply_completion_commit_migration,
    apply_completion_evidence_migration,
    apply_effort_advisory_migration,
    apply_git_snapshot_schema_migration,
    apply_handoff_outbox_migration,
    apply_initial_schema_migration,
    apply_paused_state_migration,
    apply_project_maintenance_migration,
    apply_review_evidence_migration,
    apply_task_contract_migration,
    connect,
    connect_initialized,
    connect_readonly,
    ensure_project_meta,
    initialize_database,
    project_identity,
    resolve_database_target,
)
from task_governance_tool.tasks import add_task, edit_task  # noqa: E402


def run(*args, cwd):
    return subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git(repo, *args):
    result = run("git", *args, cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def initialize_git_repo(repo):
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "taskgov@example.invalid")
    git(repo, "config", "user.name", "Taskgov Test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-q", "-m", "base")


def write_profile(skill_root, *, enabled=True, thresholds=None):
    config = skill_root / "config" / "effort-advisory.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": PROFILE_ID,
                "enabled": enabled,
                "thresholds": thresholds or {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return load_effort_profile(skill_root)


def initialize_db(db, repo):
    target = resolve_database_target(
        repo=repo,
        db=db,
        script_path=SKILL_ROOT / "scripts" / "taskgov.py",
    )
    initialize_database(target)
    return target


def repo_file_bytes(repo):
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file()
    }


class EffortProfileTests(unittest.TestCase):
    def test_profile_is_strictly_off_unless_fixed_config_is_valid_and_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill"
            skill.mkdir()
            absent = load_effort_profile(skill)
            self.assertFalse(absent.present)
            self.assertFalse(absent.enabled)

            disabled = write_profile(skill, enabled=False)
            self.assertTrue(disabled.valid)
            self.assertFalse(disabled.enabled)

            enabled = write_profile(
                skill,
                thresholds={"changed_files": 0, "handoffs": 2},
            )
            self.assertTrue(enabled.enabled)
            self.assertEqual(enabled.profile_id, PROFILE_ID)
            self.assertEqual(enabled.thresholds, {"changed_files": 0, "handoffs": 2})
            self.assertRegex(enabled.profile_hash or "", r"^sha256:[0-9a-f]{64}$")

            config = skill / "config" / "effort-advisory.json"
            config.write_text(
                '{"schema_version":1,"profile":"informational-v1",'
                '"enabled":true,"thresholds":{"unknown":1}}',
                encoding="utf-8",
            )
            invalid = load_effort_profile(skill)
            self.assertTrue(invalid.present)
            self.assertFalse(invalid.valid)
            self.assertFalse(invalid.enabled)
            self.assertEqual(invalid.diagnostic, "profile_invalid")

    def test_action_selection_uses_only_valid_enabled_nonempty_exceeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill"
            skill.mkdir()
            absent = load_effort_profile(skill)
            disabled = write_profile(skill, enabled=False)
            enabled = write_profile(skill, thresholds={"changed_files": 0})
            invalid = disabled_profile(
                present=True,
                diagnostic="profile_invalid",
            )
            cases = (
                ("absent", absent, [], "continue"),
                ("absent_with_exceeded", absent, ["changed_files"], "continue"),
                ("disabled", disabled, [], "continue"),
                (
                    "disabled_with_exceeded",
                    disabled,
                    ["changed_files"],
                    "continue",
                ),
                ("invalid", invalid, [], "continue"),
                (
                    "invalid_with_exceeded",
                    invalid,
                    ["changed_files"],
                    "continue",
                ),
                ("below_threshold", enabled, [], "continue"),
                (
                    "exceeded",
                    enabled,
                    ["changed_files"],
                    "reconcile_scope",
                ),
            )
            for name, profile, exceeded, expected in cases:
                with self.subTest(name=name):
                    self.assertEqual(
                        effort_service._select_suggested_action(profile, exceeded),
                        expected,
                    )


class EffortMigrationTests(unittest.TestCase):
    def _create_v8(self, db, repo):
        project = project_identity(repo)
        with closing(connect(db)) as connection:
            apply_initial_schema_migration(connection)
            apply_completion_commit_migration(connection)
            ensure_project_meta(connection, project)
            connection.commit()
            apply_paused_state_migration(connection)
            apply_completion_evidence_migration(connection)
            apply_review_evidence_migration(connection)
            apply_git_snapshot_schema_migration(connection)
            apply_handoff_outbox_migration(connection)
            apply_task_contract_migration(connection)
        return project

    def test_v8_to_v9_is_rollback_safe_and_preserves_existing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            project = self._create_v8(db, repo)
            with closing(connect(db)) as connection:
                with connection:
                    add_task(connection, project, title="Preserved task")
                before = {
                    "tasks": int(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
                    "events": int(
                        connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
                    ),
                }

            with closing(connect(db)) as connection:
                with self.assertRaises(StorageError):
                    apply_effort_advisory_migration(
                        connection,
                        fail_stage="after_schema",
                    )
                self.assertEqual(
                    int(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0]
                    ),
                    8,
                )
                self.assertFalse(
                    connection.execute(
                        """
                        SELECT 1 FROM sqlite_master
                         WHERE type='table' AND name='task_effort_bases'
                        """
                    ).fetchone()
                )
                apply_effort_advisory_migration(connection)
                self.assertEqual(
                    int(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0]
                    ),
                    9,
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
                    before["tasks"],
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]),
                    before["events"],
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) FROM task_effort_bases").fetchone()[0]),
                    0,
                )
                apply_project_maintenance_migration(connection)
                self.assertEqual(
                    int(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0]
                    ),
                    10,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            """
                            SELECT enabled_at, backup_interval_minutes,
                                   backup_generations, applied_backup_generations
                              FROM project_maintenance
                             WHERE project_id = ?
                            """,
                            (project.project_id,),
                        ).fetchone()
                    ),
                    (None, None, None, None),
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(SCHEMA_VERSION, 18)


class EffortAdvisoryServiceTests(unittest.TestCase):
    def test_disabled_profile_changes_no_advisory_state_and_calls_no_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "taskgov.sqlite"
            repo = root / "repo"
            target = initialize_db(db, repo)
            with mock.patch(
                "task_governance_tool.effort.capture_git_basis",
                side_effect=AssertionError("disabled profile must not inspect Git"),
            ):
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        added = add_task(connection, target.project, title="Disabled")
                    with connection:
                        edit_task(
                            connection,
                            target.project,
                            added.task["task_id"],
                            status="in_progress",
                        )
            with closing(connect_readonly(db)) as connection:
                result = build_effort_advisory(
                    connection,
                    target.project,
                    added.task["task_id"],
                    disabled_profile(),
                    db_path=db,
                )
                self.assertFalse(result.data["enabled"])
                self.assertEqual(result.data["suggested_action"], "continue")
                self.assertEqual(result.warnings, [])
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) FROM task_effort_bases").fetchone()[0]),
                    0,
                )
                self.assertEqual(
                    int(connection.execute("SELECT COUNT(*) FROM task_effort_activity").fetchone()[0]),
                    0,
                )

    def test_first_in_progress_captures_one_basis_and_failure_never_blocks_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            head = git(repo, "rev-parse", "HEAD")

            with closing(connect_initialized(target)) as connection:
                with connection:
                    initial = add_task(
                        connection,
                        target.project,
                        title="Initial active",
                        status="in_progress",
                        effort_profile=profile,
                    )
                with connection:
                    ready = add_task(connection, target.project, title="Ready")
                with connection:
                    started = edit_task(
                        connection,
                        target.project,
                        ready.task["task_id"],
                        status="in_progress",
                        effort_profile=profile,
                    )
                with connection:
                    edit_task(
                        connection,
                        target.project,
                        ready.task["task_id"],
                        add_note="A later edit must not replace the basis.",
                        effort_profile=profile,
                    )

            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT * FROM task_effort_bases ORDER BY task_id"
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(str(row["basis_head"]) == head for row in rows))
                self.assertTrue(all(int(row["basis_clean"]) == 1 for row in rows))
                ready_rows = [
                    row for row in rows if str(row["task_id"]) == ready.task["task_id"]
                ]
                self.assertEqual(len(ready_rows), 1)

            with mock.patch(
                "task_governance_tool.effort.capture_git_basis",
                return_value=None,
            ):
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        failed_capture = add_task(
                            connection,
                            target.project,
                            title="Capture unavailable",
                            status="in_progress",
                            effort_profile=profile,
                        )
            self.assertEqual(failed_capture.task["status"], "in_progress")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(
                    int(
                        connection.execute(
                            "SELECT COUNT(*) FROM task_effort_bases WHERE task_id = ?",
                            (failed_capture.task["task_id"],),
                        ).fetchone()[0]
                    ),
                    0,
                )
            self.assertEqual(initial.task["status"], "in_progress")
            self.assertEqual(started.task["status"], "in_progress")

    def test_delayed_basis_capture_allows_unrelated_task_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    subject = add_task(
                        connection,
                        target.project,
                        title="Subject",
                        database_target=target,
                    )
                with connection:
                    unrelated = add_task(
                        connection,
                        target.project,
                        title="Unrelated",
                        database_target=target,
                    )

            started = threading.Event()
            release = threading.Event()
            original_capture = effort_service.capture_git_basis

            def delayed_capture(repo_path):
                endpoint = original_capture(repo_path)
                started.set()
                if not release.wait(10):
                    raise AssertionError("Effort preflight was not released")
                return endpoint

            def start_subject():
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        return edit_task(
                            connection,
                            target.project,
                            subject.task["task_id"],
                            status="in_progress",
                            effort_profile=profile,
                            database_target=target,
                        )

            with mock.patch.object(
                effort_service,
                "capture_git_basis",
                side_effect=delayed_capture,
            ):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(start_subject)
                    self.assertTrue(started.wait(10))
                    with closing(connect_initialized(target)) as connection:
                        with connection:
                            unrelated_result = edit_task(
                                connection,
                                target.project,
                                unrelated.task["task_id"],
                                add_note="Progress while Effort observes Git",
                                database_target=target,
                            )
                    release.set()
                    subject_result = future.result(timeout=10)

            self.assertEqual(subject_result.task["status"], "in_progress")
            self.assertEqual(unrelated_result.task["status"], "ready")
            with closing(sqlite3.connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_generation, subject_generation,
                           other_active_at_capture
                      FROM task_effort_bases
                     WHERE task_id = ?
                    """,
                    (subject.task["task_id"],),
                ).fetchone()
                self.assertEqual(basis, (1, 1, 0))
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                        (subject.task["task_id"],),
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_activity_delta_during_basis_capture_marks_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    subject = add_task(
                        connection,
                        target.project,
                        title="Subject",
                        database_target=target,
                    )
                with connection:
                    other = add_task(
                        connection,
                        target.project,
                        title="Other",
                        database_target=target,
                    )

            original_capture = effort_service.capture_git_basis
            injected = False

            def capture_after_other_activity(repo_path):
                nonlocal injected
                if injected:
                    return original_capture(repo_path)
                injected = True
                with closing(connect_initialized(target)) as writer:
                    with writer:
                        edit_task(
                            writer,
                            target.project,
                            other.task["task_id"],
                            status="in_progress",
                            effort_profile=profile,
                            database_target=target,
                        )
                    with writer:
                        edit_task(
                            writer,
                            target.project,
                            other.task["task_id"],
                            status="blocked",
                            blocked_reason="Activity completed during capture",
                            effort_profile=profile,
                            database_target=target,
                        )
                return original_capture(repo_path)

            with mock.patch.object(
                effort_service,
                "capture_git_basis",
                side_effect=capture_after_other_activity,
            ):
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        result = edit_task(
                            connection,
                            target.project,
                            subject.task["task_id"],
                            status="in_progress",
                            effort_profile=profile,
                            database_target=target,
                        )

            self.assertEqual(result.task["status"], "in_progress")
            with closing(sqlite3.connect(db)) as connection:
                basis = connection.execute(
                    """
                    SELECT project_generation, subject_generation,
                           other_active_at_capture
                      FROM task_effort_bases
                     WHERE task_id = ?
                    """,
                    (subject.task["task_id"],),
                ).fetchone()
                self.assertEqual(basis, (3, 1, 1))
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT effort_activity_generation
                          FROM project_meta
                         WHERE project_id = ?
                        """,
                        (target.project.project_id,),
                    ).fetchone()[0],
                    3,
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_implicit_lane_order_uses_locked_post_preflight_maximum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    add_task(
                        connection,
                        target.project,
                        title="Existing lane task",
                        kind="sequential",
                        lane="serial",
                        lane_order=10,
                        database_target=target,
                    )
                with connection:
                    subject = add_task(
                        connection,
                        target.project,
                        title="Move after capture",
                        database_target=target,
                    )

            original_resolve = completion_service.resolve_git_commit
            injected = False

            def resolve_after_lane_growth(repo_path, revision):
                nonlocal injected
                if not injected:
                    injected = True
                    with closing(connect_initialized(target)) as writer:
                        with writer:
                            add_task(
                                writer,
                                target.project,
                                title="Concurrent lane task",
                                kind="sequential",
                                lane="serial",
                                lane_order=20,
                                database_target=target,
                            )
                return original_resolve(repo_path, revision)

            with mock.patch.object(
                completion_service,
                "resolve_git_commit",
                side_effect=resolve_after_lane_growth,
            ):
                with closing(connect_initialized(target)) as connection:
                    with connection:
                        result = edit_task(
                            connection,
                            target.project,
                            subject.task["task_id"],
                            kind="sequential",
                            lane="serial",
                            completion_evidence_kind="git_commit",
                            completion_revision="HEAD",
                            effort_profile=profile,
                            database_target=target,
                        )

            self.assertEqual(result.task["lane_order"], 21)
            with closing(sqlite3.connect(db)) as connection:
                lane_orders = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT lane_order
                          FROM tasks
                         WHERE kind = 'sequential'
                           AND lane = 'serial'
                         ORDER BY lane_order
                        """
                    )
                ]
                self.assertEqual(lane_orders, [10, 20, 21])
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_clean_observation_is_deterministic_warns_without_writing_or_stopping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(
                skill,
                thresholds={
                    "changed_files": 0,
                    "changed_lines": 0,
                    "changed_modules": 0,
                },
            )
            repo = root / "repo"
            initialize_git_repo(repo)
            fsmonitor_marker = root / "fsmonitor-invoked.txt"
            fsmonitor_hook = root / "fsmonitor-side-effect.cmd"
            fsmonitor_hook.write_text(
                f'@echo off\r\n>"{fsmonitor_marker}" echo invoked\r\n',
                encoding="utf-8",
            )
            git(repo, "config", "core.fsmonitor", fsmonitor_hook.as_posix())
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    task = add_task(
                        connection,
                        target.project,
                        title="Measured task",
                        status="in_progress",
                        effort_profile=profile,
                    )
            self.assertFalse(fsmonitor_marker.exists())
            (repo / "feature.txt").write_text("one\ntwo\n", encoding="utf-8")
            git(repo, "-c", "core.fsmonitor=false", "add", "feature.txt")
            git(
                repo,
                "-c",
                "core.fsmonitor=false",
                "commit",
                "-q",
                "-m",
                "feature",
            )

            db_before = db.read_bytes()
            repo_before = repo_file_bytes(repo)
            sidecars_before = sorted(path.name for path in root.glob("taskgov.sqlite-*"))
            with closing(connect_readonly(db)) as connection:
                first = build_effort_advisory(
                    connection,
                    target.project,
                    task.task["task_id"],
                    profile,
                    db_path=db,
                )
            with closing(connect_readonly(db)) as connection:
                second = build_effort_advisory(
                    connection,
                    target.project,
                    task.task["task_id"],
                    profile,
                    db_path=db,
                )
            self.assertEqual(first.data["measurements"]["changed_files"], 1)
            self.assertEqual(first.data["measurements"]["changed_lines"], 2)
            self.assertEqual(first.data["measurements"]["changed_modules"], 1)
            self.assertEqual(first.data["attribution"], "exclusive_task_window")
            self.assertEqual(first.data["exceeded"], [
                "changed_files",
                "changed_lines",
                "changed_modules",
            ])
            self.assertEqual(first.data["suggested_action"], "reconcile_scope")
            self.assertEqual(first.data["warning_key"], WARNING_KEY)
            self.assertEqual(
                first.warnings,
                [
                    {
                        "code": "effort_advisory_threshold_exceeded",
                        "message": "One or more configured effort thresholds were exceeded.",
                        "warning_key": WARNING_KEY,
                        "suggested_action": "reconcile_scope",
                    }
                ],
            )
            self.assertEqual(second.data["suggested_action"], "reconcile_scope")
            self.assertEqual(second.warnings, first.warnings)
            self.assertEqual(
                first.data["measurements"],
                second.data["measurements"],
            )
            self.assertEqual(db.read_bytes(), db_before)
            self.assertEqual(repo_file_bytes(repo), repo_before)
            self.assertEqual(
                sorted(path.name for path in root.glob("taskgov.sqlite-*")),
                sidecars_before,
            )
            self.assertFalse(fsmonitor_marker.exists())

    def test_other_task_overlap_is_remembered_but_subject_activity_is_subtracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill, thresholds={"changed_files": 0})
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    subject = add_task(
                        connection,
                        target.project,
                        title="Subject",
                        status="in_progress",
                        effort_profile=profile,
                    )
                with connection:
                    edit_task(
                        connection,
                        target.project,
                        subject.task["task_id"],
                        status="review_pending",
                        effort_profile=profile,
                    )
                with connection:
                    edit_task(
                        connection,
                        target.project,
                        subject.task["task_id"],
                        status="paused",
                        pause_reason="Short hold",
                        effort_profile=profile,
                    )
                with connection:
                    edit_task(
                        connection,
                        target.project,
                        subject.task["task_id"],
                        status="in_progress",
                        effort_profile=profile,
                    )
                with closing(connect_readonly(db)) as readonly:
                    own_only = build_effort_advisory(
                        readonly,
                        target.project,
                        subject.task["task_id"],
                        profile,
                        db_path=db,
                    )
                self.assertNotIn("active_task_overlap", own_only.data["unknown_reasons"])

                with connection:
                    other = add_task(
                        connection,
                        target.project,
                        title="Other",
                        status="in_progress",
                        effort_profile=disabled_profile(),
                    )
                with connection:
                    edit_task(
                        connection,
                        target.project,
                        other.task["task_id"],
                        status="blocked",
                        blocked_reason="Synthetic hold",
                        effort_profile=disabled_profile(),
                    )
            (repo / "overlap.txt").write_text("overlap\n", encoding="utf-8")
            git(repo, "add", "overlap.txt")
            git(repo, "commit", "-q", "-m", "overlap")
            with closing(connect_readonly(db)) as connection:
                overlapped = build_effort_advisory(
                    connection,
                    target.project,
                    subject.task["task_id"],
                    profile,
                    db_path=db,
                )
            self.assertEqual(overlapped.data["attribution"], "unknown")
            self.assertIn("active_task_overlap", overlapped.data["unknown_reasons"])
            self.assertEqual(overlapped.data["exceeded"], ["changed_files"])
            self.assertEqual(
                overlapped.data["suggested_action"],
                "reconcile_scope",
            )
            self.assertEqual(
                overlapped.warnings,
                [
                    {
                        "code": "effort_advisory_threshold_exceeded",
                        "message": "One or more configured effort thresholds were exceeded.",
                        "warning_key": WARNING_KEY,
                        "suggested_action": "reconcile_scope",
                    }
                ],
            )

    def test_activity_started_during_git_observation_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    subject = add_task(
                        connection,
                        target.project,
                        title="Observed subject",
                        status="in_progress",
                        effort_profile=profile,
                    )

            def observe_after_other_task_starts(repo_path, basis_revision):
                with closing(connect_initialized(target)) as writer:
                    with writer:
                        other = add_task(
                            writer,
                            target.project,
                            title="Concurrent task",
                            status="in_progress",
                            effort_profile=profile,
                        )
                    with writer:
                        edit_task(
                            writer,
                            target.project,
                            other.task["task_id"],
                            status="blocked",
                            blocked_reason="Synthetic completion of overlap",
                            effort_profile=profile,
                        )
                return observe_git_measurements(repo_path, basis_revision)

            with mock.patch(
                "task_governance_tool.effort.observe_git_measurements",
                side_effect=observe_after_other_task_starts,
            ):
                with closing(connect_readonly(db)) as connection:
                    result = build_effort_advisory(
                        connection,
                        target.project,
                        subject.task["task_id"],
                        profile,
                        db_path=db,
                    )
            self.assertEqual(result.data["attribution"], "unknown")
            self.assertIn("active_task_overlap", result.data["unknown_reasons"])
            self.assertEqual(result.data["suggested_action"], "continue")

    def test_invalid_stored_basis_is_not_passed_to_git_or_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    task = add_task(
                        connection,
                        target.project,
                        title="Corrupt basis test",
                        status="in_progress",
                        effort_profile=profile,
                    )
                with connection:
                    connection.execute(
                        """
                        UPDATE task_effort_bases
                           SET basis_head = ?
                         WHERE task_id = ?
                        """,
                        (
                            "--output=must-not-be-created",
                            task.task["task_id"],
                        ),
                    )

            repo_before = repo_file_bytes(repo)
            with mock.patch(
                "task_governance_tool.effort._run_git",
                side_effect=AssertionError("invalid basis must not invoke Git"),
            ):
                with closing(connect_readonly(db)) as connection:
                    result = build_effort_advisory(
                        connection,
                        target.project,
                        task.task["task_id"],
                        profile,
                        db_path=db,
                    )
            self.assertEqual(result.data["basis"]["status"], "invalid")
            self.assertIsNone(result.data["basis"]["revision"])
            self.assertIn("basis_uncertain", result.data["unknown_reasons"])
            self.assertNotIn("--output", json.dumps(result.data, sort_keys=True))
            self.assertEqual(repo_file_bytes(repo), repo_before)

    def test_non_git_dirty_and_missing_basis_are_successful_unknown_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)

            non_git_repo = root / "non-git"
            non_git_db = root / "non-git.sqlite"
            non_git_target = initialize_db(non_git_db, non_git_repo)
            with closing(connect_initialized(non_git_target)) as connection:
                with connection:
                    non_git_task = add_task(
                        connection,
                        non_git_target.project,
                        title="Non Git",
                        status="in_progress",
                        effort_profile=profile,
                    )
            with closing(connect_readonly(non_git_db)) as connection:
                non_git = build_effort_advisory(
                    connection,
                    non_git_target.project,
                    non_git_task.task["task_id"],
                    profile,
                    db_path=non_git_db,
                )
            self.assertEqual(non_git.data["attribution"], "unknown")
            self.assertIn("basis_missing", non_git.data["unknown_reasons"])
            self.assertIn("non_git_repository", non_git.data["unknown_reasons"])

            dirty_repo = root / "dirty"
            initialize_git_repo(dirty_repo)
            (dirty_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            dirty_db = root / "dirty.sqlite"
            dirty_target = initialize_db(dirty_db, dirty_repo)
            with closing(connect_initialized(dirty_target)) as connection:
                with connection:
                    dirty_task = add_task(
                        connection,
                        dirty_target.project,
                        title="Dirty basis",
                        status="in_progress",
                        effort_profile=profile,
                    )
            with closing(connect_readonly(dirty_db)) as connection:
                dirty = build_effort_advisory(
                    connection,
                    dirty_target.project,
                    dirty_task.task["task_id"],
                    profile,
                    db_path=dirty_db,
                )
            self.assertEqual(dirty.data["attribution"], "unknown")
            self.assertIn("basis_dirty", dirty.data["unknown_reasons"])
            self.assertIn("observation_dirty", dirty.data["unknown_reasons"])
            self.assertEqual(dirty.data["suggested_action"], "continue")

    def test_busy_effort_activity_update_rejects_and_rolls_back_task_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    task = add_task(
                        connection,
                        target.project,
                        title="Busy activity",
                        status="ready",
                        effort_profile=profile,
                    )

                busy = sqlite3.OperationalError("sensitive lock detail")
                busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
                with mock.patch.object(
                    effort_service,
                    "_increment_activity",
                    side_effect=busy,
                ):
                    with self.assertRaises(StorageError) as raised:
                        with connection:
                            edit_task(
                                connection,
                                target.project,
                                task.task["task_id"],
                                status="in_progress",
                                effort_profile=profile,
                                database_target=target,
                            )

            self.assertEqual(raised.exception.code, "database_busy")
            self.assertEqual(raised.exception.message, DATABASE_BUSY_MESSAGE)
            with closing(connect_readonly(db)) as connection:
                stored = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (task.task["task_id"],),
                ).fetchone()
                generation = connection.execute(
                    """
                    SELECT effort_activity_generation
                      FROM project_meta
                     WHERE project_id = ?
                    """,
                    (target.project.project_id,),
                ).fetchone()
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task.task["task_id"],),
                ).fetchone()
            self.assertEqual(stored["status"], "ready")
            self.assertEqual(generation["effort_activity_generation"], 0)
            self.assertEqual(event_count[0], 1)

    def test_busy_effort_basis_insert_rejects_and_rolls_back_task_transition(self):
        class BusyBasisConnection:
            def __init__(self, connection):
                self._connection = connection

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def execute(self, statement, parameters=()):
                if "INSERT INTO task_effort_bases" in statement:
                    error = sqlite3.OperationalError("sensitive lock detail")
                    error.sqlite_errorcode = sqlite3.SQLITE_LOCKED
                    raise error
                return self._connection.execute(statement, parameters)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            profile = write_profile(skill)
            repo = root / "repo"
            initialize_git_repo(repo)
            db = root / "taskgov.sqlite"
            target = initialize_db(db, repo)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    task = add_task(
                        connection,
                        target.project,
                        title="Busy basis",
                        status="ready",
                        effort_profile=profile,
                    )

                wrapped = BusyBasisConnection(connection)
                with self.assertRaises(StorageError) as raised:
                    try:
                        edit_task(
                            wrapped,
                            target.project,
                            task.task["task_id"],
                            status="in_progress",
                            effort_profile=profile,
                            database_target=target,
                        )
                    finally:
                        connection.rollback()

            self.assertEqual(raised.exception.code, "database_busy")
            self.assertEqual(raised.exception.message, DATABASE_BUSY_MESSAGE)
            with closing(connect_readonly(db)) as connection:
                stored = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (task.task["task_id"],),
                ).fetchone()
                generation = connection.execute(
                    """
                    SELECT effort_activity_generation
                      FROM project_meta
                     WHERE project_id = ?
                    """,
                    (target.project.project_id,),
                ).fetchone()
                basis_count = connection.execute(
                    "SELECT COUNT(*) FROM task_effort_bases WHERE task_id = ?",
                    (task.task["task_id"],),
                ).fetchone()
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
                    (task.task["task_id"],),
                ).fetchone()
            self.assertEqual(stored["status"], "ready")
            self.assertEqual(generation["effort_activity_generation"], 0)
            self.assertEqual(basis_count[0], 0)
            self.assertEqual(event_count[0], 1)


class EffortAdvisoryCliTests(unittest.TestCase):
    def test_installed_copy_exposes_compact_task_effort_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = make_physical_install(root, git_managed=True)
            copied_skill = install.skill_root
            repo = install.project_root
            git(repo, "config", "user.email", "taskgov@example.invalid")
            git(repo, "config", "user.name", "Taskgov Test")
            write_profile(copied_skill, thresholds={"changed_files": 0})
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "baseline")

            def command(*args):
                return install.run(*args, "--json")

            initialized = command("setup")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            added = command(
                "task",
                "add",
                "--title",
                "CLI effort",
                "--status",
                "in_progress",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            enabled_show = json.loads(
                command("task", "show", task_id, "--read-only").stdout
            )
            self.assertTrue(enabled_show["data"]["effort_advisory_enabled"])
            self.assertEqual(enabled_show["warnings"], [])
            enabled_show_text = install.run(
                "task",
                "show",
                task_id,
                "--read-only",
            ).stdout
            observed = command("task", "effort", task_id, "--read-only")
            self.assertEqual(observed.returncode, 0, observed.stderr)
            payload = json.loads(observed.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "task.effort")
            self.assertTrue(payload["data"]["enabled"])
            self.assertEqual(payload["data"]["suggested_action"], "continue")
            self.assertEqual(payload["data"]["warning_key"], WARNING_KEY)

            change = repo / "effort-change.txt"
            change.write_text("exceeded\n", encoding="utf-8")
            git(repo, "add", "effort-change.txt")
            git(repo, "commit", "-q", "-m", "exceed effort threshold")
            db_before = install.db_path.read_bytes()
            repo_before = repo_file_bytes(repo)
            sidecars_before = sorted(
                path.name for path in install.db_path.parent.glob("taskgov.sqlite-*")
            )
            exceeded = command("task", "effort", task_id, "--read-only")
            self.assertEqual(exceeded.returncode, 0, exceeded.stderr)
            exceeded_payload = json.loads(exceeded.stdout)
            self.assertEqual(
                exceeded_payload["data"]["suggested_action"],
                "reconcile_scope",
            )
            self.assertEqual(
                exceeded_payload["warnings"],
                [
                    {
                        "code": "effort_advisory_threshold_exceeded",
                        "message": "One or more configured effort thresholds were exceeded.",
                        "warning_key": WARNING_KEY,
                        "suggested_action": "reconcile_scope",
                    }
                ],
            )
            self.assertEqual(install.db_path.read_bytes(), db_before)
            self.assertEqual(repo_file_bytes(repo), repo_before)
            self.assertEqual(
                sorted(
                    path.name
                    for path in install.db_path.parent.glob("taskgov.sqlite-*")
                ),
                sidecars_before,
            )
            change.unlink()
            git(repo, "add", "-u", "effort-change.txt")
            git(repo, "commit", "-q", "-m", "restore effort baseline")

            write_profile(copied_skill)
            git(repo, "add", ".agents/skills/task-governance-tool/config")
            git(repo, "commit", "-q", "-m", "change effort thresholds")
            text_observed = install.run(
                "task",
                "effort",
                task_id,
                "--read-only",
            )
            self.assertEqual(text_observed.returncode, 0, text_observed.stderr)
            self.assertIn(
                "Measurements: changed_files=1 changed_lines=2 changed_modules=1 "
                "contract_revisions=0 handoffs=0",
                text_observed.stdout,
            )
            self.assertIn("Thresholds: none", text_observed.stdout)

            config = copied_skill / "config" / "effort-advisory.json"
            config.write_text("{invalid", encoding="utf-8")
            invalid_show = json.loads(
                command("task", "show", task_id, "--read-only").stdout
            )
            self.assertFalse(invalid_show["data"]["effort_advisory_enabled"])
            self.assertEqual(
                invalid_show["warnings"],
                [
                    {
                        "code": "effort_advisory_profile_invalid",
                        "message": "Effort Advisory configuration is invalid; advisory remains disabled.",
                        "suggested_action": "continue",
                    }
                ],
            )

            write_profile(copied_skill, enabled=False)
            disabled_show = json.loads(
                command("task", "show", task_id, "--read-only").stdout
            )
            self.assertFalse(disabled_show["data"]["effort_advisory_enabled"])
            self.assertEqual(disabled_show["warnings"], [])
            disabled_show_text = install.run(
                "task",
                "show",
                task_id,
                "--read-only",
            ).stdout
            self.assertEqual(disabled_show_text, enabled_show_text)

            config.unlink()
            absent_show = json.loads(
                command("task", "show", task_id, "--read-only").stdout
            )
            self.assertFalse(absent_show["data"]["effort_advisory_enabled"])
            self.assertEqual(absent_show["warnings"], [])


if __name__ == "__main__":
    unittest.main()
