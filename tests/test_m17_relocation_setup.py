from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

try:
    from m14_test_support import (
        PhysicalInstall,
        create_v14_target,
        json_payload,
        make_physical_install,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        PhysicalInstall,
        create_v14_target,
        json_payload,
        make_physical_install,
    )

from task_governance_tool import setup as setup_service
from task_governance_tool.relocation import (
    RelocationContext,
    decode_relocation_token,
    encode_relocation_token,
    relocation_token_digest,
    relocation_token_expiry,
)
from task_governance_tool.state_resolver import resolve_setup_project_state
from task_governance_tool.state_transition import cleanup_roots
from task_governance_tool.storage import (
    initialize_uuid_database,
    project_identity,
)


ISSUED_AT = "2026-07-29T01:02:03Z"
CONFIRMED_AT = "2026-07-29T01:02:04Z"
SECOND_ISSUED_AT = "2026-07-29T01:03:03Z"
SECOND_CHECKED_AT = "2026-07-29T01:03:04Z"

RELOCATION_KEYS = {
    "required",
    "source_layout",
    "identity_scheme",
    "binding_generation",
    "confirmation_token",
    "expires_at",
}
FIXED_WRITES = [
    "project_binding_update",
    "viewer_publish",
]
LEGACY_WRITES = [
    "legacy_state_publish",
    "migration_backup",
    "database_migrate",
    "maintenance_configure",
    "project_binding_update",
    "viewer_publish",
    "legacy_state_cleanup",
]
RELOCATION_MESSAGES = {
    "project_relocation_required": (
        "project state is bound to a different project location; "
        "run setup --read-only"
    ),
    "relocation_token_invalid": "relocation confirmation is invalid",
    "relocation_token_expired": (
        "relocation confirmation has expired; run setup --read-only again"
    ),
    "relocation_token_stale": (
        "project relocation state changed; run setup --read-only again"
    ),
    "relocation_token_used": (
        "relocation confirmation has already been used"
    ),
    "relocation_not_required": "project relocation is not required",
}


