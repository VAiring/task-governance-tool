"""Codex source hints and filename lookup for an explicit participant set.

No transcript header of an unrelated thread is opened. Filenames locate only
candidates; usage_adapter still validates the exact physical source and header.
Only the standard dated sessions layout and flat archive are supported here.
"""

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path, PurePosixPath
from time import monotonic

from task_governance_tool.session_identity import is_session_id
from task_governance_tool.state_paths import (
    inspect_physical_directory, inspect_physical_file, path_lexically_exists, StatePathError,
)
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_values import UsageError


LOCATOR_ROOT_KINDS = ("sessions", "archive")
MAX_LOCATOR_BYTES = 4096


@dataclass(frozen=True)
class SourceLocator:
    """A retained location hint, never source admission or a validated cursor."""

    source_id: str
    thread_id: str
    root_kind: str
    root_fingerprint: str
    relative_path: str = field(repr=False)


def validate_locator(locator: SourceLocator) -> None:
    """Validate the closed persisted value without filesystem or database I/O."""
    if (type(locator) is not SourceLocator or type(locator.thread_id) is not str
            or not is_session_id(locator.thread_id)):
        raise UsageError("source_unreadable")
    if type(locator.root_kind) is not str or locator.root_kind not in LOCATOR_ROOT_KINDS:
        raise UsageError("source_unreadable")
    for digest in (locator.source_id, locator.root_fingerprint):
        if (type(digest) is not str or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise UsageError("source_unreadable")
    relative = locator.relative_path
    if (type(relative) is not str or not relative or any(char in relative for char in "\\:\0")
            or any(ord(char) < 32 for char in relative)):
        raise UsageError("source_unreadable")
    try:
        if len(relative.encode("utf-8")) > MAX_LOCATOR_BYTES:
            raise UsageError("source_unreadable")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != relative or not path.parts:
            raise UsageError("source_unreadable")
    except (ValueError, UnicodeError):
        raise UsageError("source_unreadable") from None


def _root_paths(roots):
    if len(roots) != len(LOCATOR_ROOT_KINDS):
        raise UsageError("source_unreadable")
    paths = {}
    for kind, value in zip(LOCATOR_ROOT_KINDS, roots):
        path = Path(value)
        if not path.is_absolute():
            raise UsageError("source_unreadable")
        paths[kind] = Path(os.path.normcase(os.path.abspath(path)))
    if len(set(paths.values())) != len(paths):
        raise UsageError("source_unreadable")
    return paths


def _root_fingerprint(root):
    # A lexical environment binding, not proof of the current root's identity.
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def locator_root_fingerprints(roots):
    """Current environment partitions for bounded stored-candidate selection."""
    return {kind: _root_fingerprint(path) for kind, path in _root_paths(roots).items()}


def _inspect_locator_path(path, root):
    """Reject existing unsafe entries, allowing an as-yet missing file/parent."""
    if path_lexically_exists(path):
        inspect_physical_file(path, root=root)
        return
    parent = path.parent
    while not path_lexically_exists(parent):
        if parent == root:
            return
        parent = parent.parent
    inspect_physical_directory(parent, root=root)


def locator_from_source(source: SourceInput, roots) -> SourceLocator | None:
    """Capture a bounded physical location without opening its header or body."""
    try:
        paths = _root_paths(roots)
        allowed = Path(os.path.normcase(os.path.abspath(source.allowed_root)))
        path = Path(os.path.normcase(os.path.abspath(source.path)))
        for kind, root in paths.items():
            if allowed != root or not path.is_relative_to(root):
                continue
            locator = SourceLocator(source.source_id, source.thread_id, kind,
                                    _root_fingerprint(root), path.relative_to(root).as_posix())
            validate_locator(locator)
            _inspect_locator_path(path, root)
            return locator
    except (OSError, StatePathError, UsageError, TypeError, ValueError, UnicodeError):
        pass
    return None


def source_from_locator(locator: SourceLocator, roots, project) -> SourceInput:
    """Rebuild one current-root hint; collection must still validate its header."""
    try:
        validate_locator(locator)
        root = _root_paths(roots)[locator.root_kind]
        if locator.root_fingerprint != _root_fingerprint(root):
            raise UsageError("source_unreadable")
        path = root.joinpath(*PurePosixPath(locator.relative_path).parts)
        source = SourceInput(locator.thread_id, path, root, project)
        if source.source_id != locator.source_id:
            raise UsageError("source_unreadable")
        _inspect_locator_path(path, root)
        return source
    except (OSError, StatePathError, TypeError, ValueError, UnicodeError):
        raise UsageError("source_unreadable") from None


def archive_locator(locator: SourceLocator, roots, project) -> SourceLocator | None:
    """Derive only a recognized session basename in the current flat archive."""
    try:
        source = source_from_locator(locator, roots, project)
        if locator.root_kind != "sessions" or _filename_thread(source.path.name) != locator.thread_id:
            return None
        root = _root_paths(roots)["archive"]
        return locator_from_source(SourceInput(locator.thread_id, root / source.path.name, root, project), roots)
    except UsageError:
        return None


def source_roots(environment):
    value = environment.get("CODEX_HOME")
    base = Path(value) if value else Path.home() / ".codex"
    if not base.is_absolute():
        raise UsageError("source_unreadable")
    return (base / "sessions", base / "archived_sessions")


def source_hint(thread, value, roots, project):
    if not isinstance(value, str) or not Path(value).is_absolute():
        return None
    path = Path(os.path.abspath(value))
    for root in roots:
        if path.is_relative_to(Path(os.path.abspath(root))):
            return SourceInput(thread, path, root, project)
    return None


def _directories(root):
    """Walk directory names, not log headers, and never follow links/junctions."""
    inspect_physical_directory(root)
    yield root
    if root.name == "archived_sessions":
        return
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth == 3:
            continue
        for child in directory.iterdir():
            width = 4 if depth == 0 else 2
            if len(child.name) != width or not child.name.isascii() or not child.name.isdecimal():
                continue
            try:
                inspect_physical_directory(child, root=root)
            except (OSError, StatePathError):
                continue
            yield child
            pending.append((child, depth + 1))


def _filename_thread(name):
    """Read the session suffix, optionally followed by _<segment-uuid>.

    Parse the two-ID form first, regardless of registration: a registered
    segment ID must not select another session's file. This is only a hint.
    """
    if not name.startswith("rollout-") or not name.endswith(".jsonl"):
        return None
    stem = name[:-6]
    if stem[-37:-36] == "_":
        if not is_session_id(stem[-36:]):
            return None
        stem = stem[:-37]
    thread = stem[-36:]
    return thread if stem[-37:-36] == "-" and is_session_id(thread) else None


def locate_sources(threads, roots, project):
    """Return exact-ID candidates only; missing/unreadable roots are not complete."""
    sources = {}
    for root in roots:
        if not root.exists():
            continue
        try:
            for directory in _directories(root):
                for path in directory.iterdir():
                    thread = _filename_thread(path.name)
                    if thread in threads:
                        source = SourceInput(thread, path, root, project)
                        sources[source.source_id] = source
        except (OSError, StatePathError):
            # The caller marks previously registered, undiscovered sources as
            # unavailable. No other directory/header is used as a fallback.
            continue
    return sources


def locate_slice(threads, roots, project, position, *, deadline, max_entries=1024, max_sources=15):
    """Resumable physical-directory inventory, retaining no transcript filenames.

    Only root ordinal, numeric date directories and entry ordinals persist.
    Directory enumeration is re-opened on each event; concurrent renames can
    move an entry to the next inventory cycle, never establish completeness.
    Skipping an old ordinal still costs filename I/O, not transcript parsing.
    """
    from task_governance_tool.debug_log import aggregate
    with aggregate(locate_slice, unit='items') as measurement:
        measurement.count(0)
        import copy
        position = copy.deepcopy(position)
        sources, entries = {}, 0
        while position["root"] < len(roots):
            if monotonic() >= deadline or entries >= max_entries or len(sources) >= max_sources:
                measurement.mark('limit' if entries >= max_entries or len(sources) >= max_sources else 'deadline')
                break
            root = roots[position["root"]]
            if not position["stack"]:
                position["stack"] = [[[], 0]]
            parts, ordinal = position["stack"][-1]
            directory = root.joinpath(*parts)
            descended = False
            exhausted = True
            try:
                inspect_physical_directory(directory, root=root)
                with os.scandir(directory) as iterator:
                    for index, entry in enumerate(iterator):
                        measurement.count(1)  # Includes entries skipped while resuming an ordinal.
                        if monotonic() >= deadline:
                            measurement.mark('deadline')
                            exhausted = False
                            break
                        if index < ordinal:
                            continue
                        if entries >= max_entries or len(sources) >= max_sources:
                            measurement.mark('limit')
                            exhausted = False
                            break
                        entries += 1
                        position["stack"][-1][1] = index + 1
                        thread = _filename_thread(entry.name)
                        if thread in threads:
                            source = SourceInput(thread, Path(entry.path), root, project)
                            sources[source.source_id] = source
                        width = 4 if len(parts) == 0 else 2
                        if (root.name != "archived_sessions" and len(parts) < 3 and len(entry.name) == width
                                and entry.name.isascii() and entry.name.isdecimal()):
                            try:
                                inspect_physical_directory(Path(entry.path), root=root)
                            except (OSError, StatePathError):
                                continue
                            position["stack"].append([[*parts, entry.name], 0])
                            descended = True
                            break
            except (OSError, StatePathError):
                pass
            if descended:
                continue
            if not exhausted:
                break
            position["stack"].pop()
            if not position["stack"]:
                position["root"] += 1
        complete = position["root"] == len(roots)
        return sources, position, complete
