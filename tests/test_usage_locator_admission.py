"""Real selected core/operational stores; no host, product state or log bodies.

The shared review fixture injects a temporary database. Only path resolution is
redirected to that fixture; admission SQL and persisted assignments are real.
"""

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import sqlite3
from time import monotonic
import unittest
from unittest.mock import patch

from tests.review_answer_fixture_support import review_fixture
from tests.usage_locator_fixture_support import prepare_pending_locator_assignment, publish_collection
from tests.verification_receipt_test_support import run_taskgov, add_receipt, completion
from task_governance_tool import storage, review_continuation as continuation
from task_governance_tool import usage_locator_admission as admission
from task_governance_tool.review_answer_collection_repository import _encode
from task_governance_tool.review_answer_values import basis_digest
from task_governance_tool.review_preparation_repository import PreparationRepository
from task_governance_tool.review_wait_runtime.continuation_transition_repository import TransitionRepository
from task_governance_tool.review_wait_runtime.request_continuation import RequestRound, encode
from task_governance_tool.usage_values import UsageError


class PendingLocatorAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(review_fixture(schema_version=33, contract=None))
        observed = prepare_pending_locator_assignment(self.fixture)
        for key, value in vars(observed).items():
            setattr(self, key, value)
        self.enterContext(patch.object(admission, "_paths", return_value=self.paths))

    def publish_collection(self, metadata, continuation_id=None):
        return publish_collection(self.fixture, self.paths, self.root, metadata, continuation_id)

    def find(self, reviewer=None, parent=None):
        return admission.find_pending_assignment(self.fixture.target, parent or self.parent,
            reviewer or self.reviewer, monotonic() + 30)

    def state(self, pending):
        return admission.pending_assignment_state(self.fixture.target, pending, monotonic() + 30)

    def edit(self, *args):
        result = run_taskgov("task", "edit", self.fixture.task_id, *args,
            "--repo", str(self.fixture.repo), "--db", str(self.fixture.db), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_assignment_survives_restart_without_reading_review_material(self):
        files = (self.fixture.db, self.preparation.path, self.collection.path)
        before = {path: path.read_bytes() for path in files}
        pending = self.find()
        self.assertIsNotNone(pending)
        self.assertEqual(pending["source_basis_digest"], basis_digest(self.metadata["basis"]))
        self.assertEqual(self.state(pending), "pending")
        self.assertEqual(admission.validate_pending_review(dict(pending)), pending)
        self.assertEqual({path: path.read_bytes() for path in files}, before)
        # No Packet, result or reviewer log exists in the isolated fixture.
        self.assertFalse((self.fixture.repo / "reviews").exists())

    def test_parent_child_relation_or_unbound_slot_does_not_authorize_retention(self):
        unknown = "00000000-0000-4000-8000-999999999999"
        self.assertIsNone(self.find(reviewer=unknown))
        self.assertIsNone(self.find(parent=unknown))
        self.assertIsNone(self.find(reviewer=self.parent))
        with closing(sqlite3.connect(self.collection.path)) as connection, connection:
            records = self.collection.read()
            connection.execute("UPDATE slots SET record_json=? WHERE slot=1", (_encode(dict(records[0], binding=None,
                attempts=[dict(records[0]["attempts"][0], turn_id=None)])),))
        self.assertIsNone(self.find())

    def test_exact_receipt_promotes_even_after_native_completion(self):
        pending = self.find()
        with closing(storage.connect_initialized(self.fixture.target)) as connection, connection:
            self.fixture.add(connection, 2)
        self.assertEqual(self.state(pending), "pending")  # Another actual reviewer is insufficient.
        with closing(storage.connect_initialized(self.fixture.target)) as connection, connection:
            self.fixture.add(connection, 1)
        self.assertEqual(self.state(pending), "registered")
        verified = add_receipt(self.fixture.db, self.fixture.repo, self.fixture.task_id,
            self.fixture.basis["target_generation"])
        self.assertEqual(verified.returncode, 0, verified.stdout)
        finished = completion(self.fixture.db, self.fixture.repo, self.fixture.task_id)
        self.assertEqual(finished.returncode, 0, finished.stdout)
        self.assertEqual(self.state(pending), "registered")
        self.assertIsNone(self.find())

    def test_known_cancellation_revokes_but_transient_failure_does_not(self):
        pending = self.find()
        with patch.object(admission, "_assignment", side_effect=OSError("busy")):
            self.assertEqual(self.state(pending), "unknown")
        self.edit("--status", "cancelled")
        self.assertEqual(self.state(pending), "revoked")

    def test_generation_or_project_mismatch_cannot_admit_old_pending_basis(self):
        pending = self.find()
        for key, value in (("target_generation", 2), ("ownership_generation", 100),
                ("contract_revision", 5), ("project_path_hash", "f" * 64)):
            with self.subTest(key=key):
                self.assertEqual(self.state(dict(pending, **{key: value})), "revoked")

    def test_valid_sidecar_assignment_replacement_never_reuses_old_candidate(self):
        pending = self.find()
        with closing(sqlite3.connect(self.collection.path)) as connection, connection:
            records = self.collection.read()
            binding = dict(records[0]["binding"], initial_turn_id="00000000-0000-4000-8000-000000000099")
            connection.execute("UPDATE slots SET record_json=? WHERE slot=1", (_encode(dict(records[0], binding=binding,
                attempts=[dict(records[0]["attempts"][0], turn_id=binding["initial_turn_id"])])),))
        self.assertEqual(self.state(pending), "unknown")
        self.assertNotEqual(self.find()["assignment_digest"], pending["assignment_digest"])
        with closing(storage.connect_initialized(self.fixture.target)) as connection, connection:
            self.fixture.add(connection, 1)
        self.assertEqual(self.state(pending), "registered")

    def test_missing_operational_store_is_unknown_and_expired_budget_does_no_read(self):
        pending = self.find()
        self.collection.path.rename(self.collection.path.with_suffix(".held"))
        self.assertEqual(self.state(pending), "unknown")
        with patch.object(admission, "connect_initialized_readonly", side_effect=AssertionError("expired read")):
            self.assertIsNone(admission.find_pending_assignment(self.fixture.target, self.parent, self.reviewer, 0))
            self.assertEqual(admission.pending_assignment_state(self.fixture.target, pending, 0), "unknown")

    def test_current_pointer_does_not_decode_unrelated_preparation_history(self):
        with closing(sqlite3.connect(self.preparation.path)) as connection, connection:
            connection.executemany("INSERT INTO preparations VALUES (?,?)", [
                ("tg_review_preparation_" + f"{index:016x}", "unrelated unread payload") for index in range(10, 110)])
        with patch.object(PreparationRepository, "read", side_effect=AssertionError("whole preparation inventory")), \
             patch.object(TransitionRepository, "read", side_effect=AssertionError("whole continuation inventory")):
            self.assertIsNotNone(self.find())
        with closing(storage.connect_initialized_readonly(self.fixture.target, operation_local=True)) as connection:
            plan = connection.execute("EXPLAIN QUERY PLAN SELECT task_id FROM task_ownership WHERE project_id=? "
                "AND coalesce(owner_session_id,completion_session_id)=? AND state IN ('owned','completion_only') LIMIT 2",
                (self.fixture.basis["project_id"], self.parent)).fetchall()
            continuation_plan = connection.execute("EXPLAIN QUERY PLAN SELECT * FROM review_continuations "
                "WHERE task_id=? AND preparation_id=? ORDER BY sequence DESC LIMIT 1",
                (self.fixture.task_id, self.root.preparation_id)).fetchall()
            receipt_plan = connection.execute("EXPLAIN QUERY PLAN SELECT review_receipt_id,basis_json "
                "FROM review_collection_sources WHERE preparation_id=? AND slot=? AND reviewer_session_id=? AND basis_digest=? LIMIT 3",
                (self.root.preparation_id, 1, self.reviewer, basis_digest(self.metadata["basis"]))).fetchall()
        self.assertTrue(any("idx_task_ownership_held_actor" in row[3] for row in plan), plan)
        self.assertTrue(any("SEARCH review_continuations USING INDEX idx_review_continuations_task_history" in row[3]
            for row in continuation_plan), continuation_plan)
        self.assertTrue(any("SEARCH review_collection_sources USING INDEX" in row[3]
            and "preparation_id=? AND slot=?" in row[3] for row in receipt_plan), receipt_plan)

    def test_collected_continuation_replaces_selected_initial_and_preserves_unselected(self):
        old = self.find()
        # The native preparation uses the exact same root ID as the collected sources.
        continued, metadata, _ = self.fixture.collected_source_continuation()
        row = RequestRound(preparation_id=continued.preparation_id, continuation_id=continued.continuation_id,
            continuation_sequence=continued.sequence, basis_digest=continuation.basis_digest(continued.basis),
            wait_basis_digest="c" * 64, selection_digest=continuation.selection_digest(continued.selected),
            expected_attempt=0, expected_request_digest=None, old_automation_id=None, old_request_phase=None,
            phase="prepared", packet_path="reviews/continued.json", packet_digest=metadata["basis"]["packet_digest"][7:],
            result_paths=("reviews/continued-a.json",), answer_protocol="review-answer-v1",
            collection_basis_digests=(basis_digest(metadata["basis"]),))
        path = self.paths.review_continuation_store(self.parent, self.fixture.task_id, self.fixture.basis["target_generation"])
        repo = TransitionRepository(path)
        with repo.serial(create=True):
            pass
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO rounds VALUES (1,?,?,?,?)", (row.preparation_id,
                row.continuation_sequence, row.continuation_id, encode(row)))
            connection.execute("INSERT INTO current_round VALUES (1,1)")
        self.publish_collection((metadata,), continued.continuation_id)
        new = self.find()
        self.assertEqual(new["continuation_id"], continued.continuation_id)
        self.assertEqual(self.state(old), "unknown")
        self.assertEqual(self.state(new), "pending")
        other = self.find(reviewer=self.other["binding"]["reviewer_session_id"])
        self.assertIsNotNone(other)
        self.assertIsNone(other["continuation_id"])

    def test_current_tracking_binding_is_exact_and_invalidation_never_admits_a_log(self):
        metadata = deepcopy(self.metadata)
        metadata["basis"].update(version=2, preparation_id="tg_review_preparation_" + "e" * 16)
        binding = metadata["binding"]
        binding["tracking"] = dict(kind="initial", boundary="inclusive",
            boundary_turn_id=binding.pop("initial_turn_id"), dispatch_id=binding.pop("dispatch_id"))
        binding["binding_source"].update(version=2, operation="reviewer_tracking")
        binding["basis_digest"] = basis_digest(metadata["basis"])
        root = replace(self.root, preparation_id=metadata["basis"]["preparation_id"],
            result_paths=("reviews/tracked.json",), collection_bases=(metadata["basis"],))
        with self.preparation.serial() as lease:
            self.preparation.publish(lease, root)
        collection = publish_collection(self.fixture, self.paths, root, (metadata,))
        pending = self.find()
        self.assertIsNotNone(pending)
        self.assertEqual(self.state(pending), "pending")
        with collection.serial() as lease:
            collection.update(lease, 1, lambda record: dict(record, tracking_state="invalidated"))
        self.assertIsNone(self.find())
        self.assertEqual(self.state(pending), "unknown")

    def test_closed_pending_shape_rejects_bool_null_and_extra_payload(self):
        pending = self.find()
        for changed in (dict(pending, slot=True), dict(pending, target_base_revision=None),
                dict(pending, prompt="unrelated"), dict(pending, parent_session_id=self.reviewer)):
            with self.subTest(changed=changed), self.assertRaises(UsageError):
                admission.validate_pending_review(changed)


if __name__ == "__main__":
    unittest.main()
