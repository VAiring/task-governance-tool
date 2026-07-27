import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import run_taskgov_internal


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    DATABASE_BUSY_MESSAGE,
    StorageError,
    count_tasks,
    connect_initialized_readonly,
    connect_readonly,
    initialize_database,
    resolve_database_target,
    validate_operational_journal_state,
)


SCRIPT_PATH = SCRIPTS_ROOT / "taskgov.py"
UNSUPPORTED_JOURNAL_MODE_MESSAGE = (
    "task database uses unsupported WAL journal mode"
)


def run_taskgov(*args: str) -> subprocess.CompletedProcess[str]:
    return run_taskgov_internal(*args)


def initialized_target(tmp: str):
    root = Path(tmp)
    repo = root / "repo"
    repo.mkdir()
    db = root / "taskgov.sqlite"
    target = resolve_database_target(
        repo=repo,
        db=db,
        script_path=SCRIPT_PATH,
    )
    initialize_database(target)
    return target


def insert_spill_tasks(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    count: int,
) -> None:
    timestamp = "2026-07-26T00:00:00Z"
    rows = [
        (
            f"tg_task_spill_{index:04d}",
            project_id,
            f"Spill task {index}",
            "x" * 8192,
            timestamp,
            timestamp,
        )
        for index in range(count)
    ]
    connection.executemany(
        """
        INSERT INTO tasks(
          task_id,
          project_id,
          title,
          description,
          kind,
          priority,
          status,
          review_tier,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, 'optional', 'normal', 'ready', 1, ?, ?)
        """,
        rows,
    )


