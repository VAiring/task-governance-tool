"""Read-only effective judgment/Finding basis shared by every completion consumer.

Native Receipt/Reference validation stays with existing repositories. This layer
selects validated successors and explicit resolution links in the caller's same
SQLite snapshot, without using display limits or touching operational journals.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
import secrets
import sqlite3

from . import review_continuation as values
from . import review_continuation_repository as records
from .verification_declaration import verification_requirement


@dataclass(frozen=True)
class EffectiveTaskReviews:
    effective: values.EffectiveJudgments
    pending_continuation_id: str | None
    linked_resolved_finding_ids: frozenset[str]

    @property
    def effective_ids(self):
        return frozenset(row.receipt_id for row in self.effective.heads)

    @property
    def active_ids(self):
        return frozenset(row.receipt_id for row in self.effective.active)

    def projection(self):
        return dict(state="pending" if self.pending_continuation_id else "settled",
            pending_continuation_id=self.pending_continuation_id,
            pending_sessions=list(self.effective.pending_sessions),
            effective_receipt_ids=[row.receipt_id for row in self.effective.heads],
            suspended_receipt_ids=list(self.effective.suspended_receipt_ids))


@dataclass
class EffectiveReviewSnapshot:
    """Borrowed validation for one lexical, write-free part of a transaction.

    Only ``effective_review_snapshot`` creates this read scope. Callers retain
    their transaction throughout it; it cannot cross a write, another connection
    or Task basis, or escape the context manager. It is not a completion token:
    later writers and effect boundaries still perform their own current checks.
    """

    _connection: sqlite3.Connection
    _task_projection: tuple
    _total_changes: int
    _schema_cookie: int
    _guard: str
    _view: EffectiveTaskReviews | None
    _active: bool = field(default=True, init=False)

    def for_task(self, connection, task) -> EffectiveTaskReviews | None:
        from .storage import StorageError
        if (not self._active or connection is not self._connection
            or not connection.in_transaction or connection.total_changes != self._total_changes
            or tuple(sorted(dict(task).items())) != self._task_projection
            or connection.execute("PRAGMA schema_version").fetchone()[0] != self._schema_cookie):
            raise StorageError("internal_error", "validated review read is outside its snapshot")
        # The private savepoint disappears even after a write-free transaction
        # end. Check it before opening any nested material scope: RELEASE also
        # removes savepoints created inside this guard.
        try:
            connection.execute(f"RELEASE SAVEPOINT {self._guard}")
            connection.execute(f"SAVEPOINT {self._guard}")
        except sqlite3.Error as exc:
            self._active = False
            raise StorageError("internal_error", "validated review read is outside its snapshot") from exc
        return self._view

    def close(self):
        from .storage import StorageError
        self._active = False
        self._view = None
        try:
            self._connection.execute(f"RELEASE SAVEPOINT {self._guard}")
        except sqlite3.Error as exc:
            raise StorageError("internal_error", "validated review read is outside its snapshot") from exc


@contextmanager
def effective_review_snapshot(connection, task):
    """Share one effective read only inside the caller's existing transaction."""
    from .storage import StorageError
    if not connection.in_transaction:
        raise StorageError("internal_error", "validated review read requires an active transaction")
    task_projection = tuple(sorted(dict(task).items()))
    guard = "taskgov_effective_review_" + secrets.token_hex(8)
    schema_cookie = connection.execute("PRAGMA schema_version").fetchone()[0]
    connection.execute(f"SAVEPOINT {guard}")
    snapshot = EffectiveReviewSnapshot(connection,
        task_projection, connection.total_changes,
        schema_cookie, guard, None)
    try:
        snapshot._view = effective_for_task(connection, task)
        yield snapshot
    except BaseException:
        try:
            snapshot.close()
        except Exception:
            pass  # Preserve the read/consumer failure; the caller owns rollback.
        raise
    else:
        snapshot.close()


