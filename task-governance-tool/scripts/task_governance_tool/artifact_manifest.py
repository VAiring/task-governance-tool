"""Deterministic schema-v18 artifact manifests and read-only Git observation."""

from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from task_governance_tool.completion import (
    CompletionEvidenceError,
    resolve_git_commit,
    safe_git_command,
    safe_git_environment,
)
from task_governance_tool.evidence_ledger import (
    EVIDENCE_LEDGER_ERROR_CODE,
    EVIDENCE_LEDGER_ERROR_MESSAGE,
    EvidenceLedgerError,
    TargetCaptureBinding,
    canonical_json_bytes,
)
from task_governance_tool.git_snapshot import (
    RAW_DIFF_HEADER,
    GitIndexObservation,
    GitSnapshotEntry,
    GitSnapshotError,
    GitStreamIdentity,
    GitTreeObservation,
    parse_commit_tree_and_parents,
    run_git_bytes,
    run_git_stream,
    split_nul_records,
    stream_index_fingerprint,
    stream_tree_entries,
)


ARTIFACT_MANIFEST_DOMAIN = b"taskgov-artifact-manifest-v1\0"
ARTIFACT_ENTRY_LIMIT = 10_000
ARTIFACT_MANIFEST_BYTE_LIMIT = 16_777_216
ARTIFACT_PATH_BYTE_LIMIT = 240
ARTIFACT_CONTENT_NOT_OBSERVED = "artifact_content_not_observed"

PATH_UNSAFE_CODE = "artifact_manifest_path_unsafe"
PATH_UNSAFE_MESSAGE = "artifact manifest contains an unsafe project path"
TOO_LARGE_CODE = "artifact_manifest_too_large"
TOO_LARGE_MESSAGE = "artifact manifest exceeds the supported size"
STALE_CODE = "artifact_manifest_stale"
STALE_MESSAGE = "Git material changed while capturing the artifact manifest"

_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MODES = frozenset({"100644", "100755", "120000", "160000"})
_KIND_RANK = {"add": 0, "modify": 1, "delete": 2, "rename": 3}
_OPAQUE_KINDS = frozenset({"diff_fingerprint", "external_revision"})
_GIT_KINDS = frozenset({"git_snapshot", "git_commit"})
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "AUX",
        "CON",
        "CONIN$",
        "CONOUT$",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)
_BATCH_OBJECT_LIMIT = ARTIFACT_ENTRY_LIMIT * 2
_BATCH_INPUT_BYTE_LIMIT = _BATCH_OBJECT_LIMIT * (64 + 1)
_BATCH_OUTPUT_BYTE_LIMIT = _BATCH_OBJECT_LIMIT * (64 + len(" blob ") + 20 + 1)
_BATCH_CHECK_ARGUMENT = "--batch-check=%(objectname) %(objecttype) %(objectsize)"
_BATCH_CHECK_RECORD = re.compile(
    rb"(?P<object_id>(?:[0-9a-f]{40}|[0-9a-f]{64})) "
    rb"blob (?P<object_size>0|[1-9][0-9]{0,19})\Z"
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_OBSERVATION_CONFIG = ("-c", "core.fsmonitor=false")
_RAW_DIFF_BYTE_LIMIT = ARTIFACT_MANIFEST_BYTE_LIMIT
_RAW_DIFF_RECORD_LIMIT = ARTIFACT_ENTRY_LIMIT * 2
_INDEX_RECORD_FIXED_BYTES = 6 + 1 + 1 + 1 + 1
_TREE_RECORD_FIXED_BYTES = 6 + 1 + len("commit") + 1 + 1


@dataclass(frozen=True)
class ArtifactManifestError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _path_unsafe() -> ArtifactManifestError:
    return ArtifactManifestError(PATH_UNSAFE_CODE, PATH_UNSAFE_MESSAGE)


def _too_large() -> ArtifactManifestError:
    return ArtifactManifestError(TOO_LARGE_CODE, TOO_LARGE_MESSAGE)


def _stale() -> ArtifactManifestError:
    return ArtifactManifestError(STALE_CODE, STALE_MESSAGE)


def _inconsistent() -> EvidenceLedgerError:
    return EvidenceLedgerError(
        EVIDENCE_LEDGER_ERROR_CODE,
        EVIDENCE_LEDGER_ERROR_MESSAGE,
    )


def validate_artifact_path(value: object) -> str:
    """Return one byte-preserving, portable project-relative POSIX path."""

    if not isinstance(value, str):
        raise _path_unsafe()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _path_unsafe() from exc
    if not encoded or len(encoded) > ARTIFACT_PATH_BYTE_LIMIT:
        raise _path_unsafe()
    if (
        value.startswith("/")
        or "\\" in value
        or "\0" in value
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in value
        )
    ):
        raise _path_unsafe()
    parts = value.split("/")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part)
            or part.endswith((".", " "))
        ):
            raise _path_unsafe()
        device_basename = part.split(".", 1)[0].rstrip(" ").upper()
        if device_basename in _WINDOWS_RESERVED_BASENAMES:
            raise _path_unsafe()
    return value


