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
from tests.test_review_results import document, encode, receipt, FINGERPRINT
from tests.test_review_handoff_preparation import PreparationFixture


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


class WindowsPreparationTests(PreparationFixture):
    @unittest.skipUnless(sys.platform == "win32", "Windows ACL inheritance")
    def test_missing_directory_chain_and_transport_files_inherit_parent_acl(self):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell is unavailable")

        def acl(relative):
            path = str(self.root / relative).replace("'", "''")
            kind = "Directory" if (self.root / relative).is_dir() else "File"
            command = ("$a = [System.IO." + kind + "]::GetAccessControl('" + path + "'); "
                "[pscustomobject]@{Protected=$a.AreAccessRulesProtected; "
                "Inherited=@($a.Access | Where-Object IsInherited).Count; "
                "Explicit=@($a.Access | Where-Object { -not $_.IsInherited }).Count; "
                "Dacl=$a.GetSecurityDescriptorSddlForm("
                "[System.Security.AccessControl.AccessControlSections]::Access)} | ConvertTo-Json -Compress")
            observed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                base64.b64encode(command.encode("utf-16le")).decode("ascii")],
                cwd=self.root, capture_output=True, check=False)
            self.assertEqual(observed.returncode, 0, observed.stdout or observed.stderr)
            return json.loads(observed.stdout)

        (self.root / "reviews").mkdir()
        existing = self.root / "reviews/existing.json"
        existing.write_bytes(b"{}")
        before = {path: acl(path) for path in ("reviews", "reviews/existing.json")}
        task = self.task()
        completed, result = self.prepare(task, directory="reviews/new/ancestors/g1")
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        context = result["handoff"]
        packet_path = context["packet_path"]
        packet_bytes = (self.root / packet_path).read_bytes()
        payload = json.loads(packet_bytes)["result_template"]
        payload["receipts"] = [receipt("native-acl-reviewer")]
        original = b"\n " + encode(payload) + b"\n"
        result_path = context["review_requests"][0]["result_path"]
        saved = self.invoke("save", "--repo", str(self.root), "--packet", packet_path,
                            "--output", result_path, raw=original)
        self.assertEqual(saved.returncode, 0, saved.stdout or saved.stderr)
        for path in ("reviews/new", "reviews/new/ancestors", "reviews/new/ancestors/g1",
                     packet_path, result_path):
            with self.subTest(path=path):
                observed = acl(path)
                self.assertFalse(observed["Protected"])
                self.assertGreater(observed["Inherited"], 0)
                self.assertEqual(observed["Explicit"], 0)
        self.assertEqual({path: acl(path) for path in before}, before)
        self.assertEqual(existing.read_bytes(), b"{}")
        self.assertEqual((self.root / packet_path).read_bytes(), packet_bytes)
        self.assertEqual((self.root / result_path).read_bytes(), original)
        submitted = self.invoke("submit", "--repo", str(self.root), "--packet", packet_path,
                                "--", result_path)
        self.assertEqual(submitted.returncode, 0, submitted.stdout or submitted.stderr)
        # This is native ACL/transport coverage in one execution context, not
        # evidence of access by an independent review agent.

    @unittest.skipUnless(sys.platform == "win32", "Windows shell transport")
    def test_generated_powershell_request_from_source_invocation_to_submit(self):
        self.check_generated_request("reviews/native")

    @unittest.skipUnless(sys.platform == "win32", "Windows shell transport")
    def test_generated_powershell_request_accepts_hyphen_leading_directory(self):
        self.check_generated_request("-reviews/native 日本語's")

    def check_generated_request(self, directory):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell 5.1 is unavailable")
        def shell(command):
            return subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                base64.b64encode(command.encode("utf-16le")).decode("ascii")],
                cwd=self.root, capture_output=True, check=False)
        # The entry is invoked before any Packet exists, including from the native shell.
        task = self.task()
        command = ("& '" + sys.executable.replace("'", "''") + "' -I -S '" +
            str(self.helper).replace("'", "''") + "' prepare --repo . '--directory=" +
            directory.replace("'", "''") + "' "
            "--reviewers 1 target " + task + " --kind diff_fingerprint --revision " + FINGERPRINT)
        completed = shell(command)
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        result = json.loads(completed.stdout)["handoff"]
        packet = json.loads((self.root / result["packet_path"]).read_bytes())
        payload = packet["result_template"]
        payload["receipts"] = [receipt("windows-reviewer", findings=[{
            "severity": "low", "summary": "example.py:1 日本語 🚀"}])]
        request = result["review_requests"][0]["request"]
        # Execute the generated literal data carrier, not a second test-owned save command.
        invocation = request[request.index("$OutputEncoding"):request.index("\nOn saved acknowledgement")]
        saved = shell(invocation.replace("<completed original JSON>", encode(payload).decode("utf-8")))
        self.assertEqual(saved.returncode, 0, saved.stdout or saved.stderr)
        self.assertEqual(json.loads((self.root / result["review_requests"][0]["result_path"]).read_bytes()), payload)
        submitted = shell(result["submit_command"])
        self.assertEqual(submitted.returncode, 0, submitted.stdout or submitted.stderr)
        self.assertEqual(json.loads(submitted.stdout)["data"]["receipts"][0]["findings"][0]["finding"]["severity"], "low")