@dataclass
class _JudgmentAncestry:
    """Task-local native edges and validated ancestor sets for this read only."""

    prior_by_id: dict[str, str | None]
    validated: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def read(cls, connection, *, project_id, task_id):
        rows = connection.execute("""SELECT judgment.review_receipt_id,judgment.prior_receipt_id
FROM review_receipt_judgments AS judgment
JOIN review_receipts AS receipt ON receipt.review_receipt_id=judgment.review_receipt_id
WHERE receipt.project_id=? AND receipt.task_id=?""", (project_id, task_id)).fetchall()
        return cls({row["review_receipt_id"]: row["prior_receipt_id"] for row in rows})

    def ancestors(self, receipt_id):
        path, visited = [], set()
        current = receipt_id
        while current not in self.validated:
            if current in visited or current not in self.prior_by_id:
                raise records._inconsistent()
            visited.add(current)
            path.append(current)
            prior = self.prior_by_id[current]
            if prior is None:
                self.validated[current] = frozenset()
                break
            current = prior
        for current in reversed(path):
            prior = self.prior_by_id[current]
            self.validated[current] = (frozenset() if prior is None
                else self.validated[prior] | {prior})
        return self.validated[receipt_id]


@dataclass(frozen=True)
class _ResolutionHistory:
    links: tuple[dict, ...]
    ancestry: _JudgmentAncestry | None


def read_resolution_links(connection, *, project_id, task_id):
    return _read_resolution_history(connection, project_id=project_id, task_id=task_id).links


def _read_resolution_history(connection, *, project_id, task_id):
    from .storage import validate_utc_timestamp, COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
    from .evidence_validation_repository import validate_selected_task_receipt_evidence
    from .task_values import TaskValidationError, validate_text
    rows = connection.execute("""SELECT link.*,finding.status,finding.resolution_summary AS original_resolution,
finding.resolved_at,finding.review_receipt_id AS original_receipt_id,judgment.continuation_id AS judgment_continuation,
continuation.project_id,continuation.task_id,continuation.state
FROM review_reassessment_resolutions AS link
JOIN review_findings AS finding ON finding.review_finding_id=link.review_finding_id
JOIN review_receipts AS original ON original.review_receipt_id=finding.review_receipt_id
LEFT JOIN review_receipt_judgments AS judgment ON judgment.review_receipt_id=link.successor_receipt_id
LEFT JOIN review_continuations AS continuation ON continuation.continuation_id=judgment.continuation_id
WHERE original.project_id=? AND original.task_id=? ORDER BY link.review_finding_id,link.successor_receipt_id""",
        (project_id, task_id)).fetchall()
    finding_ids = sorted({row["review_finding_id"] for row in rows})
    from .review_material_repository import review_materials
    if finding_ids:
        with review_materials(connection, project_id=project_id, task_id=task_id) as materials:
            for start in range(0, len(finding_ids), COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE):
                validate_selected_task_receipt_evidence(connection, project_id=project_id, task_id=task_id,
                    review_receipt_ids=set(), review_finding_ids=set(finding_ids[start:start + COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE]),
                    verification_receipt_ids=set(), _materials=materials)
    ancestry = _JudgmentAncestry.read(connection, project_id=project_id, task_id=task_id) if rows else None
    result = []
    for row in rows:
        try:
            if (row["status"] != "resolved" or row["continuation_id"] != row["judgment_continuation"]
                or row["project_id"] != project_id or row["task_id"] != task_id or row["state"] not in ("registered", "superseded")
                or validate_text("review_finding_resolution", row["resolution_summary"], required=True, limit=1000) != row["resolution_summary"]):
                raise records._inconsistent()
            validate_utc_timestamp(row["created_at"], field="review reassessment resolution time")
            digest = records.resolution_digest(finding_id=row["review_finding_id"], successor_id=row["successor_receipt_id"],
                continuation_id=row["continuation_id"], summary=row["resolution_summary"],
                original_resolution=row["original_resolution"], resolved_at=row["resolved_at"])
            if row["resolution_digest"] != digest:
                raise records._inconsistent()
            # Complete structural ancestry remains independent of display bounds.
            if row["original_receipt_id"] not in ancestry.ancestors(row["successor_receipt_id"]):
                raise records._inconsistent()
            result.append(dict(row))
        except (TaskValidationError, UnicodeError, ValueError) as exc:
            raise records._inconsistent() from exc
    return _ResolutionHistory(tuple(result), ancestry)


