"""Windows no-replace move; the shared entry owns validation and errors."""

from __future__ import annotations

import os
from pathlib import Path


def rename_no_replace(source: Path, destination: Path) -> None:
    """Use Windows rename semantics on paths admitted by the shared entry."""

    os.rename(source, destination)
