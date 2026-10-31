"""Same-guard collection projection; next guard and effect branches stay fresh."""
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_review_answer_tracking_collection import tracking_fixture
from task_governance_tool import review_answer_runtime as runtime
from task_governance_tool.review_answer_collection_repository import initial_record
from task_governance_tool.review_answer import AnswerError
from task_governance_tool.review_wait_runtime.managed_direct import ManagedDirectProbe
from task_governance_tool.review_wait_runtime.review_wait_direct import DirectProbe, DirectError


class GuardReuseTests(unittest.TestCase):
    def test_frozen_one_two_eight_reviewers_share_one_guard_read(self):
        for count in (1, 2, 8):
            with self.subTest(reviewers=count):
                reviewers = tuple(SimpleNamespace(reviewer_id=f'child-{i}', turn_id=f'boundary-{i}') for i in range(count))
                snapshot = {row.reviewer_id: dict(boundary_turn_id=row.turn_id, frozen=True,
                    source_identity=dict(turn_id=f'answer-{i}'), terminal='completed') for i, row in enumerate(reviewers)}
                probe = object.__new__(ManagedDirectProbe)
                probe.answer_tracking = Mock(side_effect=lambda _controller: deepcopy(snapshot))
                probe._reviewers_to_observe = Mock(return_value=reviewers)
                probe._halt_wait = Mock(return_value=False)
                probe._observe_reviewer = Mock(return_value=True)
                probe._observations_lock, probe._reviewer_observations = RLock(), {}
                probe._parent_ready = Mock(return_value=True)
                probe._prepared = Mock()
                host = Mock()
                controller, record = object(), object()
                observed = []
                self.assertTrue(probe._idle_and_ended(host, controller, record, 100,
                    reviewer_observer=observed.append))
                self.assertEqual(probe.answer_tracking.call_count, 1)
                self.assertEqual(observed, [(row.reviewer_id, f'answer-{i}', 'completed') for i, row in enumerate(reviewers)])
                host.read_child.assert_not_called()
                probe._prepared.assert_called_once_with(controller)
                # New guards reacquire, and cannot reuse an earlier frozen answer
                # after the original collection selection was replaced.
                snapshot[reviewers[-1].reviewer_id]['boundary_turn_id'] = 'replacement'
                with self.assertRaises(DirectError):
                    probe._idle_and_ended(host, controller, record, 100)
                self.assertEqual(probe.answer_tracking.call_count, 2)
                self.assertEqual(host.read_parent.call_count, 1)

    def test_pending_source_falls_back_without_borrowing_a_guard_snapshot(self):
        row = SimpleNamespace(reviewer_id='child', turn_id='boundary')
        probe = object.__new__(ManagedDirectProbe)
        probe.answer_tracking = Mock(return_value={'child': dict(boundary_turn_id='boundary', frozen=False)})
        probe._reviewers_to_observe = Mock(return_value=(row,))
        with patch.object(DirectProbe, '_idle_and_ended', return_value=False) as fresh:
            self.assertFalse(probe._idle_and_ended(object(), object(), object(), 100))
            fresh.assert_called_once()

    def test_source_selection_reads_each_group_once_then_confirms_and_projects(self):
        from task_governance_tool import storage, review_continuation_repository, review_wait_basis_repository
        for count in (1, 2, 8):
            with self.subTest(reviewers=count):
                metadata, _answer = tracking_fixture()
                records, children = [], []
                for index in range(1, count + 1):
                    child = f'90000000-0000-4000-8000-{index:012d}'
                    children.append(child)
                    row = initial_record(metadata['basis'])
                    row['binding'] = {**deepcopy(metadata['binding']), 'slot': index,
                        'reviewer_session_id': child, 'reviewer_key': child}
                    records.append(row)
                service = SimpleNamespace(repository=SimpleNamespace(read=Mock(side_effect=lambda: deepcopy(records))),
                    check_basis=Mock())
                basis = metadata['basis']['execution_basis']
                root = SimpleNamespace(version=2, basis=basis, preparation_id=metadata['basis']['preparation_id'])
                binding = SimpleNamespace(parent_thread_id=basis['parent_session_id'], task_id=basis['task_id'],
                    target_generation=basis['target_generation'])
                with patch.object(runtime, '_source_context', return_value=(SimpleNamespace(target=object()), root, None)), \
                        patch.object(runtime, '_source_service', return_value=service), \
                        patch.object(storage, 'connect_initialized_readonly', return_value=Mock()), \
                        patch.object(review_continuation_repository, 'read_continuations', return_value=()), \
                        patch.object(review_wait_basis_repository, 'read_review_basis', return_value=basis):
                    result = runtime.tracking_for_reviewers(None, None, binding, children)
                    self.assertEqual(set(result), set(children))
                    self.assertEqual(service.repository.read.call_count, 2)
                    self.assertEqual(service.check_basis.call_count, count)
                    # A change during admission is not silently projected from
                    # the earlier operational observation.
                    def changed(_basis, _slot):
                        records[0]['binding']['reviewer_session_id'] = 'foreign'
                    service.check_basis.side_effect = changed
                    with self.assertRaises(AnswerError):
                        runtime.tracking_for_reviewers(None, None, binding, children)


if __name__ == '__main__':
    unittest.main()
