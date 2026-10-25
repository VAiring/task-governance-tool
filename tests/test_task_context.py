from __future__ import annotations

import argparse
import json
import copy
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from tests.registration_output_support import registration_body

from tests.m14_test_support import (
    file_snapshot,
    initialize_taskgov_internal,
    make_physical_install,
    run_taskgov_internal,
)
from tests.test_m17_cli_consumer_hardening import _run_cli, _setup
from tests.test_review_session_migration import candidate_runtime

from task_governance_tool import cli as cli_service
from task_governance_tool import storage
from task_governance_tool.cli_output import CommandResult
from task_governance_tool.effort import disabled_profile


EMPTY_CONTEXT = {
    "selection": "none", "current": None, "next": None, "selected": None,
}


def run(db: Path, repo: Path, *arguments: str):
    return run_taskgov_internal(
        *arguments, "--db", str(db), "--repo", str(repo), "--json",
        maintenance_enabled=False,
    )


class TaskContextTests(unittest.TestCase):
    def test_detail_rejects_validated_row_from_other_task_or_connection(self):
        from task_governance_tool import stored_task_validation, task_show_projection
        from task_governance_tool.tasks import TaskRepositoryError

        first = self.add("First selected Task")
        second = self.add("Second selected Task")
        project = storage.ProjectIdentity(first["project_id"], self.repo, "fixture", "fixture")
        with closing(storage.connect(self.db)) as source, closing(storage.connect(self.db)) as other:
            source.execute("PRAGMA query_only=ON")
            other.execute("PRAGMA query_only=ON")
            source.execute("BEGIN")
            other.execute("BEGIN")
            row = stored_task_validation.fetch_validated_current_task_row(
                source, project_id=first["project_id"], task_id=first["task_id"],
            )
            self.assertIsNotNone(row)
            retained = task_show_projection.retain_validated_task_read(source, row)
            for connection, task_id in ((other, first["task_id"]), (source, second["task_id"])):
                with self.subTest(task_id=task_id, connection=connection is source), \
                        self.assertRaises(TaskRepositoryError) as error:
                    task_show_projection.show_task(
                        connection, project, task_id,
                        validated_task_read=retained,
                    )
                self.assertEqual(error.exception.code, "internal_error")

            other.rollback()
            source.rollback()
            self.success("task", "edit", first["task_id"], "--title", "Updated selected Task")
            source.execute("BEGIN")
            with self.assertRaises(TaskRepositoryError) as error:
                task_show_projection.show_task(
                    source, project, first["task_id"], validated_task_read=retained,
                )
            self.assertEqual(error.exception.code, "internal_error")
            stale_context = cli_service.CommandContext(
                command="task.show", repo=str(self.repo), repo_explicit=True,
                json_output=True, read_only=True,
                args=argparse.Namespace(task_id=first["task_id"]),
                target_override=storage.DatabaseTarget(project, self.db, True),
                read_connection_override=source,
            )
            stale_result = cli_service.read_task_detail(
                stale_context, selected_task_read=retained,
            )
            self.assertFalse(stale_result.ok)
            self.assertEqual(stale_result.errors[0]["code"], "internal_error")

    def test_separate_read_checkpoint_is_not_reused(self):
        from task_governance_tool import checkpoints
        task = self.add("Checkpoint reuse", "--status", "in_progress")
        for present in (False, True):
            if present:
                self.success("task", "checkpoint", task["task_id"], "--summary", "Resume here",
                             "--next-action", "Continue verification")
            expected = self.success("task", "show", task["task_id"])["data"]["latest_checkpoint"]
            with mock.patch.object(checkpoints, "read_latest_checkpoint", wraps=checkpoints.read_latest_checkpoint) as read:
                actual = self.success("task", "context")["data"]
            self.assertEqual(actual["selected"]["latest_checkpoint"], expected)
            # Explicit-DB fixtures open separate reads and must not reuse.
            self.assertEqual(read.call_count, 2)
            for arguments in (("task", "current"), ("task", "current", "--compact")):
                with mock.patch.object(checkpoints, "read_latest_checkpoint", wraps=checkpoints.read_latest_checkpoint) as read:
                    self.success(*arguments)
                self.assertEqual(read.call_count, 1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.db = self.root / "taskgov.sqlite"
        initialize_taskgov_internal(repo=self.repo, db=self.db)

    def success(self, *arguments):
        result = run(self.db, self.repo, *arguments)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        return payload

    def add(self, title, *arguments):
        data = self.success("task", "add", "--title", title, *arguments)["data"]
        return registration_body(data, data, "task")

    def legacy_current_pair(self, title, *arguments):
        # Pre-v26 overlap remains readable; current commands cannot create it.
        self.db = self.root / "legacy.sqlite"
        with candidate_runtime(25):
            initialize_taskgov_internal(repo=self.repo, db=self.db)
            pending = self.add(title, "--status", "in_progress", *arguments)
            self.success("task", "edit", pending["task_id"], "--status", "review_pending")
            active = self.add("First active", "--status", "in_progress", "--priority", "urgent")
        with closing(storage.connect(self.db)) as connection:
            storage.apply_migrations(connection)
        return pending, active

    def assert_matches_selection_and_display(self):
        before = file_snapshot(self.root)
        current_batch = self.success("task", "current")
        current = self.success("task", "current", "--compact")
        # Explicit current remains an all-owner/held inspection; context uses
        # the caller-owned subset before its limit. This fixture fits in one batch.
        eligible = [row for row in current_batch["data"]["tasks"]
                    if row["ownership"]["is_owner"] or row["ownership"]["is_completion_owner"]]
        visible = [row for row in current["data"]["tasks"]
                   if row["ownership"]["is_owner"] or row["ownership"]["is_completion_owner"]]
        current["data"].update(tasks=visible, total_matching=len(eligible), returned_count=len(visible), truncated=len(visible) < len(eligible))
        warnings = list(current["warnings"])
        chosen = next(
            (row for row in current_batch["data"]["tasks"]
             if row["ownership"]["is_owner"] or row["ownership"]["is_completion_owner"]),
            None,
        )
        next_payload = None
        selection = "current" if chosen else "none"
        if chosen is None:
            next_batch = self.success("task", "next")
            next_payload = self.success("task", "next", "--compact")
            warnings.extend(next_payload["warnings"])
            if next_batch["data"]["tasks"]:
                chosen = next_batch["data"]["tasks"][0]
                selection = "next"
        shown = None
        if chosen is not None:
            shown = self.success("task", "show", chosen["task_id"])
            warnings.extend(shown["warnings"])

        actual = self.success("task", "context", "--read-only")

        self.assertEqual(set(actual), {"ok", "command", "project_id", "data", "warnings", "errors"})
        self.assertEqual(actual["command"], "task.context")
        self.assertEqual(actual["project_id"], current["project_id"])
        self.assertEqual(actual["warnings"], warnings)
        self.assertEqual(actual["errors"], [])
        expected = {
            "selection": selection,
            "current": current["data"],
            "next": next_payload["data"] if next_payload else None,
            "selected": shown["data"] if shown else None,
        }
        restored = copy.deepcopy(actual["data"])
        for component in ("current", "next"):
            if expected[component] is None:
                continue
            for row, original in zip(restored[component]["tasks"], expected[component]["tasks"], strict=True):
                if "task_ref" in row:
                    self.assertEqual(row.pop("task_ref"), "/data/selected/task")
                    self.assertEqual(row["task_id"], shown["data"]["task"]["task_id"])
                    # Restore only the known compact schema for this test's
                    # comparison; ordinary clients use complete selected.
                    for key in original:
                        if key not in row:
                            row[key] = shown["data"]["task"][key]
        self.assertEqual(restored, expected)
        self.assertEqual(file_snapshot(self.root), before)
        return actual

    def test_ready_selection_preserves_next_order_limit_and_does_not_start(self):
        for index in range(7):
            self.add(f"Ready {index}", "--priority", "low")
        urgent = self.add("Urgent ready", "--priority", "urgent")

        payload = self.assert_matches_selection_and_display()

        self.assertEqual(payload["data"]["selection"], "next")
        self.assertEqual(payload["data"]["next"]["limit"], 5)
        self.assertEqual(payload["data"]["next"]["returned_count"], 5)
        self.assertEqual(payload["data"]["selected"]["task"]["task_id"], urgent["task_id"])
        self.assertEqual(payload["data"]["selected"]["task"]["status"], "ready")
        self.assertIsNone(payload["data"]["selected"]["latest_checkpoint"])
        self.assertEqual(set(payload["data"]["selected"]["completion_history"]), {"total", "legacy_history_incomplete"})
        self.assertEqual(set(payload["data"]["selected"]["review_evidence"]), {"gate", "counts", "current_receipts", "current_findings"})

    def test_authorized_registration_uses_prepared_context_and_existing_start_decision(self):
        outcomes = []
        call_counts = []
        for initial_status in ("ready", "in_progress"):
            with self.subTest(initial_status=initial_status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "repo"
                repo.mkdir()
                db = root / "taskgov.sqlite"
                initialize_taskgov_internal(repo=repo, db=db)
                calls = []

                def invoke(*args):
                    calls.append(args[:2])
                    result = run(db, repo, *args)
                    self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
                    return json.loads(result.stdout)["data"]

                # Both flows have the same explicit registration/start authority.
                added = invoke(
                    "task", "add", "--title", "Authorized guide update",
                    "--status", initial_status, "--review-tier", "2",
                    "--verification", "Check the complete guide",
                    "--contract-scope", "Update the guide only",
                    "--contract-acceptance", "The example matches the current CLI",
                    "--contract-constraints", "No runtime changes",
                    "--contract-authority-ref", "conversation:approved-guide-work",
                )
                self.assertEqual(added["context_preparation"]["status"], "ready")
                context = added["context_preparation"]["context"]
                task_id = added["task"]["task_id"]
                self.assertEqual(context["selected"]["task"]["task_id"], task_id)
                self.assertEqual(context["selected"]["contract"]["revision"], 1)
                if context["selected"]["task"]["status"] == "ready":
                    invoke("task", "edit", task_id, "--status", "in_progress")
                call_counts.append(len(calls))
                self.assertEqual(calls[0], ("task", "add"))
                self.assertNotIn(("task", "context"), calls)
                # Inspection is test evidence, not an extra normal-flow call.
                final = invoke("task", "show", task_id)
                outcomes.append((
                    {key: final["task"][key] for key in (
                        "title", "status", "kind", "lane", "lane_order", "review_tier",
                        "verification", "review_target_generation", "review_target_kind",
                        "completion_evidence_kind",
                    )},
                    {key: value for key, value in final["contract"].items() if key != "created_at"},
                    final["verification_evidence"]["gate"],
                    final["review_evidence"]["gate"],
                ))
        self.assertEqual(call_counts, [2, 1])
        self.assertEqual(outcomes[0], outcomes[1])

    def test_initial_start_remains_available_with_unrelated_held_work(self):
        paused = self.add("Held work", "--status", "in_progress")
        self.success("task", "edit", paused["task_id"], "--status", "paused",
                     "--pause-reason", "Waiting for an external decision")
        self.add("Blocked work", "--status", "blocked", "--blocked-reason", "Waiting")
        active = self.add("Authorized unrelated work", "--status", "in_progress")
        context = self.assert_matches_selection_and_display()
        self.assertEqual(context["data"]["selected"]["task"]["task_id"], active["task_id"])
        self.assertEqual(context["data"]["selection"], "current")

    def test_registration_only_does_not_displace_an_earlier_candidate(self):
        earlier = self.add("Earlier authorized work", "--priority", "urgent")
        registered = self.add("Register only, do not implement")
        context = self.assert_matches_selection_and_display()
        self.assertEqual(context["data"]["selected"]["task"]["task_id"], earlier["task_id"])
        shown = self.success("task", "show", registered["task_id"])
        self.assertEqual(shown["data"]["task"]["status"], "ready")

    def test_current_resumption_uses_first_active_status_and_skips_next(self):
        review = self.add("Review pending", "--status", "in_progress", "--priority", "urgent")
        self.success("task", "edit", review["task_id"], "--status", "review_pending")
        self.add("Urgent ready", "--priority", "urgent")
        for status in ("review_pending", "in_progress"):
            with self.subTest(status=status):
                if status == "in_progress":
                    self.success("task", "edit", review["task_id"], "--status", "ready")
                selected = review if status == "review_pending" else self.add(
                    "Active work", "--status", "in_progress", "--priority", "low"
                )
                expected = self.assert_matches_selection_and_display()
                with mock.patch.object(
                    cli_service, "_read_task_next", side_effect=AssertionError("next must be skipped")
                ), mock.patch.object(
                    cli_service, "handle_task_show", wraps=cli_service.handle_task_show
                ) as show:
                    actual = self.success("task", "context")
                self.assertEqual(actual, expected)
                self.assertEqual(actual["data"]["selected"]["task"]["task_id"], selected["task_id"])
                self.assertIsNone(actual["data"]["next"])
                self.assertEqual(show.call_count, 1)

    def test_held_work_keeps_recall_warning_and_allows_another_ready_lane(self):
        paused = self.add("Paused work", "--status", "in_progress")
        self.success("task", "edit", paused["task_id"], "--status", "paused", "--pause-reason", "Waiting for input")
        self.add("Blocked predecessor", "--kind", "sequential", "--lane", "blocked", "--order", "1",
                 "--status", "blocked", "--blocked-reason", "Dependency unavailable")
        self.add("Ineligible successor", "--kind", "sequential", "--lane", "blocked", "--order", "2", "--priority", "urgent")
        ready = self.add("Another ready lane", "--kind", "sequential", "--lane", "ready", "--order", "1")

        payload = self.assert_matches_selection_and_display()

        self.assertEqual(payload["data"]["selection"], "next")
        self.assertEqual(payload["data"]["selected"]["task"]["task_id"], ready["task_id"])
        self.assertEqual(payload["data"]["current"]["tasks"], [])
        self.assertEqual([row["status"] for row in self.success("task", "current")["data"]["tasks"]], ["paused", "blocked"])
        self.assertEqual([warning["code"] for warning in payload["warnings"]], ["paused_tasks_present"])

    def test_no_tasks_and_only_held_work_are_successful_no_selection(self):
        for held in (False, True):
            with self.subTest(held=held):
                if held:
                    self.add("Blocked work", "--status", "blocked", "--blocked-reason", "External dependency")
                with mock.patch.object(cli_service, "handle_task_show", side_effect=AssertionError("no Task to show")):
                    payload = self.assert_matches_selection_and_display()
                self.assertEqual(payload["data"]["selection"], "none")
                self.assertIsNone(payload["data"]["selected"])
                self.assertEqual(payload["data"]["next"]["tasks"], [])
                self.assertIsNotNone(payload["data"]["current"])

    def test_complete_contract_checkpoint_and_current_gates_match_show(self):
        scope = "Full scope 日本語. " * 100
        acceptance = "All acceptance conditions retained. " * 70
        task = self.add(
            "Governed task", "--status", "in_progress", "--review-tier", "2",
            "--verification", "Focused verification",
            "--contract-scope", scope, "--contract-acceptance", acceptance,
            "--contract-constraints", "Keep all current permission boundaries.",
            "--contract-authority-ref", "docs/specification.md",
        )
        checkpoint = self.success(
            "task", "checkpoint", task["task_id"], "--summary", "Initial inspection complete",
            "--next-action", "Run the remaining checks", "--unresolved-risk", "Review is pending",
        )["data"]["checkpoint"]
        target = self.success(
            "review", "target", "set", task["task_id"], "--kind", "diff_fingerprint",
            "--revision", "sha256:" + "a" * 64,
        )
        self.assertEqual(target["data"]["verification_route"], "receipt_required")
        self.success(
            "verification", "receipt", "add", task["task_id"], "--result", "fail",
            "--duration-ms", "1", "--scope-coverage", "full",
            "--expected-target-generation", str(target["data"]["task"]["review_target_generation"]),
        )

        selected = self.assert_matches_selection_and_display()["data"]["selected"]

        self.assertEqual(selected["contract"]["scope"], scope.strip())
        self.assertEqual(selected["contract"]["acceptance"], acceptance.strip())
        self.assertEqual(selected["latest_checkpoint"], checkpoint)
        self.assertFalse(selected["verification_evidence"]["gate"]["satisfied"])
        self.assertIsNotNone(selected["verification_evidence"]["gate"]["blocking_code"])
        self.assertEqual(selected["review_evidence"]["gate"]["required_independent_passes"], 2)

    def test_same_id_observation_change_retains_candidate_value_and_action(self):
        task = self.add("Candidate observation", "--status", "in_progress")
        original_show = cli_service.handle_task_show

        def changed_observation(context, **kwargs):
            result = original_show(context, **kwargs)
            changed = copy.deepcopy(result.data)
            changed["task"]["title"] = "Later detail observation"
            changed["suggested_next_action"] = "A distinct detail action"
            return cli_service.replace(result, data=changed)

        with mock.patch.object(cli_service, "handle_task_show", side_effect=changed_observation) as show:
            payload = self.success("task", "context")
        show.assert_called_once()
        data = payload["data"]
        candidate = data["current"]["tasks"][0]
        self.assertEqual(candidate["task_id"], task["task_id"])
        self.assertEqual(candidate["title"], "Candidate observation")
        self.assertNotEqual(candidate["suggested_next_action"], data["selected"]["suggested_next_action"])
        self.assertIn("latest_event", candidate)
        self.assertEqual(candidate["task_ref"], "/data/selected/task")
        self.assertNotIn("status", candidate)
        self.assertEqual(data["selected"]["task"]["title"], "Later detail observation")

    def test_context_shares_only_selected_candidate_and_reduces_response_bytes(self):
        selected = self.add("Selected 日本語", "--priority", "urgent")
        other = self.add("Other candidate", "--priority", "low")
        captured = []
        original_next = cli_service._task_next_result

        def capture(*args):
            result = original_next(*args)
            captured.append(copy.deepcopy(result.data))
            return result

        with mock.patch.object(cli_service, "_task_next_result", side_effect=capture):
            payload = self.assert_matches_selection_and_display()
        data = payload["data"]
        first, second = data["next"]["tasks"]
        self.assertEqual(first["task_id"], selected["task_id"])
        self.assertEqual(first["task_ref"], "/data/selected/task")
        self.assertNotIn("title", first)
        self.assertEqual(second["task_id"], other["task_id"])
        self.assertEqual(second, captured[-1]["tasks"][1])
        self.assertNotIn("task_ref", second)
        baseline = {**payload, "data": {**data, "next": captured[-1]}}
        size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.assertLess(size(payload), size(baseline))

    def test_invalid_profile_warning_is_carried_once_without_enabling_advisory(self):
        self.add("Ready work")
        with mock.patch.object(
            cli_service, "load_effort_profile",
            return_value=disabled_profile(present=True, diagnostic="profile_invalid"),
        ):
            payload = self.assert_matches_selection_and_display()
        self.assertFalse(payload["data"]["selected"]["effort_advisory_enabled"])
        self.assertEqual(payload["warnings"], [{
            "code": "effort_advisory_profile_invalid",
            "message": "Effort Advisory configuration is invalid; advisory remains disabled.",
            "suggested_action": "continue",
        }])

    def test_each_failed_phase_discards_partial_data_and_warnings_without_retry(self):
        first = self.add("First candidate", "--priority", "urgent")
        self.add("Second candidate")
        for phase, code, status in (("current", "database_busy", 2), ("next", "project_state_unreadable", 2), ("show", "not_found", 1)):
            with self.subTest(phase=phase):
                failure = CommandResult(
                    ok=False, command=f"task.{phase}", project_id=first["project_id"],
                    data={"must_not_leak": True},
                    warnings=[{"code": "must_not_leak", "message": "partial warning"}],
                    errors=[{"code": code, "message": "fixed safe failure"}], exit_code=status,
                )
                with mock.patch.object(cli_service, "_read_task_current", wraps=cli_service._read_task_current) as current, mock.patch.object(
                    cli_service, "_read_task_next", wraps=cli_service._read_task_next
                ) as next_read, mock.patch.object(cli_service, "handle_task_show", wraps=cli_service.handle_task_show) as show:
                    mocked = {"current": current, "next": next_read, "show": show}[phase]
                    mocked.return_value = failure
                    result = run(self.db, self.repo, "task", "context")
                payload = json.loads(result.stdout)
                self.assertEqual(result.returncode, status)
                self.assertEqual(payload["command"], "task.context")
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["data"], EMPTY_CONTEXT)
                self.assertEqual(payload["warnings"], [])
                self.assertEqual(payload["errors"], failure.errors)
                self.assertEqual(current.call_count, 1)
                self.assertEqual(next_read.call_count, int(phase != "current"))
                self.assertEqual(show.call_count, int(phase == "show"))
                if phase == "show":
                    self.assertEqual(show.call_args.args[0].args.task_id, first["task_id"])

    def test_complete_selected_current_batch_is_validated_before_selection(self):
        corrupt, _first = self.legacy_current_pair("Malformed later row")
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE tasks SET review_tier = 9 WHERE task_id = ?", (corrupt["task_id"],))
            connection.commit()
        before = file_snapshot(self.root)
        with mock.patch.object(cli_service, "handle_task_show", side_effect=AssertionError("invalid batch must stop")):
            result = run(self.db, self.repo, "task", "context")
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["data"], EMPTY_CONTEXT)
        self.assertEqual(payload["errors"], [{"code": "project_state_unreadable", "message": "project state could not be read safely"}])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(file_snapshot(self.root), before)

    def test_omitted_later_row_does_not_change_first_current_selection(self):
        _review, first = self.legacy_current_pair("Omitted review", "--lane", "l" * 30000)

        data = self.assert_matches_selection_and_display()["data"]

        self.assertEqual(data["selection"], "current")
        self.assertEqual(data["selected"]["task"]["task_id"], first["task_id"])
        self.assertEqual([row["task_id"] for row in data["current"]["tasks"]], [first["task_id"]])
        self.assertEqual(data["current"]["total_matching"], 2)
        self.assertTrue(data["current"]["truncated"])
        self.assertIsNone(data["next"])

    def test_omitted_later_row_does_not_change_first_next_selection(self):
        first = self.add("First ready", "--priority", "urgent")
        self.add("Omitted ready", "--lane", "l" * 20000)

        data = self.assert_matches_selection_and_display()["data"]

        self.assertEqual(data["selection"], "next")
        self.assertEqual(data["selected"]["task"]["task_id"], first["task_id"])
        self.assertEqual([row["task_id"] for row in data["next"]["tasks"]], [first["task_id"]])
        self.assertEqual(data["next"]["total_matching"], 2)
        self.assertTrue(data["next"]["truncated"])

    def test_context_rejects_all_leaf_options_before_state_access(self):
        with mock.patch.object(cli_service, "handle_command", side_effect=AssertionError("parse must fail first")):
            for arguments in (("--compact",), ("--audit",), ("--limit", "1"), ("--status", "paused"), ("--kind", "optional"), ("--lane", "lane"), ("--priority", "urgent"), ("tg_task_missing",)):
                with self.subTest(arguments=arguments):
                    result = run(self.db, self.repo, "task", "context", *arguments)
                    payload = json.loads(result.stdout)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(payload["command"], "parse")
                    self.assertEqual(payload["errors"][0]["code"], "invalid_argument")

    def test_missing_state_is_failure_not_successful_no_selection(self):
        missing = self.root / "missing" / "taskgov.sqlite"
        result = run(missing, self.repo, "task", "context")
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"], EMPTY_CONTEXT)
        self.assertEqual(payload["errors"][0]["code"], "db_not_initialized")
        self.assertFalse(missing.parent.exists())

    def test_physical_context_resolves_once_and_leaves_all_files_unchanged(self):
        install = make_physical_install(self.root / "physical")
        _setup(install)
        added = _run_cli(install, "task", "add", "--title", "Physical selection")
        self.assertEqual(added.returncode, 0, added.stdout)
        task_id = json.loads(added.stdout)["data"]["task"]["task_id"]
        before = file_snapshot(install.project_root)
        with mock.patch.object(cli_service, "resolve_project_state", wraps=cli_service.resolve_project_state) as resolve:
            result = _run_cli(install, "--read-only", "task", "context")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(json.loads(result.stdout)["data"]["selected"]["task"]["task_id"], task_id)
        self.assertEqual(file_snapshot(install.project_root), before)
        text = install.run("task", "context", "--read-only")
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn(task_id, text.stdout)
        self.assertIn("Physical selection", text.stdout)
        self.assertEqual(file_snapshot(install.project_root), before)


