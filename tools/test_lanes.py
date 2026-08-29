"""Deterministic offline test lanes and CI policy for this repository."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_LANES = ("fast", "integration", "release")
ALL_LANE = "all"
CI_EVENTS = ("pull_request", "push", "workflow_dispatch")
CI_PYTHON_VERSIONS = ("3.12", "3.14")
CI_PUSH_BRANCHES = ("main",)
RELEASE_CANDIDATE_EVENT = "workflow_dispatch"

CI_CHECK_INVOCATION = "python tools/test_lanes.py --repo . --check"
CI_MATRIX_INVOCATION = (
    'python tools/test_lanes.py --repo . --matrix-event '
    '"${{ github.event_name }}"'
)
CI_LANE_INVOCATION = (
    'python tools/test_lanes.py --repo . --lane "${{ matrix.lane }}" '
    '--ci-event "${{ github.event_name }}" '
    '--expected-python "${{ matrix.python-version }}"'
)


# Module ownership is explicit. A new or removed discovered test module fails
# validation until this repository policy is reviewed and updated.
LANE_MODULES: dict[str, tuple[str, ...]] = {
    "fast": (
        "test_checkpoint_cli",
        "test_checkpoints",
        "test_cli_envelope",
        "test_cli_help",
        "test_completion_commit_cli",
        "test_completion_commit_schema",
        "test_completion_cycle_lifecycle",
        "test_completion_cycle_repository",
        "test_completion_evidence",
        "test_completion_history_projection",
        "test_completion_pure_helpers",
        "test_effort_advisory",
        "test_git_snapshot",
        "test_handoffs",
        "test_m214c_stored_task_validation",
        "test_m214d_contract_pointer_validation",
        "test_m223_bundle_assembly_pure",
        "test_m223_canonical_json_pure",
        "test_m22_artifact_manifests",
        "test_m22_evidence_ledger_pure",
        "test_m22_review_provenance_pure",
        "test_m232_analysis_contracts",
        "test_m232_analysis_packet",
        "test_m232_analysis_process",
        "test_m232_analysis_renderer",
        "test_m232_analysis_validator",
        "test_m232_evidence_consumer",
        "test_m242_evidence_compatibility",
        "test_m242_runner_git",
        "test_m242_runner_model",
        "test_m242_runner_plan",
        "test_project_scope",
        "test_relocation",
        "test_review_evidence",
        "test_review_packet",
        "test_selection",
        "test_sequential_transitions",
        "test_storage_paths",
        "test_task_add",
        "test_task_compact",
        "test_task_complete",
        "test_task_contracts",
        "test_task_current",
        "test_task_edit",
        "test_task_fixture",
        "test_task_list",
        "test_task_next",
        "test_task_show",
        "test_task_validation",
    ),
    "integration": (
        "test_completion_cycle_activation",
        "test_completion_cycle_history",
        "test_db_init",
        "test_doctor",
        "test_legacy_cleanup_pending",
        "test_live_read_consistency",
        "test_m17_cli_consumer_hardening",
        "test_m17_consumer_hardening",
        "test_m17_recovery_hardening",
        "test_m17_relocation_setup",
        "test_m17_setup_regressions",
        "test_m214b_recovery_boundaries",
        "test_m214c_consumer_boundaries",
        "test_m214d_consumer_boundaries",
        "test_m214d_snapshot_boundaries",
        "test_m223_bundle_storage",
        "test_m223_evidence_projection_integration",
        "test_m223_evidence_projection_publication",
        "test_m223_post_commit_evidence",
        "test_m224_evidence_acceptance",
        "test_m22_evidence_ledger_storage",
        "test_m22_verification_subjects",
        "test_m232_analysis_adapter_publication",
        "test_m232_analysis_outbox",
        "test_m232_analysis_publication",
        "test_m232_analysis_selection",
        "test_m232_analysis_win32",
        "test_m232_analysis_worker",
        "test_m232_analysis_worker_race",
        "test_m232_codex_analysis_adapter",
        "test_m233_analysis_integration",
        "test_m242_r3a_schema20_storage",
        "test_m242_r3b_schema20_activation",
        "test_m242_runner_lifecycle",
        "test_m242_runner_process",
        "test_m242_runner_runtime",
        "test_m242_runner_service",
        "test_m242_runner_storage",
        "test_m242_runner_win32",
        "test_m243b_schema21_compatibility",
        "test_m243b_schema21_recovery",
        "test_m243b_schema21_storage",
        "test_m243c_runner_gate",
        "test_post_commit_maintenance",
        "test_project_identity_bindings",
        "test_routine_backup",
        "test_setup",
        "test_setup_backup",
        "test_setup_recovery",
        "test_state_resolver",
        "test_state_transition_primitives",
        "test_verification_receipts",
        "test_viewer_config",
        "test_viewer_maintenance",
        "test_viewer_renderer",
        "test_viewer_snapshot",
        "test_write_contention",
    ),
    "release": (
        "test_backup_performance",
        "test_document_contract",
        "test_document_history",
        "test_m14_integrated_acceptance",
        "test_m17_legacy_recovery_matrix",
        "test_m17_release_acceptance",
        "test_m19_legacy_upgrade_rehearsal",
        "test_m214b_legacy_recovery_boundaries",
        "test_m215_verification_input_capacity",
        "test_m224_package_forward",
        "test_migration_acceptance",
        "test_release_contract",
        "test_self_status",
        "test_skill_self_containment",
        "test_test_lanes",
    ),
}


# Rows are intentionally ordered for stable GitHub matrix JSON.
CI_POLICY: dict[str, tuple[tuple[str, str], ...]] = {
    "pull_request": (
        ("3.12", "fast"),
        ("3.12", "integration"),
        ("3.12", "release"),
        ("3.14", "fast"),
    ),
    "push": (
        ("3.12", "fast"),
        ("3.12", "integration"),
        ("3.12", "release"),
        ("3.14", "fast"),
        ("3.14", "integration"),
        ("3.14", "release"),
    ),
    "workflow_dispatch": (
        ("3.12", "all"),
        ("3.14", "all"),
    ),
}


class TestLaneError(RuntimeError):
    """A bounded deterministic lane-policy failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LanePlan:
    """Validated ordered test identities and their owning modules."""

    test_ids: tuple[str, ...]
    test_modules: tuple[str, ...]
    module_owners: tuple[tuple[str, str], ...]

    def ids_for(self, lane: str) -> tuple[str, ...]:
        if lane == ALL_LANE:
            return self.test_ids
        if lane not in BASE_LANES:
            raise TestLaneError("test_lane_invalid")
        owners = dict(self.module_owners)
        return tuple(
            test_id
            for test_id, module in zip(
                self.test_ids,
                self.test_modules,
                strict=True,
            )
            if owners[module] == lane
        )

    def counts(self) -> dict[str, int]:
        return {
            lane: len(self.ids_for(lane))
            for lane in (*BASE_LANES, ALL_LANE)
        }


