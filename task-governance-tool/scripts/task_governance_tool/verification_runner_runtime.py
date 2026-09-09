"""Release-bound Runner identity and shared runtime failure type."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool import __version__
from task_governance_tool.evidence_ledger import domain_digest
from task_governance_tool.self_status import (
    ReleaseManifestVerificationError,
    verify_release_manifest_core,
)
from task_governance_tool.verification_runner import (
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_POLICY_DIGEST,
    RUNNER_POSIX_POLICY_DIGEST,
)


RUNNER_IMPLEMENTATION_DIGEST_DOMAIN = (
    b"taskgov-verification-runner-implementation-v1\0"
)


@dataclass
class VerificationRunnerRuntimeError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _policy_mismatch() -> VerificationRunnerRuntimeError:
    return VerificationRunnerRuntimeError(
        "policy_mismatch",
        "the installed Runner implementation does not match its release manifest",
    )


@dataclass(frozen=True)
class RunnerImplementationIdentity:
    implementation_version: str
    implementation_digest: str
    manifest_version: int
    package_name: str
    package_version: str
    core_files: tuple[tuple[str, str], ...]

    def canonical_value(self) -> dict[str, Any]:
        return {
            "core_files": dict(self.core_files),
            "manifest_version": self.manifest_version,
            "package_name": self.package_name,
            "package_version": self.package_version,
        }


def capture_runner_implementation(
    skill_root: Path,
    *,
    expected_package_version: str | None = None,
) -> RunnerImplementationIdentity:
    """Bind the strict release manifest only after every core byte is verified."""

    try:
        manifest = verify_release_manifest_core(
            skill_root,
            expected_package_version=(
                __version__ if expected_package_version is None else expected_package_version
            ),
        )
    except ReleaseManifestVerificationError as exc:
        raise _policy_mismatch() from exc
    canonical = manifest.canonical_value()
    return RunnerImplementationIdentity(
        implementation_version=RUNNER_IMPLEMENTATION_VERSION,
        implementation_digest=domain_digest(
            RUNNER_IMPLEMENTATION_DIGEST_DOMAIN,
            canonical,
        ),
        manifest_version=manifest.manifest_version,
        package_name=manifest.package_name,
        package_version=manifest.package_version,
        core_files=tuple(sorted(manifest.core_files.items())),
    )


def current_runner_policy_digest() -> str:
    """Identify native accounting semantics without admitting a runtime launch."""

    return (
        RUNNER_POSIX_POLICY_DIGEST
        if sys.platform in {"linux", "darwin"}
        else RUNNER_POLICY_DIGEST
    )


def observe_fixed_package_runtime(
    materialized_root: Path, scratch_root: Path
) -> Path:
    """Select the native fixed-runtime observation without a fallback executable."""

    if sys.platform == "win32":
        from task_governance_tool import _verification_runner_executable_win32

        return _verification_runner_executable_win32.observe_fixed_package_runtime(
            materialized_root, scratch_root
        )
    raise VerificationRunnerRuntimeError(
        "runtime_unavailable", "the fixed Runner runtime is unavailable"
    )


__all__ = [
    "RUNNER_IMPLEMENTATION_DIGEST_DOMAIN",
    "RunnerImplementationIdentity",
    "VerificationRunnerRuntimeError",
    "capture_runner_implementation",
    "current_runner_policy_digest",
    "observe_fixed_package_runtime",
]
