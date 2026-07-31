"""Read-only M19 historical reconstruction for the M20 repository study.

This module is deliberately root-only.  It is not imported by the installable
package and it exposes no taskgov command.  All SQLite statements used by the
reconstruction live in :class:`_HistoryRepository`; reduction receives only
sanitized counts and fixed identifiers.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


sys.dont_write_bytecode = True

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "task-governance-tool" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from task_governance_tool.completion import (  # noqa: E402
    CompletionEvidenceError,
    safe_git_command,
    safe_git_environment,
    validate_evidence_matrix,
)
from task_governance_tool.contracts import read_current_contract  # noqa: E402
from task_governance_tool.handoffs import (  # noqa: E402
    HANDOFF_STATES,
    HandoffError,
    handoff_summary_for_task,
)
from task_governance_tool.project_scope import inspect_project_scope  # noqa: E402
from task_governance_tool.reviews import (  # noqa: E402
    ReviewEvidenceError,
    read_review_evidence,
    validate_stored_review_target,
)
from task_governance_tool.state_resolver import (  # noqa: E402
    canonical_state_paths,
    consumer_error_code,
    resolve_project_state,
)
from task_governance_tool.storage import (  # noqa: E402
    SCHEMA_VERSION,
    SQLITE_INT64_MAX,
    StorageError,
    is_sqlite_busy_or_locked,
    read_completion_history,
    read_latest_completion_cycle,
    validate_completion_cycle_storage,
)
from task_governance_tool.tasks import (  # noqa: E402
    TaskRepositoryError,
    TaskValidationError,
    validate_sqlite_int64,
)


_BASELINE_RE = re.compile(r"[0-9a-f]{40}\Z")
_TASK_ID_RE = re.compile(r"tg_task_[0-9a-f]{16}\Z")
_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TIMESTAMP_RE = re.compile(rb"(?:0|[1-9][0-9]*)\Z")
_ROADMAP_MAX_BYTES = 512 * 1024
_GIT_TIMEOUT_SECONDS = 15

_SCENARIO_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_M19_LABEL_RE = re.compile(r"TG-M19\.(?:0|[1-9][0-9]*)(?:A|B)?\Z")
_TASK_FACT_METRICS = (
    "completion_cycles",
    "reopens",
    "contract_revisions",
    "review_receipts",
    "changes_requested_receipts",
    "findings_open_high",
    "findings_open_medium",
    "findings_open_low",
    "handoffs_pending",
    "handoffs_delivered",
    "handoffs_withdrawn",
)
_GIT_METRIC = "git_wall_clock_span_ms"
_SUPPORTED_METRICS = frozenset((*_TASK_FACT_METRICS, _GIT_METRIC))


class M20HistoryError(Exception):
    """Sanitized source-level failure raised by historical reconstruction."""

    def __init__(self, code: str) -> None:
        if code not in {"source_missing", "source_drift", "parse_failed"}:
            code = "source_drift"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _ProtocolTask:
    label: str
    task_id: str
    expected_revision: str
    selector: str


@dataclass(frozen=True)
class _ProtocolCohort:
    scenario_id: str
    tasks: tuple[_ProtocolTask, ...]


@dataclass(frozen=True)
class _TaskFacts:
    task_id: str
    completion_cycles: int
    reopens: int
    legacy_history_incomplete: bool
    contract_revisions: int
    review_receipts: int
    changes_requested_receipts: int
    findings_open_high: int
    findings_open_medium: int
    findings_open_low: int
    handoffs_pending: int
    handoffs_delivered: int
    handoffs_withdrawn: int


def _parse_failed() -> M20HistoryError:
    return M20HistoryError("parse_failed")


def _source_missing() -> M20HistoryError:
    return M20HistoryError("source_missing")


def _source_drift() -> M20HistoryError:
    return M20HistoryError("source_drift")


def _ascii_sorted(values: Iterable[str]) -> list[str]:
    items = list(values)
    try:
        encoded = [(item.encode("ascii"), item) for item in items]
    except (AttributeError, UnicodeEncodeError) as exc:
        raise _parse_failed() from exc
    if len(items) != len(set(items)):
        raise _source_drift()
    return [item for _, item in sorted(encoded)]


def _checked_nonnegative(value: Any) -> int:
    if type(value) is not int or value < 0 or value > SQLITE_INT64_MAX:
        raise _source_drift()
    return value


def _checked_sum(values: Iterable[int]) -> int:
    total = 0
    for value in values:
        item = _checked_nonnegative(value)
        if total > SQLITE_INT64_MAX - item:
            raise _source_drift()
        total += item
    return total


def _require_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise _parse_failed()
    return value


def _parse_protocol(
    protocol: dict[str, Any],
) -> tuple[str, tuple[str, ...], tuple[_ProtocolCohort, ...]]:
    root = _require_mapping(protocol)
    if root.get("schema") != "m20-repository-protocol-v1":
        raise _parse_failed()
    authority = _require_mapping(root.get("authority"))
    if (
        set(authority)
        != {
            "contract_id",
            "contract_revision",
            "baseline_revision",
            "authority_revision",
        }
        or authority.get("contract_id") != "TG-M20-OPERATIONAL-BASELINE"
        or type(authority.get("contract_revision")) is not int
        or authority.get("contract_revision") != 1
        or not isinstance(authority.get("authority_revision"), str)
        or not _BASELINE_RE.fullmatch(authority["authority_revision"])
    ):
        raise _parse_failed()
    baseline = authority.get("baseline_revision")
    if not isinstance(baseline, str) or not _BASELINE_RE.fullmatch(baseline):
        raise _parse_failed()
    raw_metrics = root.get("retrospective_metrics")
    if (
        not isinstance(raw_metrics, list)
        or len(raw_metrics) != len(_SUPPORTED_METRICS)
        or any(not isinstance(metric, str) for metric in raw_metrics)
        or len(set(raw_metrics)) != len(raw_metrics)
        or set(raw_metrics) != _SUPPORTED_METRICS
    ):
        raise _parse_failed()
    metrics = tuple(raw_metrics)
    m20_2 = _require_mapping(root.get("m20_2"))
    raw_cohorts = m20_2.get("retrospective_cohorts")
    if not isinstance(raw_cohorts, list) or len(raw_cohorts) != 3:
        raise _parse_failed()

    cohorts: list[_ProtocolCohort] = []
    seen_scenarios: set[str] = set()
    seen_tasks: set[str] = set()
    seen_labels: set[str] = set()
    for raw_value in raw_cohorts:
        raw_cohort = _require_mapping(raw_value)
        if set(raw_cohort) != {"scenario_id", "tasks"}:
            raise _parse_failed()
        scenario_id = raw_cohort.get("scenario_id")
        if (
            not isinstance(scenario_id, str)
            or not _SCENARIO_ID_RE.fullmatch(scenario_id)
            or scenario_id in seen_scenarios
        ):
            raise _parse_failed()
        raw_tasks = raw_cohort.get("tasks")
        if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 8:
            raise _parse_failed()
        tasks: list[_ProtocolTask] = []
        for raw_task in raw_tasks:
            if (
                not isinstance(raw_task, (list, tuple))
                or len(raw_task) != 4
                or any(not isinstance(item, str) for item in raw_task)
            ):
                raise _parse_failed()
            label, task_id, revision, selector = raw_task
            if (
                not _M19_LABEL_RE.fullmatch(label)
                or not _TASK_ID_RE.fullmatch(task_id)
                or not revision
                or len(revision) > 500
                or selector not in {"completion_revision", "review_target"}
                or task_id in seen_tasks
                or label in seen_labels
            ):
                raise _parse_failed()
            if label == "TG-M19.9":
                if selector != "review_target" or not _FINGERPRINT_RE.fullmatch(
                    revision
                ):
                    raise _parse_failed()
            elif selector != "completion_revision":
                raise _parse_failed()
            seen_tasks.add(task_id)
            seen_labels.add(label)
            tasks.append(
                _ProtocolTask(
                    label=label,
                    task_id=task_id,
                    expected_revision=revision,
                    selector=selector,
                )
            )
        seen_scenarios.add(scenario_id)
        cohorts.append(
            _ProtocolCohort(scenario_id=scenario_id, tasks=tuple(tasks))
        )
    return baseline, metrics, tuple(cohorts)


def _strip_code_cell(value: str) -> str:
    text = value.strip()
    if len(text) < 2 or text[0] != "`" or text[-1] != "`":
        raise _parse_failed()
    inner = text[1:-1]
    if not inner or "`" in inner:
        raise _parse_failed()
    return inner


def _read_completion_index(repo_root: Path) -> dict[str, tuple[str, str]]:
    path = repo_root / "docs" / "implementation-roadmap.md"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise _source_missing() from exc
    if len(data) > _ROADMAP_MAX_BYTES:
        raise _parse_failed()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _parse_failed() from exc
    heading = "## Concise Completion Index"
    if text.count(heading) != 1:
        raise _parse_failed()
    section = text.split(heading, 1)[1]
    next_heading = re.search(r"(?m)^## ", section)
    if next_heading is None:
        raise _parse_failed()
    section = section[: next_heading.start()]
    rows: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("| TG-M19."):
            continue
        if not line.endswith("|"):
            raise _parse_failed()
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 4:
            raise _parse_failed()
        label = cells[0]
        if label in rows:
            raise _source_drift()
        rows[label] = (
            _strip_code_cell(cells[1]),
            _strip_code_cell(cells[2]),
        )
    return rows


def _validate_completion_index(
    repo_root: Path,
    cohorts: Sequence[_ProtocolCohort],
) -> None:
    observed = _read_completion_index(repo_root)
    expected = {
        task.label: (task.task_id, task.expected_revision)
        for cohort in cohorts
        for task in cohort.tasks
    }
    if observed != expected:
        raise _source_drift()


def _validate_scope(repo_root: Path, skill_root: Path) -> tuple[Path, Path]:
    try:
        canonical_repo = repo_root.resolve(strict=True)
        canonical_skill = skill_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _source_missing() from exc
    script = canonical_skill / "scripts" / "taskgov.py"
    inspection = inspect_project_scope(
        repo=canonical_repo,
        repo_explicit=True,
        script_path=script,
        include_runtime=True,
        include_package=True,
        include_ignore=True,
    )
    if inspection.scope is None or inspection.issues:
        raise _source_drift()
    if (
        inspection.scope.layout != "source"
        or inspection.scope.canonical_repo != canonical_repo
        or inspection.scope.skill_root != canonical_skill
        or canonical_skill.parent != canonical_repo
        or canonical_skill.name != "task-governance-tool"
    ):
        raise _source_drift()
    return canonical_repo, canonical_skill


class _HistoryRepository:
    """The sole SQL-owning boundary for the M19 reconstruction."""

    def __init__(self, connection: sqlite3.Connection, project_id: str) -> None:
        self._connection = connection
        self._project_id = project_id
        self._snapshot_validated = False

    def _validate_read_boundary(self) -> None:
        connection = self._connection
        if (
            not connection.in_transaction
            or int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1
            or int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1
            or connection.total_changes != 0
        ):
            raise _source_drift()

    def validate_snapshot(self) -> None:
        self._validate_read_boundary()
        if (
            self._connection.execute("PRAGMA foreign_key_check").fetchone()
            is not None
        ):
            raise _source_drift()
        validate_completion_cycle_storage(self._connection)
        self._snapshot_validated = True

    def validate_unchanged(self) -> None:
        if not self._snapshot_validated:
            raise _source_drift()
        self._validate_read_boundary()

    def capture(self, task: _ProtocolTask) -> _TaskFacts:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()
        if row is None:
            raise _source_missing()
        stored = dict(row)
        if (
            str(stored.get("project_id", "")) != self._project_id
            or str(stored.get("task_id", "")) != task.task_id
            or str(stored.get("status", "")) != "done"
            or stored.get("completed_at") is None
        ):
            raise _source_drift()
        try:
            validate_evidence_matrix(stored, allow_legacy=False)
        except CompletionEvidenceError as exc:
            raise _source_drift() from exc
        self._validate_selector(stored, task)
        contract_revision = self._validate_contract(stored)
        (
            cycle_count,
            reopen_count,
            history_incomplete,
        ) = self._validate_completion(stored)
        (
            receipt_count,
            changes_requested,
            open_findings,
        ) = self._validate_reviews(stored)
        handoffs = self._validate_handoffs(stored)
        return _TaskFacts(
            task_id=task.task_id,
            completion_cycles=cycle_count,
            reopens=reopen_count,
            legacy_history_incomplete=history_incomplete,
            contract_revisions=contract_revision,
            review_receipts=receipt_count,
            changes_requested_receipts=changes_requested,
            findings_open_high=open_findings["high"],
            findings_open_medium=open_findings["medium"],
            findings_open_low=open_findings["low"],
            handoffs_pending=handoffs["pending_handoff"],
            handoffs_delivered=handoffs["handed_off"],
            handoffs_withdrawn=handoffs["handoff_withdrawn_by_user"],
        )

    def _validate_selector(
        self,
        stored: dict[str, Any],
        task: _ProtocolTask,
    ) -> None:
        expected = task.expected_revision
        if task.selector == "review_target":
            valid = (
                stored.get("completion_evidence_kind")
                == "commit_not_required"
                and stored.get("completion_evidence_revision") == ""
                and stored.get("review_target_kind") == "diff_fingerprint"
                and stored.get("review_target_value") == expected
            )
        else:
            kind = str(stored.get("completion_evidence_kind", ""))
            valid = stored.get("completion_evidence_revision") == expected
            if _BASELINE_RE.fullmatch(expected):
                valid = valid and kind == "git_commit"
            else:
                valid = valid and kind == "external_revision"
        if not valid:
            raise _source_drift()

    def _validate_contract(self, stored: dict[str, Any]) -> int:
        try:
            revision = validate_sqlite_int64(
                stored.get("current_contract_revision"),
                field="current_contract_revision",
            )
            read_current_contract(
                self._connection,
                project_id=self._project_id,
                task_id=str(stored["task_id"]),
                current_revision=revision,
            )
        except (TaskRepositoryError, TaskValidationError) as exc:
            raise _source_drift() from exc
        rows = self._connection.execute(
            """
            SELECT project_id, revision
              FROM task_contract_revisions
             WHERE task_id = ?
             ORDER BY revision ASC
            """,
            (stored["task_id"],),
        ).fetchall()
        if any(str(row["project_id"]) != self._project_id for row in rows):
            raise _source_drift()
        ordinals = [int(row["revision"]) for row in rows]
        if ordinals != list(range(1, revision + 1)):
            raise _source_drift()
        return _checked_nonnegative(revision)

    @staticmethod
    def _cycle_projection(stored: dict[str, Any]) -> tuple[Any, ...]:
        return (
            stored.get("completed_at"),
            stored.get("current_contract_revision"),
            stored.get("review_tier"),
            (
                "specified"
                if str(stored.get("verification", "")).strip()
                else "unspecified"
            ),
            stored.get("completion_evidence_kind"),
            stored.get("completion_evidence_revision"),
            stored.get("completion_evidence_reason"),
            bool(stored.get("external_revision_approved")),
            bool(stored.get("completion_commit_required")),
            stored.get("completion_commit_hash"),
            stored.get("review_target_kind"),
            stored.get("review_target_value"),
            stored.get("review_target_base_revision"),
            stored.get("review_target_generation"),
        )

    @staticmethod
    def _stored_cycle_projection(cycle: Any) -> tuple[Any, ...]:
        return (
            cycle.completed_at,
            cycle.contract_revision,
            cycle.review_tier,
            cycle.verification_expectation,
            cycle.completion_evidence_kind,
            cycle.completion_evidence_revision,
            cycle.completion_evidence_reason,
            cycle.external_revision_approved,
            cycle.completion_commit_required,
            cycle.completion_commit_hash,
            cycle.review_target_kind,
            cycle.review_target_value,
            cycle.review_target_base_revision,
            cycle.review_target_generation,
        )

    def _validate_completion(
        self,
        stored: dict[str, Any],
    ) -> tuple[int, int, bool]:
        task_id = str(stored["task_id"])
        history = read_completion_history(
            self._connection,
            project_id=self._project_id,
            task_id=task_id,
            limit=10,
        )
        latest = read_latest_completion_cycle(
            self._connection,
            project_id=self._project_id,
            task_id=task_id,
        )
        if (
            latest is None
            or self._cycle_projection(stored)
            != self._stored_cycle_projection(latest)
        ):
            raise _source_drift()
        latest_events = self._connection.execute(
            """
            SELECT project_id, task_id, event_type
              FROM task_events
             WHERE completion_cycle_id = ?
            """,
            (latest.completion_cycle_id,),
        ).fetchall()
        if any(
            str(row["project_id"]) != self._project_id
            or str(row["task_id"]) != task_id
            for row in latest_events
        ) or any(
            str(row["event_type"]) == "task_reopened"
            for row in latest_events
        ):
            raise _source_drift()
        event_rows = self._connection.execute(
            """
            SELECT project_id, task_id, event_type, completion_cycle_id
              FROM task_events
             WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()
        if any(
            str(row["project_id"]) != self._project_id
            or str(row["task_id"]) != task_id
            for row in event_rows
        ):
            raise _source_drift()
        reopen_count = sum(
            1
            for row in event_rows
            if str(row["event_type"]) == "task_reopened"
        )
        return (
            _checked_nonnegative(history.total),
            _checked_nonnegative(reopen_count),
            bool(history.legacy_history_incomplete),
        )

    def _validate_reviews(
        self,
        stored: dict[str, Any],
    ) -> tuple[int, int, dict[str, int]]:
        task_id = str(stored["task_id"])
        try:
            evidence = read_review_evidence(
                self._connection,
                self._project_id,
                task_id,
                review_tier=int(stored["review_tier"]),
            )
        except (ReviewEvidenceError, TaskRepositoryError) as exc:
            raise _source_drift() from exc
        if not bool(evidence["gate"]["satisfied"]):
            raise _source_drift()

        rows = self._connection.execute(
            """
            SELECT review_receipt_id, project_id, task_id, reviewer_key,
                   receipt_kind, verdict, target_kind, target_value,
                   target_base_revision, target_generation, summary,
                   user_approved
              FROM review_receipts
             WHERE task_id = ?
             ORDER BY target_generation ASC, review_receipt_id ASC
            """,
            (task_id,),
        ).fetchall()
        targets: dict[int, tuple[str, str, str]] = {}
        changes_requested = 0
        current_generation = _checked_nonnegative(
            int(stored["review_target_generation"])
        )
        current_target = (
            str(stored["review_target_kind"]),
            str(stored["review_target_value"]),
            str(stored["review_target_base_revision"]),
        )
        for row in rows:
            if (
                str(row["project_id"]) != self._project_id
                or str(row["task_id"]) != task_id
            ):
                raise _source_drift()
            generation = int(row["target_generation"])
            if generation < 1 or generation > current_generation:
                raise _source_drift()
            target = (
                str(row["target_kind"]),
                str(row["target_value"]),
                str(row["target_base_revision"]),
            )
            previous = targets.setdefault(generation, target)
            if previous != target or (
                generation == current_generation and target != current_target
            ):
                raise _source_drift()
            try:
                validate_stored_review_target(
                    {
                        "review_target_kind": target[0],
                        "review_target_value": target[1],
                        "review_target_base_revision": target[2],
                    }
                )
            except ReviewEvidenceError as exc:
                raise _source_drift() from exc
            kind = str(row["receipt_kind"])
            verdict = str(row["verdict"])
            approved = int(row["user_approved"])
            summary = str(row["summary"])
            valid_receipt = (
                kind == "independent"
                and verdict in {"pass", "changes_requested"}
                and approved == 0
                or kind == "self_review_fallback"
                and verdict in {"pass", "changes_requested"}
                and approved in {0, 1}
                and bool(summary.strip())
                or kind == "not_required"
                and verdict == "not_required"
                and approved == 0
                and bool(summary.strip())
            )
            if not valid_receipt or (
                verdict == "changes_requested" and not summary.strip()
            ):
                raise _source_drift()
            if verdict == "changes_requested":
                changes_requested += 1

        receipt_count = _checked_nonnegative(len(rows))
        if receipt_count != _checked_nonnegative(
            int(evidence["counts"]["receipts_total"])
        ):
            raise _source_drift()

        finding_rows = self._connection.execute(
            """
            SELECT finding.severity, finding.status, finding.summary,
                   finding.resolution_summary, finding.resolved_at,
                   receipt.project_id AS receipt_project_id,
                   receipt.task_id AS receipt_task_id
              FROM review_findings AS finding
              JOIN review_receipts AS receipt
                ON receipt.review_receipt_id = finding.review_receipt_id
             WHERE receipt.task_id = ?
            """,
            (task_id,),
        ).fetchall()
        open_counts = {"high": 0, "medium": 0, "low": 0}
        for row in finding_rows:
            if (
                str(row["receipt_project_id"]) != self._project_id
                or str(row["receipt_task_id"]) != task_id
                or str(row["severity"]) not in open_counts
                or not str(row["summary"]).strip()
            ):
                raise _source_drift()
            status = str(row["status"])
            if status == "open":
                if (
                    row["resolved_at"] is not None
                    or str(row["resolution_summary"]) != ""
                ):
                    raise _source_drift()
                open_counts[str(row["severity"])] += 1
            elif status == "resolved":
                if (
                    not isinstance(row["resolved_at"], str)
                    or not str(row["resolution_summary"]).strip()
                ):
                    raise _source_drift()
            else:
                raise _source_drift()
        for severity in open_counts:
            open_counts[severity] = _checked_nonnegative(
                open_counts[severity]
            )
            if open_counts[severity] != _checked_nonnegative(
                int(evidence["counts"][f"open_{severity}"])
            ):
                raise _source_drift()
        return (
            receipt_count,
            _checked_nonnegative(changes_requested),
            open_counts,
        )

    def _validate_handoffs(self, stored: dict[str, Any]) -> dict[str, int]:
        task_id = str(stored["task_id"])
        try:
            summary = handoff_summary_for_task(
                self._connection,
                self._project_id,
                task_id,
            )
        except HandoffError as exc:
            raise _source_drift() from exc
        rows = self._connection.execute(
            """
            SELECT project_id, source_task_id, source_contract_revision
              FROM handoff_records
             WHERE source_task_id = ?
            """,
            (task_id,),
        ).fetchall()
        current_revision = int(stored["current_contract_revision"])
        if any(
            str(row["project_id"]) != self._project_id
            or str(row["source_task_id"]) != task_id
            or int(row["source_contract_revision"]) > current_revision
            for row in rows
        ):
            raise _source_drift()
        normalized = {
            state: _checked_nonnegative(int(summary[state]))
            for state in HANDOFF_STATES
        }
        if sum(normalized.values()) != len(rows):
            raise _source_drift()
        return normalized