@dataclass(frozen=True)
class DiscoveredTests:
    """One standard discovery plus its validated lane plan."""

    suite: unittest.TestSuite
    cases: tuple[unittest.TestCase, ...]
    plan: LanePlan

    def suite_for(self, lane: str) -> unittest.TestSuite:
        if lane == ALL_LANE:
            return self.suite
        selected_ids = frozenset(self.plan.ids_for(lane))
        return unittest.TestSuite(
            case for case in self.cases if case.id() in selected_ids
        )


def _flatten_suite(
    suite: unittest.TestSuite,
) -> tuple[unittest.TestCase, ...]:
    cases: list[unittest.TestCase] = []
    stack: list[Any] = [suite]
    while stack:
        item = stack.pop()
        if isinstance(item, unittest.TestSuite):
            stack.extend(reversed(tuple(item)))
        elif isinstance(item, unittest.TestCase):
            cases.append(item)
        else:
            raise TestLaneError("test_discovery_invalid")
    return tuple(cases)


def build_lane_plan(
    test_ids: Sequence[str],
    test_modules: Sequence[str],
    *,
    lane_modules: Mapping[str, Sequence[str]] = LANE_MODULES,
) -> LanePlan:
    """Validate exact module ownership without changing discovery order."""

    if tuple(lane_modules) != BASE_LANES:
        raise TestLaneError("test_lane_manifest_invalid")
    if len(test_ids) != len(test_modules) or not test_ids:
        raise TestLaneError("test_discovery_invalid")
    if len(set(test_ids)) != len(test_ids):
        raise TestLaneError("test_id_duplicate")

    owners: dict[str, str] = {}
    for lane in BASE_LANES:
        modules = tuple(lane_modules[lane])
        if not modules or any(
            not isinstance(module, str)
            or not module.startswith("test_")
            or "." in module
            for module in modules
        ):
            raise TestLaneError("test_lane_manifest_invalid")
        if modules != tuple(sorted(modules)):
            raise TestLaneError("test_lane_manifest_invalid")
        if len(set(modules)) != len(modules):
            raise TestLaneError("test_module_duplicate")
        for module in modules:
            if module in owners:
                raise TestLaneError("test_module_duplicate")
            owners[module] = lane

    discovered_modules: set[str] = set()
    normalized_ids: list[str] = []
    normalized_modules: list[str] = []
    for test_id, module in zip(test_ids, test_modules, strict=True):
        if (
            not isinstance(test_id, str)
            or not isinstance(module, str)
            or not test_id.startswith(f"{module}.")
        ):
            raise TestLaneError("test_discovery_invalid")
        normalized_ids.append(test_id)
        normalized_modules.append(module)
        discovered_modules.add(module)

    configured_modules = set(owners)
    if discovered_modules - configured_modules:
        raise TestLaneError("test_module_unassigned")
    if configured_modules - discovered_modules:
        raise TestLaneError("test_module_missing")

    plan = LanePlan(
        test_ids=tuple(normalized_ids),
        test_modules=tuple(normalized_modules),
        module_owners=tuple(sorted(owners.items())),
    )
    assigned = tuple(
        test_id
        for lane in BASE_LANES
        for test_id in plan.ids_for(lane)
    )
    if len(assigned) != len(plan.test_ids) or set(assigned) != set(plan.test_ids):
        raise TestLaneError("test_partition_invalid")
    return plan


