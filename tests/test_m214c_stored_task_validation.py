import copy
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m214c_test_support import (
    FIXED_CODE,
    FIXED_MESSAGE,
    PRIVATE_SENTINEL,
    inject_task_fault,
    seed_current_task,
    valid_stored_task_row,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
try:
    from task_governance_tool.storage import StorageError
    from task_governance_tool.tasks import validate_stored_task_rows
finally:
    sys.path.pop(0)


class StoredTaskValidationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE task_contract_revisions (
                project_id,
                task_id,
                revision
            )
            """
        )

    def tearDown(self):
        self.connection.close()

    def assert_unreadable(self, rows, **kwargs):
        with self.assertRaises(StorageError) as caught:
            validate_stored_task_rows(
                rows,
                connection=self.connection,
                source_schema_version=17,
                expected_project_id=valid_stored_task_row()["project_id"],
                **kwargs,
            )
        self.assertEqual(caught.exception.code, FIXED_CODE)
        self.assertEqual(caught.exception.message, FIXED_MESSAGE)

    def test_valid_v17_boundaries_and_legacy_capability_preserve_rows(self):
        row = valid_stored_task_row(
            title="x" * 200,
            description="x" * 4000,
            verification="x" * 500,
            tags="x" * 500,
            pause_reason="",
        )
        original = copy.deepcopy(row)
        result = validate_stored_task_rows(
            [row],
            connection=self.connection,
            source_schema_version=17,
            expected_project_id=row["project_id"],
        )
        self.assertIsNone(result.verification_rejection)
        self.assertEqual(row, original)

        legacy = dict(row)
        for field in (
            "review_target_base_revision",
            "current_contract_revision",
            "completion_history_coverage",
        ):
            legacy.pop(field)
        validate_stored_task_rows(
            [legacy],
            connection=self.connection,
            source_schema_version=5,
            expected_project_id=row["project_id"],
        )

        preserved_legacy_hash = valid_stored_task_row(
            completion_evidence_kind="legacy_unverified",
            completion_evidence_revision=" legacy ",
            completion_commit_hash=" legacy ",
        )
        preserved_original = copy.deepcopy(preserved_legacy_hash)
        validate_stored_task_rows(
            [preserved_legacy_hash],
            connection=self.connection,
            source_schema_version=4,
            expected_project_id=row["project_id"],
        )
        self.assertEqual(preserved_legacy_hash, preserved_original)

    def test_valid_v18_stored_verification_accepts_one_thousand(self):
        row = valid_stored_task_row(verification="界" * 1_000)
        original = copy.deepcopy(row)
        result = validate_stored_task_rows(
            [row],
            connection=self.connection,
            source_schema_version=18,
            expected_project_id=row["project_id"],
        )
        self.assertIsNone(result.verification_rejection)
        self.assertEqual(row, original)

    def test_fault_matrix_uses_one_fixed_sanitized_error(self):
        project_id = valid_stored_task_row()["project_id"]
        faults = {
            "private_description": {"description": PRIVATE_SENTINEL},
            "verification_capacity": {"verification": "x" * 501},
            "text_storage": {"title": sqlite3.Binary(b"not-text")},
            "integer_storage_real": {"review_tier": 1.5},
            "integer_storage_text": {"lane_order": "1"},
            "invalid_kind": {"kind": "unknown"},
            "invalid_priority": {"priority": "unknown"},
            "invalid_status": {"status": "unknown"},
            "invalid_review_tier": {"review_tier": 3},
            "noncanonical_lane": {"lane": " lane "},
            "sequential_lane_missing": {
                "kind": "sequential",
                "lane": "",
                "lane_order": None,
            },
            "blocked_reason_missing": {
                "status": "blocked",
                "blocked_reason": "",
            },
            "pause_matrix": {"status": "ready", "pause_reason": "wait"},
            "paused_whitespace_reason": {
                "status": "paused",
                "pause_reason": "   ",
            },
            "done_time_missing": {"status": "done", "completed_at": None},
            "active_time_present": {
                "status": "ready",
                "completed_at": "2026-08-03T00:00:00Z",
            },
            "completion_matrix": {
                "completion_evidence_kind": "external_revision",
                "completion_evidence_revision": "release-1",
                "completion_evidence_reason": "",
                "external_revision_approved": 1,
                "completion_commit_hash": "release-1",
            },
            "private_completion_revision": {
                "completion_evidence_kind": "external_revision",
                "completion_evidence_revision": "stdout: ordinary-private-result",
                "completion_evidence_reason": "Approved external source",
                "external_revision_approved": 1,
                "completion_commit_hash": "stdout: ordinary-private-result",
            },
            "zero_git_completion_revision": {
                "completion_evidence_kind": "git_commit",
                "completion_evidence_revision": "0" * 40,
                "completion_commit_hash": "0" * 40,
            },
            "empty_legacy_completion_revision": {
                "completion_evidence_kind": "legacy_unverified",
            },
            "review_target_matrix": {
                "review_target_kind": "diff_fingerprint",
                "review_target_value": "not-a-fingerprint",
                "review_target_generation": 1,
            },
            "zero_git_review_target": {
                "review_target_kind": "git_commit",
                "review_target_value": "0" * 40,
                "review_target_generation": 1,
            },
            "coverage_enum": {"completion_history_coverage": "unknown"},
            "timestamp": {"updated_at": "not-a-timestamp"},
            "foreign_project": {"project_id": "another-project"},
        }
        for name, changes in faults.items():
            with self.subTest(name=name):
                with self.assertRaises(StorageError) as caught:
                    validate_stored_task_rows(
                        [valid_stored_task_row(**changes)],
                        connection=self.connection,
                        source_schema_version=17,
                        expected_project_id=project_id,
                    )
                self.assertEqual(caught.exception.code, FIXED_CODE)
                self.assertEqual(caught.exception.message, FIXED_MESSAGE)
                self.assertNotIn(PRIVATE_SENTINEL, str(caught.exception))

    def test_recovery_local_result_is_privacy_first_but_structure_is_fatal(self):
        project_id = valid_stored_task_row()["project_id"]
        privacy = valid_stored_task_row(
            task_id="tg_task_privacy",
            verification=PRIVATE_SENTINEL + ("x" * 501),
        )
        capacity = valid_stored_task_row(
            task_id="tg_task_capacity",
            verification="x" * 501,
        )
        result = validate_stored_task_rows(
            [capacity, privacy],
            connection=self.connection,
            source_schema_version=17,
            expected_project_id=project_id,
            verification_rejection_is_local=True,
        )
        self.assertEqual(result.verification_rejection, "privacy")
        self.assertEqual(
            result.verification_rejected_task_ids,
            frozenset({"tg_task_capacity", "tg_task_privacy"}),
        )

        malformed = valid_stored_task_row(
            task_id="tg_task_malformed",
            review_tier=4.5,
        )
        self.assert_unreadable(
            [privacy, malformed],
            verification_rejection_is_local=True,
        )
        self.assert_unreadable(
            [valid_stored_task_row(description=PRIVATE_SENTINEL)],
            verification_rejection_is_local=True,
        )

    def test_sqlite_storage_class_is_checked_before_semantic_coercion(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, db, task = seed_current_task(Path(temp))
            storage = inject_task_fault(
                db,
                task["task_id"],
                assignments={"review_tier": 4.5},
            )
            self.assertEqual(storage, {"review_tier": "real"})
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                with self.assertRaises(StorageError) as caught:
                    validate_stored_task_rows(
                        [row],
                        connection=connection,
                        source_schema_version=17,
                        expected_project_id=task["project_id"],
                    )
                self.assertEqual(caught.exception.code, FIXED_CODE)

    def test_invalid_source_capability_fails_closed(self):
        for version in (True, 0, 19, "18"):
            with self.subTest(version=version):
                with self.assertRaises(StorageError) as caught:
                    validate_stored_task_rows(
                        [valid_stored_task_row()],
                        connection=self.connection,
                        source_schema_version=version,
                        expected_project_id=valid_stored_task_row()["project_id"],
                    )
                self.assertEqual(caught.exception.code, FIXED_CODE)


if __name__ == "__main__":
    unittest.main()
