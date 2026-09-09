from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock

from tests.m14_test_support import (
    _copy_skill,
    file_snapshot,
    initialize_taskgov_internal,
    run_taskgov_internal,
)
from tests.runner_phase_diagnostics import trace_runner_prelaunch


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _verification_runner_win32 as runner_win32  # noqa: E402
from task_governance_tool import cli as cli_module  # noqa: E402
from task_governance_tool.cli_text import review_text  # noqa: E402
from task_governance_tool import _verification_runner_process_win32 as runner_process  # noqa: E402
from task_governance_tool import _verification_runner_process_posix as posix_process  # noqa: E402
from task_governance_tool import verification_runner_process as common_process  # noqa: E402
from task_governance_tool import verification_runner_service as service  # noqa: E402
from task_governance_tool import verification_runner_selection as selection  # noqa: E402
from task_governance_tool.artifact_manifest import (  # noqa: E402
    opaque_artifact_observation,
)
from task_governance_tool.reviews import ReviewEvidenceError, ReviewTargetResult  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    connect_initialized_readonly,
    resolve_database_target,
)
from task_governance_tool.verification_runner_repository import (  # noqa: E402
    read_verification_runner_generation_locked,
)
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_POLICY_DIGEST,
    RUNNER_POSIX_POLICY_DIGEST,
)
from task_governance_tool.verification_runner_git import (  # noqa: E402
    VerificationRunnerGitError,
    observe_staged_runner_target,
    preflight_runner_material,
)
from task_governance_tool.verification_runner_plan import (  # noqa: E402
    VerificationRunnerPlanResolution,
    VerificationRunnerPlanStep,
    decode_verification_runner_plan_steps,
)
from task_governance_tool.verification_runner_lifecycle import (  # noqa: E402
    inspect_runner_layout,
)
from task_governance_tool.verification_runner_process import (  # noqa: E402
    RunnerProcessError,
    RunnerCancelSignal,
    RunnerProcessRequestV1,
    RunnerProcessResultV1,
    RunnerProcessStepResultV1,
    RunnerProcessStepV1,
)
from task_governance_tool.verification_runner_runtime import (  # noqa: E402
    RunnerImplementationIdentity,
    VerificationRunnerRuntimeError,
)


FINGERPRINT_A = "sha256:" + "a" * 64
FINGERPRINT_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64
DIGEST_E = "sha256:" + "e" * 64


def passing_process_result(request) -> RunnerProcessResultV1:
    policy = getattr(request, "runner_policy_digest", RUNNER_POLICY_DIGEST)
    auxiliary = None if policy == RUNNER_POSIX_POLICY_DIGEST else 1
    step = RunnerProcessStepResultV1(
        ordinal=1,
        outcome="pass",
        reason=None,
        launch_state="launched",
        cpu_time_ms=1,
        peak_job_memory_bytes=auxiliary,
        total_process_count=auxiliary,
        runner_policy_digest=policy,
    )
    return RunnerProcessResultV1(
        version=RUNNER_CONTRACT_VERSION,
        attempt_id=request.attempt_id,
        outcome="pass",
        reason=None,
        launch_state="launched",
        failed_step_ordinal=None,
        duration_ms=1,
        cpu_time_ms=1,
        peak_job_memory_bytes=auxiliary,
        total_process_count=auxiliary,
        process_zero=True,
        handles_closed=True,
        raw_output_discarded=True,
        steps=(step,),
        runner_policy_digest=policy,
    )


def process_request(policy: str) -> RunnerProcessRequestV1:
    attempt_id = "tg_verification_runner_attempt_0123456789abcdef"
    attempt = PurePosixPath("/private") / attempt_id
    step = RunnerProcessStepV1(
        ordinal=1, step_id="focused", mode="script", entrypoint="focused.py",
        argv=(), cwd=".", shell=False, path_lookup=False, timeout_seconds=30,
        cpu_seconds=1, memory_mib=128, process_limit=2, output_byte_limit=1_048_576,
    )
    return RunnerProcessRequestV1(
        version=RUNNER_CONTRACT_VERSION, attempt_id=attempt_id,
        executable=PurePosixPath("/runtime/python3"),
        materialized_root=attempt / "target", scratch_root=attempt / "scratch",
        clean_environment=(), steps=(step,), cancel_signal=RunnerCancelSignal(),
        runner_policy_digest=policy,
    )


def git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        shell=False,
    )


def row_counts(db: Path) -> dict[str, int]:
    with closing(sqlite3.connect(db)) as connection:
        return {
            name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in (
                "verification_runner_resolutions",
                "verification_runner_attempts",
                "verification_runner_observations",
                "verification_runner_sandbox_events",
            )
        }


class RunnerServiceFixture:
    def __init__(self, root: Path) -> None:
        root = Path(root).resolve(strict=True)
        self.repo = root / "repo"
        self.db = root / "state" / "taskgov.sqlite"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "--quiet")
        git(self.repo, "config", "user.name", "TaskGov Test")
        git(self.repo, "config", "user.email", "taskgov@example.invalid")
        tracked = self.repo / "focused.py"
        tracked.write_text("print('base')\n", encoding="utf-8")
        git(self.repo, "add", "focused.py")
        git(self.repo, "commit", "--quiet", "-m", "base")
        tracked.write_text("print('staged')\n", encoding="utf-8")
        git(self.repo, "add", "focused.py")

        initialize_taskgov_internal(repo=self.repo, db=self.db)
        added = run_taskgov_internal(
            "task",
            "add",
            "--repo",
            str(self.repo),
            "--db",
            str(self.db),
            "--title",
            "Runner service fixture",
            "--verification",
            "python -m unittest tests.test_m242_runner_service",
            "--contract-scope",
            "Exercise one bounded Runner service generation.",
            "--contract-acceptance",
            "The service preserves the exact two-transaction boundary.",
            "--json",
            maintenance_enabled=False,
        )
        if added.returncode != 0:
            raise AssertionError(added.stdout or added.stderr)
        self.task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
        self.target = replace(
            resolve_database_target(
                repo=self.repo,
                db=self.db,
                script_path=SKILL_ROOT / "scripts" / "taskgov.py",
            ),
            skill_root=SKILL_ROOT,
        )

    def authority(self):
        with closing(connect_initialized_readonly(self.target)) as connection:
            return service.read_review_target_authority_basis(
                connection,
                self.target.project,
                self.task_id,
            )

    def prepared(self, authority=None):
        authority = authority or self.authority()
        observed = observe_staged_runner_target(self.repo)
        material = preflight_runner_material(self.repo, observed)
        step = VerificationRunnerPlanStep(
            ordinal=1,
            step_id="focused",
            mode="script",
            entrypoint="focused.py",
            argv=(),
            cwd=".",
            timeout_seconds=30,
            cpu_seconds=30,
            memory_mib=128,
            process_limit=2,
            output_byte_limit=1_048_576,
        )
        plan = VerificationRunnerPlanResolution(
            plan_state="runner",
            route="runner",
            reason=None,
            plan_blob_object_id=None,
            plan_raw_digest=DIGEST_C,
            plan_id="focused-plan",
            plan_version=1,
            plan_semantic_digest=DIGEST_D,
            selected_entry_digest=DIGEST_E,
            coverage="full",
            steps=(step,),
        )
        implementation = RunnerImplementationIdentity(
            implementation_version=RUNNER_IMPLEMENTATION_VERSION,
            implementation_digest=FINGERPRINT_A,
            manifest_version=1,
            package_name="task-governance-tool",
            package_version="test",
            core_files=(),
        )
        return service._PreparedRunner(
            authority=authority,
            target=observed,
            material=material,
            plan=plan,
            implementation=implementation,
        )

    def generation(self, generation: int):
        with closing(connect_initialized_readonly(self.target)) as connection:
            return read_verification_runner_generation_locked(
                connection,
                project_id=self.target.project.project_id,
                task_id=self.task_id,
                target_generation=generation,
            )


