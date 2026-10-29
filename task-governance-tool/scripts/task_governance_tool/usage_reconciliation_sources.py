"""Closed semantic source projections, independent of byte/cursor progress.

An offset change advances the freshness fence but need not invalidate every
Task ever observed in a source. Incarnations, thread diagnostics and pending
origins are semantic inputs; old values remain available until adoption.
"""

from dataclasses import dataclass
import json

from .usage_evidence import digest, encode
from .usage_incremental_repository import _document
from .usage_response_adapter import ResponseTracker
from .usage_values import UsageError


TABLES = (
    """CREATE TABLE usage_reconcile_sources (
        source_id TEXT PRIMARY KEY,document TEXT NOT NULL,digest TEXT NOT NULL)""",
)


@dataclass(frozen=True)
class SourceDelta:
    source_id: str
    before: dict | None
    after: dict | None

    @property
    def changed(self):
        return self.before != self.after

    @property
    def replaced(self):
        return (self.before is None or self.after is None or
                (self.before["thread"], self.before["incarnation"]) != (self.after["thread"], self.after["incarnation"]))


def _saved(row):
    if row is None:
        return None
    value = json.loads(row["document"])
    if (type(value) is not dict or set(value) != {"thread", "incarnation", "diagnostics", "pending"}
            or digest(value) != row["digest"] or encode(value).decode() != row["document"]):
        raise UsageError("usage_schema_invalid")
    return value


def source_deltas(connection, source_ids):
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    selected = tuple(sorted(set(source_ids)))
    if not selected:
        return ()
    if len(selected) > 128:
        raise UsageError("usage_schema_invalid")
    parameter = (encode(selected).decode(),)
    previous = {row["source_id"]: _saved(row) for row in connection.execute(
        "SELECT * FROM usage_reconcile_sources WHERE source_id IN (SELECT value FROM json_each(?))", parameter)}
    gaps = {}
    for row in connection.execute("SELECT source_id,code FROM usage_diagnostics WHERE source_id IN (SELECT value FROM json_each(?))", parameter):
        gaps.setdefault(row["source_id"], set()).add(row["code"])
    current = {}
    for row in connection.execute("""SELECT source.source_id,source.thread_id,source.incarnation,source.pending,
            context.document AS context,scan.document AS scan
        FROM usage_sources source LEFT JOIN usage_response_context context ON context.source_id=source.source_id AND context.lane='ingest'
        LEFT JOIN usage_scan_state scan ON scan.source_id=source.source_id AND scan.lane='ingest'
        WHERE source.source_id IN (SELECT value FROM json_each(?))""", parameter):
        diagnostics = set(gaps.get(row["source_id"], ()))
        if row["pending"] == "partial_tail":
            diagnostics.add("partial_tail")
        elif row["pending"] in {"unread", "more_records"}:
            diagnostics.add("collection_pending")
        elif row["pending"] != "none":
            raise UsageError("usage_schema_invalid")
        pending = {}
        if row["context"] is not None:
            tracker = ResponseTracker(row["thread_id"], "validation", json.loads(row["context"]))
            state = _document({"document": row["scan"]}) if row["scan"] is not None else None
            if state is None:
                raise UsageError("usage_schema_invalid")
            provider = state["state"].provider
            for frame in tracker.pending_frames():
                identity = frame["call"], frame["slot"], frame["turn"], frame["order"]
                origin = [frame["call"], frame["slot"], frame["turn"], frame["order"], provider,
                          None if frame["ambiguous"] else frame["response"]]
                old = pending.get(identity)
                if old is not None and old[-1] != origin[-1]:
                    origin[-1] = None
                pending[identity] = origin
        current[row["source_id"]] = {"thread": row["thread_id"], "incarnation": row["incarnation"],
                                    "diagnostics": sorted(diagnostics), "pending": sorted(pending.values(), key=encode)}
    return tuple(SourceDelta(identity, previous.get(identity), current.get(identity)) for identity in selected)


def adopt_sources(connection, deltas):
    """Call only after queuing/applying each old/new semantic effect, same writer."""
    if not connection.in_transaction:
        raise UsageError("usage_schema_invalid")
    for item in deltas:
        old = _saved(connection.execute("SELECT document,digest FROM usage_reconcile_sources WHERE source_id=?", (item.source_id,)).fetchone())
        if old != item.before:
            raise UsageError("cursor_stale")
        if item.after is None:
            connection.execute("DELETE FROM usage_reconcile_sources WHERE source_id=?", (item.source_id,))
        elif item.changed:
            connection.execute("INSERT INTO usage_reconcile_sources VALUES (?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                               "document=excluded.document,digest=excluded.digest",
                               (item.source_id, encode(item.after).decode(), digest(item.after)))
