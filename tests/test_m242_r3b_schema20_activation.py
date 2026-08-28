from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import cli as cli_module  # noqa: E402
from task_governance_tool import storage  # noqa: E402
from task_governance_tool import verification_runner  # noqa: E402
from task_governance_tool.backup import (  # noqa: E402
    discover_managed_backup_metadata,
    publish_setup_backup,
    select_managed_backup_for_recovery,
)
from task_governance_tool.evidence_projection import (  # noqa: E402
    publish_setup_evidence_projection,
)
from task_governance_tool.state_resolver import (  # noqa: E402
    resolve_project_state,
)
from task_governance_tool.viewer import build_viewer_snapshot  # noqa: E402
from tests.m14_test_support import (  # noqa: E402
    PhysicalInstall,
    make_physical_install,
    refresh_test_manifest,
    tree_snapshot,
)
from tests.m223_test_support import (  # noqa: E402
    V20_TABLES,
    logical_database_digest,
    remove_v20_runner_shadow_for_test,
)
from tests.m23_test_support import reference_json_bytes  # noqa: E402


BUNDLE_V2_DOMAIN = b"taskgov-completion-evidence-bundle-v2\0"
INDEX_V2_DOMAIN = b"taskgov-evidence-index-v2\0"
FINGERPRINT_A = "sha256:" + "a" * 64
FINGERPRINT_B = "sha256:" + "b" * 64
FIXED_TIME = "2026-08-23T00:00:00Z"


_SCHEMA20_RUNTIME_PATCH_TARGETS = (
    "task_governance_tool.backup.SCHEMA_VERSION",
    "task_governance_tool.doctor.SCHEMA_VERSION",
    "task_governance_tool.evidence_projection.SCHEMA_VERSION",
    "task_governance_tool.reviews.SCHEMA_VERSION",
    "task_governance_tool.setup.SCHEMA_VERSION",
    "task_governance_tool.state_resolver.SCHEMA_VERSION",
    "task_governance_tool.storage.SCHEMA_VERSION",
    "task_governance_tool.tasks.SCHEMA_VERSION",
    "task_governance_tool.verification_receipts.SCHEMA_VERSION",
    "task_governance_tool.viewer.SCHEMA_VERSION",
)
_SCHEMA20_RUNTIME_PATCHERS: list[object] = []


def _start_schema20_runtime_oracle() -> None:
    """Freeze this historical module at its accepted schema-v20 boundary."""

    if _SCHEMA20_RUNTIME_PATCHERS:
        raise AssertionError("schema-v20 runtime oracle is already active")
    for target in _SCHEMA20_RUNTIME_PATCH_TARGETS:
        patcher = mock.patch(target, 20)
        patcher.start()
        _SCHEMA20_RUNTIME_PATCHERS.append(patcher)


def _stop_schema20_runtime_oracle() -> None:
    while _SCHEMA20_RUNTIME_PATCHERS:
        _SCHEMA20_RUNTIME_PATCHERS.pop().stop()


def setUpModule() -> None:
    _start_schema20_runtime_oracle()


def tearDownModule() -> None:
    _stop_schema20_runtime_oracle()


