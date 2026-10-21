"""Batch reads preserve per-document limits without limiting their union."""
from unittest import TestCase, mock

from tests import test_usage_snapshot_read as support
from tests.test_usage_collection import THREAD, TURN
from task_governance_tool.usage_evidence import encode, snapshot, MAX_DOCUMENT_BYTES
from task_governance_tool.usage_publication_repository import UsagePublicationRepository
from task_governance_tool.usage_values import METRICS, UsageError


class UsageSnapshotLimitTests(TestCase):
    setUp = support.UsageSnapshotReadTests.setUp

    def large_snapshots(self):
        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
        self.repository.initialize()
        count = 40_000
        documents, keys = [], []
        with self.repository.connection(write=True) as connection:
            for group in range(2):
                members = [("openai", f"r{number:0199d}")
                           for number in range(group * count, (group + 1) * count)]
                self.assertLess(len(encode(members)), MAX_DOCUMENT_BYTES)
                models = [{"provider": "openai", "model": "fixture-model", "response_count": count,
                           **dict(zip(METRICS, (count, 0, count, None, None, None)))}]
                document = snapshot(self.repository.basis[0], {
                    "executions": [f"tg_execution_{group + 1:016x}"], "response_keys": members,
                    "models": models, "diagnostics": []})
                connection.executemany("INSERT INTO usage_responses VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((provider, response, THREAD, TURN, "fixture-model", None, 1, 0, 1, None, None, None)
                     for provider, response in members))
                connection.execute("INSERT INTO usage_snapshots VALUES (?,?)",
                                   (document["snapshot_id"], encode(document).decode()))
                connection.executemany("INSERT INTO usage_snapshot_members VALUES (?,?,?)",
                    ((document["snapshot_id"], provider, response) for provider, response in members))
                documents.append(document)
                keys.extend(members)
            # The individual documents and original-key requests remain valid;
            # their union exceeds the real, unchanged production byte limit.
            for row in connection.execute("SELECT * FROM usage_snapshots ORDER BY rowid"):
                self.repository._snapshot(connection, row)
        with self.assertRaises(UsageError):
            encode(keys)
        return documents, keys

    def test_individually_valid_snapshots_exceeding_aggregate_limit_are_read_once(self):
        documents, keys = self.large_snapshots()
        loaded, sizes = [], []
        original = self.repository._responses

        def responses(connection, selected):
            loaded.extend(selected)
            sizes.append(len(encode(sorted(selected))))
            return original(connection, selected)

        with self.repository.connection() as connection, \
                mock.patch.object(self.repository, "_responses", side_effect=responses):
            read = self.repository._snapshot_read(connection, current=True)
            self.assertEqual(read.documents, documents)
            # A second union and overlapping subsets use only this same read.
            self.assertEqual(len(read.responses(keys + keys[:20])), len(keys))
            totals = self.repository._totals(connection, keys, read=read)
            self.assertEqual(totals["response_count"], len(keys))
            self.assertEqual(totals["models"][0]["total_tokens"], len(keys))
        self.assertEqual(set(loaded), set(keys))
        self.assertEqual(len(loaded), len(keys))
        self.assertGreater(len(sizes), 1)
        self.assertLessEqual(max(sizes), MAX_DOCUMENT_BYTES)

        # A later read must validate a changed original, including the last key.
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_responses SET input_tokens=2,total_tokens=2 "
                               "WHERE provider=? AND response_id=?", keys[-1])
        with self.repository.connection() as connection, self.assertRaises(UsageError):
            self.repository._snapshot_read(connection, current=True)
