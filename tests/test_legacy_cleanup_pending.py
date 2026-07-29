from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.m14_test_support import SOURCE_SCRIPTS_ROOT

if str(SOURCE_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_SCRIPTS_ROOT))

from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    StorageError,
    clear_legacy_cleanup_pending,
    connect_readonly,
    initialize_database,
    project_identity,
    read_project_binding_state,
    set_legacy_cleanup_pending,
)


def canonical_inventory(
    *,
    name: str = "taskgov.sqlite",
    content: bytes = b"legacy state",
) -> tuple[str, str]:
    payload = {
        "entries": [
            {
                "kind": "file",
                "name": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        ],
        "v": 1,
    }
    inventory = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        inventory,
        hashlib.sha256(inventory.encode("ascii")).hexdigest(),
    )


def make_target(root: Path, *, project_name: str = "project") -> DatabaseTarget:
    repo = root / project_name
    repo.mkdir(parents=True)
    target = DatabaseTarget(
        project=project_identity(repo),
        db_path=root / "state" / "current" / "taskgov.sqlite",
        explicit_db=True,
    )
    initialize_database(target)
    return target


def binding_state(target: DatabaseTarget):
    with closing(connect_readonly(target.db_path)) as connection:
        return read_project_binding_state(
            connection,
            expected_project_id=target.project.project_id,
        )


def database_snapshot(target: DatabaseTarget) -> dict[str, tuple[int, str]]:
    root = target.db_path.parent
    if not root.exists():
        return {}
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        data = path.read_bytes()
        result[path.relative_to(root).as_posix()] = (
            len(data),
            hashlib.sha256(data).hexdigest(),
        )
    return result


def binding_repository_projection(target: DatabaseTarget) -> tuple:
    with closing(sqlite3.connect(target.db_path)) as connection:
        connection.row_factory = sqlite3.Row
        current = connection.execute(
            """
            SELECT project_id, identity_scheme, binding_generation,
                   canonical_path_hash, display_name, binding_reason,
                   binding_updated_at, legacy_cleanup_pending,
                   legacy_cleanup_inventory, legacy_cleanup_fingerprint
              FROM project_meta
            """
        ).fetchone()
        history = connection.execute(
            """
            SELECT project_id, binding_generation, previous_path_hash,
                   canonical_path_hash, display_name, reason,
                   confirmation_token_digest, bound_at
              FROM project_path_binding_history
             ORDER BY binding_generation
            """
        ).fetchall()
        viewer = connection.execute(
            """
            SELECT project_id, source_generation
              FROM viewer_maintenance_state
            """
        ).fetchall()
    return (
        tuple(current) if current is not None else None,
        tuple(tuple(row) for row in history),
        tuple(tuple(row) for row in viewer),
    )