def _run_cli(
    target: storage.DatabaseTarget,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            returncode = cli_module.main(
                list(arguments),
                _target_override=target,
                _maintenance_enabled=False,
            )
        except SystemExit as exc:
            returncode = int(exc.code or 0)
    return subprocess.CompletedProcess(
        args=[sys.executable, "scripts/taskgov.py", *arguments],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _json_success(
    testcase: unittest.TestCase,
    result: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    testcase.assertEqual(result.returncode, 0, result.stderr or result.stdout)
    payload = json.loads(result.stdout)
    testcase.assertIsInstance(payload, dict)
    return payload


def _physical_current20_install(root: Path) -> PhysicalInstall:
    """Create a copied physical install pinned to the historical v20 runtime."""

    install = make_physical_install(root, git_managed=True)
    installed_storage = (
        install.skill_root
        / "scripts"
        / "task_governance_tool"
        / "storage.py"
    )
    source = installed_storage.read_text(encoding="utf-8")
    current_declaration = "SCHEMA_VERSION = 21"
    if source.count(current_declaration) != 1:
        raise AssertionError("schema-v20 oracle could not pin installed runtime")
    installed_storage.write_text(
        source.replace(current_declaration, "SCHEMA_VERSION = 20", 1),
        encoding="utf-8",
        newline="\n",
    )
    refresh_test_manifest(install.skill_root)
    return install


def _fixed_current20(
    root: Path,
    *,
    identity_seed: str,
) -> tuple[PhysicalInstall, storage.DatabaseTarget]:
    install = _physical_current20_install(root)
    unbound = storage.resolve_database_target(
        repo=install.project_root,
        db=install.db_path,
        script_path=install.entrypoint,
    )
    identity_parts = list(
        hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:32]
    )
    identity_parts[12] = "4"
    identity_parts[16] = "8"
    identity = "".join(identity_parts)
    initialized = storage.initialize_uuid_database(
        unbound,
        project_id_factory=lambda: identity,
        clock=lambda: FIXED_TIME,
    )
    if initialized.schema_version != 20:
        raise AssertionError("fresh fixed test database did not reach schema v20")
    resolution = resolve_project_state(
        skill_root=install.skill_root,
        repo=install.project_root,
    )
    if resolution.error_code is not None or resolution.target is None:
        raise AssertionError("fresh fixed test database did not resolve")
    return install, resolution.target


def _resolved_target(install: PhysicalInstall) -> storage.DatabaseTarget:
    resolution = resolve_project_state(
        skill_root=install.skill_root,
        repo=install.project_root,
    )
    if resolution.error_code is not None or resolution.target is None:
        raise AssertionError("fixed test database did not resolve")
    return resolution.target


def _add_task(
    testcase: unittest.TestCase,
    target: storage.DatabaseTarget,
    *,
    title: str,
    verification: str,
    with_contract: bool = False,
) -> str:
    arguments = [
        "task",
        "add",
        "--repo",
        str(target.project.canonical_repo),
        "--title",
        title,
        "--status",
        "in_progress",
        "--review-tier",
        "0",
        "--verification",
        verification,
    ]
    if with_contract:
        arguments.extend(
            (
                "--contract-scope",
                "Validate one schema-v20 Runner admission fixture.",
                "--contract-acceptance",
                "All public schema-v20 admission boundaries reject Runner rows.",
                "--contract-constraints",
                "Do not activate Runner execution or gate authority.",
            )
        )
    arguments.append("--json")
    payload = _json_success(
        testcase,
        _run_cli(target, *arguments),
    )
    return str(payload["data"]["task"]["task_id"])


def _seed_completion_gates(
    testcase: unittest.TestCase,
    target: storage.DatabaseTarget,
    task_id: str,
    *,
    fingerprint: str,
    verification_required: bool,
) -> str | None:
    repo = str(target.project.canonical_repo)
    targeted = _json_success(
        testcase,
        _run_cli(
            target,
            "review",
            "target",
            "set",
            "--repo",
            repo,
            task_id,
            "--kind",
            "diff_fingerprint",
            "--revision",
            fingerprint,
            "--json",
        ),
    )
    generation = int(targeted["data"]["task"]["review_target_generation"])
    _json_success(
        testcase,
        _run_cli(
            target,
            "review",
            "receipt",
            "add",
            "--repo",
            repo,
            task_id,
            "--reviewer",
            "mechanical-review",
            "--kind",
            "not_required",
            "--verdict",
            "not_required",
            "--summary",
            "Tier zero review is not required",
            "--json",
        ),
    )
    if not verification_required:
        return None
    recorded = _json_success(
        testcase,
        _run_cli(
            target,
            "verification",
            "receipt",
            "add",
            "--repo",
            repo,
            task_id,
            "--result",
            "pass",
            "--duration-ms",
            "25",
            "--scope-coverage",
            "full",
            "--expected-target-generation",
            str(generation),
            "--json",
        ),
    )
    return str(recorded["data"]["receipt"]["verification_receipt_id"])


def _complete_task(
    testcase: unittest.TestCase,
    target: storage.DatabaseTarget,
    task_id: str,
) -> None:
    _json_success(
        testcase,
        _run_cli(
            target,
            "task",
            "complete",
            "--repo",
            str(target.project.canonical_repo),
            task_id,
            "--verification-complete",
            "--review-complete",
            "--commit-not-required",
            "--json",
        ),
    )


def _inject_v20_marker(path: Path, marker: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        if marker == "table":
            connection.execute(
                "CREATE TABLE verification_runner_resolutions(marker INTEGER)"
            )
        elif marker == "mixed_case_table":
            connection.execute(
                "CREATE TABLE Verification_Runner_Resolutions(marker INTEGER)"
            )
        elif marker == "index":
            connection.execute(
                "CREATE INDEX idx_verification_runner_resolutions_parent "
                "ON tasks(task_id)"
            )
        elif marker == "mixed_case_index":
            connection.execute(
                "CREATE INDEX IDX_Verification_Runner_Resolutions_Parent "
                "ON tasks(task_id)"
            )
        elif marker == "trigger":
            connection.execute(
                "CREATE TRIGGER trg_verification_runner_resolutions_no_update "
                "BEFORE UPDATE ON tasks BEGIN SELECT 1; END"
            )
        elif marker == "mixed_case_trigger":
            connection.execute(
                "CREATE TRIGGER TRG_Verification_Runner_Resolutions_No_Update "
                "BEFORE UPDATE ON tasks BEGIN SELECT 1; END"
            )
        elif marker == "column":
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN "
                "review_target_runner_basis_version INTEGER"
            )
        elif marker == "mixed_case_column":
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN "
                "Review_Target_Runner_Basis_Version INTEGER"
            )
        elif marker == "generated_column":
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN "
                "review_target_runner_basis_version INTEGER "
                "GENERATED ALWAYS AS (0) VIRTUAL"
            )
        elif marker == "wrong_type_view":
            connection.execute(
                "CREATE VIEW verification_runner_resolutions "
                "AS SELECT task_id FROM tasks"
            )
        elif marker == "replacement_trigger":
            connection.execute(
                "DROP TRIGGER trg_criterion_evidence_links_matrix_insert"
            )
            connection.execute(
                storage._criterion_evidence_links_v20_matrix_trigger_sql()
            )
        else:
            raise AssertionError("unknown hybrid marker fixture")
        connection.commit()


