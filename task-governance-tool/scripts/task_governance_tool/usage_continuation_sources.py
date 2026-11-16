"""Finite source opportunities and absent-input scheduling, outside writers."""

from . import usage_continuation_state as state
from . import usage_continuation_repository as progress
from . import usage_missing_inputs as missing_inputs
from .usage_sources import source_from_locator, archive_locator
from .state_paths import path_lexically_exists
from .usage_values import UsageError


def _failure(repository, entry, code):
    identity = entry.locator.source_id
    if code != "usage_pending":
        repository.attempted(identity)
    if code not in {"usage_pending", "cursor_stale", "source_changed"}:
        if identity in repository.known_sources((identity,)):
            repository.record_gap(identity, entry.locator.thread_id, code)


def _archive(repository, entry, roots, project):
    # The same bounded counterpart used by Hook collection; never widen search.
    locator = archive_locator(entry.locator, roots, project)
    if locator is not None:
        source = source_from_locator(locator, roots, project)
        if path_lexically_exists(source.path):
            repository.remember_locator(locator)


def run(target, repository, roots, expected, round_, *, deadline, stopped, clock):
    missing = dict(expected["missing"])
    visited = attempted = 0
    while visited < 128 and attempted < 8 and not stopped() and clock() < deadline:
        entries = progress.sources(repository, round_, expected["roots"], limit=min(8, 128 - visited))
        if not entries:
            round_ = {**round_, "phase": "publish"}
            break
        for entry in entries:
            if stopped() or clock() >= deadline or attempted >= 8:
                break
            visited += 1
            identity = entry.locator.source_id
            outcome = "pending"
            deferred = missing_inputs.deferred(missing, identity)
            try:
                if deferred:
                    outcome = "available_complete"
                else:
                    source = source_from_locator(entry.locator, roots, target.project.canonical_repo)
                    if missing_inputs.absent(source):
                        _failure(repository, entry, "source_unreadable")
                        outcome = "available_complete" if missing_inputs.retain(missing, identity) else "waiting_input"
                        _archive(repository, entry, roots, target.project.canonical_repo)
                    else:
                        missing.pop(identity, None)
                        attempted += 1
                        audit = repository.collect_source(source, lane="audit", resume_audit_only=True)
                        result = repository.collect_source(source, record_attempt=True)
                        if (audit.get("replay") or result.get("replay") or audit["pending"] != "none"
                                or result["pending"] == "more_records"):
                            outcome = "budget_exhausted"
                        elif result["pending"] == "partial_tail":
                            outcome = "waiting_input"
            except UsageError as error:
                outcome = "budget_exhausted" if error.code == "usage_pending" else "waiting_input"
                _failure(repository, entry, error.code)
            if outcome == "budget_exhausted":
                return state.update(target, expected, round_=round_, status=outcome, missing=missing)
            reason = round_["reason"]
            if outcome == "waiting_input" or (outcome == "available_complete" and reason == "pending"):
                reason = outcome
            round_ = {**round_, "after_order": entry.queue_order,
                      "after_source": identity, "reason": reason}
            # Deferred locations do no filesystem or numerical work. Batch their
            # operational frontier, while preserving each actual opportunity.
            if not deferred:
                saved = state.update(target, expected, round_=round_, status="pending", missing=missing)
                if saved is None:
                    return None
                expected = saved
    if not stopped() and clock() < deadline and not progress.sources(repository, round_, expected["roots"], limit=1):
        round_ = {**round_, "phase": "publish"}
    return state.update(target, expected, round_=round_, status="pending", missing=missing)
