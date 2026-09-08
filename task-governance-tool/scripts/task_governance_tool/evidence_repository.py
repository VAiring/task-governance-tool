"""Authority, manifest, Reference, and criterion-link SQLite operations.

Callers own admission, the connection, and its transaction. Shared Evidence
assembly and Bundle validation remain in storage; these leaf operations reuse
same-snapshot validated inputs without widening their selected scope.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from typing import Any


def _criterion_id_for_text_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    kind: str,
    exact_text: str,
    created_at: str,
) -> str:
    from task_governance_tool.storage import (
        contract_criterion_digest,
        evidence_ledger_inconsistent,
    )

    digest = contract_criterion_digest(kind, exact_text)
    row = connection.execute(
        """
        SELECT criterion_id, criterion_text
          FROM contract_criteria
         WHERE project_id = ? AND task_id = ?
           AND criterion_kind = ? AND digest = ?
        """,
        (project_id, task_id, kind, digest),
    ).fetchone()
    if row is not None:
        if type(row["criterion_text"]) is not str or row["criterion_text"] != exact_text:
            raise evidence_ledger_inconsistent()
        return str(row["criterion_id"])
    criterion_id = f"tg_contract_criterion_{secrets.token_hex(8)}"
    connection.execute(
        """
        INSERT INTO contract_criteria(
          criterion_id, project_id, task_id, criterion_kind,
          criterion_text, digest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            criterion_id,
            project_id,
            task_id,
            kind,
            exact_text,
            digest,
            created_at,
        ),
    )
    return criterion_id


