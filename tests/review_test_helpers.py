import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path


FINGERPRINT = "sha256:" + "c" * 64


def _schema_version(connection):
    row = connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def _review_services():
    scripts_root = (
        Path(__file__).resolve().parents[1]
        / "task-governance-tool"
        / "scripts"
    )
    added = str(scripts_root) not in sys.path
    if added:
        sys.path.insert(0, str(scripts_root))
    try:
        from task_governance_tool.reviews import (
            add_review_receipt,
            set_review_target,
        )
        from task_governance_tool.storage import ProjectIdentity
    finally:
        if added:
            sys.path.remove(str(scripts_root))
    return ProjectIdentity, add_review_receipt, set_review_target


def _seed_native_review_evidence(
    connection,
    task_id,
    *,
    target_kind,
    target_value,
    repo_path,
):
    ProjectIdentity, add_review_receipt, set_review_target = _review_services()
    original_row_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT project_id, review_tier FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise AssertionError(f"unknown test task: {task_id}")
        project = ProjectIdentity(
            project_id=str(row["project_id"]),
            canonical_repo=Path(repo_path).resolve(),
            canonical_path_hash="0" * 64,
            display_name="test-project",
        )
        set_review_target(
            connection,
            project,
            task_id,
            kind=target_kind,
            revision=target_value,
        )
        tier = int(row["review_tier"])
        receipts = (
            [("mechanical-review", "not_required", "not_required", "Mechanical test setup")]
            if tier == 0
            else [("test-reviewer-a", "independent", "pass", "")]
        )
        if tier == 2:
            receipts.append(("test-reviewer-b", "independent", "pass", ""))
        for reviewer, kind, verdict, summary in receipts:
            provenance = (
                {}
                if kind == "not_required"
                else {
                    "reviewer_class": "human",
                    "model_state": "not_applicable",
                    "skill_state": "not_applicable",
                    "review_profiles": ["general"],
                    "review_lenses": ["correctness"],
                    "context_relation": "external_context",
                    "review_methods": ["review_packet_inspection"],
                }
            )
            add_review_receipt(
                connection,
                project,
                task_id,
                reviewer=reviewer,
                kind=kind,
                verdict=verdict,
                summary=summary,
                **provenance,
            )
    finally:
        connection.row_factory = original_row_factory


def seed_review_evidence_connection(
    connection,
    task_id,
    *,
    target_kind="diff_fingerprint",
    target_value=FINGERPRINT,
    target_base_revision="",
    repo_path=None,
):
    if _schema_version(connection) >= 18:
        _seed_native_review_evidence(
            connection,
            task_id,
            target_kind=target_kind,
            target_value=target_value,
            repo_path=repo_path or Path.cwd(),
        )
        return
    _seed_legacy_review_evidence(
        connection,
        task_id,
        target_kind=target_kind,
        target_value=target_value,
        target_base_revision=target_base_revision,
    )


def _seed_legacy_review_evidence(
    connection,
    task_id,
    *,
    target_kind,
    target_value,
    target_base_revision,
):
    row = connection.execute(
        "SELECT project_id, review_tier, review_target_generation FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise AssertionError(f"unknown test task: {task_id}")
    generation = int(row[2]) + 1
    connection.execute(
        """
        UPDATE tasks
           SET review_target_kind = ?,
               review_target_value = ?,
               review_target_base_revision = ?,
               review_target_generation = ?
         WHERE task_id = ?
        """,
        (
            target_kind,
            target_value,
            target_base_revision,
            generation,
            task_id,
        ),
    )
    tier = int(row[1])
    if tier == 0:
        receipts = [("mechanical-review", "not_required", "not_required", "Mechanical test setup")]
    else:
        receipts = [("test-reviewer-a", "independent", "pass", "")]
        if tier == 2:
            receipts.append(("test-reviewer-b", "independent", "pass", ""))
    for reviewer, kind, verdict, summary in receipts:
        connection.execute(
            """
            INSERT INTO review_receipts(
              review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
              verdict, target_kind, target_value, target_base_revision,
              target_generation, summary, user_approved, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                      '2026-07-22T00:00:00Z')
            """,
            (
                "tg_review_receipt_" + uuid.uuid4().hex[:16],
                task_id,
                row[0],
                reviewer,
                kind,
                verdict,
                target_kind,
                target_value,
                target_base_revision,
                generation,
                summary,
            ),
        )


def seed_review_evidence(db, task_id, **target):
    with closing(sqlite3.connect(db)) as connection:
        repo_path = Path(db).resolve().parent / "repo"
        seed_review_evidence_connection(
            connection,
            task_id,
            repo_path=repo_path,
            **target,
        )
        connection.commit()
