"""Explicit offline setup coordinator for the one generated-state cutover.

The package remains the policy/code owner. Private snapshots are not active
state; only retirement, no-replace publication, then activation admit the new
root. No old source artifact is deleted, and no normal command calls this code.
"""

from __future__ import annotations

from contextlib import ExitStack, closing
from dataclasses import replace
from pathlib import Path
import secrets
import uuid

from task_governance_tool.artifact_lock import ArtifactLockError, zero_wait_artifact_lock
from task_governance_tool.backup import (
    copy_database_snapshot, discover_managed_backup_metadata,
    publish_setup_backup, reconcile_private_migration_repository,
)
from task_governance_tool.backup_metadata_repository import configure_project_maintenance
from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.project_binding_repository import (
    compare_and_swap_project_binding, read_project_binding_history,
)
from task_governance_tool.relocation import (
    encode_relocation_token, relocation_token_digest, relocation_token_expiry,
)
from task_governance_tool.setup_state_inventory import (
    copy_inventory, inspect_inventory, inspect_retired_source,
)
from task_governance_tool.state_paths import (
    StatePathError, create_physical_directory_exclusive,
    inspect_physical_directory, path_lexically_exists,
)
from task_governance_tool.state_resolver import (
    ProjectStateResolution, package_state_paths, resolve_package_project_state,
    resolve_retained_source_snapshot, resolve_setup_project_state, resolve_staged_project_state,
)
from task_governance_tool.state_separation import (
    SeparationError, SeparationRecord, inspect_separation, publish_record,
    publish_retirement_marker, separation_paths,
)
from task_governance_tool.storage import (
    DatabaseTarget, SCHEMA_VERSION, StorageError, UnboundDatabaseTarget,
    connect_snapshot_readonly, migrate_bound_database, initialize_uuid_database,
    inspect_setup_state, utc_now,
)
from task_governance_tool.verification_runner_lifecycle import VerificationRunnerLifecycleError
from task_governance_tool.verification_runner_repository import has_pending_verification_runner_cleanup


LAYOUT_WRITES = ("state_layout_retire", "state_layout_publish", "state_layout_activate")


class _ConfirmationActivationObserved(Exception):
    """Unwind setup's locks before validating a concurrently applied token."""


def _directory(path: Path, *, root: Path) -> None:
    if path_lexically_exists(path):
        inspect_physical_directory(path, root=root)
    else:
        create_physical_directory_exclusive(path, root=root)


def _source_database(source: ProjectStateResolution) -> tuple[Path, object | None]:
    if source.legacy_source is not None:
        legacy = source.legacy_source
        recovery = next((item for item in legacy.managed_backups
                         if item.path == legacy.source_database), None)
        return legacy.source_database, recovery if not legacy.primary_present else None
    if source.fixed_recovery is not None:
        selected = source.fixed_recovery.selected
        return selected.path, selected
    return source.paths.database, None


def _source_root(source: ProjectStateResolution) -> Path:
    return source.legacy_source.root if source.legacy_source else source.paths.fixed_root


def _target_at(source: ProjectStateResolution, database: Path) -> DatabaseTarget:
    from task_governance_tool.setup import _stored_target_at

    # Stage paths must never request canonical active-state admission.
    return replace(_stored_target_at(source, database), canonical_fixed=False)


def _pending_runner(target: DatabaseTarget, schema: int) -> None:
    if schema < 20:
        return
    with closing(connect_snapshot_readonly(target.db_path)) as connection:
        if has_pending_verification_runner_cleanup(connection, project_id=target.project.project_id):
            raise SeparationError()


def _staged(root: Path, resolution: ProjectStateResolution) -> ProjectStateResolution:
    observed = resolve_staged_project_state(
        stage_root=root, repo=resolution.current_root.canonical_repo,
        skill_root=resolution.paths.skill_root,
    )
    if observed.error_code is not None or observed.stored_project is None:
        raise SeparationError()
    return observed


def _same_source_basis(record: SeparationRecord, source: ProjectStateResolution) -> None:
    stored = source.stored_project
    if (source.error_code is not None or stored is None
            or source.project_id != record.project_id
            or source.layout != record.source_layout
            or source.source_schema_version != record.source_schema_version
            or stored.canonical_path_hash != record.source_path_hash
            or (stored.binding_generation if stored.source_schema_version >= 14 else 0)
            != record.source_binding_generation):
        raise SeparationError()


