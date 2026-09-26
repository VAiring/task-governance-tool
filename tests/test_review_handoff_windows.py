"""Native Windows shell transport; excluded from Linux/macOS platform checks."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.m14_test_support import make_physical_install
from tests.test_review_handoff import packet_for
from tests.test_review_results import document, encode


class WindowsReviewHandoffTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows shell transport")
    def test_powershell_51_save_accepts_utf8_result_without_generated_validation_code(self):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell 5.1 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            install = make_physical_install(Path(temporary).resolve(), git_managed=True)
            root = install.project_root
            with (root / ".gitignore").open("a", encoding="utf-8") as target:
                target.write("/reviews/\n")
            (root / "reviews").mkdir()
            payload = document()
            (root / "reviews/packet.json").write_bytes(encode(packet_for(payload)))
            command = ("$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                       "@'\n" + encode(payload).decode("utf-8") + "\n'@ | & '" + sys.executable +
                       "' -I -S .agents/skills/task-governance-tool/scripts/review_handoff.py save --repo . "
                       "--packet reviews/packet.json --output reviews/a.json")
            result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                     base64.b64encode(command.encode("utf-16le")).decode("ascii")],
                                    cwd=root, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
            self.assertEqual(json.loads((root / "reviews/a.json").read_bytes()), payload)
