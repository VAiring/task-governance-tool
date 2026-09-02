"""Canonical physical publication for the local verification Runner Plan.

This authoring-only boundary captures one expected physical source and later
confirms or replaces that exact source.  It does not select an action, touch
SQLite, launch a Runner, or publish anywhere except the fixed ignored Plan
path beneath the supplied physical package root.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from task_governance_tool.state_paths import (
    DirectoryIdentity,
    FileIdentity,
    StatePathError,
    ValidatedDirectory,
    ValidatedFile,
    create_exclusive_durable_file,
    create_physical_directory_exclusive,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    read_physical_file_bounded,
    require_contained,
    unlink_validated_file,
)
from task_governance_tool.verification_runner_plan import (
    PLAN_BLOB_UTF8_BYTE_LIMIT,
    PLAN_RELATIVE_PATH,
    PLAN_SOURCE_ERROR_MESSAGE,
    VerificationRunnerPlanError,
    decode_verification_runner_plan,
    encode_verification_runner_plan,
    verification_runner_plan_is_local_only,
)


RUNNER_PLAN_CHANGED_MESSAGE = (
    "Runner Plan changed before update; no Plan change was made"
)
RUNNER_PLAN_UPDATE_FAILED_MESSAGE = "Runner Plan update did not complete"
INVALID_ARGUMENT_MESSAGE = "arguments are invalid"

_SOURCE_STATES = frozenset({"absent_directory", "absent_file", "present"})
_TEMP_PREFIX = ".taskgov-runner-plan-"
_TEMP_SUFFIX = ".tmp"


@dataclass(frozen=True)
class RunnerPlanAuthoringSource:
    """Path-free physical source record used by compare-before-replace."""

    state: Literal["absent_directory", "absent_file", "present"]
    package_identity: DirectoryIdentity
    config_identity: DirectoryIdentity | None = None
    file_identity: FileIdentity | None = None
    raw_blob: bytes | None = field(default=None, repr=False)
    raw_digest: str | None = None

    def __post_init__(self) -> None:
        present = self.state == "present"
        valid = (
            type(self.state) is str
            and self.state in _SOURCE_STATES
            and type(self.package_identity) is DirectoryIdentity
            and (
                (
                    self.state == "absent_directory"
                    and self.config_identity is None
                    and self.file_identity is None
                    and self.raw_blob is None
                    and self.raw_digest is None
                )
                or (
                    self.state == "absent_file"
                    and type(self.config_identity) is DirectoryIdentity
                    and self.file_identity is None
                    and self.raw_blob is None
                    and self.raw_digest is None
                )
                or (
                    present
                    and type(self.config_identity) is DirectoryIdentity
                    and type(self.file_identity) is FileIdentity
                    and type(self.raw_blob) is bytes
                    and len(self.raw_blob) <= PLAN_BLOB_UTF8_BYTE_LIMIT
                    and self.file_identity.size == len(self.raw_blob)
                    and type(self.raw_digest) is str
                    and self.raw_digest == _raw_digest(self.raw_blob)
                )
            )
        )
        if not valid:
            raise _source_error()


class RunnerPlanConfirmationOnly:
    """Explicit marker for a source confirmation that must perform no write."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "RunnerPlanConfirmationOnly()"


CONFIRM_RUNNER_PLAN_SOURCE = RunnerPlanConfirmationOnly()


def _source_error() -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(
        code="plan_source_invalid",
        message=PLAN_SOURCE_ERROR_MESSAGE,
    )


def _invalid_argument() -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(
        code="invalid_argument",
        message=INVALID_ARGUMENT_MESSAGE,
    )


def _source_changed() -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(
        code="runner_plan_changed",
        message=RUNNER_PLAN_CHANGED_MESSAGE,
    )


def _update_failed() -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(
        code="runner_plan_update_failed",
        message=RUNNER_PLAN_UPDATE_FAILED_MESSAGE,
    )


