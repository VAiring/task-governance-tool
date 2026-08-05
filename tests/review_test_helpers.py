import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


FINGERPRINT = "sha256:" + "c" * 64


@dataclass(frozen=True)
class ReviewProvenanceCase:
    """One explicit test-owned Review provenance acceptance case.

    The values are authority-derived literals.  Keeping them outside the
    production normalizer lets pure, storage, package, and projection tests
    share one oracle without deriving their expectations from the code under
    test.
    """

    name: str
    receipt_kind: str
    reviewer_class: str | None
    model_state: str | None
    declared_model_id: str | None
    skill_state: str | None
    declared_skill_id: str | None
    declared_skill_version: str | None
    review_profiles: tuple[str, ...]
    review_lenses: tuple[str, ...]
    context_relation: str | None
    method_codes: tuple[str, ...]
    provenance_version: int | None

    def normalization_input(self) -> dict[str, object]:
        """Return caller-shaped values without consulting production code."""

        return {
            "receipt_kind": self.receipt_kind,
            "reviewer_class": self.reviewer_class,
            "model_state": self.model_state,
            "declared_model_id": self.declared_model_id,
            "skill_state": self.skill_state,
            "declared_skill_id": self.declared_skill_id,
            "declared_skill_version": self.declared_skill_version,
            "review_profiles": list(self.review_profiles),
            "review_lenses": list(self.review_lenses),
            "context_relation": self.context_relation,
            "method_codes": list(self.method_codes),
        }

    def expected_normalized(self) -> dict[str, object] | None:
        """Return the literal v1 semantic projection or null disposition."""

        if self.provenance_version is None:
            return None
        return {
            "reviewer_class": self.reviewer_class,
            "model_state": self.model_state,
            "declared_model_id": self.declared_model_id,
            "skill_state": self.skill_state,
            "declared_skill_id": self.declared_skill_id,
            "declared_skill_version": self.declared_skill_version,
            "review_profiles": list(self.review_profiles),
            "review_lenses": list(self.review_lenses),
            "context_relation": self.context_relation,
            "method_codes": list(self.method_codes),
        }

    def expected_public_semantics(self) -> dict[str, object] | None:
        """Return v1 public semantic fields, excluding dynamic ID/digest."""

        normalized = self.expected_normalized()
        if normalized is None:
            return None
        return {
            "provenance_version": self.provenance_version,
            **normalized,
            "assurance_class": "bound_attestation",
            "producer_class": "trusted_caller",
            "producer_version": 1,
        }

    def cli_options(self) -> tuple[str, ...]:
        """Return the public receipt options represented by this fixture."""

        if self.provenance_version is None:
            return ()
        options = [
            "--reviewer-class",
            str(self.reviewer_class),
            "--model-state",
            str(self.model_state),
            "--skill-state",
            str(self.skill_state),
            "--context-relation",
            str(self.context_relation),
        ]
        for option, value in (
            ("--declared-model-id", self.declared_model_id),
            ("--declared-skill-id", self.declared_skill_id),
            ("--declared-skill-version", self.declared_skill_version),
        ):
            if value is not None:
                options.extend((option, value))
        for option, values in (
            ("--review-profile", self.review_profiles),
            ("--review-lens", self.review_lenses),
            ("--review-method", self.method_codes),
        ):
            for value in values:
                options.extend((option, value))
        return tuple(options)


# Every v1 case deliberately repeats the same profile, lens, and method.  A
# later integrated test can therefore prove that repetition across distinct
# Receipts and Bundles is valid while the existing duplicate-within-one-input
# tests remain the single rejection oracle.
_COMMON_PROFILES = ("general",)
_COMMON_LENSES = ("correctness",)
_COMMON_METHODS = ("review_packet_inspection",)