def effective_for_task(connection, task) -> EffectiveTaskReviews | None:
    """Return None for the unchanged ordinary/legacy gate without continuations."""
    from .storage import current_schema_version
    if current_schema_version(connection) < 29:
        return None
    continuations = records.read_continuations(connection, project_id=task["project_id"], task_id=task["task_id"])
    matching = [row for row in continuations if all(row.basis["target_" + key] == task["review_target_" + key]
        for key in ("kind", "value", "base_revision", "generation"))]
    if not matching:
        return None
    basis = dict(matching[0].basis)
    basis.update(contract_revision=task["current_contract_revision"], authority_snapshot_id=task["review_target_authority_snapshot_id"],
        acceptance_criterion_id=task["review_target_acceptance_criterion_id"], verification_criterion_id=task["review_target_verification_criterion_id"],
        artifact_manifest_id=task["review_target_artifact_manifest_id"], review_tier=task["review_tier"],
        verification_requirement=verification_requirement(task["verification"], task["verification_not_required_reason"]),
        verification_not_required_reason=task["verification_not_required_reason"])
    quality = values.quality_identity(values.validate_basis(basis))
    if any(values.quality_identity(row.basis) != quality for row in matching):
        raise records._inconsistent()
    nodes = records.read_judgments(connection, basis=basis, _validated_continuations=continuations)
    pending = [row for row in matching if row.state in ("preparing", "prepared")]
    if len(pending) > 1:
        raise records._inconsistent()
    choices = list(pending[0].selected) if pending else []
    by_id = {row.continuation_id: row for row in matching}
    if pending:
        for capture in records.read_retained_originals(connection, pending[0].continuation_id):
            old = by_id[capture["prior_continuation_id"]]
            choices.extend(row for row in old.selected if row.session_id == capture["session_id"])
    effective = values.select_effective(nodes, quality_basis=quality, pending=tuple(choices))
    history = _read_resolution_history(connection, project_id=task["project_id"], task_id=task["task_id"])
    head_lineage = set()
    if history.ancestry is not None:
        for head in effective.heads:
            head_lineage.update(history.ancestry.ancestors(head.receipt_id) | {head.receipt_id})
    linked = {link["review_finding_id"] for link in history.links
        if link["successor_receipt_id"] in head_lineage}
    return EffectiveTaskReviews(effective, pending[0].continuation_id if pending else None, frozenset(linked))


