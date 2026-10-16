from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))
from task_governance_tool.review_answer import AnswerError, decode_answer

def payload():
    return dict(verdict="pass", summary="  日本語確認  ", findings=[], checks=[], limitations=[])

class DecoderTests(unittest.TestCase):
    def test_original_and_fenced_payload_bytes(self):
        body = json.dumps(payload(), ensure_ascii=False).encode()
        for raw, start in ((b" \n" + body + b"\t", 2), (b" \n```json\r\n \t" + body + b"\r\n```\n", 13)):
            result = decode_answer(raw)
            self.assertEqual(result.raw, raw)
            self.assertEqual(result.payload, payload())
            self.assertEqual(raw[result.payload_start:result.payload_start+result.payload_length], body)
            self.assertEqual(result.original_digest, "sha256:" + hashlib.sha256(raw).hexdigest())
            self.assertEqual(result.payload_digest, "sha256:" + hashlib.sha256(body).hexdigest())

    def test_closed_syntax_no_digest_before_admissibility(self):
        body = json.dumps(payload()).encode()
        bad = [b"\xef\xbb\xbf"+body, body+b" trailing", b"```JSON\n"+body+b"\n```",
               b" ```json\n"+body+b"\n```", b"```json\n"+body+b"\n``` \n",
               body.replace(b'"pass"', b'1.0'), body.replace(b'"pass"', b'NaN'),
               body.replace(b'"pass"', b'"pass", "verdict":"pass"'),
               body.replace(b'"pass"', b'"\\ud800"'), b"\xff", b"", b" "*262145]
        for raw in bad:
            with self.subTest(raw=raw[:40]), self.assertRaises(AnswerError) as caught:
                decode_answer(raw)
            self.assertFalse(caught.exception.privacy_admissible)
            self.assertIsNone(caught.exception.observed_digest)

    def test_semantic_failure_keeps_only_admissible_digest(self):
        value = payload(); value["verdict"] = "invalid"
        raw = json.dumps(value).encode()
        with self.assertRaises(AnswerError) as caught:
            decode_answer(raw)
        self.assertTrue(caught.exception.privacy_admissible)
        self.assertEqual(caught.exception.observed_digest, "sha256:" + hashlib.sha256(raw).hexdigest())
        value["summary"] = "Authorization: Bearer private-token"
        with self.assertRaises(AnswerError) as caught:
            decode_answer(json.dumps(value).encode())
        self.assertEqual(caught.exception.failure["class"], "privacy_rejected")
        self.assertIsNone(caught.exception.observed_digest)
        self.assertNotIn("private-token", str(caught.exception))

    def test_normalized_duplicates_and_scalar_limits(self):
        value = payload(); value["findings"] = [dict(severity="high", summary="x"), dict(severity="high", summary=" x ")]
        with self.assertRaises(AnswerError) as caught:
            decode_answer(json.dumps(value).encode())
        self.assertEqual(caught.exception.failure["reason"], "duplicate")
        value = payload(); value["summary"] = "😀"*1000
        self.assertEqual(decode_answer(json.dumps(value).encode()).payload, value)
        value["summary"] += "😀"
        with self.assertRaises(AnswerError) as caught:
            decode_answer(json.dumps(value).encode())
        self.assertEqual(caught.exception.failure["class"], "capacity_exceeded")

if __name__ == "__main__":
    unittest.main()
