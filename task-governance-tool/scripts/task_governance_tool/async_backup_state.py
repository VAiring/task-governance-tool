"""Bounded asynchronous-backup observation, separate from durable generations.

The launcher owns writes while no worker exists. After spawning, only the
worker writes until its actual process exit is confirmed. Every caller holds
the corresponding OS lock; a saved label or PID never proves ownership.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from contextlib import contextmanager, suppress
from errno import EACCES, EAGAIN, EDEADLK
from pathlib import Path

from task_governance_tool.artifact_lock import _acquire, _release
from task_governance_tool.state_paths import (
    StatePathError, create_exclusive_durable_file, inspect_physical_directory,
    inspect_physical_file, path_lexically_exists, read_physical_file_bounded, _is_reparse,
)
from task_governance_tool.state_resolver import canonical_state_paths
from task_governance_tool.storage import StorageError, utc_now, validate_utc_timestamp

MAX_STATE_BYTES = 8192
STATE_LOCK_SECONDS = 0.5
_FIELDS = frozenset(("v", "attempt_id", "state", "started_at", "captured_at",
                     "published_at", "generation_id", "project_id",
                     "binding_path_hash", "binding_generation", "schema_version",
                     "database_identity"))
_STATES = {"idle", "starting", "running", "succeeded", "failed", "deferred"}
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _failure():
    return StorageError("backup_runtime_unavailable", "backup runtime is unavailable")


def runtime_root(target, *, create=False) -> Path:
    if not target.canonical_fixed or target.skill_root is None:
        raise _failure()
    paths = canonical_state_paths(target.skill_root, repo=target.project.canonical_repo)
    if paths.database != target.db_path or paths.backups != target.resolved_backups_path:
        raise _failure()
    inspect_physical_directory(paths.fixed_root, root=target.project.canonical_repo)
    for directory in (paths.backups, paths.backup_runtime):
        if create and not path_lexically_exists(directory):
            try:
                directory.mkdir()
            except FileExistsError:
                pass
        if path_lexically_exists(directory):
            inspect_physical_directory(directory, root=paths.fixed_root)
        elif not create:
            return paths.backup_runtime
    return paths.backup_runtime


def _regular(path: Path, maximum: int):
    _, identity = inspect_physical_file(path, root=path.parent, max_bytes=maximum)
    if path.lstat().st_nlink != 1:
        raise _failure()
    return identity


@contextmanager
def _state_guard(root: Path, *, create: bool = False, deadline: float | None = None):
    """Own only the short state-file I/O interval; observers never create it.

