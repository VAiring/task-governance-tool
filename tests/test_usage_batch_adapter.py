"""Synthetic indexed transport grammar; no host calls or private log bodies."""

import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
from task_governance_tool.usage_batch_adapter import (
    BatchSlot, MAX_SLOTS, MAX_TEXT, is_batch_input, parse_batch_input, parse_batch_output,
)
from task_governance_tool.usage_response_adapter import _text_json, ResponseBoundary, ResponseTracker
from tests.test_usage_response_adapter import call, output
from tests.test_usage_turn_adapter import acknowledgement, THREAD


POLL = 'tools.write_stdin({session_id:7,chars:"",yield_time_ms:25000,max_output_tokens:6500})'
EXEC = 'tools.exec_command({cmd:"fixture","max_output_tokens":3000})'


def batch_source(*calls, store=False):
    save = 'store("fixture_"+i,results[i]);' if store else ""
    return ("const results = await Promise.allSettled([\n" + ",\n".join(calls)
            + "]);for(let i=0;i<results.length;i++){" + save
            + "text({i,result:results[i]});}\n")


def settled(index, value):
    return {"i": index, "result": {"status": "fulfilled", "value": value}}


def block(value, kind="input_text"):
    return {"type": kind, "text": json.dumps(value)}


def routed(value, count=2):
    found = parse_batch_output(value, count, text_parser=_text_json)
    return None if found is None else {index: item.value for index, item in found.items()}


def nested(value, path):
    """Build known transport paths; a text block plus JSON costs two levels."""
    for kind in reversed(path):
        if kind == "string":
            value = json.dumps(value)
        elif kind == "block":
            value = [block(value)]
        else:
            value = {kind: value}
    return value


def unsupported_promise_sources():
    """Synthetic code variants; none is executed or positively admitted."""
    poll = 'tools["write_stdin"]({session_id:7,chars:""})'
    for member in (
        "(Promise.allSettled)", "((Promise.allSettled))",
        "Promise['allSettled']", "Promise['all'+'Settled']",
        "(Promise)['allSettled']", "((Promise))?.allSettled",
        "Promise?.['allSettled']", "Promise.allSettled?.",
        "Promise.allSettled.bind(Promise)", "Promise.all",
        r"\u0050romise.allSettled", r"Promise.\u0061llSettled",
    ):
        yield "const results=await " + member + "([" + poll + "]);text(results[0].value);"
    for alias in ("const settle=Promise.allSettled;", "const P=Promise;const settle=P.allSettled;",
                  "const {allSettled:settle}=Promise;"):
        yield alias + "const results=await settle([" + poll + "]);text(results[0].value);"
    yield "const results=await Promise.allSettled.call(Promise,[" + poll + "]);text(results[0].value);"
    yield "const results=await Promise.allSettled.apply(Promise,[[" + poll + "]]);text(results[0].value);"


def obscured_promise_sources():
    poll = 'tools["write_stdin"]({session_id:7,chars:""})'
    action = 'text((await Promise.allSettled([' + poll + ']))[0].value)'
    for separator in ("\n", "\r", "\r\n", "\u2028", "\u2029"):
        yield "// fixture" + separator + action + ";"
    for numerator in ("n++", "n--", "obj.return", "obj.await", '"+"', '";"', '"return"', "2", "(n)"):
        yield "let n=2;const obj={return:2,await:2};" + numerator + "/(" + action + ",2)/2;"


