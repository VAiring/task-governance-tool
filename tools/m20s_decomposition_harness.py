"""Temporary M20S decomposition-study validator and one-shot reducer.

The module is root-only and deliberately cannot launch agents or commands.  It
accepts only sanitized records from the separately orchestrated TG-M20S.2
trials and writes only the fixed ignored study journal/corpus plus its bounded
terminal receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_RELATIVE = Path("fixtures/m20s/protocol-v1.json")
PLAN_RELATIVE = Path("fixtures/m20s/episode-plan-v1.json")
JOURNAL_RELATIVE = Path("dist/m20s/decomposition-attempt-journal.json")
CORPUS_RELATIVE = Path("dist/m20s/decomposition-observations.json")
LOCK_RELATIVE = Path("dist/m20s/decomposition-collector.lock")
RECEIPT_RELATIVE = Path("fixtures/m20s/decomposition-collection-receipt.json")

PROTOCOL_SCHEMA = "m20s-decomposition-protocol-v1"
PLAN_SCHEMA = "m20s-decomposition-episode-plan-v1"
OBSERVATION_SCHEMA = "m20s-decomposition-observation-v1"
JOURNAL_SCHEMA = "m20s-decomposition-attempt-journal-v1"
RECEIPT_SCHEMA = "m20s-decomposition-collection-receipt-v1"
UNIT = "M20S.2"

AUTHORITY_TASK_ID = "tg_task_ddfbf721eced8c58"
AUTHORITY_CONTRACT_REVISION = 1
AUTHORITY_REF = (
    "conversation_decision:2026-08-01:"
    "interrupt-successor-task-decomposition-observation"
)
BASELINE_REVISION = "43c91d5987b0c35c66f834789aea782e98dcaff7"
PACKAGE_TREE = "529abf7ac4e4ed778b383c90b6ac5f2fedc71615"
PROTOCOL_CANONICAL_SHA256 = (
    "c7ec941b6b5bebfc7370466e593d51a26c21da9a4793b7dd6eef8e2437c28f01"
)
EPISODE_PLAN_CANONICAL_SHA256 = (
    "2b230e8494571c9f30614845ca2966198685898008eb3387320a61f093be2794"
)

SCENARIOS = (
    "sp_user_expansion_alternate",
    "sp_in_scope_discovery_alternate",
    "sp_cross_module_failure_alternate",
)
REPLACED_SCENARIOS = (
    "sp_user_expansion",
    "sp_in_scope_discovery",
    "sp_cross_module_failure",
)
ARMS = ("broad", "bounded")
RECORD_SPECS = (
    ("split_measurement", "machine_observed"),
    ("attestation", "observer_attested"),
)
VECTOR_FIELDS = (
    "files",
    "modules",
    "lines",
    "contract_revision",
    "review_generation",
    "governance_cycles",
    "review_cycles",
)
INDEPENDENCE_FIELDS = (
    "acceptance_independent",
    "verification_independent",
    "commit_independent",
    "completion_independent",
)
UNKNOWN_REASONS = frozenset(
    {
        "not_observable",
        "source_missing",
        "source_drift",
        "parse_failed",
        "timeout",
        "cap_exceeded",
        "observer_uncertain",
        "contaminated",
    }
)
EXCLUDING_REASONS = frozenset(
    {"source_missing", "source_drift", "parse_failed", "timeout", "cap_exceeded", "contaminated"}
)
PAYLOAD_INVALIDATING_REASONS = frozenset(
    {"source_missing", "source_drift", "parse_failed", "contaminated"}
)
ELIGIBILITY = frozenset({"eligible", "partial", "excluded"})
OUTCOMES = frozenset(
    {"completed", "blocked", "paused", "handed_off", "failed", "inconclusive"}
)

MAX_FIXTURE_BYTES = 32_768
MAX_RECORD_BYTES = 16_384
MAX_CORPUS_BYTES = 65_536
MAX_JOURNAL_BYTES = 65_536
MAX_RECEIPT_BYTES = 4_096
MAX_UNKNOWNS = 8
MAX_METRIC = 2_147_483_647

_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9._-]{1,64}\Z")
_FIELD = re.compile(r"[a-z0-9_.]{1,160}\Z")
_SECRET_PATTERNS = (
    re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}"),
    re.compile(rb"(?i)bearer[ ]+[A-Za-z0-9._-]{8,}"),
    re.compile(rb"(?i)(?:token|cookie|authorization)="),
    re.compile(rb"[A-Za-z]:\\\\"),
    re.compile(rb"/(?:Users|home)/"),
)
_RAW_KEYS = tuple(
    f'"{key}":'.encode("ascii")
    for key in (
        "prompt",
        "chat",
        "reasoning",
        "review_body",
        "stdout",
        "stderr",
        "argv",
        "path",
        "diff",
        "environment",
        "control_bytes",
    )
)


class M20SObservationError(RuntimeError):
    """Stable sanitized study failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise M20SObservationError(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, UnicodeError, ValueError):
        _fail("parse_failed")


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact(value: Any, keys: set[str] | frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail("parse_failed")
    return value


def _integer(value: Any, maximum: int = MAX_METRIC) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _fail("parse_failed")
    return value


def _nullable_integer(value: Any) -> int | None:
    return None if value is None else _integer(value)


def _string(value: Any, pattern: re.Pattern[str] = _IDENTIFIER) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail("parse_failed")
    return value


def _privacy_check(raw: bytes) -> None:
    lowered = raw.lower()
    if any(key in lowered for key in _RAW_KEYS):
        _fail("privacy_violation")
    if any(pattern.search(raw) is not None for pattern in _SECRET_PATTERNS):
        _fail("privacy_violation")


def _is_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        _fail("source_missing")
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & marker)


