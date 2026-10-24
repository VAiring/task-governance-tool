"""Opt-in observation of SQLite transaction boundaries, without SQL retention.

Native SQLite methods retain transaction, exception and context-manager
semantics. Durations describe observed transactions, not exact lock ownership.
Script internals and implicit boundaries inside one script remain aggregated.
"""
from __future__ import annotations

from pathlib import Path, PosixPath, WindowsPath, PurePath, PurePosixPath, PureWindowsPath
import sqlite3
import sys
from time import perf_counter_ns

from .debug_log_values import plain_fields


_ROLES = frozenset({"core", "numerical", "operational", "backup", "other"})
_ACCESS = frozenset({"readonly", "readwrite", "create"})
_INFRASTRUCTURE = frozenset({"task_governance_tool.sqlite_connection",
                            "task_governance_tool.sqlite_transaction_trace"})


def _role(database):
    # Classify known filenames only. Neither the input nor its path is retained.
    if any(type(database) is kind for kind in (Path, PosixPath, WindowsPath, PurePath, PurePosixPath, PureWindowsPath)):
        name = database.name
    elif type(database) is str:
        name = database.split("?", 1)[0].replace("\\", "/").rsplit("/", 1)[-1]
    else:
        return "other"
    return {"taskgov.sqlite": "core", "taskgov-usage.sqlite": "numerical",
            "taskgov-review-wait.sqlite": "operational"}.get(name, "other")


def _origin():
    """Return only a sink-approved source label; never retain a stack/frame."""
    frame = None
    fallback = "other"
    try:
        from task_governance_tool.sqlite_diagnostics import safe_origin
        frame = sys._getframe(1)
        for _ in range(16):
            if frame is None:
                break
            fields = plain_fields(frame.f_globals)
            module = fields.get("__name__") if fields is not None else None
            if type(module) is str and module not in _INFRASTRUCTURE:
                label = safe_origin(module, frame.f_code.co_name)
                if label != "other":
                    if module == "task_governance_tool.storage":
                        fallback = label
                    else:
                        return label
            frame = frame.f_back
    except BaseException:
        pass
    finally:
        del frame
    return fallback


def _control(arguments, keywords):
    """Inspect only bounded control tokens, with no SQL text in any record."""
    try:
        fields = plain_fields(keywords) if not arguments else None
        sql = arguments[0] if arguments else fields.get("sql") if fields is not None else None
        if type(sql) is not str:
            return "statement", None
        tokens = sql[:80].strip().rstrip(";").upper().split()
        if not tokens:
            return "statement", None
        if tokens[0] == "BEGIN":
            mode = tokens[1].lower() if len(tokens) > 1 else "deferred"
            return "begin", mode if mode in {"immediate", "exclusive"} else "deferred"
        if tokens[0] in {"COMMIT", "END"}:
            return "commit", None
        if tokens[0] == "ROLLBACK" and "TO" not in tokens[1:3]:
            return "rollback", None
    except BaseException:
        pass
    return "statement", None


def _numeric_error(error):
    try:
        # Driver errors store this numeric field in the native exception dict.
        # Attribute lookup could execute a property on an adapter's exception.
        fields = plain_fields(BaseException.__dict__["__dict__"].__get__(error))
        value = fields.get("sqlite_errorcode") if fields is not None else None
        return value if type(value) is int and 0 <= value <= 65535 else None
    except BaseException:
        return None


def _overhead(collector, started, native_elapsed, previous):
    try:
        # Collector methods already account for their own record costs.
        recorded = max(0, collector.instrumentation_ns - previous)
        collector.add_instrumentation_ns(max(0, perf_counter_ns() - started - native_elapsed - recorded))
    except BaseException:
        pass


class _ObservedCursor(sqlite3.Cursor):
    def execute(self, *args, **kwargs):
        native = super().execute
        return self.connection._observe(lambda: native(*args, **kwargs), None, sql=(args, kwargs))

    def executemany(self, *args, **kwargs):
        native = super().executemany
        return self.connection._observe(lambda: native(*args, **kwargs), None, sql=(args, kwargs))

    def executescript(self, *args, **kwargs):
        native = super().executescript
        return self.connection._observe(lambda: native(*args, **kwargs), "script")


