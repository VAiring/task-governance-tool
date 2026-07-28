"""Strict optional presentation policy for the generated Task Viewer."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any


VIEWER_CONFIG_RELATIVE_PATH = Path("config") / "viewer.json"
VIEWER_CONFIG_SCHEMA_VERSION = 1
VIEWER_CONFIG_PROFILE = "visibility-refresh-v1"
VIEWER_CONFIG_MAX_BYTES = 16 * 1024
VIEWER_REFRESH_DISABLED_SECONDS = 0
VIEWER_REFRESH_MIN_SECONDS = 5
VIEWER_REFRESH_MAX_SECONDS = 3600
_EXPECTED_KEYS = {
    "schema_version",
    "profile",
    "refresh_interval_seconds",
}
_MAX_JSON_INTEGER_LENGTH = 20


class ViewerConfigError(Exception):
    """One fixed internal failure boundary with no path or input disclosure."""

    def __init__(self) -> None:
        super().__init__("viewer refresh profile is invalid")


class _DuplicateJsonKey(ValueError):
    pass


def _same_observation(first: os.stat_result, second: os.stat_result) -> bool:
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


def _is_reparse(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _require_physical_directory(path: Path) -> os.stat_result:
    details = path.lstat()
    if _is_reparse(details) or not stat.S_ISDIR(details.st_mode):
        raise ViewerConfigError()
    return details


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey()
        result[key] = value
    return result


def _bounded_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > _MAX_JSON_INTEGER_LENGTH:
        raise ValueError("integer token is too long")
    return int(value)


def _reject_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _read_stable_file(
    path: Path,
    *,
    skill_root_before: os.stat_result,
    config_root_before: os.stat_result,
) -> bytes:
    before = path.lstat()
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode):
        raise ViewerConfigError()

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            _is_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or not _same_observation(before, opened)
            or opened.st_size > VIEWER_CONFIG_MAX_BYTES
        ):
            raise ViewerConfigError()

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(4096, VIEWER_CONFIG_MAX_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > VIEWER_CONFIG_MAX_BYTES:
                raise ViewerConfigError()
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    after_path = path.lstat()
    config_root_after = path.parent.lstat()
    skill_root_after = path.parent.parent.lstat()
    if (
        not _same_observation(opened, after_open)
        or not _same_observation(before, after_path)
        or not _same_observation(config_root_before, config_root_after)
        or not _same_observation(skill_root_before, skill_root_after)
    ):
        raise ViewerConfigError()
    return b"".join(chunks)


def load_viewer_refresh_interval(skill_root: Path | None = None) -> int:
    """Return 0 when absent, or one validated refresh interval when present."""

    root = (
        Path(skill_root)
        if skill_root is not None
        else Path(__file__).resolve().parents[2]
    )
    config_root = root / VIEWER_CONFIG_RELATIVE_PATH.parent
    config_path = root / VIEWER_CONFIG_RELATIVE_PATH
    try:
        skill_root_before = _require_physical_directory(root)
        try:
            config_root_before = _require_physical_directory(config_root)
        except FileNotFoundError:
            return VIEWER_REFRESH_DISABLED_SECONDS
        try:
            raw = _read_stable_file(
                config_path,
                skill_root_before=skill_root_before,
                config_root_before=config_root_before,
            )
        except FileNotFoundError:
            config_root_after = config_root.lstat()
            skill_root_after = root.lstat()
            if (
                not _same_observation(config_root_before, config_root_after)
                or not _same_observation(skill_root_before, skill_root_after)
            ):
                raise ViewerConfigError()
            return VIEWER_REFRESH_DISABLED_SECONDS
    except ViewerConfigError:
        raise
    except (OSError, RuntimeError):
        raise ViewerConfigError() from None

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_int=_bounded_integer,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        ValueError,
        RecursionError,
    ):
        raise ViewerConfigError() from None

    if not isinstance(payload, dict) or set(payload) != _EXPECTED_KEYS:
        raise ViewerConfigError()
    schema_version = payload["schema_version"]
    interval = payload["refresh_interval_seconds"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != VIEWER_CONFIG_SCHEMA_VERSION
        or payload["profile"] != VIEWER_CONFIG_PROFILE
        or isinstance(interval, bool)
        or not isinstance(interval, int)
        or not VIEWER_REFRESH_MIN_SECONDS
        <= interval
        <= VIEWER_REFRESH_MAX_SECONDS
    ):
        raise ViewerConfigError()
    return interval
