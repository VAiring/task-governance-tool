from __future__ import annotations

import shutil
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests.m14_test_support import initialize_taskgov_internal
from tests.m223_test_support import (
    V19_INDEXES,
    V19_TABLES,
    V19_TRIGGERS,
    logical_database_digest,
    remove_v19_bundle_storage_for_test,
    remove_v20_runner_shadow_for_test,
)
from tests.verification_receipt_test_support import (
    add_receipt,
    add_task as add_completion_task,
    completion as complete_task,
    initialize as initialize_completion_fixture,
    payload,
    run_taskgov,
    seed_current_review_evidence,
)
from task_governance_tool import evidence_projection as projection_service
from task_governance_tool import tasks as task_service
from task_governance_tool.evidence_projection import (
    build_projection_bundle_artifact,
)
from task_governance_tool.storage import (
    SCHEMA_VERSION,
    StorageError,
    apply_completion_evidence_bundle_migration,
    apply_migrations,
    capture_evidence_projection_basis,
    connect,
    current_schema_version,
    read_evidence_projection_state,
    record_evidence_projection_outcome_locked,
    validate_completion_evidence_bundle_storage,
    validate_evidence_ledger_storage,
    validate_evidence_ledger_storage_for_recovery,
)


class CompletionEvidenceBundleStorageTests(unittest.TestCase):
    def _initialized_database(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        db_path = root / "taskgov.sqlite"
        initialize_taskgov_internal(repo=repo, db=db_path)
        return db_path

    def _representative_completion_fixture(
        self,
        root: Path,
    ) -> tuple[Path, Path, str]:
        repo, db_path = initialize_completion_fixture(root)
        task = add_completion_task(
            db_path,
            repo,
            verification="focused verification",
            review_tier=1,
        )
        task_id = str(task["task_id"])
        generation = seed_current_review_evidence(
            db_path,
            repo,
            task_id,
        )
        verification = add_receipt(
            db_path,
            repo,
            task_id,
            generation,
        )
        if verification.returncode != 0:
            raise AssertionError(verification.stderr or verification.stdout)
        with closing(connect(db_path)) as connection:
            review_receipt_id = str(
                connection.execute(
                    "SELECT review_receipt_id FROM review_receipts "
                    "WHERE task_id = ? AND reviewer_key = ?",
                    (task_id, "test-reviewer-1"),
                ).fetchone()[0]
            )
        finding = run_taskgov(
            "review",
            "finding",
            "add",
            "--repo",
            str(repo),
            "--db",
            str(db_path),
            task_id,
            "--receipt-id",
            review_receipt_id,
            "--severity",
            "low",
            "--summary",
            "Representative current low finding",
            "--json",
        )
        if finding.returncode != 0:
            raise AssertionError(finding.stderr or finding.stdout)
        self.assertTrue(payload(finding)["data"]["finding"]["review_finding_id"])
        return repo, db_path, task_id

    def _completion_atomic_state(
        self,
        db_path: Path,
        task_id: str,
    ) -> tuple[object, ...]:
        tables = (
            "task_completion_cycles",
            "completion_evidence_bundles",
            "completion_bundle_members",
            "criterion_evidence_links",
            "completion_bundle_finding_snapshots",
            "evidence_references",
            "task_events",
        )
        with closing(connect(db_path)) as connection:
            counts = tuple(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
                for table_name in tables
            )
            source_generation = connection.execute(
                "SELECT source_generation FROM evidence_projection_state"
            ).fetchone()[0]
            task_row = connection.execute(
                "SELECT status, completed_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return (
                *counts,
                source_generation,
                task_row["status"],
                task_row["completed_at"],
            )

    def _corrupt_sealed_bundle_value(
        self,
        db_path: Path,
        *,
        trigger_name: str,
        update_sql: str,
    ) -> None:
        with closing(connect(db_path)) as connection:
            trigger_row = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name = ?",
                (trigger_name,),
            ).fetchone()
            self.assertIsNotNone(trigger_row)
            trigger_sql = str(trigger_row["sql"])
            connection.execute(f'DROP TRIGGER "{trigger_name}"')
            changed_before = connection.total_changes
            connection.execute(update_sql)
            self.assertEqual(connection.total_changes - changed_before, 1)
            connection.execute(trigger_sql)
            connection.commit()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'trigger' AND name = ?",
                    (trigger_name,),
                ).fetchone()[0],
                1,
            )

    def test_complete_schema_v19_has_exact_bundle_foundation_and_reenters(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = self._initialized_database(Path(directory))
            with closing(connect(db_path)) as connection:
                self.assertEqual(SCHEMA_VERSION, 21)
                remove_v20_runner_shadow_for_test(connection)
                self.assertEqual(current_schema_version(connection), 19)
                marker = connection.execute(
                    "SELECT name FROM schema_migrations WHERE version = 19"
                ).fetchone()
                self.assertEqual(marker["name"], "completion_evidence_bundles")
                for object_type, names in (
                    ("table", V19_TABLES),
                    ("index", V19_INDEXES),
                    ("trigger", V19_TRIGGERS),
                ):
                    observed = {
                        str(row["name"])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = ?",
                            (object_type,),
                        ).fetchall()
                    }
                    self.assertTrue(set(names) <= observed)
                cycle_columns = {
                    str(row["name"]): row
                    for row in connection.execute(
                        "PRAGMA table_info(task_completion_cycles)"
                    ).fetchall()
                }
                self.assertEqual(
                    cycle_columns["evidence_basis_version"]["dflt_value"],
                    "0",
                )
                self.assertIn("completion_evidence_bundle_id", cycle_columns)
                state = connection.execute(
                    "SELECT * FROM evidence_projection_state"
                ).fetchone()
                self.assertEqual(state["source_generation"], 0)
                self.assertIsNone(state["published_generation"])
                before = logical_database_digest(connection)
                apply_completion_evidence_bundle_migration(connection)
                self.assertEqual(logical_database_digest(connection), before)
                validate_completion_evidence_bundle_storage(connection)

    def test_v18_to_v19_preserves_all_v18_rows_and_invents_no_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = self._initialized_database(Path(directory))
            with closing(connect(db_path)) as connection:
                remove_v19_bundle_storage_for_test(connection)
                self.assertEqual(current_schema_version(connection), 18)
                validate_evidence_ledger_storage(connection)
                before = logical_database_digest(connection)
                apply_completion_evidence_bundle_migration(connection)
                self.assertEqual(current_schema_version(connection), 19)
                for table_name in V19_TABLES[:-1]:
                    count = connection.execute(
                        f'SELECT COUNT(*) FROM "{table_name}"'
                    ).fetchone()[0]
                    self.assertEqual(count, 0)
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM task_completion_cycles
                         WHERE evidence_basis_version != 0
                            OR completion_evidence_bundle_id IS NOT NULL
                        """
                    ).fetchone()[0],
                    0,
                )
                self.assertNotEqual(logical_database_digest(connection), before)
                validate_completion_evidence_bundle_storage(connection)

    def test_v19_migration_rolls_back_every_injected_stage(self):
        stages = (
            "after_tables",
            "after_columns",
            "after_objects",
            "after_state",
            "after_marker",
            "before_commit",
        )
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                db_path = self._initialized_database(Path(directory))
                with closing(connect(db_path)) as connection:
                    remove_v19_bundle_storage_for_test(connection)
                    before = logical_database_digest(connection)
                    with self.assertRaises(StorageError) as raised:
                        apply_completion_evidence_bundle_migration(
                            connection,
                            fail_stage=stage,
                        )
                    self.assertEqual(raised.exception.code, "internal_error")
                    self.assertEqual(current_schema_version(connection), 18)
                    self.assertEqual(logical_database_digest(connection), before)
                    validate_evidence_ledger_storage(connection)

    def test_projection_state_outcomes_are_typed_and_recovery_stays_set_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = self._initialized_database(Path(directory))
            with closing(connect(db_path)) as connection:
                project_id = str(
                    connection.execute(
                        "SELECT project_id FROM project_meta"
                    ).fetchone()[0]
                )
                connection.execute("BEGIN IMMEDIATE")
                succeeded = record_evidence_projection_outcome_locked(
                    connection,
                    project_id=project_id,
                    captured_generation=0,
                    outcome_code="succeeded",
                    recorded_at="2026-08-05T00:00:00Z",
                    index_digest="sha256:" + "a" * 64,
                )
                connection.commit()
                self.assertEqual(succeeded.published_generation, 0)
                self.assertEqual(succeeded.last_outcome_code, "succeeded")

                connection.execute("BEGIN IMMEDIATE")
                failed = record_evidence_projection_outcome_locked(
                    connection,
                    project_id=project_id,
                    captured_generation=0,
                    outcome_code="failed",
                    recorded_at="2026-08-05T00:00:01Z",
                )
                connection.commit()
                self.assertEqual(failed.published_generation, 0)
                self.assertEqual(failed.last_outcome_code, "failed")
                self.assertEqual(
                    read_evidence_projection_state(
                        connection,
                        project_id=project_id,
                    ),
                    failed,
                )

                connection.execute(
                    "UPDATE evidence_projection_state "
                    "SET source_generation = CAST(0.5 AS REAL) "
                    "WHERE project_id = ?",
                    (project_id,),
                )
                connection.commit()
                with self.assertRaises(StorageError) as raised:
                    validate_evidence_ledger_storage_for_recovery(connection)
                self.assertEqual(
                    raised.exception.code,
                    "evidence_ledger_inconsistent",
                )

    def test_prepared_canonical_bundle_mismatches_are_atomic_no_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, base_db, task_id = self._representative_completion_fixture(
                root
            )
            before = self._completion_atomic_state(base_db, task_id)
            real_build = task_service.build_native_bundle_plan

            for mismatch in (
                "bundle_digest",
                "payload_size",
                "finding_digest",
            ):
                with self.subTest(mismatch=mismatch):
                    case_db = root / f"prepared-{mismatch}.sqlite"
                    shutil.copyfile(base_db, case_db)

                    def build_mismatched_plan(*args, **kwargs):
                        plan = real_build(*args, **kwargs)
                        wrong_digest = "sha256:" + "0" * 64
                        if plan.artifact.bundle_digest == wrong_digest:
                            wrong_digest = "sha256:" + "1" * 64
                        if mismatch == "bundle_digest":
                            return replace(
                                plan,
                                artifact=replace(
                                    plan.artifact,
                                    bundle_digest=wrong_digest,
                                ),
                            )
                        if mismatch == "payload_size":
                            return replace(
                                plan,
                                artifact=replace(
                                    plan.artifact,
                                    payload_bytes=(
                                        plan.artifact.payload_bytes + b" "
                                    ),
                                ),
                            )
                        self.assertTrue(plan.finding_snapshots)
                        finding = dict(plan.finding_snapshots[0])
                        if finding["digest"] == wrong_digest:
                            wrong_digest = "sha256:" + "2" * 64
                        finding["digest"] = wrong_digest
                        return replace(
                            plan,
                            finding_snapshots=(
                                finding,
                                *plan.finding_snapshots[1:],
                            ),
                        )

                    with mock.patch.object(
                        task_service,
                        "build_native_bundle_plan",
                        side_effect=build_mismatched_plan,
                    ):
                        result = complete_task(case_db, repo, task_id)
                    self.assertNotEqual(
                        result.returncode,
                        0,
                        result.stdout,
                    )
                    self.assertEqual(
                        self._completion_atomic_state(case_db, task_id),
                        before,
                    )

    def test_persisted_canonical_bundle_mismatches_fail_validation_and_reentry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, completed_db, task_id = self._representative_completion_fixture(
                root
            )
            completed = complete_task(completed_db, repo, task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            corruptions = (
                (
                    "bundle_digest",
                    "trg_completion_evidence_bundles_no_update",
                    "UPDATE completion_evidence_bundles "
                    "SET bundle_digest = 'sha256:' || printf('%064d', 0)",
                ),
                (
                    "payload_size",
                    "trg_completion_evidence_bundles_no_update",
                    "UPDATE completion_evidence_bundles "
                    "SET payload_size_bytes = payload_size_bytes + 1",
                ),
                (
                    "finding_digest",
                    "trg_completion_bundle_finding_snapshots_no_update",
                    "UPDATE completion_bundle_finding_snapshots "
                    "SET digest = 'sha256:' || printf('%064d', 0)",
                ),
            )
            for mismatch, trigger_name, update_sql in corruptions:
                for check in ("stored_validator", "current_reentry"):
                    with self.subTest(mismatch=mismatch, check=check):
                        case_db = root / f"persisted-{mismatch}-{check}.sqlite"
                        shutil.copyfile(completed_db, case_db)
                        self._corrupt_sealed_bundle_value(
                            case_db,
                            trigger_name=trigger_name,
                            update_sql=update_sql,
                        )
                        with closing(connect(case_db)) as connection:
                            if check == "stored_validator":
                                with self.assertRaises(StorageError) as raised:
                                    validate_completion_evidence_bundle_storage(
                                        connection
                                    )
                                self.assertEqual(
                                    raised.exception.code,
                                    "evidence_ledger_inconsistent",
                                )
                            else:
                                with self.assertRaises(StorageError) as raised:
                                    apply_migrations(connection)
                                self.assertEqual(
                                    raised.exception.code,
                                    "project_state_unreadable",
                                )

    def test_native_prepare_is_read_only_and_bundle_cycle_persist_atomically(self):
        with self.subTest(outcome="success"), tempfile.TemporaryDirectory() as directory:
            repo, db_path = initialize_completion_fixture(Path(directory))
            task = add_completion_task(
                db_path,
                repo,
                verification="",
                review_tier=0,
            )
            seed_current_review_evidence(db_path, repo, task["task_id"])
            prepare_deltas: list[int] = []
            real_prepare = task_service.prepare_native_completion_cycle_locked

            def observe_prepare(connection, *args, **kwargs):
                before = connection.total_changes
                prepared = real_prepare(connection, *args, **kwargs)
                prepare_deltas.append(connection.total_changes - before)
                return prepared

            with mock.patch.object(
                task_service,
                "prepare_native_completion_cycle_locked",
                side_effect=observe_prepare,
            ):
                result = complete_task(db_path, repo, task["task_id"])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(prepare_deltas, [0])
            with closing(connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_completion_cycles"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM completion_evidence_bundles"
                    ).fetchone()[0],
                    1,
                )
                cycle = connection.execute(
                    "SELECT evidence_basis_version, "
                    "completion_evidence_bundle_id "
                    "FROM task_completion_cycles"
                ).fetchone()
                self.assertEqual(cycle["evidence_basis_version"], 1)
                self.assertIsNotNone(cycle["completion_evidence_bundle_id"])
                state = read_evidence_projection_state(
                    connection,
                    project_id=str(task["project_id"]),
                )
                self.assertEqual(state.source_generation, 1)
                validate_completion_evidence_bundle_storage(connection)
                projection = capture_evidence_projection_basis(
                    connection,
                    project_id=str(task["project_id"]),
                )
                self.assertEqual(projection.source_generation, 1)
                self.assertEqual(len(projection.cycles), 1)
                self.assertEqual(len(projection.bundles), 1)
                self.assertEqual(len(projection.native_bundles), 1)
                replay = build_projection_bundle_artifact(
                    projection.native_bundles[0]
                )
                self.assertIsNone(
                    replay.payload["target"]["base_revision"]
                )
                self.assertTrue(
                    all(
                        reference["target_base_revision"] is None
                        for reference in replay.payload[
                            "evidence_references"
                        ]
                    )
                )

        with self.subTest(outcome="rollback"), tempfile.TemporaryDirectory() as directory:
            repo, db_path = initialize_completion_fixture(Path(directory))
            task = add_completion_task(
                db_path,
                repo,
                verification="",
                review_tier=0,
            )
            seed_current_review_evidence(db_path, repo, task["task_id"])
            atomic_tables = (
                "task_completion_cycles",
                "completion_evidence_bundles",
                "completion_bundle_members",
                "criterion_evidence_links",
                "completion_bundle_finding_snapshots",
                "evidence_references",
                "task_events",
            )

            def atomic_state() -> tuple[object, ...]:
                with closing(connect(db_path)) as connection:
                    counts = tuple(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0]
                        for table_name in atomic_tables
                    )
                    source_generation = connection.execute(
                        "SELECT source_generation "
                        "FROM evidence_projection_state"
                    ).fetchone()[0]
                    task_row = connection.execute(
                        "SELECT status, completed_at FROM tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()
                    return (
                        *counts,
                        source_generation,
                        task_row["status"],
                        task_row["completed_at"],
                    )

            before = atomic_state()
            inserted: list[object] = []
            real_insert = task_service.insert_native_completion_cycle_locked

            def fail_after_insert(*args, **kwargs):
                persisted = real_insert(*args, **kwargs)
                inserted.append(persisted)
                raise StorageError(
                    "internal_error",
                    "injected Bundle/cycle rollback",
                )

            with mock.patch.object(
                task_service,
                "insert_native_completion_cycle_locked",
                side_effect=fail_after_insert,
            ):
                result = complete_task(db_path, repo, task["task_id"])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(inserted), 1)
            self.assertEqual(atomic_state(), before)

    def test_recompletion_keeps_earlier_referenced_finding_unlinked(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, db_path = initialize_completion_fixture(Path(directory))
            added = run_taskgov(
                "task",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db_path),
                "--title",
                "Historical Finding Bundle canary",
                "--status",
                "in_progress",
                "--review-tier",
                "1",
                "--verification",
                "focused verification",
                "--contract-scope",
                "Exercise Bundle Finding selection",
                "--contract-acceptance",
                "Stored Bundle validates",
                "--contract-constraints",
                "No network",
                "--contract-authority-ref",
                "contract:TG-M22.3",
                "--json",
            )
            self.assertEqual(added.returncode, 0, added.stdout)
            task = payload(added)["data"]["task"]
            task_id = str(task["task_id"])

            first_generation = seed_current_review_evidence(
                db_path,
                repo,
                task_id,
            )
            with closing(connect(db_path)) as connection:
                first_receipt_id = str(
                    connection.execute(
                        "SELECT review_receipt_id FROM review_receipts "
                        "WHERE task_id = ? AND target_generation = ?",
                        (task_id, first_generation),
                    ).fetchone()[0]
                )
            finding = run_taskgov(
                "review",
                "finding",
                "add",
                "--repo",
                str(repo),
                "--db",
                str(db_path),
                task_id,
                "--receipt-id",
                first_receipt_id,
                "--severity",
                "medium",
                "--summary",
                "Earlier generation issue",
                "--json",
            )
            self.assertEqual(finding.returncode, 0, finding.stdout)
            finding_id = str(
                payload(finding)["data"]["finding"]["review_finding_id"]
            )
            resolved = run_taskgov(
                "review",
                "finding",
                "resolve",
                "--repo",
                str(repo),
                "--db",
                str(db_path),
                finding_id,
                "--resolution",
                "Issue corrected and verified",
                "--json",
            )
            self.assertEqual(resolved.returncode, 0, resolved.stdout)

            second_generation = seed_current_review_evidence(
                db_path,
                repo,
                task_id,
                fingerprint="sha256:" + ("b" * 64),
            )
            verified = add_receipt(
                db_path,
                repo,
                task_id,
                second_generation,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout)
            first_completion = complete_task(db_path, repo, task_id)
            self.assertEqual(
                first_completion.returncode,
                0,
                first_completion.stdout,
            )

            reopened = run_taskgov(
                "task",
                "edit",
                "--repo",
                str(repo),
                "--db",
                str(db_path),
                task_id,
                "--status",
                "in_progress",
                "--reopen-reason",
                "Exercise historical Finding recompletion",
                "--json",
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout)
            third_generation = seed_current_review_evidence(
                db_path,
                repo,
                task_id,
                fingerprint="sha256:" + ("c" * 64),
            )
            verified = add_receipt(
                db_path,
                repo,
                task_id,
                third_generation,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout)
            second_completion = complete_task(db_path, repo, task_id)
            self.assertEqual(
                second_completion.returncode,
                0,
                second_completion.stdout,
            )

            with closing(connect(db_path)) as connection:
                latest = connection.execute(
                    "SELECT completion_evidence_bundle_id "
                    "FROM task_completion_cycles WHERE task_id = ? "
                    "ORDER BY saved_cycle_ordinal DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                bundle_id = str(latest["completion_evidence_bundle_id"])
                snapshot = connection.execute(
                    "SELECT target_generation, evidence_reference_id "
                    "FROM completion_bundle_finding_snapshots "
                    "WHERE completion_evidence_bundle_id = ? "
                    "AND review_finding_id = ?",
                    (bundle_id, finding_id),
                ).fetchone()
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot["target_generation"], first_generation)
                self.assertIsNotNone(snapshot["evidence_reference_id"])
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM completion_bundle_members AS member "
                        "JOIN criterion_evidence_links AS link "
                        "ON link.criterion_evidence_link_id = "
                        "member.criterion_evidence_link_id "
                        "WHERE member.completion_evidence_bundle_id = ? "
                        "AND link.evidence_reference_id = ? "
                        "AND link.relation = 'review_finding'",
                        (bundle_id, snapshot["evidence_reference_id"]),
                    ).fetchone()[0],
                    0,
                )
                validate_completion_evidence_bundle_storage(connection)

    def test_public_completion_bundle_overflow_is_sanitized_and_no_write(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, db_path, task_id = self._representative_completion_fixture(
                Path(directory)
            )
            with closing(connect(db_path)) as connection:
                before = logical_database_digest(connection)

            with mock.patch.object(
                projection_service,
                "BUNDLE_MAX_BYTES",
                10,
            ):
                result = complete_task(db_path, repo, task_id)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                payload(result)["errors"],
                [
                    {
                        "code": "evidence_bundle_too_large",
                        "message": (
                            "completion evidence bundle exceeds the "
                            "supported size"
                        ),
                    }
                ],
            )
            with closing(connect(db_path)) as connection:
                self.assertEqual(logical_database_digest(connection), before)

    def test_public_completion_projection_inconsistency_is_tool_error_no_write(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, db_path, task_id = self._representative_completion_fixture(
                Path(directory)
            )
            with closing(connect(db_path)) as connection:
                before = logical_database_digest(connection)

            with mock.patch.object(
                task_service,
                "build_native_bundle_plan",
                side_effect=projection_service.EvidenceProjectionError(
                    "evidence_ledger_inconsistent",
                    "stored evidence ledger is inconsistent",
                ),
            ):
                result = complete_task(db_path, repo, task_id)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                payload(result)["errors"],
                [
                    {
                        "code": "evidence_ledger_inconsistent",
                        "message": "stored evidence ledger is inconsistent",
                    }
                ],
            )
            with closing(connect(db_path)) as connection:
                self.assertEqual(logical_database_digest(connection), before)


if __name__ == "__main__":
    unittest.main()
