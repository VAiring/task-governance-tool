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
from . import sqlite_sql_trace as sql_trace
from .sqlite_file_access import GuardedConnection, connect_guarded


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
        # Read at most three control words, treating comments between them as
        # whitespace too. An incomplete/budget-exhausted prefix stays unknown.
        prefix, position, tokens = sql[:4096], 0, []
        while len(tokens) < 3:
            while position < len(prefix):
                if prefix[position].isspace() or (not tokens and prefix[position] == ";"):
                    position += 1
                elif prefix.startswith("--", position):
                    end = prefix.find("\n", position + 2)
                    if end < 0:
                        if len(sql) > len(prefix):
                            return "statement", None
                        position = len(prefix)
                    else:
                        position = end + 1
                elif prefix.startswith("/*", position):
                    end = prefix.find("*/", position + 2)
                    if end < 0:
                        return "statement", None
                    position = end + 2
                else:
                    break
            start = position
            while position < len(prefix) and ("a" <= prefix[position] <= "z" or "A" <= prefix[position] <= "Z"):
                position += 1
            if position == len(prefix) and len(sql) > len(prefix):
                return "statement", None
            if position == start:
                break
            tokens.append(prefix[start:position].upper())
        if not tokens:
            return "statement", None
        if tokens[0] == "BEGIN":
            mode = tokens[1].lower() if len(tokens) > 1 else "deferred"
            return "begin", mode if mode in {"immediate", "exclusive"} else "deferred"
        if tokens[0] in {"COMMIT", "END"}:
            return "commit", None
        if tokens[0] == "ROLLBACK":
            return ("rollback_to" if "TO" in tokens[1:3] else "rollback"), None
        if tokens[0] in {"SAVEPOINT", "RELEASE"}:
            return tokens[0].lower(), None
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


_ObservedCursor = sql_trace._ObservedCursor


