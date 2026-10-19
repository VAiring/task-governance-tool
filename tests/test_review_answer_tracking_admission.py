"""Tracking material and replacement fences use the existing effect boundaries."""
from contextlib import contextmanager, closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_review_answer_tracking_collection import tracking_fixture
from tests.test_review_answer_pure_values import metadata_fixture, TURN2
from tests.test_review_continuation import A, B, ident
from tests.review_answer_fixture_support import review_fixture
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_answer_collection import CollectionService, source
from task_governance_tool.review_answer_collection_repository import CollectionRepository, _encode as encode_record
from task_governance_tool import review_answer_runtime as runtime
from task_governance_tool.review_answer_values import basis_digest
from task_governance_tool.review_answer_handoff import _CollectionAdmission
from task_governance_tool.review_preparation import _replacement_collection_fence
from task_governance_tool.review_answer_transport import CollectedItem, frame_collected_submission, decode_collected_submission
from task_governance_tool import review_collected_results as results, storage
from task_governance_tool.reviews import ReviewEvidenceError


class TrackingMaterialTests(unittest.TestCase):
    def setUp(self):
        metadata, _answer = tracking_fixture()
        self.basis = metadata["basis"]
        self.basis["execution_basis"].update(target_kind="git_snapshot", target_base_revision="a" * 40,
            target_value="sha256:" + "b" * 64)
        self.admission = _CollectionAdmission(Path("governed"), None, None, None, (), "", "", (), True, False, None)

    def test_tracking_checks_exact_head_and_index_and_sanitizes_failure(self):
        basis = self.basis["execution_basis"]
        with patch("task_governance_tool.git_snapshot.capture_git_snapshot",
                return_value=SimpleNamespace(base_revision=basis["target_base_revision"], fingerprint=basis["target_value"])) as capture:
            self.admission._material(self.basis)
            capture.assert_called_once_with(Path("governed"))
            capture.return_value.fingerprint = "sha256:" + "c" * 64
            with self.assertRaises(AnswerError) as changed:
                self.admission._material(self.basis)
            self.assertEqual(changed.exception.failure["class"], "binding_mismatch")
            capture.side_effect = RuntimeError("private Git failure")
            with self.assertRaises(AnswerError) as failed:
                self.admission._material(self.basis)
            self.assertEqual(failed.exception.failure["reason"], "transport_unavailable")
            self.assertNotIn("private", str(failed.exception))

    def test_legacy_and_immutable_targets_do_not_gain_ambient_git_check(self):
        with patch("task_governance_tool.git_snapshot.capture_git_snapshot") as capture:
            self.admission._material({**self.basis, "version": 1})
            self.basis["execution_basis"]["target_kind"] = "git_commit"
            self.admission._material(self.basis)
            capture.assert_not_called()


