import json
import sqlite3
import subprocess
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
    apply_git_snapshot_schema_migration,
    apply_handoff_outbox_migration,
    apply_initial_schema_migration,
    apply_paused_state_migration,
    apply_review_evidence_migration,
    connect,
    ensure_project_meta,
    project_identity,
)


def run_taskgov(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/taskgov.py", *args],
        cwd=SKILL_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


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
    if schema_version not in {5, 6}:
        raise ValueError("schema_version must be 5 or 6")
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
        if schema_version == 6:
            apply_git_snapshot_schema_migration(connection)
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
    def test_v2_fixture_migrates_to_v9_without_losing_observed_state(self):
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
            repo = root / "governed-project"
            repo.mkdir()
            db_path = root / "taskgov.sqlite"
            project = create_realistic_v2_database(db_path, repo, fixture)
            with closing(sqlite3.connect(db_path)) as connection:
                before_migration = legacy_v2_projection(connection)
                before_project_updated_at = connection.execute(
                    "SELECT updated_at FROM project_meta WHERE project_id = ?",
                    (project.project_id,),
                ).fetchone()[0]

            migrated = run_taskgov(
                "db", "init", "--repo", str(repo), "--db", str(db_path), "--json"
            )

            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            payload = json.loads(migrated.stdout)
            self.assertEqual(payload["project_id"], project.project_id)
            self.assertEqual(payload["data"]["migrations_applied"], [3, 4, 5, 6, 7, 8, 9])
            self.assertEqual(payload["data"]["schema_version"], 9)

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
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0],
                    0,
                )
                before_second_init = durable_projection(connection)

            repeated = run_taskgov(
                "db", "init", "--repo", str(repo), "--db", str(db_path), "--json"
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout)["data"]["migrations_applied"], [])
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(durable_projection(connection), before_second_init)

    def test_v5_and_v6_fixture_migrate_to_v9_with_review_evidence_intact(self):
        fixture = load_fixture()
        for source_version, expected_migrations in ((5, [6, 7, 8, 9]), (6, [7, 8, 9])):
            with self.subTest(source_version=source_version), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "governed-project"
                repo.mkdir()
                db_path = root / "taskgov.sqlite"
                project = create_realistic_review_database(
                    db_path,
                    repo,
                    fixture,
                    schema_version=source_version,
                )
                with closing(sqlite3.connect(db_path)) as connection:
                    before = post_v5_durable_projection(connection)

                migrated = run_taskgov(
                    "db",
                    "init",
                    "--repo",
                    str(repo),
                    "--db",
                    str(db_path),
                    "--json",
                )

                self.assertEqual(migrated.returncode, 0, migrated.stderr)
                payload = json.loads(migrated.stdout)
                self.assertEqual(
                    payload["data"]["migrations_applied"],
                    expected_migrations,
                )
                self.assertEqual(payload["data"]["schema_version"], 9)
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
