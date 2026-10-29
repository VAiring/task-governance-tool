"""Fixed-path Evidence publication and read-only physical projection status.

Capture fully validated documents through storage, then publish
Bundles before the index. The index generation guard retains its connection
through replacement; outcome recording follows publication.
"""

from __future__ import annotations

import os
import secrets
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from task_governance_tool.artifact_lock import (
    ArtifactLockError,
    zero_wait_artifact_lock,
)
from task_governance_tool.evidence_projection import (
    BUNDLE_MAX_BYTES,
    INDEX_MAX_BYTES,
    _BundleDocument,
    EvidenceProjectionError,
    _IndexDocument,
    _RenderedProjection,
    _inconsistent,
)
from task_governance_tool.state_paths import (
    FileIdentity,
    StatePathError,
    ValidatedFile,
    create_exclusive_durable_file,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    read_physical_file_bounded,
    require_contained,
    unlink_validated_file,
)
from task_governance_tool.backup_metadata_repository import (
    ProjectMaintenanceState,
    read_project_maintenance,
)
from task_governance_tool.evidence_projection_metadata_repository import (
    EvidenceProjectionState,
    read_evidence_projection_state,
    record_evidence_projection_outcome,
)
from task_governance_tool.storage import (
    DatabaseTarget,
    capture_current_evidence_projection,
    connect_initialized_readonly,
    validate_current_database,
    utc_now,
    validate_utc_timestamp,
)
from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.evidence_publication_baseline import (
    BASELINE_FILENAME, BASELINE_MAX_BYTES, BASELINE_TEMP_PREFIX,
    PublicationIdentity, admitted_baseline, baseline_document, database_object,
    publication_identity,
)


MAX_PUBLICATIONS_PER_ATTEMPT = 2
_INDEX_TEMP_PREFIX = ".taskgov-evidence-index-"
_BUNDLE_TEMP_PREFIX = ".taskgov-evidence-bundle-"
_TEMP_SUFFIX = ".tmp"


@dataclass(frozen=True)
class EvidenceProjectionRefreshResult:
    code: str
    publications: int


@dataclass(frozen=True)
class _ProjectionCapture:
    maintenance: ProjectMaintenanceState
    state: EvidenceProjectionState
    rendered: _RenderedProjection | None
    identity: PublicationIdentity | None = None
    acknowledged_index: _IndexDocument | None = None


class _EvidenceSourceChanged(RuntimeError):
    pass


class _EvidenceOptedOut(RuntimeError):
    pass


def _capture(
    target: DatabaseTarget,
    *,
    include_basis: bool,
    only_when_due: bool = False,
    incremental: bool = False,
) -> _ProjectionCapture:
    project_id = target.project.project_id
    physical = database_object(target)
    saved = current = None
    if incremental:
        _, root, index, _, _ = _fixed_output_paths(target)
        saved, _ = _observe_replace_destination(target, root / BASELINE_FILENAME,
            root=root, maximum=BASELINE_MAX_BYTES, allow_unbounded=False)
        current, _ = _observe_replace_destination(target, index,
            root=root, maximum=INDEX_MAX_BYTES, allow_unbounded=False)
        if saved is None or current is None:
            raise _inconsistent()
    with closing(connect_initialized_readonly(target, operation_local=True)) as connection:
        maintenance = read_project_maintenance(connection, project_id)
        state = read_evidence_projection_state(
            connection,
            project_id=project_id,
        )
        if maintenance is None:
            raise _inconsistent()
        identity = publication_identity(connection, target, physical)
        acknowledged = (admitted_baseline(saved, identity, state, current)
                        if incremental else None)
        rendered = None
        if include_basis and (
            not only_when_due or (maintenance.enabled and state.due)
        ):
            if incremental:
                from task_governance_tool.evidence_incremental_repository import (
                    capture_incremental_evidence_projection,
                )
                rendered = capture_incremental_evidence_projection(connection,
                    project_id=project_id, state=state, published=acknowledged.payload)
            else:
                rendered = capture_current_evidence_projection(connection, target)
        if database_object(target) != physical:
            raise _inconsistent()
        if rendered is not None and (
            rendered.project_id != project_id
            or rendered.source_generation != state.source_generation
        ):
            raise _inconsistent()
        return _ProjectionCapture(
            maintenance=maintenance,
            state=state,
            rendered=rendered,
            identity=identity,
            acknowledged_index=(_IndexDocument(acknowledged.document, acknowledged.index_digest)
                                if acknowledged is not None else None),
        )