class ContextCandidateProjectionTests(unittest.TestCase):
    def test_reference_preserves_differing_types_nulls_and_candidate_only_fields(self):
        row = {"task_id": "t1", "title": "Title", "lane_order": None,
               "review_tier": 1, "ownership": {"generation": 1},
               "suggested_next_action": "Candidate action", "latest_event": None}
        selected = {"task": {"task_id": "t1", "title": "Title", "lane_order": None,
                             "review_tier": True, "ownership": {"generation": True}},
                    "suggested_next_action": "Candidate action", "latest_event": None}
        component = {"tasks": [row], "limit": 20, "returned_count": 1,
                     "total_matching": 2, "truncated": True}
        before = copy.deepcopy((component, selected))
        projected = cli_service.context_candidate_projection(
            component, selected, selected_task_ref="/data/selected/task",
        )
        self.assertEqual((component, selected), before)
        self.assertEqual(projected, {**component, "tasks": [{
            "task_id": "t1", "task_ref": "/data/selected/task", "review_tier": 1,
            "ownership": {"generation": 1}, "suggested_next_action": "Candidate action",
            "latest_event": None,
        }]})
        self.assertIs(type(projected["tasks"][0]["review_tier"]), int)
        self.assertIsNone(selected["task"]["lane_order"])

    def test_absent_detail_other_id_and_no_equal_fields_receive_no_reference(self):
        component = {"tasks": [{"task_id": "t1", "title": "Candidate"}],
                     "returned_count": 1, "truncated": False}
        for selected in (None, {"task": {"task_id": "t2", "title": "Candidate"}},
                         {"task": {"task_id": "t1", "title": "Changed"}}):
            with self.subTest(selected=selected):
                self.assertEqual(cli_service.context_candidate_projection(
                    component, selected, selected_task_ref="/data/selected/task",
                ), component)
        self.assertIsNone(cli_service.context_candidate_projection(
            None, {"task": {"task_id": "t1"}}, selected_task_ref="/data/selected/task",
        ))


