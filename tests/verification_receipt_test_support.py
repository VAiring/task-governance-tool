from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import (
    initialize_taskgov_internal,
    run_taskgov_internal,
)
from tests.test_completion_cycle_history import (
    make_v14_target,
    migrate_to_v15,
    seed_v14_tasks,
)

from task_governance_tool.storage import (
    apply_completion_cycle_capture_activation_migration,
    connect,
    current_schema_version,
    resolve_database_target,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
SCRIPT_PATH = SKILL_ROOT / "scripts" / "taskgov.py"
FINGERPRINT_A = "sha256:" + "a" * 64
FINGERPRINT_B = "sha256:" + "b" * 64
DEFAULT_VERIFICATION = "python -m unittest tests.test_verification_receipts"


def run_taskgov(*args: str, maintenance_enabled: bool = False):
    return run_taskgov_internal(
        *args,
        maintenance_enabled=maintenance_enabled,
    )


def payload(result) -> dict:
    return json.loads(result.stdout)


def initialize(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    db = root / "state" / "taskgov.sqlite"
    repo.mkdir()
    initialize_taskgov_internal(repo=repo, db=db)
    return repo, db


def add_task(
    db: Path,
    repo: Path,
    *,
    title: str = "Verification Receipt task",
    status: str = "in_progress",
    verification: str = DEFAULT_VERIFICATION,
    review_tier: int = 0,
) -> dict:
    result = run_taskgov(
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
        "--review-tier",
        str(review_tier),
        "--verification",
        verification,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return payload(result)["data"]["task"]


def set_target(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    fingerprint: str = FINGERPRINT_A,
) -> int:
    result = run_taskgov(
        "review",
        "target",
        "set",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--kind",
        "diff_fingerprint",
        "--revision",
        fingerprint,
        "--json",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return int(payload(result)["data"]["task"]["review_target_generation"])


def seed_current_review_evidence(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    fingerprint: str = FINGERPRINT_A,
) -> int:
    generation = set_target(db, repo, task_id, fingerprint=fingerprint)
    with closing(sqlite3.connect(db)) as connection:
        tier = int(
            connection.execute(
                "SELECT review_tier FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
        )
    receipts = (
        (("mechanical-review", "not_required", "not_required"),)
        if tier == 0
        else tuple(
            (f"test-reviewer-{index}", "independent", "pass")
            for index in range(1, 3 if tier == 2 else 2)
        )
    )
    for reviewer, kind, verdict in receipts:
        args = [
            "review",
            "receipt",
            "add",
            "--repo",
            str(repo),
            "--db",
            str(db),
            task_id,
            "--reviewer",
            reviewer,
            "--kind",
            kind,
            "--verdict",
            verdict,
            "--summary",
            "Focused test review",
        ]
        if kind != "not_required":
            args.extend(
                (
                    "--reviewer-class",
                    "human",
                    "--model-state",
                    "not_applicable",
                    "--skill-state",
                    "not_applicable",
                    "--context-relation",
                    "external_context",
                )
            )
        result = run_taskgov(*args, "--json")
        if result.returncode != 0:
            raise AssertionError(result.stdout)
    return generation


def add_receipt(
    db: Path,
    repo: Path,
    task_id: str,
    generation: int,
    *,
    result: str = "pass",
    duration_ms: int | str = 25,
    scope_coverage: str = "full",
    json_output: bool = True,
    read_only: bool = False,
    maintenance_enabled: bool = False,
):
    args = [
        "verification",
        "receipt",
        "add",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--result",
        result,
        "--duration-ms",
        str(duration_ms),
        "--scope-coverage",
        scope_coverage,
        "--expected-target-generation",
        str(generation),
    ]
    if read_only:
        args.append("--read-only")
    if json_output:
        args.append("--json")
    return run_taskgov(
        *args,
        maintenance_enabled=maintenance_enabled,
    )


def show_task(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    json_output: bool,
):
    args = [
        "task",
        "show",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--read-only",
    ]
    if json_output:
        args.append("--json")
    return run_taskgov(*args)


def completion(
    db: Path,
    repo: Path,
    task_id: str,
    *,
    check: bool = False,
):
    args = [
        "task",
        "complete",
        "--repo",
        str(repo),
        "--db",
        str(db),
        task_id,
        "--verification-complete",
        "--review-complete",
        "--commit-not-required",
    ]
    if check:
        args.extend(("--check", "--read-only"))
    args.append("--json")
    return run_taskgov(*args)


def target_for(db: Path, repo: Path):
    return resolve_database_target(
        repo=repo,
        db=db,
        script_path=SCRIPT_PATH,
    )


def table_count(db: Path, table: str) -> int:
    with closing(sqlite3.connect(db)) as connection:
        return int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )


def initialize_v16_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    target = make_v14_target(root)
    task_ids = seed_v14_tasks(
        target,
        variants=("external_revision",),
        include_ready=True,
    )
    migrate_to_v15(target)
    with closing(connect(target.db_path)) as connection:
        apply_completion_cycle_capture_activation_migration(connection)
        if current_schema_version(connection) != 16:
            raise AssertionError("schema-v16 fixture activation failed")
    return target, task_ids["external_revision"]