def decode_artifact_path(value: bytes) -> str:
    try:
        decoded = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _path_unsafe() from exc
    return validate_artifact_path(decoded)


def _validate_mode_and_object(mode: object, object_id: object) -> tuple[str, str]:
    if (
        not isinstance(mode, str)
        or mode not in _MODES
        or not isinstance(object_id, str)
        or _OBJECT_ID.fullmatch(object_id) is None
        or set(object_id) == {"0"}
    ):
        raise _stale()
    return mode, object_id


@dataclass(frozen=True)
class ArtifactLeaf:
    relative_posix_path: str
    mode: str
    object_id: str

    def __post_init__(self) -> None:
        validate_artifact_path(self.relative_posix_path)
        _validate_mode_and_object(self.mode, self.object_id)


@dataclass(frozen=True)
class ArtifactManifestEntry:
    ordinal: int
    kind: str
    old_path: str | None
    new_path: str | None
    before_mode: str | None
    before_object_id: str | None
    after_mode: str | None
    after_object_id: str | None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0 or self.kind not in _KIND_RANK:
            raise _inconsistent()
        if self.old_path is not None:
            validate_artifact_path(self.old_path)
        if self.new_path is not None:
            validate_artifact_path(self.new_path)
        before = (self.before_mode, self.before_object_id)
        after = (self.after_mode, self.after_object_id)
        before_null = before == (None, None)
        after_null = after == (None, None)
        if not before_null:
            if None in before:
                raise _inconsistent()
            try:
                _validate_mode_and_object(*before)
            except ArtifactManifestError as exc:
                raise _inconsistent() from exc
        if not after_null:
            if None in after:
                raise _inconsistent()
            try:
                _validate_mode_and_object(*after)
            except ArtifactManifestError as exc:
                raise _inconsistent() from exc
        valid = (
            self.kind == "add"
            and self.old_path is None
            and before_null
            and self.new_path is not None
            and not after_null
        ) or (
            self.kind == "delete"
            and self.old_path is not None
            and not before_null
            and self.new_path is None
            and after_null
        ) or (
            self.kind == "modify"
            and self.old_path is not None
            and self.old_path == self.new_path
            and not before_null
            and not after_null
            and before != after
        ) or (
            self.kind == "rename"
            and self.old_path is not None
            and self.new_path is not None
            and self.old_path != self.new_path
            and not before_null
            and before == after
        )
        if not valid:
            raise _inconsistent()

    def canonical_value(self) -> dict[str, Any]:
        return {
            "after_mode": self.after_mode,
            "after_object_id": self.after_object_id,
            "before_mode": self.before_mode,
            "before_object_id": self.before_object_id,
            "kind": self.kind,
            "new_path": self.new_path,
            "old_path": self.old_path,
            "ordinal": self.ordinal,
        }


def _leaf_map(leaves: Iterable[ArtifactLeaf]) -> dict[str, ArtifactLeaf]:
    result: dict[str, ArtifactLeaf] = {}
    for leaf in leaves:
        if not isinstance(leaf, ArtifactLeaf) or leaf.relative_posix_path in result:
            raise _stale()
        result[leaf.relative_posix_path] = leaf
    return result


def _entry_sort_text(value: str | None) -> tuple[int, bytes]:
    return (0, b"") if value is None else (1, value.encode("utf-8"))


def _entry_sort_key(entry: ArtifactManifestEntry) -> tuple[Any, ...]:
    primary = entry.old_path if entry.old_path is not None else entry.new_path
    secondary = entry.new_path if entry.new_path is not None else entry.old_path
    return (
        _entry_sort_text(primary),
        _entry_sort_text(secondary),
        _KIND_RANK[entry.kind],
        _entry_sort_text(entry.before_mode),
        _entry_sort_text(entry.before_object_id),
        _entry_sort_text(entry.after_mode),
        _entry_sort_text(entry.after_object_id),
    )


