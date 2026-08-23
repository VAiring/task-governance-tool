from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import (
    file_snapshot,
    json_payload,
    make_physical_install,
    replace_install_package_preserving_state,
    require_repository_git,
    setup_exact_install,
    tree_snapshot,
)
from tests.m223_test_support import logical_database_digest
from tests.m224_report_consumer import read_evidence_report


SCHEMA_V17_COMMIT = "92ab0060f3e7fa08f929cd02b3475f15c539cb0d"
SCHEMA_V17_PACKAGE_TREE = "44c50fa7596bd544c4aaf3876b937b11cde4d470"
TARGET_FINGERPRINT = "sha256:" + ("7" * 64)


def require_cli_json(install, *arguments: str) -> dict[str, object]:
    result = install.run(*arguments, "--json")
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json_payload(result)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database_logical_digest(path: Path) -> str:
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return logical_database_digest(connection)


def row_projection(
    path: Path,
    table_name: str,
    key_name: str,
    key_value: str,
) -> tuple[tuple[str, ...], tuple[object, ...]]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        columns = tuple(
            str(row[1])
            for row in connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
        )
        projection = ", ".join(f'"{column}"' for column in columns)
        row = connection.execute(
            f'SELECT {projection} FROM "{table_name}" WHERE "{key_name}" = ?',
            (key_value,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"missing fixture row in {table_name}")
    return columns, tuple(row)


def project_columns(
    path: Path,
    table_name: str,
    key_name: str,
    key_value: str,
    columns: tuple[str, ...],
) -> tuple[object, ...]:
    uri = path.resolve().as_uri() + "?mode=ro"
    projection = ", ".join(f'"{column}"' for column in columns)
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        row = connection.execute(
            f'SELECT {projection} FROM "{table_name}" WHERE "{key_name}" = ?',
            (key_value,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"missing migrated row in {table_name}")
    return tuple(row)


class M224PackageForwardTests(unittest.TestCase):
    def test_schema_v17_state_migrates_without_inventing_legacy_evidence(self):
        self.assertEqual(
            require_repository_git(
                "rev-parse",
                f"{SCHEMA_V17_COMMIT}^{{commit}}",
            ).decode("ascii").strip(),
            SCHEMA_V17_COMMIT,
        )
        self.assertEqual(
            require_repository_git(
                "rev-parse",
                f"{SCHEMA_V17_COMMIT}:task-governance-tool",
            ).decode("ascii").strip(),
            SCHEMA_V17_PACKAGE_TREE,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = setup_exact_install(root / "schema-v17", SCHEMA_V17_COMMIT)
            legacy_package = file_snapshot(install.skill_root, exclude_state=True)

            added = require_cli_json(
                install,
                "task",
                "add",
                "--title",
                "Schema v17 Evidence forward fixture",
                "--status",
                "in_progress",
                "--review-tier",
                "1",
            )
            project_id = str(added["project_id"])
            task_id = str(added["data"]["task"]["task_id"])
            require_cli_json(
                install,
                "review",
                "target",
                "set",
                task_id,
                "--kind",
                "diff_fingerprint",
                "--revision",
                TARGET_FINGERPRINT,
            )
            reviewed = require_cli_json(
                install,
                "review",
                "receipt",
                "add",
                task_id,
                "--reviewer",
                "schema-v17-reviewer",
                "--kind",
                "independent",
                "--verdict",
                "pass",
                "--summary",
                "Schema v17 caller-attested review",
            )
            receipt_id = str(
                reviewed["data"]["receipt"]["review_receipt_id"]
            )
            completed = require_cli_json(
                install,
                "task",
                "complete",
                task_id,
                "--verification-complete",
                "--review-complete",
                "--commit-not-required",
            )
            self.assertEqual(completed["data"]["task"]["status"], "done")

            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    17,
                )
                cycle_id = str(
                    connection.execute(
                        "SELECT completion_cycle_id "
                        "FROM task_completion_cycles WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()[0]
                )

            receipt_columns, receipt_assertion = row_projection(
                install.db_path,
                "review_receipts",
                "review_receipt_id",
                receipt_id,
            )
            cycle_columns, legacy_cycle = row_projection(
                install.db_path,
                "task_completion_cycles",
                "completion_cycle_id",
                cycle_id,
            )
            source_state = tree_snapshot(install.skill_root / "state")
            source_db_hash = file_digest(install.db_path)
            source_logical_digest = database_logical_digest(install.db_path)
            oracle_state = root / "schema-v17-state-oracle"
            shutil.copytree(install.skill_root / "state", oracle_state)
            oracle_snapshot = tree_snapshot(oracle_state)

            current_copy = make_physical_install(root / "current-package")
            current_package = file_snapshot(
                current_copy.skill_root,
                exclude_state=True,
            )
            retired_package = replace_install_package_preserving_state(
                install,
                current_copy.skill_root,
            )

            self.assertEqual(
                file_snapshot(retired_package, exclude_state=True),
                legacy_package,
            )
            self.assertEqual(
                file_snapshot(install.skill_root, exclude_state=True),
                current_package,
            )
            self.assertEqual(tree_snapshot(install.skill_root / "state"), source_state)
            self.assertEqual(file_digest(install.db_path), source_db_hash)
            self.assertEqual(
                database_logical_digest(install.db_path),
                source_logical_digest,
            )

            preview_tree = tree_snapshot(install.skill_root / "state")
            preview_db_hash = file_digest(install.db_path)
            preview_logical_digest = database_logical_digest(install.db_path)
            preview = require_cli_json(install, "setup", "--read-only")

            self.assertEqual(preview["data"]["status"], "setup_preview")
            self.assertEqual(preview["data"]["schema_from"], 17)
            self.assertIn("database_migrate", preview["data"]["planned_writes"])
            self.assertEqual(preview["data"]["completed_writes"], [])
            self.assertEqual(
                tree_snapshot(install.skill_root / "state"),
                preview_tree,
            )
            self.assertEqual(file_digest(install.db_path), preview_db_hash)
            self.assertEqual(
                database_logical_digest(install.db_path),
                preview_logical_digest,
            )

            migrated = require_cli_json(install, "setup")
            self.assertEqual(migrated["data"]["schema_from"], 17)
            self.assertIn("database_migrate", migrated["data"]["completed_writes"])
            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    20,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM review_receipt_provenance "
                        "WHERE review_receipt_id = ?",
                        (receipt_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM evidence_references "
                        "WHERE source_kind = 'review_receipt' AND source_id = ?",
                        (receipt_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT evidence_basis_version, "
                        "completion_evidence_bundle_id "
                        "FROM task_completion_cycles "
                        "WHERE completion_cycle_id = ?",
                        (cycle_id,),
                    ).fetchone(),
                    (0, None),
                )

            self.assertEqual(
                project_columns(
                    install.db_path,
                    "review_receipts",
                    "review_receipt_id",
                    receipt_id,
                    receipt_columns,
                ),
                receipt_assertion,
            )
            self.assertEqual(
                project_columns(
                    install.db_path,
                    "task_completion_cycles",
                    "completion_cycle_id",
                    cycle_id,
                    cycle_columns,
                ),
                legacy_cycle,
            )

            shown = require_cli_json(install, "task", "show", task_id)
            evidence = shown["data"]["review_evidence"]
            shown_receipt = next(
                receipt
                for receipt in evidence["recent_receipts"]
                if receipt["review_receipt_id"] == receipt_id
            )
            self.assertEqual(
                shown_receipt["review_provenance"],
                {
                    "review_provenance_id": None,
                    "provenance_version": 0,
                    "reviewer_class": None,
                    "model_state": None,
                    "declared_model_id": None,
                    "skill_state": None,
                    "declared_skill_id": None,
                    "declared_skill_version": None,
                    "review_profiles": None,
                    "review_lenses": None,
                    "context_relation": None,
                    "method_codes": None,
                    "assurance_class": "legacy_unknown",
                    "producer_class": "legacy_migration",
                    "producer_version": 1,
                    "digest": None,
                },
            )
            self.assertEqual(shown_receipt["reviewer_key"], "schema-v17-reviewer")
            self.assertEqual(shown_receipt["receipt_kind"], "independent")
            self.assertEqual(shown_receipt["verdict"], "pass")
            self.assertEqual(
                shown_receipt["summary"],
                "Schema v17 caller-attested review",
            )
            self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 1)
            self.assertTrue(evidence["gate"]["satisfied"])

            index_path = install.fixed_root / "evidence" / "index.json"
            index_bytes = index_path.read_bytes()
            index = json.loads(index_bytes)
            self.assertEqual(index["format_version"], 2)
            self.assertEqual(index["payload"]["bundle_count"], 0)
            self.assertEqual(index["payload"]["legacy_count"], 1)
            self.assertEqual(
                index["payload"]["entries"],
                [
                    {
                        "task_id": task_id,
                        "completion_cycle_id": cycle_id,
                        "cycle_ordinal": 1,
                        "bundle_state": "legacy_unknown",
                        "bundle_id": None,
                        "bundle_file": None,
                        "bundle_digest": None,
                        "file_digest": None,
                        "sealed_at": None,
                        "bundle_format_version": None,
                    }
                ],
            )
            bundles = install.fixed_root / "evidence" / "bundles"
            self.assertEqual(list(bundles.glob("*.json")), [])
            for forbidden in (
                receipt_id,
                "schema-v17-reviewer",
                "review_receipt",
                "review_provenance",
            ):
                self.assertNotIn(forbidden.encode("utf-8"), index_bytes)

            consumer_db_before = database_logical_digest(install.db_path)
            consumer_tree_before = tree_snapshot(install.skill_root / "state")
            report = read_evidence_report(
                install.fixed_root / "evidence",
                expected_project_id=project_id,
            )
            consumer_db_after = database_logical_digest(install.db_path)
            consumer_tree_after = tree_snapshot(install.skill_root / "state")
            self.assertEqual((report["bundle_count"], report["legacy_count"]), (0, 1))
            self.assertEqual(
                report["code_occurrences"],
                {
                    "review_profiles": {},
                    "review_lenses": {},
                    "method_codes": {},
                },
            )
            self.assertEqual(
                report["entries"],
                [
                    {
                        "task_id": task_id,
                        "completion_cycle_id": cycle_id,
                        "cycle_ordinal": 1,
                        "bundle_state": "legacy_unknown",
                    }
                ],
            )
            self.assertEqual(
                set(report["entries"][0]),
                {
                    "task_id",
                    "completion_cycle_id",
                    "cycle_ordinal",
                    "bundle_state",
                },
            )
            self.assertEqual(consumer_db_after, consumer_db_before)
            self.assertEqual(consumer_tree_after, consumer_tree_before)

            self.assertEqual(tree_snapshot(oracle_state), oracle_snapshot)
            self.assertEqual(file_digest(oracle_state / "current" / "taskgov.sqlite"), source_db_hash)


if __name__ == "__main__":
    unittest.main()
