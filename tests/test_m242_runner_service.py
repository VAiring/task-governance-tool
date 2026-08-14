import sys
import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from contextlib import closing

from tests.verification_receipt_test_support import (
    FINGERPRINT_A,
    initialize,
    run_taskgov,
    target_for,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.cli import runner_cancellation_latch
    from task_governance_tool.tasks import TaskRepositoryError
    from task_governance_tool.storage import (
        VerificationRunnerAttempt,
        VerificationRunnerResolution,
        _verification_runner_observation_matrix_valid,
        connect,
    )
    from task_governance_tool._verification_runner_win32 import (
        JobAccounting,
    )
    from task_governance_tool.verification_runner import (
        verification_runner_observation_digest,
    )
    from task_governance_tool.verification_runner_service import (
        VerificationRunnerServiceError,
        _attempt_terminal_observation,
        _cleanup_attempt_and_publish,
        _cleanup_failure_observation,
        _prelaunch_attempt_observation,
        _post_process_observation,
        _process_terminal_observation,
        _read_cancel_requested,
        _unexpected_attempt_observation,
        set_review_target_with_shadow_runner,
    )
    from task_governance_tool.verification_runner_runtime import (
        RunnerImplementationIdentity,
        VerificationRunnerRuntimeError,
    )
    from task_governance_tool.verification_runner_process import (
        ProcessRunResult,
        StepProcessResult,
    )
finally:
    sys.path.pop(0)


DIGEST = "sha256:" + "a" * 64
RAW = "b" * 64


def resolution() -> VerificationRunnerResolution:
    return VerificationRunnerResolution(
        verification_runner_resolution_id="tg_verification_runner_resolution_1111111111111111",
        project_id="tg_project_11111111111111111111111111111111",
        task_id="tg_task_1111111111111111",
        contract_revision=2,
        authority_snapshot_id="tg_authority_snapshot_1111111111111111",
        verification_criterion_id="tg_contract_criterion_1111111111111111",
        verification_expectation_digest=RAW,
        verification_criterion_digest=DIGEST,
        target_kind="git_snapshot",
        target_value=DIGEST,
        target_base_revision="1" * 40,
        target_generation=3,
        target_capture_version=1,
        artifact_manifest_id="tg_artifact_manifest_1111111111111111",
        target_material_digest=DIGEST,
        plan_state="runner",
        plan_blob_object_id="2" * 40,
        plan_raw_digest=DIGEST,
        plan_id="verification",
        plan_version=1,
        plan_semantic_digest=DIGEST,
        selected_entry_digest=DIGEST,
        coverage="complete",
        step_count=2,
        runner_contract_version=1,
        runner_implementation_version="taskgov-verification-runner/1",
        runner_implementation_digest=DIGEST,
        runner_policy_digest=DIGEST,
        sandbox_provider=None,
        sandbox_policy_digest=None,
        runtime_digest=None,
        gate_eligibility_version=0,
        trigger="review_target_set_v1",
        route="runner",
        reason=None,
        idempotency_digest=DIGEST,
        created_at="2026-08-09T00:00:00Z",
    )


def attempt() -> VerificationRunnerAttempt:
    return VerificationRunnerAttempt(
        verification_runner_attempt_id="tg_verification_runner_attempt_2222222222222222",
        project_id=resolution().project_id,
        task_id=resolution().task_id,
        target_generation=3,
        gate_eligibility_version=0,
        verification_runner_resolution_id=resolution().verification_runner_resolution_id,
        target_material_digest=DIGEST,
        runner_implementation_digest=DIGEST,
        sandbox_instance_digest=DIGEST,
        attempt_digest=DIGEST,
        intent_recorded_at="2026-08-09T00:00:00Z",
    )


class VerificationRunnerServiceModelTests(unittest.TestCase):
    def test_cli_ctrl_c_latch_is_bounded_and_restores_the_previous_handler(self):
        previous = object()
        handlers = []
        with (
            mock.patch(
                "task_governance_tool.cli.signal.getsignal",
                return_value=previous,
            ),
            mock.patch(
                "task_governance_tool.cli.signal.signal",
                side_effect=lambda _signal, handler: handlers.append(handler),
            ),
        ):
            with runner_cancellation_latch() as cancel_requested:
                self.assertFalse(cancel_requested())
                handlers[0](None, None)
                self.assertTrue(cancel_requested())
        self.assertIs(handlers[-1], previous)

    def test_cli_ctrl_c_latch_preserves_frozen_domain_exceptions(self):
        with self.assertRaisesRegex(TaskRepositoryError, "expected"):
            with runner_cancellation_latch():
                raise TaskRepositoryError("expected", "expected")


    def test_unknown_process_exception_is_launch_uncertain_and_integrity_never_falls_back(self):
        process_unknown = _unexpected_attempt_observation(
            resolution(),
            attempt(),
            stage="process",
            error=RuntimeError("unprojected child state"),
            observed_at="2026-08-09T00:00:01Z",
        )
        self.assertEqual(
            (
                process_unknown.launch_state,
                process_unknown.route,
                process_unknown.outcome,
                process_unknown.reason,
            ),
            (
                "launch_uncertain",
                "blocked",
                "controller_interrupted",
                "controller_interrupted",
            ),
        )

    def test_incomplete_final_proof_or_late_cancel_cannot_publish_pass(self):
        selected = resolution()
        selected_attempt = attempt()
        passed = _attempt_terminal_observation(
            selected,
            selected_attempt,
            launch_state="launched",
            outcome="pass",
            reason=None,
            route="runner",
            total_step_count=2,
            completed_step_count=2,
            failed_step_ordinal=None,
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:02Z",
            duration_ms=1000,
            cpu_time_ms=100,
            peak_job_memory_bytes=4096,
            total_process_count=2,
        )
        unproved = _post_process_observation(
            selected,
            selected_attempt,
            passed,
            outcome="sandbox_violation",
            reason="sandbox_boundary_violation",
            finished_at="2026-08-09T00:00:03Z",
        )
        cancelled = _post_process_observation(
            selected,
            selected_attempt,
            passed,
            outcome="cancelled",
            reason="cancelled",
            finished_at="2026-08-09T00:00:03Z",
        )
        interrupted = _post_process_observation(
            selected,
            selected_attempt,
            passed,
            outcome="controller_interrupted",
            reason="controller_interrupted",
            finished_at="2026-08-09T00:00:03Z",
        )
        self.assertEqual(
            (unproved.outcome, unproved.reason, unproved.complete_plan),
            ("sandbox_violation", "sandbox_boundary_violation", 0),
        )
        self.assertEqual(
            (cancelled.outcome, cancelled.reason, cancelled.complete_plan),
            ("cancelled", "cancelled", 0),
        )
        self.assertEqual(
            (
                interrupted.route,
                interrupted.launch_state,
                interrupted.outcome,
                interrupted.reason,
                interrupted.duration_ms,
            ),
            (
                "blocked",
                "launch_uncertain",
                "controller_interrupted",
                "controller_interrupted",
                0,
            ),
        )
    def test_post_process_preserves_uncertainty_and_cleanup_failure(self):
        selected = resolution()
        selected_attempt = attempt()
        uncertain = _attempt_terminal_observation(
            selected,
            selected_attempt,
            launch_state="launch_uncertain",
            outcome="controller_interrupted",
            reason="controller_interrupted",
            route="blocked",
            total_step_count=2,
            completed_step_count=1,
            failed_step_ordinal=None,
            started_at="2026-08-09T00:00:02Z",
            finished_at="2026-08-09T00:00:02Z",
            duration_ms=0,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
        )
        cleanup_failed = _cleanup_failure_observation(
            selected,
            selected_attempt,
            uncertain,
            finished_at="2026-08-09T00:00:03Z",
        )

        for outcome, reason in (
            ("sandbox_violation", "materialization_failed"),
            ("cancelled", "cancelled"),
            ("controller_interrupted", "controller_interrupted"),
        ):
            with self.subTest(outcome=outcome):
                self.assertIs(
                    _post_process_observation(
                        selected,
                        selected_attempt,
                        uncertain,
                        outcome=outcome,
                        reason=reason,
                        finished_at="2026-08-09T00:00:04Z",
                    ),
                    uncertain,
                )

        self.assertIs(
            _post_process_observation(
                selected,
                selected_attempt,
                cleanup_failed,
                outcome="controller_interrupted",
                reason="controller_interrupted",
                finished_at="2026-08-09T00:00:04Z",
            ),
            cleanup_failed,
        )




class VerificationRunnerServiceIntegrationTests(unittest.TestCase):
    def _assert_uncertain_post_process_case(
        self,
        *,
        case_name: str,
        final_proof_fails: bool,
        cancel_result: object,
        cancel_error: BaseException | None,
        expected_cancel_calls: int,
    ) -> None:
        selected = resolution()
        selected_attempt = attempt()
        uncertain = _attempt_terminal_observation(
            selected,
            selected_attempt,
            launch_state="launch_uncertain",
            outcome="controller_interrupted",
            reason="controller_interrupted",
            route="blocked",
            total_step_count=2,
            completed_step_count=1,
            failed_step_ordinal=None,
            started_at="2026-08-09T00:00:02Z",
            finished_at="2026-08-09T00:00:02Z",
            duration_ms=0,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
        )
        cancel_requested = (
            mock.Mock(side_effect=cancel_error)
            if cancel_error is not None
            else mock.Mock(return_value=cancel_result)
        )
        if final_proof_fails:
            provisional = _post_process_observation(
                selected,
                selected_attempt,
                uncertain,
                outcome="sandbox_violation",
                reason="materialization_failed",
                finished_at="2026-08-09T00:00:03Z",
            )
        else:
            cancelled = _read_cancel_requested(cancel_requested)
            if cancelled is False:
                provisional = uncertain
            else:
                provisional = _post_process_observation(
                    selected,
                    selected_attempt,
                    uncertain,
                    outcome=(
                        "cancelled"
                        if cancelled is True
                        else "controller_interrupted"
                    ),
                    reason=(
                        "cancelled"
                        if cancelled is True
                        else "controller_interrupted"
                    ),
                    finished_at="2026-08-09T00:00:03Z",
                )

        self.assertEqual(cancel_requested.call_count, expected_cancel_calls)
        self.assertIs(provisional, uncertain)
        cleanup = _cleanup_failure_observation(
            selected,
            selected_attempt,
            provisional,
            finished_at="2026-08-09T00:00:04Z",
        )
        self.assertEqual(
            (
                cleanup.route,
                cleanup.launch_state,
                cleanup.outcome,
                cleanup.reason,
                cleanup.completed_step_count,
                cleanup.failed_step_ordinal,
                cleanup.duration_ms,
                cleanup.cpu_time_ms,
                cleanup.peak_job_memory_bytes,
                cleanup.total_process_count,
            ),
            (
                "blocked",
                "launch_uncertain",
                "sandbox_cleanup_failed",
                "sandbox_cleanup_failed",
                1,
                None,
                0,
                None,
                None,
                None,
            ),
            case_name,
        )

    def test_uncertain_process_state_survives_all_post_process_paths(self):
        cases = (
            ("final-proof-failure", True, False, None, 0),
            ("late-cancel-true", False, True, None, 1),
            (
                "late-cancel-exception",
                False,
                False,
                RuntimeError("cancellation callback failed"),
                1,
            ),
            ("late-cancel-nonbool", False, "not-a-bool", None, 1),
        )
        for (
            case_name,
            final_proof_fails,
            cancel_result,
            cancel_error,
            expected_cancel_calls,
        ) in cases:
            with self.subTest(case_name=case_name):
                self._assert_uncertain_post_process_case(
                    case_name=case_name,
                    final_proof_fails=final_proof_fails,
                    cancel_result=cancel_result,
                    cancel_error=cancel_error,
                    expected_cancel_calls=expected_cancel_calls,
                )

    def test_oversized_plan_rolls_back_target_and_all_runner_evidence(self):
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-service-", dir=ROOT) as tmp:
            root = Path(tmp)
            repo, db = initialize(root)
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "TaskGov Test"],
                check=True,
            )
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "--quiet", "-m", "seed"],
                check=True,
            )
            added = run_taskgov(
                "task", "add", "--repo", str(repo), "--db", str(db),
                "--title", "M24.2 unsafe plan rollback", "--status", "in_progress",
                "--review-tier", "0", "--verification", "offline runner fixture",
                "--contract-scope", "Reject an unsafe project-owned plan before persistence.",
                "--contract-acceptance", "The target generation and Runner ledger remain unchanged.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-2",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr or added.stdout)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            plan_path = repo / "skill" / "config" / "verification-runner.json"
            plan_path.parent.mkdir(parents=True)
            plan_path.write_bytes(b"{" + b" " * 65_536)
            subprocess.run(
                ["git", "-C", str(repo), "add", "skill/config/verification-runner.json"],
                check=True,
            )
            target = replace(target_for(db, repo), skill_root=repo / "skill")

            def persisted_state():
                with closing(connect(db)) as connection:
                    task = tuple(
                        connection.execute(
                            "SELECT review_target_kind, review_target_value, "
                            "review_target_base_revision, review_target_generation "
                            "FROM tasks WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                    )
                    tables = (
                        "artifact_manifests",
                        "verification_runner_resolutions",
                        "verification_runner_attempts",
                        "verification_runner_sandbox_events",
                        "verification_runner_observations",
                        "evidence_references",
                        "criterion_evidence_links",
                        "task_events",
                    )
                    counts = tuple(
                        (
                            table,
                            int(
                                connection.execute(
                                    f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                                    (task_id,),
                                ).fetchone()[0]
                            ),
                        )
                        for table in tables
                    )
                return task, counts

            before = persisted_state()
            with self.assertRaises(VerificationRunnerServiceError) as raised:
                set_review_target_with_shadow_runner(
                    target,
                    task_id,
                    kind="git_snapshot",
                )

            self.assertEqual(raised.exception.code, "plan_invalid")
            self.assertEqual(
                raised.exception.message,
                "verification Runner plan is too large",
            )
            self.assertNotIn(str(repo), raised.exception.message)
            self.assertEqual(persisted_state(), before)


    def test_process_cleanup_failure_cannot_publish_cleanup_success(self):
        selected = resolution()
        selected_attempt = attempt()
        launched = _process_terminal_observation(
            selected,
            selected_attempt,
            ProcessRunResult(
                outcome="sandbox_cleanup_failed",
                reason="sandbox_cleanup_failed",
                launch_state="launched",
                failed_step_ordinal=None,
                duration_ms=1000,
                cpu_time_ms=None,
                peak_job_memory_bytes=None,
                total_process_count=None,
                steps=(
                    StepProcessResult(
                        ordinal=1,
                        outcome="pass",
                        reason=None,
                        launch_state="launched",
                        accounting=JobAccounting(10_000, 4096, 1, 0),
                    ),
                ),
            ),
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:02Z",
            basis_drifted=False,
        )
        intent = SimpleNamespace(
            review=mock.sentinel.review,
            resolution=selected,
            attempt=selected_attempt,
        )
        exact = SimpleNamespace(root=mock.Mock(), quarantine=mock.Mock())
        exact.root.exists.return_value = False
        with (
            mock.patch(
                "task_governance_tool.verification_runner_service._runner_paths",
                return_value=mock.sentinel.paths,
            ),
            mock.patch(
                "task_governance_tool.verification_runner_service.attempt_paths",
                return_value=exact,
            ),
            mock.patch(
                "task_governance_tool.verification_runner_service.remove_attempt_tree"
            ) as remove_tree,
            mock.patch(
                "task_governance_tool.verification_runner_service._publish_attempt_terminal",
                return_value={"outcome": "sandbox_cleanup_failed"},
            ) as publish,
        ):
            _cleanup_attempt_and_publish(
                mock.sentinel.target,
                mock.sentinel.authority,
                intent,
                launched,
            )

        remove_tree.assert_not_called()
        self.assertFalse(publish.call_args.kwargs["cleanup_proved"])
        self.assertEqual(publish.call_args.args[4].outcome, "sandbox_cleanup_failed")


    def test_unsupported_exact_target_records_shadow_fallback_atomically(self):
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-service-", dir=ROOT) as tmp:
            root = Path(tmp)
            repo, db = initialize(root)
            added = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "M24.2 shadow fallback",
                "--status",
                "in_progress",
                "--review-tier",
                "0",
                "--verification",
                "offline focused verification",
                "--contract-scope",
                "Record an inert schema-v20 Runner observation.",
                "--contract-acceptance",
                "The M21 completion basis remains unchanged.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-2",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr or added.stdout)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            target = replace(target_for(db, repo), skill_root=repo / "skill")
            implementation = RunnerImplementationIdentity(
                implementation_version="taskgov-verification-runner/1",
                implementation_digest=DIGEST,
                manifest_version=1,
                package_name="task-governance-tool",
                package_version="0.13.0",
                core_files=(("SKILL.md", DIGEST),),
            )
            with mock.patch(
                "task_governance_tool.verification_runner_service.capture_runner_implementation",
                return_value=implementation,
            ):
                result = set_review_target_with_shadow_runner(
                    target,
                    task_id,
                    kind="diff_fingerprint",
                    revision=FINGERPRINT_A,
                )

            self.assertEqual(result.review.task["review_target_generation"], 1)
            self.assertEqual(
                result.verification_runner,
                {
                    "eligibility": "shadow",
                    "observation_id": mock.ANY,
                    "outcome": "not_run",
                    "phase": "shadow",
                    "reason": "unsupported_target",
                    "route": "m21_fallback",
                    "schema_version": 1,
                    "target_generation": 1,
                },
            )

    def test_unavailable_runner_implementation_never_blocks_exact_target_kinds(self):
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-service-", dir=ROOT) as tmp:
            root = Path(tmp)
            repo, db = initialize(root)
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "TaskGov Test"],
                check=True,
            )
            (repo / "skill" / "config").mkdir(parents=True)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "--quiet", "-m", "seed"],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "candidate.txt"],
                check=True,
            )
            added = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "M24.2 unavailable implementation fallback",
                "--status",
                "in_progress",
                "--review-tier",
                "0",
                "--verification",
                "offline focused verification",
                "--contract-scope",
                "Keep exact target installation independent from Runner availability.",
                "--contract-acceptance",
                "Every supported target kind remains reviewable without a Runner row.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-2",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr or added.stdout)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            target = replace(target_for(db, repo), skill_root=repo / "skill")
            cases = (
                ("diff_fingerprint", FINGERPRINT_A),
                ("external_revision", "release-reviewed"),
                ("git_commit", commit),
                ("git_snapshot", None),
            )
            unavailable = VerificationRunnerRuntimeError(
                "policy_mismatch",
                "the installed Runner implementation is unavailable",
            )

            with mock.patch(
                "task_governance_tool.verification_runner_service.capture_runner_implementation",
                side_effect=unavailable,
            ) as capture_implementation:
                for generation, (kind, revision) in enumerate(cases, start=1):
                    with self.subTest(kind=kind):
                        result = set_review_target_with_shadow_runner(
                            target,
                            task_id,
                            kind=kind,
                            revision=revision,
                        )
                        self.assertEqual(
                            result.review.task["review_target_generation"],
                            generation,
                        )
                        self.assertEqual(
                            result.review.task["review_target_kind"],
                            kind,
                        )
                        if kind != "git_snapshot":
                            self.assertEqual(
                                result.review.task["review_target_value"],
                                revision,
                            )
                        self.assertEqual(
                            result.verification_runner,
                            {
                                "eligibility": "shadow",
                                "observation_id": None,
                                "outcome": None,
                                "phase": "shadow",
                                "reason": None,
                                "route": None,
                                "schema_version": 1,
                                "target_generation": generation,
                            },
                        )

            self.assertEqual(capture_implementation.call_count, len(cases))
            with closing(connect(db)) as connection:
                for table in (
                    "verification_runner_resolutions",
                    "verification_runner_attempts",
                    "verification_runner_observations",
                ):
                    count = connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    self.assertEqual(count, 0, table)

    def test_non_policy_runner_identity_failure_keeps_target_unset(self):
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-service-", dir=ROOT) as tmp:
            root = Path(tmp)
            repo, db = initialize(root)
            added = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--title",
                "M24.2 inconsistent implementation boundary",
                "--status",
                "in_progress",
                "--review-tier",
                "0",
                "--verification",
                "offline focused verification",
                "--contract-scope",
                "Preserve fail-closed Runner lifecycle errors.",
                "--contract-acceptance",
                "Only policy mismatch is an optional no-Runner state.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-2",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr or added.stdout)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            target = replace(target_for(db, repo), skill_root=repo / "skill")
            with (
                mock.patch(
                    "task_governance_tool.verification_runner_service.capture_runner_implementation",
                    side_effect=VerificationRunnerRuntimeError(
                        "state_inconsistent",
                        "verification Runner identity is inconsistent",
                    ),
                ),
                self.assertRaises(VerificationRunnerServiceError) as raised,
            ):
                set_review_target_with_shadow_runner(
                    target,
                    task_id,
                    kind="diff_fingerprint",
                    revision=FINGERPRINT_A,
                )

            self.assertEqual(raised.exception.code, "state_inconsistent")
            with closing(connect(db)) as connection:
                row = connection.execute(
                    "SELECT review_target_generation, review_target_kind "
                    "FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                self.assertEqual(tuple(row), (0, ""))

    def test_cleanup_unsafe_git_target_falls_back_before_attempt_side_effects(self):
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-service-", dir=ROOT) as tmp:
            root = Path(tmp)
            repo, db = initialize(root)
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "TaskGov Test"],
                check=True,
            )
            (repo / "skill" / "config").mkdir(parents=True)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "--quiet", "-m", "seed"],
                check=True,
            )
            cleanup_unsafe = "/".join((*(("a",) * 64), "check.py"))
            unsafe_path = repo.joinpath(*cleanup_unsafe.split("/"))
            unsafe_path.parent.mkdir(parents=True)
            unsafe_path.write_text("raise SystemExit(0)\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "--", cleanup_unsafe],
                check=True,
            )
            added = run_taskgov(
                "task", "add", "--repo", str(repo), "--db", str(db),
                "--title", "M24.2 cleanup-unsafe fallback", "--status", "in_progress",
                "--review-tier", "0", "--verification", "offline runner fixture",
                "--contract-scope", "Reject a cleanup-unsafe exact Git target.",
                "--contract-acceptance", "Fallback owns no attempt side effect.",
                "--contract-authority-ref",
                "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-2",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr or added.stdout)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            target = replace(target_for(db, repo), skill_root=repo / "skill")
            implementation = RunnerImplementationIdentity(
                implementation_version="taskgov-verification-runner/1",
                implementation_digest=DIGEST,
                manifest_version=1,
                package_name="task-governance-tool",
                package_version="0.13.0",
                core_files=(("SKILL.md", DIGEST),),
            )

            with (
                mock.patch(
                    "task_governance_tool.verification_runner_service.capture_runner_implementation",
                    return_value=implementation,
                ),
                mock.patch(
                    "task_governance_tool.verification_runner_service.preflight_runner_material"
                ) as preflight,
            ):
                result = set_review_target_with_shadow_runner(
                    target,
                    task_id,
                    kind="git_snapshot",
                )

            self.assertEqual(
                (
                    result.verification_runner["route"],
                    result.verification_runner["reason"],
                    result.verification_runner["outcome"],
                ),
                ("m21_fallback", "unsupported_target", "not_run"),
            )
            preflight.assert_not_called()
            with closing(connect(db)) as connection:
                attempt_count = connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_attempts"
                ).fetchone()[0]
            self.assertEqual(attempt_count, 0)
            if target.resolved_verification_runner_attempts.exists():
                self.assertEqual(
                    list(target.resolved_verification_runner_attempts.iterdir()),
                    [],
                )

    def test_supported_candidate_records_pass_only_after_owned_cleanup(self):
        selected = resolution()
        selected_attempt = attempt()
        passed = _attempt_terminal_observation(
            selected,
            selected_attempt,
            launch_state="launched",
            outcome="pass",
            reason=None,
            route="runner",
            total_step_count=2,
            completed_step_count=2,
            failed_step_ordinal=None,
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:02Z",
            duration_ms=1000,
            cpu_time_ms=100,
            peak_job_memory_bytes=4096,
            total_process_count=2,
        )
        intent = SimpleNamespace(
            review=mock.sentinel.review,
            resolution=selected,
            attempt=selected_attempt,
        )
        calls = []

        def remove_owned_tree(paths, attempt_id):
            calls.append(("remove", paths, attempt_id))

        def publish_terminal(*args, **kwargs):
            calls.append(("publish", args[3], args[4], kwargs["cleanup_proved"]))
            return {"outcome": "pass", "route": "runner"}

        with (
            mock.patch(
                "task_governance_tool.verification_runner_service._runner_paths",
                return_value=mock.sentinel.paths,
            ),
            mock.patch(
                "task_governance_tool.verification_runner_service.remove_attempt_tree",
                side_effect=remove_owned_tree,
            ),
            mock.patch(
                "task_governance_tool.verification_runner_service._publish_attempt_terminal",
                side_effect=publish_terminal,
            ),
        ):
            result = _cleanup_attempt_and_publish(
                mock.sentinel.target,
                mock.sentinel.authority,
                intent,
                passed,
            )

        self.assertEqual(
            calls,
            [
                (
                    "remove",
                    mock.sentinel.paths,
                    selected_attempt.verification_runner_attempt_id,
                ),
                ("publish", selected_attempt, passed, True),
            ],
        )
        self.assertEqual(
            result.verification_runner,
            {"outcome": "pass", "route": "runner"},
        )

    def test_pass_and_cleanup_failure_observations_use_closed_projection(self):
        selected = resolution()
        launched = _attempt_terminal_observation(
            selected,
            attempt(),
            launch_state="launched",
            outcome="pass",
            reason=None,
            route="runner",
            total_step_count=2,
            completed_step_count=2,
            failed_step_ordinal=None,
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:03Z",
            duration_ms=2100,
            cpu_time_ms=200,
            peak_job_memory_bytes=4096,
            total_process_count=2,
        )
        self.assertEqual(launched.complete_plan, 1)
        cleanup = _cleanup_failure_observation(
            selected,
            attempt(),
            launched,
            finished_at="2026-08-09T00:00:04Z",
        )
        self.assertEqual(
            (cleanup.route, cleanup.outcome, cleanup.reason, cleanup.complete_plan),
            ("blocked", "sandbox_cleanup_failed", "sandbox_cleanup_failed", 0),
        )
        self.assertEqual(
            (cleanup.cpu_time_ms, cleanup.peak_job_memory_bytes, cleanup.total_process_count),
            (None, None, None),
        )

    def test_unlaunched_cleanup_failure_uses_one_zero_duration_observation_time(self):
        selected = resolution()
        selected_attempt = attempt()
        prelaunch = _prelaunch_attempt_observation(
            selected,
            selected_attempt,
            reason="profile_collision",
            observed_at="2026-08-09T00:00:01Z",
        )
        uncertain = _attempt_terminal_observation(
            selected,
            selected_attempt,
            launch_state="launch_uncertain",
            outcome="controller_interrupted",
            reason="controller_interrupted",
            route="blocked",
            total_step_count=2,
            completed_step_count=0,
            failed_step_ordinal=None,
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:01Z",
            duration_ms=0,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
        )

        for provisional in (prelaunch, uncertain):
            with self.subTest(launch_state=provisional.launch_state):
                cleanup = _cleanup_failure_observation(
                    selected,
                    selected_attempt,
                    provisional,
                    finished_at="2026-08-09T00:00:03Z",
                )
                self.assertEqual(cleanup.started_at, cleanup.finished_at)
                self.assertEqual(cleanup.duration_ms, 0)

    def test_prelaunch_attempt_is_zero_resource_and_never_complete(self):
        selected = resolution()
        blocked = _prelaunch_attempt_observation(
            selected,
            attempt(),
            reason="profile_collision",
            observed_at="2026-08-09T00:00:02Z",
        )
        self.assertEqual(
            (
                blocked.route,
                blocked.launch_state,
                blocked.outcome,
                blocked.total_step_count,
                blocked.completed_step_count,
                blocked.failed_step_ordinal,
            ),
            ("blocked", "no_launch", "blocked_prelaunch", 2, 0, None),
        )
        self.assertEqual(
            blocked.sanitized_result_digest,
            verification_runner_observation_digest(
                {
                    "attempt_id": blocked.verification_runner_attempt_id,
                    "completed_step_count": 0,
                    "complete_plan": 0,
                    "cpu_time_ms": None,
                    "duration_ms": 0,
                    "failed_step_ordinal": None,
                    "finished_at": blocked.finished_at,
                    "gate_eligibility_version": 0,
                    "launch_state": "no_launch",
                    "outcome": "blocked_prelaunch",
                    "peak_job_memory_bytes": None,
                    "project_id": blocked.project_id,
                    "reason": "profile_collision",
                    "resolution_id": blocked.verification_runner_resolution_id,
                    "runner_implementation_digest": DIGEST,
                    "started_at": blocked.started_at,
                    "target_generation": 3,
                    "task_id": blocked.task_id,
                    "route": "blocked",
                    "total_process_count": None,
                    "total_step_count": 2,
                }
            ),
        )

    def test_process_no_launch_is_normalized_to_zero_duration_terminal(self):
        selected = resolution()
        selected_attempt = attempt()
        process_result = ProcessRunResult(
            outcome="blocked_prelaunch",
            reason="process_create_failed",
            launch_state="no_launch",
            failed_step_ordinal=None,
            duration_ms=2100,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
            steps=(),
        )

        observation = _process_terminal_observation(
            selected,
            selected_attempt,
            process_result,
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:03Z",
            basis_drifted=False,
        )

        self.assertEqual(observation.launch_state, "no_launch")
        self.assertEqual(observation.started_at, observation.finished_at)
        self.assertEqual(observation.duration_ms, 0)

    def test_between_step_controller_loss_is_persistable_uncertainty(self):
        selected = resolution()
        selected_attempt = attempt()
        process_result = ProcessRunResult(
            outcome="controller_interrupted",
            reason="controller_interrupted",
            launch_state="launched",
            failed_step_ordinal=None,
            duration_ms=2100,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
            steps=(
                StepProcessResult(
                    ordinal=1,
                    outcome="pass",
                    reason=None,
                    launch_state="launched",
                    accounting=JobAccounting(10_000, 4096, 1, 0),
                ),
            ),
        )

        observation = _process_terminal_observation(
            selected,
            selected_attempt,
            process_result,
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:03Z",
            basis_drifted=False,
        )

        self.assertEqual(
            (
                observation.route,
                observation.launch_state,
                observation.outcome,
                observation.reason,
            ),
            (
                "blocked",
                "launch_uncertain",
                "controller_interrupted",
                "controller_interrupted",
            ),
        )
        self.assertEqual(observation.completed_step_count, 1)
        self.assertIsNone(observation.failed_step_ordinal)
        self.assertEqual(observation.started_at, observation.finished_at)
        self.assertEqual(observation.duration_ms, 0)
        self.assertEqual(
            (
                observation.cpu_time_ms,
                observation.peak_job_memory_bytes,
                observation.total_process_count,
            ),
            (None, None, None),
        )
        self.assertTrue(_verification_runner_observation_matrix_valid(observation))

    def test_later_step_cleanup_failure_is_persistable_blocked_terminal(self):
        selected = resolution()
        selected_attempt = attempt()
        process_result = ProcessRunResult(
            outcome="sandbox_cleanup_failed",
            reason="sandbox_cleanup_failed",
            launch_state="launched",
            failed_step_ordinal=None,
            duration_ms=2100,
            cpu_time_ms=None,
            peak_job_memory_bytes=None,
            total_process_count=None,
            steps=(
                StepProcessResult(
                    ordinal=1,
                    outcome="pass",
                    reason=None,
                    launch_state="launched",
                    accounting=JobAccounting(10_000, 4096, 1, 0),
                ),
            ),
        )

        observation = _process_terminal_observation(
            selected,
            selected_attempt,
            process_result,
            started_at="2026-08-09T00:00:01Z",
            finished_at="2026-08-09T00:00:03Z",
            basis_drifted=False,
        )

        self.assertEqual(
            (
                observation.route,
                observation.launch_state,
                observation.outcome,
                observation.reason,
                observation.completed_step_count,
                observation.failed_step_ordinal,
            ),
            (
                "blocked",
                "launched",
                "sandbox_cleanup_failed",
                "sandbox_cleanup_failed",
                1,
                None,
            ),
        )
        self.assertEqual(
            (
                observation.cpu_time_ms,
                observation.peak_job_memory_bytes,
                observation.total_process_count,
            ),
            (None, None, None),
        )
        self.assertTrue(_verification_runner_observation_matrix_valid(observation))


if __name__ == "__main__":
    unittest.main()
