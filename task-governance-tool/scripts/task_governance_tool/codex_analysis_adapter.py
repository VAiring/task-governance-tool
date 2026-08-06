"""Closed TG-M23.2 Codex adapter facade with no live execution path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn

from task_governance_tool._analysis_windows_process import (
    AbortedAttemptTreeProof,
    AttemptRootCapability,
    AttemptTreeProof,
    DiscardedAttemptTreeProof,
    MockBinding,
    MockScenario,
    NativeProcessBoundary,
    PreparedMockAttempt as ProcessPreparedMockAttempt,
    ProcessQuarantineRequired,
    ProcessSafetyError,
    abort_prepared_mock_attempt as _abort_process_mock_attempt,
    discard_attempt_tree as _discard_process_attempt_tree,
    execute_prepared_mock_attempt as _execute_process_mock_attempt,
    mark_prepared_mock_attempt_recorded as _mark_process_attempt_recorded,
    prepare_closed_mock_attempt as _prepare_process_mock_attempt,
)
from task_governance_tool.analysis_contracts import (
    AnalysisContractError,
    canonical_json_bytes,
    validate_descriptor,
)
from task_governance_tool.analysis_packet import (
    AnalysisPacket,
    AnalysisPacketError,
    FIXED_PROMPT_BYTES,
    FIXED_PROMPT_DIGEST,
    build_analysis_stdin_frame,
    revalidate_analysis_packet,
)
from task_governance_tool.analysis_validator import (
    AnalysisValidationError,
    ValidatedAdapterOutput,
    validate_adapter_output,
)


PRIVATE_OUTPUT_SCHEMA_LEAF = "output-schema.json"
PRIVATE_OUTPUT_LEAF = "output.json"

_OUTPUT_SCHEMA = {
    "additionalProperties": False,
    "properties": {
        "output_schema_version": {"const": 1, "type": "integer"},
        "analysis_job_id": {
            "pattern": r"^tg_analysis_job_[0-9a-f]{16}$",
            "type": "string",
        },
        "source_key": {
            "pattern": r"^sha256:[0-9a-f]{64}$",
            "type": "string",
        },
        "recipe_digest": {
            "pattern": r"^sha256:[0-9a-f]{64}$",
            "type": "string",
        },
        "claims": {
            "items": {
                "additionalProperties": False,
                "properties": {
                    "text": {"minLength": 1, "type": "string"},
                    "source_refs": {
                        "items": {
                            "additionalProperties": False,
                            "properties": {
                                "kind": {
                                    "enum": ["legacy_basis", "native_pointer"]
                                },
                                "json_pointer": {
                                    "anyOf": [
                                        {"minLength": 1, "type": "string"},
                                        {"type": "null"},
                                    ]
                                },
                            },
                            "required": ["kind", "json_pointer"],
                            "type": "object",
                        },
                        "maxItems": 8,
                        "minItems": 1,
                        "type": "array",
                        "uniqueItems": True,
                    },
                    "uncertainty": {
                        "enum": [
                            "none",
                            "insufficient_basis",
                            "conflicting_basis",
                            "legacy_absence",
                        ]
                    },
                },
                "required": ["text", "source_refs", "uncertainty"],
                "type": "object",
            },
            "maxItems": 2048,
            "type": "array",
            "uniqueItems": True,
        },
    },
    "required": [
        "output_schema_version",
        "analysis_job_id",
        "source_key",
        "recipe_digest",
        "claims",
    ],
    "type": "object",
}
OUTPUT_SCHEMA_BYTES = canonical_json_bytes(_OUTPUT_SCHEMA)

_CLOSED_MOCK_ENVIRONMENTS = {
    1: (
        ("CODEX_HOME", r"C:\taskgov-private\n1\codex-home"),
        ("PATH", r"C:\taskgov-private\runtime"),
        ("PATHEXT", ".EXE"),
        ("SystemRoot", r"C:\Windows"),
        ("TEMP", r"C:\taskgov-private\n1"),
        ("TMP", r"C:\taskgov-private\n1"),
    ),
    2: (
        ("CODEX_HOME", r"C:\taskgov-private\n2\codex-home"),
        ("PATH", r"C:\taskgov-private\runtime"),
        ("PATHEXT", ".EXE"),
        ("SystemRoot", r"C:\Windows"),
        ("TEMP", r"C:\taskgov-private\n2"),
        ("TMP", r"C:\taskgov-private\n2"),
    ),
}

_PROCESS_OUTCOMES = frozenset(
    {
        "succeeded",
        "unavailable",
        "launch_failed",
        "timeout",
        "output_too_large",
        "invalid_output",
        "failed",
        "cancelled",
    }
)
_ADAPTER_FACTORY_TOKEN = object()


@dataclass(frozen=True)
class AnalysisAdapterError(ValueError):
    code: str = "analysis_adapter_input_invalid"
    message: str = "analysis adapter input is invalid"

    def __str__(self) -> str:
        return self.message


def _failure(
    code: str = "analysis_adapter_input_invalid",
    message: str = "analysis adapter input is invalid",
) -> NoReturn:
    raise AnalysisAdapterError(code, message)


@dataclass(frozen=True)
class AdapterPreflight:
    ready: bool
    inference_state: str
    adapter_attempt_count: int
    prompt_digest: str


@dataclass(frozen=True)
class AdapterPreparation:
    ready: bool
    inference_state: str
    prompt_digest: str
    prepared: PreparedAdapterAttempt | None = field(repr=False)


@dataclass(frozen=True)
class AdapterInputPreparation:
    ready: bool
    inference_state: str
    prompt_digest: str
    prepared_input: PreparedAdapterInput | None = field(repr=False)


@dataclass(frozen=True)
class AdapterAttemptResult:
    inference_state: str
    duration_ms: int
    adapter_output: ValidatedAdapterOutput | None = field(repr=False)
    tree_proof: AttemptTreeProof
    prompt_digest: str


class ClosedMockPlan:
    """One closed scenario sequence; it owns no bytes, paths, or callbacks."""

    __slots__ = ("_scenarios",)

    def __init__(self, scenarios: tuple[MockScenario, ...]) -> None:
        if (
            type(scenarios) is not tuple
            or not 1 <= len(scenarios) <= 2
            or any(type(item) is not MockScenario for item in scenarios)
        ):
            _failure("analysis_mock_plan_invalid")
        self._scenarios = scenarios

    @property
    def scenarios(self) -> tuple[MockScenario, ...]:
        return self._scenarios

    def scenario_for_attempt(self, attempt_number: int) -> MockScenario:
        if (
            type(attempt_number) is not int
            or not 1 <= attempt_number <= len(self._scenarios)
        ):
            _failure("analysis_mock_plan_invalid")
        return self._scenarios[attempt_number - 1]

    def __repr__(self) -> str:
        return f"ClosedMockPlan(scenarios={self._scenarios!r})"


class PreparedAdapterInput:
    """One-shot validated frame waiting for a session-owned physical root."""

    __slots__ = (
        "_attempt_number",
        "_argv",
        "_binding",
        "_descriptor",
        "_environment",
        "_packet",
        "_prior_discard",
        "_scenario",
        "_state",
        "_stdin_bytes",
    )

    def __init__(
        self,
        token: object,
        *,
        attempt_number: int,
        binding: MockBinding,
        descriptor: dict[str, object],
        packet: AnalysisPacket,
        stdin_bytes: bytes,
        argv: tuple[str, ...],
        environment: tuple[tuple[str, str], ...],
        scenario: MockScenario,
        prior_discard: DiscardedAttemptTreeProof | None,
    ) -> None:
        if token is not _ADAPTER_FACTORY_TOKEN:
            _failure("analysis_adapter_input_preparation_invalid")
        self._attempt_number = attempt_number
        self._argv = argv
        self._binding = binding
        self._descriptor = descriptor
        self._environment = environment
        self._packet = packet
        self._prior_discard = prior_discard
        self._scenario = scenario
        self._state = "prepared"
        self._stdin_bytes = stdin_bytes

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    def __repr__(self) -> str:
        return (
            "PreparedAdapterInput("
            f"attempt_number={self._attempt_number!r}, "
            f"binding={self._binding!r}, state={self._state!r})"
        )

    def _release(self) -> None:
        self._argv = None
        self._descriptor = None
        self._environment = None
        self._packet = None
        self._prior_discard = None
        self._scenario = None
        self._stdin_bytes = None


class PreparedAdapterAttempt:
    """Opaque facade capability retaining packet bytes only for validation."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_descriptor",
        "_final_trace",
        "_packet",
        "_process_prepared",
        "_prompt_digest",
        "_state",
    )

    def __init__(
        self,
        token: object,
        *,
        binding: MockBinding,
        descriptor: dict[str, object],
        packet: AnalysisPacket,
        process_prepared: ProcessPreparedMockAttempt,
        prompt_digest: str,
    ) -> None:
        if token is not _ADAPTER_FACTORY_TOKEN:
            _failure("analysis_adapter_preparation_invalid")
        self._attempt_number = process_prepared.attempt_number
        self._binding = binding
        self._descriptor = descriptor
        self._final_trace: tuple[str, ...] = ()
        self._packet = packet
        self._process_prepared = process_prepared
        self._prompt_digest = prompt_digest
        self._state = "prepared"

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def trace(self) -> tuple[str, ...]:
        if self._process_prepared is not None:
            return self._process_prepared.trace
        return self._final_trace

    def __repr__(self) -> str:
        return (
            "PreparedAdapterAttempt("
            f"attempt_number={self.attempt_number!r}, "
            f"binding={self._binding!r}, state={self._state!r})"
        )

    def _mark_recorded(self) -> None:
        if self._state != "prepared":
            _failure("analysis_adapter_record_marker_invalid")
        process_prepared = self._process_prepared
        if process_prepared is None:
            _failure("analysis_adapter_record_marker_invalid")
        _mark_process_attempt_recorded(process_prepared)
        self._state = "recorded"

    def _abort(self) -> AbortedAttemptTreeProof:
        if self._state != "prepared":
            _failure("analysis_adapter_abort_invalid")
        process_prepared = self._process_prepared
        if process_prepared is None:
            _failure("analysis_adapter_abort_invalid")
        self._state = "aborted"
        quarantine = False
        try:
            return _abort_process_mock_attempt(process_prepared)
        except ProcessQuarantineRequired:
            quarantine = True
            self._state = "quarantine"
            raise
        finally:
            if not quarantine:
                self._final_trace = process_prepared.trace
                self._descriptor = None
                self._packet = None
                self._process_prepared = None

    def _execute(self) -> AdapterAttemptResult:
        if self._state != "recorded":
            _failure("analysis_adapter_execute_invalid")
        process_prepared = self._process_prepared
        descriptor = self._descriptor
        packet = self._packet
        if (
            process_prepared is None
            or descriptor is None
            or packet is None
        ):
            _failure("analysis_adapter_execute_invalid")
        self._state = "executed"
        quarantine = False
        try:
            process_result = _execute_process_mock_attempt(process_prepared)
            process_outcome = process_result.outcome
            tree_proof = process_outcome.tree_proof
            if (
                process_result.attempt_number != self._attempt_number
                or tree_proof.attempt_number != self._attempt_number
                or tree_proof.binding != self._binding
                or process_outcome.inference_state not in _PROCESS_OUTCOMES
            ):
                raise ProcessSafetyError("analysis_process_unsafe")

            inference_state = process_outcome.inference_state
            adapter_output: ValidatedAdapterOutput | None = None
            sealed_result = process_outcome.sealed_result
            if sealed_result is None:
                if inference_state == "succeeded":
                    inference_state = "invalid_output"
            elif inference_state != "succeeded":
                inference_state = "invalid_output"
            else:
                try:
                    adapter_output = validate_adapter_output(
                        sealed_result.document,
                        descriptor=descriptor,
                        packet=packet,
                    )
                except AnalysisValidationError as exc:
                    inference_state = (
                        "output_too_large"
                        if exc.code == "output_too_large"
                        else "invalid_output"
                    )
                else:
                    if type(adapter_output) is not ValidatedAdapterOutput:
                        inference_state = "invalid_output"
                if inference_state != "succeeded":
                    adapter_output = None

            return AdapterAttemptResult(
                inference_state=inference_state,
                duration_ms=process_outcome.duration_ms,
                adapter_output=adapter_output,
                tree_proof=tree_proof,
                prompt_digest=self._prompt_digest,
            )
        except ProcessQuarantineRequired:
            quarantine = True
            raise
        finally:
            if not quarantine:
                self._final_trace = process_prepared.trace
                self._descriptor = None
                self._packet = None
                self._process_prepared = None


