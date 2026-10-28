"""Physical capture/publication for the asynchronous routine backup worker.

The caller holds ``backup.managed_backup_lock`` for the complete invocation.
SQLite transactions and control CAS operations are repository-owned. Existing
Setup, migration and explicit recovery continue to use ``backup.py``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from task_governance_tool import async_backup_repository as repository
from task_governance_tool import backup
from task_governance_tool.storage import DatabaseTarget, utc_now, validate_utc_timestamp
from task_governance_tool.state_paths import (
    create_exclusive_durable_file, inspect_physical_directory,
    inspect_physical_file, path_lexically_exists, read_physical_file_bounded,
)


@dataclass(frozen=True)
class BackupResult:
    code: str
    generation_id: str | None = None
    captured_at: str | None = None
    published_at: str | None = None


def _prune(target: DatabaseTarget, control: repository.BackupControl,
           artifacts: list[backup._Artifact]) -> repository.BackupControl:
    if not control.generations:
        return control
    retention = control.maintenance.applied_backup_generations
    if retention is None:
        raise repository.failure()
    by_id = {item.metadata.generation_id: item for item in artifacts}
    if len(control.generations) > retention:
        backup._require_retained_artifacts([by_id[item.generation_id]
            for item in control.generations[-retention:]])
    for item in control.generations[:-retention]:
        artifact = by_id.get(item.generation_id)
        if artifact is None or artifact.metadata != item:
            raise repository.failure()
        # Pin only the metadata/identity guard around the short file effect.
        with repository.control_snapshot(target, expected=control):
            backup._delete(artifact)
        control = repository.commit_inventory(
            target, control, tuple(row for row in control.generations if row != item),
        )
    return control


def _reconcile(target: DatabaseTarget, control: repository.BackupControl,
               timestamp: str) -> tuple[repository.BackupControl, list[backup._Artifact], bool]:
    # Each existing artifact is validated in its own private snapshot. The live
    # database is closed throughout discovery, validation and retention planning.
    directory = backup._directory(target, create=False)
    candidates = []
    generation_ids = set()
    if directory.exists():
        for path in directory.iterdir():
            item = backup._parse_filename(path.name)
            if item is not None:
                if item.generation_id in generation_ids:
                    raise repository.failure()
                generation_ids.add(item.generation_id)
                candidates.append((path, item))
                if len(candidates) > repository.MAX_GENERATIONS_WITH_RESIDUE:
                    raise repository.failure()
    from .backup_validation import cleanup_orphans
    cleanup_orphans(directory, project_id=target.project.project_id)
    artifacts = []
    for path, item in candidates:
        identity = backup._file_identity(path)
        if identity is not None and path.lstat().st_nlink == 1:
            observed = backup._artifact_observation(path, target, identity)
            if observed is not None:
                artifacts.append(backup._Artifact(path, item, identity, observed))
    artifacts.sort(key=lambda item: item.key)
    backup._validate_artifact_relationships(target, artifacts)
    by_id = {item.metadata.generation_id: item for item in artifacts}
    if len(by_id) != len(artifacts):
        # Conflicting physical tuples do not invalidate an existing managed row.
        # Reject the ambiguous set before reconciliation or pruning can write.
        raise repository.failure()
    row_ids = {item.generation_id for item in control.generations}
    file_only = tuple(item.metadata for item in artifacts if item.metadata.generation_id not in row_ids)
    if len(file_only) > 1:
        raise repository.failure()
    invalid = tuple(item for item in control.generations
                    if item.generation_id not in by_id or by_id[item.generation_id].metadata != item)
    if invalid:
        # Remove stale rows, never the invalid/untrusted physical paths. A
        # subsequent ordinary update supplies the next attempt, without a loop.
        control = repository.commit_inventory(
            target, control, tuple(item for item in control.generations if item not in invalid),
            failure_at=timestamp,
        )
        return control, artifacts, False
    expected = tuple(item.metadata for item in artifacts)
    latest = expected[-1] if expected else None
    pointer = (control.maintenance.latest_backup_generation_id,
               control.maintenance.backup_last_success_at,
               control.maintenance.applied_backup_generations)
    wanted_pointer = ((latest.generation_id, latest.published_at, latest.publication_retention)
                      if latest is not None else (None, None, None))
    if file_only or pointer != wanted_pointer:
        # A verified file-only generation proves interrupted atomic publication.
        # Recheck its identity before inserting any recovery metadata.
        backup._require_unchanged_artifacts(artifacts)
        control = repository.commit_inventory(target, control, expected, success=bool(file_only))
    control = _prune(target, control, artifacts)
    # Publication in this attempt must use the post-prune set. Retain the
    # existing observations for survivors; deleted generations are no longer
    # candidates for the new copy's embedded repository relationships.
    retained_ids = {item.generation_id for item in control.generations}
    return control, [item for item in artifacts
                     if item.metadata.generation_id in retained_ids], True


def _marker_bytes(attempt_id: str, project_id: str) -> bytes:
    if type(attempt_id) is not str or re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None:
        raise repository.failure()
    if type(project_id) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", project_id) is None:
        raise repository.failure()
    return json.dumps({"v": 1, "purpose": "async-backup-private-copy", "attempt_id": attempt_id,
                       "project_id": project_id},
                      sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


def _cleanup_scratch(root: Path, *, project_id: str, expected_attempt: str | None = None) -> None:
    """Remove only our fixed private copy, under its valid durable owner marker.

    The marker is created before the scratch and removed after it. A crash at
    either boundary therefore leaves at most one reclaimable private copy.
    Existing managed generations and legacy backup temporaries are untouched.
    """
    inspect_physical_directory(root)
    owner = root / "snapshot-owner.json"
    copied = root / "snapshot.sqlite"
    journal = root / "snapshot.sqlite-journal"
    if any(path_lexically_exists(root / name) for name in ("snapshot.sqlite-wal", "snapshot.sqlite-shm")):
        raise repository.failure()
    if not path_lexically_exists(owner):
        if path_lexically_exists(copied) or path_lexically_exists(journal):
            raise repository.failure()
        return
    raw, _ = read_physical_file_bounded(owner, root=root, max_bytes=512)
    if owner.lstat().st_nlink != 1:
        raise repository.failure()
    try:
        record = json.loads(raw.decode("ascii"))
        attempt_id = record["attempt_id"]
        if raw != _marker_bytes(attempt_id, project_id) or (expected_attempt is not None and attempt_id != expected_attempt):
            raise repository.failure()
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise repository.failure() from exc
    entries = []
    for path in (journal, copied, owner):
        if path_lexically_exists(path):
            _, identity = inspect_physical_file(path, root=root)
            if path.lstat().st_nlink != 1:
                raise repository.failure()
            entries.append((path, identity))
    for path, identity in entries:
        if inspect_physical_file(path, root=root)[1] != identity:
            raise repository.failure()
        path.unlink()


def _resolve_scratch_root(target: DatabaseTarget, root: Path | None) -> Path:
    if root is None:
        from task_governance_tool.async_backup_state import runtime_root
        root = runtime_root(target, create=True)
    else:
        # Explicit path injection belongs only to internal isolated fixtures.
        root.mkdir(parents=True, exist_ok=True)
        inspect_physical_directory(root)
    return root


def _scratch(target: DatabaseTarget, attempt_id: str, root: Path | None) -> tuple[Path, Path]:
    root = _resolve_scratch_root(target, root)
    _cleanup_scratch(root, project_id=target.project.project_id)
    create_exclusive_durable_file(root / "snapshot-owner.json", _marker_bytes(attempt_id, target.project.project_id),
                                  root=root, max_bytes=512)
    copied = root / "snapshot.sqlite"
    descriptor = os.open(copied, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(descriptor)
    return root, copied


def run_backup(target: DatabaseTarget, *,
               captured_callback: Callable[[str, str], None] | None = None,
               observed_at: str | None = None, attempt_id: str | None = None,
               expected_control: repository.BackupControl | None = None,
               _scratch_root: Path | None = None) -> BackupResult:
    """Run one due publication, already holding the shared artifact lock.

