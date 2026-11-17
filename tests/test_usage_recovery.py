"""Real interrupted SQLite writers and permission-bounded numerical recovery."""

import hashlib
from contextlib import ExitStack, closing
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from time import monotonic
import unittest
from unittest import mock

from tests import test_usage_continuation as fixture
from task_governance_tool import usage_continuation_state as state
from task_governance_tool.usage_recovery import admit
from task_governance_tool.usage_values import UsageError, UsageRecoveryRequired
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_sources import locator_root_fingerprints
from task_governance_tool import usage_lifecycle as lifecycle
from task_governance_tool.usage_continuation_service import UsageServiceObserver


def crash(path):
    # Spill dirty pages then exit without commit/close: a real hot journal.
    code = """import os,sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.execute('PRAGMA cache_size=1')
c.execute('BEGIN IMMEDIATE')
c.executemany('INSERT INTO usage_sessions VALUES (?)',
 [('00000000-0000-4000-8000-'+format(i,'012x'),) for i in range(2,2002)])
os._exit(7)
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(path)],
                            capture_output=True, timeout=10, check=False)
    if result.returncode != 7:
        raise AssertionError("synthetic writer did not reach its interruption")


def snapshot(path):
    return tuple(hashlib.sha256(item.read_bytes()).hexdigest() if item.exists() else None
                 for item in (path, Path(str(path) + "-journal")))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.target = self.fixture.repo, self.fixture.target
        self.repo.register_session(CallerIdentity(fixture.THREAD))

    def hot(self):
        crash(self.repo.path)
        with self.assertRaises(UsageRecoveryRequired) as caught:
            self.repo.inspect()
        self.assertEqual(caught.exception.code, "usage_unavailable")
        return caught.exception

    def test_readonly_inspect_and_preview_preserve_hot_bytes(self):
        self.hot()
        before = snapshot(self.repo.path)
        for _ in range(2):
            with self.assertRaises(UsageRecoveryRequired):
                admit(self.repo, self.target, source="setup", read_only=True)
            self.assertEqual(snapshot(self.repo.path), before)
        self.assertFalse(self.target.resolved_usage_runtime.exists())

    def test_readonly_task_projection_reports_unknown_without_recovery(self):
        from task_governance_tool.usage_evidence_service import read_task_usage
        from task_governance_tool.storage import connect_initialized_readonly
        self.hot()
        before = snapshot(self.repo.path)
        with closing(connect_initialized_readonly(self.target)) as core:
            result = read_task_usage(self.target, core, "tg_task_" + "a" * 16)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["periods"], [])
        self.assertEqual(snapshot(self.repo.path), before)
        self.assertFalse(self.target.resolved_usage_runtime.exists())

    def test_hook_recovers_committed_data_without_adding_uncommitted_rows(self):
        self.hot()
        self.assertEqual(admit(self.repo, self.target, source="hook"), ("current", True))
        self.assertEqual(self.repo.registered_sources()[0], {fixture.THREAD})
        self.assertEqual(self.repo.inspect(), "current")
        self.assertIsNone(snapshot(self.repo.path)[1])

    def test_service_requires_accepted_request_and_current_roots(self):
        self.hot()
        before = snapshot(self.repo.path)
        with self.assertRaises(Exception):
            admit(self.repo, self.target, source="service", roots=locator_root_fingerprints(self.fixture.roots))
        self.assertEqual(snapshot(self.repo.path), before)
        self.assertFalse(self.target.resolved_usage_runtime.exists())
        with state.lease(self.target, "state", create=True) as root:
            state.request(root, self.target, locator_root_fingerprints(self.fixture.roots))
        self.assertEqual(admit(self.repo, self.target, source="service",
                              roots=locator_root_fingerprints(self.fixture.roots)), ("current", True))

    def test_busy_execution_defers_then_later_recovers(self):
        self.hot()
        before = snapshot(self.repo.path)
        with state.lease(self.target, "execution", create=True):
            with self.assertRaises(UsageError) as caught:
                admit(self.repo, self.target, source="hook")
            self.assertEqual(caught.exception.code, "usage_pending")
        self.assertEqual(snapshot(self.repo.path), before)
        self.assertEqual(admit(self.repo, self.target, source="hook"), ("current", True))

    def test_normal_admission_never_opens_recovery_writer_or_lease(self):
        with mock.patch.object(self.repo, "recover_interrupted_read") as recover, \
                mock.patch.object(state, "lease") as lease:
            self.assertEqual(admit(self.repo, self.target, source="hook"), ("current", False))
        recover.assert_not_called()
        lease.assert_not_called()

    def test_recovery_respects_original_deadline(self):
        self.hot()
        before = snapshot(self.repo.path)
        self.repo.deadline = monotonic() - 1
        with self.assertRaises(UsageError):
            admit(self.repo, self.target, source="hook")
        self.assertEqual(snapshot(self.repo.path), before)

    def test_changed_file_rejects_saved_failed_admission(self):
        needed = self.hot()
        replacement = self.repo.path.with_suffix(".replacement")
        shutil.copyfile(self.repo.path, replacement)
        replacement.replace(self.repo.path)
        before = snapshot(self.repo.path)
        with self.assertRaises(UsageError) as caught:
            self.repo.recover_interrupted_read(needed)
        self.assertEqual(caught.exception.reason, "physical_changed")
        self.assertEqual(snapshot(self.repo.path), before)

    def test_wal_never_triggers_recovery(self):
        self.hot()
        before = snapshot(self.repo.path)
        sidecar = Path(str(self.repo.path) + "-wal")
        sidecar.write_bytes(b"unsupported")
        with mock.patch.object(self.repo, "recover_interrupted_read") as recover:
            with self.assertRaises(UsageError):
                admit(self.repo, self.target, source="hook")
        recover.assert_not_called()
        self.assertEqual(snapshot(self.repo.path), before)

    def test_binding_mismatch_after_rollback_cannot_collect_business_rows(self):
        self.hot()
        # Wrong binding is discovered only after SQLite can read again.
        self.repo.basis = ("another-project", *self.repo.basis[1:])
        from dataclasses import replace
        target = replace(self.target, project=replace(self.target.project, project_id="another-project"))
        with self.assertRaises(UsageError) as caught:
            admit(self.repo, target, source="hook")
        self.assertEqual(caught.exception.code, "usage_binding_mismatch")
        self.repo.basis = (self.target.project.project_id, *self.repo.basis[1:])
        self.assertEqual(self.repo.registered_sources()[0], {fixture.THREAD})

    def test_missing_database_is_not_created(self):
        self.repo.path.unlink()
        with mock.patch.object(self.repo, "recover_interrupted_read") as recover:
            with self.assertRaises(UsageError):
                admit(self.repo, self.target, source="hook")
        recover.assert_not_called()
        self.assertFalse(self.repo.path.exists())

    def test_schema_error_after_native_rollback_is_not_semantically_repaired(self):
        with self.repo.connection(write=True) as connection:
            connection.execute("DELETE FROM usage_migrations WHERE version=10")
        self.hot()
        with self.assertRaises(UsageError) as caught:
            admit(self.repo, self.target, source="hook")
        self.assertEqual(caught.exception.code, "usage_schema_invalid")
        with closing(sqlite3.connect(self.repo.path)) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM usage_migrations WHERE version=10").fetchone())

    def test_permission_failure_is_single_attempt_and_later_event_can_recover(self):
        self.hot()
        before = snapshot(self.repo.path)
        with mock.patch("task_governance_tool.usage_recovery_storage.connect_existing",
                        side_effect=PermissionError) as opener:
            with self.assertRaises(UsageError) as caught:
                admit(self.repo, self.target, source="hook")
            self.assertEqual(caught.exception.reason, "permission")
            self.assertEqual(opener.call_count, 1)
        self.assertEqual(snapshot(self.repo.path), before)
        self.assertEqual(admit(self.repo, self.target, source="hook"), ("current", True))

    def test_wrapped_header_permission_failure_retains_fixed_reason(self):
        from task_governance_tool.sqlite_connection import sqlite_header_uses_wal, StorageError
        from task_governance_tool.usage_recovery_storage import failure
        with mock.patch("task_governance_tool.sqlite_connection.file_reader", side_effect=PermissionError("private")):
            with self.assertRaises(StorageError) as caught:
                sqlite_header_uses_wal(self.repo.path)
        self.assertEqual(failure(caught.exception), "permission")
        with mock.patch("task_governance_tool.usage_repository.connect_readonly", side_effect=caught.exception):
            with self.assertRaises(UsageError) as admitted:
                admit(self.repo, self.target, source="hook")
        self.assertEqual(admitted.exception.reason, "permission")

    def test_wrapped_physical_permission_failure_retains_fixed_reason(self):
        from task_governance_tool import state_paths
        from task_governance_tool.usage_recovery_storage import failure
        with mock.patch.object(Path, "lstat", side_effect=PermissionError("private")):
            with self.assertRaises(state_paths.StatePathError) as caught:
                state_paths.inspect_physical_file(self.repo.path, root=self.repo.path.parent)
        self.assertEqual(failure(caught.exception), "permission")
        with mock.patch.object(self.repo, "_physical", side_effect=caught.exception):
            with self.assertRaises(UsageError) as admitted:
                admit(self.repo, self.target, source="hook")
        self.assertEqual(admitted.exception.reason, "permission")
        self.assertEqual(failure(state_paths.StatePathError()), "unavailable")

    def test_same_error_during_recovery_is_not_retried(self):
        self.hot()
        error = sqlite3.OperationalError("not retained")
        error.sqlite_errorcode = sqlite3.SQLITE_READONLY_ROLLBACK
        with mock.patch("task_governance_tool.usage_recovery_storage.connect_existing", side_effect=error) as opener:
            with self.assertRaises(UsageError):
                admit(self.repo, self.target, source="hook")
            self.assertEqual(opener.call_count, 1)

    def test_full_hook_catches_up_after_crash_and_replay_deduplicates(self):
        f = self.fixture
        core_before = f.target.db_path.read_bytes()
        self.hot()
        with mock.patch.object(lifecycle, "_target", return_value=self.target), \
                mock.patch.object(lifecycle, "collection_allowed", return_value=True):
            result = lifecycle.collect_event(f.payload, skill_root=f.root, repo=f.target.project.canonical_repo,
                                             environment=f.environment)
            self.assertGreater(result["collected_sources"], 0)
            f.drain()
            self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
            lifecycle.collect_event(f.payload, skill_root=f.root, repo=f.target.project.canonical_repo,
                                    environment=f.environment)
            f.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertTrue(f.target.resolved_usage_index.exists())
        self.assertEqual(f.target.db_path.read_bytes(), core_before)

    def test_service_initial_admission_recovers_only_saved_intent(self):
        f = self.fixture
        f.intake()
        self.hot()
        service = UsageServiceObserver(skill_root=f.root, repo=f.target.project.canonical_repo,
                                       environment=f.environment, compatible=lambda: True)
        with mock.patch.object(lifecycle, "_target", return_value=self.target), \
                mock.patch("task_governance_tool.setup_feature_config.collection_allowed", return_value=True):
            target = service._eligible_target(monotonic() + 20)
            with state.lease(target, "service"):
                repository = service._admit(target, monotonic() + 20)
        self.assertEqual(target, self.target)
        self.assertEqual(repository.inspect(), "current")
        f.drain()
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_standby_leaves_hot_database_untouched_then_takes_over_once(self):
        from task_governance_tool import usage_continuation, usage_recovery, usage_recovery_storage
        f = self.fixture
        request = f.intake()
        collect = self.repo.collect_source
        with mock.patch.object(self.repo, "collect_source", side_effect=lambda *args, **kwargs:
                collect(*args, **{**kwargs, "max_records": 3})):
            for _ in range(6):
                f.run_slice()
                if self.repo.summary()["models"]:
                    break
        partial = self.repo.summary()["models"][0]["response_count"]
        self.assertTrue(0 < partial < 20)
        self.hot()
        saved, database = f.read(), snapshot(self.repo.path)
        service = f.observer()
        waits = 0
        with ExitStack() as owner, \
                mock.patch.object(usage_recovery, "admit", wraps=admit) as admission, \
                mock.patch.object(usage_recovery_storage, "recover", wraps=usage_recovery_storage.recover) as recovery, \
                mock.patch.object(usage_continuation, "run_slice", wraps=usage_continuation.run_slice) as work:
            owner.enter_context(state.lease(self.target, "service"))
            def advance(seconds):
                nonlocal waits
                waits += 1
                if waits <= 3:
                    self.assertEqual(seconds, service._poll_seconds)
                    self.assertEqual((admission.call_count, recovery.call_count, work.call_count), (0, 0, 0))
                    self.assertEqual((f.read(), snapshot(self.repo.path)), (saved, database))
                    if waits == 3:
                        owner.close()
                elif f.read()["status"] == "caught_up" or waits > 40:
                    service._stopped.set()
                return service._stopped.is_set()
            with mock.patch.object(service._stopped, "wait", side_effect=advance):
                service._run()
            self.assertGreater(waits, 3)
            self.assertGreater(work.call_count, 0)
            recovery.assert_called_once()
        self.assertEqual((f.read()["request"], f.read()["status"]), (request["request"], "caught_up"))
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)

    def test_takeover_rechecks_policy_binding_and_replaced_database(self):
        from dataclasses import replace
        from task_governance_tool import usage_recovery
        from task_governance_tool.usage_evidence_service import repository_for
        f = self.fixture
        saved = f.intake()
        foreign = replace(self.target, binding_generation=self.target.binding_generation + 1)
        for change in ("policy", "binding", "database"):
            with self.subTest(change=change):
                service = f.observer()
                def select(deadline):
                    if selection.call_count == 2:
                        if change == "policy":
                            return None
                        if change == "binding":
                            return foreign
                        self.repo.path.rename(self.repo.path.with_suffix(".original"))
                        repository_for(foreign).initialize()
                    return self.target
                with mock.patch.object(service, "_eligible_target", side_effect=select) as selection, \
                        mock.patch.object(usage_recovery, "admit", wraps=admit) as admission, \
                        mock.patch.object(service, "_observe") as observe:
                    service._run()
                self.assertEqual(selection.call_count, 2)
                self.assertEqual(admission.call_count, int(change == "database"))
                observe.assert_not_called()
                self.assertEqual(f.read(), saved)
                if change == "database":
                    self.assertEqual(repository_for(foreign).summary()["models"], [])

    def test_service_without_an_intent_leaves_existing_runtime_directory_empty(self):
        from task_governance_tool import usage_recovery
        f = self.fixture
        root = state.root_for(self.target, create=True)
        service = f.observer()
        with mock.patch.object(service._stopped, "wait", side_effect=lambda _: service._stopped.set() or True), \
                mock.patch.object(usage_recovery, "admit") as admission:
            service._run()
        admission.assert_not_called()
        self.assertEqual(list(root.iterdir()), [])

    def test_crash_then_partial_tail_and_missing_participant_remain_pending(self):
        f = self.fixture
        f.write(tail=json.dumps(fixture.usage("later")).encode())
        self.hot()
        admit(self.repo, self.target, source="hook")
        f.intake()
        f.add_core_participant(transcript=False)
        for _ in range(14):
            f.run_slice()
        self.assertNotEqual(f.read()["status"], "caught_up")
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 20)
        self.assertIn("partial_tail", self.repo.summary()["diagnostics"])

    def test_hook_protocol_stays_neutral_on_failed_recovery(self):
        f = self.fixture
        self.hot()
        stream = io.StringIO()
        with mock.patch.object(lifecycle, "_target", return_value=self.target), \
                mock.patch.object(lifecycle, "collection_allowed", return_value=True), \
                mock.patch("task_governance_tool.usage_recovery_storage.connect_existing", side_effect=PermissionError):
            result = lifecycle.main(stdin=io.StringIO(json.dumps(f.payload)), stdout=stream,
                                    skill_root=f.root, repo=f.target.project.canonical_repo, environment=f.environment)
        self.assertEqual((result, stream.getvalue()), (0, "{}\n"))

    def test_native_writer_contention_is_bounded_and_not_a_recovery_trigger(self):
        self.repo.connection_timeout = 0.05
        with closing(sqlite3.connect(self.repo.path)) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            started = monotonic()
            with mock.patch.object(self.repo, "recover_interrupted_read") as recover:
                with self.assertRaises(UsageError) as caught:
                    admit(self.repo, self.target, source="hook")
            self.assertEqual(caught.exception.reason, "busy")
            recover.assert_not_called()
            self.assertLess(monotonic() - started, 2)
        self.assertEqual(admit(self.repo, self.target, source="hook"), ("current", False))

    def test_service_completed_or_wrong_root_intent_cannot_recover(self):
        roots = locator_root_fingerprints(self.fixture.roots)
        self.fixture.intake()
        request = self.fixture.drain()
        pending = state.update(self.target, request, round_=request["round"], status="pending")
        self.hot()
        before = snapshot(self.repo.path)
        with self.assertRaises(UsageError) as caught:
            admit(self.repo, self.target, source="service", roots=[])
        self.assertEqual(caught.exception.reason, "intent_required")
        state.update(self.target, pending, round_=pending["round"], status="caught_up")
        with self.assertRaises(UsageError):
            admit(self.repo, self.target, source="service", roots=roots)
        self.assertEqual(snapshot(self.repo.path), before)

    def test_changed_intent_under_execution_lease_cannot_recover(self):
        roots = locator_root_fingerprints(self.fixture.roots)
        with state.lease(self.target, "state", create=True) as root:
            request = state.request(root, self.target, roots)
        self.hot()
        before = snapshot(self.repo.path)
        with mock.patch.object(state, "read", side_effect=[request, None]):
            with self.assertRaises(UsageError) as caught:
                admit(self.repo, self.target, source="service", roots=roots)
        self.assertEqual(caught.exception.reason, "intent_required")
        self.assertEqual(snapshot(self.repo.path), before)

    def test_hardlinked_journal_is_rejected_before_sqlite_recovery(self):
        self.hot()
        before = snapshot(self.repo.path)
        journal = Path(str(self.repo.path) + "-journal")
        alias = journal.with_name("retained-journal")
        os.link(journal, alias)
        with self.assertRaises(UsageError):
            admit(self.repo, self.target, source="hook")
        self.assertEqual(snapshot(self.repo.path), before)

    def test_new_participant_is_rediscovered_after_recovery_without_later_hook(self):
        f = self.fixture
        request = f.intake()
        self.hot()
        f.add_core_participant()
        admit(self.repo, self.target, source="service", roots=locator_root_fingerprints(f.roots))
        self.assertEqual(f.drain()["request"], request["request"])
        self.assertEqual(self.repo.summary()["models"][0]["response_count"], 21)

    def test_process_interrupted_during_recovery_is_recoverable_on_next_admission(self):
        self.hot()
        code = """import os,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_values import UsageRecoveryRequired
