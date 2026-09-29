"""Numerical correctness, privacy and atomic replay with synthetic local logs."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))

from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.state_resolver import observe_current_root
from task_governance_tool.usage_adapter import Cursor, SourceInput, read_batch
from task_governance_tool.usage_collection import collect_registered, register_source
from task_governance_tool.usage_repository import UsageRepository
from task_governance_tool.usage_values import METRICS, ResponseUsage, UsageError


THREAD = "01234567-89ab-7cde-8fab-0123456789ab"
PARENT = "11234567-89ab-7cde-8fab-0123456789ab"
TURN = "21234567-89ab-7cde-8fab-0123456789ab"
TURN2 = "31234567-89ab-7cde-8fab-0123456789ab"
COUNTS = dict(zip(METRICS, (100, 20, 120, 60, 5, 10)))


def event(kind, **payload):
    return {"type": kind, "payload": payload}


def usage(response="resp_1", *, thread=THREAD, turn=TURN, counts=None):
    return event("token_usage_record", thread_id=thread, turn_id=turn,
                 response_id=response, usage=COUNTS if counts is None else counts)


class UsageCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.source = SourceInput(THREAD, self.logs / "selected.jsonl", self.logs, self.root)
        self.repository = UsageRepository(self.root / "usage.sqlite", "fixture-project",
                                          observe_current_root(self.root).canonical_path_hash, 1)
        self.repository.initialize()
        register_source(self.repository, self.source, CallerIdentity(THREAD))

    def records(self, *extra, thread=THREAD):
        return [event("session_meta", id=thread, cwd=str(self.root), model_provider="openai"),
                event("turn_context", turn_id=TURN, model="fixture-model", effort="high"), *extra]

    def write(self, records, *, path=None):
        (path or self.source.path).write_bytes(b"".join(
            json.dumps(row).encode() + b"\n" for row in records))

    def append(self, *records):
        with self.source.path.open("ab") as stream:
            for row in records:
                stream.write(json.dumps(row).encode() + b"\n")

    def collect(self):
        result = collect_registered(self.repository, self.source)
        self.assertNotEqual(result["status"], "unknown", result)
        return result

    def test_known_totals_subsets_and_identical_replay(self):
        self.write(self.records(usage(), usage()))
        report = self.collect()
        self.assertEqual(report["models"][0]["response_count"], 1)
        for name in METRICS:
            self.assertEqual(report["models"][0][name], COUNTS[name])
        self.assertEqual(self.collect(), report)
        self.append(usage("resp_2"))
        later = self.collect()
        self.assertEqual(later["models"][0]["total_tokens"], 240)

    def test_models_remain_separate_and_missing_breakdown_is_null(self):
        self.write(self.records(usage(), event("turn_context", turn_id=TURN2, model="second-model"),
                                usage("resp_2", turn=TURN2, counts={k: COUNTS[k] for k in METRICS[:3]})))
        models = self.collect()["models"]
        self.assertEqual(len(models), 2)
        self.assertEqual(models[1]["total_tokens"], 120)
        self.assertIsNone(models[1]["cached_input_tokens"])

    def test_unknown_model_not_fabricated(self):
        self.write([self.records()[0], usage()])
        result = self.collect()
        self.assertIsNone(result["models"][0]["model"])
        self.assertIn("model_unknown", result["diagnostics"])

    def test_empty_is_not_measured_zero_or_complete(self):
        self.write(self.records())
        result = self.collect()
        self.assertEqual(result["models"], [])
        self.assertEqual(result["status"], "pending")

    def test_parent_history_is_excluded(self):
        self.write(self.records(usage("parent", thread=PARENT), usage()))
        self.assertEqual(self.collect()["models"][0]["response_count"], 1)

    def test_split_segment_is_deduplicated(self):
        self.write(self.records(usage()))
        self.collect()
        split = replace(self.source, path=self.logs / "segment-2.jsonl")
        self.write(self.records(usage(), usage("resp_2")), path=split.path)
        register_source(self.repository, split, CallerIdentity(THREAD))
        result = collect_registered(self.repository, split)
        self.assertEqual(result["models"][0]["response_count"], 2)

    def test_conflicting_counter_or_model_is_not_last_writer_wins(self):
        for number, changed in enumerate((usage(counts={**COUNTS, "cached_input_tokens": 61}),
                                         event("turn_context", turn_id=TURN, model="different"))):
            with self.subTest(changed=changed["type"]):
                identity = f"resp_{number}"
                if changed["type"] == "token_usage_record":
                    changed["payload"]["response_id"] = identity
                self.write(self.records(usage(identity)))
                self.collect()
                self.append(changed, usage(identity))
                result = self.collect()
                self.assertEqual(result["status"], "conflicting")
                self.assertEqual(result["models"], [])
                self.assertEqual(result["conflicting_responses"], number + 1)

    def test_conflicting_owner_across_registered_sources(self):
        self.write(self.records(usage()))
        self.collect()
        other = replace(self.source, thread_id=PARENT, path=self.logs / "parent.jsonl")
        self.write(self.records(usage(thread=PARENT), thread=PARENT), path=other.path)
        register_source(self.repository, other, CallerIdentity(PARENT))
        result = collect_registered(self.repository, other)
        self.assertEqual(result["conflicting_responses"], 1)
        self.assertEqual(result["models"], [])

    def test_partial_tail_waits_without_advancing_cursor(self):
        self.write(self.records(usage()))
        complete = self.source.path.read_bytes()
        partial = json.dumps(usage("resp_2")).encode()
        with self.source.path.open("ab") as stream:
            stream.write(partial[:30])
        result = self.collect()
        self.assertIn("partial_tail", result["diagnostics"])
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).offset, len(complete))
        with self.source.path.open("ab") as stream:
            stream.write(partial[30:] + b"\n")
        result = self.collect()
        self.assertEqual(result["models"][0]["response_count"], 2)
        self.assertNotIn("partial_tail", result["diagnostics"])

    def test_truncate_and_same_length_prefix_replacement_replay(self):
        self.write(self.records(usage("resp_1"), usage("resp_2")))
        self.collect()
        self.write(self.records(usage("resp_1"), usage("resp_3")))
        result = self.collect()
        self.assertEqual(result["models"][0]["response_count"], 3)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).incarnation, 2)
        self.write(self.records(usage("resp_1")))
        result = self.collect()
        self.assertEqual(result["models"][0]["response_count"], 3)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).incarnation, 3)

    def test_replaced_physical_file_replays_without_double_count(self):
        self.write(self.records(usage()))
        self.collect()
        replacement = self.logs / "replacement.jsonl"
        self.write(self.records(usage()), path=replacement)
        replacement.replace(self.source.path)
        self.assertEqual(self.collect()["models"][0]["response_count"], 1)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD).incarnation, 2)

    def test_invalid_counters_are_gaps_not_zero(self):
        invalid = [None, {**COUNTS, "total_tokens": 1}, {**COUNTS, "input_tokens": True},
                   {**COUNTS, "input_tokens": -1}, {**COUNTS, "reasoning_output_tokens": 21},
                   {**COUNTS, "input_tokens": 2**64}, {**COUNTS, "cached_input_tokens": "60"}]
        self.write(self.records(*(event("token_usage_record", thread_id=THREAD, turn_id=TURN,
                                       response_id=f"r{i}", usage=value) for i, value in enumerate(invalid))))
        result = self.collect()
        self.assertEqual(result["models"], [])
        self.assertEqual(result["status"], "incomplete")

    def test_legacy_cumulative_is_gap_not_delta(self):
        self.write(self.records(event("event_msg", type="token_count", info={"total_token_usage": COUNTS})))
        result = self.collect()
        self.assertEqual(result["models"], [])
        self.assertIn("legacy_usage", result["diagnostics"])

    def test_mixed_turn_legacy_gap_is_not_hidden_by_other_modern_turn(self):
        self.write(self.records(usage(), event("turn_context", turn_id=TURN2, model="second-model"),
                                event("event_msg", type="token_count", info={"total_token_usage": COUNTS})))
        result = self.collect()
        self.assertIn("legacy_usage", result["diagnostics"])
        self.assertEqual(result["models"][0]["total_tokens"], 120)

    def test_parent_legacy_and_current_modern_mirror_do_not_add_usage(self):
        self.write(self.records(event("session_meta", id=PARENT),
                                event("turn_context", turn_id=TURN2, model="parent-model"),
                                event("event_msg", type="token_count", info={"total_token_usage": COUNTS}),
                                usage(), event("event_msg", type="token_count", info={"total_token_usage": COUNTS})))
        result = self.collect()
        self.assertNotIn("legacy_usage", result["diagnostics"])
        self.assertEqual(result["models"][0]["total_tokens"], 120)

    def test_delayed_previous_turn_response_does_not_hide_current_gap(self):
        self.write(self.records(event("turn_context", turn_id=TURN2, model="second-model"),
                                usage(), event("event_msg", type="token_count")))
        result = self.collect()
        self.assertIn("legacy_usage", result["diagnostics"])
        self.assertEqual(result["models"][0]["total_tokens"], 120)
        self.append(usage("resp_2", turn=TURN2))
        result = self.collect()
        self.assertNotIn("legacy_usage", result["diagnostics"])
        self.assertEqual(sum(row["response_count"] for row in result["models"]), 2)

    def test_bounded_delayed_response_keeps_active_turn_context(self):
        self.write(self.records(event("turn_context", turn_id=TURN2, model="second-model"),
                                usage(), event("event_msg", type="token_count")))
        with mock.patch("task_governance_tool.usage_adapter.MAX_BATCH_RECORDS", 1):
            self.assertIn("collection_pending", self.collect()["diagnostics"])
            result = self.collect()
            self.assertIn("legacy_usage", result["diagnostics"])
            self.append(usage("resp_2", turn=TURN2))
            result = self.collect()
        reference = UsageRepository(self.root / "one-pass.sqlite", *self.repository.basis)
        reference.initialize()
        register_source(reference, self.source, CallerIdentity(THREAD))
        self.assertEqual(result, collect_registered(reference, self.source))
        self.assertNotIn("legacy_usage", result["diagnostics"])

    def test_delayed_modern_usage_clears_legacy_gap(self):
        self.write(self.records(event("event_msg", type="token_count")))
        self.assertIn("legacy_usage", self.collect()["diagnostics"])
        self.append(usage())
        result = self.collect()
        reference = UsageRepository(self.root / "one-pass.sqlite", *self.repository.basis)
        reference.initialize()
        register_source(reference, self.source, CallerIdentity(THREAD))
        self.assertEqual(result, collect_registered(reference, self.source))
        self.assertEqual(result["status"], "pending")
        self.assertNotIn("legacy_usage", result["diagnostics"])
        self.assertEqual(self.collect(), result)

    def test_invalid_explicit_context_is_a_gap_not_silently_inherited(self):
        for context in (event("turn_context", turn_id="invalid"),
                        event("session_meta", id="invalid"),
                        {"type": "turn_context", "payload": []}):
            with self.subTest(context_type=context["type"]):
                self.write(self.records(usage(), context, event("event_msg", type="token_count")))
                result = self.collect()
                self.assertIn("invalid_record", result["diagnostics"])
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["models"][0]["response_count"], 1)

    def test_bounded_catchup_keeps_other_unresolved_legacy_turn(self):
        self.write(self.records(event("event_msg", type="token_count"),
                                event("turn_context", turn_id=TURN2, model="fixture-model", effort="high"),
                                event("event_msg", type="token_count")))
        self.assertIn("legacy_usage", self.collect()["diagnostics"])
        self.append(usage(), usage("resp_2", turn=TURN2))
        with mock.patch("task_governance_tool.usage_adapter.MAX_BATCH_RECORDS", 1):
            first = self.collect()
            self.assertIn("legacy_usage", first["diagnostics"])
            self.assertIn("collection_pending", first["diagnostics"])
            result = self.collect()
        reference = UsageRepository(self.root / "one-pass.sqlite", *self.repository.basis)
        reference.initialize()
        register_source(reference, self.source, CallerIdentity(THREAD))
        self.assertEqual(result, collect_registered(reference, self.source))
        self.assertEqual(result["models"][0]["response_count"], 2)
        self.assertNotIn("legacy_usage", result["diagnostics"])

    def test_legacy_gap_resolution_rolls_back_and_preserves_other_gaps(self):
        self.write(self.records(event("event_msg", type="token_count"), usage("invalid", counts={})))
        original = self.collect()
        self.assertIn("legacy_usage", original["diagnostics"])
        self.assertIn("invalid_record", original["diagnostics"])
        self.append(usage())
        batch = read_batch(self.source, self.repository.cursor(self.source.source_id, THREAD))
        with mock.patch.object(self.repository, "_before_cursor", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.repository.commit_batch(batch)
        self.assertEqual(self.repository.summary(), original)
        self.repository.commit_batch(batch)
        result = self.repository.summary()
        self.assertNotIn("legacy_usage", result["diagnostics"])
        self.assertIn("invalid_record", result["diagnostics"])
        self.assertEqual(result["models"][0]["total_tokens"], 120)

    def test_unregistered_source_is_not_read_and_caller_cannot_register_other(self):
        source = replace(self.source, path=self.logs / "unregistered.jsonl")
        with mock.patch("task_governance_tool.usage_collection.read_batch") as read:
            self.assertEqual(collect_registered(self.repository, source)["status"], "unknown")
            read.assert_not_called()
        with self.assertRaises(UsageError):
            register_source(self.repository, source, CallerIdentity(PARENT))

    def test_foreign_header_project_and_outside_path_rejected(self):
        for header in (event("session_meta", id=PARENT, cwd=str(self.root), model_provider="openai"),
                       event("session_meta", id=THREAD, cwd=str(self.logs), model_provider="openai")):
            self.write([header, usage()])
            self.assertEqual(collect_registered(self.repository, self.source)["status"], "unknown")
        outside = replace(self.source, path=self.root / "outside.jsonl")
        self.write(self.records(usage()), path=outside.path)
        with self.assertRaises(UsageError):
            read_batch(outside)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), Cursor())

    def test_private_content_never_saved_or_hashed(self):
        secret = "PRIVATE_BODY_MARKER_DO_NOT_RETAIN"
        row = usage()
        row["payload"]["extra"] = secret
        self.write(self.records(event("response_item", content=secret), row))
        self.collect()
        self.assertNotIn(secret.encode(), self.repository.path.read_bytes())
        cursor = self.repository.cursor(self.source.source_id, THREAD)
        changed = self.source.path.read_bytes().replace(secret.encode(), b"X" * len(secret))
        self.source.path.write_bytes(changed)
        replay = read_batch(self.source, cursor)
        self.assertEqual(replay.successor.prefix, cursor.prefix)
        self.assertEqual(replay.successor.incarnation, cursor.incarnation)

    def test_transaction_fault_rolls_back_observations_and_cursor(self):
        self.write(self.records(usage()))
        batch = read_batch(self.source)
        with mock.patch.object(self.repository, "_before_cursor", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.repository.commit_batch(batch)
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), Cursor())
        self.assertEqual(self.repository.summary()["models"], [])
        self.repository.commit_batch(batch)
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 1)

    def test_two_collectors_serialize_and_old_cursor_cannot_regress(self):
        self.write(self.records(usage()))
        batch = read_batch(self.source)
        def commit():
            try:
                self.repository.commit_batch(batch)
                return "committed"
            except UsageError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(lambda _: commit(), range(2))), ["committed", "cursor_stale"])
        self.append(usage("resp_2"))
        self.collect()
        self.assertEqual(commit(), "cursor_stale")
        self.assertEqual(self.repository.summary()["models"][0]["response_count"], 2)

    def test_read_only_missing_invalid_and_query_only(self):
        before = self.repository.path.read_bytes()
        self.repository.inspect()
        self.repository.summary()
        self.assertEqual(self.repository.path.read_bytes(), before)
        with self.repository.connection() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM usage_sources")
        absent = UsageRepository(self.root / "absent.sqlite", *self.repository.basis)
        self.assertEqual(absent.inspect(), "not_present")
        with self.assertRaises(UsageError):
            absent.summary()
        self.assertFalse(absent.path.exists())

    def test_initialization_reentry_and_failed_publication_replay(self):
        self.assertEqual(self.repository.initialize(), "current")
        new = UsageRepository(self.root / "new.sqlite", *self.repository.basis)
        with mock.patch("task_governance_tool.usage_repository.rename_no_replace", side_effect=OSError):
            with self.assertRaises(UsageError):
                new.initialize()
        self.assertFalse(new.path.exists())
        self.assertFalse(list(self.root.glob(".taskgov-usage-*")))
        self.assertEqual(new.initialize(), "initialized")

    def test_binding_change_and_corruption_do_not_repair(self):
        wrong = UsageRepository(self.repository.path, "other-project", "a" * 64, 1)
        before = self.repository.path.read_bytes()
        with self.assertRaises(UsageError):
            wrong.initialize()
        self.assertEqual(self.repository.path.read_bytes(), before)
        with closing(sqlite3.connect(self.repository.path)) as connection:
            connection.execute("DROP TABLE usage_migrations")
            connection.commit()
        damaged = self.repository.path.read_bytes()
        with self.assertRaises(UsageError):
            self.repository.initialize()
        self.assertEqual(self.repository.path.read_bytes(), damaged)

    def test_newer_usage_version_is_not_downgraded(self):
        with closing(sqlite3.connect(self.repository.path)) as connection:
            connection.execute("DROP TABLE usage_migrations")
            connection.execute("CREATE TABLE usage_migrations(version INTEGER PRIMARY KEY,name TEXT)")
            connection.execute("INSERT INTO usage_migrations VALUES (2,'future')")
            connection.commit()
        before = self.repository.path.read_bytes()
        with self.assertRaises(UsageError):
            self.repository.initialize()
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_corrupt_cursor_returns_unknown_without_reading_source(self):
        self.write(self.records(usage()))
        self.collect()
        for field, invalid in (("offset", "invalid"), ("incarnation", 1.5),
                               ("prefix", "invalid"), ("file_id", "invalid")):
            with self.subTest(field=field):
                with closing(sqlite3.connect(self.repository.path)) as connection:
                    original = connection.execute(
                        f"SELECT {field} FROM usage_sources").fetchone()[0]
                    connection.execute(f"UPDATE usage_sources SET {field}=?", (invalid,))
                    connection.commit()
                with mock.patch("task_governance_tool.usage_collection.read_batch") as read:
                    result = collect_registered(self.repository, self.source)
                    self.assertEqual(result["status"], "unknown")
                    self.assertEqual(result["models"][0]["total_tokens"], 120)
                    self.assertIn("usage_schema_invalid", result["diagnostics"])
                    read.assert_not_called()
                with closing(sqlite3.connect(self.repository.path)) as connection:
                    self.assertEqual(connection.execute(
                        f"SELECT {field} FROM usage_sources").fetchone()[0], invalid)
                    connection.execute(f"UPDATE usage_sources SET {field}=?", (original,))
                    connection.commit()

    def test_schema_transaction_failure_leaves_no_live_partial_store(self):
        other = UsageRepository(self.root / "other.sqlite", *self.repository.basis)
        with mock.patch.object(other, "_validate", side_effect=UsageError):
            with self.assertRaises(UsageError):
                other.initialize()
        self.assertFalse(other.path.exists())
        self.assertFalse(list(self.root.glob(".taskgov-usage-*")))
        self.assertEqual(other.initialize(), "initialized")

    def test_busy_writer_preserves_cursor_and_replays_later(self):
        self.write(self.records(usage()))
        batch = read_batch(self.source)
        with closing(sqlite3.connect(self.repository.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            from task_governance_tool import usage_repository
            original = usage_repository.connect_existing
            def short_wait(path):
                opened = original(path)
                opened.execute("PRAGMA busy_timeout=1")
                return opened
            with mock.patch.object(usage_repository, "connect_existing", side_effect=short_wait):
                with self.assertRaises(UsageError):
                    self.repository.commit_batch(batch)
            connection.rollback()
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), Cursor())
        self.repository.commit_batch(batch)
        self.assertEqual(self.repository.summary()["models"][0]["total_tokens"], 120)

    def test_wal_is_refused_without_checkpoint_or_removal(self):
        sidecar = Path(str(self.repository.path) + "-wal")
        sidecar.write_bytes(b"preserve")
        with self.assertRaises(UsageError):
            self.repository.summary()
        self.assertEqual(sidecar.read_bytes(), b"preserve")

    def test_absent_store_with_orphan_journal_is_not_initialized(self):
        absent = UsageRepository(self.root / "absent.sqlite", *self.repository.basis)
        journal = Path(str(absent.path) + "-journal")
        journal.write_bytes(b"preserve orphan")
        with self.assertRaises(UsageError):
            absent.initialize()
        self.assertFalse(absent.path.exists())
        self.assertEqual(journal.read_bytes(), b"preserve orphan")

    def test_lost_source_keeps_observed_totals_and_reports_unknown(self):
        self.write(self.records(usage()))
        self.collect()
        self.source.path.unlink()
        result = collect_registered(self.repository, self.source)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["models"][0]["total_tokens"], 120)
        self.assertIn("source_unreadable", self.repository.summary()["diagnostics"])

    def test_bounded_batch_resume_and_oversized_record(self):
        self.write(self.records(*(usage(f"r{i}") for i in range(5))))
        with mock.patch("task_governance_tool.usage_adapter.MAX_BATCH_RECORDS", 2):
            self.assertEqual(self.collect()["models"][0]["response_count"], 2)
            self.assertEqual(self.collect()["models"][0]["response_count"], 4)
            self.assertEqual(self.collect()["models"][0]["response_count"], 5)
        self.write(self.records(event("response_item", content="x" * 2048), usage()))
        with mock.patch("task_governance_tool.usage_adapter.MAX_LINE", 512):
            result = self.collect()
        self.assertIn("record_too_large", result["diagnostics"])

    def test_core_binding_is_required_even_if_header_matches_other_root(self):
        wrong = replace(self.source, project_root=self.logs)
        with self.assertRaises(UsageError):
            register_source(self.repository, wrong, CallerIdentity(THREAD))
        self.assertEqual(collect_registered(self.repository, wrong)["status"], "unknown")

    def test_source_change_during_read_does_not_advance(self):
        self.write(self.records(usage()))
        from task_governance_tool import usage_adapter
        original = usage_adapter.inspect_physical_file
        calls = 0
        def inspect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.append(usage("late"))
            return original(*args, **kwargs)
        with mock.patch.object(usage_adapter, "inspect_physical_file", side_effect=inspect):
            self.assertEqual(collect_registered(self.repository, self.source)["status"], "unknown")
        self.assertEqual(self.repository.cursor(self.source.source_id, THREAD), Cursor())
        self.assertEqual(self.collect()["models"][0]["response_count"], 2)


if __name__ == "__main__":
    unittest.main()
