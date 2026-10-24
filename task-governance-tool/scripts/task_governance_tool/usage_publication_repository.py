"""Numerical schema 7: resumable immutable-file audit, not a verified cache.

The cursor records completed work against an exact index basis. Every cycle
checks original rows and bytes again. It never proves that earlier items have
remained unchanged between events. Only explicit Setup migrates the store.
"""

from contextlib import contextmanager
from task_governance_tool.usage_response_repository import UsageResponseRepository
from task_governance_tool import usage_response_repository as response_policy
from task_governance_tool.usage_incremental_repository import _hex
from task_governance_tool.usage_evidence import digest, encode
from task_governance_tool.usage_values import UsageError
from task_governance_tool.usage_attribution_repository import resolve_operation
from task_governance_tool.usage_turn_adapter import OperationObservation


_MIGRATION = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2,3,4,5,6,7)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
          (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence') OR
          (version=4 AND name='review_wait_attribution') OR (version=5 AND name='incremental_collection') OR
          (version=6 AND name='response_attribution') OR (version=7 AND name='progressive_publication')))"""
_PUBLICATION_DDL = """CREATE TABLE usage_publication (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL CHECK(revision>0),
        basis TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0),
        total INTEGER NOT NULL CHECK(total>=position), published INTEGER NOT NULL CHECK(published IN (0,1)),
        checksum TEXT NOT NULL)"""
INTEGRITY = {"policy": "rotating-audit-v1", "status": "verification_deferred"}


class UsagePublicationRepository(UsageResponseRepository):
    schema_statements = (_MIGRATION, *UsageResponseRepository.schema_statements[1:], _PUBLICATION_DDL)
    migrations = (*UsageResponseRepository.migrations, (7, "progressive_publication"))
    _previous_repositories = (UsageResponseRepository, *UsageResponseRepository._previous_repositories)

    def _after_numerical_upgrade(self, connection, previous):
        # Preserve schema-6 continuations. Earlier schemas still need replay.
        # The predecessor comes from writer admission, never a stale preview.
        if previous is not UsageResponseRepository:
            super()._after_numerical_upgrade(connection, previous)
        self._before_publication_marker(connection)

    def _before_publication_marker(self, connection):
        """Fault seam before the complete numerical migration commits."""

    def _core_basis(self, core, connection, *, _review_inputs=None, _relation_inputs=None):
        relations = self._relation_inputs(connection, _relation_inputs)
        basis, periods = super()._core_basis(core, connection, _review_inputs=_review_inputs,
                                            _relation_inputs=relations)
        # Include every mutable structural classifier input as closed metadata.
        # No counters or private bodies enter this guard. Original-counter integrity is
        # checked by the snapshot audit, not certified by this fingerprint.
        metadata = {table: sorted(map(tuple, connection.execute("SELECT * FROM " + table)), key=encode)
                    for table in ("usage_turns", "usage_turn_conflicts", "usage_operation_turns",
                                  "usage_operation_conflicts", "usage_review_boundaries", "usage_review_receipt_turns",
                                  "usage_conflicts", "usage_diagnostics")}
        operations = [OperationObservation(row[1], row[2], row[3], row[4], row[0], row[5], row[6])
                      for row in metadata["usage_operation_turns"]]
        boundaries = relations.history
        operations.extend(OperationObservation(item.thread_id, item.turn_id, item.project_id, item.task_id,
                                               item.event_id, item.generation, item.status)
                          for _, _, item in boundaries if item.event_id is not None)
        resolutions = sorted(((item.event_id, resolve_operation(core, item)) for item in operations), key=encode)
        return digest([basis, metadata, resolutions]), periods

    def _index_metadata(self, connection, periods):
        identities = [row[0] for row in connection.execute("SELECT snapshot_id FROM usage_snapshots ORDER BY rowid")]
        if any(not _hex(identity) for identity in identities):
            raise UsageError()
        known = sorted(set().union(*(period["executions"] for period in periods)))
        current = [row[0] for row in connection.execute(
            "SELECT snapshot_id FROM usage_snapshots WHERE snapshot_id NOT IN "
            "(SELECT predecessor_id FROM usage_supersessions) AND NOT EXISTS "
            "(SELECT 1 FROM json_each(usage_snapshots.document,'$.executions') member "
            "WHERE member.value NOT IN (SELECT value FROM json_each(?))) ORDER BY rowid",
            (encode(known).decode(),))]
        valid = {(period["task_id"], period["completion_cycle_id"]): period["executions"] for period in periods
                 if period["completion_cycle_id"] is not None}
        links = []
        for row in connection.execute("SELECT * FROM usage_cycle_links ORDER BY rowid"):
            executions = valid.get((row["task_id"], row["completion_cycle_id"]))
            if executions and connection.execute(
                    "SELECT 1 FROM usage_snapshots snapshot,json_each(snapshot.document,'$.executions') member "
                    "WHERE snapshot.snapshot_id=? AND member.value IN (SELECT value FROM json_each(?)) LIMIT 1",
                    (row["snapshot_id"], encode(sorted(executions)).decode())).fetchone():
                links.append(dict(row))
        return {"snapshots": identities, "current_snapshot_ids": current, "cycle_links": links}

    def prepare_publication(self, core_reader):
        """Capture once and reuse classification within that same transaction."""
        with self.connection(write=True) as connection, core_reader() as core:
            captured = self._refresh(core, connection)
            projected = captured["projected"]
            index = {"format": "taskgov-usage-index-v2", "project_id": self.basis[0],
                     "core_digest": captured["fingerprint"], "attribution": response_policy.ALGORITHM,
                     "integrity": dict(INTEGRITY),
                     **self._index_metadata(connection, captured["periods"]),
                     "unattributed": {kind: self._totals(connection, projected[field], read=captured["read"]) for kind, field in
                         (("unknown", "unknown_response_keys"), ("unassigned", "unassigned_response_keys"))}}
            basis = digest(index)
            previous = self._publication_state(connection)
            revision = previous["revision"] + 1 if previous else 1
            position = (previous["position"] if previous and previous["basis"] == basis
                        and previous["total"] == len(index["snapshots"]) and not previous["published"] else 0)
            state = {"revision": revision, "basis": basis, "position": position,
                     "total": len(index["snapshots"]), "published": 0}
            self._save_publication_state(connection, state)
        return index, state

    def _publication_state(self, connection):
        row = connection.execute("SELECT revision,basis,position,total,published,checksum FROM usage_publication WHERE singleton=1").fetchone()
        if row is None:
            return None
        state = dict(row)
        checksum = state.pop("checksum")
        if (checksum != digest(state) or not _hex(state["basis"]) or any(type(state[k]) is not int for k in ("revision", "position", "total", "published"))
                or state["revision"] < 1 or not 0 <= state["position"] <= state["total"]
                or state["published"] not in (0, 1) or (state["published"] and state["position"] != state["total"])):
            raise UsageError()
        return state

    def _save_publication_state(self, connection, state):
        # Detect damaged continuation fields, without claiming authentication or
        # unchanged external bytes. This is not a verified-result certificate.
        connection.execute("INSERT INTO usage_publication VALUES (1,?,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
            "revision=excluded.revision,basis=excluded.basis,position=excluded.position,total=excluded.total,"
            "published=excluded.published,checksum=excluded.checksum",
            (state["revision"], state["basis"], state["position"], state["total"], state["published"], digest(state)))

    def publication_snapshot(self, identity):
        with self.connection() as connection:
            return self._snapshot(connection, connection.execute(
                "SELECT snapshot_id,document FROM usage_snapshots WHERE snapshot_id=?", (identity,)).fetchone())

    def advance_publication(self, state, *, published=False):
        """CAS after durable bytes; failed/unknown publication never advances."""
        with self.connection(write=True) as connection:
            if self._publication_state(connection) != state:
                raise UsageError("cursor_stale")
            if state["published"] or (published and state["position"] != state["total"]):
                raise UsageError()
            position = state["position"] if published else state["position"] + 1
            if position > state["total"]:
                raise UsageError()
            successor = {**state, "revision": state["revision"] + 1, "position": position, "published": int(published)}
            self._save_publication_state(connection, successor)
        return successor

    @contextmanager
    def publication_guard(self, core_reader, index, state):
        """Fresh admitted snapshots fence index replacement; no writer spans I/O.

        History already visited by this sweep remains explicitly deferred. The
        guard checks structural capture/collection changes, not mtime or a
        promise that previous files are still intact.
        """
        index_basis = digest(index)
        self.check_budget()
        with self.connection() as connection, core_reader() as core:
            if (self._publication_state(connection) != state or state["position"] != state["total"]
                    or state["basis"] != index_basis):
                raise UsageError("cursor_stale")
            fingerprint, periods = self._core_basis(core, connection)
            capture = connection.execute("SELECT core_digest FROM usage_capture WHERE singleton=1").fetchone()
            if (capture is None or capture[0] != fingerprint or fingerprint != index["core_digest"]
                    or connection.execute("SELECT 1 FROM usage_dirty_turns LIMIT 1").fetchone()
                    or self._index_metadata(connection, periods) != {key: index[key] for key in
                        ("snapshots", "current_snapshot_ids", "cycle_links")}):
                raise UsageError("usage_pending")
            self.check_budget()
            yield
