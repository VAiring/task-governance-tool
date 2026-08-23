from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import evidence_projection as projection  # noqa: E402
from task_governance_tool.state_paths import (  # noqa: E402
    StatePathError,
    ValidatedFile,
)
from task_governance_tool.storage import (  # noqa: E402
    DatabaseTarget,
    EvidenceProjectionBasis,
    ProjectIdentity,
)


OBSERVED_AT = "2026-08-05T06:00:00Z"
PROJECT_ID = "project-0123456789ab"
BUNDLE_ID = "tg_completion_evidence_bundle_0123456789abcdef"


def _target(root: Path) -> DatabaseTarget:
    repo = root / "repo"
    repo.mkdir()
    database = root / "state" / "current" / "taskgov.sqlite"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"database")
    return DatabaseTarget(
        project=ProjectIdentity(
            project_id=PROJECT_ID,
            canonical_repo=repo,
            canonical_path_hash="a" * 64,
            display_name="repo",
        ),
        db_path=database,
        explicit_db=False,
    )


def _bundle_artifact(document: bytes = b'{"bundle":1}\n'):
    return projection.BundleArtifact(
        payload={},
        payload_bytes=b"{}",
        bundle_digest="sha256:" + "b" * 64,
        envelope={},
        document=document,
        file_digest="sha256:" + hashlib.sha256(document).hexdigest(),
    )


def _index_artifact(document: bytes = b'{"index":1}\n'):
    return projection.IndexArtifact(
        payload={},
        payload_bytes=b"{}",
        index_digest="sha256:" + "c" * 64,
        envelope={},
        document=document,
    )


def _capture(*, generation: int, basis: object | None):
    return projection._ProjectionCapture(
        maintenance=SimpleNamespace(enabled=True),
        state=SimpleNamespace(source_generation=generation, due=True),
        basis=basis,
    )


def _write_sized_file(path: Path, size: int) -> None:
    with path.open("wb") as stream:
        stream.truncate(size)


