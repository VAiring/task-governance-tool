"""Ordered Completion Evidence Bundle SQL definitions; storage owns execution."""

from __future__ import annotations


def completion_evidence_bundle_schema_statements() -> tuple[str, ...]:
    """Return the schema-v19 Bundle foundation in migration order."""

    statements = (
        """
        CREATE TABLE criterion_evidence_links (
          criterion_evidence_link_id TEXT PRIMARY KEY CHECK (
            length(criterion_evidence_link_id) = 43
            AND substr(criterion_evidence_link_id, 1, 27) =
                  'tg_criterion_evidence_link_'
            AND substr(criterion_evidence_link_id, 28)
                  NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          criterion_id TEXT NOT NULL,
          evidence_reference_id TEXT NOT NULL,
          relation TEXT NOT NULL CHECK (relation IN (
            'verification_attestation', 'review_assessment',
            'review_finding', 'completion_basis', 'derived_analysis',
            'runner_observation'
          )),
          assurance_class TEXT NOT NULL CHECK (assurance_class IN (
            'machine_observed', 'bound_attestation',
            'deterministically_derived', 'external_reference',
            'legacy_unknown', 'llm_derived'
          )),
          producer_class TEXT NOT NULL CHECK (producer_class IN (
            'taskgov_core', 'taskgov_git', 'trusted_caller',
            'legacy_migration', 'external_system', 'batch_analyzer',
            'verification_runner'
          )),
          producer_version INTEGER NOT NULL CHECK (producer_version > 0),
          created_at TEXT NOT NULL,
          UNIQUE (
            project_id, task_id, criterion_id,
            evidence_reference_id, relation
          ),
          UNIQUE (project_id, task_id, criterion_evidence_link_id),
          FOREIGN KEY (project_id, task_id, criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, evidence_reference_id)
            REFERENCES evidence_references(
              project_id, task_id, evidence_reference_id
            )
        )
        """,
        """
        CREATE TABLE completion_evidence_bundles (
          completion_evidence_bundle_id TEXT PRIMARY KEY CHECK (
            length(completion_evidence_bundle_id) = 46
            AND substr(completion_evidence_bundle_id, 1, 30) =
                  'tg_completion_evidence_bundle_'
            AND substr(completion_evidence_bundle_id, 31)
                  NOT GLOB '*[^0-9a-f]*'
          ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          completion_cycle_id TEXT NOT NULL,
          cycle_ordinal INTEGER NOT NULL CHECK (cycle_ordinal > 0),
          source_schema_version INTEGER NOT NULL
            CHECK (source_schema_version = 19),
          bundle_version INTEGER NOT NULL CHECK (bundle_version = 1),
          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          authority_snapshot_id TEXT NOT NULL,
          acceptance_criterion_id TEXT,
          verification_criterion_id TEXT,
          target_kind TEXT NOT NULL CHECK (target_kind IN (
            'git_commit', 'diff_fingerprint', 'external_revision',
            'git_snapshot'
          )),
          target_value TEXT NOT NULL CHECK (length(target_value) BETWEEN 1 AND 500),
          target_base_revision TEXT NOT NULL
            CHECK (length(target_base_revision) <= 500),
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          target_capture_version INTEGER NOT NULL
            CHECK (target_capture_version = 1),
          artifact_manifest_id TEXT NOT NULL,
          verification_receipt_id TEXT,
          omission_mask INTEGER NOT NULL CHECK (omission_mask BETWEEN 0 AND 15),
          sealed_at TEXT NOT NULL,
          bundle_digest TEXT NOT NULL CHECK (
            length(bundle_digest) = 71
            AND substr(bundle_digest, 1, 7) = 'sha256:'
            AND substr(bundle_digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          payload_size_bytes INTEGER NOT NULL CHECK (
            payload_size_bytes BETWEEN 1 AND 16777216
          ),
          UNIQUE (project_id, task_id, completion_evidence_bundle_id),
          UNIQUE (project_id, task_id, completion_cycle_id),
          FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (project_id, task_id, authority_snapshot_id)
            REFERENCES authority_snapshots(
              project_id, task_id, authority_snapshot_id
            ),
          FOREIGN KEY (project_id, task_id, acceptance_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, verification_criterion_id)
            REFERENCES contract_criteria(project_id, task_id, criterion_id),
          FOREIGN KEY (project_id, task_id, artifact_manifest_id)
            REFERENCES artifact_manifests(
              project_id, task_id, artifact_manifest_id
            ),
          FOREIGN KEY (verification_receipt_id)
            REFERENCES verification_receipts(verification_receipt_id),
          FOREIGN KEY (completion_cycle_id)
            REFERENCES task_completion_cycles(completion_cycle_id)
            DEFERRABLE INITIALLY DEFERRED
        )
        """,
        """
        CREATE TABLE completion_bundle_members (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          completion_evidence_bundle_id TEXT NOT NULL,
          member_kind TEXT NOT NULL CHECK (
            member_kind IN ('criterion_link', 'evidence_reference')
          ),
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          criterion_evidence_link_id TEXT,
          evidence_reference_id TEXT,
          PRIMARY KEY (completion_evidence_bundle_id, member_kind, ordinal),
          UNIQUE (completion_evidence_bundle_id, criterion_evidence_link_id),
          UNIQUE (completion_evidence_bundle_id, evidence_reference_id),
          CHECK (
            (member_kind = 'criterion_link'
              AND criterion_evidence_link_id IS NOT NULL
              AND evidence_reference_id IS NULL)
            OR
            (member_kind = 'evidence_reference'
              AND criterion_evidence_link_id IS NULL
              AND evidence_reference_id IS NOT NULL)
          ),
          FOREIGN KEY (project_id, task_id, completion_evidence_bundle_id)
            REFERENCES completion_evidence_bundles(
              project_id, task_id, completion_evidence_bundle_id
            ),
          FOREIGN KEY (project_id, task_id, criterion_evidence_link_id)
            REFERENCES criterion_evidence_links(
              project_id, task_id, criterion_evidence_link_id
            ),
          FOREIGN KEY (project_id, task_id, evidence_reference_id)
            REFERENCES evidence_references(
              project_id, task_id, evidence_reference_id
            )
        )
        """,
        """
        CREATE TABLE completion_bundle_finding_snapshots (
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          completion_evidence_bundle_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          review_finding_id TEXT NOT NULL,
          review_receipt_id TEXT NOT NULL,
          target_generation INTEGER NOT NULL CHECK (target_generation > 0),
          severity TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
          summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 1000),
          status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
          resolution_summary TEXT NOT NULL CHECK (length(resolution_summary) <= 1000),
          created_at TEXT NOT NULL,
          resolved_at TEXT,
          evidence_reference_id TEXT,
          assurance_class TEXT NOT NULL CHECK (
            assurance_class IN ('bound_attestation', 'legacy_unknown')
          ),
          producer_class TEXT NOT NULL CHECK (
            producer_class IN ('trusted_caller', 'legacy_migration')
          ),
          producer_version INTEGER NOT NULL CHECK (producer_version = 1),
          digest TEXT NOT NULL CHECK (
            length(digest) = 71
            AND substr(digest, 1, 7) = 'sha256:'
            AND substr(digest, 8) NOT GLOB '*[^0-9a-f]*'
          ),
          PRIMARY KEY (completion_evidence_bundle_id, ordinal),
          UNIQUE (completion_evidence_bundle_id, review_finding_id),
          CHECK (
            (status = 'open'
              AND resolution_summary = '' AND resolved_at IS NULL)
            OR
            (status = 'resolved'
              AND resolution_summary != '' AND resolved_at IS NOT NULL)
          ),
          CHECK (
            (evidence_reference_id IS NOT NULL
              AND assurance_class = 'bound_attestation'
              AND producer_class = 'trusted_caller')
            OR
            (evidence_reference_id IS NULL
              AND assurance_class = 'legacy_unknown'
              AND producer_class = 'legacy_migration')
          ),
          FOREIGN KEY (project_id, task_id, completion_evidence_bundle_id)
            REFERENCES completion_evidence_bundles(
              project_id, task_id, completion_evidence_bundle_id
            ),
          FOREIGN KEY (review_finding_id)
            REFERENCES review_findings(review_finding_id),
          FOREIGN KEY (review_receipt_id)
            REFERENCES review_receipts(review_receipt_id),
          FOREIGN KEY (project_id, task_id, evidence_reference_id)
            REFERENCES evidence_references(
              project_id, task_id, evidence_reference_id
            )
        )
        """,
        """
        CREATE TABLE evidence_projection_state (
          project_id TEXT PRIMARY KEY,
          source_generation INTEGER NOT NULL CHECK (source_generation >= 0),
          published_generation INTEGER CHECK (
            published_generation IS NULL
            OR (published_generation >= 0
              AND published_generation <= source_generation)
          ),
          index_digest TEXT CHECK (
            index_digest IS NULL
            OR (
              length(index_digest) = 71
              AND substr(index_digest, 1, 7) = 'sha256:'
              AND substr(index_digest, 8) NOT GLOB '*[^0-9a-f]*'
            )
          ),
          last_success_at TEXT,
          last_outcome_code TEXT CHECK (
            last_outcome_code IS NULL
            OR last_outcome_code IN ('succeeded', 'deferred', 'failed')
          ),
          last_outcome_at TEXT,
          CHECK (
            (published_generation IS NULL AND index_digest IS NULL)
            OR
            (published_generation IS NOT NULL AND index_digest IS NOT NULL)
          ),
          CHECK (
            (last_outcome_code IS NULL AND last_outcome_at IS NULL)
            OR
            (last_outcome_code IS NOT NULL AND last_outcome_at IS NOT NULL)
          ),
          FOREIGN KEY (project_id) REFERENCES project_meta(project_id)
        )
        """,
        """ALTER TABLE task_completion_cycles
             ADD COLUMN evidence_basis_version INTEGER NOT NULL DEFAULT 0
             CHECK (evidence_basis_version IN (0, 1))""",
        """ALTER TABLE task_completion_cycles
             ADD COLUMN completion_evidence_bundle_id TEXT
             REFERENCES completion_evidence_bundles(completion_evidence_bundle_id)
             DEFERRABLE INITIALLY DEFERRED""",
        """CREATE INDEX idx_criterion_evidence_links_reference
             ON criterion_evidence_links(project_id, task_id, evidence_reference_id)""",
        """CREATE UNIQUE INDEX idx_completion_evidence_bundles_task_cycle
             ON completion_evidence_bundles(project_id, task_id, completion_cycle_id)""",
        """CREATE INDEX idx_completion_bundle_members_reference
             ON completion_bundle_members(completion_evidence_bundle_id, member_kind, ordinal)""",
        """CREATE INDEX idx_completion_bundle_finding_snapshots_order
             ON completion_bundle_finding_snapshots(
               completion_evidence_bundle_id, target_generation, created_at,
               review_finding_id
             )""",
    )
    immutable_tables = (
        "criterion_evidence_links",
        "completion_evidence_bundles",
        "completion_bundle_members",
        "completion_bundle_finding_snapshots",
    )
    immutable_triggers = tuple(
        statement
        for table_name in immutable_tables
        for statement in (
            f"""CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_completion_evidence'); END""",
            f"""CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name}
                 BEGIN SELECT RAISE(ABORT, 'immutable_completion_evidence'); END""",
        )
    )
    guard_triggers = (
        """
        CREATE TRIGGER trg_criterion_evidence_links_matrix_insert
        BEFORE INSERT ON criterion_evidence_links
        WHEN NOT EXISTS (
          SELECT 1
            FROM contract_criteria AS criterion
            JOIN evidence_references AS reference
              ON reference.project_id = criterion.project_id
             AND reference.task_id = criterion.task_id
           WHERE criterion.project_id = NEW.project_id
             AND criterion.task_id = NEW.task_id
             AND criterion.criterion_id = NEW.criterion_id
             AND reference.evidence_reference_id = NEW.evidence_reference_id
             AND reference.assurance_class = NEW.assurance_class
             AND reference.producer_class = NEW.producer_class
             AND reference.producer_version = NEW.producer_version
             AND (
               (NEW.relation = 'verification_attestation'
                 AND criterion.criterion_kind = 'verification'
                 AND reference.source_kind = 'verification_receipt')
               OR
               (NEW.relation = 'review_assessment'
                 AND criterion.criterion_kind = 'acceptance'
                 AND reference.source_kind = 'review_receipt')
               OR
               (NEW.relation = 'review_finding'
                 AND criterion.criterion_kind = 'acceptance'
                 AND reference.source_kind = 'review_finding')
               OR
               (NEW.relation = 'completion_basis'
                 AND criterion.criterion_kind = 'acceptance'
                 AND reference.source_kind IN (
                   'artifact_manifest', 'completion_evidence'
                 ))
             )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_criterion_evidence_link');
        END
        """,
        """
        CREATE TRIGGER trg_completion_bundle_members_matrix_insert
        BEFORE INSERT ON completion_bundle_members
        WHEN NOT (
          (
            NEW.member_kind = 'criterion_link'
            AND EXISTS (
              SELECT 1 FROM criterion_evidence_links AS link
               WHERE link.project_id = NEW.project_id
                 AND link.task_id = NEW.task_id
                 AND link.criterion_evidence_link_id =
                       NEW.criterion_evidence_link_id
            )
          )
          OR
          (
            NEW.member_kind = 'evidence_reference'
            AND EXISTS (
              SELECT 1 FROM evidence_references AS reference
               WHERE reference.project_id = NEW.project_id
                 AND reference.task_id = NEW.task_id
                 AND reference.evidence_reference_id =
                       NEW.evidence_reference_id
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_bundle_member');
        END
        """,
        """
        CREATE TRIGGER trg_completion_bundle_finding_snapshots_matrix_insert
        BEFORE INSERT ON completion_bundle_finding_snapshots
        WHEN NOT EXISTS (
          SELECT 1
            FROM review_findings AS finding
            JOIN review_receipts AS receipt
              ON receipt.review_receipt_id = finding.review_receipt_id
           WHERE finding.review_finding_id = NEW.review_finding_id
             AND finding.review_receipt_id = NEW.review_receipt_id
             AND receipt.project_id = NEW.project_id
             AND receipt.task_id = NEW.task_id
             AND receipt.target_generation = NEW.target_generation
             AND finding.severity = NEW.severity
             AND finding.summary = NEW.summary
             AND finding.status = NEW.status
             AND finding.resolution_summary = NEW.resolution_summary
             AND finding.created_at = NEW.created_at
             AND finding.resolved_at IS NEW.resolved_at
             AND (
               (
                 NEW.evidence_reference_id IS NULL
                 AND NEW.assurance_class = 'legacy_unknown'
                 AND NEW.producer_class = 'legacy_migration'
                 AND NEW.producer_version = 1
               )
               OR
               (
                 NEW.evidence_reference_id IS NOT NULL
                 AND NEW.assurance_class = 'bound_attestation'
                 AND NEW.producer_class = 'trusted_caller'
                 AND NEW.producer_version = 1
                 AND EXISTS (
                   SELECT 1 FROM evidence_references AS reference
                    WHERE reference.project_id = NEW.project_id
                      AND reference.task_id = NEW.task_id
                      AND reference.evidence_reference_id =
                            NEW.evidence_reference_id
                      AND reference.source_kind = 'review_finding'
                      AND reference.source_id = NEW.review_finding_id
                 )
               )
             )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_finding_snapshot');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_evidence_basis_insert
        BEFORE INSERT ON task_completion_cycles
        WHEN NOT (
          (
            NEW.origin = 'legacy_current_done'
            AND NEW.evidence_basis_version = 0
            AND NEW.completion_evidence_bundle_id IS NULL
          )
          OR
          (
            NEW.origin = 'native_done'
            AND NEW.evidence_basis_version = 1
            AND NEW.completion_evidence_bundle_id IS NOT NULL
            AND EXISTS (
              SELECT 1 FROM completion_evidence_bundles AS bundle
               WHERE bundle.project_id = NEW.project_id
                 AND bundle.task_id = NEW.task_id
                 AND bundle.completion_cycle_id = NEW.completion_cycle_id
                 AND bundle.cycle_ordinal = NEW.saved_cycle_ordinal
                 AND bundle.completion_evidence_bundle_id =
                       NEW.completion_evidence_bundle_id
            )
          )
        )
        BEGIN
          SELECT RAISE(ABORT, 'invalid_completion_evidence_basis');
        END
        """,
    )
    return (*statements, *immutable_triggers, *guard_triggers)
