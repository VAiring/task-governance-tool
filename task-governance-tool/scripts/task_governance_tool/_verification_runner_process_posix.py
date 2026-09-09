"""POSIX credential-excluding environment preparation, without process launch."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from task_governance_tool.state_paths import StatePathError, inspect_physical_directory
from task_governance_tool.verification_runner_process import (
    RunnerProcessError,
    _valid_absolute_path,
)


def prepare_clean_environment(scratch_root: Path) -> tuple[tuple[str, str], ...]:
    """Map only the existing private home/temp directories to a closed tuple."""

    if os.name != "posix" or sys.platform not in {"linux", "darwin"}:
        raise RunnerProcessError("runtime_unavailable")
    if (
        not isinstance(scratch_root, Path)
        or not _valid_absolute_path(scratch_root)
        or scratch_root.name != "scratch"
    ):
        raise RunnerProcessError()
    try:
        inspect_physical_directory(scratch_root, root=Path(scratch_root.anchor))
        for name in ("home", "tmp"):
            directory = scratch_root / name
            if not _valid_absolute_path(directory):
                raise RunnerProcessError()
            inspect_physical_directory(directory, root=scratch_root)
    except StatePathError as exc:
        raise RunnerProcessError("process_boundary_unproved") from exc
    return (
        ("HOME", str(scratch_root / "home")),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PYTHONNOUSERSITE", "1"),
        ("PYTHONUTF8", "1"),
        ("TEMP", str(scratch_root / "tmp")),
        ("TMP", str(scratch_root / "tmp")),
        ("TMPDIR", str(scratch_root / "tmp")),
    )


__all__ = ["prepare_clean_environment"]