def _fixed_output_paths(
    target: DatabaseTarget,
) -> tuple[Path, Path, Path, Path, Path]:
    state_root = target.db_path.parent
    evidence_root = target.resolved_evidence_root
    index_path = target.resolved_evidence_index
    bundles_path = target.resolved_evidence_bundles
    lock_path = target.resolved_evidence_lock
    if (
        evidence_root != state_root / "evidence"
        or index_path != evidence_root / "index.json"
        or bundles_path != evidence_root / "bundles"
        or lock_path != evidence_root / "taskgov-evidence.lock"
    ):
        raise StatePathError()
    for path in (evidence_root, index_path, bundles_path, lock_path):
        require_contained(path, state_root)
    return state_root, evidence_root, index_path, bundles_path, lock_path


def _prepare_output_directories(target: DatabaseTarget) -> None:
    state_root, evidence_root, _, bundles_path, _ = _fixed_output_paths(target)
    try:
        evidence_root.mkdir(exist_ok=True)
        inspect_physical_directory(evidence_root, root=state_root)
        bundles_path.mkdir(exist_ok=True)
        inspect_physical_directory(bundles_path, root=evidence_root)
    except StatePathError:
        raise
    except OSError as exc:
        raise StatePathError() from exc


def _temporary_file(
    directory: Path,
    *,
    root: Path,
    prefix: str,
    data: bytes,
    maximum: int,
) -> ValidatedFile:
    name = f"{prefix}{secrets.token_hex(4)}{_TEMP_SUFFIX}"
    return create_exclusive_durable_file(
        directory / name,
        data,
        root=root,
        max_bytes=maximum,
    )


def _discard_temporary(file: ValidatedFile | None, *, root: Path) -> None:
    if file is None or not path_lexically_exists(file.path):
        return
    with suppress(StatePathError):
        unlink_validated_file(file, root=root)


def _matches_document(
    path: Path,
    expected: bytes,
    *,
    root: Path,
    maximum: int,
) -> bool:
    if not path_lexically_exists(path):
        return False
    observed, _ = read_physical_file_bounded(
        path,
        root=root,
        max_bytes=maximum,
    )
    return observed == expected


def _same_file_object(left: FileIdentity, right: FileIdentity) -> bool:
    return (left.device, left.inode) == (right.device, right.inode)


def _observe_replace_destination(
    target: DatabaseTarget,
    destination: Path,
    *,
    root: Path,
    maximum: int,
    allow_unbounded: bool,
) -> tuple[bytes | None, FileIdentity | None]:
    if not path_lexically_exists(destination):
        return None, None
    if allow_unbounded:
        _, identity = inspect_physical_file(
            destination,
            root=root,
        )
        observed = None
    else:
        observed, validated = read_physical_file_bounded(
            destination,
            root=root,
            max_bytes=maximum,
        )
        identity = validated.identity
    _, database_identity = inspect_physical_file(
        target.db_path,
        root=target.db_path.parent,
    )
    if _same_file_object(identity, database_identity):
        raise StatePathError()
    return observed, identity


def _revalidate_replace_destination(
    destination: Path,
    *,
    root: Path,
    expected: FileIdentity | None,
) -> None:
    if expected is None:
        if path_lexically_exists(destination):
            raise StatePathError()
        return
    if not path_lexically_exists(destination):
        raise StatePathError()
    _, current = inspect_physical_file(destination, root=root)
    if current != expected:
        raise StatePathError()


