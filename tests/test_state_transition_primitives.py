import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.state_paths import (  # noqa: E402
    StatePathError,
    copy_physical_file_exclusive,
    create_exclusive_durable_file,
    hash_physical_file,
    inspect_physical_directory,
    rename_no_replace,
)
from task_governance_tool.state_transition import (  # noqa: E402
    CleanupInventoryEntry,
    StageOwner,
    StateTransitionError,
    build_cleanup_inventory,
    cleanup_roots,
    create_owned_stage,
    decode_stage_owner,
    encode_stage_owner,
    inspect_legacy_cleanup,
    inspect_stage_residue,
    parse_cleanup_inventory,
    remove_stage_residue,
    retire_legacy_inventory,
)


PROJECT_ID = "project-0123456789ab"
FINGERPRINT = "a" * 64
BACKUP_NAME = (
    "taskgov-backup-v1_20260729T120000Z_"
    "0123456789abcdef0123456789abcdef_r3.sqlite"
)


def make_state_root(base: Path) -> Path:
    state = base / "state"
    state.mkdir()
    return state


def inventory_entry(root: Path, relative_name: str) -> CleanupInventoryEntry:
    path = root.joinpath(*relative_name.split("/"))
    payload = path.read_bytes()
    return CleanupInventoryEntry(
        name=relative_name,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class StatePathPrimitiveTests(unittest.TestCase):
    def test_exclusive_durable_create_and_opaque_copy_are_no_clobber(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(Path(tmp))
            source = state / "source.bin"
            source.write_bytes(b"opaque-source")
            validated = hash_physical_file(
                source,
                root=state,
                max_bytes=64,
            )
            destination_root = state / "private"
            destination_root.mkdir()
            copied = copy_physical_file_exclusive(
                validated,
                destination_root / "copy.bin",
                source_root=state,
                destination_root=destination_root,
                max_bytes=64,
            )
            self.assertEqual(copied.sha256, validated.sha256)
            self.assertEqual(copied.path.read_bytes(), source.read_bytes())

            marker = create_exclusive_durable_file(
                state / "marker",
                b"marker",
                root=state,
                max_bytes=16,
            )
            self.assertEqual(marker.path.read_bytes(), b"marker")
            with self.assertRaises(StatePathError):
                create_exclusive_durable_file(
                    state / "marker",
                    b"replacement",
                    root=state,
                    max_bytes=16,
                )
            self.assertEqual(marker.path.read_bytes(), b"marker")

    @unittest.skipUnless(os.name == "nt", "Windows is the verified no-replace runtime")
    def test_no_replace_rename_preserves_both_entries_on_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(Path(tmp))
            source = state / "source"
            destination = state / "destination"
            source.write_bytes(b"source")
            destination.write_bytes(b"destination")
            validated = hash_physical_file(source, root=state)
            with self.assertRaises(StatePathError):
                rename_no_replace(validated, destination, root=state)
            self.assertEqual(source.read_bytes(), b"source")
            self.assertEqual(destination.read_bytes(), b"destination")


class StageOwnerAndResidueTests(unittest.TestCase):
    def test_owner_codec_is_exact_canonical_ascii(self):
        owner = StageOwner(
            stage_id="1" * 32,
            project_id=PROJECT_ID,
            inventory_fingerprint=FINGERPRINT,
        )
        encoded = encode_stage_owner(owner)
        self.assertEqual(decode_stage_owner(encoded), owner)
        self.assertEqual(
            encoded,
            json.dumps(
                {
                    "inventory_fingerprint": FINGERPRINT,
                    "project_id": PROJECT_ID,
                    "stage_id": "1" * 32,
                    "v": 1,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii"),
        )
        duplicate = (
            b'{"inventory_fingerprint":"'
            + FINGERPRINT.encode("ascii")
            + b'","project_id":"'
            + PROJECT_ID.encode("ascii")
            + b'","stage_id":"'
            + b"1" * 32
            + b'","v":1,"v":1}'
        )
        with self.assertRaises(StateTransitionError):
            decode_stage_owner(duplicate)

    def test_owned_stage_residue_is_bounded_and_explicitly_removable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(Path(tmp))
            owned = create_owned_stage(
                state,
                project_id=PROJECT_ID,
                inventory_fingerprint=FINGERPRINT,
                stage_id="2" * 32,
            )
            self.assertIsNotNone(owned.stage_directory)
            stage = owned.stage_directory.path
            (stage / "taskgov.sqlite").write_bytes(b"database")
            backups = stage / "backups"
            viewer = stage / "viewer"
            backups.mkdir()
            viewer.mkdir()
            (backups / BACKUP_NAME).write_bytes(b"backup")
            (backups / "taskgov-backup.lock").write_bytes(b"\0")
            (viewer / "task-viewer.html").write_bytes(b"viewer")
            (viewer / "taskgov-viewer.lock").write_bytes(b"\0")

            residue = inspect_stage_residue(
                state,
                max_file_bytes=1024,
                expected_project_id=PROJECT_ID,
                expected_inventory_fingerprint=FINGERPRINT,
            )
            self.assertIsNotNone(residue)
            self.assertEqual(len(residue.files), 5)
            remove_stage_residue(state, residue)
            self.assertFalse(owned.owner_file.path.exists())
            self.assertFalse(stage.exists())

    def test_orphan_owner_is_recoverable_but_stage_without_owner_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(Path(tmp))
            owned = create_owned_stage(
                state,
                project_id=PROJECT_ID,
                inventory_fingerprint=FINGERPRINT,
                stage_id="3" * 32,
            )
            owned.stage_directory.path.rmdir()
            residue = inspect_stage_residue(state, max_file_bytes=1)
            self.assertIsNotNone(residue)
            self.assertIsNone(residue.stage_directory)
            remove_stage_residue(state, residue)
            self.assertFalse(owned.owner_file.path.exists())

            unowned = state / f".current-stage-{'4' * 32}"
            unowned.mkdir()
            with self.assertRaises(StateTransitionError) as failure:
                inspect_stage_residue(state, max_file_bytes=1)
            self.assertEqual(str(failure.exception), "setup completed only partially; rerun setup")
            self.assertNotIn(str(state), str(failure.exception))
            self.assertTrue(unowned.exists())

    def test_unknown_or_oversized_stage_entry_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(Path(tmp))
            owned = create_owned_stage(
                state,
                project_id=PROJECT_ID,
                inventory_fingerprint=FINGERPRINT,
                stage_id="5" * 32,
            )
            unknown = owned.stage_directory.path / "private.txt"
            unknown.write_bytes(b"do-not-delete")
            with self.assertRaises(StateTransitionError):
                inspect_stage_residue(state, max_file_bytes=1024)
            self.assertEqual(unknown.read_bytes(), b"do-not-delete")


class PersistedCleanupPrimitiveTests(unittest.TestCase):
    def _make_legacy_files(
        self,
        state: Path,
        *,
        unrelated: bool = False,
    ) -> tuple[Path, object]:
        old, _ = cleanup_roots(state, PROJECT_ID)
        (old / "backups").mkdir(parents=True)
        (old / "viewer").mkdir()
        (old / "taskgov.sqlite").write_bytes(b"database")
        (old / "backups" / BACKUP_NAME).write_bytes(b"backup")
        (old / "viewer" / "task-viewer.html").write_bytes(b"viewer")
        if unrelated:
            (old / "notes.txt").write_bytes(b"keep")
        inventory = build_cleanup_inventory(
            (
                inventory_entry(old, "taskgov.sqlite"),
                inventory_entry(old, f"backups/{BACKUP_NAME}"),
                inventory_entry(old, "viewer/task-viewer.html"),
            )
        )
        return old, inventory

    def test_cleanup_inventory_builder_sorts_and_revalidates_exact_bytes(self):
        entries = (
            CleanupInventoryEntry(
                name="viewer/task-viewer.html",
                size=6,
                sha256=hashlib.sha256(b"viewer").hexdigest(),
            ),
            CleanupInventoryEntry(
                name="taskgov.sqlite",
                size=8,
                sha256=hashlib.sha256(b"database").hexdigest(),
            ),
        )
        inventory = build_cleanup_inventory(entries)
        self.assertEqual(
            [entry.name for entry in inventory.entries],
            ["taskgov.sqlite", "viewer/task-viewer.html"],
        )
        self.assertEqual(
            parse_cleanup_inventory(
                inventory.text,
                inventory.fingerprint,
            ),
            inventory,
        )
        with self.assertRaises(StateTransitionError):
            parse_cleanup_inventory(
                inventory.text + " ",
                inventory.fingerprint,
            )

    @unittest.skipUnless(os.name == "nt", "Windows is the verified no-replace runtime")
    def test_cleanup_moves_then_deletes_recorded_files_and_preserves_unrelated(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(Path(tmp))
            old, inventory = self._make_legacy_files(state, unrelated=True)
            result = retire_legacy_inventory(
                state,
                project_id=PROJECT_ID,
                inventory_text=inventory.text,
                inventory_fingerprint=inventory.fingerprint,
            )
            self.assertTrue(result.filesystem_complete)
            self.assertEqual((result.moved, result.deleted), (3, 3))
            self.assertEqual((old / "notes.txt").read_bytes(), b"keep")
            self.assertFalse((old / "taskgov.sqlite").exists())
            _, retirement = cleanup_roots(state, PROJECT_ID)
            self.assertFalse(retirement.exists())

    @unittest.skipUnless(os.name == "nt", "Windows is the verified no-replace runtime")
    def test_mixed_old_and_retirement_state_resumes_from_persisted_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state_root(Path(tmp))
            old, inventory = self._make_legacy_files(state)
            _, retirement = cleanup_roots(state, PROJECT_ID)
            (retirement / "backups").mkdir(parents=True)
            os.rename(
                old / "backups" / BACKUP_NAME,
                retirement / "backups" / BACKUP_NAME,
            )
            before = inspect_legacy_cleanup(
                state,
                project_id=PROJECT_ID,
                inventory_text=inventory.text,
                inventory_fingerprint=inventory.fingerprint,
            )
            self.assertEqual(before.old_files_remaining, 2)
            self.assertEqual(before.retirement_files_remaining, 1)
            result = retire_legacy_inventory(
                state,
                project_id=PROJECT_ID,
                inventory_text=inventory.text,
                inventory_fingerprint=inventory.fingerprint,
            )
            self.assertTrue(result.filesystem_complete)
            self.assertFalse(old.exists())
            self.assertFalse(retirement.exists())

    def test_collision_changed_content_and_unrecorded_retirement_peer_fail_closed(self):
        scenarios = ("collision", "changed", "unrecorded")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                state = make_state_root(Path(tmp))
                old, inventory = self._make_legacy_files(state)
                _, retirement = cleanup_roots(state, PROJECT_ID)
                if scenario == "collision":
                    retirement.mkdir()
                    (retirement / "taskgov.sqlite").write_bytes(b"database")
                elif scenario == "changed":
                    (old / "taskgov.sqlite").write_bytes(b"changed")
                else:
                    retirement.mkdir()
                    (retirement / "surprise.txt").write_bytes(b"preserve")
                with self.assertRaises(StateTransitionError) as failure:
                    inspect_legacy_cleanup(
                        state,
                        project_id=PROJECT_ID,
                        inventory_text=inventory.text,
                        inventory_fingerprint=inventory.fingerprint,
                    )
                self.assertNotIn(str(state), str(failure.exception))
                self.assertTrue(old.exists())
                if scenario != "changed":
                    self.assertTrue(retirement.exists())


if __name__ == "__main__":
    unittest.main()
