"""Opt-in, bounded SQLite timing records without SQL or input retention.

Only an outer operation admits the physical project and existing marker. SQLite
instrumentation records fixed labels and numbers in memory; output is attempted
after that operation has released every registered connection. Logging is never
an admission, retry, recovery, or public-result decision.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4


MODES = {
    b"taskgov-sqlite-diagnostics-v1 transactions\n": "transactions",
    b"taskgov-sqlite-diagnostics-v1 failures\n": "failures",
}
MAX_FILES = 128
MAX_BYTES = 64 * 1024
MAX_RECORDS = 256
MAX_CONNECTIONS = 256
_MAX_INTEGER = 2**63 - 1
_OPERATIONS = frozenset({
    "task", "review", "verification", "handoff", "setup", "detect", "profile",
    "task.add", "task.list", "task.next", "task.current", "task.context", "task.effort",
    "task.show", "task.checkpoint", "task.edit", "task.complete",
    "handoff.record", "handoff.list", "handoff.show", "handoff.withdraw",
    "review.prepare", "review.target.set", "review.receipt.add", "review.result.add",
    "review.finding.add", "review.finding.resolve", "verification.receipt.add",
    "usage_hook", "review_handoff", "review_wait", "review_wait_worker",
    "maintenance", "other",
})
_ROLES = frozenset({"core", "numerical", "operational", "backup", "other"})
_ACCESS = frozenset({"readonly", "readwrite", "create"})
_PHASES = frozenset({
    "open", "begin", "implicit_begin", "statement", "commit", "rollback",
    "close", "script", "transaction_end", "context_exit",
})
_OUTCOMES = frozenset({"succeeded", "failed", "observed"})
_BEGIN_MODES = frozenset({"deferred", "immediate", "exclusive"})
_END_REASONS = frozenset({
    "ended_after_commit_failure", "commit", "rollback", "close", "script", "observed",
})
# Code-owned labels only. Unknown code remains "other"; caller strings are
# never accepted merely because they resemble a Python identifier.
_ORIGINS = {
    "storage": {"connect_initialized", "_connect_validated_readonly", "inspect_setup_state",
                "_initialize_database_with_identity", "migrate_bound_database", "begin_initialized_write"},
    "state_resolver": {"_inspect_database"},
    "tasks": {"lock_and_reread_edit_owner"},
    "usage_repository": {"connection", "initialize"},
    "usage_publication_repository": {"prepare_publication"},
    "cli": {"handle_task_add", "prepare_registered_task_context", "read_task_detail",
            "handle_task_checkpoint", "handle_task_edit", "handle_review_command",
            "handle_verification_receipt_add", "context_read_connection"},
    "completion_workflow": {"check_completion_request", "execute_completion_request"},
    "verification_runner_plan_edit": {"_execute_task_edit", "_edit_without_action",
                                      "_edit_plan_only", "_edit_combined", "_read_task_preflight"},
    "backup": {"_source_version", "_artifact_schema_version", "_discover_recovery_artifacts",
               "restore_managed_backup", "copy_database_snapshot", "_reconcile_v10", "_copy"},
    "backup_metadata_repository": {"_connect_managed_backup_repository", "record_backup_attempt_outcome"},
    "viewer_maintenance": {"_capture", "_refresh", "replace_guard"},
    "viewer_metadata_repository": {"record_viewer_publication", "record_viewer_attempt_outcome"},
    "evidence_publication": {"_capture", "_generation_guard"},
    "evidence_projection_metadata_repository": {"record_evidence_projection_outcome"},
    "usage_evidence_service": {"core_reader", "refresh_usage"},
    "review_wait_runtime.review_wait_repository": {"_connection"},
}
_ORIGIN_LABELS = frozenset(
    f"{module}.{function}" for module, functions in _ORIGINS.items() for function in functions
) | {"other"}
_current = ContextVar("sqlite_diagnostics", default=None)
_suppressed = ContextVar("sqlite_diagnostics_suppressed", default=False)


def suppressed():
    return _suppressed.get()


@contextmanager
def suppress_diagnostics():
    token = _suppressed.set(True)
    try:
        with bind_collector(None):
            yield
    finally:
        _suppressed.reset(token)


def safe_origin(module, function):
    """Reduce known package code to a closed label, without recording filenames."""
    try:
        if type(module) is not str or type(function) is not str:
            return "other"
        if not module.startswith("task_governance_tool."):
            return "other"
        module = module.removeprefix("task_governance_tool.")
        if function in _ORIGINS.get(module, ()):
            return f"{module}.{function}"
    except BaseException:
        pass
    return "other"


def current_collector():
    try:
        return _current.get()
    except BaseException:
        return None


def _integer(value, *, maximum=_MAX_INTEGER):
    return type(value) is int and 0 <= value <= maximum


def allow_output():
    """Use the operation's existing successful state/binding admission."""
    collector = current_collector()
    if collector is not None:
        collector.validated = True


