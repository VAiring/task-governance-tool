"""Bounded, read-only review packet preparation."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from typing import Any

from task_governance_tool.completion import (
    CompletionEvidenceError,
    FULL_GIT_OBJECT_ID,
    resolve_git_commit,
)
from task_governance_tool.contracts import read_current_contract
from task_governance_tool.git_snapshot import (
    GitSnapshotError,
    capture_git_snapshot,
    parse_commit_tree_and_parents,
    run_git_bytes,
    split_nul_records,
)
from task_governance_tool.reviews import (
    DIFF_FINGERPRINT,
    REVIEW_TARGET_KINDS,
    ReviewEvidenceError,
    validate_stored_review_target,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    StorageError,
    connect_initialized_readonly,
    current_schema_version,
    stored_task_verification_limit,
)
from task_governance_tool.tasks import (
    STATUSES,
    TEXT_LIMITS,
    TaskRepositoryError,
    TaskValidationError,
    read_internal_task,
    reject_private_or_raw_content,
    validate_choice,
    validate_review_tier,
    validate_sqlite_int64,
    validate_task_id,
    validate_text,
)


REVIEW_PACKET_MAX_BYTES = 32_768
REVIEW_PACKET_MAX_PATHS = 100
REVIEW_PACKET_MAX_PATH_BYTES = 240
REVIEW_PACKET_MAX_AGGREGATE_PATH_BYTES = 16_384
REVIEW_PACKET_MAX_GIT_SUBPROCESSES = 10

REVIEW_FOCUS = (
    "Contract compliance",
    "state-transition and completion-gate integrity",
    "privacy and target-project safety",
    "verification sufficiency and regression risk",
)
TARGET_INSPECTION_FOCUS = {
    "git_snapshot": (
        "Exact target: inspect only the matching stage-0 index against "
        "review_target.base_revision, using the cached diff and index blobs; "
        "exclude unstaged and untracked worktree content"
    ),
    "git_commit": (
        "Exact target: inspect review_target.value as the canonical commit "
        "against its first parent, or the empty tree for a root commit, and "
        "read that commit's tree and blobs rather than ambient HEAD or "
        "worktree content"
    ),
    "diff_fingerprint": (
        "Exact target: do not return PASS unless the orchestrator provides "
        "the exact review material plus evidence binding it to "
        "review_target.value; the fingerprint alone cannot retrieve content"
    ),
    "external_revision": (
        "Exact target: do not return PASS unless exact external material is "
        "bound to review_target.value; taskgov does not retrieve external "
        "artifacts"
    ),
}
REQUIRED_OUTPUT = (
    "verdict PASS or CHANGES_REQUESTED",
    "severity-ordered findings with exact file/line",
    "remaining risks",
    "recommended changes",
    (
        "review provenance: reviewer class, model and Skill declaration "
        "states, context relation, profiles, lenses, and methods"
    ),
)

MISSING_TARGET_MESSAGE = (
    "review target is required before preparing a review packet"
)
STALE_PACKET_MESSAGE = "review context changed while preparing the packet"
UNSAFE_PATH_MESSAGE = "review packet contains an unsafe project path"
OVERSIZED_PACKET_MESSAGE = "review packet exceeds the supported size"
WINDOWS_DRIVE_PATH = re.compile(r"[A-Za-z]:")


@dataclass(frozen=True)
class ReviewPacketError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ReviewPacketBasis:
    task: dict[str, Any] = field(repr=False)
    contract: dict[str, Any] = field(repr=False)
    review_target: dict[str, Any] = field(repr=False)
    stability_token: tuple[Any, ...] = field(repr=False)


def packet_error(code: str, message: str) -> ReviewPacketError:
    return ReviewPacketError(code=code, message=message)


def _stored_context_error(exc: Exception) -> ReviewPacketError:
    return packet_error("internal_error", "stored review context is invalid")


def _read_basis(
    target: DatabaseTarget,
    task_id: str,
    *,
    revalidation: bool,
    connection: sqlite3.Connection | None = None,
) -> ReviewPacketBasis:
    try:
        manager = (
            nullcontext(connection)
            if connection is not None
            else closing(connect_initialized_readonly(target))
        )
        with manager as active_connection:
            stored = read_internal_task(
                active_connection,
                target.project.project_id,
                task_id,
            )
            if stored is None:
                if revalidation:
                    raise packet_error(
                        "review_packet_stale",
                        STALE_PACKET_MESSAGE,
                    )
                raise TaskRepositoryError("not_found", "task was not found")

            target_kind = str(stored["review_target_kind"])
            if not target_kind:
                if revalidation:
                    raise packet_error(
                        "review_packet_stale",
                        STALE_PACKET_MESSAGE,
                    )
                raise packet_error(
                    "review_target_missing",
                    MISSING_TARGET_MESSAGE,
                )

            task = {
                "task_id": validate_task_id(stored["task_id"]),
                "title": validate_text(
                    "title",
                    stored["title"],
                    required=True,
                    limit=TEXT_LIMITS["title"],
                ),
                "status": validate_choice(
                    "status",
                    stored["status"],
                    STATUSES,
                    "invalid_status",
                ),
                "verification": validate_text(
                    "verification",
                    stored["verification"],
                    limit=stored_task_verification_limit(
                        current_schema_version(active_connection)
                    ),
                ),
                "review_tier": validate_review_tier(
                    stored["review_tier"],
                ),
            }
            if (
                str(stored["project_id"]) != target.project.project_id
                or task["task_id"] != task_id
            ):
                raise ValueError("stored task identity mismatch")

            current_contract = read_current_contract(
                active_connection,
                project_id=target.project.project_id,
                task_id=task_id,
                current_revision=stored["current_contract_revision"],
            )
            contract = {
                "revision": current_contract["revision"],
                "scope": current_contract["scope"],
                "acceptance": current_contract["acceptance"],
                "constraints": current_contract["constraints"],
            }

            normalized_kind = validate_choice(
                "review_target_kind",
                target_kind,
                REVIEW_TARGET_KINDS,
                "invalid_review_evidence",
            )
            target_value = validate_text(
                "review_target_value",
                stored["review_target_value"],
                required=True,
                limit=TEXT_LIMITS["review_target_value"],
            )
            base_revision = validate_text(
                "review_target_base_revision",
                stored["review_target_base_revision"],
                limit=128,
            )
            generation = validate_sqlite_int64(
                stored["review_target_generation"],
                field="review_target_generation",
            )
            if generation <= 0:
                raise ValueError("review target generation must be positive")

            validate_stored_review_target(stored)
            if normalized_kind == "git_commit":
                if (
                    FULL_GIT_OBJECT_ID.fullmatch(target_value) is None
                    or set(target_value) == {"0"}
                ):
                    raise ValueError("stored Git commit target is invalid")
            elif (
                normalized_kind == "diff_fingerprint"
                and DIFF_FINGERPRINT.fullmatch(target_value) is None
            ):
                raise ValueError("stored diff fingerprint target is invalid")

            review_target = {
                "kind": normalized_kind,
                "value": target_value,
                "base_revision": base_revision,
                "generation": generation,
            }
            stability_token = (
                target.project.project_id,
                task_id,
                contract["revision"],
                review_target["kind"],
                review_target["value"],
                review_target["base_revision"],
                review_target["generation"],
            )
            return ReviewPacketBasis(
                task=task,
                contract=contract,
                review_target=review_target,
                stability_token=stability_token,
            )
    except ReviewPacketError:
        raise
    except StorageError as exc:
        if revalidation and exc.code == "project_mismatch":
            raise packet_error(
                "review_packet_stale",
                STALE_PACKET_MESSAGE,
            ) from exc
        raise
    except TaskRepositoryError as exc:
        if revalidation:
            raise packet_error(
                "review_packet_stale",
                STALE_PACKET_MESSAGE,
            ) from exc
        raise
    except (
        ReviewEvidenceError,
        TaskValidationError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        if revalidation:
            raise packet_error(
                "review_packet_stale",
                STALE_PACKET_MESSAGE,
            ) from exc
        raise _stored_context_error(exc) from exc


def _observe_git_snapshot(
    target: DatabaseTarget,
    review_target: dict[str, Any],
) -> tuple[bytes, ...]:
    try:
        snapshot = capture_git_snapshot(target.project.canonical_repo)
    except GitSnapshotError as exc:
        raise packet_error(exc.code, exc.message) from exc
    if (
        snapshot.base_revision != review_target["base_revision"]
        or snapshot.fingerprint != review_target["value"]
    ):
        raise packet_error(
            "review_target_mismatch",
            "current Git snapshot does not match the stored review target",
        )
    return snapshot.changed_paths


def _observe_git_commit(
    target: DatabaseTarget,
    review_target: dict[str, Any],
) -> tuple[bytes, ...]:
    try:
        canonical_commit = resolve_git_commit(
            target.project.canonical_repo,
            review_target["value"],
        )
    except CompletionEvidenceError as exc:
        raise packet_error(
            exc.code,
            "Git review target could not be resolved as an existing commit",
        ) from exc
    if canonical_commit != review_target["value"]:
        raise packet_error(
            "invalid_review_evidence",
            "stored Git review target is not canonical",
        )

    try:
        commit_payload = run_git_bytes(
            target.project.canonical_repo,
            ["cat-file", "commit", canonical_commit],
            code="invalid_review_evidence",
            field="review_target_value",
            message="Git review target could not be inspected",
        )
        _, parents = parse_commit_tree_and_parents(
            commit_payload,
            code="invalid_review_evidence",
            field="review_target_value",
            message="Git review target topology is unsupported",
        )
        arguments = [
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            "--no-renames",
            "--no-ext-diff",
        ]
        if parents:
            arguments.extend([parents[0], canonical_commit, "--"])
        else:
            arguments.extend(["--root", canonical_commit, "--"])
        payload = run_git_bytes(
            target.project.canonical_repo,
            arguments,
            code="invalid_review_evidence",
            field="review_target_value",
            message="Git review target changes could not be inspected",
        )
        return tuple(
            split_nul_records(
                payload,
                code="invalid_review_evidence",
                field="review_target_value",
                message="Git review target change output is malformed",
            )
        )
    except GitSnapshotError as exc:
        raise packet_error(exc.code, exc.message) from exc


def _observe_target(
    target: DatabaseTarget,
    review_target: dict[str, Any],
) -> tuple[bool, tuple[bytes, ...]]:
    kind = review_target["kind"]
    if kind == "git_snapshot":
        return True, _observe_git_snapshot(target, review_target)
    if kind == "git_commit":
        return True, _observe_git_commit(target, review_target)
    return False, ()


def _decode_safe_project_path(raw_path: bytes) -> str:
    try:
        path = raw_path.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise packet_error(
            "review_packet_path_unsafe",
            UNSAFE_PATH_MESSAGE,
        ) from exc
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or WINDOWS_DRIVE_PATH.match(path) is not None
        or any(part in {"", ".", ".."} for part in parts)
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in path
        )
    ):
        raise packet_error(
            "review_packet_path_unsafe",
            UNSAFE_PATH_MESSAGE,
        )
    try:
        reject_private_or_raw_content("review_packet_path", path)
    except TaskValidationError as exc:
        raise packet_error(
            "review_packet_path_unsafe",
            UNSAFE_PATH_MESSAGE,
        ) from exc
    return path


def project_changed_paths(
    raw_paths: tuple[bytes, ...] | list[bytes],
) -> tuple[list[str], int, bool]:
    ordered = sorted(raw_paths)
    if any(left == right for left, right in zip(ordered, ordered[1:])):
        raise packet_error(
            "invalid_review_evidence",
            "Git review target contains duplicate changed paths",
        )

    decoded = [
        (raw_path, _decode_safe_project_path(raw_path))
        for raw_path in ordered
    ]
    retained: list[str] = []
    retained_bytes = 0
    for raw_path, path in decoded:
        if (
            len(retained) >= REVIEW_PACKET_MAX_PATHS
            or len(raw_path) > REVIEW_PACKET_MAX_PATH_BYTES
            or (
                retained_bytes + len(raw_path)
                > REVIEW_PACKET_MAX_AGGREGATE_PATH_BYTES
            )
        ):
            continue
        retained.append(path)
        retained_bytes += len(raw_path)
    total = len(decoded)
    return retained, total, len(retained) != total


def prepare_review_packet(
    target: DatabaseTarget,
    task_id: Any,
    *,
    initial_connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    normalized_task_id = validate_task_id(task_id)
    basis = _read_basis(
        target,
        normalized_task_id,
        revalidation=False,
        connection=initial_connection,
    )
    if initial_connection is not None:
        initial_connection.close()

    observation_error: ReviewPacketError | None = None
    changed_paths_available = False
    raw_paths: tuple[bytes, ...] = ()
    try:
        changed_paths_available, raw_paths = _observe_target(
            target,
            basis.review_target,
        )
    except ReviewPacketError as exc:
        observation_error = exc

    current = _read_basis(
        target,
        normalized_task_id,
        revalidation=True,
    )
    if current.stability_token != basis.stability_token:
        raise packet_error(
            "review_packet_stale",
            STALE_PACKET_MESSAGE,
        )
    if observation_error is not None:
        raise observation_error

    changed_paths, changed_paths_total, changed_paths_truncated = (
        project_changed_paths(raw_paths)
        if changed_paths_available
        else ([], 0, False)
    )
    receipt_command = (
        f"taskgov review receipt add {normalized_task_id} "
        "--reviewer <reviewer-key> --kind independent "
        "--verdict <pass|changes_requested> "
        "--summary <sanitized-summary> "
        "--reviewer-class <human|llm|deterministic_tool|hybrid|unknown> "
        "--model-state <declared|not_applicable|unknown> "
        "--skill-state <declared|not_applicable|not_used|unknown> "
        "--context-relation <same_context|forked_context|fresh_context|"
        "external_context|not_applicable|unknown> "
        "[--declared-model-id <id>] [--declared-skill-id <id> "
        "--declared-skill-version <version>] "
        "[--review-profile <profile>] [--review-lens <lens>] "
        "[--review-method <method>] --json"
    )
    return {
        "task": basis.task,
        "contract": basis.contract,
        "review_target": basis.review_target,
        "changed_paths_available": changed_paths_available,
        "changed_paths": changed_paths,
        "changed_paths_total": changed_paths_total,
        "changed_paths_truncated": changed_paths_truncated,
        "review_focus": [
            *REVIEW_FOCUS,
            TARGET_INSPECTION_FOCUS[basis.review_target["kind"]],
        ],
        "required_output": list(REQUIRED_OUTPUT),
        "receipt_command": receipt_command,
    }


def _quoted(value: Any) -> str:
    return (
        json.dumps(str(value), ensure_ascii=False)
        .replace("\u0085", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def format_review_packet_text(data: dict[str, Any]) -> str:
    task = data["task"]
    contract = data["contract"]
    target = data["review_target"]
    if data["changed_paths_available"]:
        changed_path_summary = (
            f"{len(data['changed_paths'])}/{data['changed_paths_total']}"
            + (" (truncated)" if data["changed_paths_truncated"] else "")
        )
        changed_path_lines = [
            f"- {path}" for path in data["changed_paths"]
        ]
    else:
        changed_path_summary = "unavailable"
        changed_path_lines = []

    lines = [
        (
            f"Task: {task['task_id']} | {_quoted(task['title'])} "
            f"| review_tier={task['review_tier']}"
        ),
        f"Status: {task['status']}",
        f"Verification: {_quoted(task['verification'])}",
        f"Contract revision: {contract['revision']}",
        f"Scope: {_quoted(contract['scope'])}",
        f"Acceptance: {_quoted(contract['acceptance'])}",
        f"Constraints: {_quoted(contract['constraints'])}",
        (
            f"Review target: kind={target['kind']} "
            f"value={_quoted(target['value'])} "
            f"base_revision={_quoted(target['base_revision'])} "
            f"generation={target['generation']}"
        ),
        f"Changed paths: {changed_path_summary}",
        *changed_path_lines,
        "Review focus:",
        *(f"- {item}" for item in data["review_focus"]),
        "Required output:",
        *(f"- {item}" for item in data["required_output"]),
        f"Receipt command: {data['receipt_command']}",
    ]
    return "\n".join(lines)
