"""Completion-cycle schema definitions; storage owns their execution."""

from __future__ import annotations


def completion_cycle_history_schema_statements() -> tuple[str, ...]:
    """Return the exact schema-v15 objects in their migration order."""

    return (
        """
        ALTER TABLE tasks
          ADD COLUMN completion_history_coverage TEXT NOT NULL
            DEFAULT 'legacy_unknown'
            CHECK (completion_history_coverage IN ('legacy_unknown', 'complete'))
        """,
        """
        CREATE UNIQUE INDEX idx_tasks_project_task_identity
          ON tasks(project_id, task_id)
        """,
        """
        CREATE UNIQUE INDEX idx_review_receipts_completion_cycle_reference
          ON review_receipts(
            project_id, task_id, target_kind, target_value,
            target_base_revision, target_generation, review_receipt_id
          )
        """,
        """
        CREATE TABLE task_completion_cycles (
          completion_cycle_id TEXT PRIMARY KEY
            CHECK (
              length(completion_cycle_id) = 36
              AND substr(completion_cycle_id, 1, 20) = 'tg_completion_cycle_'
              AND substr(completion_cycle_id, 21) NOT GLOB '*[^0-9a-f]*'
            ),
          project_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          saved_cycle_ordinal INTEGER NOT NULL
            CHECK (saved_cycle_ordinal >= 1),

          origin TEXT NOT NULL
            CHECK (origin IN ('native_done', 'legacy_current_done')),
          completeness TEXT NOT NULL
            CHECK (completeness IN ('complete', 'partial')),
          completed_at TEXT,
          recorded_at TEXT NOT NULL,

          contract_revision INTEGER NOT NULL CHECK (contract_revision >= 0),
          review_tier INTEGER NOT NULL CHECK (review_tier IN (0, 1, 2)),
          verification_expectation TEXT NOT NULL
            CHECK (verification_expectation IN ('specified', 'unspecified')),
          verification_attestation INTEGER
            CHECK (
              verification_attestation IS NULL
              OR verification_attestation IN (0, 1)
            ),

          completion_evidence_kind TEXT NOT NULL
            CHECK (completion_evidence_kind IN (
              'none', 'git_commit', 'external_revision',
              'commit_not_required', 'legacy_unverified'
            )),
          completion_evidence_revision TEXT NOT NULL
            CHECK (length(completion_evidence_revision) <= 500),
          completion_evidence_reason TEXT NOT NULL
            CHECK (length(completion_evidence_reason) <= 1000),
          external_revision_approved INTEGER NOT NULL
            CHECK (external_revision_approved IN (0, 1)),
          completion_commit_required INTEGER NOT NULL
            CHECK (completion_commit_required IN (0, 1)),
          completion_commit_hash TEXT NOT NULL
            CHECK (length(completion_commit_hash) <= 500),

          review_target_kind TEXT NOT NULL
            CHECK (review_target_kind IN (
              '', 'git_commit', 'diff_fingerprint',
              'external_revision', 'git_snapshot'
            )),
          review_target_value TEXT NOT NULL
            CHECK (length(review_target_value) <= 500),
          review_target_base_revision TEXT NOT NULL
            CHECK (length(review_target_base_revision) <= 500),
          review_target_generation INTEGER NOT NULL
            CHECK (review_target_generation >= 0),

          gate_basis_version INTEGER NOT NULL
            CHECK (gate_basis_version IN (0, 1)),
          review_basis_kind TEXT NOT NULL
            CHECK (review_basis_kind IN (
              'unknown', 'independent_passes',
              'self_review_fallback', 'not_required'
            )),
          required_independent_passes INTEGER
            CHECK (
              required_independent_passes IS NULL
              OR required_independent_passes BETWEEN 0 AND 2
            ),
          qualifying_independent_passes INTEGER
            CHECK (
              qualifying_independent_passes IS NULL
              OR qualifying_independent_passes >= 0
            ),
          changes_requested_count INTEGER
            CHECK (changes_requested_count IS NULL OR changes_requested_count >= 0),
          open_high_count INTEGER
            CHECK (open_high_count IS NULL OR open_high_count >= 0),
          open_medium_count INTEGER
            CHECK (open_medium_count IS NULL OR open_medium_count >= 0),
          fresh_review_required_count INTEGER
            CHECK (
              fresh_review_required_count IS NULL
              OR fresh_review_required_count >= 0
            ),
          qualifying_receipt_id_1 TEXT,
          qualifying_receipt_id_2 TEXT,

          FOREIGN KEY (project_id, task_id)
            REFERENCES tasks(project_id, task_id),
          FOREIGN KEY (
            project_id, task_id, review_target_kind, review_target_value,
            review_target_base_revision, review_target_generation,
            qualifying_receipt_id_1
          ) REFERENCES review_receipts(
            project_id, task_id, target_kind, target_value,
            target_base_revision, target_generation, review_receipt_id
          ),
          FOREIGN KEY (
            project_id, task_id, review_target_kind, review_target_value,
            review_target_base_revision, review_target_generation,
            qualifying_receipt_id_2
          ) REFERENCES review_receipts(
            project_id, task_id, target_kind, target_value,
            target_base_revision, target_generation, review_receipt_id
          ),

          CHECK (
            (review_target_kind = ''
              AND review_target_value = ''
              AND review_target_base_revision = ''
              AND review_target_generation = 0)
            OR
            (review_target_kind = 'git_snapshot'
              AND review_target_value != ''
              AND review_target_base_revision != ''
              AND review_target_generation > 0)
            OR
            (review_target_kind IN (
                'git_commit', 'diff_fingerprint', 'external_revision'
              )
              AND review_target_value != ''
              AND review_target_base_revision = ''
              AND review_target_generation > 0)
          ),
          CHECK (
            (completion_evidence_kind = 'none'
              AND completeness = 'partial'
              AND completion_evidence_revision = ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_required = 1
              AND completion_commit_hash = '')
            OR
            (completion_evidence_kind = 'git_commit'
              AND completion_evidence_revision != ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_required = 1
              AND completion_commit_hash = completion_evidence_revision)
            OR
            (completion_evidence_kind = 'external_revision'
              AND completion_evidence_revision != ''
              AND completion_evidence_reason != ''
              AND external_revision_approved = 1
              AND completion_commit_required = 1
              AND completion_commit_hash = completion_evidence_revision)
            OR
            (completion_evidence_kind = 'commit_not_required'
              AND completion_evidence_revision = ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_required = 0
              AND completion_commit_hash = '')
            OR
            (completion_evidence_kind = 'legacy_unverified'
              AND completeness = 'partial'
              AND completion_evidence_revision != ''
              AND completion_evidence_reason = ''
              AND external_revision_approved = 0
              AND completion_commit_hash = completion_evidence_revision)
          ),
          CHECK (
            (origin = 'native_done'
              AND completeness = 'complete'
              AND completed_at IS NOT NULL
              AND verification_attestation = 1
              AND review_target_kind != ''
              AND gate_basis_version = 1)
            OR
            (origin = 'legacy_current_done'
              AND completeness = 'partial'
              AND verification_attestation IS NULL
              AND gate_basis_version = 0)
          ),
          CHECK (
            (gate_basis_version = 0
              AND review_basis_kind = 'unknown'
              AND required_independent_passes IS NULL
              AND qualifying_independent_passes IS NULL
              AND changes_requested_count IS NULL
              AND open_high_count IS NULL
              AND open_medium_count IS NULL
              AND fresh_review_required_count IS NULL
              AND qualifying_receipt_id_1 IS NULL
              AND qualifying_receipt_id_2 IS NULL)
            OR
            (gate_basis_version = 1
              AND required_independent_passes =
                CASE review_tier WHEN 0 THEN 0 WHEN 1 THEN 1 ELSE 2 END
              AND qualifying_independent_passes IS NOT NULL
              AND changes_requested_count = 0
              AND open_high_count = 0
              AND open_medium_count = 0
              AND fresh_review_required_count = 0
              AND (
                (review_basis_kind = 'independent_passes'
                  AND review_tier IN (1, 2)
                  AND qualifying_independent_passes >= required_independent_passes
                  AND qualifying_receipt_id_1 IS NOT NULL
                  AND (
                    (review_tier = 1 AND qualifying_receipt_id_2 IS NULL)
                    OR
                    (review_tier = 2 AND qualifying_receipt_id_2 IS NOT NULL)
                  ))
                OR
                (review_basis_kind = 'self_review_fallback'
                  AND review_tier IN (1, 2)
                  AND qualifying_independent_passes < required_independent_passes
                  AND qualifying_receipt_id_1 IS NOT NULL
                  AND qualifying_receipt_id_2 IS NULL)
                OR
                (review_basis_kind = 'not_required'
                  AND review_tier = 0
                  AND qualifying_receipt_id_1 IS NOT NULL
                  AND qualifying_receipt_id_2 IS NULL)
              ))
          )
        )
        """,
        """
        CREATE UNIQUE INDEX idx_task_completion_cycles_task_ordinal
          ON task_completion_cycles(project_id, task_id, saved_cycle_ordinal)
        """,
        """
        ALTER TABLE task_events
          ADD COLUMN completion_cycle_id TEXT
            REFERENCES task_completion_cycles(completion_cycle_id)
        """,
        """
        CREATE INDEX idx_task_events_completion_cycle
          ON task_events(completion_cycle_id)
          WHERE completion_cycle_id IS NOT NULL
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_no_update
        BEFORE UPDATE ON task_completion_cycles
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_cycle');
        END
        """,
        """
        CREATE TRIGGER trg_task_completion_cycles_no_delete
        BEFORE DELETE ON task_completion_cycles
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_cycle');
        END
        """,
        """
        CREATE TRIGGER trg_tasks_completion_history_coverage_immutable
        BEFORE UPDATE OF completion_history_coverage ON tasks
        WHEN NEW.completion_history_coverage IS NOT OLD.completion_history_coverage
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_history_coverage');
        END
        """,
        """
        CREATE TRIGGER trg_task_events_completion_cycle_link_immutable
        BEFORE UPDATE OF completion_cycle_id ON task_events
        WHEN NEW.completion_cycle_id IS NOT OLD.completion_cycle_id
        BEGIN
          SELECT RAISE(ABORT, 'immutable_completion_cycle_link');
        END
        """,
    )
