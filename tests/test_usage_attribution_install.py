"""Public CLI acknowledgements joined to actual isolated core transitions."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tests.m14_test_support import make_physical_install, refresh_test_manifest
from tests.test_usage_turn_adapter import tool_record
from tests.test_usage_collection import event, usage
from tests.test_usage_attribution import THREAD, turn
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.state_resolver import observe_current_root
from task_governance_tool.usage_adapter import SourceInput
from task_governance_tool.usage_collection import collect_registered, register_source
from task_governance_tool.usage_attribution_repository import UsageAttributionRepository


class UsageAttributionInstallTests(unittest.TestCase):
    def test_public_start_exit_restart_are_inclusive_and_use_actual_core_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary), git_managed=True)
            refresh_test_manifest(install.skill_root)
            root = install.project_root
            environment = {**os.environ, "CODEX_THREAD_ID": THREAD}
            environment.pop("PYTHONPATH", None)

            def cli(*arguments):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", str(install.entrypoint),
                                         *arguments, "--json"], cwd=root, env=environment,
                                        capture_output=True, timeout=30, check=False)
                value = json.loads(result.stdout)
                self.assertTrue(value["ok"], value)
                self.assertEqual(result.returncode, 0)
                return value

            cli("setup")
            added = cli("task", "add", "--title", "Inclusive-turn fixture", "--kind", "optional",
                        "--status", "in_progress", "--review-tier", "0", "--verification-not-required", "Isolated fixture")
            task = added["data"]["task"]
            ready = cli("task", "edit", task["task_id"], "--status", "ready")
            resumed = cli("task", "edit", task["task_id"], "--status", "in_progress")
            pending = cli("task", "edit", task["task_id"], "--status", "review_pending")
            closed = cli("task", "edit", task["task_id"], "--status", "cancelled")
            self.assertNotEqual(task["ownership"]["execution_id"], resumed["data"]["task"]["ownership"]["execution_id"])
            repository = UsageAttributionRepository(root / "isolated-usage.sqlite", task["project_id"],
                                                     observe_current_root(root).canonical_path_hash, 1)
            repository.initialize()
            logs = root / "logs"
            logs.mkdir()
            source = SourceInput(THREAD, logs / "fixture.jsonl", logs, root)
            register_source(repository, source, CallerIdentity(THREAD))
            rows = [event("session_meta", id=THREAD, cwd=str(root), model_provider="openai")]
            acknowledgements = {2: added, 4: ready, 7: resumed, 9: pending, 11: closed}
            for number in range(1, 13):
                rows.extend((event("event_msg", type="task_started", turn_id=turn(number), started_at=number * 1000),
                             event("turn_context", turn_id=turn(number), model="fixture-model")))
                if number in acknowledgements:
                    rows.append(tool_record(acknowledgements[number], number))
                rows.append(usage(f"r{number}", turn=turn(number)))
            source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
            self.assertNotEqual(collect_registered(repository, source)["status"], "unknown")
            with closing(sqlite3.connect(f"file:{install.db_path.as_posix()}?mode=ro", uri=True)) as core:
                core.row_factory = sqlite3.Row
                core.execute("PRAGMA query_only=ON")
                projected = repository.attribution(core)
            self.assertEqual(projected["unresolved_operations"], 0)
            self.assertEqual(projected["tasks"][0]["task_id"], task["task_id"])
            self.assertEqual(projected["tasks"][0]["models"][0]["total_tokens"], 8 * 120)
            self.assertEqual(len(projected["tasks"][0]["own_executions"]), 2)
            self.assertIn(("openai", "r12"), projected["unassigned_response_keys"])
