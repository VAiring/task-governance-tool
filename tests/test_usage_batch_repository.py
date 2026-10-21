"""Synthetic indexed batch collection, continuation and replay regressions."""

import json
import unittest
from unittest import mock

from tests import test_usage_response_repository as fixtures
from tests.test_usage_collection import THREAD, event, usage
from tests.test_usage_attribution import turn
from tests.test_usage_batch_adapter import nested as nest_transport
from task_governance_tool.usage_adapter import read_batch
from task_governance_tool.usage_response_adapter import ResponseBoundary, ResponsePosition
from task_governance_tool.usage_response_repository import UsageResponseRepository
from task_governance_tool.usage_values import UsageError


A, B = fixtures.A, fixtures.B
PRIVATE = "PRIVATE_BATCH_CANARY"


def batch_input(*calls):
    return ("const results=await Promise.allSettled([" + ",".join(calls)
            + "]);for(let i=0;i<results.length;i++){"
            + 'store("fixture"+i,results[i]);text({i,result:results[i]});}')


def settled(index, value=None, *, rejected=False):
    result = ({"status": "rejected", "reason": PRIVATE} if rejected else
              {"status": "fulfilled", "value": value})
    return {"type": "input_text", "text": json.dumps({"i": index, "result": result})}


def call(identity, arguments, response_id, *, number=1):
    return event("response_item", type="custom_tool_call", name="exec", call_id=identity,
                 input=arguments, internal_chat_message_metadata_passthrough={
                     "turn_id": turn(number), "response_id": response_id})


def output(identity, value, *, number=1):
    return event("response_item", type="custom_tool_call_output", call_id=identity,
                 output=value, internal_chat_message_metadata_passthrough={"turn_id": turn(number)})


def poll(handle):
    return "tools.write_stdin({session_id:" + str(handle) + ',chars:"",yield_time_ms:1000})'


def fresh():
    return 'tools.exec_command({cmd:"' + PRIVATE + '",max_output_tokens:100})'


