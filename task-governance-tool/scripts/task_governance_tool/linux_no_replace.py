"""Linux no-replace move; the shared entry owns validation and errors."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path


AT_FDCWD = -100
RENAME_NOREPLACE = 1


def rename_no_replace(source: Path, destination: Path) -> None:
    """Use renameat2 without an overwrite fallback when unavailable or denied."""

    try:
        move = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOSYS, "no-replace move unavailable") from exc
    move.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    move.restype = ctypes.c_int
    if move(
        AT_FDCWD, os.fsencode(source), AT_FDCWD, os.fsencode(destination),
        RENAME_NOREPLACE,
    ) != 0:
        raise OSError(ctypes.get_errno(), "no-replace move failed")