def discover_tests(
    repo_root: Path,
    *,
    _allow_test_root: bool = False,
) -> DiscoveredTests:
    """Run standard unittest discovery once and validate its complete inventory."""

    root = repo_root.resolve()
    if not _allow_test_root and root != DEFAULT_REPO_ROOT.resolve():
        raise TestLaneError("test_repository_mismatch")
    tests_root = root / "tests"
    if not root.is_dir() or not tests_root.is_dir():
        raise TestLaneError("test_repository_invalid")

    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=str(tests_root),
        pattern="test*.py",
        top_level_dir=str(tests_root),
    )
    if loader.errors:
        raise TestLaneError("test_discovery_failed")
    cases = _flatten_suite(suite)
    test_ids = tuple(case.id() for case in cases)
    test_modules = tuple(case.__class__.__module__ for case in cases)
    plan = build_lane_plan(test_ids, test_modules)
    return DiscoveredTests(suite=suite, cases=cases, plan=plan)


def validate_ci_policy() -> None:
    """Fail closed when the repository CI policy loses its exact coverage."""

    if tuple(CI_POLICY) != CI_EVENTS:
        raise TestLaneError("ci_policy_invalid")
    allowed_lanes = {*BASE_LANES, ALL_LANE}
    for event in CI_EVENTS:
        rows = CI_POLICY[event]
        if not rows or len(rows) != len(set(rows)):
            raise TestLaneError("ci_policy_invalid")
        if any(
            version not in CI_PYTHON_VERSIONS or lane not in allowed_lanes
            for version, lane in rows
        ):
            raise TestLaneError("ci_policy_invalid")
        if {version for version, _lane in rows} != set(CI_PYTHON_VERSIONS):
            raise TestLaneError("ci_policy_invalid")

    expected_pull_request = (
        ("3.12", "fast"),
        ("3.12", "integration"),
        ("3.12", "release"),
        ("3.14", "fast"),
    )
    expected_push = tuple(
        (version, lane)
        for version in CI_PYTHON_VERSIONS
        for lane in BASE_LANES
    )
    expected_candidate = tuple(
        (version, ALL_LANE) for version in CI_PYTHON_VERSIONS
    )
    if (
        CI_POLICY["pull_request"] != expected_pull_request
        or CI_POLICY["push"] != expected_push
        or CI_POLICY[RELEASE_CANDIDATE_EVENT] != expected_candidate
        or RELEASE_CANDIDATE_EVENT != "workflow_dispatch"
        or CI_PUSH_BRANCHES != ("main",)
    ):
        raise TestLaneError("ci_policy_invalid")


