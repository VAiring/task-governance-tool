"""Thin publication inventory and semantic closure of unpublished Bundles.

The caller owns a structurally admitted, query-only snapshot. Published index
membership is an output baseline, never a reusable database validation proof.
"""
from __future__ import annotations

from collections import defaultdict
import sqlite3
from typing import Any

from task_governance_tool.evidence_projection import (
    _IndexDocument, _RenderedProjection, build_index_artifact,
)
from task_governance_tool.evidence_projection_metadata_repository import EvidenceProjectionState
from task_governance_tool.evidence_validation_repository import (
    _selected_completion_bundle_documents,
    _validate_selected_completion_cycle_evidence,
)
from task_governance_tool.storage import (
    EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT, SCHEMA_VERSION,
    _cycle_from_row, _fetch_bounded_projection_rows, _validate_cycle_receipt_links_batch,
    _selected_history_runner_generations, _selected_storage_rows_by_ids,
    evidence_ledger_inconsistent,
)


def capture_incremental_evidence_projection(
    connection: sqlite3.Connection, *, project_id: str,
    state: EvidenceProjectionState, published: dict[str, Any],
) -> _RenderedProjection:
    """Select by exact acknowledged cycle/Bundle IDs, never by count or row offset."""
    if (not connection.in_transaction
            or connection.execute("PRAGMA query_only").fetchone()[0] != 1
            or published["project_id"] != project_id
            or published["source_schema_version"] != SCHEMA_VERSION
            or published["projection_generation"] != state.published_generation):
        raise evidence_ledger_inconsistent()
    rows = _fetch_bounded_projection_rows(connection.execute(
        """
        SELECT cycle.completion_cycle_id, cycle.task_id, cycle.saved_cycle_ordinal,
               cycle.evidence_basis_version, cycle.completion_evidence_bundle_id,
               bundle.project_id AS bundle_project_id, bundle.task_id AS bundle_task_id,
               bundle.completion_cycle_id AS bundle_cycle_id,
               bundle.cycle_ordinal AS bundle_ordinal, bundle.bundle_digest,
               bundle.bundle_version, bundle.sealed_at
          FROM task_completion_cycles AS cycle
          LEFT JOIN completion_evidence_bundles AS bundle
            ON bundle.completion_evidence_bundle_id = cycle.completion_evidence_bundle_id
         WHERE cycle.project_id = ?
         ORDER BY cycle.task_id COLLATE BINARY, cycle.saved_cycle_ordinal,
                  cycle.completion_cycle_id COLLATE BINARY
        """, (project_id,)), maximum=EVIDENCE_PROJECTION_INDEX_ENTRY_LIMIT)
    previous = {entry["completion_cycle_id"]: entry for entry in published["entries"]}
    if len(previous) != len(published["entries"]) or len(rows) != state.source_generation:
        raise evidence_ledger_inconsistent()
    entries: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    seen_cycles: set[str] = set()
    seen_bundles: set[str] = set()
    ordinals: dict[str, int] = {}
    for row in rows:
        cycle_id, task_id = row["completion_cycle_id"], row["task_id"]
        ordinal, bundle_id = row["saved_cycle_ordinal"], row["completion_evidence_bundle_id"]
        if (cycle_id in seen_cycles or type(ordinal) is not int
                or ordinal != ordinals.get(task_id, 0) + 1):
            raise evidence_ledger_inconsistent()
        seen_cycles.add(cycle_id)
        ordinals[task_id] = ordinal
        entry = dict(task_id=task_id, completion_cycle_id=cycle_id,
                     cycle_ordinal=ordinal, bundle_state="legacy_unknown",
                     bundle_id=None, bundle_file=None, bundle_digest=None,
                     file_digest=None, sealed_at=None, bundle_format_version=None)
        if row["evidence_basis_version"] == 1:
            if (type(bundle_id) is not str or bundle_id in seen_bundles
                    or row["bundle_project_id"] != project_id
                    or row["bundle_task_id"] != task_id
                    or row["bundle_cycle_id"] != cycle_id
                    or row["bundle_ordinal"] != ordinal):
                raise evidence_ledger_inconsistent()
            seen_bundles.add(bundle_id)
            entry.update(bundle_state="native", bundle_id=bundle_id,
                         bundle_file=f"bundles/{bundle_id}.json",
                         bundle_digest=row["bundle_digest"], sealed_at=row["sealed_at"],
                         bundle_format_version=row["bundle_version"])
        elif row["evidence_basis_version"] != 0 or bundle_id is not None:
            raise evidence_ledger_inconsistent()
        old = previous.get(cycle_id)
        if old is not None:
            entry["file_digest"] = old["file_digest"]
            if entry != old:
                raise evidence_ledger_inconsistent()
        else:
            selected_ids.add(cycle_id)
        entries.append(entry)
    bundle_count = connection.execute(
        "SELECT COUNT(*) FROM completion_evidence_bundles WHERE project_id = ?",
        (project_id,)).fetchone()[0]
    if not previous.keys() <= seen_cycles or bundle_count != len(seen_bundles):
        raise evidence_ledger_inconsistent()

    cycles_by_task = defaultdict(list)
    for row in _selected_storage_rows_by_ids(
        connection, table_name="task_completion_cycles", id_field="completion_cycle_id",
        selected_ids=selected_ids,
    ):
        cycle = _cycle_from_row(row)
        if cycle.project_id != project_id:
            raise evidence_ledger_inconsistent()
        cycles_by_task[cycle.task_id].append(cycle)
    if sum(map(len, cycles_by_task.values())) != len(selected_ids):
        raise evidence_ledger_inconsistent()
    documents = {}
    for cycles in cycles_by_task.values():
        for offset in range(0, len(cycles), 10):
            chunk = tuple(cycles[offset:offset + 10])
            _validate_cycle_receipt_links_batch(connection, project_id=project_id, cycles=chunk)
            _validate_selected_completion_cycle_evidence(
                connection, project_id=project_id, cycles=chunk)
            runners = _selected_history_runner_generations(connection, cycles=chunk)
            for bundle_id, document in _selected_completion_bundle_documents(
                connection, cycles=chunk, runner_generations=runners,
                container_schema_version=SCHEMA_VERSION,
            ):
                if bundle_id in documents:
                    raise evidence_ledger_inconsistent()
                documents[bundle_id] = document
    for entry in entries:
        if entry["completion_cycle_id"] in selected_ids and entry["bundle_state"] == "native":
            artifact = documents.get(entry["bundle_id"])
            if artifact is None or artifact.bundle_digest != entry["bundle_digest"]:
                raise evidence_ledger_inconsistent()
            entry["file_digest"] = artifact.file_digest
    index = build_index_artifact(dict(
        source_schema_version=SCHEMA_VERSION, project_id=project_id,
        projection_generation=state.source_generation, bundle_count=len(seen_bundles),
        legacy_count=len(entries) - len(seen_bundles), entries=entries,
    ))
    return _RenderedProjection(project_id, state.source_generation,
                               tuple(sorted(documents.items())),
                               _IndexDocument(index.document, index.index_digest))
