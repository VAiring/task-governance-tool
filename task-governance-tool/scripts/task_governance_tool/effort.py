"""Optional, informational effort observations for one task."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.completion import (
    FULL_GIT_OBJECT_ID,
    safe_git_command,
    safe_git_environment,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    ProjectIdentity,
    StorageError,
    connect_initialized_readonly,
    operational_sqlite_error,
    utc_now,
)


CONFIG_RELATIVE_PATH = Path("config") / "effort-advisory.json"
CONFIG_SCHEMA_VERSION = 1
PROFILE_ID = "informational-v1"
CONFIG_SIZE_LIMIT = 16 * 1024
SQLITE_INT64_MAX = (1 << 63) - 1
ACTIVE_EFFORT_STATUSES = {"in_progress", "review_pending"}
METRIC_ORDER = (
    "changed_files",
    "changed_lines",
    "changed_modules",
    "contract_revisions",
    "handoffs",
)
UNKNOWN_REASON_ORDER = (
    "profile_invalid",
    "basis_missing",
    "basis_dirty",
    "basis_uncertain",
    "non_git_repository",
    "observation_dirty",
    "observation_uncertain",
    "coverage_missing",
    "active_task_overlap",
    "activity_generation_uncertain",
)
WARNING_KEY = "effort_advisory.threshold_exceeded.v1"


@dataclass(frozen=True)
class EffortProfile:
    present: bool
    valid: bool
    enabled: bool
    profile_id: str | None
    version: int | None
    profile_hash: str | None
    thresholds: dict[str, int]
    diagnostic: str | None = None


@dataclass(frozen=True)
class GitEndpoint:
    available: bool
    revision: str | None
    clean: bool | None
    status_bytes: bytes | None


@dataclass(frozen=True)
class GitMeasurements:
    endpoint: GitEndpoint
    values: dict[str, int | None]
    coverage: dict[str, str]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EffortAdvisoryResult:
    data: dict[str, Any]
    warnings: list[dict[str, str]]


class EffortAdvisoryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def disabled_profile(*, present: bool = False, diagnostic: str | None = None) -> EffortProfile:
    return EffortProfile(
        present=present,
        valid=diagnostic is None,
        enabled=False,
        profile_id=None,
        version=None,
        profile_hash=None,
        thresholds={},
        diagnostic=diagnostic,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_effort_profile(skill_root: Path) -> EffortProfile:
    """Load the one fixed project-scoped advisory profile without writing it."""
    path = skill_root / CONFIG_RELATIVE_PATH
    if not path.exists():
        return disabled_profile()
    try:
        if path.is_symlink() or not path.is_file():
            return disabled_profile(present=True, diagnostic="profile_invalid")
        if path.stat().st_size > CONFIG_SIZE_LIMIT:
            return disabled_profile(present=True, diagnostic="profile_invalid")
        payload = path.read_bytes()
        if len(payload) > CONFIG_SIZE_LIMIT:
            return disabled_profile(present=True, diagnostic="profile_invalid")
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(parsed, dict):
            raise ValueError("profile must be a JSON object")
        allowed = {"schema_version", "profile", "enabled", "thresholds"}
        if set(parsed) - allowed:
            raise ValueError("profile contains unsupported fields")
        if parsed.get("schema_version") != CONFIG_SCHEMA_VERSION:
            raise ValueError("unsupported profile schema")
        if parsed.get("profile") != PROFILE_ID:
            raise ValueError("unsupported profile id")
        enabled = parsed.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        raw_thresholds = parsed.get("thresholds", {})
        if not isinstance(raw_thresholds, dict) or set(raw_thresholds) - set(METRIC_ORDER):
            raise ValueError("thresholds contain unsupported metrics")
        thresholds: dict[str, int] = {}
        for key, value in raw_thresholds.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("thresholds must be integers")
            if value < 0 or value > SQLITE_INT64_MAX:
                raise ValueError("threshold is outside the supported range")
            thresholds[key] = value
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return disabled_profile(present=True, diagnostic="profile_invalid")

    return EffortProfile(
        present=True,
        valid=True,
        enabled=enabled,
        profile_id=PROFILE_ID,
        version=CONFIG_SCHEMA_VERSION,
        profile_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        thresholds={key: thresholds[key] for key in METRIC_ORDER if key in thresholds},
    )


def _run_git(repo: Path, arguments: list[str]) -> bytes | None:
    try:
        result = subprocess.run(
            [
                *safe_git_command(repo),
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=2,
            env=safe_git_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _read_git_endpoint_once(repo: Path) -> GitEndpoint:
    revision_payload = _run_git(
        repo,
        ["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"],
    )
    status_payload = _run_git(
        repo,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=normal",
            "--ignore-submodules=all",
            "--",
        ],
    )
    if revision_payload is None or status_payload is None:
        return GitEndpoint(False, None, None, None)
    try:
        revisions = [
            line.strip().decode("ascii").lower()
            for line in revision_payload.splitlines()
            if line.strip()
        ]
    except UnicodeError:
        return GitEndpoint(False, None, None, None)
    if len(revisions) != 1 or not FULL_GIT_OBJECT_ID.fullmatch(revisions[0]):
        return GitEndpoint(False, None, None, None)
    return GitEndpoint(
        available=True,
        revision=revisions[0],
        clean=not bool(status_payload),
        status_bytes=status_payload,
    )


def capture_git_basis(repo: Path) -> GitEndpoint | None:
    """Capture one stable HEAD/cleanliness pair; never return partial metadata."""
    before = _read_git_endpoint_once(repo)
    after = _read_git_endpoint_once(repo)
    if (
        not before.available
        or not after.available
        or before.revision != after.revision
        or before.status_bytes != after.status_bytes
    ):
        return None
    return before


def _project_has_effort_basis(connection: sqlite3.Connection, project_id: str) -> bool:
    table = connection.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name = 'task_effort_bases'
        """
    ).fetchone()
    if table is None:
        return False
    row = connection.execute(
        "SELECT 1 FROM task_effort_bases WHERE project_id = ? LIMIT 1",
        (project_id,),
    ).fetchone()
    return row is not None


