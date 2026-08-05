"""Deterministic relocation-confirmation token codec.

The token is a bounded checksum transport for explicit setup confirmation.  It
is deliberately not a credential, signature, secret, or business-data
fingerprint.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from task_governance_tool.storage import validate_identity_project_id


TOKEN_PREFIX = "tgr1"
TOKEN_TTL_SECONDS = 900
TOKEN_MAX_ASCII_BYTES = 2_048
MAX_BINDING_GENERATION = 9_223_372_036_854_775_806
SUPPORTED_SOURCE_SCHEMA_MIN = 1
SUPPORTED_SOURCE_SCHEMA_MAX = 18
LEGACY_PROJECTS_SOURCE_SCHEMA_MAX = 14

IDENTITY_SCHEMES = frozenset({"legacy_path_v1", "uuid_v1"})
SOURCE_LAYOUTS = frozenset({"fixed_current_v1", "legacy_projects_v1"})
CLAIM_KEYS = frozenset(
    {
        "binding_generation",
        "expires_at",
        "identity_scheme",
        "issued_at",
        "new_path_hash",
        "old_path_hash",
        "project_id",
        "source_layout",
        "source_schema_version",
        "v",
    }
)

INVALID_CODE = "relocation_token_invalid"
INVALID_MESSAGE = "relocation confirmation is invalid"
EXPIRED_CODE = "relocation_token_expired"
EXPIRED_MESSAGE = (
    "relocation confirmation has expired; run setup --read-only again"
)

_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_CANONICAL_UTC_SECOND = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)


class RelocationTokenError(ValueError):
    """A fixed, data-free relocation token validation failure."""

    def __init__(self, code: str = INVALID_CODE, message: str = INVALID_MESSAGE):
        self.code = code
        self.message = message
        super().__init__(message)


class _DuplicateJsonKey(ValueError):
    pass


def _invalid() -> RelocationTokenError:
    return RelocationTokenError()


def _expired() -> RelocationTokenError:
    return RelocationTokenError(EXPIRED_CODE, EXPIRED_MESSAGE)


def _validate_hash(value: object) -> str:
    if type(value) is not str or _LOWER_HEX_64.fullmatch(value) is None:
        raise _invalid()
    return value


def _parse_timestamp(value: object) -> datetime:
    if (
        type(value) is not str
        or _CANONICAL_UTC_SECOND.fullmatch(value) is None
    ):
        raise _invalid()
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        raise _invalid() from None


def _format_timestamp(value: datetime) -> str:
    canonical = value.astimezone(UTC)
    return (
        f"{canonical.year:04d}-{canonical.month:02d}-{canonical.day:02d}"
        f"T{canonical.hour:02d}:{canonical.minute:02d}:"
        f"{canonical.second:02d}Z"
    )


def _expected_expiry(issued_at: str) -> str:
    try:
        expiry = _parse_timestamp(issued_at) + timedelta(
            seconds=TOKEN_TTL_SECONDS
        )
    except (OverflowError, RelocationTokenError):
        raise _invalid() from None
    return _format_timestamp(expiry)


def relocation_token_expiry(issued_at: str) -> str:
    """Return the canonical expiry exactly 900 seconds after issuance."""

    return _expected_expiry(issued_at)


@dataclass(frozen=True)
class RelocationContext:
    """Path-free state that one relocation confirmation binds."""

    project_id: str
    identity_scheme: str
    binding_generation: int
    old_path_hash: str
    new_path_hash: str
    source_layout: str
    source_schema_version: int

    def __post_init__(self) -> None:
        if type(self.identity_scheme) is not str or (
            self.identity_scheme not in IDENTITY_SCHEMES
        ):
            raise _invalid()
        try:
            validate_identity_project_id(
                self.project_id,
                self.identity_scheme,
            )
        except Exception:
            raise _invalid() from None
        if (
            type(self.binding_generation) is not int
            or not 1
            <= self.binding_generation
            <= MAX_BINDING_GENERATION
        ):
            raise _invalid()
        old_hash = _validate_hash(self.old_path_hash)
        new_hash = _validate_hash(self.new_path_hash)
        if old_hash == new_hash:
            raise _invalid()
        if type(self.source_layout) is not str or (
            self.source_layout not in SOURCE_LAYOUTS
        ):
            raise _invalid()
        if (
            type(self.source_schema_version) is not int
            or not SUPPORTED_SOURCE_SCHEMA_MIN
            <= self.source_schema_version
            <= SUPPORTED_SOURCE_SCHEMA_MAX
            or (
                self.source_layout == "legacy_projects_v1"
                and self.source_schema_version
                > LEGACY_PROJECTS_SOURCE_SCHEMA_MAX
            )
        ):
            raise _invalid()


@dataclass(frozen=True)
class RelocationTokenClaims:
    """Validated token claims, with temporal expiry checked separately."""

    context: RelocationContext
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        if type(self.context) is not RelocationContext:
            raise _invalid()
        issued = _parse_timestamp(self.issued_at)
        expiry = _parse_timestamp(self.expires_at)
        if expiry <= issued or self.expires_at != _expected_expiry(self.issued_at):
            raise _invalid()


def _claims_payload(claims: RelocationTokenClaims) -> dict[str, object]:
    context = claims.context
    return {
        "v": 1,
        "project_id": context.project_id,
        "identity_scheme": context.identity_scheme,
        "binding_generation": context.binding_generation,
        "old_path_hash": context.old_path_hash,
        "new_path_hash": context.new_path_hash,
        "source_layout": context.source_layout,
        "source_schema_version": context.source_schema_version,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
    }


def _canonical_payload_bytes(claims: RelocationTokenClaims) -> bytes:
    try:
        return json.dumps(
            _claims_payload(claims),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise _invalid() from None


def _encode_payload(claims: RelocationTokenClaims) -> str:
    return (
        base64.urlsafe_b64encode(_canonical_payload_bytes(claims))
        .decode("ascii")
        .rstrip("=")
    )


def encode_relocation_token(
    context: RelocationContext,
    *,
    issued_at: str,
) -> str:
    """Encode one canonical 15-minute relocation confirmation token."""

    if type(context) is not RelocationContext:
        raise _invalid()
    claims = RelocationTokenClaims(
        context=context,
        issued_at=issued_at,
        expires_at=relocation_token_expiry(issued_at),
    )
    payload = _encode_payload(claims)
    signed_part = f"{TOKEN_PREFIX}.{payload}"
    checksum = hashlib.sha256(signed_part.encode("ascii")).hexdigest()
    token = f"{signed_part}.{checksum}"
    if len(token.encode("ascii")) > TOKEN_MAX_ASCII_BYTES:
        raise _invalid()
    return token


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _ascii_token(token: object) -> bytes:
    if type(token) is not str:
        raise _invalid()
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError:
        raise _invalid() from None
    if not encoded or len(encoded) > TOKEN_MAX_ASCII_BYTES:
        raise _invalid()
    return encoded


def _decode_payload(payload: str) -> dict[str, Any]:
    if (
        not payload
        or "=" in payload
        or _BASE64URL.fullmatch(payload) is None
        or len(payload) % 4 == 1
    ):
        raise _invalid()
    padding = "=" * ((4 - len(payload) % 4) % 4)
    try:
        raw = base64.b64decode(
            payload + padding,
            altchars=b"-_",
            validate=True,
        )
        text = raw.decode("utf-8")
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (
        binascii.Error,
        UnicodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        TypeError,
        ValueError,
    ):
        raise _invalid() from None
    if type(decoded) is not dict or set(decoded) != CLAIM_KEYS:
        raise _invalid()
    return decoded


def _claims_from_payload(payload: dict[str, Any]) -> RelocationTokenClaims:
    if type(payload.get("v")) is not int or payload["v"] != 1:
        raise _invalid()
    context = RelocationContext(
        project_id=payload["project_id"],
        identity_scheme=payload["identity_scheme"],
        binding_generation=payload["binding_generation"],
        old_path_hash=payload["old_path_hash"],
        new_path_hash=payload["new_path_hash"],
        source_layout=payload["source_layout"],
        source_schema_version=payload["source_schema_version"],
    )
    return RelocationTokenClaims(
        context=context,
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
    )


def decode_relocation_token(
    token: str,
    *,
    now: str,
) -> RelocationTokenClaims:
    """Validate structure/checksum/future time and return canonical claims.

    Expiry is deliberately checked by :func:`require_unexpired` so setup can
    perform successful-token replay lookup before applying the expiry result.
    """

    _ascii_token(token)
    if "=" in token:
        raise _invalid()
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise _invalid()
    payload_text, checksum = parts[1], parts[2]
    if _LOWER_HEX_64.fullmatch(checksum) is None:
        raise _invalid()
    expected_checksum = hashlib.sha256(
        f"{TOKEN_PREFIX}.{payload_text}".encode("ascii")
    ).hexdigest()
    if not hmac.compare_digest(checksum, expected_checksum):
        raise _invalid()

    decoded = _decode_payload(payload_text)
    claims = _claims_from_payload(decoded)
    if _encode_payload(claims) != payload_text:
        raise _invalid()

    now_value = _parse_timestamp(now)
    if _parse_timestamp(claims.issued_at) > now_value:
        raise _invalid()
    return claims


def require_unexpired(
    claims: RelocationTokenClaims,
    *,
    now: str,
) -> None:
    """Require the exact ``issued_at <= now < expires_at`` acceptance window."""

    if type(claims) is not RelocationTokenClaims:
        raise _invalid()
    now_value = _parse_timestamp(now)
    if now_value < _parse_timestamp(claims.issued_at):
        raise _invalid()
    if now_value >= _parse_timestamp(claims.expires_at):
        raise _expired()


def context_matches(
    claims: RelocationTokenClaims,
    expected: RelocationContext,
) -> bool:
    """Return exact path-free binding-context equality."""

    if (
        type(claims) is not RelocationTokenClaims
        or type(expected) is not RelocationContext
    ):
        raise _invalid()
    return claims.context == expected


def relocation_token_digest(token: str) -> str:
    """Return SHA-256 of an ASCII token after bounded transport validation."""

    return hashlib.sha256(_ascii_token(token)).hexdigest()
