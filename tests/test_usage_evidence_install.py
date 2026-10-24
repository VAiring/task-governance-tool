"""Real isolated package: usage never enters core completion or sealed Evidence."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import make_physical_install, file_snapshot
from tests.test_usage_attribution import THREAD, turn
from tests.test_usage_collection import PARENT, event, usage
from tests.test_usage_turn_adapter import tool_record
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.storage import connect_initialized_readonly
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import collect_registered, register_source
from task_governance_tool import usage_evidence_service as service
from task_governance_tool.usage_values import UsageError


class UsageEvidenceInstallTests(unittest.TestCase):
    def test_native_pending_fence_survives_unrelated_organization_and_preserves_history(self):
        b = self.add("Independent blocked work")
        bid = b["data"]["task"]["task_id"]
        blocked = self.cli("task", "edit", bid, "--status", "blocked", "--blocked-reason", "Fixture")
        a = self.add("Pending operation work")
        aid = a["data"]["task"]["task_id"]
        organized = self.cli("task", "edit", bid, "--status", "ready")
        done = self.complete(aid)
        logs = self.root / "logs"
        logs.mkdir()
        source = SourceInput(THREAD, logs / "pending-organize.jsonl", logs, self.root)
        register_source(self.repository, source, CallerIdentity(THREAD))
        prefix = self.response_header(THREAD, 1)
        for number, ack in enumerate((b, blocked, a)):
            prefix += self.response_rows("r" + str(number), 1, ack)
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in prefix))
        self.collect_responses(source)
        old_files = file_snapshot(self.target.resolved_usage_snapshots)
        old_index = json.loads(self.target.resolved_usage_index.read_bytes())
        core, sealed = self.target.db_path.read_bytes(), file_snapshot(self.target.resolved_evidence_root)
        later = self.response_rows("r3", 1, {"opaque": True})
        for number, ack in ((4, None), (5, organized), (6, None), (7, done), (8, None)):
            later += self.response_rows("r" + str(number), 1, ack)
        with source.path.open("ab") as output:
            output.write(b"".join(json.dumps(row).encode() + b"\n" for row in later))
        self.collect_responses(source)
        shown = self.cli("task", "show", aid)["data"]["usage"]
        unit = shown["periods"][0]["work_units"][0]
        self.assertEqual(unit["exclusive"]["response_count"], 3)
        self.assertEqual(sum(part["response_count"] for part in unit["partial"]), 4)
        self.assertEqual(shown["total"]["models"][0]["total_tokens"], 840)
        self.assertEqual(self.cli("task", "show", bid)["data"]["usage"]["total"]["response_count"], 2)
        index = json.loads(self.target.resolved_usage_index.read_bytes())
        self.assertEqual(index["unattributed"]["unknown"]["response_count"], 4)
        self.assertEqual(index["unattributed"]["unassigned"]["response_count"], 0)
        self.assertTrue(old_files.items() <= file_snapshot(self.target.resolved_usage_snapshots).items())
        self.assertTrue({tuple(sorted(row.items())) for row in old_index["cycle_links"]} <=
                        {tuple(sorted(row.items())) for row in index["cycle_links"]})
        before = file_snapshot(self.root)
        self.cli("task", "show", aid)
        self.assertEqual(file_snapshot(self.root), before)
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        self.assertEqual(json.loads(self.target.resolved_usage_index.read_bytes()), index)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), sealed)

    def test_uncertain_stop_policy_upgrade_preserves_history_and_publishes_correct_native_display(self):
        import task_governance_tool.usage_response_attribution as policy
        import task_governance_tool.usage_response_repository as repository_module
        for status in ("ready", "cancelled", "paused"):
            with self.subTest(status=status):
                self.setUp()
                start = self.add("Uncertain stop upgrade")
                task = start["data"]["task"]["task_id"]
                original_execution = self.cli("task", "context")["data"]["selected"]["task"]["ownership"]["execution_id"]
                arguments = ["task", "edit", task, "--status", status]
                if status == "paused":
                    arguments += ["--pause-reason", "Isolated uncertain order"]
                stopped = self.cli(*arguments)
                self.assertEqual(stopped["data"]["task"]["ownership"]["execution_id"],
                                 original_execution if status == "paused" else None)
                resumed = self.cli("task", "edit", task, "--status", "in_progress")
                done = self.complete(task)
                other = self.add("Independent later work")
                other_id = other["data"]["task"]["task_id"]
                logs = self.root / "logs"
                logs.mkdir()
                source = SourceInput(THREAD, logs / "uncertain-upgrade.jsonl", logs, self.root)
                register_source(self.repository, source, CallerIdentity(THREAD))
                rows = self.response_header(THREAD, 1)
                acknowledgements = {0: start, 6: resumed, 7: done, 10: other}
                if status == "paused":
                    acknowledgements[2] = stopped
                for number in range(12):
                    if status == "paused" and number == 2:
                        rows += self.response_header(THREAD, 2, session=False)
                        rows.append(event("event_msg", type="task_started", turn_id=turn(2), started_at=2100))
                    if status == "paused" and number == 4:
                        rows += self.response_header(THREAD, 3, session=False)
                    t = 1 if status != "paused" or number < 2 else 2 if number < 4 else 3
                    rows += self.response_rows("r" + str(number), t, acknowledgements.get(number))
                source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
                while self.repository.cursor(source.source_id, THREAD).offset < source.path.stat().st_size:
                    self.repository.collect_source(source, max_records=1)

                original_project = policy.project_responses
                def prior_projection(changes, *args, **kwargs):
                    # Fixed prior-policy fixture: the documented v2 defect
                    # ignored this unbound release/unordered stop. Numerical
                    # values and the real core history remain unchanged.
                    changes = tuple(change for change in changes
                                    if not (change.task_id == task and change.current_status == status))
                    return original_project(changes, *args, **kwargs)
                with mock.patch.object(policy, "ALGORITHM", "response-partitions-v2"), \
                        mock.patch.object(repository_module, "ALGORITHM", "response-partitions-v2"), \
                        mock.patch.object(repository_module, "project_responses", side_effect=prior_projection):
                    self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
                    with closing(connect_initialized_readonly(self.target)) as core:
                        old = self.repository.read(core, audit=True)
                original_files = file_snapshot(self.target.resolved_usage_snapshots)
                original_index = self.target.resolved_usage_index.read_bytes()
                core_bytes = self.target.db_path.read_bytes()
                evidence = file_snapshot(self.target.resolved_evidence_root)
                before = file_snapshot(self.root)
                unknown = self.cli("task", "show", task)["data"]["usage"]
                self.assertEqual(unknown["status"], "unknown")
                self.assertFalse(unknown["periods"])
                self.assertEqual(file_snapshot(self.root), before)
                self.assertEqual(self.target.resolved_usage_index.read_bytes(), original_index)

                self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
                actual = self.cli("task", "show", task, "--audit")["data"]["usage"]
                unit = next(unit for period in actual["periods"] for unit in period["work_units"]
                            if unit["execution_id"] == original_execution)
                self.assertEqual(unit["exclusive"]["response_count"], 5 if status == "paused" else 1)
                self.assertEqual(sum(part["response_count"] for part in unit["partial"]), 5)
                self.assertEqual(actual["total"]["models"][0]["total_tokens"], 1200)
                self.assertEqual(self.cli("task", "show", other_id)["data"]["usage"]["total"]["response_count"], 2)
                index = json.loads(self.target.resolved_usage_index.read_bytes())
                self.assertEqual(index["unattributed"]["unknown"]["response_count"], 5)
                self.assertFalse(index["unattributed"]["unassigned"]["response_count"])
                self.assertTrue({tuple(sorted(row.items())) for row in old["cycle_links"]} <=
                                {tuple(sorted(row.items())) for row in index["cycle_links"]})
                documents = {identity: json.loads((self.target.resolved_usage_snapshots / (identity + ".json")).read_bytes())
                             for identity in index["snapshots"]}
                self.assertTrue(all(documents[identity]["algorithm"] == "response-partitions-v6"
                                    for identity in index["current_snapshot_ids"]))
                self.assertTrue(any(item["predecessors"] for identity, item in documents.items()
                                    if identity in index["current_snapshot_ids"]))
                self.assertTrue(original_files.items() <= file_snapshot(self.target.resolved_usage_snapshots).items())
                self.assertEqual(self.target.db_path.read_bytes(), core_bytes)
                self.assertEqual(file_snapshot(self.target.resolved_evidence_root), evidence)
                published = self.target.resolved_usage_index.read_bytes()
                self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
                self.assertEqual(self.target.resolved_usage_index.read_bytes(), published)

    def test_native_unrelated_blocked_release_preserves_other_completion_tail(self):
        acknowledgements = {}
        completed = []
        for offset, status in ((0, "ready"), (6, "cancelled")):
            b = self.add("Blocked organization " + status)
            bid = b["data"]["task"]["task_id"]
            blocked = self.cli("task", "edit", bid, "--status", "blocked", "--blocked-reason", "Isolated fixture")
            a = self.add("Completed tail " + status)
            aid = a["data"]["task"]["task_id"]
            done = self.complete(aid)
            released = self.cli("task", "edit", bid, "--status", status)
            self.assertIsNone(released["data"]["task"]["ownership"]["execution_id"])
            acknowledgements.update({offset: b, offset + 1: blocked, offset + 2: a,
                                     offset + 3: done, offset + 4: released})
            completed.append(aid)
        logs = self.root / "logs"
        logs.mkdir()
        source = SourceInput(THREAD, logs / "organization.jsonl", logs, self.root)
        register_source(self.repository, source, CallerIdentity(THREAD))
        rows = self.response_header(THREAD, 1)
        for number in range(12):
            identity = "organization-" + str(number)
            rows += self.response_rows(identity, 1, acknowledgements.get(number))
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        core, sealed = self.target.db_path.read_bytes(), file_snapshot(self.target.resolved_evidence_root)
        self.collect_responses(source)
        for task in completed:
            unit = self.cli("task", "show", task)["data"]["usage"]["periods"][0]["work_units"][0]
            self.assertEqual(unit["exclusive"]["response_count"], 4)
            self.assertFalse(unit["partial"] or unit["shared"])
        with closing(connect_initialized_readonly(self.target)) as connection:
            index = self.repository.read(connection)
        self.assertFalse(index["unattributed"]["unassigned"]["response_count"])
        self.assertFalse(index["unattributed"]["unknown"]["response_count"])
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), sealed)

    def test_native_cross_session_delayed_pause_and_resume_preserve_original_execution(self):
        start = self.add("Cross-session recovery")
        task = start["data"]["task"]["task_id"]
        execution = self.cli("task", "context")["data"]["selected"]["task"]["ownership"]["execution_id"]
        paused = self.cli("task", "edit", task, "--status", "paused",
                          "--pause-reason", "Explicit isolated recovery", caller=PARENT)
        self.assertEqual(paused["data"]["task"]["ownership"]["execution_id"], execution)
        logs = self.root / "logs"
        logs.mkdir()
        former = SourceInput(THREAD, logs / "former.jsonl", logs, self.root)
        recovery = SourceInput(PARENT, logs / "recovery.jsonl", logs, self.root)
        for source in (former, recovery):
            register_source(self.repository, source, CallerIdentity(source.thread_id))
        old_rows = self.response_header(THREAD, 1) + self.response_rows("old-start", 1, start)
        old_rows += self.response_header(THREAD, 2, session=False) + self.response_rows("old-late", 2)
        new_rows = self.response_header(PARENT, 3)
        new_rows += self.response_rows("recovery-stop", 3, {"session_id": 7, "output": ""}, name="exec_command", thread=PARENT)
        new_rows += self.response_rows("recovery-gap", 3, thread=PARENT)
        new_rows += self.response_header(PARENT, 4, session=False)
        new_rows += self.response_rows("recovery-poll", 4, paused, name="write_stdin", arguments={"session_id": 7}, thread=PARENT)
        for source, rows in ((former, old_rows), (recovery, new_rows)):
            source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        self.collect_responses(former, recovery)
        actual = self.cli("task", "show", task)["data"]["usage"]
        unit = actual["periods"][0]["work_units"][0]
        self.assertEqual(unit["execution_id"], execution)
        self.assertEqual(unit["exclusive"]["response_count"], 2)
        self.assertEqual(sum(part["response_count"] for part in unit["partial"]), 1)
        resumed = self.cli("task", "edit", task, "--status", "in_progress", caller=PARENT)
        self.assertEqual(resumed["data"]["task"]["ownership"]["execution_id"], execution)
        append = self.response_header(PARENT, 5, session=False)
        append += self.response_rows("recovery-resume", 5, resumed, thread=PARENT) + self.response_rows("recovery-after", 5, thread=PARENT)
        with recovery.path.open("ab") as stream:
            stream.write(b"".join(json.dumps(row).encode() + b"\n" for row in append))
        core, sealed = self.target.db_path.read_bytes(), file_snapshot(self.target.resolved_evidence_root)
        self.collect_responses(former, recovery)
        actual = self.cli("task", "show", task)["data"]["usage"]
        unit = actual["periods"][0]["work_units"][0]
        self.assertEqual(unit["execution_id"], execution)
        self.assertEqual(unit["exclusive"]["response_count"], 4)
        self.assertEqual(sum(part["response_count"] for part in unit["partial"]), 1)
        self.assertEqual(actual["total"]["response_count"], 5)
        with closing(connect_initialized_readonly(self.target)) as connection:
            index = self.repository.read(connection)
        self.assertEqual(index["unattributed"]["unassigned"]["response_count"], 2)
        self.assertEqual(index["unattributed"]["unknown"]["response_count"], 1)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), sealed)

    def response_header(self, thread, number, *, session=True):
        rows = [event("session_meta", id=thread, cwd=str(self.root), model_provider="openai",
                      cli_version="0.162.0-alpha.17.2")] if session else []
        return rows + [event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                       event("turn_context", turn_id=turn(number), model="fixture-model")]

    def response_rows(self, identity, number, acknowledgement=None, *, name="exec_command", arguments=None, thread=THREAD):
        rows = []
        if acknowledgement is not None:
            rows.append(event("response_item", type="function_call", name=name, call_id=identity,
                              arguments=json.dumps({"cmd": "fixture"} if arguments is None else arguments),
                              internal_chat_message_metadata_passthrough={"turn_id": turn(number), "response_id": identity}))
        rows.append(usage(identity, turn=turn(number), thread=thread))
        if acknowledgement is not None:
            rows.append(event("response_item", type="function_call_output", call_id=identity,
                              output=json.dumps(acknowledgement),
                              internal_chat_message_metadata_passthrough={"turn_id": turn(number)}))
        return rows

    def collect_responses(self, *sources):
        for source in sources:
            while self.repository.cursor(source.source_id, source.thread_id).offset < source.path.stat().st_size:
                self.repository.collect_source(source, max_records=1)
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")

    def test_native_pause_block_resume_keeps_execution_and_separates_stopped_gaps(self):
        a = self.add("A pause/resume")
        aid = a["data"]["task"]["task_id"]
        prior_check = self.cli("task", "context")
        paused = self.cli("task", "edit", aid, "--status", "paused", "--pause-reason", "Isolated fixture")
        b = self.add("B during A pause")
        bid = b["data"]["task"]["task_id"]
        b_done = self.complete(bid)
        new_check = self.cli("task", "show", bid)
        resumed = self.cli("task", "edit", aid, "--status", "in_progress")
        blocked = self.cli("task", "edit", aid, "--status", "blocked", "--blocked-reason", "Isolated fixture")
        another_check = self.cli("task", "context")
        resumed_again = self.cli("task", "edit", aid, "--status", "in_progress")
        done = self.complete(aid)
        execution = prior_check["data"]["selected"]["task"]["ownership"]["execution_id"]
        self.assertTrue(all(ack["data"]["task"]["ownership"]["execution_id"] == execution
                            for ack in (paused, resumed, blocked, resumed_again)))
        acknowledgements = {0: a, 1: prior_check, 2: paused, 4: b, 5: b_done,
                            7: new_check, 9: resumed, 11: blocked, 13: another_check, 15: resumed_again, 16: done}
        logs = self.root / "logs"
        logs.mkdir()
        source = SourceInput(THREAD, logs / "native-stop.jsonl", logs, self.root)
        register_source(self.repository, source, CallerIdentity(THREAD))
        rows = [event("session_meta", id=THREAD, cwd=str(self.root), model_provider="openai", cli_version="0.162.0-alpha.17.2"),
                event("event_msg", type="task_started", turn_id=turn(1), started_at=1000),
                event("turn_context", turn_id=turn(1), model="fixture-model")]
        for number in range(18):
            identity = "stop-response-" + str(number)
            if number in acknowledgements:
                rows.append(event("response_item", type="function_call", name="exec", call_id=identity,
                    input='text(await tools.exec_command({cmd:"fixture"}));',
                    internal_chat_message_metadata_passthrough={"turn_id": turn(1), "response_id": identity}))
            rows.append(usage(identity, turn=turn(1)))
            if number in acknowledgements:
                rows.append(event("response_item", type="function_call_output", call_id=identity,
                    output=json.dumps(acknowledgements[number]), internal_chat_message_metadata_passthrough={"turn_id": turn(1)}))
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        core, sealed = self.target.db_path.read_bytes(), file_snapshot(self.target.resolved_evidence_root)
        while self.repository.cursor(source.source_id, THREAD).offset < source.path.stat().st_size:
            self.repository.collect_source(source, max_records=1)
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        actual = self.cli("task", "show", aid, "--audit")["data"]["usage"]
        self.assertEqual(actual["total"]["response_count"], 13)
        self.assertEqual(actual["total"]["models"][0]["total_tokens"], 1560)
        self.assertEqual(len(actual["periods"]), 1)
        self.assertEqual(len(actual["periods"][0]["work_units"]), 1)
        unit = actual["periods"][0]["work_units"][0]
        self.assertEqual(unit["execution_id"], execution)
        self.assertEqual(unit["exclusive"]["response_count"], 13)
        self.assertFalse(unit["shared"] or unit["partial"])
        self.assertEqual(self.cli("task", "show", bid)["data"]["usage"]["total"]["response_count"], 3)
        with closing(connect_initialized_readonly(self.target)) as connection:
            index = self.repository.read(connection)
        self.assertEqual(index["unattributed"]["unassigned"]["response_count"], 2)
        self.assertEqual(index["unattributed"]["unknown"]["response_count"], 0)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), sealed)

    def test_native_completion_cycles_are_exclusive_response_sets_in_one_turn(self):
        a = self.add("A")
        aid = a["data"]["task"]["task_id"]
        a_done = self.complete(aid)
        b = self.add("B")
        bid = b["data"]["task"]["task_id"]
        b_done = self.complete(bid)
        reopened = self.cli("task", "edit", aid, "--status", "in_progress", "--reopen-reason", "Approved isolated follow-up")
        done_again = self.complete(aid)
        logs = self.root / "logs"
        logs.mkdir()
        source = SourceInput(THREAD, logs / "native.jsonl", logs, self.root)
        register_source(self.repository, source, CallerIdentity(THREAD))
        rows = [event("session_meta", id=THREAD, cwd=str(self.root), model_provider="openai", cli_version="0.162.0-alpha.17.2"),
                event("event_msg", type="task_started", turn_id=turn(1), started_at=1000),
                event("turn_context", turn_id=turn(1), model="fixture-model")]
        for index, ack in enumerate((a, a_done, b, b_done, reopened, done_again)):
            identity = "native-" + str(index)
            rows += [event("response_item", type="function_call", name="functions.exec", call_id=identity,
                        arguments='text(await tools.exec_command({cmd:"fixture"}));',
                        internal_chat_message_metadata_passthrough={"turn_id": turn(1), "response_id": identity}),
                     usage(identity, turn=turn(1)),
                     event("response_item", type="function_call_output", call_id=identity, output=json.dumps(ack),
                        internal_chat_message_metadata_passthrough={"turn_id": turn(1)})]
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        core = self.target.db_path.read_bytes()
        sealed = file_snapshot(self.target.resolved_evidence_root)
        while self.repository.cursor(source.source_id, THREAD).offset < source.path.stat().st_size:
            self.repository.collect_source(source, max_records=1)
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        actual = self.cli("task", "show", aid, "--audit")["data"]["usage"]
        self.assertEqual([period["models"][0]["total_tokens"] for period in actual["periods"]], [240, 240])
        self.assertEqual(actual["total"]["models"][0]["total_tokens"], 480)
        self.assertEqual(len({period["completion_cycle_id"] for period in actual["periods"]}), 2)
        self.assertTrue(all(unit["exclusive"]["response_count"] == 2 and not unit["shared"] and not unit["partial"]
                            for period in actual["periods"] for unit in period["work_units"]))
        self.assertEqual(self.cli("task", "show", bid)["data"]["usage"]["total"]["response_count"], 2)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root), sealed)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name), git_managed=True)
        # Match the child process cwd and generated --repo operand, including
        # when Windows TEMP uses an 8.3 alias. Log/payload cwd must match too.
        self.root = self.install.project_root.resolve()
        self.cli("setup")
        resolution = resolve_project_state(skill_root=self.install.skill_root, repo=self.root)
        self.assertIsNone(resolution.error_code)
        self.target = resolution.target
        self.repository = service.repository_for(self.target)

    def cli(self, *arguments, ok=True, caller=THREAD):
        environment = {**os.environ, "CODEX_THREAD_ID": caller}
        environment.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(self.install.entrypoint),
                                 *arguments, "--json"], cwd=self.root, env=environment,
                                capture_output=True, timeout=30, check=False)
        value = json.loads(result.stdout)
        self.assertEqual(value["ok"], ok, value)
        self.assertEqual(result.returncode == 0, ok, value)
        return value

    def add(self, title="Usage fixture"):
        return self.cli("task", "add", "--title", title, "--status", "in_progress", "--review-tier", "0",
                        "--verification-not-required", "Isolated test fixture")

    def complete(self, task, *, compatibility=False):
        self.cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", "sha256:" + "a" * 64)
        self.cli("review", "receipt", "add", task, "--reviewer", "isolated-fixture", "--kind", "not_required",
                 "--verdict", "not_required", "--summary", "Mechanical fixture only; no product change")
        operation = ("task", "edit", task, "--status", "done") if compatibility else ("task", "complete", task)
        return self.cli(*operation, "--verification-complete", "--review-complete", "--commit-not-required")

    def collect(self, acknowledgements, *, count=8):
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        source = SourceInput(THREAD, logs / "fixture.jsonl", logs, self.root)
        register_source(self.repository, source, CallerIdentity(THREAD))
        rows = [event("session_meta", id=THREAD, cwd=str(self.root), model_provider="openai")]
        for number in range(1, count + 1):
            rows.extend([event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                         event("turn_context", turn_id=turn(number), model="fixture-model")])
            for ack in acknowledgements.get(number, ()):
                rows.append(tool_record(ack, number))
            rows.append(usage(f"r{number}", turn=turn(number)))
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        self.assertNotEqual(collect_registered(self.repository, source)["status"], "unknown")
        return source

    def test_public_completion_late_append_and_index_publication_leave_core_unchanged(self):
        start = self.add()
        task = start["data"]["task"]["task_id"]
        ready = self.cli("task", "edit", task, "--status", "ready")
        resumed = self.cli("task", "edit", task, "--status", "in_progress")
        end = self.cli("task", "edit", task, "--status", "review_pending")
        done = self.complete(task)
        self.assertEqual(done["data"]["usage"]["status"], "pending")
        other = self.add("Still active shared participant")
        self.collect({2: [start], 4: [ready, resumed], 6: [end], 7: [done, other]})
        core = self.target.db_path.read_bytes()
        evidence = file_snapshot(self.target.resolved_evidence_root) if self.target.resolved_evidence_root.exists() else {}
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        shown = self.cli("task", "show", task)["data"]["usage"]
        # Response partitions do not propagate the other Task's later turn 8
        # through the overlapping turn 7. Turn-only observations stay partial.
        self.assertEqual(shown["periods"][0]["models"][0]["total_tokens"], 6 * 120)
        self.assertTrue(all(unit["exclusive"]["response_count"] == 0
                            for unit in shown["periods"][0]["work_units"]))
        self.assertIn("interval_open", shown["periods"][0]["gaps"])
        self.assertEqual({link["snapshot_id"] for link in shown["cycle_links"]},
                         set(shown["periods"][0]["snapshot_ids"]))
        self.assertEqual(len(shown["cycle_links"]), 4)
        self.assertIsNotNone(shown["periods"][0]["completion_cycle_id"])
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(file_snapshot(self.target.resolved_evidence_root) if self.target.resolved_evidence_root.exists() else {}, evidence)
        index = self.target.resolved_usage_index.read_bytes()
        source = SourceInput(THREAD, self.root / "logs/fixture.jsonl", self.root / "logs", self.root)
        with source.path.open("ab") as stream:
            stream.write(json.dumps(usage("late", turn=turn(6))).encode() + b"\n")
        collect_registered(self.repository, source)
        original_publish = service._publish

        def fail_index(path, *args, **kwargs):
            if path == self.target.resolved_usage_index:
                raise UsageError()
            return original_publish(path, *args, **kwargs)

        with mock.patch.object(service, "_publish", side_effect=fail_index):
            self.assertEqual(service.refresh_usage(self.target)["publication"], "pending")
        self.assertEqual(self.target.resolved_usage_index.read_bytes(), index)
        self.assertEqual(self.target.db_path.read_bytes(), core)
        self.assertEqual(service.refresh_usage(self.target)["publication"], "current")
        final = self.cli("task", "show", task, "--audit")["data"]["usage"]
        self.assertEqual(final["periods"][0]["models"][0]["total_tokens"], 7 * 120)
        self.assertEqual(final["periods"][0]["completion_cycle_id"], shown["periods"][0]["completion_cycle_id"])
        index_data = json.loads(self.target.resolved_usage_index.read_bytes())
        for identity in index_data["snapshots"]:
            self.assertTrue((self.target.resolved_usage_snapshots / (identity + ".json")).is_file())
        before = file_snapshot(self.root)
        self.cli("task", "show", task)
        self.cli("task", "show", task, "--audit")
        self.cli("task", "context")
        self.assertEqual(file_snapshot(self.root), before)

    def test_missing_corrupt_and_busy_usage_never_prevent_both_completion_routes(self):
        path = self.target.resolved_usage_database
        initial = path.read_bytes()
        for mode in ("missing", "corrupt", "busy"):
            with self.subTest(mode=mode):
                path.write_bytes(initial)
                started = self.add(mode)
                task = started["data"]["task"]["task_id"]
                if mode == "missing":
                    path.unlink()
                elif mode == "corrupt":
                    path.write_bytes(b"private-invalid-store")
                connection = sqlite3.connect(path) if mode == "busy" else None
                try:
                    if connection:
                        connection.execute("BEGIN EXCLUSIVE")
                    done = self.complete(task, compatibility=mode == "corrupt")
                    self.assertEqual(done["data"]["task"]["status"], "done")
                    self.assertEqual(done["data"]["usage"], {"status": "pending", "coverage": "registered_only"})
                    if mode != "busy":
                        self.assertEqual(self.cli("task", "show", task)["data"]["usage"]["status"], "unknown")
                finally:
                    if connection:
                        connection.close()
                if mode == "missing":
                    self.assertFalse(path.exists())
                elif mode == "corrupt":
                    self.assertEqual(path.read_bytes(), b"private-invalid-store")

    def test_worker_uses_core_readonly_and_numerical_failure_is_sanitized(self):
        with mock.patch.object(service, "repository_for", side_effect=RuntimeError("PRIVATE")):
            result = service.refresh_usage(self.target)
        self.assertEqual(result, {"status": "unknown", "publication": "pending", "diagnostics": ["usage_unavailable"]})
        with closing(connect_initialized_readonly(self.target)) as core:
            with self.assertRaises(sqlite3.OperationalError):
                core.execute("DELETE FROM tasks")
        self.target.db_path.write_bytes(b"bad-core")
        rejected = self.cli("task", "context", ok=False)
        self.assertFalse(rejected["ok"])