class BatchInputTests(unittest.TestCase):
    def test_observed_shape_with_and_without_store_keeps_only_slot_metadata(self):
        for store in (False, True):
            with self.subTest(store=store):
                found = parse_batch_input("exec", batch_source(POLL, EXEC, store=store))
                self.assertEqual(found, (BatchSlot("poll", ("session", "7")), BatchSlot("exec")))
                self.assertNotIn("fixture", repr(found))
                self.assertNotIn("6500", repr(found))

    def test_whitespace_and_pragma_preserve_the_fixed_loop_and_positions(self):
        source = '''// @exec: {"yield_time_ms": 1000}
          const results = await Promise.allSettled ( [
            tools.write_stdin ( { "session_id" : 7 , chars : "" } ) ,
            tools.exec_command ( {cmd : "fixture"} )
          ] ) ;
          for ( let i = 0 ; i < results.length ; i ++ ) {
            store ( "fixture_" + i , results [ i ] ) ;
            text ( { i , result : results [ i ] } ) ;
          } ;
        '''
        self.assertEqual(parse_batch_input("functions.exec", source),
                         (BatchSlot("poll", ("session", "7")), BatchSlot("exec")))

    def test_multiple_polls_retain_their_own_literal_positions(self):
        source = batch_source(EXEC, POLL, POLL.replace("session_id:7", "session_id:9"))
        self.assertEqual(parse_batch_input("exec", source),
                         (BatchSlot("exec"), BatchSlot("poll", ("session", "7")),
                          BatchSlot("poll", ("session", "9"))))

    def test_literal_command_and_store_strings_are_data_not_code(self):
        command = 'Promise.allSettled([tools.write_stdin({session_id:999})]); "\\\n'
        source = batch_source('tools.exec_command({cmd:' + json.dumps(command) + '})', POLL)
        with mock.patch("builtins.eval", side_effect=AssertionError("must not evaluate")), \
                mock.patch("builtins.exec", side_effect=AssertionError("must not execute")):
            self.assertEqual(parse_batch_input("exec", source),
                             (BatchSlot("exec"), BatchSlot("poll", ("session", "7"))))

    def test_variable_spread_computed_duplicate_and_expression_arguments_are_rejected(self):
        bad_arguments = (
            "{session_id:handle}", "{session_id:7,...options}", '{["session_id"]:7}',
            '{session_id:7,"session_id":9}', '{session_id:7,"session_\\u0069d":9}',
            "{session_id:7+0}", "{session_id:readHandle()}", "{session_id:(7)}",
            "{session_id:7,chars:input}", "{session_id:7,get chars(){return '';}}",
            "{session_id:7,chars:`fixture`}", "{session_id:7,chars:'fixture'}",
            "{session_id:7,unknown:0}", "{session_id:7,chars:null}",
            "{session_id:7,yield_time_ms:true}", "{session_id:7,max_output_tokens:-1}",
            "[7]", "null",
        )
        for args in bad_arguments:
            with self.subTest(args=args):
                self.assertIsNone(parse_batch_input("exec", batch_source("tools.write_stdin(" + args + ")", EXEC)))
        for args in ('{cmd:command}', '{cmd:"a",cmd:"b"}', '{cmd:"a",...options}',
                     '{["cmd"]:"a"}', '{cmd:123}', '{cmd:"a",unknown:0}'):
            with self.subTest(exec_args=args):
                self.assertIsNone(parse_batch_input("exec", batch_source("tools.exec_command(" + args + ")")))

    def test_handle_types_and_range_are_closed(self):
        for handle in ("07", "-1", "true", "null", '"7"', "7.0", "7e0", str(2**63), "9" * 100):
            with self.subTest(handle=handle):
                source = batch_source(POLL.replace("session_id:7", "session_id:" + handle))
                self.assertIsNone(parse_batch_input("exec", source))
        self.assertEqual(parse_batch_input("exec", batch_source(POLL.replace("session_id:7", "session_id:0"))),
                         (BatchSlot("poll", ("session", "0")),))

    def test_javascript_number_literal_must_preserve_the_handle_exactly(self):
        for handle in (2**53 + 1, 2**54 + 1):
            with self.subTest(inexact_handle=handle):
                source = batch_source(POLL.replace("session_id:7", "session_id:" + str(handle)))
                self.assertIsNone(parse_batch_input("exec", source))
        for handle in (2**53, 2**54):
            with self.subTest(exact_handle=handle):
                source = batch_source(POLL.replace("session_id:7", "session_id:" + str(handle)))
                self.assertEqual(parse_batch_input("exec", source),
                                 (BatchSlot("poll", ("session", str(handle))),))

    def test_changed_loop_binding_or_additional_javascript_is_not_admitted(self):
        source = batch_source(POLL, EXEC, store=True)
        alternatives = (
            source.replace("const results", "let results"), source.replace("results", "other"),
            source.replace("i<results.length", "i<=results.length"),
            source.replace("i++", "++i"), source.replace("results[i]", "results[0]"),
            source.replace("{i,result:", "{i:0,result:"), source.replace("+i,results", "+j,results"),
            source.replace('store("fixture_"', "store(prefix"),
            source.replace("text({i,result:results[i]});", "text(results[i]);"),
            source.replace("text({i,result:results[i]});", "text({i,result:results[i]});extra();"),
            "extra();" + source, source + "extra();", source.replace("Promise.allSettled", "Promise.all"),
            source.replace("tools.exec_command", "tools.other"),
            source.replace("tools.write_stdin", "tools['write_stdin']"),
        )
        for number, altered in enumerate(alternatives):
            with self.subTest(case=number):
                self.assertIsNone(parse_batch_input("exec", altered))

    def test_slot_text_and_literal_depth_bounds_fail_closed(self):
        self.assertEqual(len(parse_batch_input("exec", batch_source(*([EXEC] * MAX_SLOTS)))), MAX_SLOTS)
        self.assertIsNone(parse_batch_input("exec", batch_source(*([EXEC] * (MAX_SLOTS + 1)))))
        self.assertIsNone(parse_batch_input("exec", batch_source()))
        self.assertIsNone(parse_batch_input("exec", batch_source(EXEC) + " " * MAX_TEXT))
        self.assertIsNone(parse_batch_input("exec", batch_source(EXEC) + "\ud800"))
        deep = 'tools.exec_command({cmd:"fixture",prefix_rule:' + "[" * 12 + '"fixture"' + "]" * 12 + "})"
        self.assertIsNone(parse_batch_input("exec", batch_source(deep)))

    def test_only_exec_string_inputs_are_candidates(self):
        for name, value in (("wait", batch_source(POLL)), ("exec", {}),
                            ("exec", "text(await tools.exec_command({cmd:\"fixture\"}));")):
            with self.subTest(name=name, value_type=type(value).__name__):
                self.assertFalse(is_batch_input(name, value))
                self.assertIsNone(parse_batch_input(name, value))

    def test_batch_candidate_detection_ignores_literal_and_comment_text(self):
        ordinary = 'text(await tools.exec_command({cmd:"fixture"}));'
        sources = (
            'text(await tools.write_stdin({session_id:7,chars:"allSettled"}));',
            'text(await tools.write_stdin({session_id:7,chars:"Promise.allSettled([])"}));',
            'text(await tools.exec_command({cmd:"Promise.allSettled([tools.write_stdin({session_id:7})])"}));',
            'store("Promise.allSettled",1);' + ordinary,
            "store('Promise.allSettled([])',1);" + ordinary,
            'store(`Promise.allSettled([])`,1);' + ordinary,
            '// Promise.allSettled([tools.write_stdin({session_id:7})]);\n' + ordinary,
            '/* Promise.allSettled([tools.write_stdin({session_id:7})]); */' + ordinary,
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertFalse(is_batch_input("exec", source))
                self.assertIsNone(parse_batch_input("exec", source))

    def test_unsupported_structural_batch_remains_a_candidate(self):
        for source in ('await Promise.allSettled(requests);',
                       'const other=await Promise.allSettled([' + POLL + ']);text(other);',
                       'await Promise /* marker */ . allSettled(requests);',
                       'await Promise["allSettled"](requests);'):
            with self.subTest(source=source):
                self.assertTrue(is_batch_input("exec", source))
                self.assertIsNone(parse_batch_input("exec", source))

    def test_executable_promise_references_are_candidates_without_interpreting_their_use(self):
        for number, source in enumerate(unsupported_promise_sources()):
            with self.subTest(case=number):
                self.assertTrue(is_batch_input("exec", source))
                self.assertIsNone(parse_batch_input("exec", source))

    def test_inert_promise_references_and_identifier_escapes_do_not_create_candidates(self):
        ordinary = 'text(await tools.exec_command({cmd:"fixture"}));'
        bodies = (r"\u0050romise.allSettled", "(Promise.allSettled)", "const P=Promise;")
        for body in bodies:
            for source in (
                "store(" + json.dumps(body) + ",1);" + ordinary,
                "/* " + body + " */" + ordinary,
                "// " + body + "\n" + ordinary,
                "const pattern=/" + body + "/;" + ordinary,
            ):
                with self.subTest(source=source):
                    self.assertFalse(is_batch_input("exec", source))
                    self.assertIsNone(parse_batch_input("exec", source))

    def test_line_terminators_and_division_cannot_hide_executable_references(self):
        for number, source in enumerate(obscured_promise_sources()):
            with self.subTest(case=number):
                self.assertTrue(is_batch_input("exec", source))
                self.assertIsNone(parse_batch_input("exec", source))

    def test_regex_text_is_inert_only_at_proven_expression_starts(self):
        regex = r"/Promise[\/]allSettled/gi"
        ordinary = 'text(await tools.exec_command({cmd:"fixture"}));'
        for prefix in (regex + ";", "const pattern=" + regex + ";",
                       "store(" + regex + ",1);", "const patterns=[" + regex + "];",
                       "const patterns={p:" + regex + "};"):
            with self.subTest(prefix=prefix):
                self.assertFalse(is_batch_input("exec", prefix + ordinary))
        for prefix in ("n++/fixture/;", "n--/fixture/;", '"+"/fixture/;',
                       "obj.return/fixture/;", "return /fixture/;", "if(n)/fixture/;"):
            with self.subTest(uncertain=prefix):
                self.assertTrue(is_batch_input("exec", prefix + ordinary))
        for separator in ("\r", "\n", "\u2028", "\u2029"):
            for escape in ("", "\\"):
                with self.subTest(separator=repr(separator), escape=escape):
                    self.assertTrue(is_batch_input("exec", "const pattern=/fixture" + escape + separator + "/;"))


class BatchOutputTests(unittest.TestCase):
    def test_indices_route_reversed_arrival_and_running_or_terminal_results(self):
        running = {"session_id": 7, "output": ""}
        terminal = {"exit_code": 0, "output": "fixture"}
        output = [{"type": "input_text", "text": "Script completed\nWall time 1.2 seconds\nOutput:\n"},
                  block(settled(1, terminal)), block(settled(0, running), "text")]
        self.assertEqual(routed(output), {0: running, 1: terminal})

    def test_missing_rejected_or_opaque_peer_never_borrows_another_slot(self):
        running = {"session_id": 7, "output": ""}
        peers = ([], [block({"i": 1, "result": {"status": "rejected", "reason": "PRIVATE"}})],
                 [{"type": "text", "text": "PRIVATE"}], [block({"unknown": settled(1, running)})],
                 [{"unknown": settled(1, running)}])
        for peer in peers:
            with self.subTest(peer_type=repr(peer)[:30]):
                self.assertEqual(routed([block(settled(0, running)), *peer]), {0: running})
        self.assertEqual(routed([block({"i": 0, "result": {"status": "rejected", "reason": "PRIVATE"}})]), {})

    def test_duplicate_indices_including_rejected_slots_invalidate_whole_mapping(self):
        first = settled(0, {"session_id": 7})
        for duplicate in (first, settled(0, {"session_id": 9}),
                          {"i": 0, "result": {"status": "rejected", "reason": "PRIVATE"}}):
            for values in ((first, duplicate), (duplicate, first)):
                with self.subTest(values=values):
                    self.assertIsNone(routed([block(value) for value in values]))

    def test_invalid_index_never_leaves_earlier_success_in_mapping(self):
        for index in (-1, 2, True, False, 0.0, "0", None, [], {}):
            with self.subTest(index=index):
                self.assertIsNone(routed([block(settled(0, {})), block(settled(index, {}))]))

    def test_duplicate_structural_json_keys_are_not_last_wins_routing(self):
        bodies = (
            '{"i":0,"i":1,"result":{"status":"fulfilled","value":{"session_id":7}}}',
            '{"i":0,"result":{"status":"rejected","reason":"PRIVATE"},'
            '"result":{"status":"fulfilled","value":{"session_id":7}}}',
            '{"i":0,"result":{"status":"rejected","status":"fulfilled","value":{"session_id":7}}}',
            '{"i":0,"result":{"status":"fulfilled","value":{"session_id":7},"value":{"session_id":9}}}',
        )
        for number, body in enumerate(bodies):
            with self.subTest(case=number):
                self.assertIsNone(routed(body))
                self.assertIsNone(routed([block(settled(1, {"exit_code": 0})),
                                          {"type": "text", "text": body}]))

    def test_complete_text_is_checked_before_any_index_is_returned(self):
        body = json.dumps(settled(0, {"session_id": 7}))
        for text in (body + '{"partial":', body + " PRIVATE", "Example: " + body,
                     "Unknown warning\n" + body, body[:-1]):
            with self.subTest(text_length=len(text)):
                self.assertEqual(routed(text), {})
        self.assertEqual(routed(body + "\n" + json.dumps(settled(1, {"exit_code": 0}))),
                         {0: {"session_id": 7}, 1: {"exit_code": 0}})

    def test_separate_malformed_text_block_does_not_discard_independent_slot(self):
        valid = settled(0, {"session_id": 7})
        malformed = json.dumps(settled(1, {"exit_code": 0})) + '{"partial":'
        self.assertEqual(routed([block(valid), {"type": "text", "text": malformed}]),
                         {0: {"session_id": 7}})

    def test_known_header_warning_and_output_content_containers_are_parsed(self):
        wrapped = settled(0, {"session_id": 7})
        body = json.dumps(wrapped)
        header = "Chunk ID: fixture\nWall time: 0.1 seconds\nProcess exited with code 0\nOutput:\n"
        warning = "Failed to find real location of C:\\fixture\\python.exe\n"
        for value in (header + body, warning + body, {"output": body},
                      {"content": [block(wrapped)], "_meta": {"private": "not inspected"}}):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(routed(value), {0: {"session_id": 7}})

    def test_unknown_containers_and_unmarked_results_are_not_searched(self):
        wrapped = settled(0, {"session_id": 7})
        for value in ({"private": wrapped}, {"output": wrapped, "unknown": True},
                      {"structuredContent": wrapped}, [wrapped],
                      [{"type": "image", "text": json.dumps(wrapped)}],
                      {"i": 0, "result": wrapped["result"], "unknown": True}):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(routed(value), {})

    def test_fulfilled_value_is_kept_opaque_until_result_validation(self):
        opaque = {"private": "PRIVATE", "nested": settled(1, {"session_id": 9})}
        self.assertEqual(routed(settled(0, opaque)), {0: opaque})
        for result in ({"status": "fulfilled"}, {"status": "fulfilled", "value": {}, "extra": True},
                       {"status": "unknown", "value": {}}, None):
            with self.subTest(result=result):
                self.assertEqual(routed({"i": 0, "result": result}), {})

    def test_count_text_and_container_depth_bounds_do_not_create_a_mapping(self):
        for count in (0, -1, MAX_SLOTS + 1, True, "2", None):
            with self.subTest(count=count):
                self.assertIsNone(routed(settled(0, {}), count=count))
        self.assertEqual(routed(settled(MAX_SLOTS - 1, {}), count=MAX_SLOTS), {MAX_SLOTS - 1: {}})
        oversized = json.dumps(settled(0, {})) + " " * MAX_TEXT
        self.assertEqual(routed(oversized), {})
        deep = settled(0, {"session_id": 7})
        for _ in range(10):
            deep = {"output": deep}
        self.assertEqual(routed(deep), {})

    def test_routed_value_retains_consumed_outer_transport_depth(self):
        for path in ((), ("output",), ("string",), ("block",),
                     ("content", "block", "output", "string")):
            with self.subTest(path=path):
                value = {"session_id": 7}
                found = parse_batch_output(nested(settled(0, value), path), 1, text_parser=_text_json)
                self.assertEqual(found[0].value, value)
                self.assertEqual(found[0].depth, 1 + sum(2 if kind == "block" else 1 for kind in path))
        self.assertEqual(routed(nested(settled(0, {"session_id": 7}), ("output",) * 8), count=1), {})


class BatchDepthAndCandidateTrackerTests(unittest.TestCase):
    def feed(self, tracker, record, order):
        return [item for item in tracker.consume(record, order, None, "fixture-project")
                if isinstance(item, ResponseBoundary)]

    def test_one_depth_budget_spans_indexed_wrapper_and_inner_value(self):
        paths = ((("output",) * 3, ("content",) * 4),
                 (("string",) * 2, ("output",) * 5),
                 (("block",), ("content", "output", "string", "content", "output")),
                 (("output", "block"), ("content", "string", "output", "content")))
        for outer, inner in paths:
            for extra in (0, 1):
                with self.subTest(outer=outer, inner=inner, extra=extra):
                    tracker = ResponseTracker(THREAD, "fixture")
                    self.feed(tracker, call("batch", arguments=batch_source(EXEC), response_id="origin"), 10)
                    body = nested(settled(0, nested(acknowledgement(), inner + ("output",) * extra)), outer)
                    found = self.feed(tracker, output("batch", body), 20)
                    self.assertEqual([item.response_id for item in found], ["origin"] if extra == 0 else [])
                    self.assertEqual("batch" in tracker.state["batches"], extra == 1)

    def test_overdepth_running_handle_cannot_bind_a_later_poll_and_valid_peer_survives(self):
        for outer_count, inner_count, accepted in ((1, 4, True), (2, 4, False), (5, 7, False)):
            with self.subTest(outer=outer_count, inner=inner_count):
                tracker = ResponseTracker(THREAD, "fixture")
                self.feed(tracker, call("batch", arguments=batch_source(EXEC, EXEC), response_id="origin"), 10)
                deep = nested(settled(0, nested({"session_id": 7}, ("content",) * inner_count)),
                              ("output",) * outer_count)
                found = self.feed(tracker, output("batch", [block(deep), block(settled(1, acknowledgement()))]), 20)
                self.assertEqual([item.response_id for item in found], ["origin"])
                self.assertEqual("session:7" in tracker.state["handles"], accepted)
                tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                self.feed(tracker, call("poll", name="write_stdin", arguments={"session_id": 7},
                                        response_id="poll-response"), 30)
                found = self.feed(tracker, output("poll", acknowledgement("paused", 2, "tg_event_1111111111111111")), 40)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].response_id, "origin" if accepted else None)

    def test_sole_literal_poll_keyword_data_and_ordinary_comments_preserve_origins(self):
        for chars in ("allSettled", "Promise.allSettled([])"):
            with self.subTest(chars=chars):
                tracker = ResponseTracker(THREAD, "fixture")
                self.feed(tracker, call("origin", response_id="origin-response"), 10)
                self.feed(tracker, output("origin", {"session_id": 7}), 20)
                source = 'text(await tools.write_stdin({session_id:7,chars:' + json.dumps(chars) + '}));'
                self.feed(tracker, call("poll", arguments=source, response_id="poll-response"), 30)
                found = self.feed(tracker, output("poll", acknowledgement()), 40)
                self.assertEqual([item.response_id for item in found], ["origin-response"])
        ordinary = 'text(await tools.exec_command({cmd:"Promise.allSettled([])"}));'
        for prefix in ('store("Promise.allSettled",1);', '// Promise.allSettled([])\n',
                       '/* Promise.allSettled([]) */'):
            with self.subTest(prefix=prefix):
                tracker = ResponseTracker(THREAD, "fixture")
                self.feed(tracker, call("ordinary", arguments=prefix + ordinary, response_id="origin"), 10)
                found = self.feed(tracker, output("ordinary", acknowledgement()), 20)
                self.assertEqual([item.response_id for item in found], ["origin"])

    def test_unsupported_promise_forms_never_promote_flattened_poll_results(self):
        for number, source in enumerate(unsupported_promise_sources()):
            for restart in (False, True):
                for indexed in (False, True):
                    with self.subTest(case=number, restart=restart, indexed=indexed):
                        tracker = ResponseTracker(THREAD, "fixture")
                        self.feed(tracker, call("original", response_id="original-response"), 10)
                        self.feed(tracker, output("original", {"session_id": 7}), 20)
                        self.feed(tracker, call("poll", arguments=source, response_id="poll-response"), 30)
                        if restart:
                            tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                        body = settled(0, acknowledgement()) if indexed else acknowledgement()
                        found = self.feed(tracker, output("poll", body), 40)
                        self.assertEqual(len(found), 1)
                        self.assertIsNone(found[0].response_id)
                        self.assertEqual(tracker.state["handles"]["session:7"]["response"], "original-response")

    def test_lexical_ambiguity_never_promotes_flattened_poll_results_after_restart(self):
        for number, source in enumerate(obscured_promise_sources()):
            for restart in (False, True):
                with self.subTest(case=number, restart=restart):
                    tracker = ResponseTracker(THREAD, "fixture")
                    self.feed(tracker, call("original", response_id="original-response"), 10)
                    self.feed(tracker, output("original", {"session_id": 7}), 20)
                    self.feed(tracker, call("poll", arguments=source, response_id="poll-response"), 30)
                    if restart:
                        tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                    found = self.feed(tracker, output("poll", acknowledgement()), 40)
                    self.assertEqual(len(found), 1)
                    self.assertIsNone(found[0].response_id)
                    self.assertEqual(tracker.state["handles"]["session:7"]["response"], "original-response")

    def test_duplicate_keys_in_fulfilled_transport_reject_all_peers_atomically(self):
        success = json.dumps(acknowledgement())
        bodies = ('{"session_id":7,"session_id":9}',
                  '{"session_id":7,"session_id":7}',
                  '{"ok":false,' + success[1:],
                  '{"ok":true,' + success[1:],
                  '{"\\u006fk":false,' + success[1:])
        wrappers = (
            lambda body: body,
            lambda body: {"output": body},
            lambda body: {"content": body},
            lambda body: [{"type": "text", "text": body}],
            lambda body: {"content": [{"type": "input_text", "text": body}]},
            lambda body: {"output": json.dumps({"content": body})},
            lambda body: json.dumps(body),
            lambda body: {"output": "Script completed\nOutput:\n" + body},
        )
        for body_number, body in enumerate(bodies):
            for wrapper_number, wrap in enumerate(wrappers):
                for reverse in (False, True):
                    with self.subTest(body=body_number, wrapper=wrapper_number, reverse=reverse):
                        tracker = ResponseTracker(THREAD, "fixture")
                        self.feed(tracker, call("batch", arguments=batch_source(EXEC, EXEC, EXEC),
                                                response_id="origin"), 10)
                        before = json.loads(json.dumps(tracker.state))
                        results = [block(settled(0, wrap(body))),
                                   block(settled(1, acknowledgement())),
                                   block(settled(2, {"session_id": 23}))]
                        if reverse:
                            results.reverse()
                        self.assertEqual(tracker.consume(output("batch", results), 20, None,
                                                         "fixture-project"), ())
                        self.assertEqual(tracker.state, before)
                        tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                        for offset, handle in enumerate((7, 9, 23)):
                            identity = "poll-" + str(handle)
                            self.feed(tracker, call(identity, name="write_stdin", arguments={"session_id": handle},
                                                    response_id="poll-response"), 30 + offset * 2)
                            found = self.feed(tracker, output(identity, acknowledgement()), 31 + offset * 2)
                            self.assertEqual(len(found), 1)
                            self.assertIsNone(found[0].response_id)

    def test_duplicate_keys_do_not_invalidate_a_preexisting_handle_or_poll_alias(self):
        tracker = ResponseTracker(THREAD, "fixture")
        self.feed(tracker, call("original", response_id="original-response"), 10)
        self.feed(tracker, output("original", {"session_id": 7}), 20)
        self.feed(tracker, call("issued", name="write_stdin", arguments={"session_id": 7},
                                response_id="poll-response"), 30)
        self.feed(tracker, call("batch", arguments=batch_source(EXEC, EXEC), response_id="batch-response"), 40)
        before = json.loads(json.dumps(tracker.state))
        results = [block(settled(0, {"output": '{"session_id":9,"session_id":7}'})),
                   block(settled(1, {"session_id": 7}))]
        self.assertEqual(tracker.consume(output("batch", results), 50, None, "fixture-project"), ())
        self.assertEqual(tracker.state, before)
        tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
        found = self.feed(tracker, output("issued", acknowledgement()), 60)
        self.assertEqual([item.response_id for item in found], ["original-response"])

    def test_duplicate_keys_in_already_resolved_slot_reject_current_correspondence(self):
        tracker = ResponseTracker(THREAD, "fixture")
        self.feed(tracker, call("batch", arguments=batch_source(EXEC, EXEC), response_id="origin"), 10)
        self.assertEqual([item.response_id for item in self.feed(
            tracker, output("batch", [block(settled(0, acknowledgement()))]), 20)], ["origin"])
        self.assertIsNone(tracker.state["batches"]["batch"][0])
        tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
        before = json.loads(json.dumps(tracker.state))
        results = [block(settled(1, acknowledgement("paused", 2, "tg_event_1111111111111111"))),
                   block(settled(0, {"output": '{"session_id":7,"session_id":9}'}))]
        self.assertEqual(tracker.consume(output("batch", results), 30, None, "fixture-project"), ())
        self.assertEqual(tracker.state, before)

    def test_duplicate_looking_text_in_rejected_or_unknown_body_stays_opaque(self):
        body = '{"session_id":7,"session_id":9}'
        opaque_peers = (
            {"i": 0, "result": {"status": "rejected", "reason": body}},
            settled(0, {"private": body}),
            settled(0, {"output": body, "unknown": True}),
        )
        for peer in opaque_peers:
            with self.subTest(peer=peer):
                tracker = ResponseTracker(THREAD, "fixture")
                self.feed(tracker, call("batch", arguments=batch_source(EXEC, EXEC), response_id="origin"), 10)
                found = self.feed(tracker, output("batch", [block(peer), block(settled(1, acknowledgement()))]), 20)
                self.assertEqual([item.response_id for item in found], ["origin"])
                self.assertEqual(tracker.state["handles"], {})
                self.assertIsNotNone(tracker.state["batches"]["batch"][0])
                self.assertIsNone(tracker.state["batches"]["batch"][1])

    def test_batch_origin_single_poll_rejects_duplicates_without_retiring_its_lifetime(self):
        success = json.dumps(acknowledgement())
        invalid = (
            '{"ok":false,' + success[1:],
            {"output": '{"ok":true,' + success[1:]},
            "Script running with cell ID fixture-cell\nOutput:\n" + '{"ok":false,' + success[1:],
            {"content": [{"type": "input_text", "text": '{"session_id":7,"session_id":9}'}]},
        )
        for index, body in enumerate(invalid):
            for wrapper in (False, True):
                with self.subTest(case=index, exec_wrapper=wrapper):
                    tracker = ResponseTracker(THREAD, "fixture")
                    self.feed(tracker, call("batch", arguments=batch_source(EXEC), response_id="origin"), 10)
                    self.feed(tracker, output("batch", [block(settled(0, {"session_id": 7}))]), 20)
                    tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                    name = "exec" if wrapper else "write_stdin"
                    arguments = ('text(await tools.write_stdin({session_id:7,chars:""}));'
                                 if wrapper else {"session_id": 7})
                    self.feed(tracker, call("poll", name=name, arguments=arguments,
                                            response_id="poll-response"), 30)
                    before = json.loads(json.dumps(tracker.state))
                    # An independent valid body must not hide the ambiguity or
                    # terminate the original batch handle before the next read.
                    results = [block(acknowledgement()), block(body)]
                    self.assertEqual(tracker.consume(output("poll", results), 40, None, "fixture-project"), ())
                    self.assertEqual(tracker.state, before)
                    tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                    found = self.feed(tracker, output("poll", acknowledgement()), 50)
                    self.assertEqual([item.response_id for item in found], ["origin"])
                    self.assertEqual(tracker.state["handles"], {})
                    self.assertNotIn("poll", tracker.state["calls"])
                    self.assertNotIn("batch", tracker.state["batches"])

    def test_semantic_body_nested_duplicates_cannot_bypass_batch_preflight(self):
        duplicate = '{"ok":false,' + json.dumps(acknowledgement("paused", 2,
                                                                  "tg_event_1111111111111111"))[1:]
        for ordinary_origin in (False, True):
            for outer in (acknowledgement(), {"status": "saved"}, {"context_check": "matched_at_read"}):
                for field in ("output", "content"):
                    with self.subTest(ordinary_origin=ordinary_origin, outer=next(iter(outer)), field=field):
                        tracker = ResponseTracker(THREAD, "fixture")
                        if ordinary_origin:
                            self.feed(tracker, call("original", response_id="original-response"), 1)
                            self.feed(tracker, output("original", {"session_id": 7}), 2)
                            tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                        self.feed(tracker, call("batch", arguments=batch_source(POLL if ordinary_origin else EXEC, EXEC),
                                                response_id="batch-response"), 10)
                        if ordinary_origin:
                            self.assertIsNone(tracker.state["batches"]["batch"][0]["slot"])
                        before = json.loads(json.dumps(tracker.state))
                        semantic = {**outer, field: duplicate}
                        results = [block(settled(1, acknowledgement())), block(settled(0, semantic))]
                        self.assertEqual(tracker.consume(output("batch", results), 20, None, "fixture-project"), ())
                        self.assertEqual(tracker.state, before)

    def test_semantic_body_children_keep_total_depth_in_legacy_projection(self):
        child = json.dumps(acknowledgement("paused", 2, "tg_event_1111111111111111"))
        for ordinary_origin in (False, True):
            for inner_depth, admitted in ((5, True), (6, False)):
                for outer in (acknowledgement(), {"status": "saved"}, {"context_check": "matched_at_read"}):
                    with self.subTest(ordinary_origin=ordinary_origin, depth=inner_depth, outer=next(iter(outer))):
                        tracker = ResponseTracker(THREAD, "fixture")
                        if ordinary_origin:
                            self.feed(tracker, call("original", response_id="original-response"), 1)
                            self.feed(tracker, output("original", {"session_id": 7}), 2)
                            tracker = ResponseTracker(THREAD, "fixture", json.loads(json.dumps(tracker.state)))
                        self.feed(tracker, call("batch", arguments=batch_source(POLL if ordinary_origin else EXEC),
                                                response_id="batch-response"), 10)
                        semantic = {**outer, "output": nested(child, ("content",) * inner_depth)}
                        found = self.feed(tracker, output("batch", settled(0, semantic)), 20)
                        expected = (["in_progress"] if "command" in outer else []) + (["paused"] if admitted else [])
                        self.assertEqual([item.status for item in found], expected)
                        self.assertEqual([item.response_id for item in found],
                                         ["original-response" if ordinary_origin else "batch-response"] * len(expected))
                        if not admitted:
                            self.assertIn("batch", tracker.state["batches"])


if __name__ == "__main__":
    unittest.main()
