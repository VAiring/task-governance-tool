from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _verification_runner_win32 as win32  # noqa: E402


class RunnerWin32PureTests(unittest.TestCase):
    def test_limit_validation_is_closed(self):
        self.assertEqual(
            win32.JobLimits(900, 900, 2048, 32),
            win32.JobLimits(900, 900, 2048, 32),
        )
        for values in ((0, 1, 64, 1), (1, 901, 64, 1), (1, 1, 63, 1), (1, 1, 64, 33)):
            with self.subTest(values=values), self.assertRaises(win32.RunnerWin32Error):
                win32.JobLimits(*values)


@unittest.skipUnless(os.name == "nt", "native Windows Runner provider")
class RunnerWin32NativeTests(unittest.TestCase):
    def test_exact_amd64_abi_job_and_stdio_handles(self):
        self.assertEqual(
            win32.abi_layout(),
            {
                "pointer_size": 8,
                "startupinfo_size": 104,
                "startupinfoex_size": 112,
                "process_information_size": 24,
                "job_basic_limit_size": 64,
                "job_extended_limit_size": 144,
                "job_accounting_size": 48,
                "job_limit_violation_size": 80,
            },
        )
        job = win32.create_job(win32.JobLimits(5, 5, 128, 2))
        try:
            job.prove_configuration()
            accounting = job.accounting()
            self.assertEqual(accounting.total_processes, 0)
            self.assertIsNone(job.limit_violation_reason(accounting))
        finally:
            job.close()

        pipes = win32.create_stdio_pipes()
        try:
            pipes.prove_before_create()
            self.assertEqual(len(set(pipes.inherited_values)), 3)
        finally:
            pipes.close_child_ends()
            pipes.close_parent_ends()