def preflight_optional() -> AdapterPreflight:
    """Perform only the current native read-only, pre-count blocked check."""

    observed = NativeProcessBoundary().preflight()
    if (
        observed.ready
        or observed.inference_state != "policy_blocked"
        or observed.adapter_attempt_count != 0
    ):
        raise ProcessSafetyError("analysis_process_unsafe")
    return AdapterPreflight(
        ready=False,
        inference_state="policy_blocked",
        adapter_attempt_count=0,
        prompt_digest=FIXED_PROMPT_DIGEST,
    )


def logical_argv(descriptor: object) -> tuple[str, ...]:
    """Return the exact shell-free logical argv for one optional recipe."""

    try:
        bound = validate_descriptor(descriptor)
    except AnalysisContractError as exc:
        raise AnalysisAdapterError() from exc
    recipe = bound["recipe"]
    if recipe["inference_mode"] != "codex_optional":
        _failure()
    model_id = recipe["declared_model_id"]
    if type(model_id) is not str or not model_id:
        _failure()
    return (
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--model",
        model_id,
        "--output-schema",
        PRIVATE_OUTPUT_SCHEMA_LEAF,
        "-o",
        PRIVATE_OUTPUT_LEAF,
        "-",
    )


def closed_mock_environment(
    attempt_number: int,
) -> tuple[tuple[str, str], ...]:
    """Return one of the two fixed, credential-free private mock environments."""

    if type(attempt_number) is not int or attempt_number not in {1, 2}:
        _failure()
    return _CLOSED_MOCK_ENVIRONMENTS[attempt_number]


