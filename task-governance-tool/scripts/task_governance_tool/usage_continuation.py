"""Bounded Usage intake and numerical slices, independent of host Hook lifetime."""

from pathlib import Path
from time import monotonic

from .artifact_lock import ArtifactLockError
from . import usage_continuation_state as state
from . import usage_continuation_repository as progress
from .usage_values import UsageError
from .usage_sources import SourceLocator, locator_root_fingerprints, source_from_locator


def collect_hook(repository, target, payload, roots, project, thread, deadline, *, limit=16, current_limit=4):
    """Durable intent precedes candidate intake under the acknowledgement lock.

    An interrupted intake is replayable through persisted registered locations
    and inventory. Raw Hook input is never retained. The execution lease keeps
    a service slice from competing with this Hook's numerical processing.
    """
    from .usage_locator_collection import _capture, collect_located
    from .usage_evidence_service import refresh_usage
    from .usage_continuation_intake import capture
    gaps, hint = set(), None
    with repository.connection():
        pass
    with state.lease(target, "state", create=True) as root:
        state.request(root, target, locator_root_fingerprints(roots),
                      intake=capture(payload, roots, project, thread))
        metadata_deadline = min(deadline, monotonic() + min(1.0, max(0, deadline - monotonic()) / 4))
        repository.deadline = metadata_deadline
        try:
            hint = _capture(repository, target, payload, roots, Path(project), thread, metadata_deadline)
        except UsageError as error:
            gaps.add(error.code)
        finally:
            repository.deadline = deadline
    try:
        with state.lease(target, "execution"):
            count, observed = collect_located(repository, target, payload, roots, project, thread, deadline,
                limit=limit, current_limit=current_limit, clock=monotonic, capture=False, hint=hint)
            gaps.update(observed)
            gaps.update(refresh_usage(target, deadline=deadline)["diagnostics"])
            return count, gaps
    except ArtifactLockError as error:
        if not error.contended:
            raise
        return 0, gaps | {"usage_pending"}


