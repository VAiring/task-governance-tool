"""Versioned Runner SQL definitions; storage owns recognition and execution."""

from __future__ import annotations

from task_governance_tool.schema_completion_evidence_bundles import (
    PRIVATE_SCHEMA20_VERSION,
    PRIVATE_SCHEMA21_VERSION,
)


_R3A_SCHEMA20_RUNNER_TABLES = (
    "verification_runner_resolutions",
    "verification_runner_attempts",
    "verification_runner_sandbox_events",
    "verification_runner_observations",
)


def _normalized_schema_sql(statement: str) -> str:
    return " ".join(statement.strip().removesuffix(";").split())


def _verification_runner_table_statements(
    *,
    schema_version: int = PRIVATE_SCHEMA20_VERSION,
) -> tuple[str, ...]:
    if schema_version not in {PRIVATE_SCHEMA20_VERSION, PRIVATE_SCHEMA21_VERSION}:
        raise AssertionError("invalid private Runner schema version")
    statements = (
        """
        CREATE TABLE verification_runner_resolutions (
          verification_runner_resolution_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_resolution_id) =
              length('tg_verification_runner_resolution_') + 16
            AND substr(verification_runner_resolution_id, 1,
              length('tg_verification_runner_resolution_')) =
              'tg_verification_runner_resolution_'
            AND substr(verification_runner_resolution_id,
              length('tg_verification_runner_resolution_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 1),
          authority_snapshot_id TEXT NOT NULL,
          verification_criterion_id TEXT NOT NULL,
          verification_expectation_digest TEXT NOT NULL CHECK (
            length(verification_expectation_digest) = 64
            AND verification_expectation_digest NOT GLOB '*[^0-9a-f]*'
          ),
          verification_criterion_digest TEXT NOT NULL CHECK (
            length(verification_criterion_digest) = 71
            AND substr(verification_criterion_digest, 1, 7) = 'sha256:'
            AND substr(verification_criterion_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          target_kind TEXT NOT NULL CHECK (
            length(target_kind) BETWEEN 1 AND 64
            AND substr(target_kind, 1, 1) GLOB '[a-z]'
            AND substr(target_kind, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT CHECK (
            target_base_revision IS NULL
            OR length(target_base_revision) BETWEEN 1 AND 128
          ),
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          target_capture_version INTEGER NOT NULL CHECK (target_capture_version = 1),
          artifact_manifest_id TEXT NOT NULL,
          target_material_digest TEXT CHECK (
            target_material_digest IS NULL OR (
              length(target_material_digest) = 71
              AND substr(target_material_digest, 1, 7) = 'sha256:'
              AND substr(target_material_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          plan_state TEXT NOT NULL CHECK (
            length(plan_state) BETWEEN 1 AND 64
            AND substr(plan_state, 1, 1) GLOB '[a-z]'
            AND substr(plan_state, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          plan_blob_object_id TEXT CHECK (
            plan_blob_object_id IS NULL
            OR length(plan_blob_object_id) BETWEEN 1 AND 500
          ),
          plan_raw_digest TEXT CHECK (
            plan_raw_digest IS NULL OR (
              length(plan_raw_digest) = 71
              AND substr(plan_raw_digest, 1, 7) = 'sha256:'
              AND substr(plan_raw_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          plan_id TEXT CHECK (plan_id IS NULL OR length(plan_id) BETWEEN 1 AND 200),
          plan_version INTEGER CHECK (plan_version >= 1),
          plan_semantic_digest TEXT CHECK (
            plan_semantic_digest IS NULL OR (
              length(plan_semantic_digest) = 71
              AND substr(plan_semantic_digest, 1, 7) = 'sha256:'
              AND substr(plan_semantic_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          selected_entry_digest TEXT CHECK (
            selected_entry_digest IS NULL OR (
              length(selected_entry_digest) = 71
              AND substr(selected_entry_digest, 1, 7) = 'sha256:'
              AND substr(selected_entry_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          coverage TEXT NOT NULL CHECK (
            length(coverage) BETWEEN 1 AND 64
            AND substr(coverage, 1, 1) GLOB '[a-z]'
            AND substr(coverage, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          step_count INTEGER NOT NULL CHECK (step_count BETWEEN 0 AND 16),
          runner_contract_version INTEGER NOT NULL CHECK (runner_contract_version = 1),
          runner_implementation_version TEXT NOT NULL CHECK (
            runner_implementation_version = 'taskgov-verification-runner/1'
          ),
          runner_implementation_digest TEXT NOT NULL CHECK (
            length(runner_implementation_digest) = 71
            AND substr(runner_implementation_digest, 1, 7) = 'sha256:'
            AND substr(runner_implementation_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          runner_policy_digest TEXT NOT NULL CHECK (
            length(runner_policy_digest) = 71
            AND substr(runner_policy_digest, 1, 7) = 'sha256:'
            AND substr(runner_policy_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          runtime_digest TEXT CHECK (
            runtime_digest IS NULL OR (
              length(runtime_digest) = 71
              AND substr(runtime_digest, 1, 7) = 'sha256:'
              AND substr(runtime_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          gate_eligibility_version INTEGER NOT NULL CHECK (gate_eligibility_version = 0),
          trigger TEXT NOT NULL CHECK (trigger = 'review_target_set_v1'),
          route TEXT NOT NULL CHECK (
            length(route) BETWEEN 1 AND 64
            AND substr(route, 1, 1) GLOB '[a-z]'
            AND substr(route, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          reason TEXT CHECK (
            reason IS NULL OR (
              length(reason) BETWEEN 1 AND 64
              AND substr(reason, 1, 1) GLOB '[a-z]'
              AND substr(reason, 2) NOT GLOB '*[^a-z0-9_]*'
            )
          ),
          idempotency_digest TEXT NOT NULL CHECK (
            length(idempotency_digest) = 71
            AND substr(idempotency_digest, 1, 7) = 'sha256:'
            AND substr(idempotency_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          FOREIGN KEY (project_id, task_id)
            REFERENCES tasks(project_id, task_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (project_id, task_id, artifact_manifest_id)
            REFERENCES artifact_manifests(project_id, task_id, artifact_manifest_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE
        )
        """,
        """
        CREATE TABLE verification_runner_attempts (
          verification_runner_attempt_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_attempt_id) =
              length('tg_verification_runner_attempt_') + 16
            AND substr(verification_runner_attempt_id, 1,
              length('tg_verification_runner_attempt_')) =
              'tg_verification_runner_attempt_'
            AND substr(verification_runner_attempt_id,
              length('tg_verification_runner_attempt_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          gate_eligibility_version INTEGER NOT NULL CHECK (gate_eligibility_version = 0),
          verification_runner_resolution_id TEXT NOT NULL,
          target_material_digest TEXT NOT NULL CHECK (
            length(target_material_digest) = 71
            AND substr(target_material_digest, 1, 7) = 'sha256:'
            AND substr(target_material_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          runner_implementation_digest TEXT NOT NULL CHECK (
            length(runner_implementation_digest) = 71
            AND substr(runner_implementation_digest, 1, 7) = 'sha256:'
            AND substr(runner_implementation_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          attempt_digest TEXT NOT NULL CHECK (
            length(attempt_digest) = 71
            AND substr(attempt_digest, 1, 7) = 'sha256:'
            AND substr(attempt_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          intent_recorded_at TEXT NOT NULL,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) REFERENCES verification_runner_resolutions(
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE
        )
        """,
        """
        CREATE TABLE verification_runner_observations (
          verification_runner_observation_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_observation_id) =
              length('tg_verification_runner_observation_') + 16
            AND substr(verification_runner_observation_id, 1,
              length('tg_verification_runner_observation_')) =
              'tg_verification_runner_observation_'
            AND substr(verification_runner_observation_id,
              length('tg_verification_runner_observation_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          gate_eligibility_version INTEGER NOT NULL CHECK (gate_eligibility_version = 0),
          verification_runner_resolution_id TEXT NOT NULL,
          verification_runner_attempt_id TEXT,
          runner_implementation_digest TEXT NOT NULL CHECK (
            length(runner_implementation_digest) = 71
            AND substr(runner_implementation_digest, 1, 7) = 'sha256:'
            AND substr(runner_implementation_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          route TEXT NOT NULL CHECK (
            length(route) BETWEEN 1 AND 64
            AND substr(route, 1, 1) GLOB '[a-z]'
            AND substr(route, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          launch_state TEXT NOT NULL CHECK (
            length(launch_state) BETWEEN 1 AND 64
            AND substr(launch_state, 1, 1) GLOB '[a-z]'
            AND substr(launch_state, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          outcome TEXT NOT NULL CHECK (
            length(outcome) BETWEEN 1 AND 64
            AND substr(outcome, 1, 1) GLOB '[a-z]'
            AND substr(outcome, 2) NOT GLOB '*[^a-z0-9_]*'
          ),
          reason TEXT CHECK (
            reason IS NULL OR (
              length(reason) BETWEEN 1 AND 64
              AND substr(reason, 1, 1) GLOB '[a-z]'
              AND substr(reason, 2) NOT GLOB '*[^a-z0-9_]*'
            )
          ),
          complete_plan INTEGER NOT NULL CHECK (complete_plan IN (0, 1)),
          total_step_count INTEGER NOT NULL CHECK (total_step_count BETWEEN 0 AND 16),
          completed_step_count INTEGER NOT NULL CHECK (
            completed_step_count BETWEEN 0 AND total_step_count
          ),
          failed_step_ordinal INTEGER CHECK (
            failed_step_ordinal BETWEEN 1 AND total_step_count
          ),
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
          cpu_time_ms INTEGER CHECK (cpu_time_ms >= 0),
          peak_job_memory_bytes INTEGER CHECK (
            peak_job_memory_bytes >= 0
          ),
          total_process_count INTEGER CHECK (
            total_process_count >= 0
          ),
          sanitized_result_digest TEXT NOT NULL CHECK (
            length(sanitized_result_digest) = 71
            AND substr(sanitized_result_digest, 1, 7) = 'sha256:'
            AND substr(sanitized_result_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) REFERENCES verification_runner_resolutions(
            project_id, task_id, target_generation,
            verification_runner_resolution_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) REFERENCES verification_runner_attempts(
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE
        )
        """,
        """
        CREATE TABLE verification_runner_sandbox_events (
          verification_runner_sandbox_event_id TEXT PRIMARY KEY CHECK (
            length(verification_runner_sandbox_event_id) =
              length('tg_verification_runner_sandbox_event_') + 16
            AND substr(verification_runner_sandbox_event_id, 1,
              length('tg_verification_runner_sandbox_event_')) =
              'tg_verification_runner_sandbox_event_'
            AND substr(verification_runner_sandbox_event_id,
              length('tg_verification_runner_sandbox_event_') + 1)
              NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
          verification_runner_attempt_id TEXT NOT NULL,
          event_kind TEXT NOT NULL CHECK (event_kind = 'attempt_cleanup_succeeded'),
          event_digest TEXT NOT NULL CHECK (
            length(event_digest) = 71
            AND substr(event_digest, 1, 7) = 'sha256:'
            AND substr(event_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          terminal_observation_id TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) REFERENCES verification_runner_attempts(
            project_id, task_id, target_generation,
            verification_runner_attempt_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT NOT DEFERRABLE,
          FOREIGN KEY (
            project_id, task_id, target_generation,
            terminal_observation_id
          ) REFERENCES verification_runner_observations(
            project_id, task_id, target_generation,
            verification_runner_observation_id
          ) ON UPDATE RESTRICT ON DELETE RESTRICT
            DEFERRABLE INITIALLY DEFERRED
        )
        """,
    )
    if schema_version == PRIVATE_SCHEMA20_VERSION:
        return statements
    return tuple(
        statement.replace(
            "CHECK (gate_eligibility_version = 0)",
            "CHECK (gate_eligibility_version IN (0, 1))",
        )
        for statement in statements
    )


