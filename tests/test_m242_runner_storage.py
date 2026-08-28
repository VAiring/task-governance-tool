import json
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import storage  # noqa: E402
from task_governance_tool.artifact_manifest import ArtifactObservation  # noqa: E402
from task_governance_tool.backup import (  # noqa: E402
    publish_setup_backup,
    select_managed_backup_for_recovery,
)
from task_governance_tool.evidence_ledger import (  # noqa: E402
    EvidenceLedgerError,
    EvidenceSource,
    TargetCaptureBinding,
    build_evidence_reference,
)
from task_governance_tool.reviews import (  # noqa: E402
    generate_review_id,
    persist_prepared_review_target_capture,
    read_review_target_authority_basis,
)
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_POLICY_DIGEST,
    RUNNER_TRIGGER,
    resolution_idempotency_digest,
    runner_observation_source_projection,
    verification_runner_attempt_digest,
    verification_runner_observation_digest,
    verification_runner_sandbox_event_digest,
)
from task_governance_tool.viewer import build_viewer_snapshot  # noqa: E402
from tests.test_m242_r3b_schema20_activation import (  # noqa: E402
    _add_task,
    _fixed_current20,
    _start_schema20_runtime_oracle,
    _stop_schema20_runtime_oracle,
)


FIXED_TIME = "2026-08-25T00:00:00Z"


def setUpModule() -> None:
    _start_schema20_runtime_oracle()


def tearDownModule() -> None:
    _stop_schema20_runtime_oracle()


def _digest(character: str) -> str:
    return "sha256:" + character * 64


