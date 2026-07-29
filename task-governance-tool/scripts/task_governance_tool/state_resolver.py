"""Bounded read-only resolution of the one package-local project state.

The resolver deliberately does not create directories, initialize SQLite, acquire
artifact locks, or repair legacy state.  It establishes enough immutable context
for setup and ordinary consumers to make their separately owned decisions.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from task_governance_tool.storage import (
    SCHEMA_VERSION,
    DatabaseTarget,
    DoctorStorageState,
    MigrationBackupMetadata,
    ProjectIdentity,
    StorageError,
    canonicalize_repo,
    connect_readonly,
    current_schema_version,
    is_sqlite_busy_or_locked,
    missing_migration_versions,
    normalized_path_for_hash,
    read_project_binding_state,
    read_doctor_state,
    read_project_maintenance,
    sanitize_project_display_name,
    table_exists,
    validate_identity_project_id,
    validate_current_database,
    validate_lower_hex_64,
    validate_migration_backup_metadata,
)


LayoutState = Literal["missing", "fixed_current_v1", "legacy_projects_v1"]
BindingState = Literal["unbound", "matching", "relocation_required"]

MAX_LEGACY_PROJECT_ENTRIES = 64
MAX_MANAGED_BACKUP_ARTIFACTS = 21
MAX_EXTRA_ARTIFACT_BYTES = 16_777_216

_BACKUP_FILENAME = re.compile(
    r"^taskgov-backup-v1_(?P<time>\d{8}T\d{6}Z)_"
    r"(?P<token>[0-9a-f]{32})_r(?P<retention>[1-9]|1[0-9]|20)\.sqlite$"
)
_ROOT_TEMP = re.compile(r"^\.taskgov-restore-[a-z0-9_]{8}\.tmp$")
_BACKUP_TEMP = re.compile(r"^\.taskgov-backup-[a-z0-9_]{8}\.tmp$")
_VIEWER_TEMP = re.compile(r"^\.task-viewer-[a-z0-9_]{8}\.tmp$")
_KNOWN_RESOLVER_ERRORS = frozenset(
    {
        "database_busy",
        "migration_required",
        "project_mismatch",
        "project_state_unreadable",
        "schema_too_new",
        "unsupported_journal_mode",
    }
)


@dataclass(frozen=True)
class CanonicalStatePaths:
    """Canonical paths owned by the physical Skill package."""

    skill_root: Path = field(repr=False)
    state_root: Path = field(repr=False)
    transition_lock: Path = field(repr=False)
    fixed_root: Path = field(repr=False)
    database: Path = field(repr=False)
    backups: Path = field(repr=False)
    viewer: Path = field(repr=False)
    legacy_projects: Path = field(repr=False)


@dataclass(frozen=True)
class CurrentRootObservation:
    """Current governed root values; none of them is durable identity."""

    canonical_repo: Path = field(repr=False)
    canonical_path_hash: str = field(repr=False)
    display_name: str


@dataclass(frozen=True)
class StoredProjectObservation:
    """Validated identity and binding read from one coherent database snapshot."""

    project_id: str
    identity_scheme: str
    binding_generation: int
    canonical_path_hash: str = field(repr=False)
    display_name: str
    source_schema_version: int
    binding_lineage: tuple[str, ...] = field(repr=False)
    legacy_cleanup_pending: bool = False


@dataclass(frozen=True)
class ManagedBackupObservation:
    """One validated managed backup without a publicly printable path."""

    metadata: MigrationBackupMetadata
    source_schema_version: int
    path: Path = field(repr=False)
    stored_project: StoredProjectObservation = field(repr=False)


@dataclass(frozen=True)
class LegacySourceObservation:
    """Validated legacy-layout input reserved for setup-owned publication."""

    primary_present: bool
    source_schema_version: int
    managed_backups: tuple[ManagedBackupObservation, ...]
    recognized_entries: tuple[str, ...]
    root: Path = field(repr=False)
    source_database: Path = field(repr=False)
    source_target: DatabaseTarget = field(repr=False)
    lock_target: DatabaseTarget = field(repr=False)


@dataclass(frozen=True)
class FixedRecoveryObservation:
    """Validated fixed-layout backup selected while the primary is absent."""

    selected: ManagedBackupObservation
    managed_backups: tuple[ManagedBackupObservation, ...]


@dataclass(frozen=True)
class ProjectStateResolution:
    """One immutable resolver result shared by setup and DB-backed consumers."""

    paths: CanonicalStatePaths
    current_root: CurrentRootObservation
    layout: LayoutState
    binding: BindingState
    stored_project: StoredProjectObservation | None = None
    target: DatabaseTarget | None = field(default=None, repr=False)
    legacy_source: LegacySourceObservation | None = field(default=None, repr=False)
    fixed_recovery: FixedRecoveryObservation | None = field(
        default=None,
        repr=False,
    )
    doctor_state: DoctorStorageState | None = field(default=None, repr=False)
    read_connection: sqlite3.Connection | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    error_code: str | None = None

    @property
    def project_id(self) -> str | None:
        return (
            self.stored_project.project_id
            if self.stored_project is not None
            else None
        )

    @property
    def source_schema_version(self) -> int | None:
        return (
            self.stored_project.source_schema_version
            if self.stored_project is not None
            else None
        )


@dataclass(frozen=True)
class _FileStamp:
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class _DatabaseObservation:
    stored_project: StoredProjectObservation
    generation_rows: tuple[MigrationBackupMetadata, ...]
    maintenance_pointer: MigrationBackupMetadata | None
    stamp: _FileStamp = field(repr=False)
    doctor_state: DoctorStorageState | None = field(default=None, repr=False)
    read_connection: sqlite3.Connection | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass
class _ResolverFailure(Exception):
    code: str


def canonical_state_paths(skill_root: Path) -> CanonicalStatePaths:
    """Derive the only production state hierarchy from the physical package."""

    canonical_skill = Path(skill_root).expanduser().resolve(strict=False)
    state_root = canonical_skill / "state"
    fixed_root = state_root / "current"
    return CanonicalStatePaths(
        skill_root=canonical_skill,
        state_root=state_root,
        transition_lock=state_root / "taskgov-state.lock",
        fixed_root=fixed_root,
        database=fixed_root / "taskgov.sqlite",
        backups=fixed_root / "backups",
        viewer=fixed_root / "viewer" / "task-viewer.html",
        legacy_projects=state_root / "projects",
    )


def observe_current_root(repo: Path) -> CurrentRootObservation:
    """Derive only binding input and display metadata from the current root."""

    canonical_repo = canonicalize_repo(repo)
    normalized = normalized_path_for_hash(canonical_repo)
    canonical_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return CurrentRootObservation(
        canonical_repo=canonical_repo,
        canonical_path_hash=canonical_hash,
        display_name=sanitize_project_display_name(
            canonical_repo.name or "project"
        ),
    )


def consumer_error_code(resolution: ProjectStateResolution) -> str | None:
    """Map internal resolver state to the public consumer boundary."""

    if resolution.error_code is not None:
        return resolution.error_code
    if resolution.binding == "relocation_required":
        return "project_relocation_required"
    if (
        resolution.layout == "fixed_current_v1"
        and resolution.source_schema_version != SCHEMA_VERSION
    ):
        return "migration_required"
    if resolution.layout == "legacy_projects_v1":
        return "migration_required"
    if resolution.layout == "missing" or resolution.fixed_recovery is not None:
        return "db_not_initialized"
    return None


def resolve_project_state(
    *,
    skill_root: Path,
    repo: Path,
    include_doctor_state: bool = False,
    retain_read_connection: bool = False,
) -> ProjectStateResolution:
    """Resolve fixed, fixed-recovery, legacy, or missing state without writes."""

    paths = canonical_state_paths(skill_root)
    current_root = observe_current_root(repo)
    return _resolve_with_paths(
        paths,
        current_root,
        include_legacy=True,
        validate_fixed_artifacts=False,
        include_doctor_state=include_doctor_state,
        retain_read_connection=retain_read_connection,
    )


def resolve_setup_project_state(
    *,
    skill_root: Path,
    repo: Path,
) -> ProjectStateResolution:
    """Resolve setup state with deep validation of fixed artifacts."""

    paths = canonical_state_paths(skill_root)
    current_root = observe_current_root(repo)
    return _resolve_with_paths(
        paths,
        current_root,
        include_legacy=True,
        validate_fixed_artifacts=True,
        include_doctor_state=False,
        retain_read_connection=False,
    )


def resolve_staged_project_state(
    *,
    stage_root: Path,
    repo: Path,
) -> ProjectStateResolution:
    """Validate one private fixed-layout stage without legacy fallback."""

    fixed_root = Path(stage_root).resolve(strict=False)
    state_root = fixed_root.parent
    paths = CanonicalStatePaths(
        skill_root=state_root.parent,
        state_root=state_root,
        transition_lock=state_root / "taskgov-state.lock",
        fixed_root=fixed_root,
        database=fixed_root / "taskgov.sqlite",
        backups=fixed_root / "backups",
        viewer=fixed_root / "viewer" / "task-viewer.html",
        legacy_projects=state_root / "projects",
    )
    return _resolve_with_paths(
        paths,
        observe_current_root(repo),
        include_legacy=False,
        validate_fixed_artifacts=True,
        include_doctor_state=False,
        retain_read_connection=False,
    )


def _resolve_with_paths(
    paths: CanonicalStatePaths,
    current_root: CurrentRootObservation,
    *,
    include_legacy: bool,
    validate_fixed_artifacts: bool,
    include_doctor_state: bool,
    retain_read_connection: bool,
) -> ProjectStateResolution:
    try:
        _validate_optional_directory(paths.state_root)
        _validate_optional_regular_file(paths.transition_lock)
        fixed = _resolve_fixed(
            paths,
            current_root,
            validate_artifacts=validate_fixed_artifacts,
            include_doctor_state=include_doctor_state,
            retain_read_connection=retain_read_connection,
        )
        if fixed is not None:
            return fixed
        if include_legacy:
            return _resolve_legacy(paths, current_root)
        raise _ResolverFailure("project_state_unreadable")
    except _ResolverFailure as exc:
        return _error_resolution(paths, current_root, exc.code)
    except StorageError as exc:
        return _error_resolution(
            paths,
            current_root,
            _storage_error_code(exc),
        )
    except sqlite3.Error as exc:
        return _error_resolution(
            paths,
            current_root,
            "database_busy"
            if is_sqlite_busy_or_locked(exc)
            else "project_state_unreadable",
        )
    except (OSError, RuntimeError, ValueError):
        return _error_resolution(
            paths,
            current_root,
            "project_state_unreadable",
        )


def _error_resolution(
    paths: CanonicalStatePaths,
    current_root: CurrentRootObservation,
    code: str,
) -> ProjectStateResolution:
    return ProjectStateResolution(
        paths=paths,
        current_root=current_root,
        layout="missing",
        binding="unbound",
        error_code=(
            code if code in _KNOWN_RESOLVER_ERRORS else "project_state_unreadable"
        ),
    )


def _storage_error_code(exc: StorageError) -> str:
    if exc.code in {
        "database_busy",
        "migration_required",
        "unsupported_journal_mode",
        "schema_too_new",
    }:
        return exc.code
    return "project_state_unreadable"


def _is_reparse(details: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & attribute
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc


def _stamp(path: Path) -> _FileStamp:
    details = _lstat(path)
    if (
        details is None
        or _is_reparse(details)
        or not stat.S_ISREG(details.st_mode)
    ):
        raise _ResolverFailure("project_state_unreadable")
    return _FileStamp(
        device=int(details.st_dev),
        inode=int(details.st_ino),
        size=int(details.st_size),
        modified_ns=int(details.st_mtime_ns),
    )


def _validate_optional_directory(path: Path) -> bool:
    details = _lstat(path)
    if details is None:
        return False
    if _is_reparse(details) or not stat.S_ISDIR(details.st_mode):
        raise _ResolverFailure("project_state_unreadable")
    return True


def _validate_optional_regular_file(path: Path) -> bool:
    details = _lstat(path)
    if details is None:
        return False
    if _is_reparse(details) or not stat.S_ISREG(details.st_mode):
        raise _ResolverFailure("project_state_unreadable")
    return True


def _require_contained(path: Path, parent: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise _ResolverFailure("project_state_unreadable") from exc


def _resolve_fixed(
    paths: CanonicalStatePaths,
    current_root: CurrentRootObservation,
    *,
    validate_artifacts: bool,
    include_doctor_state: bool,
    retain_read_connection: bool,
) -> ProjectStateResolution | None:
    fixed_exists = _validate_optional_directory(paths.fixed_root)
    if not fixed_exists:
        return None
    _require_contained(paths.fixed_root, paths.state_root)
    primary_exists = _validate_optional_regular_file(paths.database)

    if primary_exists:
        database = _inspect_database(
            paths.database,
            doctor_current_root=(
                current_root if include_doctor_state else None
            ),
            mutable=True,
            retain_connection=retain_read_connection,
            consumer_current_root=(
                current_root if retain_read_connection else None
            ),
        )
        if not validate_artifacts:
            return _fixed_resolution(
                paths,
                current_root,
                database,
                fixed_recovery=None,
            )
        backups, recognized = _inspect_backup_directory(paths.backups)
        recognized.extend(_inspect_viewer_directory(paths.viewer.parent))
        recognized.extend(_inspect_root_owned_entries(paths.fixed_root))
        source_size = database.stamp.size
        recognized.extend(
            _inspect_fixed_temporaries(paths, source_size=source_size)
        )
        _validate_recognized_artifacts(
            paths.fixed_root,
            recognized,
            source_size=source_size,
        )
        _validate_backup_set(
            backups,
            database,
            primary_present=True,
        )
        return _fixed_resolution(
            paths,
            current_root,
            database,
            fixed_recovery=None,
        )

    backups, recognized = _inspect_backup_directory(paths.backups)
    recognized.extend(_inspect_viewer_directory(paths.viewer.parent))
    root_owned = _inspect_root_owned_entries(paths.fixed_root)
    recognized.extend(root_owned)
    if backups:
        selected = backups[-1]
        if "taskgov.sqlite-journal" in root_owned:
            raise _ResolverFailure("project_state_unreadable")
        source_size = selected._database.stamp.size
        recognized.extend(
            _inspect_fixed_temporaries(paths, source_size=source_size)
        )
        _validate_recognized_artifacts(
            paths.fixed_root,
            recognized,
            source_size=source_size,
        )
        _validate_backup_set(
            backups,
            selected._database,
            primary_present=False,
        )
        if (
            selected.stored_project.canonical_path_hash
            != current_root.canonical_path_hash
        ):
            raise _ResolverFailure("project_state_unreadable")
        destination_target = _database_target(
            selected.stored_project,
            current_root,
            paths.database,
            explicit_db=False,
            paths=paths,
        )
        return ProjectStateResolution(
            paths=paths,
            current_root=current_root,
            layout="fixed_current_v1",
            binding="matching",
            stored_project=selected.stored_project,
            target=destination_target,
            fixed_recovery=FixedRecoveryObservation(
                selected=selected.public,
                managed_backups=tuple(item.public for item in backups),
            ),
        )

    # An existing fixed destination without a primary or usable recovery
    # generation is residue, not authorization to initialize or inspect legacy.
    raise _ResolverFailure("project_state_unreadable")


def _fixed_resolution(
    paths: CanonicalStatePaths,
    current_root: CurrentRootObservation,
    database: _DatabaseObservation,
    *,
    fixed_recovery: FixedRecoveryObservation | None,
) -> ProjectStateResolution:
    stored = database.stored_project
    binding: BindingState = (
        "matching"
        if stored.canonical_path_hash == current_root.canonical_path_hash
        else "relocation_required"
    )
    read_connection = database.read_connection
    if binding != "matching" and read_connection is not None:
        read_connection.close()
        read_connection = None
    return ProjectStateResolution(
        paths=paths,
        current_root=current_root,
        layout="fixed_current_v1",
        binding=binding,
        stored_project=stored,
        target=_database_target(
            stored,
            current_root,
            paths.database,
            explicit_db=False,
            paths=paths,
        ),
        fixed_recovery=fixed_recovery,
        doctor_state=database.doctor_state,
        read_connection=read_connection,
    )


def _resolve_legacy(
    paths: CanonicalStatePaths,
    current_root: CurrentRootObservation,
) -> ProjectStateResolution:
    if not _validate_optional_directory(paths.legacy_projects):
        return _missing_resolution(paths, current_root)
    _require_contained(paths.legacy_projects, paths.state_root)
    try:
        entries: list[Path] = []
        for entry in paths.legacy_projects.iterdir():
            entries.append(entry)
            if len(entries) > MAX_LEGACY_PROJECT_ENTRIES:
                raise _ResolverFailure("project_state_unreadable")
    except OSError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc
    if not entries:
        return _missing_resolution(paths, current_root)
    if len(entries) != 1:
        raise _ResolverFailure("project_state_unreadable")

    candidate = entries[0]
    if not _validate_optional_directory(candidate):
        raise _ResolverFailure("project_state_unreadable")
    _require_contained(candidate, paths.legacy_projects)
    primary = candidate / "taskgov.sqlite"
    primary_present = _validate_optional_regular_file(primary)
    backups_path = candidate / "backups"
    backups, recognized = _inspect_backup_directory(backups_path)
    viewer_path = candidate / "viewer"
    recognized.extend(_inspect_viewer_directory(viewer_path))
    if primary_present:
        database = _inspect_database(primary, mutable=True)
    elif backups:
        database = backups[-1]._database
    else:
        raise _ResolverFailure("project_state_unreadable")
    source_size = database.stamp.size
    root_owned = _inspect_root_owned_entries(candidate)
    if not primary_present and "taskgov.sqlite-journal" in root_owned:
        raise _ResolverFailure("project_state_unreadable")
    recognized.extend(root_owned)
    recognized.extend(
        _inspect_single_temporary(
            candidate,
            pattern=_ROOT_TEMP,
            relative_prefix="",
            source_size=source_size,
        )
    )
    _validate_recognized_artifacts(
        candidate,
        recognized,
        source_size=source_size,
    )
    recognized.extend(
        _inspect_single_temporary(
            backups_path,
            pattern=_BACKUP_TEMP,
            relative_prefix="backups/",
            source_size=source_size,
        )
    )
    recognized.extend(
        _inspect_single_temporary(
            viewer_path,
            pattern=_VIEWER_TEMP,
            relative_prefix="viewer/",
            source_size=source_size,
        )
    )

    stored = database.stored_project
    if candidate.name != stored.project_id:
        raise _ResolverFailure("project_mismatch")
    if stored.identity_scheme != "legacy_path_v1":
        raise _ResolverFailure("project_state_unreadable")
    _validate_backup_set(
        backups,
        database,
        primary_present=primary_present,
    )

    binding: BindingState = (
        "matching"
        if stored.canonical_path_hash == current_root.canonical_path_hash
        else "relocation_required"
    )
    if not primary_present and binding == "relocation_required":
        raise _ResolverFailure("project_state_unreadable")
    source_database = primary if primary_present else backups[-1].path
    source_target = _database_target(
        stored,
        current_root,
        source_database,
        explicit_db=True,
        skill_root=paths.skill_root,
        backups_path=backups_path,
        viewer_path=viewer_path / "task-viewer.html",
    )
    lock_target = _database_target(
        stored,
        current_root,
        primary,
        explicit_db=True,
        skill_root=paths.skill_root,
        backups_path=backups_path,
        viewer_path=viewer_path / "task-viewer.html",
    )
    if primary_present:
        recognized.append("taskgov.sqlite")
    return ProjectStateResolution(
        paths=paths,
        current_root=current_root,
        layout="legacy_projects_v1",
        binding=binding,
        stored_project=stored,
        legacy_source=LegacySourceObservation(
            primary_present=primary_present,
            source_schema_version=stored.source_schema_version,
            managed_backups=tuple(item.public for item in backups),
            recognized_entries=tuple(
                sorted(set(recognized), key=lambda value: value.encode("utf-8"))
            ),
            root=candidate,
            source_database=source_database,
            source_target=source_target,
            lock_target=lock_target,
        ),
    )


def _missing_resolution(
    paths: CanonicalStatePaths,
    current_root: CurrentRootObservation,
) -> ProjectStateResolution:
    return ProjectStateResolution(
        paths=paths,
        current_root=current_root,
        layout="missing",
        binding="unbound",
    )


def _database_target(
    stored: StoredProjectObservation,
    current_root: CurrentRootObservation,
    database: Path,
    *,
    explicit_db: bool,
    paths: CanonicalStatePaths | None = None,
    skill_root: Path | None = None,
    backups_path: Path | None = None,
    viewer_path: Path | None = None,
) -> DatabaseTarget:
    if paths is not None:
        skill_root = paths.skill_root
        backups_path = paths.backups
        viewer_path = paths.viewer
    return DatabaseTarget(
        project=ProjectIdentity(
            project_id=stored.project_id,
            canonical_repo=current_root.canonical_repo,
            canonical_path_hash=current_root.canonical_path_hash,
            display_name=current_root.display_name,
        ),
        db_path=database,
        explicit_db=explicit_db,
        binding_path_hash=stored.canonical_path_hash,
        binding_generation=stored.binding_generation,
        skill_root=skill_root,
        backups_path=backups_path,
        viewer_path=viewer_path,
        canonical_fixed=(paths is not None),
    )


def _inspect_database(
    path: Path,
    *,
    doctor_current_root: CurrentRootObservation | None = None,
    mutable: bool = False,
    retain_connection: bool = False,
    consumer_current_root: CurrentRootObservation | None = None,
) -> _DatabaseObservation:
    before = _stamp(path)
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_readonly(path)
        version = current_schema_version(connection)
        if version > SCHEMA_VERSION:
            raise _ResolverFailure("schema_too_new")
        if (
            version < 1
            or missing_migration_versions(connection, version)
            or not table_exists(connection, "project_meta")
        ):
            raise _ResolverFailure("project_state_unreadable")
        quick = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        if quick != ["ok"] or connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall():
            raise _ResolverFailure("project_state_unreadable")

        if version == 14:
            stored = _read_v14_project(connection, version)
        else:
            stored = _read_legacy_project(connection, version)
        generations = _read_generation_rows(
            connection,
            stored.project_id,
            version,
        )
        pointer = _read_maintenance_pointer(
            connection,
            stored.project_id,
            version,
        )
        doctor_state = None
        if (
            doctor_current_root is not None
            and version == SCHEMA_VERSION
            and stored.canonical_path_hash
            == doctor_current_root.canonical_path_hash
        ):
            doctor_state = read_doctor_state(
                connection,
                _database_target(
                    stored,
                    doctor_current_root,
                    path,
                    explicit_db=False,
                ),
            )
        if (
            retain_connection
            and version == SCHEMA_VERSION
            and consumer_current_root is not None
        ):
            validate_current_database(
                connection,
                _database_target(
                    stored,
                    consumer_current_root,
                    path,
                    explicit_db=False,
                ),
            )
    except _ResolverFailure:
        if connection is not None:
            connection.close()
        raise
    except StorageError as exc:
        if connection is not None:
            connection.close()
        raise _ResolverFailure(_storage_error_code(exc)) from exc
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise _ResolverFailure(
            "database_busy"
            if is_sqlite_busy_or_locked(exc)
            else "project_state_unreadable"
        ) from exc
    except Exception:
        if connection is not None:
            connection.close()
        raise
    try:
        after = _stamp(path)
    except Exception:
        connection.close()
        raise
    if (
        (before.device, before.inode) != (after.device, after.inode)
        or (not mutable and after != before)
    ):
        connection.close()
        raise _ResolverFailure("project_state_unreadable")
    keep_connection = retain_connection and version == SCHEMA_VERSION
    retained_connection = connection if keep_connection else None
    if not keep_connection:
        connection.close()
    return _DatabaseObservation(
        stored_project=stored,
        generation_rows=generations,
        maintenance_pointer=pointer,
        doctor_state=doctor_state,
        stamp=after,
        read_connection=retained_connection,
    )


def _read_legacy_project(
    connection: sqlite3.Connection,
    version: int,
) -> StoredProjectObservation:
    rows = connection.execute(
        """
        SELECT project_id, canonical_path_hash, display_name
          FROM project_meta
         ORDER BY project_id
        """
    ).fetchall()
    if len(rows) != 1:
        raise _ResolverFailure("project_state_unreadable")
    project_id = str(rows[0]["project_id"])
    canonical_hash = str(rows[0]["canonical_path_hash"])
    try:
        validate_identity_project_id(project_id, "legacy_path_v1")
        validate_lower_hex_64(canonical_hash, field="canonical path hash")
    except StorageError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc
    return StoredProjectObservation(
        project_id=project_id,
        identity_scheme="legacy_path_v1",
        binding_generation=1,
        canonical_path_hash=canonical_hash,
        display_name=sanitize_project_display_name(
            str(rows[0]["display_name"])
        ),
        source_schema_version=version,
        binding_lineage=(canonical_hash,),
    )


def _read_v14_project(
    connection: sqlite3.Connection,
    version: int,
) -> StoredProjectObservation:
    try:
        binding = read_project_binding_state(connection)
        rows = connection.execute(
            """
            SELECT canonical_path_hash
              FROM project_path_binding_history
             WHERE project_id = ?
             ORDER BY binding_generation
            """,
            (binding.project_id,),
        ).fetchall()
    except StorageError as exc:
        raise _ResolverFailure(_storage_error_code(exc)) from exc
    lineage = tuple(str(row["canonical_path_hash"]) for row in rows)
    if len(lineage) != binding.binding_generation:
        raise _ResolverFailure("project_state_unreadable")
    return StoredProjectObservation(
        project_id=binding.project_id,
        identity_scheme=binding.identity_scheme,
        binding_generation=binding.binding_generation,
        canonical_path_hash=binding.canonical_path_hash,
        display_name=binding.display_name,
        source_schema_version=version,
        binding_lineage=lineage,
        legacy_cleanup_pending=binding.legacy_cleanup_pending,
    )


def _read_generation_rows(
    connection: sqlite3.Connection,
    project_id: str,
    version: int,
) -> tuple[MigrationBackupMetadata, ...]:
    if version < 11:
        return ()
    if not table_exists(connection, "managed_backup_generations"):
        raise _ResolverFailure("project_state_unreadable")
    rows = connection.execute(
        """
        SELECT generation_id, project_id, published_at, publication_retention
          FROM managed_backup_generations
         ORDER BY published_at, generation_id
        """
    ).fetchall()
    if len(rows) > MAX_MANAGED_BACKUP_ARTIFACTS:
        raise _ResolverFailure("project_state_unreadable")
    result: list[MigrationBackupMetadata] = []
    seen: set[str] = set()
    for row in rows:
        if str(row["project_id"]) != project_id:
            raise _ResolverFailure("project_state_unreadable")
        metadata = MigrationBackupMetadata(
            generation_id=str(row["generation_id"]),
            published_at=str(row["published_at"]),
            publication_retention=int(row["publication_retention"]),
        )
        try:
            validate_migration_backup_metadata(metadata)
        except StorageError as exc:
            raise _ResolverFailure("project_state_unreadable") from exc
        if metadata.generation_id in seen:
            raise _ResolverFailure("project_state_unreadable")
        seen.add(metadata.generation_id)
        result.append(metadata)
    return tuple(result)


def _read_maintenance_pointer(
    connection: sqlite3.Connection,
    project_id: str,
    version: int,
) -> MigrationBackupMetadata | None:
    if version < 10:
        return None
    try:
        maintenance = read_project_maintenance(connection, project_id)
    except StorageError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc
    if maintenance is None:
        raise _ResolverFailure("project_state_unreadable")
    values = (
        maintenance.latest_backup_generation_id,
        maintenance.backup_last_success_at,
        maintenance.applied_backup_generations,
    )
    if values == (None, None, None):
        return None
    if any(value is None for value in values):
        raise _ResolverFailure("project_state_unreadable")
    metadata = MigrationBackupMetadata(
        generation_id=str(values[0]),
        published_at=str(values[1]),
        publication_retention=int(values[2]),
    )
    try:
        return validate_migration_backup_metadata(metadata)
    except StorageError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc


@dataclass(frozen=True)
class _BackupCandidate:
    metadata: MigrationBackupMetadata
    path: Path = field(repr=False)
    _database: _DatabaseObservation = field(repr=False)

    @property
    def key(self) -> tuple[str, str]:
        return (self.metadata.published_at, self.metadata.generation_id)

    @property
    def stored_project(self) -> StoredProjectObservation:
        return self._database.stored_project

    @property
    def public(self) -> ManagedBackupObservation:
        return ManagedBackupObservation(
            metadata=self.metadata,
            source_schema_version=self.stored_project.source_schema_version,
            path=self.path,
            stored_project=self.stored_project,
        )


def _inspect_backup_directory(
    directory: Path,
) -> tuple[list[_BackupCandidate], list[str]]:
    if not _validate_optional_directory(directory):
        return [], []
    candidates: list[_BackupCandidate] = []
    recognized: list[str] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc
    for entry in entries:
        name = entry.name
        metadata = _parse_backup_filename(name)
        if metadata is not None:
            _stamp(entry)
            candidates.append(
                _BackupCandidate(
                    metadata=metadata,
                    path=entry,
                    _database=_inspect_database(entry),
                )
            )
            recognized.append(f"backups/{name}")
            continue
        if name == "taskgov-backup.lock":
            _stamp(entry)
            recognized.append("backups/taskgov-backup.lock")
            continue
    if len(candidates) > MAX_MANAGED_BACKUP_ARTIFACTS:
        raise _ResolverFailure("project_state_unreadable")
    generation_ids = [item.metadata.generation_id for item in candidates]
    if len(generation_ids) != len(set(generation_ids)):
        raise _ResolverFailure("project_state_unreadable")
    candidates.sort(key=lambda item: item.key)
    return candidates, recognized


def _parse_backup_filename(name: str) -> MigrationBackupMetadata | None:
    match = _BACKUP_FILENAME.fullmatch(name)
    if match is None:
        return None
    try:
        timestamp = datetime.strptime(
            match.group("time"),
            "%Y%m%dT%H%M%SZ",
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        metadata = MigrationBackupMetadata(
            generation_id=f"tg_backup_{match.group('token')}",
            published_at=timestamp,
            publication_retention=int(match.group("retention")),
        )
        return validate_migration_backup_metadata(metadata)
    except (StorageError, ValueError):
        return None


def _inspect_viewer_directory(directory: Path) -> list[str]:
    if not _validate_optional_directory(directory):
        return []
    recognized: list[str] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc
    for entry in entries:
        if entry.name == "task-viewer.html":
            _stamp(entry)
            recognized.append("viewer/task-viewer.html")
        elif entry.name == "taskgov-viewer.lock":
            _stamp(entry)
            recognized.append("viewer/taskgov-viewer.lock")
    return recognized


def _inspect_root_owned_entries(root: Path) -> list[str]:
    recognized: list[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc
    for entry in entries:
        if entry.name == "taskgov.sqlite-journal":
            _stamp(entry)
            recognized.append(entry.name)
    return recognized


def _inspect_fixed_temporaries(
    paths: CanonicalStatePaths,
    *,
    source_size: int,
) -> list[str]:
    recognized = _inspect_single_temporary(
        paths.fixed_root,
        pattern=_ROOT_TEMP,
        relative_prefix="",
        source_size=source_size,
    )
    recognized.extend(
        _inspect_single_temporary(
            paths.backups,
            pattern=_BACKUP_TEMP,
            relative_prefix="backups/",
            source_size=source_size,
        )
    )
    recognized.extend(
        _inspect_single_temporary(
            paths.viewer.parent,
            pattern=_VIEWER_TEMP,
            relative_prefix="viewer/",
            source_size=source_size,
        )
    )
    return recognized


def _validate_recognized_artifacts(
    root: Path,
    relative_names: list[str],
    *,
    source_size: int,
) -> None:
    maximum = source_size + MAX_EXTRA_ARTIFACT_BYTES
    lock_names = {
        "backups/taskgov-backup.lock",
        "viewer/taskgov-viewer.lock",
    }
    for relative_name in relative_names:
        path = root.joinpath(*relative_name.split("/"))
        _require_contained(path, root)
        details = _stamp(path)
        if relative_name in lock_names:
            if details.size not in {0, 1}:
                raise _ResolverFailure("project_state_unreadable")
        elif details.size > maximum:
            raise _ResolverFailure("project_state_unreadable")


def _inspect_single_temporary(
    directory: Path,
    *,
    pattern: re.Pattern[str],
    relative_prefix: str,
    source_size: int,
) -> list[str]:
    if not _validate_optional_directory(directory):
        return []
    try:
        matches = [
            entry
            for entry in directory.iterdir()
            if pattern.fullmatch(entry.name)
        ]
    except OSError as exc:
        raise _ResolverFailure("project_state_unreadable") from exc
    if len(matches) != 1:
        return []
    details = _stamp(matches[0])
    if details.size > source_size + MAX_EXTRA_ARTIFACT_BYTES:
        return []
    return [f"{relative_prefix}{matches[0].name}"]


def _validate_backup_set(
    backups: list[_BackupCandidate],
    selected: _DatabaseObservation,
    *,
    primary_present: bool,
) -> None:
    selected_project = selected.stored_project
    for backup in backups:
        project = backup.stored_project
        if (
            project.project_id != selected_project.project_id
            or project.identity_scheme != selected_project.identity_scheme
            or not _lineage_is_prefix(
                project.binding_lineage,
                selected_project.binding_lineage,
            )
        ):
            raise _ResolverFailure("project_state_unreadable")

    version = selected_project.source_schema_version
    if version >= 11:
        files_by_id = {item.metadata.generation_id: item.metadata for item in backups}
        rows_by_id = {
            item.generation_id: item for item in selected.generation_rows
        }
        for generation_id in files_by_id.keys() & rows_by_id.keys():
            if files_by_id[generation_id] != rows_by_id[generation_id]:
                raise _ResolverFailure("project_state_unreadable")
        file_only = set(files_by_id) - set(rows_by_id)
        row_only = set(rows_by_id) - set(files_by_id)
        if len(set(files_by_id) | set(rows_by_id)) > MAX_MANAGED_BACKUP_ARTIFACTS:
            raise _ResolverFailure("project_state_unreadable")
        if primary_present:
            if (
                len(file_only) > 1
                or len(row_only) > 1
                or (file_only and row_only)
            ):
                raise _ResolverFailure("project_state_unreadable")
        else:
            if not backups:
                raise _ResolverFailure("project_state_unreadable")
            newest = backups[-1]
            if file_only != {newest.metadata.generation_id} or len(row_only) > 20:
                raise _ResolverFailure("project_state_unreadable")
            if any(
                (
                    rows_by_id[generation_id].published_at,
                    generation_id,
                )
                >= newest.key
                for generation_id in row_only
            ):
                raise _ResolverFailure("project_state_unreadable")
        return

    if version == 10 and selected.maintenance_pointer is not None:
        pointer = selected.maintenance_pointer
        exact = next(
            (
                item
                for item in backups
                if item.metadata.generation_id == pointer.generation_id
            ),
            None,
        )
        if exact is not None and exact.metadata != pointer:
            raise _ResolverFailure("project_state_unreadable")
        if exact is None and not any(
            item.key > (pointer.published_at, pointer.generation_id)
            for item in backups
        ):
            raise _ResolverFailure("project_state_unreadable")


def _lineage_is_prefix(
    older: tuple[str, ...],
    newer: tuple[str, ...],
) -> bool:
    return len(older) <= len(newer) and older == newer[: len(older)]