def _verification_runner_index_statements() -> tuple[str, ...]:
    return (
        """CREATE UNIQUE INDEX idx_verification_runner_resolutions_parent
             ON verification_runner_resolutions(
               project_id, task_id, target_generation,
               verification_runner_resolution_id
             )""",
        """CREATE INDEX idx_verification_runner_resolutions_task_generation
             ON verification_runner_resolutions(
               project_id, task_id, target_generation
             )""",
        """CREATE UNIQUE INDEX idx_verification_runner_attempts_parent
             ON verification_runner_attempts(
               project_id, task_id, target_generation,
               verification_runner_attempt_id
             )""",
        """CREATE INDEX idx_verification_runner_attempts_task_generation
             ON verification_runner_attempts(
               project_id, task_id, target_generation
             )""",
        """CREATE INDEX idx_verification_runner_attempts_resolution
             ON verification_runner_attempts(
               project_id, task_id, target_generation,
               verification_runner_resolution_id
             )""",
        """CREATE INDEX idx_verification_runner_sandbox_events_attempt_kind
             ON verification_runner_sandbox_events(
               project_id, task_id, target_generation,
               verification_runner_attempt_id, event_kind
             )""",
        """CREATE UNIQUE INDEX idx_verification_runner_observations_parent
             ON verification_runner_observations(
               project_id, task_id, target_generation,
               verification_runner_observation_id
             )""",
        """CREATE INDEX idx_verification_runner_observations_task_generation
             ON verification_runner_observations(
               project_id, task_id, target_generation
             )""",
        """CREATE INDEX idx_verification_runner_observations_resolution
             ON verification_runner_observations(
               project_id, task_id, target_generation,
               verification_runner_resolution_id
             )""",
        """CREATE INDEX idx_verification_runner_observations_attempt
             ON verification_runner_observations(
               project_id, task_id, target_generation,
               verification_runner_attempt_id
             ) WHERE verification_runner_attempt_id IS NOT NULL""",
    )


