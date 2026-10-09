"""Dirty-component numerical aggregation; full structural selection is retained.

Reevaluate interval geometry and diagnostics against the fresh admitted core.
Only components with changed geometry/diagnostics or dirty response turns load
and sum response rows. Old/new overlap is handled by immutable adoption, which
also preserves splits, merges and completion-period links. Evidence validation
remains independent and intentionally still checks original member counters.
"""

from task_governance_tool.usage_attribution import _models
from task_governance_tool.usage_evidence import digest, encode
from task_governance_tool.usage_repository import _response


def project_changed(repository, core, connection, previous):
    geometry = repository.attribution(core, numerical_connection=connection, geometry_only=True)
    dirty = {tuple(row) for row in connection.execute("SELECT thread_id,turn_id FROM usage_dirty_turns")}
    cached = {row["component_id"]: dict(row) for row in connection.execute("SELECT * FROM usage_component_basis")}
    snapshots = {item["snapshot_id"]: item for item in previous}
    components, bases = [], {}
    computed = 0
    for shape in geometry["components"]:
        repository.check_budget()
        identity = digest(shape["executions"])
        basis = digest([shape, bool(geometry["unresolved_operations"])])
        bases[identity] = basis
        stored = cached.get(identity)
        snapshot = snapshots.get(stored["snapshot_id"]) if stored else None
        turns = {tuple(turn) for turn in shape["turns"]}
        if (stored and stored["basis"] == basis and snapshot
                and snapshot["executions"] == shape["executions"] and not dirty.intersection(turns)):
            components.append({**shape, "response_keys": repository.members(connection, snapshot["snapshot_id"]),
                               "models": snapshot["models"], "diagnostics": snapshot["gaps"]})
            continue
        computed += 1
        rows = connection.execute(
            "SELECT response.*,conflict.response_id AS conflicting FROM usage_responses response "
            "JOIN json_each(?) turn ON response.thread_id=json_extract(turn.value,'$[0]') "
            "AND response.turn_id=json_extract(turn.value,'$[1]') LEFT JOIN usage_conflicts conflict "
            "ON response.provider=conflict.provider AND response.response_id=conflict.response_id",
            (encode(sorted(turns)).decode(),)).fetchall()
        good = [_response(row) for row in rows if row["conflicting"] is None]
        gaps = set(shape["diagnostics"]) - {"usage_pending"}
        if any(row["conflicting"] is not None for row in rows):
            gaps.add("response_conflict")
        if turns - {(row.thread_id, row.turn_id) for row in good}:
            gaps.add("usage_pending")
        components.append({**shape, "response_keys": sorted((row.provider, row.response_id) for row in good),
                           "models": _models(good), "diagnostics": sorted(gaps)})
    # Test-only work counters are not persisted or emitted to the model hook.
    repository.last_projection_work = {"components_aggregated": computed, "components_selected": len(components)}
    return {"components": components, "unresolved_operations": geometry["unresolved_operations"], "bases": bases}


def adopt(connection, projected, adopted):
    connection.execute("DELETE FROM usage_component_basis WHERE component_id NOT IN (SELECT value FROM json_each(?))",
                       (encode(sorted(projected["bases"])).decode(),))
    connection.executemany("INSERT INTO usage_component_basis VALUES (?,?,?) ON CONFLICT(component_id) DO UPDATE SET "
                           "basis=excluded.basis,snapshot_id=excluded.snapshot_id WHERE basis!=excluded.basis OR snapshot_id!=excluded.snapshot_id",
                           [(digest(item["executions"]), projected["bases"][digest(item["executions"])], item["snapshot_id"])
                            for item in adopted])
    connection.execute("DELETE FROM usage_dirty_turns")