class EvidenceProjectionPublicationTests(unittest.TestCase):
    def test_routine_orders_bundle_index_and_success_record(self):
        basis = object()
        rendered = projection._RenderedProjection(
            source_generation=3,
            bundles=((BUNDLE_ID, _bundle_artifact()),),
            index=_index_artifact(),
        )
        order: list[str] = []

        def publish_bundle(*_args, **_kwargs):
            order.append("bundle")
            return True

        def replace_index(*_args, **_kwargs):
            order.append("index")

        def record(*_args, **kwargs):
            order.append("record")
            self.assertEqual(kwargs["captured_generation"], 3)
            self.assertEqual(kwargs["index_digest"], rendered.index.index_digest)
            return SimpleNamespace(source_generation=3)

        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    side_effect=(
                        _capture(generation=3, basis=None),
                        _capture(generation=3, basis=basis),
                    ),
                ),
                mock.patch.object(
                    projection,
                    "_render_projection",
                    return_value=rendered,
                ),
                mock.patch.object(
                    projection,
                    "_prepare_output_directories",
                ),
                mock.patch.object(
                    projection,
                    "_fixed_output_paths",
                    return_value=(
                        target.db_path.parent,
                        target.resolved_evidence_root,
                        target.resolved_evidence_index,
                        target.resolved_evidence_bundles,
                        target.resolved_evidence_lock,
                    ),
                ),
                mock.patch.object(
                    projection,
                    "zero_wait_artifact_lock",
                    return_value=nullcontext(b"\0"),
                ),
                mock.patch.object(
                    projection,
                    "_publish_immutable_bundle",
                    side_effect=publish_bundle,
                ),
                mock.patch.object(
                    projection,
                    "_replace_index",
                    side_effect=replace_index,
                ),
                mock.patch.object(
                    projection,
                    "record_evidence_projection_outcome",
                    side_effect=record,
                ),
                mock.patch.object(
                    projection,
                    "utc_now",
                    return_value=OBSERVED_AT,
                ),
            ):
                result = projection.run_routine_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )

        self.assertEqual(result.code, "succeeded")
        self.assertEqual(result.publications, 1)
        self.assertEqual(order, ["bundle", "index", "record"])

    def test_one_source_change_gets_one_follow_up_capture(self):
        first_basis = object()
        second_basis = object()
        rendered = (
            projection._RenderedProjection(
                source_generation=1,
                bundles=((BUNDLE_ID, _bundle_artifact(b"first\n")),),
                index=_index_artifact(b"first-index\n"),
            ),
            projection._RenderedProjection(
                source_generation=2,
                bundles=((BUNDLE_ID, _bundle_artifact(b"second\n")),),
                index=_index_artifact(b"second-index\n"),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    side_effect=(
                        _capture(generation=1, basis=None),
                        _capture(generation=1, basis=first_basis),
                        _capture(generation=2, basis=second_basis),
                    ),
                ) as capture,
                mock.patch.object(
                    projection,
                    "_render_projection",
                    side_effect=rendered,
                ),
                mock.patch.object(projection, "_prepare_output_directories"),
                mock.patch.object(
                    projection,
                    "_fixed_output_paths",
                    return_value=(
                        target.db_path.parent,
                        target.resolved_evidence_root,
                        target.resolved_evidence_index,
                        target.resolved_evidence_bundles,
                        target.resolved_evidence_lock,
                    ),
                ),
                mock.patch.object(
                    projection,
                    "zero_wait_artifact_lock",
                    return_value=nullcontext(b"\0"),
                ),
                mock.patch.object(projection, "_publish_immutable_bundle"),
                mock.patch.object(
                    projection,
                    "_replace_index",
                    side_effect=(projection._EvidenceSourceChanged(), None),
                ) as replace,
                mock.patch.object(
                    projection,
                    "record_evidence_projection_outcome",
                    return_value=SimpleNamespace(source_generation=2),
                ) as record,
                mock.patch.object(
                    projection,
                    "utc_now",
                    return_value=OBSERVED_AT,
                ),
            ):
                result = projection.run_routine_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )

        self.assertEqual(
            result,
            projection.EvidenceProjectionRefreshResult("succeeded", 1),
        )
        self.assertEqual(capture.call_count, 3)
        self.assertEqual(replace.call_count, 2)
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["captured_generation"], 2)

    def test_fixed_temporaries_bundle_immutability_and_index_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            projection._prepare_output_directories(target)
            bundle = _bundle_artifact()
            index = _index_artifact()
            observed_temp_names: list[str] = []

            def portable_no_replace(source, destination, *, root):
                self.assertFalse(destination.exists())
                observed_temp_names.append(source.path.name)
                os.rename(source.path, destination)
                return ValidatedFile(destination, source.identity, source.sha256)

            with mock.patch.object(
                projection,
                "rename_no_replace",
                side_effect=portable_no_replace,
            ):
                self.assertTrue(
                    projection._publish_immutable_bundle(
                        target,
                        BUNDLE_ID,
                        bundle,
                    )
                )
                self.assertFalse(
                    projection._publish_immutable_bundle(
                        target,
                        BUNDLE_ID,
                        bundle,
                    )
                )
            bundle_path = (
                target.resolved_evidence_bundles / f"{BUNDLE_ID}.json"
            )
            self.assertEqual(bundle_path.read_bytes(), bundle.document)
            self.assertRegex(
                observed_temp_names[0],
                re.compile(r"^\.taskgov-evidence-bundle-[0-9a-f]{8}\.tmp$"),
            )
            bundle_path.write_bytes(b"changed\n")
            with self.assertRaises(StatePathError):
                projection._publish_immutable_bundle(target, BUNDLE_ID, bundle)
            self.assertTrue(
                projection._publish_immutable_bundle(
                    target,
                    BUNDLE_ID,
                    bundle,
                    replace_existing=True,
                )
            )
            self.assertEqual(bundle_path.read_bytes(), bundle.document)

            with mock.patch.object(
                projection,
                "_generation_guard",
                return_value=nullcontext(),
            ):
                projection._replace_index(
                    target,
                    index,
                    captured_generation=4,
                )
            self.assertEqual(
                target.resolved_evidence_index.read_bytes(),
                index.document,
            )
            self.assertEqual(
                list(target.resolved_evidence_root.rglob("*.tmp")),
                [],
            )

    def test_setup_force_repairs_a_corrupt_referenced_bundle(self):
        basis = object()
        bundle = _bundle_artifact()
        rendered = projection._RenderedProjection(
            source_generation=5,
            bundles=((BUNDLE_ID, bundle),),
            index=_index_artifact(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            projection._prepare_output_directories(target)
            bundle_path = (
                target.resolved_evidence_bundles / f"{BUNDLE_ID}.json"
            )
            bundle_path.write_bytes(b"corrupt\n")
            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    side_effect=(
                        _capture(generation=5, basis=None),
                        _capture(generation=5, basis=basis),
                    ),
                ),
                mock.patch.object(
                    projection,
                    "_render_projection",
                    return_value=rendered,
                ),
                mock.patch.object(
                    projection,
                    "_generation_guard",
                    return_value=nullcontext(),
                ),
                mock.patch.object(
                    projection,
                    "record_evidence_projection_outcome",
                    return_value=SimpleNamespace(source_generation=5),
                ),
                mock.patch.object(
                    projection,
                    "utc_now",
                    return_value=OBSERVED_AT,
                ),
            ):
                result = projection.publish_setup_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )

            self.assertEqual(
                result,
                projection.EvidenceProjectionRefreshResult("succeeded", 1),
            )
            self.assertEqual(bundle_path.read_bytes(), bundle.document)
            self.assertEqual(
                target.resolved_evidence_index.read_bytes(),
                rendered.index.document,
            )
            self.assertEqual(
                list(target.resolved_evidence_root.rglob("*.tmp")),
                [],
            )

    def test_only_setup_force_repairs_an_oversized_bundle_without_reading_it(
        self,
    ):
        basis = object()
        bundle = _bundle_artifact()
        rendered = projection._RenderedProjection(
            source_generation=6,
            bundles=((BUNDLE_ID, bundle),),
            index=_index_artifact(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            projection._prepare_output_directories(target)
            bundle_path = (
                target.resolved_evidence_bundles / f"{BUNDLE_ID}.json"
            )
            _write_sized_file(
                bundle_path,
                projection.BUNDLE_MAX_BYTES + 1,
            )
            original_reader = projection.read_physical_file_bounded
            bounded_reads: list[Path] = []

            def observe_read(path, **kwargs):
                bounded_reads.append(Path(path))
                return original_reader(path, **kwargs)

            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    return_value=_capture(generation=6, basis=basis),
                ),
                mock.patch.object(
                    projection,
                    "_render_projection",
                    return_value=rendered,
                ),
                mock.patch.object(
                    projection,
                    "_generation_guard",
                    return_value=nullcontext(),
                ),
                mock.patch.object(
                    projection,
                    "record_evidence_projection_outcome",
                    return_value=SimpleNamespace(source_generation=6),
                ),
                mock.patch.object(
                    projection,
                    "read_physical_file_bounded",
                    side_effect=observe_read,
                ),
            ):
                routine = projection.run_routine_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )
                self.assertEqual(routine.code, "failed")
                self.assertEqual(
                    bundle_path.stat().st_size,
                    projection.BUNDLE_MAX_BYTES + 1,
                )
                self.assertFalse(target.resolved_evidence_index.exists())
                self.assertIn(bundle_path, bounded_reads)

                setup_read_start = len(bounded_reads)
                repaired = projection.publish_setup_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )

            self.assertEqual(
                repaired,
                projection.EvidenceProjectionRefreshResult("succeeded", 1),
            )
            self.assertNotIn(bundle_path, bounded_reads[setup_read_start:])
            self.assertEqual(bundle_path.read_bytes(), bundle.document)
            self.assertEqual(
                target.resolved_evidence_index.read_bytes(),
                rendered.index.document,
            )

    def test_only_setup_force_repairs_an_oversized_index_without_reading_it(
        self,
    ):
        basis = object()
        rendered = projection._RenderedProjection(
            source_generation=7,
            bundles=(),
            index=_index_artifact(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            projection._prepare_output_directories(target)
            index_path = target.resolved_evidence_index
            _write_sized_file(
                index_path,
                projection.INDEX_MAX_BYTES + 1,
            )
            original_reader = projection.read_physical_file_bounded
            bounded_reads: list[Path] = []

            def observe_read(path, **kwargs):
                bounded_reads.append(Path(path))
                return original_reader(path, **kwargs)

            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    return_value=_capture(generation=7, basis=basis),
                ),
                mock.patch.object(
                    projection,
                    "_render_projection",
                    return_value=rendered,
                ),
                mock.patch.object(
                    projection,
                    "_generation_guard",
                    return_value=nullcontext(),
                ),
                mock.patch.object(
                    projection,
                    "record_evidence_projection_outcome",
                    return_value=SimpleNamespace(source_generation=7),
                ),
                mock.patch.object(
                    projection,
                    "read_physical_file_bounded",
                    side_effect=observe_read,
                ),
            ):
                routine = projection.run_routine_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )
                self.assertEqual(routine.code, "failed")
                self.assertEqual(
                    index_path.stat().st_size,
                    projection.INDEX_MAX_BYTES + 1,
                )
                self.assertIn(index_path, bounded_reads)

                setup_read_start = len(bounded_reads)
                repaired = projection.publish_setup_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )

            self.assertEqual(
                repaired,
                projection.EvidenceProjectionRefreshResult("succeeded", 1),
            )
            self.assertNotIn(index_path, bounded_reads[setup_read_start:])
            self.assertEqual(index_path.read_bytes(), rendered.index.document)

    def test_setup_force_rejects_database_alias_and_destination_identity_race(
        self,
    ):
        basis = object()
        bundle = _bundle_artifact()
        rendered = projection._RenderedProjection(
            source_generation=8,
            bundles=((BUNDLE_ID, bundle),),
            index=_index_artifact(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            projection._prepare_output_directories(target)
            bundle_path = (
                target.resolved_evidence_bundles / f"{BUNDLE_ID}.json"
            )
            try:
                os.link(target.db_path, bundle_path)
            except OSError as exc:
                self.skipTest(f"hard-link setup unavailable: {exc}")
            database_bytes = target.db_path.read_bytes()
            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    return_value=_capture(generation=8, basis=basis),
                ),
                mock.patch.object(
                    projection,
                    "_render_projection",
                    return_value=rendered,
                ),
                mock.patch.object(
                    projection,
                    "record_evidence_projection_outcome",
                    return_value=SimpleNamespace(source_generation=8),
                ),
            ):
                aliased = projection.publish_setup_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )
            self.assertEqual(aliased.code, "failed")
            self.assertEqual(target.db_path.read_bytes(), database_bytes)
            self.assertTrue(os.path.samefile(target.db_path, bundle_path))
            self.assertFalse(target.resolved_evidence_index.exists())

            bundle_path.unlink()
            bundle_path.write_bytes(b"old\n")
            original_temporary = projection._temporary_file

            def race_after_temporary(*args, **kwargs):
                temporary_file = original_temporary(*args, **kwargs)
                bundle_path.write_bytes(b"raced\n")
                return temporary_file

            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    return_value=_capture(generation=8, basis=basis),
                ),
                mock.patch.object(
                    projection,
                    "_render_projection",
                    return_value=rendered,
                ),
                mock.patch.object(
                    projection,
                    "_temporary_file",
                    side_effect=race_after_temporary,
                ),
                mock.patch.object(
                    projection,
                    "record_evidence_projection_outcome",
                    return_value=SimpleNamespace(source_generation=8),
                ),
            ):
                raced = projection.publish_setup_evidence_projection(
                    target,
                    observed_at=OBSERVED_AT,
                )
            self.assertEqual(raced.code, "failed")
            self.assertEqual(bundle_path.read_bytes(), b"raced\n")
            self.assertFalse(target.resolved_evidence_index.exists())
            self.assertEqual(
                list(target.resolved_evidence_root.rglob("*.tmp")),
                [],
            )

    def test_bundle_limit_includes_envelope_and_terminal_lf(self):
        from tests.test_m223_bundle_assembly_pure import sample_payload

        payload = sample_payload()
        artifact = projection.build_bundle_artifact(payload)
        self.assertLess(len(artifact.payload_bytes), len(artifact.document))
        with (
            mock.patch.object(
                projection,
                "BUNDLE_MAX_BYTES",
                len(artifact.document) - 1,
            ),
            self.assertRaises(projection.EvidenceProjectionError) as raised,
        ):
            projection.build_bundle_artifact(payload)
        self.assertEqual(raised.exception.code, "evidence_bundle_too_large")

    def test_status_is_current_only_for_exact_files_and_db_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = _target(Path(temporary))
            projection._prepare_output_directories(target)
            bundle = _bundle_artifact()
            index = _index_artifact()
            bundle_path = (
                target.resolved_evidence_bundles / f"{BUNDLE_ID}.json"
            )
            bundle_path.write_bytes(bundle.document)
            target.resolved_evidence_index.write_bytes(index.document)
            capture = projection._ProjectionCapture(
                maintenance=SimpleNamespace(enabled=True),
                state=SimpleNamespace(
                    due=False,
                    published_generation=7,
                    index_digest=index.index_digest,
                ),
                basis=object(),
            )
            rendered = projection._RenderedProjection(
                source_generation=7,
                bundles=((BUNDLE_ID, bundle),),
                index=index,
            )
            with (
                mock.patch.object(
                    projection,
                    "_capture",
                    return_value=capture,
                ),
                mock.patch.object(
                    projection,
                    "_render_projection",
                    return_value=rendered,
                ),
            ):
                self.assertEqual(
                    projection.inspect_canonical_evidence_status(target),
                    "current",
                )
                bundle_path.write_bytes(b"corrupt\n")
                self.assertEqual(
                    projection.inspect_canonical_evidence_status(target),
                    "repair_required",
                )
                target.resolved_evidence_index.unlink()
                self.assertEqual(
                    projection.inspect_canonical_evidence_status(target),
                    "not_present",
                )

    def test_legacy_cycles_are_index_only(self):
        cycle = SimpleNamespace(
            project_id=PROJECT_ID,
            task_id="tg_task_0123456789abcdef",
            completion_cycle_id="tg_completion_cycle_0123456789abcdef",
            saved_cycle_ordinal=1,
            evidence_basis_version=0,
            completion_evidence_bundle_id=None,
        )
        rendered = projection._render_projection(
            EvidenceProjectionBasis(
                source_schema_version=20,
                project_id=PROJECT_ID,
                source_generation=2,
                cycles=(cycle,),
                bundles=(),
                native_bundles=(),
            )
        )
        self.assertEqual(rendered.bundles, ())
        self.assertEqual(
            rendered.index.payload["entries"],
            [
                {
                    "task_id": cycle.task_id,
                    "completion_cycle_id": cycle.completion_cycle_id,
                    "cycle_ordinal": 1,
                    "bundle_state": "legacy_unknown",
                    "bundle_id": None,
                    "bundle_file": None,
                    "bundle_digest": None,
                    "file_digest": None,
                    "sealed_at": None,
                    "bundle_format_version": None,
                }
            ],
        )

    def test_projection_capture_closes_sqlite_before_returning_basis(self):
        class Connection:
            closed = False

            def close(self):
                self.closed = True

        connection = Connection()
        target = SimpleNamespace(
            project=SimpleNamespace(project_id=PROJECT_ID)
        )
        maintenance = SimpleNamespace(enabled=True)
        state = SimpleNamespace(source_generation=9)
        basis = SimpleNamespace(
            project_id=PROJECT_ID,
            source_generation=9,
        )
        with (
            mock.patch.object(
                projection,
                "connect_initialized_readonly",
                return_value=connection,
            ),
            mock.patch.object(
                projection,
                "read_project_maintenance",
                return_value=maintenance,
            ),
            mock.patch.object(
                projection,
                "read_evidence_projection_state",
                return_value=state,
            ),
            mock.patch.object(
                projection,
                "capture_evidence_projection_basis",
                return_value=basis,
            ),
        ):
            captured = projection._capture(target, include_basis=True)

        self.assertTrue(connection.closed)
        self.assertIs(captured.basis, basis)


if __name__ == "__main__":
    unittest.main()
