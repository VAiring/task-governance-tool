"""Ordered Verification Receipt SQL definitions; storage owns execution."""

from __future__ import annotations


def verification_receipt_schema_statements() -> tuple[str, ...]:
    """Return the exact schema-v17 Receipt objects in migration order."""

    return (
        """
        CREATE TABLE verification_receipts (
          verification_receipt_id TEXT PRIMARY KEY
            CHECK (
              length(verification_receipt_id) = 40
              AND substr(verification_receipt_id, 1, 24) =
                    'tg_verification_receipt_'
              AND substr(verification_receipt_id, 25)
                    NOT GLOB '*[^0-9a-f]*'
            ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          contract_revision INTEGER NOT NULL
            CHECK (contract_revision >= 0),
          verification_expectation_digest TEXT NOT NULL
            CHECK (
              length(verification_expectation_digest) = 64
              AND verification_expectation_digest
                    NOT GLOB '*[^0-9a-f]*'
            ),
          command_label TEXT NOT NULL
            CHECK (
              length(command_label) BETWEEN 1 AND 200
              AND command_label = trim(command_label)
            ),
          result TEXT NOT NULL
            CHECK (result IN ('pass', 'fail', 'timeout')),
          duration_ms INTEGER NOT NULL
            CHECK (duration_ms >= 0),
          scope_coverage TEXT NOT NULL
            CHECK (scope_coverage IN ('full', 'partial')),
          target_kind TEXT NOT NULL
            CHECK (target_kind IN (
              'git_commit', 'diff_fingerprint',
              'external_revision', 'git_snapshot'
            )),
          target_value TEXT NOT NULL
            CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL
            CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL
            CHECK (target_generation >= 1),
          created_at TEXT NOT NULL,

          FOREIGN KEY (project_id, task_id)
            REFERENCES tasks(project_id, task_id),

          CHECK (
            (target_kind = 'git_snapshot'
              AND target_base_revision != '')
            OR
            (target_kind IN (
                'git_commit', 'diff_fingerprint', 'external_revision'
              )
              AND target_base_revision = '')
          )
        )
        """,
        """
        CREATE UNIQUE INDEX idx_verification_receipts_task_generation
          ON verification_receipts(project_id, task_id, target_generation)
        """,
        """
        CREATE INDEX idx_verification_receipts_exact_basis
          ON verification_receipts(
            project_id, task_id, contract_revision,
            verification_expectation_digest,
            target_kind, target_value, target_base_revision,
            target_generation
          )
        """,
        """
        CREATE INDEX idx_verification_receipts_recent
          ON verification_receipts(
            project_id, task_id, created_at DESC,
            verification_receipt_id DESC
          )
        """,
        """
        ALTER TABLE task_completion_cycles
          ADD COLUMN verification_basis_version INTEGER NOT NULL DEFAULT 0
            CHECK (verification_basis_version IN (0, 1))
        """,
        """
        ALTER TABLE task_completion_cycles
          ADD COLUMN verification_expectation_digest TEXT
            CHECK (
              verification_expectation_digest IS NULL
              OR (
                length(verification_expectation_digest) = 64
                AND verification_expectation_digest
                      NOT GLOB '*[^0-9a-f]*'
              )
            )
        """,
        """
        ALTER TABLE task_completion_cycles
          ADD COLUMN verification_receipt_id TEXT
            REFERENCES verification_receipts(verification_receipt_id)
        """,
        """
        CREATE TRIGGER trg_verification_receipts_no_update
        BEFORE UPDATE ON verification_receipts
        BEGIN
          SELECT RAISE(ABORT, 'immutable_verification_receipt');
        END
        """,
        """
        CREATE TRIGGER trg_verification_receipts_no_delete
        BEFORE DELETE ON verification_receipts
        BEGIN
          SELECT RAISE(ABORT, 'immutable_verification_receipt');
        END
        """,
        """
        CREATE TRIGGER trg_verification_receipts_locked_basis_insert
        BEFORE INSERT ON verification_receipts
        WHEN NOT EXISTS (
          SELECT 1
            FROM tasks AS task
           WHERE task.project_id = NEW.project_id
             AND task.task_id = NEW.task_id
             AND task.status IN ('in_progress', 'review_pending')
             AND taskgov_verification_specified(task.verification) = 1
             AND task.current_contract_revision = NEW.contract_revision
             AND task.review_target_kind = NEW.target_kind
             AND task.review_target_value = NEW.target_value
             AND task.review_target_base_revision = NEW.target_base_revision
             AND task.review_target_generation = NEW.target_generation
        )
        BEGIN
          SELECT RAISE(ABORT, 'verification_receipt_basis_mismatch');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_verification_basis_insert
        BEFORE INSERT ON task_completion_cycles
        WHEN NOT (
          (
            NEW.verification_basis_version = 0
            AND NEW.verification_expectation_digest IS NULL
            AND NEW.verification_receipt_id IS NULL
            AND NEW.origin = 'legacy_current_done'
            AND NEW.completeness = 'partial'
            AND EXISTS (
              SELECT 1
                FROM tasks AS task
               WHERE task.project_id = NEW.project_id
                 AND task.task_id = NEW.task_id
                 AND task.status = 'done'
                 AND task.completion_history_coverage = 'legacy_unknown'
            )
            AND NOT EXISTS (
              SELECT 1
                FROM task_completion_cycles AS earlier
               WHERE earlier.project_id = NEW.project_id
                 AND earlier.task_id = NEW.task_id
            )
          )
          OR
          (
            NEW.verification_basis_version = 1
            AND NEW.verification_expectation_digest IS NOT NULL
            AND NEW.origin = 'native_done'
            AND EXISTS (
              SELECT 1
                FROM tasks AS task
               WHERE task.project_id = NEW.project_id
                 AND task.task_id = NEW.task_id
                 AND task.current_contract_revision = NEW.contract_revision
                 AND task.review_target_kind = NEW.review_target_kind
                 AND task.review_target_value = NEW.review_target_value
                 AND task.review_target_base_revision =
                       NEW.review_target_base_revision
                 AND task.review_target_generation =
                       NEW.review_target_generation
                 AND (
                   (
                     taskgov_verification_specified(task.verification) = 0
                     AND NEW.verification_expectation = 'unspecified'
                     AND NEW.verification_receipt_id IS NULL
                   )
                   OR
                   (
                     taskgov_verification_specified(task.verification) = 1
                     AND NEW.verification_expectation = 'specified'
                     AND NEW.verification_receipt_id IS NOT NULL
                     AND EXISTS (
                       SELECT 1
                         FROM verification_receipts AS receipt
                        WHERE receipt.verification_receipt_id =
                              NEW.verification_receipt_id
                          AND receipt.project_id = NEW.project_id
                          AND receipt.task_id = NEW.task_id
                          AND receipt.contract_revision =
                                NEW.contract_revision
                          AND receipt.verification_expectation_digest =
                                NEW.verification_expectation_digest
                          AND receipt.target_kind = NEW.review_target_kind
                          AND receipt.target_value = NEW.review_target_value
                          AND receipt.target_base_revision =
                                NEW.review_target_base_revision
                          AND receipt.target_generation =
                                NEW.review_target_generation
                          AND receipt.result = 'pass'
                          AND receipt.scope_coverage = 'full'
                     )
                   )
                 )
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_verification_basis');
        END
        """,
    )


