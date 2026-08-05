from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from tests.m14_test_support import json_payload, make_physical_install


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import state_resolver  # noqa: E402
from task_governance_tool.state_resolver import (  # noqa: E402
    canonical_state_paths,
    observe_current_root,
    resolve_project_state,
    resolve_setup_project_state,
    resolve_staged_project_state,
)
from task_governance_tool.state_transition import (  # noqa: E402
    CleanupInventoryEntry,
    StateTransitionError,
    build_cleanup_inventory,
    cleanup_roots,
    create_owned_stage,
    inspect_stage_residue,
    remove_stage_residue,
    retire_legacy_inventory,
    validate_publishable_stage,
)


PROJECT_ID = "project-0123456789ab"
FINGERPRINT = "a" * 64
BUNDLE_FILENAME = "tg_completion_evidence_bundle_0123456789abcdef.json"


def _database_observation(repo: Path, db_path: Path):
    current = observe_current_root(repo)
    details = db_path.lstat()
    return state_resolver._DatabaseObservation(
        stored_project=state_resolver.StoredProjectObservation(
            project_id=PROJECT_ID,
            identity_scheme="legacy_path_v1",
            binding_generation=1,
            canonical_path_hash=current.canonical_path_hash,
            display_name=current.display_name,
            source_schema_version=19,
            binding_lineage=(current.canonical_path_hash,),
        ),
        generation_rows=(),
        maintenance_pointer=None,
        stamp=state_resolver._FileStamp(
            device=int(details.st_dev),
            inode=int(details.st_ino),
            size=int(details.st_size),
            modified_ns=int(details.st_mtime_ns),
        ),
    )


def _inspect_database(repo: Path):
    def inspect(db_path: Path, **_kwargs):
        return _database_observation(repo, db_path)

    return inspect


