"""Endpoint-only reads retain the full classifier's nonlocal conflict rules."""

from dataclasses import asdict, replace
from pathlib import Path
import json
import tempfile
import unittest

from tests import test_usage_review_attribution as review
from tests import test_usage_reconciliation_inputs as input_tests
from tests.test_usage_attribution import THREAD, OTHER, turn
from tests.test_usage_response_repository import A
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_reconciliation_intervals import endpoint_turns, reconcile_intervals
from task_governance_tool.usage_turn_adapter import TurnObservation
from task_governance_tool.usage_review_attribution import reviewer_intervals
from task_governance_tool.usage_evidence import encode


class EndpointSelectionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repository = UsageReconciliationRepository(self.root / "usage.sqlite", "project", "a" * 64, 1)
        self.repository.initialize()
        self.repository.register_session(CallerIdentity(THREAD))
        self.reference = review.UsageReviewAttributionTests()
        self.reference.setUp()
        self.turns = [TurnObservation(THREAD, turn(number), number * 1000) for number in range(1, 1001)]
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_turns VALUES (?,?,?)", [(value.thread_id, value.turn_id, value.started_at) for value in self.turns])

    def compare(self, *, conflict=(), extra=()):
        with self.repository.connection(write=True) as connection:
            connection.executemany("INSERT INTO usage_turn_conflicts VALUES (?,?)", conflict)
            connection.executemany("INSERT INTO usage_turns VALUES (?,?,?)", [(item.thread_id, item.turn_id, item.started_at) for item in extra])
        ref = self.reference
        keys = {(THREAD, item.turn_id) for item in (ref.read, ref.save)}
        with self.repository.connection() as connection:
            selected, conflicts = endpoint_turns(connection, keys, (THREAD,))
        arguments = (ref.owners, (ref.read, ref.save), (), [(review.RECEIPT, ref.target, ref.binding)])
        expected = reviewer_intervals(arguments[0], self.turns + list(extra), *arguments[1:], conflicting_turns=frozenset(conflict), conservative_legacy=True)
        actual = reviewer_intervals(arguments[0], selected, *arguments[1:], conflicting_turns=conflicts, conservative_legacy=True)
        self.assertEqual(actual, expected)
        return selected, actual

    def test_unrelated_thousand_turns_do_not_enter_endpoint_admission(self):
        selected, actual = self.compare()
        self.assertEqual(len(selected), 2)
        self.assertFalse(actual[0].identity_only)

    def test_conflict_far_outside_range_still_forces_identity_only(self):
        selected, actual = self.compare(conflict=((THREAD, turn(999)),))
        self.assertEqual(len(selected), 3)
        self.assertTrue(actual[0].identity_only)

    def test_equal_clock_peer_outside_selected_endpoint_set_is_not_hidden(self):
        selected, actual = self.compare(extra=(TurnObservation(THREAD, turn(2000), self.reference.turns[1].started_at),))
        self.assertEqual(len(selected), 3)


class IntervalReplacementTests(unittest.TestCase):
    def test_pause_resume_preserves_old_ranges_until_replacement_is_consumed(self):
        fixture = input_tests.ReconciliationInputTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "r1", direct=True)
        rows += fixture.operation("pause", fixture.acknowledgement(A, 1, "paused"), "r2", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        fixture.resolve((A,))
        def profile(core):
            from task_governance_tool.usage_attribution import Transition
            return {"transitions": [{"order": row["ordinal"], "value": asdict(Transition(**{field: row[field] for field in Transition.__dataclass_fields__}))}
                    for row in core.execute("SELECT rowid AS ordinal,* FROM task_owner_transitions ORDER BY rowid")], "reviews": [], "endpoints": []}
        with fixture.reader() as core, fixture.repository.connection(write=True) as connection:
            changed = reconcile_intervals(fixture.repository, core, connection, A, profile(core))
            self.assertEqual(len(changed), 1)
            saved = connection.execute("SELECT after_document FROM usage_reconcile_interval_work").fetchone()[0]
            connection.execute("DELETE FROM usage_reconcile_interval_work")
        with fixture.reader() as core, fixture.repository.connection(write=True) as connection:
            self.assertEqual(reconcile_intervals(fixture.repository, core, connection, A, profile(core)), ())
        # Losing the numerical pause binding must replace its closed endpoint,
        # retaining the previously admitted range for removal/reclassification.
        with fixture.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_operation_turns WHERE status='paused'")
        fixture.resolve((A,))
        with fixture.reader() as core, fixture.repository.connection(write=True) as connection:
            reconcile_intervals(fixture.repository, core, connection, A, profile(core))
            row = connection.execute("SELECT before_document,after_document FROM usage_reconcile_interval_work").fetchone()
            self.assertEqual(row[0], saved)
            self.assertIsNone(json.loads(row[1])["interval"]["end_turn"])


if __name__ == "__main__":
    unittest.main()
