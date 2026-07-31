"""Deterministic repository-only controller for M20 fresh collections.

The controller joins bounded trial-local JSON inputs to the fixed observation
reducers and :mod:`tools.m20_fresh_lifecycle`.  It never launches a subject,
deletes trial material, or retains raw paths, prompts, logs, or state bodies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


sys.dont_write_bytecode = True
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(DEFAULT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_REPO_ROOT))

from tools.m20_fresh_lifecycle import (
    FreshCollectionLifecycle,
    check_fresh_collection,
    trial_root_digest,
    trial_root_identity_digest,
    trial_root_parent_digest,
)
from tools.m20_fresh_measurement import (
    BoundarySnapshot,
    _safe_environment,
    build_state_pair_observation,
    capture_boundary_snapshot,
    reduce_m20_3_trial,
    reduce_m20_4_measurement,
    reduce_verification_log,
)
from tools.m20_observation import (
    CollectionLock,
    M20ObservationError,
    _exact_keys,
    _excluded_rows,
    _fail,
    _load_json_bytes,
    build_observation,
    canonical_json_bytes,
    derive_inventory,
    load_m20_4_episode_plan,
    load_protocol,
    validate_control_bundle,
)
from tools.m20_trial_observer import (
    LOG_SCHEMA,
    MAX_TASKGOV_OPERATIONS,
    MAX_VERIFICATION_STEPS,
    PUBLIC_COMMAND_LEAVES,
    TrialObserver,
    TrialObserverError,
    VERIFICATION_KINDS,
)


OBSERVER_SNAPSHOT_SCHEMA = "m20-fresh-observer-snapshot-v1"
ASSESSMENT_SCHEMA = "m20-fresh-assessment-v1"
BOUNDARY_SNAPSHOT_SCHEMA = "m20-fresh-boundary-snapshot-v1"
MAX_CONTROL_BYTES = 262_144
MAX_LOG_BYTES = 262_144
MAX_STATE_BYTES = 1_048_576
MAX_ASSESSMENT_BYTES = 32_768
MAX_BOUNDARY_BYTES = 32_768
MAX_SIGNED_32 = 2_147_483_647
_SAFE_RELATIVE = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9._-]{1,64}\Z")
_UNKNOWN_FIELD = re.compile(r"[a-z][a-z0-9_.]{0,95}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail("parse_failed")
    return value


def _integer(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _fail("parse_failed")
    return value


def _sha256(value: Any) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        _fail("parse_failed")
    return value


def _object_sha256(value: Mapping[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_baseline_head(root: Path, expected: str) -> None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=10,
            env=_safe_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("source_missing")
    try:
        observed = result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeError:
        _fail("parse_failed")
    if result.returncode != 0 or observed != expected:
        _fail("contaminated")


def _is_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        _fail("source_missing")
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0) & marker
    )


def _safe_trial_root(lifecycle: FreshCollectionLifecycle, value: Path) -> Path:
    candidate = Path(value)
    # Reuse the lifecycle's broad-root checks without persisting the path.
    roots = lifecycle._extra_source_roots((candidate,))
    root = roots[0]
    if not root.exists() or not root.is_dir() or _is_reparse(root):
        _fail("source_missing" if not root.exists() else "source_drift")
    return root


def _trial_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or _SAFE_RELATIVE.fullmatch(relative) is None:
        _fail("unsafe_source_path")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.as_posix() != relative
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _fail("unsafe_source_path")
    lexical = root.joinpath(*pure.parts)
    current = lexical
    while current != root:
        if os.path.lexists(current) and _is_reparse(current):
            _fail("unsafe_source_path")
        current = current.parent
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        _fail("source_missing")
    if not resolved.is_file() or _is_reparse(resolved):
        _fail("unsafe_source_path")
    return resolved


def _canonical_object(root: Path, relative: str, maximum: int) -> dict[str, Any]:
    path = _trial_file(root, relative)
    try:
        before = path.stat()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(maximum + 1)
        after = path.stat()
    except OSError:
        _fail("source_missing")
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_size,
        item.st_mtime_ns,
    )
    if identity(before) != identity(opened) or identity(opened) != identity(after):
        _fail("source_drift")
    if not stat.S_ISREG(opened.st_mode):
        _fail("unsafe_source_path")
    if not raw or len(raw) > maximum or opened.st_size > maximum:
        _fail("cap_exceeded" if len(raw) > maximum else "parse_failed")
    value = _load_json_bytes(raw)
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        _fail("source_drift")
    return value


def _trial_output(root: Path, relative: str) -> Path:
    """Resolve one absent regular-file destination below an existing trial root."""

    if not isinstance(relative, str) or _SAFE_RELATIVE.fullmatch(relative) is None:
        _fail("unsafe_source_path")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.as_posix() != relative
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _fail("unsafe_source_path")
    lexical = root.joinpath(*pure.parts)
    if os.path.lexists(lexical):
        _fail("artifact_exists")
    current = lexical.parent
    while current != root:
        if not current.exists() or not current.is_dir() or _is_reparse(current):
            _fail("unsafe_source_path")
        current = current.parent
    try:
        lexical.parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        _fail("unsafe_source_path")
    return lexical


def _write_trial_object(root: Path, relative: str, value: Mapping[str, Any]) -> None:
    destination = _trial_output(root, relative)
    payload = canonical_json_bytes(value)
    if not payload or len(payload) > MAX_BOUNDARY_BYTES:
        _fail("cap_exceeded")
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        _fail("artifact_exists")
    except OSError:
        _fail("source_missing")


def _plan_for_trial(
    episode_plan: Mapping[str, Any],
    *,
    scenario_id: str,
    trial_id: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in episode_plan["plans"]
        if item["scenario_id"] == scenario_id and item["trial_id"] == trial_id
    ]
    if len(matches) != 1:
        _fail("source_drift")
    return dict(matches[0])


def _m20_4_path(trial_id: str, leaf: str) -> str:
    """Return one fixed Git-admin path excluded from worktree measurement."""

    safe_trial = _identifier(trial_id)
    if not isinstance(leaf, str) or _SAFE_RELATIVE.fullmatch(leaf) is None:
        _fail("unsafe_source_path")
    pure = PurePosixPath(leaf)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("unsafe_source_path")
    return f".git/m20/{safe_trial}/{pure.as_posix()}"


def _m20_4_config_binding(
    config: Mapping[str, Any],
    control: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    scenario_id: str,
    trial_id: str,
    arm: str,
) -> dict[str, str]:
    configured_slots = tuple(
        str(item["task_slot"]) for item in config["task_slots"]
    )
    if (
        config["unit"] != "M20.4"
        or config["scenario_id"] != scenario_id
        or config["trial_id"] != trial_id
        or config["arm"] != arm
        or plan["arm"] != arm
        or configured_slots != tuple(plan["task_slots"])
    ):
        _fail("source_drift")
    manifest_labels = tuple(
        (str(item["label"]), str(item["kind"]))
        for item in control["reducer_manifest"]["verification_labels"]
    )
    config_labels = tuple(
        sorted(
            (
                (str(item["label"]), str(item["kind"]))
                for item in config["verification_labels"]
            ),
            key=lambda row: row[0].encode("ascii"),
        )
    )
    if config_labels != manifest_labels:
        _fail("source_drift")
    return {
        str(item["task_slot"]): str(item["task_id"])
        for item in config["task_slots"]
    }


def _boundary_payload(snapshot: BoundarySnapshot, *, arm: str) -> dict[str, Any]:
    return {
        "schema": BOUNDARY_SNAPSHOT_SCHEMA,
        "unit": "M20.4",
        "scenario_id": snapshot.scenario_id,
        "trial_id": snapshot.trial_id,
        "arm": arm,
        "boundary_id": snapshot.boundary_id,
        "files": snapshot.files,
        "modules": snapshot.modules,
        "lines": snapshot.lines,
        "metric_unknowns": [
            {"field": field, "reason": reason}
            for field, reason in snapshot.metric_unknowns
        ],
        "task_states": [
            {
                "task_slot": slot,
                "task_id": task_id,
                "contract_revision": contract_revision,
                "review_generation": review_generation,
            }
            for slot, task_id, contract_revision, review_generation in snapshot.task_states
        ],
        "observer_log_position": snapshot.observer_log_position,
        "observer_verification_position": snapshot.observer_verification_position,
    }


def _boundary_snapshot(
    value: Mapping[str, Any],
    *,
    scenario_id: str,
    trial_id: str,
    arm: str,
    boundary_id: str,
    task_ids: Mapping[str, str],
) -> BoundarySnapshot:
    item = _exact_keys(
        value,
        {
            "schema",
            "unit",
            "scenario_id",
            "trial_id",
            "arm",
            "boundary_id",
            "files",
            "modules",
            "lines",
            "metric_unknowns",
            "task_states",
            "observer_log_position",
            "observer_verification_position",
        },
    )
    if (
        item["schema"] != BOUNDARY_SNAPSHOT_SCHEMA
        or item["unit"] != "M20.4"
        or item["scenario_id"] != scenario_id
        or item["trial_id"] != trial_id
        or item["arm"] != arm
        or item["boundary_id"] != boundary_id
    ):
        _fail("source_drift")
    unknown_items = item["metric_unknowns"]
    if not isinstance(unknown_items, list) or len(unknown_items) > 3:
        _fail("parse_failed")
    unknowns: list[tuple[str, str]] = []
    for raw in unknown_items:
        unknown = _exact_keys(raw, {"field", "reason"})
        field = unknown["field"]
        reason = unknown["reason"]
        if field != "lines" or reason not in {
            "not_observable",
            "cap_exceeded",
        }:
            _fail("parse_failed")
        unknowns.append((field, reason))
    if (
        len({field for field, _reason in unknowns}) != len(unknowns)
        or unknowns != sorted(unknowns, key=lambda row: row[0].encode("ascii"))
    ):
        _fail("parse_failed")
    reasons = dict(unknowns)
    metrics: dict[str, int | None] = {}
    for field in ("files", "modules", "lines"):
        raw_metric = item[field]
        reason = reasons.get(field)
        if raw_metric is None:
            if reason is None or reason == "cap_exceeded":
                _fail("parse_failed")
            metrics[field] = None
        else:
            metric = _integer(raw_metric, MAX_SIGNED_32)
            if reason is not None and (
                reason != "cap_exceeded" or metric != MAX_SIGNED_32
            ):
                _fail("parse_failed")
            metrics[field] = metric
    raw_states = item["task_states"]
    if not isinstance(raw_states, list) or len(raw_states) != len(task_ids):
        _fail("parse_failed")
    states: list[tuple[str, str, int, int]] = []
    for raw in raw_states:
        state = _exact_keys(
            raw,
            {
                "task_slot",
                "task_id",
                "contract_revision",
                "review_generation",
            },
        )
        slot = _identifier(state["task_slot"])
        task_id = state["task_id"]
        if task_ids.get(slot) != task_id:
            _fail("source_drift")
        states.append(
            (
                slot,
                task_id,
                _integer(state["contract_revision"], 9_223_372_036_854_775_807),
                _integer(state["review_generation"], 9_223_372_036_854_775_807),
            )
        )
    if tuple(slot for slot, *_rest in states) != tuple(task_ids):
        _fail("source_drift")
    return BoundarySnapshot(
        scenario_id=scenario_id,
        trial_id=trial_id,
        boundary_id=boundary_id,
        files=metrics["files"],
        modules=metrics["modules"],
        lines=metrics["lines"],
        metric_unknowns=tuple(unknowns),
        task_states=tuple(states),
        observer_log_position=_integer(item["observer_log_position"], MAX_TASKGOV_OPERATIONS),
        observer_verification_position=_integer(
            item["observer_verification_position"], MAX_VERIFICATION_STEPS
        ),
    )


def _identity(
    value: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str,
    arm: str,
) -> None:
    if (
        value["unit"] != unit
        or value["scenario_id"] != scenario_id
        or value["trial_id"] != trial_id
        or value["arm"] != arm
    ):
        _fail("source_drift")


def _input_unknowns(
    value: Any,
    *,
    allowed_fields: set[str],
    allowed_reasons: set[str],
    maximum: int,
) -> dict[str, str]:
    if not isinstance(value, list) or len(value) > maximum:
        _fail("parse_failed")
    result: dict[str, str] = {}
    ordered: list[str] = []
    for raw in value:
        item = _exact_keys(raw, {"field", "reason"})
        field = item["field"]
        reason = item["reason"]
        if (
            not isinstance(field, str)
            or _UNKNOWN_FIELD.fullmatch(field) is None
            or field not in allowed_fields
            or reason not in allowed_reasons
            or field in result
        ):
            _fail("parse_failed")
        result[field] = reason
        ordered.append(field)
    if ordered != sorted(ordered, key=lambda item: item.encode("ascii")):
        _fail("parse_failed")
    return result


def _observer_log(
    value: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str,
    arm: str,
) -> dict[str, Any]:
    log = _exact_keys(
        value,
        {
            "schema",
            "unit",
            "scenario_id",
            "trial_id",
            "arm",
            "taskgov",
            "verifications",
        },
    )
    if log["schema"] != LOG_SCHEMA:
        _fail("source_drift")
    _identity(
        log,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        arm=arm,
    )
    operations = log["taskgov"]
    if not isinstance(operations, list) or len(operations) > MAX_TASKGOV_OPERATIONS:
        _fail("cap_exceeded")
    for raw in operations:
        item = _exact_keys(
            raw, {"command_leaf", "task_slot", "duration_ms", "result"}
        )
        if item["command_leaf"] not in PUBLIC_COMMAND_LEAVES:
            _fail("source_drift")
        if item["task_slot"] is not None:
            _identifier(item["task_slot"])
        duration = _integer(item["duration_ms"], 300_000)
        if item["result"] == "timeout":
            if duration != 300_000:
                _fail("parse_failed")
        elif item["result"] in {"success", "input_error", "service_error"}:
            if duration > 299_999:
                _fail("parse_failed")
        else:
            _fail("parse_failed")
    steps = log["verifications"]
    if not isinstance(steps, list) or len(steps) > MAX_VERIFICATION_STEPS:
        _fail("cap_exceeded")
    reduce_verification_log(steps)
    return dict(log)


def _observer_snapshot(
    value: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str,
    arm: str,
) -> dict[str, Any]:
    snapshot = _exact_keys(
        value,
        {
            "schema",
            "unit",
            "scenario_id",
            "trial_id",
            "arm",
            "outcome",
            "reference_opens",
            "clarification_turns",
            "manual_inputs",
            "reviewer_invocations",
            "unknowns",
        },
    )
    if snapshot["schema"] != OBSERVER_SNAPSHOT_SCHEMA:
        _fail("source_drift")
    _identity(
        snapshot,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        arm=arm,
    )
    if snapshot["outcome"] not in {
        "completed",
        "blocked",
        "paused",
        "handed_off",
        "failed",
        "inconclusive",
    }:
        _fail("parse_failed")
    caps = (
        ("reference_opens", 256),
        ("clarification_turns", 16),
        ("manual_inputs", 32),
        ("reviewer_invocations", 8),
    )
    unknowns = _input_unknowns(
        snapshot["unknowns"],
        allowed_fields={field for field, _maximum in caps},
        allowed_reasons={"not_observable", "cap_exceeded"},
        maximum=len(caps),
    )
    for field, maximum in caps:
        value = snapshot[field]
        reason = unknowns.get(field)
        if value is None:
            if reason != "not_observable":
                _fail("parse_failed")
        else:
            _integer(value, maximum)
            if reason == "not_observable":
                _fail("parse_failed")
            if reason == "cap_exceeded" and value != maximum:
                _fail("parse_failed")
    return dict(snapshot)


def _assessment(
    value: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str,
    arm: str,
) -> dict[str, Any]:
    assessment = _exact_keys(
        value,
        {
            "schema",
            "unit",
            "scenario_id",
            "trial_id",
            "arm",
            "assessment_kind",
            "assessment",
            "unknowns",
        },
    )
    if assessment["schema"] != ASSESSMENT_SCHEMA:
        _fail("source_drift")
    _identity(
        assessment,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        arm=arm,
    )
    expected_kind = (
        "verification_proportionality" if unit == "M20.3" else "split_pressure"
    )
    if assessment["assessment_kind"] != expected_kind:
        _fail("source_drift")
    if not isinstance(assessment["assessment"], dict):
        _fail("parse_failed")
    if unit == "M20.3":
        allowed_unknowns = {
            f"assessment.{field}"
            for field in (
                "distinct_risks",
                "new_cases",
                "redundant_responsibilities",
                "verification_fact_codes",
                "manual_reentry_fact_codes",
                "responsibility_pattern_codes",
                "reuse",
                "instruction_fit",
                "minimal_receipt_fit",
            )
        }
    else:
        episodes = assessment["assessment"].get("episodes")
        if not isinstance(episodes, list) or len(episodes) > 8:
            _fail("parse_failed")
        allowed_unknowns = {
            f"assessment.episodes.{index}.{field}"
            for index in range(len(episodes))
            for field in (
                "acceptance_independent",
                "verification_independent",
                "commit_independent",
                "completion_independent",
            )
        }
    _input_unknowns(
        assessment["unknowns"],
        allowed_fields=allowed_unknowns,
        allowed_reasons={"observer_uncertain"},
        maximum=min(128, len(allowed_unknowns)),
    )
    return dict(assessment)


def build_attestation_observation(
    protocol: Mapping[str, Any],
    *,
    unit: str,
    scenario_id: str,
    trial_id: str,
    arm: str,
    control_bundle: Mapping[str, Any],
    observer_log: Mapping[str, Any],
    observer_snapshot: Mapping[str, Any],
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one schema-validated attestation without retaining raw inputs."""

    digests = validate_control_bundle(
        protocol,
        control_bundle,
        unit=unit,
        scenario_id=scenario_id,
        arm=arm,
        trial_id=trial_id,
    )
    log = _observer_log(
        observer_log,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        arm=arm,
    )
    snapshot = _observer_snapshot(
        observer_snapshot,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        arm=arm,
    )
    judged = _assessment(
        assessment,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        arm=arm,
    )
    payload = {
        "cohort": "fresh_baseline_v1",
        "arm": arm,
        **digests,
        "outcome": snapshot["outcome"],
        "reference_opens": snapshot["reference_opens"],
        "clarification_turns": snapshot["clarification_turns"],
        "manual_inputs": snapshot["manual_inputs"],
        "governance_invocations": len(log["taskgov"]),
        "reviewer_invocations": snapshot["reviewer_invocations"],
        "assessment_kind": judged["assessment_kind"],
        "assessment": judged["assessment"],
    }
    unknowns = [
        {"field": item["field"], "reasons": [item["reason"]]}
        for item in (*snapshot["unknowns"], *judged["unknowns"])
    ]
    return build_observation(
        protocol,
        unit=unit,
        scenario_id=scenario_id,
        trial_id=trial_id,
        evidence_class="observer_attested",
        channel="fresh_agent_trial",
        record_key="attestation",
        payload=payload,
        unknowns=unknowns,
    )


