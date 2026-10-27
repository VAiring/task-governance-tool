"""Selected Viewer source reads in one caller-owned query-only snapshot.

Every selected Task's complete consumed history is checked. Unchanged display
Tasks are never fetched here. This repository does not load or publish caches.
"""
from __future__ import annotations

from collections import deque
from contextlib import closing
import json

from .completion_history_projection import format_completion_history
from .storage import (
    CompletionHistory, StorageError, _cycle_from_row,
    _validate_cycle_receipt_links_batch, _validate_current_completion_verification,
    completion_history_inconsistent,
)
from .stored_task_validation import fetch_stored_task_rows, validate_stored_task_rows
from .tasks import row_to_event, row_to_viewer_task


def _review_evidence(connection, project_id, rows):
    from .reviews import _ViewerFindingStream, _read_review_evidence
    from .review_continuation_repository import validate_judgment_storage
    if not rows:
        return []
    # Start with the selected Receipt owners. The global orphan disjunction in
    # the legacy Viewer stream would force a scan of all unrelated Findings.
    # Physical FK admission still rejects dangling parents; selected aliases
    # remain visible to the unchanged semantic parent validator.
    with closing(connection.execute("""
        WITH selected(value, ordinal) AS (
            SELECT value, CAST(key AS INTEGER) FROM json_each(?)
        ), aliases(value, ordinal) AS (
            SELECT value, ordinal FROM selected
            UNION ALL SELECT CAST(value AS BLOB), ordinal FROM selected
        )
        SELECT finding.*, finding.rowid AS inventory_rowid,
               receipt.review_receipt_id AS parent_receipt_id,
               receipt.project_id AS receipt_project_id,
               receipt.task_id AS receipt_task_id,
               receipt.target_generation, receipt.reviewer_key,
               (SELECT COUNT(*) FROM review_receipts AS candidate
                WHERE candidate.review_receipt_id IN (finding.review_receipt_id,
                    CAST(finding.review_receipt_id AS TEXT),
                    CAST(finding.review_receipt_id AS BLOB))) AS parent_candidate_count
        FROM aliases CROSS JOIN review_receipts AS receipt
          ON receipt.task_id=aliases.value
        CROSS JOIN review_findings AS finding
          ON finding.review_receipt_id IN (receipt.review_receipt_id,
                                           CAST(receipt.review_receipt_id AS BLOB))
         AND finding.status IN ('open','resolved')
         AND finding.severity IN ('high','medium','low')
        ORDER BY aliases.ordinal, finding.rowid
        """, (json.dumps([row["task_id"] for row in rows]),))) as cursor:
        # All leading columns of the existing compound index are constrained.
        # Fresh schema admission plus quick_check in this same snapshot checks
        # the closed status/severity CHECK domains, including malformed rows;
        # these seek predicates cannot conceal a corrupt out-of-domain Finding.
        stream = _ViewerFindingStream(cursor)
        result = []
        for row in rows:
            # The ordinary gate may have no continuation and return early;
            # each displayed Receipt still requires its stored judgment row.
            validate_judgment_storage(connection, review_task=(project_id, row["task_id"]))
            stream.task_id = row["task_id"]
            evidence = _read_review_evidence(
                connection, project_id, row["task_id"], validated_task=row,
                source_schema_version=34, finding_stream=stream)
            for receipt in evidence["recent_receipts"]:
                receipt.pop("review_provenance", None)
            result.append(evidence)
        return result


