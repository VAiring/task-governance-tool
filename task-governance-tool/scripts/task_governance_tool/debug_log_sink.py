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
MAX_DAY_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 300 * 1024 * 1024
MAX_FILES = 3
_FILE = re.compile(r"taskgov-debug\.log\.([0-9]{4}-[0-9]{2}-[0-9]{2})\Z")
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

    def _inspect_log(self, path):
        _, identity = inspect_physical_file(path, root=self.root)
        details = path.lstat()
        if (details.st_nlink != 1 or
                (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns) !=
                (identity.device, identity.inode, identity.size, identity.modified_ns)):
            raise OSError("diagnostic file changed")
        return identity

    def _append(self, day, data):
        files = {}
        # Bound inventory work even if unrelated artifacts have accumulated.
        with os.scandir(self.directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 128:
                    return "capacity"
                match = _FILE.fullmatch(entry.name)
                if match is None:
                    continue
                try:
                    date.fromisoformat(match[1])
                except ValueError:
                    continue
                files[match[1]] = self._inspect_log(Path(entry.path))
        # Existing newer dates also anchor retention when the clock rolls back.
        retained = set(sorted({*files, date.today().isoformat(), day}, reverse=True)[:MAX_FILES])
        for expired in sorted(files.keys() - retained):
            path = self.directory / ("taskgov-debug.log." + expired)
            if (self._inspect_log(path) != files[expired] or
                    inspect_physical_directory(self.directory, root=self.root).identity != self.identity):
                return "changed"
            path.unlink()
            del files[expired]
        if day not in retained:
            return "capacity"
        total = sum(identity.size for identity in files.values())
        path = self.directory / ("taskgov-debug.log." + day)
        previous = files.get(day)
        size = previous.size if previous else 0
        if (any(identity.size > MAX_DAY_BYTES for identity in files.values()) or
                size + len(data) > MAX_DAY_BYTES or total + len(data) > MAX_TOTAL_BYTES):
            return "capacity"
        if path_lexically_exists(path):
            if self._inspect_log(path) != previous:
                return "changed"
        flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if previous is None:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            details = os.fstat(descriptor)
            current = self._inspect_log(path)
            if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or
                    (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns) !=
                    (current.device, current.inode, current.size, current.modified_ns) or
                    (current != previous if previous else current.size != 0) or
                    inspect_physical_directory(self.directory, root=self.root).identity != self.identity):
                return "changed"
            separator = b""
            if details.st_size:
                os.lseek(descriptor, -1, os.SEEK_END)
                if os.read(descriptor, 1) != b"\n":
                    separator = b"\n"
            added = len(separator) + len(data)
            if details.st_size + added > MAX_DAY_BYTES or total + added > MAX_TOTAL_BYTES:
                return "capacity"
            if separator:
                _write_all(descriptor, separator)
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