def freeze_basis_locked(connection, task):
    """Capture the exact graph now; never reconstruct a sealed graph later."""
    from .storage import _require_evidence_writer, current_schema_version
    from .review_repository import read_review_receipt_with_provenance
    from .review_continuation_evidence import ASSERTION_KEYS, REFERENCE_KEYS, validate_graph
    from .evidence_ledger import canonical_json_bytes
    from .evidence_projection import _reference_projection
    import json
    _require_evidence_writer(connection)
    view = effective_for_task(connection, task)
    if view is None:
        return None
    if view.pending_continuation_id is not None:
        raise records._inconsistent()
    rows = connection.execute("SELECT * FROM review_continuations WHERE project_id=? AND task_id=? AND target_generation=? ORDER BY continuation_id",
        (task["project_id"], task["task_id"], task["review_target_generation"])).fetchall()
    continuation_ids = [row["continuation_id"] for row in rows]
    json_ids = json.dumps(continuation_ids)
    reviewers = connection.execute("SELECT * FROM review_continuation_reviewers WHERE continuation_id IN (SELECT value FROM json_each(?)) ORDER BY continuation_id,session_id", (json_ids,)).fetchall()
    retained = connection.execute("SELECT * FROM review_continuation_retained_originals WHERE continuation_id IN (SELECT value FROM json_each(?)) ORDER BY continuation_id,session_id", (json_ids,)).fetchall()
    judgments = connection.execute("""SELECT judgment.* FROM review_receipt_judgments AS judgment
JOIN review_receipts AS receipt ON receipt.review_receipt_id=judgment.review_receipt_id
WHERE receipt.project_id=? AND receipt.task_id=? AND receipt.target_generation=? ORDER BY judgment.review_receipt_id""",
        (task["project_id"], task["task_id"], task["review_target_generation"])).fetchall()
    receipt_ids = [row["review_receipt_id"] for row in judgments]
    receipts = []
    for receipt_id in receipt_ids:
        value = read_review_receipt_with_provenance(connection, review_receipt_id=receipt_id)
        if value is None:
            raise records._inconsistent()
        receipt = value["receipt"]
        assertion = {key: receipt[key] for key in ASSERTION_KEYS - {"review_provenance"}}
        assertion.update(review_provenance=value["provenance"], judgment_revision=receipt["judgment_revision"])
        receipts.append(assertion)
    selected = json.dumps(receipt_ids)
    sessions = connection.execute("SELECT * FROM review_receipt_sessions WHERE review_receipt_id IN (SELECT value FROM json_each(?)) ORDER BY review_receipt_id", (selected,)).fetchall()
    findings = connection.execute("SELECT * FROM review_findings WHERE review_receipt_id IN (SELECT value FROM json_each(?)) ORDER BY review_finding_id", (selected,)).fetchall()
    finding_ids = json.dumps([row["review_finding_id"] for row in findings])
    resolutions = connection.execute("SELECT * FROM review_reassessment_resolutions WHERE successor_receipt_id IN (SELECT value FROM json_each(?)) ORDER BY review_finding_id,successor_receipt_id", (selected,)).fetchall()
    references = connection.execute("""SELECT * FROM evidence_references
WHERE (source_kind='review_receipt' AND source_id IN (SELECT value FROM json_each(?)))
OR (source_kind='review_finding' AND source_id IN (SELECT value FROM json_each(?))) ORDER BY evidence_reference_id""", (selected, finding_ids)).fetchall()
    graph = dict(version=2 if current_schema_version(connection)>=30 else 1, continuations=[dict(row) for row in rows], reviewers=[dict(row) for row in reviewers],
        retained_originals=[dict(row) for row in retained],
        judgments=[dict(row) for row in judgments], resolutions=[dict(row) for row in resolutions], receipts=receipts,
        session_bindings=[dict(row) for row in sessions], findings=[dict(row) for row in findings],
        references=[_reference_projection(dict(row)) for row in references],
        effective_receipt_ids=[row.receipt_id for row in view.effective.heads])
    if graph["version"] == 2:
        from .review_answer_repository import read_collected_captures
        graph["collected_captures"] = [capture for cid in sorted(continuation_ids) for capture in read_collected_captures(connection,cid)]
    validate_graph(graph)
    # The caller stores these canonical bytes in the immutable Bundle row.
    canonical_json_bytes(graph)
    return graph


