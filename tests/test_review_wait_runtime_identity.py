"""Source-compiled MCP runtime diagnosis; no host, Task state or configuration."""

import hashlib
import json
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).absolute().parents[1] / "task-governance-tool/scripts"
sys.path.insert(0, str(SCRIPTS))

from task_governance_tool.review_wait_runtime import runtime_identity


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-runtime-identity-")
        self.addCleanup(temporary.cleanup)
        self.scripts = Path(temporary.name) / "scripts"
        self.package = self.scripts / "task_governance_tool"
        self.runtime = self.package / "review_wait_runtime"
        self.runtime.mkdir(parents=True)
        for relative in ("source_imports.py", "review_wait_server.py",
                         "task_governance_tool/review_wait_runtime/runtime_identity.py"):
            (self.scripts / relative).write_bytes((SCRIPTS / relative).read_bytes())
        for path in (self.package / "__init__.py", self.runtime / "__init__.py",
                     self.scripts / "review_handoff.py"):
            path.write_text("", encoding="utf-8")
        (self.package / "storage.py").write_text("SCHEMA_VERSION = 27\n", encoding="utf-8")
        (self.package / "late.py").write_text("VALUE = 1\n", encoding="utf-8")

    def run_entry(self, body="", *, before_bootstrap=""):
        stub = (
            "import json\nfrom pathlib import Path\n"
            "from task_governance_tool.storage import SCHEMA_VERSION\n"
            "def main(*, runtime_identity):\n"
            "    scripts = Path(__file__).absolute().parents[2]\n"
            "    results = [runtime_identity.inspect(loaded_schema=SCHEMA_VERSION)]\n"
            + "".join("    " + line + "\n" for line in body.splitlines())
            + "    results.append(runtime_identity.inspect(loaded_schema=SCHEMA_VERSION))\n"
            "    print(json.dumps(results))\n    return 0\n"
        )
        (self.runtime / "project_server.py").write_text(stub, encoding="utf-8")
        if before_bootstrap:
            entry = self.scripts / "review_wait_server.py"
            text = entry.read_text(encoding="utf-8")
            entry.write_text(text.replace("try:\n", before_bootstrap + "\ntry:\n", 1), encoding="utf-8")
        before = self.snapshot()
        completed = subprocess.run([sys.executable, "-I", "-B", str(self.scripts / "review_wait_server.py")],
            capture_output=True, timeout=20, cwd=self.scripts)
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        for item in result:
            self.assertEqual({"version", "code_id", "supported_schema", "deployed_supported_schema", "comparison"}, set(item))
            self.assertNotIn(str(self.scripts), json.dumps(item))
            self.assertNotIn("private_failure", json.dumps(item))
        return result, before

    def snapshot(self):
        return {p.relative_to(self.scripts).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.scripts.rglob("*") if p.is_file()}

    def test_source_only_entry_and_inspect_are_read_only_and_matching(self):
        results, before = self.run_entry()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(results[0], results[1])
        self.assertEqual("matching", results[0]["comparison"])
        self.assertRegex(results[0]["code_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(27, results[0]["supported_schema"])
        self.assertEqual(27, results[0]["deployed_supported_schema"])
        self.assertFalse(any("__pycache__" in name for name in before))

    def test_entry_replaced_during_bootstrap_cannot_claim_new_disk_implementation(self):
        replacement = (
            'entry_path = Path(__file__)\n'
            'entry_text = entry_path.read_text(encoding="utf-8")\n'
            'entry_parts = entry_text.rsplit("raise SystemExit(main(runtime_identity=runtime_identity))", 1)\n'
            'entry_path.write_text("raise SystemExit(89)".join(entry_parts), encoding="utf-8")'
        )
        results, _ = self.run_entry(before_bootstrap=replacement)
        # The old compiled entry still reaches main and returns zero; the new
        # disk entry would exit 89. Its source is never claimed as loaded code.
        self.assertIn("raise SystemExit(89)", (self.scripts / "review_wait_server.py").read_text(encoding="utf-8"))
        for result in results:
            self.assertEqual("unknown", result["comparison"])
            self.assertIsNone(result["code_id"])
            self.assertEqual(27, result["deployed_supported_schema"])

    def test_missing_executing_entry_evidence_is_unknown_without_blocking_service(self):
        entry = self.scripts / "review_wait_server.py"
        entry.write_text(entry.read_text(encoding="utf-8").replace(
            "entry_code=entry_code)", "entry_code=None)"), encoding="utf-8")
        results, before = self.run_entry()
        self.assertEqual(before, self.snapshot())
        self.assertEqual("unknown", results[0]["comparison"])
        self.assertIsNone(results[0]["code_id"])

    def test_updated_deployment_is_distinct_from_loaded_schema(self):
        results, _ = self.run_entry(
            '(scripts / "task_governance_tool/storage.py").write_text("SCHEMA_VERSION = 28\\n", encoding="utf-8")')
        self.assertEqual("matching", results[0]["comparison"])
        self.assertEqual("different", results[1]["comparison"])
        self.assertEqual(results[0]["code_id"], results[1]["code_id"])
        self.assertEqual(27, results[1]["supported_schema"])
        self.assertEqual(28, results[1]["deployed_supported_schema"])

    def test_same_schema_lazy_module_and_helper_changes_are_included(self):
        for relative in ("task_governance_tool/late.py", "review_handoff.py"):
            with self.subTest(relative=relative):
                results, _ = self.run_entry(
                    f'(scripts / {relative!r}).write_text("CHANGED = True\\n", encoding="utf-8")')
                self.assertEqual("different", results[1]["comparison"])
                self.assertEqual(27, results[1]["deployed_supported_schema"])

    def test_changed_lazy_source_import_makes_loaded_identity_unknown(self):
        results, _ = self.run_entry(
            '(scripts / "task_governance_tool/late.py").write_text("VALUE = 2\\n", encoding="utf-8")\n'
            'from task_governance_tool import late')
        self.assertEqual("matching", results[0]["comparison"])
        self.assertEqual("unknown", results[1]["comparison"])
        self.assertIsNone(results[1]["code_id"])

    def test_new_runtime_module_is_covered_without_a_separate_roster(self):
        (self.runtime / "failure_diagnostics.py").write_text("VERSION = 1\n", encoding="utf-8")
        results, before = self.run_entry('from task_governance_tool.review_wait_runtime import failure_diagnostics')
        self.assertEqual("matching", results[1]["comparison"])
        self.assertEqual(results[0]["code_id"], results[1]["code_id"])
        self.assertEqual(before, self.snapshot())

    def test_uncaptured_module_is_unknown_without_returning_its_name(self):
        results, _ = self.run_entry(
            'import sys, types\n'
            'sys.modules["task_governance_tool.private_failure"] = types.ModuleType("private_failure")')
        self.assertEqual("unknown", results[1]["comparison"])
        self.assertIsNone(results[1]["code_id"])

    def test_schema_mismatch_alone_never_asserts_a_stale_runtime(self):
        results, _ = self.run_entry('globals()["SCHEMA_VERSION"] = 26')
        self.assertEqual("unknown", results[1]["comparison"])
        self.assertIsNone(results[1]["code_id"])
        self.assertEqual(26, results[1]["supported_schema"])
        self.assertEqual(27, results[1]["deployed_supported_schema"])

    def test_unverified_preexisting_import_is_unknown(self):
        results, before = self.run_entry(before_bootstrap="import task_governance_tool")
        self.assertEqual("unknown", results[0]["comparison"])
        self.assertIsNone(results[0]["code_id"])
        self.assertEqual(before, self.snapshot())

    def test_discovery_does_not_accept_a_stale_bytecode_module(self):
        source = self.package / "storage.py"
        source.write_text("SCHEMA_VERSION = 99\n", encoding="utf-8")
        py_compile.compile(str(source), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        source.write_text("SCHEMA_VERSION = 27\n", encoding="utf-8")
        results, before = self.run_entry()
        self.assertEqual(27, results[0]["supported_schema"])
        self.assertEqual("matching", results[0]["comparison"])
        self.assertEqual(before, self.snapshot())

    def test_unreadable_or_unparsable_deployment_is_unknown_without_private_details(self):
        results, _ = self.run_entry(
            '(scripts / "task_governance_tool/storage.py").write_text("SCHEMA_VERSION = private_failure()\\n", encoding="utf-8")')
        self.assertEqual("unknown", results[1]["comparison"])
        self.assertIsNone(results[1]["deployed_supported_schema"])
        self.assertEqual(results[0]["code_id"], results[1]["code_id"])

    def test_missing_required_source_is_unknown(self):
        results, _ = self.run_entry('(scripts / "review_handoff.py").unlink()')
        self.assertEqual("unknown", results[1]["comparison"])

    def test_new_package_source_is_detected(self):
        results, _ = self.run_entry(
            '(scripts / "task_governance_tool/new_feature.py").write_text("VALUE = 1\\n", encoding="utf-8")')
        self.assertEqual("different", results[1]["comparison"])

    def test_read_and_size_failures_remain_bounded(self):
        for setting, value in (("_MAX_FILES", 1), ("_MAX_FILE_BYTES", 1), ("_MAX_TOTAL_BYTES", 1)):
            with self.subTest(setting=setting), mock.patch.object(runtime_identity, setting, value):
                with self.assertRaises(ValueError):
                    runtime_identity._snapshot(self.scripts)
        identity = runtime_identity.RuntimeIdentity(self.scripts)
        with mock.patch.object(runtime_identity, "_snapshot", side_effect=PermissionError("private_failure")):
            result = identity.inspect(loaded_schema=27)
        self.assertEqual("unknown", result["comparison"])
        self.assertNotIn("private_failure", json.dumps(result))

    def test_schema_literal_is_not_executed_or_inferred(self):
        for source in (b"SCHEMA_VERSION = True", b"SCHEMA_VERSION = lookup()",
                       b"SCHEMA_VERSION = 27\nSCHEMA_VERSION += 1",
                       b"SCHEMA_VERSION = OTHER = 27", b"SCHEMA_VERSION: int = 27"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                runtime_identity._schema(source)

    def test_unavailable_capture_never_performs_inspection_or_claims_identity(self):
        with mock.patch.object(runtime_identity, "_snapshot", side_effect=AssertionError("no read")):
            result = runtime_identity.RuntimeIdentity().inspect(loaded_schema=27)
        self.assertEqual({"version": 1, "code_id": None, "supported_schema": 27,
                          "deployed_supported_schema": None, "comparison": "unknown"}, result)

    def test_bootstrap_failure_reports_only_fixed_text(self):
        (self.scripts / "source_imports.py").unlink()
        completed = subprocess.run([sys.executable, "-I", "-B", str(self.scripts / "review_wait_server.py")],
            capture_output=True, timeout=20, cwd=self.scripts)
        self.assertEqual(1, completed.returncode)
        self.assertEqual(b"", completed.stdout)
        self.assertEqual(b"Review-wait server unavailable.\n", completed.stderr.replace(b"\r\n", b"\n"))


if __name__ == "__main__":
    unittest.main()
