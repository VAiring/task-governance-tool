from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.m14_test_support import SOURCE_SKILL_ROOT, canonical_test_path

from task_governance_tool import viewer as viewer_module
from task_governance_tool import viewer_maintenance as viewer_service
from task_governance_tool.artifact_lock import ArtifactLockError
from task_governance_tool.storage import (
    StorageError,
    configure_project_maintenance,
    connect_initialized,
    connect_initialized_readonly,
    initialize_database,
    read_viewer_maintenance,
    record_viewer_attempt_outcome,
    record_viewer_publication,
    resolve_database_target,
)
from task_governance_tool.tasks import add_task
from task_governance_tool.viewer import (
    ViewerError,
    resolve_canonical_viewer_output_target,
)


SCRIPT_PATH = SOURCE_SKILL_ROOT / "scripts" / "taskgov.py"
FIXED_TIME = "2026-07-27T00:00:00Z"


def make_target(root: Path, *, enabled: bool):
    repo = root / "project"
    repo.mkdir()
    target = resolve_database_target(
        repo=repo,
        db=root / "isolated-state" / "taskgov.sqlite",
        script_path=SCRIPT_PATH,
    )
    initialize_database(target)
    if enabled:
        configure_project_maintenance(
            target,
            requested_interval_minutes=None,
            requested_generations=None,
            enabled_at=FIXED_TIME,
        )
    return target


def viewer_state(target):
    with closing(connect_initialized_readonly(target)) as connection:
        state = read_viewer_maintenance(
            connection,
            target.project.project_id,
        )
    if state is None:
        raise AssertionError("Viewer maintenance state is missing")
    return state


def add_viewer_event(target, title: str) -> None:
    with closing(connect_initialized(target)) as connection:
        add_task(
            connection,
            target.project,
            database_target=target,
            title=title,
        )
        connection.commit()