def _task_completion_cycle_verification_basis_v21_trigger_sql() -> str:
    """Return the closed schema-v21 completion-basis insert guard."""

    return """
    CREATE TRIGGER trg_task_completion_cycles_verification_basis_insert
    BEFORE INSERT ON task_completion_cycles
    WHEN NOT (
      (
        NEW.verification_basis_version = 0
        AND NEW.verification_expectation_digest IS NULL
        AND NEW.verification_receipt_id IS NULL
        AND NEW.verification_basis_kind IS NULL
        AND NEW.verification_runner_observation_id IS NULL
        AND NEW.origin = 'legacy_current_done'
        AND NEW.completeness = 'partial'
        AND EXISTS (
          SELECT 1 FROM tasks AS task
           WHERE task.project_id = NEW.project_id
             AND task.task_id = NEW.task_id
             AND task.status = 'done'
             AND task.completion_history_coverage = 'legacy_unknown'
        )
        AND NOT EXISTS (
          SELECT 1 FROM task_completion_cycles AS earlier
           WHERE earlier.project_id = NEW.project_id
             AND earlier.task_id = NEW.task_id
        )
      )
      OR
      (
        NEW.verification_basis_version = 1
        AND NEW.verification_expectation_digest IS NOT NULL
        AND NEW.origin = 'native_done'
        AND EXISTS (
          SELECT 1 FROM tasks AS task
           WHERE task.project_id = NEW.project_id
             AND task.task_id = NEW.task_id
             AND task.current_contract_revision = NEW.contract_revision
             AND task.review_target_kind = NEW.review_target_kind
             AND task.review_target_value = NEW.review_target_value
             AND task.review_target_base_revision = NEW.review_target_base_revision
             AND task.review_target_generation = NEW.review_target_generation
             AND (
               (
                 task.review_target_runner_basis_version = 0
                 AND NEW.verification_runner_observation_id IS NULL
                 AND (
                   (
                     taskgov_verification_specified(task.verification) = 0
                     AND NEW.verification_expectation = 'unspecified'
                     AND NEW.verification_basis_kind = 'not_required'
                     AND NEW.verification_receipt_id IS NULL
                   )
                   OR
                   (
                     taskgov_verification_specified(task.verification) = 1
                     AND NEW.verification_expectation = 'specified'
                     AND NEW.verification_basis_kind = 'caller_attestation'
                     AND EXISTS (
                       SELECT 1 FROM verification_receipts AS receipt
                        WHERE receipt.verification_receipt_id =
                              NEW.verification_receipt_id
                          AND receipt.project_id = NEW.project_id
                          AND receipt.task_id = NEW.task_id
                          AND receipt.contract_revision = NEW.contract_revision
                          AND receipt.verification_expectation_digest =
                                NEW.verification_expectation_digest
                          AND receipt.target_kind = NEW.review_target_kind
                          AND receipt.target_value = NEW.review_target_value
                          AND receipt.target_base_revision =
                                NEW.review_target_base_revision
                          AND receipt.target_generation =
                                NEW.review_target_generation
                          AND receipt.result = 'pass'
                          AND receipt.scope_coverage = 'full'
                     )
                   )
                 )
               )
               OR
               (
                 task.review_target_runner_basis_version = 2
                 AND taskgov_verification_specified(task.verification) = 1
                 AND NEW.verification_expectation = 'specified'
                 AND EXISTS (
                   SELECT 1
                     FROM verification_runner_resolutions AS resolution
                     JOIN verification_runner_attempts AS attempt
                       ON attempt.project_id = resolution.project_id
                      AND attempt.task_id = resolution.task_id
                      AND attempt.target_generation = resolution.target_generation
                      AND attempt.verification_runner_resolution_id =
                            resolution.verification_runner_resolution_id
                      AND attempt.gate_eligibility_version = 1
                     JOIN verification_runner_observations AS observation
                       ON observation.project_id = resolution.project_id
                      AND observation.task_id = resolution.task_id
                      AND observation.target_generation =
                            resolution.target_generation
                      AND observation.verification_runner_resolution_id =
                            resolution.verification_runner_resolution_id
                      AND observation.verification_runner_attempt_id =
                            attempt.verification_runner_attempt_id
                      AND observation.gate_eligibility_version = 1
                     JOIN verification_runner_sandbox_events AS cleanup
                       ON cleanup.project_id = observation.project_id
                      AND cleanup.task_id = observation.task_id
                      AND cleanup.target_generation = observation.target_generation
                      AND cleanup.verification_runner_attempt_id =
                            attempt.verification_runner_attempt_id
                      AND cleanup.terminal_observation_id =
                            observation.verification_runner_observation_id
                     JOIN evidence_references AS reference
                       ON reference.project_id = observation.project_id
                      AND reference.task_id = observation.task_id
                      AND reference.source_kind = 'runner_observation'
                      AND reference.source_id =
                            observation.verification_runner_observation_id
                     JOIN criterion_evidence_links AS link
                       ON link.project_id = reference.project_id
                      AND link.task_id = reference.task_id
                      AND link.evidence_reference_id =
                            reference.evidence_reference_id
                      AND link.criterion_id =
                            resolution.verification_criterion_id
                      AND link.relation = 'runner_observation'
                    WHERE resolution.project_id = NEW.project_id
                      AND resolution.task_id = NEW.task_id
                      AND resolution.contract_revision = NEW.contract_revision
                      AND resolution.verification_expectation_digest =
                            NEW.verification_expectation_digest
                      AND resolution.authority_snapshot_id =
                            task.review_target_authority_snapshot_id
                      AND resolution.verification_criterion_id =
                            task.review_target_verification_criterion_id
                      AND resolution.target_kind = NEW.review_target_kind
                      AND resolution.target_value = NEW.review_target_value
                      AND COALESCE(resolution.target_base_revision, '') =
                            NEW.review_target_base_revision
                      AND resolution.target_generation =
                            NEW.review_target_generation
                      AND resolution.artifact_manifest_id =
                            task.review_target_artifact_manifest_id
                      AND resolution.gate_eligibility_version = 1
                      AND (
                        (
                          NEW.verification_basis_kind = 'caller_attestation'
                          AND NEW.verification_runner_observation_id IS NULL
                          AND observation.route = 'm21_fallback'
                          AND observation.launch_state = 'no_launch'
                          AND observation.outcome = 'blocked_prelaunch'
                          AND observation.reason IN (
                            'runtime_unavailable', 'process_setup_failed'
                          )
                          AND observation.complete_plan = 0
                          AND NEW.verification_receipt_id IS NOT NULL
                          AND EXISTS (
                            SELECT 1 FROM verification_receipts AS receipt
                             WHERE receipt.verification_receipt_id =
                                   NEW.verification_receipt_id
                               AND receipt.project_id = NEW.project_id
                               AND receipt.task_id = NEW.task_id
                               AND receipt.contract_revision =
                                     NEW.contract_revision
                               AND receipt.verification_expectation_digest =
                                     NEW.verification_expectation_digest
                               AND receipt.target_kind = NEW.review_target_kind
                               AND receipt.target_value = NEW.review_target_value
                               AND receipt.target_base_revision =
                                     NEW.review_target_base_revision
                               AND receipt.target_generation =
                                     NEW.review_target_generation
                               AND receipt.result = 'pass'
                               AND receipt.scope_coverage = 'full'
                          )
                        )
                        OR
                        (
                          NEW.verification_basis_kind = 'runner_observation'
                          AND NEW.verification_receipt_id IS NULL
                          AND NEW.verification_runner_observation_id =
                                observation.verification_runner_observation_id
                          AND observation.route = 'runner'
                          AND observation.launch_state = 'launched'
                          AND observation.outcome = 'pass'
                          AND observation.reason IS NULL
                          AND observation.complete_plan = 1
                          AND observation.total_step_count =
                                resolution.step_count
                          AND observation.completed_step_count =
                                resolution.step_count
                          AND observation.failed_step_ordinal IS NULL
                        )
                      )
                 )
               )
             )
        )
      )
    )
    BEGIN
      SELECT RAISE(ABORT, 'invalid_completion_verification_basis');
    END
    """