def _verification_runner_trigger_statements(
    *,
    schema_version: int = PRIVATE_SCHEMA20_VERSION,
) -> tuple[str, ...]:
    if schema_version not in {PRIVATE_SCHEMA20_VERSION, PRIVATE_SCHEMA21_VERSION}:
        raise AssertionError("invalid private Runner schema version")
    immutable = tuple(
        f"""CREATE TRIGGER trg_{table_name}_{suffix}
              BEFORE {verb} ON {table_name} FOR EACH ROW
              BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END"""
        for table_name in _R3A_SCHEMA20_RUNNER_TABLES
        for suffix, verb in (("no_update", "UPDATE"), ("no_delete", "DELETE"))
    )
    parent = (
        """
        CREATE TRIGGER trg_verification_runner_resolutions_parent_insert
        BEFORE INSERT ON verification_runner_resolutions FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1
            FROM tasks AS t
            JOIN authority_snapshots AS s
              ON s.project_id = t.project_id
             AND s.task_id = t.task_id
             AND s.authority_snapshot_id = NEW.authority_snapshot_id
            JOIN artifact_manifests AS m
              ON m.project_id = t.project_id
             AND m.task_id = t.task_id
             AND m.artifact_manifest_id = NEW.artifact_manifest_id
            JOIN contract_criteria AS vc
              ON vc.project_id = t.project_id
             AND vc.task_id = t.task_id
             AND vc.criterion_id = NEW.verification_criterion_id
            JOIN authority_snapshot_criteria AS vcm
              ON vcm.project_id = t.project_id
             AND vcm.task_id = t.task_id
             AND vcm.authority_snapshot_id = NEW.authority_snapshot_id
             AND vcm.criterion_kind = 'verification'
             AND vcm.criterion_id = NEW.verification_criterion_id
           WHERE t.project_id = NEW.project_id
             AND t.task_id = NEW.task_id
             AND t.current_contract_revision = NEW.contract_revision
             AND t.review_target_authority_snapshot_id = NEW.authority_snapshot_id
             AND t.review_target_acceptance_criterion_id IS m.acceptance_criterion_id
             AND t.review_target_verification_criterion_id =
                   NEW.verification_criterion_id
             AND t.review_target_kind = NEW.target_kind
             AND t.review_target_value = NEW.target_value
             AND t.review_target_base_revision =
                   COALESCE(NEW.target_base_revision, '')
             AND t.review_target_generation = NEW.target_generation
             AND t.review_target_capture_version = NEW.target_capture_version
             AND t.review_target_artifact_manifest_id = NEW.artifact_manifest_id
             AND t.review_target_runner_basis_version = 0
             AND s.contract_revision = NEW.contract_revision
             AND s.verification_digest = NEW.verification_expectation_digest
             AND vc.criterion_kind = 'verification'
             AND vc.digest = NEW.verification_criterion_digest
             AND m.authority_snapshot_id = NEW.authority_snapshot_id
             AND m.acceptance_criterion_id IS
                   t.review_target_acceptance_criterion_id
             AND m.verification_criterion_id = NEW.verification_criterion_id
             AND m.target_kind = NEW.target_kind
             AND m.target_value = NEW.target_value
             AND m.target_base_revision = COALESCE(NEW.target_base_revision, '')
             AND m.target_generation = NEW.target_generation
             AND (
               t.review_target_acceptance_criterion_id IS NULL
               OR EXISTS (
                 SELECT 1
                   FROM contract_criteria AS ac
                   JOIN authority_snapshot_criteria AS acm
                     ON acm.project_id = ac.project_id
                    AND acm.task_id = ac.task_id
                    AND acm.authority_snapshot_id = NEW.authority_snapshot_id
                    AND acm.criterion_kind = 'acceptance'
                    AND acm.criterion_id = ac.criterion_id
                  WHERE ac.project_id = t.project_id
                    AND ac.task_id = t.task_id
                    AND ac.criterion_id =
                          t.review_target_acceptance_criterion_id
                    AND ac.criterion_kind = 'acceptance'
               )
             )
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
        """
        CREATE TRIGGER trg_verification_runner_attempts_parent_insert
        BEFORE INSERT ON verification_runner_attempts FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1 FROM verification_runner_resolutions AS r
           WHERE r.project_id = NEW.project_id
             AND r.task_id = NEW.task_id
             AND r.target_generation = NEW.target_generation
             AND r.verification_runner_resolution_id =
                   NEW.verification_runner_resolution_id
             AND r.target_material_digest = NEW.target_material_digest
             AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
        """
        CREATE TRIGGER trg_verification_runner_observations_parent_insert
        BEFORE INSERT ON verification_runner_observations FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1 FROM verification_runner_resolutions AS r
           WHERE r.project_id = NEW.project_id
             AND r.task_id = NEW.task_id
             AND r.target_generation = NEW.target_generation
             AND r.verification_runner_resolution_id =
                   NEW.verification_runner_resolution_id
             AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
             AND (
               NEW.verification_runner_attempt_id IS NULL
               OR EXISTS (
                 SELECT 1 FROM verification_runner_attempts AS a
                  WHERE a.project_id = NEW.project_id
                    AND a.task_id = NEW.task_id
                    AND a.target_generation = NEW.target_generation
                    AND a.verification_runner_attempt_id =
                          NEW.verification_runner_attempt_id
                    AND a.verification_runner_resolution_id =
                          NEW.verification_runner_resolution_id
                    AND a.runner_implementation_digest =
                          NEW.runner_implementation_digest
               )
             )
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
        """
        CREATE TRIGGER trg_verification_runner_sandbox_events_parent_insert
        BEFORE INSERT ON verification_runner_sandbox_events FOR EACH ROW
        WHEN NOT EXISTS (
          SELECT 1 FROM verification_runner_attempts AS a
           WHERE a.project_id = NEW.project_id
             AND a.task_id = NEW.task_id
             AND a.target_generation = NEW.target_generation
             AND a.verification_runner_attempt_id =
                   NEW.verification_runner_attempt_id
             AND (
               NEW.terminal_observation_id IS NULL
               OR EXISTS (
                 SELECT 1 FROM verification_runner_observations AS o
                  WHERE o.project_id = NEW.project_id
                    AND o.task_id = NEW.task_id
                    AND o.target_generation = NEW.target_generation
                    AND o.verification_runner_observation_id =
                          NEW.terminal_observation_id
                    AND o.verification_runner_attempt_id =
                          NEW.verification_runner_attempt_id
               )
             )
        )
        BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END
        """,
    )
    statements = (*immutable, *parent)
    if schema_version == PRIVATE_SCHEMA20_VERSION:
        return statements
    result: list[str] = []
    for statement in statements:
        normalized = _normalized_schema_sql(statement)
        if "trg_verification_runner_resolutions_parent_insert" in normalized:
            statement = statement.replace(
                "AND t.review_target_runner_basis_version = 0",
                """AND (
               (NEW.gate_eligibility_version = 0
                 AND t.review_target_runner_basis_version = 0)
               OR
               (NEW.gate_eligibility_version = 1
                 AND t.review_target_runner_basis_version = 2)
             )""",
            )
        elif "trg_verification_runner_attempts_parent_insert" in normalized:
            statement = statement.replace(
                "AND r.runner_implementation_digest =\n                   NEW.runner_implementation_digest",
                """AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
             AND r.gate_eligibility_version = NEW.gate_eligibility_version""",
            )
        elif "trg_verification_runner_observations_parent_insert" in normalized:
            statement = statement.replace(
                "AND r.runner_implementation_digest =\n                   NEW.runner_implementation_digest",
                """AND r.runner_implementation_digest =
                   NEW.runner_implementation_digest
             AND r.gate_eligibility_version = NEW.gate_eligibility_version""",
            ).replace(
                "AND a.runner_implementation_digest =\n                          NEW.runner_implementation_digest",
                """AND a.runner_implementation_digest =
                          NEW.runner_implementation_digest
                    AND a.gate_eligibility_version =
                          NEW.gate_eligibility_version""",
            )
        result.append(statement)
    return tuple(result)
