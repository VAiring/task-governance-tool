"""Read-only recovery admission, without attribution or derived-work execution.

Original values and retained restart inputs are checked in the private copy.
These streaming checks deliberately cost O(original input), not O(replay work).
They do not certify historical aggregates or advance the rotating evidence audit.
"""

import json

from .session_identity import is_session_id
from .task_ownership import _identifier
from .task_values import TaskValidationError, validate_task_id
from .usage_repository import _cursor, _response
from .usage_turn_adapter import TurnObservation, OperationObservation
from .usage_review_attribution import ReviewBoundary, ReviewReceiptTurn
from .usage_wait_attribution import WaitObservation
from .usage_response_repository import _decode_boundary_relation
from .usage_response_adapter import ResponsePosition, ResponseTracker
from .usage_incremental_repository import _document, _discovery, _model_values, _hex
from .usage_reconciliation_core import _decode, cursor
from .usage_values import GAP_CODES, RESPONSE_ID, UsageError, label


def validate_recovery_inputs(repository, connection):
    """Borrow one already-admitted, query-only numerical transaction."""
    if not connection.in_transaction or connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise UsageError("usage_schema_invalid")
    _original_values(connection)
    _collection_continuations(repository, connection)
    _restart_inputs(repository, connection)
    # Bootstrap must decode retained immutable bodies even after derived queues
    # are reset. No full membership classification or historical total is read.
    for row in connection.execute("SELECT snapshot_id,document FROM usage_snapshots"):
        repository._snapshot_document(row)


def _original_values(connection):
    for row in connection.execute("SELECT code FROM usage_diagnostics"):
        if type(row[0]) is not str or row[0] not in GAP_CODES:
            raise UsageError("usage_schema_invalid")
    for row in connection.execute("SELECT * FROM usage_responses"):
        _response(row)
    for table, codec in (("usage_turns", TurnObservation), ("usage_operation_turns", OperationObservation),
                         ("usage_review_boundaries", ReviewBoundary), ("usage_review_receipt_turns", ReviewReceiptTurn),
                         ("usage_wait_turns", WaitObservation)):
        for row in connection.execute("SELECT * FROM " + table):
            codec(**dict(row))
    for row in connection.execute("SELECT position.*,source.thread_id AS source_thread FROM usage_response_positions position "
                                  "JOIN usage_sources source USING(source_id)"):
        item = ResponsePosition(**{name: row[name] for name in ResponsePosition.__dataclass_fields__})
        if item.thread_id != row["source_thread"]:
            raise UsageError("usage_schema_invalid")
    for row in connection.execute("SELECT boundary.*,source.thread_id AS source_thread FROM usage_response_boundaries boundary "
                                  "JOIN usage_sources source USING(source_id)"):
        _decode_boundary_relation(row)


