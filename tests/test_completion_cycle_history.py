import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    StorageError,
    apply_completion_cycle_history_migration,
    connect,
    connect_initialized_readonly,
    connect_snapshot_readonly,
    current_schema_version,
    insert_completion_cycle_locked,
    read_completion_history,
    resolve_database_target,
)
from task_governance_tool.tasks import (  # noqa: E402
    PUBLIC_EVENT_FIELDS,
    add_task,
    create_task_event,
    row_to_event,
    show_task,
)
from task_governance_tool.viewer import build_viewer_snapshot  # noqa: E402
from tests.m14_test_support import (  # noqa: E402
    create_v14_target,
    make_physical_install,
)
from tests.test_project_identity_bindings import (  # noqa: E402
    logical_database_state,
)


MIGRATION_TIME = "2026-07-30T01:02:03Z"
COMPLETION_TIMES = {
    "none": "2026-07-30T02:00:00Z",
    "legacy_unverified": "2026-07-30T02:01:00Z",
    "external_revision": "2026-07-30T02:02:00Z",
}
EXTERNAL_REVISION_500 = "x" * 500
EXPECTED_NEW_INDEXES = {
    "idx_tasks_project_task_identity",
    "idx_review_receipts_completion_cycle_reference",
    "idx_task_completion_cycles_task_ordinal",
    "idx_task_events_completion_cycle",
}
EXPECTED_NEW_TRIGGERS = {
    "trg_task_completion_cycles_no_update",
    "trg_task_completion_cycles_no_delete",
    "trg_tasks_completion_history_coverage_immutable",
    "trg_task_events_completion_cycle_link_immutable",
}
EXPECTED_PUBLIC_EVENT_FIELDS = (
    "task_event_id",
    "task_id",
    "project_id",
    "event_type",
    "summary",
    "created_at",
)
FAILURE_STAGES = (
    "after_columns",
    "after_cycle_schema",
    "after_event_link",
    "after_schema",
    "after_backfill",
    "before_commit",
)


def make_v14_target(root: Path) -> DatabaseTarget:
    repo = root / "repo"
    repo.mkdir()
    target = resolve_database_target(
        repo=repo,
        db=root / "taskgov.sqlite",
        script_path=SCRIPT_PATH,
    )
    create_v14_target(target)
    return target


def seed_v14_tasks(
    target: DatabaseTarget,
    *,
    variants: tuple[str, ...] = (
        "none",
        "legacy_unverified",
        "external_revision",
    ),
    include_ready: bool = True,
) -> dict[str, str]:
    task_ids: dict[str, str] = {}
    with (
        closing(connect(target.db_path)) as connection,
        mock.patch(
            "task_governance_tool.tasks.utc_now",
            return_value="2026-07-30T00:30:00Z",
        ),
    ):
        for variant in variants:
            task_ids[variant] = add_task(
                connection,
                target.project,
                title=f"Legacy {variant} completion",
                verification=(
                    "offline verification"
                    if variant == "external_revision"
                    else ""
                ),
            ).task["task_id"]
        if include_ready:
            task_ids["ready"] = add_task(
                connection,
                target.project,
                title="Current ready task",
            ).task["task_id"]

        for variant in variants:
            if variant == "none":
                evidence = ("none", "", "", 0, 1, "")
            elif variant == "legacy_unverified":
                evidence = (
                    "legacy_unverified",
                    "legacy-revision",
                    "",
                    0,
                    1,
                    "legacy-revision",
                )
            elif variant == "external_revision":
                evidence = (
                    "external_revision",
                    EXTERNAL_REVISION_500,
                    "Approved external source.",
                    1,
                    1,
                    EXTERNAL_REVISION_500,
                )
            else:
                raise AssertionError(f"unsupported fixture variant: {variant}")
            connection.execute(
                """
                UPDATE tasks
                   SET status = 'done',
                       completed_at = ?,
                       updated_at = ?,
                       completion_evidence_kind = ?,
                       completion_evidence_revision = ?,
                       completion_evidence_reason = ?,
                       external_revision_approved = ?,
                       completion_commit_required = ?,
                       completion_commit_hash = ?
                 WHERE project_id = ?
                   AND task_id = ?
                """,
                (
                    COMPLETION_TIMES[variant],
                    COMPLETION_TIMES[variant],
                    *evidence,
                    target.project.project_id,
                    task_ids[variant],
                ),
            )
        connection.commit()
    return task_ids


