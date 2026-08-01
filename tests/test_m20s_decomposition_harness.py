from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.m20s_decomposition_harness import (
    AUTHORITY_CONTRACT_REVISION,
    AUTHORITY_REF,
    AUTHORITY_TASK_ID,
    BASELINE_REVISION,
    EPISODE_PLAN_CANONICAL_SHA256,
    M20SDecompositionHarness,
    M20SObservationError,
    PACKAGE_TREE,
    PROTOCOL_CANONICAL_SHA256,
    SCENARIOS,
    canonical_json_bytes,
    load_frozen_contract,
    observation_id,
    validate_observation,
)
from tools.test_lanes import LANE_MODULES


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "m20s_decomposition_harness.py"
FIXTURE_ROOT = ROOT / "fixtures" / "m20s"
RECORD_KEYS = ("split_measurement", "attestation")


def assert_error(testcase: unittest.TestCase, code: str, callback) -> None:
    with testcase.assertRaises(M20SObservationError) as caught:
        callback()
    testcase.assertEqual(caught.exception.code, code)


def copy_contract(destination: Path) -> None:
    target = destination / "fixtures" / "m20s"
    target.mkdir(parents=True)
    for name in ("protocol-v1.json", "episode-plan-v1.json"):
        shutil.copyfile(FIXTURE_ROOT / name, target / name)


def plan_for(harness: M20SDecompositionHarness, scenario: str) -> dict:
    return next(item for item in harness.plan["plans"] if item["scenario_id"] == scenario)


def commitments(index: int = 0) -> dict:
    chars = "123456789abcdef"
    offset = index * 4
    return {
        "broad": {
            "workload_digest": chars[index] * 64,
            "control_digest": chars[offset + 1] * 64,
            "observer_config_digest": chars[offset + 2] * 64,
            "trial_root_digest": chars[offset + 3] * 64,
            "cohort": "fresh_baseline_v1",
        },
        "bounded": {
            "workload_digest": chars[index] * 64,
            "control_digest": chars[offset + 2] * 64,
            "observer_config_digest": chars[offset + 3] * 64,
            "trial_root_digest": chars[offset + 4] * 64,
            "cohort": "fresh_baseline_v1",
        },
    }


def envelope(
    harness: M20SDecompositionHarness,
    scenario: str,
    arm: str,
    record_key: str,
    payload: dict | None,
    *,
    eligibility: str = "eligible",
    unknown_reasons: list[str] | None = None,
    unknowns: list[dict[str, str]] | None = None,
) -> dict:
    trial_id = f"{scenario}.{arm}.01"
    return {
        "schema": "m20s-decomposition-observation-v1",
        "authority_task_id": AUTHORITY_TASK_ID,
        "authority_contract_revision": AUTHORITY_CONTRACT_REVISION,
        "authority_ref": AUTHORITY_REF,
        "baseline_revision": BASELINE_REVISION,
        "package_tree": PACKAGE_TREE,
        "protocol_sha256": PROTOCOL_CANONICAL_SHA256,
        "episode_plan_sha256": EPISODE_PLAN_CANONICAL_SHA256,
        "observation_id": observation_id(scenario, arm, trial_id, record_key),
        "scenario_id": scenario,
        "arm": arm,
        "trial_id": trial_id,
        "record_key": record_key,
        "evidence_class": (
            "machine_observed" if record_key == "split_measurement" else "observer_attested"
        ),
        "eligibility": eligibility,
        "unknown_reasons": unknown_reasons or [],
        "unknowns": unknowns or [],
        "payload": payload,
    }


