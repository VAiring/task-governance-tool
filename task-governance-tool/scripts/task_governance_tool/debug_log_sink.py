"""Opt-in admission and bounded daily JSONL append, outside SQLite lifetimes."""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import re
import stat
import threading

from .artifact_lock import zero_wait_artifact_lock
from .state_paths import (inspect_physical_directory, inspect_physical_file,
                          read_physical_file_bounded, path_lexically_exists)

MODES = {b"taskgov-logger-v1 debug\n": "debug", b"taskgov-logger-v1 errors\n": "errors"}
MAX_DAY_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_FILES = 30
_FILE = re.compile(r"taskgov-debug\.log\.(\d{4}-\d{2}-\d{2})\Z")
_LOCK = threading.Lock()


class DailySink:
    def __init__(self, root, directory, identity, marker):
        self.root, self.directory, self.identity, self.marker = root, directory, identity, marker
        self.mode = MODES[marker]
        self.stopped = False

    def _valid(self):
        from .project_scope import _state_is_ignored
        if (self.stopped or inspect_physical_directory(self.directory, root=self.root).identity != self.identity
                or read_physical_file_bounded(self.directory / "enabled", root=self.root, max_bytes=64)[0] != self.marker
                or not _state_is_ignored(self.root, "ordinary")):
            self.stopped = True
            return False
        return True

    def write(self, day, data):
        """One nonblocking attempt. Returns a closed outcome, never propagates."""
        if not _LOCK.acquire(blocking=False):
            return "contended"
        try:
            if not self._valid():
                return "disabled"
            date.fromisoformat(day)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                return "invalid"
            with zero_wait_artifact_lock(self.directory / "logger.lock", require_single_link=True):
                if not self._valid():
                    return "disabled"
                return self._append(day, data)
        except BaseException:
            return "unavailable"
        finally:
            _LOCK.release()

    def _append(self, day, data):
        total, files = 0, {}
        # Bound inventory work even if unrelated artifacts have accumulated.
        with os.scandir(self.directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 128:
                    return "capacity"
                match = _FILE.fullmatch(entry.name)
                if match is None:
                    continue
                date.fromisoformat(match[1])
                _, identity = inspect_physical_file(Path(entry.path), root=self.root, max_bytes=MAX_DAY_BYTES)
                files[entry.name] = identity
                total += identity.size
                if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
                    return "capacity"
        path = self.directory / ("taskgov-debug.log." + day)
        previous = files.get(path.name)
        size = previous.size if previous else 0
        if (previous is None and len(files) >= MAX_FILES or
                size + len(data) + 1 > MAX_DAY_BYTES or total + len(data) + 1 > MAX_TOTAL_BYTES):
            return "capacity"
        if path_lexically_exists(path):
            _, current = inspect_physical_file(path, root=self.root, max_bytes=MAX_DAY_BYTES)
            if current != previous:
                return "changed"
        flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if previous is None:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            details = os.fstat(descriptor)
            _, current = inspect_physical_file(path, root=self.root, max_bytes=MAX_DAY_BYTES)
            if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or
                    (details.st_dev, details.st_ino) != (current.device, current.inode) or
                    inspect_physical_directory(self.directory, root=self.root).identity != self.identity):
                return "changed"
            if details.st_size:
                os.lseek(descriptor, -1, os.SEEK_END)
                if os.read(descriptor, 1) != b"\n":
                    _write_all(descriptor, b"\n")
            _write_all(descriptor, data)
            # os.write is unbuffered; closing releases the file for other users.
            return "written"
        finally:
            os.close(descriptor)


def _write_all(descriptor, data):
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("diagnostic write failed")
        view = view[written:]


def admit(repo, entrypoint, *, repo_explicit):
    from .state_resolver import canonical_state_paths
    from .storage import skill_root_from_script
    from .project_scope import inspect_project_scope, _state_is_ignored
    paths = canonical_state_paths(skill_root_from_script(entrypoint), repo=repo)
    if not (paths.debug_logs / "enabled").exists():
        return None
    inspected = inspect_project_scope(repo=repo, repo_explicit=repo_explicit, script_path=entrypoint,
        include_runtime=False, include_package=False, include_ignore=False)
    if inspected.issues or inspected.scope is None:
        return None
    scope = inspected.scope
    paths = canonical_state_paths(scope.skill_root, repo=scope.canonical_repo)
    observed = inspect_physical_directory(paths.debug_logs, root=scope.canonical_repo)
    marker = read_physical_file_bounded(paths.debug_logs / "enabled", root=scope.canonical_repo, max_bytes=64)[0]
    if marker not in MODES or not _state_is_ignored(scope.canonical_repo, scope.layout):
        return None
    return DailySink(scope.canonical_repo, paths.debug_logs, observed.identity, marker)