class VerificationRunnerServiceTests(unittest.TestCase):
    def test_fixture_canonicalizes_temporary_root_before_deriving_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            alias_component = temporary_root / "alias-component"
            alias_component.mkdir()

            fixture = RunnerServiceFixture(alias_component / "..")
            canonical_root = temporary_root.resolve(strict=True)

            self.assertEqual(fixture.repo.parent, canonical_root)
            self.assertEqual(fixture.repo, fixture.target.project.canonical_repo)
            self.assertEqual(fixture.db, fixture.target.db_path)

    def test_commit_revision_is_required_before_git_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            with mock.patch(
                "task_governance_tool.reviews.resolve_git_commit",
                side_effect=AssertionError("Git resolution must not run"),
            ) as resolve_commit, self.assertRaises(ReviewEvidenceError) as raised:
                service.set_review_target_with_optional_runner(
                    fixture.target, fixture.task_id, kind="git_commit"
                )

            error = raised.exception
            self.assertEqual(error.code, "invalid_review_evidence")
            self.assertEqual(error.field, "review_target_value")
            self.assertEqual(
                error.message,
                "--revision is required unless review target kind is git_snapshot",
            )
            resolve_commit.assert_not_called()
            self.assertEqual(sum(row_counts(fixture.db).values()), 0)

    def test_public_fallback_returns_receipt_route_and_runner_tables_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            result = run_taskgov_internal(
                "review",
                "target",
                "set",
                "--repo",
                str(fixture.repo),
                "--db",
                str(fixture.db),
                fixture.task_id,
                "--kind",
                "diff_fingerprint",
                "--revision",
                FINGERPRINT_A,
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(
                set(payload["data"]),
                {
                    "task",
                    "changed_fields",
                    "event",
                    "verification_route",
                    "blocking_code",
                },
            )
            self.assertEqual(payload["data"]["verification_route"], "receipt_required")
            self.assertIsNone(payload["data"]["blocking_code"])
            self.assertEqual(
                row_counts(fixture.db),
                {
                    "verification_runner_resolutions": 0,
                    "verification_runner_attempts": 0,
                    "verification_runner_observations": 0,
                    "verification_runner_sandbox_events": 0,
                },
            )

    def test_public_target_without_verification_returns_not_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            added = run_taskgov_internal(
                "task",
                "add",
                "--repo",
                str(fixture.repo),
                "--db",
                str(fixture.db),
                "--title",
                "No verification target",
                "--contract-scope",
                "Capture one target without a verification criterion.",
                "--contract-acceptance",
                "The target-set response selects the not-required branch.",
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(added.returncode, 0, added.stdout)
            task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
            result = run_taskgov_internal(
                "review",
                "target",
                "set",
                "--repo",
                str(fixture.repo),
                "--db",
                str(fixture.db),
                task_id,
                "--kind",
                "diff_fingerprint",
                "--revision",
                FINGERPRINT_A,
                "--json",
                maintenance_enabled=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)["data"]
            self.assertEqual(data["verification_route"], "not_required")
            self.assertIsNone(data["blocking_code"])

    def test_plan_fallback_matrix_keeps_runner_tables_empty(self):
        fallback_reasons = {
            "absent": "plan_absent",
            "disabled": "trusted_local_disabled",
            "no_match": "plan_entry_absent",
        }
        for state, reason in fallback_reasons.items():
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                source_absent = state == "absent"
                fallback = VerificationRunnerPlanResolution(
                    plan_state=state,
                    route="m21_fallback",
                    reason=reason,
                    plan_blob_object_id=None,
                    plan_raw_digest=None if source_absent else DIGEST_C,
                    plan_id=None if source_absent else "focused-plan",
                    plan_version=None if source_absent else 1,
                    plan_semantic_digest=None if source_absent else DIGEST_D,
                    selected_entry_digest=None,
                    coverage="not_applicable",
                    steps=(),
                )
                with mock.patch.object(
                    service,
                    "capture_verification_runner_plan",
                    return_value=None if source_absent else object(),
                ), mock.patch.object(
                    service,
                    "resolve_verification_runner_plan",
                    return_value=fallback,
                ):
                    result = service.set_review_target_with_optional_runner(
                        fixture.target,
                        fixture.task_id,
                        kind="git_snapshot",
                    )
                self.assertEqual(result.task["review_target_generation"], 1)
                self.assertEqual(sum(row_counts(fixture.db).values()), 0)

    def test_target_unsupported_falls_back_with_zero_runner_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            unsupported = VerificationRunnerGitError(
                code="target_unsupported",
                message="verification runner target is unsupported",
            )
            with mock.patch.object(
                service,
                "capture_verification_runner_plan",
                return_value=object(),
            ), mock.patch.object(
                service,
                "resolve_verification_runner_plan",
                return_value=prepared.plan,
            ), mock.patch.object(
                service,
                "capture_runner_implementation",
                return_value=prepared.implementation,
            ), mock.patch.object(
                service,
                "observe_staged_runner_target",
                side_effect=unsupported,
            ):
                result = service.set_review_target_with_optional_runner(
                    fixture.target,
                    fixture.task_id,
                    kind="git_snapshot",
                )
            self.assertEqual(result.task["review_target_generation"], 1)
            self.assertEqual(sum(row_counts(fixture.db).values()), 0)

    def test_cli_target_set_has_no_outer_connection_and_error_skips_maintenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            review = SimpleNamespace(
                task={
                    "task_id": fixture.task_id,
                    "review_target_kind": "diff_fingerprint",
                    "review_target_generation": 1,
                    "review_target_base_revision": "",
                },
                changed_fields=["review_target"],
                event={"event_type": "review_target_set", "summary": "set"},
                verification_route="receipt_required",
                blocking_code=None,
            )
            with mock.patch.object(
                cli_module,
                "connect_initialized",
                side_effect=AssertionError("CLI opened an outer transaction"),
            ), mock.patch.object(
                cli_module,
                "set_review_target_with_optional_runner",
                return_value=review,
            ):
                success = run_taskgov_internal(
                    "review", "target", "set", "--repo", str(fixture.repo),
                    "--db", str(fixture.db), fixture.task_id, "--kind",
                    "diff_fingerprint", "--revision", FINGERPRINT_A, "--json",
                    maintenance_enabled=False,
                )
            self.assertEqual(success.returncode, 0, success.stdout)
            success_data = json.loads(success.stdout)["data"]
            self.assertEqual(
                set(success_data),
                {
                    "task",
                    "changed_fields",
                    "event",
                    "verification_route",
                    "blocking_code",
                },
            )
            self.assertEqual(success_data["verification_route"], "receipt_required")
            self.assertIsNone(success_data["blocking_code"])
            self.assertEqual(
                review_text("review.target.set", success_data),
                review_text(
                    "review.target.set",
                    {
                        "task": review.task,
                        "changed_fields": review.changed_fields,
                        "event": review.event,
                    },
                ),
            )

            with mock.patch.object(
                cli_module,
                "set_review_target_with_optional_runner",
                side_effect=service.VerificationRunnerServiceError(
                    "runner_state_invalid",
                    "verification runner state could not be changed safely",
                ),
            ), mock.patch.object(cli_module, "run_post_commit_maintenance") as maintenance:
                failed = run_taskgov_internal(
                    "review", "target", "set", "--repo", str(fixture.repo),
                    "--db", str(fixture.db), fixture.task_id, "--kind",
                    "diff_fingerprint", "--revision", FINGERPRINT_A, "--json",
                    maintenance_enabled=True,
                )
            self.assertEqual(failed.returncode, 2, failed.stdout)
            self.assertEqual(
                json.loads(failed.stdout)["errors"][0]["code"],
                "runner_state_invalid",
            )
            maintenance.assert_not_called()

    def test_lock_and_reconciliation_precede_atomic_t1(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            events: list[str] = []
            review = SimpleNamespace(
                task={},
                changed_fields=[],
                event={},
                verification_route="runner_pass",
                blocking_code=None,
            )
            intent = SimpleNamespace(review=review)
            prepare_count = 0

            def prepare(*_args, **_kwargs):
                nonlocal prepare_count
                prepare_count += 1
                events.append(
                    "prepare_initial" if prepare_count == 1 else "prepare_refresh"
                )
                return prepared

            @contextmanager
            def observed_lock(_paths):
                events.append("lock_enter")
                yield object()
                events.append("lock_exit")

            def reconcile(*_args):
                events.append("reconcile")

            def t1(*_args):
                events.append("t1")
                return intent

            def run(*_args, **_kwargs):
                events.append("run")
                return review

            with mock.patch.object(service, "_prepare_runner", side_effect=prepare), mock.patch.object(
                service, "zero_wait_runner_lock", side_effect=observed_lock
            ), mock.patch.object(
                service, "_deny_unresolved_attempt", side_effect=reconcile
            ), mock.patch.object(
                service, "_persist_launch_intent", side_effect=t1
            ), mock.patch.object(service, "_run_intent_under_lock", side_effect=run):
                result = service.set_review_target_with_optional_runner(
                    fixture.target,
                    fixture.task_id,
                    kind="git_snapshot",
                )
            self.assertIs(result, review)
            self.assertEqual(
                events,
                [
                    "prepare_initial",
                    "lock_enter",
                    "reconcile",
                    "prepare_refresh",
                    "t1",
                    "run",
                    "lock_exit",
                ],
            )

    def test_real_runner_busy_contention_writes_nothing_and_does_not_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            with mock.patch.object(
                service,
                "_prepare_runner",
                return_value=prepared,
            ), mock.patch.object(
                service,
                "_persist_launch_intent",
            ) as persist_intent, mock.patch.object(
                service,
                "run_process_request",
            ) as process:
                with service.zero_wait_runner_lock(paths):
                    with self.assertRaises(
                        service.VerificationRunnerServiceError
                    ) as raised:
                        service.set_review_target_with_optional_runner(
                            fixture.target,
                            fixture.task_id,
                            kind="git_snapshot",
                        )
            self.assertEqual(raised.exception.code, "runner_busy")
            persist_intent.assert_not_called()
            process.assert_not_called()
            self.assertEqual(sum(row_counts(fixture.db).values()), 0)
            self.assertEqual(fixture.authority().task["review_target_generation"], 0)

    def test_locked_pre_t1_refresh_falls_back_or_blocks_without_runner_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            with mock.patch.object(
                service,
                "_prepare_runner",
                side_effect=(prepared, None),
            ), mock.patch.object(
                service,
                "_persist_launch_intent",
                side_effect=AssertionError("fallback reached T1 intent"),
            ):
                result = service.set_review_target_with_optional_runner(
                    fixture.target,
                    fixture.task_id,
                    kind="git_snapshot",
                )
            self.assertEqual(result.task["review_target_generation"], 1)
            self.assertEqual(sum(row_counts(fixture.db).values()), 0)

        for drift_kind in ("target", "material", "implementation"):
            with self.subTest(drift_kind=drift_kind), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                prepared = fixture.prepared()
                if drift_kind == "target":
                    (fixture.repo / "focused.py").write_text(
                        "print('changed again')\n",
                        encoding="utf-8",
                    )
                    git(fixture.repo, "add", "focused.py")
                    refreshed = fixture.prepared(prepared.authority)
                    expected_code = "target_stale"
                elif drift_kind == "material":
                    object_id, object_size = prepared.material.object_sizes[0]
                    refreshed = replace(
                        prepared,
                        material=replace(
                            prepared.material,
                            object_sizes=((object_id, object_size + 1),),
                            total_bytes=prepared.material.total_bytes + 1,
                        ),
                    )
                    expected_code = "target_stale"
                else:
                    refreshed = replace(
                        prepared,
                        implementation=replace(
                            prepared.implementation,
                            implementation_digest=FINGERPRINT_B,
                        ),
                    )
                    expected_code = "runner_state_invalid"
                with mock.patch.object(
                    service,
                    "_prepare_runner",
                    side_effect=(prepared, refreshed),
                ):
                    with self.assertRaises(
                        service.VerificationRunnerServiceError
                    ) as raised:
                        service.set_review_target_with_optional_runner(
                            fixture.target,
                            fixture.task_id,
                            kind="git_snapshot",
                        )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(sum(row_counts(fixture.db).values()), 0)
                self.assertEqual(
                    fixture.authority().task["review_target_generation"],
                    0,
                )

    def test_target_and_intent_t1_roll_back_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            with mock.patch.object(
                service,
                "insert_verification_runner_resolution_locked",
                side_effect=StorageError("internal_error", "forced T1 rejection"),
            ):
                with self.assertRaises(StorageError):
                    service._persist_launch_intent(fixture.target, prepared)

            with closing(sqlite3.connect(fixture.db)) as connection:
                generation, marker = connection.execute(
                    "SELECT review_target_generation, "
                    "review_target_runner_basis_version FROM tasks "
                    "WHERE task_id = ?",
                    (fixture.task_id,),
                ).fetchone()
            self.assertEqual(generation, 0)
            self.assertEqual(marker, 0)
            self.assertEqual(sum(row_counts(fixture.db).values()), 0)

    def test_prelaunch_terminal_uses_explicit_t2_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                service.create_attempt_directories(
                    paths,
                    intent.attempt.verification_runner_attempt_id,
                )
                with mock.patch.object(
                    service,
                    "_physical_basis_matches",
                    return_value=True,
                ):
                    result = service._complete_prelaunch(
                        fixture.target,
                        paths,
                        prepared,
                        intent,
                        reason="runtime_unavailable",
                    )
            self.assertEqual(result.task["review_target_generation"], 1)
            self.assertEqual(result.verification_route, "receipt_required")
            self.assertIsNone(result.blocking_code)
            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "terminal")
            self.assertEqual(generation["resolution"].gate_eligibility_version, 1)
            self.assertEqual(generation["attempt"].gate_eligibility_version, 1)
            self.assertEqual(generation["observation"].gate_eligibility_version, 1)
            self.assertEqual(generation["observation"].route, "m21_fallback")
            self.assertEqual(generation["observation"].launch_state, "no_launch")
            self.assertEqual(generation["observation"].outcome, "blocked_prelaunch")
            self.assertEqual(generation["observation"].reason, "runtime_unavailable")
            with closing(sqlite3.connect(fixture.db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM evidence_references "
                        "WHERE source_kind = 'runner_observation'"
                    ).fetchone()[0],
                    1,
                )

    def test_runtime_observation_failure_terminalizes_without_a_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            failure = VerificationRunnerRuntimeError(
                "runtime_unavailable",
                "the fixed package runtime could not be verified",
            )
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                with mock.patch.object(
                    service, "materialize_runner_target", return_value=None,
                ), mock.patch.object(
                    service, "_basis_is_current", return_value=True,
                ), mock.patch.object(
                    service, "_physical_basis_matches", return_value=True,
                ), mock.patch.object(
                    service, "observe_fixed_package_runtime", side_effect=failure,
                ) as observe, mock.patch.object(
                    service, "run_process_request",
                ) as process:
                    result = service._run_intent_under_lock(
                        fixture.target, paths, prepared, intent,
                        cancel_requested=lambda: False,
                    )
            observe.assert_called_once()
            self.assertEqual(observe.call_args.args[0].name, "target")
            self.assertEqual(observe.call_args.args[1].name, "scratch")
            process.assert_not_called()
            self.assertEqual(result.verification_route, "receipt_required")
            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "terminal")
            self.assertEqual(generation["observation"].reason, "runtime_unavailable")
            self.assertEqual(generation["observation"].launch_state, "no_launch")

    def test_interrupted_runtime_observation_remains_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                with mock.patch.object(
                    service,
                    "materialize_runner_target",
                    return_value=None,
                ), mock.patch.object(
                    service,
                    "_basis_is_current",
                    return_value=True,
                ), mock.patch.object(
                    service,
                    "observe_fixed_package_runtime",
                    side_effect=KeyboardInterrupt("runtime observation interrupted"),
                ):
                    with self.assertRaises(
                        service.VerificationRunnerServiceError
                    ) as raised:
                        service._run_intent_under_lock(
                            fixture.target,
                            paths,
                            prepared,
                            intent,
                            cancel_requested=lambda: False,
                        )
            self.assertEqual(raised.exception.code, "runner_state_invalid")
            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "pending")
            self.assertIsNone(generation["observation"])
            self.assertIsNone(generation["cleanup_event"])

    def test_post_bind_runtime_and_unknown_failures_remain_pending(self):
        failures = (
            VerificationRunnerRuntimeError(
                "runtime_unavailable",
                "the fixed package runtime could not be verified",
            ),
            RuntimeError("unknown request setup failure"),
            KeyboardInterrupt("request setup interrupted"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                prepared = fixture.prepared()
                paths = service._runner_paths(fixture.target)
                with service.zero_wait_runner_lock(paths):
                    intent = service._persist_launch_intent(fixture.target, prepared)
                    with mock.patch.object(
                        service,
                        "materialize_runner_target",
                        return_value=None,
                    ), mock.patch.object(
                        service,
                        "_basis_is_current",
                        return_value=True,
                    ), mock.patch.object(
                        service,
                        "observe_fixed_package_runtime",
                        return_value=Path(sys.executable).resolve(),
                    ), mock.patch.object(
                        service,
                        "build_clean_environment",
                        side_effect=failure,
                    ), mock.patch.object(
                        service,
                        "run_process_request",
                    ) as process:
                        with self.assertRaises(
                            service.VerificationRunnerServiceError
                        ) as raised:
                            service._run_intent_under_lock(
                                fixture.target,
                                paths,
                                prepared,
                                intent,
                                cancel_requested=lambda: False,
                            )
                self.assertEqual(raised.exception.code, "runner_state_invalid")
                process.assert_not_called()
                generation = fixture.generation(1)
                self.assertEqual(generation["state"], "pending")
                self.assertIsNone(generation["observation"])
                self.assertIsNone(generation["cleanup_event"])

    def test_process_step_setup_failure_terminalizes_before_runtime_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                with mock.patch.object(
                    service,
                    "materialize_runner_target",
                    return_value=None,
                ), mock.patch.object(
                    service,
                    "_basis_is_current",
                    return_value=True,
                ), mock.patch.object(
                    service,
                    "_physical_basis_matches",
                    return_value=True,
                ), mock.patch.object(
                    service,
                    "_process_steps",
                    side_effect=RunnerProcessError("process_setup_failed"),
                ), mock.patch.object(
                    service,
                    "observe_fixed_package_runtime",
                ) as observe:
                    service._run_intent_under_lock(
                        fixture.target,
                        paths,
                        prepared,
                        intent,
                        cancel_requested=lambda: False,
                    )
            observe.assert_not_called()
            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "terminal")
            self.assertEqual(
                generation["observation"].reason,
                "process_setup_failed",
            )

    def test_bound_process_setup_failure_terminalizes_without_a_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                with mock.patch.object(
                    service, "materialize_runner_target", return_value=None,
                ), mock.patch.object(
                    service, "_basis_is_current", return_value=True,
                ), mock.patch.object(
                    service, "_physical_basis_matches", return_value=True,
                ), mock.patch.object(
                    service, "observe_fixed_package_runtime",
                    return_value=Path(sys.executable).resolve(),
                ) as observe, mock.patch.object(
                    service, "build_clean_environment",
                    side_effect=RunnerProcessError("process_setup_failed"),
                ), mock.patch.object(
                    service, "run_process_request",
                ) as process:
                    service._run_intent_under_lock(
                        fixture.target, paths, prepared, intent,
                        cancel_requested=lambda: False,
                    )
            observe.assert_called_once()
            process.assert_not_called()
            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "terminal")
            self.assertEqual(generation["observation"].reason, "process_setup_failed")

    def test_unknown_pre_adapter_exceptions_remain_pending(self):
        for failure_type in (KeyboardInterrupt, SystemExit, RuntimeError):
            with self.subTest(failure_type=failure_type.__name__), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                prepared = fixture.prepared()
                paths = service._runner_paths(fixture.target)

                def fail_callback():
                    raise failure_type("unknown pre-adapter failure")

                with service.zero_wait_runner_lock(paths):
                    intent = service._persist_launch_intent(fixture.target, prepared)
                    with mock.patch.object(
                        service,
                        "materialize_runner_target",
                        return_value=None,
                    ), mock.patch.object(
                        service,
                        "_basis_is_current",
                        return_value=True,
                    ), mock.patch.object(
                        service,
                        "observe_fixed_package_runtime",
                    ) as observe:
                        with self.assertRaises(
                            service.VerificationRunnerServiceError
                        ) as raised:
                            service._run_intent_under_lock(
                                fixture.target,
                                paths,
                                prepared,
                                intent,
                                cancel_requested=fail_callback,
                            )
                self.assertEqual(raised.exception.code, "runner_state_invalid")
                observe.assert_not_called()
                generation = fixture.generation(1)
                self.assertEqual(generation["state"], "pending")
                self.assertIsNone(generation["observation"])
                self.assertIsNone(generation["cleanup_event"])

    def test_basis_storage_error_keeps_stable_code_and_does_not_append_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            failure = StorageError(
                "evidence_ledger_inconsistent",
                "stable storage failure",
            )
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                with mock.patch.object(
                    service,
                    "materialize_runner_target",
                    return_value=None,
                ), mock.patch.object(
                    service,
                    "_current_basis_matches",
                    side_effect=failure,
                ), mock.patch.object(
                    service,
                    "_physical_basis_matches",
                ) as physical_basis, mock.patch.object(
                    service,
                    "observe_fixed_package_runtime",
                ) as observe:
                    with self.assertRaises(StorageError) as raised:
                        service._run_intent_under_lock(
                            fixture.target,
                            paths,
                            prepared,
                            intent,
                            cancel_requested=lambda: False,
                        )
            self.assertIs(raised.exception, failure)
            self.assertEqual(
                raised.exception.code,
                "evidence_ledger_inconsistent",
            )
            physical_basis.assert_not_called()
            observe.assert_not_called()
            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "pending")
            self.assertIsNone(generation["observation"])
            self.assertIsNone(generation["cleanup_event"])

    def test_post_t1_boundary_sanitizes_cleanup_and_terminal_faults(self):
        seams = (
            "cleanup_attempt_tree",
            "_physical_basis_matches",
            "_persist_cleanup_only",
            "_persist_terminal",
        )
        failure_types = (RuntimeError, KeyboardInterrupt, SystemExit)
        for seam in seams:
            for failure_type in failure_types:
                with self.subTest(
                    seam=seam,
                    failure_type=failure_type.__name__,
                ), tempfile.TemporaryDirectory() as temporary:
                    fixture = RunnerServiceFixture(Path(temporary))
                    prepared = fixture.prepared()
                    failure = failure_type(
                        r"private failure at C:\sensitive\runner-state"
                    )
                    basis_results = (
                        (True, False)
                        if seam == "_persist_cleanup_only"
                        else (True, True)
                    )
                    patchers = [
                        mock.patch.object(
                            service,
                            "_prepare_runner",
                            side_effect=(prepared, prepared),
                        ),
                        mock.patch.object(
                            service,
                            "materialize_runner_target",
                            return_value=None,
                        ),
                        mock.patch.object(
                            service,
                            "_basis_is_current",
                            side_effect=basis_results,
                        ),
                        mock.patch.object(
                            service,
                            "observe_fixed_package_runtime",
                            return_value=Path(sys.executable).resolve(),
                        ),
                        mock.patch.object(
                            service,
                            "run_process_request",
                            side_effect=passing_process_result,
                        ),
                    ]
                    if seam == "cleanup_attempt_tree":
                        patchers.append(
                            mock.patch.object(
                                service,
                                "cleanup_attempt_tree",
                                side_effect=failure,
                            )
                        )
                    if seam == "_physical_basis_matches":
                        patchers.append(
                            mock.patch.object(
                                service,
                                "_physical_basis_matches",
                                side_effect=failure,
                            )
                        )
                    else:
                        patchers.append(
                            mock.patch.object(
                                service,
                                "_physical_basis_matches",
                                return_value=True,
                            )
                        )
                    if seam == "_persist_cleanup_only":
                        patchers.append(
                            mock.patch.object(
                                service,
                                "_persist_cleanup_only",
                                side_effect=failure,
                            )
                        )
                    if seam == "_persist_terminal":
                        patchers.append(
                            mock.patch.object(
                                service,
                                "_persist_terminal",
                                side_effect=failure,
                            )
                        )

                    with ExitStack() as stack:
                        for patcher in patchers:
                            stack.enter_context(patcher)
                        with self.assertRaises(
                            service.VerificationRunnerServiceError
                        ) as raised:
                            service.set_review_target_with_optional_runner(
                                fixture.target,
                                fixture.task_id,
                                kind="git_snapshot",
                            )

                    self.assertEqual(
                        raised.exception.code,
                        "runner_state_invalid",
                    )
                    self.assertEqual(
                        str(raised.exception),
                        service.RUNNER_FAILURE_MESSAGE,
                    )
                    self.assertNotIn("sensitive", str(raised.exception))
                    generation = fixture.generation(1)
                    self.assertEqual(generation["state"], "pending")
                    self.assertIsNone(generation["observation"])
                    self.assertIsNone(generation["cleanup_event"])

    def test_post_t1_boundary_preserves_storage_error_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            review = ReviewTargetResult(task={}, changed_fields=[], event={})
            intent = SimpleNamespace(review=review)
            failure = StorageError(
                "evidence_ledger_inconsistent",
                "stable storage failure",
            )

            @contextmanager
            def observed_lock(_paths):
                yield object()

            with mock.patch.object(
                service,
                "_prepare_runner",
                side_effect=(prepared, prepared),
            ), mock.patch.object(
                service,
                "zero_wait_runner_lock",
                side_effect=observed_lock,
            ), mock.patch.object(
                service,
                "_deny_unresolved_attempt",
                return_value=None,
            ), mock.patch.object(
                service,
                "_persist_launch_intent",
                return_value=intent,
            ), mock.patch.object(
                service,
                "_run_intent_under_lock",
                side_effect=failure,
            ):
                with self.assertRaises(StorageError) as raised:
                    service.set_review_target_with_optional_runner(
                        fixture.target,
                        fixture.task_id,
                        kind="git_snapshot",
                    )

            self.assertIs(raised.exception, failure)
            self.assertEqual(
                raised.exception.code,
                "evidence_ledger_inconsistent",
            )

    def test_restart_boundary_sanitizes_cleanup_and_persistence_faults(self):
        seams = ("cleanup_attempt_tree", "_persist_cleanup_only")
        failure_types = (RuntimeError, KeyboardInterrupt, SystemExit)
        for seam in seams:
            for failure_type in failure_types:
                with self.subTest(
                    seam=seam,
                    failure_type=failure_type.__name__,
                ), tempfile.TemporaryDirectory() as temporary:
                    fixture = RunnerServiceFixture(Path(temporary))
                    prepared = fixture.prepared()
                    paths = service._runner_paths(fixture.target)
                    with service.zero_wait_runner_lock(paths):
                        intent = service._persist_launch_intent(
                            fixture.target,
                            prepared,
                        )
                        service.create_attempt_directories(
                            paths,
                            intent.attempt.verification_runner_attempt_id,
                        )
                        service.create_scratch_directories(
                            paths,
                            intent.attempt.verification_runner_attempt_id,
                        )

                    attempt_root = (
                        paths.attempts
                        / intent.attempt.verification_runner_attempt_id
                    )
                    failure = failure_type(
                        r"private restart failure at C:\sensitive\runner-state"
                    )
                    with mock.patch.object(
                        service,
                        "_prepare_runner",
                        return_value=prepared,
                    ), mock.patch.object(
                        service,
                        seam,
                        side_effect=failure,
                    ) as failed_seam, mock.patch.object(
                        service,
                        "_persist_launch_intent",
                    ) as new_intent:
                        with self.assertRaises(
                            service.VerificationRunnerServiceError
                        ) as raised:
                            service.set_review_target_with_optional_runner(
                                fixture.target,
                                fixture.task_id,
                                kind="git_snapshot",
                            )

                    self.assertEqual(
                        raised.exception.code,
                        "runner_state_invalid",
                    )
                    self.assertEqual(
                        str(raised.exception),
                        service.RUNNER_FAILURE_MESSAGE,
                    )
                    self.assertNotIn("sensitive", str(raised.exception))
                    failed_seam.assert_called_once()
                    new_intent.assert_not_called()
                    generation = fixture.generation(1)
                    self.assertEqual(generation["state"], "pending")
                    self.assertIsNone(generation["observation"])
                    self.assertIsNone(generation["cleanup_event"])
                    self.assertEqual(
                        attempt_root.exists(),
                        seam == "cleanup_attempt_tree",
                    )

    def test_restart_boundary_preserves_storage_error_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                service.create_attempt_directories(
                    paths,
                    intent.attempt.verification_runner_attempt_id,
                )
                service.create_scratch_directories(
                    paths,
                    intent.attempt.verification_runner_attempt_id,
                )
            failure = StorageError(
                "evidence_ledger_inconsistent",
                "stable restart storage failure",
            )

            with mock.patch.object(
                service,
                "_prepare_runner",
                return_value=prepared,
            ), mock.patch.object(
                service,
                "_persist_cleanup_only",
                side_effect=failure,
            ), mock.patch.object(
                service,
                "_persist_launch_intent",
            ) as new_intent:
                with self.assertRaises(StorageError) as raised:
                    service.set_review_target_with_optional_runner(
                        fixture.target,
                        fixture.task_id,
                        kind="git_snapshot",
                    )

            self.assertIs(raised.exception, failure)
            self.assertEqual(
                raised.exception.code,
                "evidence_ledger_inconsistent",
            )
            new_intent.assert_not_called()
            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "pending")
            self.assertIsNone(generation["observation"])
            self.assertIsNone(generation["cleanup_event"])

    def test_post_t1_physical_basis_includes_fresh_material_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            with mock.patch.object(
                service,
                "capture_verification_runner_plan",
                return_value=object(),
            ), mock.patch.object(
                service,
                "resolve_verification_runner_plan",
                return_value=prepared.plan,
            ), mock.patch.object(
                service,
                "capture_runner_implementation",
                return_value=prepared.implementation,
            ), mock.patch.object(
                service,
                "observe_staged_runner_target",
                return_value=prepared.target,
            ), mock.patch.object(
                service,
                "preflight_runner_material",
                return_value=object(),
            ) as preflight:
                self.assertFalse(
                    service._physical_basis_matches(fixture.target, prepared)
                )
            preflight.assert_called_once_with(fixture.repo, prepared.target)

    def test_v2_windows_limits_are_copied_to_process_steps(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            physical_step = prepared.plan.steps[0].physical_value(plan_version=2)
            for limits, expected in (
                ({"memory_mib": 256, "process_limit": 4}, (256, 4)),
                (None, (None, None)),
            ):
                with self.subTest(windows_limits=limits):
                    steps = decode_verification_runner_plan_steps(
                        [dict(physical_step, windows_limits=limits)],
                        plan_version=2,
                    )
                    plan = replace(prepared.plan, plan_version=2, steps=steps)
                    process_steps = service._process_steps(plan)
                    self.assertEqual(len(process_steps), 1)
                    converted = process_steps[0]
                    self.assertEqual(
                        (converted.memory_mib, converted.process_limit), expected,
                    )
                    self.assertEqual(converted.ordinal, steps[0].ordinal)
                    self.assertEqual(converted.argv, steps[0].argv)
                    self.assertEqual(
                        converted.timeout_seconds, steps[0].timeout_seconds,
                    )
                    self.assertEqual(converted.cpu_seconds, steps[0].cpu_seconds)
                    self.assertEqual(
                        converted.output_byte_limit, steps[0].output_byte_limit,
                    )
                    self.assertFalse(converted.shell)
                    self.assertFalse(converted.path_lookup)

    @unittest.skipUnless(sys.platform == "win32", "Windows no-launch admission")
    def test_v2_missing_windows_limits_terminalize_as_manual_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            physical_step = prepared.plan.steps[0].physical_value(plan_version=2)
            steps = decode_verification_runner_plan_steps(
                [dict(physical_step, windows_limits=None)], plan_version=2,
            )
            prepared = replace(
                prepared,
                plan=replace(prepared.plan, plan_version=2, steps=steps),
            )
            paths = service._runner_paths(fixture.target)
            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                with mock.patch.object(
                    service, "materialize_runner_target", return_value=None,
                ), mock.patch.object(
                    service, "_basis_is_current", return_value=True,
                ), mock.patch.object(
                    service, "_physical_basis_matches", return_value=True,
                ), mock.patch.object(
                    service,
                    "observe_fixed_package_runtime",
                    return_value=Path(sys.executable).resolve(),
                ), mock.patch.object(
                    runner_process,
                    "run_process_request",
                    wraps=runner_process.run_process_request,
                ) as process, mock.patch.object(
                    runner_process,
                    "_admit_request",
                    side_effect=AssertionError("native admission must not run"),
                ) as admission:
                    result = service._run_intent_under_lock(
                        fixture.target,
                        paths,
                        prepared,
                        intent,
                        cancel_requested=lambda: False,
                    )
                process.assert_called_once()
                admission.assert_not_called()
                request_step = process.call_args.args[0].steps[0]
                self.assertIsNone(request_step.memory_mib)
                self.assertIsNone(request_step.process_limit)
                self.assertEqual(result.verification_route, "receipt_required")
                self.assertIsNone(result.blocking_code)

            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "terminal")
            self.assertEqual(generation["resolution"].plan_version, 2)
            observation = generation["observation"]
            self.assertEqual(
                (
                    observation.route,
                    observation.launch_state,
                    observation.outcome,
                    observation.reason,
                    observation.complete_plan,
                    observation.completed_step_count,
                ),
                ("m21_fallback", "no_launch", "blocked_prelaunch",
                 "process_setup_failed", 0, 0),
            )
            self.assertEqual(
                generation["cleanup_event"].terminal_observation_id,
                observation.verification_runner_observation_id,
            )
            inventory = inspect_runner_layout(paths)
            self.assertEqual(inventory.attempt_ids, ())
            self.assertEqual(inventory.quarantine_ids, ())

    def test_launched_pass_maps_to_one_runner_terminal_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            paths = service._runner_paths(fixture.target)

            with service.zero_wait_runner_lock(paths):
                intent = service._persist_launch_intent(fixture.target, prepared)
                with mock.patch.object(
                    service,
                    "materialize_runner_target",
                    return_value=None,
                ), mock.patch.object(
                    service,
                    "_basis_is_current",
                    return_value=True,
                ), mock.patch.object(
                    service,
                    "_physical_basis_matches",
                    return_value=True,
                ) as physical_basis, mock.patch.object(
                    service,
                    "observe_fixed_package_runtime",
                    return_value=Path(sys.executable).resolve(),
                ) as observe, mock.patch.object(
                    service,
                    "run_process_request",
                    side_effect=passing_process_result,
                ) as process:
                    result = service._run_intent_under_lock(
                        fixture.target,
                        paths,
                        prepared,
                        intent,
                        cancel_requested=lambda: False,
                    )
                self.assertEqual(result.task["review_target_generation"], 1)
                self.assertEqual(result.verification_route, "runner_pass")
                self.assertIsNone(result.blocking_code)
                physical_basis.assert_called_once_with(fixture.target, prepared)
                observe.assert_called_once()
                process.assert_called_once()
                self.assertEqual(
                    process.call_args.args[0].executable, observe.return_value,
                )

            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "terminal")
            self.assertEqual(generation["observation"].route, "runner")
            self.assertEqual(generation["observation"].launch_state, "launched")
            self.assertEqual(generation["observation"].outcome, "pass")
            self.assertIsNone(generation["observation"].reason)
            self.assertEqual(generation["observation"].complete_plan, 1)
            self.assertIsNotNone(generation["cleanup_event"].terminal_observation_id)

    @unittest.skipUnless(os.name == "nt", "integrated Windows shadow Runner")
    def test_real_windows_shadow_runner_integrates_plan_git_process_lifecycle_and_qualifying_evidence(
        self,
    ):
        raw_output_secret = "TG_M242D_RAW_OUTPUT_MUST_NOT_PERSIST_7f20c1"
        credential_name = "TG_M242D_PARENT_CREDENTIAL"
        credential_value = "TG_M242D_PARENT_CREDENTIAL_VALUE_91d5a4"
        committed_source = "print('staged')\n"

        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            skill_parent = fixture.repo / ".agents" / "skills"
            skill_parent.mkdir(parents=True)
            skill_root = _copy_skill(skill_parent)
            fixture.target = replace(fixture.target, skill_root=skill_root)

            (fixture.repo / ".gitignore").write_text(
                "/.agents/skills/task-governance-tool/config/\n"
                "/.agents/skills/task-governance-tool/state/\n",
                encoding="utf-8",
            )
            checks = fixture.repo / "checks"
            checks.mkdir()
            (checks / "run.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                f"raw_output_secret = {raw_output_secret!r}\n"
                "root = Path.cwd()\n"
                "valid = (\n"
                f"    (root / 'focused.py').read_text(encoding='utf-8') == {committed_source!r}\n"
                "    and not (root / 'ambient-untracked.txt').exists()\n"
                "    and not (root / '.agents' / 'skills' / 'task-governance-tool' / "
                "'config' / 'verification-runner.json').exists()\n"
                f"    and {credential_name!r} not in os.environ\n"
                ")\n"
                "print(raw_output_secret)\n"
                "raise SystemExit(0 if valid else 9)\n",
                encoding="utf-8",
            )

            authority = fixture.authority()
            plan_path = skill_root / "config" / "verification-runner.json"
            plan_path.parent.mkdir()
            plan_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "plan_id": "m242d-integrated",
                        "trusted_local": True,
                        "entries": [
                            {
                                "task_id": fixture.task_id,
                                "contract_revision": int(
                                    authority.task["current_contract_revision"]
                                ),
                                "verification_expectation_digest": (
                                    authority.verification_expectation_digest
                                ),
                                "verification_criterion_digest": (
                                    authority.verification_criterion_digest
                                ),
                                "coverage": "full",
                                "steps": [
                                    {
                                        "step_id": "integrated",
                                        "mode": "script",
                                        "entrypoint": "checks/run.py",
                                        "argv": [],
                                        "cwd": ".",
                                        "timeout_seconds": 30,
                                        "cpu_seconds": 20,
                                        "memory_mib": 128,
                                        "process_limit": 2,
                                        "output_byte_limit": 1_048_576,
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            git(
                fixture.repo,
                "add",
                ".gitignore",
                ".agents",
                "checks/run.py",
                "focused.py",
            )
            git(fixture.repo, "commit", "--quiet", "-m", "integrated target")
            revision = (
                git(fixture.repo, "rev-parse", "HEAD")
                .stdout.decode("ascii")
                .strip()
            )

            (fixture.repo / "focused.py").write_text(
                "print('ambient staged')\n",
                encoding="utf-8",
            )
            git(fixture.repo, "add", "focused.py")
            (fixture.repo / "ambient-untracked.txt").write_text(
                "ambient only\n",
                encoding="utf-8",
            )
            repo_before = file_snapshot(fixture.repo)

            with trace_runner_prelaunch(
                runner_process,
                runner_win32,
            ) as prelaunch_trace, mock.patch.dict(
                os.environ,
                {credential_name: credential_value},
            ):
                result = service.set_review_target_with_optional_runner(
                    fixture.target,
                    fixture.task_id,
                    kind="git_commit",
                    revision=revision,
                )

            self.assertEqual(file_snapshot(fixture.repo), repo_before)
            self.assertEqual(result.task["review_target_generation"], 1)
            self.assertEqual(result.task["review_target_kind"], "git_commit")
            self.assertEqual(result.task["review_target_value"], revision)
            self.assertNotIn("runner_observation", result.task)
            self.assertEqual(
                row_counts(fixture.db),
                {
                    "verification_runner_resolutions": 1,
                    "verification_runner_attempts": 1,
                    "verification_runner_observations": 1,
                    "verification_runner_sandbox_events": 1,
                },
            )

            generation = fixture.generation(1)
            self.assertEqual(generation["state"], "terminal")
            self.assertEqual(generation["resolution"].plan_state, "runner")
            self.assertEqual(generation["resolution"].route, "runner")
            observation = generation["observation"]
            self.assertEqual(
                (
                    observation.route,
                    observation.launch_state,
                    observation.outcome,
                    observation.reason,
                    observation.complete_plan,
                    observation.completed_step_count,
                ),
                ("runner", "launched", "pass", None, 1, 1),
                prelaunch_trace.assertion_message,
            )
            self.assertEqual(
                generation["cleanup_event"].terminal_observation_id,
                observation.verification_runner_observation_id,
            )
            inventory = inspect_runner_layout(service._runner_paths(fixture.target))
            self.assertEqual(inventory.attempt_ids, ())
            self.assertEqual(inventory.quarantine_ids, ())

            with closing(sqlite3.connect(fixture.db)) as connection:
                references = connection.execute(
                    "SELECT evidence_reference_id FROM evidence_references "
                    "WHERE source_kind = 'runner_observation'"
                ).fetchall()
                links = connection.execute(
                    "SELECT criterion_evidence_link_id FROM criterion_evidence_links "
                    "WHERE relation = 'runner_observation'"
                ).fetchall()
                self.assertEqual(len(references), 1)
                self.assertEqual(len(links), 1)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM completion_bundle_members "
                        "WHERE evidence_reference_id = ? "
                        "OR criterion_evidence_link_id = ?",
                        (references[0][0], links[0][0]),
                    ).fetchone()[0],
                    0,
                )

            with mock.patch.object(
                cli_module,
                "select_current_verification_runner_basis",
                side_effect=lambda _target, *, task: (
                    selection.select_current_verification_runner_basis(
                        fixture.target,
                        task=task,
                    )
                ),
            ):
                shown_result = run_taskgov_internal(
                    "task",
                    "show",
                    "--repo",
                    str(fixture.repo),
                    "--db",
                    str(fixture.db),
                    fixture.task_id,
                    "--json",
                    maintenance_enabled=False,
                )
            self.assertEqual(shown_result.returncode, 0, shown_result.stdout)
            shown = json.loads(shown_result.stdout)["data"]
            self.assertEqual(
                shown["verification_evidence"]["gate"],
                {
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                    "required": True,
                    "satisfied": True,
                },
            )
            self.assertEqual(
                shown["verification_evidence"]["counts"],
                {
                    "blocking_exact_current": 0,
                    "qualifying_exact_current": 0,
                    "receipts_exact_current": 0,
                    "receipts_total": 0,
                },
            )

            for state_file in fixture.db.parent.rglob("*"):
                if not state_file.is_file():
                    continue
                retained = state_file.read_bytes()
                self.assertNotIn(raw_output_secret.encode("utf-8"), retained)
                self.assertNotIn(credential_value.encode("utf-8"), retained)

    def test_false_process_proof_is_pending_then_restart_cleanup_allows_next_generation(self):
        for policy in (RUNNER_POLICY_DIGEST, RUNNER_POSIX_POLICY_DIGEST):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                prepared = replace(fixture.prepared(), runner_policy_digest=policy)
                paths = service._runner_paths(fixture.target)

                def unproved_result(request):
                    return RunnerProcessResultV1(
                        version=RUNNER_CONTRACT_VERSION,
                        attempt_id=request.attempt_id,
                        outcome="cleanup_failed",
                        reason="process_cleanup_failed",
                        launch_state="launched",
                        failed_step_ordinal=None,
                        duration_ms=1,
                        cpu_time_ms=None,
                        peak_job_memory_bytes=None,
                        total_process_count=None,
                        process_zero=False,
                        handles_closed=True,
                        raw_output_discarded=True,
                        steps=(),
                        runner_policy_digest=request.runner_policy_digest,
                    )

                with service.zero_wait_runner_lock(paths):
                    intent = service._persist_launch_intent(fixture.target, prepared)
                    with mock.patch.object(
                        service,
                        "materialize_runner_target",
                        return_value=None,
                    ), mock.patch.object(
                        service,
                        "_basis_is_current",
                        return_value=True,
                    ), mock.patch.object(
                        service,
                        "observe_fixed_package_runtime",
                        return_value=Path(sys.executable).resolve(),
                    ), mock.patch.object(
                        service,
                        "build_clean_environment",
                        return_value=(),
                    ), mock.patch.object(
                        service,
                        "run_process_request",
                        side_effect=unproved_result,
                    ):
                        with self.assertRaises(
                            service.VerificationRunnerServiceError
                        ) as raised:
                            service._run_intent_under_lock(
                                fixture.target,
                                paths,
                                prepared,
                                intent,
                                cancel_requested=lambda: False,
                            )
                    self.assertEqual(raised.exception.code, "runner_state_invalid")

                pending = fixture.generation(1)
                self.assertEqual(pending["state"], "pending")
                attempt_root = (
                    paths.attempts / intent.attempt.verification_runner_attempt_id
                )
                self.assertTrue(attempt_root.is_dir())

                with service.zero_wait_runner_lock(paths) as inventory:
                    with self.assertRaises(
                        service.VerificationRunnerServiceError
                    ) as cleanup_error:
                        service._deny_unresolved_attempt(
                            fixture.target,
                            paths,
                            inventory,
                        )
                self.assertEqual(cleanup_error.exception.code, "runner_state_invalid")
                cleaned = fixture.generation(1)
                self.assertEqual(cleaned["state"], "restart_cleaned")
                self.assertIsNone(cleaned["observation"])
                self.assertIsNone(cleaned["cleanup_event"].terminal_observation_id)
                self.assertFalse(attempt_root.exists())

                next_prepared = replace(
                    fixture.prepared(fixture.authority()), runner_policy_digest=policy,
                )
                with service.zero_wait_runner_lock(paths) as inventory:
                    service._deny_unresolved_attempt(
                        fixture.target,
                        paths,
                        inventory,
                    )
                    next_intent = service._persist_launch_intent(
                        fixture.target,
                        next_prepared,
                    )
                self.assertEqual(next_intent.resolution.target_generation, 2)
                self.assertEqual(fixture.generation(2)["state"], "pending")

    def test_t2_basis_race_becomes_cleanup_only_and_next_generation_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            intent = service._persist_launch_intent(fixture.target, prepared)

            advanced_authority = fixture.authority()
            service._persist_ordinary_target(
                fixture.target,
                advanced_authority,
                opaque_artifact_observation(
                    target_kind="diff_fingerprint",
                    target_value=FINGERPRINT_B,
                ),
            )
            observed_at = service.utc_now()
            observation = service._observation_row(
                intent.resolution,
                intent.attempt,
                route="m21_fallback",
                launch_state="no_launch",
                outcome="blocked_prelaunch",
                reason="runtime_unavailable",
                complete_plan=0,
                completed_step_count=0,
                failed_step_ordinal=None,
                started_at=observed_at,
                finished_at=observed_at,
                duration_ms=0,
                cpu_time_ms=None,
                peak_job_memory_bytes=None,
                total_process_count=None,
            )
            with self.assertRaises(service.VerificationRunnerServiceError) as raised:
                service._persist_terminal(fixture.target, intent, observation)
            self.assertEqual(raised.exception.code, "runner_state_invalid")
            old = fixture.generation(1)
            self.assertEqual(old["state"], "restart_cleaned")
            self.assertIsNone(old["observation"])
            self.assertIsNone(old["cleanup_event"].terminal_observation_id)

            next_prepared = fixture.prepared(fixture.authority())
            next_intent = service._persist_launch_intent(fixture.target, next_prepared)
            self.assertEqual(next_intent.resolution.target_generation, 3)
            self.assertEqual(fixture.generation(3)["state"], "pending")

    def test_mapper_rejects_wrong_attempt_or_nonprefix_ordinals(self):
        request = process_request(RUNNER_POLICY_DIGEST)
        step_two = RunnerProcessStepResultV1(
            ordinal=2,
            outcome="pass",
            reason=None,
            launch_state="launched",
            cpu_time_ms=1,
            peak_job_memory_bytes=1,
            total_process_count=1,
        )
        wrong_attempt = RunnerProcessResultV1(
            version=RUNNER_CONTRACT_VERSION,
            attempt_id="tg_verification_runner_attempt_fedcba9876543210",
            outcome="pass",
            reason=None,
            launch_state="launched",
            failed_step_ordinal=None,
            duration_ms=1,
            cpu_time_ms=1,
            peak_job_memory_bytes=1,
            total_process_count=1,
            process_zero=True,
            handles_closed=True,
            raw_output_discarded=True,
            steps=(step_two,),
        )
        self.assertFalse(service._process_result_matches_request(request, wrong_attempt))
        right_attempt = replace(wrong_attempt, attempt_id=request.attempt_id)
        self.assertFalse(service._process_result_matches_request(request, right_attempt))

    def test_mapper_checks_policy_and_uses_its_actual_cpu_scope(self):
        posix_request = process_request(RUNNER_POSIX_POLICY_DIGEST)
        result = passing_process_result(posix_request)
        result = replace(
            result, cpu_time_ms=2_001,
            steps=(replace(result.steps[0], cpu_time_ms=2_001),),
        )
        self.assertTrue(service._process_result_matches_request(posix_request, result))
        self.assertFalse(service._process_result_matches_request(
            process_request(RUNNER_POLICY_DIGEST), result,
        ))
        windows_request = process_request(RUNNER_POLICY_DIGEST)
        windows_result = passing_process_result(windows_request)
        self.assertTrue(service._process_result_matches_request(windows_request, windows_result))
        windows_over = replace(
            windows_result, cpu_time_ms=2_001,
            steps=(replace(windows_result.steps[0], cpu_time_ms=2_001),),
        )
        self.assertFalse(service._process_result_matches_request(windows_request, windows_over))
        self.assertFalse(service._process_result_matches_request(posix_request, object()))

    def test_common_posix_dispatch_copies_policy_and_unmeasured_auxiliary_to_terminal(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                prepared = replace(
                    fixture.prepared(), runner_policy_digest=RUNNER_POSIX_POLICY_DIGEST,
                )
                paths = service._runner_paths(fixture.target)

                def run_common(request):
                    with mock.patch.object(common_process.sys, "platform", platform):
                        return common_process.run_process_request(request)

                with service.zero_wait_runner_lock(paths):
                    intent = service._persist_launch_intent(fixture.target, prepared)
                    self.assertEqual(intent.resolution.runner_policy_digest, RUNNER_POSIX_POLICY_DIGEST)
                    with mock.patch.object(
                        service, "materialize_runner_target", return_value=None,
                    ), mock.patch.object(
                        service, "_basis_is_current", return_value=True,
                    ), mock.patch.object(
                        service, "_physical_basis_matches", return_value=True,
                    ), mock.patch.object(
                        service, "observe_fixed_package_runtime",
                        return_value=Path(sys.executable).resolve(),
                    ), mock.patch.object(
                        service, "build_clean_environment", return_value=(),
                    ), mock.patch.object(
                        service, "run_process_request", side_effect=run_common,
                    ) as process, mock.patch.object(
                        posix_process, "run_process_request", side_effect=passing_process_result,
                    ) as native_dispatch, mock.patch.object(
                        runner_process, "run_process_request",
                    ) as windows_dispatch:
                        result = service._run_intent_under_lock(
                            fixture.target, paths, prepared, intent,
                            cancel_requested=lambda: False,
                        )
                    self.assertEqual(result.verification_route, "runner_pass")
                    self.assertEqual(
                        process.call_args.args[0].runner_policy_digest, RUNNER_POSIX_POLICY_DIGEST,
                    )
                    native_dispatch.assert_called_once_with(process.call_args.args[0])
                    windows_dispatch.assert_not_called()
                stored = fixture.generation(1)
                self.assertEqual(stored["resolution"].runner_policy_digest, RUNNER_POSIX_POLICY_DIGEST)
                self.assertEqual(stored["observation"].complete_plan, 1)
                self.assertEqual(stored["observation"].cpu_time_ms, 1)
                self.assertIsNone(stored["observation"].peak_job_memory_bytes)
                self.assertIsNone(stored["observation"].total_process_count)
                self.assertEqual(
                    selection._terminal_runner_mode(stored["resolution"], stored["observation"]),
                    "runner_observation",
                )

    def test_preparation_captures_policy_and_revalidation_rejects_policy_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            original = fixture.prepared()
            with ExitStack() as stack:
                for name, value in (
                    ("capture_verification_runner_plan", object()),
                    ("resolve_verification_runner_plan", original.plan),
                    ("capture_runner_implementation", original.implementation),
                    ("observe_staged_runner_target", original.target),
                    ("preflight_runner_material", original.material),
                    ("current_runner_policy_digest", RUNNER_POSIX_POLICY_DIGEST),
                ):
                    stack.enter_context(mock.patch.object(service, name, return_value=value))
                prepared = service._prepare_runner(
                    fixture.target, original.authority, kind="git_snapshot", revision=None,
                )
                self.assertEqual(prepared.runner_policy_digest, RUNNER_POSIX_POLICY_DIGEST)
                self.assertTrue(service._physical_basis_matches(fixture.target, prepared))
                self.assertFalse(service._physical_basis_matches(fixture.target, original))
                with self.assertRaises(service.VerificationRunnerServiceError) as raised:
                    service._revalidate_prepared_runner(
                        fixture.target, original, kind="git_snapshot", revision=None,
                    )
                self.assertEqual(raised.exception.code, "runner_state_invalid")

    def test_current_policy_freshness_keeps_package_error_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            prepared = fixture.prepared()
            intent = service._persist_launch_intent(fixture.target, prepared)
            snapshot = {"resolution": intent.resolution}
            with ExitStack() as stack:
                for name, value in (
                    ("capture_verification_runner_plan", object()),
                    ("resolve_verification_runner_plan", prepared.plan),
                    ("capture_runner_implementation", prepared.implementation),
                    ("observe_staged_runner_target", prepared.target),
                    ("preflight_runner_material", prepared.material),
                ):
                    stack.enter_context(mock.patch.object(selection, name, return_value=value))
                package = stack.enter_context(mock.patch.object(
                    selection, "inspect_local_package", return_value=SimpleNamespace(status="clean"),
                ))
                for stored_policy in (RUNNER_POLICY_DIGEST, RUNNER_POSIX_POLICY_DIGEST):
                    snapshot["resolution"] = replace(
                        intent.resolution, runner_policy_digest=stored_policy,
                    )
                    for policy in (RUNNER_POLICY_DIGEST, RUNNER_POSIX_POLICY_DIGEST):
                        with self.subTest(stored=stored_policy, current=policy), mock.patch.object(
                            selection, "current_runner_policy_digest", return_value=policy,
                        ):
                            self.assertEqual(
                                selection._stored_runner_physical_basis_matches(fixture.target, snapshot),
                                stored_policy == policy,
                            )
                package.return_value = SimpleNamespace(status="modified")
                with mock.patch.object(selection, "current_runner_policy_digest") as policy:
                    with self.assertRaises(StorageError) as raised:
                        selection._stored_runner_physical_basis_matches(fixture.target, snapshot)
                self.assertEqual(raised.exception.code, "package_core_modified")
                policy.assert_not_called()
            with mock.patch.object(selection, "current_runner_policy_digest") as policy:
                self.assertIsNone(selection.select_current_verification_runner_basis(
                    fixture.target, task={"status": "done"},
                ))
            policy.assert_not_called()

    def test_complete_plan_requires_every_planned_step_to_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            intent = service._persist_launch_intent(
                fixture.target,
                fixture.prepared(),
            )
            failed_step = RunnerProcessStepResultV1(
                ordinal=1,
                outcome="fail",
                reason="step_nonzero",
                launch_state="launched",
                cpu_time_ms=1,
                peak_job_memory_bytes=1,
                total_process_count=1,
            )
            inconsistent_parent = RunnerProcessResultV1(
                version=RUNNER_CONTRACT_VERSION,
                attempt_id=intent.attempt.verification_runner_attempt_id,
                outcome="pass",
                reason=None,
                launch_state="launched",
                failed_step_ordinal=None,
                duration_ms=1,
                cpu_time_ms=1,
                peak_job_memory_bytes=1,
                total_process_count=1,
                process_zero=True,
                handles_closed=True,
                raw_output_discarded=True,
                steps=(failed_step,),
            )
            observation = service._terminal_from_process(
                intent.resolution,
                intent.attempt,
                inconsistent_parent,
                started_at="2026-08-25T00:00:00Z",
                finished_at="2026-08-25T00:00:01Z",
            )
            self.assertEqual(observation.complete_plan, 0)


if __name__ == "__main__":
    unittest.main()
