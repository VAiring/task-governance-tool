from __future__ import annotations

import math
import unittest

from tests.evidence_reader_codec import (
    EvidenceCodecError,
    canonical_json_bytes,
    parse_canonical_json_document,
)


class EvidenceReaderCodecTests(unittest.TestCase):
    def test_existing_literal_vector_and_round_trip(self):
        value = {"z": "é", "a": [None, False, True, -2, "\x00"]}
        expected = b'{"a":[null,false,true,-2,"\\u0000"],"z":"\xc3\xa9"}'
        self.assertEqual(canonical_json_bytes(value), expected)
        self.assertEqual(
            parse_canonical_json_document(expected + b"\n", maximum=1024),
            value,
        )

    def test_exact_sorted_utf8_and_control_escaping(self):
        value = {
            "\U00010000": "non-bmp",
            "\ue000": "private",
            "z": '"/\\\b\f\n\r\t\x00\x1f',
            "a": [None, False, True, -12, 0, "é/"],
        }
        expected = (
            b'{"a":[null,false,true,-12,0,"\xc3\xa9/"],'
            b'"z":"\\"/\\\\\\b\\f\\n\\r\\t\\u0000\\u001f",'
            b'"\xee\x80\x80":"private","\xf0\x90\x80\x80":"non-bmp"}'
        )
        self.assertEqual(canonical_json_bytes(value), expected)
        self.assertEqual(
            parse_canonical_json_document(expected + b"\n", maximum=1024),
            value,
        )

    def test_no_unicode_normalization(self):
        composed = canonical_json_bytes({"v": "é"})
        decomposed = canonical_json_bytes({"v": "e\u0301"})
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(composed, b'{"v":"\xc3\xa9"}')
        self.assertEqual(decomposed, b'{"v":"e\xcc\x81"}')

    def test_rejects_non_json_numeric_container_key_and_unicode_values(self):
        cyclic: list[object] = []
        cyclic.append(cyclic)
        for value in (
            1.0,
            math.nan,
            math.inf,
            (1, 2),
            {1: "value"},
            {"v": "\ud800"},
            cyclic,
            object(),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(EvidenceCodecError):
                    canonical_json_bytes(value)

    def test_integer_and_boolean_types_remain_distinct(self):
        self.assertEqual(canonical_json_bytes(True), b"true")
        self.assertEqual(canonical_json_bytes(1), b"1")
        self.assertEqual(canonical_json_bytes(-0), b"0")

    def test_parser_rejects_duplicate_float_and_noncanonical_documents(self):
        for document in (
            b'{"a":1,"a":2}\n',
            b'{"a":{"b":1,"b":2}}\n',
            b'{"a":1.0}\n',
            b'{"a":1e0}\n',
            b'{"a":NaN}\n',
            b'{"a":Infinity}\n',
            b'{ "a":1}\n',
            b'{"b":2,"a":1}\n',
            br'{"a":"\u0061"}' + b"\n",
            br'{"a":"\ud800"}' + b"\n",
            b'{"a":"\xff"}\n',
            b'\xef\xbb\xbf{}\n',
            b'{}\r\n',
            b'{}\n\n',
            b'{}',
        ):
            with self.subTest(document=document):
                with self.assertRaises(EvidenceCodecError):
                    parse_canonical_json_document(document, maximum=1024)

    def test_parser_exact_type_and_size_boundaries(self):
        self.assertEqual(parse_canonical_json_document(b"0\n", maximum=2), 0)
        self.assertEqual(parse_canonical_json_document(b"{}\n", maximum=3), {})
        for document, maximum in (
            ("{}\n", 3),
            (bytearray(b"{}\n"), 3),
            (b"{}\n", True),
            (b"{}\n", 3.0),
            (b"0\n", 1),
            (b"{}\n", 2),
            (b"\n", 2),
        ):
            with self.subTest(document=document, maximum=maximum):
                with self.assertRaises(EvidenceCodecError):
                    parse_canonical_json_document(document, maximum=maximum)


if __name__ == "__main__":
    unittest.main()