def run_slice(target, repository, roots, expected, *, deadline, stopped=lambda: False):
    """One existing-budget opportunity; never reissue or invent a Hook event."""
    from .usage_locator_collection import _capture, _pending, _inventory
    from .usage_continuation_intake import restore
    from .usage_ingest_work import IngestWork
    from .usage_evidence_service import refresh_usage
    repository.deadline = deadline
    if expected["roots"] != locator_root_fingerprints(roots):
        return state.update(target, expected, round_=expected["round"], status="unavailable")
    with state.lease(target, "execution"):
        if stopped():
            return expected
        round_ = expected["round"]
        if round_ is None:
            round_ = progress.begin(repository)
            saved = state.update(target, expected, round_=round_, status="pending")
            if saved is None:
                return None
            expected = saved
        try:
            inventory = progress.inventory_needed(repository, round_)
        except UsageError as error:
            if error.code != "cursor_stale":
                raise
            # The current admitted epoch, never the old cursor, owns restart.
            round_ = progress.begin(repository)
            saved = state.update(target, expected, round_=round_, status="pending")
            if saved is None:
                return None
            expected = saved
            inventory = True
        # Metadata opportunities are separate from source/publication work.
        # Advance the operational schedule before fallible work, retaining its
        # input until success. A slow or unavailable candidate cannot consume
        # every later source opportunity, even after an exception or restart.
        if round_["phase"] == "intake":
            item = expected["intakes"][0] if expected["intakes"] else None
            retained = expected["intakes"][1:] + [item] if item is not None else []
            saved = state.update(target, expected, round_={**round_, "phase": "review"},
                                 status="pending", intakes=retained)
            if saved is None:
                return None
            if item is not None:
                try:
                    payload, thread = restore(item, roots, target.project.canonical_repo)
                    hint = _capture(repository, target, payload, roots, target.project.canonical_repo, thread, deadline)
                    accepted = hint is None or repository.locator_by_id(hint.source_id) is not None
                except UsageError:
                    accepted = False
                if accepted:
                    retained = list(saved["intakes"])
                    retained.remove(item)
                return state.update(target, saved, round_=saved["round"], status="pending", intakes=retained)
            expected, round_ = saved, saved["round"]
        if round_["phase"] == "review":
            saved = state.update(target, expected, round_={**round_, "phase": "inventory"}, status="pending")
            if saved is None:
                return None
            _pending(repository, target, roots, deadline, monotonic, set(), limit=1)
            return saved
        if round_["phase"] in {"sources", "publish", "confirm"} and progress.added_sources(repository, round_, expected["roots"]):
            round_ = {**progress.source_round(repository, round_), "reason": round_["reason"]}
        if round_["phase"] == "inventory":
            saved = state.update(target, expected, round_=progress.source_round(repository, round_), status="pending")
            if saved is None:
                return None
            if inventory:
                # Discovery retains candidates but does not charge body work
                # to the residual metadata budget. The ordinary source pass
                # commits attempt watermarks before this page can advance.
                hint = None
                if expected["intakes"] and expected["intakes"][-1]["locator"] is not None:
                    try:
                        hint = source_from_locator(SourceLocator(**expected["intakes"][-1]["locator"]),
                                                   roots, target.project.canonical_repo)
                    except UsageError:
                        pass
                work = IngestWork(repository, (), None, deadline, limit=16, current_limit=0,
                                  clock=monotonic, resume_audits=True)
                _inventory(repository, target, roots, target.project.canonical_repo, hint, work, collect=False)
                if work.gaps - {"usage_pending", "prefix_verification_deferred", "evidence_verification_deferred"}:
                    return state.update(target, saved, round_={**saved["round"], "reason": "waiting_input"},
                                        status="pending")
            return saved
        if round_["phase"] == "sources":
            entries = progress.sources(repository, round_, expected["roots"])
            for entry in entries:
                if stopped() or monotonic() >= deadline:
                    break
                outcome = "pending"
                try:
                    source = source_from_locator(entry.locator, roots, target.project.canonical_repo)
                    audit = repository.collect_source(source, lane="audit", resume_audit_only=True)
                    result = repository.collect_source(source, record_attempt=True)
                    if (audit.get("replay") or result.get("replay") or audit["pending"] != "none"
                            or result["pending"] == "more_records"):
                        outcome = "budget_exhausted"
                    elif result["pending"] == "partial_tail":
                        outcome = "waiting_input"
                except UsageError as error:
                    outcome = "budget_exhausted" if error.code == "usage_pending" else "waiting_input"
                    if error.code != "usage_pending":
                        # Failed non-budget attempts cover discovery candidates,
                        # never numerical data. Without this, one invalid header
                        # pins the inventory page before later obtainable files.
                        repository.attempted(entry.locator.source_id)
                    if error.code not in {"usage_pending", "cursor_stale", "source_changed"}:
                        identity = entry.locator.source_id
                        if identity in repository.known_sources((identity,)):
                            repository.record_gap(identity, entry.locator.thread_id, error.code)
                if outcome == "budget_exhausted":
                    # Retry this unfinished source first in a fresh slice. Moving
                    # past it would repeat earlier successes on every failed pass,
                    # permanently leaving only their residual budget for this unit.
                    # Its numerical cursor retains any partial committed progress.
                    return state.update(target, expected, round_=round_, status=outcome)
                # A checkpoint follows the observed result, never queue rotation.
                round_ = {**round_, "after_order": entry.queue_order,
                          "after_source": entry.locator.source_id,
                          "reason": outcome if outcome != "pending" else round_["reason"]}
                # Keep each completed opportunity even if the next admission or
                # publication exhausts the deadline. This operational frontier
                # makes no validity/catch-up claim: the next slice rechecks its
                # saved identity/epoch, and publication retains its fresh guard.
                saved = state.update(target, expected, round_=round_, status="pending")
                if saved is None:
                    return None
                expected = saved
            if not stopped() and monotonic() < deadline and not progress.sources(repository, round_, expected["roots"], limit=1):
                round_ = {**round_, "phase": "publish"}
                saved = state.update(target, expected, round_=round_, status="pending")
                if saved is None:
                    return None
                expected = saved
        if stopped() or monotonic() >= deadline:
            return state.update(target, expected, round_=round_,
                                status="pending" if stopped() else "budget_exhausted")
        # Publication retries do not restart completed source traversal. A new
        # intake supersedes this request before publishing its candidates.
        if round_["phase"] == "publish":
            publication = refresh_usage(target, deadline=deadline)
            if publication.get("publication") == "current":
                # Save the next opportunity before any more budgeted DB work.
                # A timeout after publication cannot keep restarting its audit.
                saved = state.update(target, expected, round_={**round_, "phase": "confirm"}, status="pending")
                if saved is None or stopped() or monotonic() >= deadline:
                    return saved
                expected, round_ = saved, saved["round"]
            else:
                return state.update(target, expected, round_=round_,
                                    status="budget_exhausted" if monotonic() >= deadline else "unavailable")
        if round_["phase"] == "confirm":
            from .usage_evidence_service import confirm_usage
            admitted = False
            try:
                with confirm_usage(target, repository, deadline=deadline) as (connection, recheck):
                    admitted = True
                    status, checked = progress.completion(repository, round_, expected["roots"], expected["intakes"], connection)
                    recheck()
                    return state.update(target, expected, round_=checked, status=status)
            except UsageError as error:
                if monotonic() >= deadline:
                    return state.update(target, expected, round_=round_, status="budget_exhausted")
                if error.code == "cursor_stale" or (not admitted and error.code == "usage_pending"):
                    # The fresh fence rejected the old publication; reconcile
                    # and republish on a new opportunity, never claim catch-up.
                    return state.update(target, expected, round_={**round_, "phase": "publish"}, status="pending")
                if not admitted:
                    return state.update(target, expected, round_=round_, status="unavailable")
                raise
        return state.update(target, expected, round_=round_, status="pending")
