from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tools.m20_fresh_measurement import (
    CheckerSpec,
    FIXED_CHECKERS,
    Materialization,
    _ProcessTree,
    _checker_outcome,
    _final_side,
    build_state_pair_observation,
    capture_boundary_snapshot,
    reduce_m20_3_trial,
    reduce_m20_4_measurement,
    reduce_verification_log,
    target_change_sensitivity,
)
from tools.m20_observation import (
    MAX_SIGNED_32,
    M20ObservationError,
    canonical_json_bytes,
    load_m20_4_episode_plan,
    load_protocol,
)


ROOT = Path(__file__).resolve().parents[1]


def run_git(root: Path, *arguments: str) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "M20 Fixture",
            "GIT_AUTHOR_EMAIL": "m20@example.invalid",
            "GIT_COMMITTER_NAME": "M20 Fixture",
            "GIT_COMMITTER_EMAIL": "m20@example.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        env=environment,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def write_text(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def initialize_repo(root: Path) -> str:
    write_text(
        root,
        "sample.py",
        'VALUE = "before"\nCONTRACT = "needle"\n',
    )
    write_text(root, "tests/__init__.py", "")
    write_text(
        root,
        "tests/test_cli_envelope.py",
        """import unittest
import sample


class CliTests(unittest.TestCase):
    def test_value(self):
        self.assertEqual(sample.VALUE, "before")

    def test_fixture(self):
        self.assertTrue(True)
""",
    )
    run_git(root, "init", "--quiet")
    run_git(root, "add", "--all")
    run_git(root, "commit", "--quiet", "-m", "baseline")
    return run_git(root, "rev-parse", "HEAD")


def materialize_final(root: Path) -> None:
    write_text(
        root,
        "sample.py",
        'VALUE = "after"\nCONTRACT = "needle"  # clarified\n',
    )
    write_text(root, "contract_copy.py", 'CONTRACT = "needle"\n')
    write_text(
        root,
        "tests/test_cli_envelope.py",
        """import unittest
import sample


class CliTests(unittest.TestCase):
    def test_value(self):
        self.assertEqual(sample.VALUE, "after")

    def test_fixture(self):
        self.assertTrue(True)


class OtherTests(unittest.TestCase):
    def test_fixture(self):
        self.assertTrue(True)
""",
    )


def protocol_for(baseline: str) -> dict:
    protocol = copy.deepcopy(load_protocol(ROOT))
    protocol["authority"]["baseline_revision"] = baseline
    return protocol


def control_bundle() -> dict:
    return {
        "workload": "Update a fixed CLI contract.",
        "delivered_request": "Make the requested localized change.",
        "neutral_clarification": "Use the stated contract.",
        "reducer_manifest": {
            "schema": "m20-reducer-manifest-v1",
            "scenario_id": "vp_cli_contract",
            "arm": "baseline",
            "owner_slots": ["core", "tests"],
            "contract_probes": [
                {
                    "probe_id": "cli_contract",
                    "owner_slot": "core",
                    "selector": "sample.py",
                    "needle_lf": 'CONTRACT = "needle"',
                }
            ],
            "inventory_probes": [
                {
                    "probe_id": "cli_inventory",
                    "owner_slot": "tests",
                    "selector": "tests/test_cli_envelope.py",
                    "needle_lf": 'self.assertEqual(sample.VALUE, "before")',
                }
            ],
            "maintenance_selectors": [
                {"owner_slot": "core", "selector": "sample.py"},
                {
                    "owner_slot": "tests",
                    "selector": "tests/test_cli_envelope.py",
                },
            ],
            "fixture_probes": [
                {
                    "probe_id": "cli_fixture",
                    "selector": "tests/test_cli_envelope.py",
                    "qualified_name": "CliTests.test_fixture",
                }
            ],
            "verification_labels": [
                {"label": "cli_contract", "kind": "focused"},
                {"label": "fast_lane", "kind": "lane"},
            ],
            "target_change": {
                "selector": "sample.py",
                "before_lf": 'VALUE = "before"\n',
                "after_lf": 'VALUE = "after"\n',
                "verification_label": "cli_contract",
            },
        },
    }


