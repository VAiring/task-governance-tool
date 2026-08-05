from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
from contextlib import closing
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SKILL_ROOT = ROOT / "task-governance-tool"
SOURCE_SCRIPTS_ROOT = SOURCE_SKILL_ROOT / "scripts"
if str(SOURCE_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    MigrationBackupMetadata,
    ProjectIdentity,
    apply_completion_commit_migration,
    apply_completion_evidence_migration,
    apply_effort_advisory_migration,
    apply_git_snapshot_schema_migration,
    apply_handoff_outbox_migration,
    apply_initial_schema_migration,
    apply_managed_backup_generations_migration,
    apply_paused_state_migration,
    apply_project_identity_bindings_migration,
    apply_project_maintenance_migration,
    apply_review_evidence_migration,
    apply_task_checkpoints_migration,
    apply_task_contract_migration,
    apply_viewer_maintenance_migration,
    connect,
    connect_readonly,
    default_db_path,
    default_viewer_output_path,
    ensure_project_meta,
    initialize_database,
    project_identity,
    resolve_database_target,
)
from task_governance_tool.state_resolver import (  # noqa: E402
    observe_current_root,
)


MANIFEST_NAME = "release-manifest.json"
MANIFEST_EXCLUDED_ROOTS = {"adapters", "config", "state"}
_CLI_CAPTURE_LOCK = threading.RLock()


def repository_git_environment() -> dict[str, str]:
    """Return a local-only, noninteractive environment for history fixtures."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


def run_repository_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Run one bounded Git read against this repository without lazy fetch."""

    return subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-C",
            str(ROOT),
            *arguments,
        ],
        env=repository_git_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=60,
    )


def require_repository_git(*arguments: str) -> bytes:
    result = run_repository_git(*arguments)
    if result.returncode != 0:
        stderr_digest = hashlib.sha256(result.stderr).hexdigest()
        raise AssertionError(
            "local Git command failed "
            f"(exit={result.returncode}, stderr_sha256={stderr_digest})"
        )
    return result.stdout


def extract_skill_at_commit(destination: Path, commit: str) -> Path:
    """Materialize one complete tracked skill package from local Git history."""

    require_repository_git("cat-file", "-e", f"{commit}^{{commit}}")
    archive = require_repository_git(
        "archive",
        "--format=tar",
        commit,
        "--",
        "task-governance-tool",
    )
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source.getmembers():
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != "task-governance-tool"
                or any(part in {"", ".", ".."} for part in relative.parts)
                or not (member.isdir() or member.isfile())
            ):
                raise AssertionError("skill archive has an unsupported member")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            extracted = source.extractfile(member)
            if extracted is None:
                raise AssertionError("skill archive file could not be read")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(extracted.read())
    return destination / "task-governance-tool"


def _copy_skill(destination: Path) -> Path:
    copied = destination / "task-governance-tool"
    shutil.copytree(
        SOURCE_SKILL_ROOT,
        copied,
        ignore=shutil.ignore_patterns(
            "config",
            "state",
            "__pycache__",
            "*.pyc",
            ".pytest_cache",
            "*.sqlite",
            "*.sqlite3",
            "*.db",
            "*-wal",
            "*-shm",
            "*-journal",
            "task-viewer.html",
            "*.log",
            "*.tmp",
        ),
    )
    refresh_test_manifest(copied)
    return copied


