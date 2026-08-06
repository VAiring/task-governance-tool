from __future__ import annotations

from dataclasses import dataclass
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.m23_test_support import write_evidence_tree


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import analysis_packet as packet_module  # noqa: E402
from task_governance_tool.analysis_contracts import (  # noqa: E402
    build_descriptor,
    canonical_json_bytes,
    default_recipe,
    sealed_domain_digest,
)
from task_governance_tool.analysis_packet import (  # noqa: E402
    AnalysisPacket,
    AnalysisPacketError,
    build_analysis_packet,
    build_analysis_stdin_frame,
    revalidate_analysis_packet,
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


class AnalysisPacketTests(unittest.TestCase):
    def _sources(self, root: Path):
        index = read_evidence_index(write_evidence_tree(root))
        return [validate_evidence_source(index, entry) for entry in index.entries]

    def _forge_packet(
        self,
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

    def test_native_and_legacy_packets_bind_exact_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            for source in self._sources(Path(temporary)):
                with self.subTest(source_kind=source.source_kind):
                    descriptor = build_descriptor(
                        source_kind=source.source_kind,
                        source_basis=source.source_basis,
                        recipe=default_recipe(),
                    )
                    packet = build_analysis_packet(descriptor, source)
                    self.assertEqual(
                        packet.value["analysis_job_id"],
                        descriptor["analysis_job_id"],
                    )
                    self.assertEqual(packet.value["source"], source.source)
                    self.assertTrue(packet.packet_digest.startswith("sha256:"))
                    self.assertIs(
                        revalidate_analysis_packet(packet, descriptor),
                        packet,
                    )

                    leaked = packet.value
                    leaked["analysis_job_id"] = "tg_analysis_job_0000000000000000"
                    self.assertEqual(
                        packet.value["analysis_job_id"],
                        descriptor["analysis_job_id"],
                    )

    def test_exact_stdin_framing_and_invalid_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self._sources(Path(temporary))[0]
            descriptor = build_descriptor(
                source_kind=source.source_kind,
                source_basis=source.source_basis,
                recipe=default_recipe(),
            )
            packet = build_analysis_packet(descriptor, source)
            prompt = b"Analyze only cited source values.\n"
            framed = build_analysis_stdin_frame(
                prompt_bytes=prompt,
                packet=packet,
            )
            expected_prefix = (
                b"taskgov-analysis-stdin-v1\n"
                + f"prompt-length:{len(prompt)}\n".encode("ascii")
                + f"packet-length:{len(packet.packet_bytes)}\n\n".encode("ascii")
            )
            self.assertEqual(
                framed.stdin_bytes,
                expected_prefix + prompt + packet.packet_bytes + b"\n",
            )
            self.assertEqual(framed.packet_digest, packet.packet_digest)
            for invalid in (b"", b"bom\r\n", b"nul\0\n", b"double\n\n"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(AnalysisPacketError):
                        build_analysis_stdin_frame(
                            prompt_bytes=invalid,
                            packet=packet,
                        )

            with patch(
                "task_governance_tool.analysis_packet.ANALYSIS_STDIN_MAX_BYTES",
                10,
            ):
                with self.assertRaises(AnalysisPacketError) as raised:
                    build_analysis_stdin_frame(prompt_bytes=prompt, packet=packet)
            self.assertEqual(raised.exception.code, "input_too_large")

    def test_packet_cap_fails_without_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self._sources(Path(temporary))[0]
            descriptor = build_descriptor(
                source_kind=source.source_kind,
                source_basis=source.source_basis,
                recipe=default_recipe(),
            )
            with patch(
                "task_governance_tool.analysis_packet.LEGACY_PACKET_MAX_BYTES",
                1,
            ):
                with self.assertRaises(AnalysisPacketError) as raised:
                    build_analysis_packet(descriptor, source)
            self.assertEqual(raised.exception.code, "packet_too_large")

    def test_validated_source_owns_canonical_bytes_and_returns_fresh_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = next(
                item
                for item in self._sources(Path(temporary))
                if item.source_kind == "native_bundle"
            )
            original_basis = source.source_basis
            original_source = source.source
            descriptor = build_descriptor(
                source_kind=source.source_kind,
                source_basis=source.source_basis,
                recipe=default_recipe(),
            )
            packet = build_analysis_packet(descriptor, source)

            leaked_basis = source.source_basis
            leaked_envelope = source.source
            leaked_basis["projection_generation"] += 99
            leaked_basis["entry"]["task_id"] = "tg_task_0000000000000000"
            leaked_envelope["payload"]["task"]["title"] = "mutated after validation"

            self.assertEqual(source.source_basis, original_basis)
            self.assertEqual(source.source, original_source)
            rebuilt_descriptor = build_descriptor(
                source_kind=source.source_kind,
                source_basis=source.source_basis,
                recipe=default_recipe(),
            )
            rebuilt_packet = build_analysis_packet(rebuilt_descriptor, source)
            self.assertEqual(rebuilt_descriptor, descriptor)
            self.assertEqual(rebuilt_packet.packet_bytes, packet.packet_bytes)
            self.assertEqual(rebuilt_packet.packet_digest, packet.packet_digest)

    def test_factory_seal_and_source_shape_revalidation_reject_spoofs(self):
        with tempfile.TemporaryDirectory() as temporary:
            sources = self._sources(Path(temporary))
            for source in sources:
                with self.subTest(source_kind=source.source_kind):
                    descriptor = build_descriptor(
                        source_kind=source.source_kind,
                        source_basis=source.source_basis,
                        recipe=default_recipe(),
                    )
                    packet = build_analysis_packet(descriptor, source)
                    spoof = _PacketSpoof(
                        value=packet.value,
                        packet_bytes=packet.packet_bytes,
                        packet_digest=packet.packet_digest,
                    )
                    with self.assertRaises(AnalysisPacketError):
                        revalidate_analysis_packet(spoof, descriptor)
                    with self.assertRaises(AnalysisPacketError):
                        AnalysisPacket(
                            object(),
                            packet_bytes=packet.packet_bytes,
                            packet_digest=packet.packet_digest,
                        )

                    changed = packet.value
                    if source.source_kind == "legacy_index_entry":
                        changed["source"] = {"unexpected": True}
                    else:
                        changed["source"]["payload"]["task"]["title"] = (
                            "forged native bundle"
                        )
                    forged = self._forge_packet(packet, changed)
                    with self.assertRaises(AnalysisPacketError):
                        revalidate_analysis_packet(forged, descriptor)


if __name__ == "__main__":
    unittest.main()
