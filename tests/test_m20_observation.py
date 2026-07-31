import ast
import builtins
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import tools.m20_observation as m20_observation
from tools.m20_observation import (
    AttemptJournal,
    CollectionLock,
    M20ObservationError,
    build_observation,
    canonical_json_bytes,
    derive_inventory,
    load_protocol,
    load_m20_4_episode_plan,
    observation_id,
    reduce_task_show_state,
    serialize_corpus,
    validate_control_bundle,
    validate_observation,
)


ROOT = Path(__file__).resolve().parents[1]
M20_2 = "M20.2"
_DEFAULT_PAYLOAD = object()


class M20ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = load_protocol(ROOT)
        cls.inventory = derive_inventory(cls.protocol, M20_2)
        cls.scenarios = {
            item["scenario_id"]: item
            for item in cls.protocol["m20_2"]["harness_scenarios"]
        }
        cls.cohorts = {
            item["scenario_id"]: item
            for item in cls.protocol["m20_2"]["retrospective_cohorts"]
        }
        cls.episode_plans = {
            (item["scenario_id"], item["trial_id"]): item
            for item in load_m20_4_episode_plan(cls.protocol, ROOT)["plans"]
        }

    def assert_m20_error(self, call, expected_code=None):
        with self.assertRaises(M20ObservationError) as raised:
            call()
        self.assertIsInstance(raised.exception.code, str)
        self.assertTrue(raised.exception.code)
        if expected_code is not None:
            self.assertEqual(raised.exception.code, expected_code)

    @staticmethod
    def cli_operation(ordinal, phase, command_leaf):
        return {
            "ordinal": ordinal,
            "phase": phase,
            "command_leaf": command_leaf,
            "duration_ms": ordinal,
            "duration_capped": False,
            "result": "success",
            "exit_code": 0,
            "warning_codes": [],
            "error_codes": [],
            "stdout_bytes": 16,
            "stderr_bytes": 0,
        }

    def cli_payload(self, scenario_id):
        scenario = self.scenarios[scenario_id]
        return {
            "operations": [
                self.cli_operation(ordinal, phase, command_leaf)
                for ordinal, (phase, command_leaf) in enumerate(
                    scenario["operations"],
                    start=1,
                )
            ]
        }

    @staticmethod
    def state_defaults():
        return {
            "task_status": "in_progress",
            "contract_revision": 1,
            "review_generation": 0,
            "receipts_current": 0,
            "qualifying_passes": 0,
            "changes_requested_current": 0,
            "findings_open_high": 0,
            "findings_open_medium": 0,
            "findings_open_low": 0,
            "handoffs_pending": 0,
            "handoffs_delivered": 0,
            "handoffs_withdrawn": 0,
            "completion_cycles": 0,
            "verification_attestation": None,
            "verification_detail": "absent",
        }

    def state_payload(self, scenario_id):
        scenario = self.scenarios[scenario_id]
        before = self.state_defaults()
        before.update(scenario["before"])
        after = self.state_defaults()
        after.update(scenario["after"])
        return {"before": before, "after": after}

    def references_for(self, scenario_id, metric):
        tasks = self.cohorts[scenario_id]["tasks"]
        if metric == "git_wall_clock_span_ms":
            return sorted(
                {
                    revision
                    for _scope, _task_id, revision, _revision_kind in tasks
                    if len(revision) == 40
                    and all(character in "0123456789abcdef" for character in revision)
                }
            )
        return sorted(task_id for _scope, task_id, _revision, _kind in tasks)

    def payload_for_row(self, row):
        _unit, scenario_id, _trial_id, _evidence_class, channel, record_key = row
        if channel == "cli_invocation":
            return self.cli_payload(scenario_id)
        if channel == "state_projection":
            return self.state_payload(scenario_id)
        if channel == "task_git_reconstruction":
            return {
                "metric": record_key,
                "value": 0,
                "coverage": "complete",
                "references": self.references_for(scenario_id, record_key),
            }
        self.fail(f"unexpected M20.2 channel: {channel}")

    def observation_for_row(self, row, *, payload=_DEFAULT_PAYLOAD, unknowns=()):
        unit, scenario_id, trial_id, evidence_class, channel, record_key = row
        return build_observation(
            self.protocol,
            unit=unit,
            scenario_id=scenario_id,
            trial_id=trial_id,
            evidence_class=evidence_class,
            channel=channel,
            record_key=record_key,
            payload=(
                self.payload_for_row(row)
                if payload is _DEFAULT_PAYLOAD
                else payload
            ),
            unknowns=unknowns,
        )

    def complete_corpus(self):
        return [self.observation_for_row(row) for row in self.inventory]

    @staticmethod
    def verification_measurement_payload(*, escalation="proportional", steps=None):
        if steps is None:
            steps = [
                {
                    "ordinal": 1,
                    "kind": "focused",
                    "duration_ms": 1,
                    "duration_capped": False,
                    "result": "success",
                }
            ]
        return {
            "measurement_kind": "verification_proportionality",
            "data": {
                "product_files": 1,
                "test_files": 1,
                "product_lines": 1,
                "test_lines": 1,
                "test_cases": 1,
                "contract_owner_fanout": 1,
                "inventory_owner_fanout": 1,
                "maintenance_fanout": 1,
                "duplicate_contract_locations": 0,
                "fixture_copy_groups": 0,
                "verification_escalation": escalation,
                "target_change_result": "detected",
                "verification_steps": steps,
            },
        }

    @staticmethod
    def verification_attestation_payload():
        return {
            "cohort": "fresh_baseline_v1",
            "arm": "baseline",
            "workload_digest": "a" * 64,
            "control_digest": "b" * 64,
            "outcome": "completed",
            "reference_opens": 1,
            "clarification_turns": 0,
            "manual_inputs": 0,
            "governance_invocations": 1,
            "reviewer_invocations": 0,
            "assessment_kind": "verification_proportionality",
            "assessment": {
                "distinct_risks": 1,
                "new_cases": 1,
                "redundant_responsibilities": 0,
                "verification_fact_codes": ["result"],
                "manual_reentry_fact_codes": [],
                "responsibility_pattern_codes": [],
                "reuse": "reused",
                "instruction_fit": "yes",
                "minimal_receipt_fit": "yes",
            },
        }

    @staticmethod
    def split_measurement_payload(episode_ids=("episode_01",)):
        return {
            "measurement_kind": "split_pressure",
            "data": {
                "episodes": [
                    {
                        "episode_id": episode_id,
                        "files_before": 1,
                        "files_after": 2,
                        "modules_before": 1,
                        "modules_after": 1,
                        "lines_before": 10,
                        "lines_after": 20,
                        "contract_revision_before": 1,
                        "contract_revision_after": 1,
                        "review_generation_before": 0,
                        "review_generation_after": 0,
                        "governance_cycles": 1,
                        "review_cycles": 0,
                    }
                    for episode_id in episode_ids
                ]
            },
        }

    @staticmethod
    def split_attestation_payload(
        scenario_id,
        trial_id,
        episode_ids=("episode_01",),
    ):
        arm = trial_id.rsplit(".", 2)[1]
        handoff_control = scenario_id == "sp_handoff_control"
        return {
            "cohort": "fresh_baseline_v1",
            "arm": arm,
            "workload_digest": "a" * 64,
            "control_digest": "b" * 64,
            "outcome": "handed_off" if handoff_control else "completed",
            "reference_opens": 1,
            "clarification_turns": 0,
            "manual_inputs": 0,
            "governance_invocations": 1,
            "reviewer_invocations": 0,
            "assessment_kind": "split_pressure",
            "assessment": {
                "episodes": [
                    {
                        "episode_id": episode_id,
                        "phase": "implementation",
                        "cause": (
                            "out_of_scope_control"
                            if handoff_control
                            else "multiple_outcomes"
                        ),
                        "current_response": (
                            "handoff" if handoff_control else "keep_current"
                        ),
                        "acceptance_independent": "yes",
                        "verification_independent": "yes",
                        "commit_independent": "yes",
                        "completion_independent": "yes",
                    }
                    for episode_id in episode_ids
                ]
            },
        }

    def complete_m20_4_corpus(self):
        records = []
        for row in derive_inventory(self.protocol, "M20.4"):
            unit, scenario_id, trial_id, evidence_class, channel, record_key = row
            episode_ids = tuple(
                episode["episode_id"]
                for episode in self.episode_plans[
                    (scenario_id, trial_id)
                ]["episodes"]
            )
            if channel == "trial_measurement":
                payload = self.split_measurement_payload(episode_ids)
            elif channel == "fresh_agent_trial":
                payload = self.split_attestation_payload(
                    scenario_id,
                    trial_id,
                    episode_ids,
                )
            elif channel == "state_projection":
                payload = {
                    "before": self.state_defaults(),
                    "after": self.state_defaults(),
                }
            else:  # pragma: no cover - fixed protocol inventory guard
                self.fail(f"unexpected M20.4 channel: {channel}")
            records.append(
                build_observation(
                    self.protocol,
                    unit=unit,
                    scenario_id=scenario_id,
                    trial_id=trial_id,
                    evidence_class=evidence_class,
                    channel=channel,
                    record_key=record_key,
                    payload=payload,
                )
            )
        return records

    @staticmethod
    def write_protocol_root(root, protocol_bytes):
        protocol_path = root / "fixtures" / "m20" / "protocol-v1.json"
        protocol_path.parent.mkdir(parents=True)
        protocol_path.write_bytes(protocol_bytes)
        return protocol_path

    @staticmethod
    def receipt_for(raw, records):
        counts = Counter(record["eligibility"] for record in records)
        return {
            "schema": "m20-collection-receipt-v1",
            "unit": "M20.2",
            "authority_revision": (
                "a77afbe0140fef416cceeee529e9ff2c985a8e4d"
            ),
            "baseline_revision": (
                "43c91d5987b0c35c66f834789aea782e98dcaff7"
            ),
            "protocol_sha256": m20_observation.PROTOCOL_CANONICAL_SHA256,
            "status": "closed",
            "artifact_status": "retained",
            "retirement_revision": None,
            "record_count": len(records),
            "corpus_bytes": len(raw),
            "corpus_sha256": hashlib.sha256(raw).hexdigest(),
            "eligible_records": counts["eligible"],
            "partial_records": counts["partial"],
            "excluded_records": counts["excluded"],
            "outcome": "retrospective_launch_failed",
        }

    def write_terminal_fixture(self, root, *, retain_corpus):
        self.write_protocol_root(
            root,
            (ROOT / "fixtures" / "m20" / "protocol-v1.json").read_bytes(),
        )
        records = [
            (
                self.observation_for_row(
                    row,
                    payload=None,
                    unknowns=[
                        {"field": "payload", "reasons": ["source_missing"]}
                    ],
                )
                if row[3] == "historically_reconstructed"
                else self.observation_for_row(row)
            )
            for row in self.inventory
        ]
        raw = serialize_corpus(self.protocol, M20_2, records)
        receipt_path = root / "fixtures" / "m20" / "m20.2-collection-receipt.json"
        receipt_path.write_bytes(
            canonical_json_bytes(self.receipt_for(raw, records)) + b"\n"
        )
        if retain_corpus:
            corpus_path = root / "dist" / "m20" / "m20.2-observations.json"
            corpus_path.parent.mkdir(parents=True)
            corpus_path.write_bytes(raw)
        return raw

    @staticmethod
    def task_show_envelope(*, status="done"):
        cycle = {
            "verification_attestation": True,
        }
        return {
            "ok": True,
            "command": "task.show",
            "project_id": "project_fixture",
            "data": {
                "task": {
                    "status": status,
                    "review_target_generation": 2,
                },
                "events": [],
                "suggested_next_action": "continue",
                "review_evidence": {
                    "target": {"kind": "git_commit", "value": "a" * 40, "generation": 2},
                    "gate": {
                        "review_tier": 2,
                        "required_independent_passes": 2,
                        "qualifying_independent_passes": 2,
                        "fallback_kind": "none",
                        "satisfied": True,
                    },
                    "counts": {
                        "receipts_total": 3,
                        "receipts_current_generation": 2,
                        "changes_requested_current_generation": 0,
                        "open_high": 0,
                        "open_medium": 0,
                        "open_low": 1,
                    },
                    "blocking_findings": [],
                    "recent_receipts": [],
                    "recent_findings": [],
                },
                "handoff_summary": {
                    "pending_handoff": 1,
                    "handed_off": 2,
                    "handoff_withdrawn_by_user": 3,
                },
                "contract": {
                    "revision": 4,
                    "scope": "",
                    "acceptance": "",
                    "constraints": "",
                    "authority_ref": "",
                    "change_reason": "",
                    "created_at": None,
                },
                "latest_checkpoint": None,
                "effort_advisory_enabled": False,
                "completion_history": {
                    "total": 1,
                    "returned_count": 1,
                    "truncated": False,
                    "legacy_history_incomplete": False,
                    "cycles": [cycle],
                },
            },
            "warnings": [],
            "errors": [],
        }

    @staticmethod
    def control_bundle():
        return {
            "workload": "日本語の通常作業 PRIVATE_WORKLOAD",
            "delivered_request": "normal request PRIVATE_REQUEST",
            "neutral_clarification": "neutral response PRIVATE_CLARIFICATION",
            "reducer_manifest": {
                "schema": "m20-reducer-manifest-v1",
                "scenario_id": "vp_cli_contract",
                "arm": "baseline",
                "owner_slots": ["core"],
                "contract_probes": [],
                "inventory_probes": [],
                "maintenance_selectors": [],
                "fixture_probes": [],
                "verification_labels": [
                    {"label": "focused-check", "kind": "focused"}
                ],
                "target_change": {
                    "selector": "tests/test_example.py",
                    "before_lf": "before\n",
                    "after_lf": "after\n",
                    "verification_label": "focused-check",
                },
            },
        }

    def test_protocol_and_m20_2_inventory_are_exact(self):
        authority = self.protocol["authority"]
        self.assertEqual(
            authority,
            {
                "contract_id": "TG-M20-OPERATIONAL-BASELINE",
                "contract_revision": 1,
                "baseline_revision": "43c91d5987b0c35c66f834789aea782e98dcaff7",
                "authority_revision": "a77afbe0140fef416cceeee529e9ff2c985a8e4d",
            },
        )
        self.assertEqual(len(self.inventory), 46)
        self.assertEqual(len(set(self.inventory)), 46)
        self.assertEqual(
            Counter(row[4] for row in self.inventory),
            {
                "cli_invocation": 5,
                "state_projection": 5,
                "task_git_reconstruction": 36,
            },
        )
        self.assertEqual(
            Counter(row[1] for row in self.inventory),
            {
                "gov_tier1_commitless": 2,
                "gov_tier2_snapshot": 2,
                "gov_pause_resume": 2,
                "gov_handoff_continue": 2,
                "gov_reopen_rereview": 2,
                "m19_preparation_reconstruction": 12,
                "m19_publication_reconstruction": 12,
                "m19_postrelease_reconstruction": 12,
            },
        )
        for row in self.inventory:
            with self.subTest(row=row):
                if row[4] == "task_git_reconstruction":
                    self.assertIsNone(row[2])
                    self.assertEqual(row[5], self.payload_for_row(row)["metric"])
                else:
                    self.assertEqual(row[2], f"{row[1]}.harness.01")

    def test_protocol_digest_rejects_semantic_mutation_not_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pretty = json.dumps(
                self.protocol,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            protocol_path = self.write_protocol_root(root, pretty)
            self.assertEqual(
                load_protocol(root)["authority"],
                self.protocol["authority"],
            )

            mutated = copy.deepcopy(self.protocol)
            mutated["m20_2"]["harness_scenarios"][0]["operations"][0] = [
                "diagnose",
                "setup",
            ]
            protocol_path.write_bytes(canonical_json_bytes(mutated))
            self.assert_m20_error(
                lambda: load_protocol(root),
                "source_drift",
            )

    def test_m20_4_episode_plan_is_pinned_and_fail_closed(self):
        path = ROOT / m20_observation.M20_4_EPISODE_PLAN_RELATIVE_PATH
        raw = path.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            m20_observation.M20_4_EPISODE_PLAN_RAW_SHA256,
        )
        plan = load_m20_4_episode_plan(self.protocol, ROOT)
        self.assertEqual(len(plan["plans"]), 9)
        self.assertEqual(
            hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
            m20_observation.M20_4_EPISODE_PLAN_CANONICAL_SHA256,
        )

        invalid_plans = []
        duplicate_boundary = copy.deepcopy(plan)
        duplicate_boundary["plans"][0]["boundaries"][1] = "b00_start"
        invalid_plans.append(("duplicate_boundary", duplicate_boundary))

        reverse_interval = copy.deepcopy(plan)
        reverse_interval["plans"][0]["episodes"][0].update(
            start_boundary="b10_transition",
            end_boundary="b00_start",
        )
        invalid_plans.append(("reverse_interval", reverse_interval))

        overlap = copy.deepcopy(plan)
        overlap["plans"][0]["episodes"][1][
            "start_boundary"
        ] = "b00_start"
        invalid_plans.append(("overlap", overlap))

        unknown_slot = copy.deepcopy(plan)
        unknown_slot["plans"][0]["episodes"][0]["task_slot"] = "unknown_slot"
        invalid_plans.append(("unknown_slot", unknown_slot))

        duplicate_episode = copy.deepcopy(plan)
        duplicate_episode["plans"][0]["episodes"][1][
            "episode_id"
        ] = duplicate_episode["plans"][0]["episodes"][0]["episode_id"]
        invalid_plans.append(("duplicate_episode", duplicate_episode))

        paired_mismatch = copy.deepcopy(plan)
        paired_mismatch["plans"][1]["episodes"][1][
            "episode_id"
        ] = "different_outcome"
        invalid_plans.append(("paired_mismatch", paired_mismatch))

        control_multiple = copy.deepcopy(plan)
        control = control_multiple["plans"][-1]
        control["boundaries"].insert(1, "b05_transition")
        control["episodes"] = [
            {
                "episode_id": "first_control",
                "task_slot": "source_task",
                "start_boundary": "b00_start",
                "end_boundary": "b05_transition",
            },
            {
                "episode_id": "second_control",
                "task_slot": "source_task",
                "start_boundary": "b05_transition",
                "end_boundary": "b10_end",
            },
        ]
        invalid_plans.append(("control_multiple", control_multiple))

        for label, candidate in invalid_plans:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                candidate_path = (
                    root / m20_observation.M20_4_EPISODE_PLAN_RELATIVE_PATH
                )
                candidate_path.parent.mkdir(parents=True)
                candidate_raw = (
                    json.dumps(candidate, ensure_ascii=False, indent=2)
                    + "\n"
                ).encode("utf-8")
                candidate_path.write_bytes(candidate_raw)
                with (
                    patch.object(
                        m20_observation,
                        "M20_4_EPISODE_PLAN_RAW_SHA256",
                        hashlib.sha256(candidate_raw).hexdigest(),
                    ),
                    patch.object(
                        m20_observation,
                        "M20_4_EPISODE_PLAN_CANONICAL_SHA256",
                        hashlib.sha256(
                            canonical_json_bytes(candidate)
                        ).hexdigest(),
                    ),
                ):
                    self.assert_m20_error(
                        lambda: load_m20_4_episode_plan(
                            self.protocol,
                            root,
                        ),
                        "source_drift",
                    )

    def test_canonical_json_and_observation_identity_have_golden_bytes(self):
        self.assertEqual(
            canonical_json_bytes({"z": "日本", "a": 1}),
            b'{"a":1,"z":"\xe6\x97\xa5\xe6\x9c\xac"}',
        )
        first = next(
            row
            for row in self.inventory
            if row[1] == "gov_tier1_commitless"
            and row[4] == "cli_invocation"
        )
        self.assertEqual(
            observation_id(self.protocol, first),
            "m20obs_b5d531bce4d3afb5946a10e02505653bbeae029cfcec95da7a1af93c9930c3c6",
        )
        changed = list(first)
        changed[5] = "state_pair"
        self.assertNotEqual(
            observation_id(self.protocol, first),
            observation_id(self.protocol, changed),
        )
        self.assert_m20_error(lambda: canonical_json_bytes({"bad": 1.5}))
        self.assert_m20_error(lambda: canonical_json_bytes({"bad": "\ud800"}))

    def test_build_and_validate_observation_enforce_exact_envelope(self):
        observation = self.observation_for_row(self.inventory[0])
        self.assertEqual(validate_observation(self.protocol, observation), observation)
        self.assertEqual(observation["eligibility"], "eligible")
        self.assertEqual(observation["unknown_reasons"], [])
        self.assertEqual(observation["unknowns"], [])
        self.assertEqual(
            set(observation),
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
            },
        )
        mutations = []
        extra = copy.deepcopy(observation)
        extra["raw_prompt"] = "PRIVATE"
        mutations.append(extra)
        wrong_identity = copy.deepcopy(observation)
        wrong_identity["observation_id"] = "m20obs_" + "0" * 64
        mutations.append(wrong_identity)
        wrong_authority = copy.deepcopy(observation)
        wrong_authority["authority_revision"] = "0" * 40
        mutations.append(wrong_authority)
        wrong_channel = copy.deepcopy(observation)
        wrong_channel["channel"] = "task_git_reconstruction"
        mutations.append(wrong_channel)
        for candidate in mutations:
            with self.subTest(candidate=set(candidate)):
                self.assert_m20_error(
                    lambda candidate=candidate: validate_observation(
                        self.protocol,
                        candidate,
                    )
                )

    def test_unknown_eligibility_and_reason_applicability_are_exact(self):
        cli_row = next(row for row in self.inventory if row[4] == "cli_invocation")
        capped_payload = self.payload_for_row(cli_row)
        capped_payload["operations"][0]["stdout_bytes"] = 1_048_576
        partial = self.observation_for_row(
            cli_row,
            payload=capped_payload,
            unknowns=[
                {
                    "field": "operations.0.stdout_bytes",
                    "reasons": ["cap_exceeded"],
                }
            ],
        )
        self.assertEqual(partial["eligibility"], "partial")
        self.assertEqual(partial["unknown_reasons"], ["cap_exceeded"])
        self.assertEqual(validate_observation(self.protocol, partial), partial)

        historical_row = next(
            row
            for row in self.inventory
            if row[4] == "task_git_reconstruction"
        )
        partial_historical_payload = self.payload_for_row(historical_row)
        partial_historical_payload.update(value=None, coverage="partial")
        partial_historical = self.observation_for_row(
            historical_row,
            payload=partial_historical_payload,
            unknowns=[
                {"field": "value", "reasons": ["not_reconstructable"]}
            ],
        )
        self.assertEqual(partial_historical["eligibility"], "partial")

        excluded = self.observation_for_row(
            historical_row,
            payload=None,
            unknowns=[{"field": "payload", "reasons": ["source_missing"]}],
        )
        self.assertEqual(excluded["eligibility"], "excluded")
        self.assertEqual(excluded["unknown_reasons"], ["source_missing"])

        invalid_reason = copy.deepcopy(partial)
        invalid_reason["unknowns"][0]["reasons"] = ["not_reconstructable"]
        invalid_reason["unknown_reasons"] = ["not_reconstructable"]
        self.assert_m20_error(
            lambda: validate_observation(self.protocol, invalid_reason)
        )

    def test_impossible_unknown_payload_and_operation_combinations_fail_closed(self):
        state_row = next(row for row in self.inventory if row[4] == "state_projection")
        state_payload = self.payload_for_row(state_row)
        state_payload["before"]["task_status"] = None
        self.assert_m20_error(
            lambda: self.observation_for_row(state_row, payload=state_payload),
            "parse_failed",
        )
        attestation_unknown = self.payload_for_row(state_row)
        self.assert_m20_error(
            lambda: self.observation_for_row(
                state_row,
                payload=attestation_unknown,
                unknowns=[
                    {
                        "field": "before.verification_attestation",
                        "reasons": ["not_observable"],
                    }
                ],
            ),
            "parse_failed",
        )
        false_attestation = self.payload_for_row(state_row)
        false_attestation["after"]["verification_attestation"] = False
        self.assert_m20_error(
            lambda: self.observation_for_row(
                state_row,
                payload=false_attestation,
            ),
            "parse_failed",
        )
        partial_state_row = next(
            row
            for row in derive_inventory(self.protocol, "M20.3")
            if row[4] == "state_projection"
        )
        partial_state_payload = {
            "before": self.state_defaults(),
            "after": self.state_defaults(),
        }
        partial_state_payload["after"].update(
            task_status="done",
            completion_cycles=1,
            verification_attestation=None,
        )
        partial_state = build_observation(
            self.protocol,
            unit=partial_state_row[0],
            scenario_id=partial_state_row[1],
            trial_id=partial_state_row[2],
            evidence_class=partial_state_row[3],
            channel=partial_state_row[4],
            record_key=partial_state_row[5],
            payload=partial_state_payload,
            unknowns=[
                {
                    "field": "after.verification_attestation",
                    "reasons": ["not_observable"],
                }
            ],
        )
        self.assertEqual(partial_state["eligibility"], "partial")

        excluded_measurement_row = next(
            row
            for row in derive_inventory(self.protocol, "M20.4")
            if row[1] == "sp_in_scope_discovery"
            and row[2] == "sp_in_scope_discovery.broad.01"
            and row[4] == "trial_measurement"
        )
        episode_ids = tuple(
            episode["episode_id"]
            for episode in self.episode_plans[
                (
                    excluded_measurement_row[1],
                    excluded_measurement_row[2],
                )
            ]["episodes"]
        )
        excluded_payload = self.split_measurement_payload(episode_ids)
        excluded_payload["data"]["episodes"][0]["files_before"] = None
        field_excluded = build_observation(
            self.protocol,
            unit=excluded_measurement_row[0],
            scenario_id=excluded_measurement_row[1],
            trial_id=excluded_measurement_row[2],
            evidence_class=excluded_measurement_row[3],
            channel=excluded_measurement_row[4],
            record_key=excluded_measurement_row[5],
            payload=excluded_payload,
            unknowns=[
                {
                    "field": "data.episodes.0.files_before",
                    "reasons": ["source_drift"],
                }
            ],
        )
        self.assertEqual(field_excluded["eligibility"], "excluded")
        self.assertIsNotNone(field_excluded["payload"])
        excluded_corpus = self.complete_m20_4_corpus()
        excluded_corpus = [
            (
                field_excluded
                if record["observation_id"]
                == field_excluded["observation_id"]
                else record
            )
            for record in excluded_corpus
        ]
        self.assertIsInstance(
            serialize_corpus(
                self.protocol,
                "M20.4",
                excluded_corpus,
            ),
            bytes,
        )

        cli_row = next(row for row in self.inventory if row[4] == "cli_invocation")
        cap_mismatch = self.payload_for_row(cli_row)
        cap_mismatch["operations"][0]["stdout_bytes"] = 1_048_575
        self.assert_m20_error(
            lambda: self.observation_for_row(
                cli_row,
                payload=cap_mismatch,
                unknowns=[
                    {
                        "field": "operations.0.stdout_bytes",
                        "reasons": ["cap_exceeded"],
                    }
                ],
            ),
            "parse_failed",
        )

        invalidating_payload = self.payload_for_row(cli_row)
        self.assert_m20_error(
            lambda: self.observation_for_row(
                cli_row,
                payload=invalidating_payload,
                unknowns=[{"field": "payload", "reasons": ["source_missing"]}],
            ),
            "parse_failed",
        )

        wrong_sequence = self.payload_for_row(cli_row)
        wrong_sequence["operations"][0], wrong_sequence["operations"][1] = (
            wrong_sequence["operations"][1],
            wrong_sequence["operations"][0],
        )
        self.assert_m20_error(
            lambda: self.observation_for_row(cli_row, payload=wrong_sequence),
            "source_drift",
        )

    def test_m20_3_escalation_and_observer_unknown_reasons_are_derived(self):
        measurement_row = next(
            row
            for row in derive_inventory(self.protocol, "M20.3")
            if row[1] == "vp_cli_contract" and row[4] == "trial_measurement"
        )
        invalid_escalations = (
            self.verification_measurement_payload(
                escalation="proportional",
                steps=[],
            ),
            self.verification_measurement_payload(
                escalation="proportional",
                steps=[
                    {
                        "ordinal": 1,
                        "kind": "all",
                        "duration_ms": 1,
                        "duration_capped": False,
                        "result": "success",
                    }
                ],
            ),
            self.verification_measurement_payload(
                escalation="all_first",
                steps=[
                    {
                        "ordinal": ordinal,
                        "kind": "all",
                        "duration_ms": ordinal,
                        "duration_capped": False,
                        "result": "success",
                    }
                    for ordinal in (1, 2)
                ],
            ),
        )
        for payload in invalid_escalations:
            with self.subTest(escalation=payload["data"]["verification_escalation"]):
                self.assert_m20_error(
                    lambda payload=payload: build_observation(
                        self.protocol,
                        unit=measurement_row[0],
                        scenario_id=measurement_row[1],
                        trial_id=measurement_row[2],
                        evidence_class=measurement_row[3],
                        channel=measurement_row[4],
                        record_key=measurement_row[5],
                        payload=payload,
                    ),
                    "source_drift",
                )

        invalid_machine_reasons = (
            (
                self.verification_measurement_payload(
                    escalation="unknown",
                    steps=[],
                ),
                [
                    {
                        "field": "data.verification_escalation",
                        "reasons": ["timeout"],
                    }
                ],
            ),
            (
                {
                    **self.verification_measurement_payload(),
                    "data": {
                        **self.verification_measurement_payload()["data"],
                        "target_change_result": "not_run",
                    },
                },
                [
                    {
                        "field": "data.target_change_result",
                        "reasons": ["timeout"],
                    }
                ],
            ),
        )
        for payload, unknowns in invalid_machine_reasons:
            with self.subTest(machine_path=unknowns[0]["field"]):
                self.assert_m20_error(
                    lambda payload=payload, unknowns=unknowns: build_observation(
                        self.protocol,
                        unit=measurement_row[0],
                        scenario_id=measurement_row[1],
                        trial_id=measurement_row[2],
                        evidence_class=measurement_row[3],
                        channel=measurement_row[4],
                        record_key=measurement_row[5],
                        payload=payload,
                        unknowns=unknowns,
                    ),
                    "parse_failed",
                )

        attestation_row = next(
            row
            for row in derive_inventory(self.protocol, "M20.3")
            if row[1] == "vp_cli_contract" and row[4] == "fresh_agent_trial"
        )
        semantic = self.verification_attestation_payload()
        semantic["assessment"]["instruction_fit"] = "unknown"
        common_count = self.verification_attestation_payload()
        common_count["reference_opens"] = None
        for payload, unknowns in (
            (
                semantic,
                [
                    {
                        "field": "assessment.instruction_fit",
                        "reasons": ["not_observable"],
                    }
                ],
            ),
            (
                common_count,
                [
                    {
                        "field": "reference_opens",
                        "reasons": ["observer_uncertain"],
                    }
                ],
            ),
        ):
            with self.subTest(path=unknowns[0]["field"]):
                self.assert_m20_error(
                    lambda payload=payload, unknowns=unknowns: build_observation(
                        self.protocol,
                        unit=attestation_row[0],
                        scenario_id=attestation_row[1],
                        trial_id=attestation_row[2],
                        evidence_class=attestation_row[3],
                        channel=attestation_row[4],
                        record_key=attestation_row[5],
                        payload=payload,
                        unknowns=unknowns,
                    ),
                    "parse_failed",
                )

    def test_cli_result_timeout_and_shape_boundaries_are_validated(self):
        cli_row = next(row for row in self.inventory if row[4] == "cli_invocation")
        timeout_payload = self.payload_for_row(cli_row)
        timeout = timeout_payload["operations"][0]
        timeout.update(
            duration_ms=300_000,
            duration_capped=True,
            result="timeout",
            exit_code=None,
            warning_codes=[],
            error_codes=[],
            stdout_bytes=0,
            stderr_bytes=0,
        )
        timeout_observation = self.observation_for_row(
            cli_row,
            payload=timeout_payload,
        )
        self.assertEqual(timeout_observation["eligibility"], "eligible")
        self.assertEqual(timeout_observation["unknowns"], [])

        invalid_cases = []
        wrong_exit = self.payload_for_row(cli_row)
        wrong_exit["operations"][0]["exit_code"] = 2
        invalid_cases.append(wrong_exit)
        wrong_errors = self.payload_for_row(cli_row)
        wrong_errors["operations"][0]["error_codes"] = ["unexpected"]
        invalid_cases.append(wrong_errors)
        too_many = self.payload_for_row(cli_row)
        base = too_many["operations"][0]
        too_many["operations"] = [
            {**base, "ordinal": ordinal, "duration_ms": ordinal}
            for ordinal in range(1, 34)
        ]
        invalid_cases.append(too_many)
        for payload in invalid_cases:
            with self.subTest(operation_count=len(payload["operations"])):
                self.assert_m20_error(
                    lambda payload=payload: self.observation_for_row(
                        cli_row,
                        payload=payload,
                    )
                )

    def test_task_show_state_projection_is_public_and_current_only(self):
        done = reduce_task_show_state(self.task_show_envelope())
        self.assertEqual(
            done,
            {
                "task_status": "done",
                "contract_revision": 4,
                "review_generation": 2,
                "receipts_current": 2,
                "qualifying_passes": 2,
                "changes_requested_current": 0,
                "findings_open_high": 0,
                "findings_open_medium": 0,
                "findings_open_low": 1,
                "handoffs_pending": 1,
                "handoffs_delivered": 2,
                "handoffs_withdrawn": 3,
                "completion_cycles": 1,
                "verification_attestation": True,
                "verification_detail": "absent",
            },
        )
        reopened = reduce_task_show_state(
            self.task_show_envelope(status="in_progress")
        )
        self.assertIsNone(reopened["verification_attestation"])

        invalid = self.task_show_envelope()
        del invalid["data"]["review_evidence"]["counts"]["open_medium"]
        self.assert_m20_error(lambda: reduce_task_show_state(invalid))
        failed = self.task_show_envelope()
        failed["ok"] = False
        self.assert_m20_error(lambda: reduce_task_show_state(failed))

    def test_corpus_is_exact_sorted_bounded_and_replayable(self):
        observations = self.complete_corpus()
        encoded = serialize_corpus(
            self.protocol,
            M20_2,
            list(reversed(observations)),
        )
        self.assertLessEqual(
            len(encoded),
            self.protocol["bounds"]["unit_corpus_bytes"],
        )
        self.assertFalse(encoded.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(encoded.endswith(b"\n"))
        decoded = json.loads(encoded)
        self.assertEqual(len(decoded), 46)
        self.assertEqual(
            [item["observation_id"] for item in decoded],
            sorted(item["observation_id"] for item in decoded),
        )
        self.assertEqual(
            serialize_corpus(self.protocol, M20_2, decoded),
            encoded,
        )

    def test_corpus_rejects_missing_duplicate_and_record_overflow(self):
        observations = self.complete_corpus()
        invalid_cases = (
            observations[:-1],
            observations + [observations[-1]],
            [observations[0], *observations[1:-1], observations[0]],
        )
        for candidate in invalid_cases:
            with self.subTest(count=len(candidate)):
                self.assert_m20_error(
                    lambda candidate=candidate: serialize_corpus(
                        self.protocol,
                        M20_2,
                        candidate,
                    )
                )

        maximum_record = max(
            len(canonical_json_bytes(observation))
            for observation in observations
        )
        self.assert_m20_error(
            lambda: serialize_corpus(
                self.protocol,
                M20_2,
                observations,
                record_limit=maximum_record - 1,
            )
        )
        self.assertIsInstance(
            serialize_corpus(
                self.protocol,
                M20_2,
                observations,
                record_limit=maximum_record,
            ),
            bytes,
        )

    def test_corpus_cap_failure_replaces_every_observation(self):
        observations = self.complete_corpus()
        ordinary = serialize_corpus(self.protocol, M20_2, observations)
        exact = serialize_corpus(
            self.protocol,
            M20_2,
            observations,
            corpus_limit=len(ordinary),
        )
        self.assertEqual(exact, ordinary)
        failure = json.loads(
            serialize_corpus(
                self.protocol,
                M20_2,
                observations,
                corpus_limit=len(ordinary) - 1,
            )
        )
        self.assertEqual(
            set(failure),
            {"schema", "unit", "reason", "record_count", "candidate_bytes"},
        )
        self.assertEqual(failure["schema"], "m20-operational-corpus-failure-v1")
        self.assertEqual(failure["unit"], M20_2)
        self.assertEqual(failure["reason"], "cap_exceeded")
        self.assertEqual(failure["record_count"], 46)
        self.assertEqual(failure["candidate_bytes"], len(ordinary))

    def test_m20_4_cross_record_pairing_and_handoff_control_fail_closed(self):
        records = self.complete_m20_4_corpus()
        self.assertIsInstance(
            serialize_corpus(self.protocol, "M20.4", records),
            bytes,
        )

        def select(candidate, scenario_id, trial_id, channel):
            return next(
                record
                for record in candidate
                if record["scenario_id"] == scenario_id
                and record["trial_id"] == trial_id
                and record["channel"] == channel
            )

        workload_mismatch = copy.deepcopy(records)
        select(
            workload_mismatch,
            "sp_multi_outcome_intake",
            "sp_multi_outcome_intake.bounded.01",
            "fresh_agent_trial",
        )["payload"]["workload_digest"] = "c" * 64

        episode_mismatch = copy.deepcopy(records)
        select(
            episode_mismatch,
            "sp_in_scope_discovery",
            "sp_in_scope_discovery.broad.01",
            "fresh_agent_trial",
        )["payload"]["assessment"]["episodes"][0]["episode_id"] = "episode_02"

        fabricated_plan_ids = copy.deepcopy(records)
        for candidate_record in fabricated_plan_ids:
            if (
                candidate_record["scenario_id"] == "sp_in_scope_discovery"
                and candidate_record["channel"]
                in {"trial_measurement", "fresh_agent_trial"}
            ):
                if candidate_record["channel"] == "trial_measurement":
                    episodes = candidate_record["payload"]["data"]["episodes"]
                else:
                    episodes = candidate_record["payload"]["assessment"][
                        "episodes"
                    ]
                for index, episode in enumerate(episodes, start=1):
                    episode["episode_id"] = f"fabricated_{index:02d}"

        duplicate_measurement_episode = copy.deepcopy(records)
        measurement_episodes = select(
            duplicate_measurement_episode,
            "sp_in_scope_discovery",
            "sp_in_scope_discovery.broad.01",
            "trial_measurement",
        )["payload"]["data"]["episodes"]
        measurement_episodes.append(copy.deepcopy(measurement_episodes[0]))

        duplicate_attestation_episode = copy.deepcopy(records)
        attestation_episodes = select(
            duplicate_attestation_episode,
            "sp_in_scope_discovery",
            "sp_in_scope_discovery.broad.01",
            "fresh_agent_trial",
        )["payload"]["assessment"]["episodes"]
        attestation_episodes.append(copy.deepcopy(attestation_episodes[0]))

        invalid_handoff = copy.deepcopy(records)
        handoff_episode = select(
            invalid_handoff,
            "sp_handoff_control",
            "sp_handoff_control.broad.01",
            "fresh_agent_trial",
        )["payload"]["assessment"]["episodes"][0]
        handoff_episode.update(
            cause="multiple_outcomes",
            current_response="keep_current",
        )

        handoff_state_mismatch = copy.deepcopy(records)
        select(
            handoff_state_mismatch,
            "sp_handoff_control",
            "sp_handoff_control.broad.01",
            "state_projection",
        )["payload"]["after"]["review_generation"] = 1

        for label, candidate in (
            ("paired_workload", workload_mismatch),
            ("paired_episode", episode_mismatch),
            ("fabricated_plan_ids", fabricated_plan_ids),
            ("duplicate_measurement_episode", duplicate_measurement_episode),
            ("duplicate_attestation_episode", duplicate_attestation_episode),
            ("handoff_control", invalid_handoff),
            ("handoff_state", handoff_state_mismatch),
        ):
            with self.subTest(case=label):
                self.assert_m20_error(
                    lambda candidate=candidate: serialize_corpus(
                        self.protocol,
                        "M20.4",
                        candidate,
                    ),
                    "source_drift",
                )

    def test_attempt_journal_persists_no_rerun_state(self):
        scenario_id = "gov_tier1_commitless"
        attempt_id = f"{scenario_id}.harness.01"
        rows = [row for row in self.inventory if row[1] == scenario_id]
        records = [self.observation_for_row(row) for row in rows]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attempt-journal.json"
            journal = AttemptJournal(path, self.protocol)
            journal.start(attempt_id)
            self.assert_m20_error(
                lambda: journal.start(attempt_id),
                "attempt_already_started",
            )
            crashed = AttemptJournal(path, self.protocol)
            self.assert_m20_error(
                lambda: crashed.start(attempt_id),
                "attempt_already_started",
            )
            journal.finish(attempt_id, records)
            self.assertEqual(
                {
                    record["observation_id"]
                    for record in journal.reduced_records()
                },
                {record["observation_id"] for record in records},
            )
            reopened = AttemptJournal(path, self.protocol)
            self.assert_m20_error(
                lambda: reopened.start(attempt_id),
                "attempt_already_started",
            )
            self.assertEqual(
                {
                    record["observation_id"]
                    for record in reopened.reduced_records()
                },
                {record["observation_id"] for record in records},
            )
            reopened.remove()
            self.assertFalse(path.exists())

    def test_attempt_journal_checks_privacy_before_persisting_records(self):
        scenario_id = "gov_tier1_commitless"
        attempt_id = f"{scenario_id}.harness.01"
        row = next(
            row
            for row in self.inventory
            if row[1] == scenario_id and row[4] == "cli_invocation"
        )
        payload = self.payload_for_row(row)
        payload["operations"][0]["warning_codes"] = ["raw_prompt"]
        contaminated = self.observation_for_row(row, payload=payload)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attempt-journal.json"
            journal = AttemptJournal(path, self.protocol)
            journal.start(attempt_id)
            self.assert_m20_error(
                lambda: journal.finish(attempt_id, [contaminated]),
                "contaminated",
            )
            retained = path.read_bytes()
            self.assertNotIn(b"raw_prompt", retained)
            self.assertEqual(journal.status(attempt_id), "started")
            sanitized = m20_observation._sanitize_records_for_retention(
                self.protocol,
                [row],
                [contaminated],
            )
            self.assertIsNone(sanitized[0]["payload"])
            self.assertEqual(
                sanitized[0]["unknowns"],
                [{"field": "payload", "reasons": ["contaminated"]}],
            )
            journal.finish(attempt_id, sanitized)
            reduced = journal.reduced_records()
            self.assertEqual(len(reduced), 1)
            self.assertEqual(
                reduced[0]["unknowns"],
                [{"field": "payload", "reasons": ["contaminated"]}],
            )
            self.assertNotIn(b"raw_prompt", path.read_bytes())

    def test_retention_requires_exact_attempt_inventory(self):
        scenario_id = "gov_tier1_commitless"
        rows = [row for row in self.inventory if row[1] == scenario_id]
        records = [self.observation_for_row(row) for row in rows]
        foreign_row = next(
            row
            for row in self.inventory
            if row[1] != scenario_id
        )
        foreign = self.observation_for_row(foreign_row)
        candidates = {
            "missing": records[:-1],
            "duplicate": records[:-1] + [records[0]],
            "extra_duplicate": records + [records[0]],
            "foreign_attempt": records[:-1] + [foreign],
        }
        expected_ids = {observation_id(self.protocol, row) for row in rows}
        for label, candidate in candidates.items():
            with self.subTest(case=label):
                retained = m20_observation._sanitize_records_for_retention(
                    self.protocol,
                    rows,
                    candidate,
                )
                self.assertEqual(len(retained), len(rows))
                self.assertEqual(
                    {record["observation_id"] for record in retained},
                    expected_ids,
                )
                self.assertEqual(
                    {record["eligibility"] for record in retained},
                    {"excluded"},
                )
                self.assertEqual(
                    {
                        tuple(record["unknown_reasons"])
                        for record in retained
                    },
                    {("source_drift",)},
                )

    def test_bounded_stdout_can_reduce_only_complete_json_before_overflow(self):
        envelope = {
            "ok": True,
            "command": "task.show",
            "project_id": "project_fixture",
            "data": {},
            "warnings": [],
            "errors": [],
        }
        encoded = canonical_json_bytes(envelope)
        whitespace = m20_observation._BoundedPipe(
            io.BytesIO(encoded + b" " * 32),
            len(encoded) + 1,
        )
        whitespace.read()
        self.assertGreater(whitespace.total, whitespace.cap)
        self.assertFalse(whitespace.overflow_non_whitespace)
        self.assertEqual(
            m20_observation._parse_bounded_json_object(
                whitespace,
                timed_out=False,
            ),
            envelope,
        )

        non_whitespace = m20_observation._BoundedPipe(
            io.BytesIO(encoded + b"x" * 32),
            len(encoded) + 1,
        )
        non_whitespace.read()
        self.assertTrue(non_whitespace.overflow_non_whitespace)
        self.assert_m20_error(
            lambda: m20_observation._parse_bounded_json_object(
                non_whitespace,
                timed_out=False,
            ),
            "parse_failed",
        )

        capture = m20_observation.InvocationCapture(
            phase="diagnose",
            command_leaf="task.show",
            duration_ms=1,
            timed_out=False,
            exit_code=0,
            stdout_bytes=m20_observation.MAX_STREAM_BYTES + 1,
            stderr_bytes=0,
            envelope=envelope,
        )
        payload, unknowns = m20_observation._reduce_cli_captures([capture])
        self.assertEqual(
            payload["operations"][0]["stdout_bytes"],
            m20_observation.MAX_STREAM_BYTES,
        )
        self.assertEqual(
            unknowns,
            [
                {
                    "field": "operations.0.stdout_bytes",
                    "reasons": ["cap_exceeded"],
                }
            ],
        )

    def test_oversized_json_integer_finalizes_harness_attempt_without_rerun(self):
        scenario = self.scenarios["gov_tier1_commitless"]
        raw = b'{"value":' + (b"9" * 5001) + b"}"

        def oversized_integer(*_args):
            capture = m20_observation._BoundedPipe(
                io.BytesIO(raw),
                m20_observation.MAX_STREAM_BYTES,
            )
            capture.read()
            m20_observation._parse_bounded_json_object(
                capture,
                timed_out=False,
            )
            raise AssertionError("oversized integer unexpectedly parsed")

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            journal = AttemptJournal(
                temp_root / "attempt-journal.json",
                self.protocol,
            )
            with (
                patch.object(m20_observation, "_extract_baseline"),
                patch.object(
                    m20_observation,
                    "_run_harness_scenario",
                    side_effect=oversized_integer,
                ) as run_scenario,
            ):
                m20_observation._collect_harness_scenario_once(
                    self.protocol,
                    temp_root / "unused-baseline.tar",
                    temp_root,
                    scenario,
                    self.inventory,
                    journal,
                )
                m20_observation._collect_harness_scenario_once(
                    self.protocol,
                    temp_root / "unused-baseline.tar",
                    temp_root,
                    scenario,
                    self.inventory,
                    journal,
                )
            run_scenario.assert_called_once()
            records = journal.reduced_records()
            expected = [
                row
                for row in self.inventory
                if row[1] == scenario["scenario_id"]
            ]
            self.assertEqual(len(records), len(expected))
            self.assertEqual(
                {record["eligibility"] for record in records},
                {"excluded"},
            )
            self.assertEqual(
                {tuple(record["unknown_reasons"]) for record in records},
                {("parse_failed",)},
            )

    def test_harness_os_error_finalizes_attempt_without_rerun(self):
        scenario = self.scenarios["gov_tier1_commitless"]
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            journal = AttemptJournal(
                temp_root / "attempt-journal.json",
                self.protocol,
            )
            with (
                patch.object(m20_observation, "_extract_baseline"),
                patch.object(
                    m20_observation,
                    "_run_harness_scenario",
                    side_effect=OSError("synthetic private filesystem detail"),
                ) as run_scenario,
            ):
                m20_observation._collect_harness_scenario_once(
                    self.protocol,
                    temp_root / "unused-baseline.tar",
                    temp_root,
                    scenario,
                    self.inventory,
                    journal,
                )
                m20_observation._collect_harness_scenario_once(
                    self.protocol,
                    temp_root / "unused-baseline.tar",
                    temp_root,
                    scenario,
                    self.inventory,
                    journal,
                )
            run_scenario.assert_called_once()
            records = journal.reduced_records()
            expected = [
                row
                for row in self.inventory
                if row[1] == scenario["scenario_id"]
            ]
            self.assertEqual(len(records), len(expected))
            self.assertEqual(
                {record["eligibility"] for record in records},
                {"excluded"},
            )
            self.assertEqual(
                {tuple(record["unknown_reasons"]) for record in records},
                {("source_missing",)},
            )

    def test_collection_lock_rejects_overlap_and_allows_reacquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m20.2-collector.lock"

            def acquire_once():
                with CollectionLock(path):
                    pass

            with CollectionLock(path):
                self.assert_m20_error(acquire_once, "collection_busy")
            acquire_once()

    def test_terminal_receipt_stops_collection_before_product_runner_or_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.write_terminal_fixture(root, retain_corpus=False)
            imports = []
            real_import = builtins.__import__

            def guarded_import(name, *args, **kwargs):
                imports.append(name)
                if name == "tools.m20_history" or name.startswith(
                    "tools.m20_history."
                ):
                    raise AssertionError("history import attempted")
                return real_import(name, *args, **kwargs)

            with (
                patch.object(m20_observation, "DEFAULT_REPO_ROOT", root),
                patch.object(
                    m20_observation,
                    "M20_2_RECEIPT_SHA256",
                    hashlib.sha256(
                        (
                            root
                            / self.protocol["m20_2"]["receipt_path"]
                        ).read_bytes()
                    ).hexdigest(),
                ),
                patch.object(m20_observation, "_product_boundary_check") as product,
                patch.object(m20_observation, "_collect_m20_2_locked") as runner,
                patch.object(builtins, "__import__", side_effect=guarded_import),
            ):
                self.assert_m20_error(
                    lambda: m20_observation.collect_m20_2(root),
                    "collection_closed",
                )
            product.assert_not_called()
            runner.assert_not_called()
            self.assertFalse(
                any(name.startswith("tools.m20_history") for name in imports)
            )

    def test_history_import_failure_becomes_sanitized_source_missing(self):
        real_import = builtins.__import__

        def failed_history_import(name, *args, **kwargs):
            if name == "tools.m20_history":
                raise ModuleNotFoundError("synthetic private import detail")
            return real_import(name, *args, **kwargs)

        with patch.object(
            builtins,
            "__import__",
            side_effect=failed_history_import,
        ):
            self.assert_m20_error(
                lambda: m20_observation._reconstruct_m19_once(
                    ROOT,
                    self.protocol,
                ),
                "source_missing",
            )

    def test_retrospective_import_failure_finalizes_all_attempts_without_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = AttemptJournal(
                Path(tmp) / "attempt-journal.json",
                self.protocol,
            )
            failure = M20ObservationError("source_missing")
            with patch.object(
                m20_observation,
                "_reconstruct_m19_once",
                side_effect=failure,
            ) as reconstruct:
                m20_observation._collect_retrospective_once(
                    ROOT,
                    self.protocol,
                    journal,
                )
                m20_observation._collect_retrospective_once(
                    ROOT,
                    self.protocol,
                    journal,
                )
            reconstruct.assert_called_once_with(ROOT, self.protocol)
            records = journal.reduced_records()
            self.assertEqual(len(records), 36)
            self.assertEqual(
                {record["eligibility"] for record in records},
                {"excluded"},
            )
            self.assertEqual(
                {tuple(record["unknown_reasons"]) for record in records},
                {("source_missing",)},
            )

    def test_retrospective_reduction_failure_finalizes_without_source_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = AttemptJournal(
                Path(tmp) / "attempt-journal.json",
                self.protocol,
            )
            with (
                patch.object(
                    m20_observation,
                    "_reconstruct_m19_once",
                    return_value={},
                ) as reconstruct,
                patch.object(
                    m20_observation,
                    "_history_observations",
                    side_effect=M20ObservationError("parse_failed"),
                ) as reduce_history,
            ):
                m20_observation._collect_retrospective_once(
                    ROOT,
                    self.protocol,
                    journal,
                )
                m20_observation._collect_retrospective_once(
                    ROOT,
                    self.protocol,
                    journal,
                )
            reconstruct.assert_called_once_with(ROOT, self.protocol)
            reduce_history.assert_called_once_with(self.protocol, {})
            records = journal.reduced_records()
            self.assertEqual(len(records), 36)
            self.assertEqual(
                {record["eligibility"] for record in records},
                {"excluded"},
            )
            self.assertEqual(
                {tuple(record["unknown_reasons"]) for record in records},
                {("parse_failed",)},
            )

    def test_tracked_terminal_receipt_is_exactly_pinned(self):
        receipt_path = ROOT / self.protocol["m20_2"]["receipt_path"]
        raw = receipt_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            m20_observation.M20_2_RECEIPT_SHA256,
        )
        receipt = m20_observation._read_collection_receipt(
            ROOT,
            self.protocol,
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["artifact_status"], "retained")

    def test_check_validates_retained_corpus_without_history_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            raw = self.write_terminal_fixture(root, retain_corpus=True)
            imports = []
            real_import = builtins.__import__

            def guarded_import(name, *args, **kwargs):
                imports.append(name)
                if name == "tools.m20_history" or name.startswith(
                    "tools.m20_history."
                ):
                    raise AssertionError("history import attempted")
                return real_import(name, *args, **kwargs)

            def fixed_output(repo_root, relative, *, require_ignored):
                return Path(repo_root) / Path(relative)

            with (
                patch.object(m20_observation, "DEFAULT_REPO_ROOT", root),
                patch.object(
                    m20_observation,
                    "M20_2_RECEIPT_SHA256",
                    hashlib.sha256(
                        (
                            root
                            / self.protocol["m20_2"]["receipt_path"]
                        ).read_bytes()
                    ).hexdigest(),
                ),
                patch.object(m20_observation, "_product_boundary_check") as product,
                patch.object(m20_observation, "_fixed_output", fixed_output),
                patch.object(builtins, "__import__", side_effect=guarded_import),
            ):
                result = m20_observation.check_m20_2(root)
            product.assert_called_once_with(self.protocol)
            self.assertEqual(
                result,
                {
                    "artifact_status": "retained",
                    "record_count": 46,
                    "corpus_bytes": len(raw),
                    "corpus_sha256": hashlib.sha256(raw).hexdigest(),
                },
            )
            self.assertFalse(
                any(name.startswith("tools.m20_history") for name in imports)
            )

    def test_terminal_receipt_rejects_impossible_or_noncanonical_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.write_terminal_fixture(root, retain_corpus=False)
            receipt_path = (
                root / self.protocol["m20_2"]["receipt_path"]
            )
            original = json.loads(receipt_path.read_text(encoding="utf-8"))

            impossible = copy.deepcopy(original)
            impossible.update(
                record_count=0,
                eligible_records=0,
                partial_records=0,
                excluded_records=0,
            )
            receipt_path.write_bytes(canonical_json_bytes(impossible) + b"\n")
            with patch.object(
                m20_observation,
                "M20_2_RECEIPT_SHA256",
                hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            ):
                self.assert_m20_error(
                    lambda: m20_observation._read_collection_receipt(
                        root,
                        self.protocol,
                    ),
                    "source_drift",
                )

            receipt_path.write_bytes(canonical_json_bytes(original))
            with patch.object(
                m20_observation,
                "M20_2_RECEIPT_SHA256",
                hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            ):
                self.assert_m20_error(
                    lambda: m20_observation._read_collection_receipt(
                        root,
                        self.protocol,
                    ),
                    "source_drift",
                )

    def test_retained_receipt_does_not_hide_missing_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.write_terminal_fixture(root, retain_corpus=False)
            receipt_path = root / self.protocol["m20_2"]["receipt_path"]

            def fixed_output(repo_root, relative, *, require_ignored):
                return Path(repo_root) / Path(relative)

            with (
                patch.object(m20_observation, "DEFAULT_REPO_ROOT", root),
                patch.object(
                    m20_observation,
                    "M20_2_RECEIPT_SHA256",
                    hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                ),
                patch.object(m20_observation, "_product_boundary_check"),
                patch.object(m20_observation, "_fixed_output", fixed_output),
            ):
                self.assert_m20_error(
                    lambda: m20_observation.check_m20_2(root),
                    "source_missing",
                )

    def test_retired_receipt_rejects_still_present_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.write_terminal_fixture(root, retain_corpus=True)
            receipt_path = root / self.protocol["m20_2"]["receipt_path"]
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt.update(
                artifact_status="retired",
                retirement_revision="c" * 40,
            )
            receipt_path.write_bytes(canonical_json_bytes(receipt) + b"\n")

            def fixed_output(repo_root, relative, *, require_ignored):
                return Path(repo_root) / Path(relative)

            with (
                patch.object(m20_observation, "DEFAULT_REPO_ROOT", root),
                patch.object(
                    m20_observation,
                    "M20_2_RECEIPT_SHA256",
                    hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                ),
                patch.object(m20_observation, "_product_boundary_check"),
                patch.object(m20_observation, "_fixed_output", fixed_output),
            ):
                self.assert_m20_error(
                    lambda: m20_observation.check_m20_2(root),
                    "source_drift",
                )

    def test_fixed_output_rejects_lexical_symlink_before_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            link = root / "link"
            path_type = type(link)
            ordinary_is_symlink = path_type.is_symlink
            ordinary_lstat = path_type.lstat
            ordinary_lexists = m20_observation.os.path.lexists

            def marked_symlink(path):
                return path == link or ordinary_is_symlink(path)

            def marked_lexists(path):
                return Path(path) == link or ordinary_lexists(path)

            def marked_lstat(path):
                return ordinary_lstat(root) if path == link else ordinary_lstat(path)

            with (
                patch.object(path_type, "is_symlink", marked_symlink),
                patch.object(path_type, "lstat", marked_lstat),
                patch.object(
                    m20_observation.os.path,
                    "lexists",
                    side_effect=marked_lexists,
                ),
            ):
                self.assert_m20_error(
                    lambda: m20_observation._fixed_output(
                        root,
                        "link",
                        require_ignored=False,
                    ),
                    "source_drift",
                )

    def test_fixed_output_normalizes_git_launch_and_timeout_failures(self):
        failures = (
            OSError("synthetic private launch detail"),
            m20_observation.subprocess.TimeoutExpired(
                cmd=["git", "check-ignore"],
                timeout=15,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for failure in failures:
                with (
                    self.subTest(failure=type(failure).__name__),
                    patch.object(
                        m20_observation.subprocess,
                        "run",
                        side_effect=failure,
                    ),
                ):
                    self.assert_m20_error(
                        lambda: m20_observation._fixed_output(
                            root,
                            "dist/m20/result.json",
                            require_ignored=True,
                        ),
                        "source_missing",
                    )

    def test_subprocess_calls_are_shell_free_and_network_fetch_is_disabled(self):
        source = (ROOT / "tools" / "m20_observation.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        subprocess_calls = []
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.partition(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in {"Popen", "run"}
            ):
                subprocess_calls.append(node)

        self.assertGreaterEqual(len(subprocess_calls), 4)
        for call in subprocess_calls:
            shell = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "shell"),
                None,
            )
            with self.subTest(line=call.lineno):
                self.assertIsInstance(shell, ast.Constant)
                self.assertIs(shell.value, False)
        self.assertTrue(
            imported_roots.isdisjoint(
                {"aiohttp", "ftplib", "http", "requests", "socket", "urllib"}
            )
        )
        self.assertEqual(
            m20_observation._safe_git_environment()["GIT_NO_LAZY_FETCH"],
            "1",
        )
        self.assertEqual(
            m20_observation._safe_git_environment()["GIT_CONFIG_GLOBAL"],
            os.devnull,
        )
        self.assertEqual(
            m20_observation._safe_git_environment()["GIT_ATTR_NOSYSTEM"],
            "1",
        )

    def test_control_bundle_digests_do_not_retain_private_material(self):
        bundle = self.control_bundle()
        identity = {
            "unit": "M20.3",
            "scenario_id": "vp_cli_contract",
            "arm": "baseline",
            "trial_id": "vp_cli_contract.baseline.01",
        }
        result = validate_control_bundle(
            self.protocol,
            bundle,
            **identity,
        )
        self.assertEqual(set(result), {"workload_digest", "control_digest"})
        for digest in result.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            result["workload_digest"],
            hashlib.sha256(bundle["workload"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            result["control_digest"],
            hashlib.sha256(
                json.dumps(
                    bundle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        )
        reduced = canonical_json_bytes(result)
        for forbidden in (
            b"PRIVATE_WORKLOAD",
            b"PRIVATE_REQUEST",
            b"PRIVATE_CLARIFICATION",
        ):
            self.assertNotIn(forbidden, reduced)

        reordered = dict(reversed(tuple(bundle.items())))
        self.assertEqual(
            validate_control_bundle(
                self.protocol,
                reordered,
                **identity,
            ),
            result,
        )
        changed = copy.deepcopy(bundle)
        changed["workload"] += "\n"
        self.assertNotEqual(
            validate_control_bundle(
                self.protocol,
                changed,
                **identity,
            )["workload_digest"],
            result["workload_digest"],
        )
        extra = copy.deepcopy(bundle)
        extra["raw_prompt"] = "forbidden"
        invalid_cases = [("extra_key", extra)]
        unsafe_selector = copy.deepcopy(bundle)
        unsafe_selector["reducer_manifest"]["target_change"]["selector"] = (
            "../outside.py"
        )
        invalid_cases.append(("unsafe_selector", unsafe_selector))
        for label, selector in (
            ("duplicate_separator", "tests//test_x.py"),
            ("dot_component", "tests/./test_x.py"),
            ("trailing_separator", "tests/test_x.py/"),
            ("current_directory", "."),
        ):
            noncanonical = copy.deepcopy(bundle)
            noncanonical["reducer_manifest"]["target_change"][
                "selector"
            ] = selector
            invalid_cases.append((label, noncanonical))
        carriage_return = copy.deepcopy(bundle)
        carriage_return["reducer_manifest"]["target_change"]["before_lf"] = (
            "before\r\n"
        )
        invalid_cases.append(("carriage_return", carriage_return))
        duplicate_slots = copy.deepcopy(bundle)
        duplicate_slots["reducer_manifest"]["owner_slots"] = ["core", "core"]
        invalid_cases.append(("duplicate_slots", duplicate_slots))
        unknown_label = copy.deepcopy(bundle)
        unknown_label["reducer_manifest"]["target_change"][
            "verification_label"
        ] = "missing"
        invalid_cases.append(("unknown_label", unknown_label))
        unchanged_target = copy.deepcopy(bundle)
        unchanged_target["reducer_manifest"]["target_change"]["after_lf"] = (
            "before\n"
        )
        invalid_cases.append(("unchanged_target", unchanged_target))
        for label, candidate in invalid_cases:
            with self.subTest(case=label):
                self.assert_m20_error(
                    lambda candidate=candidate: validate_control_bundle(
                        self.protocol,
                        candidate,
                        **identity,
                    )
                )

        cross_scenario = copy.deepcopy(bundle)
        cross_scenario["reducer_manifest"][
            "scenario_id"
        ] = "vp_state_transition"
        self.assert_m20_error(
            lambda: validate_control_bundle(
                self.protocol,
                cross_scenario,
                **identity,
            ),
            "source_drift",
        )
        self.assert_m20_error(
            lambda: validate_control_bundle(
                self.protocol,
                bundle,
                unit="M20.3",
                scenario_id="not_frozen",
                arm="invented",
                trial_id="not_frozen.invented.01",
            ),
            "source_drift",
        )

    def test_serialized_corpus_contains_no_forbidden_raw_bytes(self):
        encoded = serialize_corpus(
            self.protocol,
            M20_2,
            self.complete_corpus(),
        )
        for forbidden in (
            b"PRIVATE_WORKLOAD",
            b"PRIVATE_REQUEST",
            b"PRIVATE_CLARIFICATION",
            b"Authorization:",
            b"C:\\",
            b"/Users/",
            b"raw_prompt",
            b"stdout_content",
            b"stderr_content",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    unittest.main()
