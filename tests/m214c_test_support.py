"""Shared TG-M21.4C fixtures; intentionally not a collected test module."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from tests.m14_test_support import initialize_taskgov_internal, run_taskgov_internal


FIXED_CODE = "project_state_unreadable"
FIXED_MESSAGE = "project state could not be read safely"
PRIVATE_SENTINEL = "token=m214c-private-sentinel"


def valid_stored_task_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "task_id": "tg_task_0123456789abcdef",
        "project_id": "tg_project_0123456789abcdef0123456789abcdef",
        "title": "Validate stored Task reads",
        "description": "",
        "kind": "optional",
        "lane": "",
        "lane_order": None,
        "priority": "normal",
        "status": "ready",
        "blocked_reason": "",
        "pause_reason": "",
        "review_tier": 1,
        "verification": "focused offline verification",
        "tags": "m21,validation",
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
        "completed_at": None,
        "completion_commit_required": 1,
        "completion_commit_hash": "",
        "completion_evidence_kind": "none",
        "completion_evidence_revision": "",
        "completion_evidence_reason": "",
        "external_revision_approved": 0,
        "review_target_kind": "",
        "review_target_value": "",
        "review_target_base_revision": "",
        "review_target_generation": 0,
        "current_contract_revision": 0,
        "completion_history_coverage": "complete",
    }
    row.update(overrides)
    return row


def seed_current_task(
    root: Path,
    *,
    status: str = "ready",
    title: str = "Stored Task boundary",
    priority: str = "normal",
) -> tuple[Path, Path, dict[str, Any]]:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    db = root / "taskgov.sqlite"
    initialize_taskgov_internal(repo=repo, db=db)
    result = run_taskgov_internal(
        "task",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        "--title",
        title,
        "--status",
        status,
        "--priority",
        priority,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return repo, db, json.loads(result.stdout)["data"]["task"]


def inject_task_fault(
    db: Path,
    task_id: str,
    *,
    assignments: dict[str, Any],
) -> dict[str, str]:
    columns = tuple(assignments)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        set_clause = ", ".join(f"{column} = ?" for column in columns)
        connection.execute(
            f"UPDATE tasks SET {set_clause} WHERE task_id = ?",
            (*assignments.values(), task_id),
        )
        storage = connection.execute(
            "SELECT "
            + ", ".join(f"typeof({column}) AS {column}" for column in columns)
            + " FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        connection.commit()
    if storage is None:
        raise AssertionError("fault target task was not found")
    return {column: str(storage[index]) for index, column in enumerate(columns)}


def assert_fixed_cli_failure(testcase: Any, result: Any) -> dict[str, Any]:
    testcase.assertEqual(result.returncode, 2, result.stdout or result.stderr)
    payload = json.loads(result.stdout)
    testcase.assertFalse(payload["ok"])
    testcase.assertEqual(payload["warnings"], [])
    testcase.assertEqual(
        payload["errors"],
        [{"code": FIXED_CODE, "message": FIXED_MESSAGE}],
    )
    serialized = result.stdout + result.stderr
    testcase.assertNotIn(PRIVATE_SENTINEL, serialized)
    return payload