The caller closes every state reader before releasing this lock. Ordinary
rename therefore never overlaps a cooperating reader's Windows file handle.
Only lock contention is retried, up to the caller's absolute deadline; unsafe
paths and I/O errors are not retried. No DB or process wait belongs here.
"""
    directory = inspect_physical_directory(root)
    path = root / "state.lock"
    descriptor = None
    acquired = False
    try:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT
        descriptor = os.open(path, flags, 0o600)
        opened = os.fstat(descriptor)
        observed = _regular(path, 1)
        if (_is_reparse(opened) or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1 or opened.st_size not in ({0, 1} if create else {1})
                or (opened.st_dev, opened.st_ino) != (observed.device, observed.inode)):
            raise _failure()
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise _failure()
            try:
                _acquire(descriptor)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {EACCES, EAGAIN, EDEADLK}:
                    raise
                remaining = 0 if deadline is None else deadline - time.monotonic()
                if remaining <= 0:
                    raise _failure() from exc
                time.sleep(min(0.01, remaining))
        observed = _regular(path, 1)
        if (observed.size != 1
                or (observed.device, observed.inode) != (opened.st_dev, opened.st_ino)
                or inspect_physical_directory(root).identity != directory.identity):
            raise _failure()
        yield directory
        after = _regular(path, 1)
        if (after != observed
                or inspect_physical_directory(root).identity != directory.identity):
            raise _failure()
    except OSError as exc:
        raise _failure() from exc
    finally:
        if descriptor is not None:
            if acquired:
                with suppress(OSError):
                    _release(descriptor)
            os.close(descriptor)


def _read_runtime_bytes(path: Path, maximum: int) -> bytes:
    """Called only under state.lock; common physical reading closes its fd."""
    before = _regular(path, maximum)
    data, observed = read_physical_file_bounded(path, root=path.parent, max_bytes=maximum)
    if observed.identity != before or _regular(path, maximum) != before:
        raise _failure()
    return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _failure()
        result[key] = value
    return result


def validate_record(record: object) -> dict:
    if type(record) is not dict or set(record) != _FIELDS:
        raise _failure()
    if (type(record["v"]) is not int or record["v"] != 1
            or type(record["attempt_id"]) is not str
            or not _TOKEN.fullmatch(record["attempt_id"])
            or type(record["state"]) is not str or record["state"] not in _STATES
            or type(record["project_id"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", record["project_id"])
            or type(record["binding_path_hash"]) is not str
            or not _HASH.fullmatch(record["binding_path_hash"])):
        raise _failure()
    for key in ("binding_generation", "schema_version"):
        if type(record[key]) is not int or not 1 <= record[key] < 2**63:
            raise _failure()
    identity = record["database_identity"]
    if (type(identity) is not list or len(identity) != 2
            or any(type(value) is not int or value < 0 for value in identity)):
        raise _failure()
    for key in ("started_at", "captured_at", "published_at"):
        if record[key] is None and key != "started_at":
            continue
        validate_utc_timestamp(record[key], field="backup runtime observation")
    generation = record["generation_id"]
    if generation is not None and (type(generation) is not str or not re.fullmatch(
            r"tg_backup_[0-9a-f]{32}", generation)):
        raise _failure()
    if record["state"] == "starting" and any(record[k] is not None for k in (
            "captured_at", "published_at", "generation_id")):
        raise _failure()
    if record["published_at"] is not None and record["state"] != "succeeded":
        raise _failure()
    if record["state"] == "succeeded" and any(record[k] is None for k in (
            "captured_at", "published_at", "generation_id")):
        raise _failure()
    if (record["captured_at"] is None) != (generation is None):
        raise _failure()
    return record


def _read_record_locked(root: Path) -> dict | None:
    path = root / "state.json"
    if not path_lexically_exists(path):
        return None
    data = _read_runtime_bytes(path, MAX_STATE_BYTES)
    try:
        return validate_record(json.loads(data.decode("ascii"), object_pairs_hook=_unique))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise _failure() from exc


def read_record(root: Path, *, deadline: float | None = None) -> dict | None:
    """Observe with an existing lock only; the default is strictly zero-wait."""
    if not path_lexically_exists(root):
        return None
    inspect_physical_directory(root)
    if (not path_lexically_exists(root / "state.lock")
            and not path_lexically_exists(root / "state.json")):
        return None
    with _state_guard(root, deadline=deadline):
        return _read_record_locked(root)


def write_record(root: Path, record: dict, *, deadline: float | None = None) -> None:
    """Publish one fixed temporary, under ownership and a short state lock."""
    validate_record(record)
    deadline = time.monotonic() + STATE_LOCK_SECONDS if deadline is None else deadline
    with _state_guard(root, create=True, deadline=deadline) as directory:
        path, temporary = root / "state.json", root / ".state-next.json"
        if path_lexically_exists(path):
            _regular(path, MAX_STATE_BYTES)
        if path_lexically_exists(temporary):
            # Only this exact bounded operational temporary is crash residue.
            # Old unreleased ~RF artifacts are unknown and are never removed.
            _regular(temporary, MAX_STATE_BYTES)
            temporary.unlink()
        data = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        create_exclusive_durable_file(temporary, data, root=root, max_bytes=MAX_STATE_BYTES)
        if inspect_physical_directory(root).identity != directory.identity:
            raise _failure()
        if path_lexically_exists(path):
            _regular(path, MAX_STATE_BYTES)
        os.replace(temporary, path)


def uncertain_launch(root: Path, *, clear_finished=False, deadline: float | None = None) -> bool:
    """A child whose termination was unconfirmed must not multiply on retry."""
    path = root / ".launch-uncertain"
    if not path_lexically_exists(path):
        return False
    if clear_finished and deadline is None:
        deadline = time.monotonic() + STATE_LOCK_SECONDS
    with _state_guard(root, deadline=deadline):
        # Avoid recursively acquiring the non-reentrant lock through read_record.
        value = _read_runtime_bytes(path, 32)
        nonce = value.decode("ascii")
        if not _TOKEN.fullmatch(nonce):
            raise _failure()
        record = _read_record_locked(root)
        finished = (record is not None and record["attempt_id"] == nonce
                    and record["state"] in {"idle", "failed", "deferred", "succeeded"}
                    and lock_owned(root / "worker.lock") is False)
        if finished and clear_finished:
            path.unlink()
        return not finished


def mark_uncertain_launch(root: Path, nonce: str) -> None:
    # The parent holds launch.lock. This immutable barrier must not wait for
    # state.lock, which the very child whose exit is unknown may still own.
    create_exclusive_durable_file(root / ".launch-uncertain", nonce.encode("ascii"),
                                  root=root, max_bytes=32)


def lock_owned(path: Path) -> bool | None:
    """Observe an existing OS lock without creating any filesystem entry."""
    descriptor = None
    acquired = False
    try:
        if not path_lexically_exists(path):
            return False
        before = _regular(path, 1)
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        actual = os.fstat(descriptor)
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                or (actual.st_dev, actual.st_ino) != (before.device, before.inode)):
            return None
        try:
            _acquire(descriptor)
            acquired = True
        except OSError as exc:
            if exc.errno not in {EACCES, EAGAIN, EDEADLK}:
                return None
        after = _regular(path, 1)
        if (after.device, after.inode) != (before.device, before.inode):
            return None
        return not acquired
    except (OSError, StatePathError, StorageError):
        return None
    finally:
        if descriptor is not None:
            if acquired:
                with suppress(OSError):
                    _release(descriptor)
            os.close(descriptor)


def observe_runtime(target, *, observed_at=None, schema_version=None) -> dict:
    result = dict(state="unknown", observed_at=observed_at or utc_now(),
                  attempt_id=None, captured_at=None, published_at=None, generation_id=None)
    try:
        root = runtime_root(target)
        if uncertain_launch(root):
            return result
        first = read_record(root)
        worker = lock_owned(root / "worker.lock")
        launcher = lock_owned(root / "launch.lock")
        second = read_record(root)
        if first != second or worker is None or launcher is None:
            return result
        if first is None:
            result["state"] = "unknown" if worker or launcher else "idle"
            return result
        if (first["project_id"] != target.project.project_id
                or first["binding_path_hash"] != target.binding_path_hash
                or first["binding_generation"] != target.binding_generation
                or (schema_version is not None and first["schema_version"] != schema_version)):
            return result
        from task_governance_tool.backup_metadata_repository import _backup_database_identity
        if list(_backup_database_identity(target)) != first["database_identity"]:
            return result
        state = first["state"]
        if state == "running" and not worker:
            state = "interrupted"
        elif state == "starting" and not launcher:
            state = "interrupted" if not worker else "unknown"
        elif state not in {"starting", "running"} and (worker or launcher):
            state = "unknown"
        result.update(state=state, attempt_id=first["attempt_id"],
                      captured_at=first["captured_at"], published_at=first["published_at"],
                      generation_id=first["generation_id"])
    except (OSError, StatePathError, StorageError, ValueError, TypeError):
        pass
    return result
