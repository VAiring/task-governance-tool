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
    OldFixedPhysicalInstall,
    PhysicalInstall,
    extract_skill_at_commit,
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
from tests.test_m19_legacy_upgrade_rehearsal import (
    seed_supported_local_configs,
    supported_local_config_snapshot,
)


SCHEMA_V17_COMMIT = "92ab0060f3e7fa08f929cd02b3475f15c539cb0d"
SCHEMA_V17_PACKAGE_TREE = "44c50fa7596bd544c4aaf3876b937b11cde4d470"
TARGET_FINGERPRINT = "sha256:" + ("7" * 64)
LAYOUT_WRITES = [
    "state_layout_retire", "state_layout_publish", "state_layout_activate",
]
QUALIFIED_OLD_EXECUTABLES = (
    ("a9b80ce177a6dead10d51a070b76ff01f7af0294", "0.10.0"),
    ("c997fb65d58c598dac20f430498edf58b612fe32", "0.13.0"),
)


def replace_probe_core(
    install: PhysicalInstall,
    replacement: Path,
    *,
    retired_core: Path,
    fixture_root: Path,
) -> None:
    """Exchange only temp-fixture core; retain every old core and all state/config."""

    root = fixture_root.resolve(strict=True)
    skill = install.skill_root.resolve(strict=True)
    replacement = replacement.resolve(strict=True)
    retired_core = retired_core.resolve(strict=False)
    for selected in (skill, replacement, retired_core):
        selected.relative_to(root)
    if (
        skill != install.project_root.resolve() / ".agents" / "skills" / "task-governance-tool"
        or replacement == skill
        or retired_core.exists()
        or any((replacement / name).exists() for name in ("state", "config"))
    ):
        raise AssertionError("test core exchange scope is invalid")
    retired_core.mkdir()
    for child in tuple(skill.iterdir()):
        if child.name not in {"state", "config"}:
            child.rename(retired_core / child.name)
    for child in replacement.iterdir():
        destination = skill / child.name
        if child.is_dir():
            shutil.copytree(child, destination)
        else:
            shutil.copyfile(child, destination)


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
    def test_qualified_old_executables_refuse_real_setup_retirement_barrier(self):
        for commit, version in QUALIFIED_OLD_EXECUTABLES:
            for origin in ("fresh", "old_fixed"):
                with self.subTest(commit=commit, origin=origin), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    exact_package = extract_skill_at_commit(root / "exact-code", commit)
                    self.assertEqual(
                        require_repository_git("rev-parse", f"{commit}^{{commit}}")
                        .decode("ascii").strip(),
                        commit,
                    )
                    if origin == "old_fixed":
                        old = setup_exact_install(root / "install", commit)
                        source = require_cli_json(
                            old, "task", "add", "--title", "Preserved old-binary barrier source",
                            "--status", "in_progress", "--review-tier", "0",
                        )
                        source_task_id = source["data"]["task"]["task_id"]
                        source_project_id = source["project_id"]
                        config = seed_supported_local_configs(old.skill_root)
                        original_state = old.state_snapshot()
                        candidate = make_physical_install(root / "candidate")
                        replace_probe_core(
                            old, candidate.skill_root, retired_core=root / "original-core",
                            fixture_root=root,
                        )
                        self.assertEqual(old.state_snapshot(), original_state)
                        install = PhysicalInstall(old.project_root, old.skill_root)
                    else:
                        install = make_physical_install(root / "install")
                        config = seed_supported_local_configs(install.skill_root)
                        source_task_id = source_project_id = None

                    activated = require_cli_json(install, "setup")
                    self.assertEqual(activated["data"]["completed_writes"], LAYOUT_WRITES)
                    if source_project_id is not None:
                        self.assertEqual(activated["project_id"], source_project_id)
                        shown = require_cli_json(install, "task", "show", source_task_id)
                        self.assertEqual(shown["data"]["task"]["task_id"], source_task_id)
                    added = require_cli_json(
                        install, "task", "add", "--title", "Active new-state barrier sentinel",
                        "--status", "in_progress", "--review-tier", "0",
                    )
                    sentinel_id = added["data"]["task"]["task_id"]
                    old = OldFixedPhysicalInstall(install.project_root, install.skill_root)
                    marker_bytes = old.db_path.read_bytes()
                    marker = json.loads(marker_bytes)
                    self.assertEqual(marker["kind"], "taskgov-retired-state")
                    self.assertEqual(marker["project_id"], activated["project_id"])
                    state_before = install.state_snapshot()
                    self.assertEqual(supported_local_config_snapshot(install.skill_root), config)

                    # Deliberate negative old-executable probe, not a rollback:
                    # no old business DB is restored and new state stays active.
                    saved_current_core = root / "current-core"
                    replace_probe_core(
                        install, exact_package, retired_core=saved_current_core,
                        fixture_root=root,
                    )
                    old_version = old.run("--version")
                    self.assertEqual(old_version.returncode, 0)
                    self.assertEqual(old_version.stdout.strip(), f"taskgov {version}")
                    for arguments in (("task", "current"), ("setup",)):
                        refused = old.run(*arguments, "--repo", str(old.project_root), "--json")
                        self.assertEqual(refused.returncode, 2, refused.stdout or refused.stderr)
                        payload = json_payload(refused)
                        self.assertEqual(payload["errors"][0]["code"], "project_state_unreadable")
                        if arguments == ("setup",):
                            self.assertEqual(payload["data"]["completed_writes"], [])
                        self.assertEqual(old.db_path.read_bytes(), marker_bytes)
                        self.assertEqual(install.state_snapshot(), state_before)
                        self.assertEqual(supported_local_config_snapshot(install.skill_root), config)

                    replace_probe_core(
                        install, saved_current_core, retired_core=root / "probed-old-core",
                        fixture_root=root,
                    )
                    shown = require_cli_json(install, "task", "show", sentinel_id)
                    self.assertEqual(shown["data"]["task"]["task_id"], sentinel_id)
                    self.assertEqual(install.state_snapshot(), state_before)
                    self.assertEqual(supported_local_config_snapshot(install.skill_root), config)

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

            preview_tree = install.state_snapshot()
            preview_db_hash = file_digest(install.db_path)
            preview_logical_digest = database_logical_digest(install.db_path)
            preview = require_cli_json(install, "setup", "--read-only")

            self.assertEqual(preview["data"]["status"], "setup_preview")
            self.assertEqual(preview["data"]["schema_from"], 17)
            self.assertEqual(preview["data"]["planned_writes"], LAYOUT_WRITES)
            self.assertEqual(preview["data"]["completed_writes"], [])
            self.assertEqual(
                install.state_snapshot(),
                preview_tree,
            )
            self.assertEqual(file_digest(install.db_path), preview_db_hash)
            self.assertEqual(
                database_logical_digest(install.db_path),
                preview_logical_digest,
            )

            migrated = require_cli_json(install, "setup")
            self.assertEqual(migrated["data"]["schema_from"], 17)
            self.assertEqual(migrated["data"]["completed_writes"], LAYOUT_WRITES)
            # The exact old binary owns package-local paths until explicit
            # activation. Never infer the active layout from file existence.
            old_install = install
            install = PhysicalInstall(old_install.project_root, old_install.skill_root)
            self.assertNotEqual(install.db_path, old_install.db_path)
            marker = json.loads(old_install.db_path.read_bytes())
            self.assertEqual(marker["kind"], "taskgov-retired-state")
            self.assertEqual(marker["project_id"], project_id)
            retained = (
                install.project_root / ".taskgov" / ".state-separation"
                / "source" / "taskgov.sqlite"
            )
            self.assertEqual(database_logical_digest(retained), source_logical_digest)
            with closing(sqlite3.connect(install.db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0],
                    22,
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

            shown = require_cli_json(install, "task", "show", task_id, "--audit")
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
            self.assertEqual(index["payload"]["source_schema_version"], 22)
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
            consumer_tree_before = install.state_snapshot()
            report = read_evidence_report(
                install.fixed_root / "evidence",
                expected_project_id=project_id,
            )
            consumer_db_after = database_logical_digest(install.db_path)
            consumer_tree_after = install.state_snapshot()
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
