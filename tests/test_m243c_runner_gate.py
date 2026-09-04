from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    _copy_skill,
    refresh_test_manifest,
    run_taskgov_internal,
    tree_snapshot,
)
from tests.test_m242_runner_service import RunnerServiceFixture, git
from tests.verification_receipt_test_support import (
    add_receipt,
    show_task,
)

from task_governance_tool import completion_workflow
from task_governance_tool import cli as cli_module
from task_governance_tool import storage as storage_module
from task_governance_tool import tasks as tasks_module
from task_governance_tool import verification_runner_service as service
from task_governance_tool.storage import utc_now


def _launch(fixture: RunnerServiceFixture):
    started = run_taskgov_internal(
        "task",
        "edit",
        "--repo",
        str(fixture.repo),
        "--db",
        str(fixture.db),
        fixture.task_id,
        "--status",
        "in_progress",
        "--json",
        maintenance_enabled=False,
    )
    if started.returncode != 0:
        raise AssertionError(started.stdout)
    prepared = fixture.prepared()
    intent = service._persist_launch_intent(fixture.target, prepared)
    return prepared, intent


def _persist_terminal(fixture: RunnerServiceFixture, intent, *, branch: str):
    observed_at = utc_now()
    if branch == "pass":
        values = {
            "route": "runner",
            "launch_state": "launched",
            "outcome": "pass",
            "reason": None,
            "complete_plan": 1,
            "completed_step_count": intent.resolution.step_count,
            "failed_step_ordinal": None,
            "cpu_time_ms": 1,
            "peak_job_memory_bytes": 1,
            "total_process_count": 1,
        }
    elif branch == "fallback":
        values = {
            "route": "m21_fallback",
            "launch_state": "no_launch",
            "outcome": "blocked_prelaunch",
            "reason": "runtime_unavailable",
            "complete_plan": 0,
            "completed_step_count": 0,
            "failed_step_ordinal": None,
            "cpu_time_ms": None,
            "peak_job_memory_bytes": None,
            "total_process_count": None,
        }
    elif branch == "blocking":
        values = {
            "route": "runner",
            "launch_state": "launched",
            "outcome": "fail",
            "reason": "step_nonzero",
            "complete_plan": 0,
            "completed_step_count": 0,
            "failed_step_ordinal": 1,
            "cpu_time_ms": 1,
            "peak_job_memory_bytes": 1,
            "total_process_count": 1,
        }
    else:
        raise AssertionError("unsupported test branch")
    observation = service._observation_row(
        intent.resolution,
        intent.attempt,
        started_at=observed_at,
        finished_at=observed_at,
        duration_ms=1,
        **values,
    )
    service._persist_terminal(fixture.target, intent, observation)
    return observation


def _seed_review_receipts(fixture: RunnerServiceFixture) -> None:
    with closing(sqlite3.connect(fixture.db)) as connection:
        tier = int(
            connection.execute(
                "SELECT review_tier FROM tasks WHERE task_id = ?",
                (fixture.task_id,),
            ).fetchone()[0]
        )
    receipts = (
        (("mechanical-review", "not_required", "not_required"),)
        if tier == 0
        else tuple(
            (f"m243c-reviewer-{index}", "independent", "pass")
            for index in range(1, 3 if tier == 2 else 2)
        )
    )
    for reviewer, kind, verdict in receipts:
        arguments = [
            "review",
            "receipt",
            "add",
            "--repo",
            str(fixture.repo),
            "--db",
            str(fixture.db),
            fixture.task_id,
            "--reviewer",
            reviewer,
            "--kind",
            kind,
            "--verdict",
            verdict,
            "--summary",
            "Focused TG-M24.3C review",
        ]
        if kind != "not_required":
            arguments.extend(
                (
                    "--reviewer-class",
                    "human",
                    "--model-state",
                    "not_applicable",
                    "--skill-state",
                    "not_applicable",
                    "--context-relation",
                    "external_context",
                )
            )
        recorded = run_taskgov_internal(
            *arguments,
            "--json",
            maintenance_enabled=False,
        )
        if recorded.returncode != 0:
            raise AssertionError(recorded.stdout)


def _add_runner_task(fixture: RunnerServiceFixture, *, title: str) -> str:
    added = run_taskgov_internal(
        "task",
        "add",
        "--repo",
        str(fixture.repo),
        "--db",
        str(fixture.db),
        "--title",
        title,
        "--verification",
        "python -m unittest tests.test_m243c_runner_gate",
        "--contract-scope",
        "Exercise one independent selected Runner graph.",
        "--contract-acceptance",
        "The selected graph remains isolated from unrelated Task state.",
        "--json",
        maintenance_enabled=False,
    )
    if added.returncode != 0:
        raise AssertionError(added.stdout)
    return str(json.loads(added.stdout)["data"]["task"]["task_id"])


def _corrupt_runner_observation_digest(db: Path, *, task_id: str) -> None:
    trigger_name = "trg_verification_runner_observations_no_update"
    with closing(sqlite3.connect(db)) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        ).fetchone()
        if trigger is None or type(trigger[0]) is not str:
            raise AssertionError("Runner observation immutability trigger is missing")
        connection.execute(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            "UPDATE verification_runner_observations "
            "SET sanitized_result_digest = ? WHERE task_id = ?",
            ("sha256:" + "0" * 64, task_id),
        )
        connection.execute(trigger[0])
        connection.commit()


