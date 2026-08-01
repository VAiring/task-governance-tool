import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
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
)
from task_governance_tool.tasks import STATUSES, VIEWER_TASK_FIELDS, add_task  # noqa: E402
from task_governance_tool.reviews import (  # noqa: E402
    add_review_finding,
    add_review_receipt,
    set_review_target,
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


class ViewerSnapshotTests(unittest.TestCase):
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
            self.assertEqual(snapshot["snapshot_version"], 4)
            self.assertEqual(snapshot["source_schema_version"], 17)
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
            self.assertEqual(result.event_count, 16)
            self.assertEqual(snapshot["tasks"][0]["priority"], "urgent")

            serialized = json.dumps(snapshot)
            self.assertNotIn("review_target_base_revision", serialized)
            self.assertNotIn("target_base_revision", serialized)
            self.assertNotIn("VIEWER_CONTRACT_SCOPE_MUST_STAY_LOCAL", serialized)
            self.assertNotIn("current_contract_revision", serialized)
            self.assertNotIn("task_contract_revisions", serialized)
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
