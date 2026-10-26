"""One bounded backup opportunity shared with the existing project service.

The publisher performs no database access or process work. A requested result
means publication only; the service owns admission and any worker startup.
All cooperating file readers close before replacement under opportunity.lock.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from uuid import uuid4

from task_governance_tool.artifact_lock import ArtifactLockError, zero_wait_artifact_lock
from task_governance_tool.async_backup import BackupLaunchResult
from task_governance_tool.async_backup_state import lock_owned, runtime_root
from task_governance_tool.state_paths import (
    StatePathError, create_exclusive_durable_file, inspect_physical_directory,
    inspect_physical_file, path_lexically_exists, read_physical_file_bounded,
)
from task_governance_tool.storage import SCHEMA_VERSION, StorageError

MAX_OPPORTUNITY_BYTES = 512
PROTOCOL_VERSION = 1
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_FIELDS = frozenset({"v", "owner_id", "project_id", "binding_path_hash",
                     "binding_generation", "compatibility"})
_FILE_ERRORS = (OSError, StatePathError, StorageError, ValueError, TypeError)


def _failure():
    return StorageError("backup_opportunity_unavailable", "backup opportunity is unavailable")


def current_compatibility() -> dict:
    return {"protocol": PROTOCOL_VERSION, "schema_version": SCHEMA_VERSION}


def _compatibility(value):
    if (type(value) is not dict or set(value) != {"protocol", "schema_version"}
            or any(type(value[key]) is not int or not 0 < value[key] < 65536
                   for key in ("protocol", "schema_version"))):
        raise _failure()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _failure()
        result[key] = value
    return result


def _validate(value, *, request=False):
    fields = _FIELDS | {"request_id"} if request else _FIELDS
    if type(value) is not dict or set(value) != fields:
        raise _failure()
    if (type(value["v"]) is not int or value["v"] != 1
            or type(value["owner_id"]) is not str or not _TOKEN.fullmatch(value["owner_id"])
            or type(value["project_id"]) is not str or not _PROJECT.fullmatch(value["project_id"])
            or type(value["binding_path_hash"]) is not str
            or not _HASH.fullmatch(value["binding_path_hash"])
            or type(value["binding_generation"]) is not int
            or not 0 < value["binding_generation"] < 2**63):
        raise _failure()
    _compatibility(value["compatibility"])
    if request and (type(value["request_id"]) is not str
                    or not _TOKEN.fullmatch(value["request_id"])):
        raise _failure()
    return value


def _record(target, owner_id, compatibility):
    return _validate(dict(v=1, owner_id=owner_id,
                          project_id=target.project.project_id,
                          binding_path_hash=target.binding_path_hash,
                          binding_generation=target.binding_generation,
                          compatibility=dict(_compatibility(compatibility))))


def _regular(path):
    _, identity = inspect_physical_file(path, root=path.parent,
                                        max_bytes=MAX_OPPORTUNITY_BYTES)
    if path.lstat().st_nlink != 1:
        raise _failure()
    return identity


def _read(root, name, *, request=False):
    path = root / name
    if not path_lexically_exists(path):
        return None
    before = _regular(path)
    data, observed = read_physical_file_bounded(path, root=root,
                                               max_bytes=MAX_OPPORTUNITY_BYTES)
    if observed.identity != before or _regular(path) != before:
        raise _failure()
    try:
        return _validate(json.loads(data.decode("ascii"), object_pairs_hook=_unique),
                         request=request)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise _failure() from exc


def _same_binding(record, target):
    if record is not None and any(record[key] != value for key, value in (
            ("project_id", target.project.project_id),
            ("binding_path_hash", target.binding_path_hash),
            ("binding_generation", target.binding_generation))):
        raise _failure()


def _same_project(record, target):
    # A freshly admitted core binding is authority. Closed control left by an
    # earlier binding or restored generation is baseline only, never executable.
    if record is not None and record["project_id"] != target.project.project_id:
        raise _failure()


@contextmanager
def _guard(root, *, create=False):
    directory = inspect_physical_directory(root)
    if not create and not path_lexically_exists(root / "opportunity.lock"):
        raise _failure()
    with zero_wait_artifact_lock(root / "opportunity.lock", require_single_link=True):
        if inspect_physical_directory(root).identity != directory.identity:
            raise _failure()
        yield directory
        if inspect_physical_directory(root).identity != directory.identity:
            raise _failure()


def _write(root, name, temporary_name, record, directory):
    data = json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")
    if len(data) > MAX_OPPORTUNITY_BYTES:
        raise _failure()
    path, temporary = root / name, root / temporary_name
    if path_lexically_exists(temporary):
        # This exact reserved, bounded physical temporary is owned crash residue,
        # including partial writes. Unknown filenames and malformed finals remain.
        _regular(temporary)
        temporary.unlink()
    create_exclusive_durable_file(temporary, data, root=root,
                                  max_bytes=MAX_OPPORTUNITY_BYTES)
    if inspect_physical_directory(root).identity != directory.identity:
        raise _failure()
    if path_lexically_exists(path):
        _regular(path)
    os.replace(temporary, path)


@contextmanager
def service_lease(target):
    """Own service presence for this process; a saved record is not liveness."""
    root = runtime_root(target, create=True)
    with zero_wait_artifact_lock(root / "service.lock", require_single_link=True):
        yield uuid4().hex


def activate_service(target, owner_nonce: str, compatibility: dict) -> str | None:
    """Caller holds service_lease and freshly admits target before activation.

    Baseline existing same-project content without executing its old request.
    """
    root = runtime_root(target)
    record = _record(target, owner_nonce, compatibility)
    if lock_owned(root / "service.lock") is not True:
        raise _failure()
    with _guard(root, create=True) as directory:
        previous = _read(root, "service.json")
        baseline = _read(root, "opportunity.json", request=True)
        _same_project(previous, target)
        _same_project(baseline, target)
        _write(root, "service.json", ".service-next.json", record, directory)
        return baseline["request_id"] if baseline is not None else None


def observe_opportunity(target, owner_nonce: str, compatibility: dict, *,
                        after_request_id: str | None = None) -> dict | None:
    """Read one request under the file lock, without consuming it or reading DB."""
    root = runtime_root(target)
    expected = _record(target, owner_nonce, compatibility)
    with _guard(root):
        value = _read(root, "opportunity.json", request=True)
        _same_binding(value, target)
        if value is None or any(value[key] != expected[key] for key in _FIELDS):
            return None
        if value["request_id"] == after_request_id:
            return None
        if (_read(root, "service.json") != expected
                or lock_owned(root / "service.lock") is not True):
            raise _failure()
        return value


def request_backup(target) -> BackupLaunchResult:
    """Publish once for an already eligible mutation. No DB access or spawning."""
    if not target.canonical_fixed:
        return BackupLaunchResult("deferred")
    try:
        root = runtime_root(target)
        if lock_owned(root / "service.lock") is not True:
            return BackupLaunchResult("deferred")
        with _guard(root) as directory:
            try:
                service = _read(root, "service.json")
                if service is None or service != _record(
                        target, service["owner_id"], current_compatibility()):
                    return BackupLaunchResult("deferred")
            except _FILE_ERRORS:
                return BackupLaunchResult("deferred")
            worker = lock_owned(root / "worker.lock")
            if worker is not False:
                return BackupLaunchResult("running" if worker else "deferred")
            previous = _read(root, "opportunity.json", request=True)
            _same_project(previous, target)
            if lock_owned(root / "service.lock") is not True:
                return BackupLaunchResult("deferred")
            request = dict(service, request_id=uuid4().hex)
            _validate(request, request=True)
            _write(root, "opportunity.json", ".opportunity-next.json", request, directory)
            return BackupLaunchResult("requested")
    except ArtifactLockError as exc:
        return BackupLaunchResult("deferred" if exc.contended else "failed")
    except _FILE_ERRORS:
        return BackupLaunchResult("failed")