def _validate_handoff_candidates(
    measurement: Mapping[str, Any],
    attestation: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """Fail before retention when the sole M20.4 control records conflict."""

    try:
        assessed = attestation["payload"]["assessment"]["episodes"]
        measured = measurement["payload"]["data"]["episodes"]
        before = state["payload"]["before"]
        after = state["payload"]["after"]
    except (KeyError, TypeError):
        _fail("source_drift")
    if (
        not isinstance(assessed, list)
        or len(assessed) != 1
        or assessed[0]["cause"] != "out_of_scope_control"
        or assessed[0]["current_response"] != "handoff"
        or not isinstance(measured, list)
        or len(measured) != 1
    ):
        _fail("source_drift")
    episode = measured[0]
    if (
        episode["contract_revision_before"] != before["contract_revision"]
        or episode["contract_revision_after"] != after["contract_revision"]
        or episode["review_generation_before"] != before["review_generation"]
        or episode["review_generation_after"] != after["review_generation"]
    ):
        _fail("source_drift")


class FreshCollectionController:
    """Connect fixed trial-local inputs to a unit-bound lifecycle."""

    def __init__(self, repo_root: Path, unit: str) -> None:
        self.lifecycle = FreshCollectionLifecycle(repo_root, unit)
        self.unit = unit

    def start(self, attempt_ids: Iterable[str]) -> dict[str, Any]:
        if self.unit == "M20.4":
            _fail("control_commitment_required")
        attempts = tuple(attempt_ids)
        self.lifecycle.start_many(attempts)
        return {"unit": self.unit, "started_attempts": len(attempts)}

    def resume(self) -> dict[str, Any]:
        attempts = self.lifecycle.resume_started()
        return {"unit": self.unit, "terminalized_attempts": len(attempts)}

    def validate_control(
        self,
        *,
        trial_root: Path,
        scenario_id: str,
        trial_id: str,
        arm: str,
        control_path: str,
    ) -> dict[str, str]:
        """Validate one ephemeral control and return only its two digests."""

        self.lifecycle._ensure_open()
        protocol = load_protocol(self.lifecycle.repo_root)
        root = _safe_trial_root(self.lifecycle, trial_root)
        control = _canonical_object(root, control_path, MAX_CONTROL_BYTES)
        return validate_control_bundle(
            protocol,
            control,
            unit=self.unit,
            scenario_id=scenario_id,
            arm=arm,
            trial_id=trial_id,
        )

    def _m20_4_launch_commitment(
        self,
        *,
        protocol: Mapping[str, Any],
        episode_plan: Mapping[str, Any],
        trial_root: Path,
        scenario_id: str,
        trial_id: str,
        arm: str,
    ) -> dict[str, str]:
        root = _safe_trial_root(self.lifecycle, trial_root)
        _require_baseline_head(root, str(protocol["authority"]["baseline_revision"]))
        plan = _plan_for_trial(
            episode_plan, scenario_id=scenario_id, trial_id=trial_id
        )
        control = _canonical_object(
            root, _m20_4_path(trial_id, "control.json"), MAX_CONTROL_BYTES
        )
        digests = validate_control_bundle(
            protocol,
            control,
            unit=self.unit,
            scenario_id=scenario_id,
            arm=arm,
            trial_id=trial_id,
        )
        config_path = _m20_4_path(trial_id, "observer-config.json")
        config = _canonical_object(root, config_path, MAX_CONTROL_BYTES)
        try:
            observer = TrialObserver(
                root,
                config_path,
                _m20_4_path(trial_id, "observer-log.json"),
            )
        except TrialObserverError as error:
            _fail(error.code)
        _m20_4_config_binding(
            observer.config,
            control,
            plan,
            scenario_id=scenario_id,
            trial_id=trial_id,
            arm=arm,
        )
        return {
            **digests,
            "observer_config_digest": _object_sha256(config),
            "trial_root_digest": trial_root_digest(root),
            "trial_root_parent_digest": trial_root_parent_digest(root),
            "trial_root_identity_digest": trial_root_identity_digest(root),
        }

    def validate_m20_4_pair(
        self,
        *,
        scenario_id: str,
        broad_root: Path,
        bounded_root: Path,
    ) -> dict[str, str]:
        """Validate both fixed controls together without starting either arm."""

        if self.unit != "M20.4" or scenario_id == "sp_handoff_control":
            _fail("source_drift")
        self.lifecycle._ensure_open()
        protocol = load_protocol(self.lifecycle.repo_root)
        episode_plan = load_m20_4_episode_plan(
            protocol, self.lifecycle.repo_root
        )
        broad_trial = f"{scenario_id}.broad.01"
        bounded_trial = f"{scenario_id}.bounded.01"
        broad_plan = _plan_for_trial(
            episode_plan, scenario_id=scenario_id, trial_id=broad_trial
        )
        bounded_plan = _plan_for_trial(
            episode_plan, scenario_id=scenario_id, trial_id=bounded_trial
        )
        if broad_plan["arm"] != "broad" or bounded_plan["arm"] != "bounded":
            _fail("source_drift")
        roots = {
            "broad": _safe_trial_root(self.lifecycle, broad_root),
            "bounded": _safe_trial_root(self.lifecycle, bounded_root),
        }
        broad_resolved = roots["broad"]
        bounded_resolved = roots["bounded"]
        try:
            same_root = os.path.samefile(broad_resolved, bounded_resolved)
        except OSError:
            _fail("source_missing")
        if (
            same_root
            or broad_resolved in bounded_resolved.parents
            or bounded_resolved in broad_resolved.parents
        ):
            _fail("contaminated")
        results: dict[str, dict[str, str]] = {}
        for arm, trial_id in (("broad", broad_trial), ("bounded", bounded_trial)):
            results[arm] = self._m20_4_launch_commitment(
                protocol=protocol,
                episode_plan=episode_plan,
                trial_root=roots[arm],
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
            )
        if (
            results["broad"]["workload_digest"]
            != results["bounded"]["workload_digest"]
            or results["broad"]["trial_root_parent_digest"]
            != results["bounded"]["trial_root_parent_digest"]
        ):
            _fail("source_drift")
        return {
            "workload_digest": results["broad"]["workload_digest"],
            "broad_control_digest": results["broad"]["control_digest"],
            "bounded_control_digest": results["bounded"]["control_digest"],
        }

    def start_m20_4_pair(
        self,
        *,
        scenario_id: str,
        broad_root: Path,
        bounded_root: Path,
        reviewed_broad_digest: str,
        reviewed_bounded_digest: str,
    ) -> dict[str, Any]:
        """Bind reviewed controls and atomically start one frozen pair."""

        reviewed_broad = _sha256(reviewed_broad_digest)
        reviewed_bounded = _sha256(reviewed_bounded_digest)
        self.validate_m20_4_pair(
            scenario_id=scenario_id,
            broad_root=broad_root,
            bounded_root=bounded_root,
        )
        protocol = load_protocol(self.lifecycle.repo_root)
        episode_plan = load_m20_4_episode_plan(
            protocol, self.lifecycle.repo_root
        )
        attempts = (
            f"{scenario_id}.broad.01",
            f"{scenario_id}.bounded.01",
        )
        commitments = {
            attempt_id: self._m20_4_launch_commitment(
                protocol=protocol,
                episode_plan=episode_plan,
                trial_root=root,
                scenario_id=scenario_id,
                trial_id=attempt_id,
                arm=arm,
            )
            for attempt_id, arm, root in (
                (attempts[0], "broad", broad_root),
                (attempts[1], "bounded", bounded_root),
            )
        }
        if (
            commitments[attempts[0]]["control_digest"] != reviewed_broad
            or commitments[attempts[1]]["control_digest"] != reviewed_bounded
            or commitments[attempts[0]]["workload_digest"]
            != commitments[attempts[1]]["workload_digest"]
        ):
            _fail("source_drift")
        self.lifecycle.start_many(attempts, commitments=commitments)
        digests = {
            "workload_digest": commitments[attempts[0]]["workload_digest"],
            "broad_control_digest": commitments[attempts[0]]["control_digest"],
            "bounded_control_digest": commitments[attempts[1]]["control_digest"],
        }
        return {"unit": self.unit, "started_attempts": 2, **digests}

    def start_m20_4_control(
        self,
        *,
        trial_root: Path,
        reviewed_control_digest: str,
    ) -> dict[str, Any]:
        """Bind the reviewed Handoff control and start its sole attempt."""

        if self.unit != "M20.4":
            _fail("source_drift")
        scenario_id = "sp_handoff_control"
        trial_id = f"{scenario_id}.broad.01"
        reviewed = _sha256(reviewed_control_digest)
        self.lifecycle._ensure_open()
        protocol = load_protocol(self.lifecycle.repo_root)
        episode_plan = load_m20_4_episode_plan(
            protocol, self.lifecycle.repo_root
        )
        commitment = self._m20_4_launch_commitment(
            protocol=protocol,
            episode_plan=episode_plan,
            trial_root=trial_root,
            scenario_id=scenario_id,
            trial_id=trial_id,
            arm="broad",
        )
        if commitment["control_digest"] != reviewed:
            _fail("source_drift")
        self.lifecycle.start_many(
            (trial_id,), commitments={trial_id: commitment}
        )
        return {
            "unit": self.unit,
            "started_attempts": 1,
            "workload_digest": commitment["workload_digest"],
            "control_digest": commitment["control_digest"],
        }

    def _require_started(self, trial_id: str) -> None:
        protocol = load_protocol(self.lifecycle.repo_root)
        self.lifecycle._paths()
        journal = self.lifecycle._read_journal(protocol)
        state = journal["attempts"].get(trial_id)
        if state is None or state["status"] != "started":
            _fail("attempt_not_started")

    def _terminalize_started(
        self,
        protocol: Mapping[str, Any],
        trial_id: str,
        reason: str,
    ) -> None:
        rows = tuple(
            row
            for row in derive_inventory(protocol, self.unit)
            if row[2] == trial_id
        )
        if not rows:
            _fail("source_drift")
        self.lifecycle.claim(trial_id)
        self.lifecycle.finish(trial_id, _excluded_rows(protocol, rows, reason))

    def snapshot_task(
        self,
        *,
        trial_root: Path,
        observer_config_path: str,
        observer_log_path: str,
        task_slot: str,
        output_path: str,
    ) -> dict[str, str]:
        """Write one parent-only public Task snapshot without returning it."""

        if self.unit == "M20.4":
            _fail("source_drift")
        self.lifecycle._ensure_open()
        root = _safe_trial_root(self.lifecycle, trial_root)
        observer = TrialObserver(
            root,
            observer_config_path,
            observer_log_path,
        )
        config = observer.config
        if config["unit"] != self.unit:
            _fail("source_drift")
        trial_id = str(config["trial_id"])
        self._require_started(trial_id)
        safe_slot = _identifier(task_slot)
        observer.snapshot_task(safe_slot, output_path)
        return {"status": "written", "task_slot": safe_slot}

    def capture_m20_4_boundary(
        self,
        *,
        trial_root: Path,
        scenario_id: str,
        trial_id: str,
        arm: str,
        boundary_id: str,
    ) -> dict[str, str]:
        """Serialize all capture work for one no-rerun attempt."""

        if self.unit != "M20.4" or trial_id != f"{scenario_id}.{arm}.01":
            _fail("source_drift")
        self.lifecycle._ensure_open()
        self.lifecycle._paths()
        capture_lock = self.lifecycle.attempt_lock_path(trial_id)
        with CollectionLock(capture_lock):
            return self._capture_m20_4_boundary_locked(
                trial_root=trial_root,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
                boundary_id=boundary_id,
            )

    def _capture_m20_4_boundary_locked(
        self,
        *,
        trial_root: Path,
        scenario_id: str,
        trial_id: str,
        arm: str,
        boundary_id: str,
    ) -> dict[str, str]:
        """Capture one fixed safe boundary below the isolated Git admin area."""

        if self.unit != "M20.4" or trial_id != f"{scenario_id}.{arm}.01":
            _fail("source_drift")
        self.lifecycle._ensure_open()
        protocol = load_protocol(self.lifecycle.repo_root)
        episode_plan = load_m20_4_episode_plan(
            protocol, self.lifecycle.repo_root
        )
        plan = _plan_for_trial(
            episode_plan, scenario_id=scenario_id, trial_id=trial_id
        )
        if plan["arm"] != arm or boundary_id not in plan["boundaries"]:
            _fail("source_drift")
        self._require_started(trial_id)
        duplicate_boundary = False
        try:
            commitment = self.lifecycle.attempt_commitment(trial_id)
            root = _safe_trial_root(self.lifecycle, trial_root)
            if (
                trial_root_digest(root) != commitment["trial_root_digest"]
                or trial_root_identity_digest(root)
                != commitment["trial_root_identity_digest"]
            ):
                _fail("contaminated")
            boundary_index = tuple(plan["boundaries"]).index(boundary_id)
            for index, expected_boundary in enumerate(plan["boundaries"]):
                boundary_path = root / Path(
                    *_m20_4_path(
                        trial_id, f"boundaries/{expected_boundary}.json"
                    ).split("/")
                )
                exists = os.path.lexists(boundary_path)
                if index == boundary_index and exists:
                    duplicate_boundary = True
                if (index < boundary_index and not exists) or (
                    index >= boundary_index and exists
                ):
                    _fail("source_drift" if index < boundary_index else "artifact_exists")
            if boundary_index == 0:
                _require_baseline_head(
                    root, str(protocol["authority"]["baseline_revision"])
                )
            control = _canonical_object(
                root, _m20_4_path(trial_id, "control.json"), MAX_CONTROL_BYTES
            )
            control_digests = validate_control_bundle(
                protocol,
                control,
                unit=self.unit,
                scenario_id=scenario_id,
                arm=arm,
                trial_id=trial_id,
            )
            if (
                control_digests["workload_digest"]
                != commitment["workload_digest"]
                or control_digests["control_digest"]
                != commitment["control_digest"]
            ):
                _fail("source_drift")
            config_path = _m20_4_path(trial_id, "observer-config.json")
            config = _canonical_object(root, config_path, MAX_CONTROL_BYTES)
            if _object_sha256(config) != commitment["observer_config_digest"]:
                _fail("source_drift")
            try:
                observer = TrialObserver(
                    root,
                    config_path,
                    _m20_4_path(trial_id, "observer-log.json"),
                )
            except TrialObserverError as error:
                _fail(error.code)
            task_ids = _m20_4_config_binding(
                observer.config,
                control,
                plan,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
            )
            try:
                reported_log = observer.report()
            except TrialObserverError as error:
                _fail(error.code)
            log = _observer_log(
                reported_log,
                unit=self.unit,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
            )
            if boundary_index == 0 and (
                log["taskgov"] or log["verifications"]
            ):
                _fail("source_drift")
            slot_envelopes: dict[str, Mapping[str, Any]] = {}
            for slot in task_ids:
                try:
                    slot_envelopes[slot] = observer.snapshot_task(
                        slot,
                        _m20_4_path(
                            trial_id, f"states/{boundary_id}.{slot}.json"
                        ),
                    )
                except TrialObserverError as error:
                    _fail(error.code)
            snapshot = capture_boundary_snapshot(
                protocol,
                episode_plan,
                repo_root=root,
                scenario_id=scenario_id,
                trial_id=trial_id,
                control_bundle=control,
                boundary_id=boundary_id,
                slot_envelopes=slot_envelopes,
                observer_log_position=len(log["taskgov"]),
                observer_verification_position=len(log["verifications"]),
            )
            if boundary_index == 0 and (
                snapshot.files != 0
                or snapshot.modules != 0
                or snapshot.lines != 0
                or snapshot.metric_unknowns
            ):
                _fail("contaminated")
            if any(
                field != "lines"
                or reason not in {"not_observable", "cap_exceeded"}
                for field, reason in snapshot.metric_unknowns
            ):
                _fail("source_drift")
            _write_trial_object(
                root,
                _m20_4_path(trial_id, f"boundaries/{boundary_id}.json"),
                _boundary_payload(snapshot, arm=arm),
            )
        except M20ObservationError as error:
            if duplicate_boundary and error.code == "artifact_exists":
                raise
            self._terminalize_started(protocol, trial_id, error.code)
            raise
        except OSError:
            self._terminalize_started(protocol, trial_id, "source_missing")
            _fail("source_missing")
        except (
            AttributeError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
            RecursionError,
        ):
            self._terminalize_started(protocol, trial_id, "parse_failed")
            _fail("parse_failed")
        return {"status": "written", "boundary_id": boundary_id}

    def reduce_m20_3(
        self,
        *,
        trial_root: Path,
        scenario_id: str,
        trial_id: str,
        control_path: str,
        observer_config_path: str,
        observer_log_path: str,
        observer_snapshot_path: str,
        assessment_path: str,
        before_state_path: str,
        after_state_path: str,
    ) -> dict[str, Any]:
        if self.unit != "M20.3" or trial_id != f"{scenario_id}.baseline.01":
            _fail("source_drift")
        # Receipt refusal deliberately precedes trial-root or input access.
        self.lifecycle._ensure_open()
        protocol = load_protocol(self.lifecycle.repo_root)
        rows = tuple(
            row
            for row in derive_inventory(protocol, self.unit)
            if row[2] == trial_id
        )
        if len(rows) != 3:
            _fail("source_drift")
        # Claim under the lifecycle lock before touching the isolated trial.
        # A competing reducer therefore fails without reopening raw material.
        self.lifecycle.claim(trial_id)
        try:
            root = _safe_trial_root(self.lifecycle, trial_root)
            control = _canonical_object(root, control_path, MAX_CONTROL_BYTES)
            try:
                observer = TrialObserver(
                    root,
                    observer_config_path,
                    observer_log_path,
                )
            except TrialObserverError as error:
                _fail(error.code)
            config = observer.config
            if (
                config["unit"] != self.unit
                or config["scenario_id"] != scenario_id
                or config["trial_id"] != trial_id
                or config["arm"] != "baseline"
                or len(config["task_slots"]) != 1
            ):
                _fail("source_drift")
            expected_task_id = str(config["task_slots"][0]["task_id"])
            log = _observer_log(
                _canonical_object(root, observer_log_path, MAX_LOG_BYTES),
                unit=self.unit,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm="baseline",
            )
            snapshot = _canonical_object(
                root, observer_snapshot_path, MAX_ASSESSMENT_BYTES
            )
            judged = _canonical_object(root, assessment_path, MAX_ASSESSMENT_BYTES)
            before = _canonical_object(root, before_state_path, MAX_STATE_BYTES)
            after = _canonical_object(root, after_state_path, MAX_STATE_BYTES)
            state, measurement = reduce_m20_3_trial(
                protocol,
                repo_root=root,
                scenario_id=scenario_id,
                trial_id=trial_id,
                expected_task_id=expected_task_id,
                control_bundle=control,
                before_envelope=before,
                after_envelope=after,
                verification_log=log["verifications"],
            )
            attestation = build_attestation_observation(
                protocol,
                unit=self.unit,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm="baseline",
                control_bundle=control,
                observer_log=log,
                observer_snapshot=snapshot,
                assessment=judged,
            )
            candidates = (state, measurement, attestation)
        except M20ObservationError as error:
            self.lifecycle.finish(
                trial_id,
                _excluded_rows(protocol, rows, error.code),
            )
            raise
        except OSError:
            self.lifecycle.finish(
                trial_id,
                _excluded_rows(protocol, rows, "source_missing"),
            )
            _fail("source_missing")
        except (
            AttributeError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
            RecursionError,
        ):
            self.lifecycle.finish(
                trial_id,
                _excluded_rows(protocol, rows, "parse_failed"),
            )
            _fail("parse_failed")
        retained = self.lifecycle.finish(trial_id, candidates)
        counts = {"eligible": 0, "partial": 0, "excluded": 0}
        for record in retained:
            counts[record["eligibility"]] += 1
        return {
            "unit": self.unit,
            "trial_id": trial_id,
            "record_count": len(retained),
            "eligible_records": counts["eligible"],
            "partial_records": counts["partial"],
            "excluded_records": counts["excluded"],
        }

    def reduce_m20_4(
        self,
        *,
        trial_root: Path,
        scenario_id: str,
        trial_id: str,
        arm: str,
    ) -> dict[str, Any]:
        """Serialize final capture and one reduction for the same attempt."""

        if self.unit != "M20.4" or trial_id != f"{scenario_id}.{arm}.01":
            _fail("source_drift")
        self.lifecycle._ensure_open()
        self.lifecycle._paths()
        attempt_lock = self.lifecycle.attempt_lock_path(trial_id)
        with CollectionLock(attempt_lock):
            return self._reduce_m20_4_locked(
                trial_root=trial_root,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
            )

    def _reduce_m20_4_locked(
        self,
        *,
        trial_root: Path,
        scenario_id: str,
        trial_id: str,
        arm: str,
    ) -> dict[str, Any]:
        """Reduce one frozen M20.4 arm exactly once after an atomic claim."""

        if self.unit != "M20.4" or trial_id != f"{scenario_id}.{arm}.01":
            _fail("source_drift")
        # Receipt refusal and inventory derivation deliberately precede trial access.
        self.lifecycle._ensure_open()
        protocol = load_protocol(self.lifecycle.repo_root)
        episode_plan = load_m20_4_episode_plan(
            protocol, self.lifecycle.repo_root
        )
        plan = _plan_for_trial(
            episode_plan, scenario_id=scenario_id, trial_id=trial_id
        )
        rows = tuple(
            row
            for row in derive_inventory(protocol, self.unit)
            if row[2] == trial_id
        )
        expected_rows = 3 if scenario_id == "sp_handoff_control" else 2
        if plan["arm"] != arm or len(rows) != expected_rows:
            _fail("source_drift")
        # Claim under the lifecycle lock before opening any raw trial material.
        commitment = self.lifecycle.attempt_commitment(trial_id)
        self.lifecycle.claim(trial_id)
        try:
            root = _safe_trial_root(self.lifecycle, trial_root)
            if (
                trial_root_digest(root) != commitment["trial_root_digest"]
                or trial_root_identity_digest(root)
                != commitment["trial_root_identity_digest"]
            ):
                _fail("contaminated")
            control = _canonical_object(
                root, _m20_4_path(trial_id, "control.json"), MAX_CONTROL_BYTES
            )
            control_digests = validate_control_bundle(
                protocol,
                control,
                unit=self.unit,
                scenario_id=scenario_id,
                arm=arm,
                trial_id=trial_id,
            )
            if (
                control_digests["workload_digest"]
                != commitment["workload_digest"]
                or control_digests["control_digest"]
                != commitment["control_digest"]
            ):
                _fail("source_drift")
            config_path = _m20_4_path(trial_id, "observer-config.json")
            config_value = _canonical_object(
                root, config_path, MAX_CONTROL_BYTES
            )
            if (
                _object_sha256(config_value)
                != commitment["observer_config_digest"]
            ):
                _fail("source_drift")
            try:
                observer = TrialObserver(
                    root,
                    config_path,
                    _m20_4_path(trial_id, "observer-log.json"),
                )
            except TrialObserverError as error:
                _fail(error.code)
            config = observer.config
            task_ids = _m20_4_config_binding(
                config,
                control,
                plan,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
            )
            configured_slots = tuple(task_ids)
            try:
                reported_log = observer.report()
            except TrialObserverError as error:
                _fail(error.code)
            log = _observer_log(
                reported_log,
                unit=self.unit,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
            )
            snapshot = _canonical_object(
                root,
                _m20_4_path(trial_id, "observer-snapshot.json"),
                MAX_ASSESSMENT_BYTES,
            )
            judged = _canonical_object(
                root,
                _m20_4_path(trial_id, "assessment.json"),
                MAX_ASSESSMENT_BYTES,
            )
            normalized_assessment = _assessment(
                judged,
                unit=self.unit,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
            )
            raw_assessment_episodes = normalized_assessment["assessment"].get(
                "episodes"
            )
            if not isinstance(raw_assessment_episodes, list):
                _fail("parse_failed")
            try:
                assessment_episode_ids = tuple(
                    _identifier(item["episode_id"])
                    for item in raw_assessment_episodes
                )
            except (KeyError, TypeError):
                _fail("parse_failed")
            plan_episode_ids = tuple(
                str(item["episode_id"]) for item in plan["episodes"]
            )
            if assessment_episode_ids != plan_episode_ids:
                _fail("source_drift")
            boundaries = tuple(
                _boundary_snapshot(
                    _canonical_object(
                        root,
                        _m20_4_path(
                            trial_id, f"boundaries/{boundary_id}.json"
                        ),
                        MAX_BOUNDARY_BYTES,
                    ),
                    scenario_id=scenario_id,
                    trial_id=trial_id,
                    arm=arm,
                    boundary_id=boundary_id,
                    task_ids=task_ids,
                )
                for boundary_id in plan["boundaries"]
            )
            if (
                boundaries[0].observer_log_position != 0
                or boundaries[-1].observer_log_position
                != len(log["taskgov"])
                or boundaries[0].observer_verification_position != 0
                or boundaries[-1].observer_verification_position
                != len(log["verifications"])
                or tuple(
                    item.observer_verification_position for item in boundaries
                )
                != tuple(
                    sorted(
                        item.observer_verification_position
                        for item in boundaries
                    )
                )
            ):
                _fail("source_drift")
            measurement = reduce_m20_4_measurement(
                protocol,
                episode_plan,
                scenario_id=scenario_id,
                trial_id=trial_id,
                snapshots=boundaries,
                observer_log=log["taskgov"],
            )
            attestation = build_attestation_observation(
                protocol,
                unit=self.unit,
                scenario_id=scenario_id,
                trial_id=trial_id,
                arm=arm,
                control_bundle=control,
                observer_log=log,
                observer_snapshot=snapshot,
                assessment=judged,
            )
            candidates: tuple[dict[str, Any], ...] = (measurement, attestation)
            if scenario_id == "sp_handoff_control":
                slot = configured_slots[0]
                before = _canonical_object(
                    root,
                    _m20_4_path(
                        trial_id, f"states/{plan['boundaries'][0]}.{slot}.json"
                    ),
                    MAX_STATE_BYTES,
                )
                after = _canonical_object(
                    root,
                    _m20_4_path(
                        trial_id, f"states/{plan['boundaries'][-1]}.{slot}.json"
                    ),
                    MAX_STATE_BYTES,
                )
                state = build_state_pair_observation(
                    protocol,
                    unit=self.unit,
                    scenario_id=scenario_id,
                    trial_id=trial_id,
                    expected_task_id=task_ids[slot],
                    before_envelope=before,
                    after_envelope=after,
                )
                _validate_handoff_candidates(
                    measurement, attestation, state
                )
                candidates = (*candidates, state)
            retained = self.lifecycle.finish(trial_id, candidates)
        except M20ObservationError as error:
            self.lifecycle.finish(
                trial_id,
                _excluded_rows(protocol, rows, error.code),
            )
            raise
        except OSError:
            self.lifecycle.finish(
                trial_id,
                _excluded_rows(protocol, rows, "source_missing"),
            )
            _fail("source_missing")
        except (
            AttributeError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
            RecursionError,
        ):
            self.lifecycle.finish(
                trial_id,
                _excluded_rows(protocol, rows, "parse_failed"),
            )
            _fail("parse_failed")
        counts = {"eligible": 0, "partial": 0, "excluded": 0}
        for record in retained:
            counts[record["eligibility"]] += 1
        return {
            "unit": self.unit,
            "trial_id": trial_id,
            "record_count": len(retained),
            "eligible_records": counts["eligible"],
            "partial_records": counts["partial"],
            "excluded_records": counts["excluded"],
        }

    def finalize(self, extra_source_roots: Iterable[Path]) -> dict[str, Any]:
        return self.lifecycle.finalize(extra_source_roots=extra_source_roots)

    def check(self) -> dict[str, Any]:
        return check_fresh_collection(self.lifecycle.repo_root, self.unit)


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        _fail("input_error")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Run fixed repository-only M20 fresh collection steps.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--unit", required=True, choices=("M20.3", "M20.4"))
    commands = parser.add_subparsers(dest="mode", required=True, parser_class=_Parser)

    start = commands.add_parser("start")
    start.add_argument("--attempt", action="append", required=True)
    commands.add_parser("resume")
    commands.add_parser("check")
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--extra-source-root", action="append", default=[])

    validate_control = commands.add_parser("validate-control")
    validate_control.add_argument("--trial-root", required=True)
    validate_control.add_argument("--scenario", required=True)
    validate_control.add_argument("--trial", required=True)
    validate_control.add_argument("--arm", required=True)
    validate_control.add_argument("--control", required=True)

    validate_pair = commands.add_parser("validate-m20-4-pair")
    validate_pair.add_argument("--scenario", required=True)
    validate_pair.add_argument("--broad-root", required=True)
    validate_pair.add_argument("--bounded-root", required=True)

    start_pair = commands.add_parser("start-m20-4-pair")
    start_pair.add_argument("--scenario", required=True)
    start_pair.add_argument("--broad-root", required=True)
    start_pair.add_argument("--bounded-root", required=True)
    start_pair.add_argument("--reviewed-broad-digest", required=True)
    start_pair.add_argument("--reviewed-bounded-digest", required=True)

    start_control = commands.add_parser("start-m20-4-control")
    start_control.add_argument("--trial-root", required=True)
    start_control.add_argument("--reviewed-control-digest", required=True)

    snapshot_task = commands.add_parser("snapshot-task")
    snapshot_task.add_argument("--trial-root", required=True)
    snapshot_task.add_argument("--observer-config", required=True)
    snapshot_task.add_argument("--observer-log", required=True)
    snapshot_task.add_argument("--task-slot", required=True)
    snapshot_task.add_argument("--output", required=True)

    capture_m20_4 = commands.add_parser("capture-m20-4-boundary")
    capture_m20_4.add_argument("--trial-root", required=True)
    capture_m20_4.add_argument("--scenario", required=True)
    capture_m20_4.add_argument("--trial", required=True)
    capture_m20_4.add_argument("--arm", required=True)
    capture_m20_4.add_argument("--boundary", required=True)

    reduce_m20_3 = commands.add_parser("reduce-m20-3")
    reduce_m20_3.add_argument("--trial-root", required=True)
    reduce_m20_3.add_argument("--scenario", required=True)
    reduce_m20_3.add_argument("--trial", required=True)
    for option in (
        "control",
        "observer-config",
        "observer-log",
        "observer-snapshot",
        "assessment",
        "before-state",
        "after-state",
    ):
        reduce_m20_3.add_argument(f"--{option}", required=True)

    reduce_m20_4 = commands.add_parser("reduce-m20-4")
    reduce_m20_4.add_argument("--trial-root", required=True)
    reduce_m20_4.add_argument("--scenario", required=True)
    reduce_m20_4.add_argument("--trial", required=True)
    reduce_m20_4.add_argument("--arm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        controller = FreshCollectionController(Path(args.repo), args.unit)
        if args.mode == "start":
            result = controller.start(args.attempt)
        elif args.mode == "resume":
            result = controller.resume()
        elif args.mode == "check":
            result = controller.check()
        elif args.mode == "finalize":
            result = controller.finalize(
                Path(value) for value in args.extra_source_root
            )
        elif args.mode == "validate-control":
            result = controller.validate_control(
                trial_root=Path(args.trial_root),
                scenario_id=args.scenario,
                trial_id=args.trial,
                arm=args.arm,
                control_path=args.control,
            )
        elif args.mode == "validate-m20-4-pair":
            result = controller.validate_m20_4_pair(
                scenario_id=args.scenario,
                broad_root=Path(args.broad_root),
                bounded_root=Path(args.bounded_root),
            )
        elif args.mode == "start-m20-4-pair":
            result = controller.start_m20_4_pair(
                scenario_id=args.scenario,
                broad_root=Path(args.broad_root),
                bounded_root=Path(args.bounded_root),
                reviewed_broad_digest=args.reviewed_broad_digest,
                reviewed_bounded_digest=args.reviewed_bounded_digest,
            )
        elif args.mode == "start-m20-4-control":
            result = controller.start_m20_4_control(
                trial_root=Path(args.trial_root),
                reviewed_control_digest=args.reviewed_control_digest,
            )
        elif args.mode == "snapshot-task":
            result = controller.snapshot_task(
                trial_root=Path(args.trial_root),
                observer_config_path=args.observer_config,
                observer_log_path=args.observer_log,
                task_slot=args.task_slot,
                output_path=args.output,
            )
        elif args.mode == "capture-m20-4-boundary":
            result = controller.capture_m20_4_boundary(
                trial_root=Path(args.trial_root),
                scenario_id=args.scenario,
                trial_id=args.trial,
                arm=args.arm,
                boundary_id=args.boundary,
            )
        elif args.mode == "reduce-m20-3":
            result = controller.reduce_m20_3(
                trial_root=Path(args.trial_root),
                scenario_id=args.scenario,
                trial_id=args.trial,
                control_path=args.control,
                observer_config_path=args.observer_config,
                observer_log_path=args.observer_log,
                observer_snapshot_path=args.observer_snapshot,
                assessment_path=args.assessment,
                before_state_path=args.before_state,
                after_state_path=args.after_state,
            )
        else:
            result = controller.reduce_m20_4(
                trial_root=Path(args.trial_root),
                scenario_id=args.scenario,
                trial_id=args.trial,
                arm=args.arm,
            )
    except M20ObservationError as error:
        print(
            canonical_json_bytes({"ok": False, "error_code": error.code}).decode(
                "utf-8"
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, TypeError, ValueError):
        print(
            canonical_json_bytes(
                {"ok": False, "error_code": "input_error"}
            ).decode("utf-8"),
            file=sys.stderr,
        )
        return 2
    print(canonical_json_bytes({"ok": True, "mode": args.mode, **result}).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ASSESSMENT_SCHEMA",
    "BOUNDARY_SNAPSHOT_SCHEMA",
    "FreshCollectionController",
    "OBSERVER_SNAPSHOT_SCHEMA",
    "build_attestation_observation",
    "main",
]
