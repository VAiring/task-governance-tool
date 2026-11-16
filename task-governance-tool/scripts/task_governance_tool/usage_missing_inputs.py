"""Scheduling only for physically absent, previously admitted log locations.

This is neither source admission nor a data/integrity certificate. Numerical
gaps and originals remain in their existing repositories.
"""

from time import time

RECHECK_SECONDS = 600
MAX_MISSING = 16384


def deferred(missing, identity, *, now=None):
    now = int(time()) if now is None else now
    due = missing.get(identity)
    # A wall-clock rollback cannot postpone a location indefinitely.
    return due is not None and 0 < due - now <= RECHECK_SECONDS


def ready(missing):
    now = int(time())
    return not missing or any(not deferred(missing, identity, now=now) for identity in missing)


def absent(source):
    """Call only after fresh locator/root/physical admission. Never infer ENOENT
    from the generic source_unreadable classification (e.g. permission denied).
    """
    try:
        source.path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        pass
    return False


def retain(missing, identity):
    if identity not in missing and len(missing) >= MAX_MISSING:
        return False
    missing[identity] = int(time()) + RECHECK_SECONDS
    return True
