"""Private fixed-state ownership for bounded verification Runner attempts.

This module owns only deterministic paths, the package-state Runner lock, and
explicit no-follow directory cleanup.  It owns no SQLite, process, native,
Evidence, or business-gate responsibility.
"""

from __future__ import annotations

import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

from task_governance_tool.artifact_lock import (
    ArtifactLockError,
    zero_wait_artifact_lock,
)
from task_governance_tool.state_paths import (
    DirectoryIdentity,
    FileIdentity,
    StatePathError,
    ValidatedDirectory,
    create_physical_directory_exclusive,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    remove_explicit_files_and_directories,
    rename_no_replace,
    rmdir_validated_directory,
    require_contained,
)


RUNNER_FAILURE_MESSAGE = "verification runner state could not be changed safely"
_ATTEMPT_ID = re.compile(r"tg_verification_runner_attempt_[0-9a-f]{16}\Z")
_EXPECTED_ATTEMPT_CHILDREN = frozenset({"target", "scratch"})
_EXPECTED_SCRATCH_CHILDREN = ("tmp", "home", "local", "roaming")
_MAX_ATTEMPT_FILES = 30_000
_MAX_ATTEMPT_DIRECTORIES = 30_100
_MAX_ATTEMPT_DEPTH = 64
_MAX_ATTEMPT_TRAVERSAL_ENTRIES = _MAX_ATTEMPT_FILES + _MAX_ATTEMPT_DIRECTORIES
_MAX_LAYOUT_ATTEMPT_ENTRIES = 1
_MAX_LAYOUT_TRAVERSAL_ENTRIES = 3 + _MAX_LAYOUT_ATTEMPT_ENTRIES
_TRAVERSAL_TIMEOUT_SECONDS = 30.0


@dataclass
class VerificationRunnerLifecycleError(Exception):
    code: str
    message: str = RUNNER_FAILURE_MESSAGE

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class VerificationRunnerStatePaths:
    """Closed caller-owned paths for the private Runner lifecycle tree."""

    root: Path = field(repr=False)
    lock: Path = field(repr=False)
    attempts: Path = field(repr=False)
    quarantine: Path = field(repr=False)

    def __post_init__(self) -> None:
        values = (self.root, self.lock, self.attempts, self.quarantine)
        if any(not isinstance(value, Path) or not value.is_absolute() for value in values):
            raise _failure()
        root_key = os.path.normcase(os.path.normpath(str(self.root)))
        expected = (
            (self.lock, "taskgov-verification-runner.lock"),
            (self.attempts, "attempts"),
            (self.quarantine, "quarantine"),
        )
        for candidate, name in expected:
            if (
                candidate.name != name
                or os.path.normcase(os.path.normpath(str(candidate.parent)))
                != root_key
            ):
                raise _failure()


def verification_runner_state_paths(root: Path) -> VerificationRunnerStatePaths:
    """Derive the one fixed internal lifecycle layout from its owned root."""

    if not isinstance(root, Path) or not root.is_absolute():
        raise _failure()
    return VerificationRunnerStatePaths(
        root=root,
        lock=root / "taskgov-verification-runner.lock",
        attempts=root / "attempts",
        quarantine=root / "quarantine",
    )


@dataclass
class _TraversalBudget:
    maximum_entries: int
    deadline: float
    observed_entries: int = 0

    def check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise _failure()

    def observe(self) -> None:
        self.check_deadline()
        if self.observed_entries >= self.maximum_entries:
            raise _failure()
        self.observed_entries += 1


@dataclass(frozen=True)
class RunnerLayoutInventory:
    attempt_ids: tuple[str, ...]
    quarantine_ids: tuple[str, ...]


@dataclass(frozen=True)
class RunnerAttemptPaths:
    attempt_id: str
    root: Path = field(repr=False)
    target: Path = field(repr=False)
    scratch: Path = field(repr=False)
    quarantine: Path = field(repr=False)


def _failure(*, code: str = "runner_state_invalid") -> VerificationRunnerLifecycleError:
    return VerificationRunnerLifecycleError(code=code)


