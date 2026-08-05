import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    remove_v18_evidence_ledger_for_test,
    run_taskgov_internal,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    DATABASE_BUSY_MESSAGE,
    SCHEMA_VERSION,
    StorageError,
    apply_completion_cycle_capture_activation_migration,
    apply_completion_cycle_history_migration,
    apply_completion_commit_migration,
    apply_completion_evidence_migration,
    apply_git_snapshot_schema_migration,
    apply_handoff_outbox_migration,
    apply_initial_schema_migration,
    apply_managed_backup_generations_migration,
    apply_paused_state_migration,
    apply_project_maintenance_migration,
    apply_project_identity_bindings_migration,
    apply_review_evidence_migration,
    apply_task_contract_migration,
    apply_task_checkpoints_migration,
    apply_verification_receipts_migration,
    apply_viewer_maintenance_migration,
    apply_effort_advisory_migration,
    connect,
    connect_snapshot_readonly,
    ensure_project_meta,
    ensure_viewer_maintenance_row,
    initial_schema_sql,
    initialize_database,
    resolve_database_target,
    validate_snapshot_database,
    validate_snapshot_database_for_viewer,
)
from task_governance_tool import tasks as tasks_module  # noqa: E402
from task_governance_tool import storage as storage_module  # noqa: E402
from task_governance_tool.tasks import (  # noqa: E402
    STATUSES,
    VIEWER_TASK_FIELDS,
    _list_tasks_for_validated_viewer_snapshot,
    add_task,
    list_tasks_for_viewer,
)
from task_governance_tool.reviews import (  # noqa: E402
    add_review_finding,
    add_review_receipt,
    set_review_target,
)
from task_governance_tool.verification_receipts import (  # noqa: E402
    add_verification_receipt,
)
from task_governance_tool import viewer as viewer_module  # noqa: E402
from task_governance_tool.viewer import build_viewer_snapshot  # noqa: E402


SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"


def initialized_target(tmp: str):
    db = Path(tmp) / "taskgov.sqlite"
    repo = Path(tmp) / "repo"
    target = resolve_database_target(repo=repo, db=db, script_path=SCRIPT_PATH)
    initialize_database(target)
    return target


def table_count(db: Path, table: str) -> int:
    with closing(sqlite3.connect(db)) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def sqlite_lock_error(error_code: int) -> sqlite3.OperationalError:
    error = sqlite3.OperationalError(
        "Authorization: Bearer sensitive Viewer lock detail"
    )
    error.sqlite_errorcode = error_code
    return error


