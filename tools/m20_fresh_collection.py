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
)
from tools.m20_fresh_measurement import reduce_m20_3_trial, reduce_verification_log
from tools.m20_observation import (
    M20ObservationError,
    _exact_keys,
    _excluded_rows,
    _fail,
    _load_json_bytes,
    build_observation,
    canonical_json_bytes,
    derive_inventory,
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
MAX_CONTROL_BYTES = 262_144
MAX_LOG_BYTES = 262_144
MAX_STATE_BYTES = 1_048_576
MAX_ASSESSMENT_BYTES = 32_768
_SAFE_RELATIVE = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9._-]{1,64}\Z")
_UNKNOWN_FIELD = re.compile(r"[a-z][a-z0-9_.]{0,95}\Z")


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail("parse_failed")
    return value


def _integer(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _fail("parse_failed")
    return value


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
        raw = path.read_bytes()
    except OSError:
        _fail("source_missing")
    if not raw or len(raw) > maximum:
        _fail("cap_exceeded" if len(raw) > maximum else "parse_failed")
    value = _load_json_bytes(raw)
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        _fail("source_drift")
    return value


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


class FreshCollectionController:
    """Connect fixed trial-local inputs to a unit-bound lifecycle."""

    def __init__(self, repo_root: Path, unit: str) -> None:
        self.lifecycle = FreshCollectionLifecycle(repo_root, unit)
        self.unit = unit

    def start(self, attempt_ids: Iterable[str]) -> dict[str, Any]:
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

    def _require_started(self, trial_id: str) -> None:
        protocol = load_protocol(self.lifecycle.repo_root)
        self.lifecycle._paths()
        journal = self.lifecycle._read_journal(protocol)
        state = journal["attempts"].get(trial_id)
        if state is None or state["status"] != "started":
            _fail("attempt_not_started")

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

    snapshot_task = commands.add_parser("snapshot-task")
    snapshot_task.add_argument("--trial-root", required=True)
    snapshot_task.add_argument("--observer-config", required=True)
    snapshot_task.add_argument("--observer-log", required=True)
    snapshot_task.add_argument("--task-slot", required=True)
    snapshot_task.add_argument("--output", required=True)

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
        elif args.mode == "snapshot-task":
            result = controller.snapshot_task(
                trial_root=Path(args.trial_root),
                observer_config_path=args.observer_config,
                observer_log_path=args.observer_log,
                task_slot=args.task_slot,
                output_path=args.output,
            )
        else:
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
    "FreshCollectionController",
    "OBSERVER_SNAPSHOT_SCHEMA",
    "build_attestation_observation",
    "main",
]