@dataclass(frozen=True)
class RunnerPrivateTreeResultV1:
    attempt_id: str
    state: str

    def __post_init__(self) -> None:
        if (
            type(self.attempt_id) is not str
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
            or self.state not in {"absent", "uncertain"}
        ):
            raise _failure()


@dataclass(frozen=True)
class _RunnerOwnerDirectoryIdentities:
    root: DirectoryIdentity
    attempts: DirectoryIdentity
    quarantine: DirectoryIdentity


@dataclass(frozen=True)
class _OwnedDeletionFile:
    path: Path = field(repr=False)
    identity: FileIdentity


def _attempt_id(value: object) -> str:
    if not isinstance(value, str) or _ATTEMPT_ID.fullmatch(value) is None:
        raise _failure()
    return value


def _is_reparse(details: object) -> bool:
    mode = getattr(details, "st_mode", 0)
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _ensure_directory(path: Path, *, owner_root: Path) -> None:
    if path_lexically_exists(path):
        inspect_physical_directory(path, root=owner_root)
        return
    create_physical_directory_exclusive(path, root=owner_root)


def _bounded_sorted_children(
    directory: Path,
    *,
    budget: _TraversalBudget,
    maximum_items: int | None = None,
    reverse: bool = False,
) -> list[Path]:
    """Collect only a bounded directory fan-out, then sort that bounded set."""

    entries: list[Path] = []
    try:
        with os.scandir(directory) as iterator:
            while True:
                budget.check_deadline()
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                budget.observe()
                if maximum_items is not None and len(entries) >= maximum_items:
                    raise _failure()
                entries.append(directory / entry.name)
        entries.sort(
            key=lambda item: item.name.encode("utf-8"),
            reverse=reverse,
        )
        budget.check_deadline()
        return entries
    except VerificationRunnerLifecycleError:
        raise
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise _failure() from exc


def ensure_runner_layout(paths: VerificationRunnerStatePaths) -> RunnerLayoutInventory:
    """Create only the fixed empty layout and return its closed inventory."""

    if not isinstance(paths, VerificationRunnerStatePaths):
        raise _failure()
    fixed_root = paths.root.parent
    try:
        inspect_physical_directory(fixed_root)
        _ensure_directory(paths.root, owner_root=fixed_root)
        _ensure_directory(paths.attempts, owner_root=paths.root)
        _ensure_directory(paths.quarantine, owner_root=paths.root)
        return inspect_runner_layout(paths)
    except (OSError, StatePathError) as exc:
        raise _failure() from exc


def _closed_attempt_names(
    directory: Path,
    *,
    owner_root: Path,
    budget: _TraversalBudget,
) -> tuple[str, ...]:
    inspect_physical_directory(directory, root=owner_root)
    entries = _bounded_sorted_children(directory, budget=budget)
    names: list[str] = []
    for entry in entries:
        budget.check_deadline()
        name = _attempt_id(entry.name)
        inspect_physical_directory(entry, root=owner_root)
        budget.check_deadline()
        names.append(name)
    return tuple(names)


def inspect_runner_layout(paths: VerificationRunnerStatePaths) -> RunnerLayoutInventory:
    """Reject aliases, unexpected children, and non-attempt private roots."""

    if not isinstance(paths, VerificationRunnerStatePaths):
        raise _failure()
    try:
        inspect_physical_directory(paths.root, root=paths.root.parent)
        deadline = time.monotonic() + _TRAVERSAL_TIMEOUT_SECONDS
        layout_budget = _TraversalBudget(
            maximum_entries=_MAX_LAYOUT_TRAVERSAL_ENTRIES,
            deadline=deadline,
        )
        root_entries = _bounded_sorted_children(
            paths.root,
            budget=layout_budget,
            maximum_items=3,
        )
        permitted = {paths.lock.name, paths.attempts.name, paths.quarantine.name}
        if any(entry.name not in permitted for entry in root_entries):
            raise _failure()
        for entry in root_entries:
            layout_budget.check_deadline()
            if entry.name == paths.lock.name:
                details = entry.lstat()
                if (
                    _is_reparse(details)
                    or not stat.S_ISREG(details.st_mode)
                    or int(details.st_size) not in {0, 1}
                ):
                    raise _failure()
            else:
                inspect_physical_directory(entry, root=paths.root)
            layout_budget.check_deadline()
        layout_budget.maximum_entries = (
            layout_budget.observed_entries + _MAX_LAYOUT_ATTEMPT_ENTRIES
        )
        return RunnerLayoutInventory(
            attempt_ids=_closed_attempt_names(
                paths.attempts,
                owner_root=paths.root,
                budget=layout_budget,
            ),
            quarantine_ids=_closed_attempt_names(
                paths.quarantine,
                owner_root=paths.root,
                budget=layout_budget,
            ),
        )
    except VerificationRunnerLifecycleError:
        raise
    except (OSError, StatePathError, UnicodeError) as exc:
        raise _failure() from exc