def _raw_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _single_link_matches(path: Path, *, root: Path, expected: FileIdentity) -> bool:
    try:
        _path, observed = inspect_physical_file(
            path,
            root=root,
            max_bytes=PLAN_BLOB_UTF8_BYTE_LIMIT,
        )
        details = path.lstat()
    except (OSError, StatePathError):
        return False
    return (
        observed == expected
        and int(details.st_nlink) == 1
        and not stat.S_ISLNK(details.st_mode)
        and stat.S_ISREG(details.st_mode)
        and (
            int(details.st_dev),
            int(details.st_ino),
            int(details.st_size),
            int(details.st_mtime_ns),
        )
        == (
            expected.device,
            expected.inode,
            expected.size,
            expected.modified_ns,
        )
    )


def _validated_roots(
    repo: Path,
    physical_package_root: Path,
) -> tuple[ValidatedDirectory, ValidatedDirectory, Path, Path]:
    if (
        not isinstance(repo, Path)
        or not repo.is_absolute()
        or not isinstance(physical_package_root, Path)
        or not physical_package_root.is_absolute()
    ):
        raise _source_error()
    repository = inspect_physical_directory(repo)
    package = inspect_physical_directory(physical_package_root, root=repo)
    require_contained(physical_package_root, repo)
    config = physical_package_root / PLAN_RELATIVE_PATH.parent
    plan = physical_package_root / PLAN_RELATIVE_PATH
    require_contained(config, physical_package_root)
    require_contained(plan, physical_package_root)
    return repository, package, config, plan


def _roots_unchanged(
    repo: Path,
    physical_package_root: Path,
    *,
    repository: ValidatedDirectory,
    package: ValidatedDirectory,
) -> bool:
    try:
        return (
            inspect_physical_directory(repo) == repository
            and inspect_physical_directory(physical_package_root, root=repo)
            == package
        )
    except StatePathError:
        return False


def capture_runner_plan_authoring_source(
    repo: Path,
    physical_package_root: Path,
) -> RunnerPlanAuthoringSource:
    """Capture the exact local-only physical source for one future action."""

    try:
        repository, package, config, plan = _validated_roots(
            repo,
            physical_package_root,
        )
        if not path_lexically_exists(config):
            if (
                not verification_runner_plan_is_local_only(repo, plan)
                or path_lexically_exists(config)
                or not _roots_unchanged(
                    repo,
                    physical_package_root,
                    repository=repository,
                    package=package,
                )
                or not verification_runner_plan_is_local_only(repo, plan)
            ):
                raise _source_error()
            return RunnerPlanAuthoringSource(
                state="absent_directory",
                package_identity=package.identity,
            )

        config_before = inspect_physical_directory(
            config,
            root=physical_package_root,
        )
        if not path_lexically_exists(plan):
            if (
                not verification_runner_plan_is_local_only(repo, plan)
                or path_lexically_exists(plan)
                or inspect_physical_directory(
                    config,
                    root=physical_package_root,
                )
                != config_before
                or not _roots_unchanged(
                    repo,
                    physical_package_root,
                    repository=repository,
                    package=package,
                )
                or not verification_runner_plan_is_local_only(repo, plan)
            ):
                raise _source_error()
            return RunnerPlanAuthoringSource(
                state="absent_file",
                package_identity=package.identity,
                config_identity=config_before.identity,
            )

        if not verification_runner_plan_is_local_only(repo, plan):
            raise _source_error()
        _path, before = inspect_physical_file(
            plan,
            root=physical_package_root,
            max_bytes=PLAN_BLOB_UTF8_BYTE_LIMIT,
        )
        if not _single_link_matches(
            plan,
            root=physical_package_root,
            expected=before,
        ):
            raise _source_error()
        raw, validated = read_physical_file_bounded(
            plan,
            root=physical_package_root,
            max_bytes=PLAN_BLOB_UTF8_BYTE_LIMIT,
        )
        if (
            validated.identity != before
            or not _single_link_matches(
                plan,
                root=physical_package_root,
                expected=before,
            )
            or not verification_runner_plan_is_local_only(repo, plan)
            or inspect_physical_directory(
                config,
                root=physical_package_root,
            )
            != config_before
            or not _roots_unchanged(
                repo,
                physical_package_root,
                repository=repository,
                package=package,
            )
        ):
            raise _source_error()
        return RunnerPlanAuthoringSource(
            state="present",
            package_identity=package.identity,
            config_identity=config_before.identity,
            file_identity=before,
            raw_blob=raw,
            raw_digest=_raw_digest(raw),
        )
    except VerificationRunnerPlanError:
        raise
    except (OSError, StatePathError, RuntimeError, UnicodeError) as exc:
        raise _source_error() from exc


