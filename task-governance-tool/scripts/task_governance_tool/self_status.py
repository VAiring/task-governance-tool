"""Offline, read-only inspection of the installed skill package."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_FILENAME = "release-manifest.json"
MANIFEST_VERSION = 1
PACKAGE_NAME = "task-governance-tool"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_CORE_FILES = 512
MAX_PACKAGE_ENTRIES = 2048
MAX_CORE_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_CORE_BYTES = 256 * 1024 * 1024
MAX_CHANGED_CORE_PATHS = 20
MAX_PACKAGE_NAME_LENGTH = 100
MAX_PACKAGE_VERSION_LENGTH = 64
MAX_RELEASE_ORIGIN_LENGTH = 200
MAX_JSON_INTEGER_LENGTH = 20

_EXPECTED_MANIFEST_KEYS = {
    "manifest_version",
    "package_name",
    "package_version",
    "release_origin",
    "core_files",
}
_EXCLUDED_ROOT_DIRECTORIES = {"adapters", "config", "state"}
_PORTABLE_PATH_PATTERN = re.compile(r"[A-Za-z0-9._/-]{1,500}\Z")
_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_ORIGIN_PATTERN = re.compile(r"github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class _DuplicateJsonKey(ValueError):
    pass


class _ManifestInvalid(ValueError):
    pass


class _InspectionIncomplete(OSError):
    def __init__(self, reason: str = "inspection_incomplete") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ReleaseManifest:
    manifest_version: int
    package_name: str
    package_version: str
    release_origin: str
    core_files: dict[str, str]


@dataclass(frozen=True)
class PackageSelfStatus:
    package_name: str
    package_version: str
    release_origin: str | None
    manifest_version: int | None
    status: str
    changed_core_count: int
    changed_core_paths: tuple[str, ...]
    changed_core_paths_truncated: bool
    unknown_reasons: tuple[str, ...]
    suggested_action: str = "continue"

    def to_data(self) -> dict[str, Any]:
        return {
            "package_name": self.package_name,
            "package_version": self.package_version,
            "release_origin": self.release_origin,
            "manifest_version": self.manifest_version,
            "status": self.status,
            "changed_core_count": self.changed_core_count,
            "changed_core_paths": list(self.changed_core_paths),
            "changed_core_paths_truncated": self.changed_core_paths_truncated,
            "unknown_reasons": list(self.unknown_reasons),
            "suggested_action": self.suggested_action,
        }


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _bounded_json_integer(value: str) -> int:
    if len(value) > MAX_JSON_INTEGER_LENGTH:
        raise _ManifestInvalid("JSON integer is too large")
    return int(value)


def _reject_json_constant(value: str) -> None:
    raise _ManifestInvalid(f"unsupported JSON constant: {value}")


def _is_junction(path: Path) -> bool:
    predicate = getattr(path, "is_junction", None)
    return bool(predicate is not None and predicate())


def _entry_is_linklike(entry: os.DirEntry[str], path: Path) -> bool:
    return entry.is_symlink() or _is_junction(path)


def _portable_core_path(value: Any) -> str:
    if not isinstance(value, str) or _PORTABLE_PATH_PATTERN.fullmatch(value) is None:
        raise _ManifestInvalid("invalid core path")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise _ManifestInvalid("invalid core path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _ManifestInvalid("invalid core path")
    if parts[0] in _EXCLUDED_ROOT_DIRECTORIES:
        raise _ManifestInvalid("excluded path in core manifest")
    if "__pycache__" in parts or value.endswith(".pyc"):
        raise _ManifestInvalid("cache path in core manifest")
    if value == MANIFEST_FILENAME:
        raise _ManifestInvalid("manifest cannot hash itself")
    return value


def _safe_output_path(value: str) -> bool:
    if _PORTABLE_PATH_PATTERN.fullmatch(value) is None:
        return False
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _same_file_observation(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_size,
        second.st_mtime_ns,
    )


def _open_regular_readonly(path: Path) -> tuple[int, os.stat_result, os.stat_result]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise _InspectionIncomplete()
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_observation(before, opened):
            raise _InspectionIncomplete()
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, before, opened


def _read_manifest_bytes(path: Path) -> bytes:
    descriptor, before, opened = _open_regular_readonly(path)
    try:
        if opened.st_size > MAX_MANIFEST_BYTES:
            raise _ManifestInvalid("manifest is too large")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_MANIFEST_BYTES:
                raise _ManifestInvalid("manifest is too large")
            chunks.append(chunk)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = path.lstat()
    if not _same_file_observation(opened, after_open) or not _same_file_observation(
        before, after_path
    ):
        raise _InspectionIncomplete()
    return b"".join(chunks)


def _load_manifest(skill_root: Path) -> ReleaseManifest:
    manifest_path = skill_root / MANIFEST_FILENAME
    try:
        raw_bytes = _read_manifest_bytes(manifest_path)
    except FileNotFoundError:
        raise _InspectionIncomplete("manifest_missing") from None
    except _ManifestInvalid:
        raise
    except (OSError, RuntimeError):
        raise _InspectionIncomplete("manifest_unreadable") from None

    try:
        payload = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_int=_bounded_json_integer,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        _ManifestInvalid,
        ValueError,
        RecursionError,
    ):
        raise _ManifestInvalid("manifest is not strict UTF-8 JSON") from None

    if not isinstance(payload, dict) or set(payload) != _EXPECTED_MANIFEST_KEYS:
        raise _ManifestInvalid("manifest fields are invalid")

    manifest_version = payload["manifest_version"]
    if isinstance(manifest_version, bool) or not isinstance(manifest_version, int):
        raise _ManifestInvalid("manifest version is invalid")

    package_name = payload["package_name"]
    package_version = payload["package_version"]
    release_origin = payload["release_origin"]
    core_files = payload["core_files"]
    if (
        not isinstance(package_name, str)
        or not package_name
        or len(package_name) > MAX_PACKAGE_NAME_LENGTH
    ):
        raise _ManifestInvalid("package name is invalid")
    if (
        not isinstance(package_version, str)
        or len(package_version) > MAX_PACKAGE_VERSION_LENGTH
        or _VERSION_PATTERN.fullmatch(package_version) is None
    ):
        raise _ManifestInvalid("package version is invalid")
    if (
        not isinstance(release_origin, str)
        or len(release_origin) > MAX_RELEASE_ORIGIN_LENGTH
        or _ORIGIN_PATTERN.fullmatch(release_origin) is None
    ):
        raise _ManifestInvalid("release origin is invalid")
    if not isinstance(core_files, dict) or not core_files or len(core_files) > MAX_CORE_FILES:
        raise _ManifestInvalid("core file map is invalid")

    normalized_files: dict[str, str] = {}
    casefolded_paths: set[str] = set()
    for raw_path, raw_digest in core_files.items():
        path = _portable_core_path(raw_path)
        folded = path.casefold()
        if folded in casefolded_paths:
            raise _ManifestInvalid("core paths collide by case")
        if not isinstance(raw_digest, str) or _SHA256_PATTERN.fullmatch(raw_digest) is None:
            raise _ManifestInvalid("core digest is invalid")
        casefolded_paths.add(folded)
        normalized_files[path] = raw_digest

    return ReleaseManifest(
        manifest_version=manifest_version,
        package_name=package_name,
        package_version=package_version,
        release_origin=release_origin,
        core_files=dict(sorted(normalized_files.items())),
    )


def _is_excluded_entry(
    entry: os.DirEntry[str],
    path: Path,
    relative_path: str,
    *,
    at_root: bool,
) -> bool:
    if relative_path == MANIFEST_FILENAME and at_root:
        return True
    if entry.name.endswith(".pyc"):
        return True
    if entry.name == "__pycache__":
        try:
            return entry.is_dir(follow_symlinks=False) or _entry_is_linklike(entry, path)
        except OSError:
            raise _InspectionIncomplete() from None
    if at_root and entry.name in _EXCLUDED_ROOT_DIRECTORIES:
        try:
            return entry.is_dir(follow_symlinks=False) or _entry_is_linklike(entry, path)
        except OSError:
            raise _InspectionIncomplete() from None
    return False


def _observe_directory(path: Path) -> os.stat_result:
    try:
        observed = path.lstat()
        if (
            not stat.S_ISDIR(observed.st_mode)
            or path.is_symlink()
            or _is_junction(path)
        ):
            raise _InspectionIncomplete()
        return observed
    except (OSError, RuntimeError):
        raise _InspectionIncomplete() from None


def _revalidate_directories(
    observations: tuple[tuple[Path, os.stat_result], ...],
) -> None:
    for path, before in observations:
        after = _observe_directory(path)
        if not _same_file_observation(before, after):
            raise _InspectionIncomplete()


def _scan_core_entries(
    skill_root: Path,
) -> tuple[list[str], set[str], tuple[tuple[Path, os.stat_result], ...]]:
    root_observation = _observe_directory(skill_root)
    stack: list[tuple[Path, str, os.stat_result]] = [
        (skill_root, "", root_observation)
    ]
    file_paths: list[str] = []
    non_regular_paths: set[str] = set()
    directory_observations: list[tuple[Path, os.stat_result]] = []
    entry_count = 0

    while stack:
        directory, relative_directory, discovered = stack.pop()
        before_scan = _observe_directory(directory)
        if not _same_file_observation(discovered, before_scan):
            raise _InspectionIncomplete()
        directory_observations.append((directory, before_scan))
        try:
            with os.scandir(directory) as iterator:
                entries: list[os.DirEntry[str]] = []
                for entry in iterator:
                    entry_count += 1
                    if entry_count > MAX_PACKAGE_ENTRIES:
                        raise _InspectionIncomplete("inspection_limit_exceeded")
                    entries.append(entry)
                entries.sort(key=lambda item: item.name)
            if not _same_file_observation(
                before_scan,
                _observe_directory(directory),
            ):
                raise _InspectionIncomplete()
        except _InspectionIncomplete:
            raise
        except OSError:
            raise _InspectionIncomplete() from None

        child_directories: list[tuple[Path, str, os.stat_result]] = []
        for entry in entries:
            relative_path = (
                entry.name
                if not relative_directory
                else f"{relative_directory}/{entry.name}"
            )
            path = directory / entry.name
            if _is_excluded_entry(
                entry,
                path,
                relative_path,
                at_root=not relative_directory,
            ):
                continue
            if not _safe_output_path(relative_path):
                raise _InspectionIncomplete("unsafe_core_path")
            try:
                if _entry_is_linklike(entry, path):
                    non_regular_paths.add(relative_path)
                elif entry.is_dir(follow_symlinks=False):
                    child_directories.append(
                        (path, relative_path, path.lstat())
                    )
                elif entry.is_file(follow_symlinks=False):
                    file_paths.append(relative_path)
                else:
                    non_regular_paths.add(relative_path)
            except OSError:
                raise _InspectionIncomplete() from None
        stack.extend(reversed(child_directories))

    observations = tuple(directory_observations)
    _revalidate_directories(observations)
    return sorted(file_paths), non_regular_paths, observations


def _hash_core_file(path: Path, *, remaining_total_bytes: int) -> tuple[str, int]:
    descriptor, before, opened = _open_regular_readonly(path)
    try:
        if (
            opened.st_size > MAX_CORE_FILE_BYTES
            or opened.st_size > remaining_total_bytes
        ):
            raise _InspectionIncomplete("inspection_limit_exceeded")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 128 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CORE_FILE_BYTES or total > remaining_total_bytes:
                raise _InspectionIncomplete("inspection_limit_exceeded")
            digest.update(chunk)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = path.lstat()
    if not _same_file_observation(opened, after_open) or not _same_file_observation(
        before, after_path
    ):
        raise _InspectionIncomplete()
    return f"sha256:{digest.hexdigest()}", total


def _unknown_status(
    *,
    package_version: str,
    reason: str,
    manifest_version: int | None = None,
    release_origin: str | None = None,
) -> PackageSelfStatus:
    return PackageSelfStatus(
        package_name=PACKAGE_NAME,
        package_version=package_version,
        release_origin=release_origin,
        manifest_version=manifest_version,
        status="unknown",
        changed_core_count=0,
        changed_core_paths=(),
        changed_core_paths_truncated=False,
        unknown_reasons=(reason,),
    )


def inspect_local_package(
    skill_root: str | os.PathLike[str],
    *,
    installed_version: str,
) -> PackageSelfStatus:
    """Compare packaged core files with the co-located release manifest."""

    root = Path(skill_root)
    try:
        manifest = _load_manifest(root)
    except _ManifestInvalid:
        return _unknown_status(
            package_version=installed_version,
            reason="manifest_invalid",
        )
    except _InspectionIncomplete as exc:
        return _unknown_status(
            package_version=installed_version,
            reason=exc.reason,
        )

    if manifest.manifest_version != MANIFEST_VERSION:
        return _unknown_status(
            package_version=installed_version,
            reason="manifest_unsupported",
            manifest_version=manifest.manifest_version,
            release_origin=manifest.release_origin,
        )
    if manifest.package_name != PACKAGE_NAME:
        return _unknown_status(
            package_version=installed_version,
            reason="package_identity_mismatch",
            manifest_version=manifest.manifest_version,
            release_origin=manifest.release_origin,
        )
    if manifest.package_version != installed_version:
        return _unknown_status(
            package_version=installed_version,
            reason="package_version_mismatch",
            manifest_version=manifest.manifest_version,
            release_origin=manifest.release_origin,
        )

    try:
        actual_files, non_regular_paths, directory_observations = (
            _scan_core_entries(root)
        )
        _revalidate_directories(directory_observations)
        expected_paths = set(manifest.core_files)
        actual_paths = set(actual_files)
        changed_paths = set(non_regular_paths)
        changed_paths.update(expected_paths - actual_paths)
        changed_paths.update(actual_paths - expected_paths)

        total_hashed_bytes = 0
        for relative_path in sorted(expected_paths & actual_paths):
            path = root / Path(*relative_path.split("/"))
            digest, byte_count = _hash_core_file(
                path,
                remaining_total_bytes=MAX_TOTAL_CORE_BYTES - total_hashed_bytes,
            )
            total_hashed_bytes += byte_count
            if digest != manifest.core_files[relative_path]:
                changed_paths.add(relative_path)
        _revalidate_directories(directory_observations)
    except _InspectionIncomplete as exc:
        return _unknown_status(
            package_version=installed_version,
            reason=exc.reason,
            manifest_version=manifest.manifest_version,
            release_origin=manifest.release_origin,
        )
    except OSError:
        return _unknown_status(
            package_version=installed_version,
            reason="inspection_incomplete",
            manifest_version=manifest.manifest_version,
            release_origin=manifest.release_origin,
        )

    sorted_changes = sorted(changed_paths)
    return PackageSelfStatus(
        package_name=PACKAGE_NAME,
        package_version=installed_version,
        release_origin=manifest.release_origin,
        manifest_version=manifest.manifest_version,
        status="modified" if sorted_changes else "clean",
        changed_core_count=len(sorted_changes),
        changed_core_paths=tuple(sorted_changes[:MAX_CHANGED_CORE_PATHS]),
        changed_core_paths_truncated=len(sorted_changes) > MAX_CHANGED_CORE_PATHS,
        unknown_reasons=(),
    )