@contextmanager
def zero_wait_runner_lock(
    paths: VerificationRunnerStatePaths,
) -> Iterator[RunnerLayoutInventory]:
    """Acquire the sole Runner lock after validating the fixed hierarchy."""

    inventory = ensure_runner_layout(paths)
    try:
        with zero_wait_artifact_lock(paths.lock):
            yield inspect_runner_layout(paths)
    except ArtifactLockError as exc:
        raise _failure(code="runner_busy" if exc.contended else "runner_state_invalid") from exc


def attempt_paths(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
) -> RunnerAttemptPaths:
    exact_id = _attempt_id(attempt_id)
    root = paths.attempts / exact_id
    quarantine = paths.quarantine / exact_id
    for candidate in (root, quarantine):
        require_contained(candidate, paths.root)
    return RunnerAttemptPaths(
        attempt_id=exact_id,
        root=root,
        target=root / "target",
        scratch=root / "scratch",
        quarantine=quarantine,
    )


def create_attempt_directories(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
) -> RunnerAttemptPaths:
    """Create an attempt tree only after the caller persisted its owner row."""

    exact = attempt_paths(paths, attempt_id)
    inspect_runner_layout(paths)
    if path_lexically_exists(exact.root) or path_lexically_exists(exact.quarantine):
        raise _failure()
    created: list[ValidatedDirectory] = []
    try:
        created.append(
            create_physical_directory_exclusive(exact.root, root=paths.root)
        )
        for child in (exact.target, exact.scratch):
            created.append(
                create_physical_directory_exclusive(child, root=paths.root)
            )
        return exact
    except (OSError, StatePathError) as exc:
        try:
            remove_explicit_files_and_directories(
                root=paths.root,
                files=(),
                directories_deepest_first=reversed(created),
                max_files=0,
                max_directories=3,
            )
        except StatePathError:
            pass
        raise _failure() from exc


def validate_empty_attempt_tree(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
) -> RunnerAttemptPaths:
    """Validate the freshly-created exact two-child attempt tree."""

    exact = attempt_paths(paths, attempt_id)
    inspect_physical_directory(exact.root, root=paths.root)
    deadline = time.monotonic() + _TRAVERSAL_TIMEOUT_SECONDS
    budget = _TraversalBudget(maximum_entries=2, deadline=deadline)
    children = _bounded_sorted_children(
        exact.root,
        budget=budget,
        maximum_items=2,
    )
    if {entry.name for entry in children} != _EXPECTED_ATTEMPT_CHILDREN:
        raise _failure()
    for child in (exact.target, exact.scratch):
        inspect_physical_directory(child, root=paths.root)
        try:
            budget.check_deadline()
            with os.scandir(child) as iterator:
                if next(iterator, None) is not None:
                    raise _failure()
                budget.check_deadline()
        except (OSError, RuntimeError) as exc:
            raise _failure() from exc
    return exact


