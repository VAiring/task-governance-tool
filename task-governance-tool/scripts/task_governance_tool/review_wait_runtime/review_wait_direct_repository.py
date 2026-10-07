"""Bounded development-only record for one non-replayable direct-wake probe.

This repository reuses the wait repository's physical-path, SQLite connection
and OS-lease protections. It never opens Task storage or saves host payloads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import UUID

from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository, RepositoryError, _missing, _identity
from task_governance_tool.review_wait_runtime.review_wait_host import HOST_BOUNDARY_REASONS


_SCHEMA = {
    "schema_history": "CREATE TABLE schema_history (version INTEGER PRIMARY KEY CHECK(version = 1), migration_name TEXT NOT NULL CHECK(migration_name = 'review-direct-source-v1'))",
    "direct_probe": "CREATE TABLE direct_probe (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), payload TEXT NOT NULL CHECK(length(payload) <= 4096))",
}
_STATUSES = {"waiting", "dispatching", "accepted", "rejected", "unknown", "cancelled", "abandoned", "expired", "suppressed"}
DIRECT_REASONS = frozenset({
    "invalid_request", "invalid_configuration", "caller_mismatch", "executor_context_required",
    "wait_not_prepared", "task_binding_changed", "heartbeat_changed", "probe_mismatch",
    "service_closed", "probe_already_exists", "parent_turn_mismatch", "parent_turn_changed",
    "reviewer_turn_changed", "worker_unavailable", "wait_changed", "clock_changed",
    "ack_turn_mismatch", "direct_probe_unavailable", "host_transport_unavailable",
    "invalid_host_configuration", "host_call_failed", "invalid_host_response",
    "child_status_unavailable", "timezone_not_admitted", "timezone_changed", "timezone_unavailable",
    "automation_config_unavailable", "automation_config_invalid", "automation_config_unsafe",
    "automation_config_changed", "wait_basis_unavailable", "wait_basis_invalid_arguments",
    "wait_basis_response_invalid", "state_path_invalid", "state_unreadable", "writer_busy",
    "binding_mismatch", "state_transition_invalid", "invalid_snapshot", "unsupported_journal_mode",
    "state_exists", "send_rejected", "send_unknown", "heartbeat_delete_unknown",
    "heartbeat_update_unknown", "appointment_unavailable", "timer_cleanup_unknown",
}) | HOST_BOUNDARY_REASONS


def marker_path(wait_path: Path) -> Path:
    path = Path(wait_path)
    return path.with_name(path.name + ".direct.sqlite")


def marker_exists(wait_path: Path) -> bool:
    """Fail closed for broken links or unavailable metadata; never follow them."""
    try:
        marker_path(wait_path).lstat()
        return True
    except FileNotFoundError:
        return False
    except (OSError, TypeError, ValueError):
        return True


def owns_timer(wait_path: Path) -> bool:
    """A deletion experiment or unreadable marker excludes generic cleanup."""
    if not marker_exists(wait_path):
        return False
    try:
        return DirectRepository.for_wait(wait_path).read().version == 2
    except Exception:
        return True


def _uuid(value: object) -> bool:
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def instant(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise RepositoryError("invalid_snapshot")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class DirectRecord:
    probe_id: str
    binding_digest: str
    original_parent_turn: str
    status: str
    created_at: str
    deadline: str
    acknowledged_turn: str | None = None
    reason: str | None = None
    version: int = 1
    timer_phase: str | None = None
    timer_rule: str | None = None
    timer_updated_at: int | None = None
    timer_due_at: str | None = None

    def __post_init__(self):
        try:
            valid = (_uuid(self.probe_id) and _uuid(self.original_parent_turn)
                and type(self.binding_digest) is str and re.fullmatch(r"[a-f0-9]{64}", self.binding_digest)
                and type(self.status) is str and self.status in _STATUSES
                and type(self.created_at) is str and len(self.created_at) <= 40
                and type(self.deadline) is str and len(self.deadline) <= 40
                and (self.reason is None or type(self.reason) is str and self.reason in DIRECT_REASONS))
            if not valid:
                raise ValueError
            began, end = datetime.fromisoformat(self.created_at), datetime.fromisoformat(self.deadline)
            if (instant(began) != self.created_at or instant(end) != self.deadline
                    or end - began != timedelta(minutes=10 if self.version == 1 else 20)):
                raise ValueError
            if type(self.version) is not int or self.version not in (1, 2):
                raise ValueError
            if self.version == 1:
                if any(v is not None for v in (self.timer_phase, self.timer_rule, self.timer_updated_at, self.timer_due_at)):
                    raise ValueError
            else:
                if (type(self.timer_phase) is not str or self.timer_phase not in
                        {"arming", "active", "deleting", "deleted", "pausing", "paused", "unknown"}
                        or type(self.timer_rule) is not str or began.microsecond != 0
                        or type(self.timer_updated_at) is not int or self.timer_updated_at < 0):
                    raise ValueError
                if type(self.timer_due_at) is not str or len(self.timer_due_at) > 40:
                    raise ValueError
                due = datetime.fromisoformat(self.timer_due_at)
                if instant(due) != self.timer_due_at or due - began != timedelta(minutes=10):
                    raise ValueError
                match = re.fullmatch(r"FREQ=DAILY;BYHOUR=(\d{1,2});BYMINUTE=(\d{1,2});BYSECOND=(\d{1,2});COUNT=1", self.timer_rule)
                if not match or any(int(x) > bound for x, bound in zip(match.groups(), (23, 59, 59))):
                    raise ValueError
                if self.status in {"dispatching", "accepted", "rejected", "unknown"} and self.timer_phase != "deleted":
                    raise ValueError
            if self.acknowledged_turn is not None:
                if (not _uuid(self.acknowledged_turn) or self.acknowledged_turn == self.original_parent_turn
                        or self.status not in {"dispatching", "accepted", "rejected", "unknown"}):
                    raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise RepositoryError("invalid_snapshot") from None


def _encode(record: DirectRecord) -> str:
    if type(record) is not DirectRecord:
        raise RepositoryError("invalid_snapshot")
    value = asdict(record)
    if record.version == 1:
        for field in ("version", "timer_phase", "timer_rule", "timer_updated_at", "timer_due_at"):
            value.pop(field)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class DirectRepository(ReviewWaitRepository):
    """One fixed sibling marker; existence permanently prevents another probe."""

    @classmethod
    def for_wait(cls, path: Path):
        return cls(marker_path(path))

    def exists(self) -> bool:
        return not _missing(self.path)  # lstat retains broken-link exclusion.

    @contextmanager
    def _serial(self, *, create_lock=False):
        # Reads and writes are short, so serialize both. A read must not hold a
        # SQLite shared lock while a sender's intent/settlement tries to commit.
        # Only OS-lock acquisition is retried, never a transaction or effect.
        deadline = time.monotonic() + 1
        while True:
            manager = self._lease(create_lock=create_lock)
            try:
                lease = manager.__enter__()
                break
            except RepositoryError as error:
                if error.code != "writer_busy" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        try:
            existing = self.exists()
            if existing:
                lease._database_identity = _identity(self._inspect())
                lease._check()
            yield lease
            if existing:
                lease._check()
        except BaseException:
            manager.__exit__(*sys.exc_info())
            raise
        else:
            manager.__exit__(None, None, None)

    def read(self) -> DirectRecord:
        with self._serial():
            with self._connection() as connection:
                return self._load(connection)

    def create_record(self, record: DirectRecord) -> None:
        if (record.status != "waiting" or record.acknowledged_turn is not None
                or record.version == 2 and record.timer_phase != "arming"):
            raise RepositoryError("invalid_snapshot")
        payload = _encode(record)
        if self.exists():
            raise RepositoryError("state_exists")
        with self._serial(create_lock=True):
            descriptor = None
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                raise RepositoryError("state_exists") from None
            except OSError:
                raise RepositoryError("state_unreadable") from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            with self._connection(write=True) as connection:
                for statement in _SCHEMA.values():
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
                connection.execute("INSERT INTO schema_history VALUES (1, 'review-direct-source-v1')")
                connection.execute("INSERT INTO direct_probe VALUES (1, ?)", (payload,))

    @staticmethod
    def _load(connection) -> DirectRecord:
        try:
            objects = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            if (objects != [("table", name, name, sql) for name, sql in sorted(_SCHEMA.items())]
                    or connection.execute("PRAGMA user_version").fetchone() != (1,)
                    or connection.execute("SELECT * FROM schema_history").fetchall() != [(1, "review-direct-source-v1")]):
                raise ValueError
            rows = connection.execute("SELECT singleton, payload FROM direct_probe").fetchall()
            if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not str or len(rows[0][1]) > 4096:
                raise ValueError
            value = json.loads(rows[0][1])
            if type(value) is not dict:
                raise ValueError
            record = DirectRecord(**value)
            if _encode(record) != rows[0][1]:
                raise ValueError
            return record
        except (RepositoryError, ValueError, TypeError, OverflowError, RecursionError):
            raise RepositoryError("state_unreadable") from None

    def update(self, probe_id: str, *, status: str | None = None,
               acknowledged_turn: str | None = None, reason: str | None = None,
               timer_phase: str | None = None, timer_updated_at: int | None = None) -> DirectRecord:
        """Read-modify-save under one lease; settlement never overwrites an ack."""
        with self._serial():
            with self._connection(write=True) as connection:
                before = self._load(connection)
                if before.probe_id != probe_id:
                    raise RepositoryError("binding_mismatch")
                if status is not None:
                    allowed = ({"dispatching", "cancelled", "abandoned", "expired", "suppressed"}
                        if before.status == "waiting" else {"accepted", "rejected", "unknown"}
                        if before.status == "dispatching" else set())
                    if status not in allowed:
                        raise RepositoryError("state_transition_invalid")
                if acknowledged_turn is not None:
                    if (before.status not in {"dispatching", "accepted", "unknown"}
                            or before.acknowledged_turn not in (None, acknowledged_turn)):
                        raise RepositoryError("state_transition_invalid")
                if reason is not None and status is None:
                    raise RepositoryError("state_transition_invalid")
                if timer_phase is not None:
                    transitions = {"arming": {"active", "unknown"}, "active": {"deleting", "pausing"},
                        "deleting": {"deleted", "unknown"}, "pausing": {"paused", "unknown"}}
                    if before.version != 2 or timer_phase not in transitions.get(before.timer_phase, set()):
                        raise RepositoryError("state_transition_invalid")
                    if timer_phase == "deleting" and before.status != "waiting":
                        raise RepositoryError("state_transition_invalid")
                    if timer_phase in {"active", "paused"}:
                        if type(timer_updated_at) is not int or timer_updated_at < before.timer_updated_at:
                            raise RepositoryError("state_transition_invalid")
                    elif timer_updated_at is not None:
                        raise RepositoryError("state_transition_invalid")
                elif timer_updated_at is not None:
                    raise RepositoryError("state_transition_invalid")
                after = replace(before, status=status or before.status,
                    acknowledged_turn=acknowledged_turn or before.acknowledged_turn,
                    reason=reason if status is not None else before.reason,
                    timer_phase=timer_phase or before.timer_phase,
                    timer_updated_at=before.timer_updated_at if timer_updated_at is None else timer_updated_at)
                connection.execute("UPDATE direct_probe SET payload = ? WHERE singleton = 1", (_encode(after),))
                return after
