from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import closing
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
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


def remove_v10_maintenance_for_test(connection) -> None:
    """Downgrade a current test database before exercising an older migration."""

    connection.execute(
        "DELETE FROM schema_migrations WHERE version IN (10, 11, 12, 13, 14, 15, 16)"
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