class _ObservedConnection(GuardedConnection):
    def _bind_observer(self, collector, connection_id, *, converters_possible=True):
        self._diagnostic_collector = collector
        self._diagnostic_id = connection_id
        self._diagnostic_transaction = None
        self._diagnostic_started = None
        self._diagnostic_sequence = 0
        self._diagnostic_closed = False
        self._diagnostic_converters_possible = converters_possible

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
                in_transaction=active, begin_mode=mode, end_reason=reason,
                lifetime=("observed" if self._diagnostic_started is not None else "unknown")
                         if self._diagnostic_transaction is not None else None)
        except BaseException:
            pass

    def _after(self, phase, before, started, ended, error, mode, context_exit, *, native_restart=False):
        """Diagnostic-only work; callers suppress failures in this observer."""
        after = False if phase == "close" and error is None else self._active()
        # In autocommit=False the native commit/rollback immediately opens a
        # fresh transaction. True -> True is not a continuous lifetime.
        restarted = (native_restart and before is True and after is True and error is None
                     and sqlite3.Connection.autocommit.__get__(self) is False
                     and phase in {"commit", "rollback"})
        if phase == "script":
            self._diagnostic_started = None
        if context_exit and error is not None and before is True and after is True:
            self._diagnostic_started = None
        if after is True and self._diagnostic_transaction is None:
            self._diagnostic_sequence += 1
            self._diagnostic_transaction = self._diagnostic_sequence
            # Successful BEGIN/implicit-DML return is the observed start.
            self._diagnostic_started = None if phase == "script" else ended
            if phase != "begin":
                self._event("implicit_begin", "observed", 0, active=after)
        if phase == "script" and after is True:
            # Internal BEGIN/COMMIT/restart is not observable. Even a newly
            # discovered transaction has unknown start, including failed scripts.
            self._diagnostic_started = None
        elapsed = ended - started
        duration = (max(0, ended - self._diagnostic_started)
                    if self._diagnostic_started is not None else None)
        if phase != "statement" or error is not None:
            outcome = ("succeeded" if error is None else "failed"
                       if issubclass(type(error), Exception) else "cancelled")
            self._event(phase, outcome, elapsed,
                        error=error, mode=mode, active=after, transaction_elapsed=duration)
        if context_exit and phase != "context_exit":
            self._event("context_exit", "failed" if error is not None else "succeeded", elapsed,
                        error=error, active=after, transaction_elapsed=duration)
        if self._diagnostic_transaction is not None and (after is False or restarted):
            reason = ("ended_after_commit_failure" if context_exit and phase == "commit" and error is not None
                      else phase if phase in {"commit", "rollback", "close", "script", "release"} else "observed")
            self._event("transaction_end", "observed", 0, reason=reason,
                        active=False, transaction_elapsed=duration)
            self._diagnostic_transaction = None
            self._diagnostic_started = None
        if restarted:
            self._diagnostic_sequence += 1
            self._diagnostic_transaction = self._diagnostic_sequence
            self._diagnostic_started = ended
            self._event("implicit_begin", "observed", 0, active=True)
        if phase == "close" and error is None and not self._diagnostic_closed:
            self._diagnostic_closed = True
            self._diagnostic_collector.closed(self._diagnostic_id)

    def _observe(self, native, phase, *, mode=None, context_exit=False, sql=None,
                 transaction_method=False, observed=None):
        collector = sql_trace.observer_collector(self)
        if collector is None:
            return native()
        try:
            overhead_started = perf_counter_ns()
            previous_overhead = collector.instrumentation_ns
            before = self._active()
            native_restart = transaction_method and sqlite3.Connection.autocommit.__get__(self) is False
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
                self._after(phase, before, started, ended, error, mode, context_exit,
                            native_restart=native_restart)
            except BaseException:
                pass
            else:
                sql_trace.safe(observed, None, error, started, ended)
                _overhead(collector, overhead_started, ended - started, previous_overhead)
            raise
        try:
            ended = perf_counter_ns()
            self._after(phase, before, started, ended, None, mode, context_exit,
                        native_restart=native_restart)
        except BaseException:
            pass
        else:
            sql_trace.safe(observed, result, None, started, ended)
            _overhead(collector, overhead_started, ended - started, previous_overhead)
        return result

    def execute(self, *args, **kwargs):
        return self._remember_cursor(sql_trace.shortcut(self, 'execute', args, kwargs))

    def executemany(self, *args, **kwargs):
        return self._remember_cursor(sql_trace.shortcut(self, 'executemany', args, kwargs))

    def executescript(self, *args, **kwargs):
        return self._remember_cursor(sql_trace.shortcut(self, 'executescript', args, kwargs))

    def cursor(self, *args, **kwargs):
        sql_trace.observer_collector(self)
        if not args and not kwargs:
            return super().cursor(factory=_ObservedCursor)
        sql_trace.custom_cursor(self)
        return super().cursor(*args, **kwargs)

    def commit(self):
        return self._observe(super().commit, "commit", transaction_method=True)

    def rollback(self):
        return self._observe(super().rollback, "rollback", transaction_method=True)

    def backup(self, *args, **kwargs):
        native = super().backup
        return self._observe(lambda: native(*args, **kwargs), "backup")

    def close(self):
        result = self._observe(super().close, "close")
        sql_trace.connection_closed(self)
        return result

    def __exit__(self, *args):
        phase = ("commit" if args and args[0] is None else "rollback") if self._active() else "context_exit"
        native = super().__exit__
        return self._observe(lambda: native(*args), phase, context_exit=True, transaction_method=True)


def connect_traced(database, *args, access="readwrite", role=None, file_guard_path=None, **kwargs):
    """Keep product file lifetime protection independent of diagnostic opt-in."""
    return connect_guarded(file_guard_path, lambda guarded: _connect_traced(
        database, *args, access=access, role=role, guarded=guarded, **kwargs))


def _connect_traced(database, *args, access="readwrite", role=None, guarded=False, **kwargs):
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
        return sqlite3.connect(database, *args, **({"factory": GuardedConnection} if guarded else {}), **kwargs)
    try:
        started = perf_counter_ns()
    except BaseException:
        # Retain the outstanding registration, suppressing any partial flush.
        return sqlite3.connect(database, *args, **({"factory": GuardedConnection} if guarded else {}), **kwargs)
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
        detect_types = args[1] if len(args) > 1 else fields.get('detect_types', 0)
        connection._bind_observer(collector, connection_id,
                                  converters_possible=type(detect_types) is not int or detect_types != 0)
        connection._after("open", False, started, ended, None, None, False)
        _overhead(collector, overhead_started, ended - started, previous_overhead)
    except BaseException:
        # Failure to attach diagnostics never closes a successfully opened DB.
        pass
    return connection