class Collector:
    """One operation's bounded, sanitized memory buffer; no hot-path file I/O."""

    def __init__(self, directory, root, mode, *, operation, marker, directory_identity, record_sink=None):
        self.directory = directory
        self.root = root
        self.mode = mode
        self.operation = operation if type(operation) is str and operation in _OPERATIONS else "other"
        self.marker = marker
        self.directory_identity = directory_identity
        self.operation_id = uuid4().hex
        self.pid = os.getpid()
        self.thread_id = threading.get_ident()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started_ns = time.monotonic_ns()
        self.admission_ns = 0
        self.instrumentation_ns = 0
        self.operation_elapsed_ns = None
        self.records = []
        self.records_dropped = 0
        self.connections_registered = 0
        self._open = {}
        self._untracked = False
        self._failed = False
        self.record_sink = record_sink
        self.validated = False

    def register_connection(self, role, access, origin):
        """Return a call-local ID. Rejected/overflow tracking prevents output."""
        try:
            started = time.monotonic_ns()
            if (type(role) is not str or role not in _ROLES
                    or type(access) is not str or access not in _ACCESS
                    or len(self._open) >= MAX_CONNECTIONS
                    or threading.get_ident() != self.thread_id):
                self._untracked = True
                return 0
            origin = origin if type(origin) is str and origin in _ORIGIN_LABELS else "other"
            self.connections_registered += 1
            identity = self.connections_registered
            self._open[identity] = (role, access, origin)
            self.instrumentation_ns += time.monotonic_ns() - started
            return identity
        except BaseException:
            self._untracked = True
            return 0

    def closed(self, connection_id):
        """Notify only after the underlying connection has actually closed."""
        try:
            started = time.monotonic_ns()
            if _integer(connection_id):
                self._open.pop(connection_id, None)
            self.instrumentation_ns += time.monotonic_ns() - started
        except BaseException:
            self._untracked = True

    def add_instrumentation_ns(self, value):
        """Add observer overhead excluding time already counted by this collector."""
        try:
            if _integer(value):
                self.instrumentation_ns += value
        except BaseException:
            pass

    def mark_untracked(self):
        """An explicitly supplied native factory cannot prove close-before-flush."""
        self._untracked = True

    def event(self, connection_id, phase, outcome, elapsed_ns, *, transaction_id=None,
              transaction_elapsed_ns=None, sqlite_errorcode=None, origin=None,
              in_transaction=None, begin_mode=None, end_reason=None):
        """Accept closed labels and bounded primitives only; never stringify input."""
        started = None
        try:
            started = time.monotonic_ns()
            if (not _integer(connection_id) or connection_id not in self._open
                    or type(phase) is not str or phase not in _PHASES
                    or type(outcome) is not str or outcome not in _OUTCOMES
                    or not _integer(elapsed_ns)):
                self.records_dropped += 1
                return
            if outcome == "failed":
                self._failed = True
            if len(self.records) >= MAX_RECORDS:
                self.records_dropped += 1
                if outcome != "failed":
                    return
                # Late COMMIT failures must survive a buffer filled by earlier
                # successful readers. If all retained events failed, keep the
                # more recent failure without exceeding the cap.
                removable = next((index for index, item in enumerate(self.records)
                                  if item["outcome"] != "failed"), 0)
                self.records.pop(removable)
            role, access, registered_origin = self._open[connection_id]
            record = {
                "connection_id": connection_id, "role": role, "access": access,
                "origin": origin if type(origin) is str and origin in _ORIGIN_LABELS else registered_origin,
                "phase": phase, "outcome": outcome, "elapsed_ns": elapsed_ns,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "monotonic_ns": started,
            }
            for key, value in (("transaction_id", transaction_id),
                               ("transaction_elapsed_ns", transaction_elapsed_ns)):
                if _integer(value):
                    record[key] = value
            if _integer(sqlite_errorcode, maximum=65535):
                record["sqlite_errorcode"] = sqlite_errorcode
            if type(in_transaction) is bool:
                record["in_transaction"] = in_transaction
            if type(begin_mode) is str and begin_mode in _BEGIN_MODES:
                record["begin_mode"] = begin_mode
            if type(end_reason) is str and end_reason in _END_REASONS:
                record["end_reason"] = end_reason
            if self.record_sink is None:
                self.records.append(record)
            else:
                local = datetime.now().astimezone()
                record.update(observed_at=local.astimezone(timezone.utc).isoformat(),
                              log_date=local.date().isoformat(), utc_offset=local.strftime("%z"))
                self.record_sink(record)
        except BaseException:
            # A malformed diagnostic input must not replace a SQLite outcome.
            self.records_dropped += 1
        finally:
            try:
                if started is not None:
                    self.instrumentation_ns += time.monotonic_ns() - started
            except BaseException:
                pass

    def finish(self):
        """Best effort output, called only after context reset and resource exit."""
        try:
            self._finish()
        except BaseException:
            pass

    def _finish(self):
        if (not self.validated or self._open or self._untracked or not self.records
                or self.mode == "failures" and not self._failed):
            return
        from .state_paths import (inspect_physical_directory, read_physical_file_bounded,
                                  path_lexically_exists, create_exclusive_durable_file)
        from .project_scope import _state_is_ignored
        observed = inspect_physical_directory(self.directory, root=self.root)
        if observed.identity != self.directory_identity:
            return
        marker = read_physical_file_bounded(self.directory / "enabled", root=self.root, max_bytes=64)[0]
        if marker != self.marker:
            return
        # Source and installed packages share this exact state-root operand.
        # Recheck after the operation, while no registered connection is open.
        if not _state_is_ignored(self.root, "ordinary"):
            return
        document = {
            "format": "taskgov-sqlite-diagnostics-v1", "operation_id": self.operation_id,
            "operation": self.operation, "mode": self.mode, "pid": self.pid,
            "thread_id": self.thread_id, "started_at": self.started_at,
            "started_monotonic_ns": self.started_ns,
            "operation_elapsed_ns": self.operation_elapsed_ns,
            "admission_ns": self.admission_ns, "instrumentation_ns": self.instrumentation_ns,
            "connections_registered": self.connections_registered,
            "records_dropped": self.records_dropped, "failed": self._failed,
            "records": list(self.records),
        }
        while True:
            data = (json.dumps(document, separators=(",", ":")) + "\n").encode("utf-8")
            if len(data) <= MAX_BYTES:
                break
            if not document["records"]:
                return
            removable = next((index for index, item in enumerate(document["records"])
                              if item["outcome"] != "failed"), 0)
            document["records"].pop(removable)
            document["records_dropped"] += 1
        for slot in range(MAX_FILES):
            # No directory-wide inventory, replacement, rotation, or deletion.
            if inspect_physical_directory(self.directory, root=self.root).identity != self.directory_identity:
                return
            destination = self.directory / f"trace-{slot:03d}.json"
            if path_lexically_exists(destination):
                continue
            try:
                create_exclusive_durable_file(destination, data, root=self.root, max_bytes=MAX_BYTES)
            except Exception:
                # A concurrent process may have occupied this exact slot.
                if path_lexically_exists(destination):
                    continue
                return
            return