def _inject_valid_runner_resolution(path: Path, task_id: str) -> None:
    with closing(storage.connect(path)) as connection:
        basis = connection.execute(
            """
            SELECT t.project_id,
                   t.task_id,
                   t.current_contract_revision AS contract_revision,
                   t.review_target_authority_snapshot_id AS authority_snapshot_id,
                   t.review_target_verification_criterion_id AS verification_criterion_id,
                   s.verification_digest AS verification_expectation_digest,
                   c.digest AS verification_criterion_digest,
                   t.review_target_kind AS target_kind,
                   t.review_target_value AS target_value,
                   t.review_target_base_revision AS target_base_revision,
                   t.review_target_generation AS target_generation,
                   t.review_target_capture_version AS target_capture_version,
                   t.review_target_artifact_manifest_id AS artifact_manifest_id
              FROM tasks AS t
              JOIN authority_snapshots AS s
                ON s.project_id = t.project_id
               AND s.task_id = t.task_id
               AND s.authority_snapshot_id = t.review_target_authority_snapshot_id
              JOIN contract_criteria AS c
                ON c.project_id = t.project_id
               AND c.task_id = t.task_id
               AND c.criterion_id = t.review_target_verification_criterion_id
             WHERE t.task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if basis is None:
            raise AssertionError("Runner fixture target basis was not captured")
        values: dict[str, object] = {
            "verification_runner_resolution_id": (
                "tg_verification_runner_resolution_" + "1" * 16
            ),
            **{
                name: basis[name]
                for name in (
                    "project_id",
                    "task_id",
                    "contract_revision",
                    "authority_snapshot_id",
                    "verification_criterion_id",
                    "verification_expectation_digest",
                    "verification_criterion_digest",
                    "target_kind",
                    "target_value",
                    "target_generation",
                    "target_capture_version",
                    "artifact_manifest_id",
                )
            },
            "target_base_revision": (
                str(basis["target_base_revision"])
                if basis["target_base_revision"]
                else None
            ),
            "target_material_digest": "sha256:" + "c" * 64,
            "plan_state": "ready",
            "plan_blob_object_id": None,
            "plan_raw_digest": None,
            "plan_id": None,
            "plan_version": None,
            "plan_semantic_digest": None,
            "selected_entry_digest": None,
            "coverage": "full",
            "step_count": 1,
            "runner_contract_version": 1,
            "runner_implementation_version": "taskgov-verification-runner/1",
            "runner_implementation_digest": "sha256:" + "d" * 64,
            "runner_policy_digest": "sha256:" + "e" * 64,
            "runtime_digest": None,
            "gate_eligibility_version": 0,
            "trigger": "review_target_set_v1",
            "route": "direct",
            "reason": None,
            "idempotency_digest": "sha256:" + "f" * 64,
            "created_at": FIXED_TIME,
        }
        values["idempotency_digest"] = (
            verification_runner.resolution_idempotency_digest(
                storage._verification_runner_resolution_digest_projection(values)
            )
        )
        columns = tuple(values)
        connection.execute(
            "INSERT INTO verification_runner_resolutions("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(values[column] for column in columns),
        )
        connection.commit()


class R3BSchema20ActivationTests(unittest.TestCase):
    def test_complete_v19_public_setup_migrates_to_v20_and_preserves_extras(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-r3b-v19-", dir=ROOT) as tmp:
            install, target = _fixed_current20(
                Path(tmp),
                identity_seed="r3b-v19-public-migration",
            )
            task_id = _add_task(
                self,
                target,
                title="Preserved schema-v19 task",
                verification="",
            )
            with closing(storage.connect(target.db_path)) as connection:
                remove_v20_runner_shadow_for_test(connection)
                connection.execute(
                    "CREATE TABLE r3b_unrelated_extra("
                    "extra_id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO r3b_unrelated_extra(extra_id, value) "
                    "VALUES (1, 'preserved')"
                )
                connection.execute(
                    "CREATE INDEX idx_r3b_unrelated_extra_value "
                    "ON r3b_unrelated_extra(value)"
                )
                connection.execute(
                    "CREATE TRIGGER trg_r3b_unrelated_extra_no_delete "
                    "BEFORE DELETE ON r3b_unrelated_extra BEGIN SELECT 1; END"
                )
                connection.execute(
                    "CREATE TABLE Verification_Runner_Resolutions_Extra("
                    "marker INTEGER PRIMARY KEY)"
                )
                connection.commit()
                self.assertEqual(storage.current_schema_version(connection), 19)
                self.assertFalse(
                    storage.schema_objects_inconsistent_with_version(connection, 19)
                )
                before_task = tuple(
                    connection.execute(
                        "SELECT task_id, title, status FROM tasks WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                )
                extra_sql = tuple(
                    tuple(row)
                    for row in connection.execute(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master "
                        "WHERE name LIKE '%r3b_unrelated_extra%' ORDER BY name"
                    ).fetchall()
                )

            setup_state = storage.inspect_setup_state(target)
            self.assertTrue(setup_state.needs_migration)
            self.assertEqual(setup_state.schema_version, 19)
            migrated = install.run("setup", "--json")
            migrated_payload = _json_success(self, migrated)
            self.assertEqual(migrated_payload["data"]["schema_from"], 19)
            self.assertEqual(migrated_payload["data"]["schema_to"], 20)

            current = _resolved_target(install)
            with closing(storage.connect(current.db_path)) as connection:
                self.assertEqual(storage.current_schema_version(connection), 20)
                self.assertEqual(
                    tuple(
                        connection.execute(
                            "SELECT task_id, title, status FROM tasks "
                            "WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()
                    ),
                    before_task,
                )
                self.assertEqual(
                    tuple(
                        tuple(row)
                        for row in connection.execute(
                            "SELECT type, name, tbl_name, sql FROM sqlite_master "
                            "WHERE name LIKE '%r3b_unrelated_extra%' ORDER BY name"
                        ).fetchall()
                    ),
                    extra_sql,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            "SELECT extra_id, value FROM r3b_unrelated_extra"
                        ).fetchone()
                    ),
                    (1, "preserved"),
                )
                self.assertTrue(
                    storage.table_exists(
                        connection,
                        "Verification_Runner_Resolutions_Extra",
                    )
                )
                for table_name in V20_TABLES:
                    self.assertEqual(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0],
                        0,
                    )
                before_reentry = logical_database_digest(connection)
                self.assertEqual(storage.apply_migrations(connection), ([], []))
                self.assertEqual(logical_database_digest(connection), before_reentry)
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM schema_migrations WHERE version > 20"
                    ).fetchone()
                )

    def test_fresh_v20_completion_writes_only_null_runner_v2_and_remains_compatible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-r3b-v20-", dir=ROOT) as tmp:
            install = _physical_current20_install(Path(tmp))
            setup = install.run("setup", "--json")
            setup_payload = _json_success(self, setup)
            self.assertIsNone(setup_payload["data"]["schema_from"])
            self.assertEqual(setup_payload["data"]["schema_to"], 20)
            target = _resolved_target(install)

            caller_task = _add_task(
                self,
                target,
                title="Caller-attested schema-v20 task",
                verification="Focused offline verification",
            )
            empty_task = _add_task(
                self,
                target,
                title="Not-required schema-v20 task",
                verification="",
            )
            caller_receipt = _seed_completion_gates(
                self,
                target,
                caller_task,
                fingerprint=FINGERPRINT_A,
                verification_required=True,
            )
            self.assertIsNotNone(caller_receipt)
            self.assertIsNone(
                _seed_completion_gates(
                    self,
                    target,
                    empty_task,
                    fingerprint=FINGERPRINT_B,
                    verification_required=False,
                )
            )

            process_error = AssertionError("completion must not launch a process")
            runner_error = AssertionError("completion must not allocate Runner state")
            with (
                mock.patch.object(
                    subprocess,
                    "Popen",
                    side_effect=process_error,
                ) as popen,
                mock.patch.object(
                    verification_runner,
                    "generate_runner_id",
                    side_effect=runner_error,
                ) as runner_ids,
            ):
                _complete_task(self, target, caller_task)
                _complete_task(self, target, empty_task)
            popen.assert_not_called()
            runner_ids.assert_not_called()

            with closing(storage.connect(target.db_path)) as connection:
                bundle_rows = {
                    str(row["task_id"]): dict(row)
                    for row in connection.execute(
                        "SELECT task_id, completion_evidence_bundle_id, "
                        "source_schema_version, bundle_version, "
                        "verification_receipt_id, verification_basis_kind, "
                        "verification_runner_observation_id, bundle_digest "
                        "FROM completion_evidence_bundles ORDER BY task_id"
                    ).fetchall()
                }
                self.assertEqual(set(bundle_rows), {caller_task, empty_task})
                expected_bases = {
                    caller_task: ("caller_attestation", caller_receipt),
                    empty_task: ("not_required", None),
                }
                for task_id, (basis_kind, receipt_id) in expected_bases.items():
                    row = bundle_rows[task_id]
                    self.assertEqual(
                        (row["source_schema_version"], row["bundle_version"]),
                        (20, 2),
                    )
                    self.assertEqual(row["verification_basis_kind"], basis_kind)
                    self.assertEqual(row["verification_receipt_id"], receipt_id)
                    self.assertIsNone(row["verification_runner_observation_id"])

                cycle_rows = {
                    str(row["task_id"]): tuple(row)[1:]
                    for row in connection.execute(
                        "SELECT task_id, verification_basis_kind, "
                        "verification_receipt_id, "
                        "verification_runner_observation_id "
                        "FROM task_completion_cycles ORDER BY task_id"
                    ).fetchall()
                }
                self.assertEqual(
                    cycle_rows,
                    {
                        caller_task: ("caller_attestation", caller_receipt, None),
                        empty_task: ("not_required", None, None),
                    },
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM tasks "
                        "WHERE review_target_runner_basis_version != 0"
                    ).fetchone()[0],
                    0,
                )
                for table_name in V20_TABLES:
                    self.assertEqual(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0],
                        0,
                    )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM evidence_references "
                        "WHERE source_kind = 'runner_observation'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM criterion_evidence_links "
                        "WHERE relation = 'runner_observation'"
                    ).fetchone()[0],
                    0,
                )

            published = publish_setup_evidence_projection(
                target,
                observed_at="2026-08-23T00:10:00Z",
            )
            self.assertEqual(published.code, "succeeded")
            index_envelope = json.loads(target.resolved_evidence_index.read_bytes())
            self.assertEqual(
                set(index_envelope),
                {"format_version", "index_digest", "payload"},
            )
            self.assertEqual(index_envelope["format_version"], 2)
            self.assertEqual(
                index_envelope["index_digest"],
                "sha256:"
                + hashlib.sha256(
                    INDEX_V2_DOMAIN
                    + reference_json_bytes(index_envelope["payload"])
                ).hexdigest(),
            )
            entries = {
                entry["task_id"]: entry
                for entry in index_envelope["payload"]["entries"]
            }
            self.assertEqual(set(entries), {caller_task, empty_task})
            for task_id, (basis_kind, receipt_id) in expected_bases.items():
                entry = entries[task_id]
                self.assertEqual(entry["bundle_format_version"], 2)
                document = (
                    target.resolved_evidence_root / entry["bundle_file"]
                ).read_bytes()
                self.assertEqual(
                    entry["file_digest"],
                    "sha256:" + hashlib.sha256(document).hexdigest(),
                )
                envelope = json.loads(document)
                payload = envelope["payload"]
                self.assertEqual(envelope["format_version"], 2)
                self.assertEqual(
                    envelope["bundle_digest"],
                    "sha256:"
                    + hashlib.sha256(
                        BUNDLE_V2_DOMAIN + reference_json_bytes(payload)
                    ).hexdigest(),
                )
                self.assertEqual(
                    payload["verification_basis"],
                    {
                        "basis_version": 1,
                        "kind": basis_kind,
                        "verification_receipt_id": receipt_id,
                        "runner_observation_id": None,
                    },
                )
                self.assertIsNone(payload["runner_observation"])
                self.assertFalse(
                    any(
                        row["source_kind"] == "runner_observation"
                        for row in payload["evidence_references"]
                    )
                )
                self.assertFalse(
                    any(
                        row["relation"] == "runner_observation"
                        for row in payload["criterion_links"]
                    )
                )

            with closing(
                storage.connect_snapshot_readonly(target.db_path)
            ) as connection:
                snapshot = build_viewer_snapshot(
                    connection,
                    target,
                    generated_at="2026-08-23T00:20:00Z",
                ).snapshot
            self.assertEqual(snapshot["source_schema_version"], 20)
            serialized_snapshot = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn("runner_observation", serialized_snapshot)
            self.assertNotIn("verification_basis", serialized_snapshot)

            backup_metadata = publish_setup_backup(target, 3)
            self.assertTrue(backup_metadata.generation_id.startswith("tg_backup_"))
            recovery = select_managed_backup_for_recovery(target)
            self.assertIsNotNone(recovery)
            self.assertEqual(recovery.schema_version, 20)

            with closing(storage.connect(target.db_path)) as connection:
                before_reentry = logical_database_digest(connection)
                self.assertEqual(storage.apply_migrations(connection), ([], []))
                self.assertEqual(logical_database_digest(connection), before_reentry)
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    20,
                )
            self.assertEqual(
                [
                    path
                    for path in target.db_path.parent.rglob("*")
                    if path.is_file() and "runner" in path.name.casefold()
                ],
                [],
            )
            replay = install.run("setup", "--json")
            replay_payload = _json_success(self, replay)
            self.assertEqual(replay_payload["data"]["schema_to"], 20)
            self.assertTrue(install.viewer_path.is_file())

            reopened = _json_success(
                self,
                _run_cli(
                    target,
                    "task",
                    "edit",
                    "--repo",
                    str(target.project.canonical_repo),
                    empty_task,
                    "--status",
                    "in_progress",
                    "--reopen-reason",
                    "Representative schema-v20 reopen",
                    "--json",
                ),
            )
            self.assertEqual(reopened["data"]["task"]["status"], "in_progress")
            self.assertIsNone(reopened["data"]["task"]["completed_at"])
            self.assertEqual(
                reopened["data"]["event"]["event_type"],
                "task_reopened",
            )
            self.assertNotIn("completion_cycle_id", reopened["data"]["event"])
            with closing(storage.connect(target.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_completion_cycles "
                        "WHERE task_id = ?",
                        (empty_task,),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    tuple(
                        connection.execute(
                            "SELECT source_schema_version, bundle_version, "
                            "verification_basis_kind, "
                            "verification_runner_observation_id "
                            "FROM completion_evidence_bundles WHERE task_id = ?",
                            (empty_task,),
                        ).fetchone()
                    ),
                    (20, 2, "not_required", None),
                )
                for table_name in V20_TABLES:
                    self.assertEqual(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0],
                        0,
                    )

    def test_declared_v19_hybrids_fail_closed_at_every_public_boundary(self) -> None:
        markers = (
            "table",
            "mixed_case_table",
            "index",
            "mixed_case_index",
            "trigger",
            "mixed_case_trigger",
            "column",
            "mixed_case_column",
            "generated_column",
            "wrong_type_view",
            "replacement_trigger",
        )
        with tempfile.TemporaryDirectory(prefix=".tmp-m242-r3b-hybrid-", dir=ROOT) as tmp:
            root = Path(tmp)
            for marker in markers:
                with self.subTest(marker=marker):
                    install, target = _fixed_current20(
                        root / marker,
                        identity_seed="r3b-hybrid-" + marker,
                    )
                    with closing(storage.connect(target.db_path)) as connection:
                        remove_v20_runner_shadow_for_test(connection)
                        self.assertEqual(storage.current_schema_version(connection), 19)
                    target = _resolved_target(install)
                    publish_setup_backup(target, 3)
                    backup_paths = sorted(
                        target.resolved_backups_path.glob(
                            "taskgov-backup-v1_*.sqlite"
                        )
                    )
                    self.assertTrue(backup_paths)
                    for path in (target.db_path, *backup_paths):
                        _inject_v20_marker(path, marker)

                    baseline = tree_snapshot(install.skill_root / "state")

                    resolution = resolve_project_state(
                        skill_root=install.skill_root,
                        repo=install.project_root,
                    )
                    self.assertEqual(
                        resolution.error_code,
                        "project_state_unreadable",
                    )
                    self.assertEqual(
                        tree_snapshot(install.skill_root / "state"),
                        baseline,
                    )

                    with self.assertRaises(storage.StorageError) as inspected:
                        storage.inspect_setup_state(target)
                    self.assertEqual(
                        inspected.exception.code,
                        "project_state_unreadable",
                    )
                    self.assertEqual(
                        tree_snapshot(install.skill_root / "state"),
                        baseline,
                    )

                    with closing(storage.connect(target.db_path)) as connection:
                        before_digest = logical_database_digest(connection)
                        with self.assertRaises(storage.StorageError) as migrated:
                            storage.apply_migrations(connection)
                        self.assertEqual(migrated.exception.code, "migration_required")
                        self.assertEqual(
                            logical_database_digest(connection),
                            before_digest,
                        )
                    self.assertEqual(
                        tree_snapshot(install.skill_root / "state"),
                        baseline,
                    )

                    with closing(storage.connect(target.db_path)) as connection:
                        before_digest = logical_database_digest(connection)
                        with self.assertRaises(storage.StorageError) as managed:
                            storage.validate_managed_backup_source_database(
                                connection,
                                target,
                            )
                        self.assertEqual(
                            managed.exception.code,
                            "migration_required",
                        )
                        self.assertEqual(
                            logical_database_digest(connection),
                            before_digest,
                        )
                    self.assertEqual(
                        tree_snapshot(install.skill_root / "state"),
                        baseline,
                    )

                    with closing(
                        storage.connect_snapshot_readonly(target.db_path)
                    ) as connection:
                        with self.assertRaises(storage.StorageError) as viewer:
                            build_viewer_snapshot(connection, target)
                    self.assertEqual(viewer.exception.code, "migration_required")
                    self.assertEqual(
                        tree_snapshot(install.skill_root / "state"),
                        baseline,
                    )

                    with self.assertRaises(storage.StorageError) as backup:
                        publish_setup_backup(target, 3)
                    self.assertEqual(backup.exception.code, "setup_backup_failed")
                    self.assertEqual(
                        tree_snapshot(install.skill_root / "state"),
                        baseline,
                    )

                    with self.assertRaises(storage.StorageError) as recovery:
                        select_managed_backup_for_recovery(target)
                    self.assertEqual(
                        recovery.exception.code,
                        "setup_restore_failed",
                    )
                    self.assertEqual(
                        tree_snapshot(install.skill_root / "state"),
                        baseline,
                    )


    def test_current_v20_runner_rows_fail_closed_at_every_public_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-r3b-current-runner-",
            dir=ROOT,
        ) as tmp:
            install, target = _fixed_current20(
                Path(tmp),
                identity_seed="r3b-current-runner-admission",
            )
            task_id = _add_task(
                self,
                target,
                title="Runner admission fixture",
                verification="python -m unittest",
                with_contract=True,
            )
            _seed_completion_gates(
                self,
                target,
                task_id,
                fingerprint=FINGERPRINT_A,
                verification_required=False,
            )
            publish_setup_backup(target, 3)
            backup_paths = sorted(
                target.resolved_backups_path.glob("taskgov-backup-v1_*.sqlite")
            )
            self.assertTrue(backup_paths)
            for path in (target.db_path, *backup_paths):
                _inject_valid_runner_resolution(path, task_id)

            baseline = tree_snapshot(install.skill_root / "state")

            resolution = resolve_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertEqual(resolution.error_code, "project_state_unreadable")
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                baseline,
            )

            with self.assertRaises(storage.StorageError) as inspected:
                storage.inspect_setup_state(target)
            self.assertEqual(inspected.exception.code, "project_state_unreadable")
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                baseline,
            )

            with closing(storage.connect(target.db_path)) as connection:
                before_digest = logical_database_digest(connection)
                with self.assertRaises(storage.StorageError) as writer:
                    storage.begin_initialized_write(connection, target)
                self.assertEqual(writer.exception.code, "project_state_unreadable")
                self.assertFalse(connection.in_transaction)
                self.assertEqual(logical_database_digest(connection), before_digest)
                with self.assertRaises(storage.StorageError) as reentry:
                    storage.apply_migrations(connection)
                self.assertEqual(reentry.exception.code, "project_state_unreadable")
                self.assertEqual(logical_database_digest(connection), before_digest)
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                baseline,
            )

            with closing(storage.connect(target.db_path)) as connection:
                with self.assertRaises(storage.StorageError) as managed:
                    storage.validate_managed_backup_source_database(
                        connection,
                        target,
                    )
            self.assertEqual(managed.exception.code, "project_state_unreadable")

            with closing(
                storage.connect_snapshot_readonly(target.db_path)
            ) as connection:
                with self.assertRaises(storage.StorageError) as viewer:
                    build_viewer_snapshot(connection, target)
            self.assertEqual(viewer.exception.code, "project_state_unreadable")
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                baseline,
            )

            with self.assertRaises(storage.StorageError) as backup:
                publish_setup_backup(target, 3)
            self.assertEqual(backup.exception.code, "setup_backup_failed")
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                baseline,
            )

            self.assertEqual(discover_managed_backup_metadata(target), ())
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                baseline,
            )

            with self.assertRaises(storage.StorageError) as recovery:
                select_managed_backup_for_recovery(target)
            self.assertEqual(recovery.exception.code, "setup_restore_failed")
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                baseline,
            )

    def test_current_v20_task_runner_basis_is_not_admitted(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m242-r3b-current-basis-",
            dir=ROOT,
        ) as tmp:
            install, target = _fixed_current20(
                Path(tmp),
                identity_seed="r3b-current-runner-basis",
            )
            task_id = _add_task(
                self,
                target,
                title="Runner basis fixture",
                verification="",
            )
            with closing(storage.connect(target.db_path)) as connection:
                connection.execute(
                    "UPDATE tasks SET review_target_runner_basis_version = 2 "
                    "WHERE task_id = ?",
                    (task_id,),
                )
                connection.commit()
                with self.assertRaises(storage.StorageError) as admitted:
                    storage.validate_current_schema20_admitted_rows(connection)
            self.assertEqual(admitted.exception.code, "project_state_unreadable")

            resolution = resolve_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertEqual(resolution.error_code, "project_state_unreadable")


if __name__ == "__main__":
    unittest.main()