REVIEW_PROVENANCE_V1_CASES = (
    ReviewProvenanceCase(
        name="human",
        receipt_kind="independent",
        reviewer_class="human",
        model_state="not_applicable",
        declared_model_id=None,
        skill_state="not_applicable",
        declared_skill_id=None,
        declared_skill_version=None,
        review_profiles=_COMMON_PROFILES,
        review_lenses=_COMMON_LENSES,
        context_relation="external_context",
        method_codes=_COMMON_METHODS,
        provenance_version=1,
    ),
    ReviewProvenanceCase(
        name="deterministic_tool",
        receipt_kind="independent",
        reviewer_class="deterministic_tool",
        model_state="not_applicable",
        declared_model_id=None,
        skill_state="not_applicable",
        declared_skill_id=None,
        declared_skill_version=None,
        review_profiles=_COMMON_PROFILES,
        review_lenses=_COMMON_LENSES,
        context_relation="not_applicable",
        method_codes=_COMMON_METHODS,
        provenance_version=1,
    ),
    ReviewProvenanceCase(
        name="llm_without_skill",
        receipt_kind="independent",
        reviewer_class="llm",
        model_state="declared",
        declared_model_id="openai/gpt-5.6",
        skill_state="not_used",
        declared_skill_id=None,
        declared_skill_version=None,
        review_profiles=_COMMON_PROFILES,
        review_lenses=_COMMON_LENSES,
        context_relation="forked_context",
        method_codes=_COMMON_METHODS,
        provenance_version=1,
    ),
    ReviewProvenanceCase(
        name="llm_declared_model_and_skill",
        receipt_kind="independent",
        reviewer_class="llm",
        model_state="declared",
        declared_model_id="openai/gpt-5.6",
        skill_state="declared",
        declared_skill_id="review/skill-v1",
        declared_skill_version="1.0+local",
        review_profiles=_COMMON_PROFILES,
        review_lenses=_COMMON_LENSES,
        context_relation="fresh_context",
        method_codes=_COMMON_METHODS,
        provenance_version=1,
    ),
    ReviewProvenanceCase(
        name="llm_unknown_model_declared_skill",
        receipt_kind="independent",
        reviewer_class="llm",
        model_state="unknown",
        declared_model_id=None,
        skill_state="declared",
        declared_skill_id="review/skill-v1",
        declared_skill_version="1.0+local",
        review_profiles=_COMMON_PROFILES,
        review_lenses=_COMMON_LENSES,
        context_relation="external_context",
        method_codes=_COMMON_METHODS,
        provenance_version=1,
    ),
    ReviewProvenanceCase(
        name="hybrid",
        receipt_kind="independent",
        reviewer_class="hybrid",
        model_state="declared",
        declared_model_id="model:1",
        skill_state="unknown",
        declared_skill_id=None,
        declared_skill_version=None,
        review_profiles=_COMMON_PROFILES,
        review_lenses=_COMMON_LENSES,
        context_relation="same_context",
        method_codes=_COMMON_METHODS,
        provenance_version=1,
    ),
    ReviewProvenanceCase(
        name="explicit_unknown",
        receipt_kind="independent",
        reviewer_class="unknown",
        model_state="unknown",
        declared_model_id=None,
        skill_state="unknown",
        declared_skill_id=None,
        declared_skill_version=None,
        review_profiles=_COMMON_PROFILES,
        review_lenses=_COMMON_LENSES,
        context_relation="unknown",
        method_codes=_COMMON_METHODS,
        provenance_version=1,
    ),
)

NOT_REQUIRED_REVIEW_PROVENANCE_CASE = ReviewProvenanceCase(
    name="not_required",
    receipt_kind="not_required",
    reviewer_class=None,
    model_state=None,
    declared_model_id=None,
    skill_state=None,
    declared_skill_id=None,
    declared_skill_version=None,
    review_profiles=(),
    review_lenses=(),
    context_relation=None,
    method_codes=(),
    provenance_version=None,
)

REVIEW_PROVENANCE_CASES = (
    *REVIEW_PROVENANCE_V1_CASES,
    NOT_REQUIRED_REVIEW_PROVENANCE_CASE,
)


def _schema_version(connection):
    row = connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def _review_services():
    scripts_root = (
        Path(__file__).resolve().parents[1]
        / "task-governance-tool"
        / "scripts"
    )
    added = str(scripts_root) not in sys.path
    if added:
        sys.path.insert(0, str(scripts_root))
    try:
        from task_governance_tool.reviews import (
            add_review_receipt,
            set_review_target,
        )
        from task_governance_tool.storage import ProjectIdentity
    finally:
        if added:
            sys.path.remove(str(scripts_root))
    return ProjectIdentity, add_review_receipt, set_review_target


