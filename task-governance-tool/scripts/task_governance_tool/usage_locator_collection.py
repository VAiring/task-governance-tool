"""Capture locations before collection, then serve finite direct and inventory work.

Only the already admitted lifecycle calls this composition. Pending reviewer
locations cannot register participants or open transcript bodies. Exact later
Receipt admission is separate from both path capture and numerical attribution.
"""

from contextlib import closing
from pathlib import Path

from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.storage import connect_initialized_readonly
from task_governance_tool.task_ownership import validate_storage_rows as validate_usage_ownership
from task_governance_tool.usage_attribution_repository import registered_participants
from task_governance_tool.usage_ingest_work import IngestWork
from task_governance_tool.usage_locator_admission import find_pending_assignment, pending_assignment_state
from task_governance_tool.usage_sources import (
    source_hint, locator_from_source, source_from_locator, archive_locator,
    locator_root_fingerprints, locate_slice,
)
from task_governance_tool.state_paths import path_lexically_exists
from task_governance_tool.usage_values import UsageError


PENDING_PAGE = 4
DIRECT_PAGE = 8


def _participants(target, deadline):
    try:
        with closing(connect_initialized_readonly(target, deadline=deadline, operation_local=True)) as core:
            validate_usage_ownership(core)
            return registered_participants(core, target.project.project_id)
    except Exception:
        raise UsageError() from None


def _capture(repository, target, payload, roots, project, thread, deadline):
    child = payload["hook_event_name"] == "SubagentStop"
    hint = source_hint(thread, payload.get("agent_transcript_path" if child else "transcript_path"), roots, project)
    locator = locator_from_source(hint, roots) if hint is not None else None
    if payload["hook_event_name"] == "SessionStart":
        repository.register_session(CallerIdentity(thread))
    if locator is None:
        return None
    if thread not in repository.registered_sessions((thread,)):
        # Metadata-only exact assignment first: a reviewer may finish before
        # the parent creates its Receipt. Do not scan unrelated Task history.
        if child:
            pending = find_pending_assignment(target, parent_id=payload["session_id"],
                                              reviewer_id=thread, deadline=deadline)
            if pending is not None:
                repository.remember_locator(locator, pending_review=pending)
                return None
        # Unknown participation is resolved once in the residual inventory
        # stage, with its remaining allowance. Do not put the legacy history
        # query before known direct work or give it a permanently short budget.
        return hint
    repository.remember_locator(locator)
    return hint


def _pending(repository, target, roots, deadline, clock, gaps):
    for entry in repository.locator_page(locator_root_fingerprints(roots), limit=PENDING_PAGE, pending=True):
        if clock() >= deadline:
            gaps.add("usage_pending")
            break
        current = repository.begin_locator_attempt(entry)
        if current is None:
            continue
        state = pending_assignment_state(target, current.pending_review, deadline=deadline)
        if state == "registered":
            repository.register_session(CallerIdentity(current.locator.thread_id))
            repository.promote_locator(current)
        elif state == "revoked":
            repository.discard_pending_locator(current)
        elif state not in {"pending", "unknown"}:
            raise UsageError()
        # Unknown cannot invalidate a candidate. Neither waiting nor revocation
        # is a claim that numerical coverage is complete.
        if state in {"pending", "unknown"}:
            gaps.add("usage_pending")


def _direct(repository, roots, project, hint, work, ceiling):
    entries = list(repository.locator_page(locator_root_fingerprints(roots), limit=DIRECT_PAGE))
    if hint is not None and all(item.locator.source_id != hint.source_id for item in entries):
        current = repository.locator_by_id(hint.source_id)
        if current is not None:
            entries.insert(min(1, len(entries)), current)
    sources, selected = {}, {}
    work.known_sources.update(repository.known_sources([item.locator.source_id for item in entries]))
    for entry in entries:
        if work.clock() >= work.deadline:
            work.gaps.add("usage_pending")
            break
        identity = entry.locator.source_id
        try:
            source = source_from_locator(entry.locator, roots, project)
            sources[identity] = source
            selected[identity] = entry
        except UsageError as error:
            # Invalid/missing physical parents must not monopolize the queue.
            if repository.begin_locator_attempt(entry) is not None:
                work.gaps.add(error.code)
                if identity in work.known_sources:
                    repository.record_gap(identity, entry.locator.thread_id, error.code)

    def begin(identity):
        current = repository.begin_locator_attempt(selected[identity])
        if current is None:
            return False
        # A bounded exact basename check also finds a coexisting archive copy.
        # Absence is optional, so it creates neither a candidate nor a false gap.
        try:
            archived = archive_locator(current.locator, roots, project)
            if archived is not None:
                source = source_from_locator(archived, roots, project)
                if path_lexically_exists(source.path):
                    repository.remember_locator(archived)
        except UsageError as error:
            work.gaps.add(error.code)
        return True

    work.run(sources, list(sources), hint, ceiling=ceiling, before_attempt=begin)