def arm_records(
    harness: M20SDecompositionHarness,
    scenario: str,
    arm: str,
    commitment: dict,
    *,
    improvement: str | None = None,
) -> list[dict]:
    episodes = plan_for(harness, scenario)["episodes"]
    measurement = []
    attestation = []
    for index, planned in enumerate(episodes):
        machine = {
            "episode_id": planned["episode_id"],
            "files_before": 1,
            "files_after": 2,
            "modules_before": 1,
            "modules_after": 2,
            "lines_before": 10,
            "lines_after": 12,
            "contract_revision_before": 1,
            "contract_revision_after": 1,
            "review_generation_before": 1,
            "review_generation_after": 1,
            "governance_cycles": 1,
            "review_cycles": 0,
        }
        independence = {
            "acceptance_independent": "yes",
            "verification_independent": "yes",
            "commit_independent": "yes",
            "completion_independent": "yes",
        }
        if arm == "broad" and index == 0:
            if improvement == "vector":
                machine["governance_cycles"] = 2
                independence["completion_independent"] = "no"
            elif improvement == "contract":
                machine["contract_revision_after"] = 2
            elif improvement == "review":
                machine["review_cycles"] = 1
        measurement.append(machine)
        attestation.append(
            {
                "episode_id": planned["episode_id"],
                "phase": planned["phase"],
                "cause": planned["cause"],
                "current_response": "keep_current",
                **independence,
            }
        )
    return [
        envelope(
            harness,
            scenario,
            arm,
            "split_measurement",
            {"episodes": measurement},
        ),
        envelope(
            harness,
            scenario,
            arm,
            "attestation",
            {
                "cohort": commitment["cohort"],
                "workload_digest": commitment["workload_digest"],
                "control_digest": commitment["control_digest"],
                "outcome": "completed",
                "reference_opens": 2,
                "clarification_turns": 0,
                "manual_inputs": 1,
                "governance_invocations": 4,
                "reviewer_invocations": 1,
                "episodes": attestation,
            },
        ),
    ]