def tree_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    result: dict[str, tuple[object, ...]] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode):
            result[relative] = ("link", os.readlink(path))
        elif stat.S_ISDIR(details.st_mode):
            result[relative] = ("directory",)
        elif stat.S_ISREG(details.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            result[relative] = (
                "file",
                int(details.st_size),
                digest.hexdigest(),
            )
        else:
            result[relative] = (
                "other",
                int(details.st_mode),
                int(details.st_size),
            )
    return result


def relocate_install(
    install: PhysicalInstall,
    *,
    destination: Path,
) -> PhysicalInstall:
    install.project_root.rename(destination)
    return PhysicalInstall(
        project_root=destination,
        skill_root=(
            destination
            / ".agents"
            / "skills"
            / "task-governance-tool"
        ),
    )


def make_moved_fixed_install(root: Path) -> PhysicalInstall:
    install = make_physical_install(root)
    initialized = install.run("setup", "--json")
    if initialized.returncode != 0:
        raise AssertionError(initialized.stderr or initialized.stdout)
    return relocate_install(
        install,
        destination=root / "moved-project",
    )


def make_moved_unconfigured_fixed_install(root: Path) -> PhysicalInstall:
    install = make_physical_install(root)
    resolution = resolve_setup_project_state(
        skill_root=install.skill_root,
        repo=install.project_root,
    )
    initialize_uuid_database(setup_service._unbound_target(resolution))
    return relocate_install(
        install,
        destination=root / "moved-project",
    )


def make_moved_legacy_install(root: Path) -> PhysicalInstall:
    install = make_physical_install(root)
    create_v14_target(install.legacy_target)
    return relocate_install(
        install,
        destination=root / "moved-project",
    )


def relocation_context(install: PhysicalInstall) -> RelocationContext:
    resolution = resolve_setup_project_state(
        skill_root=install.skill_root,
        repo=install.project_root,
    )
    stored = resolution.stored_project
    if stored is None or resolution.binding != "relocation_required":
        raise AssertionError("fixture must resolve as relocation-required")
    return RelocationContext(
        project_id=stored.project_id,
        identity_scheme=stored.identity_scheme,
        binding_generation=stored.binding_generation,
        old_path_hash=stored.canonical_path_hash,
        new_path_hash=resolution.current_root.canonical_path_hash,
        source_layout=resolution.layout,
        source_schema_version=stored.source_schema_version,
    )


def run_service_setup(
    install: PhysicalInstall,
    *,
    read_only: bool,
    confirmation_token: str | None = None,
    now: str,
):
    with mock.patch.object(
        setup_service,
        "utc_now",
        return_value=now,
    ):
        return setup_service.run_setup(
            repo=str(install.project_root),
            repo_explicit=True,
            script_path=install.entrypoint,
            read_only=read_only,
            backup_interval_minutes=None,
            backup_generations=None,
            confirmation_token=confirmation_token,
        )


def configure_viewer_refresh(
    install: PhysicalInstall,
    *,
    interval_seconds: int = 7,
) -> None:
    config_root = install.skill_root / "config"
    config_root.mkdir(exist_ok=True)
    (config_root / "viewer.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": "visibility-refresh-v1",
                "refresh_interval_seconds": interval_seconds,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def assert_viewer_presentation_contract(
    case: unittest.TestCase,
    install: PhysicalInstall,
    *,
    interval_seconds: int = 7,
) -> None:
    html = install.viewer_path.read_text(encoding="utf-8")
    case.assertIn(
        (
            "data-taskgov-refresh-interval-seconds="
            f'"{interval_seconds}"'
        ),
        html,
    )
    case.assertIn(
        'const reloadStateOwner = "taskgov-viewer-auto-reload";',
        html,
    )
    case.assertIn('window.history.replaceState(candidate, "");', html)
    case.assertIn(
        'window.history.scrollRestoration = "manual";',
        html,
    )


class M17RelocationSetupTests(unittest.TestCase):
    def assert_cli_error(
        self,
        payload: dict,
        code: str,
    ) -> dict:
        self.assertEqual(
            payload["errors"],
            [{"code": code, "message": RELOCATION_MESSAGES[code]}],
        )
        self.assertEqual(payload["warnings"], [])
        data = payload["data"]
        self.assertEqual(set(data["relocation"]), RELOCATION_KEYS)
        self.assertIsNone(data["status"])
        return data

    def assert_service_error(
        self,
        result,
        code: str,
        *,
        required: bool,
        rejected_token: str,
    ) -> None:
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, code)
        self.assertEqual(result.error_message, RELOCATION_MESSAGES[code])
        self.assertEqual(result.data["planned_writes"], [])
        self.assertEqual(result.data["completed_writes"], [])
        self.assertIsNone(result.data["status"])
        self.assertEqual(result.data["schema_from"], 15)
        self.assertEqual(result.data["schema_to"], 15)
        self.assertTrue(result.data["maintenance_enabled"])
        self.assertEqual(result.data["backup_interval_minutes"], 30)
        self.assertEqual(result.data["backup_generations"], 3)
        self.assertEqual(result.data["viewer_status"], "current")
        relocation = result.data["relocation"]
        self.assertEqual(set(relocation), RELOCATION_KEYS)
        self.assertEqual(
            relocation,
            {
                "required": required,
                "source_layout": "fixed_current_v1",
                "identity_scheme": "uuid_v1",
                "binding_generation": 1 if required else 2,
                "confirmation_token": None,
                "expires_at": None,
            },
        )
        serialized = json.dumps(
            {
                "project_id": result.project_id,
                "data": result.data,
                "error_code": result.error_code,
                "error_message": result.error_message,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        self.assertNotIn(rejected_token, serialized)
        self.assertNotIn(str(Path("C:/private/moved-project")), serialized)

    def test_viewer_refresh_and_history_contract_survive_rebind_and_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = make_physical_install(root)
            configure_viewer_refresh(install)
            initialized = run_service_setup(
                install,
                read_only=False,
                now=ISSUED_AT,
            )
            self.assertTrue(initialized.ok, initialized)
            assert_viewer_presentation_contract(self, install)

            moved = relocate_install(
                install,
                destination=root / "moved-project",
            )
            preview = run_service_setup(
                moved,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            confirmed = run_service_setup(
                moved,
                read_only=False,
                confirmation_token=token,
                now=CONFIRMED_AT,
            )
            self.assertTrue(confirmed.ok, confirmed)
            assert_viewer_presentation_contract(self, moved)

            moved.viewer_path.unlink()
            repaired = run_service_setup(
                moved,
                read_only=False,
                now=SECOND_CHECKED_AT,
            )
            self.assertTrue(repaired.ok, repaired)
            assert_viewer_presentation_contract(self, moved)

    def test_viewer_refresh_and_history_contract_survive_legacy_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary))
            configure_viewer_refresh(install)
            create_v14_target(install.legacy_target)

            migrated = run_service_setup(
                install,
                read_only=False,
                now=ISSUED_AT,
            )

            self.assertTrue(migrated.ok, migrated)
            self.assertEqual(
                migrated.data["completed_writes"],
                [
                    "legacy_state_publish",
                    "migration_backup",
                    "database_migrate",
                    "maintenance_configure",
                    "viewer_publish",
                    "legacy_state_cleanup",
                ],
            )
            assert_viewer_presentation_contract(self, install)

    def test_fixed_preview_and_no_token_error_are_exact_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_fixed_install(Path(temporary))
            context = relocation_context(install)
            expected_token = encode_relocation_token(
                context,
                issued_at=ISSUED_AT,
            )
            expected_expiry = relocation_token_expiry(ISSUED_AT)
            before = tree_snapshot(install.project_root)

            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )

            self.assertTrue(preview.ok)
            self.assertEqual(preview.project_id, context.project_id)
            self.assertEqual(
                preview.data,
                {
                    "status": "relocation_preview",
                    "planned_writes": FIXED_WRITES,
                    "completed_writes": [],
                    "schema_from": 15,
                    "schema_to": 15,
                    "maintenance_enabled": True,
                    "backup_interval_minutes": 30,
                    "backup_generations": 3,
                    "viewer_status": "current",
                    "relocation": {
                        "required": True,
                        "source_layout": "fixed_current_v1",
                        "identity_scheme": "uuid_v1",
                        "binding_generation": 1,
                        "confirmation_token": expected_token,
                        "expires_at": expected_expiry,
                    },
                },
            )
            self.assertEqual(tree_snapshot(install.project_root), before)

            cli_preview = install.run(
                "setup",
                "--read-only",
                "--json",
            )
            self.assertEqual(cli_preview.returncode, 0, cli_preview.stderr)
            cli_payload = json_payload(cli_preview)
            cli_relocation = cli_payload["data"]["relocation"]
            self.assertEqual(set(cli_relocation), RELOCATION_KEYS)
            self.assertEqual(
                cli_payload["data"]["planned_writes"],
                FIXED_WRITES,
            )
            cli_claims = decode_relocation_token(
                cli_relocation["confirmation_token"],
                now=cli_relocation["expires_at"],
            )
            self.assertEqual(cli_claims.context, context)
            self.assertEqual(tree_snapshot(install.project_root), before)

            no_token = install.run("setup", "--json")
            self.assertEqual(no_token.returncode, 2)
            no_token_data = self.assert_cli_error(
                json_payload(no_token),
                "project_relocation_required",
            )
            self.assertEqual(no_token_data["planned_writes"], FIXED_WRITES)
            self.assertEqual(no_token_data["completed_writes"], [])
            self.assertEqual(
                no_token_data["relocation"],
                {
                    "required": True,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "uuid_v1",
                    "binding_generation": 1,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )
            self.assertEqual(tree_snapshot(install.project_root), before)

    def test_fixed_cli_confirmation_replay_repair_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_fixed_install(Path(temporary))
            preview = json_payload(
                install.run("setup", "--read-only", "--json")
            )
            token = preview["data"]["relocation"]["confirmation_token"]
            claims = decode_relocation_token(
                token,
                now=preview["data"]["relocation"]["expires_at"],
            )

            confirmed_process = install.run(
                "setup",
                "--confirm-relocation",
                token,
                "--json",
            )

            self.assertEqual(
                confirmed_process.returncode,
                0,
                confirmed_process.stderr,
            )
            confirmed = json_payload(confirmed_process)
            self.assertEqual(confirmed["errors"], [])
            self.assertEqual(
                confirmed["data"]["planned_writes"],
                FIXED_WRITES,
            )
            self.assertEqual(
                confirmed["data"]["completed_writes"],
                FIXED_WRITES,
            )
            self.assertEqual(
                confirmed["data"]["relocation"],
                {
                    "required": False,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "uuid_v1",
                    "binding_generation": 2,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )

            expected_hash = project_identity(
                install.project_root
            ).canonical_path_hash
            with closing(sqlite3.connect(install.db_path)) as connection:
                current = connection.execute(
                    """
                    SELECT binding_generation, canonical_path_hash,
                           binding_reason
                      FROM project_meta
                    """
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT binding_generation, previous_path_hash,
                           canonical_path_hash, reason,
                           confirmation_token_digest
                      FROM project_path_binding_history
                     ORDER BY binding_generation
                    """
                ).fetchall()
                viewer = connection.execute(
                    """
                    SELECT source_generation, rendered_generation,
                           last_outcome_code
                      FROM viewer_maintenance_state
                    """
                ).fetchone()
            self.assertEqual(
                current,
                (2, expected_hash, "confirmed_relocation"),
            )
            self.assertEqual(len(history), 2)
            self.assertEqual(history[1][1], history[0][2])
            self.assertEqual(history[1][2], expected_hash)
            self.assertEqual(history[1][3], "confirmed_relocation")
            self.assertEqual(
                history[1][4],
                relocation_token_digest(token),
            )
            self.assertEqual(viewer[0], viewer[1])
            self.assertEqual(viewer[2], "succeeded")

            before_replay = tree_snapshot(install.project_root)
            replay = run_service_setup(
                install,
                read_only=False,
                confirmation_token=token,
                now=claims.expires_at,
            )
            self.assert_service_error(
                replay,
                "relocation_token_used",
                required=False,
                rejected_token=token,
            )
            self.assertEqual(
                tree_snapshot(install.project_root),
                before_replay,
            )

            install.viewer_path.unlink()
            repaired_process = install.run("setup", "--json")
            self.assertEqual(
                repaired_process.returncode,
                0,
                repaired_process.stderr,
            )
            repaired = json_payload(repaired_process)
            self.assertEqual(
                repaired["data"]["planned_writes"],
                ["viewer_publish"],
            )
            self.assertEqual(
                repaired["data"]["completed_writes"],
                ["viewer_publish"],
            )
            self.assertTrue(install.viewer_path.is_file())

            idempotent_process = install.run("setup", "--json")
            self.assertEqual(
                idempotent_process.returncode,
                0,
                idempotent_process.stderr,
            )
            idempotent = json_payload(idempotent_process)["data"]
            self.assertEqual(idempotent["status"], "already_setup")
            self.assertEqual(idempotent["planned_writes"], [])
            self.assertEqual(idempotent["completed_writes"], [])
            self.assertEqual(idempotent["relocation"]["required"], False)
            self.assertEqual(
                idempotent["relocation"]["binding_generation"],
                2,
            )

    def test_fixed_viewer_failure_consumes_binding_then_repairs_without_token(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_fixed_install(Path(temporary))
            context = relocation_context(install)
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            last_good = install.viewer_path.read_bytes()

            with mock.patch.object(
                setup_service,
                "_publish_viewer",
                side_effect=setup_service.ViewerError(
                    "output_write_failed",
                    "injected post-binding Viewer failure",
                ),
            ):
                failed = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(failed.error_code, "setup_incomplete")
            self.assertEqual(
                failed.error_message,
                "setup completed only partially; rerun setup",
            )
            self.assertEqual(failed.data["planned_writes"], FIXED_WRITES)
            self.assertEqual(
                failed.data["completed_writes"],
                ["project_binding_update"],
            )
            self.assertEqual(
                failed.data["relocation"],
                {
                    "required": False,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "uuid_v1",
                    "binding_generation": 2,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )
            self.assertEqual(install.viewer_path.read_bytes(), last_good)

            expected_hash = project_identity(
                install.project_root
            ).canonical_path_hash
            with closing(sqlite3.connect(install.db_path)) as connection:
                current = connection.execute(
                    """
                    SELECT binding_generation, canonical_path_hash,
                           binding_reason
                      FROM project_meta
                    """
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT binding_generation, confirmation_token_digest
                      FROM project_path_binding_history
                     ORDER BY binding_generation
                    """
                ).fetchall()
            self.assertEqual(
                current,
                (2, expected_hash, "confirmed_relocation"),
            )
            self.assertEqual(len(history), 2)
            self.assertEqual(history[1][0], 2)
            self.assertEqual(
                history[1][1],
                relocation_token_digest(token),
            )

            before_replay = tree_snapshot(install.project_root)
            replay = run_service_setup(
                install,
                read_only=False,
                confirmation_token=token,
                now=CONFIRMED_AT,
            )
            self.assertFalse(replay.ok)
            self.assertEqual(replay.error_code, "relocation_token_used")
            self.assertEqual(replay.data["planned_writes"], [])
            self.assertEqual(replay.data["completed_writes"], [])
            self.assertEqual(
                replay.data["relocation"]["binding_generation"],
                2,
            )
            self.assertFalse(replay.data["relocation"]["required"])
            self.assertEqual(
                tree_snapshot(install.project_root),
                before_replay,
            )

            repaired = run_service_setup(
                install,
                read_only=False,
                now=SECOND_CHECKED_AT,
            )
            self.assertTrue(repaired.ok)
            self.assertEqual(
                repaired.data["planned_writes"],
                ["viewer_publish"],
            )
            self.assertEqual(
                repaired.data["completed_writes"],
                ["viewer_publish"],
            )
            self.assertEqual(repaired.data["viewer_status"], "published")
            self.assertNotEqual(install.viewer_path.read_bytes(), last_good)

    def test_fixed_partial_failure_reports_durable_maintenance_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_unconfigured_fixed_install(Path(temporary))
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)

            with mock.patch.object(
                setup_service,
                "_publish_viewer",
                side_effect=setup_service.ViewerError(
                    "output_write_failed",
                    "injected post-binding Viewer failure",
                ),
            ):
                failed = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(failed.error_code, "setup_incomplete")
            self.assertEqual(
                failed.data["planned_writes"],
                [
                    "maintenance_configure",
                    "project_binding_update",
                    "viewer_publish",
                ],
            )
            self.assertEqual(
                failed.data["completed_writes"],
                ["maintenance_configure", "project_binding_update"],
            )
            self.assertTrue(failed.data["maintenance_enabled"])
            self.assertEqual(
                failed.data["relocation"],
                {
                    "required": False,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "uuid_v1",
                    "binding_generation": 2,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )
            with closing(sqlite3.connect(install.db_path)) as connection:
                maintenance = connection.execute(
                    """
                    SELECT enabled_at, backup_interval_minutes,
                           backup_generations
                      FROM project_maintenance
                    """
                ).fetchone()
            self.assertIsNotNone(maintenance)
            self.assertEqual(maintenance[1:], (30, 3))

    def test_concurrent_fixed_confirmation_replays_as_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_unconfigured_fixed_install(Path(temporary))
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            real_lock = setup_service.state_transition_lock
            first_entry = True
            winner_results = []

            @contextmanager
            def racing_lock(state_root):
                nonlocal first_entry
                if first_entry:
                    first_entry = False
                    with mock.patch.object(
                        setup_service,
                        "state_transition_lock",
                        real_lock,
                    ):
                        winner_results.append(
                            run_service_setup(
                                install,
                                read_only=False,
                                confirmation_token=token,
                                now=CONFIRMED_AT,
                            )
                        )
                with real_lock(state_root):
                    yield

            with mock.patch.object(
                setup_service,
                "state_transition_lock",
                racing_lock,
            ):
                replay = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertEqual(len(winner_results), 1)
            self.assertTrue(winner_results[0].ok)
            self.assertFalse(replay.ok)
            self.assertEqual(replay.error_code, "relocation_token_used")
            self.assertEqual(replay.data["planned_writes"], [])
            self.assertEqual(replay.data["completed_writes"], [])
            self.assertTrue(replay.data["maintenance_enabled"])
            self.assertEqual(replay.data["viewer_status"], "current")
            self.assertFalse(replay.data["relocation"]["required"])
            self.assertEqual(
                replay.data["relocation"]["binding_generation"],
                2,
            )

    def test_outer_confirmation_race_reports_current_used_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_fixed_install(Path(temporary))
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            real_validate = (
                setup_service._validate_pending_cleanup_readonly
            )
            winner_results = []

            def racing_validate(resolution, target):
                if not winner_results:
                    with mock.patch.object(
                        setup_service,
                        "_validate_pending_cleanup_readonly",
                        real_validate,
                    ):
                        winner_results.append(
                            run_service_setup(
                                install,
                                read_only=False,
                                confirmation_token=token,
                                now=CONFIRMED_AT,
                            )
                        )
                return real_validate(resolution, target)

            with mock.patch.object(
                setup_service,
                "_validate_pending_cleanup_readonly",
                racing_validate,
            ):
                replay = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertEqual(len(winner_results), 1)
            self.assertTrue(winner_results[0].ok)
            self.assertFalse(replay.ok)
            self.assertEqual(replay.error_code, "relocation_token_used")
            self.assertEqual(
                replay.data["relocation"],
                {
                    "required": False,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "uuid_v1",
                    "binding_generation": 2,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )

    def test_inner_revalidation_preserves_shared_state_error_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_fixed_install(Path(temporary))
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            real_lock = setup_service.state_transition_lock
            first_entry = True

            @contextmanager
            def invalidating_lock(state_root):
                nonlocal first_entry
                if first_entry:
                    first_entry = False
                    install.db_path.unlink()
                with real_lock(state_root):
                    yield

            with mock.patch.object(
                setup_service,
                "state_transition_lock",
                invalidating_lock,
            ):
                failed = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(
                failed.error_code,
                "project_state_unreadable",
            )
            self.assertEqual(failed.data["planned_writes"], [])
            self.assertEqual(failed.data["completed_writes"], [])
            self.assertIsNone(failed.data["schema_from"])
            self.assertIsNone(failed.data["maintenance_enabled"])
            self.assertIsNone(failed.data["viewer_status"])
            self.assertEqual(
                failed.data["relocation"],
                {
                    "required": False,
                    "source_layout": None,
                    "identity_scheme": None,
                    "binding_generation": None,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )

    def test_rejected_tokens_have_empty_arrays_and_never_echo_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_fixed_install(Path(temporary))
            context = relocation_context(install)
            token = encode_relocation_token(
                context,
                issued_at=ISSUED_AT,
            )
            stale_token = encode_relocation_token(
                replace(context, source_schema_version=13),
                issued_at=ISSUED_AT,
            )
            invalid_token = "REJECTED_PRIVATE_TOKEN_VALUE"
            cases = (
                (
                    "relocation_token_invalid",
                    invalid_token,
                    ISSUED_AT,
                ),
                (
                    "relocation_token_expired",
                    token,
                    relocation_token_expiry(ISSUED_AT),
                ),
                (
                    "relocation_token_stale",
                    stale_token,
                    CONFIRMED_AT,
                ),
            )
            for code, rejected_token, checked_at in cases:
                with self.subTest(code=code):
                    before = tree_snapshot(install.project_root)
                    rejected = run_service_setup(
                        install,
                        read_only=False,
                        confirmation_token=rejected_token,
                        now=checked_at,
                    )
                    self.assert_service_error(
                        rejected,
                        code,
                        required=True,
                        rejected_token=rejected_token,
                    )
                    serialized = json.dumps(rejected.data, sort_keys=True)
                    self.assertNotIn(context.old_path_hash, serialized)
                    self.assertNotIn(context.new_path_hash, serialized)
                    self.assertNotIn(
                        str(install.project_root),
                        serialized,
                    )
                    self.assertNotIn(
                        str(install.skill_root),
                        serialized,
                    )
                    self.assertEqual(
                        tree_snapshot(install.project_root),
                        before,
                    )

            confirmed = run_service_setup(
                install,
                read_only=False,
                confirmation_token=token,
                now=CONFIRMED_AT,
            )
            self.assertTrue(confirmed.ok)

            not_required_token = encode_relocation_token(
                context,
                issued_at=SECOND_ISSUED_AT,
            )
            before_not_required = tree_snapshot(install.project_root)
            not_required = run_service_setup(
                install,
                read_only=False,
                confirmation_token=not_required_token,
                now=SECOND_CHECKED_AT,
            )
            self.assert_service_error(
                not_required,
                "relocation_not_required",
                required=False,
                rejected_token=not_required_token,
            )
            self.assertEqual(
                tree_snapshot(install.project_root),
                before_not_required,
            )

    def test_moved_legacy_viewer_failure_preserves_source_and_token_for_retry(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_legacy_install(Path(temporary))
            context = relocation_context(install)
            legacy_root = (
                install.skill_root
                / "state"
                / "projects"
                / context.project_id
            )
            legacy_database = legacy_root / "taskgov.sqlite"
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            source_bytes = legacy_database.read_bytes()
            source_tree = tree_snapshot(legacy_root)

            with mock.patch.object(
                setup_service,
                "_publish_viewer",
                side_effect=setup_service.ViewerError(
                    "output_write_failed",
                    "injected staged Viewer failure",
                ),
            ):
                failed = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(failed.error_code, "setup_incomplete")
            self.assertEqual(
                failed.error_message,
                "setup completed only partially; rerun setup",
            )
            self.assertEqual(failed.data["planned_writes"], LEGACY_WRITES)
            self.assertEqual(failed.data["completed_writes"], [])
            self.assertFalse(install.fixed_root.exists())
            self.assertTrue(legacy_root.is_dir())
            self.assertEqual(legacy_database.read_bytes(), source_bytes)
            self.assertEqual(
                tree_snapshot(legacy_root),
                source_tree,
            )
            with closing(sqlite3.connect(legacy_database)) as connection:
                current = connection.execute(
                    """
                    SELECT binding_generation, canonical_path_hash,
                           binding_reason
                      FROM project_meta
                    """
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT binding_generation, confirmation_token_digest
                      FROM project_path_binding_history
                     ORDER BY binding_generation
                    """
                ).fetchall()
            self.assertEqual(
                current,
                (
                    1,
                    context.old_path_hash,
                    "legacy_migration",
                ),
            )
            self.assertEqual(history, [(1, None)])

            retried = run_service_setup(
                install,
                read_only=False,
                confirmation_token=token,
                now=SECOND_CHECKED_AT,
            )
            self.assertTrue(retried.ok)
            self.assertEqual(
                retried.data["planned_writes"],
                LEGACY_WRITES,
            )
            self.assertEqual(
                retried.data["completed_writes"],
                LEGACY_WRITES,
            )
            self.assertTrue(install.fixed_root.is_dir())
            self.assertTrue(install.db_path.is_file())
            self.assertFalse(legacy_root.exists())

    def test_moved_legacy_cleanup_failure_reports_durable_state_and_used_replay(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_legacy_install(Path(temporary))
            context = relocation_context(install)
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)

            with mock.patch.object(
                setup_service,
                "_complete_pending_cleanup",
                side_effect=setup_service.StateTransitionError(),
            ):
                failed = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(failed.error_code, "setup_incomplete")
            self.assertEqual(
                failed.data["completed_writes"],
                LEGACY_WRITES[:-1],
            )
            self.assertTrue(failed.data["maintenance_enabled"])
            self.assertEqual(failed.data["viewer_status"], "published")
            self.assertEqual(
                failed.data["relocation"],
                {
                    "required": False,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "legacy_path_v1",
                    "binding_generation": 2,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )
            with closing(sqlite3.connect(install.db_path)) as connection:
                durable = connection.execute(
                    """
                    SELECT p.legacy_cleanup_pending,
                           m.backup_interval_minutes,
                           m.backup_generations
                      FROM project_meta AS p
                      JOIN project_maintenance AS m
                        ON m.project_id = p.project_id
                    """
                ).fetchone()
            self.assertEqual(durable, (1, 30, 3))

            _, retirement_root = cleanup_roots(
                install.fixed_root.parent,
                context.project_id,
            )
            retirement_root.mkdir()
            (retirement_root / "unrecorded.txt").write_bytes(b"preserve")
            before_replay = tree_snapshot(install.project_root)

            replay = run_service_setup(
                install,
                read_only=False,
                confirmation_token=token,
                now=SECOND_CHECKED_AT,
            )

            self.assertFalse(replay.ok)
            self.assertEqual(replay.error_code, "relocation_token_used")
            self.assertEqual(replay.data["planned_writes"], [])
            self.assertEqual(replay.data["completed_writes"], [])
            self.assertFalse(replay.data["relocation"]["required"])
            self.assertEqual(
                replay.data["relocation"]["binding_generation"],
                2,
            )
            self.assertEqual(
                tree_snapshot(install.project_root),
                before_replay,
            )

    def test_moved_legacy_locked_revalidation_preserves_database_busy(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_legacy_install(Path(temporary))
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            real_backup_lock = (
                setup_service._legacy_managed_backup_lock
            )

            @contextmanager
            def busy_backup_lock(target):
                with real_backup_lock(target) as lock_bytes:
                    blocker = sqlite3.connect(
                        target.db_path,
                        timeout=0.0,
                    )
                    blocker.execute("BEGIN EXCLUSIVE")
                    try:
                        yield lock_bytes
                    finally:
                        blocker.rollback()
                        blocker.close()

            with mock.patch.object(
                setup_service,
                "_legacy_managed_backup_lock",
                busy_backup_lock,
            ):
                failed = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertFalse(failed.ok)
            self.assertEqual(failed.error_code, "database_busy")
            self.assertEqual(
                failed.error_message,
                "task database is busy; run the command again later",
            )
            self.assertEqual(failed.data["planned_writes"], [])
            self.assertEqual(failed.data["completed_writes"], [])
            self.assertIsNone(failed.data["schema_from"])
            self.assertIsNone(failed.data["maintenance_enabled"])
            self.assertIsNone(failed.data["viewer_status"])
            self.assertEqual(
                failed.data["relocation"],
                {
                    "required": False,
                    "source_layout": None,
                    "identity_scheme": None,
                    "binding_generation": None,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )

    def test_concurrent_moved_legacy_confirmation_replays_as_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_legacy_install(Path(temporary))
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            real_lock = setup_service.state_transition_lock
            first_entry = True
            winner_results = []

            @contextmanager
            def racing_lock(state_root):
                nonlocal first_entry
                if first_entry:
                    first_entry = False
                    with mock.patch.object(
                        setup_service,
                        "state_transition_lock",
                        real_lock,
                    ):
                        winner_results.append(
                            run_service_setup(
                                install,
                                read_only=False,
                                confirmation_token=token,
                                now=CONFIRMED_AT,
                            )
                        )
                with real_lock(state_root):
                    yield

            with mock.patch.object(
                setup_service,
                "state_transition_lock",
                racing_lock,
            ):
                replay = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertEqual(len(winner_results), 1)
            self.assertTrue(winner_results[0].ok)
            self.assertFalse(replay.ok)
            self.assertEqual(replay.error_code, "relocation_token_used")
            self.assertEqual(
                replay.data["relocation"],
                {
                    "required": False,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "legacy_path_v1",
                    "binding_generation": 2,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )

    def test_outer_moved_legacy_confirmation_race_retries_current_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_legacy_install(Path(temporary))
            preview = run_service_setup(
                install,
                read_only=True,
                now=ISSUED_AT,
            )
            token = preview.data["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            real_validate = setup_service._validate_confirmation
            first_validation = True
            winner_results = []

            def racing_validate(*args, **kwargs):
                nonlocal first_validation
                if first_validation:
                    first_validation = False
                    with mock.patch.object(
                        setup_service,
                        "_validate_confirmation",
                        real_validate,
                    ):
                        winner_results.append(
                            run_service_setup(
                                install,
                                read_only=False,
                                confirmation_token=token,
                                now=CONFIRMED_AT,
                            )
                        )
                return real_validate(*args, **kwargs)

            with mock.patch.object(
                setup_service,
                "_validate_confirmation",
                racing_validate,
            ):
                replay = run_service_setup(
                    install,
                    read_only=False,
                    confirmation_token=token,
                    now=CONFIRMED_AT,
                )

            self.assertEqual(len(winner_results), 1)
            self.assertTrue(winner_results[0].ok)
            self.assertFalse(replay.ok)
            self.assertEqual(replay.error_code, "relocation_token_used")
            self.assertEqual(replay.data["planned_writes"], [])
            self.assertEqual(replay.data["completed_writes"], [])
            self.assertEqual(
                replay.data["relocation"],
                {
                    "required": False,
                    "source_layout": "fixed_current_v1",
                    "identity_scheme": "legacy_path_v1",
                    "binding_generation": 2,
                    "confirmation_token": None,
                    "expires_at": None,
                },
            )

    def test_moved_legacy_cli_confirmation_publishes_rebinds_and_cleans(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_moved_legacy_install(Path(temporary))
            context = relocation_context(install)
            project_id = context.project_id
            legacy_root = (
                install.skill_root
                / "state"
                / "projects"
                / project_id
            )
            legacy_database = legacy_root / "taskgov.sqlite"
            source_bytes = legacy_database.read_bytes()
            before = tree_snapshot(install.project_root)

            preview_process = install.run(
                "setup",
                "--read-only",
                "--json",
            )

            self.assertEqual(
                preview_process.returncode,
                0,
                preview_process.stderr,
            )
            preview = json_payload(preview_process)
            self.assertEqual(preview["project_id"], project_id)
            self.assertEqual(
                preview["data"]["planned_writes"],
                LEGACY_WRITES,
            )
            self.assertEqual(
                preview["data"]["completed_writes"],
                [],
            )
            self.assertEqual(
                preview["data"]["relocation"]["source_layout"],
                "legacy_projects_v1",
            )
            token = preview["data"]["relocation"]["confirmation_token"]
            self.assertIsInstance(token, str)
            self.assertEqual(
                tree_snapshot(install.project_root),
                before,
            )
            self.assertEqual(legacy_database.read_bytes(), source_bytes)

            no_token_process = install.run("setup", "--json")
            self.assertEqual(no_token_process.returncode, 2)
            no_token = json_payload(no_token_process)
            no_token_data = self.assert_cli_error(
                no_token,
                "project_relocation_required",
            )
            self.assertEqual(
                no_token_data["planned_writes"],
                LEGACY_WRITES,
            )
            self.assertEqual(no_token_data["completed_writes"], [])
            self.assertEqual(
                tree_snapshot(install.project_root),
                before,
            )
            self.assertTrue(legacy_root.is_dir())
            self.assertEqual(legacy_database.read_bytes(), source_bytes)

            confirmed_process = install.run(
                "setup",
                "--confirm-relocation",
                token,
                "--json",
            )

            self.assertEqual(
                confirmed_process.returncode,
                0,
                confirmed_process.stderr,
            )
            confirmed = json_payload(confirmed_process)
            self.assertEqual(confirmed["project_id"], project_id)
            self.assertEqual(
                confirmed["data"]["planned_writes"],
                LEGACY_WRITES,
            )
            self.assertEqual(
                confirmed["data"]["completed_writes"],
                LEGACY_WRITES,
            )
            self.assertTrue(install.db_path.is_file())
            self.assertFalse(legacy_root.exists())
            self.assertTrue(install.viewer_path.is_file())
            with closing(sqlite3.connect(install.db_path)) as connection:
                current = connection.execute(
                    """
                    SELECT identity_scheme, binding_generation,
                           canonical_path_hash, binding_reason,
                           legacy_cleanup_pending
                      FROM project_meta
                    """
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT binding_generation, reason,
                           confirmation_token_digest
                      FROM project_path_binding_history
                     ORDER BY binding_generation
                    """
                ).fetchall()
                viewer = connection.execute(
                    """
                    SELECT source_generation, rendered_generation,
                           last_outcome_code
                      FROM viewer_maintenance_state
                    """
                ).fetchone()
            self.assertEqual(
                current,
                (
                    "legacy_path_v1",
                    2,
                    project_identity(
                        install.project_root
                    ).canonical_path_hash,
                    "confirmed_relocation",
                    0,
                ),
            )
            self.assertEqual(
                [(row[0], row[1]) for row in history],
                [(1, "legacy_migration"), (2, "confirmed_relocation")],
            )
            self.assertEqual(
                history[1][2],
                relocation_token_digest(token),
            )
            self.assertEqual(viewer[0], viewer[1])
            self.assertEqual(viewer[2], "succeeded")


if __name__ == "__main__":
    unittest.main()
