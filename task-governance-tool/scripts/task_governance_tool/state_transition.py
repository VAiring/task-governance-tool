"""Owned staging and replay-safe legacy-state filesystem transitions.

This module intentionally does not decide setup policy, migrate SQLite, update
binding state, or clear persisted cleanup metadata.  It only implements the
bounded filesystem primitives that setup may orchestrate under the package
state-transition lock.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

from task_governance_tool.artifact_lock import (
    ArtifactLockError,
    inspect_existing_artifact_lock,
    zero_wait_artifact_lock,
)
from task_governance_tool.state_paths import (
    EVIDENCE_BUNDLE_MAX_BYTES,
    EVIDENCE_BUNDLES_DIRECTORY_NAME,
    EVIDENCE_DIRECTORY_NAME,
    EVIDENCE_INDEX_FILENAME,
    EVIDENCE_INDEX_MAX_BYTES,
    StatePathError,
    ValidatedDirectory,
    ValidatedFile,
    create_exclusive_durable_file,
    create_physical_directory_exclusive,
    evidence_relative_file_kind,
    hash_physical_file,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    read_physical_file_bounded,
    remove_explicit_files_and_directories,
    rename_no_replace,
    require_contained,
    rmdir_if_empty,
    unlink_validated_file,
)
from task_governance_tool.storage import (
    LOWER_HEX_64_PATTERN,
    StorageError,
    validate_cleanup_inventory,
    validate_identity_project_id,
)


STATE_TRANSITION_FAILURE_MESSAGE = "setup completed only partially; rerun setup"
STATE_TRANSITION_LOCK_FILENAME = "taskgov-state.lock"
STAGE_OWNER_MAX_BYTES = 2_048
STAGE_FILE_MAX_OVERHEAD = 16_777_216
# Legacy publication regenerates an index-only projection and never copies a
# fixed-current bundle inventory, so Evidence does not expand this stage cap.
STAGE_MAX_FILES = 32
STAGE_MAX_DIRECTORIES = 5
STAGE_MAX_BACKUPS = 21

_STAGE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STAGE_DIRECTORY_PATTERN = re.compile(r"^\.current-stage-([0-9a-f]{32})$")
_STAGE_OWNER_PATTERN = re.compile(r"^\.current-stage-([0-9a-f]{32})\.owner$")
_RESTORE_TEMP_PATTERN = re.compile(r"^\.taskgov-restore-[a-z0-9_]{8}\.tmp$")
_BACKUP_TEMP_PATTERN = re.compile(r"^\.taskgov-backup-[a-z0-9_]{8}\.tmp$")
_VIEWER_TEMP_PATTERN = re.compile(r"^\.task-viewer-[a-z0-9_]{8}\.tmp$")
_BACKUP_BASENAME_PATTERN = re.compile(
    r"^taskgov-backup-v1_\d{8}T\d{6}Z_"
    r"[0-9a-f]{32}_r(?:[1-9]|1[0-9]|20)\.sqlite$"
)


@dataclass
class StateTransitionError(Exception):
    code: str = "setup_incomplete"
    message: str = STATE_TRANSITION_FAILURE_MESSAGE

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class StageOwner:
    stage_id: str
    project_id: str
    inventory_fingerprint: str


@dataclass(frozen=True)
class OwnedStage:
    owner: StageOwner
    owner_file: ValidatedFile
    stage_directory: ValidatedDirectory | None


@dataclass(frozen=True)
class StageResidue:
    owner: StageOwner
    owner_file: ValidatedFile
    stage_directory: ValidatedDirectory | None
    files: tuple[ValidatedFile, ...] = ()
    directories_deepest_first: tuple[ValidatedDirectory, ...] = ()


@dataclass(frozen=True)
class CleanupInventoryEntry:
    name: str
    size: int
    sha256: str
    kind: str = "file"


@dataclass(frozen=True)
class CleanupInventory:
    text: str = field(repr=False)
    fingerprint: str
    entries: tuple[CleanupInventoryEntry, ...]


@dataclass(frozen=True)
class CleanupEntryLocation:
    entry: CleanupInventoryEntry
    old_file: ValidatedFile | None = field(default=None, repr=False)
    retirement_file: ValidatedFile | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CleanupObservation:
    inventory: CleanupInventory
    state_root: Path = field(repr=False)
    old_root: Path = field(repr=False)
    retirement_root: Path = field(repr=False)
    locations: tuple[CleanupEntryLocation, ...]

    @property
    def old_files_remaining(self) -> int:
        return sum(item.old_file is not None for item in self.locations)

    @property
    def retirement_files_remaining(self) -> int:
        return sum(item.retirement_file is not None for item in self.locations)


@dataclass(frozen=True)
class CleanupFilesystemResult:
    moved: int
    deleted: int
    filesystem_complete: bool


def _failure() -> StateTransitionError:
    return StateTransitionError()


def _translate_error(exc: Exception) -> StateTransitionError:
    if isinstance(exc, StateTransitionError):
        return exc
    return _failure()


def _validate_project_id(project_id: str) -> str:
    for scheme in ("legacy_path_v1", "uuid_v1"):
        try:
            return validate_identity_project_id(project_id, scheme)
        except StorageError:
            continue
    raise _failure()


def _validate_legacy_project_id(project_id: str) -> str:
    try:
        return validate_identity_project_id(project_id, "legacy_path_v1")
    except StorageError as exc:
        raise _failure() from exc


def _validate_fingerprint(value: str) -> str:
    if not isinstance(value, str) or LOWER_HEX_64_PATTERN.fullmatch(value) is None:
        raise _failure()
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def encode_stage_owner(owner: StageOwner) -> bytes:
    if (
        not isinstance(owner, StageOwner)
        or not isinstance(owner.stage_id, str)
        or _STAGE_ID_PATTERN.fullmatch(owner.stage_id) is None
    ):
        raise _failure()
    _validate_project_id(owner.project_id)
    _validate_fingerprint(owner.inventory_fingerprint)
    encoded = json.dumps(
        {
            "inventory_fingerprint": owner.inventory_fingerprint,
            "project_id": owner.project_id,
            "stage_id": owner.stage_id,
            "v": 1,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if not 1 <= len(encoded) <= STAGE_OWNER_MAX_BYTES:
        raise _failure()
    return encoded


def decode_stage_owner(data: bytes) -> StageOwner:
    if (
        not isinstance(data, bytes)
        or not 1 <= len(data) <= STAGE_OWNER_MAX_BYTES
        or not data.isascii()
    ):
        raise _failure()
    try:
        payload = json.loads(
            data.decode("ascii"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, ValueError) as exc:
        raise _failure() from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {
            "inventory_fingerprint",
            "project_id",
            "stage_id",
            "v",
        }
        or type(payload.get("v")) is not int
        or payload["v"] != 1
    ):
        raise _failure()
    owner = StageOwner(
        stage_id=payload.get("stage_id"),
        project_id=payload.get("project_id"),
        inventory_fingerprint=payload.get("inventory_fingerprint"),
    )
    if encode_stage_owner(owner) != data:
        raise _failure()
    return owner


def stage_paths(state_root: Path, stage_id: str) -> tuple[Path, Path]:
    if not isinstance(stage_id, str) or _STAGE_ID_PATTERN.fullmatch(stage_id) is None:
        raise _failure()
    state_root = Path(state_root)
    stage = state_root / f".current-stage-{stage_id}"
    owner = state_root / f".current-stage-{stage_id}.owner"
    require_contained(stage, state_root)
    require_contained(owner, state_root)
    return stage, owner


@contextmanager
def state_transition_lock(state_root: Path) -> Iterator[None]:
    """Take the one fail-fast package-state lock after read-only planning."""

    entered = False
    try:
        root = inspect_physical_directory(Path(state_root))
        lock_path = root.path / STATE_TRANSITION_LOCK_FILENAME
        with zero_wait_artifact_lock(lock_path):
            entered = True
            yield
    except ArtifactLockError as exc:
        code = "database_busy" if exc.contended else "setup_incomplete"
        raise StateTransitionError(code=code) from exc
    except Exception as exc:
        if entered:
            raise
        raise _translate_error(exc) from exc


def inspect_state_transition_lock(state_root: Path) -> None:
    """Validate an existing package-state lock without creating or taking it."""

    if not path_lexically_exists(state_root):
        return
    try:
        root = inspect_physical_directory(Path(state_root))
        inspect_existing_artifact_lock(
            root.path / STATE_TRANSITION_LOCK_FILENAME
        )
    except ArtifactLockError as exc:
        raise StateTransitionError() from exc
    except Exception as exc:
        raise _translate_error(exc) from exc


def create_owned_stage(
    state_root: Path,
    *,
    project_id: str,
    inventory_fingerprint: str,
    stage_id: str | None = None,
) -> OwnedStage:
    """Persist the owner marker before creating its paired private directory."""

    try:
        root = inspect_physical_directory(Path(state_root))
        owner = StageOwner(
            stage_id=stage_id or secrets.token_hex(16),
            project_id=_validate_project_id(project_id),
            inventory_fingerprint=_validate_fingerprint(inventory_fingerprint),
        )
        stage_path, owner_path = stage_paths(root.path, owner.stage_id)
        if path_lexically_exists(stage_path) or path_lexically_exists(owner_path):
            raise _failure()
        owner_file = create_exclusive_durable_file(
            owner_path,
            encode_stage_owner(owner),
            root=root.path,
            max_bytes=STAGE_OWNER_MAX_BYTES,
        )
        stage_directory = create_physical_directory_exclusive(
            stage_path,
            root=root.path,
        )
        return OwnedStage(
            owner=owner,
            owner_file=owner_file,
            stage_directory=stage_directory,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


def _stage_relative_name(stage_root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(stage_root)
    except ValueError as exc:
        raise _failure() from exc
    return PurePosixPath(*relative.parts).as_posix()


def _valid_stage_file_name(relative_name: str) -> tuple[str, bool]:
    """Return the temporary class and backup flag for one exact allowed file."""

    if relative_name in {
        "taskgov.sqlite",
        "taskgov.sqlite-journal",
        "backups/taskgov-backup.lock",
        "viewer/task-viewer.html",
        "viewer/taskgov-viewer.lock",
    }:
        return "", False
    if _RESTORE_TEMP_PATTERN.fullmatch(relative_name):
        return "restore", False
    if relative_name.startswith("backups/"):
        basename = relative_name.removeprefix("backups/")
        if _BACKUP_BASENAME_PATTERN.fullmatch(basename):
            return "", True
        if _BACKUP_TEMP_PATTERN.fullmatch(basename):
            return "backup", False
    if relative_name.startswith("viewer/"):
        basename = relative_name.removeprefix("viewer/")
        if _VIEWER_TEMP_PATTERN.fullmatch(basename):
            return "viewer", False
    evidence_prefix = f"{EVIDENCE_DIRECTORY_NAME}/"
    if relative_name.startswith(evidence_prefix):
        evidence_name = relative_name.removeprefix(evidence_prefix)
        kind = evidence_relative_file_kind(evidence_name)
        if kind in {"index", "lock", "bundle"}:
            return "", False
        if kind == "index_temporary":
            return "evidence_index", False
        if kind == "bundle_temporary":
            return "evidence_bundle", False
    raise _failure()


def _inspect_stage_tree(
    stage_path: Path,
    *,
    state_root: Path,
    max_file_bytes: int,
) -> tuple[
    ValidatedDirectory,
    tuple[ValidatedFile, ...],
    tuple[ValidatedDirectory, ...],
]:
    if (
        isinstance(max_file_bytes, bool)
        or not isinstance(max_file_bytes, int)
        or max_file_bytes < 0
    ):
        raise _failure()
    stage = inspect_physical_directory(stage_path, root=state_root)
    try:
        root_entries = sorted(stage.path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise _failure() from exc

    directories: dict[str, ValidatedDirectory] = {}
    file_paths: list[Path] = []
    for entry in root_entries:
        if entry.name in {"backups", "viewer", EVIDENCE_DIRECTORY_NAME}:
            directories[entry.name] = inspect_physical_directory(
                entry,
                root=state_root,
            )
            try:
                children = sorted(entry.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                raise _failure() from exc
            for child in children:
                if (
                    entry.name == EVIDENCE_DIRECTORY_NAME
                    and child.name == EVIDENCE_BUNDLES_DIRECTORY_NAME
                ):
                    key = (
                        f"{EVIDENCE_DIRECTORY_NAME}/"
                        f"{EVIDENCE_BUNDLES_DIRECTORY_NAME}"
                    )
                    directories[key] = inspect_physical_directory(
                        child,
                        root=state_root,
                    )
                    try:
                        bundle_children = sorted(
                            child.iterdir(),
                            key=lambda item: item.name,
                        )
                    except OSError as exc:
                        raise _failure() from exc
                    for bundle_child in bundle_children:
                        try:
                            details = bundle_child.lstat()
                        except OSError as exc:
                            raise _failure() from exc
                        if not stat.S_ISREG(details.st_mode):
                            raise _failure()
                        file_paths.append(bundle_child)
                    continue
                try:
                    details = child.lstat()
                except OSError as exc:
                    raise _failure() from exc
                if not stat.S_ISREG(details.st_mode):
                    raise _failure()
                file_paths.append(child)
            continue
        try:
            details = entry.lstat()
        except OSError as exc:
            raise _failure() from exc
        if not stat.S_ISREG(details.st_mode):
            raise _failure()
        file_paths.append(entry)

    if len(file_paths) > STAGE_MAX_FILES:
        raise _failure()
    temporary_classes: set[str] = set()
    backup_count = 0
    files: list[ValidatedFile] = []
    for path in file_paths:
        relative_name = _stage_relative_name(stage.path, path)
        temporary_class, managed_backup = _valid_stage_file_name(relative_name)
        if temporary_class:
            if temporary_class in temporary_classes:
                raise _failure()
            temporary_classes.add(temporary_class)
        if managed_backup:
            backup_count += 1
            if backup_count > STAGE_MAX_BACKUPS:
                raise _failure()
        evidence_prefix = f"{EVIDENCE_DIRECTORY_NAME}/"
        evidence_kind = (
            evidence_relative_file_kind(
                relative_name.removeprefix(evidence_prefix)
            )
            if relative_name.startswith(evidence_prefix)
            else None
        )
        if relative_name in {
            "backups/taskgov-backup.lock",
            "viewer/taskgov-viewer.lock",
        } or evidence_kind == "lock":
            allowed_size = min(max_file_bytes, 1)
        elif evidence_kind in {"index", "index_temporary"}:
            allowed_size = EVIDENCE_INDEX_MAX_BYTES
        elif evidence_kind in {"bundle", "bundle_temporary"}:
            allowed_size = EVIDENCE_BUNDLE_MAX_BYTES
        else:
            allowed_size = max_file_bytes
        files.append(
            hash_physical_file(
                path,
                root=state_root,
                max_bytes=allowed_size,
            )
        )
    ordered_directories = [
        directories[name]
        for name in (
            f"{EVIDENCE_DIRECTORY_NAME}/{EVIDENCE_BUNDLES_DIRECTORY_NAME}",
            EVIDENCE_DIRECTORY_NAME,
            "viewer",
            "backups",
        )
        if name in directories
    ]
    ordered_directories.append(stage)
    return stage, tuple(files), tuple(ordered_directories)


def inspect_stage_residue(
    state_root: Path,
    *,
    max_file_bytes: int,
    expected_project_id: str | None = None,
    expected_inventory_fingerprint: str | None = None,
) -> StageResidue | None:
    """Validate zero or one exact owner/stage residue without changing it."""

    try:
        root = inspect_physical_directory(Path(state_root))
        try:
            names = sorted(item.name for item in root.path.iterdir())
        except OSError as exc:
            raise _failure() from exc
        stage_ids = [
            match.group(1)
            for name in names
            if (match := _STAGE_DIRECTORY_PATTERN.fullmatch(name)) is not None
        ]
        owner_ids = [
            match.group(1)
            for name in names
            if (match := _STAGE_OWNER_PATTERN.fullmatch(name)) is not None
        ]
        if not stage_ids and not owner_ids:
            return None
        if (
            len(stage_ids) > 1
            or len(owner_ids) > 1
            or not owner_ids
            or (stage_ids and stage_ids[0] != owner_ids[0])
        ):
            raise _failure()
        stage_id = owner_ids[0]
        stage_path, owner_path = stage_paths(root.path, stage_id)
        owner_bytes, owner_file = read_physical_file_bounded(
            owner_path,
            root=root.path,
            max_bytes=STAGE_OWNER_MAX_BYTES,
        )
        owner = decode_stage_owner(owner_bytes)
        if owner.stage_id != stage_id:
            raise _failure()
        if (
            expected_project_id is not None
            and owner.project_id != _validate_project_id(expected_project_id)
        ):
            raise _failure()
        if (
            expected_inventory_fingerprint is not None
            and owner.inventory_fingerprint
            != _validate_fingerprint(expected_inventory_fingerprint)
        ):
            raise _failure()
        if not stage_ids:
            return StageResidue(
                owner=owner,
                owner_file=owner_file,
                stage_directory=None,
            )
        stage, files, directories = _inspect_stage_tree(
            stage_path,
            state_root=root.path,
            max_file_bytes=max_file_bytes,
        )
        return StageResidue(
            owner=owner,
            owner_file=owner_file,
            stage_directory=stage,
            files=files,
            directories_deepest_first=directories,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


def validate_publishable_stage(residue: StageResidue) -> tuple[str, ...]:
    """Require a complete stage with no crash-only journal or temporary."""

    stage = residue.stage_directory
    if stage is None:
        raise _failure()
    names = tuple(
        sorted(
            _stage_relative_name(stage.path, item.path)
            for item in residue.files
        )
    )
    if (
        "taskgov.sqlite" not in names
        or "viewer/task-viewer.html" not in names
        or (
            f"{EVIDENCE_DIRECTORY_NAME}/{EVIDENCE_INDEX_FILENAME}"
            not in names
        )
    ):
        raise _failure()
    for name in names:
        temporary_class, _ = _valid_stage_file_name(name)
        if temporary_class or name == "taskgov.sqlite-journal":
            raise _failure()
    return names


def remove_stage_residue(state_root: Path, residue: StageResidue) -> None:
    """Remove only a previously validated stage and its owner marker."""

    try:
        root = inspect_physical_directory(Path(state_root))
        if residue.stage_directory is not None:
            remove_explicit_files_and_directories(
                root=root.path,
                files=residue.files,
                directories_deepest_first=residue.directories_deepest_first,
                max_files=STAGE_MAX_FILES,
                max_directories=STAGE_MAX_DIRECTORIES,
            )
        unlink_validated_file(residue.owner_file, root=root.path)
    except Exception as exc:
        raise _translate_error(exc) from exc


def build_cleanup_inventory(
    entries: Iterable[CleanupInventoryEntry],
) -> CleanupInventory:
    try:
        ordered = tuple(
            sorted(
                entries,
                key=lambda entry: entry.name.encode("utf-8"),
            )
        )
        payload = {
            "entries": [
                {
                    "kind": entry.kind,
                    "name": entry.name,
                    "sha256": entry.sha256,
                    "size": entry.size,
                }
                for entry in ordered
            ],
            "v": 1,
        }
        text = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(text.encode("ascii")).hexdigest()
        validate_cleanup_inventory(text, fingerprint)
        return CleanupInventory(
            text=text,
            fingerprint=fingerprint,
            entries=ordered,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


def parse_cleanup_inventory(
    text: str,
    fingerprint: str,
) -> CleanupInventory:
    try:
        validate_cleanup_inventory(text, fingerprint)
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        entries = tuple(
            CleanupInventoryEntry(
                kind=entry["kind"],
                name=entry["name"],
                size=entry["size"],
                sha256=entry["sha256"],
            )
            for entry in payload["entries"]
        )
        return CleanupInventory(
            text=text,
            fingerprint=fingerprint,
            entries=entries,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


def _entry_path(root: Path, entry: CleanupInventoryEntry) -> Path:
    relative = PurePosixPath(entry.name)
    path = root.joinpath(*relative.parts)
    require_contained(path, root)
    return path


def cleanup_roots(state_root: Path, project_id: str) -> tuple[Path, Path]:
    project_id = _validate_legacy_project_id(project_id)
    state_root = Path(state_root)
    old_root = state_root / "projects" / project_id
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    retirement_root = state_root / f".legacy-cleanup-{digest}"
    require_contained(old_root, state_root)
    require_contained(retirement_root, state_root)
    return old_root, retirement_root


def _physical_directory_if_present(
    path: Path,
    *,
    state_root: Path,
) -> ValidatedDirectory | None:
    if not path_lexically_exists(path):
        return None
    return inspect_physical_directory(path, root=state_root)


def _entry_file_if_present(
    path: Path,
    entry: CleanupInventoryEntry,
    *,
    state_root: Path,
    parent_root: Path,
) -> ValidatedFile | None:
    relative = path.relative_to(parent_root)
    parent = parent_root
    if not path_lexically_exists(parent):
        return None
    inspect_physical_directory(parent, root=state_root)
    for component in relative.parts[:-1]:
        parent /= component
        if not path_lexically_exists(parent):
            return None
        inspect_physical_directory(parent, root=state_root)
    if not path_lexically_exists(path):
        return None
    observed = hash_physical_file(
        path,
        root=state_root,
        expected_size=entry.size,
    )
    if observed.sha256 != entry.sha256:
        raise _failure()
    return observed


def _validate_retirement_inventory(
    retirement_root: Path,
    *,
    state_root: Path,
    expected_names: set[str],
) -> None:
    if not path_lexically_exists(retirement_root):
        return
    inspect_physical_directory(retirement_root, root=state_root)
    try:
        root_entries = sorted(retirement_root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise _failure() from exc
    observed_names: set[str] = set()
    for entry in root_entries:
        if entry.name in {"backups", "viewer", EVIDENCE_DIRECTORY_NAME}:
            inspect_physical_directory(entry, root=state_root)
            try:
                children = sorted(entry.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                raise _failure() from exc
            for child in children:
                if (
                    entry.name == EVIDENCE_DIRECTORY_NAME
                    and child.name == EVIDENCE_BUNDLES_DIRECTORY_NAME
                ):
                    inspect_physical_directory(child, root=state_root)
                    try:
                        bundle_children = sorted(
                            child.iterdir(),
                            key=lambda item: item.name,
                        )
                    except OSError as exc:
                        raise _failure() from exc
                    for bundle_child in bundle_children:
                        relative = PurePosixPath(
                            entry.name,
                            child.name,
                            bundle_child.name,
                        ).as_posix()
                        if relative not in expected_names:
                            raise _failure()
                        inspect_physical_file(bundle_child, root=state_root)
                        observed_names.add(relative)
                    continue
                relative = PurePosixPath(entry.name, child.name).as_posix()
                if relative not in expected_names:
                    raise _failure()
                inspect_physical_file(child, root=state_root)
                observed_names.add(relative)
            continue
        if entry.name not in expected_names:
            raise _failure()
        inspect_physical_file(entry, root=state_root)
        observed_names.add(entry.name)
    if not observed_names.issubset(expected_names):
        raise _failure()


def inspect_legacy_cleanup(
    state_root: Path,
    *,
    project_id: str,
    inventory_text: str,
    inventory_fingerprint: str,
) -> CleanupObservation:
    """Reconstruct the exact old/retirement state from persisted inventory."""

    try:
        root = inspect_physical_directory(Path(state_root))
        inventory = parse_cleanup_inventory(
            inventory_text,
            inventory_fingerprint,
        )
        old_root, retirement_root = cleanup_roots(root.path, project_id)
        _physical_directory_if_present(old_root, state_root=root.path)
        _validate_retirement_inventory(
            retirement_root,
            state_root=root.path,
            expected_names={entry.name for entry in inventory.entries},
        )
        locations: list[CleanupEntryLocation] = []
        for entry in inventory.entries:
            old_path = _entry_path(old_root, entry)
            retirement_path = _entry_path(retirement_root, entry)
            old_file = _entry_file_if_present(
                old_path,
                entry,
                state_root=root.path,
                parent_root=old_root,
            )
            retirement_file = _entry_file_if_present(
                retirement_path,
                entry,
                state_root=root.path,
                parent_root=retirement_root,
            )
            if old_file is not None and retirement_file is not None:
                raise _failure()
            locations.append(
                CleanupEntryLocation(
                    entry=entry,
                    old_file=old_file,
                    retirement_file=retirement_file,
                )
            )
        return CleanupObservation(
            inventory=inventory,
            state_root=root.path,
            old_root=old_root,
            retirement_root=retirement_root,
            locations=tuple(locations),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


def _ensure_retirement_parent(
    observation: CleanupObservation,
    entry: CleanupInventoryEntry,
) -> None:
    root = observation.state_root
    retirement = observation.retirement_root
    if not path_lexically_exists(retirement):
        create_physical_directory_exclusive(retirement, root=root)
    else:
        inspect_physical_directory(retirement, root=root)
    relative = PurePosixPath(entry.name)
    current = retirement
    for component in relative.parts[:-1]:
        current /= component
        if path_lexically_exists(current):
            inspect_physical_directory(current, root=root)
        else:
            create_physical_directory_exclusive(current, root=root)


def _remove_owned_empty_directories(observation: CleanupObservation) -> None:
    for base in (observation.retirement_root, observation.old_root):
        evidence_bundles = (
            base
            / EVIDENCE_DIRECTORY_NAME
            / EVIDENCE_BUNDLES_DIRECTORY_NAME
        )
        if path_lexically_exists(evidence_bundles):
            rmdir_if_empty(
                evidence_bundles,
                root=observation.state_root,
            )
        for child_name in (EVIDENCE_DIRECTORY_NAME, "viewer", "backups"):
            child = base / child_name
            if path_lexically_exists(child):
                rmdir_if_empty(child, root=observation.state_root)
        if path_lexically_exists(base):
            rmdir_if_empty(base, root=observation.state_root)


def retire_legacy_inventory(
    state_root: Path,
    *,
    project_id: str,
    inventory_text: str,
    inventory_fingerprint: str,
) -> CleanupFilesystemResult:
    """Move, then delete, only persisted entries; retry is naturally bounded."""

    try:
        observation = inspect_legacy_cleanup(
            state_root,
            project_id=project_id,
            inventory_text=inventory_text,
            inventory_fingerprint=inventory_fingerprint,
        )
        moved = 0
        deleted = 0
        for location in observation.locations:
            if location.old_file is None:
                continue
            _ensure_retirement_parent(observation, location.entry)
            destination = _entry_path(
                observation.retirement_root,
                location.entry,
            )
            moved_file = rename_no_replace(
                location.old_file,
                destination,
                root=observation.state_root,
            )
            if not isinstance(moved_file, ValidatedFile):
                raise _failure()
            verified = hash_physical_file(
                destination,
                root=observation.state_root,
                expected_size=location.entry.size,
            )
            if verified.sha256 != location.entry.sha256:
                raise _failure()
            moved += 1

        observation = inspect_legacy_cleanup(
            state_root,
            project_id=project_id,
            inventory_text=inventory_text,
            inventory_fingerprint=inventory_fingerprint,
        )
        if observation.old_files_remaining:
            raise _failure()
        for location in observation.locations:
            if location.retirement_file is None:
                continue
            unlink_validated_file(
                location.retirement_file,
                root=observation.state_root,
            )
            deleted += 1
        _remove_owned_empty_directories(observation)

        final = inspect_legacy_cleanup(
            state_root,
            project_id=project_id,
            inventory_text=inventory_text,
            inventory_fingerprint=inventory_fingerprint,
        )
        complete = (
            final.old_files_remaining == 0
            and final.retirement_files_remaining == 0
            and not path_lexically_exists(final.retirement_root)
        )
        if not complete:
            raise _failure()
        return CleanupFilesystemResult(
            moved=moved,
            deleted=deleted,
            filesystem_complete=True,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