def _publish_immutable_bundle(
    target: DatabaseTarget,
    bundle_id: str,
    artifact: _BundleDocument,
    *,
    replace_existing: bool = False,
) -> bool:
    _, evidence_root, _, bundles_path, _ = _fixed_output_paths(target)
    destination = bundles_path / f"{bundle_id}.json"
    require_contained(destination, evidence_root)
    observed, destination_identity = _observe_replace_destination(
        target,
        destination,
        root=evidence_root,
        maximum=BUNDLE_MAX_BYTES,
        allow_unbounded=replace_existing,
    )
    if destination_identity is not None:
        if not replace_existing:
            if observed == artifact.document:
                return False
            raise StatePathError()

    temporary = _temporary_file(
        bundles_path,
        root=evidence_root,
        prefix=_BUNDLE_TEMP_PREFIX,
        data=artifact.document,
        maximum=BUNDLE_MAX_BYTES,
    )
    try:
        if destination_identity is not None:
            _atomic_replace_temporary(
                temporary,
                destination,
                root=evidence_root,
                maximum=BUNDLE_MAX_BYTES,
                expected_destination=destination_identity,
            )
            temporary = None
            return True
        try:
            rename_no_replace(
                temporary,
                destination,
                root=evidence_root,
            )
        except StatePathError:
            if not replace_existing and _matches_document(
                destination,
                artifact.document,
                root=evidence_root,
                maximum=BUNDLE_MAX_BYTES,
            ):
                return False
            raise
        temporary = None
        return True
    finally:
        _discard_temporary(temporary, root=evidence_root)


def _atomic_replace_temporary(
    temporary: ValidatedFile,
    destination: Path,
    *,
    root: Path,
    maximum: int,
    expected_destination: FileIdentity | None,
) -> None:
    require_contained(destination, root)
    if temporary.path.parent != destination.parent:
        raise StatePathError()
    current_temp, identity = inspect_physical_file(
        temporary.path,
        root=root,
        max_bytes=maximum,
    )
    if identity != temporary.identity:
        raise StatePathError()
    if (
        expected_destination is not None
        and _same_file_object(identity, expected_destination)
    ):
        raise StatePathError()
    _revalidate_replace_destination(
        destination,
        root=root,
        expected=expected_destination,
    )
    parent_before = inspect_physical_directory(
        destination.parent,
        root=root,
    )
    _revalidate_replace_destination(
        destination,
        root=root,
        expected=expected_destination,
    )
    try:
        os.replace(temporary.path, destination)
    except OSError as exc:
        raise StatePathError() from exc
    parent_after = inspect_physical_directory(
        destination.parent,
        root=root,
    )
    published, published_identity = inspect_physical_file(
        destination,
        root=root,
        max_bytes=maximum,
    )
    if (
        current_temp != temporary.path
        or parent_before != parent_after
        or published != destination
        or published_identity != temporary.identity
    ):
        raise StatePathError()


@contextmanager
def _generation_guard(
    target: DatabaseTarget,
    *,
    captured_generation: int,
    expected_identity: PublicationIdentity | None = None,
    expected_state: EvidenceProjectionState | None = None,
    full_audit: bool = False,
):
    project_id = target.project.project_id
    physical = database_object(target) if expected_identity is not None else None
    with closing(connect_initialized_readonly(target, operation_local=not full_audit)) as connection:
        if expected_identity is not None and publication_identity(connection, target, physical) != expected_identity:
            raise _EvidenceSourceChanged()
        maintenance = read_project_maintenance(connection, project_id)
        state = read_evidence_projection_state(
            connection,
            project_id=project_id,
        )
        if maintenance is None or not maintenance.enabled:
            raise _EvidenceOptedOut()
        if state.source_generation != captured_generation:
            raise _EvidenceSourceChanged()
        if expected_state is not None and (
            state.published_generation, state.index_digest
        ) != (expected_state.published_generation, expected_state.index_digest):
            raise _EvidenceSourceChanged()
        yield


def _replace_index(
    target: DatabaseTarget,
    artifact: _IndexDocument,
    *,
    captured_generation: int,
    replace_existing_unbounded: bool = False,
    expected_identity: PublicationIdentity | None = None,
    expected_state: EvidenceProjectionState | None = None,
) -> None:
    state_root, evidence_root, index_path, _, _ = _fixed_output_paths(target)
    _, destination_identity = _observe_replace_destination(
        target,
        index_path,
        root=state_root,
        maximum=INDEX_MAX_BYTES,
        allow_unbounded=replace_existing_unbounded,
    )
    temporary = _temporary_file(
        evidence_root,
        root=state_root,
        prefix=_INDEX_TEMP_PREFIX,
        data=artifact.document,
        maximum=INDEX_MAX_BYTES,
    )
    try:
        with _generation_guard(
            target,
            captured_generation=captured_generation,
            expected_identity=expected_identity,
            expected_state=expected_state,
            full_audit=replace_existing_unbounded,
        ):
            _atomic_replace_temporary(
                temporary,
                index_path,
                root=state_root,
                maximum=INDEX_MAX_BYTES,
                expected_destination=destination_identity,
            )
        temporary = None
    finally:
        _discard_temporary(temporary, root=state_root)


