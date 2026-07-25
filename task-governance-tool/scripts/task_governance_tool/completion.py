"""Deterministic completion-evidence validation and Git resolution."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WRITABLE_EVIDENCE_KINDS = (
    "git_commit",
    "external_revision",
    "commit_not_required",
)
FULL_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


@dataclass(frozen=True)
class CompletionEvidenceError(Exception):
    code: str
    message: str
    field: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class CompletionEvidenceUpdate:
    values: dict[str, Any]
    audit_marker: str


def evidence_error(code: str, message: str, field: str) -> CompletionEvidenceError:
    return CompletionEvidenceError(code=code, message=message, field=field)


def resolve_git_commit(repo: Path, revision: str) -> str:
    """Resolve one commit-ish without using a shell or changing repository state."""
    candidate = revision.strip()
    if not candidate or candidate.startswith("-"):
        raise evidence_error(
            "git_commit_not_found_or_ambiguous",
            "Git completion revision must name one existing, unambiguous commit",
            "completion_revision",
        )
    try:
        git_environment = os.environ.copy()
        git_environment["GIT_OPTIONAL_LOCKS"] = "0"
        git_environment["GIT_NO_LAZY_FETCH"] = "1"
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{candidate}^{{commit}}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=15,
            env=git_environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise evidence_error(
            "git_commit_not_found_or_ambiguous",
            "Git completion revision could not be verified as a commit",
            "completion_revision",
        ) from exc

    lines = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or len(lines) != 1 or not FULL_GIT_OBJECT_ID.fullmatch(lines[0]):
        raise evidence_error(
            "git_commit_not_found_or_ambiguous",
            "Git completion revision must name one existing, unambiguous commit",
            "completion_revision",
        )
    return lines[0]


def completion_evidence_values(
    *,
    repo: Path,
    kind: str,
    revision: str = "",
    reason: str = "",
    external_revision_approved: bool = False,
) -> CompletionEvidenceUpdate:
    """Build the complete schema-v4 evidence/legacy projection matrix."""
    normalized_revision = revision.strip()
    normalized_reason = reason.strip()
    if kind == "git_commit":
        if not normalized_revision:
            raise evidence_error(
                "git_commit_not_found_or_ambiguous",
                "Git completion revision must name one existing, unambiguous commit",
                "completion_revision",
            )
        if normalized_reason or external_revision_approved:
            raise evidence_error(
                "completion_evidence_conflict",
                "git_commit requires only a completion revision",
                "completion_evidence_kind",
            )
        canonical = resolve_git_commit(repo, normalized_revision)
        return CompletionEvidenceUpdate(
            values={
                "completion_evidence_kind": "git_commit",
                "completion_evidence_revision": canonical,
                "completion_evidence_reason": "",
                "external_revision_approved": 0,
                "completion_commit_required": 1,
                "completion_commit_hash": canonical,
            },
            audit_marker="Git completion commit verified",
        )

    if kind == "external_revision":
        if not external_revision_approved:
            raise evidence_error(
                "external_revision_approval_required",
                "external_revision requires --external-revision-approved",
                "external_revision_approved",
            )
        if not normalized_revision or not normalized_reason:
            raise evidence_error(
                "completion_evidence_conflict",
                "external_revision requires both revision and reason",
                "completion_evidence_kind",
            )
        return CompletionEvidenceUpdate(
            values={
                "completion_evidence_kind": "external_revision",
                "completion_evidence_revision": normalized_revision,
                "completion_evidence_reason": normalized_reason,
                "external_revision_approved": 1,
                "completion_commit_required": 1,
                "completion_commit_hash": normalized_revision,
            },
            audit_marker="external revision approved as durable source outside target Git history",
        )

    if kind == "commit_not_required":
        if normalized_revision or normalized_reason or external_revision_approved:
            raise evidence_error(
                "completion_evidence_conflict",
                "commit_not_required cannot include revision, reason, or external approval",
                "completion_evidence_kind",
            )
        return CompletionEvidenceUpdate(
            values={
                "completion_evidence_kind": "commit_not_required",
                "completion_evidence_revision": "",
                "completion_evidence_reason": "",
                "external_revision_approved": 0,
                "completion_commit_required": 0,
                "completion_commit_hash": "",
            },
            audit_marker="commit not required",
        )

    raise evidence_error(
        "completion_evidence_conflict",
        "completion evidence kind must be git_commit, external_revision, or commit_not_required",
        "completion_evidence_kind",
    )


def validate_evidence_matrix(task: dict[str, Any], *, allow_legacy: bool) -> None:
    kind = str(task.get("completion_evidence_kind", ""))
    revision = str(task.get("completion_evidence_revision", ""))
    reason = str(task.get("completion_evidence_reason", ""))
    approved = int(task.get("external_revision_approved", 0))
    required = int(task.get("completion_commit_required", 1))
    legacy_hash = str(task.get("completion_commit_hash", ""))

    valid = False
    if kind == "none":
        valid = not revision and not reason and approved == 0 and required == 1 and not legacy_hash
    elif kind == "git_commit":
        valid = (
            bool(FULL_GIT_OBJECT_ID.fullmatch(revision))
            and not reason
            and approved == 0
            and required == 1
            and legacy_hash == revision
        )
    elif kind == "external_revision":
        valid = bool(
            revision
            and revision == revision.strip()
            and reason
            and reason == reason.strip()
            and approved == 1
            and required == 1
            and legacy_hash == revision
        )
    elif kind == "commit_not_required":
        valid = not revision and not reason and approved == 0 and required == 0 and not legacy_hash
    elif kind == "legacy_unverified" and allow_legacy:
        valid = revision == legacy_hash and not reason and approved == 0

    if not valid:
        raise evidence_error(
            "completion_evidence_conflict",
            "completion evidence fields do not form a valid evidence record",
            "completion_evidence_kind",
        )
