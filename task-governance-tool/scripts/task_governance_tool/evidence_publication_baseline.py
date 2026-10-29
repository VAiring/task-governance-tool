"""Closed, disposable publication baseline; no Bundle bodies or SQLite authority."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from task_governance_tool.evidence_projection import (
    IndexArtifact, build_index_artifact, canonical_json_bytes, _inconsistent,
)
from task_governance_tool.state_paths import (
    inspect_physical_file,
    EVIDENCE_BASELINE_FILENAME as BASELINE_FILENAME,
    EVIDENCE_BASELINE_MAX_BYTES as BASELINE_MAX_BYTES,
    EVIDENCE_BASELINE_TEMP_PREFIX as BASELINE_TEMP_PREFIX,
)
from task_governance_tool.storage import DatabaseTarget, SCHEMA_VERSION, read_project_binding_state
from task_governance_tool.evidence_projection_metadata_repository import EvidenceProjectionState


@dataclass(frozen=True)
class PublicationIdentity:
    project_id: str
    binding_generation: int
    canonical_path_hash: str
    schema_version: int
    database_device: int
    database_inode: int


def database_object(target: DatabaseTarget) -> tuple[int, int]:
    # lstat only: opening/closing another descriptor can release POSIX SQLite locks.
    _, identity = inspect_physical_file(target.db_path, root=target.db_path.parent)
    if identity.inode <= 0:
        raise _inconsistent()
    return identity.device, identity.inode


def publication_identity(
    connection: sqlite3.Connection, target: DatabaseTarget, physical: tuple[int, int],
) -> PublicationIdentity:
    binding = read_project_binding_state(connection)
    if database_object(target) != physical:
        raise _inconsistent()
    return PublicationIdentity(binding.project_id, binding.binding_generation,
        binding.canonical_path_hash, SCHEMA_VERSION, *physical)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _inconsistent()
        result[key] = value
    return result


def _invalid_number(_):
    raise _inconsistent()


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_float=_invalid_number, parse_constant=_invalid_number)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _inconsistent() from exc


def decode_index(raw: bytes) -> IndexArtifact:
    value = _decode(raw)
    if type(value) is not dict or set(value) != {"format_version", "index_digest", "payload"}:
        raise _inconsistent()
    artifact = build_index_artifact(value["payload"])
    if artifact.document != raw:
        raise _inconsistent()
    return artifact


def baseline_document(identity: PublicationIdentity, index_document: bytes) -> bytes:
    index = decode_index(index_document)
    if (index.payload["project_id"] != identity.project_id
            or index.payload["source_schema_version"] != identity.schema_version):
        raise _inconsistent()
    document = canonical_json_bytes(dict(format_version=1,
        identity=vars(identity), index=index.envelope)) + b"\n"
    if len(document) > BASELINE_MAX_BYTES:
        raise _inconsistent()
    return document


def admitted_baseline(
    raw: bytes, identity: PublicationIdentity, state: EvidenceProjectionState,
    current_index: bytes,
) -> IndexArtifact:
    """Use the saved acknowledged index, or recover an outcome committed before the baseline."""
    value = _decode(raw)
    if (type(value) is not dict or set(value) != {"format_version", "identity", "index"}
            or type(value["format_version"]) is not int or value["format_version"] != 1
            or type(value["identity"]) is not dict
            or canonical_json_bytes(value["identity"]) != canonical_json_bytes(vars(identity))):
        raise _inconsistent()
    saved = decode_index(canonical_json_bytes(value["index"]) + b"\n")
    if baseline_document(identity, saved.document) != raw:
        raise _inconsistent()
    if (state.published_generation is None
            or saved.payload["projection_generation"] > state.published_generation):
        raise _inconsistent()
    current = decode_index(current_index)
    if (current.payload["project_id"] != identity.project_id
            or current.payload["source_schema_version"] != identity.schema_version
            or not state.published_generation <= current.payload["projection_generation"] <= state.source_generation):
        raise _inconsistent()
    if (saved.index_digest == state.index_digest
            and saved.payload["projection_generation"] == state.published_generation):
        # An index ahead of the acknowledged state is not adopted. Its new IDs
        # are selected and semantically revalidated on the next publication.
        if (current.payload["projection_generation"] == state.published_generation
                and current.index_digest != state.index_digest):
            raise _inconsistent()
        return saved
    if (current.index_digest == state.index_digest
            and current.payload["projection_generation"] == state.published_generation):
        return current
    raise _inconsistent()
