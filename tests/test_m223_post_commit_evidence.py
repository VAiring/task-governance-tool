from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import cli as cli_service
from task_governance_tool import maintenance as maintenance_service
from task_governance_tool.cli import CommandResult, apply_post_commit_maintenance
from task_governance_tool.maintenance import (
    BACKUP_WARNING_MESSAGES,
    EVIDENCE_WARNING_MESSAGES,
    MutationOutcome,
    VIEWER_WARNING_MESSAGES,
    run_post_commit_maintenance,
)


class EvidencePostCommitOrderingTests(unittest.TestCase):
    def test_coordinator_fallback_warns_evidence_first_for_every_mutation(self):
        for viewer_relevant in (False, True):
            with self.subTest(viewer_relevant=viewer_relevant):
                result = CommandResult(
                    ok=True,
                    command="task.edit",
                    text="task updated",
                    mutation_outcome=MutationOutcome(
                        state_changed=True,
                        viewer_relevant=viewer_relevant,
                    ),
                    maintenance_target=object(),
                )
                with mock.patch.object(
                    cli_service,
                    "run_post_commit_maintenance",
                    side_effect=RuntimeError("injected coordinator failure"),
                ):
                    maintained = apply_post_commit_maintenance(
                        object(),
                        result,
                    )

                expected = [
                    {
                        "code": "evidence_projection_failed",
                        "message": EVIDENCE_WARNING_MESSAGES["failed"],
                    }
                ]
                if viewer_relevant:
                    expected.append(
                        {
                            "code": "viewer_refresh_failed",
                            "message": VIEWER_WARNING_MESSAGES["failed"],
                        }
                    )
                expected.append(
                    {
                        "code": "backup_failed",
                        "message": BACKUP_WARNING_MESSAGES["failed"],
                    }
                )
                self.assertEqual(maintained.warnings, expected)
                self.assertEqual(
                    maintained.text.splitlines(),
                    ["task updated", *(item["message"] for item in expected)],
                )

    def test_evidence_viewer_backup_order_and_fixed_warnings(self):
        calls: list[str] = []

        def outcome(name: str, code: str):
            def invoke(*args, **kwargs):
                calls.append(name)
                return SimpleNamespace(code=code)

            return invoke

        with (
            mock.patch.object(
                maintenance_service,
                "run_routine_evidence_projection",
                side_effect=outcome("evidence", "deferred"),
            ),
            mock.patch.object(
                maintenance_service,
                "run_routine_viewer_refresh",
                side_effect=outcome("viewer", "failed"),
            ),
            mock.patch.object(
                maintenance_service,
                "run_routine_backup",
                side_effect=outcome("backup", "deferred"),
            ),
        ):
            warnings = run_post_commit_maintenance(
                object(),
                MutationOutcome(state_changed=True, viewer_relevant=True),
                observed_at="2026-08-05T00:00:00Z",
            )

        self.assertEqual(calls, ["evidence", "viewer", "backup"])
        self.assertEqual(
            warnings,
            [
                {
                    "code": "evidence_projection_deferred",
                    "message": EVIDENCE_WARNING_MESSAGES["deferred"],
                },
                {
                    "code": "viewer_refresh_failed",
                    "message": VIEWER_WARNING_MESSAGES["failed"],
                },
                {
                    "code": "backup_deferred",
                    "message": BACKUP_WARNING_MESSAGES["deferred"],
                },
            ],
        )

    def test_non_viewer_mutation_still_runs_evidence_then_backup(self):
        calls: list[str] = []

        def current(name: str):
            def invoke(*args, **kwargs):
                calls.append(name)
                return SimpleNamespace(code="current")

            return invoke

        with (
            mock.patch.object(
                maintenance_service,
                "run_routine_evidence_projection",
                side_effect=current("evidence"),
            ),
            mock.patch.object(
                maintenance_service,
                "run_routine_viewer_refresh",
            ) as viewer,
            mock.patch.object(
                maintenance_service,
                "run_routine_backup",
                side_effect=current("backup"),
            ),
        ):
            warnings = run_post_commit_maintenance(
                object(),
                MutationOutcome(state_changed=True, viewer_relevant=False),
            )

        self.assertEqual(warnings, [])
        self.assertEqual(calls, ["evidence", "backup"])
        viewer.assert_not_called()

    def test_no_change_skips_every_maintenance_stage(self):
        with (
            mock.patch.object(
                maintenance_service,
                "run_routine_evidence_projection",
            ) as evidence,
            mock.patch.object(
                maintenance_service,
                "run_routine_viewer_refresh",
            ) as viewer,
            mock.patch.object(
                maintenance_service,
                "run_routine_backup",
            ) as backup,
        ):
            warnings = run_post_commit_maintenance(
                object(),
                MutationOutcome(state_changed=False, viewer_relevant=True),
            )

        self.assertEqual(warnings, [])
        evidence.assert_not_called()
        viewer.assert_not_called()
        backup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
