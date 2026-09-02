"""Physical project plan capture and closed TG-M24.2A plan resolution.

The local package config is authority input independent of selected Git target
material.  This module performs no target observation, process launch, database
write, or durable publication.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.evidence_ledger import (
    EvidenceLedgerError,
    canonical_json_bytes,
)
from task_governance_tool.completion import safe_git_command, safe_git_environment
from task_governance_tool.state_paths import (
    FileIdentity,
    StatePathError,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    require_contained,
)
from task_governance_tool.verification_runner import RUNNER_MAX_OUTPUT_BYTES


PLAN_VERSION = 1
PLAN_BLOB_UTF8_BYTE_LIMIT = 65_536
PLAN_ENTRY_LIMIT = 64
PLAN_STEP_LIMIT = 16
PLAN_ARG_LIMIT = 64
PLAN_TOTAL_TIMEOUT_SECONDS = 1_800
PLAN_SEMANTIC_DOMAIN = b"taskgov-verification-runner-plan-v1\0"
PLAN_ENTRY_DOMAIN = b"taskgov-verification-runner-plan-entry-v1\0"
PLAN_RELATIVE_PATH = Path("config") / "verification-runner.json"

PLAN_ERROR_MESSAGE = "verification runner plan is invalid"
PLAN_SOURCE_ERROR_MESSAGE = "verification runner plan could not be read safely"

_PLAN_KEYS = frozenset({"version", "plan_id", "trusted_local", "entries"})
_ENTRY_KEYS = frozenset(
    {
        "task_id",
        "contract_revision",
        "verification_expectation_digest",
        "verification_criterion_digest",
        "coverage",
        "steps",
    }
)
_STEP_KEYS = frozenset(
    {
        "step_id",
        "mode",
        "entrypoint",
        "argv",
        "cwd",
        "timeout_seconds",
        "cpu_seconds",
        "memory_mib",
        "process_limit",
        "output_byte_limit",
    }
)
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_TASK_ID = re.compile(r"tg_task_[0-9a-f]{16}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RELATIVE_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")
_MODULE_COMPONENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


@dataclass
class VerificationRunnerPlanError(Exception):
    code: str
    message: str = PLAN_ERROR_MESSAGE

    def __str__(self) -> str:
        return self.message


def _plan_error(code: str = "plan_invalid") -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(code=code)


def _source_error() -> VerificationRunnerPlanError:
    return VerificationRunnerPlanError(
        code="plan_source_invalid",
        message=PLAN_SOURCE_ERROR_MESSAGE,
    )


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _domain_digest(domain: bytes, value: Any) -> str:
    try:
        payload = canonical_json_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _plan_error() from exc
    return _sha256(domain + payload)


@dataclass(frozen=True)
class VerificationRunnerPlanSource:
    raw_blob: bytes
    raw_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.raw_blob) is not bytes
            or len(self.raw_blob) > PLAN_BLOB_UTF8_BYTE_LIMIT
            or type(self.raw_digest) is not str
            or _DIGEST.fullmatch(self.raw_digest) is None
            or self.raw_digest != _sha256(self.raw_blob)
        ):
            raise _source_error()


def _same_file_identity(path: Path, root: Path, expected: FileIdentity) -> bool:
    try:
        _path, observed = inspect_physical_file(
            path,
            root=root,
            max_bytes=PLAN_BLOB_UTF8_BYTE_LIMIT,
        )
        details = path.lstat()
    except (OSError, StatePathError):
        return False
    return observed == expected and _opened_file_matches(details, expected)


def _opened_file_matches(details: os.stat_result, expected: FileIdentity) -> bool:
    return int(details.st_nlink) == 1 and (
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(details.st_mtime_ns),
    ) == (
        expected.device,
        expected.inode,
        expected.size,
        expected.modified_ns,
    )


def _plan_is_local_only(repo: Path, plan: Path) -> bool:
    """Require the physical plan to be absent from the index and ignored."""

    try:
        inspect_physical_directory(repo)
        require_contained(plan, repo)
        operand = plan.relative_to(repo).as_posix()
        index_pathspec = f":(icase,literal){operand}"
        common = [
            *safe_git_command(repo),
            "-c",
            "core.fsmonitor=false",
        ]
        tracked = subprocess.run(
            [
                *common,
                "ls-files",
                "--cached",
                "--stage",
                "--error-unmatch",
                "-z",
                "--",
                index_pathspec,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
            env=safe_git_environment(),
            shell=False,
        )
        ignored = subprocess.run(
            [*common, "check-ignore", "--quiet", "--no-index", "--", operand],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
            env=safe_git_environment(),
            shell=False,
        )
    except (OSError, StatePathError, ValueError, subprocess.SubprocessError):
        return False
    return (
        tracked.returncode == 1 and ignored.returncode == 0
    )


def verification_runner_plan_is_local_only(repo: Path, plan: Path) -> bool:
    """Expose the existing canonical Plan index/ignore policy to its publisher."""

    return _plan_is_local_only(repo, plan)


def capture_verification_runner_plan(
    repo: Path,
    physical_package_root: Path,
) -> VerificationRunnerPlanSource | None:
    """Read the sole local-only plan without consulting selected Git material."""

    if (
        not isinstance(repo, Path)
        or not repo.is_absolute()
        or not isinstance(physical_package_root, Path)
        or not physical_package_root.is_absolute()
    ):
        raise _source_error()
    root = physical_package_root
    config = root / PLAN_RELATIVE_PATH.parent
    plan = root / PLAN_RELATIVE_PATH
    try:
        root_before = inspect_physical_directory(root)
        require_contained(root, repo)
        require_contained(config, root)
        require_contained(plan, root)
        if not path_lexically_exists(config):
            if (
                path_lexically_exists(config)
                or inspect_physical_directory(root) != root_before
            ):
                raise _source_error()
            return None
        config_before = inspect_physical_directory(config, root=root)
        if not path_lexically_exists(plan):
            if (
                path_lexically_exists(plan)
                or inspect_physical_directory(config, root=root) != config_before
                or inspect_physical_directory(root) != root_before
            ):
                raise _source_error()
            return None
        if not _plan_is_local_only(repo, plan):
            raise _source_error()
        _path, before = inspect_physical_file(
            plan,
            root=root,
            max_bytes=PLAN_BLOB_UTF8_BYTE_LIMIT,
        )
        if not _same_file_identity(plan, root, before):
            raise _source_error()
        descriptor = os.open(
            plan,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                stat.S_ISLNK(opened.st_mode)
                or not stat.S_ISREG(opened.st_mode)
                or not _opened_file_matches(opened, before)
            ):
                raise _source_error()
            payload = bytearray()
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                if len(payload) > PLAN_BLOB_UTF8_BYTE_LIMIT - len(chunk):
                    raise _source_error()
                payload.extend(chunk)
            after_open = os.fstat(descriptor)
            if (
                not stat.S_ISREG(after_open.st_mode)
                or not _opened_file_matches(after_open, before)
                or int(after_open.st_size) != len(payload)
            ):
                raise _source_error()
        finally:
            os.close(descriptor)
        if not _same_file_identity(plan, root, before) or not _plan_is_local_only(
            repo, plan
        ):
            raise _source_error()
        if (
            inspect_physical_directory(config, root=root) != config_before
            or inspect_physical_directory(root) != root_before
        ):
            raise _source_error()
        raw = bytes(payload)
        return VerificationRunnerPlanSource(raw_blob=raw, raw_digest=_sha256(raw))
    except VerificationRunnerPlanError:
        raise
    except (OSError, StatePathError, RuntimeError, UnicodeError) as exc:
        raise _source_error() from exc


def _utf8_bytes(value: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise _plan_error() from exc


def _utf16_units(value: str) -> int:
    try:
        return len(value.encode("utf-16-le", errors="strict")) // 2
    except UnicodeEncodeError as exc:
        raise _plan_error() from exc


def _exact_mapping(value: object, keys: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != keys:
        raise _plan_error()
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise _plan_error()
    return value


def _task_id(value: object) -> str:
    if type(value) is not str or _TASK_ID.fullmatch(value) is None:
        raise _plan_error()
    return value


def validate_verification_runner_plan_task_id(value: object) -> str:
    """Validate the shared closed Task ID grammar for pure Plan operations."""

    return _task_id(value)


def _positive_int(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _plan_error()
    return value


def _relative_path(value: object, *, script: bool = False) -> str:
    if type(value) is not str or _utf8_bytes(value) > 512:
        raise _plan_error()
    if value == ".":
        if script:
            raise _plan_error()
        return value
    parts = value.split("/")
    if (
        not 1 <= len(parts) <= 32
        or any(_RELATIVE_COMPONENT.fullmatch(part) is None for part in parts)
        or (script and not value.endswith(".py"))
    ):
        raise _plan_error()
    return value


def _module_entrypoint(value: object) -> str:
    if type(value) is not str or _utf8_bytes(value) > 512:
        raise _plan_error()
    parts = value.split(".")
    if not 1 <= len(parts) <= 16 or any(
        _MODULE_COMPONENT.fullmatch(part) is None for part in parts
    ):
        raise _plan_error()
    return value


def _literal_arg(value: object) -> str:
    if (
        type(value) is not str
        or _utf8_bytes(value) > 4_096
        or _utf16_units(value) > 4_096
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise _plan_error()
    return value


@dataclass(frozen=True)
class VerificationRunnerPlanBasis:
    task_id: str
    contract_revision: int
    verification_expectation_digest: str
    verification_criterion_digest: str

    def __post_init__(self) -> None:
        if (
            _task_id(self.task_id) != self.task_id
            or _positive_int(self.contract_revision, 1, 2**63 - 1)
            != self.contract_revision
            or type(self.verification_expectation_digest) is not str
            or _HEX64.fullmatch(self.verification_expectation_digest) is None
            or type(self.verification_criterion_digest) is not str
            or _DIGEST.fullmatch(self.verification_criterion_digest) is None
        ):
            raise _plan_error()


@dataclass(frozen=True)
class VerificationRunnerPlanStep:
    ordinal: int
    step_id: str
    mode: str
    entrypoint: str
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: int
    cpu_seconds: int
    memory_mib: int
    process_limit: int
    output_byte_limit: int
    shell: bool = False
    path_lookup: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or not 1 <= self.ordinal <= PLAN_STEP_LIMIT
            or _identifier(self.step_id) != self.step_id
            or type(self.mode) is not str
            or self.mode not in {"script", "module"}
            or type(self.argv) is not tuple
            or len(self.argv) > PLAN_ARG_LIMIT
            or tuple(_literal_arg(item) for item in self.argv) != self.argv
            or _relative_path(self.cwd) != self.cwd
            or _positive_int(self.timeout_seconds, 1, 900) != self.timeout_seconds
            or _positive_int(self.cpu_seconds, 1, 900) != self.cpu_seconds
            or _positive_int(self.memory_mib, 64, 2_048) != self.memory_mib
            or _positive_int(self.process_limit, 1, 32) != self.process_limit
            or type(self.output_byte_limit) is not int
            or self.output_byte_limit != RUNNER_MAX_OUTPUT_BYTES
            or self.shell is not False
            or self.path_lookup is not False
        ):
            raise _plan_error()
        expected = (
            _relative_path(self.entrypoint, script=True)
            if self.mode == "script"
            else _module_entrypoint(self.entrypoint)
        )
        if expected != self.entrypoint:
            raise _plan_error()

    def canonical_value(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "cpu_seconds": self.cpu_seconds,
            "cwd": self.cwd,
            "entrypoint": self.entrypoint,
            "memory_mib": self.memory_mib,
            "mode": self.mode,
            "ordinal": self.ordinal,
            "output_byte_limit": self.output_byte_limit,
            "path_lookup": False,
            "process_limit": self.process_limit,
            "shell": False,
            "step_id": self.step_id,
            "timeout_seconds": self.timeout_seconds,
        }

    def physical_value(self) -> dict[str, Any]:
        """Return the exact closed StepV1 file representation."""

        return {
            "argv": list(self.argv),
            "cpu_seconds": self.cpu_seconds,
            "cwd": self.cwd,
            "entrypoint": self.entrypoint,
            "memory_mib": self.memory_mib,
            "mode": self.mode,
            "output_byte_limit": self.output_byte_limit,
            "process_limit": self.process_limit,
            "step_id": self.step_id,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class VerificationRunnerPlanEntry:
    task_id: str
    contract_revision: int
    verification_expectation_digest: str
    verification_criterion_digest: str
    coverage: str
    steps: tuple[VerificationRunnerPlanStep, ...]

    def __post_init__(self) -> None:
        if (
            _task_id(self.task_id) != self.task_id
            or _positive_int(self.contract_revision, 1, 2**63 - 1)
            != self.contract_revision
            or type(self.verification_expectation_digest) is not str
            or _HEX64.fullmatch(self.verification_expectation_digest) is None
            or type(self.verification_criterion_digest) is not str
            or _DIGEST.fullmatch(self.verification_criterion_digest) is None
            or type(self.coverage) is not str
            or self.coverage != "full"
            or type(self.steps) is not tuple
            or not 1 <= len(self.steps) <= PLAN_STEP_LIMIT
            or any(
                type(step) is not VerificationRunnerPlanStep
                or step.ordinal != ordinal
                for ordinal, step in enumerate(self.steps, start=1)
            )
            or len({step.step_id for step in self.steps}) != len(self.steps)
            or sum(step.timeout_seconds for step in self.steps)
            > PLAN_TOTAL_TIMEOUT_SECONDS
        ):
            raise _plan_error()

    def basis(self) -> VerificationRunnerPlanBasis:
        return VerificationRunnerPlanBasis(
            task_id=self.task_id,
            contract_revision=self.contract_revision,
            verification_expectation_digest=self.verification_expectation_digest,
            verification_criterion_digest=self.verification_criterion_digest,
        )

    def canonical_value(self) -> dict[str, Any]:
        return {
            "contract_revision": self.contract_revision,
            "coverage": self.coverage,
            "steps": [step.canonical_value() for step in self.steps],
            "task_id": self.task_id,
            "verification_criterion_digest": self.verification_criterion_digest,
            "verification_expectation_digest": self.verification_expectation_digest,
        }

    def physical_value(self) -> dict[str, Any]:
        return {
            "contract_revision": self.contract_revision,
            "coverage": self.coverage,
            "steps": [step.physical_value() for step in self.steps],
            "task_id": self.task_id,
            "verification_criterion_digest": self.verification_criterion_digest,
            "verification_expectation_digest": self.verification_expectation_digest,
        }


@dataclass(frozen=True)
class VerificationRunnerPlan:
    version: int
    plan_id: str
    trusted_local: bool
    entries: tuple[VerificationRunnerPlanEntry, ...]

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != PLAN_VERSION
            or _identifier(self.plan_id) != self.plan_id
            or type(self.trusted_local) is not bool
            or type(self.entries) is not tuple
            or any(
                type(entry) is not VerificationRunnerPlanEntry
                for entry in self.entries
            )
        ):
            raise _plan_error()
        if len(self.entries) > PLAN_ENTRY_LIMIT:
            raise _plan_error("plan_too_large")
        bases = tuple(entry.basis() for entry in self.entries)
        if len(set(bases)) != len(bases):
            raise _plan_error("plan_ambiguous")

    def canonical_value(self) -> dict[str, Any]:
        """Return the established normalized semantic-digest representation."""

        return {
            "entries": [entry.canonical_value() for entry in self.entries],
            "plan_id": self.plan_id,
            "trusted_local": self.trusted_local,
            "version": self.version,
        }

    def physical_value(self) -> dict[str, Any]:
        """Return the exact closed PlanV1 file representation."""

        return {
            "entries": [entry.physical_value() for entry in self.entries],
            "plan_id": self.plan_id,
            "trusted_local": self.trusted_local,
            "version": self.version,
        }


@dataclass(frozen=True)
class VerificationRunnerPlanResolution:
    plan_state: str
    route: str
    reason: str | None
    plan_blob_object_id: str | None
    plan_raw_digest: str | None
    plan_id: str | None
    plan_version: int | None
    plan_semantic_digest: str | None
    selected_entry_digest: str | None
    coverage: str
    steps: tuple[VerificationRunnerPlanStep, ...]

    def __post_init__(self) -> None:
        fallback = {
            "absent": "plan_absent",
            "disabled": "trusted_local_disabled",
            "no_match": "plan_entry_absent",
        }
        if (
            self.plan_blob_object_id is not None
            or type(self.steps) is not tuple
            or any(
                not isinstance(step, VerificationRunnerPlanStep)
                for step in self.steps
            )
        ):
            raise _plan_error()
        if self.plan_state == "runner":
            if (
                self.route != "runner"
                or self.reason is not None
                or type(self.plan_raw_digest) is not str
                or _DIGEST.fullmatch(self.plan_raw_digest) is None
                or _identifier(self.plan_id) != self.plan_id
                or self.plan_version != PLAN_VERSION
                or type(self.plan_semantic_digest) is not str
                or _DIGEST.fullmatch(self.plan_semantic_digest) is None
                or type(self.selected_entry_digest) is not str
                or _DIGEST.fullmatch(self.selected_entry_digest) is None
                or self.coverage != "full"
                or not 1 <= len(self.steps) <= PLAN_STEP_LIMIT
            ):
                raise _plan_error()
            return
        if self.plan_state not in fallback:
            raise _plan_error()
        source_absent = self.plan_state == "absent"
        if (
            self.route != "m21_fallback"
            or self.reason != fallback[self.plan_state]
            or self.coverage != "not_applicable"
            or self.steps
            or self.selected_entry_digest is not None
            or (
                source_absent
                and any(
                    value is not None
                    for value in (
                        self.plan_raw_digest,
                        self.plan_id,
                        self.plan_version,
                        self.plan_semantic_digest,
                    )
                )
            )
            or (
                not source_absent
                and (
                    type(self.plan_raw_digest) is not str
                    or _DIGEST.fullmatch(self.plan_raw_digest) is None
                    or _identifier(self.plan_id) != self.plan_id
                    or self.plan_version != PLAN_VERSION
                    or type(self.plan_semantic_digest) is not str
                    or _DIGEST.fullmatch(self.plan_semantic_digest) is None
                )
            )
        ):
            raise _plan_error()

    @property
    def step_count(self) -> int:
        return len(self.steps)


def _parse_step(value: object, ordinal: int) -> VerificationRunnerPlanStep:
    item = _exact_mapping(value, _STEP_KEYS)
    argv = item["argv"]
    if type(argv) is not list or len(argv) > PLAN_ARG_LIMIT:
        raise _plan_error()
    return VerificationRunnerPlanStep(
        ordinal=ordinal,
        step_id=_identifier(item["step_id"]),
        mode=item["mode"],
        entrypoint=item["entrypoint"],
        argv=tuple(_literal_arg(argument) for argument in argv),
        cwd=item["cwd"],
        timeout_seconds=item["timeout_seconds"],
        cpu_seconds=item["cpu_seconds"],
        memory_mib=item["memory_mib"],
        process_limit=item["process_limit"],
        output_byte_limit=item["output_byte_limit"],
    )


def verification_runner_plan_step_string_leaves(value: object) -> tuple[str, ...]:
    """Recognize one StepV1 shape and return its caller-controlled strings."""

    item = _exact_mapping(value, _STEP_KEYS)
    argv = item["argv"]
    string_fields = (
        item["step_id"],
        item["mode"],
        item["entrypoint"],
        item["cwd"],
    )
    integer_fields = (
        item["timeout_seconds"],
        item["cpu_seconds"],
        item["memory_mib"],
        item["process_limit"],
        item["output_byte_limit"],
    )
    if (
        any(type(field) is not str for field in string_fields)
        or any(type(field) is not int for field in integer_fields)
        or type(argv) is not list
        or any(type(argument) is not str for argument in argv)
    ):
        raise _plan_error()
    return (*string_fields[:3], *argv, string_fields[3])


def decode_verification_runner_plan_steps(
    value: object,
) -> tuple[VerificationRunnerPlanStep, ...]:
    """Decode one exact bounded ordered StepV1 collection."""

    if type(value) is not list or not 1 <= len(value) <= PLAN_STEP_LIMIT:
        raise _plan_error()
    steps = tuple(
        _parse_step(step, ordinal)
        for ordinal, step in enumerate(value, start=1)
    )
    if (
        len({step.step_id for step in steps}) != len(steps)
        or sum(step.timeout_seconds for step in steps) > PLAN_TOTAL_TIMEOUT_SECONDS
    ):
        raise _plan_error()
    return steps


def _parse_entry(value: object) -> VerificationRunnerPlanEntry:
    item = _exact_mapping(value, _ENTRY_KEYS)
    steps = decode_verification_runner_plan_steps(item["steps"])
    expectation = item["verification_expectation_digest"]
    criterion = item["verification_criterion_digest"]
    if (
        type(expectation) is not str
        or _HEX64.fullmatch(expectation) is None
        or type(criterion) is not str
        or _DIGEST.fullmatch(criterion) is None
        or item["coverage"] != "full"
    ):
        raise _plan_error()
    return VerificationRunnerPlanEntry(
        task_id=_task_id(item["task_id"]),
        contract_revision=_positive_int(item["contract_revision"], 1, 2**63 - 1),
        verification_expectation_digest=expectation,
        verification_criterion_digest=criterion,
        coverage="full",
        steps=steps,
    )


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _plan_error()
        result[key] = value
    return result


def _reject_number(_value: str) -> None:
    raise _plan_error()


def decode_verification_runner_json(raw_blob: bytes) -> Any:
    """Decode bounded duplicate-free Runner JSON without accepting floats."""

    if type(raw_blob) is not bytes:
        raise _plan_error()
    if len(raw_blob) > PLAN_BLOB_UTF8_BYTE_LIMIT:
        raise _plan_error("plan_too_large")
    try:
        text = raw_blob.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except VerificationRunnerPlanError:
        raise
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        raise _plan_error() from exc
    return value


def decode_verification_runner_plan(raw_blob: bytes) -> VerificationRunnerPlan:
    """Decode and validate one complete physical PlanV1 value."""

    decoded = _exact_mapping(decode_verification_runner_json(raw_blob), _PLAN_KEYS)
    if (
        type(decoded["version"]) is not int
        or decoded["version"] != PLAN_VERSION
        or type(decoded["trusted_local"]) is not bool
    ):
        raise _plan_error()
    plan_id = _identifier(decoded["plan_id"])
    raw_entries = decoded["entries"]
    if type(raw_entries) is not list or len(raw_entries) > PLAN_ENTRY_LIMIT:
        raise _plan_error("plan_too_large")
    return VerificationRunnerPlan(
        version=PLAN_VERSION,
        plan_id=plan_id,
        trusted_local=decoded["trusted_local"],
        entries=tuple(_parse_entry(item) for item in raw_entries),
    )


def encode_verification_runner_plan(plan: VerificationRunnerPlan) -> bytes:
    """Encode one validated PlanV1 as complete canonical physical bytes."""

    if type(plan) is not VerificationRunnerPlan:
        raise _plan_error()
    try:
        payload = canonical_json_bytes(plan.physical_value())
    except (
        EvidenceLedgerError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
    ) as exc:
        raise _plan_error() from exc
    if len(payload) > PLAN_BLOB_UTF8_BYTE_LIMIT:
        raise _plan_error("plan_too_large")
    return payload


def _fallback(
    *,
    state: str,
    reason: str,
    source: VerificationRunnerPlanSource | None,
    plan_id: str | None = None,
    plan_semantic_digest: str | None = None,
) -> VerificationRunnerPlanResolution:
    return VerificationRunnerPlanResolution(
        plan_state=state,
        route="m21_fallback",
        reason=reason,
        plan_blob_object_id=None,
        plan_raw_digest=None if source is None else source.raw_digest,
        plan_id=plan_id,
        plan_version=None if source is None else PLAN_VERSION,
        plan_semantic_digest=plan_semantic_digest,
        selected_entry_digest=None,
        coverage="not_applicable",
        steps=(),
    )


def resolve_verification_runner_plan(
    source: VerificationRunnerPlanSource | None,
    *,
    task_id: str,
    contract_revision: int,
    verification_expectation_digest: str,
    verification_criterion_digest: str,
) -> VerificationRunnerPlanResolution:
    """Resolve one exact current basis from the independently captured plan."""

    if source is None:
        return _fallback(state="absent", reason="plan_absent", source=None)
    if not isinstance(source, VerificationRunnerPlanSource):
        raise _source_error()
    current_basis = VerificationRunnerPlanBasis(
        task_id=task_id,
        contract_revision=contract_revision,
        verification_expectation_digest=verification_expectation_digest,
        verification_criterion_digest=verification_criterion_digest,
    )
    current_task = current_basis.task_id

    plan = decode_verification_runner_plan(source.raw_blob)
    semantic_digest = _domain_digest(PLAN_SEMANTIC_DOMAIN, plan.canonical_value())
    if plan.trusted_local is False:
        return _fallback(
            state="disabled",
            reason="trusted_local_disabled",
            source=source,
            plan_id=plan.plan_id,
            plan_semantic_digest=semantic_digest,
        )

    for_task = tuple(
        entry for entry in plan.entries if entry.task_id == current_task
    )
    if not for_task:
        return _fallback(
            state="no_match",
            reason="plan_entry_absent",
            source=source,
            plan_id=plan.plan_id,
            plan_semantic_digest=semantic_digest,
        )
    if len(for_task) != 1:
        raise _plan_error("plan_ambiguous")
    exact = for_task[0]
    if exact.basis() != current_basis:
        raise _plan_error("plan_basis_mismatch")
    return VerificationRunnerPlanResolution(
        plan_state="runner",
        route="runner",
        reason=None,
        plan_blob_object_id=None,
        plan_raw_digest=source.raw_digest,
        plan_id=plan.plan_id,
        plan_version=PLAN_VERSION,
        plan_semantic_digest=semantic_digest,
        selected_entry_digest=_domain_digest(
            PLAN_ENTRY_DOMAIN,
            exact.canonical_value(),
        ),
        coverage="full",
        steps=exact.steps,
    )