def _seed_native_review_evidence(
    connection,
    task_id,
    *,
    target_kind,
    target_value,
    repo_path,
):
    ProjectIdentity, add_review_receipt, set_review_target = _review_services()
    original_row_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT project_id, review_tier FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise AssertionError(f"unknown test task: {task_id}")
        project = ProjectIdentity(
            project_id=str(row["project_id"]),
            canonical_repo=Path(repo_path).resolve(),
            canonical_path_hash="0" * 64,
            display_name="test-project",
        )
        set_review_target(
            connection,
            project,
            task_id,
            kind=target_kind,
            revision=target_value,
        )
        tier = int(row["review_tier"])
        receipts = (
            [("mechanical-review", "not_required", "not_required", "Mechanical test setup")]
            if tier == 0
            else [("test-reviewer-a", "independent", "pass", "")]
        )
        if tier == 2:
            receipts.append(("test-reviewer-b", "independent", "pass", ""))
        for reviewer, kind, verdict, summary in receipts:
            provenance = (
                {}
                if kind == "not_required"
                else {
                    "reviewer_class": "human",
                    "model_state": "not_applicable",
                    "skill_state": "not_applicable",
                    "review_profiles": ["general"],
                    "review_lenses": ["correctness"],
                    "context_relation": "external_context",
                    "review_methods": ["review_packet_inspection"],
                }
            )
            add_review_receipt(
                connection,
                project,
                task_id,
                reviewer=reviewer,
                kind=kind,
                verdict=verdict,
                summary=summary,
                **provenance,
            )
    finally:
        connection.row_factory = original_row_factory


def seed_review_evidence_connection(
    connection,
    task_id,
    *,
    target_kind="diff_fingerprint",
    target_value=FINGERPRINT,
    target_base_revision="",
    repo_path=None,
):
    if _schema_version(connection) >= 18:
        _seed_native_review_evidence(
            connection,
            task_id,
            target_kind=target_kind,
            target_value=target_value,
            repo_path=repo_path or Path.cwd(),
        )
        return
    _seed_legacy_review_evidence(
        connection,
        task_id,
        target_kind=target_kind,
        target_value=target_value,
        target_base_revision=target_base_revision,
    )


def _seed_legacy_review_evidence(
    connection,
    task_id,
    *,
    target_kind,
    target_value,
    target_base_revision,
):
    row = connection.execute(
        "SELECT project_id, review_tier, review_target_generation FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise AssertionError(f"unknown test task: {task_id}")
    generation = int(row[2]) + 1
    connection.execute(
        """
        UPDATE tasks
           SET review_target_kind = ?,
               review_target_value = ?,
               review_target_base_revision = ?,
               review_target_generation = ?
         WHERE task_id = ?
        """,
        (
            target_kind,
            target_value,
            target_base_revision,
            generation,
            task_id,
        ),
    )
    tier = int(row[1])
    if tier == 0:
        receipts = [("mechanical-review", "not_required", "not_required", "Mechanical test setup")]
    else:
        receipts = [("test-reviewer-a", "independent", "pass", "")]
        if tier == 2:
            receipts.append(("test-reviewer-b", "independent", "pass", ""))
    for reviewer, kind, verdict, summary in receipts:
        connection.execute(
            """
            INSERT INTO review_receipts(
              review_receipt_id, task_id, project_id, reviewer_key, receipt_kind,
              verdict, target_kind, target_value, target_base_revision,
              target_generation, summary, user_approved, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                      '2026-07-22T00:00:00Z')
            """,
            (
                "tg_review_receipt_" + uuid.uuid4().hex[:16],
                task_id,
                row[0],
                reviewer,
                kind,
                verdict,
                target_kind,
                target_value,
                target_base_revision,
                generation,
                summary,
            ),
        )


def seed_review_evidence(db, task_id, **target):
    with closing(sqlite3.connect(db)) as connection:
        repo_path = Path(db).resolve().parent / "repo"
        seed_review_evidence_connection(
            connection,
            task_id,
            repo_path=repo_path,
            **target,
        )
        connection.commit()
