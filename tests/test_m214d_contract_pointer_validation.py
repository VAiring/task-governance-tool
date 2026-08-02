from __future__ import annotations

import copy
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any

from tests.m214c_test_support import (
    FIXED_CODE,
    FIXED_MESSAGE,
    inject_contract_pointer_fault,
    seed_current_task,
    valid_stored_task_row,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.storage import (
        StorageError,
        validate_stored_task_verification,
    )
    from task_governance_tool.tasks import validate_stored_task_rows
finally:
    sys.path.pop(0)


class StoredContractPointerValidationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE task_contract_revisions (project_id, task_id, revision)"
        )

    def tearDown(self):
        self.connection.close()

    def insert_relation(
        self,
        *,
        project_id: Any = None,
        task_id: Any = None,
        revision: Any = 1,
    ) -> None:
        row = valid_stored_task_row()
        self.connection.execute(
            "INSERT INTO task_contract_revisions(project_id, task_id, revision) "
            "VALUES (?, ?, ?)",
            (
                row["project_id"] if project_id is None else project_id,
                row["task_id"] if task_id is None else task_id,
                revision,
            ),
        )

    def assert_unreadable(self, row: dict[str, Any]) -> None:
        with self.assertRaises(StorageError) as caught:
            validate_stored_task_rows(
                [row],
                connection=self.connection,
                source_schema_version=17,
                expected_project_id=valid_stored_task_row()["project_id"],
            )
        self.assertEqual(caught.exception.code, FIXED_CODE)
        self.assertEqual(caught.exception.message, FIXED_MESSAGE)

    def test_pointer_and_raw_relationship_fault_matrix_is_fixed(self):
        row = valid_stored_task_row(current_contract_revision=2)
        self.insert_relation(revision=1)
        self.insert_relation(revision=2)
        validate_stored_task_rows(
            [row],
            connection=self.connection,
            source_schema_version=17,
            expected_project_id=row["project_id"],
        )

        cases = {
            "pointer_real": (1.5, ()),
            "pointer_text": ("1", ()),
            "pointer_blob": (sqlite3.Binary(b"1"), ()),
            "dangling": (1, ()),
            "revision_zero_with_row": (0, ((row["project_id"], 1),)),
            "nonlatest": (
                1,
                ((row["project_id"], 1), (row["project_id"], 2)),
            ),
            "foreign_project": (1, (("foreign-project", 1),)),
            "revision_real": (1, ((row["project_id"], 1.5),)),
            "revision_text": (1, ((row["project_id"], "1"),)),
            "revision_blob": (1, ((row["project_id"], sqlite3.Binary(b"1")),)),
            "project_blob": (1, ((sqlite3.Binary(b"foreign"), 1),)),
            "task_blob": (
                0,
                ((row["project_id"], 1, sqlite3.Binary(row["task_id"].encode())),),
            ),
        }
        for name, (pointer, relationships) in cases.items():
            with self.subTest(name=name):
                self.connection.execute("DELETE FROM task_contract_revisions")
                for relationship in relationships:
                    project_id, revision, *task_id = relationship
                    self.insert_relation(
                        project_id=project_id,
                        revision=revision,
                        task_id=task_id[0] if task_id else None,
                    )
                self.assert_unreadable(
                    valid_stored_task_row(current_contract_revision=pointer)
                )

    def test_relation_read_is_one_selected_batch_and_skips_pre_v8(self):
        project_id = valid_stored_task_row()["project_id"]
        second_task_id = "tg_task_fedcba9876543210"
        self.insert_relation(task_id=second_task_id)
        self.insert_relation(
            project_id=sqlite3.Binary(b"unrelated"),
            task_id="tg_task_unselected",
            revision=sqlite3.Binary(b"bad"),
        )
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)
        validate_stored_task_rows(
            [
                valid_stored_task_row(),
                valid_stored_task_row(
                    task_id=second_task_id,
                    current_contract_revision=1,
                ),
            ],
            connection=self.connection,
            source_schema_version=17,
            expected_project_id=project_id,
        )
        relationship_reads = [
            statement
            for statement in statements
            if "FROM task_contract_revisions" in statement
        ]
        self.assertEqual(len(relationship_reads), 1)
        self.assertNotIn("tg_task_unselected", relationship_reads[0])

        statements.clear()
        legacy = valid_stored_task_row()
        legacy.pop("current_contract_revision")
        legacy.pop("completion_history_coverage")
        validate_stored_task_rows(
            [legacy],
            connection=self.connection,
            source_schema_version=7,
            expected_project_id=project_id,
        )
        self.assertFalse(
            any("task_contract_revisions" in statement for statement in statements)
        )

    def test_valid_relation_preserves_intentional_target_and_lane_states(self):
        row = valid_stored_task_row(
            kind="sequential",
            lane="TG-M21-RELATION",
            lane_order=1,
            review_target_generation=1,
            current_contract_revision=1,
        )
        original = copy.deepcopy(row)
        self.insert_relation()
        validate_stored_task_rows(
            [row],
            connection=self.connection,
            source_schema_version=17,
            expected_project_id=row["project_id"],
        )
        self.assertEqual(row, original)

    def test_recovery_treats_relationship_fault_as_structural(self):
        with tempfile.TemporaryDirectory() as temp:
            _, db, task = seed_current_task(
                Path(temp),
                status="ready",
                with_contract=True,
            )
            inject_contract_pointer_fault(db, task["task_id"], pointer=99)
            with closing(sqlite3.connect(db)) as connection:
                connection.row_factory = sqlite3.Row
                with self.assertRaises(StorageError) as caught:
                    validate_stored_task_verification(
                        connection,
                        17,
                        task["project_id"],
                    )
            self.assertEqual(caught.exception.code, FIXED_CODE)
            self.assertEqual(caught.exception.message, FIXED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
