"""Ephemeral analysis packet and exact stdin framing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from task_governance_tool.analysis_contracts import (
    ANALYSIS_STDIN_MAX_BYTES,
    LEGACY_PACKET_MAX_BYTES,
    NATIVE_PACKET_MAX_BYTES,
    PACKET_VERSION,
    AnalysisContractError,
    canonical_json_bytes,
    parse_canonical_json_document,
    sealed_domain_digest,
    validate_descriptor,
    validate_source_basis,
)
from task_governance_tool.evidence_consumer import (
    EvidenceConsumerError,
    ValidatedEvidenceSource,
    revalidate_validated_source,
)


FIXED_PROMPT_BYTES = (
    b"Read the framed Task Governance analysis packet from stdin.\n"
    b"Return only one JSON object matching the supplied output schema.\n"
    b"Use only packet evidence; claims are non-authoritative and may be empty.\n"
    b"Do not include credentials, logs, prompts, private reasoning, or provider data.\n"
)
FIXED_PROMPT_DIGEST = sealed_domain_digest(
    "taskgov-analysis-prompt-v1",
    FIXED_PROMPT_BYTES,
)


@dataclass(frozen=True)
class AnalysisPacketError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class AnalysisPacket:
    """Factory-sealed canonical packet with fresh-copy public projections."""

    __slots__ = ("_factory_token", "_packet_bytes", "_packet_digest")

    def __init__(
        self,
        token: object,
        *,
        packet_bytes: bytes,
        packet_digest: str,
    ) -> None:
        if (
            token is not _PACKET_FACTORY_TOKEN
            or type(packet_bytes) is not bytes
            or type(packet_digest) is not str
        ):
            raise AnalysisPacketError("source_invalid", "analysis source is invalid")
        object.__setattr__(self, "_factory_token", token)
        object.__setattr__(self, "_packet_bytes", packet_bytes)
        object.__setattr__(self, "_packet_digest", packet_digest)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("AnalysisPacket is immutable")

    @property
    def value(self) -> dict[str, object]:
        return _fresh_packet_value(self._packet_bytes)

    @property
    def packet_bytes(self) -> bytes:
        return self._packet_bytes

    @property
    def packet_digest(self) -> str:
        return self._packet_digest

    def __repr__(self) -> str:
        return f"AnalysisPacket(packet_digest={self._packet_digest!r})"


@dataclass(frozen=True)
class AnalysisInputFrame:
    stdin_bytes: bytes = field(repr=False)
    prompt_digest: str
    packet_digest: str


_PACKET_FACTORY_TOKEN = object()
_PACKET_KEYS = {
    "packet_version",
    "analysis_job_id",
    "source_kind",
    "source_basis",
    "source",
}


def _fresh_packet_value(packet_bytes: bytes) -> dict[str, object]:
    try:
        value = parse_canonical_json_document(
            packet_bytes + b"\n",
            maximum=max(NATIVE_PACKET_MAX_BYTES, LEGACY_PACKET_MAX_BYTES) + 1,
        )
    except AnalysisContractError as exc:
        raise AnalysisPacketError("source_invalid", "analysis source is invalid") from exc
    if type(value) is not dict or set(value) != _PACKET_KEYS:
        raise AnalysisPacketError("source_invalid", "analysis source is invalid")
    return value


def _revalidated_packet_value(
    packet: object,
    *,
    descriptor: object | None = None,
) -> dict[str, object]:
    try:
        if (
            type(packet) is not AnalysisPacket
            or packet._factory_token is not _PACKET_FACTORY_TOKEN
            or type(packet._packet_bytes) is not bytes
            or type(packet._packet_digest) is not str
        ):
            raise AnalysisContractError()
        value = _fresh_packet_value(packet._packet_bytes)
        source_kind = value["source_kind"]
        basis = validate_source_basis(
            value["source_basis"],
            source_kind=source_kind,
        )
        stable_source = ValidatedEvidenceSource(
            source_kind,
            basis,
            value["source"],
        )
        expected_value: dict[str, object] = {
            "packet_version": PACKET_VERSION,
            "analysis_job_id": value["analysis_job_id"],
            "source_kind": stable_source.source_kind,
            "source_basis": stable_source.source_basis,
            "source": stable_source.source,
        }
        expected_bytes = canonical_json_bytes(expected_value)
        maximum = (
            NATIVE_PACKET_MAX_BYTES
            if stable_source.source_kind == "native_bundle"
            else LEGACY_PACKET_MAX_BYTES
        )
        if (
            value["packet_version"] != PACKET_VERSION
            or packet._packet_bytes != expected_bytes
            or len(expected_bytes) > maximum
            or packet._packet_digest
            != sealed_domain_digest("taskgov-analysis-packet-v1", expected_bytes)
        ):
            raise AnalysisContractError()
        if descriptor is not None:
            bound = validate_descriptor(descriptor)
            if (
                value["analysis_job_id"] != bound["analysis_job_id"]
                or stable_source.source_kind != bound["source_kind"]
                or stable_source.source_basis != bound["source_basis"]
            ):
                raise AnalysisContractError()
    except (AnalysisContractError, AnalysisPacketError, EvidenceConsumerError) as exc:
        raise AnalysisPacketError("source_invalid", "analysis source is invalid") from exc
    return value


def build_analysis_packet(
    descriptor: object,
    source: ValidatedEvidenceSource,
) -> AnalysisPacket:
    """Build one memory-only packet bound byte-for-byte to its descriptor."""

    try:
        stable_source = revalidate_validated_source(source)
        bound = validate_descriptor(descriptor)
        basis = validate_source_basis(
            stable_source.source_basis,
            source_kind=stable_source.source_kind,
        )
    except (AnalysisContractError, EvidenceConsumerError) as exc:
        raise AnalysisPacketError("source_invalid", "analysis source is invalid") from exc
    source_kind = stable_source.source_kind
    source_value = stable_source.source
    if (
        bound["source_kind"] != source_kind
        or bound["source_basis"] != basis
        or (source_kind == "native_bundle" and source_value is None)
        or (source_kind == "legacy_index_entry" and source_value is not None)
    ):
        raise AnalysisPacketError("source_invalid", "analysis source is invalid")
    value: dict[str, object] = {
        "packet_version": PACKET_VERSION,
        "analysis_job_id": bound["analysis_job_id"],
        "source_kind": source_kind,
        "source_basis": deepcopy(basis),
        "source": deepcopy(source_value),
    }
    try:
        packet_bytes = canonical_json_bytes(value)
    except AnalysisContractError as exc:
        raise AnalysisPacketError("source_invalid", "analysis source is invalid") from exc
    maximum = (
        NATIVE_PACKET_MAX_BYTES
        if source_kind == "native_bundle"
        else LEGACY_PACKET_MAX_BYTES
    )
    if len(packet_bytes) > maximum:
        raise AnalysisPacketError(
            "packet_too_large",
            "analysis packet exceeds the supported size",
        )
    return AnalysisPacket(
        _PACKET_FACTORY_TOKEN,
        packet_bytes=packet_bytes,
        packet_digest=sealed_domain_digest(
            "taskgov-analysis-packet-v1",
            packet_bytes,
        ),
    )


def revalidate_analysis_packet(
    packet: object,
    descriptor: object,
) -> AnalysisPacket:
    """Revalidate one sealed packet and its exact descriptor binding."""

    _revalidated_packet_value(packet, descriptor=descriptor)
    return packet


def build_analysis_stdin_frame(
    *,
    prompt_bytes: bytes,
    packet: AnalysisPacket,
) -> AnalysisInputFrame:
    """Frame the exact versioned prompt and canonical packet for stdin."""

    if (
        type(prompt_bytes) is not bytes
        or not prompt_bytes
        or prompt_bytes.startswith(b"\xef\xbb\xbf")
        or b"\0" in prompt_bytes
        or b"\r" in prompt_bytes
        or not prompt_bytes.endswith(b"\n")
        or prompt_bytes.endswith(b"\n\n")
    ):
        raise AnalysisPacketError("input_invalid", "analysis input is invalid")
    try:
        prompt_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AnalysisPacketError("input_invalid", "analysis input is invalid") from exc
    _revalidated_packet_value(packet)
    packet_bytes = packet.packet_bytes
    prefix = (
        b"taskgov-analysis-stdin-v1\n"
        + b"prompt-length:"
        + str(len(prompt_bytes)).encode("ascii")
        + b"\npacket-length:"
        + str(len(packet_bytes)).encode("ascii")
        + b"\n\n"
    )
    stdin_bytes = prefix + prompt_bytes + packet_bytes + b"\n"
    if len(stdin_bytes) > ANALYSIS_STDIN_MAX_BYTES:
        raise AnalysisPacketError(
            "input_too_large",
            "analysis input exceeds the supported size",
        )
    return AnalysisInputFrame(
        stdin_bytes=stdin_bytes,
        prompt_digest=sealed_domain_digest(
            "taskgov-analysis-prompt-v1",
            prompt_bytes,
        ),
        packet_digest=packet.packet_digest,
    )


__all__ = (
    "AnalysisInputFrame",
    "AnalysisPacket",
    "AnalysisPacketError",
    "FIXED_PROMPT_BYTES",
    "FIXED_PROMPT_DIGEST",
    "build_analysis_packet",
    "build_analysis_stdin_frame",
    "revalidate_analysis_packet",
)