def _increment_activity(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> tuple[int, int]:
    project_row = connection.execute(
        """
        SELECT effort_activity_generation
          FROM project_meta
         WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if project_row is None:
        raise sqlite3.IntegrityError("project metadata is missing")
    project_generation = int(project_row["effort_activity_generation"])
    task_row = connection.execute(
        """
        SELECT generation
          FROM task_effort_activity
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project_id, task_id),
    ).fetchone()
    subject_generation = int(task_row["generation"]) if task_row is not None else 0
    if project_generation >= SQLITE_INT64_MAX or subject_generation >= SQLITE_INT64_MAX:
        raise OverflowError("effort activity generation overflow")
    project_generation += 1
    subject_generation += 1
    connection.execute(
        """
        UPDATE project_meta
           SET effort_activity_generation = ?
         WHERE project_id = ?
        """,
        (project_generation, project_id),
    )
    connection.execute(
        """
        INSERT INTO task_effort_activity(task_id, project_id, generation)
        VALUES (?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET generation = excluded.generation
        """,
        (task_id, project_id, subject_generation),
    )
    return project_generation, subject_generation


def record_task_transition(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    *,
    task_id: str,
    previous_status: str | None,
    current_status: str,
    profile: EffortProfile | None,
    occurred_at: str,
) -> None:
    """Maintain enabled advisory metadata inside the owning task transaction."""
    effective_profile = profile or disabled_profile()
    has_existing_basis = _project_has_effort_basis(connection, project.project_id)
    if not effective_profile.enabled and not has_existing_basis:
        return

    previous_active = previous_status in ACTIVE_EFFORT_STATUSES
    current_active = current_status in ACTIVE_EFFORT_STATUSES
    project_row = connection.execute(
        """
        SELECT effort_activity_generation
          FROM project_meta
         WHERE project_id = ?
        """,
        (project.project_id,),
    ).fetchone()
    if project_row is None:
        return
    project_generation = int(project_row["effort_activity_generation"])
    subject_row = connection.execute(
        """
        SELECT generation
          FROM task_effort_activity
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project.project_id, task_id),
    ).fetchone()
    subject_generation = int(subject_row["generation"]) if subject_row is not None else 0

    if previous_active != current_active:
        activity_savepoint = "taskgov_effort_activity"
        connection.execute(f"SAVEPOINT {activity_savepoint}")
        try:
            project_generation, subject_generation = _increment_activity(
                connection,
                project_id=project.project_id,
                task_id=task_id,
            )
        except (OverflowError, sqlite3.Error):
            # Advisory bookkeeping must not reject the owning Task transition.
            connection.execute(f"ROLLBACK TO SAVEPOINT {activity_savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {activity_savepoint}")
            return
        connection.execute(f"RELEASE SAVEPOINT {activity_savepoint}")

    if (
        not effective_profile.enabled
        or current_status != "in_progress"
        or previous_status == current_status
    ):
        return
    existing_basis = connection.execute(
        """
        SELECT 1
          FROM task_effort_bases
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project.project_id, task_id),
    ).fetchone()
    if existing_basis is not None:
        return

    endpoint = capture_git_basis(project.canonical_repo)
    if endpoint is None or endpoint.revision is None or endpoint.clean is None:
        return
    other_active = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM tasks
             WHERE project_id = ?
               AND task_id != ?
               AND status IN ('in_progress', 'review_pending')
            """,
            (project.project_id, task_id),
        ).fetchone()[0]
    )
    savepoint = "taskgov_effort_basis"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        connection.execute(
            """
            INSERT INTO task_effort_bases(
              task_id,
              project_id,
              basis_head,
              basis_clean,
              captured_at,
              project_generation,
              subject_generation,
              other_active_at_capture
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                project.project_id,
                endpoint.revision,
                int(endpoint.clean),
                occurred_at,
                project_generation,
                subject_generation,
                int(other_active > 0),
            ),
        )
    except (sqlite3.Error, OSError):
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
    finally:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")