def create_scratch_directories(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
) -> tuple[Path, ...]:
    """Create the fixed empty clean-environment directories for one attempt."""

    exact = validate_empty_attempt_tree(paths, attempt_id)
    created: list[ValidatedDirectory] = []
    try:
        for name in _EXPECTED_SCRATCH_CHILDREN:
            created.append(
                create_physical_directory_exclusive(
                    exact.scratch / name,
                    root=paths.root,
                )
            )
        return tuple(item.path for item in created)
    except (OSError, StatePathError) as exc:
        try:
            remove_explicit_files_and_directories(
                root=paths.root,
                files=(),
                directories_deepest_first=reversed(created),
                max_files=0,
                max_directories=len(_EXPECTED_SCRATCH_CHILDREN),
            )
        except StatePathError:
            pass
        raise _failure() from exc


def quarantine_attempt_tree(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
) -> RunnerAttemptPaths:
    """Move one validated owned attempt to its non-replacing quarantine name."""

    exact = attempt_paths(paths, attempt_id)
    source = inspect_physical_directory(exact.root, root=paths.root)
    try:
        rename_no_replace(source, exact.quarantine, root=paths.root)
    except StatePathError as exc:
        raise _failure() from exc
    return exact


def _enumerate_owned_tree(
    tree: Path,
    *,
    owner_root: Path,
    deadline: float,
) -> tuple[tuple[_OwnedDeletionFile, ...], tuple[ValidatedDirectory, ...]]:
    files: list[_OwnedDeletionFile] = []
    directories: list[tuple[int, ValidatedDirectory]] = []
    file_count = 0
    directory_count = 1
    pending: list[tuple[Path, int]] = [(tree, 0)]
    budget = _TraversalBudget(
        maximum_entries=_MAX_ATTEMPT_TRAVERSAL_ENTRIES - 1,
        deadline=deadline,
    )
    budget.observe()
    while pending:
        budget.check_deadline()
        current, depth = pending.pop()
        if depth > _MAX_ATTEMPT_DEPTH:
            raise _failure()
        validated = inspect_physical_directory(current, root=owner_root)
        directories.append((depth, validated))
        if len(directories) > directory_count:
            raise _failure()
        entries = _bounded_sorted_children(
            current,
            budget=budget,
            reverse=True,
        )
        for entry in entries:
            budget.check_deadline()
            require_contained(entry, owner_root)
            try:
                details = entry.lstat()
            except OSError as exc:
                raise _failure() from exc
            budget.check_deadline()
            if _is_reparse(details):
                raise _failure()
            if stat.S_ISDIR(details.st_mode):
                directory_count += 1
                if directory_count > _MAX_ATTEMPT_DIRECTORIES:
                    raise _failure()
                pending.append((entry, depth + 1))
            elif stat.S_ISREG(details.st_mode):
                size = int(details.st_size)
                if size < 0:
                    raise _failure()
                file_count += 1
                if file_count > _MAX_ATTEMPT_FILES:
                    raise _failure()
                budget.check_deadline()
                validated_path, identity = inspect_physical_file(
                    entry,
                    root=owner_root,
                )
                files.append(
                    _OwnedDeletionFile(
                        path=validated_path,
                        identity=identity,
                    )
                )
                budget.check_deadline()
            else:
                raise _failure()
    files.sort(key=lambda item: str(item.path).encode("utf-8"))
    budget.check_deadline()
    directories.sort(key=lambda item: (-item[0], str(item[1].path).encode("utf-8")))
    budget.check_deadline()
    return tuple(files), tuple(item[1] for item in directories)


def _remove_validated_tree(
    *,
    root: Path,
    files: tuple[_OwnedDeletionFile, ...],
    directories: tuple[ValidatedDirectory, ...],
    deadline: float,
) -> None:
    if len(files) > _MAX_ATTEMPT_FILES or len(directories) > _MAX_ATTEMPT_DIRECTORIES:
        raise _failure()
    for file in files:
        if time.monotonic() > deadline:
            raise _failure()
        validated_path, identity = inspect_physical_file(file.path, root=root)
        if identity != file.identity:
            raise _failure()
        try:
            validated_path.unlink()
        except OSError as exc:
            raise _failure() from exc
        if time.monotonic() > deadline:
            raise _failure()
    for directory in directories:
        if time.monotonic() > deadline:
            raise _failure()
        rmdir_validated_directory(directory, root=root)
        if time.monotonic() > deadline:
            raise _failure()


