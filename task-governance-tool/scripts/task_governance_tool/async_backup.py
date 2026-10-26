"""One-shot detached backup startup and worker lifecycle.

No recurring timer, task queue or completion coupling. The short parent-child
GO handshake prevents a failed/abandoned launch from beginning backup effects.
The worker lock survives the parent, and the existing publication lock also
excludes Setup. Durable generations remain owned by the backup repository.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from task_governance_tool.artifact_lock import ArtifactLockError, zero_wait_artifact_lock
from task_governance_tool.async_backup_state import (
    lock_owned, mark_uncertain_launch, read_record, runtime_root, uncertain_launch,
    write_record, observe_runtime, STATE_LOCK_SECONDS,
)
from task_governance_tool.backup import managed_backup_due, managed_backup_lock
from task_governance_tool.state_resolver import backup_runtime_target, canonical_state_paths
from task_governance_tool.state_paths import StatePathError
from task_governance_tool.storage import StorageError, utc_now

STARTUP_SECONDS = 2.0


@dataclass(frozen=True)
class BackupLaunchResult:
    code: str


def _spawn(target, nonce):
    entry = Path(__file__).resolve().parents[1] / "backup_worker.py"
    options = dict(stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True, bufsize=0,
                   cwd=str(target.project.canonical_repo))
    if os.name == "nt":
        options["creationflags"] = (subprocess.DETACHED_PROCESS
                                    | subprocess.CREATE_NEW_PROCESS_GROUP
                                    | subprocess.CREATE_NO_WINDOW)
    else:
        options["start_new_session"] = True
    return subprocess.Popen([sys.executable, "-I", "-S", "-B", str(entry),
                             str(target.project.canonical_repo), nonce], **options)


def _stop_unstarted(process) -> bool:
    """Only the actual child handle, never a persisted PID, may be stopped."""
    with suppress(Exception):
        process.stdin.close()
    try:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=0.25)
        return True
    except Exception:
        try:
            process.kill()
            process.wait(timeout=0.5)
            return True
        except Exception:
            return process.poll() is not None


def _same_attempt(root, nonce, *, deadline=None):
    deadline = time.monotonic() + STATE_LOCK_SECONDS if deadline is None else deadline
    record = read_record(root, deadline=deadline)
    if record is None:
        raise StorageError("backup_runtime_unavailable", "backup runtime is unavailable")
    if record["attempt_id"] != nonce:
        raise StorageError("backup_attempt_changed", "backup attempt changed")
    return record


def launch_backup(target, *, observed_at=None, _spawn_child=None,
                  _startup_seconds=STARTUP_SECONDS, _admit_launch=None) -> BackupLaunchResult:
    """Service-owned dispatch of one opportunity; never waits for a backup."""
    from task_governance_tool.async_backup_repository import read_control
    if not target.canonical_fixed:
        # Alternate paths exist only for internal fixture injection, not runtime.
        return BackupLaunchResult("not_opted_in")
    process = None
    try:
        # Inspect existing ownership before any DB read. Busy workers do not
        # cause repeated admission, requests or subprocesses on every update.
        root = runtime_root(target)
        if lock_owned(root / "worker.lock") is True:
            return BackupLaunchResult("running")
        control = read_control(target)
        from task_governance_tool.debug_log import allow_output
        allow_output()
        if not control.maintenance.enabled:
            return BackupLaunchResult("not_opted_in")
        timestamp = observed_at or utc_now()
        recovery = observe_runtime(target, observed_at=timestamp,
                                   schema_version=control.schema_version)["state"] in {
                                       "interrupted", "failed", "deferred"}
        if not recovery and not managed_backup_due(control.maintenance, observed_at=timestamp):
            return BackupLaunchResult("current")
        root = runtime_root(target, create=True)
        with zero_wait_artifact_lock(root / "launch.lock", require_single_link=True):
            if uncertain_launch(root, clear_finished=True):
                return BackupLaunchResult("unknown")
            owned = lock_owned(root / "worker.lock")
            if owned is not False:
                return BackupLaunchResult("running" if owned else "unknown")
            # Recheck metadata after serialized launch admission, then close it
            # before process work. Setup may have published since the first read.
            control = read_control(target)
            if not control.maintenance.enabled:
                return BackupLaunchResult("not_opted_in")
            if not recovery and not managed_backup_due(control.maintenance, observed_at=timestamp):
                return BackupLaunchResult("current")
            if _admit_launch is not None and _admit_launch() is not True:
                return BackupLaunchResult("deferred")
            nonce = uuid4().hex
            record = dict(v=1, attempt_id=nonce, state="starting", started_at=timestamp,
                          captured_at=None, published_at=None, generation_id=None,
                          project_id=target.project.project_id,
                          binding_path_hash=target.binding_path_hash,
                          binding_generation=target.binding_generation,
                          schema_version=control.schema_version,
                          database_identity=list(control.database_identity))
            write_record(root, record)
            try:
                process = (_spawn_child or _spawn)(target, nonce)
                deadline = time.monotonic() + _startup_seconds
                while time.monotonic() < deadline:
                    try:
                        current = _same_attempt(root, nonce, deadline=deadline)
                    except (StorageError, StatePathError) as exc:
                        if isinstance(exc, StorageError) and exc.code != "backup_runtime_unavailable":
                            raise
                        # A brief state-lock contention or missing observation
                        # is not a different nonce. Share the original startup
                        # deadline; no GO or process action is repeated.
                        current = None
                    if time.monotonic() >= deadline:
                        break
                    if current is not None and current["state"] == "running":
                        if process.poll() is not None:
                            break
                        process.stdin.write(b"G")
                        process.stdin.close()
                        # Worker owns every further state and backup effect.
                        process = None
                        return BackupLaunchResult("started")
                    if process.poll() is not None:
                        break
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            except Exception:
                pass
            stopped = process is None or _stop_unstarted(process)
            process = None
            if not stopped:
                # Do not race a still-live child writing state.json.
                mark_uncertain_launch(root, nonce)
                return BackupLaunchResult("unknown")
            current = _same_attempt(root, nonce)
            write_record(root, dict(current, state="failed", published_at=None))
            return BackupLaunchResult("failed")
    except ArtifactLockError as exc:
        return BackupLaunchResult("running" if exc.contended else "failed")
    except Exception:
        return BackupLaunchResult("failed")
    finally:
        if process is not None:
            _stop_unstarted(process)


def worker_main(repo: Path, nonce: str, *, skill_root: Path, go_stream=None) -> int:
    """Internal entry. A launch token alone grants no backup side effect."""
    from task_governance_tool.debug_log import operation_scope
    with operation_scope(repo, entrypoint=skill_root / "scripts/backup_worker.py",
                         operation="backup_worker", repo_explicit=True, independent=True):
        return _worker_main(repo, nonce, skill_root=skill_root, go_stream=go_stream)


def _worker_main(repo: Path, nonce: str, *, skill_root: Path, go_stream=None) -> int:
    from task_governance_tool.async_backup_capture import run_backup
    from task_governance_tool.async_backup_repository import read_control
    root = canonical_state_paths(skill_root, repo=repo).backup_runtime
    try:
        # No lock file is created until an existing matching request and its
        # physical canonical parent chain have been validated.
        from task_governance_tool.state_paths import inspect_physical_directory
        inspect_physical_directory(root, root=Path(repo))
        initial = _same_attempt(root, nonce)
        if initial["state"] != "starting":
            return 2
        with zero_wait_artifact_lock(root / "worker.lock", require_single_link=True):
            record = _same_attempt(root, nonce)
            if record["state"] != "starting":
                return 2
            target = backup_runtime_target(
                skill_root, repo=repo, project_id=record["project_id"],
                binding_path_hash=record["binding_path_hash"],
                binding_generation=record["binding_generation"])
            runtime_root(target)
            write_record(root, dict(record, state="running"))
            try:
                if (go_stream or sys.stdin.buffer).read(1) != b"G":
                    raise StorageError("backup_start_abandoned", "backup start was abandoned")
                record = _same_attempt(root, nonce)
                with managed_backup_lock(target):
                    control = read_control(target)
                    if (control.schema_version != record["schema_version"]
                            or list(control.database_identity) != record["database_identity"]):
                        raise StorageError("backup_basis_changed", "backup basis changed")
                    # Reuse this existing admitted basis; logging adds no DB read.
                    from task_governance_tool.debug_log import allow_output
                    allow_output()
                    def captured(at, generation):
                        current = _same_attempt(root, nonce)
                        write_record(root, dict(current, captured_at=at, generation_id=generation))
                    result = run_backup(target, captured_callback=captured, attempt_id=nonce,
                                        expected_control=control)
                current = _same_attempt(root, nonce)
                if result.code == "succeeded":
                    write_record(root, dict(current, state="succeeded",
                                            generation_id=result.generation_id,
                                            captured_at=result.captured_at,
                                            published_at=result.published_at))
                    return 0
                if result.code in {"current", "not_opted_in"}:
                    write_record(root, dict(current, state="idle", captured_at=None,
                                            generation_id=None, published_at=None))
                    return 0
                write_record(root, dict(current, state="deferred" if result.code in {
                    "deferred"} else "failed"))
                return 2
            except Exception as exc:
                code = "deferred" if isinstance(exc, StorageError) and exc.code in {
                    "backup_lock_contended", "database_busy"} else "failed"
                with suppress(Exception):
                    current = _same_attempt(root, nonce)
                    write_record(root, dict(current, state=code, published_at=None))
                return 2
    except Exception:
        return 2
