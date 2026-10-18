"""Numerical schema 6: response relations and exact owner-set projections.

The schema-5 reader remains unchanged for historical fixtures and stores.
Only explicit Setup admits/migrates this current schema; collectors extend the
existing cursor/CAS writer and never change a core Task or completion gate.
"""

from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
import json

from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository, _PREVIOUS
from task_governance_tool.usage_attribution_repository import (
    UsageAttributionRepository, core_transitions, resolve_operation,
)
from task_governance_tool.usage_repository import _response
from task_governance_tool.usage_turn_adapter import OperationObservation, TurnObservation
from task_governance_tool.usage_review_attribution import ReviewBoundary, ReviewReceiptTurn, reviewer_intervals
from task_governance_tool.usage_response_adapter import ResponsePosition, ResponseBoundary, ResponseTracker, CHECKS
from task_governance_tool.usage_response_attribution import project_responses, ALGORITHM
from task_governance_tool.usage_attribution import owner_segments, _models
from task_governance_tool.usage_evidence import digest, encode, RESPONSE_FORMAT
from task_governance_tool.usage_values import UsageError


_MIGRATION = """CREATE TABLE usage_migrations (
        version INTEGER PRIMARY KEY CHECK(version IN (1,2,3,4,5,6)),
        name TEXT NOT NULL CHECK((version=1 AND name='response_collection') OR
          (version=2 AND name='turn_attribution') OR (version=3 AND name='immutable_usage_evidence') OR
          (version=4 AND name='review_wait_attribution') OR (version=5 AND name='incremental_collection') OR
          (version=6 AND name='response_attribution')))"""
_RESPONSE_DDL = (
    """CREATE TABLE usage_response_positions (
        source_id TEXT NOT NULL, incarnation INTEGER NOT NULL, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
        provider TEXT NOT NULL, response_id TEXT NOT NULL, record_order INTEGER NOT NULL CHECK(record_order>=0),
        PRIMARY KEY(source_id,incarnation,provider,response_id),
        FOREIGN KEY(source_id,incarnation) REFERENCES usage_incarnations(source_id,incarnation))""",
    """CREATE TABLE usage_response_boundaries (
        source_id TEXT NOT NULL, incarnation INTEGER NOT NULL, observation_id TEXT NOT NULL, document TEXT NOT NULL,
        PRIMARY KEY(source_id,incarnation,observation_id),
        FOREIGN KEY(source_id,incarnation) REFERENCES usage_incarnations(source_id,incarnation))""",
    """CREATE TABLE usage_response_context (
        source_id TEXT NOT NULL REFERENCES usage_sources(source_id), lane TEXT NOT NULL CHECK(lane IN ('ingest','audit')),
        document TEXT NOT NULL, PRIMARY KEY(source_id,lane))""",
)

_COLLECTED_ENDPOINT_BASIS = "collected-review-endpoints-v4"


@dataclass(frozen=True)
class ResponseIdentity:
    provider: str
    response_id: str
    thread_id: str
    turn_id: str


def component_identity(component):
    return digest([component.get("format", RESPONSE_FORMAT), component.get("algorithm", ALGORITHM),
                   component["executions"], component.get("assignment", "legacy")])