def _admit(repo, entrypoint, operation, *, repo_explicit):
    """Admit only the diagnostic target, without opening or resolving a DB."""
    from .state_resolver import canonical_state_paths
    from .storage import skill_root_from_script
    from .project_scope import inspect_project_scope, _state_is_ignored
    from .state_paths import inspect_physical_directory, read_physical_file_bounded

    # Absent opt-in avoids package/scope/ignore inspection and every write.
    candidate = canonical_state_paths(skill_root_from_script(entrypoint), repo=repo).sqlite_diagnostics
    if not (candidate / "enabled").exists():
        return None
    inspection = inspect_project_scope(repo=repo, repo_explicit=repo_explicit, script_path=entrypoint,
        include_runtime=False, include_package=False, include_ignore=False)
    if inspection.issues or inspection.scope is None:
        return None
    scope = inspection.scope
    paths = canonical_state_paths(scope.skill_root, repo=scope.canonical_repo)
    directory = paths.sqlite_diagnostics
    observed = inspect_physical_directory(directory, root=scope.canonical_repo)
    marker = read_physical_file_bounded(directory / "enabled", root=scope.canonical_repo, max_bytes=64)[0]
    mode = MODES.get(marker)
    if mode is None or not _state_is_ignored(scope.canonical_repo, scope.layout):
        return None
    return Collector(directory, scope.canonical_repo, mode, operation=operation,
                     marker=marker, directory_identity=observed.identity)


@contextmanager
def bind_collector(collector):
    """Share the existing observer with a separately admitted operation sink."""
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)


class diagnostic_scope:
    """Reuse nested collectors; never turn a logging failure into an operation failure."""
    def __init__(self, repo: Path, *, entrypoint: Path, operation: str, repo_explicit: bool = False,
                 validated: bool = False):
        self.repo, self.entrypoint, self.operation = repo, entrypoint, operation
        self.repo_explicit, self.validated = repo_explicit, validated
        self.collector = self.token = None

    def __enter__(self):
        if suppressed():
            return None
        existing = current_collector()
        if existing is not None:
            return existing
        try:
            if type(self.operation) is str and self.operation in _OPERATIONS:
                started = time.monotonic_ns()
                self.collector = _admit(Path(self.repo), Path(self.entrypoint), self.operation,
                                        repo_explicit=self.repo_explicit)
                if self.collector is not None:
                    self.collector.validated = self.validated
                    self.collector.admission_ns = time.monotonic_ns() - started
                    self.token = _current.set(self.collector)
        except BaseException:
            self.collector = None
        return self.collector

    def __exit__(self, error_type, error, traceback):
        if self.collector is not None and error is not None:
            self.collector._failed = True
        if self.token is not None:
            try:
                _current.reset(self.token)
            except BaseException:
                pass
        if self.collector is not None:
            try:
                self.collector.operation_elapsed_ns = time.monotonic_ns() - self.collector.started_ns
                self.collector.finish()
            except BaseException:
                pass
        return False
