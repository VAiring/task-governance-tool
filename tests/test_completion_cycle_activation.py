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

from task_governance_tool import state_resolver as state_resolver_service  # noqa: E402
from task_governance_tool import storage as storage_service  # noqa: E402
from task_governance_tool.backup import (  # noqa: E402
    publish_setup_backup,
    restore_managed_backup,
    select_managed_backup_for_recovery,
)
from task_governance_tool.relocation import (  # noqa: E402
    LEGACY_PROJECTS_SOURCE_SCHEMA_MAX,
    RelocationContext,
    RelocationTokenError,
)
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    apply_completion_cycle_capture_activation_migration,
    connect,
    connect_readonly,
    connect_snapshot_readonly,
    current_schema_version,
    initialize_database,
    insert_native_completion_cycle_locked,
    validate_completion_cycle_storage,
)
from task_governance_tool.tasks import add_task  # noqa: E402
from task_governance_tool.viewer import build_viewer_snapshot  # noqa: E402
from tests.test_completion_cycle_history import (  # noqa: E402
    make_v14_target,
    migrate_to_v15,
    schema_inventory,
    seed_v14_tasks,
)
from tests.m14_test_support import make_physical_install  # noqa: E402


ACTIVATION_TIME = "2026-07-30T05:00:00Z"


def make_captureless_done(
    connection,
    *,
    project_id: str,
    task_id: str,
    completed_at: str,
) -> None:
    connection.execute(
        """
        UPDATE tasks
           SET status = 'done',
               completed_at = ?,
               updated_at = ?
         WHERE project_id = ? AND task_id = ?
        """,
        (completed_at, completed_at, project_id, task_id),
    )


