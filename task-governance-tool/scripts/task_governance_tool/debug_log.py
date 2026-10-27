"""Small local diagnostic API: operation_scope, span, aggregate, record_exception.

Caller functions supply code-owned field names; runtime strings are values, not
labels. All events are protected before entering the finite operation buffer.
"""
from contextlib import ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
import threading
import time
from uuid import uuid4

from . import debug_log_values as projection
from . import sqlite_diagnostics as sqlite
from .debug_log_sink import admit
from .sqlite_sql_trace import Metrics, RESERVED_EVENTS
from .debug_log_metrics import Aggregates, ItemCount, STATES, RESERVED_EVENTS as PERFORMANCE_EVENTS

MAX_EVENTS = 256
MAX_BUFFER_BYTES = 512 * 1024
MAX_EVENT_BYTES = 16 * 1024
_current = ContextVar("debug_log", default=None)
_span = ContextVar("debug_log_span", default=None)
_measurement = ContextVar("debug_log_measurement", default=None)


def _moment():
    now = datetime.now().astimezone()
    return {"observed_at": now.astimezone(timezone.utc).isoformat(),
            "log_date": now.date().isoformat(), "utc_offset": now.strftime("%z")}


class Session:
    def __init__(self, sink, operation, *, validated):
        self.sink, self.validated = sink, validated
        self.invocation_id = uuid4().hex
        self.operation = operation if type(operation) is str and operation in sqlite._OPERATIONS else "other"
        self.mode = sink.mode
        self.events = []
        self.size = 0
        self.dropped = {}
        self.instrumentation_ns = 0
        self.collector = sqlite.Collector(sink.directory, sink.root, "transactions", operation=operation,
            marker=sink.marker, directory_identity=sink.identity, record_sink=self.db_event)
        self.collector.operation_id = self.invocation_id
        self.performance = Aggregates(self.mode, self.emit)
        self.collector.sql_metrics = Metrics(self.mode, self.emit, self.collector, context=self.sql_context)

    def sql_context(self):
        if _current.get() is not self:
            return None, None
        return _span.get(), _measurement.get()

    def drop(self, reason):
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def emit(self, kind, details, *, failed=False, moment=None, reserved=False):
        """Internal protected-record entry, not an arbitrary public message API."""
        started = time.perf_counter_ns()
        try:
            if self.mode == "errors" and not failed:
                return
            event = {"format": "taskgov-debug-v1", **(moment or _moment()),
                     "event": kind, "level": "error" if failed else "debug", "mode": self.mode,
                     "invocation_id": self.invocation_id, "operation": self.operation,
                     "pid": os.getpid(), "thread_id": threading.get_ident(), **details}
            data = (json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
            if len(data) > MAX_EVENT_BYTES:
                event.pop("args", None)
                event["truncated"] = True
                data = (json.dumps(event, separators=(",", ":")) + "\n").encode()
            if len(data) > MAX_EVENT_BYTES:
                self.drop("event_limit")
                return
            # Reserve the final summary's event slot as part of the same
            # operation budget, including when the detail buffer overflowed.
            limit = MAX_EVENTS if kind == "diagnostic.summary" else MAX_EVENTS - 1
            if kind != "diagnostic.summary" and not reserved:
                limit -= RESERVED_EVENTS + PERFORMANCE_EVENTS
            while len(self.events) >= limit or self.size + len(data) > MAX_BUFFER_BYTES:
                if not failed or not self.events:
                    self.drop("buffer_limit")
                    return
                index = next((i for i, item in enumerate(self.events) if not item[2]), 0)
                self.size -= len(self.events.pop(index)[1])
                self.drop("buffer_limit")
            self.events.append((event["log_date"], data, failed))
            self.size += len(data)
        except BaseException:
            self.drop("projection_failed")
        finally:
            self.instrumentation_ns += time.perf_counter_ns() - started

    def db_event(self, record):
        # Collector has already reduced every field to closed labels/numbers.
        moment = {key: record.pop(key) for key in ("observed_at", "log_date", "utc_offset")}
        self.emit("db.transaction", record, failed=record["outcome"] in {"failed", "cancelled"}, moment=moment)

    def finish(self):
        if sqlite.suppressed() or threading.get_ident() != self.collector.thread_id:
            self.collector.mark_untracked()
        allowed = self.validated and not self.collector._open and not self.collector._untracked
        self.collector.sql_metrics.finish(publish=allowed)
        self.performance.finish(publish=allowed)
        self.collector.sealed = True
        if not allowed:
            self.events.clear()
            return
        batches = {}
        for day, data, _ in self.events:
            batches.setdefault(day, bytearray()).extend(data)
        for day, data in batches.items():
            result = self.sink.write(day, bytes(data))
            if result != "written":
                self.drop(result)
        self.events.clear()
        self.size = 0
        if self.mode == "debug" or self.dropped:
            # This summary excludes its own construction/write and sink I/O.
            self.emit("diagnostic.summary", {"dropped": self.dropped.copy(),
                      "buffer_instrumentation_ns": self.instrumentation_ns,
                      "sqlite_instrumentation_ns": self.collector.instrumentation_ns,
                      "connections_registered": self.collector.connections_registered,
                      "db_records_dropped": self.collector.records_dropped}, failed=bool(self.dropped))
            for day, data, _ in self.events:
                self.sink.write(day, data)
            self.events.clear()


def allow_output():
    """Called by the existing CLI state-admission boundary, never by the sink."""
    sqlite.allow_output()
    session = _current.get()
    if session is not None:
        session.validated = True


class operation_scope:
    """Outer boundary; no enabling, DB admission, retries or public output.

    A class context manager does not read/write the primary exception's
    __traceback__ property, unlike contextlib's generator adapter.
    """
    def __init__(self, repo, *, entrypoint, operation, repo_explicit=False, validated=False, read_only=False,
                 independent=False):
        self.repo, self.entrypoint, self.operation = repo, entrypoint, operation
        self.repo_explicit, self.validated, self.read_only = repo_explicit, validated, read_only
        self.independent = independent
        self.session = self.collector = None
        self.stack = ExitStack()

    def __enter__(self):
        try:
            if self.read_only or sqlite.suppressed():
                token = _current.set(None)
                self.stack.callback(_current.reset, token)
                self.stack.enter_context(sqlite.suppress_diagnostics())
                return None
            if self.independent:
                token = _current.set(None)
                self.stack.callback(_current.reset, token)
                self.stack.enter_context(sqlite.bind_collector(None))
            if _current.get() is not None:
                return _current.get()
            sink = admit(self.repo, self.entrypoint, repo_explicit=self.repo_explicit)
            if sink is not None:
                self.session = Session(sink, self.operation, validated=self.validated)
                token = _current.set(self.session)
                self.stack.callback(_current.reset, token)
                for variable in (_span, _measurement):
                    token = variable.set(None)
                    self.stack.callback(variable.reset, token)
                self.stack.enter_context(sqlite.bind_collector(self.session.collector))
                return self.session
        except BaseException:
            self.session = None
            try:
                self.stack.close()
            except BaseException:
                pass
        try:
            self.collector = self.stack.enter_context(sqlite.diagnostic_scope(
                self.repo, entrypoint=self.entrypoint, operation=self.operation,
                repo_explicit=self.repo_explicit, validated=self.validated))
        except BaseException:
            pass
        return None

    def __exit__(self, error_type, error, traceback):
        try:
            if error is not None:
                if self.session is not None:
                    record_exception(error)
                if self.collector is not None:
                    self.collector._failed = True
        finally:
            try:
                # Cleanup needs no access to the primary exception object.
                self.stack.close()
            except BaseException:
                pass
            try:
                if self.session is not None:
                    self.session.finish()
            except BaseException:
                pass
        return False


class span:
    """Example: with span(load_task, task_id=task_id): ...

    Only the source function's declared parameters become fields. No wrapper,
    signature rewriting, automatic return-value capture or generator decorator.
    """
    def __init__(self, function, **arguments):
        self.function, self.arguments = function, arguments
        self.session = self.token = self.started = None
        self.details = {}
        self.counts = None

    def __enter__(self):
        self.session = _current.get()
        if self.session is None:
            self.function = self.arguments = None
            return self
        try:
            self.counts = ItemCount()
            identity, parent = uuid4().hex, _span.get()
            self.token = _span.set(identity)
            self.details = {"span_id": identity, "parent_span_id": parent, "method": "other"}
            self.details["method"] = projection.callable_metadata(self.function)[0]
            if self.session.mode == "debug":
                self.session.emit("method.start", {**self.details,
                    **projection.arguments(self.function, self.arguments)})
        except BaseException:
            pass
        # Do not retain caller values through the method body or in this handle.
        self.function = self.arguments = None
        try:
            self.started = time.perf_counter_ns()
        except BaseException:
            pass
        return self

    def count(self, value):
        """Add an already-known item count, including an explicitly known zero."""
        try:
            if self.counts is not None and _current.get() is self.session:
                self.counts.add(value)
        except BaseException:
            pass

    def __exit__(self, error_type, error, traceback):
        if self.session is None:
            return False
        try:
            if self.token is not None:
                _span.reset(self.token)
            ended = time.perf_counter_ns()
            timing = {"duration_ns": ended - self.started} if self.started is not None else {}
            outcome = 'succeeded' if error is None else 'failed' if issubclass(type(error), Exception) else 'cancelled'
            counts = self.counts.fields() if self.counts is not None else {}
            self.session.emit("method.end", {**self.details, "outcome": outcome, **timing, **counts},
                              failed=error is not None)
        except BaseException:
            pass
        return False


class aggregate:
    """Use around a repeated body; emit one aggregate per code location at exit.

    with aggregate(process_batch) as work:
        ...
        work.count(processed)  # no result iteration or query by the logger
    """
    def __init__(self, function, *, unit='items'):
        self.function = function
        self.unit, self.states = unit, None
        self.session = self.group = self.token = self.started = self.counts = None

    def __enter__(self):
        self.session = _current.get()
        if self.session is None:
            self.function = None
            self.unit = None
            return self
        try:
            self.counts = ItemCount()
            self.states = set()
            self.group = self.session.performance.begin(self.function, self.unit)
            self.token = _measurement.set(self.group['measurement_id'] if self.group else None)
            self.started = time.perf_counter_ns()
        except BaseException:
            self._failed_observation()
        finally:
            self.function = None
            self.unit = None
        return self

    count = span.count

    def mark(self, state):
        """Count a closed code-owned decision once per measured call."""
        try:
            if (self.states is not None and _current.get() is self.session
                    and type(state) is str and len(state) <= 32 and state in STATES):
                self.states.add(state)
        except BaseException:
            pass

    def _failed_observation(self):
        try:
            self.session.performance.bump('failures')
        except BaseException:
            pass

    def __exit__(self, error_type, error, traceback):
        if self.session is None:
            return False
        try:
            try:
                ended = time.perf_counter_ns()
                duration = ended - self.started if self.started is not None else None
            except BaseException:
                duration = None
                self._failed_observation()
            outcome = 'succeeded' if error is None else 'failed' if issubclass(type(error), Exception) else 'cancelled'
            self.session.performance.end(self.group, duration, outcome, self.counts or ItemCount(),
                                         self.states or ())
        except BaseException:
            self._failed_observation()
        finally:
            if self.token is not None:
                try:
                    _measurement.reset(self.token)
                except BaseException:
                    pass
        return False


def record_exception(error):
    """Call once at a handling boundary; spans only record their failure result."""
    session = _current.get()
    if session is None:
        return
    try:
        session.emit("exception", {"exception_id": uuid4().hex, "span_id": _span.get(),
                                   **projection.exception(error)}, failed=True)
    except BaseException:
        pass