def m20_4_control_bundle() -> dict:
    return {
        "workload": "Handle a bounded out-of-scope handoff.",
        "delivered_request": "Record the required local handoff state.",
        "neutral_clarification": "Use only the stated task boundary.",
        "reducer_manifest": {
            "schema": "m20-reducer-manifest-v1",
            "scenario_id": "sp_handoff_control",
            "arm": "broad",
            "owner_slots": ["core"],
            "contract_probes": [],
            "inventory_probes": [],
            "maintenance_selectors": [
                {"owner_slot": "core", "selector": "sample.py"}
            ],
            "fixture_probes": [],
            "verification_labels": [
                {"label": "other_check", "kind": "focused"}
            ],
            "target_change": {
                "selector": "sample.py",
                "before_lf": 'VALUE = "before"\n',
                "after_lf": 'VALUE = "after"\n',
                "verification_label": "other_check",
            },
        },
    }


def m20_4_authority_for(baseline: str) -> tuple[dict, dict]:
    original = load_protocol(ROOT)
    plan = copy.deepcopy(load_m20_4_episode_plan(original, ROOT))
    protocol = copy.deepcopy(original)
    protocol["authority"]["baseline_revision"] = baseline
    plan["baseline_revision"] = baseline
    return protocol, plan


def task_show_envelope(
    *,
    contract_revision: int,
    review_generation: int,
    task_id: str = "tg_task_0123456789abcdef",
) -> dict:
    return {
        "ok": True,
        "command": "task.show",
        "project_id": "task-governance-tool-test",
        "data": {
            "task": {
                "task_id": task_id,
                "status": "in_progress",
                "review_target_generation": review_generation,
            },
            "events": [],
            "suggested_next_action": "continue",
            "review_evidence": {
                "target": None,
                "gate": {
                    "review_tier": 1,
                    "required_independent_passes": 1,
                    "qualifying_independent_passes": 0,
                    "fallback_kind": None,
                    "satisfied": False,
                },
                "counts": {
                    "receipts_total": 0,
                    "receipts_current_generation": 0,
                    "changes_requested_current_generation": 0,
                    "open_high": 0,
                    "open_medium": 0,
                    "open_low": 0,
                },
                "blocking_findings": [],
                "recent_receipts": [],
                "recent_findings": [],
            },
            "handoff_summary": {
                "pending_handoff": 0,
                "handed_off": 0,
                "handoff_withdrawn_by_user": 0,
            },
            "contract": {"revision": contract_revision},
            "latest_checkpoint": None,
            "effort_advisory_enabled": False,
            "completion_history": {
                "total": 0,
                "returned_count": 0,
                "truncated": False,
                "legacy_history_incomplete": False,
                "cycles": [],
            },
        },
        "warnings": [],
        "errors": [],
    }


