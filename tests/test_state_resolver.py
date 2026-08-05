from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import SOURCE_SCRIPTS_ROOT, create_v14_target


if str(SOURCE_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_SCRIPTS_ROOT))

from task_governance_tool.state_resolver import (  # noqa: E402
    canonical_state_paths,
    consumer_error_code,
    observe_current_root,
    resolve_project_state,
    resolve_setup_project_state,
    resolve_staged_project_state,
)
from task_governance_tool.project_scope import PROJECT_STATE_MESSAGES  # noqa: E402
from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    MigrationBackupMetadata,
    UnboundDatabaseTarget,
    apply_initial_schema_migration,
    connect,
    ensure_project_meta,
    initialize_database,
    initialize_uuid_database,
    project_identity,
)


UUID_HEX = "00112233445546778899aabbccddeeff"
UUID_PROJECT_ID = f"tg_project_{UUID_HEX}"


def tree_snapshot(root: Path) -> dict[str, tuple[str, int, str]]:
    if not root.exists():
        return {}
    result: dict[str, tuple[str, int, str]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            result[relative] = ("directory", 0, "")
        elif path.is_file():
            data = path.read_bytes()
            result[relative] = (
                "file",
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
        else:
            result[relative] = ("other", 0, "")
    return result


def backup_name(
    *,
    token: str = "1" * 32,
    timestamp: str = "20260729T010203Z",
    retention: int = 3,
) -> str:
    return (
        f"taskgov-backup-v1_{timestamp}_{token}_r{retention}.sqlite"
    )


def copy_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


def backup_metadata(index: int) -> MigrationBackupMetadata:
    return MigrationBackupMetadata(
        generation_id=f"tg_backup_{index:032x}",
        published_at=f"2026-07-29T01:02:{index:02d}Z",
        publication_retention=20,
    )


def backup_path(
    target: DatabaseTarget,
    metadata: MigrationBackupMetadata,
) -> Path:
    compact_time = (
        metadata.published_at.replace("-", "").replace(":", "")
    )
    return target.db_path.parent / "backups" / backup_name(
        token=metadata.generation_id[10:],
        timestamp=compact_time,
        retention=metadata.publication_retention,
    )


def create_backup_artifact(
    target: DatabaseTarget,
    metadata: MigrationBackupMetadata,
) -> Path:
    destination = backup_path(target, metadata)
    copy_sqlite(target.db_path, destination)
    return destination


def insert_generation_rows(
    target: DatabaseTarget,
    metadata_items: tuple[MigrationBackupMetadata, ...],
) -> None:
    with closing(sqlite3.connect(target.db_path)) as connection:
        connection.executemany(
            """
            INSERT INTO managed_backup_generations(
              generation_id, project_id, published_at, publication_retention
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    metadata.generation_id,
                    target.project.project_id,
                    metadata.published_at,
                    metadata.publication_retention,
                )
                for metadata in metadata_items
            ],
        )
        connection.commit()


def set_generation_pointer(
    target: DatabaseTarget,
    metadata: MigrationBackupMetadata,
) -> None:
    with closing(sqlite3.connect(target.db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE project_maintenance
               SET latest_backup_generation_id = ?,
                   backup_last_success_at = ?,
                   applied_backup_generations = ?
             WHERE project_id = ?
            """,
            (
                metadata.generation_id,
                metadata.published_at,
                metadata.publication_retention,
                target.project.project_id,
            ),
        )
        if cursor.rowcount != 1:
            raise AssertionError("resolver fixture maintenance row is missing")
        connection.commit()


def initialize_resolver_layout(
    root: Path,
    layout: str,
) -> tuple[ResolverFixture, DatabaseTarget]:
    fixture = ResolverFixture(root)
    if layout == "fixed":
        return fixture, fixture.initialize_fixed_uuid()
    if layout == "legacy":
        return fixture, fixture.initialize_legacy_v14()
    if layout == "stage":
        target = DatabaseTarget(
            project=project_identity(fixture.repo),
            db_path=(
                fixture.paths.state_root
                / ".taskgov-stage-aaaaaaaa"
                / "taskgov.sqlite"
            ),
            explicit_db=True,
        )
        initialize_database(target)
        return fixture, target
    raise AssertionError(f"unknown test layout: {layout}")


def resolve_layout(
    fixture: ResolverFixture,
    target: DatabaseTarget,
    layout: str,
):
    if layout == "stage":
        return resolve_staged_project_state(
            stage_root=target.db_path.parent,
            repo=fixture.repo,
        )
    return resolve_setup_project_state(
        skill_root=fixture.skill_root,
        repo=fixture.repo,
    )


class ResolverFixture:
    def __init__(self, root: Path) -> None:
        self.skill_root = root / "skill"
        self.repo = root / "project"
        self.skill_root.mkdir()
        self.repo.mkdir()
        self.paths = canonical_state_paths(self.skill_root)

    def legacy_target(self, *, repo: Path | None = None) -> DatabaseTarget:
        identity = project_identity(repo or self.repo)
        return DatabaseTarget(
            project=identity,
            db_path=(
                self.paths.legacy_projects
                / identity.project_id
                / "taskgov.sqlite"
            ),
            explicit_db=True,
        )

    def initialize_legacy_v14(
        self,
        *,
        repo: Path | None = None,
    ) -> DatabaseTarget:
        target = self.legacy_target(repo=repo)
        create_v14_target(target)
        return target

    def initialize_legacy_v1(self) -> DatabaseTarget:
        target = self.legacy_target()
        target.db_path.parent.mkdir(parents=True)
        with closing(connect(target.db_path)) as connection:
            apply_initial_schema_migration(connection)
            ensure_project_meta(connection, target.project)
            connection.commit()
        return target

    def initialize_fixed_uuid(self) -> DatabaseTarget:
        current = observe_current_root(self.repo)
        result = initialize_uuid_database(
            UnboundDatabaseTarget(
                canonical_repo=current.canonical_repo,
                canonical_path_hash=current.canonical_path_hash,
                display_name=current.display_name,
                db_path=self.paths.database,
                explicit_db=True,
            ),
            project_id_factory=lambda: UUID_HEX,
            clock=lambda: "2026-07-29T01:02:03Z",
        )
        return result.target


class StateResolverTests(unittest.TestCase):
    def test_missing_state_is_unbound_and_resolution_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.layout, "missing")
            self.assertEqual(resolution.binding, "unbound")
            self.assertIsNone(resolution.project_id)
            self.assertIsNone(resolution.error_code)
            self.assertEqual(consumer_error_code(resolution), "db_not_initialized")
            self.assertEqual(
                resolution.paths.database,
                fixture.skill_root.resolve()
                / "state"
                / "current"
                / "taskgov.sqlite",
            )
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_fixed_uuid_database_is_authoritative_and_matching(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            fixture.initialize_fixed_uuid()
            legacy = fixture.initialize_legacy_v14()
            unrelated = legacy.db_path.parent / "local.sqlite"
            unrelated.write_bytes(b"opaque")
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.layout, "fixed_current_v1")
            self.assertEqual(resolution.binding, "matching")
            self.assertEqual(resolution.project_id, UUID_PROJECT_ID)
            self.assertEqual(resolution.stored_project.identity_scheme, "uuid_v1")
            self.assertEqual(resolution.source_schema_version, 19)
            self.assertIsNone(consumer_error_code(resolution))
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_fixed_primary_consumer_ignores_non_authoritative_artifact_damage(self):
        for shape in ("corrupt_backup", "invalid_viewer_lock"):
            with (
                self.subTest(shape=shape),
                tempfile.TemporaryDirectory() as temporary,
            ):
                fixture = ResolverFixture(Path(temporary))
                fixture.initialize_fixed_uuid()
                if shape == "corrupt_backup":
                    backup = fixture.paths.backups / backup_name()
                    backup.parent.mkdir(parents=True)
                    backup.write_bytes(b"corrupt non-authoritative backup")
                else:
                    viewer_lock = (
                        fixture.paths.viewer.parent
                        / "taskgov-viewer.lock"
                    )
                    viewer_lock.parent.mkdir(parents=True)
                    viewer_lock.write_bytes(b"XX")
                before = tree_snapshot(fixture.skill_root)

                consumer = resolve_project_state(
                    skill_root=fixture.skill_root,
                    repo=fixture.repo,
                )
                setup = resolve_setup_project_state(
                    skill_root=fixture.skill_root,
                    repo=fixture.repo,
                )

                self.assertEqual(consumer.layout, "fixed_current_v1")
                self.assertEqual(consumer.binding, "matching")
                self.assertEqual(consumer.project_id, UUID_PROJECT_ID)
                self.assertIsNone(consumer.error_code)
                self.assertEqual(
                    setup.error_code,
                    "project_state_unreadable",
                )
                self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_staged_fixed_database_resolves_matching_without_legacy_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "project"
            repo.mkdir()
            stage_root = root / "state" / ".taskgov-stage-aaaaaaaa"
            target = DatabaseTarget(
                project=project_identity(repo),
                db_path=stage_root / "taskgov.sqlite",
                explicit_db=True,
            )
            initialize_database(target)
            before = tree_snapshot(root / "state")

            resolution = resolve_staged_project_state(
                stage_root=stage_root,
                repo=repo,
            )

            self.assertEqual(resolution.layout, "fixed_current_v1")
            self.assertEqual(resolution.binding, "matching")
            self.assertIsNone(resolution.error_code)
            self.assertIsNone(resolution.legacy_source)
            self.assertIsNone(resolution.fixed_recovery)
            self.assertEqual(resolution.project_id, target.project.project_id)
            self.assertEqual(resolution.paths.fixed_root, stage_root.resolve())
            self.assertEqual(before, tree_snapshot(root / "state"))

    def test_staged_missing_primary_fails_without_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "project"
            repo.mkdir()
            state_root = root / "state"
            stage_root = state_root / ".taskgov-stage-aaaaaaaa"
            stage_root.mkdir(parents=True)
            identity = project_identity(repo)
            initialize_database(
                DatabaseTarget(
                    project=identity,
                    db_path=(
                        state_root
                        / "projects"
                        / identity.project_id
                        / "taskgov.sqlite"
                    ),
                    explicit_db=True,
                )
            )
            before = tree_snapshot(state_root)

            resolution = resolve_staged_project_state(
                stage_root=stage_root,
                repo=repo,
            )

            self.assertEqual(
                resolution.error_code,
                "project_state_unreadable",
            )
            self.assertIsNone(resolution.project_id)
            self.assertIsNone(resolution.legacy_source)
            self.assertEqual(before, tree_snapshot(state_root))

    def test_staged_corrupt_or_foreign_backup_fails_closed(self):
        for shape in ("corrupt", "foreign"):
            with self.subTest(shape=shape):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    repo = root / "project"
                    repo.mkdir()
                    state_root = root / "state"
                    stage_root = state_root / ".taskgov-stage-aaaaaaaa"
                    target = DatabaseTarget(
                        project=project_identity(repo),
                        db_path=stage_root / "taskgov.sqlite",
                        explicit_db=True,
                    )
                    initialize_database(target)
                    artifact = backup_path(target, backup_metadata(1))
                    artifact.parent.mkdir(parents=True)
                    if shape == "corrupt":
                        artifact.write_bytes(b"not sqlite")
                    else:
                        foreign_repo = root / "foreign-project"
                        foreign_repo.mkdir()
                        foreign_target = DatabaseTarget(
                            project=project_identity(foreign_repo),
                            db_path=root / "foreign-state" / "taskgov.sqlite",
                            explicit_db=True,
                        )
                        initialize_database(foreign_target)
                        copy_sqlite(foreign_target.db_path, artifact)
                    before = tree_snapshot(root)

                    resolution = resolve_staged_project_state(
                        stage_root=stage_root,
                        repo=repo,
                    )

                    self.assertEqual(
                        resolution.error_code,
                        "project_state_unreadable",
                    )
                    self.assertIsNone(resolution.project_id)
                    self.assertIsNone(resolution.legacy_source)
                    self.assertEqual(before, tree_snapshot(root))

    def test_fixed_legacy_and_stage_share_zero_or_one_byte_lock_rule(self):
        lock_names = (
            "backups/taskgov-backup.lock",
            "viewer/taskgov-viewer.lock",
        )
        for layout in ("fixed", "legacy", "stage"):
            for lock_name in lock_names:
                for size in (0, 1, 2):
                    with self.subTest(
                        layout=layout,
                        lock=lock_name,
                        size=size,
                    ):
                        with tempfile.TemporaryDirectory() as temporary:
                            fixture, target = initialize_resolver_layout(
                                Path(temporary),
                                layout,
                            )
                            lock_path = target.db_path.parent.joinpath(
                                *lock_name.split("/")
                            )
                            lock_path.parent.mkdir(parents=True, exist_ok=True)
                            lock_path.write_bytes(b"x" * size)
                            before = tree_snapshot(fixture.skill_root)

                            resolution = resolve_layout(
                                fixture,
                                target,
                                layout,
                            )

                            if size in {0, 1}:
                                self.assertIsNone(resolution.error_code)
                                self.assertEqual(
                                    resolution.layout,
                                    (
                                        "legacy_projects_v1"
                                        if layout == "legacy"
                                        else "fixed_current_v1"
                                    ),
                                )
                            else:
                                self.assertEqual(
                                    resolution.error_code,
                                    "project_state_unreadable",
                                )
                                self.assertIsNone(resolution.project_id)
                            self.assertEqual(
                                before,
                                tree_snapshot(fixture.skill_root),
                            )

    def test_fixed_legacy_and_stage_share_database_plus_overhead_cap(self):
        for layout in ("fixed", "legacy", "stage"):
            for extra in (0, 1):
                with self.subTest(layout=layout, above_cap=bool(extra)):
                    with tempfile.TemporaryDirectory() as temporary:
                        fixture, target = initialize_resolver_layout(
                            Path(temporary),
                            layout,
                        )
                        viewer = (
                            target.db_path.parent
                            / "viewer"
                            / "task-viewer.html"
                        )
                        viewer.parent.mkdir(parents=True)
                        maximum = (
                            target.db_path.stat().st_size
                            + 16_777_216
                        )
                        with viewer.open("wb") as stream:
                            stream.truncate(maximum + extra)
                        before = tree_snapshot(fixture.skill_root)

                        resolution = resolve_layout(
                            fixture,
                            target,
                            layout,
                        )

                        if extra == 0:
                            self.assertIsNone(resolution.error_code)
                        else:
                            self.assertEqual(
                                resolution.error_code,
                                "project_state_unreadable",
                            )
                            self.assertIsNone(resolution.project_id)
                        self.assertEqual(
                            before,
                            tree_snapshot(fixture.skill_root),
                        )

    def test_missing_primary_journal_rejects_fixed_legacy_and_stage_recovery(self):
        for layout in ("fixed", "legacy", "stage"):
            with self.subTest(layout=layout):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture, target = initialize_resolver_layout(
                        Path(temporary),
                        layout,
                    )
                    create_backup_artifact(target, backup_metadata(1))
                    target.db_path.unlink()
                    journal = Path(f"{target.db_path}-journal")
                    journal.write_bytes(b"x")
                    before = tree_snapshot(fixture.skill_root)

                    resolution = resolve_layout(
                        fixture,
                        target,
                        layout,
                    )

                    self.assertEqual(
                        resolution.error_code,
                        "project_state_unreadable",
                    )
                    self.assertIsNone(resolution.project_id)
                    self.assertEqual(
                        before,
                        tree_snapshot(fixture.skill_root),
                    )

    def test_fixed_database_with_different_root_is_read_only_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            fixture.initialize_fixed_uuid()
            moved = Path(temporary) / "moved-project"
            moved.mkdir()
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=moved,
            )

            self.assertEqual(resolution.layout, "fixed_current_v1")
            self.assertEqual(resolution.binding, "relocation_required")
            self.assertEqual(resolution.project_id, UUID_PROJECT_ID)
            self.assertEqual(
                consumer_error_code(resolution),
                "project_relocation_required",
            )
            self.assertEqual(
                PROJECT_STATE_MESSAGES["project_relocation_required"],
                (
                    "project state is bound to a different project location; "
                    "run setup --read-only"
                ),
            )
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_invalid_fixed_primary_never_falls_back_to_valid_legacy(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            legacy = fixture.initialize_legacy_v14()
            fixture.paths.fixed_root.mkdir(parents=True)
            fixture.paths.database.write_bytes(b"not sqlite")
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.error_code, "project_state_unreadable")
            self.assertEqual(resolution.layout, "missing")
            self.assertIsNone(resolution.project_id)
            self.assertTrue(legacy.db_path.is_file())
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_fixed_backup_only_recovery_is_observed_without_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            fixture.initialize_fixed_uuid()
            backup = fixture.paths.backups / backup_name()
            copy_sqlite(fixture.paths.database, backup)
            fixture.paths.database.unlink()
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.layout, "fixed_current_v1")
            self.assertEqual(resolution.binding, "matching")
            self.assertEqual(resolution.project_id, UUID_PROJECT_ID)
            self.assertIsNotNone(resolution.fixed_recovery)
            self.assertEqual(
                consumer_error_code(resolution),
                "db_not_initialized",
            )
            self.assertFalse(fixture.paths.database.exists())
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_moved_fixed_backup_only_state_is_unreadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            fixture.initialize_fixed_uuid()
            backup = fixture.paths.backups / backup_name()
            copy_sqlite(fixture.paths.database, backup)
            fixture.paths.database.unlink()
            moved = Path(temporary) / "moved-project"
            moved.mkdir()
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=moved,
            )

            self.assertEqual(resolution.error_code, "project_state_unreadable")
            self.assertIsNone(resolution.project_id)
            self.assertFalse(fixture.paths.database.exists())
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_pre_v14_legacy_database_has_implicit_generation_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            target = fixture.initialize_legacy_v1()
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.layout, "legacy_projects_v1")
            self.assertEqual(resolution.binding, "matching")
            self.assertEqual(resolution.project_id, target.project.project_id)
            self.assertEqual(resolution.source_schema_version, 1)
            self.assertEqual(
                resolution.stored_project.binding_lineage,
                (target.project.canonical_path_hash,),
            )
            self.assertEqual(
                consumer_error_code(resolution),
                "migration_required",
            )
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_v14_legacy_database_is_accepted_only_for_legacy_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            legacy = fixture.initialize_legacy_v14()

            accepted = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(accepted.layout, "legacy_projects_v1")
            self.assertEqual(accepted.binding, "matching")
            self.assertEqual(accepted.project_id, legacy.project.project_id)
            self.assertEqual(accepted.source_schema_version, 14)
            self.assertEqual(consumer_error_code(accepted), "migration_required")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            fixture.initialize_fixed_uuid()
            invalid_candidate = (
                fixture.paths.legacy_projects / UUID_PROJECT_ID
            )
            invalid_candidate.parent.mkdir(parents=True)
            fixture.paths.fixed_root.rename(invalid_candidate)

            rejected = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )
            self.assertEqual(rejected.error_code, "project_state_unreadable")
            self.assertIsNone(rejected.project_id)

    def test_moved_primary_legacy_is_observed_but_backup_only_is_unreadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            original = Path(temporary) / "original-project"
            original.mkdir()
            target = fixture.initialize_legacy_v14(repo=original)
            moved = fixture.repo
            before = tree_snapshot(fixture.skill_root)

            primary = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=moved,
            )

            self.assertEqual(primary.layout, "legacy_projects_v1")
            self.assertEqual(primary.binding, "relocation_required")
            self.assertEqual(primary.project_id, target.project.project_id)
            self.assertEqual(
                consumer_error_code(primary),
                "project_relocation_required",
            )
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

            backup = target.db_path.parent / "backups" / backup_name()
            copy_sqlite(target.db_path, backup)
            target.db_path.unlink()
            backup_before = tree_snapshot(fixture.skill_root)

            backup_only = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=moved,
            )

            self.assertEqual(
                backup_only.error_code,
                "project_state_unreadable",
            )
            self.assertIsNone(backup_only.project_id)
            self.assertEqual(backup_before, tree_snapshot(fixture.skill_root))

    def test_candidate_basename_mismatch_is_project_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            target = fixture.initialize_legacy_v14()
            wrong = target.db_path.parent.with_name("wrong-project")
            target.db_path.parent.rename(wrong)
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.error_code, "project_mismatch")
            self.assertIsNone(resolution.project_id)
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_primary_present_accepts_each_bounded_backup_crash_relation(self):
        forms = (
            ("pre-row file-only", True, False),
            ("post-row coherent", True, True),
            ("single row-only", False, True),
        )
        for label, artifact_present, row_present in forms:
            with self.subTest(form=label):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = ResolverFixture(Path(temporary))
                    target = fixture.initialize_legacy_v14()
                    metadata = backup_metadata(1)
                    if artifact_present:
                        create_backup_artifact(target, metadata)
                    if row_present:
                        insert_generation_rows(target, (metadata,))
                        set_generation_pointer(target, metadata)
                    before = tree_snapshot(fixture.skill_root)

                    resolution = resolve_project_state(
                        skill_root=fixture.skill_root,
                        repo=fixture.repo,
                    )

                    self.assertEqual(
                        resolution.layout,
                        "legacy_projects_v1",
                    )
                    self.assertEqual(resolution.binding, "matching")
                    self.assertEqual(
                        len(resolution.legacy_source.managed_backups),
                        1 if artifact_present else 0,
                    )
                    self.assertEqual(
                        consumer_error_code(resolution),
                        "migration_required",
                    )
                    self.assertEqual(
                        before,
                        tree_snapshot(fixture.skill_root),
                    )

    def test_primary_accepts_twenty_retained_plus_one_before_and_after_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            target = fixture.initialize_legacy_v14()
            for metadata in map(backup_metadata, range(1, 21)):
                create_backup_artifact(target, metadata)
                insert_generation_rows(target, (metadata,))
                set_generation_pointer(target, metadata)
            in_flight = backup_metadata(21)
            create_backup_artifact(target, in_flight)
            before_pre_row = tree_snapshot(fixture.skill_root)

            pre_row = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(pre_row.layout, "legacy_projects_v1")
            self.assertEqual(
                len(pre_row.legacy_source.managed_backups),
                21,
            )
            self.assertEqual(
                before_pre_row,
                tree_snapshot(fixture.skill_root),
            )

            insert_generation_rows(target, (in_flight,))
            set_generation_pointer(target, in_flight)
            before_post_row = tree_snapshot(fixture.skill_root)
            post_row = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(post_row.layout, "legacy_projects_v1")
            self.assertEqual(
                len(post_row.legacy_source.managed_backups),
                21,
            )
            self.assertEqual(
                before_post_row,
                tree_snapshot(fixture.skill_root),
            )

    def test_missing_primary_accepts_newest_file_only_and_missing_older_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            target = fixture.initialize_legacy_v14()
            for metadata in map(backup_metadata, range(1, 21)):
                insert_generation_rows(target, (metadata,))
                set_generation_pointer(target, metadata)
            newest = backup_metadata(21)
            newest_path = create_backup_artifact(target, newest)
            target.db_path.unlink()
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.layout, "legacy_projects_v1")
            self.assertEqual(resolution.binding, "matching")
            self.assertFalse(resolution.legacy_source.primary_present)
            self.assertEqual(
                resolution.legacy_source.source_database,
                newest_path,
            )
            self.assertEqual(
                [
                    item.metadata.generation_id
                    for item in resolution.legacy_source.managed_backups
                ],
                [newest.generation_id],
            )
            self.assertEqual(
                consumer_error_code(resolution),
                "migration_required",
            )
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_invalid_backup_generation_envelopes_fail_closed(self):
        cases = (
            "second file-only",
            "second row-only",
            "twenty-two rows",
            "twenty-two union identities",
        )
        for shape in cases:
            with self.subTest(shape=shape):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = ResolverFixture(Path(temporary))
                    target = fixture.initialize_legacy_v14()
                    if shape == "second file-only":
                        create_backup_artifact(target, backup_metadata(1))
                        create_backup_artifact(target, backup_metadata(2))
                    elif shape == "second row-only":
                        insert_generation_rows(
                            target,
                            (backup_metadata(1), backup_metadata(2)),
                        )
                    elif shape == "twenty-two rows":
                        insert_generation_rows(
                            target,
                            tuple(map(backup_metadata, range(1, 23))),
                        )
                    else:
                        insert_generation_rows(
                            target,
                            tuple(map(backup_metadata, range(1, 22))),
                        )
                        create_backup_artifact(
                            target,
                            backup_metadata(22),
                        )
                    before = tree_snapshot(fixture.skill_root)

                    resolution = resolve_project_state(
                        skill_root=fixture.skill_root,
                        repo=fixture.repo,
                    )

                    self.assertEqual(
                        resolution.error_code,
                        "project_state_unreadable",
                    )
                    self.assertIsNone(resolution.project_id)
                    self.assertEqual(
                        before,
                        tree_snapshot(fixture.skill_root),
                    )

    def test_zero_multiple_and_sixty_five_legacy_entries_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            fixture.paths.legacy_projects.mkdir(parents=True)
            empty = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )
            self.assertEqual(empty.layout, "missing")

            (fixture.paths.legacy_projects / "one").mkdir()
            (fixture.paths.legacy_projects / "two").mkdir()
            multiple = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )
            self.assertEqual(
                multiple.error_code,
                "project_state_unreadable",
            )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            fixture.paths.legacy_projects.mkdir(parents=True)
            for index in range(65):
                (fixture.paths.legacy_projects / f"entry-{index:02d}").mkdir()
            over_limit = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )
            self.assertEqual(
                over_limit.error_code,
                "project_state_unreadable",
            )

    def test_unrelated_candidate_entries_and_duplicate_temporaries_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            target = fixture.initialize_legacy_v14()
            candidate = target.db_path.parent
            unrelated = candidate / "private.sqlite"
            unrelated.write_bytes(b"do not inspect or remove")
            first = candidate / ".taskgov-restore-aaaaaaaa.tmp"
            second = candidate / ".taskgov-restore-bbbbbbbb.tmp"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(resolution.layout, "legacy_projects_v1")
            self.assertNotIn(
                first.name,
                resolution.legacy_source.recognized_entries,
            )
            self.assertNotIn(
                second.name,
                resolution.legacy_source.recognized_entries,
            )
            self.assertEqual(before, tree_snapshot(fixture.skill_root))

    def test_one_bounded_temporary_per_class_is_recognized_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResolverFixture(Path(temporary))
            target = fixture.initialize_legacy_v14()
            candidate = target.db_path.parent
            root_temp = candidate / ".taskgov-restore-aaaaaaaa.tmp"
            backup_temp = (
                candidate / "backups" / ".taskgov-backup-bbbbbbbb.tmp"
            )
            viewer_temp = (
                candidate / "viewer" / ".task-viewer-cccccccc.tmp"
            )
            for path in (root_temp, backup_temp, viewer_temp):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"bounded")
            before = tree_snapshot(fixture.skill_root)

            resolution = resolve_project_state(
                skill_root=fixture.skill_root,
                repo=fixture.repo,
            )

            self.assertEqual(
                set(resolution.legacy_source.recognized_entries),
                {
                    "taskgov.sqlite",
                    root_temp.name,
                    f"backups/{backup_temp.name}",
                    f"viewer/{viewer_temp.name}",
                },
            )
            self.assertEqual(before, tree_snapshot(fixture.skill_root))


if __name__ == "__main__":
    unittest.main()