class TrackingOriginalAssignmentTests(unittest.TestCase):
    def ready(self, version):
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="taskgov-tracking-arm-"))
        self.repo = Path(temporary)
        metadata, _ = tracking_fixture() if version == 2 else metadata_fixture()
        self.bases = tuple({**metadata["basis"], "request_digest": "sha256:" + str(slot) * 64}
                           for slot in (1, 2))
        original_repository = CollectionRepository(self.repo / "initial.sqlite")
        original_repository.initialize(self.bases)
        self.initial = CollectionService(self.repo, original_repository,
            original_paths=(".tmp-review/a.json", ".tmp-review/b.json"), admit_basis=Mock())
        for slot, child in enumerate((A, B), 1):
            saved_binding = deepcopy(metadata["binding"])
            saved_binding.update(basis_digest=basis_digest(self.bases[slot - 1]), slot=slot,
                reviewer_key=child, reviewer_session_id=child)
            self.initial.bind(slot, saved_binding)
        # After selection the initial reviewer is no longer a fresh source.
        # Its issued assignment still proves dedication for the new slot.
        self.initial.admit_basis = Mock(side_effect=AnswerError("binding_mismatch", reason="identity"))
        continued, _ = tracking_fixture(continuation=True)
        continuation_repository = CollectionRepository(self.repo / "continued.sqlite")
        continuation_repository.initialize((continued["basis"],))
        self.current = CollectionService(self.repo, continuation_repository,
            original_paths=(".tmp-review/continued.json",), admit_basis=Mock())
        self.group = runtime.SourceGroup(self.current, (B,), (1,), (1,))
        self.root = SimpleNamespace(collection_bases=deepcopy(self.bases))
        self.binding = SimpleNamespace(parent_thread_id=metadata["binding"]["parent_session_id"])
        self.host = Mock()
        self.host.observe_tracking_baseline.return_value = dict(turn_id=TURN2, terminal="completed",
            binding_source=source("reviewer_tracking", 2))
        self.enterContext(patch.object(runtime, "_source_context", return_value=(None, self.root, None)))
        self.enterContext(patch.object(runtime, "_source_service", return_value=self.initial))

    def arm(self):
        return runtime.arm_sources(self.group, self.host, repo=self.repo, skill=None, binding=self.binding)

    def test_retained_initial_assignment_uses_its_original_slot_pin_for_both_versions(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.ready(version)
                original = self.initial.repository.read()[1]
                armed = self.arm()[0]
                self.assertEqual(armed["binding"]["slot"], 1)
                self.assertEqual(armed["binding"]["reviewer_session_id"], B)
                self.assertEqual(armed["binding"]["assignment"], original["binding"]["assignment"])
                self.assertEqual(armed["binding"]["tracking"]["boundary_turn_id"], TURN2)
                self.host.observe_tracking_baseline.assert_called_once_with(B)
                self.initial.admit_basis.assert_not_called()
                self.assertEqual(self.arm()[0], armed)
                self.host.observe_tracking_baseline.assert_called_once_with(B)
                self.assertEqual(self.initial.repository.read()[1], original)

    def test_self_consistent_unissued_original_rejects_before_host_or_binding(self):
        for version in (1, 2):
            for corruption in ("request_digest", "copied_slot", "copied_preparation"):
                with self.subTest(version=version, corruption=corruption):
                    self.ready(version)
                    originals = list(self.initial.repository.read())
                    if corruption == "request_digest":
                        originals[1]["basis"]["request_digest"] = "sha256:" + "f" * 64
                    elif corruption == "copied_slot":
                        # A record copied from another request can still have
                        # internally consistent reviewer and source identities.
                        originals[1]["basis"] = deepcopy(originals[0]["basis"])
                    else:
                        for row in originals:
                            row["basis"]["preparation_id"] = ident("tg_review_preparation_", 9)
                    for row in originals:
                        row["binding"]["basis_digest"] = basis_digest(row["basis"])
                    with closing(sqlite3.connect(self.initial.repository.path)) as connection, connection:
                        connection.executemany("UPDATE slots SET record_json=? WHERE slot=?",
                            [(encode_record(row), slot) for slot, row in enumerate(originals, 1)])
                    # Both the closed records and full operational inventory
                    # remain valid; only independent issuance rejects them.
                    self.assertEqual(self.initial.repository.read(), tuple(originals))
                    before = self.current.repository.path.read_bytes()
                    with self.assertRaises(AnswerError) as rejected:
                        self.arm()
                    self.assertEqual(rejected.exception.failure,
                        {"class": "binding_mismatch", "field": None, "reason": "identity"})
                    self.host.observe_tracking_baseline.assert_not_called()
                    self.initial.admit_basis.assert_not_called()
                    self.assertEqual(self.current.repository.path.read_bytes(), before)
                    self.assertIsNone(self.current.repository.read()[0]["binding"])


class ReplacementFenceTests(unittest.TestCase):
    def test_finalization_then_all_indexed_source_leases_span_pointer_publication(self):
        events = []
        @contextmanager
        def lease(label):
            events.append(("enter", label))
            yield
            events.append(("exit", label))
        def repository(path):
            return SimpleNamespace(serial=lambda: lease(str(path)))
        basis = dict(parent_session_id="parent", task_id="task", target_generation=1)
        root = SimpleNamespace(version=2, basis=basis, preparation_id="root", route="integrated")
        paths = SimpleNamespace(review_answer_collection_store=lambda parent, task, generation, prep, continuation=None:
            Path("initial" if continuation is None else continuation),
            review_continuation_store=lambda *args: Path("transitions"),
            review_finalization_store=lambda *args: Path("finalizer"))
        rounds = [SimpleNamespace(preparation_id="root", answer_protocol="review-answer-v1",
            collection_basis_digests=("pin",), continuation_id=name) for name in ("round-b", "round-a")]
        rounds.append(SimpleNamespace(preparation_id="other", answer_protocol="review-answer-v1",
            collection_basis_digests=("pin",), continuation_id="unrelated"))
        with patch("task_governance_tool.review_preparation.path_lexically_exists", return_value=True), \
                patch("task_governance_tool.review_preparation.FinalizationRepository", side_effect=repository), \
                patch("task_governance_tool.review_answer_collection_repository.CollectionRepository", side_effect=repository), \
                patch("task_governance_tool.review_wait_runtime.continuation_transition_repository.TransitionRepository") as transitions:
            transitions.return_value.read.return_value = rounds
            with _replacement_collection_fence(paths, root):
                events.append(("publish", "new preparation"))
        labels = ["finalizer", "initial", "round-a", "round-b"]
        self.assertEqual(events, [("enter", label) for label in labels]
            + [("publish", "new preparation")]
            + [("exit", label) for label in reversed(labels)])


class TrackingNativeAdmissionTests(unittest.TestCase):
    def test_locked_inactive_task_never_gets_collected_receipts(self):
        with review_fixture(schema_version=31, contract=None) as fixture:
            metadata, answer = fixture.metadata(1)
            metadata["basis"]["version"] = 2
            binding = metadata["binding"]
            dispatch, turn = binding.pop("dispatch_id"), binding.pop("initial_turn_id")
            binding.update(basis_digest=basis_digest(metadata["basis"]), binding_source=source("reviewer_tracking", 2),
                tracking=dict(kind="initial", boundary="inclusive", boundary_turn_id=turn, dispatch_id=dispatch))
            metadata.update(answer_source=source("answer_observation", 2), terminal_source=source("terminal_observation", 2))
            submitted = decode_collected_submission(frame_collected_submission(metadata["basis"]["preparation_id"],
                (CollectedItem(1, answer.raw, metadata, answer),)))
            actual = results.lock_and_reread_target_owner
            for status in ("cancelled", "paused", "blocked", "done"):
                def locked(*args, **kwargs):
                    row = deepcopy(actual(*args, **kwargs))
                    row["status"] = status
                    return row
                with self.subTest(status=status), closing(storage.connect_initialized(fixture.target)) as connection:
                    with patch.object(results, "lock_and_reread_target_owner", side_effect=locked), \
                            patch.object(results, "add_review_receipt") as writer:
                        with self.assertRaises(ReviewEvidenceError):
                            with connection:
                                results.add_collected_review_results(connection, fixture.target.project,
                                    fixture.basis["task_id"], submitted, caller=fixture.caller, database_target=fixture.target)
                        writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
