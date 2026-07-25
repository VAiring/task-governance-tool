"""Canonical, read-only Git snapshot capture and completion comparison."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
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


def snapshot_error(code: str, message: str, field: str) -> GitSnapshotError:
    return GitSnapshotError(code=code, message=message, field=field)


def run_git_bytes(
    repo: Path,
    arguments: list[str],
    *,
    code: str = "invalid_review_evidence",
    field: str = "review_target_value",
    message: str = "Git snapshot data could not be read safely",
) -> bytes:
    try:
        result = subprocess.run(
            [*safe_git_command(repo), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
            env=safe_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise snapshot_error(code, message, field) from exc
    if result.returncode != 0:
        raise snapshot_error(code, message, field)
    return result.stdout


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


def parse_index_entries(payload: bytes) -> list[GitSnapshotEntry]:
    entries: list[GitSnapshotEntry] = []
    for record in split_nul_records(
        payload,
        code="invalid_review_evidence",
        field="review_target_value",
        message="Git index snapshot output is malformed",
    ):
        try:
            metadata, path = record.split(b"\t", 1)
        except ValueError as exc:
            raise snapshot_error(
                "invalid_review_evidence",
                "Git index snapshot output is malformed",
                "review_target_value",
            ) from exc
        match = INDEX_HEADER.fullmatch(metadata)
        if match is None:
            raise snapshot_error(
                "invalid_review_evidence",
                "Git index snapshot output is malformed",
                "review_target_value",
            )
        mode = match.group("mode")
        object_id = match.group("object_id")
        stage = match.group("stage")
        if stage != b"0":
            raise snapshot_error(
                "invalid_review_evidence",
                "Git snapshot requires a fully merged stage-0 index",
                "review_target_value",
            )
        if mode in SPARSE_DIRECTORY_MODES:
            raise snapshot_error(
                "invalid_review_evidence",
                "Git snapshot does not support sparse-directory index entries",
                "review_target_value",
            )
        if mode not in INDEX_LEAF_MODES:
            raise snapshot_error(
                "invalid_review_evidence",
                "Git snapshot contains an unsupported index mode",
                "review_target_value",
            )
        entries.append(
            GitSnapshotEntry(
                mode=mode,
                object_id=validate_object_id(
                    object_id,
                    code="invalid_review_evidence",
                    field="review_target_value",
                    message="Git snapshot contains an unsupported index object",
                ),
                path=path,
            )
        )
    return validate_entries(entries)


def parse_tree_entries(payload: bytes) -> list[GitSnapshotEntry]:
    entries: list[GitSnapshotEntry] = []
    for record in split_nul_records(
        payload,
        code="review_target_mismatch",
        field="completion_revision",
        message="completion commit tree output is malformed",
    ):
        try:
            metadata, path = record.split(b"\t", 1)
        except ValueError as exc:
            raise snapshot_error(
                "review_target_mismatch",
                "completion commit tree output is malformed",
                "completion_revision",
            ) from exc
        match = TREE_HEADER.fullmatch(metadata)
        if match is None:
            raise snapshot_error(
                "review_target_mismatch",
                "completion commit tree output is malformed",
                "completion_revision",
            )
        mode = match.group("mode")
        object_type = match.group("object_type")
        object_id = match.group("object_id")
        expected_type = b"commit" if mode == b"160000" else b"blob"
        if mode not in INDEX_LEAF_MODES or object_type != expected_type:
            raise snapshot_error(
                "review_target_mismatch",
                "completion commit tree contains an unsupported entry",
                "completion_revision",
            )
        entries.append(
            GitSnapshotEntry(
                mode=mode,
                object_id=validate_object_id(
                    object_id,
                    code="review_target_mismatch",
                    field="completion_revision",
                    message="completion commit tree contains an unsupported object",
                ),
                path=path,
            )
        )
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


def read_intent_to_add_views(repo: Path, base_revision: str) -> tuple[bytes, bytes]:
    common = [
        "diff-index",
        "--cached",
        "--raw",
        "-z",
        "--no-abbrev",
        "--no-renames",
        "--no-ext-diff",
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
    payload_before = run_git_bytes(
        repo,
        ["ls-files", "--cached", "--stage", "--sparse", "-z", "--"],
    )
    try:
        base_after = resolve_git_commit(repo, "HEAD")
    except CompletionEvidenceError as exc:
        raise snapshot_error(exc.code, exc.message, "review_target_base_revision") from exc
    visible_after, invisible_after = read_intent_to_add_views(repo, base_after)
    payload_after = run_git_bytes(
        repo,
        ["ls-files", "--cached", "--stage", "--sparse", "-z", "--"],
    )
    if (
        base_before != base_after
        or visible_before != visible_after
        or invisible_before != invisible_after
        or payload_before != payload_after
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
    entries = parse_index_entries(payload_before)
    return GitSnapshot(
        base_revision=base_before,
        fingerprint=manifest_fingerprint(base_before, entries),
        entry_count=len(entries),
    )


def parse_commit_tree_and_parents(payload: bytes) -> tuple[str, list[str]]:
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
        raise snapshot_error(
            "review_target_mismatch",
            "completion commit topology is unsupported",
            "completion_revision",
        ) from exc
    if (
        len(tree_ids) != 1
        or not FULL_GIT_OBJECT_ID.fullmatch(tree_ids[0])
        or any(not FULL_GIT_OBJECT_ID.fullmatch(parent) for parent in parent_ids)
    ):
        raise snapshot_error(
            "review_target_mismatch",
            "completion commit topology is unsupported",
            "completion_revision",
        )
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