r=UsageReconciliationRepository(Path(sys.argv[2]),sys.argv[3],sys.argv[4],int(sys.argv[5]))
try:
    r.inspect()
except UsageRecoveryRequired as needed:
    original=r._validate
    def interrupted(c):
        c.set_progress_handler(lambda: os._exit(8), 1)
        return original(c)
    r._validate=interrupted
    r.recover_interrupted_read(needed)
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
            str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"),
            str(self.repo.path), *(str(value) for value in self.repo.basis)],
            capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertEqual(admit(self.repo, self.target, source="hook")[0], "current")
        self.assertEqual(self.repo.registered_sources()[0], {fixture.THREAD})

    def test_service_retry_after_initial_failure_and_restart_keeps_saved_cursor(self):
        f = self.fixture
        request = f.intake()
        collect = self.repo.collect_source
        with mock.patch.object(self.repo, "collect_source", side_effect=lambda *args, **kwargs:
                collect(*args, **{**kwargs, "max_records": 3})):
            for _ in range(6):
                f.run_slice()
                if self.repo.summary()["models"]:
                    break
        partial = self.repo.summary()["models"][0]["response_count"]
        self.assertLess(partial, 20)
        self.hot()
        service = UsageServiceObserver(skill_root=f.root, repo=f.target.project.canonical_repo,
                                       environment=f.environment, compatible=lambda: True)
        with mock.patch.object(lifecycle, "_target", return_value=self.target), \
                mock.patch("task_governance_tool.setup_feature_config.collection_allowed", return_value=True):
            with mock.patch("task_governance_tool.usage_recovery_storage.connect_existing", side_effect=PermissionError):
                with self.assertRaises(UsageError):
                    with state.lease(self.target, "service"):
                        service._admit(self.target, monotonic() + 20)
            self.assertEqual(f.read()["request"], request["request"])
            # A fresh observer simulates process restart; no new Hook or intent.
            restarted = UsageServiceObserver(skill_root=f.root, repo=f.target.project.canonical_repo,
                                              environment=f.environment, compatible=lambda: True)
            with state.lease(self.target, "service"):
                f.repo = restarted._admit(self.target, monotonic() + 20)
            self.assertEqual(f.repo.summary()["models"][0]["response_count"], partial)
            self.assertEqual(f.drain()["request"], request["request"])
            self.assertEqual(f.repo.summary()["models"][0]["response_count"], 20)

    def test_service_collection_diagnostics_distinguish_unavailable_pending_and_caught_up(self):
        from task_governance_tool import usage_continuation
        f = self.fixture
        f.intake()
        for status, outcome, reason in (("unavailable", "unavailable", "unavailable"),
                ("waiting_input", "pending", "input_pending"), ("caught_up", "succeeded", "none")):
            with self.subTest(status=status):
                service = UsageServiceObserver(skill_root=f.root, repo=f.target.project.canonical_repo,
                                               environment=f.environment, compatible=lambda: True)
                def one_slice(*args, **kwargs):
                    service._stopped.set()
                    return {"status": status}
                with mock.patch.object(service, "_eligible_target", return_value=self.target), \
                        mock.patch.object(service, "_admit", return_value=self.repo), \
                        mock.patch.object(usage_continuation, "run_slice", side_effect=one_slice), \
                        mock.patch("task_governance_tool.debug_log.usage_diagnostic") as diagnostic:
                    service._observe(self.target, f.roots)
                diagnostic.assert_called_once_with("collection", outcome, reason)

    def test_running_service_recovers_after_transient_post_lease_failure(self):
        from task_governance_tool import usage_recovery_storage as recovery
        from task_governance_tool.usage_continuation_service import RETRY_SECONDS
        f = self.fixture
        for reason in ("permission", "busy", "budget", "readonly_rollback", "expired_budget",
                       "intent_permission", "execution_permission", "state_permission"):
            with self.subTest(reason=reason):
                f.intake()
                service = UsageServiceObserver(skill_root=f.root, repo=f.target.project.canonical_repo,
                                               environment=f.environment, compatible=lambda: True)
                original_admit = service._admit
                admissions, waits = [], []
                failed_request = None
                failed_bytes = None
                def admission(target, deadline):
                    nonlocal failed_request, failed_bytes
                    admissions.append(deadline)
                    if len(admissions) == 1:
                        self.hot()
                        failed_request, failed_bytes = f.read(), snapshot(self.repo.path)
                        if reason == "expired_budget":
                            # Exercise the producer's real usage_pending shape.
                            return original_admit(target, monotonic() - 1)
                        if reason in {"intent_permission", "execution_permission", "state_permission"}:
                            filename = {"intent_permission": "continuation.json",
                                        "execution_permission": "execution.lock",
                                        "state_permission": "state.lock"}[reason]
                            original_open = os.open
                            def denied_intent(path, *args, **kwargs):
                                if Path(path).name == filename:
                                    raise PermissionError("private")
                                return original_open(path, *args, **kwargs)
                            # Actual physical-reader and lock wrappers retain native causes.
                            with mock.patch("task_governance_tool.state_paths.os.open", side_effect=denied_intent):
                                return original_admit(target, deadline)
                        with mock.patch.object(recovery, "connect_existing", side_effect=UsageError(reason=reason)):
                            return original_admit(target, deadline)
                    return original_admit(target, deadline)
                def yielded(seconds):
                    waits.append(seconds)
                    self.assertEqual(f.read(), failed_request)
                    self.assertEqual(snapshot(self.repo.path), failed_bytes)
                    # The failed service attempt must have released its lease.
                    with state.lease(self.target, "service"):
                        pass
                    if len(waits) > 2:
                        service._stopped.set()
                    return service._stopped.is_set()
                def observed(target, roots):
                    self.assertEqual(target, self.target)
                    self.assertEqual(f.read()["request"], failed_request["request"])
                    self.assertEqual(self.repo.inspect(), "current")
                    service._stopped.set()
                with mock.patch.object(lifecycle, "_target", return_value=self.target), \
                        mock.patch("task_governance_tool.setup_feature_config.collection_allowed", return_value=True), \
                        mock.patch.object(service, "_admit", side_effect=admission), \
                        mock.patch.object(service._stopped, "wait", side_effect=yielded), \
                        mock.patch.object(service, "_observe", side_effect=observed) as observe:
                    service._run()
                self.assertEqual(len(admissions), 2)
                self.assertEqual(waits, [RETRY_SECONDS, service._poll_seconds])
                observe.assert_called_once()
                self.assertIsNone(snapshot(self.repo.path)[1])

    def test_running_service_does_not_retry_invalid_post_lease_operational_state(self):
        f = self.fixture
        f.intake()
        service = UsageServiceObserver(skill_root=f.root, repo=f.target.project.canonical_repo,
                                       environment=f.environment, compatible=lambda: True)
        with mock.patch.object(service, "_eligible_target", return_value=self.target), \
                mock.patch.object(state, "read", side_effect=UsageError()), \
                mock.patch.object(service._stopped, "wait") as wait, \
                mock.patch.object(service, "_observe") as observe:
            service._run()
        wait.assert_not_called()
        observe.assert_not_called()
        self.assertFalse(service._stopped.is_set())

    def test_running_service_rejects_invalid_recovery_locks_without_retry(self):
        f = self.fixture
        for name in ("state.lock", "execution.lock"):
            with self.subTest(name=name):
                f.intake()
                service = UsageServiceObserver(skill_root=f.root, repo=f.target.project.canonical_repo,
                                               environment=f.environment, compatible=lambda: True)
                original_admit = service._admit
                calls, saved = [], {}
                path = self.target.resolved_usage_runtime / name
                def admission(target, deadline):
                    calls.append(deadline)
                    if len(calls) == 1:
                        self.hot()
                        saved.update(request=f.read(), database=snapshot(self.repo.path), lock=path.read_bytes())
                        path.write_bytes(b"invalid")
                    return original_admit(target, deadline)
                try:
                    with mock.patch.object(lifecycle, "_target", return_value=self.target), \
                            mock.patch("task_governance_tool.setup_feature_config.collection_allowed", return_value=True), \
                            mock.patch.object(service, "_admit", side_effect=admission), \
                            mock.patch.object(service._stopped, "wait") as wait, \
                            mock.patch.object(service, "_observe") as observe:
                        service._run()
                    self.assertEqual(len(calls), 1)
                    wait.assert_not_called()
                    observe.assert_not_called()
                    self.assertEqual(path.read_bytes(), b"invalid")
                    self.assertEqual(snapshot(self.repo.path), saved["database"])
                finally:
                    if saved:
                        path.write_bytes(saved["lock"])
                self.assertEqual(f.read(), saved["request"])
                admit(self.repo, self.target, source="hook")


