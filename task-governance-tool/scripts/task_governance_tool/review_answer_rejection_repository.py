"""Fresh native absence proof inside the existing continuation writer."""
from .review_answer_rejection_values import validate
from .task_values import validation_error


def require_absent_locked(connection, basis, preparation_id, selected, audit):
    from .storage import _require_evidence_writer
    _require_evidence_writer(connection)
    audit = validate(audit)
    def require(condition):
        if not condition:
            raise validation_error("review_continuation_effect_unknown", "Rejected registration is not proven absent")
    require({s["reviewer_session_id"] for s in audit["sources"] if s["rejection"] is not None}
            == {s.session_id for s in selected})
    require(all(s.action == "supplement" and s.prior_receipt_id is None for s in selected))
    for source in audit["sources"]:
        identity = tuple(source[k] for k in ("reviewer_session_id", "turn_id", "message_id"))
        require(connection.execute("SELECT 1 FROM review_collection_sources WHERE reviewer_session_id=? AND turn_id=? AND message_id=?", identity).fetchone() is None)
        # Include unproven same-actor receipts, not only a complete source row.
        require(connection.execute("""SELECT 1 FROM review_receipt_sessions AS s
JOIN review_receipts AS r ON r.review_receipt_id=s.review_receipt_id
WHERE r.project_id=? AND r.task_id=? AND r.target_generation=? AND s.session_id=?""",
            (basis["project_id"], basis["task_id"], basis["target_generation"], source["reviewer_session_id"])).fetchone() is None)
        digest = source["registration"]["submission_digest"]
        if digest is not None:
            selectors = (preparation_id, digest)
            require(connection.execute("""SELECT 1 FROM review_collection_dispatches WHERE preparation_id=? AND submission_digest=?
UNION ALL SELECT 1 FROM review_collection_dispatch_entries WHERE preparation_id=? AND submission_digest=?
UNION ALL SELECT 1 FROM review_collection_sources WHERE preparation_id=? AND submission_digest=? LIMIT 1""", selectors*3).fetchone() is None)
    prior = audit["source_continuation_id"]
    if prior is not None:
        row = connection.execute("SELECT * FROM review_continuations WHERE continuation_id=?", (prior,)).fetchone()
        require(row is not None and row["preparation_id"] == preparation_id and row["state"] == "prepared")
        require(connection.execute("""SELECT 1 FROM review_collected_captures WHERE continuation_id=? AND result_receipt_id IS NOT NULL
UNION ALL SELECT 1 FROM review_continuation_reviewers WHERE continuation_id=? AND result_receipt_id IS NOT NULL LIMIT 1""", (prior, prior)).fetchone() is None)
