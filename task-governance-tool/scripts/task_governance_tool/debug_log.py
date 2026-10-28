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
from .debug_log_wait_sql import WaitSqlContext, CONTEXT_BYTES

MAX_EVENTS = 256
MAX_BUFFER_BYTES = 512 * 1024
MAX_EVENT_BYTES = 16 * 1024
WAIT_EVENTS = 64
WAIT_BYTES = 128 * 1024
WAIT_FLUSHES = 64
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
        self.closed = False
        self.events = []
        self.size = 0
        self.dropped = {}
        self.instrumentation_ns = 0
        self.wait_identity = None
        self.wait_observation = None
        self.wait_progress = None
        self.wait_parent = None
        self.wait_sql = None
        self.important = []
        self.important_size = 0
        self.wait_seen = set()
        self.wait_polls = 0
        self.wait_started = time.monotonic_ns()
        self.checkpoints = 0
        self.last_checkpoint = None
        self.previous_checkpoint = "not_attempted"
        self.collector = sqlite.Collector(sink.directory, sink.root, "transactions", operation=operation,
            marker=sink.marker, directory_identity=sink.identity, record_sink=self.db_event,
            closed_sink=self.db_closed)
        self.collector.operation_id = self.invocation_id
        self.performance = Aggregates(self.mode, self.emit)
        self.collector.sql_metrics = Metrics(self.mode, self.emit, self.collector, context=self.sql_context)

    def usable(self, *, active=False):
        """Reject borrowed mutable state without imposing a publication context.

        Finalization runs after ContextVar restoration and seals the SQLite
        collector before its last summary/checkpoint. Only live API handles
        require the current, unsealed scope; publication still checks ownership.
        """
        if threading.get_ident() != self.collector.thread_id or sqlite.suppressed():
            self.collector.mark_untracked()
            return False
        return (not self.closed and not self.collector._untracked
                and (not active or not self.collector.sealed and _current.get() is self))

    def sql_context(self):
        if not self.usable(active=True):
            return None, None
        return _span.get(), _measurement.get()

    def publishable(self):
        # Re-evaluate at dispatch, including after aggregate finalization. A
        # sharing violation can invalidate buffers since an earlier check.
        return self.usable() and self.validated and not self.collector._open

    def drop(self, reason):
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def emit(self, kind, details, *, failed=False, moment=None, reserved=False, important=False):
        """Internal protected-record entry, not an arbitrary public message API."""
        if not self.usable():
            return
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
            if important:
                # Separate finite transition/failure budgets. SQL/detail pressure
                # cannot evict these events; preserve the first caught failures.
                same_kind = sum(item[2] == failed for item in self.important)
                if (same_kind >= (16 if failed else 48)
                        or self.important_size + len(data) > WAIT_BYTES - CONTEXT_BYTES):
                    self.drop("important_limit")
                    return
                self.important.append((event["log_date"], data, failed))
                self.important_size += len(data)
                return
            # Reserve the final summary's event slot as part of the same
            # operation budget, including when the detail buffer overflowed.
            summary = kind in {"diagnostic.summary", "wait.checkpoint"}
            limit = MAX_EVENTS if summary else MAX_EVENTS - 1
            byte_limit = MAX_BUFFER_BYTES
            if self.wait_identity is not None:
                limit -= WAIT_EVENTS
                byte_limit -= WAIT_BYTES
            if not summary and not reserved:
                limit -= RESERVED_EVENTS + PERFORMANCE_EVENTS
            while len(self.events) >= limit or self.size + len(data) > byte_limit:
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
        if not self.usable():
            return
        moment = {key: record.pop(key) for key in ("observed_at", "log_date", "utc_offset")}
        if self.wait_sql is not None:
            self.wait_sql.observe({**record, **moment}, self.wait_identity)
        self.emit("db.transaction", record, failed=record["outcome"] in {"failed", "cancelled"}, moment=moment)

    def bind_wait(self, identity):
        """Internal entry: identity is already reduced by worker_diagnostics."""
        if not self.usable(active=True):
            return
        # A managed renewal stops one probe and starts another in one request.
        # Buffered events already own their identity; only future events change.
        # Budgets and aggregates stay invocation-local across these bindings.
        self.wait_identity = identity
        if self.wait_sql is None:
            self.wait_sql = WaitSqlContext(self.emit, self.drop)
        while (len(self.events) > MAX_EVENTS - WAIT_EVENTS - 1
               or self.size > MAX_BUFFER_BYTES - WAIT_BYTES):
            self.size -= len(self.events.pop()[1])
            self.drop("buffer_limit")

    def db_closed(self, connection_id):
        if not self.usable():
            return
        if self.wait_sql is not None:
            self.wait_sql.closed(connection_id)

    def checkpoint(self, *, urgent=False, final=False):
        """Bounded intermediate publication, only outside all DB lifetimes.

        No sealing/resetting SQL or method aggregates: their IDs and lifetime
        remain operation-local. Failed batches are discarded, never replayed.
        """
        if not self.usable() or self.wait_identity is None:
            return
        now = time.monotonic_ns()
        if not final and self.checkpoints >= WAIT_FLUSHES:
            self.drop("checkpoint_limit")
            return
        if (not final and not urgent and self.last_checkpoint is not None
                and now - self.last_checkpoint < 60_000_000_000):
            return
        if not self.publishable():
            return
        if not self.events and not self.important and not final:
            return
        self.checkpoints += 1
        self.last_checkpoint = now
        failed = (bool(self.dropped) or any(item[2] for item in self.events)
                  or any(item[2] for item in self.important))
        self.emit("wait.checkpoint", {**self.wait_identity,
            "checkpoint": self.checkpoints, "previous": self.previous_checkpoint,
            "scope_returned": final, "aggregate_scope": "invocation", "polls": self.wait_polls,
            "elapsed_ns": min(max(0, now - self.wait_started), 2**63 - 1),
            "dropped": self.dropped.copy(), "coverage": "partial",
            "buffer_instrumentation_ns": self.instrumentation_ns,
            "sqlite_instrumentation_ns": self.collector.instrumentation_ns,
            "connections_registered": self.collector.connections_registered,
            "db_records_dropped": self.collector.records_dropped,
            "last_observation": self.wait_observation,
            "worker_exit": "unknown"}, failed=failed, reserved=True)
        batches = {}
        for day, data, _ in [*self.important, *self.events]:
            batches.setdefault(day, bytearray()).extend(data)
        # Clear before sink entry: even a raised/partial append is never replayed.
        self.events.clear()
        self.important.clear()
        self.size = self.important_size = 0
        outcomes = []
        for day, data in batches.items():
            if not self.publishable():
                self.previous_checkpoint = "unknown"
                return
            try:
                result = self.sink.write(day, bytes(data))
            except BaseException:
                result = "unavailable"
            if type(result) is not str or result not in {
                    "written", "contended", "disabled", "invalid", "capacity", "changed", "unavailable"}:
                result = "unavailable"
            outcomes.append(result)
            if result != "written":
                self.drop(result)
        self.previous_checkpoint = ("written" if outcomes and all(v == "written" for v in outcomes)
                                    else "unknown")

    def finish(self):
        if self.closed:
            return
        # The scope has restored its caller's context. Sink admission itself
        # uses instrumented file readers; it must not borrow that caller's
        # collector (including a parent copied into an independent worker).
        token = _current.set(None)
        try:
            self._finish()
        finally:
            self.collector.sealed = self.closed = True
            self.events.clear()
            self.important.clear()
            self.size = self.important_size = 0
            _current.reset(token)

    def _finish(self):
        allowed = self.publishable()
        self.collector.sql_metrics.finish(publish=allowed)
        self.performance.finish(publish=allowed)
        self.collector.sealed = True
        if not allowed or not self.publishable():
            self.events.clear()
            self.important.clear()
            return
        if self.wait_identity is not None:
            self.checkpoint(final=True)
            return
        batches = {}
        for day, data, _ in self.events:
            batches.setdefault(day, bytearray()).extend(data)
        for day, data in batches.items():
            if not self.publishable():
                return
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
                if not self.publishable():
                    return
                self.sink.write(day, data)
            self.events.clear()


