"""Bounded Viewer display cache codec and physical artifact publication.

This file contains no SQL or source-evidence admission. The coordinator owns
the Viewer lock and supplies a fresh database guard for each atomic replacement.
"""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets

from .state_paths import (
    StatePathError, ValidatedFile, create_exclusive_durable_file,
    inspect_physical_file, path_lexically_exists, read_physical_file_bounded,
    unlink_validated_file,
)
from .viewer import (
    ViewerError, ViewerOutputTarget, validate_default_output_parent,
    validate_output_database_separation,
)

CACHE_FORMAT_VERSION = 1
PROJECTION_VERSION = 1
MAX_BYTES = 64 * 1024 * 1024
HEADER_KEYS = frozenset({
    "cache_format_version", "projection_version", "project_id",
    "binding_generation", "binding_path_hash", "database_epoch",
    "database_file_identity", "source_schema_version", "applied_generation",
})


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _invalid():
    return ViewerError("internal_error", "Viewer projection cache is invalid")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def physical_identity(path: Path, *, root: Path) -> tuple[int, int]:
    """Pin a regular file object, never infer identity from time or size."""
    _, identity = inspect_physical_file(path, root=root)
    return identity.device, identity.inode


def artifact_present(output: ViewerOutputTarget) -> bool:
    validate_default_output_parent(output.path, output.state_root)
    validate_output_database_separation(output.path, output.database_path)
    if not path_lexically_exists(output.path):
        return False
    inspect_physical_file(output.path, root=output.state_root)
    if output.path.lstat().st_nlink != 1:
        raise _invalid()
    return True


def cache_output(target) -> ViewerOutputTarget:
    return ViewerOutputTarget(target.resolved_viewer_projection_path,
                              target.db_path, target.db_path.parent)


@dataclass(frozen=True)
class DisplayCache:
    payload: dict
    data: bytes
    sha256: str

    @property
    def tasks(self):
        return self.payload["tasks"]

    @property
    def header(self):
        return {key: self.payload[key] for key in HEADER_KEYS}


def _validate_header(payload):
    import re
    if type(payload) is not dict or set(payload) != HEADER_KEYS | {"tasks"}:
        raise _invalid()
    for key in ("cache_format_version", "projection_version", "binding_generation",
                "source_schema_version", "applied_generation"):
        minimum = 0 if key == "applied_generation" else 1
        if type(payload[key]) is not int or not minimum <= payload[key] < 2**63:
            raise _invalid()
    if (payload["cache_format_version"] != CACHE_FORMAT_VERSION
            or payload["projection_version"] != PROJECTION_VERSION
            or payload["source_schema_version"] != 34):
        raise _invalid()
    if type(payload["project_id"]) is not str or not payload["project_id"]:
        raise _invalid()
    for key, size in (("database_epoch", 32), ("binding_path_hash", 64)):
        if type(payload[key]) is not str or re.fullmatch("[0-9a-f]{%d}" % size, payload[key]) is None:
            raise _invalid()
    identity = payload["database_file_identity"]
    if type(identity) is not dict or set(identity) != {"device", "inode"}:
        raise _invalid()
    if any(type(value) is not int or value < 0 for value in identity.values()):
        raise _invalid()


def encode_cache(header: dict, tasks: list[dict], *, validation=None) -> DisplayCache:
    from .viewer_display_validation import validate_display_tasks
    payload = {**header, "tasks": tasks}
    _validate_header(payload)
    validate_display_tasks(tasks, project_id=header["project_id"], validation=validation)
    data = canonical_bytes(payload)
    if not data or len(data) > MAX_BYTES:
        raise _invalid()
    return DisplayCache(payload, data, hashlib.sha256(data).hexdigest())


def load_candidate(output: ViewerOutputTarget, *, validation=None) -> DisplayCache | None:
    """Invalid bytes rebuild; unsafe physical targets always fail closed."""
    if not artifact_present(output):
        return None
    if output.path.stat().st_size > MAX_BYTES:
        return None
    try:
        data, observed = read_physical_file_bounded(
            output.path, root=output.state_root, max_bytes=MAX_BYTES)
        # Physical read errors are not invalid JSON: do not repair an unsafe path.
    except StatePathError:
        raise
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        _validate_header(payload)
        from .viewer_display_validation import validate_display_tasks
        validate_display_tasks(payload["tasks"], project_id=payload["project_id"], validation=validation)
        if canonical_bytes(payload) != data:
            return None
    except (ValueError, TypeError, UnicodeError, RecursionError, ViewerError):
        return None
    return DisplayCache(payload, data, observed.sha256)


def matches(candidate: DisplayCache | None, *, header: dict, state) -> bool:
    if candidate is None or state.cache_generation is None:
        return False
    expected = {**header, "applied_generation": state.cache_generation}
    return (candidate.header == expected
            and state.cache_generation >= state.invalidated_generation
            and state.cache_format_version == CACHE_FORMAT_VERSION
            and state.projection_version == PROJECTION_VERSION
            and candidate.sha256 == state.cache_sha256
            and len(candidate.data) == state.cache_bytes)


def checked_artifact(output: ViewerOutputTarget, data: bytes) -> ValidatedFile:
    if not artifact_present(output):
        raise _invalid()
    actual, observed = read_physical_file_bounded(
        output.path, root=output.state_root, max_bytes=MAX_BYTES)
    if actual != data:
        raise _invalid()
    return observed


def require_same_artifact(output: ViewerOutputTarget, observed: ValidatedFile) -> None:
    if not artifact_present(output):
        raise _invalid()
    _, current = inspect_physical_file(output.path, root=output.state_root)
    if current != observed.identity:
        raise _invalid()


def replace_artifact(output: ViewerOutputTarget, data: bytes, *, guard,
                     cache: bool) -> ValidatedFile:
    """Flush outside SQLite; guard covers replacement only; readback outside it."""
    if not data or len(data) > MAX_BYTES:
        raise _invalid()
    artifact_present(output)
    prefix = ".task-projection-" if cache else ".task-viewer-"
    temporary = output.path.with_name(prefix + secrets.token_hex(4) + ".tmp")
    prepared = create_exclusive_durable_file(
        temporary, data, root=output.state_root, max_bytes=MAX_BYTES)
    temporary_output = ViewerOutputTarget(temporary, output.database_path, output.state_root)
    try:
        with guard():
            artifact_present(output)
            require_same_artifact(temporary_output, prepared)
            os.replace(temporary, output.path)
        return checked_artifact(output, data)
    finally:
        # Only our still-identical temporary may be removed. Never follow a link
        # or delete an entry installed by another writer after a failed guard.
        with suppress(OSError, StatePathError, ViewerError):
            require_same_artifact(temporary_output, prepared)
            unlink_validated_file(prepared, root=output.state_root)
