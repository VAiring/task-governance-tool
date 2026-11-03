"""Private structural facts for one lexical, actual SQLite read epoch.

No domain verdict, physical audit or caller-supplied proof is cached. Unknown
connections and reads outside a transaction always validate afresh. Native
trace notifications observe transaction controls even through cursors, scripts
and implicit commits; SQL text is neither retained nor logged here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import re
import sqlite3

from .sqlite_file_access import GuardedConnection


_scope = ContextVar("structural_admission", default=None)
_retained_task_read = ContextVar("retained_task_read", default=None)
_READ_PRAGMA = re.compile(
    r"\s*PRAGMA\s+(?:(?:main|temp)\.)?(?:"
    r"(?:schema_version|query_only|foreign_keys|data_version|database_list)\s*;?\s*"
    r"|(?:table_info|table_xinfo|index_info|index_xinfo|index_list|foreign_key_list)\s*\([^;]*\)\s*;?\s*)\Z",
    re.IGNORECASE,
)
_CTE_WRITE = re.compile(r"\b(?:INSERT|REPLACE|UPDATE|DELETE)\b", re.IGNORECASE | re.ASCII)


def _preserves_retained_version(sql):
    if re.match(r"\s*(?:SAVEPOINT|RELEASE|ROLLBACK\s+(?:TRANSACTION\s+)?TO)\b", sql[:4096],
                re.IGNORECASE):
        return True
    # WITH can prefix SELECT or DML. Look at the entire bounded statement;
    # a keyword in a literal or comment may conservatively cost a fresh read.
    return (len(sql) <= 4096 and re.match(r"\s*WITH\b", sql, re.IGNORECASE) is not None
            and _CTE_WRITE.search(sql) is None)


def _invalidates_native_read(method):
    @wraps(method)
    def call(connection, /, *args, **kwargs):
        connection._expire_native_facts()
        try:
            return method(connection, *args, **kwargs)
        finally:
            # Argument conversion or a callback may have observed the old state.
            connection._expire_native_facts()
    return call


def _observed_factory(descriptor):
    # A cursor can inherit a transient factory even if a callback restores
    # the original connection setting before the repository read returns.
    return property(descriptor.__get__, _invalidates_native_read(descriptor.__set__),
                    _invalidates_native_read(descriptor.__delete__), descriptor.__doc__)


@dataclass(eq=False)
class _Observer:
    owners: int = 1
    epoch: int = 0
    basis: tuple | None = None
    listeners: list = field(default_factory=list)


@dataclass
class _StructuralFacts:
    observer: _Observer
    basis: tuple | None = None
    values: dict = field(default_factory=dict)


class AdmissionConnection(GuardedConnection):
    """Native connection with a temporary, multiplexed epoch observer."""

    _admission_watching = None
    _retained_version = None
    row_factory = _observed_factory(GuardedConnection.row_factory)
    text_factory = _observed_factory(GuardedConnection.text_factory)

    def set_trace_callback(self, trace_callback, /):
        # Keep the public native callback behavior, including replacement while
        # admission is active. Unsupported direct base-class calls are not used
        # by product code and cannot establish an admission scope.
        callback = self._admission_trace if getattr(self, "_admission_watching", False) else trace_callback
        result = super().set_trace_callback(callback)
        self._admission_user_trace = trace_callback
        return result

    def _admission_trace(self, sql):
        # Conservatively expire on every non-read statement, including comments
        # or unfamiliar syntax. This is an optimization classifier, not a SQL
        # permission check. SELECT-side user functions are also covered by the
        # actual transaction epoch, write count and schema-cookie observation.
        prefix = sql[:4096]
        for listener in tuple(self._admission_watching.listeners):
            listener(sql)
        if getattr(self, "_retained_version", None) is not None and not self.in_transaction:
            # A savepoint can start a new transaction after an implicit rollback.
            self._retained_version = None
        if not (re.match(r"\s*SELECT\b", prefix, re.IGNORECASE)
                or len(sql) <= 4096 and _READ_PRAGMA.fullmatch(prefix)):
            self._admission_watching.epoch += 1
            # This narrower fact belongs to the resolver's explicit, query-only
            # BEGIN. Read-only CTEs and nested savepoints can preserve it.
            # Any other boundary revokes it before forwarding a user callback.
            if (getattr(self, "_retained_version", None) is not None
                    and not _preserves_retained_version(sql)):
                self._retained_version = None
        callback = getattr(self, "_admission_user_trace", None)
        if callback is not None:
            callback(sql)

    def backup(self, target, *args, **kwargs):
        # SQLite's copy API does not emit SQL or increment target.total_changes.
        if isinstance(target, AdmissionConnection):
            target._expire_native_facts()
        try:
            return super().backup(target, *args, **kwargs)
        finally:
            # Argument conversion or a callback may start watching mid-call.
            if isinstance(target, AdmissionConnection):
                target._expire_native_facts()

    def _expire_native_facts(self):
        if self._admission_watching is not None:
            self._admission_watching.epoch += 1
            self._retained_version = None
            for listener in tuple(self._admission_watching.listeners):
                listener(None)

    @_invalidates_native_read
    def deserialize(self, *args, **kwargs):
        return super().deserialize(*args, **kwargs)

    # These change what a subsequent query may read, return or execute
    # without a SQL trace. Preserve each native call and its exceptions.
    @_invalidates_native_read
    def setconfig(self, *args, **kwargs):
        return super().setconfig(*args, **kwargs)

    set_authorizer = _invalidates_native_read(GuardedConnection.set_authorizer)
    setlimit = _invalidates_native_read(GuardedConnection.setlimit)
    set_progress_handler = _invalidates_native_read(GuardedConnection.set_progress_handler)
    create_collation = _invalidates_native_read(GuardedConnection.create_collation)
    create_function = _invalidates_native_read(GuardedConnection.create_function)
    create_aggregate = _invalidates_native_read(GuardedConnection.create_aggregate)

    if hasattr(GuardedConnection, "create_window_function"):
        create_window_function = _invalidates_native_read(GuardedConnection.create_window_function)

    def _admission_watch(self):
        observer = self._admission_watching
        if observer is None:
            observer = _Observer()
            super().set_trace_callback(self._admission_trace)
            self._admission_watching = observer
        else:
            observer.owners += 1
        return observer

    def _admission_unwatch(self, observer):
        if self._admission_watching is not observer:
            return
        observer.owners -= 1
        if observer.owners:
            return
        self._admission_watching = None
        try:
            super().set_trace_callback(getattr(self, "_admission_user_trace", None))
        except sqlite3.ProgrammingError:
            # The lexical owner may have closed its connection in finally.
            pass


@dataclass
class _RetainedVersion:
    connection: AdmissionConnection | None = None
    observer: _Observer | None = None
    changes: int = 0
    version: int | None = None


@contextmanager
def retained_task_read():
    """Allow one real resolver snapshot to lend only its migration version."""
    fact = _RetainedVersion()
    token = _retained_task_read.set(fact)
    try:
        yield
    finally:
        _retained_task_read.reset(token)
        if fact.connection is not None:
            if fact.connection._retained_version is fact:
                fact.connection._retained_version = None
            fact.connection._admission_unwatch(fact.observer)


def observe_retained_task_connection(connection):
    """Called immediately after the resolver's query-only opener starts BEGIN."""
    fact = _retained_task_read.get()
    if (fact is None or fact.connection is not None or not isinstance(connection, AdmissionConnection)
            or not connection.in_transaction):
        return
    fact.observer = connection._admission_watch()
    fact.connection = connection
    fact.changes = connection.total_changes
    connection._retained_version = fact
    # Share the structural observer's existing control probe. A writable or
    # unproven basis must never use the query-only CTE/savepoint rule.
    basis = _basis(connection, fact.observer)
    if basis is None or basis[3] != 1:
        connection._retained_version = None


