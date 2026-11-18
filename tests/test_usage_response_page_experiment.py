"""Safety gates for the adopted response page, executing the shipped runtime."""

from contextlib import closing
import json
import multiprocessing
import os
from threading import Event
import unittest
from unittest import mock
from tests.current_database_fixture_support import ordinary_current_database, uses_current_database_fixture

from tools.benchmark_usage_response_pages import policy, observe
from tests import test_usage_reconciliation_changes_runtime as changed
from task_governance_tool import storage, usage_reconciliation_runtime as runtime
from task_governance_tool import usage_reconciliation_membership as membership
from task_governance_tool.usage_reconciliation_repository import UsageReconciliationRepository
from task_governance_tool.usage_values import UsageError


def crash_page(target, path, basis):
    """Child owns only the parent's disposable fixture; exit without cleanup."""
    repository = UsageReconciliationRepository(path, *basis)
    def reader():
        return closing(storage.connect_initialized_readonly(target, operation_local=True))
    apply = membership.reconcile_response
    visited = 0
    def crash(connection, *key):
        nonlocal visited
        apply(connection, *key)
        visited += 1
        if visited == 3:
            os._exit(73)
    with policy("candidate"), mock.patch.object(membership, "reconcile_response", crash), \
            mock.patch.object(runtime, "monotonic", return_value=0):
        repository.prepare_publication(reader)
    raise AssertionError("crash was not reached")


def run_usage_service(root):
    """Run the real service on accepted fixture intake until it confirms publication."""
    from task_governance_tool import usage_continuation
    from task_governance_tool.usage_continuation_service import UsageServiceObserver
    finished = Event()
    run_slice = usage_continuation.run_slice

    def observed(*args, **kwargs):
        result = run_slice(*args, **kwargs)
        if result is not None and result["status"] == "caught_up":
            finished.set()
        return result

    observer = UsageServiceObserver(skill_root=root / ".agents/skills/task-governance-tool", repo=root,
        environment={"CODEX_HOME": str(root.parent / "codex-host")}, compatible=lambda: True, _poll_seconds=.02)
    with mock.patch.object(usage_continuation, "run_slice", observed):
        observer.start()
        try:
            if not finished.wait(60):
                raise AssertionError("continuation service did not confirm publication")
        finally:
            if not observer.close():
                raise AssertionError("continuation service did not stop")


def crash_service(root):
    apply = membership.reconcile_response
    visited = 0
    def crash(*args, **kwargs):
        nonlocal visited
        apply(*args, **kwargs)
        visited += 1
        if visited == 3:
            os._exit(74)
    with mock.patch.object(membership, "reconcile_response", crash):
        run_usage_service(root)
    raise AssertionError("service crash point was not reached")


