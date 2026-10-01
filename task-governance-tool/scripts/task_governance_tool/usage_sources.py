"""Codex source hints and filename lookup for an explicit participant set.

No transcript header of an unrelated thread is opened. Filenames locate only
candidates; usage_adapter still validates the exact physical source and header.
Only the standard dated sessions layout and flat archive are supported here.
"""

import os
from pathlib import Path

from task_governance_tool.state_paths import inspect_physical_directory, StatePathError
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_values import UsageError


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


def locate_sources(threads, roots, project):
    """Return exact-ID candidates only; missing/unreadable roots are not complete."""
    sources = {}
    for root in roots:
        if not root.exists():
            continue
        try:
            for directory in _directories(root):
                for path in directory.iterdir():
                    # The suffix is a discovery hint, NOT an identity assertion.
                    if not path.name.startswith("rollout-") or not path.name.endswith(".jsonl"):
                        continue
                    thread = path.name[-42:-6]
                    if thread in threads:
                        source = SourceInput(thread, path, root, project)
                        sources[source.source_id] = source
        except (OSError, StatePathError):
            # The caller marks previously registered, undiscovered sources as
            # unavailable. No other directory/header is used as a fallback.
            continue
    return sources