class LegacyCleanupPendingTests(unittest.TestCase):
    def test_set_succeeds_on_exact_basis_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = make_target(Path(temporary))
            current = binding_state(target)
            inventory, fingerprint = canonical_inventory()
            before = binding_repository_projection(target)

            updated = set_legacy_cleanup_pending(
                target,
                project_id=current.project_id,
                expected_identity_scheme=current.identity_scheme,
                expected_generation=current.binding_generation,
                expected_path_hash=current.canonical_path_hash,
                inventory=inventory,
                fingerprint=fingerprint,
            )

            self.assertTrue(updated.legacy_cleanup_pending)
            self.assertEqual(updated.legacy_cleanup_inventory, inventory)
            self.assertEqual(updated.legacy_cleanup_fingerprint, fingerprint)
            after = binding_repository_projection(target)
            self.assertEqual(before[0][:7], after[0][:7])
            self.assertEqual(before[1:], after[1:])
            self.assertEqual(after[0][7:], (1, inventory, fingerprint))

            persisted = database_snapshot(target)
            repeated = set_legacy_cleanup_pending(
                target,
                project_id=current.project_id,
                expected_identity_scheme=current.identity_scheme,
                expected_generation=current.binding_generation,
                expected_path_hash=current.canonical_path_hash,
                inventory=inventory,
                fingerprint=fingerprint,
            )
            self.assertEqual(repeated, updated)
            self.assertEqual(database_snapshot(target), persisted)

    def test_set_rejects_stale_binding_and_foreign_project_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = make_target(root)
            current = binding_state(target)
            inventory, fingerprint = canonical_inventory()
            foreign = project_identity(root / "foreign")
            cases = (
                (
                    "generation",
                    {
                        "project_id": current.project_id,
                        "expected_identity_scheme": current.identity_scheme,
                        "expected_generation": current.binding_generation + 1,
                        "expected_path_hash": current.canonical_path_hash,
                    },
                    "project_binding_stale",
                ),
                (
                    "hash",
                    {
                        "project_id": current.project_id,
                        "expected_identity_scheme": current.identity_scheme,
                        "expected_generation": current.binding_generation,
                        "expected_path_hash": "f" * 64,
                    },
                    "project_binding_stale",
                ),
                (
                    "foreign project",
                    {
                        "project_id": foreign.project_id,
                        "expected_identity_scheme": "legacy_path_v1",
                        "expected_generation": 1,
                        "expected_path_hash": foreign.canonical_path_hash,
                    },
                    "internal_error",
                ),
                (
                    "identity scheme",
                    {
                        "project_id": current.project_id,
                        "expected_identity_scheme": "uuid_v1",
                        "expected_generation": current.binding_generation,
                        "expected_path_hash": current.canonical_path_hash,
                    },
                    "internal_error",
                ),
            )
            for label, basis, expected_code in cases:
                with self.subTest(label=label):
                    before = database_snapshot(target)
                    with self.assertRaises(StorageError) as raised:
                        set_legacy_cleanup_pending(
                            target,
                            **basis,
                            inventory=inventory,
                            fingerprint=fingerprint,
                        )
                    self.assertEqual(raised.exception.code, expected_code)
                    self.assertEqual(database_snapshot(target), before)
                    self.assertFalse(binding_state(target).legacy_cleanup_pending)

    def test_set_rejects_invalid_inventory_or_fingerprint_before_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = make_target(Path(temporary))
            current = binding_state(target)
            inventory, fingerprint = canonical_inventory()
            duplicated_payload = json.loads(inventory)
            duplicated_payload["entries"].append(
                dict(duplicated_payload["entries"][0])
            )
            duplicated = json.dumps(
                duplicated_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            invalid_cases = (
                ("non-canonical JSON", "{}", hashlib.sha256(b"{}").hexdigest()),
                ("wrong fingerprint", inventory, "f" * 64),
                (
                    "duplicate entry",
                    duplicated,
                    hashlib.sha256(duplicated.encode("ascii")).hexdigest(),
                ),
            )
            for label, invalid_inventory, invalid_fingerprint in invalid_cases:
                with self.subTest(label=label):
                    before = database_snapshot(target)
                    with self.assertRaises(StorageError) as raised:
                        set_legacy_cleanup_pending(
                            target,
                            project_id=current.project_id,
                            expected_identity_scheme=current.identity_scheme,
                            expected_generation=current.binding_generation,
                            expected_path_hash=current.canonical_path_hash,
                            inventory=invalid_inventory,
                            fingerprint=invalid_fingerprint,
                        )
                    self.assertEqual(raised.exception.code, "internal_error")
                    self.assertEqual(database_snapshot(target), before)

    def test_set_rejects_a_different_plan_when_pending_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = make_target(Path(temporary))
            current = binding_state(target)
            inventory, fingerprint = canonical_inventory()
            set_legacy_cleanup_pending(
                target,
                project_id=current.project_id,
                expected_identity_scheme=current.identity_scheme,
                expected_generation=current.binding_generation,
                expected_path_hash=current.canonical_path_hash,
                inventory=inventory,
                fingerprint=fingerprint,
            )
            different_inventory, different_fingerprint = canonical_inventory(
                name="viewer/task-viewer.html",
                content=b"viewer",
            )
            before = database_snapshot(target)

            with self.assertRaises(StorageError) as raised:
                set_legacy_cleanup_pending(
                    target,
                    project_id=current.project_id,
                    expected_identity_scheme=current.identity_scheme,
                    expected_generation=current.binding_generation,
                    expected_path_hash=current.canonical_path_hash,
                    inventory=different_inventory,
                    fingerprint=different_fingerprint,
                )

            self.assertEqual(raised.exception.code, "project_binding_stale")
            self.assertEqual(database_snapshot(target), before)
            self.assertEqual(
                binding_state(target).legacy_cleanup_fingerprint,
                fingerprint,
            )

    def test_clear_requires_exact_pending_basis_and_clears_only_cleanup_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = make_target(Path(temporary))
            current = binding_state(target)
            inventory, fingerprint = canonical_inventory()
            set_legacy_cleanup_pending(
                target,
                project_id=current.project_id,
                expected_identity_scheme=current.identity_scheme,
                expected_generation=current.binding_generation,
                expected_path_hash=current.canonical_path_hash,
                inventory=inventory,
                fingerprint=fingerprint,
            )
            pending_projection = binding_repository_projection(target)
            stale_cases = (
                (
                    "generation",
                    current.binding_generation + 1,
                    current.canonical_path_hash,
                    fingerprint,
                ),
                (
                    "hash",
                    current.binding_generation,
                    "f" * 64,
                    fingerprint,
                ),
                (
                    "fingerprint",
                    current.binding_generation,
                    current.canonical_path_hash,
                    "f" * 64,
                ),
            )
            for label, generation, path_hash, expected_fingerprint in stale_cases:
                with self.subTest(label=label):
                    before = database_snapshot(target)
                    with self.assertRaises(StorageError) as raised:
                        clear_legacy_cleanup_pending(
                            target,
                            project_id=current.project_id,
                            expected_identity_scheme=current.identity_scheme,
                            expected_generation=generation,
                            expected_path_hash=path_hash,
                            expected_inventory_fingerprint=expected_fingerprint,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "project_binding_stale",
                    )
                    self.assertEqual(database_snapshot(target), before)

            foreign = project_identity(Path(temporary) / "foreign")
            before_foreign = database_snapshot(target)
            with self.assertRaises(StorageError) as raised:
                clear_legacy_cleanup_pending(
                    target,
                    project_id=foreign.project_id,
                    expected_identity_scheme="legacy_path_v1",
                    expected_generation=1,
                    expected_path_hash=foreign.canonical_path_hash,
                    expected_inventory_fingerprint=fingerprint,
                )
            self.assertEqual(raised.exception.code, "internal_error")
            self.assertEqual(database_snapshot(target), before_foreign)

            cleared = clear_legacy_cleanup_pending(
                target,
                project_id=current.project_id,
                expected_identity_scheme=current.identity_scheme,
                expected_generation=current.binding_generation,
                expected_path_hash=current.canonical_path_hash,
                expected_inventory_fingerprint=fingerprint,
            )
            self.assertFalse(cleared.legacy_cleanup_pending)
            self.assertIsNone(cleared.legacy_cleanup_inventory)
            self.assertIsNone(cleared.legacy_cleanup_fingerprint)
            cleared_projection = binding_repository_projection(target)
            self.assertEqual(
                pending_projection[0][:7],
                cleared_projection[0][:7],
            )
            self.assertEqual(pending_projection[1:], cleared_projection[1:])

            before_repeat = database_snapshot(target)
            with self.assertRaises(StorageError) as raised:
                clear_legacy_cleanup_pending(
                    target,
                    project_id=current.project_id,
                    expected_identity_scheme=current.identity_scheme,
                    expected_generation=current.binding_generation,
                    expected_path_hash=current.canonical_path_hash,
                    expected_inventory_fingerprint=fingerprint,
                )
            self.assertEqual(raised.exception.code, "project_binding_stale")
            self.assertEqual(database_snapshot(target), before_repeat)

    def test_clear_rejects_corrupt_persisted_plan_without_write(self):
        cases = (
            ("inventory", "legacy_cleanup_inventory", "{}", None),
            ("fingerprint", "legacy_cleanup_fingerprint", "e" * 64, "e" * 64),
        )
        for label, column, value, expected_override in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    target = make_target(Path(temporary))
                    current = binding_state(target)
                    inventory, fingerprint = canonical_inventory()
                    set_legacy_cleanup_pending(
                        target,
                        project_id=current.project_id,
                        expected_identity_scheme=current.identity_scheme,
                        expected_generation=current.binding_generation,
                        expected_path_hash=current.canonical_path_hash,
                        inventory=inventory,
                        fingerprint=fingerprint,
                    )
                    with closing(sqlite3.connect(target.db_path)) as connection:
                        connection.execute(
                            f"""
                            UPDATE project_meta
                               SET {column} = ?
                             WHERE project_id = ?
                            """,
                            (value, current.project_id),
                        )
                        connection.commit()
                    before = database_snapshot(target)

                    with self.assertRaises(StorageError) as raised:
                        clear_legacy_cleanup_pending(
                            target,
                            project_id=current.project_id,
                            expected_identity_scheme=current.identity_scheme,
                            expected_generation=current.binding_generation,
                            expected_path_hash=current.canonical_path_hash,
                            expected_inventory_fingerprint=(
                                expected_override or fingerprint
                            ),
                        )

                    self.assertEqual(
                        raised.exception.code,
                        "project_state_unreadable",
                    )
                    self.assertEqual(database_snapshot(target), before)


if __name__ == "__main__":
    unittest.main()
