import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest import mock


try:
    from m14_test_support import (
        create_v10_database,
        file_snapshot,
        json_payload,
        make_physical_install,
        make_source_self_host,
    )
except ModuleNotFoundError:
    from tests.m14_test_support import (
        create_v10_database,
        file_snapshot,
        json_payload,
        make_physical_install,
        make_source_self_host,
    )

from task_governance_tool import doctor as doctor_service
from task_governance_tool import project_scope as project_scope_service
from task_governance_tool.project_scope import ProjectScopeIssue
from task_governance_tool.storage import (
    SCHEMA_VERSION,
    ProjectMaintenanceState,
    StorageError,
    ViewerMaintenanceState,
)


DOCTOR_DATA_KEYS = {"suggested_action", "setup_eligible", "components"}
COMPONENT_KEYS = {
    "package",
    "project_state",
    "task_summary",
    "handoff_delivery",
    "maintenance",
}
PROJECT_STATE_KEYS = {"code", "schema_version", "required_schema_version"}
TASK_SUMMARY_KEYS = {
    "code",
    "active",
    "blocked",
    "done",
    "next_actionable",
    "paused",
    "review_pending",
}
HANDOFF_KEYS = {
    "code",
    "handoff_pending",
    "adapter_enabled",
    "delivery_due",
}
MAINTENANCE_KEYS = {"code", "opted_in", "backup", "viewer"}
BACKUP_KEYS = {
    "code",
    "due",
    "interval_minutes",
    "generations",
    "last_success_at",
    "last_outcome",
}
VIEWER_KEYS = {
    "code",
    "due",
    "source_generation",
    "rendered_generation",
    "last_success_at",
    "last_outcome",
}
OUTCOME_KEYS = {"code", "occurred_at"}
FIXED_TIME = "2026-01-01T00:00:00Z"


def maintenance_state(*, enabled: bool) -> ProjectMaintenanceState:
    return ProjectMaintenanceState(
        project_id="proj_doctor",
        enabled_at=FIXED_TIME if enabled else None,
        backup_interval_minutes=30 if enabled else None,
        backup_generations=3 if enabled else None,
        applied_backup_generations=3 if enabled else None,
        backup_last_success_at=None,
        backup_last_outcome_code=None,
        backup_last_outcome_at=None,
        latest_backup_generation_id=None,
        viewer_last_success_at=FIXED_TIME,
        viewer_last_outcome_code="succeeded",
        viewer_last_outcome_at=FIXED_TIME,
    )


def viewer_state(
    source: int,
    rendered: int | None,
    outcome: str | None,
) -> ViewerMaintenanceState:
    return ViewerMaintenanceState(
        project_id="proj_doctor",
        source_generation=source,
        rendered_generation=rendered,
        last_success_at=FIXED_TIME if rendered is not None else None,
        last_outcome_code=outcome,
        last_outcome_at=FIXED_TIME if outcome is not None else None,
    )


