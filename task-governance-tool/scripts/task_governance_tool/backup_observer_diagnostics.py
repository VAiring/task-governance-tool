"""Explicit Doctor observation of the service lease and its last recorded admission.

Only an observer holding the service lease publishes. Neither a saved event
nor the absence of a record identifies the process presently holding a lease.
"""
from __future__ import annotations

import json

from . import async_backup_opportunity as opportunity
from .async_backup_state import _read_runtime_bytes, _state_guard, lock_owned, runtime_root
from .state_paths import StatePathError, inspect_physical_directory, path_lexically_exists
from .storage import StorageError, utc_now, validate_utc_timestamp


_EVENTS = {
    ("owner_readmission", "database_busy"),
    ("presence_publication", "unavailable"),
    ("presence_publication", "activated"),
}


def _read(root):
    path = root / "observer.json"
    if not path_lexically_exists(path):
        return None
    data = _read_runtime_bytes(path, opportunity.MAX_OPPORTUNITY_BYTES)
    value = json.loads(data.decode("ascii"), object_pairs_hook=opportunity._unique)
    if type(value) is not dict:
        raise ValueError("invalid observer record")
    stage, reason, occurred_at = (value.pop(k) for k in ("stage", "reason", "occurred_at"))
    opportunity._validate(value)
    if (stage, reason) not in _EVENTS:
        raise ValueError("invalid observer event")
    validate_utc_timestamp(occurred_at, field="observer event")
    return {**value, "stage": stage, "reason": reason, "occurred_at": occurred_at}


def record(target, owner, compatibility, stage, reason):
    """Best effort, within the already held lease and outside DB transactions."""
    try:
        if (stage, reason) not in _EVENTS:
            return
        root = runtime_root(target)
        base = opportunity._record(target, owner, compatibility)
        with _state_guard(root, create=True) as directory:
            previous = _read(root)
            opportunity._same_project(previous, target)
            event = {**base, "stage": stage, "reason": reason, "occurred_at": utc_now()}
            opportunity._write(root, "observer.json", ".observer-next.json", event, directory)
    except Exception:
        # An optional diagnostic may never change activation or backup behavior.
        return


def observe(target, *, enabled):
    result = dict(ownership="unknown" if enabled else "not_opted_in",
                  observed_at=utc_now(), last_recorded=None)
    if not enabled:
        return result
    try:
        root = runtime_root(target)
        if not path_lexically_exists(root):
            return result
        directory = inspect_physical_directory(root).identity
        path = root / "service.lock"
        if not path_lexically_exists(path):
            return result
        identity = opportunity._regular(path)
        if identity.size != 1:
            return result
        held = lock_owned(path)
        if held is None:
            return result
        event = None
        if path_lexically_exists(root / "observer.json"):
            try:
                with _state_guard(root):
                    candidate = _read(root)
                    opportunity._same_binding(candidate, target)
                event = candidate
            except (OSError, StatePathError, StorageError, ValueError, TypeError, KeyError):
                pass
        if (opportunity._regular(path) != identity or lock_owned(path) is not held
                or inspect_physical_directory(root).identity != directory):
            return result
        result["ownership"] = "held" if held else "released"
        if event is not None and event["compatibility"] == opportunity.current_compatibility():
            result["last_recorded"] = {key: event[key] for key in ("stage", "reason", "occurred_at")}
    except (OSError, StatePathError, StorageError, ValueError, TypeError, KeyError):
        pass
    return result
