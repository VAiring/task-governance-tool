from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests.m23_test_support import write_evidence_tree  # noqa: E402
from task_governance_tool import codex_analysis_adapter as adapter  # noqa: E402
from task_governance_tool import analysis_packet as packet_module  # noqa: E402
from task_governance_tool import _analysis_windows_process as process_boundary  # noqa: E402
from task_governance_tool._analysis_windows_process import (  # noqa: E402
    MockBinding,
    MockScenario,
    ProcessQuarantineRequired,
    ProcessSafetyError,
)
from task_governance_tool.analysis_contracts import (  # noqa: E402
    build_descriptor,
    canonical_json_bytes,
    default_recipe,
    parse_canonical_json_document,
    sealed_domain_digest,
)
from task_governance_tool.analysis_packet import (  # noqa: E402
    AnalysisPacket,
    AnalysisPacketError,
    build_analysis_packet,
    revalidate_analysis_packet,
)
from task_governance_tool.analysis_validator import (  # noqa: E402
    AnalysisValidationError,
    ValidatedAdapterOutput,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)


@dataclass(frozen=True)
class _PacketSpoof:
    value: dict[str, object]
    packet_bytes: bytes
    packet_digest: str


def _optional_job(root: Path, *, bundle_state: str = "legacy_unknown"):
    index = read_evidence_index(write_evidence_tree(root / "source"))
    entry = next(
        item for item in index.entries if item["bundle_state"] == bundle_state
    )
    source = validate_evidence_source(index, entry)
    descriptor = build_descriptor(
        source_kind=source.source_kind,
        source_basis=source.source_basis,
        recipe=default_recipe(
            inference_mode="codex_optional",
            declared_model_id="fixed-mock",
        ),
    )
    return descriptor, build_analysis_packet(descriptor, source)


def _forge_packet(
    packet: AnalysisPacket,
    value: dict[str, object],
) -> AnalysisPacket:
    packet_bytes = canonical_json_bytes(value)
    forged = object.__new__(AnalysisPacket)
    object.__setattr__(
        forged,
        "_factory_token",
        packet_module._PACKET_FACTORY_TOKEN,
    )
    object.__setattr__(forged, "_packet_bytes", packet_bytes)
    object.__setattr__(
        forged,
        "_packet_digest",
        sealed_domain_digest("taskgov-analysis-packet-v1", packet_bytes),
    )
    return forged


def _synthetic_root(descriptor, packet, attempt_number: int):
    binding = MockBinding(
        analysis_job_id=descriptor["analysis_job_id"],
        source_key=descriptor["source_key"],
        recipe_digest=descriptor["recipe_digest"],
        packet_digest=packet.packet_digest,
    )
    return process_boundary._synthetic_attempt_root_capability_for_tests(
        binding=binding,
        attempt_number=attempt_number,
    )