class LiveReadConsistencyTests(unittest.TestCase):
    def test_common_read_connection_is_query_only_and_transactional(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)

            with closing(connect_readonly(target.db_path)) as connection:
                self.assertTrue(connection.in_transaction)
                self.assertEqual(
                    int(connection.execute("PRAGMA query_only").fetchone()[0]),
                    1,
                )
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "CREATE TABLE must_not_be_created(value TEXT)"
                    )

            with closing(connect_initialized_readonly(target)) as connection:
                self.assertTrue(connection.in_transaction)
                self.assertEqual(
                    int(connection.execute("PRAGMA query_only").fetchone()[0]),
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT project_id FROM project_meta"
                    ).fetchone()[0],
                    target.project.project_id,
                )

            with closing(sqlite3.connect(target.db_path)) as connection:
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT 1
                          FROM sqlite_master
                         WHERE type = 'table'
                           AND name = 'must_not_be_created'
                        """
                    ).fetchone()
                )
            self.assertFalse(Path(str(target.db_path) + "-wal").exists())
            self.assertFalse(Path(str(target.db_path) + "-shm").exists())

    def test_rollback_journal_sidecar_is_not_rejected_by_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            journal = Path(str(target.db_path) + "-journal")
            journal.write_bytes(b"synthetic non-hot rollback journal")

            validate_operational_journal_state(target.db_path)

            self.assertTrue(journal.exists())
            self.assertEqual(
                target.db_path.read_bytes()[:16],
                b"SQLite format 3\x00",
            )

    def test_wal_and_shm_sidecars_are_each_rejected_before_sqlite_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            database_before = target.db_path.read_bytes()

            for suffix in ("-wal", "-shm"):
                with self.subTest(suffix=suffix):
                    sidecar = Path(str(target.db_path) + suffix)
                    sidecar.write_bytes(b"synthetic persistent sidecar")
                    with mock.patch(
                        "task_governance_tool.storage.sqlite3.connect"
                    ) as sqlite_open:
                        with self.assertRaises(StorageError) as caught:
                            connect_readonly(target.db_path)

                    sqlite_open.assert_not_called()
                    self.assertEqual(
                        (caught.exception.code, caught.exception.message),
                        (
                            "unsupported_journal_mode",
                            UNSUPPORTED_JOURNAL_MODE_MESSAGE,
                        ),
                    )
                    self.assertEqual(target.db_path.read_bytes(), database_before)
                    self.assertEqual(
                        sidecar.read_bytes(),
                        b"synthetic persistent sidecar",
                    )
                    sidecar.unlink()

    def test_orphan_wal_and_shm_sidecars_override_missing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()

            for suffix in ("-wal", "-shm"):
                with self.subTest(suffix=suffix):
                    db = root / f"orphan{suffix[1:]}.sqlite"
                    sidecar = Path(str(db) + suffix)
                    sidecar.write_bytes(b"preserve orphan sidecar")
                    target = resolve_database_target(
                        repo=repo,
                        db=db,
                        script_path=SCRIPT_PATH,
                    )

                    with mock.patch(
                        "task_governance_tool.storage.sqlite3.connect"
                    ) as sqlite_open:
                        with self.assertRaises(StorageError) as caught:
                            connect_initialized_readonly(target)
                    sqlite_open.assert_not_called()
                    self.assertEqual(
                        (caught.exception.code, caught.exception.message),
                        (
                            "unsupported_journal_mode",
                            UNSUPPORTED_JOURNAL_MODE_MESSAGE,
                        ),
                    )

                    output = root / f"viewer{suffix[1:]}.html"
                    for command in (
                        ("task", "list"),
                        ("task", "add", "--title", "must not write"),
                        (
                            "web",
                            "export",
                            "--read-only",
                            "--output",
                            str(output),
                        ),
                    ):
                        with self.subTest(suffix=suffix, command=command):
                            result = run_taskgov(
                                *command,
                                "--repo",
                                str(repo),
                                "--db",
                                str(db),
                                "--json",
                            )
                            self.assertEqual(
                                result.returncode,
                                2,
                                result.stderr,
                            )
                            payload = json.loads(result.stdout)
                            self.assertEqual(
                                payload["errors"][0],
                                {
                                    "code": "unsupported_journal_mode",
                                    "message": (
                                        UNSUPPORTED_JOURNAL_MODE_MESSAGE
                                    ),
                                },
                            )

                    self.assertFalse(db.exists())
                    self.assertFalse(output.exists())
                    self.assertEqual(
                        sidecar.read_bytes(),
                        b"preserve orphan sidecar",
                    )
                    sidecar.unlink()

    def test_cache_spill_never_exposes_uncommitted_or_partial_state(self):
        task_count = 400
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(sqlite3.connect(target.db_path)) as seed:
                insert_spill_tasks(
                    seed,
                    project_id=target.project.project_id,
                    count=task_count,
                )
                seed.commit()

            writer = sqlite3.connect(target.db_path, timeout=0.1)
            try:
                self.assertEqual(
                    writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0],
                    "delete",
                )
                writer.execute("PRAGMA cache_size=1")
                writer.execute("PRAGMA cache_spill=ON")
                writer.execute("BEGIN IMMEDIATE")
                writer.execute(
                    """
                    UPDATE tasks
                       SET status = 'done',
                           completed_at = '2026-07-26T00:00:01Z',
                           updated_at = '2026-07-26T00:00:01Z'
                    """
                )
                journal = Path(str(target.db_path) + "-journal")
                self.assertTrue(journal.exists())
                self.assertGreater(journal.stat().st_size, 512)

                try:
                    with closing(connect_initialized_readonly(target)) as reader:
                        counts = count_tasks(reader, target.project.project_id)
                except StorageError as exc:
                    self.assertEqual(exc.code, "database_busy")
                    self.assertEqual(exc.message, DATABASE_BUSY_MESSAGE)
                else:
                    self.assertEqual(counts["active"], task_count)
                    self.assertEqual(counts["done"], 0)
            finally:
                writer.rollback()
                writer.close()

            with closing(connect_initialized_readonly(target)) as reader:
                after_counts = count_tasks(reader, target.project.project_id)
            self.assertEqual(after_counts["active"], task_count)
            self.assertEqual(after_counts["done"], 0)

    def test_persistent_wal_header_rejects_read_and_write_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = initialized_target(tmp)
            with closing(sqlite3.connect(target.db_path)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0],
                    "wal",
                )
            self.assertFalse(Path(str(target.db_path) + "-wal").exists())
            self.assertFalse(Path(str(target.db_path) + "-shm").exists())
            before = target.db_path.read_bytes()

            for command in (
                ("task", "list"),
                ("task", "add", "--title", "must not write"),
                ("task", "next"),
                ("task", "current"),
                ("task", "show", "tg_task_missing"),
                ("task", "effort", "tg_task_missing"),
                ("handoff", "list"),
                ("handoff", "show", "tg_handoff_missing"),
            ):
                with self.subTest(command=command):
                    result = run_taskgov(
                        *command,
                        "--repo",
                        str(target.project.canonical_repo),
                        "--db",
                        str(target.db_path),
                        "--json",
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(
                        payload["errors"][0],
                        {
                            "code": "unsupported_journal_mode",
                            "message": UNSUPPORTED_JOURNAL_MODE_MESSAGE,
                        },
                    )
                    self.assertEqual(target.db_path.read_bytes(), before)
                    self.assertFalse(
                        Path(str(target.db_path) + "-wal").exists()
                    )
                    self.assertFalse(
                        Path(str(target.db_path) + "-shm").exists()
                    )

    def test_malformed_database_is_not_mislabeled_as_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            db = root / "taskgov.sqlite"
            db.write_bytes(
                b"SQLite format 3\x00"
                + b"\x00\x00"
                + b"\x02\x03"
                + b"not a valid sqlite database"
            )
            target = resolve_database_target(
                repo=repo,
                db=db,
                script_path=SCRIPT_PATH,
            )

            validate_operational_journal_state(db)
            with self.assertRaises(StorageError) as caught:
                connect_initialized_readonly(target)

            self.assertNotEqual(
                caught.exception.code,
                "unsupported_journal_mode",
            )


if __name__ == "__main__":
    unittest.main()
