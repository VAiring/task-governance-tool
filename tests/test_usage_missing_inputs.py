"""Missing logs do not starve obtainable work or become fabricated coverage."""

import json
from time import monotonic
import unittest
from unittest import mock

from tests import test_usage_continuation as fixture
from tests.test_usage_collection import THREAD
from task_governance_tool import usage_continuation as continuation
from task_governance_tool import usage_continuation_state as state
from task_governance_tool import usage_missing_inputs as missing
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_sources import locator_from_source, locator_root_fingerprints
from task_governance_tool.usage_evidence_service import repository_for
from task_governance_tool.usage_values import UsageError
from task_governance_tool.session_identity import CallerIdentity


class MissingInputTests(unittest.TestCase):
    setUp = fixture.ContinuationTests.setUp
    write = fixture.ContinuationTests.write
    read = fixture.ContinuationTests.read
    intake = fixture.ContinuationTests.intake
    run_slice = fixture.ContinuationTests.run_slice
    source_pass = fixture.ContinuationTests.source_pass
    observer = fixture.ContinuationTests.observer

    def source(self, path=None):
        return SourceInput(THREAD, path or self.path, self.roots[0], self.target.project.canonical_repo)

    def missing_locations(self, count):
        self.repo.register_session(CallerIdentity(THREAD))
        ids = []
        for index in range(count):
            source = self.source(self.path.parent / f"rollout-missing{index}-{THREAD}.jsonl")
            self.repo.remember_locator(locator_from_source(source, self.roots))
            ids.append(source.source_id)
        return ids

    def finish(self, count=35):
        for _ in range(count):
            result = self.run_slice()
            if result and result["status"] in {"caught_up", "available_complete"}:
                return result
        self.fail(f"work did not finish: {self.read()}")

    def test_many_missing_and_repeated_intake_preserve_frontier_and_numbers(self):
        ids = self.missing_locations(64)
        self.intake()
        collect = self.repo.collect_source
        with mock.patch.object(self.repo, "collect_source", side_effect=lambda *args, **kwargs:
                collect(*args, **{**kwargs, "max_records": 3})):
            for index in range(24):
                if index in (4, 8, 12, 16):
                    before = self.read()
                    newer = self.intake()
                    self.assertEqual(newer["round"], before["round"])
                    self.assertNotEqual(newer["request"], before["request"])
                    self.assertIsNone(state.update(self.target, before, round_=before["round"], status="pending"))
                self.run_slice()
                if self.repo.summary()["models"] and self.repo.summary()["models"][0]["response_count"] == 20:
                    break
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        result = self.finish()
        self.assertEqual(result["status"], "available_complete")
        self.assertEqual(set(result["missing"]), set(ids))
        before = self.target.resolved_usage_database.read_bytes()
        with mock.patch.object(self.repo, "collect_source", side_effect=AssertionError("parked body read")):
            self.assertEqual(self.run_slice(), result)
        self.assertEqual(self.target.resolved_usage_database.read_bytes(), before)

    def test_all_missing_finishes_obtainable_work_without_zero_measurement(self):
        self.path.rename(self.path.with_suffix(".held"))
        self.intake()
        result = self.finish()
        self.assertEqual(result["status"], "available_complete")
        self.assertEqual(self.repo.summary()["models"], [])
        self.assertEqual(len(result["missing"]), 1)
        self.assertTrue(self.target.resolved_usage_index.exists())

    def test_known_partial_numbers_and_gap_survive_missing_then_reappearance(self):
        self.intake()
        self.repo.collect_source(self.source(), max_records=5)
        partial = self.repo.summary()["models"][0]["response_count"]
        held = self.path.with_suffix(".held")
        self.path.rename(held)
        result = self.finish()
        self.assertEqual(result["status"], "available_complete")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], partial)
        self.assertIn("source_unreadable", self.repo.summary()["diagnostics"])
        held.rename(self.path)
        self.repo = repository_for(self.target)
        self.assertEqual(self.run_slice()["status"], "available_complete")
        due = max(result["missing"].values())
        with mock.patch.object(missing, "time", return_value=due):
            result = self.finish()
        self.assertEqual(result["status"], "caught_up")
        self.assertEqual(result["missing"], {})
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_old_parked_request_cannot_acknowledge_new_intake(self):
        self.path.rename(self.path.with_suffix(".held"))
        self.intake()
        previous = self.finish()
        self.intake()
        self.assertIsNone(self.run_slice(previous))

    def test_fresh_hint_recovers_before_cooldown_even_when_hook_execution_is_busy(self):
        held = self.path.with_suffix(".held")
        self.path.rename(held)
        self.intake()
        old = self.finish()
        self.assertEqual(old["status"], "available_complete")
        held.rename(self.path)
        current = self.intake()  # This Hook deliberately cannot acquire execution.
        self.assertEqual(current["missing"], {})
        self.assertEqual(self.finish()["status"], "caught_up")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_service_restart_preserves_unfinished_frontier_and_pending_request(self):
        self.intake()
        saved = self.source_pass()
        observer = self.observer()
        observed = []
        with mock.patch.object(observer, "_observe", side_effect=lambda *_: observed.append(self.read())):
            observer._run()
        self.assertEqual(observed, [saved])

    def test_parked_service_checks_intent_without_numerical_admission(self):
        self.path.rename(self.path.with_suffix(".held"))
        self.intake()
        self.finish()
        observer = self.observer()
        with mock.patch.object(observer, "_admit", side_effect=AssertionError("parked admission")), \
                mock.patch.object(observer._stopped, "wait", side_effect=lambda _: observer._stopped.set()):
            observer._observe(self.target, self.roots)

    def test_rotated_queue_extends_ceiling_without_resetting_completed_prefix(self):
        self.intake()
        original = self.source_pass()
        entry = self.repo.locator_page(locator_root_fingerprints(self.roots))[0]
        round_ = {**original["round"], "after_order": entry.queue_order,
                  "after_source": entry.locator.source_id}
        saved = state.update(self.target, original, round_=round_, status="pending")
        self.repo.begin_locator_attempt(entry)
        from task_governance_tool import usage_continuation_repository as progress
        extended = progress.extend_sources(self.repo, round_)
        self.assertGreater(extended["through"], round_["through"])
        self.assertEqual(extended["after_order"], round_["after_order"])
        self.assertEqual(extended["after_source"], round_["after_source"])
        self.assertEqual(self.finish()["status"], "caught_up")

    def test_reappearing_wrong_header_does_not_become_collected(self):
        self.path.rename(self.path.with_suffix(".held"))
        self.intake()
        previous = self.finish()
        self.path.write_bytes(b'{"type":"session_meta","payload":{"id":"wrong"}}\n')
        with mock.patch.object(missing, "time", return_value=max(previous["missing"].values())):
            for _ in range(5):
                result = self.run_slice()
                self.assertNotIn(result["status"], {"caught_up", "available_complete"})
        self.assertEqual(self.repo.summary()["models"], [])
        self.assertEqual(result["missing"], {})

    def test_archive_enqueued_during_source_pass_prevents_early_completion(self):
        self.intake()
        self.repo.collect_source(self.source(), max_records=5)
        self.source_pass()
        archive = self.host / "archived_sessions"
        archive.mkdir()
        self.path.rename(archive / self.path.name)
        first = self.run_slice()
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["round"]["phase"], "sources")
        result = self.finish()
        self.assertEqual(result["status"], "available_complete")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertIn("source_unreadable", self.repo.summary()["diagnostics"])

    def test_new_hook_does_not_reset_missing_delay_and_skips_deferred_bodies(self):
        self.missing_locations(4)
        self.intake()
        done = self.finish()
        calls = []
        original = self.repo.collect_source
        def collect(source, **kwargs):
            calls.append(source.source_id)
            return original(source, **kwargs)
        with mock.patch.object(self.repo, "collect_source", side_effect=collect):
            continuation.collect_hook(self.repo, self.target, self.payload, self.roots,
                                      self.target.project.canonical_repo, THREAD, monotonic() + 20)
        self.assertFalse(set(calls) & set(done["missing"]))
        self.assertEqual(self.read()["missing"], done["missing"])
        self.assertNotEqual(self.read()["request"], done["request"])

    def test_unreadable_present_file_is_not_classified_as_absent(self):
        self.intake()
        self.source_pass()
        with mock.patch.object(self.repo, "collect_source", side_effect=UsageError("source_unreadable")):
            result = self.run_slice()
        self.assertEqual(result["status"], "waiting_input")
        self.assertEqual(result["missing"], {})

    def test_missing_locations_cannot_hide_an_entirely_unlocated_participant(self):
        self.missing_locations(2)
        self.intake()
        unlocated = "01234567-89ab-7cde-8fab-0123456789ac"
        with mock.patch("task_governance_tool.usage_locator_collection._participants", return_value={THREAD, unlocated}):
            results = [self.run_slice() for _ in range(10)]
        self.assertNotIn("caught_up", [item["status"] for item in results])
        self.assertNotIn("available_complete", [item["status"] for item in results])
        self.assertIn("waiting_input", [item["status"] for item in results])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_partial_tail_remains_unfinished_even_with_other_missing_logs(self):
        self.missing_locations(2)
        self.write(tail=b'{"unfinished":')
        self.intake()
        for _ in range(8):
            result = self.run_slice()
            self.assertNotIn(result["status"], {"caught_up", "available_complete"})
        self.assertTrue(result["missing"])

    def test_v1_read_is_normalized_without_rewriting_and_future_fields_rejected(self):
        saved = self.intake()
        legacy = {key: value for key, value in saved.items() if key != "missing"}
        legacy["v"] = 1
        path = self.target.resolved_usage_runtime / "continuation.json"
        data = json.dumps(legacy).encode("ascii")
        path.write_bytes(data)
        result = self.read()
        self.assertEqual(result["v"], 2)
        self.assertEqual(result["missing"], {})
        self.assertEqual(path.read_bytes(), data)
        with self.assertRaises(UsageError):
            state.validate({**result, "unknown": True})
        with self.assertRaises(UsageError):
            state.validate({**result, "missing": {"a" * 64: True}})

    def test_clock_rollback_and_capacity_never_hide_work(self):
        identity = "a" * 64
        with mock.patch.object(missing, "time", return_value=1000):
            values = {}
            self.assertTrue(missing.retain(values, identity))
            self.assertTrue(missing.deferred(values, identity))
        self.assertFalse(missing.deferred(values, identity, now=999))
        self.assertFalse(missing.deferred(values, identity, now=1600))
        with mock.patch.object(missing, "MAX_MISSING", 1):
            self.assertFalse(missing.retain(values, "b" * 64))
        self.assertEqual(set(values), {identity})


if __name__ == "__main__":
    unittest.main()