def cycle_counts(connection, task_ids: tuple[str, ...]) -> dict[str, int]:
    return {
        task_id: int(
            connection.execute(
                """
                SELECT COUNT(*)
                  FROM task_completion_cycles
                 WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()[0]
        )
        for task_id in task_ids
    }


class CompletionCycleActivationTests(unittest.TestCase):
    def test_marker_only_activation_reconciles_in_binary_order_and_reentry_is_read_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            task_ids = seed_v14_tasks(
                target,
                variants=(
                    "none",
                    "legacy_unverified",
                    "external_revision",
                ),
                include_ready=False,
            )
            migrate_to_v15(target)

            with closing(connect(target.db_path)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET completed_at = '2026-07-30T05:01:00Z'
                     WHERE task_id = ?
                    """,
                    (task_ids["none"],),
                )
                reopened_cycle = connection.execute(
                    """
                    SELECT completion_cycle_id
                      FROM task_completion_cycles
                     WHERE task_id = ?
                    """,
                    (task_ids["legacy_unverified"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id, task_id, project_id, event_type,
                      summary, created_at, completion_cycle_id
                    ) VALUES (
                      'tg_event_activation_reopen',
                      ?, ?, 'task_reopened', 'Staging reopen.',
                      '2026-07-30T05:02:00Z', ?
                    )
                    """,
                    (
                        task_ids["legacy_unverified"],
                        target.project.project_id,
                        reopened_cycle,
                    ),
                )
                absent_ids = []
                for index in range(2):
                    task_id = str(
                        add_task(
                            connection,
                            target.project,
                            title=f"Capture-less done {index}",
                        ).task["task_id"]
                    )
                    make_captureless_done(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                        completed_at=f"2026-07-30T05:0{3 + index}:00Z",
                    )
                    absent_ids.append(task_id)
                connection.commit()
                before_inventory = schema_inventory(connection)
                all_ids = tuple(task_ids.values()) + tuple(absent_ids)
                before_counts = cycle_counts(connection, all_ids)

            reconciled_ids = {
                task_ids["none"],
                task_ids["legacy_unverified"],
                *absent_ids,
            }
            expected_order = sorted(reconciled_ids)
            tokens = [f"{index:016x}" for index in range(1, 5)]
            with (
                closing(connect(target.db_path)) as connection,
                mock.patch(
                    "task_governance_tool.storage.utc_now",
                    return_value=ACTIVATION_TIME,
                ),
                mock.patch(
                    "task_governance_tool.storage.secrets.token_hex",
                    side_effect=tokens,
                ),
            ):
                apply_completion_cycle_capture_activation_migration(
                    connection
                )
                self.assertEqual(current_schema_version(connection), 16)
                marker = connection.execute(
                    """
                    SELECT name
                      FROM schema_migrations
                     WHERE version = 16
                    """
                ).fetchone()[0]
                self.assertEqual(
                    marker,
                    "completion_cycle_capture_activation",
                )
                self.assertEqual(schema_inventory(connection), before_inventory)
                inserted_order = [
                    str(row["task_id"])
                    for row in connection.execute(
                        """
                        SELECT task_id
                          FROM task_completion_cycles
                         WHERE recorded_at = ?
                         ORDER BY rowid
                        """,
                        (ACTIVATION_TIME,),
                    ).fetchall()
                ]
                self.assertEqual(inserted_order, expected_order)
                after_counts = cycle_counts(connection, all_ids)
                for task_id in all_ids:
                    expected_delta = 1 if task_id in reconciled_ids else 0
                    self.assertEqual(
                        after_counts[task_id],
                        before_counts[task_id] + expected_delta,
                    )

                connection.execute(
                    """
                    UPDATE tasks
                       SET completed_at = '2026-07-30T05:09:00Z'
                     WHERE task_id = ?
                    """,
                    (task_ids["external_revision"],),
                )
                connection.commit()
                before_reentry = cycle_counts(connection, all_ids)
                apply_completion_cycle_capture_activation_migration(
                    connection
                )
                self.assertEqual(
                    cycle_counts(connection, all_ids),
                    before_reentry,
                )

    def test_activation_failure_and_ordinal_overflow_roll_back_cycles_and_marker(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            seed_v14_tasks(target, variants=(), include_ready=False)
            migrate_to_v15(target)
            with closing(connect(target.db_path)) as connection:
                task_ids = []
                for index in range(2):
                    task_id = str(
                        add_task(
                            connection,
                            target.project,
                            title=f"Rollback task {index}",
                        ).task["task_id"]
                    )
                    make_captureless_done(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                        completed_at=f"2026-07-30T05:1{index}:00Z",
                    )
                    task_ids.append(task_id)
                connection.commit()
                before = cycle_counts(connection, tuple(task_ids))
                with self.assertRaises(StorageError) as raised:
                    apply_completion_cycle_capture_activation_migration(
                        connection,
                        fail_stage="after_first_reconciliation",
                    )
                self.assertEqual(raised.exception.code, "internal_error")
                self.assertFalse(connection.in_transaction)
                self.assertEqual(current_schema_version(connection), 15)
                self.assertEqual(
                    cycle_counts(connection, tuple(task_ids)),
                    before,
                )

        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            task_ids = seed_v14_tasks(
                target,
                variants=("none",),
                include_ready=False,
            )
            migrate_to_v15(target)
            with closing(connect(target.db_path)) as connection:
                connection.execute(
                    """
                    UPDATE tasks
                       SET completed_at = '2026-07-30T05:20:00Z'
                     WHERE task_id = ?
                    """,
                    (task_ids["none"],),
                )
                connection.commit()
                before = cycle_counts(connection, (task_ids["none"],))
                with (
                    mock.patch.object(
                        storage_service,
                        "SQLITE_INT64_MAX",
                        1,
                    ),
                    self.assertRaises(StorageError) as raised,
                ):
                    apply_completion_cycle_capture_activation_migration(
                        connection
                    )
                self.assertEqual(
                    raised.exception.code,
                    "project_state_unreadable",
                )
                self.assertEqual(current_schema_version(connection), 15)
                self.assertEqual(
                    cycle_counts(connection, (task_ids["none"],)),
                    before,
                )

    def test_native_insert_uses_proposed_tier_and_prefers_independent_pass(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            seed_v14_tasks(target, variants=(), include_ready=False)
            migrate_to_v15(target)
            with closing(connect(target.db_path)) as connection:
                apply_completion_cycle_capture_activation_migration(
                    connection
                )
                task_id = str(
                    add_task(
                        connection,
                        target.project,
                        title="Native capture",
                        review_tier=0,
                    ).task["task_id"]
                )
                fingerprint = "sha256:" + ("a" * 64)
                connection.execute(
                    """
                    UPDATE tasks
                       SET review_target_kind = 'diff_fingerprint',
                           review_target_value = ?,
                           review_target_base_revision = '',
                           review_target_generation = 1
                     WHERE task_id = ?
                    """,
                    (fingerprint, task_id),
                )
                for receipt in (
                    (
                        "receipt-independent",
                        "reviewer-a",
                        "independent",
                        "pass",
                    ),
                    (
                        "receipt-fallback",
                        "fallback",
                        "self_review_fallback",
                        "pass",
                    ),
                ):
                    connection.execute(
                        """
                        INSERT INTO review_receipts(
                          review_receipt_id, task_id, project_id,
                          reviewer_key, receipt_kind, verdict,
                          target_kind, target_value, target_base_revision,
                          target_generation, summary, user_approved,
                          created_at
                        ) VALUES (
                          ?, ?, ?, ?, ?, ?, 'diff_fingerprint', ?, '',
                          1, 'Accepted.', 0, '2026-07-30T05:30:00Z'
                        )
                        """,
                        (
                            receipt[0],
                            task_id,
                            target.project.project_id,
                            receipt[1],
                            receipt[2],
                            receipt[3],
                            fingerprint,
                        ),
                    )
                connection.commit()
                current = dict(
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                )
                proposed = dict(current)
                proposed.update(
                    {
                        "status": "done",
                        "completed_at": "2026-07-30T05:31:00Z",
                        "review_tier": 1,
                        "completion_evidence_kind": "commit_not_required",
                        "completion_evidence_revision": "",
                        "completion_evidence_reason": "",
                        "external_revision_approved": 0,
                        "completion_commit_required": 0,
                        "completion_commit_hash": "",
                    }
                )
                connection.execute("BEGIN IMMEDIATE")
                cycle = insert_native_completion_cycle_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    task_projection=proposed,
                    recorded_at="2026-07-30T05:31:00Z",
                )
                connection.execute(
                    """
                    UPDATE tasks
                       SET status = 'done',
                           completed_at = ?,
                           review_tier = 1,
                           completion_evidence_kind =
                             'commit_not_required',
                           completion_commit_required = 0
                     WHERE task_id = ?
                    """,
                    (proposed["completed_at"], task_id),
                )
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id, task_id, project_id, event_type,
                      summary, created_at, completion_cycle_id
                    ) VALUES (
                      'tg_event_native_tier_change', ?, ?,
                      'review_tier_changed', 'Tier changed and completed.',
                      '2026-07-30T05:31:00Z', ?
                    )
                    """,
                    (
                        task_id,
                        target.project.project_id,
                        cycle.completion_cycle_id,
                    ),
                )
                connection.commit()
                self.assertEqual(cycle.review_tier, 1)
                self.assertEqual(
                    cycle.gate_basis.kind,
                    "independent_passes",
                )
                self.assertEqual(
                    cycle.gate_basis.qualifying_receipt_ids,
                    ("receipt-independent",),
                )
                validate_completion_cycle_storage(connection)

    def test_v16_reentry_marker_v15_binary_viewer_v4_and_relocation_boundaries(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialized = install.run("setup", "--json")
            self.assertEqual(
                initialized.returncode,
                0,
                initialized.stdout or initialized.stderr,
            )
            target = install.target
            with closing(connect(target.db_path)) as connection:
                add_task(
                    connection,
                    target.project,
                    title="Viewer source-16 task",
                )
                connection.commit()
            with closing(
                connect_snapshot_readonly(target.db_path)
            ) as connection:
                snapshot = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at="2026-07-30T05:40:00Z",
                ).snapshot
            self.assertEqual(snapshot["snapshot_version"], 4)
            self.assertEqual(snapshot["source_schema_version"], 16)
            self.assertEqual(
                snapshot["tasks"][0]["completion_history"],
                {
                    "total": 0,
                    "returned_count": 0,
                    "truncated": False,
                    "legacy_history_incomplete": False,
                    "cycles": [],
                },
            )
            self.assertNotIn(
                "completion_history_coverage",
                snapshot["tasks"][0],
            )
            self.assertNotIn(
                "completion_cycle_id",
                str(snapshot),
            )

            with mock.patch.object(
                state_resolver_service,
                "SCHEMA_VERSION",
                15,
            ):
                resolution = state_resolver_service.resolve_project_state(
                    skill_root=install.skill_root,
                    repo=install.project_root,
                )
            self.assertEqual(resolution.error_code, "schema_too_new")

            fixed = RelocationContext(
                project_id=target.project.project_id,
                identity_scheme="uuid_v1",
                binding_generation=1,
                old_path_hash="1" * 64,
                new_path_hash="2" * 64,
                source_layout="fixed_current_v1",
                source_schema_version=16,
            )
            self.assertEqual(fixed.source_schema_version, 16)
            self.assertEqual(LEGACY_PROJECTS_SOURCE_SCHEMA_MAX, 14)
            with self.assertRaises(RelocationTokenError):
                RelocationContext(
                    project_id=target.project.project_id,
                    identity_scheme="uuid_v1",
                    binding_generation=1,
                    old_path_hash="1" * 64,
                    new_path_hash="2" * 64,
                    source_layout="legacy_projects_v1",
                    source_schema_version=15,
                )

            with closing(connect(target.db_path)) as connection:
                connection.execute(
                    """
                    UPDATE schema_migrations
                       SET name = 'wrong_activation_name'
                     WHERE version = 16
                    """
                )
                connection.commit()
                with self.assertRaises(StorageError) as raised:
                    apply_completion_cycle_capture_activation_migration(
                        connection
                    )
                self.assertEqual(
                    raised.exception.code,
                    "project_state_unreadable",
                )

    def test_fixed_v15_backup_restores_then_activates_before_current_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            seed_v14_tasks(
                target,
                variants=("none",),
                include_ready=False,
            )
            migrate_to_v15(target)
            with mock.patch(
                "task_governance_tool.backup.utc_now",
                return_value="2026-07-30T05:50:00Z",
            ):
                publish_setup_backup(target, 2)
            candidate = select_managed_backup_for_recovery(target)
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.schema_version, 15)
            target.db_path.unlink()
            self.assertEqual(
                restore_managed_backup(target, candidate),
                15,
            )
            result = initialize_database(target)
            self.assertEqual(result.migrations_applied, [16])
            self.assertEqual(result.schema_version, 16)
            with closing(connect_readonly(target.db_path)) as connection:
                self.assertEqual(current_schema_version(connection), 16)
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT name
                          FROM schema_migrations
                         WHERE version = 16
                        """
                    ).fetchone()[0],
                    "completion_cycle_capture_activation",
                )


if __name__ == "__main__":
    unittest.main()