The callback receives ``(captured_at, generation_id)`` after the live source
closes and before expensive private-copy validation. Callback failure aborts
publication. A successful result means file publication AND metadata commit;
the result never claims that mutations after the capture are included.
An optional caller observation fences the attempt before any scratch recovery
or metadata effect; it is not replaced by a fresh observation of a different DB.
"""
    timestamp = validate_utc_timestamp(observed_at or utc_now(), field="backup observation time")
    control: repository.BackupControl | None = None
    temporary: Path | None = None
    generation_id: str | None = None
    captured_at: str | None = None
    published_at: str | None = None
    scratch_root: Path | None = None
    attempt_id = attempt_id or secrets.token_hex(16)
    try:
        observed = repository.read_control(target)
        if expected_control is not None:
            repository.require_same_control(observed, expected_control)
        control = observed
        if not control.maintenance.enabled:
            return BackupResult("not_opted_in")
        scratch_root = _resolve_scratch_root(target, _scratch_root)
        _cleanup_scratch(scratch_root, project_id=target.project.project_id)
        control, artifacts, consistent = _reconcile(target, control, timestamp)
        if not consistent:
            return BackupResult("failed")
        if not backup.managed_backup_due(control.maintenance, observed_at=timestamp):
            return BackupResult("current")
        directory = backup._directory(target, create=True)
        directory_identity = backup._directory_identity(directory)
        scratch_root, temporary = _scratch(target, attempt_id, _scratch_root)
        scratch_identity = backup._directory_identity(scratch_root)
        generation_id = "tg_backup_" + secrets.token_hex(16)
        control, captured_at = repository.copy_snapshot(target, temporary, control)
        if captured_callback is not None:
            captured_callback(captured_at, generation_id)
        validated_identity = backup._file_identity(temporary)
        if validated_identity is None or temporary.lstat().st_nlink != 1:
            raise repository.failure()
        # The full private-copy inspection below also prepares its reusable record.
        if (backup._file_identity(temporary) != validated_identity
                or temporary.lstat().st_nlink != 1):
            raise repository.failure()
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        retention = control.maintenance.backup_generations
        latest = control.generations[-1] if control.generations else None
        publication = replace(
            backup._new_metadata(retention, published_at=observed_at or utc_now(), after=latest),
            generation_id=generation_id,
        )
        final = directory / backup._filename(publication)
        from .backup_validation import prepare_copy, publish, require_prepared_copy, require_published_record
        prepared = prepare_copy(temporary, publication, project_id=target.project.project_id,
            validator=lambda: repository.validate_snapshot(temporary, target, control.schema_version))
        if prepared.record.schema != control.schema_version:
            raise repository.failure()
        published_record = publish(final, prepared)
        with repository.control_snapshot(target, expected=control):
            # Admission can outlive the record publication. Certify the separate
            # closed copy again at the actual file effect, not merely its stamp.
            require_prepared_copy(prepared)
            require_published_record(final, prepared, published_record)
            if (backup._directory_identity(directory) != directory_identity
                    or backup._directory_identity(scratch_root) != scratch_identity
                    or backup._file_identity(temporary) != validated_identity
                    or temporary.lstat().st_nlink != 1
                    or final.exists() or final.is_symlink()):
                raise repository.failure()
            os.replace(temporary, final)
        temporary = None
        require_published_record(final, prepared, published_record)
        published_at = publication.published_at
        control = repository.commit_inventory(
            target, control, (*control.generations, publication), success=True,
        )
        require_published_record(final, prepared, published_record)
        identity = backup._file_identity(final)
        if identity is None:
            raise repository.failure()
        observed = backup._artifact_observation(final, target, identity)
        if observed is None or observed.content_sha256 != prepared.record.sha256:
            raise repository.failure()
        artifacts.append(backup._Artifact(final, publication, identity, observed))
        backup._validate_artifact_relationships(target, artifacts)
        control = _prune(target, control, artifacts)
        return BackupResult("succeeded", generation_id, captured_at, published_at)
    except Exception:
        if control is not None:
            with suppress(Exception):
                repository.record_failure(target, control, timestamp)
        # A file may have been published before metadata/retention failed. The
        # failure result never advertises a confirmed successful publication.
        return BackupResult("failed", generation_id, captured_at)
    finally:
        if scratch_root is not None:
            with suppress(Exception):
                _cleanup_scratch(scratch_root, project_id=target.project.project_id, expected_attempt=attempt_id)