class _ObservedConnection(sqlite3.Connection):
    def _bind_observer(self, collector, connection_id):
        self._diagnostic_collector = collector
        self._diagnostic_id = connection_id
        self._diagnostic_transaction = None
        self._diagnostic_started = None
        self._diagnostic_sequence = 0
        self._diagnostic_closed = False

    def _active(self):
        try:
            return self.in_transaction
        except BaseException:
            return None

    def _event(self, phase, outcome, elapsed, *, error=None, mode=None, reason=None,
               active=None, transaction_elapsed=None):
        try:
            collector = getattr(self, "_diagnostic_collector", None)
            if collector is None:
                return
            collector.event(self._diagnostic_id, phase, outcome, max(0, elapsed),
                transaction_id=self._diagnostic_transaction,
                transaction_elapsed_ns=transaction_elapsed,
                sqlite_errorcode=_numeric_error(error), origin=_origin(),
                in_transaction=active, begin_mode=mode, end_reason=reason)
        except BaseException:
            pass

    def _after(self, phase, before, started, ended, error, mode, context_exit):
        """Diagnostic-only work; callers suppress failures in this observer."""
        after = False if phase == "close" and error is None else self._active()
        if phase == "script" and before is True and after is True:
            # A script may commit and begin again internally. Do not report a
            # continuous measured lifetime across that unobservable boundary.
            self._diagnostic_started = None
        if after is True and self._diagnostic_transaction is None:
            self._diagnostic_sequence += 1
            self._diagnostic_transaction = self._diagnostic_sequence
            # Successful BEGIN/implicit-DML return is the observed start.
            self._diagnostic_started = ended
            if phase != "begin":
                self._event("implicit_begin", "observed", 0, active=after)
        elapsed = ended - started
        duration = (max(0, ended - self._diagnostic_started)
                    if self._diagnostic_started is not None else None)
        if phase != "statement" or error is not None:
            self._event(phase, "failed" if error is not None else "succeeded", elapsed,
                        error=error, mode=mode, active=after, transaction_elapsed=duration)
        if context_exit and phase != "context_exit":
            self._event("context_exit", "failed" if error is not None else "succeeded", elapsed,
                        error=error, active=after, transaction_elapsed=duration)
        if self._diagnostic_transaction is not None and after is False:
            reason = ("ended_after_commit_failure" if context_exit and phase == "commit" and error is not None
                      else phase if phase in {"commit", "rollback", "close", "script"} else "observed")
            self._event("transaction_end", "observed", 0, reason=reason,
                        active=False, transaction_elapsed=duration)
            self._diagnostic_transaction = None
            self._diagnostic_started = None
        if phase == "close" and error is None and not self._diagnostic_closed:
            self._diagnostic_closed = True
            self._diagnostic_collector.closed(self._diagnostic_id)

    def _observe(self, native, phase, *, mode=None, context_exit=False, sql=None):
        collector = getattr(self, "_diagnostic_collector", None)
        if collector is None:
            return native()
        try:
            overhead_started = perf_counter_ns()
            previous_overhead = collector.instrumentation_ns
            before = self._active()
            if sql is not None:
                phase, mode = _control(*sql)
            started = perf_counter_ns()
        except BaseException:
            return native()
        try:
            result = native()
        except BaseException as error:
            try:
                ended = perf_counter_ns()
                self._after(phase, before, started, ended, error, mode, context_exit)
            except BaseException:
                pass
            else:
                _overhead(collector, overhead_started, ended - started, previous_overhead)
            raise
        try:
            ended = perf_counter_ns()
            self._after(phase, before, started, ended, None, mode, context_exit)
        except BaseException:
            pass
        else:
            _overhead(collector, overhead_started, ended - started, previous_overhead)
        return result

    def execute(self, *args, **kwargs):
        native = super().execute
        return self._observe(lambda: native(*args, **kwargs), None, sql=(args, kwargs))

    def executemany(self, *args, **kwargs):
        native = super().executemany
        return self._observe(lambda: native(*args, **kwargs), None, sql=(args, kwargs))

    def executescript(self, *args, **kwargs):
        native = super().executescript
        return self._observe(lambda: native(*args, **kwargs), "script")

    def cursor(self, *args, **kwargs):
        if not args and not kwargs:
            return super().cursor(factory=_ObservedCursor)
        return super().cursor(*args, **kwargs)

    def commit(self):
        return self._observe(super().commit, "commit")

    def rollback(self):
        return self._observe(super().rollback, "rollback")

    def close(self):
        return self._observe(super().close, "close")

    def __exit__(self, *args):
        phase = ("commit" if args and args[0] is None else "rollback") if self._active() else "context_exit"
        native = super().__exit__
        return self._observe(lambda: native(*args), phase, context_exit=True)


def connect_traced(database, *args, access="readwrite", role=None, **kwargs):
    """Use the native opener unchanged when no diagnostic scope is active.

    A supplied SQLite factory remains authoritative and is not instrumented.
    Shared operational openers do not supply one. No sink I/O occurs here.
    """
    collector, connection_id = None, 0
    try:
        overhead_started = perf_counter_ns()
        from task_governance_tool.sqlite_diagnostics import current_collector
        collector = current_collector()
        previous_overhead = collector.instrumentation_ns if collector is not None else 0
        if collector is not None:
            fields = plain_fields(kwargs)
            if fields is None or "factory" in fields or len(args) >= 5:
                collector.mark_untracked()
            else:
                connection_id = collector.register_connection(
                    role if type(role) is str and role in _ROLES else _role(database),
                    access if type(access) is str and access in _ACCESS else "readwrite", _origin())
    except BaseException:
        collector, connection_id = None, 0
    if collector is None or not connection_id:
        return sqlite3.connect(database, *args, **kwargs)
    try:
        started = perf_counter_ns()
    except BaseException:
        # Retain the outstanding registration, suppressing any partial flush.
        return sqlite3.connect(database, *args, **kwargs)
    try:
        connection = sqlite3.connect(database, *args, factory=_ObservedConnection, **kwargs)
    except BaseException as error:
        try:
            ended = perf_counter_ns()
            collector.event(connection_id, "open", "failed", ended - started,
                            sqlite_errorcode=_numeric_error(error), origin=_origin())
            collector.closed(connection_id)
            _overhead(collector, overhead_started, ended - started, previous_overhead)
        except BaseException:
            pass
        raise
    try:
        ended = perf_counter_ns()
        connection._bind_observer(collector, connection_id)
        collector.event(connection_id, "open", "succeeded", ended - started,
                        origin=_origin(), in_transaction=connection.in_transaction)
        _overhead(collector, overhead_started, ended - started, previous_overhead)
    except BaseException:
        # Failure to attach diagnostics never closes a successfully opened DB.
        pass
    return connection
