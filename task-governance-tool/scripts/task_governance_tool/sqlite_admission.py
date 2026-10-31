"""Private structural facts for one lexical, actual SQLite read epoch.

No domain verdict, physical audit or caller-supplied proof is cached. Unknown
connections and reads outside a transaction always validate afresh. Native
trace notifications observe transaction controls even through cursors, scripts
and implicit commits; SQL text is neither retained nor logged here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import re
import sqlite3

from .sqlite_file_access import GuardedConnection


_scope = ContextVar("structural_admission", default=None)
_READ_PRAGMA = re.compile(
    r"\s*PRAGMA\s+(?:(?:main|temp)\.)?(?:"
    r"(?:schema_version|query_only|foreign_keys|data_version|database_list)\s*;?\s*"
    r"|(?:table_info|table_xinfo|index_info|index_xinfo|index_list|foreign_key_list)\s*\([^;]*\)\s*;?\s*)\Z",
    re.IGNORECASE,
)


class AdmissionConnection(GuardedConnection):
    """Native connection with a temporary, multiplexed epoch observer."""

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
        if not (re.match(r"\s*SELECT\b", prefix, re.IGNORECASE)
                or len(sql) <= 4096 and _READ_PRAGMA.fullmatch(prefix)):
            self._admission_epoch += 1
        callback = getattr(self, "_admission_user_trace", None)
        if callback is not None:
            callback(sql)

    def backup(self, target, *args, **kwargs):
        # SQLite's copy API does not emit SQL or increment target.total_changes.
        if isinstance(target, AdmissionConnection) and getattr(target, "_admission_watching", False):
            target._admission_epoch += 1
        return super().backup(target, *args, **kwargs)

    def deserialize(self, *args, **kwargs):
        if getattr(self, "_admission_watching", False):
            self._admission_epoch += 1
        return super().deserialize(*args, **kwargs)

    def _admission_watch(self):
        self._admission_epoch = 0
        super().set_trace_callback(self._admission_trace)
        self._admission_watching = True

    def _admission_unwatch(self):
        self._admission_watching = False
        try:
            super().set_trace_callback(getattr(self, "_admission_user_trace", None))
        except sqlite3.ProgrammingError:
            # The lexical owner may have closed its connection in finally.
            pass


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
        for connection in states:
            connection._admission_unwatch()
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
    if (states is None or connection not in states
            or not getattr(connection, "_admission_watching", False)):
        return None
    return _basis(connection)


def _basis(connection):
    if not connection.in_transaction:
        return None
    cookie = connection.execute("PRAGMA schema_version").fetchone()[0]
    query_only = connection.execute("PRAGMA query_only").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    return (connection._admission_epoch, connection.total_changes, cookie,
            query_only, foreign_keys, connection.row_factory)


def same_read_fact(function):
    """Memoize only repository-produced immutable structural return values."""
    @wraps(function)
    def read(connection, *args, **kwargs):
        states = _scope.get()
        if states is None or not isinstance(connection, AdmissionConnection):
            return function(connection, *args, **kwargs)
        if connection not in states:
            connection._admission_watch()
            states[connection] = (None, {})
        before = _basis(connection)
        previous, facts = states[connection]
        if before is None or before != previous:
            facts = {}
            states[connection] = (before, facts)
        key = (function, args, tuple(sorted(kwargs.items())))
        if before is not None and key in facts:
            return facts[key]
        result = function(connection, *args, **kwargs)
        if before is not None and before == _basis(connection):
            facts[key] = result
        return result
    return read
