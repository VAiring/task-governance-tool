from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests import test_m242_r3a_schema20_storage as _schema20_fixture  # noqa: E402
from tests import test_m243b_schema21_compatibility as _schema21_fixture  # noqa: E402
from tests.evidence_reader_oracle import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)
from tests.m14_test_support import tree_snapshot  # noqa: E402
from tests.test_m242_runner_service import RunnerServiceFixture  # noqa: E402
from tests.test_m243c_runner_gate import _complete_runner_pass  # noqa: E402
from task_governance_tool import storage  # noqa: E402
from task_governance_tool.evidence_projection import (  # noqa: E402
    build_projection_bundle_artifact,
)


_DURABLE_HISTORY_TABLES = (
    "tasks",
    "task_contract_revisions",
    "contract_criteria",
    "authority_snapshots",
    "authority_snapshot_criteria",
    "artifact_manifests",
    "verification_receipts",
    "review_receipts",
    "review_findings",
    "task_completion_cycles",
    "evidence_references",
    "criterion_evidence_links",
    "completion_evidence_bundles",
    "completion_bundle_members",
    "completion_bundle_finding_snapshots",
    "task_events",
)


def _history_projection(
    connection,
    baseline: dict[str, tuple[tuple[str, ...], tuple]] | None = None,
) -> dict[str, tuple[tuple[str, ...], tuple]]:
    projection = {}
    for table_name in _DURABLE_HISTORY_TABLES:
        current_columns = tuple(
            str(row["name"])
            for row in connection.execute(f'PRAGMA table_info("{table_name}")')
        )
        if not current_columns:
            raise AssertionError(f"missing history table: {table_name}")
        columns = (
            current_columns
            if baseline is None
            else baseline[table_name][0]
        )
        if not set(columns).issubset(current_columns):
            raise AssertionError(f"history columns were removed: {table_name}")
        column_sql = ", ".join(f'"{column}"' for column in columns)
        rows = tuple(
            sorted(
                (
                    tuple(row)
                    for row in connection.execute(
                        f'SELECT {column_sql} FROM "{table_name}"'
                    )
                ),
                key=repr,
            )
        )
        projection[table_name] = (columns, rows)
    return projection


def _only_bundle_artifact(connection, project_id: str, schema_version: int):
    with mock.patch.object(storage, "SCHEMA_VERSION", schema_version):
        basis = storage.capture_evidence_projection_basis(
            connection,
            project_id=project_id,
        )
    if len(basis.native_bundles) != 1:
        raise AssertionError("expected one preserved native Bundle")
    return build_projection_bundle_artifact(basis.native_bundles[0])


