from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.m23_test_support import (
    held_analysis_tree_snapshot as _held_tree_snapshot,
    write_evidence_tree,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import analysis_outbox  # noqa: E402
from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402
from task_governance_tool.analysis_contracts import default_recipe  # noqa: E402
from task_governance_tool.analysis_outbox import (  # noqa: E402
    AnalysisOutboxSession,
    AnalysisOutboxError,
    enqueue_analysis_source,
    replace_analysis_status,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    ValidatedEvidenceSource,
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.state_paths import analysis_state_paths  # noqa: E402
from task_governance_tool.state_resolver import canonical_state_paths  # noqa: E402


def _force_close_lease(lease) -> None:
    """Test-only cleanup after asserting a deliberate fail-fast retention."""

    if not lease._byte_lock.released:
        win32_boundary.unlock_byte_zero(lease._byte_lock)
    for handle in (
        lease._lock_handle,
        lease._root_handle,
        lease._parent_handle,
    ):
        if not handle.closed:
            handle.close()
    if not lease._security.closed:
        lease._security.close()


class _FakeLeaseOwnership:
    def __init__(self, real=None) -> None:
        self.events: list[str] = []
        self.real = real
        self.root = object()

    def borrow_root(self):
        if self.real is not None:
            return self.real.borrow_root()
        return self.root

    def release_normal(self) -> None:
        self.events.extend(("unlock", "close"))
        if self.real is not None:
            self.real.release_normal()

    def retain_for_quarantine(self) -> None:
        self.events.append("retain")
        if self.real is not None:
            self.real.retain_for_quarantine()


class _FakeDirectories:
    def prove(self, _root) -> None:
        return None

    def close(self) -> None:
        return None


class AnalysisOutboxTests(unittest.TestCase):
    def _legacy_source(self, root: Path):
        index = read_evidence_index(write_evidence_tree(root / "source"))
        entry = next(
            item for item in index.entries if item["bundle_state"] == "legacy_unknown"
        )
        return validate_evidence_source(index, entry)

    def test_canonical_paths_and_descriptor_before_status_enqueue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            fixed_root = root / "skill" / "state" / "current"
            fixed_root.mkdir(parents=True)
            paths = analysis_state_paths(fixed_root)
            resolved = canonical_state_paths(root / "skill")
            self.assertEqual(resolved.analysis_root, paths.root)
            self.assertEqual(resolved.analysis_temporary, paths.temporary)

            source = self._legacy_source(root)
            order: list[str] = []
            original = analysis_outbox._create_relative_durable_file

            def observed(parent, basename, data, **kwargs):
                order.append(kwargs["kind"])
                return original(parent, basename, data, **kwargs)

            with patch.object(
                analysis_outbox,
                "_create_relative_durable_file",
                side_effect=observed,
            ):
                result = enqueue_analysis_source(
                    paths=paths,
                    source=source,
                    recipe=default_recipe(),
                )
            self.assertFalse(result.replayed)
            self.assertEqual(
                order[:2],
                ["analysis-descriptor-th", "analysis-status-th"],
            )
            job_file = f"{result.descriptor['analysis_job_id']}.json"
            self.assertTrue((paths.outbox / job_file).is_file())
            self.assertTrue((paths.status / job_file).is_file())
            self.assertFalse(any(paths.temporary.iterdir()))

    def test_generation_drift_replays_original_and_status_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            source = self._legacy_source(root)
            first = enqueue_analysis_source(
                paths=paths,
                source=source,
                recipe=default_recipe(),
            )

            drift_basis = deepcopy(source.source_basis)
            drift_basis["projection_generation"] += 1
            drift_basis["index_digest"] = "sha256:" + "9" * 64
            replay = enqueue_analysis_source(
                paths=paths,
                source=ValidatedEvidenceSource(
                    source_kind=source.source_kind,
                    source_basis=drift_basis,
                    source=None,
                ),
                recipe=default_recipe(),
            )
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.descriptor, first.descriptor)

            running = deepcopy(first.status)
            running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": "sha256:" + "a" * 64,
                }
            )
            running = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=first.status,
                status=running,
            )
            failed = deepcopy(running)
            failed.update({"state": "failed", "fixed_code": "interrupted"})
            replaced = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=running,
                status=failed,
            )
            self.assertEqual(replaced, failed)
            replay_terminal = enqueue_analysis_source(
                paths=paths,
                source=source,
                recipe=default_recipe(),
            )
            self.assertEqual(replay_terminal.status, failed)
            self.assertFalse(
                any(path.name.startswith(".taskgov-analysis-status-") for path in paths.status.iterdir())
            )

    def test_status_compare_and_transition_rejects_rollback_decrease_and_r3_substitution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            first = enqueue_analysis_source(
                paths=paths,
                source=self._legacy_source(root),
                recipe=default_recipe(),
            )
            running = deepcopy(first.status)
            running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": "sha256:" + "a" * 64,
                }
            )
            running = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=first.status,
                status=running,
            )

            retry = deepcopy(running)
            retry["worker_attempt_count"] = 2
            before = _held_tree_snapshot(paths)
            with self.assertRaises(AnalysisOutboxError) as stale:
                replace_analysis_status(
                    paths=paths,
                    descriptor=first.descriptor,
                    expected_status=first.status,
                    status=retry,
                )
            self.assertEqual(stale.exception.code, "analysis_collision")
            self.assertEqual(_held_tree_snapshot(paths), before)

            retry = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=running,
                status=retry,
            )

            timed_retry = deepcopy(retry)
            timed_retry["duration_ms"] = 10
            retry = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=retry,
                status=timed_retry,
            )

            for name, mutate in (
                ("counter_decrease", lambda value: value.update({"worker_attempt_count": 1})),
                ("duration_decrease", lambda value: value.update({"duration_ms": 9})),
            ):
                invalid = deepcopy(retry)
                mutate(invalid)
                before = _held_tree_snapshot(paths)
                with self.subTest(name=name), self.assertRaises(AnalysisOutboxError):
                    replace_analysis_status(
                        paths=paths,
                        descriptor=first.descriptor,
                        expected_status=retry,
                        status=invalid,
                    )
                self.assertEqual(_held_tree_snapshot(paths), before)

            intent = deepcopy(retry)
            intent.update(
                {
                    "report_id": "tg_analysis_report_0123456789abcdef",
                    "report_digest": "sha256:" + "b" * 64,
                    "render_digest": "sha256:" + "c" * 64,
                }
            )
            intent = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=retry,
                status=intent,
            )
            substituted = deepcopy(intent)
            substituted["report_id"] = "tg_analysis_report_fedcba9876543210"
            before = _held_tree_snapshot(paths)
            with self.assertRaises(AnalysisOutboxError):
                replace_analysis_status(
                    paths=paths,
                    descriptor=first.descriptor,
                    expected_status=intent,
                    status=substituted,
                )
            self.assertEqual(_held_tree_snapshot(paths), before)

            terminal = deepcopy(intent)
            terminal.update(
                {
                    "state": "failed",
                    "fixed_code": "publication_failed",
                    "report_id": None,
                    "report_digest": None,
                    "render_digest": None,
                }
            )
            terminal = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=intent,
                status=terminal,
            )
            rollback = deepcopy(terminal)
            rollback.update({"state": "running", "fixed_code": None})
            before = _held_tree_snapshot(paths)
            with self.assertRaises(AnalysisOutboxError):
                replace_analysis_status(
                    paths=paths,
                    descriptor=first.descriptor,
                    expected_status=terminal,
                    status=rollback,
                )
            self.assertEqual(_held_tree_snapshot(paths), before)

    def test_optional_phase_dfa_rejections_are_cas_no_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            first = enqueue_analysis_source(
                paths=paths,
                source=self._legacy_source(root),
                recipe=default_recipe(
                    inference_mode="codex_optional",
                    declared_model_id="fixed-mock",
                ),
            )
            ready = deepcopy(first.status)
            ready.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": "sha256:" + "a" * 64,
                }
            )
            ready = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=first.status,
                status=ready,
            )

            counter_and_outcome = deepcopy(ready)
            counter_and_outcome.update(
                {"adapter_attempt_count": 1, "inference_state": "timeout"}
            )
            before = _held_tree_snapshot(paths)
            with self.assertRaises(AnalysisOutboxError):
                replace_analysis_status(
                    paths=paths,
                    descriptor=first.descriptor,
                    expected_status=ready,
                    status=counter_and_outcome,
                )
            self.assertEqual(_held_tree_snapshot(paths), before)

            pre_call = deepcopy(ready)
            pre_call.update(
                {"adapter_attempt_count": 1, "inference_state": "running"}
            )
            pre_call = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=ready,
                status=pre_call,
            )
            timeout = deepcopy(pre_call)
            timeout["inference_state"] = "timeout"
            timeout = replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=pre_call,
                status=timeout,
            )

            for name, invalid in (
                (
                    "outcome_substitution",
                    {**timeout, "inference_state": "failed"},
                ),
                (
                    "retry_and_old_outcome_same_write",
                    {**timeout, "adapter_attempt_count": 2},
                ),
            ):
                before = _held_tree_snapshot(paths)
                with self.subTest(name=name), self.assertRaises(AnalysisOutboxError):
                    replace_analysis_status(
                        paths=paths,
                        descriptor=first.descriptor,
                        expected_status=timeout,
                        status=invalid,
                    )
                self.assertEqual(_held_tree_snapshot(paths), before)

            retry = deepcopy(timeout)
            retry.update(
                {"adapter_attempt_count": 2, "inference_state": "running"}
            )
            self.assertEqual(
                replace_analysis_status(
                    paths=paths,
                    descriptor=first.descriptor,
                    expected_status=timeout,
                    status=retry,
                ),
                retry,
            )

    def test_pending_allows_only_prepacket_failure_terminal_without_partial_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            first = enqueue_analysis_source(
                paths=paths,
                source=self._legacy_source(root),
                recipe=default_recipe(),
            )
            forbidden = deepcopy(first.status)
            forbidden.update(
                {
                    "state": "cancelled",
                    "worker_attempt_count": 1,
                    "fixed_code": "cancelled",
                }
            )
            before = _held_tree_snapshot(paths)
            with self.assertRaises(AnalysisOutboxError):
                replace_analysis_status(
                    paths=paths,
                    descriptor=first.descriptor,
                    expected_status=first.status,
                    status=forbidden,
                )
            self.assertEqual(_held_tree_snapshot(paths), before)

            failed = deepcopy(first.status)
            failed.update(
                {
                    "state": "failed",
                    "worker_attempt_count": 1,
                    "fixed_code": "packet_too_large",
                }
            )
            self.assertEqual(
                replace_analysis_status(
                    paths=paths,
                    descriptor=first.descriptor,
                    expected_status=first.status,
                    status=failed,
                ),
                failed,
            )

    def test_explicit_session_holds_one_lease_across_read_and_cas_then_releases_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            first = enqueue_analysis_source(
                paths=paths,
                source=self._legacy_source(root),
                recipe=default_recipe(),
            )
            leases: list[_FakeLeaseOwnership] = []
            real_acquire = analysis_outbox._acquire_analysis_lease

            def observed_acquire(selected_paths):
                lease = _FakeLeaseOwnership(real_acquire(selected_paths))
                leases.append(lease)
                return lease

            with patch.object(
                analysis_outbox,
                "_acquire_analysis_lease",
                side_effect=observed_acquire,
            ):
                session = AnalysisOutboxSession.acquire(paths)
                lease = leases[0]
                selected = session.read_bound_job(first.descriptor)
                self.assertEqual(selected.status, first.status)
                running = deepcopy(first.status)
                running.update(
                    {
                        "state": "running",
                        "worker_attempt_count": 1,
                        "packet_digest": "sha256:" + "a" * 64,
                    }
                )
                result = session.cas_status(
                    descriptor=first.descriptor,
                    expected_status=first.status,
                    status=running,
                )
                self.assertEqual(result.disposition, "replaced")
                self.assertTrue(result.applied)
                self.assertEqual(lease.events, [])

                session.release_normal()
                self.assertEqual(lease.events, ["unlock", "close"])
                self.assertEqual(session.state, "released")
                with self.assertRaises(AnalysisOutboxError):
                    session.release_normal()
                with self.assertRaises(AnalysisOutboxError):
                    session.read_bound_job(first.descriptor)
                self.assertEqual(lease.events, ["unlock", "close"])

    def test_held_root_and_lock_reject_concurrent_split_brain_rename(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixed_root = Path(temporary) / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            session = AnalysisOutboxSession.acquire(paths)
            lease = session._lease
            root_before = win32_boundary.query_handle_identity(lease._root_handle)
            lock_before = win32_boundary.query_handle_identity(lease._lock_handle)
            outcomes: list[tuple[str, int | None]] = []

            def race() -> None:
                for name, source, destination in (
                    ("root", paths.root, fixed_root / "analysis-raced"),
                    ("lock", paths.lock, paths.root / "taskgov-analysis-raced.lock"),
                ):
                    try:
                        os.replace(source, destination)
                    except OSError as failure:
                        outcomes.append((name, getattr(failure, "winerror", None)))
                    else:
                        outcomes.append((name, None))

            worker = threading.Thread(target=race, daemon=True)
            try:
                worker.start()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertEqual([name for name, _ in outcomes], ["root", "lock"])
                self.assertTrue(all(error is not None for _, error in outcomes))
                session._prove_session_directories()
                self.assertTrue(
                    root_before.same_object(
                        win32_boundary.query_handle_identity(lease._root_handle)
                    )
                )
                self.assertTrue(
                    lock_before.same_object(
                        win32_boundary.query_handle_identity(lease._lock_handle)
                    )
                )
            finally:
                session.release_normal()
            self.assertFalse((fixed_root / "analysis-raced").exists())
            self.assertFalse(
                (paths.root / "taskgov-analysis-raced.lock").exists()
            )

    def test_status_s0_is_direct_session_lifetime_and_does_not_admit_a_peer(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixed_root = Path(temporary) / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            calls = []
            original_open = win32_boundary.open_or_create_status_directory

            def observed_open(parent, basename, security, **kwargs):
                opened = original_open(parent, basename, security, **kwargs)
                calls.append((parent, basename, security, opened))
                return opened

            with patch.object(
                win32_boundary,
                "open_or_create_status_directory",
                side_effect=observed_open,
            ):
                session = AnalysisOutboxSession.acquire(paths)
                status = session._directories.status
                status_security = session._directories.status_security
                try:
                    root = session._borrow_analysis_root()
                    self.assertEqual(len(calls), 1)
                    parent, basename, security, opened = calls[0]
                    self.assertIs(parent, root)
                    self.assertEqual(basename, paths.status.name)
                    self.assertIs(security, status_security)
                    self.assertIs(opened.handle, status)
                    self.assertEqual(status.kind, "analysis-status-s0")
                    self.assertFalse(status.closed)
                    self.assertFalse(status_security.closed)
                    self.assertEqual(
                        win32_boundary.prove_exact_handle_security(
                            status,
                            status_security,
                        ).policy,
                        "root",
                    )
                    analysis_outbox._AnalysisLeaseOwnership._prove_directory(
                        status,
                        root,
                        paths.status.name,
                        session._directories.status_identity,
                    )
                    before = _held_tree_snapshot(paths, session=session)
                    peer_failure = None
                    with (
                        patch.object(
                            analysis_outbox,
                            "_prepare_locked_tree",
                            wraps=analysis_outbox._prepare_locked_tree,
                        ) as inventory,
                        patch.object(
                            analysis_outbox,
                            "_read_descriptor",
                            wraps=analysis_outbox._read_descriptor,
                        ) as descriptor_read,
                        patch.object(
                            analysis_outbox,
                            "_read_status",
                            wraps=analysis_outbox._read_status,
                        ) as status_read,
                        patch.object(
                            analysis_outbox,
                            "_create_relative_durable_file",
                            wraps=analysis_outbox._create_relative_durable_file,
                        ) as durable_write,
                    ):
                        peer = None
                        try:
                            peer = AnalysisOutboxSession.acquire(paths)
                        except AnalysisOutboxError as failure:
                            peer_failure = failure
                        else:
                            peer.release_normal()
                    self.assertIsNotNone(peer_failure)
                    self.assertEqual(peer_failure.code, "analysis_busy")
                    self.assertTrue(peer_failure.contended)
                    self.assertEqual(inventory.call_count, 0)
                    self.assertEqual(descriptor_read.call_count, 0)
                    self.assertEqual(status_read.call_count, 0)
                    self.assertEqual(durable_write.call_count, 0)
                    self.assertEqual(
                        _held_tree_snapshot(paths, session=session),
                        before,
                    )
                    self.assertEqual(len(calls), 1)
                finally:
                    session.release_normal()
                self.assertTrue(status.closed)
                self.assertTrue(status_security.closed)

    def test_membership_proof_uncertainty_is_quarantined_before_release(self):
        paths = analysis_state_paths(Path("C:/bounded-fixture"))
        lease = _FakeLeaseOwnership()
        directories = _FakeDirectories()

        def fail_proof(_root):
            raise win32_boundary.Win32BoundaryError()

        directories.prove = fail_proof
        with (
            patch.object(
                analysis_outbox,
                "_acquire_analysis_lease",
                return_value=lease,
            ),
            patch.object(
                analysis_outbox,
                "_prepare_locked_tree",
                return_value=directories,
            ),
            self.assertRaises(win32_boundary.Win32QuarantineRequired) as failure,
        ):
            with analysis_outbox._short_lived_session(paths) as session:
                session._borrow_analysis_root()
        self.assertEqual(failure.exception.phase, "analysis_session_membership_unproved")
        self.assertEqual(lease.events, ["retain"])

    def test_lease_release_order_and_uncertainty_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixed_root = Path(temporary) / "normal"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            session = AnalysisOutboxSession.acquire(paths)
            events: list[str] = []
            original_unlock = win32_boundary.unlock_byte_zero
            original_close = win32_boundary.OwnedHandle.close

            def observed_unlock(lock):
                events.append("unlock")
                return original_unlock(lock)

            def observed_close(handle):
                events.append("close:" + handle.kind)
                return original_close(handle)

            with (
                patch.object(
                    win32_boundary,
                    "unlock_byte_zero",
                    side_effect=observed_unlock,
                ),
                patch.object(
                    win32_boundary.OwnedHandle,
                    "close",
                    new=observed_close,
                ),
            ):
                session.release_normal()
            self.assertEqual(
                events[-6:],
                [
                    "close:analysis-status-s0",
                    "close:analysis-outbox-r0",
                    "unlock",
                    "close:analysis-lease",
                    "close:analysis-root",
                    "close:analysis-state-parent",
                ],
            )

        with tempfile.TemporaryDirectory() as temporary:
            fixed_root = Path(temporary) / "unlock-failure"
            fixed_root.mkdir()
            session = AnalysisOutboxSession.acquire(analysis_state_paths(fixed_root))
            lease = session._lease
            try:
                with (
                    patch.object(
                        win32_boundary,
                        "unlock_byte_zero",
                        side_effect=win32_boundary.Win32BoundaryError(),
                    ),
                    self.assertRaises(
                        win32_boundary.Win32QuarantineRequired
                    ) as quarantined,
                ):
                    session.release_normal()
                self.assertIs(quarantined.exception.handle, lease._lock_handle)
                self.assertEqual(session.state, "retained")
                self.assertFalse(lease._byte_lock.released)
            finally:
                _force_close_lease(lease)

        with tempfile.TemporaryDirectory() as temporary:
            fixed_root = Path(temporary) / "close-failure"
            fixed_root.mkdir()
            session = AnalysisOutboxSession.acquire(analysis_state_paths(fixed_root))
            lease = session._lease

            def fail_lock_close(handle):
                if handle is lease._lock_handle:
                    raise win32_boundary.Win32BoundaryError()
                return original_close(handle)

            try:
                with (
                    patch.object(
                        win32_boundary.OwnedHandle,
                        "close",
                        new=fail_lock_close,
                    ),
                    self.assertRaises(
                        analysis_outbox._AnalysisLeaseReleaseUncertain
                    ) as uncertain,
                ):
                    session.release_normal()
                self.assertEqual(session.state, "release_uncertain")
                self.assertFalse(isinstance(uncertain.exception, Exception))
                self.assertFalse(
                    isinstance(uncertain.exception, AnalysisOutboxError)
                )
                self.assertTrue(lease._byte_lock.released)
                self.assertFalse(lease._lock_handle.closed)
            finally:
                _force_close_lease(lease)

    def test_explicit_session_quarantine_retains_without_unlock_or_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixed_root = Path(temporary) / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            paths.root.mkdir()
            lease = _FakeLeaseOwnership()
            with (
                patch.object(
                    analysis_outbox,
                    "_acquire_analysis_lease",
                    return_value=lease,
                ),
                patch.object(
                    analysis_outbox,
                    "_prepare_locked_tree",
                    return_value=_FakeDirectories(),
                ),
            ):
                session = AnalysisOutboxSession.acquire(paths)
                session.retain_for_quarantine()
                self.assertEqual(session.state, "retained")
                self.assertEqual(lease.events, ["retain"])
                with self.assertRaises(AnalysisOutboxError):
                    session.release_normal()
                with self.assertRaises(AnalysisOutboxError):
                    session.read_bound_job({})
                self.assertEqual(lease.events, ["retain"])

            scoped_lease = _FakeLeaseOwnership()

            class _Quarantine(BaseException):
                pass

            with (
                patch.object(
                    analysis_outbox,
                    "_acquire_analysis_lease",
                    return_value=scoped_lease,
                ),
                patch.object(
                    analysis_outbox,
                    "_prepare_locked_tree",
                    return_value=_FakeDirectories(),
                ),
            ):
                with self.assertRaises(_Quarantine):
                    with analysis_outbox.analysis_lease(paths):
                        raise _Quarantine()
            self.assertEqual(scoped_lease.events, ["retain"])

    def test_status_cas_uses_only_typed_atomic_replace_not_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            first = enqueue_analysis_source(
                paths=paths,
                source=self._legacy_source(root),
                recipe=default_recipe(),
            )
            leases: list[_FakeLeaseOwnership] = []
            real_acquire = analysis_outbox._acquire_analysis_lease

            def observed_acquire(selected_paths):
                lease = _FakeLeaseOwnership(real_acquire(selected_paths))
                leases.append(lease)
                return lease

            with patch.object(
                analysis_outbox,
                "_acquire_analysis_lease",
                side_effect=observed_acquire,
            ):
                session = AnalysisOutboxSession.acquire(paths)
                lease = leases[0]
                running = deepcopy(first.status)
                running.update(
                    {
                        "state": "running",
                        "worker_attempt_count": 1,
                        "packet_digest": "sha256:" + "a" * 64,
                    }
                )
                before = _held_tree_snapshot(paths, session=session)
                with patch.object(
                    win32_boundary,
                    "replace_relative_file",
                    side_effect=win32_boundary.Win32BoundaryError(
                        "analysis_replace_not_applied",
                        "analysis replace was not applied",
                    ),
                ):
                    not_applied = session.cas_status(
                        descriptor=first.descriptor,
                        expected_status=first.status,
                        status=running,
                    )
                self.assertEqual(
                    not_applied.disposition,
                    "ambiguous_not_applied",
                )
                self.assertEqual(not_applied.status, first.status)
                self.assertEqual(
                    _held_tree_snapshot(paths, session=session),
                    before,
                )

                applied = session.cas_status(
                    descriptor=first.descriptor,
                    expected_status=first.status,
                    status=running,
                )
                self.assertEqual(applied.disposition, "replaced")
                self.assertEqual(applied.status, running)

                failed = deepcopy(running)
                failed.update({"state": "failed", "fixed_code": "interrupted"})
                before = _held_tree_snapshot(paths, session=session)
                with patch.object(
                    win32_boundary,
                    "replace_relative_file",
                    side_effect=win32_boundary.Win32BoundaryError(
                        "analysis_replace_not_applied",
                        "analysis replace was not applied",
                    ),
                ):
                    not_applied = session.cas_status(
                        descriptor=first.descriptor,
                        expected_status=running,
                        status=failed,
                    )
                self.assertEqual(
                    not_applied.disposition,
                    "ambiguous_not_applied",
                )
                self.assertFalse(not_applied.applied)
                self.assertEqual(not_applied.status, running)
                self.assertEqual(
                    _held_tree_snapshot(paths, session=session),
                    before,
                )
                self.assertEqual(lease.events, [])
                session.release_normal()
                self.assertEqual(lease.events, ["unlock", "close"])

    def test_post_apply_status_mismatch_quarantines_held_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            first = enqueue_analysis_source(
                paths=paths,
                source=self._legacy_source(root),
                recipe=default_recipe(),
            )
            running = deepcopy(first.status)
            running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": "sha256:" + "a" * 64,
                }
            )
            session = AnalysisOutboxSession.acquire(paths)
            leaf = None
            try:
                with (
                    patch.object(
                        analysis_outbox,
                        "_status_from_held_durable_file",
                        return_value=first.status,
                    ),
                    self.assertRaises(
                        win32_boundary.Win32QuarantineRequired
                    ) as quarantined,
                ):
                    session.cas_status(
                        descriptor=first.descriptor,
                        expected_status=first.status,
                        status=running,
                    )
                self.assertEqual(
                    quarantined.exception.phase,
                    "analysis_status_replace_postcondition_unproved",
                )
                self.assertIs(
                    quarantined.exception._resources[0],
                    session._directories.status,
                )
                leaf = quarantined.exception._resources[1]
                self.assertIsInstance(leaf, analysis_outbox._RelativeDurableFile)
                self.assertIs(quarantined.exception.handle, leaf.handle)
                self.assertFalse(leaf.handle.closed)
                self.assertEqual(
                    leaf.basename,
                    f"{first.descriptor['analysis_job_id']}.json",
                )
                self.assertTrue(
                    leaf.identity.same_object(
                        win32_boundary.prove_held_membership(
                            leaf.handle,
                            session._directories.status,
                            leaf.basename,
                        )
                    )
                )
                self.assertEqual(
                    analysis_outbox._status_from_held_durable_file(
                        session._directories.status,
                        leaf,
                        descriptor=first.descriptor,
                    ),
                    running,
                )
            finally:
                if leaf is not None and leaf.handle is not None:
                    analysis_outbox._close_relative_durable_file(
                        session._directories.status,
                        leaf,
                    )
                if session.state == "active":
                    session.release_normal()

            audit = AnalysisOutboxSession.acquire(paths)
            try:
                self.assertEqual(audit.read_bound_job(first.descriptor).status, running)
            finally:
                audit.release_normal()


if __name__ == "__main__":
    unittest.main()
