"""Explicit, synchronous protection publication outside ordinary rotation.

Only this maintenance entry creates protection artifacts. A failed private
stage is retained without a manifest; it is never a successful capture.
"""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import secrets

from . import protection_repository as repository
from .backup import _filename
from .debug_log import allow_output, record_exception, span
from .no_replace import rename_no_replace
from .state_paths import (create_physical_directory_exclusive, inspect_physical_directory,
    inspect_physical_file, path_lexically_exists, hash_physical_file,
    create_exclusive_durable_file, copy_physical_file_exclusive,
    remove_explicit_files_and_directories)
from .storage import utc_now


FORMAT = "taskgov-protection-v2"
MAX_MANIFEST_BYTES = 16384


def _bytes(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _copy(source, destination, root):
    sealed = hash_physical_file(source, root=root)
    return copy_physical_file_exclusive(sealed, destination, source_root=root,
                                        destination_root=root, max_bytes=sealed.identity.size)


def _component(path, version, started, finished, identity, root):
    repository.fsync_copy(path)
    sealed = hash_physical_file(path, root=root)
    return {"presence": "present", "filename": path.name, "schema_version": version,
        "capture_started_at": started, "capture_finished_at": finished,
        "source_identity": {"device": identity[0], "inode": identity[1]},
        "bytes": sealed.identity.size, "sha256": sealed.sha256, "validation": "passed"}, sealed


def _same_source(target, core_identity, usage_identity):
    if repository.source_identity(target.db_path) != core_identity:
        raise repository.failure()
    usage = target.resolved_usage_database
    if usage_identity is None:
        if path_lexically_exists(usage) or any(path_lexically_exists(Path(str(usage) + suffix))
                                             for suffix in ("-journal", "-wal", "-shm")):
            raise repository.failure()
    elif repository.source_identity(usage) != usage_identity:
        raise repository.failure()
    else:
        # The independent store can be rebound/migrated in place. An unchanged
        # inode alone cannot establish compatible schema and binding.
        with repository.repository_for(target).connection():
            pass
        if repository.source_identity(usage) != usage_identity:
            raise repository.failure()


def protect(target, root, *, package_basis, read_only=False, publication_guard=lambda: None):
    """The caller supplies the admitted resolver path (internal injection in tests)."""
    boundary = root.parent
    inspect_physical_directory(boundary)
    if path_lexically_exists(root):
        inspect_physical_directory(root, root=boundary)
    control = repository.capture.read_control(target)
    usage_present = path_lexically_exists(target.resolved_usage_database)
    usage_identity = None
    if usage_present:
        usage_identity = repository.source_identity(target.resolved_usage_database)
    _same_source(target, control.database_identity, usage_identity)
    data = {"status": "preview", "accepted": True, "completed": False,
        "capture_id": None, "location": None, "components": {"core": "planned",
        "usage": "planned" if usage_present else "absent"}, "recovery": "not_attempted",
        "usage_aggregation": "not_attempted",
        "coverage": "independent_snapshots", "live_restore": "not_performed"}
    if read_only:
        return data
    if not path_lexically_exists(root):
        create_physical_directory_exclusive(root, root=boundary)
    root_identity = inspect_physical_directory(root, root=boundary)
    capture_id = "tg_protection_" + secrets.token_hex(16)
    destination = root / capture_id
    if path_lexically_exists(destination):
        raise repository.failure()
    stage = root / (".pending-" + capture_id)
    staged = create_physical_directory_exclusive(stage, root=root)
    # Every path is generated here; callers cannot select sources or outputs.
    core = stage / "core.sqlite"
    with span(repository.copy_core):
        metadata, version, started, finished, core_identity = repository.copy_core(target, core)
    if core_identity != control.database_identity:
        raise repository.failure()
    sealed_core = stage / _filename(metadata)
    rename_no_replace(hash_physical_file(core, root=root), sealed_core, root=root)
    components = {}
    components["core"], core_file = _component(sealed_core, version, started, finished, core_identity, root)
    usage = None
    usage_file = None
    if usage_present:
        usage = stage / "taskgov-usage.sqlite"
        with span(repository.copy_usage):
            usage_version, started, finished, copied_identity = repository.copy_usage(target, usage)
        if copied_identity != usage_identity:
            raise repository.failure()
        components["usage"], usage_file = _component(usage, usage_version, started, finished, usage_identity, root)
    else:
        components["usage"] = {"presence": "absent"}
    inbox = stage / "recovery"
    create_physical_directory_exclusive(inbox, root=root)
    backups = inbox / "backups"
    create_physical_directory_exclusive(backups, root=root)
    _copy(sealed_core, backups / sealed_core.name, root)
    with span(repository.rehearse):
        proof = repository.rehearse(target, inbox, usage)
    # Only the explicit, tool-owned inbox entries are removed. Unknown residue
    # makes cleanup fail closed and leaves an unpublished stage for diagnosis.
    paths = [backups / sealed_core.name, inbox / "taskgov.sqlite"]
    if usage is not None:
        paths.append(inbox / "taskgov-usage.sqlite")
    with span(remove_explicit_files_and_directories):
        remove_explicit_files_and_directories(root=root,
            files=[hash_physical_file(path, root=root) for path in paths],
            directories_deepest_first=[inspect_physical_directory(backups, root=root),
                                       inspect_physical_directory(inbox, root=root)])
    if (hash_physical_file(sealed_core, root=root) != core_file
            or usage_file is not None and hash_physical_file(usage, root=root) != usage_file):
        raise repository.failure()
    with span(_same_source):
        _same_source(target, core_identity, usage_identity)
    current = repository.capture.read_control(target)
    if (current.database_identity != core_identity
            or current.binding != control.binding or current.schema_version != control.schema_version
            or current.schema_cookie != control.schema_cookie):
        raise repository.failure()
    with span(publication_guard):
        publication_guard()
    verification = {"format": FORMAT, "capture_id": capture_id, "checked_at": utc_now(), **proof}
    verified = create_exclusive_durable_file(stage / "verification.json", _bytes(verification),
                                            root=root, max_bytes=MAX_MANIFEST_BYTES)
    manifest = {"format": FORMAT, "capture_id": capture_id, "project_id": target.project.project_id,
        "binding_hash": target.binding_path_hash, "binding_generation": target.binding_generation,
        "package_basis": package_basis, "validation_rules": 2,
        "usage_aggregation": proof["usage_aggregation"],
        "coverage": "independent_snapshots", "published_at": utc_now(),
        "generation": asdict(metadata), "components": components,
        "verification": {"filename": "verification.json", "sha256": verified.sha256},
        "excluded": ["operational_stores", "host_effects", "later_writes"], "live_restore": "not_performed"}
    # Manifest is the last staged write. Directory publication is no-replace.
    create_exclusive_durable_file(stage / "manifest.json", _bytes(manifest), root=root,
                                  max_bytes=MAX_MANIFEST_BYTES)
    if inspect_physical_directory(root, root=boundary) != root_identity:
        raise repository.failure()
    with span(rename_no_replace):
        rename_no_replace(staged, destination, root=root)
    return {**data, "status": "succeeded", "completed": True, "capture_id": capture_id,
        "location": ".taskgov/protection/" + capture_id,
        "components": {"core": "protected", "usage": "protected" if usage_present else "absent"},
        "recovery": "passed", "usage_aggregation": proof["usage_aggregation"]}


def run_protection(context, script_path):
    """Public preflight and sanitized output; no ordinary maintenance dispatch."""
    from .cli_output import CommandResult
    from .project_scope import inspect_project_scope
    from .state_resolver import resolve_project_state, canonical_state_paths, consumer_error_code
    from .sqlite_connection import StorageError
    project_id = None
    accepted = False
    try:
        scope = inspect_project_scope(repo=context.repo, repo_explicit=context.repo_explicit, script_path=script_path)
        issue = scope.first_issue()
        if issue is not None or scope.scope is None:
            raise StorageError(issue.code if issue else "unsupported_install_layout",
                               issue.message if issue else "project layout is unavailable")
        resolution = resolve_project_state(skill_root=scope.scope.skill_root, repo=scope.scope.canonical_repo)
        error = consumer_error_code(resolution)
        if error or resolution.target is None:
            raise StorageError(error or "project_state_unreadable", "project state is unavailable")
        target = resolution.target
        project_id = target.project.project_id
        # Reuse the successful project/state admission. Preview stays entirely
        # write-free, and neither diagnostics nor a failed admission can repair
        # or admit state. The outer CLI scope owns close-before-flush.
        if not context.read_only:
            allow_output()
        paths = canonical_state_paths(scope.scope.skill_root, repo=scope.scope.canonical_repo)
        package = scope.scope.skill_root / "release-manifest.json"
        basis = hashlib.sha256(package.read_bytes()).hexdigest()
        def publication_guard():
            from . import __version__
            from .self_status import inspect_local_package
            status = inspect_local_package(scope.scope.skill_root, installed_version=__version__)
            if status.status in {"modified", "unknown"} or hashlib.sha256(package.read_bytes()).hexdigest() != basis:
                raise repository.failure()
        accepted = True
        with span(protect):
            data = protect(target, paths.protection, package_basis=basis, read_only=context.read_only,
                           publication_guard=publication_guard)
        return CommandResult(True, "backup.protect", data, project_id,
            text="Protection preview; no writes performed." if context.read_only else
                 "Protection and isolated recovery completed: " + data["location"])
    except Exception as exc:
        record_exception(exc)
        code = exc.code if isinstance(exc, StorageError) and exc.code in {
            "database_busy", "db_not_initialized", "migration_required", "schema_too_new",
            "project_state_unreadable", "project_relocation_required", "unsupported_journal_mode",
            "unsupported_install_layout", "package_core_modified", "package_status_unknown", "state_ignore_required",
            "state_path_invalid", "invalid_project_root", "project_root_uninspectable", "unsupported_python"} else "protection_failed"
        return CommandResult(False, "backup.protect",
            {"status": "failed", "accepted": accepted, "completed": False,
             "recovery": "not_confirmed", "usage_aggregation": "not_confirmed",
             "private_stage": "may_remain", "live_restore": "not_performed"},
            project_id, errors=[{"code": code, "message": "database protection could not be completed"}],
            text="Database protection failed; no complete protection is confirmed.", exit_code=2)