class ViewerMaintenanceTests(unittest.TestCase):
    def test_pre_opt_in_skips_artifact_and_lock_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=False)
            output = resolve_canonical_viewer_output_target(target)

            result = viewer_service.run_routine_viewer_refresh(
                target,
                observed_at=FIXED_TIME,
            )

            self.assertEqual(result.code, "not_opted_in")
            self.assertEqual(result.renders, 0)
            self.assertFalse(output.path.exists())
            self.assertFalse(output.path.parent.exists())
            state = viewer_state(target)
            self.assertIsNone(state.rendered_generation)
            self.assertIsNone(state.last_outcome_code)

    def test_setup_force_publishes_current_state_to_isolated_canonical_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = make_target(root, enabled=True)
            output = resolve_canonical_viewer_output_target(target)

            first = viewer_service.publish_setup_viewer(
                target,
                observed_at=FIXED_TIME,
            )
            current = viewer_service.run_routine_viewer_refresh(
                target,
                observed_at=FIXED_TIME,
            )
            forced = viewer_service.publish_setup_viewer(
                target,
                observed_at=FIXED_TIME,
            )

            self.assertEqual((first.code, first.renders), ("succeeded", 1))
            self.assertEqual((current.code, current.renders), ("current", 0))
            self.assertEqual((forced.code, forced.renders), ("succeeded", 1))
            self.assertEqual(
                canonical_test_path(output.path),
                canonical_test_path(
                    root
                    / "isolated-state"
                    / "viewer"
                    / "task-viewer.html"
                ),
            )
            self.assertTrue(output.path.is_file())
            self.assertNotIn(SOURCE_SKILL_ROOT, output.path.parents)
            state = viewer_state(target)
            self.assertEqual(state.rendered_generation, state.source_generation)
            self.assertEqual(state.last_outcome_code, "succeeded")

    def test_routine_refresh_moves_due_state_to_current_then_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            viewer_service.publish_setup_viewer(target, observed_at=FIXED_TIME)
            add_viewer_event(target, "Due Viewer task")
            due = viewer_state(target)

            refreshed = viewer_service.run_routine_viewer_refresh(
                target,
                observed_at=FIXED_TIME,
            )
            current = viewer_service.run_routine_viewer_refresh(
                target,
                observed_at=FIXED_TIME,
            )

            self.assertTrue(due.due)
            self.assertIsNotNone(due.rendered_generation)
            self.assertEqual(
                due.source_generation,
                due.rendered_generation + 1,
            )
            self.assertEqual(
                (refreshed.code, refreshed.renders),
                ("succeeded", 1),
            )
            self.assertEqual((current.code, current.renders), ("current", 0))
            final = viewer_state(target)
            self.assertFalse(final.due)
            self.assertEqual(final.rendered_generation, final.source_generation)

    def test_zero_wait_contention_is_deferred_and_remains_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            with mock.patch.object(
                viewer_service,
                "utc_now",
                return_value=FIXED_TIME,
            ):
                viewer_service.publish_setup_viewer(
                    target,
                    observed_at=FIXED_TIME,
                )
            add_viewer_event(target, "Contended Viewer task")

            with mock.patch.object(
                viewer_service,
                "zero_wait_artifact_lock",
                side_effect=ArtifactLockError(contended=True),
            ) as locker:
                result = viewer_service.run_routine_viewer_refresh(
                    target,
                    observed_at="2026-07-27T00:00:01Z",
                )

            locker.assert_called_once()
            self.assertEqual((result.code, result.renders), ("deferred", 0))
            state = viewer_state(target)
            self.assertTrue(state.due)
            self.assertEqual(state.last_outcome_code, "deferred")
            self.assertEqual(
                state.last_outcome_at,
                "2026-07-27T00:00:01Z",
            )

    def test_atomic_write_failure_preserves_last_good_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            with mock.patch.object(
                viewer_service,
                "utc_now",
                return_value=FIXED_TIME,
            ):
                viewer_service.publish_setup_viewer(
                    target,
                    observed_at=FIXED_TIME,
                )
            output = resolve_canonical_viewer_output_target(target)
            last_good = output.path.read_bytes()
            before = viewer_state(target)
            add_viewer_event(target, "Failed Viewer task")

            with mock.patch.object(
                viewer_module.os,
                "replace",
                side_effect=OSError("injected"),
            ):
                result = viewer_service.run_routine_viewer_refresh(
                    target,
                    observed_at="2026-07-27T00:00:01Z",
                )

            self.assertEqual((result.code, result.renders), ("failed", 0))
            self.assertEqual(output.path.read_bytes(), last_good)
            self.assertEqual(
                list(output.path.parent.glob(".task-viewer-*.tmp")),
                [],
            )
            state = viewer_state(target)
            self.assertTrue(state.due)
            self.assertEqual(state.rendered_generation, before.rendered_generation)
            self.assertEqual(state.last_success_at, before.last_success_at)
            self.assertEqual(state.last_outcome_code, "failed")

    def test_configured_interval_is_published_on_next_relevant_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skill"
            skill_root.mkdir()
            target = make_target(root, enabled=True)
            output = resolve_canonical_viewer_output_target(target)

            viewer_service.publish_setup_viewer(
                target,
                observed_at=FIXED_TIME,
                skill_root=skill_root,
            )
            self.assertIn(
                'data-taskgov-refresh-interval-seconds="0"',
                output.path.read_text(encoding="utf-8"),
            )

            config = skill_root / "config" / "viewer.json"
            config.parent.mkdir()
            for interval in (5, 30):
                with self.subTest(interval=interval):
                    config.write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "profile": "visibility-refresh-v1",
                                "refresh_interval_seconds": interval,
                            }
                        ),
                        encoding="utf-8",
                    )
                    add_viewer_event(target, f"Interval {interval}")
                    result = viewer_service.run_routine_viewer_refresh(
                        target,
                        observed_at=FIXED_TIME,
                        skill_root=skill_root,
                    )
                    self.assertEqual(
                        (result.code, result.renders),
                        ("succeeded", 1),
                    )
                    self.assertIn(
                        (
                            "data-taskgov-refresh-interval-seconds="
                            f'"{interval}"'
                        ),
                        output.path.read_text(encoding="utf-8"),
                    )

    def test_invalid_config_preserves_last_good_and_due_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skill"
            skill_root.mkdir()
            target = make_target(root, enabled=True)
            with mock.patch.object(
                viewer_service,
                "utc_now",
                return_value=FIXED_TIME,
            ):
                viewer_service.publish_setup_viewer(
                    target,
                    observed_at=FIXED_TIME,
                    skill_root=skill_root,
                )
            output = resolve_canonical_viewer_output_target(target)
            last_good = output.path.read_bytes()
            before = viewer_state(target)
            config = skill_root / "config" / "viewer.json"
            config.parent.mkdir()
            config.write_text('{"schema_version":1}', encoding="utf-8")
            add_viewer_event(target, "Invalid config change")

            result = viewer_service.run_routine_viewer_refresh(
                target,
                observed_at="2026-07-27T00:00:01Z",
                skill_root=skill_root,
            )

            self.assertEqual((result.code, result.renders), ("failed", 0))
            self.assertEqual(output.path.read_bytes(), last_good)
            state = viewer_state(target)
            self.assertTrue(state.due)
            self.assertEqual(state.rendered_generation, before.rendered_generation)
            self.assertEqual(state.last_success_at, before.last_success_at)
            self.assertEqual(state.last_outcome_code, "failed")

    def test_second_render_progress_is_bounded_and_remaining_churn_stays_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            viewer_service.publish_setup_viewer(target, observed_at=FIXED_TIME)
            add_viewer_event(target, "Initial Viewer change")
            real_write = viewer_service.write_viewer_html
            write_count = 0

            def write_and_advance(output, html):
                nonlocal write_count
                real_write(output, html)
                write_count += 1
                add_viewer_event(target, f"Viewer churn {write_count}")
                return False

            with (
                mock.patch.object(
                    viewer_service,
                    "write_viewer_html",
                    side_effect=write_and_advance,
                ) as writer,
                mock.patch.object(
                    viewer_service,
                    "load_viewer_refresh_interval",
                    return_value=5,
                ) as loader,
            ):
                result = viewer_service.run_routine_viewer_refresh(
                    target,
                    observed_at=FIXED_TIME,
                )

            self.assertEqual(result.code, "succeeded")
            self.assertEqual(result.renders, 2)
            self.assertEqual(writer.call_count, 2)
            loader.assert_called_once_with(None)
            state = viewer_state(target)
            self.assertTrue(state.due)
            self.assertEqual(
                state.source_generation,
                state.rendered_generation + 1,
            )
            self.assertEqual(state.last_outcome_code, "succeeded")

    def test_follow_up_success_supersedes_later_started_contention(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            with mock.patch.object(
                viewer_service,
                "utc_now",
                return_value=FIXED_TIME,
            ):
                viewer_service.publish_setup_viewer(
                    target,
                    observed_at=FIXED_TIME,
                )
            add_viewer_event(target, "Initial Viewer change")
            real_write = viewer_service.write_viewer_html
            write_count = 0

            def write_and_contend(output, html):
                nonlocal write_count
                real_write(output, html)
                write_count += 1
                if write_count == 1:
                    add_viewer_event(target, "Concurrent Viewer change")
                    record_viewer_attempt_outcome(
                        target,
                        code="deferred",
                        occurred_at="2026-07-27T00:00:01Z",
                    )

            with (
                mock.patch.object(
                    viewer_service,
                    "write_viewer_html",
                    side_effect=write_and_contend,
                ),
                mock.patch.object(
                    viewer_service,
                    "utc_now",
                    return_value="2026-07-27T00:00:02Z",
                ),
            ):
                result = viewer_service.run_routine_viewer_refresh(
                    target,
                    observed_at=FIXED_TIME,
                )

            self.assertEqual((result.code, result.renders), ("succeeded", 2))
            state = viewer_state(target)
            self.assertFalse(state.due)
            self.assertEqual(
                state.source_generation,
                state.rendered_generation,
            )
            self.assertEqual(state.last_outcome_code, "succeeded")
            self.assertEqual(
                state.last_outcome_at,
                "2026-07-27T00:00:02Z",
            )

    def test_older_generation_cannot_replace_newer_publication_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            viewer_service.publish_setup_viewer(target, observed_at=FIXED_TIME)
            add_viewer_event(target, "First generation")
            add_viewer_event(target, "Second generation")
            viewer_service.run_routine_viewer_refresh(
                target,
                observed_at=FIXED_TIME,
            )
            output = resolve_canonical_viewer_output_target(target)
            artifact = output.path.read_bytes()
            newer = viewer_state(target)

            with self.assertRaises(StorageError):
                record_viewer_publication(
                    target,
                    source_generation=newer.source_generation - 1,
                    published_at=FIXED_TIME,
                )
            record_viewer_publication(
                target,
                source_generation=newer.source_generation,
                published_at="2026-07-26T23:59:59Z",
            )

            self.assertEqual(output.path.read_bytes(), artifact)
            self.assertEqual(viewer_state(target), newer)

    def test_same_time_attempt_cannot_replace_success_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)
            viewer_service.publish_setup_viewer(
                target,
                observed_at="2026-07-27T00:00:02Z",
            )
            succeeded = viewer_state(target)

            record_viewer_attempt_outcome(
                target,
                code="deferred",
                occurred_at=str(succeeded.last_outcome_at),
            )

            current = viewer_state(target)
            self.assertEqual(current.last_outcome_code, "succeeded")
            self.assertEqual(current.last_outcome_at, succeeded.last_outcome_at)
            self.assertFalse(current.due)

    def test_canonical_target_rejects_reparse_parent_and_database_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_target(Path(tmp), enabled=True)

            with mock.patch.object(
                viewer_module,
                "path_is_reparse_point",
                return_value=True,
            ):
                with self.assertRaises(ViewerError) as reparse:
                    resolve_canonical_viewer_output_target(target)
            self.assertEqual(reparse.exception.code, "output_path_invalid")

            with mock.patch.object(
                viewer_module,
                "paths_refer_to_same_location",
                return_value=True,
            ):
                with self.assertRaises(ViewerError) as alias:
                    resolve_canonical_viewer_output_target(target)
            self.assertEqual(alias.exception.code, "output_path_invalid")


if __name__ == "__main__":
    unittest.main()
