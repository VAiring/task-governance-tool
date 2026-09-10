from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    file_snapshot, initialize_taskgov_internal, make_physical_install,
    run_taskgov_internal,
)

from task_governance_tool import cli as cli_service
from task_governance_tool import effort as effort_service
from task_governance_tool import task_registration as registration
from task_governance_tool import tasks as task_service
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.storage import resolve_database_target
from task_governance_tool.task_values import TaskValidationError


SCRIPT = Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts" / "taskgov.py"


def document(*, common=None, tasks=None):
    return {
        "version": 1,
        "common": {"review_tier": 1} if common is None else common,
        "tasks": [{"title": "One accepted responsibility", "contract": None}] if tasks is None else tasks,
    }


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class BinaryInput(io.BytesIO):
    def __init__(self, value=b"", *, readable=True):
        super().__init__(value)
        self.readable_by_contract = readable
        self.read_sizes = []

    @property
    def buffer(self):
        return self

    def read(self, size=-1):
        if not self.readable_by_contract:
            raise AssertionError("stdin must not be consumed")
        self.read_sizes.append(size)
        return super().read(size)


class TaskBatchInputTests(unittest.TestCase):
    def assert_invalid(self, value):
        with self.assertRaises(TaskValidationError):
            registration.decode_task_registration(value)

    def test_explicit_common_values_expand_without_activating_null_contract(self):
        shared_constraints = "Keep the approved boundary 日本語"
        shared_authority = "conversation:approved-task-set"
        candidate = document(common={
            "review_tier": 2, "verification": "Run the focused checks",
            "contract": {"constraints": shared_constraints, "authority_ref": shared_authority},
        }, tasks=[
            {"title": "First", "contract": {"scope": "First scope", "acceptance": "First acceptance"}},
            {"title": "Second", "verification": "", "review_tier": 1,
             "contract": {"scope": "Second scope", "acceptance": "Second acceptance", "constraints": "", "authority_ref": ""}},
            {"title": "Whole outcome", "contract": None},
        ])
        raw = encode(candidate)
        expanded = registration.decode_task_registration(raw)
        self.assertEqual([item["title"] for item in expanded], ["First", "Second", "Whole outcome"])
        self.assertEqual(expanded[0]["contract_constraints"], shared_constraints)
        self.assertEqual(expanded[0]["contract_authority_ref"], shared_authority)
        self.assertEqual(expanded[1]["verification"], "")
        self.assertEqual(expanded[1]["review_tier"], 1)
        self.assertEqual(expanded[1]["contract_constraints"], "")
        self.assertFalse(any(key.startswith("contract_") for key in expanded[2]))
        self.assertEqual(raw.count(shared_constraints.encode()), 1)
        self.assertEqual(raw.count(shared_authority.encode()), 1)

    def test_strict_closed_shapes_types_and_required_tier(self):
        baseline = document()
        malformed = [
            [], None, {**baseline, "extra": 1}, {"version": 1, "tasks": baseline["tasks"]},
            {**baseline, "version": True}, {**baseline, "version": "1"}, {**baseline, "version": 2},
            {**baseline, "common": None}, document(common={"title": "Not shared", "review_tier": 1}),
            document(common={"contract": {"scope": "Cannot share scope"}, "review_tier": 1}),
            document(common={"contract": None, "review_tier": 1}),
            document(tasks=[{"contract": None}]), document(tasks=[{"title": "Missing Contract decision"}]),
            document(tasks=[{"title": "No inferred acceptance", "contract": {"scope": "Scope"}}]),
            document(tasks=[{"title": "No added fields", "contract": None, "task_id": "caller-id"}]),
            document(common={}),
        ]
        for key, invalid in (
            ("description", None), ("status", False), ("kind", 1), ("lane", []),
            ("lane_order", True), ("lane_order", "1"), ("priority", {}),
            ("blocked_reason", None), ("review_tier", True), ("review_tier", "2"),
            ("verification", []), ("tags", ["tag"]),
        ):
            malformed.append(document(tasks=[{"title": "Typed input", "contract": None, key: invalid}]))
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assert_invalid(encode(candidate))
        self.assert_invalid(encode(document(tasks=[{"title": "Typed Contract", "contract": {"scope": "Scope", "acceptance": "Acceptance", "constraints": None}}])))

    def test_bounds_unicode_duplicate_keys_and_noninteger_numbers(self):
        raw = encode(document())
        padded = raw + b" " * (262144 - len(raw))
        self.assertEqual(len(registration.decode_task_registration(padded)), 1)
        self.assert_invalid(padded + b" ")
        sixty_four = document(tasks=[{"title": f"Task {i}", "contract": None} for i in range(64)])
        self.assertEqual(len(registration.decode_task_registration(encode(sixty_four))), 64)
        sixty_four["tasks"].append({"title": "Too many", "contract": None})
        self.assert_invalid(encode(sixty_four))
        self.assert_invalid(encode(document(tasks=[])))
        for value in (
            b"", b"{", b"\xff", b"\xef\xbb\xbf" + raw, raw + raw,
            raw[:-1] + b',"version":1}',
            raw.replace(b'"review_tier":1', b'"review_tier":1.0'),
            raw.replace(b'"review_tier":1', b'"review_tier":NaN'),
            raw.replace(b'"review_tier":1', b'"review_tier":Infinity'),
            raw.replace(b'"One accepted responsibility"', b'"\\ud800"'),
        ):
            with self.subTest(value=value[:55]):
                self.assert_invalid(value)


class TaskBatchCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.db = self.root / "taskgov.sqlite"
        initialize_taskgov_internal(repo=self.repo, db=self.db)
        self.target = resolve_database_target(repo=self.repo, db=self.db, script_path=SCRIPT)

    def success(self, *arguments):
        response = run_taskgov_internal(*arguments, "--repo", str(self.repo), "--db", str(self.db), "--json", maintenance_enabled=False)
        self.assertEqual(response.returncode, 0, response.stdout or response.stderr)
        return json.loads(response.stdout)["data"]

    def invoke(self, candidate=None, *arguments, stdin=None, maintenance_enabled=False, json_output=True):
        stream = stdin if stdin is not None else BinaryInput(encode(document() if candidate is None else candidate))
        argv = ["--repo", str(self.repo), "task", "add", "--from-stdin", *arguments]
        if json_output:
            argv.append("--json")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", stream), redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_service.main(argv, _target_override=self.target, _maintenance_enabled=maintenance_enabled)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_failure(self, response, error, *, exit_code=1):
        code, stdout, stderr = response
        self.assertEqual((code, stderr), (exit_code, ""), stdout or stderr)
        envelope = json.loads(stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["command"], "task.add")
        self.assertEqual(envelope["data"], {"tasks": []})
        self.assertEqual(envelope["errors"][0]["code"], error)
        self.assertEqual(envelope["warnings"], [])

    def test_batch_matches_single_defaults_and_explicit_contract_semantics(self):
        candidate = document(common={
            "review_tier": 2, "description": "Shared description", "verification": "Focused verification",
            "contract": {"constraints": "Shared constraint", "authority_ref": "conversation:approved-groups"},
        }, tasks=[
            {"title": "Scoped group", "contract": {"scope": " Scope\r\nline ", "acceptance": "Accepted outcome"}},
            {"title": "Whole outcome", "description": "", "verification": "", "review_tier": 1, "contract": None},
        ])
        code, stdout, stderr = self.invoke(candidate)
        self.assertEqual((code, stderr), (0, ""), stdout)
        rows = json.loads(stdout)["data"]["tasks"]
        self.assertEqual([row["input_index"] for row in rows], [0, 1])
        self.assertEqual(len({row["task"]["task_id"] for row in rows}), 2)
        self.assertEqual(set(rows[0]), {"input_index", "task", "event", "contract_write"})
        self.assertEqual(set(rows[1]), {"input_index", "task", "event"})
        single = self.success("task", "add", "--title", "Whole outcome")
        volatile = {"task_id", "created_at", "updated_at"}
        self.assertEqual({k: v for k, v in rows[1]["task"].items() if k not in volatile}, {k: v for k, v in single["task"].items() if k not in volatile})
        scoped = self.success("task", "show", rows[0]["task"]["task_id"])["contract"]
        self.assertEqual((scoped["revision"], scoped["scope"], scoped["constraints"], scoped["authority_ref"]), (1, "Scope\nline", "Shared constraint", "conversation:approved-groups"))
        whole = self.success("task", "show", rows[1]["task"]["task_id"])["contract"]
        self.assertEqual(whole["revision"], 0)
        for row in rows:
            self.assertEqual(row["event"]["task_id"], row["task"]["task_id"])
            self.assertEqual(row["event"]["event_type"], "task_added")

    def test_shared_partial_fields_merge_before_cross_field_validation(self):
        candidate = document(common={"review_tier": 2, "kind": "sequential", "lane": " Shared lane ", "status": "blocked"}, tasks=[
            {"title": "First", "blocked_reason": "First dependency", "contract": None},
            {"title": "Second", "blocked_reason": "Second dependency", "contract": None},
        ])
        code, stdout, stderr = self.invoke(candidate)
        self.assertEqual((code, stderr), (0, ""), stdout)
        rows = json.loads(stdout)["data"]["tasks"]
        self.assertEqual([row["task"]["lane_order"] for row in rows], [1, 2])
        self.assertEqual([row["task"]["lane"] for row in rows], ["Shared lane", "Shared lane"])

    def test_blocked_reason_preserves_single_registration_capacity_and_bytes(self):
        reason = "  Awaiting the existing dependency. " * 80
        single = self.success("task", "add", "--title", "Single blocked Task", "--status", "blocked", "--blocked-reason", reason)["task"]
        candidate = document(common={"review_tier": 1, "status": "blocked", "blocked_reason": reason})
        code, stdout, stderr = self.invoke(candidate)
        self.assertEqual((code, stderr), (0, ""), stdout)
        task = json.loads(stdout)["data"]["tasks"][0]["task"]
        self.assertEqual(task["blocked_reason"], single["blocked_reason"])
        self.assertEqual(task["blocked_reason"], reason)

    def test_later_order_conflict_rolls_back_all_and_retry_preserves_preexisting_task(self):
        existing = self.success("task", "add", "--title", "Already registered", "--kind", "sequential", "--lane", "Lane", "--order", "20")["task"]
        before = file_snapshot(self.root)
        candidate = document(common={"review_tier": 2, "kind": "sequential", "lane": "Lane"}, tasks=[
            {"title": "First new group", "lane_order": 10, "contract": {"scope": "First scope", "acceptance": "First acceptance"}},
            {"title": "Second new group", "lane_order": 20, "contract": None},
        ])
        with mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            self.assert_failure(self.invoke(candidate, maintenance_enabled=True), "invalid_argument")
        maintenance.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)
        candidate["tasks"][1]["lane_order"] = 30
        code, stdout, stderr = self.invoke(candidate)
        self.assertEqual((code, stderr), (0, ""), stdout)
        registered = self.success("task", "list")["tasks"]
        self.assertEqual(len(registered), 3)
        self.assertEqual(next(row for row in registered if row["task_id"] == existing["task_id"]), existing)
        self.assertEqual({row["task"]["title"] for row in json.loads(stdout)["data"]["tasks"]}, {"First new group", "Second new group"})

    def test_adding_incomplete_predecessor_ahead_of_active_work_rolls_back_batch(self):
        self.success("task", "add", "--title", "Active successor", "--kind", "sequential", "--lane", "Lane", "--order", "20", "--status", "in_progress")
        before = file_snapshot(self.root)
        candidate = document(tasks=[
            {"title": "Unrelated optional", "contract": None},
            {"title": "Forbidden predecessor", "kind": "sequential", "lane": "Lane", "lane_order": 10, "contract": None},
        ])
        self.assert_failure(self.invoke(candidate), "sequential_predecessor_incomplete")
        self.assertEqual(file_snapshot(self.root), before)

    def test_invalid_later_item_and_overridden_common_are_rejected_before_writer(self):
        candidates = [
            (document(tasks=[{"title": "Good", "contract": None}, {"title": "bad" * 100, "contract": None}]), "invalid_argument"),
            (document(common={"review_tier": 1, "verification": "token=secret"}, tasks=[{"title": "Override", "verification": "Safe", "contract": None}]), "privacy_rejected"),
            (document(common={"review_tier": 1, "kind": "unsupported"}, tasks=[{"title": "Override", "kind": "optional", "contract": None}]), "invalid_kind"),
            (document(common={"review_tier": 1, "contract": {"constraints": "token=secret"}}, tasks=[{"title": "No activation", "contract": None}]), "privacy_rejected"),
            (document(tasks=[{"title": "No initial done", "status": "done", "contract": None}]), "initial_done_forbidden"),
            (document(tasks=[{"title": "No initial paused", "status": "paused", "contract": None}]), "initial_paused_forbidden"),
            (document(tasks=[{"title": "Cancelled Contract", "status": "cancelled", "contract": {"scope": "Scope", "acceptance": "Acceptance"}}]), "contract_activation_forbidden"),
        ]
        before = file_snapshot(self.root)
        for candidate, error in candidates:
            with self.subTest(error=error), mock.patch.object(task_service, "begin_task_write", side_effect=AssertionError("preflight must precede writer")), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
                result = self.invoke(candidate, maintenance_enabled=True)
                self.assert_failure(result, error)
                self.assertNotIn("token=secret", result[1] + result[2])
                maintenance.assert_not_called()
                self.assertEqual(file_snapshot(self.root), before)

    def test_late_event_failure_rolls_back_task_contract_authority_and_events(self):
        before = file_snapshot(self.root)
        original_event = task_service.create_task_event
        observed = []

        def fail_second(connection, **kwargs):
            if kwargs["event_type"] == "task_added":
                count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                observed.append(count)
                if count == 2:
                    raise sqlite3.OperationalError("private failure token=never-echo")
            return original_event(connection, **kwargs)

        candidate = document(tasks=[{"title": title, "contract": {"scope": f"{title} scope", "acceptance": f"{title} acceptance"}} for title in ("First", "Second")])
        with mock.patch.object(task_service, "create_task_event", side_effect=fail_second), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            result = self.invoke(candidate, maintenance_enabled=True)
        self.assert_failure(result, "internal_error", exit_code=2)
        self.assertEqual(observed, [1, 2])
        self.assertNotIn("never-echo", result[1] + result[2])
        maintenance.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)

    def test_effort_observation_finishes_for_every_active_item_before_writer(self):
        profile = effort_service.EffortProfile(True, True, True, effort_service.PROFILE_ID, 1, "a" * 64, {})
        endpoint = effort_service.GitEndpoint(True, "a" * 40, True, b"")
        opened, observed = [], []
        actual_connect = cli_service.connect_initialized

        def connect(*args, **kwargs):
            connection = actual_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        def capture(repo):
            self.assertFalse(opened[0].in_transaction)
            with closing(sqlite3.connect(self.db)) as reader:
                observed.append(reader.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            return endpoint

        candidate = document(common={"review_tier": 1, "status": "in_progress"}, tasks=[{"title": title, "contract": None} for title in ("First", "Second")])
        with mock.patch.object(cli_service, "load_effort_profile", return_value=profile), mock.patch.object(cli_service, "connect_initialized", side_effect=connect), mock.patch.object(effort_service, "capture_git_basis", side_effect=capture):
            code, stdout, stderr = self.invoke(candidate)
        self.assertEqual((code, stderr), (0, ""), stdout)
        self.assertEqual(observed, [0, 0])
        with closing(sqlite3.connect(self.db)) as reader:
            rows = reader.execute("SELECT task_id,basis_head FROM task_effort_bases").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row[0] for row in rows}, {row["task"]["task_id"] for row in json.loads(stdout)["data"]["tasks"]})
        self.assertEqual({row[1] for row in rows}, {"a" * 40})

    def test_parse_and_read_only_reject_before_stdin_and_invalid_json_before_connection(self):
        for flag, value in (("--title", "Single"), ("--review-tier", "1"), ("--contract-scope", "Scope"), ("--verification", "")):
            with self.subTest(flag=flag), mock.patch.object(cli_service, "resolve_context_target", side_effect=AssertionError("parse before state")):
                code, stdout, stderr = self.invoke(None, flag, value, stdin=BinaryInput(readable=False))
                self.assertEqual((code, stderr), (1, ""), stdout)
                parsed = json.loads(stdout)
                self.assertEqual(parsed["command"], "parse")
                self.assertEqual(parsed["errors"][0]["code"], "invalid_option_combination")
        before = file_snapshot(self.root)
        with mock.patch.object(cli_service, "connect_initialized", side_effect=AssertionError("no connection")), mock.patch.object(cli_service, "run_post_commit_maintenance") as maintenance:
            code, stdout, stderr = self.invoke(None, "--read-only", stdin=BinaryInput(readable=False), maintenance_enabled=True)
            self.assertEqual((code, stderr), (1, ""), stdout)
            self.assertEqual(json.loads(stdout)["errors"][0]["code"], "invalid_argument")
            for raw in (b"{", b" " * 262145, b"\xff"):
                stream = BinaryInput(raw)
                self.assert_failure(self.invoke(stdin=stream, maintenance_enabled=True), "invalid_argument")
                self.assertEqual(stream.read_sizes, [262145])
        maintenance.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)

    def test_one_post_commit_maintenance_after_all_items_and_connection_close(self):
        opened, observed = [], []
        actual_connect = cli_service.connect_initialized

        def connect(*args, **kwargs):
            connection = actual_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        def maintain(target, outcome):
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute("SELECT 1")
            with closing(sqlite3.connect(self.db)) as reader:
                observed.append((outcome, reader.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]))
            raise OSError("private maintenance detail token=never-echo")

        candidate = document(tasks=[{"title": title, "contract": None} for title in ("First", "Second")])
        with mock.patch.object(cli_service, "connect_initialized", side_effect=connect), mock.patch.object(cli_service, "run_post_commit_maintenance", side_effect=maintain) as maintenance:
            code, stdout, stderr = self.invoke(candidate, maintenance_enabled=True)
        self.assertEqual((code, stderr), (0, ""), stdout)
        maintenance.assert_called_once()
        self.assertEqual(observed, [(MutationOutcome(state_changed=True, viewer_relevant=True), 2)])
        self.assertEqual(len(json.loads(stdout)["data"]["tasks"]), 2)
        self.assertNotIn("never-echo", stdout)

    def test_physical_install_consumes_one_utf8_batch_without_source_or_config_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve())
            setup = install.run("setup", "--json")
            self.assertEqual(setup.returncode, 0, setup.stdout or setup.stderr)
            candidate = document(common={"review_tier": 1, "verification": "Focused verification 日本語"}, tasks=[{"title": title, "contract": None} for title in ("First 日本語", "Second 日本語")])
            before = file_snapshot(install.project_root, exclude_state=True)
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(install.entrypoint), "task", "add", "--from-stdin", "--repo", str(install.project_root), "--json"],
                cwd=install.project_root, input=encode(candidate), capture_output=True, timeout=30, check=False,
            )
            self.assertEqual((result.returncode, result.stderr), (0, b""), result.stdout.decode("utf-8"))
            rows = json.loads(result.stdout)["data"]["tasks"]
            self.assertEqual([row["task"]["title"] for row in rows], [item["title"] for item in candidate["tasks"]])
            self.assertEqual([row["input_index"] for row in rows], [0, 1])
            self.assertEqual(file_snapshot(install.project_root, exclude_state=True), before)
            self.assertFalse((install.skill_root / "config" / "verification-runner.json").exists())


if __name__ == "__main__":
    unittest.main()
