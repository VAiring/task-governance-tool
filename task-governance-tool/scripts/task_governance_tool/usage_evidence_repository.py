"""Numerical schema 3: immutable snapshots, exact-cycle links and successors.

The worker owns only this numerical writer. Its core connection is read-only;
Task completion never calls it or attaches this store to the core transaction.
"""

from __future__ import annotations

import json

from task_governance_tool.usage_attribution import _models
from task_governance_tool.usage_attribution_repository import (
    UsageAttributionRepository, _ATTRIBUTION_DDL, core_transitions,
)
from task_governance_tool.usage_repository import UsageRepository, _DDL, _response
from task_governance_tool.usage_evidence import digest, encode, snapshot, validate, MAX_DOCUMENT_BYTES
from task_governance_tool.usage_values import UsageError


_MIGRATION = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2,3)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
          (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence')))"""
_IMMUTABLE = ("usage_snapshots", "usage_snapshot_members", "usage_cycle_links", "usage_supersessions")
_EVIDENCE_DDL = (
    """CREATE TABLE usage_snapshots (snapshot_id TEXT PRIMARY KEY, document TEXT NOT NULL)""",
    """CREATE TABLE usage_snapshot_members (
        snapshot_id TEXT NOT NULL REFERENCES usage_snapshots(snapshot_id),
        provider TEXT NOT NULL, response_id TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,provider,response_id),
        FOREIGN KEY(provider,response_id) REFERENCES usage_responses(provider,response_id))""",
    """CREATE TABLE usage_cycle_links (
        snapshot_id TEXT NOT NULL REFERENCES usage_snapshots(snapshot_id),
        completion_cycle_id TEXT NOT NULL, task_id TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,completion_cycle_id))""",
    """CREATE TABLE usage_supersessions (
        predecessor_id TEXT NOT NULL REFERENCES usage_snapshots(snapshot_id),
        successor_id TEXT NOT NULL REFERENCES usage_snapshots(snapshot_id),
        CHECK(predecessor_id != successor_id), PRIMARY KEY(predecessor_id,successor_id))""",
    """CREATE TABLE usage_capture (singleton INTEGER PRIMARY KEY CHECK(singleton=1), core_digest TEXT NOT NULL)""",
    *(f"CREATE TRIGGER trg_{table}_no_{operation.lower()} BEFORE {operation} ON {table} "
      "BEGIN SELECT RAISE(ABORT, 'immutable_usage_evidence'); END"
      for table in _IMMUTABLE for operation in ("UPDATE", "DELETE")),
)


def core_basis(core, project_id):
    """No prose or timestamps are hashed; restore admission uses original IDs."""
    transitions = core_transitions(core, project_id)
    cycles = [dict(row) for row in core.execute(
        "SELECT completion_cycle_id,task_id,execution_id FROM task_execution_cycles WHERE project_id=? ORDER BY rowid",
        (project_id,))]
    reviewers = [tuple(row) for row in core.execute(
        "SELECT binding.review_receipt_id,binding.session_id,binding.execution_id,binding.original_result_digest "
        "FROM review_receipt_sessions binding JOIN task_executions execution USING(execution_id) "
        "WHERE execution.project_id=? ORDER BY binding.review_receipt_id", (project_id,))]
    fingerprint = digest({"transitions": [tuple(getattr(row, key) for key in row.__dataclass_fields__) for row in transitions],
                          "cycles": cycles, "reviewers": reviewers})
    cycle_by_execution = {row["execution_id"]: row for row in cycles}
    periods, preceding = {}, {}
    for change in transitions:
        key = (change.task_id, preceding.get(change.task_id))
        if change.previous_status == "done" and change.current_status != "done":
            periods.setdefault(key, {"task_id": change.task_id, "preceding_completion": key[1],
                                     "executions": set(), "completion_cycle_id": None})
        if change.current_status == "in_progress" and change.execution_id is not None:
            periods.setdefault(key, {"task_id": change.task_id, "preceding_completion": key[1],
                                     "executions": set(), "completion_cycle_id": None})["executions"].add(change.execution_id)
        if change.current_status == "done":
            cycle = cycle_by_execution.get(change.execution_id)
            if key in periods and cycle is not None and cycle["task_id"] == change.task_id:
                periods[key]["completion_cycle_id"] = cycle["completion_cycle_id"]
            preceding[change.task_id] = change.transition_id
    return fingerprint, list(periods.values())


class UsageEvidenceRepository(UsageAttributionRepository):
    schema_statements = (_MIGRATION, *_DDL[1:], *_ATTRIBUTION_DDL, *_EVIDENCE_DDL)
    migrations = (*UsageAttributionRepository.migrations, (3, "immutable_usage_evidence"))

    def _core_basis(self, core, numerical_connection):
        return core_basis(core, self.basis[0])

    def inspect(self):
        try:
            return UsageRepository.inspect(self)
        except UsageError as error:
            if error.code != "usage_schema_invalid":
                raise
        for previous in (UsageAttributionRepository, UsageRepository):
            try:
                with previous(self.path, *self.basis).connection():
                    return "migration_required"
            except UsageError as error:
                if error.code != "usage_schema_invalid":
                    raise
        raise UsageError("usage_schema_invalid")

    def initialize(self):
        status = self.inspect()
        if status != "migration_required":
            return UsageRepository.initialize(self)
        for previous in (UsageAttributionRepository, UsageRepository):
            old = previous(self.path, *self.basis)
            try:
                with old.connection(write=True) as connection:
                    if (connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                            or connection.execute("PRAGMA foreign_key_check").fetchone()):
                        raise UsageError()
                    connection.execute("ALTER TABLE usage_migrations RENAME TO usage_migrations_old")
                    connection.execute(_MIGRATION)
                    connection.execute("INSERT INTO usage_migrations SELECT * FROM usage_migrations_old")
                    connection.execute("DROP TABLE usage_migrations_old")
                    if previous is UsageRepository:
                        for statement in _ATTRIBUTION_DDL:
                            connection.execute(statement)
                        connection.execute("INSERT INTO usage_migrations VALUES (2,'turn_attribution')")
                    for statement in _EVIDENCE_DDL:
                        connection.execute(statement)
                    self._before_evidence_marker(connection)
                    connection.execute("INSERT INTO usage_migrations VALUES (3,'immutable_usage_evidence')")
                    self._validate(connection)
                return "migrated"
            except UsageError as error:
                if error.code != "usage_schema_invalid":
                    raise
        raise UsageError("usage_schema_invalid")

    def _before_evidence_marker(self, connection):
        """Test seam before committing a numerical-only migration."""

    def _snapshots(self, connection, *, current=False):
        clause = " WHERE snapshot_id NOT IN (SELECT predecessor_id FROM usage_supersessions)" if current else ""
        result = []
        for row in connection.execute("SELECT snapshot_id,document FROM usage_snapshots" + clause + " ORDER BY rowid"):
            if not isinstance(row["document"], str) or len(row["document"].encode("utf-8")) > MAX_DOCUMENT_BYTES:
                raise UsageError()
            document = validate(json.loads(row["document"]), self.basis[0])
            if document["snapshot_id"] != row["snapshot_id"]:
                raise UsageError()
            keys = self.members(connection, row["snapshot_id"])
            if len(keys) != document["response_count"] or digest(keys) != document["response_set_digest"]:
                raise UsageError()
            models = _models(self._responses(connection, keys))
            if models != document["models"]:
                raise UsageError()
            predecessors = sorted(r[0] for r in connection.execute(
                "SELECT predecessor_id FROM usage_supersessions WHERE successor_id=?", (row["snapshot_id"],)))
            if predecessors != document["predecessors"]:
                raise UsageError()
            result.append(document)
        return result

    def members(self, connection, snapshot_id):
        """Exact immutable membership for audit; not expanded into CLI responses."""
        return [tuple(row) for row in connection.execute(
            "SELECT provider,response_id FROM usage_snapshot_members WHERE snapshot_id=? ORDER BY provider,response_id",
            (snapshot_id,))]

    def _responses(self, connection, keys):
        # Join the original key union, never sum overlapping snapshot totals.
        rows = connection.execute(
            "SELECT response.* FROM usage_responses response JOIN json_each(?) key "
            "ON response.provider=json_extract(key.value,'$[0]') AND response.response_id=json_extract(key.value,'$[1]')",
            (encode(sorted(keys)).decode("utf-8"),)).fetchall()
        if len(rows) != len(keys):
            raise UsageError()
        return [_response(row) for row in rows]

    def refresh(self, core_reader):
        """Replay all committed intervals. Serialized numerical writer, fresh core read.

        Opening the core snapshot after acquiring the numerical writer prevents
        an older queued worker from replacing a newer capture. No filesystem
        publication, host transcript read, or core write occurs in this writer.
        """
        with self.connection(write=True) as connection, core_reader() as core:
            fingerprint, periods = self._core_basis(core, connection)
            previous = self._snapshots(connection, current=True)
            projected = self._project_for_refresh(core, connection, previous)
            adopted = []
            for component in projected["components"]:
                if projected["unresolved_operations"]:
                    component = {**component, "diagnostics": sorted(set(component["diagnostics"]) | {"operation_unbound"})}
                overlaps = [item for item in previous if set(item["executions"]).intersection(component["executions"])]
                candidate = snapshot(self.basis[0], component)
                comparable = lambda value: {k: v for k, v in value.items() if k not in {"snapshot_id", "predecessors"}}
                if len(overlaps) == 1 and comparable(candidate) == comparable(overlaps[0]):
                    candidate = overlaps[0]
                else:
                    candidate = snapshot(self.basis[0], component, (item["snapshot_id"] for item in overlaps))
                    identity = candidate["snapshot_id"]
                    connection.execute("INSERT INTO usage_snapshots VALUES (?,?)", (identity, encode(candidate).decode("utf-8")))
                    connection.executemany("INSERT INTO usage_snapshot_members VALUES (?,?,?)",
                                           [(identity, *key) for key in component["response_keys"]])
                    connection.executemany("INSERT INTO usage_supersessions VALUES (?,?)",
                                           [(predecessor, identity) for predecessor in candidate["predecessors"]])
                adopted.append(candidate)
                for period in periods:
                    if period["completion_cycle_id"] is not None and period["executions"].intersection(candidate["executions"]):
                        connection.execute("INSERT OR IGNORE INTO usage_cycle_links VALUES (?,?,?)",
                                           (candidate["snapshot_id"], period["completion_cycle_id"], period["task_id"]))
            connection.execute("INSERT INTO usage_capture VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET core_digest=excluded.core_digest",
                               (fingerprint,))
            self._adopt_projection(connection, projected, adopted)
            self._before_snapshot_commit(connection)
        return adopted

    def _project_for_refresh(self, core, connection, previous):
        return self.attribution(core, numerical_connection=connection)

    def _adopt_projection(self, connection, projected, adopted):
        """Optional numerical cache adoption in the same immutable-evidence transaction."""

    def _before_snapshot_commit(self, connection):
        """Test seam; snapshots, supersessions, links and adoption commit together."""

    def read(self, core, *, task_id=None, audit=False, numerical_connection=None):
        from contextlib import nullcontext
        with (self.connection() if numerical_connection is None else nullcontext(numerical_connection)) as connection:
            fingerprint, periods = self._core_basis(core, connection)
            capture = connection.execute("SELECT core_digest FROM usage_capture WHERE singleton=1").fetchone()
            if capture is None or capture[0] != fingerprint:
                raise UsageError("usage_pending")
            documents = self._snapshots(connection, current=not audit)
            superseded = {row[0] for row in connection.execute("SELECT predecessor_id FROM usage_supersessions")}
            known_executions = set().union(*(period["executions"] for period in periods))
            current = [item for item in documents if item["snapshot_id"] not in superseded
                       and set(item["executions"]) <= known_executions]
            links = [dict(row) for row in connection.execute("SELECT * FROM usage_cycle_links ORDER BY rowid")]
            valid_links = {(period["task_id"], period["completion_cycle_id"]): period["executions"] for period in periods
                           if period["completion_cycle_id"] is not None}
            document_map = {item["snapshot_id"]: item for item in documents}
            # Restored-away cycles remain immutable history in the numerical
            # store, but are not accepted or rebound by a current projection.
            links = [link for link in links if link["snapshot_id"] in document_map
                     and valid_links.get((link["task_id"], link["completion_cycle_id"]), set()).intersection(
                         document_map[link["snapshot_id"]]["executions"])]
            if task_id is None:
                return {"format": "taskgov-usage-index-v1", "project_id": self.basis[0], "core_digest": fingerprint,
                        "snapshots": documents, "current_snapshot_ids": [item["snapshot_id"] for item in current], "cycle_links": links}
            selected = [period for period in periods if period["task_id"] == task_id]
            if not selected:
                raise UsageError("usage_pending")
            if not audit:
                selected = selected[-1:]
            else:
                selected = selected[-10:]
            summaries = []
            for period in selected:
                active = [item for item in current if period["executions"].intersection(item["executions"])]
                keys = {key for item in active for key in self.members(connection, item["snapshot_id"])}
                summaries.append({"completion_cycle_id": period["completion_cycle_id"],
                                  "snapshot_ids": [item["snapshot_id"] for item in active],
                                  "own_executions": sorted(period["executions"]),
                                  "shared_executions": sorted({execution for item in active for execution in item["executions"]}),
                                  "models": _models(self._responses(connection, keys)),
                                  "response_count": len(keys),
                                  "quality": "conflicting" if any(item["quality"] == "conflicting" for item in active)
                                  else "incomplete" if any(item["quality"] == "incomplete" for item in active) else "pending",
                                  "gaps": sorted({gap for item in active for gap in item["gaps"]})})
            chosen = {identity for period in summaries for identity in period["snapshot_ids"]}
            return {"status": "pending", "coverage": "registered_only", "periods": summaries,
                    "cycle_links": [link for link in links if link["task_id"] == task_id and link["snapshot_id"] in chosen]}