def _corrupt_completion_bundle_digest(db: Path, *, task_id: str) -> None:
    trigger_name = "trg_completion_evidence_bundles_no_update"
    with closing(sqlite3.connect(db)) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        ).fetchone()
        if trigger is None or type(trigger[0]) is not str:
            raise AssertionError("Bundle immutability trigger is missing")
        connection.execute(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            "UPDATE completion_evidence_bundles "
            "SET bundle_digest = ? WHERE task_id = ?",
            ("sha256:" + "0" * 64, task_id),
        )
        connection.execute(trigger[0])
        connection.commit()


def _corrupt_completion_finding_digest_type(db: Path, *, task_id: str) -> None:
    trigger_name = "trg_completion_bundle_finding_snapshots_no_update"
    with closing(sqlite3.connect(db)) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        ).fetchone()
        if trigger is None or type(trigger[0]) is not str:
            raise AssertionError("Finding snapshot immutability trigger is missing")
        connection.execute(f'DROP TRIGGER "{trigger_name}"')
        connection.execute("PRAGMA ignore_check_constraints = ON")
        updated = connection.execute(
            "UPDATE completion_bundle_finding_snapshots "
            "SET digest = ? WHERE task_id = ?",
            (sqlite3.Binary(b"x" * 71), task_id),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        if updated.rowcount != 1:
            raise AssertionError("expected one selected Finding snapshot")
        connection.execute(trigger[0])
        connection.commit()


def _complete_with_matching_commit(
    fixture: RunnerServiceFixture,
    *,
    check: bool = False,
    compatibility_edit: bool = False,
    extra_arguments: tuple[str, ...] = (),
    advance_head: bool = False,
    revision: str | None = None,
):
    if revision is None:
        if advance_head:
            git(
                fixture.repo,
                "commit",
                "--quiet",
                "-m",
                "TG-M24.3C reviewed completion",
            )
            revision = (
                git(fixture.repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
            )
        else:
            tree = git(fixture.repo, "write-tree").stdout.decode("ascii").strip()
            parent = (
                git(fixture.repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
            )
            revision = (
                git(
                    fixture.repo,
                    "commit-tree",
                    tree,
                    "-p",
                    parent,
                    "-m",
                    "TG-M24.3C completion evidence",
                )
                .stdout.decode("ascii")
                .strip()
            )
    arguments = [
        "task",
        "edit" if compatibility_edit else "complete",
        "--repo",
        str(fixture.repo),
        "--db",
        str(fixture.db),
        fixture.task_id,
    ]
    if compatibility_edit:
        arguments.extend(("--status", "done"))
    arguments.extend(extra_arguments)
    arguments.extend(
        (
            "--verification-complete",
            "--review-complete",
            "--completion-evidence-kind",
            "git_commit",
            "--completion-revision",
            revision,
        )
    )
    if check:
        arguments.extend(("--check", "--read-only"))
    return run_taskgov_internal(
        *arguments,
        "--json",
        maintenance_enabled=False,
    )


def _complete_runner_pass(fixture: RunnerServiceFixture) -> None:
    _prepared, intent = _launch(fixture)
    _persist_terminal(fixture, intent, branch="pass")
    _seed_review_receipts(fixture)
    with mock.patch.object(
        service,
        "_stored_runner_physical_basis_matches",
        return_value=True,
    ):
        completed = _complete_with_matching_commit(fixture)
    if completed.returncode != 0:
        raise AssertionError(completed.stdout)


def _show_task_through_task_local_connection(
    fixture: RunnerServiceFixture,
    *,
    task_id: str,
):
    with closing(
        storage_module.connect_initialized_task_readonly(fixture.target)
    ) as connection:
        return tasks_module.show_task(
            connection,
            fixture.target.project,
            task_id,
        )


class M243CRunnerGateTests(unittest.TestCase):
    def test_target_set_route_reuses_the_closed_runner_terminal_classification(self):
        cases = (
            ("pass", "runner_pass", None),
            ("fallback", "receipt_required", None),
            ("blocking", "blocked", "verification_receipt_blocking"),
        )
        for branch, expected_route, expected_code in cases:
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = _launch(fixture)
                observation = _persist_terminal(fixture, intent, branch=branch)
                result = service._routed_runner_target(
                    intent.review,
                    intent.resolution,
                    observation,
                )
                self.assertEqual(result.verification_route, expected_route)
                self.assertEqual(result.blocking_code, expected_code)

    def test_public_runner_routes_release_sqlite_before_physical_selection(self):
        for route in ("show", "receipt", "complete", "complete_check"):
            with self.subTest(route=route), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = _launch(fixture)
                if route == "receipt":
                    _persist_terminal(fixture, intent, branch="fallback")
                elif route in {"complete", "complete_check"}:
                    _persist_terminal(fixture, intent, branch="pass")
                    _seed_review_receipts(fixture)
                physical_checks = 0

                def assert_no_outer_read_transaction(
                    _target,
                    _snapshot,
                    *,
                    completion_revision=None,
                ):
                    nonlocal physical_checks
                    physical_checks += 1
                    with closing(
                        sqlite3.connect(fixture.db, timeout=0, isolation_level=None)
                    ) as probe:
                        probe.execute("BEGIN EXCLUSIVE")
                        probe.execute("ROLLBACK")
                    return True

                with mock.patch.object(
                    service,
                    "_stored_runner_physical_basis_matches",
                    side_effect=assert_no_outer_read_transaction,
                ):
                    if route == "show":
                        result = show_task(
                            fixture.db,
                            fixture.repo,
                            fixture.task_id,
                            json_output=True,
                        )
                    elif route == "receipt":
                        result = add_receipt(
                            fixture.db,
                            fixture.repo,
                            fixture.task_id,
                            1,
                        )
                    else:
                        result = _complete_with_matching_commit(
                            fixture,
                            check=route == "complete_check",
                        )

                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertGreater(physical_checks, 0)

    def test_task_show_builds_one_projection_without_global_runner_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")

            original_graph_validator = (
                storage_module._validated_verification_runner_graph
            )

            def selected_graph_only(connection, **kwargs):
                if kwargs.get("selected_generation") is None:
                    raise AssertionError("task show scanned the global Runner graph")
                return original_graph_validator(connection, **kwargs)

            with mock.patch.object(
                storage_module,
                "_validated_verification_runner_graph",
                side_effect=selected_graph_only,
            ), mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                return_value=True,
            ), mock.patch.object(
                cli_module,
                "show_task",
                wraps=cli_module.show_task,
            ) as projection:
                result = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(projection.call_count, 1)

    def test_task_local_connector_isolates_unrelated_corruption_while_global_rejects(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            selected_task_id = fixture.task_id
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")

            unrelated_task_id = _add_runner_task(
                fixture,
                title="Unrelated Runner graph",
            )
            fixture.task_id = unrelated_task_id
            try:
                _prepared, unrelated_intent = _launch(fixture)
                _persist_terminal(fixture, unrelated_intent, branch="pass")
            finally:
                fixture.task_id = selected_task_id
            _corrupt_runner_observation_digest(
                fixture.db,
                task_id=unrelated_task_id,
            )

            with self.assertRaises(storage_module.StorageError) as global_error:
                storage_module.connect_initialized_readonly(fixture.target)
            self.assertEqual(global_error.exception.code, "project_state_unreadable")

            with closing(
                storage_module.connect_initialized_task_readonly(fixture.target)
            ) as connection:
                snapshot = storage_module.read_current_verification_runner_gate_snapshot(
                    connection,
                    project_id=fixture.target.project.project_id,
                    task_id=selected_task_id,
                )
            self.assertEqual(snapshot["target_generation"], 1)

    def test_task_local_runner_read_rejects_selected_graph_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            _corrupt_runner_observation_digest(
                fixture.db,
                task_id=fixture.task_id,
            )

            with closing(
                storage_module.connect_initialized_task_readonly(fixture.target)
            ) as connection, self.assertRaises(
                storage_module.StorageError
            ) as selected_error:
                storage_module.read_current_verification_runner_gate_snapshot(
                    connection,
                    project_id=fixture.target.project.project_id,
                    task_id=fixture.task_id,
                )
            self.assertEqual(selected_error.exception.code, "project_state_unreadable")
            result = show_task(
                fixture.db,
                fixture.repo,
                fixture.task_id,
                json_output=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["errors"][0]["code"],
                "project_state_unreadable",
            )

    def test_done_runner_history_uses_only_its_exact_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _complete_runner_pass(fixture)
            with closing(sqlite3.connect(fixture.db)) as connection:
                cycle_generation = int(
                    connection.execute(
                        "SELECT review_target_generation "
                        "FROM task_completion_cycles WHERE task_id = ?",
                        (fixture.task_id,),
                    ).fetchone()[0]
                )

            original_graph_validator = (
                storage_module._validated_verification_runner_graph
            )
            selected_generations: list[int] = []

            def historical_generation_only(connection, **kwargs):
                selected = kwargs.get("selected_generation")
                cycle = kwargs.get("selected_history_cycle")
                if selected is None or cycle is None or kwargs.get("selected_task"):
                    raise AssertionError("history used an unscoped Runner validator")
                selected_generations.append(int(selected[2]))
                return original_graph_validator(connection, **kwargs)

            with mock.patch.object(
                storage_module,
                "_validated_verification_runner_graph",
                side_effect=historical_generation_only,
            ), mock.patch.object(
                storage_module,
                "_validated_completion_evidence_projection_bases",
                side_effect=AssertionError("history used the global Bundle validator"),
            ):
                shown = _show_task_through_task_local_connection(
                    fixture,
                    task_id=fixture.task_id,
                )

            self.assertEqual(shown.completion_history["total"], 1)
            self.assertEqual(selected_generations, [cycle_generation])

    def test_done_manual_history_does_not_invoke_runner_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            started = run_taskgov_internal(
                "task",
                "edit",
                "--repo",
                str(fixture.repo),
                "--db",
                str(fixture.db),
                fixture.task_id,
                "--status",
                "in_progress",
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(started.returncode, 0, started.stdout)
            with mock.patch.object(service, "_prepare_runner", return_value=None):
                service.set_review_target_with_shadow_runner(
                    fixture.target,
                    fixture.task_id,
                    kind="git_snapshot",
                )
            with closing(sqlite3.connect(fixture.db)) as connection:
                target_generation = int(
                    connection.execute(
                        "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                        (fixture.task_id,),
                    ).fetchone()[0]
                )
            receipt = add_receipt(
                fixture.db,
                fixture.repo,
                fixture.task_id,
                target_generation,
            )
            self.assertEqual(receipt.returncode, 0, receipt.stdout)
            _seed_review_receipts(fixture)
            completed = _complete_with_matching_commit(fixture)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with mock.patch.object(
                storage_module,
                "_validated_verification_runner_graph",
                side_effect=AssertionError("manual history invoked Runner validation"),
            ), mock.patch.object(
                storage_module,
                "_validated_completion_evidence_projection_bases",
                side_effect=AssertionError("history used the global Bundle validator"),
            ):
                shown = _show_task_through_task_local_connection(
                    fixture,
                    task_id=fixture.task_id,
                )

            self.assertEqual(shown.completion_history["total"], 1)

    def test_reopened_history_uses_stored_generation_not_current_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _complete_runner_pass(fixture)
            reopened = run_taskgov_internal(
                "task",
                "edit",
                "--repo",
                str(fixture.repo),
                "--db",
                str(fixture.db),
                fixture.task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Exercise exact historical Runner selection",
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            with closing(sqlite3.connect(fixture.db)) as connection:
                cycle_generation = int(
                    connection.execute(
                        "SELECT review_target_generation "
                        "FROM task_completion_cycles WHERE task_id = ?",
                        (fixture.task_id,),
                    ).fetchone()[0]
                )
                current_generation = int(
                    connection.execute(
                        "SELECT review_target_generation FROM tasks WHERE task_id = ?",
                        (fixture.task_id,),
                    ).fetchone()[0]
                )
            self.assertGreater(current_generation, cycle_generation)

            original_graph_validator = (
                storage_module._validated_verification_runner_graph
            )
            selected_generations: list[int] = []

            def historical_generation_only(connection, **kwargs):
                selected = kwargs.get("selected_generation")
                if kwargs.get("selected_history_cycle") is None or selected is None:
                    raise AssertionError("reopened history used an unscoped Runner read")
                selected_generations.append(int(selected[2]))
                return original_graph_validator(connection, **kwargs)

            with mock.patch.object(
                storage_module,
                "_validated_verification_runner_graph",
                side_effect=historical_generation_only,
            ), mock.patch.object(
                storage_module,
                "_validated_completion_evidence_projection_bases",
                side_effect=AssertionError("history used the global Bundle validator"),
            ):
                shown = _show_task_through_task_local_connection(
                    fixture,
                    task_id=fixture.task_id,
                )

            self.assertEqual(shown.completion_history["total"], 1)
            self.assertEqual(selected_generations, [cycle_generation])
            self.assertNotIn(current_generation, selected_generations)

    def test_done_history_isolates_unrelated_corruption_but_rejects_selected(self):
        cases = (
            (
                "runner",
                _corrupt_runner_observation_digest,
                "project_state_unreadable",
            ),
            (
                "bundle",
                _corrupt_completion_bundle_digest,
                "completion_history_inconsistent",
            ),
        )
        global_bundle_validator = getattr(
            storage_module,
            "_validated_completion_evidence_projection_bases",
        )
        for name, corrupt, selected_code in cases:
            with self.subTest(corruption=name):
                temporary_context = tempfile.TemporaryDirectory()
                self.addCleanup(temporary_context.cleanup)
                fixture = RunnerServiceFixture(Path(temporary_context.name))
                selected_task_id = fixture.task_id
                _complete_runner_pass(fixture)
                unrelated_task_id = _add_runner_task(
                    fixture,
                    title="Unrelated completed Runner history",
                )
                fixture.task_id = unrelated_task_id
                try:
                    _complete_runner_pass(fixture)
                finally:
                    fixture.task_id = selected_task_id
                corrupt(fixture.db, task_id=unrelated_task_id)

                with self.assertRaises(
                    storage_module.StorageError
                ) as global_error:
                    if name == "runner":
                        with closing(
                            storage_module.connect_initialized_readonly(
                                fixture.target
                            )
                        ):
                            pass
                    else:
                        with closing(
                            storage_module.connect(fixture.db)
                        ) as connection:
                            global_bundle_validator(connection)
                self.assertEqual(
                    global_error.exception.code,
                    (
                        "project_state_unreadable"
                        if name == "runner"
                        else "evidence_ledger_inconsistent"
                    ),
                )

                with mock.patch.object(
                    storage_module,
                    "_validated_completion_evidence_projection_bases",
                    side_effect=AssertionError(
                        "history used the global Bundle validator"
                    ),
                ):
                    shown = _show_task_through_task_local_connection(
                        fixture,
                        task_id=selected_task_id,
                    )
                self.assertEqual(shown.completion_history["total"], 1)

                corrupt(fixture.db, task_id=selected_task_id)
                with self.assertRaises(
                    storage_module.StorageError
                ) as selected_error:
                    _show_task_through_task_local_connection(
                        fixture,
                        task_id=selected_task_id,
                    )
                self.assertEqual(selected_error.exception.code, selected_code)

    def test_selected_history_maps_malformed_finding_to_closed_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            _seed_review_receipts(fixture)
            with closing(sqlite3.connect(fixture.db)) as connection:
                receipt_id = str(
                    connection.execute(
                        "SELECT review_receipt_id FROM review_receipts "
                        "WHERE task_id = ? ORDER BY created_at LIMIT 1",
                        (fixture.task_id,),
                    ).fetchone()[0]
                )
            finding = run_taskgov_internal(
                "review",
                "finding",
                "add",
                "--repo",
                str(fixture.repo),
                "--db",
                str(fixture.db),
                fixture.task_id,
                "--receipt-id",
                receipt_id,
                "--severity",
                "low",
                "--summary",
                "Nonblocking selected-history audit snapshot",
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(finding.returncode, 0, finding.stdout)
            with mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                return_value=True,
            ):
                completed = _complete_with_matching_commit(fixture)
            self.assertEqual(completed.returncode, 0, completed.stdout)

            _corrupt_completion_finding_digest_type(
                fixture.db,
                task_id=fixture.task_id,
            )
            with self.assertRaises(storage_module.StorageError) as rejected:
                _show_task_through_task_local_connection(
                    fixture,
                    task_id=fixture.task_id,
                )
            self.assertEqual(
                rejected.exception.code,
                "completion_history_inconsistent",
            )

    def test_actual_review_commit_preserves_runner_pass_and_closed_fallback(self):
        for branch, expected_basis in (
            ("pass", "runner_observation"),
            ("fallback", "caller_attestation"),
        ):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                prepared, intent = _launch(fixture)
                skill_parent = fixture.repo / ".agents" / "skills"
                skill_parent.mkdir(parents=True)
                installed_skill_root = _copy_skill(skill_parent)
                installed_target = replace(
                    fixture.target,
                    skill_root=installed_skill_root,
                )
                with mock.patch.object(
                    service,
                    "capture_verification_runner_plan",
                    return_value=None,
                ), mock.patch.object(
                    service,
                    "resolve_verification_runner_plan",
                    return_value=prepared.plan,
                ), mock.patch.object(
                    service,
                    "capture_runner_implementation",
                    return_value=prepared.implementation,
                ), mock.patch(
                    "tests.m14_test_support.resolve_database_target",
                    return_value=installed_target,
                ):
                    _persist_terminal(fixture, intent, branch=branch)
                    if branch == "fallback":
                        receipt = add_receipt(
                            fixture.db,
                            fixture.repo,
                            fixture.task_id,
                            1,
                        )
                        self.assertEqual(receipt.returncode, 0, receipt.stdout)
                    _seed_review_receipts(fixture)

                    with mock.patch.object(
                        service,
                        "observe_staged_runner_target",
                        side_effect=AssertionError(
                            "completion reread the ambient index"
                        ),
                    ):
                        git(
                            fixture.repo,
                            "commit",
                            "--quiet",
                            "-m",
                            "TG-M24.3C reviewed completion",
                        )
                        revision = (
                            git(fixture.repo, "rev-parse", "HEAD")
                            .stdout.decode("ascii")
                            .strip()
                        )
                        with closing(
                            storage_module.connect_initialized_task_readonly(
                                installed_target
                            )
                        ) as connection:
                            snapshot = (
                                storage_module.read_current_verification_runner_gate_snapshot(
                                    connection,
                                    project_id=fixture.target.project.project_id,
                                    task_id=fixture.task_id,
                                )
                            )
                        self.assertTrue(
                            service._stored_runner_physical_basis_matches(
                                installed_target,
                                snapshot,
                                completion_revision=revision,
                            )
                        )
                        checked = _complete_with_matching_commit(
                            fixture,
                            check=True,
                            revision=revision,
                        )
                        completed = _complete_with_matching_commit(
                            fixture,
                            revision=revision,
                        )

                self.assertEqual(checked.returncode, 0, checked.stdout)
                self.assertEqual(
                    json.loads(checked.stdout)["data"]["blocking_codes"],
                    [],
                )
                self.assertEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(
                    git(fixture.repo, "diff", "--cached", "--quiet").returncode,
                    0,
                )
                with closing(sqlite3.connect(fixture.db)) as connection:
                    cycle = connection.execute(
                        "SELECT verification_basis_kind FROM task_completion_cycles "
                        "WHERE task_id = ? ORDER BY saved_cycle_ordinal DESC LIMIT 1",
                        (fixture.task_id,),
                    ).fetchone()
                self.assertIsNotNone(cycle)
                self.assertEqual(cycle[0], expected_basis)

    def test_valid_package_identity_change_makes_live_runner_basis_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            skill_parent = fixture.repo / ".agents" / "skills"
            skill_parent.mkdir(parents=True)
            installed_skill_root = _copy_skill(skill_parent)
            fixture.target = replace(
                fixture.target,
                skill_root=installed_skill_root,
            )
            implementation_before = service.capture_runner_implementation(
                installed_skill_root
            )
            original_prepared = fixture.prepared

            def prepare_with_installed_identity():
                return replace(
                    original_prepared(),
                    implementation=implementation_before,
                )

            with mock.patch.object(
                fixture,
                "prepared",
                side_effect=prepare_with_installed_identity,
            ):
                prepared, intent = _launch(fixture)
            observation = _persist_terminal(fixture, intent, branch="pass")
            self.assertEqual(
                observation.runner_implementation_digest,
                implementation_before.implementation_digest,
            )

            # Only Plan admission and the existing private target seam are
            # substituted; package capture and physical comparison stay real.
            with mock.patch.object(
                service,
                "capture_verification_runner_plan",
                return_value=None,
            ), mock.patch.object(
                service,
                "resolve_verification_runner_plan",
                return_value=prepared.plan,
            ), mock.patch(
                "tests.m14_test_support.resolve_database_target",
                return_value=fixture.target,
            ):
                _seed_review_receipts(fixture)
                state_before = tree_snapshot(fixture.db.parent)
                current = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )
                current_check = _complete_with_matching_commit(fixture, check=True)
                self.assertEqual(current.returncode, 0, current.stdout)
                self.assertEqual(current_check.returncode, 0, current_check.stdout)
                self.assertEqual(
                    json.loads(current.stdout)["data"]["verification_evidence"][
                        "gate"
                    ],
                    {
                        "required": True,
                        "satisfied": True,
                        "blocking_code": None,
                        "qualifying_receipt_id": None,
                    },
                )
                self.assertEqual(
                    json.loads(current_check.stdout)["data"]["blocking_codes"],
                    [],
                )

                core = installed_skill_root / "SKILL.md"
                core.write_bytes(
                    core.read_bytes() + b"\n<!-- Package identity fixture. -->\n"
                )
                refresh_test_manifest(installed_skill_root)
                implementation_after = service.capture_runner_implementation(
                    installed_skill_root
                )
                self.assertNotEqual(
                    implementation_after.implementation_digest,
                    implementation_before.implementation_digest,
                )
                stale = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )
                stale_check = _complete_with_matching_commit(fixture, check=True)

            self.assertEqual(stale.returncode, 0, stale.stdout)
            self.assertEqual(
                json.loads(stale.stdout)["data"]["verification_evidence"]["gate"],
                {
                    "required": True,
                    "satisfied": False,
                    "blocking_code": "evidence_basis_stale",
                    "qualifying_receipt_id": None,
                },
            )
            self.assertEqual(stale_check.returncode, 0, stale_check.stdout)
            self.assertEqual(
                json.loads(stale_check.stdout)["data"]["blocking_codes"],
                ["evidence_basis_stale"],
            )
            self.assertEqual(tree_snapshot(fixture.db.parent), state_before)

    def test_completion_workflow_does_not_import_runner_service(self):
        module_name = "task_governance_tool.verification_runner_service"
        syntax = ast.parse(
            Path(completion_workflow.__file__).read_text(encoding="utf-8")
        )
        forbidden_imports = []
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                forbidden_imports.extend(
                    alias.name for alias in node.names if alias.name == module_name
                )
            elif isinstance(node, ast.ImportFrom) and node.module == module_name:
                forbidden_imports.append(node.module)

        self.assertEqual(forbidden_imports, [])

    def test_concurrent_target_invalidation_precedes_runner_package_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _launch(fixture)
            original_prepare = completion_workflow.prepare_completion_plan

            def invalidate_after_preflight(*args, **kwargs):
                plan = original_prepare(*args, **kwargs)
                changed = run_taskgov_internal(
                    "task",
                    "edit",
                    "--repo",
                    str(fixture.repo),
                    "--db",
                    str(fixture.db),
                    fixture.task_id,
                    "--title",
                    "Concurrent target invalidation",
                    "--json",
                    maintenance_enabled=False,
                )
                self.assertEqual(changed.returncode, 0, changed.stdout)
                return plan

            with mock.patch.object(
                completion_workflow,
                "prepare_completion_plan",
                side_effect=invalidate_after_preflight,
            ), mock.patch.object(
                cli_module,
                "select_current_verification_runner_basis",
            ) as selector:
                result = _complete_with_matching_commit(fixture, check=True)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                json.loads(result.stdout)["data"]["blocking_codes"],
                ["completion_check_stale"],
            )
            selector.assert_not_called()

    def test_completion_argument_error_precedes_runner_package_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _launch(fixture)

            with mock.patch.object(
                cli_module,
                "select_current_verification_runner_basis",
            ) as selector:
                result = run_taskgov_internal(
                    "task",
                    "complete",
                    "--repo",
                    str(fixture.repo),
                    "--db",
                    str(fixture.db),
                    fixture.task_id,
                    "--completion-revision",
                    "a" * 40,
                    "--json",
                    maintenance_enabled=False,
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                json.loads(result.stdout)["errors"][0]["code"],
                "completion_evidence_conflict",
            )
            selector.assert_not_called()

    def test_runner_pass_is_receiptless_and_seals_existing_runner_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)

            with mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                return_value=True,
            ):
                pending = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )
            self.assertEqual(pending.returncode, 0, pending.stdout)
            self.assertEqual(
                json.loads(pending.stdout)["data"]["verification_evidence"][
                    "gate"
                ]["blocking_code"],
                "evidence_basis_stale",
            )

            observation = _persist_terminal(fixture, intent, branch="pass")
            with mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                return_value=True,
            ):
                shown = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )
                rejected_receipt = add_receipt(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    1,
                )
                _seed_review_receipts(fixture)
                completed = _complete_with_matching_commit(fixture)

            self.assertEqual(shown.returncode, 0, shown.stdout)
            shown_data = json.loads(shown.stdout)["data"]
            self.assertEqual(
                shown_data["verification_evidence"]["gate"],
                {
                    "blocking_code": None,
                    "qualifying_receipt_id": None,
                    "required": True,
                    "satisfied": True,
                },
            )
            self.assertEqual(
                shown_data["verification_evidence"]["counts"],
                {
                    "blocking_exact_current": 0,
                    "qualifying_exact_current": 0,
                    "receipts_exact_current": 0,
                    "receipts_total": 0,
                },
            )
            self.assertNotEqual(rejected_receipt.returncode, 0)
            self.assertEqual(
                json.loads(rejected_receipt.stdout)["errors"][0]["code"],
                "evidence_basis_stale",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)

            with closing(sqlite3.connect(fixture.db)) as connection:
                connection.row_factory = sqlite3.Row
                cycle = dict(
                    connection.execute(
                        "SELECT * FROM task_completion_cycles "
                        "WHERE task_id = ? ORDER BY saved_cycle_ordinal DESC "
                        "LIMIT 1",
                        (fixture.task_id,),
                    ).fetchone()
                )
                bundle = dict(
                    connection.execute(
                        "SELECT * FROM completion_evidence_bundles "
                        "WHERE completion_evidence_bundle_id = ?",
                        (cycle["completion_evidence_bundle_id"],),
                    ).fetchone()
                )
                runner_reference_id = connection.execute(
                    "SELECT evidence_reference_id FROM evidence_references "
                    "WHERE source_kind = 'runner_observation'",
                ).fetchone()[0]
                runner_link_id = connection.execute(
                    "SELECT criterion_evidence_link_id "
                    "FROM criterion_evidence_links "
                    "WHERE relation = 'runner_observation'",
                ).fetchone()[0]
                member_count = connection.execute(
                    "SELECT COUNT(*) FROM completion_bundle_members "
                    "WHERE completion_evidence_bundle_id = ? AND "
                    "(evidence_reference_id = ? OR criterion_evidence_link_id = ?)",
                    (
                        cycle["completion_evidence_bundle_id"],
                        runner_reference_id,
                        runner_link_id,
                    ),
                ).fetchone()[0]
                receipt_count = connection.execute(
                    "SELECT COUNT(*) FROM verification_receipts "
                    "WHERE task_id = ?",
                    (fixture.task_id,),
                ).fetchone()[0]

            observation_id = observation.verification_runner_observation_id
            self.assertEqual(cycle["verification_basis_kind"], "runner_observation")
            self.assertIsNone(cycle["verification_receipt_id"])
            self.assertEqual(cycle["verification_runner_observation_id"], observation_id)
            self.assertEqual(bundle["source_schema_version"], 21)
            self.assertEqual(bundle["bundle_version"], 2)
            self.assertEqual(bundle["verification_basis_kind"], "runner_observation")
            self.assertEqual(bundle["verification_runner_observation_id"], observation_id)
            self.assertEqual(member_count, 2)
            self.assertEqual(receipt_count, 0)

            with mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                side_effect=AssertionError("done history inspected current package"),
            ):
                replay = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )
            self.assertEqual(replay.returncode, 0, replay.stdout)
            self.assertNotIn(observation_id, replay.stdout)

    def test_closed_fallback_uses_m21_receipt_and_caller_attestation_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            observation = _persist_terminal(fixture, intent, branch="fallback")

            with mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                return_value=True,
            ):
                before = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )
                receipt = add_receipt(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    1,
                )
                _seed_review_receipts(fixture)
                completed = _complete_with_matching_commit(fixture)

            self.assertEqual(before.returncode, 0, before.stdout)
            self.assertEqual(
                json.loads(before.stdout)["data"]["verification_evidence"][
                    "gate"
                ]["blocking_code"],
                "verification_receipt_required",
            )
            self.assertEqual(receipt.returncode, 0, receipt.stdout)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            receipt_id = json.loads(receipt.stdout)["data"]["receipt"][
                "verification_receipt_id"
            ]

            with closing(sqlite3.connect(fixture.db)) as connection:
                connection.row_factory = sqlite3.Row
                cycle = dict(
                    connection.execute(
                        "SELECT * FROM task_completion_cycles "
                        "WHERE task_id = ? ORDER BY saved_cycle_ordinal DESC "
                        "LIMIT 1",
                        (fixture.task_id,),
                    ).fetchone()
                )
                bundle = dict(
                    connection.execute(
                        "SELECT * FROM completion_evidence_bundles "
                        "WHERE completion_evidence_bundle_id = ?",
                        (cycle["completion_evidence_bundle_id"],),
                    ).fetchone()
                )
                runner_members = connection.execute(
                    "SELECT COUNT(*) FROM completion_bundle_members AS member "
                    "LEFT JOIN evidence_references AS reference "
                    "ON reference.evidence_reference_id = member.evidence_reference_id "
                    "LEFT JOIN criterion_evidence_links AS link "
                    "ON link.criterion_evidence_link_id = member.criterion_evidence_link_id "
                    "WHERE member.completion_evidence_bundle_id = ? AND "
                    "(reference.source_kind = 'runner_observation' "
                    "OR link.relation = 'runner_observation')",
                    (cycle["completion_evidence_bundle_id"],),
                ).fetchone()[0]

            self.assertEqual(cycle["verification_basis_kind"], "caller_attestation")
            self.assertEqual(cycle["verification_receipt_id"], receipt_id)
            self.assertIsNone(cycle["verification_runner_observation_id"])
            self.assertEqual(bundle["verification_basis_kind"], "caller_attestation")
            self.assertEqual(bundle["verification_receipt_id"], receipt_id)
            self.assertIsNone(bundle["verification_runner_observation_id"])
            self.assertEqual(runner_members, 0)
            self.assertNotEqual(
                observation.verification_runner_observation_id,
                cycle["verification_runner_observation_id"],
            )

    def test_compatibility_edit_uses_the_same_runner_pass_and_fallback_selector(self):
        for branch in ("pass", "fallback"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = _launch(fixture)
                observation = _persist_terminal(fixture, intent, branch=branch)

                with mock.patch.object(
                    service,
                    "_stored_runner_physical_basis_matches",
                    return_value=True,
                ):
                    receipt_id = None
                    if branch == "fallback":
                        receipt = add_receipt(
                            fixture.db,
                            fixture.repo,
                            fixture.task_id,
                            1,
                        )
                        self.assertEqual(receipt.returncode, 0, receipt.stdout)
                        receipt_id = json.loads(receipt.stdout)["data"]["receipt"][
                            "verification_receipt_id"
                        ]
                    _seed_review_receipts(fixture)
                    completed = _complete_with_matching_commit(
                        fixture,
                        compatibility_edit=True,
                    )

                self.assertEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(json.loads(completed.stdout)["command"], "task.edit")
                with closing(sqlite3.connect(fixture.db)) as connection:
                    connection.row_factory = sqlite3.Row
                    cycle = dict(
                        connection.execute(
                            "SELECT * FROM task_completion_cycles "
                            "WHERE task_id = ? ORDER BY saved_cycle_ordinal DESC "
                            "LIMIT 1",
                            (fixture.task_id,),
                        ).fetchone()
                    )

                self.assertEqual(
                    cycle["verification_basis_kind"],
                    "runner_observation" if branch == "pass" else "caller_attestation",
                )
                self.assertEqual(cycle["verification_receipt_id"], receipt_id)
                self.assertEqual(
                    cycle["verification_runner_observation_id"],
                    (
                        observation.verification_runner_observation_id
                        if branch == "pass"
                        else None
                    ),
                )

    def test_compatibility_edit_rejects_concurrent_target_before_runner_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            _seed_review_receipts(fixture)
            original_revalidate = tasks_module.revalidate_done_git_evidence
            retargeted = False

            def invalidate_after_git(*args, **kwargs):
                nonlocal retargeted
                result = original_revalidate(*args, **kwargs)
                if kwargs.get("status_was_provided") and not retargeted:
                    retargeted = True
                    changed = run_taskgov_internal(
                        "task",
                        "edit",
                        "--repo",
                        str(fixture.repo),
                        "--db",
                        str(fixture.db),
                        fixture.task_id,
                        "--title",
                        "Concurrent compatibility invalidation",
                        "--json",
                        maintenance_enabled=False,
                    )
                    self.assertEqual(changed.returncode, 0, changed.stdout)
                return result

            with mock.patch.object(
                tasks_module,
                "revalidate_done_git_evidence",
                side_effect=invalidate_after_git,
            ), mock.patch.object(
                cli_module,
                "select_current_verification_runner_basis",
            ) as selector:
                completed = _complete_with_matching_commit(
                    fixture,
                    compatibility_edit=True,
                )

            self.assertTrue(retargeted)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(
                json.loads(completed.stdout)["errors"][0]["code"],
                "review_target_mismatch",
            )
            selector.assert_not_called()

    def test_compatibility_edit_rejects_retarget_after_first_basis_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            _seed_review_receipts(fixture)
            original_capture = tasks_module.capture_completion_basis
            retargeted = False

            def retarget_after_capture(connection, *args, **kwargs):
                nonlocal retargeted
                basis = original_capture(connection, *args, **kwargs)
                if not retargeted and kwargs.get("runner_selection") is None:
                    retargeted = True
                    changed = run_taskgov_internal(
                        "task",
                        "edit",
                        "--repo",
                        str(fixture.repo),
                        "--db",
                        str(fixture.db),
                        fixture.task_id,
                        "--title",
                        "Concurrent post-capture retarget",
                        "--json",
                        maintenance_enabled=False,
                    )
                    self.assertEqual(changed.returncode, 0, changed.stdout)
                return basis

            with mock.patch.object(
                tasks_module,
                "capture_completion_basis",
                side_effect=retarget_after_capture,
            ), mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                return_value=True,
            ):
                completed = _complete_with_matching_commit(
                    fixture,
                    compatibility_edit=True,
                )

            self.assertTrue(retargeted)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(
                json.loads(completed.stdout)["errors"][0]["code"],
                "review_target_mismatch",
            )
            with closing(sqlite3.connect(fixture.db)) as connection:
                status = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (fixture.task_id,),
                ).fetchone()[0]
                cycle_count = connection.execute(
                    "SELECT COUNT(*) FROM task_completion_cycles WHERE task_id = ?",
                    (fixture.task_id,),
                ).fetchone()[0]
            self.assertEqual(status, "in_progress")
            self.assertEqual(cycle_count, 0)

    def test_compatibility_edit_checks_proposed_lane_before_runner_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            predecessor = run_taskgov_internal(
                "task",
                "add",
                "--repo",
                str(fixture.repo),
                "--db",
                str(fixture.db),
                "--title",
                "Incomplete predecessor",
                "--kind",
                "sequential",
                "--lane",
                "blocked-lane",
                "--order",
                "1",
                "--contract-scope",
                "Keep the predecessor incomplete for ordering validation.",
                "--contract-acceptance",
                "The compatibility completion reports the lane blocker first.",
                "--json",
                maintenance_enabled=False,
            )
            self.assertEqual(predecessor.returncode, 0, predecessor.stdout)
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            _seed_review_receipts(fixture)

            with mock.patch.object(
                cli_module,
                "select_current_verification_runner_basis",
            ) as selector:
                completed = _complete_with_matching_commit(
                    fixture,
                    compatibility_edit=True,
                    extra_arguments=(
                        "--kind",
                        "sequential",
                        "--lane",
                        "blocked-lane",
                        "--order",
                        "2",
                    ),
                )

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(
                json.loads(completed.stdout)["errors"][0]["code"],
                "sequential_predecessor_incomplete",
            )
            selector.assert_not_called()

    def test_other_terminal_is_blocking_and_receipt_cannot_override_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="blocking")

            with mock.patch.object(
                service,
                "_stored_runner_physical_basis_matches",
                return_value=True,
            ):
                shown = show_task(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    json_output=True,
                )
                receipt = add_receipt(
                    fixture.db,
                    fixture.repo,
                    fixture.task_id,
                    1,
                )

            self.assertEqual(shown.returncode, 0, shown.stdout)
            self.assertEqual(
                json.loads(shown.stdout)["data"]["verification_evidence"][
                    "gate"
                ]["blocking_code"],
                "verification_receipt_blocking",
            )
            self.assertNotEqual(receipt.returncode, 0)
            self.assertEqual(
                json.loads(receipt.stdout)["errors"][0]["code"],
                "evidence_basis_stale",
            )


if __name__ == "__main__":
    unittest.main()
