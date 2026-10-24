"""Transaction-local relation reuse preserves the old current/history selection."""

from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
import json
import sqlite3
import unittest
from unittest import mock

from tests import test_usage_publication as support
from tests.test_usage_response_repository import A
from tests.test_usage_collection import usage
from tests.test_usage_attribution import turn
from task_governance_tool import usage_response_repository as response
from task_governance_tool import usage_evidence_service as service
from task_governance_tool import usage_incremental_repository as incremental
from task_governance_tool.usage_evidence import digest, encode
from task_governance_tool.usage_locator_repository import UsageLocatorRepository
from task_governance_tool.usage_values import UsageError


def legacy_relations(connection, history=False):
    """Pre-reuse SQL/decoding oracle, independent of the optimized loader."""
    positions, boundaries = [], []
    for row in connection.execute("SELECT position.*,source.thread_id AS source_thread FROM usage_response_positions position JOIN usage_sources source "
                                  "ON source.source_id=position.source_id AND source.incarnation=position.incarnation"):
        item = response.ResponsePosition(**{key: row[key] for key in response.ResponsePosition.__dataclass_fields__})
        if item.thread_id != row["source_thread"]:
            raise UsageError("usage_schema_invalid")
        positions.append((row["source_id"], row["incarnation"], item))
    for row in connection.execute("SELECT boundary.*,source.thread_id AS source_thread FROM usage_response_boundaries boundary JOIN usage_sources source "
                                  "ON source.source_id=boundary.source_id" + ("" if history else
                                  " AND source.incarnation=boundary.incarnation")):
        try:
            body = json.loads(row["document"])
            fields = set(response.ResponseBoundary.__dataclass_fields__)
            if not isinstance(body, dict) or set(body) not in (fields, fields - {"origin_version"}):
                raise UsageError("usage_schema_invalid")
            item = response.ResponseBoundary(**{"origin_version": 1, **body})
            if digest(body) != row["observation_id"] or item.thread_id != row["source_thread"]:
                raise UsageError("usage_schema_invalid")
        except (ValueError, TypeError, KeyError):
            raise UsageError("usage_schema_invalid") from None
        boundaries.append((row["source_id"], row["incarnation"], item))
    return positions, boundaries


class CountingConnection:
    def __init__(self, connection, counts):
        self.connection, self.counts = connection, counts

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args):
        cursor = self.connection.execute(sql, *args)
        kind = next((name for name in ("positions", "boundaries")
                     if "FROM usage_response_" + name + " " in sql), None)
        if kind is None:
            return cursor
        self.counts[kind + "_queries"] += 1
        def rows():
            for row in cursor:
                self.counts[kind + "_rows"] += 1
                yield row
        return rows()