def _manifest_core_paths(skill_root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in skill_root.rglob("*"):
        relative = path.relative_to(skill_root)
        if relative.parts[0] in MANIFEST_EXCLUDED_ROOTS:
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if relative.as_posix() == MANIFEST_NAME:
            continue
        if path.is_file():
            paths.append(relative)
    return sorted(paths, key=lambda item: item.as_posix())


def refresh_test_manifest(skill_root: Path) -> None:
    manifest_path = skill_root / MANIFEST_NAME
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    source["core_files"] = {
        relative.as_posix(): (
            "sha256:" + hashlib.sha256((skill_root / relative).read_bytes()).hexdigest()
        )
        for relative in _manifest_core_paths(skill_root)
    }
    manifest_path.write_text(
        json.dumps(source, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def file_snapshot(root: Path, *, exclude_state: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if exclude_state and "state" in relative.parts:
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def tree_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture names, kinds, sizes, and contents without following links."""

    snapshot: dict[str, tuple[object, ...]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode):
            snapshot[relative] = ("link", os.readlink(path))
        elif stat.S_ISDIR(details.st_mode):
            snapshot[relative] = ("directory",)
        elif stat.S_ISREG(details.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            snapshot[relative] = (
                "file",
                int(details.st_size),
                digest.hexdigest(),
            )
        else:
            snapshot[relative] = ("other", int(details.st_mode))
    return snapshot


def canonical_test_path(path: Path) -> Path:
    """Normalize one test path to its canonical filesystem spelling."""

    resolved = path.resolve(strict=False)
    return Path(os.path.normcase(os.path.normpath(str(resolved))))


@dataclass(frozen=True)
class PhysicalInstall:
    project_root: Path
    skill_root: Path

    @property
    def entrypoint(self) -> Path:
        return self.skill_root / "scripts" / "taskgov.py"

    @property
    def legacy_project_id(self) -> str:
        return project_identity(self.project_root).project_id

    @property
    def fixed_root(self) -> Path:
        return self.skill_root.resolve() / "state" / "current"

    @property
    def db_path(self) -> Path:
        return self.fixed_root / "taskgov.sqlite"

    @property
    def viewer_path(self) -> Path:
        return self.fixed_root / "viewer" / "task-viewer.html"

    @property
    def legacy_root(self) -> Path:
        return (
            self.skill_root.resolve()
            / "state"
            / "projects"
            / self.legacy_project_id
        )

    @property
    def legacy_db_path(self) -> Path:
        return default_db_path(self.skill_root, self.legacy_project_id)

    @property
    def legacy_target(self) -> DatabaseTarget:
        return resolve_database_target(
            repo=self.project_root,
            db=self.legacy_db_path,
            script_path=self.entrypoint,
        )

    def _fixed_project_id(self) -> str:
        with closing(connect_readonly(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT project_id FROM project_meta"
            ).fetchall()
        if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
            raise AssertionError("fixed test database must contain one project")
        return str(rows[0][0])

    @property
    def project_id(self) -> str:
        if self.db_path.is_file():
            return self._fixed_project_id()
        return self.legacy_project_id

    @property
    def target(self) -> DatabaseTarget:
        # Explicit path injection is an internal repository/test seam. It is not
        # passed to the public parser.
        if not self.db_path.is_file():
            return self.legacy_target
        observed = observe_current_root(self.project_root)
        return DatabaseTarget(
            project=ProjectIdentity(
                project_id=self._fixed_project_id(),
                canonical_repo=observed.canonical_repo,
                canonical_path_hash=observed.canonical_path_hash,
                display_name=observed.display_name,
            ),
            db_path=self.db_path,
            explicit_db=True,
        )

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        isolated: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable]
        if isolated:
            command.extend(["-I", "-S"])
        command.extend([str(self.entrypoint), *map(str, args)])
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        return subprocess.run(
            command,
            cwd=cwd or self.project_root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


@dataclass(frozen=True)
class LegacyPhysicalInstall(PhysicalInstall):
    """M17.1 staging fixture whose canonical runtime target is still legacy."""

    @property
    def db_path(self) -> Path:
        return self.legacy_db_path

    @property
    def viewer_path(self) -> Path:
        return default_viewer_output_path(
            self.skill_root,
            self.legacy_project_id,
        )

    @property
    def target(self) -> DatabaseTarget:
        return self.legacy_target


def make_physical_install(root: Path, *, git_managed: bool = False) -> PhysicalInstall:
    project = root / "project"
    skill_parent = project / ".agents" / "skills"
    skill_parent.mkdir(parents=True)
    skill_root = _copy_skill(skill_parent)
    if git_managed:
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=project,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        (project / ".gitignore").write_text(
            "/.agents/skills/task-governance-tool/state/\n",
            encoding="utf-8",
        )
    return PhysicalInstall(project_root=project, skill_root=skill_root)


def make_legacy_physical_install(
    root: Path,
    *,
    git_managed: bool = False,
) -> LegacyPhysicalInstall:
    install = make_physical_install(root, git_managed=git_managed)
    return LegacyPhysicalInstall(
        project_root=install.project_root,
        skill_root=install.skill_root,
    )


def make_source_self_host(root: Path) -> PhysicalInstall:
    repo = root / "source-repository"
    repo.mkdir(parents=True)
    skill_root = _copy_skill(repo)
    for relative in (
        Path("AGENTS.md"),
        Path("docs/specification.md"),
        Path("docs/design.md"),
        Path("plan.md"),
    ):
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    return PhysicalInstall(project_root=repo, skill_root=skill_root)


def json_payload(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _absolute_test_path(value: str, cwd: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return str(path.resolve(strict=False))


def _prepare_internal_invocation(
    *args: str,
    cwd: Path | None = None,
    script_path: Path | None = None,
) -> tuple[list[str], DatabaseTarget]:
    invocation_cwd = (cwd or SOURCE_SKILL_ROOT).resolve()
    raw = [str(item) for item in args]
    filtered: list[str] = []
    db_value: str | None = None
    repo_value: str | None = None
    index = 0
    while index < len(raw):
        token = raw[index]
        if token == "--db":
            if index + 1 >= len(raw):
                raise AssertionError("test --db requires a value")
            db_value = raw[index + 1]
            index += 2
            continue
        if token.startswith("--db="):
            db_value = token.split("=", 1)[1]
            index += 1
            continue
        if token == "--repo" and index + 1 < len(raw):
            repo_value = raw[index + 1]
            filtered.extend(
                [
                    "--repo",
                    _absolute_test_path(repo_value, invocation_cwd),
                ]
            )
            index += 2
            continue
        if token.startswith("--repo="):
            repo_value = token.split("=", 1)[1]
            filtered.append(
                "--repo=" + _absolute_test_path(repo_value, invocation_cwd)
            )
            index += 1
            continue
        filtered.append(token)
        index += 1

    resolved_repo = _absolute_test_path(repo_value or ".", invocation_cwd)
    if repo_value is None:
        # Internal storage-target tests are not install-layout tests. Supplying
        # the resolved repo keeps relative paths deterministic without chdir.
        filtered = ["--repo", resolved_repo, *filtered]
    resolved_db = (
        _absolute_test_path(db_value, invocation_cwd)
        if db_value is not None
        else None
    )
    target = resolve_database_target(
        repo=resolved_repo,
        db=resolved_db,
        script_path=script_path or SOURCE_SKILL_ROOT / "scripts" / "taskgov.py",
    )
    return filtered, target


def run_taskgov_internal(
    *args: str,
    cwd: Path | None = None,
    script_path: Path | None = None,
    maintenance_enabled: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the public parser with only the private DatabaseTarget test seam.

    The helper strips legacy test-only ``--db`` input before parsing. No
    environment variable, hidden public option, process cwd change, or global
    target monkeypatch is used. Public setup/doctor/layout/removed-option tests
    deliberately use :class:`PhysicalInstall` instead.
    """

    from task_governance_tool import cli as cli_module

    filtered, target = _prepare_internal_invocation(
        *args,
        cwd=cwd,
        script_path=script_path,
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    with _CLI_CAPTURE_LOCK, redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            returncode = cli_module.main(
                filtered,
                _target_override=target,
                _maintenance_enabled=maintenance_enabled,
            )
        except SystemExit as exc:
            returncode = int(exc.code or 0)
    return subprocess.CompletedProcess(
        args=[sys.executable, "scripts/taskgov.py", *filtered],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def run_taskgov_internal_raw(
    *args: str,
    cwd: Path | None = None,
    script_path: Path | None = None,
    maintenance_enabled: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = run_taskgov_internal(
        *args,
        cwd=cwd,
        script_path=script_path,
        maintenance_enabled=maintenance_enabled,
    )
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout.encode("utf-8"),
        stderr=result.stderr.encode("utf-8"),
    )


def internal_command_context(*args: str):
    """Build a CLI context with an explicit private storage target."""

    from task_governance_tool import cli as cli_module

    filtered, target = _prepare_internal_invocation(*args)
    parsed = cli_module.build_parser().parse_args(filtered)
    return cli_module.make_context(parsed, target_override=target)


def initialize_taskgov_internal(
    *,
    repo: str | os.PathLike[str],
    db: str | os.PathLike[str],
) -> dict[str, object]:
    """Initialize an explicitly injected test database without a public command."""

    target = resolve_database_target(
        repo=repo,
        db=db,
        script_path=SOURCE_SKILL_ROOT / "scripts" / "taskgov.py",
    )
    result = initialize_database(target)
    return {
        "project_id": target.project.project_id,
        "created": result.created,
        "schema_version": result.schema_version,
    }


def remove_v18_evidence_ledger_for_test(connection) -> None:
    """Downgrade a current test database to the exact schema-v17 surface."""

    if connection.in_transaction:
        raise AssertionError("schema downgrade fixture requires no active transaction")
    if connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 19"
    ).fetchone() is not None:
        try:
            from m223_test_support import remove_v19_bundle_storage_for_test
        except ModuleNotFoundError:
            from tests.m223_test_support import (
                remove_v19_bundle_storage_for_test,
            )
        remove_v19_bundle_storage_for_test(connection)
    foreign_keys_enabled = bool(
        connection.execute("PRAGMA foreign_keys").fetchone()[0]
    )
    connection.execute("PRAGMA foreign_keys = OFF")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
        raise AssertionError("schema downgrade fixture could not disable foreign keys")
    try:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = 18"
        )
        for trigger in (
            "trg_review_receipts_provenance_basis_insert",
            "trg_verification_receipts_subject_basis_insert",
            "trg_task_completion_cycles_subject_basis_insert",
            "trg_authority_snapshots_no_update",
            "trg_authority_snapshots_no_delete",
            "trg_contract_criteria_no_update",
            "trg_contract_criteria_no_delete",
            "trg_authority_snapshot_criteria_no_update",
            "trg_authority_snapshot_criteria_no_delete",
            "trg_review_receipt_provenance_no_update",
            "trg_review_receipt_provenance_no_delete",
            "trg_review_receipt_provenance_codes_no_update",
            "trg_review_receipt_provenance_codes_no_delete",
            "trg_artifact_manifests_no_update",
            "trg_artifact_manifests_no_delete",
            "trg_artifact_manifest_entries_no_update",
            "trg_artifact_manifest_entries_no_delete",
            "trg_evidence_references_no_update",
            "trg_evidence_references_no_delete",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        for index in (
            "idx_evidence_references_source",
            "idx_artifact_manifests_target",
            "idx_review_provenance_receipt",
            "idx_contract_criteria_task_kind_digest",
            "idx_authority_snapshots_task_generation",
        ):
            connection.execute(f"DROP INDEX {index}")
        for column in (
            "review_target_artifact_manifest_id",
            "review_target_verification_criterion_id",
            "review_target_acceptance_criterion_id",
            "review_target_authority_snapshot_id",
            "review_target_capture_version",
            "current_authority_snapshot_generation",
            "current_authority_snapshot_id",
        ):
            connection.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
        for column in (
            "review_provenance_id",
            "review_provenance_basis_version",
        ):
            connection.execute(
                f"ALTER TABLE review_receipts DROP COLUMN {column}"
            )
        for table in ("verification_receipts", "task_completion_cycles"):
            for column in (
                "subject_verification_criterion_id",
                "subject_authority_snapshot_id",
                "verification_subject_basis_version",
            ):
                connection.execute(
                    f"ALTER TABLE {table} DROP COLUMN {column}"
                )
        for table in (
            "evidence_references",
            "artifact_manifest_entries",
            "authority_snapshot_criteria",
            "review_receipt_provenance_codes",
            "artifact_manifests",
            "review_receipt_provenance",
            "contract_criteria",
            "authority_snapshots",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if foreign_keys_enabled:
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise AssertionError(
                    "schema downgrade fixture could not restore foreign keys"
                )


def remove_v10_maintenance_for_test(connection) -> None:
    """Downgrade a current test database before exercising an older migration."""

    remove_v18_evidence_ledger_for_test(connection)
    connection.execute(
        "DELETE FROM schema_migrations "
        "WHERE version IN (10, 11, 12, 13, 14, 15, 16, 17)"
    )
    for trigger in (
        "trg_task_events_completion_cycle_link_immutable",
        "trg_tasks_completion_history_coverage_immutable",
        "trg_task_completion_cycles_no_delete",
        "trg_task_completion_cycles_no_update",
    ):
        connection.execute(f"DROP TRIGGER {trigger}")
    for index in (
        "idx_task_events_completion_cycle",
        "idx_task_completion_cycles_task_ordinal",
        "idx_review_receipts_completion_cycle_reference",
        "idx_tasks_project_task_identity",
    ):
        connection.execute(f"DROP INDEX {index}")
    connection.execute(
        "ALTER TABLE task_events DROP COLUMN completion_cycle_id"
    )
    connection.execute("DROP TABLE task_completion_cycles")
    connection.execute("DROP TABLE verification_receipts")
    connection.execute(
        "ALTER TABLE tasks DROP COLUMN completion_history_coverage"
    )
    for trigger in (
        "trg_project_meta_identity_immutable",
        "trg_project_meta_no_delete",
        "trg_project_meta_cleanup_insert_valid",
        "trg_project_meta_cleanup_update_valid",
        "trg_project_path_binding_history_no_update",
        "trg_project_path_binding_history_no_delete",
    ):
        connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute("DROP TABLE project_path_binding_history")
    for column in (
        "legacy_cleanup_fingerprint",
        "legacy_cleanup_inventory",
        "legacy_cleanup_pending",
        "binding_updated_at",
        "binding_reason",
        "binding_generation",
        "identity_scheme",
    ):
        connection.execute(f"ALTER TABLE project_meta DROP COLUMN {column}")
    connection.execute("DROP TRIGGER trg_task_events_viewer_generation")
    connection.execute("DROP TABLE viewer_maintenance_state")
    connection.execute("DROP TABLE task_checkpoints")
    connection.execute("DROP TABLE managed_backup_generations")
    connection.execute("DROP TABLE project_maintenance")


def assert_no_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(db_path) + suffix).exists():
            raise AssertionError(f"unexpected SQLite sidecar: {suffix}")


def canonical_managed_sqlite_files(
    install: PhysicalInstall,
    *,
    exclude: Iterable[Path] = (),
) -> list[Path]:
    excluded = {path.resolve(strict=False) for path in exclude}
    state_root = install.skill_root / "state"
    if not state_root.exists():
        return []
    return sorted(
        (
            path
            for path in state_root.rglob("*")
            if path.is_file()
            and path.resolve(strict=False) not in excluded
            and path.suffix in {".sqlite", ".sqlite3", ".db"}
        ),
        key=lambda path: path.as_posix(),
    )


def create_v9_target(target: DatabaseTarget) -> None:
    target.db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(target.db_path)) as connection:
        apply_initial_schema_migration(connection)
        apply_completion_commit_migration(connection)
        ensure_project_meta(connection, target.project)
        connection.commit()
        apply_paused_state_migration(connection)
        apply_completion_evidence_migration(connection)
        apply_review_evidence_migration(connection)
        apply_git_snapshot_schema_migration(connection)
        apply_handoff_outbox_migration(connection)
        apply_task_contract_migration(connection)
        apply_effort_advisory_migration(connection)


def create_v9_database(install: PhysicalInstall) -> None:
    create_v9_target(install.target)


def create_v10_target(
    target: DatabaseTarget,
    *,
    enabled: bool = False,
    setup_backup: MigrationBackupMetadata | None = None,
    interval_minutes: int = 30,
    generations: int = 3,
) -> None:
    """Create a staged schema-v10 fixture without invoking current migration."""

    create_v9_target(target)
    with closing(connect(target.db_path)) as connection:
        apply_project_maintenance_migration(
            connection,
            setup_backup=setup_backup,
        )
        if enabled:
            connection.execute(
                """
                UPDATE project_maintenance
                   SET enabled_at = '2026-07-27T00:00:00Z',
                       backup_interval_minutes = ?,
                       backup_generations = ?
                 WHERE project_id = ?
                """,
                (
                    interval_minutes,
                    generations,
                    target.project.project_id,
                ),
            )
            connection.commit()


def create_v10_database(
    install: PhysicalInstall,
    *,
    enabled: bool = False,
    setup_backup: MigrationBackupMetadata | None = None,
    interval_minutes: int = 30,
    generations: int = 3,
) -> None:
    create_v10_target(
        install.target,
        enabled=enabled,
        setup_backup=setup_backup,
        interval_minutes=interval_minutes,
        generations=generations,
    )


def create_v11_target(
    target: DatabaseTarget,
    *,
    enabled: bool = False,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
    interval_minutes: int = 30,
    generations: int = 3,
) -> None:
    """Create a staged schema-v11 fixture without invoking current migration."""

    create_v10_target(
        target,
        enabled=enabled,
        setup_backup=setup_backup,
        interval_minutes=interval_minutes,
        generations=generations,
    )
    with closing(connect(target.db_path)) as connection:
        apply_managed_backup_generations_migration(
            connection,
            managed_backups=managed_backups,
        )


def create_v11_database(
    install: PhysicalInstall,
    *,
    enabled: bool = False,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
    interval_minutes: int = 30,
    generations: int = 3,
) -> None:
    create_v11_target(
        install.target,
        enabled=enabled,
        setup_backup=setup_backup,
        managed_backups=managed_backups,
        interval_minutes=interval_minutes,
        generations=generations,
    )


def create_v12_target(
    target: DatabaseTarget,
    *,
    enabled: bool = False,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
    interval_minutes: int = 30,
    generations: int = 3,
) -> None:
    """Create a staged schema-v12 fixture without invoking current migration."""

    create_v11_target(
        target,
        enabled=enabled,
        setup_backup=setup_backup,
        managed_backups=managed_backups,
        interval_minutes=interval_minutes,
        generations=generations,
    )
    with closing(connect(target.db_path)) as connection:
        apply_task_checkpoints_migration(connection)


def create_v12_database(
    install: PhysicalInstall,
    *,
    enabled: bool = False,
    setup_backup: MigrationBackupMetadata | None = None,
    managed_backups: tuple[MigrationBackupMetadata, ...] = (),
    interval_minutes: int = 30,
    generations: int = 3,
) -> None:
    create_v12_target(
        install.target,
        enabled=enabled,
        setup_backup=setup_backup,
        managed_backups=managed_backups,
        interval_minutes=interval_minutes,
        generations=generations,
    )


def create_v14_target(target: DatabaseTarget) -> None:
    """Create a complete legacy-layout schema-v14 fixture."""

    create_v12_target(target)
    with closing(connect(target.db_path)) as connection:
        apply_viewer_maintenance_migration(connection)
        apply_project_identity_bindings_migration(connection)
