"""Prepared completion Bundle validation and immutable row persistence.

Callers supply the active writer and retain the savepoint, native cycle seal,
Task/event updates, and generation atomicity. Shared stored-Bundle acquisition
and projection validation stay with their existing storage owner. The focused
completion Bundle storage tests cover this persistence boundary.
"""

from __future__ import annotations

import sqlite3


def _validate_prepared_completion_bundle(
    bundle: _storage.PreparedCompletionEvidenceBundle,
    *,
    expected_cycle: _storage.CompletionCycle,
) -> None:
    from task_governance_tool.storage import (
        ARTIFACT_MANIFEST_ID_PATTERN,
        COMPLETION_BUNDLE_MEMBER_KINDS,
        COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN,
        COMPLETION_EVIDENCE_BUNDLE_MAX_BYTES,
        EVIDENCE_REFERENCE_ID_PATTERN,
        PreparedCompletionBundleMember,
        PreparedCompletionEvidenceBundle,
        PreparedCompletionFindingSnapshot,
        PreparedCriterionEvidenceLink,
        REVIEW_FINDING_ID_PATTERN,
        REVIEW_RECEIPT_ID_PATTERN,
        SHA256_DIGEST_PATTERN,
        StorageError,
        _completion_bundle_version_basis_valid,
        evidence_ledger_inconsistent,
        validate_utc_timestamp,
    )

    if (
        not isinstance(bundle, PreparedCompletionEvidenceBundle)
        or COMPLETION_EVIDENCE_BUNDLE_ID_PATTERN.fullmatch(
            bundle.completion_evidence_bundle_id
        )
        is None
        or bundle.completion_evidence_bundle_id
        != expected_cycle.completion_evidence_bundle_id
        or bundle.project_id != expected_cycle.project_id
        or bundle.task_id != expected_cycle.task_id
        or bundle.completion_cycle_id != expected_cycle.completion_cycle_id
        or bundle.cycle_ordinal != expected_cycle.saved_cycle_ordinal
        or not _completion_bundle_version_basis_valid(
            source_schema_version=bundle.source_schema_version,
            bundle_version=bundle.bundle_version,
            verification_basis_kind=bundle.verification_basis_kind,
            verification_runner_observation_id=(
                bundle.verification_runner_observation_id
            ),
            verification_receipt_id=bundle.verification_receipt_id,
            cycle=expected_cycle,
        )
        or bundle.contract_revision != expected_cycle.contract_revision
        or type(bundle.contract_revision) is not int
        or bundle.contract_revision < 0
        or type(bundle.authority_snapshot_id) is not str
        or not bundle.authority_snapshot_id
        or (
            bundle.acceptance_criterion_id is not None
            and type(bundle.acceptance_criterion_id) is not str
        )
        or (
            bundle.verification_criterion_id is not None
            and type(bundle.verification_criterion_id) is not str
        )
        or (
            bundle.target_kind,
            bundle.target_value,
            bundle.target_base_revision,
            bundle.target_generation,
        )
        != (
            expected_cycle.review_target_kind,
            expected_cycle.review_target_value,
            expected_cycle.review_target_base_revision,
            expected_cycle.review_target_generation,
        )
        or bundle.target_capture_version != 1
        or type(bundle.target_capture_version) is not int
        or type(bundle.artifact_manifest_id) is not str
        or ARTIFACT_MANIFEST_ID_PATTERN.fullmatch(bundle.artifact_manifest_id)
        is None
        or bundle.verification_receipt_id
        != expected_cycle.verification_receipt_id
        or type(bundle.omission_mask) is not int
        or not 0 <= bundle.omission_mask <= 15
        or type(bundle.sealed_at) is not str
        or bundle.sealed_at != expected_cycle.recorded_at
        or type(bundle.bundle_digest) is not str
        or SHA256_DIGEST_PATTERN.fullmatch(bundle.bundle_digest) is None
        or type(bundle.payload_size_bytes) is not int
        or not 1
        <= bundle.payload_size_bytes
        <= COMPLETION_EVIDENCE_BUNDLE_MAX_BYTES
    ):
        raise evidence_ledger_inconsistent()
    try:
        validate_utc_timestamp(
            bundle.sealed_at,
            field="completion Evidence Bundle seal time",
        )
    except StorageError as exc:
        raise evidence_ledger_inconsistent() from exc
    link_ids: set[str] = set()
    for link in bundle.criterion_links:
        if (
            not isinstance(link, PreparedCriterionEvidenceLink)
            or link.project_id != bundle.project_id
            or link.task_id != bundle.task_id
            or link.criterion_evidence_link_id in link_ids
        ):
            raise evidence_ledger_inconsistent()
        link_ids.add(link.criterion_evidence_link_id)
    member_keys: set[tuple[str, int]] = set()
    member_link_ids: set[str] = set()
    member_reference_ids: set[str] = set()
    next_ordinals = {kind: 0 for kind in COMPLETION_BUNDLE_MEMBER_KINDS}
    for member in bundle.members:
        if (
            not isinstance(member, PreparedCompletionBundleMember)
            or member.project_id != bundle.project_id
            or member.task_id != bundle.task_id
            or member.completion_evidence_bundle_id
            != bundle.completion_evidence_bundle_id
            or member.member_kind not in COMPLETION_BUNDLE_MEMBER_KINDS
            or type(member.ordinal) is not int
            or member.ordinal != next_ordinals[member.member_kind]
            or (member.member_kind, member.ordinal) in member_keys
        ):
            raise evidence_ledger_inconsistent()
        next_ordinals[member.member_kind] += 1
        member_keys.add((member.member_kind, member.ordinal))
        if member.member_kind == "criterion_link":
            if (
                type(member.criterion_evidence_link_id) is not str
                or member.criterion_evidence_link_id not in link_ids
                or member.evidence_reference_id is not None
                or member.criterion_evidence_link_id in member_link_ids
            ):
                raise evidence_ledger_inconsistent()
            member_link_ids.add(member.criterion_evidence_link_id)
        elif (
            member.criterion_evidence_link_id is not None
            or type(member.evidence_reference_id) is not str
            or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(
                member.evidence_reference_id
            )
            is None
            or member.evidence_reference_id in member_reference_ids
        ):
            raise evidence_ledger_inconsistent()
        else:
            member_reference_ids.add(member.evidence_reference_id)
    if member_link_ids != link_ids or any(
        link.evidence_reference_id not in member_reference_ids
        for link in bundle.criterion_links
    ):
        raise evidence_ledger_inconsistent()
    finding_ids: set[str] = set()
    for ordinal, finding in enumerate(bundle.finding_snapshots):
        if (
            not isinstance(finding, PreparedCompletionFindingSnapshot)
            or finding.project_id != bundle.project_id
            or finding.task_id != bundle.task_id
            or finding.completion_evidence_bundle_id
            != bundle.completion_evidence_bundle_id
            or type(finding.ordinal) is not int
            or finding.ordinal != ordinal
            or type(finding.review_finding_id) is not str
            or REVIEW_FINDING_ID_PATTERN.fullmatch(finding.review_finding_id)
            is None
            or type(finding.review_receipt_id) is not str
            or REVIEW_RECEIPT_ID_PATTERN.fullmatch(finding.review_receipt_id)
            is None
            or finding.review_finding_id in finding_ids
            or type(finding.target_generation) is not int
            or finding.target_generation <= 0
            or finding.severity not in {"high", "medium", "low"}
            or type(finding.summary) is not str
            or not 1 <= len(finding.summary) <= 1_000
            or finding.status not in {"open", "resolved"}
            or type(finding.resolution_summary) is not str
            or len(finding.resolution_summary) > 1_000
            or type(finding.created_at) is not str
            or (
                finding.status == "open"
                and (
                    finding.resolution_summary != ""
                    or finding.resolved_at is not None
                )
            )
            or (
                finding.status == "resolved"
                and (
                    not finding.resolution_summary
                    or type(finding.resolved_at) is not str
                )
            )
            or type(finding.producer_version) is not int
            or finding.producer_version != 1
            or type(finding.digest) is not str
            or SHA256_DIGEST_PATTERN.fullmatch(finding.digest) is None
            or (
                finding.evidence_reference_id is None
                and (
                    finding.assurance_class != "legacy_unknown"
                    or finding.producer_class != "legacy_migration"
                )
            )
            or (
                finding.evidence_reference_id is not None
                and (
                    type(finding.evidence_reference_id) is not str
                    or EVIDENCE_REFERENCE_ID_PATTERN.fullmatch(
                        finding.evidence_reference_id
                    )
                    is None
                    or finding.evidence_reference_id not in member_reference_ids
                    or finding.assurance_class != "bound_attestation"
                    or finding.producer_class != "trusted_caller"
                )
            )
        ):
            raise evidence_ledger_inconsistent()
        try:
            validate_utc_timestamp(
                finding.created_at,
                field="completion Finding snapshot creation time",
            )
            if finding.resolved_at is not None:
                validate_utc_timestamp(
                    finding.resolved_at,
                    field="completion Finding snapshot resolution time",
                )
        except StorageError as exc:
            raise evidence_ledger_inconsistent() from exc
        finding_ids.add(finding.review_finding_id)