def migrate_to_v15(target: DatabaseTarget) -> None:
    with (
        closing(connect(target.db_path)) as connection,
        mock.patch(
            "task_governance_tool.storage.utc_now",
            return_value=MIGRATION_TIME,
        ),
    ):
        apply_completion_cycle_history_migration(connection)


def schema_inventory(
    connection: sqlite3.Connection,
) -> dict[str, set[str]]:
    inventory = {
        "table": set(),
        "index": set(),
        "trigger": set(),
        "view": set(),
    }
    for row in connection.execute(
        """
        SELECT type, name
          FROM sqlite_master
         WHERE name NOT LIKE 'sqlite_%'
           AND type IN ('table', 'index', 'trigger', 'view')
        """
    ).fetchall():
        inventory[str(row["type"])].add(str(row["name"]))
    return inventory


def table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def foreign_key_signatures(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[tuple[str, tuple[tuple[str, str], ...], str, str, str]]:
    groups: dict[int, list[sqlite3.Row]] = {}
    for row in connection.execute(
        f'PRAGMA foreign_key_list("{table_name}")'
    ).fetchall():
        groups.setdefault(int(row["id"]), []).append(row)
    signatures = []
    for rows in groups.values():
        ordered = sorted(rows, key=lambda row: int(row["seq"]))
        signatures.append(
            (
                str(ordered[0]["table"]),
                tuple(
                    (str(row["from"]), str(row["to"]))
                    for row in ordered
                ),
                str(ordered[0]["on_update"]),
                str(ordered[0]["on_delete"]),
                str(ordered[0]["match"]),
            )
        )
    return sorted(signatures, key=repr)


class CompletionCycleHistoryTests(unittest.TestCase):
    def test_v14_migration_has_exact_schema_delta_and_honest_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            task_ids = seed_v14_tasks(target)
            with closing(connect(target.db_path)) as connection:
                before_inventory = schema_inventory(connection)
                before_task_columns = table_columns(connection, "tasks")
                before_event_columns = table_columns(connection, "task_events")

            migrate_to_v15(target)

            with closing(connect(target.db_path)) as connection:
                self.assertEqual(current_schema_version(connection), 15)
                after_inventory = schema_inventory(connection)
                self.assertEqual(
                    after_inventory["table"] - before_inventory["table"],
                    {"task_completion_cycles"},
                )
                self.assertEqual(
                    after_inventory["index"] - before_inventory["index"],
                    EXPECTED_NEW_INDEXES,
                )
                self.assertEqual(
                    after_inventory["trigger"] - before_inventory["trigger"],
                    EXPECTED_NEW_TRIGGERS,
                )
                self.assertEqual(
                    after_inventory["view"] - before_inventory["view"],
                    set(),
                )
                self.assertEqual(
                    table_columns(connection, "tasks") - before_task_columns,
                    {"completion_history_coverage"},
                )
                self.assertEqual(
                    table_columns(connection, "task_events")
                    - before_event_columns,
                    {"completion_cycle_id"},
                )
                receipt_prefix = (
                    ("project_id", "project_id"),
                    ("task_id", "task_id"),
                    ("review_target_kind", "target_kind"),
                    ("review_target_value", "target_value"),
                    (
                        "review_target_base_revision",
                        "target_base_revision",
                    ),
                    (
                        "review_target_generation",
                        "target_generation",
                    ),
                )
                self.assertEqual(
                    foreign_key_signatures(
                        connection,
                        "task_completion_cycles",
                    ),
                    sorted(
                        [
                            (
                                "tasks",
                                (
                                    ("project_id", "project_id"),
                                    ("task_id", "task_id"),
                                ),
                                "NO ACTION",
                                "NO ACTION",
                                "NONE",
                            ),
                            (
                                "review_receipts",
                                (
                                    *receipt_prefix,
                                    (
                                        "qualifying_receipt_id_1",
                                        "review_receipt_id",
                                    ),
                                ),
                                "NO ACTION",
                                "NO ACTION",
                                "NONE",
                            ),
                            (
                                "review_receipts",
                                (
                                    *receipt_prefix,
                                    (
                                        "qualifying_receipt_id_2",
                                        "review_receipt_id",
                                    ),
                                ),
                                "NO ACTION",
                                "NO ACTION",
                                "NONE",
                            ),
                        ],
                        key=repr,
                    ),
                )
                self.assertEqual(
                    foreign_key_signatures(connection, "task_events"),
                    sorted(
                        [
                            (
                                "tasks",
                                (("task_id", "task_id"),),
                                "NO ACTION",
                                "NO ACTION",
                                "NONE",
                            ),
                            (
                                "task_completion_cycles",
                                (
                                    (
                                        "completion_cycle_id",
                                        "completion_cycle_id",
                                    ),
                                ),
                                "NO ACTION",
                                "NO ACTION",
                                "NONE",
                            ),
                        ],
                        key=repr,
                    ),
                )
                marker = connection.execute(
                    """
                    SELECT name, applied_at
                      FROM schema_migrations
                     WHERE version = 15
                    """
                ).fetchone()
                self.assertEqual(
                    tuple(marker),
                    ("completion_cycle_history", MIGRATION_TIME),
                )

                coverage = connection.execute(
                    """
                    SELECT task_id, completion_history_coverage
                      FROM tasks
                     ORDER BY task_id COLLATE BINARY
                    """
                ).fetchall()
                self.assertEqual(
                    {str(row["completion_history_coverage"]) for row in coverage},
                    {"legacy_unknown"},
                )
                cycles = {
                    str(row["task_id"]): dict(row)
                    for row in connection.execute(
                        """
                        SELECT *
                          FROM task_completion_cycles
                         ORDER BY task_id COLLATE BINARY
                        """
                    ).fetchall()
                }
                self.assertEqual(
                    set(cycles),
                    {
                        task_ids["none"],
                        task_ids["legacy_unverified"],
                        task_ids["external_revision"],
                    },
                )
                self.assertNotIn(task_ids["ready"], cycles)

                for variant in (
                    "none",
                    "legacy_unverified",
                    "external_revision",
                ):
                    cycle = cycles[task_ids[variant]]
                    self.assertEqual(cycle["saved_cycle_ordinal"], 1)
                    self.assertEqual(cycle["origin"], "legacy_current_done")
                    self.assertEqual(cycle["completeness"], "partial")
                    self.assertEqual(
                        cycle["completed_at"],
                        COMPLETION_TIMES[variant],
                    )
                    self.assertEqual(cycle["recorded_at"], MIGRATION_TIME)
                    self.assertEqual(cycle["contract_revision"], 0)
                    self.assertEqual(cycle["review_tier"], 1)
                    self.assertIsNone(cycle["verification_attestation"])
                    self.assertEqual(cycle["review_target_kind"], "")
                    self.assertEqual(cycle["review_target_generation"], 0)
                    self.assertEqual(cycle["gate_basis_version"], 0)
                    self.assertEqual(cycle["review_basis_kind"], "unknown")
                    for field in (
                        "required_independent_passes",
                        "qualifying_independent_passes",
                        "changes_requested_count",
                        "open_high_count",
                        "open_medium_count",
                        "fresh_review_required_count",
                        "qualifying_receipt_id_1",
                        "qualifying_receipt_id_2",
                    ):
                        self.assertIsNone(cycle[field])

                none_cycle = cycles[task_ids["none"]]
                self.assertEqual(
                    (
                        none_cycle["completion_evidence_kind"],
                        none_cycle["completion_evidence_revision"],
                        none_cycle["completion_evidence_reason"],
                        none_cycle["external_revision_approved"],
                        none_cycle["completion_commit_required"],
                        none_cycle["completion_commit_hash"],
                        none_cycle["verification_expectation"],
                    ),
                    ("none", "", "", 0, 1, "", "unspecified"),
                )
                legacy_cycle = cycles[task_ids["legacy_unverified"]]
                self.assertEqual(
                    (
                        legacy_cycle["completion_evidence_kind"],
                        legacy_cycle["completion_evidence_revision"],
                        legacy_cycle["completion_evidence_reason"],
                        legacy_cycle["external_revision_approved"],
                        legacy_cycle["completion_commit_required"],
                        legacy_cycle["completion_commit_hash"],
                        legacy_cycle["verification_expectation"],
                    ),
                    (
                        "legacy_unverified",
                        "legacy-revision",
                        "",
                        0,
                        1,
                        "legacy-revision",
                        "unspecified",
                    ),
                )
                external_cycle = cycles[task_ids["external_revision"]]
                self.assertEqual(
                    (
                        external_cycle["completion_evidence_kind"],
                        external_cycle["completion_evidence_revision"],
                        external_cycle["completion_evidence_reason"],
                        external_cycle["external_revision_approved"],
                        external_cycle["completion_commit_required"],
                        external_cycle["completion_commit_hash"],
                        external_cycle["verification_expectation"],
                    ),
                    (
                        "external_revision",
                        EXTERNAL_REVISION_500,
                        "Approved external source.",
                        1,
                        1,
                        EXTERNAL_REVISION_500,
                        "specified",
                    ),
                )
                self.assertEqual(
                    len(external_cycle["completion_evidence_revision"]),
                    500,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                          FROM task_events
                         WHERE completion_cycle_id IS NOT NULL
                        """
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertIsNone(
                    connection.execute("PRAGMA foreign_key_check").fetchone()
                )

    def test_migration_rolls_back_every_injected_failure_stage(self):
        for failure_stage in FAILURE_STAGES:
            with (
                self.subTest(failure_stage=failure_stage),
                tempfile.TemporaryDirectory() as tmp,
            ):
                target = make_v14_target(Path(tmp))
                seed_v14_tasks(
                    target,
                    variants=("none",),
                    include_ready=True,
                )
                before = logical_database_state(target.db_path)
                with (
                    closing(connect(target.db_path)) as connection,
                    mock.patch(
                        "task_governance_tool.storage.utc_now",
                        return_value=MIGRATION_TIME,
                    ),
                ):
                    with self.assertRaises(StorageError) as raised:
                        apply_completion_cycle_history_migration(
                            connection,
                            fail_stage=failure_stage,
                        )
                    self.assertEqual(raised.exception.code, "internal_error")
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(current_schema_version(connection), 14)

                self.assertEqual(
                    logical_database_state(target.db_path),
                    before,
                )

    def test_cycle_rows_coverage_and_event_links_are_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            task_ids = seed_v14_tasks(
                target,
                variants=("none",),
                include_ready=False,
            )
            migrate_to_v15(target)

            with closing(connect(target.db_path)) as connection:
                cycle_id = str(
                    connection.execute(
                        """
                        SELECT completion_cycle_id
                          FROM task_completion_cycles
                         WHERE task_id = ?
                        """,
                        (task_ids["none"],),
                    ).fetchone()["completion_cycle_id"]
                )
                event_id = str(
                    connection.execute(
                        """
                        SELECT task_event_id
                          FROM task_events
                         WHERE task_id = ?
                         ORDER BY rowid
                         LIMIT 1
                        """,
                        (task_ids["none"],),
                    ).fetchone()["task_event_id"]
                )
                attempts = (
                    (
                        """
                        UPDATE task_completion_cycles
                           SET recorded_at = recorded_at
                         WHERE completion_cycle_id = ?
                        """,
                        (cycle_id,),
                        "immutable_completion_cycle",
                    ),
                    (
                        """
                        DELETE FROM task_completion_cycles
                         WHERE completion_cycle_id = ?
                        """,
                        (cycle_id,),
                        "immutable_completion_cycle",
                    ),
                    (
                        """
                        UPDATE tasks
                           SET completion_history_coverage = 'complete'
                         WHERE task_id = ?
                        """,
                        (task_ids["none"],),
                        "immutable_completion_history_coverage",
                    ),
                    (
                        """
                        UPDATE task_events
                           SET completion_cycle_id = ?
                         WHERE task_event_id = ?
                        """,
                        (cycle_id, event_id),
                        "immutable_completion_cycle_link",
                    ),
                )
                for statement, parameters, expected_message in attempts:
                    with self.subTest(expected_message=expected_message):
                        with self.assertRaises(
                            sqlite3.IntegrityError
                        ) as raised:
                            connection.execute(statement, parameters)
                        self.assertIn(
                            expected_message,
                            str(raised.exception),
                        )
                        connection.rollback()

                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                          FROM task_completion_cycles
                         WHERE completion_cycle_id = ?
                        """,
                        (cycle_id,),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT completion_history_coverage
                          FROM tasks
                         WHERE task_id = ?
                        """,
                        (task_ids["none"],),
                    ).fetchone()[0],
                    "legacy_unknown",
                )
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT completion_cycle_id
                          FROM task_events
                         WHERE task_event_id = ?
                        """,
                        (event_id,),
                    ).fetchone()[0]
                )

    def test_public_event_contract_never_exposes_completion_cycle_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            task_ids = seed_v14_tasks(
                target,
                variants=(),
                include_ready=True,
            )
            migrate_to_v15(target)

            with closing(connect(target.db_path)) as connection:
                self.assertEqual(
                    PUBLIC_EVENT_FIELDS,
                    EXPECTED_PUBLIC_EVENT_FIELDS,
                )
                raw_event = connection.execute(
                    """
                    SELECT *
                      FROM task_events
                     WHERE task_id = ?
                     ORDER BY rowid
                     LIMIT 1
                    """,
                    (task_ids["ready"],),
                ).fetchone()
                self.assertIn("completion_cycle_id", raw_event.keys())
                projected = row_to_event(raw_event)
                self.assertEqual(
                    tuple(projected),
                    EXPECTED_PUBLIC_EVENT_FIELDS,
                )
                self.assertNotIn("completion_cycle_id", projected)

                created = create_task_event(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_ids["ready"],
                    event_type="task_updated",
                    summary="Recorded a sanitized public event.",
                    created_at="2026-07-30T03:00:00Z",
                )
                self.assertEqual(
                    tuple(created),
                    EXPECTED_PUBLIC_EVENT_FIELDS,
                )
                self.assertNotIn("completion_cycle_id", created)
                connection.commit()

                shown = show_task(
                    connection,
                    target.project,
                    task_ids["ready"],
                )
                self.assertGreaterEqual(len(shown.events), 2)
                for event in shown.events:
                    self.assertEqual(
                        tuple(event),
                        EXPECTED_PUBLIC_EVENT_FIELDS,
                    )
                    self.assertNotIn("completion_cycle_id", event)

    def test_full_history_scan_is_limited_to_setup_reentry_and_doctor(self):
        from task_governance_tool import doctor as doctor_service
        from task_governance_tool import state_resolver as state_resolver_module
        from task_governance_tool import storage as storage_module

        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialized = install.run("setup", "--json")
            self.assertEqual(
                initialized.returncode,
                0,
                initialized.stdout or initialized.stderr,
            )
            target = install.target

            with mock.patch.object(
                storage_module,
                "validate_completion_cycle_storage",
                side_effect=AssertionError("unexpected full history scan"),
            ) as full_validator:
                with closing(connect_initialized_readonly(target)):
                    pass
                with closing(
                    connect_snapshot_readonly(target.db_path)
                ) as connection:
                    snapshot = build_viewer_snapshot(
                        connection,
                        target,
                        generated_at="2026-07-30T03:10:00Z",
                    )
                self.assertEqual(snapshot.snapshot["source_schema_version"], 15)
                full_validator.assert_not_called()

            full_validator = storage_module.validate_completion_cycle_storage

            def validate_inside_snapshot(connection: sqlite3.Connection) -> None:
                self.assertTrue(connection.in_transaction)
                full_validator(connection)

            with mock.patch.object(
                storage_module,
                "validate_completion_cycle_storage",
                side_effect=validate_inside_snapshot,
            ) as setup_validator:
                with closing(connect(target.db_path)) as connection:
                    self.assertFalse(connection.in_transaction)
                    apply_completion_cycle_history_migration(connection)
                    self.assertFalse(connection.in_transaction)
                self.assertGreaterEqual(setup_validator.call_count, 1)

            with mock.patch.object(
                state_resolver_module,
                "validate_completion_cycle_storage",
                wraps=state_resolver_module.validate_completion_cycle_storage,
            ) as doctor_validator:
                result = doctor_service.run_doctor(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                )
                self.assertTrue(result.ok, result.errors)
                self.assertGreaterEqual(doctor_validator.call_count, 1)

    def test_semantically_invalid_cycle_is_rejected_by_reader_setup_and_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            initialized = install.run("setup", "--json")
            self.assertEqual(
                initialized.returncode,
                0,
                initialized.stdout or initialized.stderr,
            )
            target = install.target
            with (
                closing(connect(target.db_path)) as connection,
                mock.patch(
                    "task_governance_tool.tasks.utc_now",
                    return_value="2026-07-30T03:15:00Z",
                ),
            ):
                task_id = add_task(
                    connection,
                    target.project,
                    title="Semantic validation sentinel",
                ).task["task_id"]
                connection.execute(
                    """
                    UPDATE tasks
                       SET status = 'done',
                           completed_at = '2026-07-30T03:16:00Z',
                           updated_at = '2026-07-30T03:16:00Z'
                     WHERE project_id = ?
                       AND task_id = ?
                    """,
                    (target.project.project_id, task_id),
                )
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                insert_completion_cycle_locked(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                    recorded_at="2026-07-30T03:17:00Z",
                )
                connection.commit()

                trigger_sql = str(
                    connection.execute(
                        """
                        SELECT sql
                          FROM sqlite_master
                         WHERE type = 'trigger'
                           AND name =
                             'trg_task_completion_cycles_no_update'
                        """
                    ).fetchone()["sql"]
                )
                connection.execute(
                    """
                    DROP TRIGGER
                      trg_task_completion_cycles_no_update
                    """
                )
                connection.execute(
                    """
                    UPDATE task_completion_cycles
                       SET recorded_at = 'invalid-recorded-at'
                     WHERE task_id = ?
                    """,
                    (task_id,),
                )
                connection.execute(trigger_sql)
                connection.commit()

            with closing(connect_initialized_readonly(target)) as connection:
                with self.assertRaises(StorageError) as reader_error:
                    read_completion_history(
                        connection,
                        project_id=target.project.project_id,
                        task_id=task_id,
                    )
                self.assertEqual(
                    reader_error.exception.code,
                    "completion_history_inconsistent",
                )
                self.assertEqual(
                    reader_error.exception.message,
                    "stored completion history is inconsistent",
                )
                self.assertNotIn(
                    "invalid-recorded-at",
                    str(reader_error.exception),
                )

            before_setup = logical_database_state(target.db_path)
            with closing(connect(target.db_path)) as connection:
                self.assertFalse(connection.in_transaction)
                with self.assertRaises(StorageError) as reentry_error:
                    apply_completion_cycle_history_migration(connection)
                self.assertEqual(
                    reentry_error.exception.code,
                    "project_state_unreadable",
                )
                self.assertFalse(connection.in_transaction)
            self.assertEqual(
                logical_database_state(target.db_path),
                before_setup,
            )

            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 2)
            setup_payload = json.loads(setup.stdout)
            self.assertEqual(
                setup_payload["errors"][0]["code"],
                "project_state_unreadable",
            )
            self.assertNotIn(
                "invalid-recorded-at",
                json.dumps(setup_payload, sort_keys=True),
            )
            self.assertEqual(
                logical_database_state(target.db_path),
                before_setup,
            )

            before_doctor = logical_database_state(target.db_path)
            doctor = install.run("doctor", "--json")
            self.assertEqual(doctor.returncode, 2)
            doctor_payload = json.loads(doctor.stdout)
            self.assertEqual(
                doctor_payload["errors"][0]["code"],
                "project_state_unreadable",
            )
            self.assertNotIn(
                "invalid-recorded-at",
                json.dumps(doctor_payload, sort_keys=True),
            )
            self.assertEqual(
                logical_database_state(target.db_path),
                before_doctor,
            )

    def test_v15_reentry_does_not_reassert_migration_time_cardinality(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_v14_target(Path(tmp))
            task_ids = seed_v14_tasks(
                target,
                variants=("none",),
                include_ready=True,
            )
            migrate_to_v15(target)

            with (
                closing(connect(target.db_path)) as connection,
                mock.patch(
                    "task_governance_tool.tasks.utc_now",
                    return_value="2026-07-30T03:05:00Z",
                ),
            ):
                post_v15_task_id = add_task(
                    connection,
                    target.project,
                    title="Post-v15 capture-less task",
                ).task["task_id"]
                connection.execute(
                    """
                    UPDATE tasks
                       SET status = 'in_progress',
                           completed_at = NULL,
                           completion_evidence_kind = 'none',
                           completion_evidence_revision = '',
                           completion_evidence_reason = '',
                           external_revision_approved = 0,
                           completion_commit_required = 1,
                           completion_commit_hash = ''
                     WHERE task_id = ?
                    """,
                    (task_ids["none"],),
                )
                create_task_event(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_ids["none"],
                    event_type="task_reopened",
                    summary="Reopened after the archived completion.",
                    created_at="2026-07-30T03:06:00Z",
                )
                for task_id, completed_at in (
                    (task_ids["none"], "2026-07-30T03:07:00Z"),
                    (task_ids["ready"], "2026-07-30T03:08:00Z"),
                ):
                    connection.execute(
                        """
                        UPDATE tasks
                           SET status = 'done',
                               completed_at = ?,
                               updated_at = ?,
                               completion_evidence_kind =
                                 'commit_not_required',
                               completion_evidence_revision = '',
                               completion_evidence_reason = '',
                               external_revision_approved = 0,
                               completion_commit_required = 0,
                               completion_commit_hash = ''
                         WHERE task_id = ?
                        """,
                        (completed_at, completed_at, task_id),
                    )
                connection.commit()

                counts = tuple(
                    connection.execute(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM tasks WHERE status = 'done'),
                          (SELECT COUNT(*) FROM task_completion_cycles)
                        """
                    ).fetchone()
                )
                self.assertNotEqual(counts[0], counts[1])
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                          FROM task_completion_cycles
                         WHERE task_id = ?
                        """,
                        (post_v15_task_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT completion_history_coverage
                          FROM tasks
                         WHERE task_id = ?
                        """,
                        (post_v15_task_id,),
                    ).fetchone()[0],
                    "legacy_unknown",
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                          FROM task_events
                         WHERE event_type = 'task_reopened'
                           AND completion_cycle_id IS NULL
                        """
                    ).fetchone()[0],
                    1,
                )

            before_reentry = logical_database_state(target.db_path)
            with closing(connect(target.db_path)) as connection:
                self.assertFalse(connection.in_transaction)
                apply_completion_cycle_history_migration(connection)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(current_schema_version(connection), 15)
            self.assertEqual(
                logical_database_state(target.db_path),
                before_reentry,
            )


if __name__ == "__main__":
    unittest.main()
