import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.m20_trial_observer as observer


ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self, *, stdout=b"", stderr=b"", returncode=0, timeout=False):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.timeout = timeout
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("safe", timeout)
        return -9 if self.killed else self.returncode

    def kill(self):
        self.killed = True


class FakeProcessTree:
    def __init__(self, process):
        self.process = process

    def terminate(self):
        self.process.kill()
        self.process.wait()

    def close(self):
        return None


class M20TrialObserverTests(unittest.TestCase):
    def setUp(self):
        self.real_new_process_tree = observer._new_process_tree
        self.process_tree_patch = patch.object(
            observer,
            "_new_process_tree",
            side_effect=self.new_process_tree_for_test,
        )
        self.process_tree_patch.start()
        self.addCleanup(self.process_tree_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        script = self.root / "task-governance-tool" / "scripts" / "taskgov.py"
        script.parent.mkdir(parents=True)
        script.write_text("raise SystemExit(0)\n", encoding="utf-8")
        tools = self.root / "tools"
        tools.mkdir()
        for name in ("release_contract.py", "test_lanes.py"):
            (tools / name).write_text("raise SystemExit(0)\n", encoding="utf-8")
        self.config_path = self.root / "observer-config.json"
        self.log_path = self.root / "observer-log.json"
        self.task_id = "tg_task_0123456789abcdef"
        self.config = {
            "schema": observer.CONFIG_SCHEMA,
            "unit": "M20.3",
            "scenario_id": "vp_cli_contract",
            "trial_id": "vp_cli_contract.baseline.01",
            "arm": "baseline",
            "task_slots": [{"task_slot": "primary", "task_id": self.task_id}],
            "verification_labels": [
                {
                    "label": "focused_cli",
                    "kind": "focused",
                    "argv": ["-m", "unittest", "tests.test_cli_envelope"],
                },
                {
                    "label": "release_contract",
                    "kind": "focused",
                    "argv": ["-B", "tools/release_contract.py", "--repo", "."],
                },
                {
                    "label": "all",
                    "kind": "all",
                    "argv": ["tools/test_lanes.py", "--repo", ".", "--lane", "all"],
                },
            ],
        }
        self.config_path.write_bytes(observer.canonical_json_bytes(self.config))

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        return observer.TrialObserver(self.root, "observer-config.json", "observer-log.json")

    def new_process_tree_for_test(self, process):
        if isinstance(process, FakeProcess):
            return FakeProcessTree(process)
        return self.real_new_process_tree(process)

    @staticmethod
    def envelope(*, ok=True, errors=None, task_data=None):
        return observer.canonical_json_bytes(
            {
                "ok": ok,
                "command": "task.show",
                "project_id": "project_fixture",
                "data": (
                    {"task": {"status": "in_progress"}}
                    if task_data is None
                    else task_data
                ),
                "warnings": [],
                "errors": [] if errors is None else errors,
            }
        ) + b"\n"

    def assert_error(self, call, code):
        with self.assertRaises(observer.TrialObserverError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)

    def test_public_leaf_inventory_matches_runtime_owner(self):
        from tools.release_contract import build_parser, parser_leaf_commands

        self.assertEqual(
            observer.PUBLIC_COMMAND_LEAVES,
            frozenset(
                command.replace(" ", ".")
                for command in parser_leaf_commands(build_parser())
            ),
        )

    def test_taskgov_proxy_logs_only_sanitized_fields_and_returns_readback(self):
        instance = self.build()
        process = FakeProcess(stdout=self.envelope())
        secret = "private-unlogged-value"
        with patch.object(observer.subprocess, "Popen", return_value=process) as popen:
            result = instance.run_taskgov(
                "task.show",
                (self.task_id, "--note", secret),
                task_slot="primary",
            )
        self.assertTrue(result["ok"])
        kwargs = popen.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["cwd"], self.root)
        if os.name == "nt":
            self.assertEqual(
                kwargs["creationflags"],
                subprocess.CREATE_NEW_PROCESS_GROUP
                | observer.WINDOWS_CREATE_SUSPENDED,
            )
        else:
            self.assertTrue(kwargs["start_new_session"])
        command = popen.call_args.args[0]
        self.assertEqual(command[1:3], ["-I", "-S"])
        self.assertIn(secret, command)
        raw_log = self.log_path.read_bytes()
        self.assertNotIn(secret.encode(), raw_log)
        self.assertNotIn(str(self.root).encode(), raw_log)
        self.assertEqual(raw_log, observer.canonical_json_bytes(json.loads(raw_log)))
        operation = instance.report()["taskgov"][0]
        self.assertEqual(
            set(operation),
            {"command_leaf", "task_slot", "duration_ms", "result"},
        )
        self.assertEqual(operation["task_slot"], "primary")
        self.assertEqual(operation["result"], "success")

    def test_real_installable_taskgov_pretty_json_is_bounded_and_accepted(self):
        shutil.copytree(
            ROOT / "task-governance-tool",
            self.root / "task-governance-tool",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("state", "__pycache__", "*.pyc"),
        )
        instance = self.build()
        envelope = instance.run_taskgov("doctor")
        self.assertIsNotNone(envelope)
        self.assertIn("ok", envelope)
        self.assertIn(
            instance.report()["taskgov"][0]["result"],
            {"success", "service_error"},
        )

    def test_read_task_maps_slot_to_id_without_persisting_id(self):
        instance = self.build()
        with patch.object(
            observer.subprocess,
            "Popen",
            return_value=FakeProcess(stdout=self.envelope()),
        ):
            result = instance.read_task("primary")
        self.assertTrue(result["ok"])
        self.assertNotIn(self.task_id.encode(), self.log_path.read_bytes())

    def test_parent_snapshot_captures_before_after_and_boundary_without_log(self):
        script = self.root / "task-governance-tool" / "scripts" / "taskgov.py"
        script.write_text(
            "import json\n"
            "from pathlib import Path\n"
            "data = json.loads(Path('snapshot-state.json').read_text(encoding='utf-8'))\n"
            "print(json.dumps({'ok': True, 'command': 'task.show', "
            "'project_id': 'project_fixture', 'data': data, 'warnings': [], "
            "'errors': []}, indent=2))\n",
            encoding="utf-8",
        )
        instance = self.build()
        snapshots = self.root / "raw-snapshots"
        snapshots.mkdir()
        state_path = self.root / "snapshot-state.json"
        state_path.write_text(
            json.dumps({"task": {"status": "ready", "revision": 1}}),
            encoding="utf-8",
        )
        before = instance.snapshot_task("primary", "raw-snapshots/before.json")
        state_path.write_text(
            json.dumps({"task": {"status": "in_progress", "revision": 1}}),
            encoding="utf-8",
        )
        boundary = instance.snapshot_task("primary", "raw-snapshots/boundary.json")
        state_path.write_text(
            json.dumps({"task": {"status": "done", "revision": 2}}),
            encoding="utf-8",
        )
        after = instance.snapshot_task("primary", "raw-snapshots/after.json")
        self.assertEqual(before["data"]["task"]["status"], "ready")
        self.assertEqual(boundary["data"]["task"]["status"], "in_progress")
        self.assertEqual(after["data"]["task"]["status"], "done")
        for name, expected in (
            ("before", before),
            ("boundary", boundary),
            ("after", after),
        ):
            raw = (snapshots / f"{name}.json").read_bytes()
            self.assertEqual(raw, observer.canonical_json_bytes(expected))
            self.assertNotIn(b"\r", raw)
            self.assertFalse(raw.endswith(b"\n"))
        self.assertFalse(self.log_path.exists())
        self.assertEqual(instance.report()["taskgov"], [])
        self.assertNotIn("snapshot", observer._build_parser().format_help())

    def test_parent_snapshot_rejects_cross_path_existing_and_symlink_outputs(self):
        instance = self.build()
        snapshots = self.root / "raw-snapshots"
        snapshots.mkdir()
        self.assert_error(
            lambda: instance.snapshot_task("primary", "../outside.json"),
            "cross_path",
        )
        existing = snapshots / "existing.json"
        existing.write_bytes(b"preserve")
        self.assert_error(
            lambda: instance.snapshot_task("primary", "raw-snapshots/existing.json"),
            "artifact_exists",
        )
        try:
            link = snapshots / "linked.json"
            link.symlink_to(existing)
        except (OSError, NotImplementedError):
            return
        self.assert_error(
            lambda: instance.snapshot_task("primary", "raw-snapshots/linked.json"),
            "cross_path",
        )

    def test_input_and_service_results_are_strictly_classified(self):
        instance = self.build()
        error = [{"code": "invalid_argument", "message": "safe"}]
        with patch.object(
            observer.subprocess,
            "Popen",
            side_effect=[
                FakeProcess(stdout=self.envelope(ok=False, errors=error), returncode=1),
                FakeProcess(stdout=self.envelope(ok=False, errors=error), returncode=2),
            ],
        ):
            instance.run_taskgov("doctor")
            instance.run_taskgov("doctor")
        self.assertEqual(
            [row["result"] for row in instance.report()["taskgov"]],
            ["input_error", "service_error"],
        )

    def test_unknown_leaf_task_label_and_repo_override_fail_before_process(self):
        instance = self.build()
        with patch.object(observer.subprocess, "Popen") as popen:
            self.assert_error(lambda: instance.run_taskgov("task.removed"), "unknown_leaf")
            self.assert_error(
                lambda: instance.run_taskgov("task.show", ("tg_task_ffffffffffffffff",)),
                "unknown_task",
            )
            self.assert_error(
                lambda: instance.run_taskgov("doctor", ("--repo", "..")),
                "cross_path",
            )
            self.assert_error(lambda: instance.run_verification("missing"), "unknown_label")
        popen.assert_not_called()

    def test_verification_is_predefined_shell_free_and_logs_no_label_or_argv(self):
        instance = self.build()
        process = FakeProcess(returncode=0)
        with patch.object(observer.subprocess, "Popen", return_value=process) as popen:
            step = instance.run_verification("release_contract")
        command = popen.call_args.args[0]
        self.assertEqual(
            command[1:],
            ["-B", "tools/release_contract.py", "--repo", "."],
        )
        self.assertFalse(popen.call_args.kwargs["shell"])
        if os.name == "nt":
            self.assertEqual(
                popen.call_args.kwargs["creationflags"],
                subprocess.CREATE_NEW_PROCESS_GROUP
                | observer.WINDOWS_CREATE_SUSPENDED,
            )
        else:
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(
            set(step),
            {"ordinal", "kind", "duration_ms", "result"},
        )
        raw = self.log_path.read_bytes()
        self.assertNotIn(b"release_contract", raw)
        self.assertNotIn(b"tools/", raw)

    def test_verification_discards_streams_for_success_failure_and_timeout(self):
        instance = self.build()
        processes = [
            FakeProcess(returncode=0),
            FakeProcess(returncode=7),
            FakeProcess(timeout=True),
        ]
        with patch.object(
            observer.subprocess,
            "Popen",
            side_effect=processes,
        ) as popen:
            results = [
                instance.run_verification("focused_cli")["result"]
                for _process in processes
            ]
        self.assertEqual(results, ["success", "failure", "timeout"])
        self.assertEqual(
            [row["result"] for row in instance.report()["verifications"]],
            results,
        )
        for call in popen.call_args_list:
            self.assertIs(call.kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(call.kwargs["stderr"], subprocess.DEVNULL)
            self.assertFalse(call.kwargs["shell"])
        for row in instance.report()["verifications"]:
            self.assertEqual(
                set(row),
                {"ordinal", "kind", "duration_ms", "result"},
            )

    def test_timeout_is_killed_and_recorded_at_exact_cap(self):
        instance = self.build()
        process = FakeProcess(timeout=True)
        with patch.object(observer.subprocess, "Popen", return_value=process):
            result = instance.run_verification("focused_cli")
        self.assertTrue(process.killed)
        self.assertEqual(result["duration_ms"], 300_000)
        self.assertEqual(result["result"], "timeout")

    def test_taskgov_timeout_discards_untrusted_output_and_records_timeout(self):
        instance = self.build()
        process = FakeProcess(stdout=b"not trusted", timeout=True)
        with patch.object(observer.subprocess, "Popen", return_value=process):
            result = instance.run_taskgov("doctor")
        self.assertIsNone(result)
        self.assertTrue(process.killed)
        operation = instance.report()["taskgov"][0]
        self.assertEqual(operation["duration_ms"], 300_000)
        self.assertEqual(operation["result"], "timeout")

    def test_cross_process_reservation_serializes_execution_and_canonical_append(self):
        taskgov = self.root / "task-governance-tool" / "scripts" / "taskgov.py"
        taskgov.write_text(
            "import json\n"
            "import os\n"
            "import time\n"
            "lock = '.observer-child-active'\n"
            "descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)\n"
            "try:\n"
            "    time.sleep(0.05)\n"
            "    print(json.dumps({'ok': True, 'command': 'doctor', "
            "'project_id': 'fixture', 'data': {}, 'warnings': [], 'errors': []}))\n"
            "finally:\n"
            "    os.close(descriptor)\n"
            "    os.unlink(lock)\n",
            encoding="utf-8",
        )
        release = self.root / "tools" / "release_contract.py"
        release.write_text(
            "import os\n"
            "import time\n"
            "lock = '.observer-child-active'\n"
            "descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)\n"
            "try:\n"
            "    time.sleep(0.05)\n"
            "finally:\n"
            "    os.close(descriptor)\n"
            "    os.unlink(lock)\n",
            encoding="utf-8",
        )
        base = [
            sys.executable,
            "-B",
            str(ROOT / "tools" / "m20_trial_observer.py"),
            "--trial-root",
            str(self.root),
            "--config",
            "observer-config.json",
            "--log",
            "observer-log.json",
        ]
        commands = [
            [*base, "taskgov", "--leaf", "doctor"],
            [*base, "verify", "--label", "release_contract"],
        ] * 4
        processes = [
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            for command in commands
        ]
        outcomes = [process.communicate(timeout=20) for process in processes]
        for process, (stdout, stderr) in zip(processes, outcomes, strict=True):
            self.assertEqual(
                (process.returncode, stderr),
                (0, b""),
                msg=(stdout, stderr),
            )
        report = self.build().report()
        self.assertEqual(len(report["taskgov"]), 4)
        self.assertEqual(len(report["verifications"]), 4)
        self.assertEqual(
            [row["result"] for row in report["taskgov"]],
            ["success"] * 4,
        )
        self.assertEqual(
            [row["result"] for row in report["verifications"]],
            ["success"] * 4,
        )
        self.assertEqual(
            [row["ordinal"] for row in report["verifications"]],
            [1, 2, 3, 4],
        )
        raw = self.log_path.read_bytes()
        self.assertEqual(raw, observer.canonical_json_bytes(json.loads(raw)))
        self.assertFalse((self.root / observer.LOCK_FILENAME).exists())
        self.assertFalse((self.root / ".observer-child-active").exists())

    def test_concurrent_final_capacity_runs_only_one_subprocess(self):
        instance = self.build()
        log = observer._empty_log(instance.config)
        log["verifications"] = [
            {
                "ordinal": ordinal,
                "kind": "focused",
                "duration_ms": 1,
                "result": "success",
            }
            for ordinal in range(1, observer.MAX_VERIFICATION_STEPS)
        ]
        self.log_path.write_bytes(observer.canonical_json_bytes(log))
        release = self.root / "tools" / "release_contract.py"
        release.write_text(
            "from pathlib import Path\n"
            "import time\n"
            "with Path('capacity-invocations.bin').open('ab', buffering=0) as stream:\n"
            "    stream.write(b'x')\n"
            "time.sleep(0.1)\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-B",
            str(ROOT / "tools" / "m20_trial_observer.py"),
            "--trial-root",
            str(self.root),
            "--config",
            "observer-config.json",
            "--log",
            "observer-log.json",
            "verify",
            "--label",
            "release_contract",
        ]
        processes = [
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            for _index in range(2)
        ]
        outcomes = [process.communicate(timeout=20) for process in processes]
        self.assertEqual(sorted(process.returncode for process in processes), [0, 2])
        failed = [
            stderr
            for process, (_stdout, stderr) in zip(processes, outcomes, strict=True)
            if process.returncode == 2
        ]
        self.assertEqual(len(failed), 1)
        self.assertIn(b'"error_code":"cap_exceeded"', failed[0])
        self.assertEqual((self.root / "capacity-invocations.bin").read_bytes(), b"x")
        final = self.build().report()["verifications"]
        self.assertEqual(len(final), observer.MAX_VERIFICATION_STEPS)
        self.assertEqual(final[-1]["ordinal"], observer.MAX_VERIFICATION_STEPS)
        self.assertFalse((self.root / observer.LOCK_FILENAME).exists())

    def test_reservation_is_bounded_fail_closed_and_reparse_safe(self):
        instance = self.build()
        instance.lock_path.write_bytes(b"")
        with (
            patch.object(observer, "LOCK_WAIT_SECONDS", 0),
            patch.object(observer.subprocess, "Popen") as popen,
        ):
            self.assert_error(
                lambda: instance.run_verification("focused_cli"),
                "observer_busy",
            )
        popen.assert_not_called()
        instance.lock_path.unlink()
        try:
            instance.lock_path.symlink_to(self.config_path)
        except (OSError, NotImplementedError):
            return
        with patch.object(observer.subprocess, "Popen") as popen:
            self.assert_error(
                lambda: instance.run_verification("focused_cli"),
                "cross_path",
            )
        popen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows process-tree behavior")
    def test_windows_timeout_terminates_descendant_before_recording(self):
        release = self.root / "tools" / "release_contract.py"
        release.write_text(
            "import subprocess\n"
            "import sys\n"
            "import time\n"
            "child = (\"from pathlib import Path\\n\"\n"
            "         \"import time\\n\"\n"
            "         \"with Path('descendant-heartbeat.bin').open('ab', buffering=0) as stream:\\n\"\n"
            "         \"    for _index in range(400):\\n\"\n"
            "         \"        stream.write(b'x')\\n\"\n"
            "         \"        time.sleep(0.025)\\n\")\n"
            "subprocess.Popen([sys.executable, '-c', child])\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        with (
            patch.object(observer, "TIMEOUT_SECONDS", 0.5),
            patch.object(
                observer,
                "_new_process_tree",
                side_effect=self.real_new_process_tree,
            ),
        ):
            result = self.build().run_verification("release_contract")
        self.assertEqual(result["result"], "timeout")
        heartbeat = self.root / "descendant-heartbeat.bin"
        self.assertTrue(heartbeat.exists())
        size_after_return = heartbeat.stat().st_size
        self.assertGreater(size_after_return, 0)
        time.sleep(0.2)
        self.assertEqual(heartbeat.stat().st_size, size_after_return)
        self.assertFalse((self.root / observer.LOCK_FILENAME).exists())

    def test_exact_channel_caps_fail_before_any_subprocess(self):
        instance = self.build()
        log = observer._empty_log(instance.config)
        log["taskgov"] = [
            {
                "command_leaf": "doctor",
                "task_slot": None,
                "duration_ms": 1,
                "result": "success",
            }
            for _index in range(observer.MAX_TASKGOV_OPERATIONS)
        ]
        self.log_path.write_bytes(observer.canonical_json_bytes(log))
        with patch.object(observer.subprocess, "Popen") as popen:
            self.assert_error(
                lambda: instance.run_taskgov("doctor"),
                "cap_exceeded",
            )
        popen.assert_not_called()

        log = observer._empty_log(instance.config)
        log["verifications"] = [
            {
                "ordinal": ordinal,
                "kind": "focused",
                "duration_ms": 1,
                "result": "success",
            }
            for ordinal in range(1, observer.MAX_VERIFICATION_STEPS + 1)
        ]
        self.log_path.write_bytes(observer.canonical_json_bytes(log))
        with patch.object(observer.subprocess, "Popen") as popen:
            self.assert_error(
                lambda: instance.run_verification("focused_cli"),
                "cap_exceeded",
            )
        popen.assert_not_called()

    def test_config_rejects_unsafe_verification_kind_duplicate_and_noncanonical(self):
        unsafe = dict(self.config)
        unsafe["verification_labels"] = [
            {"label": "unsafe", "kind": "focused", "argv": ["-c", "print(1)"]}
        ]
        self.config_path.write_bytes(observer.canonical_json_bytes(unsafe))
        self.assert_error(self.build, "unsafe_verification")

        duplicate = dict(self.config)
        duplicate["task_slots"] = [
            {"task_slot": "primary", "task_id": self.task_id},
            {"task_slot": "primary", "task_id": "tg_task_fedcba9876543210"},
        ]
        self.config_path.write_bytes(observer.canonical_json_bytes(duplicate))
        self.assert_error(self.build, "duplicate_config")

        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        self.assert_error(self.build, "source_drift")

    def test_config_is_bound_to_frozen_unit_scenario_arm_and_trial(self):
        for updates in (
            {"unit": "M20.2"},
            {"scenario_id": "vp_unplanned"},
            {"arm": "broad"},
            {"trial_id": "vp_cli_contract.baseline.02"},
        ):
            with self.subTest(updates=updates):
                candidate = dict(self.config)
                candidate.update(updates)
                self.config_path.write_bytes(observer.canonical_json_bytes(candidate))
                self.assert_error(self.build, "inventory_mismatch")

    def test_duplicate_json_key_and_malformed_log_fail_closed(self):
        self.config_path.write_bytes(
            b'{"arm":"baseline","arm":"broad","scenario_id":"vp_cli_contract",'
            b'"schema":"m20-trial-observer-config-v1","task_slots":[],'
            b'"trial_id":"vp_cli_contract.baseline.01","unit":"M20.3",'
            b'"verification_labels":[]}'
        )
        self.assert_error(self.build, "duplicate_json_key")

        self.config_path.write_bytes(observer.canonical_json_bytes(self.config))
        instance = self.build()
        self.log_path.write_bytes(b"{}")
        self.assert_error(instance.report, "source_drift")

        self.log_path.write_bytes(
            b'{"arm":"baseline","arm":"broad","scenario_id":"vp_cli_contract",'
            b'"schema":"m20-trial-observer-log-v1","taskgov":[],'
            b'"trial_id":"vp_cli_contract.baseline.01","unit":"M20.3",'
            b'"verifications":[]}'
        )
        self.assert_error(instance.report, "duplicate_json_key")

    def test_cross_path_and_symlink_are_rejected(self):
        outside = self.root.parent / "outside-observer-config.json"
        self.assert_error(
            lambda: observer.TrialObserver(self.root, "../outside-observer-config.json", "log.json"),
            "cross_path",
        )
        try:
            link = self.root / "linked-config.json"
            link.symlink_to(self.config_path)
        except (OSError, NotImplementedError):
            return
        self.assert_error(
            lambda: observer.TrialObserver(self.root, "linked-config.json", "log.json"),
            "source_missing",
        )

    @unittest.skipUnless(os.name == "nt", "Windows rooted-path semantics")
    def test_existing_rooted_outside_config_log_and_snapshot_fail_promptly(self):
        with tempfile.TemporaryDirectory(dir=self.root.parent) as outside_tmp:
            outside = Path(outside_tmp) / "outside.json"
            outside.write_bytes(b"preserve")
            rooted = str(outside)[len(outside.drive) :]
            self.assertTrue(Path(rooted).root)
            self.assertFalse(Path(rooted).is_absolute())
            with patch.object(observer.subprocess, "Popen") as popen:
                self.assert_error(
                    lambda: observer.TrialObserver(
                        self.root,
                        rooted,
                        "observer-log.json",
                    ),
                    "cross_path",
                )
                self.assert_error(
                    lambda: observer.TrialObserver(
                        self.root,
                        "observer-config.json",
                        rooted,
                    ),
                    "cross_path",
                )
                instance = self.build()
                self.assert_error(
                    lambda: instance.snapshot_task("primary", rooted),
                    "cross_path",
                )
            popen.assert_not_called()

    def test_tampered_duplicate_verification_ordinal_is_rejected(self):
        instance = self.build()
        log = {
            "schema": observer.LOG_SCHEMA,
            "unit": self.config["unit"],
            "scenario_id": self.config["scenario_id"],
            "trial_id": self.config["trial_id"],
            "arm": self.config["arm"],
            "taskgov": [],
            "verifications": [
                {"ordinal": 2, "kind": "focused", "duration_ms": 1, "result": "success"}
            ],
        }
        self.log_path.write_bytes(observer.canonical_json_bytes(log))
        self.assert_error(instance.report, "source_drift")


if __name__ == "__main__":
    unittest.main()
