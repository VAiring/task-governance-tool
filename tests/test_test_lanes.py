from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from tools.test_lanes import (
    ALL_LANE,
    BASE_LANES,
    CI_POLICY,
    CI_PYTHON_VERSIONS,
    DiscoveredTests,
    LANE_MODULES,
    MANDATORY_NATIVE_TEST_IDS,
    RELEASE_CANDIDATE_EVENT,
    LanePlan,
    TestLaneError,
    build_lane_plan,
    ci_matrix,
    discover_tests,
    mandatory_native_skips,
    main as lane_main,
    validate_ci_policy,
    validate_ci_selection,
    validate_mandatory_native_tests,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "test_lanes.py"


def flatten_suite(suite: unittest.TestSuite) -> tuple[unittest.TestCase, ...]:
    cases: list[unittest.TestCase] = []
    stack = [suite]
    while stack:
        item = stack.pop()
        if isinstance(item, unittest.TestSuite):
            stack.extend(reversed(tuple(item)))
        else:
            if not isinstance(item, unittest.TestCase):
                raise AssertionError("standard discovery returned an invalid item")
            cases.append(item)
    return tuple(cases)


def standard_discovery_ids() -> tuple[str, ...]:
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=str(ROOT / "tests"),
        pattern="test*.py",
        top_level_dir=str(ROOT / "tests"),
    )
    if loader.errors:
        raise AssertionError(f"standard discovery failed: {len(loader.errors)}")
    return tuple(case.id() for case in flatten_suite(suite))


def assert_lane_error(
    testcase: unittest.TestCase,
    expected_code: str,
    callback,
) -> None:
    with testcase.assertRaises(TestLaneError) as caught:
        callback()
    testcase.assertEqual(caught.exception.code, expected_code)


