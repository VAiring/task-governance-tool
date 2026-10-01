"""Explicit setup prepares definitions without assuming host trust/delivery."""

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import make_physical_install, make_source_self_host, file_snapshot
from task_governance_tool import setup, usage_hook_setup as hooks


class UsageHookSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = make_physical_install(Path(self.tmp.name).resolve() / "日本語 $x & ' ‘ ’ ‚ ‛ project")

    @property
    def path(self):
        return self.install.project_root / ".codex" / "hooks.json"

    def run_setup(self, read_only=False):
        return setup.run_setup(repo=str(self.install.project_root), repo_explicit=True,
                               script_path=self.install.entrypoint, read_only=read_only,
                               backup_interval_minutes=None, backup_generations=None)

    def write_config(self, document):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def test_fresh_preview_preparation_and_byte_identical_replay(self):
        before = file_snapshot(self.install.project_root)
        preview = self.run_setup(True)
        self.assertEqual(preview.data["usage_hooks"]["status"], "preparation_required")
        self.assertEqual(preview.data["usage_hooks"]["completed_writes"], [])
        self.assertEqual(file_snapshot(self.install.project_root), before)
        result = self.run_setup()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["usage_hooks"], {
            "status": "prepared", "planned_writes": ["usage_hooks_prepare"],
            "completed_writes": ["usage_hooks_prepare"], "trust": "unknown",
            "next_action": "review_and_trust_hooks", "error": None})
        document = json.loads(self.path.read_bytes())
        self.assertEqual(set(document["hooks"]), set(hooks.EVENTS))
        for event, timeout in hooks.EVENTS.items():
            self.assertEqual(document["hooks"][event][0]["hooks"][0]["timeout"], timeout)
        # A current config retains user formatting and does not require new trust.
        self.path.write_text(json.dumps(document, indent=4), encoding="utf-8-sig")
        original = self.path.read_bytes()
        self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "current")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIn("trust and collection are not confirmed", result.text)

    def test_preserves_foreign_hooks_metadata_and_prepares_only_missing_handlers(self):
        foreign = {"type": "command", "command": "echo keep", "timeout": 7, "async": True}
        existing = {"description": "keep 日本語", "hooks": {
            "Stop": [{"matcher": "x", "hooks": [foreign]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [foreign]}]}}
        self.write_config(existing)
        self.assertTrue(self.run_setup().ok)
        after = json.loads(self.path.read_bytes())
        self.assertEqual(after["description"], existing["description"])
        self.assertEqual(after["hooks"]["PreToolUse"], existing["hooks"]["PreToolUse"])
        self.assertEqual(after["hooks"]["Stop"][0], existing["hooks"]["Stop"][0])
        self.assertEqual(len(after["hooks"]["Stop"]), 2)

    def test_owned_duplicates_and_old_paths_are_replaced_without_foreign_loss(self):
        self.run_setup()
        document = json.loads(self.path.read_bytes())
        own = document["hooks"]["Stop"][0]["hooks"][0]
        own["command"] = "obsolete-path"
        document["hooks"]["Stop"][0]["hooks"].append(dict(own))
        document["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "echo keep"})
        self.write_config(document)
        self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "prepared")
        handlers = json.loads(self.path.read_bytes())["hooks"]["Stop"][0]["hooks"]
        self.assertEqual(sum(h.get("statusMessage") == hooks.MARKER for h in handlers), 1)
        self.assertEqual(handlers[-1]["command"], "echo keep")

    def test_exact_legacy_documented_recipe_is_adopted_once(self):
        relative = ".agents/skills/task-governance-tool/scripts/usage_hook.py"
        self.write_config({"hooks": {event: [{"hooks": [{"type": "command",
            "command": f"python3 -B {relative}", "commandWindows": f"python -B {relative}",
            "timeout": timeout}]}] for event, timeout in hooks.EVENTS.items()}})
        self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "prepared")
        document = json.loads(self.path.read_bytes())
        self.assertTrue(all(len(groups) == len(groups[0]["hooks"]) == 1
                            for groups in document["hooks"].values()))

    def test_invalid_or_ambiguous_configs_are_preserved_and_do_not_rollback_core(self):
        cases = [b'{broken', b'{"hooks":{},"hooks":{}}', b'{"hooks":[]}',
                 json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command",
                     "command": "python customized/usage_hook.py"}]}]}}).encode()]
        self.path.parent.mkdir(exist_ok=True)
        for content in cases:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                result = self.run_setup()
                self.assertTrue(result.ok)
                self.assertEqual(result.data["usage_hooks"]["status"], "unavailable")
                self.assertEqual(self.path.read_bytes(), content)
                self.assertEqual(result.data["usage"]["status"] in {"initialized", "current"}, True)
                self.assertNotIn("customized", json.dumps(result.data))

    def test_inline_usage_conflict_preserves_both_files_other_inline_hooks_coexist(self):
        self.path.parent.mkdir(exist_ok=True)
        config = self.path.with_name("config.toml")
        original = b'[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype="command"\ncommand="python usage_hook.py"\n'
        config.write_bytes(original)
        self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "unavailable")
        self.assertFalse(self.path.exists())
        self.assertEqual(config.read_bytes(), original)
        config.write_bytes(original.replace(b"python usage_hook.py", b"echo keep"))
        self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "prepared")
        self.assertEqual(config.read_bytes(), original.replace(b"python usage_hook.py", b"echo keep"))

    def test_inline_windows_alias_conflict_never_adds_second_collector(self):
        self.path.parent.mkdir(exist_ok=True)
        config = self.path.with_name("config.toml")
        original = b'[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype="command"\ncommand="exit 0"\ncommand_windows="python usage_hook.py"\n'
        config.write_bytes(original)
        before = file_snapshot(self.install.project_root)
        preview = self.run_setup(True)
        self.assertEqual(preview.data["usage_hooks"]["status"], "unavailable")
        self.assertEqual(file_snapshot(self.install.project_root), before)
        self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "unavailable")
        self.assertFalse(self.path.exists())
        self.assertEqual(config.read_bytes(), original)

    def test_manual_usage_detection_respects_platform_filename_case(self):
        for key in ("command", "commandWindows", "command_windows"):
            with self.subTest(key=key):
                self.assertTrue(hooks._usage_handler({key: "python usage_hook.py"}))
                self.assertEqual(hooks._usage_handler({key: "python UsAgE_HoOk.Py"}), os.name == "nt")
                self.assertFalse(hooks._usage_handler({key: "echo unrelated"}))

    @unittest.skipUnless(os.name == "nt", "Windows filename aliases")
    def test_windows_case_variant_conflicts_preserve_json_and_inline_config(self):
        for filename, content in (
                ("hooks.json", b'{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"exit 0","commandWindows":"python USAGE_HOOK.PY"}]}]}}'),
                ("config.toml", b'[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype="command"\ncommand="exit 0"\ncommand_windows="python USAGE_HOOK.PY"\n')):
            with self.subTest(filename=filename):
                self.install = make_physical_install(Path(self.tmp.name).resolve() / filename)
                self.path.parent.mkdir()
                existing = self.path.with_name(filename)
                existing.write_bytes(content)
                before = file_snapshot(self.install.project_root)
                preview = self.run_setup(True)
                self.assertEqual(preview.data["usage_hooks"]["status"], "unavailable")
                self.assertEqual(file_snapshot(self.install.project_root), before)
                result = self.run_setup()
                self.assertTrue(result.ok)
                self.assertEqual(result.data["usage_hooks"]["status"], "unavailable")
                self.assertEqual(result.data["usage_hooks"]["completed_writes"], [])
                self.assertEqual(existing.read_bytes(), content)
                if filename == "config.toml":
                    self.assertFalse(self.path.exists())

    def test_current_config_with_nonstandard_constants_is_preserved_not_current(self):
        self.run_setup()
        document = json.loads(self.path.read_bytes())
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                document["metadata"] = value
                self.write_config(document)
                original = self.path.read_bytes()
                result = self.run_setup()
                self.assertTrue(result.ok)
                self.assertEqual(result.data["usage_hooks"]["status"], "unavailable")
                self.assertEqual(self.path.read_bytes(), original)

    def test_publish_failure_preserves_original_and_successful_core_retry(self):
        self.write_config({"description": "keep"})
        original = self.path.read_bytes()
        with mock.patch.object(hooks, "replace_configuration", side_effect=OSError("private detail")):
            result = self.run_setup()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["usage_hooks"]["error"], "usage_hooks_unavailable")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".taskgov-hooks-*.tmp")), [])
        retried = self.run_setup()
        self.assertEqual(retried.data["completed_writes"], [])
        self.assertEqual(retried.data["usage_hooks"]["status"], "prepared")

    def test_changed_configuration_before_publication_is_not_overwritten(self):
        self.write_config({"description": "initial"})
        real_create = hooks.create_exclusive_durable_file
        def create(*args, **kwargs):
            result = real_create(*args, **kwargs)
            self.path.write_bytes(b'{"description":"concurrent edit"}')
            return result
        with mock.patch.object(hooks, "create_exclusive_durable_file", side_effect=create):
            result = self.run_setup()
        self.assertEqual(result.data["usage_hooks"]["status"], "unavailable")
        self.assertEqual(self.path.read_bytes(), b'{"description":"concurrent edit"}')

    def test_core_or_usage_failure_never_creates_configuration(self):
        with mock.patch("task_governance_tool.usage_collection.setup_usage", return_value={"status": "unavailable"}):
            self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "not_attempted")
        self.assertFalse(self.path.exists())
        result = setup.run_setup(repo=str(self.install.project_root), repo_explicit=True,
            script_path=self.install.entrypoint, read_only=False,
            backup_interval_minutes=-1, backup_generations=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.data["usage_hooks"]["status"], "not_attempted")
        self.assertFalse(self.path.exists())

    def test_generated_command_runs_neutrally_with_special_path_characters(self):
        self.run_setup()
        handler = json.loads(self.path.read_bytes())["hooks"]["Stop"][0]["hooks"][0]
        if os.name == "nt":
            encoded = handler["commandWindows"].split()[-1]
            body = base64.b64decode(encoded).decode("utf-16-le")
            self.assertIn("--repo", body)
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        else:
            command = ["/bin/sh", "-c", handler["command"]]
        run = subprocess.run(command, input=b"{}", capture_output=True,
                             cwd=self.install.project_root, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, b"{}\n")

    @unittest.skipUnless(os.name == "nt", "PowerShell parser is a Windows check")
    def test_smart_quotes_in_interpreter_and_project_paths_round_trip_through_parser(self):
        interpreter = self.install.project_root / "Python ‘quoted’ ‚and‛" / "python.exe"
        with mock.patch.object(hooks.sys, "executable", str(interpreter)):
            _, command = hooks._commands(self.install.project_root, self.install.skill_root)
        encoded = command.split()[-1]
        # Parse only; do not execute the intentionally nonexistent interpreter.
        script = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                  "$source = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + encoded + "')); "
                  "$issues = $null; $ast = [Management.Automation.Language.Parser]::ParseInput($source,[ref]$null,[ref]$issues); "
                  "if ($issues.Count) { exit 1 }; "
                  "ConvertTo-Json -Compress -InputObject @($ast.EndBlock.Statements[0].PipelineElements[0].CommandElements | "
                  "ForEach-Object { if ($_ -is [Management.Automation.Language.CommandParameterAst]) { $_.Extent.Text } else { $_.Value } })")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii")], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [interpreter.as_posix(), "-B",
            (self.install.skill_root / "scripts/usage_hook.py").as_posix(), "--repo", self.install.project_root.as_posix()])

    def test_public_setup_self_host_and_ordinary_reads(self):
        install = make_source_self_host(Path(self.tmp.name) / "self-host")
        result = install.run("setup", "--repo", str(install.project_root), "--json")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["data"]["usage_hooks"]["status"], "prepared")
        path = install.project_root / ".codex" / "hooks.json"
        config = path.read_bytes()
        before = file_snapshot(install.project_root)
        for args in (("doctor", "--json"), ("task", "context", "--json"), ("setup", "--read-only", "--json")):
            self.assertEqual(install.run(*args, "--repo", str(install.project_root)).returncode, 0)
        self.assertEqual(path.read_bytes(), config)
        self.assertEqual(file_snapshot(install.project_root), before)

    def test_nonphysical_or_unreadable_paths_are_preserved(self):
        self.path.parent.write_bytes(b"not a directory")
        result = self.run_setup()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["usage_hooks"]["status"], "unavailable")
        self.assertEqual(self.path.parent.read_bytes(), b"not a directory")
        self.path.parent.unlink()
        self.path.parent.mkdir()
        self.path.mkdir()
        self.assertEqual(self.run_setup().data["usage_hooks"]["status"], "unavailable")
        self.assertTrue(self.path.is_dir())

    def test_linked_configuration_is_not_followed(self):
        outside = Path(self.tmp.name) / "outside-hooks"
        outside.mkdir()
        config = outside / "hooks.json"
        config.write_bytes(b'{"description":"preserve"}')
        if os.name == "nt":
            linked = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.path.parent), str(outside)],
                                    capture_output=True, timeout=30)
            self.assertEqual(linked.returncode, 0, linked.stderr)
        else:
            self.path.parent.symlink_to(outside, target_is_directory=True)
        result = self.run_setup()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["usage_hooks"]["status"], "unavailable")
        self.assertEqual(config.read_bytes(), b'{"description":"preserve"}')