def build_artifact_entries(
    before_leaves: Iterable[ArtifactLeaf],
    after_leaves: Iterable[ArtifactLeaf],
) -> tuple[ArtifactManifestEntry, ...]:
    """Merge leaves, apply unique exact renames, sort, then assign ordinals."""

    before = _leaf_map(before_leaves)
    after = _leaf_map(after_leaves)
    deletes: list[ArtifactLeaf] = []
    adds: list[ArtifactLeaf] = []
    provisional: list[ArtifactManifestEntry] = []
    for path in sorted(before.keys() | after.keys(), key=lambda item: item.encode("utf-8")):
        old = before.get(path)
        new = after.get(path)
        if old is None:
            assert new is not None
            adds.append(new)
        elif new is None:
            deletes.append(old)
        elif (old.mode, old.object_id) != (new.mode, new.object_id):
            provisional.append(
                ArtifactManifestEntry(
                    ordinal=0,
                    kind="modify",
                    old_path=path,
                    new_path=path,
                    before_mode=old.mode,
                    before_object_id=old.object_id,
                    after_mode=new.mode,
                    after_object_id=new.object_id,
                )
            )

    deletes_by_value: dict[tuple[str, str], list[ArtifactLeaf]] = defaultdict(list)
    adds_by_value: dict[tuple[str, str], list[ArtifactLeaf]] = defaultdict(list)
    for leaf in deletes:
        deletes_by_value[(leaf.mode, leaf.object_id)].append(leaf)
    for leaf in adds:
        adds_by_value[(leaf.mode, leaf.object_id)].append(leaf)

    renamed_delete_paths: set[str] = set()
    renamed_add_paths: set[str] = set()
    for key in deletes_by_value.keys() & adds_by_value.keys():
        old_group = deletes_by_value[key]
        new_group = adds_by_value[key]
        if len(old_group) == 1 and len(new_group) == 1:
            old = old_group[0]
            new = new_group[0]
            renamed_delete_paths.add(old.relative_posix_path)
            renamed_add_paths.add(new.relative_posix_path)
            provisional.append(
                ArtifactManifestEntry(
                    ordinal=0,
                    kind="rename",
                    old_path=old.relative_posix_path,
                    new_path=new.relative_posix_path,
                    before_mode=old.mode,
                    before_object_id=old.object_id,
                    after_mode=new.mode,
                    after_object_id=new.object_id,
                )
            )

    for leaf in deletes:
        if leaf.relative_posix_path not in renamed_delete_paths:
            provisional.append(
                ArtifactManifestEntry(
                    ordinal=0,
                    kind="delete",
                    old_path=leaf.relative_posix_path,
                    new_path=None,
                    before_mode=leaf.mode,
                    before_object_id=leaf.object_id,
                    after_mode=None,
                    after_object_id=None,
                )
            )
    for leaf in adds:
        if leaf.relative_posix_path not in renamed_add_paths:
            provisional.append(
                ArtifactManifestEntry(
                    ordinal=0,
                    kind="add",
                    old_path=None,
                    new_path=leaf.relative_posix_path,
                    before_mode=None,
                    before_object_id=None,
                    after_mode=leaf.mode,
                    after_object_id=leaf.object_id,
                )
            )
    if len(provisional) > ARTIFACT_ENTRY_LIMIT:
        raise _too_large()
    ordered = sorted(provisional, key=_entry_sort_key)
    return tuple(
        ArtifactManifestEntry(
            ordinal=ordinal,
            kind=entry.kind,
            old_path=entry.old_path,
            new_path=entry.new_path,
            before_mode=entry.before_mode,
            before_object_id=entry.before_object_id,
            after_mode=entry.after_mode,
            after_object_id=entry.after_object_id,
        )
        for ordinal, entry in enumerate(ordered)
    )


@dataclass(frozen=True)
class ArtifactObservation:
    state: str
    object_format: str | None
    comparison_base: str | None
    target_kind: str
    target_value: str
    target_base_revision: str
    before_leaves: tuple[ArtifactLeaf, ...] = ()
    after_leaves: tuple[ArtifactLeaf, ...] = ()
    omission_code: str | None = None

    def __post_init__(self) -> None:
        if self.state == "complete_git":
            if (
                self.target_kind not in _GIT_KINDS
                or self.object_format not in {"sha1", "sha256"}
                or not isinstance(self.comparison_base, str)
                or _OBJECT_ID.fullmatch(self.comparison_base) is None
                or self.omission_code is not None
            ):
                raise _inconsistent()
            expected_length = 40 if self.object_format == "sha1" else 64
            if (
                len(self.comparison_base) != expected_length
                or set(self.comparison_base) == {"0"}
                or (
                    self.target_kind == "git_snapshot"
                    and (
                        self.comparison_base != self.target_base_revision
                        or _DIGEST.fullmatch(self.target_value) is None
                    )
                )
                or (
                    self.target_kind == "git_commit"
                    and (
                        self.target_base_revision != ""
                        or _OBJECT_ID.fullmatch(self.target_value) is None
                        or len(self.target_value) != expected_length
                        or set(self.target_value) == {"0"}
                    )
                )
                or any(
                    len(leaf.object_id) != expected_length
                    for leaf in (*self.before_leaves, *self.after_leaves)
                )
            ):
                raise _stale()
        elif self.state == "opaque_target":
            if (
                self.target_kind not in _OPAQUE_KINDS
                or self.object_format is not None
                or self.comparison_base is not None
                or self.before_leaves
                or self.after_leaves
                or self.omission_code != ARTIFACT_CONTENT_NOT_OBSERVED
            ):
                raise _inconsistent()
        else:
            raise _inconsistent()
        if not isinstance(self.target_value, str) or not self.target_value:
            raise _inconsistent()
        if not isinstance(self.target_base_revision, str):
            raise _inconsistent()
        if self.target_kind == "git_snapshot":
            if not self.target_base_revision:
                raise _inconsistent()
        elif self.target_base_revision:
            raise _inconsistent()


