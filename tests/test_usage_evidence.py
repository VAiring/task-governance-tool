"""Closed numerical snapshot format; no fabricated finality or private fields."""

import copy
import unittest

from tests.test_usage_attribution import response
from task_governance_tool.usage_attribution import _models
from task_governance_tool.usage_evidence import snapshot, validate, digest
from task_governance_tool.usage_values import UsageError


class UsageEvidenceFormatTests(unittest.TestCase):
    def component(self):
        return {"executions": ["tg_execution_" + "a" * 16], "response_keys": [("openai", "r1")],
                "models": _models([response(1)]), "diagnostics": []}

    def test_digest_membership_and_observed_not_final(self):
        value = snapshot("project", self.component())
        self.assertEqual(value["quality"], "pending")
        self.assertEqual(value["coverage"], "registered_only")
        self.assertEqual(value["response_set_digest"], digest([("openai", "r1")]))
        self.assertEqual(validate(value, "project"), value)
        self.assertEqual(value, snapshot("project", self.component()))

    def test_conflict_and_empty_are_not_complete_or_measured_zero(self):
        component = self.component()
        component.update(response_keys=[], models=[], diagnostics=["response_conflict"])
        value = snapshot("project", component)
        self.assertEqual(value["quality"], "conflicting")
        self.assertEqual(value["models"], [])

    def test_untrusted_document_cannot_emit_arbitrary_fields_or_gap_text(self):
        original = snapshot("project", self.component())
        changes = ({"prompt": "PRIVATE"}, {"quality": "complete"}, {"gaps": ["PRIVATE"]},
                   {"executions": ["PRIVATE"]}, {"project_id": "different"}, {"response_count": True})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(UsageError):
                value = {**copy.deepcopy(original), **change}
                value["snapshot_id"] = digest({k: v for k, v in value.items() if k != "snapshot_id"})
                validate(value, "project")

    def test_predecessors_change_identity_without_mutating_previous(self):
        first = snapshot("project", self.component())
        second = snapshot("project", self.component(), [first["snapshot_id"]])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["predecessors"], [])