def _same_source(record: SeparationRecord, source: ProjectStateResolution, fingerprint: str) -> None:
    _same_source_basis(record, source)
    if fingerprint != record.source_fingerprint:
        raise SeparationError()


def _retained_source(resolution, record, *, planned_source=None):
    root = separation_paths(resolution.paths.state_root).source
    if record.retained_digest is not None and inspect_inventory(
        root, strict=True, repair_evidence=True,
    ).digest != record.retained_digest:
        raise SeparationError()
    retained = resolve_retained_source_snapshot(
        source_root=root, repo=resolution.current_root.canonical_repo,
        skill_root=resolution.paths.skill_root,
    )
    retained = replace(retained, layout=record.source_layout)
    _same_source_basis(record, retained)
    if planned_source is not None and retained.stored_project != planned_source.stored_project:
        raise SeparationError()
    return retained


def _backup_only(source):
    return source is not None and (
        source.fixed_recovery is not None
        or (source.legacy_source is not None and not source.legacy_source.primary_present)
    )


def _require_planned_recovery(planned, observed):
    from task_governance_tool import setup as service

    if not _backup_only(planned):
        return
    matches = observed.error_code is None and (
        observed.fixed_recovery == planned.fixed_recovery
        if planned.fixed_recovery is not None else service._same_legacy_observation(
            planned, observed, expected_binding=planned.binding,
        )
    )
    if not matches:
        raise StorageError("setup_restore_failed", "managed backup could not be restored")


def _confirmation(source, target, token, *, read_only):
    from task_governance_tool import setup as service

    projection = service._relocation_projection(source)
    if token is not None:
        if source is None:
            raise service._RelocationConfirmationFailure("relocation_not_required")
        return service._validate_confirmation(
            token, resolution=source, target=target, checked_at=utc_now(),
        ), projection
    if source is not None and source.binding == "relocation_required":
        if not read_only:
            raise service._RelocationConfirmationFailure("project_relocation_required")
        context = service._relocation_context(source)
        issued_at = utc_now()
        expiry = relocation_token_expiry(issued_at)
        projection = service._relocation_projection(
            source, confirmation_token=encode_relocation_token(context, issued_at=issued_at),
            expires_at=expiry,
        )
    return None, projection


def _plan(source, *, interval, generations):
    from task_governance_tool import setup as service

    state = (inspect_setup_state(_target_at(source, _source_database(source)[0]))
             if source is not None else service._empty_setup_state())
    return service._build_plan(
        state, restore=False, legacy_publish=False, legacy_cleanup=False,
        rebind=bool(source and source.binding == "relocation_required"),
        requested_interval=interval, requested_generations=generations,
        evidence_status="repair_required", viewer_status="repair_required",
    ), state


def _validate_sealed_candidate(resolution, record, source, root, token):
    """Recognize the already-authorized binding result, never infer a rebind."""
    from task_governance_tool import setup as service

    if inspect_inventory(root, strict=True).digest != record.candidate_digest:
        raise SeparationError()
    candidate = _staged(root, resolution)
    observed = candidate.stored_project
    if (candidate.project_id != record.project_id or candidate.binding != "matching"
            or candidate.source_schema_version != SCHEMA_VERSION):
        raise SeparationError()
    target = _target_at(candidate, candidate.paths.database)
    with closing(connect_snapshot_readonly(target.db_path)) as connection:
        history = read_project_binding_history(connection, expected_project_id=record.project_id)
    if source is None:
        if (observed.identity_scheme != "uuid_v1" or observed.binding_generation != 1
                or len(history) != 1 or history[0].reason != "fresh_setup"):
            raise SeparationError()
        return candidate
    original = source.stored_project
    moved = original.canonical_path_hash != resolution.current_root.canonical_path_hash
    expected_lineage = original.binding_lineage + (
        (resolution.current_root.canonical_path_hash,) if moved else ()
    )
    if (observed.identity_scheme != original.identity_scheme
            or observed.binding_generation != original.binding_generation + int(moved)
            or observed.binding_lineage != expected_lineage):
        raise SeparationError()
    if moved:
        last = history[-1]
        if (last.reason != "confirmed_relocation"
                or last.previous_path_hash != original.canonical_path_hash
                or last.canonical_path_hash != resolution.current_root.canonical_path_hash
                or last.confirmation_token_digest is None):
            raise SeparationError()
        if token is not None and relocation_token_digest(token) != last.confirmation_token_digest:
            raise service._RelocationConfirmationFailure("relocation_token_stale")
    elif token is not None:
        _confirmation(source, _target_at(source, _source_database(source)[0]),
                      token, read_only=False)
    return candidate


