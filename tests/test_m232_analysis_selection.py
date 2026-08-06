from __future__ import annotations

import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.m23_test_support import write_evidence_tree


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import _analysis_win32 as win32_boundary  # noqa: E402
from task_governance_tool import analysis_outbox  # noqa: E402
from task_governance_tool.analysis_contracts import (  # noqa: E402
    ANALYSIS_STATUS_MAX_BYTES,
    canonical_json_document_bytes,
    default_recipe,
)
from task_governance_tool.analysis_outbox import (  # noqa: E402
    AnalysisOutboxError,
    AnalysisOutboxSession,
    SelectedAnalysisJob,
    enqueue_analysis_source,
    replace_analysis_status,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.state_paths import analysis_state_paths  # noqa: E402


@unittest.skipUnless(os.name == "nt", "TG-M23.2 selection is Windows-only")
class AnalysisSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._source_cache = {}

    def _sources(self, root: Path):
        key = str(root)
        if key not in self._source_cache:
            index = read_evidence_index(write_evidence_tree(root / "source"))
            self._source_cache[key] = [
                validate_evidence_source(index, entry) for entry in index.entries
            ]
        return self._source_cache[key]

    @staticmethod
    def _active_snapshot(paths):
        session = AnalysisOutboxSession.acquire(paths)
        rows = {}
        try:
            for directory_name, parent in (
                ("outbox", session._directories.outbox),
                ("status", session._directories.status),
            ):
                entries = win32_boundary.enumerate_held_directory(
                    parent,
                    maximum_entries=100_000,
                )
                for entry in entries:
                    handle = win32_boundary.open_relative_file_if_present(
                        parent,
                        entry.name,
                        maximum=1_000_000,
                        kind="analysis-selection-test-rh",
                    )
                    if handle is None:
                        raise AssertionError("inventoried leaf disappeared")
                    try:
                        rows[(directory_name, entry.name)] = (
                            entry.file_id,
                            entry.size,
                            win32_boundary.read_handle_capped(
                                handle,
                                maximum=1_000_000,
                            ),
                        )
                    finally:
                        handle.close()
            return rows
        finally:
            session.release_normal()

    @staticmethod
    def _replace_status_fixture(paths, basename: str, content: bytes) -> None:
        (paths.status / basename).unlink()
        session = AnalysisOutboxSession.acquire(paths)
        try:
            analysis_outbox._create_relative_durable_file(
                session._directories.status,
                basename,
                content,
                maximum=ANALYSIS_STATUS_MAX_BYTES,
                kind="analysis-selection-test-th",
            )
        finally:
            session.release_normal()

    def _queue(self, root: Path, paths, *, source_index: int = 0, optional=False):
        sources = self._sources(root)
        recipe = (
            default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixture-model",
            )
            if optional
            else default_recipe()
        )
        return enqueue_analysis_source(
            paths=paths,
            source=sources[source_index],
            recipe=recipe,
        )

    @staticmethod
    def _running(paths, queued):
        running = deepcopy(queued.status)
        running.update(
            {
                "state": "running",
                "worker_attempt_count": 1,
                "packet_digest": "sha256:" + "a" * 64,
            }
        )
        return replace_analysis_status(
            paths=paths,
            descriptor=queued.descriptor,
            expected_status=queued.status,
            status=running,
        )

    def _intent(self, paths, queued):
        running = self._running(paths, queued)
        intent = deepcopy(running)
        intent.update(
            {
                "report_id": "tg_analysis_report_" + "b" * 16,
                "report_digest": "sha256:" + "c" * 64,
                "render_digest": "sha256:" + "d" * 64,
            }
        )
        return replace_analysis_status(
            paths=paths,
            descriptor=queued.descriptor,
            expected_status=running,
            status=intent,
        )

    def _cancelled(self, paths, queued):
        running = self._running(paths, queued)
        cancelled = deepcopy(running)
        cancelled.update(
            {
                "state": "cancelled",
                "fixed_code": "cancelled",
            }
        )
        return replace_analysis_status(
            paths=paths,
            descriptor=queued.descriptor,
            expected_status=running,
            status=cancelled,
        )

    def test_classifies_pending_recovery_reclaim_and_skips_terminal(self):
        cases = (
            ("pending", "pending"),
            ("recover", "recover_intent"),
            ("reclaim", "reclaim_running"),
            ("terminal", None),
        )
        for state, expected_kind in cases:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                fixed_root = root / "fixed"
                fixed_root.mkdir()
                paths = analysis_state_paths(fixed_root)
                queued = self._queue(root, paths)
                if state == "recover":
                    expected_status = self._intent(paths, queued)
                elif state == "reclaim":
                    expected_status = self._running(paths, queued)
                elif state == "terminal":
                    expected_status = self._cancelled(paths, queued)
                else:
                    expected_status = queued.status

                session = AnalysisOutboxSession.acquire(paths)
                try:
                    selected = session.select_next_job()
                    if expected_kind is None:
                        self.assertIsNone(selected)
                    else:
                        self.assertIsInstance(selected, SelectedAnalysisJob)
                        self.assertEqual(selected.kind, expected_kind)
                        self.assertEqual(selected.descriptor, queued.descriptor)
                        self.assertEqual(selected.status, expected_status)
                finally:
                    session.release_normal()

    def test_lexicographic_order_fresh_copy_factory_and_once_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            first = self._queue(root, paths, source_index=0)
            second = self._queue(root, paths, source_index=1)
            expected = min(
                (first, second),
                key=lambda queued: queued.descriptor["analysis_job_id"],
            )
            original_descriptor = deepcopy(expected.descriptor)
            original_status = deepcopy(expected.status)

            session = AnalysisOutboxSession.acquire(paths)
            try:
                selected = session.select_next_job()
                self.assertEqual(selected.descriptor, original_descriptor)
                self.assertEqual(selected.status, original_status)
                selected.descriptor["source_basis"]["project_id"] = "mutated"
                selected.status["state"] = "mutated"
                stored = session.read_bound_job(original_descriptor)
                self.assertEqual(stored.descriptor, original_descriptor)
                self.assertEqual(stored.status, original_status)
                with self.assertRaises(AnalysisOutboxError) as repeated:
                    session.select_next_job()
                self.assertEqual(repeated.exception.code, "analysis_selection_invalid")
            finally:
                session.release_normal()

            with self.assertRaises(AnalysisOutboxError):
                SelectedAnalysisJob(
                    "pending",
                    original_descriptor,
                    original_status,
                    _token=object(),
                )

    def test_descriptor_only_repair_occurs_once_after_full_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            queued = self._queue(root, paths)
            status_path = paths.status / f"{queued.descriptor['analysis_job_id']}.json"
            status_path.unlink()
            original_create = analysis_outbox._create_relative_durable_file

            with patch.object(
                analysis_outbox,
                "_create_relative_durable_file",
                wraps=original_create,
            ) as create:
                session = AnalysisOutboxSession.acquire(paths)
                try:
                    selected = session.select_next_job()
                    self.assertEqual(selected.kind, "pending")
                    self.assertEqual(selected.status, queued.status)
                    self.assertEqual(create.call_count, 1)
                    self.assertEqual(create.call_args.kwargs["kind"], "analysis-status-th")
                    with self.assertRaises(AnalysisOutboxError):
                        session.select_next_job()
                finally:
                    session.release_normal()

            audit = AnalysisOutboxSession.acquire(paths)
            try:
                self.assertEqual(
                    audit.read_bound_job(queued.descriptor).status,
                    queued.status,
                )
            finally:
                audit.release_normal()

    def test_later_invalid_pair_blocks_earlier_descriptor_only_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            queued = (
                self._queue(root, paths, source_index=0),
                self._queue(root, paths, source_index=1),
            )
            earlier, later = sorted(
                queued,
                key=lambda item: item.descriptor["analysis_job_id"],
            )
            earlier_status = paths.status / f"{earlier.descriptor['analysis_job_id']}.json"
            later_status = paths.status / f"{later.descriptor['analysis_job_id']}.json"
            earlier_status.unlink()
            self._replace_status_fixture(paths, later_status.name, b"{}\n")
            before = self._active_snapshot(paths)

            session = AnalysisOutboxSession.acquire(paths)
            try:
                with self.assertRaises(AnalysisOutboxError):
                    session.select_next_job()
            finally:
                session.release_normal()
            self.assertEqual(self._active_snapshot(paths), before)

    def test_status_only_name_id_and_binding_mismatches_are_no_write(self):
        for failure_kind in ("status_only", "name_id", "binding"):
            with (
                self.subTest(failure_kind=failure_kind),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                fixed_root = root / "fixed"
                fixed_root.mkdir()
                paths = analysis_state_paths(fixed_root)
                first = self._queue(root, paths, source_index=0)
                first_name = f"{first.descriptor['analysis_job_id']}.json"
                if failure_kind == "status_only":
                    (paths.outbox / first_name).unlink()
                elif failure_kind == "name_id":
                    replacement_id = (
                        "tg_analysis_job_" + "f" * 16
                        if first.descriptor["analysis_job_id"]
                        != "tg_analysis_job_" + "f" * 16
                        else "tg_analysis_job_" + "e" * 16
                    )
                    replacement_name = replacement_id + ".json"
                    (paths.outbox / first_name).replace(paths.outbox / replacement_name)
                    (paths.status / first_name).replace(paths.status / replacement_name)
                else:
                    second = self._queue(root, paths, source_index=1)
                    self._replace_status_fixture(
                        paths,
                        first_name,
                        canonical_json_document_bytes(second.status),
                    )

                before = self._active_snapshot(paths)
                session = AnalysisOutboxSession.acquire(paths)
                try:
                    with self.assertRaises(AnalysisOutboxError):
                        session.select_next_job()
                finally:
                    session.release_normal()
                after = self._active_snapshot(paths)
                self.assertEqual(after, before)

    def test_reports_rendered_and_quarantine_contents_are_not_selection_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            queued = self._queue(root, paths)
            (paths.reports / ("tg_analysis_report_" + "1" * 16 + ".json")).write_bytes(
                b"not-json"
            )
            (paths.rendered / ("tg_analysis_report_" + "1" * 16 + ".md")).write_bytes(
                b"not-markdown"
            )
            quarantine = paths.temporary / ".taskgov-analysis-aaaaaaaa"
            quarantine.mkdir()
            (quarantine / "must-not-be-read.bin").write_bytes(b"private")

            session = AnalysisOutboxSession.acquire(paths)
            original_enumerate = win32_boundary.enumerate_held_directory
            try:
                with (
                    patch.object(
                        win32_boundary,
                        "open_relative_directory",
                        wraps=win32_boundary.open_relative_directory,
                    ) as directory_open,
                    patch.object(
                        win32_boundary,
                        "enumerate_held_directory",
                        wraps=original_enumerate,
                    ) as enumerate_directory,
                ):
                    selected = session.select_next_job()
                self.assertEqual(selected.kind, "pending")
                self.assertEqual(directory_open.call_count, 0)
                self.assertEqual(
                    [call.args[0] for call in enumerate_directory.call_args_list],
                    [session._directories.outbox, session._directories.status],
                )
            finally:
                session.release_normal()

    def test_selection_inventory_accepts_100000_and_rejects_100001(self):
        def status_temp_entries(count: int):
            for index in range(count):
                yield win32_boundary.DirectoryEntry(
                    name=f".taskgov-analysis-status-{index:08x}.tmp",
                    file_id=index.to_bytes(16, "little"),
                    size=0,
                    is_directory=False,
                    is_reparse=False,
                )

        for count, accepted in ((100_000, True), (100_001, False)):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as temporary:
                fixed_root = Path(temporary) / "fixed"
                fixed_root.mkdir()
                paths = analysis_state_paths(fixed_root)
                session = AnalysisOutboxSession.acquire(paths)

                def enumerate_oracle(handle, *, maximum_entries):
                    self.assertEqual(maximum_entries, 100_000)
                    if handle is session._directories.outbox:
                        return ()
                    if handle is session._directories.status:
                        return status_temp_entries(count)
                    raise AssertionError("selection touched a passive/private directory")

                try:
                    with (
                        patch.object(
                            win32_boundary,
                            "enumerate_held_directory",
                            side_effect=enumerate_oracle,
                        ),
                        patch.object(
                            analysis_outbox,
                            "_create_relative_durable_file",
                            wraps=analysis_outbox._create_relative_durable_file,
                        ) as durable_write,
                    ):
                        if accepted:
                            self.assertIsNone(session.select_next_job())
                        else:
                            with self.assertRaises(AnalysisOutboxError) as too_large:
                                session.select_next_job()
                            self.assertEqual(too_large.exception.code, "analysis_too_large")
                    self.assertEqual(durable_write.call_count, 0)
                finally:
                    session.release_normal()

    def test_missing_status_repair_cannot_create_file_100001(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixed_root = root / "fixed"
            fixed_root.mkdir()
            paths = analysis_state_paths(fixed_root)
            queued = self._queue(root, paths)
            status_name = f"{queued.descriptor['analysis_job_id']}.json"
            (paths.status / status_name).unlink()
            before = self._active_snapshot(paths)
            session = AnalysisOutboxSession.acquire(paths)
            original_enumerate = win32_boundary.enumerate_held_directory

            def enumerate_oracle(handle, *, maximum_entries):
                self.assertEqual(maximum_entries, 100_000)
                if handle is session._directories.outbox:
                    return original_enumerate(
                        handle,
                        maximum_entries=maximum_entries,
                    )
                if handle is session._directories.status:
                    return (
                        win32_boundary.DirectoryEntry(
                            name=f".taskgov-analysis-status-{index:08x}.tmp",
                            file_id=index.to_bytes(16, "little"),
                            size=0,
                            is_directory=False,
                            is_reparse=False,
                        )
                        for index in range(99_999)
                    )
                raise AssertionError("selection touched a passive/private directory")

            try:
                with (
                    patch.object(
                        win32_boundary,
                        "enumerate_held_directory",
                        side_effect=enumerate_oracle,
                    ),
                    patch.object(
                        analysis_outbox,
                        "_create_relative_durable_file",
                        wraps=analysis_outbox._create_relative_durable_file,
                    ) as durable_write,
                ):
                    with self.assertRaises(AnalysisOutboxError) as too_large:
                        session.select_next_job()
                    self.assertEqual(too_large.exception.code, "analysis_too_large")
                self.assertEqual(durable_write.call_count, 0)
            finally:
                session.release_normal()
            self.assertEqual(self._active_snapshot(paths), before)


if __name__ == "__main__":
    unittest.main()