class ViewerSnapshotTests(unittest.TestCase):
    def test_v18_viewer_batch_preserves_database_busy(self):
        project_id = "project-a"
        task_id = "tg_task_" + "1" * 16
        savepoint_name = "taskgov_viewer_batch_" + "a" * 32

        for error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            with self.subTest(stage="issuance", error_code=error_code):
                connection = mock.Mock()
                connection.execute.side_effect = sqlite_lock_error(error_code)
                target = mock.Mock()
                target.project.project_id = project_id
                with (
                    mock.patch.object(
                        storage_module,
                        "_validate_snapshot_database_state",
                        return_value=(18, ({"task_id": task_id},)),
                    ),
                    self.assertRaises(StorageError) as failure,
                ):
                    validate_snapshot_database_for_viewer(connection, target)

                self.assertEqual(failure.exception.code, "database_busy")
                self.assertEqual(
                    failure.exception.message,
                    DATABASE_BUSY_MESSAGE,
                )
                self.assertNotIn("Authorization", str(failure.exception))

            for stage in ("consume_read", "consume_release"):
                with self.subTest(stage=stage, error_code=error_code):
                    connection = mock.Mock()
                    connection.in_transaction = True
                    batch = storage_module._register_validated_viewer_task_batch(
                        connection,
                        project_id=project_id,
                        source_schema_version=18,
                        task_ids=(task_id,),
                        task_count=1,
                        data_version=1,
                        savepoint_name=savepoint_name,
                    )
                    if stage == "consume_read":
                        connection.execute.side_effect = sqlite_lock_error(
                            error_code
                        )
                    else:
                        def execute(sql):
                            if sql == "PRAGMA query_only":
                                cursor = mock.Mock()
                                cursor.fetchone.return_value = (1,)
                                return cursor
                            if sql == "PRAGMA data_version":
                                cursor = mock.Mock()
                                cursor.fetchone.return_value = (1,)
                                return cursor
                            raise sqlite_lock_error(error_code)

                        connection.execute.side_effect = execute

                    with self.assertRaises(StorageError) as failure:
                        storage_module._consume_validated_viewer_task_batch(
                            connection,
                            batch,
                            project_id=project_id,
                            source_schema_version=18,
                            task_rows=[
                                {
                                    "project_id": project_id,
                                    "task_id": task_id,
                                }
                            ],
                        )

                    self.assertEqual(failure.exception.code, "database_busy")
                    self.assertEqual(
                        failure.exception.message,
                        DATABASE_BUSY_MESSAGE,
                    )
                    self.assertNotIn("Authorization", str(failure.exception))

    def test_v18_snapshot_reuses_exact_ledger_validated_task_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    add_task(
                        connection,
                        target.project,
                        title="Later Viewer task",
                        priority="low",
                    )
                    add_task(
                        connection,
                        target.project,
                        title="First Viewer task",
                        priority="urgent",
                    )

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                self.assertEqual(
                    validate_snapshot_database(connection, target),
                    SCHEMA_VERSION,
                )

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                ordinary = list_tasks_for_viewer(
                    connection,
                    target.project,
                    source_schema_version=SCHEMA_VERSION,
                )

            validated_batch_sizes = []
            real_validator = tasks_module.validate_stored_task_rows

            def counted_validator(rows, *args, **kwargs):
                validated_batch_sizes.append(len(rows))
                return real_validator(rows, *args, **kwargs)

            with (
                closing(connect_snapshot_readonly(target.db_path)) as connection,
                mock.patch.object(
                    tasks_module,
                    "validate_stored_task_rows",
                    side_effect=counted_validator,
                ),
                mock.patch.object(
                    tasks_module,
                    "_consume_validated_viewer_task_batch",
                    wraps=tasks_module._consume_validated_viewer_task_batch,
                ) as consume_batch,
            ):
                snapshot = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at="2026-08-04T00:00:00Z",
                )

            projected_tasks = []
            for task in snapshot.snapshot["tasks"]:
                projected = dict(task)
                projected.pop("completion_history")
                projected_tasks.append(projected)
            self.assertEqual(projected_tasks, ordinary.tasks)
            self.assertEqual(snapshot.event_count, ordinary.event_count)
            self.assertEqual(validated_batch_sizes, [2])
            consume_batch.assert_called_once()

    def test_v18_validated_viewer_task_batch_rejects_wrong_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    add_task(
                        connection,
                        target.project,
                        title="Viewer proof binding",
                    )
            other_target = resolve_database_target(
                repo=Path(tmp) / "other-repo",
                db=target.db_path,
                script_path=SCRIPT_PATH,
            )

            with (
                closing(connect_snapshot_readonly(target.db_path)) as source,
                closing(connect_snapshot_readonly(target.db_path)) as other,
            ):
                validation = validate_snapshot_database_for_viewer(source, target)
                with self.assertRaises(StorageError) as wrong_connection:
                    _list_tasks_for_validated_viewer_snapshot(
                        other,
                        target.project,
                        validation.validated_task_batch,
                    )
                self.assertEqual(
                    wrong_connection.exception.code,
                    "project_state_unreadable",
                )

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                validation = validate_snapshot_database_for_viewer(connection, target)
                batch = validation.validated_task_batch
                issuance = storage_module._VIEWER_TASK_BATCH_ISSUANCES[id(batch)][1]
                connection.execute(f"RELEASE SAVEPOINT {issuance.savepoint_name}")
                connection.execute(f"SAVEPOINT {issuance.savepoint_name}")
                reconstructed = type(batch)()
                with self.assertRaises(StorageError) as reconstructed_token:
                    _list_tasks_for_validated_viewer_snapshot(
                        connection,
                        target.project,
                        reconstructed,
                    )
                self.assertEqual(
                    reconstructed_token.exception.code,
                    "project_state_unreadable",
                )

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                validation = validate_snapshot_database_for_viewer(connection, target)
                with self.assertRaises(StorageError) as wrong_project:
                    _list_tasks_for_validated_viewer_snapshot(
                        connection,
                        other_target.project,
                        validation.validated_task_batch,
                    )
                self.assertEqual(
                    wrong_project.exception.code,
                    "project_state_unreadable",
                )
                with self.assertRaises(StorageError) as burned_token:
                    _list_tasks_for_validated_viewer_snapshot(
                        connection,
                        target.project,
                        validation.validated_task_batch,
                    )
                self.assertEqual(
                    burned_token.exception.code,
                    "project_state_unreadable",
                )

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                validation = validate_snapshot_database_for_viewer(connection, target)
                _list_tasks_for_validated_viewer_snapshot(
                    connection,
                    target.project,
                    validation.validated_task_batch,
                )
                with self.assertRaises(StorageError) as reused_batch:
                    _list_tasks_for_validated_viewer_snapshot(
                        connection,
                        target.project,
                        validation.validated_task_batch,
                    )
                self.assertEqual(
                    reused_batch.exception.code,
                    "project_state_unreadable",
                )

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                validation = validate_snapshot_database_for_viewer(connection, target)
                batch = validation.validated_task_batch
                issuance = storage_module._VIEWER_TASK_BATCH_ISSUANCES[id(batch)][1]
                savepoint_name = issuance.savepoint_name
                connection.rollback()
                with closing(connect(target.db_path)) as writer:
                    writer.execute(
                        "UPDATE tasks SET title = ? WHERE project_id = ?",
                        (
                            "Authorization: Bearer unvalidated-private-value",
                            target.project.project_id,
                        ),
                    )
                    writer.commit()
                connection.execute("BEGIN")
                connection.execute(f"SAVEPOINT {savepoint_name}")
                with (
                    mock.patch.object(
                        tasks_module,
                        "row_to_viewer_task",
                        side_effect=AssertionError(
                            "revoked proof projected changed Task bytes"
                        ),
                    ) as project_task,
                    self.assertRaises(StorageError) as wrong_transaction,
                ):
                    _list_tasks_for_validated_viewer_snapshot(
                        connection,
                        target.project,
                        batch,
                    )
                self.assertEqual(
                    wrong_transaction.exception.code,
                    "project_state_unreadable",
                )
                project_task.assert_not_called()

            with tempfile.TemporaryDirectory() as invalid_tmp:
                invalid_target = initialized_target(invalid_tmp)
                with (
                    closing(
                        connect_snapshot_readonly(invalid_target.db_path)
                    ) as connection,
                    mock.patch.object(
                        storage_module.secrets,
                        "token_hex",
                        return_value="invalid;savepoint",
                    ),
                    self.assertRaises(StorageError) as invalid_savepoint,
                ):
                    validate_snapshot_database_for_viewer(
                        connection,
                        invalid_target,
                    )
                self.assertEqual(
                    invalid_savepoint.exception.code,
                    "project_state_unreadable",
                )

    def test_schema_v17_native_receipt_history_uses_legacy_subject_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    task = add_task(
                        connection,
                        target.project,
                        title="Schema 17 Viewer receipt history",
                        status="in_progress",
                        review_tier=0,
                        verification="Focused offline verification",
                    ).task

            with closing(connect(target.db_path)) as connection:
                set_review_target(
                    connection,
                    target.project,
                    task["task_id"],
                    kind="diff_fingerprint",
                    revision="sha256:" + ("d" * 64),
                )
                add_review_receipt(
                    connection,
                    target.project,
                    task["task_id"],
                    reviewer="mechanical-review",
                    kind="not_required",
                    verdict="not_required",
                    summary="Mechanical Viewer compatibility fixture",
                )
                connection.commit()

            with closing(connect(target.db_path)) as connection:
                add_verification_receipt(
                    connection,
                    target.project,
                    task["task_id"],
                    result="pass",
                    duration_ms=1,
                    scope_coverage="full",
                    expected_target_generation=1,
                )
                connection.commit()

            completed = run_taskgov_internal(
                "task",
                "complete",
                task["task_id"],
                "--repo",
                str(target.project.canonical_repo),
                "--db",
                str(target.db_path),
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
                "--json",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(connect(target.db_path)) as connection:
                remove_v18_evidence_ledger_for_test(connection)
                connection.commit()

            validated_batch_sizes = []
            real_validator = tasks_module.validate_stored_task_rows

            def counted_validator(rows, *args, **kwargs):
                validated_batch_sizes.append(len(rows))
                return real_validator(rows, *args, **kwargs)

            with (
                closing(connect_snapshot_readonly(target.db_path)) as connection,
                mock.patch.object(
                    tasks_module,
                    "validate_stored_task_rows",
                    side_effect=counted_validator,
                ),
                mock.patch.object(
                    tasks_module,
                    "_consume_validated_viewer_task_batch",
                    wraps=tasks_module._consume_validated_viewer_task_batch,
                ) as consume_batch,
            ):
                result = build_viewer_snapshot(connection, target)

            self.assertEqual(result.snapshot["source_schema_version"], 17)
            self.assertEqual(validated_batch_sizes, [1])
            consume_batch.assert_not_called()
            history = result.snapshot["tasks"][0]["completion_history"]
            self.assertEqual(history["returned_count"], 1)
            self.assertIs(
                history["cycles"][0]["verification_attestation"],
                True,
            )

    def test_snapshot_v4_reads_schema_v5_through_v17_with_honest_history(self):
        for source_version in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17):
            with self.subTest(source_version=source_version), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "taskgov.sqlite"
                repo = Path(tmp) / "repo"
                target = resolve_database_target(
                    repo=repo,
                    db=db,
                    script_path=SCRIPT_PATH,
                )
                with closing(connect(db)) as connection:
                    apply_initial_schema_migration(connection)
                    apply_completion_commit_migration(connection)
                    connection.commit()
                    apply_paused_state_migration(connection)
                    apply_completion_evidence_migration(connection)
                    apply_review_evidence_migration(connection)
                    if source_version >= 6:
                        apply_git_snapshot_schema_migration(connection)
                    if source_version >= 7:
                        apply_handoff_outbox_migration(connection)
                    if source_version >= 8:
                        apply_task_contract_migration(connection)
                    if source_version >= 9:
                        apply_effort_advisory_migration(connection)
                    if source_version >= 10:
                        apply_project_maintenance_migration(connection)
                    if source_version >= 11:
                        apply_managed_backup_generations_migration(connection)
                    if source_version >= 12:
                        apply_task_checkpoints_migration(connection)
                    if source_version >= 13:
                        apply_viewer_maintenance_migration(connection)
                    if source_version >= 14:
                        apply_project_identity_bindings_migration(connection)
                    if source_version >= 15:
                        apply_completion_cycle_history_migration(connection)
                    if source_version >= 16:
                        apply_completion_cycle_capture_activation_migration(
                            connection
                        )
                    if source_version >= 17:
                        apply_verification_receipts_migration(connection)
                    with connection:
                        ensure_project_meta(connection, target.project)
                        if source_version >= 13:
                            ensure_viewer_maintenance_row(
                                connection,
                                target.project.project_id,
                            )
                        added = add_task(
                            connection,
                            target.project,
                            title=f"Schema {source_version} viewer task",
                            **(
                                {
                                    "contract_scope": "SCHEMA_8_PRIVATE_SCOPE",
                                    "contract_acceptance": "Viewer compatibility passes",
                                }
                                if source_version >= 8
                                else {}
                            ),
                        )
                        if source_version >= 12:
                            connection.execute(
                                """
                                INSERT INTO task_checkpoints(
                                  checkpoint_id, task_id, project_id,
                                  contract_revision, summary, next_action,
                                  unresolved_risks_json, created_at
                                ) VALUES (
                                  'tg_checkpoint_viewer_private', ?, ?, 0,
                                  'VIEWER_CHECKPOINT_PRIVATE',
                                  'VIEWER_CHECKPOINT_NEXT_PRIVATE',
                                  '["VIEWER_CHECKPOINT_RISK_PRIVATE"]',
                                  '2026-07-26T00:00:00Z'
                                )
                                """,
                                (
                                    added.task["task_id"],
                                    target.project.project_id,
                                ),
                            )

                with closing(connect_snapshot_readonly(db)) as connection:
                    result = build_viewer_snapshot(
                        connection,
                        target,
                        generated_at="2026-07-26T00:00:00Z",
                    )

                self.assertEqual(result.snapshot["snapshot_version"], 4)
                self.assertEqual(
                    result.snapshot["source_schema_version"],
                    source_version,
                )
                self.assertEqual(result.task_count, 1)
                projected = result.snapshot["tasks"][0]
                self.assertEqual(
                    set(projected),
                    set(VIEWER_TASK_FIELDS)
                    | {"events", "review_evidence", "completion_history"},
                )
                self.assertEqual(
                    set(projected["completion_history"]),
                    {
                        "total",
                        "returned_count",
                        "truncated",
                        "legacy_history_incomplete",
                        "cycles",
                    },
                )
                self.assertEqual(projected["completion_history"]["total"], 0)
                self.assertEqual(
                    projected["completion_history"]["returned_count"],
                    0,
                )
                self.assertFalse(projected["completion_history"]["truncated"])
                self.assertEqual(
                    projected["completion_history"]["legacy_history_incomplete"],
                    source_version <= 15,
                )
                self.assertEqual(projected["completion_history"]["cycles"], [])
                serialized = json.dumps(result.snapshot)
                self.assertNotIn("handoff", serialized.lower())
                self.assertNotIn("review_target_base_revision", serialized)
                self.assertNotIn("SCHEMA_8_PRIVATE_SCOPE", serialized)
                self.assertNotIn("project_maintenance", serialized)
                self.assertNotIn("managed_backup_generations", serialized)
                self.assertNotIn("backup_interval", serialized)
                self.assertNotIn("task_checkpoints", serialized)
                self.assertNotIn("latest_checkpoint", serialized)
                self.assertNotIn("VIEWER_CHECKPOINT", serialized)
                self.assertNotIn("viewer_maintenance_state", serialized)
                self.assertNotIn("source_generation", serialized)
                self.assertNotIn("rendered_generation", serialized)
                self.assertNotIn("completion_history_coverage", serialized)
                self.assertNotIn("completion_cycle_id", serialized)

    def test_snapshot_v4_projects_stored_cycle_with_exact_public_allow_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "taskgov.sqlite"
            target = resolve_database_target(
                repo=Path(tmp) / "repo",
                db=db,
                script_path=SCRIPT_PATH,
            )
            with closing(connect(db)) as connection:
                apply_initial_schema_migration(connection)
                apply_completion_commit_migration(connection)
                connection.commit()
                apply_paused_state_migration(connection)
                apply_completion_evidence_migration(connection)
                apply_review_evidence_migration(connection)
                apply_git_snapshot_schema_migration(connection)
                apply_handoff_outbox_migration(connection)
                apply_task_contract_migration(connection)
                apply_effort_advisory_migration(connection)
                apply_project_maintenance_migration(connection)
                apply_managed_backup_generations_migration(connection)
                apply_task_checkpoints_migration(connection)
                apply_viewer_maintenance_migration(connection)
                apply_project_identity_bindings_migration(connection)
                with connection:
                    ensure_project_meta(connection, target.project)
                    ensure_viewer_maintenance_row(
                        connection,
                        target.project.project_id,
                    )
                    task = add_task(
                        connection,
                        target.project,
                        title="Legacy completed Viewer task",
                    ).task
                    connection.execute(
                        """
                        UPDATE tasks
                           SET status = 'done',
                               completed_at = '2026-07-27T00:00:00Z'
                         WHERE task_id = ?
                        """,
                        (task["task_id"],),
                    )
                apply_completion_cycle_history_migration(connection)
                apply_completion_cycle_capture_activation_migration(connection)

            with closing(connect_snapshot_readonly(db)) as connection:
                snapshot = build_viewer_snapshot(connection, target).snapshot

            history = snapshot["tasks"][0]["completion_history"]
            self.assertEqual(
                {
                    key: history[key]
                    for key in (
                        "total",
                        "returned_count",
                        "truncated",
                        "legacy_history_incomplete",
                    )
                },
                {
                    "total": 1,
                    "returned_count": 1,
                    "truncated": False,
                    "legacy_history_incomplete": True,
                },
            )
            self.assertEqual(len(history["cycles"]), 1)
            cycle = history["cycles"][0]
            self.assertEqual(
                set(cycle),
                {
                    "completion_cycle_id",
                    "saved_cycle_ordinal",
                    "origin",
                    "completeness",
                    "completed_at",
                    "contract_revision",
                    "review_tier",
                    "verification_expectation",
                    "verification_attestation",
                    "completion_evidence",
                    "review_target",
                    "gate_basis",
                },
            )
            self.assertEqual(cycle["origin"], "legacy_current_done")
            self.assertEqual(cycle["completeness"], "partial")
            self.assertIsNone(cycle["verification_attestation"])
            self.assertEqual(
                set(cycle["completion_evidence"]),
                {
                    "kind",
                    "revision",
                    "reason",
                    "external_revision_approved",
                    "completion_commit_required",
                    "completion_commit_hash",
                },
            )
            self.assertEqual(
                set(cycle["review_target"]),
                {"kind", "value", "base_revision", "generation"},
            )
            self.assertEqual(
                set(cycle["gate_basis"]),
                {
                    "version",
                    "kind",
                    "required_independent_passes",
                    "qualifying_independent_passes",
                    "changes_requested",
                    "open_high",
                    "open_medium",
                    "fresh_review_required",
                    "qualifying_receipt_ids",
                },
            )
            self.assertEqual(cycle["gate_basis"]["version"], 0)
            self.assertEqual(cycle["gate_basis"]["qualifying_receipt_ids"], [])
            for field in (
                "required_independent_passes",
                "qualifying_independent_passes",
                "changes_requested",
                "open_high",
                "open_medium",
                "fresh_review_required",
            ):
                self.assertIsNone(cycle["gate_basis"][field])
            serialized = json.dumps(history, ensure_ascii=False)
            self.assertNotIn("recorded_at", serialized)
            self.assertNotIn("project_id", serialized)
            self.assertNotIn("task_id", serialized)

    def test_snapshot_v4_uses_one_bounded_batch_history_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    first = add_task(
                        connection,
                        target.project,
                        title="First Viewer task",
                    ).task
                    second = add_task(
                        connection,
                        target.project,
                        title="Second Viewer task",
                    ).task

            real_reader = viewer_module.read_completion_histories_for_tasks
            with mock.patch.object(
                viewer_module,
                "read_completion_histories_for_tasks",
                wraps=real_reader,
            ) as reader:
                with closing(connect_snapshot_readonly(target.db_path)) as connection:
                    snapshot = build_viewer_snapshot(connection, target).snapshot

            self.assertEqual(reader.call_count, 1)
            self.assertEqual(
                set(reader.call_args.kwargs["task_ids"]),
                {first["task_id"], second["task_id"]},
            )
            self.assertEqual(len(snapshot["tasks"]), 2)
            self.assertTrue(
                all("completion_history" in task for task in snapshot["tasks"])
            )

    def test_history_batch_preserves_task_501_without_n_plus_one_reads(self):
        task_ids = tuple(f"tg_task_{index:04d}" for index in range(501))

        def fake_reader(
            connection,
            *,
            project_id,
            task_ids,
        ):
            self.assertEqual(connection, "connection")
            self.assertEqual(project_id, "project")
            return {task_id: task_id for task_id in task_ids}

        with mock.patch.object(
            viewer_module,
            "read_completion_histories_for_tasks",
            side_effect=fake_reader,
        ) as reader:
            histories = viewer_module._read_viewer_completion_histories(
                "connection",
                project_id="project",
                task_ids=task_ids,
            )

        self.assertEqual(tuple(histories), task_ids)
        self.assertEqual(histories[task_ids[-1]], task_ids[-1])
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(
            [len(call.kwargs["task_ids"]) for call in reader.call_args_list],
            [500, 1],
        )

    def test_snapshot_projects_all_statuses_show_fields_and_bounded_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            tasks = {}
            with closing(connect(target.db_path)) as connection:
                with connection:
                    for index, status in enumerate(STATUSES):
                        initial_status = (
                            "ready" if status == "done" else
                            "in_progress" if status == "paused" else
                            status
                        )
                        contract_input = (
                            {
                                "contract_scope": "VIEWER_CONTRACT_SCOPE_MUST_STAY_LOCAL",
                                "contract_acceptance": "Focused Contract checks pass",
                            }
                            if status == "ready"
                            else {}
                        )
                        tasks[status] = add_task(
                            connection,
                            target.project,
                            title=f"{status} task",
                            status=initial_status,
                            blocked_reason=("Waiting for input" if status == "blocked" else ""),
                            priority=("urgent" if index == 0 else "normal"),
                            verification=(
                                "Run the bounded Viewer verification"
                                if status == "in_progress"
                                else ""
                            ),
                            **contract_input,
                        ).task
                        if status == "done":
                            # Viewer projection deliberately seeds a historical row;
                            # completion-transition gates are tested at the CLI boundary.
                            connection.execute(
                                """
                                UPDATE tasks
                                   SET status = 'done',
                                       completed_at = '2026-07-17T00:00:00Z'
                                 WHERE task_id = ?
                                """,
                                (tasks[status]["task_id"],),
                            )
                            tasks[status]["status"] = "done"
                            tasks[status]["completed_at"] = "2026-07-17T00:00:00Z"
                        elif status == "paused":
                            connection.execute(
                                """
                                UPDATE tasks
                                   SET status = 'paused',
                                       pause_reason = 'Viewer pause reason'
                                 WHERE task_id = ?
                                """,
                                (tasks[status]["task_id"],),
                            )
                            tasks[status]["status"] = "paused"
                            tasks[status]["pause_reason"] = "Viewer pause reason"
                    selected = tasks["ready"]
                    verification_task = tasks["in_progress"]
                    set_review_target(
                        connection,
                        target.project,
                        verification_task["task_id"],
                        kind="diff_fingerprint",
                        revision="sha256:" + ("b" * 64),
                    )
                    connection.execute(
                        """
                        UPDATE tasks
                           SET completion_commit_hash = ?,
                               completion_evidence_kind = 'legacy_unverified',
                               completion_evidence_revision = ?
                         WHERE task_id = ?
                        """,
                        ("abc123viewer", "abc123viewer", selected["task_id"]),
                    )
                    set_review_target(
                        connection,
                        target.project,
                        selected["task_id"],
                        kind="diff_fingerprint",
                        revision="sha256:" + ("a" * 64),
                    )
                    receipt = add_review_receipt(
                        connection,
                        target.project,
                        selected["task_id"],
                        reviewer="viewer-reviewer",
                        kind="independent",
                        verdict="pass",
                        summary="Viewer evidence projection accepted",
                        reviewer_class="human",
                        model_state="not_applicable",
                        skill_state="not_applicable",
                        review_profiles=["general"],
                        review_lenses=["correctness"],
                        context_relation="external_context",
                        review_methods=["review_packet_inspection"],
                    )
                    add_review_finding(
                        connection,
                        target.project,
                        selected["task_id"],
                        receipt_id=receipt.receipt["review_receipt_id"],
                        severity="low",
                        summary="Non-blocking presentation note",
                    )
                    for index in range(12):
                        connection.execute(
                            """
                            INSERT INTO task_events(
                              task_event_id, task_id, project_id,
                              event_type, summary, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"tg_event_viewer_{index:02d}",
                                selected["task_id"],
                                target.project.project_id,
                                "note_added",
                                f"Viewer event {index:02d}",
                                "2999-01-01T00:00:00Z",
                            ),
                        )

            with closing(connect(target.db_path)) as connection:
                add_verification_receipt(
                    connection,
                    target.project,
                    verification_task["task_id"],
                    result="pass",
                    duration_ms=25,
                    scope_coverage="full",
                    expected_target_generation=1,
                )
                connection.commit()

            task_events_before = table_count(target.db_path, "task_events")
            tool_events_before = table_count(target.db_path, "tool_events")
            generated_at = "2026-07-17T00:00:00Z"
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                result = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at=generated_at,
                )

            snapshot = result.snapshot
            self.assertEqual(
                set(snapshot),
                {
                    "snapshot_version",
                    "generated_at",
                    "project",
                    "source_schema_version",
                    "counts",
                    "tasks",
                },
            )
            self.assertEqual(snapshot["snapshot_version"], 4)
            self.assertEqual(snapshot["source_schema_version"], 19)
            self.assertEqual(snapshot["generated_at"], generated_at)
            self.assertEqual(snapshot["source_schema_version"], SCHEMA_VERSION)
            self.assertEqual(snapshot["project"], {
                "project_id": target.project.project_id,
                "display_name": target.project.display_name,
            })
            self.assertEqual(snapshot["counts"]["total"], len(STATUSES))
            for status in STATUSES:
                self.assertEqual(snapshot["counts"][status], 1)

            self.assertEqual(result.task_count, len(STATUSES))
            ready = next(task for task in snapshot["tasks"] if task["status"] == "ready")
            self.assertEqual(
                set(ready),
                set(VIEWER_TASK_FIELDS)
                | {"events", "review_evidence", "completion_history"},
            )
            self.assertEqual(
                ready["completion_history"],
                {
                    "total": 0,
                    "returned_count": 0,
                    "truncated": False,
                    "legacy_history_incomplete": False,
                    "cycles": [],
                },
            )
            self.assertEqual(ready["completion_commit_required"], 1)
            self.assertEqual(ready["completion_commit_hash"], "abc123viewer")
            self.assertEqual(ready["completion_evidence_kind"], "legacy_unverified")
            self.assertEqual(ready["completion_evidence_revision"], "abc123viewer")
            self.assertEqual(ready["review_target_kind"], "diff_fingerprint")
            self.assertEqual(ready["review_target_generation"], 1)
            self.assertEqual(
                ready["review_evidence"]["gate"]["qualifying_independent_passes"],
                1,
            )
            self.assertEqual(ready["review_evidence"]["counts"]["open_low"], 1)
            self.assertEqual(
                ready["review_evidence"]["counts"][
                    "changes_requested_current_generation"
                ],
                0,
            )
            self.assertLessEqual(len(ready["review_evidence"]["recent_receipts"]), 10)
            self.assertLessEqual(len(ready["review_evidence"]["recent_findings"]), 10)
            self.assertEqual(len(ready["events"]), 10)
            self.assertEqual(
                [event["summary"] for event in ready["events"]],
                [f"Viewer event {index:02d}" for index in range(11, 1, -1)],
            )
            self.assertEqual(result.event_count, 17)
            self.assertEqual(snapshot["tasks"][0]["priority"], "urgent")

            serialized = json.dumps(snapshot)
            self.assertNotIn("review_target_base_revision", serialized)
            self.assertNotIn("target_base_revision", serialized)
            self.assertNotIn("VIEWER_CONTRACT_SCOPE_MUST_STAY_LOCAL", serialized)
            self.assertNotIn("current_contract_revision", serialized)
            self.assertNotIn("task_contract_revisions", serialized)
            self.assertNotIn("review_provenance", serialized)
            self.assertNotIn("authority_snapshot_id", serialized)
            self.assertNotIn("acceptance_criterion_id", serialized)
            self.assertNotIn("verification_criterion_id", serialized)
            self.assertNotIn("artifact_manifest", serialized)
            self.assertNotIn("evidence_reference", serialized)
            self.assertNotIn("verification_subject", serialized)
            self.assertNotIn(str(target.project.canonical_repo), serialized)
            self.assertNotIn(str(target.db_path), serialized)
            self.assertEqual(table_count(target.db_path, "task_events"), task_events_before)
            self.assertEqual(table_count(target.db_path, "tool_events"), tool_events_before)
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(target.db_path) + suffix).exists())

    def test_snapshot_connection_is_query_only_and_revalidates_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                self.assertTrue(connection.in_transaction)
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "UPDATE project_meta SET display_name = 'changed'"
                    )

            other_target = resolve_database_target(
                repo=Path(tmp) / "other-repo",
                db=target.db_path,
                script_path=SCRIPT_PATH,
            )
            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                with self.assertRaises(StorageError) as mismatch:
                    build_viewer_snapshot(connection, other_target)
            self.assertEqual(mismatch.exception.code, "project_mismatch")

    def test_snapshot_rejects_migration_mismatch_inside_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    connection.execute("DELETE FROM schema_migrations WHERE version = 6")

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                with self.assertRaises(StorageError) as migration:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(migration.exception.code, "migration_required")

    def test_snapshot_rejects_current_version_database_missing_show_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = resolve_database_target(
                repo=Path(tmp) / "repo",
                db=Path(tmp) / "taskgov.sqlite",
                script_path=SCRIPT_PATH,
            )
            incomplete_schema = initial_schema_sql().replace(
                "  description TEXT NOT NULL DEFAULT '',\n",
                "",
            )
            with closing(connect(target.db_path)) as connection:
                with connection:
                    connection.executescript(incomplete_schema)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (1, 'incomplete_initial_schema', '2026-07-17T00:00:00Z')
                        """
                    )
                    apply_completion_commit_migration(connection)
                    ensure_project_meta(connection, target.project)

            with closing(connect_snapshot_readonly(target.db_path)) as connection:
                with self.assertRaises(StorageError) as migration:
                    build_viewer_snapshot(connection, target)

            self.assertEqual(migration.exception.code, "migration_required")

    def test_snapshot_maps_sqlite_read_failure_to_storage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            connection = connect_snapshot_readonly(target.db_path)
            connection.close()

            with self.assertRaises(StorageError) as failure:
                build_viewer_snapshot(connection, target)

            self.assertEqual(failure.exception.code, "internal_error")
            self.assertEqual(failure.exception.message, "could not read viewer snapshot")

    def test_active_wal_is_rejected_before_snapshot_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as writer:
                writer.execute("PRAGMA journal_mode = WAL")
                writer.execute("BEGIN IMMEDIATE")
                writer.execute(
                    "UPDATE project_meta SET display_name = display_name"
                )
                with self.assertRaises(StorageError) as failure:
                    connect_snapshot_readonly(target.db_path)
                self.assertEqual(
                    (failure.exception.code, failure.exception.message),
                    (
                        "unsupported_journal_mode",
                        "task database uses unsupported WAL journal mode",
                    ),
                )
                writer.rollback()

    def test_open_snapshot_stays_consistent_when_writer_cannot_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(connect(target.db_path)) as connection:
                with connection:
                    task = add_task(connection, target.project, title="Before writer").task

            with closing(connect_snapshot_readonly(target.db_path)) as snapshot_connection:
                first = build_viewer_snapshot(snapshot_connection, target).snapshot
                with closing(sqlite3.connect(target.db_path, timeout=0.01)) as writer:
                    writer.execute("PRAGMA busy_timeout = 1")
                    writer.execute(
                        "UPDATE tasks SET title = ? WHERE task_id = ?",
                        ("After writer", task["task_id"]),
                    )
                    with self.assertRaises(sqlite3.OperationalError):
                        writer.commit()
                    writer.rollback()
                second = build_viewer_snapshot(snapshot_connection, target).snapshot

            self.assertEqual(first["tasks"][0]["title"], "Before writer")
            self.assertEqual(second["tasks"][0]["title"], "Before writer")


if __name__ == "__main__":
    unittest.main()
