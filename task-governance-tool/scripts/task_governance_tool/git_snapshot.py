"""Canonical, read-only Git snapshot capture and completion comparison."""

from __future__ import annotations

import hashlib
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from task_governance_tool.completion import (
    CompletionEvidenceError,
    FULL_GIT_OBJECT_ID,
    resolve_git_commit,
    safe_git_command,
    safe_git_environment,
)


MANIFEST_PREFIX = b"taskgov-git-snapshot-v1\0"
OBJECT_ID_BYTES = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SNAPSHOT_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z")
INDEX_LEAF_MODES = {b"100644", b"100755", b"120000", b"160000"}
SPARSE_DIRECTORY_MODES = {b"040000", b"40000"}
INDEX_HEADER = re.compile(
    rb"(?P<mode>[0-9]{6}) "
    rb"(?P<object_id>(?:[0-9a-f]{40}|[0-9a-f]{64})) "
    rb"(?P<stage>[0-3])\Z"
)
TREE_HEADER = re.compile(
    rb"(?P<mode>[0-9]{5,6}) "
    rb"(?P<object_type>blob|commit|tree) "
    rb"(?P<object_id>(?:[0-9a-f]{40}|[0-9a-f]{64}))\Z"
)
RAW_DIFF_HEADER = re.compile(
    rb":(?P<before_mode>[0-7]{6}) (?P<after_mode>[0-7]{6}) "
    rb"(?P<before_object_id>(?:[0-9a-f]{40}|[0-9a-f]{64})) "
    rb"(?P<after_object_id>(?:[0-9a-f]{40}|[0-9a-f]{64})) "
    rb"(?P<status>[ACDMRTUXB])(?P<score>[0-9]*)\Z"
)

GIT_COMMAND_TIMEOUT_SECONDS = 15
GIT_TERMINATION_GRACE_SECONDS = 2
GIT_CAPTURE_BYTE_LIMIT = 16_777_216
GIT_STREAM_CHUNK_SIZE = 64 * 1024
GIT_RECORD_BYTE_LIMIT = 1_048_576
_FS_MONITOR_DISABLED = ("-c", "core.fsmonitor=false")


@dataclass
class GitSnapshotError(Exception):
    code: str
    message: str
    field: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class GitSnapshotEntry:
    mode: bytes
    object_id: bytes
    path: bytes


