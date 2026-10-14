"""Isolated physical operational stores: immutable locations and atomic pointer."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from unittest import mock

from tests.test_review_continuation import basis, ident
from task_governance_tool.review_preparation_repository import PreparationRecord, PreparationRepository, _encode
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError


def record(index=1, **changes):
    data = dict(version=1, preparation_id=ident("tg_review_preparation_", index), basis=basis(), route="manual",
        packet_path=f"reviews/g{index}/packet.json", packet_digest="a" * 64,
        result_paths=(f"reviews/g{index}/a.json", f"reviews/g{index}/b.json"), finalization_store=None,
        created_at="2026-10-14T00:00:00Z")
    data.update(changes)
    return PreparationRecord(**data)


class PreparationRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-preparation-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve() / "preparation.sqlite"
        self.repo = PreparationRepository(self.path)

    def initialize(self):
        with self.repo.serial(initial=record()):
            pass

    def test_read_is_write_free_and_publish_retains_every_prior_location(self):
        self.initialize()
        before = self.path.read_bytes()
        rows, current = self.repo.read()
        self.assertEqual((rows, current), ((record(),), record()))
        self.assertEqual(self.path.read_bytes(), before)
        with self.repo.serial() as lease:
            self.repo.publish(lease, record(2))
        rows, current = self.repo.read()
        self.assertEqual(rows, (record(), record(2)))
        self.assertEqual(current, record(2))
        with self.repo.serial() as lease:
            self.assertEqual(self.repo.publish(lease, record(2)), record(2))
            with self.assertRaises(RepositoryError):
                self.repo.publish(lease, record())
        self.assertEqual(self.repo.read()[1], record(2))

    def test_changed_root_or_conflicting_identity_never_advances_pointer(self):
        self.initialize()
        with self.repo.serial() as lease:
            for changed in (record(2, basis=basis(target_generation=2)), record(packet_digest="b" * 64)):
                with self.subTest(changed=changed), self.assertRaises(RepositoryError):
                    self.repo.publish(lease, changed)
        self.assertEqual(self.repo.read(), ((record(),), record()))

    def test_publication_failure_rolls_back_row_and_pointer(self):
        self.initialize()
        load = self.repo._load
        observations = []
        def fail_after_publication(connection):
            result = load(connection)
            observations.append(result)
            if len(result[0]) == 2:
                raise RepositoryError("state_unreadable")
            return result
        with self.repo.serial() as lease:
            with mock.patch.object(self.repo, "_load", side_effect=fail_after_publication):
                with self.assertRaises(RepositoryError):
                    self.repo.publish(lease, record(2))
        self.assertEqual(len(observations[-1][0]), 2)
        self.assertEqual(self.repo.read(), ((record(),), record()))

    def test_dangling_pointer_unknown_schema_and_noncanonical_record_reject_read(self):
        self.initialize()
        before = self.path.read_bytes()
        mutations = ("UPDATE current SET preparation_id='missing'", "PRAGMA user_version=2",
                     "DROP TRIGGER preparations_no_update")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.path.write_bytes(before)
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute(mutation)
                changed = self.path.read_bytes()
                with self.assertRaises(RepositoryError):
                    self.repo.read()
                self.assertEqual(self.path.read_bytes(), changed)

    def test_sql_guards_keep_saved_records_immutable(self):
        self.initialize()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for statement in ("UPDATE preparations SET record_json='{}'", "DELETE FROM preparations"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)

    def test_failed_admission_never_publishes_first_or_replaces_prior_pointer(self):
        def reject():
            raise RepositoryError("binding_mismatch")
        with self.assertRaises(RepositoryError):
            with self.repo.serial(initial=record(), admit=reject):
                self.fail("admission cannot yield")
        self.assertFalse(self.path.exists())
        # A separate physical store tests failure against existing ready state.
        self.repo = PreparationRepository(self.path.with_name("existing.sqlite"))
        with self.repo.serial(initial=record()):
            pass
        before = self.repo.path.read_bytes()
        with self.assertRaises(RepositoryError):
            with self.repo.serial(admit=reject) as lease:
                self.repo.publish(lease, record(2))
        self.assertEqual(self.repo.path.read_bytes(), before)

    def test_integrated_identity_is_exact_and_cannot_promote_manual_route(self):
        value = basis()
        digest = sha256((value["parent_session_id"] + ":" + value["task_id"]).encode()).hexdigest()
        expected = f"finalize-{digest}-1.sqlite"
        self.assertEqual(record(route="integrated", finalization_store=expected).finalization_store, expected)
        for changes in (dict(finalization_store=expected), dict(route="integrated"),
                        dict(route="integrated", finalization_store=expected.replace("-1.sqlite", "-2.sqlite"))):
            with self.subTest(changes=changes), self.assertRaises(RepositoryError):
                record(**changes)

    def test_closed_record_paths_encoding_and_size(self):
        for changes in (dict(version=True), dict(result_paths=("reviews/g1/packet.json",)),
                        dict(packet_path=".taskgov/packet.json"), dict(packet_path="reviews/../packet.json"),
                        dict(created_at="later"), dict(packet_digest="sha256:" + "a" * 64)):
            with self.subTest(changes=changes), self.assertRaises(RepositoryError):
                record(**changes)
        value = record(packet_path="reviews/日本語.json")
        self.assertEqual(json.loads(_encode(value))["packet_path"], "reviews/日本語.json")
        with self.assertRaises(RepositoryError):
            _encode(record(result_paths=tuple("reviews/" + "日" * 1300 + str(i) + ".json" for i in range(8))))


if __name__ == "__main__":
    unittest.main()
