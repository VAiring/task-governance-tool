from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import (
    file_snapshot,
    initialize_taskgov_internal,
    make_physical_install,
    run_taskgov_internal,
)
from tests.test_m17_cli_consumer_hardening import _run_cli, _setup

from task_governance_tool import cli as cli_service
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
        return self.success("task", "add", "--title", title, *arguments)["data"]["task"]

    def assert_matches_selection_and_display(self):
        before = file_snapshot(self.root)
        current_batch = self.success("task", "current")
        current = self.success("task", "current", "--compact")
        warnings = list(current["warnings"])
        chosen = next(
            (row for row in current_batch["data"]["tasks"]
             if row["status"] in {"in_progress", "review_pending"}),
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
        self.assertEqual(actual["data"], {
            "selection": selection,
            "current": current["data"],
            "next": next_payload["data"] if next_payload else None,
            "selected": shown["data"] if shown else None,
        })
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
        review = self.add("Review pending", "--status", "review_pending", "--priority", "urgent")
        self.add("Urgent ready", "--priority", "urgent")
        for status in ("review_pending", "in_progress"):
            with self.subTest(status=status):
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
        self.assertEqual([row["status"] for row in payload["data"]["current"]["tasks"]], ["paused", "blocked"])
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
        self.add("Valid first", "--status", "in_progress", "--priority", "urgent")
        corrupt = self.add("Malformed later row", "--status", "review_pending")
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
        first = self.add("First active", "--status", "in_progress")
        self.add("Omitted review", "--status", "review_pending", "--blocked-reason", "r" * 30000)

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


class TaskContextCompactSelectionTests(unittest.TestCase):
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
        return self.success("task", "add", "--title", title, *arguments)["data"]["task"]

    def assert_empty_compact_preserves_selection(self, component, selected, selection):
        before = file_snapshot(self.install.project_root)
        compact = self.success("task", component, "--compact")["data"]
        self.assertEqual(compact["tasks"], [])
        self.assertGreater(compact["total_matching"], 0)
        self.assertEqual(compact["returned_count"], 0)
        self.assertTrue(compact["truncated"])
        shown = self.success("task", "show", selected["task_id"])["data"]

        payload = self.success("task", "context", "--read-only")

        self.assertEqual(payload["data"][component], compact)
        self.assertEqual(payload["data"]["selection"], selection)
        self.assertEqual(payload["data"]["selected"], shown)
        self.assertEqual(payload["data"]["selected"]["task"]["task_id"], selected["task_id"])
        self.assertEqual(payload["data"]["selected"]["task"]["status"], selected["status"])
        self.assertEqual(file_snapshot(self.install.project_root), before)
        return payload["data"]

    def assert_omitted_current_resumes(self, status):
        active = self.add("Omitted current", "--status", status, "--blocked-reason", "r" * 30000)
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
    def test_marker_two_selected_detail_reuses_the_exact_show_runner_route(self):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import _launch, _persist_terminal
        from task_governance_tool import verification_runner_selection as selection

        for branch in ("pass", "blocking", "fallback"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = _launch(fixture)
                _persist_terminal(fixture, intent, branch=branch)
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
                self.assertEqual(file_snapshot(Path(temporary)), before)

    def test_context_preserves_global_admission_before_task_local_runner_detail(self):
        from tests.test_m242_runner_service import RunnerServiceFixture
        from tests.test_m243c_runner_gate import (
            _add_runner_task, _corrupt_runner_observation_digest, _launch, _persist_terminal,
        )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            selected_id = fixture.task_id
            _prepared, intent = _launch(fixture)
            _persist_terminal(fixture, intent, branch="pass")
            unrelated_id = _add_runner_task(fixture, title="Unrelated corrupt Runner graph")
            fixture.task_id = unrelated_id
            _prepared, unrelated_intent = _launch(fixture)
            _persist_terminal(fixture, unrelated_intent, branch="pass")
            fixture.task_id = selected_id
            _corrupt_runner_observation_digest(fixture.db, task_id=unrelated_id)
            before = file_snapshot(Path(temporary))
            with mock.patch.object(cli_service, "handle_task_show", side_effect=AssertionError("global admission must fail first")):
                current = run(fixture.db, fixture.repo, "task", "current", "--compact")
                context = run(fixture.db, fixture.repo, "task", "context")
            self.assertEqual(current.returncode, 2)
            self.assertEqual(context.returncode, current.returncode)
            payload = json.loads(context.stdout)
            self.assertEqual(payload["errors"], json.loads(current.stdout)["errors"])
            self.assertEqual(payload["data"], EMPTY_CONTEXT)
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(file_snapshot(Path(temporary)), before)


if __name__ == "__main__":
    unittest.main()
