"""Pure canonical JSON to Markdown v1 analysis renderer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn

from task_governance_tool.analysis_contracts import (
    AnalysisContractError,
    canonical_json_bytes,
    parse_canonical_json_document,
    sealed_domain_digest,
)


REPORT_JSON_MAX_BYTES = 16_777_216
REPORT_MARKDOWN_MAX_BYTES = 8_388_608

REPORT_ENVELOPE_KEYS = ("report_schema_version", "report_digest", "payload")
REPORT_PAYLOAD_KEYS = (
    "report_id",
    "analysis_job_id",
    "source_kind",
    "source_key",
    "recipe_digest",
    "inference_state",
    "structural_facts",
    "trusted_caller_declarations",
    "legacy_absence",
    "llm_derived",
    "omissions",
    "uncertainties",
    "declared_code_occurrences",
    "citations",
    "reproducibility",
)

_BLOCKS = (
    ("Structural Facts", "structural_facts"),
    ("Trusted Caller Declarations", "trusted_caller_declarations"),
    ("Legacy Absence", "legacy_absence"),
    ("LLM Derived", "llm_derived"),
    ("Omissions", "omissions"),
    ("Uncertainties", "uncertainties"),
    ("Declared Code Occurrences", "declared_code_occurrences"),
    ("Citations", "citations"),
    ("Reproducibility", "reproducibility"),
)


@dataclass(frozen=True)
class AnalysisRendererError(ValueError):
    """One sanitized rejection at the report-rendering boundary."""

    code: str = "report_invalid"
    message: str = "analysis report is invalid"

    def __str__(self) -> str:
        return self.message


def _invalid() -> NoReturn:
    raise AnalysisRendererError()


def _mapping(value: object, keys: tuple[str, ...]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        _invalid()
    return dict(value)


def _validated_envelope(document: bytes) -> dict[str, Any]:
    try:
        envelope = _mapping(
            parse_canonical_json_document(document, maximum=REPORT_JSON_MAX_BYTES),
            REPORT_ENVELOPE_KEYS,
        )
        payload = _mapping(envelope["payload"], REPORT_PAYLOAD_KEYS)
        if (
            envelope["report_schema_version"] != 1
            or type(envelope["report_digest"]) is not str
            or envelope["report_digest"]
            != sealed_domain_digest(
                "taskgov-analysis-report-v1",
                canonical_json_bytes(payload),
            )
        ):
            _invalid()
    except (AnalysisContractError, TypeError, ValueError) as exc:
        if isinstance(exc, AnalysisRendererError):
            raise
        raise AnalysisRendererError() from exc
    envelope["payload"] = payload
    return envelope


def render_markdown_v1(report_document: bytes) -> bytes:
    """Render exactly the contract's ten-block Markdown v1 byte sequence."""

    envelope = _validated_envelope(report_document)
    payload = envelope["payload"]
    identity = {
        "report_schema_version": envelope["report_schema_version"],
        "report_digest": envelope["report_digest"],
        "report_id": payload["report_id"],
        "analysis_job_id": payload["analysis_job_id"],
        "source_kind": payload["source_kind"],
        "source_key": payload["source_key"],
        "recipe_digest": payload["recipe_digest"],
        "inference_state": payload["inference_state"],
    }
    blocks = [
        b"## Identity\n\n    " + canonical_json_bytes(identity),
        *(
            b"## "
            + name.encode("utf-8")
            + b"\n\n    "
            + canonical_json_bytes(payload[key])
            for name, key in _BLOCKS
        ),
    ]
    rendered = (
        b"# Task Governance Analysis Report v1\n\n"
        + b"\n\n".join(blocks)
        + b"\n"
    )
    if len(rendered) > REPORT_MARKDOWN_MAX_BYTES:
        _invalid()
    return rendered


__all__ = (
    "AnalysisRendererError",
    "REPORT_JSON_MAX_BYTES",
    "REPORT_MARKDOWN_MAX_BYTES",
    "REPORT_ENVELOPE_KEYS",
    "REPORT_PAYLOAD_KEYS",
    "render_markdown_v1",
)