class M20FreshMeasurementTests(unittest.TestCase):
    def test_m20_3_reduces_git_ast_manifest_log_state_and_sensitivity(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            baseline = initialize_repo(repo)
            materialize_final(repo)
            protocol = protocol_for(baseline)
            expected_final = (repo / "sample.py").read_bytes()

            state, measurement = reduce_m20_3_trial(
                protocol,
                repo_root=repo,
                scenario_id="vp_cli_contract",
                trial_id="vp_cli_contract.baseline.01",
                expected_task_id="tg_task_0123456789abcdef",
                control_bundle=control_bundle(),
                before_envelope=task_show_envelope(
                    contract_revision=1,
                    review_generation=0,
                ),
                after_envelope=task_show_envelope(
                    contract_revision=2,
                    review_generation=1,
                ),
                verification_log=[
                    {
                        "ordinal": 1,
                        "kind": "focused",
                        "duration_ms": 12,
                        "result": "success",
                    },
                    {
                        "ordinal": 2,
                        "kind": "lane",
                        "duration_ms": 34,
                        "result": "success",
                    },
                ],
            )

            self.assertEqual(state["eligibility"], "eligible")
            self.assertEqual(state["payload"]["before"]["contract_revision"], 1)
            self.assertEqual(state["payload"]["after"]["review_generation"], 1)
            self.assertEqual(measurement["eligibility"], "eligible")
            data = measurement["payload"]["data"]
            self.assertEqual(data["product_files"], 2)
            self.assertEqual(data["test_files"], 1)
            self.assertGreater(data["product_lines"], 0)
            self.assertGreater(data["test_lines"], 0)
            self.assertGreaterEqual(data["test_cases"], 2)
            self.assertEqual(data["contract_owner_fanout"], 1)
            self.assertEqual(data["inventory_owner_fanout"], 1)
            self.assertEqual(data["maintenance_fanout"], 2)
            self.assertEqual(data["duplicate_contract_locations"], 2)
            self.assertEqual(data["fixture_copy_groups"], 1)
            self.assertEqual(data["verification_escalation"], "proportional")
            self.assertEqual(data["target_change_result"], "detected")
            self.assertEqual(
                [step["kind"] for step in data["verification_steps"]],
                ["focused", "lane"],
            )
            self.assertNotIn("label", json.dumps(measurement, sort_keys=True))
            self.assertNotIn(str(repo), json.dumps(measurement, sort_keys=True))
            self.assertEqual((repo / "sample.py").read_bytes(), expected_final)

    def test_state_pair_requires_same_configured_task_id(self):
        protocol = load_protocol(ROOT)
        common = {
            "unit": "M20.3",
            "scenario_id": "vp_cli_contract",
            "trial_id": "vp_cli_contract.baseline.01",
        }
        before = task_show_envelope(contract_revision=1, review_generation=0)
        after_other_task = task_show_envelope(
            contract_revision=2,
            review_generation=1,
            task_id="tg_task_fedcba9876543210",
        )

        with self.assertRaises(M20ObservationError) as mismatch:
            build_state_pair_observation(
                protocol,
                **common,
                expected_task_id="tg_task_0123456789abcdef",
                before_envelope=before,
                after_envelope=after_other_task,
            )
        self.assertEqual(mismatch.exception.code, "source_drift")

        with self.assertRaises(M20ObservationError) as configured_mismatch:
            build_state_pair_observation(
                protocol,
                **common,
                expected_task_id="tg_task_fedcba9876543210",
                before_envelope=before,
                after_envelope=before,
            )
        self.assertEqual(configured_mismatch.exception.code, "source_drift")

    def test_target_sensitivity_restores_exact_bytes_after_mutated_checker(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            initialize_repo(repo)
            materialize_final(repo)
            original = (repo / "sample.py").read_bytes()

            outcomes = iter(("pass", "fail"))
            with mock.patch(
                "tools.m20_fresh_measurement._checker_outcome",
                side_effect=lambda *_args: next(outcomes),
            ):
                result = target_change_sensitivity(
                    repo,
                    scenario_id="vp_cli_contract",
                    manifest=control_bundle()["reducer_manifest"],
                )

            self.assertEqual(result, ("detected", None))
            self.assertEqual((repo / "sample.py").read_bytes(), original)

            timeout_outcomes = iter(("pass", "timeout"))
            with mock.patch(
                "tools.m20_fresh_measurement._checker_outcome",
                side_effect=lambda *_args: next(timeout_outcomes),
            ):
                timeout_result = target_change_sensitivity(
                    repo,
                    scenario_id="vp_cli_contract",
                    manifest=control_bundle()["reducer_manifest"],
                )

            self.assertEqual(timeout_result, ("unknown", "timeout"))
            self.assertEqual((repo / "sample.py").read_bytes(), original)

    def test_verification_log_accepts_observer_shape_and_empty_is_partial(self):
        steps, escalation, unknowns = reduce_verification_log(
            [
                {
                    "ordinal": 1,
                    "kind": "lane",
                    "duration_ms": 1,
                    "result": "success",
                },
                {
                    "ordinal": 2,
                    "kind": "lane",
                    "duration_ms": 2,
                    "result": "failure",
                },
            ],
        )
        self.assertEqual(escalation, "proportional")
        self.assertEqual([step["ordinal"] for step in steps], [1, 2])
        self.assertEqual(unknowns, [])

        self.assertEqual(
            reduce_verification_log([]),
            (
                [],
                "unknown",
                [
                    {
                        "field": "data.verification_escalation",
                        "reasons": ["not_observable"],
                    }
                ],
            ),
        )
        with self.assertRaises(M20ObservationError):
            reduce_verification_log(
                [
                    {
                        "ordinal": 2,
                        "kind": "focused",
                        "duration_ms": 1,
                        "result": "success",
                    }
                ],
            )
        with self.assertRaises(M20ObservationError):
            reduce_verification_log(
                [
                    {
                        "ordinal": 1,
                        "kind": "focused",
                        "duration_ms": 1,
                        "result": "service_error",
                    }
                ],
            )

    def test_binary_changed_product_keeps_file_count_but_lines_are_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            baseline = initialize_repo(repo)
            (repo / "sample.py").write_bytes(b"\x00changed")

            material = Materialization(repo, baseline)

            self.assertEqual(material.changed_paths, ("sample.py",))
            self.assertIsNone(material.line_total(material.changed_paths))

            with mock.patch(
                "tools.m20_fresh_measurement._checker_outcome",
                return_value="pass",
            ) as checker:
                state, measurement = reduce_m20_3_trial(
                    protocol_for(baseline),
                    repo_root=repo,
                    scenario_id="vp_cli_contract",
                    trial_id="vp_cli_contract.baseline.01",
                    expected_task_id="tg_task_0123456789abcdef",
                    control_bundle=control_bundle(),
                    before_envelope=task_show_envelope(
                        contract_revision=1,
                        review_generation=0,
                    ),
                    after_envelope=task_show_envelope(
                        contract_revision=2,
                        review_generation=1,
                    ),
                    verification_log=[
                        {
                            "ordinal": 1,
                            "kind": "focused",
                            "duration_ms": 1,
                            "result": "success",
                        }
                    ],
                )

            self.assertEqual(state["eligibility"], "eligible")
            self.assertEqual(measurement["eligibility"], "partial")
            data = measurement["payload"]["data"]
            self.assertIsNone(data["product_lines"])
            self.assertIsNone(data["contract_owner_fanout"])
            self.assertEqual(data["target_change_result"], "unknown")
            reasons = {
                item["field"]: item["reasons"]
                for item in measurement["unknowns"]
            }
            self.assertEqual(reasons["data.product_lines"], ["not_observable"])
            self.assertEqual(
                reasons["data.contract_owner_fanout"],
                ["not_observable"],
            )
            self.assertEqual(
                reasons["data.target_change_result"],
                ["not_observable"],
            )
            checker.assert_not_called()

    def test_symlink_and_unreadable_final_sources_fail_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            baseline = initialize_repo(repo)
            sample = repo / "sample.py"
            sample.unlink()
            try:
                sample.symlink_to(repo / "tests" / "test_cli_envelope.py")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")

            material = Materialization(repo, baseline)
            value, reason = material.line_metric(material.changed_paths)
            self.assertIsNone(value)
            self.assertEqual(reason, "not_observable")
            sensitivity = target_change_sensitivity(
                repo,
                scenario_id="vp_cli_contract",
                manifest=control_bundle()["reducer_manifest"],
            )
            self.assertEqual(sensitivity, ("unknown", "not_observable"))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "sample.py").write_text("safe\n", encoding="utf-8")
            with mock.patch(
                "tools.m20_fresh_measurement.os.open",
                side_effect=PermissionError("denied"),
            ):
                with self.assertRaises(M20ObservationError) as unreadable:
                    _final_side(root, "sample.py")
            self.assertEqual(unreadable.exception.code, "source_missing")

    def test_final_source_growth_is_detected_after_bounded_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            sample = root / "sample.py"
            sample.write_bytes(b"before\n")
            real_read = os.read
            changed = False

            def grow_then_read(descriptor: int, count: int) -> bytes:
                nonlocal changed
                if not changed:
                    changed = True
                    with sample.open("ab") as stream:
                        stream.write(b"after\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                return real_read(descriptor, count)

            with mock.patch(
                "tools.m20_fresh_measurement.os.read",
                side_effect=grow_then_read,
            ):
                with self.assertRaises(M20ObservationError) as drift:
                    _final_side(root, "sample.py")
            self.assertEqual(drift.exception.code, "source_drift")

    def test_oversize_sources_cap_only_the_dependent_measurement_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            initialize_repo(repo)
            padding = "# " + ("x" * 512) + "\n"
            write_text(
                repo,
                "sample.py",
                'VALUE = "before"\nCONTRACT = "needle"\n' + padding,
            )
            test_source = (repo / "tests/test_cli_envelope.py").read_text(
                encoding="utf-8"
            )
            write_text(repo, "tests/test_cli_envelope.py", test_source + padding)
            run_git(repo, "add", "--all")
            run_git(repo, "commit", "--quiet", "--amend", "--no-edit")
            baseline = run_git(repo, "rev-parse", "HEAD")
            materialize_final(repo)
            with (repo / "sample.py").open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(padding)
            with (repo / "tests/test_cli_envelope.py").open(
                "a", encoding="utf-8", newline="\n"
            ) as stream:
                stream.write(padding)
            protocol = protocol_for(baseline)

            with (
                mock.patch(
                    "tools.m20_fresh_measurement.MAX_SOURCE_BYTES",
                    128,
                ),
                mock.patch(
                    "tools.m20_fresh_measurement.MAX_GIT_OUTPUT_BYTES",
                    256,
                ),
            ):
                state, measurement = reduce_m20_3_trial(
                    protocol,
                    repo_root=repo,
                    scenario_id="vp_cli_contract",
                    trial_id="vp_cli_contract.baseline.01",
                    expected_task_id="tg_task_0123456789abcdef",
                    control_bundle=control_bundle(),
                    before_envelope=task_show_envelope(
                        contract_revision=1,
                        review_generation=0,
                    ),
                    after_envelope=task_show_envelope(
                        contract_revision=2,
                        review_generation=1,
                    ),
                    verification_log=[
                        {
                            "ordinal": 1,
                            "kind": "focused",
                            "duration_ms": 1,
                            "result": "success",
                        }
                    ],
                )

            self.assertEqual(state["eligibility"], "eligible")
            self.assertEqual(measurement["eligibility"], "partial")
            expected_capped = {
                "data.product_lines",
                "data.test_lines",
                "data.test_cases",
                "data.contract_owner_fanout",
                "data.inventory_owner_fanout",
                "data.duplicate_contract_locations",
                "data.fixture_copy_groups",
            }
            self.assertEqual(
                {item["field"] for item in measurement["unknowns"]},
                expected_capped | {"data.target_change_result"},
            )
            self.assertTrue(
                all(
                    item["reasons"] == ["cap_exceeded"]
                    for item in measurement["unknowns"]
                )
            )
            data = measurement["payload"]["data"]
            for field in expected_capped:
                self.assertEqual(data[field.removeprefix("data.")], MAX_SIGNED_32)
            self.assertEqual(data["target_change_result"], "unknown")
            self.assertEqual(data["product_files"], 2)
            self.assertEqual(data["test_files"], 1)
            self.assertEqual(data["maintenance_fanout"], 2)

    def test_checker_timeout_uses_launch_time_tree_and_joins(self):
        process = mock.Mock()
        process.pid = 4321
        process.wait.side_effect = subprocess.TimeoutExpired("checker", 300)
        tree = mock.Mock()

        with (
            mock.patch(
                "tools.m20_fresh_measurement.subprocess.Popen",
                return_value=process,
            ) as popen,
            mock.patch(
                "tools.m20_fresh_measurement._new_process_tree",
                return_value=tree,
            ),
        ):
            result = _checker_outcome(
                Path("."), CheckerSpec("test", ("-B", "checker.py"))
            )

        self.assertEqual(result, "timeout")
        tree.terminate.assert_called_once_with()
        tree.close.assert_called_once_with()
        if os.name == "nt":
            self.assertEqual(
                popen.call_args.kwargs["creationflags"],
                subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004,
            )
        else:
            self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_checker_wait_error_terminates_tree_and_tree_failure_is_excluded(self):
        process = mock.Mock()
        process.wait.side_effect = OSError("wait failed")
        tree = mock.Mock()
        with (
            mock.patch(
                "tools.m20_fresh_measurement.subprocess.Popen",
                return_value=process,
            ),
            mock.patch(
                "tools.m20_fresh_measurement._new_process_tree",
                return_value=tree,
            ),
        ):
            self.assertEqual(
                _checker_outcome(
                    Path("."), CheckerSpec("test", ("-B", "checker.py"))
                ),
                "unavailable",
            )
        tree.terminate.assert_called_once_with()
        tree.close.assert_called_once_with()

        process.wait.side_effect = subprocess.TimeoutExpired("checker", 300)
        tree.reset_mock()
        tree.terminate.side_effect = M20ObservationError("source_missing")
        with (
            mock.patch(
                "tools.m20_fresh_measurement.subprocess.Popen",
                return_value=process,
            ),
            mock.patch(
                "tools.m20_fresh_measurement._new_process_tree",
                return_value=tree,
            ),
        ):
            with self.assertRaises(M20ObservationError) as teardown:
                _checker_outcome(
                    Path("."), CheckerSpec("test", ("-B", "checker.py"))
                )
        self.assertEqual(teardown.exception.code, "source_missing")
        tree.close.assert_called_once_with()

    def test_posix_process_tree_terminates_the_process_group(self):
        process = mock.Mock()
        process.pid = 4321
        process.wait.return_value = -9
        with (
            mock.patch("tools.m20_fresh_measurement.os.name", "posix"),
            mock.patch(
                "tools.m20_fresh_measurement.os.killpg",
                create=True,
            ) as killpg,
            mock.patch(
                "tools.m20_fresh_measurement.signal.SIGKILL",
                9,
                create=True,
            ),
        ):
            _ProcessTree(process).terminate()
        killpg.assert_called_once_with(4321, 9)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree behavior")
    def test_windows_checker_timeout_stops_descendant_before_return(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "checker.py"
            checker.write_text(
                "import subprocess\n"
                "import sys\n"
                "import time\n"
                "child = (\"from pathlib import Path\\n\"\n"
                "         \"import time\\n\"\n"
                "         \"with Path('heartbeat.bin').open('ab', buffering=0) as stream:\\n\"\n"
                "         \"    for _index in range(400):\\n\"\n"
                "         \"        stream.write(b'x')\\n\"\n"
                "         \"        time.sleep(0.025)\\n\")\n"
                "subprocess.Popen([sys.executable, '-c', child])\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            with mock.patch(
                "tools.m20_fresh_measurement.CHECKER_TIMEOUT_SECONDS",
                0.5,
            ):
                result = _checker_outcome(
                    root,
                    CheckerSpec("test", ("-B", checker.name)),
                )
            self.assertEqual(result, "timeout")
            heartbeat = root / "heartbeat.bin"
            self.assertTrue(heartbeat.exists())
            size_after_return = heartbeat.stat().st_size
            self.assertGreater(size_after_return, 0)
            time.sleep(0.2)
            self.assertEqual(heartbeat.stat().st_size, size_after_return)

    def test_fixed_sensitivity_labels_are_scenario_specific(self):
        self.assertEqual(
            {scenario: spec.label for scenario, spec in FIXED_CHECKERS.items()},
            {
                "vp_cli_contract": "cli_contract",
                "vp_state_transition": "state_transition",
                "vp_release_contract": "release_contract",
            },
        )
        manifest = control_bundle()["reducer_manifest"]
        manifest["target_change"]["verification_label"] = "fast_lane"
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            initialize_repo(repo)
            materialize_final(repo)
            with self.assertRaises(M20ObservationError):
                target_change_sensitivity(
                    repo,
                    scenario_id="vp_cli_contract",
                    manifest=manifest,
                )

    def test_m20_4_reduces_actual_observer_rows_and_boundary_deltas(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            baseline = initialize_repo(repo)
            protocol, episode_plan = m20_4_authority_for(baseline)
            plan_digest = hashlib.sha256(
                canonical_json_bytes(episode_plan)
            ).hexdigest()
            digest_patch = mock.patch(
                "tools.m20_fresh_measurement."
                "M20_4_EPISODE_PLAN_CANONICAL_SHA256",
                plan_digest,
            )
            digest_patch.start()
            self.addCleanup(digest_patch.stop)
            bundle = m20_4_control_bundle()
            scenario_id = "sp_handoff_control"
            trial_id = "sp_handoff_control.broad.01"

            start = capture_boundary_snapshot(
                protocol,
                episode_plan,
                repo_root=repo,
                scenario_id=scenario_id,
                trial_id=trial_id,
                control_bundle=bundle,
                boundary_id="b00_start",
                slot_envelopes={
                    "source_task": task_show_envelope(
                        contract_revision=1,
                        review_generation=0,
                    )
                },
                observer_log_position=0,
            )
            write_text(
                repo,
                "sample.py",
                'VALUE = "after"\nCONTRACT = "needle"\n',
            )
            end = capture_boundary_snapshot(
                protocol,
                episode_plan,
                repo_root=repo,
                scenario_id=scenario_id,
                trial_id=trial_id,
                control_bundle=bundle,
                boundary_id="b10_end",
                slot_envelopes={
                    "source_task": task_show_envelope(
                        contract_revision=2,
                        review_generation=2,
                    )
                },
                observer_log_position=5,
            )
            observer_log = [
                {
                    "command_leaf": "task.show",
                    "task_slot": "source_task",
                    "duration_ms": 1,
                    "result": "success",
                },
                {
                    "command_leaf": "task.edit",
                    "task_slot": "source_task",
                    "duration_ms": 2,
                    "result": "success",
                },
                {
                    "command_leaf": "review.target.set",
                    "task_slot": "source_task",
                    "duration_ms": 3,
                    "result": "input_error",
                },
                {
                    "command_leaf": "review.target.set",
                    "task_slot": "source_task",
                    "duration_ms": 4,
                    "result": "success",
                },
                {
                    "command_leaf": "setup",
                    "task_slot": None,
                    "duration_ms": 5,
                    "result": "success",
                },
            ]

            measurement = reduce_m20_4_measurement(
                protocol,
                episode_plan,
                scenario_id=scenario_id,
                trial_id=trial_id,
                snapshots=[start, end],
                observer_log=observer_log,
            )

            self.assertEqual(measurement["eligibility"], "eligible")
            episode = measurement["payload"]["data"]["episodes"][0]
            self.assertEqual(episode["files_before"], 0)
            self.assertEqual(episode["files_after"], 1)
            self.assertEqual(episode["modules_before"], 0)
            self.assertEqual(episode["modules_after"], 1)
            self.assertEqual(episode["lines_before"], 0)
            self.assertEqual(episode["lines_after"], 2)
            self.assertEqual(episode["contract_revision_before"], 1)
            self.assertEqual(episode["contract_revision_after"], 2)
            self.assertEqual(episode["governance_cycles"], 2)
            self.assertEqual(episode["review_cycles"], 2)
            encoded = json.dumps(measurement, sort_keys=True)
            self.assertNotIn(str(repo), encoded)
            self.assertNotIn("task.edit", encoded)

            nonmonotonic_start = replace(
                start,
                task_states=(
                    ("source_task", "tg_task_0123456789abcdef", 1, 3),
                ),
            )
            with self.assertRaises(M20ObservationError):
                reduce_m20_4_measurement(
                    protocol,
                    episode_plan,
                    scenario_id=scenario_id,
                    trial_id=trial_id,
                    snapshots=[nonmonotonic_start, end],
                    observer_log=observer_log,
                )

    def test_m20_4_boundary_line_cap_propagates_to_episode_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            baseline = initialize_repo(repo)
            protocol, episode_plan = m20_4_authority_for(baseline)
            plan_digest = hashlib.sha256(
                canonical_json_bytes(episode_plan)
            ).hexdigest()
            bundle = m20_4_control_bundle()
            common = {
                "protocol": protocol,
                "episode_plan": episode_plan,
                "repo_root": repo,
                "scenario_id": "sp_handoff_control",
                "trial_id": "sp_handoff_control.broad.01",
                "control_bundle": bundle,
            }
            with (
                mock.patch(
                    "tools.m20_fresh_measurement."
                    "M20_4_EPISODE_PLAN_CANONICAL_SHA256",
                    plan_digest,
                ),
                mock.patch(
                    "tools.m20_fresh_measurement.MAX_SOURCE_BYTES",
                    64,
                ),
            ):
                start = capture_boundary_snapshot(
                    **common,
                    boundary_id="b00_start",
                    slot_envelopes={
                        "source_task": task_show_envelope(
                            contract_revision=1,
                            review_generation=0,
                        )
                    },
                    observer_log_position=0,
                )
                write_text(
                    repo,
                    "sample.py",
                    'VALUE = "after"\nCONTRACT = "needle"\n# '
                    + ("x" * 512)
                    + "\n",
                )
                end = capture_boundary_snapshot(
                    **common,
                    boundary_id="b10_end",
                    slot_envelopes={
                        "source_task": task_show_envelope(
                            contract_revision=1,
                            review_generation=0,
                        )
                    },
                    observer_log_position=0,
                )
                measurement = reduce_m20_4_measurement(
                    protocol,
                    episode_plan,
                    scenario_id="sp_handoff_control",
                    trial_id="sp_handoff_control.broad.01",
                    snapshots=[start, end],
                    observer_log=[],
                )

            self.assertEqual(end.lines, MAX_SIGNED_32)
            self.assertEqual(end.metric_unknowns, (("lines", "cap_exceeded"),))
            self.assertEqual(measurement["eligibility"], "partial")
            episode = measurement["payload"]["data"]["episodes"][0]
            self.assertEqual(episode["lines_after"], MAX_SIGNED_32)
            self.assertEqual(
                measurement["unknowns"],
                [
                    {
                        "field": "data.episodes.0.lines_after",
                        "reasons": ["cap_exceeded"],
                    }
                ],
            )

    def test_m20_4_rejects_tampered_plan_digest_and_base_protocol_digest(self):
        protocol = load_protocol(ROOT)
        episode_plan = load_m20_4_episode_plan(protocol, ROOT)
        common = {
            "scenario_id": "sp_handoff_control",
            "trial_id": "sp_handoff_control.broad.01",
            "snapshots": [],
            "observer_log": [],
        }

        tampered_plan = copy.deepcopy(episode_plan)
        tampered_plan["plans"][-1]["episodes"][0][
            "episode_id"
        ] = "tampered_handoff"
        with self.assertRaises(M20ObservationError) as digest_mismatch:
            reduce_m20_4_measurement(protocol, tampered_plan, **common)
        self.assertEqual(digest_mismatch.exception.code, "source_drift")

        tampered_base = copy.deepcopy(episode_plan)
        tampered_base["base_protocol_sha256"] = "0" * 64
        tampered_digest = hashlib.sha256(
            canonical_json_bytes(tampered_base)
        ).hexdigest()
        with mock.patch(
            "tools.m20_fresh_measurement."
            "M20_4_EPISODE_PLAN_CANONICAL_SHA256",
            tampered_digest,
        ):
            with self.assertRaises(M20ObservationError) as base_mismatch:
                reduce_m20_4_measurement(protocol, tampered_base, **common)
        self.assertEqual(base_mismatch.exception.code, "source_drift")


if __name__ == "__main__":
    unittest.main()
