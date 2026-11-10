"""Usage continuation owned by the configured project MCP process.

One OS lease spans the observer's actual lifetime. No thread/tool dispatch,
timer creation, child process or unrelated Task operation starts this service.
Unfinished intent survives shutdown; each execution slice re-admits policy,
runtime, physical project binding and numerical storage.
"""

import math
from pathlib import Path
import threading
from time import monotonic

from .artifact_lock import ArtifactLockError
from . import usage_continuation_state as state
from .usage_sources import source_roots, locator_root_fingerprints
from .state_paths import path_lexically_exists

POLL_SECONDS = 10.0
RETRY_SECONDS = 60.0
CLOSE_SECONDS = 0.5


class UsageServiceObserver:
    def __init__(self, *, skill_root, repo, environment, compatible, _poll_seconds=POLL_SECONDS):
        if (not callable(compatible) or type(_poll_seconds) not in (int, float)
                or not math.isfinite(_poll_seconds) or not 0 < _poll_seconds <= POLL_SECONDS):
            raise ValueError("invalid_usage_observer")
        self.skill_root, self.repo = Path(skill_root), Path(repo)
        # Only the configured root selection, never arbitrary environment data.
        self.environment = {key: environment[key] for key in ("CODEX_HOME", "USERPROFILE", "HOME") if key in environment}
        self.compatible = compatible
        self._poll_seconds = _poll_seconds
        self._stopped = threading.Event()
        self._lifecycle = threading.Lock()
        self._thread = None

    def start(self):
        with self._lifecycle:
            if self._stopped.is_set():
                return False
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="taskgov-usage-continuation", daemon=True)
                self._thread.start()
            return True

    def close(self):
        self._stopped.set()
        with self._lifecycle:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(CLOSE_SECONDS)
        return thread is None or not thread.is_alive()

    def _admit(self, deadline):
        from .setup_feature_config import collection_allowed
        from .usage_lifecycle import _target
        from .usage_evidence_service import repository_for
        from .debug_log import allow_output
        if not collection_allowed(self.skill_root) or self.compatible() is not True:
            return None
        target = _target(self.skill_root, self.repo, repo_explicit=True, operation_local=True, deadline=deadline)
        repository = repository_for(target)
        repository.deadline = deadline
        with repository.connection():
            pass
        allow_output()
        return target, repository

    def _scope(self):
        from .debug_log import operation_scope
        return operation_scope(self.repo, entrypoint=self.skill_root / "scripts/review_wait_server.py",
                               operation="usage_continuation", repo_explicit=True)

    def _run(self):
        from .usage_lifecycle import EVENT_SECONDS
        while not self._stopped.is_set():
            acquired = False
            try:
                with self._scope():
                    admitted = self._admit(monotonic() + EVENT_SECONDS)
                    if admitted is None:
                        return
                    target, _ = admitted
                # Config alone is not evidence that a trusted Hook ever ran.
                # Only actual intake creates this directory and work intent.
                while not path_lexically_exists(target.resolved_usage_runtime):
                    if self._stopped.wait(self._poll_seconds):
                        return
                with state.lease(target, "service"):
                    acquired = True
                    # A second owner may have waited. Re-admit after acquisition.
                    with self._scope():
                        admitted = self._admit(monotonic() + EVENT_SECONDS)
                        if admitted is None or admitted[0] != target or self._stopped.is_set():
                            return
                    roots = source_roots(self.environment)
                    while not self._stopped.is_set():
                        try:
                            with state.lease(target, "state", create=True) as root:
                                previous = state.read(root, target)
                                if previous is not None and previous["roots"] == locator_root_fingerprints(roots):
                                    # Re-admit physical stores after process restart. Numerical
                                    # cursors/queues keep their committed positions; only the
                                    # operational traversal is restarted (including a restore).
                                    state.write(root, {**previous, "round": None, "status": "pending"})
                            break
                        except ArtifactLockError as error:
                            if not error.contended:
                                raise
                            self._stopped.wait(self._poll_seconds)
                    self._observe(target, roots)
                    return
            except ArtifactLockError as error:
                if acquired or not error.contended:
                    return
            except Exception:
                if acquired:
                    # Invalid operational state is not silently repaired.
                    return
                # An unavailable initial DB admission may recover while this
                # service is still running. No candidate state was written.
                self._stopped.wait(RETRY_SECONDS)
            self._stopped.wait(self._poll_seconds)

    def _observe(self, target, roots):
        from .usage_lifecycle import EVENT_SECONDS
        from .usage_continuation import run_slice
        while not self._stopped.is_set():
            expected = None
            delay = self._poll_seconds
            try:
                with state.lease(target, "state") as root:
                    expected = state.read(root, target)
                if expected is not None and expected["status"] != "caught_up":
                    with self._scope():
                        deadline = monotonic() + EVENT_SECONDS
                        admitted = self._admit(deadline)
                        if admitted is None or admitted[0] != target:
                            if admitted is None:
                                state.update(target, expected, round_=expected["round"], status="unavailable")
                            return
                        current, repository = admitted
                        result = run_slice(current, repository, roots, expected, deadline=deadline,
                                           stopped=self._stopped.is_set)
                        if result is not None and result["status"] in {"waiting_input", "waiting_review", "unavailable"}:
                            delay = max(self._poll_seconds, RETRY_SECONDS)
            except ArtifactLockError as error:
                if not error.contended:
                    return
            except Exception as error:
                from .usage_values import UsageError
                exhausted = isinstance(error, UsageError) and error.code == "usage_pending"
                delay = self._poll_seconds if exhausted else max(self._poll_seconds, RETRY_SECONDS)
                if expected is not None:
                    try:
                        state.update(target, expected, round_=expected["round"],
                                     status="budget_exhausted" if exhausted else "unavailable")
                    except Exception:
                        pass
            # A retry always yields. Missing input is not success or a hot loop.
            self._stopped.wait(delay)
