"""Numerical-only migration and atomic turn/response/cursor replay."""

from contextlib import closing
from dataclasses import replace
import sqlite3
from unittest import mock

from tests import test_usage_collection as collection_tests
from tests.test_usage_collection import event, usage, THREAD, TURN
from tests.test_usage_turn_adapter import acknowledgement, tool_record, TASK, EVENT
from tests.test_usage_attribution import turn
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_attribution_repository import UsageAttributionRepository, resolve_operation
from task_governance_tool.usage_repository import UsageRepository
from task_governance_tool.usage_turn_adapter import OperationObservation, TurnObservation
from task_governance_tool.usage_values import UsageError
from task_governance_tool.session_identity import CallerIdentity


class UsageAttributionRepositoryTests(collection_tests.UsageCollectionTests):
    # Reuse the same numerical/atomic/privacy regression against schema 2 too.
    def setUp(self):
        super().setUp()
        self.old = self.repository
        self.repository = UsageAttributionRepository(self.old.path, *self.old.basis)
        self.assertEqual(self.repository.initialize(), "migrated")

    def test_turn_and_operation_replay_with_source_cursor(self):
        self.write(self.records(event("event_msg", type="task_started", turn_id=turn(2), started_at=2000),
                                tool_record(acknowledgement()), usage(turn=turn(2))))
        self.collect()
        self.collect()
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_turns").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_operation_turns").fetchone()[0], 1)

    def test_known_ack_discards_private_task_text_before_storage_and_hash(self):
        secret = "PRIVATE_TOOL_BODY_DO_NOT_RETAIN"
        ack = acknowledgement()
        ack["data"]["task"]["title"] = secret
        ack["data"]["event"]["summary"] = secret
        self.write(self.records(tool_record(ack), usage()))
        self.collect()
        self.assertNotIn(secret.encode(), self.repository.path.read_bytes())
        cursor = self.repository.cursor(self.source.source_id, THREAD)
        self.source.path.write_bytes(self.source.path.read_bytes().replace(secret.encode(), b"X" * len(secret)))
        replay = read_batch(self.source, cursor, include_attribution=True,
                            attribution_project_id=self.repository.basis[0])
        self.assertEqual(replay.successor.prefix, cursor.prefix)
        self.assertEqual(replay.successor.incarnation, cursor.incarnation)

    def test_other_project_ack_does_not_block_same_source_numerical_collection(self):
        ack = acknowledgement()
        ack["project_id"] = "other-project"
        ack["data"]["task"]["project_id"] = "other-project"
        ack["data"]["event"]["project_id"] = "other-project"
        self.write(self.records(tool_record(ack), usage()))
        self.assertEqual(self.collect()["models"][0]["response_count"], 1)
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_operation_turns").fetchone()[0], 0)

    def test_failure_rolls_back_boundary_response_and_cursor_together(self):
        self.write(self.records(event("event_msg", type="task_started", turn_id=turn(2), started_at=2000),
                                tool_record(acknowledgement()), usage(turn=turn(2))))
        batch = read_batch(self.source, self.repository.cursor(self.source.source_id, THREAD), include_attribution=True)
        with mock.patch.object(self.repository, "_before_cursor", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                self.repository.commit_batch(batch)
        with self.repository.connection() as connection:
            for table in ("usage_turns", "usage_operation_turns", "usage_responses"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).offset, 0)
        self.repository.commit_batch(batch)

    def test_binding_conflicts_do_not_replace_first_observation(self):
        first = OperationObservation(THREAD, turn(2), self.repository.basis[0], TASK, EVENT, 1, "in_progress")
        self.repository.record((first, replace(first, turn_id=turn(3)), TurnObservation(THREAD, turn(2), 2), TurnObservation(THREAD, turn(2), 9)))
        with self.repository.connection() as connection:
            self.assertEqual(connection.execute("SELECT turn_id FROM usage_operation_turns").fetchone()[0], turn(2))
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_operation_conflicts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM usage_turn_conflicts").fetchone()[0], 1)

    def test_unregistered_session_is_not_inferred_from_parent(self):
        with self.assertRaises(UsageError):
            self.repository.record((TurnObservation("11234567-89ab-7cde-8fab-0123456789ab", turn(2), 2),))

    def test_old_reader_refuses_schema_two_without_repair(self):
        original = self.repository.path.read_bytes()
        with self.assertRaises(UsageError):
            self.old.inspect()
        self.assertEqual(self.repository.path.read_bytes(), original)
        self.assertEqual(self.repository.initialize(), "current")

    def test_new_store_initializes_with_exact_schema_two(self):
        fresh = UsageAttributionRepository(self.root / "new.sqlite", *self.repository.basis)
        self.assertEqual(fresh.initialize(), "initialized")
        self.assertEqual(fresh.initialize(), "current")

    def test_migration_preserves_observations_and_rolls_back_on_failure(self):
        old = UsageRepository(self.root / "v1.sqlite", *self.repository.basis)
        old.initialize()
        old.register_source(self.source.source_id, CallerIdentity(THREAD))
        self.write(self.records(usage()))
        old.commit_batch(read_batch(self.source))
        baseline = old.summary()
        upgraded = UsageAttributionRepository(old.path, *old.basis)
        with mock.patch.object(upgraded, "_before_attribution_marker", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                upgraded.initialize()
        self.assertEqual(old.summary(), baseline)
        self.assertEqual(upgraded.initialize(), "migrated")
        self.assertEqual(upgraded.summary(), baseline)

    def test_exact_core_ack_lookup_and_missing_restored_reference(self):
        with closing(sqlite3.connect(":memory:")) as core:
            core.row_factory = sqlite3.Row
            core.executescript("""CREATE TABLE task_events(task_event_id,project_id,task_id,created_at);
                CREATE TABLE task_owner_transitions(transition_id,project_id,task_id,generation,current_status,actor_session_id,created_at);""")
            core.execute("INSERT INTO task_events VALUES (?,?,?,?)", (EVENT, self.repository.basis[0], TASK, "same-exact-time"))
            core.execute("INSERT INTO task_owner_transitions VALUES (?,?,?,?,?,?,?)", ("transition", self.repository.basis[0], TASK, 1, "in_progress", THREAD, "same-exact-time"))
            observed = OperationObservation(THREAD, turn(2), self.repository.basis[0], TASK, EVENT, 1, "in_progress")
            self.assertEqual(resolve_operation(core, observed), "transition")
            self.assertIsNone(resolve_operation(core, replace(observed, generation=2)))
            core.execute("DELETE FROM task_events")
            self.assertIsNone(resolve_operation(core, observed))
