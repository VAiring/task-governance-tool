"""Closed metadata and physical publication for the one state-layout cutover.

This module neither selects a database nor validates business rows.  The resolver
reads its observation; explicit setup owns offline quiescence, domain inventories,
locks, candidate validation and the decision to publish.  No active database is
compared with a pre-cutover content hash here.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from task_governance_tool.artifact_lock import (
    ArtifactLockError,
    inspect_existing_artifact_lock,
)
from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.project_binding_repository import validate_identity_project_id
from task_governance_tool.state_paths import (
    StatePathError,
    ValidatedFile,
    create_exclusive_durable_file,
    create_physical_directory_exclusive,
    hash_physical_file,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    read_physical_file_bounded,
)
from task_governance_tool.storage import SCHEMA_VERSION, StorageError


RECORD_MAX_BYTES = 4096
MARKER_MAX_BYTES = 512
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PHASES = ("private", "fenced", "activated")


@dataclass
class SeparationError(Exception):
    code: str = "project_state_unreadable"
    message: str = "project state could not be read safely"

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class SeparationPaths:
    root: Path
    record: Path
    next_record: Path
    source: Path
    candidate: Path


@dataclass(frozen=True)
class SeparationRecord:
    v: int
    transition_id: str
    phase: Literal["private", "fenced", "activated"]
    project_id: str
    source_layout: str | None = None
    source_schema_version: int | None = None
    source_binding_generation: int | None = None
    source_path_hash: str | None = None
    source_fingerprint: str | None = None
    retained_digest: str | None = None
    candidate_digest: str | None = None


@dataclass(frozen=True)
class SeparationObservation:
    state: Literal["absent", "pending", "activated", "repair_barrier"]
    record: SeparationRecord | None
    marker_matches: bool


def separation_paths(state_root: Path) -> SeparationPaths:
    root = Path(state_root) / ".state-separation"
    return SeparationPaths(
        root, root / "record.json", root / ".record-next.json",
        root / "source", root / "candidate",
    )


def _project_id(value: str) -> None:
    for scheme in ("uuid_v1", "legacy_path_v1"):
        try:
            validate_identity_project_id(value, scheme)
            return
        except StorageError:
            pass
    raise SeparationError()


def _digest(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def encode_separation_record(record: SeparationRecord) -> bytes:
    if (
        not isinstance(record, SeparationRecord)
        or type(record.v) is not int or record.v != 1
        or not isinstance(record.transition_id, str)
        or _HEX32.fullmatch(record.transition_id) is None
        or record.phase not in _PHASES
    ):
        raise SeparationError()
    _project_id(record.project_id)
    source_values = (
        record.source_schema_version, record.source_binding_generation,
        record.source_path_hash, record.source_fingerprint, record.retained_digest,
    )
    if record.source_layout is None:
        if any(value is not None for value in source_values):
            raise SeparationError()
    else:
        if (
            not isinstance(record.source_layout, str)
            or record.source_layout not in {"fixed_current_v1", "legacy_projects_v1"}
            or type(record.source_schema_version) is not int
            or not 1 <= record.source_schema_version <= SCHEMA_VERSION
            or type(record.source_binding_generation) is not int
            or not 0 <= record.source_binding_generation <= 2**63 - 1
            or not _digest(record.source_path_hash)
            or not _digest(record.source_fingerprint)
            or (record.source_schema_version >= 14
                and record.source_binding_generation == 0)
            or (record.source_schema_version < 14
                and record.source_binding_generation != 0)
            or (record.source_layout == "legacy_projects_v1"
                and record.source_schema_version > 14)
        ):
            raise SeparationError()
    for value in (record.retained_digest, record.candidate_digest):
        if value is not None and not _digest(value):
            raise SeparationError()
    if record.phase != "private" and (
        record.candidate_digest is None
        or (record.source_layout is not None and record.retained_digest is None)
    ):
        raise SeparationError()
    data = _canonical(asdict(record))
    if len(data) > RECORD_MAX_BYTES:
        raise SeparationError()
    return data


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SeparationError()
        result[key] = value
    return result


def decode_separation_record(data: bytes) -> SeparationRecord:
    if not isinstance(data, bytes) or not 1 <= len(data) <= RECORD_MAX_BYTES:
        raise SeparationError()
    try:
        payload = json.loads(data.decode("ascii"), object_pairs_hook=_unique)
        if not isinstance(payload, dict) or set(payload) != set(
            SeparationRecord.__dataclass_fields__
        ):
            raise SeparationError()
        record = SeparationRecord(**payload)
        if encode_separation_record(record) != data:
            raise SeparationError()
        return record
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SeparationError() from exc


def retirement_marker_bytes(record: SeparationRecord) -> bytes:
    encode_separation_record(record)
    return _canonical({
        "kind": "taskgov-retired-state", "v": 1,
        "transition_id": record.transition_id, "project_id": record.project_id,
    })


def read_record(state_root: Path) -> SeparationRecord:
    try:
        data, _ = read_physical_file_bounded(
            separation_paths(state_root).record, root=state_root,
            max_bytes=RECORD_MAX_BYTES,
        )
        return decode_separation_record(data)
    except (OSError, StatePathError) as exc:
        raise SeparationError() from exc


def _require_successor(old: SeparationRecord, new: SeparationRecord) -> None:
    encode_separation_record(old)
    encode_separation_record(new)
    mutable = {"phase", "retained_digest", "candidate_digest"}
    if any(asdict(old)[key] != value for key, value in asdict(new).items()
           if key not in mutable):
        raise SeparationError()
    old_phase, new_phase = _PHASES.index(old.phase), _PHASES.index(new.phase)
    if not old_phase <= new_phase <= old_phase + 1:
        raise SeparationError()
    for field in ("retained_digest", "candidate_digest"):
        before, after = getattr(old, field), getattr(new, field)
        if before is not None and before != after:
            raise SeparationError()


def inspect_separation(
    *, state_root: Path, old_database: Path,
) -> SeparationObservation:
    """Read only closed transition metadata; domain contents belong to callers."""
    try:
        if not path_lexically_exists(state_root):
            return SeparationObservation("absent", None, False)
        inspect_physical_directory(state_root)
        names = set()
        for entry in state_root.iterdir():
            if entry.name not in {"taskgov-state.lock", "current", ".state-separation"}:
                raise SeparationError()
            names.add(entry.name)
            if entry.name == "taskgov-state.lock":
                inspect_physical_file(entry, root=state_root, max_bytes=1)
                inspect_existing_artifact_lock(entry)
            else:
                inspect_physical_directory(entry, root=state_root)
        paths = separation_paths(state_root)
        if ".state-separation" not in names:
            if "current" in names:
                raise SeparationError()
            return SeparationObservation("absent", None, False)
        record = read_record(state_root)
        children = set()
        for entry in paths.root.iterdir():
            if entry.name not in {"record.json", ".record-next.json", "source", "candidate"}:
                raise SeparationError()
            children.add(entry.name)
            if entry.name in {"source", "candidate"}:
                inspect_physical_directory(entry, root=state_root)
        if ".record-next.json" in children:
            data, _ = read_physical_file_bounded(
                paths.next_record, root=state_root, max_bytes=RECORD_MAX_BYTES,
            )
            _require_successor(record, decode_separation_record(data))
        if "candidate" in children and "current" in names:
            raise SeparationError()
        if record.source_layout is None and "source" in children:
            raise SeparationError()
        if record.retained_digest is not None and "source" not in children:
            raise SeparationError()
        if record.phase != "private" and not (
            "candidate" in children or "current" in names
        ):
            raise SeparationError()
        marker_matches = False
        old_exists = path_lexically_exists(old_database)
        if old_exists:
            _, identity = inspect_physical_file(old_database, root=old_database.parent.parent)
            if identity.size <= MARKER_MAX_BYTES:
                data, _ = read_physical_file_bounded(
                    old_database, root=old_database.parent.parent,
                    max_bytes=MARKER_MAX_BYTES,
                )
                marker_matches = data == retirement_marker_bytes(record)
            if not marker_matches and (
                record.phase != "private" or record.source_layout is None
            ):
                raise SeparationError()
        if record.phase == "activated":
            if "candidate" in children:
                raise SeparationError()
            # Missing/corrupt active DB is left to normal new-root recovery.
            return SeparationObservation(
                "activated" if marker_matches else "repair_barrier",
                record, marker_matches,
            )
        if record.phase == "fenced" and not marker_matches:
            raise SeparationError()
        if "current" in names and not marker_matches:
            raise SeparationError()
        return SeparationObservation("pending", record, marker_matches)
    except (OSError, StatePathError, ArtifactLockError) as exc:
        raise SeparationError() from exc


def _replace_exact(
    temporary: ValidatedFile, destination: ValidatedFile, *, root: Path,
) -> ValidatedFile:
    """The sole replacing operation: both entries already belong to this cutover."""
    if hash_physical_file(temporary.path, root=root) != temporary:
        raise SeparationError()
    if hash_physical_file(destination.path, root=root) != destination:
        raise SeparationError()
    os.replace(temporary.path, destination.path)
    published = hash_physical_file(destination.path, root=root)
    if (published.identity, published.sha256) != (temporary.identity, temporary.sha256):
        raise SeparationError()
    return published


def publish_record(
    state_root: Path, record: SeparationRecord, *, expected: SeparationRecord | None,
) -> None:
    """Publish one record under setup's transition lock; preserve uncertain residue."""
    try:
        data = encode_separation_record(record)
        paths = separation_paths(state_root)
        inspect_physical_directory(state_root)
        if expected is None:
            if record.phase != "private":
                raise SeparationError()
            if not path_lexically_exists(paths.root):
                create_physical_directory_exclusive(paths.root, root=state_root)
            else:
                inspect_physical_directory(paths.root, root=state_root)
                if any(paths.root.iterdir()):
                    raise SeparationError()
            create_exclusive_durable_file(paths.record, data, root=state_root, max_bytes=RECORD_MAX_BYTES)
            return
        _require_successor(expected, record)
        previous, observed = read_physical_file_bounded(
            paths.record, root=state_root, max_bytes=RECORD_MAX_BYTES,
        )
        if previous != encode_separation_record(expected):
            raise SeparationError()
        if path_lexically_exists(paths.next_record):
            prepared, temporary = read_physical_file_bounded(
                paths.next_record, root=state_root, max_bytes=RECORD_MAX_BYTES,
            )
            if prepared != data:
                raise SeparationError()
        else:
            temporary = create_exclusive_durable_file(
                paths.next_record, data, root=state_root, max_bytes=RECORD_MAX_BYTES,
            )
        _replace_exact(temporary, observed, root=state_root)
    except (OSError, StatePathError) as exc:
        raise SeparationError() from exc