class TestLanePolicyTests(unittest.TestCase):
    def test_current_inventory_exactly_matches_standard_discovery(self):
        inventory = discover_tests(ROOT)
        standard_ids = standard_discovery_ids()

        self.assertEqual(inventory.plan.test_ids, standard_ids)
        self.assertEqual(len(standard_ids), len(set(standard_ids)))
        self.assertEqual(
            sum(len(inventory.plan.ids_for(lane)) for lane in BASE_LANES),
            len(standard_ids),
        )
        self.assertEqual(inventory.plan.ids_for(ALL_LANE), standard_ids)
        self.assertEqual(
            dict(inventory.plan.module_owners)["test_test_lanes"],
            "release",
        )

    def test_each_lane_is_an_ordered_disjoint_subsequence(self):
        inventory = discover_tests(ROOT)
        all_ids = inventory.plan.test_ids
        owners = dict(inventory.plan.module_owners)
        observed: set[str] = set()

        for lane in BASE_LANES:
            expected = tuple(
                test_id
                for test_id, module in zip(
                    inventory.plan.test_ids,
                    inventory.plan.test_modules,
                    strict=True,
                )
                if owners[module] == lane
            )
            selected = inventory.plan.ids_for(lane)
            selected_suite = tuple(
                case.id() for case in flatten_suite(inventory.suite_for(lane))
            )
            self.assertEqual(selected, expected)
            self.assertEqual(selected_suite, expected)
            self.assertTrue(observed.isdisjoint(selected))
            observed.update(selected)

        self.assertEqual(observed, set(all_ids))
        self.assertEqual(
            tuple(case.id() for case in flatten_suite(inventory.suite_for("all"))),
            all_ids,
        )

    def test_manifest_rejects_unassigned_missing_and_duplicate_modules(self):
        base_manifest = {
            "fast": ("test_a",),
            "integration": ("test_b",),
            "release": ("test_c",),
        }
        assert_lane_error(
            self,
            "test_module_unassigned",
            lambda: build_lane_plan(
                (
                    "test_a.Case.test_one",
                    "test_b.Case.test_one",
                    "test_c.Case.test_one",
                    "test_new.Case.test_one",
                ),
                ("test_a", "test_b", "test_c", "test_new"),
                lane_modules=base_manifest,
            ),
        )
        assert_lane_error(
            self,
            "test_module_missing",
            lambda: build_lane_plan(
                ("test_a.Case.test_one", "test_b.Case.test_one"),
                ("test_a", "test_b"),
                lane_modules=base_manifest,
            ),
        )
        duplicate_manifest = {
            "fast": ("test_a",),
            "integration": ("test_a", "test_b"),
            "release": ("test_c",),
        }
        assert_lane_error(
            self,
            "test_module_duplicate",
            lambda: build_lane_plan(
                (
                    "test_a.Case.test_one",
                    "test_b.Case.test_one",
                    "test_c.Case.test_one",
                ),
                ("test_a", "test_b", "test_c"),
                lane_modules=duplicate_manifest,
            ),
        )

    def test_manifest_rejects_invalid_order_ids_and_module_identity(self):
        unsorted_manifest = {
            "fast": ("test_z", "test_a"),
            "integration": ("test_b",),
            "release": ("test_c",),
        }
        assert_lane_error(
            self,
            "test_lane_manifest_invalid",
            lambda: build_lane_plan(
                (
                    "test_z.Case.test_one",
                    "test_a.Case.test_one",
                    "test_b.Case.test_one",
                    "test_c.Case.test_one",
                ),
                ("test_z", "test_a", "test_b", "test_c"),
                lane_modules=unsorted_manifest,
            ),
        )
        manifest = {
            "fast": ("test_a",),
            "integration": ("test_b",),
            "release": ("test_c",),
        }
        assert_lane_error(
            self,
            "test_id_duplicate",
            lambda: build_lane_plan(
                (
                    "test_a.Case.test_one",
                    "test_a.Case.test_one",
                    "test_b.Case.test_one",
                    "test_c.Case.test_one",
                ),
                ("test_a", "test_a", "test_b", "test_c"),
                lane_modules=manifest,
            ),
        )
        assert_lane_error(
            self,
            "test_discovery_invalid",
            lambda: build_lane_plan(
                (
                    "test_wrong.Case.test_one",
                    "test_b.Case.test_one",
                    "test_c.Case.test_one",
                ),
                ("test_a", "test_b", "test_c"),
                lane_modules=manifest,
            ),
        )
        malformed_manifest = {
            "fast": ("test_a", 7),
            "integration": ("test_b",),
            "release": ("test_c",),
        }
        assert_lane_error(
            self,
            "test_lane_manifest_invalid",
            lambda: build_lane_plan(
                (
                    "test_a.Case.test_one",
                    "test_b.Case.test_one",
                    "test_c.Case.test_one",
                ),
                ("test_a", "test_b", "test_c"),
                lane_modules=malformed_manifest,
            ),
        )

    def test_loader_error_fails_before_partition_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_broken_lane_fixture.py").write_text(
                "def invalid syntax\n",
                encoding="utf-8",
            )

            assert_lane_error(
                self,
                "test_discovery_failed",
                lambda: discover_tests(root, _allow_test_root=True),
            )

    def test_ci_policy_has_exact_event_python_and_lane_matrix(self):
        validate_ci_policy()
        self.assertEqual(
            ci_matrix("pull_request"),
            {
                "include": [
                    {"lane": "fast", "python-version": "3.12"},
                    {"lane": "integration", "python-version": "3.12"},
                    {"lane": "release", "python-version": "3.12"},
                    {"lane": "fast", "python-version": "3.14"},
                ]
            },
        )
        self.assertEqual(
            ci_matrix("push"),
            {
                "include": [
                    {"lane": lane, "python-version": version}
                    for version in CI_PYTHON_VERSIONS
                    for lane in BASE_LANES
                ]
            },
        )
        self.assertEqual(
            ci_matrix(RELEASE_CANDIDATE_EVENT),
            {
                "include": [
                    {"lane": "all", "python-version": version}
                    for version in CI_PYTHON_VERSIONS
                ]
            },
        )
        self.assertEqual(tuple(CI_POLICY), ("pull_request", "push", "workflow_dispatch"))

    def test_ci_selection_rejects_unknown_rows_and_runtime_mismatch(self):
        validate_ci_selection(
            event="pull_request",
            expected_python="3.12",
            lane="integration",
            runtime_version=(3, 12),
        )
        assert_lane_error(
            self,
            "ci_selection_invalid",
            lambda: validate_ci_selection(
                event="pull_request",
                expected_python="3.14",
                lane="release",
                runtime_version=(3, 14),
            ),
        )
        assert_lane_error(
            self,
            "ci_runtime_mismatch",
            lambda: validate_ci_selection(
                event="workflow_dispatch",
                expected_python="3.12",
                lane="all",
                runtime_version=(3, 14),
            ),
        )
        assert_lane_error(
            self,
            "ci_event_invalid",
            lambda: ci_matrix("schedule"),
        )

    def test_matrix_cli_is_compact_and_deterministic(self):
        command = [
            sys.executable,
            "-B",
            str(RUNNER),
            "--repo",
            str(ROOT),
            "--matrix-event",
            "workflow_dispatch",
        ]
        first = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        second = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stderr, "")
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stdout.count("\n"), 1)
        self.assertEqual(json.loads(first.stdout), ci_matrix("workflow_dispatch"))

    def test_invalid_cli_option_combination_fails_before_discovery(self):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(RUNNER),
                "--repo",
                str(ROOT / "missing-repository"),
                "--check",
                "--ci-event",
                "push",
                "--expected-python",
                "3.12",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "test lanes: ERROR (test_lane_arguments_invalid)\n",
        )

        with tempfile.TemporaryDirectory() as temporary:
            foreign_root = Path(temporary)
            foreign_tests = foreign_root / "tests"
            foreign_tests.mkdir()
            sentinel = foreign_root / "imported.txt"
            (foreign_tests / "test_foreign_side_effect.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('imported')\n",
                encoding="utf-8",
            )

            foreign = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(RUNNER),
                    "--repo",
                    str(foreign_root),
                    "--check",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(foreign.returncode, 2)
            self.assertEqual(foreign.stdout, "")
            self.assertEqual(
                foreign.stderr,
                "test lanes: ERROR (test_repository_mismatch)\n",
            )
            self.assertFalse(sentinel.exists())

    def test_manifest_contains_only_sorted_owned_test_modules(self):
        self.assertEqual(tuple(LANE_MODULES), BASE_LANES)
        all_modules = [
            module for lane in BASE_LANES for module in LANE_MODULES[lane]
        ]
        self.assertEqual(len(all_modules), len(set(all_modules)))
        for lane in BASE_LANES:
            self.assertEqual(LANE_MODULES[lane], tuple(sorted(LANE_MODULES[lane])))

    def test_mandatory_native_ids_exist_in_the_integration_lane(self):
        inventory = discover_tests(ROOT)

        validate_mandatory_native_tests(inventory.plan)
        self.assertEqual(
            MANDATORY_NATIVE_TEST_IDS,
            (
                "test_m241a_lpac_portability.RunnerLpacPortabilityNativeTests."
                "test_real_lpac_portability_matrix_and_cleanup",
            ),
        )
        self.assertTrue(
            set(MANDATORY_NATIVE_TEST_IDS).issubset(
                inventory.plan.ids_for("integration")
            )
        )

    def test_mandatory_native_policy_rejects_missing_and_wrong_owner(self):
        test_id = MANDATORY_NATIVE_TEST_IDS[0]
        module = test_id.split(".", 1)[0]
        missing = LanePlan(
            test_ids=("test_other.Case.test_one",),
            test_modules=("test_other",),
            module_owners=(("test_other", "integration"),),
        )
        assert_lane_error(
            self,
            "mandatory_native_test_missing",
            lambda: validate_mandatory_native_tests(
                missing,
                mandatory_test_ids=(test_id,),
            ),
        )
        wrong_owner = LanePlan(
            test_ids=(test_id,),
            test_modules=(module,),
            module_owners=((module, "fast"),),
        )
        assert_lane_error(
            self,
            "mandatory_native_test_owner_invalid",
            lambda: validate_mandatory_native_tests(
                wrong_owner,
                mandatory_test_ids=(test_id,),
            ),
        )

    def test_only_selected_mandatory_native_skips_make_a_lane_non_pass(self):
        mandatory_id = MANDATORY_NATIVE_TEST_IDS[0]

        class FakeCase:
            def __init__(self, test_id: str):
                self._test_id = test_id

            def id(self) -> str:
                return self._test_id

        mandatory = FakeCase(mandatory_id)
        optional = FakeCase("test_optional_native.Case.test_optional")
        result = unittest.TestResult()
        result.addSkip(mandatory, "unsupported")
        result.addSkip(optional, "optional")

        self.assertEqual(
            mandatory_native_skips(result, (mandatory_id, optional.id())),
            (mandatory_id,),
        )
        self.assertEqual(mandatory_native_skips(result, (optional.id(),)), ())

        optional_only = unittest.TestResult()
        optional_only.addSkip(optional, "optional")
        self.assertEqual(
            mandatory_native_skips(
                optional_only,
                (mandatory_id, optional.id()),
            ),
            (),
        )

    def test_lane_command_rejects_mandatory_but_not_optional_skip(self):
        mandatory_id = MANDATORY_NATIVE_TEST_IDS[0]
        mandatory_module, mandatory_class, mandatory_name = mandatory_id.rsplit(
            ".", 2
        )

        def skipped(_self):
            raise unittest.SkipTest("fixture")

        mandatory_type = type(
            mandatory_class,
            (unittest.TestCase,),
            {
                "__module__": mandatory_module,
                mandatory_name: skipped,
            },
        )
        mandatory_case = mandatory_type(mandatory_name)
        mandatory_inventory = DiscoveredTests(
            suite=unittest.TestSuite((mandatory_case,)),
            cases=(mandatory_case,),
            plan=LanePlan(
                test_ids=(mandatory_case.id(),),
                test_modules=(mandatory_module,),
                module_owners=((mandatory_module, "integration"),),
            ),
        )
        with patch(
            "tools.test_lanes.discover_tests",
            return_value=mandatory_inventory,
        ), redirect_stderr(io.StringIO()) as mandatory_stderr:
            self.assertEqual(lane_main(("--lane", "integration")), 1)
        self.assertIn(
            "mandatory_native_test_skipped",
            mandatory_stderr.getvalue(),
        )

        optional_type = type(
            "OptionalNativeTests",
            (unittest.TestCase,),
            {
                "__module__": "test_optional_native",
                "test_optional": skipped,
            },
        )
        optional_case = optional_type("test_optional")
        optional_inventory = DiscoveredTests(
            suite=unittest.TestSuite((optional_case,)),
            cases=(optional_case,),
            plan=LanePlan(
                test_ids=(optional_case.id(),),
                test_modules=("test_optional_native",),
                module_owners=(("test_optional_native", "integration"),),
            ),
        )
        with patch(
            "tools.test_lanes.discover_tests",
            return_value=optional_inventory,
        ), redirect_stderr(io.StringIO()):
            self.assertEqual(lane_main(("--lane", "integration")), 0)


if __name__ == "__main__":
    unittest.main()
