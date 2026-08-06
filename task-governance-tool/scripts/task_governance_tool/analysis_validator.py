"""Pure validation and exact report derivation for local analysis jobs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, NoReturn

from task_governance_tool import analysis_renderer
from task_governance_tool.analysis_contracts import (
    LEGACY_PACKET_MAX_BYTES,
    NATIVE_PACKET_MAX_BYTES,
    PRODUCER_VERSION,
    PROMPT_SCHEMA_VERSION,
    RENDERER_VERSION,
    REPORT_SCHEMA_VERSION,
    AnalysisContractError,
    canonical_json_bytes,
    canonical_json_document_bytes,
    domain_hash,
    parse_canonical_json_document,
    sealed_domain_digest,
    validate_descriptor,
    validate_source_basis,
)
from task_governance_tool.analysis_packet import AnalysisPacket, FIXED_PROMPT_DIGEST
from task_governance_tool.analysis_renderer import (
    AnalysisRendererError,
    REPORT_ENVELOPE_KEYS,
    REPORT_PAYLOAD_KEYS,
)


ADAPTER_OUTPUT_MAX_BYTES = 65_536
REPORT_JSON_MAX_BYTES = 16_777_216

FACT_MAX_COUNT = 16_384
DECLARATION_MAX_COUNT = 16_384
CITATION_MAX_COUNT = 65_536
OCCURRENCE_MAX_COUNT = 65_536
OMISSION_MAX_COUNT = 4_096
UNCERTAINTY_MAX_COUNT = 4_096
CLAIM_MAX_COUNT = 2_048
CLAIM_TEXT_MAX_BYTES = 1_000

FACT_KINDS = frozenset(
    {
        "bundle",
        "task",
        "contract",
        "target",
        "authority_snapshot",
        "criterion",
        "criterion_link",
        "artifact_manifest",
        "artifact_entry",
        "evidence_reference",
        "verification_receipt",
        "review_receipt",
        "review_provenance",
        "finding_snapshot",
        "completion_evidence",
        "omission",
    }
)
DECLARATION_KINDS = frozenset(
    {
        "reviewer_class",
        "model_state",
        "declared_model_id",
        "skill_state",
        "declared_skill_id",
        "declared_skill_version",
        "profile",
        "lens",
        "context_relation",
        "method",
    }
)
DERIVED_UNCERTAINTIES = frozenset(
    {"none", "insufficient_basis", "conflicting_basis", "legacy_absence"}
)
REPORT_INFERENCE_STATES = frozenset(
    {
        "disabled",
        "policy_blocked",
        "succeeded",
        "input_too_large",
        "unavailable",
        "launch_failed",
        "timeout",
        "output_too_large",
        "invalid_output",
        "failed",
    }
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PRIVACY_PATTERNS = (
    re.compile(r"Authorization\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(
        r"Authorization\s+(?:Basic|Bearer|Token|ApiKey)(?:\s*[:=])?\s+\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:Set-)?Cookie\s*:", re.IGNORECASE),
    re.compile(
        r"\b[A-Z0-9_.-]*(?:Password|Passwd|Pwd|Token|Secret|Cookie|Credential|Credentials|Api[-_]?Key|ApiKey|Access[-_]?Key|AccessKey|Private[-_]?Key|PrivateKey)[A-Z0-9_.-]*\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"'][A-Z0-9_.-]*(?:Password|Passwd|Pwd|Token|Secret|Cookie|Credential|Credentials|Api[-_]?Key|ApiKey|Access[-_]?Key|AccessKey|Private[-_]?Key|PrivateKey|Authorization|dispatch_authorization)[A-Z0-9_.-]*[\"']\s*:\s*(?:[\"'][^\"']+[\"']|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:api|access|secret|private|client\s+secret)\s+key\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![^\s`])dispatch_authorization\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/]{8,}={0,2}(?=$|[\s,.;:)])", re.IGNORECASE),
    re.compile(r"\b(?:Basic|Bearer)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"-----(?:BEGIN|END)\s+", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"(?im)^\s*(?:private|system|developer)\s+prompt\s*[:=-]\s*\S+"),
    re.compile(
        r"(?im)^\s*(?:private\s+reasoning|chain[- ]of[- ]thought)\s*[:=-]\s*\S+"
    ),
    re.compile(r"(?im)^\s*(?:raw\s+)?review\s+transcript\s*[:=-]\s*\S+"),
    re.compile(r"(?im)^\s*(?:raw\s+)?(?:stdout|stderr)(?:\s+dump)?\s*\n\s*\S+"),
    re.compile(r"(?im)^\s*(?:log\s+output|raw\s+log)\s*[:=-]?\s*\n\s*\S+"),
    re.compile(
        r"(?im)^\s*(?:environment(?:\s+(?:variables|dump))?|env(?:\s+(?:dump|vars))?)\s*[:=-]?\s*\n\s*[A-Z_][A-Z0-9_]*\s*[:=]"
    ),
    re.compile(r"(?m)^\s*[A-Z_][A-Z0-9_]*=.*\n\s*[A-Z_][A-Z0-9_]*="),
    re.compile(r"(?m)^\s*diff --git "),
    re.compile(r"(?m)^\s*@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@"),
    re.compile(r"(?m)^\s*---\s+\S+.*\n\s*\+\+\+\s+\S+.*\n\s*@@\s"),
    re.compile(r"(?m)^\s*at\s+(?:async\s+)?(?:[\w.$<>]+\s+)?\(?[^()\s]+:\d+:\d+\)?"),
    re.compile(r"(?m)^Exception in thread\s+"),
    re.compile(r"(?m)^\s*Caused by:\s+\S+"),
    re.compile(r"(?m)^panic:\s+"),
    re.compile(r"(?m)^goroutine\s+\d+\s+\[running\]:"),
)

_PACKET_KEYS = (
    "packet_version",
    "analysis_job_id",
    "source_kind",
    "source_basis",
    "source",
)
_BUNDLE_KEYS = ("bundle_digest", "format_version", "payload")
_PAYLOAD_KEYS = (
    "artifact_manifest",
    "authority_snapshot",
    "bundle_id",
    "bundle_version",
    "completion_cycle_id",
    "cycle_ordinal",
    "sealed_at",
    "completion_evidence",
    "contract",
    "criteria",
    "criterion_links",
    "evidence_references",
    "finding_snapshots",
    "omissions",
    "project_id",
    "review_receipts",
    "source_schema_version",
    "target",
    "task",
    "verification_receipt",
)
_BUNDLE_METADATA_KEYS = (
    "bundle_id",
    "bundle_version",
    "completion_cycle_id",
    "cycle_ordinal",
    "sealed_at",
    "project_id",
    "source_schema_version",
)
_ARTIFACT_KEYS = (
    "artifact_manifest_id",
    "state",
    "object_format",
    "comparison_base",
    "digest",
    "omission_code",
    "entries",
)
_ARTIFACT_ENTRY_KEYS = (
    "ordinal",
    "kind",
    "old_path",
    "new_path",
    "before_mode",
    "before_object_id",
    "after_mode",
    "after_object_id",
)
_AUTHORITY_KEYS = ("authority_snapshot_id", "generation", "digest")
_COMPLETION_KEYS = (
    "kind",
    "revision",
    "reason",
    "external_revision_approved",
    "completion_commit_required",
    "completion_commit_hash",
)
_CONTRACT_KEYS = (
    "revision",
    "specified",
    "scope",
    "acceptance",
    "constraints",
    "authority_ref",
)
_CRITERION_KEYS = ("criterion_id", "kind", "text", "digest")
_LINK_KEYS = (
    "criterion_evidence_link_id",
    "criterion_id",
    "evidence_reference_id",
    "relation",
    "assurance_class",
    "producer_class",
    "producer_version",
)
_REFERENCE_KEYS = (
    "evidence_reference_id",
    "source_kind",
    "source_state",
    "source_id",
    "assurance_class",
    "producer_class",
    "producer_version",
    "contract_revision",
    "authority_snapshot_id",
    "acceptance_criterion_id",
    "verification_criterion_id",
    "target_kind",
    "target_value",
    "target_base_revision",
    "target_generation",
    "completion_cycle_id",
    "digest",
)
_FINDING_KEYS = (
    "review_finding_id",
    "review_receipt_id",
    "target_generation",
    "severity",
    "summary",
    "status",
    "resolution_summary",
    "created_at",
    "resolved_at",
    "evidence_reference_id",
    "assurance_class",
    "producer_class",
    "producer_version",
    "digest",
)
_REVIEW_KEYS = (
    "review_receipt_id",
    "reviewer_key",
    "receipt_kind",
    "verdict",
    "summary",
    "user_approved",
    "created_at",
    "review_provenance",
)
_PROVENANCE_KEYS = (
    "review_provenance_id",
    "provenance_version",
    "reviewer_class",
    "model_state",
    "declared_model_id",
    "skill_state",
    "declared_skill_id",
    "declared_skill_version",
    "review_profiles",
    "review_lenses",
    "context_relation",
    "method_codes",
    "assurance_class",
    "producer_class",
    "producer_version",
    "digest",
)
_PROVENANCE_FACT_FIELDS = (
    "review_provenance_id",
    "provenance_version",
    "assurance_class",
    "producer_class",
    "producer_version",
    "digest",
)
_PROVENANCE_DECLARATIONS = (
    ("reviewer_class", "reviewer_class"),
    ("model_state", "model_state"),
    ("declared_model_id", "declared_model_id"),
    ("skill_state", "skill_state"),
    ("declared_skill_id", "declared_skill_id"),
    ("declared_skill_version", "declared_skill_version"),
    ("context_relation", "context_relation"),
)
_PROVENANCE_CODES = (
    ("review_profiles", "profile"),
    ("review_lenses", "lens"),
    ("method_codes", "method"),
)
_TARGET_KEYS = ("kind", "value", "base_revision", "generation", "capture_version")
_TASK_KEYS = ("task_id", "title", "description", "review_tier", "verification")
_VERIFICATION_KEYS = (
    "verification_receipt_id",
    "verification_subject",
    "result",
    "duration_ms",
    "scope_coverage",
    "created_at",
)
_SUBJECT_KEYS = (
    "basis_version",
    "kind",
    "authority_snapshot_id",
    "verification_criterion_id",
)
_OUTPUT_KEYS = (
    "output_schema_version",
    "analysis_job_id",
    "source_key",
    "recipe_digest",
    "claims",
)
_CLAIM_KEYS = ("text", "source_refs", "uncertainty")
_SOURCE_REF_KEYS = ("kind", "json_pointer")
_REPRODUCIBILITY_KEYS = (
    "producer_version",
    "declared_model_id",
    "prompt_schema_version",
    "prompt_digest",
    "input_digest",
    "accepted_output_digest",
    "report_schema_version",
    "renderer_version",
)


@dataclass(frozen=True)
class AnalysisValidationError(ValueError):
    """One fixed rejection at the analysis validation boundary."""

    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ValidatedAdapterOutput:
    value: dict[str, Any]
    document: bytes
    accepted_output_digest: str


@dataclass(frozen=True)
class ValidatedAnalysisReport:
    envelope: dict[str, Any]
    report_document: bytes
    markdown_bytes: bytes
    report_id: str
    report_digest: str
    render_digest: str


@dataclass
class _SourceProjection:
    structural_facts: list[dict[str, Any]]
    declarations: list[dict[str, Any]]
    source_omissions: list[dict[str, Any]]
    occurrences: list[dict[str, Any]]
    citations: dict[str, dict[str, Any]]
    pointer_citations: dict[str, str]
    legacy_absence: dict[str, Any] | None
    runtime_citation: dict[str, Any]


def _failure(code: str) -> NoReturn:
    messages = {
        "output_too_large": "analysis output exceeds the supported size",
        "invalid_output": "analysis output is invalid",
        "report_invalid": "analysis report is invalid",
    }
    raise AnalysisValidationError(code, messages[code])


def _mapping(value: object, keys: tuple[str, ...], *, code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        _failure(code)
    return dict(value)


def _array(value: object, *, code: str) -> list[Any]:
    if type(value) is not list:
        _failure(code)
    return list(value)


def _text(value: object, *, code: str, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or "\0" in value:
        _failure(code)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise AnalysisValidationError(code, "analysis data is invalid") from exc
    return value


def _digest(value: object, *, code: str, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _failure(code)
    return value


def _pointer(base: str, token: str | int) -> str:
    escaped = str(token).replace("~", "~0").replace("/", "~1")
    return f"{base}/{escaped}" if base else f"/{escaped}"


def _element_bytes(value: object) -> bytes:
    try:
        return canonical_json_bytes(value)
    except AnalysisContractError as exc:
        raise AnalysisValidationError("report_invalid", "analysis report is invalid") from exc


def _sorted_unique(
    values: list[dict[str, Any]],
    *,
    maximum: int,
) -> list[dict[str, Any]]:
    if len(values) > maximum:
        _failure("report_invalid")
    keyed = [(_element_bytes(value), value) for value in values]
    keyed.sort(key=lambda item: item[0])
    if any(left[0] == right[0] for left, right in zip(keyed, keyed[1:])):
        _failure("report_invalid")
    return [value for _, value in keyed]


def _deduplicated_sorted(
    values: list[dict[str, Any]],
    *,
    maximum: int,
) -> list[dict[str, Any]]:
    by_bytes: dict[bytes, dict[str, Any]] = {}
    for value in values:
        encoded = _element_bytes(value)
        by_bytes.setdefault(encoded, value)
    if len(by_bytes) > maximum:
        _failure("report_invalid")
    return [by_bytes[key] for key in sorted(by_bytes)]


def _citation_id(value_without_id: dict[str, Any]) -> str:
    return "tg_analysis_citation_" + domain_hash(
        "taskgov-analysis-citation-v1",
        canonical_json_bytes(value_without_id),
    ).hex()[:16]


def _checked_citation_insert(
    citations: dict[str, dict[str, Any]], citation: dict[str, Any]
) -> str:
    citation_id = citation["citation_id"]
    existing = citations.get(citation_id)
    if existing is not None and existing != citation:
        _failure("report_invalid")
    citations[citation_id] = citation
    return citation_id


def _native_citation(
    *,
    source_key: str,
    bundle_id: str,
    bundle_digest: str,
    file_digest: str,
    pointer: str,
    kind: str,
    entity_id: str | None,
    entity_digest: str | None,
) -> dict[str, Any]:
    if kind not in FACT_KINDS:
        _failure("report_invalid")
    value = {
        "citation_kind": kind,
        "source_key": source_key,
        "bundle_id": bundle_id,
        "bundle_digest": bundle_digest,
        "file_digest": file_digest,
        "json_pointer": pointer,
        "entity_id": entity_id,
        "entity_digest": entity_digest,
    }
    return {"citation_id": _citation_id(value), **value}


def _legacy_citation(descriptor: dict[str, Any]) -> dict[str, Any]:
    basis = descriptor["source_basis"]
    entry = basis["entry"]
    value = {
        "citation_kind": "legacy_index_entry",
        "source_key": descriptor["source_key"],
        "project_id": basis["project_id"],
        "projection_generation": basis["projection_generation"],
        "index_digest": basis["index_digest"],
        "task_id": entry["task_id"],
        "completion_cycle_id": entry["completion_cycle_id"],
        "cycle_ordinal": entry["cycle_ordinal"],
    }
    return {"citation_id": _citation_id(value), **value}


def _validated_descriptor_packet(
    descriptor: object,
    packet: object,
    *,
    code: str,
) -> tuple[dict[str, Any], AnalysisPacket, dict[str, Any]]:
    try:
        bound = validate_descriptor(descriptor)
    except AnalysisContractError as exc:
        raise AnalysisValidationError(code, "analysis data is invalid") from exc
    if not isinstance(packet, AnalysisPacket):
        _failure(code)
    recipe = bound["recipe"]
    if (
        recipe["producer_version"] != PRODUCER_VERSION
        or recipe["report_schema_version"] != REPORT_SCHEMA_VERSION
        or recipe["renderer_version"] != RENDERER_VERSION
        or recipe["prompt_schema_version"] != PROMPT_SCHEMA_VERSION
    ):
        _failure(code)
    value = _mapping(packet.value, _PACKET_KEYS, code=code)
    try:
        packet_bytes = canonical_json_bytes(value)
        expected_digest = sealed_domain_digest(
            "taskgov-analysis-packet-v1",
            packet_bytes,
        )
        basis = validate_source_basis(
            value["source_basis"],
            source_kind=value["source_kind"],
        )
    except AnalysisContractError as exc:
        raise AnalysisValidationError(code, "analysis data is invalid") from exc
    maximum = (
        NATIVE_PACKET_MAX_BYTES
        if value["source_kind"] == "native_bundle"
        else LEGACY_PACKET_MAX_BYTES
    )
    if (
        value["packet_version"] != 1
        or value["analysis_job_id"] != bound["analysis_job_id"]
        or value["source_kind"] != bound["source_kind"]
        or basis != bound["source_basis"]
        or packet.packet_bytes != packet_bytes
        or packet.packet_digest != expected_digest
        or len(packet_bytes) > maximum
    ):
        _failure(code)
    source = value["source"]
    if value["source_kind"] == "legacy_index_entry":
        if source is not None:
            _failure(code)
        return bound, packet, value

    envelope = _mapping(source, _BUNDLE_KEYS, code=code)
    payload = _mapping(envelope["payload"], _PAYLOAD_KEYS, code=code)
    entry = basis["entry"]
    try:
        document = canonical_json_document_bytes(envelope)
        expected_file_digest = "sha256:" + hashlib.sha256(document).hexdigest()
        expected_bundle_digest = sealed_domain_digest(
            "taskgov-completion-evidence-bundle-v1",
            canonical_json_bytes(payload),
        )
    except AnalysisContractError as exc:
        raise AnalysisValidationError(code, "analysis data is invalid") from exc
    if (
        envelope["format_version"] != 1
        or envelope["bundle_digest"] != expected_bundle_digest
        or envelope["bundle_digest"] != entry["bundle_digest"]
        or expected_file_digest != entry["file_digest"]
        or payload["bundle_id"] != entry["bundle_id"]
        or payload["completion_cycle_id"] != entry["completion_cycle_id"]
        or payload["cycle_ordinal"] != entry["cycle_ordinal"]
        or payload["sealed_at"] != entry["sealed_at"]
        or payload["project_id"] != basis["project_id"]
    ):
        _failure(code)
    return bound, packet, value


class _NativeProjector:
    def __init__(self, descriptor: dict[str, Any], source: dict[str, Any]) -> None:
        self.descriptor = descriptor
        self.source = source
        self.payload = source["payload"]
        self.entry = descriptor["source_basis"]["entry"]
        self.bundle_id = _text(self.payload["bundle_id"], code="report_invalid")
        self.bundle_digest = _digest(source["bundle_digest"], code="report_invalid")
        self.file_digest = _digest(self.entry["file_digest"], code="report_invalid")
        self.facts: list[dict[str, Any]] = []
        self.declarations: list[dict[str, Any]] = []
        self.omissions: list[dict[str, Any]] = []
        self.occurrences: list[dict[str, Any]] = []
        self.citations: dict[str, dict[str, Any]] = {}
        self.pointer_citations: dict[str, str] = {}
        self.reference_rows: list[dict[str, Any]] = []
        self.reference_citations: dict[tuple[str, str], list[str]] = {}

    def citation(
        self,
        pointer: str,
        kind: str,
        entity_id: str | None,
        entity_digest: str | None,
        *,
        primary: bool = True,
    ) -> str:
        citation = _native_citation(
            source_key=self.descriptor["source_key"],
            bundle_id=self.bundle_id,
            bundle_digest=self.bundle_digest,
            file_digest=self.file_digest,
            pointer=pointer,
            kind=kind,
            entity_id=entity_id,
            entity_digest=entity_digest,
        )
        citation_id = _checked_citation_insert(self.citations, citation)
        if primary:
            previous = self.pointer_citations.get(pointer)
            if previous is not None and previous != citation_id:
                _failure("report_invalid")
            self.pointer_citations[pointer] = citation_id
        return citation_id

    @staticmethod
    def citation_ids(direct: str, extra: tuple[str, ...] = ()) -> list[str]:
        values = sorted({direct, *extra})
        if not 1 <= len(values) <= 8:
            _failure("report_invalid")
        return values

    def fact(
        self,
        kind: str,
        value: Any,
        direct: str,
        extra: tuple[str, ...] = (),
    ) -> None:
        if kind not in FACT_KINDS:
            _failure("report_invalid")
        if type(value) is list and value != []:
            _failure("report_invalid")
        self.facts.append(
            {
                "fact_kind": kind,
                "value": value,
                "citation_ids": self.citation_ids(direct, extra),
            }
        )

    def declaration(self, kind: str, value: Any, citation_id: str) -> None:
        if kind not in DECLARATION_KINDS:
            _failure("report_invalid")
        self.declarations.append(
            {
                "declaration_kind": kind,
                "value": value,
                "citation_ids": [citation_id],
            }
        )

    def leaf_facts(
        self,
        value: Any,
        *,
        path: str,
        fact_kind: str,
        citation_kind: str,
        entity_id: str | None,
        entity_digest: str | None,
        extra: tuple[str, ...] = (),
    ) -> None:
        if type(value) is dict:
            if not value:
                _failure("report_invalid")
            for key in sorted(value):
                self.leaf_facts(
                    value[key],
                    path=_pointer(path, key),
                    fact_kind=fact_kind,
                    citation_kind=citation_kind,
                    entity_id=entity_id,
                    entity_digest=entity_digest,
                    extra=extra,
                )
            return
        if type(value) is list and value:
            for index, item in enumerate(value):
                self.leaf_facts(
                    item,
                    path=_pointer(path, index),
                    fact_kind=fact_kind,
                    citation_kind=citation_kind,
                    entity_id=entity_id,
                    entity_digest=entity_digest,
                    extra=extra,
                )
            return
        try:
            canonical_json_bytes(value)
        except AnalysisContractError as exc:
            raise AnalysisValidationError(
                "report_invalid", "analysis report is invalid"
            ) from exc
        empty = type(value) is list
        direct = self.citation(
            path,
            "bundle" if empty else citation_kind,
            None if empty else entity_id,
            None if empty else entity_digest,
        )
        self.fact(fact_kind, value, direct, () if empty else extra)

    def atomic_rows(
        self,
        value: object,
        *,
        path: str,
        fact_kind: str,
        keys: tuple[str, ...],
        entity: Any,
    ) -> list[dict[str, Any]]:
        rows = _array(value, code="report_invalid")
        if not rows:
            direct = self.citation(path, "bundle", None, None)
            self.fact(fact_kind, [], direct)
            return []
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(rows):
            row = _mapping(raw, keys, code="report_invalid")
            entity_id, entity_digest = entity(row, index)
            direct = self.citation(
                _pointer(path, index),
                fact_kind,
                entity_id,
                entity_digest,
            )
            self.fact(fact_kind, row, direct)
            normalized.append(row)
        return normalized

    def prepare_references(self) -> None:
        raw_rows = _array(self.payload["evidence_references"], code="report_invalid")
        if not raw_rows:
            self.reference_rows = []
            return
        seen_ids: set[str] = set()
        for index, raw in enumerate(raw_rows):
            row = _mapping(raw, _REFERENCE_KEYS, code="report_invalid")
            reference_id = _text(row["evidence_reference_id"], code="report_invalid")
            reference_digest = _digest(row["digest"], code="report_invalid")
            source_kind = _text(row["source_kind"], code="report_invalid")
            source_id = _text(row["source_id"], code="report_invalid")
            if reference_id in seen_ids:
                _failure("report_invalid")
            seen_ids.add(reference_id)
            citation_id = self.citation(
                _pointer("/payload/evidence_references", index),
                "evidence_reference",
                reference_id,
                reference_digest,
            )
            self.reference_citations.setdefault((source_kind, source_id), []).append(
                citation_id
            )
            self.reference_rows.append(row)

    def reference_for(self, source_kind: str, source_id: str) -> str:
        matches = self.reference_citations.get((source_kind, source_id), [])
        if len(matches) != 1:
            _failure("report_invalid")
        return matches[0]

    def validate_reference_cardinality(
        self,
        reviews: list[dict[str, Any]],
        verification: dict[str, Any] | None,
    ) -> None:
        expected = {
            ("completion_evidence", _text(self.payload["completion_cycle_id"], code="report_invalid"))
        }
        expected.update(
            ("review_receipt", _text(row["review_receipt_id"], code="report_invalid"))
            for row in reviews
        )
        if verification is not None:
            expected.add(
                (
                    "verification_receipt",
                    _text(
                        verification["verification_receipt_id"],
                        code="report_invalid",
                    ),
                )
            )
        observed = {
            key
            for key in self.reference_citations
            if key[0]
            in {"completion_evidence", "review_receipt", "verification_receipt"}
        }
        if observed != expected or any(
            len(self.reference_citations[key]) != 1 for key in expected
        ):
            _failure("report_invalid")

    def project(self) -> _SourceProjection:
        payload = self.payload
        self.prepare_references()
        reviews = [
            _mapping(row, _REVIEW_KEYS, code="report_invalid")
            for row in _array(payload["review_receipts"], code="report_invalid")
        ]
        verification_value = payload["verification_receipt"]
        verification = (
            None
            if verification_value is None
            else _mapping(verification_value, _VERIFICATION_KEYS, code="report_invalid")
        )
        self.validate_reference_cardinality(reviews, verification)

        self.leaf_facts(
            self.source["format_version"],
            path="/format_version",
            fact_kind="bundle",
            citation_kind="bundle",
            entity_id=None,
            entity_digest=None,
        )
        self.leaf_facts(
            self.source["bundle_digest"],
            path="/bundle_digest",
            fact_kind="bundle",
            citation_kind="bundle",
            entity_id=None,
            entity_digest=None,
        )
        for key in _BUNDLE_METADATA_KEYS:
            self.leaf_facts(
                payload[key],
                path=_pointer("/payload", key),
                fact_kind="bundle",
                citation_kind="bundle",
                entity_id=None,
                entity_digest=None,
            )

        task = _mapping(payload["task"], _TASK_KEYS, code="report_invalid")
        task_id = _text(task["task_id"], code="report_invalid")
        self.leaf_facts(
            task,
            path="/payload/task",
            fact_kind="task",
            citation_kind="task",
            entity_id=task_id,
            entity_digest=None,
        )
        contract = _mapping(payload["contract"], _CONTRACT_KEYS, code="report_invalid")
        self.leaf_facts(
            contract,
            path="/payload/contract",
            fact_kind="contract",
            citation_kind="contract",
            entity_id=None,
            entity_digest=None,
        )
        target = _mapping(payload["target"], _TARGET_KEYS, code="report_invalid")
        self.leaf_facts(
            target,
            path="/payload/target",
            fact_kind="target",
            citation_kind="target",
            entity_id=None,
            entity_digest=None,
        )
        authority = _mapping(
            payload["authority_snapshot"], _AUTHORITY_KEYS, code="report_invalid"
        )
        authority_id = _text(authority["authority_snapshot_id"], code="report_invalid")
        authority_digest = _digest(authority["digest"], code="report_invalid")
        self.leaf_facts(
            authority,
            path="/payload/authority_snapshot",
            fact_kind="authority_snapshot",
            citation_kind="authority_snapshot",
            entity_id=authority_id,
            entity_digest=authority_digest,
        )

        manifest = _mapping(
            payload["artifact_manifest"], _ARTIFACT_KEYS, code="report_invalid"
        )
        manifest_id = _text(manifest["artifact_manifest_id"], code="report_invalid")
        manifest_digest = _digest(manifest["digest"], code="report_invalid")
        for key in _ARTIFACT_KEYS[:-1]:
            self.leaf_facts(
                manifest[key],
                path=_pointer("/payload/artifact_manifest", key),
                fact_kind="artifact_manifest",
                citation_kind="artifact_manifest",
                entity_id=manifest_id,
                entity_digest=manifest_digest,
            )
        self.atomic_rows(
            manifest["entries"],
            path="/payload/artifact_manifest/entries",
            fact_kind="artifact_entry",
            keys=_ARTIFACT_ENTRY_KEYS,
            entity=lambda _row, _index: (manifest_id, manifest_digest),
        )
        self.atomic_rows(
            payload["criteria"],
            path="/payload/criteria",
            fact_kind="criterion",
            keys=_CRITERION_KEYS,
            entity=lambda row, _index: (
                _text(row["criterion_id"], code="report_invalid"),
                _digest(row["digest"], code="report_invalid"),
            ),
        )
        self.atomic_rows(
            payload["criterion_links"],
            path="/payload/criterion_links",
            fact_kind="criterion_link",
            keys=_LINK_KEYS,
            entity=lambda row, _index: (
                _text(row["criterion_evidence_link_id"], code="report_invalid"),
                None,
            ),
        )
        if not self.reference_rows:
            direct = self.citation(
                "/payload/evidence_references", "bundle", None, None
            )
            self.fact("evidence_reference", [], direct)
        else:
            for index, row in enumerate(self.reference_rows):
                direct = self.pointer_citations[
                    _pointer("/payload/evidence_references", index)
                ]
                self.fact("evidence_reference", row, direct)
        self.atomic_rows(
            payload["finding_snapshots"],
            path="/payload/finding_snapshots",
            fact_kind="finding_snapshot",
            keys=_FINDING_KEYS,
            entity=lambda row, _index: (
                _text(row["review_finding_id"], code="report_invalid"),
                _digest(row["digest"], code="report_invalid"),
            ),
        )

        completion = _mapping(
            payload["completion_evidence"], _COMPLETION_KEYS, code="report_invalid"
        )
        cycle_id = _text(payload["completion_cycle_id"], code="report_invalid")
        completion_reference = self.reference_for("completion_evidence", cycle_id)
        self.leaf_facts(
            completion,
            path="/payload/completion_evidence",
            fact_kind="completion_evidence",
            citation_kind="completion_evidence",
            entity_id=cycle_id,
            entity_digest=None,
            extra=(completion_reference,),
        )

        if not reviews:
            direct = self.citation("/payload/review_receipts", "bundle", None, None)
            self.fact("review_receipt", [], direct)
        seen_receipts: set[str] = set()
        for index, receipt in enumerate(reviews):
            receipt_id = _text(receipt["review_receipt_id"], code="report_invalid")
            if receipt_id in seen_receipts:
                _failure("report_invalid")
            seen_receipts.add(receipt_id)
            reference_id = self.reference_for("review_receipt", receipt_id)
            receipt_path = _pointer("/payload/review_receipts", index)
            for key in _REVIEW_KEYS[:-1]:
                self.leaf_facts(
                    receipt[key],
                    path=_pointer(receipt_path, key),
                    fact_kind="review_receipt",
                    citation_kind="review_receipt",
                    entity_id=receipt_id,
                    entity_digest=None,
                    extra=(reference_id,),
                )
            provenance = receipt["review_provenance"]
            provenance_path = _pointer(receipt_path, "review_provenance")
            if provenance is None:
                if receipt["receipt_kind"] != "not_required":
                    _failure("report_invalid")
                direct = self.citation(
                    provenance_path,
                    "review_provenance",
                    receipt_id,
                    None,
                )
                self.fact("review_provenance", None, direct, (reference_id,))
                continue
            if receipt["receipt_kind"] == "not_required":
                _failure("report_invalid")
            projected = _mapping(provenance, _PROVENANCE_KEYS, code="report_invalid")
            provenance_id = _text(
                projected["review_provenance_id"], code="report_invalid"
            )
            provenance_digest = _digest(projected["digest"], code="report_invalid")
            if (
                projected["provenance_version"] != 1
                or projected["assurance_class"] != "bound_attestation"
                or projected["producer_class"] != "trusted_caller"
                or projected["producer_version"] != 1
            ):
                _failure("report_invalid")
            for key in _PROVENANCE_FACT_FIELDS:
                direct = self.citation(
                    _pointer(provenance_path, key),
                    "review_provenance",
                    provenance_id,
                    provenance_digest,
                )
                self.fact("review_provenance", projected[key], direct)
            for key, declaration_kind in _PROVENANCE_DECLARATIONS:
                direct = self.citation(
                    _pointer(provenance_path, key),
                    "review_provenance",
                    provenance_id,
                    provenance_digest,
                )
                self.declaration(declaration_kind, projected[key], direct)
            for key, code_kind in _PROVENANCE_CODES:
                codes = _array(projected[key], code="report_invalid")
                if any(type(code) is not str for code in codes) or len(codes) != len(
                    set(codes)
                ):
                    _failure("report_invalid")
                collection_path = _pointer(provenance_path, key)
                if not codes:
                    direct = self.citation(collection_path, "bundle", None, None)
                    self.fact("review_provenance", [], direct)
                    continue
                for code_index, code_value in enumerate(codes):
                    direct = self.citation(
                        _pointer(collection_path, code_index),
                        "review_provenance",
                        provenance_id,
                        provenance_digest,
                    )
                    self.declaration(code_kind, code_value, direct)
                    self.occurrences.append(
                        {
                            "kind": code_kind,
                            "code": code_value,
                            "bundle_id": self.bundle_id,
                            "review_receipt_id": receipt_id,
                            "review_provenance_id": provenance_id,
                            "citation_ids": [direct],
                        }
                    )

        if verification is None:
            direct = self.citation(
                "/payload/verification_receipt", "bundle", None, None
            )
            self.fact("verification_receipt", None, direct)
        else:
            subject = _mapping(
                verification["verification_subject"],
                _SUBJECT_KEYS,
                code="report_invalid",
            )
            verification["verification_subject"] = subject
            verification_id = _text(
                verification["verification_receipt_id"], code="report_invalid"
            )
            verification_reference = self.reference_for(
                "verification_receipt", verification_id
            )
            self.leaf_facts(
                verification,
                path="/payload/verification_receipt",
                fact_kind="verification_receipt",
                citation_kind="verification_receipt",
                entity_id=verification_id,
                entity_digest=None,
                extra=(verification_reference,),
            )

        source_omissions = _array(payload["omissions"], code="report_invalid")
        if not source_omissions:
            direct = self.citation("/payload/omissions", "bundle", None, None)
            self.fact("omission", [], direct)
        else:
            if any(type(code) is not str for code in source_omissions) or len(
                set(source_omissions)
            ) != len(source_omissions):
                _failure("report_invalid")
            for index, omission_code in enumerate(source_omissions):
                direct = self.citation(
                    _pointer("/payload/omissions", index),
                    "omission",
                    None,
                    None,
                )
                self.omissions.append(
                    {"code": omission_code, "citation_ids": [direct]}
                )

        runtime_citation = _native_citation(
            source_key=self.descriptor["source_key"],
            bundle_id=self.bundle_id,
            bundle_digest=self.bundle_digest,
            file_digest=self.file_digest,
            pointer="",
            kind="bundle",
            entity_id=None,
            entity_digest=None,
        )
        return _SourceProjection(
            structural_facts=_sorted_unique(self.facts, maximum=FACT_MAX_COUNT),
            declarations=_sorted_unique(
                self.declarations, maximum=DECLARATION_MAX_COUNT
            ),
            source_omissions=_sorted_unique(
                self.omissions, maximum=OMISSION_MAX_COUNT
            ),
            occurrences=_sorted_unique(
                self.occurrences, maximum=OCCURRENCE_MAX_COUNT
            ),
            citations=self.citations,
            pointer_citations=self.pointer_citations,
            legacy_absence=None,
            runtime_citation=runtime_citation,
        )


def _derive_source_projection(
    descriptor: dict[str, Any], packet_value: dict[str, Any]
) -> _SourceProjection:
    if descriptor["source_kind"] == "native_bundle":
        return _NativeProjector(descriptor, packet_value["source"]).project()
    citation = _legacy_citation(descriptor)
    citation_id = citation["citation_id"]
    return _SourceProjection(
        structural_facts=[],
        declarations=[],
        source_omissions=[],
        occurrences=[],
        citations={citation_id: citation},
        pointer_citations={},
        legacy_absence={
            "state": "legacy_unknown",
            "receipt_detail": "unavailable",
            "provenance_detail": "unavailable",
            "citation_id": citation_id,
        },
        runtime_citation=citation,
    )


def _privacy_guard(text: str) -> None:
    """Apply the isolated deny-by-default analyzer free-text guard."""

    if any(pattern.search(text) is not None for pattern in _PRIVACY_PATTERNS):
        _failure("invalid_output")
    bearer = re.search(
        r"\bBearer\s+([A-Za-z0-9._~+/=-]{3,})(?=$|[\s,.;:)])",
        text,
        re.IGNORECASE,
    )
    if bearer is not None:
        token = bearer.group(1).rstrip(",.;)")
        lowered = token.lower()
        if (
            lowered in {"secret", "abc123"}
            or "secret" in lowered
            or lowered.startswith(("sk-", "xox", "ghp_", "gho_", "pat_"))
            or (any(not character.isalpha() for character in token) and len(token) >= 5)
            or len(token) >= 20
        ):
            _failure("invalid_output")


def _source_ref_sort_key(value: dict[str, Any]) -> tuple[int, int, bytes]:
    kind = value["kind"]
    pointer = value["json_pointer"]
    kind_order = {"legacy_basis": 0, "native_pointer": 1}
    if kind not in kind_order:
        _failure("invalid_output")
    return (
        kind_order[kind],
        0 if pointer is None else 1,
        b"" if pointer is None else pointer.encode("utf-8"),
    )


def validate_adapter_output(
    document: bytes,
    *,
    descriptor: object,
    packet: object,
) -> ValidatedAdapterOutput:
    """Validate one strict canonical, LF-free optional-adapter output."""

    if type(document) is not bytes:
        _failure("invalid_output")
    if len(document) > ADAPTER_OUTPUT_MAX_BYTES:
        _failure("output_too_large")
    try:
        output = _mapping(
            parse_canonical_json_document(
                document + b"\n",
                maximum=ADAPTER_OUTPUT_MAX_BYTES + 1,
            ),
            _OUTPUT_KEYS,
            code="invalid_output",
        )
        bound, _, packet_value = _validated_descriptor_packet(
            descriptor,
            packet,
            code="invalid_output",
        )
        projection = _derive_source_projection(bound, packet_value)
    except AnalysisValidationError:
        raise
    except (AnalysisContractError, TypeError, ValueError) as exc:
        raise AnalysisValidationError(
            "invalid_output", "analysis output is invalid"
        ) from exc
    if (
        output["output_schema_version"] != 1
        or output["analysis_job_id"] != bound["analysis_job_id"]
        or output["source_key"] != bound["source_key"]
        or output["recipe_digest"] != bound["recipe_digest"]
        or bound["recipe"]["inference_mode"] != "codex_optional"
    ):
        _failure("invalid_output")
    claims = _array(output["claims"], code="invalid_output")
    if len(claims) > CLAIM_MAX_COUNT:
        _failure("invalid_output")
    normalized: list[dict[str, Any]] = []
    for raw_claim in claims:
        claim = _mapping(raw_claim, _CLAIM_KEYS, code="invalid_output")
        text = _text(claim["text"], code="invalid_output")
        if not text or len(text.encode("utf-8")) > CLAIM_TEXT_MAX_BYTES:
            _failure("invalid_output")
        _privacy_guard(text)
        uncertainty = claim["uncertainty"]
        if uncertainty not in DERIVED_UNCERTAINTIES:
            _failure("invalid_output")
        refs = [
            _mapping(item, _SOURCE_REF_KEYS, code="invalid_output")
            for item in _array(claim["source_refs"], code="invalid_output")
        ]
        if not 1 <= len(refs) <= 8:
            _failure("invalid_output")
        encoded_refs = [canonical_json_bytes(item) for item in refs]
        if len(set(encoded_refs)) != len(refs) or refs != sorted(
            refs, key=_source_ref_sort_key
        ):
            _failure("invalid_output")
        if bound["source_kind"] == "native_bundle":
            if uncertainty == "legacy_absence":
                _failure("invalid_output")
            for ref in refs:
                pointer = ref["json_pointer"]
                if (
                    ref["kind"] != "native_pointer"
                    or type(pointer) is not str
                    or not pointer
                    or not pointer.startswith("/")
                    or pointer not in projection.pointer_citations
                ):
                    _failure("invalid_output")
        elif refs != [{"kind": "legacy_basis", "json_pointer": None}]:
            _failure("invalid_output")
        normalized.append(
            {"text": text, "source_refs": refs, "uncertainty": uncertainty}
        )
    encoded_claims = [canonical_json_bytes(item) for item in normalized]
    if len(set(encoded_claims)) != len(normalized) or normalized != sorted(
        normalized, key=canonical_json_bytes
    ):
        _failure("invalid_output")
    output["claims"] = normalized
    if canonical_json_bytes(output) != document:
        _failure("invalid_output")
    return ValidatedAdapterOutput(
        value=output,
        document=document,
        accepted_output_digest=sealed_domain_digest(
            "taskgov-analysis-output-v1",
            document,
        ),
    )


def _validated_optional_output(
    value: object,
    *,
    descriptor: dict[str, Any],
    packet: AnalysisPacket,
) -> ValidatedAdapterOutput:
    if not isinstance(value, ValidatedAdapterOutput):
        _failure("report_invalid")
    try:
        validated = validate_adapter_output(
            value.document,
            descriptor=descriptor,
            packet=packet,
        )
    except AnalysisValidationError as exc:
        raise AnalysisValidationError(
            "report_invalid", "analysis report is invalid"
        ) from exc
    if validated != value:
        _failure("report_invalid")
    return validated


def _report_id(
    *,
    source_key: str,
    recipe_digest: str,
    inference_state: str,
    accepted_output_digest: str | None,
) -> str:
    body = (
        source_key.encode("ascii")
        + b"\0"
        + recipe_digest.encode("ascii")
        + b"\0"
        + inference_state.encode("ascii")
        + b"\0"
        + (accepted_output_digest or "offline-null").encode("ascii")
    )
    return "tg_analysis_report_" + domain_hash(
        "taskgov-analysis-report-id-v1", body
    ).hex()[:16]


def _claim_citation_ids(
    claim: dict[str, Any],
    *,
    descriptor: dict[str, Any],
    projection: _SourceProjection,
) -> list[str]:
    if descriptor["source_kind"] == "legacy_index_entry":
        return [projection.runtime_citation["citation_id"]]
    citation_ids = sorted(
        {
            projection.pointer_citations[reference["json_pointer"]]
            for reference in claim["source_refs"]
        }
    )
    if not 1 <= len(citation_ids) <= 8:
        _failure("report_invalid")
    return citation_ids


def _candidate_parts(
    *,
    descriptor: dict[str, Any],
    packet: AnalysisPacket,
    projection: _SourceProjection,
    inference_state: str,
    prompt_digest: str | None,
    accepted_output_digest: str | None,
    claims: list[dict[str, Any]],
    claim_capacity_exceeded: bool,
    render_capacity_exceeded: bool,
) -> tuple[dict[str, Any], bytes]:
    citations = dict(projection.citations)
    derived: list[dict[str, Any]] = []
    uncertainties: list[dict[str, Any]] = []
    if projection.legacy_absence is not None:
        uncertainties.append(
            {
                "code": "legacy_absence",
                "citation_ids": [projection.runtime_citation["citation_id"]],
            }
        )
    for claim in claims:
        citation_ids = _claim_citation_ids(
            claim,
            descriptor=descriptor,
            projection=projection,
        )
        derived.append(
            {
                "tag": "llm_derived/batch_analyzer/1",
                "non_authoritative": True,
                "text": claim["text"],
                "citation_ids": citation_ids,
                "uncertainty": claim["uncertainty"],
            }
        )
        if claim["uncertainty"] != "none":
            uncertainties.append(
                {
                    "code": claim["uncertainty"],
                    "citation_ids": citation_ids,
                }
            )

    omissions = list(projection.source_omissions)
    runtime_codes: list[str] = []
    if descriptor["source_kind"] == "legacy_index_entry":
        runtime_codes.append("legacy_detail_unavailable")
    if (
        descriptor["recipe"]["inference_mode"] == "codex_optional"
        and inference_state != "succeeded"
    ):
        runtime_codes.append("inference_unavailable")
    if claim_capacity_exceeded:
        runtime_codes.append("claim_capacity_exceeded")
    if render_capacity_exceeded:
        runtime_codes.append("render_capacity_exceeded")
    if runtime_codes:
        runtime_citation = projection.runtime_citation
        citation_id = runtime_citation["citation_id"]
        _checked_citation_insert(citations, runtime_citation)
        omissions.extend(
            {"code": code, "citation_ids": [citation_id]}
            for code in runtime_codes
        )

    structural_facts = _sorted_unique(
        list(projection.structural_facts), maximum=FACT_MAX_COUNT
    )
    declarations = _sorted_unique(
        list(projection.declarations), maximum=DECLARATION_MAX_COUNT
    )
    occurrences = _sorted_unique(
        list(projection.occurrences), maximum=OCCURRENCE_MAX_COUNT
    )
    derived = _sorted_unique(derived, maximum=CLAIM_MAX_COUNT)
    omissions = _sorted_unique(omissions, maximum=OMISSION_MAX_COUNT)
    uncertainties = _deduplicated_sorted(
        uncertainties, maximum=UNCERTAINTY_MAX_COUNT
    )
    citation_values = _sorted_unique(
        list(citations.values()), maximum=CITATION_MAX_COUNT
    )
    recipe = descriptor["recipe"]
    report_id = _report_id(
        source_key=descriptor["source_key"],
        recipe_digest=descriptor["recipe_digest"],
        inference_state=inference_state,
        accepted_output_digest=accepted_output_digest,
    )
    payload = {
        "report_id": report_id,
        "analysis_job_id": descriptor["analysis_job_id"],
        "source_kind": descriptor["source_kind"],
        "source_key": descriptor["source_key"],
        "recipe_digest": descriptor["recipe_digest"],
        "inference_state": inference_state,
        "structural_facts": structural_facts,
        "trusted_caller_declarations": declarations,
        "legacy_absence": projection.legacy_absence,
        "llm_derived": derived,
        "omissions": omissions,
        "uncertainties": uncertainties,
        "declared_code_occurrences": occurrences,
        "citations": citation_values,
        "reproducibility": {
            "producer_version": recipe["producer_version"],
            "declared_model_id": recipe["declared_model_id"],
            "prompt_schema_version": recipe["prompt_schema_version"],
            "prompt_digest": prompt_digest,
            "input_digest": packet.packet_digest,
            "accepted_output_digest": accepted_output_digest,
            "report_schema_version": recipe["report_schema_version"],
            "renderer_version": recipe["renderer_version"],
        },
    }
    if set(payload["reproducibility"]) != set(_REPRODUCIBILITY_KEYS):
        _failure("report_invalid")
    report_digest = sealed_domain_digest(
        "taskgov-analysis-report-v1",
        canonical_json_bytes(payload),
    )
    envelope = {
        "report_schema_version": recipe["report_schema_version"],
        "report_digest": report_digest,
        "payload": payload,
    }
    return envelope, canonical_json_document_bytes(envelope)


def _report_fits(document: bytes) -> bool:
    return len(document) <= REPORT_JSON_MAX_BYTES


def _render_if_fits(document: bytes) -> bytes | None:
    try:
        return analysis_renderer.render_markdown_v1(document)
    except AnalysisRendererError:
        return None


def build_analysis_report(
    *,
    descriptor: object,
    packet: object,
    inference_state: str,
    adapter_output: ValidatedAdapterOutput | None = None,
    prompt_digest: str | None = None,
) -> ValidatedAnalysisReport:
    """Derive the only report and Markdown bytes valid for one bound outcome."""

    bound, normalized_packet, packet_value = _validated_descriptor_packet(
        descriptor,
        packet,
        code="report_invalid",
    )
    if inference_state not in REPORT_INFERENCE_STATES:
        _failure("report_invalid")
    recipe = bound["recipe"]
    mode = recipe["inference_mode"]
    claims: list[dict[str, Any]] = []
    accepted_output_digest: str | None = None
    if mode == "offline":
        if (
            inference_state != "disabled"
            or adapter_output is not None
            or prompt_digest is not None
            or recipe["declared_model_id"] is not None
        ):
            _failure("report_invalid")
    else:
        _digest(prompt_digest, code="report_invalid")
        if prompt_digest != FIXED_PROMPT_DIGEST or inference_state == "disabled":
            _failure("report_invalid")
        if inference_state == "succeeded":
            validated_output = _validated_optional_output(
                adapter_output,
                descriptor=bound,
                packet=normalized_packet,
            )
            claims = list(validated_output.value["claims"])
            accepted_output_digest = validated_output.accepted_output_digest
        elif adapter_output is not None:
            _failure("report_invalid")

    projection = _derive_source_projection(bound, packet_value)
    total = len(claims)

    def parts(
        count: int,
        *,
        claim_exceeded: bool,
        render_exceeded: bool,
    ) -> tuple[dict[str, Any], bytes]:
        return _candidate_parts(
            descriptor=bound,
            packet=normalized_packet,
            projection=projection,
            inference_state=inference_state,
            prompt_digest=prompt_digest,
            accepted_output_digest=accepted_output_digest,
            claims=claims[:count],
            claim_capacity_exceeded=claim_exceeded,
            render_capacity_exceeded=render_exceeded,
        )

    envelope, document = parts(
        total,
        claim_exceeded=False,
        render_exceeded=False,
    )
    if _report_fits(document):
        report_count = total
    else:
        if total == 0:
            _failure("report_invalid")
        low = 0
        high = total - 1
        report_count = -1
        while low <= high:
            middle = (low + high) // 2
            _, candidate = parts(
                middle,
                claim_exceeded=True,
                render_exceeded=False,
            )
            if _report_fits(candidate):
                report_count = middle
                low = middle + 1
            else:
                high = middle - 1
        if report_count < 0:
            _failure("report_invalid")
        envelope, document = parts(
            report_count,
            claim_exceeded=True,
            render_exceeded=False,
        )

    markdown = _render_if_fits(document)
    if markdown is None:
        if report_count == 0:
            _failure("report_invalid")
        low = 0
        high = report_count - 1
        render_count = -1
        render_result: tuple[dict[str, Any], bytes, bytes] | None = None
        while low <= high:
            middle = (low + high) // 2
            candidate_envelope, candidate_document = parts(
                middle,
                claim_exceeded=report_count < total,
                render_exceeded=True,
            )
            candidate_markdown = (
                _render_if_fits(candidate_document)
                if _report_fits(candidate_document)
                else None
            )
            if candidate_markdown is not None:
                render_count = middle
                render_result = (
                    candidate_envelope,
                    candidate_document,
                    candidate_markdown,
                )
                low = middle + 1
            else:
                high = middle - 1
        if render_count < 0 or render_result is None:
            _failure("report_invalid")
        envelope, document, markdown = render_result

    report_digest = envelope["report_digest"]
    report_id = envelope["payload"]["report_id"]
    render_digest = "sha256:" + hashlib.sha256(markdown).hexdigest()
    return ValidatedAnalysisReport(
        envelope=envelope,
        report_document=document,
        markdown_bytes=markdown,
        report_id=report_id,
        report_digest=report_digest,
        render_digest=render_digest,
    )


def validate_report_document(
    document: bytes,
    *,
    descriptor: object,
    packet: object,
    inference_state: str,
    adapter_output: ValidatedAdapterOutput | None = None,
    prompt_digest: str | None = None,
) -> ValidatedAnalysisReport:
    """Re-derive and byte-compare a report instead of trusting its claims."""

    if type(document) is not bytes or len(document) > REPORT_JSON_MAX_BYTES:
        _failure("report_invalid")
    expected = build_analysis_report(
        descriptor=descriptor,
        packet=packet,
        inference_state=inference_state,
        adapter_output=adapter_output,
        prompt_digest=prompt_digest,
    )
    if document != expected.report_document:
        _failure("report_invalid")
    try:
        parsed = _mapping(
            parse_canonical_json_document(document, maximum=REPORT_JSON_MAX_BYTES),
            REPORT_ENVELOPE_KEYS,
            code="report_invalid",
        )
        _mapping(parsed["payload"], REPORT_PAYLOAD_KEYS, code="report_invalid")
    except AnalysisContractError as exc:
        raise AnalysisValidationError(
            "report_invalid", "analysis report is invalid"
        ) from exc
    return expected


def _validated_report_array(
    value: object,
    *,
    keys: tuple[str, ...],
    maximum: int,
) -> list[dict[str, Any]]:
    rows = [
        _mapping(item, keys, code="report_invalid")
        for item in _array(value, code="report_invalid")
    ]
    if len(rows) > maximum:
        _failure("report_invalid")
    encoded = [canonical_json_bytes(item) for item in rows]
    if encoded != sorted(encoded) or len(encoded) != len(set(encoded)):
        _failure("report_invalid")
    return rows


def _validated_report_citation_ids(
    value: object,
    *,
    allowed: set[str],
) -> list[str]:
    citation_ids = _array(value, code="report_invalid")
    if (
        not 1 <= len(citation_ids) <= 8
        or any(type(item) is not str or item not in allowed for item in citation_ids)
        or citation_ids != sorted(citation_ids)
        or len(citation_ids) != len(set(citation_ids))
    ):
        _failure("report_invalid")
    return citation_ids


def validate_recovery_report_document(
    document: bytes,
    *,
    descriptor: object,
    packet: object,
    inference_state: str,
    accepted_output_digest: str | None,
    expected_prompt_digest: str | None,
    expected_report_id: str,
    expected_report_digest: str,
    expected_render_digest: str,
) -> ValidatedAnalysisReport:
    """Validate an intent-bound report without reopening raw adapter output.

    Recovery can authenticate retained bytes only through the complete intent.
    It therefore re-derives every source-owned item and validates each retained
    derived item independently, while the expected R3 and output digest bind
    those exact retained bytes to the already completed normal-path build.
    """

    if type(document) is not bytes or len(document) > REPORT_JSON_MAX_BYTES:
        _failure("report_invalid")
    bound, normalized_packet, packet_value = _validated_descriptor_packet(
        descriptor,
        packet,
        code="report_invalid",
    )
    if inference_state not in REPORT_INFERENCE_STATES:
        _failure("report_invalid")
    _digest(expected_report_digest, code="report_invalid")
    _digest(expected_render_digest, code="report_invalid")
    accepted_output_digest = _digest(
        accepted_output_digest,
        code="report_invalid",
        nullable=True,
    )
    expected_prompt_digest = _digest(
        expected_prompt_digest,
        code="report_invalid",
        nullable=True,
    )
    recipe = bound["recipe"]
    mode = recipe["inference_mode"]
    if mode == "offline":
        if (
            inference_state != "disabled"
            or accepted_output_digest is not None
            or expected_prompt_digest is not None
        ):
            _failure("report_invalid")
    else:
        if expected_prompt_digest != FIXED_PROMPT_DIGEST or (
            (inference_state == "succeeded")
            != (accepted_output_digest is not None)
        ):
            _failure("report_invalid")
    if mode == "codex_optional" and inference_state == "disabled":
        _failure("report_invalid")

    try:
        envelope = _mapping(
            parse_canonical_json_document(document, maximum=REPORT_JSON_MAX_BYTES),
            REPORT_ENVELOPE_KEYS,
            code="report_invalid",
        )
        payload = _mapping(
            envelope["payload"], REPORT_PAYLOAD_KEYS, code="report_invalid"
        )
    except AnalysisContractError as exc:
        raise AnalysisValidationError(
            "report_invalid", "analysis report is invalid"
        ) from exc
    calculated_report_digest = sealed_domain_digest(
        "taskgov-analysis-report-v1",
        canonical_json_bytes(payload),
    )
    calculated_report_id = _report_id(
        source_key=bound["source_key"],
        recipe_digest=bound["recipe_digest"],
        inference_state=inference_state,
        accepted_output_digest=accepted_output_digest,
    )
    if (
        envelope["report_schema_version"] != REPORT_SCHEMA_VERSION
        or envelope["report_digest"] != calculated_report_digest
        or envelope["report_digest"] != expected_report_digest
        or payload["report_id"] != calculated_report_id
        or payload["report_id"] != expected_report_id
        or payload["analysis_job_id"] != bound["analysis_job_id"]
        or payload["source_kind"] != bound["source_kind"]
        or payload["source_key"] != bound["source_key"]
        or payload["recipe_digest"] != bound["recipe_digest"]
        or payload["inference_state"] != inference_state
    ):
        _failure("report_invalid")

    projection = _derive_source_projection(bound, packet_value)
    if (
        payload["structural_facts"] != projection.structural_facts
        or payload["trusted_caller_declarations"] != projection.declarations
        or payload["declared_code_occurrences"] != projection.occurrences
        or payload["legacy_absence"] != projection.legacy_absence
    ):
        _failure("report_invalid")

    base_claim_citations = (
        {projection.runtime_citation["citation_id"]}
        if bound["source_kind"] == "legacy_index_entry"
        else set(projection.pointer_citations.values())
    )
    derived = _validated_report_array(
        payload["llm_derived"],
        keys=("tag", "non_authoritative", "text", "citation_ids", "uncertainty"),
        maximum=CLAIM_MAX_COUNT,
    )
    if (mode != "codex_optional" or inference_state != "succeeded") and derived:
        _failure("report_invalid")
    for item in derived:
        text = _text(item["text"], code="report_invalid")
        if (
            item["tag"] != "llm_derived/batch_analyzer/1"
            or item["non_authoritative"] is not True
            or not text
            or len(text.encode("utf-8")) > CLAIM_TEXT_MAX_BYTES
            or item["uncertainty"] not in DERIVED_UNCERTAINTIES
            or (
                bound["source_kind"] == "native_bundle"
                and item["uncertainty"] == "legacy_absence"
            )
        ):
            _failure("report_invalid")
        try:
            _privacy_guard(text)
        except AnalysisValidationError as exc:
            raise AnalysisValidationError(
                "report_invalid", "analysis report is invalid"
            ) from exc
        _validated_report_citation_ids(
            item["citation_ids"], allowed=base_claim_citations
        )

    omission_rows = _validated_report_array(
        payload["omissions"],
        keys=("code", "citation_ids"),
        maximum=OMISSION_MAX_COUNT,
    )
    omission_codes = [item["code"] for item in omission_rows]
    if any(type(code) is not str for code in omission_codes) or len(
        omission_codes
    ) != len(set(omission_codes)):
        _failure("report_invalid")
    capacity_codes = {
        code
        for code in omission_codes
        if code in {"claim_capacity_exceeded", "render_capacity_exceeded"}
    }
    if capacity_codes and (
        mode != "codex_optional"
        or inference_state != "succeeded"
        or accepted_output_digest is None
        or len(derived) >= CLAIM_MAX_COUNT
    ):
        _failure("report_invalid")
    expected_omissions = list(projection.source_omissions)
    runtime_codes: list[str] = []
    if bound["source_kind"] == "legacy_index_entry":
        runtime_codes.append("legacy_detail_unavailable")
    if mode == "codex_optional" and inference_state != "succeeded":
        runtime_codes.append("inference_unavailable")
    runtime_codes.extend(
        code
        for code in ("claim_capacity_exceeded", "render_capacity_exceeded")
        if code in capacity_codes
    )
    expected_citations = dict(projection.citations)
    if runtime_codes:
        runtime_citation_id = _checked_citation_insert(
            expected_citations, projection.runtime_citation
        )
        expected_omissions.extend(
            {"code": code, "citation_ids": [runtime_citation_id]}
            for code in runtime_codes
        )
    expected_omissions = _sorted_unique(
        expected_omissions, maximum=OMISSION_MAX_COUNT
    )
    if omission_rows != expected_omissions:
        _failure("report_invalid")

    expected_uncertainties: list[dict[str, Any]] = []
    if projection.legacy_absence is not None:
        expected_uncertainties.append(
            {
                "code": "legacy_absence",
                "citation_ids": [projection.runtime_citation["citation_id"]],
            }
        )
    expected_uncertainties.extend(
        {"code": item["uncertainty"], "citation_ids": item["citation_ids"]}
        for item in derived
        if item["uncertainty"] != "none"
    )
    expected_uncertainties = _deduplicated_sorted(
        expected_uncertainties, maximum=UNCERTAINTY_MAX_COUNT
    )
    uncertainty_rows = _validated_report_array(
        payload["uncertainties"],
        keys=("code", "citation_ids"),
        maximum=UNCERTAINTY_MAX_COUNT,
    )
    if uncertainty_rows != expected_uncertainties:
        _failure("report_invalid")

    citation_keys = (
        "citation_id",
        "citation_kind",
        "source_key",
        "project_id",
        "projection_generation",
        "index_digest",
        "task_id",
        "completion_cycle_id",
        "cycle_ordinal",
    ) if bound["source_kind"] == "legacy_index_entry" else (
        "citation_id",
        "citation_kind",
        "source_key",
        "bundle_id",
        "bundle_digest",
        "file_digest",
        "json_pointer",
        "entity_id",
        "entity_digest",
    )
    citation_rows = _validated_report_array(
        payload["citations"],
        keys=citation_keys,
        maximum=CITATION_MAX_COUNT,
    )
    expected_citation_rows = _sorted_unique(
        list(expected_citations.values()), maximum=CITATION_MAX_COUNT
    )
    if citation_rows != expected_citation_rows:
        _failure("report_invalid")
    citation_ids = {item["citation_id"] for item in citation_rows}
    for rows in (
        projection.structural_facts,
        projection.declarations,
        projection.source_omissions,
        projection.occurrences,
        derived,
        omission_rows,
        uncertainty_rows,
    ):
        for item in rows:
            if "citation_ids" in item:
                _validated_report_citation_ids(
                    item["citation_ids"], allowed=citation_ids
                )

    reproducibility = _mapping(
        payload["reproducibility"],
        _REPRODUCIBILITY_KEYS,
        code="report_invalid",
    )
    if (
        reproducibility["producer_version"] != recipe["producer_version"]
        or reproducibility["declared_model_id"] != recipe["declared_model_id"]
        or reproducibility["prompt_schema_version"]
        != recipe["prompt_schema_version"]
        or reproducibility["input_digest"] != normalized_packet.packet_digest
        or reproducibility["accepted_output_digest"] != accepted_output_digest
        or reproducibility["report_schema_version"]
        != recipe["report_schema_version"]
        or reproducibility["renderer_version"] != recipe["renderer_version"]
        or reproducibility["prompt_digest"] != expected_prompt_digest
    ):
        _failure("report_invalid")

    try:
        markdown = analysis_renderer.render_markdown_v1(document)
    except AnalysisRendererError as exc:
        raise AnalysisValidationError(
            "report_invalid", "analysis report is invalid"
        ) from exc
    render_digest = "sha256:" + hashlib.sha256(markdown).hexdigest()
    if render_digest != expected_render_digest:
        _failure("report_invalid")
    return ValidatedAnalysisReport(
        envelope=envelope,
        report_document=document,
        markdown_bytes=markdown,
        report_id=payload["report_id"],
        report_digest=envelope["report_digest"],
        render_digest=render_digest,
    )


__all__ = (
    "ADAPTER_OUTPUT_MAX_BYTES",
    "AnalysisValidationError",
    "REPORT_JSON_MAX_BYTES",
    "ValidatedAdapterOutput",
    "ValidatedAnalysisReport",
    "build_analysis_report",
    "validate_adapter_output",
    "validate_recovery_report_document",
    "validate_report_document",
)