def _prepare_candidate(resolution, record, source, plan, accepted):
    from task_governance_tool import setup as service

    paths = separation_paths(resolution.paths.state_root)
    _directory(paths.candidate, root=paths.root)
    if any(paths.candidate.iterdir()):
        # A record owns a location, not arbitrary unsealed bytes. Preserve an
        # interrupted preparation rather than guessing which writes completed.
        raise StorageError("setup_incomplete", "setup completed only partially; rerun setup")
    if source is None:
        current = resolution.current_root
        try:
            initialize_uuid_database(
                UnboundDatabaseTarget(
                    canonical_repo=current.canonical_repo,
                    canonical_path_hash=current.canonical_path_hash,
                    display_name=current.display_name,
                    db_path=paths.candidate / "taskgov.sqlite", explicit_db=True,
                    skill_root=resolution.paths.skill_root,
                ),
                project_id_factory=lambda: record.project_id.removeprefix("tg_project_"),
            )
        except Exception as exc:
            raise StorageError("setup_initialization_failed", "project state could not be initialized") from exc
        candidate = _staged(paths.candidate, resolution)
        target = _target_at(candidate, paths.candidate / "taskgov.sqlite")
    else:
        retained = inspect_inventory(paths.source, strict=True, repair_evidence=True)
        if retained.digest != record.retained_digest:
            raise SeparationError()
        copy_inventory(retained, paths.candidate, skip_database=False)
        target = _target_at(source, paths.candidate / "taskgov.sqlite")
        reconcile_private_migration_repository(target)
        if plan.migrate:
            try:
                backup = publish_setup_backup(target, plan.publication_retention)
            except Exception as exc:
                raise StorageError("setup_backup_failed", "setup backup could not be completed") from exc
            try:
                migrate_bound_database(
                    target, setup_backup=backup if record.source_schema_version < 10 else None,
                    managed_backups=discover_managed_backup_metadata(target),
                )
            except Exception as exc:
                raise StorageError("setup_migration_failed", "project state could not be migrated") from exc
    if plan.configure:
        try:
            configure_project_maintenance(
                target, requested_interval_minutes=plan.interval_minutes,
                requested_generations=plan.generations,
            )
        except Exception as exc:
            raise StorageError("setup_incomplete", "setup completed only partially; rerun setup") from exc
    if accepted is not None:
        context = accepted.claims.context
        target = replace(service._bound_target_at(
            source, paths.candidate / "taskgov.sqlite",
            binding_path_hash=context.old_path_hash,
            binding_generation=context.binding_generation,
        ), canonical_fixed=False)
        compare_and_swap_project_binding(
            target, project_id=context.project_id, identity_scheme=context.identity_scheme,
            expected_generation=context.binding_generation, expected_old_hash=context.old_path_hash,
            new_hash=context.new_path_hash, new_display_name=resolution.current_root.display_name,
            reason="confirmed_relocation", confirmation_token_digest=accepted.digest,
            bound_at=accepted.checked_at,
        )
        target = replace(target, project=replace(target.project,
                         canonical_path_hash=context.new_path_hash,
                         display_name=resolution.current_root.display_name),
                         binding_path_hash=context.new_path_hash,
                         binding_generation=context.binding_generation + 1)
    try:
        service._publish_evidence(target)
        service._publish_viewer(resolution.paths.skill_root, target)
    except Exception as exc:
        raise StorageError("setup_incomplete", "setup completed only partially; rerun setup") from exc
    candidate = _staged(paths.candidate, resolution)
    if (candidate.project_id != record.project_id or candidate.binding != "matching"
            or candidate.source_schema_version != SCHEMA_VERSION):
        raise SeparationError()
    candidate_state = inspect_setup_state(target)
    if (candidate_state.needs_initialize or candidate_state.needs_migration
            or service._evidence_status(target, setup_state=candidate_state) != "current"
            or service._viewer_status(resolution.paths.skill_root, target,
                                      setup_state=candidate_state) != "current"):
        raise SeparationError()
    return inspect_inventory(paths.candidate, strict=True).digest


