"""Stored Review validation and receipt/provenance persistence.

Callers retain transaction, selection, and gate ownership. Shared stored-state
validation is resolved from storage when a repository operation is invoked.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


REVIEW_RECEIPT_ID_PATTERN = re.compile(
    r"^tg_review_receipt_[0-9a-f]{16}$"
)

REVIEW_FINDING_ID_PATTERN = re.compile(
    r"^tg_review_finding_[0-9a-f]{16}$"
)

_REVIEW_RECEIPT_INSERT_FIELDS = (
    "review_receipt_id",
    "task_id",
    "project_id",
    "reviewer_key",
    "receipt_kind",
    "verdict",
    "target_kind",
    "target_value",
    "target_base_revision",
    "target_generation",
    "summary",
    "user_approved",
    "created_at",
)


def _iter_validated_review_receipts_with_provenance(
    connection: sqlite3.Connection,
    receipt_ids: set[str] | None = None,
    *,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
):
    """Stream the closed Receipt/provenance/code relation in fixed queries."""
    from task_governance_tool.storage import (
        SQLITE_INT64_MAX,
        evidence_ledger_inconsistent,
    )


    review_privacy_successes = (
        privacy_success_cache if privacy_success_cache is not None else set()
    )
    if type(review_privacy_successes) is not set:
        raise evidence_ledger_inconsistent()
    if receipt_ids is None:
        count_row = connection.execute(
            "SELECT COUNT(*) AS count FROM review_receipts"
        ).fetchone()
        receipt_count = None if count_row is None else count_row["count"]
        selected_json = None
    else:
        receipt_count = len(receipt_ids)
        selected_json = json.dumps(
            sorted(receipt_ids),
            ensure_ascii=True,
            separators=(",", ":"),
        )
    if (
        type(receipt_count) is not int
        or receipt_count < 0
        or receipt_count >= SQLITE_INT64_MAX
    ):
        raise evidence_ledger_inconsistent()
    code_caps = _review_provenance_code_caps()
    per_receipt_code_cap = sum(code_caps.values())
    if receipt_count > (SQLITE_INT64_MAX - 1) // per_receipt_code_cap:
        raise evidence_ledger_inconsistent()
    global_code_cap = receipt_count * per_receipt_code_cap

    if selected_json is None:
        receipt_cursor = connection.execute(
            """
            SELECT * FROM review_receipts
             ORDER BY (review_provenance_id IS NOT NULL),
                      review_provenance_id,
                      review_receipt_id
             LIMIT ?
            """,
            (receipt_count + 1,),
        )
        provenance_cursor = connection.execute(
            """
            SELECT * FROM review_receipt_provenance
             ORDER BY review_provenance_id
             LIMIT ?
            """,
            (receipt_count + 1,),
        )
        provenance_iterator = iter(provenance_cursor.fetchone, None)
        code_cursor = connection.execute(
            """
            SELECT * FROM review_receipt_provenance_codes
             ORDER BY review_provenance_id,
                      CASE code_kind
                        WHEN 'profile' THEN 0
                        WHEN 'lens' THEN 1
                        WHEN 'method' THEN 2
                        ELSE 3
                      END,
                      ordinal
             LIMIT ?
            """,
            (global_code_cap + 1,),
        )
        code_iterator = iter(code_cursor.fetchone, None)
    else:
        receipt_cursor = connection.execute(
            """
            WITH selected_receipt_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT receipt.*
              FROM review_receipts AS receipt
              JOIN selected_receipt_ids AS selected
                ON selected.value = receipt.review_receipt_id
             ORDER BY (receipt.review_provenance_id IS NOT NULL),
                      receipt.review_provenance_id,
                      receipt.review_receipt_id
             LIMIT ?
            """,
            (selected_json, receipt_count + 1),
        )
        provenance_cursor = connection.execute(
            """
            WITH selected_receipt_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT provenance.*
              FROM review_receipt_provenance AS provenance
              JOIN selected_receipt_ids AS selected
                ON selected.value = provenance.review_receipt_id
             ORDER BY provenance.review_provenance_id
             LIMIT ?
            """,
            (selected_json, receipt_count + 1),
        )
        provenance_iterator = iter(provenance_cursor.fetchone, None)
        code_cursor = connection.execute(
            """
            WITH selected_receipt_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT code.*
              FROM review_receipt_provenance_codes AS code
              JOIN review_receipt_provenance AS provenance
                ON provenance.review_provenance_id = code.review_provenance_id
              JOIN selected_receipt_ids AS selected
                ON selected.value = provenance.review_receipt_id
             ORDER BY code.review_provenance_id,
                      CASE code.code_kind
                        WHEN 'profile' THEN 0
                        WHEN 'lens' THEN 1
                        WHEN 'method' THEN 2
                        ELSE 3
                      END,
                      code.ordinal
             LIMIT ?
            """,
            (selected_json, global_code_cap + 1),
        )
        code_iterator = iter(code_cursor.fetchone, None)
    next_provenance = next(provenance_iterator, None)
    next_code = next(code_iterator, None)
    observed_receipts = 0
    for receipt in iter(receipt_cursor.fetchone, None):
        observed_receipts += 1
        _validate_review_receipt_base_row(
            receipt,
            privacy_success_cache=review_privacy_successes,
        )
        receipt_id = receipt["review_receipt_id"]
        basis = receipt["review_provenance_basis_version"]
        provenance_id = receipt["review_provenance_id"]
        if (
            observed_receipts > receipt_count
            or type(receipt_id) is not str
            or not receipt_id
            or type(basis) is not int
            or basis not in {0, 1}
        ):
            raise evidence_ledger_inconsistent()
        if basis == 0:
            if provenance_id is not None:
                raise evidence_ledger_inconsistent()
            yield receipt, None
            continue
        if (
            receipt["receipt_kind"] == "not_required"
            or type(provenance_id) is not str
            or not provenance_id
            or next_provenance is None
        ):
            raise evidence_ledger_inconsistent()
        stored_provenance_id = next_provenance["review_provenance_id"]
        if (
            type(stored_provenance_id) is not str
            or stored_provenance_id != provenance_id
        ):
            raise evidence_ledger_inconsistent()

        code_rows: list[sqlite3.Row] = []
        while next_code is not None:
            code_provenance_id = next_code["review_provenance_id"]
            if type(code_provenance_id) is not str:
                raise evidence_ledger_inconsistent()
            if code_provenance_id < provenance_id:
                raise evidence_ledger_inconsistent()
            if code_provenance_id != provenance_id:
                break
            if len(code_rows) >= per_receipt_code_cap:
                raise evidence_ledger_inconsistent()
            code_rows.append(next_code)
            next_code = next(code_iterator, None)

        validated = _validate_review_provenance_relation(
            receipt,
            next_provenance,
            tuple(code_rows),
        )
        next_provenance = next(provenance_iterator, None)
        yield receipt, validated
    if (
        observed_receipts != receipt_count
        or next_provenance is not None
        or next_code is not None
    ):
        raise evidence_ledger_inconsistent()


def _validate_review_finding_base_row(
    row: sqlite3.Row,
    *,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    from task_governance_tool.storage import (
        StorageError,
        _validate_evidence_ledger_stored_privacy,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    finding_id = row["review_finding_id"]
    receipt_id = row["review_receipt_id"]
    severity = row["severity"]
    status = row["status"]
    summary = row["summary"]
    resolution_summary = row["resolution_summary"]
    created_at = row["created_at"]
    resolved_at = row["resolved_at"]
    if (
        type(finding_id) is not str
        or not 1 <= len(finding_id) <= 128
        or type(receipt_id) is not str
        or not 1 <= len(receipt_id) <= 128
        or type(severity) is not str
        or severity not in {"high", "medium", "low"}
        or type(status) is not str
        or status not in {"open", "resolved"}
        or type(summary) is not str
        or type(resolution_summary) is not str
        or type(created_at) is not str
        or (
            status == "open"
            and (resolution_summary != "" or resolved_at is not None)
        )
        or (
            status == "resolved"
            and (
                not resolution_summary
                or type(resolved_at) is not str
            )
        )
    ):
        raise evidence_ledger_inconsistent()
    cache = privacy_success_cache if privacy_success_cache is not None else set()
    if type(cache) is not set:
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_stored_privacy(
        "review_finding_summary",
        summary,
        privacy_success_cache=cache,
    )
    _validate_evidence_ledger_stored_privacy(
        "review_finding_resolution",
        resolution_summary,
        privacy_success_cache=cache,
    )
    if (
        not 1 <= len(summary) <= 1_000
        or summary != summary.strip()
        or len(resolution_summary) > 1_000
        or (
            status == "resolved"
            and resolution_summary != resolution_summary.strip()
        )
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            created_at,
            field="Review Finding creation time",
        )
        if status == "resolved":
            validate_utc_timestamp(
                resolved_at,
                field="Review Finding resolution time",
            )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _review_provenance_from_storage(
    provenance_row: sqlite3.Row,
    code_rows: tuple[sqlite3.Row, ...],
) -> dict[str, Any]:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    codes = {"profile": [], "lens": [], "method": []}
    for row in code_rows:
        kind = str(row["code_kind"])
        if kind not in codes:
            raise evidence_ledger_inconsistent()
        codes[kind].append(str(row["code"]))
    return {
        "review_provenance_id": str(provenance_row["review_provenance_id"]),
        "provenance_version": provenance_row["provenance_version"],
        "reviewer_class": provenance_row["reviewer_class"],
        "model_state": provenance_row["model_state"],
        "declared_model_id": provenance_row["declared_model_id"],
        "skill_state": provenance_row["skill_state"],
        "declared_skill_id": provenance_row["declared_skill_id"],
        "declared_skill_version": provenance_row["declared_skill_version"],
        "review_profiles": codes["profile"],
        "review_lenses": codes["lens"],
        "context_relation": provenance_row["context_relation"],
        "method_codes": codes["method"],
        "assurance_class": provenance_row["assurance_class"],
        "producer_class": provenance_row["producer_class"],
        "producer_version": provenance_row["producer_version"],
        "digest": provenance_row["digest"],
    }


def _review_provenance_code_caps() -> dict[str, int]:
    from task_governance_tool.review_provenance import (
        REVIEW_LENSES,
        REVIEW_METHODS,
        REVIEW_PROFILES,
    )

    return {
        "profile": min(4, len(REVIEW_PROFILES)),
        "lens": min(8, len(REVIEW_LENSES)),
        "method": min(8, len(REVIEW_METHODS)),
    }


def _validate_review_receipt_base_row(
    receipt: sqlite3.Row | dict[str, Any],
    *,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    from task_governance_tool.storage import (
        StorageError,
        _validate_completion_target,
        _validate_evidence_ledger_stored_privacy,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    receipt_id = receipt["review_receipt_id"]
    project_id = receipt["project_id"]
    task_id = receipt["task_id"]
    reviewer_key = receipt["reviewer_key"]
    receipt_kind = receipt["receipt_kind"]
    verdict = receipt["verdict"]
    target_kind = receipt["target_kind"]
    target_value = receipt["target_value"]
    target_base_revision = receipt["target_base_revision"]
    target_generation = receipt["target_generation"]
    summary = receipt["summary"]
    user_approved = receipt["user_approved"]
    created_at = receipt["created_at"]
    provenance_basis = receipt["review_provenance_basis_version"]
    if (
        type(receipt_id) is not str
        or not 1 <= len(receipt_id) <= 128
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(reviewer_key) is not str
        or type(receipt_kind) is not str
        or receipt_kind
        not in {"independent", "self_review_fallback", "not_required"}
        or type(verdict) is not str
        or verdict not in {"pass", "changes_requested", "not_required"}
        or type(target_kind) is not str
        or type(target_value) is not str
        or type(target_base_revision) is not str
        or type(target_generation) is not int
        or type(summary) is not str
        or type(user_approved) is not int
        or user_approved not in {0, 1}
        or type(created_at) is not str
        or type(provenance_basis) is not int
        or provenance_basis not in {0, 1}
    ):
        raise evidence_ledger_inconsistent()
    cache = privacy_success_cache if privacy_success_cache is not None else set()
    if type(cache) is not set:
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_stored_privacy(
        "reviewer_key",
        reviewer_key,
        privacy_success_cache=cache,
    )
    _validate_evidence_ledger_stored_privacy(
        "review_receipt_summary",
        summary,
        privacy_success_cache=cache,
    )
    if (
        not reviewer_key
        or reviewer_key != reviewer_key.strip()
        or len(reviewer_key) > 500
        or len(summary) > 1_000
        or (
            receipt_kind == "independent"
            and (verdict not in {"pass", "changes_requested"} or user_approved != 0)
        )
        or (
            receipt_kind == "self_review_fallback"
            and (
                verdict not in {"pass", "changes_requested"}
                or not summary.strip()
                or (verdict == "changes_requested" and user_approved != 0)
            )
        )
        or (
            receipt_kind == "not_required"
            and (
                verdict != "not_required"
                or user_approved != 0
                or not summary.strip()
            )
        )
        or (verdict == "changes_requested" and not summary.strip())
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            created_at,
            field="Review Receipt creation time",
        )
        _validate_completion_target(
            kind=target_kind,
            value=target_value,
            base_revision=target_base_revision,
            generation=target_generation,
        )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _validate_native_review_receipt_tier(
    receipt: sqlite3.Row | dict[str, Any],
    *,
    review_tier: object,
) -> None:
    """Enforce creation-time semantics from the manifest-bound snapshot."""
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )


    if type(review_tier) is not int or review_tier not in {0, 1, 2}:
        raise evidence_ledger_inconsistent()
    receipt_kind = receipt["receipt_kind"]
    verdict = receipt["verdict"]
    user_approved = receipt["user_approved"]
    if receipt_kind == "self_review_fallback":
        if (
            review_tier not in {1, 2}
            or user_approved != int(review_tier == 2 and verdict == "pass")
        ):
            raise evidence_ledger_inconsistent()
    elif receipt_kind == "not_required" and review_tier != 0:
        raise evidence_ledger_inconsistent()


def validate_stored_review_receipt_projection(
    receipt: sqlite3.Row | dict[str, Any],
    *,
    source_schema_version: object,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate one source-schema Review Receipt before public projection."""
    from task_governance_tool.storage import (
        SCHEMA_VERSION,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
    )


    if (
        type(source_schema_version) is not int
        or not 5 <= source_schema_version <= SCHEMA_VERSION
    ):
        raise evidence_ledger_inconsistent()
    try:
        stored = dict(receipt)
    except (TypeError, ValueError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    if source_schema_version < 6:
        stored["target_base_revision"] = ""
    if source_schema_version < 18:
        stored["review_provenance_basis_version"] = 0
        stored["review_provenance_id"] = None
    _validate_review_receipt_base_row(
        stored,
        privacy_success_cache=_privacy_success_cache,
    )


def validate_stored_review_finding_projection(
    finding: sqlite3.Row | dict[str, Any],
    *,
    _privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Validate one stored Review Finding before public projection."""

    _validate_review_finding_base_row(
        finding,
        privacy_success_cache=_privacy_success_cache,
    )


def _validate_review_provenance_relation(
    receipt: sqlite3.Row,
    provenance_row: sqlite3.Row,
    code_rows: tuple[sqlite3.Row, ...],
) -> dict[str, Any]:
    """Validate one exact stored Receipt/provenance/code relation."""
    from task_governance_tool.storage import (
        StorageError,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )


    provenance_id = provenance_row["review_provenance_id"]
    if (
        type(provenance_id) is not str
        or provenance_id != receipt["review_provenance_id"]
        or provenance_row["review_receipt_id"] != receipt["review_receipt_id"]
        or provenance_row["project_id"] != receipt["project_id"]
        or provenance_row["task_id"] != receipt["task_id"]
        or type(receipt["created_at"]) is not str
        or type(provenance_row["created_at"]) is not str
        or provenance_row["created_at"] != receipt["created_at"]
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            receipt["created_at"],
            field="Review Receipt creation time",
        )
        validate_utc_timestamp(
            provenance_row["created_at"],
            field="Review provenance creation time",
        )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc

    code_caps = _review_provenance_code_caps()
    group_ordinals = {kind: 0 for kind in code_caps}
    if len(code_rows) > sum(code_caps.values()):
        raise evidence_ledger_inconsistent()
    for code_row in code_rows:
        code_kind = code_row["code_kind"]
        if code_kind not in code_caps:
            raise evidence_ledger_inconsistent()
        ordinal = group_ordinals[code_kind]
        if (
            code_row["review_provenance_id"] != provenance_id
            or code_row["project_id"] != receipt["project_id"]
            or code_row["task_id"] != receipt["task_id"]
            or type(code_row["ordinal"]) is not int
            or code_row["ordinal"] != ordinal
            or ordinal >= code_caps[code_kind]
        ):
            raise evidence_ledger_inconsistent()
        group_ordinals[code_kind] = ordinal + 1

    provenance = _review_provenance_from_storage(provenance_row, code_rows)
    try:
        from task_governance_tool.review_provenance import (
            ReviewProvenanceError,
            validate_stored_review_provenance_v1,
        )

        return validate_stored_review_provenance_v1(
            provenance,
            project_id=receipt["project_id"],
            task_id=receipt["task_id"],
            review_receipt_id=receipt["review_receipt_id"],
            receipt_kind=receipt["receipt_kind"],
            target={
                "kind": receipt["target_kind"],
                "value": receipt["target_value"],
                "base_revision": receipt["target_base_revision"],
                "generation": receipt["target_generation"],
                "capture_version": 1,
            },
        )
    except ReviewProvenanceError as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def read_review_receipt_with_provenance(
    connection: sqlite3.Connection,
    *,
    review_receipt_id: str,
) -> dict[str, Any] | None:
    """Read and validate one exact Receipt plus its versioned provenance."""
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )


    receipt = connection.execute(
        "SELECT * FROM review_receipts WHERE review_receipt_id = ?",
        (review_receipt_id,),
    ).fetchone()
    if receipt is None:
        return None
    _validate_review_receipt_base_row(
        receipt,
        privacy_success_cache=set(),
    )
    basis = receipt["review_provenance_basis_version"]
    provenance_id = receipt["review_provenance_id"]
    if type(basis) is not int or basis not in {0, 1}:
        raise evidence_ledger_inconsistent()
    if basis == 0:
        if provenance_id is not None:
            raise evidence_ledger_inconsistent()
        if connection.execute(
            """
            SELECT 1 FROM review_receipt_provenance
             WHERE review_receipt_id = ?
             LIMIT 1
            """,
            (review_receipt_id,),
        ).fetchone() is not None:
            raise evidence_ledger_inconsistent()
        return {"receipt": dict(receipt), "provenance": None}
    if provenance_id is None:
        raise evidence_ledger_inconsistent()
    provenance_row = connection.execute(
        """
        SELECT * FROM review_receipt_provenance
         WHERE review_provenance_id = ?
        """,
        (provenance_id,),
    ).fetchone()
    code_cap = sum(_review_provenance_code_caps().values())
    code_rows = tuple(connection.execute(
        """
        SELECT * FROM review_receipt_provenance_codes
         WHERE review_provenance_id = ?
         ORDER BY CASE code_kind
                    WHEN 'profile' THEN 0
                    WHEN 'lens' THEN 1
                    WHEN 'method' THEN 2
                    ELSE 3
                  END,
                  ordinal
         LIMIT ?
        """,
        (provenance_id, code_cap + 1),
    ).fetchall())
    if provenance_row is None or len(code_rows) > code_cap:
        raise evidence_ledger_inconsistent()
    validated = _validate_review_provenance_relation(
        receipt,
        provenance_row,
        code_rows,
    )
    return {"receipt": dict(receipt), "provenance": validated}


def insert_review_receipt_with_provenance_locked(
    connection: sqlite3.Connection,
    receipt: dict[str, Any],
    provenance: dict[str, Any] | None,
    code_rows: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Atomically append one current Receipt and its exact provenance union."""
    from task_governance_tool.storage import (
        _read_validated_current_task_row,
        _require_evidence_writer,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
    )


    _require_evidence_writer(connection)
    if set(receipt) != set(_REVIEW_RECEIPT_INSERT_FIELDS):
        raise evidence_ledger_inconsistent()
    task = _read_validated_current_task_row(
        connection,
        project_id=str(receipt.get("project_id", "")),
        task_id=str(receipt.get("task_id", "")),
    )
    if (
        task is None
        or task["review_target_capture_version"] != 1
        or task["review_target_artifact_manifest_id"] is None
        or any(
            receipt[field] != task[f"review_target_{field.removeprefix('target_')}"]
            for field in (
                "target_kind",
                "target_value",
                "target_base_revision",
                "target_generation",
            )
        )
    ):
        raise evidence_ledger_inconsistent()
    kind = receipt["receipt_kind"]
    if kind == "not_required":
        if provenance is not None or code_rows:
            raise evidence_ledger_inconsistent()
        basis = 0
        provenance_id = None
    else:
        if kind not in {"independent", "self_review_fallback"} or provenance is None:
            raise evidence_ledger_inconsistent()
        try:
            from task_governance_tool.review_provenance import (
                ReviewProvenanceError,
                validate_stored_review_provenance_v1,
            )

            validated = validate_stored_review_provenance_v1(
                provenance,
                project_id=receipt["project_id"],
                task_id=receipt["task_id"],
                review_receipt_id=receipt["review_receipt_id"],
                receipt_kind=kind,
                target={
                    "kind": receipt["target_kind"],
                    "value": receipt["target_value"],
                    "base_revision": receipt["target_base_revision"],
                    "generation": receipt["target_generation"],
                    "capture_version": 1,
                },
            )
        except ReviewProvenanceError as exc:
            raise evidence_ledger_boundary_error(exc) from exc
        provenance_id = str(validated["review_provenance_id"])
        expected_codes = tuple(
            {
                "project_id": receipt["project_id"],
                "task_id": receipt["task_id"],
                "review_provenance_id": provenance_id,
                "code_kind": code_kind,
                "ordinal": ordinal,
                "code": code,
            }
            for code_kind, field in (
                ("profile", "review_profiles"),
                ("lens", "review_lenses"),
                ("method", "method_codes"),
            )
            for ordinal, code in enumerate(validated[field])
        )
        if code_rows != expected_codes:
            raise evidence_ledger_inconsistent()
        basis = 1

    stored_receipt = {
        **receipt,
        "review_provenance_basis_version": basis,
        "review_provenance_id": provenance_id,
    }
    _validate_review_receipt_base_row(
        stored_receipt,
        privacy_success_cache=set(),
    )
    connection.execute(
        """
        INSERT INTO review_receipts(
          review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
          verdict, target_kind, target_value, target_base_revision,
          target_generation, summary, user_approved, created_at,
          review_provenance_basis_version, review_provenance_id
        ) VALUES (
          :review_receipt_id, :task_id, :project_id, :reviewer_key, :receipt_kind,
          :verdict, :target_kind, :target_value, :target_base_revision,
          :target_generation, :summary, :user_approved, :created_at,
          :review_provenance_basis_version, :review_provenance_id
        )
        """,
        stored_receipt,
    )
    if provenance_id is not None:
        connection.execute(
            """
            INSERT INTO review_receipt_provenance(
              review_provenance_id, review_receipt_id, project_id, task_id,
              provenance_version, reviewer_class, model_state,
              declared_model_id, skill_state, declared_skill_id,
              declared_skill_version, context_relation, assurance_class,
              producer_class, producer_version, digest, created_at
            ) VALUES (
              :review_provenance_id, :review_receipt_id, :project_id, :task_id,
              :provenance_version, :reviewer_class, :model_state,
              :declared_model_id, :skill_state, :declared_skill_id,
              :declared_skill_version, :context_relation, :assurance_class,
              :producer_class, :producer_version, :digest, :created_at
            )
            """,
            {
                **provenance,
                "review_receipt_id": receipt["review_receipt_id"],
                "project_id": receipt["project_id"],
                "task_id": receipt["task_id"],
                "created_at": receipt["created_at"],
            },
        )
        for row in code_rows:
            connection.execute(
                """
                INSERT INTO review_receipt_provenance_codes(
                  project_id, task_id, review_provenance_id,
                  code_kind, ordinal, code
                ) VALUES (
                  :project_id, :task_id, :review_provenance_id,
                  :code_kind, :ordinal, :code
                )
                """,
                row,
            )
    result = read_review_receipt_with_provenance(
        connection,
        review_receipt_id=str(receipt["review_receipt_id"]),
    )
    if result is None:
        raise evidence_ledger_inconsistent()
    return result
