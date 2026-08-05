from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tests.m214c_test_support import (
    FIXED_CODE,
    FIXED_MESSAGE,
    inject_contract_pointer_fault,
    seed_current_task,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool import tasks as tasks_module
    from task_governance_tool.storage import (
        StorageError,
        capture_or_reuse_current_authority_snapshot_locked,
    )
finally:
    sys.path.pop(0)


class StoredContractSnapshotBoundaryTests(unittest.TestCase):
    @staticmethod
    def connect(db: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(db, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def test_owned_read_uses_one_snapshot_across_concurrent_contract_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            _, db, task = seed_current_task(Path(temp), with_contract=True)
            with closing(self.connect(db)) as setup:
                self.assertEqual(
                    setup.execute("PRAGMA journal_mode=WAL").fetchone()[0],
                    "wal",
                )

            task_selected = threading.Event()
            writer_finished = threading.Event()
            original_fetch = tasks_module.fetch_stored_task_row

            def fetch_then_release_writer(
                connection: sqlite3.Connection,
                query: str,
                parameters: tuple[object, ...] | list[object] = (),
            ) -> sqlite3.Row | None:
                row = original_fetch(connection, query, parameters)
                if "FROM tasks" in query and not task_selected.is_set():
                    task_selected.set()
                    if not writer_finished.wait(5.0):
                        raise AssertionError("concurrent Contract writer did not finish")
                return row

            def publish_next_revision() -> None:
                try:
                    if not task_selected.wait(5.0):
                        raise AssertionError("Task snapshot was not established")
                    with closing(self.connect(db)) as writer:
                        writer.execute("BEGIN IMMEDIATE")
                        writer.execute(
                            """
                            INSERT INTO task_contract_revisions(
                                contract_revision_id,
                                task_id,
                                project_id,
                                revision,
                                scope,
                                acceptance,
                                constraints_text,
                                authority_ref,
                                change_reason,
                                created_at
                            )
                            SELECT
                                'tg_contract_revision_snapshot0002',
                                task_id,
                                project_id,
                                2,
                                scope,
                                acceptance,
                                constraints_text,
                                authority_ref,
                                'concurrent test revision',
                                created_at
                              FROM task_contract_revisions
                             WHERE task_id = ? AND revision = 1
                            """,
                            (task["task_id"],),
                        )
                        writer.execute(
                            "UPDATE tasks SET current_contract_revision = 2 "
                            "WHERE task_id = ?",
                            (task["task_id"],),
                        )
                        capture_or_reuse_current_authority_snapshot_locked(
                            writer,
                            project_id=task["project_id"],
                            task_id=task["task_id"],
                            created_at="2026-08-03T00:00:01Z",
                        )
                        writer.commit()
                finally:
                    writer_finished.set()

            with closing(self.connect(db)) as reader:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    published = pool.submit(publish_next_revision)
                    with patch.object(
                        tasks_module,
                        "fetch_stored_task_row",
                        side_effect=fetch_then_release_writer,
                    ):
                        observed = tasks_module.fetch_validated_current_task_row(
                            reader,
                            project_id=task["project_id"],
                            task_id=task["task_id"],
                        )
                    published.result(timeout=5.0)

                self.assertIsNotNone(observed)
                self.assertEqual(observed["current_contract_revision"], 1)
                self.assertFalse(reader.in_transaction)
                refreshed = tasks_module.fetch_validated_current_task_row(
                    reader,
                    project_id=task["project_id"],
                    task_id=task["task_id"],
                )
                self.assertIsNotNone(refreshed)
                self.assertEqual(refreshed["current_contract_revision"], 2)
                self.assertFalse(reader.in_transaction)

    def test_existing_write_transaction_is_reused_and_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            _, db, task = seed_current_task(Path(temp), with_contract=True)
            pending_title = "outer transaction title"
            with closing(self.connect(db)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE tasks SET title = ? WHERE task_id = ?",
                    (pending_title, task["task_id"]),
                )
                capture_or_reuse_current_authority_snapshot_locked(
                    connection,
                    project_id=task["project_id"],
                    task_id=task["task_id"],
                    created_at="2026-08-03T00:00:01Z",
                )

                observed = tasks_module.fetch_validated_current_task_row(
                    connection,
                    project_id=task["project_id"],
                    task_id=task["task_id"],
                )

                self.assertIsNotNone(observed)
                self.assertEqual(observed["title"], pending_title)
                self.assertTrue(connection.in_transaction)
                self.assertEqual(
                    connection.execute(
                        "SELECT title FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    pending_title,
                )
                connection.rollback()

            with closing(self.connect(db)) as check:
                self.assertEqual(
                    check.execute(
                        "SELECT title FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()[0],
                    task["title"],
                )

    def test_owned_read_failure_releases_its_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            _, db, task = seed_current_task(Path(temp), with_contract=True)
            inject_contract_pointer_fault(db, task["task_id"], pointer=99)

            with closing(self.connect(db)) as connection:
                self.assertFalse(connection.in_transaction)
                with self.assertRaises(StorageError) as caught:
                    tasks_module.fetch_validated_current_task_row(
                        connection,
                        project_id=task["project_id"],
                        task_id=task["task_id"],
                    )
                self.assertEqual(caught.exception.code, FIXED_CODE)
                self.assertEqual(caught.exception.message, FIXED_MESSAGE)
                self.assertFalse(connection.in_transaction)

                connection.execute("BEGIN IMMEDIATE")
                self.assertTrue(connection.in_transaction)
                connection.rollback()


if __name__ == "__main__":
    unittest.main()