def _validate_expected_source(source: RunnerPlanAuthoringSource) -> None:
    if type(source) is not RunnerPlanAuthoringSource:
        raise _invalid_argument()
    try:
        rebuilt = RunnerPlanAuthoringSource(
            state=source.state,
            package_identity=source.package_identity,
            config_identity=source.config_identity,
            file_identity=source.file_identity,
            raw_blob=source.raw_blob,
            raw_digest=source.raw_digest,
        )
    except (AttributeError, VerificationRunnerPlanError):
        raise _invalid_argument() from None
    if rebuilt != source:
        raise _invalid_argument()


def _validate_candidate(candidate: bytes) -> None:
    if (
        type(candidate) is not bytes
        or not candidate
        or len(candidate) > PLAN_BLOB_UTF8_BYTE_LIMIT
    ):
        raise _invalid_argument()
    try:
        decoded = decode_verification_runner_plan(candidate)
        if encode_verification_runner_plan(decoded) != candidate:
            raise _invalid_argument()
    except VerificationRunnerPlanError:
        raise _invalid_argument() from None


def _confirm_expected_source(
    repo: Path,
    physical_package_root: Path,
    expected: RunnerPlanAuthoringSource,
) -> None:
    try:
        observed = capture_runner_plan_authoring_source(
            repo,
            physical_package_root,
        )
    except VerificationRunnerPlanError:
        raise _update_failed() from None
    if observed != expected:
        raise _source_changed()


def _classify_operation_failure(
    repo: Path,
    physical_package_root: Path,
    expected: RunnerPlanAuthoringSource,
) -> None:
    """Prefer a safely observed source drift over an opaque update failure."""

    try:
        _confirm_expected_source(repo, physical_package_root, expected)
    except VerificationRunnerPlanError as exc:
        if exc.code == "runner_plan_changed":
            raise
    raise _update_failed() from None


def _require_exact_entries(
    directory: Path,
    *,
    root: Path,
    expected_identity: DirectoryIdentity,
    names: frozenset[str],
) -> None:
    before = inspect_physical_directory(directory, root=root)
    if before.identity != expected_identity:
        raise StatePathError()
    observed: set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name not in names or entry.name in observed:
                    raise StatePathError()
                observed.add(entry.name)
    except OSError as exc:
        raise StatePathError() from exc
    after = inspect_physical_directory(directory, root=root)
    if observed != set(names) or after != before:
        raise StatePathError()


def _create_temporary(
    config: Path,
    candidate: bytes,
    *,
    root: Path,
) -> ValidatedFile:
    temporary = config / f"{_TEMP_PREFIX}{secrets.token_hex(8)}{_TEMP_SUFFIX}"
    return create_exclusive_durable_file(
        temporary,
        candidate,
        root=root,
        max_bytes=PLAN_BLOB_UTF8_BYTE_LIMIT,
    )


