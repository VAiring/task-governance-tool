"""Lexical writer-local reuse of confirmed derived component registration.

This is not admission or a persisted validity flag. Unknown connections use
the original SQL path. Observation starts at admitted writer entry and shares
the existing trace multiplexer; nested scopes keep the same validity lifetime.
"""

from contextlib import contextmanager
import re
import sqlite3

from .sqlite_admission import AdmissionConnection


_DML = re.compile(r"\s*(INSERT(?: OR (?:IGNORE|REPLACE|ABORT|FAIL|ROLLBACK))? INTO|"
                  r"REPLACE INTO|UPDATE(?: OR (?:IGNORE|REPLACE|ABORT|FAIL|ROLLBACK))?|DELETE FROM)"
                  r"\s+([a-z_][a-z_0-9]*)\s", re.IGNORECASE | re.ASCII)
_COMPONENTS = {"usage_reconcile_components", "usage_reconcile_component_executions"}
# Native tracing expands bound strings. Admit only the complete two literal-
# VALUES shapes used by registration, never a prefix accepting UPSERT, SELECT,
# expressions, RETURNING or unfamiliar suffixes. Doubled quotes remain literals.
_STRING = r"'(?:[^']|'')*'"
_ADDITIVE = re.compile(
    r"\s*INSERT OR IGNORE INTO (?:usage_reconcile_components VALUES \("
    + _STRING + "," + _STRING + "," + _STRING
    + r"\)|usage_reconcile_component_executions VALUES \(" + _STRING + "," + _STRING
    + r"\))\s*;?\s*\Z", re.IGNORECASE | re.ASCII)
# These admitted schema-10 tables have no triggers, FKs or replacement effects
# on the two component tables. A DDL/unknown statement disables this allowance.
# Work registration/consumption is never memoized or omitted.
_OTHER_EFFECTS = {
    "usage_reconcile_response_result", "usage_reconcile_response_work",
    "usage_reconcile_component_work", "usage_reconcile_execution_work",
    "usage_reconcile_membership", "usage_reconcile_unknown", "usage_reconcile_gaps",
    "usage_reconcile_source_admissions", "usage_reconcile_diagnostic_work",
    "usage_reconcile_overlay_turn_work", "usage_reconcile_empty_components",
    "usage_reconcile_unattributed_keys", "usage_reconcile_unattributed_models",
}


class _Components:
    def __init__(self, connection):
        self.connection = connection
        self.observer = connection._admission_watch()
        self.enabled = True
        self.confirmed = set()
        self.observer.listeners.append(self.observe)

    def observe(self, sql):
        # Native control changes, transaction/savepoint transitions, DDL,
        # unfamiliar SQL and CTE writes revoke reuse for this lexical writer.
        # SELECT-side writes emit their own native trace and revoke it too.
        if sql is not None and re.match(r"\s*SELECT\b", sql[:4096], re.IGNORECASE):
            return
        match = _DML.match(sql[:4096]) if sql is not None else None
        if match:
            table = match[2].lower()
            if table in _OTHER_EFFECTS:
                return
            if table in _COMPONENTS:
                # Idempotent additive inserts cannot invalidate an already
                # confirmed row or required execution link. Every destructive
                # mutation, including retirement/reset, forgets all entries.
                if len(sql) > 4096 or _ADDITIVE.fullmatch(sql) is None:
                    self.confirmed = set()
                return
        self.confirmed = set()
        self.enabled = False

    def available(self):
        connection = self.connection
        if (not self.enabled or not connection.in_transaction
                or connection.row_factory is not sqlite3.Row or connection.text_factory is not str):
            self.confirmed = set()
            return False
        if connection._admission_watching is not self.observer:
            self.confirmed = set()
            self.enabled = False
            return False
        return True

    def close(self):
        self.enabled = False
        self.confirmed = set()
        if self.observer is not None:
            self.observer.listeners.remove(self.observe)
            self.connection._admission_unwatch(self.observer)


@contextmanager
def component_writer(connection):
    """Owned by the admitted repository writer; does not start/end a transaction."""
    if not isinstance(connection, AdmissionConnection) or not connection.in_transaction:
        yield
        return
    previous = getattr(connection, "_usage_components", None)
    if previous is not None:
        # Joining a revoked outer writer must never recertify its schema or
        # connection controls. The admitted owner alone starts a new lifetime.
        yield
        return
    state = _Components(connection)
    connection._usage_components = state
    try:
        yield
    finally:
        state.close()
        if getattr(connection, "_usage_components", None) is state:
            connection._usage_components = previous


def confirmed_components(connection):
    state = getattr(connection, "_usage_components", None)
    return state.confirmed if state is not None and state.available() else None