def run_state_separation(
    *, inspection, resolution, repo, repo_explicit, script_path, read_only,
    backup_interval_minutes, backup_generations, confirmation_token,
):
    """Plan without writes, or coordinate one explicitly requested cutover."""
    from task_governance_tool import setup as service

    scope = inspection.scope
    migration = resolution.layout_migration
    assert scope is not None and migration is not None
    state_root = resolution.paths.state_root
    paths = separation_paths(state_root)
    old = package_state_paths(scope.skill_root)
    record = migration.transition.record
    prepared_before_invocation = record is not None and record.candidate_digest is not None
    source = migration.source
    completed: list[str] = (["state_layout_retire"]
                            if migration.transition.marker_matches else [])
    data = service._setup_data(planned_writes=list(LAYOUT_WRITES))
    project_id = record.project_id if record else resolution.project_id
    confirmation_project_id = project_id

    def validate_fresh_retry_source():
        # Marker preparation may have created an empty old current directory.
        # Only this sealed fresh transition can resume through that directory.
        inspect_retired_source(old.state_root, paths.source, record)
        if path_lexically_exists(old.fixed_root) and any(old.fixed_root.iterdir()):
            raise SeparationError()

    def confirmation_was_activated(observed=None):
        # This is only a supplied-token race before this cutover's durable
        # prefix, never a general setup retry or permission to select old data.
        if (confirmation_token is None or read_only or completed
                or confirmation_project_id is None):
            return False
        if observed is None:
            try:
                observed = inspect_separation(state_root=state_root, old_database=old.database)
            except (OSError, StatePathError, SeparationError):
                return False
        return bool(
            observed.state == "activated" and observed.marker_matches
            and observed.record is not None
            and observed.record.project_id == confirmation_project_id
        )

    def replay_current_confirmation():
        # Called only after ExitStack unwinds every held lock. Reuse the same
        # invocation's one ignore preflight, but retain current setup's complete
        # structural checks and token validation against the activated DB.
        return service._run_setup_from_inspection(
            inspection=inspection, repo=repo, repo_explicit=repo_explicit,
            script_path=script_path, read_only=read_only,
            backup_interval_minutes=backup_interval_minutes,
            backup_generations=backup_generations, confirmation_token=confirmation_token,
        )

    def source_preflight_failure(observed):
        code = observed.error_code
        return service._preflight_failure(
            inspection, code=code, project_id=project_id,
            message=service.SETUP_ERROR_MESSAGES.get(
                code, service.PROJECT_STATE_MESSAGES.get(
                    code, "project state could not be read safely")),
        )

    def failure(code):
        data["completed_writes"] = list(completed)
        data["status"] = None
        return service.SetupServiceResult(
            False, project_id, data, error_code=code,
            error_message=service.SETUP_ERROR_MESSAGES.get(
                code, service.PROJECT_STATE_MESSAGES.get(code, "setup completed only partially; rerun setup")),
        )

    try:
        # Missing old marker after activation is a bounded barrier-only repair;
        # never compare an evolving active DB with its historical candidate hash.
        if migration.action == "repair_barrier":
            def inspect_old_material(*, held_locks=None):
                try:
                    residue = service._inspect_setup_residue(replace(resolution, paths=old))
                except service.StateTransitionError as exc:
                    raise StorageError("setup_incomplete", "setup completed only partially; rerun setup") from exc
                if residue is not None:
                    raise StorageError("setup_incomplete", "setup completed only partially; rerun setup")
                return inspect_retired_source(
                    old.state_root, paths.source, record, held_locks=held_locks,
                )

            try:
                inspect_old_material()
            except (OSError, StatePathError, SeparationError,
                    VerificationRunnerLifecycleError, StorageError) as exc:
                code = exc.code if isinstance(exc, StorageError) else "project_state_unreadable"
                return service._preflight_failure(
                    inspection, code=code, project_id=project_id,
                    message=service.SETUP_ERROR_MESSAGES.get(
                        code, service.PROJECT_STATE_MESSAGES.get(code,
                            "project state could not be read safely")),
                )
            active = _staged(resolution.paths.fixed_root, resolution)
            active_target = _target_at(active, _source_database(active)[0])
            active_state = inspect_setup_state(active_target)
            _, barrier_relocation = _confirmation(
                active, active_target, confirmation_token, read_only=read_only,
            )
            evidence_status = service._evidence_status(active_target, setup_state=active_state)
            viewer_status = service._viewer_status(scope.skill_root, active_target, setup_state=active_state)
            repair_plan = service._build_plan(
                active_state, restore=active.fixed_recovery is not None,
                legacy_publish=False, legacy_cleanup=False,
                rebind=active.binding == "relocation_required",
                requested_interval=backup_interval_minutes,
                requested_generations=backup_generations,
                evidence_status=evidence_status, viewer_status=viewer_status,
            )
            data = service._setup_data(
                planned_writes=["state_layout_retire", *repair_plan.planned_writes],
                schema_from=active.source_schema_version,
                maintenance_enabled=active_state.maintenance_enabled,
                interval_minutes=repair_plan.interval_minutes,
                generations=repair_plan.generations, evidence_status=evidence_status,
                viewer_status=viewer_status, relocation=barrier_relocation,
            )
            if read_only:
                data["status"] = ("relocation_preview" if barrier_relocation["required"]
                                  else "setup_preview")
                return service.SetupServiceResult(True, project_id, data, text="setup changes are planned")
            with ExitStack() as locks:
                locks.enter_context(zero_wait_artifact_lock(resolution.paths.transition_lock))
                _directory(old.state_root, root=scope.skill_root)
                locks.enter_context(zero_wait_artifact_lock(old.transition_lock))
                if service._revalidate_scope(
                    repo=repo, repo_explicit=repo_explicit, script_path=script_path,
                ) != scope:
                    raise SeparationError()
                observed = inspect_separation(state_root=state_root, old_database=old.database)
                if observed.state != "repair_barrier" or observed.record != record:
                    raise SeparationError()
                old_source_root = inspect_old_material()
                held: dict[Path, bytes] = {}
                if old_source_root is not None:
                    for relative in (
                        "verification-runner/taskgov-verification-runner.lock",
                        "evidence/taskgov-evidence.lock", "viewer/taskgov-viewer.lock",
                        "backups/taskgov-backup.lock",
                    ):
                        lock = old_source_root / relative
                        if path_lexically_exists(lock.parent):
                            held[lock] = locks.enter_context(zero_wait_artifact_lock(lock))
                inspect_old_material(held_locks=held)
                _directory(old.fixed_root, root=old.state_root)
                publish_retirement_marker(old.database, record, expected=None)
                completed.append("state_layout_retire")
            result = service._run_setup_from_inspection(
                inspection=inspection, repo=repo, repo_explicit=repo_explicit, script_path=script_path,
                read_only=False, backup_interval_minutes=backup_interval_minutes,
                backup_generations=backup_generations, confirmation_token=confirmation_token,
            )
            result.data["planned_writes"] = ["state_layout_retire", *result.data["planned_writes"]]
            result.data["completed_writes"] = ["state_layout_retire", *result.data["completed_writes"]]
            if result.ok:
                result.data["status"] = "setup_complete"
            return result

        if not migration.transition.marker_matches:
            try:
                service.inspect_state_transition_lock(old.state_root)
            except service.StateTransitionError as exc:
                return service._preflight_failure(
                    inspection, code="setup_incomplete",
                    message=service.SETUP_ERROR_MESSAGES["setup_incomplete"],
                    project_id=resolution.project_id,
                )
            try:
                residue_basis = source or replace(resolution, paths=old)
                residue = service._inspect_setup_residue(residue_basis)
            except service.StateTransitionError:
                return service._preflight_failure(
                    inspection, code="setup_incomplete",
                    message=service.SETUP_ERROR_MESSAGES["setup_incomplete"],
                )
            if residue is not None:
                raise StorageError("setup_incomplete", "setup completed only partially; rerun setup")
        if record is not None:
            for private_root, sealed_digest in (
                (paths.source, record.retained_digest), (paths.candidate, record.candidate_digest),
            ):
                if sealed_digest is None and path_lexically_exists(private_root) and any(private_root.iterdir()):
                    raise StorageError("setup_incomplete", "setup completed only partially; rerun setup")
        if source is None and record is not None and record.source_layout is not None:
            if record.retained_digest is None:
                raise SeparationError()
            source = _retained_source(resolution, record)
        if source is not None and source.stored_project.legacy_cleanup_pending:
            # Separation owns no deletion of the prior layout. An outstanding
            # legacy cleanup intent cannot silently acquire a different root.
            raise StorageError("setup_incomplete", "setup completed only partially; rerun setup")
        if source is None and confirmation_token is not None:
            # Even an already-sealed fresh candidate must not silently discard
            # an invalid or unrelated caller confirmation on the retry.
            service._validate_confirmation(
                confirmation_token, resolution=resolution, target=None, checked_at=utc_now(),
            )
        target = _target_at(source, _source_database(source)[0]) if source else None
        plan, source_state = _plan(source, interval=backup_interval_minutes, generations=backup_generations)
        schema_from = record.source_schema_version if record else (source.source_schema_version if source else None)
        observed_evidence = (service._evidence_status(target, setup_state=source_state)
                             if target is not None else "not_present")
        observed_viewer = (service._viewer_status(scope.skill_root, target, setup_state=source_state)
                           if target is not None else "not_present")
        data = service._setup_data(
            planned_writes=list(LAYOUT_WRITES), schema_from=schema_from,
            maintenance_enabled=source_state.maintenance_enabled,
            interval_minutes=plan.interval_minutes, generations=plan.generations,
            evidence_status=observed_evidence, viewer_status=observed_viewer,
            relocation=service._relocation_projection(source),
        )
        sealed_root = None
        if record is not None and record.candidate_digest is not None:
            sealed_root = (resolution.paths.fixed_root if path_lexically_exists(resolution.paths.fixed_root)
                           else paths.candidate)
            sealed_candidate = _validate_sealed_candidate(
                resolution, record, source, sealed_root, confirmation_token,
            )
            if source is None and not migration.transition.marker_matches:
                validate_fresh_retry_source()
            if completed and sealed_root == resolution.paths.fixed_root:
                completed.append("state_layout_publish")
            plan, _ = _plan(sealed_candidate, interval=backup_interval_minutes,
                            generations=backup_generations)
            data["backup_interval_minutes"] = plan.interval_minutes
            data["backup_generations"] = plan.generations
            if plan.configure:
                data["planned_writes"].append("maintenance_configure")
            accepted, projection = None, service._relocation_projection(sealed_candidate)
        else:
            accepted, projection = _confirmation(source, target, confirmation_token, read_only=read_only)
        data["relocation"] = projection
        if source is not None:
            _pending_runner(target, source.source_schema_version)
            source_inventory = inspect_inventory(_source_root(source), strict=False, repair_evidence=True)
            if record is not None and not migration.transition.marker_matches:
                # Before fencing this is the original source, not its SQLite
                # backup snapshot, whose bytes have a separate retained seal.
                try:
                    _same_source(record, source, source_inventory.digest)
                except SeparationError as exc:
                    if _backup_only(source):
                        raise StorageError("setup_restore_failed", "managed backup could not be restored") from exc
                    raise
        if record is not None and record.retained_digest is not None:
            if inspect_inventory(paths.source, strict=True, repair_evidence=True).digest != record.retained_digest:
                raise SeparationError()
        if read_only:
            data["status"] = "relocation_preview" if projection["required"] else "setup_preview"
            data["completed_writes"] = []
            return service.SetupServiceResult(True, project_id, data, text="setup changes are planned")

        service._ensure_state_root(scope, resolution)
        with ExitStack() as locks:
            locks.enter_context(zero_wait_artifact_lock(resolution.paths.transition_lock))
            _directory(old.state_root, root=scope.skill_root)
            locks.enter_context(zero_wait_artifact_lock(old.transition_lock))
            refreshed_scope = service._revalidate_scope(
                repo=repo, repo_explicit=repo_explicit, script_path=script_path,
            )
            if refreshed_scope != scope:
                raise SeparationError()
            transition = inspect_separation(state_root=state_root, old_database=old.database)
            if transition.record != record:
                if confirmation_was_activated(transition):
                    raise _ConfirmationActivationObserved()
                raise SeparationError()
            held: dict[Path, bytes] = {}
            if not transition.marker_matches:
                planned_source = source
                if prepared_before_invocation and source is None:
                    validate_fresh_retry_source()
                    current_source = None
                else:
                    current_source = resolve_package_project_state(skill_root=scope.skill_root, repo=scope.canonical_repo)
                _require_planned_recovery(planned_source, current_source)
                if current_source is not None and current_source.error_code is not None:
                    return source_preflight_failure(current_source)
                if migration.action == "fresh" and current_source.layout != "missing":
                    # A fresh plan must not adopt recovery data that appeared
                    # after inspection. Preserve it for an explicit new setup.
                    raise StorageError("setup_restore_failed", "managed backup could not be restored")
                if current_source is not None and current_source.layout != "missing":
                    source = current_source
                    root = _source_root(source)
                    for relative in (
                        "verification-runner/taskgov-verification-runner.lock",
                        "evidence/taskgov-evidence.lock", "viewer/taskgov-viewer.lock",
                        "backups/taskgov-backup.lock",
                    ):
                        lock = root / relative
                        if path_lexically_exists(lock.parent):
                            held[lock] = locks.enter_context(zero_wait_artifact_lock(lock))
                    source = resolve_package_project_state(skill_root=scope.skill_root, repo=scope.canonical_repo)
                    _require_planned_recovery(planned_source, source)
                    if source.error_code is not None:
                        return source_preflight_failure(source)
                    source_path, recovery = _source_database(source)
                    target = _target_at(source, source_path)
                    _pending_runner(target, source.source_schema_version)
                    plan, _ = _plan(source, interval=backup_interval_minutes, generations=backup_generations)
                    data["backup_interval_minutes"] = plan.interval_minutes
                    data["backup_generations"] = plan.generations
                    if record is not None and record.candidate_digest is not None:
                        sealed_candidate = _validate_sealed_candidate(
                            resolution, record, source, paths.candidate, confirmation_token,
                        )
                        plan, _ = _plan(sealed_candidate, interval=backup_interval_minutes,
                                        generations=backup_generations)
                        data["backup_interval_minutes"] = plan.interval_minutes
                        data["backup_generations"] = plan.generations
                        accepted = None
                    else:
                        accepted, _ = _confirmation(source, target, confirmation_token, read_only=False)
                    try:
                        inventory = inspect_inventory(root, strict=False, held_locks=held, repair_evidence=True)
                    except (OSError, StatePathError, SeparationError, VerificationRunnerLifecycleError) as exc:
                        if _backup_only(planned_source):
                            raise StorageError("setup_restore_failed", "managed backup could not be restored") from exc
                        raise
                    if record is None:
                        stored = source.stored_project
                        record = SeparationRecord(
                            1, secrets.token_hex(16), "private", source.project_id,
                            source.layout, source.source_schema_version,
                            stored.binding_generation if source.source_schema_version >= 14 else 0,
                            stored.canonical_path_hash, inventory.digest,
                        )
                    else:
                        try:
                            _same_source(record, source, inventory.digest)
                        except SeparationError as exc:
                            if _backup_only(planned_source):
                                raise StorageError("setup_restore_failed", "managed backup could not be restored") from exc
                            raise
                else:
                    if source is not None or (record and record.source_layout is not None):
                        raise SeparationError()
                    if record is None:
                        record = SeparationRecord(1, secrets.token_hex(16), "private",
                                                  "tg_project_" + uuid.uuid4().hex)
                if transition.record is None:
                    publish_record(state_root, record, expected=None)
                project_id = record.project_id
                if source is not None and record.retained_digest is None:
                    _directory(paths.source, root=paths.root)
                    if any(paths.source.iterdir()):
                        raise StorageError("setup_incomplete", "setup completed only partially; rerun setup")
                    retained_target = _target_at(source, paths.source / "taskgov.sqlite")
                    try:
                        copy_database_snapshot(
                            source_path=source_path, source_target=target, destination_target=retained_target,
                            expected_source_identity=recovery.identity if recovery else None,
                            require_recovery_content_valid=recovery is not None,
                            expected_recovery_observation=recovery,
                        )
                        copy_inventory(inventory, paths.source, skip_database=True)
                        _retained_source(resolution, record, planned_source=source)
                    except Exception as exc:
                        if recovery is not None:
                            raise StorageError("setup_restore_failed", "managed backup could not be restored") from exc
                        raise
                    successor = replace(record, retained_digest=inspect_inventory(
                        paths.source, strict=True, repair_evidence=True).digest)
                    publish_record(state_root, successor, expected=record)
                    record = successor
                if record.candidate_digest is None:
                    digest = _prepare_candidate(resolution, record, source, plan, accepted)
                    successor = replace(record, candidate_digest=digest)
                    publish_record(state_root, successor, expected=record)
                    record = successor
                if source is not None:
                    try:
                        final_inventory = inspect_inventory(
                            root, strict=False, held_locks=held, repair_evidence=True,
                        )
                        final_source = resolve_package_project_state(skill_root=scope.skill_root, repo=scope.canonical_repo)
                        _require_planned_recovery(planned_source, final_source)
                        _same_source(record, final_source, final_inventory.digest)
                    except (OSError, StatePathError, SeparationError, VerificationRunnerLifecycleError) as exc:
                        if _backup_only(planned_source):
                            raise StorageError("setup_restore_failed", "managed backup could not be restored") from exc
                        raise
                sealed = _validate_sealed_candidate(resolution, record, source, paths.candidate, confirmation_token)
                sealed_state = inspect_setup_state(_target_at(sealed, sealed.paths.database))
                if not prepared_before_invocation and (
                        sealed_state.backup_interval_minutes != plan.interval_minutes
                        or sealed_state.backup_generations != plan.generations):
                    raise StorageError("setup_incomplete", "setup completed only partially; rerun setup")
                _directory(old.fixed_root, root=old.state_root)
                expected = (next((item for item in inventory.files if item.path == old.database), None)
                            if source is not None else None)
                if expected is None and path_lexically_exists(old.database):
                    raise SeparationError()
                publish_retirement_marker(old.database, record, expected=expected)
            if "state_layout_retire" not in completed:
                completed.append("state_layout_retire")
            if record.retained_digest is not None and inspect_inventory(
                paths.source, strict=True, repair_evidence=True,
            ).digest != record.retained_digest:
                raise SeparationError()
            if record.phase == "private":
                successor = replace(record, phase="fenced")
                publish_record(state_root, successor, expected=record)
                record = successor
            if not path_lexically_exists(resolution.paths.fixed_root):
                if inspect_inventory(paths.candidate, strict=True).digest != record.candidate_digest:
                    raise SeparationError()
                candidate = _staged(paths.candidate, resolution)
                if candidate.project_id != record.project_id or candidate.binding != "matching":
                    raise SeparationError()
                rename_no_replace(inspect_physical_directory(paths.candidate, root=state_root),
                                  resolution.paths.fixed_root, root=state_root)
            if "state_layout_publish" not in completed:
                completed.append("state_layout_publish")
            if inspect_inventory(resolution.paths.fixed_root, strict=True).digest != record.candidate_digest:
                raise SeparationError()
            successor = replace(record, phase="activated")
            publish_record(state_root, successor, expected=record)
            completed.append("state_layout_activate")
        active = resolve_setup_project_state(skill_root=scope.skill_root, repo=scope.canonical_repo)
        if active.error_code is not None or active.target is None or active.project_id != project_id:
            raise SeparationError()
        data.update(status="setup_complete", completed_writes=list(completed), maintenance_enabled=True,
                    evidence_status="current" if prepared_before_invocation else "published",
                    viewer_status="current" if prepared_before_invocation else "published",
                    relocation=service._relocation_projection(active))
        if prepared_before_invocation and plan.configure:
            # A sealed candidate is immutable until activation. Apply new retry
            # options only to the active DB, through ordinary setup configuration.
            try:
                scope = service._revalidate_scope(
                    repo=repo, repo_explicit=repo_explicit, script_path=script_path,
                )
                active_target = service._matching_fixed_target(
                    scope, expected_project_id=project_id,
                )
                data["backup_interval_minutes"], data["backup_generations"] = (
                    configure_project_maintenance(
                        active_target, requested_interval_minutes=backup_interval_minutes,
                        requested_generations=backup_generations,
                    )
                )
                completed.append("maintenance_configure")
                data["completed_writes"] = list(completed)
            except Exception as exc:
                raise StorageError("setup_incomplete", "setup completed only partially; rerun setup") from exc
        return service.SetupServiceResult(True, project_id, data, text="setup completed")
    except _ConfirmationActivationObserved:
        if confirmation_was_activated():
            return replay_current_confirmation()
        return service._preflight_failure(
            inspection, code="project_state_unreadable", project_id=project_id,
            message=service.PROJECT_STATE_MESSAGES["project_state_unreadable"],
        )
    except service._RelocationConfirmationFailure as exc:
        if confirmation_token is not None:
            data["planned_writes"] = []
            completed.clear()
        return failure(exc.code)
    except ArtifactLockError as exc:
        return failure("database_busy" if exc.contended else "project_state_unreadable")
    except StorageError as exc:
        if confirmation_was_activated():
            return replay_current_confirmation()
        return failure(exc.code if exc.code in service.PROJECT_STATE_MESSAGES
                       or exc.code in service.SETUP_ERROR_MESSAGES else "setup_incomplete")
    except (OSError, StatePathError, SeparationError, VerificationRunnerLifecycleError):
        if confirmation_was_activated():
            return replay_current_confirmation()
        return failure("setup_incomplete" if completed else "project_state_unreadable")
    except Exception:
        return failure("setup_incomplete")
