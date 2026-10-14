"""Closed continuation values and effective judgment selection; no I/O or SQL.

Repositories validate original assertions/References and pass the complete
relevant graph here. Time, displayed prose and caller aliases never select a
successor. Pending decisions are separate from old immutable judgments.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from .evidence_ledger import canonical_json_bytes, domain_digest
from .session_identity import is_session_id
from .task_values import TaskValidationError, validate_text, validation_error


CONTINUATION_LIMIT = 64
REVIEWER_LIMIT = 8
BASIS_LIMIT = 16_384
INPUT_LIMIT = 262_144
BASIS_DOMAIN = b"taskgov-review-continuation-basis-v1\0"
SELECTION_DOMAIN = b"taskgov-review-continuation-selection-v1\0"
QUALITY_FIELDS = (
    "project_id", "task_id", "contract_revision", "authority_snapshot_id",
    "acceptance_criterion_id", "verification_criterion_id", "artifact_manifest_id",
    "review_tier", "verification_requirement", "verification_not_required_reason",
    "target_kind", "target_value",
    "target_base_revision", "target_generation",
)


@dataclass(frozen=True)
class RetainedOriginal:
    prior_continuation_id: str
    session_id: str
    original_result_digest: str
    metadata_digest: str

    def __post_init__(self):
        if (not identifier(self.prior_continuation_id, "tg_review_continuation_")
            or not is_session_id(self.session_id) or not _digest(self.original_result_digest)
            or not _digest(self.metadata_digest)):
            raise _invalid()

    def as_dict(self):
        return dict(prior_continuation_id=self.prior_continuation_id, session_id=self.session_id,
            original_result_digest=self.original_result_digest, metadata_digest=self.metadata_digest)


@dataclass(frozen=True)
class RetirementProof:
    continuation_id: str
    basis_digest: str
    request_identity_digest: str | None
    worker_stop_identity: str | None

    def __post_init__(self):
        if (not identifier(self.continuation_id, "tg_review_continuation_") or not _digest(self.basis_digest)
            or self.request_identity_digest is not None and not _digest(self.request_identity_digest)
            or self.worker_stop_identity is not None and not _digest(self.worker_stop_identity)):
            raise _invalid()

    def digest(self):
        return domain_digest(b"taskgov-review-continuation-retirement-v1\0", dict(
            continuation_id=self.continuation_id, basis_digest=self.basis_digest,
            request_identity_digest=self.request_identity_digest, worker_stop_identity=self.worker_stop_identity)).removeprefix("sha256:")
BASIS_FIELDS = frozenset((*QUALITY_FIELDS, "completion_commit_required", "version", "project_path_hash",
    "project_binding_generation", "parent_session_id", "execution_id", "ownership_generation"))


def identifier(value: object, prefix: str) -> bool:
    return type(value) is str and re.fullmatch(re.escape(prefix) + r"[0-9a-f]{16}", value) is not None


def _invalid():
    return validation_error("invalid_review_evidence", "review continuation input is invalid")


def _inconsistent():
    return validation_error("evidence_ledger_inconsistent", "review continuation evidence is inconsistent")


def _integer(value: object, low: int, high: int = (1 << 63) - 1) -> bool:
    return type(value) is int and low <= value <= high


def _digest(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_basis(value: object) -> dict:
    """Return a detached closed basis, retaining actual nullable criteria."""
    if type(value) is not dict or set(value) != BASIS_FIELDS or type(value["version"]) is not int or value["version"] != 1:
        raise _invalid()
    value = dict(value)
    for name, prefix in (("task_id", "tg_task_"), ("authority_snapshot_id", "tg_authority_snapshot_"),
                         ("artifact_manifest_id", "tg_artifact_manifest_"), ("execution_id", "tg_execution_")):
        if not identifier(value[name], prefix):
            raise _invalid()
    for name in ("acceptance_criterion_id", "verification_criterion_id"):
        if value[name] is not None and not identifier(value[name], "tg_contract_criterion_"):
            raise _invalid()
    for name, low in (("contract_revision", 0), ("target_generation", 1),
                      ("project_binding_generation", 1), ("ownership_generation", 1)):
        if not _integer(value[name], low):
            raise _invalid()
    if not (_integer(value["review_tier"], 0, 2) and _integer(value["completion_commit_required"], 0, 1)
            and is_session_id(value["parent_session_id"]) and _digest(value["project_path_hash"])):
        raise _invalid()
    for name, limit, required in (("project_id", 500, True), ("target_value", 500, True),
                                   ("target_base_revision", 500, False),
                                   ("verification_not_required_reason", 1000, False)):
        if type(value[name]) is not str:
            raise _invalid()
        validate_text(name, value[name], required=required, limit=limit)
        try:
            value[name].encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise _invalid() from exc
    if value["target_kind"] not in ("git_commit", "git_snapshot", "diff_fingerprint", "external_revision"):
        raise _invalid()
    if (value["target_kind"] == "git_snapshot") != bool(value["target_base_revision"]):
        raise _invalid()
    requirement = value["verification_requirement"]
    if (requirement not in ("required", "not_required", "unspecified")
        or (requirement == "not_required") != bool(value["verification_not_required_reason"])):
        raise _invalid()
    try:
        if len(canonical_json_bytes(value)) > BASIS_LIMIT:
            raise _invalid()
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
    return value


def basis_digest(value: dict) -> str:
    return domain_digest(BASIS_DOMAIN, validate_basis(value)).removeprefix("sha256:")


def quality_identity(value: Mapping) -> tuple:
    return tuple(value[name] for name in QUALITY_FIELDS)


@dataclass(frozen=True)
class SelectedReviewer:
    session_id: str
    action: str
    prior_receipt_id: str | None
    reason: str

    def __post_init__(self):
        if (not is_session_id(self.session_id) or self.action not in ("supplement", "reassess")
            or (self.action == "supplement" and self.prior_receipt_id is not None)
            or (self.action == "reassess" and not identifier(self.prior_receipt_id, "tg_review_receipt_"))):
            raise _invalid()
        object.__setattr__(self, "reason", validate_text(
            "review_continuation_reason", self.reason, required=True, limit=1000))
        try:
            self.reason.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise _invalid() from exc

    def as_dict(self) -> dict:
        return dict(session_id=self.session_id, action=self.action,
                    prior_receipt_id=self.prior_receipt_id, reason=self.reason)


def validate_selection(value: object) -> tuple[SelectedReviewer, ...]:
    if (type(value) is not dict or set(value) != {"version", "reviewers"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["reviewers"]) is not list or not 1 <= len(value["reviewers"]) <= REVIEWER_LIMIT):
        raise _invalid()
    result = []
    for row in value["reviewers"]:
        if type(row) is not dict or set(row) != {"session_id", "action", "prior_receipt_id", "reason"}:
            raise _invalid()
        result.append(SelectedReviewer(**row))
    if len({row.session_id for row in result}) != len(result):
        raise _invalid()
    return tuple(sorted(result, key=lambda row: row.session_id.encode("utf-8")))


def decode_selection(raw: bytes) -> tuple[SelectedReviewer, ...]:
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def invalid_number(value):
        raise ValueError("noninteger number")

    if type(raw) is not bytes or len(raw) > INPUT_LIMIT:
        raise _invalid()
    try:
        return validate_selection(json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
            parse_float=invalid_number, parse_constant=invalid_number))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc


def selection_digest(selected: tuple[SelectedReviewer, ...]) -> str:
    checked = validate_selection({"version": 1, "reviewers": [row.as_dict() for row in selected]})
    return domain_digest(SELECTION_DOMAIN, [row.as_dict() for row in checked]).removeprefix("sha256:")


@dataclass(frozen=True)
class Judgment:
    receipt_id: str
    reviewer_key: str
    session_id: str | None
    revision: int
    prior_receipt_id: str | None
    continuation_id: str | None
    verdict: str
    quality_basis: tuple

    def __post_init__(self):
        if (not identifier(self.receipt_id, "tg_review_receipt_") or not _integer(self.revision, 1)
            or self.verdict not in ("pass", "changes_requested", "not_required")
            or (self.session_id is not None and not is_session_id(self.session_id))
            or type(self.quality_basis) is not tuple or len(self.quality_basis) != len(QUALITY_FIELDS)
            or (self.continuation_id is not None and not identifier(self.continuation_id, "tg_review_continuation_"))
            or (self.revision == 1 and self.prior_receipt_id is not None)
            or (self.revision > 1 and (not identifier(self.prior_receipt_id, "tg_review_receipt_")
                                      or self.continuation_id is None or self.session_id is None))):
            raise _inconsistent()
        try:
            validate_text("reviewer_key", self.reviewer_key, required=True, limit=500)
            # Validate only quality data. These fixed structural values do not
            # establish ownership; repository admission validates real owners.
            validate_basis({**dict(zip(QUALITY_FIELDS, self.quality_basis)), "version": 1,
                "project_path_hash": "0" * 64, "project_binding_generation": 1,
                "parent_session_id": "00000000-0000-4000-8000-000000000001",
                "execution_id": "tg_execution_0000000000000000", "ownership_generation": 1,
                "completion_commit_required": 1})
        except TaskValidationError as exc:
            raise _inconsistent() from exc


@dataclass(frozen=True)
class EffectiveJudgments:
    heads: tuple[Judgment, ...]
    suspended_receipt_ids: tuple[str, ...]
    pending_sessions: tuple[str, ...]

    @property
    def active(self) -> tuple[Judgment, ...]:
        return tuple(row for row in self.heads if row.receipt_id not in self.suspended_receipt_ids)


def select_effective(judgments: tuple[Judgment, ...], *, quality_basis: tuple,
                     pending: tuple[SelectedReviewer, ...] = ()) -> EffectiveJudgments:
    """Validate every supplied node, then select heads for the exact quality basis.

    No bounds on an audit projection may limit this graph. Native assertion,
    continuation admission and typed Finding validation remain repository work.
    """
    by_id, successors, revisions, actors = {}, {}, set(), {}
    for row in judgments:
        key = (*row.quality_basis[:2], row.quality_basis[-1], row.reviewer_key, row.revision)
        if row.receipt_id in by_id or key in revisions:
            raise _inconsistent()
        revisions.add(key)
        by_id[row.receipt_id] = row
        if row.session_id is not None:
            actor_key = (row.quality_basis, row.session_id)
            if actors.setdefault(actor_key, row.reviewer_key) != row.reviewer_key:
                raise _inconsistent()
    for row in judgments:
        if row.prior_receipt_id is None:
            continue
        prior = by_id.get(row.prior_receipt_id)
        if (prior is None or prior.revision + 1 != row.revision
            or prior.quality_basis != row.quality_basis or prior.reviewer_key != row.reviewer_key
            or prior.session_id is None or prior.session_id != row.session_id
            or prior.receipt_id in successors):
            raise _inconsistent()
        successors[prior.receipt_id] = row.receipt_id
    heads = tuple(sorted((row for row in judgments if row.receipt_id not in successors
                          and row.quality_basis == quality_basis),
                         key=lambda row: (row.reviewer_key.encode("utf-8"), row.receipt_id.encode("utf-8"))))
    selected = validate_selection({"version": 1, "reviewers": [row.as_dict() for row in pending]}) if pending else ()
    suspended = []
    for choice in selected:
        matching = [row for row in heads if row.session_id == choice.session_id]
        if choice.action == "supplement":
            if matching:
                raise _inconsistent()
        elif len(matching) != 1 or matching[0].receipt_id != choice.prior_receipt_id:
            raise _inconsistent()
        else:
            suspended.append(matching[0].receipt_id)
    return EffectiveJudgments(heads, tuple(sorted(suspended)), tuple(row.session_id for row in selected))
