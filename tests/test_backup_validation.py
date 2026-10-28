"""Canonical backup boundaries with real history and metadata-only diagnostics."""
from collections import Counter
from contextlib import ExitStack, closing, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.test_m17_recovery_hardening import _setup_current
from tests.test_backup_performance import seed_fixture, large_task_specs, timestamp
from tests.review_answer_fixture_support import attach_existing
from tests import verification_receipt_test_support as reviews
from task_governance_tool import backup, storage, state_resolver, cli
from task_governance_tool import backup_metadata_repository as metadata
from task_governance_tool import review_answer_repository as answers


def canonical_history_fixture(root):
    install, target, _ = _setup_current(root)
    def invoke(*args, **options):
        # Reuse operation builders, but exercise the real physical package and
        # resolver instead of their explicit-path legacy identity test seam.
        args = list(args)
        at = args.index("--db")
        del args[at:at + 2]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), \
             mock.patch.object(cli, "cli_script_path", return_value=install.entrypoint):
            code = cli.main(args, _maintenance_enabled=False)
        return SimpleNamespace(returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())
    with mock.patch.object(reviews, "run_taskgov", side_effect=invoke):
        task = reviews.add_task(target.db_path, install.project_root, review_tier=2)
        task_id = task["task_id"]
        generation = reviews.set_target(target.db_path, install.project_root, task_id)
        fixture = attach_existing(db=target.db_path, repo=install.project_root,
            task_id=task_id, target=target, schema_version=storage.SCHEMA_VERSION)
        _, finding, successor = fixture.reassess_with_finding()
        fixture.resolve(finding, successor=successor)
        for result in (reviews.add_receipt(target.db_path, install.project_root, task_id, generation),
                       reviews.completion(target.db_path, install.project_root, task_id)):
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
    specs, events = large_task_specs()
    seed_fixture(root, task_specs=specs, tool_event_specs=events, target=target)
    with closing(storage.connect_initialized_readonly(target)) as connection:
        counts = tuple(connection.execute(
            "SELECT (SELECT COUNT(*) FROM tasks), (SELECT COUNT(*) FROM task_events), "
            "(SELECT COUNT(*) FROM review_receipts), (SELECT COUNT(*) FROM review_continuations), "
            "(SELECT COUNT(*) FROM completion_evidence_bundles)"
        ).fetchone())
    if counts[0] != 501 or counts[1] < 5000 or min(counts[2:]) < 1:
        raise AssertionError("canonical history fixture is incomplete")
    return target, counts


def measured_attempt(target, minute):
    calls, durations = Counter(), Counter()
    with ExitStack() as stack:
        for owner, name in ((backup, "_validate_canonical_artifact_set"),
                            (backup, "_reconcile_v11"), (backup, "_copy"),
                            (backup, "_validate_publication_source"),
                            (backup, "_artifact_observation"),
                            (answers, "validate_storage_rows")):
            real = getattr(owner, name)
            def observed(*args, _real=real, _name=name, **kwargs):
                calls[_name] += 1
                started = time.perf_counter()
                try:
                    return _real(*args, **kwargs)
                finally:
                    durations[_name] += time.perf_counter() - started
            stack.enter_context(mock.patch.object(owner, name, side_effect=observed))
        result = backup.run_routine_backup(target, observed_at=timestamp(minute))
    # No DB rows, paths, raw exception or private values enter this diagnostic.
    print(json.dumps(dict(phase=minute, outcome=result.code, calls=calls, seconds=durations)))
    return result, calls


class BackupValidationTests(unittest.TestCase):
    def test_canonical_large_history_due_current_contention_failure_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            target, counts = canonical_history_fixture(Path(directory))
            self.assertTrue(target.canonical_fixed)
            print(json.dumps(dict(fixture_counts=counts)))
            first, first_calls = measured_attempt(target, 0)
            self.assertEqual(first.code, "succeeded")
            current, current_calls = measured_attempt(target, 1)
            self.assertEqual((current.code, current.attempted), ("current", False))
            self.assertEqual(current_calls, {})
            with backup.managed_backup_lock(target):
                deferred, deferred_calls = measured_attempt(target, 30)
            self.assertEqual(deferred.code, "deferred")
            self.assertEqual(deferred_calls, {})
            second, second_calls = measured_attempt(target, 30)
            self.assertEqual(second.code, "succeeded")
            self.assertEqual(first_calls["_validate_publication_source"], 3)
            self.assertEqual(second_calls["_validate_publication_source"], 3)
            # 3 full independent publication phases, not 6; each retained
            # artifact's recovery classification also traverses its graph once.
            self.assertLessEqual(first_calls["validate_storage_rows"], 10)
            self.assertLessEqual(second_calls["validate_storage_rows"], 15)
            before = backup.discover_managed_backup_metadata(target)
            with mock.patch.object(backup, "_copy", side_effect=OSError("isolated copy failure")):
                failed, _ = measured_attempt(target, 60)
            self.assertEqual(failed.code, "failed")
            self.assertEqual(backup.discover_managed_backup_metadata(target), before)
            recovered, _ = measured_attempt(target, 61)
            self.assertEqual(recovered.code, "succeeded")
            # A real post-publication pruning failure must remain due.
            with mock.patch.object(backup, "_delete", side_effect=OSError("isolated prune failure")):
                failed, _ = measured_attempt(target, 91)
            self.assertEqual(failed.code, "failed")
            state = metadata.read_managed_backup_repository(target)
            self.assertEqual(state.maintenance.backup_last_outcome_code, "failed")
            self.assertEqual(len(state.generations), 4)
            repaired, _ = measured_attempt(target, 92)
            self.assertEqual(repaired.code, "succeeded")
            self.assertEqual(len(metadata.read_managed_backup_repository(target).generations), 3)
            with mock.patch.object(backup, "record_managed_backup",
                                   side_effect=OSError("isolated row-commit crash")):
                residue, _ = measured_attempt(target, 122)
            self.assertEqual(residue.code, "failed")
            with mock.patch.object(backup, "_copy", side_effect=AssertionError("residue must be adopted")):
                adopted, _ = measured_attempt(target, 123)
            self.assertEqual(adopted.code, "current")
            self.assertEqual(metadata.read_managed_backup_repository(target).maintenance.backup_last_outcome_code,
                             "succeeded")

    def test_publication_and_recovery_share_only_their_own_snapshot_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            _, target, _ = _setup_current(Path(directory))
            connections = []
            real = answers.validate_storage_rows
            def audit(connection, **kwargs):
                self.assertTrue(connection.in_transaction)
                connections.append(connection)
                return real(connection, **kwargs)
            with mock.patch.object(answers, "validate_storage_rows", side_effect=audit):
                backup._copy(target, backup._new_metadata(3, published_at=timestamp(0)))
            self.assertEqual(len(connections), 3)
            self.assertEqual(len({id(connection) for connection in connections}), 3)
            path = next(target.resolved_backups_path.glob("*.sqlite"))
            connections.clear()
            with mock.patch.object(answers, "validate_storage_rows", side_effect=audit):
                state_resolver._inspect_database(path, classify_recovery_content=True)
            self.assertEqual(len(connections), 1)


if __name__ == "__main__":
    unittest.main()
