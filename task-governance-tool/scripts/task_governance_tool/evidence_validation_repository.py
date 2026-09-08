"""Shared Evidence validation and Bundle snapshot acquisition.

Callers own connections and transactions. Storage retains schema admission and
native completion orchestration; lower repositories own their stored records.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


def _validate_selected_completion_bundle_history(
    connection: sqlite3.Connection,
    *,
    cycles: tuple[_storage.CompletionCycle, ...],
    runner_generations: dict[tuple[str, str, int], dict[str, Any]],
    container_schema_version: int,
) -> None:
    """Validate and replay only Bundles owned by the returned history cycles."""

    from task_governance_tool.completion_bundle_repository import (
        _validate_prepared_completion_bundle,
    )
    from task_governance_tool.evidence_repository import (
        _criterion_link_relation_valid,
        _validate_artifact_manifest_storage,
        _validated_authority_context,
    )
    from task_governance_tool.review_repository import (
        _iter_validated_review_receipts_with_provenance,
    )
    from task_governance_tool.storage import (
        COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN,
        CRITERION_EVIDENCE_LINK_ID_PATTERN,
        CRITERION_EVIDENCE_RELATIONS,
        StorageError,
        _selected_storage_rows_by_ids,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )
    from task_governance_tool.verification_receipt_repository import (
        _validate_verification_receipt_row,
    )


    native_cycles = tuple(
        cycle for cycle in cycles if cycle.evidence_basis_version == 1
    )
    if not native_cycles:
        return
    if (
        len(cycles) > 10
        or len({cycle.project_id for cycle in native_cycles}) != 1
        or len({cycle.task_id for cycle in native_cycles}) != 1
        or any(
            cycle.origin != "native_done"
            or type(cycle.completion_evidence_bundle_id) is not str
            or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
                cycle.completion_evidence_bundle_id
            )
            is None
            for cycle in native_cycles
        )
    ):
        raise evidence_ledger_inconsistent()
    cycle_ids = {cycle.completion_cycle_id for cycle in native_cycles}
    bundle_ids = {
        cycle.completion_evidence_bundle_id for cycle in native_cycles
    }
    if len(cycle_ids) != len(native_cycles) or len(bundle_ids) != len(native_cycles):
        raise evidence_ledger_inconsistent()

    bundle_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="completion_evidence_bundles",
        id_field="completion_evidence_bundle_id",
        selected_ids=bundle_ids,
    )
    bundles_by_id = {
        str(row["completion_evidence_bundle_id"]): row for row in bundle_rows
    }
    if len(bundles_by_id) != len(bundle_rows):
        raise evidence_ledger_inconsistent()
    cycles_by_bundle = {
        str(cycle.completion_evidence_bundle_id): cycle for cycle in native_cycles
    }
    prepared_by_id: dict[str, PreparedCompletionEvidenceBundle] = {}
    member_groups_by_bundle: dict[
        str,
        dict[str, list[sqlite3.Row | dict[str, Any]]],
    ] = {}
    findings_by_bundle: dict[
        str,
        list[sqlite3.Row | dict[str, Any]],
    ] = {}
    link_rows_by_id: dict[str, sqlite3.Row | dict[str, Any]] = {}
    reference_ids: set[str] = set()
    review_receipt_ids: set[str] = set()
    review_finding_ids: set[str] = set()
    verification_receipt_ids: set[str] = set()
    snapshot_ids: set[str] = set()
    manifest_ids: set[str] = set()
    for bundle_id, cycle in cycles_by_bundle.items():
        row = bundles_by_id.get(bundle_id)
        if row is None or row["completion_cycle_id"] != cycle.completion_cycle_id:
            raise evidence_ledger_inconsistent()
        bundle = read_completion_evidence_bundle(
            connection,
            completion_evidence_bundle_id=bundle_id,
        )
        _validate_prepared_completion_bundle(bundle, expected_cycle=cycle)
        prepared_by_id[bundle_id] = bundle
        grouped: dict[str, list[sqlite3.Row | dict[str, Any]]] = {}
        for member in bundle.members:
            member_row = dict(vars(member))
            grouped.setdefault(member.member_kind, []).append(member_row)
            if member.evidence_reference_id is not None:
                reference_ids.add(member.evidence_reference_id)
        member_groups_by_bundle[bundle_id] = grouped
        findings_by_bundle[bundle_id] = [
            dict(vars(finding)) for finding in bundle.finding_snapshots
        ]
        for link in bundle.criterion_links:
            link_row = dict(vars(link))
            existing = link_rows_by_id.get(link.criterion_evidence_link_id)
            if existing is not None and dict(existing) != link_row:
                raise evidence_ledger_inconsistent()
            link_rows_by_id[link.criterion_evidence_link_id] = link_row
        review_receipt_ids.update(cycle.gate_basis.qualifying_receipt_ids)
        review_finding_ids.update(
            finding.review_finding_id for finding in bundle.finding_snapshots
        )
        if bundle.verification_receipt_id is not None:
            verification_receipt_ids.add(bundle.verification_receipt_id)
        snapshot_ids.add(bundle.authority_snapshot_id)
        manifest_ids.add(bundle.artifact_manifest_id)

    project_id = native_cycles[0].project_id
    task_id = native_cycles[0].task_id
    validate_selected_task_receipt_evidence(
        connection,
        project_id=project_id,
        task_id=task_id,
        review_receipt_ids=review_receipt_ids,
        review_finding_ids=review_finding_ids,
        verification_receipt_ids=verification_receipt_ids,
    )
    authority = _validated_authority_context(
        connection,
        snapshot_ids=snapshot_ids,
    )
    manifests, _ = _validate_artifact_manifest_storage(
        connection,
        snapshots=authority.snapshots,
        links=authority.links,
        manifest_ids=manifest_ids,
    )
    manifest_rows = {
        manifest_id: record.row for manifest_id, record in manifests.items()
    }
    reference_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="evidence_references",
        id_field="evidence_reference_id",
        selected_ids=reference_ids,
    )
    references = {
        str(row["evidence_reference_id"]): row for row in reference_rows
    }
    if len(references) != len(reference_rows):
        raise evidence_ledger_inconsistent()

    for link_id, link in link_rows_by_id.items():
        criterion_id = link["criterion_id"]
        reference_id = link["evidence_reference_id"]
        criterion = authority.criteria.get(str(criterion_id))
        reference = references.get(str(reference_id))
        if (
            CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(link_id) is None
            or type(criterion_id) is not str
            or type(reference_id) is not str
            or type(link["relation"]) is not str
            or link["relation"] not in CRITERION_EVIDENCE_RELATIONS
            or link["relation"] == "derived_analysis"
            or type(link["producer_version"]) is not int
            or link["producer_version"] <= 0
            or type(link["created_at"]) is not str
            or criterion is None
            or reference is None
            or criterion["project_id"] != link["project_id"]
            or criterion["task_id"] != link["task_id"]
            or reference["project_id"] != link["project_id"]
            or reference["task_id"] != link["task_id"]
            or reference["assurance_class"] != link["assurance_class"]
            or reference["producer_class"] != link["producer_class"]
            or reference["producer_version"] != link["producer_version"]
            or not _criterion_link_relation_valid(
                criterion_kind=criterion["criterion_kind"],
                source_kind=reference["source_kind"],
                relation=link["relation"],
            )
        ):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                link["created_at"],
                field="criterion Evidence Link creation time",
            )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc

    verification_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="verification_receipts",
        id_field="verification_receipt_id",
        selected_ids=verification_receipt_ids,
    )
    verification_receipts = {
        str(row["verification_receipt_id"]): row for row in verification_rows
    }
    review_receipts_by_id: dict[str, dict[str, Any]] = {}
    if review_receipt_ids:
        for receipt, provenance in _iter_validated_review_receipts_with_provenance(
            connection,
            review_receipt_ids,
        ):
            review_receipts_by_id[str(receipt["review_receipt_id"])] = {
                "receipt": dict(receipt),
                "provenance": provenance,
            }

    for bundle_id, bundle in prepared_by_id.items():
        row = bundles_by_id[bundle_id]
        cycle = cycles_by_bundle[bundle_id]
        snapshot = authority.snapshots.get(bundle.authority_snapshot_id)
        manifest = manifest_rows.get(bundle.artifact_manifest_id)
        if snapshot is None or manifest is None:
            raise evidence_ledger_inconsistent()
        _validate_one_completion_evidence_bundle_row(
            bundle_id=bundle_id,
            row=row,
            container_schema_version=container_schema_version,
            cycle=cycle,
            snapshot=snapshot,
            owner_links=authority.links.get(bundle.authority_snapshot_id, {}),
            manifest=manifest,
            verification_receipts=verification_receipts,
            member_groups=member_groups_by_bundle[bundle_id],
            links=link_rows_by_id,
            references=references,
            finding_rows=findings_by_bundle[bundle_id],
            runner_generations=runner_generations,
        )
        criteria = tuple(
            dict(authority.criteria[criterion_id])
            for criterion_id in (
                authority.links.get(bundle.authority_snapshot_id, {}).get(
                    "acceptance"
                ),
                authority.links.get(bundle.authority_snapshot_id, {}).get(
                    "verification"
                ),
            )
            if criterion_id is not None
        )
        entry_rows = connection.execute(
            "SELECT * FROM artifact_manifest_entries "
            "WHERE artifact_manifest_id = ? ORDER BY ordinal",
            (bundle.artifact_manifest_id,),
        ).fetchall()
        ordered_references = tuple(
            dict(references[member.evidence_reference_id])
            for member in bundle.members
            if member.member_kind == "evidence_reference"
            and member.evidence_reference_id is not None
        )
        record = _projection_bundle_record_from_validated_rows(
            bundle=bundle,
            cycle=cycle,
            snapshot=snapshot,
            criteria=criteria,
            manifest=manifest,
            entries=tuple(dict(entry) for entry in entry_rows),
            references=ordered_references,
            verification_receipt=(
                _validate_verification_receipt_row(
                    dict(verification_receipts[bundle.verification_receipt_id])
                )
                if bundle.verification_receipt_id is not None
                else None
            ),
            review_receipts_by_id=review_receipts_by_id,
            runner_generation=runner_generations.get(
                (bundle.project_id, bundle.task_id, bundle.target_generation)
            ),
        )
        _validate_projection_bundle_record(record)


def _validate_selected_schema21_completion_bundle_history(
    connection: sqlite3.Connection,
    *,
    cycles: tuple[_storage.CompletionCycle, ...],
) -> None:
    """Revalidate selected v21/v22 native Bundle history before replay."""

    from task_governance_tool.storage import (
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        StorageError,
        _selected_history_runner_generations,
        _unreadable_project_state,
        completion_history_inconsistent,
        current_schema_version,
        evidence_ledger_sqlite_error,
    )


    container_schema_version = current_schema_version(connection)
    if container_schema_version not in {
        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
    } or not any(
        cycle.evidence_basis_version == 1 for cycle in cycles
    ):
        return
    try:
        runner_generations = _selected_history_runner_generations(
            connection,
            cycles=cycles,
        )
    except sqlite3.Error as exc:
        error = evidence_ledger_sqlite_error(exc)
        if error.code == "database_busy":
            raise error from exc
        raise _unreadable_project_state() from exc
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise _unreadable_project_state() from exc
    try:
        _validate_selected_completion_bundle_history(
            connection,
            cycles=cycles,
            runner_generations=runner_generations,
            container_schema_version=container_schema_version,
        )
    except sqlite3.Error as exc:
        error = evidence_ledger_sqlite_error(exc)
        if error.code == "database_busy":
            raise error from exc
        raise completion_history_inconsistent() from exc
    except StorageError as exc:
        if exc.code == "database_busy":
            raise
        raise completion_history_inconsistent() from exc


def _validate_evidence_reference_storage(
    connection: sqlite3.Connection,
    *,
    manifests: dict[str, _storage._ValidatedManifestRecord],
    manifests_by_target: dict[
        tuple[str, str, str, str, str, int], _storage._ValidatedManifestRecord
    ],
    snapshots: dict[str, sqlite3.Row],
    verification_receipt_ids: set[str] | None = None,
    review_receipt_ids: set[str] | None = None,
    review_finding_ids: set[str] | None = None,
    completion_cycle_ids: set[str] | None = None,
    selected_project_id: str | None = None,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> None:
    """Rebuild every native source and require its one exact Reference."""

    from task_governance_tool.evidence_repository import (
        _artifact_manifest_target_key,
        _manifest_evidence_reference_expectation,
        _read_owner_indexed_evidence_references,
        _register_expected_evidence_reference,
        _validate_stored_evidence_reference_row,
    )
    from task_governance_tool.review_repository import (
        _iter_validated_review_receipts_with_provenance,
        _validate_native_review_receipt_tier,
        _validate_review_finding_base_row,
    )
    from task_governance_tool.storage import (
        StorageError,
        _ExpectedEvidenceReference,
        _cycle_from_row,
        _selected_storage_rows_by_ids,
        _validate_completion_target,
        _validate_cycle_verification_receipt_projection,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )
    from task_governance_tool.verification_receipt_repository import (
        _validate_verification_receipt_row,
    )
    from task_governance_tool.verification_runner_repository import (
        _validated_verification_runner_references,
    )


    from task_governance_tool.evidence_ledger import (
        EvidenceLedgerError,
        EvidenceSource,
    )

    expected: dict[tuple[str, str], _ExpectedEvidenceReference] = {}
    review_privacy_successes = (
        privacy_success_cache if privacy_success_cache is not None else set()
    )
    if type(review_privacy_successes) is not set:
        raise evidence_ledger_inconsistent()
    selected_source_owners: set[tuple[str, str, str, str]] = set()
    verification_receipts_by_id: dict[str, dict[str, Any]] = {}
    review_sources: dict[
        str, tuple[dict[str, Any], _ValidatedManifestRecord | None, bool]
    ] = {}
    selection_mode = (
        verification_receipt_ids is not None
        or review_receipt_ids is not None
        or review_finding_ids is not None
        or completion_cycle_ids is not None
    )
    if selection_mode and (
        verification_receipt_ids is None
        or review_receipt_ids is None
        or review_finding_ids is None
        or completion_cycle_ids is None
        or type(selected_project_id) is not str
        or not selected_project_id
    ):
        raise evidence_ledger_inconsistent()
    if not selection_mode and selected_project_id is not None:
        raise evidence_ledger_inconsistent()
    try:
        for manifest in manifests.values():
            if selection_mode:
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

        for stored in _selected_storage_rows_by_ids(
            connection,
            table_name="verification_receipts",
            id_field="verification_receipt_id",
            selected_ids=verification_receipt_ids,
        ):
            try:
                receipt = _validate_verification_receipt_row(dict(stored))
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            receipt_id = receipt["verification_receipt_id"]
            if receipt_id in verification_receipts_by_id:
                raise evidence_ledger_inconsistent()
            verification_receipts_by_id[receipt_id] = receipt
            if selection_mode:
                selected_source_owners.add(
                    (
                        receipt["project_id"],
                        receipt["task_id"],
                        "verification_receipt",
                        receipt_id,
                    )
                )
            basis = receipt["verification_subject_basis_version"]
            manifest = manifests_by_target.get(
                _artifact_manifest_target_key(
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    target_kind=receipt["target_kind"],
                    target_value=receipt["target_value"],
                    target_base_revision=receipt["target_base_revision"],
                    target_generation=receipt["target_generation"],
                )
            )
            if basis == 0:
                if manifest is not None:
                    raise evidence_ledger_inconsistent()
                continue
            if manifest is None:
                raise evidence_ledger_inconsistent()
            snapshot = snapshots.get(manifest.binding.authority_snapshot_id)
            if (
                receipt["subject_authority_snapshot_id"]
                != manifest.binding.authority_snapshot_id
                or receipt["subject_verification_criterion_id"]
                != manifest.binding.verification_criterion_id
                or receipt["contract_revision"] != manifest.contract_revision
                or snapshot is None
                or receipt["verification_expectation_digest"]
                != snapshot["verification_digest"]
            ):
                raise evidence_ledger_inconsistent()
            source = EvidenceSource(
                source_kind="verification_receipt",
                source_state="recorded",
                source_id=receipt["verification_receipt_id"],
                source_projection={
                    "verification_receipt_id": receipt[
                        "verification_receipt_id"
                    ],
                    "subject_basis_version": basis,
                    "authority_snapshot_id": receipt[
                        "subject_authority_snapshot_id"
                    ],
                    "verification_criterion_id": receipt[
                        "subject_verification_criterion_id"
                    ],
                    "result": receipt["result"],
                    "duration_ms": receipt["duration_ms"],
                    "scope_coverage": receipt["scope_coverage"],
                    "created_at": receipt["created_at"],
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                ),
            )

        for stored, provenance in (
            _iter_validated_review_receipts_with_provenance(
                connection,
                review_receipt_ids,
                privacy_success_cache=review_privacy_successes,
            )
        ):
            receipt_id = stored["review_receipt_id"]
            if (
                type(receipt_id) is not str
                or not receipt_id
                or type(stored["project_id"]) is not str
                or not stored["project_id"]
                or type(stored["task_id"]) is not str
                or not stored["task_id"]
                or type(stored["reviewer_key"]) is not str
                or not stored["reviewer_key"]
                or type(stored["summary"]) is not str
                or type(stored["user_approved"]) is not int
                or stored["user_approved"] not in {0, 1}
                or type(stored["created_at"]) is not str
            ):
                raise evidence_ledger_inconsistent()
            try:
                validate_utc_timestamp(
                    stored["created_at"], field="Review Receipt creation time"
                )
                _validate_completion_target(
                    kind=stored["target_kind"],
                    value=stored["target_value"],
                    base_revision=stored["target_base_revision"],
                    generation=stored["target_generation"],
                )
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            manifest = manifests_by_target.get(
                _artifact_manifest_target_key(
                    project_id=stored["project_id"],
                    task_id=stored["task_id"],
                    target_kind=stored["target_kind"],
                    target_value=stored["target_value"],
                    target_base_revision=stored["target_base_revision"],
                    target_generation=stored["target_generation"],
                )
            )
            basis = stored["review_provenance_basis_version"]
            kind = stored["receipt_kind"]
            native = basis == 1 or (kind == "not_required" and manifest is not None)
            if (
                type(basis) is not int
                or basis not in {0, 1}
                or (basis == 1 and (kind == "not_required" or manifest is None))
                or (
                    basis == 0
                    and kind in {"independent", "self_review_fallback"}
                    and manifest is not None
                )
            ):
                raise evidence_ledger_inconsistent()
            if native:
                assert manifest is not None
                snapshot = snapshots.get(manifest.binding.authority_snapshot_id)
                if snapshot is None:
                    raise evidence_ledger_inconsistent()
                _validate_native_review_receipt_tier(
                    stored,
                    review_tier=snapshot["review_tier"],
                )
            receipt = dict(stored)
            review_sources[receipt_id] = (receipt, manifest, native)
            if selection_mode:
                selected_source_owners.add(
                    (
                        receipt["project_id"],
                        receipt["task_id"],
                        "review_receipt",
                        receipt_id,
                    )
                )
            if not native:
                continue
            assert manifest is not None
            source = EvidenceSource(
                source_kind="review_receipt",
                source_state="recorded",
                source_id=receipt_id,
                source_projection={
                    "review_receipt_id": receipt_id,
                    "reviewer_key": receipt["reviewer_key"],
                    "receipt_kind": kind,
                    "verdict": receipt["verdict"],
                    "summary": receipt["summary"],
                    "user_approved": receipt["user_approved"],
                    "created_at": receipt["created_at"],
                    "review_provenance": provenance,
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                ),
            )

        finding_rows = (
            _selected_storage_rows_by_ids(
                connection,
                table_name="review_findings",
                id_field="review_finding_id",
                selected_ids=review_finding_ids,
            )
            if selection_mode
            else iter(
                connection.execute(
                    "SELECT * FROM review_findings ORDER BY review_finding_id"
                ).fetchone,
                None,
            )
        )
        for stored in finding_rows:
            _validate_review_finding_base_row(
                stored,
                privacy_success_cache=review_privacy_successes,
            )
            finding_id = stored["review_finding_id"]
            receipt_id = stored["review_receipt_id"]
            receipt_record = review_sources.get(receipt_id)
            if receipt_record is None:
                raise evidence_ledger_inconsistent()
            receipt, manifest, native = receipt_record
            if selection_mode:
                selected_source_owners.add(
                    (
                        receipt["project_id"],
                        receipt["task_id"],
                        "review_finding",
                        finding_id,
                    )
                )
            if not native:
                continue
            assert manifest is not None
            source = EvidenceSource(
                source_kind="review_finding",
                source_state="recorded",
                source_id=finding_id,
                source_projection={
                    "review_finding_id": finding_id,
                    "review_receipt_id": receipt_id,
                    "severity": stored["severity"],
                    "summary": stored["summary"],
                    "created_at": stored["created_at"],
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=receipt["project_id"],
                    task_id=receipt["task_id"],
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                ),
            )

        cycle_rows = (
            _selected_storage_rows_by_ids(
                connection,
                table_name="task_completion_cycles",
                id_field="completion_cycle_id",
                selected_ids=completion_cycle_ids,
            )
            if selection_mode
            else connection.execute(
                "SELECT * FROM task_completion_cycles ORDER BY completion_cycle_id"
            )
        )
        for stored in cycle_rows:
            try:
                cycle = _cycle_from_row(stored)
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            if selection_mode:
                selected_source_owners.add(
                    (
                        cycle.project_id,
                        cycle.task_id,
                        "completion_evidence",
                        cycle.completion_cycle_id,
                    )
                )
            linked_verification_receipt = (
                verification_receipts_by_id.get(
                    cycle.verification_receipt_id
                )
                if cycle.verification_receipt_id is not None
                else None
            )
            try:
                _validate_cycle_verification_receipt_projection(
                    cycle,
                    linked_verification_receipt,
                    validate_complete_receipt=False,
                )
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            manifest = None
            if cycle.review_target_generation > 0:
                manifest = manifests_by_target.get(
                    _artifact_manifest_target_key(
                        project_id=cycle.project_id,
                        task_id=cycle.task_id,
                        target_kind=cycle.review_target_kind,
                        target_value=cycle.review_target_value,
                        target_base_revision=cycle.review_target_base_revision,
                        target_generation=cycle.review_target_generation,
                    )
                )
            if cycle.verification_subject_basis_version == 0:
                if manifest is not None:
                    raise evidence_ledger_inconsistent()
                continue
            if (
                manifest is None
                or cycle.contract_revision != manifest.contract_revision
                or (
                    cycle.verification_expectation == "specified"
                    and (
                        cycle.subject_authority_snapshot_id
                        != manifest.binding.authority_snapshot_id
                        or cycle.subject_verification_criterion_id
                        != manifest.binding.verification_criterion_id
                    )
                )
                or (
                    cycle.verification_expectation == "unspecified"
                    and manifest.binding.verification_criterion_id is not None
                )
            ):
                raise evidence_ledger_inconsistent()
            source = EvidenceSource(
                source_kind="completion_evidence",
                source_state=cycle.completion_evidence_kind,
                source_id=cycle.completion_cycle_id,
                source_projection={
                    "completion_cycle_id": cycle.completion_cycle_id,
                    "completed_at": cycle.completed_at,
                    "completion_evidence_kind": cycle.completion_evidence_kind,
                    "completion_evidence_revision": cycle.completion_evidence_revision,
                    "completion_evidence_reason": cycle.completion_evidence_reason,
                    "external_revision_approved": int(
                        cycle.external_revision_approved
                    ),
                    "completion_commit_required": int(
                        cycle.completion_commit_required
                    ),
                    "completion_commit_hash": cycle.completion_commit_hash,
                },
            )
            _register_expected_evidence_reference(
                expected,
                _ExpectedEvidenceReference(
                    source=source,
                    project_id=cycle.project_id,
                    task_id=cycle.task_id,
                    contract_revision=manifest.contract_revision,
                    binding=manifest.binding,
                    completion_cycle_id=cycle.completion_cycle_id,
                ),
            )

        if not selection_mode:
            for runner_reference in _validated_verification_runner_references(
                connection
            ):
                _register_expected_evidence_reference(
                    expected,
                    runner_reference,
                )

        reference_cursor: sqlite3.Cursor | None = None
        if selection_mode:
            assert verification_receipt_ids is not None
            assert review_receipt_ids is not None
            assert review_finding_ids is not None
            assert completion_cycle_ids is not None
            reference_rows = _read_owner_indexed_evidence_references(
                connection,
                selected_project_id=selected_project_id,
                selected_source_owners=selected_source_owners,
                expected_count=len(expected),
            )
        else:
            reference_cursor = connection.execute(
                "SELECT * FROM evidence_references "
                "ORDER BY evidence_reference_id"
            )
            reference_rows = iter(reference_cursor.fetchone, None)
        seen: set[tuple[str, str]] = set()
        try:
            for row in reference_rows:
                _validate_stored_evidence_reference_row(
                    row,
                    expected=expected,
                    seen=seen,
                )
        finally:
            if reference_cursor is not None:
                reference_cursor.close()

        if seen != set(expected):
            raise evidence_ledger_inconsistent()
    except (EvidenceLedgerError, StorageError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc


def _validate_selected_reference_source_chunk(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str,
    selected_task_ids: set[str],
    source_owners: dict[tuple[str, str], str],
) -> None:
    """Validate one bounded all-kind Reference chunk structurally."""

    from task_governance_tool.evidence_repository import (
        _artifact_manifest_target_key,
    )
    from task_governance_tool.review_repository import (
        REVIEW_FINDING_ID_PATTERN,
        REVIEW_RECEIPT_ID_PATTERN,
    )
    from task_governance_tool.storage import (
        ARTIFACT_MANIFEST_ID_PATTERN,
        COMPLETION_CYCLE_ID_PATTERN,
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        EVIDENCE_REFERENCE_ID_PATTERN,
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        StorageError,
        _selected_storage_rows_by_ids,
        _validate_completion_target,
        current_schema_version,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
    )
    from task_governance_tool.verification_receipt_repository import (
        VERIFICATION_RECEIPT_ID_PATTERN,
    )
    from task_governance_tool.verification_runner_repository import (
        VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN,
    )


    if (
        not source_owners
        or len(source_owners) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
    ):
        if source_owners:
            raise evidence_ledger_inconsistent()
        return

    artifact_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "artifact_manifest"
    }
    verification_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "verification_receipt"
    }
    review_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "review_receipt"
    }
    finding_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "review_finding"
    }
    cycle_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "completion_evidence"
    }
    runner_owners = {
        source_id: task_id
        for (source_kind, source_id), task_id in source_owners.items()
        if source_kind == "runner_observation"
    }
    source_schema_version = current_schema_version(connection)
    if source_schema_version < PRIVATE_SCHEMA20_VERSION and runner_owners:
        raise evidence_ledger_inconsistent()

    finding_rows = _selected_storage_rows_by_ids(
        connection,
        table_name="review_findings",
        id_field="review_finding_id",
        selected_ids=set(finding_owners),
    )
    for row in finding_rows:
        finding_id = row["review_finding_id"]
        expected_task_id = finding_owners.get(finding_id)
        parent_receipt_id = row["review_receipt_id"]
        if (
            type(finding_id) is not str
            or len(finding_id) > 128
            or REVIEW_FINDING_ID_PATTERN.fullmatch(finding_id) is None
            or expected_task_id not in selected_task_ids
            or type(parent_receipt_id) is not str
            or len(parent_receipt_id) > 128
            or REVIEW_RECEIPT_ID_PATTERN.fullmatch(parent_receipt_id) is None
            or (
                parent_receipt_id in review_owners
                and review_owners[parent_receipt_id] != expected_task_id
            )
        ):
            raise evidence_ledger_inconsistent()
        review_owners[parent_receipt_id] = expected_task_id
    if len(review_owners) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE:
        raise evidence_ledger_inconsistent()

    source_specs = (
        (
            "artifact_manifest",
            "artifact_manifests",
            "artifact_manifest_id",
            ARTIFACT_MANIFEST_ID_PATTERN,
            artifact_owners,
        ),
        (
            "verification_receipt",
            "verification_receipts",
            "verification_receipt_id",
            VERIFICATION_RECEIPT_ID_PATTERN,
            verification_owners,
        ),
        (
            "review_receipt",
            "review_receipts",
            "review_receipt_id",
            REVIEW_RECEIPT_ID_PATTERN,
            review_owners,
        ),
        (
            "completion_evidence",
            "task_completion_cycles",
            "completion_cycle_id",
            COMPLETION_CYCLE_ID_PATTERN,
            cycle_owners,
        ),
        *(
            (
                (
                    "runner_observation",
                    "verification_runner_observations",
                    "verification_runner_observation_id",
                    VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN,
                    runner_owners,
                ),
            )
            if source_schema_version >= PRIVATE_SCHEMA20_VERSION
            else ()
        ),
    )
    not_required_target_keys: set[tuple[str, str, str, str, str, int]] = set()
    for source_kind, table_name, id_field, pattern, owners in source_specs:
        rows = _selected_storage_rows_by_ids(
            connection,
            table_name=table_name,
            id_field=id_field,
            selected_ids=set(owners),
        )
        for row in rows:
            source_id = row[id_field]
            expected_task_id = owners.get(source_id)
            if (
                type(source_id) is not str
                or len(source_id) > 128
                or pattern.fullmatch(source_id) is None
                or expected_task_id not in selected_task_ids
                or row["project_id"] != expected_project_id
                or row["task_id"] != expected_task_id
            ):
                raise evidence_ledger_inconsistent()
            if source_kind == "review_receipt":
                basis = row["review_provenance_basis_version"]
                receipt_kind = row["receipt_kind"]
                if (
                    type(basis) is not int
                    or basis not in {0, 1}
                    or type(receipt_kind) is not str
                    or (
                        basis == 1
                        and receipt_kind
                        not in {"independent", "self_review_fallback"}
                    )
                    or (basis == 0 and receipt_kind != "not_required")
                ):
                    raise evidence_ledger_inconsistent()
                if basis == 0:
                    try:
                        _validate_completion_target(
                            kind=row["target_kind"],
                            value=row["target_value"],
                            base_revision=row["target_base_revision"],
                            generation=row["target_generation"],
                        )
                    except StorageError as exc:
                        raise evidence_ledger_boundary_error(exc) from exc
                    not_required_target_keys.add(
                        _artifact_manifest_target_key(
                            project_id=row["project_id"],
                            task_id=row["task_id"],
                            target_kind=row["target_kind"],
                            target_value=row["target_value"],
                            target_base_revision=row["target_base_revision"],
                            target_generation=row["target_generation"],
                        )
                    )
            elif source_kind == "verification_receipt":
                if (
                    type(row["verification_subject_basis_version"]) is not int
                    or row["verification_subject_basis_version"] != 1
                ):
                    raise evidence_ledger_inconsistent()
            elif source_kind == "completion_evidence":
                if (
                    type(row["verification_subject_basis_version"]) is not int
                    or row["verification_subject_basis_version"] != 1
                ):
                    raise evidence_ledger_inconsistent()
            elif source_kind == "runner_observation":
                admitted_eligibility_versions = (
                    {0, 1}
                    if source_schema_version in {
                        PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
                    }
                    else {0}
                )
                if (
                    type(row["gate_eligibility_version"]) is not int
                    or row["gate_eligibility_version"]
                    not in admitted_eligibility_versions
                    or row["route"] not in {"runner", "m21_fallback"}
                    or row["launch_state"] not in {"launched", "no_launch"}
                ):
                    raise evidence_ledger_inconsistent()

    if not_required_target_keys:
        targets_json = json.dumps(
            [list(key) for key in sorted(not_required_target_keys)],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        target_manifest_rows = connection.execute(
            """
            WITH selected_targets(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT manifest.artifact_manifest_id, manifest.project_id,
                   manifest.task_id, manifest.target_kind,
                   manifest.target_value, manifest.target_base_revision,
                   manifest.target_generation
              FROM artifact_manifests AS manifest
              JOIN selected_targets AS selected
                ON manifest.project_id = json_extract(selected.value, '$[0]')
               AND manifest.task_id = json_extract(selected.value, '$[1]')
               AND manifest.target_kind = json_extract(selected.value, '$[2]')
               AND manifest.target_value = json_extract(selected.value, '$[3]')
               AND manifest.target_base_revision = json_extract(selected.value, '$[4]')
               AND manifest.target_generation = json_extract(selected.value, '$[5]')
             ORDER BY manifest.artifact_manifest_id
             LIMIT ?
            """,
            (targets_json, len(not_required_target_keys) + 1),
        ).fetchall()
        observed_target_keys: set[
            tuple[str, str, str, str, str, int]
        ] = set()
        for row in target_manifest_rows:
            target_key = _artifact_manifest_target_key(
                project_id=row["project_id"],
                task_id=row["task_id"],
                target_kind=row["target_kind"],
                target_value=row["target_value"],
                target_base_revision=row["target_base_revision"],
                target_generation=row["target_generation"],
            )
            if (
                type(row["artifact_manifest_id"]) is not str
                or ARTIFACT_MANIFEST_ID_PATTERN.fullmatch(
                    row["artifact_manifest_id"]
                )
                is None
                or target_key not in not_required_target_keys
                or target_key in observed_target_keys
            ):
                raise evidence_ledger_inconsistent()
            observed_target_keys.add(target_key)
        if observed_target_keys != not_required_target_keys:
            raise evidence_ledger_inconsistent()

    selected_sources_json = json.dumps(
        [list(key) for key in sorted(source_owners)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    reference_rows = connection.execute(
        """
        WITH selected_sources(value) AS (
            SELECT value FROM json_each(?)
        )
        SELECT reference.evidence_reference_id, reference.project_id,
               reference.task_id, reference.source_kind, reference.source_id
          FROM selected_sources AS selected
         CROSS JOIN tasks AS owner
               INDEXED BY idx_tasks_project_task_identity
         CROSS JOIN evidence_references AS reference
               INDEXED BY idx_evidence_references_source
         WHERE owner.project_id = ?
           AND reference.project_id = owner.project_id
           AND reference.task_id = owner.task_id
           AND reference.source_kind = json_extract(selected.value, '$[0]')
           AND reference.source_id = json_extract(selected.value, '$[1]')
         LIMIT ?
        """,
        (selected_sources_json, expected_project_id, len(source_owners) + 1),
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for row in reference_rows:
        reference_id = row["evidence_reference_id"]
        source_kind = row["source_kind"]
        source_id = row["source_id"]
        key = (source_kind, source_id)
        expected_task_id = source_owners.get(key)
        if (
            type(reference_id) is not str
            or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(reference_id) is None
            or type(source_kind) is not str
            or type(source_id) is not str
            or expected_task_id is None
            or key in seen
            or row["project_id"] != expected_project_id
            or row["task_id"] != expected_task_id
        ):
            raise evidence_ledger_inconsistent()
        seen.add(key)
    if seen != set(source_owners):
        raise evidence_ledger_inconsistent()


def _validate_selected_task_evidence_reference_inventory(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str,
    selected_task_ids: set[str],
) -> None:
    """Stream every Reference owned by one bounded selected Task batch."""

    from task_governance_tool.review_repository import (
        REVIEW_FINDING_ID_PATTERN,
        REVIEW_RECEIPT_ID_PATTERN,
    )
    from task_governance_tool.storage import (
        ARTIFACT_MANIFEST_ID_PATTERN,
        COMPLETION_CYCLE_ID_PATTERN,
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        EVIDENCE_REFERENCE_ID_PATTERN,
        SELECTED_TASK_AUTHORITY_VALIDATION_CHUNK_SIZE,
        StorageError,
        _SELECTED_TASK_EVIDENCE_REFERENCE_INVENTORY_SQL,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
    )
    from task_governance_tool.verification_receipt_repository import (
        VERIFICATION_RECEIPT_ID_PATTERN,
    )
    from task_governance_tool.verification_runner_repository import (
        VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN,
    )


    if (
        type(expected_project_id) is not str
        or not expected_project_id
        or type(selected_task_ids) is not set
        or not selected_task_ids
        or len(selected_task_ids) > SELECTED_TASK_AUTHORITY_VALIDATION_CHUNK_SIZE
        or any(type(task_id) is not str or not task_id for task_id in selected_task_ids)
    ):
        raise evidence_ledger_inconsistent()
    selected_json = json.dumps(
        sorted(selected_task_ids),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    source_id_patterns = {
        "artifact_manifest": ARTIFACT_MANIFEST_ID_PATTERN,
        "verification_receipt": VERIFICATION_RECEIPT_ID_PATTERN,
        "review_receipt": REVIEW_RECEIPT_ID_PATTERN,
        "review_finding": REVIEW_FINDING_ID_PATTERN,
        "completion_evidence": COMPLETION_CYCLE_ID_PATTERN,
        "runner_observation": VERIFICATION_RUNNER_OBSERVATION_ID_PATTERN,
    }
    reference_cursor: sqlite3.Cursor | None = None
    try:
        reference_cursor = connection.execute(
            _SELECTED_TASK_EVIDENCE_REFERENCE_INVENTORY_SQL,
            (selected_json, expected_project_id, expected_project_id),
        )
        while True:
            rows = reference_cursor.fetchmany(
                COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            )
            if not rows:
                break
            source_owners: dict[tuple[str, str], str] = {}
            for row in rows:
                reference_id = row["evidence_reference_id"]
                project_id = row["project_id"]
                task_id = row["task_id"]
                source_kind = row["source_kind"]
                source_id = row["source_id"]
                pattern = (
                    source_id_patterns.get(source_kind)
                    if type(source_kind) is str
                    else None
                )
                if (
                    type(reference_id) is not str
                    or len(reference_id) > 128
                    or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(reference_id)
                    is None
                    or type(project_id) is not str
                    or project_id != expected_project_id
                    or type(task_id) is not str
                    or task_id not in selected_task_ids
                    or type(source_kind) is not str
                    or pattern is None
                    or type(source_id) is not str
                    or len(source_id) > 128
                    or pattern.fullmatch(source_id) is None
                    or (source_kind, source_id) in source_owners
                ):
                    raise evidence_ledger_inconsistent()
                key = (source_kind, source_id)
                source_owners[key] = task_id
            _validate_selected_reference_source_chunk(
                connection,
                expected_project_id=expected_project_id,
                selected_task_ids=selected_task_ids,
                source_owners=source_owners,
            )
    except (sqlite3.Error, StorageError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc
    finally:
        if reference_cursor is not None:
            reference_cursor.close()


def _validate_selected_completion_cycle_evidence(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    cycles: tuple[_storage.CompletionCycle, ...],
) -> None:
    """Validate only requested cycles against schema-v18 ledger relations."""

    from task_governance_tool.evidence_repository import (
        _artifact_manifest_target_key,
        _validate_artifact_manifest_storage,
        _validated_authority_context,
    )
    from task_governance_tool.storage import (
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        CompletionCycle,
        _selected_storage_rows_by_ids,
        _validate_stored_verification_subject_rows,
        current_schema_version,
        evidence_ledger_inconsistent,
        evidence_ledger_sqlite_error,
    )


    if current_schema_version(connection) < 18:
        return
    if (
        type(project_id) is not str
        or not project_id
        or type(cycles) is not tuple
    ):
        raise evidence_ledger_inconsistent()

    observed_cycle_ids: set[str] = set()
    for cycle in cycles:
        if not isinstance(cycle, CompletionCycle):
            raise evidence_ledger_inconsistent()
        cycle_id = cycle.completion_cycle_id
        if (
            cycle.project_id != project_id
            or type(cycle_id) is not str
            or not cycle_id
            or cycle_id in observed_cycle_ids
        ):
            raise evidence_ledger_inconsistent()
        observed_cycle_ids.add(cycle_id)

    try:
        for offset in range(
            0,
            len(cycles),
            COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        ):
            chunk = cycles[
                offset : offset + COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
            ]
            cycle_ids = {cycle.completion_cycle_id for cycle in chunk}
            verification_receipt_ids = {
                cycle.verification_receipt_id
                for cycle in chunk
                if cycle.verification_receipt_id is not None
            }
            target_keys = {
                _artifact_manifest_target_key(
                    project_id=cycle.project_id,
                    task_id=cycle.task_id,
                    target_kind=cycle.review_target_kind,
                    target_value=cycle.review_target_value,
                    target_base_revision=cycle.review_target_base_revision,
                    target_generation=cycle.review_target_generation,
                )
                for cycle in chunk
                if cycle.review_target_generation > 0
            }
            targets_json = json.dumps(
                [list(key) for key in sorted(target_keys)],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            manifest_rows = connection.execute(
                """
                WITH selected_targets(value) AS (
                    SELECT value FROM json_each(?)
                )
                SELECT manifest.artifact_manifest_id,
                       manifest.authority_snapshot_id
                  FROM artifact_manifests AS manifest
                  JOIN selected_targets AS selected
                    ON manifest.project_id = json_extract(selected.value, '$[0]')
                   AND manifest.task_id = json_extract(selected.value, '$[1]')
                   AND manifest.target_kind = json_extract(selected.value, '$[2]')
                   AND manifest.target_value = json_extract(selected.value, '$[3]')
                   AND manifest.target_base_revision =
                         json_extract(selected.value, '$[4]')
                   AND manifest.target_generation =
                         json_extract(selected.value, '$[5]')
                 ORDER BY manifest.artifact_manifest_id
                """,
                (targets_json,),
            ).fetchall()
            manifest_ids: set[str] = set()
            snapshot_ids: set[str] = set()
            for row in manifest_rows:
                manifest_id = row["artifact_manifest_id"]
                snapshot_id = row["authority_snapshot_id"]
                if (
                    type(manifest_id) is not str
                    or not manifest_id
                    or manifest_id in manifest_ids
                    or type(snapshot_id) is not str
                    or not snapshot_id
                ):
                    raise evidence_ledger_inconsistent()
                manifest_ids.add(manifest_id)
                snapshot_ids.add(snapshot_id)

            authority = _validated_authority_context(
                connection,
                snapshot_ids=snapshot_ids,
            )
            manifests, manifests_by_target = _validate_artifact_manifest_storage(
                connection,
                snapshots=authority.snapshots,
                links=authority.links,
                manifest_ids=manifest_ids,
            )
            cycle_rows = _selected_storage_rows_by_ids(
                connection,
                table_name="task_completion_cycles",
                id_field="completion_cycle_id",
                selected_ids=cycle_ids,
            )
            _validate_stored_verification_subject_rows(
                cycle_rows,
                table_name="task_completion_cycles",
                snapshots=authority.snapshots,
                criteria=authority.criteria,
                links=authority.links,
            )
            _validate_evidence_reference_storage(
                connection,
                manifests=manifests,
                manifests_by_target=manifests_by_target,
                snapshots=authority.snapshots,
                verification_receipt_ids=verification_receipt_ids,
                review_receipt_ids=set(),
                review_finding_ids=set(),
                completion_cycle_ids=cycle_ids,
                selected_project_id=project_id,
            )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc


def _validate_evidence_ledger_rows(
    connection: sqlite3.Connection,
    *,
    verification_rejection_is_local: bool = False,
    privacy_success_cache: set[tuple[str, str, str]] | None = None,
) -> tuple[sqlite3.Row, ...]:
    from task_governance_tool.evidence_repository import (
        _validate_artifact_manifest_storage,
        _validated_authority_context,
    )
    from task_governance_tool.storage import (
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        SQLITE_INT64_MAX,
        StorageError,
        StoredTaskVerificationError,
        _quoted_identifier,
        _validate_stored_verification_subject_rows,
        current_schema_version,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
    )

    authority = _validated_authority_context(
        connection,
        privacy_success_cache=privacy_success_cache,
    )
    snapshots = authority.snapshots
    criteria = authority.criteria
    links = authority.links
    snapshot_generation_state = authority.generation_state
    contracts_by_revision = authority.contracts_by_revision
    manifests, manifests_by_target = _validate_artifact_manifest_storage(
        connection,
        snapshots=snapshots,
        links=links,
    )

    from task_governance_tool.tasks import TaskRepositoryError
    from task_governance_tool.stored_task_validation import validate_stored_task_rows

    task_rows = connection.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
    expected_project_id = (
        task_rows[0]["project_id"] if task_rows else "__empty_project__"
    )
    if type(expected_project_id) is not str or not expected_project_id:
        raise evidence_ledger_inconsistent()
    physical_schema_version = current_schema_version(connection)
    if physical_schema_version not in {
        18,
        19,
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
    }:
        raise evidence_ledger_inconsistent()
    source_schema_version = (
        19
        if physical_schema_version == PRIVATE_SCHEMA20_VERSION
        else physical_schema_version
    )
    try:
        task_validation = validate_stored_task_rows(
            task_rows,
            connection=connection,
            source_schema_version=source_schema_version,
            expected_project_id=expected_project_id,
            verification_rejection_is_local=verification_rejection_is_local,
            _prevalidated_privacy_successes=authority.ordinary_privacy_successes,
        )
    except (StorageError, TaskRepositoryError) as exc:
        raise evidence_ledger_boundary_error(exc) from exc

    for row in task_rows:
        snapshot_id = row["current_authority_snapshot_id"]
        generation = row["current_authority_snapshot_generation"]
        project_id = row["project_id"]
        task_id = row["task_id"]
        title = row["title"]
        description = row["description"]
        review_tier = row["review_tier"]
        verification = row["verification"]
        contract_revision = row["current_contract_revision"]
        if (
            type(snapshot_id) is not str
            or re.fullmatch(
                r"tg_authority_snapshot_[0-9a-f]{16}", snapshot_id
            )
            is None
            or type(generation) is not int
            or not 1 <= generation <= SQLITE_INT64_MAX
        ):
            raise evidence_ledger_inconsistent()
        snapshot = snapshots.get(snapshot_id)
        contract = None
        if contract_revision > 0:
            contract = contracts_by_revision.get(
                (project_id, task_id, contract_revision)
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
            snapshot is None
            or (contract_revision > 0 and contract is None)
            or snapshot_generation_state.get(
                (project_id, task_id)
            )
            != (generation, generation)
            or snapshot["generation"] != generation
            or snapshot["project_id"] != project_id
            or snapshot["task_id"] != task_id
            or snapshot["task_title"] != title
            or snapshot["task_description"] != description
            or snapshot["review_tier"] != review_tier
            or (
                snapshot["verification"] != verification
                and task_id
                not in task_validation.verification_rejected_task_ids
            )
            or snapshot["contract_revision"] != contract_revision
            or (
                snapshot["contract_state"],
                snapshot["contract_scope"],
                snapshot["contract_acceptance"],
                snapshot["contract_constraints"],
                snapshot["contract_authority_ref"],
            )
            != expected_contract
        ):
            raise evidence_ledger_inconsistent()
        capture_version = row["review_target_capture_version"]
        target_bindings = (
            row["review_target_authority_snapshot_id"],
            row["review_target_acceptance_criterion_id"],
            row["review_target_verification_criterion_id"],
            row["review_target_artifact_manifest_id"],
        )
        if type(capture_version) is not int or capture_version not in {0, 1}:
            raise evidence_ledger_inconsistent()
        if capture_version == 0:
            if any(value is not None for value in target_bindings):
                raise evidence_ledger_inconsistent()
        elif (
            type(row["review_target_kind"]) is not str
            or not row["review_target_kind"]
            or type(row["review_target_value"]) is not str
            or not row["review_target_value"]
            or type(row["review_target_base_revision"]) is not str
            or type(row["review_target_generation"]) is not int
            or row["review_target_generation"] <= 0
            or type(target_bindings[0]) is not str
            or type(target_bindings[3]) is not str
        ):
            raise evidence_ledger_inconsistent()
        elif capture_version == 1:
            manifest_record = manifests.get(target_bindings[3])
            manifest = manifest_record.row if manifest_record is not None else None
            snapshot_links = links.get(target_bindings[0], {})
            if (
                manifest is None
                or manifest["project_id"] != project_id
                or manifest["task_id"] != task_id
                or manifest["target_kind"] != row["review_target_kind"]
                or manifest["target_value"] != row["review_target_value"]
                or manifest["target_base_revision"]
                != row["review_target_base_revision"]
                or manifest["target_generation"]
                != row["review_target_generation"]
                or manifest["authority_snapshot_id"] != target_bindings[0]
                or manifest["acceptance_criterion_id"] != target_bindings[1]
                or manifest["verification_criterion_id"] != target_bindings[2]
                or snapshot_links.get("acceptance") != target_bindings[1]
                or snapshot_links.get("verification") != target_bindings[2]
            ):
                raise evidence_ledger_inconsistent()

    for table_name in ("verification_receipts", "task_completion_cycles"):
        rows = connection.execute(
            f"SELECT * FROM {_quoted_identifier(table_name)} ORDER BY rowid"
        ).fetchall()
        _validate_stored_verification_subject_rows(
            rows,
            table_name=table_name,
            snapshots=snapshots,
            criteria=criteria,
            links=links,
        )


    _validate_evidence_reference_storage(
        connection,
        manifests=manifests,
        manifests_by_target=manifests_by_target,
        snapshots=snapshots,
    )
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise evidence_ledger_inconsistent()
    if task_validation.verification_rejection is not None:
        raise StoredTaskVerificationError(
            task_validation.verification_rejection
        )
    return tuple(task_rows)


def _validate_one_completion_evidence_bundle_row(
    *,
    bundle_id: str,
    row: sqlite3.Row | dict[str, Any],
    container_schema_version: int,
    cycle: _storage.CompletionCycle | None,
    snapshot: sqlite3.Row | dict[str, Any] | None,
    owner_links: dict[str, str],
    manifest: sqlite3.Row | dict[str, Any] | None,
    verification_receipts: dict[str, sqlite3.Row | dict[str, Any]],
    member_groups: dict[str, list[sqlite3.Row | dict[str, Any]]],
    links: dict[str, sqlite3.Row | dict[str, Any]],
    references: dict[str, sqlite3.Row | dict[str, Any]],
    finding_rows: list[sqlite3.Row | dict[str, Any]],
    runner_generations: dict[tuple[str, str, int], dict[str, Any]] | None,
) -> None:
    """Validate the semantic closure of one already selected Bundle row."""

    from task_governance_tool.storage import (
        COMPLETION_BUNDLE_OMISSION_BITS,
        COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN,
        COMPLETION_EVIDENCE_BUNDLE_MAX_BYTES,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        SHA256_DIGEST_PATTERN,
        StorageError,
        _completion_bundle_version_basis_valid,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )


    verification_receipt_id = row["verification_receipt_id"]
    row_fields = set(row.keys())
    verification_basis_kind = (
        row["verification_basis_kind"]
        if "verification_basis_kind" in row_fields
        else None
    )
    verification_runner_observation_id = (
        row["verification_runner_observation_id"]
        if "verification_runner_observation_id" in row_fields
        else None
    )
    omission_mask = row["omission_mask"]
    payload_size = row["payload_size_bytes"]
    if (
        COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(bundle_id) is None
        or (
            row["source_schema_version"] == PRIVATE_SCHEMA22_VERSION
            and container_schema_version != PRIVATE_SCHEMA22_VERSION
        )
        or cycle is None
        or cycle.evidence_basis_version != 1
        or cycle.completion_evidence_bundle_id != bundle_id
        or cycle.project_id != row["project_id"]
        or cycle.task_id != row["task_id"]
        or cycle.saved_cycle_ordinal != row["cycle_ordinal"]
        or cycle.contract_revision != row["contract_revision"]
        or not _completion_bundle_version_basis_valid(
            source_schema_version=row["source_schema_version"],
            bundle_version=row["bundle_version"],
            verification_basis_kind=verification_basis_kind,
            verification_runner_observation_id=(
                verification_runner_observation_id
            ),
            verification_receipt_id=verification_receipt_id,
            cycle=cycle,
        )
        or snapshot is None
        or snapshot["project_id"] != row["project_id"]
        or snapshot["task_id"] != row["task_id"]
        or snapshot["contract_revision"] != row["contract_revision"]
        or owner_links.get("acceptance") != row["acceptance_criterion_id"]
        or owner_links.get("verification") != row["verification_criterion_id"]
        or manifest is None
        or manifest["project_id"] != row["project_id"]
        or manifest["task_id"] != row["task_id"]
        or manifest["authority_snapshot_id"] != row["authority_snapshot_id"]
        or manifest["acceptance_criterion_id"]
        != row["acceptance_criterion_id"]
        or manifest["verification_criterion_id"]
        != row["verification_criterion_id"]
        or (
            manifest["target_kind"], manifest["target_value"],
            manifest["target_base_revision"], manifest["target_generation"],
        )
        != (
            row["target_kind"], row["target_value"],
            row["target_base_revision"], row["target_generation"],
        )
        or (
            cycle.review_target_kind, cycle.review_target_value,
            cycle.review_target_base_revision,
            cycle.review_target_generation,
        )
        != (
            row["target_kind"], row["target_value"],
            row["target_base_revision"], row["target_generation"],
        )
        or row["target_capture_version"] != 1
        or type(row["target_capture_version"]) is not int
        or type(omission_mask) is not int
        or not 0 <= omission_mask <= 15
        or type(row["sealed_at"]) is not str
        or type(row["bundle_digest"]) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(row["bundle_digest"]) is None
        or type(payload_size) is not int
        or not 1 <= payload_size <= COMPLETION_EVIDENCE_BUNDLE_MAX_BYTES
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            row["sealed_at"],
            field="completion Evidence Bundle seal time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    runner_generation = (
        runner_generations.get(
            (
                str(row["project_id"]),
                str(row["task_id"]),
                int(row["target_generation"]),
            )
        )
        if runner_generations is not None
        else None
    )
    resolution = (
        runner_generation["resolution"] if runner_generation is not None else None
    )
    attempt = runner_generation["attempt"] if runner_generation is not None else None
    observation = (
        runner_generation["observation"] if runner_generation is not None else None
    )
    cleanup = (
        runner_generation["cleanup_event"] if runner_generation is not None else None
    )
    runner_eligibility_one = (
        resolution is not None and resolution.gate_eligibility_version == 1
    )
    if (
        runner_eligibility_one
        and row["source_schema_version"] not in {
            PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
        }
    ):
        raise evidence_ledger_inconsistent()
    runner_identity_matches = (
        runner_eligibility_one
        and runner_generation is not None
        and runner_generation["state"] == "terminal"
        and attempt is not None
        and attempt.gate_eligibility_version == 1
        and observation is not None
        and observation.gate_eligibility_version == 1
        and cleanup is not None
        and cleanup.terminal_observation_id
        == observation.verification_runner_observation_id
        and resolution.contract_revision == row["contract_revision"]
        and resolution.verification_expectation_digest
        == cycle.verification_expectation_digest
        and resolution.authority_snapshot_id == row["authority_snapshot_id"]
        and resolution.verification_criterion_id
        == row["verification_criterion_id"]
        and resolution.target_kind == row["target_kind"]
        and resolution.target_value == row["target_value"]
        and (resolution.target_base_revision or "") == row["target_base_revision"]
        and resolution.target_generation == row["target_generation"]
        and resolution.artifact_manifest_id == row["artifact_manifest_id"]
    )
    if verification_basis_kind == "runner_observation":
        if (
            verification_receipt_id is not None
            or type(verification_runner_observation_id) is not str
            or row["verification_criterion_id"] is None
        ):
            raise evidence_ledger_inconsistent()
        if (
            not runner_identity_matches
            or observation.verification_runner_observation_id
            != verification_runner_observation_id
            or observation.route != "runner"
            or observation.launch_state != "launched"
            or observation.outcome != "pass"
            or observation.reason is not None
            or observation.complete_plan != 1
            or observation.total_step_count != resolution.step_count
            or observation.completed_step_count != resolution.step_count
            or observation.failed_step_ordinal is not None
        ):
            raise evidence_ledger_inconsistent()
    elif row["verification_criterion_id"] is None:
        if runner_eligibility_one or verification_receipt_id is not None:
            raise evidence_ledger_inconsistent()
    else:
        if runner_eligibility_one and (
            not runner_identity_matches
            or observation.route != "m21_fallback"
            or observation.launch_state != "no_launch"
            or observation.outcome != "blocked_prelaunch"
            or observation.reason not in {"runtime_unavailable", "process_setup_failed"}
            or observation.complete_plan != 0
        ):
            raise evidence_ledger_inconsistent()
        receipt = verification_receipts.get(str(verification_receipt_id))
        if (
            receipt is None
            or verification_receipt_id != cycle.verification_receipt_id
            or receipt["project_id"] != row["project_id"]
            or receipt["task_id"] != row["task_id"]
            or receipt["verification_subject_basis_version"] != 1
            or receipt["subject_authority_snapshot_id"]
            != row["authority_snapshot_id"]
            or receipt["subject_verification_criterion_id"]
            != row["verification_criterion_id"]
        ):
            raise evidence_ledger_inconsistent()

    reference_members = member_groups.get("evidence_reference", [])
    link_members = member_groups.get("criterion_link", [])
    member_reference_ids = {
        str(member["evidence_reference_id"]) for member in reference_members
    }
    member_link_ids = {
        str(member["criterion_evidence_link_id"]) for member in link_members
    }
    if any(
        str(links[link_id]["evidence_reference_id"]) not in member_reference_ids
        for link_id in member_link_ids
    ):
        raise evidence_ledger_inconsistent()
    source_keys = {
        (
            str(references[reference_id]["source_kind"]),
            str(references[reference_id]["source_id"]),
        ): reference_id
        for reference_id in member_reference_ids
    }
    required_source_keys = {
        ("artifact_manifest", str(row["artifact_manifest_id"])),
        ("completion_evidence", cycle.completion_cycle_id),
        *(
            ("review_receipt", receipt_id)
            for receipt_id in cycle.gate_basis.qualifying_receipt_ids
        ),
    }
    if verification_receipt_id is not None:
        required_source_keys.add(
            ("verification_receipt", str(verification_receipt_id))
        )
    if verification_runner_observation_id is not None:
        required_source_keys.add(
            ("runner_observation", str(verification_runner_observation_id))
        )
    for finding in finding_rows:
        if finding["evidence_reference_id"] is not None:
            required_source_keys.add(
                ("review_finding", str(finding["review_finding_id"]))
            )
    if set(source_keys) != required_source_keys:
        raise evidence_ledger_inconsistent()

    expected_links: set[tuple[str, str, str]] = set()
    acceptance_id = row["acceptance_criterion_id"]
    verification_id = row["verification_criterion_id"]
    current_finding_source_ids = {
        str(finding["review_finding_id"])
        for finding in finding_rows
        if (
            finding["evidence_reference_id"] is not None
            and finding["target_generation"] == cycle.review_target_generation
        )
    }
    if acceptance_id is not None:
        for source_kind, source_id in required_source_keys:
            if (
                source_kind == "review_finding"
                and source_id not in current_finding_source_ids
            ):
                continue
            relation = {
                "artifact_manifest": "completion_basis",
                "completion_evidence": "completion_basis",
                "review_receipt": "review_assessment",
                "review_finding": "review_finding",
            }.get(source_kind)
            if relation is not None:
                expected_links.add(
                    (
                        str(acceptance_id),
                        source_keys[(source_kind, source_id)],
                        relation,
                    )
                )
    if verification_id is not None and verification_receipt_id is not None:
        expected_links.add(
            (
                str(verification_id),
                source_keys[("verification_receipt", str(verification_receipt_id))],
                "verification_attestation",
            )
        )
    if verification_id is not None and verification_runner_observation_id is not None:
        expected_links.add(
            (
                str(verification_id),
                source_keys[
                    ("runner_observation", str(verification_runner_observation_id))
                ],
                "runner_observation",
            )
        )
    actual_links = {
        (
            str(links[link_id]["criterion_id"]),
            str(links[link_id]["evidence_reference_id"]),
            str(links[link_id]["relation"]),
        )
        for link_id in member_link_ids
    }
    if actual_links != expected_links:
        raise evidence_ledger_inconsistent()

    expected_omission_mask = 0
    if acceptance_id is None:
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "acceptance_criterion_absent"
        ]
    if verification_id is None:
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "verification_criterion_absent"
        ]
    if manifest["omission_code"] == "artifact_content_not_observed":
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "artifact_content_not_observed"
        ]
    if any(finding["evidence_reference_id"] is None for finding in finding_rows):
        expected_omission_mask |= COMPLETION_BUNDLE_OMISSION_BITS[
            "historical_finding_reference_absent"
        ]
    if omission_mask != expected_omission_mask:
        raise evidence_ledger_inconsistent()


def _validate_completion_evidence_bundle_rows(
    connection: sqlite3.Connection,
    *,
    _validated_runner_generations: (
        dict[tuple[str, str, int], dict[str, Any]] | None
    ) = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    from task_governance_tool.evidence_projection_metadata_repository import (
        _projection_state_from_row,
    )
    from task_governance_tool.evidence_repository import (
        _criterion_link_relation_valid,
        _snapshot_criterion_links,
    )
    from task_governance_tool.storage import (
        COMPLETION_BUNDLE_MEMBER_KINDS,
        CRITERION_EVIDENCE_LINK_ID_PATTERN,
        CRITERION_EVIDENCE_RELATIONS,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        SHA256_DIGEST_PATTERN,
        StorageError,
        _cycle_from_row,
        current_schema_version,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )
    from task_governance_tool.verification_runner_repository import (
        _validated_verification_runner_graph,
    )

    criteria = {
        str(row["criterion_id"]): row
        for row in connection.execute(
            "SELECT * FROM contract_criteria ORDER BY criterion_id"
        ).fetchall()
    }
    references = {
        str(row["evidence_reference_id"]): row
        for row in connection.execute(
            "SELECT * FROM evidence_references ORDER BY evidence_reference_id"
        ).fetchall()
    }
    links: dict[str, sqlite3.Row] = {}
    for row in connection.execute(
        "SELECT * FROM criterion_evidence_links ORDER BY criterion_evidence_link_id"
    ).fetchall():
        link_id = row["criterion_evidence_link_id"]
        criterion_id = row["criterion_id"]
        reference_id = row["evidence_reference_id"]
        relation = row["relation"]
        producer_version = row["producer_version"]
        created_at = row["created_at"]
        criterion = criteria.get(str(criterion_id))
        reference = references.get(str(reference_id))
        if (
            type(link_id) is not str
            or CRITERION_EVIDENCE_LINK_ID_PATTERN.fullmatch(link_id) is None
            or link_id in links
            or type(criterion_id) is not str
            or type(reference_id) is not str
            or type(relation) is not str
            or relation not in CRITERION_EVIDENCE_RELATIONS
            or relation == "derived_analysis"
            or type(producer_version) is not int
            or producer_version <= 0
            or type(created_at) is not str
            or criterion is None
            or reference is None
            or criterion["project_id"] != row["project_id"]
            or criterion["task_id"] != row["task_id"]
            or reference["project_id"] != row["project_id"]
            or reference["task_id"] != row["task_id"]
            or reference["assurance_class"] != row["assurance_class"]
            or reference["producer_class"] != row["producer_class"]
            or reference["producer_version"] != producer_version
            or not _criterion_link_relation_valid(
                criterion_kind=criterion["criterion_kind"],
                source_kind=reference["source_kind"],
                relation=relation,
            )
        ):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                created_at,
                field="criterion Evidence Link creation time",
            )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc
        links[link_id] = row

    bundle_rows = connection.execute(
        "SELECT * FROM completion_evidence_bundles "
        "ORDER BY completion_evidence_bundle_id"
    ).fetchall()
    container_schema_version = current_schema_version(connection)
    if (
        _validated_runner_generations is None
        and bundle_rows
        and container_schema_version in {
            PRIVATE_SCHEMA21_VERSION, PRIVATE_SCHEMA22_VERSION,
        }
    ):
        _, _validated_runner_generations = _validated_verification_runner_graph(
            connection
        )
    bundles = {
        str(row["completion_evidence_bundle_id"]): row for row in bundle_rows
    }
    if len(bundles) != len(bundle_rows):
        raise evidence_ledger_inconsistent()

    member_rows = connection.execute(
        "SELECT * FROM completion_bundle_members "
        "ORDER BY completion_evidence_bundle_id, member_kind, ordinal"
    ).fetchall()
    members_by_bundle: dict[str, dict[str, list[sqlite3.Row]]] = {}
    used_link_ids: set[str] = set()
    for row in member_rows:
        bundle_id = row["completion_evidence_bundle_id"]
        member_kind = row["member_kind"]
        ordinal = row["ordinal"]
        if (
            type(bundle_id) is not str
            or bundle_id not in bundles
            or type(member_kind) is not str
            or member_kind not in COMPLETION_BUNDLE_MEMBER_KINDS
            or type(ordinal) is not int
            or ordinal < 0
            or bundles[bundle_id]["project_id"] != row["project_id"]
            or bundles[bundle_id]["task_id"] != row["task_id"]
        ):
            raise evidence_ledger_inconsistent()
        grouped = members_by_bundle.setdefault(bundle_id, {}).setdefault(
            member_kind,
            [],
        )
        if ordinal != len(grouped):
            raise evidence_ledger_inconsistent()
        if member_kind == "criterion_link":
            link_id = row["criterion_evidence_link_id"]
            if (
                type(link_id) is not str
                or row["evidence_reference_id"] is not None
                or link_id not in links
                or links[link_id]["project_id"] != row["project_id"]
                or links[link_id]["task_id"] != row["task_id"]
            ):
                raise evidence_ledger_inconsistent()
            used_link_ids.add(link_id)
        else:
            reference_id = row["evidence_reference_id"]
            if (
                type(reference_id) is not str
                or row["criterion_evidence_link_id"] is not None
                or reference_id not in references
                or references[reference_id]["project_id"] != row["project_id"]
                or references[reference_id]["task_id"] != row["task_id"]
            ):
                raise evidence_ledger_inconsistent()
        grouped.append(row)
    bundle_link_ids = {
        link_id
        for link_id, link in links.items()
        if link["relation"] != "runner_observation"
    }
    if not bundle_link_ids.issubset(used_link_ids):
        raise evidence_ledger_inconsistent()

    snapshots_by_bundle: dict[str, list[sqlite3.Row]] = {}
    for row in connection.execute(
        "SELECT * FROM completion_bundle_finding_snapshots "
        "ORDER BY completion_evidence_bundle_id, ordinal"
    ).fetchall():
        bundle_id = row["completion_evidence_bundle_id"]
        ordinal = row["ordinal"]
        producer_version = row["producer_version"]
        reference_id = row["evidence_reference_id"]
        if (
            type(bundle_id) is not str
            or bundle_id not in bundles
            or type(ordinal) is not int
            or ordinal < 0
            or type(row["review_finding_id"]) is not str
            or type(row["review_receipt_id"]) is not str
            or type(row["target_generation"]) is not int
            or row["target_generation"] <= 0
            or type(row["summary"]) is not str
            or type(row["resolution_summary"]) is not str
            or type(row["created_at"]) is not str
            or type(producer_version) is not int
            or producer_version != 1
            or type(row["digest"]) is not str
            or SHA256_DIGEST_PATTERN.fullmatch(row["digest"]) is None
            or bundles[bundle_id]["project_id"] != row["project_id"]
            or bundles[bundle_id]["task_id"] != row["task_id"]
            or (
                reference_id is None
                and (
                    row["assurance_class"] != "legacy_unknown"
                    or row["producer_class"] != "legacy_migration"
                )
            )
            or (
                reference_id is not None
                and (
                    type(reference_id) is not str
                    or reference_id not in references
                    or row["assurance_class"] != "bound_attestation"
                    or row["producer_class"] != "trusted_caller"
                    or references[reference_id]["source_kind"]
                    != "review_finding"
                    or references[reference_id]["source_id"]
                    != row["review_finding_id"]
                )
            )
        ):
            raise evidence_ledger_inconsistent()
        grouped = snapshots_by_bundle.setdefault(bundle_id, [])
        if ordinal != len(grouped):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                row["created_at"],
                field="completion Finding snapshot creation time",
            )
            if row["resolved_at"] is not None:
                if type(row["resolved_at"]) is not str:
                    raise evidence_ledger_inconsistent()
                validate_utc_timestamp(
                    row["resolved_at"],
                    field="completion Finding snapshot resolution time",
                )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc
        grouped.append(row)

    snapshots = {
        str(row["authority_snapshot_id"]): row
        for row in connection.execute(
            "SELECT * FROM authority_snapshots ORDER BY authority_snapshot_id"
        ).fetchall()
    }
    snapshot_links = _snapshot_criterion_links(connection)
    manifests = {
        str(row["artifact_manifest_id"]): row
        for row in connection.execute(
            "SELECT * FROM artifact_manifests ORDER BY artifact_manifest_id"
        ).fetchall()
    }
    verification_receipts = {
        str(row["verification_receipt_id"]): row
        for row in connection.execute(
            "SELECT * FROM verification_receipts ORDER BY verification_receipt_id"
        ).fetchall()
    }
    cycle_rows = connection.execute(
        "SELECT * FROM task_completion_cycles "
        "ORDER BY project_id, task_id, saved_cycle_ordinal"
    ).fetchall()
    cycles = {
        cycle.completion_cycle_id: cycle
        for cycle in (_cycle_from_row(row) for row in cycle_rows)
    }
    if len(cycles) != len(cycle_rows):
        raise evidence_ledger_inconsistent()

    for bundle_id, row in bundles.items():
        _validate_one_completion_evidence_bundle_row(
            bundle_id=bundle_id,
            row=row,
            container_schema_version=container_schema_version,
            cycle=cycles.get(str(row["completion_cycle_id"])),
            snapshot=snapshots.get(str(row["authority_snapshot_id"])),
            owner_links=snapshot_links.get(
                str(row["authority_snapshot_id"]),
                {},
            ),
            manifest=manifests.get(str(row["artifact_manifest_id"])),
            verification_receipts=verification_receipts,
            member_groups=members_by_bundle.get(bundle_id, {}),
            links=links,
            references=references,
            finding_rows=snapshots_by_bundle.get(bundle_id, []),
            runner_generations=_validated_runner_generations,
        )

    for cycle in cycles.values():
        if cycle.evidence_basis_version == 1:
            if cycle.completion_evidence_bundle_id not in bundles:
                raise evidence_ledger_inconsistent()
        elif cycle.completion_evidence_bundle_id is not None:
            raise evidence_ledger_inconsistent()
    if {
        str(row["completion_cycle_id"]) for row in bundle_rows
    } != {
        cycle.completion_cycle_id
        for cycle in cycles.values()
        if cycle.evidence_basis_version == 1
    }:
        raise evidence_ledger_inconsistent()

    project_rows = connection.execute(
        "SELECT project_id FROM project_meta ORDER BY project_id"
    ).fetchall()
    projection_rows = connection.execute(
        "SELECT * FROM evidence_projection_state ORDER BY project_id"
    ).fetchall()
    projection_states = {
        state.project_id: state
        for state in (_projection_state_from_row(row) for row in projection_rows)
    }
    project_ids = {
        str(row["project_id"])
        for row in project_rows
        if type(row["project_id"]) is str and row["project_id"]
    }
    if len(project_ids) != len(project_rows) or set(projection_states) != project_ids:
        raise evidence_ledger_inconsistent()
    cycle_counts: dict[str, int] = {project_id: 0 for project_id in project_ids}
    for cycle in cycles.values():
        if cycle.project_id not in cycle_counts:
            raise evidence_ledger_inconsistent()
        cycle_counts[cycle.project_id] += 1
    if any(
        projection_states[project_id].source_generation != cycle_count
        for project_id, cycle_count in cycle_counts.items()
    ):
        raise evidence_ledger_inconsistent()
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise evidence_ledger_inconsistent()
    return (
        _validated_runner_generations
        if _validated_runner_generations is not None
        else {}
    )


def _validated_completion_evidence_projection_bases(
    connection: sqlite3.Connection,
    *,
    _validated_runner_generations: (
        dict[tuple[str, str, int], dict[str, Any]] | None
    ) = None,
) -> dict[str, _storage.EvidenceProjectionBasis]:
    from task_governance_tool.storage import (
        _validate_completion_evidence_bundle_schema_contract,
        evidence_ledger_inconsistent,
    )

    _validate_completion_evidence_bundle_schema_contract(connection)
    runner_generations = _validate_completion_evidence_bundle_rows(
        connection,
        _validated_runner_generations=_validated_runner_generations,
    )
    project_rows = connection.execute(
        "SELECT project_id FROM project_meta ORDER BY project_id"
    ).fetchall()
    project_ids = tuple(row["project_id"] for row in project_rows)
    if (
        any(
            type(project_id) is not str or not project_id
            for project_id in project_ids
        )
        or len(set(project_ids)) != len(project_ids)
    ):
        raise evidence_ledger_inconsistent()
    result: dict[str, EvidenceProjectionBasis] = {}
    for project_id in project_ids:
        basis = _capture_evidence_projection_basis_rows(
            connection,
            project_id=project_id,
            _validated_runner_generations=runner_generations,
        )
        for record in basis.native_bundles:
            _validate_projection_bundle_record(record)
        result[project_id] = basis
    return result


def validate_completion_evidence_bundle_storage(
    connection: sqlite3.Connection,
) -> None:
    _validated_completion_evidence_projection_bases(connection)


def validate_selected_task_receipt_evidence(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    review_receipt_ids: set[str],
    review_finding_ids: set[str],
    verification_receipt_ids: set[str],
) -> None:
    """Validate one bounded Receipt/Finding source set and its bindings."""

    from task_governance_tool.evidence_repository import (
        _artifact_manifest_target_key,
        _validate_artifact_manifest_storage,
        _validated_authority_context,
    )
    from task_governance_tool.review_repository import (
        _validate_review_finding_base_row,
        _validate_review_receipt_base_row,
    )
    from task_governance_tool.storage import (
        COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE,
        StorageError,
        _selected_storage_rows_by_ids,
        _validate_stored_verification_subject_rows,
        evidence_ledger_boundary_error,
        evidence_ledger_inconsistent,
        evidence_ledger_sqlite_error,
    )
    from task_governance_tool.verification_receipt_repository import (
        _validate_verification_receipt_row,
    )


    if (
        type(project_id) is not str
        or not project_id
        or type(task_id) is not str
        or not task_id
        or type(review_receipt_ids) is not set
        or type(review_finding_ids) is not set
        or type(verification_receipt_ids) is not set
        or len(review_receipt_ids) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        or len(review_finding_ids) > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        or len(verification_receipt_ids)
        > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        or any(
            type(value) is not str or not value
            for value in (
                *review_receipt_ids,
                *review_finding_ids,
                *verification_receipt_ids,
            )
        )
    ):
        raise evidence_ledger_inconsistent()
    try:
        review_privacy_successes: set[tuple[str, str, str]] = set()
        finding_rows = _selected_storage_rows_by_ids(
            connection,
            table_name="review_findings",
            id_field="review_finding_id",
            selected_ids=review_finding_ids,
        )
        parent_receipt_ids: set[str] = set()
        for row in finding_rows:
            _validate_review_finding_base_row(
                row,
                privacy_success_cache=review_privacy_successes,
            )
            receipt_id = row["review_receipt_id"]
            if type(receipt_id) is not str or not receipt_id:
                raise evidence_ledger_inconsistent()
            parent_receipt_ids.add(receipt_id)
        selected_review_receipt_ids = review_receipt_ids | parent_receipt_ids
        if (
            len(selected_review_receipt_ids)
            > COMPLETION_RECEIPT_VALIDATION_CHUNK_SIZE
        ):
            raise evidence_ledger_inconsistent()
        review_rows = _selected_storage_rows_by_ids(
            connection,
            table_name="review_receipts",
            id_field="review_receipt_id",
            selected_ids=selected_review_receipt_ids,
        )
        verification_rows = _selected_storage_rows_by_ids(
            connection,
            table_name="verification_receipts",
            id_field="verification_receipt_id",
            selected_ids=verification_receipt_ids,
        )
        for row in review_rows:
            _validate_review_receipt_base_row(
                row,
                privacy_success_cache=review_privacy_successes,
            )
            if row["project_id"] != project_id or row["task_id"] != task_id:
                raise evidence_ledger_inconsistent()
        for row in verification_rows:
            try:
                receipt = _validate_verification_receipt_row(dict(row))
            except StorageError as exc:
                raise evidence_ledger_boundary_error(exc) from exc
            if receipt["project_id"] != project_id or receipt["task_id"] != task_id:
                raise evidence_ledger_inconsistent()

        target_keys = {
            _artifact_manifest_target_key(
                project_id=row["project_id"],
                task_id=row["task_id"],
                target_kind=row["target_kind"],
                target_value=row["target_value"],
                target_base_revision=row["target_base_revision"],
                target_generation=row["target_generation"],
            )
            for row in (*review_rows, *verification_rows)
        }
        targets_json = json.dumps(
            [list(key) for key in sorted(target_keys)],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        manifest_rows = connection.execute(
            """
            WITH selected_targets(value) AS (
                SELECT value FROM json_each(?)
            )
            SELECT manifest.artifact_manifest_id,
                   manifest.authority_snapshot_id
              FROM artifact_manifests AS manifest
              JOIN selected_targets AS selected
                ON manifest.project_id = json_extract(selected.value, '$[0]')
               AND manifest.task_id = json_extract(selected.value, '$[1]')
               AND manifest.target_kind = json_extract(selected.value, '$[2]')
               AND manifest.target_value = json_extract(selected.value, '$[3]')
               AND manifest.target_base_revision = json_extract(selected.value, '$[4]')
               AND manifest.target_generation = json_extract(selected.value, '$[5]')
             ORDER BY manifest.artifact_manifest_id
            """,
            (targets_json,),
        ).fetchall()
        manifest_ids: set[str] = set()
        snapshot_ids: set[str] = set()
        for row in manifest_rows:
            manifest_id = row["artifact_manifest_id"]
            snapshot_id = row["authority_snapshot_id"]
            if (
                type(manifest_id) is not str
                or manifest_id in manifest_ids
                or type(snapshot_id) is not str
            ):
                raise evidence_ledger_inconsistent()
            manifest_ids.add(manifest_id)
            snapshot_ids.add(snapshot_id)

        authority = _validated_authority_context(
            connection,
            snapshot_ids=snapshot_ids,
        )
        manifests, manifests_by_target = _validate_artifact_manifest_storage(
            connection,
            snapshots=authority.snapshots,
            links=authority.links,
            manifest_ids=manifest_ids,
        )
        _validate_stored_verification_subject_rows(
            verification_rows,
            table_name="verification_receipts",
            snapshots=authority.snapshots,
            criteria=authority.criteria,
            links=authority.links,
        )
        _validate_evidence_reference_storage(
            connection,
            manifests=manifests,
            manifests_by_target=manifests_by_target,
            snapshots=authority.snapshots,
            verification_receipt_ids=verification_receipt_ids,
            review_receipt_ids=selected_review_receipt_ids,
            review_finding_ids=review_finding_ids,
            completion_cycle_ids=set(),
            selected_project_id=project_id,
            privacy_success_cache=review_privacy_successes,
        )
    except sqlite3.Error as exc:
        raise evidence_ledger_sqlite_error(exc) from exc


def _validate_projection_bundle_record(
    record: _storage.ProjectionBundleRecord,
) -> None:
    from task_governance_tool.storage import (
        evidence_ledger_inconsistent,
    )

    from task_governance_tool.evidence_projection import (
        EvidenceProjectionError,
        build_projection_bundle_artifact,
    )

    try:
        build_projection_bundle_artifact(record)
    except EvidenceProjectionError as exc:
        raise evidence_ledger_inconsistent() from exc


def read_completion_evidence_bundle(
    connection: sqlite3.Connection,
    *,
    completion_evidence_bundle_id: str,
) -> _storage.PreparedCompletionEvidenceBundle:
    from task_governance_tool.storage import (
        COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN,
        PreparedCompletionBundleMember,
        PreparedCompletionEvidenceBundle,
        PreparedCompletionFindingSnapshot,
        PreparedCriterionEvidenceLink,
        evidence_ledger_inconsistent,
    )

    if (
        type(completion_evidence_bundle_id) is not str
        or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
            completion_evidence_bundle_id
        )
        is None
    ):
        raise evidence_ledger_inconsistent()
    row = connection.execute(
        "SELECT * FROM completion_evidence_bundles "
        "WHERE completion_evidence_bundle_id = ?",
        (completion_evidence_bundle_id,),
    ).fetchone()
    if row is None:
        raise evidence_ledger_inconsistent()
    member_rows = connection.execute(
        "SELECT * FROM completion_bundle_members "
        "WHERE completion_evidence_bundle_id = ? "
        "ORDER BY member_kind, ordinal",
        (completion_evidence_bundle_id,),
    ).fetchall()
    link_rows = connection.execute(
        """
        SELECT link.*
          FROM completion_bundle_members AS member
          JOIN criterion_evidence_links AS link
            ON link.criterion_evidence_link_id =
               member.criterion_evidence_link_id
         WHERE member.completion_evidence_bundle_id = ?
           AND member.member_kind = 'criterion_link'
         ORDER BY member.ordinal
        """,
        (completion_evidence_bundle_id,),
    ).fetchall()
    finding_rows = connection.execute(
        "SELECT * FROM completion_bundle_finding_snapshots "
        "WHERE completion_evidence_bundle_id = ? ORDER BY ordinal",
        (completion_evidence_bundle_id,),
    ).fetchall()
    return PreparedCompletionEvidenceBundle(
        completion_evidence_bundle_id=str(row["completion_evidence_bundle_id"]),
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]),
        completion_cycle_id=str(row["completion_cycle_id"]),
        cycle_ordinal=row["cycle_ordinal"],
        source_schema_version=row["source_schema_version"],
        bundle_version=row["bundle_version"],
        contract_revision=row["contract_revision"],
        authority_snapshot_id=str(row["authority_snapshot_id"]),
        acceptance_criterion_id=row["acceptance_criterion_id"],
        verification_criterion_id=row["verification_criterion_id"],
        target_kind=str(row["target_kind"]),
        target_value=str(row["target_value"]),
        target_base_revision=str(row["target_base_revision"]),
        target_generation=row["target_generation"],
        target_capture_version=row["target_capture_version"],
        artifact_manifest_id=str(row["artifact_manifest_id"]),
        verification_receipt_id=row["verification_receipt_id"],
        verification_basis_kind=(
            row["verification_basis_kind"]
            if "verification_basis_kind" in row.keys()
            else None
        ),
        verification_runner_observation_id=(
            row["verification_runner_observation_id"]
            if "verification_runner_observation_id" in row.keys()
            else None
        ),
        omission_mask=row["omission_mask"],
        sealed_at=str(row["sealed_at"]),
        bundle_digest=str(row["bundle_digest"]),
        payload_size_bytes=row["payload_size_bytes"],
        criterion_links=tuple(
            PreparedCriterionEvidenceLink(**dict(link_row))
            for link_row in link_rows
        ),
        members=tuple(
            PreparedCompletionBundleMember(**dict(member_row))
            for member_row in member_rows
        ),
        finding_snapshots=tuple(
            PreparedCompletionFindingSnapshot(**dict(finding_row))
            for finding_row in finding_rows
        ),
    )


def read_native_completion_bundle_basis_locked(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    cycle: _storage.CompletionCycle,
    completion_reference: dict[str, Any],
) -> _storage.NativeCompletionBundleBasis:
    """Read one validated, bounded seal-time basis under the Task writer.

    Review Receipt elements are ``{"receipt": ..., "provenance": ...}`` in
    ``cycle.gate_basis.qualifying_receipt_ids`` order. Findings are ordered by
    numeric target generation, creation time, then Finding ID; the positional
    ``finding_references`` tuple contains the matching Reference or ``None``.
    """

    from task_governance_tool.evidence_repository import (
        _validate_artifact_manifest_storage,
        _validated_authority_context,
    )
    from task_governance_tool.review_repository import (
        _validate_review_finding_base_row,
        read_review_receipt_with_provenance,
    )
    from task_governance_tool.storage import (
        NativeCompletionBundleBasis,
        SHA256_DIGEST_PATTERN,
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS,
        _completion_basis_reference,
        _read_validated_current_task_row,
        _require_completion_capture_activation_locked,
        _require_evidence_writer,
        _validate_completion_cycle,
        evidence_ledger_inconsistent,
    )
    from task_governance_tool.verification_receipt_repository import (
        _validate_verification_receipt_row,
    )
    from task_governance_tool.verification_runner_repository import (
        _validated_verification_runner_graph,
    )


    _require_evidence_writer(connection)
    source_schema_version = _require_completion_capture_activation_locked(connection)
    _validate_completion_cycle(cycle)
    if (
        cycle.project_id != project_id
        or cycle.task_id != task_id
        or cycle.origin != "native_done"
        or cycle.evidence_basis_version != 1
        or cycle.completion_evidence_bundle_id is None
        or not isinstance(completion_reference, dict)
        or set(completion_reference)
        != _EVIDENCE_LEDGER_REQUIRED_COLUMNS["evidence_references"]
    ):
        raise evidence_ledger_inconsistent()

    locked = _read_validated_current_task_row(
        connection,
        project_id=project_id,
        task_id=task_id,
    )
    if locked is None:
        raise evidence_ledger_inconsistent()
    task = dict(locked)
    if (
        task.get("status") == "done"
        or task.get("current_contract_revision") != cycle.contract_revision
        or task.get("review_tier") != cycle.review_tier
        or (
            task.get("review_target_kind"),
            task.get("review_target_value"),
            task.get("review_target_base_revision"),
            task.get("review_target_generation"),
        )
        != (
            cycle.review_target_kind,
            cycle.review_target_value,
            cycle.review_target_base_revision,
            cycle.review_target_generation,
        )
        or task.get("review_target_capture_version") != 1
        or type(task.get("review_target_authority_snapshot_id")) is not str
        or type(task.get("review_target_artifact_manifest_id")) is not str
    ):
        raise evidence_ledger_inconsistent()

    authority = _validated_authority_context(connection)
    snapshot_id = str(task["review_target_authority_snapshot_id"])
    snapshot = authority.snapshots.get(snapshot_id)
    criterion_bindings = authority.links.get(snapshot_id, {})
    if (
        snapshot is None
        or snapshot["project_id"] != project_id
        or snapshot["task_id"] != task_id
        or snapshot["contract_revision"] != cycle.contract_revision
        or snapshot["task_title"] != task.get("title")
        or snapshot["task_description"] != task.get("description")
        or snapshot["review_tier"] != task.get("review_tier")
        or snapshot["verification"] != task.get("verification")
        or criterion_bindings.get("acceptance")
        != task.get("review_target_acceptance_criterion_id")
        or criterion_bindings.get("verification")
        != task.get("review_target_verification_criterion_id")
    ):
        raise evidence_ledger_inconsistent()
    criteria: list[dict[str, Any]] = []
    for kind in ("acceptance", "verification"):
        criterion_id = criterion_bindings.get(kind)
        if criterion_id is None:
            continue
        criterion = authority.criteria.get(criterion_id)
        if (
            criterion is None
            or criterion["project_id"] != project_id
            or criterion["task_id"] != task_id
            or criterion["criterion_kind"] != kind
        ):
            raise evidence_ledger_inconsistent()
        criteria.append(dict(criterion))

    manifest_id = str(task["review_target_artifact_manifest_id"])
    manifests, _by_target = _validate_artifact_manifest_storage(
        connection,
        snapshots=authority.snapshots,
        links=authority.links,
        manifest_ids={manifest_id},
    )
    manifest_record = manifests.get(manifest_id)
    if manifest_record is None:
        raise evidence_ledger_inconsistent()
    manifest = dict(manifest_record.row)
    artifact_entries = tuple(
        dict(row)
        for row in connection.execute(
            "SELECT * FROM artifact_manifest_entries "
            "WHERE artifact_manifest_id = ? ORDER BY ordinal",
            (manifest_id,),
        ).fetchall()
    )
    if len(artifact_entries) != manifest["entry_count"]:
        raise evidence_ledger_inconsistent()
    artifact_reference = _completion_basis_reference(
        connection,
        project_id=project_id,
        task_id=task_id,
        source_kind="artifact_manifest",
        source_id=manifest_id,
    )
    if artifact_reference is None:
        raise evidence_ledger_inconsistent()

    verification_receipt: dict[str, Any] | None = None
    verification_reference: dict[str, Any] | None = None
    runner_observation: dict[str, Any] | None = None
    runner_reference: dict[str, Any] | None = None
    runner_criterion_link: dict[str, Any] | None = None
    if cycle.verification_basis_kind == "runner_observation":
        from task_governance_tool.verification_runner import (
            runner_observation_source_projection,
        )

        observation_id = cycle.verification_runner_observation_id
        runner_generation_key = (
            project_id,
            task_id,
            cycle.review_target_generation,
        )
        _runner_references, runner_generations = (
            _validated_verification_runner_graph(
                connection,
                selected_generation=runner_generation_key,
                selected_task=locked,
            )
        )
        runner_generation = runner_generations.get(runner_generation_key)
        resolution = (
            runner_generation["resolution"]
            if runner_generation is not None
            else None
        )
        observation = (
            runner_generation["observation"]
            if runner_generation is not None
            else None
        )
        if (
            type(observation_id) is not str
            or runner_generation is None
            or runner_generation["state"] != "terminal"
            or resolution is None
            or observation is None
            or resolution.gate_eligibility_version != 1
            or observation.gate_eligibility_version != 1
            or observation.verification_runner_observation_id
            != observation_id
            or resolution.contract_revision != cycle.contract_revision
            or resolution.authority_snapshot_id != snapshot_id
            or resolution.verification_criterion_id
            != criterion_bindings.get("verification")
            or resolution.artifact_manifest_id != manifest_id
            or (
                resolution.target_kind,
                resolution.target_value,
                resolution.target_base_revision or "",
                resolution.target_generation,
            )
            != (
                cycle.review_target_kind,
                cycle.review_target_value,
                cycle.review_target_base_revision,
                cycle.review_target_generation,
            )
        ):
            raise evidence_ledger_inconsistent()
        resolution_row = connection.execute(
            "SELECT * FROM verification_runner_resolutions "
            "WHERE verification_runner_resolution_id = ?",
            (resolution.verification_runner_resolution_id,),
        ).fetchone()
        observation_row = connection.execute(
            "SELECT * FROM verification_runner_observations "
            "WHERE verification_runner_observation_id = ?",
            (observation_id,),
        ).fetchone()
        if resolution_row is None or observation_row is None:
            raise evidence_ledger_inconsistent()
        runner_observation = runner_observation_source_projection(
            observation=dict(observation_row),
            resolution=dict(resolution_row),
        )
        runner_reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="runner_observation",
            source_id=observation_id,
        )
        if runner_reference is None:
            raise evidence_ledger_inconsistent()
        link_rows = connection.execute(
            "SELECT * FROM criterion_evidence_links "
            "WHERE project_id = ? AND task_id = ? "
            "AND criterion_id = ? AND evidence_reference_id = ? "
            "AND relation = 'runner_observation' "
            "ORDER BY criterion_evidence_link_id LIMIT 2",
            (
                project_id,
                task_id,
                resolution.verification_criterion_id,
                runner_reference["evidence_reference_id"],
            ),
        ).fetchall()
        if len(link_rows) != 1:
            raise evidence_ledger_inconsistent()
        runner_criterion_link = dict(link_rows[0])
    elif cycle.verification_receipt_id is not None:
        stored_verification = connection.execute(
            "SELECT * FROM verification_receipts "
            "WHERE verification_receipt_id = ?",
            (cycle.verification_receipt_id,),
        ).fetchone()
        if stored_verification is None:
            raise evidence_ledger_inconsistent()
        validated_verification = _validate_verification_receipt_row(
            dict(stored_verification)
        )
        if (
            validated_verification["project_id"] != project_id
            or validated_verification["task_id"] != task_id
            or validated_verification["verification_subject_basis_version"]
            != 1
            or validated_verification["subject_authority_snapshot_id"]
            != snapshot_id
            or validated_verification["subject_verification_criterion_id"]
            != criterion_bindings.get("verification")
        ):
            raise evidence_ledger_inconsistent()
        verification_receipt = {
            "verification_receipt_id": cycle.verification_receipt_id,
            "verification_subject": {
                "basis_version": 1,
                "kind": "task_verification_criterion",
                "authority_snapshot_id": snapshot_id,
                "verification_criterion_id": criterion_bindings.get(
                    "verification"
                ),
            },
            "result": validated_verification["result"],
            "duration_ms": validated_verification["duration_ms"],
            "scope_coverage": validated_verification["scope_coverage"],
            "created_at": validated_verification["created_at"],
        }
        verification_reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="verification_receipt",
            source_id=cycle.verification_receipt_id,
        )
        if verification_reference is None:
            raise evidence_ledger_inconsistent()
    elif criterion_bindings.get("verification") is not None:
        raise evidence_ledger_inconsistent()

    review_receipts: list[dict[str, Any]] = []
    review_references: list[dict[str, Any]] = []
    for receipt_id in cycle.gate_basis.qualifying_receipt_ids:
        value = read_review_receipt_with_provenance(
            connection,
            review_receipt_id=receipt_id,
        )
        reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="review_receipt",
            source_id=receipt_id,
        )
        if value is None or reference is None:
            raise evidence_ledger_inconsistent()
        receipt = value["receipt"]
        provenance = value["provenance"]
        if (
            receipt["project_id"] != project_id
            or receipt["task_id"] != task_id
            or (
                receipt["target_kind"], receipt["target_value"],
                receipt["target_base_revision"],
                receipt["target_generation"],
            )
            != (
                cycle.review_target_kind, cycle.review_target_value,
                cycle.review_target_base_revision,
                cycle.review_target_generation,
            )
            or (
                receipt["receipt_kind"] != "not_required"
                and provenance is None
            )
            or (
                receipt["receipt_kind"] == "not_required"
                and provenance is not None
            )
        ):
            raise evidence_ledger_inconsistent()
        review_receipts.append(value)
        review_references.append(reference)

    finding_rows = connection.execute(
        """
        SELECT finding.*, receipt.target_generation AS target_generation
          FROM review_findings AS finding
          JOIN review_receipts AS receipt
            ON receipt.review_receipt_id = finding.review_receipt_id
         WHERE receipt.project_id = ? AND receipt.task_id = ?
           AND (
             receipt.target_generation = ?
             OR (
               receipt.target_generation < ?
               AND finding.severity IN ('high', 'medium')
             )
           )
         ORDER BY receipt.target_generation,
                  finding.created_at COLLATE BINARY,
                  finding.review_finding_id COLLATE BINARY
        """,
        (
            project_id,
            task_id,
            cycle.review_target_generation,
            cycle.review_target_generation,
        ),
    ).fetchall()
    findings: list[dict[str, Any]] = []
    finding_references: list[dict[str, Any] | None] = []
    for stored_finding in finding_rows:
        finding = dict(stored_finding)
        _validate_review_finding_base_row(finding)
        reference = _completion_basis_reference(
            connection,
            project_id=project_id,
            task_id=task_id,
            source_kind="review_finding",
            source_id=str(finding["review_finding_id"]),
            required=False,
        )
        if (
            finding["target_generation"] == cycle.review_target_generation
            and reference is None
        ):
            raise evidence_ledger_inconsistent()
        findings.append(finding)
        finding_references.append(reference)

    completion_dispatch = {
        "git_commit": ("machine_observed", "taskgov_git"),
        "external_revision": ("external_reference", "external_system"),
        "commit_not_required": ("bound_attestation", "trusted_caller"),
    }
    expected_attribution = completion_dispatch.get(
        cycle.completion_evidence_kind
    )
    stored_completion_reference = _completion_basis_reference(
        connection,
        project_id=project_id,
        task_id=task_id,
        source_kind="completion_evidence",
        source_id=cycle.completion_cycle_id,
        required=False,
    )
    if stored_completion_reference is not None:
        if stored_completion_reference != completion_reference:
            raise evidence_ledger_inconsistent()
    if (
        expected_attribution is None
        or completion_reference["project_id"] != project_id
        or completion_reference["task_id"] != task_id
        or completion_reference["source_kind"] != "completion_evidence"
        or completion_reference["source_state"]
        != cycle.completion_evidence_kind
        or completion_reference["source_id"] != cycle.completion_cycle_id
        or completion_reference["completion_cycle_id"]
        != cycle.completion_cycle_id
        or completion_reference["contract_revision"]
        != cycle.contract_revision
        or completion_reference["authority_snapshot_id"] != snapshot_id
        or completion_reference["acceptance_criterion_id"]
        != criterion_bindings.get("acceptance")
        or completion_reference["verification_criterion_id"]
        != criterion_bindings.get("verification")
        or (
            completion_reference["target_kind"],
            completion_reference["target_value"],
            completion_reference["target_base_revision"],
            completion_reference["target_generation"],
        )
        != (
            cycle.review_target_kind,
            cycle.review_target_value,
            cycle.review_target_base_revision,
            cycle.review_target_generation,
        )
        or (
            completion_reference["assurance_class"],
            completion_reference["producer_class"],
        )
        != expected_attribution
        or completion_reference["producer_version"] != 1
        or type(completion_reference["digest"]) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(completion_reference["digest"])
        is None
    ):
        raise evidence_ledger_inconsistent()

    return NativeCompletionBundleBasis(
        source_schema_version=source_schema_version,
        task={
            "task_id": task_id,
            "title": snapshot["task_title"],
            "description": snapshot["task_description"],
            "review_tier": snapshot["review_tier"],
            "verification": snapshot["verification"],
        },
        authority_snapshot=dict(snapshot),
        criteria=tuple(criteria),
        artifact_manifest=manifest,
        artifact_entries=artifact_entries,
        artifact_reference=artifact_reference,
        verification_receipt=verification_receipt,
        verification_reference=verification_reference,
        runner_observation=runner_observation,
        runner_reference=runner_reference,
        runner_criterion_link=runner_criterion_link,
        review_receipts=tuple(review_receipts),
        review_references=tuple(review_references),
        findings=tuple(findings),
        finding_references=tuple(finding_references),
        completion_reference=dict(completion_reference),
    )


def _projection_bundle_record_from_validated_rows(
    *,
    bundle: _storage.PreparedCompletionEvidenceBundle,
    cycle: _storage.CompletionCycle,
    snapshot: sqlite3.Row | dict[str, Any],
    criteria: tuple[dict[str, Any], ...],
    manifest: sqlite3.Row | dict[str, Any],
    entries: tuple[dict[str, Any], ...],
    references: tuple[dict[str, Any], ...],
    verification_receipt: dict[str, Any] | None,
    review_receipts_by_id: dict[str, dict[str, Any]],
    runner_generation: dict[str, Any] | None,
) -> _storage.ProjectionBundleRecord:
    """Assemble one projection record from an already validated Bundle graph."""

    from task_governance_tool.storage import (
        ProjectionBundleRecord,
        evidence_ledger_inconsistent,
    )
    from task_governance_tool.verification_runner_repository import (
        VerificationRunnerObservation,
        VerificationRunnerResolution,
        _verification_runner_value_dict,
    )


    verification_projection: dict[str, Any] | None = None
    if bundle.verification_receipt_id is not None:
        if verification_receipt is None:
            raise evidence_ledger_inconsistent()
        verification_projection = {
            "verification_receipt_id": bundle.verification_receipt_id,
            "verification_subject": {
                "basis_version": 1,
                "kind": "task_verification_criterion",
                "authority_snapshot_id": bundle.authority_snapshot_id,
                "verification_criterion_id": bundle.verification_criterion_id,
            },
            "result": verification_receipt["result"],
            "duration_ms": verification_receipt["duration_ms"],
            "scope_coverage": verification_receipt["scope_coverage"],
            "created_at": verification_receipt["created_at"],
        }
    elif verification_receipt is not None:
        raise evidence_ledger_inconsistent()

    ordered_reviews = tuple(
        review_receipts_by_id[receipt_id]
        for receipt_id in cycle.gate_basis.qualifying_receipt_ids
    )
    runner_observation: dict[str, Any] | None = None
    if bundle.verification_runner_observation_id is not None:
        from task_governance_tool.verification_runner import (
            runner_observation_source_projection,
        )

        if (
            runner_generation is None
            or runner_generation["state"] != "terminal"
            or runner_generation["observation"] is None
            or runner_generation[
                "observation"
            ].verification_runner_observation_id
            != bundle.verification_runner_observation_id
        ):
            raise evidence_ledger_inconsistent()
        runner_observation = runner_observation_source_projection(
            observation=_verification_runner_value_dict(
                runner_generation["observation"],
                VerificationRunnerObservation,
            ),
            resolution=_verification_runner_value_dict(
                runner_generation["resolution"],
                VerificationRunnerResolution,
            ),
        )

    return ProjectionBundleRecord(
        bundle=bundle,
        cycle=cycle,
        task={
            "task_id": bundle.task_id,
            "title": snapshot["task_title"],
            "description": snapshot["task_description"],
            "review_tier": snapshot["review_tier"],
            "verification": snapshot["verification"],
        },
        authority_snapshot=dict(snapshot),
        criteria=criteria,
        artifact_manifest=dict(manifest),
        artifact_entries=entries,
        evidence_references=references,
        verification_receipt=verification_projection,
        runner_observation=runner_observation,
        review_receipts=ordered_reviews,
        finding_snapshots=bundle.finding_snapshots,
    )


def _capture_evidence_projection_basis_rows(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    _validated_runner_generations: (
        dict[tuple[str, str, int], dict[str, Any]] | None
    ) = None,
) -> _storage.EvidenceProjectionBasis:
    """Capture raw cycle/Bundle rows after repository validation."""

    from task_governance_tool.evidence_projection_metadata_repository import (
        read_evidence_projection_state,
    )
    from task_governance_tool.review_repository import (
        _iter_validated_review_receipts_with_provenance,
    )
    from task_governance_tool.storage import (
        EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT,
        EvidenceProjectionBasis,
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
        PreparedCompletionBundleMember,
        PreparedCompletionEvidenceBundle,
        PreparedCompletionFindingSnapshot,
        PreparedCriterionEvidenceLink,
        _cycle_from_row,
        _fetch_bounded_projection_rows,
        current_schema_version,
        evidence_ledger_inconsistent,
    )
    from task_governance_tool.verification_receipt_repository import (
        _validate_verification_receipt_row,
    )


    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    source_schema_version = current_schema_version(connection)
    if source_schema_version not in {
        19,
        PRIVATE_SCHEMA20_VERSION,
        PRIVATE_SCHEMA21_VERSION,
        PRIVATE_SCHEMA22_VERSION,
    }:
        raise evidence_ledger_inconsistent()
    state = read_evidence_projection_state(
        connection,
        project_id=project_id,
    )
    cycle_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT * FROM task_completion_cycles
             WHERE project_id = ?
             ORDER BY task_id COLLATE BINARY, saved_cycle_ordinal,
                      completion_cycle_id COLLATE BINARY
            """,
            (project_id,),
        ),
        maximum=EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT,
    )
    cycles = tuple(_cycle_from_row(row) for row in cycle_rows)
    bundle_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT * FROM completion_evidence_bundles
             WHERE project_id = ?
             ORDER BY task_id COLLATE BINARY, cycle_ordinal,
                      completion_cycle_id COLLATE BINARY
            """,
            (project_id,),
        ),
        maximum=EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT,
    )
    member_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT member.*
              FROM completion_bundle_members AS member
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   member.completion_evidence_bundle_id
             WHERE bundle.project_id = ?
             ORDER BY member.completion_evidence_bundle_id COLLATE BINARY,
                      member.member_kind COLLATE BINARY, member.ordinal
            """,
            (project_id,),
        )
    )
    link_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT member.completion_evidence_bundle_id AS member_bundle_id,
                   link.*
              FROM completion_bundle_members AS member
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   member.completion_evidence_bundle_id
              JOIN criterion_evidence_links AS link
                ON link.criterion_evidence_link_id =
                   member.criterion_evidence_link_id
             WHERE bundle.project_id = ?
               AND member.member_kind = 'criterion_link'
             ORDER BY member.completion_evidence_bundle_id COLLATE BINARY,
                      member.ordinal
            """,
            (project_id,),
        )
    )
    finding_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT finding.*
              FROM completion_bundle_finding_snapshots AS finding
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   finding.completion_evidence_bundle_id
             WHERE bundle.project_id = ?
             ORDER BY finding.completion_evidence_bundle_id COLLATE BINARY,
                      finding.ordinal
            """,
            (project_id,),
        )
    )
    snapshot_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   snapshot.*
              FROM completion_evidence_bundles AS bundle
              JOIN authority_snapshots AS snapshot
                ON snapshot.authority_snapshot_id =
                   bundle.authority_snapshot_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    criterion_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   criterion.*
              FROM completion_evidence_bundles AS bundle
              JOIN authority_snapshot_criteria AS binding
                ON binding.authority_snapshot_id =
                   bundle.authority_snapshot_id
              JOIN contract_criteria AS criterion
                ON criterion.criterion_id = binding.criterion_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY,
                      CASE criterion.criterion_kind
                        WHEN 'acceptance' THEN 0 ELSE 1 END,
                      criterion.criterion_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    manifest_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   manifest.*
              FROM completion_evidence_bundles AS bundle
              JOIN artifact_manifests AS manifest
                ON manifest.artifact_manifest_id =
                   bundle.artifact_manifest_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    entry_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   entry.*
              FROM completion_evidence_bundles AS bundle
              JOIN artifact_manifest_entries AS entry
                ON entry.artifact_manifest_id = bundle.artifact_manifest_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY,
                      entry.ordinal
            """,
            (project_id,),
        )
    )
    reference_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT member.completion_evidence_bundle_id AS member_bundle_id,
                   member.ordinal AS member_ordinal, reference.*
              FROM completion_bundle_members AS member
              JOIN completion_evidence_bundles AS bundle
                ON bundle.completion_evidence_bundle_id =
                   member.completion_evidence_bundle_id
              JOIN evidence_references AS reference
                ON reference.evidence_reference_id =
                   member.evidence_reference_id
             WHERE bundle.project_id = ?
               AND member.member_kind = 'evidence_reference'
             ORDER BY member.completion_evidence_bundle_id COLLATE BINARY,
                      member.ordinal
            """,
            (project_id,),
        )
    )
    verification_rows = _fetch_bounded_projection_rows(
        connection.execute(
            """
            SELECT bundle.completion_evidence_bundle_id AS member_bundle_id,
                   receipt.*
              FROM completion_evidence_bundles AS bundle
              JOIN verification_receipts AS receipt
                ON receipt.verification_receipt_id =
                   bundle.verification_receipt_id
             WHERE bundle.project_id = ?
             ORDER BY bundle.completion_evidence_bundle_id COLLATE BINARY
            """,
            (project_id,),
        )
    )
    members_by_bundle: dict[str, list[PreparedCompletionBundleMember]] = {}
    for row in member_rows:
        members_by_bundle.setdefault(
            str(row["completion_evidence_bundle_id"]),
            [],
        ).append(PreparedCompletionBundleMember(**dict(row)))
    links_by_bundle: dict[str, list[PreparedCriterionEvidenceLink]] = {}
    for row in link_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        links_by_bundle.setdefault(bundle_id, []).append(
            PreparedCriterionEvidenceLink(**values)
        )
    findings_by_bundle: dict[
        str,
        list[PreparedCompletionFindingSnapshot],
    ] = {}
    for row in finding_rows:
        findings_by_bundle.setdefault(
            str(row["completion_evidence_bundle_id"]),
            [],
        ).append(PreparedCompletionFindingSnapshot(**dict(row)))
    snapshots_by_bundle_id: dict[str, dict[str, Any]] = {}
    for row in snapshot_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        if bundle_id in snapshots_by_bundle_id:
            raise evidence_ledger_inconsistent()
        snapshots_by_bundle_id[bundle_id] = values
    criteria_by_bundle: dict[str, list[dict[str, Any]]] = {}
    for row in criterion_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        criteria_by_bundle.setdefault(bundle_id, []).append(values)
    manifests_by_bundle: dict[str, dict[str, Any]] = {}
    for row in manifest_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        if bundle_id in manifests_by_bundle:
            raise evidence_ledger_inconsistent()
        manifests_by_bundle[bundle_id] = values
    entries_by_bundle: dict[str, list[dict[str, Any]]] = {}
    for row in entry_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        entries_by_bundle.setdefault(bundle_id, []).append(values)
    references_by_bundle: dict[str, list[dict[str, Any]]] = {}
    for row in reference_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        values.pop("member_ordinal")
        references_by_bundle.setdefault(bundle_id, []).append(values)
    verification_by_bundle: dict[str, dict[str, Any]] = {}
    for row in verification_rows:
        values = dict(row)
        bundle_id = str(values.pop("member_bundle_id"))
        if bundle_id in verification_by_bundle:
            raise evidence_ledger_inconsistent()
        verification_by_bundle[bundle_id] = _validate_verification_receipt_row(
            values
        )
    bundles = tuple(
        PreparedCompletionEvidenceBundle(
            completion_evidence_bundle_id=str(
                row["completion_evidence_bundle_id"]
            ),
            project_id=str(row["project_id"]),
            task_id=str(row["task_id"]),
            completion_cycle_id=str(row["completion_cycle_id"]),
            cycle_ordinal=row["cycle_ordinal"],
            source_schema_version=row["source_schema_version"],
            bundle_version=row["bundle_version"],
            contract_revision=row["contract_revision"],
            authority_snapshot_id=str(row["authority_snapshot_id"]),
            acceptance_criterion_id=row["acceptance_criterion_id"],
            verification_criterion_id=row["verification_criterion_id"],
            target_kind=str(row["target_kind"]),
            target_value=str(row["target_value"]),
            target_base_revision=str(row["target_base_revision"]),
            target_generation=row["target_generation"],
            target_capture_version=row["target_capture_version"],
            artifact_manifest_id=str(row["artifact_manifest_id"]),
            verification_receipt_id=row["verification_receipt_id"],
            verification_basis_kind=(
                row["verification_basis_kind"]
                if "verification_basis_kind" in row.keys()
                else None
            ),
            verification_runner_observation_id=(
                row["verification_runner_observation_id"]
                if "verification_runner_observation_id" in row.keys()
                else None
            ),
            omission_mask=row["omission_mask"],
            sealed_at=str(row["sealed_at"]),
            bundle_digest=str(row["bundle_digest"]),
            payload_size_bytes=row["payload_size_bytes"],
            criterion_links=tuple(
                links_by_bundle.get(
                    str(row["completion_evidence_bundle_id"]),
                    [],
                )
            ),
            members=tuple(
                members_by_bundle.get(
                    str(row["completion_evidence_bundle_id"]),
                    [],
                )
            ),
            finding_snapshots=tuple(
                findings_by_bundle.get(
                    str(row["completion_evidence_bundle_id"]),
                    [],
                )
            ),
        )
        for row in bundle_rows
    )
    cycles_by_id = {cycle.completion_cycle_id: cycle for cycle in cycles}
    bundle_by_id = {
        bundle.completion_evidence_bundle_id: bundle for bundle in bundles
    }
    selected_review_ids = {
        receipt_id
        for bundle in bundles
        for receipt_id in cycles_by_id[
            bundle.completion_cycle_id
        ].gate_basis.qualifying_receipt_ids
    }
    review_receipts_by_id: dict[str, dict[str, Any]] = {}
    if selected_review_ids:
        for receipt, provenance in _iter_validated_review_receipts_with_provenance(
            connection,
            selected_review_ids,
        ):
            receipt_id = str(receipt["review_receipt_id"])
            review_receipts_by_id[receipt_id] = {
                "receipt": dict(receipt),
                "provenance": provenance,
            }
    native_records: list[ProjectionBundleRecord] = []
    for row in bundle_rows:
        bundle_id = str(row["completion_evidence_bundle_id"])
        bundle = bundle_by_id.get(bundle_id)
        cycle = cycles_by_id.get(str(row["completion_cycle_id"]))
        snapshot = snapshots_by_bundle_id.get(bundle_id)
        manifest = manifests_by_bundle.get(bundle_id)
        references = tuple(references_by_bundle.get(bundle_id, []))
        if bundle is None or cycle is None or snapshot is None or manifest is None:
            raise evidence_ledger_inconsistent()
        native_records.append(
            _projection_bundle_record_from_validated_rows(
                bundle=bundle,
                cycle=cycle,
                snapshot=snapshot,
                criteria=tuple(criteria_by_bundle.get(bundle_id, [])),
                manifest=manifest,
                entries=tuple(entries_by_bundle.get(bundle_id, [])),
                references=references,
                verification_receipt=verification_by_bundle.get(bundle_id),
                review_receipts_by_id=review_receipts_by_id,
                runner_generation=(
                    _validated_runner_generations.get(
                        (
                            bundle.project_id,
                            bundle.task_id,
                            bundle.target_generation,
                        )
                    )
                    if _validated_runner_generations is not None
                    else None
                ),
            )
        )
    return EvidenceProjectionBasis(
        source_schema_version=source_schema_version,
        project_id=project_id,
        source_generation=state.source_generation,
        cycles=cycles,
        bundles=bundles,
        native_bundles=tuple(native_records),
    )


def capture_evidence_projection_basis(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> _storage.EvidenceProjectionBasis:
    """Validate and capture one coherent Evidence projection basis once."""

    from task_governance_tool.storage import (
        PRIVATE_SCHEMA22_VERSION,
        SCHEMA_VERSION,
        _validate_evidence_ledger_schema_contract,
        current_schema_version,
        evidence_ledger_inconsistent,
        validate_completion_cycle_storage,
    )


    if type(project_id) is not str or not project_id:
        raise evidence_ledger_inconsistent()
    if current_schema_version(connection) not in {
        SCHEMA_VERSION, PRIVATE_SCHEMA22_VERSION,
    }:
        raise evidence_ledger_inconsistent()
    _validate_evidence_ledger_schema_contract(connection)
    _validate_evidence_ledger_rows(connection)
    validate_completion_cycle_storage(connection)
    bases = _validated_completion_evidence_projection_bases(connection)
    basis = bases.get(project_id)
    if basis is None:
        raise evidence_ledger_inconsistent()
    return basis


# Shared value types remain storage-owned. Bind the module after definitions
# so either repository import order also resolves runtime annotations.
from task_governance_tool import storage as _storage
