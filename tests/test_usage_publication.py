"""Resumable real-repository publication and explicitly deferred integrity."""

from contextlib import ExitStack, closing
import json
import os
import sqlite3
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_usage_response_repository as support
from tests.test_usage_response_repository import A
from tests.test_usage_collection import usage
from tests.test_usage_attribution import turn
from task_governance_tool import usage_evidence_service as service
from task_governance_tool import usage_incremental_repository as incremental
from task_governance_tool.usage_publication_repository import UsagePublicationRepository, INTEGRITY
from task_governance_tool.usage_response_repository import UsageResponseRepository
from task_governance_tool.usage_values import UsageError


class UsagePublicationTests(unittest.TestCase):
    records = support.ResponseRepositoryTests.records
    write = support.ResponseRepositoryTests.write
    append = support.ResponseRepositoryTests.append
    reader = support.ResponseRepositoryTests.reader
    acknowledgement = support.ResponseRepositoryTests.acknowledgement
    operation = support.ResponseRepositoryTests.operation
    write_turn = support.ResponseRepositoryTests.write_turn
    ingest = support.ResponseRepositoryTests.ingest
    read = support.ResponseRepositoryTests.read

    def setUp(self):
        support.ResponseRepositoryTests.setUp(self)
        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)
        self.repository.initialize()
        self.target = SimpleNamespace(db_path=self.core_path,
            resolved_usage_root=self.root / "publication",
            resolved_usage_snapshots=self.root / "publication/snapshots",
            resolved_usage_index=self.root / "publication/index.json",
            resolved_usage_lock=self.root / "publication/usage.lock")
        self.patches = self.enterContext(ExitStack())
        self.patches.enter_context(mock.patch.object(service, "repository_for", side_effect=lambda _: self.repository))
        def core_reader(*args, **kwargs):
            connection = sqlite3.connect(f"file:{self.core_path.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            return connection
        self.patches.enter_context(mock.patch.object(service, "connect_initialized_readonly", side_effect=core_reader))

    def history(self, count=12):
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0"))
        self.ingest()
        self.repository.refresh(self.reader)
        for n in range(1, count):
            self.append(usage(f"r{n}", turn=turn(1)))
            self.ingest()
            self.repository.refresh(self.reader)

    def refresh(self):
        return service.refresh_usage(self.target)

    def restart(self):
        self.repository = UsagePublicationRepository(self.repository.path, *self.repository.basis)

    def test_stable_history_advances_across_restart_and_fixed_budget_then_matches_full_projection(self):
        self.history(64)
        self.assertEqual(self.refresh()["publication"], "current")
        last_good = self.target.resolved_usage_index.read_bytes()
        self.append(usage("new", turn=turn(1)))
        self.ingest()
        clock, visited = [0.0], []
        publish = service._publish
        def cost(path, *args, **kwargs):
            result = publish(path, *args, **kwargs)
            if path.parent == self.target.resolved_usage_snapshots:
                visited.append(path.stem)
                clock[0] += 3
            return result
        with mock.patch.object(incremental, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(service, "_publish", side_effect=cost):
            starts = []
            for _ in range(32):
                self.restart()
                clock[0] = 0.0
                before = len(visited)
                result = service.refresh_usage(self.target, deadline=10.0)
                starts.append(visited[before])
                if result["publication"] == "current":
                    break
                self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)
            else:
                self.fail("finite stable history failed to progress")
        self.assertEqual(len(starts), len(set(starts)))
        self.repository.deadline = float("inf")
        projected = self.read(audit=True)
        expected = {**projected, "snapshots": [x["snapshot_id"] for x in projected["snapshots"]], "integrity": INTEGRITY}
        self.assertEqual(json.loads(self.target.resolved_usage_index.read_bytes()), expected)
        self.assertIn("evidence_verification_deferred", result["diagnostics"])

    def test_one_capture_classification_and_one_current_validation_and_read_reuses_basis(self):
        self.history(5)
        with mock.patch.object(self.repository, "attribution", wraps=self.repository.attribution) as classify, \
                mock.patch.object(self.repository, "_snapshots", wraps=self.repository._snapshots) as snapshots:
            self.assertEqual(self.refresh()["publication"], "current")
            self.assertEqual(classify.call_count, 1)
            self.assertEqual(snapshots.call_count, 1)
        before = self.repository.path.read_bytes()
        with mock.patch.object(self.repository, "_snapshots", wraps=self.repository._snapshots) as snapshots, \
                mock.patch.object(self.repository, "_core_basis", wraps=self.repository._core_basis) as basis:
            self.read(A)
            self.assertEqual((snapshots.call_count, basis.call_count), (1, 1))
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_index_failure_resumes_at_index_without_reauditing_history(self):
        self.history(5)
        self.assertEqual(self.refresh()["publication"], "current")
        last_good = self.target.resolved_usage_index.read_bytes()
        self.append(usage("later", turn=turn(1)))
        self.ingest()
        publish = service._publish
        def fail(path, *args, **kwargs):
            if path == self.target.resolved_usage_index:
                raise UsageError()
            return publish(path, *args, **kwargs)
        with mock.patch.object(service, "_publish", side_effect=fail):
            self.assertEqual(self.refresh()["publication"], "pending")
        self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)
        self.restart()
        with mock.patch.object(self.repository, "publication_snapshot", wraps=self.repository.publication_snapshot) as checked:
            self.assertEqual(self.refresh()["publication"], "current")
            checked.assert_not_called()

    def test_same_length_restored_mtime_mutation_is_detected_next_sweep_without_overwrite(self):
        self.history(5)
        self.assertEqual(self.refresh()["publication"], "current")
        last_good = self.target.resolved_usage_index.read_bytes()
        identity = json.loads(last_good)["snapshots"][0]
        path = self.target.resolved_usage_snapshots / (identity + ".json")
        stat = path.stat()
        original = path.read_bytes()
        corrupt = original.replace(b'"registered_only"', b'"registered_onlx"', 1)
        self.assertNotEqual(corrupt, original)
        self.assertEqual(len(corrupt), stat.st_size)
        path.write_bytes(corrupt)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.restart()
        self.assertEqual(self.refresh()["publication"], "pending")
        self.assertEqual(path.read_bytes(), corrupt)
        self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)

    def test_inter_event_edit_after_visit_is_explicitly_deferred_until_next_cycle(self):
        self.history(5)
        index, state = self.repository.prepare_publication(self.reader)
        service._directory(self.target.resolved_usage_root, self.root)
        service._directory(self.target.resolved_usage_snapshots, self.target.resolved_usage_root)
        first = index["snapshots"][0]
        for identity in index["snapshots"]:
            service._publish(self.target.resolved_usage_snapshots / (identity + ".json"),
                self.repository.publication_snapshot(identity), self.target.resolved_usage_root, immutable=True)
            state = self.repository.advance_publication(state)
        path = self.target.resolved_usage_snapshots / (first + ".json")
        path.write_bytes(b"invalid-after-visit")
        self.restart()
        self.assertEqual(self.refresh()["publication"], "current")
        self.assertEqual(json.loads(self.target.resolved_usage_index.read_bytes())["integrity"], INTEGRITY)
        self.assertEqual(self.refresh()["publication"], "pending")
        self.assertEqual(path.read_bytes(), b"invalid-after-visit")

    def test_basis_change_and_stale_cas_never_adopt_old_cursor(self):
        self.history(5)
        index, old = self.repository.prepare_publication(self.reader)
        old = self.repository.advance_publication(old)
        self.append(usage("changed", turn=turn(1)))
        self.ingest()
        newer, current = self.repository.prepare_publication(self.reader)
        self.assertNotEqual(index, newer)
        self.assertEqual(current["position"], 0)
        with self.assertRaises(UsageError):
            self.repository.advance_publication(old)
        self.assertEqual(self.refresh()["publication"], "current")

    def test_final_guard_rejects_source_and_core_change_without_publishing_index(self):
        self.history(2)
        self.assertEqual(self.refresh()["publication"], "current")
        last_good = self.target.resolved_usage_index.read_bytes()
        publish = service._publish
        changed = []
        def concurrent(path, *args, **kwargs):
            result = publish(path, *args, **kwargs)
            if path.parent == self.target.resolved_usage_snapshots and not changed:
                changed.append(True)
                # A numerical writer succeeds here: publication holds no writer.
                self.append(usage("concurrent", turn=turn(1)))
                self.ingest()
            return result
        with mock.patch.object(service, "_publish", side_effect=concurrent):
            self.assertEqual(self.refresh()["publication"], "pending")
        self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)
        self.assertEqual(self.refresh()["publication"], "current")
        index, state = self.repository.prepare_publication(self.reader)
        for _ in index["snapshots"]:
            state = self.repository.advance_publication(state)
        with closing(sqlite3.connect(self.core_path)) as core:
            core.execute("DELETE FROM task_events")
            core.commit()
        with self.assertRaises(UsageError):
            with self.repository.publication_guard(self.reader, index, state):
                self.fail("stale core accepted")

    def test_corrupt_history_members_and_original_counters_fail_closed(self):
        self.history(5)
        self.assertEqual(self.refresh()["publication"], "current")
        last_good = self.target.resolved_usage_index.read_bytes()
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_responses SET input_tokens=input_tokens+1,total_tokens=total_tokens+1 WHERE response_id='r0'")
        self.assertEqual(self.refresh()["publication"], "pending")
        self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)

    def test_historical_document_members_and_edges_are_revalidated(self):
        self.history(5)
        self.assertEqual(self.refresh()["publication"], "current")
        baseline = self.repository.path.read_bytes()
        last_good = self.target.resolved_usage_index.read_bytes()
        identity = json.loads(last_good)["snapshots"][0]
        cases = (("usage_snapshots", "UPDATE", "UPDATE usage_snapshots SET document='{}' WHERE snapshot_id=?"),
                 ("usage_snapshot_members", "DELETE", "DELETE FROM usage_snapshot_members WHERE snapshot_id=?"),
                 ("usage_supersessions", "DELETE", "DELETE FROM usage_supersessions WHERE predecessor_id=?"))
        for table, operation, sql in cases:
            with self.subTest(table=table):
                self.repository.path.write_bytes(baseline)
                with self.repository.connection(write=True) as connection:
                    name = f"trg_{table}_no_{operation.lower()}"
                    ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
                    connection.execute("DROP TRIGGER " + name)
                    connection.execute(sql, (identity,))
                    connection.execute(ddl)
                self.restart()
                self.assertEqual(self.refresh()["publication"], "pending")
                self.assertEqual(self.target.resolved_usage_index.read_bytes(), last_good)

    def test_unchanged_input_progress_and_interrupted_step_replay(self):
        self.history(8)
        self.assertEqual(self.refresh()["publication"], "current")
        original = self.repository.advance_publication
        calls = []
        def interrupt(state, **kwargs):
            calls.append(state["position"])
            if len(calls) == 3:
                raise UsageError()
            return original(state, **kwargs)
        with mock.patch.object(self.repository, "advance_publication", side_effect=interrupt):
            self.assertEqual(self.refresh()["publication"], "pending")
        self.restart()
        visited = []
        check = self.repository.publication_snapshot
        def observe(identity):
            visited.append(identity)
            return check(identity)
        with mock.patch.object(self.repository, "publication_snapshot", side_effect=observe):
            self.assertEqual(self.refresh()["publication"], "current")
        index = json.loads(self.target.resolved_usage_index.read_bytes())
        self.assertEqual(visited, index["snapshots"][2:])

    def test_corrupt_progress_is_not_silently_reset(self):
        self.history(2)
        self.assertEqual(self.refresh()["publication"], "current")
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_publication SET basis='invalid'")
        before = self.repository.path.read_bytes()
        self.assertEqual(self.refresh()["publication"], "pending")
        self.assertEqual(before, self.repository.path.read_bytes())

    def test_in_range_cursor_damage_is_not_a_verified_prefix(self):
        self.history(3)
        self.repository.prepare_publication(self.reader)
        with self.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_publication SET position=total")
        self.assertEqual(self.refresh()["publication"], "pending")
        self.assertFalse(self.target.resolved_usage_index.exists())

    def test_explicit_migration_preserves_populated_schema6_and_all_older_schemas(self):
        for previous in UsagePublicationRepository._previous_repositories:
            with self.subTest(schema=previous.migrations[-1][0]):
                path = self.root / f"migration-{previous.migrations[-1][0]}.sqlite"
                old = previous(path, *self.repository.basis)
                old.initialize()
                before = path.read_bytes()
                new = UsagePublicationRepository(path, *old.basis)
                self.assertEqual(new.inspect(), "migration_required")
                with self.assertRaises(UsageError):
                    new.registered_sources()
                self.assertEqual(path.read_bytes(), before)
                with mock.patch.object(new, "_before_publication_marker", side_effect=UsageError()):
                    with self.assertRaises(UsageError):
                        new.initialize()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(new.initialize(), "migrated")
                self.assertEqual(new.initialize(), "current")
        # Build real response continuations/snapshots in a schema-6 store.
        old_path = self.root / "populated-six.sqlite"
        self.repository = UsageResponseRepository(old_path, *self.repository.basis)
        self.repository.initialize()
        from task_governance_tool.session_identity import CallerIdentity
        from tests.test_usage_collection import THREAD
        self.repository.register_source(self.source.source_id, CallerIdentity(THREAD))
        self.history(3)
        with self.repository.connection() as connection:
            tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='usage_migrations'")]
            before = {table: list(map(tuple, connection.execute('SELECT * FROM ' + table))) for table in tables}
        self.restart()
        self.assertEqual(self.repository.initialize(), "migrated")
        with self.repository.connection() as connection:
            for table, rows in before.items():
                self.assertEqual(list(map(tuple, connection.execute('SELECT * FROM ' + table))), rows, table)

    def test_migration_rechecks_predecessor_under_writer_before_selecting_replay(self):
        from task_governance_tool.usage_incremental_repository import UsageIncrementalRepository
        from task_governance_tool.session_identity import CallerIdentity
        from tests.test_usage_collection import THREAD
        self.write_turn(self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0"))
        old = UsageIncrementalRepository(self.root / "predecessor-five.sqlite", *self.repository.basis)
        old.initialize()
        old.register_source(self.source.source_id, CallerIdentity(THREAD))
        old.collect_source(self.source)
        prior_bytes = old.path.read_bytes()
        with old.connection() as connection:
            self.assertTrue(all(not json.loads(row[0])["reset"] for row in connection.execute("SELECT document FROM usage_scan_state")))
        six = UsageResponseRepository(self.root / "moving-predecessor.sqlite", *self.repository.basis)
        six.initialize()
        new = UsagePublicationRepository(six.path, *six.basis)
        inspect = new.inspect
        def changed_after_inspection():
            result = inspect()
            six.path.write_bytes(prior_bytes)
            return result
        with mock.patch.object(new, "inspect", side_effect=changed_after_inspection):
            self.assertEqual(new.initialize(), "migrated")
        with new.connection() as connection:
            rows = connection.execute("SELECT document FROM usage_scan_state").fetchall()
            self.assertTrue(rows)
            self.assertTrue(all(json.loads(row[0])["reset"] for row in rows))


if __name__ == "__main__":
    unittest.main()