@dataclass(frozen=True)
class ArtifactManifestSpec:
    state: str
    object_format: str | None
    comparison_base: str | None
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int
    authority_snapshot_id: str
    acceptance_criterion_id: str | None
    verification_criterion_id: str | None
    omission_code: str | None
    entries: tuple[ArtifactManifestEntry, ...]
    digest: str
    canonical_size: int

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def canonical_value(self) -> dict[str, Any]:
        return {
            "acceptance_criterion_id": self.acceptance_criterion_id,
            "authority_snapshot_id": self.authority_snapshot_id,
            "comparison_base": self.comparison_base,
            "entries": [entry.canonical_value() for entry in self.entries],
            "object_format": self.object_format,
            "omission_code": self.omission_code,
            "state": self.state,
            "target_base_revision": self.target_base_revision,
            "target_generation": self.target_generation,
            "target_kind": self.target_kind,
            "target_value": self.target_value,
            "verification_criterion_id": self.verification_criterion_id,
        }

    def __post_init__(self) -> None:
        TargetCaptureBinding(
            target_kind=self.target_kind,
            target_value=self.target_value,
            target_base_revision=self.target_base_revision,
            target_generation=self.target_generation,
            authority_snapshot_id=self.authority_snapshot_id,
            acceptance_criterion_id=self.acceptance_criterion_id,
            verification_criterion_id=self.verification_criterion_id,
        )
        if tuple(entry.ordinal for entry in self.entries) != tuple(range(len(self.entries))):
            raise _inconsistent()
        if len(self.entries) > ARTIFACT_ENTRY_LIMIT:
            raise _too_large()
        if tuple(sorted(self.entries, key=_entry_sort_key)) != self.entries:
            raise _inconsistent()
        if self.state == "complete_git":
            expected_length = 40 if self.object_format == "sha1" else 64
            if (
                self.target_kind not in _GIT_KINDS
                or self.object_format not in {"sha1", "sha256"}
                or not isinstance(self.comparison_base, str)
                or _OBJECT_ID.fullmatch(self.comparison_base) is None
                or set(self.comparison_base) == {"0"}
                or len(self.comparison_base) != expected_length
                or (
                    self.target_kind == "git_snapshot"
                    and self.comparison_base != self.target_base_revision
                )
                or (
                    self.target_kind == "git_commit"
                    and (
                        _OBJECT_ID.fullmatch(self.target_value) is None
                        or len(self.target_value) != expected_length
                        or set(self.target_value) == {"0"}
                    )
                )
                or self.omission_code is not None
                or any(
                    len(object_id) != expected_length
                    for entry in self.entries
                    for object_id in (entry.before_object_id, entry.after_object_id)
                    if object_id is not None
                )
            ):
                raise _inconsistent()
        elif self.state == "opaque_target":
            if (
                self.target_kind not in _OPAQUE_KINDS
                or self.object_format is not None
                or self.comparison_base is not None
                or self.entries
                or self.omission_code != ARTIFACT_CONTENT_NOT_OBSERVED
            ):
                raise _inconsistent()
        else:
            raise _inconsistent()
        canonical = canonical_json_bytes(self.canonical_value())
        if len(canonical) > ARTIFACT_MANIFEST_BYTE_LIMIT:
            raise _too_large()
        expected = "sha256:" + hashlib.sha256(
            ARTIFACT_MANIFEST_DOMAIN + canonical
        ).hexdigest()
        if self.canonical_size != len(canonical) or self.digest != expected:
            raise _inconsistent()


def build_artifact_manifest(
    observation: ArtifactObservation,
    binding: TargetCaptureBinding,
) -> ArtifactManifestSpec:
    """Bind one already-closed observation to DB-owned target basis values."""

    if not isinstance(observation, ArtifactObservation) or not isinstance(
        binding, TargetCaptureBinding
    ):
        raise _inconsistent()
    if binding.capture_version != 1:
        from task_governance_tool.evidence_ledger import require_capture_v1

        require_capture_v1(binding.capture_version)
    if (
        observation.target_kind,
        observation.target_value,
        observation.target_base_revision,
    ) != (
        binding.target_kind,
        binding.target_value,
        binding.target_base_revision,
    ):
        raise _inconsistent()
    entries = (
        build_artifact_entries(observation.before_leaves, observation.after_leaves)
        if observation.state == "complete_git"
        else ()
    )
    value = {
        "acceptance_criterion_id": binding.acceptance_criterion_id,
        "authority_snapshot_id": binding.authority_snapshot_id,
        "comparison_base": observation.comparison_base,
        "entries": [entry.canonical_value() for entry in entries],
        "object_format": observation.object_format,
        "omission_code": observation.omission_code,
        "state": observation.state,
        "target_base_revision": binding.target_base_revision,
        "target_generation": binding.target_generation,
        "target_kind": binding.target_kind,
        "target_value": binding.target_value,
        "verification_criterion_id": binding.verification_criterion_id,
    }
    canonical = canonical_json_bytes(value)
    if len(canonical) > ARTIFACT_MANIFEST_BYTE_LIMIT:
        raise _too_large()
    digest = "sha256:" + hashlib.sha256(
        ARTIFACT_MANIFEST_DOMAIN + canonical
    ).hexdigest()
    return ArtifactManifestSpec(
        state=observation.state,
        object_format=observation.object_format,
        comparison_base=observation.comparison_base,
        target_kind=binding.target_kind,
        target_value=binding.target_value,
        target_base_revision=binding.target_base_revision,
        target_generation=binding.target_generation,
        authority_snapshot_id=binding.authority_snapshot_id,
        acceptance_criterion_id=binding.acceptance_criterion_id,
        verification_criterion_id=binding.verification_criterion_id,
        omission_code=observation.omission_code,
        entries=entries,
        digest=digest,
        canonical_size=len(canonical),
    )