def _save_baseline(
    target: DatabaseTarget, identity: PublicationIdentity, index_document: bytes,
    *, repair: bool = False,
) -> None:
    """Retain acknowledged membership under the Evidence lock, after DB outcome."""
    root = target.resolved_evidence_root
    destination = root / BASELINE_FILENAME
    data = baseline_document(identity, index_document)
    observed, previous = _observe_replace_destination(target, destination,
        root=root, maximum=BASELINE_MAX_BYTES, allow_unbounded=repair)
    if observed == data:
        return
    if database_object(target) != (identity.database_device, identity.database_inode):
        raise _inconsistent()
    temporary = _temporary_file(root, root=root, prefix=BASELINE_TEMP_PREFIX,
                                data=data, maximum=BASELINE_MAX_BYTES)
    try:
        _atomic_replace_temporary(temporary, destination, root=root,
            maximum=BASELINE_MAX_BYTES, expected_destination=previous)
        temporary = None
    finally:
        _discard_temporary(temporary, root=root)


def _record_failure(
    target: DatabaseTarget,
    *,
    generation: int,
    code: str,
    occurred_at: str,
    expected_identity: PublicationIdentity | None,
    expected_state: EvidenceProjectionState | None,
) -> None:
    if expected_identity is None or expected_state is None:
        return
    with suppress(Exception):
        record_evidence_projection_outcome(
            target,
            captured_generation=generation,
            outcome_code=code,
            recorded_at=occurred_at,
            expected_identity=expected_identity,
            expected_state=expected_state,
        )


def _publish_evidence_projection(
    target: DatabaseTarget,
    *,
    force: bool,
    observed_at: str,
) -> EvidenceProjectionRefreshResult:
    from task_governance_tool.debug_log import aggregate
    with aggregate(_publish_evidence_projection, unit='items') as measurement:
        measurement.count(0)
        observed_at = validate_utc_timestamp(
            observed_at,
            field="Evidence projection observation time",
        )
        publications = 0
        captured_generation = 0
        outcome_identity = None
        outcome_state = None
        try:
            first = _capture(target, include_basis=False)
            captured_generation = first.state.source_generation
            outcome_identity, outcome_state = first.identity, first.state
            if not first.maintenance.enabled:
                measurement.mark('skipped')
                return EvidenceProjectionRefreshResult("not_opted_in", 0)
            _prepare_output_directories(target)
            _, _, _, _, lock_path = _fixed_output_paths(target)
            with zero_wait_artifact_lock(lock_path):
                capture = _capture(target, include_basis=True, only_when_due=not force,
                                   incremental=not force)
                for attempt in range(MAX_PUBLICATIONS_PER_ATTEMPT):
                    if attempt:
                        capture = _capture(target, include_basis=True, only_when_due=not force,
                                           incremental=not force)
                    captured_generation = capture.state.source_generation
                    outcome_identity, outcome_state = capture.identity, capture.state
                    if not capture.maintenance.enabled:
                        return EvidenceProjectionRefreshResult(
                            "not_opted_in",
                            publications,
                        )
                    if (
                        not force
                        and not capture.state.due
                    ):
                        if capture.acknowledged_index is not None:
                            _save_baseline(target, capture.identity, capture.acknowledged_index.document)
                        if not publications:
                            measurement.mark('unchanged')
                        return EvidenceProjectionRefreshResult(
                            "succeeded" if publications else "current", publications,
                        )
                    if capture.rendered is None:
                        raise _inconsistent()
                    rendered = capture.rendered
                    for bundle_id, artifact in rendered.bundles:
                        _publish_immutable_bundle(
                            target,
                            bundle_id,
                            artifact,
                            replace_existing=force,
                        )
                        del artifact
                    try:
                        _replace_index(
                            target,
                            rendered.index,
                            captured_generation=rendered.source_generation,
                            replace_existing_unbounded=force,
                            expected_identity=capture.identity,
                            expected_state=capture.state,
                        )
                    except _EvidenceSourceChanged:
                        measurement.mark('revalidated')
                        if attempt + 1 >= MAX_PUBLICATIONS_PER_ATTEMPT:
                            measurement.mark('limit')
                            raise RuntimeError(
                                "Evidence source changed during publication"
                            )
                        # Release the previous generation before building another.
                        del rendered, capture
                        continue
                    except _EvidenceOptedOut:
                        return EvidenceProjectionRefreshResult(
                            "not_opted_in",
                            publications,
                        )

                    state = record_evidence_projection_outcome(
                        target,
                        captured_generation=rendered.source_generation,
                        outcome_code="succeeded",
                        recorded_at=utc_now(),
                        index_digest=rendered.index.index_digest,
                        expected_identity=capture.identity,
                        expected_state=capture.state,
                    )
                    outcome_state = state
                    _save_baseline(target, capture.identity, rendered.index.document, repair=force)
                    publications += 1
                    measurement.count(1)
                    measurement.mark('published')
                    if state.source_generation == rendered.source_generation:
                        return EvidenceProjectionRefreshResult(
                            "succeeded",
                            publications,
                        )
                    if attempt + 1 >= MAX_PUBLICATIONS_PER_ATTEMPT:
                        measurement.mark('limit')
                        raise RuntimeError(
                            "Evidence source changed during publication"
                        )
                    del rendered, capture
        except ArtifactLockError as exc:
            measurement.mark('contended' if exc.contended else 'failed')
            code = "deferred" if exc.contended else "failed"
            _record_failure(
                target,
                generation=captured_generation,
                code=code,
                occurred_at=observed_at,
                expected_identity=outcome_identity,
                expected_state=outcome_state,
            )
            return EvidenceProjectionRefreshResult(code, publications)
        except Exception:
            measurement.mark('failed')
            _record_failure(
                target,
                generation=captured_generation,
                code="failed",
                occurred_at=observed_at,
                expected_identity=outcome_identity,
                expected_state=outcome_state,
            )
            return EvidenceProjectionRefreshResult("failed", publications)
        return EvidenceProjectionRefreshResult("succeeded", publications)


