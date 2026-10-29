"""Differential classification tests across the selected repository phases."""

from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

from tests import test_usage_reconciliation_inputs as fixture_support
from tests.test_usage_response_repository import A, B
from tests.test_usage_collection import THREAD, usage
from task_governance_tool.usage_attribution import Transition
from task_governance_tool.usage_response_repository import component_identity
from task_governance_tool.usage_reconciliation_intervals import reconcile_intervals
from task_governance_tool import usage_reconciliation_uncertainty as uncertainty
from task_governance_tool import usage_reconciliation_overlays as overlays
from task_governance_tool import usage_reconciliation_membership as membership
from task_governance_tool import usage_reconciliation_pending as pending
from task_governance_tool.usage_reconciliation_ranges import expand_range_page


def previous_classifier():
    # Exact file from d234e22f87e61c208db972fb9bff2f89ce304d5c. Keep this
    # independent of the new shared kernel; no Git executable is needed in CI.
    path = Path(__file__).parent / "fixtures/usage_response_attribution_before_reconciliation.py"
    spec = importlib.util.spec_from_file_location("usage_response_attribution_before_reconciliation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.project_responses


def classify(fixture, *, only_dirty=False):
    """Exercise actual selected phases; compare with a frozen old classifier."""
    from task_governance_tool.usage_reconciliation_changes import consume_page
    for _ in range(1000):
        with fixture.repository.connection(write=True) as connection:
            if consume_page(fixture.repository, connection)["selected"] == 0:
                break
    else:
        raise AssertionError("change queue did not converge")
    fixture.resolve()
    with fixture.reader() as core, fixture.repository.connection(write=True) as connection:
        from task_governance_tool.usage_reconciliation_changes import queue_sources
        queue_sources(fixture.repository, connection, [row[0] for row in connection.execute("SELECT source_id FROM usage_sources")])
        rows = tuple(core.execute("SELECT rowid AS source_order,* FROM task_owner_transitions ORDER BY rowid"))
        for task in (A, B):
            profile = {"transitions": [{"order": row["source_order"], "value": asdict(Transition(**{field: row[field] for field in Transition.__dataclass_fields__}))}
                       for row in rows if row["task_id"] == task], "reviews": [], "endpoints": []}
            reconcile_intervals(fixture.repository, core, connection, task, profile)
        turns = list(map(tuple, connection.execute(
            "SELECT thread_id,turn_id FROM usage_reconcile_input_turn_work ORDER BY rowid" if only_dirty else
            "SELECT thread_id,turn_id FROM usage_turns ORDER BY thread_id,started_at,turn_id")))
    from task_governance_tool.usage_reconciliation_inputs import stage_input_turn
    for thread, turn in turns:
        with fixture.reader() as core, fixture.repository.connection(write=True) as connection:
            stage_input_turn(fixture.repository, core, connection, thread, turn)
    fixture.replay()
    def drain(function):
        for _ in range(1000):
            with fixture.repository.connection(write=True) as connection:
                result = function(connection)
            if result is None:
                return
        raise AssertionError("phase did not converge")
    drain(membership.apply_base_one)
    drain(uncertainty.locate_one)
    drain(pending.source_one)
    drain(uncertainty.rebuild_one)
    drain(overlays.interval_metadata_one)
    drain(expand_range_page)
    with fixture.repository.connection(write=True) as connection:
        for _ in range(1000):
            row = connection.execute("SELECT thread_id,turn_id FROM usage_reconcile_overlay_turn_work ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                break
            uncertainty.apply_turn(connection, *row)
            overlays.apply_turn(connection, *row)
        else:
            raise AssertionError("overlay dependencies did not converge")
        for row in tuple(connection.execute("SELECT provider,response_id FROM usage_reconcile_response_work")):
            membership.reconcile_response(connection, *row)
        for row in tuple(connection.execute("SELECT thread_id FROM usage_reconcile_diagnostic_work")):
            membership.reconcile_thread_diagnostics(connection, row[0])
        for row in tuple(connection.execute("SELECT execution_id FROM usage_reconcile_execution_work")):
            membership.reconcile_execution(connection, row[0])
        ids = {row[0] for row in connection.execute("SELECT DISTINCT component_id FROM usage_reconcile_response_result WHERE component_id IS NOT NULL "
            "UNION SELECT component_id FROM usage_reconcile_empty_components")}
        return {"components": sorted((membership.component_geometry(connection, identity) for identity in ids), key=component_identity),
                "unknown_response_keys": sorted(tuple(row) for row in connection.execute("SELECT provider,response_id FROM usage_reconcile_response_result WHERE unknown=1")),
                "unassigned_response_keys": sorted(tuple(row) for row in connection.execute("SELECT provider,response_id FROM usage_reconcile_response_result WHERE unknown=0 AND component_id IS NULL")),
                "unresolved_operations": int(connection.execute("SELECT 1 FROM usage_reconcile_unresolved LIMIT 1").fetchone() is not None)}


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_support.ReconciliationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.maxDiff = None

    def assert_same(self, *, only_dirty=False):
        actual = classify(self.fixture, only_dirty=only_dirty)
        with self.fixture.reader() as core, mock.patch(
                "task_governance_tool.usage_response_repository.project_responses", previous_classifier()):
            expected = self.fixture.repository.attribution(core, geometry_only=True)
        expected["components"] = sorted(expected["components"], key=component_identity)
        self.assertEqual(actual, expected)
        with self.fixture.reader() as core:
            full = self.fixture.repository.attribution(core, geometry_only=True)
        full["components"] = sorted(full["components"], key=component_identity)
        self.assertEqual(full, expected)
        from task_governance_tool.usage_reconciliation_totals import totals
        with self.fixture.repository.connection() as connection:
            self.assertEqual(totals(connection), {kind: self.fixture.repository._totals(connection, expected[field])
                for kind, field in (("unknown", "unknown_response_keys"), ("unassigned", "unassigned_response_keys"))})

    def test_later_preparation_removes_earlier_turn_owner_of_conflicting_key(self):
        from tests.test_usage_collection import event
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        compact = {"tasks": [], "total_matching": 0, "returned_count": 0, "limit": 20, "truncated": False}
        check = {"ok": True, "command": "task.context", "project_id": fixture.repository.basis[0],
                 "data": {"selection": "none", "current": compact, "next": compact, "selected": None}}
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "a", direct=True)
        rows += fixture.operation("check", check, "r", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        from task_governance_tool.usage_adapter import SourceInput
        from task_governance_tool.usage_collection import register_source
        from task_governance_tool.session_identity import CallerIdentity
        second = SourceInput(THREAD, fixture.logs / "second.jsonl", fixture.logs, fixture.root)
        register_source(fixture.repository, second, CallerIdentity(THREAD))
        rows = [event("event_msg", type="task_started", turn_id=turn(2), started_at=2),
                 event("turn_context", turn_id=turn(2), model="fixture-model")]
        rows += fixture.operation("done", fixture.acknowledgement(A, 1, "done"), "done", number=2, direct=True)
        rows += [usage("r", turn=turn(2))]
        rows += fixture.operation("next", fixture.acknowledgement(B, 2, "in_progress"), "b", number=2, direct=True)
        fixture.write(fixture.records(*rows), path=second.path)
        fixture.repository.collect_source(second)
        self.assert_same()
        # Losing and restoring the admitted check removes/restores the global
        # exclusion even though no numerical observation changes.
        with fixture.repository.connection(write=True) as connection:
            saved = tuple(connection.execute("SELECT * FROM usage_response_boundaries WHERE json_extract(document,'$.call_id')='check'").fetchone())
            connection.execute("DELETE FROM usage_response_boundaries WHERE observation_id=?", (saved[2],))
        self.assert_same()
        with fixture.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_response_boundaries VALUES (?,?,?,?)", saved)
        self.assert_same()
        # A later occurrence can add the removed owner again. Dirty-work order
        # and a source's digest must not decide cross-session encounter order.
        other = "22222222-2222-4222-8222-222222222222"
        third = SourceInput(other, fixture.logs / "third.jsonl", fixture.logs, fixture.root)
        register_source(fixture.repository, third, CallerIdentity(other))
        fixture.write(fixture.records(
            event("event_msg", type="task_started", turn_id=turn(3), started_at=3),
            event("turn_context", turn_id=turn(3), model="fixture-model"),
            usage("a", turn=turn(3), thread=other), usage("r", turn=turn(3), thread=other), thread=other), path=third.path)
        fixture.repository.collect_source(third)
        self.assert_same()
        def owners():
            with fixture.repository.connection() as connection:
                return {row[0] for row in connection.execute("SELECT execution_id FROM usage_reconcile_membership "
                    "WHERE kind='base' AND provider='openai' AND response_id='r'")}
        self.assertEqual(owners(), {"tg_execution_0000000000000001", "tg_execution_0000000000000002"})
        with fixture.repository.connection(write=True) as connection:
            connection.execute("UPDATE usage_response_positions SET rowid=-rowid WHERE source_id=?", (third.source_id,))
        self.assert_same()
        self.assertEqual(owners(), {"tg_execution_0000000000000002"})
        with fixture.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=?", (third.source_id,))
        self.assert_same()

    def ordinary(self):
        fixture = self.fixture
        rows = []
        for task, number in ((A, 1), (B, 2)):
            rows += fixture.operation(f"start{number}", fixture.acknowledgement(task, number, "in_progress"), f"r{number}a", direct=True)
            rows += fixture.operation(f"done{number}", fixture.acknowledgement(task, number, "done"), f"r{number}b", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()

    def add_source(self, name, thread, number, rows):
        from tests.test_usage_collection import event
        from tests.test_usage_attribution import turn
        from task_governance_tool.usage_adapter import SourceInput
        from task_governance_tool.usage_collection import register_source
        from task_governance_tool.session_identity import CallerIdentity
        fixture = self.fixture
        source = SourceInput(thread, fixture.logs / name, fixture.logs, fixture.root)
        register_source(fixture.repository, source, CallerIdentity(thread))
        for row in rows:
            if row["type"] == "token_usage_record":
                row["payload"]["thread_id"] = thread
        fixture.write(fixture.records(
            event("event_msg", type="task_started", turn_id=turn(number), started_at=number),
            event("turn_context", turn_id=turn(number), model="fixture-model"), *rows, thread=thread), path=source.path)
        fixture.repository.collect_source(source)
        return source

    def test_missing_stop_uncertainty_keeps_earlier_repeated_occurrence(self):
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "a", direct=True)
        rows += [usage("r", turn=turn(1))]
        fixture.acknowledgement(A, 1, "paused")
        rows += fixture.operation("next", fixture.acknowledgement(B, 2, "in_progress"), "b", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        self.add_source("repeat.jsonl", THREAD, 2, [usage("r", turn=turn(2))])
        self.assert_same()
        with fixture.repository.connection(write=True) as connection:
            saved = tuple(connection.execute("SELECT * FROM usage_response_positions WHERE source_id=? AND response_id='r'", (fixture.source.source_id,)).fetchone())
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=? AND response_id='r'", (fixture.source.source_id,))
        self.assert_same()
        with fixture.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_response_positions VALUES (?,?,?,?,?,?,?)", saved)
        self.assert_same()

    def test_pending_reset_excludes_later_session_owners(self):
        from contextlib import closing
        import sqlite3
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "a", direct=True)
        rows += fixture.operation("pending", {"ok": False}, "x", direct=True)[:-1]
        rows += fixture.operation("review", fixture.acknowledgement(A, 1, "review_pending"), "r", direct=True)
        rows += [usage("tail", turn=turn(1))]
        fixture.write_turn(rows)
        fixture.ingest()
        other = "22222222-2222-4222-8222-222222222222"
        rows = fixture.operation("start-b", fixture.acknowledgement(B, 2, "in_progress"), "b", number=2, direct=True)
        rows += [usage("x", turn=turn(2), thread=other)]
        with closing(sqlite3.connect(fixture.core_path)) as core:
            core.execute("UPDATE task_owner_transitions SET actor_session_id=? WHERE task_id=?", (other, B))
            core.commit()
        source = self.add_source("other.jsonl", other, 2, rows)
        self.assert_same()
        # Reordering encounter phases leaves final membership {A,B} equal,
        # but changes the pending origin's prefix and therefore its reset.
        for _ in range(2):
            with fixture.repository.connection(write=True) as connection:
                connection.execute("UPDATE usage_response_positions SET rowid=-rowid WHERE source_id=?", (source.source_id,))
            self.assert_same()

    def test_complete_boundaries_and_unchanged_reentry_match(self):
        self.ordinary()
        self.assert_same()
        self.assert_same()

    def test_unavailable_operation_boundary_preserves_partial_coverage(self):
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        call, _, result = fixture.operation("unlocated", fixture.acknowledgement(A, 1, "in_progress"), "absent")
        fixture.write_turn([call, result, usage("after", turn=turn(1))])
        fixture.ingest()
        with fixture.repository.connection() as connection:
            boundary = json.loads(connection.execute("SELECT document FROM usage_response_boundaries").fetchone()[0])
            self.assertIsNone(boundary["response_id"])
            self.assertEqual(boundary["method"], "unavailable")
        self.assert_same()
        self.assert_same()

    def test_later_occurrence_uncertainty_recomputes_earlier_fallback_after_conflict_recovery(self):
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        rows = fixture.operation("start-a", fixture.acknowledgement(A, 1, "in_progress"), "a", direct=True)
        rows += fixture.operation("done-a", fixture.acknowledgement(A, 1, "done"), "d", direct=True)
        rows += fixture.operation("start-b", fixture.acknowledgement(B, 2, "in_progress"), "b", direct=True)
        rows += [usage("r", turn=turn(1))]
        fixture.write_turn(rows)
        fixture.ingest()
        # Retain admitted turn-only history while its response proof is absent.
        with fixture.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_boundaries WHERE json_extract(document,'$.task_id')=?", (A,))
        self.add_source("repeat.jsonl", THREAD, 2,
            fixture.operation("pending", {"ok": False}, "r", number=2, direct=True)[:-1])
        # Controlled conflict recovery, as supported by existing membership
        # fixtures; this is not a claim about automatic conflict clearing.
        with fixture.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_conflicts")
        self.assert_same()
        self.assert_same()

    def test_missing_start_and_late_reappearance_match_without_losing_outside_owner(self):
        self.ordinary()
        self.assert_same()
        with self.fixture.repository.connection(write=True) as connection:
            saved = tuple(connection.execute("SELECT * FROM usage_response_boundaries WHERE json_extract(document,'$.call_id')='start1'").fetchone())
            connection.execute("DELETE FROM usage_response_boundaries WHERE observation_id=?", (saved[2],))
        self.assert_same()
        with self.fixture.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_response_boundaries VALUES (?,?,?,?)", saved)
        self.assert_same()

    def test_delayed_start_replaces_fallback_owner_without_position_or_uncertainty_change(self):
        from tests.test_usage_attribution import turn
        from tests.test_usage_collection import event
        from task_governance_tool import usage_reconciliation_inputs as inputs
        fixture = self.fixture
        rows = fixture.operation("start-a", fixture.acknowledgement(A, 1, "in_progress"), "a", direct=True)
        rows += [event("event_msg", type="task_started", turn_id=turn(2), started_at=2),
                 event("turn_context", turn_id=turn(2), model="fixture-model"), usage("r", turn=turn(2))]
        fixture.write_turn(rows)
        fixture.ingest()
        compact = {"tasks": [], "total_matching": 0, "returned_count": 0, "limit": 20, "truncated": False}
        check = {"ok": True, "command": "task.context", "project_id": fixture.repository.basis[0],
                 "data": {"selection": "none", "current": compact, "next": compact, "selected": None}}
        rows = fixture.operation("done-a", fixture.acknowledgement(A, 1, "done"), "d", number=4, direct=True)
        rows += fixture.operation("check", check, "c", number=4, direct=True)
        rows += [usage("r", turn=turn(4))]
        delayed = fixture.operation("start-b", {"ok": False}, "b", number=4, direct=True)
        source = self.add_source("late.jsonl", THREAD, 4, rows + delayed[:-1])
        with fixture.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_turn_conflicts VALUES (?,?)", (THREAD, turn(2)))
            connection.execute("DELETE FROM usage_conflicts")
        self.assert_same()
        result = fixture.operation("start-b", fixture.acknowledgement(B, 2, "in_progress"), "b", number=4, direct=True)[-1]
        with source.path.open("ab") as stream:
            stream.write(json.dumps(result).encode() + b"\n")
        fixture.repository.collect_source(source)
        fixture.repository = type(fixture.repository)(fixture.repository.path, *fixture.repository.basis)
        with mock.patch.object(inputs, "stage_input_turn", wraps=inputs.stage_input_turn) as staged:
            self.assert_same(only_dirty=True)
        self.assertNotIn((THREAD, turn(2)), [call.args[3:5] for call in staged.call_args_list])

    def test_missing_stop_does_not_make_the_later_independent_task_unknown(self):
        self.ordinary()
        with self.fixture.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_boundaries WHERE json_extract(document,'$.call_id')='done1'")
        self.assert_same()

    def test_reversed_boundaries_match_full_uncertainty_and_partial_fallback(self):
        fixture = self.fixture
        start = fixture.acknowledgement(A, 1, "in_progress")
        finish = fixture.acknowledgement(A, 1, "done")
        rows = fixture.operation("finish", finish, "r1", direct=True)
        rows += fixture.operation("start", start, "r2", direct=True)
        rows += fixture.operation("start2", fixture.acknowledgement(B, 2, "in_progress"), "r3", direct=True)
        rows += fixture.operation("done2", fixture.acknowledgement(B, 2, "done"), "r4", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        self.assert_same()

    def test_lost_pause_filters_only_its_base_owners(self):
        fixture = self.fixture
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "r1", direct=True)
        rows += fixture.operation("pause", fixture.acknowledgement(A, 1, "paused"), "r2", direct=True)
        rows += fixture.operation("start2", fixture.acknowledgement(B, 2, "in_progress"), "r3", direct=True)
        rows += fixture.operation("done2", fixture.acknowledgement(B, 2, "done"), "r4", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        with fixture.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_boundaries WHERE json_extract(document,'$.call_id')='pause'")
        self.assert_same()

    def test_cross_actor_release_fences_former_stream_until_new_local_start(self):
        from contextlib import closing
        import sqlite3
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "r1", direct=True)
        rows += [usage("r2", turn=turn(1))]
        fixture.acknowledgement(A, 1, "paused")
        with closing(sqlite3.connect(fixture.core_path)) as core:
            core.execute("UPDATE task_owner_transitions SET actor_session_id=? WHERE current_status='paused'", ("22222222-2222-4222-8222-222222222222",))
            core.commit()
        rows += fixture.operation("start2", fixture.acknowledgement(B, 2, "in_progress"), "r3", direct=True)
        rows += fixture.operation("done2", fixture.acknowledgement(B, 2, "done"), "r4", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        self.assert_same()
        with closing(sqlite3.connect(fixture.core_path)) as core:
            core.execute("UPDATE task_owner_transitions SET actor_session_id=? WHERE current_status='paused'", (THREAD,))
            core.commit()
        self.assert_same()

    def test_pending_call_and_late_terminal_result_match(self):
        fixture = self.fixture
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "r1", direct=True)
        fixture.write_turn(rows[:-1])
        fixture.ingest(max_records=1)
        self.assert_same()
        fixture.append(rows[-1])
        fixture.ingest(max_records=1)
        self.assert_same()

    def test_pending_continuation_stops_at_new_start_with_and_without_explicit_response(self):
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "r1", direct=True)
        rows += fixture.operation("pending", {"ok": False}, "r2", direct=False)[:-1]
        rows += [usage("r3", turn=turn(1))]
        rows += fixture.operation("start2", fixture.acknowledgement(B, 2, "in_progress"), "r4", direct=True)
        rows += fixture.operation("done2", fixture.acknowledgement(B, 2, "done"), "r5", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        self.assert_same()
        fixture.append(fixture.operation("pending", {"ok": False}, "r2", direct=False)[-1])
        fixture.ingest()
        self.assert_same()


    def test_conflicting_response_in_another_source_turn_matches_full_event_order(self):
        from tests.test_usage_collection import event
        from tests.test_usage_attribution import turn
        from task_governance_tool.usage_adapter import SourceInput
        from task_governance_tool.usage_collection import register_source
        from task_governance_tool.session_identity import CallerIdentity
        fixture = self.fixture
        rows = fixture.operation("start", fixture.acknowledgement(A, 1, "in_progress"), "r1", direct=True)
        rows += fixture.operation("pause", fixture.acknowledgement(A, 1, "paused"), "q1", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        self.assert_same()
        second = SourceInput(THREAD, fixture.logs / "second.jsonl", fixture.logs, fixture.root)
        register_source(fixture.repository, second, CallerIdentity(THREAD))
        fixture.write(fixture.records(event("event_msg", type="task_started", turn_id=turn(2), started_at=2),
            event("turn_context", turn_id=turn(2), model="fixture-model"), usage("r1", turn=turn(2)), usage("s2", turn=turn(2))), path=second.path)
        fixture.repository.collect_source(second)
        self.assert_same()
        with fixture.repository.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_response_positions WHERE source_id=?", (second.source_id,))
        self.assert_same()


    def test_late_located_predecessor_moves_reversal_fence_start(self):
        fixture = self.fixture
        p = fixture.acknowledgement(A, 1, "in_progress")
        x = fixture.acknowledgement(A, 1, "review_pending")
        left = fixture.acknowledgement(A, 1, "done")
        right = fixture.acknowledgement(B, 2, "in_progress")
        done = fixture.acknowledgement(B, 2, "done")
        rows = fixture.operation("p", p, "r1", direct=True) + fixture.operation("x", x, "r2", direct=True)
        rows += fixture.operation("right", right, "r3", direct=True) + fixture.operation("left", left, "r4", direct=True)
        rows += fixture.operation("done", done, "r5", direct=True)
        fixture.write_turn(rows)
        fixture.ingest()
        with fixture.repository.connection(write=True) as connection:
            saved = tuple(connection.execute("SELECT * FROM usage_response_boundaries WHERE json_extract(document,'$.call_id')='x'").fetchone())
            connection.execute("DELETE FROM usage_response_boundaries WHERE observation_id=?", (saved[2],))
        self.assert_same()
        with fixture.repository.connection(write=True) as connection:
            connection.execute("INSERT INTO usage_response_boundaries VALUES (?,?,?,?)", saved)
        self.assert_same()


    def test_legacy_last_binding_uses_original_insertion_order(self):
        from contextlib import closing
        import sqlite3
        from tests.test_usage_collection import event
        from tests.test_usage_attribution import turn
        fixture = self.fixture
        acknowledgement = fixture.acknowledgement(A, 1, "in_progress")
        original = acknowledgement["data"]["event"]["task_event_id"]
        later = "tg_event_0000000000000000"
        with closing(sqlite3.connect(fixture.core_path)) as core:
            core.execute("INSERT INTO task_events VALUES (?,?,?,?)", (later, fixture.repository.basis[0], A, "1"))
            core.commit()
        fixture.write_turn([usage("r1", turn=turn(1)), event("event_msg", type="task_started", turn_id=turn(2), started_at=2),
            event("turn_context", turn_id=turn(2), model="fixture-model"), usage("r2", turn=turn(2))])
        fixture.ingest()
        with fixture.repository.connection(write=True) as connection:
            for identity, number in ((original, 1), (later, 2)):
                connection.execute("INSERT INTO usage_operation_turns VALUES (?,?,?,?,?,?,?)",
                    (identity, THREAD, turn(number), fixture.repository.basis[0], A, 1, "in_progress"))
        self.assert_same()


if __name__ == "__main__":
    unittest.main()