def _parse_numstat(payload: bytes) -> tuple[set[bytes], int | None] | None:
    if payload and not payload.endswith(b"\0"):
        return None
    records = payload[:-1].split(b"\0") if payload else []
    paths: set[bytes] = set()
    line_total = 0
    complete_lines = True
    for record in records:
        parts = record.split(b"\t", 2)
        if len(parts) != 3 or not parts[2]:
            return None
        added, deleted, path = parts
        paths.add(path)
        if added == b"-" or deleted == b"-":
            complete_lines = False
        elif not added.isdigit() or not deleted.isdigit():
            return None
        else:
            line_total += int(added) + int(deleted)
    return paths, line_total if complete_lines else None


def _parse_nul_paths(payload: bytes) -> set[bytes] | None:
    if not payload:
        return set()
    if not payload.endswith(b"\0"):
        return None
    records = payload[:-1].split(b"\0")
    if any(not record for record in records):
        return None
    return set(records)


def _module_count(paths: set[bytes]) -> int:
    return len({path.split(b"/", 1)[0] if b"/" in path else b"." for path in paths})


def observe_git_measurements(repo: Path, basis_revision: str) -> GitMeasurements:
    normalized_basis = basis_revision.strip().lower()
    if not FULL_GIT_OBJECT_ID.fullmatch(normalized_basis):
        return GitMeasurements(
            GitEndpoint(False, None, None, None),
            {
                "changed_files": None,
                "changed_lines": None,
                "changed_modules": None,
            },
            {
                "changed_files": "unavailable",
                "changed_lines": "unavailable",
                "changed_modules": "unavailable",
            },
            ("basis_uncertain",),
        )

    before = _read_git_endpoint_once(repo)
    empty_values = {
        "changed_files": None,
        "changed_lines": None,
        "changed_modules": None,
    }
    empty_coverage = {
        "changed_files": "unavailable",
        "changed_lines": "unavailable",
        "changed_modules": "unavailable",
    }
    if not before.available or before.revision is None:
        return GitMeasurements(
            before,
            empty_values,
            empty_coverage,
            ("non_git_repository",),
        )

    arguments = [
        "diff",
        "--numstat",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=all",
        normalized_basis,
    ]
    if before.clean:
        arguments.append(before.revision)
    arguments.append("--")
    numstat_payload = _run_git(repo, arguments)
    untracked_payload = (
        b""
        if before.clean
        else _run_git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--"])
    )
    after = _read_git_endpoint_once(repo)
    if (
        not after.available
        or before.revision != after.revision
        or before.status_bytes != after.status_bytes
    ):
        return GitMeasurements(
            GitEndpoint(True, after.revision, after.clean, after.status_bytes),
            empty_values,
            empty_coverage,
            ("observation_uncertain",),
        )
    parsed = _parse_numstat(numstat_payload) if numstat_payload is not None else None
    untracked = _parse_nul_paths(untracked_payload) if untracked_payload is not None else None
    if parsed is None or untracked is None:
        return GitMeasurements(
            after,
            empty_values,
            empty_coverage,
            ("coverage_missing",),
        )
    paths, changed_lines = parsed
    paths.update(untracked)
    if untracked:
        changed_lines = None
    values = {
        "changed_files": len(paths),
        "changed_lines": changed_lines,
        "changed_modules": _module_count(paths),
    }
    coverage = {
        "changed_files": "complete",
        "changed_lines": "complete" if changed_lines is not None else "unavailable",
        "changed_modules": "complete",
    }
    reasons: list[str] = []
    if not bool(after.clean):
        reasons.append("observation_dirty")
    if changed_lines is None:
        reasons.append("coverage_missing")
    return GitMeasurements(after, values, coverage, tuple(reasons))