def _remove_attempt_tree_or_raise(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
    expected_owners: _RunnerOwnerDirectoryIdentities,
) -> None:
    """Delete one explicit DB-named attempt/quarantine tree, never an unknown."""

    exact = attempt_paths(paths, attempt_id)
    inventory = inspect_runner_layout(paths)
    require_known_attempt_inventory(
        inventory,
        known_attempt_ids=(attempt_id,),
    )
    if _capture_owner_directory_identities(paths) != expected_owners:
        raise _failure()
    present = [
        candidate
        for candidate in (exact.root, exact.quarantine)
        if path_lexically_exists(candidate)
    ]
    if len(present) > 1:
        raise _failure()
    if not present:
        return
    try:
        deadline = time.monotonic() + _TRAVERSAL_TIMEOUT_SECONDS
        files, directories = _enumerate_owned_tree(
            present[0],
            owner_root=paths.root,
            deadline=deadline,
        )
        if _capture_owner_directory_identities(paths) != expected_owners:
            raise _failure()
        _remove_validated_tree(
            root=paths.root,
            files=files,
            directories=directories,
            deadline=deadline,
        )
    except (OSError, StatePathError) as exc:
        raise _failure() from exc


def _capture_owner_directory_identities(
    paths: VerificationRunnerStatePaths,
) -> _RunnerOwnerDirectoryIdentities:
    return _RunnerOwnerDirectoryIdentities(
        root=inspect_physical_directory(
            paths.root,
            root=paths.root.parent,
        ).identity,
        attempts=inspect_physical_directory(
            paths.attempts,
            root=paths.root,
        ).identity,
        quarantine=inspect_physical_directory(
            paths.quarantine,
            root=paths.root,
        ).identity,
    )


def _attempt_absence_is_proved(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
    expected_owners: _RunnerOwnerDirectoryIdentities,
) -> bool:
    inventory = inspect_runner_layout(paths)
    require_known_attempt_inventory(
        inventory,
        known_attempt_ids=(attempt_id,),
    )
    exact = attempt_paths(paths, attempt_id)
    owners_unchanged = _capture_owner_directory_identities(paths) == expected_owners
    return bool(
        owners_unchanged
        and attempt_id not in inventory.attempt_ids
        and attempt_id not in inventory.quarantine_ids
        and not path_lexically_exists(exact.root)
        and not path_lexically_exists(exact.quarantine)
    )


def cleanup_attempt_tree(
    paths: VerificationRunnerStatePaths,
    attempt_id: str,
) -> RunnerPrivateTreeResultV1:
    """Remove one valid owned tree and return only absent or uncertainty."""

    if not isinstance(paths, VerificationRunnerStatePaths):
        raise _failure()
    exact_id = _attempt_id(attempt_id)
    try:
        owner_identities = _capture_owner_directory_identities(paths)
        _remove_attempt_tree_or_raise(paths, exact_id, owner_identities)
        state = (
            "absent"
            if _attempt_absence_is_proved(paths, exact_id, owner_identities)
            else "uncertain"
        )
    except (
        OSError,
        RuntimeError,
        UnicodeError,
        StatePathError,
        VerificationRunnerLifecycleError,
    ):
        state = "uncertain"
    return RunnerPrivateTreeResultV1(exact_id, state)


def require_known_attempt_inventory(
    inventory: RunnerLayoutInventory,
    *,
    known_attempt_ids: Sequence[str],
) -> None:
    """Reject every filesystem owner that is not named by immutable DB state."""

    if not isinstance(inventory, RunnerLayoutInventory):
        raise _failure()
    exact_known = tuple(_attempt_id(value) for value in known_attempt_ids)
    if len(set(exact_known)) != len(exact_known):
        raise _failure()
    observed = (*inventory.attempt_ids, *inventory.quarantine_ids)
    if len(set(observed)) != len(observed) or not set(observed).issubset(exact_known):
        raise _failure()
