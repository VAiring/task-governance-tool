"""Normal public Task output never acquires numerical usage; audit still does."""
from contextlib import closing, contextmanager, redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import file_snapshot
from tests.test_m17_recovery_hardening import _setup_current
from tests import test_usage_read_performance as performance
from task_governance_tool import cli, usage_evidence_service as service
from task_governance_tool import usage_repository
from task_governance_tool.storage import connect_initialized_task_readonly


class UsageNormalOutputTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.install, self.target, _ = _setup_current(directory)
        self.root = self.install.project_root.resolve()

    def command(self, *arguments, text=False):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": performance.THREAD}), \
                mock.patch.object(cli, "cli_script_path", return_value=self.install.entrypoint), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*arguments, "--repo", str(self.root), *([] if text else ["--json"])],
                            _maintenance_enabled=False)
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        self.assertEqual(err.getvalue(), "")
        return out.getvalue() if text else json.loads(out.getvalue())

    @contextmanager
    def no_usage(self):
        with mock.patch.object(service, "read_task_usage", wraps=service.read_task_usage) as read, \
                mock.patch.object(usage_repository.UsageRepository, "connection",
                                  side_effect=AssertionError("numerical store opened")) as opened:
            yield
            read.assert_not_called()
            opened.assert_not_called()

    def add(self, title="Display fixture", *, text=False):
        return self.command("task", "add", "--title", title,
                            "--verification-not-required", "Isolated display fixture", text=text)

    def test_json_text_selection_registration_and_internal_detail_skip_usage(self):
        with self.no_usage():
            before = file_snapshot(self.root)
            self.assertIsNone(self.command("task", "context")["data"]["selected"])
            self.command("task", "context", text=True)
            self.assertEqual(file_snapshot(self.root), before)
            added = self.add()
            selected = added["data"]["context_preparation"]["context"]["selected"]
            self.assertNotIn("usage", selected)
            task = selected["task"]["task_id"]
            self.command("task", "edit", task, "--priority", "high", "--add-note", "Retain operational note")
            self.add("Text registration", text=True)
            before = file_snapshot(self.root)
            normal = self.command("task", "show", task)["data"]
            self.assertNotIn("usage", normal)
            self.assertIn("Retain operational note", [e["note_body"] for e in normal["events"]])
            self.assertEqual(self.command("task", "context")["data"]["selected"], normal)
            shown_text = self.command("task", "show", task, text=True)
            self.assertIn("Display fixture", shown_text)
            self.command("task", "context", text=True)
            with mock.patch.object(cli, "read_task_detail", wraps=cli.read_task_detail) as detail:
                self.command("task", "show", task)
                context = detail.call_args.args[0]
            # Internal finalizer's existing boundary must stay numerical-free,
            # even when an internal caller asks for audit evidence projection.
            context.args.audit = True
            internal = cli.read_task_detail(replace(context, read_connection_override=None))
            self.assertTrue(internal.ok, internal.errors)
            self.assertNotIn("usage", internal.data)
            self.assertEqual(file_snapshot(self.root), before)

    def test_uninitialized_disabled_and_corrupt_numerical_store_do_not_change_task_detail(self):
        added = self.add()
        task = added["data"]["context_preparation"]["context"]["selected"]["task"]["task_id"]
        expected = self.command("task", "show", task)["data"]
        path = self.target.resolved_usage_database
        # This is a copied fixture's opt-out, not live config. Setup itself may
        # inspect numerical state; the zero-read promise concerns Task display.
        self.command("setup", "--usage-collection", "off")
        with self.no_usage():
            for state in ("disabled", "missing", "corrupt"):
                with self.subTest(state=state):
                    if state == "missing":
                        path.unlink()
                    elif state == "corrupt":
                        path.write_bytes(b"isolated invalid numerical store")
                    before = file_snapshot(self.root)
                    self.assertEqual(self.command("task", "show", task)["data"], expected)
                    self.command("task", "show", task, text=True)
                    self.assertEqual(self.command("task", "context")["data"]["selected"], expected)
                    self.assertEqual(file_snapshot(self.root), before)


class UsageNormalOutputPerformanceTests(unittest.TestCase):
    cli = performance.UsageReadPerformanceTests.cli
    add = performance.UsageReadPerformanceTests.add
    complete = performance.UsageReadPerformanceTests.complete
    response_header = performance.UsageReadPerformanceTests.response_header
    response_rows = performance.UsageReadPerformanceTests.response_rows
    fixture = performance.UsageReadPerformanceTests.fixture
    event = performance.UsageReadPerformanceTests.event
    measure = performance.UsageReadPerformanceTests.measure

    def test_history_sizes_normal_output_and_retained_audit_internal_capture(self):
        for count in (4, 16):
            with self.subTest(tasks=count):
                tasks = self.fixture(count)
                before = file_snapshot(self.root)
                with mock.patch.object(service, "read_task_usage", wraps=service.read_task_usage) as read, \
                        mock.patch.object(usage_repository, "connect_readonly", wraps=usage_repository.connect_readonly) as opened:
                    normal, work = self.measure(lambda: self.cli("task", "show", tasks[0]))
                    read.assert_not_called()
                    opened.assert_not_called()
                self.assertNotIn("usage", normal["data"])
                self.assertEqual(work["counts"].get("numerical_sql_after_open", 0), 0)
                with mock.patch.object(service, "read_task_usage", wraps=service.read_task_usage) as read:
                    audit, audit_work = self.measure(lambda: self.cli("task", "show", tasks[0], "--audit"))
                    read.assert_called_once()
                self.assertEqual(audit["data"]["usage"]["total"]["response_count"], 3)
                self.assertGreater(audit_work["counts"]["numerical_sql_after_open"], 0)
                with closing(connect_initialized_task_readonly(self.target)) as core:
                    internal = service.read_task_usage(self.target, core, tasks[0])
                self.assertEqual(internal["total"], audit["data"]["usage"]["total"])
                self.assertEqual(file_snapshot(self.root), before)
                print(json.dumps({"tasks": count, "responses": count * 3,
                    "normal": work, "audit": audit_work}))
