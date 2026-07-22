import sqlite3
import uuid
from contextlib import closing


FINGERPRINT = "sha256:" + "c" * 64


def seed_review_evidence_connection(
    connection,
    task_id,
    *,
    target_kind=None,
    target_value=None,
):
    row = connection.execute(
        """
        SELECT project_id, review_tier, review_target_generation,
               completion_evidence_kind, completion_evidence_revision
          FROM tasks WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        raise AssertionError(f"unknown test task: {task_id}")
    generation = int(row[2]) + 1
    if target_kind is None:
        completion_kind = str(row[3])
        if completion_kind in {"git_commit", "external_revision"}:
            target_kind = completion_kind
            target_value = str(row[4])
        else:
            target_kind = "diff_fingerprint"
            target_value = FINGERPRINT
    if target_value is None:
        raise AssertionError("target_value is required when target_kind is explicit")
    connection.execute(
        """
        UPDATE tasks
           SET review_target_kind = ?,
               review_target_value = ?,
               review_target_generation = ?
         WHERE task_id = ?
        """,
        (target_kind, target_value, generation, task_id),
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
              verdict, target_kind, target_value, target_generation, summary,
              user_approved, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
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
                generation,
                summary,
            ),
        )


def seed_review_evidence(db, task_id, *, target_kind=None, target_value=None):
    with closing(sqlite3.connect(db)) as connection:
        seed_review_evidence_connection(
            connection,
            task_id,
            target_kind=target_kind,
            target_value=target_value,
        )
        connection.commit()