@dataclass(frozen=True)
class GitSnapshot:
    base_revision: str
    fingerprint: str
    entry_count: int
    changed_paths: tuple[bytes, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class GitStreamIdentity:
    byte_count: int
    digest: str


@dataclass(frozen=True)
class GitIndexObservation:
    identity: GitStreamIdentity
    fingerprint: str
    entry_count: int


@dataclass(frozen=True)
class GitTreeObservation:
    identity: GitStreamIdentity
    entry_count: int


def snapshot_error(code: str, message: str, field: str) -> GitSnapshotError:
    return GitSnapshotError(code=code, message=message, field=field)


def run_git_stream(
    repo: Path,
    arguments: list[str],
    consume: Callable[[bytes], None],
    *,
    code: str = "invalid_review_evidence",
    field: str = "review_target_value",
    message: str = "Git snapshot data could not be read safely",
) -> GitStreamIdentity:
    """Consume stdout incrementally with a fixed timeout and no stderr retention."""

    try:
        process = subprocess.Popen(
            [*safe_git_command(repo), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            env=safe_git_environment(),
        )
    except OSError as exc:
        raise snapshot_error(code, message, field) from exc

    digest = hashlib.sha256()
    byte_count = 0
    reader_errors: list[Exception] = []

    def read_stdout() -> None:
        nonlocal byte_count
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(GIT_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                byte_count += len(chunk)
                digest.update(chunk)
                consume(chunk)
        except Exception as exc:  # Propagated on the calling thread below.
            reader_errors.append(exc)
            try:
                process.kill()
            except OSError:
                pass

    reader = threading.Thread(
        target=read_stdout,
        name="taskgov-git-stdout",
        daemon=True,
    )
    reader.start()
    timed_out = False
    cleanup_incomplete = False
    try:
        returncode = process.wait(timeout=GIT_COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        try:
            returncode = process.wait(timeout=GIT_TERMINATION_GRACE_SECONDS)
        except (OSError, subprocess.SubprocessError):
            cleanup_incomplete = True
            returncode = -1
    except (OSError, subprocess.SubprocessError):
        cleanup_incomplete = True
        returncode = -1
        try:
            process.kill()
        except OSError:
            pass
    finally:
        reader.join(timeout=GIT_TERMINATION_GRACE_SECONDS)
        if reader.is_alive():
            cleanup_incomplete = True
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except (OSError, ValueError):
                    pass
            reader.join(timeout=GIT_TERMINATION_GRACE_SECONDS)
        elif process.stdout is not None:
            try:
                process.stdout.close()
            except (OSError, ValueError):
                cleanup_incomplete = True

    if reader_errors:
        reader_error = reader_errors[0]
        if not isinstance(reader_error, (OSError, ValueError)):
            raise reader_error
        cleanup_incomplete = True
    if cleanup_incomplete or reader.is_alive() or timed_out or returncode != 0:
        raise snapshot_error(code, message, field)
    return GitStreamIdentity(
        byte_count=byte_count,
        digest="sha256:" + digest.hexdigest(),
    )


def run_git_bytes(
    repo: Path,
    arguments: list[str],
    *,
    code: str = "invalid_review_evidence",
    field: str = "review_target_value",
    message: str = "Git snapshot data could not be read safely",
    output_limit: int = GIT_CAPTURE_BYTE_LIMIT,
) -> bytes:
    if (
        isinstance(output_limit, bool)
        or not isinstance(output_limit, int)
        or output_limit < 0
    ):
        raise ValueError("output_limit must be a non-negative integer")
    payload = bytearray()

    def collect(chunk: bytes) -> None:
        if len(payload) > output_limit - len(chunk):
            raise snapshot_error(code, message, field)
        payload.extend(chunk)

    run_git_stream(
        repo,
        arguments,
        collect,
        code=code,
        field=field,
        message=message,
    )
    return bytes(payload)


def validate_object_id(
    value: bytes,
    *,
    code: str,
    field: str,
    message: str,
) -> bytes:
    if not OBJECT_ID_BYTES.fullmatch(value) or set(value) == {ord("0")}:
        raise snapshot_error(code, message, field)
    return value


def validate_entries(
    entries: list[GitSnapshotEntry],
    *,
    code: str = "invalid_review_evidence",
    field: str = "review_target_value",
    message: str = "Git snapshot contains unsupported repository entries",
    object_id_length: int | None = None,
) -> list[GitSnapshotEntry]:
    ordered = sorted(entries, key=lambda entry: entry.path)
    if any(
        not entry.path
        or entry.mode not in INDEX_LEAF_MODES
        or (
            object_id_length is not None
            and len(entry.object_id) != object_id_length
        )
        for entry in ordered
    ):
        raise snapshot_error(code, message, field)
    for entry in ordered:
        validate_object_id(
            entry.object_id,
            code=code,
            field=field,
            message=message,
        )
    if any(
        left.path == right.path
        for left, right in zip(ordered, ordered[1:])
    ):
        raise snapshot_error(code, message, field)
    return ordered


def split_nul_records(
    payload: bytes,
    *,
    code: str,
    field: str,
    message: str,
) -> list[bytes]:
    if not payload:
        return []
    if not payload.endswith(b"\0"):
        raise snapshot_error(code, message, field)
    records = payload[:-1].split(b"\0")
    if any(not record for record in records):
        raise snapshot_error(code, message, field)
    return records


def parse_raw_diff_paths(payload: bytes) -> tuple[bytes, ...]:
    records = split_nul_records(
        payload,
        code="invalid_review_evidence",
        field="review_target_value",
        message="Git snapshot change output is malformed",
    )
    if len(records) % 2 != 0:
        raise snapshot_error(
            "invalid_review_evidence",
            "Git snapshot change output is malformed",
            "review_target_value",
        )
    paths: list[bytes] = []
    for index in range(0, len(records), 2):
        metadata, path = records[index : index + 2]
        if RAW_DIFF_HEADER.fullmatch(metadata) is None or not path:
            raise snapshot_error(
                "invalid_review_evidence",
                "Git snapshot change output is malformed",
                "review_target_value",
            )
        paths.append(path)
    return tuple(paths)


def _parse_index_record(
    record: bytes,
    *,
    code: str,
    field: str,
    message: str,
) -> GitSnapshotEntry:
    try:
        metadata, path = record.split(b"\t", 1)
    except ValueError as exc:
        raise snapshot_error(code, message, field) from exc
    match = INDEX_HEADER.fullmatch(metadata)
    if match is None or not path:
        raise snapshot_error(code, message, field)
    mode = match.group("mode")
    object_id = match.group("object_id")
    stage = match.group("stage")
    if stage != b"0":
        raise snapshot_error(
            code,
            (
                "Git snapshot requires a fully merged stage-0 index"
                if code == "invalid_review_evidence"
                else message
            ),
            field,
        )
    if mode in SPARSE_DIRECTORY_MODES:
        raise snapshot_error(
            code,
            (
                "Git snapshot does not support sparse-directory index entries"
                if code == "invalid_review_evidence"
                else message
            ),
            field,
        )
    if mode not in INDEX_LEAF_MODES:
        raise snapshot_error(
            code,
            (
                "Git snapshot contains an unsupported index mode"
                if code == "invalid_review_evidence"
                else message
            ),
            field,
        )
    return GitSnapshotEntry(
        mode=mode,
        object_id=validate_object_id(
            object_id,
            code=code,
            field=field,
            message=(
                "Git snapshot contains an unsupported index object"
                if code == "invalid_review_evidence"
                else message
            ),
        ),
        path=path,
    )


def parse_index_entries(payload: bytes) -> list[GitSnapshotEntry]:
    entries = [
        _parse_index_record(
            record,
            code="invalid_review_evidence",
            field="review_target_value",
            message="Git index snapshot output is malformed",
        )
        for record in split_nul_records(
            payload,
            code="invalid_review_evidence",
            field="review_target_value",
            message="Git index snapshot output is malformed",
        )
    ]
    return validate_entries(entries)


def _parse_tree_record(
    record: bytes,
    *,
    code: str,
    field: str,
    message: str,
) -> GitSnapshotEntry:
    try:
        metadata, path = record.split(b"\t", 1)
    except ValueError as exc:
        raise snapshot_error(code, message, field) from exc
    match = TREE_HEADER.fullmatch(metadata)
    if match is None or not path:
        raise snapshot_error(code, message, field)
    mode = match.group("mode")
    object_type = match.group("object_type")
    object_id = match.group("object_id")
    expected_type = b"commit" if mode == b"160000" else b"blob"
    if mode not in INDEX_LEAF_MODES or object_type != expected_type:
        raise snapshot_error(
            code,
            (
                "completion commit tree contains an unsupported entry"
                if code == "review_target_mismatch"
                else message
            ),
            field,
        )
    return GitSnapshotEntry(
        mode=mode,
        object_id=validate_object_id(
            object_id,
            code=code,
            field=field,
            message=(
                "completion commit tree contains an unsupported object"
                if code == "review_target_mismatch"
                else message
            ),
        ),
        path=path,
    )


def parse_tree_entries(payload: bytes) -> list[GitSnapshotEntry]:
    entries = [
        _parse_tree_record(
            record,
            code="review_target_mismatch",
            field="completion_revision",
            message="completion commit tree output is malformed",
        )
        for record in split_nul_records(
            payload,
            code="review_target_mismatch",
            field="completion_revision",
            message="completion commit tree output is malformed",
        )
    ]
    return validate_entries(
        entries,
        code="review_target_mismatch",
        field="completion_revision",
        message="completion commit tree cannot match the reviewed snapshot",
    )


def manifest_fingerprint(
    base_revision: str,
    entries: list[GitSnapshotEntry],
) -> str:
    if (
        not FULL_GIT_OBJECT_ID.fullmatch(base_revision)
        or set(base_revision) == {"0"}
    ):
        raise snapshot_error(
            "invalid_review_evidence",
            "Git snapshot base revision is not a canonical commit ID",
            "review_target_base_revision",
        )
    manifest = bytearray(MANIFEST_PREFIX)
    manifest.extend(base_revision.encode("ascii"))
    manifest.append(0)
    for entry in validate_entries(
        entries,
        object_id_length=len(base_revision),
    ):
        manifest.extend(entry.mode)
        manifest.append(0)
        manifest.extend(entry.object_id)
        manifest.append(0)
        manifest.extend(entry.path)
        manifest.append(0)
    return "sha256:" + hashlib.sha256(manifest).hexdigest()


class _NulRecordStream:
    def __init__(
        self,
        *,
        record_byte_limit: int,
        overflow_error: Callable[[], Exception],
        consume_record: Callable[[bytes], None],
        incomplete_error: Callable[[], Exception],
    ) -> None:
        self._record_byte_limit = record_byte_limit
        self._overflow_error = overflow_error
        self._consume_record = consume_record
        self._incomplete_error = incomplete_error
        self._pending = bytearray()

    def consume(self, chunk: bytes) -> None:
        start = 0
        while True:
            end = chunk.find(b"\0", start)
            segment = chunk[start:] if end < 0 else chunk[start:end]
            if len(self._pending) > self._record_byte_limit - len(segment):
                raise self._overflow_error()
            self._pending.extend(segment)
            if end < 0:
                return
            record = bytes(self._pending)
            self._pending.clear()
            self._consume_record(record)
            start = end + 1

    def finish(self) -> None:
        if self._pending:
            raise self._incomplete_error()


def _stream_parse_error(code: str, field: str, message: str) -> GitSnapshotError:
    return snapshot_error(code, message, field)


def stream_index_fingerprint(
    repo: Path,
    base_revision: str,
    *,
    code: str = "invalid_review_evidence",
    field: str = "review_target_value",
    message: str = "Git index snapshot output is malformed",
    record_byte_limit: int = GIT_RECORD_BYTE_LIMIT,
    record_overflow_error: Callable[[], Exception] | None = None,
    consume_entry: Callable[[GitSnapshotEntry], None] | None = None,
) -> GitIndexObservation:
    """Hash one complete stage-0 index while retaining at most one record."""

    if (
        not FULL_GIT_OBJECT_ID.fullmatch(base_revision)
        or set(base_revision) == {"0"}
    ):
        raise snapshot_error(code, message, field)
    if (
        isinstance(record_byte_limit, bool)
        or not isinstance(record_byte_limit, int)
        or record_byte_limit <= 0
    ):
        raise ValueError("record_byte_limit must be a positive integer")

    fingerprint = hashlib.sha256()
    fingerprint.update(MANIFEST_PREFIX)
    fingerprint.update(base_revision.encode("ascii"))
    fingerprint.update(b"\0")
    entry_count = 0
    previous_path: bytes | None = None

    def on_record(record: bytes) -> None:
        nonlocal entry_count, previous_path
        entry = _parse_index_record(
            record,
            code=code,
            field=field,
            message=message,
        )
        if len(entry.object_id) != len(base_revision):
            raise snapshot_error(code, message, field)
        if previous_path is not None and previous_path >= entry.path:
            raise snapshot_error(code, message, field)
        previous_path = entry.path
        if consume_entry is not None:
            consume_entry(entry)
        fingerprint.update(entry.mode)
        fingerprint.update(b"\0")
        fingerprint.update(entry.object_id)
        fingerprint.update(b"\0")
        fingerprint.update(entry.path)
        fingerprint.update(b"\0")
        entry_count += 1

    overflow = record_overflow_error or (
        lambda: _stream_parse_error(code, field, message)
    )
    parser = _NulRecordStream(
        record_byte_limit=record_byte_limit,
        overflow_error=overflow,
        consume_record=on_record,
        incomplete_error=lambda: _stream_parse_error(code, field, message),
    )
    identity = run_git_stream(
        repo,
        [
            *_FS_MONITOR_DISABLED,
            "ls-files",
            "--cached",
            "--stage",
            "--sparse",
            "-z",
            "--",
        ],
        parser.consume,
        code=code,
        field=field,
        message=message,
    )
    parser.finish()
    return GitIndexObservation(
        identity=identity,
        fingerprint="sha256:" + fingerprint.hexdigest(),
        entry_count=entry_count,
    )


def stream_tree_entries(
    repo: Path,
    tree_id: str,
    *,
    object_id_length: int,
    code: str = "review_target_mismatch",
    field: str = "completion_revision",
    message: str = "completion commit tree output is malformed",
    record_byte_limit: int = GIT_RECORD_BYTE_LIMIT,
    record_overflow_error: Callable[[], Exception] | None = None,
    consume_entry: Callable[[GitSnapshotEntry], None] | None = None,
) -> GitTreeObservation:
    """Validate one recursive tree while retaining at most one record."""

    if object_id_length not in {40, 64}:
        raise snapshot_error(code, message, field)
    if (
        isinstance(record_byte_limit, bool)
        or not isinstance(record_byte_limit, int)
        or record_byte_limit <= 0
    ):
        raise ValueError("record_byte_limit must be a positive integer")
    entry_count = 0
    previous_path: bytes | None = None

    def on_record(record: bytes) -> None:
        nonlocal entry_count, previous_path
        entry = _parse_tree_record(
            record,
            code=code,
            field=field,
            message=message,
        )
        if len(entry.object_id) != object_id_length:
            raise snapshot_error(code, message, field)
        if previous_path is not None and previous_path >= entry.path:
            raise snapshot_error(code, message, field)
        previous_path = entry.path
        if consume_entry is not None:
            consume_entry(entry)
        entry_count += 1

    overflow = record_overflow_error or (
        lambda: _stream_parse_error(code, field, message)
    )
    parser = _NulRecordStream(
        record_byte_limit=record_byte_limit,
        overflow_error=overflow,
        consume_record=on_record,
        incomplete_error=lambda: _stream_parse_error(code, field, message),
    )
    identity = run_git_stream(
        repo,
        [
            *_FS_MONITOR_DISABLED,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            "--no-abbrev",
            tree_id,
            "--",
        ],
        parser.consume,
        code=code,
        field=field,
        message=message,
    )
    parser.finish()
    return GitTreeObservation(identity=identity, entry_count=entry_count)


def read_intent_to_add_views(repo: Path, base_revision: str) -> tuple[bytes, bytes]:
    common = [
        *_FS_MONITOR_DISABLED,
        "diff-index",
        "--cached",
        "--raw",
        "-z",
        "--no-abbrev",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
    ]
    visible = run_git_bytes(
        repo,
        [*common, "--ita-visible-in-index", base_revision, "--"],
    )
    invisible = run_git_bytes(
        repo,
        [*common, "--ita-invisible-in-index", base_revision, "--"],
    )
    return visible, invisible


def capture_git_snapshot(repo: Path) -> GitSnapshot:
    try:
        base_before = resolve_git_commit(repo, "HEAD")
    except CompletionEvidenceError as exc:
        raise snapshot_error(exc.code, exc.message, "review_target_base_revision") from exc
    visible_before, invisible_before = read_intent_to_add_views(repo, base_before)
    index_before = stream_index_fingerprint(repo, base_before)
    try:
        base_after = resolve_git_commit(repo, "HEAD")
    except CompletionEvidenceError as exc:
        raise snapshot_error(exc.code, exc.message, "review_target_base_revision") from exc
    visible_after, invisible_after = read_intent_to_add_views(repo, base_after)
    index_after = stream_index_fingerprint(repo, base_after)
    if (
        base_before != base_after
        or visible_before != visible_after
        or invisible_before != invisible_after
        or index_before != index_after
    ):
        raise snapshot_error(
            "invalid_review_evidence",
            "Git HEAD or index changed while the review snapshot was being captured",
            "review_target_value",
        )
    if visible_before != invisible_before:
        raise snapshot_error(
            "invalid_review_evidence",
            "Git snapshot does not support intent-to-add index entries",
            "review_target_value",
        )
    return GitSnapshot(
        base_revision=base_before,
        fingerprint=index_before.fingerprint,
        entry_count=index_before.entry_count,
        changed_paths=parse_raw_diff_paths(visible_before),
    )


def parse_commit_tree_and_parents(
    payload: bytes,
    *,
    code: str = "review_target_mismatch",
    field: str = "completion_revision",
    message: str = "completion commit topology is unsupported",
) -> tuple[str, list[str]]:
    header = payload.split(b"\n\n", 1)[0]
    tree_ids: list[str] = []
    parent_ids: list[str] = []
    try:
        for line in header.splitlines():
            if line.startswith(b"tree "):
                tree_ids.append(line[5:].decode("ascii", errors="strict"))
            elif line.startswith(b"parent "):
                parent_ids.append(line[7:].decode("ascii", errors="strict"))
    except UnicodeDecodeError as exc:
        raise snapshot_error(code, message, field) from exc
    if (
        len(tree_ids) != 1
        or not FULL_GIT_OBJECT_ID.fullmatch(tree_ids[0])
        or any(not FULL_GIT_OBJECT_ID.fullmatch(parent) for parent in parent_ids)
    ):
        raise snapshot_error(code, message, field)
    return tree_ids[0], parent_ids


def verify_git_snapshot_commit(
    repo: Path,
    revision: str,
    *,
    expected_base_revision: str,
    expected_fingerprint: str,
) -> GitSnapshot:
    if (
        not FULL_GIT_OBJECT_ID.fullmatch(expected_base_revision)
        or set(expected_base_revision) == {"0"}
        or not SNAPSHOT_FINGERPRINT.fullmatch(expected_fingerprint)
    ):
        raise snapshot_error(
            "review_target_mismatch",
            "stored Git snapshot target is invalid",
            "review_target_value",
        )
    try:
        canonical_commit = resolve_git_commit(repo, revision)
    except CompletionEvidenceError as exc:
        raise snapshot_error(exc.code, exc.message, "completion_revision") from exc
    commit_payload = run_git_bytes(
        repo,
        ["cat-file", "commit", canonical_commit],
        code="review_target_mismatch",
        field="completion_revision",
        message="completion commit could not be inspected",
    )
    tree_id, parent_ids = parse_commit_tree_and_parents(commit_payload)
    if len(parent_ids) != 1 or parent_ids[0] != expected_base_revision:
        raise snapshot_error(
            "review_target_mismatch",
            "completion commit must have exactly the reviewed base as its parent",
            "completion_revision",
        )
    tree_payload = run_git_bytes(
        repo,
        ["ls-tree", "-r", "-z", "--full-tree", "--no-abbrev", tree_id, "--"],
        code="review_target_mismatch",
        field="completion_revision",
        message="completion commit tree could not be inspected",
    )
    entries = parse_tree_entries(tree_payload)
    if any(len(entry.object_id) != len(parent_ids[0]) for entry in entries):
        raise snapshot_error(
            "review_target_mismatch",
            "completion commit tree uses an unexpected object ID format",
            "completion_revision",
        )
    try:
        fingerprint = manifest_fingerprint(parent_ids[0], entries)
    except GitSnapshotError as exc:
        raise snapshot_error(
            "review_target_mismatch",
            "completion commit tree cannot match the reviewed Git snapshot",
            "completion_revision",
        ) from exc
    if fingerprint != expected_fingerprint:
        raise snapshot_error(
            "review_target_mismatch",
            "completion commit tree does not match the reviewed Git snapshot",
            "completion_revision",
        )
    return GitSnapshot(
        base_revision=parent_ids[0],
        fingerprint=fingerprint,
        entry_count=len(entries),
    )
