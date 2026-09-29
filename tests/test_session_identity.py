from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool" / "scripts"))

from task_governance_tool.session_identity import CallerIdentity, capture_caller_identity
from task_governance_tool.task_values import TaskValidationError


SESSION = "01234567-89ab-7cde-8fab-0123456789ab"
PARENT = "11234567-89ab-7cde-8fab-0123456789ab"


class SessionIdentityTests(unittest.TestCase):
    def test_thread_identity_not_shared_parent_session(self):
        caller = capture_caller_identity({"CODEX_THREAD_ID": SESSION, "CODEX_SESSION_ID": PARENT})
        self.assertEqual(caller.require(), SESSION)

    def test_no_parent_fallback_and_no_raw_failure_value(self):
        for value in (None, "", " ", SESSION.upper(), SESSION.replace("-", ""),
                      "private-environment-value", SESSION + "\n"):
            with self.subTest(value=value):
                environment = {"CODEX_SESSION_ID": PARENT}
                if value is not None:
                    environment["CODEX_THREAD_ID"] = value
                caller = capture_caller_identity(environment)
                self.assertIsNone(caller.session_id)
                self.assertNotIn("private-environment-value", repr(caller))
                with self.assertRaises(TaskValidationError) as error:
                    caller.require()
                self.assertEqual(error.exception.code, "session_identity_required")
                self.assertNotIn("private-environment-value", str(error.exception))

    def test_capture_reads_only_one_key_once(self):
        environment = mock.Mock()
        environment.get.return_value = SESSION
        self.assertEqual(capture_caller_identity(environment).session_id, SESSION)
        environment.get.assert_called_once_with("CODEX_THREAD_ID")
        self.assertEqual(environment.mock_calls, [mock.call.get("CODEX_THREAD_ID")])

    def test_captured_identity_is_immutable_and_not_reloaded(self):
        environment = {"CODEX_THREAD_ID": SESSION}
        caller = capture_caller_identity(environment)
        environment["CODEX_THREAD_ID"] = PARENT
        self.assertEqual(caller.require(), SESSION)
        with self.assertRaises(FrozenInstanceError):
            caller.session_id = PARENT

    def test_invalid_typed_input_is_rejected_without_echo(self):
        for value in ("private-value", 4, True, []):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                CallerIdentity(value)
            self.assertEqual(str(error.exception), "invalid caller identity")


if __name__ == "__main__":
    unittest.main()
