from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    SOURCE_SKILL_ROOT,
    json_payload,
    run_taskgov_internal,
)

from task_governance_tool import backup as backup_service
from task_governance_tool import cli as cli_service
from task_governance_tool import maintenance as maintenance_service
from task_governance_tool.backup import discover_managed_backup_metadata
from task_governance_tool.storage import (
    configure_project_maintenance,
    connect,
    initialize_database,
    read_managed_backup_repository,
    resolve_database_target,
)


SCRIPT_PATH = SOURCE_SKILL_ROOT / "scripts" / "taskgov.py"
MIGRATION_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "task-status-migration-v2"
    / "tasks.json"
)
BASE_TIME = datetime(2026, 7, 27, tzinfo=UTC)
WRITE_OFFSETS = (0, 1, 5, 29, 30, 31, 59, 60)
EXPECTED_BACKUP_OFFSETS = (0, 30, 60)


def timestamp(minute: int) -> str:
    return (BASE_TIME + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ascii_payload(label: str, size: int) -> str:
    encoded = label.encode("ascii")
    if len(encoded) > size:
        raise AssertionError("performance fixture label exceeds its fixed byte size")
    return label + ("x" * (size - len(encoded)))


def small_task_specs() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    source = json.loads(MIGRATION_FIXTURE.read_text(encoding="utf-8"))
    return source["tasks"], source["tool_events"]


def large_task_specs() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    tasks = [
        {
            "task_id": f"tg_task_performance_{index:04d}",
            "title": f"Performance task {index:04d}",
            "status": "in_progress" if index == 0 else "ready",
            "lane_order": None,
            "event_count": 10,
            "completion_commit_hash": "",
        }
        for index in range(500)
    ]
    return tasks, []


def seed_fixture(
    root: Path,
    *,
    task_specs: list[dict[str, object]],
    tool_event_specs: list[dict[str, object]],
) -> tuple[Path, Path, str, int]:
    repo = root / "project"
    repo.mkdir(parents=True)
    db_path = root / "seed" / "taskgov.sqlite"
    target = resolve_database_target(
        repo=repo,
        db=db_path,
        script_path=SCRIPT_PATH,
    )
    initialize_database(target)
    configure_project_maintenance(
        target,
        requested_interval_minutes=30,
        requested_generations=3,
        enabled_at=timestamp(0),
    )

    task_rows = []
    event_rows = []
    global_event_index = 0
    for task_index, item in enumerate(task_specs):
        task_id = str(item["task_id"])
        status = str(item["status"])
        completion_hash = str(item["completion_commit_hash"])
        completed_at = timestamp(0) if status == "done" else None
        task_rows.append(
            (
                task_id,
                target.project.project_id,
                ascii_payload(f"{item['title']} [{task_index:04d}] ", 80),
                ascii_payload(f"Performance description {task_index:04d}: ", 512),
                "sequential" if len(task_specs) == 12 else "optional",
                "MIGRATION" if len(task_specs) == 12 else "",
                item["lane_order"],
                status,
                timestamp(0),
                timestamp(0),
                completed_at,
                completion_hash,
                "legacy_unverified" if completion_hash else "none",
                completion_hash,
            )
        )
        for _ in range(int(item["event_count"])):
            global_event_index += 1
            event_rows.append(
                (
                    f"tg_event_performance_{global_event_index:05d}",
                    task_id,
                    target.project.project_id,
                    "note_added",
                    ascii_payload(
                        f"Synthetic sanitized event {global_event_index:05d}: ",
                        256,
                    ),
                    timestamp(0),
                )
            )

    with closing(connect(db_path)) as connection:
        connection.executemany(
            """
            INSERT INTO tasks(
              task_id, project_id, title, description, kind, lane, lane_order,
              priority, status, review_tier, verification, tags, created_at,
              updated_at, completed_at, completion_commit_required,
              completion_commit_hash, completion_evidence_kind,
              completion_evidence_revision
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, 'normal', ?, 0, 'offline synthetic gate',
              'performance,synthetic', ?, ?, ?, 1, ?, ?, ?
            )
            """,
            task_rows,
        )
        connection.executemany(
            """
            INSERT INTO task_events(
              task_event_id, task_id, project_id, event_type, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            event_rows,
        )
        connection.executemany(
            """
            INSERT INTO tool_events(
              tool_event_id, project_id, command, status, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(item["tool_event_id"]),
                    target.project.project_id,
                    str(item["command"]),
                    str(item["status"]),
                    ascii_payload(f"{item['summary']}: ", 256),
                    timestamp(0),
                )
                for item in tool_event_specs
            ],
        )
        connection.commit()

    target_task_id = next(
        str(item["task_id"])
        for item in task_specs
        if item["status"] == "in_progress"
    )
    return repo, db_path, target_task_id, len(event_rows)


def copied_target(repo: Path, source_db: Path, destination: Path):
    destination.parent.mkdir(parents=True)
    shutil.copy2(source_db, destination)
    return resolve_database_target(
        repo=repo,
        db=destination,
        script_path=SCRIPT_PATH,
    )


def assert_fixed_fixture_bytes(
    testcase: unittest.TestCase,
    db_path: Path,
    *,
    task_count: int,
    event_count: int,
) -> None:
    with closing(connect(db_path)) as connection:
        counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM tasks), "
            "(SELECT COUNT(*) FROM task_events)"
        ).fetchone()
        task_bytes = connection.execute(
            """
            SELECT
              MIN(length(CAST(title AS BLOB))),
              MAX(length(CAST(title AS BLOB))),
              MIN(length(CAST(description AS BLOB))),
              MAX(length(CAST(description AS BLOB)))
              FROM tasks
            """
        ).fetchone()
        event_bytes = connection.execute(
            """
            SELECT
              MIN(length(CAST(summary AS BLOB))),
              MAX(length(CAST(summary AS BLOB)))
              FROM task_events
            """
        ).fetchone()
    testcase.assertEqual(tuple(counts), (task_count, event_count))
    testcase.assertEqual(tuple(task_bytes), (80, 80, 512, 512))
    testcase.assertEqual(tuple(event_bytes), (256, 256))