def capture_or_reuse_current_authority_snapshot_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    producer_class: str = "taskgov_core",
    created_at: str | None = None,
) -> _storage.AuthoritySnapshotBinding:
    """Capture the exact locked Task/Contract basis without target mutation."""

    from task_governance_tool.storage import (
        AuthoritySnapshotBinding,
        SQLITE_INT64_MAX,
        _read_task_for_authority_capture,
        _require_evidence_writer,
        _verification_expectation_digest,
        authority_snapshot_basis_digest,
        evidence_ledger_inconsistent,
        utc_now,
        validate_utc_timestamp,
    )

    _require_evidence_writer(connection)
    if producer_class not in {"taskgov_core", "legacy_migration"}:
        raise evidence_ledger_inconsistent()
    timestamp = validate_utc_timestamp(
        created_at or utc_now(),
        field="authority snapshot creation time",
    )
    task = _read_task_for_authority_capture(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if task is None:
        raise evidence_ledger_inconsistent()
    revision = task["current_contract_revision"]
    if type(revision) is not int or revision < 0:
        raise evidence_ledger_inconsistent()
    if revision == 0:
        contract_state = "contract_unspecified"
        scope = acceptance = constraints = authority_ref = ""
    else:
        contract = connection.execute(
            """
            SELECT scope, acceptance, constraints_text, authority_ref
              FROM task_contract_revisions
             WHERE project_id = ? AND task_id = ? AND revision = ?
            """,
            (project_id, task_id, revision),
        ).fetchone()
        if contract is None or any(
            type(contract[name]) is not str
            for name in ("scope", "acceptance", "constraints_text", "authority_ref")
        ):
            raise evidence_ledger_inconsistent()
        contract_state = "contract_specified"
        scope = str(contract["scope"])
        acceptance = str(contract["acceptance"])
        constraints = str(contract["constraints_text"])
        authority_ref = str(contract["authority_ref"])
    verification = task["verification"]
    if type(verification) is not str or len(verification) > 1_000:
        raise evidence_ledger_inconsistent()
    acceptance_criterion_id = (
        _criterion_id_for_text_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
            kind="acceptance",
            exact_text=acceptance,
            created_at=timestamp,
        )
        if revision > 0 and acceptance
        else None
    )
    verification_criterion_id = (
        _criterion_id_for_text_locked(
            connection,
            project_id=project_id,
            task_id=task_id,
            kind="verification",
            exact_text=verification,
            created_at=timestamp,
        )
        if verification.strip()
        else None
    )
    digest_values = {
        "project_id": project_id,
        "task_id": task_id,
        "task_title": str(task["title"]),
        "task_description": str(task["description"]),
        "review_tier": int(task["review_tier"]),
        "verification": verification,
        "verification_digest": _verification_expectation_digest(verification),
        "contract_revision": revision,
        "contract_state": contract_state,
        "contract_scope": scope,
        "contract_acceptance": acceptance,
        "contract_constraints": constraints,
        "contract_authority_ref": authority_ref,
        "acceptance_criterion_id": acceptance_criterion_id,
        "verification_criterion_id": verification_criterion_id,
        "producer_class": producer_class,
        "producer_version": 1,
    }
    basis_digest = authority_snapshot_basis_digest(digest_values)
    current = connection.execute(
        """
        SELECT authority_snapshot_id, generation, basis_digest
          FROM authority_snapshots
         WHERE project_id = ? AND task_id = ?
         ORDER BY generation DESC LIMIT 1
        """,
        (project_id, task_id),
    ).fetchone()
    current_pointer_id = task["current_authority_snapshot_id"]
    current_pointer_generation = task["current_authority_snapshot_generation"]
    if current is None:
        if current_pointer_id is not None or current_pointer_generation != 0:
            raise evidence_ledger_inconsistent()
        current_generation = 0
    else:
        current_generation = current["generation"]
        if (
            type(current_generation) is not int
            or current_generation <= 0
            or current_pointer_id != current["authority_snapshot_id"]
            or current_pointer_generation != current_generation
        ):
            raise evidence_ledger_inconsistent()
    if current is not None and current["basis_digest"] == basis_digest:
        snapshot_id = current["authority_snapshot_id"]
        generation = current_generation
    else:
        if current_generation >= SQLITE_INT64_MAX:
            raise evidence_ledger_inconsistent()
        generation = current_generation + 1
        if not 1 <= generation <= SQLITE_INT64_MAX:
            raise evidence_ledger_inconsistent()
        snapshot_id = f"tg_authority_snapshot_{secrets.token_hex(8)}"
        connection.execute(
            """
            INSERT INTO authority_snapshots(
              authority_snapshot_id, project_id, task_id, generation,
              task_title, task_description, review_tier, verification,
              verification_digest, contract_revision, contract_state,
              contract_scope, contract_acceptance, contract_constraints,
              contract_authority_ref, basis_digest, producer_class,
              producer_version, created_at
            ) VALUES (
              :authority_snapshot_id, :project_id, :task_id, :generation,
              :task_title, :task_description, :review_tier, :verification,
              :verification_digest, :contract_revision, :contract_state,
              :contract_scope, :contract_acceptance, :contract_constraints,
              :contract_authority_ref, :basis_digest, :producer_class,
              :producer_version, :created_at
            )
            """,
            {
                "authority_snapshot_id": snapshot_id,
                "generation": generation,
                "basis_digest": basis_digest,
                "created_at": timestamp,
                **digest_values,
            },
        )
        for kind, criterion_id in (
            ("acceptance", acceptance_criterion_id),
            ("verification", verification_criterion_id),
        ):
            if criterion_id is not None:
                connection.execute(
                    """
                    INSERT INTO authority_snapshot_criteria(
                      project_id, task_id, authority_snapshot_id,
                      criterion_kind, criterion_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, task_id, snapshot_id, kind, criterion_id),
                )
    connection.execute(
        """
        UPDATE tasks
           SET current_authority_snapshot_id = ?,
               current_authority_snapshot_generation = ?
         WHERE project_id = ? AND task_id = ?
        """,
        (snapshot_id, generation, project_id, task_id),
    )
    return AuthoritySnapshotBinding(
        authority_snapshot_id=snapshot_id,
        generation=generation,
        acceptance_criterion_id=acceptance_criterion_id,
        verification_criterion_id=verification_criterion_id,
    )


def _snapshot_criterion_links(
    connection: sqlite3.Connection,
    snapshot_ids: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    result: dict[str, dict[str, str]] = {}
    if snapshot_ids is None:
        rows = connection.execute(
            """
            SELECT project_id, task_id, authority_snapshot_id,
                   criterion_kind, criterion_id
              FROM authority_snapshot_criteria
             ORDER BY authority_snapshot_id, criterion_kind
            """
        ).fetchall()
    else:
        selected_json = json.dumps(
            sorted(snapshot_ids),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        rows = connection.execute(
            """
            WITH selected_snapshot_ids(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT link.project_id, link.task_id,
                   link.authority_snapshot_id,
                   link.criterion_kind, link.criterion_id
              FROM authority_snapshot_criteria AS link
              JOIN selected_snapshot_ids AS selected
                ON selected.value = link.authority_snapshot_id
             ORDER BY link.authority_snapshot_id, link.criterion_kind
            """,
            (selected_json,),
        ).fetchall()
    for row in rows:
        project_id = row["project_id"]
        task_id = row["task_id"]
        snapshot_id = row["authority_snapshot_id"]
        kind = row["criterion_kind"]
        criterion_id = row["criterion_id"]
        if (
            type(project_id) is not str
            or not project_id
            or type(task_id) is not str
            or not task_id
            or type(snapshot_id) is not str
            or re.fullmatch(
                r"tg_authority_snapshot_[0-9a-f]{16}", snapshot_id
            )
            is None
            or type(kind) is not str
            or kind not in {"acceptance", "verification"}
            or type(criterion_id) is not str
            or re.fullmatch(
                r"tg_contract_criterion_[0-9a-f]{16}", criterion_id
            )
            is None
        ):
            raise evidence_ledger_inconsistent()
        links = result.setdefault(snapshot_id, {})
        if kind in links:
            raise evidence_ledger_inconsistent()
        links[kind] = criterion_id
    return result


def _artifact_manifest_target_key(
    *,
    project_id: object,
    task_id: object,
    target_kind: object,
    target_value: object,
    target_base_revision: object,
    target_generation: object,
) -> tuple[str, str, str, str, str, int]:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    if (
        type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(target_kind) is not str
        or type(target_value) is not str
        or type(target_base_revision) is not str
        or type(target_generation) is not int
        or target_generation <= 0
    ):
        raise evidence_ledger_inconsistent()
    return (
        project_id,
        task_id,
        target_kind,
        target_value,
        target_base_revision,
        target_generation,
    )


def _validate_artifact_manifest_storage(
    connection: sqlite3.Connection,
    *,
    snapshots: dict[str, sqlite3.Row],
    links: dict[str, dict[str, str]],
    manifest_ids: set[str] | None = None,
) -> tuple[
    dict[str, _storage._ValidatedManifestRecord],
    dict[tuple[str, str, str, str, str, int], _storage._ValidatedManifestRecord],
]:
    """Validate every bounded manifest and entry row from stored bytes."""

    from task_governance_tool.storage import (
        ARTIFACT_MANIFEST_ID_PATTERN,
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        SQLITE_INT64_MAX,
        StorageError,
        _ValidatedManifestRecord,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    from task_governance_tool.artifact_manifest import (
        ARTIFACT_ENTRY_LIMIT,
        ArtifactManifestEntry,
        ArtifactManifestError,
        ArtifactManifestSpec,
    )
    from task_governance_tool.evidence_ledger import (
        EvidenceLedgerError,
        EvidenceSource,
        TargetCaptureBinding,
        canonical_json_bytes,
    )

    by_id: dict[str, _ValidatedManifestRecord] = {}
    by_target: dict[
        tuple[str, str, str, str, str, int], _ValidatedManifestRecord
    ] = {}
    try:
        if manifest_ids is not None and (
            type(manifest_ids) is not set
            or len(manifest_ids) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            or any(
                type(manifest_id) is not str
                or len(manifest_id) > 128
                or ARTIFACT_MANIFEST_ID_PATTERN.fullmatch(manifest_id) is None
                for manifest_id in manifest_ids
            )
        ):
            raise evidence_ledger_inconsistent()
        manifest_rows: list[dict[str, Any]] = []
        manifest_headers: dict[str, dict[str, Any]] = {}
        declared_entry_total = 0
        if manifest_ids is None:
            stored_manifests = connection.execute(
                "SELECT * FROM artifact_manifests ORDER BY artifact_manifest_id"
            ).fetchall()
        else:
            selected_json = json.dumps(
                sorted(manifest_ids),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            stored_manifests = connection.execute(
                """
                WITH selected_manifest_ids(value) AS (
                    SELECT value FROM json_each(?)
                ), selected_manifest_aliases(value) AS (
                    SELECT value FROM selected_manifest_ids
                    UNION ALL
                    SELECT CAST(value AS BLOB) FROM selected_manifest_ids
                )
                SELECT manifest.*
                  FROM artifact_manifests AS manifest
                  JOIN selected_manifest_aliases AS selected
                    ON selected.value = manifest.artifact_manifest_id
                 ORDER BY manifest.artifact_manifest_id
                 LIMIT ?
                """,
                (selected_json, len(manifest_ids) + 1),
            ).fetchall()
        for stored in stored_manifests:
            row = dict(stored)
            manifest_id = row["artifact_manifest_id"]
            if (
                type(manifest_id) is not str
                or re.fullmatch(
                    r"tg_artifact_manifest_[0-9a-f]{16}", manifest_id
                )
                is None
                or manifest_id in manifest_headers
                or type(row["entry_count"]) is not int
                or not 0 <= row["entry_count"] <= ARTIFACT_ENTRY_LIMIT
                or type(row["created_at"]) is not str
                or declared_entry_total
                > SQLITE_INT64_MAX - 1 - row["entry_count"]
            ):
                raise evidence_ledger_inconsistent()
            validate_utc_timestamp(
                row["created_at"],
                field="artifact manifest creation time",
            )
            manifest_rows.append(row)
            manifest_headers[manifest_id] = row
            declared_entry_total += row["entry_count"]

        if manifest_ids is None:
            entry_cursor = connection.execute(
                """
                SELECT * FROM artifact_manifest_entries
                 ORDER BY artifact_manifest_id, ordinal
                 LIMIT ?
                """,
                (declared_entry_total + 1,),
            )
        else:
            entry_cursor = connection.execute(
                """
                WITH selected_manifest_ids(value) AS (
                    SELECT value FROM json_each(?)
                ), selected_manifest_aliases(value) AS (
                    SELECT value FROM selected_manifest_ids
                    UNION ALL
                    SELECT CAST(value AS BLOB) FROM selected_manifest_ids
                )
                SELECT entry.*
                  FROM artifact_manifest_entries AS entry
                  JOIN selected_manifest_aliases AS selected
                    ON selected.value = entry.artifact_manifest_id
                 ORDER BY entry.artifact_manifest_id, entry.ordinal
                 LIMIT ?
                """,
                (selected_json, declared_entry_total + 1),
            )
        entry_iterator = iter(entry_cursor.fetchone, None)
        next_entry = next(entry_iterator, None)
        observed_entry_total = 0

        for row in manifest_rows:
            manifest_id = row["artifact_manifest_id"]
            entry_rows_buffer: list[sqlite3.Row] = []
            while next_entry is not None:
                entry_manifest_id = next_entry["artifact_manifest_id"]
                if type(entry_manifest_id) is not str:
                    raise evidence_ledger_inconsistent()
                if entry_manifest_id < manifest_id:
                    raise evidence_ledger_inconsistent()
                if entry_manifest_id != manifest_id:
                    break
                if (
                    next_entry["project_id"] != row["project_id"]
                    or next_entry["task_id"] != row["task_id"]
                    or type(next_entry["ordinal"]) is not int
                    or next_entry["ordinal"] != len(entry_rows_buffer)
                    or len(entry_rows_buffer) >= row["entry_count"]
                    or len(entry_rows_buffer) >= ARTIFACT_ENTRY_LIMIT
                ):
                    raise evidence_ledger_inconsistent()
                entry_rows_buffer.append(next_entry)
                observed_entry_total += 1
                next_entry = next(entry_iterator, None)
            entry_rows = tuple(entry_rows_buffer)
            if len(entry_rows) != row["entry_count"]:
                raise evidence_ledger_inconsistent()
            entries = tuple(
                ArtifactManifestEntry(
                    ordinal=entry["ordinal"],
                    kind=entry["entry_kind"],
                    old_path=entry["old_path"],
                    new_path=entry["new_path"],
                    before_mode=entry["before_mode"],
                    before_object_id=entry["before_object_id"],
                    after_mode=entry["after_mode"],
                    after_object_id=entry["after_object_id"],
                )
                for entry in entry_rows
            )
            canonical_value = {
                "acceptance_criterion_id": row["acceptance_criterion_id"],
                "authority_snapshot_id": row["authority_snapshot_id"],
                "comparison_base": row["comparison_base"],
                "entries": [entry.canonical_value() for entry in entries],
                "object_format": row["object_format"],
                "omission_code": row["omission_code"],
                "state": row["state"],
                "target_base_revision": row["target_base_revision"],
                "target_generation": row["target_generation"],
                "target_kind": row["target_kind"],
                "target_value": row["target_value"],
                "verification_criterion_id": row[
                    "verification_criterion_id"
                ],
            }
            spec = ArtifactManifestSpec(
                state=row["state"],
                object_format=row["object_format"],
                comparison_base=row["comparison_base"],
                target_kind=row["target_kind"],
                target_value=row["target_value"],
                target_base_revision=row["target_base_revision"],
                target_generation=row["target_generation"],
                authority_snapshot_id=row["authority_snapshot_id"],
                acceptance_criterion_id=row["acceptance_criterion_id"],
                verification_criterion_id=row[
                    "verification_criterion_id"
                ],
                omission_code=row["omission_code"],
                entries=entries,
                digest=row["digest"],
                canonical_size=len(canonical_json_bytes(canonical_value)),
            )
            binding = TargetCaptureBinding(
                target_kind=spec.target_kind,
                target_value=spec.target_value,
                target_base_revision=spec.target_base_revision,
                target_generation=spec.target_generation,
                authority_snapshot_id=spec.authority_snapshot_id,
                acceptance_criterion_id=spec.acceptance_criterion_id,
                verification_criterion_id=spec.verification_criterion_id,
            )
            snapshot = snapshots.get(spec.authority_snapshot_id)
            snapshot_links = links.get(spec.authority_snapshot_id, {})
            if (
                snapshot is None
                or snapshot["project_id"] != row["project_id"]
                or snapshot["task_id"] != row["task_id"]
                or snapshot_links.get("acceptance")
                != spec.acceptance_criterion_id
                or snapshot_links.get("verification")
                != spec.verification_criterion_id
                or type(snapshot["contract_revision"]) is not int
            ):
                raise evidence_ledger_inconsistent()
            source_projection = (
                {
                    "artifact_manifest_id": manifest_id,
                    "state": spec.state,
                    "object_format": spec.object_format,
                    "comparison_base": spec.comparison_base,
                    "entry_count": spec.entry_count,
                    "digest": spec.digest,
                    "omission_code": spec.omission_code,
                }
                if spec.state == "complete_git"
                else {
                    "artifact_manifest_id": manifest_id,
                    "state": spec.state,
                    "target_kind": spec.target_kind,
                    "digest": spec.digest,
                    "omission_code": spec.omission_code,
                }
            )
            source = EvidenceSource(
                source_kind="artifact_manifest",
                source_state=spec.state,
                source_id=manifest_id,
                source_projection=source_projection,
            )
            record = _ValidatedManifestRecord(
                row=row,
                binding=binding,
                source=source,
                contract_revision=snapshot["contract_revision"],
            )
            target_key = _artifact_manifest_target_key(
                project_id=row["project_id"],
                task_id=row["task_id"],
                target_kind=spec.target_kind,
                target_value=spec.target_value,
                target_base_revision=spec.target_base_revision,
                target_generation=spec.target_generation,
            )
            if target_key in by_target:
                raise evidence_ledger_inconsistent()
            by_id[manifest_id] = record
            by_target[target_key] = record
        if (
            next_entry is not None
            or observed_entry_total != declared_entry_total
            or (manifest_ids is not None and set(by_id) != manifest_ids)
        ):
            raise evidence_ledger_inconsistent()
    except (ArtifactManifestError, EvidenceLedgerError, StorageError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    return by_id, by_target


def _register_expected_evidence_reference(
    expected: dict[tuple[str, str], _storage._ExpectedEvidenceReference],
    value: _storage._ExpectedEvidenceReference,
) -> None:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    key = (value.source.source_kind, value.source.source_id)
    if key in expected:
        raise evidence_ledger_inconsistent()
    expected[key] = value


def _validate_stored_evidence_reference_row(
    row: sqlite3.Row,
    *,
    expected: dict[tuple[str, str], _storage._ExpectedEvidenceReference],
    seen: set[tuple[str, str]],
) -> None:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    from task_governance_tool.evidence_ledger import build_evidence_reference

    reference_id = row["evidence_reference_id"]
    if (
        type(reference_id) is not str
        or re.fullmatch(
            r"tg_evidence_reference_[0-9a-f]{16}", reference_id
        )
        is None
        or type(row["source_kind"]) is not str
        or type(row["source_id"]) is not str
        or type(row["created_at"]) is not str
    ):
        raise evidence_ledger_inconsistent()
    validate_utc_timestamp(
        row["created_at"], field="Evidence Reference creation time"
    )
    key = (row["source_kind"], row["source_id"])
    value = expected.get(key)
    if value is None or key in seen:
        raise evidence_ledger_inconsistent()
    binding = value.binding
    if (
        row["project_id"] != value.project_id
        or row["task_id"] != value.task_id
        or row["source_state"] != value.source.source_state
        or row["contract_revision"] != value.contract_revision
        or row["authority_snapshot_id"] != binding.authority_snapshot_id
        or row["acceptance_criterion_id"] != binding.acceptance_criterion_id
        or row["verification_criterion_id"]
        != binding.verification_criterion_id
        or row["target_kind"] != binding.target_kind
        or row["target_value"] != binding.target_value
        or row["target_base_revision"] != binding.target_base_revision
        or row["target_generation"] != binding.target_generation
        or row["completion_cycle_id"] != value.completion_cycle_id
    ):
        raise evidence_ledger_inconsistent()
    rebuilt = build_evidence_reference(
        source=value.source,
        project_id=value.project_id,
        task_id=value.task_id,
        contract_revision=value.contract_revision,
        binding=binding,
        completion_cycle_id=value.completion_cycle_id,
    )
    if (
        row["assurance_class"] != rebuilt.attribution.assurance_class
        or row["producer_class"] != rebuilt.attribution.producer_class
        or row["producer_version"] != rebuilt.attribution.producer_version
        or row["digest"] != rebuilt.digest
    ):
        raise evidence_ledger_inconsistent()
    seen.add(key)


def _validate_authority_snapshot_storage_classes(
    row: sqlite3.Row,
    *,
    privacy_success_cache: set[tuple[str, str, str]],
) -> None:
    from task_governance_tool.storage import (
        LOWER_HEX_64_PATTERN,
        SQLITE_INT64_MAX,
        StorageError,
        _validate_evidence_ledger_stored_privacy,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    snapshot_id = row["authority_snapshot_id"]
    project_id = row["project_id"]
    task_id = row["task_id"]
    generation = row["generation"]
    task_title = row["task_title"]
    task_description = row["task_description"]
    review_tier = row["review_tier"]
    verification = row["verification"]
    verification_digest = row["verification_digest"]
    contract_revision = row["contract_revision"]
    contract_state = row["contract_state"]
    contract_scope = row["contract_scope"]
    contract_acceptance = row["contract_acceptance"]
    contract_constraints = row["contract_constraints"]
    contract_authority_ref = row["contract_authority_ref"]
    basis_digest = row["basis_digest"]
    producer_class = row["producer_class"]
    producer_version = row["producer_version"]
    created_at = row["created_at"]
    if (
        type(snapshot_id) is not str
        or re.fullmatch(r"tg_authority_snapshot_[0-9a-f]{16}", snapshot_id)
        is None
        or type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(generation) is not int
        or not 1 <= generation <= SQLITE_INT64_MAX
        or type(task_title) is not str
        or not 1 <= len(task_title) <= 200
        or type(task_description) is not str
        or len(task_description) > 4_000
        or type(review_tier) is not int
        or review_tier not in {0, 1, 2}
        or type(verification) is not str
        or len(verification) > 1_000
        or type(verification_digest) is not str
        or LOWER_HEX_64_PATTERN.fullmatch(verification_digest) is None
        or type(contract_revision) is not int
        or not 0 <= contract_revision <= SQLITE_INT64_MAX
        or type(contract_state) is not str
        or contract_state not in {"contract_specified", "contract_unspecified"}
        or type(contract_scope) is not str
        or len(contract_scope) > 4_000
        or type(contract_acceptance) is not str
        or len(contract_acceptance) > 4_000
        or type(contract_constraints) is not str
        or len(contract_constraints) > 2_000
        or type(contract_authority_ref) is not str
        or len(contract_authority_ref) > 500
        or type(basis_digest) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", basis_digest) is None
        or type(producer_class) is not str
        or producer_class not in {"taskgov_core", "legacy_migration"}
        or type(producer_version) is not int
        or producer_version != 1
        or type(created_at) is not str
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            created_at,
            field="authority snapshot creation time",
        )
    except StorageError as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    for field, value in (
        ("title", task_title),
        ("description", task_description),
        ("verification", verification),
        ("contract_scope", contract_scope),
        ("contract_acceptance", contract_acceptance),
        ("contract_authority_ref", contract_authority_ref),
    ):
        _validate_evidence_ledger_stored_privacy(
            field,
            value,
            privacy_success_cache=privacy_success_cache,
        )
    _validate_evidence_ledger_stored_privacy(
        "contract_constraints",
        contract_constraints,
        privacy_success_cache=privacy_success_cache,
        legacy_m19_7_stored=True,
    )


def _validated_contract_criteria_rows(
    rows: list[sqlite3.Row],
    *,
    expected_ids: set[str],
    privacy_success_cache: set[tuple[str, str, str]],
) -> dict[str, sqlite3.Row]:
    from task_governance_tool.storage import (
        StorageError,
        _validate_evidence_ledger_stored_privacy,
        contract_criterion_digest,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    criteria: dict[str, sqlite3.Row] = {}
    for row in rows:
        criterion_id = row["criterion_id"]
        project_id = row["project_id"]
        task_id = row["task_id"]
        kind = row["criterion_kind"]
        text = row["criterion_text"]
        if (
            type(criterion_id) is not str
            or not re.fullmatch(
                r"tg_contract_criterion_[0-9a-f]{16}", criterion_id
            )
            or criterion_id in criteria
            or criterion_id not in expected_ids
            or type(project_id) is not str
            or not project_id
            or type(task_id) is not str
            or not task_id
            or type(kind) is not str
            or kind not in {"acceptance", "verification"}
            or type(text) is not str
            or type(row["created_at"]) is not str
            or type(row["digest"]) is not str
            or (kind == "verification" and (not text.strip() or len(text) > 1_000))
            or (kind == "acceptance" and (not text or len(text) > 4_000))
        ):
            raise evidence_ledger_inconsistent()
        _validate_evidence_ledger_stored_privacy(
            "verification" if kind == "verification" else "contract_acceptance",
            text,
            privacy_success_cache=privacy_success_cache,
        )
        if row["digest"] != contract_criterion_digest(kind, text):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                row["created_at"],
                field="Contract criterion creation time",
            )
        except StorageError as exc:
            raise evidence_ledger_boundary_error(exc) from exc
        criteria[criterion_id] = row
    if set(criteria) != expected_ids:
        raise evidence_ledger_inconsistent()
    return criteria


def _validated_authority_context(
    connection: sqlite3.Connection,
    *,
    snapshot_ids: set[str] | None = None,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> _storage._ValidatedAuthorityContext:
    """Validate all or one selected historical authority subgraph."""

    from task_governance_tool.storage import (
        _ValidatedAuthorityContext,
        _selected_contract_revision_rows,
        _validated_contract_revision_rows,
        _verification_expectation_digest,
        authority_snapshot_basis_digest,
        evidence_ledger_inconsistent,
        evidence_ledger_sqlite_error,
    )

    selected_json = (
        json.dumps(
            sorted(snapshot_ids),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if snapshot_ids is not None
        else None
    )
    try:
        if selected_json is None:
            snapshot_rows = connection.execute(
                """
                SELECT * FROM authority_snapshots
                 ORDER BY project_id, task_id, generation
                """
            ).fetchall()
        else:
            snapshot_rows = connection.execute(
                """
                WITH selected_snapshot_ids(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT snapshot.*
                  FROM authority_snapshots AS snapshot
                  JOIN selected_snapshot_ids AS selected
                    ON selected.value = snapshot.authority_snapshot_id
                 ORDER BY snapshot.project_id, snapshot.task_id,
                          snapshot.generation
                """,
                (selected_json,),
            ).fetchall()
        links = _snapshot_criterion_links(connection, snapshot_ids)
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc

    if privacy_success_cache is None:
        privacy_success_cache = set()
    observed_ordinary_privacy_successes: set[tuple[str, str]] = set()
    required_contract_keys: set[tuple[str, str, int]] = set()
    observed_snapshot_ids: set[str] = set()
    for row in snapshot_rows:
        _validate_authority_snapshot_storage_classes(
            row,
            privacy_success_cache=privacy_success_cache,
        )
        observed_ordinary_privacy_successes.update(
            (field_name, row[column_name])
            for field_name, column_name in (
                ("title", "task_title"),
                ("description", "task_description"),
                ("verification", "verification"),
            )
        )
        observed_snapshot_ids.add(row["authority_snapshot_id"])
        if row["contract_revision"] > 0:
            required_contract_keys.add(
                (row["project_id"], row["task_id"], row["contract_revision"])
            )
    if snapshot_ids is not None and observed_snapshot_ids != snapshot_ids:
        raise evidence_ledger_inconsistent()
    if not set(links).issubset(observed_snapshot_ids):
        raise evidence_ledger_inconsistent()

    linked_criterion_ids = {
        criterion_id
        for snapshot_links in links.values()
        for criterion_id in snapshot_links.values()
    }
    try:
        if snapshot_ids is None:
            criterion_rows = connection.execute(
                "SELECT * FROM contract_criteria ORDER BY criterion_id"
            ).fetchall()
        else:
            criteria_json = json.dumps(
                sorted(linked_criterion_ids),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            criterion_rows = connection.execute(
                """
                WITH selected_criterion_ids(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT criterion.*
                  FROM contract_criteria AS criterion
                  JOIN selected_criterion_ids AS selected
                    ON selected.value = criterion.criterion_id
                 ORDER BY criterion.criterion_id
                """,
                (criteria_json,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc
    criteria = _validated_contract_criteria_rows(
        criterion_rows,
        expected_ids=linked_criterion_ids,
        privacy_success_cache=privacy_success_cache,
    )
    contracts_by_revision = _validated_contract_revision_rows(
        _selected_contract_revision_rows(connection, required_contract_keys),
        expected_keys=required_contract_keys,
    )

    snapshots: dict[str, sqlite3.Row] = {}
    generation_state: dict[tuple[str, str], tuple[int, int]] = {}
    for row in snapshot_rows:
        snapshot_id = row["authority_snapshot_id"]
        snapshot_links = links.get(snapshot_id, {})
        acceptance_id = snapshot_links.get("acceptance")
        verification_id = snapshot_links.get("verification")
        verification = row["verification"]
        contract_revision = row["contract_revision"]
        contract = (
            contracts_by_revision.get(
                (row["project_id"], row["task_id"], contract_revision)
            )
            if contract_revision > 0
            else None
        )
        expected_contract = (
            (
                "contract_specified",
                contract["scope"],
                contract["acceptance"],
                contract["constraints_text"],
                contract["authority_ref"],
            )
            if contract is not None
            else ("contract_unspecified", "", "", "", "")
        )
        if (
            snapshot_id in snapshots
            or row["verification_digest"]
            != _verification_expectation_digest(verification)
            or (bool(verification.strip()) != (verification_id is not None))
            or contract_revision > 0 and contract is None
            or (
                row["contract_state"],
                row["contract_scope"],
                row["contract_acceptance"],
                row["contract_constraints"],
                row["contract_authority_ref"],
            )
            != expected_contract
            or (contract_revision == 0 and acceptance_id is not None)
            or (contract_revision > 0 and acceptance_id is None)
        ):
            raise evidence_ledger_inconsistent()
        for kind, criterion_id in snapshot_links.items():
            criterion = criteria.get(criterion_id)
            if (
                criterion is None
                or criterion["project_id"] != row["project_id"]
                or criterion["task_id"] != row["task_id"]
                or criterion["criterion_kind"] != kind
                or criterion["criterion_text"]
                != (
                    row["contract_acceptance"]
                    if kind == "acceptance"
                    else row["verification"]
                )
            ):
                raise evidence_ledger_inconsistent()
        digest_values = {
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "task_title": row["task_title"],
            "task_description": row["task_description"],
            "review_tier": row["review_tier"],
            "verification": verification,
            "verification_digest": row["verification_digest"],
            "contract_revision": contract_revision,
            "contract_state": row["contract_state"],
            "contract_scope": row["contract_scope"],
            "contract_acceptance": row["contract_acceptance"],
            "contract_constraints": row["contract_constraints"],
            "contract_authority_ref": row["contract_authority_ref"],
            "acceptance_criterion_id": acceptance_id,
            "verification_criterion_id": verification_id,
            "producer_class": row["producer_class"],
            "producer_version": row["producer_version"],
        }
        if row["basis_digest"] != authority_snapshot_basis_digest(digest_values):
            raise evidence_ledger_inconsistent()
        generation_key = (row["project_id"], row["task_id"])
        generation_count, previous_generation = generation_state.get(
            generation_key,
            (0, 0),
        )
        if snapshot_ids is None and row["generation"] != previous_generation + 1:
            raise evidence_ledger_inconsistent()
        snapshots[snapshot_id] = row
        generation_state[generation_key] = (
            generation_count + 1,
            row["generation"],
        )
    return _ValidatedAuthorityContext(
        snapshots=snapshots,
        criteria=criteria,
        links=links,
        generation_state=generation_state,
        contracts_by_revision=contracts_by_revision,
        ordinary_privacy_successes=frozenset(
            observed_ordinary_privacy_successes
        ),
    )


def _criterion_link_relation_valid(
    *,
    criterion_kind: object,
    source_kind: object,
    relation: object,
) -> bool:
    return (
        relation == "verification_attestation"
        and criterion_kind == "verification"
        and source_kind == "verification_receipt"
    ) or (
        relation == "review_assessment"
        and criterion_kind == "acceptance"
        and source_kind == "review_receipt"
    ) or (
        relation == "review_finding"
        and criterion_kind == "acceptance"
        and source_kind == "review_finding"
    ) or (
        relation == "completion_basis"
        and criterion_kind == "acceptance"
        and source_kind in {"artifact_manifest", "completion_evidence"}
    ) or (
        relation == "runner_observation"
        and criterion_kind == "verification"
        and source_kind == "runner_observation"
    )


def persist_artifact_manifest_locked(
    connection: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    entries: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Persist one already-normalized manifest; derivation remains service-owned."""

    from task_governance_tool.storage import (
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS,
        _require_evidence_writer,
        evidence_ledger_inconsistent,
    )

    _require_evidence_writer(connection)
    manifest_fields = tuple(sorted(
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS["artifact_manifests"]
    ))
    if set(manifest) != set(manifest_fields):
        raise evidence_ledger_inconsistent()
    entry_fields = tuple(sorted(
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS["artifact_manifest_entries"]
    ))
    if any(set(entry) != set(entry_fields) for entry in entries):
        raise evidence_ledger_inconsistent()
    columns = ", ".join(manifest_fields)
    values = ", ".join(f":{name}" for name in manifest_fields)
    connection.execute(
        f"INSERT INTO artifact_manifests({columns}) VALUES ({values})",
        manifest,
    )
    for entry in entries:
        entry_columns = ", ".join(entry_fields)
        entry_values = ", ".join(f":{name}" for name in entry_fields)
        connection.execute(
            f"INSERT INTO artifact_manifest_entries({entry_columns}) VALUES ({entry_values})",
            entry,
        )
    row = connection.execute(
        "SELECT * FROM artifact_manifests WHERE artifact_manifest_id = ?",
        (manifest["artifact_manifest_id"],),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    return dict(row)


def persist_evidence_reference_locked(
    connection: sqlite3.Connection,
    *,
    reference: dict[str, Any],
) -> dict[str, Any]:
    """Persist one already-derived closed reference in the caller transaction."""

    from task_governance_tool.storage import (
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS,
        _require_evidence_writer,
        evidence_ledger_inconsistent,
    )

    _require_evidence_writer(connection)
    fields = tuple(sorted(_EVIDENCE_LEDGER_REQUIRED_COLUMNS["evidence_references"]))
    if set(reference) != set(fields):
        raise evidence_ledger_inconsistent()
    columns = ", ".join(fields)
    values = ", ".join(f":{name}" for name in fields)
    connection.execute(
        f"INSERT INTO evidence_references({columns}) VALUES ({values})",
        reference,
    )
    return read_evidence_reference(
        connection,
        evidence_reference_id=str(reference["evidence_reference_id"]),
    )


def read_evidence_reference(
    connection: sqlite3.Connection,
    *,
    evidence_reference_id: str,
) -> dict[str, Any]:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    row = connection.execute(
        "SELECT * FROM evidence_references WHERE evidence_reference_id = ?",
        (evidence_reference_id,),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    return dict(row)


def _validate_prepared_criterion_evidence_link(
    connection: sqlite3.Connection,
    link: _storage.PreparedCriterionEvidenceLink,
) -> None:
    from task_governance_tool.storage import (
        CRITERION_EVIDENCE_LINK_ID_PATTERN,
        CRITERION_EVIDENCE_RELATIONS,
        EVIDENCE_REFERENCE_ID_PATTERN,
        PreparedCriterionEvidenceLink,
        StorageError,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    if (
        not isinstance(link, PreparedCriterionEvidenceLink)
        or CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(
            link.criterion_evidence_link_id
        )
        is None
        or type(link.project_id) is not str
        or not link.project_id
        or type(link.task_id) is not str
        or not link.task_id
        or type(link.criterion_id) is not str
        or not link.criterion_id
        or type(link.evidence_reference_id) is not str
        or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(
            link.evidence_reference_id
        )
        is None
        or type(link.relation) is not str
        or link.relation not in CRITERION_EVIDENCE_RELATIONS
        or link.relation == "derived_analysis"
        or type(link.assurance_class) is not str
        or type(link.producer_class) is not str
        or type(link.producer_version) is not int
        or link.producer_version <= 0
        or type(link.created_at) is not str
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            link.created_at,
            field="criterion Evidence Link creation time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    relation = connection.execute(
        """
        SELECT criterion.criterion_kind, reference.source_kind,
               reference.assurance_class, reference.producer_class,
               reference.producer_version
          FROM contract_criteria AS criterion
          JOIN evidence_references AS reference
            ON reference.project_id = criterion.project_id
           AND reference.task_id = criterion.task_id
         WHERE criterion.project_id = ?
           AND criterion.task_id = ?
           AND criterion.criterion_id = ?
           AND reference.evidence_reference_id = ?
        """,
        (
            link.project_id,
            link.task_id,
            link.criterion_id,
            link.evidence_reference_id,
        ),
    ).fetchone()
    if (
        relation is None
        or relation["assurance_class"] != link.assurance_class
        or relation["producer_class"] != link.producer_class
        or relation["producer_version"] != link.producer_version
        or not _criterion_link_relation_valid(
            criterion_kind=relation["criterion_kind"],
            source_kind=relation["source_kind"],
            relation=link.relation,
        )
    ):
        raise evidence_ledger_inconsistent()


def persist_criterion_evidence_link_locked(
    connection: sqlite3.Connection,
    *,
    link: _storage.PreparedCriterionEvidenceLink,
) -> _storage.PreparedCriterionEvidenceLink:
    """Persist or exactly reuse one append-only criterion Evidence Link."""

    from task_governance_tool.storage import (
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS,
        _prepared_row,
        _require_evidence_writer,
        evidence_ledger_inconsistent,
    )

    _require_evidence_writer(connection)
    _validate_prepared_criterion_evidence_link(connection, link)
    fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS["criterion_evidence_links"]
    values = _prepared_row(link, fields)
    existing = connection.execute(
        """
        SELECT * FROM criterion_evidence_links
         WHERE criterion_evidence_link_id = ?
        """,
        (link.criterion_evidence_link_id,),
    ).fetchone()
    if existing is None:
        ordered = tuple(sorted(fields))
        try:
            connection.execute(
                f"INSERT INTO criterion_evidence_links("
                f"{', '.join(ordered)}) VALUES ("
                f"{', '.join(':' + name for name in ordered)})",
                values,
            )
        except sqlite3.IntegrityError as exc:
            raise evidence_ledger_inconsistent() from exc
        existing = connection.execute(
            "SELECT * FROM criterion_evidence_links "
            "WHERE criterion_evidence_link_id = ?",
            (link.criterion_evidence_link_id,),
        ).fetchone()
    if existing is None or dict(existing) != values:
        raise evidence_ledger_inconsistent()
    return link


def _manifest_evidence_reference_expectation(
    manifest: _storage._ValidatedManifestRecord,
) -> _storage._ExpectedEvidenceReference:
    from task_governance_tool.storage import _ExpectedEvidenceReference

    return _ExpectedEvidenceReference(
        source=manifest.source,
        project_id=manifest.row["project_id"],
        task_id=manifest.row["task_id"],
        contract_revision=manifest.contract_revision,
        binding=manifest.binding,
    )


def _read_owner_indexed_evidence_references(
    connection: sqlite3.Connection,
    *,
    selected_project_id: str,
    selected_source_owners: set[tuple[str, str, str, str]],
    expected_count: int,
) -> list[sqlite3.Row]:
    from task_governance_tool.storage import evidence_ledger_inconsistent

    selected_source_keys = {
        (source_kind, source_id)
        for source_project_id, _, source_kind, source_id
        in selected_source_owners
        if source_project_id == selected_project_id
    }
    if len(selected_source_keys) != len(selected_source_owners):
        raise evidence_ledger_inconsistent()
    selected_sources_json = json.dumps(
        [list(key) for key in sorted(selected_source_keys)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    # A same-source Reference owned by another valid Task is corruption.
    # Enumerating this project's Task owners keeps the owner-leading
    # Reference index seekable; one row beyond the expected set is
    # sufficient to prove inconsistency and bounds materialization.
    reference_rows = connection.execute(
        """
                WITH selected_sources(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT reference.*
                  FROM selected_sources AS selected
                 CROSS JOIN tasks AS owner
                       INDEXED BY idx_tasks_project_task_identity
                 CROSS JOIN evidence_references AS reference
                       INDEXED BY idx_evidence_references_source
                 WHERE owner.project_id = ?
                   AND reference.project_id = owner.project_id
                   AND reference.task_id = owner.task_id
                   AND reference.source_kind =
                         json_extract(selected.value, '$[0]')
                   AND reference.source_id =
                         json_extract(selected.value, '$[1]')
                 LIMIT ?
                """,
        (selected_sources_json, selected_project_id, expected_count + 1),
    ).fetchall()
    return reference_rows


def validate_manifest_evidence_references(
    connection: sqlite3.Connection,
    *,
    manifests: dict[str, _storage._ValidatedManifestRecord],
    selected_project_id: str,
) -> None:
    """Require exact References for already-validated selected manifests."""

    from task_governance_tool.evidence_ledger import EvidenceLedgerError
    from task_governance_tool.storage import (
        StorageError,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
    )

    if type(selected_project_id) is not str or not selected_project_id:
        raise evidence_ledger_inconsistent()
    expected: dict[tuple[str, str], _storage._ExpectedEvidenceReference] = {}
    selected_source_owners: set[tuple[str, str, str, str]] = set()
    try:
        for manifest in manifests.values():
            selected_source_owners.add(
                (
                    manifest.row["project_id"],
                    manifest.row["task_id"],
                    "artifact_manifest",
                    manifest.source.source_id,
                )
            )
            _register_expected_evidence_reference(
                expected,
                _manifest_evidence_reference_expectation(manifest),
            )
        reference_rows = _read_owner_indexed_evidence_references(
            connection,
            selected_project_id=selected_project_id,
            selected_source_owners=selected_source_owners,
            expected_count=len(expected),
        )
        seen: set[tuple[str, str]] = set()
        for row in reference_rows:
            _validate_stored_evidence_reference_row(
                row,
                expected=expected,
                seen=seen,
            )
        if seen != set(expected):
            raise evidence_ledger_inconsistent()
    except (EvidenceLedgerError, StorageError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc


# Shared value types remain storage-owned; bind the module only after these
# definitions so either repository import order resolves runtime annotations.
from task_governance_tool import storage as _storage
