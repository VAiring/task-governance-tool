import base64
import hashlib
import hmac
import json
import sys
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.relocation import (  # noqa: E402
    CLAIM_KEYS,
    EXPIRED_CODE,
    EXPIRED_MESSAGE,
    INVALID_CODE,
    INVALID_MESSAGE,
    MAX_BINDING_GENERATION,
    TOKEN_MAX_ASCII_BYTES,
    RelocationContext,
    RelocationTokenClaims,
    RelocationTokenError,
    context_matches,
    decode_relocation_token,
    encode_relocation_token,
    relocation_token_digest,
    relocation_token_expiry,
    require_unexpired,
)


ISSUED_AT = "2026-07-29T01:02:03Z"
EXPIRES_AT = "2026-07-29T01:17:03Z"
OLD_HASH = "1" * 64
NEW_HASH = "2" * 64
LEGACY_PROJECT_ID = "governed-project-0123456789ab"
UUID_PROJECT_ID = "tg_project_00112233445546778899aabbccddeeff"


def legacy_context(**overrides) -> RelocationContext:
    values = {
        "project_id": LEGACY_PROJECT_ID,
        "identity_scheme": "legacy_path_v1",
        "binding_generation": 1,
        "old_path_hash": OLD_HASH,
        "new_path_hash": NEW_HASH,
        "source_layout": "legacy_projects_v1",
        "source_schema_version": 14,
    }
    values.update(overrides)
    return RelocationContext(**values)


def canonical_payload(**overrides) -> dict[str, object]:
    values = {
        "binding_generation": 1,
        "expires_at": EXPIRES_AT,
        "identity_scheme": "legacy_path_v1",
        "issued_at": ISSUED_AT,
        "new_path_hash": NEW_HASH,
        "old_path_hash": OLD_HASH,
        "project_id": LEGACY_PROJECT_ID,
        "source_layout": "legacy_projects_v1",
        "source_schema_version": 14,
        "v": 1,
    }
    values.update(overrides)
    return values


