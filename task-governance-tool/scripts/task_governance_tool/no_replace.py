"""OS-neutral no-replace publication for validated state entries.

Shared containment and identity rules remain in state_paths. Callers retain
publication and cleanup policy; this operation preserves their validated
source and rejects an occupied destination without replacement.
"""

from __future__ import annotations

import os
from pathlib import Path

from task_governance_tool.state_paths import (
    STATE_PATH_FAILURE_MESSAGE,
    StatePathError,
    ValidatedDirectory,
    ValidatedFile,
    _assert_parent_chain,
    _failure,
    _same_directory_identity,
    _same_file_identity,
    path_lexically_exists,
    require_contained,
)


def rename_no_replace(
    source: ValidatedFile | ValidatedDirectory,
    destination: Path,
    *,
    root: Path,
) -> ValidatedFile | ValidatedDirectory:
    """Move one validated sibling-tree entry without replacement."""

    require_contained(source.path, root)
    require_contained(destination, root)
    _assert_parent_chain(source.path, root)
    _assert_parent_chain(destination, root)
    if path_lexically_exists(destination):
        raise _failure()
    if isinstance(source, ValidatedFile):
        if not _same_file_identity(source.path, source.identity):
            raise _failure()
    elif not _same_directory_identity(source.path, source.identity):
        raise _failure()

    # The supported Windows runtime gives os.rename no-replace semantics.
    # POSIX os.rename may replace an existing empty directory or file, so an
    # unverified port must fail closed instead of emulating this with replace.
    if os.name != "nt":
        raise StatePathError(
            code="unsupported_no_replace",
            message=STATE_PATH_FAILURE_MESSAGE,
        )
    try:
        from task_governance_tool.windows_no_replace import rename_no_replace as move

        move(source.path, destination)
    except OSError as exc:
        raise _failure() from exc
    if path_lexically_exists(source.path):
        raise _failure()
    if isinstance(source, ValidatedFile):
        if not _same_file_identity(destination, source.identity):
            raise _failure()
        return ValidatedFile(destination, source.identity, source.sha256)
    if not _same_directory_identity(destination, source.identity):
        raise _failure()
    return ValidatedDirectory(destination, source.identity)
