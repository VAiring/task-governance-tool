import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]

from tools.m20_fresh_collection import (
    ASSESSMENT_SCHEMA,
    OBSERVER_SNAPSHOT_SCHEMA,
    FreshCollectionController,
    build_attestation_observation,
    main,
)
from tools.m20_fresh_lifecycle import FreshCollectionLifecycle
from tools.m20_observation import (
    M20ObservationError,
    _excluded_rows,
    canonical_json_bytes,
    derive_inventory,
    load_protocol,
)
from tools.m20_trial_observer import CONFIG_SCHEMA, LOG_SCHEMA


class FreshCollectionControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = load_protocol(ROOT)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp.name).resolve()
        fixture_root = self.repo_root / "fixtures" / "m20"
        fixture_root.mkdir(parents=True)
        for name in ("protocol-v1.json", "m20.4-episode-plan-v1.json"):
            shutil.copyfile(ROOT / "fixtures" / "m20" / name, fixture_root / name)
        (self.repo_root / ".gitignore").write_text("/dist/\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet", str(self.repo_root)],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        self.trial_temp = tempfile.TemporaryDirectory()
        self.trial_root = Path(self.trial_temp.name).resolve()
        self.scenario = "vp_cli_contract"
        self.trial = f"{self.scenario}.baseline.01"
        self.task_id = "tg_task_0123456789abcdef"

    def tearDown(self):
        self.trial_temp.cleanup()
        self.temp.cleanup()

    def assert_m20_error(self, callback, code):
        with self.assertRaises(M20ObservationError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def write_json(self, name, value):
        path = self.trial_root / name
        path.write_bytes(canonical_json_bytes(value))
        return path

    def control(self):
        return {
            "workload": "PRIVATE WORKLOAD bytes",
            "delivered_request": "PRIVATE delivered request",
            "neutral_clarification": "PRIVATE neutral clarification",
            "reducer_manifest": {
                "schema": "m20-reducer-manifest-v1",
                "scenario_id": self.scenario,
                "arm": "baseline",
                "owner_slots": ["core"],
                "contract_probes": [],
                "inventory_probes": [],
                "maintenance_selectors": [],
                "fixture_probes": [],
                "verification_labels": [
                    {"label": "cli_contract", "kind": "focused"}
                ],
                "target_change": {
                    "selector": "tests/test_cli_envelope.py",
                    "before_lf": "before\n",
                    "after_lf": "after\n",
                    "verification_label": "cli_contract",
                },
            },
        }

    def observer_log(self):
        return {
            "schema": LOG_SCHEMA,
            "unit": "M20.3",
            "scenario_id": self.scenario,
            "trial_id": self.trial,
            "arm": "baseline",
            "taskgov": [
                {
                    "command_leaf": "task.show",
                    "task_slot": None,
                    "duration_ms": 3,
                    "result": "success",
                }
            ],
            "verifications": [
                {
                    "ordinal": 1,
                    "kind": "focused",
                    "duration_ms": 5,
                    "result": "success",
                }
            ],
        }

    def observer_config(self):
        return {
            "schema": CONFIG_SCHEMA,
            "unit": "M20.3",
            "scenario_id": self.scenario,
            "trial_id": self.trial,
            "arm": "baseline",
            "task_slots": [
                {"task_slot": "primary", "task_id": self.task_id}
            ],
            "verification_labels": [
                {
                    "label": "cli_contract",
                    "kind": "focused",
                    "argv": [
                        "-B",
                        "-m",
                        "unittest",
                        "tests.test_cli_envelope",
                    ],
                }
            ],
        }

    def observer_snapshot(self):
        return {
            "schema": OBSERVER_SNAPSHOT_SCHEMA,
            "unit": "M20.3",
            "scenario_id": self.scenario,
            "trial_id": self.trial,
            "arm": "baseline",
            "outcome": "completed",
            "reference_opens": 4,
            "clarification_turns": 0,
            "manual_inputs": 1,
            "reviewer_invocations": 1,
            "unknowns": [],
        }

    def assessment(self):
        return {
            "schema": ASSESSMENT_SCHEMA,
            "unit": "M20.3",
            "scenario_id": self.scenario,
            "trial_id": self.trial,
            "arm": "baseline",
            "assessment_kind": "verification_proportionality",
            "assessment": {
                "distinct_risks": 1,
                "new_cases": 1,
                "redundant_responsibilities": 0,
                "verification_fact_codes": [],
                "manual_reentry_fact_codes": [],
                "responsibility_pattern_codes": [],
                "reuse": "reused",
                "instruction_fit": "yes",
                "minimal_receipt_fit": "yes",
            },
            "unknowns": [],
        }

    def write_inputs(self):
        taskgov = self.trial_root / "task-governance-tool" / "scripts" / "taskgov.py"
        taskgov.parent.mkdir(parents=True, exist_ok=True)
        taskgov.write_text("# isolated fixture\n", encoding="utf-8")
        self.write_json("control.json", self.control())
        self.write_json("observer-config.json", self.observer_config())
        self.write_json("observer-log.json", self.observer_log())
        self.write_json("observer-snapshot.json", self.observer_snapshot())
        self.write_json("assessment.json", self.assessment())
        self.write_json("before.json", {"private": "before state body"})
        self.write_json("after.json", {"private": "after state body"})

    def excluded_machine_records(self):
        rows = [
            row
            for row in derive_inventory(self.protocol, "M20.3")
            if row[2] == self.trial and row[4] != "fresh_agent_trial"
        ]
        records = _excluded_rows(self.protocol, rows, "source_missing")
        return records[0], records[1]

    def reduce(self, controller):
        return controller.reduce_m20_3(
            trial_root=self.trial_root,
            scenario_id=self.scenario,
            trial_id=self.trial,
            control_path="control.json",
            observer_config_path="observer-config.json",
            observer_log_path="observer-log.json",
            observer_snapshot_path="observer-snapshot.json",
            assessment_path="assessment.json",
            before_state_path="before.json",
            after_state_path="after.json",
        )

    def test_reduce_builds_attestation_and_finishes_exact_attempt(self):
        self.write_inputs()
        controller = FreshCollectionController(self.repo_root, "M20.3")
        controller.start((self.trial,))
        with patch(
            "tools.m20_fresh_collection.reduce_m20_3_trial",
            return_value=self.excluded_machine_records(),
        ) as reducer:
            result = self.reduce(controller)
        reducer.assert_called_once()
        self.assertEqual(
            reducer.call_args.kwargs["expected_task_id"], self.task_id
        )
        self.assertEqual(result["record_count"], 3)
        self.assertEqual(result["eligible_records"], 1)
        self.assertEqual(result["excluded_records"], 2)

        lifecycle = controller.lifecycle
        journal = lifecycle._read_journal(self.protocol)
        records = journal["attempts"][self.trial]["records"]
        attestation = next(
            record for record in records if record["channel"] == "fresh_agent_trial"
        )
        self.assertEqual(attestation["payload"]["governance_invocations"], 1)
        retained = lifecycle.journal_path.read_bytes()
        self.assertNotIn(b"PRIVATE", retained)
        self.assertNotIn(str(self.trial_root).encode("utf-8"), retained)

    def test_attestation_builder_is_shared_with_future_m20_4_reducer(self):
        record = build_attestation_observation(
            self.protocol,
            unit="M20.3",
            scenario_id=self.scenario,
            trial_id=self.trial,
            arm="baseline",
            control_bundle=self.control(),
            observer_log=self.observer_log(),
            observer_snapshot=self.observer_snapshot(),
            assessment=self.assessment(),
        )
        self.assertEqual(record["eligibility"], "eligible")
        encoded = canonical_json_bytes(record)
        self.assertNotIn(b"PRIVATE", encoded)
        self.assertEqual(
            set(record["payload"]),
            {
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
            },
        )

    def test_partial_attestation_carries_exact_common_and_assessment_unknowns(self):
        snapshot = self.observer_snapshot()
        snapshot.update(reference_opens=None, reviewer_invocations=8)
        snapshot["unknowns"] = [
            {"field": "reference_opens", "reason": "not_observable"},
            {"field": "reviewer_invocations", "reason": "cap_exceeded"},
        ]
        assessment = self.assessment()
        assessment["assessment"].update(
            distinct_risks=None,
            verification_fact_codes=None,
            instruction_fit="unknown",
        )
        assessment["unknowns"] = [
            {
                "field": "assessment.distinct_risks",
                "reason": "observer_uncertain",
            },
            {
                "field": "assessment.instruction_fit",
                "reason": "observer_uncertain",
            },
            {
                "field": "assessment.verification_fact_codes",
                "reason": "observer_uncertain",
            },
        ]
        record = build_attestation_observation(
            self.protocol,
            unit="M20.3",
            scenario_id=self.scenario,
            trial_id=self.trial,
            arm="baseline",
            control_bundle=self.control(),
            observer_log=self.observer_log(),
            observer_snapshot=snapshot,
            assessment=assessment,
        )
        self.assertEqual(record["eligibility"], "partial")
        self.assertEqual(
            set(record["unknown_reasons"]),
            {"not_observable", "cap_exceeded", "observer_uncertain"},
        )
        self.assertEqual(record["payload"]["reference_opens"], None)
        self.assertEqual(record["payload"]["reviewer_invocations"], 8)
        self.assertEqual(
            {item["field"] for item in record["unknowns"]},
            {
                "reference_opens",
                "reviewer_invocations",
                "assessment.distinct_risks",
                "assessment.instruction_fit",
                "assessment.verification_fact_codes",
            },
        )

    def test_partial_attestation_rejects_unknown_value_reason_mismatches(self):
        cases = []

        missing_common = self.observer_snapshot()
        missing_common["reference_opens"] = None
        cases.append((missing_common, self.assessment()))

        invalid_cap = self.observer_snapshot()
        invalid_cap["reviewer_invocations"] = 7
        invalid_cap["unknowns"] = [
            {"field": "reviewer_invocations", "reason": "cap_exceeded"}
        ]
        cases.append((invalid_cap, self.assessment()))

        invalid_not_observable = self.observer_snapshot()
        invalid_not_observable["reference_opens"] = 1
        invalid_not_observable["unknowns"] = [
            {"field": "reference_opens", "reason": "not_observable"}
        ]
        cases.append((invalid_not_observable, self.assessment()))

        missing_assessment = self.assessment()
        missing_assessment["assessment"]["distinct_risks"] = None
        cases.append((self.observer_snapshot(), missing_assessment))

        concrete_assessment = self.assessment()
        concrete_assessment["unknowns"] = [
            {
                "field": "assessment.distinct_risks",
                "reason": "observer_uncertain",
            }
        ]
        cases.append((self.observer_snapshot(), concrete_assessment))

        wrong_reason = self.assessment()
        wrong_reason["assessment"]["reuse"] = "unknown"
        wrong_reason["unknowns"] = [
            {"field": "assessment.reuse", "reason": "not_observable"}
        ]
        cases.append((self.observer_snapshot(), wrong_reason))

        for index, (snapshot, assessment) in enumerate(cases):
            with self.subTest(case=index):
                self.assert_m20_error(
                    lambda snapshot=snapshot, assessment=assessment: (
                        build_attestation_observation(
                            self.protocol,
                            unit="M20.3",
                            scenario_id=self.scenario,
                            trial_id=self.trial,
                            arm="baseline",
                            control_bundle=self.control(),
                            observer_log=self.observer_log(),
                            observer_snapshot=snapshot,
                            assessment=assessment,
                        )
                    ),
                    "parse_failed",
                )

    def test_validate_control_returns_only_digests_and_cli_hides_raw_input(self):
        self.write_json("control.json", self.control())
        controller = FreshCollectionController(self.repo_root, "M20.3")
        result = controller.validate_control(
            trial_root=self.trial_root,
            scenario_id=self.scenario,
            trial_id=self.trial,
            arm="baseline",
            control_path="control.json",
        )
        self.assertEqual(set(result), {"workload_digest", "control_digest"})
        self.assertRegex(result["workload_digest"], r"^[0-9a-f]{64}$")

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "--repo",
                    str(self.repo_root),
                    "--unit",
                    "M20.3",
                    "validate-control",
                    "--trial-root",
                    str(self.trial_root),
                    "--scenario",
                    self.scenario,
                    "--trial",
                    self.trial,
                    "--arm",
                    "baseline",
                    "--control",
                    "control.json",
                ]
            )
        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["workload_digest"], result["workload_digest"])
        self.assertNotIn("PRIVATE", stdout.getvalue())
        self.assertNotIn(str(self.trial_root), stdout.getvalue())

    def test_snapshot_task_returns_only_status_and_slot_for_started_attempt(self):
        controller = FreshCollectionController(self.repo_root, "M20.3")
        controller.start((self.trial,))
        observer = MagicMock()
        observer.config = {
            "unit": "M20.3",
            "trial_id": self.trial,
        }
        observer.snapshot_task.return_value = {
            "private": "raw Task envelope"
        }
        with patch(
            "tools.m20_fresh_collection.TrialObserver",
            return_value=observer,
        ) as constructor:
            result = controller.snapshot_task(
                trial_root=self.trial_root,
                observer_config_path="observer-config.json",
                observer_log_path="observer-log.json",
                task_slot="primary_task",
                output_path="snapshots/before.json",
            )
        constructor.assert_called_once_with(
            self.trial_root,
            "observer-config.json",
            "observer-log.json",
        )
        observer.snapshot_task.assert_called_once_with(
            "primary_task", "snapshots/before.json"
        )
        self.assertEqual(
            result, {"status": "written", "task_slot": "primary_task"}
        )
        self.assertNotIn("private", canonical_json_bytes(result).decode("utf-8"))

        observer.reset_mock()
        stdout = io.StringIO()
        with patch(
            "tools.m20_fresh_collection.TrialObserver",
            return_value=observer,
        ), contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "--repo",
                    str(self.repo_root),
                    "--unit",
                    "M20.3",
                    "snapshot-task",
                    "--trial-root",
                    str(self.trial_root),
                    "--observer-config",
                    "observer-config.json",
                    "--observer-log",
                    "observer-log.json",
                    "--task-slot",
                    "primary_task",
                    "--output",
                    "snapshots/after.json",
                ]
            )
        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "written")
        self.assertEqual(output["task_slot"], "primary_task")
        self.assertNotIn("private", stdout.getvalue())
        self.assertNotIn(str(self.trial_root), stdout.getvalue())

    def test_snapshot_task_requires_started_attempt_before_public_read(self):
        controller = FreshCollectionController(self.repo_root, "M20.3")
        observer = MagicMock()
        observer.config = {
            "unit": "M20.3",
            "trial_id": self.trial,
        }
        with patch(
            "tools.m20_fresh_collection.TrialObserver",
            return_value=observer,
        ):
            self.assert_m20_error(
                lambda: controller.snapshot_task(
                    trial_root=self.trial_root,
                    observer_config_path="observer-config.json",
                    observer_log_path="observer-log.json",
                    task_slot="primary_task",
                    output_path="snapshots/before.json",
                ),
                "attempt_not_started",
            )
        observer.snapshot_task.assert_not_called()

    def test_invalid_input_terminalizes_without_retaining_raw_value(self):
        self.write_inputs()
        control_path = self.trial_root / "control.json"
        control_path.write_text(
            json.dumps(self.control(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        controller = FreshCollectionController(self.repo_root, "M20.3")
        controller.start((self.trial,))
        self.assert_m20_error(lambda: self.reduce(controller), "source_drift")

        journal = controller.lifecycle._read_journal(self.protocol)
        state = journal["attempts"][self.trial]
        self.assertEqual(state["status"], "reduced")
        self.assertEqual(len(state["records"]), 3)
        self.assertEqual(
            {tuple(record["unknown_reasons"]) for record in state["records"]},
            {("source_drift",)},
        )
        retained = controller.lifecycle.journal_path.read_bytes()
        self.assertNotIn(b"PRIVATE", retained)
        self.assertNotIn(str(self.trial_root).encode("utf-8"), retained)

    def test_cross_path_is_rejected_and_terminalized(self):
        self.write_inputs()
        controller = FreshCollectionController(self.repo_root, "M20.3")
        controller.start((self.trial,))
        self.assert_m20_error(
            lambda: controller.reduce_m20_3(
                trial_root=self.trial_root,
                scenario_id=self.scenario,
                trial_id=self.trial,
                control_path=str(self.trial_root / "control.json"),
                observer_config_path="observer-config.json",
                observer_log_path="observer-log.json",
                observer_snapshot_path="observer-snapshot.json",
                assessment_path="assessment.json",
                before_state_path="before.json",
                after_state_path="after.json",
            ),
            "unsafe_source_path",
        )
        journal = controller.lifecycle._read_journal(self.protocol)
        self.assertEqual(journal["attempts"][self.trial]["status"], "reduced")

    def test_reduce_requires_started_attempt_before_trial_source_access(self):
        controller = FreshCollectionController(self.repo_root, "M20.3")
        with patch(
            "tools.m20_fresh_collection._canonical_object",
            side_effect=AssertionError("trial source touched"),
        ) as reader:
            self.assert_m20_error(
                lambda: self.reduce(controller),
                "attempt_not_started",
            )
        reader.assert_not_called()

    def test_competing_reducer_fails_without_reopening_trial_sources(self):
        self.write_inputs()
        first = FreshCollectionController(self.repo_root, "M20.3")
        second = FreshCollectionController(self.repo_root, "M20.3")
        first.start((self.trial,))
        first_reader_entered = threading.Event()
        release_first_reader = threading.Event()
        reader_threads = []
        reader_threads_lock = threading.Lock()

        from tools import m20_fresh_collection

        original_reader = m20_fresh_collection._canonical_object

        def blocking_reader(*args, **kwargs):
            with reader_threads_lock:
                reader_threads.append(threading.get_ident())
            if not first_reader_entered.is_set():
                first_reader_entered.set()
                if not release_first_reader.wait(timeout=10):
                    raise AssertionError("first reducer was not released")
            return original_reader(*args, **kwargs)

        def run_reducer(controller):
            thread_id = threading.get_ident()
            try:
                return thread_id, self.reduce(controller)
            except M20ObservationError as error:
                return thread_id, error.code

        with patch(
            "tools.m20_fresh_collection._canonical_object",
            side_effect=blocking_reader,
        ), patch(
            "tools.m20_fresh_collection.reduce_m20_3_trial",
            return_value=self.excluded_machine_records(),
        ), ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(run_reducer, first)
            self.assertTrue(first_reader_entered.wait(timeout=10))
            second_future = executor.submit(run_reducer, second)
            try:
                second_thread, second_result = second_future.result(timeout=10)
            finally:
                release_first_reader.set()
            first_thread, first_result = first_future.result(timeout=10)

        self.assertIsInstance(first_result, dict)
        self.assertEqual(second_result, "attempt_not_started")
        self.assertIn(first_thread, reader_threads)
        self.assertNotIn(second_thread, reader_threads)
        journal = first.lifecycle._read_journal(self.protocol)
        self.assertEqual(journal["attempts"][self.trial]["status"], "reduced")

    def test_failure_after_claim_finishes_once_without_raw_reread(self):
        self.write_inputs()
        controller = FreshCollectionController(self.repo_root, "M20.3")
        controller.start((self.trial,))
        with patch(
            "tools.m20_fresh_collection._canonical_object",
            side_effect=M20ObservationError("source_drift"),
        ) as reader, patch.object(
            controller.lifecycle,
            "finish",
            wraps=controller.lifecycle.finish,
        ) as finish:
            self.assert_m20_error(lambda: self.reduce(controller), "source_drift")

        reader.assert_called_once()
        finish.assert_called_once()
        journal = controller.lifecycle._read_journal(self.protocol)
        retained = journal["attempts"][self.trial]
        self.assertEqual(retained["status"], "reduced")
        self.assertEqual(
            {tuple(record["unknown_reasons"]) for record in retained["records"]},
            {("source_drift",)},
        )

    def test_reduce_rejects_same_nonconfigured_task_in_both_state_reads(self):
        self.write_inputs()
        other_task_id = "tg_task_fedcba9876543210"
        state = {"data": {"task": {"task_id": other_task_id}}}
        self.write_json("before.json", state)
        self.write_json("after.json", state)
        controller = FreshCollectionController(self.repo_root, "M20.3")
        controller.start((self.trial,))
        self.assert_m20_error(lambda: self.reduce(controller), "source_drift")

        journal = controller.lifecycle._read_journal(self.protocol)
        retained = journal["attempts"][self.trial]
        self.assertEqual(retained["status"], "reduced")
        self.assertEqual(
            {tuple(record["unknown_reasons"]) for record in retained["records"]},
            {("source_drift",)},
        )
        self.assertNotIn(
            other_task_id.encode("ascii"),
            controller.lifecycle.journal_path.read_bytes(),
        )

    def test_cli_start_check_and_parse_errors_are_sanitized(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "--repo",
                    str(self.repo_root),
                    "--unit",
                    "M20.3",
                    "start",
                    "--attempt",
                    self.trial,
                ]
            )
        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertTrue(output["ok"])
        self.assertNotIn(str(self.repo_root), stdout.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "--repo",
                    str(self.repo_root),
                    "--unit",
                    "M20.3",
                    "check",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["started_attempts"], 1)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["--repo", "PRIVATE/PATH"])
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"ok": False, "error_code": "input_error"},
        )
        self.assertNotIn("PRIVATE", stderr.getvalue())

        process = subprocess.run(
            [
                os.sys.executable,
                "-B",
                "tools/m20_fresh_collection.py",
                "--repo",
                "PRIVATE/PATH",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(
            json.loads(process.stderr),
            {"ok": False, "error_code": "input_error"},
        )
        self.assertNotIn("PRIVATE", process.stderr)

    def test_controller_never_deletes_trial_material(self):
        self.write_inputs()
        controller = FreshCollectionController(self.repo_root, "M20.3")
        controller.start((self.trial,))
        with patch(
            "tools.m20_fresh_collection.reduce_m20_3_trial",
            return_value=self.excluded_machine_records(),
        ):
            self.reduce(controller)
        self.assertTrue((self.trial_root / "control.json").exists())
        self.assertTrue((self.trial_root / "observer-log.json").exists())

    def test_controller_has_no_runner_network_or_delete_surface(self):
        source = (ROOT / "tools" / "m20_fresh_collection.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(
            imported.isdisjoint(
                {"http", "requests", "shutil", "socket", "subprocess", "urllib"}
            )
        )
        self.assertTrue(
            calls.isdisjoint(
                {"Popen", "remove", "rmdir", "rmtree", "run", "unlink"}
            )
        )


if __name__ == "__main__":
    unittest.main()