def _validated_inputs(
    descriptor: object,
    packet: object,
) -> tuple[dict[str, object], AnalysisPacket]:
    try:
        bound = validate_descriptor(descriptor)
        normalized_packet = revalidate_analysis_packet(packet, bound)
        if bound["recipe"]["inference_mode"] != "codex_optional":
            raise AnalysisContractError()
    except (AnalysisContractError, AnalysisPacketError) as exc:
        raise AnalysisAdapterError() from exc
    return bound, normalized_packet


def prepare_closed_mock_input(
    descriptor: object,
    packet: object,
    attempt_number: int,
    scenario: MockScenario,
    prior_discard: DiscardedAttemptTreeProof | None = None,
) -> AdapterInputPreparation:
    """Validate and frame one input before any physical root is created."""

    if type(attempt_number) is not int or attempt_number not in {1, 2}:
        _failure()
    if type(scenario) is not MockScenario:
        _failure("analysis_mock_plan_invalid")
    bound, normalized_packet = _validated_inputs(descriptor, packet)
    try:
        frame = build_analysis_stdin_frame(
            prompt_bytes=FIXED_PROMPT_BYTES,
            packet=normalized_packet,
        )
    except AnalysisPacketError as exc:
        if exc.code == "input_too_large":
            return AdapterInputPreparation(
                ready=False,
                inference_state="input_too_large",
                prompt_digest=FIXED_PROMPT_DIGEST,
                prepared_input=None,
            )
        raise AnalysisAdapterError() from exc
    if (
        frame.prompt_digest != FIXED_PROMPT_DIGEST
        or frame.packet_digest != normalized_packet.packet_digest
    ):
        raise ProcessSafetyError("analysis_process_unsafe")
    binding = MockBinding(
        analysis_job_id=bound["analysis_job_id"],
        source_key=bound["source_key"],
        recipe_digest=bound["recipe_digest"],
        packet_digest=normalized_packet.packet_digest,
    )
    prepared_input = PreparedAdapterInput(
        _ADAPTER_FACTORY_TOKEN,
        attempt_number=attempt_number,
        binding=binding,
        descriptor=bound,
        packet=normalized_packet,
        stdin_bytes=frame.stdin_bytes,
        argv=logical_argv(bound),
        environment=closed_mock_environment(attempt_number),
        scenario=scenario,
        prior_discard=prior_discard,
    )
    return AdapterInputPreparation(
        ready=True,
        inference_state="running",
        prompt_digest=FIXED_PROMPT_DIGEST,
        prepared_input=prepared_input,
    )