def _confirm_initial_directory(
    repo: Path,
    physical_package_root: Path,
    expected: RunnerPlanAuthoringSource,
    created: ValidatedDirectory,
    temporary: ValidatedFile,
) -> None:
    config = physical_package_root / PLAN_RELATIVE_PATH.parent
    plan = physical_package_root / PLAN_RELATIVE_PATH
    try:
        package = inspect_physical_directory(physical_package_root, root=repo)
        if (
            package.identity != expected.package_identity
            or inspect_physical_directory(config, root=physical_package_root)
            != created
            or path_lexically_exists(plan)
        ):
            raise _source_changed()
        if not verification_runner_plan_is_local_only(
            repo,
            plan,
        ) or not _single_link_matches(
            temporary.path,
            root=physical_package_root,
            expected=temporary.identity,
        ):
            raise _update_failed()
        _require_exact_entries(
            config,
            root=physical_package_root,
            expected_identity=created.identity,
            names=frozenset({temporary.path.name}),
        )
        package_after = inspect_physical_directory(physical_package_root, root=repo)
        if (
            package_after.identity != expected.package_identity
            or inspect_physical_directory(config, root=physical_package_root)
            != created
            or path_lexically_exists(plan)
        ):
            raise _source_changed()
        if not verification_runner_plan_is_local_only(
            repo,
            plan,
        ) or not _single_link_matches(
            temporary.path,
            root=physical_package_root,
            expected=temporary.identity,
        ):
            raise _update_failed()
    except VerificationRunnerPlanError:
        raise
    except (OSError, StatePathError):
        raise _update_failed() from None


def publish_verification_runner_plan(
    repo: Path,
    physical_package_root: Path,
    expected_source: RunnerPlanAuthoringSource,
    candidate_or_confirmation: bytes | RunnerPlanConfirmationOnly,
) -> None:
    """Confirm one expected source or atomically publish one canonical candidate."""

    _validate_expected_source(expected_source)
    _confirm_expected_source(repo, physical_package_root, expected_source)
    if type(candidate_or_confirmation) is RunnerPlanConfirmationOnly:
        return
    _validate_candidate(candidate_or_confirmation)

    config = physical_package_root / PLAN_RELATIVE_PATH.parent
    plan = physical_package_root / PLAN_RELATIVE_PATH
    created: ValidatedDirectory | None = None
    temporary: ValidatedFile | None = None
    try:
        if expected_source.state == "absent_directory":
            if not verification_runner_plan_is_local_only(repo, plan):
                raise _update_failed()
            try:
                created = create_physical_directory_exclusive(
                    config,
                    root=physical_package_root,
                )
            except StatePathError:
                _classify_operation_failure(
                    repo,
                    physical_package_root,
                    expected_source,
                )
            if (
                inspect_physical_directory(physical_package_root, root=repo).identity
                != expected_source.package_identity
            ):
                raise _source_changed()
            _require_exact_entries(
                config,
                root=physical_package_root,
                expected_identity=created.identity,
                names=frozenset(),
            )

        try:
            temporary = _create_temporary(
                config,
                candidate_or_confirmation,
                root=physical_package_root,
            )
        except StatePathError:
            if created is None:
                _classify_operation_failure(
                    repo,
                    physical_package_root,
                    expected_source,
                )
            raise _update_failed() from None
        if expected_source.state == "absent_directory":
            if created is None:
                raise _update_failed()
            _confirm_initial_directory(
                repo,
                physical_package_root,
                expected_source,
                created,
                temporary,
            )
        else:
            _confirm_expected_source(repo, physical_package_root, expected_source)
            if not _single_link_matches(
                temporary.path,
                root=physical_package_root,
                expected=temporary.identity,
            ):
                raise _update_failed()

        try:
            os.replace(temporary.path, plan)
        except OSError:
            if created is None:
                _classify_operation_failure(
                    repo,
                    physical_package_root,
                    expected_source,
                )
            raise _update_failed() from None
        temporary = None
    except VerificationRunnerPlanError:
        if temporary is not None:
            try:
                unlink_validated_file(temporary, root=physical_package_root)
            except StatePathError:
                raise _update_failed() from None
        raise
    except (OSError, StatePathError, RuntimeError, UnicodeError):
        if temporary is not None:
            try:
                unlink_validated_file(temporary, root=physical_package_root)
            except StatePathError:
                raise _update_failed() from None
        raise _update_failed() from None
