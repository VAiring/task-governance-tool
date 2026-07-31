"""Repository-only operational observation harness for TG-M20.

This module is development tooling.  It deliberately does not enter the
installable skill package or expose a taskgov command.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence


sys.dont_write_bytecode = True

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(DEFAULT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_REPO_ROOT))
_RUNTIME_SCRIPTS = DEFAULT_REPO_ROOT / "task-governance-tool" / "scripts"
if str(_RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SCRIPTS))

from task_governance_tool.cli import build_parser as _build_taskgov_parser  # noqa: E402
from task_governance_tool.tasks import STATUSES as _PRODUCT_TASK_STATUSES  # noqa: E402
from tools.release_contract import parser_leaf_commands  # noqa: E402

PROTOCOL_RELATIVE_PATH = Path("fixtures") / "m20" / "protocol-v1.json"
M20_4_EPISODE_PLAN_RELATIVE_PATH = (
    Path("fixtures") / "m20" / "m20.4-episode-plan-v1.json"
)
OBSERVATION_SCHEMA = "m20-operational-observation-v1"
CORPUS_FAILURE_SCHEMA = "m20-operational-corpus-failure-v1"
JOURNAL_SCHEMA = "m20-attempt-journal-v1"
COLLECTION_RECEIPT_SCHEMA = "m20-collection-receipt-v1"
M20_2_RECEIPT_SHA256 = (
    "9a00ae42ca8bbb557afa9fbb418d7e51d2bf3d7162cfe4157e4f9674af57c2e7"
)
PROTOCOL_CANONICAL_SHA256 = (
    "e43c315897f952607c703660a0d629c7a739cf1b1a17b6080a25f1b8f896a6ae"
)
M20_4_EPISODE_PLAN_RAW_SHA256 = (
    "a8cfb63451d1fcaceaf4da3251bc654d5c31a6e3aa99ee37d994688d32697512"
)
M20_4_EPISODE_PLAN_CANONICAL_SHA256 = (
    "78460bc2036f43ed44cb2612be3db6a0815bbe1a1e0747f28806def6db11dda4"
)
MAX_STREAM_BYTES = 1_048_576
TASKGOV_TIMEOUT_SECONDS = 300
MAX_SIGNED_32 = 2_147_483_647
MAX_SIGNED_64 = 9_223_372_036_854_775_807

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9._-]{1,64}\Z")
_UNKNOWN_FIELD = re.compile(r"[a-z][a-z0-9_.]{0,95}\Z")
_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_TASK_ID = re.compile(r"tg_task_[0-9a-f]{16}\Z")
_OBSERVATION_ID = re.compile(r"m20obs_[0-9a-f]{64}\Z")
_SAFE_SELECTOR = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")

_UNKNOWN_REASONS = frozenset(
    {
        "not_observable",
        "not_reconstructable",
        "source_missing",
        "source_drift",
        "parse_failed",
        "timeout",
        "cap_exceeded",
        "observer_uncertain",
        "contaminated",
    }
)
_INVALIDATING_REASONS = frozenset(
    {"source_missing", "source_drift", "parse_failed", "contaminated"}
)
_PHASES = frozenset(
    {
        "setup",
        "rediscover",
        "select",
        "activate",
        "execute",
        "verify",
        "review",
        "complete",
        "diagnose",
    }
)
_COMMAND_LEAVES = frozenset(
    command.replace(" ", ".")
    for command in parser_leaf_commands(_build_taskgov_parser())
)
_TASK_STATUSES = frozenset(_PRODUCT_TASK_STATUSES)
_STATE_KEYS = (
    "task_status",
    "contract_revision",
    "review_generation",
    "receipts_current",
    "qualifying_passes",
    "changes_requested_current",
    "findings_open_high",
    "findings_open_medium",
    "findings_open_low",
    "handoffs_pending",
    "handoffs_delivered",
    "handoffs_withdrawn",
    "completion_cycles",
    "verification_attestation",
    "verification_detail",
)
_RETROSPECTIVE_METRICS = (
    "completion_cycles",
    "reopens",
    "contract_revisions",
    "review_receipts",
    "changes_requested_receipts",
    "findings_open_high",
    "findings_open_medium",
    "findings_open_low",
    "handoffs_pending",
    "handoffs_delivered",
    "handoffs_withdrawn",
    "git_wall_clock_span_ms",
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "contract_id",
        "contract_revision",
        "baseline_revision",
        "authority_revision",
        "observation_id",
        "scenario_id",
        "trial_id",
        "record_key",
        "unit",
        "evidence_class",
        "channel",
        "eligibility",
        "unknown_reasons",
        "unknowns",
        "payload",
    }
)


class M20ObservationError(Exception):
    """Sanitized fail-closed error with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise M20ObservationError(code)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("parse_failed")
        result[key] = value
    return result


