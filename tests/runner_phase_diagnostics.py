"""Test-only phase tracing for native Runner prelaunch failures.

The trace retains only closed labels. It never retains paths, stat values,
exception text, handles, environment values, or raw observation tuples.
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
    {_NOT_ENTERED, _PATH_RECHECK, _JOB_PROOF, _STDIO_PROOF, _CHILD_CREATE}
)

_CHECKPOINTS = ("before_resources", "before_child_create", "after_child_create")
_REQUEST_SUBJECTS = (
    "executable",
    "materialized_root",
    "scratch_root",
    "attempt_root",
    "system_root",
    "scratch_tmp",
    "scratch_home",
    "scratch_local",
    "scratch_roaming",
)
_STEP_SUBJECTS = ("step_cwd", "step_entrypoint")
_FIELD_DIFFERENCES = {
    1: "device_id",
    2: "file_id",
    3: "mode",
    4: "file_attributes",
}


def _closed_path_change(expected: Any, observed: Any) -> tuple[str, str] | None:
    """Reduce transient observation tuples to two closed, non-sensitive labels."""

    before = getattr(expected, "chain", None)
    after = getattr(observed, "chain", None)
    if type(before) is not tuple or type(after) is not tuple:
        return ("unavailable", "structural_invariant")
    if len(before) != len(after):
        return ("multiple", "structural_invariant")

    changes: list[tuple[int, int]] = []
    for component, (old_entry, new_entry) in enumerate(zip(before, after, strict=True)):
        if (
            type(old_entry) is not tuple
            or type(new_entry) is not tuple
            or len(old_entry) != 5
            or len(new_entry) != 5
        ):
            return ("unavailable", "structural_invariant")
        changes.extend(
            (component, field)
            for field, (old_value, new_value) in enumerate(
                zip(old_entry, new_entry, strict=True)
            )
            if old_value != new_value
        )
    if not changes:
        return None

    components = {component for component, _field in changes}
    fields = {field for _component, field in changes}
    if len(components) > 1:
        component_label = "multiple"
    elif next(iter(components)) == len(before) - 1:
        component_label = "leaf"
    else:
        component_label = "ancestor"
    if 0 in fields:
        difference = "structural_invariant"
    elif len(fields) > 1:
        difference = "multiple_fields"
    else:
        difference = _FIELD_DIFFERENCES.get(
            next(iter(fields)), "structural_invariant"
        )
    return (component_label, difference)


class RunnerPrelaunchTrace:
    """Keep one bounded failure phase and optional closed path detail."""

    def __init__(self) -> None:
        self._last_phase = _NOT_ENTERED
        self._failure_phase: str | None = None
        self._path_detail: tuple[str, str, str, str] | None = None
        self._subjects: dict[int, str] = {}
        self._visits: dict[int, int] = {}

    @property
    def assertion_message(self) -> str:
        if self._failure_phase is None:
            return f"runner_prelaunch_phase=last:{self._last_phase}"
        message = f"runner_prelaunch_phase=failed:{self._failure_phase}"
        if self._failure_phase == _PATH_RECHECK and self._path_detail is not None:
            checkpoint, subject, component, difference = self._path_detail
            message += (
                f";checkpoint={checkpoint};subject={subject};"
                f"component={component};difference={difference}"
            )
        return message

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
                return original(*args, **kwargs)
            except BaseException:
                self._fail(phase)
                raise

        return traced

    def _wrap_admission(self, original: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(original)
        def traced(*args: Any, **kwargs: Any) -> Any:
            admitted = original(*args, **kwargs)
            request_observations = getattr(admitted, "observations", ())
            if len(request_observations) == len(_REQUEST_SUBJECTS):
                self._subjects.update(
                    (id(observation), subject)
                    for observation, subject in zip(
                        request_observations, _REQUEST_SUBJECTS, strict=True
                    )
                )
            for step in getattr(admitted, "steps", ()):
                observations = getattr(step, "observations", ())
                if len(observations) in {1, 2}:
                    self._subjects.update(
                        (id(observation), subject)
                        for observation, subject in zip(observations, _STEP_SUBJECTS)
                    )
            return admitted

        return traced

    def _wrap_path_recheck(
        self, process_module: Any, original: Callable[..., Any]
    ) -> Callable[..., Any]:
        @wraps(original)
        def traced(observation: Any) -> Any:
            self._enter(_PATH_RECHECK)
            subject = self._subjects.get(id(observation))
            observe = getattr(process_module, "_observe_physical_path", None)
            if subject is None or not callable(observe):
                return self._call_path_recheck(original, observation)

            observation_id = id(observation)
            visits = self._visits.get(observation_id, 0)
            self._visits[observation_id] = visits + 1
            checkpoint = _CHECKPOINTS[visits % len(_CHECKPOINTS)]

            @wraps(observe)
            def classify(*args: Any, **kwargs: Any) -> Any:
                try:
                    current = observe(*args, **kwargs)
                except BaseException:
                    self._path_detail = (
                        checkpoint,
                        subject,
                        "unavailable",
                        "reobserve_rejected",
                    )
                    raise
                change = _closed_path_change(observation, current)
                if change is not None:
                    self._path_detail = (checkpoint, subject, *change)
                return current

            try:
                with patch.object(process_module, "_observe_physical_path", classify):
                    return original(observation)
            except BaseException:
                self._fail(_PATH_RECHECK)
                raise

        return traced

    def _call_path_recheck(
        self, original: Callable[..., Any], observation: Any
    ) -> Any:
        try:
            return original(observation)
        except BaseException:
            self._fail(_PATH_RECHECK)
            raise


@contextmanager
def trace_runner_prelaunch(
    process_module: Any,
    win32_module: Any,
) -> Iterator[RunnerPrelaunchTrace]:
    """Trace closed prelaunch phases without changing production output."""

    trace = RunnerPrelaunchTrace()
    with ExitStack() as stack:
        if hasattr(process_module, "_admit_request"):
            stack.enter_context(
                patch.object(
                    process_module,
                    "_admit_request",
                    trace._wrap_admission(process_module._admit_request),
                )
            )
        stack.enter_context(
            patch.object(
                process_module,
                "_ensure_same_observation",
                trace._wrap_path_recheck(
                    process_module, process_module._ensure_same_observation
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
