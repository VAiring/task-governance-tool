"""Fixed-file backup opportunities, without database reads or process launch."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

from task_governance_tool import async_backup_opportunity as opportunity
from task_governance_tool.artifact_lock import ArtifactLockError, zero_wait_artifact_lock
from task_governance_tool.async_backup_state import runtime_root
from task_governance_tool.state_resolver import canonical_state_paths
from task_governance_tool.storage import DatabaseTarget, ProjectIdentity, StorageError


class AsyncBackupOpportunityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        skill = self.directory / "skill"
        paths = canonical_state_paths(skill, repo=self.directory)
        paths.fixed_root.mkdir(parents=True)
        project = ProjectIdentity("project_fixture", self.directory, "a" * 64, "Fixture")
        self.target = DatabaseTarget(project, paths.database, False,
                                     binding_path_hash="a" * 64, binding_generation=1,
                                     skill_root=skill, backups_path=paths.backups,
                                     canonical_fixed=True)
        self.root = paths.backup_runtime
        self.compatibility = opportunity.current_compatibility()

    @contextmanager
    def active(self, *, compatibility=None):
        with opportunity.service_lease(self.target) as owner:
            baseline = opportunity.activate_service(
                self.target, owner, compatibility or self.compatibility)
            yield owner, baseline

    def read(self, name):
        return json.loads((self.root / name).read_text(encoding="ascii"))

    def test_absent_service_defers_without_creating_runtime(self):
        self.assertEqual(opportunity.request_backup(self.target).code, "deferred")
        self.assertFalse(self.root.exists())
        self.assertFalse(self.target.db_path.exists())

    def test_closed_request_is_published_without_db_access_or_spawning(self):
        with self.active() as (owner, baseline), mock.patch(
                "sqlite3.connect", side_effect=AssertionError("unexpected DB read")), mock.patch(
                "subprocess.Popen", side_effect=AssertionError("unexpected child")):
            self.assertIsNone(baseline)
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            record = opportunity.observe_opportunity(self.target, owner, self.compatibility)
        self.assertEqual(set(record), {"v", "owner_id", "project_id", "binding_path_hash",
                                       "binding_generation", "compatibility", "request_id"})
        self.assertEqual(record["owner_id"], owner)
        self.assertLessEqual((self.root / "opportunity.json").stat().st_size,
                             opportunity.MAX_OPPORTUNITY_BYTES)
        self.assertFalse(self.target.db_path.exists())

    def test_stale_presence_is_not_live_service_and_creates_no_pending(self):
        with self.active():
            pass
        before = (self.root / "service.json").read_bytes()
        self.assertEqual(opportunity.request_backup(self.target).code, "deferred")
        self.assertEqual((self.root / "service.json").read_bytes(), before)
        self.assertFalse((self.root / "opportunity.json").exists())

    def test_incompatible_live_service_defers_without_publication(self):
        for key in ("protocol", "schema_version"):
            with self.subTest(key=key), self.active(
                    compatibility=dict(self.compatibility, **{key: self.compatibility[key] + 1})):
                self.assertEqual(opportunity.request_backup(self.target).code, "deferred")
                self.assertFalse((self.root / "opportunity.json").exists())

    def test_running_worker_avoids_new_request(self):
        with self.active(), zero_wait_artifact_lock(self.root / "worker.lock", require_single_link=True):
            self.assertEqual(opportunity.request_backup(self.target).code, "running")
            self.assertFalse((self.root / "opportunity.json").exists())

    def test_unknown_service_or_worker_ownership_does_not_publish(self):
        original = opportunity.lock_owned
        with self.active():
            for name in ("service.lock", "worker.lock"):
                with self.subTest(name=name), mock.patch.object(
                        opportunity, "lock_owned",
                        side_effect=lambda path: None if path.name == name else original(path)):
                    self.assertEqual(opportunity.request_backup(self.target).code, "deferred")
                    self.assertFalse((self.root / "opportunity.json").exists())

    def test_opportunity_lock_contention_defers_without_mutation(self):
        with self.active(), zero_wait_artifact_lock(self.root / "opportunity.lock", require_single_link=True):
            self.assertEqual(opportunity.request_backup(self.target).code, "deferred")
            self.assertFalse((self.root / "opportunity.json").exists())

    def test_service_lease_cannot_be_duplicated(self):
        with opportunity.service_lease(self.target):
            with self.assertRaises(ArtifactLockError) as caught:
                with opportunity.service_lease(self.target):
                    self.fail("second service lease admitted")
            self.assertTrue(caught.exception.contended)

    def test_latest_request_coalesces_without_queue_or_history(self):
        with self.active() as (owner, _):
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            first = self.read("opportunity.json")["request_id"]
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            latest = opportunity.observe_opportunity(self.target, owner, self.compatibility)
            self.assertNotEqual(latest["request_id"], first)
            self.assertEqual({path.name for path in self.root.iterdir()},
                             {"service.lock", "opportunity.lock", "service.json", "opportunity.json"})

    def test_restart_baselines_existing_request_and_requires_new_owner_opportunity(self):
        with self.active() as (first_owner, _):
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            first_request = self.read("opportunity.json")["request_id"]
        with self.active() as (owner, baseline):
            self.assertNotEqual(owner, first_owner)
            self.assertEqual(baseline, first_request)
            self.assertIsNone(opportunity.observe_opportunity(self.target, owner, self.compatibility))
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            current = opportunity.observe_opportunity(self.target, owner, self.compatibility)
            self.assertEqual(current["owner_id"], owner)
            self.assertNotEqual(current["request_id"], baseline)

    def test_partial_reserved_temporary_is_recovered_but_unknown_file_is_preserved(self):
        with self.active():
            (self.root / ".opportunity-next.json").write_bytes(b'{"v":')
            (self.root / "unknown.tmp").write_bytes(b"not owned")
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            self.assertFalse((self.root / ".opportunity-next.json").exists())
            self.assertEqual((self.root / "unknown.tmp").read_bytes(), b"not owned")

    def test_failed_replace_preserves_previous_and_next_publication_recovers(self):
        with self.active():
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            previous = (self.root / "opportunity.json").read_bytes()
            with mock.patch.object(opportunity.os, "replace", side_effect=OSError("fixture replace failed")):
                self.assertEqual(opportunity.request_backup(self.target).code, "failed")
            self.assertEqual((self.root / "opportunity.json").read_bytes(), previous)
            self.assertTrue((self.root / ".opportunity-next.json").exists())
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            self.assertNotEqual((self.root / "opportunity.json").read_bytes(), previous)
            self.assertFalse((self.root / ".opportunity-next.json").exists())

    def test_malformed_final_request_and_foreign_project_are_preserved(self):
        with self.active() as (owner, _):
            variants = [b"{}", b"x" * 513,
                        json.dumps(dict(self.read("service.json"), request_id="b" * 32,
                                        project_id="foreign_project")).encode("ascii")]
            for value in variants:
                with self.subTest(value=value[:40]):
                    (self.root / "opportunity.json").write_bytes(value)
                    self.assertEqual(opportunity.request_backup(self.target).code, "failed")
                    self.assertEqual((self.root / "opportunity.json").read_bytes(), value)
                    with self.assertRaises((StorageError, opportunity.StatePathError)):
                        opportunity.observe_opportunity(self.target, owner, self.compatibility)

    def test_malformed_service_is_unknown_and_not_repaired_by_publisher(self):
        with self.active():
            (self.root / "service.json").write_bytes(b"{}")
            self.assertEqual(opportunity.request_backup(self.target).code, "deferred")
            self.assertEqual((self.root / "service.json").read_bytes(), b"{}")
            self.assertFalse((self.root / "opportunity.json").exists())

    def test_invalid_baseline_blocks_service_activation_without_repair(self):
        with opportunity.service_lease(self.target) as owner:
            (self.root / "opportunity.json").write_bytes(b"{}")
            with self.assertRaises(StorageError):
                opportunity.activate_service(self.target, owner, self.compatibility)
            self.assertFalse((self.root / "service.json").exists())
            self.assertEqual((self.root / "opportunity.json").read_bytes(), b"{}")

    def test_duplicate_keys_and_bool_protocol_are_rejected(self):
        with self.active() as (owner, _):
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            valid = (self.root / "opportunity.json").read_bytes()
            values = [valid.replace(b'"v":1', b'"v":1,"v":1'),
                      valid.replace(b'"protocol":1', b'"protocol":true')]
            for value in values:
                with self.subTest(value=value):
                    (self.root / "opportunity.json").write_bytes(value)
                    with self.assertRaises(StorageError):
                        opportunity.observe_opportunity(self.target, owner, self.compatibility)

    def test_hardlinked_reserved_temporary_is_not_removed(self):
        with self.active():
            source = self.directory / "not-a-temporary"
            source.write_bytes(b"preserve")
            temporary = self.root / ".opportunity-next.json"
            try:
                os.link(source, temporary)
            except OSError as exc:
                self.skipTest(f"hard link unavailable: {type(exc).__name__}")
            self.assertEqual(opportunity.request_backup(self.target).code, "failed")
            self.assertEqual(source.read_bytes(), b"preserve")
            self.assertTrue(temporary.exists())

    def test_noncanonical_target_never_publishes(self):
        self.assertEqual(opportunity.request_backup(replace(self.target, canonical_fixed=False)).code,
                         "deferred")
        self.assertFalse(self.root.exists())

    def test_idle_and_consumed_request_do_not_read_service_presence(self):
        original = opportunity._read
        with self.active() as (owner, _):
            with mock.patch.object(opportunity, "_read", wraps=original) as read:
                self.assertIsNone(opportunity.observe_opportunity(self.target, owner, self.compatibility))
                self.assertEqual([call.args[1] for call in read.call_args_list], ["opportunity.json"])
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            request_id = self.read("opportunity.json")["request_id"]
            with mock.patch.object(opportunity, "_read", wraps=original) as read:
                self.assertIsNone(opportunity.observe_opportunity(
                    self.target, owner, self.compatibility, after_request_id=request_id))
                self.assertEqual([call.args[1] for call in read.call_args_list], ["opportunity.json"])

    def test_same_consumed_id_with_changed_basis_is_not_accepted(self):
        with self.active() as (owner, _):
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            value = self.read("opportunity.json")
            value["binding_generation"] += 1
            (self.root / "opportunity.json").write_text(json.dumps(value), encoding="ascii")
            with self.assertRaises(StorageError):
                opportunity.observe_opportunity(self.target, owner, self.compatibility,
                                                after_request_id=value["request_id"])

    def test_new_request_revalidates_service_presence_before_returning(self):
        with self.active() as (owner, _):
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
            value = self.read("service.json")
            value["owner_id"] = "f" * 32
            (self.root / "service.json").write_text(json.dumps(value), encoding="ascii")
            with self.assertRaises(StorageError):
                opportunity.observe_opportunity(self.target, owner, self.compatibility)

    def test_admitted_binding_replaces_same_project_control_without_generation_order(self):
        for old_generation, current_generation in ((1, 2), (2, 1)):
            with self.subTest(old=old_generation, current=current_generation):
                previous_target = replace(self.target, binding_generation=old_generation,
                                          binding_path_hash="b" * 64)
                with opportunity.service_lease(previous_target) as previous_owner:
                    opportunity.activate_service(previous_target, previous_owner, self.compatibility)
                    self.assertEqual(opportunity.request_backup(previous_target).code, "requested")
                    old_request = self.read("opportunity.json")
                current_target = replace(self.target, binding_generation=current_generation)
                with opportunity.service_lease(current_target) as owner:
                    baseline = opportunity.activate_service(current_target, owner, self.compatibility)
                    self.assertEqual(baseline, old_request["request_id"])
                    self.assertEqual(self.read("opportunity.json"), old_request)
                    self.assertEqual(self.read("service.json")["binding_generation"], current_generation)
                    with self.assertRaises(StorageError):
                        opportunity.observe_opportunity(current_target, owner, self.compatibility)
                    self.assertEqual(opportunity.request_backup(current_target).code, "requested")
                    current = opportunity.observe_opportunity(current_target, owner, self.compatibility)
                    self.assertEqual(current["binding_path_hash"], current_target.binding_path_hash)
                    self.assertEqual(current["binding_generation"], current_generation)
                    self.assertNotEqual(current["request_id"], baseline)

    def test_foreign_project_control_cannot_be_adopted_on_activation(self):
        with self.active():
            self.assertEqual(opportunity.request_backup(self.target).code, "requested")
        originals = {name: (self.root / name).read_bytes()
                     for name in ("service.json", "opportunity.json")}
        for name in originals:
            with self.subTest(name=name):
                for other, value in originals.items():
                    (self.root / other).write_bytes(value)
                previous = json.loads(originals[name])
                previous["project_id"] = "foreign_project"
                data = json.dumps(previous).encode("ascii")
                (self.root / name).write_bytes(data)
                expected = dict(originals, **{name: data})
                with opportunity.service_lease(self.target) as owner:
                    with self.assertRaises(StorageError):
                        opportunity.activate_service(self.target, owner, self.compatibility)
                self.assertEqual({key: (self.root / key).read_bytes() for key in originals}, expected)

    def test_explicit_setup_relocation_can_baseline_then_publish_from_normal_update(self):
        from tests.test_m17_recovery_hardening import (
            _setup_current, _relocate_install, _run_setup,
            RELOCATION_ISSUED_AT, RELOCATION_CONFIRMED_AT,
        )
        from task_governance_tool import async_backup_repository as repository
        from task_governance_tool.state_resolver import resolve_project_state

        install, original, _ = _setup_current(self.directory / "relocation")
        original_root = runtime_root(original)
        with opportunity.service_lease(original) as owner:
            opportunity.activate_service(original, owner, self.compatibility)
            self.assertEqual(opportunity.request_backup(original).code, "requested")
            previous_request = (original_root / "opportunity.json").read_bytes()
        moved = _relocate_install(install, self.directory / "moved-project")
        preview = _run_setup(moved, read_only=True, now=RELOCATION_ISSUED_AT)
        self.assertTrue(preview.ok, preview)
        confirmed = _run_setup(moved, read_only=False, now=RELOCATION_CONFIRMED_AT,
                               confirmation_token=preview.data["relocation"]["confirmation_token"])
        self.assertTrue(confirmed.ok, confirmed)
        resolution = resolve_project_state(skill_root=moved.skill_root, repo=moved.project_root)
        rebound = resolution.target
        self.assertEqual(rebound.binding_generation, original.binding_generation + 1)
        self.assertNotEqual(rebound.binding_path_hash, original.binding_path_hash)
        current = repository.read_service_target(moved.skill_root, repo=moved.project_root)
        self.assertEqual(current.binding_generation, rebound.binding_generation)
        moved_root = runtime_root(current)
        self.assertEqual((moved_root / "opportunity.json").read_bytes(), previous_request)
        primary_before = current.db_path.read_bytes()
        with opportunity.service_lease(current) as owner:
            baseline = opportunity.activate_service(current, owner, self.compatibility)
            self.assertEqual(baseline, json.loads(previous_request)["request_id"])
            self.assertEqual(current.db_path.read_bytes(), primary_before)
            self.assertFalse((moved_root / "state.json").exists())
            changed = moved.run(
                "--repo", str(moved.project_root), "task", "add", "--title", "Relocated update",
                "--contract-scope", "Isolated relocation regression",
                "--contract-acceptance", "Normal update publishes a current-bound opportunity",
                "--contract-authority-ref", "conversation:fixture:relocated-update", "--json",
            )
            self.assertEqual(changed.returncode, 0, changed.stderr or changed.stdout)
            self.assertTrue(json.loads(changed.stdout)["ok"])
            request = opportunity.observe_opportunity(current, owner, self.compatibility,
                                                      after_request_id=baseline)
            self.assertEqual(request["binding_path_hash"], rebound.binding_path_hash)
            self.assertEqual(request["binding_generation"], rebound.binding_generation)
            self.assertNotEqual(request["request_id"], baseline)
            self.assertNotEqual(current.db_path.read_bytes(), primary_before)
            self.assertFalse((moved_root / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
