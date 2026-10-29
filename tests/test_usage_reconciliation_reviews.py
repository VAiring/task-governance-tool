"""Real Receipt admission and incremental reviewer participation versus full."""

from contextlib import closing
import unittest

from tests import test_usage_collected_review as support
from tests.review_answer_fixture_support import review_fixture
from task_governance_tool import storage
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_evidence import snapshot
from task_governance_tool.usage_response_repository import component_identity


class ReviewTests(unittest.TestCase):
    items = support.CollectedReviewUsageTests.items
    register = support.CollectedReviewUsageTests.register
    source = support.CollectedReviewUsageTests.source
    read_display = support.CollectedReviewUsageTests.read_display
    records = support.CollectedReviewUsageTests.records
    write = support.CollectedReviewUsageTests.write
    ingest = support.CollectedReviewUsageTests.ingest

    def setUp(self):
        self.fixture = self.enterContext(review_fixture(schema_version=34))
        self.root = self.fixture.repo
        self.logs = self.root / "usage-review-logs"
        self.logs.mkdir()
        basis = self.fixture.basis
        self.repository = UsageReconciliationRepository(self.root / "candidate.sqlite", basis["project_id"],
            basis["project_path_hash"], basis["project_binding_generation"])
        self.repository.initialize()

    def reader(self):
        return closing(storage.connect_initialized_readonly(self.fixture.target, operation_local=True))

    def assert_same(self):
        index, _ = self.repository.prepare_publication(self.reader)
        with self.repository.connection() as connection, self.reader() as core:
            projected = self.repository.attribution(core)
            expected = [snapshot(self.repository.basis[0], item) for item in projected["components"]]
            actual = [self.repository._snapshot(connection, connection.execute("SELECT * FROM usage_snapshots WHERE snapshot_id=?", (identity,)).fetchone())
                      for identity in index["current_snapshot_ids"]]
            def comparable(value):
                return {key: item for key, item in value.items() if key not in {"predecessors", "snapshot_id"}}
            self.assertEqual(list(map(comparable, sorted(actual, key=component_identity))),
                             list(map(comparable, sorted(expected, key=component_identity))))
        return index

    def test_reviewer_only_stream_does_not_inherit_nonexistent_owner_stream_gap(self):
        item, = self.items()
        self.register((item,))
        source = self.source(item.session_binding.session_id)
        self.write(source, self.records(source.thread_id))
        self.ingest(source)
        self.assert_same()

    def test_delayed_receipt_reconciles_without_new_numerical_records(self):
        items = self.items((1, 2))
        self.register(items[:1])
        for number, item in enumerate(items):
            source = self.source(item.session_binding.session_id)
            self.write(source, self.records(source.thread_id, prefix=f"review-{number}-"))
            self.ingest(source)
        before = self.assert_same()
        self.register(items[1:])
        after = self.assert_same()
        self.assertNotEqual(before["current_snapshot_ids"], after["current_snapshot_ids"])


if __name__ == "__main__":
    unittest.main()