def _storage_error(exc: StorageError) -> M20HistoryError:
    if exc.code in {
        "db_not_initialized",
        "database_busy",
        "unsupported_journal_mode",
    }:
        return _source_missing()
    return _source_drift()


def _capture_database(
    repo_root: Path,
    skill_root: Path,
    cohorts: Sequence[_ProtocolCohort],
) -> dict[str, tuple[_TaskFacts, ...]]:
    database = canonical_state_paths(skill_root).database
    resolution = resolve_project_state(
        skill_root=skill_root,
        repo=repo_root,
        include_doctor_state=False,
        retain_read_connection=True,
    )
    connection = resolution.read_connection
    read_error: Exception | None = None
    captured: dict[str, tuple[_TaskFacts, ...]] | None = None
    try:
        resolution_error = consumer_error_code(resolution)
        if resolution_error is not None:
            if resolution_error in {
                "db_not_initialized",
                "database_busy",
                "unsupported_journal_mode",
            }:
                raise _source_missing()
            raise _source_drift()
        if (
            resolution.layout != "fixed_current_v1"
            or resolution.binding != "matching"
            or resolution.source_schema_version != 16
            or resolution.source_schema_version != SCHEMA_VERSION
            or resolution.fixed_recovery is not None
            or resolution.target is None
            or resolution.project_id is None
            or connection is None
            or resolution.target.db_path.resolve(strict=False)
            != database.resolve(strict=False)
            or resolution.target.project.canonical_repo != repo_root
            or resolution.target.project.project_id != resolution.project_id
        ):
            raise _source_drift()
        repository = _HistoryRepository(connection, resolution.project_id)
        repository.validate_snapshot()
        try:
            captured = {
                cohort.scenario_id: tuple(
                    repository.capture(task) for task in cohort.tasks
                )
                for cohort in cohorts
            }
        finally:
            repository.validate_unchanged()
    except Exception as exc:  # checked and sanitized after closing the snapshot
        read_error = exc
    finally:
        if connection is not None:
            connection.close()
    if read_error is not None:
        if isinstance(read_error, M20HistoryError):
            raise read_error
        if isinstance(read_error, StorageError):
            raise _storage_error(read_error) from read_error
        if isinstance(read_error, sqlite3.Error):
            if is_sqlite_busy_or_locked(read_error):
                raise _source_missing() from read_error
            raise _source_drift() from read_error
        if isinstance(
            read_error,
            (
                TaskRepositoryError,
                TaskValidationError,
                ReviewEvidenceError,
                HandoffError,
                CompletionEvidenceError,
            ),
        ):
            raise _source_drift() from read_error
        if isinstance(read_error, (OSError, RuntimeError)):
            raise _source_missing() from read_error
        raise _source_drift() from read_error
    if captured is None:
        raise _source_drift()
    return captured