class M244BLegacyFreshAcceptanceTests(unittest.TestCase):
    def test_v19_history_survives_separate_v20_and_v21_migrations(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m244b-legacy-chain-",
            dir=ROOT,
        ) as temporary:
            fixture = _schema20_fixture.R3ASchema20StorageTests(
                "test_private_migration_inventory_preservation_and_reentry"
            )
            fixture.root = Path(temporary)
            db_path = fixture._fresh_v19("history")

            with closing(storage.connect(db_path)) as connection:
                history_basis, original_payload = (
                    fixture._seed_nonempty_v19_closure(connection)
                )
                self.assertEqual(storage.current_schema_version(connection), 19)
                before = _history_projection(connection)
                original_bundle = _only_bundle_artifact(
                    connection,
                    str(history_basis["project_id"]),
                    19,
                )
                self.assertEqual(original_bundle.payload_bytes, original_payload)
                self.assertEqual(original_bundle.payload["source_schema_version"], 19)
                self.assertEqual(original_bundle.payload["bundle_version"], 1)

            storage.rehearse_schema20_storage(db_path)
            with closing(storage.connect(db_path)) as connection:
                storage.validate_schema20_storage(connection)
                self.assertEqual(storage.current_schema_version(connection), 20)
                self.assertEqual(_history_projection(connection, before), before)
                schema20_bundle = _only_bundle_artifact(
                    connection,
                    str(history_basis["project_id"]),
                    20,
                )
                self.assertEqual(schema20_bundle.document, original_bundle.document)

            storage.rehearse_schema21_storage(db_path)
            with closing(storage.connect(db_path)) as connection:
                storage.validate_schema21_storage(connection)
                self.assertEqual(storage.current_schema_version(connection), 21)
                self.assertEqual(_history_projection(connection, before), before)
                schema21_bundle = _only_bundle_artifact(
                    connection,
                    str(history_basis["project_id"]),
                    21,
                )
                self.assertEqual(schema21_bundle.document, original_bundle.document)
                self.assertEqual(
                    tuple(
                        tuple(row)
                        for row in connection.execute(
                            "SELECT version, name FROM schema_migrations "
                            "WHERE version >= 19 ORDER BY version"
                        )
                    ),
                    (
                        (19, "completion_evidence_bundles"),
                        (20, storage.PRIVATE_SCHEMA20_MIGRATION_NAME),
                        (21, storage.PRIVATE_SCHEMA21_MIGRATION_NAME),
                    ),
                )

    def _assert_published_current22_source(self, target, task_id: str):
        publication = _schema21_fixture.publish_setup_evidence_projection(
            target,
            observed_at="2026-08-29T00:00:00Z",
        )
        self.assertEqual(publication.code, "succeeded")
        before = tree_snapshot(target.resolved_evidence_root)
        published_index = json.loads(target.resolved_evidence_index.read_bytes())
        published_entry = next(
            row
            for row in published_index["payload"]["entries"]
            if row["task_id"] == task_id
        )
        published_bundle = json.loads(
            (target.resolved_evidence_root / published_entry["bundle_file"]).read_bytes()
        )

        index = read_evidence_index(target.resolved_evidence_root)
        entry = next(row for row in index.entries if row["task_id"] == task_id)
        source = validate_evidence_source(index, entry)
        self.assertEqual((index.source_schema_version, index.format_version), (22, 2))
        self.assertEqual(index.project_id, target.project.project_id)
        self.assertEqual(
            index.projection_generation,
            published_index["payload"]["projection_generation"],
        )
        self.assertEqual(index.index_digest, published_index["index_digest"])
        self.assertEqual(entry, published_entry)
        self.assertEqual(entry["bundle_format_version"], 2)
        self.assertEqual(source.source_kind, "native_bundle")
        self.assertEqual(
            source.source_basis,
            {
                "index_format_version": 2,
                "source_schema_version": 22,
                "project_id": index.project_id,
                "projection_generation": index.projection_generation,
                "index_digest": index.index_digest,
                "entry": published_entry,
            },
        )
        self.assertEqual(source.source, published_bundle)
        self.assertEqual(source.source["format_version"], 2)
        self.assertEqual(source.source["payload"]["source_schema_version"], 22)
        self.assertEqual(source.source["payload"]["bundle_version"], 2)
        self.assertEqual(source.source["payload"]["task"]["task_id"], task_id)
        self.assertEqual(tree_snapshot(target.resolved_evidence_root), before)
        return source

    def test_fresh_current22_bundle_reaches_independent_reader_without_evidence_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m244b-fresh-reader-",
            dir=ROOT,
        ) as temporary:
            _install, target, task_id = (
                _schema21_fixture._seed_completed_m21_fixture(
                    self,
                    Path(temporary),
                    source_schema_version=22,
                )
            )
            source = self._assert_published_current22_source(target, task_id)
            payload = source.source["payload"]
            self.assertEqual(
                payload["verification_basis"],
                {
                    "basis_version": 1,
                    "kind": "caller_attestation",
                    "verification_receipt_id": payload["verification_receipt"][
                        "verification_receipt_id"
                    ],
                    "runner_observation_id": None,
                },
            )
            self.assertIsNone(payload["runner_observation"])

    def test_fresh_not_required_completion_reaches_independent_reader(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m244b-not-required-reader-",
            dir=ROOT,
        ) as temporary:
            _install, target, task_id = _schema21_fixture._seed_completed_m21_fixture(
                self,
                Path(temporary),
                verification_required=False,
                source_schema_version=22,
            )
            source = self._assert_published_current22_source(target, task_id)
            payload = source.source["payload"]
            self.assertEqual(
                payload["verification_basis"],
                {
                    "basis_version": 1,
                    "kind": "not_required",
                    "verification_receipt_id": None,
                    "runner_observation_id": None,
                },
            )
            self.assertEqual(payload["task"]["verification"], "")
            self.assertIsNone(payload["verification_receipt"])
            self.assertIsNone(payload["runner_observation"])

    def test_fresh_runner_completion_reaches_independent_reader(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m244b-runner-reader-",
            dir=ROOT,
        ) as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            # The existing fixture completes against a test-only physical-basis mock.
            _complete_runner_pass(fixture)
            storage.configure_project_maintenance(
                fixture.target,
                requested_interval_minutes=None,
                requested_generations=None,
            )
            source = self._assert_published_current22_source(fixture.target, fixture.task_id)
            payload = source.source["payload"]
            runner = payload["runner_observation"]
            self.assertEqual(
                payload["verification_basis"],
                {
                    "basis_version": 1,
                    "kind": "runner_observation",
                    "verification_receipt_id": None,
                    "runner_observation_id": runner["observation_id"],
                },
            )
            self.assertIsNone(payload["verification_receipt"])
            self.assertEqual(
                (runner["gate_eligibility_version"], runner["route"], runner["outcome"]),
                (1, "runner", "pass"),
            )
            self.assertEqual(runner["complete_plan"], 1)
            self.assertEqual(runner["completed_step_count"], runner["total_step_count"])


if __name__ == "__main__":
    unittest.main()
