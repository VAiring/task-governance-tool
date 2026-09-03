"""Stable Git target observation and private TG-M24.2A materialization.

This module owns only the target-material boundary.  It does not select a
verification plan, launch target code, write project files, or persist state.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from task_governance_tool.artifact_manifest import (
    ARTIFACT_ENTRY_LIMIT,
    ARTIFACT_PATH_BYTE_LIMIT,
    ArtifactManifestError,
    ArtifactObservation,
    decode_artifact_path,
    observe_git_commit_manifest,
    observe_staged_git_manifest,
    validate_artifact_path,
)
from task_governance_tool.completion import safe_git_command, safe_git_environment
from task_governance_tool.evidence_ledger import EvidenceLedgerError, domain_digest
from task_governance_tool.git_snapshot import (
    GIT_COMMAND_TIMEOUT_SECONDS,
    GitSnapshotEntry,
    GitSnapshotError,
    parse_commit_tree_and_parents,
    run_git_bytes,
    run_git_stream,
    stream_index_fingerprint,
    stream_tree_entries,
    verify_git_snapshot_commit,
)
from task_governance_tool.state_paths import (
    StatePathError,
    create_physical_directory_exclusive,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    require_contained,
)


TARGET_MATERIAL_DOMAIN = b"taskgov-verification-runner-target-material-v1\0"
TARGET_FILE_LIMIT = ARTIFACT_ENTRY_LIMIT
TARGET_DIRECTORY_LIMIT = 30_000
TARGET_DEPTH_LIMIT = 64
TARGET_TOTAL_BYTE_LIMIT = 512 * 1024 * 1024

TARGET_ERROR_MESSAGE = "verification runner target is invalid"
TARGET_UNSUPPORTED_MESSAGE = "verification runner target is unsupported"
TARGET_STALE_MESSAGE = "verification runner target changed during observation"
MATERIALIZATION_ERROR_MESSAGE = (
    "verification runner target could not be materialized safely"
)

_REGULAR_MODES = frozenset({"100644", "100755"})
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BATCH_CHECK_ARGUMENT = "--batch-check=%(objectname) %(objecttype) %(objectsize)"
_BATCH_CHECK_RECORD = re.compile(
    rb"(?P<object_id>(?:[0-9a-f]{40}|[0-9a-f]{64})) "
    rb"blob (?P<object_size>0|[1-9][0-9]{0,19})\Z"
)
_BATCH_INPUT_BYTE_LIMIT = TARGET_FILE_LIMIT * (64 + 1)
_BATCH_OUTPUT_BYTE_LIMIT = TARGET_FILE_LIMIT * (64 + len(" blob ") + 20 + 1)
_GIT_OBSERVATION_CONFIG = ("-c", "core.fsmonitor=false")
_GIT_RECORD_BYTE_LIMIT = ARTIFACT_PATH_BYTE_LIMIT + 80
_COPY_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class VerificationRunnerGitError(Exception):
    """One sanitized failure at the Git target-material boundary."""

    code: str
    message: str = TARGET_ERROR_MESSAGE

    def __str__(self) -> str:
        return self.message


def _target_error(code: str = "target_invalid") -> VerificationRunnerGitError:
    return VerificationRunnerGitError(code=code)


def _unsupported() -> VerificationRunnerGitError:
    return VerificationRunnerGitError(
        code="target_unsupported",
        message=TARGET_UNSUPPORTED_MESSAGE,
    )


def _too_large() -> VerificationRunnerGitError:
    return VerificationRunnerGitError(
        code="target_too_large",
        message=TARGET_ERROR_MESSAGE,
    )


def _stale() -> VerificationRunnerGitError:
    return VerificationRunnerGitError(
        code="target_stale",
        message=TARGET_STALE_MESSAGE,
    )


def _materialization_error() -> VerificationRunnerGitError:
    return VerificationRunnerGitError(
        code="materialization_failed",
        message=MATERIALIZATION_ERROR_MESSAGE,
    )


def _object_id_length(object_format: str) -> int:
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    raise _target_error()


def _git_hasher(object_format: str) -> Any:
    if object_format == "sha1":
        return hashlib.sha1()
    if object_format == "sha256":
        return hashlib.sha256()
    raise _target_error()


def _collision_key(relative_posix_path: str) -> str:
    return "/".join(
        unicodedata.normalize("NFC", component).casefold()
        for component in relative_posix_path.split("/")
    )


@dataclass(frozen=True)
class RunnerTargetEntry:
    relative_posix_path: str
    mode: str
    object_id: str

    def __post_init__(self) -> None:
        try:
            validate_artifact_path(self.relative_posix_path)
        except ArtifactManifestError as exc:
            raise _unsupported() from exc
        if (
            type(self.mode) is not str
            or self.mode not in _REGULAR_MODES
            or type(self.object_id) is not str
            or _OBJECT_ID.fullmatch(self.object_id) is None
            or set(self.object_id) == {"0"}
        ):
            raise _target_error()

    def canonical_value(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "object_id": self.object_id,
            "relative_posix_path": self.relative_posix_path,
        }


def _validate_entry_set(
    entries: tuple[RunnerTargetEntry, ...],
    *,
    object_format: str,
) -> tuple[str, ...]:
    if type(entries) is not tuple or len(entries) > TARGET_FILE_LIMIT:
        raise _too_large()
    expected_object_id_length = _object_id_length(object_format)
    previous_path: bytes | None = None
    file_keys: dict[str, str] = {}
    directory_keys: dict[str, str] = {}
    directories: set[str] = set()

    for entry in entries:
        if type(entry) is not RunnerTargetEntry:
            raise _target_error()
        try:
            encoded_path = entry.relative_posix_path.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise _unsupported() from exc
        if (
            len(entry.object_id) != expected_object_id_length
            or previous_path is not None
            and previous_path >= encoded_path
        ):
            raise _target_error()
        previous_path = encoded_path

        parts = entry.relative_posix_path.split("/")
        if len(parts) > TARGET_DEPTH_LIMIT:
            raise _too_large()
        file_key = _collision_key(entry.relative_posix_path)
        if file_key in file_keys or file_key in directory_keys:
            raise _unsupported()
        file_keys[file_key] = entry.relative_posix_path

        for index in range(1, len(parts)):
            relative_directory = "/".join(parts[:index])
            directory_key = _collision_key(relative_directory)
            prior_directory = directory_keys.get(directory_key)
            if directory_key in file_keys or (
                prior_directory is not None and prior_directory != relative_directory
            ):
                raise _unsupported()
            directory_keys[directory_key] = relative_directory
            directories.add(relative_directory)
            if len(directories) > TARGET_DIRECTORY_LIMIT:
                raise _too_large()

    return tuple(
        sorted(
            directories,
            key=lambda value: (
                len(value.split("/")),
                value.encode("utf-8", errors="strict"),
            ),
        )
    )


def _target_material_digest_from_identity(
    *,
    target_kind: str,
    target_value: str,
    target_base_revision: str,
    object_format: str,
    entries: tuple[RunnerTargetEntry, ...],
) -> str:
    value = {
        "entries": [entry.canonical_value() for entry in entries],
        "object_format": object_format,
        "target_base_revision": target_base_revision,
        "target_kind": target_kind,
        "target_value": target_value,
    }
    try:
        return domain_digest(TARGET_MATERIAL_DOMAIN, value)
    except (EvidenceLedgerError, TypeError, ValueError, UnicodeError) as exc:
        raise _target_error() from exc


def _target_material_digest(
    artifact: ArtifactObservation,
    object_format: str,
    entries: tuple[RunnerTargetEntry, ...],
) -> str:
    return _target_material_digest_from_identity(
        target_kind=artifact.target_kind,
        target_value=artifact.target_value,
        target_base_revision=artifact.target_base_revision,
        object_format=object_format,
        entries=entries,
    )


@dataclass(frozen=True)
class RunnerTargetObservation:
    artifact: ArtifactObservation
    object_format: str
    entries: tuple[RunnerTargetEntry, ...]
    target_material_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.artifact) is not ArtifactObservation
            or self.artifact.state != "complete_git"
            or self.artifact.target_kind not in {"git_snapshot", "git_commit"}
            or self.artifact.object_format != self.object_format
        ):
            raise _target_error()
        _validate_entry_set(self.entries, object_format=self.object_format)
        if (
            type(self.target_material_digest) is not str
            or _DIGEST.fullmatch(self.target_material_digest) is None
            or self.target_material_digest
            != _target_material_digest(self.artifact, self.object_format, self.entries)
        ):
            raise _target_error()


@dataclass(frozen=True)
class RunnerMaterialization:
    target: RunnerTargetObservation
    object_sizes: tuple[tuple[str, int], ...]
    total_bytes: int
    target_material_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.target) is not RunnerTargetObservation
            or type(self.object_sizes) is not tuple
            or type(self.total_bytes) is not int
            or self.total_bytes < 0
            or self.total_bytes > TARGET_TOTAL_BYTE_LIMIT
            or type(self.target_material_digest) is not str
            or self.target_material_digest != self.target.target_material_digest
        ):
            raise _target_error()

        expected_length = _object_id_length(self.target.object_format)
        previous_object_id: str | None = None
        sizes: dict[str, int] = {}
        for item in self.object_sizes:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or _OBJECT_ID.fullmatch(item[0]) is None
                or len(item[0]) != expected_length
                or type(item[1]) is not int
                or item[1] < 0
                or item[1] > TARGET_TOTAL_BYTE_LIMIT
                or previous_object_id is not None
                and previous_object_id >= item[0]
            ):
                raise _target_error()
            previous_object_id = item[0]
            sizes[item[0]] = item[1]

        if set(sizes) != {entry.object_id for entry in self.target.entries}:
            raise _target_error()
        computed_total = sum(sizes[entry.object_id] for entry in self.target.entries)
        if computed_total != self.total_bytes:
            raise _target_error()


@dataclass(frozen=True)
class MaterializedRunnerTarget:
    target_material_digest: str
    entry_count: int
    directory_count: int
    total_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.target_material_digest) is not str
            or _DIGEST.fullmatch(self.target_material_digest) is None
            or type(self.entry_count) is not int
            or self.entry_count < 0
            or self.entry_count > TARGET_FILE_LIMIT
            or type(self.directory_count) is not int
            or self.directory_count < 0
            or self.directory_count > TARGET_DIRECTORY_LIMIT
            or type(self.total_bytes) is not int
            or self.total_bytes < 0
            or self.total_bytes > TARGET_TOTAL_BYTE_LIMIT
        ):
            raise _target_error()


def _collect_target_entries(
    stream: Callable[[Callable[[GitSnapshotEntry], None]], object],
    *,
    object_format: str,
) -> tuple[tuple[RunnerTargetEntry, ...], object]:
    entries: list[RunnerTargetEntry] = []

    def consume(entry: GitSnapshotEntry) -> None:
        if len(entries) >= TARGET_FILE_LIMIT:
            raise _too_large()
        try:
            mode = entry.mode.decode("ascii", errors="strict")
            object_id = entry.object_id.decode("ascii", errors="strict")
            relative_path = decode_artifact_path(entry.path)
        except (UnicodeDecodeError, ArtifactManifestError) as exc:
            raise _unsupported() from exc
        if mode not in _REGULAR_MODES:
            raise _unsupported()
        entries.append(RunnerTargetEntry(relative_path, mode, object_id))

    stream_result = stream(consume)
    closed_entries = tuple(entries)
    _validate_entry_set(closed_entries, object_format=object_format)
    return closed_entries, stream_result


def _capture_staged_entries(
    repo: Path,
    artifact: ArtifactObservation,
) -> tuple[tuple[RunnerTargetEntry, ...], object]:
    def stream(consume: Callable[[GitSnapshotEntry], None]) -> object:
        return stream_index_fingerprint(
            repo,
            artifact.target_base_revision,
            code="target_stale",
            field="target",
            message=TARGET_STALE_MESSAGE,
            record_byte_limit=_GIT_RECORD_BYTE_LIMIT,
            record_overflow_error=_too_large,
            consume_entry=consume,
        )

    entries, observation = _collect_target_entries(
        stream,
        object_format=artifact.object_format or "",
    )
    if (
        getattr(observation, "fingerprint", None) != artifact.target_value
        or getattr(observation, "entry_count", None) != len(entries)
    ):
        raise _stale()
    return entries, observation


def _commit_tree_id(repo: Path, artifact: ArtifactObservation) -> str:
    payload = run_git_bytes(
        repo,
        [
            *_GIT_OBSERVATION_CONFIG,
            "cat-file",
            "commit",
            artifact.target_value,
        ],
        code="target_stale",
        field="target",
        message=TARGET_STALE_MESSAGE,
    )
    try:
        tree_id, _parents = parse_commit_tree_and_parents(
            payload,
            code="target_stale",
            field="target",
            message=TARGET_STALE_MESSAGE,
        )
    except GitSnapshotError as exc:
        raise _stale() from exc
    if len(tree_id) != _object_id_length(artifact.object_format or ""):
        raise _stale()
    return tree_id


def _capture_commit_entries(
    repo: Path,
    artifact: ArtifactObservation,
) -> tuple[tuple[RunnerTargetEntry, ...], object]:
    tree_id = _commit_tree_id(repo, artifact)

    def stream(consume: Callable[[GitSnapshotEntry], None]) -> object:
        return stream_tree_entries(
            repo,
            tree_id,
            object_id_length=_object_id_length(artifact.object_format or ""),
            code="target_stale",
            field="target",
            message=TARGET_STALE_MESSAGE,
            record_byte_limit=_GIT_RECORD_BYTE_LIMIT,
            record_overflow_error=_too_large,
            consume_entry=consume,
        )

    entries, observation = _collect_target_entries(
        stream,
        object_format=artifact.object_format or "",
    )
    if getattr(observation, "entry_count", None) != len(entries):
        raise _stale()
    return entries, (tree_id, observation)


def _translate_observation_error(exc: Exception) -> VerificationRunnerGitError:
    if isinstance(exc, VerificationRunnerGitError):
        return exc
    if isinstance(exc, ArtifactManifestError):
        if exc.code == "artifact_manifest_too_large":
            return _too_large()
        if exc.code == "artifact_manifest_path_unsafe":
            return _unsupported()
        if exc.code == "artifact_manifest_stale":
            return _stale()
    if isinstance(exc, GitSnapshotError):
        if (
            exc.code == "invalid_review_evidence"
            and exc.message
            == "Git snapshot does not support sparse-directory index entries"
        ):
            return _unsupported()
        return _stale()
    if isinstance(exc, (OSError, subprocess.SubprocessError)):
        return _stale()
    return _target_error()


def observe_staged_runner_target(repo: Path) -> RunnerTargetObservation:
    """Capture one complete, stable stage-zero index target."""

    try:
        repo = Path(repo)
        artifact_before = observe_staged_git_manifest(repo)
        entries_before, stream_before = _capture_staged_entries(repo, artifact_before)
        try:
            artifact_after = observe_staged_git_manifest(repo)
            entries_after, stream_after = _capture_staged_entries(
                repo, artifact_after
            )
        except Exception as exc:
            raise _stale() from exc
        if (
            artifact_before != artifact_after
            or entries_before != entries_after
            or stream_before != stream_after
        ):
            raise _stale()
        object_format = artifact_before.object_format or ""
        digest = _target_material_digest(
            artifact_before,
            object_format,
            entries_before,
        )
        return RunnerTargetObservation(
            artifact=artifact_before,
            object_format=object_format,
            entries=entries_before,
            target_material_digest=digest,
        )
    except Exception as exc:
        translated = _translate_observation_error(exc)
        if translated is exc:
            raise
        raise translated from exc


def observe_commit_runner_target(
    repo: Path,
    revision: str,
) -> RunnerTargetObservation:
    """Capture one complete, stable exact-commit tree target."""

    try:
        repo = Path(repo)
        artifact_before = observe_git_commit_manifest(repo, revision)
        entries_before, stream_before = _capture_commit_entries(repo, artifact_before)
        try:
            artifact_after = observe_git_commit_manifest(repo, revision)
            entries_after, stream_after = _capture_commit_entries(
                repo, artifact_after
            )
        except Exception as exc:
            raise _stale() from exc
        if (
            artifact_before != artifact_after
            or entries_before != entries_after
            or stream_before != stream_after
        ):
            raise _stale()
        object_format = artifact_before.object_format or ""
        digest = _target_material_digest(
            artifact_before,
            object_format,
            entries_before,
        )
        return RunnerTargetObservation(
            artifact=artifact_before,
            object_format=object_format,
            entries=entries_before,
            target_material_digest=digest,
        )
    except Exception as exc:
        translated = _translate_observation_error(exc)
        if translated is exc:
            raise
        raise translated from exc


def _recapture_target(
    repo: Path,
    target: RunnerTargetObservation,
) -> RunnerTargetObservation:
    try:
        if target.artifact.target_kind == "git_snapshot":
            observed = observe_staged_runner_target(repo)
        elif target.artifact.target_kind == "git_commit":
            observed = observe_commit_runner_target(repo, target.artifact.target_value)
        else:
            raise _target_error()
    except VerificationRunnerGitError as exc:
        raise _stale() from exc
    if observed != target:
        raise _stale()
    return observed


def _batch_object_sizes(
    repo: Path,
    target: RunnerTargetObservation,
) -> tuple[tuple[str, int], ...]:
    object_ids = sorted({entry.object_id for entry in target.entries})
    if not object_ids:
        return ()
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
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
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

    sizes: list[tuple[str, int]] = []
    for expected_object_id, record in zip(object_ids, records):
        match = _BATCH_CHECK_RECORD.fullmatch(record)
        if match is None:
            raise _stale()
        try:
            observed_object_id = match.group("object_id").decode(
                "ascii", errors="strict"
            )
            object_size = int(match.group("object_size"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise _stale() from exc
        if (
            observed_object_id != expected_object_id
        ):
            raise _stale()
        if object_size > TARGET_TOTAL_BYTE_LIMIT:
            raise _too_large()
        sizes.append((expected_object_id, object_size))
    return tuple(sizes)


def preflight_runner_material(
    repo: Path,
    observation: RunnerTargetObservation,
) -> RunnerMaterialization:
    """Bind exact blob sizes to a still-current target observation."""

    if type(observation) is not RunnerTargetObservation:
        raise _target_error()
    try:
        repo = Path(repo)
        _recapture_target(repo, observation)
        object_sizes = _batch_object_sizes(repo, observation)
        size_by_object = dict(object_sizes)
        total_bytes = 0
        for entry in observation.entries:
            object_size = size_by_object.get(entry.object_id)
            if object_size is None or (
                total_bytes > TARGET_TOTAL_BYTE_LIMIT - object_size
            ):
                raise _too_large()
            total_bytes += object_size
        _recapture_target(repo, observation)
        return RunnerMaterialization(
            target=observation,
            object_sizes=object_sizes,
            total_bytes=total_bytes,
            target_material_digest=observation.target_material_digest,
        )
    except VerificationRunnerGitError:
        raise
    except Exception as exc:
        raise _translate_observation_error(exc) from exc


def preflight_runner_snapshot_successor_material_digest(
    repo: Path,
    completion_revision: str,
    *,
    expected_base_revision: str,
    expected_fingerprint: str,
) -> str:
    """Bind one exact reviewed snapshot to its immutable successor commit."""

    try:
        repo = Path(repo)
        committed = observe_commit_runner_target(repo, completion_revision)
        if committed.artifact.target_value != completion_revision:
            raise _stale()
        material = preflight_runner_material(repo, committed)
        verified = verify_git_snapshot_commit(
            repo,
            completion_revision,
            expected_base_revision=expected_base_revision,
            expected_fingerprint=expected_fingerprint,
        )
        if (
            committed.artifact.comparison_base != expected_base_revision
            or verified.entry_count != len(committed.entries)
        ):
            raise _stale()
        return _target_material_digest_from_identity(
            target_kind="git_snapshot",
            target_value=expected_fingerprint,
            target_base_revision=expected_base_revision,
            object_format=material.target.object_format,
            entries=material.target.entries,
        )
    except Exception as exc:
        translated = _translate_observation_error(exc)
        if translated is exc:
            raise
        raise translated from exc


def _is_reparse(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _directory_root(path: Path) -> Path:
    if not path.is_absolute() or not path.anchor:
        raise _materialization_error()
    return Path(path.anchor)


def _directory_identity(path: Path, *, root: Path) -> tuple[int, int]:
    inspected = inspect_physical_directory(path, root=root)
    return inspected.identity.device, inspected.identity.inode


def _require_empty_destination(
    repo: Path,
    destination: Path,
) -> tuple[Path, tuple[int, int]]:
    filesystem_root = _directory_root(destination)
    initial_identity = _directory_identity(destination, root=filesystem_root)
    try:
        with os.scandir(destination) as iterator:
            if next(iterator, None) is not None:
                raise _materialization_error()
    except VerificationRunnerGitError:
        raise
    except OSError as exc:
        raise _materialization_error() from exc
    if _directory_identity(destination, root=filesystem_root) != initial_identity:
        raise _materialization_error()

    try:
        canonical_destination = destination.resolve(strict=True)
        canonical_repo = repo.resolve(strict=True)
    except OSError as exc:
        raise _materialization_error() from exc
    try:
        canonical_repo.relative_to(canonical_destination)
    except ValueError:
        pass
    else:
        raise _materialization_error()
    return filesystem_root, initial_identity


def _relative_path(root: Path, relative_posix_path: str) -> Path:
    return root.joinpath(*relative_posix_path.split("/"))


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        try:
            written = os.write(descriptor, data[offset:])
        except OSError as exc:
            raise _materialization_error() from exc
        if written <= 0:
            raise _materialization_error()
        offset += written


def _file_object_identity(details: os.stat_result) -> tuple[int, int, int]:
    return int(details.st_dev), int(details.st_ino), int(details.st_size)


def _stream_blob_to_file(
    repo: Path,
    destination: Path,
    entry: RunnerTargetEntry,
    expected_size: int,
    object_format: str,
) -> None:
    path = _relative_path(destination, entry.relative_posix_path)
    try:
        require_contained(path, destination)
        inspect_physical_directory(path.parent, root=destination)
        if path_lexically_exists(path):
            raise _materialization_error()
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except VerificationRunnerGitError:
        raise
    except (OSError, StatePathError) as exc:
        raise _materialization_error() from exc

    observed_size = 0
    hasher = _git_hasher(object_format)
    hasher.update(f"blob {expected_size}\0".encode("ascii"))
    opened_identity: tuple[int, int, int] | None = None
    try:
        opened = os.fstat(descriptor)
        if _is_reparse(opened) or not stat.S_ISREG(opened.st_mode):
            raise _materialization_error()
        opened_identity = _file_object_identity(opened)

        def consume(chunk: bytes) -> None:
            nonlocal observed_size
            if observed_size > expected_size - len(chunk):
                raise _stale()
            _write_all(descriptor, chunk)
            hasher.update(chunk)
            observed_size += len(chunk)

        stream_identity = run_git_stream(
            repo,
            [
                *_GIT_OBSERVATION_CONFIG,
                "cat-file",
                "blob",
                entry.object_id,
            ],
            consume,
            code="target_stale",
            field="target",
            message=TARGET_STALE_MESSAGE,
        )
        if (
            observed_size != expected_size
            or stream_identity.byte_count != expected_size
            or hasher.hexdigest() != entry.object_id
        ):
            raise _stale()
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise _materialization_error() from exc
        final_opened = os.fstat(descriptor)
        if (
            _is_reparse(final_opened)
            or not stat.S_ISREG(final_opened.st_mode)
            or opened_identity is None
            or _file_object_identity(final_opened)[:2] != opened_identity[:2]
            or int(final_opened.st_size) != expected_size
        ):
            raise _materialization_error()
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass

    try:
        _path, identity = inspect_physical_file(
            path,
            root=destination,
            max_bytes=expected_size,
        )
    except StatePathError as exc:
        raise _materialization_error() from exc
    if (
        opened_identity is None
        or identity.size != expected_size
        or (identity.device, identity.inode) != opened_identity[:2]
    ):
        raise _materialization_error()


def _hash_materialized_file(
    path: Path,
    *,
    destination: Path,
    expected_size: int,
    object_format: str,
) -> str:
    try:
        _path, before = inspect_physical_file(
            path,
            root=destination,
            max_bytes=expected_size,
        )
    except StatePathError as exc:
        raise _materialization_error() from exc
    if before.size != expected_size:
        raise _materialization_error()
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        raise _materialization_error() from exc

    digest = _git_hasher(object_format)
    digest.update(f"blob {expected_size}\0".encode("ascii"))
    observed_size = 0
    try:
        opened = os.fstat(descriptor)
        if (
            _is_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or (int(opened.st_dev), int(opened.st_ino))
            != (before.device, before.inode)
            or int(opened.st_size) != expected_size
        ):
            raise _materialization_error()
        while True:
            try:
                chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            except OSError as exc:
                raise _materialization_error() from exc
            if not chunk:
                break
            if observed_size > expected_size - len(chunk):
                raise _materialization_error()
            digest.update(chunk)
            observed_size += len(chunk)
        after_opened = os.fstat(descriptor)
        if (
            observed_size != expected_size
            or _is_reparse(after_opened)
            or not stat.S_ISREG(after_opened.st_mode)
            or (int(after_opened.st_dev), int(after_opened.st_ino))
            != (before.device, before.inode)
            or int(after_opened.st_size) != expected_size
        ):
            raise _materialization_error()
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass

    try:
        _path, after = inspect_physical_file(
            path,
            root=destination,
            max_bytes=expected_size,
        )
    except StatePathError as exc:
        raise _materialization_error() from exc
    if after.size != expected_size or after != before:
        raise _materialization_error()
    return digest.hexdigest()


def _inventory_materialized_target(
    destination: Path,
    target: RunnerTargetObservation,
    object_sizes: dict[str, int],
    *,
    filesystem_root: Path,
    root_identity: tuple[int, int],
) -> tuple[int, int, int]:
    expected_entries = {entry.relative_posix_path: entry for entry in target.entries}
    expected_directories = set(
        _validate_entry_set(target.entries, object_format=target.object_format)
    )
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    total_bytes = 0
    observed_nodes = 0
    pending: list[tuple[Path, str]] = [(destination, "")]

    while pending:
        directory, relative_directory = pending.pop()
        try:
            _directory_identity(directory, root=destination)
            with os.scandir(directory) as iterator:
                for child in iterator:
                    observed_nodes += 1
                    if observed_nodes > TARGET_FILE_LIMIT + TARGET_DIRECTORY_LIMIT:
                        raise _materialization_error()
                    relative_path = (
                        f"{relative_directory}/{child.name}"
                        if relative_directory
                        else child.name
                    )
                    try:
                        validate_artifact_path(relative_path)
                        details = child.stat(follow_symlinks=False)
                    except (ArtifactManifestError, OSError) as exc:
                        raise _materialization_error() from exc
                    if _is_reparse(details):
                        raise _materialization_error()
                    child_path = Path(child.path)
                    if stat.S_ISDIR(details.st_mode):
                        if (
                            relative_path not in expected_directories
                            or relative_path in observed_directories
                            or len(relative_path.split("/")) >= TARGET_DEPTH_LIMIT
                        ):
                            raise _materialization_error()
                        observed_directories.add(relative_path)
                        if len(observed_directories) > TARGET_DIRECTORY_LIMIT:
                            raise _materialization_error()
                        pending.append((child_path, relative_path))
                    elif stat.S_ISREG(details.st_mode):
                        entry = expected_entries.get(relative_path)
                        if entry is None or relative_path in observed_files:
                            raise _materialization_error()
                        expected_size = object_sizes[entry.object_id]
                        observed_object_id = _hash_materialized_file(
                            child_path,
                            destination=destination,
                            expected_size=expected_size,
                            object_format=target.object_format,
                        )
                        if observed_object_id != entry.object_id:
                            raise _materialization_error()
                        observed_files.add(relative_path)
                        if total_bytes > TARGET_TOTAL_BYTE_LIMIT - expected_size:
                            raise _materialization_error()
                        total_bytes += expected_size
                    else:
                        raise _materialization_error()
        except VerificationRunnerGitError:
            raise
        except (OSError, StatePathError) as exc:
            raise _materialization_error() from exc

    if (
        observed_files != set(expected_entries)
        or observed_directories != expected_directories
        or _directory_identity(destination, root=filesystem_root) != root_identity
    ):
        raise _materialization_error()
    return len(observed_files), len(observed_directories), total_bytes


def materialize_runner_target(
    repo: Path,
    material: RunnerMaterialization,
    destination: Path,
) -> MaterializedRunnerTarget:
    """Materialize one preflighted Git target into an existing empty directory."""

    if type(material) is not RunnerMaterialization:
        raise _target_error()
    try:
        repo = Path(repo)
        destination = Path(destination)
        _recapture_target(repo, material.target)
        directories = _validate_entry_set(
            material.target.entries,
            object_format=material.target.object_format,
        )
        filesystem_root, root_identity = _require_empty_destination(repo, destination)
        for relative_directory in directories:
            create_physical_directory_exclusive(
                _relative_path(destination, relative_directory),
                root=destination,
            )

        object_sizes = dict(material.object_sizes)
        for entry in material.target.entries:
            _stream_blob_to_file(
                repo,
                destination,
                entry,
                object_sizes[entry.object_id],
                material.target.object_format,
            )

        entry_count, directory_count, total_bytes = _inventory_materialized_target(
            destination,
            material.target,
            object_sizes,
            filesystem_root=filesystem_root,
            root_identity=root_identity,
        )
        if total_bytes != material.total_bytes:
            raise _materialization_error()
        _recapture_target(repo, material.target)
        return MaterializedRunnerTarget(
            target_material_digest=material.target_material_digest,
            entry_count=entry_count,
            directory_count=directory_count,
            total_bytes=total_bytes,
        )
    except VerificationRunnerGitError:
        raise
    except (ArtifactManifestError, GitSnapshotError) as exc:
        raise _stale() from exc
    except (OSError, StatePathError, subprocess.SubprocessError) as exc:
        raise _materialization_error() from exc
    except Exception as exc:
        raise _materialization_error() from exc
