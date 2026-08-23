import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
FIXTURE_PATH = ROOT / "fixtures" / "task-status-migration-v2" / "tasks.json"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    apply_completion_commit_migration,
    apply_completion_evidence_migration,
    apply_effort_advisory_migration,
    apply_git_snapshot_schema_migration,
    apply_handoff_outbox_migration,
    apply_initial_schema_migration,
    apply_managed_backup_generations_migration,
    apply_paused_state_migration,
    apply_project_maintenance_migration,
    apply_project_identity_bindings_migration,
    apply_review_evidence_migration,
    apply_task_checkpoints_migration,
    apply_task_contract_migration,
    apply_viewer_maintenance_migration,
    connect,
    ensure_project_meta,
    project_identity,
)
from task_governance_tool.backup import (  # noqa: E402
    select_managed_backup_for_recovery,
)
try:  # noqa: E402
    from m14_test_support import json_payload, make_physical_install
except ModuleNotFoundError:  # noqa: E402
    from tests.m14_test_support import json_payload, make_physical_install


MIGRATION_SETUP_WRITES = [
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "evidence_projection_publish",
    "viewer_publish",
]
RECOVERY_MIGRATION_SETUP_WRITES = [
    "database_restore",
    *MIGRATION_SETUP_WRITES,
]


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def create_realistic_v2_database(db_path: Path, repo: Path, fixture: dict):
    project = project_identity(repo)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        apply_initial_schema_migration(connection)
        apply_completion_commit_migration(connection)
        ensure_project_meta(connection, project)
        for task_index, item in enumerate(fixture["tasks"], start=1):
            completed_at = (
                f"2026-07-{task_index:02d}T12:00:00Z"
                if item["status"] == "done"
                else None
            )
            connection.execute(
                """
                INSERT INTO tasks(
                  task_id, project_id, title, description, kind, lane, lane_order,
                  priority, status, blocked_reason, review_tier, verification,
                  tags, created_at, updated_at, completed_at,
                  completion_commit_required, completion_commit_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["task_id"], project.project_id, item["title"],
                    "Synthetic migration acceptance task", "sequential", "MIGRATION",
                    item["lane_order"], "normal", item["status"], "", 2,
                    "synthetic offline verification", "migration,synthetic",
                    "2026-07-01T00:00:00Z", "2026-07-20T00:00:00Z", completed_at,
                    1, item["completion_commit_hash"],
                ),
            )
            for event_index in range(1, item["event_count"] + 1):
                global_index = sum(
                    prior["event_count"] for prior in fixture["tasks"][: task_index - 1]
                ) + event_index
                event_type = (
                    "task_added" if event_index == 1 else
                    "note_added" if event_index % 3 else
                    "task_updated"
                )
                connection.execute(
                    """
                    INSERT INTO task_events(
                      task_event_id, task_id, project_id, event_type, summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"tg_event_migration_{global_index:03d}", item["task_id"],
                        project.project_id, event_type,
                        f"Synthetic sanitized event {global_index:03d}",
                        f"2026-07-{task_index:02d}T12:{event_index:02d}:00Z",
                    ),
                )
        for index, item in enumerate(fixture["tool_events"], start=1):
            connection.execute(
                """
                INSERT INTO tool_events(
                  tool_event_id, project_id, command, status, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["tool_event_id"], project.project_id, item["command"],
                    item["status"], item["summary"],
                    f"2026-07-20T13:0{index}:00Z",
                ),
            )
        connection.commit()
    return project


def create_realistic_review_database(
    db_path: Path,
    repo: Path,
    fixture: dict,
    *,
    schema_version: int,
):
    if schema_version not in {5, 6, 12, 13}:
        raise ValueError("schema_version must be 5, 6, 12, or 13")
    project = create_realistic_v2_database(db_path, repo, fixture)
    with closing(connect(db_path)) as connection:
        apply_paused_state_migration(connection)
        apply_completion_evidence_migration(connection)
        apply_review_evidence_migration(connection)
        task_id = fixture["tasks"][0]["task_id"]
        fingerprint = "sha256:" + ("b" * 64)
        connection.execute(
            """
            UPDATE tasks
               SET review_target_kind = 'diff_fingerprint',
                   review_target_value = ?,
                   review_target_generation = 1
             WHERE task_id = ?
            """,
            (fingerprint, task_id),
        )
        connection.execute(
            """
            INSERT INTO review_receipts(
              review_receipt_id, task_id, project_id, reviewer_key,
              receipt_kind, verdict, target_kind, target_value,
              target_generation, summary, user_approved, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tg_receipt_migration_001",
                task_id,
                project.project_id,
                "migration-reviewer",
                "independent",
                "pass",
                "diff_fingerprint",
                fingerprint,
                1,
                "Sanitized migration review",
                0,
                "2026-07-20T14:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO review_findings(
              review_finding_id, review_receipt_id, severity, status,
              summary, resolution_summary, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tg_finding_migration_001",
                "tg_receipt_migration_001",
                "low",
                "resolved",
                "Sanitized migration finding",
                "Verified as resolved",
                "2026-07-20T14:01:00Z",
                "2026-07-20T14:02:00Z",
            ),
        )
        connection.commit()
        if schema_version >= 6:
            apply_git_snapshot_schema_migration(connection)
        if schema_version >= 12:
            apply_handoff_outbox_migration(connection)
            apply_task_contract_migration(connection)
            apply_effort_advisory_migration(connection)
            apply_project_maintenance_migration(connection)
            apply_managed_backup_generations_migration(connection)
            apply_task_checkpoints_migration(connection)
        if schema_version >= 13:
            apply_viewer_maintenance_migration(connection)
    return project