def opaque_artifact_observation(
    *,
    target_kind: str,
    target_value: str,
) -> ArtifactObservation:
    return ArtifactObservation(
        state="opaque_target",
        object_format=None,
        comparison_base=None,
        target_kind=target_kind,
        target_value=target_value,
        target_base_revision="",
        omission_code=ARTIFACT_CONTENT_NOT_OBSERVED,
    )


def _object_format(repo: Path, *, stale: bool) -> str:
    try:
        payload = run_git_bytes(
            repo,
            [*_GIT_OBSERVATION_CONFIG, "rev-parse", "--show-object-format"],
            code=STALE_CODE if stale else "invalid_review_evidence",
            message=STALE_MESSAGE if stale else "Git object format could not be read safely",
            output_limit=128,
        )
        value = payload.decode("ascii", errors="strict").strip()
    except (GitSnapshotError, UnicodeDecodeError) as exc:
        if stale:
            raise _stale() from exc
        if isinstance(exc, GitSnapshotError):
            raise
        raise GitSnapshotError(
            "invalid_review_evidence",
            "Git object format could not be read safely",
            "review_target_value",
        ) from exc
    if value not in {"sha1", "sha256"}:
        if stale:
            raise _stale()
        raise GitSnapshotError(
            "invalid_review_evidence",
            "Git object format is unsupported",
            "review_target_value",
        )
    return value


def _git_stale(repo: Path, arguments: list[str]) -> bytes:
    try:
        return run_git_bytes(
            repo,
            [*_GIT_OBSERVATION_CONFIG, *arguments],
            code=STALE_CODE,
            message=STALE_MESSAGE,
        )
    except GitSnapshotError as exc:
        raise _stale() from exc