class UsageResponseRepository(UsageIncrementalRepository):
    schema_statements = (_MIGRATION, *UsageIncrementalRepository.schema_statements[1:], *_RESPONSE_DDL)
    migrations = (*UsageIncrementalRepository.migrations, (6, "response_attribution"))
    _previous_repositories = (UsageIncrementalRepository, *_PREVIOUS)

    def _before_incremental_marker(self, connection):
        # Existing sanitized cursors and immutable history are preserved. The
        # first new scan replays in another incarnation, not an old continuation.
        from task_governance_tool.usage_incremental_repository import _document
        for row in connection.execute("SELECT * FROM usage_scan_state").fetchall():
            value = _document(row)
            value.update(state=asdict(value["state"]), reset=True)
            connection.execute("UPDATE usage_scan_state SET document=? WHERE source_id=? AND lane=?",
                               (encode(value).decode(), row["source_id"], row["lane"]))

    def _response_context(self, connection, source, lane, reset):
        if reset:
            return ResponseTracker.empty_state()
        row = connection.execute("SELECT document FROM usage_response_context WHERE source_id=? AND lane=?",
                                 (source, lane)).fetchone()
        if row is None:
            raise UsageError("usage_schema_invalid")
        try:
            value = json.loads(row[0])
            thread = connection.execute("SELECT thread_id FROM usage_sources WHERE source_id=?", (source,)).fetchone()[0]
            ResponseTracker(thread, "validation", value)
            return value
        except (ValueError, TypeError, KeyError):
            raise UsageError("usage_schema_invalid") from None

    def _save_response_context(self, connection, source, lane, context):
        if context is None:
            raise UsageError("usage_schema_invalid")
        thread = connection.execute("SELECT thread_id FROM usage_sources WHERE source_id=?", (source,)).fetchone()[0]
        ResponseTracker(thread, "validation", context)
        connection.execute("INSERT INTO usage_response_context VALUES (?,?,?) ON CONFLICT(source_id,lane) "
                           "DO UPDATE SET document=excluded.document", (source, lane, encode(context).decode()))

    def _record_attribution(self, connection, batch):
        structural = tuple(item for item in batch.attribution if not isinstance(item, (ResponsePosition, ResponseBoundary)))
        super()._record_attribution(connection, replace(batch, attribution=structural))
        for item in batch.attribution:
            if item.thread_id != batch.thread_id:
                raise UsageError("source_unreadable")
            if isinstance(item, ResponsePosition):
                connection.execute("INSERT OR IGNORE INTO usage_response_positions VALUES (?,?,?,?,?,?,?)",
                                   (batch.source_id, batch.successor.incarnation, item.thread_id, item.turn_id,
                                    item.provider, item.response_id, item.record_order))
            elif isinstance(item, ResponseBoundary):
                body = asdict(item)
                connection.execute("INSERT OR IGNORE INTO usage_response_boundaries VALUES (?,?,?,?)",
                                   (batch.source_id, batch.successor.incarnation, digest(body), encode(body).decode()))

    def _relations(self, connection, *, history=False):
        positions, boundaries = [], []
        for row in connection.execute("SELECT position.*,source.thread_id AS source_thread FROM usage_response_positions position JOIN usage_sources source "
                                      "ON source.source_id=position.source_id AND source.incarnation=position.incarnation"):
            item = ResponsePosition(**{name: row[name] for name in ResponsePosition.__dataclass_fields__})
            if item.thread_id != row["source_thread"]:
                raise UsageError("usage_schema_invalid")
            positions.append((row["source_id"], row["incarnation"], item))
        for row in connection.execute("SELECT boundary.*,source.thread_id AS source_thread FROM usage_response_boundaries boundary JOIN usage_sources source "
                                      "ON source.source_id=boundary.source_id" + ("" if history else
                                      " AND source.incarnation=boundary.incarnation")):
            try:
                body = json.loads(row["document"])
                if not isinstance(body, dict) or set(body) != set(ResponseBoundary.__dataclass_fields__):
                    raise UsageError("usage_schema_invalid")
                item = ResponseBoundary(**body)
                if digest(body) != row["observation_id"] or item.thread_id != row["source_thread"]:
                    raise UsageError("usage_schema_invalid")
            except (ValueError, TypeError, KeyError):
                raise UsageError("usage_schema_invalid") from None
            boundaries.append((row["source_id"], row["incarnation"], item))
        return positions, boundaries

    def _collected_review_endpoints(self, core):
        from task_governance_tool.storage import current_schema_version
        if current_schema_version(core) < 30:
            return ()
        from task_governance_tool.review_answer_repository import read_collected_review_endpoints
        receipts = {row[0] for row in core.execute(
            "SELECT review_receipt_id FROM review_receipts WHERE project_id=?", (self.basis[0],))}
        return read_collected_review_endpoints(core, receipt_ids=receipts)

    def _core_basis(self, core, connection):
        basis, periods = super()._core_basis(core, connection)
        positions, boundaries = self._relations(connection, history=True)
        relations = [[source, incarnation, asdict(item)] for source, incarnation, item in positions + boundaries]
        contexts = []
        for row in connection.execute("SELECT source_id,document FROM usage_response_context WHERE lane='ingest' ORDER BY source_id"):
            value = json.loads(row["document"])
            thread = connection.execute("SELECT thread_id FROM usage_sources WHERE source_id=?", (row["source_id"],)).fetchone()[0]
            ResponseTracker(thread, "validation", value)
            contexts.append((row["source_id"], value))
        sources = list(map(tuple, connection.execute("SELECT source_id,thread_id,incarnation,offset,pending FROM usage_sources ORDER BY source_id")))
        endpoints = [asdict(item) for item in self._collected_review_endpoints(core)]
        return digest([basis, ALGORITHM, sorted(relations, key=lambda item: encode(item)), contexts, sources,
                       [_COLLECTED_ENDPOINT_BASIS, endpoints]]), periods

    def attribution(self, core_connection, *, numerical_connection=None, geometry_only=False):
        with (self.connection() if numerical_connection is None else nullcontext(numerical_connection)) as connection:
            transitions = core_transitions(core_connection, self.basis[0])
            positions, boundaries = self._relations(connection)
            _, historic = self._relations(connection, history=True)
            origins, call_origins = {}, {}
            for _, _, item in historic:
                if item.response_id is not None:
                    origin = (item.thread_id, item.turn_id, item.provider, item.response_id)
                    if item.event_id is not None:
                        origins.setdefault(item.event_id, set()).add(origin)
                    call_origins.setdefault((item.thread_id, item.call_id), set()).add(origin)
            by_event, checks, unresolved = {}, [], set()
            first_positions = {(source, incarnation, item.provider, item.response_id): item
                               for source, incarnation, item in positions}
            numerical = {(row["provider"], row["response_id"]): (row["thread_id"], row["turn_id"])
                         for row in connection.execute("SELECT provider,response_id,thread_id,turn_id FROM usage_responses")}
            anchored = []
            for source, incarnation, item in boundaries:
                if item.project_id != self.basis[0]:
                    continue
                position = first_positions.get((source, incarnation, item.provider, item.response_id))
                same_numerical = numerical.get((item.provider, item.response_id), (item.thread_id, item.turn_id)) == (
                    item.thread_id, item.turn_id)
                qualified = (item.response_id is not None and same_numerical
                    and len(origins.get(item.event_id, ())) <= 1
                    and len(call_origins.get((item.thread_id, item.call_id), ())) <= 1 and (
                    (position is None and item.method == "explicit") or (position is not None
                     and position.thread_id == item.thread_id and position.turn_id == item.turn_id
                     and position.record_order >= item.record_order)))
                if not qualified:
                    item = replace(item, response_id=None, method="unavailable")
                if item.command in CHECKS:
                    checks.append(item)
                else:
                    operation = OperationObservation(item.thread_id, item.turn_id, item.project_id, item.task_id,
                                                     item.event_id, item.generation, item.status)
                    transition = resolve_operation(core_connection, operation)
                    if transition is None:
                        unresolved.add(item.event_id)
                        continue
                    by_event.setdefault(transition, []).append(item)
                if item.response_id is not None:
                    anchored.append((source, incarnation, ResponsePosition(item.thread_id, item.turn_id, item.provider,
                                                                           item.response_id, item.record_order)))
            bindings = {}
            for transition, items in by_event.items():
                identities = {(item.thread_id, item.turn_id, item.provider, item.response_id) for item in items}
                if len(identities) == 1 and next(iter(identities))[-1] is not None:
                    bindings[transition] = items[0]
                else:
                    unresolved.update(item.event_id for item in items)
            anchor_keys = {(source, incarnation, item.provider, item.response_id) for source, incarnation, item in anchored}
            positions = [entry for entry in positions if (entry[0], entry[1], entry[2].provider, entry[2].response_id)
                         not in anchor_keys] + anchored
            turns = tuple(TurnObservation(**dict(row)) for row in connection.execute("SELECT * FROM usage_turns"))
            fields = "provider,response_id,thread_id,turn_id" if geometry_only else "*"
            rows = connection.execute("SELECT " + fields + " FROM usage_responses").fetchall()
            responses = tuple(ResponseIdentity(row["provider"], row["response_id"], row["thread_id"], row["turn_id"])
                              if geometry_only else _response(row) for row in rows)
            conflicts = frozenset(tuple(row) for row in connection.execute("SELECT * FROM usage_conflicts"))
            diagnostics = {}
            for row in connection.execute("SELECT source.thread_id,gap.code FROM usage_sources source LEFT JOIN "
                                          "usage_diagnostics gap USING(source_id)"):
                if row["code"] is not None:
                    diagnostics.setdefault(row["thread_id"], set()).add(row["code"])
            old_operations = [OperationObservation(**dict(row)) for row in connection.execute(
                "SELECT thread_id,turn_id,project_id,task_id,event_id,generation,status FROM usage_operation_turns "
                "WHERE event_id NOT IN (SELECT event_id FROM usage_operation_conflicts)")]
            old_bindings = {identity: (item.thread_id, item.turn_id) for item in old_operations
                            if (identity := resolve_operation(core_connection, item)) is not None}
            segments = owner_segments(transitions, old_bindings)
            owners = tuple(interval for interval, _, _ in segments)
            turn_conflicts = frozenset(tuple(row) for row in connection.execute("SELECT * FROM usage_turn_conflicts"))
            additional = self._additional_intervals(core_connection, connection, owners, turns, turn_conflicts)
            from task_governance_tool.storage import current_schema_version
            reviews = []
            if current_schema_version(core_connection) >= 25:
                from task_governance_tool.review_session_repository import read_bindings, _receipt_target
                receipts = {row[0] for row in core_connection.execute(
                    "SELECT review_receipt_id FROM review_receipts WHERE project_id=?", (self.basis[0],))}
                for receipt_id, binding in read_bindings(core_connection, receipt_ids=receipts).items():
                    target, _, _ = _receipt_target(core_connection, receipt_id)
                    reviews.append((receipt_id, target, binding))
            review_boundaries = tuple(ReviewBoundary(**dict(row)) for row in connection.execute("SELECT * FROM usage_review_boundaries"))
            receipt_turns = tuple(ReviewReceiptTurn(**dict(row)) for row in connection.execute("SELECT * FROM usage_review_receipt_turns"))
            additional += reviewer_intervals(owners, turns, review_boundaries, receipt_turns, reviews,
                                             collected_endpoints=self._collected_review_endpoints(core_connection),
                                             conflicting_turns=turn_conflicts)
            pending = {}
            for row in connection.execute("SELECT context.document,source.source_id,source.incarnation,source.thread_id,scan.document AS scan FROM usage_response_context context "
                    "JOIN usage_sources source USING(source_id) JOIN usage_scan_state scan "
                    "ON scan.source_id=context.source_id AND scan.lane=context.lane WHERE context.lane='ingest'"):
                state = json.loads(row["document"])
                ResponseTracker(row["thread_id"], "validation", state)
                from task_governance_tool.usage_incremental_repository import _document
                provider = _document({"document": row["scan"]})["state"].provider
                overflow = () if state["overflow"] is None else (state["overflow"],)
                for frame in (*state["calls"].values(), *state["handles"].values(), *overflow):
                    identity = (row["source_id"], row["incarnation"], frame["call"], frame["turn"], frame["order"])
                    origin = (row["source_id"], row["incarnation"], row["thread_id"], frame["turn"], provider,
                              None if frame["ambiguous"] else frame["response"], frame["order"])
                    previous = pending.get(identity)
                    if previous is not None and previous[5] != origin[5]:
                        origin = (*origin[:5], None, origin[6])
                    pending[identity] = origin
            return project_responses(transitions, bindings, checks, positions, turns, responses,
                additional=additional, conflicting_responses=conflicts, thread_diagnostics=diagnostics,
                conflicting_turns=turn_conflicts, unresolved=bool(unresolved), geometry_only=geometry_only,
                pending=tuple(pending.values()), fallback=owners,
                fallback_origins=tuple((origin, endpoint) for _, origin, endpoint in segments))

    def _project_for_refresh(self, core, connection, previous):
        geometry = self.attribution(core, numerical_connection=connection, geometry_only=True)
        dirty = {tuple(row) for row in connection.execute("SELECT * FROM usage_dirty_turns")}
        cached = {row["component_id"]: dict(row) for row in connection.execute("SELECT * FROM usage_component_basis")}
        snapshots = {item["snapshot_id"]: item for item in previous}
        bases, components, computed = {}, [], 0
        for component in geometry["components"]:
            self.check_budget()
            identity, basis = component_identity(component), digest(component)
            bases[identity] = basis
            saved = cached.get(identity)
            snapshot = snapshots.get(saved["snapshot_id"]) if saved else None
            if saved and saved["basis"] == basis and snapshot and not dirty.intersection(map(tuple, component["turns"])):
                components.append({**component, "models": snapshot["models"]})
            else:
                computed += 1
                components.append({**component, "models": _models(self._responses(connection, component["response_keys"]))})
        self.last_projection_work = {"components_aggregated": computed, "components_selected": len(components)}
        selected_keys = {tuple(key) for item in components for key in item["response_keys"]}
        self._retired_snapshot_ids = {item["snapshot_id"] for item in previous if item["response_count"]
                                     and not selected_keys.intersection(self.members(connection, item["snapshot_id"]))}
        return {**geometry, "components": components, "bases": bases}

    def _adopt_projection(self, connection, projected, adopted):
        connection.execute("DELETE FROM usage_component_basis WHERE component_id NOT IN (SELECT value FROM json_each(?))",
                           (encode(sorted(projected["bases"])).decode(),))
        connection.executemany("INSERT INTO usage_component_basis VALUES (?,?,?) ON CONFLICT(component_id) DO UPDATE SET "
                               "basis=excluded.basis,snapshot_id=excluded.snapshot_id WHERE basis!=excluded.basis OR snapshot_id!=excluded.snapshot_id",
            [(component_identity(item), projected["bases"][component_identity(item)], item["snapshot_id"]) for item in adopted])
        connection.execute("DELETE FROM usage_dirty_turns")

    def _overlapping_snapshots(self, connection, previous, component):
        keys, identity = set(map(tuple, component["response_keys"])), component_identity(component)
        return [item for item in previous if
                (item["format"] != RESPONSE_FORMAT and set(item["executions"]).intersection(component["executions"]))
                or (item["format"] == RESPONSE_FORMAT and (component_identity(item) == identity
                    or keys.intersection(self.members(connection, item["snapshot_id"]))
                    or (item["snapshot_id"] in getattr(self, "_retired_snapshot_ids", set())
                        and set(item["executions"]).intersection(component["executions"]))
                    or ((not item["response_count"] or not component["response_keys"])
                        and set(item["executions"]).intersection(component["executions"]))))]

    def read(self, core, *, task_id=None, audit=False, numerical_connection=None):
        with (self.connection() if numerical_connection is None else nullcontext(numerical_connection)) as connection:
            result, current, periods = self._read(core, connection, task_id=task_id, audit=audit)
            current = [item for item in current if item["format"] == RESPONSE_FORMAT]
            if task_id is None:
                classification = self.attribution(core, numerical_connection=connection, geometry_only=True)
                result["format"] = "taskgov-usage-index-v2"
                result["attribution"] = ALGORITHM
                result["unattributed"] = {kind: self._totals(connection, classification[field]) for kind, field in
                    (("unknown", "unknown_response_keys"), ("unassigned", "unassigned_response_keys"))}
                return result
            units = {execution for period in periods if period["task_id"] == task_id for execution in period["executions"]}
            all_keys = {key for item in current if units.intersection(item["executions"])
                        for key in self.members(connection, item["snapshot_id"])}
            result["attribution"] = ALGORITHM
            result["total"] = self._totals(connection, all_keys)
            for period in result["periods"]:
                period["work_units"] = []
                for execution in period["own_executions"]:
                    parts = [item for item in current if execution in item["executions"]]
                    unit = {"execution_id": execution, "exclusive": self._totals(connection, {
                        key for part in parts if part["assignment"] == "exclusive"
                        for key in self.members(connection, part["snapshot_id"])}), "shared": [], "partial": []}
                    for part in parts:
                        if part["assignment"] in {"shared", "partial"}:
                            unit[part["assignment"]].append({"executions": part["executions"], "snapshot_id": part["snapshot_id"],
                                "members": [dict(row) for row in core.execute("SELECT execution_id,task_id FROM task_executions "
                                    "WHERE project_id=? AND execution_id IN (SELECT value FROM json_each(?)) ORDER BY execution_id",
                                    (self.basis[0], encode(part["executions"]).decode()))],
                                **self._totals(connection, self.members(connection, part["snapshot_id"]))})
                    period["work_units"].append(unit)
            return result

    def _totals(self, connection, keys):
        keys = set(map(tuple, keys))
        return {"response_count": len(keys), "models": _models(self._responses(connection, keys))}