def _inventory_entry(root: Path, relative_name: str) -> CleanupInventoryEntry:
    path = root.joinpath(*relative_name.split("/"))
    payload = path.read_bytes()
    return CleanupInventoryEntry(
        name=relative_name,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class EvidenceResolverBoundaryTests(unittest.TestCase):
    def test_public_setup_repairs_safe_oversized_index_only_in_setup_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary))
            initialized = install.run("setup", "--json")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)

            index_path = install.fixed_root / "evidence" / "index.json"
            with index_path.open("r+b") as stream:
                stream.truncate(state_resolver.EVIDENCE_INDEX_MAX_BYTES + 1)
            oversized = index_path.stat()

            setup_resolution = resolve_setup_project_state(
                skill_root=install.skill_root,
                repo=install.project_root,
            )
            self.assertIsNone(setup_resolution.error_code)
            self.assertIsNotNone(setup_resolution.target)

            preview = install.run("setup", "--read-only", "--json")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(
                json_payload(preview)["data"]["evidence_status"],
                "repair_required",
            )
            after_preview = index_path.stat()
            self.assertEqual(
                (after_preview.st_size, after_preview.st_mtime_ns),
                (oversized.st_size, oversized.st_mtime_ns),
            )

            repaired = install.run("setup", "--json")
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertEqual(
                json_payload(repaired)["data"]["evidence_status"],
                "published",
            )
            self.assertLessEqual(
                index_path.stat().st_size,
                state_resolver.EVIDENCE_INDEX_MAX_BYTES,
            )
            replay = install.run("setup", "--json")
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(
                json_payload(replay)["data"]["evidence_status"],
                "current",
            )

    def test_canonical_and_staged_targets_propagate_fixed_evidence_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_root = root / "skill"
            repo = root / "project"
            skill_root.mkdir()
            repo.mkdir()
            paths = canonical_state_paths(skill_root)
            paths.fixed_root.mkdir(parents=True)
            paths.database.write_bytes(b"database")

            with mock.patch.object(
                state_resolver,
                "_inspect_database",
                side_effect=_inspect_database(repo),
            ):
                fixed = resolve_setup_project_state(
                    skill_root=skill_root,
                    repo=repo,
                )

            expected_root = skill_root.resolve() / "state" / "current" / "evidence"
            self.assertEqual(paths.evidence_root, expected_root)
            self.assertEqual(paths.evidence_index, expected_root / "index.json")
            self.assertEqual(paths.evidence_bundles, expected_root / "bundles")
            self.assertEqual(
                paths.evidence_lock,
                expected_root / "taskgov-evidence.lock",
            )
            self.assertIsNone(fixed.error_code)
            self.assertIsNotNone(fixed.target)
            self.assertEqual(fixed.target.evidence_root, paths.evidence_root)
            self.assertEqual(fixed.target.evidence_index, paths.evidence_index)
            self.assertEqual(fixed.target.evidence_bundles, paths.evidence_bundles)
            self.assertEqual(fixed.target.evidence_lock, paths.evidence_lock)

            stage_root = paths.state_root / f".current-stage-{'1' * 32}"
            stage_root.mkdir()
            (stage_root / "taskgov.sqlite").write_bytes(b"database")
            with mock.patch.object(
                state_resolver,
                "_inspect_database",
                side_effect=_inspect_database(repo),
            ):
                staged = resolve_staged_project_state(
                    stage_root=stage_root,
                    repo=repo,
                )
            self.assertIsNone(staged.error_code)
            self.assertIsNotNone(staged.target)
            self.assertEqual(staged.paths.evidence_root, stage_root / "evidence")
            self.assertEqual(
                staged.target.evidence_index,
                stage_root / "evidence" / "index.json",
            )
            self.assertEqual(
                staged.target.evidence_bundles,
                stage_root / "evidence" / "bundles",
            )
            self.assertEqual(
                staged.target.evidence_lock,
                stage_root / "evidence" / "taskgov-evidence.lock",
            )

    def test_setup_resolution_rejects_unknown_reparse_and_database_alias(self):
        scenarios = ("unknown_name", "reparse_root", "database_alias")
        for scenario in scenarios:
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                skill_root = root / "skill"
                repo = root / "project"
                skill_root.mkdir()
                repo.mkdir()
                paths = canonical_state_paths(skill_root)
                paths.fixed_root.mkdir(parents=True)
                paths.database.write_bytes(b"database")
                paths.evidence_root.mkdir()

                patcher = None
                if scenario == "unknown_name":
                    (paths.evidence_root / "Index.json").write_bytes(b"{}\n")
                elif scenario == "database_alias":
                    os.link(paths.database, paths.evidence_index)
                else:
                    identity = paths.evidence_root.lstat()
                    real_is_reparse = state_resolver._is_reparse

                    def is_reparse(details: os.stat_result) -> bool:
                        return (
                            int(details.st_dev),
                            int(details.st_ino),
                        ) == (
                            int(identity.st_dev),
                            int(identity.st_ino),
                        ) or real_is_reparse(details)

                    patcher = mock.patch.object(
                        state_resolver,
                        "_is_reparse",
                        side_effect=is_reparse,
                    )

                with ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.object(
                            state_resolver,
                            "_inspect_database",
                            side_effect=_inspect_database(repo),
                        )
                    )
                    ordinary = resolve_project_state(
                        skill_root=skill_root,
                        repo=repo,
                    )
                    self.assertIsNone(ordinary.error_code)
                    if patcher is not None:
                        stack.enter_context(patcher)
                    setup = resolve_setup_project_state(
                        skill_root=skill_root,
                        repo=repo,
                    )
                self.assertEqual(setup.error_code, "project_state_unreadable")

    def test_evidence_inspection_rejects_a_containment_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner = root / "owner"
            outside = root / "outside" / "evidence"
            owner.mkdir()
            outside.mkdir(parents=True)
            (outside / "index.json").write_bytes(b"{}\n")

            with self.assertRaises(state_resolver._ResolverFailure):
                state_resolver._inspect_evidence_directory(
                    outside,
                    owner_root=owner,
                    database_stamps=(),
                )


