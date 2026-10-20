from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from tests.registration_output_support import registration_body, registration_task

from tests.m14_test_support import (
    SOURCE_SKILL_ROOT,
    json_payload,
    make_physical_install,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import maintenance as maintenance_service
from task_governance_tool import viewer_maintenance as viewer_maintenance_service
from task_governance_tool.backup import RoutineBackupResult
from task_governance_tool.maintenance import (
    BACKUP_WARNING_MESSAGES,
    MutationOutcome,
    VIEWER_WARNING_MESSAGES,
    run_post_commit_maintenance,
)
from task_governance_tool.storage import (
    connect_initialized_readonly,
    initialize_database,
    resolve_database_target,
)
from task_governance_tool.viewer_maintenance import ViewerRefreshResult


SCRIPT_PATH = SOURCE_SKILL_ROOT / "scripts" / "taskgov.py"


def make_target(root: Path):
    repo = root / "project"
    repo.mkdir()
    target = resolve_database_target(
        repo=repo,
        db=root / "state" / "taskgov.sqlite",
        script_path=SCRIPT_PATH,
    )
    initialize_database(target)
    return target


def invoke(
    target,
    *args: str,
    maintenance_enabled: bool = True,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    argv = ["--repo", str(target.project.canonical_repo), *args]
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli_service.main(
            argv,
            _target_override=target,
            _maintenance_enabled=maintenance_enabled,
        )
    return code, stdout.getvalue(), stderr.getvalue()


def add_contract_task(target, title: str = "Maintenance source") -> dict:
    code, stdout, stderr = invoke(
        target,
        "task",
        "add",
        "--title",
        title,
        "--status",
        "in_progress",
        "--contract-scope",
        "Initial scope",
        "--contract-acceptance",
        "Initial acceptance",
        "--json",
        maintenance_enabled=False,
    )
    if code != 0:
        raise AssertionError(stderr or stdout)
    return registration_task(json.loads(stdout))


class MaintenanceAdmissionTests(unittest.TestCase):
    """Eligibility is local; a whole-project artifact still needs a global audit."""

    def make_fixture(self, *, enabled=True, current=False):
        from tests.test_viewer_maintenance import make_target, add_viewer_event
        from task_governance_tool import backup, evidence_publication as evidence

        directory = self.enterContext(tempfile.TemporaryDirectory())
        target = make_target(Path(directory), enabled=enabled)
        add_viewer_event(target, "Unrelated retained history")
        if current:
            self.assertEqual(evidence.publish_setup_evidence_projection(target).code, "succeeded")
            self.assertEqual(viewer_maintenance_service.publish_setup_viewer(target).code, "succeeded")
            self.assertEqual(backup.run_routine_backup(target, observed_at="2026-07-27T00:00:00Z").code, "succeeded")
        return target

    def corrupt_note(self, target):
        # Change only a disposable fixture; restore its exact trigger definition.
        with closing(sqlite3.connect(target.db_path)) as connection:
            ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_events_note_body_immutable'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
            connection.execute("UPDATE task_events SET note_body='Invalid retained note' WHERE event_type='task_added'")
            connection.execute(ddl)
            connection.commit()
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def assert_global_rejects(self, target):
        from task_governance_tool.storage import StorageError
        with self.assertRaises(StorageError):
            with closing(connect_initialized_readonly(target)):
                pass

    def test_disabled_and_current_paths_do_not_audit_any_task_history(self):
        from task_governance_tool import storage, backup, evidence_publication as evidence

        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                target = self.make_fixture(enabled=enabled, current=enabled)
                self.corrupt_note(target)
                self.assert_global_rejects(target)
                before = target.db_path.read_bytes()
                with ExitStack() as stack:
                    for owner, name in (
                        (storage, "validate_current_schema21_admitted_rows"),
                        (evidence, "zero_wait_artifact_lock"),
                        (viewer_maintenance_service, "zero_wait_artifact_lock"),
                        (backup, "managed_backup_lock"),
                    ):
                        stack.enter_context(mock.patch.object(owner, name,
                            side_effect=AssertionError("No global audit or lock for ineligible work")))
                    expected = "current" if enabled else "not_opted_in"
                    self.assertEqual(evidence.run_routine_evidence_projection(target).code, expected)
                    self.assertEqual(viewer_maintenance_service.run_routine_viewer_refresh(target).code, expected)
                    self.assertEqual(backup.run_routine_backup(target,
                        observed_at="2026-07-27T00:01:00Z").code, expected)
                self.assertEqual(target.db_path.read_bytes(), before)

    def test_due_or_forced_publication_still_rejects_global_history_fault(self):
        from task_governance_tool import backup, evidence_publication as evidence

        target = self.make_fixture(current=True)
        index = target.resolved_evidence_index.read_bytes()
        viewer = target.resolved_viewer_path.read_bytes()
        backups = sorted(path.name for path in target.resolved_backups_path.iterdir())
        self.corrupt_note(target)
        self.assertEqual(evidence.publish_setup_evidence_projection(target).code, "failed")
        self.assertEqual(viewer_maintenance_service.publish_setup_viewer(target).code, "failed")
        self.assertEqual(backup.run_routine_backup(target,
            observed_at="2026-07-27T00:31:00Z").code, "failed")
        self.assertEqual(target.resolved_evidence_index.read_bytes(), index)
        self.assertEqual(target.resolved_viewer_path.read_bytes(), viewer)
        self.assertEqual(sorted(path.name for path in target.resolved_backups_path.iterdir()), backups)
        self.assert_global_rejects(target)

    def test_evidence_and_viewer_recheck_current_or_disabled_state_under_lock(self):
        from dataclasses import replace
        from task_governance_tool import evidence_publication as evidence

        for service, state_table, generation_field in (
            (evidence, "evidence_projection_state", "published_generation"),
            (viewer_maintenance_service, "viewer_maintenance_state", "rendered_generation"),
        ):
            for disabled in (False, True):
                with self.subTest(service=service.__name__, disabled=disabled):
                    target = self.make_fixture()
                    real_lock = service.zero_wait_artifact_lock
                    @contextmanager
                    def change_before_capture(path):
                        with real_lock(path):
                            with closing(sqlite3.connect(target.db_path)) as connection:
                                if not disabled:
                                    if service is evidence:
                                        connection.execute("UPDATE evidence_projection_state SET published_generation=source_generation, index_digest=?", ("sha256:" + "0" * 64,))
                                    else:
                                        connection.execute(f"UPDATE {state_table} SET {generation_field}=source_generation")
                                connection.commit()
                            self.corrupt_note(target)
                            if disabled:
                                # Opt-in is durable; inject the existing defensive
                                # opt-out observation without violating its DDL.
                                read = service.read_project_maintenance
                                with mock.patch.object(service, "read_project_maintenance",
                                    side_effect=lambda *args: replace(read(*args), enabled_at=None)):
                                    yield
                            else:
                                yield
                    with mock.patch.object(service, "zero_wait_artifact_lock", side_effect=change_before_capture), \
                         mock.patch.object(service, "validate_current_database",
                             side_effect=AssertionError("Ineligible locked capture must not audit")) as audit:
                        result = (evidence.run_routine_evidence_projection(target)
                            if service is evidence else service.run_routine_viewer_refresh(target))
                    self.assertEqual(result.code, "not_opted_in" if disabled else "current")
                    audit.assert_not_called()
                    self.assert_global_rejects(target)

    def test_full_capture_audits_the_same_snapshot_before_building(self):
        from task_governance_tool import evidence_publication as evidence

        target = self.make_fixture()
        for service, builder, capture in (
            (evidence, "capture_evidence_projection_basis",
                lambda: evidence._capture(target, include_basis=True, only_when_due=True)),
            (viewer_maintenance_service, "build_viewer_snapshot",
                lambda: viewer_maintenance_service._capture(target, force=False,
                    generated_at="2026-07-27T00:00:00Z")),
        ):
            observed = []
            original_audit = service.validate_current_database
            original_build = getattr(service, builder)
            def audit(connection, current_target):
                self.assertTrue(connection.in_transaction)
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                observed.append(connection)
                return original_audit(connection, current_target)
            def build(connection, *args, **kwargs):
                self.assertEqual(observed, [connection])
                return original_build(connection, *args, **kwargs)
            with mock.patch.object(service, "validate_current_database", side_effect=audit), \
                 mock.patch.object(service, builder, side_effect=build):
                capture()
            self.assertEqual(len(observed), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                observed[0].execute("SELECT 1")

    def test_physical_status_inspection_still_audits_current_evidence(self):
        from task_governance_tool import evidence_publication as evidence
        from task_governance_tool.storage import StorageError

        target = self.make_fixture(current=True)
        self.corrupt_note(target)
        with self.assertRaises(StorageError):
            evidence.inspect_canonical_evidence_status(target)

    def test_task_and_review_writes_keep_primary_success_with_disabled_maintenance(self):
        from task_governance_tool import storage

        target = self.make_fixture(enabled=False)
        selected = add_contract_task(target, "Selected business operation")
        # Fault is on a different Task; edit and target-set must retain their
        # own validation and must not reintroduce a global audit in maintenance.
        with closing(sqlite3.connect(target.db_path)) as connection:
            ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_events_note_body_immutable'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_task_events_note_body_immutable")
            connection.execute("UPDATE task_events SET note_body='Invalid retained note' WHERE task_id<>? AND event_type='task_added'", (selected["task_id"],))
            connection.execute(ddl)
            connection.commit()
        with mock.patch.object(storage, "validate_current_schema21_admitted_rows",
                side_effect=AssertionError("No unrelated-history audit")):
            for args in (
                ("task", "edit", selected["task_id"], "--title", "Selected title updated"),
                ("review", "target", "set", selected["task_id"], "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64),
            ):
                code, out, err = invoke(target, *args, "--json")
                self.assertEqual(code, 0, out + err)
                self.assertTrue(json.loads(out)["ok"])
                self.assertEqual(json.loads(out)["warnings"], [])
        self.assert_global_rejects(target)


class PostCommitMaintenanceTests(unittest.TestCase):
    def test_warning_messages_are_the_fixed_continuing_contract(self):
        self.assertEqual(
            VIEWER_WARNING_MESSAGES,
            {
                "deferred": (
                    "Viewer refresh was deferred; task result is unchanged"
                ),
                "failed": (
                    "Viewer refresh did not complete; task result is unchanged"
                ),
            },
        )
        self.assertEqual(
            BACKUP_WARNING_MESSAGES,
            {
                "deferred": (
                    "managed backup was deferred; task result is unchanged"
                ),
                "failed": (
                    "managed backup did not complete; task result is unchanged"
                ),
            },
        )

    def test_false_outcome_skips_viewer_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=False,
                        viewer_relevant=True,
                    ),
                )

            self.assertEqual(warnings, [])
            viewer.assert_not_called()
            backup.assert_not_called()

    def test_viewer_runs_before_backup_and_fixed_warnings_keep_that_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            calls: list[str] = []

            def viewer_refresh(*args, **kwargs):
                calls.append("viewer")
                return ViewerRefreshResult(code="deferred", renders=0)

            def routine_backup(*args, **kwargs):
                calls.append("backup")
                return RoutineBackupResult(code="failed", attempted=True)

            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                    side_effect=viewer_refresh,
                ),
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    side_effect=routine_backup,
                ),
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                    observed_at="2026-07-27T00:00:00Z",
                )

            self.assertEqual(calls, ["viewer", "backup"])
            self.assertEqual(
                warnings,
                [
                    {
                        "code": "viewer_refresh_deferred",
                        "message": VIEWER_WARNING_MESSAGES["deferred"],
                    },
                    {
                        "code": "backup_failed",
                        "message": BACKUP_WARNING_MESSAGES["failed"],
                    },
                ],
            )

    def test_viewer_failure_does_not_prevent_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            backup_result = RoutineBackupResult(
                code="current",
                attempted=False,
            )
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                    side_effect=RuntimeError("private Viewer failure"),
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    return_value=backup_result,
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )

            viewer.assert_called_once()
            backup.assert_called_once()
            self.assertEqual(
                warnings,
                [
                    {
                        "code": "viewer_refresh_failed",
                        "message": VIEWER_WARNING_MESSAGES["failed"],
                    }
                ],
            )

    def test_physical_install_invalid_viewer_config_preserves_primary_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stderr)
            last_good = install.viewer_path.read_bytes()
            config = install.skill_root / "config" / "viewer.json"
            config.parent.mkdir()
            config.write_text('{"schema_version":1}', encoding="utf-8")

            added = install.run(
                "task",
                "add",
                "--title",
                "Primary mutation survives invalid Viewer config",
                "--json",
            )

            self.assertEqual(added.returncode, 0, added.stderr)
            payload = json_payload(added)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                registration_body(payload["data"], payload["data"], "task")["title"],
                "Primary mutation survives invalid Viewer config",
            )
            self.assertEqual(
                payload["warnings"],
                [
                    {
                        "code": "viewer_refresh_failed",
                        "message": VIEWER_WARNING_MESSAGES["failed"],
                    }
                ],
            )
            self.assertEqual(install.viewer_path.read_bytes(), last_good)

            shown = install.run(
                "task",
                "show",
                payload["data"]["task"]["task_id"],
                "--read-only",
                "--json",
            )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(
                json_payload(shown)["data"]["task"]["title"],
                "Primary mutation survives invalid Viewer config",
            )
            doctor = install.run("doctor", "--read-only", "--json")
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            viewer = json_payload(doctor)["data"]["components"]["maintenance"][
                "viewer"
            ]
            self.assertTrue(viewer["due"])
            self.assertEqual(viewer["last_outcome"]["code"], "failed")

    def test_backup_failure_preserves_the_viewer_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                    return_value=ViewerRefreshResult(
                        code="deferred",
                        renders=0,
                    ),
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    side_effect=RuntimeError("private backup failure"),
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )

            viewer.assert_called_once()
            backup.assert_called_once()
            self.assertEqual(
                warnings,
                [
                    {
                        "code": "viewer_refresh_deferred",
                        "message": VIEWER_WARNING_MESSAGES["deferred"],
                    },
                    {
                        "code": "backup_failed",
                        "message": BACKUP_WARNING_MESSAGES["failed"],
                    },
                ],
            )

    def test_handoff_outcome_runs_backup_without_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            with (
                mock.patch.object(
                    maintenance_service,
                    "run_routine_viewer_refresh",
                ) as viewer,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    return_value=RoutineBackupResult(
                        code="current",
                        attempted=False,
                    ),
                ) as backup,
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=False,
                    ),
                )

            self.assertEqual(warnings, [])
            viewer.assert_not_called()
            backup.assert_called_once()

    def test_pre_opt_in_viewer_path_does_not_lock_render_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            viewer_directory = target.db_path.parent / "viewer"
            with (
                mock.patch.object(
                    viewer_maintenance_service,
                    "zero_wait_artifact_lock",
                ) as artifact_lock,
                mock.patch.object(
                    viewer_maintenance_service,
                    "build_viewer_snapshot",
                ) as snapshot,
                mock.patch.object(
                    viewer_maintenance_service,
                    "render_viewer_html",
                ) as render,
                mock.patch.object(
                    viewer_maintenance_service,
                    "write_viewer_html",
                ) as write,
                mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    return_value=RoutineBackupResult(
                        code="not_opted_in",
                        attempted=False,
                    ),
                ),
            ):
                warnings = run_post_commit_maintenance(
                    target,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )

            self.assertEqual(warnings, [])
            artifact_lock.assert_not_called()
            snapshot.assert_not_called()
            render.assert_not_called()
            write.assert_not_called()
            self.assertFalse(viewer_directory.exists())

    def test_cli_commits_and_closes_the_business_connection_before_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            opened_connections: list[sqlite3.Connection] = []
            real_connect = cli_service.connect_initialized

            def tracked_connect(database_target, **options):
                self.assertTrue(options["operation_local"])
                connection = real_connect(database_target, **options)
                opened_connections.append(connection)
                return connection

            def inspect_after_commit(database_target, outcome):
                self.assertEqual(
                    outcome,
                    MutationOutcome(
                        state_changed=True,
                        viewer_relevant=True,
                    ),
                )
                self.assertEqual(len(opened_connections), 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    opened_connections[0].execute("SELECT 1")
                with closing(
                    connect_initialized_readonly(database_target)
                ) as connection:
                    row = connection.execute(
                        """
                        SELECT title
                          FROM tasks
                         WHERE project_id = ?
                        """,
                        (database_target.project.project_id,),
                    ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["title"], "Closed before maintenance")
                return []

            with (
                mock.patch.object(
                    cli_service,
                    "connect_initialized",
                    side_effect=tracked_connect,
                ),
                mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                    side_effect=inspect_after_commit,
                ) as coordinator,
            ):
                code, stdout, stderr = invoke(
                    target,
                    "task",
                    "add",
                    "--title",
                    "Closed before maintenance",
                    "--json",
                )

            self.assertEqual(code, 0, stderr or stdout)
            coordinator.assert_called_once()

    def test_json_and_text_warnings_are_fixed_and_primary_success_is_unchanged(self):
        cases = (
            (
                [
                    {
                        "code": "viewer_refresh_deferred",
                        "message": VIEWER_WARNING_MESSAGES["deferred"],
                    },
                    {
                        "code": "backup_failed",
                        "message": BACKUP_WARNING_MESSAGES["failed"],
                    },
                ],
                True,
            ),
            (
                [
                    {
                        "code": "viewer_refresh_failed",
                        "message": VIEWER_WARNING_MESSAGES["failed"],
                    },
                    {
                        "code": "backup_deferred",
                        "message": BACKUP_WARNING_MESSAGES["deferred"],
                    },
                ],
                False,
            ),
        )
        for index, (warnings, json_output) in enumerate(cases):
            with self.subTest(warnings=warnings), tempfile.TemporaryDirectory() as tmp:
                target = make_target(Path(tmp))
                args = [
                    "task",
                    "add",
                    "--title",
                    f"Warning task {index}",
                ]
                if json_output:
                    args.append("--json")
                with mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                    return_value=warnings,
                ) as coordinator:
                    code, stdout, stderr = invoke(target, *args)

                self.assertEqual(code, 0, stderr)
                self.assertEqual(stderr, "")
                self.assertEqual(coordinator.call_count, 1)
                outcome = coordinator.call_args.args[1]
                self.assertEqual(
                    outcome,
                    MutationOutcome(state_changed=True, viewer_relevant=True),
                )
                if json_output:
                    payload = json.loads(stdout)
                    self.assertTrue(payload["ok"])
                    self.assertEqual(payload["command"], "task.add")
                    self.assertEqual(payload["warnings"], warnings)
                    self.assertEqual(
                        registration_body(payload["data"], payload["data"], "task")["title"],
                        f"Warning task {index}",
                    )
                else:
                    self.assertIn("Task added:", stdout)
                    first_position = stdout.find(warnings[0]["message"])
                    second_position = stdout.find(warnings[1]["message"])
                    self.assertGreaterEqual(first_position, 0)
                    self.assertGreater(second_position, first_position)
                    self.assertNotIn("Traceback", stdout)

    def test_read_failure_and_contract_replay_never_call_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            task = add_contract_task(target)

            commands = (
                (
                    "read",
                    (
                        "task",
                        "list",
                        "--read-only",
                        "--json",
                    ),
                    0,
                ),
                (
                    "failure",
                    (
                        "task",
                        "edit",
                        "tg_task_missing",
                        "--add-note",
                        "Must not persist",
                        "--json",
                    ),
                    1,
                ),
                (
                    "contract replay",
                    (
                        "task",
                        "edit",
                        task["task_id"],
                        "--contract-scope",
                        "Initial scope",
                        "--contract-acceptance",
                        "Initial acceptance",
                        "--json",
                    ),
                    0,
                ),
            )
            for label, args, expected_code in commands:
                with self.subTest(case=label), mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                ) as coordinator:
                    code, stdout, stderr = invoke(target, *args)

                self.assertEqual(code, expected_code, stderr)
                coordinator.assert_not_called()
                if label == "contract replay":
                    payload = json.loads(stdout)
                    self.assertEqual(payload["data"]["changed_fields"], [])
                    self.assertIsNone(payload["data"]["event"])
                    self.assertEqual(
                        payload["data"]["contract_write"],
                        {"recorded": False, "revision": 1},
                    )

    def test_handoff_record_withdraw_trigger_but_exact_record_replay_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp))
            task = add_contract_task(target, "Handoff maintenance source")
            record_args = (
                "handoff",
                "record",
                task["task_id"],
                "--summary",
                "Future work outside this task",
                "--json",
            )

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as first_coordinator:
                first_code, first_stdout, first_stderr = invoke(
                    target,
                    *record_args,
                )
            self.assertEqual(first_code, 0, first_stderr)
            first_coordinator.assert_called_once()
            self.assertEqual(
                first_coordinator.call_args.args[1],
                MutationOutcome(state_changed=True, viewer_relevant=False),
            )
            handoff_id = json.loads(first_stdout)["data"]["handoff"]["handoff_id"]

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
            ) as replay_coordinator:
                replay_code, replay_stdout, replay_stderr = invoke(
                    target,
                    *record_args,
                )
            self.assertEqual(replay_code, 0, replay_stderr)
            replay_coordinator.assert_not_called()
            replay = json.loads(replay_stdout)
            self.assertTrue(replay["data"]["local_record"]["replayed"])
            self.assertFalse(replay["data"]["local_record"]["created"])

            with mock.patch.object(
                cli_service,
                "run_post_commit_maintenance",
                return_value=[],
            ) as withdraw_coordinator:
                withdraw_code, _, withdraw_stderr = invoke(
                    target,
                    "handoff",
                    "withdraw",
                    handoff_id,
                    "--reason",
                    "User withdrew the future candidate",
                    "--json",
                )
            self.assertEqual(withdraw_code, 0, withdraw_stderr)
            withdraw_coordinator.assert_called_once()
            self.assertEqual(
                withdraw_coordinator.call_args.args[1],
                MutationOutcome(state_changed=True, viewer_relevant=False),
            )


if __name__ == "__main__":
    unittest.main()
