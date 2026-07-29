import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import completion_history_projection as projection  # noqa: E402
from task_governance_tool.cli import (  # noqa: E402
    CommandContext,
    handle_task_show,
    task_show_text,
)
from task_governance_tool.completion_history_projection import (  # noqa: E402
    COMPLETION_HISTORY_MAX_BYTES,
    COMPLETION_HISTORY_MAX_CYCLE_BYTES,
    PUBLIC_COMPLETION_CYCLE_FIELDS,
    PUBLIC_COMPLETION_EVIDENCE_FIELDS,
    PUBLIC_COMPLETION_HISTORY_FIELDS,
    PUBLIC_GATE_BASIS_FIELDS,
    PUBLIC_REVIEW_TARGET_FIELDS,
    format_completion_history,
)
from task_governance_tool.storage import (  # noqa: E402
    CompletionCycle,
    CompletionGateBasis,
    CompletionHistory,
    DatabaseTarget,
    ProjectIdentity,
    StorageError,
    completion_history_inconsistent,
)


def exact_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def native_cycle(
    ordinal: int = 1,
    *,
    review_tier: int = 2,
    reason: str = "Approved completion.",
) -> CompletionCycle:
    required = 2 if review_tier == 2 else 1
    receipts = tuple(
        f"tg_receipt_{ordinal}_{slot}"
        for slot in range(1, required + 1)
    )
    return CompletionCycle(
        completion_cycle_id=f"tg_completion_cycle_{ordinal:016x}",
        project_id="tg_project_projection",
        task_id="tg_task_projection",
        saved_cycle_ordinal=ordinal,
        origin="native_done",
        completeness="complete",
        completed_at=f"2026-07-{ordinal:02d}T01:02:03Z",
        recorded_at=f"2026-07-{ordinal:02d}T01:02:03Z",
        contract_revision=ordinal,
        review_tier=review_tier,
        verification_expectation="specified",
        verification_attestation=True,
        completion_evidence_kind="external_revision",
        completion_evidence_revision="PRIVATE-REVISION",
        completion_evidence_reason=reason,
        external_revision_approved=True,
        completion_commit_required=True,
        completion_commit_hash="PRIVATE-REVISION",
        review_target_kind="external_revision",
        review_target_value="PRIVATE-REVISION",
        review_target_base_revision="",
        review_target_generation=ordinal,
        gate_basis=CompletionGateBasis(
            version=1,
            kind="independent_passes",
            required_independent_passes=required,
            qualifying_independent_passes=required,
            changes_requested_count=0,
            open_high_count=0,
            open_medium_count=0,
            fresh_review_required_count=0,
            qualifying_receipt_ids=receipts,
        ),
    )


def oversized_native_cycle(ordinal: int) -> CompletionCycle:
    maximum_revision = "\U0001F600" * 500
    return replace(
        native_cycle(
            ordinal,
            reason="\U0001F600" * 1_000,
        ),
        completion_evidence_revision=maximum_revision,
        completion_commit_hash=maximum_revision,
        review_target_value="\U0001F600" * 500,
    )


def legacy_cycle() -> CompletionCycle:
    return replace(
        native_cycle(review_tier=0),
        origin="legacy_current_done",
        completeness="partial",
        verification_attestation=None,
        gate_basis=CompletionGateBasis(
            version=0,
            kind="unknown",
            required_independent_passes=None,
            qualifying_independent_passes=None,
            changes_requested_count=None,
            open_high_count=None,
            open_medium_count=None,
            fresh_review_required_count=None,
            qualifying_receipt_ids=(),
        ),
    )