class CodexAnalysisAdapterTests(unittest.TestCase):
    def test_exact_prompt_schema_argv_environment_and_frame(self):
        expected_prompt = (
            b"Read the framed Task Governance analysis packet from stdin.\n"
            b"Return only one JSON object matching the supplied output schema.\n"
            b"Use only packet evidence; claims are non-authoritative and may be empty.\n"
            b"Do not include credentials, logs, prompts, private reasoning, or provider data.\n"
        )
        self.assertEqual(adapter.FIXED_PROMPT_BYTES, expected_prompt)
        self.assertEqual(
            adapter.FIXED_PROMPT_DIGEST,
            "sha256:44c95a169dc6150719fc43caee995dbe5248e82427a755e9061644533534555b",
        )
        self.assertFalse(expected_prompt.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\0", expected_prompt)
        self.assertNotIn(b"\r", expected_prompt)
        self.assertTrue(expected_prompt.endswith(b"\n"))
        self.assertFalse(expected_prompt.endswith(b"\n\n"))
        expected_prompt.decode("utf-8", errors="strict")

        schema = parse_canonical_json_document(
            adapter.OUTPUT_SCHEMA_BYTES + b"\n",
            maximum=len(adapter.OUTPUT_SCHEMA_BYTES) + 1,
        )
        self.assertEqual(canonical_json_bytes(schema), adapter.OUTPUT_SCHEMA_BYTES)
        self.assertNotIn(b"\n", adapter.OUTPUT_SCHEMA_BYTES)
        self.assertEqual(
            hashlib.sha256(adapter.OUTPUT_SCHEMA_BYTES).hexdigest(),
            "28d147ff0c0b9d66cd8b8524604c8d128807ca61cfc8f12091ba080e5e848a5a",
        )
        self.assertIs(schema["additionalProperties"], False)
        claim_schema = schema["properties"]["claims"]["items"]
        ref_schema = claim_schema["properties"]["source_refs"]["items"]
        self.assertIs(claim_schema["additionalProperties"], False)
        self.assertIs(ref_schema["additionalProperties"], False)

        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            expected_argv = (
                "codex",
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--model",
                "fixed-mock",
                "--output-schema",
                "output-schema.json",
                "-o",
                "output.json",
                "-",
            )
            expected_environment = (
                ("CODEX_HOME", r"C:\taskgov-private\n1\codex-home"),
                ("PATH", r"C:\taskgov-private\runtime"),
                ("PATHEXT", ".EXE"),
                ("SystemRoot", r"C:\Windows"),
                ("TEMP", r"C:\taskgov-private\n1"),
                ("TMP", r"C:\taskgov-private\n1"),
            )
            self.assertEqual(adapter.logical_argv(descriptor), expected_argv)
            self.assertEqual(adapter.closed_mock_environment(1), expected_environment)

            observed: dict[str, object] = {}
            original = adapter._prepare_process_mock_attempt

            def capture(*args, **kwargs):
                observed.update(kwargs)
                return original(*args, **kwargs)

            with patch.object(
                adapter,
                "_prepare_process_mock_attempt",
                side_effect=capture,
            ):
                preparation = adapter.prepare_closed_mock_attempt(
                    descriptor,
                    packet,
                    1,
                    MockScenario.SUCCESS,
                    root_capability=_synthetic_root(descriptor, packet, 1),
                )
            expected_frame = (
                b"taskgov-analysis-stdin-v1\n"
                + b"prompt-length:"
                + str(len(expected_prompt)).encode("ascii")
                + b"\npacket-length:"
                + str(len(packet.packet_bytes)).encode("ascii")
                + b"\n\n"
                + expected_prompt
                + packet.packet_bytes
                + b"\n"
            )
            self.assertEqual(observed["stdin_bytes"], expected_frame)
            self.assertEqual(observed["argv"], expected_argv)
            self.assertEqual(observed["environment"], expected_environment)
            self.assertIsNone(observed["prior_discard"])

            prepared = preparation.prepared
            self.assertIsNotNone(prepared)
            process_prepared = prepared._process_prepared
            machine = process_prepared._machine
            adapter.mark_prepared_mock_attempt_recorded(prepared)
            result = adapter.execute_prepared_mock_attempt(prepared)
            self.assertIsNone(prepared._packet)
            self.assertIsNone(prepared._descriptor)
            self.assertIsNone(prepared._process_prepared)
            self.assertIsNone(process_prepared._machine)
            self.assertIsNone(process_prepared._scenario)
            self.assertIsNone(machine.attempt)
            self.assertIs(revalidate_analysis_packet(packet, descriptor), packet)
            adapter.discard_attempt_tree(result.tree_proof)

    def test_default_preflight_is_blocked_and_creates_no_frame_or_process(self):
        with patch.object(
            adapter,
            "build_analysis_stdin_frame",
            side_effect=AssertionError("frame must not be built"),
        ) as frame, patch.object(
            adapter,
            "_prepare_process_mock_attempt",
            side_effect=AssertionError("process must not be prepared"),
        ) as process:
            result = adapter.preflight_optional()
        self.assertFalse(result.ready)
        self.assertEqual(result.inference_state, "policy_blocked")
        self.assertEqual(result.adapter_attempt_count, 0)
        self.assertEqual(result.prompt_digest, adapter.FIXED_PROMPT_DIGEST)
        frame.assert_not_called()
        process.assert_not_called()

    def test_closed_plan_accepts_only_one_or_two_enum_scenarios(self):
        plan = adapter.ClosedMockPlan(
            (MockScenario.TIMEOUT, MockScenario.SUCCESS)
        )
        self.assertEqual(plan.scenario_for_attempt(1), MockScenario.TIMEOUT)
        self.assertEqual(plan.scenario_for_attempt(2), MockScenario.SUCCESS)
        for invalid in (
            (),
            (MockScenario.SUCCESS,) * 3,
            [MockScenario.SUCCESS],
            ("success",),
            (lambda: None,),
        ):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(
                adapter.AnalysisAdapterError
            ):
                adapter.ClosedMockPlan(invalid)

    def test_marker_order_single_use_and_success_returns_only_validated_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            preparation = adapter.prepare_closed_mock_attempt(
                descriptor,
                packet,
                1,
                MockScenario.SUCCESS,
                root_capability=_synthetic_root(descriptor, packet, 1),
            )
            self.assertTrue(preparation.ready)
            self.assertEqual(preparation.inference_state, "running")
            prepared = preparation.prepared
            self.assertIsNotNone(prepared)
            self.assertEqual(
                prepared.trace,
                (
                    "n1:fresh_objects",
                    "n1:ownership_proved",
                    "n1:schema_flushed",
                ),
            )
            representation = repr(prepared)
            for forbidden in (
                "fixed-input-frame",
                "stdin_bytes",
                "environment",
                "output.json",
            ):
                self.assertNotIn(forbidden, representation)

            with self.assertRaises(adapter.AnalysisAdapterError) as early:
                adapter.execute_prepared_mock_attempt(prepared)
            self.assertEqual(early.exception.code, "analysis_adapter_execute_invalid")
            self.assertNotIn("n1:broker_launch_proved", prepared.trace)

            adapter.mark_prepared_mock_attempt_recorded(prepared)
            with self.assertRaises(adapter.AnalysisAdapterError) as duplicate_marker:
                adapter.mark_prepared_mock_attempt_recorded(prepared)
            self.assertEqual(
                duplicate_marker.exception.code,
                "analysis_adapter_record_marker_invalid",
            )
            result = adapter.execute_prepared_mock_attempt(prepared)
            self.assertLess(
                prepared.trace.index("n1:schema_flushed"),
                prepared.trace.index("n1:attempt_recorded"),
            )
            self.assertLess(
                prepared.trace.index("n1:attempt_recorded"),
                prepared.trace.index("n1:broker_launch_proved"),
            )
            with self.assertRaises(adapter.AnalysisAdapterError) as duplicate_execute:
                adapter.execute_prepared_mock_attempt(prepared)
            self.assertEqual(
                duplicate_execute.exception.code,
                "analysis_adapter_execute_invalid",
            )

            self.assertEqual(result.inference_state, "succeeded")
            self.assertIsInstance(result.adapter_output, ValidatedAdapterOutput)
            self.assertEqual(result.adapter_output.value["claims"], [])
            self.assertEqual(result.prompt_digest, adapter.FIXED_PROMPT_DIGEST)
            self.assertEqual(result.tree_proof.binding, prepared.binding)
            self.assertNotIn("claims", repr(result))
            adapter.discard_attempt_tree(result.tree_proof)

    def test_split_input_bind_and_prepared_abort_release_all_raw_refs(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            input_result = adapter.prepare_closed_mock_input(
                descriptor,
                packet,
                1,
                MockScenario.SUCCESS,
            )
            self.assertTrue(input_result.ready)
            prepared_input = input_result.prepared_input
            self.assertIsNotNone(prepared_input)
            self.assertIs(prepared_input._packet, packet)
            self.assertIsInstance(prepared_input._stdin_bytes, bytes)

            wrong = process_boundary._synthetic_attempt_root_capability_for_tests(
                binding=MockBinding(
                    analysis_job_id="tg_analysis_job_fedcba9876543210",
                    source_key=descriptor["source_key"],
                    recipe_digest=descriptor["recipe_digest"],
                    packet_digest=packet.packet_digest,
                ),
                attempt_number=1,
            )
            with self.assertRaises(adapter.AnalysisAdapterError):
                adapter.bind_closed_mock_attempt(prepared_input, wrong)
            self.assertEqual(prepared_input._state, "prepared")

            prepared = adapter.bind_closed_mock_attempt(
                prepared_input,
                _synthetic_root(descriptor, packet, 1),
            )
            self.assertEqual(prepared_input._state, "bound")
            self.assertIsNone(prepared_input._packet)
            self.assertIsNone(prepared_input._stdin_bytes)
            with self.assertRaises(adapter.AnalysisAdapterError):
                adapter.bind_closed_mock_attempt(
                    prepared_input,
                    _synthetic_root(descriptor, packet, 1),
                )

            proof = adapter.abort_prepared_mock_attempt(prepared)
            self.assertIsNone(prepared._packet)
            self.assertIsNone(prepared._descriptor)
            self.assertIsNone(prepared._process_prepared)
            self.assertNotIn("n1:attempt_recorded", proof.trace)
            self.assertNotIn("n1:broker_launch_proved", proof.trace)
            process_boundary.discard_aborted_attempt_root(proof)
            with self.assertRaises(adapter.AnalysisAdapterError):
                adapter.abort_prepared_mock_attempt(prepared)

    def test_abort_cleanup_quarantine_permanently_consumes_adapter_facade(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            prepared = adapter.prepare_closed_mock_attempt(
                descriptor,
                packet,
                1,
                MockScenario.SUCCESS,
                root_capability=_synthetic_root(descriptor, packet, 1),
            ).prepared
            process_prepared = prepared._process_prepared
            failure = ProcessQuarantineRequired((), frozenset({"n1:root"}))
            with (
                patch.object(
                    adapter,
                    "_abort_process_mock_attempt",
                    side_effect=failure,
                ),
                self.assertRaises(ProcessQuarantineRequired),
            ):
                adapter.abort_prepared_mock_attempt(prepared)

            self.assertEqual(prepared._state, "quarantine")
            self.assertIs(prepared._process_prepared, process_prepared)
            for operation in (
                lambda: adapter.mark_prepared_mock_attempt_recorded(prepared),
                lambda: adapter.abort_prepared_mock_attempt(prepared),
                lambda: adapter.execute_prepared_mock_attempt(prepared),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaises(adapter.AnalysisAdapterError):
                        operation()

    def test_wrong_binding_maps_to_invalid_output_without_exposing_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            preparation = adapter.prepare_closed_mock_attempt(
                descriptor,
                packet,
                1,
                MockScenario.WRONG_BINDING,
                root_capability=_synthetic_root(descriptor, packet, 1),
            )
            prepared = preparation.prepared
            self.assertIsNotNone(prepared)
            process_prepared = prepared._process_prepared
            machine = process_prepared._machine
            adapter.mark_prepared_mock_attempt_recorded(prepared)
            result = adapter.execute_prepared_mock_attempt(prepared)
            self.assertEqual(result.inference_state, "invalid_output")
            self.assertIsNone(result.adapter_output)
            self.assertNotIn("claims", repr(result))
            self.assertNotIn("document=", repr(result))
            self.assertIsNone(prepared._packet)
            self.assertIsNone(prepared._descriptor)
            self.assertIsNone(prepared._process_prepared)
            self.assertIsNone(process_prepared._machine)
            self.assertIsNone(machine.attempt)
            adapter.discard_attempt_tree(result.tree_proof)

    def test_input_cap_returns_prompt_digest_without_process_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            with patch.object(
                adapter,
                "build_analysis_stdin_frame",
                side_effect=AnalysisPacketError(
                    "input_too_large",
                    "analysis input exceeds the supported size",
                ),
            ) as frame, patch.object(
                adapter,
                "_prepare_process_mock_attempt",
                side_effect=AssertionError("process must not be prepared"),
            ) as process:
                result = adapter.prepare_closed_mock_attempt(
                    descriptor,
                    packet,
                    1,
                    MockScenario.SUCCESS,
                    root_capability=_synthetic_root(descriptor, packet, 1),
                )
            self.assertFalse(result.ready)
            self.assertEqual(result.inference_state, "input_too_large")
            self.assertEqual(result.prompt_digest, adapter.FIXED_PROMPT_DIGEST)
            self.assertIsNone(result.prepared)
            self.assertEqual(frame.call_args.kwargs["prompt_bytes"], adapter.FIXED_PROMPT_BYTES)
            self.assertIs(frame.call_args.kwargs["packet"], packet)
            process.assert_not_called()

    def test_retry_uses_discarded_n1_proof_and_fresh_n2_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            plan = adapter.ClosedMockPlan(
                (MockScenario.TIMEOUT, MockScenario.SUCCESS)
            )
            first_preparation = adapter.prepare_closed_mock_attempt(
                descriptor,
                packet,
                1,
                plan.scenario_for_attempt(1),
                root_capability=_synthetic_root(descriptor, packet, 1),
            )
            first = first_preparation.prepared
            self.assertIsNotNone(first)
            adapter.mark_prepared_mock_attempt_recorded(first)
            first_result = adapter.execute_prepared_mock_attempt(first)
            self.assertEqual(first_result.inference_state, "timeout")
            discarded = adapter.discard_attempt_tree(first_result.tree_proof)

            second_preparation = adapter.prepare_closed_mock_attempt(
                descriptor,
                packet,
                2,
                plan.scenario_for_attempt(2),
                discarded,
                root_capability=_synthetic_root(descriptor, packet, 2),
            )
            second = second_preparation.prepared
            self.assertIsNotNone(second)
            self.assertEqual(second.trace[0], "n2:fresh_objects")
            self.assertNotEqual(
                adapter.closed_mock_environment(1),
                adapter.closed_mock_environment(2),
            )
            with self.assertRaises(ProcessSafetyError):
                adapter.prepare_closed_mock_attempt(
                    descriptor,
                    packet,
                    2,
                    MockScenario.SUCCESS,
                    discarded,
                    root_capability=_synthetic_root(descriptor, packet, 2),
                )
            adapter.mark_prepared_mock_attempt_recorded(second)
            second_result = adapter.execute_prepared_mock_attempt(second)
            self.assertEqual(second_result.inference_state, "succeeded")
            adapter.discard_attempt_tree(second_result.tree_proof)

    def test_validator_failures_map_without_returning_raw_sealed_result(self):
        for code, expected in (
            ("invalid_output", "invalid_output"),
            ("output_too_large", "output_too_large"),
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                descriptor, packet = _optional_job(Path(temporary))
                preparation = adapter.prepare_closed_mock_attempt(
                    descriptor,
                    packet,
                    1,
                    MockScenario.SUCCESS,
                    root_capability=_synthetic_root(descriptor, packet, 1),
                )
                prepared = preparation.prepared
                self.assertIsNotNone(prepared)
                adapter.mark_prepared_mock_attempt_recorded(prepared)
                with patch.object(
                    adapter,
                    "validate_adapter_output",
                    side_effect=AnalysisValidationError(code, "rejected"),
                ):
                    result = adapter.execute_prepared_mock_attempt(prepared)
                self.assertEqual(result.inference_state, expected)
                self.assertIsNone(result.adapter_output)
                self.assertFalse(hasattr(result, "sealed_result"))
                adapter.discard_attempt_tree(result.tree_proof)

    def test_quarantine_retains_the_fail_fast_ownership_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            preparation = adapter.prepare_closed_mock_attempt(
                descriptor,
                packet,
                1,
                MockScenario.TREE_ZERO_UNPROVED,
                root_capability=_synthetic_root(descriptor, packet, 1),
            )
            prepared = preparation.prepared
            self.assertIsNotNone(prepared)
            process_prepared = prepared._process_prepared
            adapter.mark_prepared_mock_attempt_recorded(prepared)
            with self.assertRaises(ProcessQuarantineRequired):
                adapter.execute_prepared_mock_attempt(prepared)
            self.assertIs(prepared._packet, packet)
            self.assertIsNotNone(prepared._descriptor)
            self.assertIs(prepared._process_prepared, process_prepared)
            self.assertIsNotNone(process_prepared._machine.attempt)

    def test_packet_binding_rejection_is_pre_process_no_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, packet = _optional_job(Path(temporary))
            forged = _PacketSpoof(
                value=packet.value,
                packet_bytes=packet.packet_bytes,
                packet_digest=packet.packet_digest,
            )
            with patch.object(adapter, "_prepare_process_mock_attempt") as process:
                with self.assertRaises(adapter.AnalysisAdapterError):
                    adapter.prepare_closed_mock_attempt(
                        descriptor,
                        forged,
                        1,
                        MockScenario.SUCCESS,
                        root_capability=_synthetic_root(descriptor, packet, 1),
                    )
            process.assert_not_called()

    def test_invalid_legacy_and_native_source_shapes_are_prelaunch_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_descriptor, legacy_packet = _optional_job(root / "legacy")
            legacy_value = legacy_packet.value
            legacy_value["source"] = {"unexpected": True}
            legacy_forged = _forge_packet(legacy_packet, legacy_value)

            native_descriptor, native_packet = _optional_job(
                root / "native",
                bundle_state="native",
            )
            native_value = native_packet.value
            native_value["source"]["payload"]["task"]["title"] = (
                "forged native bundle"
            )
            native_forged = _forge_packet(native_packet, native_value)

            for descriptor, forged in (
                (legacy_descriptor, legacy_forged),
                (native_descriptor, native_forged),
            ):
                with self.subTest(source_kind=descriptor["source_kind"]), patch.object(
                    adapter,
                    "_prepare_process_mock_attempt",
                ) as process:
                    with self.assertRaises(adapter.AnalysisAdapterError):
                        adapter.prepare_closed_mock_attempt(
                            descriptor,
                            forged,
                            1,
                            MockScenario.SUCCESS,
                            root_capability=_synthetic_root(
                                descriptor,
                                (
                                    legacy_packet
                                    if descriptor is legacy_descriptor
                                    else native_packet
                                ),
                                1,
                            ),
                        )
                    process.assert_not_called()

    def test_import_and_public_signature_exclude_live_or_mutating_boundaries(self):
        source_path = (
            ROOT
            / "task-governance-tool"
            / "scripts"
            / "task_governance_tool"
            / "codex_analysis_adapter.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        forbidden = (
            "sqlite3",
            "storage",
            "subprocess",
            "socket",
            "requests",
            "urllib",
            "cli",
            "skill",
        )
        self.assertFalse(
            [name for name in imported if any(item in name.lower() for item in forbidden)]
        )
        self.assertEqual(
            tuple(inspect.signature(adapter.ClosedMockPlan).parameters),
            ("scenarios",),
        )
        self.assertEqual(
            tuple(inspect.signature(adapter.prepare_closed_mock_attempt).parameters),
            (
                "descriptor",
                "packet",
                "attempt_number",
                "scenario",
                "prior_discard",
                "root_capability",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(adapter.prepare_closed_mock_input).parameters),
            (
                "descriptor",
                "packet",
                "attempt_number",
                "scenario",
                "prior_discard",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(adapter.bind_closed_mock_attempt).parameters),
            ("prepared_input", "root_capability"),
        )


if __name__ == "__main__":
    unittest.main()