def _empty_advisory_data(
    task_id: str,
    *,
    profile: EffortProfile,
) -> dict[str, Any]:
    reason = "profile_invalid" if not profile.valid else "profile_disabled"
    return {
        "task_id": task_id,
        "enabled": False,
        "profile": {
            "id": profile.profile_id,
            "version": profile.version,
            "hash": profile.profile_hash,
        },
        "measurements": {key: None for key in METRIC_ORDER},
        "thresholds": {},
        "exceeded": [],
        "basis": {
            "status": "not_captured",
            "revision": None,
            "clean": None,
            "captured_at": None,
            "activity_generation": None,
        },
        "observation": {
            "revision": None,
            "clean": None,
            "observed_at": None,
        },
        "coverage": {key: "disabled" for key in METRIC_ORDER},
        "attribution": "unknown",
        "unknown_reasons": [reason],
        "warning_key": WARNING_KEY,
        "suggested_action": "continue",
    }


def _ordered_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in UNKNOWN_REASON_ORDER if reason in reasons]


def _fresh_activity_generations(
    target: DatabaseTarget,
    *,
    task_id: str,
) -> tuple[int, int] | None:
    """Read activity counters after Git observation from a validated snapshot."""
    try:
        with closing(connect_initialized_readonly(target)) as connection:
            project_row = connection.execute(
                """
                SELECT effort_activity_generation
                  FROM project_meta
                 WHERE project_id = ?
                """,
                (target.project.project_id,),
            ).fetchone()
            subject_row = connection.execute(
                """
                SELECT generation
                  FROM task_effort_activity
                 WHERE project_id = ?
                   AND task_id = ?
                """,
                (target.project.project_id, task_id),
            ).fetchone()
    except StorageError as exc:
        if exc.code in {"database_busy", "unsupported_journal_mode"}:
            raise
        return None
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message="could not refresh effort activity",
        )
        if mapped.code == "database_busy":
            raise mapped from exc
        return None
    except OSError:
        return None
    if project_row is None:
        return None
    try:
        return (
            int(project_row["effort_activity_generation"]),
            int(subject_row["generation"]) if subject_row is not None else 0,
        )
    except (TypeError, ValueError, OverflowError):
        return None