def raw_token_from_bytes(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signed = f"tgr1.{encoded}"
    checksum = hashlib.sha256(signed.encode("ascii")).hexdigest()
    return f"{signed}.{checksum}"


def raw_token(payload: object, **json_options) -> str:
    options = {
        "sort_keys": True,
        "separators": (",", ":"),
        "ensure_ascii": True,
    }
    options.update(json_options)
    return raw_token_from_bytes(json.dumps(payload, **options).encode("utf-8"))


class RelocationTokenCodecTests(unittest.TestCase):
    def assert_invalid(self, callable_, *args, **kwargs) -> RelocationTokenError:
        with self.assertRaises(RelocationTokenError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(
            (raised.exception.code, raised.exception.message),
            (INVALID_CODE, INVALID_MESSAGE),
        )
        self.assertEqual(raised.exception.args, (INVALID_MESSAGE,))
        self.assertIsNone(raised.exception.__cause__)
        return raised.exception

    def test_encode_is_exact_canonical_unpadded_ascii_and_round_trips(self):
        context = legacy_context()

        token = encode_relocation_token(context, issued_at=ISSUED_AT)

        self.assertLessEqual(len(token.encode("ascii")), TOKEN_MAX_ASCII_BYTES)
        prefix, payload, checksum = token.split(".")
        self.assertEqual(prefix, "tgr1")
        self.assertNotIn("=", token)
        self.assertEqual(
            checksum,
            hashlib.sha256(f"tgr1.{payload}".encode("ascii")).hexdigest(),
        )
        decoded_json = base64.urlsafe_b64decode(
            payload + "=" * ((4 - len(payload) % 4) % 4)
        ).decode("utf-8")
        self.assertEqual(
            decoded_json,
            json.dumps(
                canonical_payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        )

        claims = decode_relocation_token(token, now=ISSUED_AT)
        self.assertEqual(
            claims,
            RelocationTokenClaims(
                context=context,
                issued_at=ISSUED_AT,
                expires_at=EXPIRES_AT,
            ),
        )
        require_unexpired(claims, now=ISSUED_AT)
        self.assertTrue(context_matches(claims, context))

    def test_uuid_and_numeric_boundaries_round_trip(self):
        context = RelocationContext(
            project_id=UUID_PROJECT_ID,
            identity_scheme="uuid_v1",
            binding_generation=MAX_BINDING_GENERATION,
            old_path_hash=OLD_HASH,
            new_path_hash=NEW_HASH,
            source_layout="fixed_current_v1",
            source_schema_version=20,
        )

        token = encode_relocation_token(context, issued_at=ISSUED_AT)
        claims = decode_relocation_token(token, now="2026-07-29T01:17:02Z")

        self.assertEqual(claims.context, context)
        self.assertEqual(claims.expires_at, EXPIRES_AT)
        require_unexpired(claims, now="2026-07-29T01:17:02Z")

    def test_timestamp_format_is_platform_independent_at_year_one(self):
        issued_at = "0001-01-01T00:00:00Z"
        token = encode_relocation_token(
            legacy_context(),
            issued_at=issued_at,
        )

        claims = decode_relocation_token(token, now=issued_at)

        self.assertEqual(claims.issued_at, issued_at)
        self.assertEqual(claims.expires_at, "0001-01-01T00:15:00Z")
        self.assertEqual(
            relocation_token_expiry(issued_at),
            "0001-01-01T00:15:00Z",
        )
        require_unexpired(claims, now=issued_at)

    def test_context_rejects_invalid_scheme_specific_values_and_ranges(self):
        cases = (
            {"project_id": "invalid-project"},
            {
                "project_id": UUID_PROJECT_ID,
                "identity_scheme": "legacy_path_v1",
            },
            {
                "project_id": LEGACY_PROJECT_ID,
                "identity_scheme": "uuid_v1",
            },
            {
                "project_id": "tg_project_00112233445536778899aabbccddeeff",
                "identity_scheme": "uuid_v1",
            },
            {
                "project_id": "tg_project_00112233445546770899aabbccddeeff",
                "identity_scheme": "uuid_v1",
            },
            {"identity_scheme": "path_v2"},
            {"binding_generation": True},
            {"binding_generation": 0},
            {"binding_generation": MAX_BINDING_GENERATION + 1},
            {"old_path_hash": "A" * 64},
            {"old_path_hash": "1" * 63},
            {"new_path_hash": "g" * 64},
            {"new_path_hash": OLD_HASH},
            {"source_layout": "backup_v1"},
            {"source_schema_version": True},
            {"source_schema_version": 0},
            {"source_schema_version": 15},
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_invalid(legacy_context, **overrides)
        self.assert_invalid(
            legacy_context,
            source_layout="fixed_current_v1",
            source_schema_version=23,
        )

    def test_encode_rejects_invalid_context_time_and_oversized_transport(self):
        for issued_at in (
            "2026-07-29T01:02:03+00:00",
            "2026-7-29T01:02:03Z",
            "2026-02-29T01:02:03Z",
            "not-a-time",
            123,
        ):
            with self.subTest(issued_at=issued_at):
                self.assert_invalid(
                    encode_relocation_token,
                    legacy_context(),
                    issued_at=issued_at,
                )

        self.assert_invalid(
            encode_relocation_token,
            legacy_context(),
            issued_at="9999-12-31T23:59:59Z",
        )
        huge_context = legacy_context(
            project_id=f"{'a' * 1800}-0123456789ab",
        )
        self.assert_invalid(
            encode_relocation_token,
            huge_context,
            issued_at=ISSUED_AT,
        )
        self.assert_invalid(
            encode_relocation_token,
            object(),
            issued_at=ISSUED_AT,
        )

    def test_time_acceptance_has_exact_future_and_expiry_boundaries(self):
        token = encode_relocation_token(legacy_context(), issued_at=ISSUED_AT)

        self.assert_invalid(
            decode_relocation_token,
            token,
            now="2026-07-29T01:02:02Z",
        )
        issued = decode_relocation_token(token, now=ISSUED_AT)
        require_unexpired(issued, now=ISSUED_AT)
        final_second = decode_relocation_token(
            token,
            now="2026-07-29T01:17:02Z",
        )
        require_unexpired(final_second, now="2026-07-29T01:17:02Z")

        # Structural decode deliberately still succeeds after expiry so replay
        # digest lookup can precede the expiry result.
        expired_claims = decode_relocation_token(token, now=EXPIRES_AT)
        for now in (EXPIRES_AT, "2026-07-29T01:17:04Z"):
            with self.subTest(now=now), self.assertRaises(
                RelocationTokenError
            ) as raised:
                require_unexpired(expired_claims, now=now)
            self.assertEqual(
                (raised.exception.code, raised.exception.message),
                (EXPIRED_CODE, EXPIRED_MESSAGE),
            )
            self.assertIsNone(raised.exception.__cause__)

        self.assert_invalid(
            require_unexpired,
            expired_claims,
            now="2026-07-29T01:02:02Z",
        )
        self.assert_invalid(
            require_unexpired,
            expired_claims,
            now="invalid",
        )

    def test_transport_rejects_wrong_type_non_ascii_bounds_and_segments(self):
        token = encode_relocation_token(legacy_context(), issued_at=ISSUED_AT)
        prefix, payload, checksum = token.split(".")
        transport_cases = (
            None,
            token.encode("ascii"),
            "",
            "é",
            "a" * (TOKEN_MAX_ASCII_BYTES + 1),
            f"{prefix}.{payload}",
            f"{prefix}.{payload}.{checksum}.extra",
            f"tgr2.{payload}.{checksum}",
            f"{prefix}.{payload}.{checksum.upper()}",
            f"{prefix}.{payload}.{checksum[:-1]}",
            f"{prefix}.{payload}.{'0' * 64}",
            f"{prefix}.{payload}=.{checksum}",
        )

        for candidate in transport_cases:
            with self.subTest(candidate_type=type(candidate).__name__):
                self.assert_invalid(
                    decode_relocation_token,
                    candidate,
                    now=ISSUED_AT,
                )

    def test_payload_rejects_invalid_base64_utf8_json_and_duplicate_keys(self):
        invalid_payload_tokens = (
            raw_token_from_bytes(b"\xff"),
            raw_token_from_bytes(b"{"),
            raw_token_from_bytes(b"[]"),
            raw_token_from_bytes(
                (
                    '{"binding_generation":1,"binding_generation":1,'
                    '"expires_at":"2026-07-29T01:17:03Z",'
                    '"identity_scheme":"legacy_path_v1",'
                    '"issued_at":"2026-07-29T01:02:03Z",'
                    f'"new_path_hash":"{NEW_HASH}",'
                    f'"old_path_hash":"{OLD_HASH}",'
                    f'"project_id":"{LEGACY_PROJECT_ID}",'
                    '"source_layout":"legacy_projects_v1",'
                    '"source_schema_version":14,"v":1}'
                ).encode("utf-8")
            ),
            raw_token_from_bytes(b'{"v":NaN}'),
        )

        for token in invalid_payload_tokens:
            with self.subTest(token_digest=hashlib.sha256(token.encode()).hexdigest()):
                self.assert_invalid(
                    decode_relocation_token,
                    token,
                    now=ISSUED_AT,
                )

        signed = "tgr1.A"
        invalid_length_payload = (
            f"{signed}.{hashlib.sha256(signed.encode('ascii')).hexdigest()}"
        )
        self.assert_invalid(
            decode_relocation_token,
            invalid_length_payload,
            now=ISSUED_AT,
        )

        invalid_character_signed = "tgr1.ab+c"
        invalid_character_token = (
            f"{invalid_character_signed}."
            f"{hashlib.sha256(invalid_character_signed.encode()).hexdigest()}"
        )
        self.assert_invalid(
            decode_relocation_token,
            invalid_character_token,
            now=ISSUED_AT,
        )

    def test_payload_rejects_missing_extra_wrong_types_ranges_and_times(self):
        missing = canonical_payload()
        del missing["project_id"]
        extra = canonical_payload(extra="value")
        cases = (
            missing,
            extra,
            canonical_payload(v=True),
            canonical_payload(v=2),
            canonical_payload(project_id=123),
            canonical_payload(identity_scheme=None),
            canonical_payload(binding_generation=1.0),
            canonical_payload(binding_generation=MAX_BINDING_GENERATION + 1),
            canonical_payload(old_path_hash=NEW_HASH),
            canonical_payload(source_layout=1),
            canonical_payload(source_schema_version=14.0),
            canonical_payload(source_schema_version=15),
            canonical_payload(issued_at="2026-07-29T01:02:03+00:00"),
            canonical_payload(expires_at="2026-07-29T01:17:02Z"),
            canonical_payload(expires_at="2026-07-29T01:17:04Z"),
            canonical_payload(expires_at=123),
        )

        for payload in cases:
            with self.subTest(keys=tuple(sorted(payload))):
                self.assert_invalid(
                    decode_relocation_token,
                    raw_token(payload),
                    now=ISSUED_AT,
                )

    def test_payload_rejects_every_noncanonical_json_or_base64_spelling(self):
        payload = canonical_payload()
        noncanonical_json = (
            json.dumps(payload, sort_keys=False, ensure_ascii=True),
            json.dumps(payload, sort_keys=True, ensure_ascii=True),
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).replace(
                "legacy_projects_v1",
                r"legacy_projects_\u0076" + "1",
            ),
        )
        for text in noncanonical_json:
            with self.subTest(text_digest=hashlib.sha256(text.encode()).hexdigest()):
                self.assert_invalid(
                    decode_relocation_token,
                    raw_token_from_bytes(text.encode("utf-8")),
                    now=ISSUED_AT,
                )

        token = encode_relocation_token(legacy_context(), issued_at=ISSUED_AT)
        prefix, encoded, _checksum = token.split(".")
        padded = encoded + "=" * ((4 - len(encoded) % 4) % 4 or 4)
        signed = f"{prefix}.{padded}"
        padded_token = (
            f"{signed}.{hashlib.sha256(signed.encode('ascii')).hexdigest()}"
        )
        self.assert_invalid(
            decode_relocation_token,
            padded_token,
            now=ISSUED_AT,
        )

    def test_decoder_uses_constant_time_checksum_comparison(self):
        token = encode_relocation_token(legacy_context(), issued_at=ISSUED_AT)
        with mock.patch(
            "task_governance_tool.relocation.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as compared:
            decode_relocation_token(token, now=ISSUED_AT)

        compared.assert_called_once()
        supplied, expected = compared.call_args.args
        self.assertEqual(supplied, expected)

    def test_context_equality_is_exact_and_contains_no_raw_path(self):
        context = legacy_context()
        claims = RelocationTokenClaims(context, ISSUED_AT, EXPIRES_AT)
        self.assertEqual(
            set(asdict(context)),
            {
                "project_id",
                "identity_scheme",
                "binding_generation",
                "old_path_hash",
                "new_path_hash",
                "source_layout",
                "source_schema_version",
            },
        )
        self.assertFalse(hasattr(context, "old_path"))
        self.assertFalse(hasattr(context, "new_path"))
        alternatives = (
            legacy_context(project_id="other-project-fedcba987654"),
            legacy_context(binding_generation=2),
            legacy_context(old_path_hash="3" * 64),
            legacy_context(new_path_hash="3" * 64),
            legacy_context(source_layout="fixed_current_v1"),
            legacy_context(source_schema_version=13),
            RelocationContext(
                project_id=UUID_PROJECT_ID,
                identity_scheme="uuid_v1",
                binding_generation=1,
                old_path_hash=OLD_HASH,
                new_path_hash=NEW_HASH,
                source_layout="legacy_projects_v1",
                source_schema_version=14,
            ),
        )
        self.assertTrue(context_matches(claims, context))
        for alternative in alternatives:
            with self.subTest(alternative=alternative):
                self.assertFalse(context_matches(claims, alternative))

        self.assert_invalid(context_matches, object(), context)
        self.assert_invalid(context_matches, claims, object())

    def test_digest_is_exact_ascii_token_sha256_and_transport_is_bounded(self):
        token = encode_relocation_token(legacy_context(), issued_at=ISSUED_AT)
        self.assertEqual(
            relocation_token_digest(token),
            hashlib.sha256(token.encode("ascii")).hexdigest(),
        )
        self.assert_invalid(relocation_token_digest, None)
        self.assert_invalid(relocation_token_digest, "秘密")
        self.assert_invalid(
            relocation_token_digest,
            "a" * (TOKEN_MAX_ASCII_BYTES + 1),
        )

    def test_invalid_exceptions_never_echo_rejected_data(self):
        rejected = "private-path-and-token-material"
        raised = self.assert_invalid(
            decode_relocation_token,
            rejected,
            now=ISSUED_AT,
        )
        self.assertNotIn(rejected, str(raised))
        self.assertNotIn(rejected, repr(raised))
        self.assertIsNone(raised.__context__)

        with self.assertRaises(RelocationTokenError) as expired:
            require_unexpired(
                RelocationTokenClaims(
                    legacy_context(),
                    ISSUED_AT,
                    EXPIRES_AT,
                ),
                now=EXPIRES_AT,
            )
        self.assertEqual(str(expired.exception), EXPIRED_MESSAGE)
        self.assertNotIn(ISSUED_AT, str(expired.exception))


if __name__ == "__main__":
    unittest.main()