def retained_version_fact(function):
    @wraps(function)
    def read(connection):
        fact = getattr(connection, "_retained_version", None)
        if fact is None or fact is not _retained_task_read.get() or fact.connection is not connection:
            return function(connection)
        if (not connection.in_transaction or connection.total_changes != fact.changes
                or connection.row_factory is not sqlite3.Row or connection.text_factory is not str):
            connection._retained_version = None
            return function(connection)
        if fact.version is None:
            result = function(connection)
            if connection._retained_version is fact:
                fact.version = result
            return result
        return fact.version
    return read


@contextmanager
def structural_read():
    """Lexical owner for synchronous calls or an admitted connection lifetime."""
    if _scope.get() is not None:
        yield
        return
    states = {}
    token = _scope.set(states)
    try:
        yield
    finally:
        _scope.reset(token)
        for connection, state in states.items():
            connection._admission_unwatch(state.observer)
        states.clear()


def admission_scope(function):
    """Compose structural readers synchronously; discard everything on exit."""
    @wraps(function)
    def scoped(*args, **kwargs):
        with structural_read():
            return function(*args, **kwargs)
    return scoped


def read_epoch(connection):
    """Return an observed read identity only while its lexical owner is active."""
    states = _scope.get()
    if states is None or connection not in states:
        return None
    observer = states[connection].observer
    basis = _basis(connection, observer)
    return (observer, basis) if basis is not None else None


def _basis(connection, observer):
    if connection._admission_watching is not observer or not connection.in_transaction:
        return None
    observed = (observer.epoch, connection.total_changes,
                connection.row_factory, connection.text_factory)
    cached = observer.basis
    if cached is not None and cached[0] == observed:
        return cached[1]
    # The native trace expires this observation for every non-read statement,
    # including temp DDL and PRAGMA controls. Probe once per observed epoch,
    # not before and after every structural fact or borrowed read.
    cookie = connection.execute("PRAGMA schema_version").fetchone()[0]
    temp_cookie = connection.execute("PRAGMA temp.schema_version").fetchone()[0]
    query_only = connection.execute("PRAGMA query_only").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    if (connection._admission_watching is not observer or not connection.in_transaction
            or observed != (observer.epoch,
            connection.total_changes, connection.row_factory, connection.text_factory)):
        return None
    basis = (observed, cookie, temp_cookie, query_only, foreign_keys)
    observer.basis = (observed, basis)
    return basis


def same_read_fact(function):
    """Memoize only repository-produced immutable structural return values."""
    @wraps(function)
    def read(connection, *args, **kwargs):
        states = _scope.get()
        if states is None or not isinstance(connection, AdmissionConnection):
            return function(connection, *args, **kwargs)
        state = states.get(connection)
        if state is None or state.observer is not connection._admission_watching:
            state = _StructuralFacts(connection._admission_watch())
            states[connection] = state
        before = _basis(connection, state.observer)
        if before is None or before != state.basis:
            state.basis = before
            state.values = {}
        facts = state.values
        key = (function, args, tuple(sorted(kwargs.items())))
        if before is not None and key in facts:
            return facts[key]
        result = function(connection, *args, **kwargs)
        if before is not None and before == _basis(connection, state.observer):
            facts[key] = result
        return result
    return read
