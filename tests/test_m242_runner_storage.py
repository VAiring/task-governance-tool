from __future__ import annotations

import os
import sqlite3
import json
import tempfile
import unittest
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.verification_receipt_test_support import (
    FINGERPRINT_A,
    FINGERPRINT_B,
    add_receipt,
    completion as complete_task,
    initialize,
    run_taskgov,
    target_for,
)
from tests.m14_test_support import make_physical_install, tree_snapshot

from task_governance_tool import reviews as review_service
from task_governance_tool import backup as backup_service
from task_governance_tool import setup as setup_service
from task_governance_tool import state_resolver as state_resolver_module
from task_governance_tool import storage
from task_governance_tool.artifact_manifest import opaque_artifact_observation
from task_governance_tool.evidence_ledger import (
    EvidenceSource,
    TargetCaptureBinding,
    build_evidence_reference,
)
from task_governance_tool.tasks import (
    TaskRepositoryError,
    read_internal_task,
    validate_current_stored_task_rows,
)
from task_governance_tool.state_resolver import (
    canonical_state_paths,
    resolve_project_state,
    resolve_setup_project_state,
)
from task_governance_tool.state_paths import VerificationRunnerStatePaths
from task_governance_tool import verification_runner_lifecycle as runner_lifecycle
from task_governance_tool.verification_runner import (
    resolution_idempotency_digest,
    verification_runner_attempt_digest,
    verification_runner_observation_digest,
    verification_runner_policy_digest,
    verification_runner_sandbox_event_digest,
)
from task_governance_tool.verification_runner_service import (
    _publish_recovery_terminal,
    _runner_paths,
    reconcile_pending_verification_runner_cleanup_under_lock,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-09T00:00:00Z"
LATER = "2026-08-09T00:00:01Z"


def labeled(char: str) -> str:
    return "sha256:" + char * 64


def sealed_resolution(
    value: storage.VerificationRunnerResolution,
) -> storage.VerificationRunnerResolution:
    values = asdict(value)
    values.pop("verification_runner_resolution_id")
    values.pop("idempotency_digest")
    values.pop("created_at")
    return replace(value, idempotency_digest=resolution_idempotency_digest(values))


def sealed_attempt(
    value: storage.VerificationRunnerAttempt,
) -> storage.VerificationRunnerAttempt:
    values = asdict(value)
    values.pop("verification_runner_attempt_id")
    values.pop("attempt_digest")
    values.pop("intent_recorded_at")
    values["resolution_id"] = values.pop("verification_runner_resolution_id")
    return replace(value, attempt_digest=verification_runner_attempt_digest(values))


def sealed_event(
    value: storage.VerificationRunnerSandboxEvent,
) -> storage.VerificationRunnerSandboxEvent:
    values = asdict(value)
    values.pop("verification_runner_sandbox_event_id")
    values.pop("event_digest")
    values.pop("created_at")
    values["attempt_id"] = values.pop("verification_runner_attempt_id")
    return replace(value, event_digest=verification_runner_sandbox_event_digest(values))


def sealed_observation(
    value: storage.VerificationRunnerObservation,
) -> storage.VerificationRunnerObservation:
    values = asdict(value)
    values.pop("verification_runner_observation_id")
    values.pop("sanitized_result_digest")
    values.pop("created_at")
    values["resolution_id"] = values.pop("verification_runner_resolution_id")
    values["attempt_id"] = values.pop("verification_runner_attempt_id")
    return replace(
        value,
        sanitized_result_digest=verification_runner_observation_digest(values),
    )


def pre_fix_runner_reference_projection(
    resolution: storage.VerificationRunnerResolution,
    observation: storage.VerificationRunnerObservation,
) -> dict[str, object]:
    return {
        "observation_id": observation.verification_runner_observation_id,
        "gate_eligibility_version": observation.gate_eligibility_version,
        "route": observation.route,
        "reason": observation.reason,
        "outcome": observation.outcome,
        "launch_state": observation.launch_state,
        "complete_plan": observation.complete_plan,
        "total_step_count": observation.total_step_count,
        "completed_step_count": observation.completed_step_count,
        "failed_step_ordinal": observation.failed_step_ordinal,
        "started_at": observation.started_at,
        "finished_at": observation.finished_at,
        "duration_ms": observation.duration_ms,
        "cpu_time_ms": observation.cpu_time_ms,
        "peak_job_memory_bytes": observation.peak_job_memory_bytes,
        "total_process_count": observation.total_process_count,
        "plan_blob_object_id": resolution.plan_blob_object_id,
        "plan_raw_digest": resolution.plan_raw_digest,
        "plan_id": resolution.plan_id,
        "plan_version": resolution.plan_version,
        "plan_semantic_digest": resolution.plan_semantic_digest,
        "runner_implementation_version": resolution.runner_implementation_version,
        "runner_implementation_digest": resolution.runner_implementation_digest,
        "runner_policy_digest": resolution.runner_policy_digest,
        "sandbox_provider": resolution.sandbox_provider,
        "sandbox_policy_digest": resolution.sandbox_policy_digest,
        "runtime_digest": resolution.runtime_digest,
        "sanitized_result_digest": observation.sanitized_result_digest,
    }


class VerificationRunnerStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix=".tmp-m242-storage-",
            dir=ROOT,
        )
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _seed_current_target(self) -> tuple[Path, Path, storage.DatabaseTarget, str]:
        repo, db = initialize(self.root)
        result = run_taskgov(
            "task",
            "add",
            "--repo",
            str(repo),
            "--db",
            str(db),
            "--title",
            "M24.2 Runner storage",
            "--status",
            "in_progress",
            "--review-tier",
            "0",
            "--verification",
            "python -m unittest tests.test_m242_runner_storage",
            "--contract-scope",
            "Persist the schema-v20 Runner shadow foundation.",
            "--contract-acceptance",
            "Runner records are immutable, atomic, and projection-safe.",
            "--contract-authority-ref",
            "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-2",
            "--json",
        )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        task = json.loads(result.stdout)["data"]["task"]
        target = target_for(db, repo)
        task_id = str(task["task_id"])
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            internal = read_internal_task(
                connection,
                target.project.project_id,
                task_id,
            )
            self.assertIsNotNone(internal)
            review_service._persist_review_target_capture(
                connection,
                target.project,
                internal,
                observation=opaque_artifact_observation(
                    target_kind="diff_fingerprint",
                    target_value=FINGERPRINT_A,
                ),
                generation=1,
                now=NOW,
            )
            connection.commit()
        return repo, db, target, task_id

    def test_schema_v19_projection_row_hydrates_runner_columns_as_null(
        self,
    ) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory = sqlite3.Row
            legacy_bundle_row = connection.execute(
                "SELECT ? AS completion_evidence_bundle_id",
                ("tg_completion_evidence_bundle_" + "a" * 16,),
            ).fetchone()
            self.assertIsNotNone(legacy_bundle_row)
            self.assertIsNone(
                storage._optional_projection_row_value(
                    legacy_bundle_row,
                    "verification_basis_kind",
                )
            )
            self.assertIsNone(
                storage._optional_projection_row_value(
                    legacy_bundle_row,
                    "verification_runner_observation_id",
                )
            )

    def _resolution(
        self,
        connection: sqlite3.Connection,
        target: storage.DatabaseTarget,
        task_id: str,
        *,
        runner: bool = False,
    ) -> storage.VerificationRunnerResolution:
        basis = storage.read_current_verification_runner_target_basis(
            connection,
            project_id=target.project.project_id,
            task_id=task_id,
        )
        if basis["verification_criterion_id"] is None:
            self.fail("the focused fixture did not create a verification criterion")
        return sealed_resolution(storage.VerificationRunnerResolution(
            verification_runner_resolution_id=(
                "tg_verification_runner_resolution_" + "1" * 16
            ),
            project_id=target.project.project_id,
            task_id=task_id,
            contract_revision=basis["contract_revision"],
            authority_snapshot_id=basis["authority_snapshot_id"],
            verification_criterion_id=basis["verification_criterion_id"],
            verification_expectation_digest=basis[
                "verification_expectation_digest"
            ],
            verification_criterion_digest=basis["verification_criterion_digest"],
            target_kind=basis["target_kind"],
            target_value=basis["target_value"],
            target_base_revision=basis["target_base_revision"],
            target_generation=basis["target_generation"],
            target_capture_version=basis["target_capture_version"],
            artifact_manifest_id=basis["artifact_manifest_id"],
            target_material_digest=labeled("2") if runner else None,
            plan_state="runner" if runner else "absent",
            plan_blob_object_id="3" * 40 if runner else None,
            plan_raw_digest=labeled("4") if runner else None,
            plan_id="focused" if runner else None,
            plan_version=1 if runner else None,
            plan_semantic_digest=labeled("5") if runner else None,
            selected_entry_digest=labeled("6") if runner else None,
            coverage="complete" if runner else "not_applicable",
            step_count=1 if runner else 0,
            runner_contract_version=1,
            runner_implementation_version="taskgov-verification-runner/1",
            runner_implementation_digest=labeled("7"),
            runner_policy_digest=verification_runner_policy_digest(),
            runtime_digest=labeled("a") if runner else None,
            gate_eligibility_version=0,
            trigger="review_target_set_v1",
            route="runner" if runner else "m21_fallback",
            reason=None if runner else "plan_absent",
            idempotency_digest=labeled("b"),
            created_at=NOW,
        ))

    def _observation(
        self,
        resolution: storage.VerificationRunnerResolution,
        *,
        attempt: storage.VerificationRunnerAttempt | None = None,
    ) -> storage.VerificationRunnerObservation:
        launched = attempt is not None
        return sealed_observation(storage.VerificationRunnerObservation(
            verification_runner_observation_id=(
                "tg_verification_runner_observation_" + "c" * 16
            ),
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            target_generation=resolution.target_generation,
            gate_eligibility_version=0,
            verification_runner_resolution_id=(
                resolution.verification_runner_resolution_id
            ),
            verification_runner_attempt_id=(
                attempt.verification_runner_attempt_id
                if attempt is not None
                else None
            ),
            runner_implementation_digest=resolution.runner_implementation_digest,
            route="runner" if launched else "m21_fallback",
            launch_state="launched" if launched else "no_launch",
            outcome="pass" if launched else "not_run",
            reason=None if launched else resolution.reason,
            complete_plan=1 if launched else 0,
            total_step_count=1 if launched else 0,
            completed_step_count=1 if launched else 0,
            failed_step_ordinal=None,
            started_at=NOW,
            finished_at=LATER if launched else NOW,
            duration_ms=1000 if launched else 0,
            cpu_time_ms=10 if launched else None,
            peak_job_memory_bytes=1024 if launched else None,
            total_process_count=1 if launched else None,
            sanitized_result_digest=labeled("c"),
            created_at=LATER if launched else NOW,
        ))

    def _attempt(
        self,
        resolution: storage.VerificationRunnerResolution,
        *,
        suffix: str = "2",
    ) -> storage.VerificationRunnerAttempt:
        attempt_id = "tg_verification_runner_attempt_" + suffix * 16
        return sealed_attempt(storage.VerificationRunnerAttempt(
            verification_runner_attempt_id=attempt_id,
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            target_generation=resolution.target_generation,
            gate_eligibility_version=resolution.gate_eligibility_version,
            verification_runner_resolution_id=(
                resolution.verification_runner_resolution_id
            ),
            target_material_digest=resolution.target_material_digest or "",
            runner_implementation_digest=resolution.runner_implementation_digest,
            attempt_digest=labeled("e"),
            intent_recorded_at=NOW,
        ))

    def _event(
        self,
        attempt: storage.VerificationRunnerAttempt,
        *,
        suffix: str,
        event_kind: str,
        terminal_observation_id: str | None = None,
        created_at: str = NOW,
    ) -> storage.VerificationRunnerSandboxEvent:
        return sealed_event(storage.VerificationRunnerSandboxEvent(
            verification_runner_sandbox_event_id=(
                "tg_verification_runner_sandbox_event_" + suffix * 16
            ),
            project_id=attempt.project_id,
            task_id=attempt.task_id,
            target_generation=attempt.target_generation,
            verification_runner_attempt_id=attempt.verification_runner_attempt_id,
            event_kind=event_kind,
            event_digest=labeled(suffix),
            terminal_observation_id=terminal_observation_id,
            created_at=created_at,
        ))

    def _terminal_rows(
        self,
        connection: sqlite3.Connection,
        resolution: storage.VerificationRunnerResolution,
        observation: storage.VerificationRunnerObservation,
        *,
        reference_id: str = "tg_evidence_reference_" + "d" * 16,
        source_projection: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], storage.PreparedCriterionEvidenceLink]:
        basis = storage.read_current_verification_runner_target_basis(
            connection,
            project_id=resolution.project_id,
            task_id=resolution.task_id,
        )
        binding = TargetCaptureBinding(
            target_kind=resolution.target_kind,
            target_value=resolution.target_value,
            target_base_revision=resolution.target_base_revision or "",
            target_generation=resolution.target_generation,
            authority_snapshot_id=resolution.authority_snapshot_id,
            acceptance_criterion_id=basis["acceptance_criterion_id"],
            verification_criterion_id=resolution.verification_criterion_id,
        )
        source = EvidenceSource(
            source_kind="runner_observation",
            source_state="recorded",
            source_id=observation.verification_runner_observation_id,
            source_projection=(
                source_projection
                if source_projection is not None
                else storage._verification_runner_reference_source_projection(
                    resolution, observation
                )
            ),
        )
        spec = build_evidence_reference(
            source=source,
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            contract_revision=resolution.contract_revision,
            binding=binding,
        )
        reference: dict[str, object] = {
            "evidence_reference_id": reference_id,
            "project_id": resolution.project_id,
            "task_id": resolution.task_id,
            "source_kind": "runner_observation",
            "source_state": "recorded",
            "source_id": observation.verification_runner_observation_id,
            "assurance_class": spec.attribution.assurance_class,
            "producer_class": spec.attribution.producer_class,
            "producer_version": spec.attribution.producer_version,
            "contract_revision": resolution.contract_revision,
            "authority_snapshot_id": resolution.authority_snapshot_id,
            "acceptance_criterion_id": basis["acceptance_criterion_id"],
            "verification_criterion_id": resolution.verification_criterion_id,
            "target_kind": resolution.target_kind,
            "target_value": resolution.target_value,
            "target_base_revision": resolution.target_base_revision or "",
            "target_generation": resolution.target_generation,
            "completion_cycle_id": None,
            "digest": spec.digest,
            "created_at": observation.created_at,
        }
        link = storage.PreparedCriterionEvidenceLink(
            criterion_evidence_link_id=(
                "tg_criterion_evidence_link_" + "e" * 16
            ),
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            criterion_id=resolution.verification_criterion_id,
            evidence_reference_id=reference_id,
            relation="runner_observation",
            assurance_class="machine_observed",
            producer_class="verification_runner",
            producer_version=1,
            created_at=observation.created_at,
        )
        return reference, link

    def _seed_pending_attempt(
        self,
        db: Path,
        target: storage.DatabaseTarget,
        task_id: str,
    ) -> tuple[
        storage.VerificationRunnerResolution,
        storage.VerificationRunnerAttempt,
    ]:
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            connection.commit()
        return resolution, attempt

    def _add_physical_runner_task(self, install) -> str:
        result = install.run(
            "task",
            "add",
            "--title",
            "M24.2 setup cleanup receipt",
            "--status",
            "in_progress",
            "--review-tier",
            "0",
            "--verification",
            "python -m unittest tests.test_m242_runner_storage",
            "--contract-scope",
            "Exercise exact schema-v20 Runner cleanup setup ordering.",
            "--contract-acceptance",
            "Pending cleanup is reconciled before maintenance writes.",
            "--contract-authority-ref",
            "docs/execution-contracts/tg-m24-verification-runner.md#tg-m24-2",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        task_id = str(json.loads(result.stdout)["data"]["task"]["task_id"])
        target = install.target
        with closing(storage.connect(install.db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            internal = read_internal_task(
                connection,
                target.project.project_id,
                task_id,
            )
            self.assertIsNotNone(internal)
            review_service._persist_review_target_capture(
                connection,
                target.project,
                internal,
                observation=opaque_artifact_observation(
                    target_kind="diff_fingerprint",
                    target_value=FINGERPRINT_A,
                ),
                generation=1,
                now=NOW,
            )
            connection.commit()
        return task_id

    def _seed_physical_pending_attempt(
        self,
        install,
        task_id: str,
    ) -> tuple[
        storage.VerificationRunnerResolution,
        storage.VerificationRunnerAttempt,
        VerificationRunnerStatePaths,
    ]:
        target = install.target
        resolution, attempt = self._seed_pending_attempt(
            install.db_path,
            target,
            task_id,
        )
        paths = canonical_state_paths(install.skill_root)
        runner_paths = VerificationRunnerStatePaths(
            root=paths.verification_runner_root,
            lock=paths.verification_runner_lock,
            attempts=paths.verification_runner_attempts,
            quarantine=paths.verification_runner_quarantine,
        )
        runner_lifecycle.ensure_runner_layout(runner_paths)
        runner_lifecycle.create_attempt_directories(
            runner_paths,
            attempt.verification_runner_attempt_id,
        )
        return resolution, attempt, runner_paths

    def _publish_cleanup_terminal(
        self,
        db: Path,
        resolution: storage.VerificationRunnerResolution,
        attempt: storage.VerificationRunnerAttempt,
    ) -> None:
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            observation = self._observation(resolution, attempt=attempt)
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup,
            )
            connection.commit()

    def test_v19_migration_is_rollback_safe_preserving_and_reentrant(self) -> None:
        repo = self.root / "repo"
        db = self.root / "state" / "taskgov.sqlite"
        repo.mkdir()
        with (
            mock.patch.object(storage, "SCHEMA_VERSION", 19),
            mock.patch.object(storage, "validate_current_database", return_value=19),
        ):
            from tests.m14_test_support import initialize_taskgov_internal

            initialize_taskgov_internal(repo=repo, db=db)

        with closing(storage.connect(db)) as connection:
            self.assertEqual(storage.current_schema_version(connection), 19)
            before = storage._selected_table_projection_snapshot(
                connection,
                tuple(
                    name
                    for name, introduced in storage._SCHEMA_TABLE_INTRODUCED_VERSION.items()
                    if name != "schema_migrations" and introduced <= 19
                ),
            )
            old_columns = {name: projection[0] for name, projection in before.items()}
            with self.assertRaises(storage.StorageError):
                storage.apply_verification_runner_shadow_migration(
                    connection,
                    fail_stage="after_bundle_swap",
                )
            self.assertEqual(storage.current_schema_version(connection), 19)
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'verification_runner_resolutions'"
                ).fetchone()
            )

            storage.apply_verification_runner_shadow_migration(connection)
            after = storage._selected_table_projection_snapshot(
                connection,
                tuple(before),
                column_basis=old_columns,
            )
            self.assertEqual(after, before)
            storage.apply_verification_runner_shadow_migration(connection)
            storage.validate_verification_runner_storage(connection)
            self.assertEqual(storage.current_schema_version(connection), 20)

    def test_schema_marker_matrix_and_immutable_resolution(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            expected = {
                "verification_runner_resolutions": storage.VerificationRunnerResolution,
                "verification_runner_attempts": storage.VerificationRunnerAttempt,
                "verification_runner_sandbox_events": storage.VerificationRunnerSandboxEvent,
                "verification_runner_observations": storage.VerificationRunnerObservation,
            }
            for table, record_type in expected.items():
                columns = tuple(
                    str(row["name"])
                    for row in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    ).fetchall()
                )
                self.assertEqual(columns, tuple(record_type.__dataclass_fields__))
            task_marker = next(
                row
                for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
                if row["name"] == "review_target_runner_basis_version"
            )
            self.assertEqual((task_marker["notnull"], task_marker["dflt_value"]), (1, "0"))
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE tasks SET review_target_runner_basis_version = 1 "
                    "WHERE task_id = ?",
                    (task_id,),
                )
            connection.execute(
                "UPDATE tasks SET review_target_runner_basis_version = 2 "
                "WHERE task_id = ?",
                (task_id,),
            )
            with self.assertRaises(storage.StorageError):
                storage.read_current_verification_runner_target_basis(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )
            connection.execute(
                "UPDATE tasks SET review_target_runner_basis_version = 0 "
                "WHERE task_id = ?",
                (task_id,),
            )
            resolution = self._resolution(connection, target, task_id)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            with self.assertRaises(storage.StorageError):
                storage.insert_verification_runner_resolution_locked(
                    connection,
                    resolution=replace(
                        resolution,
                        verification_runner_resolution_id=(
                            "tg_verification_runner_resolution_" + "2" * 16
                        ),
                        gate_eligibility_version=2,
                        idempotency_digest=labeled("d"),
                    ),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE verification_runner_resolutions SET reason = reason "
                    "WHERE verification_runner_resolution_id = ?",
                    (resolution.verification_runner_resolution_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM verification_runner_resolutions "
                    "WHERE verification_runner_resolution_id = ?",
                    (resolution.verification_runner_resolution_id,),
                )
            connection.rollback()

    def test_bundle_v20_tagged_union_and_cycle_columns_are_foundational(self) -> None:
        _repo, db = initialize(self.root)
        with closing(storage.connect(db)) as connection:
            cycle_columns = {
                str(row["name"]): int(row["notnull"])
                for row in connection.execute(
                    "PRAGMA table_info(task_completion_cycles)"
                ).fetchall()
            }
            self.assertEqual(cycle_columns["verification_basis_kind"], 0)
            self.assertEqual(
                cycle_columns["verification_runner_observation_id"],
                0,
            )

        with closing(sqlite3.connect(":memory:")) as probe:
            probe.execute("PRAGMA foreign_keys = OFF")
            probe.execute(storage.completion_evidence_bundle_v20_table_sql())
            columns = (
                "completion_evidence_bundle_id", "project_id", "task_id",
                "completion_cycle_id", "cycle_ordinal", "source_schema_version",
                "bundle_version", "contract_revision", "authority_snapshot_id",
                "acceptance_criterion_id", "verification_criterion_id",
                "target_kind", "target_value", "target_base_revision",
                "target_generation", "target_capture_version",
                "artifact_manifest_id", "verification_receipt_id",
                "verification_basis_kind", "verification_runner_observation_id",
                "omission_mask", "sealed_at", "bundle_digest",
                "payload_size_bytes",
            )
            base = {
                "completion_evidence_bundle_id": (
                    "tg_completion_evidence_bundle_" + "1" * 16
                ),
                "project_id": "project",
                "task_id": "task",
                "completion_cycle_id": "tg_completion_cycle_" + "2" * 16,
                "cycle_ordinal": 1,
                "source_schema_version": 19,
                "bundle_version": 1,
                "contract_revision": 1,
                "authority_snapshot_id": "tg_authority_snapshot_" + "3" * 16,
                "acceptance_criterion_id": None,
                "verification_criterion_id": None,
                "target_kind": "diff_fingerprint",
                "target_value": FINGERPRINT_A,
                "target_base_revision": "",
                "target_generation": 1,
                "target_capture_version": 1,
                "artifact_manifest_id": "tg_artifact_manifest_" + "4" * 16,
                "verification_receipt_id": None,
                "verification_basis_kind": None,
                "verification_runner_observation_id": None,
                "omission_mask": 0,
                "sealed_at": NOW,
                "bundle_digest": labeled("5"),
                "payload_size_bytes": 1,
            }

            def insert(values: dict[str, object]) -> None:
                probe.execute(
                    "INSERT INTO completion_evidence_bundles("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    tuple(values[name] for name in columns),
                )

            insert(base)
            probe.execute("DELETE FROM completion_evidence_bundles")
            for ordinal, basis_values in enumerate(
                (
                    {
                        "verification_basis_kind": "caller_attestation",
                        "verification_receipt_id": (
                            "tg_verification_receipt_" + "6" * 16
                        ),
                    },
                    {"verification_basis_kind": "not_required"},
                    {
                        "verification_basis_kind": "runner_observation",
                        "verification_runner_observation_id": (
                            "tg_verification_runner_observation_" + "7" * 16
                        ),
                    },
                ),
                start=1,
            ):
                candidate = {
                    **base,
                    "completion_evidence_bundle_id": (
                        "tg_completion_evidence_bundle_" + f"{ordinal + 1:x}" * 16
                    ),
                    "source_schema_version": 20,
                    "bundle_version": 2,
                    **basis_values,
                }
                insert(candidate)
                probe.execute("DELETE FROM completion_evidence_bundles")
            invalid = {
                **base,
                "completion_evidence_bundle_id": (
                    "tg_completion_evidence_bundle_" + "f" * 16
                ),
                "source_schema_version": 20,
                "bundle_version": 2,
                "verification_basis_kind": "runner_observation",
            }
            with self.assertRaises(sqlite3.IntegrityError):
                insert(invalid)
            invalid_legacy = {
                **base,
                "completion_evidence_bundle_id": (
                    "tg_completion_evidence_bundle_" + "e" * 16
                ),
                "verification_basis_kind": "not_required",
            }
            with self.assertRaises(sqlite3.IntegrityError):
                insert(invalid_legacy)

    def test_direct_fallback_terminal_is_atomic_valid_and_public(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            observation = self._observation(resolution)
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            stored = storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
            )
            self.assertEqual(stored, observation)
            storage.validate_verification_runner_storage(connection)
            storage.validate_evidence_ledger_storage(connection)
            projection = storage.read_verification_runner_public_projection(
                connection,
                project_id=target.project.project_id,
                task_id=task_id,
            )
            self.assertEqual(
                projection,
                {
                    "eligibility": "shadow",
                    "observation_id": observation.verification_runner_observation_id,
                    "outcome": "not_run",
                    "phase": "shadow",
                    "reason": "plan_absent",
                    "route": "m21_fallback",
                    "schema_version": 1,
                    "target_generation": 1,
                },
            )
            connection.commit()

    def test_selected_runner_provider_fallback_uses_zero_step_terminal(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = self._resolution(connection, target, task_id, runner=True)
            resolution = sealed_resolution(replace(
                candidate,
                route="m21_fallback",
                reason="sandbox_unavailable",
                sandbox_provider=None,
                sandbox_policy_digest=None,
                runtime_digest=None,
            ))
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            observation = self._observation(resolution)
            self.assertEqual(observation.total_step_count, 0)
            self.assertEqual(observation.reason, "sandbox_unavailable")
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
            )
            storage.validate_verification_runner_storage(connection)
            connection.commit()

    def test_candidate_fallback_retains_attempt_and_requires_cleanup_proof(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            observation = sealed_observation(replace(
                self._observation(resolution, attempt=attempt),
                route="m21_fallback",
                launch_state="no_launch",
                outcome="not_run",
                reason="sandbox_unavailable",
                complete_plan=0,
                total_step_count=0,
                completed_step_count=0,
                failed_step_ordinal=None,
                started_at=NOW,
                finished_at=NOW,
                duration_ms=0,
                cpu_time_ms=None,
                peak_job_memory_bytes=None,
                total_process_count=None,
                created_at=NOW,
            ))
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            with self.assertRaises(storage.StorageError):
                storage.persist_verification_runner_terminal_locked(
                    connection,
                    observation=observation,
                    evidence_reference=reference,
                    criterion_link=link,
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_observations"
                ).fetchone()[0],
                0,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            stored = storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup,
            )
            self.assertEqual(
                stored.verification_runner_attempt_id,
                attempt.verification_runner_attempt_id,
            )
            self.assertEqual(
                (stored.route, stored.launch_state, stored.outcome),
                ("m21_fallback", "no_launch", "not_run"),
            )
            storage.validate_verification_runner_storage(connection)
            connection.commit()

    def test_attempt_cleanup_and_terminal_commit_as_one_publication(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            self.assertTrue(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=resolution.project_id,
                )
            )
            pending = storage.read_pending_verification_runner_cleanup(
                connection,
                project_id=resolution.project_id,
            )
            self.assertEqual(len(pending), 1)
            self.assertIsNone(pending[0]["terminal_observation"])
            storage.validate_verification_runner_storage(connection)
            observation = self._observation(resolution, attempt=attempt)
            pre_fix_projection = pre_fix_runner_reference_projection(
                resolution,
                observation,
            )
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
                source_projection=pre_fix_projection,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup,
            )
            self.assertFalse(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=resolution.project_id,
                )
            )
            reference_projection = (
                storage._verification_runner_reference_source_projection(
                    resolution,
                    observation,
                )
            )
            self.assertEqual(reference_projection, pre_fix_projection)
            bundle_projection = storage._verification_runner_bundle_projection(
                resolution,
                observation,
            )
            stable_binding_keys = {
                "attempt_id",
                "resolution_id",
                "project_id",
                "task_id",
                "target_generation",
            }
            self.assertTrue(stable_binding_keys.isdisjoint(reference_projection))
            self.assertEqual(
                set(bundle_projection),
                set(reference_projection) | stable_binding_keys,
            )
            self.assertEqual(
                {
                    key: bundle_projection[key]
                    for key in (
                        "attempt_id",
                        "resolution_id",
                        "project_id",
                        "task_id",
                        "target_generation",
                    )
                },
                {
                    "attempt_id": attempt.verification_runner_attempt_id,
                    "resolution_id": resolution.verification_runner_resolution_id,
                    "project_id": resolution.project_id,
                    "task_id": resolution.task_id,
                    "target_generation": resolution.target_generation,
                },
            )
            storage.validate_verification_runner_storage(connection)
            storage.validate_evidence_ledger_storage(connection)
            connection.commit()

    def test_lost_cleanup_event_fails_individual_public_bundle_and_full_reads(
        self,
    ) -> None:
        repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            cleanup_plan_details = tuple(
                str(row["detail"])
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + storage._VERIFICATION_RUNNER_CLEANUP_EVENT_BY_ATTEMPT_QUERY,
                    (
                        resolution.project_id,
                        resolution.task_id,
                        attempt.verification_runner_attempt_id,
                    ),
                ).fetchall()
            )
            self.assertEqual(len(cleanup_plan_details), 1)
            self.assertIn(
                "USING INDEX "
                "idx_verification_runner_sandbox_events_attempt_kind",
                cleanup_plan_details[0],
            )
            self.assertNotIn("SCAN", cleanup_plan_details[0].upper())
            observation = self._observation(resolution, attempt=attempt)
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup,
            )
            storage.validate_verification_runner_storage(connection)
            connection.commit()

            trigger_name = "trg_verification_runner_sandbox_events_no_delete"
            trigger_row = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name = ?",
                (trigger_name,),
            ).fetchone()
            self.assertIsNotNone(trigger_row)
            trigger_sql = str(trigger_row["sql"])
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"DROP TRIGGER {trigger_name}")
            deleted = connection.execute(
                "DELETE FROM verification_runner_sandbox_events "
                "WHERE verification_runner_sandbox_event_id = ?",
                (cleanup.verification_runner_sandbox_event_id,),
            )
            self.assertEqual(deleted.rowcount, 1)
            connection.execute(trigger_sql)
            connection.commit()

            task_row = connection.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
                (observation.project_id, observation.task_id),
            ).fetchone()
            self.assertIsNotNone(task_row)
            contract_row = connection.execute(
                "SELECT * FROM task_contract_revisions "
                "WHERE project_id = ? AND task_id = ? AND revision = ?",
                (
                    observation.project_id,
                    observation.task_id,
                    task_row["current_contract_revision"],
                ),
            ).fetchone()
            self.assertIsNotNone(contract_row)

            readers = (
                lambda: storage.read_verification_runner_observation_locked(
                    connection,
                    project_id=observation.project_id,
                    task_id=observation.task_id,
                    target_generation=observation.target_generation,
                ),
                lambda: storage.read_verification_runner_public_projection(
                    connection,
                    project_id=observation.project_id,
                    task_id=observation.task_id,
                ),
                lambda: storage.read_pending_verification_runner_cleanup(
                    connection,
                    project_id=observation.project_id,
                ),
                lambda: storage.validate_completion_evidence_bundle_storage(
                    connection
                ),
                lambda: storage.validate_evidence_ledger_storage(connection),
                lambda: validate_current_stored_task_rows(
                    connection,
                    (task_row,),
                    expected_project_id=observation.project_id,
                ),
                lambda: storage.validate_selected_task_authority_storage(
                    connection,
                    (task_row,),
                    expected_project_id=observation.project_id,
                    current_contract_rows={observation.task_id: contract_row},
                ),
                lambda: storage.validate_verification_runner_storage(connection),
            )
            for reader in readers:
                with self.subTest(reader=reader):
                    with self.assertRaises(storage.StorageError):
                        reader()

            cli_commands = (
                ("task", "list"),
                ("task", "current"),
            )
            for command in cli_commands:
                with self.subTest(command=command):
                    before_cli = db.read_bytes()
                    result = run_taskgov(
                        *command,
                        "--repo",
                        str(repo),
                        "--db",
                        str(db),
                        "--json",
                    )
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(
                        json.loads(result.stdout)["errors"],
                        [
                            {
                                "code": "project_state_unreadable",
                                "message": (
                                    "project state could not be read safely"
                                ),
                            }
                        ],
                    )
                    self.assertEqual(db.read_bytes(), before_cli)

            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE tasks SET status = 'ready' "
                "WHERE project_id = ? AND task_id = ?",
                (observation.project_id, observation.task_id),
            )
            connection.commit()
            before_cli = db.read_bytes()
            result = run_taskgov(
                "task",
                "next",
                "--repo",
                str(repo),
                "--db",
                str(db),
                "--json",
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(
                json.loads(result.stdout)["errors"],
                [
                    {
                        "code": "project_state_unreadable",
                        "message": "project state could not be read safely",
                    }
                ],
            )
            self.assertEqual(db.read_bytes(), before_cli)

            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE tasks SET status = 'in_progress' "
                "WHERE project_id = ? AND task_id = ?",
                (observation.project_id, observation.task_id),
            )
            connection.commit()

            connection.execute("BEGIN IMMEDIATE")
            storage.insert_verification_runner_sandbox_event_locked(
                connection,
                event=cleanup,
            )
            storage.validate_completion_evidence_bundle_storage(connection)
            storage.validate_evidence_ledger_storage(connection)
            storage.validate_verification_runner_storage(connection)
            validate_current_stored_task_rows(
                connection,
                (task_row,),
                expected_project_id=observation.project_id,
            )
            connection.commit()

    def test_historical_recovery_terminal_skips_only_current_basis_check(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            observation = sealed_observation(replace(
                self._observation(resolution, attempt=attempt),
                route="blocked",
                launch_state="no_launch",
                outcome="blocked_prelaunch",
                reason="sandbox_setup_failed",
                complete_plan=0,
                completed_step_count=0,
                started_at=NOW,
                finished_at=NOW,
                duration_ms=0,
                cpu_time_ms=None,
                peak_job_memory_bytes=None,
                total_process_count=None,
                created_at=NOW,
            ))
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            connection.commit()

        # Simulate a state created by the pre-fix writer race: the immutable
        # attempt survived, but a Contract/target invalidation became current.
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE tasks
                   SET review_target_kind = '',
                       review_target_value = '',
                       review_target_base_revision = '',
                       review_target_generation = 2,
                       review_target_capture_version = 0,
                       review_target_authority_snapshot_id = NULL,
                       review_target_acceptance_criterion_id = NULL,
                       review_target_verification_criterion_id = NULL,
                       review_target_artifact_manifest_id = NULL,
                       updated_at = ?
                 WHERE project_id = ? AND task_id = ?
                """,
                (LATER, resolution.project_id, task_id),
            )
            connection.commit()

        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(storage.StorageError):
                storage.persist_verification_runner_terminal_locked(
                    connection,
                    observation=observation,
                    evidence_reference=reference,
                    criterion_link=link,
                    cleanup_event=cleanup,
                )
            connection.rollback()

        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(storage.StorageError):
                storage.persist_verification_runner_recovery_terminal_locked(
                    connection,
                    observation=replace(
                        observation,
                        sanitized_result_digest=labeled("f"),
                    ),
                    evidence_reference=reference,
                    criterion_link=link,
                    cleanup_event=cleanup,
                )
            connection.rollback()

        _publish_recovery_terminal(
            target,
            resolution,
            attempt,
            observation,
            cleanup_proved=True,
        )
        with closing(storage.connect(db)) as connection:
            self.assertFalse(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=resolution.project_id,
                )
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_observations "
                    "WHERE verification_runner_observation_id = ?",
                    (observation.verification_runner_observation_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM evidence_references "
                    "WHERE source_kind = 'runner_observation' AND source_id = ?",
                    (observation.verification_runner_observation_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM criterion_evidence_links "
                    "WHERE relation = 'runner_observation'",
                ).fetchone()[0],
                1,
            )
            storage.validate_verification_runner_storage(connection)
            storage.validate_evidence_ledger_storage(connection)

    def test_pending_cleanup_rejects_contract_and_review_target_mutation(self) -> None:
        repo, db, target, task_id = self._seed_current_target()
        resolution, _attempt = self._seed_pending_attempt(db, target, task_id)

        revised = run_taskgov(
            "task",
            "edit",
            "--repo",
            str(repo),
            "--db",
            str(db),
            task_id,
            "--contract-scope",
            "Must not invalidate the target during pending cleanup.",
            "--contract-acceptance",
            "Cleanup completes before target mutation.",
            "--contract-authority-ref",
            f"user_instruction:{task_id}:2",
            "--contract-change-reason",
            "Exercise the pending-cleanup guard.",
            "--json",
        )
        self.assertNotEqual(revised.returncode, 0, revised.stdout)
        self.assertEqual(
            json.loads(revised.stdout)["errors"][0]["code"],
            "runner_cleanup_required",
        )

        with closing(storage.connect(db)) as connection:
            with self.assertRaises(TaskRepositoryError) as raised:
                review_service.set_review_target(
                    connection,
                    target.project,
                    task_id,
                    kind="diff_fingerprint",
                    revision=FINGERPRINT_B,
                    database_target=target,
                )
            self.assertEqual(raised.exception.code, "runner_cleanup_required")

        with closing(storage.connect(db)) as connection:
            task = read_internal_task(
                connection,
                target.project.project_id,
                task_id,
            )
            self.assertEqual(task["current_contract_revision"], 1)
            self.assertEqual(task["review_target_value"], FINGERPRINT_A)
            self.assertTrue(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=resolution.project_id,
                )
            )

    def test_repository_rejects_prepared_noncanonical_runner_seals(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            with self.assertRaises(storage.StorageError):
                storage.insert_verification_runner_resolution_locked(
                    connection,
                    resolution=replace(
                        resolution,
                        idempotency_digest=labeled("f"),
                    ),
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_resolutions"
                ).fetchone()[0],
                0,
            )
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )

            attempt = self._attempt(resolution)
            forged_sandbox = sealed_attempt(
                replace(attempt, sandbox_instance_digest=labeled("f"))
            )
            with self.assertRaises(storage.StorageError):
                storage.insert_verification_runner_attempt_locked(
                    connection,
                    attempt=forged_sandbox,
                )
            with self.assertRaises(storage.StorageError):
                storage.insert_verification_runner_attempt_locked(
                    connection,
                    attempt=replace(attempt, attempt_digest=labeled("f")),
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_attempts"
                ).fetchone()[0],
                0,
            )
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )

            observation = self._observation(resolution, attempt=attempt)
            forged_observation = replace(
                observation,
                sanitized_result_digest=labeled("f"),
            )
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            with self.assertRaises(storage.StorageError):
                storage.persist_verification_runner_terminal_locked(
                    connection,
                    observation=forged_observation,
                    evidence_reference=reference,
                    criterion_link=link,
                )

            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            with self.assertRaises(storage.StorageError):
                storage.persist_verification_runner_terminal_locked(
                    connection,
                    observation=observation,
                    evidence_reference=reference,
                    criterion_link=link,
                    cleanup_event=replace(cleanup, event_digest=labeled("f")),
                )
            counts = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM verification_runner_observations
                    WHERE project_id = ? AND task_id = ?) AS observations,
                  (SELECT COUNT(*) FROM evidence_references
                    WHERE project_id = ? AND task_id = ?
                      AND source_kind = 'runner_observation') AS runner_references,
                  (SELECT COUNT(*) FROM criterion_evidence_links
                    WHERE project_id = ? AND task_id = ?
                      AND relation = 'runner_observation') AS links
                """,
                (
                    resolution.project_id,
                    resolution.task_id,
                    resolution.project_id,
                    resolution.task_id,
                    resolution.project_id,
                    resolution.task_id,
                ),
            ).fetchone()
            self.assertEqual(tuple(counts), (0, 0, 0))
            connection.rollback()

    def test_direct_sql_digest_tamper_fails_all_runner_readers_and_validation(
        self,
    ) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            observation = self._observation(resolution, attempt=attempt)
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup,
            )

            cases = (
                (
                    "verification_runner_resolutions",
                    "idempotency_digest",
                    "verification_runner_resolution_id",
                    resolution.verification_runner_resolution_id,
                    lambda: storage.read_verification_runner_resolution_locked(
                        connection,
                        project_id=resolution.project_id,
                        task_id=resolution.task_id,
                        target_generation=resolution.target_generation,
                    ),
                ),
                (
                    "verification_runner_attempts",
                    "sandbox_instance_digest",
                    "verification_runner_attempt_id",
                    attempt.verification_runner_attempt_id,
                    lambda: storage.read_verification_runner_attempt_ids(
                        connection,
                        project_id=attempt.project_id,
                    ),
                ),
                (
                    "verification_runner_attempts",
                    "attempt_digest",
                    "verification_runner_attempt_id",
                    attempt.verification_runner_attempt_id,
                    lambda: storage.read_verification_runner_attempt_locked(
                        connection,
                        project_id=attempt.project_id,
                        task_id=attempt.task_id,
                        target_generation=attempt.target_generation,
                    ),
                ),
                (
                    "verification_runner_observations",
                    "sanitized_result_digest",
                    "verification_runner_observation_id",
                    observation.verification_runner_observation_id,
                    lambda: storage.read_verification_runner_public_projection(
                        connection,
                        project_id=observation.project_id,
                        task_id=observation.task_id,
                    ),
                ),
            )
            for table_name, digest_column, id_column, row_id, reader in cases:
                with self.subTest(table=table_name, column=digest_column):
                    trigger_name = f"trg_{table_name}_no_update"
                    trigger_sql_row = connection.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'trigger' AND name = ?",
                        (trigger_name,),
                    ).fetchone()
                    self.assertIsNotNone(trigger_sql_row)
                    trigger_sql = str(trigger_sql_row["sql"])
                    connection.execute("SAVEPOINT runner_digest_tamper")
                    connection.execute(f"DROP TRIGGER {trigger_name}")
                    connection.execute(
                        f"UPDATE {table_name} SET {digest_column} = ? "
                        f"WHERE {id_column} = ?",
                        (labeled("f"), row_id),
                    )
                    connection.execute(trigger_sql)
                    with self.assertRaises(storage.StorageError):
                        reader()
                    with self.assertRaises(storage.StorageError):
                        storage.validate_verification_runner_storage(connection)
                    if digest_column == "sanitized_result_digest":
                        with self.assertRaises(storage.StorageError):
                            storage.validate_completion_evidence_bundle_storage(
                                connection
                            )
                    connection.execute("ROLLBACK TO SAVEPOINT runner_digest_tamper")
                    connection.execute("RELEASE SAVEPOINT runner_digest_tamper")
            storage.validate_verification_runner_storage(connection)
            connection.rollback()

    def test_fixed_provider_and_policy_digests_reject_resealed_substitutions(
        self,
    ) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            substitutions = (
                {"runner_policy_digest": labeled("f")},
            )
            for changes in substitutions:
                with self.subTest(writer=tuple(changes)):
                    altered = sealed_resolution(replace(resolution, **changes))
                    with self.assertRaises(storage.StorageError):
                        storage.insert_verification_runner_resolution_locked(
                            connection,
                            resolution=altered,
                        )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM verification_runner_resolutions"
                        ).fetchone()[0],
                        0,
                    )

            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            trigger_name = "trg_verification_runner_resolutions_no_update"
            trigger_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND name = ?",
                    (trigger_name,),
                ).fetchone()["sql"]
            )
            for changes in substitutions:
                with self.subTest(reader=tuple(changes)):
                    altered = sealed_resolution(replace(resolution, **changes))
                    connection.execute("SAVEPOINT runner_policy_tamper")
                    connection.execute(f"DROP TRIGGER {trigger_name}")
                    assignments = {
                        **changes,
                        "idempotency_digest": altered.idempotency_digest,
                    }
                    connection.execute(
                        "UPDATE verification_runner_resolutions SET "
                        + ", ".join(f"{name} = :{name}" for name in assignments)
                        + " WHERE verification_runner_resolution_id = :row_id",
                        {
                            **assignments,
                            "row_id": resolution.verification_runner_resolution_id,
                        },
                    )
                    connection.execute(trigger_sql)
                    with self.assertRaises(storage.StorageError):
                        storage.read_verification_runner_resolution_locked(
                            connection,
                            project_id=resolution.project_id,
                            task_id=resolution.task_id,
                            target_generation=resolution.target_generation,
                        )
                    with self.assertRaises(storage.StorageError):
                        storage.validate_verification_runner_storage(connection)
                    connection.execute("ROLLBACK TO SAVEPOINT runner_policy_tamper")
                    connection.execute("RELEASE SAVEPOINT runner_policy_tamper")
            storage.validate_verification_runner_storage(connection)
            connection.rollback()

    def test_resolution_stage_matrix_rejects_impossible_sealed_rows(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            runner = self._resolution(connection, target, task_id, runner=True)
            absent = self._resolution(connection, target, task_id, runner=False)
            invalid_rows = (
                (
                    "explicit_fallback_cannot_be_blocked",
                    sealed_resolution(replace(
                        runner,
                        target_material_digest=None,
                        plan_state="fallback",
                        coverage="complete",
                        step_count=0,
                        sandbox_provider=None,
                        sandbox_policy_digest=None,
                        runtime_digest=None,
                        route="blocked",
                        reason="manual",
                    )),
                ),
                (
                    "invalid_plan_cannot_use_terminal_timeout",
                    sealed_resolution(replace(
                        runner,
                        target_material_digest=None,
                        plan_state="invalid",
                        plan_id=None,
                        plan_version=None,
                        plan_semantic_digest=None,
                        selected_entry_digest=None,
                        coverage="not_applicable",
                        step_count=0,
                        sandbox_provider=None,
                        sandbox_policy_digest=None,
                        runtime_digest=None,
                        route="blocked",
                        reason="timeout",
                    )),
                ),
                (
                    "runner_candidate_cannot_be_resolution_blocked",
                    sealed_resolution(replace(
                        runner,
                        route="blocked",
                        reason="policy_mismatch",
                    )),
                ),
            )
            fields = tuple(storage.VerificationRunnerResolution.__dataclass_fields__)
            for label, invalid in invalid_rows:
                with self.subTest(writer=label):
                    with self.assertRaises(storage.StorageError):
                        storage.insert_verification_runner_resolution_locked(
                            connection,
                            resolution=invalid,
                        )
                with self.subTest(sql_check=label):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO verification_runner_resolutions("
                            + ", ".join(fields)
                            + ") VALUES ("
                            + ", ".join(":" + field for field in fields)
                            + ")",
                            asdict(invalid),
                        )

            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=runner,
            )
            trigger_name = "trg_verification_runner_resolutions_no_update"
            trigger_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND name = ?",
                    (trigger_name,),
                ).fetchone()["sql"]
            )
            original = asdict(runner)
            for label, invalid in invalid_rows:
                with self.subTest(tamper=label):
                    changed = {
                        name: value
                        for name, value in asdict(invalid).items()
                        if value != original[name]
                    }
                    connection.execute("SAVEPOINT runner_resolution_matrix")
                    connection.execute(f"DROP TRIGGER {trigger_name}")
                    connection.execute("PRAGMA ignore_check_constraints = ON")
                    try:
                        connection.execute(
                            "UPDATE verification_runner_resolutions SET "
                            + ", ".join(
                                f"{name} = :{name}" for name in changed
                            )
                            + " WHERE verification_runner_resolution_id = :row_id",
                            {
                                **changed,
                                "row_id": runner.verification_runner_resolution_id,
                            },
                        )
                    finally:
                        connection.execute("PRAGMA ignore_check_constraints = OFF")
                    connection.execute(trigger_sql)
                    with self.assertRaises(storage.StorageError):
                        storage.read_verification_runner_resolution_locked(
                            connection,
                            project_id=runner.project_id,
                            task_id=runner.task_id,
                            target_generation=runner.target_generation,
                        )
                    with self.assertRaises(storage.StorageError):
                        storage.validate_verification_runner_storage(connection)
                    connection.execute("ROLLBACK TO SAVEPOINT runner_resolution_matrix")
                    connection.execute("RELEASE SAVEPOINT runner_resolution_matrix")
            storage.validate_verification_runner_storage(connection)
            connection.rollback()

    def test_terminal_outcome_reason_and_step_ordinal_matrix_fails_closed(
        self,
    ) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            valid = self._observation(resolution, attempt=attempt)
            invalid = (
                (
                    "wrong_reason_and_missing_ordinal",
                    sealed_observation(replace(
                        valid,
                        outcome="fail",
                        reason="timeout",
                        complete_plan=0,
                        failed_step_ordinal=None,
                    )),
                ),
                (
                    "missing_step_ordinal",
                    sealed_observation(replace(
                        valid,
                        outcome="fail",
                        reason="step_nonzero",
                        complete_plan=0,
                        failed_step_ordinal=None,
                    )),
                ),
                (
                    "ordinal_completed_mismatch",
                    sealed_observation(replace(
                        valid,
                        outcome="timeout",
                        reason="timeout",
                        complete_plan=0,
                        completed_step_count=0,
                        failed_step_ordinal=1,
                    )),
                ),
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=valid.verification_runner_observation_id,
                created_at=valid.created_at,
            )
            for label, observation in invalid:
                with self.subTest(writer=label):
                    with self.assertRaises(storage.StorageError):
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference={},
                            criterion_link={},
                            cleanup_event=cleanup,
                        )

                with self.subTest(sql_check=label):
                    connection.execute("SAVEPOINT runner_matrix_insert")
                    storage.insert_verification_runner_sandbox_event_locked(
                        connection,
                        event=cleanup,
                    )
                    row = asdict(observation)
                    fields = tuple(row)
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO verification_runner_observations("
                            + ", ".join(fields)
                            + ") VALUES ("
                            + ", ".join(":" + field for field in fields)
                            + ")",
                            row,
                        )
                    connection.execute("ROLLBACK TO SAVEPOINT runner_matrix_insert")
                    connection.execute("RELEASE SAVEPOINT runner_matrix_insert")

            reference, link = self._terminal_rows(
                connection,
                resolution,
                valid,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=valid,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup,
            )
            trigger_name = "trg_verification_runner_observations_no_update"
            trigger_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND name = ?",
                    (trigger_name,),
                ).fetchone()["sql"]
            )
            valid_row = asdict(valid)
            for label, observation in invalid:
                with self.subTest(tamper=label):
                    changed = {
                        name: value
                        for name, value in asdict(observation).items()
                        if value != valid_row[name]
                    }
                    connection.execute("SAVEPOINT runner_matrix_tamper")
                    connection.execute(f"DROP TRIGGER {trigger_name}")
                    connection.execute("PRAGMA ignore_check_constraints = ON")
                    try:
                        connection.execute(
                            "UPDATE verification_runner_observations SET "
                            + ", ".join(f"{name} = :{name}" for name in changed)
                            + " WHERE verification_runner_observation_id = :row_id",
                            {
                                **changed,
                                "row_id": valid.verification_runner_observation_id,
                            },
                        )
                    finally:
                        connection.execute("PRAGMA ignore_check_constraints = OFF")
                    connection.execute(trigger_sql)
                    with self.assertRaises(storage.StorageError):
                        storage.read_verification_runner_observation_locked(
                            connection,
                            project_id=valid.project_id,
                            task_id=valid.task_id,
                            target_generation=valid.target_generation,
                        )
                    with self.assertRaises(storage.StorageError):
                        storage.validate_verification_runner_storage(connection)
                    connection.execute("ROLLBACK TO SAVEPOINT runner_matrix_tamper")
                    connection.execute("RELEASE SAVEPOINT runner_matrix_tamper")
            storage.validate_verification_runner_storage(connection)
            connection.rollback()

    def test_between_step_controller_interruption_preserves_completed_count(
        self,
    ) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            observation = sealed_observation(replace(
                self._observation(resolution, attempt=attempt),
                route="blocked",
                launch_state="launch_uncertain",
                outcome="controller_interrupted",
                reason="controller_interrupted",
                complete_plan=0,
                failed_step_ordinal=None,
                started_at=LATER,
                duration_ms=0,
                cpu_time_ms=None,
                peak_job_memory_bytes=None,
                total_process_count=None,
            ))
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=cleanup,
            )
            stored = storage.read_verification_runner_observation_locked(
                connection,
                project_id=observation.project_id,
                task_id=observation.task_id,
                target_generation=observation.target_generation,
            )
            self.assertIsNotNone(stored)
            self.assertEqual(stored.completed_step_count, 1)
            self.assertIsNone(stored.failed_step_ordinal)
            storage.validate_verification_runner_storage(connection)
            connection.rollback()

    def test_uncertain_cleanup_failure_preserves_completed_count(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id, runner=True)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            attempt = self._attempt(resolution)
            storage.insert_verification_runner_attempt_locked(
                connection,
                attempt=attempt,
            )
            observation = sealed_observation(replace(
                self._observation(resolution, attempt=attempt),
                route="blocked",
                launch_state="launch_uncertain",
                outcome="sandbox_cleanup_failed",
                reason="sandbox_cleanup_failed",
                complete_plan=0,
                failed_step_ordinal=None,
                started_at=LATER,
                duration_ms=0,
                cpu_time_ms=None,
                peak_job_memory_bytes=None,
                total_process_count=None,
            ))
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
            )
            cleanup = self._event(
                attempt,
                suffix="5",
                event_kind="attempt_cleanup_succeeded",
                terminal_observation_id=(
                    observation.verification_runner_observation_id
                ),
                created_at=observation.created_at,
            )
            with self.assertRaises(storage.StorageError):
                storage.persist_verification_runner_terminal_locked(
                    connection,
                    observation=observation,
                    evidence_reference=reference,
                    criterion_link=link,
                    cleanup_event=cleanup,
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_observations"
                ).fetchone()[0],
                0,
            )
            storage.persist_verification_runner_terminal_locked(
                connection,
                observation=observation,
                evidence_reference=reference,
                criterion_link=link,
                cleanup_event=None,
            )
            stored = storage.read_verification_runner_observation_locked(
                connection,
                project_id=observation.project_id,
                task_id=observation.task_id,
                target_generation=observation.target_generation,
            )
            self.assertIsNotNone(stored)
            self.assertEqual(stored.completed_step_count, 1)
            self.assertIsNone(stored.failed_step_ordinal)
            pending = storage.read_pending_verification_runner_cleanup(
                connection,
                project_id=observation.project_id,
            )
            self.assertEqual(
                tuple(
                    item["attempt"].verification_runner_attempt_id
                    for item in pending
                ),
                (attempt.verification_runner_attempt_id,),
            )
            task_row = connection.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
                (observation.project_id, observation.task_id),
            ).fetchone()
            self.assertIsNotNone(task_row)
            storage.validate_evidence_ledger_storage(connection)
            validate_current_stored_task_rows(
                connection,
                (task_row,),
                expected_project_id=observation.project_id,
            )
            storage.validate_verification_runner_storage(connection)

            storage.insert_verification_runner_sandbox_event_locked(
                connection,
                event=cleanup,
            )
            self.assertFalse(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=observation.project_id,
                )
            )
            self.assertEqual(
                storage.read_verification_runner_observation_locked(
                    connection,
                    project_id=observation.project_id,
                    task_id=observation.task_id,
                    target_generation=observation.target_generation,
                ),
                observation,
            )
            storage.validate_evidence_ledger_storage(connection)
            validate_current_stored_task_rows(
                connection,
                (task_row,),
                expected_project_id=observation.project_id,
            )
            storage.validate_verification_runner_storage(connection)
            connection.rollback()

    def test_historical_runner_bundle_survives_reopen_and_fresh_target(
        self,
    ) -> None:
        repo, db, target, task_id = self._seed_current_target()
        resolution, attempt = self._seed_pending_attempt(db, target, task_id)
        self._publish_cleanup_terminal(db, resolution, attempt)

        receipt = add_receipt(db, repo, task_id, 1)
        self.assertEqual(receipt.returncode, 0, receipt.stderr or receipt.stdout)
        review = run_taskgov(
            "review",
            "receipt",
            "add",
            "--repo",
            str(repo),
            "--db",
            str(db),
            task_id,
            "--reviewer",
            "mechanical-review",
            "--kind",
            "not_required",
            "--verdict",
            "not_required",
            "--summary",
            "Schema-v20 historical Runner fixture",
            "--json",
        )
        self.assertEqual(review.returncode, 0, review.stderr or review.stdout)
        completed = complete_task(db, repo, task_id)
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr or completed.stdout,
        )
        with closing(storage.connect_readonly(db)) as connection:
            completed_row = connection.execute(
                "SELECT completion_evidence_bundle_id "
                "FROM task_completion_cycles WHERE task_id = ? "
                "ORDER BY saved_cycle_ordinal DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            self.assertIsNotNone(completed_row)
            bundle_id = str(completed_row["completion_evidence_bundle_id"])
            bundle = storage.read_completion_evidence_bundle(
                connection,
                completion_evidence_bundle_id=bundle_id,
            )
            self.assertEqual((bundle.bundle_version, bundle.target_generation), (2, 1))
            self.assertIsNone(bundle.verification_runner_observation_id)

        reopened = run_taskgov(
            "task",
            "edit",
            "--repo",
            str(repo),
            "--db",
            str(db),
            task_id,
            "--status",
            "in_progress",
            "--reopen-reason",
            "Exercise historical Runner retention",
            "--json",
        )
        self.assertEqual(reopened.returncode, 0, reopened.stderr or reopened.stdout)
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            internal = read_internal_task(
                connection,
                target.project.project_id,
                task_id,
            )
            self.assertIsNotNone(internal)
            review_service._persist_review_target_capture(
                connection,
                target.project,
                internal,
                observation=opaque_artifact_observation(
                    target_kind="diff_fingerprint",
                    target_value=FINGERPRINT_B,
                ),
                generation=2,
                now="2026-08-09T00:00:02Z",
            )
            connection.commit()

        with closing(storage.connect_readonly(db)) as connection:
            historical_resolution = (
                storage.read_verification_runner_resolution_locked(
                    connection,
                    project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=1,
                )
            )
            historical_observation = (
                storage.read_verification_runner_observation_locked(
                    connection,
                    project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=1,
                )
            )
            self.assertEqual(historical_resolution, resolution)
            self.assertEqual(
                historical_observation.verification_runner_observation_id,
                "tg_verification_runner_observation_" + "c" * 16,
            )
            historical_bundle = storage.read_completion_evidence_bundle(
                connection,
                completion_evidence_bundle_id=bundle_id,
            )
            self.assertEqual(historical_bundle.target_generation, 1)
            projection_basis = storage._capture_evidence_projection_basis_rows(
                connection,
                project_id=target.project.project_id,
            )
            historical_projection = next(
                record
                for record in projection_basis.native_bundles
                if record.bundle.completion_evidence_bundle_id == bundle_id
            )
            self.assertEqual(
                historical_projection.runner_observation["observation_id"],
                historical_observation.verification_runner_observation_id,
            )
            self.assertEqual(
                {
                    key: historical_projection.runner_observation[key]
                    for key in (
                        "attempt_id",
                        "resolution_id",
                        "project_id",
                        "task_id",
                        "target_generation",
                    )
                },
                {
                    "attempt_id": (
                        historical_observation.verification_runner_attempt_id
                    ),
                    "resolution_id": (
                        historical_resolution.verification_runner_resolution_id
                    ),
                    "project_id": historical_resolution.project_id,
                    "task_id": historical_resolution.task_id,
                    "target_generation": historical_resolution.target_generation,
                },
            )
            self.assertEqual(
                storage.read_verification_runner_public_projection(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                ),
                {
                    "eligibility": "shadow",
                    "observation_id": None,
                    "outcome": None,
                    "phase": "shadow",
                    "reason": None,
                    "route": None,
                    "schema_version": 1,
                    "target_generation": 2,
                },
            )
            storage.validate_verification_runner_storage(connection)
            storage.validate_completion_evidence_bundle_storage(connection)
            self.assertEqual(storage.validate_current_database(connection, target), 20)
            setup_state = storage.read_setup_state(connection, target)
            self.assertEqual(setup_state.schema_version, 20)
            self.assertFalse(setup_state.needs_initialize)
            self.assertFalse(setup_state.needs_migration)

        metadata = backup_service.publish_setup_backup(target, 1)
        self.assertTrue(metadata.generation_id.startswith("tg_backup_"))

    def test_pending_attempt_blocks_backup_and_cleaned_terminal_allows_it(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        resolution, attempt = self._seed_pending_attempt(db, target, task_id)
        storage.configure_project_maintenance(
            target,
            requested_interval_minutes=30,
            requested_generations=1,
            enabled_at=NOW,
        )

        routine = backup_service.run_routine_backup(
            target,
            observed_at=NOW,
        )
        self.assertEqual((routine.code, routine.attempted), ("failed", True))
        with self.assertRaises(storage.StorageError) as raised:
            backup_service.publish_setup_backup(target, 1)
        self.assertEqual(raised.exception.code, "setup_backup_failed")

        self._publish_cleanup_terminal(db, resolution, attempt)
        metadata = backup_service.publish_setup_backup(target, 1)
        self.assertTrue(metadata.generation_id.startswith("tg_backup_"))
        with closing(storage.connect_readonly(db)) as connection:
            self.assertEqual(
                storage.read_verification_runner_attempt_ids(
                    connection,
                    project_id=target.project.project_id,
                ),
                (attempt.verification_runner_attempt_id,),
            )
            self.assertFalse(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=target.project.project_id,
                )
            )

    def test_fixed_resolver_accepts_only_db_pending_runner_inventory(self) -> None:
        repo, db, target, task_id = self._seed_current_target()
        resolution, attempt = self._seed_pending_attempt(db, target, task_id)
        skill_root = self.root / "skill"
        paths = canonical_state_paths(skill_root)
        paths.fixed_root.mkdir(parents=True)
        with (
            closing(storage.connect_readonly(db)) as source,
            closing(sqlite3.connect(paths.database)) as destination,
        ):
            source.backup(destination)
        runner_paths = VerificationRunnerStatePaths(
            root=paths.verification_runner_root,
            lock=paths.verification_runner_lock,
            attempts=paths.verification_runner_attempts,
            quarantine=paths.verification_runner_quarantine,
        )
        runner_lifecycle.ensure_runner_layout(runner_paths)
        runner_lifecycle.create_attempt_directories(
            runner_paths,
            attempt.verification_runner_attempt_id,
        )

        observed = resolve_project_state(skill_root=skill_root, repo=repo)
        self.assertIsNone(observed.error_code)
        unknown = paths.verification_runner_attempts / (
            "tg_verification_runner_attempt_" + "f" * 16
        )
        unknown.mkdir()
        rejected = resolve_project_state(skill_root=skill_root, repo=repo)
        self.assertEqual(rejected.error_code, "project_state_unreadable")
        unknown.rmdir()

        self._publish_cleanup_terminal(paths.database, resolution, attempt)
        stale = resolve_project_state(skill_root=skill_root, repo=repo)
        self.assertEqual(stale.error_code, "project_state_unreadable")
        runner_lifecycle.remove_attempt_tree(
            runner_paths,
            attempt.verification_runner_attempt_id,
        )
        cleaned = resolve_project_state(skill_root=skill_root, repo=repo)
        self.assertIsNone(cleaned.error_code)
        self.assertIsNotNone(cleaned.target)
        self.assertEqual(cleaned.target.db_path, paths.database)
        self.assertEqual(
            cleaned.target.resolved_verification_runner_root,
            paths.verification_runner_root,
        )

    def test_fixed_resolver_runner_layout_fanout_is_bounded_and_read_only(
        self,
    ) -> None:
        repo, db, target, task_id = self._seed_current_target()
        _resolution, attempt = self._seed_pending_attempt(db, target, task_id)
        skill_root = self.root / "skill"
        paths = canonical_state_paths(skill_root)
        paths.fixed_root.mkdir(parents=True)
        with (
            closing(storage.connect_readonly(db)) as source,
            closing(sqlite3.connect(paths.database)) as destination,
        ):
            source.backup(destination)
        runner_paths = VerificationRunnerStatePaths(
            root=paths.verification_runner_root,
            lock=paths.verification_runner_lock,
            attempts=paths.verification_runner_attempts,
            quarantine=paths.verification_runner_quarantine,
        )
        runner_lifecycle.ensure_runner_layout(runner_paths)
        attempt_paths = runner_lifecycle.create_attempt_directories(
            runner_paths,
            attempt.verification_runner_attempt_id,
        )
        self.assertIsNone(
            resolve_project_state(skill_root=skill_root, repo=repo).error_code
        )

        real_scandir = os.scandir

        def assert_bounded_rejection(
            watched: set[Path],
            maximum_yields: int,
        ) -> None:
            yields = 0

            class LazyScandir:
                def __init__(self, directory):
                    self.directory = Path(directory)
                    self.iterator = real_scandir(directory)

                def __enter__(self):
                    self.iterator.__enter__()
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return self.iterator.__exit__(exc_type, exc, traceback)

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal yields
                    entry = next(self.iterator)
                    if self.directory in watched:
                        yields += 1
                        if yields > maximum_yields:
                            raise AssertionError(
                                "resolver enumerated beyond the cap sentinel"
                            )
                    return entry

            before = tree_snapshot(paths.verification_runner_root)
            with mock.patch.object(
                state_resolver_module.os,
                "scandir",
                side_effect=LazyScandir,
            ):
                rejected = resolve_project_state(skill_root=skill_root, repo=repo)
            self.assertEqual(rejected.error_code, "project_state_unreadable")
            self.assertEqual(yields, maximum_yields)
            self.assertEqual(
                tree_snapshot(paths.verification_runner_root),
                before,
            )

        root_extras = tuple(
            paths.verification_runner_root / f"unexpected-{ordinal}.txt"
            for ordinal in range(5)
        )
        for extra in root_extras:
            extra.write_bytes(b"unchanged\n")
        assert_bounded_rejection(
            {paths.verification_runner_root},
            4,
        )
        for extra in root_extras:
            extra.unlink()

        unknown = paths.verification_runner_quarantine / (
            "tg_verification_runner_attempt_" + "f" * 16
        )
        unknown.mkdir()
        assert_bounded_rejection(
            {
                paths.verification_runner_attempts,
                paths.verification_runner_quarantine,
            },
            2,
        )
        unknown.rmdir()

        child_extras = tuple(
            attempt_paths.root / f"unexpected-{ordinal}"
            for ordinal in range(5)
        )
        for extra in child_extras:
            extra.mkdir()
        assert_bounded_rejection({attempt_paths.root}, 4)

    def test_fixed_resolver_runner_layout_checks_deadline_after_lazy_read(
        self,
    ) -> None:
        directory = self.root / "resolver-deadline"
        directory.mkdir()
        (directory / "first").mkdir()
        (directory / "second").mkdir()
        before = tree_snapshot(directory)
        real_scandir = os.scandir
        reads = 0

        class LazyScandir:
            def __init__(self, path):
                self.iterator = real_scandir(path)

            def __enter__(self):
                self.iterator.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                return self.iterator.__exit__(exc_type, exc, traceback)

            def __iter__(self):
                return self

            def __next__(self):
                nonlocal reads
                entry = next(self.iterator)
                reads += 1
                if reads > 1:
                    raise AssertionError("deadline did not stop enumeration")
                return entry

        budget = state_resolver_module._ResolverTraversalBudget(
            maximum_entries=10,
            deadline=100.0,
        )
        with (
            mock.patch.object(
                state_resolver_module.os,
                "scandir",
                side_effect=LazyScandir,
            ),
            mock.patch.object(
                state_resolver_module.time,
                "monotonic",
                side_effect=(99.0, 101.0),
            ),
            self.assertRaises(state_resolver_module._ResolverFailure),
        ):
            state_resolver_module._bounded_resolver_children(
                directory,
                budget=budget,
                maximum_items=10,
            )
        self.assertEqual(reads, 1)
        self.assertEqual(budget.observed_entries, 0)
        self.assertEqual(tree_snapshot(directory), before)

    def test_setup_reconciliation_holds_state_then_runner_and_revalidates(self) -> None:
        repo, db, target, task_id = self._seed_current_target()
        resolution, attempt = self._seed_pending_attempt(db, target, task_id)
        skill_root = self.root / "skill"
        paths = canonical_state_paths(skill_root)
        paths.fixed_root.mkdir(parents=True)
        with (
            closing(storage.connect_readonly(db)) as source,
            closing(sqlite3.connect(paths.database)) as destination,
        ):
            source.backup(destination)
        runner_paths = VerificationRunnerStatePaths(
            root=paths.verification_runner_root,
            lock=paths.verification_runner_lock,
            attempts=paths.verification_runner_attempts,
            quarantine=paths.verification_runner_quarantine,
        )
        runner_lifecycle.ensure_runner_layout(runner_paths)
        runner_lifecycle.create_attempt_directories(
            runner_paths,
            attempt.verification_runner_attempt_id,
        )
        initial = resolve_setup_project_state(skill_root=skill_root, repo=repo)
        self.assertIsNone(initial.error_code)
        events: list[str] = []
        real_state_lock = setup_service.state_transition_lock
        real_runner_lock = runner_lifecycle.zero_wait_runner_lock

        @contextmanager
        def observed_state_lock(state_root: Path):
            events.append("state_enter")
            with real_state_lock(state_root):
                yield
            events.append("state_exit")

        @contextmanager
        def observed_runner_lock(exact_paths: VerificationRunnerStatePaths):
            events.append("runner_enter")
            with real_runner_lock(exact_paths) as inventory:
                yield inventory
            events.append("runner_exit")

        def reconcile(
            exact_target: storage.DatabaseTarget,
            inventory: runner_lifecycle.RunnerLayoutInventory,
        ) -> None:
            self.assertEqual(events, ["state_enter", "runner_enter"])
            self.assertEqual(
                inventory.attempt_ids,
                (attempt.verification_runner_attempt_id,),
            )
            runner_lifecycle.remove_attempt_tree(
                runner_paths,
                attempt.verification_runner_attempt_id,
            )
            self._publish_cleanup_terminal(
                exact_target.db_path,
                resolution,
                attempt,
            )

        with (
            mock.patch.object(
                setup_service,
                "state_transition_lock",
                observed_state_lock,
            ),
            mock.patch.object(
                runner_lifecycle,
                "zero_wait_runner_lock",
                observed_runner_lock,
            ),
        ):
            reconciled, refreshed, _target = (
                setup_service._run_verification_runner_cleanup_reconciliation(
                    scope=SimpleNamespace(
                        skill_root=skill_root,
                        canonical_repo=repo,
                    ),
                    resolution=initial,
                    reconciler=reconcile,
                )
            )
        self.assertTrue(reconciled)
        self.assertIsNone(refreshed.error_code)
        self.assertEqual(
            events,
            ["state_enter", "runner_enter", "runner_exit", "state_exit"],
        )
        with closing(storage.connect_readonly(paths.database)) as connection:
            self.assertFalse(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=target.project.project_id,
                )
            )

    def test_schema20_pending_backup_is_set_fatal_before_older_recovery(self) -> None:
        install = make_physical_install(
            self.root / "pending-recovery",
            git_managed=True,
        )
        initialized = install.run("setup", "--json")
        self.assertEqual(
            initialized.returncode,
            0,
            initialized.stdout or initialized.stderr,
        )
        task_id = self._add_physical_runner_task(install)
        target = install.target
        older = backup_service.publish_setup_backup(target, 2)
        _resolution, attempt, _runner_paths = (
            self._seed_physical_pending_attempt(install, task_id)
        )
        newer = backup_service._new_metadata(
            2,
            published_at=older.published_at,
            after=older,
        )
        newer_path = (
            target.resolved_backups_path / backup_service._filename(newer)
        )
        with (
            closing(storage.connect_readonly(install.db_path)) as source,
            closing(sqlite3.connect(newer_path)) as destination,
        ):
            source.backup(destination)

        with self.assertRaises(storage.StorageError) as managed:
            backup_service.discover_managed_backup_metadata(target)
        self.assertEqual(managed.exception.code, "setup_backup_failed")
        with self.assertRaises(storage.StorageError) as recovery:
            backup_service.select_managed_backup_for_recovery(target)
        self.assertEqual(recovery.exception.code, "setup_restore_failed")

        install.db_path.unlink()
        before = tree_snapshot(install.fixed_root)
        resolved = resolve_setup_project_state(
            skill_root=install.skill_root,
            repo=install.project_root,
        )
        self.assertEqual(resolved.error_code, "project_state_unreadable")
        attempted = install.run("setup", "--read-only", "--json")
        self.assertEqual(attempted.returncode, 2, attempted.stdout)
        payload = json.loads(attempted.stdout)
        self.assertEqual(payload["errors"][0]["code"], "project_state_unreadable")
        self.assertEqual(payload["data"]["planned_writes"], [])
        self.assertEqual(payload["data"]["completed_writes"], [])
        self.assertEqual(tree_snapshot(install.fixed_root), before)
        self.assertFalse(install.db_path.exists())
        self.assertTrue(
            (
                install.fixed_root
                / "verification-runner"
                / "attempts"
                / attempt.verification_runner_attempt_id
            ).is_dir()
        )

    def test_setup_preview_reports_pending_runner_cleanup_without_lock_or_write(
        self,
    ) -> None:
        install = make_physical_install(
            self.root / "pending-preview",
            git_managed=True,
        )
        initialized = install.run("setup", "--json")
        self.assertEqual(initialized.returncode, 0, initialized.stdout)
        task_id = self._add_physical_runner_task(install)
        _resolution, _attempt, _runner_paths = (
            self._seed_physical_pending_attempt(install, task_id)
        )
        before = tree_snapshot(install.fixed_root)
        callback = mock.Mock(side_effect=AssertionError("unexpected cleanup"))
        with (
            mock.patch.object(
                setup_service,
                "state_transition_lock",
                side_effect=AssertionError("unexpected state lock"),
            ),
            mock.patch.object(
                runner_lifecycle,
                "zero_wait_runner_lock",
                side_effect=AssertionError("unexpected runner lock"),
            ),
        ):
            result = setup_service.run_setup(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
                read_only=True,
                backup_interval_minutes=31,
                backup_generations=None,
                _runner_cleanup_reconciler=callback,
            )
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(result.data["status"], "setup_preview")
        self.assertEqual(result.data["completed_writes"], [])
        planned = result.data["planned_writes"]
        self.assertIn("verification_runner_cleanup", planned)
        self.assertLess(
            planned.index("verification_runner_cleanup"),
            planned.index("maintenance_configure"),
        )
        self.assertEqual(
            setup_service.SETUP_WRITE_ORDER[
                setup_service.SETUP_WRITE_ORDER.index("database_migrate") + 1
            ],
            "verification_runner_cleanup",
        )
        callback.assert_not_called()
        self.assertEqual(tree_snapshot(install.fixed_root), before)

    def test_setup_records_runner_cleanup_only_after_durable_success(self) -> None:
        install = make_physical_install(
            self.root / "pending-success",
            git_managed=True,
        )
        initialized = install.run("setup", "--json")
        self.assertEqual(initialized.returncode, 0, initialized.stdout)
        task_id = self._add_physical_runner_task(install)
        resolution, attempt, runner_paths = (
            self._seed_physical_pending_attempt(install, task_id)
        )

        def reconcile(
            exact_target: storage.DatabaseTarget,
            _inventory: runner_lifecycle.RunnerLayoutInventory,
        ) -> None:
            runner_lifecycle.remove_attempt_tree(
                runner_paths,
                attempt.verification_runner_attempt_id,
            )
            self._publish_cleanup_terminal(
                exact_target.db_path,
                resolution,
                attempt,
            )

        result = setup_service.run_setup(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
            read_only=False,
            backup_interval_minutes=31,
            backup_generations=None,
            _runner_cleanup_reconciler=reconcile,
        )
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(result.data["status"], "setup_complete")
        self.assertEqual(
            result.data["completed_writes"],
            result.data["planned_writes"],
        )
        completed = result.data["completed_writes"]
        self.assertLess(
            completed.index("verification_runner_cleanup"),
            completed.index("maintenance_configure"),
        )
        with closing(storage.connect_readonly(install.db_path)) as connection:
            self.assertFalse(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=install.project_id,
                )
            )
        self.assertFalse(
            (
                runner_paths.attempts
                / attempt.verification_runner_attempt_id
            ).exists()
        )
        no_op = mock.Mock(side_effect=AssertionError("unexpected retry cleanup"))
        replay = setup_service.run_setup(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
            read_only=False,
            backup_interval_minutes=None,
            backup_generations=None,
            _runner_cleanup_reconciler=no_op,
        )
        self.assertTrue(replay.ok, replay.error_message)
        self.assertNotIn(
            "verification_runner_cleanup",
            replay.data["planned_writes"],
        )
        no_op.assert_not_called()

    def test_setup_cleanup_failure_keeps_exact_completed_prefix(self) -> None:
        install = make_physical_install(
            self.root / "pending-failure",
            git_managed=True,
        )
        initialized = install.run("setup", "--json")
        self.assertEqual(initialized.returncode, 0, initialized.stdout)
        task_id = self._add_physical_runner_task(install)
        _resolution, attempt, runner_paths = (
            self._seed_physical_pending_attempt(install, task_id)
        )
        callback = mock.Mock(side_effect=RuntimeError("injected cleanup failure"))
        result = setup_service.run_setup(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
            read_only=False,
            backup_interval_minutes=31,
            backup_generations=None,
            _runner_cleanup_reconciler=callback,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "setup_incomplete")
        self.assertIn(
            "verification_runner_cleanup",
            result.data["planned_writes"],
        )
        self.assertEqual(result.data["completed_writes"], [])
        self.assertNotIn(
            "verification_runner_cleanup",
            result.data["completed_writes"],
        )
        callback.assert_called_once()
        with closing(storage.connect_readonly(install.db_path)) as connection:
            self.assertTrue(
                storage.has_pending_verification_runner_cleanup(
                    connection,
                    project_id=install.project_id,
                )
            )
        self.assertTrue(
            (
                runner_paths.attempts
                / attempt.verification_runner_attempt_id
            ).is_dir()
        )

    def test_setup_without_pending_cleanup_never_locks_or_calls_reconciler(self) -> None:
        install = make_physical_install(
            self.root / "physical",
            git_managed=True,
        )
        initialized = install.run("setup", "--json")
        self.assertEqual(
            initialized.returncode,
            0,
            initialized.stdout or initialized.stderr,
        )
        callback = mock.Mock(side_effect=AssertionError("unexpected cleanup"))
        with mock.patch.object(
            setup_service,
            "state_transition_lock",
            side_effect=AssertionError("unexpected write lock"),
        ):
            result = setup_service.run_setup(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
                read_only=True,
                backup_interval_minutes=None,
                backup_generations=None,
                _runner_cleanup_reconciler=callback,
            )
        self.assertTrue(result.ok, result.error_message)
        self.assertNotIn(
            "verification_runner_cleanup",
            result.data["planned_writes"],
        )
        callback.assert_not_called()
        with mock.patch.object(
            setup_service,
            "state_transition_lock",
            side_effect=AssertionError("unexpected cleanup lock"),
        ):
            already_setup = setup_service.run_setup(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
                read_only=False,
                backup_interval_minutes=None,
                backup_generations=None,
                _runner_cleanup_reconciler=callback,
            )
        self.assertTrue(already_setup.ok, already_setup.error_message)
        self.assertNotIn(
            "verification_runner_cleanup",
            already_setup.data["planned_writes"],
        )
        callback.assert_not_called()

    def test_terminal_writer_rolls_back_observation_on_reference_failure(self) -> None:
        _repo, db, target, task_id = self._seed_current_target()
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            resolution = self._resolution(connection, target, task_id)
            storage.insert_verification_runner_resolution_locked(
                connection,
                resolution=resolution,
            )
            observation = self._observation(resolution)
            existing_reference_id = str(
                connection.execute(
                    "SELECT evidence_reference_id FROM evidence_references "
                    "WHERE project_id = ? AND task_id = ? "
                    "AND source_kind = 'artifact_manifest'",
                    (resolution.project_id, resolution.task_id),
                ).fetchone()[0]
            )
            reference, link = self._terminal_rows(
                connection,
                resolution,
                observation,
                reference_id=existing_reference_id,
            )
            with self.assertRaises(storage.StorageError):
                storage.persist_verification_runner_terminal_locked(
                    connection,
                    observation=observation,
                    evidence_reference=reference,
                    criterion_link=link,
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_observations"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM evidence_references "
                    "WHERE source_kind = 'runner_observation'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM criterion_evidence_links "
                    "WHERE relation = 'runner_observation'"
                ).fetchone()[0],
                0,
            )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