def _inventory(repository, target, roots, project, hint, work):
    if work.slots >= work.limit or work.clock() >= work.deadline:
        work.gaps.add("usage_pending")
        return
    sessions = repository.session_ids() | _participants(target, work.deadline)
    if hint is not None and hint.thread_id in sessions and hint.source_id not in work.attempted:
        locator = locator_from_source(hint, roots)
        if locator is not None:
            repository.register_session(CallerIdentity(hint.thread_id))
            entry = repository.remember_locator(locator)
            work.known_sources.update(repository.known_sources((hint.source_id,)))
            work.run({hint.source_id: hint}, [hint.source_id], hint,
                     before_attempt=lambda _: repository.begin_locator_attempt(entry) is not None)
    if work.slots >= work.limit or work.clock() >= work.deadline:
        work.gaps.add("usage_pending")
        return
    revision, position = repository.discovery_position()
    sources, next_position, complete = locate_slice(
        sessions, roots, project, position, deadline=work.deadline,
        max_sources=min(15, work.limit - work.slots))
    # Persist freshly discovered locations before trying their bodies. The
    # fallback remains best effort; subsequent events need not rediscover them.
    entries = {}
    for identity, source in sources.items():
        if work.clock() >= work.deadline:
            work.gaps.add("usage_pending")
            return
        locator = locator_from_source(source, roots)
        if locator is not None:
            repository.register_session(CallerIdentity(source.thread_id))
            entries[identity] = repository.remember_locator(locator)
    pending = repository.unattempted_sources(sources, position["attempt_floor"])
    if pending:
        work.known_sources.update(repository.known_sources(list(sources)))
        ordered = [identity for identity in sources if identity in pending]

        def begin(identity):
            entry = entries.get(identity)
            return entry is None or repository.begin_locator_attempt(entry) is not None

        work.run(sources, ordered, None, before_attempt=begin)
    if not repository.unattempted_sources(sources, position["attempt_floor"]):
        repository.commit_discovery(revision, next_position, sources, complete,
                                    locator_root_fingerprints=locator_root_fingerprints(roots))
        if complete and repository.missing_sessions(sessions, position["cycle"]):
            work.gaps.add("source_unreadable")


def collect_located(repository, target, payload, roots, project, thread, deadline, *, limit, current_limit, clock):
    """No stage resets the deadline; failed discovery cannot discard direct work."""
    from task_governance_tool.debug_log import aggregate
    work = IngestWork(repository, (), thread, deadline, limit=limit, current_limit=current_limit, clock=clock)
    hint = None
    try:
        # Metadata admission must leave time for already admitted direct work.
        # Sub-deadlines are carved out of, never added to, the event allowance.
        metadata_deadline = min(deadline, clock() + min(1.0, max(0, deadline - clock()) / 4))
        repository.deadline = metadata_deadline
        with aggregate(_capture):
            hint = _capture(repository, target, payload, roots, Path(project), thread, metadata_deadline)
    except UsageError as error:
        work.gaps.add(error.code)
    finally:
        repository.deadline = deadline
    try:
        pending_deadline = min(deadline, clock() + min(1.0, max(0, deadline - clock()) / 4))
        repository.deadline = pending_deadline
        with aggregate(_pending):
            _pending(repository, target, roots, pending_deadline, clock, work.gaps)
    except UsageError as error:
        work.gaps.add(error.code)
    finally:
        repository.deadline = deadline
    try:
        # Reserve source slots for inventory while serving known locations first.
        with aggregate(_direct):
            _direct(repository, roots, Path(project), hint, work, max(1, limit - 4))
    except UsageError as error:
        work.gaps.add(error.code)
    try:
        with aggregate(_inventory):
            _inventory(repository, target, roots, Path(project), hint, work)
    except UsageError as error:
        work.gaps.add(error.code)
    return len(work.successful), work.gaps