def build_effort_advisory(
    connection: sqlite3.Connection,
    project: ProjectIdentity,
    task_id: str,
    profile: EffortProfile,
    *,
    db_path: Path | None = None,
    database_target: DatabaseTarget | None = None,
) -> EffortAdvisoryResult:
    task = connection.execute(
        """
        SELECT task_id, current_contract_revision
          FROM tasks
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project.project_id, task_id),
    ).fetchone()
    if task is None:
        raise EffortAdvisoryError("not_found", "task was not found")
    if not profile.enabled:
        if connection.in_transaction:
            connection.rollback()
        return EffortAdvisoryResult(
            data=_empty_advisory_data(task_id, profile=profile),
            warnings=[],
        )

    basis_row = connection.execute(
        """
        SELECT *
          FROM task_effort_bases
         WHERE project_id = ?
           AND task_id = ?
        """,
        (project.project_id, task_id),
    ).fetchone()
    basis = dict(basis_row) if basis_row is not None else None
    contract_revisions = int(task["current_contract_revision"])
    handoffs = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM handoff_records
             WHERE project_id = ?
               AND source_task_id = ?
            """,
            (project.project_id, task_id),
        ).fetchone()[0]
    )
    if connection.in_transaction:
        connection.rollback()
    measurements: dict[str, int | None] = {
        "changed_files": None,
        "changed_lines": None,
        "changed_modules": None,
        "contract_revisions": contract_revisions,
        "handoffs": handoffs,
    }
    coverage = {
        "changed_files": "unavailable",
        "changed_lines": "unavailable",
        "changed_modules": "unavailable",
        "contract_revisions": "complete",
        "handoffs": "complete",
    }
    reasons: set[str] = set()
    observation = GitEndpoint(False, None, None, None)
    basis_revision: str | None = None
    if basis is None:
        reasons.add("basis_missing")
        endpoint = _read_git_endpoint_once(project.canonical_repo)
        if not endpoint.available:
            reasons.add("non_git_repository")
        observation = endpoint
    else:
        raw_basis_revision = str(basis["basis_head"]).strip().lower()
        if FULL_GIT_OBJECT_ID.fullmatch(raw_basis_revision):
            basis_revision = raw_basis_revision
        if not bool(basis["basis_clean"]):
            reasons.add("basis_dirty")
        git_measurements = observe_git_measurements(
            project.canonical_repo,
            raw_basis_revision,
        )
        observation = git_measurements.endpoint
        measurements.update(git_measurements.values)
        coverage.update(git_measurements.coverage)
        reasons.update(git_measurements.reasons)

        refresh_target = database_target
        if refresh_target is None and db_path is not None:
            refresh_target = DatabaseTarget(
                project=project,
                db_path=db_path,
                explicit_db=True,
            )
        generations = (
            _fresh_activity_generations(
                refresh_target,
                task_id=task_id,
            )
            if refresh_target is not None
            else None
        )
        if generations is None:
            reasons.add("activity_generation_uncertain")
        else:
            current_project_generation, current_subject_generation = generations
            project_delta = current_project_generation - int(basis["project_generation"])
            subject_delta = current_subject_generation - int(basis["subject_generation"])
            if project_delta < 0 or subject_delta < 0 or subject_delta > project_delta:
                reasons.add("activity_generation_uncertain")
            elif bool(basis["other_active_at_capture"]) or project_delta > subject_delta:
                reasons.add("active_task_overlap")

    for metric in profile.thresholds:
        if coverage.get(metric) != "complete":
            reasons.add("coverage_missing")

    exceeded = [
        metric
        for metric in METRIC_ORDER
        if metric in profile.thresholds
        and measurements[metric] is not None
        and int(measurements[metric]) > profile.thresholds[metric]
    ]
    observed_at = utc_now()
    data = {
        "task_id": task_id,
        "enabled": True,
        "profile": {
            "id": profile.profile_id,
            "version": profile.version,
            "hash": profile.profile_hash,
        },
        "measurements": measurements,
        "thresholds": profile.thresholds,
        "exceeded": exceeded,
        "basis": {
            "status": (
                "captured"
                if basis is not None and basis_revision is not None
                else "invalid"
                if basis is not None
                else "missing"
            ),
            "revision": basis_revision,
            "clean": bool(basis["basis_clean"]) if basis is not None else None,
            "captured_at": str(basis["captured_at"]) if basis is not None else None,
            "activity_generation": (
                int(basis["project_generation"]) if basis is not None else None
            ),
        },
        "observation": {
            "revision": observation.revision,
            "clean": observation.clean,
            "observed_at": observed_at,
        },
        "coverage": coverage,
        "attribution": "exclusive_task_window" if not reasons else "unknown",
        "unknown_reasons": _ordered_reasons(reasons),
        "warning_key": WARNING_KEY,
        "suggested_action": "continue",
    }
    warnings = []
    if exceeded:
        warnings.append(
            {
                "code": "effort_advisory_threshold_exceeded",
                "message": "One or more configured effort thresholds were exceeded.",
                "warning_key": WARNING_KEY,
                "suggested_action": "continue",
            }
        )
    return EffortAdvisoryResult(data=data, warnings=warnings)
