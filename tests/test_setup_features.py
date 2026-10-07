"""Optional choices are Setup-only, retained intent with observed effective state."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.m14_test_support import make_physical_install, file_snapshot
from task_governance_tool import setup, setup_features as features
from task_governance_tool import setup_feature_config as config, usage_lifecycle
from task_governance_tool.effort import load_effort_profile
from task_governance_tool.viewer_config import load_viewer_refresh_interval


class SetupFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = make_physical_install(Path(self.tmp.name).resolve() / "project", git_managed=True)
        self.root = self.install.project_root
        self.skill = self.install.skill_root
        ignore = self.root / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") + "/.agents/skills/task-governance-tool/config/\n", encoding="utf-8")

    def run_setup(self, read_only=False, **choices):
        return setup.run_setup(repo=str(self.root), repo_explicit=True,
            script_path=self.install.entrypoint, read_only=read_only,
            backup_interval_minutes=None, backup_generations=None, feature_selections=choices)

    def rows(self, result):
        self.assertTrue(result.ok, result)
        return result.data["optional_features"]["features"]

    def write_config(self, name, value):
        path = self.skill / "config" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_initial_offer_no_silent_enable_and_no_write_preview(self):
        before = file_snapshot(self.root)
        preview = self.run_setup(True)
        self.assertEqual(file_snapshot(self.root), before)
        self.assertEqual(preview.data["optional_features"]["offer"], list(config.FEATURES))
        result = self.run_setup()
        self.assertEqual(result.data["optional_features"]["offer"], list(config.FEATURES))
        self.assertEqual(result.data["usage_hooks"]["status"], "not_requested")
        self.assertFalse((self.root / ".codex/hooks.json").exists())
        self.assertFalse((self.skill / "config").exists())
        for row in self.rows(result).values():
            self.assertEqual(row["selection"], "undecided")
            self.assertEqual(row["effective"], "off")

    def test_batch_on_off_preserves_values_and_explicit_off_across_repeated_setup(self):
        result = self.run_setup(**{name: True for name in config.FEATURES})
        self.assertTrue(all(row["effective"] == "on" for row in self.rows(result).values()))
        self.assertEqual(result.data["usage_hooks"]["trust"], "unknown")
        self.assertTrue(load_effort_profile(self.skill).enabled)
        self.assertEqual(load_effort_profile(self.skill).thresholds, {})
        self.assertEqual(load_viewer_refresh_interval(self.skill), 30)
        plan_path = self.skill / "config/verification-runner.json"
        self.assertEqual(json.loads(plan_path.read_bytes())["entries"], [])
        hooks = (self.root / ".codex/hooks.json").read_bytes()
        off = self.run_setup(**{name: False for name in config.FEATURES})
        self.assertTrue(all(row["effective"] == "off" for row in self.rows(off).values()))
        self.assertFalse(config.collection_allowed(self.skill))
        self.assertEqual(load_viewer_refresh_interval(self.skill), 0)
        self.assertEqual(json.loads((self.skill / "config/viewer.json").read_bytes())["refresh_interval_seconds"], 30)
        self.assertEqual((self.root / ".codex/hooks.json").read_bytes(), hooks)
        before = file_snapshot(self.root)
        repeated = self.run_setup()
        self.assertEqual(repeated.data["optional_features"]["offer"], [])
        self.assertTrue(all(row["selection"] == "off" for row in self.rows(repeated).values()))
        # Repeated setup may inspect core projections; configuration bytes stay exact.
        after = file_snapshot(self.root)
        for name, value in before.items():
            if "/config/" in name or name.endswith("hooks.json"):
                self.assertEqual(after[name], value)

    def test_all_off_without_configs_records_choices_and_does_not_reoffer(self):
        self.run_setup(**{name: False for name in config.FEATURES})
        files = list((self.skill / "config").iterdir())
        self.assertEqual([p.name for p in files], ["setup-features.json"])
        self.assertEqual(self.run_setup().data["optional_features"]["offer"], [])

    def test_review_wait_first_off_is_applied_and_replay_is_unchanged(self):
        result = self.run_setup(review_wait=False)
        self.assertEqual("applied", self.rows(result)["review_wait"]["status"])
        self.assertEqual("unchanged", self.rows(self.run_setup(review_wait=False))["review_wait"]["status"])
        self.assertFalse((self.root / ".codex/config.toml").exists())
        self.assertFalse((self.root / ".taskgov/current/review-wait").exists())

    def test_preview_selected_changes_reports_actual_not_proposed_and_writes_nothing(self):
        self.run_setup()
        before = file_snapshot(self.root)
        preview = self.run_setup(True, **{name: True for name in config.FEATURES})
        self.assertEqual(file_snapshot(self.root), before)
        for row in self.rows(preview).values():
            self.assertEqual(row["status"], "preview")
            self.assertEqual(row["effective"], "off")
            self.assertEqual(row["selection"], "undecided")
            self.assertTrue(row["requested"])

    def test_existing_profiles_and_manual_edits_are_truth_not_saved_preference(self):
        path = self.write_config("effort-advisory.json", {"schema_version": 1,
            "profile": "informational-v1", "enabled": True, "thresholds": {"changed_files": 21}})
        viewer = self.write_config("viewer.json", {"schema_version": 1,
            "profile": "visibility-refresh-v1", "refresh_interval_seconds": 77})
        original = path.read_bytes()
        first = self.run_setup()
        self.assertEqual(self.rows(first)["effort_advisory"]["selection_source"], "existing")
        self.assertEqual(path.read_bytes(), original)
        self.run_setup(effort_advisory=False, viewer_reload=False)
        self.assertEqual(json.loads(path.read_bytes())["thresholds"], {"changed_files": 21})
        self.assertEqual(json.loads(viewer.read_bytes())["refresh_interval_seconds"], 77)
        changed = json.loads(path.read_bytes())
        changed["enabled"] = True
        path.write_text(json.dumps(changed), encoding="utf-8")
        current = self.rows(self.run_setup())["effort_advisory"]
        self.assertEqual((current["selection"], current["effective"]), ("off", "on"))
        self.run_setup(viewer_reload=True)
        self.assertEqual(load_viewer_refresh_interval(self.skill), 77)

    def test_disabled_trusted_hook_does_not_open_logs_or_write_numerical_history(self):
        self.run_setup(usage_collection=True)
        self.run_setup(usage_collection=False)
        before = file_snapshot(self.root)
        with mock.patch.object(usage_lifecycle, "_target") as target:
            usage_lifecycle.collect_event({}, skill_root=self.skill, repo=self.root, environment={})
        target.assert_not_called()
        self.assertEqual(file_snapshot(self.root), before)

    def test_legacy_hooks_preserved_and_adopted_without_assuming_host_trust(self):
        self.run_setup(usage_collection=True)
        (self.skill / config.CHOICES_PATH).unlink()  # emulate a pre-selection installation
        self.assertTrue(config.collection_allowed(self.skill))
        result = self.run_setup()
        self.assertEqual(self.rows(result)["usage_collection"]["selection_source"], "existing")
        self.assertNotIn("usage_collection", result.data["optional_features"]["offer"])
        self.assertEqual(result.data["usage_hooks"]["trust"], "unknown")

    def test_failed_feature_preserves_core_and_unrelated_feature_succeeds(self):
        path = self.write_config("effort-advisory.json", {"schema_version": 900})
        before = path.read_bytes()
        result = self.run_setup(effort_advisory=True, verification_runner=True)
        rows = self.rows(result)
        self.assertEqual(rows["effort_advisory"]["status"], "unavailable")
        self.assertEqual(rows["effort_advisory"]["effective"], "unknown")
        self.assertEqual(rows["verification_runner"]["effective"], "on")
        self.assertEqual(path.read_bytes(), before)

    def test_partial_choice_save_failure_reports_effective_but_not_saved_success(self):
        self.run_setup()
        with mock.patch.object(features, "_save_choice", side_effect=OSError("PRIVATE")):
            result = self.run_setup(effort_advisory=True)
        row = self.rows(result)["effort_advisory"]
        self.assertEqual((row["selection"], row["effective"], row["status"]),
                         ("undecided", "on", "unavailable"))
        self.assertNotIn("PRIVATE", str(result))

    def test_hook_preparation_failure_retains_core_and_saved_choice_without_success_claim(self):
        self.root.joinpath(".codex").mkdir()
        path = self.root / ".codex/hooks.json"
        path.write_bytes(b"invalid")
        result = self.run_setup(usage_collection=True)
        row = self.rows(result)["usage_collection"]
        self.assertEqual(row["selection"], "on")
        self.assertEqual(row["effective"], "unknown")
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(path.read_bytes(), b"invalid")

    def test_viewer_publication_failure_is_separate_from_saved_config_and_core(self):
        self.run_setup()
        with mock.patch.object(features, "_viewer_publish", side_effect=OSError("PRIVATE")):
            result = self.run_setup(viewer_reload=True)
        row = self.rows(result)["viewer_reload"]
        self.assertEqual((row["selection"], row["effective"], row["status"]), ("on", "on", "unavailable"))
        self.assertTrue(result.ok)

    def test_invalid_choice_file_is_preserved_never_overwritten_or_enabled(self):
        path = self.write_config("setup-features.json", {"schema_version": 1, "choices": {"unknown": True}})
        before = path.read_bytes()
        result = self.run_setup(effort_advisory=True)
        self.assertTrue(all(row["status"] == "unavailable" for row in self.rows(result).values()))
        self.assertFalse(config.collection_allowed(self.skill))
        self.assertEqual(path.read_bytes(), before)

    def test_public_cli_help_and_json_text(self):
        help_result = self.install.run("setup", "--help")
        for option in config.FEATURES:
            self.assertIn("--" + option.replace("_", "-"), help_result.stdout)
        result = self.install.run("setup", "--json", "--effort-advisory", "on")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["data"]["optional_features"]["features"]["effort_advisory"]["effective"], "on")
        text = self.install.run("setup")
        self.assertIn("effort_advisory: selection=on, effective=on", text.stdout)

    def test_runner_preserves_entries_limits_and_exact_manual_or_runner_resolution(self):
        from tests.test_m242_runner_plan import (
            plan_payload, entry_payload, TASK_ID, CONTRACT_REVISION,
            EXPECTATION_DIGEST, CRITERION_DIGEST, source_from_raw,
        )
        from task_governance_tool.verification_runner_plan import resolve_verification_runner_plan
        basis = dict(task_id=TASK_ID, contract_revision=CONTRACT_REVISION,
                     verification_expectation_digest=EXPECTATION_DIGEST,
                     verification_criterion_digest=CRITERION_DIGEST)
        path = self.write_config("verification-runner.json", plan_payload(trusted_local=False))
        self.run_setup(verification_runner=True)
        value = json.loads(path.read_bytes())
        self.assertEqual(value["entries"], [entry_payload()])
        self.assertEqual(value["plan_id"], "project-plan")
        self.assertEqual(resolve_verification_runner_plan(source_from_raw(path.read_bytes()), **basis).route, "runner")
        self.run_setup(verification_runner=False)
        self.assertEqual(json.loads(path.read_bytes())["entries"], [entry_payload()])
        resolution = resolve_verification_runner_plan(source_from_raw(path.read_bytes()), **basis)
        self.assertEqual(resolution.reason, "trusted_local_disabled")
        path.unlink()  # new absent Plan path retains manual verification on ON
        self.run_setup(verification_runner=True)
        resolution = resolve_verification_runner_plan(source_from_raw(path.read_bytes()), **basis)
        self.assertEqual(resolution.reason, "plan_entry_absent")

    def test_new_session_setup_reads_saved_off_and_ordinary_context_is_read_only(self):
        self.run_setup(**{name: False for name in config.FEATURES})
        repeat = json.loads(self.install.run("setup", "--json").stdout)
        self.assertEqual(repeat["data"]["optional_features"]["offer"], [])
        self.assertTrue(all(r["selection"] == "off" for r in repeat["data"]["optional_features"]["features"].values()))
        before = file_snapshot(self.root)
        for command in (("task", "context"), ("doctor",), ("setup", "--read-only")):
            result = self.install.run(*command, "--json")
            self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(file_snapshot(self.root), before)

    def test_invalid_unselected_hooks_are_unknown_not_off_and_not_offered(self):
        self.root.joinpath(".codex").mkdir()
        path = self.root / ".codex/hooks.json"
        path.write_bytes(b'{"hooks":[]}')
        result = self.run_setup()
        self.assertEqual(self.rows(result)["usage_collection"]["effective"], "unknown")
        self.assertNotIn("usage_collection", result.data["optional_features"]["offer"])
        self.assertEqual(path.read_bytes(), b'{"hooks":[]}')

    def test_repeated_equal_selection_does_not_rewrite_config_bytes(self):
        choices = {name: True for name in config.FEATURES}
        self.run_setup(**choices)
        paths = list((self.skill / "config").iterdir())
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        rows = self.rows(self.run_setup(**choices))
        self.assertTrue(all(row["status"] == "unchanged" for row in rows.values()), rows)
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}, before)


if __name__ == "__main__":
    unittest.main()