class EvidenceStageBoundaryTests(unittest.TestCase):
    def _owned_stage(self, root: Path, stage_id: str):
        state_root = root / "state"
        state_root.mkdir()
        owned = create_owned_stage(
            state_root,
            project_id=PROJECT_ID,
            inventory_fingerprint=FINGERPRINT,
            stage_id=stage_id,
        )
        self.assertIsNotNone(owned.stage_directory)
        stage = owned.stage_directory.path
        (stage / "taskgov.sqlite").write_bytes(b"database")
        viewer = stage / "viewer"
        viewer.mkdir()
        (viewer / "task-viewer.html").write_bytes(b"viewer")
        evidence = stage / "evidence"
        bundles = evidence / "bundles"
        bundles.mkdir(parents=True)
        return state_root, owned, evidence, bundles

    def test_stage_accepts_only_the_closed_evidence_tree_and_cleans_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_root, owned, evidence, bundles = self._owned_stage(
                Path(temporary),
                "2" * 32,
            )
            (evidence / "index.json").write_bytes(b"{}\n")
            (evidence / "taskgov-evidence.lock").write_bytes(b"\0")
            # This bundle is a grammar canary. Legacy publication itself is
            # index-only and therefore retains the existing 32-file stage cap.
            (bundles / BUNDLE_FILENAME).write_bytes(b"{}\n")
            index_temp = evidence / ".taskgov-evidence-index-aaaaaaaa.tmp"
            bundle_temp = bundles / ".taskgov-evidence-bundle-bbbbbbbb.tmp"
            index_temp.write_bytes(b"index")
            bundle_temp.write_bytes(b"bundle")

            residue = inspect_stage_residue(state_root, max_file_bytes=1024)
            self.assertIsNotNone(residue)
            with self.assertRaises(StateTransitionError):
                validate_publishable_stage(residue)

            index_temp.unlink()
            bundle_temp.unlink()
            residue = inspect_stage_residue(state_root, max_file_bytes=1024)
            names = validate_publishable_stage(residue)
            self.assertIn("evidence/index.json", names)
            self.assertIn(f"evidence/bundles/{BUNDLE_FILENAME}", names)
            remove_stage_residue(state_root, residue)
            self.assertFalse(owned.owner_file.path.exists())
            self.assertFalse(owned.stage_directory.path.exists())

    def test_stage_rejects_unknown_evidence_name_and_requires_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_root, _, evidence, bundles = self._owned_stage(
                Path(temporary),
                "3" * 32,
            )
            unknown = bundles / "tg_completion_evidence_bundle_ABCDEF0123456789.json"
            unknown.write_bytes(b"preserve")
            with self.assertRaises(StateTransitionError):
                inspect_stage_residue(state_root, max_file_bytes=1024)
            self.assertEqual(unknown.read_bytes(), b"preserve")

        with tempfile.TemporaryDirectory() as temporary:
            state_root, _, _, _ = self._owned_stage(
                Path(temporary),
                "4" * 32,
            )
            residue = inspect_stage_residue(state_root, max_file_bytes=1024)
            with self.assertRaises(StateTransitionError):
                validate_publishable_stage(residue)

    @unittest.skipUnless(os.name == "nt", "Windows is the verified no-replace runtime")
    def test_cleanup_inventory_retires_the_nested_evidence_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary) / "state"
            state_root.mkdir()
            old_root, retirement_root = cleanup_roots(state_root, PROJECT_ID)
            bundles = old_root / "evidence" / "bundles"
            bundles.mkdir(parents=True)
            (old_root / "evidence" / "index.json").write_bytes(b"index\n")
            (old_root / "evidence" / "taskgov-evidence.lock").write_bytes(b"")
            (bundles / BUNDLE_FILENAME).write_bytes(b"bundle\n")
            names = (
                "evidence/index.json",
                "evidence/taskgov-evidence.lock",
                f"evidence/bundles/{BUNDLE_FILENAME}",
            )
            inventory = build_cleanup_inventory(
                _inventory_entry(old_root, name) for name in names
            )

            result = retire_legacy_inventory(
                state_root,
                project_id=PROJECT_ID,
                inventory_text=inventory.text,
                inventory_fingerprint=inventory.fingerprint,
            )

            self.assertTrue(result.filesystem_complete)
            self.assertEqual((result.moved, result.deleted), (3, 3))
            self.assertFalse(old_root.exists())
            self.assertFalse(retirement_root.exists())


if __name__ == "__main__":
    unittest.main()
