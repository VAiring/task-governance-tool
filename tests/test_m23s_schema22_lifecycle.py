from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests import test_m243b_schema21_compatibility as manual_fixture
from tests import test_m243c_runner_gate as runner_fixture
from tests.m14_test_support import _copy_skill, refresh_test_manifest, tree_snapshot
from tests.test_m242_runner_service import RunnerServiceFixture, git
from tests.test_m23s_schema22_migration import _logical_snapshot
from tests.test_m23s_schema22_projection import _source
from tests.test_m23s_schema22_validation import _bundle_artifacts, storage
from task_governance_tool import reviews, tasks, verification_receipts
from task_governance_tool import verification_runner_service as service


def _matching_commit(fixture):
    tree = git(fixture.repo, "write-tree").stdout.decode("ascii").strip()
    parent = git(fixture.repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    return git(
        fixture.repo, "commit-tree", tree, "-p", parent, "-m", "Schema22 completion fixture"
    ).stdout.decode("ascii").strip()


def _selection(connection, target, task_id):
    snapshot = storage.read_current_verification_runner_gate_snapshot(
        connection, project_id=target.project.project_id, task_id=task_id
    )
    # Reuse the existing closed terminal classification, not a new gate table.
    mode = service._terminal_runner_mode(snapshot["resolution"], snapshot["observation"])
    return service._selection_from_snapshot(
        snapshot, project_id=target.project.project_id, task_id=task_id, mode=mode
    )


def _receipt(connection, target, task_id, *, selection=None):
    task = tasks.read_internal_task(connection, target.project.project_id, task_id)
    with connection:
        return verification_receipts.add_verification_receipt(
            connection, target.project, task_id,
            result="pass", duration_ms=1, scope_coverage="full",
            expected_target_generation=task["review_target_generation"],
            runner_selector=(lambda _task: selection) if selection is not None else None,
        ).receipt


def _review_receipts(connection, target, task_id):
    task = tasks.read_internal_task(connection, target.project.project_id, task_id)
    tier = task["review_tier"]
    for index in range(2 if tier == 2 else 1):
        with connection:
            reviews.add_review_receipt(
                connection, target.project, task_id,
                reviewer=f"schema22-reviewer-{index}",
                kind="not_required" if tier == 0 else "independent",
                verdict="not_required" if tier == 0 else "pass",
                summary="Focused schema22 fixture review",
                **({} if tier == 0 else {
                    "reviewer_class": "human", "model_state": "not_applicable",
                    "skill_state": "not_applicable", "context_relation": "external_context",
                }),
            )


def _completion_plan(connection, target, task_id, revision, *, selection=None):
    request = tasks.build_completion_request(
        task_id, verification_complete=True, review_complete=True,
        completion_evidence_kind="git_commit", completion_revision=revision,
    )
    basis = tasks.capture_completion_basis(
        connection, target.project, task_id, runner_selection=selection
    )
    return tasks.prepare_completion_plan(basis, target.project, request)


def _complete(connection, target, task_id, revision, *, selection=None):
    plan = _completion_plan(connection, target, task_id, revision, selection=selection)
    with connection:
        return tasks.complete_task(connection, target.project, plan)


class Schema22LifecycleTests(unittest.TestCase):
    def _assert_new_bundle(self, connection, target, task_id, kind):
        storage.validate_schema22_storage(connection)
        basis, artifacts = _bundle_artifacts(connection, target.project.project_id)
        record = max(
            (row for row in basis.native_bundles if row.bundle.task_id == task_id),
            key=lambda row: row.bundle.cycle_ordinal,
        )
        artifact = artifacts[record.bundle.completion_evidence_bundle_id]
        self.assertEqual((record.bundle.source_schema_version, record.bundle.bundle_version), (22, 2))
        self.assertEqual((artifact.payload["source_schema_version"], artifact.payload["bundle_version"]), (22, 2))
        self.assertEqual(artifact.payload["verification_basis"]["kind"], kind)
        cycle = next(row for row in basis.cycles if row.completion_cycle_id == record.bundle.completion_cycle_id)
        self.assertEqual(cycle.verification_basis_kind, kind)
        self.assertEqual(cycle.verification_receipt_id, record.bundle.verification_receipt_id)
        self.assertEqual(cycle.verification_runner_observation_id, record.bundle.verification_runner_observation_id)
        self.assertEqual(_source(artifact.envelope).source, artifact.envelope)
        self.assertEqual(storage.SCHEMA_VERSION, 22)
        return artifact

    def test_actual22_manual_receipt_and_not_required_completion(self):
        for required, kind in ((True, "caller_attestation"), (False, "not_required")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                _install, target, task_id, commit = manual_fixture._seed_targeted_m21_fixture(
                    self, Path(temporary), record_receipt=False, verification_required=required
                )
                with closing(storage.connect(target.db_path)) as connection:
                    self.assertTrue(storage._migrate_schema22_connection(connection))
                    receipt = _receipt(connection, target, task_id) if required else None
                    _complete(connection, target, task_id, commit)
                    artifact = self._assert_new_bundle(connection, target, task_id, kind)
                    payload = artifact.payload
                    self.assertEqual(payload["verification_basis"]["verification_receipt_id"],
                                     receipt["verification_receipt_id"] if receipt else None)
                    self.assertIsNone(payload["runner_observation"])
                    if receipt:
                        self.assertEqual(receipt["verification_subject"]["basis_version"], 1)
                        self.assertIsNone(receipt["verification_subject"]["legacy_caller_label"])
                    else:
                        self.assertIsNone(payload["verification_receipt"])
                        self.assertEqual(payload["task"]["verification"], "")

    def test_actual22_runner_pass_fallback_and_blocking_reuse_existing_matrix(self):
        for branch, code in (("pass", None), ("fallback", "verification_receipt_required"),
                             ("blocking", "verification_receipt_blocking")):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerServiceFixture(Path(temporary))
                _prepared, intent = runner_fixture._launch(fixture)
                observation = runner_fixture._persist_terminal(fixture, intent, branch=branch)
                runner_fixture._seed_review_receipts(fixture)
                commit = _matching_commit(fixture)
                with closing(storage.connect(fixture.db)) as connection:
                    self.assertEqual(storage.current_schema_version(connection), 22)
                    selected = _selection(connection, fixture.target, fixture.task_id)
                    before = _logical_snapshot(connection)
                    shown = tasks.show_task(connection, fixture.target.project, fixture.task_id,
                                            runner_selection=selected)
                    self.assertEqual(shown.verification_evidence["gate"]["blocking_code"], code)
                    self.assertEqual(shown.verification_evidence["gate"]["satisfied"], branch == "pass")
                    self.assertEqual(shown.verification_evidence["counts"]["receipts_total"], 0)
                    self.assertEqual(_logical_snapshot(connection), before)
                    if branch == "fallback":
                        receipt = _receipt(connection, fixture.target, fixture.task_id, selection=selected)
                    else:
                        with self.assertRaises(verification_receipts.VerificationReceiptError) as rejected:
                            _receipt(connection, fixture.target, fixture.task_id, selection=selected)
                        self.assertEqual(rejected.exception.code, "evidence_basis_stale")
                        self.assertEqual(_logical_snapshot(connection), before)
                    if branch == "blocking":
                        with self.assertRaises(tasks.TaskValidationError) as rejected:
                            _complete(connection, fixture.target, fixture.task_id, commit, selection=selected)
                        self.assertEqual(rejected.exception.code, "verification_receipt_blocking")
                        self.assertEqual(_logical_snapshot(connection), before)
                        continue
                    _complete(connection, fixture.target, fixture.task_id, commit, selection=selected)
                    kind = "runner_observation" if branch == "pass" else "caller_attestation"
                    artifact = self._assert_new_bundle(connection, fixture.target, fixture.task_id, kind)
                    if branch == "pass":
                        self.assertIsNone(artifact.payload["verification_receipt"])
                        self.assertEqual(artifact.payload["verification_basis"]["runner_observation_id"],
                                         observation.verification_runner_observation_id)
                    else:
                        self.assertEqual(artifact.payload["verification_basis"]["verification_receipt_id"],
                                         receipt["verification_receipt_id"])
                        self.assertIsNone(artifact.payload["runner_observation"])

    def test_reopen_fresh22_target_does_not_reactivate_or_rewrite_old_runner_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            with manual_fixture._schema21_runtime():
                fixture = RunnerServiceFixture(Path(temporary))
                runner_fixture._complete_runner_pass(fixture)
            with closing(storage.connect(fixture.db)) as connection:
                project_id = fixture.target.project.project_id
                old_basis, old_artifacts = _bundle_artifacts(connection, project_id)
                old_cycle = old_basis.cycles[0]
                old_history = storage.read_completion_history(connection, project_id=project_id, task_id=fixture.task_id)
                self.assertTrue(storage._migrate_schema22_connection(connection))
                with connection:
                    tasks.edit_task(connection, fixture.target.project, fixture.task_id,
                                    status="in_progress", reopen_reason="Require fresh schema22 evidence")
                reopened = tasks.read_internal_task(connection, project_id, fixture.task_id)
                self.assertEqual(reopened["review_target_runner_basis_version"], 0)
                self.assertEqual(reopened["review_target_kind"], "")
                self.assertGreater(reopened["review_target_generation"], old_cycle.review_target_generation)
                self.assertEqual(storage.read_completion_history(connection, project_id=project_id,
                                                                 task_id=fixture.task_id), old_history)
                with connection:
                    reviews.set_git_snapshot_target(connection, fixture.target.project, fixture.task_id)
                fresh = tasks.read_internal_task(connection, project_id, fixture.task_id)
                self.assertEqual(fresh["review_target_runner_basis_version"], 0)
                self.assertGreater(fresh["review_target_generation"], reopened["review_target_generation"])
                self.assertEqual(verification_receipts.current_verification_gate(connection, task=fresh).blocking_code,
                                 "verification_receipt_required")
                _receipt(connection, fixture.target, fixture.task_id)
                _review_receipts(connection, fixture.target, fixture.task_id)
                _complete(connection, fixture.target, fixture.task_id, _matching_commit(fixture))
                self._assert_new_bundle(connection, fixture.target, fixture.task_id, "caller_attestation")
                _basis_after, artifacts = _bundle_artifacts(connection, project_id)
                for bundle_id, artifact in old_artifacts.items():
                    self.assertEqual(artifacts[bundle_id], artifact)
                    self.assertEqual(artifact.payload["source_schema_version"], 21)
                shown = tasks.show_task(connection, fixture.target.project, fixture.task_id)
                self.assertEqual(shown.completion_history["total"], 2)

    def test_explicit22_selector_retains_real_implementation_drift_invalidation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            skill_parent = fixture.repo / ".agents" / "skills"
            skill_parent.mkdir(parents=True)
            installed = _copy_skill(skill_parent)
            fixture.target = replace(fixture.target, skill_root=installed)
            implementation_before = service.capture_runner_implementation(installed)
            original_prepared = fixture.prepared
            with mock.patch.object(fixture, "prepared", side_effect=lambda: replace(
                original_prepared(), implementation=implementation_before
            )):
                prepared, intent = runner_fixture._launch(fixture)
            runner_fixture._persist_terminal(fixture, intent, branch="pass")
            runner_fixture._seed_review_receipts(fixture)
            with closing(storage.connect(fixture.db)) as connection:
                self.assertEqual(storage.current_schema_version(connection), 22)
                task = tasks.read_internal_task(connection, fixture.target.project.project_id, fixture.task_id)
                before = _logical_snapshot(connection)
            commit = _matching_commit(fixture)
            before_files = tree_snapshot(fixture.db.parent)
            # Existing Plan seams stay unchanged. Both the public connection
            # admission and the physical package comparison remain real.
            with mock.patch.object(service, "capture_verification_runner_plan", return_value=None), \
                 mock.patch.object(service, "resolve_verification_runner_plan", return_value=prepared.plan):
                current = service.select_current_verification_runner_basis(fixture.target, task=task)
                self.assertEqual(current.mode, "runner_observation")
                with closing(storage.connect(fixture.db)) as connection:
                    _completion_plan(connection, fixture.target, fixture.task_id, commit, selection=current)
                core = installed / "SKILL.md"
                core.write_bytes(core.read_bytes() + b"\n<!-- Package identity fixture. -->\n")
                refresh_test_manifest(installed)
                self.assertNotEqual(service.capture_runner_implementation(installed).implementation_digest,
                                    implementation_before.implementation_digest)
                stale = service.select_current_verification_runner_basis(fixture.target, task=task)
                self.assertEqual(stale.mode, "stale")
                with closing(storage.connect(fixture.db)) as connection:
                    gate = verification_receipts.current_verification_gate(connection, task=task,
                                                                            runner_selection=stale)
                    self.assertEqual(gate.blocking_code, "evidence_basis_stale")
                    with self.assertRaises(tasks.TaskValidationError) as rejected:
                        _completion_plan(connection, fixture.target, fixture.task_id, commit, selection=stale)
                    self.assertEqual(rejected.exception.code, "evidence_basis_stale")
                    self.assertEqual(_logical_snapshot(connection), before)
            self.assertEqual(tree_snapshot(fixture.db.parent), before_files)

    def test_fresh22_target_intent_and_terminal_use_existing_locked_writers(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerServiceFixture(Path(temporary))
            with closing(storage.connect(fixture.db)) as connection:
                self.assertEqual(storage.current_schema_version(connection), 22)
                with connection:
                    tasks.edit_task(connection, fixture.target.project, fixture.task_id, status="in_progress")
                authority = reviews.read_review_target_authority_basis(connection, fixture.target.project, fixture.task_id)
                prepared = fixture.prepared(authority)
                now = storage.utc_now()
                with connection:
                    reviews.persist_prepared_review_target_capture(
                        connection, fixture.target.project, authority.task,
                        observation=prepared.target.artifact, now=now, runner_basis_version=2,
                    )
                    current = storage.read_current_verification_runner_target_basis(
                        connection, project_id=fixture.target.project.project_id, task_id=fixture.task_id
                    )
                    resolution = service._resolution_row(current, prepared, created_at=now)
                    attempt = service._attempt_row(resolution, created_at=now)
                    self.assertTrue(storage.insert_verification_runner_resolution_locked(
                        connection, resolution=resolution, attempt=attempt
                    ))
                captured = tasks.read_internal_task(
                    connection, fixture.target.project.project_id, fixture.task_id
                )
                self.assertEqual(captured["review_target_runner_basis_version"], 2)
                observation = service._observation_row(
                    resolution, attempt, route="runner", launch_state="launched", outcome="pass",
                    reason=None, complete_plan=1, completed_step_count=resolution.step_count,
                    failed_step_ordinal=None, started_at=now, finished_at=now, duration_ms=1,
                    cpu_time_ms=1, peak_job_memory_bytes=1, total_process_count=1,
                )
                reference, link = service._terminal_evidence(
                    resolution, observation, acceptance_criterion_id=authority.acceptance_criterion_id
                )
                cleanup = service._cleanup_event(attempt, created_at=now,
                    terminal_observation_id=observation.verification_runner_observation_id)
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.assertTrue(storage.persist_verification_runner_terminal_locked(
                        connection, observation=observation, evidence_reference=reference,
                        criterion_link=link, cleanup_event=cleanup,
                    ))
                storage.validate_schema22_storage(connection)
                selected = _selection(connection, fixture.target, fixture.task_id)
                self.assertEqual(selected.mode, "runner_observation")
                self.assertEqual(observation.gate_eligibility_version, 1)
                self.assertEqual(selected.verification_runner_observation_id,
                                 observation.verification_runner_observation_id)
                _review_receipts(connection, fixture.target, fixture.task_id)
                _complete(connection, fixture.target, fixture.task_id, _matching_commit(fixture), selection=selected)
                self._assert_new_bundle(connection, fixture.target, fixture.task_id, "runner_observation")


if __name__ == "__main__":
    unittest.main()