class CompletionHistoryProjectionTests(unittest.TestCase):
    def test_native_cycle_uses_exact_public_allowlists(self):
        result = format_completion_history(
            CompletionHistory(
                total=1,
                legacy_history_incomplete=False,
                cycles=(native_cycle(),),
            )
        )

        self.assertEqual(tuple(result), PUBLIC_COMPLETION_HISTORY_FIELDS)
        self.assertEqual(result["returned_count"], 1)
        self.assertFalse(result["truncated"])
        cycle = result["cycles"][0]
        self.assertEqual(tuple(cycle), PUBLIC_COMPLETION_CYCLE_FIELDS)
        self.assertEqual(
            tuple(cycle["completion_evidence"]),
            PUBLIC_COMPLETION_EVIDENCE_FIELDS,
        )
        self.assertEqual(
            tuple(cycle["review_target"]),
            PUBLIC_REVIEW_TARGET_FIELDS,
        )
        self.assertEqual(tuple(cycle["gate_basis"]), PUBLIC_GATE_BASIS_FIELDS)
        self.assertIs(cycle["verification_attestation"], True)
        self.assertEqual(
            cycle["gate_basis"]["qualifying_receipt_ids"],
            ["tg_receipt_1_1", "tg_receipt_1_2"],
        )
        self.assertNotIn("project_id", cycle)
        self.assertNotIn("task_id", cycle)
        self.assertNotIn("recorded_at", cycle)

    def test_legacy_cycle_maps_counts_to_null_and_receipts_to_empty(self):
        result = format_completion_history(
            CompletionHistory(
                total=1,
                legacy_history_incomplete=True,
                cycles=(legacy_cycle(),),
            )
        )

        cycle = result["cycles"][0]
        self.assertIsNone(cycle["verification_attestation"])
        basis = cycle["gate_basis"]
        for field in (
            "required_independent_passes",
            "qualifying_independent_passes",
            "changes_requested",
            "open_high",
            "open_medium",
            "fresh_review_required",
        ):
            self.assertIsNone(basis[field])
        self.assertEqual(basis["qualifying_receipt_ids"], [])

    def test_oversized_newest_cycle_stops_without_substituting_older_row(self):
        oversized = oversized_native_cycle(2)
        result = format_completion_history(
            CompletionHistory(
                total=2,
                legacy_history_incomplete=False,
                cycles=(oversized, native_cycle(1)),
            )
        )

        self.assertEqual(result["returned_count"], 0)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["cycles"], [])

    def test_candidate_final_wrapper_enforces_utf8_component_limit(self):
        cycles = tuple(
            native_cycle(ordinal, reason="界" * 2_200)
            for ordinal in range(5, 0, -1)
        )
        self.assertTrue(
            all(
                len(
                    exact_json_bytes(
                        format_completion_history(
                            CompletionHistory(
                                total=1,
                                legacy_history_incomplete=False,
                                cycles=(cycle,),
                            )
                        )["cycles"][0]
                    )
                )
                <= COMPLETION_HISTORY_MAX_CYCLE_BYTES
                for cycle in cycles
            )
        )

        result = format_completion_history(
            CompletionHistory(
                total=100,
                legacy_history_incomplete=False,
                cycles=cycles,
            )
        )

        self.assertEqual(result["returned_count"], 4)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(
            len(exact_json_bytes(result)),
            COMPLETION_HISTORY_MAX_BYTES,
        )
        rejected_candidate = {
            **result,
            "returned_count": 5,
            "cycles": [
                *result["cycles"],
                format_completion_history(
                    CompletionHistory(
                        total=1,
                        legacy_history_incomplete=False,
                        cycles=(cycles[4],),
                    )
                )["cycles"][0],
            ],
        }
        self.assertGreater(
            len(exact_json_bytes(rejected_candidate)),
            COMPLETION_HISTORY_MAX_BYTES,
        )

    def test_projection_returns_only_newest_ten_cycle_prefix(self):
        result = format_completion_history(
            CompletionHistory(
                total=11,
                legacy_history_incomplete=False,
                cycles=tuple(
                    native_cycle(ordinal, review_tier=1)
                    for ordinal in range(11, 0, -1)
                ),
            )
        )

        self.assertEqual(result["returned_count"], 10)
        self.assertTrue(result["truncated"])
        self.assertEqual(
            [
                cycle["saved_cycle_ordinal"]
                for cycle in result["cycles"]
            ],
            list(range(11, 1, -1)),
        )

    def test_cycle_byte_limit_accepts_equality_and_rejects_one_byte_less(self):
        history = CompletionHistory(
            total=1,
            legacy_history_incomplete=False,
            cycles=(native_cycle(review_tier=1),),
        )
        public_cycle = format_completion_history(history)["cycles"][0]
        measured_size = len(exact_json_bytes(public_cycle))

        with mock.patch.object(
            projection,
            "COMPLETION_HISTORY_MAX_CYCLE_BYTES",
            measured_size,
        ):
            accepted = format_completion_history(history)
        with mock.patch.object(
            projection,
            "COMPLETION_HISTORY_MAX_CYCLE_BYTES",
            measured_size - 1,
        ):
            rejected = format_completion_history(history)

        self.assertEqual(accepted["returned_count"], 1)
        self.assertFalse(accepted["truncated"])
        self.assertEqual(rejected["returned_count"], 0)
        self.assertTrue(rejected["truncated"])

    def test_wrapper_limit_measures_final_tenth_candidate_and_count_digits(self):
        history = CompletionHistory(
            total=10,
            legacy_history_incomplete=False,
            cycles=tuple(
                native_cycle(ordinal, review_tier=1)
                for ordinal in range(10, 0, -1)
            ),
        )
        complete_wrapper = format_completion_history(history)
        self.assertEqual(complete_wrapper["returned_count"], 10)
        measured_size = len(exact_json_bytes(complete_wrapper))

        with mock.patch.object(
            projection,
            "COMPLETION_HISTORY_MAX_BYTES",
            measured_size,
        ):
            accepted = format_completion_history(history)
        with mock.patch.object(
            projection,
            "COMPLETION_HISTORY_MAX_BYTES",
            measured_size - 1,
        ):
            rejected = format_completion_history(history)

        self.assertEqual(accepted["returned_count"], 10)
        self.assertFalse(accepted["truncated"])
        self.assertEqual(rejected["returned_count"], 9)
        self.assertTrue(rejected["truncated"])
        self.assertLess(
            len(exact_json_bytes(rejected)),
            measured_size - 1,
        )

    def test_invalid_false_attestation_is_rejected(self):
        with self.assertRaises(StorageError) as raised:
            format_completion_history(
                CompletionHistory(
                    total=1,
                    legacy_history_incomplete=False,
                    cycles=(
                        replace(
                            native_cycle(),
                            verification_attestation=False,
                        ),
                    ),
                )
            )
        self.assertEqual(raised.exception.code, "completion_history_inconsistent")

    def test_task_show_reports_sanitized_history_error_with_exit_two(self):
        project = ProjectIdentity(
            project_id="tg_project_projection",
            canonical_repo=ROOT,
            canonical_path_hash="0" * 64,
            display_name="projection",
        )
        context = CommandContext(
            command="task.show",
            repo=str(ROOT),
            repo_explicit=True,
            json_output=True,
            read_only=True,
            args=SimpleNamespace(task_id="tg_task_projection"),
            target_override=DatabaseTarget(
                project=project,
                db_path=ROOT / "ignored.sqlite",
                explicit_db=False,
            ),
            read_connection_override=object(),
        )

        with mock.patch(
            "task_governance_tool.cli.show_task",
            side_effect=completion_history_inconsistent(),
        ):
            result = handle_task_show(context)

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.errors,
            [
                {
                    "code": "completion_history_inconsistent",
                    "message": "stored completion history is inconsistent",
                }
            ],
        )
        self.assertIsNone(result.data["completion_history"])

    def test_task_show_text_exposes_only_latest_non_content_history_fields(self):
        raw_history = CompletionHistory(
            total=1,
            legacy_history_incomplete=False,
            cycles=(native_cycle(),),
        )
        history = format_completion_history(raw_history)
        text = task_show_text(
            {
                "task_id": "tg_task_projection",
                "title": "History text",
                "status": "done",
                "priority": "normal",
                "kind": "optional",
                "lane": "",
                "lane_order": None,
                "review_tier": 2,
                "verification": "",
                "completion_evidence_kind": "external_revision",
                "completion_evidence_revision": "",
                "completion_evidence_reason": "",
                "blocked_reason": "",
            },
            [],
            "No next action; the task is done.",
            {
                "target": {"generation": 1},
                "gate": {
                    "qualifying_independent_passes": 2,
                    "required_independent_passes": 2,
                    "satisfied": True,
                },
            },
            {
                "pending_handoff": 0,
                "handed_off": 0,
                "handoff_withdrawn_by_user": 0,
            },
            {"revision": 1},
            history,
            {
                "saved_cycle_ordinal": 1,
                "origin": "native_done",
                "completeness": "complete",
                "completed_at": "2026-07-01T01:02:03Z",
                "completion_evidence_kind": "external_revision",
                "review_target_kind": "external_revision",
                "review_target_generation": 1,
                "review_basis_kind": "independent_passes",
            },
        )

        self.assertIn("ordinal=1", text)
        self.assertIn("native_done/complete", text)
        self.assertIn("evidence=external_revision", text)
        self.assertIn("target=external_revision/generation 1", text)
        self.assertIn("review_basis=independent_passes", text)
        self.assertNotIn("PRIVATE-REVISION", text)
        self.assertNotIn("Approved completion.", text)
        self.assertNotIn("tg_receipt_", text)


if __name__ == "__main__":
    unittest.main()