class TaskContextCompactSelectionTests(unittest.TestCase):
    def test_selected_detail_reuses_validated_task_and_contract_pointer(self):
        from task_governance_tool import contracts, task_show_projection

        task = self.add("Reuse selected Task", "--contract-scope", "One selected Task",
                        "--contract-acceptance", "Same detail")
        for selection in ("next", "current"):
            if selection == "current":
                self.success("task", "edit", task["task_id"], "--status", "in_progress")
            with mock.patch.object(cli_service, "fetch_validated_current_task_row",
                                   wraps=cli_service.fetch_validated_current_task_row) as initial, \
                    mock.patch.object(task_show_projection, "fetch_validated_current_task_row",
                                      wraps=task_show_projection.fetch_validated_current_task_row) as detail, \
                    mock.patch.object(contracts, "read_current_contract",
                                      wraps=contracts.read_current_contract) as contract:
                result = _run_cli(self.install, "task", "context")
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)["data"]
            self.assertEqual(data["selection"], selection)
            self.assertEqual(data["selected"]["contract"]["revision"], 1)
            self.assertEqual(initial.call_count, 0)
            self.assertEqual(detail.call_count, 0)
            contract.assert_called_once()
            self.assertTrue(contract.call_args.kwargs["pointer_validated_in_snapshot"])

        with mock.patch.object(cli_service, "fetch_validated_current_task_row",
                               wraps=cli_service.fetch_validated_current_task_row) as initial, \
                mock.patch.object(task_show_projection, "fetch_validated_current_task_row",
                                  wraps=task_show_projection.fetch_validated_current_task_row) as detail:
            shown = _run_cli(self.install, "task", "show", task["task_id"])
        self.assertEqual(shown.returncode, 0, shown.stdout)
        self.assertEqual(initial.call_count, 1)
        self.assertEqual(detail.call_count, 0)

    def test_fixed_layout_current_checkpoint_is_reused_on_retained_read(self):
        from task_governance_tool import checkpoints
        task = self.add("Retained checkpoint", "--status", "in_progress")
        for present in (False, True):
            if present:
                self.success("task", "checkpoint", task["task_id"], "--summary", "Resume here",
                             "--next-action", "Continue verification")
            expected = self.success("task", "show", task["task_id"])["data"]["latest_checkpoint"]
            with mock.patch.object(checkpoints, "read_latest_checkpoint", wraps=checkpoints.read_latest_checkpoint) as read:
                result = _run_cli(self.install, "task", "context")
                self.assertEqual(result.returncode, 0, result.stdout)
                actual = json.loads(result.stdout)["data"]
            self.assertEqual(actual["selected"]["latest_checkpoint"], expected)
            self.assertEqual(read.call_count, 1)

        # Damage only this disposable checkpoint, preserving trigger DDL.
        with closing(sqlite3.connect(self.install.db_path)) as core, core:
            core.execute("PRAGMA ignore_check_constraints=ON")
            triggers = core.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='task_checkpoints'").fetchall()
            for name, _ in triggers:
                core.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
            core.execute("UPDATE task_checkpoints SET unresolved_risks_json='{}'")
            for _, sql in triggers:
                core.execute(sql)
        before = file_snapshot(self.install.project_root)
        for leaf in (("context",), ("current",), ("current", "--compact")):
            result = _run_cli(self.install, "task", *leaf)
            self.assertEqual(result.returncode, 2, result.stdout)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertIsNone(payload["data"].get("selected"))
            self.assertFalse(payload["data"].get("tasks"))
        self.assertEqual(file_snapshot(self.install.project_root), before)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name))
        self.success("setup")

    def success(self, *arguments):
        result = self.install.run(*arguments, "--json")
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        return payload

    def add(self, title, *arguments):
        data = self.success("task", "add", "--title", title, *arguments)["data"]
        return registration_body(data, data, "task")

    def assert_empty_compact_preserves_selection(self, component, selected, selection):
        before = file_snapshot(self.install.project_root)
        compact = self.success("task", component, "--compact")["data"]
        self.assertEqual(compact["tasks"], [])
        self.assertGreater(compact["total_matching"], 0)
        self.assertEqual(compact["returned_count"], 0)
        self.assertTrue(compact["truncated"])
        shown = self.success("task", "show", selected["task_id"])["data"]

        payload = self.success("task", "context", "--read-only")

        if component == "current" and selection == "next":
            self.assertEqual(payload["data"][component], {
                **compact, "tasks": [], "total_matching": 0,
                "returned_count": 0, "truncated": False,
            })
        else:
            self.assertEqual(payload["data"][component], compact)
        self.assertEqual(payload["data"]["selection"], selection)
        self.assertEqual(payload["data"]["selected"], shown)
        self.assertEqual(payload["data"]["selected"]["task"]["task_id"], selected["task_id"])
        self.assertEqual(payload["data"]["selected"]["task"]["status"], selected["status"])
        self.assertEqual(file_snapshot(self.install.project_root), before)
        return payload["data"]

    def assert_omitted_current_resumes(self, status):
        active = self.add("Omitted current", "--status", "in_progress", "--lane", "l" * 30000)
        if status == "review_pending":
            active = self.success("task", "edit", active["task_id"], "--status", status)["data"]["task"]
        self.add("Other ready work", "--priority", "urgent")

        data = self.assert_empty_compact_preserves_selection("current", active, "current")

        self.assertIsNone(data["next"])

    def test_public_cli_resumes_active_when_compact_current_is_empty(self):
        self.assert_omitted_current_resumes("in_progress")

    def test_public_cli_resumes_review_pending_when_compact_current_is_empty(self):
        self.assert_omitted_current_resumes("review_pending")

    def test_public_cli_selects_ready_when_compact_next_is_empty(self):
        ready = self.add("Omitted ready", "--lane", "l" * 20000, "--priority", "urgent")
        self.add("Later ready")

        self.assert_empty_compact_preserves_selection("next", ready, "next")

    def test_public_cli_omitted_held_work_allows_a_separate_ready_lane(self):
        self.add("Omitted blocker", "--kind", "sequential", "--lane", "held", "--order", "1",
                 "--status", "blocked", "--blocked-reason", "r" * 30000)
        self.add("Ineligible successor", "--kind", "sequential", "--lane", "held", "--order", "2",
                 "--priority", "urgent")
        ready = self.add("Separate ready lane", "--kind", "sequential", "--lane", "ready", "--order", "1")

        data = self.assert_empty_compact_preserves_selection("current", ready, "next")

        self.assertEqual([row["task_id"] for row in data["next"]["tasks"]], [ready["task_id"]])