class PublicationRecoveryTests(unittest.TestCase):
    def test_late_completed_task_usage_reallocates_and_publishes_after_recovery(self):
        from dataclasses import replace
        from tests.test_usage_reconciliation_changes_runtime import ChangedRuntimeTests
        from tests.review_test_helpers import seed_review_evidence_connection
        from tests.test_usage_attribution import turn
        from task_governance_tool import tasks, storage
        from task_governance_tool import usage_evidence_service as service
        add = tasks.add_task
        def add_fixture(*args, **kwargs):
            return add(*args, **kwargs, review_tier=0,
                       verification_not_required_reason="Isolated numerical recovery fixture")
        with mock.patch.object(tasks, "add_task", side_effect=add_fixture):
            f = ChangedRuntimeTests()
            f.setUp()
        self.addCleanup(f.doCleanups)
        target = replace(f.fixture.target, binding_path_hash=f.repository.basis[1], binding_generation=1)
        target.resolved_usage_database.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f.repository.path, target.resolved_usage_database)
        f.repository = f.fixture.repository = service.repository_for(target)
        f.fixture.target = target
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": fixture.THREAD}):
            with closing(storage.connect_initialized(target)) as core:
                seed_review_evidence_connection(core, f.fixture.task_id, repo_path=target.project.canonical_repo)
                core.commit()
            f.transition("done", "finished", verification_complete=True, review_complete=True,
                         commit_not_required=True)
        before_index = f.publish_same()
        with f.reader() as core:
            before = f.repository.read(core, task_id=f.fixture.task_id)
        core_bytes = target.db_path.read_bytes()
        cursor = f.repository.cursor(f.fixture.source.source_id, fixture.THREAD)
        with f.fixture.source.path.open("ab") as stream:
            stream.write(json.dumps(fixture.usage("late-after-completion", turn=turn(1))).encode() + b"\n")
        crash(f.repository.path)
        self.assertEqual(admit(f.repository, target, source="hook"), ("current", True))
        self.assertEqual(f.repository.cursor(f.fixture.source.source_id, fixture.THREAD), cursor)
        f.fixture.ingest()
        after_index = f.publish_same()
        with f.reader() as core:
            after = f.repository.read(core, task_id=f.fixture.task_id)
        self.assertEqual(after["total"]["response_count"], before["total"]["response_count"] + 1)
        self.assertEqual(after["periods"][0]["completion_cycle_id"], before["periods"][0]["completion_cycle_id"])
        self.assertTrue(set(before_index["snapshots"]) <= set(after_index["snapshots"]))
        self.assertNotEqual(before_index["current_snapshot_ids"], after_index["current_snapshot_ids"])
        f.fixture.ingest()
        self.assertEqual(f.publish_same(), after_index)
        self.assertEqual(target.db_path.read_bytes(), core_bytes)


if __name__ == "__main__":
    unittest.main()