def bind_closed_mock_attempt(
    prepared_input: PreparedAdapterInput,
    root_capability: AttemptRootCapability,
) -> PreparedAdapterAttempt:
    """Bind one validated frame to one exact session-owned attempt root."""

    if (
        type(prepared_input) is not PreparedAdapterInput
        or prepared_input._state != "prepared"
        or type(root_capability) is not AttemptRootCapability
        or root_capability.state != "bound"
        or root_capability.analysis_job_id
        != prepared_input.binding.analysis_job_id
        or root_capability.attempt_number != prepared_input.attempt_number
        or root_capability.packet_digest != prepared_input.binding.packet_digest
    ):
        _failure("analysis_adapter_root_binding_invalid")
    prepared_input._state = "binding"
    try:
        process_prepared = _prepare_process_mock_attempt(
            prepared_input._attempt_number,
            prepared_input._scenario,
            binding=prepared_input._binding,
            stdin_bytes=prepared_input._stdin_bytes,
            argv=prepared_input._argv,
            environment=prepared_input._environment,
            root_capability=root_capability,
            output_schema_bytes=OUTPUT_SCHEMA_BYTES,
            prior_discard=prepared_input._prior_discard,
        )
    except ProcessQuarantineRequired:
        prepared_input._state = "quarantine"
        raise
    except BaseException:
        if root_capability.state == "bound":
            prepared_input._state = "prepared"
        else:
            prepared_input._state = "quarantine"
        raise
    prepared = PreparedAdapterAttempt(
        _ADAPTER_FACTORY_TOKEN,
        binding=prepared_input._binding,
        descriptor=prepared_input._descriptor,
        packet=prepared_input._packet,
        process_prepared=process_prepared,
        prompt_digest=FIXED_PROMPT_DIGEST,
    )
    prepared_input._state = "bound"
    prepared_input._release()
    return prepared