class TaskContextRunnerTests(unittest.TestCase):
    def test_marker_two_discards_retained_checkpoint_before_fresh_detail(self):
        import argparse
        from task_governance_tool import checkpoints
        from task_governance_tool.session_identity import capture_caller_identity
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import _launch, _persist_terminal
        from task_governance_tool import verification_runner_selection as selection

        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            pending = run(fixture.db, fixture.repo, "task", "edit", fixture.task_id, "--status", "review_pending")
            self.assertEqual(pending.returncode, 0, pending.stdout)
            def checkpoint(summary):
                result = run(fixture.db, fixture.repo, "task", "checkpoint", fixture.task_id,
                             "--summary", summary, "--next-action", "Continue review")
                self.assertEqual(result.returncode, 0, result.stdout)
                return json.loads(result.stdout)["data"]["checkpoint"]
            old = checkpoint("Before physical selection")
            reads, fresh = [], []
            read = checkpoints.read_latest_checkpoint
            select = cli_service.select_current_verification_runner_basis
            with closing(storage.connect_initialized_readonly(fixture.target, operation_local=True)) as retained:
                if not retained.in_transaction:
                    retained.execute("BEGIN")
                context = cli_service.CommandContext(command="task.context", repo=str(fixture.repo),
                    repo_explicit=True, json_output=True, read_only=True, args=argparse.Namespace(),
                    caller=capture_caller_identity(), target_override=fixture.target,
                    read_connection_override=retained)
                def observed(core, **kwargs):
                    value = read(core, **kwargs)
                    reads.append((core, value))
                    return value
                def physical(*args, **kwargs):
                    self.assertEqual(reads, [(retained, old)])
                    with self.assertRaises(sqlite3.ProgrammingError):
                        retained.execute("SELECT 1")
                    fresh.append(checkpoint("After physical selection"))
                    return select(*args, **kwargs)
                with mock.patch.object(selection, "_stored_runner_physical_basis_matches", return_value=True), \
                        mock.patch.object(checkpoints, "read_latest_checkpoint", side_effect=observed), \
                        mock.patch.object(cli_service, "select_current_verification_runner_basis", side_effect=physical) as select_runner, \
                        mock.patch.object(cli_service, "show_task", wraps=cli_service.show_task) as show:
                    result = cli_service.handle_task_context(context)
                self.assertTrue(result.ok, result.errors)
                self.assertEqual(result.data["selection"], "current")
                select_runner.assert_called_once()
                show.assert_called_once()
                self.assertNotIn("checkpoint_read", show.call_args.kwargs)
                self.assertEqual(len(reads), 2)
                self.assertIsNot(reads[1][0], retained)
                self.assertNotEqual(old["checkpoint_id"], fresh[0]["checkpoint_id"])
                self.assertEqual(reads[1][1], fresh[0])
                self.assertEqual(result.data["selected"]["latest_checkpoint"], fresh[0])

    def test_marker_two_selected_detail_reuses_the_exact_show_runner_route(self):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import _launch, _persist_terminal
        from task_governance_tool import verification_runner_selection as selection

        for branch in ("pass", "blocking", "fallback"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = _launch(fixture)
                _persist_terminal(fixture, intent, branch=branch)
                pending = run(fixture.db, fixture.repo, "task", "edit", fixture.task_id, "--status", "review_pending")
                self.assertEqual(pending.returncode, 0, pending.stdout)
                before = file_snapshot(Path(temporary))
                with mock.patch.object(selection, "_stored_runner_physical_basis_matches", return_value=True):
                    expected = run(fixture.db, fixture.repo, "task", "show", fixture.task_id)
                    with mock.patch.object(cli_service, "select_current_verification_runner_basis", wraps=cli_service.select_current_verification_runner_basis) as select_runner, mock.patch.object(
                        cli_service, "show_task", wraps=cli_service.show_task
                    ) as show:
                        actual = run(fixture.db, fixture.repo, "task", "context")
                self.assertEqual(expected.returncode, 0, expected.stdout)
                self.assertEqual(actual.returncode, 0, actual.stdout)
                self.assertEqual(json.loads(actual.stdout)["data"]["selected"], json.loads(expected.stdout)["data"])
                self.assertEqual(select_runner.call_count, 1)
                self.assertEqual(show.call_count, 1)
                selected = json.loads(actual.stdout)["data"]["selected"]
                from task_governance_tool.tasks import suggested_next_action
                if branch == "pass":
                    self.assertIsNone(selected["verification_evidence"]["current_receipt"])
                    self.assertIn("more independent PASS", selected["suggested_next_action"])
                else:
                    self.assertEqual(selected["suggested_next_action"], suggested_next_action(selected["task"]))
                self.assertEqual(file_snapshot(Path(temporary)), before)

    def test_context_does_not_audit_unrelated_runner_history(self):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from task_governance_tool import verification_runner_selection as selection
        from tests.test_m243c_runner_gate import (
            _add_runner_task, _corrupt_runner_observation_digest, _launch, _persist_terminal,
        )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            selected_id = fixture.task_id
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            released = run(fixture.db, fixture.repo, "task", "edit", selected_id, "--status", "review_pending")
            self.assertEqual(released.returncode, 0, released.stdout)
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "22222222-2222-4222-8222-222222222222"}):
                unrelated_id = _add_runner_task(fixture, title="Unrelated corrupt Runner graph")
                fixture.task_id = unrelated_id
                _prepared, unrelated_intent = _launch(fixture)
                _persist_terminal(fixture, unrelated_intent, branch="pass")
            fixture.task_id = selected_id
            _corrupt_runner_observation_digest(fixture.db, task_id=unrelated_id)
            before = file_snapshot(Path(temporary))
            current = run(fixture.db, fixture.repo, "task", "current", "--compact")
            # This injected graph fixture has no installed physical package.
            # Use the same physical-selection seam as the adjacent Runner test.
            with mock.patch.object(selection, "_stored_runner_physical_basis_matches", return_value=True):
                context = run(fixture.db, fixture.repo, "task", "context")
            self.assertEqual(current.returncode, 0, current.stdout)
            self.assertEqual(context.returncode, 0, context.stdout)
            payload = json.loads(context.stdout)
            self.assertEqual(payload["errors"], [])
            self.assertEqual(payload["data"]["selected"]["task"]["task_id"], selected_id)
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(file_snapshot(Path(temporary)), before)


if __name__ == "__main__":
    unittest.main()