def receipt_for(records: list[dict], state: dict) -> tuple[bytes, dict]:
    records.sort(key=lambda item: item["observation_id"])
    corpus = canonical_json_bytes(records)
    return corpus, {
        "schema": "m20s-decomposition-collection-receipt-v1",
        "unit": "M20S.2",
        "authority_task_id": AUTHORITY_TASK_ID,
        "authority_contract_revision": AUTHORITY_CONTRACT_REVISION,
        "authority_ref": AUTHORITY_REF,
        "baseline_revision": BASELINE_REVISION,
        "package_tree": PACKAGE_TREE,
        "protocol_sha256": PROTOCOL_CANONICAL_SHA256,
        "episode_plan_sha256": EPISODE_PLAN_CANONICAL_SHA256,
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


class M20SDecompositionHarnessTests(unittest.TestCase):
    def make_harness(self, root: Path) -> M20SDecompositionHarness:
        copy_contract(root)
        return M20SDecompositionHarness(root, _allow_test_root=True)

    def run_pair(
        self,
        harness: M20SDecompositionHarness,
        index: int,
        *,
        improvement: str | None,
    ) -> dict:
        scenario = SCENARIOS[index]
        launch = commitments(index)
        harness.start_pair(scenario, launch)
        first = harness.reduce_arm(
            scenario,
            "broad",
            arm_records(harness, scenario, "broad", launch["broad"], improvement=improvement),
        )
        self.assertEqual(first["attempted_pairs"], index)
        self.assertEqual(first["in_flight_arms"], 1)
        return harness.reduce_arm(
            scenario,
            "bounded",
            arm_records(harness, scenario, "bounded", launch["bounded"], improvement=improvement),
        )

    def test_frozen_identity_inventory_record_shape_and_digests(self):
        protocol, plan = load_frozen_contract(ROOT)
        self.assertEqual(
            {
                key: protocol[key]
                for key in (
                    "authority_task_id",
                    "authority_contract_revision",
                    "authority_ref",
                    "baseline_revision",
                    "package_tree",
                )
            },
            {
                "authority_task_id": AUTHORITY_TASK_ID,
                "authority_contract_revision": AUTHORITY_CONTRACT_REVISION,
                "authority_ref": AUTHORITY_REF,
                "baseline_revision": BASELINE_REVISION,
                "package_tree": PACKAGE_TREE,
            },
        )
        self.assertEqual(
            protocol["inherited"],
            {
                "source": "m20_retired_aggregate_v1",
                "denominator": 4,
                "eligible_pairs": 1,
                "qualifying_pairs": 1,
                "unavailable_pairs": 3,
                "handoff_control_eligible": True,
                "conflict": False,
            },
        )
        self.assertEqual(protocol["bounds"]["record_bytes"], 16_384)
        self.assertEqual(protocol["bounds"]["corpus_bytes"], 65_536)
        self.assertEqual(protocol["bounds"]["journal_bytes"], 65_536)
        self.assertEqual(protocol["bounds"]["max_unknowns"], 8)
        self.assertEqual(
            protocol["records"]["machine_vector"],
            [
                "files",
                "modules",
                "lines",
                "contract_revision",
                "review_generation",
                "governance_cycles",
                "review_cycles",
            ],
        )
        self.assertEqual(
            hashlib.sha256(canonical_json_bytes(protocol)).hexdigest(),
            PROTOCOL_CANONICAL_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
            EPISODE_PLAN_CANONICAL_SHA256,
        )
        self.assertEqual(
            {
                key: plan[key]
                for key in (
                    "authority_task_id",
                    "authority_contract_revision",
                    "authority_ref",
                    "baseline_revision",
                    "package_tree",
                )
            },
            {
                "authority_task_id": AUTHORITY_TASK_ID,
                "authority_contract_revision": AUTHORITY_CONTRACT_REVISION,
                "authority_ref": AUTHORITY_REF,
                "baseline_revision": BASELINE_REVISION,
                "package_tree": PACKAGE_TREE,
            },
        )
        trial_ids = [arm["trial_id"] for item in plan["plans"] for arm in item["arms"]]
        self.assertEqual(len(trial_ids), 6)
        self.assertEqual(len(set(trial_ids)), 6)
        self.assertTrue(all("_alternate." in trial_id for trial_id in trial_ids))
        for replacement in protocol["replacements"]:
            self.assertNotIn(f"{replacement['replaces']}.broad.01", trial_ids)
            self.assertNotIn(f"{replacement['replaces']}.bounded.01", trial_ids)

    def test_episode_plan_fixes_boundaries_task_slots_and_same_ids(self):
        _protocol, plan = load_frozen_contract(ROOT)
        for item in plan["plans"]:
            self.assertEqual(item["boundaries"], ["b00_start", "b10_transition", "b20_end"])
            episode_ids = [episode["episode_id"] for episode in item["episodes"]]
            for arm in item["arms"]:
                self.assertEqual([slot[0] for slot in arm["episode_task_slots"]], episode_ids)
            broad_slots = {slot[1] for slot in item["arms"][0]["episode_task_slots"]}
            bounded_slots = {slot[1] for slot in item["arms"][1]["episode_task_slots"]}
            self.assertEqual(len(broad_slots), 1)
            self.assertEqual(len(bounded_slots), 2)

    def test_runtime_privacy_retirement_and_release_lane_boundaries(self):
        protocol, _plan = load_frozen_contract(ROOT)
        self.assertEqual(
            protocol["runtime_boundary"],
            {
                "accepts": "sanitized_arm_records_only",
                "launch_subject": False,
                "shell": False,
                "network": False,
                "canonical_db_write": False,
                "real_target_mutation": False,
            },
        )
        self.assertTrue(
            all(value is False for key, value in protocol["retention"].items() if key.startswith("raw_"))
        )
        self.assertEqual(protocol["retention"]["retirement_owner"], "TG-M20S.2")
        self.assertFalse((FIXTURE_ROOT / "decomposition-collection-receipt.json").exists())
        self.assertIn("test_m20s_decomposition_harness", LANE_MODULES["release"])

    def test_module_is_lean_and_has_no_launcher_network_db_or_product_import(self):
        source = SOURCE.read_text(encoding="utf-8")
        tests = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(imports.isdisjoint({"subprocess", "socket", "sqlite3", "urllib", "requests", "http"}))
        self.assertNotIn("task_governance_tool", source)
        self.assertNotIn("Popen", source)
        self.assertLess(len(source.splitlines()), 1_200)
        self.assertLess(len(source.splitlines()) + len(tests.splitlines()), 1_900)
        self.assertFalse(any((ROOT / "tools").glob("m20_*.py")))
        self.assertFalse(any((ROOT / "tests").glob("test_m20_*.py")))

    def test_each_of_three_reviewed_qualification_conditions(self):
        for improvement in ("vector", "contract", "review"):
            with self.subTest(improvement=improvement), tempfile.TemporaryDirectory() as temporary:
                harness = self.make_harness(Path(temporary))
                decision = self.run_pair(harness, 0, improvement=improvement)
                self.assertEqual(decision["decision"], "proceed_to_design")
                self.assertEqual(
                    (decision["eligible_pairs"], decision["qualifying_pairs"], decision["unavailable_pairs"]),
                    (2, 2, 2),
                )

    def test_nonqualifying_pair_requires_bounded_independence_and_no_improvement(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            decision = self.run_pair(harness, 0, improvement=None)
            self.assertIsNone(decision["decision"])
            self.assertEqual(
                (decision["eligible_pairs"], decision["qualifying_pairs"], decision["unavailable_pairs"]),
                (2, 1, 2),
            )

        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            launch = commitments(0)
            harness.start_pair(SCENARIOS[0], launch)
            broad = arm_records(harness, SCENARIOS[0], "broad", launch["broad"], improvement="vector")
            bounded = arm_records(harness, SCENARIOS[0], "bounded", launch["bounded"], improvement=None)
            bounded[1]["payload"]["episodes"][0]["completion_independent"] = "no"
            harness.reduce_arm(SCENARIOS[0], "broad", broad)
            decision = harness.reduce_arm(SCENARIOS[0], "bounded", bounded)
            self.assertEqual(decision["qualifying_pairs"], 1)

    def test_partial_record_makes_pair_unavailable_without_imputation(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            launch = commitments(0)
            scenario = SCENARIOS[0]
            harness.start_pair(scenario, launch)
            broad = arm_records(harness, scenario, "broad", launch["broad"], improvement="vector")
            broad[1]["payload"]["reference_opens"] = None
            broad[1]["eligibility"] = "partial"
            broad[1]["unknown_reasons"] = ["not_observable"]
            broad[1]["unknowns"] = [
                {"field": "payload.reference_opens", "reason": "not_observable"}
            ]
            self.assertEqual(validate_observation(broad[1], plan=harness.plan)["eligibility"], "partial")
            for reason in ("timeout", "cap_exceeded"):
                field_limited = copy.deepcopy(broad[1])
                field_limited["unknown_reasons"] = [reason]
                field_limited["unknowns"] = [
                    {"field": "payload.reference_opens", "reason": reason}
                ]
                self.assertEqual(
                    validate_observation(field_limited, plan=harness.plan)["eligibility"],
                    "partial",
                )
            invalidating = copy.deepcopy(broad[1])
            invalidating["unknown_reasons"] = ["source_missing"]
            invalidating["unknowns"] = [
                {"field": "payload.reference_opens", "reason": "source_missing"}
            ]
            assert_error(
                self,
                "source_drift",
                lambda: validate_observation(invalidating, plan=harness.plan),
            )
            harness.reduce_arm(scenario, "broad", broad)
            decision = harness.reduce_arm(
                scenario,
                "bounded",
                arm_records(harness, scenario, "bounded", launch["bounded"], improvement="vector"),
            )
            self.assertEqual(
                (decision["eligible_pairs"], decision["qualifying_pairs"], decision["unavailable_pairs"]),
                (1, 1, 3),
            )
            self.assertIsNone(decision["decision"])

    def test_invalid_or_oversized_candidate_is_terminal_excluded_and_not_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            launch = commitments(0)
            scenario = SCENARIOS[0]
            harness.start_pair(scenario, launch)
            bad = arm_records(harness, scenario, "broad", launch["broad"])
            bad[0]["prompt"] = "private"
            reduced = harness.reduce_arm(scenario, "broad", bad)
            self.assertIs(reduced["accepted"], False)
            self.assertEqual(reduced["reason"], "contaminated")
            assert_error(
                self,
                "attempt_not_started",
                lambda: harness.reduce_arm(scenario, "broad", bad),
            )
            harness.terminalize_arm(scenario, "bounded", "source_missing")
            journal = json.loads((Path(temporary) / "dist/m20s/decomposition-attempt-journal.json").read_text())
            broad_state = journal["attempts"][f"{scenario}.broad.01"]
            self.assertTrue(all(record["payload"] is None for record in broad_state["records"]))
            self.assertNotIn("private", canonical_json_bytes(journal).decode("utf-8"))

        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            sample = arm_records(harness, SCENARIOS[0], "broad", commitments(0)["broad"])[0]
            sample["padding"] = "x" * 33_000
            assert_error(
                self,
                "cap_exceeded",
                lambda: validate_observation(sample, plan=harness.plan),
            )

    def test_pair_start_is_atomic_ordered_isolated_and_one_shot(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            assert_error(
                self,
                "scenario_order_required",
                lambda: harness.start_pair(SCENARIOS[1], commitments(1)),
            )
            invalid = commitments(0)
            invalid["bounded"]["trial_root_digest"] = invalid["broad"]["trial_root_digest"]
            assert_error(self, "source_drift", lambda: harness.start_pair(SCENARIOS[0], invalid))
            harness.start_pair(SCENARIOS[0], commitments(0))
            assert_error(
                self,
                "attempt_in_flight",
                lambda: harness.start_pair(SCENARIOS[0], commitments(0)),
            )
            harness.terminalize_arm(SCENARIOS[0], "broad", "timeout")
            harness.terminalize_arm(SCENARIOS[0], "bounded", "timeout")
            assert_error(
                self,
                "scenario_order_required",
                lambda: harness.start_pair(SCENARIOS[0], commitments(0)),
            )
            reused_root = commitments(1)
            reused_root["broad"]["trial_root_digest"] = commitments(0)["broad"][
                "trial_root_digest"
            ]
            assert_error(
                self,
                "source_drift",
                lambda: harness.start_pair(SCENARIOS[1], reused_root),
            )
            harness.start_pair(SCENARIOS[1], commitments(1))

    def test_first_positive_stops_before_unlaunched_pair_and_receipt_is_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = self.make_harness(root)
            decision = self.run_pair(harness, 0, improvement="vector")
            self.assertEqual(decision["decision"], "proceed_to_design")
            assert_error(
                self,
                "collection_stopped",
                lambda: harness.start_pair(SCENARIOS[1], commitments(1)),
            )
            receipt = harness.finalize()
            self.assertEqual(
                set(receipt),
                {
                    "schema", "unit", "authority_task_id", "authority_contract_revision",
                    "authority_ref", "baseline_revision", "package_tree", "protocol_sha256",
                    "episode_plan_sha256", "status", "artifact_status", "retirement_revision",
                    "attempted_pairs", "attempted_arms", "record_count", "corpus_bytes",
                    "corpus_sha256", "eligible_pairs", "qualifying_pairs", "unavailable_pairs",
                    "decision",
                },
            )
            self.assertEqual(receipt["record_count"], 4)
            self.assertEqual(receipt["attempted_arms"], 2)
            self.assertIsNone(receipt["retirement_revision"])
            corpus = json.loads((root / "dist/m20s/decomposition-observations.json").read_text())
            self.assertEqual(len(corpus), 4)
            self.assertEqual(
                [record["observation_id"] for record in corpus],
                sorted(record["observation_id"] for record in corpus),
            )
            reopened = M20SDecompositionHarness(root, _allow_test_root=True)
            self.assertEqual(reopened.status()["artifact_status"], "closed")
            assert_error(
                self,
                "collection_closed",
                lambda: reopened.start_pair(SCENARIOS[1], commitments(1)),
            )

            receipt_path = root / "fixtures/m20s/decomposition-collection-receipt.json"
            receipt_path.write_text('{"schema":"foreign"}', encoding="utf-8")
            assert_error(self, "parse_failed", reopened.status)

    def test_negative_and_exhaustion_truth_table_and_twelve_record_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = self.make_harness(root)
            decisions = [self.run_pair(harness, index, improvement=None) for index in range(3)]
            self.assertIsNone(decisions[0]["decision"])
            self.assertIsNone(decisions[1]["decision"])
            self.assertEqual(decisions[2]["decision"], "no_follow_up")
            self.assertEqual(
                (decisions[2]["eligible_pairs"], decisions[2]["qualifying_pairs"], decisions[2]["unavailable_pairs"]),
                (4, 1, 0),
            )
            receipt = harness.finalize()
            self.assertEqual(receipt["record_count"], 12)
            self.assertLessEqual(receipt["corpus_bytes"], 65_536)

        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            for index, scenario in enumerate(SCENARIOS):
                harness.start_pair(scenario, commitments(index))
                harness.terminalize_arm(scenario, "broad", "source_missing")
                exhausted = harness.terminalize_arm(scenario, "bounded", "source_missing")
            self.assertEqual(exhausted["decision"], "observe_more")
            self.assertEqual(
                (exhausted["eligible_pairs"], exhausted["qualifying_pairs"], exhausted["unavailable_pairs"]),
                (1, 1, 3),
            )

    def test_closed_receipt_rejects_an_otherwise_coherent_nonterminal_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            state = self.run_pair(harness, 0, improvement=None)
            self.assertIsNone(state["decision"])
            journal = harness._read()
            records = [
                record
                for attempt in journal["attempts"].values()
                for record in attempt["records"]
            ]
            corpus, receipt = receipt_for(records, state)
            harness.corpus.write_bytes(corpus)
            harness.receipt.write_bytes(canonical_json_bytes(receipt))
            harness.journal.unlink()
            assert_error(self, "source_drift", harness.status)

    def test_closed_receipt_rejects_pairs_after_the_first_terminal_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = self.make_harness(Path(temporary))
            records = []
            for index, improvement in ((0, "vector"), (1, None)):
                scenario = SCENARIOS[index]
                launch = commitments(index)
                for arm in ("broad", "bounded"):
                    records.extend(
                        arm_records(
                            harness,
                            scenario,
                            arm,
                            launch[arm],
                            improvement=improvement,
                        )
                    )
            state = {
                "attempted_pairs": 2,
                "attempted_arms": 4,
                "eligible_pairs": 3,
                "qualifying_pairs": 2,
                "unavailable_pairs": 1,
                "decision": "proceed_to_design",
            }
            corpus, receipt = receipt_for(records, state)
            harness._prepare()
            harness.corpus.write_bytes(corpus)
            harness.receipt.write_bytes(canonical_json_bytes(receipt))
            assert_error(self, "source_drift", harness.status)

    def test_fixture_tampering_and_foreign_root_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_contract(root)
            assert_error(self, "repository_mismatch", lambda: M20SDecompositionHarness(root))
            protocol_path = root / "fixtures/m20s/protocol-v1.json"
            protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
            protocol["package_tree"] = "0" * 40
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            assert_error(
                self,
                "source_drift",
                lambda: M20SDecompositionHarness(root, _allow_test_root=True),
            )


if __name__ == "__main__":
    unittest.main()
