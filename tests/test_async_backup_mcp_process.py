"""Actual installed MCP entry owns a backup after a separate client exits.

This subprocess test qualifies the service topology and real source-identity
admission. It does not register or restart the user's configured MCP service.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import sqlite3
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.test_m17_recovery_hardening import _setup_current
from task_governance_tool import async_backup_opportunity as opportunity
from task_governance_tool import async_backup_repository as repository
from task_governance_tool import async_backup_state as state


_CLIENT = r'''
import json, sys
from pathlib import Path
skill, repo, project, binding, generation = sys.argv[1:]
sys.path.insert(0, str(Path(skill) / "scripts"))
from task_governance_tool.state_resolver import backup_runtime_target
from task_governance_tool.async_backup_opportunity import request_backup
target = backup_runtime_target(Path(skill), repo=Path(repo), project_id=project,
    binding_path_hash=binding, binding_generation=int(generation))
print(json.dumps({"code": request_backup(target).code}), flush=True)
'''


class AsyncBackupMcpProcessTests(unittest.TestCase):
    @contextmanager
    def running_service(self, install, directory):
        service = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B",
             str(install.skill_root / "scripts/review_wait_server.py"),
             "--repo", str(install.project_root),
             "--server", str(Path(directory) / "unused-host-wrapper.mjs"),
             "--codex-home", str(Path(directory) / "unused-codex-home"),
             "--timezone", "UTC"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        responses = queue.Queue(maxsize=32)

        def read_responses():
            for line in iter(lambda: service.stdout.readline(524289), b""):
                try:
                    responses.put_nowait(json.loads(line))
                except (ValueError, queue.Full):
                    return

        reader = threading.Thread(target=read_responses, daemon=True)
        reader.start()
        serial = 0

        def send(method, params=None, *, notification=False):
            nonlocal serial
            serial += 1
            message = {"jsonrpc": "2.0", "method": method}
            if not notification:
                message["id"] = serial
            if params is not None:
                message["params"] = params
            service.stdin.write(json.dumps(message).encode("ascii") + b"\n")
            service.stdin.flush()
            if notification:
                return None
            reply = responses.get(timeout=5)
            self.assertEqual(reply.get("id"), serial)
            self.assertNotIn("error", reply)
            return reply["result"]

        try:
            initialized = send("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "isolated-backup-test", "version": "1"}})
            self.assertEqual(initialized["serverInfo"]["name"], "taskgov-review-wait")
            send("notifications/initialized", notification=True)
            self.assertEqual(send("ping"), {})
            tools = send("tools/list")["tools"]
            self.assertTrue(any(item["name"] == "review_wait_wait" for item in tools))
            yield service, send, tools
        finally:
            # This handle belongs only to this fixture. EOF closes the
            # observer; backup completion is awaited above, not in close.
            if service.stdin is not None:
                service.stdin.close()
                service.stdin = None
            try:
                service.wait(timeout=4)
            except subprocess.TimeoutExpired:
                service.terminate()
                try:
                    service.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    service.kill()
                    service.wait(timeout=2)
            service.stdout.close()
            reader.join(timeout=1)
        self.assertEqual(service.returncode, 0)

    def test_real_owner_readmission_busy_leaves_service_responsive_until_replaced(self):
        from task_governance_tool import backup_observer_diagnostics as diagnostics
        with tempfile.TemporaryDirectory() as directory:
            install, target, _ = _setup_current(Path(directory))
            root = state.runtime_root(target, create=True)
            # Hold ownership until the child has admitted its startup target and
            # answered an MCP request. Then force its next DB read to be busy.
            with ExitStack() as stack:
                lease = stack.enter_context(ExitStack())
                lease.enter_context(opportunity.service_lease(target))
                _, send, _ = stack.enter_context(self.running_service(install, directory))
                with closing(sqlite3.connect(target.db_path, isolation_level=None)) as writer:
                    writer.execute("BEGIN EXCLUSIVE")
                    try:
                        lease.close()
                        stop = time.monotonic() + 18
                        while time.monotonic() < stop:
                            if (root / "observer.json").exists() and state.lock_owned(root / "service.lock") is False:
                                break
                            time.sleep(.05)
                        self.assertTrue((root / "observer.json").is_file())
                        self.assertEqual(send("ping"), {})
                    finally:
                        writer.rollback()
                self.assertEqual(opportunity.request_backup(target).code, "deferred")
                observed = json.loads(install.run("doctor", "--json").stdout)
                backup = observed["data"]["components"]["maintenance"]["backup"]
                self.assertEqual(backup["runtime"]["state"], "idle")
                self.assertEqual(backup["observer"]["ownership"], "released")
                failure = backup["observer"]["last_recorded"]
                self.assertEqual((failure["stage"], failure["reason"]), ("owner_readmission", "database_busy"))
                # A new service takes ownership while the first still responds.
                record_path = root / "observer.json"
                previous = record_path.stat()
                with self.running_service(install, directory) as (_, new_send, _):
                    stop = time.monotonic() + 8
                    history = None
                    while time.monotonic() < stop:
                        # The writer acquires state.lock once. Poll the atomic
                        # replacement without contending for that same lock.
                        current = record_path.stat()
                        if (current.st_ino, current.st_size) != (previous.st_ino, previous.st_size):
                            history = diagnostics.observe(target, enabled=True)
                            if (history["last_recorded"] or {}).get("reason") == "activated":
                                break
                        time.sleep(.05)
                    self.assertIsNotNone(history, "replacement service did not publish an observer record")
                    self.assertEqual(history["ownership"], "held")
                    self.assertEqual(history["last_recorded"]["reason"], "activated")
                    self.assertEqual(opportunity.request_backup(target).code, "requested")
                    self.assertEqual(new_send("ping"), {})
                    stop = time.monotonic() + 45
                    while time.monotonic() < stop:
                        if state.observe_runtime(target)["state"] == "succeeded":
                            break
                        time.sleep(.1)
                    self.assertEqual(state.observe_runtime(target)["state"], "succeeded")
                    self.assertEqual(send("ping"), {})

    def test_actual_mcp_entry_owns_worker_after_client_exit_and_stays_responsive(self):
        with tempfile.TemporaryDirectory() as directory:
            install, target, _ = _setup_current(Path(directory))
            root = state.runtime_root(target)
            with self.running_service(install, directory) as (service, send, tools):
                deadline = time.monotonic() + 5
                presence = None
                while time.monotonic() < deadline:
                    # Presence is published atomically. Poll without taking the
                    # zero-wait lock the observer needs for its one activation.
                    presence = opportunity._read(root, "service.json")
                    if presence is not None:
                        break
                    time.sleep(.02)
                self.assertIsNotNone(presence, "real MCP entry did not activate backup observation")

                client = subprocess.run(
                    [sys.executable, "-I", "-S", "-B", "-c", _CLIENT,
                     str(install.skill_root), str(install.project_root),
                     target.project.project_id, target.binding_path_hash,
                     str(target.binding_generation)],
                    capture_output=True, text=True, timeout=8)
                self.assertEqual(client.returncode, 0, client.stderr)
                self.assertEqual(json.loads(client.stdout), {"code": "requested"})
                # The client process has exited; default 10-second observation
                # has not yet started a worker. No client lifetime owns it.
                self.assertEqual(state.observe_runtime(target)["state"], "idle")
                self.assertEqual(send("ping"), {})

                deadline = time.monotonic() + 45
                observed = None
                next_ping = time.monotonic()
                pings = 0
                while time.monotonic() < deadline:
                    self.assertIsNone(service.poll(), "MCP service exited during backup")
                    observed = state.observe_runtime(target)
                    if observed["state"] == "succeeded":
                        break
                    if time.monotonic() >= next_ping:
                        self.assertEqual(send("ping"), {})
                        pings += 1
                        next_ping = time.monotonic() + 1
                    time.sleep(.05)
                self.assertIsNotNone(observed)
                self.assertEqual(observed["state"], "succeeded", observed)
                self.assertGreater(pings, 0)
                self.assertIsNotNone(observed["captured_at"])
                self.assertIsNotNone(observed["published_at"])
                control = repository.read_control(target)
                self.assertEqual(len(control.generations), 1)
                self.assertEqual(control.generations[0].generation_id, observed["generation_id"])
                self.assertEqual(send("ping"), {})
                self.assertEqual(len(send("tools/list")["tools"]), len(tools))
                self.assertIsNone(service.poll())
                self.assertIs(state.lock_owned(root / "worker.lock"), False)



if __name__ == "__main__":
    unittest.main()
