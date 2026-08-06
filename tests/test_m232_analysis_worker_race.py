from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from multiprocessing.connection import Connection, wait
from pathlib import Path
from unittest.mock import patch

from tests.m23_test_support import write_evidence_tree


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402
from task_governance_tool import analysis_outbox  # noqa: E402
from task_governance_tool import analysis_worker  # noqa: E402
from task_governance_tool.analysis_contracts import default_recipe  # noqa: E402
from task_governance_tool.analysis_outbox import (  # noqa: E402
    AnalysisOutboxSession,
    enqueue_analysis_source,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.state_paths import analysis_state_paths  # noqa: E402


_PHASE_TIMEOUT_SECONDS = 20.0
_JOIN_TIMEOUT_SECONDS = 10.0
_SNAPSHOT_MAX_BYTES = 10_000_000
_COUNTER_KEYS = (
    "prepare_tree",
    "status_s0_open",
    "select_next_job",
    "inventory_descriptor_read",
    "inventory_status_read",
    "bound_descriptor_read",
    "bound_status_read",
    "source_revalidation",
    "packet_build",
    "report_build",
    "status_cas",
    "durable_create",
    "relative_create",
    "status_replace",
    "publish_no_adapter",
    "recover_publication",
    "adapter_prepare",
    "adapter_bind",
    "adapter_execute",
    "adapter_publish",
)


class _RaceGateTimeout(BaseException):
    pass


def _held_tree_snapshot(paths, session: AnalysisOutboxSession) -> tuple[tuple, ...]:
    """Hash one exact analysis tree only through the winner's held handles."""

    opened = []
    rows = []
    try:
        root = session._borrow_analysis_root()
        for entry in win32_boundary.enumerate_held_directory(
            root,
            maximum_entries=6,
        ):
            rows.append(
                (
                    "analysis",
                    entry.name,
                    bytes(entry.file_id).hex(),
                    entry.size,
                    entry.is_directory,
                    entry.is_reparse,
                    None,
                )
            )

        parents = (
            ("outbox", session._directories.outbox),
            ("status", session._directories.status),
        )
        extras = []
        for namespace, basename in (
            ("reports", paths.reports.name),
            ("rendered", paths.rendered.name),
            ("tmp", paths.temporary.name),
        ):
            handle = win32_boundary.open_relative_directory(
                root,
                basename,
                win32_boundary.R0,
                kind="analysis-race-snapshot-" + namespace,
            )
            opened.append(handle)
            extras.append((namespace, handle))

        for namespace, parent in parents + tuple(extras):
            maximum_entries = 32 if namespace == "tmp" else 100_000
            entries = win32_boundary.enumerate_held_directory(
                parent,
                maximum_entries=maximum_entries,
            )
            for entry in entries:
                content_digest = None
                if not entry.is_directory:
                    leaf = win32_boundary.open_relative_file_if_present(
                        parent,
                        entry.name,
                        maximum=_SNAPSHOT_MAX_BYTES,
                        kind="analysis-race-snapshot-leaf",
                    )
                    if leaf is None:
                        raise AssertionError("snapshot leaf disappeared")
                    try:
                        content = win32_boundary.read_handle_capped(
                            leaf,
                            maximum=_SNAPSHOT_MAX_BYTES,
                        )
                    finally:
                        leaf.close()
                    content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
                rows.append(
                    (
                        namespace,
                        entry.name,
                        bytes(entry.file_id).hex(),
                        entry.size,
                        entry.is_directory,
                        entry.is_reparse,
                        content_digest,
                    )
                )
        return tuple(sorted(rows, key=repr))
    finally:
        for handle in reversed(opened):
            handle.close()


def _owned_tree_snapshot(paths) -> tuple[tuple, ...]:
    session = AnalysisOutboxSession.acquire(paths)
    try:
        return _held_tree_snapshot(paths, session)
    finally:
        session.release_normal()


def _read_held_leaf(
    session: AnalysisOutboxSession,
    directory_basename: str,
    leaf_basename: str,
) -> bytes:
    root = session._borrow_analysis_root()
    parent = win32_boundary.open_relative_directory(
        root,
        directory_basename,
        win32_boundary.R0,
        kind="analysis-race-final-parent",
    )
    leaf = None
    try:
        leaf = win32_boundary.open_relative_file_if_present(
            parent,
            leaf_basename,
            maximum=_SNAPSHOT_MAX_BYTES,
            kind="analysis-race-final-leaf",
        )
        if leaf is None:
            raise AssertionError("published leaf is absent")
        return win32_boundary.read_handle_capped(
            leaf,
            maximum=_SNAPSHOT_MAX_BYTES,
        )
    finally:
        if leaf is not None:
            leaf.close()
        parent.close()


def _worker_process(
    slot: int,
    fixed_root: str,
    evidence_index,
    ready,
    start,
    audit_request,
    release,
    sender: Connection,
) -> None:
    """Run one real worker session with a child-local acquisition barrier."""

    counters = {key: 0 for key in _COUNTER_KEYS}
    created_basenames: list[str] = []
    paths = analysis_state_paths(Path(fixed_root))

    def counted(key, original):
        def observed(*args, **kwargs):
            counters[key] += 1
            return original(*args, **kwargs)

        return observed

    original_relative_create = win32_boundary.create_relative_file

    def observed_relative_create(parent, basename, alias, security, **kwargs):
        counters["relative_create"] += 1
        created_basenames.append(basename)
        return original_relative_create(
            parent,
            basename,
            alias,
            security,
            **kwargs,
        )

    original_acquire = analysis_worker._acquire_session

    def paused_acquire(requested_paths):
        session = original_acquire(requested_paths)
        try:
            sender.send(
                {
                    "phase": "lease_acquired",
                    "slot": slot,
                    "counters": dict(counters),
                }
            )
            if not audit_request.wait(_PHASE_TIMEOUT_SECONDS):
                raise _RaceGateTimeout()
            snapshot = _held_tree_snapshot(requested_paths, session)
            sender.send(
                {
                    "phase": "paused_snapshot",
                    "slot": slot,
                    "snapshot": snapshot,
                    "counters": dict(counters),
                }
            )
            if not release.wait(_PHASE_TIMEOUT_SECONDS):
                raise _RaceGateTimeout()
            return session
        except BaseException:
            if session.state == "active":
                session.release_normal()
            raise

    try:
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    analysis_outbox,
                    "_prepare_locked_tree",
                    new=counted(
                        "prepare_tree",
                        analysis_outbox._prepare_locked_tree,
                    ),
                )
            )
            stack.enter_context(
                patch.object(
                    win32_boundary,
                    "open_or_create_status_directory",
                    new=counted(
                        "status_s0_open",
                        win32_boundary.open_or_create_status_directory,
                    ),
                )
            )
            for name, key in (
                ("_read_inventory_descriptor", "inventory_descriptor_read"),
                ("_read_inventory_status", "inventory_status_read"),
                ("_read_descriptor", "bound_descriptor_read"),
                ("_read_status", "bound_status_read"),
                ("_create_relative_durable_file", "durable_create"),
            ):
                stack.enter_context(
                    patch.object(
                        analysis_outbox,
                        name,
                        new=counted(key, getattr(analysis_outbox, name)),
                    )
                )
            stack.enter_context(
                patch.object(
                    win32_boundary,
                    "create_relative_file",
                    new=observed_relative_create,
                )
            )
            stack.enter_context(
                patch.object(
                    win32_boundary,
                    "replace_relative_file",
                    new=counted(
                        "status_replace",
                        win32_boundary.replace_relative_file,
                    ),
                )
            )
            for name, key in (
                ("select_next_job", "select_next_job"),
                ("cas_status", "status_cas"),
                ("publish_no_adapter", "publish_no_adapter"),
                ("recover_publication", "recover_publication"),
            ):
                stack.enter_context(
                    patch.object(
                        AnalysisOutboxSession,
                        name,
                        new=counted(key, getattr(AnalysisOutboxSession, name)),
                    )
                )
            if hasattr(AnalysisOutboxSession, "publish_adapter"):
                stack.enter_context(
                    patch.object(
                        AnalysisOutboxSession,
                        "publish_adapter",
                        new=counted(
                            "adapter_publish",
                            AnalysisOutboxSession.publish_adapter,
                        ),
                    )
                )
            for name, key in (
                ("revalidate_descriptor_source", "source_revalidation"),
                ("build_analysis_packet", "packet_build"),
                ("build_analysis_report", "report_build"),
                ("prepare_closed_mock_input", "adapter_prepare"),
                ("bind_closed_mock_attempt", "adapter_bind"),
                ("execute_prepared_mock_attempt", "adapter_execute"),
            ):
                stack.enter_context(
                    patch.object(
                        analysis_worker,
                        name,
                        new=counted(key, getattr(analysis_worker, name)),
                    )
                )
            stack.enter_context(
                patch.object(
                    analysis_worker,
                    "_acquire_session",
                    new=paused_acquire,
                )
            )

            ready.set()
            if not start.wait(_PHASE_TIMEOUT_SECONDS):
                raise _RaceGateTimeout()
            result = analysis_worker.run_once(paths, evidence_index)
            sender.send(
                {
                    "phase": "result",
                    "slot": slot,
                    "disposition": result.disposition,
                    "analysis_job_id": result.analysis_job_id,
                    "status": result.status,
                    "lease_retained": result.lease_retained,
                    "counters": dict(counters),
                    "created_basenames": tuple(created_basenames),
                }
            )
    except BaseException as failure:
        try:
            sender.send(
                {
                    "phase": "error",
                    "slot": slot,
                    "error_type": type(failure).__name__,
                    "counters": dict(counters),
                }
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        sender.close()


def _next_message(
    active: dict[Connection, int],
    deadline: float,
) -> tuple[Connection, dict]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AssertionError("worker race protocol timed out")
    available = wait(tuple(active), timeout=remaining)
    if not available:
        raise AssertionError("worker race protocol timed out")
    connection = available[0]
    try:
        message = connection.recv()
    except EOFError as failure:
        raise AssertionError("worker race channel closed early") from failure
    if type(message) is not dict or message.get("slot") != active[connection]:
        raise AssertionError("worker race message is invalid")
    return connection, message


def _bounded_join(processes, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    for process in processes:
        if process.pid is not None:
            process.join(max(0.0, deadline - time.monotonic()))


def _force_stop(processes, *, allow_graceful: bool) -> tuple[int, ...]:
    if allow_graceful:
        _bounded_join(processes, _JOIN_TIMEOUT_SECONDS)
    for process in processes:
        if process.pid is not None and process.is_alive():
            process.terminate()
    _bounded_join(processes, _JOIN_TIMEOUT_SECONDS)
    for process in processes:
        if process.pid is not None and process.is_alive():
            process.kill()
    _bounded_join(processes, _JOIN_TIMEOUT_SECONDS)
    return tuple(
        index
        for index, process in enumerate(processes)
        if process.pid is not None and process.is_alive()
    )


@unittest.skipUnless(os.name == "nt", "TG-M23.2 worker race is Windows-only")
class AnalysisWorkerRaceTests(unittest.TestCase):
    def test_two_workers_only_lease_winner_reads_writes_and_publishes(self):
        """S0 is shared only to preserve the lease-owned status CAS boundary."""

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            fixed_root = temporary_root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            evidence_index = read_evidence_index(
                write_evidence_tree(temporary_root / "source")
            )
            entry = next(
                item
                for item in evidence_index.entries
                if item["bundle_state"] == "legacy_unknown"
            )
            source = validate_evidence_source(evidence_index, entry)
            queued = enqueue_analysis_source(
                paths=paths,
                source=source,
                recipe=default_recipe(),
            )
            baseline = _owned_tree_snapshot(paths)

            context = multiprocessing.get_context("spawn")
            ready = [context.Event(), context.Event()]
            start = context.Event()
            audit_request = context.Event()
            release = context.Event()
            receiver_pairs = [context.Pipe(duplex=False) for _ in range(2)]
            receivers = [pair[0] for pair in receiver_pairs]
            senders = [pair[1] for pair in receiver_pairs]
            processes = [
                context.Process(
                    target=_worker_process,
                    args=(
                        slot,
                        str(fixed_root),
                        evidence_index,
                        ready[slot],
                        start,
                        audit_request,
                        release,
                        senders[slot],
                    ),
                    name=f"tg-m232-race-{slot}",
                )
                for slot in range(2)
            ]
            active = {receivers[slot]: slot for slot in range(2)}
            release_sent = False
            started = []
            try:
                for slot, process in enumerate(processes):
                    process.start()
                    started.append(process)
                    senders[slot].close()
                self.assertEqual(len(started), 2)

                ready_deadline = time.monotonic() + _PHASE_TIMEOUT_SECONDS
                ready_slots = set()
                while len(ready_slots) != 2:
                    ready_slots.update(
                        slot for slot, event in enumerate(ready) if event.is_set()
                    )
                    if len(ready_slots) == 2:
                        break
                    remaining = ready_deadline - time.monotonic()
                    if remaining <= 0:
                        self.fail(
                            "both workers must reach the common start barrier; "
                            f"exitcodes={[process.exitcode for process in processes]}"
                        )
                    for connection in wait(
                        tuple(active),
                        timeout=min(0.05, remaining),
                    ):
                        try:
                            message = connection.recv()
                        except EOFError as failure:
                            raise AssertionError(
                                "worker race channel closed before ready"
                            ) from failure
                        if message.get("phase") == "error":
                            self.fail(
                                "worker failed before the start barrier: "
                                + message["error_type"]
                            )
                        self.fail("worker advanced before the common start barrier")
                start.set()

                winner_acquired = None
                loser_result = None
                protocol_deadline = time.monotonic() + _PHASE_TIMEOUT_SECONDS
                while winner_acquired is None or loser_result is None:
                    connection, message = _next_message(active, protocol_deadline)
                    if message["phase"] == "lease_acquired":
                        self.assertIsNone(winner_acquired)
                        winner_acquired = message
                    elif message["phase"] == "result":
                        self.assertEqual(message["disposition"], "busy")
                        self.assertIsNone(loser_result)
                        loser_result = message
                        active.pop(connection)
                    elif message["phase"] == "error":
                        self.fail(
                            "worker failed before the race boundary: "
                            + message["error_type"]
                        )
                    else:
                        self.fail("worker advanced before the audit gate")

                winner_slot = winner_acquired["slot"]
                loser_slot = loser_result["slot"]
                self.assertNotEqual(winner_slot, loser_slot)
                self.assertIsNone(loser_result["analysis_job_id"])
                self.assertIsNone(loser_result["status"])
                self.assertFalse(loser_result["lease_retained"])
                self.assertEqual(
                    loser_result["counters"],
                    {key: 0 for key in _COUNTER_KEYS},
                )
                self.assertEqual(loser_result["created_basenames"], ())

                acquired_counters = winner_acquired["counters"]
                self.assertEqual(acquired_counters["prepare_tree"], 1)
                self.assertEqual(acquired_counters["status_s0_open"], 1)
                self.assertEqual(
                    {
                        key: acquired_counters[key]
                        for key in _COUNTER_KEYS
                        if key not in {"prepare_tree", "status_s0_open"}
                    },
                    {
                        key: 0
                        for key in _COUNTER_KEYS
                        if key not in {"prepare_tree", "status_s0_open"}
                    },
                )

                audit_request.set()
                connection, paused = _next_message(
                    active,
                    time.monotonic() + _PHASE_TIMEOUT_SECONDS,
                )
                self.assertEqual(paused["phase"], "paused_snapshot")
                self.assertEqual(paused["slot"], winner_slot)
                self.assertEqual(paused["snapshot"], baseline)
                self.assertEqual(paused["counters"], acquired_counters)
                self.assertTrue(processes[winner_slot].is_alive())
                self.assertFalse(connection.poll(0.0))

                release.set()
                release_sent = True
                connection, winner_result = _next_message(
                    active,
                    time.monotonic() + _PHASE_TIMEOUT_SECONDS,
                )
                self.assertEqual(winner_result["phase"], "result")
                self.assertEqual(winner_result["slot"], winner_slot)
                self.assertEqual(winner_result["disposition"], "published")
                self.assertEqual(
                    winner_result["analysis_job_id"],
                    queued.descriptor["analysis_job_id"],
                )
                self.assertFalse(winner_result["lease_retained"])
                active.pop(connection)
                self.assertEqual(active, {})

                winner_counters = winner_result["counters"]
                self.assertEqual(winner_counters["select_next_job"], 1)
                self.assertEqual(winner_counters["inventory_descriptor_read"], 1)
                self.assertEqual(winner_counters["inventory_status_read"], 1)
                self.assertEqual(winner_counters["source_revalidation"], 1)
                self.assertEqual(winner_counters["packet_build"], 1)
                self.assertEqual(winner_counters["report_build"], 1)
                self.assertGreaterEqual(winner_counters["status_cas"], 1)
                self.assertGreaterEqual(winner_counters["durable_create"], 1)
                self.assertGreaterEqual(winner_counters["relative_create"], 1)
                self.assertGreaterEqual(winner_counters["status_replace"], 1)
                self.assertEqual(winner_counters["publish_no_adapter"], 1)
                self.assertEqual(winner_counters["recover_publication"], 0)
                self.assertEqual(winner_counters["adapter_prepare"], 0)
                self.assertEqual(winner_counters["adapter_bind"], 0)
                self.assertEqual(winner_counters["adapter_execute"], 0)
                self.assertEqual(winner_counters["adapter_publish"], 0)
                self.assertNotIn("output.json", winner_result["created_basenames"])
                self.assertNotIn(
                    "output-schema.json",
                    winner_result["created_basenames"],
                )

                _bounded_join(processes, _JOIN_TIMEOUT_SECONDS)
                self.assertFalse(any(process.is_alive() for process in processes))
                self.assertEqual([process.exitcode for process in processes], [0, 0])

                audit = AnalysisOutboxSession.acquire(paths)
                try:
                    final = audit.read_bound_job(queued.descriptor).status
                    final_snapshot = _held_tree_snapshot(paths, audit)
                    report_bytes = _read_held_leaf(
                        audit,
                        paths.reports.name,
                        final["report_id"] + ".json",
                    )
                    markdown_bytes = _read_held_leaf(
                        audit,
                        paths.rendered.name,
                        final["report_id"] + ".md",
                    )
                finally:
                    audit.release_normal()

                self.assertEqual(final["state"], "published")
                self.assertEqual(final["worker_attempt_count"], 1)
                self.assertEqual(final["adapter_attempt_count"], 0)
                self.assertEqual(final["inference_state"], "disabled")
                self.assertTrue(
                    all(
                        final[name] is not None
                        for name in ("report_id", "report_digest", "render_digest")
                    )
                )
                report_rows = [
                    row for row in final_snapshot if row[0] == "reports"
                ]
                rendered_rows = [
                    row for row in final_snapshot if row[0] == "rendered"
                ]
                temporary_rows = [
                    row for row in final_snapshot if row[0] == "tmp"
                ]
                self.assertEqual(
                    [row[1] for row in report_rows],
                    [final["report_id"] + ".json"],
                )
                self.assertEqual(
                    [row[1] for row in rendered_rows],
                    [final["report_id"] + ".md"],
                )
                self.assertEqual(temporary_rows, [])
                report_document = json.loads(report_bytes)
                self.assertEqual(
                    report_document["report_digest"],
                    final["report_digest"],
                )
                self.assertEqual(
                    "sha256:" + hashlib.sha256(markdown_bytes).hexdigest(),
                    final["render_digest"],
                )
            finally:
                survivors = _force_stop(
                    processes,
                    allow_graceful=release_sent,
                )
                for sender in senders:
                    try:
                        sender.close()
                    except OSError:
                        pass
                for receiver in receivers:
                    receiver.close()
                for process in processes:
                    if process.pid is None or not process.is_alive():
                        process.close()
                if survivors:
                    raise AssertionError("worker process cleanup was incomplete")


if __name__ == "__main__":
    unittest.main()