def allow_output():
    """Called by the existing CLI state-admission boundary, never by the sink."""
    session = _current.get()
    if session is None:
        sqlite.allow_output()
    elif session.usable(active=True):
        sqlite.allow_output()
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
        self.session = self.collector = self.borrowed = None
        self.thread_id = None
        self.depth = 0
        self.closed = False
        self.stack = ExitStack()
        self.rejected = ContextVar("debug_log_rejected_scope", default=None)

    def _owner(self):
        if self.thread_id is None or self.thread_id == threading.get_ident():
            return True
        session = self.session or self.borrowed
        collector = session.collector if session is not None else self.collector
        if collector is not None:
            collector.mark_untracked()
        return False

    def __enter__(self):
        if not self._owner() or self.closed:
            # Rejection still suppresses the body in the borrowing context.
            # Its cleanup must never replace the original owner's stack.
            stack = ExitStack()
            try:
                token = _current.set(None)
                stack.callback(_current.reset, token)
                stack.enter_context(sqlite.suppress_diagnostics())
                token = self.rejected.set(None)
                self.rejected.set((token, stack, threading.get_ident()))
            except BaseException:
                try:
                    stack.close()
                except BaseException:
                    pass
            return None
        try:
            if self.depth:
                # Same-object nesting adds no context to replace or pop.
                self.depth += 1
                session = self.session or self.borrowed
                return session if (not self.closed and session is not None
                                   and session.usable(active=True)) else None
            self.thread_id = threading.get_ident()
            self.depth = 1
            if self.read_only or sqlite.suppressed():
                token = _current.set(None)
                self.stack.callback(_current.reset, token)
                self.stack.enter_context(sqlite.suppress_diagnostics())
                return None
            if self.independent:
                token = _current.set(None)
                self.stack.callback(_current.reset, token)
                self.stack.enter_context(sqlite.bind_collector(None))
            existing = _current.get()
            if existing is not None:
                self.borrowed = existing
                return existing if existing.usable(active=True) else None
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
        rejected = self.rejected.get()
        if rejected is not None:
            token, stack, owner = rejected
            try:
                # Reset first: a copied Context must not consume another
                # entry's cleanup, even when it runs on the same thread.
                if owner == threading.get_ident():
                    self.rejected.reset(token)
                    stack.close()
            except BaseException:
                pass
            return False
        if not self._owner() or not self.depth:
            return False
        self.depth -= 1
        if self.depth:
            return False
        self.closed = True
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
        self.closed = False
        self.entered = False

    def _enter_once(self):
        # Inspect the original owner before consulting a new ambient context.
        # A repeated active handle is ambiguous; suppress its unsaved scope.
        if self.entered or self.closed:
            if self.session is not None:
                self.session.usable(active=True)
                if not self.closed:
                    self.session.collector.mark_untracked()
            return False
        self.entered = True
        return True

    def __enter__(self):
        if not self._enter_once():
            return self
        self.session = _current.get()
        if self.session is None or not self.session.usable(active=True):
            self.session = None
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
            if (self.session is not None and self.session.usable(active=True)
                    and not self.closed and self.counts is not None):
                self.counts.add(value)
        except BaseException:
            pass

    def __exit__(self, error_type, error, traceback):
        if self.session is None:
            self.closed = True
            return False
        try:
            if not self.session.usable(active=True) or self.closed:
                return False
            ended = time.perf_counter_ns()
            timing = {"duration_ns": ended - self.started} if self.started is not None else {}
            outcome = 'succeeded' if error is None else 'failed' if issubclass(type(error), Exception) else 'cancelled'
            counts = self.counts.fields() if self.counts is not None else {}
            self.session.emit("method.end", {**self.details, "outcome": outcome, **timing, **counts},
                              failed=error is not None)
        except BaseException:
            pass
        finally:
            self.closed = True
            if self.token is not None:
                try:
                    _span.reset(self.token)
                    self.token = None
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
        self.closed = False
        self.entered = False

    _enter_once = span._enter_once

    def __enter__(self):
        if not self._enter_once():
            return self
        self.session = _current.get()
        if self.session is None or not self.session.usable(active=True):
            self.session = None
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
            if (self.session is not None and self.session.usable(active=True)
                    and not self.closed and self.states is not None
                    and type(state) is str and len(state) <= 32 and state in STATES):
                self.states.add(state)
        except BaseException:
            pass

    def _failed_observation(self):
        try:
            if self.session.usable(active=True):
                self.session.performance.bump('failures')
        except BaseException:
            pass

    def __exit__(self, error_type, error, traceback):
        if self.session is None:
            self.closed = True
            return False
        try:
            if not self.session.usable(active=True) or self.closed:
                return False
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
            self.closed = True
            if self.token is not None:
                try:
                    _measurement.reset(self.token)
                    self.token = None
                except BaseException:
                    pass
        return False


def record_exception(error):
    """Call once at a handling boundary; spans only record their failure result."""
    session = _current.get()
    if session is None or not session.usable(active=True):
        return
    try:
        session.emit("exception", {"exception_id": uuid4().hex, "span_id": _span.get(),
                                   **projection.exception(error)}, failed=True)
    except BaseException:
        pass
