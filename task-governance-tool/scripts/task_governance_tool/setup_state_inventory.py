"""Setup-owned inventories for the generated domains in a state cutover.

This is not the legacy 32-file stage validator. Each generated domain retains
its own closed names and limits; opaque old files are left at their source.
Private trees, unlike old state, may contain only entries owned by this cutover.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from task_governance_tool.state_paths import (
    EVIDENCE_BUNDLE_MAX_BYTES, EVIDENCE_INDEX_MAX_BYTES,
    EVIDENCE_MAX_BUNDLE_FILES, ValidatedDirectory, ValidatedFile,
    copy_physical_file_exclusive, create_exclusive_durable_file,
    create_physical_directory_exclusive, evidence_relative_file_kind,
    hash_physical_file, inspect_physical_directory, inspect_physical_file,
    path_lexically_exists,
)
from task_governance_tool.state_separation import SeparationError, SeparationRecord
from task_governance_tool.verification_runner_lifecycle import (
    inspect_runner_layout, verification_runner_state_paths,
)


_BACKUP = re.compile(
    r"taskgov-backup-v1_\d{8}T\d{6}Z_[0-9a-f]{32}_r(?:[1-9]|1[0-9]|20)\.sqlite\Z"
)
_TEMPS = {
    "": re.compile(r"\.taskgov-restore-[a-z0-9_]{8}\.tmp\Z"),
    "backups": re.compile(r"\.taskgov-backup-[a-z0-9_]{8}\.tmp\Z"),
    "viewer": re.compile(r"\.task-viewer-[a-z0-9_]{8}\.tmp\Z"),
}
_LOCKS = {
    "backups/taskgov-backup.lock", "viewer/taskgov-viewer.lock",
    "evidence/taskgov-evidence.lock",
    "verification-runner/taskgov-verification-runner.lock",
}


@dataclass(frozen=True)
class StateInventory:
    root: Path
    files: tuple[ValidatedFile, ...]
    directories: tuple[ValidatedDirectory, ...]
    held_lock_bytes: tuple[tuple[str, bytes], ...] = ()

    @property
    def digest(self) -> str:
        payload = {
            "directories": [p.path.relative_to(self.root).as_posix()
                            for p in self.directories],
            "files": [[p.path.relative_to(self.root).as_posix(),
                       p.identity.size, p.sha256] for p in self.files],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(b"taskgov-state-cutover-inventory-v1\0" +
                              encoded.encode("ascii")).hexdigest()


def inspect_inventory(
    root: Path, *, strict: bool, held_locks: dict[Path, bytes] | None = None,
    repair_evidence: bool = False, retained_artifact_max_bytes: int = 0,
) -> StateInventory:
    """Inspect physical recognized entries; never acquire locks or open SQLite."""
    inspect_physical_directory(root)
    held_locks = held_locks or {}
    files: list[ValidatedFile] = []
    directories: list[ValidatedDirectory] = []
    locks: list[tuple[str, bytes]] = []
    database = root / "taskgov.sqlite"
    source_size = (inspect_physical_file(database, root=root)[1].size
                   if path_lexically_exists(database) else 0)
    if not source_size and path_lexically_exists(root / "backups"):
        # Backup-only recovery has no primary whose size could supply the
        # existing resolver's artifact bound; its validated generations do.
        inspect_physical_directory(root / "backups", root=root)
        for backup in (root / "backups").iterdir():
            if _BACKUP.fullmatch(backup.name):
                source_size = max(source_size, inspect_physical_file(backup, root=root)[1].size)
    # Barrier repair may have no old primary left to supply its former bound.
    # Its caller derives this allowance from a sealed retained inventory and
    # subsequently requires exact equality; ordinary source admission stays
    # unchanged.
    extra_maximum = max(source_size + 16_777_216, retained_artifact_max_bytes)

    def directory(path: Path) -> None:
        directories.append(inspect_physical_directory(path, root=root))

    def file(path: Path, maximum: int | None) -> None:
        relative = path.relative_to(root).as_posix()
        if path in held_locks:
            _, identity = inspect_physical_file(path, root=root, max_bytes=1)
            content = held_locks[path]
            if relative not in _LOCKS or len(content) != identity.size:
                raise SeparationError()
            files.append(ValidatedFile(path, identity, hashlib.sha256(content).hexdigest()))
            locks.append((relative, content))
        else:
            files.append(hash_physical_file(path, root=root, max_bytes=maximum))

    def ordinary_entries(path: Path, domain: str) -> None:
        temporary_count = 0
        backup_count = 0
        for entry in sorted(path.iterdir(), key=lambda p: p.name):
            name = entry.name
            relative = entry.relative_to(root).as_posix()
            if relative in _LOCKS:
                file(entry, 1)
            elif domain == "backups" and _BACKUP.fullmatch(name):
                backup_count += 1
                if backup_count > 21:
                    raise SeparationError()
                file(entry, extra_maximum)
            elif domain == "viewer" and name == "task-viewer.html":
                file(entry, extra_maximum)
            elif _TEMPS[domain].fullmatch(name):
                temporary_count += 1
                if temporary_count > 1:
                    raise SeparationError()
                file(entry, extra_maximum)
            elif strict:
                raise SeparationError()

    def evidence(path: Path) -> None:
        bundle_count = 0
        temporary_kinds: set[str] = set()
        entries: list[Path] = []
        for entry in sorted(path.iterdir(), key=lambda p: p.name):
            if entry.name == "bundles":
                directory(entry)
                entries.extend(sorted(entry.iterdir(), key=lambda p: p.name))
            else:
                entries.append(entry)
        if len(entries) > EVIDENCE_MAX_BUNDLE_FILES + 4:
            raise SeparationError()
        for entry in entries:
            kind = evidence_relative_file_kind(entry.relative_to(path).as_posix())
            if kind is None:
                raise SeparationError()
            if kind == "bundle":
                bundle_count += 1
                if bundle_count > EVIDENCE_MAX_BUNDLE_FILES:
                    raise SeparationError()
            if kind.endswith("temporary"):
                if kind in temporary_kinds:
                    raise SeparationError()
                temporary_kinds.add(kind)
            maximum = (1 if kind == "lock" else EVIDENCE_INDEX_MAX_BYTES
                       if kind.startswith("index") else EVIDENCE_BUNDLE_MAX_BYTES)
            if repair_evidence and kind in {"index", "bundle"}:
                maximum = None
            file(entry, maximum)

    root_temps = 0
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        name = entry.name
        if name in {"taskgov.sqlite", "taskgov.sqlite-journal"}:
            file(entry, None if name == "taskgov.sqlite" else extra_maximum)
        elif name in {"taskgov.sqlite-wal", "taskgov.sqlite-shm"}:
            raise SeparationError()
        elif name in {"backups", "viewer", "evidence", "verification-runner"}:
            directory(entry)
            if name == "evidence":
                evidence(entry)
            elif name == "verification-runner":
                runner = verification_runner_state_paths(entry)
                observed = inspect_runner_layout(runner)
                if observed.attempt_ids or observed.quarantine_ids:
                    raise SeparationError()
                directory(runner.attempts)
                directory(runner.quarantine)
                if path_lexically_exists(runner.lock):
                    file(runner.lock, 1)
            else:
                ordinary_entries(entry, name)
        elif _TEMPS[""].fullmatch(name):
            root_temps += 1
            if root_temps > 1:
                raise SeparationError()
            file(entry, extra_maximum)
        elif strict:
            raise SeparationError()
    return StateInventory(
        root, tuple(sorted(files, key=lambda p: p.path.as_posix())),
        tuple(sorted(directories, key=lambda p: p.path.as_posix())), tuple(locks),
    )


def inspect_retired_source(
    old_state_root: Path, retained_root: Path, record: SeparationRecord,
    *, held_locks: dict[Path, bytes] | None = None,
) -> Path | None:
    """Admit only recorded old material when repairing a missing barrier.

    This is not an ordinary resolver scan. A snapshot's primary bytes can
    differ from the original SQLite file; fixed-source comparison therefore
    excludes only that replaced primary. Legacy originals were never replaced
    and must still match their complete original inventory fingerprint.
    """
    retained = None
    if record.source_layout is not None:
        retained = inspect_inventory(retained_root, strict=True, repair_evidence=True)
        if retained.digest != record.retained_digest:
            raise SeparationError()
    if not path_lexically_exists(old_state_root):
        if retained is not None:
            raise SeparationError()
        return None
    inspect_physical_directory(old_state_root)
    projects = old_state_root / "projects"
    legacy_root = None
    if path_lexically_exists(projects):
        inspect_physical_directory(projects, root=old_state_root)
        entries = iter(projects.iterdir())
        first = next(entries, None)
        if record.source_layout == "legacy_projects_v1":
            if (first is None or first.name != record.project_id
                    or next(entries, None) is not None):
                raise SeparationError()
            legacy_root = first
        elif first is not None:
            # Fixed-primary precedence may previously have hidden a candidate;
            # the cutover record does not prove that competing material's basis.
            raise SeparationError()
    if record.source_layout == "legacy_projects_v1" and legacy_root is None:
        raise SeparationError()

    fixed_root = old_state_root / "current"
    fixed = (inspect_inventory(
        fixed_root, strict=False, held_locks=held_locks, repair_evidence=True,
        retained_artifact_max_bytes=max(
            (item.identity.size for item in retained.files), default=0,
        ) if retained is not None else 0,
    ) if path_lexically_exists(fixed_root) else StateInventory(fixed_root, (), ()))
    if record.source_layout == "fixed_current_v1":
        expected = replace(retained, files=tuple(
            item for item in retained.files
            if item.path.relative_to(retained.root).as_posix() != "taskgov.sqlite"
        ))
        if fixed.digest != expected.digest:
            raise SeparationError()
        return fixed_root
    if fixed.files or fixed.directories:
        raise SeparationError()
    if legacy_root is not None:
        original = inspect_inventory(
            legacy_root, strict=False, held_locks=held_locks, repair_evidence=True,
        )
        if original.digest != record.source_fingerprint:
            raise SeparationError()
        return legacy_root
    return None


def copy_inventory(inventory: StateInventory, destination: Path, *, skip_database: bool) -> None:
    """Copy only this observed inventory into an existing, empty private root."""
    inspect_physical_directory(destination)
    if any(destination.iterdir()) and not (
        skip_database and set(p.name for p in destination.iterdir()) == {"taskgov.sqlite"}
    ):
        raise SeparationError()
    for observed in sorted(inventory.directories, key=lambda p: len(p.path.parts)):
        relative = observed.path.relative_to(inventory.root)
        inspect_physical_directory(observed.path, root=inventory.root)
        create_physical_directory_exclusive(destination / relative, root=destination)
    held = dict(inventory.held_lock_bytes)
    for observed in inventory.files:
        relative = observed.path.relative_to(inventory.root)
        if skip_database and relative.as_posix() == "taskgov.sqlite":
            continue
        output = destination / relative
        if relative.as_posix() in held:
            create_exclusive_durable_file(output, held[relative.as_posix()],
                                         root=destination, max_bytes=1)
        else:
            copy_physical_file_exclusive(observed, output, source_root=inventory.root,
                                         destination_root=destination,
                                         max_bytes=observed.identity.size)
