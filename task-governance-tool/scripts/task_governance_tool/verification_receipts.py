"""Caller-attested verification Receipt validation, gates, and projections."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from task_governance_tool.completion import FULL_GIT_OBJECT_ID
from task_governance_tool.reviews import DIFF_FINGERPRINT
from task_governance_tool.storage import (
    CompletionCycle,
    DatabaseTarget,
    ProjectIdentity,
    begin_initialized_write,
    completion_history_inconsistent,
    insert_verification_receipt_locked,
    read_verification_receipt_snapshot,
    verification_command_label_is_summary,
    verification_expectation_digest,
)
from task_governance_tool.tasks import (
    SQLITE_INT64_MAX,
    TaskRepositoryError,
    TaskValidationError,
    read_internal_task,
    validate_sqlite_int64,
    validate_task_id,
    validate_text,
)


VERIFICATION_RECEIPT_ID_PATTERN = re.compile(
    r"^tg_verification_receipt_[0-9a-f]{16}$"
)
VERIFICATION_RESULTS = ("pass", "fail", "timeout")
VERIFICATION_COVERAGE = ("full", "partial")
VERIFICATION_ACTIVE_STATUSES = {"in_progress", "review_pending"}
PUBLIC_VERIFICATION_RECEIPT_FIELDS = (
    "verification_receipt_id",
    "project_id",
    "task_id",
    "contract_revision",
    "command_label",
    "result",
    "duration_ms",
    "scope_coverage",
    "source_revision",
    "created_at",
)
STORED_RECEIPT_MESSAGE = "stored verification evidence is inconsistent"


@dataclass(frozen=True)
class VerificationReceiptError(Exception):
    code: str
    message: str
    field: str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class VerificationReceiptInput:
    command_label: str
    result: str
    duration_ms: int
    scope_coverage: str
    expected_target_generation: int


@dataclass(frozen=True)
class VerificationReceiptResult:
    receipt: dict[str, Any]


@dataclass(frozen=True)
class VerificationGate:
    required: bool
    satisfied: bool
    blocking_code: str | None
    qualifying_receipt_id: str | None

    def to_public(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "satisfied": self.satisfied,
            "blocking_code": self.blocking_code,
            "qualifying_receipt_id": self.qualifying_receipt_id,
        }


def _error(code: str, message: str, field: str | None = None) -> VerificationReceiptError:
    return VerificationReceiptError(code=code, message=message, field=field)


def _invalid_stored() -> VerificationReceiptError:
    return _error("invalid_verification_evidence", STORED_RECEIPT_MESSAGE)


def _normalize_label(value: Any) -> str:
    try:
        label = validate_text(
            "command_label",
            value,
            required=True,
            limit=200,
        )
    except TaskValidationError as exc:
        if exc.code == "privacy_rejected":
            raise _error(exc.code, exc.message, exc.field) from exc
        raise _error(
            "invalid_verification_evidence",
            "command_label must be a non-empty sanitized label of at most 200 characters",
            "command_label",
        ) from exc
    if not verification_command_label_is_summary(label):
        raise _error(
            "invalid_verification_evidence",
            "command_label must be a non-empty sanitized label of at most 200 characters",
            "command_label",
        )
    return label


def _normalize_choice(
    value: Any,
    *,
    field: str,
    allowed: tuple[str, ...],
    message: str,
) -> str:
    if not isinstance(value, str) or value.strip() not in allowed:
        raise _error("invalid_verification_evidence", message, field)
    return value.strip()


def _normalize_integer(
    value: Any,
    *,
    field: str,
    positive: bool,
    message: str,
) -> int:
    try:
        number = validate_sqlite_int64(value, field=field)
    except TaskValidationError as exc:
        raise _error("invalid_verification_evidence", message, field) from exc
    if number < (1 if positive else 0) or number > SQLITE_INT64_MAX:
        raise _error("invalid_verification_evidence", message, field)
    return number


def normalize_verification_receipt_input(
    *,
    command_label: Any,
    result: Any,
    duration_ms: Any,
    scope_coverage: Any,
    expected_target_generation: Any,
) -> VerificationReceiptInput:
    """Validate caller values in the fixed public fail-fast order."""

    label = _normalize_label(command_label)
    normalized_result = _normalize_choice(
        result,
        field="result",
        allowed=VERIFICATION_RESULTS,
        message="result must be one of pass, fail, or timeout",
    )
    normalized_duration = _normalize_integer(
        duration_ms,
        field="duration_ms",
        positive=False,
        message="duration_ms must be a nonnegative signed-64-bit integer",
    )
    normalized_coverage = _normalize_choice(
        scope_coverage,
        field="scope_coverage",
        allowed=VERIFICATION_COVERAGE,
        message="scope_coverage must be full or partial",
    )
    normalized_generation = _normalize_integer(
        expected_target_generation,
        field="expected_target_generation",
        positive=True,
        message="expected_target_generation must be a positive signed-64-bit integer",
    )
    return VerificationReceiptInput(
        command_label=label,
        result=normalized_result,
        duration_ms=normalized_duration,
        scope_coverage=normalized_coverage,
        expected_target_generation=normalized_generation,
    )


def _stored_integer(value: Any, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > SQLITE_INT64_MAX:
        raise _invalid_stored()
    return value


def _stored_text(value: Any, *, field: str, required: bool, limit: int) -> str:
    try:
        text = validate_text(field, value, required=required, limit=limit)
    except TaskValidationError as exc:
        raise _invalid_stored() from exc
    return text


def _source_revision_from_values(
    *,
    kind: Any,
    value: Any,
    base_revision: Any,
    generation: Any,
    allow_missing: bool,
) -> dict[str, Any] | None:
    if not all(isinstance(item, str) for item in (kind, value, base_revision)):
        raise _invalid_stored()
    target_kind = str(kind)
    target_value = str(value)
    target_base = str(base_revision)
    target_generation = _stored_integer(generation)
    if not target_kind:
        if target_value or target_base:
            raise _invalid_stored()
        if not allow_missing or target_generation < 0:
            raise _invalid_stored()
        return None
    if target_generation <= 0 or not target_value or target_value != target_value.strip():
        raise _invalid_stored()
    if target_kind == "git_snapshot":
        if (
            DIFF_FINGERPRINT.fullmatch(target_value) is None
            or FULL_GIT_OBJECT_ID.fullmatch(target_base) is None
            or set(target_base) == {"0"}
        ):
            raise _invalid_stored()
        public_base: str | None = target_base
    elif target_kind == "git_commit":
        if (
            target_base
            or FULL_GIT_OBJECT_ID.fullmatch(target_value) is None
            or set(target_value) == {"0"}
        ):
            raise _invalid_stored()
        public_base = None
    elif target_kind == "diff_fingerprint":
        if target_base or DIFF_FINGERPRINT.fullmatch(target_value) is None:
            raise _invalid_stored()
        public_base = None
    elif target_kind == "external_revision":
        if target_base:
            raise _invalid_stored()
        _stored_text(
            target_value,
            field="review_target_value",
            required=True,
            limit=500,
        )
        public_base = None
    else:
        raise _invalid_stored()
    return {
        "kind": target_kind,
        "value": target_value,
        "base_revision": public_base,
        "generation": target_generation,
    }


def _task_basis(task: Mapping[str, Any]) -> tuple[str, int, str, dict[str, Any] | None]:
    try:
        expectation = _stored_text(
            task["verification"],
            field="verification",
            required=False,
            limit=500,
        )
        contract_revision = _stored_integer(task["current_contract_revision"])
        source_revision = _source_revision_from_values(
            kind=task["review_target_kind"],
            value=task["review_target_value"],
            base_revision=task["review_target_base_revision"],
            generation=task["review_target_generation"],
            allow_missing=True,
        )
    except (KeyError, TypeError) as exc:
        raise _invalid_stored() from exc
    return expectation, contract_revision, verification_expectation_digest(expectation), source_revision


def _public_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        receipt_id = str(row["verification_receipt_id"])
        project_id = str(row["project_id"])
        task_id = str(row["task_id"])
        contract_revision = _stored_integer(row["contract_revision"])
        digest = str(row["verification_expectation_digest"])
        command_label = _stored_text(
            row["command_label"],
            field="command_label",
            required=True,
            limit=200,
        )
        result = str(row["result"])
        duration_ms = _stored_integer(row["duration_ms"])
        coverage = str(row["scope_coverage"])
        source_revision = _source_revision_from_values(
            kind=row["target_kind"],
            value=row["target_value"],
            base_revision=row["target_base_revision"],
            generation=row["target_generation"],
            allow_missing=False,
        )
        created_at = str(row["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid_stored() from exc
    if (
        VERIFICATION_RECEIPT_ID_PATTERN.fullmatch(receipt_id) is None
        or not project_id
        or not task_id
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in command_label
        )
        or result not in VERIFICATION_RESULTS
        or coverage not in VERIFICATION_COVERAGE
        or source_revision is None
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created_at)
    ):
        raise _invalid_stored()
    public = {
        "verification_receipt_id": receipt_id,
        "project_id": project_id,
        "task_id": task_id,
        "contract_revision": contract_revision,
        "command_label": command_label,
        "result": result,
        "duration_ms": duration_ms,
        "scope_coverage": coverage,
        "source_revision": source_revision,
        "created_at": created_at,
    }
    if tuple(public) != PUBLIC_VERIFICATION_RECEIPT_FIELDS:
        raise AssertionError("verification Receipt public allow-list drifted")
    return public


def _snapshot_for_task(
    connection: sqlite3.Connection,
    *,
    task: Mapping[str, Any],
    project_id: str,
    task_id: str,
) -> tuple[Any, str, int, str, dict[str, Any] | None]:
    expectation, contract_revision, digest, source_revision = _task_basis(task)
    target = source_revision or {
        "kind": "",
        "value": "",
        "base_revision": None,
        "generation": int(task["review_target_generation"]),
    }
    snapshot = read_verification_receipt_snapshot(
        connection,
        project_id=project_id,
        task_id=task_id,
        contract_revision=contract_revision,
        verification_expectation_digest=digest,
        target_kind=str(target["kind"]),
        target_value=str(target["value"]),
        target_base_revision=str(target["base_revision"] or ""),
        target_generation=int(target["generation"]),
        recent_limit=10,
    )
    return snapshot, expectation, contract_revision, digest, source_revision


def _validated_rows(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(_public_receipt(row) for row in rows)


def _validate_snapshot_rows(
    *,
    raw_same_generation: tuple[dict[str, Any], ...],
    raw_exact: tuple[dict[str, Any], ...],
    raw_recent: tuple[dict[str, Any], ...],
    project_id: str,
    task_id: str,
    expectation: str,
    contract_revision: int,
    digest: str,
    source_revision: dict[str, Any] | None,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    same_generation = _validated_rows(raw_same_generation)
    exact = _validated_rows(raw_exact)
    recent = _validated_rows(raw_recent)
    if len(same_generation) > 1 or len(exact) > 1 or len(recent) > 10:
        raise _invalid_stored()
    if not expectation.strip() and same_generation:
        raise _invalid_stored()
    for raw, public in (
        *tuple(zip(raw_same_generation, same_generation)),
        *tuple(zip(raw_exact, exact)),
        *tuple(zip(raw_recent, recent)),
    ):
        if public["project_id"] != project_id or public["task_id"] != task_id:
            raise _invalid_stored()
    for raw, public in (
        *tuple(zip(raw_same_generation, same_generation)),
        *tuple(zip(raw_exact, exact)),
    ):
        if (
            public["contract_revision"] != contract_revision
            or str(raw.get("verification_expectation_digest")) != digest
            or public["source_revision"] != source_revision
        ):
            raise _invalid_stored()
    same_ids = tuple(
        row["verification_receipt_id"] for row in same_generation
    )
    exact_ids = tuple(row["verification_receipt_id"] for row in exact)
    if same_ids != exact_ids:
        raise _invalid_stored()
    expected_recent = tuple(
        sorted(
            recent,
            key=lambda row: (
                row["created_at"],
                row["verification_receipt_id"],
            ),
            reverse=True,
        )
    )
    if recent != expected_recent:
        raise _invalid_stored()
    return exact, recent


def _gate_from_exact(
    *,
    expectation: str,
    source_revision: dict[str, Any] | None,
    exact_rows: tuple[dict[str, Any], ...],
) -> VerificationGate:
    if not expectation.strip():
        return VerificationGate(False, True, None, None)
    if source_revision is None:
        return VerificationGate(True, False, "review_target_required", None)
    if not exact_rows:
        return VerificationGate(True, False, "verification_receipt_required", None)
    if len(exact_rows) != 1:
        raise _invalid_stored()
    row = exact_rows[0]
    if row["result"] == "pass" and row["scope_coverage"] == "full":
        return VerificationGate(
            True,
            True,
            None,
            str(row["verification_receipt_id"]),
        )
    return VerificationGate(True, False, "verification_receipt_blocking", None)


def _validate_done_cycle(
    task: Mapping[str, Any],
    cycle: CompletionCycle | None,
    *,
    expectation: str,
    contract_revision: int,
    digest: str,
    source_revision: dict[str, Any] | None,
    gate: VerificationGate,
) -> VerificationGate:
    if cycle is None:
        raise completion_history_inconsistent()
    if cycle.verification_basis_version == 0:
        if (
            cycle.verification_expectation_digest is not None
            or cycle.verification_receipt_id is not None
            or cycle.contract_revision != contract_revision
            or cycle.verification_expectation
            != ("specified" if expectation.strip() else "unspecified")
            or cycle.review_target_kind != str(task["review_target_kind"])
            or cycle.review_target_value != str(task["review_target_value"])
            or cycle.review_target_base_revision
            != str(task["review_target_base_revision"])
            or cycle.review_target_generation
            != int(task["review_target_generation"])
        ):
            raise completion_history_inconsistent()
        return VerificationGate(False, True, None, None)
    if (
        cycle.verification_basis_version != 1
        or cycle.contract_revision != contract_revision
        or cycle.verification_expectation_digest != digest
        or cycle.verification_expectation
        != ("specified" if expectation.strip() else "unspecified")
        or cycle.review_target_kind != str(task["review_target_kind"])
        or cycle.review_target_value != str(task["review_target_value"])
        or cycle.review_target_base_revision
        != str(task["review_target_base_revision"])
        or cycle.review_target_generation != int(task["review_target_generation"])
    ):
        raise completion_history_inconsistent()
    if expectation.strip():
        if (
            not gate.satisfied
            or gate.qualifying_receipt_id is None
            or cycle.verification_receipt_id != gate.qualifying_receipt_id
        ):
            raise completion_history_inconsistent()
    elif cycle.verification_receipt_id is not None:
        raise completion_history_inconsistent()
    return gate


def read_verification_evidence(
    connection: sqlite3.Connection,
    *,
    task: Mapping[str, Any],
    completion_cycle: CompletionCycle | None = None,
) -> dict[str, Any]:
    """Return the bounded JSON-only Task-show projection."""

    project_id = str(task["project_id"])
    task_id = str(task["task_id"])
    snapshot, expectation, contract_revision, digest, source_revision = _snapshot_for_task(
        connection,
        task=task,
        project_id=project_id,
        task_id=task_id,
    )
    total = _stored_integer(snapshot.total)
    exact_rows, recent_rows = _validate_snapshot_rows(
        raw_same_generation=tuple(snapshot.same_generation),
        raw_exact=tuple(snapshot.exact_current),
        raw_recent=tuple(snapshot.recent),
        project_id=project_id,
        task_id=task_id,
        expectation=expectation,
        contract_revision=contract_revision,
        digest=digest,
        source_revision=source_revision,
    )
    if total < len(exact_rows) or total < len(recent_rows):
        raise _invalid_stored()
    gate = _gate_from_exact(
        expectation=expectation,
        source_revision=source_revision,
        exact_rows=exact_rows,
    )
    if str(task["status"]) == "done":
        gate = _validate_done_cycle(
            task,
            completion_cycle,
            expectation=expectation,
            contract_revision=contract_revision,
            digest=digest,
            source_revision=source_revision,
            gate=gate,
        )
    qualifying = sum(
        row["result"] == "pass" and row["scope_coverage"] == "full"
        for row in exact_rows
    )
    blocking = len(exact_rows) - qualifying
    return {
        "expectation": expectation,
        "contract_revision": contract_revision,
        "source_revision": source_revision,
        "gate": gate.to_public(),
        "counts": {
            "receipts_total": total,
            "receipts_exact_current": len(exact_rows),
            "qualifying_exact_current": qualifying,
            "blocking_exact_current": blocking,
        },
        "recent_receipts": list(recent_rows),
    }


def current_verification_gate(
    connection: sqlite3.Connection,
    *,
    task: Mapping[str, Any],
) -> VerificationGate:
    """Evaluate only the active completion gate from one coherent DB snapshot."""

    snapshot, expectation, _revision, _digest, source_revision = _snapshot_for_task(
        connection,
        task=task,
        project_id=str(task["project_id"]),
        task_id=str(task["task_id"]),
    )
    exact_rows, _recent_rows = _validate_snapshot_rows(
        raw_same_generation=tuple(snapshot.same_generation),
        raw_exact=tuple(snapshot.exact_current),
        raw_recent=tuple(snapshot.recent),
        project_id=str(task["project_id"]),
        task_id=str(task["task_id"]),
        expectation=expectation,
        contract_revision=_revision,
        digest=_digest,
        source_revision=source_revision,
    )
    total = _stored_integer(snapshot.total)
    if total < len(exact_rows) or total < len(_recent_rows):
        raise _invalid_stored()
    return _gate_from_exact(
        expectation=expectation,
        source_revision=source_revision,
        exact_rows=exact_rows,
    )


def enforce_verification_gate(
    connection: sqlite3.Connection,
    *,
    task: Mapping[str, Any],
) -> VerificationGate:
    gate = current_verification_gate(connection, task=task)
    if gate.blocking_code == "review_target_required":
        raise _error(
            "review_target_required",
            "task completion requires a current structured review target",
            "review_target_kind",
        )
    if gate.blocking_code == "verification_receipt_required":
        raise _error(
            "verification_receipt_required",
            "current verification evidence is required",
        )
    if gate.blocking_code == "verification_receipt_blocking":
        raise _error(
            "verification_receipt_blocking",
            "current verification evidence does not satisfy the required result and coverage",
        )
    if not gate.satisfied:
        raise _invalid_stored()
    return gate


def _validate_add_basis(
    connection: sqlite3.Connection,
    *,
    task: Mapping[str, Any],
    project_id: str,
    task_id: str,
    values: VerificationReceiptInput,
) -> tuple[str, int, str, dict[str, Any]]:
    status = str(task.get("status", ""))
    if status == "done":
        raise TaskRepositoryError(
            "done_task_requires_reopen",
            "done task writes require an explicit reopen",
        )
    if status not in VERIFICATION_ACTIVE_STATUSES:
        raise _error(
            "invalid_status_transition",
            "verification evidence may be recorded only for an in-progress or review-pending task",
            "status",
        )
    try:
        expectation = _stored_text(
            task["verification"],
            field="verification",
            required=False,
            limit=500,
        )
        contract_revision = _stored_integer(task["current_contract_revision"])
    except (KeyError, TypeError) as exc:
        raise _invalid_stored() from exc
    digest = verification_expectation_digest(expectation)
    if not expectation.strip():
        raise _error(
            "verification_expectation_required",
            "task verification must be specified before recording verification evidence",
            "verification",
        )
    try:
        source_revision = _source_revision_from_values(
            kind=task["review_target_kind"],
            value=task["review_target_value"],
            base_revision=task["review_target_base_revision"],
            generation=task["review_target_generation"],
            allow_missing=True,
        )
    except (KeyError, TypeError) as exc:
        raise _invalid_stored() from exc
    if source_revision is None:
        raise _error(
            "review_target_required",
            "set a current review target before recording verification evidence",
            "review_target_kind",
        )
    if source_revision["generation"] != values.expected_target_generation:
        raise _error(
            "verification_basis_stale",
            "verification target changed after the reported run",
            "expected_target_generation",
        )
    snapshot = read_verification_receipt_snapshot(
        connection,
        project_id=project_id,
        task_id=task_id,
        contract_revision=contract_revision,
        verification_expectation_digest=digest,
        target_kind=str(source_revision["kind"]),
        target_value=str(source_revision["value"]),
        target_base_revision=str(source_revision["base_revision"] or ""),
        target_generation=int(source_revision["generation"]),
        recent_limit=10,
    )
    same_generation_rows = tuple(getattr(snapshot, "same_generation", ()))
    if not same_generation_rows:
        same_generation_rows = tuple(
            row
            for row in (*snapshot.exact_current, *snapshot.recent)
            if row.get("target_generation") == source_revision["generation"]
        )
    public_same_generation = _validated_rows(same_generation_rows)
    for row in same_generation_rows:
        if (
            str(row.get("project_id")) != project_id
            or str(row.get("task_id")) != task_id
            or int(row.get("contract_revision", -1)) != contract_revision
            or str(row.get("verification_expectation_digest")) != digest
            or _public_receipt(row)["source_revision"] != source_revision
        ):
            raise _invalid_stored()
    if public_same_generation:
        raise _error(
            "verification_receipt_already_recorded",
            "verification evidence is already recorded for the current target",
        )
    return expectation, contract_revision, digest, source_revision


def add_verification_receipt(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: Any,
    *,
    command_label: Any,
    result: Any,
    duration_ms: Any,
    scope_coverage: Any,
    expected_target_generation: Any,
    database_target: DatabaseTarget | None = None,
) -> VerificationReceiptResult:
    """Append one immutable aggregate Receipt without changing Task state."""

    normalized_task_id = validate_task_id(task_id)
    values = normalize_verification_receipt_input(
        command_label=command_label,
        result=result,
        duration_ms=duration_ms,
        scope_coverage=scope_coverage,
        expected_target_generation=expected_target_generation,
    )
    if connection.in_transaction:
        raise _error(
            "internal_error",
            "verification receipt recording requires an idle database connection",
        )
    observed = read_internal_task(connection, project.project_id, normalized_task_id)
    if observed is None:
        raise TaskRepositoryError("not_found", "task was not found")
    _validate_add_basis(
        connection,
        task=observed,
        project_id=project.project_id,
        task_id=normalized_task_id,
        values=values,
    )

    if database_target is not None:
        begin_initialized_write(connection, database_target)
    else:
        connection.execute("BEGIN IMMEDIATE")
    locked = read_internal_task(connection, project.project_id, normalized_task_id)
    if locked is None:
        raise TaskRepositoryError("not_found", "task was not found")
    _expectation, contract_revision, digest, source_revision = _validate_add_basis(
        connection,
        task=locked,
        project_id=project.project_id,
        task_id=normalized_task_id,
        values=values,
    )
    try:
        stored = insert_verification_receipt_locked(
            connection,
            project_id=project.project_id,
            task_id=normalized_task_id,
            contract_revision=contract_revision,
            verification_expectation_digest=digest,
            command_label=values.command_label,
            result=values.result,
            duration_ms=values.duration_ms,
            scope_coverage=values.scope_coverage,
            target_kind=str(source_revision["kind"]),
            target_value=str(source_revision["value"]),
            target_base_revision=str(source_revision["base_revision"] or ""),
            target_generation=int(source_revision["generation"]),
        )
    except sqlite3.IntegrityError as exc:
        raise _invalid_stored() from exc
    receipt = _public_receipt(stored)
    if receipt["source_revision"] != source_revision:
        raise _invalid_stored()
    return VerificationReceiptResult(receipt=receipt)