class RunnerStorageTests(unittest.TestCase):
    def _new_target_and_task(self, root: Path, seed: str):
        _install, target = _fixed_current20(root, identity_seed=seed)
        task_id = _add_task(
            self,
            target,
            title=f"Runner storage {seed}",
            verification="Run the exact trusted local verification plan.",
            with_contract=True,
        )
        return target, task_id

    def _capture_target(
        self,
        target: storage.DatabaseTarget,
        task_id: str,
        *,
        commit_character: str,
        now: str,
    ) -> dict[str, object]:
        with closing(storage.connect_initialized_readonly(target)) as connection:
            authority = read_review_target_authority_basis(
                connection,
                target.project,
                task_id,
            )
        observation = ArtifactObservation(
            state="complete_git",
            object_format="sha1",
            comparison_base=commit_character * 40,
            target_kind="git_commit",
            target_value=commit_character * 40,
            target_base_revision="",
        )
        with closing(storage.connect_initialized(target)) as connection:
            with connection:
                persist_prepared_review_target_capture(
                    connection,
                    target.project,
                    authority.task,
                    observation=observation,
                    database_target=target,
                    now=now,
                )
                return storage.read_current_verification_runner_target_basis(
                    connection,
                    project_id=target.project.project_id,
                    task_id=task_id,
                )

    def _resolution_attempt(
        self,
        basis: dict[str, object],
        *,
        token: str,
        created_at: str,
        plan_id: str = "runner_plan",
    ) -> tuple[
        storage.VerificationRunnerResolution,
        storage.VerificationRunnerAttempt,
    ]:
        digest_values = {
            **{
                key: basis[key]
                for key in (
                    "contract_revision",
                    "authority_snapshot_id",
                    "verification_criterion_id",
                    "verification_expectation_digest",
                    "verification_criterion_digest",
                    "target_kind",
                    "target_value",
                    "target_generation",
                    "target_capture_version",
                    "artifact_manifest_id",
                    "gate_eligibility_version",
                )
            },
            "project_id": basis["project_id"],
            "task_id": basis["task_id"],
            "target_base_revision": basis["target_base_revision"] or None,
            "target_material_digest": _digest("1"),
            "plan_state": "runner",
            "plan_blob_object_id": None,
            "plan_raw_digest": _digest("2"),
            "plan_id": plan_id,
            "plan_version": 1,
            "plan_semantic_digest": _digest("3"),
            "selected_entry_digest": _digest("4"),
            "coverage": "full",
            "step_count": 1,
            "runner_contract_version": RUNNER_CONTRACT_VERSION,
            "runner_implementation_version": RUNNER_IMPLEMENTATION_VERSION,
            "runner_implementation_digest": _digest("5"),
            "runner_policy_digest": RUNNER_POLICY_DIGEST,
            "sandbox_provider": None,
            "sandbox_policy_digest": None,
            "runtime_digest": None,
            "trigger": RUNNER_TRIGGER,
            "route": "runner",
            "reason": None,
        }
        resolution_id = "tg_verification_runner_resolution_" + token
        storage_values = {
            key: value
            for key, value in digest_values.items()
            if key not in {"sandbox_provider", "sandbox_policy_digest"}
        }
        resolution = storage.VerificationRunnerResolution(
            verification_runner_resolution_id=resolution_id,
            **storage_values,
            idempotency_digest=resolution_idempotency_digest(digest_values),
            created_at=created_at,
        )
        attempt_digest_values = {
            "project_id": resolution.project_id,
            "task_id": resolution.task_id,
            "target_generation": resolution.target_generation,
            "gate_eligibility_version": resolution.gate_eligibility_version,
            "resolution_id": resolution_id,
            "target_material_digest": resolution.target_material_digest,
            "runner_implementation_digest": (
                resolution.runner_implementation_digest
            ),
            "sandbox_instance_digest": None,
        }
        attempt = storage.VerificationRunnerAttempt(
            verification_runner_attempt_id=(
                "tg_verification_runner_attempt_" + token
            ),
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            target_generation=resolution.target_generation,
            gate_eligibility_version=0,
            verification_runner_resolution_id=resolution_id,
            target_material_digest=str(resolution.target_material_digest),
            runner_implementation_digest=resolution.runner_implementation_digest,
            attempt_digest=verification_runner_attempt_digest(
                attempt_digest_values
            ),
            intent_recorded_at=created_at,
        )
        return resolution, attempt

    @staticmethod
    def _basis_with_owner(
        basis: dict[str, object],
        *,
        project_id: str,
        task_id: str,
    ) -> dict[str, object]:
        return {**basis, "project_id": project_id, "task_id": task_id}

    def _terminal_values(
        self,
        resolution: storage.VerificationRunnerResolution,
        attempt: storage.VerificationRunnerAttempt,
        *,
        token: str,
    ):
        started_at = "2026-08-25T00:00:02Z"
        finished_at = "2026-08-25T00:00:03Z"
        digest_values = {
            "attempt_id": attempt.verification_runner_attempt_id,
            "completed_step_count": 1,
            "complete_plan": 1,
            "cpu_time_ms": 10,
            "duration_ms": 100,
            "failed_step_ordinal": None,
            "finished_at": finished_at,
            "gate_eligibility_version": 0,
            "launch_state": "launched",
            "outcome": "pass",
            "peak_job_memory_bytes": 4096,
            "project_id": resolution.project_id,
            "reason": None,
            "resolution_id": resolution.verification_runner_resolution_id,
            "runner_implementation_digest": (
                resolution.runner_implementation_digest
            ),
            "started_at": started_at,
            "target_generation": resolution.target_generation,
            "task_id": resolution.task_id,
            "route": "runner",
            "total_process_count": 1,
            "total_step_count": 1,
        }
        observation = storage.VerificationRunnerObservation(
            verification_runner_observation_id=(
                "tg_verification_runner_observation_" + token
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
            ),
            runner_implementation_digest=resolution.runner_implementation_digest,
            route="runner",
            launch_state="launched",
            outcome="pass",
            reason=None,
            complete_plan=1,
            total_step_count=1,
            completed_step_count=1,
            failed_step_ordinal=None,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=100,
            cpu_time_ms=10,
            peak_job_memory_bytes=4096,
            total_process_count=1,
            sanitized_result_digest=verification_runner_observation_digest(
                digest_values
            ),
            created_at=finished_at,
        )
        event_values = {
            "attempt_id": attempt.verification_runner_attempt_id,
            "event_kind": "attempt_cleanup_succeeded",
            "project_id": resolution.project_id,
            "target_generation": resolution.target_generation,
            "task_id": resolution.task_id,
            "terminal_observation_id": (
                observation.verification_runner_observation_id
            ),
        }
        event = storage.VerificationRunnerSandboxEvent(
            verification_runner_sandbox_event_id=(
                "tg_verification_runner_sandbox_event_" + token
            ),
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            target_generation=resolution.target_generation,
            verification_runner_attempt_id=(
                attempt.verification_runner_attempt_id
            ),
            event_kind="attempt_cleanup_succeeded",
            event_digest=verification_runner_sandbox_event_digest(event_values),
            terminal_observation_id=(
                observation.verification_runner_observation_id
            ),
            created_at=finished_at,
        )
        return observation, event

    def _terminal_evidence(
        self,
        resolution: storage.VerificationRunnerResolution,
        observation: storage.VerificationRunnerObservation,
        *,
        acceptance_criterion_id: str | None,
        token: str,
    ):
        source = EvidenceSource(
            source_kind="runner_observation",
            source_state="recorded",
            source_id=observation.verification_runner_observation_id,
            source_projection=runner_observation_source_projection(
                observation=observation.__dict__,
                resolution=resolution.__dict__,
            ),
        )
        binding = TargetCaptureBinding(
            target_kind=resolution.target_kind,
            target_value=resolution.target_value,
            target_base_revision=resolution.target_base_revision or "",
            target_generation=resolution.target_generation,
            authority_snapshot_id=resolution.authority_snapshot_id,
            acceptance_criterion_id=acceptance_criterion_id,
            verification_criterion_id=resolution.verification_criterion_id,
        )
        spec = build_evidence_reference(
            source=source,
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            contract_revision=resolution.contract_revision,
            binding=binding,
        )
        reference_id = "tg_evidence_reference_" + token
        reference = {
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
            "acceptance_criterion_id": acceptance_criterion_id,
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
                "tg_criterion_evidence_link_" + token
            ),
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            criterion_id=resolution.verification_criterion_id,
            evidence_reference_id=reference_id,
            relation="runner_observation",
            assurance_class=spec.attribution.assurance_class,
            producer_class=spec.attribution.producer_class,
            producer_version=spec.attribution.producer_version,
            created_at=observation.created_at,
        )
        return source, reference, link

    def _pending_graph(
        self,
        root: Path,
        *,
        seed: str,
        commit_character: str,
        token: str,
    ):
        target, task_id = self._new_target_and_task(root, seed)
        basis = self._capture_target(
            target,
            task_id,
            commit_character=commit_character,
            now=FIXED_TIME,
        )
        basis = self._basis_with_owner(
            basis,
            project_id=target.project.project_id,
            task_id=task_id,
        )
        resolution, attempt = self._resolution_attempt(
            basis,
            token=token,
            created_at="2026-08-25T00:00:01Z",
        )
        with closing(storage.connect_initialized(target)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self.assertTrue(
                    storage.insert_verification_runner_resolution_locked(
                        connection,
                        resolution=resolution,
                        attempt=attempt,
                    )
                )
        return target, basis, resolution, attempt

    @staticmethod
    def _cleanup_only_event(
        resolution: storage.VerificationRunnerResolution,
        attempt: storage.VerificationRunnerAttempt,
        *,
        token: str,
    ) -> storage.VerificationRunnerSandboxEvent:
        values = {
            "attempt_id": attempt.verification_runner_attempt_id,
            "event_kind": "attempt_cleanup_succeeded",
            "project_id": resolution.project_id,
            "target_generation": resolution.target_generation,
            "task_id": resolution.task_id,
            "terminal_observation_id": None,
        }
        return storage.VerificationRunnerSandboxEvent(
            verification_runner_sandbox_event_id=(
                "tg_verification_runner_sandbox_event_" + token
            ),
            project_id=resolution.project_id,
            task_id=resolution.task_id,
            target_generation=resolution.target_generation,
            verification_runner_attempt_id=(
                attempt.verification_runner_attempt_id
            ),
            event_kind="attempt_cleanup_succeeded",
            event_digest=verification_runner_sandbox_event_digest(values),
            terminal_observation_id=None,
            created_at="2026-08-25T00:00:04Z",
        )

    @staticmethod
    def _insert_runner_value(connection, table_name: str, value: object) -> None:
        fields = tuple(sorted(value.__dict__))
        connection.execute(
            f"INSERT INTO {table_name}("
            + ", ".join(fields)
            + ") VALUES ("
            + ", ".join(":" + field for field in fields)
            + ")",
            value.__dict__,
        )

    def test_t1_exact_replay_is_read_only_and_conflict_preserves_cardinality(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-t1-replay-",
            dir=ROOT,
        ) as tmp:
            target, _basis, resolution, attempt = self._pending_graph(
                Path(tmp),
                seed="t1-replay",
                commit_character="4",
                token="a" * 16,
            )
            with closing(storage.connect_initialized(target)) as connection:
                before_replay = connection.total_changes
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertFalse(
                        storage.insert_verification_runner_resolution_locked(
                            connection,
                            resolution=resolution,
                            attempt=attempt,
                        )
                    )
                self.assertEqual(connection.total_changes, before_replay)

                conflicting_attempt = replace(
                    attempt,
                    verification_runner_attempt_id=(
                        "tg_verification_runner_attempt_" + "b" * 16
                    ),
                )
                before_conflict = connection.total_changes
                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.insert_verification_runner_resolution_locked(
                            connection,
                            resolution=resolution,
                            attempt=conflicting_attempt,
                        )
                self.assertEqual(connection.total_changes, before_conflict)
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table_name}"
                        ).fetchone()[0]
                        for table_name in (
                            "verification_runner_resolutions",
                            "verification_runner_attempts",
                        )
                    ),
                    (1, 1),
                )

    def test_terminal_replay_is_read_only_and_invalid_graph_rolls_back(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-terminal-replay-",
            dir=ROOT,
        ) as tmp:
            target, basis, resolution, attempt = self._pending_graph(
                Path(tmp),
                seed="terminal-replay",
                commit_character="5",
                token="c" * 16,
            )
            observation, event = self._terminal_values(
                resolution,
                attempt,
                token="d" * 16,
            )
            _source, reference, link = self._terminal_evidence(
                resolution,
                observation,
                acceptance_criterion_id=basis["acceptance_criterion_id"],
                token="e" * 16,
            )
            with closing(storage.connect_initialized(target)) as connection:
                wrong_digest_reference = {
                    **reference,
                    "digest": _digest("9"),
                }
                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference=wrong_digest_reference,
                            criterion_link=link,
                            cleanup_event=event,
                        )
                self.assertEqual(
                    tuple(
                        connection.execute(query).fetchone()[0]
                        for query in (
                            "SELECT COUNT(*) FROM verification_runner_observations",
                            "SELECT COUNT(*) FROM verification_runner_sandbox_events",
                            "SELECT COUNT(*) FROM evidence_references "
                            "WHERE source_kind = 'runner_observation'",
                            "SELECT COUNT(*) FROM criterion_evidence_links "
                            "WHERE relation = 'runner_observation'",
                        )
                    ),
                    (0, 0, 0, 0),
                )

                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertTrue(
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference=reference,
                            criterion_link=link,
                            cleanup_event=event,
                        )
                    )
                before_replay = connection.total_changes
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertFalse(
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference=reference,
                            criterion_link=link,
                            cleanup_event=event,
                        )
                    )
                self.assertEqual(connection.total_changes, before_replay)

                conflicting_event = replace(
                    event,
                    verification_runner_sandbox_event_id=(
                        "tg_verification_runner_sandbox_event_" + "f" * 16
                    ),
                )
                before_conflict = connection.total_changes
                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference=reference,
                            criterion_link=link,
                            cleanup_event=conflicting_event,
                        )
                self.assertEqual(connection.total_changes, before_conflict)

                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        connection.execute(
                            "DROP TRIGGER "
                            "trg_criterion_evidence_links_matrix_insert"
                        )
                        connection.execute(
                            """
                            INSERT INTO criterion_evidence_links(
                              criterion_evidence_link_id, project_id, task_id,
                              criterion_id, evidence_reference_id, relation,
                              assurance_class, producer_class, producer_version,
                              created_at
                            )
                            SELECT ?, project_id, task_id, criterion_id,
                                   evidence_reference_id,
                                   'verification_attestation', assurance_class,
                                   producer_class, producer_version, created_at
                              FROM criterion_evidence_links
                             WHERE criterion_evidence_link_id = ?
                            """,
                            (
                                "tg_criterion_evidence_link_" + "1" * 16,
                                link.criterion_evidence_link_id,
                            ),
                        )
                        storage.read_verification_runner_generation_locked(
                            connection,
                            project_id=resolution.project_id,
                            task_id=resolution.task_id,
                            target_generation=resolution.target_generation,
                        )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM criterion_evidence_links "
                        "WHERE evidence_reference_id = ?",
                        (reference["evidence_reference_id"],),
                    ).fetchone()[0],
                    1,
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
                        "AND name = "
                        "'trg_criterion_evidence_links_matrix_insert'"
                    ).fetchone()
                )

    def test_shared_validator_rejects_observation_started_before_intent(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-observation-chronology-",
            dir=ROOT,
        ) as tmp:
            target, basis, resolution, attempt = self._pending_graph(
                Path(tmp),
                seed="observation-chronology",
                commit_character="8",
                token="7" * 16,
            )
            observation, event = self._terminal_values(
                resolution,
                attempt,
                token="8" * 16,
            )
            early_started_at = "2026-08-25T00:00:00Z"
            early_digest_values = {
                "attempt_id": observation.verification_runner_attempt_id,
                "completed_step_count": observation.completed_step_count,
                "complete_plan": observation.complete_plan,
                "cpu_time_ms": observation.cpu_time_ms,
                "duration_ms": observation.duration_ms,
                "failed_step_ordinal": observation.failed_step_ordinal,
                "finished_at": observation.finished_at,
                "gate_eligibility_version": observation.gate_eligibility_version,
                "launch_state": observation.launch_state,
                "outcome": observation.outcome,
                "peak_job_memory_bytes": observation.peak_job_memory_bytes,
                "project_id": observation.project_id,
                "reason": observation.reason,
                "resolution_id": observation.verification_runner_resolution_id,
                "runner_implementation_digest": (
                    observation.runner_implementation_digest
                ),
                "started_at": early_started_at,
                "target_generation": observation.target_generation,
                "task_id": observation.task_id,
                "route": observation.route,
                "total_process_count": observation.total_process_count,
                "total_step_count": observation.total_step_count,
            }
            early_observation = replace(
                observation,
                started_at=early_started_at,
                sanitized_result_digest=verification_runner_observation_digest(
                    early_digest_values
                ),
            )
            _source, reference, link = self._terminal_evidence(
                resolution,
                early_observation,
                acceptance_criterion_id=basis["acceptance_criterion_id"],
                token="9" * 16,
            )
            with closing(storage.connect_initialized(target)) as connection:
                before_counts = tuple(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0]
                    for table_name in (
                        "verification_runner_observations",
                        "verification_runner_sandbox_events",
                        "evidence_references",
                        "criterion_evidence_links",
                    )
                )
                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        self._insert_runner_value(
                            connection,
                            "verification_runner_observations",
                            early_observation,
                        )
                        storage.persist_evidence_reference_locked(
                            connection,
                            reference=reference,
                        )
                        storage.persist_criterion_evidence_link_locked(
                            connection,
                            link=link,
                        )
                        self._insert_runner_value(
                            connection,
                            "verification_runner_sandbox_events",
                            event,
                        )
                        storage.read_verification_runner_generation_locked(
                            connection,
                            project_id=resolution.project_id,
                            task_id=resolution.task_id,
                            target_generation=resolution.target_generation,
                        )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table_name}"
                        ).fetchone()[0]
                        for table_name in (
                            "verification_runner_observations",
                            "verification_runner_sandbox_events",
                            "evidence_references",
                            "criterion_evidence_links",
                        )
                    ),
                    before_counts,
                )
                graph = storage.read_verification_runner_generation_locked(
                    connection,
                    project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=resolution.target_generation,
                )
                self.assertEqual(graph["state"], "pending")

    def test_cleanup_only_replay_is_read_only_and_conflict_preserves_one_event(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-cleanup-replay-",
            dir=ROOT,
        ) as tmp:
            target, _basis, resolution, attempt = self._pending_graph(
                Path(tmp),
                seed="cleanup-replay",
                commit_character="6",
                token="2" * 16,
            )
            cleanup = self._cleanup_only_event(
                resolution,
                attempt,
                token="3" * 16,
            )
            with closing(storage.connect_initialized(target)) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertTrue(
                        storage.persist_verification_runner_restart_cleanup_locked(
                            connection,
                            cleanup_event=cleanup,
                        )
                    )
                before_replay = connection.total_changes
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertFalse(
                        storage.persist_verification_runner_restart_cleanup_locked(
                            connection,
                            cleanup_event=cleanup,
                        )
                    )
                self.assertEqual(connection.total_changes, before_replay)

                conflicting_cleanup = replace(
                    cleanup,
                    verification_runner_sandbox_event_id=(
                        "tg_verification_runner_sandbox_event_" + "4" * 16
                    ),
                )
                before_conflict = connection.total_changes
                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.persist_verification_runner_restart_cleanup_locked(
                            connection,
                            cleanup_event=conflicting_cleanup,
                        )
                self.assertEqual(connection.total_changes, before_conflict)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM verification_runner_sandbox_events"
                    ).fetchone()[0],
                    1,
                )
                graph = storage.read_verification_runner_generation_locked(
                    connection,
                    project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=resolution.target_generation,
                )
                self.assertEqual(graph["state"], "restart_cleaned")

    def test_shared_validator_rejects_paired_same_generation_overflow(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-cardinality-",
            dir=ROOT,
        ) as tmp:
            target, _basis, resolution, attempt = self._pending_graph(
                Path(tmp),
                seed="cardinality",
                commit_character="7",
                token="5" * 16,
            )
            extra_resolution_id = (
                "tg_verification_runner_resolution_" + "6" * 16
            )
            extra_resolution = replace(
                resolution,
                verification_runner_resolution_id=extra_resolution_id,
            )
            extra_attempt_values = {
                "project_id": attempt.project_id,
                "task_id": attempt.task_id,
                "target_generation": attempt.target_generation,
                "gate_eligibility_version": attempt.gate_eligibility_version,
                "resolution_id": extra_resolution_id,
                "target_material_digest": attempt.target_material_digest,
                "runner_implementation_digest": (
                    attempt.runner_implementation_digest
                ),
                "sandbox_instance_digest": None,
            }
            extra_attempt = replace(
                attempt,
                verification_runner_attempt_id=(
                    "tg_verification_runner_attempt_" + "6" * 16
                ),
                verification_runner_resolution_id=extra_resolution_id,
                attempt_digest=verification_runner_attempt_digest(
                    extra_attempt_values
                ),
            )
            with closing(storage.connect_initialized(target)) as connection:
                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        self._insert_runner_value(
                            connection,
                            "verification_runner_resolutions",
                            extra_resolution,
                        )
                        self._insert_runner_value(
                            connection,
                            "verification_runner_attempts",
                            extra_attempt,
                        )
                        storage.read_verification_runner_generation_locked(
                            connection,
                            project_id=resolution.project_id,
                            task_id=resolution.task_id,
                            target_generation=resolution.target_generation,
                        )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table_name}"
                        ).fetchone()[0]
                        for table_name in (
                            "verification_runner_resolutions",
                            "verification_runner_attempts",
                        )
                    ),
                    (1, 1),
                )

    def test_pending_terminal_and_standalone_evidence_are_admitted(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-storage-",
            dir=ROOT,
        ) as tmp:
            target, task_id = self._new_target_and_task(Path(tmp), "terminal")
            basis = self._capture_target(
                target,
                task_id,
                commit_character="1",
                now=FIXED_TIME,
            )
            basis = self._basis_with_owner(
                basis,
                project_id=target.project.project_id,
                task_id=task_id,
            )
            resolution, attempt = self._resolution_attempt(
                basis,
                token="1" * 16,
                created_at="2026-08-25T00:00:01Z",
            )
            with closing(storage.connect_initialized(target)) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertTrue(
                        storage.insert_verification_runner_resolution_locked(
                            connection,
                            resolution=resolution,
                            attempt=attempt,
                        )
                    )
                    pending = storage.read_pending_verification_runner_cleanup(
                        connection,
                        project_id=resolution.project_id,
                    )
                    self.assertEqual(len(pending), 1)
                    self.assertEqual(pending[0]["state"], "pending")

            observation, event = self._terminal_values(
                resolution,
                attempt,
                token="2" * 16,
            )
            source, reference, link = self._terminal_evidence(
                resolution,
                observation,
                acceptance_criterion_id=basis["acceptance_criterion_id"],
                token="3" * 16,
            )
            with closing(storage.connect_initialized(target)) as connection:
                early_event = storage.VerificationRunnerSandboxEvent(
                    **{
                        **event.__dict__,
                        "created_at": "2026-08-25T00:00:02Z",
                    }
                )
                with self.assertRaises(storage.StorageError):
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference=reference,
                            criterion_link=link,
                            cleanup_event=early_event,
                        )
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertTrue(
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference=reference,
                            criterion_link=link,
                            cleanup_event=event,
                        )
                    )
                storage.validate_schema20_storage(
                    connection,
                    allow_native_bundle_v2=True,
                )
                graph = storage.read_verification_runner_generation_locked(
                    connection,
                    project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=resolution.target_generation,
                )
                self.assertEqual(graph["state"], "terminal")
                self.assertFalse(
                    storage.has_pending_verification_runner_cleanup(
                        connection,
                        project_id=resolution.project_id,
                    )
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM completion_bundle_members "
                        "WHERE evidence_reference_id = ? "
                        "OR criterion_evidence_link_id = ?",
                        (
                            reference["evidence_reference_id"],
                            link.criterion_evidence_link_id,
                        ),
                    ).fetchone()[0],
                    0,
                )

            with closing(
                storage.connect_snapshot_readonly(target.db_path)
            ) as connection:
                viewer = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at="2026-08-25T00:00:04Z",
                ).snapshot
            serialized_viewer = json.dumps(viewer, sort_keys=True)
            self.assertNotIn(
                observation.verification_runner_observation_id,
                serialized_viewer,
            )
            backup = publish_setup_backup(target, 3)
            self.assertTrue(backup.generation_id.startswith("tg_backup_"))
            recovery = select_managed_backup_for_recovery(target)
            self.assertIsNotNone(recovery)
            self.assertEqual(recovery.schema_version, 20)

            invalid_projection = dict(source.source_projection)
            invalid_projection["runtime_digest"] = _digest("9")
            with self.assertRaises(EvidenceLedgerError):
                EvidenceSource(
                    source_kind="runner_observation",
                    source_state="recorded",
                    source_id=source.source_id,
                    source_projection=invalid_projection,
                )

    def test_shared_validator_rejects_digest_consistent_invalid_plan_id(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-plan-id-",
            dir=ROOT,
        ) as tmp:
            target, task_id = self._new_target_and_task(Path(tmp), "plan-id")
            basis = self._capture_target(
                target,
                task_id,
                commit_character="9",
                now=FIXED_TIME,
            )
            basis = self._basis_with_owner(
                basis,
                project_id=target.project.project_id,
                task_id=task_id,
            )
            resolution, attempt = self._resolution_attempt(
                basis,
                token="9" * 16,
                created_at="2026-08-25T00:00:01Z",
                plan_id="UPPER",
            )
            with closing(storage.connect_initialized(target)) as connection:
                with self.assertRaises(storage.StorageError) as raised:
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.insert_verification_runner_resolution_locked(
                            connection,
                            resolution=resolution,
                            attempt=attempt,
                        )
                self.assertEqual(
                    raised.exception.code,
                    "evidence_ledger_inconsistent",
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table_name}"
                        ).fetchone()[0]
                        for table_name in (
                            "verification_runner_resolutions",
                            "verification_runner_attempts",
                        )
                    ),
                    (0, 0),
                )

    def test_t2_basis_drift_rejects_terminal_then_cleanup_only_closes(self):
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-runner-drift-",
            dir=ROOT,
        ) as tmp:
            target, task_id = self._new_target_and_task(Path(tmp), "drift")
            basis = self._capture_target(
                target,
                task_id,
                commit_character="2",
                now=FIXED_TIME,
            )
            basis = self._basis_with_owner(
                basis,
                project_id=target.project.project_id,
                task_id=task_id,
            )
            resolution, attempt = self._resolution_attempt(
                basis,
                token="4" * 16,
                created_at="2026-08-25T00:00:01Z",
            )
            with closing(storage.connect_initialized(target)) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    storage.insert_verification_runner_resolution_locked(
                        connection,
                        resolution=resolution,
                        attempt=attempt,
                    )

            with closing(storage.connect_initialized(target)) as connection:
                with connection:
                    connection.execute(
                        """
                        UPDATE tasks
                           SET review_target_kind = '',
                               review_target_value = '',
                               review_target_base_revision = '',
                               review_target_generation =
                                   review_target_generation + 1,
                               review_target_capture_version = 0,
                               review_target_authority_snapshot_id = NULL,
                               review_target_acceptance_criterion_id = NULL,
                               review_target_verification_criterion_id = NULL,
                               review_target_artifact_manifest_id = NULL,
                               updated_at = ?
                         WHERE project_id = ? AND task_id = ?
                        """,
                        (
                            "2026-08-25T00:00:02Z",
                            target.project.project_id,
                            task_id,
                        ),
                    )
            observation, terminal_event = self._terminal_values(
                resolution,
                attempt,
                token="5" * 16,
            )
            _source, reference, link = self._terminal_evidence(
                resolution,
                observation,
                acceptance_criterion_id=basis["acceptance_criterion_id"],
                token="6" * 16,
            )
            with closing(storage.connect_initialized(target)) as connection:
                with self.assertRaises(storage.StorageError) as caught:
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        storage.persist_verification_runner_terminal_locked(
                            connection,
                            observation=observation,
                            evidence_reference=reference,
                            criterion_link=link,
                            cleanup_event=terminal_event,
                        )
                self.assertEqual(caught.exception.code, "runner_state_invalid")
                counts = tuple(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "verification_runner_observations",
                        "verification_runner_sandbox_events",
                    )
                )
                self.assertEqual(counts, (0, 0))

                cleanup_values = {
                    "attempt_id": attempt.verification_runner_attempt_id,
                    "event_kind": "attempt_cleanup_succeeded",
                    "project_id": resolution.project_id,
                    "target_generation": resolution.target_generation,
                    "task_id": resolution.task_id,
                    "terminal_observation_id": None,
                }
                cleanup = storage.VerificationRunnerSandboxEvent(
                    verification_runner_sandbox_event_id=(
                        "tg_verification_runner_sandbox_event_" + "7" * 16
                    ),
                    project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=resolution.target_generation,
                    verification_runner_attempt_id=(
                        attempt.verification_runner_attempt_id
                    ),
                    event_kind="attempt_cleanup_succeeded",
                    event_digest=verification_runner_sandbox_event_digest(
                        cleanup_values
                    ),
                    terminal_observation_id=None,
                    created_at="2026-08-25T00:00:04Z",
                )
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertTrue(
                        storage.persist_verification_runner_restart_cleanup_locked(
                            connection,
                            cleanup_event=cleanup,
                        )
                    )
                graph = storage.read_verification_runner_generation_locked(
                    connection,
                    project_id=resolution.project_id,
                    task_id=resolution.task_id,
                    target_generation=resolution.target_generation,
                )
                self.assertEqual(graph["state"], "restart_cleaned")
                self.assertEqual(
                    storage.read_pending_verification_runner_cleanup(
                        connection,
                        project_id=resolution.project_id,
                    ),
                    (),
                )

            next_basis = self._capture_target(
                target,
                task_id,
                commit_character="3",
                now="2026-08-25T00:00:05Z",
            )
            next_basis = self._basis_with_owner(
                next_basis,
                project_id=target.project.project_id,
                task_id=task_id,
            )
            with closing(storage.connect_initialized(target)) as connection:
                next_resolution, next_attempt = self._resolution_attempt(
                    next_basis,
                    token="8" * 16,
                    created_at="2026-08-25T00:00:06Z",
                )
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertTrue(
                        storage.insert_verification_runner_resolution_locked(
                            connection,
                            resolution=next_resolution,
                            attempt=next_attempt,
                        )
                    )


if __name__ == "__main__":
    unittest.main()
