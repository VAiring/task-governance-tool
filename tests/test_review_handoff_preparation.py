"""Start before the Packet-producing command, not with a prepared Packet file."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.m14_test_support import file_snapshot, make_physical_install
from tests.test_review_results import encode, receipt, FINGERPRINT
from task_governance_tool import review_handoff as handoff
from task_governance_tool import review_handoff_preparation as preparation
from task_governance_tool.review_results import review_result_instructions


def request_commands(request):
    """Select the generated invocations for shell execution in transport tests."""
    (read,) = (line for line in request.splitlines()
              if "--packet=" in line and "--output=" not in line
              and shlex.split(line.removeprefix("& "))[3:4] == ["read"])
    (save,) = (line for line in request.splitlines() if "--output=" in line)
    return read, save.removeprefix("'@ | ").removesuffix(" <<'TASKGOV_REVIEW_RESULT'")


class PreparationFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install = make_physical_install(Path(temporary.name).resolve(), git_managed=True)
        self.root = self.install.project_root
        with (self.root / ".gitignore").open("a", encoding="utf-8") as target:
            target.write("/reviews/\n/-reviews/\n")
        self.cli("setup")
        self.helper = self.install.skill_root / "scripts/review_handoff.py"

    def cli(self, *args):
        result = self.install.run(*args, "--json")
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        return json.loads(result.stdout)["data"]

    def task(self, verification=""):
        declaration = (["--verification", verification] if verification else
                       ["--verification-not-required", "Isolated transport fixture without executable changes"])
        task_id = self.cli("task", "add", "--title", "Full pipeline 日本語", "--review-tier", "2",
                           "--status", "in_progress", *declaration)["task"]["task_id"]
        self.cli("task", "edit", task_id, "--status", "review_pending")
        return task_id

    @staticmethod
    def review_environment(index=0):
        return {**os.environ, "CODEX_THREAD_ID": f"00000000-0000-4000-8000-{200 + index:012d}"}

    def invoke(self, *args, raw=None, reviewer=None):
        return subprocess.run([sys.executable, "-I", "-S", str(self.helper), *args],
                              input=raw, capture_output=True, cwd=self.root, check=False,
                              env=None if reviewer is None else self.review_environment(reviewer))

    def prepare(self, task, action="target", options=None, directory="reviews/g1", raw=None):
        options = options if options is not None else ["--kind", "diff_fingerprint", "--revision", FINGERPRINT]
        completed = self.invoke("prepare", "--repo", str(self.root), "--directory=" + directory,
                                action, task, *options, raw=raw)
        return completed, json.loads(completed.stdout)


class ReviewRequestLayoutTests(unittest.TestCase):
    def test_shared_rules_precede_case_commands_without_changing_bindings(self):
        prefixes = []
        for platform in ("nt", "posix"):
            for records_only in (False, True):
                for case, directory in enumerate(("reviews/first", "-reviews/日本語's ‘quoted’; $x & case")):
                    repo = Path("project-" + str(case)).absolute()
                    packet = directory + "/packet.json"
                    args = SimpleNamespace(directory=directory, reviewers=case + 1,
                                           records_only=records_only, task_id="case-" + str(case))
                    # Replace only this module's OS selector, not pathlib's host.
                    with mock.patch.object(preparation, "os", SimpleNamespace(name=platform)), \
                         mock.patch.object(preparation, "_shell", wraps=preparation._shell) as shell, \
                         mock.patch("task_governance_tool.setup_feature_config.read_choices", return_value=(None, {})):
                        result = preparation._requests(repo, args, packet)
                    entry = str(Path(preparation.__file__).parent.parent / "review_handoff.py")
                    base = [sys.executable, *(["-I", "-S", "-B"] if records_only else ["-B"]),
                            entry, *(["--records-only"] if records_only else [])]
                    common = ["--repo=" + str(repo), "--packet=" + packet]
                    expected_calls = [[*base, "read", *common]]
                    for index, row in enumerate(result["review_requests"], 1):
                        self.assertEqual(set(row), {"result_path", "request"})
                        output = directory + f"/review-{index}.json"
                        self.assertEqual(row["result_path"], output)
                        prefix, commands = row["request"].split("\n\nRead command:\n")
                        read, save = commands.split("\n\nSave command:\n")
                        prefixes.append(prefix)
                        for value in (str(repo), directory, packet, output, str(sys.executable)):
                            self.assertNotIn(value, prefix)
                        self.assertEqual(row["request"].count("<completed original JSON>"), 1)
                        expected_calls.append([*base, "save", *common, "--output=" + output])
                        if platform == "posix":
                            self.assertEqual(shlex.split(read), expected_calls[0])
                            self.assertEqual(shlex.split(save.split(" <<'TASKGOV_REVIEW_RESULT'", 1)[0]), expected_calls[-1])
                            self.assertTrue(save.endswith("\nTASKGOV_REVIEW_RESULT"))
                        else:
                            self.assertTrue(read.startswith("& "))
                            self.assertTrue(save.startswith("$OutputEncoding = [Console]::InputEncoding = "))
                            self.assertIn("\n'@ | & ", save)
                    expected_calls.append([*base, "submit", *common, "--", *(row["result_path"] for row in result["review_requests"])])
                    self.assertEqual([call.args[0] for call in shell.call_args_list], expected_calls)
        self.assertEqual(len(set(prefixes)), 1)
        # Conditions and prohibitions stay ahead of dynamic material. Independent
        # review checks full meaning; these guard the clauses most easily lost.
        for rule in ("complete exact target under current project authority",
                     "no Skill operating guide or internal fingerprint implementation is a prerequisite",
                     "scope, acceptance, constraints and verification expectation",
                     "required source/tests, discovered dependencies and retrieval exceptions",
                     "when they are actually governing or reviewed material",
                     "ask the caller, do not infer independence",
                     "result_instructions, actual judgment and provenance, and all Findings",
                     "severity, exact file/line, risks and recommended correction",
                     "Do not search internals to guess unknown model/Skill identity or version",
                     "run this fixed save operation, not new save/validation code",
                     "return path, verdict and Finding count once in the final response",
                     "do not echo JSON or send a duplicate normal-success notification",
                     "read mismatch, unavailable/truncated material, unknown role, save failure or lost acknowledgement",
                     "do not claim PASS from incomplete inspection or success from an unknown outcome",
                     "Preserve failed residue; do not overwrite or blindly repeat a save",
                     "Do not manage Tasks, reset targets, register DB evidence, complete work or implement transport/recovery code"):
            self.assertIn(rule, prefixes[0])


class InstalledPreparationTests(PreparationFixture):
    def test_native_directory_modes_preserve_existing_parent(self):
        parent = self.root / "reviews"
        parent.mkdir()
        before = stat.S_IMODE(parent.stat().st_mode)
        completed, result = self.prepare(self.task(), directory="reviews/new/ancestors/g1")
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        self.assertEqual(result["handoff"]["status"], "ready")
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), before)
        if os.name != "nt":
            for path in (parent / "new", parent / "new/ancestors", parent / "new/ancestors/g1"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        # Windows mode bits do not prove DACL inheritance; its native suite
        # reads the ACL instead. The POSIX mode assertion runs only on POSIX.

    def test_padded_task_id_remains_canonical_through_target_receipt_and_recovery(self):
        task = self.task("Focused checks")
        padded = " \t" + task + "\t "
        completed, result = self.prepare(padded)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(result["source"]["task_id"], task)
        completed, result = self.prepare(padded, "receipt", ["--from-stdin"], raw=encode({
            "version": 1, "task_id": task, "result": "pass", "duration_ms": 1,
            "scope_coverage": "full", "expected_target_generation": 1}))
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(result["source"]["task_id"], task)
        completed, result = self.prepare(padded, "recover", ["--verification-receipt-id",
            result["source"]["verification_receipt_id"]], "reviews/recovery")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        packet = json.loads((self.root / result["handoff"]["packet_path"]).read_bytes())
        self.assertEqual(packet["task"]["task_id"], task)

    def test_generated_posix_arguments_preserve_hyphen_leading_paths(self):
        task = self.task()
        completed, result = self.prepare(task, directory="-reviews/日本語 quoted's path")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        packet_path = result["handoff"]["packet_path"]
        packet = json.loads((self.root / packet_path).read_bytes())
        # Exercise POSIX shell quoting/argv on every host; Windows also executes
        # its own generated command in the separate native PowerShell test.
        args = mock.Mock(directory="-reviews/日本語 quoted's path", reviewers=1)
        with mock.patch.object(preparation, "_shell", side_effect=shlex.join), \
             mock.patch.object(preparation, "__file__", str(self.install.skill_root /
                 "scripts/task_governance_tool/review_handoff_preparation.py")):
            generated = preparation._requests(self.root, args, packet_path)
        payload = packet["result_template"]
        payload["receipts"] = [receipt("quoted-path-reviewer")]
        read, save = request_commands(generated["review_requests"][0]["request"])
        for command, raw, reviewer in ((read, None, 0),
                                       (save, encode(payload), 0),
                                       (generated["submit_command"], None, None)):
            invoked = subprocess.run(shlex.split(command), input=raw, capture_output=True,
                                     cwd=self.root, check=False,
                                     env=None if reviewer is None else self.review_environment(reviewer))
            self.assertEqual(invoked.returncode, 0, invoked.stdout or invoked.stderr)
        self.assertEqual((self.root / generated["review_requests"][0]["result_path"]).read_bytes(), encode(payload))

    def test_installed_target_through_original_registration_and_stale_rejection(self):
        task = self.task()
        completed, result = self.prepare(task)
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertEqual(result["source"]["verification_route"], "not_required")
        context = result["handoff"]
        self.assertEqual({"status", "packet_path", "review_requests", "review_wait", "submit_command"}, set(context))
        self.assertEqual("references/review_wait.md#normal-wait", context["review_wait"]["guide"])
        packet = json.loads((self.root / context["packet_path"]).read_bytes())
        self.assertEqual(packet["task"]["task_id"], task)
        self.assertEqual(len(context["review_requests"]), 2)
        original_paths = []
        originals = []
        for index, request in enumerate(context["review_requests"]):
            self.assertEqual(set(request), {"result_path", "request"})
            read, save = request_commands(request["request"])
            self.assertEqual(request["request"].count(read), 1)
            self.assertEqual(request["request"].count(save), 1)
            self.assertNotIn(context["submit_command"], request["request"])
            self.assertNotIn("--role", request["request"])
            displayed = self.invoke("read", "--repo", str(self.root), "--packet", context["packet_path"],
                                    reviewer=index)
            self.assertEqual(displayed.returncode, 0, displayed.stdout)
            payload = json.loads(displayed.stdout)["result_template"]
            self.assertEqual(payload, packet["result_template"])
            payload["receipts"] = [receipt("reviewer-" + str(index), findings=[{
                "severity": "low", "summary": "example.py:1 日本語 🚀 retained"}])]
            raw = b"\n " + encode(payload) + b"\n"
            originals.append(raw)
            original_paths.append(request["result_path"])
            saved = self.invoke("save", "--repo", str(self.root), "--packet", context["packet_path"],
                                "--output", request["result_path"], raw=raw, reviewer=index)
            self.assertEqual(saved.returncode, 0, saved.stdout)
            self.assertEqual(json.loads(saved.stdout)["finding_count"], 1)
        args = ["submit", "--repo", str(self.root), "--packet", context["packet_path"], *original_paths]
        submitted = self.invoke(*args)
        self.assertEqual(submitted.returncode, 0, submitted.stdout)
        self.assertEqual(len(json.loads(submitted.stdout)["data"]["receipts"]), 2)
        self.assertNotEqual(self.invoke(*args).returncode, 0)
        self.assertEqual(self.cli("task", "show", task)["review_evidence"]["counts"]["receipts_current_generation"], 2)
        self.cli("review", "target", "set", task, "--kind", "diff_fingerprint", "--revision", FINGERPRINT)
        self.assertNotEqual(self.invoke(*args).returncode, 0)
        for path, raw in zip(original_paths, originals):
            self.assertEqual((self.root / path).read_bytes(), raw)
        self.assertEqual(sorted(path.name for path in (self.root / "reviews/g1").iterdir()),
                         ["packet.json", "review-1.json", "review-1.json.session.json",
                          "review-2.json", "review-2.json.session.json"])

    def test_receipt_required_then_manual_and_structured_receipt_and_recovery(self):
        for from_stdin in (False, True):
            with self.subTest(from_stdin=from_stdin):
                task = self.task("Focused checks")
                directory = "reviews/stdin" if from_stdin else "reviews/manual"
                completed, result = self.prepare(task, directory=directory)
                self.assertEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(result["source"]["verification_route"], "receipt_required")
                self.assertEqual(result["handoff"]["status"], "not_applicable")
                self.assertFalse((self.root / directory).exists())
                generation = result["source"]["review_target"]["generation"]
                raw = encode({"version": 1, "task_id": task, "result": "pass", "duration_ms": 10,
                              "scope_coverage": "full", "expected_target_generation": generation}) if from_stdin else None
                options = ["--from-stdin"] if from_stdin else ["--result", "pass", "--duration-ms", "10",
                    "--scope-coverage", "full", "--expected-target-generation", str(generation)]
                completed, result = self.prepare(task, "receipt", options, directory, raw)
                self.assertEqual(completed.returncode, 0, completed.stdout)
                receipt_id = result["source"]["verification_receipt_id"]
                completed, recovered = self.prepare(task, "recover", ["--verification-receipt-id", receipt_id], directory + "-recovery")
                self.assertEqual(completed.returncode, 0, completed.stdout)
                # CLI serializers can order keys differently; the complete Packet
                # object (unlike original reviewer bytes) is the transport contract.
                earlier = json.loads((self.root / result["handoff"]["packet_path"]).read_bytes())
                later = json.loads((self.root / recovered["handoff"]["packet_path"]).read_bytes())
                # The read timestamp advances; every evidence fact and binding
                # must remain identical on bound preparation recovery.
                self.assertGreaterEqual(later["verification_evidence"].pop("observed_at"),
                                        earlier["verification_evidence"].pop("observed_at"))
                self.assertEqual(later, earlier)
                # Release the combined execution/review slot between input forms.
                self.cli("task", "edit", task, "--status", "ready")

    def test_bound_recovery_does_not_write_another_target_and_conflicting_binding_fails(self):
        task = self.task()
        _, result = self.prepare(task)
        binding = result["source"]["preparation_binding"]
        completed, recovered = self.prepare(task, "recover", ["--expected-binding", binding], "reviews/recovery")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(json.loads((self.root / recovered["handoff"]["packet_path"]).read_bytes())["review_target"]["generation"], 1)
        completed, failed = self.prepare(task, "recover", ["--expected-binding", binding,
            "--verification-receipt-id", "invalid"], "reviews/invalid")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((self.root / "reviews/invalid").exists())

    def test_actual_source_failure_and_blocked_receipt_do_not_create_handoff(self):
        task = self.task("Focused checks")
        completed, failed = self.prepare(task, options=["--kind", "git_snapshot", "--revision", "forbidden"])
        self.assertNotEqual(completed.returncode, 0)
        # This service rejection actually precedes a write, but its envelope
        # cannot prove that. Do not infer a phase from the error code.
        self.assertEqual(failed["operation_status"], "unknown")
        self.assertEqual(failed["warnings"][-1]["code"], "handoff_outcome_unknown")
        self.assertNotIn("review_requests", failed["handoff"])
        self.assertFalse((self.root / "reviews").exists())
        self.prepare(task)
        completed, blocked = self.prepare(task, "receipt", ["--result", "fail", "--duration-ms", "10",
            "--scope-coverage", "full", "--expected-target-generation", "1"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(blocked["operation_status"], "succeeded", blocked)
        self.assertEqual(blocked["handoff"]["status"], "blocked")
        self.assertTrue(blocked["source"]["verification_receipt_id"])
        self.assertFalse((self.root / "reviews").exists())

    def test_public_parser_rejection_is_failed_without_target_or_runner_write(self):
        task = self.task()
        completed, failed = self.prepare(task, options=["--kind", "git_commit"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(failed["operation_status"], "failed")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["source_exit_code"], 1)
        self.assertEqual(self.cli("task", "show", task)["task"]["review_target_generation"], 0)
        self.assertFalse((self.root / "reviews").exists())

    def test_complete_public_packet_is_required_before_save_or_registration(self):
        task = self.task()
        completed, ready = self.prepare(task)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        context = ready["handoff"]
        packet_path = self.root / context["packet_path"]
        packet = json.loads(packet_path.read_bytes())
        original = copy.deepcopy(packet["result_template"])
        original["receipts"] = [receipt()]
        result_path = context["review_requests"][0]["result_path"]
        (self.root / result_path).write_bytes(encode(original))
        mutations = {
            "verification_missing": lambda p: p["task"].pop("verification"),
            "verification_reason_missing": lambda p: p["task"].pop("verification_not_required_reason"),
            "scope_missing": lambda p: p["contract"].pop("scope"),
            "acceptance_missing": lambda p: p["contract"].pop("acceptance"),
            "scope_type": lambda p: p["contract"].update(scope=[]),
            "authority_type": lambda p: p["contract"].update(authority_ref=None),
            "authority_size": lambda p: p["contract"].update(authority_ref="x" * 501),
            "authority_multiline": lambda p: p["contract"].update(authority_ref="first\nsecond"),
            "authority_private": lambda p: p["contract"].update(authority_ref="password=never-retain"),
            "authority_legacy_counter": lambda p: p["contract"].update(authority_ref="dispatch_authorization=7"),
            "unknown_contract_field": lambda p: p["contract"].update(unknown="context"),
            "conflicting_declaration": lambda p: p["task"].update(verification="Run checks"),
            "empty_instructions": lambda p: p.update(result_instructions=[]),
            "target_template_mismatch": lambda p: p["review_target"].update(generation=2),
        }
        run = subprocess.run
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                broken = copy.deepcopy(packet)
                mutate(broken)
                packet_path.write_bytes(encode(broken))
                with self.assertRaises((handoff.HandoffError, handoff.TaskValidationError)):
                    preparation._packet(broken, task)
                with self.assertRaises((handoff.HandoffError, handoff.TaskValidationError)):
                    preparation.read_for_reviewer(self.root, context["packet_path"])
                with self.assertRaises((handoff.HandoffError, handoff.TaskValidationError)):
                    handoff.save(self.root, context["packet_path"], "reviews/g1/unsaved.json", encode(original), ())
                self.assertFalse((self.root / "reviews/g1/unsaved.json").exists())
                output = io.StringIO()
                with mock.patch.object(handoff.subprocess, "run", wraps=run) as calls, redirect_stdout(output):
                    code = handoff.main(["submit", "--repo", str(self.root), "--packet",
                                         context["packet_path"], result_path])
                self.assertEqual(code, 1, output.getvalue())
                self.assertFalse(json.loads(output.getvalue())["ok"])
                self.assertFalse(any("review" in call.args[0] and "result" in call.args[0]
                                     for call in calls.call_args_list), calls.call_args_list)
                self.assertEqual((self.root / result_path).read_bytes(), encode(original))


class RunnerPreparationOutcomeTests(unittest.TestCase):
    """Real public parser/service and isolated storage; inject only boundary faults.

    The capture bridge replaces the process boundary to permit fault injection,
    not the CLI envelope, transactions, or their persisted observations.
    """

    def invoke(self, fixture, *, lose_response=False):
        from tests.m14_test_support import run_taskgov_internal
        envelopes = []

        def captured(command, raw, started):
            self.assertIsNone(raw)
            started()
            completed = run_taskgov_internal(*command[3:], "--db", str(fixture.db),
                                            maintenance_enabled=False)
            envelopes.append(json.loads(completed.stdout))
            return completed.returncode, b"" if lose_response else completed.stdout.encode("utf-8")

        output = io.StringIO()
        with mock.patch.object(preparation, "_capture", side_effect=captured) as capture, redirect_stdout(output):
            code = handoff.main(["prepare", "--repo", str(fixture.repo), "--directory", "reviews/g1",
                                 "target", fixture.task_id, "--kind", "git_snapshot"])
        capture.assert_called_once()
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1, result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["operation_status"], "unknown")
        self.assertNotEqual(result["source_exit_code"], 0)
        self.assertNotIn("review_requests", result["handoff"])
        self.assertFalse((fixture.repo / "reviews").exists())
        self.assertNotIn("private fault", output.getvalue())
        if not lose_response:
            self.assertEqual(result["errors"], envelopes[0]["errors"])
            self.assertEqual(result["warnings"][:-1], envelopes[0]["warnings"])
            self.assertEqual(result["warnings"][-1]["code"], "handoff_outcome_unknown")
            self.assertEqual(result["errors"][0]["code"], "runner_state_invalid")
        return result

    def test_pre_t1_post_t1_cleanup_and_lost_response_match_persisted_state(self):
        from tests.test_m242_runner_service import RunnerServiceFixture, passing_process_result, row_counts
        from task_governance_tool import verification_runner_service as service

        for case in ("pre_t1", "post_t1", "cleanup", "lost_response"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                (fixture.repo / ".gitignore").write_text("/reviews/\n", encoding="utf-8")
                prepared = fixture.prepared()
                with ExitStack() as stack:
                    stack.enter_context(mock.patch.object(service, "_prepare_runner", return_value=prepared))
                    if case == "pre_t1":
                        fault = stack.enter_context(mock.patch.object(service, "_revalidate_prepared_runner",
                                                                      side_effect=service._state_invalid()))
                    elif case == "cleanup":
                        for name, options in (
                            ("materialize_runner_target", {"return_value": None}),
                            ("_basis_is_current", {"return_value": True}),
                            ("observe_fixed_package_runtime", {"return_value": Path(sys.executable).resolve()}),
                            ("run_process_request", {"side_effect": passing_process_result}),
                            ("cleanup_attempt_tree", {"side_effect": RuntimeError("private fault")}),
                        ):
                            fault = stack.enter_context(mock.patch.object(service, name, **options))
                    else:
                        fault = stack.enter_context(mock.patch.object(service, "_run_intent_under_lock",
                                                                      side_effect=RuntimeError("private fault")))
                    self.invoke(fixture, lose_response=case == "lost_response")
                    fault.assert_called_once()
                generation = 0 if case == "pre_t1" else 1
                self.assertEqual(fixture.authority().task["review_target_generation"], generation)
                self.assertEqual(row_counts(fixture.db)["verification_runner_attempts"], generation)
                if generation:
                    self.assertEqual(fixture.generation(1)["state"], "pending")
                if case == "post_t1":
                    # A separately requested target call may persist cleanup,
                    # then refuse without advancing a generation or launching.
                    # This explicit test call is not a helper retry.
                    with mock.patch.object(service, "_prepare_runner", return_value=fixture.prepared()), \
                         mock.patch.object(service, "_run_intent_under_lock") as launch:
                        self.invoke(fixture)
                    launch.assert_not_called()
                    self.assertEqual(fixture.authority().task["review_target_generation"], 1)
                    self.assertEqual(fixture.generation(1)["state"], "restart_cleaned")


class ReviewerDisplayTests(PreparationFixture):
    def test_first_read_refreshes_verification_without_transcription_or_extra_lookup(self):
        from task_governance_tool.review_verification import validate_review_verification
        task = self.task(verification="Declared expectation, not proof of execution")
        self.prepare(task)  # Receipt-required, so capture the ordinary public Packet.
        packet = self.cli("review", "prepare", task, "--read-only")
        self.assertEqual(packet["verification_evidence"]["source_kind"], "unavailable")
        directory = self.root / "reviews" / "verification"
        directory.mkdir(parents=True)
        packet_path = "reviews/verification/packet.json"
        self.cli("verification", "receipt", "add", task, "--result", "pass", "--duration-ms", "42",
                 "--scope-coverage", "full", "--expected-target-generation", "1")
        for legacy in (False, True):
            saved = copy.deepcopy(packet)
            if legacy:
                del saved["verification_evidence"]
            path = self.root / packet_path
            path.write_bytes(encode(saved))
            original = path.read_bytes()
            with mock.patch.object(preparation, "__file__", str(self.install.skill_root /
                    "scripts/task_governance_tool/review_handoff_preparation.py")), \
                 mock.patch.object(preparation, "_capture", wraps=preparation._capture) as capture:
                view = preparation.read_for_reviewer(self.root, packet_path)
            capture.assert_called_once()
            self.assertEqual(path.read_bytes(), original)
            summary = view["verification_evidence"]
            validate_review_verification(summary)
            self.assertEqual(summary["source_kind"], "caller_attestation")
            self.assertTrue(summary["gate"]["satisfied"])
            self.assertEqual(summary["current_receipt"]["duration_ms"], 42)
            self.assertIn("not an execution result", view["verification_guidance"])
            self.assertNotIn("recent_receipts", summary)
            self.assertNotIn("review_evidence", view)
            for mutation in (lambda x: x.update(raw_output="not permitted"),
                             lambda x: x.update(source_kind="other_reviewer_pass"),
                             lambda x: x["current_receipt"].update(duration_ms=True),
                             lambda x: x["gate"].update(qualifying_receipt_id=None, satisfied=False)):
                invalid = copy.deepcopy(view)
                mutation(invalid["verification_evidence"])
                with self.assertRaises((ValueError, TypeError)):
                    validate_review_verification(invalid["verification_evidence"])
                malformed_packet = copy.deepcopy(packet)
                malformed_packet["verification_evidence"] = invalid["verification_evidence"]
                with self.assertRaises(handoff.HandoffError):
                    handoff._packet(encode(malformed_packet))

    def read(self, packet, *options):
        return self.invoke("read", "--repo", str(self.root), "--packet", packet, *options)

    def test_authority_reference_and_legacy_packet_round_trip_without_reconstruction(self):
        cases = (("", False), ("docs/absent.md#根拠@abc123", False),
                 ("conversation:example:approved", False), ("external-id:review-42", True))
        for index, (reference, legacy) in enumerate(cases):
            with self.subTest(reference=reference, legacy=legacy):
                task = self.cli("task", "add", "--title", "Reference transport", "--review-tier", "2",
                    "--status", "in_progress", "--verification-not-required", "Isolated context fixture",
                    "--contract-scope", "Copy reference", "--contract-acceptance", "Preserve raw value",
                    "--contract-authority-ref", reference)["task"]["task_id"]
                self.cli("task", "edit", task, "--status", "review_pending")
                completed, prepared = self.prepare(task, directory=f"reviews/reference-{index}")
                self.assertEqual(completed.returncode, 0, completed.stdout)
                context = prepared["handoff"]
                path = self.root / context["packet_path"]
                packet = json.loads(path.read_bytes())
                self.assertEqual(packet["contract"]["authority_ref"], reference)
                old_shape = copy.deepcopy(packet)
                del old_shape["contract"]["authority_ref"]
                added_bytes = len(encode(packet)) - len(encode(old_shape))
                self.assertEqual(added_bytes, len(encode({"authority_ref": reference})) - 1)
                if legacy:
                    packet = old_shape
                    path.write_bytes(encode(packet))
                original_packet = path.read_bytes()
                with mock.patch.object(preparation, "__file__", str(self.install.skill_root /
                        "scripts/task_governance_tool/review_handoff_preparation.py")), \
                     mock.patch.object(preparation, "_capture", wraps=preparation._capture) as source:
                    view = preparation.read_for_reviewer(self.root, context["packet_path"])
                source.assert_called_once()  # No extra lookup for the reference.
                self.assertEqual(view["contract"], packet["contract"])
                self.assertEqual(view["result_template"], packet["result_template"])
                original = copy.deepcopy(view["result_template"])
                original["receipts"] = [receipt(f"reference-reviewer-{index}")]
                raw = b"\n " + encode(original) + b"\n"
                output = context["review_requests"][0]["result_path"]
                saved = self.invoke("save", "--repo", str(self.root), "--packet", context["packet_path"],
                                    "--output", output, raw=raw, reviewer=index)
                self.assertEqual(saved.returncode, 0, saved.stdout)
                submitted = self.invoke("submit", "--repo", str(self.root), "--packet", context["packet_path"], output)
                self.assertEqual(submitted.returncode, 0, submitted.stdout)
                self.assertEqual(path.read_bytes(), original_packet)
                self.assertEqual((self.root / output).read_bytes(), raw)
                print(f"AUTHORITY_REF legacy={legacy} added_packet_bytes={added_bytes}; "
                      "reference_only_lookup=unneeded_when_present; read_source_calls=1; tokens=unmeasured")
                # Optional compatibility must not hide a mismatching supplied
                # reference, nor any of the old Contract or target fields.
                for change in (lambda p: p["contract"].update(authority_ref="different-ref"),
                               lambda p: p["contract"].update(scope="Different scope"),
                               lambda p: p["contract"].update(revision=2),
                               lambda p: p["review_target"].update(generation=2)):
                    broken = copy.deepcopy(packet)
                    change(broken)
                    # Keep the template structurally consistent: this checks
                    # live comparison, not only the pure shape validator.
                    from task_governance_tool.review_results import review_result_template
                    broken["result_template"] = review_result_template(task, broken["contract"]["revision"], broken["review_target"])
                    path.write_bytes(encode(broken))
                    failed = self.read(context["packet_path"])
                    self.assertNotEqual(failed.returncode, 0, failed.stdout)
                    self.assertEqual(json.loads(failed.stdout)["code"], "review_packet_stale")
                path.write_bytes(original_packet)
                self.cli("task", "edit", task, "--status", "ready")

    def test_complete_packet_display_and_original_registration_conserve_meaning(self):
        from task_governance_tool.review_results import normalize_review_results
        task = self.task()
        _, prepared = self.prepare(task)
        packet_path = prepared["handoff"]["packet_path"]
        original_packet = (self.root / packet_path).read_bytes()
        packet = json.loads(original_packet)
        before = file_snapshot(self.root)
        result = self.read(packet_path)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(file_snapshot(self.root), before)
        view = json.loads(result.stdout)
        self.assertEqual(result.stdout, encode(view) + b"\n")
        self.assertIn("日本語".encode("utf-8"), result.stdout)
        self.assertEqual(set(view), (set(packet) - {"receipt_command"}) |
                         {"review_material", "context_check", "warnings", "verification_guidance"})
        for key in packet.keys() - {"result_instructions", "receipt_command", "verification_evidence"}:
            self.assertEqual(view[key], packet[key], key)
        self.assertEqual(view["context_check"], "matched_at_read")
        self.assertEqual(view["verification_evidence"]["source_kind"], "not_required")
        self.assertFalse(view["verification_evidence"]["gate"]["required"])
        self.assertIsNone(view["verification_evidence"]["current_receipt"])
        self.assertEqual(view["result_instructions"], review_result_instructions(independent=True))
        self.assertEqual(view["review_material"]["status"], "requires_supplied_material")
        self.assertEqual(view["result_template"]["receipts"], packet["result_template"]["receipts"])
        self.assertIsNone(view["result_template"]["receipts"][0]["kind"])
        with self.assertRaises(handoff.ReviewEvidenceError):
            normalize_review_results(view["result_template"], review_tier=2)
        original = copy.deepcopy(view["result_template"])
        original["receipts"] = [receipt("independent-view", verdict="changes_requested", findings=[
            {"severity": "medium", "summary": "example.py:1 Fix the boundary"},
            {"severity": "low", "summary": "example.py:2 Preserve this too"}])]
        raw = b"\n " + encode(original) + b"\n"
        old_original = copy.deepcopy(packet["result_template"])
        old_original["receipts"] = copy.deepcopy(original["receipts"])
        self.assertEqual(raw, b"\n " + encode(old_original) + b"\n")
        saved = self.invoke("save", "--repo", str(self.root), "--packet", packet_path,
                            "--output", "reviews/g1/review-1.json", raw=raw, reviewer=0)
        self.assertEqual(saved.returncode, 0, saved.stdout)
        submitted = self.invoke("submit", "--repo", str(self.root), "--packet", packet_path,
                                "reviews/g1/review-1.json")
        self.assertEqual(submitted.returncode, 0, submitted.stdout)
        registered = json.loads(submitted.stdout)["data"]["receipts"][0]
        self.assertEqual(registered["receipt"]["verdict"], "changes_requested")
        self.assertEqual([(f["finding"]["severity"], f["finding"]["summary"]) for f in registered["findings"]],
                         [(f["severity"], f["summary"]) for f in original["receipts"][0]["findings"]])
        self.assertEqual((self.root / "reviews/g1/review-1.json").read_bytes(), raw)
        self.assertEqual((self.root / packet_path).read_bytes(), original_packet)
        self.assertEqual(sorted(p.name for p in (self.root / "reviews/g1").iterdir()),
                         ["packet.json", "review-1.json", "review-1.json.session.json"])
        print(f"REVIEWER_VIEW packet_bytes={len(encode(packet))}->{len(encode(view))}; "
              "outer_read/save/submit_calls=1/1/1->1/1/1; "
              "display_helper_calls=0->1; registration_cli_calls=1->1; tokens=unmeasured")

    def test_removed_role_incomplete_packet_and_output_loss_never_yield_success(self):
        _, prepared = self.prepare(self.task())
        relative = prepared["handoff"]["packet_path"]
        path = self.root / relative
        raw = path.read_bytes()
        help_result = self.invoke("read", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stdout)
        self.assertNotIn(b"--role", help_result.stdout)
        for required in (b"--repo", b"--packet"):
            self.assertIn(required, help_result.stdout)
        before = file_snapshot(self.root)
        for options in (("--role", "independent"), ("--role", "unknown"), ("--role", "self_review_fallback")):
            failed = self.read(relative, *options)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(json.loads(failed.stdout)["ok"])
        self.assertEqual(file_snapshot(self.root), before)
        invalid = [b"{", raw[:-1], raw + b" " * handoff.PACKET_LIMIT]
        for change in (lambda p: p["contract"].pop("scope"),
                       lambda p: p["result_template"]["review_target"].update(generation=8),
                       lambda p: p["task"].update(review_tier=None),
                       lambda p: p.update(required_output=[])):
            packet = json.loads(raw)
            change(packet)
            invalid.append(encode(packet))
        for broken in invalid:
            path.write_bytes(broken)
            failed = self.read(relative)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(json.loads(failed.stdout)["ok"])
            self.assertNotIn("result_template", json.loads(failed.stdout))
        path.write_bytes(raw)
        bounded = json.loads(raw)
        bounded.update(changed_paths_available=True, changed_paths=["first.py"],
                       changed_paths_total=2, changed_paths_truncated=True)
        path.write_bytes(encode(bounded))
        view = self.read(relative)
        self.assertNotEqual(view.returncode, 0, view.stdout)
        self.assertEqual(json.loads(view.stdout)["code"], "review_packet_stale")
        path.write_bytes(raw)
        with mock.patch.object(handoff, "_emit", return_value=False), \
             mock.patch.object(handoff, "_write_new") as write, \
             mock.patch.object(preparation, "_capture", wraps=preparation._capture) as source:
            code = handoff.main(["read", "--repo", str(self.root), "--packet", relative])
        self.assertEqual(code, 1)
        write.assert_not_called()
        source.assert_called_once()
        self.assertEqual(source.call_args.args[0][3:5], ["review", "prepare"])
        self.assertEqual(path.read_bytes(), raw)


class ReviewerMaterialTests(PreparationFixture):
    def git(self, *args):
        from task_governance_tool.completion import safe_git_command, safe_git_environment
        result = subprocess.run([*safe_git_command(self.root), *args], capture_output=True,
                                env=safe_git_environment(), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def committed_fixture(self):
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.root / "source.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("Read SPEC.md.\n", encoding="utf-8")
        (self.root / "SPEC.md").write_text("The value must be 2.\n", encoding="utf-8")
        self.git("add", "--", ".gitignore", "source.py", "AGENTS.md", "SPEC.md")
        self.git("commit", "--quiet", "-m", "Fixture basis")
        return self.git("rev-parse", "HEAD").decode().strip()

    def displayed(self, packet, *, details=False):
        options = ["--material-details"] if details else []
        return self.invoke("read", "--repo", str(self.root), "--packet", packet, *options)

    def material_read(self, command, *, environment=None, posix=False, raw=None):
        if os.name == "nt" and not posix:
            import base64
            arguments = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                         base64.b64encode(command.encode("utf-16le")).decode("ascii")]
        elif os.name == "nt":
            # Portable POSIX argv preservation; native /bin/sh runs on POSIX.
            arguments = shlex.split(command)
        else:
            arguments = ["/bin/sh", "-c", command]
        return subprocess.run(arguments, input=raw, capture_output=True, env=environment, check=False)

    def test_complete_matching_authority_is_reused_without_detail_lookup_before_save(self):
        base = self.committed_fixture()
        # This already-held complete authority has an exact immutable source,
        # unlike an ambient file or inherited prose with no version binding.
        held_agents = self.git("show", base + ":AGENTS.md")
        (self.root / "source.py").write_bytes(b"value = 2\n")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        context = prepared["handoff"]
        packet = context["packet_path"]
        original_packet = (self.root / packet).read_bytes()
        before = file_snapshot(self.root)
        displayed = self.displayed(packet)
        self.assertEqual(displayed.returncode, 0, displayed.stdout)
        view = json.loads(displayed.stdout)
        material = view["review_material"]
        self.assertEqual(material["verification_workspace"]["status"], "optional")
        self.assertNotIn("prepare_command", material["verification_workspace"])
        self.assertNotIn("cleanup_command", material["verification_workspace"])
        self.assertEqual(material["dependency_revision"], base)
        inventory = {row["path"]: row for row in material["unchanged_inventory"]["entries"]}
        self.assertEqual(held_agents, self.git("cat-file", "blob", inventory["AGENTS.md"]["object_id"]))
        # Omit the matching complete body; retrieve the still-needed rule and
        # all changed sides/diffs through the unchanged normal collect command.
        collected = self.material_read(material["collect_command"].replace(
            "<selected dependency paths as JSON array>", '["SPEC.md"]'))
        self.assertEqual(collected.returncode, 0, collected.stderr)
        delivery = json.loads(collected.stdout)
        self.assertNotIn(inventory["AGENTS.md"]["object_id"], delivery["bodies"])
        spec, = delivery["dependencies"]
        self.assertEqual((spec["path"], spec["revision"]), ("SPEC.md", base))
        self.assertEqual(delivery["bodies"][spec["body_id"]]["text"], "The value must be 2.\n")
        changed, = delivery["changes"]
        for side, text in (("before", "value = 1\n"), ("after", "value = 2\n")):
            self.assertEqual(delivery["bodies"][changed[side]["body_id"]]["text"], text)
        self.assertIn("+value = 2", delivery["patches"][changed["diff"]["patch_id"]]["text"])
        self.assertEqual(file_snapshot(self.root), before)
        self.assertEqual(view["result_instructions"], review_result_instructions(independent=True))
        payload = copy.deepcopy(view["result_template"])
        payload["receipts"] = [receipt("normal-material-reuse")]
        raw = encode(payload)
        output = context["review_requests"][0]["result_path"]
        saved = self.invoke("save", "--repo", str(self.root), "--packet", packet,
                            "--output", output, raw=raw, reviewer=0)
        self.assertEqual(saved.returncode, 0, saved.stdout)
        submitted = self.invoke("submit", "--repo", str(self.root), "--packet", packet, output)
        self.assertEqual(submitted.returncode, 0, submitted.stdout)
        self.assertEqual(json.loads(submitted.stdout)["data"]["receipts"][0]["receipt"]["verdict"], "pass")
        self.assertEqual((self.root / packet).read_bytes(), original_packet)
        self.assertEqual((self.root / output).read_bytes(), raw)

    def test_conditional_material_details_recover_only_needed_content_and_revalidate(self):
        old = self.committed_fixture()
        old_spec = self.git("show", old + ":SPEC.md")
        (self.root / "SPEC.md").write_bytes(b"The value must now be 3.\n")
        (self.root / "later.py").write_bytes(b"dependency = True\n")
        self.git("add", "--", "SPEC.md", "later.py")
        self.git("commit", "--quiet", "-m", "Current rules and later dependency")
        current = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "source.py").write_bytes(b"value = 3\n")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        packet = prepared["handoff"]["packet_path"]
        before = file_snapshot(self.root)
        normal_result = self.displayed(packet)
        self.assertEqual(normal_result.returncode, 0, normal_result.stdout)
        normal = json.loads(normal_result.stdout)
        material = normal["review_material"]
        self.assertNotEqual(material["dependency_revision"], old)
        self.assertEqual(material["dependency_revision"], current)
        detailed_result = self.material_read(material["recovery_command"])
        self.assertEqual(detailed_result.returncode, 0, detailed_result.stderr)
        detailed = json.loads(detailed_result.stdout)
        details = detailed["review_material"]
        self.assertGreaterEqual(detailed["verification_evidence"].pop("observed_at"),
                                normal["verification_evidence"].pop("observed_at"))
        self.assertEqual({k: v for k, v in normal.items() if k != "review_material"},
                         {k: v for k, v in detailed.items() if k != "review_material"})
        for name in ("changes", "comparison_base", "dependency_revision", "unchanged_inventory", "collect_command"):
            self.assertEqual(material[name], details[name])
        for name in ("blob_command", "blob_batch_command", "diff_command", "dependency_command", "directory_command"):
            self.assertNotIn(name, material)
            self.assertIn(name, details)
        self.assertLess(len(normal_result.stdout), len(detailed_result.stdout))
        for condition in ("immutable object identity", "incomplete/unknown", "mismatched", "tool-truncated",
                          "Project reread obligations", "before/after sides", "independently"):
            self.assertIn(condition, " ".join(material["instructions"]))
        # Wrong-version, unknown, missing or truncated prior text cannot replace
        # the selected immutable body. Recovery returns it without successful siblings.
        for path, expected in (("SPEC.md", b"The value must now be 3.\n"),
                               ("later.py", b"dependency = True\n")):
            recovered = self.material_read(details["dependency_command"].replace("<project-relative-path>", path))
            self.assertEqual((recovered.returncode, recovered.stdout), (0, expected), recovered.stderr)
            self.assertNotEqual(recovered.stdout, old_spec)
            self.assertNotIn(b"value = 3", recovered.stdout)
        missing = self.material_read(details["dependency_command"].replace("<project-relative-path>", "absent.py"))
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(missing.stdout, b"")
        self.assertEqual(file_snapshot(self.root), before)
        (self.root / "source.py").write_bytes(b"value = 4\n")
        self.git("add", "--", "source.py")
        stale = self.material_read(material["recovery_command"])
        self.assertNotEqual(stale.returncode, 0)
        self.assertEqual(json.loads(stale.stdout)["code"], "review_target_mismatch")

    def test_inventory_discovers_unchanged_tests_and_batches_selected_exact_blobs(self):
        self.committed_fixture()
        sources = {"checks/behavior.py": "# 日本語\r\nfrom source import value\r\nassert value == 2\r\n".encode("utf-8"),
                   "support/values.py": b"expected = 2"}
        for path, raw in sources.items():
            (self.root / path).parent.mkdir(exist_ok=True)
            (self.root / path).write_bytes(raw)
        self.git("-c", "core.autocrlf=false", "add", "--", *sources)
        self.git("commit", "--quiet", "-m", "Unchanged review dependencies")
        base = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "source.py").write_bytes(b"value = 2\n")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        (self.root / "checks/behavior.py").write_bytes(b"unreviewed ambient replacement\n")
        material = json.loads(self.displayed(prepared["handoff"]["packet_path"], details=True).stdout)["review_material"]
        inventory = material["unchanged_inventory"]
        self.assertEqual(set(inventory), {"entries", "total", "returned", "truncated"})
        self.assertEqual(material["dependency_revision"], base)
        self.assertFalse(inventory["truncated"])
        self.assertEqual(inventory["total"], inventory["returned"])
        listed = {row["path"]: row for row in inventory["entries"]}
        self.assertNotIn("source.py", listed)
        selected = [material["changes"][0]["after_object_id"],
                    listed["checks/behavior.py"]["object_id"], listed["support/values.py"]["object_id"]]
        before = file_snapshot(self.root)
        actual = self.material_read(material["blob_batch_command"], raw=("\n".join(selected) + "\n").encode())
        expected = b"".join(oid.encode() + b" blob " + str(len(body)).encode() + b"\n" + body + b"\n"
                            for oid, body in zip(selected, [b"value = 2\n", *sources.values()]))
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(actual.stdout, expected)
        self.assertEqual(file_snapshot(self.root), before)

    def test_collect_supplies_exact_deduplicated_bodies_and_patch_without_extra_parent_input(self):
        self.committed_fixture()
        text = "# 日本語\r\nvalue = 2"
        paths = ["O‘Brien; $x & 日本語.md", "same.md"]
        for path in paths:
            (self.root / path).write_bytes(text.encode())
        self.git("-c", "core.autocrlf=false", "add", "--", *paths)
        self.git("commit", "--quiet", "-m", "Known dependencies")
        base = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "source.py").write_bytes(text.encode())
        self.git("-c", "core.autocrlf=false", "add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        packet = prepared["handoff"]["packet_path"]
        view = json.loads(self.displayed(packet).stdout)
        self.assertNotIn(text, json.dumps(view, ensure_ascii=False))
        material = view["review_material"]
        selected = [*paths, "source.py", "AGENTS.md", paths[0]]
        command = material["collect_command"].replace(
            "<selected dependency paths as JSON array>", json.dumps(selected, ensure_ascii=False))
        (self.root / "source.py").write_text("unreviewed ambient value", encoding="utf-8")
        before = file_snapshot(self.root)
        actual = self.material_read(command, environment=dict(os.environ, GIT_DIR="absent.git", GIT_NO_LAZY_FETCH="0"))
        self.assertEqual(actual.returncode, 0, actual.stderr)
        collected = json.loads(actual.stdout)
        self.assertEqual(collected["status"], "complete")
        self.assert_lossless_collection(collected, material["changes"])
        self.assertEqual(collected["review_target"], view["review_target"])
        self.assertEqual(len(collected["dependencies"]), 4)
        change, = collected["changes"]
        oid = change["after"]["object_id"]
        self.assertEqual(collected["bodies"][oid], {"status": "provided", "text": text, "byte_count": len(text.encode())})
        self.assertEqual(len(collected["bodies"]), 3)
        for dep in collected["dependencies"][:3]:
            self.assertEqual(dep["body_id"], oid)
            self.assertEqual(dep["side"], "dependency")
        self.assertEqual(collected["dependencies"][0]["revision"], base)
        self.assertEqual(collected["dependencies"][2]["revision"], view["review_target"]["value"])
        self.assertEqual(change["before"]["revision"], base)
        self.assertEqual(change["after"]["side"], "after")
        self.assertEqual(collected["bodies"][change["before"]["object_id"]]["text"].encode(),
                         self.git("cat-file", "blob", change["before"]["object_id"]))
        self.assertEqual(collected["patches"][change["diff"]["patch_id"]]["text"].encode(),
                         self.git("diff", "--no-ext-diff", "--no-textconv", change["before"]["object_id"], oid, "--"))
        self.assertEqual(file_snapshot(self.root), before)

    def assert_lossless_collection(self, collected, manifest):
        """Compare with the prior presentation, not another product consumer."""
        old = copy.deepcopy(collected)
        del old["patches"]
        for row, old_row, entry in zip(collected["changes"], old["changes"], manifest, strict=True):
            self.assertEqual(set(row), {"ordinal", "kind", "before", "after", "diff"})
            self.assertEqual((row["ordinal"], row["kind"]), (entry["ordinal"], entry["kind"]))
            for side, path_key in (("before", "old_path"), ("after", "new_path")):
                source = row[side]
                if entry[path_key] is None:
                    self.assertIsNone(source)
                    continue
                self.assertEqual(set(source), {"path", "revision", "revision_kind", "side",
                                               "mode", "object_id", "body_id", "status"})
                self.assertEqual((source["path"], source["mode"], source["object_id"], source["side"]),
                                 (entry[path_key], entry[side + "_mode"], entry[side + "_object_id"], side))
            old_row.update(entry)
            if row["diff"].get("patch_id"):
                patch = collected["patches"][row["diff"]["patch_id"]]
                expected = self.git("diff", "--no-ext-diff", "--no-textconv",
                                    row["before"]["object_id"], row["after"]["object_id"], "--")
                self.assertEqual(patch, {"status": "provided", "byte_count": len(expected),
                                         "text": expected.decode("utf-8")})
                old_row["diff"] = {"format": "git_patch", **patch}
        # Compare structural UTF-8 delivery bytes with identical guidance. This
        # is not a tokenizer measurement or a general performance assertion.
        sizes = tuple(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
                      for value in (old, collected))
        self.assertLess(sizes[1], sizes[0])
        return sizes

    def test_collect_shares_only_complete_identical_patches_with_all_source_mappings(self):
        from task_governance_tool import review_material_collection as collection
        self.committed_fixture()
        initial = {"a.txt": b"same old\r\n", "b.txt": b"same old\r\n",
                   "c.txt": b"different old\n", "d.txt": b"mode one\n", "e.txt": b"mode two\n"}
        for path, raw in initial.items():
            (self.root / path).write_bytes(raw)
        self.git("-c", "core.autocrlf=false", "add", "--", *initial)
        self.git("commit", "--quiet", "-m", "Duplicate and distinct patch sources")
        base = self.git("rev-parse", "HEAD").decode().strip()
        for path in ("a.txt", "b.txt", "c.txt"):
            (self.root / path).write_bytes(b"same new\r\nwithout final newline")
        self.git("-c", "core.autocrlf=false", "add", "--", "a.txt", "b.txt", "c.txt")
        self.git("update-index", "--chmod=+x", "d.txt", "e.txt")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        packet = prepared["handoff"]["packet_path"]
        view = json.loads(self.displayed(packet).stdout)
        material = view["review_material"]
        actual = self.material_read(material["collect_command"].replace(
            "<selected dependency paths as JSON array>", '["a.txt","b.txt","c.txt","AGENTS.md"]'))
        self.assertEqual(actual.returncode, 0, actual.stderr)
        collected = json.loads(actual.stdout)
        self.assert_lossless_collection(collected, material["changes"])
        rows = {row["after"]["path"]: row for row in collected["changes"]}
        self.assertEqual(len(collected["patches"]), 3)
        self.assertEqual(rows["a.txt"]["diff"], rows["b.txt"]["diff"])
        self.assertNotEqual(rows["a.txt"]["diff"], rows["c.txt"]["diff"])
        self.assertEqual(rows["d.txt"]["diff"], rows["e.txt"]["diff"])
        self.assertEqual(collected["patches"][rows["d.txt"]["diff"]["patch_id"]]["text"], "")
        for row in rows.values():
            self.assertEqual((row["before"]["revision"], row["before"]["revision_kind"]), (base, "git_treeish"))
            self.assertEqual((row["after"]["revision"], row["after"]["revision_kind"]),
                             (view["review_target"]["value"], "git_snapshot"))
        self.assertEqual(len({row["body_id"] for row in collected["dependencies"][:3]}), 1)
        digest = hashlib.sha256((self.root / packet).read_bytes()).hexdigest()
        unique_bytes = sum(row["byte_count"] for table in ("bodies", "patches") for row in collected[table].values())
        # Exact capacity admits duplicate references without charging or reading
        # their patch twice. One byte less preserves explicit incomplete delivery.
        with mock.patch.object(collection, "TOTAL_BYTE_LIMIT", unique_bytes):
            bounded = collection.collect_material(self.root, packet, digest, io.BytesIO(b'["a.txt","b.txt","c.txt","AGENTS.md"]'))
        self.assertEqual(bounded, collected)
        with mock.patch.object(collection, "TOTAL_BYTE_LIMIT", unique_bytes - 1):
            limited = collection.collect_material(self.root, packet, digest, io.BytesIO(b'["a.txt","b.txt","c.txt","AGENTS.md"]'))
        self.assertEqual(limited["status"], "incomplete")
        self.assertEqual(limited["dependencies"][-1]["status"], "delivery_limit")

    def test_collect_modes_rename_deleted_paths_and_whole_file_diffs(self):
        base = self.committed_fixture()
        blob = self.git("rev-parse", base + ":SPEC.md").decode().strip()
        self.git("mv", "--", "source.py", "renamed.py")
        self.git("rm", "--", "SPEC.md")
        self.git("update-index", "--chmod=+x", "AGENTS.md")
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},link-text")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{base},submodule")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        material = json.loads(self.displayed(prepared["handoff"]["packet_path"]).stdout)["review_material"]
        command = material["collect_command"].replace("<selected dependency paths as JSON array>",
                                                     '["source.py","SPEC.md","renamed.py","link-text"]')
        actual = self.material_read(command)
        collected = json.loads(actual.stdout)
        self.assertEqual(collected["status"], "incomplete")
        self.assert_lossless_collection(collected, material["changes"])
        deps = {row["path"]: row for row in collected["dependencies"]}
        self.assertEqual(deps["source.py"]["status"], "absent_at_target")
        self.assertEqual(deps["SPEC.md"]["status"], "absent_at_target")
        self.assertEqual(deps["renamed.py"]["status"], "provided")
        self.assertEqual(deps["link-text"]["mode"], "120000")
        self.assertEqual(collected["bodies"][blob]["text"], "The value must be 2.\n")
        changes = collected["changes"]
        rename = next(row for row in changes if row["kind"] == "rename" and row["after"]["path"] == "renamed.py")
        self.assertEqual((rename["before"]["path"], rename["after"]["path"]), ("source.py", "renamed.py"))
        self.assertEqual(rename["before"]["body_id"], rename["after"]["body_id"])
        submodule = next(row for row in changes if row["after"] and row["after"]["path"] == "submodule")
        self.assertEqual(submodule["after"]["status"], "submodule_unavailable")
        self.assertEqual(submodule["diff"]["status"], "source_unavailable")

    def test_collect_large_binary_missing_and_later_dependency_recovery(self):
        self.committed_fixture()
        from task_governance_tool import review_material_collection as collection
        contents = {"large.md": b"x" * (collection.BODY_BYTE_LIMIT + 1),
                    "binary.bin": b"\xff\0", "missing.md": b"missing", "later.md": b"later dependency"}
        for path, raw in contents.items():
            (self.root / path).write_bytes(raw)
        self.git("add", "--", *contents)
        self.git("commit", "--quiet", "-m", "Recovery fixtures")
        base = self.git("rev-parse", "HEAD").decode().strip()
        missing = self.git("rev-parse", base + ":missing.md").decode().strip()
        (self.root / "source.py").write_bytes(b"value = 2\n")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        packet = prepared["handoff"]["packet_path"]
        material = json.loads(self.displayed(packet, details=True).stdout)["review_material"]
        object_path = self.root / ".git" / "objects" / missing[:2] / missing[2:]
        object_path.chmod(stat.S_IWRITE | stat.S_IREAD)
        object_path.unlink()
        digest = hashlib.sha256((self.root / packet).read_bytes()).hexdigest()
        result = self.invoke("material", "--repo", str(self.root), "collect", "--packet=" + packet,
                             "--packet-sha256=" + digest, raw=b'["large.md","binary.bin","missing.md"]')
        self.assertNotEqual(result.returncode, 0)
        collected = json.loads(result.stdout)
        self.assertEqual(collected["status"], "incomplete")
        self.assertEqual([row["status"] for row in collected["dependencies"]], ["too_large", "non_text", "unavailable"])
        self.assertTrue(all("text" not in collected["bodies"][row["object_id"]] for row in collected["dependencies"]))
        self.assertEqual(collected["changes"][0]["after"]["status"], "provided")
        for path in ("large.md", "later.md"):
            recovered = self.material_read(material["dependency_command"].replace("<project-relative-path>", path))
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(recovered.stdout, contents[path])

    def test_collect_rejects_invalid_input_changed_packet_and_target_without_bodies(self):
        self.committed_fixture()
        (self.root / "source.py").write_bytes(b"value = 2\n")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        packet = prepared["handoff"]["packet_path"]
        raw_packet = (self.root / packet).read_bytes()
        arguments = ("material", "--repo", str(self.root), "collect", "--packet=" + packet,
                     "--packet-sha256=" + hashlib.sha256(raw_packet).hexdigest())
        snapshot = file_snapshot(self.root)
        for raw in (b"", b"{}", b'[1]', b'["../SPEC.md"]', b'["HEAD:SPEC.md"]', b'\xff', b'["SPEC.md",null]'):
            result = self.invoke(*arguments, raw=raw)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(file_snapshot(self.root), snapshot)
        (self.root / packet).write_bytes(raw_packet + b"\n")
        result = self.invoke(*arguments, raw=b"[]")
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"review_packet_stale", result.stderr)
        (self.root / packet).write_bytes(raw_packet)
        (self.root / "source.py").write_bytes(b"value = 3\n")
        self.git("add", "--", "source.py")
        result = self.invoke(*arguments, raw=b"[]")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"review_target_mismatch", result.stderr)

    def test_collect_commit_add_delete_and_aggregate_budget(self):
        from task_governance_tool import review_material_collection as collection
        base = self.committed_fixture()
        self.git("rm", "--", "source.py")
        (self.root / "added.py").write_bytes(b"new content without LF")
        self.git("add", "--", "added.py")
        self.git("commit", "--quiet", "-m", "Whole-file change")
        revision = self.git("rev-parse", "HEAD").decode().strip()
        _, prepared = self.prepare(self.task(), options=["--kind", "git_commit", "--revision", revision])
        packet = prepared["handoff"]["packet_path"]
        digest = hashlib.sha256((self.root / packet).read_bytes()).hexdigest()
        self.git("commit", "--quiet", "--allow-empty", "-m", "Later HEAD")
        result = collection.collect_material(self.root, packet, digest, io.BytesIO(b'["SPEC.md"]'))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["dependencies"][0]["revision"], revision)
        for entry in result["changes"]:
            self.assertEqual(entry["diff"]["format"], "whole_file")
            self.assertEqual(entry["diff"]["operation"], entry["kind"])
            side = entry["after"] or entry["before"]
            self.assertEqual(entry["diff"]["body_id"], side["object_id"])
            self.assertEqual(result["bodies"][side["object_id"]]["text"].encode(), self.git("cat-file", "blob", side["object_id"]))
        with mock.patch.object(collection, "TOTAL_BYTE_LIMIT", 25):
            bounded = collection.collect_material(self.root, packet, digest, io.BytesIO(b'["SPEC.md"]'))
        self.assertEqual(bounded["status"], "incomplete")
        self.assertEqual(bounded["dependencies"][0]["status"], "delivery_limit")
        self.assertLessEqual(sum(row.get("byte_count", 0) for row in bounded["bodies"].values()), 25)

    def test_directory_discovery_keeps_tree_coordinates_from_nested_project(self):
        from task_governance_tool.artifact_manifest import observe_staged_git_manifest

        self.committed_fixture()
        for path in ("checks/behavior.py", "nested/local.py"):
            (self.root / path).parent.mkdir()
            (self.root / path).write_bytes(b"original = True\n")
        self.git("add", "--", "checks", "nested")
        self.git("commit", "--quiet", "-m", "Nested project and sibling checks")
        revision = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "source.py").write_bytes(b"value = 2\n")
        self.git("add", "--", "source.py")
        snapshot = observe_staged_git_manifest(self.root / "nested")
        targets = [
            {"kind": "git_commit", "value": revision, "base_revision": ""},
            {"kind": "git_snapshot", "value": snapshot.target_value,
             "base_revision": snapshot.target_base_revision},
        ]
        # Neither the caller's nested cwd nor ambient dependency edits select material.
        (self.root / "checks/behavior.py").write_bytes(b"ambient = False\n")
        before = file_snapshot(self.root)
        for target in targets:
            material = preparation._review_material(self.root / "nested", target)
            self.assertEqual(material["dependency_revision"], revision)
            for directory in ("", "checks"):
                with self.subTest(kind=target["kind"], directory=directory):
                    command = material["directory_command"].replace("<project-relative-directory>", directory)
                    actual = self.material_read(command)
                    expected = self.git("ls-tree", "--full-tree", "--no-abbrev", revision + ":" + directory, "--")
                    self.assertTrue(expected)
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    self.assertEqual(actual.stdout, expected)
        self.assertEqual(file_snapshot(self.root), before)

    def test_bounded_inventory_can_discover_omitted_new_dependency_at_fixed_revision(self):
        self.committed_fixture()
        (self.root / "later").mkdir()
        for index in range(135):
            (self.root / f"aaa-{index:03}.py").write_text("value = 0\n", encoding="utf-8")
        (self.root / "later/needed.py").write_text("required = True\n", encoding="utf-8")
        self.git("add", "--", "later", *(f"aaa-{index:03}.py" for index in range(135)))
        self.git("commit", "--quiet", "-m", "Larger dependency tree")
        (self.root / "source.py").write_text("from later.needed import required\n", encoding="utf-8")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        material = json.loads(self.displayed(prepared["handoff"]["packet_path"], details=True).stdout)["review_material"]
        inventory = material["unchanged_inventory"]
        self.assertTrue(inventory["truncated"])
        self.assertLess(inventory["returned"], inventory["total"])
        self.assertLessEqual(inventory["returned"], preparation.INVENTORY_ENTRY_LIMIT)
        self.assertLessEqual(len(encode(inventory["entries"])), preparation.INVENTORY_BYTE_LIMIT)
        self.assertNotIn("later/needed.py", [row["path"] for row in inventory["entries"]])
        collected = self.material_read(material["collect_command"].replace(
            "<selected dependency paths as JSON array>", '["later/needed.py"]'))
        self.assertEqual(collected.returncode, 0, collected.stderr)
        delivery = json.loads(collected.stdout)
        dependency, = delivery["dependencies"]
        self.assertEqual(delivery["bodies"][dependency["body_id"]]["text"], "required = True\n")
        actual = self.material_read(material["directory_command"].replace("<project-relative-directory>", "later"))
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertIn(b"needed.py", actual.stdout)
        expected_id = self.git("rev-parse", material["dependency_revision"] + ":later/needed.py").strip()
        self.assertIn(expected_id, actual.stdout)
        actual = self.material_read(material["blob_command"].replace("<object_id>", expected_id.decode()))
        self.assertEqual(actual.stdout.replace(b"\r\n", b"\n"), b"required = True\n")
        # Byte bounds are independent of count bounds; no false complete prefix.
        with mock.patch.object(preparation, "INVENTORY_BYTE_LIMIT", 2):
            bounded = preparation._dependency_inventory(self.root, material["dependency_revision"], material["changes"])
        self.assertEqual(bounded["entries"], [])
        self.assertEqual(bounded["total"], inventory["total"])
        self.assertTrue(bounded["truncated"])

    def test_inventory_modes_deletions_and_rename_do_not_restore_old_material(self):
        base = self.committed_fixture()
        blob = self.git("rev-parse", base + ":SPEC.md").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},link-text")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{base},submodule")
        self.git("commit", "--quiet", "-m", "Special-mode fixture")
        self.git("mv", "--", "source.py", "renamed.py")
        self.git("rm", "--", "SPEC.md")
        self.git("update-index", "--chmod=+x", "AGENTS.md")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        material = json.loads(self.displayed(prepared["handoff"]["packet_path"]).stdout)["review_material"]
        rows = {row["path"]: row for row in material["unchanged_inventory"]["entries"]}
        self.assertEqual(rows["link-text"]["mode"], "120000")
        self.assertEqual(rows["submodule"]["mode"], "160000")
        self.assertTrue(set(rows).isdisjoint({"source.py", "renamed.py", "SPEC.md", "AGENTS.md"}))
        self.assertEqual({entry["kind"] for entry in material["changes"]}, {"delete", "rename", "modify"})

    def test_snapshot_material_uses_immutable_objects_and_excludes_ambient_files(self):
        base = self.committed_fixture()
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        packet = prepared["handoff"]["packet_path"]
        (self.root / "source.py").write_text("value = 999\n", encoding="utf-8")
        (self.root / "untracked.py").write_text("not reviewed\n", encoding="utf-8")
        before = file_snapshot(self.root)
        result = self.displayed(packet, details=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(file_snapshot(self.root), before)
        material = json.loads(result.stdout)["review_material"]
        self.assertEqual(material["status"], "git_objects_verified")
        self.assertEqual(material["dependency_revision"], base)
        self.assertEqual(len(material["changes"]), 1)
        entry = material["changes"][0]
        self.assertEqual(entry["new_path"], "source.py")
        self.assertEqual(self.git("cat-file", "blob", entry["after_object_id"]), b"value = 2\n")
        self.assertEqual(self.git("show", base + ":SPEC.md"), b"The value must be 2.\n")
        self.assertIn(b"+value = 2", self.git("diff", "--no-ext-diff", "--no-textconv",
                      entry["before_object_id"], entry["after_object_id"], "--"))
        # Execute the emitted object command with replacement refs present.
        self.git("replace", entry["after_object_id"], entry["before_object_id"])
        command = material["blob_command"].replace("<object_id>", entry["after_object_id"])
        actual = self.material_read(command)
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(actual.stdout.replace(b"\r\n", b"\n"), b"value = 2\n")
        # Subsequent mutations cannot silently become the old Packet's target.
        self.git("add", "--", "source.py")
        failed = self.displayed(packet)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(json.loads(failed.stdout)["code"], "review_target_mismatch")

    def test_material_commands_preserve_path_literals_and_sanitized_environment(self):
        self.committed_fixture()
        paths = ("docs/O'Brien; $x & 日本語.md", "docs/x'; echo unintended; '.md",
                 *(f"docs/O{quote}Brien.md" for quote in "‘’‚‛′＇"))
        (self.root / "docs").mkdir()
        for index, path in enumerate(paths):
            (self.root / path).write_text(f"dependency {index}\n", encoding="utf-8")
        self.git("add", "--", "docs")
        self.git("commit", "--quiet", "-m", "Quoted dependencies")
        revision = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        material = json.loads(self.displayed(prepared["handoff"]["packet_path"], details=True).stdout)["review_material"]
        environment = dict(os.environ, GIT_DIR=str(self.root / "absent.git"),
                           GIT_OBJECT_DIRECTORY=str(self.root / "absent-objects"),
                           GIT_NO_LAZY_FETCH="0", GIT_OPTIONAL_LOCKS="1",
                           GIT_NO_REPLACE_OBJECTS="0", GIT_TERMINAL_PROMPT="1")
        before = file_snapshot(self.root)
        for index, path in enumerate(paths):
            escaped = (path.translate({ord(quote): quote * 2 for quote in "'‘’‚‛"})
                       if os.name == "nt" else path.replace("'", "'\"'\"'"))
            command = material["dependency_command"].replace("<project-relative-path>", escaped)
            actual = self.material_read(command, environment=environment)
            self.assertEqual(actual.returncode, 0, actual.stderr)
            self.assertEqual(actual.stdout.replace(b"\r\n", b"\n"), f"dependency {index}\n".encode())
            # Fully bound generated arguments use the same literal rule.
            actual = self.material_read(preparation._material_command(self.root, "dependency", revision, "--path=" + path),
                                        environment=environment)
            self.assertEqual(actual.returncode, 0, actual.stderr)
            self.assertEqual(actual.stdout.replace(b"\r\n", b"\n"), f"dependency {index}\n".encode())
            with mock.patch.object(preparation, "_shell", side_effect=shlex.join):
                template = preparation._material_command(self.root, "dependency", revision, "--path=<project-relative-path>")
            command = template.replace("<project-relative-path>", path.replace("'", "'\"'\"'"))
            actual = self.material_read(command, environment=environment, posix=True)
            self.assertEqual(actual.returncode, 0, actual.stderr)
            self.assertEqual(actual.stdout.replace(b"\r\n", b"\n"), f"dependency {index}\n".encode())
        entry = material["changes"][0]
        command = material["diff_command"].replace("<before_object_id>", entry["before_object_id"]).replace(
            "<after_object_id>", entry["after_object_id"])
        actual = self.material_read(command, environment=environment)
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertIn(b"+value = 2", actual.stdout)
        self.assertEqual(file_snapshot(self.root), before)

    def test_missing_unchanged_dependency_is_not_lazily_fetched(self):
        base = self.committed_fixture()
        missing = self.git("rev-parse", base + ":SPEC.md").decode().strip()
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        self.git("config", "remote.fixture.promisor", "true")
        # An unavailable local-only remote: this test never uses a network URL.
        self.git("config", "remote.fixture.url", str(self.root / "absent-local-remote"))
        object_path = self.root / ".git" / "objects" / missing[:2] / missing[2:]
        object_path.chmod(stat.S_IWRITE | stat.S_IREAD)
        object_path.unlink()
        result = self.displayed(prepared["handoff"]["packet_path"], details=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        material = json.loads(result.stdout)["review_material"]
        before = file_snapshot(self.root)
        actual = self.material_read(material["dependency_command"].replace("<project-relative-path>", "SPEC.md"),
                                    environment=dict(os.environ, GIT_NO_LAZY_FETCH="0"))
        self.assertNotEqual(actual.returncode, 0)
        self.assertEqual(actual.stdout, b"")
        self.assertNotIn(b"promisor", actual.stderr.lower())
        self.assertNotIn(b"remote", actual.stderr.lower())
        self.assertEqual(file_snapshot(self.root), before)
        # A batch exit zero is deliberately not a content-availability claim.
        available = material["changes"][0]["after_object_id"]
        body = b"value = 2\n"
        actual = self.material_read(material["blob_batch_command"],
                                    raw=(available + "\n" + missing + "\n").encode())
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(actual.stdout, available.encode() + b" blob " +
                         str(len(body)).encode() + b"\n" + body + b"\n" +
                         missing.encode() + b" missing\n")
        recovered = self.material_read(material["blob_command"].replace("<object_id>", available))
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(recovered.stdout, body)
        self.assertEqual(file_snapshot(self.root), before)

    def test_short_material_commands_match_previous_fixed_wrapper_bytes_and_failures(self):
        base = self.committed_fixture()
        before_id = self.git("rev-parse", base + ":source.py").decode().strip()
        (self.root / "source.py").write_bytes("# 日本語\r\nvalue = 2".encode("utf-8"))
        self.git("-c", "core.autocrlf=false", "add", "--", "source.py")
        after_id = self.git("rev-parse", ":source.py").decode().strip()
        # These overrides must not enable external diff/text conversion.
        self.git("config", "diff.external", "missing-external-command")
        self.git("config", "diff.default.textconv", "missing-textconv-command")
        program = (
            "import subprocess,sys;from pathlib import Path;"
            "sys.path.insert(0,sys.argv[1]);"
            "from task_governance_tool.completion import safe_git_command,safe_git_environment;"
            "sys.exit(subprocess.call([*safe_git_command(Path(sys.argv[2])),*sys.argv[3:]],"
            "env=safe_git_environment()))"
        )  # Previous fixed wrapper from the Task's input revision, test-only oracle.
        missing = "f" * 40
        cases = [
            (("blob", after_id), ["cat-file", "blob", after_id], None),
            (("blob", missing), ["cat-file", "blob", missing], None),
            (("diff", before_id, after_id), ["diff", "--no-ext-diff", "--no-textconv", before_id, after_id, "--"], None),
            (("dependency", base, "--path=source.py"), ["show", base + ":source.py"], None),
            (("dependency", base, "--path=absent.py"), ["show", base + ":absent.py"], None),
            (("directory", base, "--path="), ["-c", "core.quotePath=false", "ls-tree", "--full-tree", "--no-abbrev", base + ":", "--"], None),
            (("batch",), ["cat-file", "--batch"], (after_id + "\n" + missing + "\n" + base + "\n").encode()),
        ]
        snapshot = file_snapshot(self.root)
        for short, arguments, raw in cases:
            with self.subTest(operation=short):
                old_command = preparation._shell([sys.executable, "-I", "-S", "-B", "-c", program,
                    str(self.helper.parent), str(self.root), *arguments])
                new_command = preparation._material_command(self.root, *short)
                self.assertLess(len(new_command.encode("utf-8")), len(old_command.encode("utf-8")))
                self.assertNotIn("import ", new_command)
                old = self.material_read(old_command, raw=raw)
                new = self.material_read(new_command, raw=raw)
                self.assertEqual((new.returncode, new.stdout, new.stderr),
                                 (old.returncode, old.stdout, old.stderr))
        self.assertEqual(file_snapshot(self.root), snapshot)

    def test_material_rejects_ambient_selectors_unsafe_paths_and_arbitrary_commands(self):
        base = self.committed_fixture()
        oid = self.git("rev-parse", base + ":SPEC.md").decode().strip()
        invalid = [
            ("blob", "HEAD:SPEC.md"), ("blob", oid[:8]), ("blob", "--not-an-object"),
            ("diff", base, "HEAD"), ("dependency", "HEAD", "--path=SPEC.md"),
            ("checkout", base), ("blob", oid, "--output=unexpected"),
            *(("dependency", base, "--path=" + path) for path in
              ("", "../SPEC.md", "./SPEC.md", "/SPEC.md", "a//b", "C:/SPEC.md", "a\\b", "NUL", "a\nb")),
            ("directory", base, "--path=../"),
        ]
        snapshot = file_snapshot(self.root)
        help_result = self.invoke("material", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn(b"blob,batch,diff,dependency,directory", help_result.stdout)
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                result = self.invoke("material", "--repo", str(self.root), *arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b"The value must be", result.stdout)
        for raw in (b"HEAD\n", b"--help\n", b"\n", b"\xff\n", b"f" * 100 + b"\n",
                    (oid + "\nHEAD\n").encode()):
            with self.subTest(raw=raw):
                result = self.invoke("material", "--repo", str(self.root), "batch", raw=raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b'"ok"', result.stdout)
                self.assertNotIn(b"Traceback", result.stderr)
        for raw in (oid.encode(), (oid + "\r\n").encode()):
            result = self.invoke("material", "--repo", str(self.root), "batch", raw=raw)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"The value must be 2.", result.stdout)
        self.assertEqual(file_snapshot(self.root), snapshot)

    def test_material_link_is_text_and_child_environment_is_sanitized(self):
        from task_governance_tool.completion import safe_git_environment
        from types import SimpleNamespace

        base = self.committed_fixture()
        oid = self.git("rev-parse", base + ":SPEC.md").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"120000,{oid},link")
        self.git("commit", "--quiet", "-m", "Immutable link text")
        revision = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "link").write_bytes(b"ambient must not be read")
        actual = self.invoke("material", "--repo", str(self.root), "dependency", revision, "--path=link")
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(actual.stdout, b"The value must be 2.\n")
        environment = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="diff.external",
                           GIT_CONFIG_VALUE_0="unwanted", GIT_NO_LAZY_FETCH="0")
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(preparation.subprocess, "run") as run:
            run.return_value.returncode = 0
            preparation.read_material(self.root, SimpleNamespace(material_operation="diff", before=oid, after=oid))
            self.assertEqual(dict(os.environ), environment)
            self.assertEqual(run.call_args.kwargs["env"], safe_git_environment())
            self.assertEqual({k: v for k, v in run.call_args.kwargs["env"].items() if k.startswith("GIT_")},
                             {"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
                              "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"})
            self.assertIn("--no-ext-diff", run.call_args.args[0])
            self.assertIn("--no-textconv", run.call_args.args[0])
            self.assertFalse(run.call_args.kwargs["shell"])

    def test_commit_root_and_first_parent_material_ignore_new_head(self):
        root = self.committed_fixture()
        task = self.task()
        for index, revision in enumerate((root,)):
            _, prepared = self.prepare(task, options=["--kind", "git_commit", "--revision", revision],
                                       directory=f"reviews/commit{index}")
            (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
            self.git("add", "--", "source.py")
            self.git("commit", "--quiet", "-m", "Later commit")
            result = self.displayed(prepared["handoff"]["packet_path"])
            self.assertEqual(result.returncode, 0, result.stdout)
            material = json.loads(result.stdout)["review_material"]
            self.assertEqual(material["dependency_revision"], root)
            self.assertTrue(all(e["kind"] == "add" for e in material["changes"]))
            collected = self.material_read(material["collect_command"].replace(
                "<selected dependency paths as JSON array>", "[]"))
            self.assertEqual(collected.returncode, 0, collected.stderr)
            self.assertTrue(all(entry["diff"]["format"] == "whole_file"
                                for entry in json.loads(collected.stdout)["changes"]))
        current = self.git("rev-parse", "HEAD").decode().strip()
        _, prepared = self.prepare(task, options=["--kind", "git_commit", "--revision", current],
                                   directory="reviews/second")
        result = self.displayed(prepared["handoff"]["packet_path"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["review_material"]["comparison_base"], root)
        material = json.loads(result.stdout)["review_material"]
        self.assertEqual(material["dependency_revision"], current)
        self.assertIn("SPEC.md", [row["path"] for row in material["unchanged_inventory"]["entries"]])

    def test_live_contract_and_generation_drift_and_opaque_targets(self):
        task = self.cli("task", "add", "--title", "Contract drift", "--status", "in_progress",
                        "--review-tier", "2", "--contract-scope", "Initial scope",
                        "--verification-not-required", "Isolated transport fixture without executable changes",
                        "--contract-acceptance", "Focused acceptance")["task"]["task_id"]
        _, prepared = self.prepare(task)
        packet = prepared["handoff"]["packet_path"]
        self.cli("task", "edit", task, "--contract-scope", "Changed accepted scope",
                 "--contract-acceptance", "Focused acceptance",
                 "--contract-authority-ref", f"user_instruction:{task}:2",
                 "--contract-change-reason", "Fixture authority")
        failed = self.displayed(packet)
        self.assertNotEqual(failed.returncode, 0)
        # A semantic Contract revision clears the live target by contract.
        self.assertEqual(json.loads(failed.stdout)["code"], "review_target_missing")
        # The independent opaque-target case needs a free owner slot; a
        # review-pending Task now retains it just like an executing Task.
        self.cli("task", "edit", task, "--status", "cancelled")
        _, prepared = self.prepare(self.task(), options=["--kind", "external_revision", "--revision", "approved-revision"],
                                   directory="reviews/external")
        result = self.displayed(prepared["handoff"]["packet_path"])
        self.assertEqual(result.returncode, 0, result.stdout)
        material = json.loads(result.stdout)["review_material"]
        self.assertEqual(material["status"], "requires_supplied_material")
        self.assertIsNone(material["changes"])
        task = json.loads((self.root / prepared["handoff"]["packet_path"]).read_bytes())["task"]["task_id"]
        self.cli("review", "target", "set", task, "--kind", "external_revision", "--revision", "approved-revision")
        failed = self.displayed(prepared["handoff"]["packet_path"])
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(json.loads(failed.stdout)["code"], "review_packet_stale")

    def test_complete_delta_recovers_bounded_packet_paths_and_modes(self):
        self.committed_fixture()
        for number in range(101):
            (self.root / f"added-{number:03}.py").write_text(f"n = {number}\n", encoding="utf-8")
        self.git("add", "--", *(f"added-{number:03}.py" for number in range(101)))
        self.git("mv", "--", "source.py", "renamed.py")
        self.git("update-index", "--chmod=+x", "SPEC.md")
        _, prepared = self.prepare(self.task(), options=["--kind", "git_snapshot"])
        result = self.displayed(prepared["handoff"]["packet_path"])
        self.assertEqual(result.returncode, 0, result.stdout)
        view = json.loads(result.stdout)
        self.assertTrue(view["changed_paths_truncated"])
        self.assertEqual(len(view["review_material"]["changes"]), 103)
        rename = next(e for e in view["review_material"]["changes"] if e["kind"] == "rename")
        self.assertEqual((rename["old_path"], rename["new_path"]), ("source.py", "renamed.py"))
        mode = next(e for e in view["review_material"]["changes"] if e["new_path"] == "SPEC.md")
        self.assertEqual((mode["before_mode"], mode["after_mode"]), ("100644", "100755"))

    def test_unavailable_response_or_git_material_returns_no_partial_view(self):
        task = self.task()
        _, prepared = self.prepare(task)
        packet = prepared["handoff"]["packet_path"]
        for raw in (None, b"{", b"{}"):
            with mock.patch.object(preparation, "_capture", return_value=(0, raw)):
                with self.assertRaises((handoff.HandoffError, ValueError)):
                    preparation.read_for_reviewer(self.root, packet)
        self.committed_fixture()
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "--", "source.py")
        _, prepared = self.prepare(task, options=["--kind", "git_snapshot"], directory="reviews/git")
        from task_governance_tool.artifact_manifest import ArtifactManifestError
        with mock.patch("task_governance_tool.artifact_manifest.observe_staged_git_manifest",
                        side_effect=ArtifactManifestError("artifact_manifest_stale", "fixture")):
            with self.assertRaises(handoff.HandoffError):
                preparation.read_for_reviewer(self.root, prepared["handoff"]["packet_path"])

    def test_complete_packet_alternative_routes_keep_explicit_approval_and_tier_zero(self):
        for tier, kind in ((2, "self_review_fallback"), (0, "not_required")):
            task = self.cli("task", "add", "--title", "Alternative route", "--review-tier", str(tier),
                            "--status", "in_progress", "--verification-not-required",
                            "Isolated transport fixture without executable changes")["task"]["task_id"]
            _, prepared = self.prepare(task, directory=f"reviews/tier{tier}")
            packet_path = prepared["handoff"]["packet_path"]
            packet = json.loads((self.root / packet_path).read_bytes())
            self.assertEqual(packet["result_instructions"], review_result_instructions())
            payload = copy.deepcopy(packet["result_template"])
            payload["receipts"] = [receipt("alternative", kind=kind)]
            if tier == 0:
                payload["receipts"][0].update(verdict="not_required", provenance=None)
            raw = encode(payload)
            output = f"reviews/tier{tier}/original.json"
            approvals = ["--user-approved-reviewer", "alternative"] if tier == 2 else []
            if tier == 2:
                failed = self.invoke("save", "--repo", str(self.root), "--packet", packet_path,
                                     "--output", output, raw=raw)
                self.assertNotEqual(failed.returncode, 0)
            saved = self.invoke("save", "--repo", str(self.root), "--packet", packet_path,
                                "--output", output, *approvals, raw=raw)
            self.assertEqual(saved.returncode, 0, saved.stdout)
            submitted = self.invoke("submit", "--repo", str(self.root), "--packet", packet_path,
                                    *approvals, output)
            self.assertEqual(submitted.returncode, 0, submitted.stdout)
            self.assertEqual(json.loads(submitted.stdout)["data"]["receipts"][0]["receipt"]["receipt_kind"], kind)
            self.cli("task", "edit", task, "--status", "review_pending")
            # End this fixture's execution before acquiring the next tier case;
            # review-pending retains the same session/project slot as execution.
            self.cli("task", "edit", task, "--status", "ready")


class DirectPacketReviewerTests(unittest.TestCase):
    def test_package_only_non_git_direct_packet_returns_original_without_shared_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve(), git_managed=False)
            def cli(*args):
                completed = install.run(*args, "--json")
                self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
                return json.loads(completed.stdout)["data"]
            cli("setup")
            task = cli("task", "add", "--title", "Direct independent review", "--review-tier", "2",
                       "--status", "in_progress", "--verification-not-required", "Isolated transport fixture without executable changes")["task"]["task_id"]
            packet = cli("review", "target", "set", task, "--kind", "diff_fingerprint",
                         "--revision", FINGERPRINT)["review_preparation"]["packet"]
            # Reviewer receives this complete object, not a file or helper commands.
            # Retrieve only its own packaged procedure. Semantic review checks
            # the transport condition; no natural-language canary duplicates it.
            guide = subprocess.run([sys.executable, "-I", "-S",
                str(install.skill_root / "scripts/read_reference.py"),
                "references/task_workflow.md#independent-reviewer"],
                cwd=install.project_root, capture_output=True, check=False)
            self.assertEqual(guide.returncode, 0, guide.stderr)
            self.assertIn(b"## Independent Reviewer", guide.stdout)
            self.assertNotIn(b"### Prepare And Record Reviews", guide.stdout)
            payload = copy.deepcopy(packet["result_template"])
            payload["receipts"] = [receipt("direct-reviewer", findings=[
                {"severity": "low", "summary": "example.py:1 Retain this direct-path Finding"}])]
            returned_original = b"\n " + encode(payload) + b"\n"
            # Parent registers those exact bytes through the existing public CLI.
            submitted = subprocess.run([sys.executable, "-I", "-S", str(install.entrypoint),
                "review", "result", "add", task, "--json"], cwd=install.project_root,
                input=returned_original, capture_output=True, check=False)
            self.assertEqual(submitted.returncode, 0, submitted.stdout or submitted.stderr)
            data = json.loads(submitted.stdout)["data"]
            row = data["receipts"][0]
            self.assertEqual(data["review_gate"]["basis"], {
                "task_id": task, "contract_revision": packet["contract"]["revision"],
                "review_target": packet["review_target"],
            })
            self.assertEqual(row["findings"][0]["finding"]["summary"], payload["receipts"][0]["findings"][0]["summary"])
            self.assertFalse((install.project_root / ".git").exists())
            self.assertFalse((install.project_root / "reviews").exists())


class PreparationFailureTests(PreparationFixture):
    # Reuse the real source response for injected transport/file failure checks,
    # not a handwritten Packet. No fixture file precedes the source invocation.
    def setUp(self):
        super().setUp()
        self.task_id = self.task()
        completed = self.install.run("review", "target", "set", self.task_id, "--kind", "diff_fingerprint",
                                     "--revision", FINGERPRINT, "--json")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.response = json.loads(completed.stdout)

    def run_injected(self, response=None, code=0, directory="reviews/g1", capture_error=None):
        def captured(command, raw, started):
            started()
            if capture_error:
                raise capture_error
            return code, encode(self.response) if response is None else response
        output = io.StringIO()
        with mock.patch.object(preparation, "_capture", side_effect=captured) as capture, redirect_stdout(output):
            exit_code = handoff.main(["prepare", "--repo", str(self.root), "--directory", directory,
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        return exit_code, json.loads(output.getvalue()), capture

    def test_simulated_os_choice_applies_to_every_missing_directory(self):
        (self.root / "reviews").mkdir()
        mkdir = Path.mkdir
        for name in ("nt", "posix"):
            with self.subTest(os_name=name):
                variant = mock.Mock(wraps=os)
                variant.name = name  # Do not change the host os.name used by pathlib.
                seen = []

                def create(path, *args, **kwargs):
                    seen.append((path, args, kwargs))
                    return mkdir(path, *args, **kwargs)

                with mock.patch.object(preparation, "os", variant), \
                     mock.patch.object(Path, "mkdir", create):
                    code, result, capture = self.run_injected(directory=f"reviews/{name}/middle/g1")
                self.assertEqual(code, 0, result)
                capture.assert_called_once()
                expected = {} if name == "nt" else {"mode": 0o700}
                self.assertEqual(seen, [(self.root / path, (), expected) for path in
                    (f"reviews/{name}", f"reviews/{name}/middle", f"reviews/{name}/middle/g1")])
        # OS-selection coverage only; not a native POSIX permission observation.

    def test_malformed_or_incomplete_capture_has_unknown_outcome_and_no_files(self):
        for raw in (b"", b"{", b"{}", b"\xef\xbb\xbf{}", b'{"ok":true,"ok":false}', b"null"):
            with self.subTest(raw=raw):
                code, result, capture = self.run_injected(raw)
                self.assertEqual(code, 1)
                self.assertEqual(result["operation_status"], "unknown")
                capture.assert_called_once()
                self.assertFalse((self.root / "reviews").exists())
        code, result, capture = self.run_injected(capture_error=KeyboardInterrupt())
        self.assertEqual(result["operation_status"], "unknown")
        capture.assert_called_once()

    def test_preparation_failure_preserves_committed_outcome_binding_and_warnings(self):
        changed = copy.deepcopy(self.response)
        changed["warnings"] = [{"code": "viewer_update_failed", "message": "Viewer update failed"}]
        changed["data"]["review_preparation"].update(status="failed", packet=None,
            errors=[{"code": "internal_error", "message": "could not prepare review context"}])
        code, result, capture = self.run_injected(encode(changed))
        self.assertEqual(code, 1)
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertEqual(result["source"]["preparation_binding"], changed["data"]["review_preparation"]["binding"])
        self.assertEqual(result["warnings"], changed["warnings"])
        capture.assert_called_once()
        self.assertFalse((self.root / "reviews").exists())

    def test_packet_missing_or_mismatched_never_dispatches_reviewers(self):
        for change in (
            lambda p: p.pop("required_output"),
            lambda p: p["task"].pop("verification"),
            lambda p: p["contract"].pop("scope"),
            lambda p: p["result_template"]["review_target"].update(generation=9),
            lambda p: p["review_target"].update(base_revision="incorrect"),
            lambda p: p.update(review_focus=[]),
        ):
            changed = copy.deepcopy(self.response)
            change(changed["data"]["review_preparation"]["packet"])
            code, result, capture = self.run_injected(encode(changed))
            self.assertEqual(code, 1)
            self.assertEqual(result["operation_status"], "succeeded")
            self.assertNotIn("review_requests", result["handoff"])
            self.assertFalse((self.root / "reviews").exists())

    def test_stored_constraints_compatibility_does_not_relax_other_privacy(self):
        cases = (("constraints", "dispatch_authorization=07"),
                 ("constraints", '{"dispatch_authorization":"7"}'),
                 ("constraints", "dispatch_authorization=7 token=opaque-value"),
                 ("scope", "dispatch_authorization=7"),
                 ("acceptance", '{"dispatch_authorization":7}'))
        for field, text in cases:
            with self.subTest(field=field, text=text):
                changed = copy.deepcopy(self.response)
                changed["data"]["review_preparation"]["packet"]["contract"][field] = text
                _, result, capture = self.run_injected(encode(changed))
                self.assertEqual(result["operation_status"], "succeeded")
                self.assertEqual(result["errors"][0]["code"], "privacy_rejected")
                self.assertNotIn("review_requests", result["handoff"])
                capture.assert_called_once()
                self.assertFalse((self.root / "reviews").exists())
        for text in ("dispatch_authorization=7", '{"dispatch_authorization":7}'):
            rejected = self.install.run("task", "add", "--title", "New caller input",
                "--contract-scope", "Bounded scope", "--contract-acceptance", "Focused checks",
                "--contract-constraints", text, "--json")
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(json.loads(rejected.stdout)["errors"][0]["code"], "privacy_rejected")

    def test_exit_mismatch_and_source_start_failure_never_claim_success(self):
        _, result, capture = self.run_injected(code=1)
        self.assertEqual(result["operation_status"], "unknown")
        capture.assert_called_once()
        output = io.StringIO()
        with mock.patch.object(preparation, "_capture", side_effect=PermissionError("private details")), redirect_stdout(output):
            code = handoff.main(["prepare", "--repo", str(self.root), "--directory", "reviews/g1",
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["operation_status"], "not_started")
        self.assertFalse((self.root / "reviews").exists())

    def test_preflight_bad_or_existing_directories_never_invoke_source(self):
        (self.root / "reviews").mkdir()
        for directory in ("../escape", ".taskgov/copy", ".agents/copy", "reviews", "unignored", "reviews/NUL", "reviews/a:ads"):
            code, result, capture = self.run_injected(directory=directory)
            self.assertEqual(code, 1, directory)
            self.assertEqual(result["operation_status"], "not_started")
            capture.assert_not_called()
        stat = Path.lstat
        def reparse(path):
            if path == self.root / "reviews":
                value = mock.Mock(st_mode=0o040700, st_file_attributes=1024)
                return value
            return stat(path)
        with mock.patch.object(Path, "lstat", reparse):
            _, result, capture = self.run_injected()
        self.assertEqual(result["operation_status"], "not_started")
        capture.assert_not_called()

    def test_write_and_readback_failures_keep_source_success_and_residue(self):
        for index, method in enumerate(("_write_new", "_read")):
            with mock.patch.object(handoff, method, side_effect=PermissionError("private failure detail")):
                _, result, capture = self.run_injected(directory=f"reviews/fail-{index}")
            self.assertEqual(result["operation_status"], "succeeded")
            self.assertNotIn("review_requests", result["handoff"])
            self.assertNotIn("private failure detail", json.dumps(result))
            self.assertTrue((self.root / f"reviews/fail-{index}").is_dir())
            self.assertEqual((self.root / f"reviews/fail-{index}/packet.json").exists(), method == "_read")
            capture.assert_called_once()

    def test_partial_packet_and_raced_directory_are_retained_without_requests(self):
        write = os.write
        count = []
        def partial(descriptor, raw):
            count.append(True)
            if len(count) == 1:
                return write(descriptor, raw[:8])
            raise OSError("private error")
        with mock.patch.object(handoff.os, "write", side_effect=partial):
            _, result, capture = self.run_injected(directory="reviews/partial")
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertNotIn("review_requests", result["handoff"])
        self.assertEqual((self.root / "reviews/partial/packet.json").stat().st_size, 8)
        capture.assert_called_once()
        def raced(command, raw, started):
            started()
            (self.root / "reviews/raced").mkdir()
            return 0, encode(self.response)
        output = io.StringIO()
        with mock.patch.object(preparation, "_capture", side_effect=raced) as capture, redirect_stdout(output):
            handoff.main(["prepare", "--repo", str(self.root), "--directory", "reviews/raced",
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        self.assertEqual(json.loads(output.getvalue())["operation_status"], "succeeded")
        self.assertFalse((self.root / "reviews/raced/packet.json").exists())
        capture.assert_called_once()

    def test_changed_packet_readback_and_preflight_ignore_failure_do_not_dispatch(self):
        read = handoff._read
        def changed(path, limit):
            return read(path, limit) + b" "
        with mock.patch.object(handoff, "_read", side_effect=changed):
            _, result, capture = self.run_injected()
        self.assertEqual(result["operation_status"], "succeeded")
        self.assertNotIn("review_requests", result["handoff"])
        self.assertTrue((self.root / "reviews/g1/packet.json").exists())
        with mock.patch.object(handoff, "_ignored", side_effect=handoff.HandoffError("handoff_ignore_required")):
            _, result, capture = self.run_injected(directory="reviews/denied")
        self.assertEqual(result["operation_status"], "not_started")
        capture.assert_not_called()

    def test_lost_acknowledgement_never_replays_the_source(self):
        def captured(command, raw, started):
            started()
            return 0, encode(self.response)
        with mock.patch.object(preparation, "_capture", side_effect=captured) as capture, \
             mock.patch.object(handoff, "_emit", return_value=False):
            result = handoff.main(["prepare", "--repo", str(self.root), "--directory", "reviews/g1",
                "target", self.task_id, "--kind", "diff_fingerprint", "--revision", FINGERPRINT])
        self.assertEqual(result, 1)
        capture.assert_called_once()
        self.assertTrue((self.root / "reviews/g1/packet.json").is_file())

    def test_capture_drains_excess_without_persisting_or_replaying(self):
        process = mock.Mock(stdout=io.BytesIO(b"x" * (preparation.RESPONSE_LIMIT + 1)), stdin=None)
        process.wait.return_value = 0
        with mock.patch.object(preparation.subprocess, "Popen", return_value=process) as launch:
            code, raw = preparation._capture(["fixed-entry"], None, lambda: None)
        self.assertEqual(code, 0)
        self.assertIsNone(raw)
        launch.assert_called_once()
        process.wait.assert_called_once()


class SharedPatchDeliveryTests(unittest.TestCase):
    def test_exact_equality_keeps_headers_line_endings_and_unique_budget(self):
        from task_governance_tool import review_material_collection as collection
        payloads = iter((b"header A\n+same\n", b"header A\n+same\n",
                         b"header B\n+same\n", b"header A\r\n+same\r\n"))
        def stream(repo, arguments, consume):
            consume(next(payloads))
        bodies = collection._Bodies(Path("unused"))
        with mock.patch.object(collection, "run_git_stream", side_effect=stream) as read:
            first = bodies.patch("a", "b")
            self.assertEqual(first, bodies.patch("a", "b"))
            self.assertEqual(first, bodies.patch("c", "d"))
            self.assertNotEqual(first, bodies.patch("e", "f"))
            self.assertNotEqual(first, bodies.patch("g", "h"))
        self.assertEqual(read.call_count, 4)
        self.assertEqual(len(bodies.patches), 3)
        self.assertEqual(collection.TOTAL_BYTE_LIMIT - bodies.remaining,
                         sum(row["byte_count"] for row in bodies.patches.values()))

    def test_failed_patch_has_no_shared_or_partial_text_and_keeps_status(self):
        from task_governance_tool import review_material_collection as collection
        cases = ((b"long", 10, 3, "too_large"), (b"long", 3, 10, "delivery_limit"),
                 (b"\xff", 10, 10, "non_text"), (b"\0", 10, 10, "non_text"))
        for raw, total, per_body, status in cases:
            with self.subTest(status=status), mock.patch.object(collection, "BODY_BYTE_LIMIT", per_body):
                bodies = collection._Bodies(Path("unused"))
                bodies.remaining = total
                with mock.patch.object(collection, "run_git_stream", side_effect=lambda repo, args, consume: consume(raw)) as read:
                    expected = {"format": "git_patch", "status": status}
                    self.assertEqual(bodies.patch("a", "b"), expected)
                    self.assertEqual(bodies.patch("a", "b"), expected)
                read.assert_called_once()
                self.assertEqual(bodies.patches, {})
                self.assertEqual(bodies.remaining, total)


class LegacyPacketPreparationTests(unittest.TestCase):
    def test_packet_rejects_legacy_done_contract_drift(self):
        from tests.test_m22_evidence_ledger_storage import initialize_v17_fixture, seed_v17_contract_constraints
        from task_governance_tool.storage import connect, apply_migrations, StorageError
        from task_governance_tool.review_packet import prepare_review_packet
        with tempfile.TemporaryDirectory() as temporary:
            target, task_id = initialize_v17_fixture(Path(temporary))
            with closing(connect(target.db_path)) as connection:
                seed_v17_contract_constraints(connection, project_id=target.project.project_id,
                                             task_id=task_id, constraints="dispatch_authorization=7")
                apply_migrations(connection)
            with self.assertRaises(StorageError) as failed:
                prepare_review_packet(target, task_id)
            self.assertEqual(failed.exception.code, "completion_history_inconsistent")

    def test_migrated_public_packet_preserves_both_stored_constraints_forms(self):
        from tests.test_m22_evidence_ledger_storage import initialize_v17_fixture, seed_v17_contract_constraints
        from task_governance_tool.storage import connect, apply_migrations
        from task_governance_tool.review_packet import prepare_review_packet

        for constraints in ("dispatch_authorization=7", '{"dispatch_authorization":7}'):
            with self.subTest(constraints=constraints), tempfile.TemporaryDirectory() as temporary:
                target, _ = initialize_v17_fixture(Path(temporary))
                with closing(connect(target.db_path)) as connection:
                    # Add this Contract to the live legacy Task, not after a
                    # different Task's immutable completion. The latter creates
                    # a real Contract/cycle mismatch when evidence is projected.
                    task_id = connection.execute("SELECT task_id FROM tasks WHERE status = 'ready'").fetchone()[0]
                    connection.execute("UPDATE tasks SET review_target_kind = 'external_revision', "
                        "review_target_value = 'legacy-live', review_target_generation = 1 WHERE task_id = ?", (task_id,))
                    seed_v17_contract_constraints(connection, project_id=target.project.project_id,
                                                 task_id=task_id, constraints=constraints)
                    apply_migrations(connection)
                # Real migrated storage and public Packet producer, no invented
                # Packet/constraints normalization in the transport regression.
                packet = prepare_review_packet(target, task_id)
                transported = json.loads(preparation._packet(packet, task_id))
                self.assertEqual(transported, packet)
                self.assertEqual(transported["contract"]["constraints"], constraints)
                self.assertFalse(transported["verification_evidence"]["gate"]["satisfied"])
