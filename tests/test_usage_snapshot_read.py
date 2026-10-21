"""Same-read reuse, selected originals and immutable overlap regressions."""
from unittest import TestCase, mock

from tests import test_usage_response_repository as support
from task_governance_tool import usage_snapshot_read
from task_governance_tool.usage_evidence import digest, encode
from task_governance_tool.usage_values import UsageError


class UsageSnapshotReadTests(TestCase):
    setUp = support.ResponseRepositoryTests.setUp
    records = support.ResponseRepositoryTests.records
    write = support.ResponseRepositoryTests.write
    append = support.ResponseRepositoryTests.append
    reader = support.ResponseRepositoryTests.reader
    acknowledgement = support.ResponseRepositoryTests.acknowledgement
    operation = support.ResponseRepositoryTests.operation
    write_turn = support.ResponseRepositoryTests.write_turn
    ingest = support.ResponseRepositoryTests.ingest
    read = support.ResponseRepositoryTests.read

    def independent(self, count=24):
        tasks = [f"tg_task_{i + 1:016x}" for i in range(count)]
        rows = []
        for i, task in enumerate(tasks):
            rows += self.operation(f"start{i}", self.acknowledgement(task, i + 1, "in_progress"), f"start{i}")
            rows += self.operation(f"end{i}", self.acknowledgement(task, i + 1, "done", cycle=f"cycle{i}"), f"end{i}")
        self.write_turn(rows)
        self.ingest()
        self.repository.refresh(self.reader)
        return tasks

    def test_capture_batches_members_and_originals_and_preserves_output(self):
        self.independent()
        before = self.read()
        reads = []
        original = self.repository._responses

        def responses(connection, keys):
            reads.append(len(keys))
            return original(connection, keys)

        with mock.patch.object(self.repository, "members", wraps=self.repository.members) as members, \
                mock.patch.object(self.repository, "_responses", side_effect=responses):
            self.repository.refresh(self.reader)
        self.assertEqual(members.call_count, 0)
        self.assertEqual(reads, [48])
        self.assertEqual(self.read(), before)

    def test_task_reads_only_its_originals_and_rechecks_next_connection(self):
        tasks = self.independent()
        original = self.repository._responses
        counts = []

        def responses(connection, keys):
            counts.append(len(keys))
            return original(connection, keys)

        with mock.patch.object(self.repository, "_responses", side_effect=responses), \
                mock.patch.object(self.repository, "_snapshot", wraps=self.repository._snapshot) as snapshots:
            before = self.read(tasks[0])
        self.assertEqual(counts, [2])
        self.assertEqual(snapshots.call_count, 1)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_responses SET input_tokens=input_tokens+1,total_tokens=total_tokens+1 "
                               "WHERE response_id='start1'")
        self.assertEqual(self.read(tasks[0]), before)
        with self.assertRaises(UsageError):
            self.read(tasks[1])
        with self.assertRaises(UsageError):
            self.repository.refresh(self.reader)

    def test_metadata_corruption_cannot_hide_a_task_original_from_selection(self):
        tasks = self.independent(2)
        with self.repository.connection(write=True) as connection:
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_usage_snapshots_no_update'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_usage_snapshots_no_update")
            connection.execute("UPDATE usage_snapshots SET document=json_set(document,'$.executions',json(?)) "
                               "WHERE document LIKE ?", ('["tg_execution_ffffffffffffffff"]', '%tg_execution_0000000000000001%'))
            connection.execute(trigger)
        with self.assertRaises(UsageError):
            self.read(tasks[0])

    def test_reuse_value_cannot_outlive_its_read_transaction(self):
        self.independent(2)
        with self.repository.connection() as connection:
            captured = self.repository._snapshot_read(connection, current=True)
            keys = next(iter(captured.members.values()))
            self.assertEqual(len(captured.responses(keys)), 2)
            connection.rollback()
            with self.assertRaises(UsageError):
                captured.responses(keys)

    def test_batched_identities_preserve_members_and_predecessor_edges(self):
        self.independent(3)
        with self.repository.connection(write=True) as connection:
            first = self.repository._snapshot_read(connection, current=True).documents[0]
            successor = {**first, "predecessors": [first["snapshot_id"]]}
            successor["snapshot_id"] = digest({k: v for k, v in successor.items() if k != "snapshot_id"})
            connection.execute("INSERT INTO usage_snapshots VALUES (?,?)",
                               (successor["snapshot_id"], encode(successor).decode()))
            connection.executemany("INSERT INTO usage_snapshot_members VALUES (?,?,?)",
                ((successor["snapshot_id"], *key) for key in self.repository.members(connection, first["snapshot_id"])))
            connection.execute("INSERT INTO usage_supersessions VALUES (?,?)",
                               (first["snapshot_id"], successor["snapshot_id"]))
        with self.repository.connection() as connection:
            expected = self.repository._snapshot_read(connection, current=True)
            with mock.patch.object(usage_snapshot_read, "_READ_BATCH_SIZE", 1):
                actual = self.repository._snapshot_read(connection, current=True)
            self.assertEqual(actual.documents, expected.documents)
            self.assertEqual(actual.members, expected.members)
            self.assertEqual(actual.predecessors, expected.predecessors)
            self.assertEqual(actual.predecessors[successor["snapshot_id"]], [first["snapshot_id"]])

    def test_deadline_between_original_batches_does_not_write_or_continue(self):
        self.independent(2)
        before = self.repository.path.read_bytes()
        original = self.repository._responses

        def expire(connection, keys):
            result = original(connection, keys)
            self.repository.deadline = 0
            return result

        try:
            with mock.patch.object(usage_snapshot_read, "_READ_BATCH_SIZE", 1), \
                    mock.patch.object(self.repository, "_responses", side_effect=expire) as reads:
                with self.repository.connection() as connection, self.assertRaises(UsageError):
                    self.repository._snapshot_read(connection, current=True)
                self.assertEqual(reads.call_count, 1)
        finally:
            self.repository.deadline = float("inf")
        self.assertEqual(self.repository.path.read_bytes(), before)
        with self.repository.connection() as connection:
            self.assertEqual(len(self.repository._snapshot_read(connection, current=True).documents), 2)
