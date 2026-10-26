"""One coalesced backup opportunity observer owned by the project MCP service.

The idle loop touches only the fixed opportunity and ownership artifacts. It
does not query SQLite, hash the deployed package, retain a queue or replay a
request after taking ownership. Backup work belongs to the separate one-shot
worker, whose lifetime and completion are not part of observer shutdown.
"""
from __future__ import annotations

import math
import threading

POLL_SECONDS = 10.0
CLOSE_SECONDS = 0.5


class BackupServiceObserver:
    """Observe new opportunities under one project-scoped OS lease.

    The caller supplies an already admitted target and the loaded protocol /
    schema identity. ``compatible`` is passed to the launcher, which calls it
    only after a due decision and before starting a child. An injected launch
    callback owns that same check and its independent diagnostic scope.
    """

    def __init__(self, target, *, compatibility, compatible, launch=None, admit_owner=None,
                 _poll_seconds=POLL_SECONDS):
        if (type(compatibility) is not dict or not callable(compatible)
                or (launch is not None and not callable(launch))
                or (admit_owner is not None and not callable(admit_owner))
                or type(_poll_seconds) not in (int, float)
                or not math.isfinite(_poll_seconds)
                or not 0 < _poll_seconds <= POLL_SECONDS):
            raise ValueError("invalid_backup_observer")
        self.target = target
        self.compatibility = dict(compatibility)
        self._compatible = compatible
        self._launch = launch
        self._owner_admission = admit_owner
        self._poll_seconds = _poll_seconds
        self._stopped = threading.Event()
        self._lifecycle = threading.Lock()
        self._thread = None

    def start(self):
        """Start once; startup advertises no claim that a backup has begun."""
        with self._lifecycle:
            if self._stopped.is_set():
                return False
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="taskgov-backup-opportunity", daemon=True)
                self._thread.start()
            return True

    def close(self):
        """Stop observation with a bounded join, never join or stop a backup.

        An already admitted launch may finish its bounded GO handshake. Its
        observer thread releases the service lease when that call returns;
        shutdown does not release ownership under a still-active dispatcher.
        """
        self._stopped.set()
        with self._lifecycle:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(CLOSE_SECONDS)
        return thread is None or not thread.is_alive()

    def _dispatch(self):
        if self._launch is not None:
            return self._launch(self.target)
        from task_governance_tool.async_backup import launch_backup
        return launch_backup(self.target, _admit_launch=self._compatible)

    def _admit_owner(self):
        if self._owner_admission is not None:
            return self._owner_admission(self.target) is True
        from task_governance_tool.async_backup_repository import read_control
        # read_control closes its snapshot before this returns. Its schema,
        # file and exact binding checks revalidate a possibly old cached target.
        return read_control(self.target).maintenance.enabled is True

    def _run(self):
        from task_governance_tool.artifact_lock import ArtifactLockError
        from task_governance_tool.async_backup_opportunity import (
            activate_service, observe_opportunity, service_lease,
        )
        while not self._stopped.is_set():
            lease_acquired = False
            try:
                with service_lease(self.target) as owner_nonce:
                    lease_acquired = True
                    if self._stopped.is_set():
                        return
                    try:
                        if not self._admit_owner() or self._stopped.is_set():
                            return
                        # Publish presence and baseline old input only after
                        # current admission has closed its DB snapshot.
                        last_request = activate_service(
                            self.target, owner_nonce, self.compatibility)
                    except Exception:
                        # Invalid admission or activation is terminal for this
                        # observer. Retrying would turn unavailable control
                        # artifacts into a database read on every idle tick.
                        return
                    while not self._stopped.wait(self._poll_seconds):
                        try:
                            request = observe_opportunity(
                                self.target, owner_nonce, self.compatibility,
                                after_request_id=last_request)
                        except Exception:
                            # An invalid or temporarily unreadable file is not
                            # consumed. No storage or launch effect is retried.
                            continue
                        if request is None or request["request_id"] == last_request:
                            continue
                        if self._stopped.is_set():
                            break
                        # Consume before dispatch, even if admission or startup
                        # fails or is unknown. A later update must supply a new
                        # opportunity; this is not a retry queue.
                        last_request = request["request_id"]
                        try:
                            self._dispatch()
                        except Exception:
                            pass
            except ArtifactLockError as exc:
                # Only confirmed acquisition contention permits standby.
                # Once owned, cleanup failure cannot authorize reacquisition.
                if lease_acquired or not exc.contended:
                    return
            except Exception:
                # Unsafe paths and other lease failures require service restart.
                return
            if self._stopped.wait(self._poll_seconds):
                return