def _git_environment() -> dict[str, str]:
    environment = safe_git_environment()
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


def _run_git(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    capture_stdout: bool,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            [
                *safe_git_command(repo_root),
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=(subprocess.PIPE if capture_stdout else subprocess.DEVNULL),
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _source_missing() from exc
    return result


def _resolve_exact_commit(repo_root: Path, revision: str) -> str:
    if not _BASELINE_RE.fullmatch(revision):
        raise _parse_failed()
    result = _run_git(
        repo_root,
        [
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{revision}^{{commit}}",
        ],
        capture_stdout=True,
    )
    lines = [
        line.strip().lower()
        for line in result.stdout.splitlines()
        if line.strip()
    ]
    if result.returncode != 0:
        raise _source_missing()
    if (
        len(lines) != 1
        or len(lines[0]) != 40
        or lines[0].decode("ascii", errors="ignore") != revision
    ):
        raise _source_drift()
    return revision


def _committer_timestamp(repo_root: Path, commit: str) -> int:
    result = _run_git(
        repo_root,
        ["show", "-s", "--no-show-signature", "--format=%ct", commit, "--"],
        capture_stdout=True,
    )
    if result.returncode != 0:
        raise _source_missing()
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if (
        len(result.stdout) > 64
        or len(lines) != 1
        or not _TIMESTAMP_RE.fullmatch(lines[0])
    ):
        raise _parse_failed()
    value = int(lines[0])
    if value > SQLITE_INT64_MAX:
        raise _parse_failed()
    return value


def _git_span(
    repo_root: Path,
    baseline_revision: str,
    tasks: Sequence[_ProtocolTask],
) -> tuple[int, list[str]]:
    baseline = _resolve_exact_commit(repo_root, baseline_revision)
    commits = _git_references(tasks)
    if not commits:
        raise _source_drift()
    timestamps: list[int] = []
    for commit in commits:
        canonical = _resolve_exact_commit(repo_root, commit)
        ancestor = _run_git(
            repo_root,
            ["merge-base", "--is-ancestor", canonical, baseline],
            capture_stdout=False,
        )
        if ancestor.returncode == 1:
            raise _source_drift()
        if ancestor.returncode != 0:
            raise _source_missing()
        timestamps.append(_committer_timestamp(repo_root, canonical))
    span_seconds = max(timestamps) - min(timestamps)
    if span_seconds > SQLITE_INT64_MAX // 1000:
        raise _parse_failed()
    return span_seconds * 1000, commits


def _git_references(tasks: Sequence[_ProtocolTask]) -> list[str]:
    return _ascii_sorted(
        {
            task.expected_revision
            for task in tasks
            if task.selector == "completion_revision"
            and _BASELINE_RE.fullmatch(task.expected_revision)
        }
    )


def _complete_metric(
    metric: str,
    value: int,
    references: Sequence[str],
) -> dict[str, Any]:
    return {
        "metric": metric,
        "value": _checked_nonnegative(value),
        "coverage": "complete",
        "references": list(references),
        "unknowns": [],
    }


def _partial_metric(
    metric: str,
    references: Sequence[str],
    *,
    reason: str = "not_reconstructable",
) -> dict[str, Any]:
    return {
        "metric": metric,
        "value": None,
        "coverage": "partial",
        "references": list(references),
        "unknowns": [
            {"field": "value", "reasons": [reason]}
        ],
    }


def _reduce_scenario(
    repo_root: Path,
    baseline_revision: str,
    metrics: Sequence[str],
    cohort: _ProtocolCohort,
    facts: Sequence[_TaskFacts],
) -> list[dict[str, Any]]:
    if len(facts) != len(cohort.tasks) or {
        fact.task_id for fact in facts
    } != {task.task_id for task in cohort.tasks}:
        raise _source_drift()
    task_references = _ascii_sorted(task.task_id for task in cohort.tasks)
    incomplete_history = any(
        fact.legacy_history_incomplete for fact in facts
    )
    values = {
        metric: _checked_sum(getattr(fact, metric) for fact in facts)
        for metric in _TASK_FACT_METRICS
    }
    git_references = _git_references(cohort.tasks)
    if not git_references:
        raise _source_drift()
    try:
        git_value, git_references = _git_span(
            repo_root,
            baseline_revision,
            cohort.tasks,
        )
        git_record = _complete_metric(
            _GIT_METRIC,
            git_value,
            git_references,
        )
    except M20HistoryError as exc:
        git_record = _partial_metric(
            _GIT_METRIC,
            git_references,
            reason=exc.code,
        )
    records: list[dict[str, Any]] = []
    for metric in metrics:
        if metric == _GIT_METRIC:
            records.append(git_record)
        elif incomplete_history and metric in {
            "completion_cycles",
            "reopens",
        }:
            records.append(_partial_metric(metric, task_references))
        else:
            records.append(
                _complete_metric(metric, values[metric], task_references)
            )
    return records


def reconstruct_m19(
    repo_root: Path,
    skill_root: Path,
    protocol: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Reconstruct the three fixed M19 cohorts from one validated DB snapshot.

    The function never initializes, migrates, writes, fetches, or retains raw
    database/Git output.  A source-level failure raises ``M20HistoryError``
    with only a stable reason code.
    """

    try:
        baseline, metrics, cohorts = _parse_protocol(protocol)
        canonical_repo, canonical_skill = _validate_scope(
            Path(repo_root),
            Path(skill_root),
        )
        _validate_completion_index(canonical_repo, cohorts)
        captured = _capture_database(
            canonical_repo,
            canonical_skill,
            cohorts,
        )
        return {
            cohort.scenario_id: _reduce_scenario(
                canonical_repo,
                baseline,
                metrics,
                cohort,
                captured[cohort.scenario_id],
            )
            for cohort in cohorts
        }
    except M20HistoryError:
        raise
    except StorageError as exc:
        raise _storage_error(exc) from exc
    except sqlite3.Error as exc:
        if is_sqlite_busy_or_locked(exc):
            raise _source_missing() from exc
        raise _source_drift() from exc
    except (OSError, RuntimeError) as exc:
        raise _source_missing() from exc
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _parse_failed() from exc
    except Exception as exc:
        raise _source_drift() from exc