def _fixed_path(root: Path, relative: Path, *, create_parent: bool = False) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        _fail("unsafe_path")
    candidate = root / relative
    current = candidate
    while current != root:
        if os.path.lexists(current) and _is_reparse(current):
            _fail("unsafe_path")
        current = current.parent
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        _fail("unsafe_path")
    if create_parent:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse(candidate.parent):
            _fail("unsafe_path")
    return candidate


def _load_json(path: Path, maximum: int) -> tuple[Any, bytes]:
    if not path.is_file() or _is_reparse(path):
        _fail("source_missing")
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("source_missing")
    if not raw or len(raw) > maximum:
        _fail("cap_exceeded")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs)
    except M20SObservationError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("parse_failed")
    return value, raw


def _atomic_write(path: Path, payload: bytes, *, replace: bool = False) -> None:
    temporary = path.with_name(f".{path.name}.m20s-tmp")
    if os.path.lexists(temporary) or (not replace and os.path.lexists(path)):
        _fail("artifact_exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            _fail("artifact_write_failed")


@contextmanager
def _exclusive_lock(path: Path):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        _fail("collection_busy")
    except OSError:
        _fail("artifact_write_failed")
    os.close(descriptor)
    try:
        yield
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _identity() -> dict[str, Any]:
    return {
        "authority_task_id": AUTHORITY_TASK_ID,
        "authority_contract_revision": AUTHORITY_CONTRACT_REVISION,
        "authority_ref": AUTHORITY_REF,
        "baseline_revision": BASELINE_REVISION,
        "package_tree": PACKAGE_TREE,
        "protocol_sha256": PROTOCOL_CANONICAL_SHA256,
        "episode_plan_sha256": EPISODE_PLAN_CANONICAL_SHA256,
    }


def _validate_protocol(value: Any) -> dict[str, Any]:
    root = _exact(
        value,
        {
            "schema",
            "authority_task_id",
            "authority_contract_revision",
            "authority_ref",
            "baseline_revision",
            "package_tree",
            "inherited",
            "replacements",
            "records",
            "bounds",
            "stop_rules",
            "runtime_boundary",
            "retention",
        },
    )
    if (
        root["schema"] != PROTOCOL_SCHEMA
        or root["authority_task_id"] != AUTHORITY_TASK_ID
        or root["authority_contract_revision"] != AUTHORITY_CONTRACT_REVISION
        or root["authority_ref"] != AUTHORITY_REF
        or root["baseline_revision"] != BASELINE_REVISION
        or root["package_tree"] != PACKAGE_TREE
    ):
        _fail("source_drift")
    _string(root["baseline_revision"], _HEX_40)
    _string(root["package_tree"], _HEX_40)
    inherited = _exact(
        root["inherited"],
        {
            "source",
            "denominator",
            "eligible_pairs",
            "qualifying_pairs",
            "unavailable_pairs",
            "handoff_control_eligible",
            "conflict",
        },
    )
    if inherited != {
        "source": "m20_retired_aggregate_v1",
        "denominator": 4,
        "eligible_pairs": 1,
        "qualifying_pairs": 1,
        "unavailable_pairs": 3,
        "handoff_control_eligible": True,
        "conflict": False,
    }:
        _fail("source_drift")
    replacements = root["replacements"]
    if not isinstance(replacements, list) or len(replacements) != 3:
        _fail("source_drift")
    for order, (raw, scenario, replaced) in enumerate(
        zip(replacements, SCENARIOS, REPLACED_SCENARIOS, strict=True), start=1
    ):
        if _exact(raw, {"order", "scenario_id", "replaces"}) != {
            "order": order,
            "scenario_id": scenario,
            "replaces": replaced,
        }:
            _fail("source_drift")
    records = _exact(root["records"], {"schema", "per_arm", "machine_vector", "arm_commitment_keys"})
    if (
        records["schema"] != OBSERVATION_SCHEMA
        or records["per_arm"] != [list(item) for item in RECORD_SPECS]
        or records["machine_vector"] != list(VECTOR_FIELDS)
        or records["arm_commitment_keys"]
        != ["workload_digest", "control_digest", "observer_config_digest", "trial_root_digest", "cohort"]
    ):
        _fail("source_drift")
    bounds = _exact(
        root["bounds"],
        {"fixture_bytes", "record_bytes", "corpus_bytes", "journal_bytes", "receipt_bytes", "max_unknowns", "max_metric"},
    )
    if bounds != {
        "fixture_bytes": MAX_FIXTURE_BYTES,
        "record_bytes": MAX_RECORD_BYTES,
        "corpus_bytes": MAX_CORPUS_BYTES,
        "journal_bytes": MAX_JOURNAL_BYTES,
        "receipt_bytes": MAX_RECEIPT_BYTES,
        "max_unknowns": MAX_UNKNOWNS,
        "max_metric": MAX_METRIC,
    }:
        _fail("source_drift")
    if root["stop_rules"] != {
        "positive_min_qualifying": 2,
        "negative_min_eligible": 3,
        "negative_q_plus_u_below": 2,
        "exhaustion_pairs": 3,
        "stop_at_first_decision": True,
    }:
        _fail("source_drift")
    if root["runtime_boundary"] != {
        "accepts": "sanitized_arm_records_only",
        "launch_subject": False,
        "shell": False,
        "network": False,
        "canonical_db_write": False,
        "real_target_mutation": False,
    }:
        _fail("source_drift")
    retention = _exact(
        root["retention"],
        {"raw_prompt", "raw_chat", "raw_reasoning", "raw_review", "raw_diff", "raw_stream", "raw_path", "raw_control", "retirement_owner", "retained_after_retirement"},
    )
    if (
        any(retention[key] is not False for key in retention if key.startswith("raw_"))
        or retention["retirement_owner"] != "TG-M20S.2"
        or retention["retained_after_retirement"] != "terminal_receipt_only"
    ):
        _fail("source_drift")
    return root


def _validate_plan(value: Any, protocol: Mapping[str, Any]) -> dict[str, Any]:
    root = _exact(
        value,
        {
            "schema",
            "authority_task_id",
            "authority_contract_revision",
            "authority_ref",
            "baseline_revision",
            "package_tree",
            "plans",
        },
    )
    if (
        root["schema"] != PLAN_SCHEMA
        or root["authority_task_id"] != AUTHORITY_TASK_ID
        or root["authority_contract_revision"] != AUTHORITY_CONTRACT_REVISION
        or root["authority_ref"] != AUTHORITY_REF
        or root["baseline_revision"] != BASELINE_REVISION
        or root["package_tree"] != PACKAGE_TREE
    ):
        _fail("source_drift")
    plans = root["plans"]
    if not isinstance(plans, list) or len(plans) != 3:
        _fail("source_drift")
    for order, (raw, replacement) in enumerate(zip(plans, protocol["replacements"], strict=True), start=1):
        item = _exact(raw, {"order", "scenario_id", "replacement_for", "boundaries", "episodes", "arms"})
        scenario = SCENARIOS[order - 1]
        if item["order"] != order or item["scenario_id"] != scenario or item["replacement_for"] != replacement["replaces"]:
            _fail("source_drift")
        boundaries = item["boundaries"]
        if boundaries != ["b00_start", "b10_transition", "b20_end"]:
            _fail("source_drift")
        episodes = item["episodes"]
        if not isinstance(episodes, list) or len(episodes) != 2:
            _fail("source_drift")
        episode_ids = []
        for index, episode in enumerate(episodes):
            parsed = _exact(episode, {"episode_id", "phase", "cause", "start_boundary", "end_boundary"})
            _string(parsed["episode_id"])
            if (
                parsed["phase"] not in {"implementation", "verification"}
                or parsed["cause"] not in {"user_expansion", "in_scope_discovery", "cross_module"}
                or parsed["start_boundary"] != boundaries[index]
                or parsed["end_boundary"] != boundaries[index + 1]
            ):
                _fail("source_drift")
            episode_ids.append(parsed["episode_id"])
        if len(set(episode_ids)) != 2:
            _fail("source_drift")
        arms = item["arms"]
        if not isinstance(arms, list) or len(arms) != 2:
            _fail("source_drift")
        for arm_index, raw_arm in enumerate(arms):
            arm = _exact(raw_arm, {"arm", "trial_id", "episode_task_slots"})
            expected_arm = ARMS[arm_index]
            if arm["arm"] != expected_arm or arm["trial_id"] != f"{scenario}.{expected_arm}.01":
                _fail("source_drift")
            slots = arm["episode_task_slots"]
            if not isinstance(slots, list) or [entry[0] for entry in slots] != episode_ids:
                _fail("source_drift")
            if any(not isinstance(entry, list) or len(entry) != 2 or _IDENTIFIER.fullmatch(entry[1]) is None for entry in slots):
                _fail("source_drift")
            if expected_arm == "broad" and len({entry[1] for entry in slots}) != 1:
                _fail("source_drift")
            if expected_arm == "bounded" and len({entry[1] for entry in slots}) != 2:
                _fail("source_drift")
    return root


def load_frozen_contract(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(repo_root).resolve(strict=True)
    protocol, _ = _load_json(_fixed_path(root, PROTOCOL_RELATIVE), MAX_FIXTURE_BYTES)
    protocol = _validate_protocol(protocol)
    if _sha256(protocol) != PROTOCOL_CANONICAL_SHA256:
        _fail("source_drift")
    plan, _ = _load_json(_fixed_path(root, PLAN_RELATIVE), MAX_FIXTURE_BYTES)
    plan = _validate_plan(plan, protocol)
    if _sha256(plan) != EPISODE_PLAN_CANONICAL_SHA256:
        _fail("source_drift")
    return protocol, plan


def _plan_for(plan: Mapping[str, Any], scenario_id: str) -> dict[str, Any]:
    for item in plan["plans"]:
        if item["scenario_id"] == scenario_id:
            return item
    _fail("unknown_scenario")


def _trial(plan: Mapping[str, Any], scenario_id: str, arm: str) -> str:
    item = _plan_for(plan, scenario_id)
    for arm_plan in item["arms"]:
        if arm_plan["arm"] == arm:
            return arm_plan["trial_id"]
    _fail("unknown_arm")


def observation_id(scenario_id: str, arm: str, trial_id: str, record_key: str) -> str:
    preimage = {
        "domain": "m20s-decomposition-observation-id-v1",
        **_identity(),
        "scenario_id": scenario_id,
        "arm": arm,
        "trial_id": trial_id,
        "record_key": record_key,
    }
    return "m20sobs_" + hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()


def _unknowns(value: Any) -> tuple[list[dict[str, str]], set[str]]:
    if not isinstance(value, list) or len(value) > MAX_UNKNOWNS:
        _fail("parse_failed")
    parsed = []
    for raw in value:
        item = _exact(raw, {"field", "reason"})
        field = _string(item["field"], _FIELD)
        reason = item["reason"]
        if reason not in UNKNOWN_REASONS:
            _fail("parse_failed")
        parsed.append({"field": field, "reason": reason})
    if parsed != sorted(parsed, key=lambda item: (item["field"], item["reason"])):
        _fail("parse_failed")
    if len({(item["field"], item["reason"]) for item in parsed}) != len(parsed):
        _fail("parse_failed")
    return parsed, {item["field"] for item in parsed}


def _measurement(payload: Any, episodes: Sequence[Mapping[str, Any]]) -> set[str]:
    root = _exact(payload, {"episodes"})
    raw_episodes = root["episodes"]
    if not isinstance(raw_episodes, list) or len(raw_episodes) != len(episodes):
        _fail("source_drift")
    nulls: set[str] = set()
    fields = (
        "files_before", "files_after", "modules_before", "modules_after",
        "lines_before", "lines_after", "contract_revision_before", "contract_revision_after",
        "review_generation_before", "review_generation_after", "governance_cycles", "review_cycles",
    )
    for index, (raw, expected) in enumerate(zip(raw_episodes, episodes, strict=True)):
        item = _exact(raw, {"episode_id", *fields})
        if item["episode_id"] != expected["episode_id"]:
            _fail("source_drift")
        for field in fields:
            if _nullable_integer(item[field]) is None:
                nulls.add(f"payload.episodes.{index}.{field}")
        for stem in ("contract_revision", "review_generation"):
            before, after = item[f"{stem}_before"], item[f"{stem}_after"]
            if before is not None and after is not None and after < before:
                _fail("source_drift")
    return nulls


def _attestation(payload: Any, episodes: Sequence[Mapping[str, Any]]) -> set[str]:
    root = _exact(
        payload,
        {"cohort", "workload_digest", "control_digest", "outcome", "reference_opens", "clarification_turns", "manual_inputs", "governance_invocations", "reviewer_invocations", "episodes"},
    )
    _string(root["cohort"])
    _string(root["workload_digest"], _HEX_64)
    _string(root["control_digest"], _HEX_64)
    if root["outcome"] not in OUTCOMES:
        _fail("parse_failed")
    caps = {
        "reference_opens": 256,
        "clarification_turns": 16,
        "manual_inputs": 32,
        "governance_invocations": 64,
        "reviewer_invocations": 8,
    }
    nulls = set()
    for field, maximum in caps.items():
        value = root[field]
        if value is None:
            nulls.add(f"payload.{field}")
        else:
            _integer(value, maximum)
    raw_episodes = root["episodes"]
    if not isinstance(raw_episodes, list) or len(raw_episodes) != len(episodes):
        _fail("source_drift")
    for index, (raw, expected) in enumerate(zip(raw_episodes, episodes, strict=True)):
        item = _exact(raw, {"episode_id", "phase", "cause", "current_response", *INDEPENDENCE_FIELDS})
        if (
            item["episode_id"] != expected["episode_id"]
            or item["phase"] != expected["phase"]
            or item["cause"] != expected["cause"]
            or item["current_response"] not in {"keep_current", "block", "handoff"}
        ):
            _fail("source_drift")
        for field in INDEPENDENCE_FIELDS:
            if item[field] not in {"yes", "no", "unknown"}:
                _fail("parse_failed")
            if item[field] == "unknown":
                nulls.add(f"payload.episodes.{index}.{field}")
    return nulls


def validate_observation(value: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    raw = canonical_json_bytes(value)
    if len(raw) > MAX_RECORD_BYTES:
        _fail("cap_exceeded")
    _privacy_check(raw)
    record = _exact(
        value,
        {"schema", *set(_identity()), "observation_id", "scenario_id", "arm", "trial_id", "record_key", "evidence_class", "eligibility", "unknown_reasons", "unknowns", "payload"},
    )
    if record["schema"] != OBSERVATION_SCHEMA or any(record[key] != expected for key, expected in _identity().items()):
        _fail("source_drift")
    scenario = record["scenario_id"]
    arm = record["arm"]
    if scenario not in SCENARIOS or arm not in ARMS:
        _fail("source_drift")
    trial_id = _trial(plan, scenario, arm)
    if record["trial_id"] != trial_id:
        _fail("source_drift")
    spec = next((item for item in RECORD_SPECS if item[0] == record["record_key"]), None)
    if spec is None or record["evidence_class"] != spec[1]:
        _fail("source_drift")
    if record["observation_id"] != observation_id(scenario, arm, trial_id, spec[0]):
        _fail("source_drift")
    eligibility = record["eligibility"]
    if eligibility not in ELIGIBILITY:
        _fail("parse_failed")
    unknowns, unknown_fields = _unknowns(record["unknowns"])
    reasons = record["unknown_reasons"]
    if not isinstance(reasons, list) or reasons != sorted(set(reasons)) or any(reason not in UNKNOWN_REASONS for reason in reasons):
        _fail("parse_failed")
    if eligibility == "excluded":
        if record["payload"] is not None or unknowns or not reasons or not set(reasons) & EXCLUDING_REASONS:
            _fail("source_drift")
        return record
    episodes = _plan_for(plan, scenario)["episodes"]
    nulls = (
        _measurement(record["payload"], episodes)
        if spec[0] == "split_measurement"
        else _attestation(record["payload"], episodes)
    )
    if nulls != unknown_fields or reasons != sorted({item["reason"] for item in unknowns}):
        _fail("source_drift")
    if eligibility == "eligible" and (unknowns or reasons):
        _fail("source_drift")
    if eligibility == "partial" and (
        not unknowns or set(reasons) & PAYLOAD_INVALIDATING_REASONS
    ):
        _fail("source_drift")
    return record


def excluded_observation(plan: Mapping[str, Any], scenario: str, arm: str, record_key: str, reason: str) -> dict[str, Any]:
    if reason not in EXCLUDING_REASONS:
        _fail("parse_failed")
    evidence = dict(RECORD_SPECS).get(record_key)
    if evidence is None:
        _fail("parse_failed")
    trial_id = _trial(plan, scenario, arm)
    return {
        "schema": OBSERVATION_SCHEMA,
        **_identity(),
        "observation_id": observation_id(scenario, arm, trial_id, record_key),
        "scenario_id": scenario,
        "arm": arm,
        "trial_id": trial_id,
        "record_key": record_key,
        "evidence_class": evidence,
        "eligibility": "excluded",
        "unknown_reasons": [reason],
        "unknowns": [],
        "payload": None,
    }


def _commitment(value: Any) -> dict[str, str]:
    item = _exact(value, {"workload_digest", "control_digest", "observer_config_digest", "trial_root_digest", "cohort"})
    for field in ("workload_digest", "control_digest", "observer_config_digest", "trial_root_digest"):
        _string(item[field], _HEX_64)
    _string(item["cohort"])
    return dict(item)


def _record_map(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {record["record_key"]: record for record in records}


def _vector(episode: Mapping[str, Any]) -> tuple[int, ...]:
    return (
        episode["files_after"] - episode["files_before"],
        episode["modules_after"] - episode["modules_before"],
        episode["lines_after"] - episode["lines_before"],
        episode["contract_revision_after"] - episode["contract_revision_before"],
        episode["review_generation_after"] - episode["review_generation_before"],
        episode["governance_cycles"],
        episode["review_cycles"],
    )


def _qualifies(arm_records: Mapping[str, Sequence[Mapping[str, Any]]]) -> bool:
    mapped = {arm: _record_map(records) for arm, records in arm_records.items()}
    broad_m = mapped["broad"]["split_measurement"]["payload"]["episodes"]
    bounded_m = mapped["bounded"]["split_measurement"]["payload"]["episodes"]
    broad_a = mapped["broad"]["attestation"]["payload"]["episodes"]
    bounded_a = mapped["bounded"]["attestation"]["payload"]["episodes"]
    if any(item[field] != "yes" for item in bounded_a for field in INDEPENDENCE_FIELDS):
        return False
    vector_improvement = any(
        any(broad_att[field] == "no" for field in INDEPENDENCE_FIELDS)
        and any(left > right for left, right in zip(_vector(broad_measure), _vector(bounded_measure), strict=True))
        for broad_att, broad_measure, bounded_measure in zip(broad_a, broad_m, bounded_m, strict=True)
    )
    broad_contract = sum(_vector(item)[3] for item in broad_m)
    bounded_contract = sum(_vector(item)[3] for item in bounded_m)
    broad_reviews = sum(_vector(item)[6] for item in broad_m)
    bounded_reviews = sum(_vector(item)[6] for item in bounded_m)
    return vector_improvement or bounded_contract <= broad_contract - 1 or bounded_reviews <= broad_reviews - 1


class M20SDecompositionHarness:
    """Fixed six-arm journal, reducer, decision, and no-rerun receipt."""

    def __init__(self, repo_root: Path = DEFAULT_REPO_ROOT, *, _allow_test_root: bool = False) -> None:
        root = Path(repo_root).resolve(strict=True)
        if not _allow_test_root and root != DEFAULT_REPO_ROOT.resolve(strict=True):
            _fail("repository_mismatch")
        self.root = root
        self.protocol, self.plan = load_frozen_contract(root)
        self.journal = _fixed_path(root, JOURNAL_RELATIVE)
        self.corpus = _fixed_path(root, CORPUS_RELATIVE)
        self.lock = _fixed_path(root, LOCK_RELATIVE)
        self.receipt = _fixed_path(root, RECEIPT_RELATIVE)

    def _prepare(self) -> None:
        self.journal = _fixed_path(self.root, JOURNAL_RELATIVE, create_parent=True)
        self.corpus = _fixed_path(self.root, CORPUS_RELATIVE, create_parent=True)
        self.lock = _fixed_path(self.root, LOCK_RELATIVE, create_parent=True)
        self.receipt = _fixed_path(self.root, RECEIPT_RELATIVE, create_parent=True)

    def _ensure_open(self) -> None:
        if os.path.lexists(self.receipt):
            _fail("collection_closed")

    def _empty(self) -> dict[str, Any]:
        return {"schema": JOURNAL_SCHEMA, **_identity(), "attempts": {}}

    def _read(self) -> dict[str, Any]:
        if not self.journal.exists():
            return self._empty()
        value, raw = _load_json(self.journal, MAX_JOURNAL_BYTES)
        if raw != canonical_json_bytes(value):
            _fail("source_drift")
        _privacy_check(raw)
        root = _exact(value, {"schema", *set(_identity()), "attempts"})
        if root["schema"] != JOURNAL_SCHEMA or any(root[key] != expected for key, expected in _identity().items()):
            _fail("source_drift")
        attempts = root["attempts"]
        if not isinstance(attempts, dict):
            _fail("source_drift")
        present_scenarios = []
        for scenario in SCENARIOS:
            trial_ids = {_trial(self.plan, scenario, arm) for arm in ARMS}
            present = trial_ids & set(attempts)
            if present and present != trial_ids:
                _fail("source_drift")
            if present:
                present_scenarios.append(scenario)
        if present_scenarios != list(SCENARIOS[: len(present_scenarios)]) or set(attempts) != {
            _trial(self.plan, scenario, arm) for scenario in present_scenarios for arm in ARMS
        }:
            _fail("source_drift")
        for scenario in present_scenarios:
            for arm in ARMS:
                trial_id = _trial(self.plan, scenario, arm)
                state = _exact(attempts[trial_id], {"status", "commitment", "records"})
                commitment = _commitment(state["commitment"])
                records = state["records"]
                if state["status"] in {"started", "reducing"}:
                    if records != []:
                        _fail("source_drift")
                elif state["status"] == "finished":
                    if not isinstance(records, list) or len(records) != 2:
                        _fail("source_drift")
                    validated = [validate_observation(record, plan=self.plan) for record in records]
                    if [(item["record_key"], item["evidence_class"]) for item in validated] != list(RECORD_SPECS):
                        _fail("source_drift")
                    if any(item["trial_id"] != trial_id for item in validated):
                        _fail("source_drift")
                    attestation = validated[1]
                    if attestation["payload"] is not None and (
                        attestation["payload"]["workload_digest"] != commitment["workload_digest"]
                        or attestation["payload"]["control_digest"] != commitment["control_digest"]
                        or attestation["payload"]["cohort"] != commitment["cohort"]
                    ):
                        _fail("source_drift")
                else:
                    _fail("source_drift")
        self._reject_post_terminal(attempts, len(present_scenarios))
        return root

    def _write(self, journal: Mapping[str, Any]) -> None:
        raw = canonical_json_bytes(journal)
        if len(raw) > MAX_JOURNAL_BYTES:
            _fail("cap_exceeded")
        _privacy_check(raw)
        _atomic_write(self.journal, raw, replace=self.journal.exists())

    def _pair(self, attempts: Mapping[str, Any], scenario: str) -> tuple[bool, bool] | None:
        states = [attempts.get(_trial(self.plan, scenario, arm)) for arm in ARMS]
        if any(state is None or state["status"] != "finished" for state in states):
            return None
        arm_records = {arm: states[index]["records"] for index, arm in enumerate(ARMS)}
        records = [record for arm in ARMS for record in arm_records[arm]]
        eligible = all(record["eligibility"] == "eligible" for record in records)
        if not eligible:
            return False, False
        mapped = {arm: _record_map(arm_records[arm]) for arm in ARMS}
        attestations = [mapped[arm]["attestation"]["payload"] for arm in ARMS]
        episode_sets = [
            tuple(item["episode_id"] for item in mapped[arm][key]["payload"]["episodes"])
            for arm in ARMS
            for key, _evidence in RECORD_SPECS
        ]
        if (
            attestations[0]["cohort"] != attestations[1]["cohort"]
            or attestations[0]["workload_digest"] != attestations[1]["workload_digest"]
            or len(set(episode_sets)) != 1
        ):
            return False, False
        return True, _qualifies(arm_records)

    def _decision(self, journal: Mapping[str, Any]) -> dict[str, Any]:
        attempts = journal["attempts"]
        evaluated = []
        for scenario in SCENARIOS:
            pair = self._pair(attempts, scenario)
            if pair is None:
                break
            evaluated.append(pair)
        eligible = 1 + sum(pair[0] for pair in evaluated)
        qualifying = 1 + sum(pair[1] for pair in evaluated)
        unavailable = 4 - eligible
        in_flight = sum(state["status"] != "finished" for state in attempts.values())
        if qualifying >= 2:
            decision = "proceed_to_design"
        elif eligible >= 3 and qualifying + unavailable < 2:
            decision = "no_follow_up"
        elif len(evaluated) == 3:
            decision = "observe_more"
        else:
            decision = None
        return {
            "decision": decision,
            "attempted_pairs": len(evaluated),
            "attempted_arms": len(attempts),
            "eligible_pairs": eligible,
            "qualifying_pairs": qualifying,
            "unavailable_pairs": unavailable,
            "in_flight_arms": in_flight,
        }

    def _reject_post_terminal(
        self, attempts: Mapping[str, Any], attempted_pairs: int
    ) -> None:
        for prefix_length in range(1, attempted_pairs):
            prefix_trials = {
                _trial(self.plan, scenario, arm)
                for scenario in SCENARIOS[:prefix_length]
                for arm in ARMS
            }
            prefix_attempts = {
                trial_id: attempt
                for trial_id, attempt in attempts.items()
                if trial_id in prefix_trials
            }
            if self._decision({"attempts": prefix_attempts})["decision"] is not None:
                _fail("source_drift")

    def status(self) -> dict[str, Any]:
        if self.receipt.exists():
            receipt, raw = _load_json(self.receipt, MAX_RECEIPT_BYTES)
            if raw != canonical_json_bytes(receipt):
                _fail("source_drift")
            _privacy_check(raw)
            receipt = _exact(
                receipt,
                {
                    "schema", "unit", *set(_identity()), "status",
                    "artifact_status", "retirement_revision", "attempted_pairs",
                    "attempted_arms", "record_count", "corpus_bytes",
                    "corpus_sha256", "eligible_pairs", "qualifying_pairs",
                    "unavailable_pairs", "decision",
                },
            )
            if (
                receipt["schema"] != RECEIPT_SCHEMA
                or receipt["unit"] != UNIT
                or any(receipt[key] != expected for key, expected in _identity().items())
                or receipt["status"] != "closed"
                or receipt["artifact_status"] != "retained"
                or receipt["retirement_revision"] is not None
            ):
                _fail("source_drift")
            attempted_pairs = _integer(receipt["attempted_pairs"], 3)
            if attempted_pairs < 1:
                _fail("source_drift")
            for field in (
                "attempted_arms", "record_count", "corpus_bytes", "eligible_pairs",
                "qualifying_pairs", "unavailable_pairs",
            ):
                _integer(receipt[field])
            _string(receipt["corpus_sha256"], _HEX_64)
            if receipt["decision"] not in {
                "proceed_to_design",
                "no_follow_up",
                "observe_more",
            }:
                _fail("source_drift")
            corpus, corpus_raw = _load_json(self.corpus, MAX_CORPUS_BYTES)
            if corpus_raw != canonical_json_bytes(corpus):
                _fail("source_drift")
            _privacy_check(corpus_raw)
            if not isinstance(corpus, list):
                _fail("source_drift")
            records = [validate_observation(item, plan=self.plan) for item in corpus]
            if (
                len(records) != attempted_pairs * len(ARMS) * len(RECORD_SPECS)
                or len({item["observation_id"] for item in records}) != len(records)
                or any(
                    item["scenario_id"] not in SCENARIOS[:attempted_pairs]
                    for item in records
                )
                or self.journal.exists()
            ):
                _fail("source_drift")
            if [item["observation_id"] for item in records] != sorted(
                item["observation_id"] for item in records
            ):
                _fail("source_drift")
            attempts: dict[str, Any] = {}
            for scenario in SCENARIOS[:attempted_pairs]:
                for arm in ARMS:
                    arm_records = [
                        item
                        for item in records
                        if item["scenario_id"] == scenario and item["arm"] == arm
                    ]
                    if sorted(item["record_key"] for item in arm_records) != [
                        "attestation", "split_measurement"
                    ]:
                        _fail("source_drift")
                    arm_records.sort(
                        key=lambda item: tuple(key for key, _evidence in RECORD_SPECS).index(
                            item["record_key"]
                        )
                    )
                    attempts[_trial(self.plan, scenario, arm)] = {
                        "status": "finished",
                        "records": arm_records,
                    }
            decision = self._decision({"attempts": attempts})
            self._reject_post_terminal(attempts, attempted_pairs)
            expected = {
                "attempted_pairs": decision["attempted_pairs"],
                "attempted_arms": decision["attempted_arms"],
                "record_count": len(records),
                "corpus_bytes": len(corpus_raw),
                "corpus_sha256": hashlib.sha256(corpus_raw).hexdigest(),
                "eligible_pairs": decision["eligible_pairs"],
                "qualifying_pairs": decision["qualifying_pairs"],
                "unavailable_pairs": decision["unavailable_pairs"],
                "decision": decision["decision"],
            }
            if any(receipt[key] != value for key, value in expected.items()):
                _fail("source_drift")
            return {"artifact_status": "closed", "receipt": receipt}
        return {"artifact_status": "open", **self._decision(self._read())}

    def start_pair(self, scenario: str, commitments: Mapping[str, Any]) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            _fail("unknown_scenario")
        self._ensure_open()
        self._prepare()
        with _exclusive_lock(self.lock):
            journal = self._read()
            state = self._decision(journal)
            if state["decision"] is not None:
                _fail("collection_stopped")
            if state["in_flight_arms"]:
                _fail("attempt_in_flight")
            expected = SCENARIOS[state["attempted_pairs"]]
            if scenario != expected:
                _fail("scenario_order_required")
            raw_commitments = _exact(commitments, set(ARMS))
            parsed = {arm: _commitment(raw_commitments[arm]) for arm in ARMS}
            used_trial_roots = {
                attempt["commitment"]["trial_root_digest"]
                for attempt in journal["attempts"].values()
            }
            if (
                parsed["broad"]["workload_digest"] != parsed["bounded"]["workload_digest"]
                or parsed["broad"]["cohort"] != parsed["bounded"]["cohort"]
                or parsed["broad"]["trial_root_digest"] == parsed["bounded"]["trial_root_digest"]
                or any(
                    parsed[arm]["trial_root_digest"] in used_trial_roots
                    for arm in ARMS
                )
            ):
                _fail("source_drift")
            for arm in ARMS:
                journal["attempts"][_trial(self.plan, scenario, arm)] = {
                    "status": "started",
                    "commitment": parsed[arm],
                    "records": [],
                }
            self._write(journal)
            return {"scenario_id": scenario, "started_arms": 2}

    def reduce_arm(self, scenario: str, arm: str, candidates: Any) -> dict[str, Any]:
        if scenario not in SCENARIOS or arm not in ARMS:
            _fail("unknown_trial")
        self._ensure_open()
        self._prepare()
        with _exclusive_lock(self.lock):
            journal = self._read()
            trial_id = _trial(self.plan, scenario, arm)
            state = journal["attempts"].get(trial_id)
            if state is None or state["status"] != "started":
                _fail("attempt_not_started")
            state["status"] = "reducing"
            self._write(journal)
        try:
            if not isinstance(candidates, list) or len(candidates) != 2:
                _fail("parse_failed")
            records = [validate_observation(candidate, plan=self.plan) for candidate in candidates]
            if [(item["record_key"], item["evidence_class"]) for item in records] != list(RECORD_SPECS):
                _fail("source_drift")
            commitment = state["commitment"]
            if any(item["trial_id"] != trial_id for item in records):
                _fail("source_drift")
            attestation = records[1]
            if attestation["payload"] is not None and (
                attestation["payload"]["workload_digest"] != commitment["workload_digest"]
                or attestation["payload"]["control_digest"] != commitment["control_digest"]
                or attestation["payload"]["cohort"] != commitment["cohort"]
            ):
                _fail("source_drift")
            accepted, reason = True, None
        except M20SObservationError as error:
            reason = error.code if error.code in EXCLUDING_REASONS else "contaminated"
            records = [excluded_observation(self.plan, scenario, arm, key, reason) for key, _ in RECORD_SPECS]
            accepted = False
        with _exclusive_lock(self.lock):
            journal = self._read()
            target = journal["attempts"].get(trial_id)
            if target is None or target["status"] != "reducing":
                _fail("source_drift")
            target["status"] = "finished"
            target["records"] = records
            self._write(journal)
            return {"accepted": accepted, "reason": reason, **self._decision(journal)}

    def terminalize_arm(self, scenario: str, arm: str, reason: str) -> dict[str, Any]:
        if reason not in EXCLUDING_REASONS:
            _fail("parse_failed")
        self._ensure_open()
        self._prepare()
        with _exclusive_lock(self.lock):
            journal = self._read()
            trial_id = _trial(self.plan, scenario, arm)
            state = journal["attempts"].get(trial_id)
            if state is None or state["status"] not in {"started", "reducing"}:
                _fail("attempt_not_started")
            state["status"] = "finished"
            state["records"] = [excluded_observation(self.plan, scenario, arm, key, reason) for key, _ in RECORD_SPECS]
            self._write(journal)
            return self._decision(journal)

    def finalize(self) -> dict[str, Any]:
        self._ensure_open()
        self._prepare()
        with _exclusive_lock(self.lock):
            if self.corpus.exists():
                _fail("artifact_exists")
            journal = self._read()
            state = self._decision(journal)
            if state["decision"] is None or state["in_flight_arms"]:
                _fail("collection_incomplete")
            records = []
            for scenario in SCENARIOS[: state["attempted_pairs"]]:
                for arm in ARMS:
                    records.extend(journal["attempts"][_trial(self.plan, scenario, arm)]["records"])
            records.sort(key=lambda item: item["observation_id"].encode("ascii"))
            corpus = canonical_json_bytes(records)
            if len(corpus) > MAX_CORPUS_BYTES:
                _fail("cap_exceeded")
            _privacy_check(corpus)
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "unit": UNIT,
                **_identity(),
                "status": "closed",
                "artifact_status": "retained",
                "retirement_revision": None,
                "attempted_pairs": state["attempted_pairs"],
                "attempted_arms": state["attempted_arms"],
                "record_count": len(records),
                "corpus_bytes": len(corpus),
                "corpus_sha256": hashlib.sha256(corpus).hexdigest(),
                "eligible_pairs": state["eligible_pairs"],
                "qualifying_pairs": state["qualifying_pairs"],
                "unavailable_pairs": state["unavailable_pairs"],
                "decision": state["decision"],
            }
            receipt_bytes = canonical_json_bytes(receipt)
            if len(receipt_bytes) > MAX_RECEIPT_BYTES:
                _fail("cap_exceeded")
            _atomic_write(self.corpus, corpus)
            _atomic_write(self.receipt, receipt_bytes)
            try:
                self.journal.unlink()
            except OSError:
                _fail("artifact_delete_failed")
            return receipt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the temporary TG-M20S.1 harness.")
    parser.add_argument("--check", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _build_parser().parse_args(argv)
    try:
        result = M20SDecompositionHarness().status()
    except M20SObservationError as error:
        print(canonical_json_bytes({"ok": False, "error_code": error.code}).decode("utf-8"), file=sys.stderr)
        return 2
    print(canonical_json_bytes({"ok": True, **result}).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUTHORITY_CONTRACT_REVISION",
    "AUTHORITY_REF",
    "AUTHORITY_TASK_ID",
    "BASELINE_REVISION",
    "EPISODE_PLAN_CANONICAL_SHA256",
    "M20SDecompositionHarness",
    "M20SObservationError",
    "PACKAGE_TREE",
    "PROTOCOL_CANONICAL_SHA256",
    "SCENARIOS",
    "canonical_json_bytes",
    "excluded_observation",
    "load_frozen_contract",
    "observation_id",
    "validate_observation",
]