def prepare_closed_mock_attempt(
    descriptor: object,
    packet: object,
    attempt_number: int,
    scenario: MockScenario,
    prior_discard: DiscardedAttemptTreeProof | None = None,
    *,
    root_capability: AttemptRootCapability,
) -> AdapterPreparation:
    """Compatibility wrapper; new workers prepare input before making a root."""

    input_result = prepare_closed_mock_input(
        descriptor,
        packet,
        attempt_number,
        scenario,
        prior_discard,
    )
    if not input_result.ready:
        return AdapterPreparation(
            ready=False,
            inference_state=input_result.inference_state,
            prompt_digest=input_result.prompt_digest,
            prepared=None,
        )
    return AdapterPreparation(
        ready=True,
        inference_state="running",
        prompt_digest=input_result.prompt_digest,
        prepared=bind_closed_mock_attempt(
            input_result.prepared_input,
            root_capability,
        ),
    )


def mark_prepared_mock_attempt_recorded(prepared: PreparedAdapterAttempt) -> None:
    """Apply the marker only after the caller has completed its status CAS."""

    if type(prepared) is not PreparedAdapterAttempt:
        _failure("analysis_adapter_preparation_invalid")
    prepared._mark_recorded()


def abort_prepared_mock_attempt(
    prepared: PreparedAdapterAttempt,
) -> AbortedAttemptTreeProof:
    """Abort a prepared attempt after a non-applied caller status CAS."""

    if type(prepared) is not PreparedAdapterAttempt:
        _failure("analysis_adapter_preparation_invalid")
    return prepared._abort()


def execute_prepared_mock_attempt(
    prepared: PreparedAdapterAttempt,
) -> AdapterAttemptResult:
    """Execute and validate one marked closed mock preparation exactly once."""

    if type(prepared) is not PreparedAdapterAttempt:
        _failure("analysis_adapter_preparation_invalid")
    return prepared._execute()


def discard_attempt_tree(
    proof: AttemptTreeProof,
    *,
    root_owner_token: object | None = None,
) -> DiscardedAttemptTreeProof:
    """Consume a bound tree proof for retry, cancel, or no-report cleanup."""

    return _discard_process_attempt_tree(
        proof,
        root_owner_token=root_owner_token,
    )


__all__ = (
    "AdapterAttemptResult",
    "AdapterInputPreparation",
    "AdapterPreflight",
    "AdapterPreparation",
    "AnalysisAdapterError",
    "ClosedMockPlan",
    "FIXED_PROMPT_BYTES",
    "FIXED_PROMPT_DIGEST",
    "OUTPUT_SCHEMA_BYTES",
    "PRIVATE_OUTPUT_LEAF",
    "PRIVATE_OUTPUT_SCHEMA_LEAF",
    "PreparedAdapterAttempt",
    "PreparedAdapterInput",
    "abort_prepared_mock_attempt",
    "bind_closed_mock_attempt",
    "closed_mock_environment",
    "discard_attempt_tree",
    "execute_prepared_mock_attempt",
    "logical_argv",
    "mark_prepared_mock_attempt_recorded",
    "preflight_optional",
    "prepare_closed_mock_attempt",
    "prepare_closed_mock_input",
)
