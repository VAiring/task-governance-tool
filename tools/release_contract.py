"""Offline, read-only release contract checker for this source repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


sys.dont_write_bytecode = True

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRECTORY = "task-governance-tool"
OFFICIAL_APACHE_2_LICENSE_SHA256 = (
    "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
)
CHECKER_INVOCATION = "python tools/release_contract.py --repo ."
EXPECTED_RELEASE_ORIGIN = "github:VAiring/task-governance-tool"

if str(DEFAULT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_REPO_ROOT))

from tools.test_lanes import (  # noqa: E402
    CI_CHECK_INVOCATION,
    CI_EVENTS,
    CI_LANE_INVOCATION,
    CI_MATRIX_INVOCATION,
    CI_PUSH_BRANCHES,
    CI_PYTHON_VERSIONS,
    RELEASE_CANDIDATE_EVENT,
    TestLaneError,
    validate_ci_policy,
)

_RUNTIME_SCRIPTS = DEFAULT_REPO_ROOT / SKILL_DIRECTORY / "scripts"
if str(_RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SCRIPTS))

from task_governance_tool import __version__  # noqa: E402
from task_governance_tool.cli import build_parser  # noqa: E402
from task_governance_tool.self_status import inspect_local_package  # noqa: E402
from task_governance_tool.storage import SCHEMA_VERSION  # noqa: E402
from task_governance_tool.viewer import SNAPSHOT_VERSION  # noqa: E402


@dataclass(frozen=True, order=True)
class ContractIssue:
    code: str
    subject: str
    message: str

    def to_data(self) -> dict[str, str]:
        return {
            "code": self.code,
            "subject": self.subject,
            "message": self.message,
        }


@dataclass(frozen=True)
class RuntimeContract:
    package_version: str
    schema_version: int
    snapshot_version: int
    public_commands: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseContractResult:
    runtime: RuntimeContract
    ci_python_versions: tuple[str, ...]
    manifest_core_count: int | None
    tracked_path_count: int | None
    issues: tuple[ContractIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_data(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "runtime": {
                "package_version": self.runtime.package_version,
                "schema_version": self.runtime.schema_version,
                "snapshot_version": self.runtime.snapshot_version,
                "public_commands": list(self.runtime.public_commands),
            },
            "ci_python_versions": list(self.ci_python_versions),
            "manifest_core_count": self.manifest_core_count,
            "tracked_path_count": self.tracked_path_count,
            "issues": [issue.to_data() for issue in self.issues],
        }


def parser_leaf_commands(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
) -> frozenset[str]:
    """Return parser leaves directly from the owning argparse tree."""

    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    if not subparsers:
        return frozenset({" ".join(prefix)})
    leaves: set[str] = set()
    for subparser_action in subparsers:
        for name, child in subparser_action.choices.items():
            leaves.update(parser_leaf_commands(child, (*prefix, name)))
    return frozenset(leaves)


def collect_runtime_contract() -> RuntimeContract:
    """Read release-facing values from their runtime owners."""

    return RuntimeContract(
        package_version=__version__,
        schema_version=SCHEMA_VERSION,
        snapshot_version=SNAPSHOT_VERSION,
        public_commands=tuple(sorted(parser_leaf_commands(build_parser()))),
    )


def _read_utf8(
    path: Path,
    *,
    subject: str,
    issues: list[ContractIssue],
) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        issues.append(
            ContractIssue(
                "required_file_missing",
                subject,
                "required release contract file is unavailable",
            )
        )
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        issues.append(
            ContractIssue(
                "required_file_invalid",
                subject,
                "required release contract file is not UTF-8",
            )
        )
        return None


def _markdown_section(text: str, heading: str) -> tuple[str, ...] | None:
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return None
    level = len(heading) - len(heading.lstrip("#"))
    section: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level and stripped[next_level :].startswith(" "):
                break
        section.append(line)
    return tuple(section)


def _numbered_commands(
    section: Iterable[str],
) -> tuple[str, ...]:
    commands: list[str] = []
    expected_number = 1
    for line in section:
        stripped = line.strip()
        number, separator, value = stripped.partition(". ")
        if not separator or not number.isdecimal():
            continue
        if not (value.startswith("`") and value.endswith("`")):
            continue
        command = value[1:-1]
        if command.startswith("taskgov "):
            command = command[len("taskgov ") :]
        if int(number) != expected_number or not command:
            return ()
        commands.append(command)
        expected_number += 1
    return tuple(commands)


def _documented_commands(section: Iterable[str]) -> tuple[str, ...]:
    lines = tuple(section)
    numbered = _numbered_commands(lines)
    if numbered:
        return numbered

    in_fence = False
    fenced: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence and fenced:
                return tuple(fenced)
            in_fence = not in_fence
            continue
        if not in_fence or not stripped:
            continue
        if stripped.split(" ", 1)[0] not in {
            "setup",
            "doctor",
            "task",
            "handoff",
            "review",
        }:
            return ()
        fenced.append(stripped)
    return ()


def _markdown_table(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if len(cells) != 2:
            continue
        if not cells[0] or set(cells[0]) <= {"-", ":", " "}:
            continue
        if cells[0] in rows:
            continue
        rows[cells[0]] = cells[1]
    return rows


def _parse_ci_triggers(
    text: str,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    lines = text.splitlines()
    on_indexes = [
        index for index, line in enumerate(lines) if line == "on:"
    ]
    if len(on_indexes) != 1:
        return None
    block: list[str] = []
    for line in lines[on_indexes[0] + 1 :]:
        if line and not line.startswith((" ", "\t")):
            break
        if line.strip():
            block.append(line)

    events: list[str] = []
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    for line in block:
        if "\t" in line:
            return None
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 2 and stripped.endswith(":"):
            event = stripped[:-1]
            if not event or event in bodies:
                return None
            events.append(event)
            bodies[event] = []
            current = event
        elif current is None or indent <= 2:
            return None
        else:
            bodies[current].append(stripped)

    if set(events) != set(CI_EVENTS) or len(events) != len(CI_EVENTS):
        return None
    if bodies.get("pull_request") or bodies.get("workflow_dispatch"):
        return None
    push_body = bodies.get("push")
    if push_body is None or not push_body or push_body[0] != "branches:":
        return None
    branches = tuple(
        line[2:].strip()
        for line in push_body[1:]
        if line.startswith("- ")
    )
    if len(branches) != len(push_body) - 1:
        return None
    return tuple(events), branches


def _parse_ci_jobs(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    jobs_indexes = [
        index for index, line in enumerate(lines) if line == "jobs:"
    ]
    if len(jobs_indexes) != 1:
        return None

    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[jobs_indexes[0] + 1 :]:
        if line and not line.startswith((" ", "\t")):
            break
        if "\t" in line:
            return None
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 2 and stripped.endswith(":"):
            job = stripped[:-1]
            if not job or job in jobs:
                return None
            jobs[job] = []
            current = job
        elif current is not None:
            jobs[current].append(line)
        elif stripped:
            return None
    return {
        name: "\n".join(body) + "\n"
        for name, body in jobs.items()
    }


def _parse_named_steps(job_block: str) -> dict[str, tuple[str, ...]] | None:
    steps: dict[str, list[str]] = {}
    current: str | None = None
    for line in job_block.splitlines():
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 6 and stripped.startswith("- name: "):
            name = stripped[len("- name: ") :]
            if not name or name in steps:
                return None
            steps[name] = []
            current = name
        elif current is not None:
            steps[current].append(line)
    return {
        name: tuple(line for line in body if line.strip())
        for name, body in steps.items()
    }


def _job_preamble(job_block: str) -> tuple[str, ...] | None:
    preamble: list[str] = []
    for line in job_block.splitlines():
        if line == "    steps:":
            return tuple(preamble)
        if line.strip():
            preamble.append(line)
    return None


def _parse_skill_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return None
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not separator or not key or not value or key in metadata:
            return None
        metadata[key] = value
    return metadata


def _parse_openai_metadata(text: str) -> dict[str, str] | None:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "interface:":
        return None
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if not line.startswith("  ") or line.startswith("   "):
            return None
        key, separator, raw_value = line.strip().partition(":")
        if not separator or not key or key in metadata:
            return None
        try:
            value = json.loads(raw_value.strip())
        except json.JSONDecodeError:
            return None
        if not isinstance(value, str) or not value:
            return None
        metadata[key] = value
    return metadata


def _git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


def _git_tracked_paths(repo_root: Path) -> tuple[str, ...] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"safe.directory={repo_root.as_posix()}",
                "-C",
                str(repo_root),
                "ls-files",
                "-z",
            ],
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        paths = tuple(
            item.decode("utf-8")
            for item in result.stdout.split(b"\0")
            if item
        )
    except UnicodeDecodeError:
        return None
    return tuple(sorted(paths))


def forbidden_tracked_artifact(path: str) -> bool:
    """Classify generated/local-only paths in the tracked release source."""

    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    components = normalized.split("/")
    name = components[-1]
    lowered = normalized.lower()
    lower_name = name.lower()
    lower_components = tuple(component.lower() for component in components)

    if lowered == "research.md" or lowered.startswith("references/"):
        return True
    if lowered.startswith(f"{SKILL_DIRECTORY}/state/"):
        return True
    if lowered.startswith(f"{SKILL_DIRECTORY}/config/"):
        return True
    if (
        lowered.startswith(".agents/skills/")
        and "/state/" in lowered
    ):
        return True
    if lower_name == "task-viewer.html":
        return True
    if "__pycache__" in lower_components or lower_name.endswith(".pyc"):
        return True
    if lower_name.endswith((".log", ".tmp", ".bak")):
        return True
    if lower_name == ".coverage" or "htmlcov" in lower_components:
        return True
    database_endings = (
        ".sqlite",
        ".sqlite3",
        ".db",
        ".sqlite-wal",
        ".sqlite-shm",
        ".sqlite-journal",
        ".sqlite3-wal",
        ".sqlite3-shm",
        ".sqlite3-journal",
        ".db-wal",
        ".db-shm",
        ".db-journal",
    )
    return lowered.endswith(database_endings)


def _package_checks(
    repo_root: Path,
    runtime: RuntimeContract,
    issues: list[ContractIssue],
) -> int | None:
    skill_root = repo_root / SKILL_DIRECTORY
    status = inspect_local_package(
        skill_root,
        installed_version=runtime.package_version,
    )
    if (
        status.release_origin is not None
        and status.release_origin != EXPECTED_RELEASE_ORIGIN
    ):
        issues.append(
            ContractIssue(
                "release_origin_mismatch",
                f"{SKILL_DIRECTORY}/release-manifest.json",
                "release origin differs from the approved repository identity",
            )
        )
    if status.status == "modified":
        issues.append(
            ContractIssue(
                "package_integrity_mismatch",
                SKILL_DIRECTORY,
                "packaged core differs from the release manifest",
            )
        )
    elif status.status == "unknown":
        code = (
            "manifest_missing"
            if status.unknown_reasons == ("manifest_missing",)
            else "manifest_invalid"
        )
        issues.append(
            ContractIssue(
                code,
                f"{SKILL_DIRECTORY}/release-manifest.json",
                "release manifest could not establish package integrity",
            )
        )

    manifest_path = skill_root / "release-manifest.json"
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest_payload, dict):
        return None
    if manifest_payload.get("release_origin") != EXPECTED_RELEASE_ORIGIN:
        issues.append(
            ContractIssue(
                "release_origin_mismatch",
                f"{SKILL_DIRECTORY}/release-manifest.json",
                "release origin differs from the approved repository identity",
            )
        )
    core_files = manifest_payload.get("core_files")
    if not isinstance(core_files, dict):
        return None
    return len(core_files)


def _tracked_package_inventory_check(
    repo_root: Path,
    tracked_paths: tuple[str, ...] | None,
    issues: list[ContractIssue],
) -> None:
    if tracked_paths is None:
        return
    try:
        manifest = json.loads(
            (repo_root / SKILL_DIRECTORY / "release-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        core_files = manifest["core_files"]
        if not isinstance(core_files, dict):
            return
        expected = {
            f"{SKILL_DIRECTORY}/release-manifest.json",
            *(
                f"{SKILL_DIRECTORY}/{relative}"
                for relative in core_files
                if isinstance(relative, str)
            ),
        }
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return
    actual = {
        path
        for path in tracked_paths
        if path.startswith(f"{SKILL_DIRECTORY}/")
    }
    if actual != expected:
        issues.append(
            ContractIssue(
                "package_tracked_inventory_mismatch",
                SKILL_DIRECTORY,
                "tracked package inventory differs from the release manifest",
            )
        )


def _license_checks(
    repo_root: Path,
    issues: list[ContractIssue],
) -> None:
    root_license = repo_root / "LICENSE"
    package_license = repo_root / SKILL_DIRECTORY / "LICENSE"
    try:
        root_bytes = root_license.read_bytes()
        package_bytes = package_license.read_bytes()
    except OSError:
        issues.append(
            ContractIssue(
                "license_missing",
                "LICENSE",
                "required root or package license is unavailable",
            )
        )
        return

    expected = OFFICIAL_APACHE_2_LICENSE_SHA256
    root_digest = hashlib.sha256(root_bytes).hexdigest()
    package_digest = hashlib.sha256(package_bytes).hexdigest()
    if root_bytes != package_bytes:
        issues.append(
            ContractIssue(
                "license_mismatch",
                "LICENSE",
                "root and package license bytes differ",
            )
        )
    if root_digest != expected or package_digest != expected:
        issues.append(
            ContractIssue(
                "license_not_official",
                "LICENSE",
                "license bytes do not match the approved Apache-2.0 text",
            )
        )

    try:
        manifest = json.loads(
            (repo_root / SKILL_DIRECTORY / "release-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        manifest_license = manifest["core_files"]["LICENSE"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        manifest_license = None
    if manifest_license != f"sha256:{expected}":
        issues.append(
            ContractIssue(
                "license_manifest_mismatch",
                f"{SKILL_DIRECTORY}/release-manifest.json",
                "package license is not covered by the approved digest",
            )
        )

    for relative in ("NOTICE", f"{SKILL_DIRECTORY}/NOTICE"):
        if (repo_root / Path(*relative.split("/"))).exists():
            issues.append(
                ContractIssue(
                    "notice_present",
                    relative,
                    "NOTICE requires a separately reviewed attribution duty",
                )
            )


def _metadata_checks(
    repo_root: Path,
    issues: list[ContractIssue],
) -> None:
    skill_relative = f"{SKILL_DIRECTORY}/SKILL.md"
    skill_text = _read_utf8(
        repo_root / SKILL_DIRECTORY / "SKILL.md",
        subject=skill_relative,
        issues=issues,
    )
    if skill_text is not None:
        frontmatter = _parse_skill_frontmatter(skill_text)
        if (
            frontmatter is None
            or set(frontmatter) != {"name", "description"}
            or frontmatter.get("name") != SKILL_DIRECTORY
            or "task" not in frontmatter.get("description", "").lower()
            or len(skill_text.splitlines()) > 500
        ):
            issues.append(
                ContractIssue(
                    "skill_metadata_invalid",
                    skill_relative,
                    "Skill metadata or progressive-disclosure bound is invalid",
                )
            )

    openai_relative = f"{SKILL_DIRECTORY}/agents/openai.yaml"
    openai_text = _read_utf8(
        repo_root / SKILL_DIRECTORY / "agents" / "openai.yaml",
        subject=openai_relative,
        issues=issues,
    )
    if openai_text is not None:
        metadata = _parse_openai_metadata(openai_text)
        if metadata is None:
            invalid = True
        else:
            short = metadata.get("short_description", "")
            prompt = metadata.get("default_prompt", "")
            invalid = (
                set(metadata)
                != {"display_name", "short_description", "default_prompt"}
                or not 25 <= len(short) <= 64
                or f"${SKILL_DIRECTORY}" not in prompt
                or "task" not in prompt.lower()
            )
        if invalid:
            issues.append(
                ContractIssue(
                    "agent_metadata_invalid",
                    openai_relative,
                    "agent display metadata is invalid or inconsistent",
                )
            )


def _documentation_checks(
    repo_root: Path,
    runtime: RuntimeContract,
    ci_versions: tuple[str, ...],
    issues: list[ContractIssue],
) -> None:
    documents: dict[str, str | None] = {}
    for relative in (
        "README.md",
        "docs/specification.md",
        "docs/design.md",
        "docs/release-install.md",
        f"docs/releases/v{runtime.package_version}.md",
        f"{SKILL_DIRECTORY}/references/cli_contracts.md",
    ):
        documents[relative] = _read_utf8(
            repo_root / Path(*relative.split("/")),
            subject=relative,
            issues=issues,
        )

    command_sections = (
        ("README.md", "## Public Commands"),
        ("docs/specification.md", "### Command Inventory"),
        ("docs/design.md", "### Command Surface"),
        ("docs/release-install.md", "## Public CLI Surface"),
        (
            f"{SKILL_DIRECTORY}/references/cli_contracts.md",
            "## Invocation And Public Inventory",
        ),
    )
    expected_commands = set(runtime.public_commands)
    for relative, heading in command_sections:
        text = documents.get(relative)
        if text is None:
            continue
        section = _markdown_section(text, heading)
        documented = _documented_commands(section or ())
        if (
            section is None
            or not documented
            or len(documented) != len(set(documented))
            or set(documented) != expected_commands
        ):
            issues.append(
                ContractIssue(
                    "documented_cli_mismatch",
                    relative,
                    "documented public command inventory differs from the parser",
                )
            )

    release_record = documents.get("docs/release-install.md")
    if release_record is not None:
        table = _markdown_table(release_record)
        ci_matrix = " and ".join(ci_versions)
        expected_rows = {
            "Package version": f"`{runtime.package_version}`",
            "SQLite schema": f"v{runtime.schema_version}",
            "Public command leaves": str(len(runtime.public_commands)),
            "CI Python matrix": ci_matrix,
        }
        for label, expected in expected_rows.items():
            if table.get(label, "").strip("`") != expected.strip("`"):
                issues.append(
                    ContractIssue(
                        "documented_runtime_mismatch",
                        "docs/release-install.md",
                        "release identity table differs from runtime or CI owners",
                    )
                )
                break
        viewer_value = table.get("Viewer snapshot", "")
        if not viewer_value.startswith(f"v{runtime.snapshot_version},"):
            issues.append(
                ContractIssue(
                    "documented_runtime_mismatch",
                    "docs/release-install.md",
                    "release identity table differs from runtime or CI owners",
                )
            )
        if (
            table.get("Remote/repository")
            != "`origin`, `VAiring/task-governance-tool`"
        ):
            issues.append(
                ContractIssue(
                    "documented_runtime_mismatch",
                    "docs/release-install.md",
                    "release repository identity differs from the approved origin",
                )
            )
        if ci_versions:
            expected_runtime = f"Python {ci_versions[0]} or newer on Windows"
            if table.get("Supported runtime") != expected_runtime:
                issues.append(
                    ContractIssue(
                        "documented_runtime_mismatch",
                        "docs/release-install.md",
                        "release identity table differs from runtime or CI owners",
                    )
                )

    readme = documents.get("README.md")
    if readme is not None:
        expected = (
            f"Release `{runtime.package_version}` uses SQLite schema "
            f"v{runtime.schema_version} and Viewer snapshot "
            f"v{runtime.snapshot_version}"
        )
        if expected not in readme:
            issues.append(
                ContractIssue(
                    "documented_runtime_mismatch",
                    "README.md",
                    "release summary differs from runtime owners",
                )
            )

    for relative in ("docs/specification.md", "docs/design.md"):
        text = documents.get(relative)
        if text is None:
            continue
        normalized = " ".join(text.split())
        expected_tokens = (
            f"v{runtime.package_version}",
            f"SQLite schema v{runtime.schema_version}",
            f"snapshot v{runtime.snapshot_version}",
        )
        if not all(token in normalized for token in expected_tokens):
            issues.append(
                ContractIssue(
                    "documented_runtime_mismatch",
                    relative,
                    "active authority differs from runtime owners",
                )
            )

    release_note_relative = f"docs/releases/v{runtime.package_version}.md"
    release_note = documents.get(release_note_relative)
    if release_note is not None:
        required = (
            f"# {SKILL_DIRECTORY} v{runtime.package_version}",
            f"- SQLite schema: v{runtime.schema_version}",
            f"- Viewer snapshot: v{runtime.snapshot_version},",
            f"- Public CLI: exactly {len(runtime.public_commands)} command leaves",
            f"- CI Python matrix: {' and '.join(ci_versions)}",
        )
        if not all(token in release_note for token in required):
            issues.append(
                ContractIssue(
                    "documented_runtime_mismatch",
                    release_note_relative,
                    "published release note differs from runtime or CI owners",
                )
            )


def _workflow_checks(
    workflow: str | None,
    issues: list[ContractIssue],
) -> tuple[str, ...]:
    if workflow is None:
        return ()
    try:
        validate_ci_policy()
        versions = CI_PYTHON_VERSIONS
    except TestLaneError:
        versions = ()
        issues.append(
            ContractIssue(
                "ci_runtime_matrix_invalid",
                "tools/test_lanes.py",
                "CI event, Python, or lane policy is invalid",
            )
        )
    triggers = _parse_ci_triggers(workflow)
    if triggers is None or triggers[1] != CI_PUSH_BRANCHES:
        issues.append(
            ContractIssue(
                "ci_event_policy_invalid",
                ".github/workflows/ci.yml",
                "CI events or push branches differ from the repository policy",
            )
        )
    run_commands = [
        line.strip()[len("run: ") :]
        for line in workflow.splitlines()
        if line.strip().startswith("run: ")
    ]
    if (
        run_commands.count(CHECKER_INVOCATION) != 1
        or "$requiredFiles" in workflow
        or "$publicTokens" in workflow
        or "Get-FileHash" in workflow
        or "Guard generated artifacts" in workflow
    ):
        issues.append(
            ContractIssue(
                "ci_checker_wiring_invalid",
                ".github/workflows/ci.yml",
                "CI does not delegate release consistency to one checker",
            )
        )
    jobs = _parse_ci_jobs(workflow)
    jobs_valid = (
        jobs is not None
        and tuple(jobs) == ("policy", "test", "release-candidate")
    )
    policy_block = jobs.get("policy", "") if jobs is not None else ""
    test_block = jobs.get("test", "") if jobs is not None else ""
    candidate_block = (
        jobs.get("release-candidate", "") if jobs is not None else ""
    )
    policy_steps = _parse_named_steps(policy_block)
    test_steps = _parse_named_steps(test_block)

    expected_checkout = (
        "        uses: actions/checkout@v6",
        "        with:",
        "          fetch-depth: 0",
        "          persist-credentials: false",
    )
    expected_policy_python = (
        "        uses: actions/setup-python@v6",
        "        with:",
        '          python-version: "3.12"',
    )
    expected_test_python = (
        "        uses: actions/setup-python@v6",
        "        with:",
        "          python-version: ${{ matrix.python-version }}",
    )
    expected_matrix_step = (
        "        id: matrix",
        "        shell: pwsh",
        "        run: |",
        f"          $matrix = {CI_MATRIX_INVOCATION}",
        "          if ($LASTEXITCODE -ne 0) {",
        "            throw 'Test matrix policy failed'",
        "          }",
        (
            '          "matrix=$matrix" | Out-File -FilePath '
            "$env:GITHUB_OUTPUT -Encoding utf8 -Append"
        ),
    )
    expected_policy_preamble = (
        "    name: Repository test policy",
        "    runs-on: windows-latest",
        "    outputs:",
        "      matrix: ${{ steps.matrix.outputs.matrix }}",
    )
    expected_test_preamble = (
        (
            "    name: Windows tests (${{ matrix.lane }}, "
            "Python ${{ matrix.python-version }})"
        ),
        "    needs: policy",
        "    runs-on: windows-latest",
        "    strategy:",
        "      fail-fast: false",
        "      matrix: ${{ fromJSON(needs.policy.outputs.matrix) }}",
    )
    policy_valid = (
        jobs_valid
        and policy_steps is not None
        and tuple(policy_steps)
        == (
            "Checkout",
            "Set up Python",
            "Validate test partition",
            "Plan event test matrix",
            "Check CLI help",
            "Check release contract",
        )
        and _job_preamble(policy_block) == expected_policy_preamble
        and policy_steps.get("Checkout") == expected_checkout
        and policy_steps.get("Set up Python") == expected_policy_python
        and policy_steps.get("Validate test partition")
        == (f"        run: {CI_CHECK_INVOCATION}",)
        and policy_steps.get("Plan event test matrix") == expected_matrix_step
        and policy_steps.get("Check release contract")
        == (f"        run: {CHECKER_INVOCATION}",)
        and "Check CLI help" in policy_steps
    )
    test_valid = (
        jobs_valid
        and test_steps is not None
        and tuple(test_steps)
        == ("Checkout", "Set up Python", "Run test lane")
        and _job_preamble(test_block) == expected_test_preamble
        and test_steps.get("Checkout") == expected_checkout
        and test_steps.get("Set up Python") == expected_test_python
        and test_steps.get("Run test lane")
        == (f"        run: {CI_LANE_INVOCATION}",)
    )
    if (
        not policy_valid
        or not test_valid
        or run_commands.count(CI_CHECK_INVOCATION) != 1
        or run_commands.count(CI_LANE_INVOCATION) != 1
        or "python -m unittest discover -s tests" in workflow
        or "permissions:\n  contents: read\n\njobs:\n" not in workflow
    ):
        issues.append(
            ContractIssue(
                "ci_test_policy_wiring_invalid",
                ".github/workflows/ci.yml",
                "CI does not consume the deterministic test-lane policy",
            )
        )
    expected_candidate = (
        "    name: Full release-candidate gate",
        (
            "    if: ${{ always() && github.event_name == "
            f"'{RELEASE_CANDIDATE_EVENT}' "
            "}}"
        ),
        "    needs:",
        "      - policy",
        "      - test",
        "    runs-on: windows-latest",
        "    steps:",
        "      - name: Require policy and complete test matrix",
        "        shell: pwsh",
        "        env:",
        "          POLICY_RESULT: ${{ needs.policy.result }}",
        "          TEST_RESULT: ${{ needs.test.result }}",
        "        run: |",
        "          if (",
        "            $env:POLICY_RESULT -ne 'success' -or",
        "            $env:TEST_RESULT -ne 'success'",
        "          ) {",
        "            throw 'Full release-candidate gate failed'",
        "          }",
    )
    observed_candidate = tuple(
        line for line in candidate_block.splitlines() if line.strip()
    )
    if observed_candidate != expected_candidate:
        issues.append(
            ContractIssue(
                "ci_candidate_gate_invalid",
                ".github/workflows/ci.yml",
                "CI does not enforce the complete manual release-candidate gate",
            )
        )
    if (
        "runs-on: windows-latest" not in workflow
        or "ubuntu-" in workflow
        or "macos-" in workflow
    ):
        issues.append(
            ContractIssue(
                "ci_platform_invalid",
                ".github/workflows/ci.yml",
                "CI platform differs from the verified Windows boundary",
            )
        )
    return versions


def check_release_contract(
    repo_root: str | os.PathLike[str],
    *,
    tracked_paths: Iterable[str] | None = None,
    runtime_contract: RuntimeContract | None = None,
) -> ReleaseContractResult:
    """Check the source release contract without mutating repository state."""

    root = Path(repo_root).resolve()
    runtime = runtime_contract or collect_runtime_contract()
    issues: list[ContractIssue] = []
    if runtime_contract is None and root != DEFAULT_REPO_ROOT.resolve():
        issues.append(
            ContractIssue(
                "runtime_owner_mismatch",
                "release_contract",
                "selected repository does not own the imported runtime facts",
            )
        )

    workflow = _read_utf8(
        root / ".github" / "workflows" / "ci.yml",
        subject=".github/workflows/ci.yml",
        issues=issues,
    )
    for relative in ("tools/release_contract.py", "tools/test_lanes.py"):
        _read_utf8(
            root / Path(*relative.split("/")),
            subject=relative,
            issues=issues,
        )
    ci_versions = _workflow_checks(workflow, issues)
    observed_paths = (
        tuple(sorted(path.replace("\\", "/") for path in tracked_paths))
        if tracked_paths is not None
        else _git_tracked_paths(root)
    )
    manifest_core_count = _package_checks(root, runtime, issues)
    _tracked_package_inventory_check(root, observed_paths, issues)
    _license_checks(root, issues)
    _metadata_checks(root, issues)
    _documentation_checks(root, runtime, ci_versions, issues)

    if observed_paths is None:
        issues.append(
            ContractIssue(
                "tracked_inventory_unavailable",
                ".git",
                "tracked path inventory could not be read safely",
            )
        )
        tracked_path_count = None
    else:
        tracked_path_count = len(observed_paths)
        for path in observed_paths:
            if forbidden_tracked_artifact(path):
                issues.append(
                    ContractIssue(
                        "generated_artifact_tracked",
                        path,
                        "generated or local-only artifact is tracked",
                    )
                )

    return ReleaseContractResult(
        runtime=runtime,
        ci_python_versions=ci_versions,
        manifest_core_count=manifest_core_count,
        tracked_path_count=tracked_path_count,
        issues=tuple(sorted(set(issues))),
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check repository release contracts offline and read-only."
    )
    parser.add_argument(
        "--repo",
        default=str(DEFAULT_REPO_ROOT),
        help="source repository root",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit deterministic JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    try:
        result = check_release_contract(args.repo)
    except Exception:
        payload = {
            "ok": False,
            "runtime": {},
            "ci_python_versions": [],
            "manifest_core_count": None,
            "tracked_path_count": None,
            "issues": [
                {
                    "code": "checker_internal_error",
                    "subject": "release_contract",
                    "message": "release contract checker could not complete safely",
                }
            ],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print("release contract: FAIL (checker_internal_error)")
        return 1

    if args.json:
        print(json.dumps(result.to_data(), ensure_ascii=False, sort_keys=True))
    elif result.ok:
        print(
            "release contract: PASS "
            f"({len(result.runtime.public_commands)} commands, "
            f"{result.manifest_core_count} manifest core files)"
        )
    else:
        print(f"release contract: FAIL ({len(result.issues)} issue(s))")
        for issue in result.issues:
            print(f"- {issue.code}: {issue.subject}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
