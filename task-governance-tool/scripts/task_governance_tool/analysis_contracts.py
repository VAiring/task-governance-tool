"""Pure contracts for the local derived-evidence analysis pipeline.

This module owns byte encoding, identifiers, digests, and closed descriptor and
status shapes.  It performs no filesystem, SQLite, subprocess, or network I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn


DESCRIPTOR_VERSION = 1
PACKET_VERSION = 1
PRODUCER_VERSION = 1
REPORT_SCHEMA_VERSION = 1
RENDERER_VERSION = 1
PROMPT_SCHEMA_VERSION = 1

NATIVE_PACKET_MAX_BYTES = 16_842_752
LEGACY_PACKET_MAX_BYTES = 16_384
ANALYSIS_STDIN_MAX_BYTES = 262_144
ANALYSIS_DESCRIPTOR_MAX_BYTES = 131_072
ANALYSIS_STATUS_MAX_BYTES = 16_384

SOURCE_KINDS = frozenset({"native_bundle", "legacy_index_entry"})
INFERENCE_MODES = frozenset({"offline", "codex_optional"})
STATUS_STATES = frozenset(
    {"pending", "running", "published", "failed", "cancelled"}
)
TERMINAL_STATUS_STATES = frozenset({"published", "failed", "cancelled"})
INFERENCE_STATES = frozenset(
    {
        "disabled",
        "policy_blocked",
        "pending",
        "running",
        "succeeded",
        "input_too_large",
        "unavailable",
        "launch_failed",
        "timeout",
        "output_too_large",
        "invalid_output",
        "failed",
        "cancelled",
    }
)
FAILED_CODES = frozenset(
    {
        "source_invalid",
        "packet_too_large",
        "report_invalid",
        "publication_failed",
        "interrupted",
    }
)
POST_ATTEMPT_INFERENCE_STATES = frozenset(
    {
        "policy_blocked",
        "input_too_large",
        "succeeded",
        "unavailable",
        "launch_failed",
        "timeout",
        "output_too_large",
        "invalid_output",
        "failed",
    }
)
NO_CALL_INFERENCE_STATES = frozenset({"policy_blocked", "input_too_large"})
ATTEMPT_OUTCOME_INFERENCE_STATES = (
    POST_ATTEMPT_INFERENCE_STATES - NO_CALL_INFERENCE_STATES
)
RETRYABLE_INFERENCE_STATES = frozenset(
    {"unavailable", "launch_failed", "timeout", "invalid_output"}
)

SOURCE_BASIS_KEYS = (
    "project_id",
    "projection_generation",
    "index_digest",
    "entry",
)
SOURCE_ENTRY_KEYS = (
    "task_id",
    "completion_cycle_id",
    "cycle_ordinal",
    "bundle_state",
    "bundle_id",
    "bundle_file",
    "bundle_digest",
    "file_digest",
    "sealed_at",
)
RECIPE_KEYS = (
    "producer_version",
    "report_schema_version",
    "renderer_version",
    "prompt_schema_version",
    "inference_mode",
    "declared_model_id",
)
DESCRIPTOR_KEYS = (
    "analysis_job_id",
    "descriptor_version",
    "source_kind",
    "source_key",
    "source_basis",
    "recipe",
    "recipe_digest",
    "descriptor_digest",
)
STATUS_KEYS = (
    "analysis_job_id",
    "state",
    "worker_attempt_count",
    "adapter_attempt_count",
    "inference_state",
    "fixed_code",
    "duration_ms",
    "packet_digest",
    "accepted_output_digest",
    "report_id",
    "report_digest",
    "render_digest",
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROJECT_ID = re.compile(
    r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{12}|tg_project_[0-9a-f]{32})$"
)
_TASK_ID = re.compile(r"^tg_task_[0-9a-f]{16}$")
_CYCLE_ID = re.compile(r"^tg_completion_cycle_[0-9a-f]{16}$")
_BUNDLE_ID = re.compile(r"^tg_completion_evidence_bundle_[0-9a-f]{16}$")
_UTC_SECOND = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$"
)
_JOB_ID = re.compile(r"^tg_analysis_job_[0-9a-f]{16}$")
_REPORT_ID = re.compile(r"^tg_analysis_report_[0-9a-f]{16}$")
_DECLARED_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$")


@dataclass(frozen=True)
class AnalysisContractError(ValueError):
    """One fixed rejection at a pure analysis-contract boundary."""

    code: str = "analysis_contract_invalid"
    message: str = "analysis data is invalid"

    def __str__(self) -> str:
        return self.message


def _invalid(
    code: str = "analysis_contract_invalid",
    message: str = "analysis data is invalid",
) -> NoReturn:
    raise AnalysisContractError(code, message)


def _escape_string(value: str) -> bytes:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise AnalysisContractError() from exc
    short = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    result = ['"']
    for character in value:
        replacement = short.get(character)
        if replacement is not None:
            result.append(replacement)
        elif ord(character) < 0x20:
            result.append(f"\\u00{ord(character):02x}")
        else:
            result.append(character)
    result.append('"')
    return "".join(result).encode("utf-8", errors="strict")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the exact integer-only canonical JSON form without an LF."""

    active: set[int] = set()

    def encode(item: Any) -> bytes:
        if item is None:
            return b"null"
        if type(item) is bool:
            return b"true" if item else b"false"
        if type(item) is int:
            return str(item).encode("ascii")
        if type(item) is str:
            return _escape_string(item)
        if type(item) is list:
            identity = id(item)
            if identity in active:
                _invalid()
            active.add(identity)
            try:
                return b"[" + b",".join(encode(value) for value in item) + b"]"
            finally:
                active.remove(identity)
        if type(item) is dict:
            identity = id(item)
            if identity in active or any(type(key) is not str for key in item):
                _invalid()
            active.add(identity)
            try:
                return b"{" + b",".join(
                    _escape_string(key) + b":" + encode(item[key])
                    for key in sorted(item)
                ) + b"}"
            finally:
                active.remove(identity)
        _invalid()

    return encode(value)


