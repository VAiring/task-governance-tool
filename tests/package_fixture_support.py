"""Explicit immutable package bases for independent physical test copies.

Freezing is a content capture, never a claim that a directory is unchanged.
Call freeze_package again for mutable/different material. The named current
basis is an explicit, process-local choice made by current-package callers.
"""
from __future__ import annotations

import os
import stat
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _Entry:
    relative: str
    data: bytes | None
    mode: int


@dataclass(frozen=True)
class FrozenPackage:
    entries: tuple[_Entry, ...]

    @classmethod
    def capture_prepared(cls, root: Path) -> FrozenPackage:
        entries = []
        for path in (root, *sorted(root.rglob("*"))):
            info = path.lstat()
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("frozen test packages require physical files")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError("frozen test packages require regular entries")
            entries.append(_Entry(path.relative_to(root).as_posix(),
                path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
                stat.S_IMODE(info.st_mode)))
        return cls(tuple(entries))

    def copy_to(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.mkdir(parents=True)
        directories = []
        files = []
        for entry in self.entries:
            path = destination if entry.relative == "." else destination / entry.relative
            if entry.data is None:
                if path != destination:
                    path.mkdir()
                directories.append((path, entry))
            else:
                with path.open("xb") as stream:
                    stream.write(entry.data)
                files.append(path)
                if os.name != "nt" or not entry.mode & stat.S_IWRITE:
                    os.chmod(path, entry.mode)
        for path, entry in reversed(directories):
            if os.name != "nt" or not entry.mode & stat.S_IWRITE:
                os.chmod(path, entry.mode)
        read_fresh_files(files)
        return destination


def read_fresh_files(files) -> None:
    """Overlap first reads of a caller's fresh independent physical copies.

    This Windows-only preparation neither regenerates a manifest nor caches
    validation or digests. Every checker still reads and hashes the real files.
    """
    if os.name == "nt":
        files = tuple(files)
        if files:
            with ThreadPoolExecutor(max_workers=min(8, len(files))) as executor:
                for _ in executor.map(_read_fresh_file, files):
                    pass


def _read_fresh_file(path: Path) -> None:
    with path.open("rb") as stream:
        while stream.read(1024 * 1024):
            pass


def freeze_package(source_root: Path) -> FrozenPackage:
    """Generate the real matching manifest, then freeze this exact content once."""
    from tests.m14_test_support import _copy_skill
    with tempfile.TemporaryDirectory(prefix="taskgov-package-basis-") as temporary:
        prepared = _copy_skill(Path(temporary), source_root=source_root)
        return FrozenPackage.capture_prepared(prepared)


class _CurrentPackageBasis:
    """One explicitly declared immutable candidate, shared by helper aliases."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = None

    def copy_to(self, destination: Path) -> Path:
        with self._lock:
            if self._snapshot is None:
                source = Path(__file__).resolve().parents[1] / "task-governance-tool"
                self._snapshot = freeze_package(source)
            snapshot = self._snapshot
        return snapshot.copy_to(destination)


CURRENT_TEST_PACKAGE = _CurrentPackageBasis()