class ResponsePageExperimentTests(unittest.TestCase):
    def test_candidate_uses_unmodified_product_function_and_adopted_policy(self):
        advance = runtime.advance_effects
        with policy("candidate"):
            self.assertIs(runtime.advance_effects, advance)
            self.assertEqual((runtime._RESPONSE_PAGE_SIZE, runtime._RESPONSE_PAGE_SECONDS,
                              runtime._RESPONSE_COMMIT_MARGIN_SECONDS), (128, .250, .050))

    def fixture(self):
        fixture = changed.ChangedRuntimeTests()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare_fixture(real_initialization=not uses_current_database_fixture(self))
        return fixture

    @ordinary_current_database
    def test_zero_one_boundaries_and_multiple_pages_preserve_fifo_and_full_oracle(self):
        for count in (0, 1, 7, 8, 9, 127, 128, 129, 257):
            with self.subTest(count=count):
                fixture = self.fixture()
                keys, expected = fixture.queued_responses(count)
                with policy("candidate"), mock.patch.object(runtime, "monotonic", return_value=0), observe() as (_, pages):
                    self.assertEqual(fixture.assert_frozen_same(), expected)
                remaining = count - 1
                sizes = [] if count == 0 else [1]
                while remaining > 0:
                    size = min(128, remaining)
                    sizes.append(size)
                    remaining -= size
                self.assertEqual([page["units"] for page in pages], sizes)
                with fixture.repository.connection() as connection:
                    self.assertIsNone(connection.execute("SELECT 1 FROM usage_reconcile_response_work").fetchone())

    @ordinary_current_database
    def test_deadline_margin_is_50ms_and_250ms_only_limits_optional_start(self):
        fixture = self.fixture()
        keys, _ = fixture.queued_responses(8)
        # 75ms remains: two 10ms keys can start, a third reaching the 50ms
        # commit margin ends this page. Accidentally using 250ms as margin
        # would stop after the first key and fail this test.
        fixture.repository.deadline = 100.075
        from task_governance_tool import usage_incremental_repository as incremental
        with policy("candidate"), mock.patch.object(incremental, "monotonic", return_value=100), \
                mock.patch.object(runtime, "monotonic", side_effect=[100, 100.010, 100.020, 100.026]), observe() as (_, pages):
            runtime.advance_effects(fixture.repository, fixture.reader)
        self.assertEqual(pages[0]["units"], 3)
        fixture.repository.deadline = float("inf")
        with policy("candidate"), mock.patch.object(runtime, "monotonic", side_effect=[100, 100.251]), observe() as (_, pages):
            runtime.advance_effects(fixture.repository, fixture.reader)
        self.assertEqual(pages[0]["units"], 1)
        fixture.assert_frozen_same()

    def test_single_first_commit_exception_rollback_and_later_resume(self):
        fixture = self.fixture()
        with policy("candidate"):
            fixture.test_response_pages_commit_a_single_key_before_optional_siblings_and_resume_fifo()

    @ordinary_current_database
    def test_new_priority_work_runs_before_the_next_response_page(self):
        from tests.test_usage_collection import THREAD
        from tests.test_usage_attribution import turn
        fixture = self.fixture()
        keys, expected = fixture.queued_responses(129)
        with policy("candidate"):
            self.assertEqual(runtime.advance_effects(fixture.repository, fixture.reader, single_response=True)["key"], keys[0])
            with fixture.repository.connection(write=True) as connection:
                connection.execute("INSERT INTO usage_reconcile_overlay_turn_work VALUES (?,?)", (THREAD, turn(1)))
            with mock.patch.object(membership, "reconcile_response", side_effect=AssertionError("priority work skipped")):
                self.assertEqual(runtime.advance_effects(fixture.repository, fixture.reader)["phase"], "turn_overlays")
            self.assertEqual(fixture.assert_frozen_same(), expected)

    def test_process_death_preserves_first_key_and_rolls_back_uncommitted_page(self):
        fixture = self.fixture()
        keys, expected = fixture.queued_responses(129)
        with fixture.repository.connection(write=True) as connection:
            connection.executemany("DELETE FROM usage_reconcile_response_result WHERE provider=? AND response_id=?", keys)
        child = multiprocessing.get_context("spawn").Process(target=crash_page,
            args=(fixture.fixture.target, fixture.repository.path, fixture.repository.basis))
        child.start()
        child.join(45)
        if child.is_alive():
            child.kill()
            child.join()
            self.fail("isolated child did not reach crash point")
        self.assertEqual(child.exitcode, 73)
        fixture.repository = UsageReconciliationRepository(fixture.repository.path, *fixture.repository.basis)
        with fixture.repository.connection() as connection:
            self.assertEqual(list(map(tuple, connection.execute(
                "SELECT provider,response_id FROM usage_reconcile_response_work ORDER BY rowid"))), keys[1:])
            actual = set(map(tuple, connection.execute("SELECT provider,response_id FROM usage_reconcile_response_result")))
            self.assertEqual(actual.intersection(keys), {keys[0]})
        with policy("candidate"):
            self.assertEqual(fixture.assert_frozen_same(), expected)

    def test_later_hook_recovers_process_death_to_identical_public_json(self):
        """A single Hook supplies intake; only the restarted service resumes the work."""
        from tools.benchmark_usage_response_pages import hook_fixture, saved_files, restore, check_publication
        from tools.benchmark_usage_reconciliation_pages import publication_digests
        from task_governance_tool import usage_continuation, usage_continuation_state as state
        from tests.test_usage_attribution import turn
        with hook_fixture(129) as fixture:
            directory = fixture.root / ".taskgov"
            target = fixture.target
            core = target.db_path.read_bytes()
            # A busy execution lease allows normal Hook intake while reserving
            # all reconciliation for the service in both runs.
            with state.lease(target, "execution", create=True):
                fixture.hook("SessionStart", turn_id=turn(1))
            with state.lease(target, "state") as root:
                accepted = state.read(root, target)
            self.assertEqual(accepted["status"], "requested")
            self.assertTrue(accepted["intakes"])
            saved = saved_files(directory)
            run_usage_service(fixture.root)
            expected = publication_digests(target)
            self.assertEqual(check_publication(target), 129)
            self.assertEqual(target.db_path.read_bytes(), core)
            restore(directory, saved)
            child = multiprocessing.get_context("spawn").Process(target=crash_service, args=(fixture.root,))
            child.start()
            child.join(75)
            if child.is_alive():
                child.kill()
                child.join()
                self.fail("isolated service did not reach crash point")
            self.assertEqual(child.exitcode, 74)
            self.assertEqual(target.db_path.read_bytes(), core)
            # Inspect the first resumed slice after native crash recovery has
            # admitted the numerical DB, before the slice advances any work.
            checkpoints = []
            run_slice = usage_continuation.run_slice
            def resumed(target, repository, roots, intent, **kwargs):
                if not checkpoints:
                    with repository.connection() as connection:
                        def keys(table):
                            return set(map(tuple, connection.execute("SELECT provider,response_id FROM " + table)))
                        checkpoints.append((intent["request"], keys("usage_reconcile_response_result"),
                                            keys("usage_reconcile_response_work")))
                return run_slice(target, repository, roots, intent, **kwargs)
            with mock.patch.object(usage_continuation, "run_slice", resumed):
                run_usage_service(fixture.root)
            request, committed, pending = checkpoints[0]
            self.assertEqual(request, accepted["request"])
            self.assertEqual((len(committed), len(pending)), (1, 128))
            self.assertTrue(committed.isdisjoint(pending))
            self.assertEqual(committed | pending, {("openai", f"r-{n}") for n in range(129)})
            self.assertEqual(publication_digests(target), expected)
            self.assertEqual(check_publication(target), 129)
            self.assertEqual(target.db_path.read_bytes(), core)

    def test_changed_core_late_input_partial_diagnostics_and_publication_recovery(self):
        methods = (
            "test_competing_task_update_can_commit_between_response_pages",
            "test_late_duplicate_key_in_other_turn_and_removal_use_actual_reverse_queue",
            "test_pause_and_resume_keep_unassigned_gap_and_old_execution",
            "test_source_replacement_and_diagnostic_removal_reconcile_without_rewriting_history",
            "test_partial_split_retains_predecessors_across_input_change_and_restores",
            "test_base_owner_removal_revisits_original_without_position",
        )
        for name in methods:
            with self.subTest(name=name):
                fixture = self.fixture()
                with policy("candidate"):
                    getattr(fixture, name)()
                    fixture.assert_frozen_same()

    def test_shared_partition_full_replay_and_final_input_guard(self):
        from tests.test_usage_lifecycle_install import UsageLifecycleInstallTests
        from tests.test_usage_response_repository import ResponseRepositoryTests
        from task_governance_tool.usage_adapter import SourceInput
        from task_governance_tool.usage_collection import register_source
        from task_governance_tool.session_identity import CallerIdentity
        from tests.test_usage_collection import THREAD, event, usage
        from tests.test_usage_attribution import turn
        fixture = UsageLifecycleInstallTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        a = fixture.add()
        task_id = a["data"]["task"]["task_id"]
        review = fixture.cli("task", "edit", task_id, "--status", "review_pending")
        done = fixture.complete(task_id)
        b = fixture.add("Next shared participant")
        rows = ResponseRepositoryTests.operation(fixture, "all", a, "shared", direct=True)
        rows[-1]["payload"]["output"] = "\n".join(json.dumps(ack) for ack in (a, review, done, b))
        rows += [usage(f"exclusive-{i}", turn=turn(1)) for i in range(129)]
        source = SourceInput(THREAD, fixture.root / "shared.jsonl", fixture.root, fixture.root)
        register_source(fixture.repository, source, CallerIdentity(THREAD))
        rows = [event("session_meta", id=THREAD, cwd=str(fixture.root), model_provider="openai"),
                event("event_msg", type="task_started", turn_id=turn(1), started_at=1),
                event("turn_context", turn_id=turn(1), model="fixture-model"), *rows]
        source.path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        fixture.repository.collect_source(source)
        checker = changed.ChangedRuntimeTests()
        checker.repository = fixture.repository
        checker.reader = lambda: closing(storage.connect_initialized_readonly(fixture.target, operation_local=True))
        with policy("candidate"):
            index = checker.assert_frozen_same()
            self.assertTrue(any(fixture.repository.publication_snapshot(identity)["assignment"] == "shared"
                                for identity in index["current_snapshot_ids"]))
            index, state = fixture.repository.prepare_publication(checker.reader)
            for identity in index["snapshots"][state["position"]:]:
                fixture.repository.publication_snapshot(identity)
                state = fixture.repository.advance_publication(state)
            with fixture.repository.connection(write=True) as connection:
                connection.execute("UPDATE usage_sources SET pending='partial_tail'")
            with self.assertRaisesRegex(UsageError, "usage_pending"):
                with fixture.repository.publication_guard(checker.reader, index, state):
                    self.fail("stale publication allowed")
            checker.assert_frozen_same()


if __name__ == "__main__":
    unittest.main()
