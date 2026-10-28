"""Content-bound validation records for immutable managed backup files.

Readers never create records. Writers are called only under the existing backup
artifact lock. A record certifies internal contents, not a generation's ownership,
current binding, inventory relationships, or permission to restore/delete it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
from itertools import islice
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from .backup_metadata_repository import MAX_BACKUP_GENERATIONS, MigrationBackupMetadata, validate_migration_backup_metadata
from .state_paths import (
    StatePathError, ValidatedFile, create_exclusive_durable_file,
    hash_physical_file, inspect_physical_directory, inspect_physical_file,
    path_lexically_exists, read_physical_file_bounded,
)
from .storage import SCHEMA_VERSION, StorageError

RULES_VERSION = 1
MAX_RECORD_BYTES = 4096
MAX_RECORDS = MAX_BACKUP_GENERATIONS + 1
_NAME = re.compile(r"tg_backup_[0-9a-f]{32}\.json")

if TYPE_CHECKING:
    from .state_resolver import _DatabaseObservation


def failure(code: str = "project_state_unreadable") -> StorageError:
    # Preserve the resolver's established operational distinctions; callers
    # retain their existing public message projection for these closed codes.
    if code not in {"database_busy", "unsupported_journal_mode", "schema_too_new"}:
        code = "project_state_unreadable"
    return StorageError(code, "project state could not be read safely")


@dataclass(frozen=True)
class ValidationRecord:
    version: int
    rules: int
    mode: str
    scope: str
    project_id: str
    generation_id: str
    published_at: str
    retention: int
    schema: int
    sha256: str
    size: int
    classification: str

    @property
    def metadata(self) -> MigrationBackupMetadata:
        return MigrationBackupMetadata(self.generation_id, self.published_at, self.retention)

    @property
    def reusable(self) -> bool:
        return (self.rules == RULES_VERSION and self.mode == "recovery"
                and self.scope == "complete" and self.classification == "eligible")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def encode(record: ValidationRecord) -> bytes:
    payload = asdict(record)
    return _canonical({"record": payload, "checksum": hashlib.sha256(_canonical(payload)).hexdigest()}) + b"\n"


def decode(raw: bytes) -> ValidationRecord:
    try:
        if not raw or len(raw) > MAX_RECORD_BYTES:
            raise ValueError()
        envelope = json.loads(raw.decode("ascii"))
        if type(envelope) is not dict or set(envelope) != {"record", "checksum"}:
            raise ValueError()
        record = ValidationRecord(**envelope["record"])
        if encode(record) != raw:
            raise ValueError()
        if (type(record.version) is not int or record.version != 1
                or type(record.rules) is not int or not 1 <= record.rules < 2**31
                or record.mode not in {"recovery", "structure"}
                or record.scope not in {"complete", "structure"}
                or record.classification not in {"eligible", "structural_only"}
                or type(record.schema) is not int or not 1 <= record.schema <= SCHEMA_VERSION
                or type(record.size) is not int or not 0 < record.size < 2**63
                or type(record.project_id) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", record.project_id) is None
                or type(record.sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", record.sha256) is None):
            raise ValueError()
        validate_migration_backup_metadata(record.metadata)
        if (record.mode, record.scope, record.classification) not in {
                ("recovery", "complete", "eligible"),
                ("structure", "structure", "structural_only")}:
            raise ValueError()
        return record
    except (TypeError, ValueError, KeyError, UnicodeError, StorageError) as exc:
        raise failure() from exc


def record_directory(backups: Path) -> Path:
    # Resolver owns this fixed relative name for both physical project and test roots.
    from .state_resolver import backup_validation_path
    return backup_validation_path(backups)


def inventory(backups: Path, *, include_pending: bool = False) -> tuple[Path, ...]:
    """Inspect bounded record paths, never use them to discover backup ownership."""
    root = record_directory(backups)
    if not path_lexically_exists(root):
        return ()
    inspect_physical_directory(root, root=backups)
    entries = tuple(islice(root.iterdir(), MAX_RECORDS + 2))
    if len(entries) > MAX_RECORDS + 1:
        raise failure()
    records = []
    for path in entries:
        if not _NAME.fullmatch(path.name) and path.name != ".pending.json":
            raise failure()
        inspect_physical_file(path, root=root, max_bytes=MAX_RECORD_BYTES)
        if path.lstat().st_nlink != 1:
            raise failure()
        if path.name != ".pending.json":
            records.append(path)
    if len(records) > MAX_RECORDS:
        raise failure()
    # Setup cutover preserves/seals the complete recognized physical domain;
    # ordinary record consumers never interpret the pending publication file.
    return tuple(entries if include_pending else records)


def require_safe_artifact(path: Path) -> None:
    inspect_physical_file(path, root=path.parent)
    if path.lstat().st_nlink != 1:
        raise failure()
    aliases = {path.name.casefold() + suffix for suffix in ("-journal", "-wal", "-shm")}
    if any(entry.name.casefold() in aliases for entry in path.parent.iterdir()):
        raise failure()


def read_record(path: Path, metadata: MigrationBackupMetadata) -> ValidationRecord | None:
    root = record_directory(path.parent)
    inventory(path.parent)
    record_path = root / (metadata.generation_id + ".json")
    if not path_lexically_exists(record_path):
        return None
    raw, _ = read_physical_file_bounded(record_path, root=root, max_bytes=MAX_RECORD_BYTES)
    record = decode(raw)
    if record.metadata != metadata:
        raise failure()
    return record


@contextmanager
def artifact_snapshot(path: Path, metadata: MigrationBackupMetadata, *,
                      project_id: str | None = None, audit: bool = False):
    """Bind one fingerprint to the actual pinned SQLite observation.

    The raw hash descriptor outlives the SQLite connection, also on exceptions.
    A consumer may copy this exact source snapshot before both are closed.
    """
    from .state_resolver import _inspect_database, _ResolverFailure
    try:
        require_safe_artifact(path)
        record = read_record(path, metadata)
        proof = record if record is not None and record.reusable and not audit else None
        with ExitStack() as descriptors:
            observed = _inspect_database(path, classify_recovery_content=True,
                content_proof=proof, retain_connection=True)
            try:
                fingerprint = hash_physical_file(path, root=path.parent, descriptor_scope=descriptors)
                if record is not None and (record.sha256 != fingerprint.sha256
                        or record.size != fingerprint.identity.size):
                    raise failure()
                if ((observed.stamp.device, observed.stamp.inode, observed.stamp.size, observed.stamp.modified_ns)
                        != (fingerprint.identity.device, fingerprint.identity.inode,
                            fingerprint.identity.size, fingerprint.identity.modified_ns)):
                    raise failure()
                if (project_id is not None and observed.stored_project.project_id != project_id
                        or record is not None and (record.project_id != observed.stored_project.project_id
                            or record.schema != observed.stored_project.source_schema_version)):
                    raise failure()
                require_safe_artifact(path)
                if read_record(path, metadata) != record:
                    raise failure()
                yield replace(observed, content_sha256=fingerprint.sha256)
            finally:
                observed.read_connection.close()
    except _ResolverFailure as exc:
        raise failure(exc.code) from exc
    except (StatePathError, OSError) as exc:
        raise failure() from exc


def inspect_artifact(path: Path, metadata: MigrationBackupMetadata, *,
                     project_id: str | None = None, audit: bool = False) -> _DatabaseObservation:
    """Return a closed observation; no connection or descriptor escapes."""
    with artifact_snapshot(path, metadata, project_id=project_id, audit=audit) as observed:
        return replace(observed, read_connection=None)


@dataclass(frozen=True)
class PreparedRecord:
    record: ValidationRecord
    fingerprint: ValidatedFile


def prepare_copy(path: Path, metadata: MigrationBackupMetadata, *, project_id: str,
                 validator: Callable[[], int] | None = None) -> PreparedRecord:
    """Inspect a private closed copy completely and bind its final bytes."""
    from .state_resolver import _inspect_database, _ResolverFailure
    try:
        require_safe_artifact(path)
        before = hash_physical_file(path, root=path.parent)
        if validator is None:
            observed = _inspect_database(path, classify_recovery_content=True)
            if observed.stored_project.project_id != project_id or not observed.recovery_content_valid:
                raise failure()
            schema = observed.stored_project.source_schema_version
        else:
            # Repository callback owns its complete private-copy admission.
            schema = validator()
        after = hash_physical_file(path, root=path.parent)
        if before != after:
            raise failure()
        record = ValidationRecord(1, RULES_VERSION, "recovery", "complete", project_id,
            metadata.generation_id, metadata.published_at, metadata.publication_retention,
            schema, after.sha256, after.identity.size, "eligible")
        decode(encode(record))
        return PreparedRecord(record, after)
    except _ResolverFailure as exc:
        raise failure(exc.code) from exc
    except (StatePathError, OSError) as exc:
        raise failure() from exc


def require_prepared_copy(prepared: PreparedRecord) -> None:
    """Recheck the closed copy at a distinct publication boundary."""
    source = prepared.fingerprint.path
    require_safe_artifact(source)
    if hash_physical_file(source, root=source.parent) != prepared.fingerprint:
        raise failure()


def publish(path: Path, prepared: PreparedRecord) -> ValidatedFile:
    """Publish the record before the backup file, under existing artifact ownership.

    A crash may leave an orphan record, which grants no file/row ownership.
    Publishing first prevents a later changed file from being silently adopted
    as a record-less legacy artifact after an interrupted publication.
    """
    require_prepared_copy(prepared)
    root = record_directory(path.parent)
    inspect_physical_directory(path.parent)
    root.mkdir(exist_ok=True)
    records = inventory(path.parent)
    final = root / (prepared.record.generation_id + ".json")
    if path_lexically_exists(final) or len(records) >= MAX_RECORDS:
        raise failure()
    pending = root / ".pending.json"
    if path_lexically_exists(pending):
        # Only this fixed bounded scratch name is disposable; never a final record.
        inspect_physical_file(pending, root=root, max_bytes=MAX_RECORD_BYTES)
        if pending.lstat().st_nlink != 1:
            raise failure()
        pending.unlink()
    created = create_exclusive_durable_file(pending, encode(prepared.record), root=root,
                                          max_bytes=MAX_RECORD_BYTES)
    if inspect_physical_file(pending, root=root)[1] != created.identity:
        raise failure()
    # Existing artifact ownership serializes publishers. Never overwrite a record.
    if path_lexically_exists(final):
        raise failure()
    os.replace(pending, final)
    return replace(created, path=final)


def require_published_record(path: Path, prepared: PreparedRecord, published: ValidatedFile) -> None:
    """A newly published record must remain the exact prepared physical file."""
    if read_record(path, prepared.record.metadata) != prepared.record:
        raise failure()
    raw, current = read_physical_file_bounded(published.path,
        root=record_directory(path.parent), max_bytes=MAX_RECORD_BYTES)
    if current != published or raw != encode(prepared.record):
        raise failure()


def cleanup_orphans(backups: Path, *, project_id: str) -> None:
    """Discard only valid derived records lacking their exact artifact, under lock."""
    from .backup import _filename
    for path in inventory(backups):
        raw, identity = read_physical_file_bounded(path, root=path.parent, max_bytes=MAX_RECORD_BYTES)
        record = decode(raw)
        if record.project_id != project_id or path.name != record.generation_id + ".json":
            raise failure()
        artifact = backups / _filename(record.metadata)
        if path_lexically_exists(artifact):
            continue
        if any(path_lexically_exists(Path(str(artifact) + suffix)) for suffix in ("-journal", "-wal", "-shm")):
            raise failure()
        if inspect_physical_file(path, root=path.parent)[1] != identity.identity:
            raise failure()
        path.unlink()


def remove_after_prune(path: Path, metadata: MigrationBackupMetadata) -> None:
    """Called only after independent generation/file pruning authority succeeded."""
    record = read_record(path, metadata)
    if record is None:
        return
    record_path = record_directory(path.parent) / (metadata.generation_id + ".json")
    raw, identity = read_physical_file_bounded(record_path, root=record_path.parent,
                                              max_bytes=MAX_RECORD_BYTES)
    if decode(raw) != record or inspect_physical_file(record_path, root=record_path.parent)[1] != identity.identity:
        raise failure()
    record_path.unlink()