def completion_selection_locked(connection, task):
    from .storage import _require_evidence_writer, CompletionGateBasis, current_schema_version
    from .reviews import read_review_evidence, first_review_gate_error
    _require_evidence_writer(connection)
    with effective_review_snapshot(connection, task) as snapshot:
        view = snapshot.for_task(connection, task)
        if view is None:
            return None
        evidence = read_review_evidence(connection, task["project_id"], task["task_id"],
            validated_task=task, source_schema_version=current_schema_version(connection), _continuation_snapshot=snapshot)
    if first_review_gate_error(evidence) is not None:
        raise records._inconsistent()
    import json
    active = json.dumps(sorted(view.active_ids))
    independent = connection.execute("""SELECT review_receipt_id FROM review_receipts
WHERE review_receipt_id IN (SELECT value FROM json_each(?)) AND receipt_kind='independent'
AND verdict='pass' AND user_approved=0 ORDER BY reviewer_key COLLATE BINARY,review_receipt_id COLLATE BINARY""", (active,)).fetchall()
    tier = task["review_tier"]
    required = {0: 0, 1: 1, 2: 2}[tier]
    if tier in (1, 2) and len(independent) >= required:
        kind = "independent_passes"
        receipt_ids = tuple(row[0] for row in independent[:required])
    else:
        kind = "not_required" if tier == 0 else "self_review_fallback"
        verdict = "not_required" if tier == 0 else "pass"
        approval = int(tier == 2)
        row = connection.execute("""SELECT review_receipt_id FROM review_receipts
WHERE review_receipt_id IN (SELECT value FROM json_each(?)) AND receipt_kind=? AND verdict=?
AND user_approved=? ORDER BY review_receipt_id COLLATE BINARY LIMIT 1""", (active, kind, verdict, approval)).fetchone()
        if row is None:
            raise records._inconsistent()
        receipt_ids = (row[0],)
    return CompletionGateBasis(2, kind, required, len(independent), 0, 0, 0, 0, receipt_ids)


def validate_frozen_against_core(connection, graph):
    """Historical comparisons use immutable fields, not later live resolution state."""
    from .review_continuation_evidence import validate_graph, ASSERTION_KEYS, REFERENCE_KEYS
    from .review_repository import read_review_receipt_with_provenance
    from .evidence_projection import _reference_projection
    validate_graph(graph)
    if graph["version"] == 2:
        from .review_answer_repository import read_collected_captures
        # A supplied-row comparison cannot detect omitted captures. The frozen
        # consumers are settled, so their full native inventories are immutable;
        # later consumers remain outside this historical Bundle's boundary.
        captures = [capture for consumer in graph["continuations"]
            for capture in read_collected_captures(connection, consumer["continuation_id"])]
        if graph["collected_captures"] != captures:
            raise records._inconsistent()
    for name, table, keys in (
        ("continuations", "review_continuations", ("continuation_id",)),
        ("reviewers", "review_continuation_reviewers", ("continuation_id", "session_id")),
        ("retained_originals", "review_continuation_retained_originals", ("continuation_id", "session_id")),
        ("judgments", "review_receipt_judgments", ("review_receipt_id",)),
        ("resolutions", "review_reassessment_resolutions", ("review_finding_id", "successor_receipt_id")),
        ("session_bindings", "review_receipt_sessions", ("review_receipt_id",))):
        for row in graph[name]:
            # Closed table/column names above, never caller SQL.
            predicate = " AND ".join(key + "=?" for key in keys)
            current = connection.execute(f"SELECT * FROM {table} WHERE {predicate}", tuple(row[key] for key in keys)).fetchone()
            if current is None or dict(current) != row:
                raise records._inconsistent()
    for row in graph["receipts"]:
        current = read_review_receipt_with_provenance(connection, review_receipt_id=row["review_receipt_id"])
        if current is None:
            raise records._inconsistent()
        assertion = {key: current["receipt"][key] for key in ASSERTION_KEYS - {"review_provenance"}}
        assertion.update(review_provenance=current["provenance"], judgment_revision=current["receipt"]["judgment_revision"])
        if assertion != row:
            raise records._inconsistent()
    for row in graph["findings"]:
        current = connection.execute("SELECT * FROM review_findings WHERE review_finding_id=?", (row["review_finding_id"],)).fetchone()
        if current is None or any(current[key] != row[key] for key in ("review_finding_id", "review_receipt_id", "severity", "summary", "created_at")):
            raise records._inconsistent()
    for row in graph["references"]:
        current = connection.execute("SELECT * FROM evidence_references WHERE evidence_reference_id=?", (row["evidence_reference_id"],)).fetchone()
        if current is None or _reference_projection(dict(current)) != row:
            raise records._inconsistent()