def ci_matrix(event: str) -> dict[str, list[dict[str, str]]]:
    """Return one deterministic GitHub Actions include matrix."""

    validate_ci_policy()
    if event not in CI_POLICY:
        raise TestLaneError("ci_event_invalid")
    return {
        "include": [
            {"lane": lane, "python-version": version}
            for version, lane in CI_POLICY[event]
        ]
    }


def validate_ci_selection(
    *,
    event: str,
    expected_python: str,
    lane: str,
    runtime_version: tuple[int, int] | None = None,
) -> None:
    """Bind one CI test job to the generated policy row and interpreter."""

    matrix = ci_matrix(event)
    row = {"lane": lane, "python-version": expected_python}
    if row not in matrix["include"]:
        raise TestLaneError("ci_selection_invalid")
    observed = runtime_version or (sys.version_info.major, sys.version_info.minor)
    if f"{observed[0]}.{observed[1]}" != expected_python:
        raise TestLaneError("ci_runtime_mismatch")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or run deterministic repository test lanes."
    )
    parser.add_argument("--repo", default=str(DEFAULT_REPO_ROOT))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--lane", choices=(*BASE_LANES, ALL_LANE))
    mode.add_argument("--matrix-event", choices=CI_EVENTS)
    parser.add_argument("--ci-event", choices=CI_EVENTS)
    parser.add_argument("--expected-python", choices=CI_PYTHON_VERSIONS)
    return parser


def _print_check(plan: LanePlan) -> None:
    counts = plan.counts()
    print(
        "test lanes: PASS "
        f"(all={counts['all']}; "
        f"fast={counts['fast']}; "
        f"integration={counts['integration']}; "
        f"release={counts['release']})"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        validate_ci_policy()
        if args.matrix_event is not None:
            if args.ci_event is not None or args.expected_python is not None:
                raise TestLaneError("test_lane_arguments_invalid")
            print(
                json.dumps(
                    ci_matrix(args.matrix_event),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0

        if (args.ci_event is None) != (args.expected_python is None):
            raise TestLaneError("test_lane_arguments_invalid")
        if args.check and args.ci_event is not None:
            raise TestLaneError("test_lane_arguments_invalid")
        if args.lane is not None and args.ci_event is not None:
            validate_ci_selection(
                event=args.ci_event,
                expected_python=args.expected_python,
                lane=args.lane,
            )

        inventory = discover_tests(Path(args.repo))
        if args.check:
            _print_check(inventory.plan)
            return 0

        lane = args.lane
        if lane is None:
            raise TestLaneError("test_lane_arguments_invalid")
        suite = inventory.suite_for(lane)
        expected_count = len(inventory.plan.ids_for(lane))
        print(
            f"test lane: {lane} ({expected_count} tests)",
            file=sys.stderr,
        )
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful():
            return 1
        if result.testsRun != expected_count:
            raise TestLaneError("test_execution_count_mismatch")
        return 0
    except TestLaneError as exc:
        print(f"test lanes: ERROR ({exc.code})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
