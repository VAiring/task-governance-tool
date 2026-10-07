"""Source-only worker owned and joined by one parent MCP stdio session.

This module creates no process, installs nothing, and does not prove that the
Desktop keeps that session or its executor context usable after a parent turn.
The owner must call stop on request/EOF and check worker_alive before claiming
cleanup. Stopping observation never implicitly pauses a reservation.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from task_governance_tool.review_wait_runtime.review_wait_host import HostAdapterError, WaitSnapshot
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
from task_governance_tool.review_wait_runtime.review_wait_runtime import ReviewWaitRuntime, RuntimeError as WaitRuntimeError


class ObserverError(ValueError):
    def __init__(self):
        super().__init__("invalid_observer_configuration")


@dataclass(frozen=True)
class ObserverSnapshot:
    state: str
    worker_alive: bool
    reason: str | None = None


class ReviewWaitObserver:
    """A one-shot, non-daemon worker sharing its runtime's admitted context.

    Wait calls hold no writer lease. Only runtime.observe admits exact terminal
    turns or may shorten the same reservation. Poll results are candidate
    signals, never terminal facts. In-flight host operations retain their own
    finite deadlines; cooperative cancellation stops before the next operation.
    A join timeout is reported as cleanup_unknown and permits no replacement
    worker until the original has actually ended.
    """

    def __init__(self, runtime: ReviewWaitRuntime, *, wait_timeout_ms: int = 30_000,
                 join_timeout_seconds: float = 40, minimum_cycle_seconds: float = 1):
        if (type(wait_timeout_ms) is not int or not 1_000 <= wait_timeout_ms <= 30_000
                or type(join_timeout_seconds) not in (int, float)
                or not 0 < join_timeout_seconds <= 60
                or type(minimum_cycle_seconds) not in (int, float)
                or not 1 <= minimum_cycle_seconds <= 30):
            raise ObserverError()
        self._runtime = runtime
        self._wait_timeout = wait_timeout_ms
        self._join_timeout = join_timeout_seconds
        self._minimum_cycle = minimum_cycle_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state, self._reason = "new", None

    def snapshot(self) -> ObserverSnapshot:
        with self._lock:
            return ObserverSnapshot(self._state, self._thread is not None and self._thread.is_alive(),
                                    self._reason)

    def start(self) -> ObserverSnapshot:
        with self._lock:
            if self._thread is None and self._state == "new":
                self._state = "running"
                self._thread = threading.Thread(target=self._run, name="review-wait-observer", daemon=False)
                try:
                    self._thread.start()
                except Exception:
                    self._state, self._reason = "error", "worker_start_failed"
        return self.snapshot()

    def stop(self) -> ObserverSnapshot:
        """Request cancellation and bounded join; no timer update or retry."""
        self._stop.set()
        with self._lock:
            worker = self._thread
            if worker is None:
                self._state, self._reason = "stopped", None
            elif worker.is_alive():
                self._state, self._reason = "stopping", None
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(self._join_timeout)
        with self._lock:
            if worker is not None and worker.is_alive():
                self._state, self._reason = "cleanup_unknown", "worker_join_timeout"
        return self.snapshot()

    @staticmethod
    def _halt(controller, *, include_ended: bool = True) -> tuple[str, str | None] | None:
        if controller.pending is not None:
            return "pending", "operation_pending"
        if controller.phase in ("closed", "checking"):
            return controller.phase, None
        if controller.needs_reconciliation:
            return "error", "reconciliation_required"
        if include_ended and controller.all_ended:
            return "ended", None
        return None

    def _run(self) -> None:
        final = ("stopped", None)
        cursors: dict[str, str] = {}
        batch_number = 0
        try:
            # This first exact observation retains endings that predate arm or
            # subscription; no future completion event is required for them.
            controller = self._runtime.repository.read().controller
            halted = self._halt(controller, include_ended=False)
            if halted:
                final = halted
                return
            if self._stop.is_set():
                return
            controller = self._runtime.observe(cancelled=self._stop.is_set).controller
            while not self._stop.is_set():
                halted = self._halt(controller)
                if halted:
                    final = halted
                    return
                recorded = {item["reviewer_id"] for item in controller.to_snapshot()["terminal"]}
                remaining = tuple(r.reviewer_id for r in controller.reviewers if r.reviewer_id not in recorded)
                if not remaining:
                    final = ("error", "invalid_wait_state")
                    return
                batches = tuple(remaining[i:i + 8] for i in range(0, len(remaining), 8))
                batch = batches[batch_number % len(batches)]
                batch_number += 1
                # Share one long-wait budget across a full reviewer round;
                # a 64-reviewer set must not take eight 30-second waits.
                timeout_ms = max(1, self._wait_timeout // len(batches))
                started = time.monotonic()
                waited = self._runtime.host.wait_children(batch, timeout_ms=timeout_ms,
                    cursors={child: cursors[child] for child in batch if child in cursors})
                if self._stop.is_set():
                    return
                if not isinstance(waited, WaitSnapshot) or {t.child_id for t in waited.turns} != set(batch):
                    final = ("error", "invalid_wait_result")
                    return
                cursors.update(waited.cursors)
                terminal_candidate = any(turn.status in ("completed", "failed", "interrupted")
                                         for turn in waited.turns)
                # Parent controls may have closed or checked this arm while the
                # public wait was outstanding. Refresh before any exact read.
                controller = self._runtime.repository.read().controller
                halted = self._halt(controller)
                if halted:
                    final = halted
                    return
                if terminal_candidate:
                    # Wait and detailed read snapshots can converge at different
                    # times. An unchanged candidate remains eligible until the
                    # exact read confirms it; confirmed reviewers leave the set.
                    controller = self._runtime.observe(cancelled=self._stop.is_set).controller
                    halted = self._halt(controller)
                    if halted:
                        final = halted
                        return
                # An immediate unchanged host response must not become a busy
                # loop. Event.wait is interruptible by request/EOF stop.
                if self._stop.wait(max(0, self._minimum_cycle - (time.monotonic() - started))):
                    return
                controller = self._runtime.repository.read().controller
        except WaitRuntimeError as error:
            if error.code != "observation_cancelled" or not self._stop.is_set():
                final = ("error", "observation_failed")
        except (HostAdapterError, RepositoryError):
            final = ("error", "observation_failed")
        except Exception:
            final = ("error", "observer_failed")
        finally:
            # Never retain raw exception details, provider text or caller meta.
            with self._lock:
                self._state, self._reason = final