class UsageRelationInputsTests(unittest.TestCase):
    records = support.UsagePublicationTests.records
    write = support.UsagePublicationTests.write
    append = support.UsagePublicationTests.append
    reader = support.UsagePublicationTests.reader
    acknowledgement = support.UsagePublicationTests.acknowledgement
    operation = support.UsagePublicationTests.operation
    write_turn = support.UsagePublicationTests.write_turn
    ingest = support.UsagePublicationTests.ingest
    read = support.UsagePublicationTests.read
    history = support.UsagePublicationTests.history
    refresh = support.UsagePublicationTests.refresh

    def setUp(self):
        support.UsagePublicationTests.setUp(self)
        self.repository = UsageLocatorRepository(self.repository.path, *self.repository.basis)
        self.repository.initialize()
        self.history(2)

    def add_boundaries(self, count, *, historical=False, conflict=False, legacy=False):
        with self.repository.connection(write=True) as connection:
            source, incarnation, item = self.repository._relations(connection)[1][0]
            if historical:
                old = incarnation + 10
                connection.execute("INSERT OR IGNORE INTO usage_incarnations SELECT source_id,?,offset,prefix,file_id "
                                   "FROM usage_sources WHERE source_id=?", (old, source))
                incarnation = old
            for n in range(count):
                changed = (replace(item, response_id="conflicting-origin", method="explicit") if conflict else
                           replace(item, command="task.context", call_id=f"check-{historical}-{n}", event_id=None,
                                   task_id=None, generation=None, status=None))
                body = asdict(changed)
                if legacy:
                    body.pop("origin_version")
                connection.execute("INSERT INTO usage_response_boundaries VALUES (?,?,?,?)",
                                   (source, incarnation, digest(body), encode(body).decode()))
            # An old position must never enter the current position set.
            if historical:
                connection.execute("INSERT OR IGNORE INTO usage_response_positions VALUES (?,?,?,?,?,?,?)",
                                   (source, incarnation, item.thread_id, item.turn_id, item.provider, "old-position", 1))

    @contextmanager
    def measured(self):
        counts = Counter()
        original_connection, decode, boundary = self.repository.connection, response.json.loads, response.ResponseBoundary
        @contextmanager
        def connection(**kwargs):
            with original_connection(**kwargs) as admitted:
                yield CountingConnection(admitted, counts)
        def loads(value, *args, **kwargs):
            result = decode(value, *args, **kwargs)
            if isinstance(result, dict) and "call_id" in result and "command" in result:
                counts["boundary_decodes"] += 1
            return result
        def validate(*args, **kwargs):
            counts["boundary_validations"] += 1
            return boundary(*args, **kwargs)
        with mock.patch.object(self.repository, "connection", side_effect=connection), \
                mock.patch.object(response.json, "loads", side_effect=loads), \
                mock.patch.object(response, "ResponseBoundary", side_effect=validate) as constructor:
            constructor.__dataclass_fields__ = boundary.__dataclass_fields__
            yield counts

    def assert_single_read(self, operation):
        with self.repository.connection() as connection:
            positions, history = legacy_relations(connection, True)
        with self.measured() as counts:
            operation()
        self.assertEqual(counts, Counter(positions_queries=1, positions_rows=len(positions),
                         boundaries_queries=1, boundaries_rows=len(history), boundary_decodes=len(history),
                         boundary_validations=len(history)))

    def test_capture_reads_and_validates_each_relation_once_as_sets_grow_independently(self):
        self.add_boundaries(3, historical=True, legacy=True)
        for growth in ("initial", "positions", "boundaries", "snapshots"):
            with self.subTest(growth=growth):
                if growth == "positions":
                    self.append(*(usage(f"extra-{n}", turn=turn(1)) for n in range(5)))
                    self.ingest()
                elif growth == "boundaries":
                    self.add_boundaries(7)
                elif growth == "snapshots":
                    for n in range(4):
                        self.append(usage(f"history-{n}", turn=turn(1)))
                        self.ingest()
                        self.repository.refresh(self.reader)
                self.assert_single_read(lambda: self.repository.prepare_publication(self.reader))

    def test_standalone_basis_attribution_display_and_fresh_guard_each_read_again(self):
        self.add_boundaries(2, historical=True)
        index, _ = self.repository.prepare_publication(self.reader)
        with self.reader() as core:
            self.assert_single_read(lambda: self.repository.attribution(core, geometry_only=True))
            self.assert_single_read(lambda: self.read())
            self.assert_single_read(lambda: self.read(A))
            def basis():
                with self.repository.connection() as connection:
                    self.repository._core_basis(core, connection)
            self.assert_single_read(basis)
        # Guard is a separate admitted read, even immediately after capture.
        with self.repository.connection() as connection:
            cursor = self.repository._publication_state(connection)
        while cursor["position"] < cursor["total"]:
            cursor = self.repository.advance_publication(cursor)
        def guard():
            with self.repository.publication_guard(self.reader, index, cursor):
                pass
        self.assert_single_read(guard)
        self.assert_single_read(lambda: self.repository.prepare_publication(self.reader))

    def assert_legacy_equivalent(self):
        def old_inputs(connection, inputs=None):
            positions, current = legacy_relations(connection)
            _, history = legacy_relations(connection, True)
            return response._RelationInputs(connection, tuple(positions), tuple(current), tuple(history))
        with self.reader() as core:
            classification = self.repository.attribution(core)
        index, _ = self.repository.prepare_publication(self.reader)
        projection = self.read(audit=True)
        with self.repository.connection() as connection:
            originals = list(map(tuple, connection.execute("SELECT * FROM usage_snapshots ORDER BY rowid")))
        with mock.patch.object(self.repository, "_relation_inputs", side_effect=old_inputs):
            with self.reader() as core:
                self.assertEqual(self.repository.attribution(core), classification)
            self.assertEqual(self.repository.prepare_publication(self.reader)[0], index)
            self.assertEqual(self.read(audit=True), projection)
        with self.repository.connection() as connection:
            self.assertEqual(list(map(tuple, connection.execute("SELECT * FROM usage_snapshots ORDER BY rowid"))), originals)

    def test_current_history_legacy_origin_and_conflict_keep_classification_basis_and_original_bytes(self):
        self.add_boundaries(2, historical=True, legacy=True)
        self.assert_legacy_equivalent()
        self.add_boundaries(1, historical=True, conflict=True)
        self.assert_legacy_equivalent()

    def test_boundary_addition_and_restored_away_core_are_fresh_and_keep_unresolved_result(self):
        old, _ = self.repository.prepare_publication(self.reader)
        self.add_boundaries(2)
        changed, _ = self.repository.prepare_publication(self.reader)
        self.assertNotEqual(old["core_digest"], changed["core_digest"])
        with closing(sqlite3.connect(self.core_path)) as core:
            core.execute("DELETE FROM task_events")
            core.commit()
        with self.assertRaisesRegex(UsageError, "usage_pending"):
            self.read()
        self.assert_legacy_equivalent()

    def test_historical_bad_digest_is_rejected_without_expanding_current_only_validation(self):
        self.add_boundaries(1, historical=True)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_response_boundaries SET observation_id=? WHERE incarnation>1", ("0" * 64,))
        with self.repository.connection() as connection:
            self.assertEqual(self.repository._relations(connection), legacy_relations(connection))
            for operation in (lambda: self.repository._relations(connection, history=True),
                              lambda: legacy_relations(connection, True)):
                with self.assertRaisesRegex(UsageError, "usage_schema_invalid"):
                    operation()
        for operation in (lambda: self.repository.prepare_publication(self.reader), lambda: self.read()):
            with self.assertRaisesRegex(UsageError, "usage_schema_invalid"):
                operation()

    def test_failed_capture_rolls_back_and_restart_reloads_relations(self):
        index, cursor = self.repository.prepare_publication(self.reader)
        self.append(usage("later", turn=turn(1)))
        self.ingest()
        with mock.patch.object(self.repository, "_before_snapshot_commit", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.prepare_publication(self.reader)
        with self.repository.connection() as connection:
            self.assertEqual(self.repository._publication_state(connection), cursor)
        self.repository = UsageLocatorRepository(self.repository.path, *self.repository.basis)
        self.assert_single_read(lambda: self.repository.prepare_publication(self.reader))
        self.assertNotEqual(self.repository.prepare_publication(self.reader)[0]["core_digest"], index["core_digest"])
        self.assert_legacy_equivalent()

    def test_bad_boundary_type_still_fails_even_with_a_matching_digest(self):
        with self.repository.connection(write=True) as connection:
            row = connection.execute("SELECT observation_id,document FROM usage_response_boundaries LIMIT 1").fetchone()
            body = json.loads(row["document"])
            body["record_order"] = "1"
            connection.execute("UPDATE usage_response_boundaries SET observation_id=?,document=? WHERE observation_id=?",
                               (digest(body), encode(body).decode(), row["observation_id"]))
        with self.repository.connection() as connection:
            for load in (lambda: self.repository._relation_inputs(connection), lambda: legacy_relations(connection, True)):
                with self.assertRaisesRegex(UsageError, "response_binding_unknown"):
                    load()
        with self.assertRaisesRegex(UsageError, "response_binding_unknown"):
            self.repository.prepare_publication(self.reader)

    def test_deadline_keeps_last_good_and_restarted_publication_matches_projection(self):
        self.assertEqual(self.refresh()["publication"], "current")
        last_good = self.target.resolved_usage_index.read_bytes()
        self.append(usage("later", turn=turn(1)))
        self.ingest()
        clock, publish = [0.0], service._publish
        def cost(path, *args, **kwargs):
            result = publish(path, *args, **kwargs)
            if path.parent == self.target.resolved_usage_snapshots:
                clock[0] += 3
            return result
        with mock.patch.object(incremental, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(service, "_publish", side_effect=cost):
            result = service.refresh_usage(self.target, deadline=3.0)
            self.assertEqual(result["publication"], "pending")
            self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)
            # At the exact deadline even the checkpoint cannot run. A resumed
            # event must fit one item AND its checkpoint, as the contract says.
            for _ in range(10):
                self.repository = UsageLocatorRepository(self.repository.path, *self.repository.basis)
                clock[0] = 0.0
                if service.refresh_usage(self.target, deadline=5.0)["publication"] == "current":
                    break
            else:
                self.fail("unchanged finite history did not resume")
        self.repository.deadline = float("inf")
        projected = self.read(audit=True)
        self.assertEqual(json.loads(self.target.resolved_usage_index.read_bytes()),
                         {**projected, "snapshots": [x["snapshot_id"] for x in projected["snapshots"]],
                          "integrity": dict(support.INTEGRITY)})
        self.assert_legacy_equivalent()

    def test_internal_inputs_cannot_cross_connection_or_closed_transaction(self):
        with self.repository.connection() as first, self.repository.connection() as second:
            inputs = self.repository._relation_inputs(first)
            with self.assertRaisesRegex(UsageError, "usage_schema_invalid"):
                self.repository._relation_inputs(second, inputs)
            first.rollback()
            with self.assertRaisesRegex(UsageError, "usage_schema_invalid"):
                self.repository._relation_inputs(first, inputs)
