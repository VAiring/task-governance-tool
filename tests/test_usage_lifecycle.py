"""Trusted transport, registered-only discovery, incremental replay and isolation."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.test_usage_collection import THREAD, PARENT, TURN, event, usage
from task_governance_tool import usage_lifecycle as lifecycle
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.state_resolver import observe_current_root
from task_governance_tool.usage_evidence_repository import UsageEvidenceRepository
from task_governance_tool.usage_values import UsageError


class UsageLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.host = self.root / "host"
        self.logs = self.host / "sessions/2026/10/01"
        self.logs.mkdir(parents=True)
        self.path = self.logs / f"rollout-example-{THREAD}.jsonl"
        self.repo = UsageEvidenceRepository(self.project / "usage.sqlite", "fixture-project",
                   observe_current_root(self.project).canonical_path_hash, 1)
        self.repo.initialize()
        self.target = mock.Mock()
        self.target.project.project_id = "fixture-project"
        for name, value in (
            ("_target", mock.Mock(return_value=self.target)),
            ("repository_for", mock.Mock(return_value=self.repo)),
            ("connect_initialized_readonly", mock.Mock(return_value=mock.Mock())),
            ("registered_participants", mock.Mock(return_value=frozenset())),
            ("refresh_usage", mock.Mock(return_value={"diagnostics": []})),
        ):
            patch = mock.patch.object(lifecycle, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.write(self.path, THREAD, usage())

    def write(self, path, thread, *rows, project=None):
        path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in [
            event("session_meta", id=thread, cwd=str(project or self.project), model_provider="openai"),
            event("turn_context", turn_id=TURN, model="test-model"), *rows]))

    def payload(self, kind="SessionStart", **changes):
        return {"hook_event_name": kind, "session_id": THREAD, "cwd": str(self.project),
                "transcript_path": str(self.path), **changes}

    def collect(self, payload=None):
        return lifecycle.collect_event(self.payload() if payload is None else payload,
              skill_root=self.project, repo=self.project, environment={"CODEX_HOME": str(self.host)})

    def test_start_registers_self_stop_replays_no_duplicate_or_final_claim(self):
        self.assertEqual(self.collect()["collected_sources"], 1)
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 120)
        self.assertEqual(self.collect(self.payload("Stop"))["status"], "pending")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 1)
        self.assertEqual(self.repo.registered_sources()[0], {THREAD})

    def test_saved_off_prevents_later_collection_and_preserves_accumulated_history(self):
        self.collect()
        before = self.repo.summary()
        self.assertEqual(before["models"][0]["total_tokens"], 120)
        settings = self.project / "config"
        settings.mkdir()
        (settings / "setup-features.json").write_text(json.dumps({
            "schema_version": 1, "choices": {"usage_collection": False}}), encoding="utf-8")
        self.write(self.path, THREAD, usage(), usage("late"))
        with mock.patch.object(lifecycle, "read_batch") as read:
            self.assertEqual(self.collect(self.payload("Stop"))["collected_sources"], 0)
        read.assert_not_called()
        self.assertEqual(self.repo.summary(), before)

    def test_unregistered_stop_and_descendant_do_not_read_headers(self):
        with mock.patch.object(lifecycle, "read_batch", wraps=lifecycle.read_batch) as read:
            self.collect(self.payload("Stop"))
            self.collect(self.payload("SubagentStop", session_id=PARENT, agent_id=THREAD,
                                      agent_transcript_path=str(self.path)))
        read.assert_not_called()
        self.assertEqual(self.repo.registered_sources(), (frozenset(), {}))

    def test_core_owner_or_actual_reviewer_registration_is_collected(self):
        lifecycle.registered_participants.return_value = frozenset({THREAD})
        self.assertEqual(self.collect(self.payload("Stop"))["collected_sources"], 1)
        lifecycle.registered_participants.return_value = frozenset()
        self.assertEqual(self.collect(self.payload("Stop"))["collected_sources"], 1)

    def test_child_identity_is_not_parent_and_no_implicit_children(self):
        self.repo.register_session(CallerIdentity(THREAD))
        other = self.logs / f"rollout-other-{PARENT}.jsonl"
        self.write(other, PARENT, usage(thread=PARENT))
        self.collect(self.payload("SubagentStop", session_id=PARENT, agent_id=THREAD,
                                  transcript_path=str(other), agent_transcript_path=str(self.path)))
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 1)
        self.assertEqual(self.repo.registered_sources()[0], {THREAD})

    def test_late_tail_caught_by_another_registered_session_start(self):
        self.collect()
        late = json.dumps(usage("late")).encode() + b"\n"
        with self.path.open("ab") as stream:
            stream.write(late[:20])
        self.collect(self.payload("Stop"))
        self.assertIn("partial_tail", self.repo.summary()["diagnostics"])
        with self.path.open("ab") as stream:
            stream.write(late[20:])
        other = self.logs / f"rollout-other-{PARENT}.jsonl"
        self.write(other, PARENT)
        self.collect(self.payload(session_id=PARENT, transcript_path=str(other)))
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 240)
        self.assertNotIn("partial_tail", self.repo.summary()["diagnostics"])

    def test_filename_does_not_override_header_project_or_thread(self):
        for changes in ({"thread": PARENT}, {"thread": THREAD, "project": self.root}):
            self.write(self.path, **changes)
            self.assertEqual(self.collect()["collected_sources"], 0)
            self.assertEqual(self.repo.registered_sources()[1], {})

    def test_never_open_unrelated_headers_even_when_named_file_is_invalid(self):
        other = self.logs / f"rollout-private-{PARENT}.jsonl"
        other.write_bytes(b"private-invalid-unrelated-transcript")
        original = lifecycle.read_batch
        calls = []
        def read(source, *args, **kwargs):
            calls.append(source.path)
            return original(source, *args, **kwargs)
        with mock.patch.object(lifecycle, "read_batch", side_effect=read):
            self.collect()
        self.assertEqual(calls, [self.path])

    def test_split_and_legacy_sources_without_hint_replay_once(self):
        archive = self.host / "archived_sessions"
        archive.mkdir()
        for count, directory in enumerate((self.logs, archive), start=2):
            with self.subTest(directory=directory.name):
                segment = directory / f"rollout-2026-10-02T00-00-00-{THREAD}_{TURN}.jsonl"
                self.write(segment, THREAD, usage(), usage("split-response"))
                result = self.collect(self.payload(transcript_path=""))
                self.assertEqual(result["collected_sources"], count)
                self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 240)
                self.collect(self.payload("Stop", transcript_path=""))
                self.assertEqual(self.repo.summary()["models"][0]["response_count"], 2)
        self.assertEqual(self.repo.registered_sources()[0], {THREAD})
        self.assertEqual(set(self.repo.registered_sources()[1].values()), {THREAD})

    def test_segment_id_is_not_a_session_candidate(self):
        for name in (
            f"rollout-2026-10-02T00-00-00-{PARENT}_{THREAD}.jsonl",
            f"rollout-2026-10-02T00-00-00-{PARENT}_{TURN}.jsonl",
            f"rollout-2026-10-02T00-00-00-{THREAD}_invalid-segment.jsonl",
            f"other-{THREAD}_{TURN}.jsonl",
            f"rollout-2026-10-02T00-00-00-{THREAD}_{TURN}.txt",
        ):
            (self.logs / name).write_bytes(b"private-unrelated-body")
        with mock.patch.object(lifecycle, "read_batch", wraps=lifecycle.read_batch) as read:
            self.collect(self.payload(transcript_path=""))
        self.assertEqual([call.args[0].path for call in read.call_args_list], [self.path])

    def test_split_filename_still_requires_matching_header(self):
        self.path.unlink()
        segment = self.logs / f"rollout-2026-10-02T00-00-00-{THREAD}_{TURN}.jsonl"
        for changes in ({"thread": PARENT}, {"thread": THREAD, "project": self.root}):
            with self.subTest(changes=changes):
                self.write(segment, **changes)
                with mock.patch.object(lifecycle, "read_batch", wraps=lifecycle.read_batch) as read:
                    result = self.collect(self.payload(transcript_path=""))
                self.assertEqual(read.call_count, 1)
                self.assertEqual(result["collected_sources"], 0)
                self.assertIn("source_unreadable", result["diagnostics"])
                self.assertEqual(self.repo.registered_sources()[1], {})

    def test_lost_source_preserves_totals_and_records_gap(self):
        self.collect()
        self.path.unlink()
        self.collect(self.payload("Stop"))
        result = self.repo.summary()
        self.assertEqual(result["models"][0]["total_tokens"], 120)
        self.assertIn("source_unreadable", result["diagnostics"])

    def test_unlocatable_registered_session_reports_unavailable_not_measured_zero(self):
        self.path.unlink()
        result = self.collect(self.payload(transcript_path=""))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["diagnostics"], ["source_unreadable"])
        self.assertEqual(self.repo.summary()["models"], [])

    def test_replacement_replay_and_interrupted_commit_recovery(self):
        self.collect()
        self.path.unlink()
        self.write(self.path, THREAD, usage(), usage("new"))
        before = self.repo.registered_sources()
        with mock.patch.object(self.repo, "_before_cursor", side_effect=UsageError()):
            self.assertEqual(self.collect(self.payload("Stop"))["collected_sources"], 0)
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 120)
        self.assertEqual(self.repo.registered_sources(), before)
        self.assertEqual(self.collect(self.payload("Stop"))["collected_sources"], 1)
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 240)

    def test_foreign_cwd_invalid_event_or_child_identity_has_no_write(self):
        initial = self.repo.path.read_bytes()
        for changes in ({"cwd": str(self.root)}, {"hook_event_name": "UserPromptSubmit"},
                        {"hook_event_name": "SubagentStop", "agent_id": "bad"}):
            self.assertEqual(self.collect(self.payload(**changes))["status"], "unknown")
        self.assertEqual(initial, self.repo.path.read_bytes())
        lifecycle.refresh_usage.assert_not_called()

    def test_neutral_protocol_never_emits_raw_input_context_or_retries(self):
        for raw in (b"PRIVATE malformed", json.dumps(self.payload()).encode(), b"x" * (lifecycle.MAX_INPUT + 1)):
            output = io.StringIO()
            with mock.patch.object(lifecycle, "collect_event", side_effect=RuntimeError("PRIVATE")) as collect:
                code = lifecycle.main(stdin=io.BytesIO(raw), stdout=output, skill_root=self.project,
                                      repo=self.project, environment={})
            self.assertEqual(code, 0)
            self.assertEqual(output.getvalue(), "{}\n")
            self.assertLessEqual(collect.call_count, 1)

    def test_saved_reviewer_registration_is_best_effort_without_log_read(self):
        lifecycle.register_reviewer(self.project, CallerIdentity(THREAD))
        self.assertEqual(self.repo.registered_sources(), (frozenset({THREAD}), {}))
        lifecycle.repository_for.side_effect = RuntimeError("PRIVATE")
        lifecycle.register_reviewer(self.project, CallerIdentity(PARENT))
        self.assertEqual(self.repo.registered_sources()[0], {THREAD})

    def test_usage_absent_or_invalid_never_initialized_by_event(self):
        self.repo.path.unlink()
        self.assertEqual(self.collect()["status"], "unknown")
        self.assertFalse(self.repo.path.exists())
        lifecycle.refresh_usage.assert_not_called()

    def test_concurrent_stale_batch_never_rewinds_and_next_event_is_idempotent(self):
        self.collect()
        with self.path.open("ab") as stream:
            stream.write(json.dumps(usage("late")).encode() + b"\n")
        original = lifecycle.read_batch
        def raced(source, *args, **kwargs):
            batch = original(source, *args, **kwargs)
            self.repo.commit_batch(batch)  # Another worker wins after our read.
            return batch
        with mock.patch.object(lifecycle, "read_batch", side_effect=raced):
            result = self.collect(self.payload("Stop"))
        self.assertIn("cursor_stale", result["diagnostics"])
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 240)
        self.assertNotIn("cursor_stale", self.repo.summary()["diagnostics"])
        self.assertEqual(self.collect(self.payload("Stop"))["status"], "pending")
        self.assertEqual(self.repo.summary()["models"][0]["total_tokens"], 240)


if __name__ == "__main__":
    unittest.main()
