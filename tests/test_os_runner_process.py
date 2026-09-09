"""Actual POSIX process checks through the private Runner adapter only."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import (  # noqa: E402
    _verification_runner_process_posix as process_posix,
    verification_runner_lifecycle as lifecycle,
    verification_runner_process as process,
    verification_runner_runtime as runtime,
)
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION,
    RUNNER_POSIX_POLICY_DIGEST,
)
from tests.test_os_runner_preparation import ATTEMPT_ID, POSIX_HOST, _attempt  # noqa: E402


@unittest.skipUnless(POSIX_HOST, "requires an actual Linux/macOS host")
class PosixRunnerProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="taskgov-posix-process-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.paths, self.exact = _attempt(self.root)
        self.executable = runtime.observe_fixed_package_runtime(
            self.exact.target, self.exact.scratch,
        )
        self.environment = process.build_clean_environment(self.exact.scratch)

    def tearDown(self):
        cleanup = lifecycle.cleanup_attempt_tree(self.paths, ATTEMPT_ID)
        self.assertEqual(cleanup.state, "absent")
        self.assertFalse(self.exact.root.exists())
        self.assertFalse(self.exact.quarantine.exists())

    def _script(self, name: str, source: str) -> None:
        destination = self.exact.target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(textwrap.dedent(source), encoding="utf-8")

    def _step(self, ordinal: int = 1, **changes):
        values = dict(
            ordinal=ordinal, step_id=f"step-{ordinal}", mode="script",
            entrypoint="check.py", argv=(), cwd=".", shell=False,
            path_lookup=False, timeout_seconds=10, cpu_seconds=5,
            memory_mib=None, process_limit=None, output_byte_limit=1_048_576,
        )
        values.update(changes)
        return process.RunnerProcessStepV1(**values)

    def _request(self, *steps, signal=None):
        return process.RunnerProcessRequestV1(
            version=RUNNER_CONTRACT_VERSION, attempt_id=ATTEMPT_ID,
            executable=self.executable, materialized_root=self.exact.target,
            scratch_root=self.exact.scratch, clean_environment=self.environment,
            steps=steps or (self._step(),),
            cancel_signal=signal or process.RunnerCancelSignal(),
            runner_policy_digest=RUNNER_POSIX_POLICY_DIGEST,
        )

    def _assert_result(self, result, *, outcome="pass", reason=None, launched=True):
        self.assertIs(type(result), process.RunnerProcessResultV1)
        self.assertEqual(result.attempt_id, ATTEMPT_ID)
        self.assertEqual(result.runner_policy_digest, RUNNER_POSIX_POLICY_DIGEST)
        self.assertEqual(
            (result.outcome, result.reason, result.launch_state),
            (outcome, reason, "launched" if launched else "no_launch"),
        )
        self.assertTrue(result.process_zero)
        self.assertTrue(result.handles_closed)
        self.assertTrue(result.raw_output_discarded)
        self.assertIsNone(result.peak_job_memory_bytes)
        self.assertIsNone(result.total_process_count)
        if outcome == "pass":
            self.assertIs(type(result.cpu_time_ms), int)
        if result.cpu_time_ms is not None:
            self.assertGreaterEqual(result.cpu_time_ms, 0)
        for step in result.steps:
            self.assertEqual(step.runner_policy_digest, RUNNER_POSIX_POLICY_DIGEST)
            self.assertIsNone(step.peak_job_memory_bytes)
            self.assertIsNone(step.total_process_count)
        serialized = json.dumps(dataclasses.asdict(result))
        for forbidden in (
            str(self.root), str(self.executable), "RAW_STDOUT_UNRETAINED",
            "RAW_STDERR_UNRETAINED", "UNRETAINED_LITERAL_ARGUMENT",
        ):
            self.assertNotIn(forbidden, serialized)
        # Private-tree removal remains with lifecycle, after process proofs.
        self.assertTrue(self.exact.target.is_dir())
        self.assertTrue(self.exact.scratch.is_dir())

    def _cancel_when_ready(self, ready: Path, request):
        stopped = threading.Event()
        observed_ready = []

        def cancel():
            while not stopped.wait(0.01):
                if ready.exists():
                    observed_ready.append(True)
                    request.cancel_signal.request()
                    return

        watcher = threading.Thread(target=cancel, daemon=True)
        watcher.start()
        try:
            result = process_posix.run_process_request(request)
        finally:
            stopped.set()
            watcher.join(timeout=2)
        self.assertFalse(watcher.is_alive())
        self.assertEqual(observed_ready, [True])
        return result

    def test_script_and_module_keep_fixed_argv_cwd_environment_and_limits(self):
        for mode, entrypoint in (("script", "check.py"), ("module", "checks.run")):
            with self.subTest(mode=mode):
                working = self.exact.target / "working"
                working.mkdir(exist_ok=True)
                arguments = ("", "with space", 'quote"inside', "日本語", "UNRETAINED_LITERAL_ARGUMENT")
                expected_env = dict(self.environment)
                source = f"""
                    import os, resource, sys
                    assert sys.argv[1:] == {list(arguments)!r}
                    assert os.getcwd() == {str(working)!r}
                    for key, value in {expected_env!r}.items():
                        assert os.environ[key] == value
                    assert 'PATH' not in os.environ
                    assert 'PYTHONPATH' not in os.environ
                    assert 'RUNNER_TEST_SECRET' not in os.environ
                    assert sys.flags.isolated == 1
                    assert sys.flags.no_site == 1
                    assert sys.flags.dont_write_bytecode == 1
                    assert sys.flags.utf8_mode == 1
                    assert resource.getrlimit(resource.RLIMIT_CPU) == (5, 6)
                    assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
                    assert os.getsid(0) == os.getpid()
                    assert os.getpgrp() == os.getpid()
                    print('RAW_STDOUT_UNRETAINED')
                    print('RAW_STDERR_UNRETAINED', file=sys.stderr)
                """
                if mode == "module":
                    self._script("checks/__init__.py", "")
                    self._script("checks/run.py", source)
                else:
                    self._script(entrypoint, source)
                request = self._request(self._step(mode=mode, entrypoint=entrypoint, argv=arguments, cwd="working"))
                result = process_posix.run_process_request(request)
                self._assert_result(result)
                self.assertEqual(len(result.steps), 1)
                self.assertEqual(result.cpu_time_ms, result.steps[0].cpu_time_ms)
                self.assertFalse(any(self.exact.target.rglob("__pycache__")))

    def test_inheritable_parent_descriptor_is_not_passed_to_target(self):
        import fcntl

        fixture = self.root / "private-parent-file"
        fixture.write_bytes(b"parent descriptor must stay private")
        with fixture.open("rb") as stream:
            inherited = fcntl.fcntl(stream.fileno(), fcntl.F_DUPFD, 100)
            try:
                os.set_inheritable(inherited, True)
                identity = os.fstat(inherited)
                self._script("check.py", f"""
                    import os
                    try:
                        observed = os.fstat({inherited})
                    except OSError:
                        pass
                    else:
                        assert (observed.st_dev, observed.st_ino) != {(identity.st_dev, identity.st_ino)!r}
                """)
                self._assert_result(process_posix.run_process_request(self._request()))
            finally:
                os.close(inherited)

    def test_nonzero_and_output_cap_keep_only_closed_results(self):
        self._script("check.py", """
            import sys
            print('RAW_STDOUT_UNRETAINED')
            print('RAW_STDERR_UNRETAINED', file=sys.stderr)
            raise SystemExit(17)
        """)
        failed = process_posix.run_process_request(self._request())
        self._assert_result(failed, outcome="fail", reason="step_nonzero")
        self.assertEqual(failed.failed_step_ordinal, 1)

        self._script("check.py", """
            import os
            for _ in range(32):
                os.write(1, b'o' * 65536)
                os.write(2, b'e' * 65536)
        """)
        capped = process_posix.run_process_request(self._request())
        self._assert_result(capped, outcome="output_rejected", reason="output_limit")
        self.assertEqual(capped.failed_step_ordinal, 1)
        self.assertEqual({item.name for item in self.exact.target.iterdir()}, {"check.py"})
        self.assertTrue(all(not tuple(item.iterdir()) for item in self.exact.scratch.iterdir()))

    def test_actual_sigxcpu_is_resource_exceeded(self):
        self._script("check.py", """
            import signal
            signal.signal(signal.SIGXCPU, signal.SIG_DFL)
            while True:
                pass
        """)
        result = process_posix.run_process_request(
            self._request(self._step(cpu_seconds=1, timeout_seconds=20)),
        )
        self._assert_result(result, outcome="resource_exceeded", reason="cpu_limit")
        self.assertEqual(result.failed_step_ordinal, 1)

    def test_wall_timeout_stops_a_waiting_root(self):
        self._script("check.py", """
            import signal
            while True:
                signal.pause()
        """)
        result = process_posix.run_process_request(self._request(self._step(timeout_seconds=1)))
        self._assert_result(result, outcome="timeout", reason="timeout")
        self.assertEqual(result.failed_step_ordinal, 1)

    def test_cancellation_stops_a_ready_root(self):
        self._script("check.py", """
            import signal
            from pathlib import Path
            Path('ready').write_text('ready', encoding='utf-8')
            while True:
                signal.pause()
        """)
        result = self._cancel_when_ready(self.exact.target / "ready", self._request())
        self._assert_result(result, outcome="cancelled", reason="cancelled")

    def test_parent_waits_and_reaps_ordinary_child(self):
        self._script("check.py", """
            import os, resource
            from pathlib import Path
            child = os.fork()
            if child == 0:
                assert resource.getrlimit(resource.RLIMIT_CPU) == (5, 6)
                Path('child-finished').write_text('finished', encoding='utf-8')
                os._exit(0)
            waited, status = os.waitpid(child, 0)
            assert waited == child and os.waitstatus_to_exitcode(status) == 0
        """)
        result = process_posix.run_process_request(self._request())
        self._assert_result(result)
        self.assertEqual((self.exact.target / "child-finished").read_text(), "finished")

    def test_root_success_waits_for_ordinary_child_to_finish(self):
        self._script("check.py", """
            import os, time
            from pathlib import Path
            parent = os.getpid()
            child = os.fork()
            if child == 0:
                while os.getppid() == parent:
                    time.sleep(0.001)
                Path('child-finished').write_text('finished', encoding='utf-8')
                os._exit(0)
            os._exit(0)
        """)
        result = process_posix.run_process_request(self._request())
        self._assert_result(result)
        self.assertEqual((self.exact.target / "child-finished").read_text(), "finished")

    def test_cancellation_stops_the_ordinary_group_and_reaps_child(self):
        self._script("check.py", """
            import json, os, signal
            from pathlib import Path
            reader, writer = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(reader)
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                os.write(writer, b'ready')
                os.close(writer)
                while True:
                    signal.pause()
            os.close(writer)
            assert os.read(reader, 5) == b'ready'
            os.close(reader)
            def retire(_signal, _frame):
                os.waitpid(child, 0)
                os._exit(0)
            signal.signal(signal.SIGTERM, retire)
            pending = Path('group-ready.pending')
            pending.write_text(json.dumps([os.getpid(), child]), encoding='utf-8')
            pending.replace('group-ready')
            while True:
                signal.pause()
        """)
        ready = self.exact.target / "group-ready"
        result = self._cancel_when_ready(ready, self._request())
        self._assert_result(result, outcome="cancelled", reason="cancelled")
        for pid in json.loads(ready.read_text()):
            with self.subTest(pid=pid), self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_steps_stop_on_first_failure_and_aggregate_only_executed_prefix(self):
        self._script("first.py", "from pathlib import Path\nPath('first-ran').touch()\n")
        self._script("second.py", "raise SystemExit(4)\n")
        self._script("third.py", "from pathlib import Path\nPath('third-ran').touch()\n")
        result = process_posix.run_process_request(self._request(*(
            self._step(ordinal, entrypoint=name)
            for ordinal, name in enumerate(("first.py", "second.py", "third.py"), 1)
        )))
        self._assert_result(result, outcome="fail", reason="step_nonzero")
        self.assertEqual(result.failed_step_ordinal, 2)
        self.assertEqual(tuple(step.ordinal for step in result.steps), (1, 2))
        self.assertEqual(tuple(step.outcome for step in result.steps), ("pass", "fail"))
        self.assertTrue((self.exact.target / "first-ran").exists())
        self.assertFalse((self.exact.target / "third-ran").exists())
        if all(step.cpu_time_ms is not None for step in result.steps):
            self.assertEqual(result.cpu_time_ms, sum(step.cpu_time_ms for step in result.steps))

    def test_prelaunch_cancellation_acquires_no_process_result(self):
        self._script("check.py", "from pathlib import Path\nPath('must-not-run').touch()\n")
        request = self._request(signal=process.RunnerCancelSignal(True))
        result = process_posix.run_process_request(request)
        self._assert_result(result, outcome="blocked_prelaunch", reason="cancelled", launched=False)
        self.assertEqual(result.steps, ())
        self.assertIsNone(result.failed_step_ordinal)
        self.assertIsNone(result.cpu_time_ms)
        self.assertFalse((self.exact.target / "must-not-run").exists())


if __name__ == "__main__":
    unittest.main()