def run_write_sequence(
    testcase: unittest.TestCase,
    target,
    task_id: str,
    *,
    maintenance_enabled: bool,
) -> list[float]:
    timings = []
    for payload_index, minute in enumerate(WRITE_OFFSETS):
        note = ascii_payload(f"payload-{payload_index:02d}: ", 244)
        testcase.assertEqual(
            len(("Note added: " + note).encode("utf-8")),
            256,
        )
        started = time.perf_counter()
        with mock.patch.object(
            maintenance_service,
            "utc_now",
            return_value=timestamp(minute),
        ):
            result = run_taskgov_internal(
                "--repo",
                str(target.project.canonical_repo),
                "--db",
                str(target.db_path),
                "task",
                "edit",
                task_id,
                "--add-note",
                note,
                "--json",
                maintenance_enabled=maintenance_enabled,
            )
        elapsed = time.perf_counter() - started
        testcase.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json_payload(result)
        testcase.assertEqual(payload["warnings"], [])
        testcase.assertEqual(
            len(payload["data"]["event"]["summary"].encode("utf-8")),
            256,
        )
        testcase.assertLess(elapsed, 5.0)
        timings.append(elapsed)
    return timings


class BackupPerformanceTests(unittest.TestCase):
    def test_small_and_large_backup_only_performance_contract(self):
        scenarios = (
            ("small-migration", small_task_specs(), 12, 191),
            ("large", large_task_specs(), 500, 5000),
        )
        for name, specs, task_count, event_count in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo, seed_db, task_id, seeded_event_count = seed_fixture(
                    root,
                    task_specs=specs[0],
                    tool_event_specs=specs[1],
                )
                self.assertEqual(seeded_event_count, event_count)
                assert_fixed_fixture_bytes(
                    self,
                    seed_db,
                    task_count=task_count,
                    event_count=event_count,
                )
                disabled = copied_target(
                    repo,
                    seed_db,
                    root / "disabled" / "taskgov.sqlite",
                )
                enabled = copied_target(
                    repo,
                    seed_db,
                    root / "enabled" / "taskgov.sqlite",
                )

                with mock.patch.object(
                    maintenance_service,
                    "run_routine_backup",
                    side_effect=AssertionError(
                        "disabled coordinator performed backup work"
                    ),
                ):
                    disabled_timings = run_write_sequence(
                        self,
                        disabled,
                        task_id,
                        maintenance_enabled=False,
                    )

                routine_results = []
                published_at = []
                real_routine = maintenance_service.run_routine_backup
                real_copy = backup_service._copy

                def counted_routine(*args, **kwargs):
                    result = real_routine(*args, **kwargs)
                    routine_results.append(result)
                    return result

                def counted_copy(*args, **kwargs):
                    published_at.append(args[1].published_at)
                    return real_copy(*args, **kwargs)

                with (
                    mock.patch.object(
                        maintenance_service,
                        "run_routine_backup",
                        side_effect=counted_routine,
                    ),
                    mock.patch.object(
                        backup_service,
                        "_copy",
                        side_effect=counted_copy,
                    ),
                    mock.patch.object(
                        cli_service,
                        "build_viewer_snapshot",
                        side_effect=AssertionError(
                            "M14.3 backup-only benchmark performed Viewer work"
                        ),
                    ) as viewer_snapshot,
                    mock.patch.object(
                        cli_service,
                        "write_viewer_html",
                        side_effect=AssertionError(
                            "M14.3 backup-only benchmark published a Viewer"
                        ),
                    ) as viewer_write,
                ):
                    enabled_timings = run_write_sequence(
                        self,
                        enabled,
                        task_id,
                        maintenance_enabled=True,
                    )

                self.assertEqual(len(routine_results), len(WRITE_OFFSETS))
                self.assertEqual(
                    [
                        WRITE_OFFSETS[index]
                        for index, result in enumerate(routine_results)
                        if result.attempted
                    ],
                    list(EXPECTED_BACKUP_OFFSETS),
                )
                self.assertEqual(
                    published_at,
                    [timestamp(minute) for minute in EXPECTED_BACKUP_OFFSETS],
                )
                self.assertEqual(
                    len(discover_managed_backup_metadata(enabled)),
                    3,
                )
                self.assertEqual(
                    len(read_managed_backup_repository(enabled).generations),
                    3,
                )
                viewer_snapshot.assert_not_called()
                viewer_write.assert_not_called()
                self.assertFalse(
                    (
                        enabled.db_path.parent
                        / "task-viewer.html"
                    ).exists()
                )

                assert_fixed_fixture_bytes(
                    self,
                    enabled.db_path,
                    task_count=task_count,
                    event_count=event_count + len(WRITE_OFFSETS),
                )
                disabled_total = sum(disabled_timings)
                enabled_total = sum(enabled_timings)
                self.assertLess(enabled_total - disabled_total, 10.0)
                print(
                    "backup-performance "
                    f"{name}: disabled={disabled_total:.3f}s "
                    f"backup-only={enabled_total:.3f}s "
                    f"delta={enabled_total - disabled_total:.3f}s "
                    f"max-command={max(*disabled_timings, *enabled_timings):.3f}s "
                    f"publications={len(published_at)}"
                )


if __name__ == "__main__":
    unittest.main()
