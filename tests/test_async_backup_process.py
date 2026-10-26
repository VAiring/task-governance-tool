"""Real one-shot workers and launching-CLI lifetime, using isolated installs.

Controlled barriers wrap only the private-copy work inside fixture processes.
The production launcher, GO handshake, worker entry, locks and database effects
remain real. These tests do not certify every host's external process/job policy.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.test_m17_recovery_hardening import _setup_current
from tests.test_routine_backup import timestamp
from task_governance_tool import async_backup_capture as capture
from task_governance_tool import async_backup_repository as repository
from task_governance_tool import async_backup_state as state
from task_governance_tool.backup import managed_backup_lock
from task_governance_tool.backup_metadata_repository import configure_project_maintenance


_CHILD = r'''
import json, runpy, sys, time, traceback
from pathlib import Path
entry, repo, nonce, mode, entered, release = sys.argv[1:]
sys.path.insert(0, str(Path(entry).parent))
from task_governance_tool import async_backup_capture as capture
from task_governance_tool import async_backup as runtime
from task_governance_tool import async_backup_state as state

events = []
def trace_call(label, original):
    def traced(*args, **kwargs):
        try:
            result = original(*args, **kwargs)
        except Exception as error:
            frames = traceback.extract_tb(error.__traceback__)
            events.append({"stage": label, "error": type(error).__name__,
                           "code": getattr(error, "code", None),
                           "winerror": getattr(error, "winerror", None),
                           "frames": [[frame.name, frame.lineno] for frame in frames[-5:]]})
            Path(entered + ".trace").write_text(json.dumps(events[-12:]), encoding="ascii")
            raise
        return result
    return traced

for name in ("read_control", "copy_snapshot", "validate_snapshot", "commit_inventory"):
    setattr(capture.repository, name, trace_call(name, getattr(capture.repository, name)))
capture._reconcile = trace_call("reconcile", capture._reconcile)
runtime.write_record = trace_call("state_write", runtime.write_record)

def barrier():
    Path(entered).write_text("entered", encoding="ascii")
    deadline = time.monotonic() + 45
    while not Path(release).exists():
        if time.monotonic() > deadline:
            Path(entered + ".failure").write_text("barrier-timeout", encoding="ascii")
            raise RuntimeError("isolated barrier timed out")
        time.sleep(.02)

if mode == "prunecrash":
    original = capture._prune
    def paused_prune(target, control, artifacts):
        record = state.read_record(state.runtime_root(target))
        if record is not None and record["captured_at"] is not None:
            barrier()
        return original(target, control, artifacts)
    capture._prune = paused_prune
elif mode != "beforego":
    original = capture.run_backup
    def paused_run(*args, **kwargs):
        barrier()
        result = original(*args, **kwargs)
        Path(entered + ".result").write_text(json.dumps({"code": result.code,
            "captured": result.captured_at is not None}), encoding="ascii")
        return result
    capture.run_backup = paused_run
sys.argv = [entry, repo, nonce]
runpy.run_path(entry, run_name="__main__")
'''


_PARENT = r'''
import json, subprocess, sys, time
from pathlib import Path
scripts, repo, mode, entered, release, ready, child_bootstrap = sys.argv[1:]
sys.path.insert(0, scripts)
from task_governance_tool import async_backup as runtime
from task_governance_tool.state_resolver import resolve_project_state
resolution = resolve_project_state(skill_root=Path(scripts).parent, repo=Path(repo))
if resolution.target is None:
    raise RuntimeError("isolated project resolution failed")
target = resolution.target
native_popen = subprocess.Popen
children = []

def wait_for(path):
    deadline = time.monotonic() + 45
    while not Path(path).exists():
        if time.monotonic() > deadline:
            raise RuntimeError("isolated parent barrier timed out")
        time.sleep(.02)

class BeforeGo:
    def __init__(self, pipe):
        self.pipe = pipe
    def write(self, value):
        Path(ready).write_text("before-go", encoding="ascii")
        wait_for(release)
        return self.pipe.write(value)
    def close(self):
        return self.pipe.close()

def spawn(arguments, **options):
    # Preserve production detach/stdio flags, replacing only the child bootstrap
    # with a fixture wrapper that then executes the actual backup_worker entry.
    entry, project, nonce = arguments[-3:]
    replacement = [arguments[0], "-I", "-S", "-B", "-c", child_bootstrap,
                   entry, project, nonce, mode, entered, release]
    child = native_popen(replacement, **options)
    children.append(child)
    if mode == "beforego":
        child.stdin = BeforeGo(child.stdin)
    return child

if mode not in {"plain", "probe"}:
    runtime.subprocess.Popen = spawn
launch_started = time.monotonic()
result = runtime.launch_backup(target)
launch_seconds = time.monotonic() - launch_started
Path(ready).write_text(result.code, encoding="ascii")
if mode == "aftergo" and result.code == "started":
    wait_for(release)
elif mode == "prunecrash" and result.code == "started":
    wait_for(entered)
    children[0].terminate()
    children[0].wait(timeout=5)
    Path(ready).write_text("child-stopped-after-metadata", encoding="ascii")
from task_governance_tool import async_backup_state as state
try:
    recorded = state.read_record(state.runtime_root(target))
    attempt_state = None if recorded is None else recorded["state"]
except Exception as error:
    attempt_state = type(error).__name__
print(json.dumps({"code": result.code, "launch_seconds": launch_seconds,
                  "attempt_state": attempt_state}), flush=True)
'''


class AsyncBackupProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.install, self.target, _ = _setup_current(self.directory)
        self.entered = self.directory / "worker-entered"
        self.release = self.directory / "release-worker"
        self.ready = self.directory / "parent-ready"
        self.parents = []
        # Cleanup registered last runs before fixture removal, including on an
        # assertion failure while the child is paused inside a controlled phase.
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        self.release.touch(exist_ok=True)
        for parent in self.parents:
            if parent.poll() is None:
                parent.terminate()
            try:
                parent.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                parent.kill()
                parent.communicate(timeout=5)
        root = state.runtime_root(self.target)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if state.lock_owned(root / "worker.lock") is False:
                break
            time.sleep(.02)

    def parent(self, mode):
        marker = self.ready if not self.parents else self.directory / f"parent-ready-{len(self.parents)}"
        command = [sys.executable, "-I", "-S", "-B", "-c", _PARENT,
                   str(self.install.skill_root / "scripts"), str(self.install.project_root),
                   mode, str(self.entered), str(self.release), str(marker), _CHILD]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=str(self.directory), close_fds=True)
        self.parents.append(process)
        return process, marker

    def wait_for(self, predicate, message):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.02)
        self.fail(message)

    def parent_result(self, parent):
        stdout, stderr = parent.communicate(timeout=8)
        self.assertEqual(parent.returncode, 0, stderr.decode("utf-8", errors="replace"))
        self.last_parent_result = json.loads(stdout.decode("utf-8"))
        return self.last_parent_result["code"]

    def terminal(self):
        root = state.runtime_root(self.target)

        def finished():
            try:
                record = state.read_record(root)
            except (state.StatePathError, state.StorageError, OSError):
                # Atomic writer replacement may invalidate a concurrent read;
                # retry the test's observation, never a product side effect.
                return None
            if record is not None and record["state"] not in {"starting", "running"}:
                if state.lock_owned(root / "worker.lock") is False:
                    return record
            return None

        return self.wait_for(finished, "worker did not persist a terminal outcome and release ownership")

    def assert_succeeded(self):
        record = self.terminal()
        diagnostics = {path.suffix: path.read_text(encoding="ascii")
                       for path in self.directory.glob("worker-entered.*")}
        self.assertEqual(record["state"], "succeeded", (record, diagnostics))
        control = repository.read_control(self.target)
        self.assertEqual(control.maintenance.latest_backup_generation_id, record["generation_id"])
        self.assertEqual(control.maintenance.backup_last_success_at, record["published_at"])
        self.assertIsNotNone(record["captured_at"])
        self.assertEqual(len(control.generations), 1)
        artifacts = list(self.target.resolved_backups_path.glob("taskgov-backup-v1_*.sqlite"))
        self.assertEqual(len(artifacts), 1)
        repository.validate_snapshot(artifacts[0], self.target, control.schema_version)

    def public_task_add(self):
        # The real service owns startup; the public command only publishes an
        # opportunity. No CLI/worker monkeypatch or synchronous fallback.
        service = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B",
             str(self.install.skill_root / "scripts/review_wait_server.py"),
             "--repo", str(self.install.project_root),
             "--server", str(self.directory / "unused-host.mjs"),
             "--codex-home", str(self.directory / "unused-codex-home"),
             "--timezone", "UTC"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(self.directory), close_fds=True)
        self.parents.append(service)
        root = state.runtime_root(self.target)
        self.wait_for(lambda: state.lock_owned(root / "service.lock") is True
                      and (root / "service.json").is_file(), "service did not become available")
        command = [sys.executable, "-I", "-S", "-B", str(self.install.entrypoint),
                   "--repo", str(self.install.project_root), "--json", "task", "add",
                   "--title", "Isolated asynchronous backup opportunity"]
        parent = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=str(self.directory), close_fds=True)
        self.parents.append(parent)
        stdout, stderr = parent.communicate(timeout=15)
        self.assertEqual(parent.returncode, 0, stderr.decode("utf-8", errors="replace"))
        payload = json.loads(stdout.decode("utf-8"))
        self.assertTrue(payload["ok"], payload)
        self.assertFalse(any(item["code"].startswith("backup_request_")
                             for item in payload.get("warnings", [])), payload)
        self.assertEqual(payload["command"], "task.add")
        self.assertTrue(payload["data"]["task"]["task_id"].startswith("tg_task_"))
        return payload["data"]["task"]["task_id"]

    def test_public_cli_returns_business_result_and_default_worker_publishes(self):
        self.public_task_add()
        self.assert_succeeded()

    def test_state_publication_process_crash_has_only_fixed_recoverable_residue(self):
        root = state.runtime_root(self.target, create=True)
        control = repository.read_control(self.target)
        record = dict(v=1, attempt_id="a" * 32, state="starting", started_at=timestamp(0),
                      captured_at=None, published_at=None, generation_id=None,
                      project_id=self.target.project.project_id,
                      binding_path_hash=self.target.binding_path_hash,
                      binding_generation=self.target.binding_generation,
                      schema_version=control.schema_version,
                      database_identity=list(control.database_identity))
        # An old unreleased native temporary is an unknown artifact, not a
        # cleanup target. Repeated new-format recovery must leave it untouched.
        retained = root / "state.json~RF-fixture.TMP"
        retained.write_bytes(b"unknown-local-artifact")
        child_source = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool import async_backup_state as state
root, marker, phase = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
record = json.loads(sys.argv[5])
original = os.replace
def barrier():
    marker.write_text("entered", encoding="ascii")
    while True:
        time.sleep(.02)
def replace(source, destination):
    if phase == "before":
        barrier()
    original(source, destination)
    if phase == "after":
        barrier()
os.replace = replace
state.write_record(root, dict(record, state="running"))
'''
        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                state.write_record(root, record)
                marker = self.directory / f"replace-{phase}"
                child = subprocess.Popen(
                    [sys.executable, "-I", "-S", "-B", "-c", child_source,
                     str(self.install.skill_root / "scripts"), str(root), str(marker),
                     phase, json.dumps(record)], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                )
                self.parents.append(child)
                try:
                    self.wait_for(lambda: marker.exists() and marker.stat().st_size > 0,
                                  "child did not reach state publication boundary")
                    self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
                finally:
                    if child.poll() is None:
                        child.terminate()
                    child.communicate(timeout=5)
                current = state.read_record(root)
                self.assertEqual(current["state"], "starting" if phase == "before" else "running")
                self.assertEqual((root / ".state-next.json").exists(), phase == "before")
                state.write_record(root, dict(record, state="failed"))
                self.assertEqual(state.read_record(root)["state"], "failed")
                self.assertEqual({path.name for path in root.iterdir()},
                                 {"state.lock", "state.json", retained.name})
                self.assertEqual(retained.read_bytes(), b"unknown-local-artifact")

    def test_state_reader_writer_process_stress_has_no_missing_record_or_random_residue(self):
        root = state.runtime_root(self.target, create=True)
        control = repository.read_control(self.target)
        record = dict(v=1, attempt_id="a" * 32, state="starting", started_at=timestamp(0),
                      captured_at=None, published_at=None, generation_id=None,
                      project_id=self.target.project.project_id,
                      binding_path_hash=self.target.binding_path_hash,
                      binding_generation=self.target.binding_generation,
                      schema_version=control.schema_version,
                      database_identity=list(control.database_identity))
        state.write_record(root, record)
        child_source = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool import async_backup_state as state
root, record = Path(sys.argv[2]), json.loads(sys.argv[3])
failures = []
for index in range(100):
    try:
        state.write_record(root, dict(record, state="running" if index % 2 else "starting"))
    except Exception as error:
        failures.append({"error": type(error).__name__, "code": getattr(error, "code", None)})
print(json.dumps({"attempts": 100, "failures": failures}), flush=True)
'''
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", child_source,
             str(self.install.skill_root / "scripts"), str(root), json.dumps(record)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True,
        )
        self.parents.append(child)
        observed = 0
        deadline = time.monotonic() + 20
        while child.poll() is None and time.monotonic() < deadline:
            try:
                current = state.read_record(root)
            except state.StorageError as error:
                self.assertEqual(error.code, "backup_runtime_unavailable")
            else:
                self.assertIsNotNone(current, "successful replacement exposed a missing state file")
                self.assertEqual(current["attempt_id"], record["attempt_id"])
                self.assertIn(current["state"], {"starting", "running"})
                observed += 1
            time.sleep(.001)
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stderr.decode("utf-8", errors="replace"))
        self.assertEqual(json.loads(stdout), {"attempts": 100, "failures": []})
        self.assertGreater(observed, 0)
        self.assertEqual(state.read_record(root)["state"], "running")
        self.assertEqual({path.name for path in root.iterdir()}, {"state.lock", "state.json"})

    def test_explicit_setup_restores_real_async_backup_with_runtime_directory_present(self):
        task_id = self.public_task_add()
        self.assert_succeeded()
        root = state.runtime_root(self.target)
        retained_runtime = (root / "state.json").read_bytes()
        self.assertTrue(self.target.db_path.is_relative_to(self.directory))
        self.target.db_path.unlink()  # Only this test's isolated canonical DB.
        restored = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(self.install.entrypoint),
             "--repo", str(self.install.project_root), "--json", "setup"],
            stdin=subprocess.DEVNULL, capture_output=True, cwd=str(self.directory),
            timeout=30, check=False,
        )
        payload = json.loads(restored.stdout.decode("utf-8"))
        self.assertEqual(restored.returncode, 0, payload)
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(self.target.db_path.exists())
        self.assertEqual((root / "state.json").read_bytes(), retained_runtime)
        self.assertEqual(state.observe_runtime(self.target)["state"], "unknown")
        shown = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(self.install.entrypoint),
             "--repo", str(self.install.project_root), "--read-only", "--json",
             "task", "show", task_id],
            stdin=subprocess.DEVNULL, capture_output=True, cwd=str(self.directory),
            timeout=15, check=False,
        )
        result = json.loads(shown.stdout.decode("utf-8"))
        self.assertEqual(shown.returncode, 0, result)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["task"]["task_id"], task_id)
        self.assertEqual((root / "state.json").read_bytes(), retained_runtime)

    def test_worker_continues_after_launching_cli_normally_exits(self):
        parent, _ = self.parent("normal")
        self.assertEqual(self.parent_result(parent), "started")
        self.wait_for(self.entered.exists, "worker did not reach private-work barrier")
        self.assertIsNotNone(parent.poll())
        self.assertEqual(state.observe_runtime(self.target)["state"], "running")
        self.assertEqual(repository.read_control(self.target).generations, ())
        self.release.touch()
        self.assert_succeeded()

    def test_parent_forced_exit_before_go_has_no_backup_effect(self):
        parent, ready = self.parent("beforego")
        self.wait_for(lambda: ready.exists() and ready.stat().st_size,
                      "parent did not reach the pre-GO barrier")
        self.assertEqual(ready.read_text(encoding="ascii"), "before-go")
        self.assertEqual(state.observe_runtime(self.target)["state"], "running")
        parent.terminate()
        parent.communicate(timeout=5)
        record = self.terminal()
        self.assertEqual(record["state"], "failed")
        self.assertIsNone(record["captured_at"])
        self.assertEqual(repository.read_control(self.target).generations, ())
        self.assertEqual(list(self.target.resolved_backups_path.glob("*.sqlite")), [])
        self.assertFalse(self.entered.exists())

    def test_parent_forced_exit_after_go_preserves_worker_and_single_flight(self):
        parent, ready = self.parent("aftergo")
        self.wait_for(lambda: ready.exists() and ready.stat().st_size,
                      "parent did not complete GO handshake")
        self.assertEqual(ready.read_text(encoding="ascii"), "started")
        self.wait_for(self.entered.exists, "worker did not reach private-work barrier")
        record = state.read_record(state.runtime_root(self.target))
        second, _ = self.parent("probe")
        self.assertEqual(self.parent_result(second), "running")
        self.assertEqual(state.read_record(state.runtime_root(self.target))["attempt_id"], record["attempt_id"])
        parent.terminate()
        parent.communicate(timeout=5)
        self.assertEqual(state.observe_runtime(self.target)["state"], "running")
        self.release.touch()
        self.assert_succeeded()

    def test_interruption_after_success_metadata_recovers_pruning_when_not_due(self):
        configure_project_maintenance(self.target, requested_interval_minutes=30,
                                      requested_generations=1, enabled_at=timestamp(0))
        with managed_backup_lock(self.target):
            initial = capture.run_backup(self.target, observed_at=timestamp(0))
        self.assertEqual(initial.code, "succeeded")
        parent, _ = self.parent("prunecrash")
        self.assertEqual(self.parent_result(parent), "started")
        self.assertEqual(state.observe_runtime(self.target)["state"], "interrupted")
        before = repository.read_control(self.target)
        self.assertEqual(len(before.generations), 2)
        self.assertEqual(before.maintenance.applied_backup_generations, 1)
        recovery, _ = self.parent("plain")
        self.assertEqual(self.parent_result(recovery), "started", self.last_parent_result)
        record = self.terminal()
        self.assertEqual(record["state"], "idle")
        after = repository.read_control(self.target)
        self.assertEqual(len(after.generations), 1)
        self.assertEqual(after.maintenance.latest_backup_generation_id,
                         before.maintenance.latest_backup_generation_id)
        # A successful reconciliation must not schedule an endless recovery loop.
        third, _ = self.parent("plain")
        self.assertEqual(self.parent_result(third), "current")
        self.assertEqual(len(list(self.target.resolved_backups_path.glob("*.sqlite"))), 1)


if __name__ == "__main__":
    unittest.main()