def persist_completion_evidence_bundle_locked(
    connection: sqlite3.Connection,
    *,
    bundle: _storage.PreparedCompletionEvidenceBundle,
    expected_cycle: _storage.CompletionCycle,
) -> _storage.PreparedCompletionEvidenceBundle:
    """Persist caller-prepared relational Bundle rows before its deferred cycle."""

    from task_governance_tool.evidence_repository import (
        persist_criterion_evidence_link_locked,
    )
    from task_governance_tool.evidence_validation_repository import (
        read_completion_evidence_bundle,
    )
    from task_governance_tool.storage import (
        _EVIDENCE_LEDGER_REQUIRED_COLUMNS,
        _prepared_row,
        _require_evidence_writer,
        current_schema_version,
        evidence_ledger_inconsistent,
    )

    _require_evidence_writer(connection)
    if bundle.source_schema_version != current_schema_version(connection):
        raise evidence_ledger_inconsistent()
    _validate_prepared_completion_bundle(bundle, expected_cycle=expected_cycle)
    try:
        for link in bundle.criterion_links:
            persist_criterion_evidence_link_locked(connection, link=link)
        available_bundle_fields = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(completion_evidence_bundles)"
            ).fetchall()
        }
        bundle_fields = {
            field
            for field in _EVIDENCE_LEDGER_REQUIRED_COLUMNS[
                "completion_evidence_bundles"
            ]
            if field in available_bundle_fields
        }
        bundle_values = _prepared_row(bundle, bundle_fields)
        ordered_bundle_fields = tuple(sorted(bundle_fields))
        connection.execute(
            f"INSERT INTO completion_evidence_bundles("
            f"{', '.join(ordered_bundle_fields)}) VALUES ("
            f"{', '.join(':' + name for name in ordered_bundle_fields)})",
            bundle_values,
        )
        member_fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS[
            "completion_bundle_members"
        ]
        ordered_member_fields = tuple(sorted(member_fields))
        for member in bundle.members:
            connection.execute(
                f"INSERT INTO completion_bundle_members("
                f"{', '.join(ordered_member_fields)}) VALUES ("
                f"{', '.join(':' + name for name in ordered_member_fields)})",
                _prepared_row(member, member_fields),
            )
        finding_fields = _EVIDENCE_LEDGER_REQUIRED_COLUMNS[
            "completion_bundle_finding_snapshots"
        ]
        ordered_finding_fields = tuple(sorted(finding_fields))
        for finding in bundle.finding_snapshots:
            connection.execute(
                f"INSERT INTO completion_bundle_finding_snapshots("
                f"{', '.join(ordered_finding_fields)}) VALUES ("
                f"{', '.join(':' + name for name in ordered_finding_fields)})",
                _prepared_row(finding, finding_fields),
            )
    except sqlite3.IntegrityError as exc:
        raise evidence_ledger_inconsistent() from exc
    stored = read_completion_evidence_bundle(
        connection,
        completion_evidence_bundle_id=bundle.completion_evidence_bundle_id,
    )
    if stored != bundle:
        raise evidence_ledger_inconsistent()
    return stored


# Shared value types remain storage-owned. Bind the module after definitions
# so either repository import order also resolves runtime annotations.
from task_governance_tool import storage as _storage