def post_v5_durable_projection(connection: sqlite3.Connection) -> dict:
    return {
        "project_meta": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT project_id, canonical_path_hash, display_name, created_at
                  FROM project_meta ORDER BY project_id
                """
            )
        ],
        "tasks": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT task_id, project_id, status, completion_commit_hash,
                       completion_evidence_kind, completion_evidence_revision,
                       review_target_kind, review_target_value,
                       review_target_generation
                  FROM tasks ORDER BY task_id
                """
            )
        ],
        "task_events": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT task_event_id, task_id, project_id, event_type, summary,
                       created_at
                  FROM task_events ORDER BY task_event_id
                """
            )
        ],
        "tool_events": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT tool_event_id, project_id, command, status, summary,
                       created_at
                  FROM tool_events ORDER BY tool_event_id
                """
            )
        ],
        "review_receipts": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT review_receipt_id, task_id, project_id, reviewer_key,
                       receipt_kind, verdict, target_kind, target_value,
                       target_generation, summary, user_approved, created_at
                  FROM review_receipts ORDER BY review_receipt_id
                """
            )
        ],
        "review_findings": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT review_finding_id, review_receipt_id, severity, status,
                       summary, resolution_summary, created_at, resolved_at
                  FROM review_findings ORDER BY review_finding_id
                """
            )
        ],
    }


def durable_projection(connection: sqlite3.Connection) -> dict:
    return {
        "tasks": [
            tuple(row) for row in connection.execute(
                """
                SELECT task_id, status, completion_commit_hash,
                       completion_evidence_kind, completion_evidence_revision,
                       review_target_base_revision
                  FROM tasks ORDER BY task_id
                """
            )
        ],
        "task_events": [
            tuple(row) for row in connection.execute(
                "SELECT task_event_id, task_id FROM task_events ORDER BY task_event_id"
            )
        ],
        "tool_events": [
            tuple(row) for row in connection.execute(
                "SELECT tool_event_id, command FROM tool_events ORDER BY tool_event_id"
            )
        ],
    }


