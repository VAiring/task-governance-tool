from __future__ import annotations

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
from tests.m14_test_support import tree_snapshot  # noqa: E402
from task_governance_tool import storage  # noqa: E402
from task_governance_tool.analysis_contracts import (  # noqa: E402
    build_descriptor,
    default_recipe,
)
from task_governance_tool.analysis_packet import build_analysis_packet  # noqa: E402
from task_governance_tool.evidence_consumer import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)
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

    def test_fresh_v21_bundle_reaches_analyzer_packet_without_evidence_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".tmp-m244b-fresh-analyzer-",
            dir=ROOT,
        ) as temporary:
            _install, target, task_id = (
                _schema21_fixture._seed_completed_m21_fixture(
                    self,
                    Path(temporary),
                )
            )
            publication = _schema21_fixture.publish_setup_evidence_projection(
                target,
                observed_at="2026-08-29T00:00:00Z",
            )
            self.assertEqual(publication.code, "succeeded")
            before = tree_snapshot(target.resolved_evidence_root)

            index = read_evidence_index(target.resolved_evidence_root)
            entry = next(row for row in index.entries if row["task_id"] == task_id)
            source = validate_evidence_source(index, entry)
            descriptor = build_descriptor(
                source_kind=source.source_kind,
                source_basis=source.source_basis,
                recipe=default_recipe(),
            )
            packet = build_analysis_packet(descriptor, source)

            self.assertEqual(
                (index.source_schema_version, index.format_version),
                (21, 2),
            )
            self.assertEqual(entry["bundle_format_version"], 2)
            self.assertEqual(source.source["format_version"], 2)
            self.assertEqual(source.source["payload"]["source_schema_version"], 21)
            self.assertEqual(descriptor["source_basis"], source.source_basis)
            self.assertEqual(packet.value["source_basis"], source.source_basis)
            self.assertEqual(packet.value["source"], source.source)
            self.assertEqual(
                tree_snapshot(target.resolved_evidence_root),
                before,
            )


if __name__ == "__main__":
    unittest.main()
