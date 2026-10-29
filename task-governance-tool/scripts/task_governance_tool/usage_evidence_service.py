"""Numerical-only replay/publication and non-blocking Task-detail projection.

No public command, ownership permission, hook install or implicit setup. The
internal worker consumes a resolver-admitted target, never caller-supplied IDs.
"""

from contextlib import closing, contextmanager, nullcontext, suppress
from uuid import uuid4

from task_governance_tool.artifact_lock import zero_wait_artifact_lock
from task_governance_tool.evidence_publication import _atomic_replace_temporary
from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.state_paths import (
    inspect_physical_directory, path_lexically_exists, create_physical_directory_exclusive,
    read_physical_file_bounded, create_exclusive_durable_file, unlink_validated_file,
)
from task_governance_tool.storage import connect_initialized_readonly
from task_governance_tool.usage_evidence import encode, MAX_DOCUMENT_BYTES
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_values import UsageError


def repository_for(target):
    return UsageReconciliationRepository(target.resolved_usage_database, target.project.project_id,
                                   target.binding_path_hash, target.binding_generation)


def read_task_usage(target, core, task_id, *, audit=False):
    try:
        repository = repository_for(target)
        # Reject unavailable numerical state before consuming core history.
        # Keep admission and projection in the same numerical transaction.
        with repository.connection() as numerical:
            # The repository's fresh fence checks the admitted capture. Changed
            # histories remain pending until the numerical worker reconciles them.
            return repository.read(core, task_id=task_id, audit=audit,
                                   numerical_connection=numerical)
    except Exception:
        # Unknown is not measured zero; numerical corruption is not core failure.
        return {"status": "unknown", "coverage": "registered_only", "periods": [],
                "cycle_links": [], "diagnostics": ["usage_unavailable"]}


def _directory(path, root):
    if not path_lexically_exists(path):
        try:
            create_physical_directory_exclusive(path, root=root)
        except Exception:
            # Another worker may have created the same physical directory.
            inspect_physical_directory(path, root=root)
    return inspect_physical_directory(path, root=root)


def _publish(path, document, root, *, immutable, guard=None):
    """Prepare bytes before admission; retain fresh readers only for adoption."""
    data = encode(document)
    identity = None
    same_bytes = False
    if path_lexically_exists(path):
        old, validated = read_physical_file_bounded(path, root=root, max_bytes=MAX_DOCUMENT_BYTES)
        same_bytes = old == data
        if same_bytes and guard is None:
            return
        if immutable and not same_bytes:
            raise UsageError()
        identity = validated.identity
    temporary = None if same_bytes else create_exclusive_durable_file(
        path.parent / (".usage-" + uuid4().hex + ".tmp"),
        data, root=root, max_bytes=MAX_DOCUMENT_BYTES)
    try:
        with guard if guard is not None else nullcontext():
            if same_bytes:
                # Earlier equal bytes are only an observation, never adoption.
                current, validated = read_physical_file_bounded(
                    path, root=root, max_bytes=MAX_DOCUMENT_BYTES)
                if validated.identity != identity or current != data:
                    raise UsageError()
            elif identity is None:
                rename_no_replace(temporary, path, root=root)
            else:
                _atomic_replace_temporary(temporary, path, root=root, maximum=MAX_DOCUMENT_BYTES,
                                          expected_destination=identity)
        temporary = None
    finally:
        if temporary is not None:
            with suppress(Exception):
                unlink_validated_file(temporary, root=root)


def refresh_usage(target, *, deadline=None):
    """Replayable internal worker for optional lifecycle events, not completion.

    A separate zero-wait publication lock serializes index adoption. Capture
    fixes index metadata; separate admitted reads validate each snapshot before
    its bytes are published and progress is saved. A fresh numerical/core guard
    fences index replacement. Earlier history remains subject to the disclosed
    deferred audit across events. Original Evidence and Viewer maintenance are
    not involved. Failures preserve last-good index and return only a fixed
    diagnostic, never a request to repeat completion.
    """
    try:
        repository = repository_for(target)
        if deadline is not None:
            repository.deadline = deadline
        @contextmanager
        def core_reader():
            # Every capture and final guard has a fresh admitted snapshot.
            # Selected ownership/cycle/review validation belongs to the
            # repository's change consumer, not every phase's core admission.
            with closing(connect_initialized_readonly(target, deadline=deadline, operation_local=True)) as core:
                yield core
        # Admit both stores before creating projection directories. No migration.
        with repository.connection():
            pass
        with closing(connect_initialized_readonly(target, deadline=deadline, operation_local=True)):
            pass
        root = target.resolved_usage_root
        _directory(root, target.db_path.parent)
        with zero_wait_artifact_lock(target.resolved_usage_lock):
            index, state = repository.prepare_publication(core_reader)
            _directory(target.resolved_usage_snapshots, root)
            for identity in index["snapshots"][state["position"]:]:
                if deadline is not None:
                    repository.check_budget()
                item = repository.publication_snapshot(identity)
                _publish(target.resolved_usage_snapshots / (identity + ".json"), item, root, immutable=True)
                state = repository.advance_publication(state)
            if deadline is not None:
                repository.check_budget()
            _publish(target.resolved_usage_index, index, root, immutable=False,
                     guard=repository.publication_guard(core_reader, index, state))
            repository.advance_publication(state, published=True)
        return {"status": "pending", "publication": "current", "diagnostics": ["evidence_verification_deferred"]}
    except Exception:
        return {"status": "unknown", "publication": "pending", "diagnostics": ["usage_unavailable"]}