def canonical_json_document_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _pairs_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _reject_float(_value: str) -> NoReturn:
    _invalid()


def parse_canonical_json_document(
    document: bytes,
    *,
    maximum: int,
) -> Any:
    """Parse one duplicate-free canonical JSON document with exactly one LF."""

    if (
        type(document) is not bytes
        or type(maximum) is not int
        or maximum < 2
        or len(document) < 2
        or len(document) > maximum
        or not document.endswith(b"\n")
    ):
        _invalid()
    body = document[:-1]
    try:
        decoded = body.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except AnalysisContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AnalysisContractError() from exc
    if canonical_json_bytes(value) != body:
        _invalid()
    return value


def _mapping(value: object, keys: Sequence[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        _invalid()
    copied = dict(value)
    canonical_json_bytes(copied)
    return copied


def _text(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or "\0" in value:
        _invalid()
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise AnalysisContractError() from exc
    return value


def _integer(value: object, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum:
        _invalid()
    if maximum is not None and value > maximum:
        _invalid()
    return value


def _digest(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _invalid()
    return value


def _utc_second(value: object) -> str:
    text = _text(value)
    matched = _UTC_SECOND.fullmatch(text)
    if matched is None:
        _invalid()
    try:
        datetime(*(int(part) for part in matched.groups()))
    except ValueError as exc:
        raise AnalysisContractError() from exc
    return text


def domain_hash(domain: str, body: bytes) -> bytes:
    """Return H(domain, body) from the TG-M23 contract."""

    if type(domain) is not str or type(body) is not bytes or not domain or "\0" in domain:
        _invalid()
    try:
        prefix = domain.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise AnalysisContractError() from exc
    return hashlib.sha256(prefix + b"\0" + body).digest()


def sealed_domain_digest(domain: str, body: bytes) -> str:
    return "sha256:" + domain_hash(domain, body).hex()


def source_key_for_identity(identity: Mapping[str, Any]) -> str:
    """Seal an already selected exact source-identity object."""

    if not isinstance(identity, Mapping):
        _invalid()
    copied = dict(identity)
    return sealed_domain_digest(
        "taskgov-analysis-source-v1",
        canonical_json_bytes(copied),
    )


def validate_source_basis(value: object, *, source_kind: str) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        _invalid()
    basis = _mapping(value, SOURCE_BASIS_KEYS)
    project_id = _text(basis["project_id"])
    if _PROJECT_ID.fullmatch(project_id) is None:
        _invalid()
    _integer(basis["projection_generation"])
    _digest(basis["index_digest"])
    entry = _mapping(basis["entry"], SOURCE_ENTRY_KEYS)
    task_id = _text(entry["task_id"])
    cycle_id = _text(entry["completion_cycle_id"])
    if _TASK_ID.fullmatch(task_id) is None or _CYCLE_ID.fullmatch(cycle_id) is None:
        _invalid()
    _integer(entry["cycle_ordinal"], minimum=1)
    native = source_kind == "native_bundle"
    expected_state = "native" if native else "legacy_unknown"
    if entry["bundle_state"] != expected_state:
        _invalid()
    bundle_fields = (
        "bundle_id",
        "bundle_file",
        "bundle_digest",
        "file_digest",
        "sealed_at",
    )
    if native:
        for field in bundle_fields:
            if not _text(entry[field]):
                _invalid()
        bundle_id = _text(entry["bundle_id"])
        if (
            _BUNDLE_ID.fullmatch(bundle_id) is None
            or entry["bundle_file"] != f"bundles/{bundle_id}.json"
        ):
            _invalid()
        _digest(entry["bundle_digest"])
        _digest(entry["file_digest"])
        _utc_second(entry["sealed_at"])
    elif any(entry[field] is not None for field in bundle_fields):
        _invalid()
    basis["entry"] = entry
    return basis


def source_identity(source_kind: str, source_basis: object) -> dict[str, Any]:
    basis = validate_source_basis(source_basis, source_kind=source_kind)
    entry = basis["entry"]
    if source_kind == "native_bundle":
        return {
            "project_id": basis["project_id"],
            "task_id": entry["task_id"],
            "completion_cycle_id": entry["completion_cycle_id"],
            "cycle_ordinal": entry["cycle_ordinal"],
            "bundle_id": entry["bundle_id"],
            "bundle_digest": entry["bundle_digest"],
            "file_digest": entry["file_digest"],
        }
    return {
        "project_id": basis["project_id"],
        "task_id": entry["task_id"],
        "completion_cycle_id": entry["completion_cycle_id"],
        "cycle_ordinal": entry["cycle_ordinal"],
        "bundle_state": entry["bundle_state"],
    }


def validate_recipe(value: object) -> dict[str, Any]:
    recipe = _mapping(value, RECIPE_KEYS)
    for field in RECIPE_KEYS[:4]:
        _integer(recipe[field], minimum=1)
    mode = recipe["inference_mode"]
    if mode not in INFERENCE_MODES:
        _invalid()
    model_id = recipe["declared_model_id"]
    if mode == "offline":
        if model_id is not None:
            _invalid()
    elif type(model_id) is not str or _DECLARED_MODEL.fullmatch(model_id) is None:
        _invalid()
    return recipe


def default_recipe(
    *,
    inference_mode: str = "offline",
    declared_model_id: str | None = None,
) -> dict[str, Any]:
    return validate_recipe(
        {
            "producer_version": PRODUCER_VERSION,
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "renderer_version": RENDERER_VERSION,
            "prompt_schema_version": PROMPT_SCHEMA_VERSION,
            "inference_mode": inference_mode,
            "declared_model_id": declared_model_id,
        }
    )


def recipe_digest(recipe: object) -> str:
    normalized = validate_recipe(recipe)
    return sealed_domain_digest(
        "taskgov-analysis-recipe-v1",
        canonical_json_bytes(normalized),
    )


def build_descriptor(
    *,
    source_kind: str,
    source_basis: object,
    recipe: object,
) -> dict[str, Any]:
    basis = validate_source_basis(source_basis, source_kind=source_kind)
    normalized_recipe = validate_recipe(recipe)
    source_key = source_key_for_identity(source_identity(source_kind, basis))
    sealed_recipe = recipe_digest(normalized_recipe)
    job_body = source_key.encode("ascii") + b"\0" + sealed_recipe.encode("ascii")
    job_id = "tg_analysis_job_" + domain_hash(
        "taskgov-analysis-job-v1",
        job_body,
    ).hex()[:16]
    unsealed = {
        "analysis_job_id": job_id,
        "descriptor_version": DESCRIPTOR_VERSION,
        "source_kind": source_kind,
        "source_key": source_key,
        "source_basis": basis,
        "recipe": normalized_recipe,
        "recipe_digest": sealed_recipe,
    }
    return {
        **unsealed,
        "descriptor_digest": sealed_domain_digest(
            "taskgov-analysis-descriptor-v1",
            canonical_json_bytes(unsealed),
        ),
    }


def validate_descriptor(value: object) -> dict[str, Any]:
    descriptor = _mapping(value, DESCRIPTOR_KEYS)
    if descriptor["descriptor_version"] != DESCRIPTOR_VERSION:
        _invalid()
    if type(descriptor["analysis_job_id"]) is not str or _JOB_ID.fullmatch(
        descriptor["analysis_job_id"]
    ) is None:
        _invalid()
    _digest(descriptor["source_key"])
    _digest(descriptor["recipe_digest"])
    _digest(descriptor["descriptor_digest"])
    expected = build_descriptor(
        source_kind=descriptor["source_kind"],
        source_basis=descriptor["source_basis"],
        recipe=descriptor["recipe"],
    )
    if descriptor != expected:
        _invalid()
    return descriptor


def descriptor_replay_matches(
    existing: object,
    proposed: object,
) -> bool:
    """Apply the exact replay comparison while ignoring index-only drift."""

    left = validate_descriptor(existing)
    right = validate_descriptor(proposed)
    return (
        left["analysis_job_id"] == right["analysis_job_id"]
        and left["source_key"] == right["source_key"]
        and left["recipe_digest"] == right["recipe_digest"]
        and left["source_basis"]["project_id"]
        == right["source_basis"]["project_id"]
        and left["source_basis"]["entry"]
        == right["source_basis"]["entry"]
    )


def pending_status(descriptor: object) -> dict[str, Any]:
    normalized = validate_descriptor(descriptor)
    return {
        "analysis_job_id": normalized["analysis_job_id"],
        "state": "pending",
        "worker_attempt_count": 0,
        "adapter_attempt_count": 0,
        "inference_state": (
            "disabled"
            if normalized["recipe"]["inference_mode"] == "offline"
            else "pending"
        ),
        "fixed_code": None,
        "duration_ms": 0,
        "packet_digest": None,
        "accepted_output_digest": None,
        "report_id": None,
        "report_digest": None,
        "render_digest": None,
    }


def validate_status(value: object, *, descriptor: object) -> dict[str, Any]:
    status = _mapping(value, STATUS_KEYS)
    bound = validate_descriptor(descriptor)
    if status["analysis_job_id"] != bound["analysis_job_id"]:
        _invalid()
    state = status["state"]
    inference = status["inference_state"]
    if state not in STATUS_STATES or inference not in INFERENCE_STATES:
        _invalid()
    worker = _integer(status["worker_attempt_count"], maximum=2)
    adapter = _integer(status["adapter_attempt_count"], maximum=2)
    duration = _integer(status["duration_ms"], maximum=600_000)
    if duration > 300_000 * worker:
        _invalid()
    packet_digest = _digest(status["packet_digest"], nullable=True)
    output_digest = _digest(status["accepted_output_digest"], nullable=True)
    report_id = status["report_id"]
    if report_id is not None and (
        type(report_id) is not str or _REPORT_ID.fullmatch(report_id) is None
    ):
        _invalid()
    report_digest = _digest(status["report_digest"], nullable=True)
    render_digest = _digest(status["render_digest"], nullable=True)
    r3 = (report_id, report_digest, render_digest)
    if not (all(item is None for item in r3) or all(item is not None for item in r3)):
        _invalid()
    has_intent = all(item is not None for item in r3)
    fixed_code = status["fixed_code"]
    mode = bound["recipe"]["inference_mode"]

    optional_pre_call_reclaim_terminal = (
        mode == "codex_optional"
        and state == "failed"
        and worker == 2
        and adapter == 0
        and inference == "failed"
        and fixed_code == "interrupted"
        and packet_digest is not None
        and output_digest is None
        and not has_intent
    )

    if mode == "offline":
        if adapter != 0 or inference != "disabled" or output_digest is not None:
            _invalid()
    else:
        if optional_pre_call_reclaim_terminal:
            pass
        elif inference == "cancelled":
            pass
        elif inference in {"pending", "policy_blocked", "input_too_large"}:
            if adapter != 0:
                _invalid()
        elif adapter < 1:
            _invalid()
        if (inference == "succeeded") != (output_digest is not None):
            _invalid()

    if state == "pending":
        if (
            worker != 0
            or adapter != 0
            or duration != 0
            or fixed_code is not None
            or packet_digest is not None
            or output_digest is not None
            or any(item is not None for item in r3)
            or inference != ("disabled" if mode == "offline" else "pending")
        ):
            _invalid()
    else:
        if worker < 1:
            _invalid()
        if state == "running":
            if (
                fixed_code is not None
                or packet_digest is None
                or (
                    mode == "codex_optional"
                    and inference
                    not in {"pending", "running", *POST_ATTEMPT_INFERENCE_STATES}
                )
                or (
                    mode == "codex_optional"
                    and has_intent
                    and inference not in POST_ATTEMPT_INFERENCE_STATES
                )
            ):
                _invalid()
        elif state == "published":
            if (
                fixed_code is not None
                or packet_digest is None
                or any(item is None for item in r3)
                or (
                    mode == "codex_optional"
                    and inference not in POST_ATTEMPT_INFERENCE_STATES
                )
            ):
                _invalid()
        elif state == "failed":
            if fixed_code not in FAILED_CODES or any(item is not None for item in r3):
                _invalid()
            if fixed_code in {"source_invalid", "packet_too_large"}:
                if (
                    packet_digest is not None
                    or output_digest is not None
                    or inference != ("disabled" if mode == "offline" else "pending")
                ):
                    _invalid()
            elif fixed_code in {"report_invalid", "publication_failed"}:
                if packet_digest is None or (
                    mode == "codex_optional"
                    and inference not in POST_ATTEMPT_INFERENCE_STATES
                ):
                    _invalid()
            else:
                if packet_digest is None:
                    if (
                        output_digest is not None
                        or inference != ("disabled" if mode == "offline" else "pending")
                    ):
                        _invalid()
                elif output_digest is None:
                    if mode == "codex_optional" and inference not in (
                        POST_ATTEMPT_INFERENCE_STATES - {"succeeded"}
                    ):
                        _invalid()
                elif inference != "succeeded":
                    _invalid()
        elif state == "cancelled":
            if fixed_code != "cancelled" or any(item is not None for item in r3):
                _invalid()
            if mode == "offline" and inference != "disabled":
                _invalid()
            if mode == "codex_optional" and inference != "cancelled":
                _invalid()
    return status


def validate_status_transition(
    old: object,
    new: object,
    *,
    descriptor: object,
) -> dict[str, Any]:
    """Validate one literal persisted status transition without I/O."""

    bound = validate_descriptor(descriptor)
    left = validate_status(old, descriptor=bound)
    right = validate_status(new, descriptor=bound)
    old_state = left["state"]
    new_state = right["state"]
    if old_state in TERMINAL_STATUS_STATES:
        if canonical_json_bytes(left) != canonical_json_bytes(right):
            _invalid()
        return right

    r3_fields = ("report_id", "report_digest", "render_digest")
    old_r3 = tuple(left[field] for field in r3_fields)
    new_r3 = tuple(right[field] for field in r3_fields)
    if old_state == "pending":
        if new_state == "failed":
            if (
                right["fixed_code"] not in {"source_invalid", "packet_too_large"}
                or right["worker_attempt_count"] != 1
                or right["adapter_attempt_count"] != 0
                or right["inference_state"] != left["inference_state"]
                or right["packet_digest"] is not None
                or right["accepted_output_digest"] is not None
                or any(item is not None for item in new_r3)
            ):
                _invalid()
            return right
        if (
            new_state != "running"
            or right["worker_attempt_count"] != left["worker_attempt_count"] + 1
            or right["adapter_attempt_count"] != left["adapter_attempt_count"]
            or right["duration_ms"] != left["duration_ms"]
            or right["inference_state"] != left["inference_state"]
            or right["fixed_code"] is not None
            or right["packet_digest"] is None
            or right["accepted_output_digest"]
            != left["accepted_output_digest"]
            or new_r3 != old_r3
        ):
            _invalid()
        return right

    if old_state != "running" or new_state not in {"running", *TERMINAL_STATUS_STATES}:
        _invalid()
    old_worker = left["worker_attempt_count"]
    new_worker = right["worker_attempt_count"]
    old_adapter = left["adapter_attempt_count"]
    new_adapter = right["adapter_attempt_count"]
    if (
        new_worker not in {old_worker, old_worker + 1}
        or new_adapter not in {old_adapter, old_adapter + 1}
        or right["duration_ms"] < left["duration_ms"]
    ):
        _invalid()
    worker_incremented = new_worker == old_worker + 1
    adapter_incremented = new_adapter == old_adapter + 1
    if worker_incremented and adapter_incremented:
        _invalid()
    if right["packet_digest"] != left["packet_digest"]:
        _invalid()

    old_output = left["accepted_output_digest"]
    new_output = right["accepted_output_digest"]
    old_has_intent = all(item is not None for item in old_r3)
    new_has_intent = all(item is not None for item in new_r3)

    # A worker reclaim is literally counter-only.  It never changes duration,
    # launches, changes an attempt phase, consumes output, or changes intent.
    if worker_incremented:
        if (
            old_has_intent
            or any(
                right[field] != left[field]
                for field in STATUS_KEYS
                if field != "worker_attempt_count"
            )
        ):
            _invalid()
        return right

    mode = bound["recipe"]["inference_mode"]
    old_inference = left["inference_state"]
    new_inference = right["inference_state"]

    # The optional pre-call reclaim terminal is the second write after the
    # counter-only worker 1 -> 2 CAS.  It cannot consume an adapter attempt or
    # change duration, packet, output, publication intent, or binding fields.
    if (
        mode == "codex_optional"
        and new_state == "failed"
        and new_adapter == 0
        and new_inference == "failed"
        and right["fixed_code"] == "interrupted"
    ):
        if (
            old_worker != 2
            or new_worker != 2
            or old_adapter != 0
            or old_inference != "pending"
            or new_inference != "failed"
            or left["fixed_code"] is not None
            or right["fixed_code"] != "interrupted"
            or left["packet_digest"] is None
            or old_output is not None
            or old_has_intent
            or any(
                right[field] != left[field]
                for field in STATUS_KEYS
                if field not in {"state", "inference_state", "fixed_code"}
            )
        ):
            _invalid()
        return right

    # An adapter counter is consumed in a distinct pre-call write.  Attempt 1
    # starts only from adapter-zero pending; attempt 2 starts only from a
    # contractually retryable closed outcome.  The write itself has no output.
    if adapter_incremented:
        initial_call = old_adapter == 0 and old_inference == "pending"
        retry_call = (
            old_adapter == 1 and old_inference in RETRYABLE_INFERENCE_STATES
        )
        if (
            mode != "codex_optional"
            or not (initial_call or retry_call)
            or new_state != "running"
            or new_worker != old_worker
            or new_inference != "running"
            or old_output is not None
            or new_output is not None
            or old_has_intent
            or new_has_intent
        ):
            _invalid()
        return right

    # From this point both counters are unchanged.  Output is immutable except
    # for the single active-attempt -> succeeded outcome write.
    phase_changed = old_inference != new_inference
    if new_state == "cancelled":
        if (
            old_has_intent
            or new_has_intent
            or old_output is not None
            or new_output is not None
            or (
                mode == "codex_optional" and new_inference != "cancelled"
            )
            or (mode == "offline" and new_inference != "disabled")
        ):
            _invalid()
        return right

    if mode == "offline":
        if phase_changed:
            _invalid()
    elif old_inference == "pending":
        if phase_changed and (
            new_inference not in NO_CALL_INFERENCE_STATES
            or new_state != "running"
            or old_has_intent
            or new_has_intent
        ):
            _invalid()
    elif old_inference == "running":
        if phase_changed and new_inference not in ATTEMPT_OUTCOME_INFERENCE_STATES:
            _invalid()
        if phase_changed and (old_has_intent or new_has_intent):
            _invalid()
    elif old_inference in POST_ATTEMPT_INFERENCE_STATES:
        if phase_changed:
            _invalid()
    else:
        _invalid()

    if phase_changed:
        valid_outcome_capture = (
            mode == "codex_optional"
            and old_inference == "running"
            and new_inference in ATTEMPT_OUTCOME_INFERENCE_STATES
            and old_output is None
            and (new_output is not None) == (new_inference == "succeeded")
        )
        valid_no_call = (
            mode == "codex_optional"
            and old_inference == "pending"
            and new_inference in NO_CALL_INFERENCE_STATES
            and old_output is None
            and new_output is None
        )
        if not (valid_outcome_capture or valid_no_call):
            _invalid()
    elif new_output != old_output:
        _invalid()

    if new_state == "running":
        if old_has_intent:
            if new_r3 != old_r3:
                _invalid()
        elif new_has_intent:
            reportable = mode == "offline" or (
                old_inference in POST_ATTEMPT_INFERENCE_STATES
                and new_inference == old_inference
            )
            if not reportable:
                _invalid()
        return right

    if new_state == "published":
        if phase_changed or not old_has_intent or new_r3 != old_r3:
            _invalid()
        return right

    if new_state != "failed":
        _invalid()
    if old_has_intent:
        if (
            right["fixed_code"] not in {"publication_failed", "report_invalid"}
            or new_has_intent
            or phase_changed
        ):
            _invalid()
    elif (
        new_has_intent
        or right["fixed_code"] not in {"report_invalid", "interrupted"}
    ):
        _invalid()
    return right


__all__ = (
    "ANALYSIS_DESCRIPTOR_MAX_BYTES",
    "ANALYSIS_STATUS_MAX_BYTES",
    "ANALYSIS_STDIN_MAX_BYTES",
    "AnalysisContractError",
    "DESCRIPTOR_KEYS",
    "LEGACY_PACKET_MAX_BYTES",
    "NATIVE_PACKET_MAX_BYTES",
    "PACKET_VERSION",
    "SOURCE_BASIS_KEYS",
    "SOURCE_ENTRY_KEYS",
    "STATUS_KEYS",
    "build_descriptor",
    "canonical_json_bytes",
    "canonical_json_document_bytes",
    "default_recipe",
    "descriptor_replay_matches",
    "domain_hash",
    "parse_canonical_json_document",
    "pending_status",
    "recipe_digest",
    "sealed_domain_digest",
    "source_identity",
    "source_key_for_identity",
    "validate_descriptor",
    "validate_recipe",
    "validate_source_basis",
    "validate_status",
    "validate_status_transition",
)
