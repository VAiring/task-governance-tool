from __future__ import annotations

import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _verification_runner_win32 as win32  # noqa: E402
from task_governance_tool import verification_runner_process as process  # noqa: E402
from task_governance_tool.verification_runner import (  # noqa: E402
    RUNNER_CONTRACT_VERSION,
    RUNNER_EXECUTABLE_ID,
    RUNNER_IMPLEMENTATION_VERSION,
    RUNNER_MAX_OUTPUT_BYTES,
)


def _step(step_id: str = "unit") -> process.ProcessStep:
    return process.ProcessStep(
        step_id,
        "script",
        "checks/run.py",
        ("", "plain", "with space", 'quote"inside', "trail\\"),
        ".",
        win32.JobLimits(30, 20, 128, 2),
    )


class RunnerProcessPureTests(unittest.TestCase):
    def test_process_boundary_has_no_business_freshness_callback(self):
        parameters = inspect.signature(process.run_process_steps).parameters
        self.assertNotIn("basis_is_current", parameters)
        self.assertEqual(
            set(parameters),
            {
                "steps",
                "runtime_executable",
                "target_root",
                "scratch_root",
                "windows_directory",
                "cancel_requested",
            },
        )

    def test_central_identity_and_fixed_bootstrap(self):
        self.assertEqual(process.RUNNER_CONTRACT_VERSION, RUNNER_CONTRACT_VERSION)
        self.assertEqual(process.RUNNER_IMPLEMENTATION_VERSION, RUNNER_IMPLEMENTATION_VERSION)
        self.assertEqual(process.EXECUTABLE_ID, RUNNER_EXECUTABLE_ID)
        self.assertEqual(process.MAX_OUTPUT_BYTES, RUNNER_MAX_OUTPUT_BYTES)
        self.assertIn("runpy.run_path", process.FIXED_BOOTSTRAP)
        self.assertIn("runpy.run_module", process.FIXED_BOOTSTRAP)
        self.assertIn("find_spec", process.FIXED_BOOTSTRAP)
        self.assertIn("_under(_root,_spec.origin)", process.FIXED_BOOTSTRAP)
        self.assertNotIn("subprocess", process.FIXED_BOOTSTRAP)
        self.assertNotIn("shell", process.FIXED_BOOTSTRAP)

    @unittest.skipUnless(os.name == "nt", "CommandLineToArgvW")
    def test_windows_quote_roundtrip_and_limit(self):
        fixtures = (
            ("program.exe",),
            ("program.exe", "", "plain", "with space", 'quote"inside', "trail\\"),
            (r"C:\private runtime\python.exe", "-c", "line1\nline2", "日本語"),
        )
        for argv in fixtures:
            with self.subTest(argv=argv):
                serialized = process.quote_windows_argv(argv)
                self.assertEqual(win32.command_line_to_argv(serialized), argv)
        with self.assertRaises(process.RunnerProcessError):
            process.quote_windows_argv(("p.exe", "x" * 24_577))

    def test_step_validation_is_strict(self):
        self.assertEqual(_step().mode, "script")
        invalid = (
            dict(step_id="UPPER"),
            dict(entrypoint="../run.py"),
            dict(entrypoint="checks/RUN.PY"),
            dict(argv=("bad\n",)),
            dict(cwd="../outside"),
        )
        base = dict(
            step_id="unit",
            mode="script",
            entrypoint="checks/run.py",
            argv=(),
            cwd=".",
            limits=win32.JobLimits(30, 20, 128, 2),
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(process.RunnerProcessError):
                process.ProcessStep(**(base | changes))

    def test_clean_environment_is_exact_and_drops_ambient_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary) / "scratch"
            windows = Path(temporary) / "Windows"
            scratch.mkdir()
            windows.mkdir()
            for child in ("tmp", "home", "local", "roaming"):
                (scratch / child).mkdir()
            with patch.dict(
                os.environ,
                {
                    "PATH": r"C:\secret",
                    "HTTP_PROXY": "http://secret",
                    "TOKEN": "secret",
                    "PYTHONPATH": r"C:\ambient",
                },
                clear=False,
            ), patch.object(
                process._win32, "verified_windows_directory", return_value=windows
            ):
                environment = process.build_clean_environment(windows, scratch)
            keys = {key for key, _value in environment.entries}
            self.assertEqual(
                keys,
                {
                    "SystemRoot",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "HOME",
                    "USERPROFILE",
                    "LOCALAPPDATA",
                    "APPDATA",
                    "PYTHONDONTWRITEBYTECODE",
                    "PYTHONNOUSERSITE",
                    "PYTHONUTF8",
                },
            )
            self.assertTrue(keys.isdisjoint({"PATH", "HTTP_PROXY", "TOKEN", "PYTHONPATH"}))

    def test_python_argv_is_fixed_and_target_contained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            target = root / "target"
            runtime.mkdir()
            target.mkdir()
            executable = runtime / "python.exe"
            executable.write_bytes(b"fixture")
            (target / "checks").mkdir()
            (target / "checks" / "run.py").write_text("pass\n", encoding="utf-8")
            argv, cwd = process.build_python_argv(executable, target, _step())
            self.assertEqual(argv[1:5], ("-I", "-B", "-X", "utf8"))
            self.assertEqual(argv[5], "-c")
            self.assertEqual(argv[6], process.FIXED_BOOTSTRAP)
            self.assertEqual(cwd, target.resolve())
            self.assertNotIn("-m", argv)

    @unittest.skipUnless(
        Path(sys.executable).name.casefold() == "python.exe",
        "fixed Runner bootstrap requires the Windows CPython executable",
    )
    def test_module_parent_observes_exact_argv_before_find_spec(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "target"
            package = target / "pkg"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                "import sys\n"
                "if sys.argv != ['pkg.check', 'expected']:\n"
                "    raise SystemExit(0)\n",
                encoding="utf-8",
            )
            (package / "check.py").write_text(
                "raise SystemExit(9)\n",
                encoding="utf-8",
            )
            step = process.ProcessStep(
                "module",
                "module",
                "pkg.check",
                ("expected",),
                ".",
                win32.JobLimits(30, 20, 128, 2),
            )
            argv, cwd = process.build_python_argv(Path(sys.executable), target, step)
            completed = subprocess.run(
                argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        self.assertEqual(completed.returncode, 9)

    def test_multi_step_aggregation_and_stop_on_nonpass(self):
        pass_one = process.StepProcessResult(
            1,
            "pass",
            None,
            "launched",
            win32.JobAccounting(15_001, 100, 2, 0),
        )
        failure = process.StepProcessResult(
            2,
            "fail",
            "step_nonzero",
            "launched",
            win32.JobAccounting(25_001, 90, 3, 0),
        )
        calls = []

        def execute(**kwargs):
            calls.append(kwargs["ordinal"])
            return pass_one if kwargs["ordinal"] == 1 else failure

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            scratch = root / "scratch"
            windows = root / "Windows"
            target = root / "target"
            runtime = root / "runtime"
            for directory in (scratch, windows, target, runtime):
                directory.mkdir()
            for child in ("tmp", "home", "local", "roaming"):
                (scratch / child).mkdir()
            executable = runtime / "python.exe"
            executable.write_bytes(b"fixture")
            with patch.object(
                process._win32, "verified_windows_directory", return_value=windows
            ), patch.object(process, "_execute_step", side_effect=execute):
                result = process.run_process_steps(
                    steps=(_step("one"), _step("two"), _step("three")),
                    runtime_executable=executable,
                    target_root=target,
                    scratch_root=scratch,
                    windows_directory=windows,
                )
        self.assertEqual(calls, [1, 2])
        self.assertEqual(result.outcome, "fail")
        self.assertEqual(result.failed_step_ordinal, 2)
        self.assertEqual(result.cpu_time_ms, 4)
        self.assertEqual(result.peak_job_memory_bytes, 100)
        self.assertEqual(result.total_process_count, 5)

    def test_first_and_later_create_failure_matrix_is_exact(self):
        first_no_launch = process.StepProcessResult(
            1, "blocked_prelaunch", "process_create_failed", "no_launch", None
        )
        prior_pass = process.StepProcessResult(
            1,
            "pass",
            None,
            "launched",
            win32.JobAccounting(20_000, 128, 1, 0),
        )
        later_no_launch = process.StepProcessResult(
            2, "blocked_prelaunch", "process_create_failed", "no_launch", None
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("scratch", "Windows", "target", "runtime"):
                (root / name).mkdir()
            for child in ("tmp", "home", "local", "roaming"):
                (root / "scratch" / child).mkdir()
            executable = root / "runtime" / "python.exe"
            executable.write_bytes(b"fixture")
            arguments = dict(
                runtime_executable=executable,
                target_root=root / "target",
                scratch_root=root / "scratch",
                windows_directory=root / "Windows",
            )
            with patch.object(
                process._win32,
                "verified_windows_directory",
                return_value=root / "Windows",
            ), patch.object(process, "_execute_step", return_value=first_no_launch):
                first = process.run_process_steps(steps=(_step("one"),), **arguments)
            with patch.object(
                process._win32,
                "verified_windows_directory",
                return_value=root / "Windows",
            ), patch.object(
                process,
                "_execute_step",
                side_effect=(prior_pass, later_no_launch),
            ):
                later = process.run_process_steps(
                    steps=(_step("one"), _step("two")), **arguments
                )
        self.assertEqual(first.launch_state, "no_launch")
        self.assertEqual(first.outcome, "blocked_prelaunch")
        self.assertIsNone(first.failed_step_ordinal)
        self.assertEqual(first.steps, ())
        self.assertEqual(later.launch_state, "launched")
        self.assertEqual(later.outcome, "process_error")
        self.assertEqual(later.reason, "process_create_failed")
        self.assertEqual(later.failed_step_ordinal, 2)
        self.assertEqual(len(later.steps), 2)
        self.assertEqual(later.steps[-1].reason, "process_create_failed")
        self.assertEqual(later.cpu_time_ms, 2)
        self.assertEqual(later.total_process_count, 1)

    def test_later_no_launch_result_uses_exact_terminal_classification(self):
        prior_pass = process.StepProcessResult(
            1,
            "pass",
            None,
            "launched",
            win32.JobAccounting(20_000, 128, 1, 0),
        )
        cases = (
            (
                process.StepProcessResult(
                    2,
                    "blocked_prelaunch",
                    "sandbox_setup_failed",
                    "no_launch",
                    None,
                ),
                "process_error",
                "process_create_failed",
                2,
                (2, 128, 1),
                2,
            ),
            (
                process.StepProcessResult(
                    2,
                    "sandbox_cleanup_failed",
                    "sandbox_cleanup_failed",
                    "no_launch",
                    None,
                ),
                "sandbox_cleanup_failed",
                "sandbox_cleanup_failed",
                None,
                (None, None, None),
                1,
            ),
            (
                process.StepProcessResult(
                    2,
                    "controller_interrupted",
                    "controller_interrupted",
                    "no_launch",
                    None,
                ),
                "controller_interrupted",
                "controller_interrupted",
                None,
                (None, None, None),
                1,
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("scratch", "Windows", "target", "runtime"):
                (root / name).mkdir()
            for child in ("tmp", "home", "local", "roaming"):
                (root / "scratch" / child).mkdir()
            executable = root / "runtime" / "python.exe"
            executable.write_bytes(b"fixture")
            arguments = dict(
                steps=(_step("one"), _step("two")),
                runtime_executable=executable,
                target_root=root / "target",
                scratch_root=root / "scratch",
                windows_directory=root / "Windows",
            )
            for (
                later_result,
                expected_outcome,
                expected_reason,
                expected_ordinal,
                expected_resources,
                expected_step_count,
            ) in cases:
                with self.subTest(outcome=later_result.outcome), patch.object(
                    process._win32,
                    "verified_windows_directory",
                    return_value=root / "Windows",
                ), patch.object(
                    process,
                    "_execute_step",
                    side_effect=(prior_pass, later_result),
                ):
                    result = process.run_process_steps(**arguments)
                self.assertEqual(result.launch_state, "launched")
                self.assertEqual(result.outcome, expected_outcome)
                self.assertEqual(result.reason, expected_reason)
                self.assertEqual(result.failed_step_ordinal, expected_ordinal)
                self.assertEqual(
                    (
                        result.cpu_time_ms,
                        result.peak_job_memory_bytes,
                        result.total_process_count,
                    ),
                    expected_resources,
                )
                self.assertEqual(len(result.steps), expected_step_count)
                self.assertEqual(result.steps[0], prior_pass)

    def test_between_job_cancel_starts_no_next_job(self):
        first = process.StepProcessResult(
            1,
            "pass",
            None,
            "launched",
            win32.JobAccounting(30_000, 256, 2, 0),
        )
        observations = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("scratch", "Windows", "target", "runtime"):
                (root / name).mkdir()
            for child in ("tmp", "home", "local", "roaming"):
                (root / "scratch" / child).mkdir()
            executable = root / "runtime" / "python.exe"
            executable.write_bytes(b"fixture")

            cancel_calls = {"count": 0}

            def cancel():
                cancel_calls["count"] += 1
                observations.append(("cancel", cancel_calls["count"]))
                return cancel_calls["count"] == 2

            with patch.object(
                process._win32,
                "verified_windows_directory",
                return_value=root / "Windows",
            ), patch.object(process, "_execute_step", return_value=first) as execute:
                result = process.run_process_steps(
                    steps=(_step("one"), _step("two")),
                    runtime_executable=executable,
                    target_root=root / "target",
                    scratch_root=root / "scratch",
                    windows_directory=root / "Windows",
                    cancel_requested=cancel,
                )
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(
            observations,
            [("cancel", 1), ("cancel", 2)],
        )
        self.assertEqual(result.outcome, "cancelled")
        self.assertEqual(result.launch_state, "launched")
        self.assertIsNone(result.failed_step_ordinal)
        self.assertEqual(result.cpu_time_ms, 3)
        self.assertEqual(result.total_process_count, 2)

    def test_between_job_invalid_cancel_callback_is_controller_uncertainty(self):
        first = process.StepProcessResult(
            1,
            "pass",
            None,
            "launched",
            win32.JobAccounting(30_000, 256, 2, 0),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("scratch", "Windows", "target", "runtime"):
                (root / name).mkdir()
            for child in ("tmp", "home", "local", "roaming"):
                (root / "scratch" / child).mkdir()
            executable = root / "runtime" / "python.exe"
            executable.write_bytes(b"fixture")
            calls = {"count": 0}

            def cancel():
                calls["count"] += 1
                return False if calls["count"] == 1 else None

            with patch.object(
                process._win32,
                "verified_windows_directory",
                return_value=root / "Windows",
            ), patch.object(process, "_execute_step", return_value=first) as execute:
                result = process.run_process_steps(
                    steps=(_step("one"), _step("two")),
                    runtime_executable=executable,
                    target_root=root / "target",
                    scratch_root=root / "scratch",
                    windows_directory=root / "Windows",
                    cancel_requested=cancel,
                )

        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result.outcome, "controller_interrupted")
        self.assertEqual(result.reason, "controller_interrupted")
        self.assertEqual(result.launch_state, "launched")
        self.assertIsNone(result.failed_step_ordinal)

    def test_first_job_cancel_is_closed_before_create(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("scratch", "Windows", "target", "runtime"):
                (root / name).mkdir()
            for child in ("tmp", "home", "local", "roaming"):
                (root / "scratch" / child).mkdir()
            executable = root / "runtime" / "python.exe"
            executable.write_bytes(b"fixture")

            with patch.object(
                process._win32,
                "verified_windows_directory",
                return_value=root / "Windows",
            ), patch.object(process, "_execute_step") as execute:
                result = process.run_process_steps(
                    steps=(_step("one"),),
                    runtime_executable=executable,
                    target_root=root / "target",
                    scratch_root=root / "scratch",
                    windows_directory=root / "Windows",
                    cancel_requested=lambda: True,
                )
        execute.assert_not_called()
        self.assertEqual(result.outcome, "blocked_prelaunch")
        self.assertEqual(result.reason, "cancelled")
        self.assertEqual(result.launch_state, "no_launch")
        self.assertIsNone(result.failed_step_ordinal)
        self.assertEqual(result.steps, ())