class BatchResponseRepositoryTests(unittest.TestCase):
    # Borrow only setup/fixture helpers; do not inherit another test suite.
    setUp = fixtures.ResponseRepositoryTests.setUp
    records = fixtures.ResponseRepositoryTests.records
    write = fixtures.ResponseRepositoryTests.write
    append = fixtures.ResponseRepositoryTests.append
    reader = fixtures.ResponseRepositoryTests.reader
    acknowledgement = fixtures.ResponseRepositoryTests.acknowledgement
    operation = fixtures.ResponseRepositoryTests.operation
    write_turn = fixtures.ResponseRepositoryTests.write_turn
    read = fixtures.ResponseRepositoryTests.read

    def restart(self):
        self.repository = UsageResponseRepository(self.repository.path, *self.repository.basis)

    def ingest(self, *, sliced=False):
        # A finite fixture bound also detects replay that never makes progress.
        limit = len(self.source.path.read_bytes().splitlines()) * 4 + 4
        for _ in range(limit):
            if self.repository.cursor(self.source.source_id, THREAD).offset >= self.source.path.stat().st_size:
                return
            self.repository.collect_source(self.source, max_records=1 if sliced else 10000)
            if sliced:
                self.restart()
        self.fail("bounded fixture did not finish collection")

    def running(self, identity, handle, response_id, *, number=1):
        rows = self.operation(identity, {"session_id": handle}, response_id, number=number, direct=True)
        rows[0]["payload"]["input"] = 'text(await ' + fresh() + ');'
        return rows

    def batch(self, identity, calls, response_id, results, *, number=1):
        return [call(identity, batch_input(*calls), response_id, number=number),
                usage(response_id, turn=turn(number)), output(identity, results, number=number)]

    def write_observed_turn(self, rows):
        data = self.records(event("event_msg", type="task_started", turn_id=turn(1), started_at=1),
                            event("turn_context", turn_id=turn(1), model="fixture-model"), *rows)
        data[0]["payload"]["cli_version"] = "0.163.0-alpha.10.2"
        self.write(data)

    def old_contexts(self, version):
        """Install an exact old continuation fixture, not merely its prefix."""
        with self.repository.connection(write=True) as connection:
            for saved in connection.execute("SELECT source_id,lane,document FROM usage_response_context").fetchall():
                current = json.loads(saved["document"])
                def old_frame(frame):
                    if frame is None:
                        return None
                    return {key: value for key, value in frame.items()
                            if not (version < 3 and key == "bracket_closed") and not (version == 1 and key == "slot")}
                old = {**current, "version": version,
                       "calls": {key: old_frame(frame) for key, frame in current["calls"].items()},
                       "handles": {key: old_frame(frame) for key, frame in current["handles"].items()},
                       "overflow": old_frame(current["overflow"])}
                if version == 1:
                    self.assertFalse(old.pop("batches"))
                else:
                    old["batches"] = {key: [old_frame(frame) for frame in frames]
                                      for key, frames in current["batches"].items()}
                connection.execute("UPDATE usage_response_context SET document=? WHERE source_id=? AND lane=?",
                                   (json.dumps(old), saved["source_id"], saved["lane"]))

    def assert_relations_match_full(self):
        full = read_batch(self.source, include_attribution=True,
                          attribution_project_id=self.repository.basis[0], include_response_relations=True)
        first_positions = {}
        for item in full.attribution:
            if isinstance(item, ResponsePosition):
                first_positions.setdefault((item.provider, item.response_id), item)
        with self.repository.connection() as connection:
            positions, boundaries = self.repository._relations(connection)
            self.assertEqual({item for _, _, item in positions},
                             set(first_positions.values()))
            self.assertEqual({item for _, _, item in boundaries},
                             {item for item in full.attribution if isinstance(item, ResponseBoundary)})
        return [item for item in full.attribution if isinstance(item, ResponseBoundary)]

    def assert_private_not_stored(self):
        with self.repository.connection() as connection:
            self.assertNotIn(PRIVATE, "\n".join(connection.iterdump()))
        self.assertNotIn(PRIVATE.encode(), self.repository.path.read_bytes())

    def projection(self, *, include_b=True):
        self.repository.refresh(self.reader)
        index = self.read(audit=True)
        return self.read(A), self.read(B) if include_b else None, index["unattributed"], index["snapshots"]

    def test_poll_and_fresh_exec_preserve_original_turn_in_full_sliced_restarted_collection(self):
        projections = []
        for sliced in (False, True):
            with self.subTest(sliced=sliced):
                self.setUp()
                start = self.acknowledgement(A, 1, "in_progress")
                pause = self.acknowledgement(A, 1, "paused")
                next_start = self.acknowledgement(B, 2, "in_progress")
                done = self.acknowledgement(B, 2, "done", cycle="B")
                rows = self.operation("start", start, "r0", direct=True)
                rows += self.running("pause-origin", 7, "r1")
                rows += [usage("r2", turn=turn(1)),
                         event("event_msg", type="task_started", turn_id=turn(2), started_at=2000),
                         event("turn_context", turn_id=turn(2), model="fixture-model")]
                # Reversed presentation order must not swap the call slots.
                rows += self.batch("mixed", [poll(7), fresh()], "r3", [
                    settled(1, {"exit_code": 0, "output": json.dumps(next_start)}),
                    settled(0, {"exit_code": 0, "output":
                        "Failed to find real location of C:\\" + PRIVATE + "\\python.exe\n" + json.dumps(pause)})], number=2)
                rows += self.operation("done", done, "r4", number=2, direct=True)
                self.write_turn(rows)
                self.ingest(sliced=sliced)
                found = self.assert_relations_match_full()
                paused = next(item for item in found if item.status == "paused")
                self.assertEqual((paused.response_id, paused.turn_id, paused.call_id),
                                 ("r1", turn(1), "pause-origin"))
                b_start = next(item for item in found if item.task_id == B and item.status == "in_progress")
                self.assertEqual((b_start.response_id, b_start.turn_id), ("r3", turn(2)))
                projection = self.projection()
                self.assertEqual(projection[0]["total"]["response_count"], 2)
                self.assertEqual(projection[1]["total"]["response_count"], 2)
                self.assertEqual(projection[2]["unassigned"]["response_count"], 1)
                self.assertEqual(projection[2]["unknown"]["response_count"], 0)
                self.assert_private_not_stored()
                projections.append(projection)
        self.assertEqual(projections[0], projections[1])

    def test_multiple_polls_reversed_results_keep_independent_origins_and_deduplicate(self):
        start = self.acknowledgement(A, 1, "in_progress")
        pause = self.acknowledgement(A, 1, "paused")
        next_start = self.acknowledgement(B, 2, "in_progress")
        rows = self.operation("start", start, "r0", direct=True)
        rows += self.running("pause-origin", 7, "r1")
        rows += self.running("start-origin", 8, "r2")
        results = [settled(1, {"exit_code": 0, "output": json.dumps(next_start)}),
                   settled(0, {"exit_code": 0, "output": json.dumps(pause)})]
        rows += self.batch("two-polls", [poll(7), poll(8)], "r3", results)
        rows += [output("two-polls", results), usage("r3", turn=turn(1))]
        self.write_turn(rows)
        self.ingest(sliced=True)
        found = self.assert_relations_match_full()
        self.assertEqual({(item.task_id, item.status, item.response_id) for item in found},
                         {(A, "in_progress", "r0"), (A, "paused", "r1"), (B, "in_progress", "r2")})
        a, b, unattributed, _ = self.projection()
        self.assertEqual((a["total"]["response_count"], b["total"]["response_count"]), (2, 2))
        self.assertEqual(unattributed["unknown"]["response_count"], 0)
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 4)

    def test_missing_or_rejected_slot_stays_pending_until_its_supported_terminal_result(self):
        for rejected in (False, True):
            with self.subTest(rejected=rejected):
                self.setUp()
                rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                rows += self.running("unknown-operation", 7, "r1")
                rows += [usage("r2", turn=turn(1))]
                results = [settled(1, {"exit_code": 0, "output": ""})]
                if rejected:
                    results.append(settled(0, rejected=True))
                rows += self.batch("incomplete", [poll(7), fresh()], "r3", results)
                self.write_turn(rows)
                self.ingest(sliced=True)
                self.repository.refresh(self.reader)
                self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)
                self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 3)
                self.assert_private_not_stored()
                failure = {"ok": False, "command": "task.edit", "project_id": self.repository.basis[0], "data": {}}
                self.append(output("incomplete", [settled(0, {"exit_code": 1, "output": json.dumps(failure)})]))
                self.restart()
                self.ingest(sliced=True)
                self.assert_relations_match_full()
                self.repository.refresh(self.reader)
                self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 0)
                self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 4)

    def test_live_collision_invalidates_already_issued_batch_poll_after_restart(self):
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        rows += self.running("first", 7, "r1")
        rows += [call("issued", batch_input(poll(7)), "r3")]
        rows += self.running("second", 7, "r2")
        self.write_turn(rows)
        self.ingest(sliced=True)
        self.append(usage("r3", turn=turn(1)), output("issued", [settled(0,
                    {"exit_code": 0, "output": json.dumps(self.acknowledgement(A, 1, "paused"))})]))
        self.restart()
        self.ingest(sliced=True)
        found = self.assert_relations_match_full()
        self.assertIsNone(next(item for item in found if item.status == "paused").response_id)
        self.repository.refresh(self.reader)
        self.assertEqual(self.read(A)["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)
        self.assertEqual(self.read()["unattributed"]["unknown"]["response_count"], 3)

    def test_retired_handle_reuse_in_batch_never_becomes_a_new_origin(self):
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        rows += self.running("pause-origin", 7, "r1")
        rows += self.batch("terminal", [poll(7)], "r2", [settled(0,
            {"exit_code": 0, "output": json.dumps(self.acknowledgement(A, 1, "paused"))})])
        rows += self.batch("reuse", [poll(7)], "r3", [settled(0,
            {"exit_code": 0, "output": json.dumps(self.acknowledgement(A, 1, "in_progress"))})])
        self.write_turn(rows)
        self.ingest(sliced=True)
        found = self.assert_relations_match_full()
        resumed = [item for item in found if item.status == "in_progress" and item.call_id != "start"]
        self.assertEqual(len(resumed), 1)
        self.assertIsNone(resumed[0].response_id)
        self.repository.refresh(self.reader)
        self.assertGreater(self.read()["unattributed"]["unknown"]["response_count"], 0)

    def test_observed_version_item_completed_bracket_and_unsupported_explicit_id(self):
        cases = (("0.162.0-alpha.17.2", False, "single_bracket"),
                 ("0.163.0-alpha.10.2", False, "single_bracket"),
                 ("0.163.0-alpha.10.1", False, "unavailable"),
                 ("0.163.0-alpha.10.1", True, "explicit"))
        for version, direct, method in cases:
            projections = []
            for sliced in (False, True):
                with self.subTest(version=version, direct=direct, sliced=sliced):
                    self.setUp()
                    rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"),
                                          "r0", direct=direct)
                    # Metadata-only reproduction of the observed marker. Its
                    # presence neither invents a response nor clears a bracket.
                    rows.insert(2, event("event_msg", type="item_completed", turn_id=turn(1)))
                    data = self.records(event("event_msg", type="task_started", turn_id=turn(1), started_at=1),
                                        event("turn_context", turn_id=turn(1), model="fixture-model"), *rows)
                    data[0]["payload"]["cli_version"] = version
                    self.write(data)
                    self.ingest(sliced=sliced)
                    found = self.assert_relations_match_full()
                    self.assertEqual(len(found), 1)
                    self.assertEqual(found[0].method, method)
                    self.assertEqual(found[0].response_id, None if method == "unavailable" else "r0")
                    self.assertEqual(self.repository.summary()["models"][0]["response_count"], 1)
                    self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 120)
                    self.repository.refresh(self.reader)
                    shown, index = self.read(A), self.read()
                    self.assertEqual(shown["periods"][0]["work_units"][0]["exclusive"]["response_count"],
                                     0 if method == "unavailable" else 1)
                    self.assertEqual(index["unattributed"]["unknown"]["response_count"],
                                     1 if method == "unavailable" else 0)
                    projections.append((shown, index["unattributed"]))
            self.assertEqual(projections[0], projections[1])

    def test_first_batch_result_closes_bracket_before_supported_terminal_result(self):
        first_results = {
            "opaque": [settled(0, PRIVATE)],
            "rejected": [settled(0, rejected=True)],
            "duplicate-index": [settled(0, PRIVATE), settled(0, {"session_id": 7})],
            "missing-slot": [settled(1, {"exit_code": 0, "output": ""})],
        }
        for name, initial in first_results.items():
            for original in (False, True):
                projections = []
                for sliced in (False, True):
                    with self.subTest(first=name, original=original, sliced=sliced):
                        self.setUp()
                        start = self.acknowledgement(A, 1, "in_progress")
                        rows = [call("pending", batch_input(fresh(), fresh()), None)]
                        if original:
                            rows.append(usage("original", turn=turn(1)))
                        rows.append(output("pending", initial))
                        self.write_observed_turn(rows)
                        self.ingest(sliced=sliced)
                        self.restart()
                        self.append(usage("later", turn=turn(1)), output("pending", [settled(0, start),
                            settled(1, {"exit_code": 0, "output": ""})]))
                        self.ingest(sliced=sliced)
                        found = self.assert_relations_match_full()
                        self.assertEqual(len(found), 1)
                        self.assertEqual((found[0].call_id, found[0].response_id, found[0].method),
                            ("pending", "original" if original else None,
                             "single_bracket" if original else "unavailable"))
                        projection = self.projection(include_b=False)
                        exclusive = projection[0]["periods"][0]["work_units"][0]["exclusive"]
                        self.assertEqual(exclusive["response_count"], 2 if original else 0)
                        self.assertEqual(projection[2]["unknown"]["response_count"], 0 if original else 1)
                        self.assertEqual(self.repository.summary()["models"][0]["total_tokens"],
                                         240 if original else 120)
                        self.assert_private_not_stored()
                        projections.append(projection)
                self.assertEqual(projections[0], projections[1])

    def test_first_opaque_result_freezes_origin_through_later_handle_and_poll(self):
        for batch_origin in (False, True):
            for original in (False, True):
                projections = []
                for sliced in (False, True):
                    with self.subTest(batch=batch_origin, original=original, sliced=sliced):
                        self.setUp()
                        start = self.acknowledgement(A, 1, "in_progress")
                        source = batch_input(fresh()) if batch_origin else 'text(await ' + fresh() + ');'
                        wrap = (lambda value: [settled(0, value)]) if batch_origin else (lambda value: value)
                        rows = [call("origin", source, None)]
                        if original:
                            rows.append(usage("original", turn=turn(1)))
                        rows.append(output("origin", wrap(PRIVATE)))
                        self.write_observed_turn(rows)
                        self.ingest(sliced=sliced)
                        self.restart()
                        self.append(usage("later", turn=turn(1)), output("origin", wrap({"session_id": 7})))
                        self.ingest(sliced=sliced)
                        self.restart()
                        self.append(*self.batch("poll", [poll(7)], "poll-response", [settled(0, start)]))
                        self.ingest(sliced=sliced)
                        found = self.assert_relations_match_full()
                        self.assertEqual([(item.call_id, item.response_id) for item in found],
                                         [("origin", "original" if original else None)])
                        projection = self.projection(include_b=False)
                        exclusive = projection[0]["periods"][0]["work_units"][0]["exclusive"]
                        self.assertEqual(exclusive["response_count"], 3 if original else 0)
                        self.assertEqual(projection[2]["unknown"]["response_count"], 0 if original else 2)
                        self.assertEqual(self.repository.summary()["models"][0]["total_tokens"],
                                         360 if original else 240)
                        self.assert_private_not_stored()
                        projections.append(projection)
                self.assertEqual(projections[0], projections[1])

    def test_unsupported_parenthesized_computed_poll_with_flattened_output_is_unbound(self):
        projections = []
        for sliced in (False, True):
            with self.subTest(sliced=sliced):
                self.setUp()
                start = self.acknowledgement(A, 1, "in_progress")
                self.write_observed_turn(self.running("origin", 7, "original"))
                self.ingest(sliced=sliced)
                self.restart()
                source = ('const results=await (Promise.allSettled)([tools["write_stdin"]'
                          '({session_id:7})]);text(results[0].value);')
                self.append(call("unsupported", source, "later"), usage("later", turn=turn(1)),
                            output("unsupported", start))
                self.ingest(sliced=sliced)
                found = self.assert_relations_match_full()
                self.assertEqual([(item.call_id, item.response_id) for item in found], [("unsupported", None)])
                projection = self.projection(include_b=False)
                self.assertEqual(projection[0]["periods"][0]["work_units"][0]["exclusive"]["response_count"], 0)
                self.assertEqual(projection[2]["unknown"]["response_count"], 2)
                self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 240)
                self.assert_private_not_stored()
                projections.append(projection)
        self.assertEqual(projections[0], projections[1])

    def assert_unsupported_poll_stays_unknown(self, source, *, explicit=True):
        projections = []
        for sliced in (False, True):
            with self.subTest(sliced=sliced, explicit=explicit):
                self.setUp()
                start = self.acknowledgement(A, 1, "in_progress")
                rows = self.running("origin", 7, "original")
                if not explicit:
                    rows[0]["payload"]["internal_chat_message_metadata_passthrough"].pop("response_id")
                self.write_observed_turn(rows)
                self.ingest(sliced=sliced)
                with self.repository.connection() as connection:
                    context = json.loads(connection.execute(
                        "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                origin = context["handles"]["session:7"]
                self.assertEqual((origin["call"], origin["response"]), ("origin", "original"))
                self.restart()
                self.append(call("unsupported", source, "later" if explicit else None),
                            usage("later", turn=turn(1)), output("unsupported", start))
                self.ingest(sliced=sliced)
                found = self.assert_relations_match_full()
                self.assertEqual([(item.call_id, item.response_id, item.method) for item in found],
                                 [("unsupported", None, "unavailable")])
                with self.repository.connection() as connection:
                    context = json.loads(connection.execute(
                        "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                # Rejecting unsupported syntax cannot resolve, replace or steal
                # the independently recorded original handle lifetime.
                self.assertEqual(context["handles"]["session:7"], origin)
                projection = self.projection(include_b=False)
                self.assertEqual(projection[0]["periods"][0]["work_units"][0]["exclusive"]["response_count"], 0)
                self.assertEqual(projection[2]["unknown"]["response_count"], 2)
                self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 240)
                self.assert_private_not_stored()
                projections.append(projection)
        self.assertEqual(projections[0], projections[1])

    def test_all_javascript_line_terminators_expose_unsupported_poll_after_comment(self):
        for terminator in ("\n", "\r", "\u2028", "\u2029"):
            for explicit in ((False, True) if terminator == "\r" else (True,)):
                with self.subTest(terminator=repr(terminator), explicit=explicit):
                    source = ("// " + PRIVATE + terminator +
                              'const results=await Promise.allSettled([tools["write_stdin"]'
                              '({session_id:7})]);text(results[0].value);')
                    self.assert_unsupported_poll_stays_unknown(source, explicit=explicit)

    def test_division_after_postfix_cannot_hide_flattened_poll_origin(self):
        for postfix in ("++", "--"):
            for explicit in (False, True):
                with self.subTest(postfix=postfix, explicit=explicit):
                    source = ('let n=2;let results;n' + postfix +
                              ' / (results=await Promise.allSettled([tools["write_stdin"]'
                              '({session_id:7})]),2) / 3;text(results[0].value);')
                    self.assert_unsupported_poll_stays_unknown(source, explicit=explicit)

    def test_division_after_literal_or_keyword_property_cannot_hide_poll_origin(self):
        for expression in ('"+"', '";"', "obj.return"):
            with self.subTest(expression=expression):
                source = ('const obj={return:2};let results;' + expression +
                          ' / (results=await Promise.allSettled([tools["write_stdin"]'
                          '({session_id:7})]),2) / 3;text(results[0].value);')
                self.assert_unsupported_poll_stays_unknown(source)

    def test_total_depth_budget_gates_running_handles_with_independent_peer_and_restart(self):
        for outer_count, inner_count, accepted in ((1, 4, True), (2, 4, False), (5, 7, False)):
            projections = []
            for sliced in (False, True):
                with self.subTest(outer=outer_count, inner=inner_count, sliced=sliced):
                    self.setUp()
                    rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
                    pause = self.acknowledgement(A, 1, "paused")
                    next_start = self.acknowledgement(B, 2, "in_progress")
                    running = nest_transport({"session_id": 7}, ("content",) * inner_count)
                    deep = nest_transport({"i": 0, "result": {"status": "fulfilled", "value": running}},
                                          ("output",) * outer_count)
                    results = [{"type": "input_text", "text": json.dumps(deep)}, settled(1, pause)]
                    rows += self.batch("deep-batch", [fresh(), fresh()], "r1", results)
                    self.write_turn(rows)
                    self.ingest(sliced=sliced)
                    with self.repository.connection() as connection:
                        context = json.loads(connection.execute(
                            "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                    self.assertEqual("session:7" in context["handles"], accepted)
                    self.restart()
                    self.append(*self.batch("later-poll", [poll(7)], "r2", [settled(0, next_start)]))
                    self.ingest(sliced=sliced)
                    found = self.assert_relations_match_full()
                    self.assertEqual(next(item for item in found if item.status == "paused").response_id, "r1")
                    b_start = next(item for item in found if item.task_id == B and item.status == "in_progress")
                    self.assertEqual(b_start.response_id, "r1" if accepted else None)
                    projection = self.projection()
                    self.assertEqual(self.repository.summary()["models"][0]["response_count"], 3)
                    self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 360)
                    if accepted:
                        self.assertEqual(projection[2]["unknown"]["response_count"], 0)
                        self.assertEqual(projection[1]["total"]["response_count"], 2)
                    else:
                        self.assertGreater(projection[2]["unknown"]["response_count"], 0)
                    self.assert_private_not_stored()
                    projections.append(projection)
            self.assertEqual(projections[0], projections[1])

    def test_literal_allsettled_text_preserves_single_poll_origin_across_restart(self):
        for chars in ("allSettled", "Promise.allSettled([])"):
            projections = []
            for sliced in (False, True):
                with self.subTest(chars=chars, sliced=sliced):
                    self.setUp()
                    start = self.acknowledgement(A, 1, "in_progress")
                    self.write_turn(self.running("original", 7, "r0"))
                    self.ingest(sliced=sliced)
                    self.restart()
                    source = 'text(await tools.write_stdin({session_id:7,chars:' + json.dumps(chars) + '}));'
                    self.append(call("poll", source, "r1"), usage("r1", turn=turn(1)), output("poll", start))
                    self.ingest(sliced=sliced)
                    found = self.assert_relations_match_full()
                    self.assertEqual([(item.call_id, item.response_id) for item in found], [("original", "r0")])
                    self.repository.refresh(self.reader)
                    shown, index = self.read(A), self.read()
                    self.assertEqual(shown["total"]["response_count"], 2)
                    self.assertEqual(index["unattributed"]["unknown"]["response_count"], 0)
                    projections.append((shown, index["unattributed"]))
            self.assertEqual(projections[0], projections[1])

    def test_nested_duplicate_keys_preserve_pending_and_unknown_after_restart(self):
        cases = (
            ("handle-conflict", lambda ack: '{"session_id":7,"session_id":9}'),
            ("handle-same", lambda ack: {"output": '{"session_id":9,"session_id":9}'}),
            ("ok-conflict", lambda ack: {"content": [{"type": "input_text", "text":
                '{"ok":false,' + json.dumps(ack)[1:]}]}),
            ("ok-same", lambda ack: {"output": json.dumps({"content":
                '{"ok":true,' + json.dumps(ack)[1:]})}),
        )
        for name, malformed in cases:
            projections = []
            for sliced in (False, True):
                with self.subTest(case=name, sliced=sliced):
                    self.setUp()
                    start = self.acknowledgement(A, 1, "in_progress")
                    pause = self.acknowledgement(A, 1, "paused")
                    rows = self.operation("start", start, "r0", direct=True)
                    rows += [call("ambiguous", batch_input(fresh(), fresh(), fresh()), "r1"),
                             usage("r1", turn=turn(1))]
                    self.write_turn(rows)
                    self.ingest(sliced=sliced)
                    with self.repository.connection() as connection:
                        before = json.loads(connection.execute(
                            "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                    self.restart()
                    results = [settled(2, {"session_id": 23}), settled(1, pause),
                               settled(0, malformed(pause))]
                    self.append(output("ambiguous", results))
                    self.ingest(sliced=sliced)
                    with self.repository.connection() as connection:
                        after = json.loads(connection.execute(
                            "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                    self.assertEqual(after, before)
                    found = self.assert_relations_match_full()
                    self.assertEqual([(item.call_id, item.response_id) for item in found], [("start", "r0")])
                    self.restart()
                    next_start = self.acknowledgement(B, 2, "in_progress")
                    self.append(*self.batch("unbound-poll", [poll(9)], "r2", [settled(0, next_start)]))
                    self.ingest(sliced=sliced)
                    found = self.assert_relations_match_full()
                    late = next(item for item in found if item.task_id == B)
                    self.assertIsNone(late.response_id)
                    self.assertEqual(late.method, "unavailable")
                    self.assertFalse(any(item.status == "paused" for item in found))
                    projection = self.projection()
                    self.assertGreater(projection[2]["unknown"]["response_count"], 0)
                    self.assertEqual(self.repository.summary()["models"][0]["response_count"], 3)
                    self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 360)
                    self.assert_private_not_stored()
                    projections.append(projection)
            self.assertEqual(projections[0], projections[1])

    def test_previous_context_prefixes_replay_on_current_codec_without_projection_change(self):
        for old_version in ("codex-response-relations-v2", "codex-response-relations-v3",
                            "codex-response-relations-v4", "codex-response-relations-v5",
                            "codex-response-relations-v6", "codex-response-relations-v7"):
            with self.subTest(old_version=old_version):
                self.setUp()
                self.assert_codec_upgrade_replays_without_projection_change(old_version)

    def test_v6_lexical_misattribution_is_replaced_by_bounded_replay(self):
        import task_governance_tool.usage_response_adapter as adapter

        source = ('let n=2;let results;n++ / (results=await Promise.allSettled('
                  '[tools["write_stdin"]({session_id:7})]),2) / 3;text(results[0].value);')
        start = self.acknowledgement(A, 1, "in_progress")
        rows = self.running("origin", 7, "original")
        rows += [call("unsupported", source, "later"), usage("later", turn=turn(1)),
                 output("unsupported", start)]
        self.write_observed_turn(rows)
        current_admission = adapter._call_input

        def old_admission(name, arguments):
            # Reproduce only the observed v6 false negative. All other parser,
            # continuation and repository behavior stays on the real path.
            return adapter.CallInput("execution") if arguments == source else current_admission(name, arguments)

        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v6"), \
                mock.patch.object(adapter, "_call_input", side_effect=old_admission):
            self.ingest(sliced=True)
        previous = self.projection(include_b=False)
        self.assertEqual(previous[0]["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)
        self.assertEqual(previous[2]["unknown"]["response_count"], 1)
        old_documents = {item["snapshot_id"]: item for item in previous[3]}
        old_models = self.repository.summary()["models"]
        old_cursor = self.repository.cursor(self.source.source_id, THREAD)
        with self.repository.connection() as connection:
            _, boundaries = self.repository._relations(connection)
            self.assertEqual([(item.call_id, item.response_id) for _, _, item in boundaries],
                             [("unsupported", "later")])
            context = json.loads(connection.execute(
                "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
            self.assertEqual(context["version"], 4)
        self.restart()
        for _ in range(len(self.source.path.read_bytes().splitlines()) * 3):
            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
            self.assertLessEqual(result["records"], 1)
            self.restart()
            if result.get("replay"):
                break
        else:
            self.fail("v6 lexical relation did not schedule bounded replay")
        self.repository.collect_source(self.source, max_records=1)
        self.restart()
        self.ingest(sliced=True)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation,
                           old_cursor.incarnation)
        found = self.assert_relations_match_full()
        self.assertEqual([(item.call_id, item.response_id, item.method) for item in found],
                         [("unsupported", None, "unavailable")])
        current = self.projection(include_b=False)
        self.assertEqual(current[0]["periods"][0]["work_units"][0]["exclusive"]["response_count"], 0)
        self.assertEqual(current[2]["unknown"]["response_count"], 2)
        self.assertEqual(self.repository.summary()["models"], old_models)
        self.assertTrue(old_documents.items() <=
                        {item["snapshot_id"]: item for item in current[3]}.items())
        self.assert_private_not_stored()

    def test_legacy_pending_bracket_candidate_cannot_validate_without_source_replay(self):
        import task_governance_tool.usage_response_adapter as adapter

        for version, batch_origin in ((1, False), (2, False), (2, True)):
            for original in (False, True):
                with self.subTest(version=version, batch=batch_origin, original=original):
                    self.setUp()
                    start = self.acknowledgement(A, 1, "in_progress")
                    source = batch_input(fresh()) if batch_origin else 'text(await ' + fresh() + ');'
                    wrap = (lambda value: [settled(0, value)]) if batch_origin else (lambda value: value)
                    rows = [call("origin", source, None)]
                    if original:
                        rows.append(usage("original", turn=turn(1)))
                    rows += [output("origin", wrap(PRIVATE)), usage("later", turn=turn(1))]
                    self.write_observed_turn(rows)
                    old_prefix = "codex-response-relations-v2" if version == 1 else "codex-response-relations-v5"
                    with mock.patch.object(adapter, "VERSION", old_prefix):
                        self.ingest(sliced=True)
                    self.old_contexts(version)
                    # Older contexts cannot establish whether this candidate
                    # preceded the first result. The late-only case reproduces
                    # the candidate which the old implementation could retain.
                    with self.repository.connection(write=True) as connection:
                        context = json.loads(connection.execute(
                            "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                        frame = context["batches"]["origin"][0] if batch_origin else context["calls"]["origin"]
                        frame.update(response="original" if original else "later", method="single_bracket")
                        self.assertNotIn("bracket_closed", frame)
                        connection.execute("UPDATE usage_response_context SET document=? WHERE lane='ingest'",
                                           (json.dumps(context),))
                    old_cursor = self.repository.cursor(self.source.source_id, THREAD)
                    self.restart()
                    self.append(output("origin", wrap(start)))
                    self.ingest(sliced=True)
                    with self.repository.connection() as connection:
                        _, boundaries = self.repository._relations(connection)
                    self.assertEqual([(item.response_id, item.method) for _, _, item in boundaries],
                                     [(None, "unavailable")])
                    before = self.projection(include_b=False)
                    self.assertEqual(before[0]["periods"][0]["work_units"][0]["exclusive"]["response_count"], 0)
                    self.assertEqual(before[2]["unknown"]["response_count"], 2 if original else 1)
                    old_models = self.repository.summary()["models"]
                    old_documents = {item["snapshot_id"]: item for item in before[3]}
                    for _ in range(len(self.source.path.read_bytes().splitlines()) * 3):
                        result = self.repository.collect_source(self.source, lane="audit", max_records=1)
                        self.assertLessEqual(result["records"], 1)
                        self.restart()
                        if result.get("replay"):
                            break
                    else:
                        self.fail("legacy bracket proof did not schedule bounded replay")
                    self.repository.collect_source(self.source, max_records=1)
                    self.restart()
                    self.ingest(sliced=True)
                    self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation,
                                       old_cursor.incarnation)
                    found = self.assert_relations_match_full()
                    self.assertEqual([(item.response_id, item.method) for item in found],
                                     [("original", "single_bracket")] if original else [(None, "unavailable")])
                    after = self.projection(include_b=False)
                    self.assertEqual(after[0]["periods"][0]["work_units"][0]["exclusive"]["response_count"],
                                     2 if original else 0)
                    self.assertEqual(after[2]["unknown"]["response_count"], 0 if original else 1)
                    self.assertEqual(self.repository.summary()["models"], old_models)
                    self.assertTrue(old_documents.items() <=
                                    {item["snapshot_id"]: item for item in after[3]}.items())
                    self.assert_private_not_stored()

    def test_legacy_capture_requires_refresh_before_returning_changed_pending_projection(self):
        import task_governance_tool.usage_response_adapter as adapter
        import task_governance_tool.usage_response_repository as repository_module

        class LegacyPendingTracker(adapter.ResponseTracker):
            """Exact v2 pending semantics: validated, without v3 normalization."""
            def __init__(self, thread, provider, state=None, **kwargs):
                super().__init__(thread, provider, state, **kwargs)
                if state is not None and state["version"] == 2:
                    self.state = state

        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        # An identical replay can make the retained pending candidate earlier
        # than the source-order fallback. The fixture parser generates this
        # candidate; it is not manually assigned or inferred by the test.
        rows += [usage("r1", turn=turn(1)), call("pending", batch_input(fresh()), None),
                 usage("r1", turn=turn(1)), output("pending", [settled(0, {"session_id": 7})]),
                 usage("r2", turn=turn(1))]
        self.write_observed_turn(rows)
        self.ingest(sliced=True)
        self.old_contexts(2)
        with mock.patch.object(repository_module, "ResponseTracker", LegacyPendingTracker):
            self.repository.refresh(self.reader)
            previous = self.read(A)
            history = self.read(audit=True)
        old_models = self.repository.summary()["models"]
        old_documents = {item["snapshot_id"]: item for item in history["snapshots"]}
        self.assertEqual(previous["periods"][0]["work_units"][0]["exclusive"]["response_count"], 1)
        before_store, before_source = self.repository.path.read_bytes(), self.source.path.read_bytes()
        for task in (None, A):
            with self.subTest(task=task):
                with self.assertRaises(UsageError) as raised:
                    self.read(task)
                self.assertEqual(raised.exception.code, "usage_pending")
        self.assertEqual(self.repository.path.read_bytes(), before_store)
        self.assertEqual(self.source.path.read_bytes(), before_source)
        self.repository.refresh(self.reader)
        current = self.read(A)
        index = self.read(audit=True)
        self.assertEqual(current["periods"][0]["work_units"][0]["exclusive"]["response_count"], 2)
        self.assertEqual(index["unattributed"]["unknown"]["response_count"], 1)
        self.assertEqual(self.repository.summary()["models"], old_models)
        self.assertTrue(old_documents.items() <= {item["snapshot_id"]: item for item in index["snapshots"]}.items())
        self.assert_private_not_stored()

    def test_batch_origin_single_poll_ambiguity_survives_restart_until_valid_result(self):
        projections = []
        for sliced in (False, True):
            with self.subTest(sliced=sliced):
                self.setUp()
                start = self.acknowledgement(A, 1, "in_progress")
                pause = self.acknowledgement(A, 1, "paused")
                rows = self.operation("start", start, "r0", direct=True)
                rows += self.batch("batch", [fresh()], "r1", [settled(0, {"session_id": 7})])
                rows += [call("poll", 'text(await tools.write_stdin({session_id:7,chars:""}));', "r2"),
                         usage("r2", turn=turn(1))]
                self.write_turn(rows)
                self.ingest(sliced=sliced)
                with self.repository.connection() as connection:
                    before = json.loads(connection.execute(
                        "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                self.restart()
                self.append(output("poll", {"content": [{"type": "text", "text":
                    '{"ok":false,' + json.dumps(pause)[1:]}]}))
                self.ingest(sliced=sliced)
                with self.repository.connection() as connection:
                    after = json.loads(connection.execute(
                        "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                self.assertEqual(after, before)
                found = self.assert_relations_match_full()
                self.assertEqual([(item.call_id, item.response_id) for item in found], [("start", "r0")])
                self.restart()
                self.append(output("poll", pause))
                self.ingest(sliced=sliced)
                found = self.assert_relations_match_full()
                self.assertEqual([(item.call_id, item.response_id) for item in found],
                                 [("start", "r0"), ("batch", "r1")])
                self.assertEqual(self.repository.summary()["models"][0]["response_count"], 3)
                self.assert_private_not_stored()
                self.repository.refresh(self.reader)
                index = self.read(audit=True)
                projections.append((self.read(A), index["unattributed"], index["snapshots"]))
        self.assertEqual(projections[0], projections[1])

    def test_semantic_batch_poll_child_cannot_reparse_ambiguity_after_restart(self):
        projections = []
        for sliced in (False, True):
            with self.subTest(sliced=sliced):
                self.setUp()
                start = self.acknowledgement(A, 1, "in_progress")
                pause = self.acknowledgement(A, 1, "paused")
                next_start = self.acknowledgement(B, 2, "in_progress")
                rows = self.operation("start", start, "r0", direct=True)
                rows += self.running("ordinary-origin", 7, "r1")
                rows += [call("batch", batch_input(poll(7), fresh()), "r2"), usage("r2", turn=turn(1))]
                self.write_turn(rows)
                self.ingest(sliced=sliced)
                with self.repository.connection() as connection:
                    before = json.loads(connection.execute(
                        "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                self.assertIsNone(before["batches"]["batch"][0]["slot"])
                self.restart()
                semantic = {**pause, "content": '{"ok":false,' + json.dumps(next_start)[1:]}
                self.append(output("batch", [settled(1, next_start), settled(0, semantic)]))
                self.ingest(sliced=sliced)
                with self.repository.connection() as connection:
                    after = json.loads(connection.execute(
                        "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
                self.assertEqual(after, before)
                found = self.assert_relations_match_full()
                self.assertEqual([(item.call_id, item.response_id) for item in found], [("start", "r0")])
                self.restart()
                self.append(output("batch", [settled(1, {"exit_code": 0, "output": ""}),
                                             settled(0, pause)]))
                self.ingest(sliced=sliced)
                found = self.assert_relations_match_full()
                self.assertEqual([(item.call_id, item.response_id) for item in found],
                                 [("start", "r0"), ("ordinary-origin", "r1")])
                self.assertEqual(self.repository.summary()["models"][0]["response_count"], 3)
                self.assert_private_not_stored()
                self.repository.refresh(self.reader)
                index = self.read(audit=True)
                projections.append((self.read(A), index["unattributed"], index["snapshots"]))
        self.assertEqual(projections[0], projections[1])

    def assert_codec_upgrade_replays_without_projection_change(self, old_version):
        import task_governance_tool.usage_response_adapter as adapter

        self.assertEqual(adapter.VERSION, "codex-response-relations-v14")
        rows = self.operation("start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        rows += self.operation("done", self.acknowledgement(A, 1, "done", cycle="A"), "r1", direct=True)
        self.write_turn(rows)
        # These completed records project identically. Install the exact old
        # continuation as well as its codec identity before the bounded audit.
        with mock.patch.object(adapter, "VERSION", old_version):
            self.ingest(sliced=True)
        old_shape = (1 if old_version == "codex-response-relations-v2" else
                     3 if old_version in {"codex-response-relations-v6", "codex-response-relations-v7"} else 2)
        self.old_contexts(old_shape)
        self.repository.refresh(self.reader)
        previous = self.read(audit=True)
        old_documents = {item["snapshot_id"]: item for item in previous["snapshots"]}
        old_cycles = {tuple(sorted(row.items())) for row in previous["cycle_links"]}
        old_models = self.repository.summary()["models"]
        old_cursor = self.repository.cursor(self.source.source_id, THREAD)
        with self.repository.connection() as connection:
            old_context = json.loads(connection.execute(
                "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
            self.assertEqual(old_context["version"], old_shape)
            positions, boundaries = self.repository._relations(connection)
            old_relations = ({item for _, _, item in positions}, {item for _, _, item in boundaries})
        self.restart()
        for _ in range(len(self.source.path.read_bytes().splitlines()) + 1):
            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
            self.assertLessEqual(result["records"], 1)
            self.restart()
            if result.get("replay"):
                break
        else:
            self.fail("codec identity alone did not schedule bounded replay")
        self.repository.collect_source(self.source, max_records=1)
        self.restart()
        self.ingest(sliced=True)
        current_cursor = self.repository.cursor(self.source.source_id, THREAD)
        self.assertGreater(current_cursor.incarnation, old_cursor.incarnation)
        self.assertNotEqual(current_cursor.prefix, old_cursor.prefix)
        self.assertEqual(current_cursor.offset, old_cursor.offset)
        with self.repository.connection() as connection:
            positions, boundaries = self.repository._relations(connection)
            self.assertEqual(({item for _, _, item in positions}, {item for _, _, item in boundaries}), old_relations)
            context = json.loads(connection.execute(
                "SELECT document FROM usage_response_context WHERE lane='ingest'").fetchone()[0])
            self.assertEqual(context, {**old_context, "version": 4, "batches": {}})
        self.repository.refresh(self.reader)
        current = self.read(audit=True)
        self.assertEqual(self.repository.summary()["models"], old_models)
        self.assertEqual(self.read(A)["total"]["response_count"], 2)
        self.assertTrue(old_documents.items() <= {item["snapshot_id"]: item for item in current["snapshots"]}.items())
        self.assertTrue(old_cycles <= {tuple(sorted(row.items())) for row in current["cycle_links"]})
        # A known codec upgrade is not permission to silently reset damaged or
        # unknown continuation formats on an ordinary collection attempt.
        for invalid in ({}, {**context, "version": 99}):
            with self.subTest(invalid_version=invalid.get("version")):
                with self.repository.connection(write=True) as connection:
                    connection.execute("UPDATE usage_response_context SET document=? WHERE lane='ingest'",
                                       (json.dumps(invalid),))
                before = self.repository.path.read_bytes()
                with self.assertRaises(UsageError) as raised:
                    self.repository.collect_source(self.source, max_records=1)
                self.assertEqual(raised.exception.code, "usage_schema_invalid")
                self.assertEqual(self.repository.path.read_bytes(), before)

    def test_exact_old_v1_pending_context_and_partial_audit_replay_preserve_history(self):
        import task_governance_tool.usage_response_adapter as adapter

        rows = self.operation("a-start", self.acknowledgement(A, 1, "in_progress"), "r0", direct=True)
        rows += self.operation("a-done", self.acknowledgement(A, 1, "done", cycle="A"), "r1", direct=True)
        rows += self.running("b-start-origin", 7, "r2")
        self.write_turn(rows)
        # These ordinary calls have exactly the old codec's meaning. Generate
        # its prefix, then persist the exact closed v1 continuation shape.
        with mock.patch.object(adapter, "VERSION", "codex-response-relations-v2"):
            self.ingest(sliced=True)
            self.repository.collect_source(self.source, lane="audit", max_records=2)
        self.old_contexts(1)
        with self.repository.connection() as connection:
            audit = json.loads(connection.execute(
                "SELECT document FROM usage_scan_state WHERE lane='audit'").fetchone()[0])
            self.assertLess(audit["state"]["offset"], audit["goal"])
            self.assertIn("session:7", next(json.loads(row["document"]) for row in
                connection.execute("SELECT document FROM usage_response_context WHERE lane='ingest'"))["handles"])
        self.restart()
        self.repository.refresh(self.reader)
        previous = self.read(audit=True)
        old_documents = {item["snapshot_id"]: item for item in previous["snapshots"]}
        old_cycles = {tuple(sorted(row.items())) for row in previous["cycle_links"]}
        incarnation = self.repository.cursor(self.source.source_id, THREAD).incarnation
        next_start = self.acknowledgement(B, 2, "in_progress")
        self.append(*self.batch("late-batch", [poll(7), fresh()], "r3", [
            settled(1, {"exit_code": 0, "output": ""}),
            settled(0, {"exit_code": 0, "output": json.dumps(next_start)})]))
        self.append(*self.operation("b-done", self.acknowledgement(B, 2, "done", cycle="B"), "r4", direct=True))
        self.ingest(sliced=True)
        for _ in range(len(self.source.path.read_bytes().splitlines()) * 3):
            result = self.repository.collect_source(self.source, lane="audit", max_records=1)
            self.restart()
            if result.get("replay"):
                break
        else:
            self.fail("old prefix did not schedule bounded replay")
        self.repository.collect_source(self.source, max_records=1)
        self.restart()
        self.ingest(sliced=True)
        self.assertGreater(self.repository.cursor(self.source.source_id, THREAD).incarnation, incarnation)
        self.assert_relations_match_full()
        a, b, unattributed, _ = self.projection()
        self.assertEqual((a["total"]["response_count"], b["total"]["response_count"]), (2, 3))
        self.assertEqual(unattributed["unknown"]["response_count"], 0)
        current = self.read(audit=True)
        self.assertTrue(old_documents.items() <= {item["snapshot_id"]: item for item in current["snapshots"]}.items())
        self.assertTrue(old_cycles <= {tuple(sorted(row.items())) for row in current["cycle_links"]})
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 5)
        self.assert_private_not_stored()


if __name__ == "__main__":
    unittest.main()
