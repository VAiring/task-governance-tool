"""Test-only phase tracing for native Runner prelaunch failures.

The trace intentionally retains only closed phase labels.  It never records
arguments, paths, exception text, handles, or environment values.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from functools import wraps
from typing import Any, Callable, Iterator
from unittest.mock import patch


_NOT_ENTERED = "unclassified"
_PATH_RECHECK = "path_recheck"
_JOB_PROOF = "job_proof"
_STDIO_PROOF = "stdio_proof"
_CHILD_CREATE = "child_create"

_PHASES = frozenset(
    {
        _NOT_ENTERED,
        _PATH_RECHECK,
        _JOB_PROOF,
        _STDIO_PROOF,
        _CHILD_CREATE,
    }
)


class RunnerPrelaunchTrace:
    """Keep one bounded failure phase and the last entered phase."""

    def __init__(self) -> None:
        self._last_phase = _NOT_ENTERED
        self._failure_phase: str | None = None

    @property
    def assertion_message(self) -> str:
        if self._failure_phase is not None:
            return f"runner_prelaunch_phase=failed:{self._failure_phase}"
        return f"runner_prelaunch_phase=last:{self._last_phase}"

    def _enter(self, phase: str) -> None:
        if phase not in _PHASES:
            raise AssertionError("runner prelaunch trace phase is not closed")
        self._last_phase = phase

    def _fail(self, phase: str) -> None:
        if self._failure_phase is None:
            self._failure_phase = phase

    def _wrap(
        self,
        original: Callable[..., Any],
        phase: str,
    ) -> Callable[..., Any]:
        @wraps(original)
        def traced(*args: Any, **kwargs: Any) -> Any:
            self._enter(phase)
            try:
                result = original(*args, **kwargs)
            except BaseException:
                self._fail(phase)
                raise
            return result

        return traced


@contextmanager
def trace_runner_prelaunch(
    process_module: Any,
    win32_module: Any,
) -> Iterator[RunnerPrelaunchTrace]:
    """Trace closed prelaunch phases without changing production output."""

    trace = RunnerPrelaunchTrace()
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                process_module,
                "_ensure_same_observation",
                trace._wrap(
                    process_module._ensure_same_observation,
                    _PATH_RECHECK,
                ),
            )
        )
        stack.enter_context(
            patch.object(
                win32_module.NativeJob,
                "prove_configuration",
                trace._wrap(
                    win32_module.NativeJob.prove_configuration,
                    _JOB_PROOF,
                ),
            )
        )
        stack.enter_context(
            patch.object(
                win32_module.StdioPipes,
                "prove_before_create",
                trace._wrap(
                    win32_module.StdioPipes.prove_before_create,
                    _STDIO_PROOF,
                ),
            )
        )
        stack.enter_context(
            patch.object(
                win32_module,
                "create_suspended_child",
                trace._wrap(
                    win32_module.create_suspended_child,
                    _CHILD_CREATE,
                ),
            )
        )
        yield trace
