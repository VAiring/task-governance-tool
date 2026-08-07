from __future__ import annotations

import hashlib
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.m23_test_support import expected_markdown_v1


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.analysis_renderer import (  # noqa: E402
    AnalysisRendererError,
    render_markdown_v1,
)


REPORT_DOMAIN = b"taskgov-analysis-report-v1\0"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def report_envelope() -> dict[str, object]:
    payload = {
        "report_id": "tg_analysis_report_0123456789abcdef",
        "analysis_job_id": "tg_analysis_job_0123456789abcdef",
        "source_kind": "legacy_index_entry",
        "source_key": "sha256:" + "1" * 64,
        "recipe_digest": "sha256:" + "2" * 64,
        "inference_state": "disabled",
        "structural_facts": [],
        "trusted_caller_declarations": [],
        "legacy_absence": {
            "state": "legacy_unknown",
            "receipt_detail": "unavailable",
            "provenance_detail": "unavailable",
            "citation_id": "tg_analysis_citation_0123456789abcdef",
        },
        "llm_derived": [
            {
                "tag": "llm_derived/batch_analyzer/1",
                "non_authoritative": True,
                "text": 'é "quoted"\nline',
                "citation_ids": ["tg_analysis_citation_0123456789abcdef"],
                "uncertainty": "legacy_absence",
            }
        ],
        "omissions": [],
        "uncertainties": [],
        "declared_code_occurrences": [],
        "citations": [],
        "reproducibility": {
            "producer_version": 1,
            "declared_model_id": None,
            "prompt_schema_version": 1,
            "prompt_digest": None,
            "input_digest": "sha256:" + "3" * 64,
            "accepted_output_digest": None,
            "report_schema_version": 1,
            "renderer_version": 1,
        },
    }
    digest = "sha256:" + hashlib.sha256(
        REPORT_DOMAIN + canonical(payload)
    ).hexdigest()
    return {"report_schema_version": 1, "report_digest": digest, "payload": payload}


class AnalysisRendererTests(unittest.TestCase):
    def test_exact_markdown_v1_framing_order_and_json_escaping(self):
        envelope = report_envelope()
        document = canonical(envelope) + b"\n"
        rendered = render_markdown_v1(document)
        self.assertEqual(rendered, expected_markdown_v1(envelope))
        self.assertEqual(rendered.count(b"\n## "), 10)
        self.assertTrue(rendered.endswith(b"\n"))
        self.assertFalse(rendered.endswith(b"\n\n"))
        self.assertNotIn(b"\r", rendered)
        self.assertNotIn(b"\t", rendered)
        self.assertNotIn(b"```", rendered)
        self.assertNotIn(b"<", rendered)
        self.assertIn('é'.encode("utf-8"), rendered)
        self.assertIn(b'\\"quoted\\"\\nline', rendered)

    def test_renderer_rejects_noncanonical_wrong_digest_and_extra_key(self):
        envelope = report_envelope()
        valid = canonical(envelope) + b"\n"
        invalid_documents = [
            valid[:-1],
            b" " + valid,
        ]
        wrong_digest = deepcopy(envelope)
        wrong_digest["report_digest"] = "sha256:" + "0" * 64
        invalid_documents.append(canonical(wrong_digest) + b"\n")
        extra = deepcopy(envelope)
        extra["payload"]["extra"] = None
        extra["report_digest"] = "sha256:" + hashlib.sha256(
            REPORT_DOMAIN + canonical(extra["payload"])
        ).hexdigest()
        invalid_documents.append(canonical(extra) + b"\n")

        for document in invalid_documents:
            with self.subTest(document=document[:40]):
                with self.assertRaises(AnalysisRendererError) as raised:
                    render_markdown_v1(document)
                self.assertEqual(raised.exception.code, "report_invalid")

    def test_renderer_enforces_exact_markdown_byte_cap(self):
        envelope = report_envelope()
        document = canonical(envelope) + b"\n"
        expected = expected_markdown_v1(envelope)
        with patch(
            "task_governance_tool.analysis_renderer.REPORT_MARKDOWN_MAX_BYTES",
            len(expected) - 1,
        ):
            with self.assertRaises(AnalysisRendererError) as raised:
                render_markdown_v1(document)
        self.assertEqual(raised.exception.code, "report_invalid")


if __name__ == "__main__":
    unittest.main()