def publish_retirement_marker(
    old_database: Path, record: SeparationRecord, *, expected: ValidatedFile | None,
) -> None:
    """Fence only the validated old primary; source retention is setup's duty."""
    try:
        # Incomplete preparation cannot retire the source even in write setup.
        encode_separation_record(record)
        if record.candidate_digest is None or (
            record.source_layout is not None and record.retained_digest is None
        ):
            raise SeparationError()
        old_root = old_database.parent.parent
        inspect_physical_directory(old_root)
        inspect_physical_directory(old_database.parent, root=old_root)
        data = retirement_marker_bytes(record)
        temporary_path = old_root / f".taskgov-retirement-{record.transition_id}.tmp"
        if expected is not None and expected.path != old_database:
            raise SeparationError()
        if path_lexically_exists(temporary_path):
            existing, temporary = read_physical_file_bounded(
                temporary_path, root=old_root, max_bytes=MARKER_MAX_BYTES,
            )
            if existing != data:
                raise SeparationError()
        else:
            temporary = create_exclusive_durable_file(
                temporary_path, data, root=old_root, max_bytes=MARKER_MAX_BYTES,
            )
        if expected is None:
            rename_no_replace(temporary, old_database, root=old_root)
        else:
            _replace_exact(temporary, expected, root=old_root)
    except (OSError, StatePathError) as exc:
        raise SeparationError() from exc