def nonidentity_table_projection(connection: sqlite3.Connection) -> dict:
    excluded = {
        "project_meta",
        "project_path_binding_history",
        "schema_migrations",
    }
    tables = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
              FROM sqlite_master
             WHERE type = 'table'
               AND name NOT LIKE 'sqlite_%'
             ORDER BY name
            """
        ).fetchall()
        if str(row[0]) not in excluded
    ]
    return {
        table: tuple(
            sorted(
                (
                    tuple(row)
                    for row in connection.execute(
                        f'SELECT * FROM "{table}"'
                    ).fetchall()
                ),
                key=repr,
            )
        )
        for table in tables
    }


def legacy_v2_projection(connection: sqlite3.Connection) -> dict:
    """Return v2 durable content, excluding db-init's operational meta timestamp."""
    return {
        "project_meta": [
            tuple(row) for row in connection.execute(
                """
                SELECT project_id, canonical_path_hash, display_name, created_at
                  FROM project_meta ORDER BY project_id
                """
            )
        ],
        "tasks": [
            tuple(row) for row in connection.execute(
                """
                SELECT task_id, project_id, title, description, kind, lane,
                       lane_order, priority, status, blocked_reason, review_tier,
                       verification, tags, created_at, updated_at, completed_at,
                       completion_commit_required, completion_commit_hash
                  FROM tasks ORDER BY task_id
                """
            )
        ],
        "task_events": [
            tuple(row) for row in connection.execute(
                """
                SELECT task_event_id, task_id, project_id, event_type, summary,
                       created_at
                  FROM task_events ORDER BY task_event_id
                """
            )
        ],
        "tool_events": [
            tuple(row) for row in connection.execute(
                """
                SELECT tool_event_id, project_id, command, status, summary,
                       created_at
                  FROM tool_events ORDER BY tool_event_id
                """
            )
        ],
    }