def _reject_noncanonical(value: Any) -> None:
    if isinstance(value, float):
        _fail("canonicalization_failed")
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _fail("canonicalization_failed")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, list) or isinstance(value, tuple):
        for item in value:
            _reject_noncanonical(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("canonicalization_failed")
            _reject_noncanonical(key)
            _reject_noncanonical(item)
        return
    _fail("canonicalization_failed")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one canonical UTF-8 representation used by M20."""

    _reject_noncanonical(value)
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8", errors="strict")
    except (TypeError, UnicodeError, ValueError):
        _fail("canonicalization_failed")


def _parse_json_integer(value: str) -> int:
    if len(value.lstrip("-")) > 19:
        _fail("parse_failed")
    try:
        return int(value)
    except ValueError:
        _fail("parse_failed")


def _load_json_bytes(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_int=_parse_json_integer,
            parse_float=lambda _value: _fail("parse_failed"),
            parse_constant=lambda _value: _fail("parse_failed"),
        )
    except M20ObservationError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        _fail("parse_failed")


def _exact_keys(value: Any, expected: Iterable[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != set(expected):
        _fail("parse_failed")
    return value


def _integer(value: Any, *, minimum: int = 0, maximum: int = MAX_SIGNED_64) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("parse_failed")
    return value


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        _fail("parse_failed")
    return value


def _string(
    value: Any,
    *,
    pattern: re.Pattern[str] | None = None,
    minimum: int = 0,
    maximum: int | None = None,
) -> str:
    if not isinstance(value, str):
        _fail("parse_failed")
    if not minimum <= len(value) or maximum is not None and len(value) > maximum:
        _fail("parse_failed")
    if pattern is not None and pattern.fullmatch(value) is None:
        _fail("parse_failed")
    _reject_noncanonical(value)
    return value


def _sorted_unique_strings(
    value: Any,
    *,
    pattern: re.Pattern[str],
    maximum_items: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        _fail("parse_failed")
    normalized = tuple(_string(item, pattern=pattern) for item in value)
    if normalized != tuple(sorted(set(normalized), key=lambda item: item.encode("ascii"))):
        _fail("parse_failed")
    return normalized


def _protocol_path(repo_root: Path) -> Path:
    root = Path(repo_root).resolve(strict=True)
    path = (root / PROTOCOL_RELATIVE_PATH).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError:
        _fail("source_drift")
    return path


def load_protocol(repo_root: Path = DEFAULT_REPO_ROOT) -> dict[str, Any]:
    """Load and validate the tracked repository protocol."""

    try:
        raw = _protocol_path(repo_root).read_bytes()
    except OSError:
        _fail("source_missing")
    if not raw or len(raw) > 262_144:
        _fail("source_drift")
    value = _load_json_bytes(raw)
    if hashlib.sha256(canonical_json_bytes(value)).hexdigest() != (
        PROTOCOL_CANONICAL_SHA256
    ):
        _fail("source_drift")
    protocol = _exact_keys(
        value,
        {
            "schema",
            "authority",
            "canonical_json",
            "bounds",
            "failure_policy",
            "m20_2",
            "inventory",
            "retrospective_metrics",
            "control_bundle",
            "reducer_manifest",
        },
    )
    if protocol["schema"] != "m20-repository-protocol-v1":
        _fail("source_drift")
    authority = _exact_keys(
        protocol["authority"],
        {
            "contract_id",
            "contract_revision",
            "baseline_revision",
            "authority_revision",
        },
    )
    if (
        authority["contract_id"] != "TG-M20-OPERATIONAL-BASELINE"
        or authority["contract_revision"] != 1
        or _HEX_40.fullmatch(str(authority["baseline_revision"])) is None
        or _HEX_40.fullmatch(str(authority["authority_revision"])) is None
    ):
        _fail("source_drift")
    canonical = _exact_keys(
        protocol["canonical_json"],
        {
            "encoding",
            "ensure_ascii",
            "sort_keys",
            "separators",
            "trailing_newline",
            "surrogates",
        },
    )
    if canonical != {
        "encoding": "utf-8",
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": [",", ":"],
        "trailing_newline": False,
        "surrogates": "reject",
    }:
        _fail("source_drift")
    failure_policy = _exact_keys(
        protocol["failure_policy"],
        {
            "non_cap_invalid",
            "invalid_state_read",
            "legacy_history_incomplete",
            "replay",
            "started_attempt",
        },
    )
    if failure_policy != {
        "non_cap_invalid": "fail_without_corpus",
        "invalid_state_read": "exclude_attempt",
        "legacy_history_incomplete": "partial_completion_cycles_and_reopens",
        "replay": "reduction_only",
        "started_attempt": "never_rerun",
    }:
        _fail("source_drift")
    metrics = tuple(protocol["retrospective_metrics"])
    if metrics != _RETROSPECTIVE_METRICS:
        _fail("source_drift")
    counts = protocol.get("bounds", {}).get("unit_record_counts", {})
    if counts != {"M20.2": 46, "M20.3": 9, "M20.4": 19}:
        _fail("source_drift")
    for unit, expected in counts.items():
        if len(derive_inventory(protocol, unit)) != expected:
            _fail("source_drift")
    _validate_scenario_protocol(protocol)
    _validate_reducer_protocol(protocol)
    return dict(protocol)


def load_m20_4_episode_plan(
    protocol: Mapping[str, Any],
    repo_root: Path = DEFAULT_REPO_ROOT,
) -> dict[str, Any]:
    """Load the versioned M20.4-only supplement without changing M20.2 provenance."""

    root = Path(repo_root).resolve(strict=True)
    lexical = root / M20_4_EPISODE_PLAN_RELATIVE_PATH
    if lexical.is_symlink():
        _fail("source_drift")
    try:
        path = lexical.resolve(strict=True)
        path.relative_to(root)
        raw = path.read_bytes()
    except (OSError, ValueError):
        _fail("source_missing")
    if (
        not raw
        or len(raw) > 32_768
        or hashlib.sha256(raw).hexdigest() != M20_4_EPISODE_PLAN_RAW_SHA256
    ):
        _fail("source_drift")
    value = _load_json_bytes(raw)
    if hashlib.sha256(canonical_json_bytes(value)).hexdigest() != (
        M20_4_EPISODE_PLAN_CANONICAL_SHA256
    ):
        _fail("source_drift")
    plan = _exact_keys(
        value,
        {
            "schema",
            "unit",
            "contract_id",
            "contract_revision",
            "baseline_revision",
            "authority_revision",
            "base_protocol_sha256",
            "plans",
        },
    )
    authority = protocol["authority"]
    if (
        plan["schema"] != "m20.4-episode-plan-v1"
        or plan["unit"] != "M20.4"
        or plan["contract_id"] != authority["contract_id"]
        or plan["contract_revision"] != authority["contract_revision"]
        or plan["baseline_revision"] != authority["baseline_revision"]
        or plan["authority_revision"] != authority["authority_revision"]
        or plan["base_protocol_sha256"] != PROTOCOL_CANONICAL_SHA256
    ):
        _fail("source_drift")
    raw_plans = plan["plans"]
    if not isinstance(raw_plans, list) or len(raw_plans) != 9:
        _fail("source_drift")
    expected_attempts = {
        (row[1], row[2])
        for row in derive_inventory(protocol, "M20.4")
        if row[4] == "trial_measurement"
    }
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_plan in raw_plans:
        item = _exact_keys(
            raw_plan,
            {
                "scenario_id",
                "arm",
                "trial_id",
                "task_slots",
                "boundaries",
                "episodes",
            },
        )
        scenario_id = _string(item["scenario_id"], pattern=_IDENTIFIER)
        arm = _string(item["arm"], pattern=_IDENTIFIER)
        trial_id = _string(item["trial_id"], pattern=_IDENTIFIER)
        if trial_id != f"{scenario_id}.{arm}.01":
            _fail("source_drift")
        key = (scenario_id, trial_id)
        if key not in expected_attempts or key in indexed:
            _fail("source_drift")
        task_slots_raw = item["task_slots"]
        if not isinstance(task_slots_raw, list) or not 1 <= len(task_slots_raw) <= 8:
            _fail("parse_failed")
        task_slots = tuple(
            _string(value, pattern=_IDENTIFIER) for value in task_slots_raw
        )
        if len(task_slots) != len(set(task_slots)):
            _fail("source_drift")
        boundaries_raw = item["boundaries"]
        if not isinstance(boundaries_raw, list) or not 2 <= len(boundaries_raw) <= 9:
            _fail("parse_failed")
        boundaries = tuple(
            _string(value, pattern=_IDENTIFIER) for value in boundaries_raw
        )
        if len(boundaries) != len(set(boundaries)):
            _fail("source_drift")
        boundary_index = {
            boundary: index for index, boundary in enumerate(boundaries)
        }
        episodes_raw = item["episodes"]
        if not isinstance(episodes_raw, list) or not 1 <= len(episodes_raw) <= 8:
            _fail("parse_failed")
        episode_ids: list[str] = []
        used_slots: set[str] = set()
        used_boundaries: set[str] = set()
        previous_end = -1
        for raw_episode in episodes_raw:
            episode = _exact_keys(
                raw_episode,
                {
                    "episode_id",
                    "task_slot",
                    "start_boundary",
                    "end_boundary",
                },
            )
            episode_id = _string(episode["episode_id"], pattern=_IDENTIFIER)
            task_slot = _string(episode["task_slot"], pattern=_IDENTIFIER)
            start = _string(episode["start_boundary"], pattern=_IDENTIFIER)
            end = _string(episode["end_boundary"], pattern=_IDENTIFIER)
            if (
                task_slot not in task_slots
                or start not in boundary_index
                or end not in boundary_index
                or boundary_index[start] >= boundary_index[end]
                or boundary_index[start] < previous_end
            ):
                _fail("source_drift")
            previous_end = boundary_index[end]
            episode_ids.append(episode_id)
            used_slots.add(task_slot)
            used_boundaries.update((start, end))
        if (
            len(episode_ids) != len(set(episode_ids))
            or used_slots != set(task_slots)
            or used_boundaries != set(boundaries)
        ):
            _fail("source_drift")
        indexed[key] = dict(item)
    if set(indexed) != expected_attempts:
        _fail("source_drift")
    paired_scenarios = {
        scenario_id
        for scenario_id, _trial_id in expected_attempts
        if scenario_id != "sp_handoff_control"
    }
    for scenario_id in paired_scenarios:
        broad = indexed[(scenario_id, f"{scenario_id}.broad.01")]
        bounded = indexed[(scenario_id, f"{scenario_id}.bounded.01")]
        broad_ids = tuple(
            episode["episode_id"] for episode in broad["episodes"]
        )
        bounded_ids = tuple(
            episode["episode_id"] for episode in bounded["episodes"]
        )
        if broad_ids != bounded_ids:
            _fail("source_drift")
    control = indexed[
        ("sp_handoff_control", "sp_handoff_control.broad.01")
    ]
    if len(control["episodes"]) != 1:
        _fail("source_drift")
    return dict(plan)


def derive_inventory(
    protocol: Mapping[str, Any],
    unit: str,
) -> tuple[tuple[Any, ...], ...]:
    """Return the fixed six-field inventory, including derived M19 metrics."""

    if unit not in {"M20.2", "M20.3", "M20.4"}:
        _fail("parse_failed")
    rows: list[tuple[Any, ...]] = []
    inventory = protocol.get("inventory")
    if not isinstance(inventory, list):
        _fail("parse_failed")
    for raw in inventory:
        if not isinstance(raw, list) or len(raw) != 6:
            _fail("parse_failed")
        row = tuple(raw)
        if row[0] == unit:
            rows.append(row)
    if unit == "M20.2":
        m20_2 = protocol.get("m20_2")
        if not isinstance(m20_2, dict):
            _fail("parse_failed")
        metrics = protocol.get("retrospective_metrics")
        cohorts = m20_2.get("retrospective_cohorts")
        if not isinstance(metrics, list) or not isinstance(cohorts, list):
            _fail("parse_failed")
        for cohort in cohorts:
            scenario = cohort.get("scenario_id") if isinstance(cohort, dict) else None
            _string(scenario, pattern=_IDENTIFIER)
            for metric in metrics:
                _string(metric, pattern=_IDENTIFIER)
                rows.append(
                    (
                        "M20.2",
                        scenario,
                        None,
                        "historically_reconstructed",
                        "task_git_reconstruction",
                        metric,
                    )
                )
    normalized = tuple(rows)
    if len(normalized) != len(set(normalized)):
        _fail("source_drift")
    return normalized


def observation_id(
    protocol: Mapping[str, Any],
    inventory_row: Sequence[Any],
) -> str:
    """Derive an observation ID from the exact frozen NUL-delimited preimage."""

    if len(inventory_row) != 6:
        _fail("parse_failed")
    unit, scenario_id, trial_id, evidence_class, channel, record_key = inventory_row
    authority = protocol.get("authority")
    if not isinstance(authority, dict):
        _fail("parse_failed")
    values = (
        "m20-observation-v1",
        authority.get("contract_id"),
        str(authority.get("contract_revision")),
        authority.get("baseline_revision"),
        authority.get("authority_revision"),
        unit,
        scenario_id,
        trial_id or "",
        evidence_class,
        channel,
        record_key,
    )
    if any(not isinstance(value, str) or not value.isascii() for value in values):
        _fail("parse_failed")
    preimage = ("\0".join(values) + "\0").encode("ascii")
    return "m20obs_" + hashlib.sha256(preimage).hexdigest()


def _validate_scenario_protocol(protocol: Mapping[str, Any]) -> None:
    m20_2 = _exact_keys(
        protocol.get("m20_2"),
        {
            "corpus_path",
            "journal_path",
            "lock_path",
            "receipt_path",
            "receipt_lifecycle",
            "harness_scenarios",
            "retrospective_cohorts",
        },
    )
    if (
        m20_2["corpus_path"] != "dist/m20/m20.2-observations.json"
        or m20_2["journal_path"] != "dist/m20/m20.2-attempt-journal.json"
        or m20_2["lock_path"] != "dist/m20/m20.2-collector.lock"
        or m20_2["receipt_path"]
        != "fixtures/m20/m20.2-collection-receipt.json"
        or m20_2["receipt_lifecycle"]
        != "retain_closed_tombstone_after_m20_5"
    ):
        _fail("source_drift")
    scenarios = m20_2["harness_scenarios"]
    if not isinstance(scenarios, list) or len(scenarios) != 5:
        _fail("source_drift")
    expected_scenarios = (
        "gov_tier1_commitless",
        "gov_tier2_snapshot",
        "gov_pause_resume",
        "gov_handoff_continue",
        "gov_reopen_rereview",
    )
    if tuple(item.get("scenario_id") for item in scenarios) != expected_scenarios:
        _fail("source_drift")
    for item in scenarios:
        _exact_keys(
            item,
            {"scenario_id", "arm", "trial_id", "operations", "before", "after"},
        )
        if item["arm"] != "harness":
            _fail("source_drift")
        if item["trial_id"] != f"{item['scenario_id']}.harness.01":
            _fail("source_drift")
        operations = item["operations"]
        if not isinstance(operations, list) or not 1 <= len(operations) <= 32:
            _fail("source_drift")
        for operation in operations:
            if (
                not isinstance(operation, list)
                or len(operation) != 2
                or operation[0] not in _PHASES
                or operation[1] not in _COMMAND_LEAVES
            ):
                _fail("source_drift")
    cohorts = m20_2["retrospective_cohorts"]
    if not isinstance(cohorts, list) or len(cohorts) != 3:
        _fail("source_drift")
    expected_cohorts = (
        "m19_preparation_reconstruction",
        "m19_publication_reconstruction",
        "m19_postrelease_reconstruction",
    )
    if tuple(item.get("scenario_id") for item in cohorts) != expected_cohorts:
        _fail("source_drift")
    for item in cohorts:
        _exact_keys(item, {"scenario_id", "tasks"})
        tasks = item["tasks"]
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 8:
            _fail("source_drift")
        for task in tasks:
            if (
                not isinstance(task, list)
                or len(task) != 4
                or _IDENTIFIER.fullmatch(str(task[0]).lower().replace("-", "_"))
                is None
                or _TASK_ID.fullmatch(str(task[1])) is None
                or task[3] not in {"completion_revision", "review_target"}
            ):
                _fail("source_drift")


def _validate_reducer_protocol(protocol: Mapping[str, Any]) -> None:
    reducer = _exact_keys(
        protocol.get("reducer_manifest"),
        {
            "schema",
            "exact_keys",
            "max_entries_per_list",
            "max_selector_bytes",
            "max_probe_bytes",
            "max_target_change_bytes",
            "list_shapes",
            "target_change_keys",
            "verification_kinds",
        },
    )
    if reducer["schema"] != "m20-reducer-manifest-v1":
        _fail("source_drift")
    if reducer["exact_keys"] != [
        "schema",
        "scenario_id",
        "arm",
        "owner_slots",
        "contract_probes",
        "inventory_probes",
        "maintenance_selectors",
        "fixture_probes",
        "verification_labels",
        "target_change",
    ]:
        _fail("source_drift")
    if (
        reducer["max_entries_per_list"] != 64
        or reducer["max_selector_bytes"] != 240
        or reducer["max_probe_bytes"] != 4096
        or reducer["max_target_change_bytes"] != 16384
        or reducer["verification_kinds"] != ["focused", "lane", "all", "other"]
    ):
        _fail("source_drift")


def _normalize_unknowns(
    unknowns: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    all_reasons: set[str] = set()
    for raw in unknowns:
        item = _exact_keys(raw, {"field", "reasons"})
        field = _string(item["field"], pattern=_UNKNOWN_FIELD)
        if field in seen_fields:
            _fail("parse_failed")
        reasons = _sorted_unique_strings(
            item["reasons"],
            pattern=_CODE,
            maximum_items=len(_UNKNOWN_REASONS),
        )
        if not reasons or not set(reasons) <= _UNKNOWN_REASONS:
            _fail("parse_failed")
        seen_fields.add(field)
        all_reasons.update(reasons)
        normalized.append({"field": field, "reasons": list(reasons)})
    normalized.sort(key=lambda item: item["field"].encode("ascii"))
    if len(normalized) > 128:
        _fail("parse_failed")
    return normalized, sorted(all_reasons, key=lambda item: item.encode("ascii"))


def build_observation(
    protocol: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str | None,
    evidence_class: str,
    channel: str,
    record_key: str,
    payload: Any,
    unknowns: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build and immediately validate one observation."""

    normalized_unknowns, reasons = _normalize_unknowns(unknowns)
    if set(reasons) & _INVALIDATING_REASONS:
        eligibility = "excluded"
    elif reasons:
        eligibility = "partial"
    else:
        eligibility = "eligible"
    row = (unit, scenario_id, trial_id, evidence_class, channel, record_key)
    authority = protocol["authority"]
    observation = {
        "schema": OBSERVATION_SCHEMA,
        "contract_id": authority["contract_id"],
        "contract_revision": authority["contract_revision"],
        "baseline_revision": authority["baseline_revision"],
        "authority_revision": authority["authority_revision"],
        "observation_id": observation_id(protocol, row),
        "scenario_id": scenario_id,
        "trial_id": trial_id,
        "record_key": record_key,
        "unit": unit,
        "evidence_class": evidence_class,
        "channel": channel,
        "eligibility": eligibility,
        "unknown_reasons": reasons,
        "unknowns": normalized_unknowns,
        "payload": payload,
    }
    return validate_observation(protocol, observation)


def _validate_unknown_applicability(
    evidence_class: str,
    unknowns: Sequence[Mapping[str, Any]],
) -> None:
    for item in unknowns:
        reasons = set(item["reasons"])
        if "not_reconstructable" in reasons and evidence_class != "historically_reconstructed":
            _fail("parse_failed")
        if "observer_uncertain" in reasons and evidence_class != "observer_attested":
            _fail("parse_failed")
        if "timeout" in reasons and evidence_class != "machine_observed":
            _fail("parse_failed")
        if "cap_exceeded" in reasons and evidence_class not in {
            "machine_observed",
            "observer_attested",
        }:
            _fail("parse_failed")
        if "not_observable" in reasons and evidence_class not in {
            "machine_observed",
            "observer_attested",
        }:
            _fail("parse_failed")


def _payload_value(payload: Any, path: str) -> Any:
    current = payload
    for component in path.split("."):
        if isinstance(current, list):
            if not component.isascii() or not component.isdecimal():
                _fail("parse_failed")
            index = int(component)
            if index >= len(current):
                _fail("parse_failed")
            current = current[index]
        elif isinstance(current, dict) and component in current:
            current = current[component]
        else:
            _fail("parse_failed")
    return current


def _validate_unknown_values(
    payload: Any,
    channel: str,
    unknowns: Sequence[Mapping[str, Any]],
) -> None:
    cap_paths: dict[str, int] = {}
    if channel == "cli_invocation":
        operations = payload.get("operations") if isinstance(payload, dict) else None
        if isinstance(operations, list):
            for index in range(len(operations)):
                for field in ("stdout_bytes", "stderr_bytes"):
                    cap_paths[f"operations.{index}.{field}"] = MAX_STREAM_BYTES
    elif channel == "fresh_agent_trial":
        cap_paths = {
            "reference_opens": 256,
            "clarification_turns": 16,
            "manual_inputs": 32,
            "governance_invocations": 64,
            "reviewer_invocations": 8,
        }
    elif channel == "trial_measurement":
        for unknown in unknowns:
            path = str(unknown["field"])
            cap_paths[path] = (
                MAX_SIGNED_64
                if "contract_revision" in path or "review_generation" in path
                else MAX_SIGNED_32
            )
    for unknown in unknowns:
        path = str(unknown["field"])
        reasons = set(unknown["reasons"])
        value = _payload_value(payload, path)
        if reasons & _INVALIDATING_REASONS:
            if not reasons <= _INVALIDATING_REASONS:
                _fail("parse_failed")
            if value not in (None, "unknown", "not_run"):
                _fail("parse_failed")
            continue
        if channel == "fresh_agent_trial":
            if path in cap_paths:
                expected = (
                    {"cap_exceeded"}
                    if value == cap_paths[path]
                    else {"not_observable"}
                )
            elif path.startswith("assessment."):
                expected = {"observer_uncertain"}
            else:
                _fail("parse_failed")
            if reasons != expected:
                _fail("parse_failed")
        elif channel == "state_projection" and reasons != {"not_observable"}:
            _fail("parse_failed")
        elif (
            channel == "task_git_reconstruction"
            and reasons != {"not_reconstructable"}
        ):
            _fail("parse_failed")
        elif channel == "trial_measurement":
            if path in {
                "data.verification_escalation",
                "data.verification_steps",
            }:
                expected = {"not_observable"}
            elif path == "data.target_change_result":
                expected = (
                    {"not_observable"}
                    if value == "not_run"
                    else reasons
                )
                if value == "unknown" and reasons not in (
                    {"cap_exceeded"},
                    {"not_observable"},
                    {"timeout"},
                ):
                    _fail("parse_failed")
            else:
                expected = (
                    {"cap_exceeded"}
                    if value == cap_paths.get(path)
                    else {"not_observable"}
                )
            if reasons != expected:
                _fail("parse_failed")
        enum_cap_unknown = (
            channel == "trial_measurement"
            and path == "data.target_change_result"
            and value == "unknown"
        )
        if "cap_exceeded" in reasons:
            if reasons != {"cap_exceeded"} or (
                not enum_cap_unknown and cap_paths.get(path) != value
            ):
                _fail("parse_failed")
        elif value not in (None, "unknown", "not_run"):
            _fail("parse_failed")


def validate_observation(
    protocol: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless one observation matches the frozen schema."""

    item = _exact_keys(observation, _TOP_LEVEL_KEYS)
    authority = protocol["authority"]
    if (
        item["schema"] != OBSERVATION_SCHEMA
        or item["contract_id"] != authority["contract_id"]
        or item["contract_revision"] != authority["contract_revision"]
        or item["baseline_revision"] != authority["baseline_revision"]
        or item["authority_revision"] != authority["authority_revision"]
    ):
        _fail("source_drift")
    scenario_id = _string(item["scenario_id"], pattern=_IDENTIFIER)
    trial_id = item["trial_id"]
    if trial_id is not None:
        _string(trial_id, pattern=_IDENTIFIER)
    record_key = _string(item["record_key"], pattern=_IDENTIFIER)
    unit = item["unit"]
    evidence_class = item["evidence_class"]
    channel = item["channel"]
    row = (unit, scenario_id, trial_id, evidence_class, channel, record_key)
    if row not in derive_inventory(protocol, str(unit)):
        _fail("source_drift")
    if item["observation_id"] != observation_id(protocol, row):
        _fail("source_drift")
    _string(item["observation_id"], pattern=_OBSERVATION_ID)
    allowed_channels = {
        "machine_observed": {
            "cli_invocation",
            "state_projection",
            "trial_measurement",
        },
        "historically_reconstructed": {"task_git_reconstruction"},
        "observer_attested": {"fresh_agent_trial"},
    }
    if evidence_class not in allowed_channels or channel not in allowed_channels[evidence_class]:
        _fail("source_drift")
    if trial_id is None and evidence_class != "historically_reconstructed":
        _fail("source_drift")
    if trial_id is not None and evidence_class == "historically_reconstructed":
        _fail("source_drift")
    unknowns, reasons = _normalize_unknowns(item["unknowns"])
    if item["unknowns"] != unknowns or item["unknown_reasons"] != reasons:
        _fail("parse_failed")
    _validate_unknown_applicability(str(evidence_class), unknowns)
    reason_set = set(reasons)
    eligibility = item["eligibility"]
    if eligibility == "eligible":
        if reasons:
            _fail("parse_failed")
    elif eligibility == "partial":
        if not reasons or reason_set & _INVALIDATING_REASONS:
            _fail("parse_failed")
    elif eligibility == "excluded":
        if not reason_set & _INVALIDATING_REASONS:
            _fail("parse_failed")
    else:
        _fail("parse_failed")
    payload = item["payload"]
    if payload is None:
        if (
            eligibility != "excluded"
            or unknowns != [{"field": "payload", "reasons": reasons}]
        ):
            _fail("parse_failed")
        return dict(item)
    if any(unknown["field"] == "payload" for unknown in unknowns):
        _fail("parse_failed")
    unknown_fields = {unknown["field"] for unknown in unknowns}
    if channel == "cli_invocation":
        _validate_cli_payload(
            protocol,
            scenario_id,
            payload,
            unknown_fields,
        )
    elif channel == "state_projection":
        _validate_state_payload(
            protocol,
            scenario_id,
            payload,
            unknown_fields,
        )
    elif channel == "task_git_reconstruction":
        _validate_history_payload(payload, record_key, unknown_fields)
    elif channel == "trial_measurement":
        _validate_trial_measurement(payload, unit, record_key, unknown_fields)
    elif channel == "fresh_agent_trial":
        _validate_fresh_agent(
            payload,
            unit,
            scenario_id,
            str(trial_id),
            unknown_fields,
        )
    _validate_unknown_values(payload, str(channel), unknowns)
    return dict(item)


def _m20_2_scenario(
    protocol: Mapping[str, Any],
    scenario_id: str,
) -> Mapping[str, Any]:
    matches = [
        item
        for item in protocol["m20_2"]["harness_scenarios"]
        if item["scenario_id"] == scenario_id
    ]
    if len(matches) != 1:
        _fail("source_drift")
    return matches[0]


def _validate_cli_payload(
    protocol: Mapping[str, Any],
    scenario_id: str,
    payload: Any,
    unknown_fields: set[str],
) -> None:
    root = _exact_keys(payload, {"operations"})
    operations = root["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 32:
        _fail("parse_failed")
    expected = tuple(
        tuple(operation)
        for operation in _m20_2_scenario(protocol, scenario_id)["operations"]
    )
    actual = tuple(
        (operation.get("phase"), operation.get("command_leaf"))
        if isinstance(operation, dict)
        else (None, None)
        for operation in operations
    )
    if actual != expected:
        _fail("source_drift")
    allowed_unknowns: set[str] = set()
    operation_keys = {
        "ordinal",
        "phase",
        "command_leaf",
        "duration_ms",
        "duration_capped",
        "result",
        "exit_code",
        "warning_codes",
        "error_codes",
        "stdout_bytes",
        "stderr_bytes",
    }
    for index, raw in enumerate(operations):
        operation = _exact_keys(raw, operation_keys)
        if _integer(operation["ordinal"], minimum=1, maximum=32) != index + 1:
            _fail("parse_failed")
        if operation["phase"] not in _PHASES or operation["command_leaf"] not in _COMMAND_LEAVES:
            _fail("parse_failed")
        duration = _integer(operation["duration_ms"], maximum=300_000)
        capped = _boolean(operation["duration_capped"])
        result = operation["result"]
        exit_code = operation["exit_code"]
        warnings = _sorted_unique_strings(
            operation["warning_codes"], pattern=_CODE, maximum_items=16
        )
        errors = _sorted_unique_strings(
            operation["error_codes"], pattern=_CODE, maximum_items=16
        )
        if result == "timeout":
            if duration != 300_000 or not capped or exit_code is not None or warnings or errors:
                _fail("parse_failed")
        else:
            if duration > 299_999 or capped:
                _fail("parse_failed")
            if result == "success":
                if exit_code != 0 or errors:
                    _fail("parse_failed")
            elif result == "input_error":
                if exit_code != 1 or not errors:
                    _fail("parse_failed")
            elif result == "service_error":
                if exit_code != 2 or not errors:
                    _fail("parse_failed")
            else:
                _fail("parse_failed")
        for field in ("stdout_bytes", "stderr_bytes"):
            _integer(operation[field], maximum=MAX_STREAM_BYTES)
            allowed_unknowns.add(f"operations.{index}.{field}")
    if not unknown_fields <= allowed_unknowns:
        _fail("parse_failed")


def _validate_state_object(
    state: Any,
    *,
    prefix: str,
    unknown_fields: set[str],
) -> None:
    item = _exact_keys(state, _STATE_KEYS)
    task_status_path = f"{prefix}.task_status"
    if item["task_status"] is None:
        if task_status_path not in unknown_fields:
            _fail("parse_failed")
    elif (
        item["task_status"] not in _TASK_STATUSES
        or task_status_path in unknown_fields
    ):
        _fail("parse_failed")
    numeric = {
        "contract_revision",
        "review_generation",
        "receipts_current",
        "qualifying_passes",
        "changes_requested_current",
        "findings_open_high",
        "findings_open_medium",
        "findings_open_low",
        "handoffs_pending",
        "handoffs_delivered",
        "handoffs_withdrawn",
        "completion_cycles",
    }
    for field in numeric:
        value = item[field]
        if value is None:
            if f"{prefix}.{field}" not in unknown_fields:
                _fail("parse_failed")
        elif f"{prefix}.{field}" in unknown_fields:
            _fail("parse_failed")
        else:
            _integer(value)
    attestation = item["verification_attestation"]
    attestation_path = f"{prefix}.verification_attestation"
    if attestation is not None:
        if attestation_path in unknown_fields:
            _fail("parse_failed")
        _boolean(attestation)
        if attestation is not True:
            _fail("parse_failed")
    elif attestation_path in unknown_fields and item["task_status"] != "done":
        _fail("parse_failed")
    detail = item["verification_detail"]
    if detail not in {"absent", "unknown", None}:
        _fail("parse_failed")
    if detail in {"unknown", None} and f"{prefix}.verification_detail" not in unknown_fields:
        _fail("parse_failed")
    if detail == "absent" and f"{prefix}.verification_detail" in unknown_fields:
        _fail("parse_failed")
    if item["task_status"] != "done" and attestation is not None:
        _fail("parse_failed")


def _validate_state_payload(
    protocol: Mapping[str, Any],
    scenario_id: str,
    payload: Any,
    unknown_fields: set[str],
) -> None:
    root = _exact_keys(payload, {"before", "after"})
    _validate_state_object(root["before"], prefix="before", unknown_fields=unknown_fields)
    _validate_state_object(root["after"], prefix="after", unknown_fields=unknown_fields)
    if scenario_id.startswith("gov_"):
        scenario = _m20_2_scenario(protocol, scenario_id)
        for boundary in ("before", "after"):
            if any(
                root[boundary][field] != expected
                for field, expected in scenario[boundary].items()
            ):
                _fail("source_drift")
    allowed = {
        f"{prefix}.{field}"
        for prefix in ("before", "after")
        for field in _STATE_KEYS
    }
    if not unknown_fields <= allowed:
        _fail("parse_failed")


def _validate_history_payload(
    payload: Any,
    record_key: str,
    unknown_fields: set[str],
) -> None:
    item = _exact_keys(payload, {"metric", "value", "coverage", "references"})
    if item["metric"] != record_key or record_key not in _RETROSPECTIVE_METRICS:
        _fail("source_drift")
    value = item["value"]
    coverage = item["coverage"]
    if coverage == "complete":
        _integer(value)
        if unknown_fields:
            _fail("parse_failed")
    elif coverage == "partial":
        if value is not None or unknown_fields != {"value"}:
            _fail("parse_failed")
    else:
        _fail("parse_failed")
    references = item["references"]
    if not isinstance(references, list) or not 1 <= len(references) <= 8:
        _fail("parse_failed")
    pattern = _HEX_40 if record_key == "git_wall_clock_span_ms" else _TASK_ID
    _sorted_unique_strings(references, pattern=pattern, maximum_items=8)


def _nullable_integer(
    value: Any,
    *,
    path: str,
    unknown_fields: set[str],
    maximum: int = MAX_SIGNED_32,
) -> None:
    if value is None:
        if path not in unknown_fields:
            _fail("parse_failed")
    else:
        _integer(value, maximum=maximum)


def _validate_trial_measurement(
    payload: Any,
    unit: str,
    record_key: str,
    unknown_fields: set[str],
) -> None:
    root = _exact_keys(payload, {"measurement_kind", "data"})
    kind = root["measurement_kind"]
    if unit == "M20.3":
        if kind != "verification_proportionality" or record_key != "verification_measurement":
            _fail("source_drift")
        _validate_verification_measurement(root["data"], unknown_fields)
    elif unit == "M20.4":
        if kind != "split_pressure" or record_key != "split_measurement":
            _fail("source_drift")
        _validate_split_measurement(root["data"], unknown_fields)
    else:
        _fail("source_drift")


def _validate_verification_measurement(data: Any, unknown_fields: set[str]) -> None:
    keys = {
        "product_files",
        "test_files",
        "product_lines",
        "test_lines",
        "test_cases",
        "contract_owner_fanout",
        "inventory_owner_fanout",
        "maintenance_fanout",
        "duplicate_contract_locations",
        "fixture_copy_groups",
        "verification_escalation",
        "target_change_result",
        "verification_steps",
    }
    item = _exact_keys(data, keys)
    allowed: set[str] = set()
    for field in keys - {
        "verification_escalation",
        "target_change_result",
        "verification_steps",
    }:
        path = f"data.{field}"
        allowed.add(path)
        _nullable_integer(item[field], path=path, unknown_fields=unknown_fields)
    escalation = item["verification_escalation"]
    if escalation not in {"repeated_all", "all_first", "proportional", "unknown"}:
        _fail("parse_failed")
    if escalation == "unknown" and "data.verification_escalation" not in unknown_fields:
        _fail("parse_failed")
    allowed.add("data.verification_escalation")
    target = item["target_change_result"]
    if target not in {"detected", "not_detected", "not_run", "unknown"}:
        _fail("parse_failed")
    if target in {"not_run", "unknown"} and "data.target_change_result" not in unknown_fields:
        _fail("parse_failed")
    allowed.add("data.target_change_result")
    steps = item["verification_steps"]
    allowed.add("data.verification_steps")
    if steps is None:
        if "data.verification_steps" not in unknown_fields:
            _fail("parse_failed")
        expected_escalation = "unknown"
    else:
        if not isinstance(steps, list) or len(steps) > 16:
            _fail("parse_failed")
        for index, step in enumerate(steps):
            value = _exact_keys(
                step, {"ordinal", "kind", "duration_ms", "duration_capped", "result"}
            )
            if value["ordinal"] != index + 1 or value["kind"] not in {
                "focused",
                "lane",
                "all",
                "other",
            }:
                _fail("parse_failed")
            duration = _integer(value["duration_ms"], maximum=300_000)
            capped = _boolean(value["duration_capped"])
            result = value["result"]
            if result == "timeout":
                if duration != 300_000 or not capped:
                    _fail("parse_failed")
            elif result in {"success", "failure"}:
                if duration > 299_999 or capped:
                    _fail("parse_failed")
            else:
                _fail("parse_failed")
        all_count = sum(step["kind"] == "all" for step in steps)
        if all_count >= 2:
            expected_escalation = "repeated_all"
        elif steps and steps[0]["kind"] == "all":
            expected_escalation = "all_first"
        elif steps:
            expected_escalation = "proportional"
        else:
            expected_escalation = "unknown"
    if escalation != expected_escalation:
        _fail("source_drift")
    if expected_escalation == "unknown":
        if "data.verification_escalation" not in unknown_fields:
            _fail("parse_failed")
    elif "data.verification_escalation" in unknown_fields:
        _fail("parse_failed")
    if not unknown_fields <= allowed:
        _fail("parse_failed")


def _validate_split_measurement(data: Any, unknown_fields: set[str]) -> None:
    root = _exact_keys(data, {"episodes"})
    episodes = root["episodes"]
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= 8:
        _fail("parse_failed")
    keys = {
        "episode_id",
        "files_before",
        "files_after",
        "modules_before",
        "modules_after",
        "lines_before",
        "lines_after",
        "contract_revision_before",
        "contract_revision_after",
        "review_generation_before",
        "review_generation_after",
        "governance_cycles",
        "review_cycles",
    }
    allowed: set[str] = set()
    episode_ids: list[str] = []
    for index, raw in enumerate(episodes):
        episode = _exact_keys(raw, keys)
        episode_ids.append(_string(episode["episode_id"], pattern=_IDENTIFIER))
        for field in keys - {"episode_id"}:
            path = f"data.episodes.{index}.{field}"
            allowed.add(path)
            maximum = (
                MAX_SIGNED_64
                if field.startswith(("contract_revision", "review_generation"))
                else MAX_SIGNED_32
            )
            _nullable_integer(
                episode[field],
                path=path,
                unknown_fields=unknown_fields,
                maximum=maximum,
            )
        before_revision = episode["contract_revision_before"]
        after_revision = episode["contract_revision_after"]
        before_generation = episode["review_generation_before"]
        after_generation = episode["review_generation_after"]
        if (
            before_revision is not None
            and after_revision is not None
            and after_revision < before_revision
            or before_generation is not None
            and after_generation is not None
            and after_generation < before_generation
        ):
            _fail("source_drift")
    if len(episode_ids) != len(set(episode_ids)):
        _fail("source_drift")
    if not unknown_fields <= allowed:
        _fail("parse_failed")


def _validate_fresh_agent(
    payload: Any,
    unit: str,
    scenario_id: str,
    trial_id: str,
    unknown_fields: set[str],
) -> None:
    keys = {
        "cohort",
        "arm",
        "workload_digest",
        "control_digest",
        "outcome",
        "reference_opens",
        "clarification_turns",
        "manual_inputs",
        "governance_invocations",
        "reviewer_invocations",
        "assessment_kind",
        "assessment",
    }
    item = _exact_keys(payload, keys)
    if item["cohort"] != "fresh_baseline_v1":
        _fail("source_drift")
    arm = _string(item["arm"], pattern=_IDENTIFIER)
    expected_trial_id = f"{scenario_id}.{arm}.01"
    if trial_id != expected_trial_id:
        _fail("source_drift")
    _string(item["workload_digest"], pattern=_HEX_64)
    _string(item["control_digest"], pattern=_HEX_64)
    if item["outcome"] not in {
        "completed",
        "blocked",
        "paused",
        "handed_off",
        "failed",
        "inconclusive",
    }:
        _fail("parse_failed")
    caps = {
        "reference_opens": 256,
        "clarification_turns": 16,
        "manual_inputs": 32,
        "governance_invocations": 64,
        "reviewer_invocations": 8,
    }
    allowed = set()
    for field, maximum in caps.items():
        allowed.add(field)
        _nullable_integer(
            item[field],
            path=field,
            unknown_fields=unknown_fields,
            maximum=maximum,
        )
    if unit == "M20.3":
        if (
            arm != "baseline"
            or item["assessment_kind"] != "verification_proportionality"
        ):
            _fail("source_drift")
        _validate_verification_assessment(item["assessment"], unknown_fields, allowed)
    elif unit == "M20.4":
        if (
            arm not in {"broad", "bounded"}
            or scenario_id == "sp_handoff_control"
            and arm != "broad"
            or item["assessment_kind"] != "split_pressure"
        ):
            _fail("source_drift")
        _validate_split_assessment(item["assessment"], unknown_fields, allowed)
    else:
        _fail("source_drift")
    if not unknown_fields <= allowed:
        _fail("parse_failed")


def _validate_verification_assessment(
    assessment: Any,
    unknown_fields: set[str],
    allowed: set[str],
) -> None:
    keys = {
        "distinct_risks",
        "new_cases",
        "redundant_responsibilities",
        "verification_fact_codes",
        "manual_reentry_fact_codes",
        "responsibility_pattern_codes",
        "reuse",
        "instruction_fit",
        "minimal_receipt_fit",
    }
    item = _exact_keys(assessment, keys)
    for field in {"distinct_risks", "new_cases", "redundant_responsibilities"}:
        path = f"assessment.{field}"
        allowed.add(path)
        _nullable_integer(item[field], path=path, unknown_fields=unknown_fields)
    fact_values = frozenset(
        {"command_label", "result", "source_revision", "duration", "scope_coverage"}
    )
    arrays: dict[str, tuple[str, ...] | None] = {}
    for field, value_set in (
        ("verification_fact_codes", fact_values),
        ("manual_reentry_fact_codes", fact_values),
        (
            "responsibility_pattern_codes",
            frozenset(
                {
                    "duplicate_contract_assertion",
                    "duplicate_inventory_owner",
                    "fixture_copy",
                    "nondetecting_regression",
                    "unbounded_verification_escalation",
                }
            ),
        ),
    ):
        path = f"assessment.{field}"
        allowed.add(path)
        value = item[field]
        if value is None:
            if path not in unknown_fields:
                _fail("parse_failed")
            arrays[field] = None
        else:
            values = _sorted_unique_strings(value, pattern=_CODE, maximum_items=16)
            if not set(values) <= value_set:
                _fail("parse_failed")
            arrays[field] = values
    if (
        arrays["manual_reentry_fact_codes"] is not None
        and arrays["verification_fact_codes"] is not None
        and not set(arrays["manual_reentry_fact_codes"])
        <= set(arrays["verification_fact_codes"])
    ):
        _fail("source_drift")
    if item["reuse"] not in {"reused", "mixed", "copied", "new_justified", "unknown"}:
        _fail("parse_failed")
    if item["reuse"] == "unknown":
        allowed.add("assessment.reuse")
        if "assessment.reuse" not in unknown_fields:
            _fail("parse_failed")
    for field in ("instruction_fit", "minimal_receipt_fit"):
        if item[field] not in {"yes", "no", "unknown"}:
            _fail("parse_failed")
        if item[field] == "unknown":
            path = f"assessment.{field}"
            allowed.add(path)
            if path not in unknown_fields:
                _fail("parse_failed")


def _validate_split_assessment(
    assessment: Any,
    unknown_fields: set[str],
    allowed: set[str],
) -> None:
    root = _exact_keys(assessment, {"episodes"})
    episodes = root["episodes"]
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= 8:
        _fail("parse_failed")
    keys = {
        "episode_id",
        "phase",
        "cause",
        "current_response",
        "acceptance_independent",
        "verification_independent",
        "commit_independent",
        "completion_independent",
    }
    episode_ids: list[str] = []
    for index, raw in enumerate(episodes):
        item = _exact_keys(raw, keys)
        episode_ids.append(_string(item["episode_id"], pattern=_IDENTIFIER))
        if item["phase"] not in {"intake", "implementation", "verification", "review"}:
            _fail("parse_failed")
        if item["cause"] not in {
            "multiple_outcomes",
            "in_scope_discovery",
            "user_expansion",
            "repeated_failure",
            "cross_module",
            "out_of_scope_control",
        }:
            _fail("parse_failed")
        if item["current_response"] not in {"keep_current", "block", "handoff"}:
            _fail("parse_failed")
        for field in (
            "acceptance_independent",
            "verification_independent",
            "commit_independent",
            "completion_independent",
        ):
            if item[field] not in {"yes", "no", "unknown"}:
                _fail("parse_failed")
            if item[field] == "unknown":
                path = f"assessment.episodes.{index}.{field}"
                allowed.add(path)
                if path not in unknown_fields:
                    _fail("parse_failed")
    if len(episode_ids) != len(set(episode_ids)):
        _fail("source_drift")


def _episode_ids(record: Mapping[str, Any]) -> tuple[str, ...] | None:
    payload = record["payload"]
    if payload is None:
        return None
    if record["channel"] == "trial_measurement":
        episodes = payload["data"]["episodes"]
    elif record["channel"] == "fresh_agent_trial":
        episodes = payload["assessment"]["episodes"]
    else:
        _fail("source_drift")
    return tuple(str(episode["episode_id"]) for episode in episodes)


def _validate_cross_record_invariants(
    protocol: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    unit: str,
) -> None:
    if unit != "M20.4":
        return
    episode_plan = load_m20_4_episode_plan(protocol)
    plans_by_key = {
        (item["scenario_id"], item["trial_id"]): item
        for item in episode_plan["plans"]
    }
    by_key = {
        (record["scenario_id"], record["trial_id"], record["channel"]): record
        for record in records
    }
    attempts = {
        (record["scenario_id"], record["trial_id"])
        for record in records
        if record["channel"] in {"trial_measurement", "fresh_agent_trial"}
    }
    for scenario_id, trial_id in attempts:
        measurement = by_key[(scenario_id, trial_id, "trial_measurement")]
        attestation = by_key[(scenario_id, trial_id, "fresh_agent_trial")]
        measurement_ids = _episode_ids(measurement)
        attestation_ids = _episode_ids(attestation)
        expected_ids = tuple(
            episode["episode_id"]
            for episode in plans_by_key[(scenario_id, trial_id)]["episodes"]
        )
        if (
            measurement_ids is not None
            and measurement_ids != expected_ids
            or attestation_ids is not None
            and attestation_ids != expected_ids
        ):
            _fail("source_drift")
        if (
            measurement_ids is not None
            and attestation_ids is not None
            and measurement_ids != attestation_ids
        ):
            _fail("source_drift")
        if scenario_id == "sp_handoff_control" and attestation["payload"] is not None:
            episodes = attestation["payload"]["assessment"]["episodes"]
            if any(
                episode["cause"] != "out_of_scope_control"
                or episode["current_response"] != "handoff"
                for episode in episodes
            ):
                _fail("source_drift")
            if len(episodes) != 1:
                _fail("source_drift")
        if scenario_id == "sp_handoff_control":
            state = by_key[(scenario_id, trial_id, "state_projection")]
            measurement_payload = measurement["payload"]
            if measurement_payload is not None:
                episodes = measurement_payload["data"]["episodes"]
                if len(episodes) != 1:
                    _fail("source_drift")
            if measurement_payload is not None and state["payload"] is not None:
                episode = episodes[0]
                before = state["payload"]["before"]
                after = state["payload"]["after"]
                if (
                    episode["contract_revision_before"]
                    != before["contract_revision"]
                    or episode["contract_revision_after"]
                    != after["contract_revision"]
                    or episode["review_generation_before"]
                    != before["review_generation"]
                    or episode["review_generation_after"]
                    != after["review_generation"]
                ):
                    _fail("source_drift")
    for scenario_id in {
        scenario
        for scenario, _trial in attempts
        if scenario != "sp_handoff_control"
    }:
        broad_trial = f"{scenario_id}.broad.01"
        bounded_trial = f"{scenario_id}.bounded.01"
        broad_attestation = by_key[
            (scenario_id, broad_trial, "fresh_agent_trial")
        ]
        bounded_attestation = by_key[
            (scenario_id, bounded_trial, "fresh_agent_trial")
        ]
        if (
            broad_attestation["payload"] is not None
            and bounded_attestation["payload"] is not None
            and broad_attestation["payload"]["workload_digest"]
            != bounded_attestation["payload"]["workload_digest"]
        ):
            _fail("source_drift")
        broad_measurement = by_key[
            (scenario_id, broad_trial, "trial_measurement")
        ]
        bounded_measurement = by_key[
            (scenario_id, bounded_trial, "trial_measurement")
        ]
        broad_ids = _episode_ids(broad_measurement)
        bounded_ids = _episode_ids(bounded_measurement)
        if (
            broad_ids is not None
            and bounded_ids is not None
            and broad_ids != bounded_ids
        ):
            _fail("source_drift")


def serialize_corpus(
    protocol: Mapping[str, Any],
    unit: str,
    observations: Iterable[Mapping[str, Any]],
    *,
    record_limit: int | None = None,
    corpus_limit: int | None = None,
) -> bytes:
    """Validate exact identity/cardinality and serialize one unit corpus."""

    bounds = protocol["bounds"]
    record_limit = bounds["record_bytes"] if record_limit is None else record_limit
    corpus_limit = bounds["unit_corpus_bytes"] if corpus_limit is None else corpus_limit
    _integer(record_limit, minimum=1, maximum=MAX_SIGNED_32)
    _integer(corpus_limit, minimum=1, maximum=MAX_SIGNED_32)
    records = [validate_observation(protocol, item) for item in observations]
    expected_rows = derive_inventory(protocol, unit)
    if len(records) != len(expected_rows):
        _fail("source_drift")
    ids = [record["observation_id"] for record in records]
    if len(ids) != len(set(ids)):
        _fail("source_drift")
    expected_ids = {observation_id(protocol, row) for row in expected_rows}
    if set(ids) != expected_ids:
        _fail("source_drift")
    _validate_cross_record_invariants(protocol, records, unit)
    for record in records:
        if len(canonical_json_bytes(record)) > record_limit:
            _fail("cap_exceeded")
    records.sort(key=lambda item: item["observation_id"].encode("ascii"))
    candidate = canonical_json_bytes(records)
    if len(candidate) <= corpus_limit:
        return candidate
    failure = {
        "schema": CORPUS_FAILURE_SCHEMA,
        "unit": unit,
        "reason": "cap_exceeded",
        "record_count": len(records),
        "candidate_bytes": len(candidate),
    }
    return canonical_json_bytes(failure)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


class AttemptJournal:
    """Sanitized no-rerun journal for the eight fixed M20.2 attempts."""

    def __init__(self, path: Path, protocol: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self.protocol = protocol

    def _empty(self) -> dict[str, Any]:
        return {
            "schema": JOURNAL_SCHEMA,
            "authority_revision": self.protocol["authority"]["authority_revision"],
            "attempts": {},
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            raw = self.path.read_bytes()
        except OSError:
            _fail("source_missing")
        _privacy_check(raw)
        value = _load_json_bytes(raw)
        root = _exact_keys(value, {"schema", "authority_revision", "attempts"})
        if (
            root["schema"] != JOURNAL_SCHEMA
            or root["authority_revision"]
            != self.protocol["authority"]["authority_revision"]
            or not isinstance(root["attempts"], dict)
            or canonical_json_bytes(root) != raw
        ):
            _fail("source_drift")
        for attempt_id, raw_state in root["attempts"].items():
            _string(attempt_id, pattern=_IDENTIFIER)
            state = _exact_keys(raw_state, {"status", "records"})
            if state["status"] == "started":
                if state["records"] != []:
                    _fail("source_drift")
            elif state["status"] == "reduced":
                if not isinstance(state["records"], list) or not state["records"]:
                    _fail("source_drift")
                for record in state["records"]:
                    validate_observation(self.protocol, record)
            else:
                _fail("source_drift")
        return dict(root)

    def _write(self, value: Mapping[str, Any]) -> None:
        payload = canonical_json_bytes(value)
        _privacy_check(payload)
        _atomic_write(self.path, payload)

    def status(self, attempt_id: str) -> str | None:
        _string(attempt_id, pattern=_IDENTIFIER)
        state = self._read()["attempts"].get(attempt_id)
        return None if state is None else str(state["status"])

    def start(self, attempt_id: str) -> None:
        self.start_many((attempt_id,))

    def start_many(self, attempt_ids: Iterable[str]) -> None:
        identifiers = tuple(attempt_ids)
        if not identifiers or len(identifiers) != len(set(identifiers)):
            _fail("parse_failed")
        root = self._read()
        for attempt_id in identifiers:
            _string(attempt_id, pattern=_IDENTIFIER)
            if attempt_id in root["attempts"]:
                _fail("attempt_already_started")
        for attempt_id in identifiers:
            root["attempts"][attempt_id] = {"status": "started", "records": []}
        self._write(root)

    def finish(
        self,
        attempt_id: str,
        records: Iterable[Mapping[str, Any]],
    ) -> None:
        self.finish_many({attempt_id: list(records)})

    def finish_many(
        self,
        records_by_attempt: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> None:
        if not records_by_attempt:
            _fail("parse_failed")
        root = self._read()
        validated: dict[str, list[dict[str, Any]]] = {}
        for attempt_id, records in records_by_attempt.items():
            state = root["attempts"].get(attempt_id)
            if state is None or state["status"] != "started":
                _fail("attempt_not_started")
            if not records:
                _fail("parse_failed")
            validated[attempt_id] = [
                validate_observation(self.protocol, record) for record in records
            ]
            _privacy_check(canonical_json_bytes(validated[attempt_id]))
        for attempt_id, records in validated.items():
            root["attempts"][attempt_id] = {
                "status": "reduced",
                "records": records,
            }
        self._write(root)

    def reduced_records(self) -> list[dict[str, Any]]:
        root = self._read()
        records: list[dict[str, Any]] = []
        for attempt_id in sorted(root["attempts"], key=lambda value: value.encode("ascii")):
            state = root["attempts"][attempt_id]
            if state["status"] == "started":
                _fail("interrupted_attempt")
            records.extend(state["records"])
        return records

    def remove(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            _fail("artifact_delete_failed")


class CollectionLock:
    """Cross-process, non-blocking lock for the one-shot collector."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._descriptor: int | None = None

    def __enter__(self) -> CollectionLock:
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink():
                _fail("source_drift")
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                descriptor = None
                _fail("source_drift")
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(descriptor)
                descriptor = None
                if error.errno in {errno.EACCES, errno.EAGAIN}:
                    _fail("collection_busy")
                _fail("source_missing")
            self._descriptor = descriptor
            return self
        except M20ObservationError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            _fail("source_missing")

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                _fail("source_missing")
        finally:
            os.close(descriptor)


def _utf8_size(value: Any, *, maximum: int, allow_empty: bool = True) -> str:
    text = _string(value, minimum=0 if allow_empty else 1)
    try:
        size = len(text.encode("utf-8", errors="strict"))
    except UnicodeError:
        _fail("parse_failed")
    if size > maximum:
        _fail("cap_exceeded")
    return text


def _safe_selector(value: Any, maximum: int) -> str:
    selector = _string(value, pattern=_SAFE_SELECTOR)
    if len(selector.encode("ascii")) > maximum or "\\" in selector or ":" in selector:
        _fail("parse_failed")
    path = PurePosixPath(selector)
    if (
        path.is_absolute()
        or not path.name
        or path.as_posix() != selector
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail("parse_failed")
    return selector


def _lf_text(value: Any, *, maximum: int, allow_empty: bool = False) -> str:
    text = _utf8_size(value, maximum=maximum, allow_empty=allow_empty)
    if "\r" in text:
        _fail("parse_failed")
    return text


def _validate_manifest(protocol: Mapping[str, Any], manifest: Any) -> dict[str, Any]:
    definition = protocol["reducer_manifest"]
    item = _exact_keys(manifest, definition["exact_keys"])
    if item["schema"] != definition["schema"]:
        _fail("source_drift")
    _string(item["scenario_id"], pattern=_IDENTIFIER)
    _string(item["arm"], pattern=_IDENTIFIER)
    maximum_entries = definition["max_entries_per_list"]
    maximum_selector = definition["max_selector_bytes"]
    maximum_probe = definition["max_probe_bytes"]

    owner_slots = _sorted_unique_strings(
        item["owner_slots"], pattern=_IDENTIFIER, maximum_items=maximum_entries
    )
    owner_set = set(owner_slots)
    list_shapes = definition["list_shapes"]
    probe_ids: set[str] = set()
    for field in ("contract_probes", "inventory_probes"):
        values = item[field]
        if not isinstance(values, list) or len(values) > maximum_entries:
            _fail("parse_failed")
        sort_keys: list[tuple[bytes, bytes]] = []
        for raw in values:
            probe = _exact_keys(raw, list_shapes[field])
            probe_id = _string(probe["probe_id"], pattern=_IDENTIFIER)
            owner_slot = _string(probe["owner_slot"], pattern=_IDENTIFIER)
            if owner_slot not in owner_set or probe_id in probe_ids:
                _fail("source_drift")
            selector = _safe_selector(probe["selector"], maximum_selector)
            _lf_text(probe["needle_lf"], maximum=maximum_probe)
            probe_ids.add(probe_id)
            sort_keys.append((selector.encode("ascii"), probe_id.encode("ascii")))
        if sort_keys != sorted(set(sort_keys)):
            _fail("parse_failed")

    selectors = item["maintenance_selectors"]
    if not isinstance(selectors, list) or len(selectors) > maximum_entries:
        _fail("parse_failed")
    selector_keys: list[tuple[bytes, bytes]] = []
    for raw in selectors:
        selector = _exact_keys(raw, list_shapes["maintenance_selectors"])
        owner_slot = _string(selector["owner_slot"], pattern=_IDENTIFIER)
        if owner_slot not in owner_set:
            _fail("source_drift")
        path = _safe_selector(selector["selector"], maximum_selector)
        selector_keys.append((owner_slot.encode("ascii"), path.encode("ascii")))
    if selector_keys != sorted(set(selector_keys)):
        _fail("parse_failed")

    fixtures = item["fixture_probes"]
    if not isinstance(fixtures, list) or len(fixtures) > maximum_entries:
        _fail("parse_failed")
    fixture_keys: list[tuple[bytes, bytes]] = []
    for raw in fixtures:
        fixture = _exact_keys(raw, list_shapes["fixture_probes"])
        probe_id = _string(fixture["probe_id"], pattern=_IDENTIFIER)
        if probe_id in probe_ids:
            _fail("source_drift")
        selector = _safe_selector(fixture["selector"], maximum_selector)
        qualified = _string(fixture["qualified_name"], minimum=1, maximum=240)
        if not qualified.isascii() or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._"
            for character in qualified
        ):
            _fail("parse_failed")
        probe_ids.add(probe_id)
        fixture_keys.append((selector.encode("ascii"), probe_id.encode("ascii")))
    if fixture_keys != sorted(set(fixture_keys)):
        _fail("parse_failed")

    labels = item["verification_labels"]
    if not isinstance(labels, list) or not 1 <= len(labels) <= maximum_entries:
        _fail("parse_failed")
    label_names: list[str] = []
    for raw in labels:
        label = _exact_keys(raw, list_shapes["verification_labels"])
        name = _string(label["label"], pattern=_IDENTIFIER)
        if label["kind"] not in definition["verification_kinds"]:
            _fail("parse_failed")
        label_names.append(name)
    if label_names != sorted(set(label_names), key=lambda value: value.encode("ascii")):
        _fail("parse_failed")

    target = _exact_keys(item["target_change"], definition["target_change_keys"])
    _safe_selector(target["selector"], maximum_selector)
    before = _lf_text(
        target["before_lf"],
        maximum=definition["max_target_change_bytes"],
        allow_empty=True,
    )
    after = _lf_text(
        target["after_lf"],
        maximum=definition["max_target_change_bytes"],
        allow_empty=True,
    )
    if before == after:
        _fail("source_drift")
    verification_label = _string(target["verification_label"], pattern=_IDENTIFIER)
    if verification_label not in label_names:
        _fail("source_drift")
    return dict(item)


def validate_control_bundle(
    protocol: Mapping[str, Any],
    bundle: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    arm: str,
    trial_id: str,
) -> dict[str, str]:
    """Validate one ephemeral control bundle and return only its digests."""

    if unit not in {"M20.3", "M20.4"}:
        _fail("source_drift")
    scenario_id = _string(scenario_id, pattern=_IDENTIFIER)
    arm = _string(arm, pattern=_IDENTIFIER)
    trial_id = _string(trial_id, pattern=_IDENTIFIER)
    if trial_id != f"{scenario_id}.{arm}.01":
        _fail("source_drift")
    expected_attempts = {
        (row[1], row[2])
        for row in derive_inventory(protocol, unit)
        if row[4] == "fresh_agent_trial"
    }
    if (scenario_id, trial_id) not in expected_attempts:
        _fail("source_drift")
    definition = protocol["control_bundle"]
    item = _exact_keys(bundle, definition["exact_keys"])
    workload = _utf8_size(
        item["workload"],
        maximum=definition["max_workload_bytes"],
        allow_empty=False,
    )
    _utf8_size(
        item["delivered_request"],
        maximum=definition["max_delivered_request_bytes"],
        allow_empty=False,
    )
    _utf8_size(
        item["neutral_clarification"],
        maximum=definition["max_neutral_clarification_bytes"],
        allow_empty=False,
    )
    manifest = _validate_manifest(protocol, item["reducer_manifest"])
    if manifest["scenario_id"] != scenario_id or manifest["arm"] != arm:
        _fail("source_drift")
    encoded = canonical_json_bytes(item)
    if len(encoded) > definition["max_bundle_bytes"]:
        _fail("cap_exceeded")
    return {
        "workload_digest": hashlib.sha256(workload.encode("utf-8")).hexdigest(),
        "control_digest": hashlib.sha256(encoded).hexdigest(),
    }


def reduce_task_show_state(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Project exactly the public task-show fields approved for M20."""

    root = _exact_keys(
        envelope,
        {"ok", "command", "project_id", "data", "warnings", "errors"},
    )
    if root["ok"] is not True or root["command"] != "task.show":
        _fail("parse_failed")
    data = _exact_keys(
        root["data"],
        {
            "task",
            "events",
            "suggested_next_action",
            "review_evidence",
            "handoff_summary",
            "contract",
            "latest_checkpoint",
            "effort_advisory_enabled",
            "completion_history",
        },
    )
    task = data["task"]
    review = _exact_keys(
        data["review_evidence"],
        {
            "target",
            "gate",
            "counts",
            "blocking_findings",
            "recent_receipts",
            "recent_findings",
        },
    )
    counts = _exact_keys(
        review["counts"],
        {
            "receipts_total",
            "receipts_current_generation",
            "changes_requested_current_generation",
            "open_high",
            "open_medium",
            "open_low",
        },
    )
    gate = _exact_keys(
        review["gate"],
        {
            "review_tier",
            "required_independent_passes",
            "qualifying_independent_passes",
            "fallback_kind",
            "satisfied",
        },
    )
    handoffs = _exact_keys(
        data["handoff_summary"],
        {"pending_handoff", "handed_off", "handoff_withdrawn_by_user"},
    )
    history = _exact_keys(
        data["completion_history"],
        {
            "total",
            "returned_count",
            "truncated",
            "legacy_history_incomplete",
            "cycles",
        },
    )
    if not isinstance(task, dict) or "status" not in task or "review_target_generation" not in task:
        _fail("parse_failed")
    if not isinstance(data["contract"], dict) or "revision" not in data["contract"]:
        _fail("parse_failed")
    status = task["status"]
    total = _integer(history["total"])
    attestation: bool | None = None
    if status == "done" and total:
        cycles = history["cycles"]
        if (
            not isinstance(cycles, list)
            or not cycles
            or history["returned_count"] < 1
        ):
            _fail("parse_failed")
        attestation = cycles[0]["verification_attestation"]
        if attestation is not None:
            _boolean(attestation)
    current_verification_keys = {
        "current_verification",
        "verification_run",
        "verification_result",
    }
    detail = (
        "unknown"
        if current_verification_keys & set(data)
        else "absent"
    )
    result = {
        "task_status": status,
        "contract_revision": data["contract"]["revision"],
        "review_generation": task["review_target_generation"],
        "receipts_current": counts["receipts_current_generation"],
        "qualifying_passes": gate["qualifying_independent_passes"],
        "changes_requested_current": counts[
            "changes_requested_current_generation"
        ],
        "findings_open_high": counts["open_high"],
        "findings_open_medium": counts["open_medium"],
        "findings_open_low": counts["open_low"],
        "handoffs_pending": handoffs["pending_handoff"],
        "handoffs_delivered": handoffs["handed_off"],
        "handoffs_withdrawn": handoffs["handoff_withdrawn_by_user"],
        "completion_cycles": total,
        "verification_attestation": attestation,
        "verification_detail": detail,
    }
    _validate_state_object(result, prefix="state", unknown_fields=set())
    return result


@dataclass(frozen=True)
class InvocationCapture:
    phase: str
    command_leaf: str
    duration_ms: int
    timed_out: bool
    exit_code: int | None
    stdout_bytes: int
    stderr_bytes: int
    envelope: Mapping[str, Any] | None


class _BoundedPipe:
    def __init__(self, stream: Any, cap: int) -> None:
        self.stream = stream
        self.cap = cap
        self.total = 0
        self.prefix = bytearray()
        self.overflow_non_whitespace = False
        self.error: BaseException | None = None

    def read(self) -> None:
        try:
            while True:
                block = self.stream.read(65_536)
                if not block:
                    break
                self.total += len(block)
                remaining = self.cap - len(self.prefix)
                if remaining > 0:
                    self.prefix.extend(block[:remaining])
                discarded = block[max(remaining, 0) :]
                if discarded.strip(b" \t\r\n"):
                    self.overflow_non_whitespace = True
        except BaseException as error:  # pragma: no cover - defensive thread path
            self.error = error
        finally:
            self.stream.close()


def _parse_bounded_json_object(
    capture: _BoundedPipe,
    *,
    timed_out: bool,
) -> Mapping[str, Any] | None:
    if timed_out:
        return None
    if capture.total > capture.cap and capture.overflow_non_whitespace:
        _fail("parse_failed")
    parsed = _load_json_bytes(bytes(capture.prefix))
    if not isinstance(parsed, dict):
        _fail("parse_failed")
    return parsed


def _minimal_environment() -> dict[str, str]:
    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    return environment


def _invoke_taskgov(
    project_root: Path,
    phase: str,
    command_leaf: str,
    arguments: Sequence[str] = (),
) -> InvocationCapture:
    """Invoke only the isolated baseline taskgov entrypoint with bounded pipes."""

    if phase not in _PHASES or command_leaf not in _COMMAND_LEAVES:
        _fail("source_drift")
    if any(not isinstance(argument, str) for argument in arguments):
        _fail("parse_failed")
    script = project_root / "task-governance-tool" / "scripts" / "taskgov.py"
    leaf = command_leaf.split(".")
    argv = [
        sys.executable,
        "-I",
        "-S",
        str(script),
        *leaf,
        *arguments,
        "--repo",
        str(project_root),
        "--json",
    ]
    start = time.monotonic_ns()
    try:
        process = subprocess.Popen(
            argv,
            cwd=project_root,
            env=_minimal_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError:
        _fail("source_missing")
    assert process.stdout is not None and process.stderr is not None
    stdout = _BoundedPipe(process.stdout, MAX_STREAM_BYTES)
    stderr = _BoundedPipe(process.stderr, MAX_STREAM_BYTES)
    threads = (
        threading.Thread(target=stdout.read, daemon=True),
        threading.Thread(target=stderr.read, daemon=True),
    )
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        exit_code = process.wait(timeout=TASKGOV_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
        exit_code = None
    for thread in threads:
        thread.join()
    if stdout.error is not None or stderr.error is not None:
        _fail("source_missing")
    duration_ms = min(
        300_000,
        max(0, (time.monotonic_ns() - start) // 1_000_000),
    )
    envelope = _parse_bounded_json_object(stdout, timed_out=timed_out)
    return InvocationCapture(
        phase=phase,
        command_leaf=command_leaf,
        duration_ms=300_000 if timed_out else min(duration_ms, 299_999),
        timed_out=timed_out,
        exit_code=exit_code,
        stdout_bytes=stdout.total,
        stderr_bytes=stderr.total,
        envelope=envelope,
    )


def _codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        _fail("parse_failed")
    codes: set[str] = set()
    for raw in value:
        item = _exact_keys(raw, {"code", "message"})
        codes.add(_string(item["code"], pattern=_CODE))
    if len(codes) > 16:
        _fail("parse_failed")
    return sorted(codes, key=lambda code: code.encode("ascii"))


def _reduce_cli_captures(
    captures: Sequence[InvocationCapture],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    operations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for index, capture in enumerate(captures):
        if capture.timed_out:
            result = "timeout"
            warnings: list[str] = []
            errors: list[str] = []
        else:
            envelope = capture.envelope
            if envelope is None:
                _fail("parse_failed")
            root = _exact_keys(
                envelope,
                {"ok", "command", "project_id", "data", "warnings", "errors"},
            )
            warnings = _codes(root["warnings"])
            errors = _codes(root["errors"])
            if capture.exit_code == 0 and root["ok"] is True and not errors:
                result = "success"
            elif capture.exit_code == 1 and root["ok"] is False and errors:
                result = "input_error"
            elif capture.exit_code == 2 and root["ok"] is False and errors:
                result = "service_error"
            else:
                _fail("parse_failed")
        stdout_bytes = min(capture.stdout_bytes, MAX_STREAM_BYTES)
        stderr_bytes = min(capture.stderr_bytes, MAX_STREAM_BYTES)
        for name, original in (
            ("stdout_bytes", capture.stdout_bytes),
            ("stderr_bytes", capture.stderr_bytes),
        ):
            if original > MAX_STREAM_BYTES:
                unknowns.append(
                    {
                        "field": f"operations.{index}.{name}",
                        "reasons": ["cap_exceeded"],
                    }
                )
        operations.append(
            {
                "ordinal": index + 1,
                "phase": capture.phase,
                "command_leaf": capture.command_leaf,
                "duration_ms": capture.duration_ms,
                "duration_capped": capture.timed_out,
                "result": result,
                "exit_code": capture.exit_code,
                "warning_codes": warnings,
                "error_codes": errors,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
            }
        )
    return {"operations": operations}, unknowns


def _success_data(capture: InvocationCapture, *, command: str) -> Mapping[str, Any]:
    envelope = capture.envelope
    if (
        capture.timed_out
        or capture.exit_code != 0
        or envelope is None
        or envelope.get("ok") is not True
        or envelope.get("command") != command
        or not isinstance(envelope.get("data"), dict)
    ):
        _fail("source_drift")
    return envelope["data"]


def _safe_git_environment() -> dict[str, str]:
    environment = _minimal_environment()
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_CONFIG_COUNT": "4",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "NUL" if os.name == "nt" else "/dev/null",
            "GIT_CONFIG_KEY_1": "commit.gpgSign",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "tag.gpgSign",
            "GIT_CONFIG_VALUE_2": "false",
            "GIT_CONFIG_KEY_3": "core.fsmonitor",
            "GIT_CONFIG_VALUE_3": "false",
        }
    )
    return environment


def _git(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    timeout: int = 60,
) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root}",
                "-C",
                str(repo_root),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            shell=False,
            env=_safe_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("source_missing")
    if result.returncode != 0 or len(result.stdout) > MAX_STREAM_BYTES:
        _fail("source_drift")
    try:
        return result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        _fail("parse_failed")


def _prepare_git_fixture(project_root: Path) -> None:
    _git(project_root, ("init", "--quiet"))
    _git(
        project_root,
        (
            "-c",
            "user.name=M20 Fixture",
            "-c",
            "user.email=m20@example.invalid",
            "add",
            "--all",
        ),
    )
    _git(
        project_root,
        (
            "-c",
            "user.name=M20 Fixture",
            "-c",
            "user.email=m20@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "M20 isolated baseline",
        ),
    )


def _commit_git_fixture(project_root: Path) -> str:
    _git(
        project_root,
        (
            "-c",
            "user.name=M20 Fixture",
            "-c",
            "user.email=m20@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "M20 reviewed fixture",
        ),
    )
    revision = _git(project_root, ("rev-parse", "--verify", "HEAD"))
    if _HEX_40.fullmatch(revision) is None:
        _fail("parse_failed")
    return revision


class _ScenarioSession:
    def __init__(self, project_root: Path, scenario: Mapping[str, Any]) -> None:
        self.project_root = project_root
        self.scenario = scenario
        self.expected = tuple(tuple(item) for item in scenario["operations"])
        self.captures: list[InvocationCapture] = []

    def invoke(
        self,
        phase: str,
        command_leaf: str,
        *arguments: str,
    ) -> InvocationCapture:
        position = len(self.captures)
        if position >= len(self.expected) or self.expected[position] != (
            phase,
            command_leaf,
        ):
            _fail("source_drift")
        capture = _invoke_taskgov(
            self.project_root,
            phase,
            command_leaf,
            arguments,
        )
        self.captures.append(capture)
        return capture

    def finish(self) -> None:
        if len(self.captures) != len(self.expected):
            _fail("source_drift")


def _task_add_arguments(title_code: str, review_tier: int) -> tuple[str, ...]:
    return (
        "--title",
        f"M20 isolated {title_code}",
        "--kind",
        "optional",
        "--priority",
        "normal",
        "--status",
        "in_progress",
        "--review-tier",
        str(review_tier),
        "--verification",
        "Fixed offline verification",
        "--contract-scope",
        "One isolated M20 fixture",
        "--contract-acceptance",
        "Fixed scenario reaches its declared state",
        "--contract-constraints",
        "No network or external project mutation",
    )


def _task_id_from_add(capture: InvocationCapture) -> str:
    data = _success_data(capture, command="task.add")
    task = data.get("task")
    if not isinstance(task, dict):
        _fail("parse_failed")
    return _string(task.get("task_id"), pattern=_TASK_ID)


def _show_state(
    session: _ScenarioSession,
    task_id: str,
) -> dict[str, Any]:
    capture = session.invoke("diagnose", "task.show", task_id, "--read-only")
    _success_data(capture, command="task.show")
    assert capture.envelope is not None
    return reduce_task_show_state(capture.envelope)


def _set_diff_target(
    session: _ScenarioSession,
    task_id: str,
    marker: str,
) -> None:
    revision = "sha256:" + hashlib.sha256(marker.encode("ascii")).hexdigest()
    capture = session.invoke(
        "review",
        "review.target.set",
        task_id,
        "--kind",
        "diff_fingerprint",
        "--revision",
        revision,
    )
    _success_data(capture, command="review.target.set")


def _add_pass(
    session: _ScenarioSession,
    task_id: str,
    reviewer: str,
) -> None:
    capture = session.invoke(
        "review",
        "review.receipt.add",
        task_id,
        "--reviewer",
        reviewer,
        "--kind",
        "independent",
        "--verdict",
        "pass",
        "--summary",
        "No blocking findings",
    )
    _success_data(capture, command="review.receipt.add")


def _completion_arguments(
    task_id: str,
    *,
    revision: str | None = None,
    check: bool,
) -> tuple[str, ...]:
    arguments: list[str] = [
        task_id,
        "--verification-complete",
        "--review-complete",
    ]
    if revision is None:
        arguments.append("--commit-not-required")
    else:
        arguments.extend(
            (
                "--completion-evidence-kind",
                "git_commit",
                "--completion-revision",
                revision,
            )
        )
    if check:
        arguments.extend(("--check", "--read-only"))
    return tuple(arguments)


def _complete(
    session: _ScenarioSession,
    task_id: str,
    *,
    revision: str | None = None,
    check: bool,
) -> None:
    capture = session.invoke(
        "complete",
        "task.complete",
        *_completion_arguments(task_id, revision=revision, check=check),
    )
    data = _success_data(capture, command="task.complete")
    if check and (
        data.get("ready") is not True
        or data.get("blocking_codes") != []
    ):
        _fail("source_drift")


def _scenario_tier1(
    session: _ScenarioSession,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _success_data(session.invoke("setup", "setup"), command="setup")
    task_id = _task_id_from_add(
        session.invoke(
            "execute",
            "task.add",
            *_task_add_arguments("tier1", 1),
        )
    )
    before = _show_state(session, task_id)
    _set_diff_target(session, task_id, "m20-tier1-a")
    _add_pass(session, task_id, "m20-tier1-reviewer")
    _complete(session, task_id, check=True)
    _complete(session, task_id, check=False)
    after = _show_state(session, task_id)
    return before, after


def _scenario_tier2(
    session: _ScenarioSession,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _success_data(session.invoke("setup", "setup"), command="setup")
    task_id = _task_id_from_add(
        session.invoke(
            "execute",
            "task.add",
            *_task_add_arguments("tier2", 2),
        )
    )
    before = _show_state(session, task_id)
    fixture = session.project_root / "m20-fixture.txt"
    fixture.write_text("reviewed fixture\n", encoding="utf-8", newline="\n")
    _git(session.project_root, ("add", "--", "m20-fixture.txt"))
    target = session.invoke(
        "review",
        "review.target.set",
        task_id,
        "--kind",
        "git_snapshot",
    )
    target_data = _success_data(target, command="review.target.set")
    target_task = target_data.get("task")
    if (
        not isinstance(target_task, dict)
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(target_task.get("review_target_value", "")),
        )
        is None
        or _HEX_40.fullmatch(
            str(target_task.get("review_target_base_revision", ""))
        )
        is None
    ):
        _fail("source_drift")
    _add_pass(session, task_id, "m20-tier2-reviewer-a")
    _add_pass(session, task_id, "m20-tier2-reviewer-b")
    revision = _commit_git_fixture(session.project_root)
    _complete(session, task_id, revision=revision, check=True)
    _complete(session, task_id, revision=revision, check=False)
    after = _show_state(session, task_id)
    return before, after


def _scenario_pause(
    session: _ScenarioSession,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _success_data(session.invoke("setup", "setup"), command="setup")
    task_id = _task_id_from_add(
        session.invoke(
            "execute",
            "task.add",
            *_task_add_arguments("pause", 1),
        )
    )
    _success_data(
        session.invoke(
            "execute",
            "task.edit",
            task_id,
            "--status",
            "paused",
            "--pause-reason",
            "Waiting for safe continuation",
        ),
        command="task.edit",
    )
    before = _show_state(session, task_id)
    _success_data(
        session.invoke(
            "activate",
            "task.edit",
            task_id,
            "--status",
            "in_progress",
        ),
        command="task.edit",
    )
    after = _show_state(session, task_id)
    return before, after


def _scenario_handoff(
    session: _ScenarioSession,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _success_data(session.invoke("setup", "setup"), command="setup")
    task_id = _task_id_from_add(
        session.invoke(
            "execute",
            "task.add",
            *_task_add_arguments("handoff", 1),
        )
    )
    before = _show_state(session, task_id)
    _success_data(
        session.invoke(
            "execute",
            "handoff.record",
            task_id,
            "--summary",
            "Optional hardening is outside accepted scope",
            "--rationale",
            "Current acceptance does not require it",
            "--occurrence-id",
            "m20-handoff-01",
        ),
        command="handoff.record",
    )
    _success_data(
        session.invoke(
            "execute",
            "task.edit",
            task_id,
            "--add-note",
            "Continued the accepted bounded work",
        ),
        command="task.edit",
    )
    after = _show_state(session, task_id)
    return before, after


def _scenario_reopen(
    session: _ScenarioSession,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _success_data(session.invoke("setup", "setup"), command="setup")
    task_id = _task_id_from_add(
        session.invoke(
            "execute",
            "task.add",
            *_task_add_arguments("reopen", 1),
        )
    )
    _set_diff_target(session, task_id, "m20-reopen-a")
    _add_pass(session, task_id, "m20-reopen-reviewer-a")
    _complete(session, task_id, check=True)
    _complete(session, task_id, check=False)
    before = _show_state(session, task_id)
    _success_data(
        session.invoke(
            "activate",
            "task.edit",
            task_id,
            "--status",
            "in_progress",
            "--reopen-reason",
            "Acceptance changed",
        ),
        command="task.edit",
    )
    _set_diff_target(session, task_id, "m20-reopen-b")
    _add_pass(session, task_id, "m20-reopen-reviewer-b")
    _complete(session, task_id, check=True)
    _complete(session, task_id, check=False)
    after = _show_state(session, task_id)
    return before, after


_SCENARIO_RUNNERS: dict[
    str,
    Callable[[_ScenarioSession], tuple[dict[str, Any], dict[str, Any]]],
] = {
    "gov_tier1_commitless": _scenario_tier1,
    "gov_tier2_snapshot": _scenario_tier2,
    "gov_pause_resume": _scenario_pause,
    "gov_handoff_continue": _scenario_handoff,
    "gov_reopen_rereview": _scenario_reopen,
}


def _matches_expected(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return all(key in actual and actual[key] == value for key, value in expected.items())


def _run_harness_scenario(
    protocol: Mapping[str, Any],
    project_root: Path,
    scenario: Mapping[str, Any],
) -> list[dict[str, Any]]:
    scenario_id = str(scenario["scenario_id"])
    if scenario_id == "gov_tier2_snapshot":
        _prepare_git_fixture(project_root)
    session = _ScenarioSession(project_root, scenario)
    before, after = _SCENARIO_RUNNERS[scenario_id](session)
    session.finish()
    if not _matches_expected(before, scenario["before"]) or not _matches_expected(
        after, scenario["after"]
    ):
        _fail("source_drift")
    cli_payload, cli_unknowns = _reduce_cli_captures(session.captures)
    trial_id = str(scenario["trial_id"])
    return [
        build_observation(
            protocol,
            unit="M20.2",
            scenario_id=scenario_id,
            trial_id=trial_id,
            evidence_class="machine_observed",
            channel="cli_invocation",
            record_key="cli",
            payload=cli_payload,
            unknowns=cli_unknowns,
        ),
        build_observation(
            protocol,
            unit="M20.2",
            scenario_id=scenario_id,
            trial_id=trial_id,
            evidence_class="machine_observed",
            channel="state_projection",
            record_key="state_pair",
            payload={"before": before, "after": after},
        ),
    ]


def _excluded_rows(
    protocol: Mapping[str, Any],
    rows: Iterable[Sequence[Any]],
    reason: str,
) -> list[dict[str, Any]]:
    if reason not in _INVALIDATING_REASONS:
        reason = "source_drift"
    records: list[dict[str, Any]] = []
    for row in rows:
        unit, scenario_id, trial_id, evidence_class, channel, record_key = row
        records.append(
            build_observation(
                protocol,
                unit=unit,
                scenario_id=scenario_id,
                trial_id=trial_id,
                evidence_class=evidence_class,
                channel=channel,
                record_key=record_key,
                payload=None,
                unknowns=[{"field": "payload", "reasons": [reason]}],
            )
        )
    return records


def _sanitize_records_for_retention(
    protocol: Mapping[str, Any],
    rows: Iterable[Sequence[Any]],
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    expected_rows = tuple(tuple(row) for row in rows)
    try:
        retained = [dict(record) for record in records]
        for record in retained:
            validate_observation(protocol, record)
        _privacy_check(canonical_json_bytes(retained))
    except M20ObservationError as error:
        return _excluded_rows(protocol, expected_rows, error.code)
    except (KeyError, TypeError, UnicodeError, ValueError, RecursionError):
        return _excluded_rows(protocol, expected_rows, "parse_failed")
    expected_ids = [observation_id(protocol, row) for row in expected_rows]
    actual_ids = [str(record["observation_id"]) for record in retained]
    if (
        len(actual_ids) != len(expected_ids)
        or len(actual_ids) != len(set(actual_ids))
        or set(actual_ids) != set(expected_ids)
    ):
        return _excluded_rows(protocol, expected_rows, "source_drift")
    return retained


def _history_observations(
    protocol: Mapping[str, Any],
    reconstruction: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    try:
        by_attempt: dict[str, list[dict[str, Any]]] = {}
        for scenario_id, metrics in reconstruction.items():
            records: list[dict[str, Any]] = []
            for raw in metrics:
                metric = str(raw["metric"])
                reasons = sorted(
                    {
                        reason
                        for unknown in raw["unknowns"]
                        for reason in unknown["reasons"]
                    },
                    key=lambda value: value.encode("ascii"),
                )
                invalidating = bool(set(reasons) & _INVALIDATING_REASONS)
                records.append(
                    build_observation(
                        protocol,
                        unit="M20.2",
                        scenario_id=scenario_id,
                        trial_id=None,
                        evidence_class="historically_reconstructed",
                        channel="task_git_reconstruction",
                        record_key=metric,
                        payload=(
                            None
                            if invalidating
                            else {
                                "metric": metric,
                                "value": raw["value"],
                                "coverage": raw["coverage"],
                                "references": raw["references"],
                            }
                        ),
                        unknowns=(
                            [{"field": "payload", "reasons": reasons}]
                            if invalidating
                            else raw["unknowns"]
                        ),
                    )
                )
            by_attempt[f"{scenario_id}.retrospective.01"] = records
        return by_attempt
    except M20ObservationError:
        raise
    except (
        AttributeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
        RecursionError,
    ):
        _fail("parse_failed")


def _reconstruct_m19_once(
    repo_root: Path,
    protocol: Mapping[str, Any],
) -> Mapping[str, Sequence[Mapping[str, Any]]]:
    try:
        from tools.m20_history import M20HistoryError, reconstruct_m19
    except ImportError:
        _fail("source_missing")
    try:
        return reconstruct_m19(
            repo_root,
            repo_root / "task-governance-tool",
            dict(protocol),
        )
    except M20HistoryError as error:
        _fail(error.code)


def _collect_retrospective_once(
    repo_root: Path,
    protocol: Mapping[str, Any],
    journal: AttemptJournal,
) -> None:
    m20 = protocol["m20_2"]
    inventory = derive_inventory(protocol, "M20.2")
    attempt_ids = tuple(
        f"{cohort['scenario_id']}.retrospective.01"
        for cohort in m20["retrospective_cohorts"]
    )
    statuses = {
        attempt_id: journal.status(attempt_id)
        for attempt_id in attempt_ids
    }
    if set(statuses.values()) == {"reduced"}:
        return
    if not all(status is None for status in statuses.values()):
        _fail("source_drift")
    journal.start_many(attempt_ids)
    failure_code: str | None = None
    try:
        reconstruction = _reconstruct_m19_once(repo_root, protocol)
        records_by_attempt = _history_observations(protocol, reconstruction)
        for cohort, attempt_id in zip(
            m20["retrospective_cohorts"],
            attempt_ids,
            strict=True,
        ):
            rows = [
                row
                for row in inventory
                if row[1] == cohort["scenario_id"]
            ]
            records_by_attempt[attempt_id] = _sanitize_records_for_retention(
                protocol,
                rows,
                records_by_attempt[attempt_id],
            )
    except M20ObservationError as error:
        failure_code = error.code
    except (
        AttributeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
        RecursionError,
    ):
        failure_code = "parse_failed"
    if failure_code is not None:
        records_by_attempt = {}
        for cohort, attempt_id in zip(
            m20["retrospective_cohorts"],
            attempt_ids,
            strict=True,
        ):
            rows = [
                row
                for row in inventory
                if row[1] == cohort["scenario_id"]
            ]
            records_by_attempt[attempt_id] = _excluded_rows(
                protocol,
                rows,
                failure_code,
            )
    journal.finish_many(records_by_attempt)


def _extract_baseline(archive_path: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            if len(members) > 4096:
                _fail("cap_exceeded")
            total = 0
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    _fail("source_drift")
                total += max(0, member.size)
                if total > 67_108_864:
                    _fail("cap_exceeded")
            archive.extractall(destination, members=members, filter="data")
    except M20ObservationError:
        raise
    except (OSError, tarfile.TarError):
        _fail("source_missing")


def _materialize_baseline_archive(
    protocol: Mapping[str, Any],
    temp_root: Path,
) -> Path:
    baseline = protocol["authority"]["baseline_revision"]
    resolved = _git(
        DEFAULT_REPO_ROOT,
        ("rev-parse", "--verify", f"{baseline}^{{commit}}"),
    )
    if resolved != baseline:
        _fail("source_drift")
    archive_path = temp_root / "baseline.tar"
    _git(
        DEFAULT_REPO_ROOT,
        (
            "archive",
            "--format=tar",
            f"--output={archive_path}",
            baseline,
        ),
        timeout=120,
    )
    if not archive_path.is_file() or archive_path.is_symlink():
        _fail("source_missing")
    return archive_path


def _fixed_output(
    repo_root: Path,
    relative: str,
    *,
    require_ignored: bool,
) -> Path:
    root = Path(repo_root).resolve(strict=True)
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        _fail("source_drift")
    unresolved = root / relative_path
    current = unresolved
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    while current != root:
        if os.path.lexists(current):
            try:
                details = current.lstat()
            except OSError:
                _fail("source_missing")
            if current.is_symlink() or bool(
                getattr(details, "st_file_attributes", 0) & reparse
            ):
                _fail("source_drift")
        current = current.parent
    candidate = unresolved.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        _fail("source_drift")
    if require_ignored:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={root}",
                    "-C",
                    str(root),
                    "check-ignore",
                    "--no-index",
                    "--quiet",
                    "--",
                    str(candidate),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
                shell=False,
                env=_safe_git_environment(),
            )
        except (OSError, subprocess.TimeoutExpired):
            _fail("source_missing")
        if result.returncode != 0:
            _fail("source_drift")
    return candidate


def _read_collection_receipt(
    repo_root: Path,
    protocol: Mapping[str, Any],
) -> dict[str, Any] | None:
    path = _fixed_output(
        repo_root,
        str(protocol["m20_2"]["receipt_path"]),
        require_ignored=False,
    )
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        _fail("source_drift")
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("source_missing")
    if not raw or len(raw) > 4096:
        _fail("source_drift")
    if hashlib.sha256(raw).hexdigest() != M20_2_RECEIPT_SHA256:
        _fail("source_drift")
    receipt = _exact_keys(
        _load_json_bytes(raw),
        {
            "schema",
            "unit",
            "authority_revision",
            "baseline_revision",
            "protocol_sha256",
            "status",
            "artifact_status",
            "retirement_revision",
            "record_count",
            "corpus_bytes",
            "corpus_sha256",
            "eligible_records",
            "partial_records",
            "excluded_records",
            "outcome",
        },
    )
    if raw != canonical_json_bytes(receipt) + b"\n":
        _fail("source_drift")
    if (
        receipt["schema"] != COLLECTION_RECEIPT_SCHEMA
        or receipt["unit"] != "M20.2"
        or receipt["authority_revision"]
        != protocol["authority"]["authority_revision"]
        or receipt["baseline_revision"]
        != protocol["authority"]["baseline_revision"]
        or receipt["protocol_sha256"] != PROTOCOL_CANONICAL_SHA256
        or receipt["status"] != "closed"
    ):
        _fail("source_drift")
    if receipt["artifact_status"] == "retained":
        if receipt["retirement_revision"] is not None:
            _fail("source_drift")
    elif receipt["artifact_status"] == "retired":
        _string(receipt["retirement_revision"], pattern=_HEX_40)
    else:
        _fail("source_drift")
    for field in (
        "record_count",
        "corpus_bytes",
        "eligible_records",
        "partial_records",
        "excluded_records",
    ):
        _integer(receipt[field], maximum=MAX_SIGNED_32)
    _string(receipt["corpus_sha256"], pattern=_HEX_64)
    _string(receipt["outcome"], pattern=_IDENTIFIER)
    if receipt["record_count"] != sum(
        receipt[field]
        for field in ("eligible_records", "partial_records", "excluded_records")
    ):
        _fail("source_drift")
    if (
        receipt["record_count"]
        != protocol["bounds"]["unit_record_counts"]["M20.2"]
        or not 1 <= receipt["corpus_bytes"] <= protocol["bounds"]["unit_corpus_bytes"]
        or receipt["outcome"] != "retrospective_launch_failed"
        or (
            receipt["eligible_records"],
            receipt["partial_records"],
            receipt["excluded_records"],
        )
        != (10, 0, 36)
    ):
        _fail("source_drift")
    return dict(receipt)


def _match_collection_receipt(
    receipt: Mapping[str, Any],
    raw: bytes,
    records: Sequence[Mapping[str, Any]] | None,
) -> None:
    counts = {"eligible": 0, "partial": 0, "excluded": 0}
    if records is not None:
        for record in records:
            counts[str(record["eligibility"])] += 1
    if (
        receipt["corpus_bytes"] != len(raw)
        or receipt["corpus_sha256"] != hashlib.sha256(raw).hexdigest()
        or receipt["record_count"] != (0 if records is None else len(records))
        or receipt["eligible_records"] != counts["eligible"]
        or receipt["partial_records"] != counts["partial"]
        or receipt["excluded_records"] != counts["excluded"]
    ):
        _fail("source_drift")


def _privacy_check(raw: bytes) -> None:
    forbidden = (
        b"Authorization:",
        b"Bearer ",
        b"raw_prompt",
        b"stdout_content",
        b"stderr_content",
        b"/Users/",
        b"\\\\Users\\\\",
    )
    if any(value in raw for value in forbidden):
        _fail("contaminated")


def _product_boundary_check(protocol: Mapping[str, Any]) -> None:
    baseline = protocol["authority"]["baseline_revision"]
    authority = protocol["authority"]["authority_revision"]
    if _git(DEFAULT_REPO_ROOT, ("rev-parse", "--verify", f"{authority}^{{commit}}")) != authority:
        _fail("source_drift")
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={DEFAULT_REPO_ROOT}",
                "-C",
                str(DEFAULT_REPO_ROOT),
                "diff",
                "--quiet",
                baseline,
                "--",
                "task-governance-tool",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
            shell=False,
            env=_safe_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("source_missing")
    if result.returncode != 0:
        _fail("product_boundary_changed")


def _validate_retained_corpus(
    protocol: Mapping[str, Any],
    raw: bytes,
) -> list[dict[str, Any]] | None:
    parsed = _load_json_bytes(raw)
    if isinstance(parsed, dict):
        failure = _exact_keys(
            parsed,
            {"schema", "unit", "reason", "record_count", "candidate_bytes"},
        )
        if (
            failure["schema"] != CORPUS_FAILURE_SCHEMA
            or failure["unit"] != "M20.2"
            or failure["reason"] != "cap_exceeded"
            or type(failure["record_count"]) is not int
            or type(failure["candidate_bytes"]) is not int
            or failure["record_count"] < 0
            or failure["candidate_bytes"] < 0
            or canonical_json_bytes(failure) != raw
        ):
            _fail("source_drift")
        return None
    if not isinstance(parsed, list):
        _fail("parse_failed")
    canonical = serialize_corpus(protocol, "M20.2", parsed)
    if canonical != raw:
        _fail("source_drift")
    _privacy_check(raw)
    return parsed


def _interrupted_records(
    protocol: Mapping[str, Any],
    attempt_id: str,
) -> list[dict[str, Any]]:
    inventory = derive_inventory(protocol, "M20.2")
    if attempt_id.endswith(".harness.01"):
        rows = [row for row in inventory if row[2] == attempt_id]
    elif attempt_id.endswith(".retrospective.01"):
        scenario_id = attempt_id[: -len(".retrospective.01")]
        rows = [
            row
            for row in inventory
            if row[1] == scenario_id and row[2] is None
        ]
    else:
        _fail("source_drift")
    if not rows:
        _fail("source_drift")
    return _excluded_rows(protocol, rows, "source_missing")


def _collect_harness_scenario_once(
    protocol: Mapping[str, Any],
    archive_path: Path,
    temp_root: Path,
    scenario: Mapping[str, Any],
    inventory: Sequence[Sequence[Any]],
    journal: AttemptJournal,
) -> None:
    attempt_id = str(scenario["trial_id"])
    status = journal.status(attempt_id)
    if status == "reduced":
        return
    if status is not None:
        _fail("interrupted_attempt")
    rows = [row for row in inventory if row[1] == scenario["scenario_id"]]
    journal.start(attempt_id)
    try:
        with tempfile.TemporaryDirectory(
            prefix="m20-scenario-",
            dir=temp_root,
        ) as raw_project:
            project_root = Path(raw_project)
            _extract_baseline(archive_path, project_root)
            records = _run_harness_scenario(
                protocol,
                project_root,
                scenario,
            )
    except M20ObservationError as error:
        records = _excluded_rows(protocol, rows, error.code)
    except OSError:
        records = _excluded_rows(protocol, rows, "source_missing")
    except (
        AttributeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
        RecursionError,
    ):
        records = _excluded_rows(protocol, rows, "parse_failed")
    records = _sanitize_records_for_retention(
        protocol,
        rows,
        records,
    )
    journal.finish(attempt_id, records)


def _collect_m20_2_locked(
    root: Path,
    protocol: Mapping[str, Any],
    corpus_path: Path,
    journal_path: Path,
) -> dict[str, Any]:
    m20 = protocol["m20_2"]
    if corpus_path.exists():
        _fail("artifact_exists")
    journal = AttemptJournal(journal_path, protocol)
    # A started attempt is intentionally unrecoverable.  On resumption it is
    # finalized as excluded without reopening its source or launching it twice.
    existing = journal._read()
    interrupted = {
        attempt_id: _interrupted_records(protocol, attempt_id)
        for attempt_id, state in existing["attempts"].items()
        if state["status"] == "started"
    }
    if interrupted:
        journal.finish_many(interrupted)

    inventory = derive_inventory(protocol, "M20.2")
    with tempfile.TemporaryDirectory(prefix="m20-baseline-") as raw_temp:
        temp_root = Path(raw_temp)
        archive_path = _materialize_baseline_archive(protocol, temp_root)
        for scenario in m20["harness_scenarios"]:
            _collect_harness_scenario_once(
                protocol,
                archive_path,
                temp_root,
                scenario,
                inventory,
                journal,
            )

    _collect_retrospective_once(root, protocol, journal)

    records = journal.reduced_records()
    raw = serialize_corpus(protocol, "M20.2", records)
    _privacy_check(raw)
    _atomic_write(corpus_path, raw)
    try:
        retained = corpus_path.read_bytes()
    except OSError:
        _fail("source_missing")
    _validate_retained_corpus(protocol, retained)
    return {
        "record_count": 0 if retained.startswith(b"{") else len(records),
        "corpus_bytes": len(retained),
        "corpus_sha256": hashlib.sha256(retained).hexdigest(),
    }


def collect_m20_2(
    repo_root: Path = DEFAULT_REPO_ROOT,
) -> dict[str, Any]:
    """Run the one-shot fixed collection only while no terminal receipt exists."""

    root = Path(repo_root).resolve(strict=True)
    if root != DEFAULT_REPO_ROOT.resolve(strict=True):
        _fail("source_drift")
    protocol = load_protocol(root)
    if _read_collection_receipt(root, protocol) is not None:
        _fail("collection_closed")
    m20 = protocol["m20_2"]
    corpus_path = _fixed_output(root, m20["corpus_path"], require_ignored=True)
    journal_path = _fixed_output(root, m20["journal_path"], require_ignored=True)
    lock_path = _fixed_output(root, m20["lock_path"], require_ignored=True)
    with CollectionLock(lock_path):
        if _read_collection_receipt(root, protocol) is not None:
            _fail("collection_closed")
        _product_boundary_check(protocol)
        return _collect_m20_2_locked(
            root,
            protocol,
            corpus_path,
            journal_path,
        )


def check_m20_2(
    repo_root: Path = DEFAULT_REPO_ROOT,
) -> dict[str, Any]:
    """Validate the framework and, when present, the retained corpus."""

    root = Path(repo_root).resolve(strict=True)
    if root != DEFAULT_REPO_ROOT.resolve(strict=True):
        _fail("source_drift")
    protocol = load_protocol(root)
    _product_boundary_check(protocol)
    receipt = _read_collection_receipt(root, protocol)
    corpus_path = _fixed_output(
        root,
        protocol["m20_2"]["corpus_path"],
        require_ignored=True,
    )
    if not corpus_path.exists():
        if receipt is not None and receipt["artifact_status"] == "retired":
            return {
                "artifact_status": "retired",
                "record_count": receipt["record_count"],
                "corpus_bytes": receipt["corpus_bytes"],
                "corpus_sha256": receipt["corpus_sha256"],
            }
        if receipt is not None:
            _fail("source_missing")
        return {
            "artifact_status": "absent",
            "record_count": 0,
            "corpus_bytes": 0,
            "corpus_sha256": None,
        }
    try:
        raw = corpus_path.read_bytes()
    except OSError:
        _fail("source_missing")
    records = _validate_retained_corpus(protocol, raw)
    if receipt is None:
        _fail("source_drift")
    if receipt["artifact_status"] != "retained":
        _fail("source_drift")
    _match_collection_receipt(receipt, raw, records)
    return {
        "artifact_status": "failure" if records is None else "retained",
        "record_count": 0 if records is None else len(records),
        "corpus_bytes": len(raw),
        "corpus_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or run the fixed repository-only M20.2 harness."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--collect-m20-2", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = (
            collect_m20_2()
            if args.collect_m20_2
            else check_m20_2()
        )
    except M20ObservationError as error:
        print(
            canonical_json_bytes({"ok": False, "error_code": error.code}).decode(
                "utf-8"
            ),
            file=sys.stderr,
        )
        return 2
    output = {
        "ok": True,
        "mode": "collect_m20_2" if args.collect_m20_2 else "check",
        **result,
    }
    print(canonical_json_bytes(output).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
