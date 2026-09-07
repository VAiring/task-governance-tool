"""Ordered Evidence Ledger SQL definitions; storage owns execution."""

from __future__ import annotations


_REVIEW_PROVENANCE_GUARD_TRIGGER_SQL = """
CREATE TRIGGER trg_review_receipts_provenance_basis_insert
BEFORE INSERT ON review_receipts
WHEN NOT (
  (
    NEW.receipt_kind = 'not_required'
    AND NEW.review_provenance_basis_version = 0
    AND NEW.review_provenance_id IS NULL
  )
  OR
  (
    NEW.receipt_kind IN ('independent', 'self_review_fallback')
    AND NEW.review_provenance_basis_version = 1
    AND NEW.review_provenance_id IS NOT NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'invalid_review_provenance_basis');
END
"""


def evidence_ledger_capture_schema_statements() -> tuple[str, ...]:
    """Return the additive schema-v18 capture foundation in migration order."""

    statements = (
        """
        CREATE TABLE authority_snapshots (
          authority_snapshot_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          generation INTEGER NOT NULL CHECK (generation > 0),
          task_title TEXT NOT NULL CHECK (length(task_title) BETWEEN 1 AND 200),
          task_description TEXT NOT NULL CHECK (length(task_description) <= 4000),
          review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
          verification TEXT NOT NULL CHECK (length(verification) <= 1000),
          verification_digest TEXT NOT NULL CHECK (
            length(verification_digest) = 64
            AND verification_digest NOT GLOB '*[^0-9a-f]*'
          ),
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          contract_state TEXT NOT NULL CHECK (
            contract_state IN ('contract_specified', 'contract_unspecified')
          ),
          contract_scope TEXT NOT NULL,
          contract_acceptance TEXT NOT NULL,
          contract_constraints TEXT NOT NULL,
          contract_authority_ref TEXT NOT NULL,
          basis_digest TEXT NOT NULL CHECK (
            length(basis_digest) = 71
            AND substr(basis_digest, 1, 7) = 'sha256:'
            AND substr(basis_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          producer_class TEXT NOT NULL CHECK (
            producer_class IN ('taskgov_core', 'legacy_migration')
          ),
          producer_version INTEGER NOT NULL CHECK (producer_version = 1),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, generation),
          UNIQUE (project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
        )
        """,
        """
        CREATE TABLE contract_criteria (
          criterion_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          criterion_kind TEXT NOT NULL CHECK (
            criterion_kind IN ('acceptance', 'verification')
          ),
          criterion_text TEXT NOT NULL,
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, criterion_kind, digest),
          UNIQUE (project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
        )
        """,
        """
        CREATE TABLE authority_snapshot_criteria (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          authority_snapshot_id TEXT NOT NULL,
          criterion_kind TEXT NOT NULL CHECK (
            criterion_kind IN ('acceptance', 'verification')
          ),
          criterion_id TEXT NOT NULL,
          PRIMARY KEY (authority_snapshot_id, criterion_kind),
          UNIQUE (authority_snapshot_id, criterion_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id, criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id)
        )
        """,
        """
        CREATE TABLE review_receipt_provenance (
          review_provenance_id TEXT PRIMARY KEY,
          review_receipt_id TEXT NOT NULL UNIQUE,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          provenance_version INTEGER NOT NULL CHECK (provenance_version = 1),
          reviewer_class TEXT NOT NULL CHECK (reviewer_class IN (
            'human', 'llm', 'deterministic_tool', 'hybrid', 'unknown'
          )),
          model_state TEXT NOT NULL CHECK (model_state IN (
            'declared', 'not_applicable', 'unknown'
          )),
          declared_model_id TEXT,
          skill_state TEXT NOT NULL CHECK (skill_state IN (
            'declared', 'not_applicable', 'not_used', 'unknown'
          )),
          declared_skill_id TEXT,
          declared_skill_version TEXT,
          context_relation TEXT NOT NULL CHECK (context_relation IN (
            'same_context', 'forked_context', 'fresh_context',
            'external_context', 'not_applicable', 'unknown'
          )),
          assurance_class TEXT NOT NULL CHECK (assurance_class = 'bound_attestation'),
          producer_class TEXT NOT NULL CHECK (producer_class = 'trusted_caller'),
          producer_version INTEGER NOT NULL CHECK (producer_version = 1),
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, review_provenance_id),
          FOREIGN KEY (review_receipt_id) REFERENCES review_receipts(review_receipt_id)
            DEFERRABLE INITIALLY DEFERRED,
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id)
        )
        """,
        """
        CREATE TABLE review_receipt_provenance_codes (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          review_provenance_id TEXT NOT NULL,
          code_kind TEXT NOT NULL CHECK (code_kind IN ('profile', 'lens', 'method')),
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          code TEXT NOT NULL,
          PRIMARY KEY (review_provenance_id, code_kind, ordinal),
          UNIQUE (review_provenance_id, code_kind, code),
          FOREIGN KEY (project_id, task_id, review_provenance_id)
            REFERENCES review_receipt_provenance(
              project_id, task_id, review_provenance_id
            )
        )
        """,
        """
        CREATE TABLE artifact_manifests (
          artifact_manifest_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          state TEXT NOT NULL CHECK (state IN ('complete_git', 'opaque_target')),
          object_format TEXT CHECK (object_format IS NULL OR object_format IN ('sha1', 'sha256')),
          comparison_base TEXT,
          target_kind TEXT NOT NULL CHECK (target_kind IN (
            'git_commit', 'diff_fingerprint', 'external_revision', 'git_snapshot'
          )),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          authority_snapshot_id TEXT NOT NULL,
          acceptance_criterion_id TEXT,
          verification_criterion_id TEXT,
          omission_code TEXT CHECK (
            omission_code IS NULL OR omission_code = 'artifact_content_not_observed'
          ),
          entry_count INTEGER NOT NULL CHECK (entry_count BETWEEN 0 AND 10000),
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, target_generation),
          UNIQUE (project_id, task_id, artifact_manifest_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id, acceptance_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id)
        )
        """,
        """
        CREATE TABLE artifact_manifest_entries (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          artifact_manifest_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          entry_kind TEXT NOT NULL CHECK (entry_kind IN ('add', 'modify', 'delete', 'rename')),
          old_path TEXT,
          new_path TEXT,
          before_mode TEXT,
          before_object_id TEXT,
          after_mode TEXT,
          after_object_id TEXT,
          PRIMARY KEY (artifact_manifest_id, ordinal),
          FOREIGN KEY (project_id, task_id, artifact_manifest_id)
            REFERENCES artifact_manifests(
              project_id, task_id, artifact_manifest_id
            )
        )
        """,
        """
        CREATE TABLE evidence_references (
          evidence_reference_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          source_kind TEXT NOT NULL CHECK (source_kind IN (
            'artifact_manifest', 'verification_receipt', 'review_receipt',
            'review_finding', 'completion_evidence', 'derived_analysis',
            'runner_observation'
          )),
          source_state TEXT NOT NULL,
          source_id TEXT NOT NULL,
          assurance_class TEXT NOT NULL CHECK (assurance_class IN (
            'machine_observed', 'bound_attestation', 'deterministically_derived',
            'external_reference', 'legacy_unknown', 'llm_derived'
          )),
          producer_class TEXT NOT NULL CHECK (producer_class IN (
            'taskgov_core', 'taskgov_git', 'trusted_caller', 'legacy_migration',
            'external_system', 'batch_analyzer', 'verification_runner'
          )),
          producer_version INTEGER NOT NULL CHECK (producer_version > 0),
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          authority_snapshot_id TEXT NOT NULL,
          acceptance_criterion_id TEXT,
          verification_criterion_id TEXT,
          target_kind TEXT NOT NULL CHECK (target_kind IN (
            'git_commit', 'diff_fingerprint', 'external_revision', 'git_snapshot'
          )),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          completion_cycle_id TEXT,
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          created_at TEXT NOT NULL,
          UNIQUE (project_id, task_id, source_kind, source_id),
          UNIQUE (project_id, task_id, evidence_reference_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(project_id, task_id, authority_snapshot_id),
          FOREIGN KEY (project_id, task_id, acceptance_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (completion_cycle_id) REFERENCES task_completion_cycles(completion_cycle_id)
            DEFERRABLE INITIALLY DEFERRED
        )
        """,
        """ALTER TABLE tasks ADD COLUMN current_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE tasks ADD COLUMN current_authority_snapshot_generation INTEGER NOT NULL DEFAULT 0
             CHECK (current_authority_snapshot_generation >= 0)""",
        """ALTER TABLE tasks ADD COLUMN review_target_capture_version INTEGER NOT NULL DEFAULT 0
             CHECK (review_target_capture_version IN (0, 1))""",
        """ALTER TABLE tasks ADD COLUMN review_target_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE tasks ADD COLUMN review_target_acceptance_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """ALTER TABLE tasks ADD COLUMN review_target_verification_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """ALTER TABLE tasks ADD COLUMN review_target_artifact_manifest_id TEXT
             REFERENCES artifact_manifests(artifact_manifest_id)""",
        """ALTER TABLE review_receipts ADD COLUMN review_provenance_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (review_provenance_basis_version IN (0, 1))""",
        """ALTER TABLE review_receipts ADD COLUMN review_provenance_id TEXT
             REFERENCES review_receipt_provenance(review_provenance_id)
             DEFERRABLE INITIALLY DEFERRED""",
        """ALTER TABLE verification_receipts ADD COLUMN verification_subject_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (verification_subject_basis_version IN (0, 1))""",
        """ALTER TABLE verification_receipts ADD COLUMN subject_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE verification_receipts ADD COLUMN subject_verification_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """ALTER TABLE task_completion_cycles ADD COLUMN verification_subject_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (verification_subject_basis_version IN (0, 1))""",
        """ALTER TABLE task_completion_cycles ADD COLUMN subject_authority_snapshot_id TEXT
             REFERENCES authority_snapshots(authority_snapshot_id)""",
        """ALTER TABLE task_completion_cycles ADD COLUMN subject_verification_criterion_id TEXT
             REFERENCES contract_criteria(criterion_id)""",
        """CREATE UNIQUE INDEX idx_authority_snapshots_task_generation
             ON authority_snapshots(project_id, task_id, generation)""",
        """CREATE UNIQUE INDEX idx_contract_criteria_task_kind_digest
             ON contract_criteria(project_id, task_id, criterion_kind, digest)""",
        """CREATE UNIQUE INDEX idx_review_provenance_receipt
             ON review_receipt_provenance(review_receipt_id)""",
        """CREATE UNIQUE INDEX idx_artifact_manifests_target
             ON artifact_manifests(project_id, task_id, target_generation)""",
        """CREATE UNIQUE INDEX idx_evidence_references_source
             ON evidence_references(project_id, task_id, source_kind, source_id)""",
    )
    immutable_tables = (
        "authority_snapshots",
        "contract_criteria",
        "authority_snapshot_criteria",
        "review_receipt_provenance",
        "review_receipt_provenance_codes",
        "artifact_manifests",
        "artifact_manifest_entries",
        "evidence_references",
    )
    immutable_triggers = tuple(
        statement
        for table_name in immutable_tables
        for statement in (
            f"""CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_evidence_ledger'); END""",
            f"""CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_evidence_ledger'); END""",
        )
    )
    subject_triggers = (
        _REVIEW_PROVENANCE_GUARD_TRIGGER_SQL,
        """
        CREATE TRIGGER trg_verification_receipts_subject_basis_insert
        BEFORE INSERT ON verification_receipts
        WHEN NOT (
          NEW.verification_subject_basis_version = 1
          AND NEW.command_label = 'taskgov-owned-verification-subject-v1'
          AND NEW.subject_authority_snapshot_id IS NOT NULL
          AND NEW.subject_verification_criterion_id IS NOT NULL
          AND EXISTS (
            SELECT 1
              FROM tasks AS task
             WHERE task.project_id = NEW.project_id
               AND task.task_id = NEW.task_id
               AND task.review_target_kind = NEW.target_kind
               AND task.review_target_value = NEW.target_value
               AND task.review_target_base_revision = NEW.target_base_revision
               AND task.review_target_generation = NEW.target_generation
               AND task.review_target_capture_version = 1
               AND task.review_target_authority_snapshot_id =
                     NEW.subject_authority_snapshot_id
               AND task.review_target_verification_criterion_id =
                     NEW.subject_verification_criterion_id
               AND task.review_target_artifact_manifest_id IS NOT NULL
          )
          AND EXISTS (
            SELECT 1
              FROM authority_snapshot_criteria AS link
             WHERE link.project_id = NEW.project_id
               AND link.task_id = NEW.task_id
               AND link.authority_snapshot_id =
                     NEW.subject_authority_snapshot_id
               AND link.criterion_kind = 'verification'
               AND link.criterion_id = NEW.subject_verification_criterion_id
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_verification_subject_basis');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_subject_basis_insert
        BEFORE INSERT ON task_completion_cycles
        WHEN NOT (
          (
            NEW.origin = 'legacy_current_done'
            AND NEW.completeness = 'partial'
            AND NEW.verification_subject_basis_version = 0
            AND NEW.subject_authority_snapshot_id IS NULL
            AND NEW.subject_verification_criterion_id IS NULL
          )
          OR
          (
            NEW.origin = 'native_done'
            AND NEW.completeness = 'complete'
            AND NEW.verification_subject_basis_version = 1
            AND EXISTS (
              SELECT 1
                FROM tasks AS task
               WHERE task.project_id = NEW.project_id
                 AND task.task_id = NEW.task_id
                 AND task.review_target_kind = NEW.review_target_kind
                 AND task.review_target_value = NEW.review_target_value
                 AND task.review_target_base_revision =
                       NEW.review_target_base_revision
                 AND task.review_target_generation = NEW.review_target_generation
                 AND task.review_target_capture_version = 1
                 AND task.review_target_artifact_manifest_id IS NOT NULL
                 AND (
                   (
                     NEW.verification_expectation = 'specified'
                     AND NEW.subject_authority_snapshot_id =
                           task.review_target_authority_snapshot_id
                     AND NEW.subject_verification_criterion_id =
                           task.review_target_verification_criterion_id
                     AND EXISTS (
                       SELECT 1
                         FROM authority_snapshot_criteria AS link
                        WHERE link.project_id = NEW.project_id
                          AND link.task_id = NEW.task_id
                          AND link.authority_snapshot_id =
                                NEW.subject_authority_snapshot_id
                          AND link.criterion_kind = 'verification'
                          AND link.criterion_id =
                                NEW.subject_verification_criterion_id
                     )
                   )
                   OR
                   (
                     NEW.verification_expectation = 'unspecified'
                     AND NEW.subject_authority_snapshot_id IS NULL
                     AND NEW.subject_verification_criterion_id IS NULL
                     AND task.review_target_verification_criterion_id IS NULL
                   )
                 )
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_subject_basis');
        END
        """,
    )
    return (*statements, *immutable_triggers, *subject_triggers)
