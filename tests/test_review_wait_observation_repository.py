"""Short polling reads coexist with real isolated rollback-journal writers."""

from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_review_wait_repository import NOW, make_controller
from tools.review_wait_direct_repository import DirectRecord, DirectRepository, instant
from tools.review_wait_repository import ObservationBusy, RepositoryError, ReviewWaitRepository
from tools import review_wait_repository as repository_module


class ReviewWaitObservationRepositoryTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix=".tmp-wait-observation-", dir=Path(__file__).absolute().parents[1])))
        self.wait = ReviewWaitRepository.create(root / "wait.sqlite", make_controller())
        self.direct = DirectRepository.for_wait(self.wait.path)
        self.record = DirectRecord(
            probe_id="11111111-1111-4111-8111-111111111111",
            binding_digest="a" * 64,
            original_parent_turn="22222222-2222-4222-8222-222222222222",
            status="waiting",
            created_at=instant(NOW),
            deadline=instant(NOW + timedelta(minutes=10)),
        )
        self.direct.create_record(self.record)

    def capture_connections(self):
        connections = []
        original = repository_module.connect_traced

        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            connections.append(connection)
            return connection

        self.enterContext(patch.object(repository_module, "connect_traced", side_effect=connect))
        return connections

    def assert_closed(self, connections):
        self.assertTrue(connections)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def held_observation(self):
        """Hold a real query-only snapshot until the test releases its reader."""
        reader = DirectRepository(self.direct.path)
        acquired, release = threading.Event(), threading.Event()
        result, errors = [], []
        original = reader._load

        def hold(connection):
            record = original(connection)
            acquired.set()
            if not release.wait(5):
                raise AssertionError("observation release was not signalled")
            return record

        def observe():
            try:
                result.append(reader.read_observation())
            except BaseException as error:
                errors.append(error)

        patched = patch.object(reader, "_load", side_effect=hold)
        patched.start()
        worker = threading.Thread(target=observe)
        worker.start()

        def finish():
            release.set()
            worker.join(6)
            patched.stop()
            self.assertFalse(worker.is_alive())
            self.assertEqual([], errors)

        self.addCleanup(finish)
        self.assertTrue(acquired.wait(5))
        return release, worker, result

    def update_during_observation(self, **changes):
        release, reader, snapshots = self.held_observation()
        committing, finished = threading.Event(), threading.Event()
        statements, results, errors = [], [], []
        original = repository_module.connect_traced

        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            self.assertEqual("readwrite", kwargs["access"])

            def trace(sql):
                statements.append(sql)
                if sql == "COMMIT":
                    committing.set()

            connection.set_trace_callback(trace)
            return connection

        def update():
            try:
                results.append(self.direct.update(self.record.probe_id, **changes))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        with patch.object(repository_module, "connect_traced", side_effect=connect) as opened:
            writer = threading.Thread(target=update)
            writer.start()
            try:
                self.assertTrue(committing.wait(5))
                # The real COMMIT is pending while the other connection still
                # owns its snapshot; success after read() returned is not enough.
                self.assertFalse(finished.wait(0.05))
            finally:
                release.set()
                reader.join(6)
                writer.join(6)
            self.assertFalse(writer.is_alive())
            self.assertEqual([], errors)
            self.assertEqual(1, opened.call_count)
        self.assertEqual(1, statements.count("BEGIN IMMEDIATE"))
        self.assertEqual(1, statements.count("COMMIT"))
        self.assertEqual(1, sum(sql.startswith("UPDATE direct_probe ") for sql in statements))
        self.assertEqual(1, len(snapshots))
        self.assertEqual(1, len(results))
        self.assertEqual(results[0], self.direct.read())
        return snapshots[0], results[0]

    def test_direct_observation_does_not_acquire_writer_lease_or_mutate(self):
        before = self.direct.path.read_bytes()
        with self.direct._serial():
            with patch.object(self.direct, "_serial", side_effect=AssertionError("poll took writer lease")):
                self.assertEqual(self.record, self.direct.read_observation())
        self.assertEqual(before, self.direct.path.read_bytes())
        self.assertEqual(self.record, self.direct.read())

    def test_success_closes_connection_before_caller_can_write(self):
        connections = self.capture_connections()
        for repository in (self.wait, self.direct):
            repository.read_observation()
            self.assert_closed(connections)
            with closing(sqlite3.connect(repository.path, timeout=0)) as writer:
                writer.execute("BEGIN EXCLUSIVE")
                writer.rollback()

    def test_busy_is_deferred_only_for_observation_and_closes_its_connection(self):
        connections = self.capture_connections()
        for repository in (self.wait, self.direct):
            with self.subTest(repository=type(repository).__name__):
                with closing(sqlite3.connect(repository.path, timeout=0)) as writer:
                    writer.execute("BEGIN EXCLUSIVE")
                    started = time.monotonic()
                    with self.assertRaises(ObservationBusy) as observed:
                        repository.read_observation()
                    self.assertLess(time.monotonic() - started, 0.5)
                    self.assertEqual("observation_busy", observed.exception.code)
                    self.assert_closed(connections)
                    with self.assertRaises(RepositoryError) as ordinary:
                        repository.read()
                    self.assertNotIsInstance(ordinary.exception, ObservationBusy)
                    self.assertEqual("state_unreadable", ordinary.exception.code)
                    writer.rollback()
                repository.read_observation()
                self.assert_closed(connections)

    def test_only_native_busy_codes_are_deferable(self):
        for code, expected in (
            (sqlite3.SQLITE_BUSY, ObservationBusy),
            (sqlite3.SQLITE_BUSY | (3 << 8), ObservationBusy),
            (sqlite3.SQLITE_LOCKED, RepositoryError),
            (sqlite3.SQLITE_IOERR, RepositoryError),
            (sqlite3.SQLITE_CORRUPT, RepositoryError),
            (None, RepositoryError),
            (True, RepositoryError),
        ):
            with self.subTest(code=code):
                error = sqlite3.OperationalError("database is busy")
                if code is not None:
                    error.sqlite_errorcode = code
                with patch.object(repository_module, "connect_traced", side_effect=error):
                    with self.assertRaises(expected) as caught:
                        self.direct.read_observation()
                self.assertIs(type(caught.exception), expected)

    def test_observation_is_query_only_and_failure_closes_connection(self):
        connections = self.capture_connections()
        before = self.direct.path.read_bytes()

        def write_attempt(connection):
            self.assertEqual((1,), connection.execute("PRAGMA query_only").fetchone())
            connection.execute("DELETE FROM direct_probe")

        with patch.object(self.direct, "_load", side_effect=write_attempt):
            with self.assertRaises(RepositoryError) as caught:
                self.direct.read_observation()
        self.assertNotIsInstance(caught.exception, ObservationBusy)
        self.assert_closed(connections)
        self.assertEqual(before, self.direct.path.read_bytes())

    def test_pending_cancel_observation_does_not_prevent_writer_commit(self):
        with closing(sqlite3.connect(self.direct.path, timeout=0)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            payload = json.loads(writer.execute("SELECT payload FROM direct_probe").fetchone()[0])
            payload["status"] = "cancelled"
            writer.execute("UPDATE direct_probe SET payload=?", (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ))
            # A reserved writer still permits reading the previously committed
            # record; that observation carries no authority to send or cancel.
            self.assertEqual("waiting", self.direct.read_observation().status)
            writer.commit()  # timeout=0 proves the returned observation released its read lock.
        self.assertEqual("cancelled", self.direct.read_observation().status)

    def test_observation_does_not_erase_receipt_when_send_result_settles(self):
        self.direct.update(self.record.probe_id, status="dispatching")
        self.assertEqual("dispatching", self.direct.read_observation().status)
        receipt_turn = "33333333-3333-4333-8333-333333333333"
        self.direct.update(self.record.probe_id, acknowledged_turn=receipt_turn)
        self.assertEqual(receipt_turn, self.direct.read_observation().acknowledged_turn)
        self.direct.update(self.record.probe_id, status="accepted")
        observed = self.direct.read_observation()
        self.assertEqual(("accepted", receipt_turn), (observed.status, observed.acknowledged_turn))

    def test_overlapping_observation_allows_explicit_cancel_to_commit_once(self):
        observed, cancelled = self.update_during_observation(status="cancelled")
        self.assertEqual("waiting", observed.status)
        self.assertEqual("cancelled", cancelled.status)

    def test_overlapping_observations_preserve_send_intent_receipt_and_settlement(self):
        observed, dispatched = self.update_during_observation(status="dispatching")
        self.assertEqual(("waiting", "dispatching"), (observed.status, dispatched.status))
        receipt_turn = "33333333-3333-4333-8333-333333333333"
        observed, acknowledged = self.update_during_observation(acknowledged_turn=receipt_turn)
        self.assertIsNone(observed.acknowledged_turn)
        self.assertEqual(("dispatching", receipt_turn), (acknowledged.status, acknowledged.acknowledged_turn))
        observed, settled = self.update_during_observation(status="accepted")
        self.assertEqual(("dispatching", receipt_turn), (observed.status, observed.acknowledged_turn))
        self.assertEqual(("accepted", receipt_turn), (settled.status, settled.acknowledged_turn))

    def test_overlapping_observation_allows_receipt_after_accepted_settlement(self):
        self.direct.update(self.record.probe_id, status="dispatching")
        self.direct.update(self.record.probe_id, status="accepted")
        receipt_turn = "33333333-3333-4333-8333-333333333333"
        observed, acknowledged = self.update_during_observation(acknowledged_turn=receipt_turn)
        self.assertEqual(("accepted", None), (observed.status, observed.acknowledged_turn))
        self.assertEqual(("accepted", receipt_turn), (acknowledged.status, acknowledged.acknowledged_turn))

    def test_prolonged_observation_bounds_writer_wait_and_rolls_back_without_replay(self):
        release, reader, _ = self.held_observation()
        connections = self.capture_connections()
        started = time.monotonic()
        with patch.object(self.direct, "_load", wraps=self.direct._load) as loaded:
            try:
                with self.assertRaises(RepositoryError) as caught:
                    self.direct.update(self.record.probe_id, status="cancelled")
                elapsed = time.monotonic() - started
                self.assertEqual("state_unreadable", caught.exception.code)
                self.assertNotIsInstance(caught.exception, ObservationBusy)
                self.assertGreaterEqual(elapsed, 0.9)
                self.assertLess(elapsed, 3)
                self.assertEqual(1, loaded.call_count)
                self.assertEqual(1, len(connections))
                self.assert_closed(connections)
            finally:
                release.set()
                reader.join(6)
        self.assertEqual(self.record, self.direct.read())
        # Timeout releases the writer's OS lease as well as its transaction.
        self.assertEqual("cancelled", self.direct.update(self.record.probe_id, status="cancelled").status)

    def test_missing_or_corrupt_data_is_not_observation_busy(self):
        with closing(sqlite3.connect(self.direct.path)) as connection, connection:
            connection.execute("UPDATE direct_probe SET payload='{}'")
        for repository in (self.direct, ReviewWaitRepository(self.wait.path.parent / "absent.sqlite")):
            with self.subTest(repository=repository.path.name):
                with self.assertRaises(RepositoryError) as caught:
                    repository.read_observation()
                self.assertNotIsInstance(caught.exception, ObservationBusy)
                self.assertEqual("state_unreadable", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