def run_routine_evidence_projection(
    target: DatabaseTarget,
    *,
    observed_at: str | None = None,
) -> EvidenceProjectionRefreshResult:
    """Publish one due generation without waiting or changing its mutation."""

    return _publish_evidence_projection(
        target,
        force=False,
        observed_at=observed_at or utc_now(),
    )


def publish_setup_evidence_projection(
    target: DatabaseTarget,
    *,
    observed_at: str | None = None,
) -> EvidenceProjectionRefreshResult:
    """Force one bounded canonical publication for explicit setup repair."""

    return _publish_evidence_projection(
        target,
        force=True,
        observed_at=observed_at or utc_now(),
    )


def inspect_canonical_evidence_status(target: DatabaseTarget) -> str:
    """Return the read-only status of the fixed DB-derived Evidence files."""

    capture = _capture(target, include_basis=True)
    if capture.rendered is None:
        raise _inconsistent()
    rendered = capture.rendered
    state_root, evidence_root, index_path, bundles_path, _ = (
        _fixed_output_paths(target)
    )
    if not path_lexically_exists(evidence_root):
        return "not_present"
    try:
        inspect_physical_directory(evidence_root, root=state_root)
        if not path_lexically_exists(index_path):
            return "not_present"
        inspect_physical_directory(bundles_path, root=evidence_root)
        if not _matches_document(
            index_path,
            rendered.index.document,
            root=state_root,
            maximum=INDEX_MAX_BYTES,
        ):
            return "repair_required"
        for bundle_id, artifact in rendered.bundles:
            if not _matches_document(
                bundles_path / f"{bundle_id}.json",
                artifact.document,
                root=evidence_root,
                maximum=BUNDLE_MAX_BYTES,
            ):
                return "repair_required"
        baseline, _ = _observe_replace_destination(target, evidence_root / BASELINE_FILENAME,
            root=evidence_root, maximum=BASELINE_MAX_BYTES, allow_unbounded=False)
        if baseline is None:
            return "repair_required"
        admitted_baseline(baseline, capture.identity, capture.state, rendered.index.document)
    except (OSError, StatePathError, EvidenceProjectionError):
        return "repair_required"
    return (
        "current"
        if (
            not capture.state.due
            and capture.state.published_generation
            == rendered.source_generation
            and capture.state.index_digest == rendered.index.index_digest
        )
        else "repair_required"
    )
