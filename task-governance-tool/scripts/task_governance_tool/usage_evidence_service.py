"""Numerical-only replay/publication and non-blocking Task-detail projection.

No public command, ownership permission, hook install or implicit setup. The
internal worker consumes a resolver-admitted target, never caller-supplied IDs.
"""

from contextlib import closing, suppress
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
from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository
from task_governance_tool.usage_values import UsageError


def repository_for(target):
    return UsageEvidenceRepository(target.resolved_usage_database, target.project.project_id,
                                   target.binding_path_hash, target.binding_generation)


def read_task_usage(target, core, task_id, *, audit=False):
    try:
        return repository_for(target).read(core, task_id=task_id, audit=audit)
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


def _publish(path, document, root, *, immutable):
    data = encode(document)
    identity = None
    if path_lexically_exists(path):
        old, validated = read_physical_file_bounded(path, root=root, max_bytes=MAX_DOCUMENT_BYTES)
        if old == data:
            return
        if immutable:
            raise UsageError()
        identity = validated.identity
    temporary = create_exclusive_durable_file(path.parent / (".usage-" + uuid4().hex + ".tmp"),
                                               data, root=root, max_bytes=MAX_DOCUMENT_BYTES)
    try:
        if identity is None:
            rename_no_replace(temporary, path, root=root)
        else:
            _atomic_replace_temporary(temporary, path, root=root, maximum=MAX_DOCUMENT_BYTES,
                                      expected_destination=identity)
        temporary = None
    finally:
        if temporary is not None:
            with suppress(Exception):
                unlink_validated_file(temporary, root=root)


def refresh_usage(target):
    """Replayable internal worker for later lifecycle integration, not completion.

    A separate zero-wait publication lock serializes index adoption. Snapshot
    bytes and links are read from one numerical snapshot; original Evidence
    files and Viewer maintenance are not involved. Failures preserve last-good
    index and return only a fixed diagnostic, never a request to repeat completion.
    """
    try:
        repository = repository_for(target)
        # Admit both stores before creating projection directories. No migration.
        with repository.connection():
            pass
        with closing(connect_initialized_readonly(target)):
            pass
        root = target.resolved_usage_root
        _directory(root, target.db_path.parent)
        with zero_wait_artifact_lock(target.resolved_usage_lock):
            repository.refresh(lambda: closing(connect_initialized_readonly(target)))
            with closing(connect_initialized_readonly(target)) as core:
                projection = repository.read(core, audit=True)
            _directory(target.resolved_usage_snapshots, root)
            for item in projection["snapshots"]:
                _publish(target.resolved_usage_snapshots / (item["snapshot_id"] + ".json"), item, root, immutable=True)
            index = {**projection, "snapshots": [item["snapshot_id"] for item in projection["snapshots"]]}
            _publish(target.resolved_usage_index, index, root, immutable=False)
        return {"status": "pending", "publication": "current", "diagnostics": []}
    except Exception:
        return {"status": "unknown", "publication": "pending", "diagnostics": ["usage_unavailable"]}