class RealisticMigrationAcceptanceTests(unittest.TestCase):
    def assert_single_seeded_managed_backup(
        self,
        connection: sqlite3.Connection,
        db_path: Path,
    ) -> None:
        self.assertEqual(
            connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0],
            20,
        )
        generations = connection.execute(
            """
            SELECT generation_id, published_at, publication_retention
              FROM managed_backup_generations
             ORDER BY published_at, generation_id
            """
        ).fetchall()
        self.assertEqual(len(generations), 1)
        generation = tuple(generations[0])
        maintenance = tuple(
            connection.execute(
                """
                SELECT latest_backup_generation_id, backup_last_success_at,
                       applied_backup_generations, backup_last_outcome_code,
                       backup_last_outcome_at
                  FROM project_maintenance
                """
            ).fetchone()
        )
        self.assertEqual(
            maintenance,
            (*generation, "succeeded", generation[1]),
        )
        self.assertEqual(
            tuple(
                connection.execute(
                    """
                    SELECT source_generation, rendered_generation,
                           last_outcome_code
                      FROM viewer_maintenance_state
                    """
                ).fetchone()
            ),
            (0, 0, "succeeded"),
        )
        artifacts = sorted(
            (db_path.parent / "backups").glob(
                "taskgov-backup-v1_*.sqlite"
            )
        )
        self.assertEqual(len(artifacts), 1)
        self.assertIn(
            (
                f"_{generation[0].removeprefix('tg_backup_')}"
                f"_r{generation[2]}.sqlite"
            ),
            artifacts[0].name,
        )

    def test_v2_fixture_setup_migrates_to_current_without_losing_observed_state(self):
        fixture = load_fixture()
        self.assertEqual(fixture["schema_version"], 2)
        self.assertEqual(len(fixture["tasks"]), 12)
        self.assertEqual(sum(item["event_count"] for item in fixture["tasks"]), 191)
        self.assertEqual(
            sum(bool(item["completion_commit_hash"]) for item in fixture["tasks"]),
            9,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = make_physical_install(root)
            repo = install.project_root
            db_path = install.db_path
            db_path.parent.mkdir(parents=True)
            project = create_realistic_v2_database(db_path, repo, fixture)
            with closing(sqlite3.connect(db_path)) as connection:
                before_migration = legacy_v2_projection(connection)
                before_project_updated_at = connection.execute(
                    "SELECT updated_at FROM project_meta WHERE project_id = ?",
                    (project.project_id,),
                ).fetchone()[0]

            migrated = install.run("setup", "--json")

            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            payload = json_payload(migrated)
            self.assertEqual(payload["project_id"], project.project_id)
            self.assertEqual(payload["data"]["schema_from"], 2)
            self.assertEqual(payload["data"]["schema_to"], 20)
            self.assertEqual(
                payload["data"]["completed_writes"],
                MIGRATION_SETUP_WRITES,
            )
            self.assertEqual(payload["data"]["evidence_status"], "published")

            with closing(sqlite3.connect(db_path)) as connection:
                connection.row_factory = sqlite3.Row
                task_rows = connection.execute(
                    "SELECT * FROM tasks ORDER BY task_id"
                ).fetchall()
                event_ids = [
                    row[0] for row in connection.execute(
                        "SELECT task_event_id FROM task_events ORDER BY task_event_id"
                    )
                ]
                tool_event_ids = [
                    row[0] for row in connection.execute(
                        "SELECT tool_event_id FROM tool_events ORDER BY tool_event_id"
                    )
                ]
                project_row = connection.execute(
                    "SELECT * FROM project_meta WHERE project_id = ?", (project.project_id,)
                ).fetchone()

                self.assertEqual(legacy_v2_projection(connection), before_migration)
                self.assertEqual(len(task_rows), 12)
                self.assertEqual(
                    [row["task_id"] for row in task_rows],
                    sorted(item["task_id"] for item in fixture["tasks"]),
                )
                self.assertEqual(len(event_ids), 191)
                self.assertEqual(
                    event_ids,
                    [f"tg_event_migration_{index:03d}" for index in range(1, 192)],
                )
                self.assertEqual(
                    tool_event_ids,
                    [item["tool_event_id"] for item in fixture["tool_events"]],
                )
                self.assertEqual(project_row["canonical_path_hash"], project.canonical_path_hash)
                self.assertEqual(project_row["display_name"], project.display_name)
                self.assertGreaterEqual(project_row["updated_at"], before_project_updated_at)
                self.assertEqual(
                    {status: sum(row["status"] == status for row in task_rows)
                     for status in ("done", "in_progress", "ready")},
                    {"done": 9, "in_progress": 1, "ready": 2},
                )
                expected_hashes = {
                    item["task_id"]: item["completion_commit_hash"]
                    for item in fixture["tasks"] if item["completion_commit_hash"]
                }
                actual_hashes = {
                    row["task_id"]: row["completion_commit_hash"]
                    for row in task_rows if row["completion_commit_hash"]
                }
                self.assertEqual(actual_hashes, expected_hashes)
                for row in task_rows:
                    self.assertEqual(row["completion_commit_required"], 1)
                    if row["task_id"] in expected_hashes:
                        self.assertEqual(row["completion_evidence_kind"], "legacy_unverified")
                        self.assertEqual(
                            row["completion_evidence_revision"], expected_hashes[row["task_id"]]
                        )
                    else:
                        self.assertEqual(row["completion_evidence_kind"], "none")
                    self.assertEqual(row["review_target_kind"], "")
                    self.assertEqual(row["review_target_value"], "")
                    self.assertEqual(row["review_target_base_revision"], "")
                    self.assertEqual(row["review_target_generation"], 0)
                    self.assertEqual(row["current_contract_revision"], 0)
                    self.assertEqual(
                        row["completion_history_coverage"],
                        "legacy_unknown",
                    )
                cycle_rows = connection.execute(
                    """
                    SELECT completion_cycle_id, task_id,
                           saved_cycle_ordinal, origin, completeness,
                           completion_evidence_kind,
                           completion_evidence_revision,
                           completion_commit_hash,
                           verification_expectation,
                           verification_attestation,
                           gate_basis_version, review_basis_kind
                      FROM task_completion_cycles
                     ORDER BY task_id
                    """
                ).fetchall()
                self.assertEqual(len(cycle_rows), 9)
                self.assertEqual(
                    [row["task_id"] for row in cycle_rows],
                    sorted(expected_hashes),
                )
                for row in cycle_rows:
                    expected_hash = expected_hashes[row["task_id"]]
                    self.assertEqual(row["saved_cycle_ordinal"], 1)
                    self.assertEqual(row["origin"], "legacy_current_done")
                    self.assertEqual(row["completeness"], "partial")
                    self.assertEqual(
                        row["completion_evidence_kind"],
                        "legacy_unverified",
                    )
                    self.assertEqual(
                        row["completion_evidence_revision"],
                        expected_hash,
                    )
                    self.assertEqual(
                        row["completion_commit_hash"],
                        expected_hash,
                    )
                    self.assertEqual(
                        row["verification_expectation"],
                        "specified",
                    )
                    self.assertIsNone(row["verification_attestation"])
                    self.assertEqual(row["gate_basis_version"], 0)
                    self.assertEqual(row["review_basis_kind"], "unknown")
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                          FROM task_events
                         WHERE completion_cycle_id IS NULL
                        """
                    ).fetchone()[0],
                    191,
                )
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_checkpoints"
                    ).fetchone()[0],
                    0,
                )
                self.assert_single_seeded_managed_backup(connection, db_path)
                before_second_init = durable_projection(connection)
                before_second_cycles = [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT *
                          FROM task_completion_cycles
                         ORDER BY task_id, saved_cycle_ordinal
                        """
                    )
                ]

            repeated = install.run("setup", "--json")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            repeated_data = json_payload(repeated)["data"]
            self.assertEqual(repeated_data["status"], "already_setup")
            self.assertEqual(repeated_data["planned_writes"], [])
            self.assertEqual(repeated_data["completed_writes"], [])
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(durable_projection(connection), before_second_init)
                self.assertEqual(
                    [
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT *
                              FROM task_completion_cycles
                             ORDER BY task_id, saved_cycle_ordinal
                            """
                        )
                    ],
                    before_second_cycles,
                )
                self.assert_single_seeded_managed_backup(connection, db_path)

    def test_v5_v6_v12_and_v13_setup_migrate_to_current_with_review_evidence_intact(self):
        fixture = load_fixture()
        for source_version in (5, 6, 12, 13):
            with self.subTest(source_version=source_version), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                install = make_physical_install(root)
                repo = install.project_root
                db_path = install.db_path
                db_path.parent.mkdir(parents=True)
                project = create_realistic_review_database(
                    db_path,
                    repo,
                    fixture,
                    schema_version=source_version,
                )
                with closing(sqlite3.connect(db_path)) as connection:
                    before = post_v5_durable_projection(connection)

                migrated = install.run("setup", "--json")

                self.assertEqual(migrated.returncode, 0, migrated.stderr)
                payload = json_payload(migrated)
                self.assertEqual(payload["data"]["schema_from"], source_version)
                self.assertEqual(payload["data"]["schema_to"], 20)
                self.assertEqual(
                    payload["data"]["completed_writes"],
                    MIGRATION_SETUP_WRITES,
                )
                self.assertEqual(
                    payload["data"]["evidence_status"],
                    "published",
                )
                self.assertEqual(payload["project_id"], project.project_id)
                with closing(sqlite3.connect(db_path)) as connection:
                    self.assertEqual(post_v5_durable_projection(connection), before)
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                        12,
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
                        191,
                    )
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM tasks
                             WHERE completion_commit_hash != ''
                            """
                        ).fetchone()[0],
                        9,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM review_receipts"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM review_findings"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM task_checkpoints"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM tasks
                             WHERE review_target_base_revision != ''
                            """
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM handoff_records"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA quick_check").fetchone()[0],
                        "ok",
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )
                    self.assert_single_seeded_managed_backup(connection, db_path)

    def test_realistic_v13_direct_migration_preserves_every_business_table(self):
        fixture = load_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = make_physical_install(root)
            install.db_path.parent.mkdir(parents=True)
            project = create_realistic_review_database(
                install.db_path,
                install.project_root,
                fixture,
                schema_version=13,
            )
            with closing(connect(install.db_path)) as connection:
                before = nonidentity_table_projection(connection)
                apply_project_identity_bindings_migration(connection)
                after = nonidentity_table_projection(connection)

                self.assertEqual(after, before)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    12,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events"
                    ).fetchone()[0],
                    191,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM tasks
                         WHERE completion_commit_hash != ''
                        """
                    ).fetchone()[0],
                    9,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipts"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_findings"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM project_path_binding_history
                         WHERE project_id = ?
                           AND binding_generation = 1
                           AND reason = 'legacy_migration'
                        """,
                        (project.project_id,),
                    ).fetchone()[0],
                    1,
                )

    def test_setup_recovers_realistic_v12_backup_with_completion_and_review_trace(self):
        fixture = load_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = make_physical_install(root)
            db_path = install.db_path
            db_path.parent.mkdir(parents=True)
            project = create_realistic_review_database(
                db_path,
                install.project_root,
                fixture,
                schema_version=12,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                task_id = fixture["tasks"][0]["task_id"]
                connection.execute(
                    """
                    INSERT INTO task_contract_revisions(
                      contract_revision_id, task_id, project_id, revision,
                      scope, acceptance, constraints_text, authority_ref,
                      change_reason, created_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "tg_contract_migration_recovery_001",
                        task_id,
                        project.project_id,
                        "Preserve realistic recovery scope.",
                        "Preserve realistic recovery acceptance.",
                        "Preserve realistic recovery constraints.",
                        "docs/specification.md",
                        "Seed recovery acceptance evidence.",
                        "2026-07-20T14:03:00Z",
                    ),
                )
                connection.execute(
                    """
                    UPDATE tasks
                       SET current_contract_revision = 1
                     WHERE task_id = ?
                    """,
                    (task_id,),
                )
                connection.commit()
                expected = post_v5_durable_projection(connection)
                expected_contract = tuple(
                    connection.execute(
                        """
                        SELECT contract_revision_id, task_id, project_id,
                               revision, scope, acceptance, constraints_text,
                               authority_ref, change_reason, created_at
                          FROM task_contract_revisions
                         WHERE task_id = ?
                        """,
                        (task_id,),
                    ).fetchone()
                )

            migrated = install.run("setup", "--json")

            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            backup_paths = sorted(
                (db_path.parent / "backups").glob(
                    "taskgov-backup-v1_*.sqlite"
                )
            )
            self.assertEqual(len(backup_paths), 1)
            backup_bytes = backup_paths[0].read_bytes()
            selected = select_managed_backup_for_recovery(install.target)
            self.assertIsNotNone(selected)
            db_path.unlink()

            recovered = install.run("setup", "--json")

            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            payload = json_payload(recovered)
            self.assertEqual(payload["project_id"], project.project_id)
            self.assertEqual(payload["data"]["schema_from"], 12)
            self.assertEqual(payload["data"]["schema_to"], 20)
            self.assertEqual(
                payload["data"]["completed_writes"],
                RECOVERY_MIGRATION_SETUP_WRITES,
            )
            self.assertEqual(payload["data"]["evidence_status"], "published")
            self.assertEqual(backup_paths[0].read_bytes(), backup_bytes)
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(post_v5_durable_projection(connection), expected)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    12,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_events"
                    ).fetchone()[0],
                    191,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM tasks
                         WHERE completion_commit_hash != ''
                        """
                    ).fetchone()[0],
                    9,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipts"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_findings"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            """
                            SELECT contract_revision_id, task_id, project_id,
                                   revision, scope, acceptance,
                                   constraints_text, authority_ref,
                                   change_reason, created_at
                              FROM task_contract_revisions
                             WHERE task_id = ?
                            """,
                            (task_id,),
                        ).fetchone()
                    ),
                    expected_contract,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT current_contract_revision
                          FROM tasks
                         WHERE task_id = ?
                        """,
                        (task_id,),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            final_candidate = select_managed_backup_for_recovery(install.target)
            self.assertIsNotNone(final_candidate)
            self.assertGreaterEqual(
                (final_candidate.metadata.published_at,
                 final_candidate.metadata.generation_id),
                (selected.metadata.published_at,
                 selected.metadata.generation_id),
            )
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT latest_backup_generation_id,
                               backup_last_success_at,
                               applied_backup_generations
                          FROM project_maintenance
                        """
                    ).fetchone(),
                    (
                        final_candidate.metadata.generation_id,
                        final_candidate.metadata.published_at,
                        final_candidate.metadata.publication_retention,
                    ),
                )
                self.assertIsNotNone(
                    connection.execute(
                        """
                        SELECT 1 FROM managed_backup_generations
                         WHERE generation_id = ?
                        """,
                        (selected.metadata.generation_id,),
                    ).fetchone()
                )

    def test_v7_migration_rollback_preserves_realistic_v6_fixture(self):
        fixture = load_fixture()
        for fail_stage in ("after_schema", "before_commit"):
            with self.subTest(fail_stage=fail_stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "governed-project"
                repo.mkdir()
                db_path = root / "taskgov.sqlite"
                create_realistic_review_database(
                    db_path,
                    repo,
                    fixture,
                    schema_version=6,
                )
                with closing(connect(db_path)) as connection:
                    before = post_v5_durable_projection(connection)
                    with self.assertRaisesRegex(Exception, "injected handoff-outbox"):
                        apply_handoff_outbox_migration(
                            connection,
                            fail_stage=fail_stage,
                        )
                    self.assertEqual(post_v5_durable_projection(connection), before)
                    self.assertIsNone(
                        connection.execute(
                            """
                            SELECT name FROM sqlite_master
                             WHERE type = 'table' AND name = 'handoff_records'
                            """
                        ).fetchone()
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        6,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA quick_check").fetchone()[0],
                        "ok",
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )


if __name__ == "__main__":
    unittest.main()