def _collection_continuations(repository, connection):
    _discovery(connection.execute("SELECT * FROM usage_discovery").fetchone())
    for row in connection.execute("SELECT thread_id FROM usage_sessions"):
        if not is_session_id(row[0]):
            raise UsageError("usage_schema_invalid")
    for table in ("usage_sources", "usage_incarnations"):
        for row in connection.execute("SELECT * FROM " + table):
            if not _hex(row["source_id"]):
                raise UsageError("usage_schema_invalid")
            _cursor(row)
    for table, column in (("usage_work_order", "last_attempt"), ("usage_discovered", "cycle")):
        for row in connection.execute("SELECT * FROM " + table):
            if (not _hex(row["source_id"]) or type(row[column]) is not int
                    or not 0 <= row[column] < 2**63):
                raise UsageError("usage_schema_invalid")
    for row in connection.execute("SELECT turn_id,model,effort FROM usage_scan_models"):
        if not is_session_id(row["turn_id"]):
            raise UsageError("usage_schema_invalid")
        _model_values((row["model"], row["effort"]))
    for row in connection.execute("SELECT turn_id,modern,legacy FROM usage_scan_coverage"):
        # Legacy token_count records may precede any admitted turn context.
        # Preserve the collector's missing-turn sentinel, never a modern turn.
        unknown_legacy = tuple(row) == ("", 0, 1)
        if not unknown_legacy and not is_session_id(row["turn_id"]):
            raise UsageError("usage_schema_invalid")
    # Saved locations are shape-checked hints, never authority to read a path
    # or attribute a pending reviewer to a restored Core Task.
    repository._sequence_value(connection)
    for table, pending in (("usage_locators", False), ("usage_pending_locators", True)):
        for row in connection.execute("SELECT * FROM " + table):
            repository._read_locator(row, pending=pending)
    for row in connection.execute("SELECT scan.*,source.incarnation AS source_incarnation,source.offset AS source_offset,"
                                  "context.document AS context FROM usage_scan_state scan JOIN usage_sources source USING(source_id) "
                                  "LEFT JOIN usage_response_context context USING(source_id,lane)"):
        if type(row["revision"]) is not int or not 0 < row["revision"] < 2**63:
            raise UsageError("usage_schema_invalid")
        state = _document(row)
        needs_context = not state["reset"] and state["incarnation"] == row["source_incarnation"]
        if row["lane"] == "audit" and (not row["source_offset"] or state["state"].offset == state["goal"]):
            needs_context = False
        if needs_context and row["context"] is None:
            raise UsageError("usage_schema_invalid")
    for row in connection.execute("SELECT context.document,source.thread_id,scan.document AS scan FROM usage_response_context context "
                                  "JOIN usage_sources source USING(source_id) LEFT JOIN usage_scan_state scan USING(source_id,lane)"):
        if row["scan"] is None:
            raise UsageError("usage_schema_invalid")
        ResponseTracker(row["thread_id"], "validation", json.loads(row["document"]))


def _restart_inputs(repository, connection):
    # Unlike the freshness fence, these checks cannot short-circuit on the new
    # core/file identity: admit_identity decodes the saved identity before reset.
    revision = repository.reconciliation_revision(connection)
    # The audit cursor survives identity reset and is decoded before a new
    # publication cursor is saved. Validate control only, never run the audit.
    repository._publication_state(connection)
    row = connection.execute("SELECT * FROM usage_reconcile_identity WHERE singleton=1").fetchone()
    _decode(row)
    if row is not None and (type(row["observed_generation"]) is not int
                           or not 0 <= row["observed_generation"] < 2**63):
        raise UsageError("usage_schema_invalid")
    cursor(connection)
    row = connection.execute("SELECT generation FROM usage_reconcile_evidence_revision WHERE singleton=1").fetchone()
    if row is None or type(row[0]) is not int or not 0 <= row[0] < 2**63:
        raise UsageError("usage_schema_invalid")
    # Changes survive reset, including OLD identities after deletion. Validate
    # every producer's key shape without requiring any restored Core anchor.
    for row in connection.execute("SELECT kind,key1,key2,generation FROM usage_reconcile_changes"):
        if (type(row["generation"]) is not int or not 0 < row["generation"] <= revision.generation
                or not _change_keys(row["kind"], row["key1"], row["key2"])):
            raise UsageError("usage_schema_invalid")


def _change_keys(kind, first, second):
    """Closed structural keys emitted by reconciliation's OLD/NEW triggers."""
    if type(first) is not str or type(second) is not str:
        return False
    if kind == "thread":
        return is_session_id(first) and second == ""
    if kind == "turn":
        return is_session_id(first) and is_session_id(second)
    if kind == "task":
        try:
            return 0 < len(first) <= 200 and validate_task_id(second) == second
        except TaskValidationError:
            return False
    if kind in {"source", "context"}:
        return _hex(first) and second == ""
    if kind == "response":
        return bool(first and label(first) == first and RESPONSE_ID.fullmatch(second))
    if kind == "boundary":
        return _hex(first) and _hex(second)
    if kind == "origin_event":
        return _identifier(first, "tg_event_") and second == ""
    if kind == "origin_call":
        return bool(is_session_id(first) and RESPONSE_ID.fullmatch(second))
    if kind == "clock":
        return (is_session_id(first) and 0 < len(second) <= 19
                and second.isascii() and second.isdecimal()
                and str(int(second)) == second and int(second) < 2**63)
    if kind == "receipt":
        return _identifier(first, "tg_review_receipt_") and second == ""
    return False
