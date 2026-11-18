"""Run and reconcile the three Windows unittest shards for one interpreter."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from tools import test_lanes


SHARDS = (1, 2, 3)
# Keep the Setup recovery/consumer regression sequence and shared schema
# runtime fixtures together. Within a group discovery determines execution order.
MODULE_GROUPS = (
    (
        "test_m17_cli_consumer_hardening", "test_m17_consumer_hardening",
        "test_m17_recovery_hardening", "test_m17_relocation_setup",
        "test_m17_setup_regressions", "test_m214b_legacy_recovery_boundaries",
        "test_m214b_recovery_boundaries", "test_m214c_consumer_boundaries",
        "test_m214c_stored_task_validation", "test_m214d_consumer_boundaries",
        "test_m214d_contract_pointer_validation", "test_m214d_snapshot_boundaries",
    ),
    ("test_m242_r3b_schema20_activation", "test_m242_runner_storage"),
    ("test_m243b_schema21_compatibility", "test_m243b_schema21_recovery"),
)
# Initial seconds per case, for scheduling only. The three review estimates
# use the Windows sample at ee11512, the other samples use 24933b5. Unknown
# modules are weighted by case count until comparable runner timings exist.
SECONDS_PER_CASE = {
    "test_review_finalization": 45,
    "test_review_handoff_preparation": 51,
    "test_review_workspace": 42,
    "test_m14_integrated_acceptance": 21,
    "test_m19_legacy_upgrade_rehearsal": 26,
    "test_m215_verification_input_capacity": 19,
    "test_m224_package_forward": 49,
    "test_viewer_capture_admission": 32,
    "test_m214b_recovery_boundaries": 16,
    "test_usage_wait_install": 33,
    "test_usage_response_page_experiment": 17,
}


def shard_ids(plan, *, groups=MODULE_GROUPS):
    """Balance indivisible module groups, then filter original discovery order."""
    counts = Counter(plan.test_modules)
    grouped = [module for group in groups for module in group]
    if len(set(grouped)) != len(grouped) or set(grouped) - counts.keys():
        raise ValueError("invalid module group")
    units = [*groups, *((module,) for module in sorted(counts.keys() - set(grouped)))]
    weights = {
        unit: sum(counts[module] * SECONDS_PER_CASE.get(module, 3) for module in unit)
        for unit in units
    }
    loads = dict.fromkeys(SHARDS, 0)
    owners = {}
    for unit in sorted(units, key=lambda group: (-weights[group], group)):
        shard = min(loads, key=loads.get)
        loads[shard] += weights[unit]
        owners.update(dict.fromkeys(unit, shard))
    selections = {shard: [] for shard in SHARDS}
    for test_id, module in zip(plan.test_ids, plan.test_modules, strict=True):
        selections[owners[module]].append(test_id)
    if not all(selections.values()):
        raise ValueError("empty Windows shard")
    return selections


class TimedResult(unittest.TextTestResult):
    """Record actual unittest invocations, including their terminal outcomes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        self.started_at = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append((test.id(), round(1000 * (time.monotonic() - self.started_at))))
        super().stopTest(test)

    def rows(self):
        outcomes = {test.id(): "skip" for test, _ in self.skipped}
        outcomes.update((test.id(), "expected_failure") for test, _ in self.expectedFailures)
        outcomes.update((test.id(), "unexpected_success") for test in self.unexpectedSuccesses)
        for status, problems in (("failure", self.failures), ("error", self.errors)):
            for test, _ in problems:
                parent = getattr(test, "test_case", test)  # Failed subtest belongs to its method.
                outcomes[parent.id()] = status
        return [{"id": test_id, "ms": ms, "outcome": outcomes.get(test_id, "pass")}
                for test_id, ms in self.timings]


def execution_identity(version):
    actual = f"{sys.version_info.major}.{sys.version_info.minor}"
    if actual != version or sys.platform != "win32":
        raise ValueError("Windows runner or Python version mismatch")
    identity = {"python": actual}
    for key in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        value = os.environ.get(key)
        if not value:
            raise ValueError(f"missing {key}")
        identity[key] = value
    return identity


def check_report(report, identity, shard, expected):
    if (report["identity"] != identity or type(report["shard"]) is not int or report["shard"] != shard
            or report["success"] is not True):
        raise ValueError("failed or mismatched shard")
    rows = report["tests"]
    if [row["id"] for row in rows] != expected:
        raise ValueError("executed test IDs differ from ordered discovery")
    for row in rows:
        if (type(row["ms"]) is not int or row["ms"] < 0
                or row["outcome"] not in ("pass", "skip", "expected_failure")):
            raise ValueError("invalid test result")
        if row["outcome"] != "pass" and row["id"] in test_lanes.MANUAL_TIMING_QUALIFICATION_TEST_IDS:
            raise ValueError("manual timing qualifier did not pass")


def run_shard(inventory, expected, identity, shard, directory):
    selected = set(expected)
    suite = unittest.TestSuite(case for case in inventory.cases if case.id() in selected)
    with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "00000000-0000-4000-8000-000000000001"}):
        result = unittest.TextTestRunner(resultclass=TimedResult, verbosity=1).run(suite)
    report = {"identity": identity, "shard": shard,
              "success": result.wasSuccessful(), "tests": result.rows()}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"shard-{shard}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, separators=(",", ":"))
    check_report(report, identity, shard, expected)


def verify_reports(plan, selections, identity, directory):
    prefix = f"windows-{identity['GITHUB_RUN_ATTEMPT']}-{identity['python']}-"
    if {path.name for path in directory.iterdir()} != {prefix + str(shard) for shard in SHARDS}:
        raise ValueError("missing or extra Windows shard artifact")
    observed = []
    for shard, expected in selections.items():
        artifact = directory / (prefix + str(shard))
        path = artifact / f"shard-{shard}.json"
        if list(artifact.iterdir()) != [path]:
            raise ValueError("missing or extra shard result")
        report = json.loads(path.read_text(encoding="utf-8"))
        check_report(report, identity, shard, expected)
        observed.extend(row["id"] for row in report["tests"])
    if Counter(observed) != Counter(plan.test_ids) or len(set(observed)) != len(observed):
        raise ValueError("incomplete or duplicated Windows coverage")
    print(f"Python {identity['python']}: verified {len(observed)} test IDs across three shards")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--shard", type=int, choices=SHARDS)
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--python", required=True, choices=test_lanes.CI_PYTHON_VERSIONS)
    parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        identity = execution_identity(args.python)
        inventory = test_lanes.discover_tests(test_lanes.DEFAULT_REPO_ROOT)
        selections = shard_ids(inventory.plan)
        if args.verify:
            verify_reports(inventory.plan, selections, identity, args.results)
        else:
            run_shard(inventory, selections[args.shard], identity, args.shard, args.results)
        return 0
    except (KeyError, OSError, ValueError, TypeError, test_lanes.TestLaneError) as exc:
        print(f"Windows shard failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
