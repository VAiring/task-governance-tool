from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.analysis_contracts import (  # noqa: E402
    AnalysisContractError,
    build_descriptor,
    canonical_json_bytes,
    default_recipe,
    descriptor_replay_matches,
    parse_canonical_json_document,
    pending_status,
    recipe_digest,
    source_key_for_identity,
    validate_status,
    validate_status_transition,
)


def legacy_basis() -> dict[str, object]:
    return {
        "project_id": "tg_project_" + ("0" * 32),
        "projection_generation": 7,
        "index_digest": "sha256:" + "1" * 64,
        "entry": {
            "task_id": "tg_task_1111111111111111",
            "completion_cycle_id": "tg_completion_cycle_2222222222222222",
            "cycle_ordinal": 1,
            "bundle_state": "legacy_unknown",
            "bundle_id": None,
            "bundle_file": None,
            "bundle_digest": None,
            "file_digest": None,
            "sealed_at": None,
        },
    }


class AnalysisContractsTests(unittest.TestCase):
    def test_canonical_json_and_duplicate_free_parser(self):
        value = {"z": "é", "a": [None, False, True, -2, "\x00"]}
        expected = b'{"a":[null,false,true,-2,"\\u0000"],"z":"\xc3\xa9"}'
        self.assertEqual(canonical_json_bytes(value), expected)
        self.assertEqual(
            parse_canonical_json_document(expected + b"\n", maximum=1024),
            value,
        )
        for invalid in (
            b'{"a":1,"a":2}\n',
            b'{"a":1.0}\n',
            b'{ "a":1}\n',
            br'{"a":"\ud800"}' + b"\n",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(AnalysisContractError):
                    parse_canonical_json_document(invalid, maximum=1024)

    def test_contract_vectors_and_descriptor_replay(self):
        identity = {
            "bundle_state": "legacy_unknown",
            "completion_cycle_id": "c",
            "cycle_ordinal": 1,
            "project_id": "p",
            "task_id": "t",
        }
        self.assertEqual(
            source_key_for_identity(identity),
            "sha256:43de9c707c10c49ab1b3bc939975b058bbf9b79dfbd495324ecd5e2135581fbf",
        )
        recipe = default_recipe()
        self.assertEqual(
            recipe_digest(recipe),
            "sha256:8ac0a31a34894d0d759b7844b8f0d8b6999520374f34a73b45a2a4cff7b29f3d",
        )
        descriptor = build_descriptor(
            source_kind="legacy_index_entry",
            source_basis=legacy_basis(),
            recipe=recipe,
        )
        self.assertRegex(
            descriptor["analysis_job_id"],
            r"^tg_analysis_job_[0-9a-f]{16}$",
        )
        drift = legacy_basis()
        drift["projection_generation"] = 8
        drift["index_digest"] = "sha256:" + "2" * 64
        replay = build_descriptor(
            source_kind="legacy_index_entry",
            source_basis=drift,
            recipe=recipe,
        )
        self.assertNotEqual(descriptor["descriptor_digest"], replay["descriptor_digest"])
        self.assertTrue(descriptor_replay_matches(descriptor, replay))

    def test_source_basis_structural_ids_are_closed(self):
        accepted_legacy = legacy_basis()
        accepted_legacy["project_id"] = "sample-project-abcdef123456"
        self.assertEqual(
            build_descriptor(
                source_kind="legacy_index_entry",
                source_basis=accepted_legacy,
                recipe=default_recipe(),
            )["source_basis"]["project_id"],
            accepted_legacy["project_id"],
        )
        for field, value in (
            ("project_id", "arbitrary-project"),
            ("task_id", "task-1"),
            ("completion_cycle_id", "cycle-1"),
        ):
            invalid = legacy_basis()
            target = invalid if field == "project_id" else invalid["entry"]
            target[field] = value
            with self.subTest(field=field), self.assertRaises(AnalysisContractError):
                build_descriptor(
                    source_kind="legacy_index_entry",
                    source_basis=invalid,
                    recipe=default_recipe(),
                )

    def test_pending_and_terminal_status_matrix(self):
        descriptor = build_descriptor(
            source_kind="legacy_index_entry",
            source_basis=legacy_basis(),
            recipe=default_recipe(),
        )
        pending = pending_status(descriptor)
        self.assertEqual(validate_status(pending, descriptor=descriptor), pending)

        source_invalid = deepcopy(pending)
        source_invalid.update(
            {
                "state": "failed",
                "worker_attempt_count": 1,
                "fixed_code": "source_invalid",
            }
        )
        self.assertEqual(
            validate_status_transition(pending, source_invalid, descriptor=descriptor),
            source_invalid,
        )
        for state, fixed_code in (
            ("cancelled", "cancelled"),
            ("failed", "interrupted"),
        ):
            forbidden_terminal = deepcopy(pending)
            forbidden_terminal.update(
                {
                    "state": state,
                    "worker_attempt_count": 1,
                    "fixed_code": fixed_code,
                }
            )
            with self.subTest(state=state, fixed_code=fixed_code):
                with self.assertRaises(AnalysisContractError):
                    validate_status_transition(
                        pending,
                        forbidden_terminal,
                        descriptor=descriptor,
                    )

        running = deepcopy(pending)
        running.update(
            {
                "state": "running",
                "worker_attempt_count": 1,
                "packet_digest": "sha256:" + "a" * 64,
            }
        )
        self.assertEqual(
            validate_status_transition(pending, running, descriptor=descriptor),
            running,
        )

        failed = deepcopy(running)
        failed.update({"state": "failed", "fixed_code": "interrupted"})
        self.assertEqual(validate_status(failed, descriptor=descriptor), failed)
        self.assertEqual(
            validate_status_transition(running, failed, descriptor=descriptor),
            failed,
        )
        self.assertEqual(
            validate_status_transition(failed, deepcopy(failed), descriptor=descriptor),
            failed,
        )
        rollback = deepcopy(failed)
        rollback.update({"state": "running", "fixed_code": None})
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(failed, rollback, descriptor=descriptor)
        failed["accepted_output_digest"] = "sha256:" + "a" * 64
        with self.assertRaises(AnalysisContractError):
            validate_status(failed, descriptor=descriptor)

    def test_failed_fixed_code_matrix_is_literal(self):
        descriptor = build_descriptor(
            source_kind="legacy_index_entry",
            source_basis=legacy_basis(),
            recipe=default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixed-mock",
            ),
        )
        base = pending_status(descriptor)
        base.update({"state": "failed", "worker_attempt_count": 1})
        digest = "sha256:" + "a" * 64

        source_invalid = deepcopy(base)
        source_invalid["fixed_code"] = "source_invalid"
        self.assertEqual(
            validate_status(source_invalid, descriptor=descriptor),
            source_invalid,
        )
        for field in ("packet_digest", "accepted_output_digest"):
            invalid = deepcopy(source_invalid)
            invalid[field] = digest
            with self.subTest(source_invalid_field=field):
                with self.assertRaises(AnalysisContractError):
                    validate_status(invalid, descriptor=descriptor)

        report_invalid = deepcopy(base)
        report_invalid.update(
            {
                "fixed_code": "report_invalid",
                "inference_state": "invalid_output",
                "adapter_attempt_count": 1,
                "packet_digest": digest,
            }
        )
        self.assertEqual(
            validate_status(report_invalid, descriptor=descriptor),
            report_invalid,
        )
        missing_packet = deepcopy(report_invalid)
        missing_packet["packet_digest"] = None
        with self.assertRaises(AnalysisContractError):
            validate_status(missing_packet, descriptor=descriptor)

        interrupted = deepcopy(base)
        interrupted.update(
            {
                "fixed_code": "interrupted",
                "packet_digest": digest,
                "inference_state": "failed",
                "adapter_attempt_count": 1,
            }
        )
        self.assertEqual(validate_status(interrupted, descriptor=descriptor), interrupted)
        still_running = deepcopy(interrupted)
        still_running["inference_state"] = "running"
        with self.assertRaises(AnalysisContractError):
            validate_status(still_running, descriptor=descriptor)

        succeeded = deepcopy(interrupted)
        succeeded.update(
            {
                "inference_state": "succeeded",
                "accepted_output_digest": "sha256:" + "b" * 64,
            }
        )
        self.assertEqual(validate_status(succeeded, descriptor=descriptor), succeeded)

    def test_optional_pending_prepacket_failure_preserves_pending_inference_matrix(self):
        descriptor = build_descriptor(
            source_kind="legacy_index_entry",
            source_basis=legacy_basis(),
            recipe=default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixed-mock",
            ),
        )
        pending = pending_status(descriptor)
        failed = deepcopy(pending)
        failed.update(
            {
                "state": "failed",
                "worker_attempt_count": 1,
                "fixed_code": "packet_too_large",
            }
        )
        self.assertEqual(
            validate_status_transition(pending, failed, descriptor=descriptor),
            failed,
        )
        wrong_inference = deepcopy(failed)
        wrong_inference["inference_state"] = "policy_blocked"
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                pending,
                wrong_inference,
                descriptor=descriptor,
            )

    def test_optional_attempt_phase_dfa_is_literal(self):
        descriptor = build_descriptor(
            source_kind="legacy_index_entry",
            source_basis=legacy_basis(),
            recipe=default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixed-mock",
            ),
        )
        pending = pending_status(descriptor)
        ready = deepcopy(pending)
        ready.update(
            {
                "state": "running",
                "worker_attempt_count": 1,
                "packet_digest": "sha256:" + "a" * 64,
            }
        )
        self.assertEqual(
            validate_status_transition(pending, ready, descriptor=descriptor),
            ready,
        )

        pre_call = deepcopy(ready)
        pre_call.update({"adapter_attempt_count": 1, "inference_state": "running"})
        self.assertEqual(
            validate_status_transition(ready, pre_call, descriptor=descriptor),
            pre_call,
        )
        for phase in (ready, pre_call):
            false_intent = deepcopy(phase)
            false_intent.update(
                {
                    "report_id": "tg_analysis_report_0123456789abcdef",
                    "report_digest": "sha256:" + "c" * 64,
                    "render_digest": "sha256:" + "d" * 64,
                }
            )
            with self.assertRaises(AnalysisContractError):
                validate_status(false_intent, descriptor=descriptor)
        counter_and_outcome = deepcopy(ready)
        counter_and_outcome.update(
            {"adapter_attempt_count": 1, "inference_state": "timeout"}
        )
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                ready,
                counter_and_outcome,
                descriptor=descriptor,
            )

        timeout = deepcopy(pre_call)
        timeout["inference_state"] = "timeout"
        self.assertEqual(
            validate_status_transition(pre_call, timeout, descriptor=descriptor),
            timeout,
        )
        switched_outcome = deepcopy(timeout)
        switched_outcome["inference_state"] = "failed"
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                timeout,
                switched_outcome,
                descriptor=descriptor,
            )

        retry = deepcopy(timeout)
        retry.update({"adapter_attempt_count": 2, "inference_state": "running"})
        self.assertEqual(
            validate_status_transition(timeout, retry, descriptor=descriptor),
            retry,
        )
        retry_with_old_outcome = deepcopy(timeout)
        retry_with_old_outcome["adapter_attempt_count"] = 2
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                timeout,
                retry_with_old_outcome,
                descriptor=descriptor,
            )

        succeeded = deepcopy(pre_call)
        succeeded.update(
            {
                "inference_state": "succeeded",
                "accepted_output_digest": "sha256:" + "b" * 64,
            }
        )
        self.assertEqual(
            validate_status_transition(pre_call, succeeded, descriptor=descriptor),
            succeeded,
        )
        retry_after_success = deepcopy(succeeded)
        retry_after_success.update(
            {
                "adapter_attempt_count": 2,
                "inference_state": "running",
                "accepted_output_digest": None,
            }
        )
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                succeeded,
                retry_after_success,
                descriptor=descriptor,
            )

        no_call = deepcopy(ready)
        no_call["inference_state"] = "policy_blocked"
        self.assertEqual(
            validate_status_transition(ready, no_call, descriptor=descriptor),
            no_call,
        )
        input_too_large = deepcopy(ready)
        input_too_large["inference_state"] = "input_too_large"
        self.assertEqual(
            validate_status_transition(
                ready,
                input_too_large,
                descriptor=descriptor,
            ),
            input_too_large,
        )
        changed_no_call = deepcopy(no_call)
        changed_no_call["inference_state"] = "input_too_large"
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                no_call,
                changed_no_call,
                descriptor=descriptor,
            )
        call_after_no_call = deepcopy(no_call)
        call_after_no_call.update(
            {"adapter_attempt_count": 1, "inference_state": "running"}
        )
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                no_call,
                call_after_no_call,
                descriptor=descriptor,
            )

        nonretryable = deepcopy(pre_call)
        nonretryable["inference_state"] = "output_too_large"
        retry_nonretryable = deepcopy(nonretryable)
        retry_nonretryable.update(
            {"adapter_attempt_count": 2, "inference_state": "running"}
        )
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                nonretryable,
                retry_nonretryable,
                descriptor=descriptor,
            )

        reclaim = deepcopy(timeout)
        reclaim["worker_attempt_count"] = 2
        self.assertEqual(
            validate_status_transition(timeout, reclaim, descriptor=descriptor),
            reclaim,
        )
        intent = deepcopy(timeout)
        intent.update(
            {
                "report_id": "tg_analysis_report_0123456789abcdef",
                "report_digest": "sha256:" + "c" * 64,
                "render_digest": "sha256:" + "d" * 64,
            }
        )
        self.assertEqual(
            validate_status_transition(timeout, intent, descriptor=descriptor),
            intent,
        )
        report_invalid = deepcopy(intent)
        report_invalid.update(
            {
                "state": "failed",
                "fixed_code": "report_invalid",
                "report_id": None,
                "report_digest": None,
                "render_digest": None,
            }
        )
        self.assertEqual(
            validate_status_transition(
                intent,
                report_invalid,
                descriptor=descriptor,
            ),
            report_invalid,
        )
        forbidden_intent_failure = deepcopy(report_invalid)
        forbidden_intent_failure["fixed_code"] = "interrupted"
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                intent,
                forbidden_intent_failure,
                descriptor=descriptor,
            )
        published = deepcopy(intent)
        published["state"] = "published"
        self.assertEqual(
            validate_status_transition(intent, published, descriptor=descriptor),
            published,
        )

    def test_optional_pre_call_reclaim_is_two_exact_writes(self):
        descriptor = build_descriptor(
            source_kind="legacy_index_entry",
            source_basis=legacy_basis(),
            recipe=default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixed-mock",
            ),
        )
        pending = pending_status(descriptor)
        ready = deepcopy(pending)
        ready.update(
            {
                "state": "running",
                "worker_attempt_count": 1,
                "packet_digest": "sha256:" + "a" * 64,
            }
        )
        reclaimed = deepcopy(ready)
        reclaimed["worker_attempt_count"] = 2
        self.assertEqual(
            validate_status_transition(ready, reclaimed, descriptor=descriptor),
            reclaimed,
        )

        terminal = deepcopy(reclaimed)
        terminal.update(
            {
                "state": "failed",
                "inference_state": "failed",
                "fixed_code": "interrupted",
            }
        )
        self.assertEqual(validate_status(terminal, descriptor=descriptor), terminal)
        self.assertEqual(
            validate_status_transition(reclaimed, terminal, descriptor=descriptor),
            terminal,
        )

        one_write_terminal = deepcopy(terminal)
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                ready,
                one_write_terminal,
                descriptor=descriptor,
            )

        duration_changed = deepcopy(reclaimed)
        duration_changed["duration_ms"] = 1
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                ready,
                duration_changed,
                descriptor=descriptor,
            )

        terminal_duration_changed = deepcopy(terminal)
        terminal_duration_changed["duration_ms"] = 1
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                reclaimed,
                terminal_duration_changed,
                descriptor=descriptor,
            )

        pending_terminal = deepcopy(terminal)
        pending_terminal["inference_state"] = "pending"
        with self.assertRaises(AnalysisContractError):
            validate_status(pending_terminal, descriptor=descriptor)

        adapter_changed = deepcopy(terminal)
        adapter_changed["adapter_attempt_count"] = 1
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                reclaimed,
                adapter_changed,
                descriptor=descriptor,
            )

        output_changed = deepcopy(terminal)
        output_changed.update(
            {
                "adapter_attempt_count": 1,
                "inference_state": "succeeded",
                "accepted_output_digest": "sha256:" + "b" * 64,
            }
        )
        with self.assertRaises(AnalysisContractError):
            validate_status_transition(
                reclaimed,
                output_changed,
                descriptor=descriptor,
            )

        intent_changed = deepcopy(terminal)
        intent_changed.update(
            {
                "report_id": "tg_analysis_report_0123456789abcdef",
                "report_digest": "sha256:" + "c" * 64,
                "render_digest": "sha256:" + "d" * 64,
            }
        )
        with self.assertRaises(AnalysisContractError):
            validate_status(intent_changed, descriptor=descriptor)


if __name__ == "__main__":
    unittest.main()