def _payload_identity(payload: bytes) -> GitStreamIdentity:
    return GitStreamIdentity(
        byte_count=len(payload),
        digest="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


def _leaf(entry: GitSnapshotEntry, object_format: str, *, stale: bool) -> ArtifactLeaf:
    expected_length = 40 if object_format == "sha1" else 64
    if len(entry.object_id) != expected_length:
        raise _stale()
    try:
        mode = entry.mode.decode("ascii", errors="strict")
        object_id = entry.object_id.decode("ascii", errors="strict")
        return ArtifactLeaf(
            relative_posix_path=decode_artifact_path(entry.path),
            mode=mode,
            object_id=object_id,
        )
    except (UnicodeDecodeError, ArtifactManifestError) as exc:
        if stale:
            raise _stale() from exc
        if isinstance(exc, ArtifactManifestError):
            raise
        raise _stale() from exc


def _stream_entry_validator(
    object_format: str,
    *,
    stale: bool,
) -> Any:
    def validate(entry: GitSnapshotEntry) -> None:
        _leaf(entry, object_format, stale=stale)

    return validate


def _stream_tree_material(
    repo: Path,
    tree_id: str,
    object_format: str,
    *,
    stale: bool,
) -> GitTreeObservation:
    code = STALE_CODE if stale else "review_target_mismatch"
    message = STALE_MESSAGE if stale else "completion commit tree output is malformed"
    object_id_length = 40 if object_format == "sha1" else 64
    try:
        return stream_tree_entries(
            repo,
            tree_id,
            object_id_length=object_id_length,
            code=code,
            field="completion_revision",
            message=message,
            record_byte_limit=(
                _TREE_RECORD_FIXED_BYTES
                + object_id_length
                + ARTIFACT_PATH_BYTE_LIMIT
            ),
            record_overflow_error=_stale if stale else _path_unsafe,
            consume_entry=_stream_entry_validator(object_format, stale=stale),
        )
    except GitSnapshotError as exc:
        if stale:
            raise _stale() from exc
        raise


def _stream_index_material(
    repo: Path,
    head: str,
    object_format: str,
    *,
    stale: bool,
) -> GitIndexObservation:
    code = STALE_CODE if stale else "invalid_review_evidence"
    message = STALE_MESSAGE if stale else "Git index snapshot output is malformed"
    object_id_length = 40 if object_format == "sha1" else 64
    try:
        return stream_index_fingerprint(
            repo,
            head,
            code=code,
            message=message,
            record_byte_limit=(
                _INDEX_RECORD_FIXED_BYTES
                + object_id_length
                + ARTIFACT_PATH_BYTE_LIMIT
            ),
            record_overflow_error=_stale if stale else _path_unsafe,
            consume_entry=_stream_entry_validator(object_format, stale=stale),
        )
    except GitSnapshotError as exc:
        if stale:
            raise _stale() from exc
        raise


def _bounded_diff_payload(
    repo: Path,
    arguments: list[str],
    *,
    stale: bool,
) -> tuple[bytes, GitStreamIdentity]:
    payload = bytearray()

    def collect(chunk: bytes) -> None:
        if len(payload) > _RAW_DIFF_BYTE_LIMIT - len(chunk):
            raise _stale() if stale else _too_large()
        payload.extend(chunk)

    try:
        identity = run_git_stream(
            repo,
            [*_GIT_OBSERVATION_CONFIG, *arguments],
            collect,
            code=STALE_CODE if stale else "invalid_review_evidence",
            message=STALE_MESSAGE if stale else "Git snapshot data could not be read safely",
        )
    except GitSnapshotError as exc:
        if stale:
            raise _stale() from exc
        raise
    return bytes(payload), identity


def _raw_diff_leaves(
    payload: bytes,
    object_format: str,
    *,
    stale: bool,
    commit: bool,
) -> tuple[tuple[ArtifactLeaf, ...], tuple[ArtifactLeaf, ...]]:
    code = STALE_CODE if stale else (
        "review_target_mismatch" if commit else "invalid_review_evidence"
    )
    field = "completion_revision" if commit else "review_target_value"
    message = STALE_MESSAGE if stale else (
        "completion commit tree output is malformed"
        if commit
        else "Git snapshot change output is malformed"
    )
    try:
        records = split_nul_records(
            payload,
            code=code,
            field=field,
            message=message,
        )
    except GitSnapshotError as exc:
        if stale:
            raise _stale() from exc
        raise
    if len(records) % 2 != 0:
        if stale:
            raise _stale()
        raise GitSnapshotError(code, message, field)
    changed_count = len(records) // 2
    if changed_count > _RAW_DIFF_RECORD_LIMIT:
        raise _stale() if stale else _too_large()

    expected_length = 40 if object_format == "sha1" else 64
    before: list[ArtifactLeaf] = []
    after: list[ArtifactLeaf] = []
    observed_paths: set[bytes] = set()
    for index in range(0, len(records), 2):
        metadata, raw_path = records[index : index + 2]
        match = RAW_DIFF_HEADER.fullmatch(metadata)
        if (
            match is None
            or not raw_path
            or raw_path in observed_paths
            or match.group("score")
        ):
            if stale:
                raise _stale()
            raise GitSnapshotError(code, message, field)
        observed_paths.add(raw_path)
        before_mode = match.group("before_mode")
        after_mode = match.group("after_mode")
        before_object_id = match.group("before_object_id")
        after_object_id = match.group("after_object_id")
        status = match.group("status")
        before_present = before_mode != b"000000"
        after_present = after_mode != b"000000"
        valid_absence = (
            (before_present or set(before_object_id) == {ord("0")})
            and (after_present or set(after_object_id) == {ord("0")})
        )
        valid_presence = (
            (not before_present or set(before_object_id) != {ord("0")})
            and (not after_present or set(after_object_id) != {ord("0")})
        )
        expected_status = (
            b"A"
            if not before_present and after_present
            else b"D"
            if before_present and not after_present
            else None
        )
        if (
            len(before_object_id) != expected_length
            or len(after_object_id) != expected_length
            or not valid_absence
            or not valid_presence
            or not (before_present or after_present)
            or (expected_status is not None and status != expected_status)
            or (expected_status is None and status not in {b"M", b"T"})
        ):
            if stale:
                raise _stale()
            raise GitSnapshotError(code, message, field)
        try:
            path = decode_artifact_path(raw_path)
            if before_present:
                before.append(
                    ArtifactLeaf(
                        path,
                        before_mode.decode("ascii", errors="strict"),
                        before_object_id.decode("ascii", errors="strict"),
                    )
                )
            if after_present:
                after.append(
                    ArtifactLeaf(
                        path,
                        after_mode.decode("ascii", errors="strict"),
                        after_object_id.decode("ascii", errors="strict"),
                    )
                )
        except (UnicodeDecodeError, ArtifactManifestError) as exc:
            if stale:
                raise _stale() from exc
            if isinstance(exc, ArtifactManifestError):
                raise
            raise _stale() from exc
    return tuple(before), tuple(after)


def _require_manifest_objects(
    repo: Path,
    before: tuple[ArtifactLeaf, ...],
    after: tuple[ArtifactLeaf, ...],
) -> None:
    """Fail closed when a blob named by a changed manifest entry is absent."""

    entries = build_artifact_entries(before, after)
    object_ids = sorted(
        {
            object_id
            for entry in entries
            for mode, object_id in (
                (entry.before_mode, entry.before_object_id),
                (entry.after_mode, entry.after_object_id),
            )
            if mode is not None and object_id is not None and mode != "160000"
        }
    )
    if not object_ids:
        return
    if len(object_ids) > _BATCH_OBJECT_LIMIT:
        raise _too_large()
    request = b"".join(object_id.encode("ascii") + b"\n" for object_id in object_ids)
    if len(request) > _BATCH_INPUT_BYTE_LIMIT:
        raise _too_large()
    try:
        result = subprocess.run(
            [
                *safe_git_command(repo),
                *_GIT_OBSERVATION_CONFIG,
                "cat-file",
                _BATCH_CHECK_ARGUMENT,
            ],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
            env=safe_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _stale() from exc
    if (
        result.returncode != 0
        or len(result.stdout) > _BATCH_OUTPUT_BYTE_LIMIT
        or not result.stdout.endswith(b"\n")
    ):
        raise _stale()
    records = result.stdout[:-1].split(b"\n")
    if len(records) != len(object_ids):
        raise _stale()
    for expected_object_id, record in zip(object_ids, records):
        match = _BATCH_CHECK_RECORD.fullmatch(record)
        if (
            match is None
            or match.group("object_id").decode("ascii") != expected_object_id
        ):
            raise _stale()


def _commit_tree(
    repo: Path,
    commit_id: str,
    *,
    stale: bool,
) -> tuple[GitStreamIdentity, str, list[str]]:
    payload = (
        _git_stale(repo, ["cat-file", "commit", commit_id])
        if stale
        else run_git_bytes(
            repo,
            [*_GIT_OBSERVATION_CONFIG, "cat-file", "commit", commit_id],
        )
    )
    try:
        tree_id, parents = parse_commit_tree_and_parents(
            payload,
            code=STALE_CODE if stale else "invalid_review_evidence",
            field="review_target_value",
            message=STALE_MESSAGE if stale else "Git commit topology is unsupported",
        )
    except GitSnapshotError as exc:
        if stale:
            raise _stale() from exc
        raise
    return _payload_identity(payload), tree_id, parents


@dataclass(frozen=True)
class _StagedMaterial:
    object_format: str
    head: str
    head_commit: GitStreamIdentity
    head_tree: GitTreeObservation
    visible_diff: GitStreamIdentity
    invisible_diff: GitStreamIdentity
    index: GitIndexObservation


def _initial_staged_material(repo: Path) -> tuple[_StagedMaterial, tuple[ArtifactLeaf, ...], tuple[ArtifactLeaf, ...]]:
    try:
        head = resolve_git_commit(repo, "HEAD")
    except CompletionEvidenceError as exc:
        raise GitSnapshotError(exc.code, exc.message, "review_target_base_revision") from exc
    object_format = _object_format(repo, stale=False)
    expected_length = 40 if object_format == "sha1" else 64
    if len(head) != expected_length:
        raise GitSnapshotError(
            "invalid_review_evidence",
            "Git object format is unsupported",
            "review_target_value",
        )
    head_commit, tree_id, _parents = _commit_tree(repo, head, stale=False)
    if len(tree_id) != expected_length:
        raise GitSnapshotError(
            "invalid_review_evidence",
            "Git object format is unsupported",
            "review_target_value",
        )
    head_tree = _stream_tree_material(repo, tree_id, object_format, stale=False)
    common = [
        "diff-index",
        "--cached",
        "--raw",
        "-z",
        "--no-abbrev",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
    ]
    visible_payload, visible = _bounded_diff_payload(
        repo,
        [*common, "--ita-visible-in-index", head, "--"],
        stale=False,
    )
    invisible_payload, invisible = _bounded_diff_payload(
        repo,
        [*common, "--ita-invisible-in-index", head, "--"],
        stale=False,
    )
    if visible_payload != invisible_payload:
        raise GitSnapshotError(
            "invalid_review_evidence",
            "Git snapshot does not support intent-to-add index entries",
            "review_target_value",
        )
    before, after = _raw_diff_leaves(
        visible_payload,
        object_format,
        stale=False,
        commit=False,
    )
    index = _stream_index_material(repo, head, object_format, stale=False)
    return (
        _StagedMaterial(
            object_format,
            head,
            head_commit,
            head_tree,
            visible,
            invisible,
            index,
        ),
        before,
        after,
    )


def _repeat_staged_material(repo: Path, initial: _StagedMaterial) -> _StagedMaterial:
    try:
        head = resolve_git_commit(repo, "HEAD")
    except CompletionEvidenceError as exc:
        raise _stale() from exc
    object_format = _object_format(repo, stale=True)
    expected_length = 40 if object_format == "sha1" else 64
    if len(head) != expected_length:
        raise _stale()
    commit_identity, tree_id, _parents = _commit_tree(repo, head, stale=True)
    if len(tree_id) != expected_length:
        raise _stale()
    tree = _stream_tree_material(repo, tree_id, object_format, stale=True)
    common = [
        "diff-index",
        "--cached",
        "--raw",
        "-z",
        "--no-abbrev",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
    ]
    visible_payload, visible = _bounded_diff_payload(
        repo,
        [*common, "--ita-visible-in-index", head, "--"],
        stale=True,
    )
    invisible_payload, invisible = _bounded_diff_payload(
        repo,
        [*common, "--ita-invisible-in-index", head, "--"],
        stale=True,
    )
    if visible_payload != invisible_payload:
        raise _stale()
    _raw_diff_leaves(
        visible_payload,
        object_format,
        stale=True,
        commit=False,
    )
    index = _stream_index_material(repo, head, object_format, stale=True)
    return _StagedMaterial(
        object_format,
        head,
        commit_identity,
        tree,
        visible,
        invisible,
        index,
    )


def observe_staged_git_manifest(repo: Path) -> ArtifactObservation:
    """Observe exact HEAD-tree versus stable stage-0 index material."""

    initial, before, after = _initial_staged_material(repo)
    _require_manifest_objects(repo, before, after)
    repeated = _repeat_staged_material(repo, initial)
    if initial != repeated:
        raise _stale()
    _require_manifest_objects(repo, before, after)
    return ArtifactObservation(
        state="complete_git",
        object_format=initial.object_format,
        comparison_base=initial.head,
        target_kind="git_snapshot",
        target_value=initial.index.fingerprint,
        target_base_revision=initial.head,
        before_leaves=before,
        after_leaves=after,
    )


@dataclass(frozen=True)
class _CommitMaterial:
    object_format: str
    commit_id: str
    commit: GitStreamIdentity
    target_tree: GitTreeObservation
    comparison_base: str
    comparison_commit: GitStreamIdentity | None
    before_tree: GitTreeObservation
    diff: GitStreamIdentity
    before_leaves: tuple[ArtifactLeaf, ...]
    after_leaves: tuple[ArtifactLeaf, ...]


def _capture_commit_material(
    repo: Path,
    revision: str,
    *,
    stale: bool,
) -> _CommitMaterial:
    try:
        commit_id = resolve_git_commit(repo, revision)
    except CompletionEvidenceError as exc:
        if stale:
            raise _stale() from exc
        raise
    object_format = _object_format(repo, stale=stale)
    expected_length = 40 if object_format == "sha1" else 64
    if len(commit_id) != expected_length:
        if stale:
            raise _stale()
        raise GitSnapshotError(
            "invalid_review_evidence",
            "Git object format is unsupported",
            "review_target_value",
        )
    commit_identity, tree_id, parents = _commit_tree(repo, commit_id, stale=stale)
    if len(tree_id) != expected_length or any(
        len(parent) != expected_length for parent in parents
    ):
        if stale:
            raise _stale()
        raise GitSnapshotError(
            "invalid_review_evidence",
            "Git object format is unsupported",
            "review_target_value",
        )
    target_tree = _stream_tree_material(repo, tree_id, object_format, stale=stale)
    if parents:
        comparison_base = parents[0]
        comparison_commit, before_tree_id, _ = _commit_tree(
            repo,
            comparison_base,
            stale=stale,
        )
        if len(before_tree_id) != expected_length:
            if stale:
                raise _stale()
            raise GitSnapshotError(
                "invalid_review_evidence",
                "Git object format is unsupported",
                "review_target_value",
            )
    else:
        comparison_commit = None
        empty = (
            _git_stale(repo, ["hash-object", "-t", "tree", "--stdin"])
            if stale
            else run_git_bytes(
                repo,
                [*_GIT_OBSERVATION_CONFIG, "hash-object", "-t", "tree", "--stdin"],
                output_limit=128,
            )
        )
        try:
            comparison_base = empty.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError as exc:
            if stale:
                raise _stale() from exc
            raise GitSnapshotError(
                "invalid_review_evidence",
                "Git empty tree identity is unsupported",
                "review_target_value",
            ) from exc
        if (
            _OBJECT_ID.fullmatch(comparison_base) is None
            or len(comparison_base) != expected_length
        ):
            if stale:
                raise _stale()
            raise GitSnapshotError(
                "invalid_review_evidence",
                "Git empty tree identity is unsupported",
                "review_target_value",
            )
        before_tree_id = comparison_base
    before_tree = _stream_tree_material(
        repo,
        before_tree_id,
        object_format,
        stale=stale,
    )
    diff_payload, diff_identity = _bounded_diff_payload(
        repo,
        [
            "diff-tree",
            "-r",
            "--raw",
            "-z",
            "--no-abbrev",
            "--no-renames",
            "--no-commit-id",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=none",
            before_tree_id,
            tree_id,
            "--",
        ],
        stale=stale,
    )
    before_leaves, after_leaves = _raw_diff_leaves(
        diff_payload,
        object_format,
        stale=stale,
        commit=True,
    )
    return _CommitMaterial(
        object_format,
        commit_id,
        commit_identity,
        target_tree,
        comparison_base,
        comparison_commit,
        before_tree,
        diff_identity,
        before_leaves,
        after_leaves,
    )


def observe_git_commit_manifest(repo: Path, revision: str) -> ArtifactObservation:
    """Observe one canonical commit versus its first parent or the empty tree."""

    initial = _capture_commit_material(repo, revision, stale=False)
    _require_manifest_objects(repo, initial.before_leaves, initial.after_leaves)
    repeated = _capture_commit_material(repo, revision, stale=True)
    if initial != repeated:
        raise _stale()
    _require_manifest_objects(repo, initial.before_leaves, initial.after_leaves)
    return ArtifactObservation(
        state="complete_git",
        object_format=initial.object_format,
        comparison_base=initial.comparison_base,
        target_kind="git_commit",
        target_value=initial.commit_id,
        target_base_revision="",
        before_leaves=initial.before_leaves,
        after_leaves=initial.after_leaves,
    )