class DoctorCommandTests(unittest.TestCase):
    def assert_doctor_shape(self, payload: dict) -> dict:
        self.assertEqual(payload["command"], "doctor")
        self.assertEqual(set(payload["data"]), DOCTOR_DATA_KEYS)
        self.assertEqual(
            set(payload["data"]["components"]),
            COMPONENT_KEYS,
        )
        self.assertEqual(payload["data"]["suggested_action"], "continue")
        self.assertNotIn("db_path", payload)
        serialized = json.dumps(payload)
        for forbidden in (
            "backup_path",
            "viewer_path",
            "Traceback",
            "Authorization:",
        ):
            self.assertNotIn(forbidden, serialized)
        return payload["data"]

    def test_viewer_projection_uses_only_bounded_storage_state(self):
        self.assertFalse(
            hasattr(doctor_service, "inspect_canonical_viewer_status")
        )
        viewer = doctor_service._maintenance_component(
            maintenance_state(enabled=False),
            viewer_state(9, 8, "failed"),
            observed_at="2026-01-01T00:01:00Z",
        )["viewer"]

        self.assertEqual(
            viewer,
            {
                "code": "not_opted_in",
                "due": None,
                "source_generation": None,
                "rendered_generation": None,
                "last_success_at": None,
                "last_outcome": {
                    "code": "none",
                    "occurred_at": None,
                },
            },
        )

    def test_viewer_projection_prioritizes_outcome_then_due_then_current(self):
        cases = (
            ("current", 2, 2, "succeeded", False, "current"),
            ("never-rendered", 0, None, None, True, "due"),
            ("behind", 3, 2, "succeeded", True, "due"),
            ("deferred", 3, 3, "deferred", True, "deferred"),
            ("failed", 3, 3, "failed", True, "failed"),
        )
        maintenance = maintenance_state(enabled=True)

        for (
            label,
            source,
            rendered,
            outcome,
            expected_due,
            expected_code,
        ) in cases:
            with self.subTest(case=label):
                stored_viewer = viewer_state(source, rendered, outcome)
                viewer = doctor_service._maintenance_component(
                    maintenance,
                    stored_viewer,
                    observed_at="2026-01-01T00:01:00Z",
                )["viewer"]

                self.assertEqual(set(viewer), VIEWER_KEYS)
                self.assertEqual(viewer["code"], expected_code)
                self.assertIs(viewer["due"], expected_due)
                self.assertEqual(
                    viewer["source_generation"],
                    stored_viewer.source_generation,
                )
                self.assertEqual(
                    viewer["rendered_generation"],
                    stored_viewer.rendered_generation,
                )
                self.assertEqual(
                    set(viewer["last_outcome"]),
                    OUTCOME_KEYS,
                )
                self.assertEqual(
                    viewer["last_outcome"]["code"],
                    outcome or "none",
                )

    def test_missing_state_is_setup_required_and_doctor_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            before = file_snapshot(install.project_root)

            result = install.run("doctor", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json_payload(result)
            self.assertTrue(payload["ok"])
            data = self.assert_doctor_shape(payload)
            self.assertTrue(data["setup_eligible"])
            self.assertEqual(data["components"]["package"]["status"], "clean")
            self.assertEqual(
                data["components"]["project_state"],
                {
                    "code": "setup_required",
                    "schema_version": None,
                    "required_schema_version": SCHEMA_VERSION,
                },
            )
            for component in ("task_summary", "handoff_delivery", "maintenance"):
                self.assertEqual(data["components"][component], {"code": "unavailable"})
            self.assertEqual(
                payload["warnings"],
                [{"code": "setup_required", "message": "project state is not set up"}],
            )
            self.assertEqual(payload["errors"], [])
            self.assertEqual(file_snapshot(install.project_root), before)
            self.assertFalse((install.skill_root / "state").exists())

    def test_fresh_setup_reports_first_backup_due_without_starting_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stderr)
            before = file_snapshot(install.project_root)

            result = install.run("doctor", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json_payload(result)
            self.assertEqual(payload["warnings"], [])
            backup = self.assert_doctor_shape(payload)["components"][
                "maintenance"
            ]["backup"]
            self.assertEqual(backup["code"], "due")
            self.assertTrue(backup["due"])
            self.assertIsNone(backup["last_success_at"])
            self.assertEqual(
                backup["last_outcome"],
                {"code": "none", "occurred_at": None},
            )
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_ready_state_uses_exact_component_shapes_and_one_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stderr)
            added = install.run(
                "task",
                "add",
                "--title",
                "Doctor ready task",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            task_id = json_payload(added)["data"]["task"]["task_id"]
            handoff = install.run(
                "handoff",
                "record",
                task_id,
                "--summary",
                "Deferred doctor fixture",
                "--json",
            )
            self.assertEqual(handoff.returncode, 0, handoff.stderr)
            before = file_snapshot(install.project_root)

            result = install.run("doctor", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json_payload(result)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(payload["errors"], [])
            data = self.assert_doctor_shape(payload)
            self.assertTrue(data["setup_eligible"])
            components = data["components"]
            self.assertEqual(set(components["project_state"]), PROJECT_STATE_KEYS)
            self.assertEqual(
                components["project_state"],
                {
                    "code": "ready",
                    "schema_version": SCHEMA_VERSION,
                    "required_schema_version": SCHEMA_VERSION,
                },
            )
            self.assertEqual(set(components["task_summary"]), TASK_SUMMARY_KEYS)
            self.assertEqual(
                components["task_summary"],
                {
                    "code": "ready",
                    "active": 1,
                    "blocked": 0,
                    "done": 0,
                    "next_actionable": 1,
                    "paused": 0,
                    "review_pending": 0,
                },
            )
            self.assertEqual(set(components["handoff_delivery"]), HANDOFF_KEYS)
            self.assertEqual(
                components["handoff_delivery"],
                {
                    "code": "ready",
                    "handoff_pending": 1,
                    "adapter_enabled": False,
                    "delivery_due": False,
                },
            )
            maintenance = components["maintenance"]
            self.assertEqual(set(maintenance), MAINTENANCE_KEYS)
            self.assertEqual(maintenance["code"], "enabled")
            self.assertTrue(maintenance["opted_in"])
            self.assertEqual(set(maintenance["backup"]), BACKUP_KEYS)
            self.assertEqual(maintenance["backup"]["code"], "current")
            self.assertFalse(maintenance["backup"]["due"])
            self.assertEqual(maintenance["backup"]["interval_minutes"], 30)
            self.assertEqual(maintenance["backup"]["generations"], 3)
            self.assertIsNotNone(maintenance["backup"]["last_success_at"])
            self.assertEqual(
                maintenance["backup"]["last_outcome"]["code"],
                "succeeded",
            )
            self.assertIsNotNone(
                maintenance["backup"]["last_outcome"]["occurred_at"]
            )
            self.assertEqual(set(maintenance["viewer"]), VIEWER_KEYS)
            self.assertEqual(maintenance["viewer"]["code"], "current")
            self.assertFalse(maintenance["viewer"]["due"])
            self.assertEqual(
                maintenance["viewer"]["source_generation"],
                maintenance["viewer"]["rendered_generation"],
            )
            self.assertEqual(
                set(maintenance["viewer"]["last_outcome"]),
                OUTCOME_KEYS,
            )
            self.assertEqual(
                maintenance["viewer"]["last_outcome"]["code"],
                "succeeded",
            )
            self.assertIsNotNone(maintenance["viewer"]["last_success_at"])
            self.assertIsNotNone(
                maintenance["viewer"]["last_outcome"]["occurred_at"]
            )
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_supported_older_schema_is_migration_required_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            create_v10_database(install)
            before = file_snapshot(install.project_root)
            before_db = hashlib.sha256(install.db_path.read_bytes()).hexdigest()

            result = install.run("doctor", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json_payload(result)
            data = self.assert_doctor_shape(payload)
            self.assertTrue(data["setup_eligible"])
            self.assertEqual(
                data["components"]["project_state"],
                {
                    "code": "migration_required",
                    "schema_version": 10,
                    "required_schema_version": SCHEMA_VERSION,
                },
            )
            for component in ("task_summary", "handoff_delivery", "maintenance"):
                self.assertEqual(data["components"][component], {"code": "unavailable"})
            self.assertEqual(
                payload["warnings"],
                [{
                    "code": "migration_required",
                    "message": "task database requires setup migration",
                }],
            )
            self.assertEqual(hashlib.sha256(install.db_path.read_bytes()).hexdigest(), before_db)
            self.assertEqual(file_snapshot(install.project_root), before)

    def test_modified_package_remains_advisory_and_setup_ineligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            modified = (
                install.skill_root
                / "scripts"
                / "task_governance_tool"
                / "compact.py"
            )
            modified.write_text(
                modified.read_text(encoding="utf-8") + "\n# local test drift\n",
                encoding="utf-8",
            )
            before = file_snapshot(install.project_root)

            result = install.run("doctor", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json_payload(result)
            data = self.assert_doctor_shape(payload)
            self.assertFalse(data["setup_eligible"])
            self.assertEqual(data["components"]["package"]["status"], "modified")
            self.assertEqual(
                payload["warnings"][0]["code"],
                "package_core_modified",
            )
            self.assertEqual(payload["errors"], [])
            self.assertEqual(file_snapshot(install.project_root), before)
            self.assertFalse((install.skill_root / "state").exists())

    def test_source_collision_is_project_layout_error_not_package_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_source_self_host(Path(tmp))
            competing = (
                install.project_root
                / ".agents"
                / "skills"
                / "task-governance-tool"
            )
            competing.mkdir(parents=True)

            result = install.run(
                "doctor",
                "--repo",
                str(install.project_root),
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json_payload(result)
            data = self.assert_doctor_shape(payload)
            self.assertFalse(data["setup_eligible"])
            self.assertEqual(data["components"]["package"]["status"], "clean")
            self.assertEqual(
                data["components"]["project_state"],
                {
                    "code": "invalid_layout",
                    "schema_version": None,
                    "required_schema_version": SCHEMA_VERSION,
                },
            )
            for component in ("task_summary", "handoff_delivery", "maintenance"):
                self.assertEqual(data["components"][component], {"code": "unavailable"})
            self.assertEqual(
                payload["errors"],
                [{
                    "code": "unsupported_install_layout",
                    "message": (
                        "stateful use requires one supported physical "
                        "project-scoped package layout"
                    ),
                }],
            )

    def test_storage_failure_rows_are_exact_and_unavailable(self):
        cases = (
            ("unsupported_journal_mode", "unsupported_journal", None, "connect"),
            ("database_busy", "busy", None, "connect"),
            ("project_state_unreadable", "unreadable", None, "connect"),
            ("project_mismatch", "foreign", 11, "state"),
            ("schema_too_new", "newer", SCHEMA_VERSION + 1, "state"),
        )
        for source_code, projected_code, schema_version, phase in cases:
            with self.subTest(code=source_code), tempfile.TemporaryDirectory() as tmp:
                install = make_physical_install(Path(tmp))
                inspection = project_scope_service.inspect_project_scope(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                )
                failure = StorageError(
                    source_code,
                    doctor_service.DOCTOR_MESSAGES[source_code],
                )
                with ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.object(
                            doctor_service,
                            "inspect_project_scope",
                            return_value=inspection,
                        )
                    )
                    if phase == "connect":
                        stack.enter_context(
                            mock.patch.object(
                                doctor_service,
                                "connect_readonly",
                                side_effect=failure,
                            )
                        )
                    else:
                        stack.enter_context(
                            mock.patch.object(
                                doctor_service,
                                "connect_readonly",
                                return_value=mock.MagicMock(),
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                doctor_service,
                                "current_schema_version",
                                return_value=schema_version,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                doctor_service,
                                "read_doctor_state",
                                side_effect=failure,
                            )
                        )
                    result = doctor_service.run_doctor(
                        repo=str(install.project_root),
                        repo_explicit=True,
                        script_path=install.entrypoint,
                    )

                self.assertFalse(result.ok)
                self.assertEqual(
                    result.errors,
                    [{
                        "code": source_code,
                        "message": doctor_service.DOCTOR_MESSAGES[source_code],
                    }],
                )
                self.assertEqual(result.warnings, [])
                self.assertEqual(result.data["suggested_action"], "continue")
                self.assertFalse(result.data["setup_eligible"])
                self.assertEqual(
                    result.data["components"]["project_state"],
                    {
                        "code": projected_code,
                        "schema_version": schema_version,
                        "required_schema_version": SCHEMA_VERSION,
                    },
                )
                for component in ("task_summary", "handoff_delivery", "maintenance"):
                    self.assertEqual(
                        result.data["components"][component],
                        {"code": "unavailable"},
                    )

    def test_scope_and_package_advisories_use_fixed_rows(self):
        structural_cases = (
            ("unsupported_python", "unsupported_runtime"),
            ("unsupported_install_layout", "invalid_layout"),
            ("project_scope_required", "invalid_project"),
            ("invalid_project_root", "invalid_project"),
            ("state_path_invalid", "invalid_state_path"),
            ("state_ignore_required", "ignore_required"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            base = project_scope_service.inspect_project_scope(
                repo=str(install.project_root),
                repo_explicit=True,
                script_path=install.entrypoint,
            )
            self.assertIsNotNone(base.package_status)

            for source_code, projected_code in structural_cases:
                with self.subTest(code=source_code):
                    inspection = replace(
                        base,
                        issues=(
                            ProjectScopeIssue(
                                source_code,
                                doctor_service.DOCTOR_MESSAGES[source_code],
                            ),
                        ),
                    )
                    with mock.patch.object(
                        doctor_service,
                        "inspect_project_scope",
                        return_value=inspection,
                    ):
                        result = doctor_service.run_doctor(
                            repo=str(install.project_root),
                            repo_explicit=True,
                            script_path=install.entrypoint,
                        )
                    self.assertFalse(result.ok)
                    self.assertEqual(
                        result.errors,
                        [{
                            "code": source_code,
                            "message": doctor_service.DOCTOR_MESSAGES[source_code],
                        }],
                    )
                    self.assertEqual(
                        result.data["components"]["project_state"]["code"],
                        projected_code,
                    )
                    self.assertFalse(result.data["setup_eligible"])

            unknown_package = replace(
                base.package_status,
                status="unknown",
                unknown_reasons=("manifest_unreadable",),
            )
            unknown_inspection = replace(
                base,
                package_status=unknown_package,
                issues=(
                    ProjectScopeIssue(
                        "package_status_unknown",
                        doctor_service.DOCTOR_MESSAGES["package_status_unknown"],
                    ),
                ),
            )
            with mock.patch.object(
                doctor_service,
                "inspect_project_scope",
                return_value=unknown_inspection,
            ):
                unknown = doctor_service.run_doctor(
                    repo=str(install.project_root),
                    repo_explicit=True,
                    script_path=install.entrypoint,
                )
            self.assertTrue(unknown.ok)
            self.assertFalse(unknown.data["setup_eligible"])
            self.assertEqual(
                unknown.data["components"]["package"]["status"],
                "unknown",
            )
            self.assertEqual(
                unknown.data["components"]["project_state"]["code"],
                "setup_required",
            )
            self.assertEqual(
                unknown.warnings,
                [{
                    "code": "package_status_unknown",
                    "message": doctor_service.DOCTOR_MESSAGES[
                        "package_status_unknown"
                    ],
                }],
            )

    def test_invalid_explicit_project_root_maps_to_fixed_public_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = make_physical_install(Path(tmp))
            missing = Path(tmp) / "missing-project"
            before = file_snapshot(install.project_root)

            result = install.run(
                "doctor",
                "--repo",
                str(missing),
                "--json",
            )

            self.assertEqual(result.returncode, 2)
            payload = json_payload(result)
            self.assertEqual(payload["errors"][0]["code"], "invalid_project_root")
            self.assertEqual(
                payload["data"]["components"]["project_state"]["code"],
                "invalid_project",
            )
            self.assertEqual(file_snapshot(install.project_root), before)


if __name__ == "__main__":
    unittest.main()
