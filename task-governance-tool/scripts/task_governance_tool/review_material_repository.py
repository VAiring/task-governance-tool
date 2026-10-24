"""Lexically borrowed immutable review material in one caller transaction.

Only authority/manifest originals are retained. Receipt, Finding, Reference,
ownership, Task state and gate projections always remain fresh caller reads.
"""

from contextlib import contextmanager
import secrets
import sqlite3


class ReviewMaterials:
    def __init__(self, connection, project_id, task_id):
        self.connection = connection
        self.project_id = project_id
        self.task_id = task_id
        self.schema_cookie = connection.execute("PRAGMA schema_version").fetchone()[0]
        self.guard = 'taskgov_review_material_' + secrets.token_hex(8)
        connection.execute(f"SAVEPOINT {self.guard}")
        self.authorities = {}
        self.manifests = {}

    def require(self, connection, project_id, task_id):
        from .storage import evidence_ledger_inconsistent
        if (self.connection is not connection or not connection.in_transaction
                or (project_id, task_id) != (self.project_id, self.task_id)
                or connection.execute("PRAGMA schema_version").fetchone()[0] != self.schema_cookie):
            raise evidence_ledger_inconsistent()
        # A commit/rollback followed by BEGIN can leave in_transaction true.
        # The private savepoint proves this is still the original transaction;
        # releasing/replacing it commits nothing in the required outer one.
        try:
            connection.execute(f"RELEASE SAVEPOINT {self.guard}")
            connection.execute(f"SAVEPOINT {self.guard}")
        except sqlite3.Error as exc:
            raise evidence_ledger_inconsistent() from exc

    def close(self):
        from .storage import evidence_ledger_inconsistent
        connection, self.connection = self.connection, None
        self.authorities.clear()
        self.manifests.clear()
        try:
            connection.execute(f"RELEASE SAVEPOINT {self.guard}")
        except sqlite3.Error as exc:
            raise evidence_ledger_inconsistent() from exc

    def selected(self, connection, *, project_id, task_id, manifest_ids, snapshot_ids):
        from .evidence_repository import _validated_authority_context, _validate_artifact_manifest_storage
        from .storage import evidence_ledger_inconsistent
        self.require(connection, project_id, task_id)
        missing = snapshot_ids - self.authorities.keys()
        if missing:
            context = _validated_authority_context(connection, snapshot_ids=set(missing))
            for identifier, snapshot in context.snapshots.items():
                if (snapshot["project_id"], snapshot["task_id"]) != (project_id, task_id):
                    raise evidence_ledger_inconsistent()
                self.authorities[identifier] = context
        snapshots, links, criteria = {}, {}, {}
        for identifier in snapshot_ids:
            context = self.authorities[identifier]
            snapshots[identifier] = context.snapshots[identifier]
            links[identifier] = context.links.get(identifier, {})
            for criterion_id in links[identifier].values():
                criteria[criterion_id] = context.criteria[criterion_id]
        missing_manifests = manifest_ids - self.manifests.keys()
        if missing_manifests:
            records, _ = _validate_artifact_manifest_storage(connection,
                snapshots=snapshots, links=links, manifest_ids=set(missing_manifests))
            for identifier, record in records.items():
                if (record.row["project_id"], record.row["task_id"]) != (project_id, task_id):
                    raise evidence_ledger_inconsistent()
                self.manifests[identifier] = record
        records = {identifier: self.manifests[identifier] for identifier in manifest_ids}
        targets = {}
        from .evidence_repository import _artifact_manifest_target_key
        for record in records.values():
            if record.binding.authority_snapshot_id not in snapshots:
                raise evidence_ledger_inconsistent()
            key = _artifact_manifest_target_key(**{name: record.row[name] for name in (
                "project_id", "task_id", "target_kind", "target_value", "target_base_revision", "target_generation")})
            if key in targets:
                raise evidence_ledger_inconsistent()
            targets[key] = record
        return snapshots, criteria, links, records, targets


@contextmanager
def review_materials(connection, *, project_id, task_id):
    """Borrow immutable originals; callers never end a transaction inside scope."""
    owns_read = not connection.in_transaction
    value = None
    try:
        if owns_read:
            connection.execute("BEGIN")
        value = ReviewMaterials(connection, project_id, task_id)
        try:
            yield value
        except BaseException:
            try:
                value.close()
            except Exception:
                pass  # Preserve the original failure; caller rolls back writes.
            raise
        else:
            value.close()
    finally:
        if owns_read and connection.in_transaction:
            connection.rollback()


class FindingBatch:
    def __init__(self, connection, project, task, materials):
        from . import task_ownership
        self.project = project
        self.task = dict(task)
        self.materials = materials
        self.ownership = task_ownership.capture_basis(connection,
            project_id=project.project_id, task_id=task["task_id"])

    def read_basis(self, connection):
        from .stored_task_validation import fetch_stored_task_row, validate_stored_task_rows
        from .storage import current_schema_version
        from .reviews import reject_concurrent_review_basis_change
        from .tasks import TaskRepositoryError
        self.materials.require(connection, self.project.project_id, self.task["task_id"])
        row = fetch_stored_task_row(connection,
            "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
            (self.project.project_id, self.task["task_id"]))
        if row is None:
            raise TaskRepositoryError("not_found", "task was not found")
        # Mutable Task fields, its current Contract relation and ownership are
        # still validated. The outer locked read admitted the fixed authority.
        validate_stored_task_rows([row], connection=connection,
            source_schema_version=current_schema_version(connection),
            expected_project_id=self.project.project_id)
        task = dict(row)
        reject_concurrent_review_basis_change(self.task, task)
        return task, self.ownership


@contextmanager
def finding_batch(connection, project, task):
    """Use only after the outer writer's full locked Task admission."""
    from .storage import _require_evidence_writer
    from .tasks import read_internal_task
    from .reviews import reject_concurrent_review_basis_change
    _require_evidence_writer(connection)
    with review_materials(connection, project_id=project.project_id, task_id=task["task_id"]) as materials:
        value = FindingBatch(connection, project, task, materials)
        yield value
        # Revalidate the complete mutable graph at the batch boundary, including
        # newly created References. No successful-prefix or gate cache escapes.
        observed = read_internal_task(connection, project.project_id, task["task_id"])
        if observed is None:
            from .tasks import TaskRepositoryError
            raise TaskRepositoryError("not_found", "task was not found")
        reject_concurrent_review_basis_change(task, observed)