def _history(connection, task):
    from .evidence_validation_repository import (
        _validate_selected_completion_cycle_evidence,
        _validate_selected_schema21_completion_bundle_history,
    )
    project_id, task_id = task["project_id"], task["task_id"]
    # Check both directions of the relationship: the cycle stream below sees
    # incoming links, but cannot see an own event pointing to a foreign cycle.
    # Seek all own events, including those outside the display window, and only
    # the identities of their referenced cycles. Do not read foreign evidence.
    with closing(connection.execute("""SELECT event.project_id,event.task_id,
            event.completion_cycle_id,cycle.project_id AS cycle_project_id,
            cycle.task_id AS cycle_task_id
        FROM task_events AS event LEFT JOIN task_completion_cycles AS cycle
          ON cycle.completion_cycle_id=event.completion_cycle_id
        WHERE event.task_id IN (?,CAST(? AS BLOB))""", (task_id, task_id))) as cursor:
        for event in cursor:
            if ((event["project_id"], event["task_id"]) != (project_id, task_id)
                    or (event["completion_cycle_id"] is not None
                        and (event["cycle_project_id"], event["cycle_task_id"]) != (project_id, task_id))):
                raise completion_history_inconsistent()
    total = 0
    incomplete = task["completion_history_coverage"] != "complete"
    retained = deque(maxlen=10)
    # All own cycles, not just the visible ten, receive the same selected Bundle
    # validation. Fixed chunks satisfy the existing single-Task Bundle boundary.
    with closing(connection.execute("""SELECT * FROM task_completion_cycles
            WHERE project_id=? AND task_id IN (?,CAST(? AS BLOB)) ORDER BY saved_cycle_ordinal""",
            (project_id, task_id, task_id))) as cursor:
        while rows := cursor.fetchmany(10):
            cycles = tuple(_cycle_from_row(row) for row in rows)
            for cycle in cycles:
                total += 1
                if ((cycle.project_id, cycle.task_id) != (project_id, task_id)
                        or cycle.saved_cycle_ordinal != total):
                    raise completion_history_inconsistent()
                incomplete |= cycle.completeness == "partial"
                links = connection.execute("""SELECT project_id,task_id,event_type
                    FROM task_events WHERE completion_cycle_id=?""",
                    (cycle.completion_cycle_id,)).fetchall()
                complete = reopened = 0
                for link in links:
                    if (link["project_id"], link["task_id"]) != (project_id, task_id):
                        raise completion_history_inconsistent()
                    if link["event_type"] in {"task_updated", "review_tier_changed"}:
                        complete += 1
                    elif link["event_type"] == "task_reopened":
                        reopened += 1
                    else:
                        raise completion_history_inconsistent()
                if complete != int(cycle.origin == "native_done") or reopened > 1:
                    raise completion_history_inconsistent()
                retained.append(cycle)
            _validate_cycle_receipt_links_batch(connection, project_id=project_id, cycles=cycles)
            _validate_selected_completion_cycle_evidence(connection, project_id=project_id, cycles=cycles)
            _validate_selected_schema21_completion_bundle_history(connection, cycles=cycles)
    if retained:
        _validate_current_completion_verification(task, retained[-1])
    incomplete |= connection.execute("""SELECT 1 FROM task_events
        WHERE project_id=? AND task_id=? AND event_type='task_reopened'
          AND completion_cycle_id IS NULL LIMIT 1""", (project_id, task_id)).fetchone() is not None
    return format_completion_history(CompletionHistory(
        total=total, legacy_history_incomplete=incomplete, cycles=tuple(reversed(retained))))


def read_task_projections(connection, *, project_id, task_ids=None):
    """None rebuilds every display Task; a tuple selects exact changed IDs."""
    if not connection.in_transaction or connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise StorageError("internal_error", "Viewer projection requires a query-only snapshot")
    if task_ids is None:
        rows = fetch_stored_task_rows(connection, "SELECT * FROM tasks ORDER BY task_id")
    else:
        if len(task_ids) != len(set(task_ids)):
            raise completion_history_inconsistent()
        if not task_ids:
            return []
        rows = fetch_stored_task_rows(connection, """
            WITH selected(value) AS (SELECT value FROM json_each(?)),
                 aliases(value) AS (SELECT value FROM selected UNION ALL
                                    SELECT CAST(value AS BLOB) FROM selected)
            SELECT * FROM tasks WHERE task_id IN (SELECT value FROM aliases)
            ORDER BY task_id""", (json.dumps(task_ids),))
        if len(rows) != len(task_ids):
            raise completion_history_inconsistent()
    validate_stored_task_rows(rows, connection=connection, source_schema_version=34,
                             expected_project_id=project_id)
    evidence = _review_evidence(connection, project_id, rows)
    result = []
    for row, review in zip(rows, evidence, strict=True):
        task = row_to_viewer_task(row)
        events = connection.execute("""SELECT task_event_id,task_id,project_id,event_type,summary,created_at
            FROM task_events WHERE task_id IN (?,CAST(? AS BLOB))
            ORDER BY created_at DESC,rowid DESC LIMIT 10""", (row["task_id"], row["task_id"])).fetchall()
        if any((event["project_id"], event["task_id"]) != (project_id, row["task_id"]) for event in events):
            raise completion_history_inconsistent()
        task["events"] = [row_to_event(event) for event in events]
        task["review_evidence"] = review
        task["completion_history"] = _history(connection, row)
        result.append(task)
    return result
